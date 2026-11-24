"""Current-price snapshots: freshness labels and 1h/24h changes."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from forex_scanner.analysis.snapshot import (
    DELAYED,
    LIVE,
    MARKET_CLOSED,
    STALE,
    build_snapshot,
    freshness_status,
    price_change,
    status_at,
)
from forex_scanner.providers import CandleSet


def m1_set(times, closes, fetched_at, pair="EUR/USD"):
    idx = pd.DatetimeIndex(times, tz="UTC")
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                       "volume": float("nan"), "complete": True}, index=idx)
    return CandleSet(pair, "M1", "yfinance", fetched_at, df, volume_available=False)


@pytest.mark.parametrize("age_min,closed,expected", [
    (0, False, LIVE), (5, False, LIVE), (6, False, DELAYED), (30, False, DELAYED),
    (31, False, STALE), (3000, False, STALE), (3000, True, MARKET_CLOSED), (1, True, MARKET_CLOSED),
])
def test_freshness_status(age_min, closed, expected):
    got = freshness_status(timedelta(minutes=age_min), closed, timedelta(minutes=5), timedelta(minutes=30))
    assert got == expected


def test_one_hour_change_in_pips_and_percent():
    times = pd.date_range("2026-11-25 10:00", "2026-11-25 11:00", freq="1min")
    closes = [1.1000] * (len(times) - 1) + [1.1025]
    c = m1_set(times, closes, datetime(2026, 11, 25, 11, 0, 30, tzinfo=timezone.utc)).candles
    ch = price_change(c, "EUR/USD", timedelta(hours=1))
    assert ch.reference_price == 1.1000
    assert ch.pips == pytest.approx(25.0)
    assert ch.percent == pytest.approx(0.2272727, rel=1e-5)
    assert not ch.spans_gap


def test_jpy_pips_use_two_decimals():
    times = pd.date_range("2026-11-25 10:00", "2026-11-25 11:00", freq="1min")
    closes = [158.00] * (len(times) - 1) + [158.25]
    c = m1_set(times, closes, datetime(2026, 11, 25, 11, 1, tzinfo=timezone.utc), pair="USD/JPY").candles
    assert price_change(c, "USD/JPY", timedelta(hours=1)).pips == pytest.approx(25.0)


def test_not_enough_history_gives_none_not_a_guess():
    times = pd.date_range("2026-11-25 10:30", "2026-11-25 11:00", freq="1min")
    c = m1_set(times, [1.1] * len(times), datetime(2026, 11, 25, 11, 1, tzinfo=timezone.utc)).candles
    assert price_change(c, "EUR/USD", timedelta(hours=1)) is None


def test_change_across_weekend_is_flagged():
    # Friday until the close, then Sunday after the open: "1h ago" falls in the weekend.
    fri = pd.date_range("2026-11-20 20:00", "2026-11-20 21:59", freq="1min")
    sun = pd.date_range("2026-11-22 22:00", "2026-11-22 22:30", freq="1min")
    times = fri.append(sun)
    c = m1_set(times, [1.1] * len(times), datetime(2026, 11, 22, 22, 31, tzinfo=timezone.utc)).candles
    ch = price_change(c, "EUR/USD", timedelta(hours=1))
    assert ch.reference_time == datetime(2026, 11, 20, 21, 59, tzinfo=timezone.utc)
    assert ch.spans_gap


def test_snapshot_live_during_week():
    times = pd.date_range("2026-11-25 09:00", "2026-11-25 11:00", freq="1min")
    cs = m1_set(times, [1.1] * len(times), datetime(2026, 11, 25, 11, 2, tzinfo=timezone.utc))
    s = build_snapshot(cs)
    assert s.status == LIVE and s.age == timedelta(minutes=2) and s.price == 1.1


def test_snapshot_stale_when_source_stops_updating():
    times = pd.date_range("2026-11-25 09:00", "2026-11-25 10:00", freq="1min")
    cs = m1_set(times, [1.1] * len(times), datetime(2026, 11, 25, 11, 0, tzinfo=timezone.utc))
    assert build_snapshot(cs).status == STALE


def test_snapshot_market_closed_on_saturday():
    times = pd.date_range("2026-11-20 20:00", "2026-11-20 21:59", freq="1min")
    cs = m1_set(times, [1.1] * len(times), datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))
    assert build_snapshot(cs).status == MARKET_CLOSED


def test_status_is_rejudged_when_shown_later():
    times = pd.date_range("2026-11-25 09:00", "2026-11-25 11:00", freq="1min")
    s = build_snapshot(m1_set(times, [1.1] * len(times), datetime(2026, 11, 25, 11, 1, tzinfo=timezone.utc)))
    assert s.status == LIVE                                            # at fetch time
    later = datetime(2026, 11, 25, 11, 20, tzinfo=timezone.utc)        # page drawn 19 min later
    status, age = status_at(s, later)
    assert status == DELAYED and age == timedelta(minutes=20)
    assert status_at(s, datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc))[0] == STALE
    assert status_at(s, datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc))[0] == MARKET_CLOSED


def test_snapshot_refuses_non_minute_candles():
    times = pd.date_range("2026-11-25 09:00", periods=3, freq="1h")
    cs = m1_set(times, [1.1] * 3, datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc))
    cs = CandleSet(cs.pair, "H1", cs.source, cs.fetched_at, cs.candles, False)
    with pytest.raises(ValueError):
        build_snapshot(cs)
