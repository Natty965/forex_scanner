"""The alert engine: runs the rules after each worker cycle and decides what happens to each alert.

For every candidate alert, in order:
  1. same identity already recorded           -> ignored (duplicate)
  2. inside its cooldown                      -> ignored
  3. today's limit for this type reached      -> recorded as "suppressed" (so you can still see it)
  4. sending paused / quiet hours / dry run   -> recorded as "paused" / "held_quiet" / "dry_run"
  5. otherwise                                -> recorded as "pending" (sent by the Telegram sender, A4)

Its memory (cooldowns, RSI zones, data-problem state) lives in the alert_state table, so a
restart doesn't cause repeats.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from forex_scanner import config
from forex_scanner.analysis.snapshot import status_at
from forex_scanner.analysis.trend import DATA_OK
from forex_scanner.market import is_market_closed

from . import conditions as cond
from . import rules
from .brief import brief_due_at, compose_brief
from .rules import Candidate
from .settings import AlertSettings, load_settings


@dataclass
class AlertInputs:
    """What one worker cycle produced, in the form the rules need."""
    at: datetime
    candle_sets: dict = field(default_factory=dict)        # (pair, tf) -> CandleSet
    statuses: dict = field(default_factory=dict)           # (pair, tf) -> data status
    snapshots: list = field(default_factory=list)          # PriceSnapshot
    trends: list = field(default_factory=list)             # (TrendResult, status)
    indicators: list = field(default_factory=list)         # (IndicatorSummary, status)
    events: list = field(default_factory=list)             # MarketEvent from this cycle
    ok_count: int = 0
    total_failures: int = 0                                # consecutive cycles in which everything failed
    previous_last_cycle: datetime | None = None            # set on the first cycle after a (re)start
    interval: timedelta = timedelta(minutes=5)

    @classmethod
    def from_cycle(cls, result, total_failures: int, previous_last_cycle: datetime | None,
                   interval: timedelta) -> "AlertInputs":
        return cls(
            at=result.at,
            candle_sets=dict(result.view.candle_sets),
            statuses={(t.pair, t.timeframe): status for t, status in result.view.trends},
            snapshots=list(result.snapshots),
            trends=list(result.view.trends),
            indicators=list(result.view.indicators),
            events=list(result.events),
            ok_count=result.ok_count,
            total_failures=total_failures,
            previous_last_cycle=previous_last_cycle,
            interval=interval,
        )


class AlertEngine:
    def __init__(self, store, settings: AlertSettings | None = None):
        self.store = store
        self.settings = settings or load_settings(store)    # re-read each cycle: page edits apply at once

    def process(self, inp: AlertInputs) -> list[dict]:
        """Run every rule; record what passes. Returns the alerts recorded this cycle."""
        s, store = self.settings, self.store
        candidates: list[Candidate] = []

        down = rules.downtime(inp.previous_last_cycle, inp.at, inp.interval, s)
        if down:
            candidates.append(down)
        problem, data_state = rules.data_problem(inp.total_failures, inp.ok_count, store.get_state("data"), inp.at, s)
        store.set_state("data", data_state, inp.at)
        if problem:
            candidates.append(problem)

        if inp.ok_count > 0:                                 # market rules only with fresh data
            candidates += rules.unusual_ranges(inp.candle_sets, inp.statuses, inp.at, s)
            h1_atr = {ind.pair: ind.atr_pips for ind, status in inp.indicators
                      if ind.timeframe == "H1" and status == DATA_OK and ind.atr_pips}
            candidates += rules.live_moves(inp.snapshots, h1_atr, inp.at, s)
            batch, zones = rules.market_events(inp.events, inp.indicators, store.get_state("rsi_zones", {}), inp.at, s)
            store.set_state("rsi_zones", zones, inp.at)
            if batch:
                candidates.append(batch)

        # caution alerts: the clock-based warnings need no fresh data, volatility needs current H1
        found = self.conditions(inp)
        cautions, armed = cond.caution_alerts(found, self._h1(inp), store.get_state("volatility_armed", {}),
                                              inp.at, s)
        store.set_state("volatility_armed", armed, inp.at)
        candidates += cautions

        recorded = []
        for c in candidates:
            alert = self._record(c, inp.at)
            if alert:
                recorded.append(alert)
        brief = self._daily_brief(inp)
        if brief:
            recorded.append(brief)
        return recorded

    # ---------- trading conditions ----------

    @staticmethod
    def _h1(inp: AlertInputs) -> dict:
        """Current H1 candles per pair (only where the H1 data is OK)."""
        return {pair: cs.candles for (pair, tf), cs in inp.candle_sets.items()
                if tf == "H1" and inp.statuses.get((pair, tf)) == DATA_OK}

    def conditions(self, inp: AlertInputs) -> list:
        price_status = {snap.pair: status_at(snap, inp.at, config.LIVE_MAX_AGE_MINUTES,
                                             config.DELAYED_MAX_AGE_MINUTES)[0] for snap in inp.snapshots}
        return cond.trading_conditions(inp.at, self._h1(inp), price_status, self.settings.volatility_threshold)

    # ---------- daily brief ----------

    BRIEF_WINDOW = timedelta(hours=3)        # a late brief is still created within this time
    LATE_AFTER = timedelta(minutes=15)       # created later than this after the brief time = "late"

    def _daily_brief(self, inp: AlertInputs) -> dict | None:
        """Create today's brief once, at or after the brief time. Every day gets one brief_log row:
        created, created late, or skipped with the reason."""
        s, store, at = self.settings, self.store, inp.at
        local = at.astimezone(s.tz)
        day = f"{local:%Y-%m-%d}"
        due = brief_due_at(local, s)
        if at < due or store.get_brief_log(day):
            return None                                       # not yet, or already handled today
        if is_market_closed(at):
            store.set_brief_log(day, "skipped: market closed", at)
            return None
        late = at - due
        if late > self.BRIEF_WINDOW:
            store.set_brief_log(day, f"skipped: the worker wasn't running between {s.brief_time} and "
                                     f"{(due + self.BRIEF_WINDOW):%H:%M} {s.place}", at)
            return None
        if inp.ok_count == 0:
            return None                                       # no fresh data this cycle: try the next one
        held = [a for a in store.load_alerts(limit=500)
                if a["delivery"] == "held_quiet" and a["created_at"] >= due - timedelta(hours=24)]
        held.reverse()                                        # oldest first
        c = compose_brief(at, s, inp.snapshots, inp.trends, inp.indicators, held,
                          late_by=late if late > self.LATE_AFTER else None, conditions=self.conditions(inp))
        alert = self._record(c, at)
        store.set_brief_log(day, "created late" if late > self.LATE_AFTER else "created", at,
                            alert["id"] if alert else None)
        return alert

    def _record(self, c: Candidate, at: datetime) -> dict | None:
        store = self.store
        if store.alert_exists(c.key):
            return None                                       # duplicate
        cooldowns = store.get_state("cooldowns", {})
        if c.cooldown:
            name, minutes = c.cooldown
            last = cooldowns.get(name)
            if last and at - datetime.fromisoformat(last) < timedelta(minutes=minutes):
                return None                                   # still cooling down
        delivery, note = self._delivery(c, at)
        alert = {"key": c.key, "created_at": at, "type": c.type, "severity": c.severity, "pair": c.pair,
                 "timeframe": c.timeframe, "candle_time": c.candle_time, "title": c.title, "body": c.body,
                 "data": c.data, "delivery": delivery, "delivery_note": note}
        alert_id = store.add_alert(alert)
        if alert_id is None:
            return None
        if c.cooldown and delivery != "suppressed":
            cooldowns[c.cooldown[0]] = at.isoformat()
            store.set_state("cooldowns", cooldowns, at)
        return {**alert, "id": alert_id}

    def _delivery(self, c: Candidate, at: datetime) -> tuple[str, str | None]:
        s = self.settings
        cap = s.daily_caps.get(c.type)
        if cap is not None and self.store.count_alerts(c.type, since=s.local_midnight(at)) >= cap:
            return "suppressed", f"today's limit of {cap} '{c.type}' alerts was reached"
        if s.paused:
            return "paused", "sending is paused"
        if s.in_quiet_hours(at) and c.type != "brief":       # the brief IS the morning summary: never held
            return "held_quiet", f"quiet hours ({s.quiet_start}-{s.quiet_end} {s.place}): held for the summary"
        if s.dry_run:
            return "dry_run", "dry run: recorded, not sent"
        return "pending", None
