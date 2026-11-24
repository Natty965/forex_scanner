"""Market events: what changed for a pair and timeframe since the previous check.

Four kinds:
  trend        the trend label changed (e.g. SIDEWAYS -> DOWN)
  ema_cross    the short EMA crossed the long EMA (EMA 20 vs EMA 50 by default)
  rsi_zone     RSI moved into or out of its HIGH (>= 70) or LOW (<= 30) zone
  level_break  a newly closed candle closed beyond a support/resistance level (v0.5 B4)

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
from forex_scanner.analysis.structure import StructureResult
from forex_scanner.analysis.trend import UNKNOWN, TrendResult
from forex_scanner.market import pip_size

TREND, EMA_CROSS, RSI_ZONE, LEVEL_BREAK = "trend", "ema_cross", "rsi_zone", "level_break"
# Level breaks (tuned on Nov 2026 data: about 3-4 H1 events per pair per day, ~1 D1 per pair per week)
BREAK_MARGIN_ATR = 0.25    # the close must be this x ATR beyond the zone edge (a touch isn't a break)
BREAK_MIN_TOUCHES = 3      # only levels where price turned at least this often
BREAK_CAME_FROM = 5        # price must have closed on the other side within this many candles
ABOVE, BELOW = 1, -1


@dataclass(frozen=True)
class MarketEvent:
    at: datetime
    pair: str
    timeframe: str
    kind: str        # TREND / EMA_CROSS / RSI_ZONE / LEVEL_BREAK
    message: str


@dataclass(frozen=True)
class ItemState:
    """What we last knew about one pair and timeframe. None = not known (yet)."""
    trend: str | None = None
    ema_side: int | None = None     # ABOVE = short EMA above long EMA, BELOW = below
    rsi_zone: str | None = None
    rsi: float | None = None
    # support/resistance memory (B4): the zones and close of the last analysed candle
    levels: tuple[tuple[float, float, int], ...] | None = None    # (zone low, zone high, touches)
    levels_closes: tuple[float, ...] = ()      # the last BREAK_CAME_FROM closes, oldest first
    levels_as_of: datetime | None = None


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


def _zone(low: float, high: float, pair: str) -> str:
    digits = 3 if pip_size(pair) >= 0.01 else 5
    lo, hi = f"{low:.{digits}f}", f"{high:.{digits}f}"
    return lo if lo == hi else f"{lo}-{hi}"


def level_breaks(prev: ItemState, s: StructureResult) -> tuple[ItemState, list[str]]:
    """Compare a NEW closed candle with the levels known before it. Returns (new state, messages).

    Break up (resistance): the new close is above the zone top by BREAK_MARGIN_ATR x ATR, the
    previous close wasn't, and price closed BELOW the zone within the last BREAK_CAME_FROM candles
    (it really crossed the zone, rather than wobbling inside it). Break down (support): the mirror
    image. Only levels with BREAK_MIN_TOUCHES+ touches, known BEFORE this candle (a level can't be
    broken by the candle that created it). At most one message per direction per candle: the
    strongest level, plus how many more were crossed.
    """
    if s.atr is None or s.close is None or s.last_candle_time is None:
        return prev, []                                   # nothing reliable: keep the old memory
    if prev.levels_as_of is not None and s.last_candle_time <= prev.levels_as_of:
        return prev, []                                   # no new candle since the last check
    messages: list[str] = []
    closes, close, margin = prev.levels_closes, s.close, BREAK_MARGIN_ATR * s.atr
    if prev.levels and closes:
        up, down = [], []
        for low, high, touches in prev.levels:
            if touches < BREAK_MIN_TOUCHES:
                continue
            if closes[-1] <= high + margin < close and min(closes) < low:
                up.append((touches, low, high))
            elif closes[-1] >= low - margin > close and max(closes) > high:
                down.append((touches, low, high))
        for found, words in ((up, "Closed above resistance"), (down, "Closed below support")):
            if found:
                touches, low, high = max(found)
                more = f", and {len(found) - 1} more level(s)" if len(found) > 1 else ""
                messages.append(f"{words} {_zone(low, high, s.pair)} ({touches} touches{more})")
    zones = tuple((lv.low, lv.high, lv.touches) for lv in (*s.resistance, *s.at_price, *s.support))
    return replace(prev, levels=zones, levels_closes=(*closes, close)[-BREAK_CAME_FROM:],
                   levels_as_of=s.last_candle_time), messages


class EventTracker:
    """Remembers the last state of every pair and timeframe and reports what changed."""

    def __init__(self, cross: tuple[int, int] = (20, 50)):
        self.cross = cross
        self.states: dict[tuple[str, str], ItemState] = {}

    def update(self, at: datetime, trends: list[TrendResult], indicators: list[IndicatorSummary],
               structures: list[StructureResult] = ()) -> list[MarketEvent]:
        by_trend = {(t.pair, t.timeframe): t for t in trends}
        by_ind = {(s.pair, s.timeframe): s for s in indicators}
        by_struct = {(s.pair, s.timeframe): s for s in structures}
        events: list[MarketEvent] = []
        for key in sorted(set(by_trend) | set(by_ind) | set(by_struct)):
            curr = state_from(by_trend.get(key), by_ind.get(key), self.cross)
            prev = self.states.get(key)
            if prev is None:                       # first time we see it: baseline only
                state = curr
                if key in by_struct:
                    state, _ = level_breaks(state, by_struct[key])
                self.states[key] = state
                continue
            state, new = compare(prev, curr, at, key[0], key[1], self.cross)
            if key in by_struct:
                state, messages = level_breaks(state, by_struct[key])
                new += [MarketEvent(at, key[0], key[1], LEVEL_BREAK, m) for m in messages]
            self.states[key] = state
            events.extend(new)
        return events
