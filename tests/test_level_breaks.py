"""Level-break events (v0.5, milestone B4): a closed candle closing beyond a support/resistance level."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from forex_scanner.alerts import rules
from forex_scanner.alerts.settings import AlertSettings
from forex_scanner.analysis.structure import AT_PRICE, RESISTANCE, SUPPORT, Level, StructureResult
from forex_scanner.events import LEVEL_BREAK, EventTracker, ItemState, level_breaks
from forex_scanner.storage import CandleStore
from forex_scanner.storage.records import jsonable, state_from
from tests.fakes import WEDNESDAY

T0 = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
ATR = 0.0010                                   # 10 pips: break margin = 1 pip


def lvl(side, low, high, touches=3):
    t = T0 - timedelta(days=1)
    return Level(side, (low + high) / 2, low, high, touches, t, t, 0.0, 0.0)


def struct(close, hour=0, levels=(), atr=ATR, pair="EUR/USD", tf="H1"):
    res = [x for x in levels if x.side == RESISTANCE]
    sup = [x for x in levels if x.side == SUPPORT]
    at = [x for x in levels if x.side == AT_PRICE]
    return StructureResult(pair, tf, T0 + timedelta(hours=hour), close, atr, 300, "MIXED", [], None, None,
                           res, sup, at, [])


RES = lvl(RESISTANCE, 1.1000, 1.1005, 7)
SUP = lvl(SUPPORT, 1.0900, 1.0905, 4)


def run(*structs):
    """Feed candles one by one; return the messages of the last one."""
    state, msgs = ItemState(), []
    for s in structs:
        state, msgs = level_breaks(state, s)
    return msgs


def after(first, second):
    return run(first, second)


# ---------- the rule (margin 0.25 x ATR = 2.5 pips here) ----------


def test_close_above_resistance_is_a_break():
    assert after(struct(1.0990, 0, [RES, SUP]), struct(1.1010, 1)) == [
        "Closed above resistance 1.10000-1.10050 (7 touches)"]


def test_close_below_support_is_a_break():
    assert after(struct(1.0950, 0, [RES, SUP]), struct(1.0895, 1)) == [
        "Closed below support 1.09000-1.09050 (4 touches)"]


def test_a_close_within_the_margin_is_only_a_touch():
    assert after(struct(1.0990, 0, [RES]), struct(1.1007, 1)) == []        # 2 pips above: inside the margin


def test_a_wick_through_is_not_a_break():
    assert after(struct(1.0990, 0, [RES]), struct(1.0995, 1)) == []        # only the close counts


def test_climbing_onto_a_level_then_through_it_is_a_break():
    on = lvl(AT_PRICE, 1.1000, 1.1005)
    msgs = run(struct(1.0990, 0, [RES]), struct(1.1002, 1, [on]), struct(1.1010, 2))
    assert msgs == ["Closed above resistance 1.10000-1.10050 (3 touches)"]


def test_wobbling_inside_a_level_is_not_a_break():
    on = lvl(AT_PRICE, 1.1000, 1.1005)
    assert run(struct(1.1002, 0, [on]), struct(1.1003, 1, [on]), struct(1.1010, 2)) == []


def test_weak_levels_are_ignored():
    weak = lvl(RESISTANCE, 1.1000, 1.1005, touches=2)
    assert after(struct(1.0990, 0, [weak]), struct(1.1010, 1)) == []


def test_one_message_per_candle_names_the_strongest_level():
    other = lvl(RESISTANCE, 1.0995, 1.0996, touches=4)
    assert after(struct(1.0990, 0, [other, RES]), struct(1.1010, 1)) == [
        "Closed above resistance 1.10000-1.10050 (7 touches, and 1 more level(s))"]


def test_nothing_new_until_a_new_candle_closes():
    state, _ = level_breaks(ItemState(), struct(1.0990, 0, [RES]))
    state2, msgs = level_breaks(state, struct(1.1010, 0, [RES]))            # same candle, next cycle
    assert msgs == [] and state2 == state


def test_already_above_is_not_a_new_break():
    assert after(struct(1.1010, 0, [RES]), struct(1.1020, 1)) == []


def test_without_atr_nothing_is_judged_and_memory_is_kept():
    state, _ = level_breaks(ItemState(), struct(1.0990, 0, [RES]))
    kept, msgs = level_breaks(state, struct(1.1050, 1, atr=None))
    assert msgs == [] and kept == state


def test_jpy_zone_formatting():
    jpy = lvl(RESISTANCE, 159.10, 159.10)
    first = struct(158.90, 0, [jpy], atr=0.10, pair="USD/JPY")
    assert after(first, struct(159.30, 1, atr=0.10, pair="USD/JPY")) == [
        "Closed above resistance 159.100 (3 touches)"]


# ---------- tracker, memory, alert ----------


def test_tracker_emits_level_break_events_and_starts_with_a_baseline():
    tr = EventTracker()
    assert tr.update(T0, [], [], [struct(1.1007, 0, [RES])]) == []         # first sight: baseline only
    tr2 = EventTracker()
    tr2.update(T0, [], [], [struct(1.0990, 0, [RES])])
    (e,) = tr2.update(T0 + timedelta(hours=1), [], [], [struct(1.1010, 1)])
    assert (e.kind, e.pair, e.timeframe) == (LEVEL_BREAK, "EUR/USD", "H1")


def test_level_memory_survives_a_restart(tmp_path):
    tr = EventTracker()
    tr.update(T0, [], [], [struct(1.0990, 0, [RES])])
    assert state_from(jsonable(tr.states[("EUR/USD", "H1")])) == tr.states[("EUR/USD", "H1")]
    with CandleStore(tmp_path / "s.db") as s:
        s.save_event_states("fake", tr.states, T0)
        restarted = EventTracker()
        restarted.states = s.load_event_states("fake")
    (e,) = restarted.update(T0 + timedelta(hours=1), [], [], [struct(1.1010, 1)])
    assert e.kind == LEVEL_BREAK


def test_states_saved_before_b4_still_load():
    old = {"trend": "UP", "ema_side": 1, "rsi_zone": "NEUTRAL", "rsi": 55.0}
    assert state_from(old).levels is None


def test_level_breaks_alert_only_on_chosen_timeframes():
    tr = EventTracker()
    tr.update(WEDNESDAY, [], [], [struct(1.0990, 0, [RES])])
    events = tr.update(WEDNESDAY, [], [], [struct(1.1010, 1)])             # an H1 break
    assert rules.market_events(events, [], {}, WEDNESDAY, AlertSettings())[0] is None     # default: D1 only
    h1_too = replace(AlertSettings(), level_break_timeframes=["H1", "D1"])
    c, _ = rules.market_events(events, [], {}, WEDNESDAY, h1_too)
    assert "EUR/USD H1: Closed above resistance 1.10000-1.10050 (7 touches)" in c.body
