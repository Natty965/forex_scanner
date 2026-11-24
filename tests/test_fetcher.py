"""Tests for fetching many pairs/timeframes: isolation, retries, politeness. Fully offline."""

from datetime import datetime, timezone

import pandas as pd

from forex_scanner.fetcher import fetch_many, fetch_one
from forex_scanner.providers import CandleSet, DataProvider, ProviderError


def make_set(pair, tf):
    idx = pd.DatetimeIndex(["2026-11-20 10:00"], tz="UTC")
    df = pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                       "volume": [float("nan")], "complete": [True]}, index=idx)
    return CandleSet(pair, tf, "fake", datetime.now(timezone.utc), df, False)


class ScriptedProvider(DataProvider):
    """Plays back a list of outcomes per (pair, timeframe): an Exception or 'ok'."""

    name = "fake"

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def get_candles(self, pair, timeframe, count):
        self.calls.append((pair, timeframe))
        outcome = self.script.get((pair, timeframe), ["ok"]).pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return make_set(pair, timeframe)


def no_sleep(_seconds):
    pass


def test_retries_temporary_error_then_succeeds():
    p = ScriptedProvider({("EUR/USD", "H1"): [ProviderError("timeout", retryable=True), "ok"]})
    r = fetch_one(p, "EUR/USD", "H1", 10, sleep=no_sleep)
    assert r.ok and r.attempts == 2


def test_does_not_retry_permanent_error():
    p = ScriptedProvider({("EUR/USD", "H1"): [ProviderError("bad pair", retryable=False), "ok"]})
    r = fetch_one(p, "EUR/USD", "H1", 10, sleep=no_sleep)
    assert not r.ok and r.attempts == 1 and "bad pair" in r.error


def test_gives_up_after_max_retries():
    err = ProviderError("down", retryable=True)
    p = ScriptedProvider({("EUR/USD", "H1"): [err, err, err, "ok"]})
    r = fetch_one(p, "EUR/USD", "H1", 10, max_retries=2, sleep=no_sleep)
    assert not r.ok and r.attempts == 3


def test_one_failure_does_not_stop_the_others():
    p = ScriptedProvider({("GBP/USD", "H1"): [ProviderError("broken")]})
    results = fetch_many(p, ["EUR/USD", "GBP/USD", "USD/JPY"], ["H1"], 10, sleep=no_sleep)
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].error == "broken"


def test_pauses_between_requests():
    waits = []
    fetch_many(ScriptedProvider({}), ["EUR/USD", "GBP/USD"], ["M15", "H1"], 10,
               delay_seconds=1.5, sleep=waits.append)
    assert waits == [1.5, 1.5, 1.5]   # 4 requests -> 3 pauses, none before the first
