"""Alert rules. Each looks at one cycle's results and returns candidate alerts.

A rule only says "this could be worth telling you, and here is why". Whether it is actually
recorded and sent (duplicates, cooldowns, daily caps, quiet hours, dry run) is the engine's job.

Safeguards built into the rules:
  - indicator-based alerts use CLOSED candles only, and only recent ones (no alerts about old data)
  - nothing about the market while it is closed or while the data isn't current
  - the first candle after a gap (the weekend, or a hole in the data) is skipped: its range
    includes the jump from the last price before the gap and would look like a huge move
  - RSI zones use hysteresis (in at 70, out only below 65), so RSI hovering near 70 doesn't flap
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from forex_scanner import config
from forex_scanner.analysis.indicator_summary import IndicatorSummary
from forex_scanner.analysis.indicators import atr, drop_warmup, warmup_candles
from forex_scanner.analysis.snapshot import LIVE, PriceSnapshot, status_at
from forex_scanner.analysis.trend import DATA_OK
from forex_scanner.events import EMA_CROSS, LEVEL_BREAK, TREND, MarketEvent
from forex_scanner.market import is_market_closed, pip_size
from forex_scanner.providers import CandleSet
from forex_scanner.providers.base import TIMEFRAME_DURATIONS

from .settings import AlertSettings

NOT_A_SIGNAL = "This describes what price did. It is not a signal."


@dataclass
class Candidate:
    key: str                      # identity: the same key is never recorded twice
    type: str                     # unusual_move / market_events / data
    severity: str                 # info / notice / warning
    title: str
    body: str
    pair: str | None = None
    timeframe: str | None = None
    candle_time: datetime | None = None
    data: dict = field(default_factory=dict)
    cooldown: tuple[str, int] | None = None   # (name, minutes): no other alert with this name for that long
    market: bool = True           # about the market (False = about the data/system)


def when(dt: datetime, s: AlertSettings) -> str:
    """'14:00 UTC (14:00 London)'."""
    return f"{dt.astimezone(timezone.utc):%H:%M} UTC ({dt.astimezone(s.tz):%H:%M} {s.place})"


def fmt_price(pair: str, v: float) -> str:
    return f"{v:.{3 if pair.endswith('JPY') else 5}f}"


def candle_is_recent(candle_open: datetime, timeframe: str, now: datetime) -> bool:
    """Did this candle close recently enough to be news (within one more period + 30 min)?"""
    dur = TIMEFRAME_DURATIONS[timeframe]
    return now - (candle_open + dur) <= dur + timedelta(minutes=30)


def is_rollover_candle(candle_open: datetime) -> bool:
    """Does this candle open at the Forex daily rollover (17:00 New York time)?"""
    ny = candle_open.astimezone(ZoneInfo("America/New_York"))
    return ny.hour == 17 and ny.minute == 0


# ---------- unusual moves ----------

def unusual_ranges(candle_sets: dict[tuple[str, str], CandleSet], statuses: dict[tuple[str, str], str],
                   now: datetime, s: AlertSettings) -> list[Candidate]:
    """A newly closed candle whose high-low range is at least `unusual_range_atr` x the ATR before it."""
    out: list[Candidate] = []
    if not s.unusual_enabled or is_market_closed(now):
        return out
    need = warmup_candles("atr", config.ATR_PERIOD) + 1
    for (pair, tf), cs in candle_sets.items():
        if tf not in s.unusual_timeframes or statuses.get((pair, tf)) != DATA_OK:
            continue
        closed = cs.candles[cs.candles["complete"]]
        if len(closed) < need:
            continue
        last_t, prev_t = closed.index[-1], closed.index[-2]
        dur = TIMEFRAME_DURATIONS[tf]
        if last_t - prev_t > dur * 1.5:
            continue                                   # first candle after a gap: skip
        if s.skip_rollover_candle and is_rollover_candle(last_t.to_pydatetime()):
            continue                                   # daily rollover candle: skip if you chose to
        if not candle_is_recent(last_t.to_pydatetime(), tf, now):
            continue
        atr_before = drop_warmup(atr(closed, config.ATR_PERIOD), need - 1).iloc[-2]
        if atr_before != atr_before or atr_before <= 0:   # NaN or zero
            continue
        row = closed.iloc[-1]
        rng = float(row["high"] - row["low"])
        ratio = rng / atr_before
        if ratio < s.unusual_range_atr:
            continue
        pip = pip_size(pair)
        up = row["close"] >= row["open"]
        close_at = last_t.to_pydatetime() + dur
        out.append(Candidate(
            key=f"unusual_range:{pair}:{tf}:{last_t:%Y%m%dT%H%M}", type="unusual_move", severity="notice",
            pair=pair, timeframe=tf, candle_time=last_t.to_pydatetime(),
            title=f"UNUSUAL MOVE · {pair} · {tf}",
            body=(f"Candle closed {when(close_at, s)}: range {rng / pip:.1f} pips = {ratio:.1f}x normal "
                  f"(ATR {atr_before / pip:.1f} pips)\n"
                  f"Direction: {'up' if up else 'down'} · close {fmt_price(pair, float(row['close']))}\n"
                  f"Why you got this: range above your {s.unusual_range_atr:g}x ATR threshold.\n"
                  f"What it means: price is moving more than usual; spreads can widen.\n{NOT_A_SIGNAL}"),
            data={"range_pips": rng / pip, "atr_pips": atr_before / pip, "ratio": ratio,
                  "direction": "up" if up else "down", "close": float(row["close"]), "threshold": s.unusual_range_atr},
            cooldown=(f"unusual_range:{pair}:{tf}", s.unusual_cooldown_minutes),
        ))
    return out


def live_moves(snapshots: list[PriceSnapshot], h1_atr_pips: dict[str, float], now: datetime,
               s: AlertSettings) -> list[Candidate]:
    """Price moved at least `live_move_atr` x the hourly ATR within the last 60 minutes (live price)."""
    out: list[Candidate] = []
    if not (s.unusual_enabled and s.live_move_enabled) or is_market_closed(now):
        return out
    for snap in snapshots:
        status, _ = status_at(snap, now, config.LIVE_MAX_AGE_MINUTES, config.DELAYED_MAX_AGE_MINUTES)
        ch, atr_pips = snap.change_1h, h1_atr_pips.get(snap.pair)
        if status != LIVE or ch is None or ch.spans_gap or not atr_pips:
            continue                                   # only from live prices, never across a gap
        ratio = abs(ch.pips) / atr_pips
        if ratio < s.live_move_atr:
            continue
        out.append(Candidate(
            key=f"live_move:{snap.pair}:{snap.price_time:%Y%m%dT%H%M}", type="unusual_move", severity="notice",
            pair=snap.pair, timeframe="60 min",
            title=f"FAST MOVE · {snap.pair} · last 60 min",
            body=(f"Price {'rose' if ch.pips > 0 else 'fell'} {abs(ch.pips):.1f} pips in the last hour = "
                  f"{ratio:.1f}x the typical hourly move (H1 ATR {atr_pips:.1f} pips)\n"
                  f"Now {fmt_price(snap.pair, snap.price)} · LIVE price at {when(snap.price_time, s)}\n"
                  f"Why you got this: above your {s.live_move_atr:g}x threshold.\n{NOT_A_SIGNAL}"),
            data={"pips": ch.pips, "atr_pips": atr_pips, "ratio": ratio, "price": snap.price,
                  "threshold": s.live_move_atr},
            cooldown=(f"live_move:{snap.pair}", s.unusual_cooldown_minutes),
        ))
    return out


# ---------- market events (batched) ----------

def rsi_zone_with_hysteresis(previous: str | None, rsi: float, s: AlertSettings) -> str:
    """HIGH / LOW / NEUTRAL. Entering needs 70 (30); leaving needs below 65 (above 35)."""
    if previous == "HIGH" and rsi >= s.rsi_high_exit:
        return "HIGH"
    if previous == "LOW" and rsi <= s.rsi_low_exit:
        return "LOW"
    if rsi >= s.rsi_high:
        return "HIGH"
    if rsi <= s.rsi_low:
        return "LOW"
    return "NEUTRAL"


def market_events(events: list[MarketEvent], indicators: list[tuple[IndicatorSummary, str]],
                  rsi_zones: dict[str, str], now: datetime, s: AlertSettings) -> tuple[Candidate | None, dict]:
    """One batched alert with this cycle's trend changes, EMA crossings, level breaks and RSI zone changes.

    Returns (candidate or None, updated RSI zone memory). The first time a pair/timeframe is
    seen only sets its starting zone, without an alert.
    """
    zones = dict(rsi_zones)
    if not s.events_enabled:
        return None, zones
    lines = [f"{e.pair} {e.timeframe}: {e.message}" for e in events
             if (e.kind in (TREND, EMA_CROSS) and e.timeframe in s.events_timeframes)
             or (e.kind == LEVEL_BREAK and e.timeframe in s.level_break_timeframes)]
    for ind, status in indicators:
        if ind.timeframe not in s.events_timeframes or status != DATA_OK or ind.rsi is None:
            continue
        key = f"{ind.pair}|{ind.timeframe}"
        before = zones.get(key)
        after = rsi_zone_with_hysteresis(before, ind.rsi, s)
        zones[key] = after
        if before is None or after == before:
            continue
        names = {"HIGH": f"HIGH zone ({s.rsi_high:g}+)", "LOW": f"LOW zone ({s.rsi_low:g} or less)"}
        if after in names and before in names:
            text = f"RSI jumped from the {names[before]} to the {names[after]}"
        elif after in names:
            text = f"RSI entered the {names[after]}"
        else:
            text = f"RSI left the {names[before]}"
        lines.append(f"{ind.pair} {ind.timeframe}: {text} ({ind.rsi:.1f})")
    if not lines or is_market_closed(now):
        return None, zones
    body = "\n".join(lines) + "\nDescriptions of past prices, not signals."
    return Candidate(
        key=f"market_events:{now:%Y%m%dT%H%M%S}", type="market_events", severity="info",
        title=f"MARKET EVENTS · {when(now, s)}", body=body, data={"lines": lines},
    ), zones


# ---------- data and system ----------

def data_problem(total_failures: int, ok_count: int, state: dict, now: datetime,
                 s: AlertSettings) -> tuple[Candidate | None, dict]:
    """Alert once when every request has failed for `data_fail_cycles` cycles, and once on recovery."""
    st = dict(state) if state else {"active": False, "since": None}
    if not s.data_enabled:
        return None, st
    if ok_count == 0:
        st["since"] = st.get("since") or now.isoformat()
        if not st["active"] and total_failures >= s.data_fail_cycles:
            st["active"] = True
            since = datetime.fromisoformat(st["since"])
            return Candidate(
                key=f"data_problem:{since:%Y%m%dT%H%M%S}", type="data", severity="warning", market=False,
                title="DATA PROBLEM",
                body=(f"The data source has failed for {total_failures} cycles in a row (since {when(since, s)}).\n"
                      "No market alerts will be sent until fresh data returns. "
                      "Prices on the dashboard will turn STALE."),
                data={"failed_cycles": total_failures, "since": st["since"]},
            ), st
        return None, st
    if st["active"]:
        since = datetime.fromisoformat(st["since"])
        st = {"active": False, "since": None}
        return Candidate(
            key=f"data_recovered:{now:%Y%m%dT%H%M%S}", type="data", severity="info", market=False,
            title="DATA RECOVERED",
            body=f"Fresh data is arriving again ({when(now, s)}). The problem started {when(since, s)}.",
            data={"since": since.isoformat()},
        ), st
    return None, {"active": False, "since": None}


def downtime(previous_last_cycle: datetime | None, now: datetime, interval: timedelta,
             s: AlertSettings) -> Candidate | None:
    """The worker was off for a while (computer asleep, window closed): say so instead of pretending
    nothing happened. Gaps entirely inside the weekend closure don't count."""
    if previous_last_cycle is None:
        return None
    gap = now - previous_last_cycle
    if gap <= interval * 2 + timedelta(minutes=3):
        return None
    if is_market_closed(previous_last_cycle) and is_market_closed(now) and gap < timedelta(days=2):
        return None
    hours, minutes = divmod(int(gap.total_seconds() // 60), 60)
    return Candidate(
        key=f"downtime:{previous_last_cycle:%Y%m%dT%H%M%S}", type="data", severity="info", market=False,
        title="WORKER WAS OFF",
        body=(f"No data was collected from {when(previous_last_cycle, s)} to {when(now, s)} "
              f"({hours} h {minutes} min). Nothing was checked for alerts in that time."),
        data={"from": previous_last_cycle.isoformat(), "to": now.isoformat()},
    )
