"""Indicators table (EMA distances, EMA alignment, RSI, ATR) plus the plain-English reasons."""

from rich import box
from rich.console import Console
from rich.table import Table

from forex_scanner.analysis.indicator_summary import (
    MIXED,
    NA,
    RSI_HIGH,
    RSI_LOW,
    STACKED_DOWN,
    STACKED_UP,
    IndicatorSummary,
)
from forex_scanner.analysis.trend import DATA_OK, DATA_STALE
from forex_scanner.display.formatting import candle_label, price_digits
from forex_scanner.fetcher import FetchResult

ALIGN_STYLE = {STACKED_UP: "green", STACKED_DOWN: "red", MIXED: "yellow", NA: "dim"}


def _distance(d: float | None) -> str:
    if d is None:
        return "[dim]n/a[/]"
    color = "green" if d > 0 else "red" if d < 0 else "white"
    return f"[{color}]{d:+.1f}[/]"


def _rsi(s: IndicatorSummary) -> str:
    if s.rsi is None:
        return "[dim]n/a[/]"
    # Zones in a neutral colour: HIGH/LOW are conditions, not "sell"/"buy".
    zone = f" [cyan]{s.rsi_zone}[/]" if s.rsi_zone in (RSI_HIGH, RSI_LOW) else ""
    return f"{s.rsi:.1f}{zone}"


def _data(status: str) -> str:
    if status == DATA_OK:
        return f"[green]{status}[/]"
    return f"[bold red]{status}[/]" if status == DATA_STALE else f"[cyan]{status}[/]"


def print_indicators(rows: list[tuple[IndicatorSummary, str]], failures: list[FetchResult], source: str,
                     ema_periods: list[int], explain: bool = True, console: Console | None = None) -> None:
    """rows: (summary, data status) pairs."""
    console = console or Console()
    table = Table(title="Indicators (times in UTC)", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("Pair")
    table.add_column("TF")
    table.add_column("Close", justify="right")
    for p in ema_periods:
        table.add_column(f"vs EMA{p}", justify="right")
    for col, justify in [("EMAs", "left"), ("RSI", "right"), ("ATR", "right"), ("Up to candle", "left"),
                         ("Data", "left")]:
        table.add_column(col, justify=justify)

    for s, status in rows:
        close = "-" if s.close is None else f"{s.close:.{price_digits(s.pair)}f}"
        atr_cell = "[dim]n/a[/]" if s.atr_pips is None else f"{s.atr_pips:.1f}"
        when = candle_label(s.last_candle_time, s.timeframe)
        table.add_row(s.pair, s.timeframe, close, *(_distance(s.distance_pips(p)) for p in ema_periods),
                      f"[{ALIGN_STYLE[s.alignment]}]{s.alignment}[/]", _rsi(s), atr_cell, when, _data(status))
    for f in failures:
        table.add_row(f.pair, f.timeframe, "-", *["-"] * len(ema_periods), "[bold red]FAILED[/]",
                      "-", "-", "-", "-")
    console.print(table)

    console.print(f"Source: {source}. Closed candles only. 'vs EMA' = close minus EMA, in pips. "
                  "ATR = typical candle size, in pips.")
    console.print("n/a = not enough history for a reliable value (see README: indicator warm-up).")
    console.print("[bold]These describe past prices. They are not predictions, signals or advice.[/]")
    if any(status != DATA_OK for _, status in rows):
        console.print("[yellow]Rows not marked OK are based on data that is not current.[/]")

    if explain:
        for s, _ in rows:
            suffix = f" (as of the {candle_label(s.last_candle_time, s.timeframe, long=True)})" \
                if s.last_candle_time else ""
            console.print(f"\n[bold]{s.pair} {s.timeframe}[/]{suffix}")
            for r in s.reasons:
                console.print(f"  - {r}")
    for f in failures:
        console.print(f"[bold red]FAILED {f.pair} {f.timeframe}[/] after {f.attempts} attempt(s): {f.error}")
        console.print("  No indicators shown, because there is no fresh data.")
