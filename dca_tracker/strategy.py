from __future__ import annotations

import math
from datetime import datetime, timezone, date
from urllib.parse import urlparse

import requests


STRATEGY_COST_URL = "https://lianyanshe.com/api/holdings/treasury?id=mstr"


def fetch_btc_cost() -> dict:
    response = requests.get(STRATEGY_COST_URL, timeout=10)
    response.raise_for_status()
    try:
        payload = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise ValueError("微策略成本接口未返回有效 JSON") from exc
    if not isinstance(payload, dict) or payload.get("asset") != "BTC" or payload.get("ticker") != "MSTR":
        raise ValueError("微策略成本接口未返回 MSTR 的 BTC 数据")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("微策略成本接口缺少持仓汇总")
    avg_cost = summary.get("avgCostUsd")
    if isinstance(avg_cost, bool) or not isinstance(avg_cost, (int, float)) or not math.isfinite(avg_cost) or avg_cost <= 0:
        raise ValueError("微策略 BTC 累计持仓均价必须为有限正数")
    as_of = summary.get("avgCostAsOf")
    if not isinstance(as_of, str):
        raise ValueError("微策略 BTC 成本披露日期缺失")
    try:
        source_date = date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("微策略 BTC 成本披露日期无效") from exc
    if source_date > datetime.now(timezone.utc).date():
        raise ValueError("微策略 BTC 成本披露日期在未来")
    source_name = summary.get("avgCostSource")
    sources = payload.get("sources")
    if not isinstance(source_name, str) or not source_name or not isinstance(sources, list):
        raise ValueError("微策略 BTC 成本缺少披露来源")
    source = next(
        (item for item in sources if isinstance(item, dict) and item.get("name") == source_name),
        None,
    )
    source_url = source.get("url") if source else None
    if not isinstance(source_url, str):
        raise ValueError("微策略 BTC 成本缺少原始披露链接")
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.netloc not in {"strategy.com", "www.strategy.com", "sec.gov", "www.sec.gov"}:
        raise ValueError("微策略 BTC 成本来源不是 Strategy 或 SEC 官方链接")
    return {
        "avg_cost_usd": float(avg_cost),
        "as_of": source_date.isoformat(),
        "source_url": source_url,
    }
