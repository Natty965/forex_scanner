"""Latest EMA / RSI / ATR readings for one pair and timeframe, with plain-English reasons.

Uses CLOSED candles only, and only values that have finished warming up (see
`warmup_candles`). Anything else is reported as None ("n/a") with the reason.

These are descriptions of what price has done. They are not signals or advice.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from forex_scanner.analysis.indicators import atr, drop_warmup, ema, rsi, warmup_candles
from forex_scanner.market import pip_size

STACKED_UP = "STACKED UP"      # shorter EMAs above longer ones: prices have been rising
STACKED_DOWN = "STACKED DOWN"  # shorter EMAs below longer ones: prices have been falling
MIXED = "MIXED"
NA = "n/a"

RSI_HIGH, RSI_LOW, RSI_NEUTRAL = "HIGH", "LOW", "NEUTRAL"
RSI_HIGH_LEVEL, RSI_LOW_LEVEL = 70, 30


@dataclass(frozen=True)
class IndicatorSummary:
    pair: str
    timeframe: str
    closed_candles: int
    last_candle_time: datetime | None   # open time of the newest closed candle used
    close: float | None
    emas: dict[int, float | None]       # period -> value (None = not enough history)
    rsi: float | None
    atr: float | None
    alignment: str                      # STACKED UP / STACKED DOWN / MIXED / n/a
    rsi_zone: str                       # HIGH / LOW / NEUTRAL / n/a
    reasons: list[str] = field(default_factory=list)

    def distance_pips(self, period: int) -> float | None:
        """Close minus EMA, in pips (positive = price above the EMA)."""
        value = self.emas.get(period)
        if value is None or self.close is None:
            return None
        return (self.close - value) / pip_size(self.pair)

    @property
    def atr_pips(self) -> float | None:
        return None if self.atr is None else self.atr / pip_size(self.pair)


def _last(values: pd.Series) -> float | None:
    v = values.iloc[-1] if len(values) else float("nan")
    return None if pd.isna(v) else float(v)


# EMAs closer together than this (in pips) are treated as equal: a gap that small
# carries no meaning (and with flat prices the only "gap" is computer rounding).
ALIGNMENT_TOLERANCE_PIPS = 0.1


def _alignment(emas: dict[int, float | None], pip: float) -> tuple[str, list[int]]:
    """Compare available EMAs from shortest to longest period."""
    available = sorted(p for p, v in emas.items() if v is not None)
    if len(available) < 2:
        return NA, available
    vals = [emas[p] for p in available]
    tol = ALIGNMENT_TOLERANCE_PIPS * pip
    neighbours = list(zip(vals, vals[1:], strict=False))   # (shorter EMA, next longer EMA)
    if all(a - b > tol for a, b in neighbours):
        return STACKED_UP, available
    if all(b - a > tol for a, b in neighbours):
        return STACKED_DOWN, available
    return MIXED, available


def summarize_indicators(candles: pd.DataFrame, pair: str, timeframe: str,
                         ema_periods: list[int], rsi_period: int, atr_period: int) -> IndicatorSummary:
    closed = candles[candles["complete"]]
    n = len(closed)
    pip = pip_size(pair)
    reasons: list[str] = []
    if n == 0:
        return IndicatorSummary(pair, timeframe, 0, None, None, dict.fromkeys(ema_periods),
                                None, None, NA, NA, ["No closed candles to calculate from."])

    close = float(closed["close"].iloc[-1])
    last_time = closed.index[-1].to_pydatetime()

    def too_short(name: str, need: int) -> str:
        return f"{name}: n/a, needs {need} closed {timeframe} candles to be reliable, only {n} available."

    # --- EMAs ---
    emas: dict[int, float | None] = {}
    for p in ema_periods:
        need = warmup_candles("ema", p)
        emas[p] = _last(drop_warmup(ema(closed["close"], p), need))
        if emas[p] is None:
            reasons.append(too_short(f"EMA {p}", need))
        else:
            d = (close - emas[p]) / pip
            side = "above" if d > 0 else "below" if d < 0 else "exactly at"
            reasons.append(f"Price is {side} EMA {p} ({emas[p]:.{5 if pip < 0.01 else 3}f}) by {abs(d):.1f} pips.")

    alignment, used = _alignment(emas, pip)
    names = " > ".join(f"EMA {p}" for p in used)
    if alignment == STACKED_UP:
        reasons.append(f"EMAs are stacked upward ({names}): recent prices have averaged higher "
                       "than older ones, typical of a rising market.")
    elif alignment == STACKED_DOWN:
        reasons.append(f"EMAs are stacked downward ({names.replace('>', '<')}): recent prices have "
                       "averaged lower than older ones, typical of a falling market.")
    elif alignment == MIXED:
        reasons.append("EMAs are mixed (not in order, or practically equal): no consistent direction "
                       "across short and long averages.")
    missing = [p for p in ema_periods if emas[p] is None]
    if alignment != NA and missing:
        reasons.append(f"(Alignment uses {', '.join(f'EMA {p}' for p in used)} only; "
                       f"{', '.join(f'EMA {p}' for p in missing)} not available.)")

    # --- RSI ---
    need = warmup_candles("rsi", rsi_period)
    rsi_value = _last(drop_warmup(rsi(closed["close"], rsi_period), need))
    if rsi_value is None:
        rsi_zone = NA
        reasons.append(too_short(f"RSI {rsi_period}", need) if n < need
                       else f"RSI {rsi_period}: undefined, prices did not move.")
    elif rsi_value >= RSI_HIGH_LEVEL:
        rsi_zone = RSI_HIGH
        reasons.append(f"RSI {rsi_period} is {rsi_value:.1f}, at or above {RSI_HIGH_LEVEL} (often called "
                       "'overbought'): recent up-moves have been much larger than down-moves. This does not "
                       "mean price will fall; in strong trends RSI can stay high for a long time.")
    elif rsi_value <= RSI_LOW_LEVEL:
        rsi_zone = RSI_LOW
        reasons.append(f"RSI {rsi_period} is {rsi_value:.1f}, at or below {RSI_LOW_LEVEL} (often called "
                       "'oversold'): recent down-moves have been much larger than up-moves. This does not "
                       "mean price will rise; in strong trends RSI can stay low for a long time.")
    else:
        rsi_zone = RSI_NEUTRAL
        reasons.append(f"RSI {rsi_period} is {rsi_value:.1f}, between {RSI_LOW_LEVEL} and {RSI_HIGH_LEVEL}: "
                       "up- and down-moves have been fairly balanced.")

    # --- ATR ---
    need = warmup_candles("atr", atr_period)
    atr_value = _last(drop_warmup(atr(closed, atr_period), need))
    if atr_value is None:
        reasons.append(too_short(f"ATR {atr_period}", need))
    else:
        reasons.append(f"ATR {atr_period} is {atr_value / pip:.1f} pips: a typical {timeframe} candle has "
                       "moved about that far (high to low, including gaps).")

    return IndicatorSummary(pair, timeframe, n, last_time, close, emas, rsi_value, atr_value,
                            alignment, rsi_zone, reasons)
