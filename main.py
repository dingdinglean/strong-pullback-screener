from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

from email_sender import EmailConfigError, send_report_email


OUTPUT_DIR = Path("output")
CSV_PATH = OUTPUT_DIR / "strong_pullback_candidates.csv"
MD_PATH = OUTPUT_DIR / "strong_pullback_report.md"
BENCHMARK = "SPY"
TOP_STRONG_STOCKS = 50
DISCLAIMER_LINES = [
    "This is a screening report, not financial advice.",
    "A榜只是重点观察名单，不代表自动买入。",
    "必须结合人工复核、仓位控制和止损。",
]


DEFAULT_UNIVERSE = {
    "NVDA": "AI & Semiconductors",
    "AMD": "AI & Semiconductors",
    "AVGO": "AI & Semiconductors",
    "TSM": "AI & Semiconductors",
    "ASML": "AI & Semiconductors",
    "AMAT": "AI & Semiconductors",
    "LRCX": "AI & Semiconductors",
    "MU": "AI & Semiconductors",
    "ARM": "AI & Semiconductors",
    "SMCI": "AI & Semiconductors",
    "MSFT": "Mega Cap Tech",
    "AAPL": "Mega Cap Tech",
    "GOOGL": "Mega Cap Tech",
    "META": "Mega Cap Tech",
    "AMZN": "Mega Cap Tech",
    "TSLA": "Mega Cap Tech",
    "NFLX": "Mega Cap Tech",
    "ORCL": "Cloud Software",
    "CRM": "Cloud Software",
    "NOW": "Cloud Software",
    "ADBE": "Cloud Software",
    "SNOW": "Cloud Software",
    "DDOG": "Cloud Software",
    "NET": "Cloud Software",
    "CRWD": "Cybersecurity",
    "PANW": "Cybersecurity",
    "ZS": "Cybersecurity",
    "FTNT": "Cybersecurity",
    "SHOP": "Ecommerce & Payments",
    "MELI": "Ecommerce & Payments",
    "PYPL": "Ecommerce & Payments",
    "SQ": "Ecommerce & Payments",
    "V": "Ecommerce & Payments",
    "MA": "Ecommerce & Payments",
    "COIN": "Crypto & Brokers",
    "MSTR": "Crypto & Brokers",
    "HOOD": "Crypto & Brokers",
    "IBKR": "Crypto & Brokers",
    "JPM": "Financials",
    "GS": "Financials",
    "MS": "Financials",
    "BAC": "Financials",
    "WFC": "Financials",
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "SLB": "Energy",
    "LNG": "Energy",
    "LLY": "Healthcare & Biotech",
    "NVO": "Healthcare & Biotech",
    "MRK": "Healthcare & Biotech",
    "VRTX": "Healthcare & Biotech",
    "REGN": "Healthcare & Biotech",
    "ISRG": "Healthcare & Biotech",
    "UNH": "Healthcare & Biotech",
    "GE": "Industrials & Defense",
    "GEV": "Industrials & Defense",
    "CAT": "Industrials & Defense",
    "DE": "Industrials & Defense",
    "ETN": "Industrials & Defense",
    "PH": "Industrials & Defense",
    "HON": "Industrials & Defense",
    "RTX": "Industrials & Defense",
    "LMT": "Industrials & Defense",
    "NOC": "Industrials & Defense",
    "COST": "Consumer Leaders",
    "WMT": "Consumer Leaders",
    "HD": "Consumer Leaders",
    "LOW": "Consumer Leaders",
    "NKE": "Consumer Leaders",
    "SBUX": "Consumer Leaders",
    "CMG": "Consumer Leaders",
    "DIS": "Consumer Leaders",
    "UBER": "Transport & Platforms",
    "ABNB": "Transport & Platforms",
    "BKNG": "Transport & Platforms",
    "DAL": "Transport & Platforms",
    "UAL": "Transport & Platforms",
    "FCX": "Metals & Miners",
    "NEM": "Metals & Miners",
    "AA": "Metals & Miners",
    "CLF": "Metals & Miners",
    "LEN": "Housing",
    "DHI": "Housing",
    "TOL": "Housing",
    "PHM": "Housing",
}


@dataclass
class DailyMetrics:
    symbol: str
    theme: str
    close: float
    return_1m: float
    return_3m: float
    return_6m: float
    return_12m: float
    rs_raw: float
    rs_score: float = 0.0
    sma20: float = 0.0
    sma50: float = 0.0
    sma150: float = 0.0
    rsi14: float = 0.0
    distance_sma20_pct: float = 0.0
    distance_52w_high_pct: float = 0.0
    avg_volume_20d: float = 0.0
    daily_uptrend: bool = False


@dataclass
class ScreenedStock:
    symbol: str
    theme: str
    list_name: str
    close: float
    rs_score: float
    daily_uptrend: bool
    hourly_pullback: bool
    overheated: bool
    distance_sma20_pct: float
    distance_52w_high_pct: float
    hourly_rsi14: float
    downgrade_reason: str


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strong trend pullback stock screener")
    parser.add_argument("--send-email", action="store_true", help="send the generated report by SMTP")
    parser.add_argument("--top-buy", type=int, default=10, help="maximum A-list candidates")
    parser.add_argument("--top-watch", type=int, default=20, help="maximum B-list candidates")
    parser.add_argument("--min-rs-score", type=float, default=70, help="minimum relative strength score")
    parser.add_argument(
        "--universe-file",
        type=Path,
        default=None,
        help="optional CSV with symbol,theme columns",
    )
    return parser.parse_args()


def load_universe(path: Path | None) -> dict[str, str]:
    if path is None:
        return dict(DEFAULT_UNIVERSE)
    if not path.exists():
        raise FileNotFoundError(f"universe file not found: {path}")

    universe: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            symbol = (row.get("symbol") or row.get("ticker") or "").strip().upper()
            theme = (row.get("theme") or row.get("sector") or "Unclassified").strip()
            if symbol:
                universe[symbol] = theme or "Unclassified"
    if not universe:
        raise ValueError("universe file did not contain any symbols")
    return universe


def pct_return(close, periods: int) -> float:
    if len(close) <= periods:
        return float(close.iloc[-1] / close.iloc[0] - 1)
    return float(close.iloc[-1] / close.iloc[-periods - 1] - 1)


def rsi(series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    value = 100 - (100 / (1 + rs))
    latest = value.iloc[-1]
    return 50.0 if latest != latest else float(latest)


def fetch_history(symbol: str, period: str, interval: str, attempts: int = 3):
    import yfinance as yf

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period, interval=interval, auto_adjust=True)
            if data.empty or "Close" not in data:
                raise ValueError(f"no price data returned for {symbol}")
            return data.dropna(subset=["Close"])
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 * attempt)

    if interval == "1d":
        try:
            return fetch_stooq_daily(symbol)
        except Exception as fallback_error:
            raise ValueError(
                f"{symbol} history unavailable after {attempts} attempts: {last_error}; "
                f"Stooq fallback failed: {fallback_error}"
            ) from fallback_error

    raise ValueError(f"{symbol} history unavailable after {attempts} attempts: {last_error}")


def fetch_stooq_daily(symbol: str):
    import pandas as pd

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=430)
    stooq_symbol = symbol.lower().replace(".", "-") + ".us"
    url = (
        "https://stooq.com/q/d/l/"
        f"?s={stooq_symbol}&i=d&d1={start:%Y%m%d}&d2={end:%Y%m%d}"
    )

    with urlopen(url, timeout=30) as response:
        csv_text = response.read().decode("utf-8")

    if "No data" in csv_text or len(csv_text.splitlines()) < 2:
        raise ValueError("no Stooq daily data returned")

    data = pd.read_csv(StringIO(csv_text), parse_dates=["Date"])
    required_columns = {"Open", "High", "Low", "Close", "Volume"}
    if data.empty or not required_columns.issubset(data.columns):
        raise ValueError("Stooq response missing OHLCV columns")

    data = data.set_index("Date").sort_index()
    return data[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def compute_daily_metrics(symbol: str, theme: str, benchmark_returns: dict[str, float]) -> DailyMetrics:
    data = fetch_history(symbol, period="1y", interval="1d")
    close = data["Close"]
    volume = data["Volume"] if "Volume" in data else close * 0

    returns = {
        "1m": pct_return(close, 21),
        "3m": pct_return(close, 63),
        "6m": pct_return(close, 126),
        "12m": pct_return(close, 252),
    }
    rs_raw = (
        0.20 * (returns["1m"] - benchmark_returns["1m"])
        + 0.40 * (returns["3m"] - benchmark_returns["3m"])
        + 0.25 * (returns["6m"] - benchmark_returns["6m"])
        + 0.15 * (returns["12m"] - benchmark_returns["12m"])
    )

    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50_series = close.rolling(50).mean()
    sma50 = float(sma50_series.iloc[-1])
    sma150 = float(close.rolling(150).mean().iloc[-1])
    high_52w = float(close.rolling(min(252, len(close))).max().iloc[-1])
    current_close = float(close.iloc[-1])
    sma50_slope_up = len(sma50_series.dropna()) < 11 or sma50 > float(sma50_series.dropna().iloc[-10])
    daily_uptrend = current_close > sma50 > sma150 and current_close > sma20 and sma50_slope_up

    return DailyMetrics(
        symbol=symbol,
        theme=theme,
        close=current_close,
        return_1m=returns["1m"],
        return_3m=returns["3m"],
        return_6m=returns["6m"],
        return_12m=returns["12m"],
        rs_raw=rs_raw,
        sma20=sma20,
        sma50=sma50,
        sma150=sma150,
        rsi14=rsi(close),
        distance_sma20_pct=(current_close / sma20 - 1) * 100,
        distance_52w_high_pct=(current_close / high_52w - 1) * 100,
        avg_volume_20d=float(volume.tail(20).mean()),
        daily_uptrend=daily_uptrend,
    )


def assign_rs_scores(metrics: list[DailyMetrics]) -> None:
    ranked = sorted(metrics, key=lambda item: item.rs_raw)
    total = len(ranked)
    for index, item in enumerate(ranked, start=1):
        item.rs_score = round((index / total) * 100, 1)


def compute_hourly_setup(symbol: str) -> tuple[bool, bool, float]:
    try:
        data = fetch_history(symbol, period="60d", interval="1h")
    except Exception:
        return False, False, 50.0

    close = data["Close"]
    if len(close) < 50:
        return False, False, 50.0

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    current_close = close.iloc[-1]
    recent_high = close.tail(40).max()
    hourly_rsi = rsi(close)

    pullback_depth_pct = (current_close / recent_high - 1) * 100
    distance_ema20_pct = (current_close / ema20 - 1) * 100
    distance_ema50_pct = (current_close / ema50 - 1) * 100

    hourly_pullback = (
        -8.0 <= pullback_depth_pct <= -1.0
        and -1.5 <= distance_ema20_pct <= 3.0
        and distance_ema50_pct >= -3.0
        and 38.0 <= hourly_rsi <= 68.0
    )
    overheated = distance_ema20_pct > 8.0 or hourly_rsi > 72.0
    return bool(hourly_pullback), bool(overheated), float(hourly_rsi)


def top_themes_from_market(metrics: list[DailyMetrics]) -> list[dict[str, object]]:
    top_stocks = sorted(metrics, key=lambda item: item.rs_score, reverse=True)[:TOP_STRONG_STOCKS]
    grouped: dict[str, list[DailyMetrics]] = {}
    for item in top_stocks:
        grouped.setdefault(item.theme, []).append(item)

    theme_rows = []
    for theme, items in grouped.items():
        avg_rs = sum(item.rs_score for item in items) / len(items)
        top_symbols = ", ".join(item.symbol for item in sorted(items, key=lambda row: row.rs_score, reverse=True)[:5])
        theme_rows.append(
            {
                "theme": theme,
                "count": len(items),
                "avg_rs_score": round(avg_rs, 1),
                "top_symbols": top_symbols,
            }
        )
    return sorted(theme_rows, key=lambda row: (-int(row["count"]), -float(row["avg_rs_score"])))[:5]


def classify_stock(
    item: DailyMetrics,
    strong_themes: set[str],
    min_rs_score: float,
) -> ScreenedStock:
    hourly_pullback, hourly_overheated, hourly_rsi = compute_hourly_setup(item.symbol)
    daily_overheated = item.rsi14 > 75.0 or item.distance_sma20_pct > 12.0
    overheated = hourly_overheated or daily_overheated

    reasons = []
    if item.rs_score < min_rs_score:
        reasons.append("RS below threshold")
    if item.theme not in strong_themes:
        reasons.append("Not in today's top strong themes")
    if not item.daily_uptrend:
        reasons.append("Daily trend not aligned")
    if overheated:
        reasons.append("Overheated, do not chase")
    if not hourly_pullback:
        reasons.append("1H pullback not formed")

    if item.rs_score >= min_rs_score and item.daily_uptrend and overheated:
        list_name = "C"
    elif (
        item.rs_score >= min_rs_score
        and item.theme in strong_themes
        and item.daily_uptrend
        and hourly_pullback
    ):
        list_name = "A"
    elif item.rs_score >= min_rs_score and item.daily_uptrend:
        list_name = "B"
    else:
        list_name = "DROPPED"

    if list_name == "A":
        reason_text = "Strong theme, strong stock, daily uptrend, 1H pullback"
    elif list_name == "B" and not reasons:
        reason_text = "Strong watch candidate, but not A-list quality"
    else:
        reason_text = "; ".join(reasons) if reasons else "Downgraded by ranking limits"

    return ScreenedStock(
        symbol=item.symbol,
        theme=item.theme,
        list_name=list_name,
        close=item.close,
        rs_score=item.rs_score,
        daily_uptrend=item.daily_uptrend,
        hourly_pullback=hourly_pullback,
        overheated=overheated,
        distance_sma20_pct=item.distance_sma20_pct,
        distance_52w_high_pct=item.distance_52w_high_pct,
        hourly_rsi14=hourly_rsi,
        downgrade_reason=reason_text,
    )


def screen_market(
    universe: dict[str, str],
    top_buy: int,
    top_watch: int,
    min_rs_score: float,
) -> tuple[list[ScreenedStock], list[dict[str, object]], list[str]]:
    errors: list[str] = []
    try:
        benchmark_data = fetch_history(BENCHMARK, period="1y", interval="1d")
        benchmark_close = benchmark_data["Close"]
        benchmark_returns = {
            "1m": pct_return(benchmark_close, 21),
            "3m": pct_return(benchmark_close, 63),
            "6m": pct_return(benchmark_close, 126),
            "12m": pct_return(benchmark_close, 252),
        }
    except Exception as exc:
        errors.append(f"{BENCHMARK} benchmark unavailable, using absolute momentum: {exc}")
        benchmark_returns = {"1m": 0.0, "3m": 0.0, "6m": 0.0, "12m": 0.0}

    metrics: list[DailyMetrics] = []
    for symbol, theme in sorted(universe.items()):
        try:
            metrics.append(compute_daily_metrics(symbol, theme, benchmark_returns))
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")

    if not metrics:
        return [], [], errors

    assign_rs_scores(metrics)
    top_themes = top_themes_from_market(metrics)
    strong_themes = {str(row["theme"]) for row in top_themes}

    top_market = sorted(metrics, key=lambda item: item.rs_score, reverse=True)[:TOP_STRONG_STOCKS]
    screened = [classify_stock(item, strong_themes, min_rs_score) for item in top_market]

    a_list = sorted(
        (row for row in screened if row.list_name == "A"),
        key=lambda row: row.rs_score,
        reverse=True,
    )[:top_buy]
    b_list = sorted(
        (row for row in screened if row.list_name == "B"),
        key=lambda row: row.rs_score,
        reverse=True,
    )[:top_watch]
    c_list = sorted(
        (row for row in screened if row.list_name == "C"),
        key=lambda row: row.rs_score,
        reverse=True,
    )
    dropped = [row for row in screened if row.list_name == "DROPPED"]

    limited_symbols = {row.symbol for row in [*a_list, *b_list, *c_list, *dropped]}
    ranking_dropped = [
        row
        for row in screened
        if row.symbol not in limited_symbols and row.list_name in {"A", "B"}
    ]
    for row in ranking_dropped:
        row.list_name = "DROPPED"
        row.downgrade_reason = "Downgraded by ranking limits"

    final_rows = [*a_list, *b_list, *c_list, *dropped, *ranking_dropped]
    return final_rows, top_themes, errors


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def markdown_table(rows: Iterable[ScreenedStock]) -> str:
    rows = list(rows)
    if not rows:
        return "_None today._"

    lines = [
        "| Symbol | Theme | RS | Close | 1H Pullback | Overheated | Dist 20D SMA | 52W High Gap | Reason |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.symbol,
                    row.theme.replace("|", "/"),
                    f"{row.rs_score:.1f}",
                    f"{row.close:.2f}",
                    "Y" if row.hourly_pullback else "N",
                    "Y" if row.overheated else "N",
                    fmt_pct(row.distance_sma20_pct),
                    fmt_pct(row.distance_52w_high_pct),
                    row.downgrade_reason.replace("|", "/"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def build_report(
    rows: list[ScreenedStock],
    top_themes: list[dict[str, object]],
    errors: list[str],
    min_rs_score: float,
) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    a_rows = [row for row in rows if row.list_name == "A"]
    b_rows = [row for row in rows if row.list_name == "B"]
    c_rows = [row for row in rows if row.list_name == "C"]
    dropped_rows = [row for row in rows if row.list_name == "DROPPED"]

    reason_counter = Counter()
    for row in [*b_rows, *c_rows, *dropped_rows]:
        for reason in row.downgrade_reason.split("; "):
            if reason:
                reason_counter[reason] += 1

    theme_lines = [
        "| Rank | Theme | Top-50 Count | Avg RS | Leading Symbols |",
        "|---:|---|---:|---:|---|",
    ]
    for index, row in enumerate(top_themes, start=1):
        theme_lines.append(
            f"| {index} | {row['theme']} | {row['count']} | "
            f"{row['avg_rs_score']} | {row['top_symbols']} |"
        )
    if not top_themes:
        theme_lines.append("| - | No data | 0 | 0 | - |")

    reason_lines = ["| Reason | Count |", "|---|---:|"]
    for reason, count in reason_counter.most_common():
        reason_lines.append(f"| {reason.replace('|', '/')} | {count} |")
    if not reason_counter:
        reason_lines.append("| No downgrades | 0 |")

    error_section = ""
    if errors:
        sample_errors = "\n".join(f"- {error}" for error in errors[:20])
        more = "" if len(errors) <= 20 else f"\n- ... {len(errors) - 20} more"
        error_section = f"\n\n## Data Warnings\n{sample_errors}{more}"

    return "\n\n".join(
        [
            "# Strong Pullback Screener Report",
            f"Generated: {generated_at}\n\nMinimum RS Score: {min_rs_score:.1f}",
            "## 今日强势主题 Top 5\n" + "\n".join(theme_lines),
            "## A榜：强主题 + 强个股 + 1H回踩\n" + markdown_table(a_rows),
            "## B榜：强势观察，不追\n" + markdown_table(b_rows),
            "## C榜：过热，不追\n" + markdown_table(c_rows),
            "## 降级原因摘要\n" + "\n".join(reason_lines),
            "## 系统免责声明\n" + "\n".join(DISCLAIMER_LINES) + error_section,
        ]
    )


def write_csv(rows: list[ScreenedStock]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "list",
                "symbol",
                "theme",
                "close",
                "rs_score",
                "daily_uptrend",
                "hourly_pullback",
                "overheated",
                "distance_sma20_pct",
                "distance_52w_high_pct",
                "hourly_rsi14",
                "downgrade_reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "list": row.list_name,
                    "symbol": row.symbol,
                    "theme": row.theme,
                    "close": round(row.close, 2),
                    "rs_score": round(row.rs_score, 1),
                    "daily_uptrend": row.daily_uptrend,
                    "hourly_pullback": row.hourly_pullback,
                    "overheated": row.overheated,
                    "distance_sma20_pct": round(row.distance_sma20_pct, 2),
                    "distance_52w_high_pct": round(row.distance_52w_high_pct, 2),
                    "hourly_rsi14": round(row.hourly_rsi14, 1),
                    "downgrade_reason": row.downgrade_reason,
                }
            )


def write_markdown(report: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MD_PATH.write_text(report, encoding="utf-8")


def main() -> int:
    args = parse_args()
    send_email = args.send_email or env_flag("SEND_EMAIL", default=False)

    try:
        universe = load_universe(args.universe_file)
        rows, top_themes, errors = screen_market(
            universe=universe,
            top_buy=args.top_buy,
            top_watch=args.top_watch,
            min_rs_score=args.min_rs_score,
        )
    except Exception as exc:
        rows = []
        top_themes = []
        errors = [f"fatal screener error: {exc}"]
        print(f"ERROR: {errors[0]}", file=sys.stderr)

    report = build_report(rows, top_themes, errors, args.min_rs_score)
    write_csv(rows)
    write_markdown(report)
    print(f"Wrote {CSV_PATH}")
    print(f"Wrote {MD_PATH}")

    if send_email:
        a_count = sum(1 for row in rows if row.list_name == "A")
        try:
            send_report_email(
                markdown_body=report,
                report_paths=[CSV_PATH, MD_PATH],
                a_candidate_count=a_count,
            )
            print("Email sent successfully.")
        except EmailConfigError as exc:
            print(f"ERROR: Email requested but SMTP configuration is incomplete: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"ERROR: Email requested but sending failed: {exc}", file=sys.stderr)
    else:
        print("Email sending disabled. Set SEND_EMAIL=true or pass --send-email to enable it.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
