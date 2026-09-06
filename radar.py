"""V4 美股强势板块雷达：日线趋势扫描，不提供自动交易或投资建议。"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen

from email_sender import EmailConfigError, send_report_email


OUTPUT_DIR = Path("output")
LEADERS_CSV = OUTPUT_DIR / "market_leaders.csv"
SECTORS_CSV = OUTPUT_DIR / "sector_strength.csv"
WATCHLIST_CSV = OUTPUT_DIR / "watchlist.csv"
MARKDOWN_PATH = OUTPUT_DIR / "market_radar_report.md"
TEXT_PATH = OUTPUT_DIR / "market_radar_report.txt"
METADATA_CACHE = Path(".cache/market_metadata.json")
METADATA_TTL = timedelta(days=7)
MIN_AVG_VOLUME = 100_000
TOP10_LIMIT = 10
TOP_SECTOR_LIMIT = 3
MAX_PER_SECTOR = 3

# Existing, hand-curated theme map is preferred over broad GICS classifications.
THEME_MAP = {
    "NVDA": "半导体", "AMD": "半导体", "AVGO": "半导体", "TSM": "半导体",
    "ASML": "半导体", "AMAT": "半导体", "LRCX": "半导体", "MU": "半导体",
    "ARM": "半导体", "SMCI": "AI基础设施", "PLTR": "AI基础设施",
    "MSFT": "云软件", "ORCL": "云软件", "CRM": "云软件", "NOW": "云软件",
    "SNOW": "云软件", "DDOG": "云软件", "NET": "云软件",
    "CRWD": "网络安全", "PANW": "网络安全", "ZS": "网络安全", "FTNT": "网络安全",
    "JPM": "银行", "BAC": "银行", "WFC": "银行", "GS": "金融", "MS": "金融",
    "LLY": "生物医药", "NVO": "生物医药", "MRK": "生物医药", "VRTX": "生物医药", "REGN": "生物医药",
    "NEM": "黄金矿业", "AEM": "黄金矿业", "GOLD": "黄金矿业", "FCX": "金属/矿业",
    "XOM": "能源", "CVX": "能源", "COP": "能源", "SLB": "能源", "LNG": "能源",
    "RTX": "航空航天", "LMT": "航空航天", "NOC": "航空航天", "GE": "工业", "CAT": "工业",
    "COST": "消费", "WMT": "消费", "HD": "消费", "LOW": "消费", "AMZN": "消费",
    "AAPL": "大型科技", "META": "大型科技", "GOOGL": "大型科技", "NFLX": "大型科技", "TSLA": "大型科技",
}

FALLBACK_UNIVERSE = tuple(sorted(THEME_MAP))
WIKI_SOURCES = (
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "https://en.wikipedia.org/wiki/Nasdaq-100",
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def rsi14(close):
    """Wilder RSI(14), returning a neutral 50 until the first valid window."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0)
    result = 100 - 100 / (1 + rs)
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0)
    return result.fillna(50)


def pct_change(close, sessions: int) -> float:
    if len(close) <= sessions:
        raise ValueError(f"insufficient history for {sessions} sessions")
    start, end = finite(close.iloc[-sessions - 1]), finite(close.iloc[-1])
    if start <= 0 or end <= 0:
        raise ValueError("invalid close")
    return (end / start - 1) * 100


def _cache_is_fresh(value: dict[str, Any]) -> bool:
    try:
        cached_at = datetime.fromisoformat(str(value.get("cached_at", "")).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - cached_at <= METADATA_TTL
    except ValueError:
        return False


def load_metadata_cache(path: Path = METADATA_CACHE) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("symbols", {}) if isinstance(raw, dict) else {}
    return {symbol: value for symbol, value in entries.items() if isinstance(value, dict) and _cache_is_fresh(value)}


def save_metadata_cache(metadata: dict[str, dict[str, str]], path: Path = METADATA_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "symbols": metadata}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _find_column(columns: Iterable[str], choices: Iterable[str]) -> str | None:
    normalized = {str(column).strip().lower(): str(column) for column in columns}
    for choice in choices:
        if choice.lower() in normalized:
            return normalized[choice.lower()]
    return None


def fetch_universe_metadata() -> dict[str, dict[str, str]]:
    """Read stable index tables once; never request company metadata one ticker at a time."""
    import pandas as pd

    metadata: dict[str, dict[str, str]] = {}
    for url in WIKI_SOURCES:
        try:
            # Wikipedia rejects the default Python user agent from hosted
            # runners.  Fetch the public table once with an identifying
            # browser-like header rather than falling back to a tiny universe.
            request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketStrengthRadar/4.0)"})
            with urlopen(request, timeout=20) as response:
                tables = pd.read_html(StringIO(response.read().decode("utf-8", errors="replace")))
        except Exception as exc:
            print(f"::warning::Universe metadata source unavailable: {url} ({exc})", file=sys.stderr)
            continue
        for table in tables:
            symbol_column = _find_column(table.columns, ("Symbol", "Ticker", "Ticker symbol"))
            if not symbol_column:
                continue
            sector_column = _find_column(table.columns, ("GICS Sector", "Sector", "ICB sector"))
            industry_column = _find_column(table.columns, ("GICS Sub-Industry", "Industry", "ICB subsector"))
            for _, row in table.iterrows():
                symbol = str(row.get(symbol_column, "")).strip().upper().replace(".", "-")
                if not symbol or len(symbol) > 10 or not symbol.replace("-", "").isalnum():
                    continue
                sector = str(row.get(sector_column, "") if sector_column else "").strip()
                industry = str(row.get(industry_column, "") if industry_column else "").strip()
                metadata[symbol] = {"sector": sector or "其他", "industry": industry or sector or "其他"}
            break
    return metadata


def build_universe(cache_path: Path = METADATA_CACHE) -> dict[str, dict[str, str]]:
    cached = load_metadata_cache(cache_path)
    fresh = fetch_universe_metadata() if not cached else {}
    merged: dict[str, dict[str, str]] = dict(cached)
    now = datetime.now(timezone.utc).isoformat()
    for symbol, value in fresh.items():
        merged[symbol] = {**value, "cached_at": now}
    for symbol, theme in THEME_MAP.items():
        old = merged.get(symbol, {})
        merged[symbol] = {"sector": old.get("sector", "其他"), "industry": theme, "cached_at": now}
    if not merged:
        merged = {symbol: {"sector": "其他", "industry": theme, "cached_at": now} for symbol, theme in THEME_MAP.items()}
    save_metadata_cache(merged, cache_path)
    return merged


def theme_for(symbol: str, meta: dict[str, str] | None) -> tuple[str, str]:
    meta = meta or {}
    sector = str(meta.get("sector") or "其他")
    industry = THEME_MAP.get(symbol) or str(meta.get("industry") or sector or "其他")
    return sector, industry


def download_histories(symbols: list[str]):
    """Batch yfinance request. One broken ticker is skipped instead of ending the scan."""
    import pandas as pd
    import yfinance as yf

    histories: dict[str, Any] = {}
    for start in range(0, len(symbols), 100):
        batch = symbols[start:start + 100]
        try:
            raw = yf.download(batch, period="15mo", interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=True, timeout=25)
        except Exception as exc:
            print(f"::warning::Batch price request failed for {len(batch)} symbols: {exc}", file=sys.stderr)
            continue
        if raw is None or raw.empty:
            print(f"::warning::Batch returned no data for {len(batch)} symbols", file=sys.stderr)
            continue
        for symbol in batch:
            try:
                frame = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
                required = {"Close", "Volume"}
                if frame.empty or not required.issubset(frame.columns):
                    raise ValueError("missing Close/Volume")
                frame = frame.dropna(subset=["Close"]).copy()
                if len(frame) >= 205:
                    histories[symbol] = frame
            except Exception as exc:
                print(f"::warning::{symbol} skipped: {exc}", file=sys.stderr)
    return histories


def compute_metrics(symbol: str, frame, metadata: dict[str, str]) -> dict[str, Any] | None:
    close = frame["Close"].astype(float).dropna()
    volume = frame["Volume"].astype(float).reindex(close.index).fillna(0)
    if len(close) < 205 or finite(close.iloc[-1]) <= 0:
        return None
    avg_volume = finite(volume.tail(20).mean())
    if avg_volume < MIN_AVG_VOLUME:
        return None
    ema20 = finite(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = finite(close.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = finite(close.ewm(span=200, adjust=False).mean().iloc[-1])
    last = finite(close.iloc[-1])
    if min(last, ema20, ema50, ema200) <= 0:
        return None
    sector, industry = theme_for(symbol, metadata)
    return {
        "symbol": symbol,
        "close": last,
        "rsi14": finite(rsi14(close).iloc[-1]),
        "return_5d": pct_change(close, 5),
        "return_20d": pct_change(close, 20),
        "return_60d": pct_change(close, 60),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "distance_ema20_pct": (last / ema20 - 1) * 100,
        "avg_volume_20d": avg_volume,
        "sector": sector,
        "industry": industry,
    }


def qualifies_for_top10(row: dict[str, Any]) -> bool:
    return row["close"] > row["ema20"] > row["ema50"] and row["rsi14"] > 50


def rank_market_leaders(rows: Iterable[dict[str, Any]], limit: int = TOP10_LIMIT) -> list[dict[str, Any]]:
    return sorted((row for row in rows if qualifies_for_top10(row)), key=lambda row: (-row["rsi14"], -row["return_20d"], row["symbol"]))[:limit]


def rank_sectors(top10: Iterable[dict[str, Any]], limit: int = TOP_SECTOR_LIMIT) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in top10:
        grouped[row["industry"] or row["sector"] or "其他"].append(row)
    ranked = []
    for theme, items in grouped.items():
        count = len(items)
        avg_rsi = sum(row["rsi14"] for row in items) / count
        avg_return_20d = sum(row["return_20d"] for row in items) / count
        # Count dominates; RSI and momentum only resolve ties and quality.
        score = count * 1000 + avg_rsi * 10 + avg_return_20d
        ranked.append({"sector_theme": theme, "top10_count": count, "avg_rsi": avg_rsi, "avg_return_20d": avg_return_20d, "strength_score": score, "market_leader": count >= 3})
    return sorted(ranked, key=lambda row: (-row["strength_score"], row["sector_theme"]))[:limit]


def candidate_score(row: dict[str, Any]) -> float:
    trend = min(100, max(0, 50 + row["return_60d"] * 2.5))
    rsi_score = min(100, max(0, 100 - abs(row["rsi14"] - 67) * 2.2))
    momentum = min(100, max(0, row["return_20d"] * 5))
    distance = min(100, max(0, 100 - abs(row["distance_ema20_pct"] - 4) * 12.5))
    return 0.35 * trend + 0.25 * rsi_score + 0.20 * momentum + 0.20 * distance


def classify_candidate(row: dict[str, Any]) -> str:
    overheated = row["rsi14"] > 80 or row["distance_ema20_pct"] > 10
    if overheated:
        return "过热不追"
    if 58 <= row["rsi14"] <= 75 and 0 <= row["distance_ema20_pct"] <= 8:
        return "优先关注"
    return "观察"


def build_watchlist(rows: Iterable[dict[str, Any]], sectors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    strong = {row["sector_theme"] for row in sectors}
    candidates = []
    for row in rows:
        if row["industry"] not in strong or not (row["close"] > row["ema20"] > row["ema50"]):
            continue
        if row["rsi14"] < 55 or row["return_20d"] <= 0:
            continue
        candidates.append({**row, "sector_theme": row["industry"], "candidate_score": candidate_score(row), "status": classify_candidate(row)})
    status_order = {"优先关注": 0, "观察": 1, "过热不追": 2}
    selected: list[dict[str, Any]] = []
    for theme in strong:
        options = sorted((row for row in candidates if row["sector_theme"] == theme), key=lambda row: (status_order[row["status"]], -row["candidate_score"], row["symbol"]))
        selected.extend(options[:MAX_PER_SECTOR])
    return sorted(selected, key=lambda row: (status_order[row["status"]], -row["candidate_score"], row["sector_theme"], row["symbol"]))


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def report_body(top10: list[dict[str, Any]], sectors: list[dict[str, Any]], watchlist: list[dict[str, Any]], data_status: str, as_of: str) -> str:
    lines = ["【美股强势雷达】"]
    if data_status != "正常":
        lines.extend(["", "数据异常：有效日线数据不足，本日报不输出假排名。", f"数据时间：{as_of} 美股收盘"])
        return "\n".join(lines) + "\n"
    lines.extend(["", "市场主线："])
    medals = ["🥇", "🥈", "🥉"]
    for index, sector in enumerate(sectors):
        fire = " 🔥" if sector["market_leader"] else ""
        lines.append(f"{medals[index]} {sector['sector_theme']}{fire}｜Top10 {sector['top10_count']} 席｜RSI {sector['avg_rsi']:.1f}｜20D {sector['avg_return_20d']:+.1f}%")
    lines.extend(["", "RSI Top10："])
    lines.extend(f"{index}. {row['symbol']} {row['rsi14']:.1f}｜20D {row['return_20d']:+.1f}%｜{row['industry']}" for index, row in enumerate(top10, 1))
    lines.extend(["", "重点关注："])
    if not watchlist:
        lines.append("暂无满足趋势、动量与不过热条件的候选，继续观察。")
    else:
        for theme in [row["sector_theme"] for row in sectors]:
            rows = [row for row in watchlist if row["sector_theme"] == theme]
            if not rows:
                continue
            lines.append(f"【{theme}】")
            lines.extend(f"{row['symbol']}｜RSI {row['rsi14']:.0f}｜距EMA20 {row['distance_ema20_pct']:+.1f}%｜{row['status']}" for row in rows)
    leader = sectors[0]["sector_theme"] if sectors else "暂无明确主线"
    priority = next((row for row in watchlist if row["status"] == "优先关注"), None)
    conclusion = f"{leader}是当前最明确主线。"
    if priority:
        conclusion += f" 优先等待 {priority['symbol']} 等强势但未明显过热标的。"
    lines.extend(["", f"今日结论：{conclusion}", f"数据时间：{as_of} 美股收盘", "仅供市场观察，不构成投资建议。"])
    return "\n".join(lines) + "\n"


def write_reports(top10: list[dict[str, Any]], sectors: list[dict[str, Any]], watchlist: list[dict[str, Any]], data_status: str, as_of: str) -> str:
    write_csv(LEADERS_CSV, ["rank", "symbol", "close", "rsi14", "return_5d", "return_20d", "return_60d", "ema20", "ema50", "ema200", "distance_ema20_pct", "avg_volume_20d", "sector", "industry_theme"], [{"rank": index, **row, "industry_theme": row["industry"]} for index, row in enumerate(top10, 1)])
    write_csv(SECTORS_CSV, ["rank", "sector_theme", "top10_count", "avg_rsi", "avg_return_20d", "strength_score", "market_leader"], [{"rank": index, **row} for index, row in enumerate(sectors, 1)])
    write_csv(WATCHLIST_CSV, ["sector_theme", "symbol", "rsi14", "distance_ema20_pct", "return_20d", "candidate_score", "status"], watchlist)
    body = report_body(top10, sectors, watchlist, data_status, as_of)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_PATH.write_text(body, encoding="utf-8")
    MARKDOWN_PATH.write_text("# 美股强势雷达\n\n" + body.replace("\n", "  \n"), encoding="utf-8")
    return body


def build_subject(sectors: list[dict[str, Any]], top10: list[dict[str, Any]], data_status: str, as_of: str) -> str:
    if data_status != "正常":
        return f"【美股强势雷达】数据异常｜{as_of}"
    leader = sectors[0]["sector_theme"] if sectors else "无明确主线"
    names = " ".join(row["symbol"] for row in top10[:3]) or "无候选"
    return f"【美股强势雷达】{leader}领涨｜{names}｜{as_of}"


def run_scan(universe: dict[str, dict[str, str]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
    universe = universe or build_universe()
    symbols = sorted(universe)
    histories = download_histories(symbols)
    rows = []
    for symbol, frame in histories.items():
        try:
            row = compute_metrics(symbol, frame, universe.get(symbol, {}))
            if row:
                rows.append(row)
        except Exception as exc:
            print(f"::warning::{symbol} metric skipped: {exc}", file=sys.stderr)
    expected_floor = min(len(symbols), max(20, int(len(symbols) * 0.15)))
    data_status = "正常" if len(rows) >= expected_floor else "数据异常"
    if data_status != "正常":
        print(f"::warning::Data anomaly: only {len(rows)}/{len(symbols)} usable symbols", file=sys.stderr)
        return [], [], [], data_status
    top10 = rank_market_leaders(rows)
    sectors = rank_sectors(top10)
    watchlist = build_watchlist(rows, sectors)
    return top10, sectors, watchlist, data_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V4 US market strength sector radar")
    parser.add_argument("--send-email", action="store_true", help="send the daily report through SMTP")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        top10, sectors, watchlist, data_status = run_scan()
        as_of = datetime.now(timezone.utc).date().isoformat()
        body = write_reports(top10, sectors, watchlist, data_status, as_of)
        print(body)
        if args.send_email or os.getenv("SEND_EMAIL", "").lower() in {"1", "true", "yes"}:
            try:
                send_report_email(body, [TEXT_PATH], 0, subject_override=build_subject(sectors, top10, data_status, as_of), retries=1)
            except EmailConfigError:
                raise
            except Exception as exc:
                # The market report remains valid and archived when a mail
                # provider is temporarily unavailable.  The non-zero status
                # keeps the Actions run visibly actionable.
                print(f"::warning::Daily report was generated but email delivery failed: {exc!r}", file=sys.stderr)
                return 1
        return 0
    except EmailConfigError as exc:
        print(f"::warning::SMTP configuration missing: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"::warning::Radar failed: {exc!r}", file=sys.stderr)
        # Leave a trace even if an upstream data source fails before scan completion.
        body = write_reports([], [], [], "数据异常", datetime.now(timezone.utc).date().isoformat())
        print(body)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
