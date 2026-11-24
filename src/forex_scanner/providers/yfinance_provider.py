"""Forex candles from Yahoo Finance via the unofficial `yfinance` library.

Limits found by testing on 2026-11-22 (yfinance 1.8.0):
  - 1m candles: last ~8 days only.  5m/15m/30m: last 60 days.  1h: last 730 days.
  - Volume is always 0 for FX, so we store it as NaN ("unknown"), never as 0.
  - Intraday timestamps come in London time; we convert everything to UTC.
  - Yahoo's "regularMarketTime" can show the current time even when no new
    price exists, so freshness is judged only from the last candle's time.
  - Yahoo's DAILY FX candles do not match its intraday data (the daily "close"
    is roughly the price at the START of the day, and a stray Sunday bar exists).
    So D1 candles are built here from real hourly candles, using the standard
    Forex day that ends at 17:00 New York time. That limits D1 to ~2 years.
  - Yahoo adds flat "candles" after the Friday close (e.g. 22:00 UTC). The market
    is shut from Friday 17:00 to Sunday 17:00 New York time, so candles stamped
    in that window are discarded.
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from yfinance import exceptions as yf_errors

from forex_scanner.market import closed_mask

from .base import CANDLE_COLUMNS, TIMEFRAME_DURATIONS, CandleSet, DataProvider, ProviderError

# yfinance prints its own warnings. We raise clear errors ourselves instead.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
# Make yfinance raise its errors (it hides them by default) so we can tell them apart.
yf.config.debug.hide_exceptions = False

REQUEST_TIMEOUT_SECONDS = 15

# Our pair name -> Yahoo symbol. (Yahoo's own USD/JPY symbol is "JPY=X".)
YAHOO_SYMBOLS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
}

# Our timeframe -> (Yahoo interval, how many days back Yahoo allows)
YAHOO_INTERVALS = {
    "M1": ("1m", 7),
    "M5": ("5m", 59),
    "M15": ("15m", 59),
    "M30": ("30m", 59),
    "H1": ("1h", 729),
    "D1": ("1h", 729),  # built from hourly candles, see note at the top
}


class YFinanceProvider(DataProvider):
    name = "yfinance"

    def get_candles(self, pair: str, timeframe: str, count: int | None) -> CandleSet:
        if pair not in YAHOO_SYMBOLS:
            raise ProviderError(f"Unsupported pair {pair!r}. Supported: {', '.join(YAHOO_SYMBOLS)}")
        if timeframe not in YAHOO_INTERVALS:
            raise ProviderError(f"Unsupported timeframe {timeframe!r}. Supported: {', '.join(YAHOO_INTERVALS)}")
        if count is not None and count < 1:
            raise ProviderError("count must be at least 1")

        symbol = YAHOO_SYMBOLS[pair]
        interval, max_days = YAHOO_INTERVALS[timeframe]
        fetched_at = datetime.now(timezone.utc)
        # For D1 ask for one extra day: the oldest day fetched is usually partial and gets dropped.
        extra = 1 if timeframe == "D1" else 0
        start = fetched_at - self._lookback(timeframe, None if count is None else count + extra, max_days)

        raw = self._download(symbol, interval, start, f"{pair} {timeframe}")
        candles, warnings = self._normalize(raw, timeframe, fetched_at)
        if candles.empty:
            raise ProviderError(
                f"yfinance returned no usable candles for {pair} {timeframe} (symbol {symbol}).",
                retryable=True,
            )

        if count is not None:
            if len(candles) < count:
                warnings.append(
                    f"only {len(candles)} of {count} candles available "
                    f"(Yahoo keeps about {max_days} days of {interval} data)"
                )
            candles = candles.tail(count)

        return CandleSet(
            pair=pair,
            timeframe=timeframe,
            source=self.name,
            fetched_at=fetched_at,
            candles=candles,
            volume_available=False,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _download(symbol: str, interval: str, start: datetime, label: str) -> pd.DataFrame:
        try:
            return yf.Ticker(symbol).history(
                start=start, interval=interval, auto_adjust=False, actions=False,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except yf_errors.YFRateLimitError as exc:
            raise ProviderError(f"Yahoo rate limit hit for {label}; wait before retrying.", retryable=True) from exc
        except yf_errors.YFTickerMissingError as exc:
            raise ProviderError(f"Yahoo does not recognise the symbol for {label}: {exc}") from exc
        except yf_errors.YFPricesMissingError as exc:
            raise ProviderError(f"Yahoo has no prices for {label} in this range: {exc}", retryable=True) from exc
        except Exception as exc:  # network errors, timeouts, Yahoo format changes
            raise ProviderError(f"yfinance request failed for {label}: {exc}", retryable=True) from exc

    @staticmethod
    def _lookback(timeframe: str, count: int | None, max_days: int) -> timedelta:
        if count is None:
            return timedelta(days=max_days)
        # Ask for extra time to cover weekends and gaps, but stay inside Yahoo's limit.
        wanted = TIMEFRAME_DURATIONS[timeframe] * count * 1.6 + timedelta(days=4)
        return min(wanted, timedelta(days=max_days))

    @staticmethod
    def _normalize(raw: pd.DataFrame, timeframe: str, fetched_at: datetime) -> tuple[pd.DataFrame, list[str]]:
        warnings: list[str] = []
        if raw is None or raw.empty:
            return pd.DataFrame(columns=CANDLE_COLUMNS), warnings

        df = raw.rename(columns=str.lower)[["open", "high", "low", "close"]].copy()

        idx = pd.DatetimeIndex(df.index)
        df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        df.index.name = "time"
        df = df[~df.index.duplicated(keep="last")].sort_index()

        df, warnings = YFinanceProvider._drop_bad_rows(df)
        df = df[~closed_mask(df.index)]

        if timeframe == "D1":
            df, candle_end = YFinanceProvider._hourly_to_daily(df)
        else:
            candle_end = df.index + TIMEFRAME_DURATIONS[timeframe]

        df["volume"] = float("nan")  # Yahoo FX volume is always 0 = meaningless
        df["complete"] = candle_end <= pd.Timestamp(fetched_at)
        return df[CANDLE_COLUMNS], warnings

    @staticmethod
    def _drop_bad_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        """Remove rows we cannot trust. We never repair or guess prices."""
        warnings = []
        missing = df.isna().any(axis=1)
        if missing.any():
            warnings.append(f"dropped {int(missing.sum())} candle(s) with missing prices")
        df = df[~missing]

        op, hi, lo, cl = df["open"], df["high"], df["low"], df["close"]
        invalid = (
            (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | (hi < lo) | (hi < op) | (hi < cl) | (lo > op) | (lo > cl)
        )
        if invalid.any():
            warnings.append(f"dropped {int(invalid.sum())} candle(s) with impossible prices (e.g. high < low)")
        return df[~invalid], warnings

    @staticmethod
    def _hourly_to_daily(hourly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
        """Group hourly candles into Forex trading days (17:00 New York -> 17:00 New York).

        Returns the daily candles (index = day open time in UTC) and each day's end time.
        The oldest day is dropped because the fetch window usually starts part-way through it.
        """
        if hourly.empty:
            return hourly, hourly.index
        ny = hourly.index.tz_convert("America/New_York")
        trading_date = (ny + pd.Timedelta(hours=7)).normalize().tz_localize(None)  # 17:00 NY -> next date
        daily = hourly.groupby(trading_date).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}
        ).iloc[1:]
        # A trading date D runs from 17:00 New York on D-1 to 17:00 New York on D.
        dates = pd.DatetimeIndex(daily.index)
        ny_close = (dates + pd.Timedelta(hours=17)).tz_localize("America/New_York")
        daily.index = (ny_close - pd.Timedelta(days=1)).tz_convert("UTC")
        daily.index.name = "time"
        return daily, ny_close.tz_convert("UTC")
