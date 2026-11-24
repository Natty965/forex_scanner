"""Tests for the yfinance provider.

Most tests replace Yahoo with a fake, so they are fast, offline and repeatable.
The one real-network test is marked `live` and skipped unless asked for:
    pytest -m live
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from forex_scanner.providers import ProviderError
from forex_scanner.providers import yfinance_provider as yp


class FakeTicker:
    """Stands in for yf.Ticker and returns whatever DataFrame we give it."""

    result: pd.DataFrame | Exception = pd.DataFrame()

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, **kwargs):
        if isinstance(FakeTicker.result, Exception):
            raise FakeTicker.result
        return FakeTicker.result


@pytest.fixture
def fake_yahoo(monkeypatch):
    monkeypatch.setattr(yp.yf, "Ticker", FakeTicker)
    return FakeTicker


def yahoo_frame(times, tz="Europe/London"):
    """Build a DataFrame shaped like Yahoo's: capitalised columns, London time, Volume 0."""
    idx = pd.DatetimeIndex(times).tz_localize(tz) if tz else pd.DatetimeIndex(times)
    n = len(idx)
    return pd.DataFrame(
        {"Open": [1.1] * n, "High": [1.2] * n, "Low": [1.0] * n,
         "Close": [1.15] * n, "Adj Close": [1.15] * n, "Volume": [0] * n},
        index=idx,
    )


def test_normalizes_to_utc_lowercase_and_unknown_volume(fake_yahoo):
    fake_yahoo.result = yahoo_frame(["2026-07-01 10:00", "2026-07-01 10:15"])  # summer: London = UTC+1
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 10)

    assert list(cs.candles.columns) == ["open", "high", "low", "close", "volume", "complete"]
    assert str(cs.candles.index.tz) == "UTC"
    assert cs.candles.index[0] == pd.Timestamp("2026-07-01 09:00", tz="UTC")
    assert cs.candles["volume"].isna().all()      # unknown, never a fake 0
    assert cs.volume_available is False
    assert cs.source == "yfinance"


def test_returns_only_requested_count_oldest_first(fake_yahoo):
    times = pd.date_range("2026-11-20 10:00", periods=20, freq="15min")
    fake_yahoo.result = yahoo_frame(times)
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 5)
    assert len(cs.candles) == 5
    assert cs.candles.index.is_monotonic_increasing
    assert cs.last_candle_time == times[-1].tz_localize("Europe/London").tz_convert("UTC")


def test_marks_still_forming_candle(fake_yahoo):
    now = pd.Timestamp.now(tz="UTC").floor("15min")
    fake_yahoo.result = yahoo_frame([now - pd.Timedelta(minutes=15), now], tz=None)
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 10)
    assert cs.candles["complete"].tolist() == [True, False]


def test_drops_rows_with_missing_prices(fake_yahoo):
    df = yahoo_frame(["2026-11-20 10:00", "2026-11-20 10:15"])
    df.iloc[0, df.columns.get_loc("Close")] = float("nan")
    fake_yahoo.result = df
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 1)
    assert len(cs.candles) == 1
    assert any("missing prices" in w for w in cs.warnings)


def test_daily_candles_are_built_from_hourly_with_new_york_close(fake_yahoo):
    # Hourly candles from Wed 20:00 UTC to Fri 21:00 UTC (November: New York = UTC-5,
    # so each Forex day ends at 22:00 UTC).
    times = pd.date_range("2026-11-18 20:00", "2026-11-20 21:00", freq="1h", tz="UTC")
    df = yahoo_frame(times, tz=None)
    df["Open"] = [100.0 + i for i in range(len(df))]
    df["Close"] = df["Open"] + 0.5
    df["High"] = df["Close"] + 1
    df["Low"] = df["Open"] - 1
    fake_yahoo.result = df
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "D1", 5)

    # The partial first day (Wed 20:00-21:00) is dropped; Thu and Fri remain.
    assert list(cs.candles.index) == [
        pd.Timestamp("2026-11-18 22:00", tz="UTC"),   # Thursday's Forex day opens Wed 17:00 NY
        pd.Timestamp("2026-11-19 22:00", tz="UTC"),   # Friday's opens Thu 17:00 NY
    ]
    thursday = cs.candles.iloc[0]
    assert thursday["open"] == 102        # first hour = Wed 22:00 UTC (row 2)
    assert thursday["close"] == 125.5     # last hour = Thu 21:00 UTC (row 25)
    assert cs.candles["complete"].all()


def test_candles_while_market_closed_are_discarded(fake_yahoo):
    # November: New York = UTC-5, so the market closes Fri 22:00 UTC, reopens Sun 22:00 UTC.
    times = ["2026-11-20 21:00", "2026-11-20 22:00",   # last real hour, then Yahoo's flat bar
             "2026-11-22 21:00", "2026-11-22 22:00"]   # still closed, then the weekly open
    fake_yahoo.result = yahoo_frame(times, tz="UTC")
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "H1", 10)
    assert list(cs.candles.index.strftime("%a %H:%M")) == ["Fri 21:00", "Sun 22:00"]


def test_friday_flat_bar_does_not_create_a_saturday_daily_candle(fake_yahoo):
    times = pd.date_range("2026-11-19 21:00", "2026-11-20 22:00", freq="1h", tz="UTC")
    fake_yahoo.result = yahoo_frame(times, tz=None)
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "D1", 5)
    assert list(cs.candles.index) == [pd.Timestamp("2026-11-19 22:00", tz="UTC")]  # Friday only


def test_yahoo_daily_interval_is_never_used():
    assert yp.YAHOO_INTERVALS["D1"][0] == "1h"


def test_empty_result_is_an_error_not_silence(fake_yahoo):
    fake_yahoo.result = pd.DataFrame()
    with pytest.raises(ProviderError, match="no usable candles"):
        yp.YFinanceProvider().get_candles("EUR/USD", "M15", 10)


def test_network_failure_becomes_retryable_provider_error(fake_yahoo):
    fake_yahoo.result = ConnectionError("no internet")
    with pytest.raises(ProviderError, match="no internet") as info:
        yp.YFinanceProvider().get_candles("GBP/USD", "H1", 10)
    assert info.value.retryable


def test_rate_limit_is_retryable(fake_yahoo):
    fake_yahoo.result = yp.yf_errors.YFRateLimitError()
    with pytest.raises(ProviderError, match="rate limit") as info:
        yp.YFinanceProvider().get_candles("EUR/USD", "M15", 10)
    assert info.value.retryable


def test_unknown_symbol_is_not_retryable(fake_yahoo):
    fake_yahoo.result = yp.yf_errors.YFTickerMissingError("EURUSD=X", "no data")
    with pytest.raises(ProviderError) as info:
        yp.YFinanceProvider().get_candles("EUR/USD", "M15", 10)
    assert not info.value.retryable


def test_impossible_prices_are_dropped_with_warning(fake_yahoo):
    df = yahoo_frame(["2026-11-20 10:00", "2026-11-20 10:15", "2026-11-20 10:30"])
    df.iloc[1, df.columns.get_loc("High")] = 0.9   # high below low: impossible
    fake_yahoo.result = df
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 10)
    assert len(cs.candles) == 2
    assert any("impossible prices" in w for w in cs.warnings)


def test_warns_when_fewer_candles_than_requested(fake_yahoo):
    fake_yahoo.result = yahoo_frame(["2026-11-20 10:00", "2026-11-20 10:15"])
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 50)
    assert len(cs.candles) == 2          # never padded with made-up candles
    assert any("only 2 of 50" in w for w in cs.warnings)


def test_count_none_returns_everything_and_asks_for_max_history(fake_yahoo):
    fake_yahoo.result = yahoo_frame(pd.date_range("2026-11-20", periods=30, freq="15min"))
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", None)
    assert len(cs.candles) == 30
    assert cs.warnings == ()
    assert yp.YFinanceProvider._lookback("H1", None, 729) == timedelta(days=729)


@pytest.mark.parametrize("pair,tf", [("AUD/USD", "M15"), ("EUR/USD", "H4")])
def test_rejects_unsupported_inputs(pair, tf):
    with pytest.raises(ProviderError, match="Unsupported"):
        yp.YFinanceProvider().get_candles(pair, tf, 10)


def test_lookback_stays_within_yahoo_limits():
    assert yp.YFinanceProvider._lookback("M1", 100_000, 7) == timedelta(days=7)
    assert yp.YFinanceProvider._lookback("M15", 10, 59) < timedelta(days=59)


def test_age_is_measured_from_end_of_last_candle(fake_yahoo):
    fake_yahoo.result = yahoo_frame(["2026-11-20 21:00"], tz="UTC")
    cs = yp.YFinanceProvider().get_candles("EUR/USD", "M15", 1)
    expected = cs.fetched_at - datetime(2026, 11, 20, 21, 15, tzinfo=timezone.utc)
    assert cs.age == expected


@pytest.mark.live
@pytest.mark.parametrize("pair", ["EUR/USD", "GBP/USD", "USD/JPY"])
def test_live_fetch_returns_real_candles(pair):
    cs = yp.YFinanceProvider().get_candles(pair, "H1", 5)
    assert 1 <= len(cs.candles) <= 5
    assert (cs.candles["high"] >= cs.candles["low"]).all()
