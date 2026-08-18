from __future__ import annotations

import json
from datetime import datetime

from . import performance, portfolio, rules
from .db import connect, write_lock


DASHBOARD_KEY = "dashboard"
PERFORMANCE_SCOPES = ["all", "BTC_CYCLE", "CRCL_GROWTH", "US_INDEX_CORE"]


def get_cache(key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT payload, updated_at FROM api_cache WHERE cache_key = ?", (key,)).fetchone()
    if not row:
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
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def build_performance(scope: str) -> dict:
    return performance.performance(scope)


def refresh_all() -> dict:
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
    return f"performance:{scope.upper()}"
