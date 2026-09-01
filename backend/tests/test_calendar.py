from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.util.timeutil import (ET, half_days, is_trading_day, market_holidays,
                               next_scan_start, session_phase)


def test_holidays_2026():
    h = market_holidays(2026)
    assert date(2026, 1, 1) in h
    assert date(2026, 7, 3) in h          # Jul 4 observed Friday
    assert date(2026, 9, 7) in h          # Labor Day
    assert date(2026, 11, 26) in h        # Thanksgiving
    assert date(2026, 12, 25) in h
    assert date(2026, 4, 3) in h          # Good Friday 2026


def test_trading_days():
    assert is_trading_day(date(2026, 9, 1)) is True    # Tue
    assert is_trading_day(date(2026, 9, 5)) is False   # Sat
    assert is_trading_day(date(2026, 9, 7)) is False   # Labor Day


def test_phases():
    d = datetime(2026, 9, 1, 5, 30, tzinfo=ET)
    assert session_phase(d) == "premarket"
    assert session_phase(datetime(2026, 9, 1, 10, 0, tzinfo=ET)) == "regular"
    assert session_phase(datetime(2026, 9, 1, 17, 0, tzinfo=ET)) == "afterhours"
    assert session_phase(datetime(2026, 9, 1, 2, 0, tzinfo=ET)) == "closed"
    assert session_phase(datetime(2026, 9, 7, 10, 0, tzinfo=ET)) == "closed"  # holiday


def test_half_day_close():
    assert date(2026, 11, 27) in half_days(2026)
    assert session_phase(datetime(2026, 11, 27, 14, 0, tzinfo=ET)) == "afterhours"


def test_next_scan_skips_weekend():
    # Friday 21:00 -> Monday 4am (or Tuesday if Monday is a holiday)
    nxt = next_scan_start(datetime(2026, 9, 4, 21, 0, tzinfo=ET))
    assert nxt == datetime(2026, 9, 8, 4, 0, tzinfo=ET)  # Labor Day 9/7 skipped
