from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from .config import ROOT


YFINANCE_CACHE_DIR = ROOT / "data" / "yfinance_cache"
YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
NASDAQ_PE_CACHE_FILE = YFINANCE_CACHE_DIR / "nasdaq_pe.json"
try:
    yf.set_tz_cache_location(str(YFINANCE_CACHE_DIR))
except AttributeError:
    pass


def _flatten(data: pd.DataFrame) -> pd.DataFrame:
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)
    return data


def history(symbol: str, period: str = "1y", start: str | None = None) -> pd.DataFrame:
    if start:
        data = yf.download(symbol, start=start, auto_adjust=False, progress=False, threads=False, timeout=20)
    else:
        data = yf.download(symbol, period=period, auto_adjust=False, progress=False, threads=False, timeout=20)
    data = _flatten(data)
    if data.empty or "Close" not in data:
        raise RuntimeError(f"No price history for {symbol}")
    return data.dropna(subset=["Close"])


def latest_price(symbol: str) -> dict:
    if symbol.upper() == "BTC":
        symbol = "BTC-USD"
    data = history(symbol, period="1y")
    close = data["Close"].dropna()
    current = float(close.iloc[-1])
    high = float(close.max())
    low = float(close.min())
    first = float(close.iloc[0])
    ma200 = float(close.tail(min(200, len(close))).mean())
    return {
        "symbol": symbol,
        "price": current,
        "high_1y": high,
        "low_1y": low,
        "drawdown_pct": (current / high - 1.0) * 100 if high else 0.0,
        "return_1y_pct": (current / first - 1.0) * 100 if first else 0.0,
        "ma200": ma200,
        "below_ma200_pct": (current / ma200 - 1.0) * 100 if ma200 else 0.0,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _positive_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _read_last_valid_trailing_pe() -> dict | None:
    if not NASDAQ_PE_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(NASDAQ_PE_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pe = _positive_float(data.get("pe"))
    if pe is None:
        return None
    return {
        "symbol": data.get("symbol") or "unknown",
        "pe": pe,
        "field": "trailingPE",
        "source": "last_valid_trailingPE",
        "cached_at": data.get("updated_at"),
    }


def _write_last_valid_trailing_pe(item: dict) -> None:
    payload = {
        "symbol": item["symbol"],
        "pe": item["pe"],
        "field": "trailingPE",
        "updated_at": item["updated_at"],
    }
    try:
        NASDAQ_PE_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def nasdaq_pe() -> dict:
    errors = []
    quotes = {}
    for symbol in ["QQQ", "QQQM", "^NDX"]:
        try:
            info = yf.Ticker(symbol).info or {}
            trailing_pe = _positive_float(info.get("trailingPE"))
            forward_pe = _positive_float(info.get("forwardPE"))
            quotes[symbol] = {"trailingPE": trailing_pe, "forwardPE": forward_pe}
            if trailing_pe is not None:
                item = {
                    "symbol": symbol,
                    "pe": trailing_pe,
                    "field": "trailingPE",
                    "source": "live",
                    "forward_pe": forward_pe,
                    "quotes": quotes,
                    "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                }
                _write_last_valid_trailing_pe(item)
                return item
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
    cached = _read_last_valid_trailing_pe()
    if cached:
        cached["forward_pe"] = None
        cached["quotes"] = quotes
        cached["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return cached
    raise RuntimeError("No Nasdaq trailingPE from yfinance" + (f" ({'; '.join(errors)})" if errors else ""))


def monthly_trend(symbol: str, months: int = 10) -> dict:
    data = history(symbol, period="5y")
    close = data["Close"].dropna()
    monthly_close = close.resample("M").last().dropna()
    if len(monthly_close) < months:
        raise RuntimeError(f"Not enough monthly history for {symbol}")
    latest_month_close = float(monthly_close.iloc[-1])
    ma = float(monthly_close.tail(months).mean())
    return {
        "monthly_close": latest_month_close,
        "ma10m": ma,
        "below_ma10m": latest_month_close < ma,
        "ma10m_distance_pct": (latest_month_close / ma - 1.0) * 100 if ma else 0.0,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def btc_history() -> pd.DataFrame:
    df = yf.download("BTC-USD", start="2011-01-01", interval="1d", auto_adjust=False, progress=False, threads=False, timeout=30)
    df = _flatten(df).reset_index()
    if df.empty:
        raise RuntimeError("BTC price data is empty")
    if "Date" not in df:
        df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df["Price"] = pd.to_numeric(df["Close"], errors="coerce")
    return df.dropna(subset=["Price"])


BTC_GENESIS = pd.Timestamp("2009-01-03")


def _block_reward(date: pd.Timestamp) -> float:
    halvings = [
        (pd.Timestamp("2009-01-03"), 50.0),
        (pd.Timestamp("2012-11-28"), 25.0),
        (pd.Timestamp("2016-07-09"), 12.5),
        (pd.Timestamp("2020-05-11"), 6.25),
        (pd.Timestamp("2024-04-20"), 3.125),
    ]
    reward = 50.0
    for start, value in halvings:
        if date >= start:
            reward = value
    return reward


def btc_indicators() -> pd.DataFrame:
    df = btc_history()
    out = df[["Date", "Price"]].copy()

    ma_days = 200 * 7
    out["ma_200w"] = out["Price"].rolling(ma_days, min_periods=ma_days).mean()
    out["deviation"] = np.log(out["Price"] / out["ma_200w"])
    out["proxy_z"] = (
        out["deviation"] - out["deviation"].expanding(200).mean()
    ) / out["deviation"].expanding(200).std()

    out["Age_Days"] = (out["Date"] - BTC_GENESIS).dt.days
    out = out[out["Age_Days"] > 0].copy()
    out["cost_200d"] = out["Price"].rolling(200, min_periods=200).apply(
        lambda x: np.exp(np.mean(np.log(x))), raw=True
    )
    out["exp_growth"] = 10 ** (5.84 * np.log10(out["Age_Days"]) - 17.01)
    out["ahr999"] = (out["Price"] / out["cost_200d"]) * (out["Price"] / out["exp_growth"])

    out["Reward"] = out["Date"].apply(_block_reward)
    out["issuance_usd"] = out["Price"] * out["Reward"] * 144
    out["issuance_ma365"] = out["issuance_usd"].rolling(365, min_periods=200).mean()
    out["puell"] = out["issuance_usd"] / out["issuance_ma365"]
    out["dca_score"] = out.apply(lambda r: btc_dca_score(r["proxy_z"], r["ahr999"], r["puell"]), axis=1)
    out["p10"] = out["dca_score"].expanding(200).quantile(0.10)
    out["p25"] = out["dca_score"].expanding(200).quantile(0.25)
    out["p50"] = out["dca_score"].expanding(200).quantile(0.50)
    out["p75"] = out["dca_score"].expanding(200).quantile(0.75)
    return out


def btc_dca_score(proxy_z: float, ahr999: float, puell: float) -> float:
    def score_proxy_z(z):
        if pd.isna(z): return np.nan
        if z <= -1.0: return 5
        if z <= -0.5: return 20
        if z <= 0.0: return 40
        if z <= 1.0: return 70
        return 90

    def score_ahr999(a):
        if pd.isna(a): return np.nan
        if a < 0.45: return 10
        if a < 0.70: return 25
        if a < 1.20: return 45
        if a < 2.50: return 75
        return 90

    def score_puell(p):
        if pd.isna(p): return np.nan
        if p < 0.5: return 15
        if p < 1.0: return 35
        if p < 2.0: return 55
        if p < 4.0: return 75
        return 90

    weights = {"proxy_z": 0.40, "ahr999": 0.25, "puell": 0.35}
    scores = {"proxy_z": score_proxy_z(proxy_z), "ahr999": score_ahr999(ahr999), "puell": score_puell(puell)}
    valid = [(k, v) for k, v in scores.items() if not pd.isna(v)]
    if not valid:
        return np.nan
    total_weight = sum(weights[k] for k, _ in valid)
    return float(sum(scores[k] * weights[k] for k, _ in valid) / total_weight)
