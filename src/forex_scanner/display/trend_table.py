"""Trend table plus the plain-English reasons behind each label."""

from rich import box
from rich.console import Console
from rich.table import Table

from forex_scanner.analysis.trend import DATA_OK, DOWN, SIDEWAYS, UNKNOWN, UP, TrendResult
from forex_scanner.display.formatting import candle_label
from forex_scanner.fetcher import FetchResult

TREND_STYLE = {UP: "green", DOWN: "red", SIDEWAYS: "yellow", UNKNOWN: "dim"}


def print_trends(rows: list[tuple[TrendResult, str]], failures: list[FetchResult], source: str,
                 explain: bool = True, console: Console | None = None) -> None:
    """rows: (trend result, data status) pairs."""
    console = console or Console()
    table = Table(title="Basic trend (times in UTC)", box=box.SIMPLE_HEAD, pad_edge=False)
    for col, justify in [("Pair", "left"), ("TF", "left"), ("Trend", "left"), ("Move", "right"),
                         ("vs average", "right"), ("Up to candle", "left"), ("Data", "left")]:
        table.add_column(col, justify=justify)

    for t, status in rows:
        move = "-" if t.move_pips is None else f"{t.move_pips:+.1f} pips ({t.move_ranges:+.1f}R)"
        dist = "-" if t.distance_pips is None else f"{t.distance_pips:+.1f} pips"
        when = candle_label(t.last_candle_time, t.timeframe)
        data = f"[green]{status}[/]" if status == DATA_OK else f"[bold red]{status}[/]" \
            if status == "STALE" else f"[cyan]{status}[/]"
        table.add_row(t.pair, t.timeframe, f"[{TREND_STYLE[t.direction]}]{t.direction}[/]", move, dist, when, data)
    for f in failures:
        table.add_row(f.pair, f.timeframe, "[bold red]FAILED[/]", "-", "-", "-", "-")
    console.print(table)

    console.print(f"Source: {source}. Move = best-fit line over the window; R = average candle ranges. "
                  "Only closed candles are used.")
    console.print("[bold]A trend label describes past prices. It is not a prediction or a trade signal.[/]")
    if any(status != DATA_OK for _, status in rows):
        console.print("[yellow]Rows not marked OK are based on data that is not current.[/]")

    if explain:
        for t, _ in rows:
            console.print(f"\n[bold]{t.pair} {t.timeframe}: {t.direction}[/]")
            for r in t.reasons:
                console.print(f"  - {r}")
    for f in failures:
        console.print(f"[bold red]FAILED {f.pair} {f.timeframe}[/] after {f.attempts} attempt(s): {f.error}")
        console.print("  No trend shown, because there is no fresh data.")
