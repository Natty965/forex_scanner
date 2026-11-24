"""Alerts API (v0.4, milestone A3): listing, preferences, and protection of the one write endpoint."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from forex_scanner.alerts.engine import AlertEngine
from forex_scanner.alerts.settings import load_settings
from forex_scanner.api.app import create_app
from forex_scanner.storage import CandleStore
from tests.fakes import WEDNESDAY

DASHBOARD = "http://localhost"          # the dashboard page's own origin in tests


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "a.db"
    with CandleStore(path) as s:
        for n, (kind, delivery) in enumerate([("unusual_move", "dry_run"), ("market_events", "held_quiet"),
                                              ("brief", "dry_run")]):
            s.add_alert({"key": f"k{n}", "created_at": WEDNESDAY + timedelta(minutes=n), "type": kind,
                         "severity": "info", "pair": "EUR/USD", "timeframe": "H1", "title": f"T{n}",
                         "body": f"body {n}", "delivery": delivery, "delivery_note": "note"})
        s.set_brief_log("2026-11-25", "created", WEDNESDAY, 3)
    return path


def client(db, **kw):
    return TestClient(create_app(db, source="fake", now=lambda: WEDNESDAY), base_url="http://localhost", **kw)


def put(c, body, origin=DASHBOARD, content_type="application/json"):
    import json

    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    return c.put("/api/alert-settings", content=json.dumps(body), headers=headers)


# ---------- reading ----------


def test_alerts_list_newest_first_with_mode_and_briefs(db):
    r = client(db).get("/api/alerts").json()
    assert [a["title"] for a in r["alerts"]] == ["T2", "T1", "T0"]
    assert r["mode"] == "dry_run" and r["telegram_connected"] is False
    assert r["briefs"][0]["status"] == "created"
    assert "dry run" in r["delivery_meaning"]["dry_run"]
    assert "key" not in r["alerts"][0]


def test_alerts_filters(db):
    c = client(db)
    assert [a["title"] for a in c.get("/api/alerts", params={"type": "brief"}).json()["alerts"]] == ["T2"]
    assert [a["title"] for a in c.get("/api/alerts", params={"delivery": "held_quiet"}).json()["alerts"]] == ["T1"]


def test_settings_have_defaults(db):
    r = client(db).get("/api/alert-settings").json()
    assert r["settings"] == r["defaults"]
    assert r["settings"]["timezone"] == "Europe/London" and r["settings"]["dry_run"] is True


# ---------- changing preferences ----------


def test_change_is_saved_and_used_by_the_engine(db):
    r = put(client(db), {"brief_time": "06:30", "unusual_range_atr": 3, "events_timeframes": ["D1"]})
    assert r.status_code == 200
    assert r.json()["settings"]["brief_time"] == "06:30"
    with CandleStore(db) as s:
        engine_settings = AlertEngine(s).settings                  # what the worker will use next cycle
        assert (engine_settings.brief_time, engine_settings.unusual_range_atr) == ("06:30", 3.0)
        assert engine_settings.events_timeframes == ["D1"]


def test_pause_switch(db):
    c = client(db)
    assert put(c, {"paused": True}).status_code == 200
    assert c.get("/api/alerts").json()["mode"] == "paused"


@pytest.mark.parametrize("body,problem", [
    ({"timezone": "Mars/Base"}, "Unknown timezone"),
    ({"quiet_start": "late"}, "quiet_start"),
    ({"unusual_range_atr": 100}, "between 0.5 and 20"),
    ({"rsi_high_exit": 75}, "RSI levels"),
    ({"dry_run": "no"}, "wrong kind of value"),
    ({"data_fail_cycles": 2.5}, "wrong kind of value"),
    ({"send_to": "someone@example.com"}, "Unknown setting"),
])
def test_bad_changes_are_refused_with_reasons_and_change_nothing(db, body, problem):
    r = put(client(db), body)
    assert r.status_code == 422
    assert any(problem in p for p in r.json()["detail"]["problems"])
    with CandleStore(db) as s:
        assert load_settings(s).timezone == "Europe/London" and load_settings(s).dry_run is True


# ---------- protection ----------


@pytest.mark.parametrize("origin", [None, "https://evil.example", "http://localhost.evil.example", "null"])
def test_changes_from_anywhere_but_the_dashboard_are_refused(db, origin):
    r = put(client(db), {"paused": True}, origin=origin)
    assert r.status_code == 403
    with CandleStore(db) as s:
        assert load_settings(s).paused is False


def test_changes_must_be_json(db):
    r = put(client(db), {"paused": True}, content_type="text/plain")
    assert r.status_code == 415


def test_dev_server_origin_is_allowed(db):
    assert put(client(db), {"paused": True}, origin="http://localhost:5173").status_code == 200


def test_requests_to_any_other_host_name_are_refused(db):
    rebound = TestClient(create_app(db, source="fake"), base_url="http://attacker.example")
    assert rebound.get("/api/alerts").status_code == 400            # DNS rebinding blocked
    assert rebound.put("/api/alert-settings", json={"paused": True},
                       headers={"Origin": "http://attacker.example"}).status_code == 400
    assert TestClient(create_app(db, source="fake"), base_url="http://127.0.0.1:8000").get(
        "/api/health").status_code == 200
