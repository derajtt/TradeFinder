"""Eastern-time market sessions, US trading calendar, and window logic.

Never hard-codes UTC offsets; all conversions go through zoneinfo so DST is correct.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_et() -> datetime:
    return datetime.now(ET)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Sat -> Fri
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sun -> Mon
        return d + timedelta(days=1)
    return d


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def market_holidays(year: int) -> set:
    """NYSE/NASDAQ full-closure holidays."""
    hs = {
        _observed(date(year, 1, 1)),                       # New Year's Day
        _nth_weekday(year, 1, 0, 3),                       # MLK Day
        _nth_weekday(year, 2, 0, 3),                       # Presidents' Day
        _easter(year) - timedelta(days=2),                 # Good Friday
        _last_weekday(year, 5, 0),                         # Memorial Day
        _observed(date(year, 6, 19)),                      # Juneteenth
        _observed(date(year, 7, 4)),                       # Independence Day
        _nth_weekday(year, 9, 0, 1),                       # Labor Day
        _nth_weekday(year, 11, 3, 4),                      # Thanksgiving
        _observed(date(year, 12, 25)),                     # Christmas
    }
    return hs


def half_days(year: int) -> set:
    """1:00 p.m. ET early closes: day after Thanksgiving; Jul 3 / Christmas Eve when weekday."""
    hd = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    for m, d in ((7, 3), (12, 24)):
        dd = date(year, m, d)
        if dd.weekday() < 5 and dd not in market_holidays(year):
            hd.add(dd)
    return hd


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in market_holidays(d.year)


def next_trading_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not is_trading_day(n):
        n += timedelta(days=1)
    return n


def session_phase(dt_et: Optional[datetime] = None) -> str:
    """One of: closed, prep, premarket, regular, afterhours."""
    dt = dt_et or now_et()
    d = dt.date()
    if not is_trading_day(d):
        return "closed"
    t = dt.time()
    close_t = time(13, 0) if d in half_days(d.year) else time(16, 0)
    if time(3, 45) <= t < time(4, 0):
        return "prep"
    if time(4, 0) <= t < time(9, 30):
        return "premarket"
    if time(9, 30) <= t < close_t:
        return "regular"
    if close_t <= t < time(20, 0):
        return "afterhours"
    return "closed"


def next_scan_start(dt_et: Optional[datetime] = None) -> datetime:
    """Next 4:00 a.m. ET on a trading day at/after now."""
    dt = dt_et or now_et()
    d = dt.date()
    if is_trading_day(d) and dt.time() < time(20, 0):
        if dt.time() < time(4, 0):
            return datetime.combine(d, time(4, 0), tzinfo=ET)
        if session_phase(dt) != "closed":
            return dt  # in an active window now
    nd = next_trading_day(d)
    return datetime.combine(nd, time(4, 0), tzinfo=ET)


def minutes_since_4am(dt_et: Optional[datetime] = None) -> int:
    dt = dt_et or now_et()
    start = dt.replace(hour=4, minute=0, second=0, microsecond=0)
    return max(0, int((dt - start).total_seconds() // 60))
