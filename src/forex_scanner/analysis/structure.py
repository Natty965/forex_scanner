"""Market structure: swing highs/lows and support/resistance levels, from CLOSED candles.

Swing high = a closed candle whose high is above the highs of the SWING_STRENGTH candles before
it and not below the ones after it (swing low: the mirror image). It is confirmed only once those
later candles have closed, so a swing never appears and then disappears ("repaints"); the cost
is that the newest swing is always at least SWING_STRENGTH candles old.

Structure = the last two swing highs and the last two swing lows:
  higher highs and higher lows -> RISING,  lower highs and lower lows -> FALLING,  else MIXED.

Levels = places where price turned more than once. Swing points (highs AND lows: old resistance
often becomes support) within the last STRUCTURE_LOOKBACK candles that lie within
LEVEL_CLUSTER_ATR x ATR of each other form one zone; a zone needs MIN_TOUCHES swings. Zones above
the close are "resistance", below are "support", and one that the close is in (or very near) is
"at price".

These describe where price turned in the past. They are not predictions, signals or advice:
levels often break.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from forex_scanner.analysis.indicators import atr, drop_warmup, warmup_candles
from forex_scanner.market import pip_size

SWING_STRENGTH = 3          # candles on each side
STRUCTURE_LOOKBACK = 300    # closed candles searched for swings and levels
LEVEL_CLUSTER_ATR = 0.5     # swings within this x ATR of each other form one level
AT_PRICE_ATR = 0.25         # a level within this x ATR of the close counts as "at price"
MIN_TOUCHES = 2
MAX_LEVELS = 3              # per side
RECENT_SWINGS = 8           # swings kept in the result (for display)

HIGH, LOW = "HIGH", "LOW"
RISING, FALLING, MIXED, NA = "RISING", "FALLING", "MIXED", "n/a"
SUPPORT, RESISTANCE, AT_PRICE = "SUPPORT", "RESISTANCE", "AT PRICE"


@dataclass(frozen=True)
class SwingPoint:
    time: datetime              # open time of the swing candle
    kind: str                   # HIGH / LOW
    price: float
    label: str | None = None    # HH / LH / EH (equal) for highs, HL / LL / EL for lows; None = first one


@dataclass(frozen=True)
class Level:
    side: str                   # SUPPORT / RESISTANCE / AT PRICE
    price: float                # average of the swing prices in the zone
    low: float                  # zone bottom
    high: float                 # zone top
    touches: int
    first_time: datetime
    last_time: datetime
    distance_pips: float        # from the close to the nearest edge of the zone (0 = at price)
    distance_atr: float


@dataclass(frozen=True)
class StructureResult:
    pair: str
    timeframe: str
    last_candle_time: datetime | None
    close: float | None
    atr: float | None
    candles_used: int
    structure: str              # RISING / FALLING / MIXED / n/a
    swings: list[SwingPoint]    # the most recent RECENT_SWINGS, oldest first
    last_high: SwingPoint | None
    last_low: SwingPoint | None
    resistance: list[Level]     # nearest first
    support: list[Level]        # nearest first
    at_price: list[Level]
    reasons: list[str] = field(default_factory=list)


def find_swings(closed: pd.DataFrame, strength: int = SWING_STRENGTH) -> list[SwingPoint]:
    """Confirmed swing highs and lows, oldest first. `closed` = closed candles only."""
    highs, lows = closed["high"].to_numpy(float), closed["low"].to_numpy(float)
    times = closed.index
    out: list[SwingPoint] = []
    for i in range(strength, len(closed) - strength):
        before_h, after_h = highs[i - strength:i], highs[i + 1:i + strength + 1]
        if highs[i] > before_h.max() and highs[i] >= after_h.max():
            out.append(SwingPoint(times[i].to_pydatetime(), HIGH, float(highs[i])))
        before_l, after_l = lows[i - strength:i], lows[i + 1:i + strength + 1]
        if lows[i] < before_l.min() and lows[i] <= after_l.min():
            out.append(SwingPoint(times[i].to_pydatetime(), LOW, float(lows[i])))
    return out


def label_swings(swings: list[SwingPoint], tolerance: float) -> list[SwingPoint]:
    """Compare each swing with the previous one of the same kind: HH/LH/EH, HL/LL/EL.
    Within `tolerance` (price units) counts as equal."""
    out, prev = [], {HIGH: None, LOW: None}
    for s in swings:
        p = prev[s.kind]
        label = None
        if p is not None:
            d = s.price - p.price
            letter = "H" if s.kind == HIGH else "L"
            label = ("E" if abs(d) <= tolerance else "H" if d > 0 else "L") + letter
        out.append(SwingPoint(s.time, s.kind, s.price, label))
        prev[s.kind] = s
    return out


def classify(swings: list[SwingPoint]) -> str:
    """RISING if the last high is HH and the last low HL; FALLING if LH and LL; else MIXED."""
    last = {k: next((s for s in reversed(swings) if s.kind == k), None) for k in (HIGH, LOW)}
    if last[HIGH] is None or last[LOW] is None or last[HIGH].label is None or last[LOW].label is None:
        return NA
    if last[HIGH].label == "HH" and last[LOW].label == "HL":
        return RISING
    if last[HIGH].label == "LH" and last[LOW].label == "LL":
        return FALLING
    return MIXED


def cluster_levels(swings: list[SwingPoint], width: float, min_touches: int = MIN_TOUCHES) -> list[list[SwingPoint]]:
    """Group swing points by price: a zone grows while the next price is within `width` of its bottom."""
    groups: list[list[SwingPoint]] = []
    for s in sorted(swings, key=lambda x: x.price):
        if groups and s.price - groups[-1][0].price <= width:
            groups[-1].append(s)
        else:
            groups.append([s])
    return [g for g in groups if len(g) >= min_touches]


def _levels(groups: list[list[SwingPoint]], close: float, atr_value: float, pip: float
            ) -> tuple[list[Level], list[Level], list[Level]]:
    res, sup, at = [], [], []
    near = AT_PRICE_ATR * atr_value
    for g in groups:
        low, high = min(s.price for s in g), max(s.price for s in g)
        if low - near <= close <= high + near:
            side, dist = AT_PRICE, 0.0
        elif low > close:
            side, dist = RESISTANCE, low - close
        else:
            side, dist = SUPPORT, close - high
        times = [s.time for s in g]
        lvl = Level(side, sum(s.price for s in g) / len(g), low, high, len(g), min(times), max(times),
                    dist / pip, dist / atr_value)
        {RESISTANCE: res, SUPPORT: sup, AT_PRICE: at}[side].append(lvl)
    def by_distance(lv: Level) -> float:
        return lv.distance_pips

    return sorted(res, key=by_distance)[:MAX_LEVELS], sorted(sup, key=by_distance)[:MAX_LEVELS], at


def analyse_structure(candles: pd.DataFrame, pair: str, timeframe: str, atr_period: int = 14,
                      lookback: int = STRUCTURE_LOOKBACK, strength: int = SWING_STRENGTH) -> StructureResult:
    closed_all = candles[candles["complete"].astype(bool)]
    pip = pip_size(pair)
    digits = 3 if pip >= 0.01 else 5

    def empty(reason: str, closed_n: int = len(closed_all), close=None, last=None) -> StructureResult:
        return StructureResult(pair, timeframe, last, close, None, closed_n, NA, [], None, None, [], [], [],
                               [reason])

    if closed_all.empty:
        return empty("No closed candles to analyse.")
    close = float(closed_all["close"].iloc[-1])
    last_time = closed_all.index[-1].to_pydatetime()
    need_atr = warmup_candles("atr", atr_period)
    atr_series = drop_warmup(atr(closed_all, atr_period), need_atr) if len(closed_all) > atr_period else None
    atr_value = None if atr_series is None or pd.isna(atr_series.iloc[-1]) else float(atr_series.iloc[-1])
    if atr_value is None or atr_value <= 0:
        return empty(f"Levels: n/a, needs ATR, which needs {need_atr} closed {timeframe} candles "
                     f"(only {len(closed_all)}).", close=close, last=last_time)

    closed = closed_all.tail(lookback)
    tolerance = 0.1 * atr_value                                 # "equal" highs/lows
    swings = label_swings(find_swings(closed, strength), tolerance)
    structure = classify(swings)
    last_high = next((s for s in reversed(swings) if s.kind == HIGH), None)
    last_low = next((s for s in reversed(swings) if s.kind == LOW), None)
    groups = cluster_levels(swings, LEVEL_CLUSTER_ATR * atr_value)
    resistance, support, at_price = _levels(groups, close, atr_value, pip)

    def fmt(v: float) -> str:
        return f"{v:.{digits}f}"

    def zone(lv: Level) -> str:
        lo, hi = fmt(lv.low), fmt(lv.high)
        return lo if lo == hi else f"{lo}-{hi}"

    reasons = [f"Searched the last {len(closed)} closed {timeframe} candles: {len(swings)} swing points "
               f"(a swing high needs {strength} lower highs on each side, a swing low {strength} higher lows, "
               f"so the newest one is at least {strength} candles old)."]
    if structure == RISING:
        reasons.append("Structure RISING: the last swing high was higher than the one before (HH) and the last "
                       "swing low was higher too (HL). Typical of an up-move; it says nothing about what comes next.")
    elif structure == FALLING:
        reasons.append("Structure FALLING: the last swing high was lower than the one before (LH) and the last "
                       "swing low was lower too (LL). Typical of a down-move; it says nothing about what comes next.")
    elif structure == MIXED:
        reasons.append(f"Structure MIXED: last high {last_high.label}, last low {last_low.label}, so highs and lows "
                       "don't agree on a direction (often a range, or a turn in progress).")
    else:
        reasons.append("Structure n/a: needs at least two swing highs and two swing lows.")
    if not groups:
        reasons.append(f"No levels: no price area was turned at {MIN_TOUCHES}+ times in this period.")
    if groups and not resistance:
        reasons.append("No resistance above: price is above every level found in this period "
                       "(at or near its highest area).")
    if groups and not support:
        reasons.append("No support below: price is below every level found in this period "
                       "(at or near its lowest area).")
    for lv in at_price:
        reasons.append(f"Price is AT a level {zone(lv)} ({lv.touches} touches).")
    for name, lst in (("Resistance", resistance), ("Support", support)):
        for lv in lst:
            reasons.append(f"{name} {zone(lv)}: {lv.touches} touches, {lv.distance_pips:.1f} pips "
                           f"away ({lv.distance_atr:.1f} x ATR).")
    reasons.append("Levels show where price turned before. Price often breaks through them: not a signal.")
    return StructureResult(pair, timeframe, last_time, close, atr_value, len(closed), structure,
                           swings[-RECENT_SWINGS:], last_high, last_low, resistance, support, at_price, reasons)
