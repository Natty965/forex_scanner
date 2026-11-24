"""Event detection between monitor cycles: trend changes, EMA crossings, RSI zones."""

from datetime import datetime, timedelta, timezone

from forex_scanner.analysis.indicator_summary import NA, RSI_HIGH, RSI_LOW, RSI_NEUTRAL, IndicatorSummary
from forex_scanner.analysis.trend import DOWN, SIDEWAYS, UNKNOWN, UP, TrendResult
from forex_scanner.events import EMA_CROSS, RSI_ZONE, TREND, EventTracker

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def trend(direction, pair="EUR/USD", tf="H1"):
    return TrendResult(pair, tf, direction, 50, T0)


def ind(ema20=None, ema50=None, rsi=None, zone=NA, pair="EUR/USD", tf="H1"):
    return IndicatorSummary(pair, tf, 1200, T0, 1.10, {20: ema20, 50: ema50, 200: None}, rsi, 0.001,
                            "MIXED", zone)


def run(tracker, cycles):
    """cycles: list of (trends, indicators). Returns the events of each cycle."""
    return [tracker.update(T0 + timedelta(minutes=5 * i), t, s) for i, (t, s) in enumerate(cycles)]


def test_first_cycle_is_only_a_baseline():
    assert run(EventTracker(), [([trend(UP)], [ind(1.11, 1.10, 75, RSI_HIGH)])]) == [[]]


def test_trend_change():
    out = run(EventTracker(), [([trend(SIDEWAYS)], []), ([trend(DOWN)], [])])
    assert [(e.kind, e.message) for e in out[1]] == [(TREND, "Trend SIDEWAYS -> DOWN")]


def test_unknown_trend_is_not_a_change():
    out = run(EventTracker(), [([trend(UP)], []), ([trend(UNKNOWN)], []), ([trend(UP)], [])])
    assert out == [[], [], []]


def test_ema_cross_up_and_down():
    out = run(EventTracker(), [
        ([], [ind(1.0990, 1.1000)]),     # EMA 20 below EMA 50
        ([], [ind(1.1010, 1.1000)]),     # now above
        ([], [ind(1.0995, 1.1000)]),     # below again
    ])
    assert [e.message for e in out[1]] == ["EMA 20 crossed above EMA 50"]
    assert [e.message for e in out[2]] == ["EMA 20 crossed below EMA 50"]
    assert out[1][0].kind == EMA_CROSS


def test_ema_wobble_within_tolerance_is_ignored():
    # 1.10001 vs 1.10000 = 0.1 pip: not a real gap, so the last known side is kept
    out = run(EventTracker(), [
        ([], [ind(1.1010, 1.1000)]),       # above
        ([], [ind(1.10001, 1.10000)]),     # practically equal
        ([], [ind(1.1010, 1.1000)]),       # above again: no crossing happened
    ])
    assert out == [[], [], []]


def test_ema_cross_through_equal_still_counts_once():
    out = run(EventTracker(), [
        ([], [ind(1.1010, 1.1000)]),       # above
        ([], [ind(1.10000, 1.10000)]),     # equal
        ([], [ind(1.0990, 1.1000)]),       # below
    ])
    assert [len(e) for e in out] == [0, 0, 1]
    assert out[2][0].message == "EMA 20 crossed below EMA 50"


def test_rsi_zone_enter_leave_and_jump():
    out = run(EventTracker(), [
        ([], [ind(rsi=60, zone=RSI_NEUTRAL)]),
        ([], [ind(rsi=72.3, zone=RSI_HIGH)]),
        ([], [ind(rsi=65.0, zone=RSI_NEUTRAL)]),
        ([], [ind(rsi=25.0, zone=RSI_LOW)]),
        ([], [ind(rsi=75.0, zone=RSI_HIGH)]),
    ])
    assert [e.message for e in out[1]] == ["RSI entered the HIGH zone (70+) (72.3)"]
    assert [e.message for e in out[2]] == ["RSI left the HIGH zone (70+) (65.0)"]
    assert [e.message for e in out[3]] == ["RSI entered the LOW zone (30 or less) (25.0)"]
    assert [e.message for e in out[4]] == ["RSI jumped from the LOW zone (30 or less) to the HIGH zone (70+) (75.0)"]
    assert out[1][0].kind == RSI_ZONE


def test_failed_fetch_keeps_last_state_and_creates_no_event():
    out = run(EventTracker(), [
        ([trend(UP)], [ind(1.1010, 1.1000, 75, RSI_HIGH)]),
        ([], []),                                              # fetch failed: nothing known
        ([trend(UP)], [ind(1.1010, 1.1000, 75, RSI_HIGH)]),    # same as before failure
    ])
    assert out == [[], [], []]


def test_change_during_a_failure_is_reported_when_data_returns():
    out = run(EventTracker(), [
        ([trend(UP)], []),
        ([], []),                  # failed
        ([trend(DOWN)], []),       # changed while we could not see it
    ])
    assert [e.message for e in out[2]] == ["Trend UP -> DOWN"]


def test_items_are_tracked_separately():
    out = run(EventTracker(), [
        ([trend(UP, "EUR/USD"), trend(UP, "GBP/USD")], []),
        ([trend(UP, "EUR/USD"), trend(DOWN, "GBP/USD")], []),
    ])
    assert [(e.pair, e.message) for e in out[1]] == [("GBP/USD", "Trend UP -> DOWN")]
