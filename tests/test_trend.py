"""Trend detection on synthetic prices where the right answer is obvious."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from forex_scanner.analysis.trend import (
    DATA_MARKET_CLOSED,
    DATA_OK,
    DATA_STALE,
    DOWN,
    SIDEWAYS,
    UNKNOWN,
    UP,
    data_status,
    detect_trend,
)
from forex_scanner.providers import CandleSet


def candles(closes, half_range=0.0005, complete_last=True, start="2026-11-24 00:00", freq="1h"):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    df = pd.DataFrame({"open": closes, "high": closes + half_range, "low": closes - half_range,
                       "close": closes, "volume": np.nan, "complete": True}, index=idx)
    if not complete_last:
        df.iloc[-1, df.columns.get_loc("complete")] = False
    return df


def test_steady_rise_is_up():
    t = detect_trend(candles(np.linspace(1.10, 1.11, 60)), "EUR/USD", "H1")  # +100 pips, range 10 pips
    assert t.direction == UP
    assert t.move_pips > 0 and t.move_ranges >= 3 and t.distance_pips > 0
    assert any("UP" in r for r in t.reasons)


def test_steady_fall_is_down():
    t = detect_trend(candles(np.linspace(1.11, 1.10, 60)), "EUR/USD", "H1")
    assert t.direction == DOWN


def test_zigzag_without_progress_is_sideways():
    closes = 1.10 + 0.0008 * np.sin(np.arange(60))
    t = detect_trend(candles(closes), "EUR/USD", "H1")
    assert t.direction == SIDEWAYS
    assert any("under 3" in r for r in t.reasons)


def test_small_drift_is_sideways():
    t = detect_trend(candles(np.linspace(1.1000, 1.1010, 60)), "EUR/USD", "H1")  # 10 pips vs 10-pip candles
    assert t.direction == SIDEWAYS


def test_rise_then_sharp_drop_below_average_is_sideways_mixed():
    closes = np.concatenate([np.linspace(1.10, 1.12, 45), np.full(5, 1.1050)])
    t = detect_trend(candles(closes), "EUR/USD", "H1")
    assert t.move_ranges >= 3 and t.distance_pips < 0
    assert t.direction == SIDEWAYS
    assert any("disagree" in r for r in t.reasons)


def test_forming_candle_is_ignored():
    closes = np.concatenate([np.full(55, 1.10), [1.20]])   # a huge spike in the forming candle
    t = detect_trend(candles(closes, complete_last=False), "EUR/USD", "H1")
    assert t.direction == SIDEWAYS and t.last_close == 1.10


def test_not_enough_candles_is_unknown():
    t = detect_trend(candles(np.linspace(1.10, 1.11, 30)), "EUR/USD", "H1")
    assert t.direction == UNKNOWN and t.move_pips is None
    assert "need 50" in t.reasons[0]


def test_flat_prices_are_unknown_not_a_division_error():
    t = detect_trend(candles(np.full(60, 1.10), half_range=0.0), "EUR/USD", "H1")
    assert t.direction == UNKNOWN


def test_jpy_pips():
    t = detect_trend(candles(np.linspace(158.0, 159.0, 60), half_range=0.05), "USD/JPY", "H1")
    assert t.direction == UP
    assert t.move_pips == pytest.approx(100.0 * 49 / 59, rel=1e-6)  # line over the last 50 of 60 points


def _set(df, fetched_at, tf="H1"):
    return CandleSet("EUR/USD", tf, "yfinance", fetched_at, df, volume_available=False)


def test_data_status_ok_stale_closed():
    df = candles(np.full(60, 1.1), start="2026-11-24 00:00")            # last candle opens 11:00 UTC Tue
    last = df.index[-1].to_pydatetime()
    assert data_status(_set(df, last + timedelta(minutes=50))) == DATA_OK
    assert data_status(_set(df, last + timedelta(hours=2))) == DATA_STALE
    sat = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    assert data_status(_set(df, sat)) == DATA_MARKET_CLOSED
