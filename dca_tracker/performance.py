from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import pandas as pd

from . import prices
from .db import connect, rows_to_dicts
from .portfolio import BUCKET_NAMES, VALID_ASSETS


PerformanceScope = Literal["all", "BTC_CYCLE", "CRCL_GROWTH", "US_INDEX_CORE"]


def performance(scope: str = "all") -> dict:
    scope = scope.upper()
    if scope != "ALL" and scope not in BUCKET_NAMES:
        raise ValueError(f"unknown performance scope: {scope}")

    txs = _transactions(scope)
    if not txs:
        return _empty_result(scope)

    start = min(pd.to_datetime(tx["date"]).date() for tx in txs)
    end = date.today()
    assets = sorted({tx["asset"].upper() for tx in txs if tx["asset"].upper() in VALID_ASSETS})
    price_maps, errors = _price_maps(assets, start)

    series = []
    for day in _date_range(start, end):
        cost = 0.0
        value = 0.0
        shares_by_asset = {asset: 0.0 for asset in assets}
        for tx in txs:
            if pd.to_datetime(tx["date"]).date() > day:
                continue
            asset = tx["asset"].upper()
            amount = float(tx["amount_usd"])
            shares = float(tx["shares"])
            if tx["side"] == "sell":
                amount = -abs(amount)
            cost += amount
            shares_by_asset[asset] = shares_by_asset.get(asset, 0.0) + shares
        missing = []
        for asset, shares in shares_by_asset.items():
            price = price_maps.get(asset, {}).get(day.isoformat())
            if price is None and abs(shares) > 0:
                missing.append(asset)
                continue
            value += shares * (price or 0.0)
        pnl = value - cost if not missing else None
        pnl_pct = pnl / cost * 100 if pnl is not None and cost else None
        series.append({
            "date": day.isoformat(),
            "cost": round(cost, 2),
            "value": round(value, 2) if not missing else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "missing_prices": missing,
        })

    latest = series[-1] if series else {"cost": 0.0, "value": 0.0, "pnl": 0.0, "pnl_pct": 0.0}
    return {
        "scope": "all" if scope == "ALL" else scope,
        "title": "总组合" if scope == "ALL" else BUCKET_NAMES[scope],
        "summary": {
            "cost": latest["cost"],
            "value": latest["value"],
            "pnl": latest["pnl"],
            "pnl_pct": latest["pnl_pct"],
        },
        "series": series,
        "data_ok": not errors and not any(point["missing_prices"] for point in series[-5:]),
        "errors": errors,
    }


def _transactions(scope: str) -> list[dict]:
    where = "UPPER(asset) IN ({})".format(",".join("?" for _ in VALID_ASSETS))
    params: list[str] = sorted(VALID_ASSETS)
    if scope != "ALL":
        where += " AND bucket = ?"
        params.append(scope)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT date, bucket, asset, side, amount_usd, price, shares
            FROM transactions
            WHERE {where}
            ORDER BY date ASC, id ASC
            """,
            tuple(params),
        ).fetchall()
    return rows_to_dicts(rows)


def _price_maps(assets: list[str], start: date) -> tuple[dict[str, dict[str, float]], list[dict]]:
    out: dict[str, dict[str, float]] = {}
    errors: list[dict] = []
    fetch_start = (start - timedelta(days=7)).isoformat()
    for asset in assets:
        symbol = "BTC-USD" if asset == "BTC" else asset
        try:
            hist = prices.history(symbol, start=fetch_start)
            close = hist["Close"].dropna().copy()
            close.index = pd.to_datetime(close.index).tz_localize(None).date
            daily = pd.Series(close.values, index=pd.to_datetime(list(close.index)))
            all_days = pd.date_range(start=start, end=date.today(), freq="D")
            filled = daily.reindex(all_days).ffill()
            out[asset] = {
                idx.date().isoformat(): float(value)
                for idx, value in filled.dropna().items()
            }
        except Exception as exc:
            errors.append({"asset": asset, "message": str(exc)})
            out[asset] = {}
    return out, errors


def _date_range(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _empty_result(scope: str) -> dict:
    return {
        "scope": "all" if scope == "ALL" else scope,
        "title": "总组合" if scope == "ALL" else BUCKET_NAMES.get(scope, scope),
        "summary": {"cost": 0.0, "value": 0.0, "pnl": 0.0, "pnl_pct": 0.0},
        "series": [],
        "data_ok": True,
        "errors": [],
    }
