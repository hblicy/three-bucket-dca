from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from numbers import Real
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from .config import ROOT, FMP_API_KEY


_HISTORY_LOCK = threading.Lock()
_HISTORY_FETCH_LOCKS: dict[str, threading.Lock] = {}
_HISTORY_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_HISTORY_TTL_SECONDS = 15 * 60
_BINANCE_EQUITY_LOCK = threading.Lock()
_BINANCE_EQUITY_SYMBOLS = frozenset({"CRCL", "QQQM", "VOO"})
_BINANCE_EQUITY_CACHE = {"quotes": {}, "errors": {}, "checked_at": None}
# Retain the existing directory so migrated PE and halving state remain readable.
MARKET_CACHE_DIR = ROOT / "data" / "yfinance_cache"
MARKET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
NASDAQ_PE_CACHE_FILE = MARKET_CACHE_DIR / "nasdaq_pe.json"
NASDAQ_PE_CACHE_MAX_AGE = timedelta(days=35)
INVESCO_QQQ_PE_URL = (
    "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46090E103"
    "?expand=nav&idType=cusip&variationType=fundCharacteristics&productType=ETF"
)
BTC_PRICE_MAX_AGE = timedelta(days=2)
US_EQUITY_PRICE_MAX_AGE = timedelta(days=4)
BTC_HALVING_CACHE_FILE = MARKET_CACHE_DIR / "btc_halving.json"
BTC_HALVING_CACHE_MAX_AGE = timedelta(hours=6)
BTC_HALVING_NEAR_TARGET_BLOCKS = 144
BTC_HALVING_NEAR_CACHE_MAX_AGE = timedelta(minutes=1)
BTC_HALVING_FINALITY_CONFIRMATIONS = 6


def fmp_request(path: str, params: dict) -> list:
    if not FMP_API_KEY:
        raise RuntimeError("FMP_API_KEY 未配置，无法读取美股历史行情")
    try:
        response = requests.get(f"https://financialmodelingprep.com/stable/{path}",
                                params={**params, "apikey": FMP_API_KEY}, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        safe_cause = RuntimeError(f"{type(exc).__name__}: {str(exc).replace(FMP_API_KEY, '[REDACTED]')}")
        raise RuntimeError(f"FMP {path} {params.get('symbol')}: {type(exc).__name__}") from safe_cause
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"FMP {path} {params.get('symbol')}: 数据为空或接口返回错误")
    return payload


def _fetch_fmp_history(symbol: str) -> pd.DataFrame:
    today = _utc_now().date()
    earliest = (pd.Timestamp(today) - pd.DateOffset(years=5)).date()
    rows = fmp_request("historical-price-eod/full", {"symbol": symbol, "from": str(earliest), "to": str(today)})
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or row.get("symbol") != symbol:
            raise RuntimeError(f"FMP {symbol}: 历史行情代码不匹配")
        try:
            day = datetime.strptime(row["date"], "%Y-%m-%d").date()
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"FMP {symbol}: 历史行情日期无效") from exc
        if day > today or isinstance(row.get("close"), bool):
            raise RuntimeError(f"FMP {symbol}: 历史行情日期或收盘价无效")
        normalized.append({"Date": day, **{target: row.get(source) for source, target in
                           (("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume"))}})
    frame = pd.DataFrame(normalized).set_index("Date").sort_index()
    frame.index = pd.DatetimeIndex(frame.index, name="Date")
    if frame.index.has_duplicates:
        raise RuntimeError(f"FMP {symbol}: 历史行情日期重复")
    for column in frame:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if any(not np.isfinite(frame[column].to_numpy()).all() for column in ("Open", "High", "Low", "Volume")):
        raise RuntimeError(f"FMP {symbol}: 历史 OHLCV 数据无效")
    return frame


def _fetch_binance_history() -> pd.DataFrame:
    end = int(_utc_now().timestamp() * 1000)
    cursor = int(datetime(2011, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    records = []
    while cursor <= end:
        try:
            response = requests.get("https://data-api.binance.vision/api/v3/klines", params={
                "symbol": "BTCUSDT", "interval": "1d", "startTime": cursor, "endTime": end, "limit": 1000,
            }, timeout=20)
            response.raise_for_status()
            rows = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Binance BTCUSDT 历史日线下载失败：{type(exc).__name__}") from exc
        if not isinstance(rows, list):
            raise RuntimeError("Binance BTCUSDT 历史日线响应格式无效")
        if not rows:
            break
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                raise RuntimeError("Binance BTCUSDT K线字段不足")
            stamp = _positive_real(row[0])
            if stamp is None or stamp < cursor or stamp > end or stamp % 86_400_000:
                raise RuntimeError("Binance BTCUSDT K线日期无效或分页未前进")
            values = [None if isinstance(value, bool) else _positive_float(value) for value in row[1:5]]
            try:
                volume = float(row[5])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Binance BTCUSDT K线成交量无效") from exc
            if any(value is None for value in values) or not np.isfinite(volume) or volume < 0:
                raise RuntimeError("Binance BTCUSDT K线价格或成交量无效")
            if records and stamp != records[-1][0] + 86_400_000:
                raise RuntimeError("Binance BTCUSDT K线日期缺失、重复或倒序")
            records.append((int(stamp), *values, volume))
        cursor = records[-1][0] + 86_400_000
        if len(rows) < 1000:
            break
    if not records:
        raise RuntimeError("Binance BTCUSDT 历史日线为空")
    frame = pd.DataFrame(records, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    frame["Date"] = pd.to_datetime(frame["Date"], unit="ms", utc=True).dt.tz_localize(None)
    return frame.set_index("Date")


def _cached_history(symbol: str) -> pd.DataFrame:
    with _HISTORY_LOCK:
        lock = _HISTORY_FETCH_LOCKS.setdefault(symbol, threading.Lock())
    with lock:
        cached = _HISTORY_CACHE.get(symbol)
        if cached is not None and 0 <= time.monotonic() - cached[0] < _HISTORY_TTL_SECONDS:
            return cached[1].copy()
        data = _fetch_binance_history() if symbol == "BTCUSDT" else _fetch_fmp_history(symbol)
        if data.empty or "Close" not in data:
            raise RuntimeError(f"No price history for {symbol}")
        if symbol in _BINANCE_EQUITY_SYMBOLS and pd.isna(data["Close"].iloc[-1]):
            data = _fill_missing_latest_equity_close(data, symbol)
        if _positive_float(data["Close"].iloc[-1]) is None:
            raise RuntimeError(f"Invalid latest price for {symbol}")
        _validated_close(data, symbol)
        _validate_latest_price_date(data.index[-1], symbol, BTC_PRICE_MAX_AGE if symbol == "BTCUSDT" else US_EQUITY_PRICE_MAX_AGE)
        _HISTORY_CACHE[symbol] = (time.monotonic(), data.copy())
        return data


def history(symbol: str, period: str = "1y", start: str | None = None) -> pd.DataFrame:
    source_symbol = "BTCUSDT" if symbol.upper() in {"BTC", "BTC-USD", "BTCUSDT"} else symbol.upper()
    data = _cached_history(source_symbol)
    if data.empty or "Close" not in data:
        raise RuntimeError(f"No price history for {symbol}")
    if _positive_float(data["Close"].iloc[-1]) is None:
        raise RuntimeError(f"Invalid latest price for {symbol}")
    max_age = BTC_PRICE_MAX_AGE if source_symbol == "BTCUSDT" else US_EQUITY_PRICE_MAX_AGE
    _validate_latest_price_date(data.index[-1], symbol, max_age)
    _validated_close(data, symbol)
    if start:
        lower = pd.Timestamp(start)
        if source_symbol != "BTCUSDT" and lower < pd.Timestamp(_utc_now().date()) - pd.DateOffset(years=5):
            raise RuntimeError(f"FMP {symbol}: 请求超出当前套餐5年历史范围")
    else:
        if period not in {"1y", "5y"}:
            raise ValueError(f"Unsupported history period: {period}")
        lower = pd.Timestamp(_utc_now().date()) - pd.DateOffset(years=int(period[:-1]))
    return data.loc[data.index >= lower].dropna(subset=["Close"]).copy()


def _fill_missing_latest_equity_close(data: pd.DataFrame, symbol: str) -> pd.DataFrame:
    rows = fmp_request("quote", {"symbol": symbol})
    metadata = rows[0] if len(rows) == 1 else None
    error = f"Invalid latest price for {symbol}"
    if not isinstance(metadata, dict) or metadata.get("symbol") != symbol.upper():
        raise RuntimeError(f"{error}: missing or mismatched quote symbol")
    price = _positive_real(metadata.get("price"))
    timestamp = _positive_real(metadata.get("timestamp"))
    if price is None or timestamp is None:
        raise RuntimeError(f"{error}: invalid quote price or timestamp")
    try:
        exchange_tz = ZoneInfo("America/New_York")
        quote_time = pd.to_datetime(timestamp, unit="s", utc=True)
        latest = pd.Timestamp(data.index[-1])
        latest_date = latest.tz_convert(exchange_tz).date() if latest.tzinfo else latest.date()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"{error}: invalid quote date or exchange timezone") from exc
    if quote_time > _utc_now() or quote_time.tz_convert(exchange_tz).date() != latest_date:
        raise RuntimeError(f"{error}: quote time does not match the latest trading date")
    _validate_latest_price_date(data.index[-1], symbol, US_EQUITY_PRICE_MAX_AGE)
    repaired = data.copy()
    repaired.iloc[-1, repaired.columns.get_loc("Close")] = price
    logging.getLogger(__name__).warning(
        "Filled missing Close for %s on %s from FMP quote price=%s at %s",
        symbol, latest_date.isoformat(), price, quote_time.isoformat(),
    )
    return repaired


def _validated_close(data: pd.DataFrame, symbol: str) -> pd.Series:
    close = data["Close"].dropna()
    try:
        close = close.astype(float)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid price history for {symbol}") from exc
    if close.empty or not np.isfinite(close.to_numpy()).all() or (close <= 0).any():
        raise RuntimeError(f"Invalid price history for {symbol}")
    return close


def btc_spot_quote() -> dict:
    url = "https://data-api.binance.vision/api/v3/ticker/price"
    try:
        response = requests.get(url, params={"symbol": "BTCUSDT"}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("symbol") != "BTCUSDT":
            raise ValueError("响应交易对不是 BTCUSDT")
        raw_price = payload.get("price")
        price = None if isinstance(raw_price, bool) else _positive_float(raw_price)
        if price is None:
            raise ValueError("报价必须是有限正数")
    except (requests.RequestException, ValueError, OverflowError) as exc:
        logging.getLogger(__name__).warning(
            "Binance BTCUSDT spot quote failed: source=%s", url, exc_info=True
        )
        raise RuntimeError(f"币安现货 BTCUSDT 报价获取失败：{exc}") from exc
    return {
        "price": price,
        "symbol": "BTCUSDT",
        "source": "Binance Spot",
        "quote_asset": "USDT",
        "updated_at": _utc_now().isoformat(),
    }


def _parse_equity_quote(row: dict) -> dict:
    symbol = row["s"]
    raw_price = row.get("c")
    price = None if isinstance(raw_price, bool) else _positive_float(raw_price)
    expected_type = "STOCK" if symbol == "CRCL" else "ETF"
    if price is None or row.get("t") != expected_type:
        raise ValueError(f"{symbol} 股票类型或报价无效")
    session = row.get("mp")
    if not isinstance(session, str) or session not in {"C", "ON", "PRE", "O", "POST"}:
        raise ValueError(f"{symbol} 交易时段无效")
    return {
        "price": price,
        "symbol": symbol,
        "source": "Binance Stocks",
        "quote_asset": "USD",
        "updated_at": _utc_now().isoformat(),
        "session": session,
    }


def _collect_equity_quotes() -> tuple[dict, dict]:
    response = requests.get(
        "https://www.binance.com/bapi/equity/v1/public/equity/symbol/get-symbols-by-param",
        params={"symbols": ",".join(sorted(_BINANCE_EQUITY_SYMBOLS))}, timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("code") != "000000" or payload.get("success") is not True or not isinstance(payload.get("data"), list):
        raise ValueError("股票报价快照格式无效或接口返回失败")
    quotes, errors = {}, {}
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("s"), str) or row["s"] not in _BINANCE_EQUITY_SYMBOLS:
            continue
        symbol = row["s"]
        try:
            quote = _parse_equity_quote(row)
        except (ValueError, OverflowError) as exc:
            errors[symbol] = exc
            quotes.pop(symbol, None)
        else:
            quotes[symbol] = quote
            errors.pop(symbol, None)
    return quotes, errors


def equity_quote(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in _BINANCE_EQUITY_SYMBOLS:
        raise ValueError(f"未接入币安美股代码：{symbol}")
    try:
        with _BINANCE_EQUITY_LOCK:
            checked_at = _BINANCE_EQUITY_CACHE["checked_at"]
            if checked_at is None or not 0 <= time.monotonic() - checked_at < 30:
                quotes, errors = _collect_equity_quotes()
                _BINANCE_EQUITY_CACHE.update(quotes=quotes, errors=errors, checked_at=time.monotonic())
            if symbol in _BINANCE_EQUITY_CACHE["errors"]:
                raise _BINANCE_EQUITY_CACHE["errors"][symbol]
            if symbol not in _BINANCE_EQUITY_CACHE["quotes"]:
                raise ValueError(f"股票快照没有返回 {symbol} 报价")
            return dict(_BINANCE_EQUITY_CACHE["quotes"][symbol])
    except (requests.RequestException, ValueError, OverflowError) as exc:
        logging.getLogger(__name__).warning("Binance equity quote failed: symbol=%s", symbol, exc_info=True)
        raise RuntimeError(f"币安美股 {symbol} 行情获取失败：{exc}") from exc


def latest_price(symbol: str, *, quote: dict | None = None) -> dict:
    if symbol.upper() in {"BTC", "BTC-USD", "BTCUSDT"}:
        symbol = "BTCUSDT"
    data = history(symbol, period="1y")
    close = _validated_close(data, symbol)
    current = float(quote["price"]) if quote is not None else float(close.iloc[-1])
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
        data.get("field") != "priceToEarningsRatio"
        or data.get("symbol") != "QQQ"
        or data.get("source") != "invesco"
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
    age = _utc_now() - cached_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > NASDAQ_PE_CACHE_MAX_AGE:
        return None
    try:
        _validate_invesco_pe_date(data.get("as_of"))
    except ValueError:
        return None
    return {
        "symbol": data.get("symbol") or "unknown",
        "pe": pe,
        "field": data["field"],
        "source": "last_valid_official_pe",
        "as_of": data.get("as_of"),
        "source_url": data.get("source_url"),
        "cached_at": data["updated_at"],
    }


def _write_last_valid_trailing_pe(item: dict) -> None:
    payload = {
        "symbol": item["symbol"],
        "pe": item["pe"],
        "field": item["field"],
        "updated_at": item["updated_at"],
        "source": item.get("source"),
        "as_of": item.get("as_of"),
        "source_url": item.get("source_url"),
    }
    NASDAQ_PE_CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _validate_invesco_pe_date(value) -> str:
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError("Invesco QQQ PE 披露日期无效") from exc
    age = _utc_now().date() - day
    if age < timedelta(0) or age > NASDAQ_PE_CACHE_MAX_AGE:
        raise ValueError(f"Invesco QQQ PE 披露日期超出35天有效期：{day}")
    return day.isoformat()


def _fetch_invesco_pe() -> dict:
    response = requests.get(INVESCO_QQQ_PE_URL, timeout=20)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or data.get("cusip") != "46090E103" or data.get("currencyCode") != "USD":
        raise ValueError("Invesco QQQ PE 基金身份不匹配")
    pe = _positive_real(data.get("priceToEarningsRatio"))
    if pe is None:
        raise ValueError("Invesco QQQ 官方 PE 缺失或无效，不使用前瞻 PE 替代")
    as_of = _validate_invesco_pe_date(data.get("priceToEarningsRatioEffectiveDate") or data.get("effectiveDate"))
    return {
        "symbol": "QQQ", "field": "priceToEarningsRatio", "pe": pe,
        "source": "invesco", "as_of": as_of, "source_url": INVESCO_QQQ_PE_URL,
        "forward_pe": _positive_real(data.get("forwardPriceToEarningsRatio")), "quotes": {},
        "updated_at": _utc_now().isoformat(timespec="seconds"),
    }


def nasdaq_pe() -> dict:
    try:
        item = _fetch_invesco_pe()
    except (requests.RequestException, ValueError) as exc:
        logging.getLogger(__name__).warning("Invesco QQQ PE unavailable: source=%s", INVESCO_QQQ_PE_URL, exc_info=True)
        cached = _read_last_valid_trailing_pe()
        if cached:
            cached.update({"forward_pe": None, "quotes": {}, "updated_at": _utc_now().isoformat(timespec="seconds")})
            return cached
        raise RuntimeError("Invesco QQQ 官方 PE 不可用，且没有有效缓存") from exc
    _write_last_valid_trailing_pe(item)
    return item


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
    df = _cached_history("BTCUSDT").reset_index()
    if df.empty:
        raise RuntimeError("BTC price data is empty")
    if "Close" not in df or _positive_float(df["Close"].iloc[-1]) is None:
        raise RuntimeError("Invalid latest BTC price")
    if "Date" not in df:
        df = df.rename(columns={df.columns[0]: "Date"})
    _validate_latest_price_date(df["Date"].iloc[-1], "BTC", BTC_PRICE_MAX_AGE)
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df["Price"] = pd.to_numeric(df["Close"], errors="coerce")
    df.attrs.update({"source": "Binance Spot", "symbol": "BTCUSDT", "quote_asset": "USDT",
                     "history_start": df["Date"].iloc[0].date().isoformat()})
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
