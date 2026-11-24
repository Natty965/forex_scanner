"""Technical indicators: EMA, RSI and ATR, written out by hand with pandas.

All three are built on one idea, a *seeded smoothed average*:
  1. Start (the "seed") with a plain average of the first `period` values.
  2. After that, each new value updates the average:
         new_average = weight * new_value + (1 - weight) * previous_average
     A larger weight reacts faster to new prices.

  EMA uses weight 2 / (period + 1). RSI and ATR use J. Welles Wilder's original
  weight 1 / period, which is smoother.

Rules for callers:
  - Pass CLOSED candles only. A still-forming candle makes values jump around.
  - Inputs must not contain missing values (a ValueError is raised); our providers
    already drop such candles.
  - Values are NaN until there is enough data for the first seed. Early values are
    also less accurate, because the seed still matters. Use `warmup_candles()` and
    `drop_warmup()` (below) to hide values that are not yet trustworthy.

Values can differ slightly from charting platforms: they use other price feeds and
sometimes other seeding methods. The method here is the classic textbook one.
"""

import math

import numpy as np
import pandas as pd

# ---------- warm-up: when is a value trustworthy? ----------
#
# The seed's influence on later values shrinks by (1 - weight) every candle, so after
# k candles it is about e^(-k * weight). We wait until k * weight >= 8, i.e. the seed's
# influence is below e^-8 ~ 0.03%.
#
# Checked on real data (Milestone 2.2, Nov 2026: EUR/USD, GBP/USD, USD/JPY on M15 and H1,
# about 400 end points each compared against the longest history available). With this
# rule the worst error found was below: EMA 1% of ATR, RSI 0.1 points, ATR 0.5%.
# EMA 200 is the slowest: it needs ~1,000 candles (800 was not enough).
WARMUP_TIME_CONSTANTS = 8

WEIGHTS = {
    "ema": lambda period: 2 / (period + 1),
    "rsi": lambda period: 1 / period,
    "atr": lambda period: 1 / period,
}


def warmup_candles(kind: str, period: int) -> int:
    """Closed candles needed before `kind` ('ema', 'rsi' or 'atr') with `period` is trustworthy."""
    if kind not in WEIGHTS:
        raise ValueError(f"unknown indicator {kind!r}; expected one of {', '.join(WEIGHTS)}")
    seed = period if kind == "ema" else period + 1   # RSI and ATR need `period` price changes
    return seed + math.ceil(WARMUP_TIME_CONSTANTS / WEIGHTS[kind](period))


def drop_warmup(values: pd.Series, required: int) -> pd.Series:
    """Set values to NaN until `required` candles of history exist behind them.

    The value at position i (0-based) is based on i + 1 candles.
    """
    out = values.copy()
    out.iloc[: max(required - 1, 0)] = np.nan
    return out


def _check(series: pd.Series, period: int, name: str) -> None:
    if not isinstance(period, int) or period < 1:
        raise ValueError(f"{name}: period must be a positive whole number, got {period!r}")
    if series.isna().any():
        raise ValueError(f"{name}: input contains missing values; pass clean, closed candles only")


def _seeded_average(values: pd.Series, period: int, weight: float) -> pd.Series:
    """Plain average of the first `period` values, then exponential smoothing with `weight`.

    `values` may start with NaN (e.g. the first price change has no previous price);
    the seed starts at the first real value.
    """
    out = pd.Series(np.nan, index=values.index, dtype=float)
    positions = np.flatnonzero(values.notna().to_numpy())
    if len(positions) < period:
        return out                       # not enough data: no value at all, not a guess
    seed_pos = int(positions[period - 1])
    seeded = values.iloc[seed_pos:].astype(float).copy()
    seeded.iloc[0] = values.iloc[positions[:period]].mean()
    # adjust=False gives exactly: new = weight * value + (1 - weight) * previous
    out.iloc[seed_pos:] = seeded.ewm(alpha=weight, adjust=False).mean().to_numpy()
    return out


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential moving average of closing prices.

    The first value appears at the `period`-th candle (a plain average of the first
    `period` closes); after that, weight = 2 / (period + 1).
    """
    _check(close, period, "ema")
    return _seeded_average(close, period, 2 / (period + 1))


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder), from 0 to 100.

    1. Price change per candle: up-moves count as gains, down-moves as losses.
    2. Average gain and average loss: plain average of the first `period` changes,
       then Wilder smoothing (weight 1 / period).
    3. RSI = 100 - 100 / (1 + average gain / average loss).

    Special cases: no losses -> 100, no gains -> 0, no movement at all -> NaN (undefined).
    The first value appears at candle number period + 1 (it needs `period` changes).
    """
    _check(close, period, "rsi")
    change = close.astype(float).diff()          # first value NaN: no previous close
    gains = change.clip(lower=0)
    losses = (-change).clip(lower=0)
    gains[change.isna()] = np.nan
    losses[change.isna()] = np.nan

    avg_gain = _seeded_average(gains, period, 1 / period)
    avg_loss = _seeded_average(losses, period, 1 / period)

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        values = 100 - 100 / (1 + rs)
    values = values.where(avg_loss != 0, 100.0)                         # only gains
    values = values.where(~((avg_gain == 0) & (avg_loss == 0)), np.nan)  # no movement
    values[avg_gain.isna()] = np.nan                                     # not enough data
    return values.rename(None)


def true_range(candles: pd.DataFrame) -> pd.Series:
    """How far price really travelled in each candle, including any gap from the previous close.

    TR = largest of: high - low, |high - previous close|, |low - previous close|.
    The first candle has no previous close, so its TR is NaN (not guessed).
    """
    prev_close = candles["close"].astype(float).shift(1)
    high = candles["high"].astype(float)
    low = candles["low"].astype(float)
    ranges = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    tr = ranges.max(axis=1)
    tr[prev_close.isna()] = np.nan
    return tr


def atr(candles: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder): the typical size of a candle's move, in price units.

    Plain average of the first `period` true ranges, then Wilder smoothing (weight 1 / period).
    The first value appears at candle number period + 1. ATR says how much price moves,
    not in which direction.
    """
    for col in ("high", "low", "close"):
        _check(candles[col], period, "atr")
    return _seeded_average(true_range(candles), period, 1 / period)
