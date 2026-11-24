"""Monitor loop: pacing, backoff, trend-change events, saving. Offline with a fake provider and clock."""

import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from rich.console import Console

from forex_scanner import config
from forex_scanner.monitor import next_wait, run_monitor
from forex_scanner.providers import CandleSet, DataProvider, ProviderError
from forex_scanner.providers.base import TIMEFRAME_DURATIONS
from forex_scanner.storage import CandleStore

WEDNESDAY = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)   # market open
SATURDAY = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)    # market closed


@pytest.fixture(autouse=True)
def no_request_delay(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)


class FakeProvider(DataProvider):
    """Flat prices, or a steady rise when `rising` is True. Can be told to fail everything."""

    name = "fake"

    def __init__(self, clock):
        self.clock = clock
        self.rising = False
        self.fail = False

    def get_candles(self, pair, timeframe, count):
        if self.fail:
            raise ProviderError("source down", retryable=False)
        now = self.clock()
        step = TIMEFRAME_DURATIONS[timeframe]
        n = 120
        idx = pd.date_range(end=pd.Timestamp(now).floor("min") - step, periods=n, freq=step)
        closes = np.linspace(1.10, 1.12, n) if self.rising else np.full(n, 1.10)
        df = pd.DataFrame({"open": closes, "high": closes + 0.0005, "low": closes - 0.0005,
                           "close": closes, "volume": np.nan, "complete": True}, index=idx)
        return CandleSet(pair, timeframe, self.name, now, df, volume_available=False)


def run(provider, clock, sleeps, cycles, **kw):
    out = io.StringIO()
    console = Console(file=out, width=140)
    n = run_monitor(provider, ["EUR/USD"], ["H1"], interval=kw.pop("interval", timedelta(minutes=5)),
                    closed_interval=timedelta(minutes=30), live_max=5, delayed_max=30,
                    cycles=cycles, console=console, sleep=sleeps.append, now=clock,
                    clear_screen=False, **kw)
    return n, out.getvalue()


def test_next_wait_rules():
    five, thirty = timedelta(minutes=5), timedelta(minutes=30)
    assert next_wait(five, thirty, market_closed=False, consecutive_total_failures=0) == five
    assert next_wait(five, thirty, market_closed=True, consecutive_total_failures=0) == thirty
    assert next_wait(five, thirty, False, 1) == timedelta(minutes=10)
    assert next_wait(five, thirty, False, 2) == timedelta(minutes=20)
    assert next_wait(five, thirty, False, 5) == timedelta(minutes=30)   # capped


def test_detects_trend_change_between_cycles_and_waits_normal_interval():
    provider = FakeProvider(lambda: WEDNESDAY)
    sleeps = []

    def sleep(seconds):          # between cycle 1 and 2 the market starts rising
        sleeps.append(seconds)
        provider.rising = True

    out = io.StringIO()
    n = run_monitor(provider, ["EUR/USD"], ["H1"], timedelta(minutes=5), timedelta(minutes=30), 5, 30,
                    cycles=2, console=Console(file=out, width=140), sleep=sleep,
                    now=lambda: WEDNESDAY, clear_screen=False)
    text = out.getvalue()
    assert n == 2
    assert sleeps == [300.0]
    assert "EUR/USD H1: Trend SIDEWAYS -> UP" in text
    assert "LIVE" in text
    assert "Indicators" in text                     # indicators table shown every cycle
    assert "Events this session" in text


def test_one_download_per_pair_and_timeframe_per_cycle():
    provider = FakeProvider(lambda: WEDNESDAY)
    calls = []
    original = provider.get_candles

    def counting(pair, tf, count):
        calls.append((pair, tf))
        return original(pair, tf, count)

    provider.get_candles = counting
    run_monitor(provider, ["EUR/USD", "GBP/USD"], ["M15", "H1"], timedelta(minutes=5), timedelta(minutes=30),
                5, 30, cycles=1, console=Console(file=io.StringIO()), sleep=lambda s: None,
                now=lambda: WEDNESDAY, clear_screen=False)
    assert sorted(calls) == sorted([("EUR/USD", "M1"), ("GBP/USD", "M1"), ("EUR/USD", "M15"),
                                    ("EUR/USD", "H1"), ("GBP/USD", "M15"), ("GBP/USD", "H1")])


def test_backs_off_when_everything_fails_and_shows_no_prices():
    provider = FakeProvider(lambda: WEDNESDAY)
    provider.fail = True
    sleeps = []
    n, text = run(provider, lambda: WEDNESDAY, sleeps, cycles=3)
    assert sleeps == [600.0, 1200.0]            # 10 min, then 20 min
    assert "Backing off" in text
    assert "1.10000" not in text                # never a price without fresh data
    assert "FAILED" in text


def test_slows_down_while_market_closed():
    sleeps = []
    _, text = run(FakeProvider(lambda: SATURDAY), lambda: SATURDAY, sleeps, cycles=2)
    assert sleeps == [1800.0]
    assert "Market closed" in text and "MARKET CLOSED" in text


def test_interval_below_two_minutes_is_raised_to_two():
    sleeps = []
    run(FakeProvider(lambda: WEDNESDAY), lambda: WEDNESDAY, sleeps, cycles=2, interval=timedelta(seconds=10))
    assert sleeps == [120.0]


def test_ctrl_c_stops_cleanly():
    def interrupt(_):
        raise KeyboardInterrupt

    out = io.StringIO()
    n = run_monitor(FakeProvider(lambda: WEDNESDAY), ["EUR/USD"], ["H1"], timedelta(minutes=5),
                    timedelta(minutes=30), 5, 30, cycles=None, console=Console(file=out),
                    sleep=interrupt, now=lambda: WEDNESDAY, clear_screen=False)
    assert n == 1 and "Stopped after 1 cycle(s)" in out.getvalue()


def test_saves_each_cycle_to_database(tmp_path):
    db = tmp_path / "monitor.db"
    run(FakeProvider(lambda: WEDNESDAY), lambda: WEDNESDAY, [], cycles=1, db_path=db)
    with CandleStore(db) as store:
        assert len(store.load("fake", "EUR/USD", "M1")) == 120
        assert len(store.load("fake", "EUR/USD", "H1")) == 120
