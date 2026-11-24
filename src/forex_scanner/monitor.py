"""`monitor`: the worker loop (worker.py) with full terminal tables after each cycle.

It fetches, saves and detects events exactly like `worker` (and takes the same lock, so
the two can never run at the same time); it just shows much more on screen.
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from rich.console import Console

from forex_scanner import config
from forex_scanner.display.indicators_table import print_indicators
from forex_scanner.display.prices_table import print_prices
from forex_scanner.display.trend_table import print_trends
from forex_scanner.events import MarketEvent
from forex_scanner.market import next_open
from forex_scanner.providers import DataProvider
from forex_scanner.worker import MIN_INTERVAL, CycleInfo, next_wait, run_loop

__all__ = ["MIN_INTERVAL", "next_wait", "run_monitor"]

MAX_EVENTS_SHOWN = 15


def run_monitor(
    provider: DataProvider,
    pairs: list[str],
    timeframes: list[str],
    interval: timedelta,
    closed_interval: timedelta,
    live_max: float,
    delayed_max: float,
    db_path=None,
    cycles: int | None = None,
    console: Console | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    clear_screen: bool = True,
    force: bool = False,
) -> int:
    """Run until Ctrl+C (or for `cycles` cycles). Returns the number of cycles completed."""
    console = console or Console()
    session_events: list[MarketEvent] = []

    def show(info: CycleInfo) -> None:
        r = info.result
        session_events.extend(r.events)
        if clear_screen:
            console.clear()
        console.rule(f"[bold]Forex monitor[/]  cycle {info.number}  {r.at:%Y-%m-%d %H:%M:%S} UTC")
        print_prices(r.snapshots, r.price_failures, live_max, delayed_max, console=console)
        console.print()
        print_trends(r.view.trends, r.view.failures, provider.name, explain=False, console=console)
        console.print()
        print_indicators(r.view.indicators, r.view.failures, provider.name, config.EMA_PERIODS,
                         explain=False, console=console)

        console.print("\n[bold]Events this session[/] (changes since the previous cycle)")
        if session_events:
            for e in session_events[-MAX_EVENTS_SHOWN:]:
                console.print(f"  {e.at:%m-%d %H:%M} UTC  {e.pair} {e.timeframe}: {e.message}")
        elif info.number == 1:
            console.print("  none yet (the first cycle compares with the last saved state, if any)")
        else:
            console.print("  none yet")

        if info.total_failures:
            console.print(f"\n[bold red]Every request failed {info.total_failures} cycle(s) in a row. "
                          f"Backing off.[/]")
        elif info.market_closed:
            console.print(f"\n[cyan]Market closed. Checking every {closed_interval.seconds // 60} min. "
                          f"Reopens {next_open(r.at):%a %H:%M} UTC.[/]")
        if not info.last:
            console.print(f"\n{info.requests_per_cycle} requests per cycle. Next refresh at "
                          f"{r.at + info.wait:%H:%M:%S} UTC. Press Ctrl+C to stop.")

    return run_loop(provider, pairs, timeframes, interval, closed_interval, db_path=db_path, cycles=cycles,
                    on_cycle=show, on_stop=lambda done: console.print(f"\nStopped after {done} cycle(s)."),
                    sleep=sleep, now=now, force=force)
