"""Local API (U2): endpoints, freshness judged at request time, worker states, and no fetching."""

import shutil
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from forex_scanner import config
from forex_scanner.api.app import create_app
from forex_scanner.providers import CandleSet, yfinance_provider
from forex_scanner.storage import CandleStore
from forex_scanner.storage import db as dbmod
from forex_scanner.worker import run_loop
from tests.fakes import SATURDAY, WEDNESDAY, FakeProvider


@pytest.fixture(autouse=True)
def no_request_delay(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)


def collect(db, provider, cycles=1):
    """Let the worker save one cycle for all configured pairs and timeframes, at WEDNESDAY 12:00."""
    run_loop(provider, config.PAIRS, config.TIMEFRAMES, timedelta(minutes=5), timedelta(minutes=30),
             db_path=db, cycles=cycles, sleep=lambda s: None, now=lambda: WEDNESDAY)


@pytest.fixture(scope="module")
def saved_db(tmp_path_factory):
    """Built once per test file (it's the slow part): one worker cycle for 3 pairs x 4 timeframes."""
    path = tmp_path_factory.mktemp("api") / "api.db"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "REQUEST_DELAY_SECONDS", 0)
        collect(path, FakeProvider())
    return path


@pytest.fixture
def db(saved_db, tmp_path):
    """A private copy per test, so a test that adds data can't affect the others."""
    path = tmp_path / "api.db"
    shutil.copyfile(saved_db, path)
    return path


def local_client(app):
    """A test client that talks to the API like a browser on this PC (host: localhost)."""
    return TestClient(app, base_url="http://localhost")


def client(db, at=WEDNESDAY):
    return local_client(create_app(db, source="fake", now=lambda: at))


# ---------- basics ----------


def test_health_and_docs_page(db):
    c = client(db)
    assert c.get("/api/health").json()["ok"] is True
    assert c.get("/api/docs").status_code == 200
    paths = c.get("/api/openapi.json").json()["paths"]
    assert {"/api/status", "/api/overview", "/api/pairs/{pid}/analysis", "/api/pairs/{pid}/candles",
            "/api/events"} <= set(paths)
    # read-only, except changing alert preferences
    writable = {p: set(m) - {"get"} for p, m in paths.items() if set(m) - {"get"}}
    assert writable == {"/api/alert-settings": {"put"}, "/api/telegram/test": {"post"}}


def test_status(db):
    s = client(db).get("/api/status").json()
    assert s["source"] == "fake" and s["schema_version"] == dbmod.SCHEMA_VERSION
    assert s["market"] == {"open": True, "next_open": None}
    assert [p["id"] for p in s["pairs"]] == ["EURUSD", "GBPUSD", "USDJPY"]
    assert s["worker"]["label"] == "STOPPED" and s["worker"]["cycles"] == 1


def test_status_market_closed_shows_next_open(db):
    s = client(db, at=SATURDAY).get("/api/status").json()
    assert s["market"]["open"] is False
    assert s["market"]["next_open"].startswith("2026-11-29T22:00:00")


# ---------- worker states ----------


def test_worker_states(tmp_path):
    path = tmp_path / "w.db"
    CandleStore(path).close()
    assert client(path).get("/api/status").json()["worker"]["label"] == "NEVER STARTED"
    with CandleStore(path) as s:
        s.claim_worker(4321, WEDNESDAY)
        s.worker_heartbeat(WEDNESDAY, 3, WEDNESDAY + timedelta(minutes=5))
    w = client(path, at=WEDNESDAY + timedelta(minutes=1)).get("/api/status").json()["worker"]
    assert w["label"] == "RUNNING" and w["alive"] is True and w["pid"] == 4321
    late = client(path, at=WEDNESDAY + timedelta(minutes=20)).get("/api/status").json()["worker"]
    assert late["label"] == "NOT RESPONDING" and late["alive"] is False


# ---------- overview and freshness ----------


def test_overview_has_every_pair_and_timeframe(db):
    o = client(db).get("/api/overview").json()
    assert [p["pair"] for p in o["pairs"]] == config.PAIRS
    eur = o["pairs"][0]
    assert eur["price"]["status"] == "LIVE" and eur["price"]["price"] > 0
    assert set(eur["timeframes"]) == set(config.TIMEFRAMES)
    h1 = eur["timeframes"]["H1"]
    assert h1["trend"]["direction"] in ("UP", "DOWN", "SIDEWAYS") and h1["trend"]["status"] == "OK"
    assert h1["indicators"]["alignment"] and set(h1["indicators"]["emas"]) == {"20", "50", "200"}
    assert "reasons" not in h1["trend"]                         # details only on the analysis endpoint


@pytest.mark.parametrize("minutes_later,price_status,analysis_status", [
    (1, "LIVE", "OK"),
    (10, "DELAYED", "OK"),
    (20, "DELAYED", "STALE"),           # worker hasn't refreshed for 20 min (limit 13)
    (45, "STALE", "STALE"),
])
def test_freshness_is_judged_at_request_time(db, minutes_later, price_status, analysis_status):
    o = client(db, at=WEDNESDAY + timedelta(minutes=minutes_later)).get("/api/overview").json()
    eur = o["pairs"][0]
    assert eur["price"]["status"] == price_status
    assert eur["timeframes"]["H1"]["indicators"]["status"] == analysis_status
    assert eur["timeframes"]["M15"]["trend"]["status"] == analysis_status


def test_conditions_rsi_zone_only_from_current_data(tmp_path):
    path = tmp_path / "rising.db"
    provider = FakeProvider()
    provider.rising = True                                        # RSI 100 everywhere -> HIGH zone
    collect(path, provider)
    fresh = client(path, at=WEDNESDAY + timedelta(minutes=1)).get("/api/overview").json()["conditions"]
    rsi = [c for c in fresh if c["kind"] == "rsi_zone"]
    assert len(rsi) == len(config.PAIRS) * len(config.TIMEFRAMES)
    assert rsi[0]["text"].startswith("RSI 100.0: HIGH zone") and "not a signal" in rsi[0]["why"]

    stale = client(path, at=WEDNESDAY + timedelta(minutes=45)).get("/api/overview").json()["conditions"]
    assert not [c for c in stale if c["kind"] == "rsi_zone"]      # old analysis is not a market condition
    assert any(c["kind"] == "data" and "STALE" in c["text"] for c in stale)


def test_conditions_warn_when_worker_is_not_running(db):
    conds = client(db).get("/api/overview").json()["conditions"]
    assert conds[0]["kind"] == "data" and conds[0]["text"] == "Worker stopped: nothing is being updated"


def test_overview_on_saturday_says_market_closed(db):
    eur = client(db, at=SATURDAY).get("/api/overview").json()["pairs"][0]
    assert eur["price"]["status"] == "MARKET CLOSED"
    assert eur["timeframes"]["H1"]["trend"]["status"] == "MARKET CLOSED"


def test_overview_with_no_data_returns_nulls_not_guesses(tmp_path):
    path = tmp_path / "empty.db"
    CandleStore(path).close()
    o = client(path).get("/api/overview").json()
    assert all(p["price"] is None for p in o["pairs"])
    assert all(tf["trend"] is None for p in o["pairs"] for tf in p["timeframes"].values())


# ---------- one pair ----------


def test_pair_analysis_includes_reasons(db):
    a = client(db).get("/api/pairs/eurusd/analysis", params={"timeframe": "D1"}).json()
    assert a["pair"] == "EUR/USD" and a["timeframe"] == "D1"
    assert a["trend"]["reasons"] and a["indicators"]["reasons"]
    assert a["indicators"]["as_of_label"].endswith(" day")       # D1 labelled by trading day


def test_unknown_pair_and_timeframe(db):
    c = client(db)
    r = c.get("/api/pairs/AUDUSD/analysis")
    assert r.status_code == 404 and "EURUSD" in r.json()["detail"]
    assert c.get("/api/pairs/EURUSD/analysis", params={"timeframe": "H4"}).status_code == 422
    assert c.get("/api/pairs/EURUSD/candles", params={"limit": 0}).status_code == 422


def test_candles_oldest_first_with_limit(db):
    r = client(db).get("/api/pairs/USDJPY/candles", params={"timeframe": "M15", "limit": 5}).json()
    assert r["count"] == 5 and r["pair"] == "USD/JPY"
    times = [c["time"] for c in r["candles"]]
    assert times == sorted(times)
    assert all({"open", "high", "low", "close", "complete", "fetched_at"} <= set(c) for c in r["candles"])


# ---------- chart data ----------


def test_chart_lines_match_the_saved_analysis_exactly(db):
    c = client(db)
    chart = c.get("/api/pairs/EURUSD/chart", params={"timeframe": "H1", "limit": 300}).json()
    analysis = c.get("/api/pairs/EURUSD/analysis", params={"timeframe": "H1"}).json()["indicators"]
    assert chart["count"] == 300 and chart["time_zone"] == "UTC"
    last = {name: pts[-1]["value"] for name, pts in chart["lines"].items()}
    assert last["ema20"] == analysis["emas"]["20"]
    assert last["ema200"] == analysis["emas"]["200"]
    assert last["rsi14"] == analysis["rsi"]
    assert last["atr14"] == pytest.approx(analysis["atr_pips"], rel=1e-12)
    assert chart["status"] == "OK"


def test_chart_lines_respect_warm_up_and_closed_candles(db):
    c = client(db)
    # all 1300 stored candles: EMA 200 needs 1004, so its line starts at the 1004th closed candle
    full = c.get("/api/pairs/EURUSD/chart", params={"timeframe": "H1", "limit": 5000}).json()
    times = [k["time"] for k in full["candles"]]
    assert full["warmup"]["ema200"] == {"needed": 1004, "available": 1300, "ok": True}
    assert full["lines"]["ema200"][0]["time"] == times[1003]
    assert full["lines"]["ema20"][0]["time"] == times[103]              # 104 needed
    assert full["lines"]["rsi14"][0]["time"] == times[126]              # 127 needed
    # every line point belongs to a closed candle
    closed = {k["time"] for k in full["candles"] if k["complete"]}
    assert all(p["time"] in closed for pts in full["lines"].values() for p in pts)


def test_chart_forming_candle_is_flagged_and_has_no_indicator_value(db):
    with CandleStore(db) as s:          # mark the newest stored H1 candle as still forming
        s.conn.execute("UPDATE candles SET complete = 0 WHERE pair='EUR/USD' AND timeframe='H1' "
                       "AND time = (SELECT max(time) FROM candles WHERE pair='EUR/USD' AND timeframe='H1')")
        s.conn.commit()
    chart = client(db).get("/api/pairs/EURUSD/chart", params={"timeframe": "H1"}).json()
    assert chart["candles"][-1]["complete"] is False
    forming_time = chart["candles"][-1]["time"]
    assert all(p["time"] != forming_time for pts in chart["lines"].values() for p in pts)


def test_chart_with_too_little_history_has_empty_lines_and_says_why(tmp_path):
    path = tmp_path / "short.db"
    with CandleStore(path) as s:
        cs = FakeProvider().get_candles("EUR/USD", "D1", None)
        short = cs.candles.tail(500)                                     # like Yahoo's ~520 daily candles
        s.save(CandleSet(cs.pair, "D1", "fake", cs.fetched_at, short, False))
    chart = client(path).get("/api/pairs/EURUSD/chart", params={"timeframe": "D1", "limit": 500}).json()
    assert chart["warmup"]["ema200"] == {"needed": 1004, "available": 500, "ok": False}
    assert chart["lines"]["ema200"] == []
    assert chart["lines"]["ema50"] and chart["lines"]["rsi14"]
    assert chart["status"] is None                                       # no saved analysis for it


def test_chart_limit_bounds(db):
    c = client(db)
    assert c.get("/api/pairs/EURUSD/chart", params={"limit": 5}).status_code == 422
    assert c.get("/api/pairs/EURUSD/chart", params={"limit": 6000}).status_code == 422


# ---------- data health ----------


def test_data_health_all_ok_after_a_worker_cycle(db):
    h = client(db, at=WEDNESDAY + timedelta(minutes=1)).get("/api/data-health").json()
    assert h["database"]["schema_version"] == dbmod.SCHEMA_VERSION and h["database"]["size_bytes"] > 0
    assert len(h["items"]) == 3 * 4                                   # 3 pairs x (M1 + M15/H1/D1)
    assert {i["health"] for i in h["items"]} == {"OK"}
    assert h["fetches_24h"] == {"ok": 12, "failed": 0}
    assert h["recent_failures"] == []
    assert [g["name"] for g in h["settings"]["groups"]] == ["Data", "Worker", "Freshness", "Analysis"]


def test_data_health_reports_failures_old_data_and_unmonitored_items(db):
    with CandleStore(db) as s:
        s.log_failure("fake", "GBP/USD", "H1", "Yahoo timed out", fetched_at=WEDNESDAY + timedelta(minutes=5))
        cs = FakeProvider().get_candles("EUR/USD", "M5", None)       # saved once, e.g. with `fetch`
        s.save(cs)
    h = client(db, at=WEDNESDAY + timedelta(minutes=20)).get("/api/data-health").json()
    by = {(i["pair"], i["timeframe"]): i for i in h["items"]}
    assert by[("GBP/USD", "H1")]["health"] == "FAILING"
    assert by[("GBP/USD", "H1")]["last_error"] == "Yahoo timed out"
    assert by[("EUR/USD", "M5")]["health"] == "NOT MONITORED"
    assert by[("EUR/USD", "H1")]["health"] == "NOT UPDATING"         # 20 min without a worker cycle
    assert h["recent_failures"][0]["error"] == "Yahoo timed out"
    assert h["fetches_24h"]["failed"] == 1


# ---------- events ----------


def test_events_newest_first_and_filtered_by_pair(db):
    provider = FakeProvider()
    provider.rising = True
    collect(db, provider)                     # a second run: prices now rising -> events
    c = client(db)
    all_events = c.get("/api/events").json()
    assert all_events["count"] > 0
    assert {e["pair"] for e in all_events["events"]} == set(config.PAIRS)
    jpy = c.get("/api/events", params={"pair": "USDJPY"}).json()["events"]
    assert jpy and all(e["pair"] == "USD/JPY" for e in jpy)
    assert "Trend SIDEWAYS -> UP" in {e["message"] for e in jpy}


# ---------- serving the dashboard ----------


def test_dashboard_not_built_page(db, tmp_path):
    r = local_client(create_app(db, source="fake", web_dist=tmp_path / "missing")).get("/")
    assert r.status_code == 503 and "npm run build" in r.text


def test_dashboard_files_and_page_routes(db, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>APP</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build folder", encoding="utf-8")
    c = local_client(create_app(db, source="fake", web_dist=dist))

    assert c.get("/").text == "<html>APP</html>"
    assert c.get("/markets/EURUSD").text == "<html>APP</html>"       # app pages load index.html
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert "outside" not in c.get("/../secret.txt").text              # no escaping the build folder
    assert "outside" not in c.get("/%2e%2e/secret.txt").text
    assert c.get("/api/status").json()["source"] == "fake"           # API still wins
    r = c.get("/api/nope")
    assert r.status_code == 404 and "/api/docs" in r.json()["detail"]


# ---------- the `run` command: shutdown ----------


def test_run_command_stops_worker_and_releases_lock_when_server_stops(tmp_path, monkeypatch):
    """Simulates Ctrl+C: the 'server' returns after the worker's first cycle; the worker thread
    must then end on its own and mark itself stopped."""
    import threading

    import uvicorn

    from forex_scanner import __main__ as cli

    db = tmp_path / "run.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(cli, "get_provider", lambda name: FakeProvider())
    first_cycle = threading.Event()
    real_heartbeat = CandleStore.worker_heartbeat

    def heartbeat_and_signal(self, *args, **kwargs):
        real_heartbeat(self, *args, **kwargs)
        first_cycle.set()

    monkeypatch.setattr(CandleStore, "worker_heartbeat", heartbeat_and_signal)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: first_cycle.wait(60))   # "server" stops after cycle 1

    before = {t.name for t in threading.enumerate()}
    assert cli.cmd_run(interval_min=5, port=0, with_worker=True, force=False) == 0
    assert "worker" not in {t.name for t in threading.enumerate()} - before     # thread has ended
    with CandleStore(db) as s:
        st = s.worker_status()
    assert st["state"] == "stopped" and st["cycles"] == 1 and st["message"] == "stopped with Ctrl+C"


# ---------- threads ----------


def test_api_connections_can_be_closed_from_another_thread(db, monkeypatch):
    """Regression: live, FastAPI opened a request's connection in one thread and closed it in
    another, and SQLite refused ('objects created in a thread can only be used in that same
    thread'). The test client happens to use one thread, so this checks the setting directly."""
    from forex_scanner.api import app as api_module

    real = api_module.CandleStore
    seen_kwargs = []

    def recording_store(*args, **kwargs):
        seen_kwargs.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(api_module, "CandleStore", recording_store)
    client(db).get("/api/status")
    assert seen_kwargs and all(k.get("check_same_thread") is False for k in seen_kwargs)

    relaxed = real(db, check_same_thread=False)           # what the API uses
    assert run_in_other_thread(relaxed.close) is None     # closes fine from another thread

    strict = real(db)                                     # the default (worker, CLI) stays strict
    assert isinstance(run_in_other_thread(strict.close), sqlite3.ProgrammingError)
    strict.close()


def run_in_other_thread(fn):
    """Run fn() in a new thread; return the exception it raised, or None."""
    import threading

    outcome = []

    def target():
        try:
            fn()
            outcome.append(None)
        except Exception as exc:
            outcome.append(exc)

    t = threading.Thread(target=target)
    t.start()
    t.join()
    return outcome[0]


# ---------- never fetches ----------


def test_api_never_fetches_market_data(db, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the API must not fetch market data")

    monkeypatch.setattr(yfinance_provider.YFinanceProvider, "get_candles", forbidden)
    c = client(db)
    for url in ["/api/status", "/api/overview", "/api/pairs/EURUSD/analysis", "/api/pairs/EURUSD/candles",
                "/api/events"]:
        assert c.get(url).status_code == 200
