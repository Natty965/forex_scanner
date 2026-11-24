"""Local candle storage in SQLite (one file, no server, built into Python).

Rules:
  - A candle is identified by (source, pair, timeframe, open time). Different
    sources are never mixed, because each quotes slightly different prices.
  - Saving the same candle twice never creates a duplicate. If its prices changed
    (it was still forming, or the source corrected it) the row is updated and
    counted as "revised"; otherwise it is left alone.
  - Every fetch attempt is logged, including failures, so we can always tell
    "last good data" apart from "the source is failing right now".
  - All times are stored as UTC text like 2026-11-20T21:45:00Z (sorts correctly).

Schema versions (PRAGMA user_version), upgraded automatically when the file is opened:
  0  v0.1/v0.2: candles, fetch_log
  1  v0.3: analysis_latest, market_events, event_state, worker_status
  2  v0.4: alerts, alert_state, settings, brief_log
Each upgrade runs in one transaction: it either completes or leaves the file unchanged.
WAL mode lets readers (the dashboard API) read while the worker writes.
"""

import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from forex_scanner.providers import CandleSet

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    source      TEXT    NOT NULL,
    pair        TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    time        TEXT    NOT NULL,              -- candle open time, UTC
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL,                          -- NULL = unknown (not zero)
    complete    INTEGER NOT NULL,              -- 1 = closed, 0 = was still forming
    fetched_at  TEXT    NOT NULL,              -- when this version was received, UTC
    PRIMARY KEY (source, pair, timeframe, time),
    CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0),
    CHECK (high >= low AND high >= open AND high >= close AND low <= open AND low <= close)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT    NOT NULL,
    pair        TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    fetched_at  TEXT    NOT NULL,
    ok          INTEGER NOT NULL,              -- 1 = success, 0 = failure
    received    INTEGER NOT NULL DEFAULT 0,
    inserted    INTEGER NOT NULL DEFAULT 0,
    revised     INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    warnings    TEXT                           -- JSON list
);
"""

MIGRATIONS: dict[int, str] = {
    1: """
    -- Latest analysis per pair/timeframe, written by the worker, read by the dashboard.
    CREATE TABLE analysis_latest (
        source       TEXT NOT NULL,
        pair         TEXT NOT NULL,
        timeframe    TEXT NOT NULL,             -- M1 for prices, else M15/H1/D1
        kind         TEXT NOT NULL,             -- price / trend / indicators
        as_of        TEXT,                      -- price time or newest closed candle used, UTC
        data_status  TEXT,                      -- status when computed (readers re-judge freshness)
        computed_at  TEXT NOT NULL,
        payload      TEXT NOT NULL,             -- JSON of the analysis result
        PRIMARY KEY (source, pair, timeframe, kind)
    );

    -- Every detected event, kept permanently (v0.2 only showed them on screen).
    CREATE TABLE market_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        source     TEXT NOT NULL,
        at         TEXT NOT NULL,
        pair       TEXT NOT NULL,
        timeframe  TEXT NOT NULL,
        kind       TEXT NOT NULL,               -- trend / ema_cross / rsi_zone
        message    TEXT NOT NULL
    );
    CREATE INDEX market_events_at ON market_events (at);

    -- What the event tracker last knew, so a restart can still detect changes.
    CREATE TABLE event_state (
        source      TEXT NOT NULL,
        pair        TEXT NOT NULL,
        timeframe   TEXT NOT NULL,
        state       TEXT NOT NULL,              -- JSON
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (source, pair, timeframe)
    );

    -- One row: is a worker running, and how is it doing? Also prevents two workers.
    CREATE TABLE worker_status (
        id             INTEGER PRIMARY KEY CHECK (id = 1),
        state          TEXT NOT NULL,           -- running / stopped
        pid            INTEGER,
        started_at     TEXT,
        heartbeat_at   TEXT,
        last_cycle_at  TEXT,
        next_run_at    TEXT,                    -- when the next cycle is due (NULL = none)
        cycles         INTEGER NOT NULL DEFAULT 0,
        message        TEXT
    );
    """,
    2: """
    -- Every alert the engine created (v0.4). `key` is the alert's identity: the same key is never
    -- stored twice, which is what prevents duplicate alerts.
    CREATE TABLE alerts (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        key            TEXT NOT NULL UNIQUE,
        created_at     TEXT NOT NULL,
        type           TEXT NOT NULL,           -- unusual_move / market_events / data / brief
        severity       TEXT NOT NULL,           -- info / notice / warning
        pair           TEXT,
        timeframe      TEXT,
        candle_time    TEXT,
        title          TEXT NOT NULL,
        body           TEXT NOT NULL,           -- plain text (Telegram formatting is added when sending)
        data           TEXT NOT NULL,           -- JSON: the numbers behind the message
        delivery       TEXT NOT NULL,           -- dry_run / held_quiet / paused / suppressed / pending / sent / failed
        delivery_note  TEXT,
        sent_at        TEXT,
        attempts       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX alerts_created ON alerts (created_at);

    -- The alert engine's memory (cooldowns, RSI zones, failure counters), as JSON values.
    CREATE TABLE alert_state (
        name        TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    -- User settings (alert preferences), as JSON values. Missing names use the defaults in code.
    CREATE TABLE settings (
        name        TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );

    -- One row per day for the daily brief (sent, late, skipped), filled in by milestone A2.
    CREATE TABLE brief_log (
        day         TEXT PRIMARY KEY,           -- local date, YYYY-MM-DD
        status      TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        alert_id    INTEGER
    );
    """,
}
SCHEMA_VERSION = max(MIGRATIONS)

PRICE_COLUMNS = ["open", "high", "low", "close"]


def to_db_time(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def from_db_time(text: str) -> pd.Timestamp:
    return pd.Timestamp(text).tz_convert("UTC")


@dataclass(frozen=True)
class AnalysisRecord:
    """One row of analysis_latest. `payload` is the JSON form of the result (see records.py)."""
    pair: str
    timeframe: str
    kind: str                 # "price", "trend" or "indicators"
    as_of: datetime | None    # price time, or newest closed candle used
    data_status: str | None   # status when computed (readers must re-judge freshness)
    computed_at: datetime
    payload: dict


class WorkerBusy(Exception):
    """Another worker (or monitor) appears to be running against this database."""


# A worker is considered alive until its next cycle is this late.
WORKER_GRACE = timedelta(minutes=3)
# While a worker has not yet finished its first cycle, it is considered alive this long.
FIRST_CYCLE_ALLOWANCE = timedelta(minutes=10)


def _dt(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text.replace("Z", "+00:00"))


def process_alive(pid: int | None) -> bool | None:
    """Is process `pid` still running on this computer? True / False, or None if we can't tell.

    Read-only: on Windows it asks for the process's exit code (never sends it anything); elsewhere
    it uses the standard "signal 0" existence check. Used to release the worker lock straight away
    when a worker was closed without Ctrl+C (e.g. its terminal window was closed).
    """
    if pid is None or pid <= 0:
        return None
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        ERROR_INVALID_PARAMETER, ERROR_ACCESS_DENIED = 87, 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            if err == ERROR_INVALID_PARAMETER:
                return False                     # no process with that id
            return True if err == ERROR_ACCESS_DENIED else None
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True)
class SaveStats:
    received: int    # candles in the fetched set
    inserted: int    # new candles
    revised: int     # existing candles whose prices/status changed
    unchanged: int   # existing candles, identical


class CandleStore:
    def __init__(self, path: Path | str, check_same_thread: bool = True):
        """`check_same_thread=False` allows the connection to be opened in one thread and closed in
        another. The API needs that: its web framework runs a request's setup and cleanup in different
        worker threads. It is safe there because each connection still serves one request at a time."""
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=10,     # wait up to 10 s if another process writes
                                    check_same_thread=check_same_thread)
        self.conn.execute("PRAGMA busy_timeout = 10000")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")     # readers don't block the writer
        self.conn.executescript(SCHEMA)
        self._upgrade()

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _upgrade(self) -> None:
        """Apply any missing schema upgrades, each in its own all-or-nothing transaction.

        Statements are split on ';', so migration SQL (including comments) must not use ';'
        anywhere except to end a statement.
        """
        for version in sorted(v for v in MIGRATIONS if v > self.schema_version):
            try:
                self.conn.execute("BEGIN")
                for statement in MIGRATIONS[version].split(";"):
                    if statement.strip():
                        self.conn.execute(statement)
                self.conn.execute(f"PRAGMA user_version = {version}")
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- writing ----------

    def save(self, cs: CandleSet) -> SaveStats:
        """Store a fetched CandleSet and log the fetch. All-or-nothing (one transaction)."""
        rows = self._rows(cs)
        existing = self._existing(cs.source, cs.pair, cs.timeframe, [r[3] for r in rows])

        new, changed = [], []
        for r in rows:
            old = existing.get(r[3])
            if old is None:
                new.append(r)
            elif old != (r[4], r[5], r[6], r[7], r[9]):   # open, high, low, close, complete
                changed.append(r)

        with self.conn:  # commits on success, rolls back on any error
            self.conn.executemany(
                "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?)", new
            )
            self.conn.executemany(
                """UPDATE candles SET open=?, high=?, low=?, close=?, volume=?, complete=?, fetched_at=?
                   WHERE source=? AND pair=? AND timeframe=? AND time=?""",
                [(*r[4:11], *r[0:4]) for r in changed],
            )
            stats = SaveStats(len(rows), len(new), len(changed), len(rows) - len(new) - len(changed))
            self._log(cs.source, cs.pair, cs.timeframe, cs.fetched_at, ok=True,
                      stats=stats, warnings=list(cs.warnings))
        return stats

    def log_failure(self, source: str, pair: str, timeframe: str, error: str,
                    fetched_at: datetime | None = None) -> None:
        with self.conn:
            self._log(source, pair, timeframe, fetched_at or datetime.now(timezone.utc),
                      ok=False, error=error)

    def _log(self, source, pair, timeframe, fetched_at, ok, stats=None, error=None, warnings=None):
        self.conn.execute(
            """INSERT INTO fetch_log (source, pair, timeframe, fetched_at, ok, received, inserted,
                                      revised, error, warnings) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (source, pair, timeframe, to_db_time(fetched_at), int(ok),
             stats.received if stats else 0, stats.inserted if stats else 0,
             stats.revised if stats else 0, error, json.dumps(warnings or [])),
        )

    @staticmethod
    def _rows(cs: CandleSet) -> list[tuple]:
        fetched = to_db_time(cs.fetched_at)
        out = []
        for ts, row in cs.candles.iterrows():
            volume = None if pd.isna(row["volume"]) else float(row["volume"])
            out.append((cs.source, cs.pair, cs.timeframe, to_db_time(ts),
                        float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]),
                        volume, int(bool(row["complete"])), fetched))
        return out

    def _existing(self, source, pair, timeframe, times) -> dict[str, tuple]:
        if not times:
            return {}
        cur = self.conn.execute(
            """SELECT time, open, high, low, close, complete FROM candles
               WHERE source=? AND pair=? AND timeframe=? AND time BETWEEN ? AND ?""",
            (source, pair, timeframe, min(times), max(times)),
        )
        return {t: (op, hi, lo, cl, comp) for t, op, hi, lo, cl, comp in cur}

    # ---------- reading ----------

    def load(self, source: str, pair: str, timeframe: str, limit: int | None = None) -> pd.DataFrame:
        """Stored candles, oldest first, UTC index. Includes each candle's fetched_at."""
        sql = """SELECT time, open, high, low, close, volume, complete, fetched_at FROM candles
                 WHERE source=? AND pair=? AND timeframe=? ORDER BY time DESC"""
        params: tuple = (source, pair, timeframe)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        df = pd.read_sql_query(sql, self.conn, params=params)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df["fetched_at"] = pd.to_datetime(df["fetched_at"], utc=True)
        df["complete"] = df["complete"].astype(bool)
        df["volume"] = df["volume"].astype(float)   # NULL -> NaN
        return df.set_index("time").sort_index()

    def summary(self) -> pd.DataFrame:
        """One row per (source, pair, timeframe): how much is stored and how the last fetch went.

        Includes items that were attempted but never succeeded (0 candles), so a
        permanently failing pair cannot silently disappear from view.
        """
        return pd.read_sql_query(
            """
            WITH items AS (
                SELECT source, pair, timeframe FROM candles
                UNION
                SELECT source, pair, timeframe FROM fetch_log
            ),
            stats AS (
                SELECT source, pair, timeframe, COUNT(*) AS candles,
                       MIN(time) AS first_candle, MAX(time) AS last_candle
                FROM candles GROUP BY source, pair, timeframe
            ),
            last_fetch AS (
                SELECT source, pair, timeframe, fetched_at, ok, error,
                       ROW_NUMBER() OVER (PARTITION BY source, pair, timeframe
                                          ORDER BY fetched_at DESC, id DESC) AS rn
                FROM fetch_log
            ),
            last_ok AS (
                SELECT source, pair, timeframe, MAX(fetched_at) AS last_ok_fetch
                FROM fetch_log WHERE ok = 1 GROUP BY source, pair, timeframe
            )
            SELECT i.source, i.pair, i.timeframe, COALESCE(s.candles, 0) AS candles,
                   s.first_candle, s.last_candle,
                   o.last_ok_fetch, f.fetched_at AS last_attempt, f.ok AS last_attempt_ok, f.error AS last_error
            FROM items i
            LEFT JOIN stats s USING (source, pair, timeframe)
            LEFT JOIN last_ok o USING (source, pair, timeframe)
            LEFT JOIN last_fetch f ON f.source = i.source AND f.pair = i.pair
                                   AND f.timeframe = i.timeframe AND f.rn = 1
            ORDER BY i.source, i.pair, i.timeframe
            """,
            self.conn,
        )

    def recent_failures(self, source: str, limit: int = 20) -> list[dict]:
        """The newest failed fetch attempts, newest first."""
        cur = self.conn.execute(
            "SELECT fetched_at, pair, timeframe, error FROM fetch_log WHERE source = ? AND ok = 0 "
            "ORDER BY fetched_at DESC, id DESC LIMIT ?", (source, limit))
        return [{"at": _dt(t), "pair": p, "timeframe": tf, "error": e} for t, p, tf, e in cur]

    def fetch_counts(self, source: str, since: datetime) -> dict:
        """How many fetch attempts succeeded / failed since `since`."""
        ok, failed = self.conn.execute(
            "SELECT COALESCE(SUM(ok = 1), 0), COALESCE(SUM(ok = 0), 0) FROM fetch_log "
            "WHERE source = ? AND fetched_at >= ?", (source, to_db_time(since))).fetchone()
        return {"ok": int(ok), "failed": int(failed)}

    # ---------- latest analysis (schema 1) ----------

    def save_analysis(self, source: str, records: list[AnalysisRecord]) -> None:
        """Replace the latest analysis rows for these pair/timeframe/kind combinations."""
        rows = [(source, r.pair, r.timeframe, r.kind, None if r.as_of is None else to_db_time(r.as_of),
                 r.data_status, to_db_time(r.computed_at), json.dumps(r.payload)) for r in records]
        with self.conn:
            self.conn.executemany(
                """INSERT INTO analysis_latest VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT (source, pair, timeframe, kind) DO UPDATE SET
                     as_of = excluded.as_of, data_status = excluded.data_status,
                     computed_at = excluded.computed_at, payload = excluded.payload""",
                rows,
            )

    def load_analysis(self, source: str, kind: str | None = None) -> list[AnalysisRecord]:
        sql = ("SELECT pair, timeframe, kind, as_of, data_status, computed_at, payload FROM analysis_latest "
               "WHERE source = ?")
        params: tuple = (source,)
        if kind is not None:
            sql += " AND kind = ?"
            params += (kind,)
        return [AnalysisRecord(p, tf, k, _dt(a), s, _dt(c), json.loads(payload))
                for p, tf, k, a, s, c, payload in self.conn.execute(sql + " ORDER BY pair, timeframe, kind", params)]

    # ---------- market events and tracker state (schema 1) ----------

    def save_events(self, source: str, events: list) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO market_events (source, at, pair, timeframe, kind, message) VALUES (?,?,?,?,?,?)",
                [(source, to_db_time(e.at), e.pair, e.timeframe, e.kind, e.message) for e in events],
            )

    def load_events(self, source: str, limit: int = 100, pair: str | None = None) -> list:
        """Most recent events first, optionally for one pair only."""
        from forex_scanner.events import MarketEvent

        sql = "SELECT at, pair, timeframe, kind, message FROM market_events WHERE source = ?"
        params: tuple = (source,)
        if pair is not None:
            sql += " AND pair = ?"
            params += (pair,)
        cur = self.conn.execute(sql + " ORDER BY at DESC, id DESC LIMIT ?", params + (limit,))
        return [MarketEvent(_dt(at), p, tf, k, m) for at, p, tf, k, m in cur]

    def save_event_states(self, source: str, states: dict, at: datetime) -> None:
        from forex_scanner.storage.records import jsonable

        with self.conn:
            self.conn.executemany(
                """INSERT INTO event_state VALUES (?,?,?,?,?)
                   ON CONFLICT (source, pair, timeframe) DO UPDATE SET
                     state = excluded.state, updated_at = excluded.updated_at""",
                [(source, pair, tf, json.dumps(jsonable(st)), to_db_time(at)) for (pair, tf), st in states.items()],
            )

    def load_event_states(self, source: str) -> dict:
        from forex_scanner.storage.records import state_from

        cur = self.conn.execute("SELECT pair, timeframe, state FROM event_state WHERE source = ?", (source,))
        return {(p, tf): state_from(json.loads(s)) for p, tf, s in cur}

    # ---------- alerts, alert state, settings (schema 2) ----------

    def add_alert(self, a: dict) -> int | None:
        """Store an alert. Returns its id, or None if an alert with the same key already exists
        (that is how duplicates are prevented)."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO alerts (key, created_at, type, severity, pair, timeframe, candle_time,
                                                 title, body, data, delivery, delivery_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (a["key"], to_db_time(a["created_at"]), a["type"], a["severity"], a.get("pair"), a.get("timeframe"),
                 None if a.get("candle_time") is None else to_db_time(a["candle_time"]),
                 a["title"], a["body"], json.dumps(a.get("data", {})), a["delivery"], a.get("delivery_note")))
            return cur.lastrowid if cur.rowcount else None

    def pending_alerts(self, limit: int = 10) -> list[dict]:
        """Alerts waiting to be sent, oldest first."""
        return [a for a in reversed(self.load_alerts(limit=500)) if a["delivery"] == "pending"][:limit]

    def update_alert_delivery(self, alert_id: int, delivery: str, note: str | None, attempts: int,
                              sent_at: datetime | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE alerts SET delivery = ?, delivery_note = ?, attempts = ?, sent_at = ? WHERE id = ?",
                (delivery, note, attempts, None if sent_at is None else to_db_time(sent_at), alert_id))

    def alert_exists(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM alerts WHERE key = ?", (key,)).fetchone() is not None

    def count_alerts(self, alert_type: str, since: datetime,
                     exclude_delivery: tuple[str, ...] = ("suppressed",)) -> int:
        marks = ",".join("?" * len(exclude_delivery))
        return self.conn.execute(
            f"SELECT count(*) FROM alerts WHERE type = ? AND created_at >= ? AND delivery NOT IN ({marks})",
            (alert_type, to_db_time(since), *exclude_delivery)).fetchone()[0]

    def load_alerts(self, limit: int = 100, alert_type: str | None = None) -> list[dict]:
        """Newest first."""
        sql = ("SELECT id, key, created_at, type, severity, pair, timeframe, candle_time, title, body, data, "
               "delivery, delivery_note, sent_at, attempts FROM alerts")
        params: tuple = ()
        if alert_type is not None:
            sql += " WHERE type = ?"
            params = (alert_type,)
        cur = self.conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", params + (limit,))
        keys = ["id", "key", "created_at", "type", "severity", "pair", "timeframe", "candle_time", "title", "body",
                "data", "delivery", "delivery_note", "sent_at", "attempts"]
        out = []
        for row in cur:
            d = dict(zip(keys, row, strict=True))
            for k in ("created_at", "candle_time", "sent_at"):
                d[k] = _dt(d[k])
            d["data"] = json.loads(d["data"])
            out.append(d)
        return out

    def get_state(self, name: str, default=None):
        row = self.conn.execute("SELECT value FROM alert_state WHERE name = ?", (name,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_state(self, name: str, value, at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO alert_state VALUES (?,?,?) ON CONFLICT (name) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (name, json.dumps(value), to_db_time(at)))

    def get_brief_log(self, day: str) -> dict | None:
        row = self.conn.execute("SELECT day, status, created_at, alert_id FROM brief_log WHERE day = ?",
                                (day,)).fetchone()
        return None if row is None else {"day": row[0], "status": row[1], "created_at": _dt(row[2]),
                                         "alert_id": row[3]}

    def recent_brief_log(self, limit: int = 14) -> list[dict]:
        cur = self.conn.execute("SELECT day, status, created_at, alert_id FROM brief_log ORDER BY day DESC LIMIT ?",
                                (limit,))
        return [{"day": d, "status": st, "created_at": _dt(c), "alert_id": a} for d, st, c, a in cur]

    def set_brief_log(self, day: str, status: str, at: datetime, alert_id: int | None = None) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO brief_log VALUES (?,?,?,?)",
                              (day, status, to_db_time(at), alert_id))

    def get_setting(self, name: str, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_setting(self, name: str, value, at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings VALUES (?,?,?) ON CONFLICT (name) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (name, json.dumps(value), to_db_time(at)))

    # ---------- worker status and lock (schema 1) ----------

    def worker_status(self) -> dict | None:
        row = self.conn.execute(
            "SELECT state, pid, started_at, heartbeat_at, last_cycle_at, next_run_at, cycles, message "
            "FROM worker_status WHERE id = 1").fetchone()
        if row is None:
            return None
        keys = ["state", "pid", "started_at", "heartbeat_at", "last_cycle_at", "next_run_at", "cycles", "message"]
        out = dict(zip(keys, row, strict=True))
        for k in ("started_at", "heartbeat_at", "last_cycle_at", "next_run_at"):
            out[k] = _dt(out[k])
        return out

    @staticmethod
    def worker_alive(status: dict | None, now: datetime) -> bool:
        """Does `status` describe a worker that should still be running at `now`?"""
        if status is None or status["state"] != "running":
            return False
        if process_alive(status["pid"]) is False:   # its process is gone: closed or crashed
            return False
        if status["next_run_at"] is not None:
            return now <= status["next_run_at"] + WORKER_GRACE
        last = status["heartbeat_at"] or status["started_at"]
        return last is not None and now <= last + FIRST_CYCLE_ALLOWANCE

    def claim_worker(self, pid: int, now: datetime, force: bool = False) -> None:
        """Mark this process as THE worker. Raises WorkerBusy if another one looks alive.

        If the previous worker's process no longer exists, its claim is released immediately.
        If that can't be determined, a crashed worker's claim still expires on its own, because it
        stops refreshing next_run_at.
        """
        self.conn.execute("BEGIN IMMEDIATE")      # nobody else can claim at the same moment
        try:
            status = self.worker_status()
            if not force and self.worker_alive(status, now):
                raise WorkerBusy(
                    f"Another worker or monitor (process {status['pid']}) is already running on this "
                    f"database, started {status['started_at']:%Y-%m-%d %H:%M} UTC. Running two would fetch "
                    "everything twice. Stop it first, or use --force if you are sure it is not running.")
            self.conn.execute(
                """INSERT INTO worker_status (id, state, pid, started_at, heartbeat_at, last_cycle_at, next_run_at,
                                              cycles, message) VALUES (1, 'running', ?, ?, ?, NULL, NULL, 0, NULL)
                   ON CONFLICT (id) DO UPDATE SET state='running', pid=excluded.pid,
                     started_at=excluded.started_at, heartbeat_at=excluded.heartbeat_at,
                     last_cycle_at=NULL, next_run_at=NULL, cycles=0, message=NULL""",
                (pid, to_db_time(now), to_db_time(now)))
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def worker_heartbeat(self, now: datetime, cycles: int, next_run_at: datetime | None,
                         message: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE worker_status SET heartbeat_at = ?, last_cycle_at = ?, next_run_at = ?, cycles = ?,
                                            message = ? WHERE id = 1""",
                (to_db_time(now), to_db_time(now), None if next_run_at is None else to_db_time(next_run_at),
                 cycles, message))

    def release_worker(self, now: datetime, message: str = "stopped") -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE worker_status SET state = 'stopped', heartbeat_at = ?, next_run_at = NULL, message = ? "
                "WHERE id = 1", (to_db_time(now), message))
