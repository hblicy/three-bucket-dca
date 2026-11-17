from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import requests

from . import cache
from .db import write_lock

SOURCE_URL = "https://api.alternative.me/fng/"
STATE_KEY = "btc_fear_greed"
CLAIM_TTL = timedelta(minutes=2)
logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(value: int) -> str | None:
    if value < 20:
        return "极度恐惧"
    if value > 80:
        return "极度贪婪"
    return None


def _fetch_reading(now: datetime) -> dict:
    response = requests.get(SOURCE_URL, timeout=8)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict) or payload["metadata"].get("error") is not None:
        raise ValueError("Alternative.me 指数接口报告错误或缺少元数据")
    readings = payload.get("data")
    if not isinstance(readings, list) or len(readings) != 1 or not isinstance(readings[0], dict):
        raise ValueError("Alternative.me 指数响应格式无效")
    row = readings[0]
    value = row.get("value")
    timestamp = row.get("timestamp")
    if not str(value).isascii() or not str(value).isdecimal() or int(value) > 100:
        raise ValueError("Alternative.me 指数必须是 0 到 100 的整数")
    if not str(timestamp).isascii() or not str(timestamp).isdecimal():
        raise ValueError("Alternative.me 指数日期无效")
    try:
        observed = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Alternative.me 指数日期无效") from exc
    if observed > now:
        raise ValueError("Alternative.me 返回了未来的指数")
    return {"value": int(value), "as_of": observed.date().isoformat()}


def snapshot(*, now: datetime | None = None) -> dict:
    state = cache.get_cache(STATE_KEY)
    if state is None:
        return {"value": None, "as_of": None, "stale": False}
    return {
        "value": state["value"],
        "as_of": state["as_of"],
        "stale": state["as_of"] != (now or _now()).date().isoformat(),
    }


def poll(send: Callable[[str], bool], *, now: datetime | None = None) -> str:
    """Read the index and send at most one confirmed alert per UTC index date.

    Persist the attempt before calling WeWork so overlapping jobs share the claim.
    """
    now = now or _now()
    today = now.date().isoformat()
    reading = cache.get_cache(STATE_KEY)
    if reading is None or reading["as_of"] != today:
        try:
            reading = _fetch_reading(now)
        except (requests.RequestException, ValueError):
            logger.warning("BTC Fear & Greed fetch failed: source=%s", SOURCE_URL, exc_info=True)
            return "unavailable"
        if reading["as_of"] != today:
            return "awaiting_today"

    with write_lock():
        state = cache.get_cache(STATE_KEY)
        if state is not None and state["as_of"] > today:
            return "outdated"
        if state is None or state["as_of"] < today:
            state = {
                "value": reading["value"],
                "as_of": today,
                "sent": False,
                "pending_since": None,
            }
            cache.set_cache(STATE_KEY, state)
        zone = _zone(state["value"])
        if zone is None or state["sent"]:
            return "unchanged"
        pending = state["pending_since"]
        if pending and now - datetime.fromisoformat(pending) < CLAIM_TTL:
            return "in_progress"
        claim = now.isoformat()
        state["pending_since"] = claim
        state.pop("cache", None)
        cache.set_cache(STATE_KEY, state)

    threshold = "<20" if state["value"] < 20 else ">80"
    message = (
        f"BTC 恐惧贪婪指数提醒\n"
        f"{state['value']}/100 · {zone}（{threshold}）\n"
        f"指数日期：{today} UTC\n"
        "来源：Alternative.me"
    )
    sent = False
    try:
        sent = send(message)
    finally:
        with write_lock():
            current = cache.get_cache(STATE_KEY)
            if current and current["pending_since"] == claim:
                current.pop("cache", None)
                current.update(sent=sent, pending_since=None)
                cache.set_cache(STATE_KEY, current)
    return "sent" if sent else "send_failed"
