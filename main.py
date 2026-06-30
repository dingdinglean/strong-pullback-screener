from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

from email_sender import EmailConfigError, send_report_email


OUTPUT_DIR = Path("output")
CSV_PATH = OUTPUT_DIR / "strong_pullback_candidates.csv"
MD_PATH = OUTPUT_DIR / "strong_pullback_report.md"
TXT_PATH = OUTPUT_DIR / "strong_pullback_report.txt"
BENCHMARK = "SPY"
TOP_STRONG_STOCKS = 50
REPORT_LIMIT = 5
TOP_THEME_LIMIT = 3

DISCLAIMER_LINES = [
    "This is a screening report, not financial advice.",
    "A榜只是重点观察名单，不代表自动买入。",
    "必须结合人工复核、仓位控制和止损。",
]


DEFAULT_UNIVERSE = {
    "NVDA": "AI/半导体",
    "AMD": "AI/半导体",
    "AVGO": "AI/半导体",
    "TSM": "AI/半导体",
    "ASML": "AI/半导体",
    "AMAT": "AI/半导体",
    "LRCX": "AI/半导体",
    "MU": "AI/半导体",
    "ARM": "AI/半导体",
    "SMCI": "AI/半导体",
    "MSFT": "大型科技",
    "AAPL": "大型科技",
    "GOOGL": "大型科技",
    "META": "大型科技",
    "AMZN": "大型科技",
    "TSLA": "大型科技",
    "NFLX": "大型科技",
    "ORCL": "云软件",
    "CRM": "云软件",
    "NOW": "云软件",
    "ADBE": "云软件",
    "SNOW": "云软件",
    "DDOG": "云软件",
    "NET": "云软件",
    "CRWD": "网络安全",
    "PANW": "网络安全",
    "ZS": "网络安全",
    "FTNT": "网络安全",
    "SHOP": "电商/支付",
    "MELI": "电商/支付",
    "PYPL": "电商/支付",
    "SQ": "电商/支付",
    "V": "电商/支付",
    "MA": "电商/支付",
    "COIN": "加密/券商",
    "MSTR": "加密/券商",
    "HOOD": "加密/券商",
    "IBKR": "加密/券商",
    "JPM": "金融",
    "GS": "金融",
    "MS": "金融",
    "BAC": "金融",
    "WFC": "金融",
    "XOM": "能源",
    "CVX": "能源",
    "COP": "能源",
    "SLB": "能源",
    "LNG": "能源",
    "LLY": "医疗",
    "NVO": "医疗",
    "MRK": "医疗",
    "VRTX": "医疗",
    "REGN": "医疗",
    "ISRG": "医疗",
    "UNH": "医疗",
    "GE": "工业/电力设备",
    "GEV": "工业/电力设备",
    "CAT": "工业/电力设备",
    "DE": "工业/电力设备",
    "ETN": "工业/电力设备",
    "PH": "工业/电力设备",
    "HON": "工业/电力设备",
    "RTX": "军工/工业",
    "LMT": "军工/工业",
    "NOC": "军工/工业",
    "COST": "消费龙头",
    "WMT": "消费龙头",
    "HD": "消费龙头",
    "LOW": "消费龙头",
    "NKE": "消费龙头",
    "SBUX": "消费龙头",
    "CMG": "消费龙头",
    "DIS": "消费龙头",
    "UBER": "出行/平台",
    "ABNB": "出行/平台",
    "BKNG": "出行/平台",
    "DAL": "出行/平台",
    "UAL": "出行/平台",
    "FCX": "金属/矿业",
    "NEM": "金属/矿业",
    "AA": "金属/矿业",
    "CLF": "金属/矿业",
    "LEN": "地产/住宅",
    "DHI": "地产/住宅",
    "TOL": "地产/住宅",
    "PHM": "地产/住宅",
}


@dataclass
class HistoryResult:
    data: object
    source: str
    warning: str = ""
    price_invalid: bool = False


@dataclass
class IntradaySetup:
    early_signal_1h: bool = False
    early_signal_1h_source: str = "DXDX"
    two_hour_bottom_signal: bool = False
    two_hour_signal_source: str = "DXDX"
    proxy_warning: bool = False
    sell_signal_1h: bool = False
    sell_signal_2h: bool = False
    rsi_1h: float = 50.0
    rsi_2h: float = 50.0
    ema23_2h_slope_5: float = 0.0
    overheated_2h: bool = False
    warning: str = ""


@dataclass
class DailyMetrics:
    symbol: str
    theme: str
    close: float
    data_source: str
    data_warning: str
    price_invalid: bool
    return_1m: float
    return_3m: float
    return_6m: float
    return_12m: float
    rs_raw: float
    rs_score: float = 0.0
    ema23: float = 0.0
    ema89: float = 0.0
    ema23_slope_5: float = 0.0
    up1: float = 0.0
    dw1: float = 0.0
    up2: float = 0.0
    dw2: float = 0.0
    blue_channel_above_yellow: bool = False
    up1_gt_up2: bool = False
    blue_channel_slope_up: bool = False
    distance_blue_lower_pct: float = 0.0
    distance_blue_lower_abs_pct: float = 0.0
    rsi14: float = 0.0
    distance_ema23_pct: float = 0.0
    distance_ema23_abs_pct: float = 0.0
    distance_52w_high_pct: float = 0.0
    avg_volume_20d: float = 0.0
    ema23_gt_ema89: bool = False
    close_gt_ema89: bool = False
    descending_channel: bool = False
    higher_lows: bool = False
    max_drawdown_pct: float = 0.0
    repair_profile: bool = False


@dataclass
class ScreenedStock:
    symbol: str
    theme: str
    list_name: str
    setup_class: str
    setup_grade: str
    close: float
    data_source: str
    data_warning: str
    price_invalid: bool
    rs_score: float
    ema23: float
    ema89: float
    up1: float
    dw1: float
    up2: float
    dw2: float
    blue_channel_above_yellow: bool
    up1_gt_up2: bool
    blue_channel_slope_up: bool
    distance_blue_lower_pct: float
    distance_blue_lower_abs_pct: float
    distance_ema23_pct: float
    distance_ema23_abs_pct: float
    distance_52w_high_pct: float
    ema23_gt_ema89: bool
    close_gt_ema89: bool
    descending_channel: bool
    higher_lows: bool
    max_drawdown_pct: float
    early_signal_1h: bool
    early_signal_1h_source: str
    two_hour_bottom_signal: bool
    two_hour_signal_source: str
    proxy_warning: bool
    sell_signal_1h: bool
    sell_signal_2h: bool
    rsi_1h: float
    rsi_2h: float
    overheated: bool
    downgrade_reason: str


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strong trend pullback stock screener")
    parser.add_argument("--send-email", action="store_true", help="send the generated report by SMTP")
    parser.add_argument("--top-buy", type=int, default=10, help="maximum A-list candidates before report cap")
    parser.add_argument("--top-watch", type=int, default=20, help="maximum B-list candidates before report cap")
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
            theme = (row.get("theme") or row.get("sector") or "未分类").strip()
            if symbol:
                universe[symbol] = theme or "未分类"
    if not universe:
        raise ValueError("universe file did not contain any symbols")
    return universe


def is_valid_number(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def pct_return(close, periods: int) -> float:
    if len(close) <= 1:
        return 0.0
    if len(close) <= periods:
        return float(close.iloc[-1] / close.iloc[0] - 1)
    return float(close.iloc[-1] / close.iloc[-periods - 1] - 1)


def rsi_series(series, period: int = 14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gain / loss.where(loss != 0)
    values = 100 - (100 / (1 + rs))
    values = values.mask((loss == 0) & (gain > 0), 100)
    values = values.mask((gain == 0) & (loss > 0), 0)
    return values.fillna(50)


def latest_rsi(series, period: int = 14) -> float:
    return float(rsi_series(series, period).iloc[-1])


def slope(series, periods: int = 5) -> float:
    clean = series.dropna()
    if len(clean) <= periods:
        return 0.0
    return float(clean.iloc[-1] - clean.iloc[-periods - 1])


def ref_array(values, periods=1, default=float("nan")):
    import numpy as np

    arr = np.asarray(values)
    if np.isscalar(periods):
        periods_arr = np.full(len(arr), int(periods), dtype=int)
    else:
        periods_arr = np.asarray(periods).astype(int)

    result = np.full(len(arr), default, dtype=object)
    for index, period in enumerate(periods_arr):
        source_index = index - max(0, int(period))
        if source_index >= 0:
            result[index] = arr[source_index]
    return result


def ref_bool(values, periods=1):
    return ref_array(values, periods, default=False).astype(bool)


def ref_float(values, periods=1):
    import numpy as np

    return np.asarray(ref_array(values, periods, default=np.nan), dtype=float)


def barslast_safe(condition, lookback: int = 100):
    import numpy as np

    cond = np.asarray(condition, dtype=bool)
    result = np.zeros(len(cond), dtype=int)
    last_true = -1
    for index, value in enumerate(cond):
        if value:
            last_true = index
        window_start = max(0, index - lookback + 1)
        if last_true >= window_start:
            result[index] = index - last_true
        else:
            result[index] = 0
    return result


def rolling_count_bool(condition, window: int):
    import numpy as np

    cond = np.asarray(condition, dtype=bool)
    result = np.zeros(len(cond), dtype=int)
    for index in range(len(cond)):
        start = max(0, index - window + 1)
        result[index] = int(cond[start : index + 1].sum())
    return result


def dynamic_lowest(values, lengths):
    import numpy as np

    arr = np.asarray(values, dtype=float)
    lens = np.asarray(lengths).astype(int)
    result = np.full(len(arr), np.nan, dtype=float)
    for index, length in enumerate(lens):
        length = max(1, int(length))
        start = max(0, index - length + 1)
        result[index] = float(np.nanmin(arr[start : index + 1]))
    return result


def dynamic_highest(values, lengths):
    import numpy as np

    arr = np.asarray(values, dtype=float)
    lens = np.asarray(lengths).astype(int)
    result = np.full(len(arr), np.nan, dtype=float)
    for index, length in enumerate(lens):
        length = max(1, int(length))
        start = max(0, index - length + 1)
        result[index] = float(np.nanmax(arr[start : index + 1]))
    return result


def compute_cd_docx_signals(data) -> dict[str, bool]:
    import numpy as np

    close_series = data["Close"].astype(float)
    if len(close_series) < 60:
        return {"bottom_signal": False, "sell_signal": False}

    close = close_series.to_numpy(dtype=float)
    diff_series = close_series.ewm(span=12, adjust=False).mean() - close_series.ewm(span=26, adjust=False).mean()
    dea_series = diff_series.ewm(span=9, adjust=False).mean()
    diff = diff_series.to_numpy(dtype=float)
    dea = dea_series.to_numpy(dtype=float)
    macd = (diff - dea) * 2

    ref_macd_1 = ref_float(macd, 1)
    neg_cross = (ref_macd_1 >= 0) & (macd < 0)
    pos_cross = (ref_macd_1 <= 0) & (macd > 0)
    n1 = barslast_safe(neg_cross, 100)
    mm1 = barslast_safe(pos_cross, 100)

    cc1 = dynamic_lowest(close, n1 + 1)
    cc2 = ref_float(cc1, np.maximum(1, mm1 + 1))
    cc3 = ref_float(cc2, np.maximum(1, mm1 + 1))
    difl1 = dynamic_lowest(diff, n1 + 1)
    difl2 = ref_float(difl1, np.maximum(1, mm1 + 1))
    difl3 = ref_float(difl2, np.maximum(1, mm1 + 1))

    ch1 = dynamic_highest(close, mm1 + 1)
    ch2 = ref_float(ch1, np.maximum(1, n1 + 1))
    ch3 = ref_float(ch2, np.maximum(1, n1 + 1))
    difh1 = dynamic_highest(diff, mm1 + 1)
    difh2 = ref_float(difh1, np.maximum(1, n1 + 1))
    difh3 = ref_float(difh2, np.maximum(1, n1 + 1))

    ref_diff_1 = ref_float(diff, 1)
    aaa = (cc1 < cc2) & (difl1 > difl2) & (ref_macd_1 < 0) & (diff < 0)
    bbb = (cc1 < cc3) & (difl1 < difl2) & (difl1 > difl3) & (ref_macd_1 < 0) & (diff < 0)
    ccc = (aaa | bbb) & (diff < 0)
    lll = (~ref_bool(ccc, 1)) & ccc
    xxx = (
        ref_bool(aaa, 1) & (difl1 <= difl2) & (diff < dea)
    ) | (
        ref_bool(bbb, 1) & (difl1 <= difl3) & (diff < dea)
    )
    jjj = ref_bool(ccc, 1) & (np.abs(ref_diff_1) >= (np.abs(diff) * 1.01))
    dxdx = (~ref_bool(jjj, 1)) & jjj

    ref_jjj_mm1_plus = ref_bool(jjj, mm1 + 1)
    ref_jjj_mm1 = ref_bool(jjj, mm1)
    djgxx = (
        ((close < cc2) | (close < cc1))
        & ((ref_jjj_mm1_plus | ref_jjj_mm1) & (~ref_bool(lll, 1)) & (rolling_count_bool(jjj, 24) >= 1))
    )
    djxx = (~(rolling_count_bool(ref_bool(djgxx, 1), 2) >= 1)) & djgxx
    _dxx = (xxx | djxx) & (~ccc)

    zjdbl = (ch1 > ch2) & (difh1 < difh2) & (ref_macd_1 > 0) & (diff > 0)
    gxdbl = (ch1 > ch3) & (difh1 > difh2) & (difh1 < difh3) & (ref_macd_1 > 0) & (diff > 0)
    dbbl = (zjdbl | gxdbl) & (diff > 0)
    _dbl = (~ref_bool(dbbl, 1)) & dbbl & (diff > dea)
    _dblxs = (
        ref_bool(zjdbl, 1) & (difh1 >= difh2) & (diff > dea)
    ) | (
        ref_bool(gxdbl, 1) & (difh1 >= difh3) & (diff > dea)
    )
    dbjg = ref_bool(dbbl, 1) & (ref_diff_1 >= (diff * 1.01))
    dbjgxc = ref_bool(~dbjg, 1) & dbjg

    return {
        "bottom_signal": bool(dxdx[-1]),
        "sell_signal": bool(dbjgxc[-1]),
    }


def fetch_yfinance_history(symbol: str, period: str, interval: str, attempts: int = 3):
    import yfinance as yf

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period, interval=interval, auto_adjust=True)
            if data.empty or "Close" not in data:
                raise ValueError("no price data returned")
            return data.dropna(subset=["Close"])
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise ValueError(f"yfinance unavailable after {attempts} attempts: {last_error}")


def fetch_yfinance_recent_price(symbol: str) -> float | None:
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        fast_info = getattr(ticker, "fast_info", {}) or {}
        for key in ("last_price", "regular_market_price", "previous_close"):
            try:
                value = fast_info.get(key) if hasattr(fast_info, "get") else getattr(fast_info, key)
            except Exception:
                value = None
            if value and is_valid_number(float(value)) and float(value) > 0:
                return float(value)

        data = ticker.history(period="5d", interval="1d", auto_adjust=True)
        if not data.empty and "Close" in data:
            return float(data["Close"].dropna().iloc[-1])
    except Exception:
        return None
    return None


def fetch_stooq_daily(symbol: str):
    import pandas as pd

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=430)
    stooq_symbol = symbol.lower().replace(".", "-") + ".us"
    url = (
        "https://stooq.com/q/d/l/"
        f"?s={stooq_symbol}&i=d&d1={start:%Y%m%d}&d2={end:%Y%m%d}"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        csv_text = response.read().decode("utf-8")

    if "Date," not in csv_text or "No data" in csv_text:
        raise ValueError("no usable Stooq CSV returned")

    data = pd.read_csv(StringIO(csv_text), parse_dates=["Date"])
    required_columns = {"Open", "High", "Low", "Close", "Volume"}
    if data.empty or not required_columns.issubset(data.columns):
        raise ValueError("Stooq response missing OHLCV columns")

    data = data.set_index("Date").sort_index()
    return data[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def fetch_daily_history(symbol: str) -> HistoryResult:
    try:
        data = fetch_yfinance_history(symbol, period="1y", interval="1d")
        close = float(data["Close"].iloc[-1])
        warning, invalid = validate_price(symbol, close)
        return HistoryResult(data=data, source="yfinance", warning=warning, price_invalid=invalid)
    except Exception as yf_error:
        try:
            fallback_data = fetch_stooq_daily(symbol)
        except Exception as fallback_error:
            raise ValueError(
                f"yfinance日线失败：{yf_error}；fallback失败：{fallback_error}"
            ) from fallback_error
        fallback_close = float(fallback_data["Close"].iloc[-1])
        warning_parts = [f"yfinance日线失败，使用fallback：{yf_error}"]
        warning, invalid = validate_price(symbol, fallback_close)
        if warning:
            warning_parts.append(warning)

        recent_price = fetch_yfinance_recent_price(symbol)
        if recent_price and recent_price > 0:
            diff_pct = abs(fallback_close / recent_price - 1) * 100
            if diff_pct > 20:
                invalid = True
                warning_parts.append(
                    f"fallback价格与yfinance最近价差异{diff_pct:.1f}%，禁止进入A榜"
                )
        else:
            warning_parts.append("fallback价格未能与yfinance最近价完成校验")

        return HistoryResult(
            data=fallback_data,
            source="fallback",
            warning="；".join(warning_parts),
            price_invalid=invalid,
        )


def validate_price(symbol: str, close: float) -> tuple[str, bool]:
    if not is_valid_number(close) or close <= 0:
        return f"{symbol} close价格无效：{close}", True
    if close > 100000:
        return f"{symbol} close价格异常偏高：{close}", True
    return "", False


def compute_daily_metrics(symbol: str, theme: str, benchmark_returns: dict[str, float]) -> DailyMetrics:
    history = fetch_daily_history(symbol)
    data = history.data
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

    ema23_series = close.ewm(span=23, adjust=False).mean()
    ema89_series = close.ewm(span=89, adjust=False).mean()
    ema23 = float(ema23_series.iloc[-1])
    ema89 = float(ema89_series.iloc[-1])
    high = data["High"] if "High" in data else close
    low = data["Low"] if "Low" in data else close
    up1_series = high.ewm(span=23, adjust=False).mean()
    dw1_series = low.ewm(span=23, adjust=False).mean()
    up2_series = high.ewm(span=89, adjust=False).mean()
    dw2_series = low.ewm(span=89, adjust=False).mean()
    up1 = float(up1_series.iloc[-1])
    dw1 = float(dw1_series.iloc[-1])
    up2 = float(up2_series.iloc[-1])
    dw2 = float(dw2_series.iloc[-1])
    current_close = float(close.iloc[-1])
    high_52w = float(close.tail(min(252, len(close))).max())
    rolling_peak = close.cummax()
    drawdown = close / rolling_peak - 1
    max_drawdown_pct = float(drawdown.min() * 100)

    recent_window = close.tail(20)
    previous_window = close.tail(60).head(40) if len(close) >= 60 else close.head(max(len(close) - 20, 1))
    recent_low = float(recent_window.min())
    previous_low = float(previous_window.min())
    recent_high = float(recent_window.max())
    previous_high = float(previous_window.max())
    higher_lows = recent_low > previous_low
    ema23_slope_5 = slope(ema23_series, 5)
    descending_channel = ema23_slope_5 < 0 or (recent_high < previous_high and recent_low < previous_low)

    distance_ema23_pct = (current_close / ema23 - 1) * 100
    distance_blue_lower_pct = ((current_close - dw1) / dw1) * 100 if dw1 else 999.0
    distance_52w_high_pct = (current_close / high_52w - 1) * 100
    ema23_gt_ema89 = ema23 > ema89
    close_gt_ema89 = current_close > ema89
    blue_channel_above_yellow = dw1 > dw2
    up1_gt_up2 = up1 > up2
    blue_channel_slope_up = slope(up1_series, 5) >= 0 and slope(dw1_series, 5) >= 0
    repair_profile = (
        max_drawdown_pct <= -25
        and distance_52w_high_pct <= -12
        and blue_channel_above_yellow
        and up1_gt_up2
        and current_close >= dw1 * 0.97
        and higher_lows
    )

    return DailyMetrics(
        symbol=symbol,
        theme=theme,
        close=current_close,
        data_source=history.source,
        data_warning=history.warning,
        price_invalid=history.price_invalid,
        return_1m=returns["1m"],
        return_3m=returns["3m"],
        return_6m=returns["6m"],
        return_12m=returns["12m"],
        rs_raw=rs_raw,
        ema23=ema23,
        ema89=ema89,
        ema23_slope_5=ema23_slope_5,
        up1=up1,
        dw1=dw1,
        up2=up2,
        dw2=dw2,
        blue_channel_above_yellow=blue_channel_above_yellow,
        up1_gt_up2=up1_gt_up2,
        blue_channel_slope_up=blue_channel_slope_up,
        distance_blue_lower_pct=distance_blue_lower_pct,
        distance_blue_lower_abs_pct=abs(distance_blue_lower_pct),
        rsi14=latest_rsi(close),
        distance_ema23_pct=distance_ema23_pct,
        distance_ema23_abs_pct=abs(distance_ema23_pct),
        distance_52w_high_pct=distance_52w_high_pct,
        avg_volume_20d=float(volume.tail(20).mean()),
        ema23_gt_ema89=ema23_gt_ema89,
        close_gt_ema89=close_gt_ema89,
        descending_channel=descending_channel,
        higher_lows=higher_lows,
        max_drawdown_pct=max_drawdown_pct,
        repair_profile=repair_profile,
    )


def assign_rs_scores(metrics: list[DailyMetrics]) -> None:
    ranked = sorted(metrics, key=lambda item: item.rs_raw)
    total = len(ranked)
    for index, item in enumerate(ranked, start=1):
        item.rs_score = round((index / total) * 100, 1)


def resample_to_2h(data):
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in data:
        agg["Volume"] = "sum"
    return data.resample("2h").agg(agg).dropna(subset=["Close"])


def compute_intraday_setup(symbol: str) -> IntradaySetup:
    try:
        data_1h = fetch_yfinance_history(symbol, period="60d", interval="1h", attempts=2)
    except Exception as exc:
        return IntradaySetup(warning=f"1H/2H数据不可用：{exc}")

    close_1h = data_1h["Close"]
    if len(close_1h) < 30:
        return IntradaySetup(warning="1H数据不足，无法确认回踩")

    ema23_1h = close_1h.ewm(span=23, adjust=False).mean()
    rsi_1h_series = rsi_series(close_1h, 14)
    rsi_1h = float(rsi_1h_series.iloc[-1])
    proxy_warning_1h = (
        float(rsi_1h_series.tail(10).min()) < 45
        and rsi_1h > 45
        and float(close_1h.iloc[-1]) > float(ema23_1h.iloc[-1])
    )
    one_hour_signals = compute_cd_docx_signals(data_1h)
    early_signal_1h = one_hour_signals["bottom_signal"]
    sell_signal_1h = one_hour_signals["sell_signal"]

    data_2h = resample_to_2h(data_1h)
    close_2h = data_2h["Close"]
    if len(close_2h) < 30:
        return IntradaySetup(
            early_signal_1h=bool(early_signal_1h),
            proxy_warning=bool(proxy_warning_1h),
            sell_signal_1h=bool(sell_signal_1h),
            rsi_1h=rsi_1h,
            warning="2H数据不足，无法确认回踩",
        )

    ema23_2h = close_2h.ewm(span=23, adjust=False).mean()
    ema89_2h = close_2h.ewm(span=89, adjust=False).mean()
    rsi_2h_series = rsi_series(close_2h, 14)
    rsi_2h = float(rsi_2h_series.iloc[-1])
    ema23_2h_slope = slope(ema23_2h, 5)
    distance_2h_ema23_pct = (float(close_2h.iloc[-1]) / float(ema23_2h.iloc[-1]) - 1) * 100
    proxy_warning_2h = (
        float(rsi_2h_series.tail(10).min()) < 45
        and rsi_2h > 45
        and float(close_2h.iloc[-1]) > float(ema23_2h.iloc[-1])
        and ema23_2h_slope >= 0
    )
    two_hour_signals = compute_cd_docx_signals(data_2h)
    two_hour_bottom_signal = two_hour_signals["bottom_signal"]
    sell_signal_2h = two_hour_signals["sell_signal"]
    overheated_2h = rsi_2h > 72 or distance_2h_ema23_pct > 8 or float(close_2h.iloc[-1]) > float(ema89_2h.iloc[-1]) * 1.18

    return IntradaySetup(
        early_signal_1h=bool(early_signal_1h),
        early_signal_1h_source="DXDX",
        two_hour_bottom_signal=bool(two_hour_bottom_signal),
        two_hour_signal_source="DXDX",
        proxy_warning=bool(proxy_warning_1h or proxy_warning_2h),
        sell_signal_1h=bool(sell_signal_1h),
        sell_signal_2h=bool(sell_signal_2h),
        rsi_1h=rsi_1h,
        rsi_2h=rsi_2h,
        ema23_2h_slope_5=ema23_2h_slope,
        overheated_2h=bool(overheated_2h),
    )


def top_themes_from_market(metrics: list[DailyMetrics]) -> list[dict[str, object]]:
    valid = [item for item in metrics if not item.price_invalid]
    top_stocks = sorted(valid, key=lambda item: item.rs_score, reverse=True)[:TOP_STRONG_STOCKS]
    grouped: dict[str, list[DailyMetrics]] = {}
    for item in top_stocks:
        grouped.setdefault(item.theme, []).append(item)

    theme_rows = []
    for theme, items in grouped.items():
        avg_rs = sum(item.rs_score for item in items) / len(items)
        top_symbols = "、".join(item.symbol for item in sorted(items, key=lambda row: row.rs_score, reverse=True)[:5])
        theme_rows.append(
            {
                "theme": theme,
                "count": len(items),
                "avg_rs_score": round(avg_rs, 1),
                "top_symbols": top_symbols,
            }
        )
    return sorted(theme_rows, key=lambda row: (-int(row["count"]), -float(row["avg_rs_score"])))[:TOP_THEME_LIMIT]


def setup_grade(distance_abs_pct: float, two_hour_bottom_signal: bool, early_signal_1h: bool) -> str:
    if distance_abs_pct > 10:
        return "C"
    if two_hour_bottom_signal and distance_abs_pct <= 5 and early_signal_1h:
        return "A+"
    if two_hour_bottom_signal and distance_abs_pct <= 7:
        return "A"
    if early_signal_1h or distance_abs_pct <= 10:
        return "B"
    return "C"


def classify_stock(
    item: DailyMetrics,
    strong_themes: set[str],
    min_rs_score: float,
) -> ScreenedStock:
    intraday = compute_intraday_setup(item.symbol)
    rs_ok = item.rs_score >= min_rs_score
    in_strong_theme = item.theme in strong_themes
    daily_position_ok = (
        item.blue_channel_above_yellow
        and item.up1_gt_up2
        and item.blue_channel_slope_up
        and not item.descending_channel
    )
    distance_ok_for_a = item.distance_blue_lower_abs_pct <= 7
    overheated = item.distance_blue_lower_pct > 10 or item.rsi14 > 75 or intraday.overheated_2h or intraday.sell_signal_2h
    grade = setup_grade(item.distance_blue_lower_abs_pct, intraday.two_hour_bottom_signal, intraday.early_signal_1h)

    warning_parts = [part for part in [item.data_warning, intraday.warning] if part]
    data_warning = "；".join(warning_parts)
    if item.price_invalid:
        return screened_from_parts(
            item,
            intraday,
            list_name="DATA_WARNING",
            setup_class="DATA_WARNING",
            setup_grade="DATA",
            overheated=overheated,
            data_warning=data_warning or "价格异常，禁止进入A榜",
            reason="价格或数据源异常，只进入数据异常列表",
        )

    a1 = (
        in_strong_theme
        and rs_ok
        and daily_position_ok
        and distance_ok_for_a
        and intraday.two_hour_bottom_signal
        and not overheated
        and not item.repair_profile
    )
    a2 = (
        item.repair_profile
        and daily_position_ok
        and item.higher_lows
        and distance_ok_for_a
        and intraday.two_hour_bottom_signal
        and not overheated
    )

    if a1:
        return screened_from_parts(
            item,
            intraday,
            list_name="A",
            setup_class="A1_MAIN_LEADER",
            setup_grade=grade,
            overheated=overheated,
            data_warning=data_warning,
            reason="主线强股，日线蓝色通道在黄色通道上方，靠近DW1，2H真实DXDX抄底",
        )
    if a2:
        return screened_from_parts(
            item,
            intraday,
            list_name="A",
            setup_class="A2_REPAIR_LEADER",
            setup_grade=grade,
            overheated=overheated,
            data_warning=data_warning,
            reason="暴跌修复后转强，A2类，只允许小仓观察，不按主线强股处理",
        )

    reasons = []
    if overheated or item.distance_blue_lower_abs_pct > 10:
        reasons.append("距离日线蓝色下边缘DW1偏远/过热，或出现卖出信号，不追")
        list_name = "C"
        setup_class = "OVERHEATED"
    elif rs_ok and daily_position_ok and (intraday.early_signal_1h or intraday.proxy_warning or item.distance_blue_lower_abs_pct <= 10):
        if in_strong_theme and not intraday.two_hour_bottom_signal:
            reasons.append("主线内强股，但2H真实DXDX抄底未出现")
        elif not in_strong_theme:
            reasons.append("个股强，但主题不是今日主线")
        if intraday.early_signal_1h and not intraday.two_hour_bottom_signal:
            reasons.append("只有1H真实DXDX抄底，先观察")
        if intraday.proxy_warning and not intraday.two_hour_bottom_signal:
            reasons.append("只有proxy预警，不能进A榜")
        if 7 < item.distance_blue_lower_abs_pct <= 10:
            reasons.append("距离日线蓝色下边缘DW1为7%到10%，降级观察")
        if item.repair_profile:
            reasons.append("修复转强股，等待2H抄底后再看小仓")
        list_name = "B"
        setup_class = "WATCH"
    else:
        if not rs_ok:
            reasons.append("RS不足")
        if not daily_position_ok:
            reasons.append("日线蓝黄通道位置不合格")
        if item.descending_channel:
            reasons.append("仍有下降通道特征")
        if not intraday.two_hour_bottom_signal:
            reasons.append("2H真实DXDX抄底未出现")
        list_name = "DROPPED"
        setup_class = "DROPPED"

    return screened_from_parts(
        item,
        intraday,
        list_name=list_name,
        setup_class=setup_class,
        setup_grade=grade,
        overheated=overheated,
        data_warning=data_warning,
        reason="；".join(dict.fromkeys(reasons)) if reasons else "未达到A榜条件",
    )


def screened_from_parts(
    item: DailyMetrics,
    intraday: IntradaySetup,
    list_name: str,
    setup_class: str,
    setup_grade: str,
    overheated: bool,
    data_warning: str,
    reason: str,
) -> ScreenedStock:
    return ScreenedStock(
        symbol=item.symbol,
        theme=item.theme,
        list_name=list_name,
        setup_class=setup_class,
        setup_grade=setup_grade,
        close=item.close,
        data_source=item.data_source,
        data_warning=data_warning,
        price_invalid=item.price_invalid,
        rs_score=item.rs_score,
        ema23=item.ema23,
        ema89=item.ema89,
        up1=item.up1,
        dw1=item.dw1,
        up2=item.up2,
        dw2=item.dw2,
        blue_channel_above_yellow=item.blue_channel_above_yellow,
        up1_gt_up2=item.up1_gt_up2,
        blue_channel_slope_up=item.blue_channel_slope_up,
        distance_blue_lower_pct=item.distance_blue_lower_pct,
        distance_blue_lower_abs_pct=item.distance_blue_lower_abs_pct,
        distance_ema23_pct=item.distance_ema23_pct,
        distance_ema23_abs_pct=item.distance_ema23_abs_pct,
        distance_52w_high_pct=item.distance_52w_high_pct,
        ema23_gt_ema89=item.ema23_gt_ema89,
        close_gt_ema89=item.close_gt_ema89,
        descending_channel=item.descending_channel,
        higher_lows=item.higher_lows,
        max_drawdown_pct=item.max_drawdown_pct,
        early_signal_1h=intraday.early_signal_1h,
        early_signal_1h_source=intraday.early_signal_1h_source,
        two_hour_bottom_signal=intraday.two_hour_bottom_signal,
        two_hour_signal_source=intraday.two_hour_signal_source,
        proxy_warning=intraday.proxy_warning,
        sell_signal_1h=intraday.sell_signal_1h,
        sell_signal_2h=intraday.sell_signal_2h,
        rsi_1h=intraday.rsi_1h,
        rsi_2h=intraday.rsi_2h,
        overheated=overheated,
        downgrade_reason=reason,
    )


def screen_market(
    universe: dict[str, str],
    top_buy: int,
    top_watch: int,
    min_rs_score: float,
) -> tuple[list[ScreenedStock], list[dict[str, object]], list[str]]:
    errors: list[str] = []
    try:
        benchmark_history = fetch_daily_history(BENCHMARK)
        benchmark_close = benchmark_history.data["Close"]
        benchmark_returns = {
            "1m": pct_return(benchmark_close, 21),
            "3m": pct_return(benchmark_close, 63),
            "6m": pct_return(benchmark_close, 126),
            "12m": pct_return(benchmark_close, 252),
        }
        if benchmark_history.warning:
            errors.append(f"{BENCHMARK}：{benchmark_history.warning}")
    except Exception as exc:
        errors.append(f"{BENCHMARK}基准不可用，改用绝对动量：{exc}")
        benchmark_returns = {"1m": 0.0, "3m": 0.0, "6m": 0.0, "12m": 0.0}

    metrics: list[DailyMetrics] = []
    for symbol, theme in sorted(universe.items()):
        try:
            metrics.append(compute_daily_metrics(symbol, theme, benchmark_returns))
        except Exception as exc:
            errors.append(f"{symbol}：日线数据不可用：{exc}")

    if not metrics:
        return [], [], errors

    assign_rs_scores(metrics)
    top_themes = top_themes_from_market(metrics)
    strong_themes = {str(row["theme"]) for row in top_themes}
    top_market = sorted(metrics, key=lambda item: item.rs_score, reverse=True)[:TOP_STRONG_STOCKS]
    screened = [classify_stock(item, strong_themes, min_rs_score) for item in top_market]

    return sort_screened_rows(screened, top_buy, top_watch), top_themes, errors


def sort_screened_rows(rows: list[ScreenedStock], top_buy: int, top_watch: int) -> list[ScreenedStock]:
    class_order = {"A1_MAIN_LEADER": 0, "A2_REPAIR_LEADER": 1}
    grade_order = {"A+": 0, "A": 1, "B": 2, "C": 3, "DATA": 4}

    a_rows = sorted(
        (row for row in rows if row.list_name == "A"),
        key=lambda row: (
            class_order.get(row.setup_class, 9),
            grade_order.get(row.setup_grade, 9),
            -row.rs_score,
            row.distance_blue_lower_abs_pct,
        ),
    )[: min(top_buy, REPORT_LIMIT)]
    b_rows = sorted(
        (row for row in rows if row.list_name == "B"),
        key=lambda row: (not row.early_signal_1h, not row.proxy_warning, row.distance_blue_lower_abs_pct, -row.rs_score),
    )[: min(top_watch, REPORT_LIMIT)]
    c_rows = sorted(
        (row for row in rows if row.list_name == "C"),
        key=lambda row: (-row.rs_score, -row.distance_blue_lower_abs_pct),
    )[:REPORT_LIMIT]
    data_rows = sorted(
        (row for row in rows if row.list_name == "DATA_WARNING"),
        key=lambda row: row.symbol,
    )
    dropped = sorted(
        (row for row in rows if row.list_name == "DROPPED"),
        key=lambda row: -row.rs_score,
    )
    selected = {row.symbol for row in [*a_rows, *b_rows, *c_rows, *data_rows, *dropped]}
    overflow = [row for row in rows if row.symbol not in selected]
    return [*a_rows, *b_rows, *c_rows, *data_rows, *overflow, *dropped]


def yes_no(value: bool) -> str:
    return "是" if value else "否"


def signal_label(active: bool, source: str) -> str:
    if not active:
        if source == "missing_custom":
            return "否（无真实信号）"
        return "否"
    if source == "custom":
        return "是"
    if source == "proxy":
        return "proxy信号"
    return "是"


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def fmt_rs(value: float) -> str:
    return f"{value:.0f}"


def row_type_label(row: ScreenedStock) -> str:
    if row.setup_class == "A1_MAIN_LEADER":
        return "A1主线强股"
    if row.setup_class == "A2_REPAIR_LEADER":
        return "A2修复转强"
    if row.setup_class == "OVERHEATED":
        return "过热"
    if row.setup_class == "DATA_WARNING":
        return "数据异常"
    return "观察"


def build_report(
    rows: list[ScreenedStock],
    top_themes: list[dict[str, object]],
    errors: list[str],
    min_rs_score: float,
) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    a_rows = [row for row in rows if row.list_name == "A"][:REPORT_LIMIT]
    b_rows = [row for row in rows if row.list_name == "B"][:REPORT_LIMIT]
    c_rows = [row for row in rows if row.list_name == "C"][:REPORT_LIMIT]
    data_rows = [row for row in rows if row.list_name == "DATA_WARNING"]

    sections = [
        "# 强势股回踩系统 V3",
        f"生成时间：{generated_at}｜最低RS：{min_rs_score:.0f}",
        "## 今日结论\n" + "\n".join(build_conclusion_lines(top_themes, a_rows, b_rows, c_rows)),
        "## 强势主题Top 3\n" + build_theme_lines(top_themes),
        "## A榜：最多5只\n" + build_a_lines(a_rows),
        "## B榜：最多5只\n" + build_watch_lines(b_rows, empty_text="今日没有合格观察票。"),
        "## C榜：最多5只\n" + build_watch_lines(c_rows, empty_text="今日没有明显过热票。"),
        "## 风险提示\n" + build_risk_lines(rows),
        "## Data Warnings\n" + build_data_warning_lines(data_rows, rows, errors),
        "## 免责声明\n" + "\n".join(DISCLAIMER_LINES),
    ]
    return "\n\n".join(sections) + "\n"


def build_plain_text_report(
    rows: list[ScreenedStock],
    top_themes: list[dict[str, object]],
) -> str:
    a_rows = [row for row in rows if row.list_name == "A"][:3]
    b_rows = [row for row in rows if row.list_name == "B"][:3]
    c_rows = [row for row in rows if row.list_name == "C"][:2]

    lines = [
        "【强势股回踩 V3】",
        "今日结论：",
        build_plain_focus_line(a_rows),
        build_plain_far_line([*b_rows, *c_rows]),
        build_plain_signal_line(a_rows),
        "主线：" + build_plain_theme_line(top_themes),
        "A榜：",
    ]
    lines.extend(build_plain_a_lines(a_rows))
    lines.append("B榜：")
    lines.extend(build_plain_b_lines(b_rows))
    lines.append("不追：")
    lines.extend(build_plain_c_lines(c_rows))
    lines.extend(build_plain_risk_lines())
    return "\n".join(lines[:20]) + "\n"


def build_plain_focus_line(a_rows: list[ScreenedStock]) -> str:
    if not a_rows:
        return "今日没有A榜，宁可空手。"
    symbols = "、".join(row.symbol for row in a_rows)
    return f"只看 {symbols}。"


def build_plain_far_line(rows: list[ScreenedStock]) -> str:
    far_symbols = [row.symbol for row in rows if row.distance_blue_lower_abs_pct > 7]
    if not far_symbols:
        return "距离DW1超过7%的票不进A榜。"
    return f"{'、'.join(far_symbols[:4])} 距离DW1偏远，不追。"


def build_plain_signal_line(a_rows: list[ScreenedStock]) -> str:
    if a_rows:
        return "A榜都满足：日线靠近DW1 + 2H真实DXDX抄底。"
    return "当前没有2H真实DXDX抄底，A榜为空。"


def build_plain_theme_line(top_themes: list[dict[str, object]]) -> str:
    if not top_themes:
        return "数据不足，暂不判断。"
    labels = []
    for index, row in enumerate(top_themes[:3], start=1):
        suffix = "最强" if index == 1 else "第二" if index == 2 else "第三"
        labels.append(f"{index}、{row['theme']}{suffix}")
    return "；".join(labels) + "。"


def build_plain_a_lines(rows: list[ScreenedStock]) -> list[str]:
    if not rows:
        return ["无。"]
    lines = []
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}、{row.symbol}｜{plain_type_label(row)}｜距DW1 {fmt_pct(row.distance_blue_lower_abs_pct)}"
            f"｜2H真实抄底：{yes_no(row.two_hour_bottom_signal)}｜1H真实抄底：{yes_no(row.early_signal_1h)}"
            f"｜proxy预警：{yes_no(row.proxy_warning)}｜{plain_action_label(row)}。"
        )
    return lines


def build_plain_b_lines(rows: list[ScreenedStock]) -> list[str]:
    if not rows:
        return ["无。"]
    return [
        f"{row.symbol}｜距DW1 {fmt_pct(row.distance_blue_lower_abs_pct)}，{plain_reason(row)}。"
        for row in rows
    ]


def build_plain_c_lines(rows: list[ScreenedStock]) -> list[str]:
    if not rows:
        return ["无。"]
    return [
        f"{row.symbol}｜距DW1 {fmt_pct(row.distance_blue_lower_abs_pct)}，太远/不追。"
        for row in rows
    ]


def build_plain_risk_lines() -> list[str]:
    return [
        "风险：proxy只作为辅助预警，不能作为A榜依据。",
        "真实抄底来自 cd.docx 的 DXDX，卖出来自 DBJGXC。",
        "真实买点必须：日线蓝线在黄线上方 + 靠近DW1 + 2H真实DXDX。",
    ]


def plain_type_label(row: ScreenedStock) -> str:
    if row.setup_class == "A1_MAIN_LEADER":
        return "主线强股"
    if row.setup_class == "A2_REPAIR_LEADER":
        return "修复转强"
    return row_type_label(row)


def plain_2h_label(row: ScreenedStock) -> str:
    return "2H真实" if row.two_hour_bottom_signal else "2H否"


def plain_action_label(row: ScreenedStock) -> str:
    if row.setup_class == "A2_REPAIR_LEADER":
        return "小仓观察"
    return "观察"


def plain_reason(row: ScreenedStock) -> str:
    if row.setup_class == "A2_REPAIR_LEADER" or "修复" in row.downgrade_reason:
        return "修复股，小仓观察"
    if row.distance_blue_lower_abs_pct > 7:
        return "偏远，等回踩"
    if "主题不是今日主线" in row.downgrade_reason:
        return "不是今日主线，观察"
    if row.early_signal_1h and not row.two_hour_bottom_signal:
        return "只有1H真实抄底，等2H"
    if row.proxy_warning and not row.two_hour_bottom_signal:
        return "只有proxy预警，不能进A榜"
    return row.downgrade_reason.split("；")[0]


def build_email_subject(rows: list[ScreenedStock]) -> str:
    a_symbols = [row.symbol for row in rows if row.list_name == "A"][:3]
    if a_symbols:
        return f"【真实抄底】强势股回踩 V3：{'、'.join(a_symbols)}"
    return "【观察版】强势股回踩 V3：无真实2H抄底"


def build_conclusion_lines(
    top_themes: list[dict[str, object]],
    a_rows: list[ScreenedStock],
    b_rows: list[ScreenedStock],
    c_rows: list[ScreenedStock],
) -> list[str]:
    lines = []
    if top_themes:
        main_theme = top_themes[0]["theme"]
        second = f"，其次是{top_themes[1]['theme']}" if len(top_themes) > 1 else ""
        lines.append(f"1、今日主线：{main_theme}最强{second}。")
    else:
        lines.append("1、今日主线：数据不足，暂不判断。")

    a1_symbols = [row.symbol for row in a_rows if row.setup_class == "A1_MAIN_LEADER"]
    a2_rows = [row for row in a_rows if row.setup_class == "A2_REPAIR_LEADER"]
    if a1_symbols:
        lines.append(f"2、A榜优先看：{'、'.join(a1_symbols)}。")
    else:
        lines.append("2、A榜暂无主线强股的舒服买点。")

    far_rows = [row for row in [*b_rows, *c_rows] if row.distance_blue_lower_abs_pct > 7]
    if far_rows:
        examples = "、".join(row.symbol for row in far_rows[:3])
        lines.append(f"3、{examples}距离日线蓝色下边缘DW1偏远，降级观察或不追。")
    else:
        lines.append("3、距离日线蓝色下边缘DW1超过7%的票不会进入A榜。")

    if a2_rows:
        for row in a2_rows:
            lines.append(f"4、{row.symbol}属于暴跌修复后转强，A2类，小仓观察。")
    else:
        lines.append("4、修复转强股单独看待，不按主线强股处理。")

    if c_rows:
        lines.append("5、C榜过热票不追；今日没有舒服买点则宁可空手。")
    else:
        lines.append("5、今日没有舒服买点则宁可空手。")
    return lines


def build_theme_lines(top_themes: list[dict[str, object]]) -> str:
    if not top_themes:
        return "暂无足够数据。"
    return "\n".join(
        f"{index}、{row['theme']}：{row['top_symbols']}。"
        for index, row in enumerate(top_themes, start=1)
    )


def build_a_lines(rows: list[ScreenedStock]) -> str:
    if not rows:
        return "今日没有A榜。只有1H真实DXDX或proxy预警不进A榜，必须等2H真实DXDX。"

    lines = []
    for index, row in enumerate(rows, start=1):
        suffix = "｜小仓观察" if row.setup_class == "A2_REPAIR_LEADER" else ""
        lines.append(
            f"{index}、{row.symbol}｜{row_type_label(row)}｜{row.setup_grade}｜RS {fmt_rs(row.rs_score)}"
            f"｜距DW1 {fmt_pct(row.distance_blue_lower_abs_pct)}"
            f"｜2H真实抄底：{yes_no(row.two_hour_bottom_signal)}"
            f"｜1H真实抄底：{yes_no(row.early_signal_1h)}"
            f"｜proxy预警：{yes_no(row.proxy_warning)}{suffix}。"
        )
        if row.setup_class == "A2_REPAIR_LEADER":
            lines.append(
                f"{row.symbol}：暴跌修复后转强，A2类，只允许小仓观察，不按主线强股处理。"
            )
    return "\n".join(lines)


def build_watch_lines(rows: list[ScreenedStock], empty_text: str) -> str:
    if not rows:
        return empty_text
    return "\n".join(
        f"{row.symbol}：{row.downgrade_reason}｜RS {fmt_rs(row.rs_score)}"
        f"｜距DW1 {fmt_pct(row.distance_blue_lower_abs_pct)}"
        f"｜2H真实抄底：{yes_no(row.two_hour_bottom_signal)}"
        f"｜1H真实抄底：{yes_no(row.early_signal_1h)}"
        f"｜proxy预警：{yes_no(row.proxy_warning)}。"
        for row in rows
    )


def build_risk_lines(rows: list[ScreenedStock]) -> str:
    reason_counter = Counter()
    for row in rows:
        if row.list_name in {"B", "C", "DROPPED", "DATA_WARNING"}:
            for reason in row.downgrade_reason.split("；"):
                if reason:
                    reason_counter[reason] += 1

    lines = [
        "1、日线蓝黄通道定位置，2H真实DXDX定买点，1H真实DXDX只做提前预警。",
        "2、距离日线蓝色下边缘DW1超过7%的股票不进A榜。",
        "3、A2修复转强股只允许小仓观察，不按主线强股处理。",
    ]
    if reason_counter:
        summary = "；".join(f"{reason} {count}只" for reason, count in reason_counter.most_common(3))
        lines.append(f"4、今日主要降级原因：{summary}。")
    return "\n".join(lines)


def build_data_warning_lines(
    data_rows: list[ScreenedStock],
    rows: list[ScreenedStock],
    errors: list[str],
) -> str:
    warnings = []
    if any(row.proxy_warning for row in rows):
        warnings.append("proxy预警只代表RSI回升+收回短线均线，不能作为A榜依据；A榜只认cd.docx的2H DXDX。")
    for row in rows:
        if row.data_warning:
            warnings.append(f"{row.symbol}：{row.data_warning}")
    for row in data_rows:
        if not row.data_warning:
            warnings.append(f"{row.symbol}：价格异常，禁止进入A榜")
    warnings.extend(errors)
    if not warnings:
        return "无。"
    unique = list(dict.fromkeys(warnings))
    return "\n".join(f"- {warning}" for warning in unique[:20])


def write_csv(rows: list[ScreenedStock]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "list",
                "setup_class",
                "setup_grade",
                "symbol",
                "theme",
                "close",
                "data_source",
                "data_warning",
                "price_invalid",
                "rs_score",
                "ema23",
                "ema89",
                "up1",
                "dw1",
                "up2",
                "dw2",
                "blue_channel_above_yellow",
                "up1_gt_up2",
                "blue_channel_slope_up",
                "distance_blue_lower_pct",
                "distance_blue_lower_abs_pct",
                "distance_ema23_pct",
                "distance_ema23_abs_pct",
                "distance_52w_high_pct",
                "ema23_gt_ema89",
                "close_gt_ema89",
                "descending_channel",
                "higher_lows",
                "max_drawdown_pct",
                "two_hour_bottom_signal",
                "two_hour_signal_source",
                "early_signal_1h",
                "early_signal_1h_source",
                "proxy_warning",
                "sell_signal_1h",
                "sell_signal_2h",
                "rsi_1h",
                "rsi_2h",
                "overheated",
                "downgrade_reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "list": row.list_name,
                    "setup_class": row.setup_class,
                    "setup_grade": row.setup_grade,
                    "symbol": row.symbol,
                    "theme": row.theme,
                    "close": round(row.close, 2),
                    "data_source": row.data_source,
                    "data_warning": row.data_warning,
                    "price_invalid": row.price_invalid,
                    "rs_score": round(row.rs_score, 1),
                    "ema23": round(row.ema23, 2),
                    "ema89": round(row.ema89, 2),
                    "up1": round(row.up1, 2),
                    "dw1": round(row.dw1, 2),
                    "up2": round(row.up2, 2),
                    "dw2": round(row.dw2, 2),
                    "blue_channel_above_yellow": row.blue_channel_above_yellow,
                    "up1_gt_up2": row.up1_gt_up2,
                    "blue_channel_slope_up": row.blue_channel_slope_up,
                    "distance_blue_lower_pct": round(row.distance_blue_lower_pct, 2),
                    "distance_blue_lower_abs_pct": round(row.distance_blue_lower_abs_pct, 2),
                    "distance_ema23_pct": round(row.distance_ema23_pct, 2),
                    "distance_ema23_abs_pct": round(row.distance_ema23_abs_pct, 2),
                    "distance_52w_high_pct": round(row.distance_52w_high_pct, 2),
                    "ema23_gt_ema89": row.ema23_gt_ema89,
                    "close_gt_ema89": row.close_gt_ema89,
                    "descending_channel": row.descending_channel,
                    "higher_lows": row.higher_lows,
                    "max_drawdown_pct": round(row.max_drawdown_pct, 2),
                    "two_hour_bottom_signal": row.two_hour_bottom_signal,
                    "two_hour_signal_source": row.two_hour_signal_source,
                    "early_signal_1h": row.early_signal_1h,
                    "early_signal_1h_source": row.early_signal_1h_source,
                    "proxy_warning": row.proxy_warning,
                    "sell_signal_1h": row.sell_signal_1h,
                    "sell_signal_2h": row.sell_signal_2h,
                    "rsi_1h": round(row.rsi_1h, 1),
                    "rsi_2h": round(row.rsi_2h, 1),
                    "overheated": row.overheated,
                    "downgrade_reason": row.downgrade_reason,
                }
            )


def write_markdown(report: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MD_PATH.write_text(report, encoding="utf-8")


def write_text_report(report: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TXT_PATH.write_text(report, encoding="utf-8")


def main() -> int:
    args = parse_args()
    send_email = args.send_email or env_flag("SEND_EMAIL", default=False)

    try:
        universe = load_universe(args.universe_file)
        print("Using cd.docx DXDX/DBJGXC formula for real 1H/2H signals")
        rows, top_themes, errors = screen_market(
            universe=universe,
            top_buy=args.top_buy,
            top_watch=args.top_watch,
            min_rs_score=args.min_rs_score,
        )
    except Exception as exc:
        rows = []
        top_themes = []
        errors = [f"系统运行失败：{exc}"]
        print(f"ERROR: {errors[0]}", file=sys.stderr)
        traceback.print_exc()

    report = build_report(rows, top_themes, errors, args.min_rs_score)
    plain_report = build_plain_text_report(rows, top_themes)
    email_subject = build_email_subject(rows)
    write_csv(rows)
    write_markdown(report)
    write_text_report(plain_report)
    print(f"Wrote {CSV_PATH}")
    print(f"Wrote {MD_PATH}")
    print(f"Wrote {TXT_PATH}")

    if send_email:
        a_count = sum(1 for row in rows if row.list_name == "A")
        try:
            send_report_email(
                report_body=plain_report,
                report_paths=[CSV_PATH, MD_PATH, TXT_PATH],
                a_candidate_count=a_count,
                subject_override=email_subject,
            )
        except EmailConfigError as exc:
            print(f"Missing SMTP config: {exc}", file=sys.stderr)
        except Exception as exc:
            print(f"Email send failed: {exc!r}", file=sys.stderr)
            traceback.print_exc()
    else:
        print("Email sending skipped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
