from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import BEIJING_TZ, WEWORK_BOT_WEBHOOK
from .db import connect, write_lock
from .rules import Decision, btc_decision, crcl_decision, us_index_decisions


def _today() -> datetime:
    return datetime.now(ZoneInfo(BEIJING_TZ))


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    day = date(year, month, 1)
    offset = (weekday - day.weekday()) % 7
    return day + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    raw = date(year, month, day)
    if raw.weekday() == 5:
        return raw - timedelta(days=1)
    if raw.weekday() == 6:
        return raw + timedelta(days=1)
    return raw


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _us_market_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_date(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    holidays.add(_observed_fixed_holiday(year + 1, 1, 1))
    return holidays


def _is_us_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _us_market_holidays(day.year)


def _last_us_trading_day(year: int, month: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while not _is_us_trading_day(day):
        day -= timedelta(days=1)
    return day


def _next_us_trading_day(day: date) -> date:
    day += timedelta(days=1)
    while not _is_us_trading_day(day):
        day += timedelta(days=1)
    return day


def _previous_month(day: date) -> tuple[int, int]:
    if day.month == 1:
        return day.year - 1, 12
    return day.year, day.month - 1


def is_us_index_signal_due(now: datetime | None = None) -> bool:
    now = now or _today()
    year, month = _previous_month(now.date())
    signal_day = _next_us_trading_day(_last_us_trading_day(year, month))
    return now.date() == signal_day and now.hour == 16


def is_due(decision: Decision, now: datetime | None = None) -> bool:
    now = now or _today()
    if not decision.data_ok:
        return False
    if decision.bucket == "BTC_CYCLE":
        return decision.status in {"buy", "pause"} and now.weekday() == 0 and now.hour == 16
    if decision.bucket == "CRCL_GROWTH":
        return decision.status in {"buy", "pause"} and now.weekday() == 1 and now.hour == 16
    if decision.bucket == "US_INDEX_CORE":
        return decision.recommended_amount > 0 and is_us_index_signal_due(now)
    return False


def claim_reminder(decision: Decision, reminder_date: str) -> bool:
    with write_lock(), connect() as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO reminder_history(bucket, asset, reminder_date, channel, sent_at)
            VALUES (?, ?, ?, 'wework', ?)
            """,
            (decision.bucket, decision.asset, reminder_date, datetime.utcnow().isoformat(timespec="seconds") + "Z"),
        )
        return cur.rowcount == 1


def release_reminder_claim(decision: Decision, reminder_date: str) -> None:
    with write_lock(), connect() as conn:
        conn.execute(
            """
            DELETE FROM reminder_history
            WHERE bucket=? AND asset=? AND reminder_date=? AND channel='wework'
            """,
            (decision.bucket, decision.asset, reminder_date),
        )


def send_wework_text(content: str) -> bool:
    if not WEWORK_BOT_WEBHOOK:
        print("WEWORK_BOT_WEBHOOK not configured; skip reminder.")
        return False
    payload = json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WEWORK_BOT_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print(f"WeWork send failed: {exc}")
        return False
    if data.get("errcode") != 0:
        print(f"WeWork send failed: {data}")
        return False
    return True


def message(decision: Decision) -> str:
    price = "-" if decision.price is None else f"${decision.price:,.2f}"
    lines = [
        f"三仓定投提醒 - {decision.asset}",
        f">仓位：{decision.bucket_name}",
        f">动作：{decision.action}",
        f">建议金额：${decision.recommended_amount:,.2f}",
        f">当前价格：{price}",
        f">原因：{decision.reason}",
        f">窗口：{decision.next_window}",
    ]
    return "\n".join(lines)


def send_due_reminders() -> dict:
    now = _today()
    reminder_date = now.date().isoformat()
    sent = []
    skipped = []
    failed = []
    due_decisions = []
    if now.weekday() == 0:
        due_decisions.append(btc_decision())
    if now.weekday() == 1:
        due_decisions.append(crcl_decision())
    if is_us_index_signal_due(now):
        due_decisions.extend(us_index_decisions())

    for decision in due_decisions:
        if not is_due(decision, now):
            skipped.append(decision.asset)
            continue
        if not claim_reminder(decision, reminder_date):
            skipped.append(decision.asset)
            continue
        ok = send_wework_text(message(decision))
        if ok:
            sent.append(decision.asset)
        else:
            release_reminder_claim(decision, reminder_date)
            failed.append(decision.asset)
    return {"date": reminder_date, "sent": sent, "skipped": skipped, "failed": failed}
