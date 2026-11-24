"""Market structure (v0.5, milestone B2): swing points and support/resistance, on hand-made candles."""

import numpy as np
import pandas as pd
import pytest

from forex_scanner.analysis import structure as st
from forex_scanner.analysis.structure import (
    AT_PRICE,
    FALLING,
    HIGH,
    LOW,
    MIXED,
    NA,
    RISING,
    SwingPoint,
    analyse_structure,
    classify,
    cluster_levels,
    find_swings,
    label_swings,
)
from forex_scanner.storage.records import jsonable, structure_from


def candles(highs, lows=None, complete=None):
    highs = np.asarray(highs, float)
    lows = highs - 1.0 if lows is None else np.asarray(lows, float)
    idx = pd.date_range("2026-11-02", periods=len(highs), freq="1h", tz="UTC")
    mid = (highs + lows) / 2
    return pd.DataFrame({"open": mid, "high": highs, "low": lows, "close": mid, "volume": np.nan,
                         "complete": True if complete is None else complete}, index=idx)


def wave(n=400, low=1.1000, high=1.1100, period=20, end_at=None):
    """Triangle wave between `low` and `high` (candle mid-points), each candle 10 pips tall."""
    phase = np.arange(n) % period
    up = phase < period // 2
    frac = np.where(up, phase / (period // 2), (period - phase) / (period // 2))
    mid = low + (high - low) * frac
    if end_at is not None:
        mid[-1] = end_at
    return candles(mid + 0.0005, mid - 0.0005)


# ---------- swings ----------


def test_a_swing_needs_three_lower_highs_on_each_side():
    (s,) = [x for x in find_swings(candles([1, 2, 3, 5, 3, 2, 1])) if x.kind == HIGH]
    assert s.price == 5 and s.time == pd.Timestamp("2026-11-02 03:00", tz="UTC")


def test_a_swing_is_not_confirmed_until_three_more_candles_close():
    assert [x for x in find_swings(candles([1, 2, 3, 5, 3, 2])) if x.kind == HIGH] == []


def test_equal_highs_count_once():
    highs = [x for x in find_swings(candles([1, 2, 3, 5, 5, 2, 1, 0])) if x.kind == HIGH]
    assert len(highs) == 1 and highs[0].time.hour == 3


def test_swing_lows_mirror_highs():
    lows = [x for x in find_swings(candles([9, 9, 9, 9, 9, 9, 9], [5, 4, 3, 1, 3, 4, 5])) if x.kind == LOW]
    assert [x.price for x in lows] == [1]


def test_the_forming_candle_is_never_used():
    df = wave()
    df.iloc[-1, df.columns.get_loc("high")] = 2.0           # absurd, but still forming
    df.iloc[-1, df.columns.get_loc("complete")] = False
    r = analyse_structure(df, "EUR/USD", "H1")
    assert r.last_candle_time == df.index[-2] and all(lv.high < 1.2 for lv in r.resistance)


# ---------- structure ----------


def swing_list(kinds_prices):
    t = pd.Timestamp("2026-11-02", tz="UTC")
    return [SwingPoint((t + pd.Timedelta(hours=i)).to_pydatetime(), k, p) for i, (k, p) in enumerate(kinds_prices)]


@pytest.mark.parametrize("seq,expected", [
    ([(HIGH, 10), (LOW, 5), (HIGH, 12), (LOW, 7)], RISING),
    ([(HIGH, 12), (LOW, 7), (HIGH, 10), (LOW, 5)], FALLING),
    ([(HIGH, 10), (LOW, 5), (HIGH, 12), (LOW, 4)], MIXED),           # higher high, lower low
    ([(HIGH, 10), (LOW, 5), (HIGH, 10.05), (LOW, 7)], MIXED),        # equal high (within tolerance)
    ([(HIGH, 10), (LOW, 5)], NA),
])
def test_structure_from_the_last_two_highs_and_lows(seq, expected):
    labelled = label_swings(swing_list(seq), tolerance=0.1)
    assert classify(labelled) == expected


def test_swing_labels():
    labelled = label_swings(swing_list([(HIGH, 10), (LOW, 5), (HIGH, 12), (LOW, 4)]), tolerance=0.1)
    assert [s.label for s in labelled] == [None, None, "HH", "LL"]


# ---------- levels ----------


def test_nearby_swings_form_one_level():
    groups = cluster_levels(swing_list([(HIGH, 1.1000), (LOW, 1.1003), (HIGH, 1.1050)]), width=0.0005)
    assert [[s.price for s in g] for g in groups] == [[1.1000, 1.1003]]


def test_a_range_gives_resistance_above_and_support_below():
    r = analyse_structure(wave(end_at=1.1050), "EUR/USD", "H1")
    (res,) = r.resistance[:1]
    (sup,) = r.support[:1]
    assert res.low == pytest.approx(1.1105) and res.touches >= 10
    assert sup.high == pytest.approx(1.0995) and sup.touches >= 10
    assert res.distance_pips == pytest.approx(55, abs=0.5)        # 1.1105 - 1.1050
    assert r.structure == MIXED                                    # equal highs and lows: a range


def test_price_sitting_on_a_level_is_at_price():
    r = analyse_structure(wave(end_at=1.1102), "EUR/USD", "H1")
    assert [lv.side for lv in r.at_price] == [AT_PRICE] and r.at_price[0].distance_pips == 0


def test_no_support_below_the_lowest_area_is_said_plainly():
    r = analyse_structure(wave(end_at=1.0900), "EUR/USD", "H1")
    assert r.support == [] and any("No support below" in x for x in r.reasons)


def test_not_enough_history():
    r = analyse_structure(wave(n=60), "EUR/USD", "H1")
    assert r.structure == NA and r.resistance == [] and "needs ATR" in r.reasons[0]


def test_only_the_lookback_window_is_searched():
    df = wave(n=700)
    old = df.index[: 700 - st.STRUCTURE_LOOKBACK]
    df.loc[old, "high"] += 0.0300                                   # very old highs far above
    r = analyse_structure(df, "EUR/USD", "H1")
    assert all(lv.high < 1.12 for lv in r.resistance)


def test_wording_is_descriptive_and_round_trips_through_the_database():
    r = analyse_structure(wave(end_at=1.1050), "EUR/USD", "H1")
    text = " ".join(r.reasons).lower()
    assert "not a signal" in text
    for word in ("buy", "sell", "entry", "target"):
        assert word not in text
    assert structure_from(jsonable(r)) == r
