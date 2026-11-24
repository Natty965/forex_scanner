"""Tests for SQLite candle storage. Each test uses its own temporary database file."""

import sqlite3
from datetime import datetime, timezone

import pandas as pd
import pytest

from forex_scanner.providers import CandleSet
from forex_scanner.storage import CandleStore

FETCH_1 = datetime(2026, 11, 20, 21, 50, tzinfo=timezone.utc)
FETCH_2 = datetime(2026, 11, 20, 22, 5, tzinfo=timezone.utc)


def candle_set(rows, fetched_at=FETCH_1, source="yfinance", pair="EUR/USD", tf="M15", warnings=()):
    """rows: list of (time, open, high, low, close, complete)."""
    idx = pd.DatetimeIndex([r[0] for r in rows], tz="UTC")
    df = pd.DataFrame(
        {"open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
         "close": [r[4] for r in rows], "volume": float("nan"), "complete": [r[5] for r in rows]},
        index=idx,
    )
    return CandleSet(pair, tf, source, fetched_at, df, volume_available=False, warnings=warnings)


@pytest.fixture
def store(tmp_path):
    with CandleStore(tmp_path / "test.db") as s:
        yield s


A = ("2026-11-20 21:15", 1.1000, 1.1010, 1.0990, 1.1005, True)
B = ("2026-11-20 21:30", 1.1005, 1.1020, 1.1000, 1.1015, True)
C_FORMING = ("2026-11-20 21:45", 1.1015, 1.1018, 1.1012, 1.1016, False)
C_FINAL = ("2026-11-20 21:45", 1.1015, 1.1030, 1.1010, 1.1025, True)


def test_save_and_load_round_trip(store):
    stats = store.save(candle_set([A, B]))
    assert (stats.inserted, stats.revised, stats.unchanged) == (2, 0, 0)

    df = store.load("yfinance", "EUR/USD", "M15")
    assert list(df.index) == [pd.Timestamp(A[0], tz="UTC"), pd.Timestamp(B[0], tz="UTC")]
    assert df.loc[pd.Timestamp(B[0], tz="UTC"), "close"] == 1.1015   # exact, no rounding
    assert df["volume"].isna().all()                                 # unknown stays unknown
    assert df["fetched_at"].iloc[0] == pd.Timestamp(FETCH_1)


def test_saving_same_data_twice_creates_no_duplicates(store):
    store.save(candle_set([A, B]))
    stats = store.save(candle_set([A, B], fetched_at=FETCH_2))
    assert (stats.inserted, stats.revised, stats.unchanged) == (0, 0, 2)
    assert len(store.load("yfinance", "EUR/USD", "M15")) == 2


def test_forming_candle_is_replaced_by_final_version(store):
    store.save(candle_set([A, B, C_FORMING]))
    stats = store.save(candle_set([B, C_FINAL], fetched_at=FETCH_2))
    assert (stats.inserted, stats.revised, stats.unchanged) == (0, 1, 1)

    last = store.load("yfinance", "EUR/USD", "M15").iloc[-1]
    assert last["close"] == 1.1025 and bool(last["complete"])
    assert last["fetched_at"] == pd.Timestamp(FETCH_2)    # shows when this version arrived


def test_sources_are_kept_separate(store):
    store.save(candle_set([A], source="yfinance"))
    store.save(candle_set([A], source="oanda"))
    assert len(store.load("yfinance", "EUR/USD", "M15")) == 1
    assert len(store.load("oanda", "EUR/USD", "M15")) == 1


def test_load_limit_returns_most_recent_oldest_first(store):
    store.save(candle_set([A, B, C_FINAL]))
    df = store.load("yfinance", "EUR/USD", "M15", limit=2)
    assert list(df.index.strftime("%H:%M")) == ["21:30", "21:45"]


def test_database_rejects_impossible_prices(store):
    bad = ("2026-11-20 22:00", 1.1, 1.0, 1.2, 1.1, True)   # high below low
    with pytest.raises(sqlite3.IntegrityError):
        store.save(candle_set([A, bad]))
    # All-or-nothing: the good candle in the same save was not stored either.
    assert store.load("yfinance", "EUR/USD", "M15").empty


def test_failures_are_logged_and_shown_in_summary(store):
    store.save(candle_set([A, B]))
    store.log_failure("yfinance", "EUR/USD", "M15", "Yahoo down", fetched_at=FETCH_2)

    s = store.summary().iloc[0]
    assert s["candles"] == 2
    assert s["last_ok_fetch"] == "2026-11-20T21:50:00Z"
    assert s["last_attempt"] == "2026-11-20T22:05:00Z"
    assert s["last_attempt_ok"] == 0 and s["last_error"] == "Yahoo down"


def test_pair_that_never_succeeded_still_appears_in_summary(store):
    store.save(candle_set([A]))
    store.log_failure("yfinance", "GBP/USD", "H1", "symbol not found", fetched_at=FETCH_1)
    s = store.summary().set_index(["pair", "timeframe"])
    gbp = s.loc[("GBP/USD", "H1")]
    assert gbp["candles"] == 0 and gbp["last_attempt_ok"] == 0
    assert gbp["last_error"] == "symbol not found"
    assert pd.isna(gbp["last_ok_fetch"])          # never had a good fetch


def test_warnings_are_kept_in_fetch_log(store):
    store.save(candle_set([A], warnings=("dropped 1 candle(s) with missing prices",)))
    (warnings,) = store.conn.execute("SELECT warnings FROM fetch_log").fetchone()
    assert "missing prices" in warnings


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "persist.db"
    with CandleStore(path) as s:
        s.save(candle_set([A, B]))
    with CandleStore(path) as s:
        assert len(s.load("yfinance", "EUR/USD", "M15")) == 2
