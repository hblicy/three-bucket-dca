from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from numbers import Real

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from .config import ROOT


YFINANCE_CACHE_DIR = ROOT / "data" / "yfinance_cache"
YFINANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
NASDAQ_PE_CACHE_FILE = YFINANCE_CACHE_DIR / "nasdaq_pe.json"
NASDAQ_PE_CACHE_MAX_AGE = timedelta(days=35)
BTC_PRICE_MAX_AGE = timedelta(days=2)
US_EQUITY_PRICE_MAX_AGE = timedelta(days=4)
BTC_HALVING_CACHE_FILE = YFINANCE_CACHE_DIR / "btc_halving.json"
BTC_HALVING_CACHE_MAX_AGE = timedelta(hours=6)
BTC_HALVING_NEAR_TARGET_BLOCKS = 144
BTC_HALVING_NEAR_CACHE_MAX_AGE = timedelta(minutes=1)
BTC_HALVING_FINALITY_CONFIRMATIONS = 6
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
    if _positive_float(data["Close"].iloc[-1]) is None:
        raise RuntimeError(f"Invalid latest price for {symbol}")
    max_age = BTC_PRICE_MAX_AGE if symbol.upper() in {"BTC", "BTC-USD"} else US_EQUITY_PRICE_MAX_AGE
    _validate_latest_price_date(data.index[-1], symbol, max_age)
    return data.dropna(subset=["Close"])


def _validated_close(data: pd.DataFrame, symbol: str) -> pd.Series:
    close = data["Close"].dropna()
    try:
        close = close.astype(float)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid price history for {symbol}") from exc
    if close.empty or not np.isfinite(close.to_numpy()).all() or (close <= 0).any():
        raise RuntimeError(f"Invalid price history for {symbol}")
    return close


def latest_price(symbol: str) -> dict:
    if symbol.upper() == "BTC":
        symbol = "BTC-USD"
    data = history(symbol, period="1y")
    close = _validated_close(data, symbol)
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
    return number if np.isfinite(number) and number > 0 else None


def _positive_real(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    return _positive_float(value)


def _validate_latest_price_date(value, symbol: str, max_age: timedelta) -> None:
    try:
        latest = pd.to_datetime(value, utc=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Invalid latest price date for {symbol}") from exc
    if pd.isna(latest):
        raise RuntimeError(f"Invalid latest price date for {symbol}")
    age = _utc_now().date() - latest.date()
    if age < timedelta(0) or age > max_age:
        raise RuntimeError(f"Stale latest price date for {symbol}: {latest.date().isoformat()}")


def _read_last_valid_trailing_pe() -> dict | None:
    if not NASDAQ_PE_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(NASDAQ_PE_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or (
        data.get("field") != "trailingPE"
        or data.get("symbol") not in {"QQQ", "QQQM", "^NDX"}
    ):
        return None
    pe = _positive_real(data.get("pe"))
    if pe is None:
        return None
    try:
        cached_at = datetime.fromisoformat(str(data.get("updated_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > NASDAQ_PE_CACHE_MAX_AGE:
        return None
    return {
        "symbol": data.get("symbol") or "unknown",
        "pe": pe,
        "field": "trailingPE",
        "source": "last_valid_trailingPE",
        "cached_at": data["updated_at"],
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
            trailing_pe = _positive_real(info.get("trailingPE"))
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
    close = _validated_close(data, symbol)
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
    if "Close" not in df or _positive_float(df["Close"].iloc[-1]) is None:
        raise RuntimeError("Invalid latest BTC price")
    if "Date" not in df:
        df = df.rename(columns={df.columns[0]: "Date"})
    _validate_latest_price_date(df["Date"].iloc[-1], "BTC", BTC_PRICE_MAX_AGE)
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df["Price"] = pd.to_numeric(df["Close"], errors="coerce")
    return df.dropna(subset=["Price"])


BTC_GENESIS = pd.Timestamp("2009-01-03")
BTC_2028_HALVING_HEIGHT = 1_050_000
BTC_2028_HALVING_REWARD = 1.5625
BTC_2028_HALVING_EARLIEST_TIME = pd.Timestamp("2024-04-20")
BTC_BLOCK_TIME_FUTURE_TOLERANCE = pd.Timedelta(hours=2)
BLOCKSTREAM_API_BASE = "https://blockstream.info/api"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validated_btc_halving_time(value, observed_at, *, unit: str | None = None) -> pd.Timestamp:
    try:
        halving_time = pd.to_datetime(value, unit=unit, utc=True).tz_localize(None)
        observed_time = pd.to_datetime(observed_at, utc=True).tz_localize(None)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("BTC 减半区块时间戳无法解析") from exc
    if pd.isna(halving_time) or pd.isna(observed_time):
        raise ValueError("BTC 减半区块时间戳无效")
    if halving_time <= BTC_2028_HALVING_EARLIEST_TIME:
        raise ValueError("BTC 减半区块时间早于上一轮减半")
    if halving_time > observed_time + BTC_BLOCK_TIME_FUTURE_TOLERANCE:
        raise ValueError("BTC 减半区块时间晚于允许的未来范围")
    return halving_time


def _read_btc_halving_cache() -> dict | None:
    if not BTC_HALVING_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(BTC_HALVING_CACHE_FILE.read_text(encoding="utf-8"))
        if int(data.get("target_height")) != BTC_2028_HALVING_HEIGHT:
            return None
        tip_height = int(data.get("tip_height"))
        checked_at = datetime.fromisoformat(str(data.get("checked_at") or "").replace("Z", "+00:00"))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        checked_at = checked_at.astimezone(timezone.utc)
        age = _utc_now() - checked_at
        if tip_height < 0 or age < timedelta(0):
            return None
        raw_halving_time = data.get("halving_time")
        halving_time = None
        block_hash = None
        if raw_halving_time:
            halving_time = _validated_btc_halving_time(raw_halving_time, checked_at)
            block_hash = str(data.get("block_hash") or "").strip()
            if tip_height < BTC_2028_HALVING_HEIGHT or not block_hash:
                return None
        elif tip_height >= BTC_2028_HALVING_HEIGHT:
            return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return {
        "tip_height": tip_height,
        "halving_time": halving_time,
        "block_hash": block_hash,
        "checked_at": checked_at,
        "age": age,
    }


def _write_btc_halving_cache(
    tip_height: int,
    halving_time: pd.Timestamp | None,
    block_hash: str | None = None,
) -> None:
    payload = {
        "target_height": BTC_2028_HALVING_HEIGHT,
        "tip_height": tip_height,
        "halving_time": (
            halving_time.tz_localize("UTC").isoformat().replace("+00:00", "Z")
            if halving_time is not None
            else None
        ),
        "block_hash": block_hash if halving_time is not None else None,
        "checked_at": _utc_now().isoformat().replace("+00:00", "Z"),
    }
    BTC_HALVING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = BTC_HALVING_CACHE_FILE.with_name(f"{BTC_HALVING_CACHE_FILE.name}.{os.getpid()}.tmp")
    try:
        temp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(BTC_HALVING_CACHE_FILE)
    finally:
        temp_file.unlink(missing_ok=True)


def _confirmed_2028_halving_time() -> pd.Timestamp | None:
    cached = _read_btc_halving_cache()
    if cached is not None:
        if cached["halving_time"] is not None:
            confirmations = cached["tip_height"] - BTC_2028_HALVING_HEIGHT + 1
            if confirmations >= BTC_HALVING_FINALITY_CONFIRMATIONS:
                return cached["halving_time"]  # type: ignore[return-value]
            max_age = BTC_HALVING_NEAR_CACHE_MAX_AGE
        else:
            blocks_remaining = BTC_2028_HALVING_HEIGHT - cached["tip_height"]
            max_age = (
                BTC_HALVING_NEAR_CACHE_MAX_AGE
                if blocks_remaining <= BTC_HALVING_NEAR_TARGET_BLOCKS
                else BTC_HALVING_CACHE_MAX_AGE
            )
        if cached["age"] <= max_age:
            return cached["halving_time"]  # type: ignore[return-value]

    latest_tip_height = None
    try:
        latest_tip_height = _fetch_btc_tip_height()
        return _fetch_confirmed_2028_halving_time(latest_tip_height)
    except (requests.RequestException, RuntimeError) as exc:
        if cached is None or cached["halving_time"] is not None:
            raise
        fallback_tip_height = cached["tip_height"] if latest_tip_height is None else latest_tip_height
        blocks_remaining = BTC_2028_HALVING_HEIGHT - fallback_tip_height
        if blocks_remaining <= BTC_HALVING_NEAR_TARGET_BLOCKS:
            raise
        print(f"Blockstream lookup failed; use cached BTC halving state: {exc}")
        return None


def _fetch_btc_tip_height() -> int:
    tip_response = requests.get(f"{BLOCKSTREAM_API_BASE}/blocks/tip/height", timeout=10)
    tip_response.raise_for_status()
    try:
        tip_height = int(tip_response.text.strip())
    except ValueError as exc:
        raise RuntimeError("Blockstream 返回的 BTC 最新区块高度无效") from exc
    if tip_height < 0:
        raise RuntimeError("Blockstream 返回的 BTC 最新区块高度无效")
    return tip_height


def _fetch_btc_block_hash(height: int) -> str:
    hash_response = requests.get(f"{BLOCKSTREAM_API_BASE}/block-height/{height}", timeout=10)
    hash_response.raise_for_status()
    block_hash = hash_response.text.strip()
    if not block_hash:
        raise RuntimeError(f"Blockstream 未返回 BTC 区块 {height} 的哈希")
    return block_hash


def _fetch_confirmed_2028_halving_time(tip_height: int | None = None) -> pd.Timestamp | None:
    if tip_height is None:
        tip_height = _fetch_btc_tip_height()
    if tip_height < BTC_2028_HALVING_HEIGHT:
        _write_btc_halving_cache(tip_height, None)
        return None

    block_hash = _fetch_btc_block_hash(BTC_2028_HALVING_HEIGHT)

    block_response = requests.get(f"{BLOCKSTREAM_API_BASE}/block/{block_hash}", timeout=10)
    block_response.raise_for_status()
    block = block_response.json()
    try:
        height = int(block["height"])
        timestamp = int(block["timestamp"])
        halving_time = _validated_btc_halving_time(timestamp, _utc_now(), unit="s")
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Blockstream 返回的 BTC 2028 减半区块数据无效") from exc
    if height != BTC_2028_HALVING_HEIGHT:
        raise RuntimeError(f"Blockstream 返回了错误的 BTC 区块高度：{height}")

    verified_tip_height = _fetch_btc_tip_height()
    if verified_tip_height < BTC_2028_HALVING_HEIGHT:
        raise RuntimeError("BTC 2028 减半区块在复核时不再确认")
    verified_block_hash = _fetch_btc_block_hash(BTC_2028_HALVING_HEIGHT)
    if verified_block_hash != block_hash:
        raise RuntimeError("BTC 2028 减半区块在复核时已被替换")

    _write_btc_halving_cache(verified_tip_height, halving_time, block_hash)
    return halving_time


def _block_reward(
    date: pd.Timestamp,
    confirmed_2028_halving_time: pd.Timestamp | None = None,
) -> float:
    halvings = [
        (pd.Timestamp("2009-01-03"), 50.0),
        (pd.Timestamp("2012-11-28"), 25.0),
        (pd.Timestamp("2016-07-09"), 12.5),
        (pd.Timestamp("2020-05-11"), 6.25),
        (pd.Timestamp("2024-04-20"), 3.125),
    ]
    if confirmed_2028_halving_time is not None:
        halvings.append((confirmed_2028_halving_time, BTC_2028_HALVING_REWARD))
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

    confirmed_2028_halving_time = _confirmed_2028_halving_time()
    reward_times = out["Date"].copy()
    if confirmed_2028_halving_time is not None:
        halving_day = confirmed_2028_halving_time.normalize()
        reward_times.loc[reward_times.dt.normalize() == halving_day] = confirmed_2028_halving_time
    out["Reward"] = reward_times.apply(
        lambda date: _block_reward(
            date,
            confirmed_2028_halving_time=confirmed_2028_halving_time,
        )
    )
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
    try:
        proxy_z, ahr999, puell = (float(value) for value in (proxy_z, ahr999, puell))
    except (TypeError, ValueError):
        return np.nan
    if not all(np.isfinite(value) for value in (proxy_z, ahr999, puell)):
        return np.nan

    def score_proxy_z(z):
        if z <= -1.0: return 5
        if z <= -0.5: return 20
        if z <= 0.0: return 40
        if z <= 1.0: return 70
        return 90

    def score_ahr999(a):
        if a < 0.45: return 10
        if a < 0.70: return 25
        if a < 1.20: return 45
        if a < 2.50: return 75
        return 90

    def score_puell(p):
        if p < 0.5: return 15
        if p < 1.0: return 35
        if p < 2.0: return 55
        if p < 4.0: return 75
        return 90

    weights = {"proxy_z": 0.40, "ahr999": 0.25, "puell": 0.35}
    scores = {"proxy_z": score_proxy_z(proxy_z), "ahr999": score_ahr999(ahr999), "puell": score_puell(puell)}
    return float(sum(scores[key] * weight for key, weight in weights.items()))
