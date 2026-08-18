from __future__ import annotations

from collections import defaultdict

from . import prices
from .db import connect, rows_to_dicts


BUCKET_NAMES = {
    "BTC_CYCLE": "BTC：周期进攻仓",
    "CRCL_GROWTH": "CRCL：加密金融成长仓",
    "US_INDEX_CORE": "QQQM + VOO：美股长期底仓",
}

VALID_BUCKETS = set(BUCKET_NAMES)
VALID_ASSETS = {"BTC", "CRCL", "QQQM", "VOO"}


def list_transactions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM transactions ORDER BY date DESC, id DESC").fetchall()
    return rows_to_dicts(rows)


def current_prices() -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for asset in sorted(VALID_ASSETS):
        symbol = "BTC-USD" if asset == "BTC" else asset
        try:
            out[asset] = float(prices.latest_price(symbol)["price"])
        except Exception:
            out[asset] = None
    return out


def portfolio_summary(price_map: dict[str, float | None] | None = None) -> dict:
    price_map = price_map or current_prices()
    txs = [tx for tx in list_transactions() if tx["asset"].upper() in VALID_ASSETS]
    by_asset = defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "buys": 0, "sells": 0})
    by_bucket = defaultdict(lambda: {"cost": 0.0, "value": 0.0, "missing_prices": []})

    for tx in txs:
        asset = tx["asset"].upper()
        shares = float(tx["shares"])
        amount = float(tx["amount_usd"])
        if tx["side"] == "sell":
            amount = -abs(amount)
        item = by_asset[asset]
        item["shares"] += shares
        item["cost"] += amount
        item["buys" if tx["side"] == "buy" else "sells"] += 1

    assets = []
    for asset, item in sorted(by_asset.items()):
        price = price_map.get(asset)
        value = item["shares"] * price if price is not None else None
        pnl = value - item["cost"] if value is not None else None
        pnl_pct = pnl / item["cost"] * 100 if pnl is not None and item["cost"] else None
        avg_cost = item["cost"] / item["shares"] if item["shares"] else None
        bucket = _bucket_for_asset(asset)
        by_bucket[bucket]["cost"] += item["cost"]
        if value is None:
            by_bucket[bucket]["missing_prices"].append(asset)
        else:
            by_bucket[bucket]["value"] += value
        assets.append({
            "asset": asset,
            "bucket": bucket,
            "bucket_name": BUCKET_NAMES.get(bucket, bucket),
            "shares": item["shares"],
            "cost": item["cost"],
            "avg_cost": avg_cost,
            "price": price,
            "value": value,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "price_data_ok": price is not None,
            "buys": item["buys"],
            "sells": item["sells"],
        })

    buckets = []
    for bucket, item in sorted(by_bucket.items()):
        pnl = item["value"] - item["cost"] if not item["missing_prices"] else None
        buckets.append({
            "bucket": bucket,
            "bucket_name": BUCKET_NAMES.get(bucket, bucket),
            "cost": item["cost"],
            "value": None if item["missing_prices"] else item["value"],
            "pnl": pnl,
            "pnl_pct": pnl / item["cost"] * 100 if pnl is not None and item["cost"] else None,
            "price_data_ok": not item["missing_prices"],
            "missing_prices": item["missing_prices"],
        })

    total_cost = sum(x["cost"] for x in assets)
    missing_price_assets = [x["asset"] for x in assets if not x["price_data_ok"]]
    total_value = None if missing_price_assets else sum(x["value"] or 0.0 for x in assets)
    total_pnl = total_value - total_cost if total_value is not None else None
    return {
        "assets": assets,
        "buckets": buckets,
        "total_cost": total_cost,
        "total_value": total_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl / total_cost * 100 if total_pnl is not None and total_cost else None,
        "price_data_ok": not missing_price_assets,
        "missing_price_assets": missing_price_assets,
        "transaction_count": len(txs),
    }


def _bucket_for_asset(asset: str) -> str:
    if asset == "BTC":
        return "BTC_CYCLE"
    if asset == "CRCL":
        return "CRCL_GROWTH"
    if asset in {"QQQM", "VOO"}:
        return "US_INDEX_CORE"
    return "OTHER"
