"""Trading conditions (v0.5, milestone B1): risk warnings, never trade signals."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from forex_scanner.alerts import conditions as cond
from forex_scanner.alerts.brief import compose_brief
from forex_scanner.alerts.engine import AlertEngine, AlertInputs
from forex_scanner.alerts.settings import AlertSettings, apply_changes, load_settings, validate
from forex_scanner.api.app import create_app
from forex_scanner.providers import CandleSet
from forex_scanner.storage import CandleStore
from tests.fakes import SATURDAY, WEDNESDAY

UTC = timezone.utc
S = AlertSettings()
FRIDAY_1630_NY = datetime(2026, 11, 27, 21, 30, tzinfo=UTC)       # winter: New York = UTC-5
SUNDAY_1730_NY = datetime(2026, 11, 29, 22, 30, tzinfo=UTC)
WEDNESDAY_1650_NY = datetime(2026, 11, 25, 21, 50, tzinfo=UTC)


def h1(recent_pips=10.0, normal_pips=10.0, n=600, now=WEDNESDAY):
    """n closed H1 candles of `normal_pips` range; the newest 14 have `recent_pips`."""
    idx = pd.date_range(end=pd.Timestamp(now) - pd.Timedelta(hours=1), periods=n, freq="1h")
    half = np.full(n, normal_pips * 0.0001 / 2)
    half[-cond.RECENT_CANDLES:] = recent_pips * 0.0001 / 2
    close = np.full(n, 1.1)
    return pd.DataFrame({"open": close, "high": close + half, "low": close - half, "close": close,
                         "volume": np.nan, "complete": True}, index=idx)


def kinds(found):
    return [(c.kind, c.level) for c in found]


# ---------- the clock ----------


@pytest.mark.parametrize("now,expected", [
    (WEDNESDAY, []),                                                    # an ordinary Wednesday noon
    (FRIDAY_1630_NY, [("weekend_close", "AVOID")]),
    (datetime(2026, 11, 27, 20, 59, tzinfo=UTC), []),                   # Friday 15:59 New York
    (SUNDAY_1730_NY, [("weekly_open", "CAUTION")]),
    (WEDNESDAY_1650_NY, [("rollover", "CAUTION")]),
    (datetime(2026, 11, 25, 22, 10, tzinfo=UTC), [("rollover", "CAUTION")]),    # 17:10 New York
    (datetime(2026, 11, 25, 22, 20, tzinfo=UTC), []),                   # 17:20: over
    (SATURDAY, [("market_closed", "INFO")]),
    (datetime(2026, 7, 10, 20, 30, tzinfo=UTC), [("weekend_close", "AVOID")]),  # summer: New York = UTC-4
])
def test_time_conditions(now, expected):
    assert kinds(cond.time_conditions(now)) == expected


def test_weekend_close_says_how_long_is_left():
    (c,) = cond.time_conditions(FRIDAY_1630_NY)
    assert "30 min" in c.text and "gap" in c.why


# ---------- volatility ----------


def test_volatility_ratio_compares_recent_ranges_with_normal():
    assert cond.volatility_ratio(h1(10)) == pytest.approx(1.0)
    assert cond.volatility_ratio(h1(25)) == pytest.approx(2.5)
    assert cond.volatility_ratio(h1(25, n=40)) is None                  # not enough history


def test_volatility_ignores_the_forming_candle():
    df = h1(10)
    df.iloc[-1, df.columns.get_loc("high")] += 0.05                     # huge, but still forming
    df.iloc[-1, df.columns.get_loc("complete")] = False
    assert cond.volatility_ratio(df) == pytest.approx(1.0, abs=0.01)


def test_high_volatility_condition_per_pair():
    found = cond.volatility_conditions({"EUR/USD": h1(25), "GBP/USD": h1(12)}, 1.8)
    assert [(c.pair, c.kind) for c in found] == [("EUR/USD", "high_volatility")]
    assert "2.5x" in found[0].text


# ---------- all together ----------


def test_stale_prices_and_ordering():
    found = cond.trading_conditions(WEDNESDAY_1650_NY, {"EUR/USD": h1(25)},
                                    {"EUR/USD": "LIVE", "USD/JPY": "STALE"})
    assert kinds(found) == [("stale_data", "AVOID"), ("rollover", "CAUTION"), ("high_volatility", "CAUTION")]


def test_only_market_closed_at_the_weekend():
    found = cond.trading_conditions(SATURDAY, {"EUR/USD": h1(25, now=SATURDAY)}, {"EUR/USD": "STALE"})
    assert kinds(found) == [("market_closed", "INFO")]


def test_warnings_never_suggest_trading():
    found = cond.trading_conditions(FRIDAY_1630_NY, {"EUR/USD": h1(25, now=FRIDAY_1630_NY)}, {})
    text = " ".join(c.text + " " + c.why for c in found).lower()
    for word in ("buy", "sell", "good time", "opportunity"):
        assert word not in text


# ---------- settings ----------


def test_caution_settings_validation():
    assert validate(replace(S, caution_kinds=["weekend_close", "moon_phase"]))
    assert validate(replace(S, volatility_threshold=1.0))
    new, problems = apply_changes(S, {"volatility_threshold": 2, "caution_kinds": ["rollover"]})
    assert problems == [] and new.volatility_threshold == 2.0 and new.caution_kinds == ["rollover"]


def test_old_stored_daily_caps_get_the_new_caution_cap(tmp_path):
    with CandleStore(tmp_path / "s.db") as store:
        store.set_setting("alerts", {"daily_caps": {"unusual_move": 3, "market_events": 12, "data": 6,
                                                    "brief": 2}}, WEDNESDAY)
        caps = load_settings(store).daily_caps
        assert caps["unusual_move"] == 3 and caps["caution"] == 6


# ---------- caution alerts in the engine ----------


def inputs(at, recent_pips=10.0):
    cs = CandleSet("EUR/USD", "H1", "fake", at, h1(recent_pips, now=at), False)
    return AlertInputs(at=at, candle_sets={("EUR/USD", "H1"): cs}, statuses={("EUR/USD", "H1"): "OK"},
                       ok_count=12)


def cautions(alerts):
    return [a for a in alerts if a["type"] == "caution"]


def test_weekend_close_alert_once(tmp_path):
    with CandleStore(tmp_path / "e.db") as store:
        (a,) = cautions(AlertEngine(store).process(inputs(FRIDAY_1630_NY)))
        assert a["title"] == "AVOID NEW TRADES" and a["severity"] == "warning"
        assert "not a signal" in a["body"] and a["delivery"] == "dry_run"
        later = AlertEngine(store).process(inputs(FRIDAY_1630_NY + timedelta(minutes=10)))
        assert cautions(later) == []                                    # same window: not repeated


def test_rollover_is_not_an_alert_by_default_but_can_be_chosen(tmp_path):
    with CandleStore(tmp_path / "e.db") as store:
        assert cautions(AlertEngine(store).process(inputs(WEDNESDAY_1650_NY))) == []
        chosen = replace(S, caution_kinds=["rollover"])
        (a,) = cautions(AlertEngine(store, chosen).process(inputs(WEDNESDAY_1650_NY + timedelta(minutes=5))))
        assert a["data"]["kind"] == "rollover"


def test_volatility_alert_uses_hysteresis(tmp_path):
    at = WEDNESDAY
    with CandleStore(tmp_path / "e.db") as store:
        def run(pips):
            nonlocal at
            at += timedelta(minutes=5)
            return cautions(AlertEngine(store, replace(S, unusual_enabled=False)).process(inputs(at, pips)))

        (a,) = run(25)
        assert a["pair"] == "EUR/USD" and a["title"] == "CAUTION · EUR/USD"
        assert run(25) == []                         # still high: no repeat
        assert run(17) == []                         # 1.7x: below 1.8 but not below 0.85 x 1.8
        assert run(19) == []                         # back above: still not re-armed
        assert run(14) == []                         # 1.4x: clearly calm, re-armed
        assert len(run(20)) == 1                     # a new episode


def test_caution_alerts_can_be_switched_off(tmp_path):
    with CandleStore(tmp_path / "e.db") as store:
        off = replace(S, caution_enabled=False)
        assert cautions(AlertEngine(store, off).process(inputs(FRIDAY_1630_NY))) == []


# ---------- brief and Overview ----------


def test_brief_lists_conditions_or_says_none():
    none = compose_brief(WEDNESDAY, S, [], [], [], conditions=[])
    assert "No warnings right now. This is not a signal to trade." in none.body
    some = compose_brief(FRIDAY_1630_NY, S, [], [], [], conditions=cond.time_conditions(FRIDAY_1630_NY))
    assert "AVOID: Avoid new trades" in some.body


def test_overview_includes_trading_conditions(tmp_path):
    db = tmp_path / "o.db"
    CandleStore(db).close()
    c = TestClient(create_app(db, source="fake", now=lambda: FRIDAY_1630_NY), base_url="http://localhost")
    found = c.get("/api/overview").json()["trading_conditions"]
    assert [(x["kind"], x["level"]) for x in found] == [("weekend_close", "AVOID")]
