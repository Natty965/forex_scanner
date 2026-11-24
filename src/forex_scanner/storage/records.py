"""Convert analysis results to JSON for the database, and back again.

The worker writes these; the dashboard API (U2) reads them back as the same objects the
CLI uses, so both sides share one definition of every value.
"""

import dataclasses
import math
from datetime import datetime, timezone
from typing import Any

from forex_scanner.analysis.indicator_summary import IndicatorSummary
from forex_scanner.analysis.snapshot import Change, PriceSnapshot
from forex_scanner.analysis.trend import TrendResult
from forex_scanner.events import ItemState


def jsonable(obj: Any) -> Any:
    """Dataclasses, datetimes, dicts and lists -> plain JSON values. NaN becomes None."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, datetime):
        return obj.astimezone(timezone.utc).isoformat()
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, float):
        return None if math.isnan(obj) else float(obj)     # also turns numpy floats into plain floats
    return obj


def _dt(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text).astimezone(timezone.utc)


def _change(d: dict | None) -> Change | None:
    if d is None:
        return None
    return Change(d["reference_price"], _dt(d["reference_time"]), d["pips"], d["percent"], d["spans_gap"])


def snapshot_from(d: dict) -> PriceSnapshot:
    return PriceSnapshot(d["pair"], d["source"], d["price"], _dt(d["price_time"]), _dt(d["fetched_at"]),
                         d["status"], _change(d["change_1h"]), _change(d["change_24h"]))


def trend_from(d: dict) -> TrendResult:
    return TrendResult(d["pair"], d["timeframe"], d["direction"], d["candles_used"], _dt(d["last_candle_time"]),
                       d["move_ranges"], d["move_pips"], d["last_close"], d["average"], d["distance_pips"],
                       list(d["reasons"]))


def indicators_from(d: dict) -> IndicatorSummary:
    return IndicatorSummary(d["pair"], d["timeframe"], d["closed_candles"], _dt(d["last_candle_time"]),
                            d["close"], {int(k): v for k, v in d["emas"].items()}, d["rsi"], d["atr"],
                            d["alignment"], d["rsi_zone"], list(d["reasons"]))


def state_from(d: dict) -> ItemState:
    return ItemState(d["trend"], d["ema_side"], d["rsi_zone"], d["rsi"])
