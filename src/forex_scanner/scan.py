"""Fetch -> save -> analyse steps shared by the commands and the monitor (and, later, a dashboard)."""

import time
from dataclasses import dataclass, field

from forex_scanner import config
from forex_scanner.analysis.indicator_summary import IndicatorSummary, summarize_indicators
from forex_scanner.analysis.snapshot import PriceSnapshot, build_snapshot
from forex_scanner.analysis.structure import StructureResult, analyse_structure
from forex_scanner.analysis.trend import TrendResult, data_status, detect_trend
from forex_scanner.fetcher import FetchResult, fetch_many
from forex_scanner.providers import DataProvider
from forex_scanner.storage import CandleStore

# 1-minute candles for current prices: about 25 trading hours, enough for the 24h change.
PRICE_CANDLES = 1500


def fetch_and_store(provider: DataProvider, pairs: list[str], timeframes: list[str], count: int | None,
                    store: CandleStore | None) -> list[FetchResult]:
    """Fetch every pair x timeframe, save successes and log failures (if a store is given)."""
    results = fetch_many(provider, pairs, timeframes, count,
                         delay_seconds=config.REQUEST_DELAY_SECONDS, max_retries=config.MAX_RETRIES)
    if store is not None:
        for r in results:
            if r.ok:
                store.save(r.candle_set)
            else:
                store.log_failure(provider.name, r.pair, r.timeframe, r.error)
    return results


def get_prices(provider: DataProvider, pairs: list[str], store: CandleStore | None
               ) -> tuple[list[PriceSnapshot], list[FetchResult]]:
    results = fetch_and_store(provider, pairs, ["M1"], PRICE_CANDLES, store)
    snapshots = [build_snapshot(r.candle_set, config.LIVE_MAX_AGE_MINUTES, config.DELAYED_MAX_AGE_MINUTES)
                 for r in results if r.ok]
    return snapshots, [r for r in results if not r.ok]


def indicator_count(timeframe: str) -> int | None:
    """Candles to fetch for indicators. D1 asks for everything (the source has only ~520),
    which avoids a needless 'fewer candles than requested' warning on every run."""
    return None if timeframe == "D1" else config.INDICATOR_HISTORY


def _fetch_indicator_history(provider: DataProvider, pairs: list[str], timeframes: list[str],
                             store: CandleStore | None) -> list[FetchResult]:
    """Fetch enough history for indicators, ordered pair by pair."""
    by_key: dict[tuple[str, str], FetchResult] = {}
    for i, tf in enumerate(timeframes):   # one call per timeframe, because D1 uses a different count
        if i > 0:
            time.sleep(config.REQUEST_DELAY_SECONDS)   # keep the usual pause between requests
        for r in fetch_and_store(provider, pairs, [tf], indicator_count(tf), store):
            by_key[(r.pair, r.timeframe)] = r
    return [by_key[(p, tf)] for p in pairs for tf in timeframes]


def _summarize(r: FetchResult) -> IndicatorSummary:
    return summarize_indicators(r.candle_set.candles, r.pair, r.timeframe, config.EMA_PERIODS,
                                config.RSI_PERIOD, config.ATR_PERIOD)


def _trend(r: FetchResult) -> TrendResult:
    return detect_trend(r.candle_set.candles, r.pair, r.timeframe, config.TREND_LOOKBACK, config.TREND_THRESHOLD)


def _structure(r: FetchResult) -> StructureResult:
    return analyse_structure(r.candle_set.candles, r.pair, r.timeframe, config.ATR_PERIOD)


def get_structure(provider: DataProvider, pairs: list[str], timeframes: list[str], store: CandleStore | None
                  ) -> tuple[list[tuple[StructureResult, str]], list[FetchResult]]:
    """Swing points and support/resistance per pair and timeframe, from fresh data."""
    results = _fetch_indicator_history(provider, pairs, timeframes, store)
    rows = [(_structure(r), data_status(r.candle_set)) for r in results if r.ok]
    return rows, [r for r in results if not r.ok]


def get_indicators(provider: DataProvider, pairs: list[str], timeframes: list[str], store: CandleStore | None
                   ) -> tuple[list[tuple[IndicatorSummary, str]], list[FetchResult]]:
    """Latest EMA/RSI/ATR per pair and timeframe, from fresh data. Returns (summary, data status) rows."""
    results = _fetch_indicator_history(provider, pairs, timeframes, store)
    rows = [(_summarize(r), data_status(r.candle_set)) for r in results if r.ok]
    return rows, [r for r in results if not r.ok]


@dataclass(frozen=True)
class MarketView:
    """Trend and indicators computed from ONE fetch per pair and timeframe (used by the monitor)."""
    trends: list[tuple[TrendResult, str]]
    indicators: list[tuple[IndicatorSummary, str]]
    failures: list[FetchResult]
    candle_sets: dict = field(default_factory=dict)   # (pair, tf) -> CandleSet, for the alert rules
    structures: list = field(default_factory=list)    # (StructureResult, status), v0.5


def get_market(provider: DataProvider, pairs: list[str], timeframes: list[str], store: CandleStore | None
               ) -> MarketView:
    """The trend rule only uses the last 50 closed candles, so the longer indicator history
    gives exactly the same trend result, and saves a second download."""
    results = _fetch_indicator_history(provider, pairs, timeframes, store)
    ok = [r for r in results if r.ok]
    return MarketView(
        trends=[(_trend(r), data_status(r.candle_set)) for r in ok],
        indicators=[(_summarize(r), data_status(r.candle_set)) for r in ok],
        failures=[r for r in results if not r.ok],
        candle_sets={(r.pair, r.timeframe): r.candle_set for r in ok},
        structures=[(_structure(r), data_status(r.candle_set)) for r in ok],
    )


def get_trends(provider: DataProvider, pairs: list[str], timeframes: list[str], store: CandleStore | None
               ) -> tuple[list[tuple[TrendResult, str]], list[FetchResult]]:
    """Always fresh data: a trend from old stored candles could be mistaken for a current one."""
    results = fetch_and_store(provider, pairs, timeframes, config.TREND_LOOKBACK + 50, store)
    rows = [(_trend(r), data_status(r.candle_set)) for r in results if r.ok]
    return rows, [r for r in results if not r.ok]
