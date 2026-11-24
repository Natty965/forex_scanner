"""Indicator tests.

1. Compare against slow, plain-Python versions written straight from the textbook
   definitions (independent of pandas' ewm).
2. Small examples worked out by hand.
3. Edge cases: flat prices, too little data, bad input.
"""

import math

import numpy as np
import pandas as pd
import pytest

from forex_scanner.analysis.indicators import atr, drop_warmup, ema, rsi, true_range, warmup_candles

# ---------- slow reference versions (definitions, one candle at a time) ----------


def ref_ema(closes, period):
    out = [math.nan] * len(closes)
    if len(closes) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(closes[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(closes)):
        prev = k * closes[i] + (1 - k) * prev
        out[i] = prev
    return out


def ref_rsi(closes, period):
    out = [math.nan] * len(closes)
    if len(closes) < period + 1:
        return out
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(c, 0) for c in changes[:period]) / period
    avg_loss = sum(max(-c, 0) for c in changes[:period]) / period

    def value(g, lo):
        if g == 0 and lo == 0:
            return math.nan
        if lo == 0:
            return 100.0
        return 100 - 100 / (1 + g / lo)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        c = changes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(c, 0)) / period    # Wilder's own formula
        avg_loss = (avg_loss * (period - 1) + max(-c, 0)) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def ref_atr(highs, lows, closes, period):
    n = len(closes)
    out = [math.nan] * n
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, n)]
    if len(trs) < period:
        return out
    prev = sum(trs[:period]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i - 1]) / period
        out[i] = prev
    return out


# ---------- helpers ----------


def random_candles(n=600, seed=7, start=1.10):
    """A random walk shaped like real candles (high >= open/close >= low)."""
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(0, 0.0008, n))
    open_ = np.concatenate([[start], close[:-1]]) + rng.normal(0, 0.0001, n)
    high = np.maximum(open_, close) + rng.uniform(0, 0.0006, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.0006, n)
    idx = pd.date_range("2026-11-02", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def close_series(values):
    return pd.Series(values, index=pd.date_range("2026-11-02", periods=len(values), freq="1h", tz="UTC"),
                     dtype=float)


# ---------- 1. matches the definitions ----------


@pytest.mark.parametrize("period", [3, 20, 50, 200])
def test_ema_matches_definition(period):
    c = random_candles()["close"]
    np.testing.assert_allclose(ema(c, period).to_numpy(), ref_ema(c.tolist(), period), rtol=1e-12)


@pytest.mark.parametrize("period", [2, 14, 30])
def test_rsi_matches_wilder_definition(period):
    c = random_candles()["close"]
    np.testing.assert_allclose(rsi(c, period).to_numpy(), ref_rsi(c.tolist(), period), rtol=1e-10)


@pytest.mark.parametrize("period", [2, 14])
def test_atr_matches_wilder_definition(period):
    df = random_candles()
    expected = ref_atr(df["high"].tolist(), df["low"].tolist(), df["close"].tolist(), period)
    np.testing.assert_allclose(atr(df, period).to_numpy(), expected, rtol=1e-12)


# ---------- 2. worked by hand ----------


def test_ema_by_hand():
    # period 3: seed = (1+2+3)/3 = 2; weight = 2/4 = 0.5
    # then 0.5*4 + 0.5*2 = 3, then 0.5*5 + 0.5*3 = 4
    got = ema(close_series([1, 2, 3, 4, 5]), 3).tolist()
    assert math.isnan(got[0]) and math.isnan(got[1])
    assert got[2:] == [2.0, 3.0, 4.0]


def test_rsi_by_hand():
    # closes 1, 2, 1, 3 -> changes +1, -1, +2. Period 2:
    # seed: avg gain (1+0)/2 = 0.5, avg loss (0+1)/2 = 0.5 -> RSI 50
    # next: gain (0.5*1 + 2)/2 = 1.25, loss (0.5*1 + 0)/2 = 0.25 -> RS 5 -> RSI 100 - 100/6
    got = rsi(close_series([1, 2, 1, 3]), 2).tolist()
    assert math.isnan(got[0]) and math.isnan(got[1])
    assert got[2] == pytest.approx(50.0)
    assert got[3] == pytest.approx(100 - 100 / 6)


def test_true_range_includes_gaps():
    df = pd.DataFrame({"high": [1.10, 1.13, 1.12], "low": [1.09, 1.12, 1.10], "close": [1.095, 1.125, 1.11]},
                      index=pd.date_range("2026-11-02", periods=3, freq="1h", tz="UTC"))
    tr = true_range(df).tolist()
    assert math.isnan(tr[0])                    # no previous close: not guessed
    assert tr[1] == pytest.approx(0.035)        # gap up: high 1.13 - previous close 1.095
    assert tr[2] == pytest.approx(0.025)        # previous close 1.125 - low 1.10


def test_atr_of_identical_candles_equals_their_range():
    n = 40
    df = pd.DataFrame({"high": [1.101] * n, "low": [1.099] * n, "close": [1.100] * n},
                      index=pd.date_range("2026-11-02", periods=n, freq="1h", tz="UTC"))
    assert atr(df, 14).dropna().to_numpy() == pytest.approx(0.002)


# ---------- 3. edge cases ----------


def test_rsi_only_rising_is_100_only_falling_is_0():
    assert rsi(close_series(np.linspace(1.0, 2.0, 30)), 14).dropna().eq(100).all()
    assert rsi(close_series(np.linspace(2.0, 1.0, 30)), 14).dropna().eq(0).all()


def test_rsi_flat_prices_is_undefined_not_50():
    assert rsi(close_series([1.1] * 30), 14).isna().all()


def test_ema_of_constant_is_constant():
    assert ema(close_series([1.25] * 30), 10).dropna().eq(1.25).all()


def test_first_value_positions():
    c = random_candles(100)["close"]
    assert ema(c, 20).first_valid_index() == c.index[19]   # needs 20 closes
    assert rsi(c, 14).first_valid_index() == c.index[14]   # needs 14 changes = 15 closes
    assert atr(random_candles(100), 14).first_valid_index() == c.index[14]


def test_too_little_data_gives_nan_not_a_guess():
    c = close_series([1.1, 1.2, 1.3])
    assert ema(c, 20).isna().all()
    assert rsi(c, 14).isna().all()


def test_rsi_always_between_0_and_100():
    values = rsi(random_candles(2000, seed=3)["close"], 14).dropna()
    assert values.between(0, 100).all()


def test_output_keeps_index_and_input_is_untouched():
    df = random_candles(50)
    before = df.copy()
    out = ema(df["close"], 10)
    assert out.index.equals(df.index)
    pd.testing.assert_frame_equal(df, before)


# ---------- 4. warm-up ----------


@pytest.mark.parametrize("kind,period,expected", [
    ("ema", 20, 20 + 84),     # weight 2/21  -> 8 / weight = 84
    ("ema", 50, 50 + 204),    # weight 2/51  -> 204
    ("ema", 200, 200 + 804),  # weight 2/201 -> 804
    ("rsi", 14, 15 + 112),    # weight 1/14  -> 112, seed needs 14 changes = 15 closes
    ("atr", 14, 15 + 112),
])
def test_warmup_candles(kind, period, expected):
    assert warmup_candles(kind, period) == expected


def test_warmup_rejects_unknown_indicator():
    with pytest.raises(ValueError, match="unknown"):
        warmup_candles("macd", 12)


def test_drop_warmup_hides_values_until_enough_history():
    s = close_series(range(1, 11))
    out = drop_warmup(s, 4)
    assert out.isna().tolist() == [True, True, True] + [False] * 7   # position 3 = 4th candle
    assert out.iloc[3] == 4 and s.isna().sum() == 0                   # input untouched


@pytest.mark.parametrize("kind,period", [("ema", 20), ("ema", 50), ("ema", 200), ("rsi", 14), ("atr", 14)])
def test_required_history_matches_long_history(kind, period):
    """With exactly `warmup_candles` of history, the last value equals the long-history value."""
    df = random_candles(6000, seed=11)
    need = warmup_candles(kind, period)
    short = df.iloc[-need:]

    def calc(frame):
        if kind == "ema":
            return ema(frame["close"], period).iloc[-1]
        if kind == "rsi":
            return rsi(frame["close"], period).iloc[-1]
        return atr(frame, period).iloc[-1]

    long_value, short_value = calc(df), calc(short)
    typical_move = atr(df, 14).iloc[-1]
    if kind == "ema":
        assert abs(short_value - long_value) <= 0.01 * typical_move     # within 1% of ATR
    elif kind == "rsi":
        assert abs(short_value - long_value) <= 0.1                     # within 0.1 RSI points
    else:
        assert abs(short_value - long_value) <= 0.005 * long_value      # within 0.5%


@pytest.mark.live
@pytest.mark.parametrize("pair", ["EUR/USD", "USD/JPY"])
def test_live_configured_history_is_enough_for_ema200(pair):
    """Real data: EMA 200 from the configured 1,200 H1 candles matches the full ~2-year history."""
    from forex_scanner import config
    from forex_scanner.providers import get_provider

    full = get_provider("yfinance").get_candles(pair, "H1", None).candles
    full = full[full["complete"]]
    short = full.iloc[-config.INDICATOR_HISTORY:]
    diff = abs(ema(short["close"], 200).iloc[-1] - ema(full["close"], 200).iloc[-1])
    assert diff <= 0.01 * atr(full, 14).iloc[-1]


def test_rejects_missing_values_and_bad_periods():
    c = close_series([1.1, np.nan, 1.2] + [1.3] * 20)
    with pytest.raises(ValueError, match="missing"):
        ema(c, 5)
    with pytest.raises(ValueError, match="period"):
        rsi(close_series([1.1] * 20), 0)
    with pytest.raises(ValueError, match="period"):
        ema(close_series([1.1] * 20), 2.5)
