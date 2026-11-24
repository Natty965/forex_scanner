"""Indicator summaries on synthetic prices, plus the fetch step's candle counts and ordering."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from forex_scanner import config
from forex_scanner.analysis.indicator_summary import (
    MIXED,
    NA,
    RSI_HIGH,
    RSI_LOW,
    RSI_NEUTRAL,
    STACKED_DOWN,
    STACKED_UP,
    summarize_indicators,
)
from forex_scanner.providers import CandleSet, DataProvider
from forex_scanner.scan import get_indicators, get_market, get_trends

EMAS = [20, 50, 200]


def candles(closes, half_range=0.0005, complete_last=True):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-09-01", periods=len(closes), freq="1h", tz="UTC")
    df = pd.DataFrame({"open": closes, "high": closes + half_range, "low": closes - half_range,
                       "close": closes, "volume": np.nan, "complete": True}, index=idx)
    if not complete_last:
        df.iloc[-1, df.columns.get_loc("complete")] = False
    return df


def summary(df, pair="EUR/USD", tf="H1"):
    return summarize_indicators(df, pair, tf, EMAS, 14, 14)


def test_steady_rise():
    s = summary(candles(np.linspace(1.05, 1.15, 1500)))
    assert s.alignment == STACKED_UP
    assert all(s.distance_pips(p) > 0 for p in EMAS)
    assert s.rsi == 100 and s.rsi_zone == RSI_HIGH
    assert any("does not mean price will fall" in r for r in s.reasons)
    # every candle is 10 pips high-to-low, and the step between closes (0.67 pip) is smaller
    assert s.atr_pips == pytest.approx(10.0)


def test_emas_a_hair_apart_are_not_called_stacked():
    # tiny rise: EMAs end up only ~0.01-0.05 pips apart, below the 0.1-pip tolerance
    s = summary(candles(np.linspace(1.1000, 1.10001, 1500)))
    assert s.alignment == MIXED


def test_steady_fall():
    s = summary(candles(np.linspace(1.15, 1.05, 1500)))
    assert s.alignment == STACKED_DOWN
    assert all(s.distance_pips(p) < 0 for p in EMAS)
    assert s.rsi == 0 and s.rsi_zone == RSI_LOW


def test_sideways_zigzag_has_neutral_rsi():
    closes = 1.10 + 0.002 * np.sin(np.arange(1500) / 3)
    s = summary(candles(closes))
    assert s.rsi_zone == RSI_NEUTRAL and 30 < s.rsi < 70


def test_daily_sized_history_leaves_ema200_unavailable():
    s = summary(candles(np.linspace(1.05, 1.15, 520)), tf="D1")
    assert s.emas[200] is None and s.distance_pips(200) is None
    assert s.emas[20] is not None and s.emas[50] is not None
    assert s.alignment == STACKED_UP                       # from EMA 20 and 50 only
    assert any("EMA 200: n/a, needs 1004 closed D1 candles" in r for r in s.reasons)
    assert any("EMA 200 not available" in r for r in s.reasons)


def test_too_little_history_gives_na_everywhere_with_reasons():
    s = summary(candles(np.linspace(1.10, 1.11, 50)))
    assert all(v is None for v in s.emas.values())
    assert s.rsi is None and s.atr is None
    assert s.alignment == NA and s.rsi_zone == NA
    assert sum("n/a, needs" in r for r in s.reasons) == 5    # 3 EMAs + RSI + ATR


def test_flat_prices_rsi_undefined_not_fifty():
    s = summary(candles(np.full(1500, 1.10), half_range=0.0))
    assert s.rsi is None and s.rsi_zone == NA
    assert any("did not move" in r for r in s.reasons)
    assert s.alignment == MIXED                            # all EMAs equal: not in order


def test_forming_candle_is_ignored():
    closes = np.concatenate([np.full(1499, 1.10), [1.30]])
    s = summary(candles(closes, complete_last=False))
    assert s.close == 1.10 and s.closed_candles == 1499


def test_jpy_pips():
    s = summary(candles(np.linspace(150.0, 160.0, 1500), half_range=0.05), pair="USD/JPY")
    assert s.atr_pips == pytest.approx(s.atr / 0.01)
    assert s.distance_pips(20) == pytest.approx((s.close - s.emas[20]) / 0.01)


# ---------- fetch step ----------


class CountingProvider(DataProvider):
    name = "fake"

    def __init__(self):
        self.calls = []

    def get_candles(self, pair, timeframe, count):
        self.calls.append((pair, timeframe, count))
        df = candles(np.linspace(1.05, 1.15, 600))
        return CandleSet(pair, timeframe, self.name, datetime(2026, 11, 25, tzinfo=timezone.utc), df, False)


def test_get_indicators_counts_and_order(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    p = CountingProvider()
    rows, failures = get_indicators(p, ["EUR/USD", "GBP/USD"], ["H1", "D1"], store=None)
    counts = {(pair, tf): c for pair, tf, c in p.calls}
    assert counts[("EUR/USD", "H1")] == config.INDICATOR_HISTORY
    assert counts[("EUR/USD", "D1")] is None                # everything the source has
    assert [(s.pair, s.timeframe) for s, _ in rows] == [
        ("EUR/USD", "H1"), ("EUR/USD", "D1"), ("GBP/USD", "H1"), ("GBP/USD", "D1")]
    assert failures == []


def test_get_market_fetches_once_and_matches_standalone_trend(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    p = CountingProvider()
    view = get_market(p, ["EUR/USD"], ["H1", "D1"], store=None)
    assert len(p.calls) == 2                               # one download per pair x timeframe
    assert len(view.trends) == len(view.indicators) == 2

    standalone, _ = get_trends(CountingProvider(), ["EUR/USD"], ["H1", "D1"], store=None)
    for (a, _), (b, _) in zip(view.trends, standalone, strict=True):
        assert (a.direction, a.move_pips, a.last_candle_time) == (b.direction, b.move_pips, b.last_candle_time)
