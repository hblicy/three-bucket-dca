from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


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
    # NYSE keeps December 31 open if New Year's Day falls on a Saturday.
    if date(year, 1, 1).weekday() != 5:
        holidays.add(_observed_fixed_holiday(year, 1, 1))
    return holidays


def _is_us_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in _us_market_holidays(day.year)


def last_us_trading_day(year: int, month: int) -> date:
    day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while not _is_us_trading_day(day):
        day -= timedelta(days=1)
    return day


def next_us_trading_day(day: date) -> date:
    day += timedelta(days=1)
    while not _is_us_trading_day(day):
        day += timedelta(days=1)
    return day


def previous_month(day: date) -> tuple[int, int]:
    if day.month == 1:
        return day.year - 1, 12
    return day.year, day.month - 1


def last_completed_us_month_end(now: datetime) -> date:
    eastern = now.astimezone(ZoneInfo("America/New_York"))
    today = eastern.date()
    month_end = last_us_trading_day(today.year, today.month)
    # The Friday after Thanksgiving can be the last trading day of November.
    thanksgiving_friday = _nth_weekday(month_end.year, 11, 3, 4) + timedelta(days=1)
    close_hour = 13 if month_end == thanksgiving_friday else 16
    if today > month_end or (today == month_end and eastern.hour >= close_hour):
        return month_end
    return last_us_trading_day(*previous_month(today))
