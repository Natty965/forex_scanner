"""The contract every data provider must follow.

The rest of the app only talks to `DataProvider`, never to yfinance or OANDA
directly. Switching provider later means writing one new class, nothing else.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

# Timeframe names follow OANDA's style so a later switch needs no renaming.
TIMEFRAME_DURATIONS: dict[str, timedelta] = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "D1": timedelta(days=1),
}

# Standard candle columns. The index is the candle OPEN time, in UTC.
CANDLE_COLUMNS = ["open", "high", "low", "close", "volume", "complete"]


class ProviderError(Exception):
    """Data could not be fetched. Callers must show this, never fall back silently.

    retryable=True  -> temporary problem (network, rate limit); trying again may work.
    retryable=False -> the request itself is wrong (unknown pair); retrying is pointless.
    """

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class CandleSet:
    """Candles plus the facts needed to judge how trustworthy and fresh they are."""

    pair: str                 # e.g. "EUR/USD"
    timeframe: str            # e.g. "M15"
    source: str               # e.g. "yfinance"
    fetched_at: datetime      # when WE received the data (UTC)
    candles: pd.DataFrame     # columns = CANDLE_COLUMNS, UTC index, oldest first
    volume_available: bool    # False = volume column is all NaN (unknown, not zero)
    warnings: tuple[str, ...] = ()  # data-quality notes, e.g. rows dropped as invalid

    @property
    def last_candle_time(self) -> datetime:
        return self.candles.index[-1].to_pydatetime()

    @property
    def age(self) -> timedelta:
        """Time between the END of the last candle and when we fetched it."""
        end = self.last_candle_time + TIMEFRAME_DURATIONS[self.timeframe]
        return max(self.fetched_at - end, timedelta(0))


class DataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_candles(self, pair: str, timeframe: str, count: int | None) -> CandleSet:
        """Return up to `count` most recent candles (None = all the source allows).

        Raise ProviderError on failure. Never return made-up or padded data.
        """
