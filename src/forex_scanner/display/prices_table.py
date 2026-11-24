"""Current prices table: price, 1h and 24h change, and a freshness status for every pair."""

from datetime import datetime

from rich import box
from rich.console import Console
from rich.table import Table

from forex_scanner.analysis.snapshot import DELAYED, LIVE, MARKET_CLOSED, STALE, Change, PriceSnapshot
from forex_scanner.display.formatting import format_age, price_digits
from forex_scanner.fetcher import FetchResult
from forex_scanner.market import next_open

STATUS_STYLE = {LIVE: "green", DELAYED: "yellow", STALE: "bold red", MARKET_CLOSED: "cyan"}


def _change_cell(ch: Change | None) -> str:
    if ch is None:
        return "n/a"
    color = "green" if ch.pips > 0 else "red" if ch.pips < 0 else "white"
    mark = "*" if ch.spans_gap else ""
    return f"[{color}]{ch.pips:+.1f} pips ({ch.percent:+.2f}%){mark}[/]"


def print_prices(snapshots: list[PriceSnapshot], failures: list[FetchResult],
                 live_max: float, delayed_max: float, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title="Current prices (times in UTC)", box=box.SIMPLE_HEAD, pad_edge=False)
    for col, justify in [("Pair", "left"), ("Price", "right"), ("1h change", "right"),
                         ("24h change", "right"), ("Price time", "left"), ("Age", "right"), ("Status", "left")]:
        table.add_column(col, justify=justify)

    for s in snapshots:
        style = STATUS_STYLE[s.status]
        table.add_row(
            s.pair, f"{s.price:.{price_digits(s.pair)}f}", _change_cell(s.change_1h), _change_cell(s.change_24h),
            f"{s.price_time:%m-%d %H:%M}", format_age(s.age), f"[{style}]{s.status}[/]",
        )
    for f in failures:
        table.add_row(f.pair, "-", "-", "-", "-", "-", "[bold red]FAILED[/]")
    console.print(table)

    if snapshots:
        fetched: datetime = max(s.fetched_at for s in snapshots)
        console.print(f"Source: {snapshots[0].source} (unofficial, free). Fetched {fetched:%Y-%m-%d %H:%M:%S} UTC.")
        console.print("Prices are indicative mid quotes, not prices you could trade at.")
        if any(s.status == MARKET_CLOSED for s in snapshots):
            console.print(f"[cyan]Market closed: prices are the last ones before the close. "
                          f"Reopens {next_open(fetched):%a %Y-%m-%d %H:%M} UTC.[/]")
        if any(s.status in (DELAYED, STALE) for s in snapshots):
            console.print("[yellow]Some prices are not live: the source has not updated recently.[/]")
        if any(ch is not None and ch.spans_gap for s in snapshots for ch in (s.change_1h, s.change_24h)):
            console.print("* no price at that exact time, so compared with the last earlier price "
                          "(e.g. before the weekend, or before a gap in the data).")
    console.print(f"Status: LIVE = price at most {live_max:g} min old, DELAYED = up to {delayed_max:g} min, "
                  "STALE = older while the market is open.")
    for f in failures:
        console.print(f"[bold red]FAILED {f.pair}[/] after {f.attempts} attempt(s): {f.error}")
        console.print("  No price shown for this pair, because there is no fresh data.")
