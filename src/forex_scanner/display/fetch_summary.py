"""Print one row per (pair, timeframe) fetch: what we got, how old it is, or why it failed."""

from rich import box
from rich.console import Console
from rich.table import Table

from forex_scanner.display.formatting import format_age, price_digits
from forex_scanner.fetcher import FetchResult
from forex_scanner.storage import SaveStats


def print_fetch_summary(
    results: list[FetchResult],
    saved: dict[tuple[str, str], SaveStats] | None = None,
    console: Console | None = None,
) -> None:
    console = console or Console()
    table = Table(title="Fetch summary (times in UTC)", box=box.SIMPLE_HEAD, pad_edge=False)
    cols = ["Pair", "TF", "Result", "Candles", "From", "Last candle", "Age", "Close"]
    if saved is not None:
        cols += ["New", "Revised"]
    right = {"Candles", "Close", "New", "Revised"}
    for col in cols:
        table.add_column(col, justify="right" if col in right else "left")

    for r in results:
        if not r.ok:
            table.add_row(r.pair, r.timeframe, "[red]FAILED[/]", *["-"] * (len(cols) - 3))
            continue
        cs = r.candle_set
        c = cs.candles
        cells = [
            r.pair, r.timeframe, "[yellow]OK*[/]" if cs.warnings else "[green]OK[/]", str(len(c)),
            f"{c.index[0]:%y-%m-%d %H:%M}", f"{cs.last_candle_time:%y-%m-%d %H:%M}",
            format_age(cs.age), f"{c['close'].iloc[-1]:.{price_digits(r.pair)}f}",
        ]
        if saved is not None:
            s = saved[(r.pair, r.timeframe)]
            cells += [str(s.inserted), str(s.revised)]
        table.add_row(*cells)
    console.print(table)

    ok = sum(r.ok for r in results)
    console.print(f"{ok} of {len(results)} succeeded. Source: "
                  f"{results[0].candle_set.source if ok else 'n/a'}. Age = time since the last candle closed.")
    if saved is not None:
        console.print("New = candles not stored before. Revised = stored candles whose prices changed.")
    for r in results:
        if not r.ok:
            console.print(f"[red]FAILED {r.pair} {r.timeframe}[/] after {r.attempts} attempt(s): {r.error}")
        elif r.candle_set.warnings:
            for w in r.candle_set.warnings:
                console.print(f"[yellow]* {r.pair} {r.timeframe}: {w}[/]")
