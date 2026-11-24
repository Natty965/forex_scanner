"""Shared test doubles: a fake data source that never touches the internet."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from forex_scanner.providers import CandleSet, DataProvider, ProviderError
from forex_scanner.providers.base import TIMEFRAME_DURATIONS

WEDNESDAY = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)   # market open
SATURDAY = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)    # market closed


class FakeProvider(DataProvider):
    """Flat prices, or a steady rise when `rising` is True. Enough history for every indicator.

    The newest candle of each timeframe closed just before `clock()`, like real data.
    """

    name = "fake"

    def __init__(self, clock=lambda: WEDNESDAY):
        self.clock = clock
        self.rising = False
        self.fail = False
        self.calls = 0

    def get_candles(self, pair, timeframe, count):
        self.calls += 1
        if self.fail:
            raise ProviderError("source down", retryable=False)
        now = self.clock()
        step = TIMEFRAME_DURATIONS[timeframe]
        n = 1300
        idx = pd.date_range(end=pd.Timestamp(now).floor("min") - step, periods=n, freq=step)
        # rising: about 113 pips over the last 50 candles (~11 average ranges), clearly UP
        closes = np.linspace(1.00, 1.30, n) if self.rising else 1.10 + 0.0003 * np.sin(np.arange(n))
        df = pd.DataFrame({"open": closes, "high": closes + 0.0005, "low": closes - 0.0005,
                           "close": closes, "volume": np.nan, "complete": True}, index=idx)
        return CandleSet(pair, timeframe, self.name, now, df, volume_available=False)
