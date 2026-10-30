from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta


from . import performance, portfolio, rules, strategy
from .db import connect, write_lock


DASHBOARD_KEY = "dashboard:fmp-binance-invesco:v1"
DASHBOARD_CACHE_MAX_AGE = timedelta(minutes=15)
STRATEGY_BTC_COST_KEY = "strategy_btc_cost"
STRATEGY_BTC_COST_MAX_AGE = timedelta(days=1)
STRATEGY_BTC_COST_ATTEMPT_KEY = "strategy_btc_cost_attempt"
PERFORMANCE_SCOPES = ["all", "BTC_CYCLE", "CRCL_GROWTH", "US_INDEX_CORE"]


def get_cache(key: str, max_age: timedelta | None = None) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT payload, updated_at FROM api_cache WHERE cache_key = ?", (key,)).fetchone()
    if not row:
        return None
    if max_age is not None:
        try:
            updated_at = datetime.fromisoformat(row["updated_at"])
        except (TypeError, ValueError):
            return None
        now = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.now()
        age = now - updated_at
        if age < timedelta(0) or age > max_age:
            return None
    payload = json.loads(row["payload"])
    payload["cache"] = {"key": key, "updated_at": row["updated_at"], "hit": True}
    return payload


def set_cache(key: str, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    now = datetime.now().isoformat(timespec="seconds")
    with write_lock(), connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO api_cache(cache_key, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, raw, now),
        )


def strategy_btc_cost() -> dict:
    cached = get_cache(STRATEGY_BTC_COST_KEY)
    attempt = get_cache(STRATEGY_BTC_COST_ATTEMPT_KEY)
    error = attempt.get("error") if attempt else None
    return {
        **(cached or {"avg_cost_usd": None, "as_of": None, "source_url": None}),
        "stale": error is not None,
        "error": error,
    }


def refresh_strategy_btc_cost() -> dict:
    with write_lock():
        attempt = get_cache(STRATEGY_BTC_COST_ATTEMPT_KEY)
        # A crashed browser worker releases its claim after five minutes.
        max_age = timedelta(minutes=5) if attempt and attempt.get("pending") else STRATEGY_BTC_COST_MAX_AGE
        if get_cache(STRATEGY_BTC_COST_ATTEMPT_KEY, max_age=max_age):
            return strategy_btc_cost()
        claim = secrets.token_hex(16)
        set_cache(STRATEGY_BTC_COST_ATTEMPT_KEY, {"pending": claim, "error": attempt.get("error") if attempt else None})
    error = None
    try:
        data = strategy.fetch_btc_cost()
    except (strategy.StrategySourceError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "Strategy BTC cost refresh failed: source=%s", strategy.STRATEGY_COST_URL, exc_info=True
        )
        error = {
            "code": "source_unavailable" if isinstance(exc, strategy.StrategySourceError) else "invalid_source_data",
            "message": str(exc),
        }
    with write_lock():
        current = get_cache(STRATEGY_BTC_COST_ATTEMPT_KEY)
        if current and current.get("pending") == claim:
            if error is None:
                set_cache(STRATEGY_BTC_COST_KEY, data)
            set_cache(STRATEGY_BTC_COST_ATTEMPT_KEY, {"error": error})
    return strategy_btc_cost()


def build_dashboard() -> dict:
    decision_objs = rules.all_decisions()
    decisions = [d.__dict__ for d in decision_objs]
    errors = [
        {"asset": d.asset, "bucket": d.bucket, "message": d.reason}
        for d in decision_objs
        if not d.data_ok
    ]
    price_map = {d.asset: d.price for d in decision_objs}
    summary = portfolio.portfolio_summary(price_map)
    monthly_plan = sum(d.recommended_amount for d in decision_objs)
    return {
        "title": "三仓定投计划",
        "module": "DCA Tracker",
        "decisions": decisions,
        "has_errors": bool(errors),
        "errors": errors,
        "portfolio": summary,
        "monthly_plan": monthly_plan,
        "strategy_btc": strategy_btc_cost(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_performance(scope: str) -> dict:
    return performance.performance(scope)


def refresh_all() -> dict:
    refresh_strategy_btc_cost()
    dashboard = build_dashboard()
    set_cache(DASHBOARD_KEY, dashboard)
    perf_counts = {}
    perf_errors = {}
    for scope in PERFORMANCE_SCOPES:
        try:
            data = build_performance(scope)
        except Exception as exc:
            data = _performance_error_payload(scope, exc)
            perf_errors[scope] = str(exc)
        set_cache(performance_key(scope), data)
        perf_counts[scope] = len(data.get("series", []))
    return {
        "dashboard": bool(dashboard),
        "performance_points": perf_counts,
        "performance_errors": perf_errors,
    }


def _performance_error_payload(scope: str, exc: Exception) -> dict:
    return {
        "scope": scope,
        "title": "总组合" if scope.lower() == "all" else scope,
        "summary": {"cost": 0.0, "value": None, "pnl": None, "pnl_pct": None},
        "series": [],
        "data_ok": False,
        "errors": [{"asset": scope, "message": str(exc)}],
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def performance_key(scope: str) -> str:
    return f"performance:fmp-binance:v1:{scope.upper()}"
