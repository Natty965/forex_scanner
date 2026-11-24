"""Daily brief (v0.4, milestone A2): content, schedule, late rule, weekends, held alerts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from forex_scanner.alerts.brief import compose_brief
from forex_scanner.alerts.engine import AlertEngine, AlertInputs
from forex_scanner.alerts.settings import AlertSettings
from forex_scanner.analysis.indicator_summary import IndicatorSummary
from forex_scanner.analysis.snapshot import Change, PriceSnapshot
from forex_scanner.analysis.trend import TrendResult
from forex_scanner.storage import CandleStore

UTC = timezone.utc
S = AlertSettings()                                          # brief 07:00 Europe/London
MONDAY_0700 = datetime(2026, 11, 23, 7, 0, tzinfo=UTC)      # November: London = UTC
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]


def snaps(at, age_min=1):
    out = []
    for p, price in zip(PAIRS, [1.0994, 1.3127, 159.07], strict=True):
        ch = Change(price - 0.002, at - timedelta(hours=24), 20.0, 0.18, True)
        out.append(PriceSnapshot(p, "fake", price, at - timedelta(minutes=age_min), at, "LIVE", None, ch))
    return out


def trends(at, status="OK"):
    return [(TrendResult(p, tf, "DOWN" if tf == "D1" else "SIDEWAYS", 50, at), status)
            for p in PAIRS for tf in ["M15", "H1", "D1"]]


def inds(at, status="OK"):
    return [(IndicatorSummary(p, "D1", 520, at, 1.1, {20: 1.1, 50: 1.2, 200: None}, 29.3, 0.0062,
                              "STACKED DOWN", "LOW"), status) for p in PAIRS]


def inputs(at, ok=12, status="OK"):
    return AlertInputs(at=at, snapshots=snaps(at), trends=trends(at, status), indicators=inds(at, status),
                       ok_count=ok)


# ---------- content ----------


def test_brief_content():
    c = compose_brief(MONDAY_0700, S, snaps(MONDAY_0700), trends(MONDAY_0700), inds(MONDAY_0700))
    assert c.title == "DAILY BRIEF · Mon 23 Nov 2026" and c.key == "brief:2026-11-23"
    for text in ["EUR/USD  1.09940  LIVE  24h +20.0 pips (+0.18%) *", "USD/JPY  159.070",
                 "Trend   M15 sideways · H1 sideways · D1 DOWN", "D1 EMAs stacked down",
                 "D1 RSI  29.3, LOW zone (often called 'oversold')", "Typical daily range ≈ 62 pips",
                 "London opens 08:00 UTC (08:00 London)", "New York opens 13:00 UTC (13:00 London)",
                 "Economic calendar: not available yet", "not predictions or signals"]:
        assert text in c.body, text


def test_brief_never_presents_stale_data_as_current():
    at = MONDAY_0700
    c = compose_brief(at, S, snaps(at, age_min=45), trends(at, "STALE"), inds(at, "STALE"))
    assert "STALE (45 min old)" in c.body
    assert "Trend   M15 n/a · H1 n/a · D1 n/a" in c.body and "D1 indicators not current." in c.body
    c = compose_brief(at, S, [], [], [])
    assert "EUR/USD: no current price" in c.body


def test_brief_session_times_follow_summer_time():
    july = datetime(2026, 7, 6, 6, 0, tzinfo=UTC)               # 07:00 London (BST)
    body = compose_brief(july, S, [], [], []).body
    assert "London opens 07:00 UTC (08:00 London)" in body and "New York opens 12:00 UTC (13:00 London)" in body


# ---------- schedule ----------


def run(store, at, **kw):
    return AlertEngine(store, kw.pop("settings", S)).process(inputs(at, **kw))


def test_brief_created_once_at_the_brief_time(tmp_path):
    with CandleStore(tmp_path / "b.db") as store:
        assert run(store, MONDAY_0700 - timedelta(minutes=5)) == []            # not yet
        (a,) = run(store, MONDAY_0700 + timedelta(minutes=2))
        assert a["type"] == "brief" and a["delivery"] == "dry_run" and "(late)" not in a["title"]
        assert run(store, MONDAY_0700 + timedelta(minutes=7)) == []            # once per day
        assert store.get_brief_log("2026-11-23")["status"] == "created"


def test_late_brief_within_three_hours_is_labelled_late(tmp_path):
    with CandleStore(tmp_path / "b.db") as store:
        (a,) = run(store, MONDAY_0700 + timedelta(hours=1, minutes=30))
        assert a["title"].endswith("(late)") and "Late: due at 07:00 London, created 08:30" in a["body"]
        assert store.get_brief_log("2026-11-23")["status"] == "created late"


def test_brief_skipped_when_worker_was_off_too_long(tmp_path):
    with CandleStore(tmp_path / "b.db") as store:
        assert run(store, MONDAY_0700 + timedelta(hours=4)) == []
        assert "wasn't running between 07:00 and 10:00" in store.get_brief_log("2026-11-23")["status"]


def test_brief_skipped_on_weekends(tmp_path):
    saturday = datetime(2026, 11, 28, 7, 5, tzinfo=UTC)
    with CandleStore(tmp_path / "b.db") as store:
        assert run(store, saturday) == []
        assert store.get_brief_log("2026-11-28")["status"] == "skipped: market closed"


def test_brief_waits_for_fresh_data(tmp_path):
    with CandleStore(tmp_path / "b.db") as store:
        assert run(store, MONDAY_0700 + timedelta(minutes=2), ok=0) == []      # everything failed
        assert store.get_brief_log("2026-11-23") is None                       # try again next cycle
        (a,) = run(store, MONDAY_0700 + timedelta(minutes=7))
        assert a["type"] == "brief"


def test_brief_is_not_held_by_quiet_hours_but_lists_held_alerts(tmp_path):
    s = replace(S, brief_time="06:30", dry_run=False)                          # inside quiet hours
    with CandleStore(tmp_path / "b.db") as store:
        store.add_alert({"key": "x", "created_at": MONDAY_0700 - timedelta(hours=5), "type": "unusual_move",
                         "severity": "notice", "title": "UNUSUAL MOVE · GBP/USD · H1", "body": "...",
                         "delivery": "held_quiet"})
        (a,) = run(store, MONDAY_0700 - timedelta(minutes=25), settings=s)
        assert a["delivery"] == "pending"                                      # not held
        assert "Held during quiet hours (1):" in a["body"] and "02:00 UNUSUAL MOVE · GBP/USD · H1" in a["body"]
