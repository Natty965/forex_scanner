"""Worker (U1): database upgrade, saved analysis/events, restart behaviour, and the one-worker lock."""

import sqlite3
from datetime import timedelta

import pytest

from forex_scanner import config
from forex_scanner.events import EventTracker
from forex_scanner.storage import CandleStore, WorkerBusy
from forex_scanner.storage import db as dbmod
from forex_scanner.storage.records import indicators_from, snapshot_from, structure_from, trend_from
from forex_scanner.worker import print_cycle_line, run_cycle, run_loop
from tests.fakes import WEDNESDAY, FakeProvider


@pytest.fixture(autouse=True)
def no_request_delay(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)


def loop(db, provider, cycles=1, **kw):
    return run_loop(provider, ["EUR/USD"], ["H1"], timedelta(minutes=5), timedelta(minutes=30),
                    db_path=db, cycles=cycles, sleep=kw.pop("sleep", lambda s: None),
                    now=kw.pop("now", lambda: WEDNESDAY), **kw)


# ---------- database upgrade ----------


def make_v02_database(path):
    """A database exactly as v0.1/v0.2 created it, with one candle and one fetch log row."""
    con = sqlite3.connect(path)
    con.executescript(dbmod.SCHEMA)
    con.execute("INSERT INTO candles VALUES ('yfinance','EUR/USD','H1','2026-11-20T21:00:00Z',"
                "1.1,1.2,1.0,1.15,NULL,1,'2026-11-20T21:05:00Z')")
    con.execute("INSERT INTO fetch_log (source,pair,timeframe,fetched_at,ok) "
                "VALUES ('yfinance','EUR/USD','H1','2026-11-20T21:05:00Z',1)")
    con.commit()
    assert con.execute("PRAGMA user_version").fetchone()[0] == 0
    con.close()


def test_v03_database_is_upgraded_to_schema_2_and_keeps_its_data(tmp_path):
    path = tmp_path / "v03.db"
    make_v02_database(path)
    con = sqlite3.connect(path)
    for stmt in dbmod.MIGRATIONS[1].split(";"):                    # bring it to schema 1, like v0.3
        if stmt.strip():
            con.execute(stmt)
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()
    with CandleStore(path) as s:
        assert s.schema_version == 2
        assert len(s.load("yfinance", "EUR/USD", "H1")) == 1
        assert s.load_alerts() == []


def test_v02_database_is_upgraded_and_keeps_its_data(tmp_path):
    path = tmp_path / "old.db"
    make_v02_database(path)
    with CandleStore(path) as s:
        assert s.schema_version == dbmod.SCHEMA_VERSION            # upgraded all the way
        tables = {r[0] for r in s.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"analysis_latest", "market_events", "event_state", "worker_status",     # schema 1
                "alerts", "alert_state", "settings", "brief_log"} <= tables             # schema 2
        assert len(s.load("yfinance", "EUR/USD", "H1")) == 1                 # old candle still there
        assert s.conn.execute("SELECT count(*) FROM fetch_log").fetchone()[0] == 1
        assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reopening_does_not_upgrade_twice(tmp_path):
    path = tmp_path / "db.db"
    CandleStore(path).close()
    with CandleStore(path) as s:                    # would fail with "table already exists"
        assert s.schema_version == dbmod.SCHEMA_VERSION


def test_failed_upgrade_leaves_database_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "db.db"
    CandleStore(path).close()
    broken = dbmod.SCHEMA_VERSION + 1
    monkeypatch.setitem(dbmod.MIGRATIONS, broken, "CREATE TABLE half_done (x INTEGER); THIS IS NOT SQL")
    with pytest.raises(sqlite3.OperationalError):
        CandleStore(path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == broken - 1
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='half_done'").fetchone()[0] == 0
    con.close()


# ---------- saved analysis ----------


def test_cycle_saves_analysis_that_reads_back_identically(tmp_path):
    with CandleStore(tmp_path / "db.db") as store:
        result = run_cycle(FakeProvider(), ["EUR/USD"], ["H1", "D1"], store, EventTracker(), lambda: WEDNESDAY)
        recs = {(r.pair, r.timeframe, r.kind): r for r in store.load_analysis("fake")}

    assert set(recs) == {("EUR/USD", "M1", "price"), ("EUR/USD", "H1", "trend"), ("EUR/USD", "D1", "trend"),
                         ("EUR/USD", "H1", "indicators"), ("EUR/USD", "D1", "indicators"),
                         ("EUR/USD", "H1", "structure"), ("EUR/USD", "D1", "structure")}
    assert snapshot_from(recs[("EUR/USD", "M1", "price")].payload) == result.snapshots[0]
    assert trend_from(recs[("EUR/USD", "H1", "trend")].payload) == result.view.trends[0][0]
    assert indicators_from(recs[("EUR/USD", "H1", "indicators")].payload) == result.view.indicators[0][0]
    assert structure_from(recs[("EUR/USD", "H1", "structure")].payload) == result.view.structures[0][0]
    r = recs[("EUR/USD", "H1", "indicators")]
    assert r.computed_at == WEDNESDAY and r.data_status == "OK"
    assert r.as_of == result.view.indicators[0][0].last_candle_time


def test_latest_analysis_is_replaced_not_duplicated(tmp_path):
    db = tmp_path / "db.db"
    loop(db, FakeProvider(), cycles=3)
    with CandleStore(db) as s:
        rows = s.conn.execute("SELECT count(*) FROM analysis_latest").fetchone()[0]
    assert rows == 4                                    # price, trend, indicators, structure


# ---------- events across a restart ----------


def test_change_during_downtime_is_detected_after_restart(tmp_path):
    db = tmp_path / "db.db"
    provider = FakeProvider()
    loop(db, provider)                      # run 1: flat prices, saves the tracker's memory
    provider.rising = True
    loop(db, provider)                      # run 2 (a "restart"): prices now rising
    with CandleStore(db) as s:
        messages = [e.message for e in s.load_events("fake")]
    assert "Trend SIDEWAYS -> UP" in messages
    assert "EMA 20 crossed above EMA 50" in messages
    assert "RSI entered the HIGH zone (70+) (100.0)" in messages


def test_without_saved_state_the_first_cycle_is_only_a_baseline(tmp_path):
    db = tmp_path / "db.db"
    provider = FakeProvider()
    provider.rising = True
    loop(db, provider)
    with CandleStore(db) as s:
        assert s.load_events("fake") == []


# ---------- one-worker lock ----------


def test_second_worker_is_refused_while_first_is_alive(tmp_path):
    with CandleStore(tmp_path / "db.db") as s:
        s.claim_worker(111, WEDNESDAY)
        s.worker_heartbeat(WEDNESDAY, 1, WEDNESDAY + timedelta(minutes=5))
        with pytest.raises(WorkerBusy, match="process 111"):
            s.claim_worker(222, WEDNESDAY + timedelta(minutes=6))       # within 5 min + 3 min grace
        s.claim_worker(222, WEDNESDAY + timedelta(minutes=6), force=True)
        assert s.worker_status()["pid"] == 222


def test_crashed_worker_claim_expires(tmp_path):
    with CandleStore(tmp_path / "db.db") as s:
        s.claim_worker(111, WEDNESDAY)
        s.worker_heartbeat(WEDNESDAY, 1, WEDNESDAY + timedelta(minutes=5))
        s.claim_worker(222, WEDNESDAY + timedelta(minutes=9))           # 5 + 3 grace has passed
        assert s.worker_status()["pid"] == 222


def test_worker_in_its_first_cycle_counts_as_alive(tmp_path):
    with CandleStore(tmp_path / "db.db") as s:
        s.claim_worker(111, WEDNESDAY)                                    # no heartbeat yet
        with pytest.raises(WorkerBusy):
            s.claim_worker(222, WEDNESDAY + timedelta(minutes=5))
        s.claim_worker(222, WEDNESDAY + timedelta(minutes=11))           # first-cycle allowance over


def _start_python(code: str):
    import subprocess
    import sys

    return subprocess.Popen([sys.executable, "-c", code])


@pytest.mark.real_process_check
def test_process_check_sees_running_and_finished_processes():
    import os

    assert dbmod.process_alive(os.getpid()) is True
    assert dbmod.process_alive(None) is None
    p = _start_python("import time; time.sleep(30)")
    try:
        assert dbmod.process_alive(p.pid) is True
    finally:
        p.kill()
        p.wait()
    assert dbmod.process_alive(p.pid) is False


@pytest.mark.real_process_check
def test_closed_worker_releases_its_lock_immediately(tmp_path):
    """E.g. its terminal window was closed: no Ctrl+C, so no clean release."""
    gone = _start_python("pass")
    gone.wait()
    with CandleStore(tmp_path / "db.db") as s:
        s.claim_worker(gone.pid, WEDNESDAY)
        s.worker_heartbeat(WEDNESDAY, 1, WEDNESDAY + timedelta(minutes=5))
        s.claim_worker(222, WEDNESDAY + timedelta(minutes=1))     # allowed at once, no waiting
        assert s.worker_status()["pid"] == 222


@pytest.mark.real_process_check
def test_worker_whose_process_still_runs_keeps_its_lock(tmp_path):
    alive = _start_python("import time; time.sleep(30)")
    try:
        with CandleStore(tmp_path / "db.db") as s:
            s.claim_worker(alive.pid, WEDNESDAY)
            s.worker_heartbeat(WEDNESDAY, 1, WEDNESDAY + timedelta(minutes=5))
            with pytest.raises(WorkerBusy):
                s.claim_worker(222, WEDNESDAY + timedelta(minutes=1))
    finally:
        alive.kill()
        alive.wait()


def test_loop_refuses_to_start_next_to_a_running_worker(tmp_path):
    db = tmp_path / "db.db"
    with CandleStore(db) as s:
        s.claim_worker(111, WEDNESDAY)
    fetched = []
    provider = FakeProvider()
    provider.get_candles = lambda *a: fetched.append(a)
    with pytest.raises(WorkerBusy):
        loop(db, provider)
    assert fetched == []                                                  # nothing was downloaded


def test_heartbeat_and_release_after_normal_finish(tmp_path):
    db = tmp_path / "db.db"
    loop(db, FakeProvider(), cycles=2)
    with CandleStore(db) as s:
        st = s.worker_status()
    assert st["state"] == "stopped" and st["cycles"] == 2 and st["next_run_at"] is None
    assert st["last_cycle_at"] == WEDNESDAY and st["message"] == "stopped"


def test_lock_released_after_ctrl_c_and_after_a_crash(tmp_path):
    db = tmp_path / "db.db"

    def interrupt(_):
        raise KeyboardInterrupt

    stopped = []
    assert loop(db, FakeProvider(), cycles=None, sleep=interrupt, on_stop=stopped.append) == 1
    assert stopped == [1]
    with CandleStore(db) as s:
        assert s.worker_status()["state"] == "stopped"
        assert s.worker_status()["message"] == "stopped with Ctrl+C"

    broken = FakeProvider()
    broken.get_candles = lambda *a: 1 / 0          # an unexpected bug, not a ProviderError
    with pytest.raises(ZeroDivisionError):
        loop(db, broken)
    with CandleStore(db) as s:
        assert s.worker_status()["state"] == "stopped"
        assert "error" in s.worker_status()["message"]


def test_failed_cycles_are_reported_and_backed_off(tmp_path):
    provider = FakeProvider()
    provider.fail = True
    sleeps, lines = [], []
    loop(tmp_path / "db.db", provider, cycles=3, sleep=sleeps.append,
         on_cycle=lambda info: print_cycle_line(info, lines.append))
    assert sleeps == [600.0, 1200.0]
    assert any("ALL REQUESTS FAILED" in line for line in lines)
    assert any("FAILED EUR/USD M1: source down" in line for line in lines)
    with CandleStore(tmp_path / "db.db") as s:
        assert s.load_analysis("fake") == []         # nothing saved as if it were fresh
