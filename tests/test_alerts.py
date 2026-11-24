"""Alert rules, settings and engine (v0.4, milestone A1). Offline, synthetic prices."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from forex_scanner import config
from forex_scanner.alerts import rules
from forex_scanner.alerts.engine import AlertEngine, AlertInputs
from forex_scanner.alerts.settings import AlertSettings, load_settings, save_settings, validate
from forex_scanner.analysis.indicator_summary import IndicatorSummary
from forex_scanner.analysis.snapshot import Change, PriceSnapshot
from forex_scanner.events import MarketEvent
from forex_scanner.providers import CandleSet
from forex_scanner.storage import CandleStore
from forex_scanner.worker import run_loop
from tests.fakes import SATURDAY, WEDNESDAY, FakeProvider

S = AlertSettings()
UTC = timezone.utc


# ---------- helpers ----------


def h1_set(last_range_pips=10.0, now=WEDNESDAY, n=200, gap_before_last=False, pair="EUR/USD"):
    """n closed H1 candles, each 10 pips high-to-low, the newest closing just before `now`;
    the newest one can be made bigger, or placed after a gap."""
    times = list(pd.date_range(end=pd.Timestamp(now) - pd.Timedelta(hours=1), periods=n, freq="1h"))
    if gap_before_last:
        times = [t - pd.Timedelta(hours=48) for t in times[:-1]] + [times[-1]]
    idx = pd.DatetimeIndex(times)
    close = np.full(n, 1.1)
    half = np.full(n, 0.0005)
    half[-1] = last_range_pips * 0.0001 / 2
    df = pd.DataFrame({"open": close - 0.0001, "high": close + half, "low": close - half, "close": close,
                       "volume": np.nan, "complete": True}, index=idx)
    return {(pair, "H1"): CandleSet(pair, "H1", "fake", now, df, False)}


def snapshot(pips=25.0, spans_gap=False, age_min=1, pair="EUR/USD", now=WEDNESDAY):
    ch = Change(1.1, now - timedelta(hours=1), pips, pips / 110, spans_gap)
    return PriceSnapshot(pair, "fake", 1.1025, now - timedelta(minutes=age_min), now, "LIVE", ch, None)


def ind(rsi, pair="EUR/USD", tf="H1", atr_pips=10.0):
    return IndicatorSummary(pair, tf, 1200, WEDNESDAY, 1.1, {20: 1.1, 50: 1.1, 200: 1.1}, rsi,
                            atr_pips * 0.0001, "MIXED", "NEUTRAL")


# ---------- unusual moves ----------


def test_big_closed_candle_triggers_unusual_move():
    (c,) = rules.unusual_ranges(h1_set(30), {("EUR/USD", "H1"): "OK"}, WEDNESDAY, S)
    assert c.type == "unusual_move" and c.pair == "EUR/USD" and c.timeframe == "H1"
    assert c.data["ratio"] == pytest.approx(3.0, rel=0.05)
    assert "30.0 pips = 3.0x normal (ATR 10.0 pips)" in c.body
    assert "12:00 UTC (12:00 London)" in c.body and "not a signal" in c.body


@pytest.mark.parametrize("why,kwargs,statuses,now", [
    ("below threshold", {"last_range_pips": 20}, "OK", WEDNESDAY),
    ("first candle after a gap", {"last_range_pips": 30, "gap_before_last": True}, "OK", WEDNESDAY),
    ("data not current", {"last_range_pips": 30}, "STALE", WEDNESDAY),
    ("old candle", {"last_range_pips": 30}, "OK", WEDNESDAY + timedelta(hours=5)),
    ("market closed", {"last_range_pips": 30, "now": SATURDAY}, "OK", SATURDAY),
])
def test_unusual_move_not_triggered(why, kwargs, statuses, now):
    assert rules.unusual_ranges(h1_set(**kwargs), {("EUR/USD", "H1"): statuses}, now, S) == [], why


def test_timeframes_not_selected_are_ignored():
    s = replace(S, unusual_timeframes=["M15"])
    assert rules.unusual_ranges(h1_set(30), {("EUR/USD", "H1"): "OK"}, WEDNESDAY, s) == []


def test_fast_live_move():
    (c,) = rules.live_moves([snapshot(25)], {"EUR/USD": 10.0}, WEDNESDAY, S)
    assert "rose 25.0 pips in the last hour = 2.5x" in c.body
    assert rules.live_moves([snapshot(15)], {"EUR/USD": 10.0}, WEDNESDAY, S) == []              # 1.5x
    assert rules.live_moves([snapshot(25, spans_gap=True)], {"EUR/USD": 10.0}, WEDNESDAY, S) == []  # gap
    assert rules.live_moves([snapshot(25, age_min=10)], {"EUR/USD": 10.0}, WEDNESDAY, S) == []  # not LIVE
    assert rules.live_moves([snapshot(25)], {}, WEDNESDAY, S) == []                               # no ATR


# ---------- market events ----------


@pytest.mark.parametrize("before,rsi,after", [
    (None, 70.0, "HIGH"), (None, 69.9, "NEUTRAL"), ("HIGH", 66.0, "HIGH"), ("HIGH", 64.9, "NEUTRAL"),
    (None, 30.0, "LOW"), ("LOW", 34.0, "LOW"), ("LOW", 35.1, "NEUTRAL"), ("LOW", 71.0, "HIGH"),
])
def test_rsi_hysteresis(before, rsi, after):
    assert rules.rsi_zone_with_hysteresis(before, rsi, S) == after


def test_market_events_batched_filtered_and_baselined():
    zones = {}
    c, zones = rules.market_events([], [(ind(72.0), "OK")], zones, WEDNESDAY, S)
    assert c is None and zones == {"EUR/USD|H1": "HIGH"}           # first sight: baseline only
    events = [MarketEvent(WEDNESDAY, "EUR/USD", "H1", "trend", "Trend SIDEWAYS -> UP"),
              MarketEvent(WEDNESDAY, "EUR/USD", "M15", "trend", "Trend UP -> DOWN"),        # M15: excluded
              MarketEvent(WEDNESDAY, "GBP/USD", "D1", "rsi_zone", "RSI entered ...")]       # own RSI logic
    c, zones = rules.market_events(events, [(ind(66.0), "OK")], zones, WEDNESDAY, S)
    assert c.data["lines"] == ["EUR/USD H1: Trend SIDEWAYS -> UP"]  # 66 is still HIGH (exit below 65)
    c, zones = rules.market_events([], [(ind(64.0), "OK")], zones, WEDNESDAY + timedelta(minutes=5), S)
    assert c.data["lines"] == ["EUR/USD H1: RSI left the HIGH zone (70+) (64.0)"]
    c, _ = rules.market_events([], [(ind(80.0), "STALE")], zones, WEDNESDAY, S)
    assert c is None                                               # stale data: nothing


# ---------- data and system ----------


def test_data_problem_once_then_recovery_once():
    state, out = None, []
    for n in range(1, 6):
        c, state = rules.data_problem(n, 0, state, WEDNESDAY + timedelta(minutes=5 * n), S)
        out.append(c)
    assert [c is not None for c in out] == [False, False, True, False, False]
    assert "failed for 3 cycles" in out[2].body
    c, state = rules.data_problem(0, 12, state, WEDNESDAY + timedelta(hours=1), S)
    assert c.title == "DATA RECOVERED" and state == {"active": False, "since": None}
    c, state = rules.data_problem(0, 12, state, WEDNESDAY + timedelta(hours=2), S)
    assert c is None


def test_worker_downtime():
    five = timedelta(minutes=5)
    c = rules.downtime(WEDNESDAY - timedelta(hours=3), WEDNESDAY, five, S)
    assert c.title == "WORKER WAS OFF" and "(3 h 0 min)" in c.body
    assert rules.downtime(WEDNESDAY - timedelta(minutes=12), WEDNESDAY, five, S) is None       # normal gap
    sat, sun = SATURDAY, SATURDAY + timedelta(hours=20)
    assert rules.downtime(sat, sun, five, S) is None                                            # weekend only
    assert rules.downtime(None, WEDNESDAY, five, S) is None


# ---------- settings ----------


def test_quiet_hours_wrap_past_midnight_in_local_time():
    def london(h, m=0):                                     # a London summer time (UTC+1), as UTC
        return datetime(2026, 7, 1, h, m, tzinfo=UTC) - timedelta(hours=1)

    assert S.in_quiet_hours(london(22)) and S.in_quiet_hours(london(3)) and S.in_quiet_hours(london(6, 59))
    assert not S.in_quiet_hours(london(7)) and not S.in_quiet_hours(london(21, 59))


def test_validation_explains_problems():
    bad = replace(S, timezone="Mars/Base", brief_time="7am", unusual_timeframes=["H4"], rsi_high_exit=75)
    problems = " ".join(validate(bad))
    assert "timezone" in problems and "brief_time" in problems and "H4" in problems and "RSI" in problems
    assert validate(S) == []


def test_settings_round_trip_and_invalid_saves_are_refused(tmp_path):
    with CandleStore(tmp_path / "s.db") as store:
        assert load_settings(store) == AlertSettings()
        assert save_settings(store, replace(S, brief_time="06:30", dry_run=False), WEDNESDAY) == []
        assert load_settings(store).brief_time == "06:30" and load_settings(store).dry_run is False
        assert save_settings(store, replace(S, timezone="Nowhere"), WEDNESDAY)       # refused
        assert load_settings(store).brief_time == "06:30"                             # unchanged


# ---------- engine ----------


def engine_inputs(at=WEDNESDAY, last_range=30.0, **kw):
    return AlertInputs(at=at, candle_sets=h1_set(last_range, now=at), statuses={("EUR/USD", "H1"): "OK"},
                       ok_count=12, **kw)


def test_engine_records_in_dry_run_and_ignores_duplicates(tmp_path):
    with CandleStore(tmp_path / "e.db") as store:
        (a,) = AlertEngine(store).process(engine_inputs())
        assert a["delivery"] == "dry_run" and a["id"] == 1
        assert AlertEngine(store).process(engine_inputs()) == []          # same candle again: duplicate
        assert len(store.load_alerts()) == 1


def test_engine_cooldown(tmp_path):
    with CandleStore(tmp_path / "e.db") as store:
        AlertEngine(store).process(engine_inputs())
        later = AlertEngine(store).process(engine_inputs(at=WEDNESDAY + timedelta(minutes=30)))
        assert later == []                                                # new candle key, but cooling down
        after = AlertEngine(store).process(engine_inputs(at=WEDNESDAY + timedelta(minutes=61)))
        assert len(after) == 1                                            # cooldown over


def test_engine_daily_cap_records_as_suppressed(tmp_path):
    s = replace(S, daily_caps={"unusual_move": 1, "market_events": 12, "data": 6, "brief": 2},
                unusual_cooldown_minutes=0)
    with CandleStore(tmp_path / "e.db") as store:
        AlertEngine(store, s).process(engine_inputs())
        (a,) = AlertEngine(store, s).process(engine_inputs(at=WEDNESDAY + timedelta(hours=1)))
        assert a["delivery"] == "suppressed" and "limit of 1" in a["delivery_note"]


@pytest.mark.parametrize("changes,at,expected", [
    ({"dry_run": False}, WEDNESDAY, "pending"),
    ({"dry_run": False, "paused": True}, WEDNESDAY, "paused"),
    ({"dry_run": False}, datetime(2026, 11, 25, 23, 0, tzinfo=UTC), "held_quiet"),     # 23:00 London
])
def test_engine_delivery_decision(tmp_path, changes, at, expected):
    with CandleStore(tmp_path / "e.db") as store:
        (a,) = AlertEngine(store, replace(S, **changes)).process(engine_inputs(at=at))
        assert a["delivery"] == expected


def test_no_market_alerts_when_every_request_failed(tmp_path):
    with CandleStore(tmp_path / "e.db") as store:
        inp = engine_inputs()
        inp.ok_count = 0
        assert AlertEngine(store).process(inp) == []


# ---------- inside the worker ----------


@pytest.fixture(autouse=True)
def no_request_delay(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)


def test_worker_runs_the_engine_without_errors_and_reports_downtime(tmp_path):
    db = tmp_path / "w.db"
    infos = []
    run_loop(FakeProvider(), ["EUR/USD"], ["H1"], timedelta(minutes=5), timedelta(minutes=30), db_path=db,
             cycles=1, sleep=lambda s: None, now=lambda: WEDNESDAY, on_cycle=infos.append)
    assert infos[0].alert_error is None
    later = WEDNESDAY + timedelta(hours=3)                         # restarted 3 hours later
    provider = FakeProvider(clock=lambda: later)
    run_loop(provider, ["EUR/USD"], ["H1"], timedelta(minutes=5), timedelta(minutes=30), db_path=db,
             cycles=1, sleep=lambda s: None, now=lambda: later, on_cycle=infos.append)
    assert infos[1].alert_error is None
    assert [a["title"] for a in infos[1].alerts] == ["WORKER WAS OFF"]


def test_alert_engine_error_never_stops_data_collection(tmp_path, monkeypatch):
    from forex_scanner.alerts import engine

    monkeypatch.setattr(engine.AlertEngine, "process", lambda self, inp: 1 / 0)
    infos = []
    run_loop(FakeProvider(), ["EUR/USD"], ["H1"], timedelta(minutes=5), timedelta(minutes=30),
             db_path=tmp_path / "w.db", cycles=2, sleep=lambda s: None, now=lambda: WEDNESDAY, on_cycle=infos.append)
    assert len(infos) == 2 and "ZeroDivisionError" in infos[0].alert_error
