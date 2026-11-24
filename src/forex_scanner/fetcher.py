"""Fetch candles for many pairs and timeframes in one go.

Rules:
  - One failure never stops the others; every result says OK or why it failed.
  - Temporary errors are retried a few times, waiting longer each time.
  - A short pause between requests keeps us polite to the free data source.
"""

import time
from dataclasses import dataclass
from typing import Callable

from forex_scanner.providers import CandleSet, DataProvider, ProviderError


@dataclass(frozen=True)
class FetchResult:
    pair: str
    timeframe: str
    candle_set: CandleSet | None = None
    error: str | None = None
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.candle_set is not None


def fetch_one(
    provider: DataProvider,
    pair: str,
    timeframe: str,
    count: int | None,
    max_retries: int = 2,
    retry_wait_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    attempt = 0
    while True:
        attempt += 1
        try:
            cs = provider.get_candles(pair, timeframe, count)
            return FetchResult(pair, timeframe, candle_set=cs, attempts=attempt)
        except ProviderError as exc:
            if not exc.retryable or attempt > max_retries:
                return FetchResult(pair, timeframe, error=str(exc), attempts=attempt)
            sleep(retry_wait_seconds * attempt)  # 2 s, then 4 s, ...


def fetch_many(
    provider: DataProvider,
    pairs: list[str],
    timeframes: list[str],
    count: int | None,
    delay_seconds: float = 1.0,
    max_retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> list[FetchResult]:
    results = []
    for i, (pair, tf) in enumerate((p, t) for p in pairs for t in timeframes):
        if i > 0:
            sleep(delay_seconds)
        results.append(fetch_one(provider, pair, tf, count, max_retries=max_retries, sleep=sleep))
    return results
