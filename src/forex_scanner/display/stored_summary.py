"""Show what the local database holds, and whether the latest fetch for each item worked."""

from pathlib import Path

import pandas as pd
from rich import box
from rich.console import Console
from rich.table import Table


def _short(ts_text) -> str:
    return "-" if ts_text is None or pd.isna(ts_text) else pd.Timestamp(ts_text).strftime("%y-%m-%d %H:%M")


def print_stored_summary(summary: pd.DataFrame, db_path: Path, console: Console | None = None) -> None:
    console = console or Console()
    if summary.empty:
        console.print(f"Database {db_path} has no fetches recorded yet.")
        return

    table = Table(title="Stored candles (times in UTC)", box=box.SIMPLE_HEAD, pad_edge=False)
    for col in ("Source", "Pair", "TF", "Candles", "First", "Last", "Last good fetch", "Last attempt"):
        table.add_column(col, justify="right" if col == "Candles" else "left")

    for row in summary.itertuples():
        attempt = "[green]OK[/]" if row.last_attempt_ok == 1 else "[red]FAILED[/]" \
            if row.last_attempt_ok == 0 else "-"
        table.add_row(row.source, row.pair, row.timeframe, str(row.candles),
                      _short(row.first_candle), _short(row.last_candle),
                      _short(row.last_ok_fetch), f"{attempt} {_short(row.last_attempt)}")
    console.print(table)
    console.print(f"Database: {db_path}")
    console.print("This is stored history, not live prices. Check 'Last good fetch' for how recent it is.")
    for row in summary.itertuples():
        if row.last_attempt_ok == 0:
            console.print(f"[red]Latest fetch FAILED for {row.pair} {row.timeframe}:[/] {row.last_error}")
