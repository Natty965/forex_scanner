"""Trading conditions: when NOT to open new (demo) trades, and when to be careful.

These describe RISK, not opportunity. No warnings never means "good time to trade": the
scanner has no strategy yet (that is v0.6). Times follow New York, where the Forex day ends.

  AVOID    weekend close coming   Friday 16:00-17:00 New York: positions face the weekend gap
  AVOID    data not current       the price isn't LIVE while the market is open
  CAUTION  market just reopened   Sunday 17:00-18:00 New York: gaps, wide spreads
  CAUTION  daily rollover         16:45-17:15 New York: thin trading, wide spreads
  CAUTION  high volatility        a pair's recent hourly ranges are well above its normal
  INFO     market closed          the weekend

Volatility uses the plain high-to-low range of candles (not ATR), because ATR includes the jump
across the weekend gap and would make every Monday morning look "volatile".
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from forex_scanner.market import is_market_closed, next_open

from .rules import Candidate
from .settings import AlertSettings

NEW_YORK = ZoneInfo("America/New_York")
AVOID, CAUTION, INFO = "AVOID", "CAUTION", "INFO"

RECENT_CANDLES = 14          # "recent" = the last 14 closed H1 candles
NORMAL_CANDLES = 480         # "normal" = about 20 trading days of H1 candles


@dataclass(frozen=True)
class TradingCondition:
    kind: str                # weekend_close / weekly_open / rollover / high_volatility / stale_data / market_closed
    level: str               # AVOID / CAUTION / INFO
    pair: str | None
    text: str
    why: str
    data: dict | None = None


def _ny(when: datetime) -> datetime:
    return when.astimezone(NEW_YORK)


def time_conditions(now: datetime) -> list[TradingCondition]:
    """Warnings that depend only on the clock."""
    if is_market_closed(now):
        reopen = next_open(now)
        hours = (reopen - now).total_seconds() / 3600
        return [TradingCondition(
            "market_closed", INFO, None, f"Market closed (reopens in about {hours:.0f} h)",
            "Forex closes from Friday 17:00 to Sunday 17:00 New York time. Prices shown are the last ones "
            "before the close.")]
    ny = _ny(now)
    minutes = ny.hour * 60 + ny.minute
    out = []
    if ny.weekday() == 4 and 16 * 60 <= minutes < 17 * 60:
        left = 17 * 60 - minutes
        out.append(TradingCondition(
            "weekend_close", AVOID, None, f"Avoid new trades: the market closes for the weekend in {left} min",
            "A position held over the weekend can't be closed until Sunday evening, and price often "
            "jumps (gaps) at the reopen, past any stop-loss."))
    if ny.weekday() == 6 and 17 * 60 <= minutes < 18 * 60:
        out.append(TradingCondition(
            "weekly_open", CAUTION, None, "Caution: the market reopened less than an hour ago",
            "Right after the weekly open, prices can gap away from Friday's close, spreads are wide and "
            "few traders are active. (Our free data source also has no candles until 00:00 UTC.)"))
    elif ny.weekday() != 4 and 16 * 60 + 45 <= minutes < 17 * 60 + 15:
        out.append(TradingCondition(
            "rollover", CAUTION, None, "Caution: daily rollover (17:00 New York)",
            "Around the end of the Forex day, trading is thin and spreads widen, so prices can jump "
            "more than usual and trades cost more."))
    return out


def volatility_ratio(h1: pd.DataFrame) -> float | None:
    """Recent average high-low range of closed H1 candles, compared with its normal level.
    Returns e.g. 2.1 (= 2.1x normal), or None if there isn't enough history."""
    closed = h1[h1["complete"].astype(bool)]
    if len(closed) < RECENT_CANDLES + 50:
        return None
    rng = (closed["high"] - closed["low"]).astype(float)
    rolling = rng.rolling(RECENT_CANDLES).mean().dropna()
    normal = rolling.tail(NORMAL_CANDLES).median()
    if not normal or normal != normal:          # 0 or NaN
        return None
    return float(rolling.iloc[-1] / normal)


def volatility_conditions(h1_by_pair: dict[str, pd.DataFrame], threshold: float) -> list[TradingCondition]:
    out = []
    for pair, h1 in h1_by_pair.items():
        ratio = volatility_ratio(h1)
        if ratio is None or ratio < threshold:
            continue
        out.append(TradingCondition(
            "high_volatility", CAUTION, pair, f"Caution: {pair} is moving {ratio:.1f}x more than usual",
            f"Its hourly candles have recently been {ratio:.1f} times their normal size (last 20 days). "
            "Stops get hit more easily and moves can reverse sharply. Smaller positions or no trade are "
            "the usual responses.", {"ratio": ratio, "threshold": threshold}))
    return out


def data_conditions(price_status: dict[str, str], now: datetime) -> list[TradingCondition]:
    if is_market_closed(now):
        return []
    return [TradingCondition(
        "stale_data", AVOID, pair, f"Don't act on {pair} prices: they are {status}",
        "The data source hasn't delivered a fresh price recently, so what you see may not be the real "
        "current price.") for pair, status in price_status.items() if status in ("DELAYED", "STALE")]


def trading_conditions(now: datetime, h1_by_pair: dict[str, pd.DataFrame], price_status: dict[str, str],
                       volatility_threshold: float = 1.8) -> list[TradingCondition]:
    """All current warnings, most serious first."""
    found = time_conditions(now)
    if not is_market_closed(now):
        found += volatility_conditions(h1_by_pair, volatility_threshold) + data_conditions(price_status, now)
    order = {AVOID: 0, CAUTION: 1, INFO: 2}
    return sorted(found, key=lambda c: order[c.level])


def window_key(c: TradingCondition, now: datetime) -> str:
    """Identity for alerting once per occurrence: a scheduled window once per New York day.
    (Volatility alerts are limited by hysteresis instead, see caution_alerts.)"""
    return f"caution:{c.kind}:{c.pair or 'all'}:{_ny(now):%Y-%m-%d}"


TITLE = {AVOID: "AVOID NEW TRADES", CAUTION: "CAUTION", INFO: "INFO"}
REARM_FRACTION = 0.85    # a volatility warning re-arms only after the ratio falls below 0.85 x threshold


def caution_alerts(conditions: list[TradingCondition], h1_by_pair: dict[str, pd.DataFrame], armed: dict,
                   now: datetime, s: AlertSettings) -> tuple[list[Candidate], dict]:
    """Turn current conditions into caution alerts (only the kinds chosen in the settings).

    `armed` = {pair: bool} from the engine's memory. A pair's volatility warning is sent once,
    then stays quiet until the ratio has dropped clearly below the threshold (hysteresis), so a
    ratio hovering around 1.8 doesn't repeat. Returns (candidates, new armed state)."""
    new_armed = dict(armed)
    for pair, h1 in h1_by_pair.items():
        ratio = volatility_ratio(h1)
        if ratio is not None and ratio < s.volatility_threshold * REARM_FRACTION:
            new_armed[pair] = True
    out: list[Candidate] = []
    if not s.caution_enabled:
        return out, new_armed
    for c in conditions:
        if c.kind not in s.caution_kinds:
            continue
        if c.kind == "high_volatility":
            if not new_armed.get(c.pair, True):
                continue
            new_armed[c.pair] = False
            key = f"caution:high_volatility:{c.pair}:{now:%Y-%m-%dT%H:%M}"
        else:
            key = window_key(c, now)
        out.append(Candidate(
            key=key, type="caution", severity="warning" if c.level == AVOID else "notice",
            title=TITLE[c.level] + (f" · {c.pair}" if c.pair else ""), pair=c.pair,
            body=f"{c.text}.\nWhy: {c.why}\n{NOT_A_SIGNAL_CAUTION}",
            data={"kind": c.kind, "level": c.level, **(c.data or {})}))
    return out, new_armed


NOT_A_SIGNAL_CAUTION = "This is a risk warning, not a signal. No warning never means 'good time to trade'."
