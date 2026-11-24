"""Market events: what changed for a pair and timeframe since the previous check.

Three kinds:
  trend      the trend label changed (e.g. SIDEWAYS -> DOWN)
  ema_cross  the short EMA crossed the long EMA (EMA 20 vs EMA 50 by default)
  rsi_zone   RSI moved into or out of its HIGH (>= 70) or LOW (<= 30) zone

Rules:
  - The first check only sets a baseline; events need a previous state to compare with.
  - Missing information (a failed fetch, an "n/a" value, EMAs practically equal) is never a
    change: the last known state is kept until real data replaces it. So an EMA that wobbles
    within 0.1 pip of the other does not produce a stream of crossings.

Events describe what happened in past prices. They are not signals or advice, and nothing
here sends anything anywhere; that is for a later alerts module.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from forex_scanner.analysis.indicator_summary import (
    ALIGNMENT_TOLERANCE_PIPS,
    NA,
    RSI_HIGH,
    RSI_LOW,
    IndicatorSummary,
)
from forex_scanner.analysis.trend import UNKNOWN, TrendResult
from forex_scanner.market import pip_size

TREND, EMA_CROSS, RSI_ZONE = "trend", "ema_cross", "rsi_zone"
ABOVE, BELOW = 1, -1


@dataclass(frozen=True)
class MarketEvent:
    at: datetime
    pair: str
    timeframe: str
    kind: str        # TREND / EMA_CROSS / RSI_ZONE
    message: str


@dataclass(frozen=True)
class ItemState:
    """What we last knew about one pair and timeframe. None = not known (yet)."""
    trend: str | None = None
    ema_side: int | None = None     # ABOVE = short EMA above long EMA, BELOW = below
    rsi_zone: str | None = None
    rsi: float | None = None


def state_from(trend: TrendResult | None, ind: IndicatorSummary | None, cross: tuple[int, int]) -> ItemState:
    """Turn one cycle's results into a state. Unknown parts become None."""
    trend_label = None if trend is None or trend.direction == UNKNOWN else trend.direction
    ema_side = rsi_zone = rsi_value = None
    if ind is not None:
        short, long_ = ind.emas.get(cross[0]), ind.emas.get(cross[1])
        if short is not None and long_ is not None:
            gap = (short - long_) / pip_size(ind.pair)
            if abs(gap) > ALIGNMENT_TOLERANCE_PIPS:
                ema_side = ABOVE if gap > 0 else BELOW
        if ind.rsi_zone != NA:
            rsi_zone, rsi_value = ind.rsi_zone, ind.rsi
    return ItemState(trend_label, ema_side, rsi_zone, rsi_value)


def _rsi_message(before: str, after: str, value: float | None) -> str:
    v = f" ({value:.1f})" if value is not None else ""
    names = {RSI_HIGH: "HIGH zone (70+)", RSI_LOW: "LOW zone (30 or less)"}
    if after in names and before in names:
        return f"RSI jumped from the {names[before]} to the {names[after]}{v}"
    if after in names:
        return f"RSI entered the {names[after]}{v}"
    return f"RSI left the {names[before]}{v}"


def compare(prev: ItemState, curr: ItemState, at: datetime, pair: str, timeframe: str,
            cross: tuple[int, int]) -> tuple[ItemState, list[MarketEvent]]:
    """Return (new remembered state, events). Unknown parts of `curr` keep the previous value."""
    events: list[MarketEvent] = []

    def add(kind: str, message: str) -> None:
        events.append(MarketEvent(at, pair, timeframe, kind, message))

    if curr.trend is not None and prev.trend is not None and curr.trend != prev.trend:
        add(TREND, f"Trend {prev.trend} -> {curr.trend}")
    if curr.ema_side is not None and prev.ema_side is not None and curr.ema_side != prev.ema_side:
        direction = "above" if curr.ema_side == ABOVE else "below"
        add(EMA_CROSS, f"EMA {cross[0]} crossed {direction} EMA {cross[1]}")
    if curr.rsi_zone is not None and prev.rsi_zone is not None and curr.rsi_zone != prev.rsi_zone:
        add(RSI_ZONE, _rsi_message(prev.rsi_zone, curr.rsi_zone, curr.rsi))

    remembered = replace(
        prev,
        trend=curr.trend if curr.trend is not None else prev.trend,
        ema_side=curr.ema_side if curr.ema_side is not None else prev.ema_side,
        rsi_zone=curr.rsi_zone if curr.rsi_zone is not None else prev.rsi_zone,
        rsi=curr.rsi if curr.rsi_zone is not None else prev.rsi,
    )
    return remembered, events


class EventTracker:
    """Remembers the last state of every pair and timeframe and reports what changed."""

    def __init__(self, cross: tuple[int, int] = (20, 50)):
        self.cross = cross
        self.states: dict[tuple[str, str], ItemState] = {}

    def update(self, at: datetime, trends: list[TrendResult], indicators: list[IndicatorSummary]
               ) -> list[MarketEvent]:
        by_trend = {(t.pair, t.timeframe): t for t in trends}
        by_ind = {(s.pair, s.timeframe): s for s in indicators}
        events: list[MarketEvent] = []
        for key in sorted(set(by_trend) | set(by_ind)):
            curr = state_from(by_trend.get(key), by_ind.get(key), self.cross)
            prev = self.states.get(key)
            if prev is None:                       # first time we see it: baseline only
                self.states[key] = curr
                continue
            self.states[key], new = compare(prev, curr, at, key[0], key[1], self.cross)
            events.extend(new)
        return events
