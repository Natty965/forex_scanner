"""Market hours, trading days and pip sizes. Times are UTC; New York is UTC-5 in winter, UTC-4 in summer."""

from datetime import date, datetime, timezone

import pytest

from forex_scanner.display.formatting import candle_label
from forex_scanner.market import is_market_closed, next_open, pip_size, trading_date


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


@pytest.mark.parametrize("when,closed", [
    (utc(2026, 11, 20, 21, 59), False),  # Fri, 1 min before close (winter: 22:00 UTC)
    (utc(2026, 11, 20, 22, 0), True),    # Fri close
    (utc(2026, 11, 21, 12, 0), True),    # Saturday
    (utc(2026, 11, 22, 21, 59), True),   # Sun, 1 min before open
    (utc(2026, 11, 22, 22, 0), False),   # Sun open
    (utc(2026, 11, 25, 3, 0), False),    # Wednesday
    (utc(2026, 7, 3, 21, 0), True),      # summer Friday: closes 21:00 UTC
    (utc(2026, 7, 3, 20, 59), False),
    (utc(2026, 7, 5, 21, 0), False),     # summer Sunday: opens 21:00 UTC
])
def test_market_hours_follow_new_york_time(when, closed):
    assert is_market_closed(when) is closed


def test_next_open_from_saturday_is_sunday_evening():
    assert next_open(utc(2026, 11, 21, 12, 0)) == utc(2026, 11, 22, 22, 0)


def test_next_open_after_this_weeks_open_is_next_sunday():
    assert next_open(utc(2026, 11, 23, 1, 0)) == utc(2026, 11, 29, 22, 0)


@pytest.mark.parametrize("candle_open,day", [
    (utc(2026, 11, 19, 22, 0), date(2026, 11, 20)),   # winter: Friday's candle opens Thu 22:00 UTC
    (utc(2026, 7, 2, 21, 0), date(2026, 7, 3)),       # summer: Friday's candle opens Thu 21:00 UTC
    (utc(2026, 11, 22, 22, 0), date(2026, 11, 23)),   # Monday's candle opens Sunday evening
])
def test_trading_date_of_daily_candles(candle_open, day):
    assert trading_date(candle_open) == day


def test_candle_labels():
    assert candle_label(utc(2026, 11, 19, 22, 0), "D1") == "11-20 day"
    assert candle_label(utc(2026, 11, 19, 22, 0), "D1", long=True) == "daily candle for Fri 2026-11-20"
    assert candle_label(utc(2026, 11, 20, 21, 0), "H1") == "11-20 21:00"
    assert candle_label(None, "H1") == "-"


def test_pip_sizes():
    assert pip_size("EUR/USD") == 0.0001
    assert pip_size("USD/JPY") == 0.01
