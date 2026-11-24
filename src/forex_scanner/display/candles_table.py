"""Print a CandleSet as a terminal table, with source and freshness always visible."""

from rich.console import Console
from rich.table import Table

from forex_scanner.display.formatting import format_age, price_digits
from forex_scanner.providers import CandleSet


def print_candles(cs: CandleSet, console: Console | None = None) -> None:
    console = console or Console()
    digits = price_digits(cs.pair)

    table = Table(title=f"{cs.pair}  {cs.timeframe}  (last {len(cs.candles)} candles)")
    table.add_column("Open time (UTC)")
    for name in ("Open", "High", "Low", "Close"):
        table.add_column(name, justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("Status")

    for ts, row in cs.candles.iterrows():
        table.add_row(
            ts.strftime("%Y-%m-%d %H:%M"),
            *(f"{row[c]:.{digits}f}" for c in ("open", "high", "low", "close")),
            "n/a" if not cs.volume_available else f"{row['volume']:.0f}",
            "closed" if row["complete"] else "[yellow]forming[/]",
        )
    console.print(table)

    console.print(f"Source      : {cs.source}  (unofficial, free, may be delayed)")
    console.print(f"Fetched at  : {cs.fetched_at:%Y-%m-%d %H:%M:%S} UTC")
    console.print(f"Last candle : {cs.last_candle_time:%Y-%m-%d %H:%M} UTC "
                  f"[bold]({format_age(cs.age)} old when fetched)[/]")
    if not cs.volume_available:
        console.print("Volume      : not available from this source (shown as n/a, not 0)")
    for w in cs.warnings:
        console.print(f"[yellow]Warning     : {w}[/]")
