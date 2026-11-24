"""The worker: the ONLY part of the app that fetches market data on a schedule.

Each cycle:
  1. fetch 1-minute candles (prices) and M15/H1/D1 history (trend + indicators), via scan.py
  2. save candles and the latest analysis to SQLite (analysis_latest)
  3. detect changes since the last cycle and save them (market_events), together with
     the tracker's memory (event_state), so a restart still notices changes
  4. record a heartbeat (worker_status), which also stops a second worker from starting

The dashboard API (U2) and later Telegram only READ what the worker writes.

Pacing (unchanged from v0.1/v0.2 monitor):
  - market open: every `interval` (default 5 min, never less than 2 min)
  - market closed: every `closed_interval` (default 30 min)
  - every request in a cycle failed: wait twice as long each time, up to 30 min
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from forex_scanner import config
from forex_scanner.analysis.snapshot import PriceSnapshot
from forex_scanner.events import EventTracker, MarketEvent
from forex_scanner.fetcher import FetchResult
from forex_scanner.market import is_market_closed
from forex_scanner.providers import DataProvider
from forex_scanner.scan import MarketView, get_market, get_prices
from forex_scanner.storage import AnalysisRecord, CandleStore
from forex_scanner.storage.records import jsonable

MIN_INTERVAL = timedelta(minutes=2)
MAX_BACKOFF = timedelta(minutes=30)


def next_wait(interval: timedelta, closed_interval: timedelta, market_closed: bool,
              consecutive_total_failures: int) -> timedelta:
    """How long to wait before the next cycle."""
    if consecutive_total_failures > 0:
        return min(interval * 2 ** consecutive_total_failures, MAX_BACKOFF)
    return closed_interval if market_closed else interval


@dataclass(frozen=True)
class CycleResult:
    at: datetime
    snapshots: list[PriceSnapshot]
    price_failures: list[FetchResult]
    view: MarketView
    events: list[MarketEvent]

    @property
    def ok_count(self) -> int:
        return len(self.snapshots) + len(self.view.trends)

    @property
    def failure_count(self) -> int:
        return len(self.price_failures) + len(self.view.failures)


@dataclass(frozen=True)
class CycleInfo:
    """What the loop hands to its display after each cycle."""
    result: CycleResult
    number: int                   # 1, 2, 3...
    wait: timedelta               # until the next cycle
    market_closed: bool
    total_failures: int           # consecutive cycles in which every request failed
    last: bool                    # True if no further cycle will run
    requests_per_cycle: int
    alerts: tuple = ()            # alerts recorded this cycle (dicts)
    alert_error: str | None = None


def analysis_records(result: CycleResult) -> list[AnalysisRecord]:
    """Turn one cycle's results into analysis_latest rows."""
    recs = [AnalysisRecord(s.pair, "M1", "price", s.price_time, s.status, result.at, jsonable(s))
            for s in result.snapshots]
    recs += [AnalysisRecord(t.pair, t.timeframe, "trend", t.last_candle_time, status, result.at, jsonable(t))
             for t, status in result.view.trends]
    recs += [AnalysisRecord(s.pair, s.timeframe, "indicators", s.last_candle_time, status, result.at, jsonable(s))
             for s, status in result.view.indicators]
    return recs


def run_cycle(provider: DataProvider, pairs: list[str], timeframes: list[str], store: CandleStore | None,
              tracker: EventTracker, now: Callable[[], datetime]) -> CycleResult:
    """One fetch -> save -> analyse -> detect-changes pass."""
    snapshots, price_failures = get_prices(provider, pairs, store)
    view = get_market(provider, pairs, timeframes, store)
    at = now()
    events = tracker.update(at, [t for t, _ in view.trends], [s for s, _ in view.indicators])
    result = CycleResult(at, snapshots, price_failures, view, events)
    if store is not None:
        store.save_analysis(provider.name, analysis_records(result))
        store.save_events(provider.name, events)
        store.save_event_states(provider.name, tracker.states, at)
    return result


def run_alerts(store: CandleStore, result: CycleResult, total_failures: int,
               previous_last_cycle: datetime | None, interval: timedelta) -> tuple[list, str | None]:
    """Run the alert engine on one cycle, then send what is pending (if Telegram is set up).
    An alert or sending problem never stops data collection: it is returned as text and
    shown in the worker's output instead."""
    from forex_scanner.alerts.engine import AlertEngine, AlertInputs
    from forex_scanner.alerts.sender import deliver_pending
    from forex_scanner.alerts.telegram import TelegramClient, TelegramConfig, mask

    try:
        inputs = AlertInputs.from_cycle(result, total_failures, previous_last_cycle, interval)
        engine = AlertEngine(store)
        recorded = engine.process(inputs)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    cfg = TelegramConfig.from_env()
    if cfg is None:
        return recorded, None
    try:
        sent = deliver_pending(store, TelegramClient(cfg.token), cfg.chat_id, result.at, engine.settings)
        by_id = {r["id"]: r for r in sent}
        recorded = [{**a, "delivery": by_id[a["id"]]["outcome"]} if a.get("id") in by_id else a for a in recorded]
        problems = [r["note"] for r in sent if r["outcome"] in ("retrying", "failed")]
        return recorded, ("Telegram: " + problems[0]) if problems else None
    except Exception as exc:
        return recorded, mask(f"Telegram sender: {type(exc).__name__}: {exc}", cfg.token)


def run_loop(
    provider: DataProvider,
    pairs: list[str],
    timeframes: list[str],
    interval: timedelta,
    closed_interval: timedelta,
    db_path=None,
    cycles: int | None = None,
    on_cycle: Callable[[CycleInfo], None] = lambda info: None,
    on_stop: Callable[[int], None] = lambda done: None,
    on_start: Callable[[], None] = lambda: None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    force: bool = False,
    alerts: bool = True,
) -> int:
    """Run cycles until Ctrl+C (or `cycles` cycles). Returns the number completed.

    With a database: claims the worker lock first (raises WorkerBusy if another worker is
    running), restores the event tracker's memory, runs the alert engine after each cycle,
    and releases the lock when it stops.
    """
    interval = max(interval, MIN_INTERVAL)
    requests_per_cycle = len(pairs) * (1 + len(timeframes))
    tracker = EventTracker(cross=tuple(config.EMA_CROSS))

    previous_last_cycle = None       # for the "worker was off" alert
    if db_path is not None:
        with CandleStore(db_path) as store:
            before = store.worker_status()
            previous_last_cycle = before["last_cycle_at"] if before else None
            store.claim_worker(os.getpid(), now(), force=force)
            tracker.states = store.load_event_states(provider.name)
    on_start()                       # only after the lock is ours

    total_failures = 0
    done = 0
    stop_message = "stopped"
    try:
        while cycles is None or done < cycles:
            store = CandleStore(db_path) if db_path is not None else None
            try:
                result = run_cycle(provider, pairs, timeframes, store, tracker, now)
                done += 1
                total_failures = total_failures + 1 if result.ok_count == 0 else 0
                closed = is_market_closed(result.at)
                wait = next_wait(interval, closed_interval, closed, total_failures)
                last = cycles is not None and done >= cycles
                recorded, alert_error = (), None
                if store is not None:
                    note = (f"{result.ok_count} ok, {result.failure_count} failed"
                            + (f", backing off ({total_failures} failed cycles)" if total_failures else ""))
                    store.worker_heartbeat(result.at, done, None if last else result.at + wait, note)
                    if alerts:
                        recorded, alert_error = run_alerts(
                            store, result, total_failures, previous_last_cycle if done == 1 else None, interval)
            finally:
                if store is not None:
                    store.close()

            on_cycle(CycleInfo(result, done, wait, closed, total_failures, last, requests_per_cycle,
                               tuple(recorded), alert_error))
            if last:
                break
            sleep(wait.total_seconds())
    except KeyboardInterrupt:
        stop_message = "stopped with Ctrl+C"
        on_stop(done)
    except BaseException as exc:
        stop_message = f"stopped by an error: {exc}"
        raise
    finally:
        if db_path is not None:
            with CandleStore(db_path) as store:
                store.release_worker(now(), stop_message)
    return done


def print_cycle_line(info: CycleInfo, print_fn: Callable[[str], None] = print) -> None:
    """Compact output for the `worker` command: one line per cycle, plus any events."""
    r = info.result
    state = ("ALL REQUESTS FAILED, backing off" if info.total_failures
             else "market closed" if info.market_closed else "market open")
    line = (f"{r.at:%Y-%m-%d %H:%M:%S} UTC  cycle {info.number}: prices {len(r.snapshots)} ok"
            f"/{len(r.price_failures)} failed, analysis {len(r.view.trends)} ok/{len(r.view.failures)} failed, "
            f"{len(r.events)} event(s), {state}")
    if not info.last:
        line += f". Next at {r.at + info.wait:%H:%M:%S} UTC"
    print_fn(line)
    for e in r.events:
        print_fn(f"    event  {e.pair} {e.timeframe}: {e.message}")
    for f in r.price_failures + r.view.failures:
        print_fn(f"    FAILED {f.pair} {f.timeframe}: {f.error}")
    for a in info.alerts:
        print_fn(f"    alert  {a['title']}  [{a['delivery']}]")
    if info.alert_error:
        print_fn(f"    ALERT ENGINE ERROR (data collection continues): {info.alert_error}")
