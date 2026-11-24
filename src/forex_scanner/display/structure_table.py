"""Market structure table: swing structure, nearest support/resistance, with the reasons."""

from rich import box
from rich.console import Console
from rich.table import Table

from forex_scanner.analysis.structure import FALLING, MIXED, NA, RISING, Level, StructureResult
from forex_scanner.analysis.trend import DATA_OK, DATA_STALE
from forex_scanner.display.formatting import candle_label, price_digits
from forex_scanner.fetcher import FetchResult

STRUCTURE_STYLE = {RISING: "green", FALLING: "red", MIXED: "yellow", NA: "dim"}


def _level(lv: Level | None, pair: str) -> str:
    if lv is None:
        return "[dim]none found[/]"
    d = price_digits(pair)
    zone = f"{lv.price:.{d}f}" if lv.high - lv.low < 10 ** -d * 5 else f"{lv.low:.{d}f}-{lv.high:.{d}f}"
    return f"{zone} [dim]({lv.touches}x, {lv.distance_pips:.0f} pips)[/]"


def _data(status: str) -> str:
    if status == DATA_OK:
        return f"[green]{status}[/]"
    return f"[bold red]{status}[/]" if status == DATA_STALE else f"[cyan]{status}[/]"


def print_structure(rows: list[tuple[StructureResult, str]], failures: list[FetchResult], source: str,
                    explain: bool = True, console: Console | None = None) -> None:
    console = console or Console()
    table = Table(title="Market structure (times in UTC)", box=box.SIMPLE_HEAD, pad_edge=False)
    for col, justify in [("Pair", "left"), ("TF", "left"), ("Close", "right"), ("Structure", "left"),
                         ("Swings", "left"), ("Nearest resistance", "left"), ("Nearest support", "left"),
                         ("At price", "left"), ("Up to candle", "left"), ("Data", "left")]:
        table.add_column(col, justify=justify)
    for s, status in rows:
        close = "-" if s.close is None else f"{s.close:.{price_digits(s.pair)}f}"
        labels = " ".join(x.label for x in s.swings[-4:] if x.label) or "-"
        at = f"{len(s.at_price)} level(s)" if s.at_price else "-"
        table.add_row(s.pair, s.timeframe, close, f"[{STRUCTURE_STYLE[s.structure]}]{s.structure}[/]", labels,
                      _level(s.resistance[0] if s.resistance else None, s.pair),
                      _level(s.support[0] if s.support else None, s.pair), at,
                      candle_label(s.last_candle_time, s.timeframe), _data(status))
    for f in failures:
        table.add_row(f.pair, f.timeframe, "-", "[bold red]FAILED[/]", "-", "-", "-", "-", "-", "-")
    console.print(table)
    console.print(f"Source: {source}. Closed candles only. Swings: HH/LH = higher/lower high, HL/LL = higher/lower "
                  "low. Levels: (touches, distance from the close).")
    console.print("[bold]Levels show where price turned before. They are not predictions, signals or advice.[/]")
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
        console.print("  No structure shown, because there is no fresh data.")
