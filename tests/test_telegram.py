"""Telegram delivery (v0.4, milestone A4), tested against a FAKE Telegram server on this PC.
Nothing here contacts the real Telegram."""

import json
import threading
from dataclasses import replace
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from forex_scanner import config
from forex_scanner.alerts import telegram
from forex_scanner.alerts.sender import deliver_pending, send_test_message
from forex_scanner.alerts.settings import AlertSettings, save_settings
from forex_scanner.alerts.telegram import TelegramClient, TelegramError, format_alert
from forex_scanner.api.app import create_app
from forex_scanner.storage import CandleStore
from forex_scanner.worker import run_loop
from tests.fakes import WEDNESDAY, FakeProvider

TOKEN = "123456:SECRET-TOKEN-abc"
SENDING = replace(AlertSettings(), dry_run=False)


class FakeTelegram:
    """Answers like the Bot API. `script` = list of responses to give, in order (default: ok)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.script: list[tuple[int, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                fake.requests.append({"path": self.path, "body": body})
                ok = (200, {"ok": True, "result": {"message_id": 1}})
                status, payload = fake.script.pop(0) if fake.script else ok
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def sent(self):
        return [r["body"]["text"] for r in self.requests if r["path"].endswith("/sendMessage")]


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "API_BASE", fake.base)
    yield fake
    fake.stop()


@pytest.fixture
def store(tmp_path):
    with CandleStore(tmp_path / "t.db") as s:
        yield s


def add(store, key="a", kind="unusual_move", minutes_ago=1, delivery="pending", title="UNUSUAL MOVE · EUR/USD · H1",
        body="range 30.0 pips = 3.0x normal"):
    return store.add_alert({"key": key, "created_at": WEDNESDAY - timedelta(minutes=minutes_ago), "type": kind,
                            "severity": "notice", "title": title, "body": body, "delivery": delivery})


# ---------- formatting ----------


def test_text_is_escaped_for_telegram_html():
    (msg,) = format_alert("A < B & C", "price > 1.1 & <script>", "unusual_move")
    assert msg == "⚡ <b>A &lt; B &amp; C</b>\nprice &gt; 1.1 &amp; &lt;script&gt;"


def test_long_messages_are_split_under_the_limit():
    parts = format_alert("BIG", "\n".join(f"line {i} " + "x" * 80 for i in range(200)), "brief")
    assert len(parts) > 1 and all(len(p) <= 4000 for p in parts)
    assert parts[1].startswith("<i>(continued)</i>")


# ---------- client ----------


def test_client_sends_the_documented_request(tg):
    TelegramClient(TOKEN).send_message("999", "<b>hi</b>")
    (r,) = tg.requests
    assert r["path"] == f"/bot{TOKEN}/sendMessage"
    assert r["body"] == {"chat_id": "999", "text": "<b>hi</b>", "parse_mode": "HTML",
                         "link_preview_options": {"is_disabled": True}}


def test_errors_never_contain_the_token(tg):
    tg.script = [(400, {"ok": False, "error_code": 400, "description": f"bad token {TOKEN}"})]
    with pytest.raises(TelegramError) as info:
        TelegramClient(TOKEN).send_message("1", "x")
    assert TOKEN not in str(info.value) and "<token>" in str(info.value)
    tg.stop()                                           # Telegram unreachable
    with pytest.raises(TelegramError) as info:
        TelegramClient(TOKEN, timeout=2).send_message("1", "x")
    assert info.value.retryable and TOKEN not in str(info.value)


# ---------- outbox ----------


def deliver(store, settings=SENDING, at=WEDNESDAY, sleeps=None):
    return deliver_pending(store, TelegramClient(TOKEN), "999", at, settings,
                           sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_nothing_is_sent_in_dry_run_or_when_paused(tg, store):
    add(store)
    assert deliver(store, AlertSettings()) == []                         # dry run (default)
    assert deliver(store, replace(SENDING, paused=True)) == []
    assert tg.sent == []


def test_pending_alerts_are_sent_oldest_first_one_per_second(tg, store):
    add(store, "old", minutes_ago=5, title="FIRST")
    add(store, "new", minutes_ago=1, title="SECOND")
    sleeps = []
    results = deliver(store, sleeps=sleeps)
    assert [r["outcome"] for r in results] == ["sent", "sent"]
    assert ["FIRST" in tg.sent[0], "SECOND" in tg.sent[1]] == [True, True]
    assert sleeps == [1.1]                                                # gap between messages
    a = store.load_alerts()[0]
    assert a["delivery"] == "sent" and a["sent_at"] == WEDNESDAY and a["attempts"] == 1


def test_flood_control_waits_as_telegram_asks(tg, store):
    add(store)
    tg.script = [(429, {"ok": False, "error_code": 429, "description": "Too Many Requests",
                        "parameters": {"retry_after": 7}})]
    (r,) = deliver(store)
    assert r["outcome"] == "retrying" and "after 7 s" in r["note"]
    assert deliver(store, at=WEDNESDAY + timedelta(seconds=5)) == []     # still waiting
    (r,) = deliver(store, at=WEDNESDAY + timedelta(seconds=8))
    assert r["outcome"] == "sent"


def test_outage_retries_then_gives_up_after_three_attempts(tg, store):
    add(store, kind="data")
    tg.script = [(502, {"ok": False, "error_code": 502, "description": "Bad Gateway"})] * 3
    at = WEDNESDAY
    outcomes = []
    for wait in (0, 61, 301):
        at += timedelta(seconds=wait)
        outcomes += [r["outcome"] for r in deliver(store, at=at)]
    assert outcomes == ["retrying", "retrying", "failed"]
    a = store.load_alerts()[0]
    assert a["delivery"] == "failed" and "gave up after 3 attempts" in a["delivery_note"]


def test_wrong_token_or_chat_is_not_retried(tg, store):
    add(store)
    tg.script = [(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})]
    (r,) = deliver(store)
    assert r["outcome"] == "failed" and "check the bot token and chat id" in r["note"]


def test_delayed_market_alert_expires_instead_of_arriving_late(tg, store):
    add(store, "stale", kind="market_events", minutes_ago=45)
    add(store, "fresh", kind="data", minutes_ago=45)                     # data alerts last longer
    by_id = {r["id"]: r["outcome"] for r in deliver(store)}
    alerts = {a["key"]: a for a in store.load_alerts()}
    assert by_id[alerts["stale"]["id"]] == "expired" and alerts["stale"]["delivery"] == "expired"
    assert by_id[alerts["fresh"]["id"]] == "sent"
    assert len(tg.sent) == 1


def test_test_message_is_sent_even_in_dry_run_and_recorded(tg, store):
    r = send_test_message(store, TelegramClient(TOKEN), "999", WEDNESDAY, AlertSettings())
    assert r["ok"] and "TEST MESSAGE" in tg.sent[0] and "dry run" in tg.sent[0]
    a = store.load_alerts()[0]
    assert a["type"] == "test" and a["delivery"] == "sent"


# ---------- API and worker ----------


@pytest.fixture
def configured(monkeypatch, tg):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    return tg


def api(db):
    return TestClient(create_app(db, source="fake", now=lambda: WEDNESDAY), base_url="http://localhost")


def test_api_status_never_reveals_the_token(tmp_path, configured):
    r = api(tmp_path / "x.db").get("/api/telegram")
    assert r.json()["configured"] is True and TOKEN not in r.text


def test_api_test_button_is_protected_and_works(tmp_path, configured):
    c = api(tmp_path / "x.db")
    assert c.post("/api/telegram/test", json={}).status_code == 403                       # no Origin
    r = c.post("/api/telegram/test", json={}, headers={"Origin": "http://localhost"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(configured.sent) == 1


def test_tests_never_see_the_real_bot_token():
    import os

    assert os.getenv("TELEGRAM_BOT_TOKEN") is None and os.getenv("TELEGRAM_CHAT_ID") is None


def test_api_test_button_when_not_set_up(tmp_path):
    r = api(tmp_path / "x.db").post("/api/telegram/test", json={}, headers={"Origin": "http://localhost"})
    assert r.status_code == 409 and "telegram-setup" in r.json()["detail"]


def test_worker_sends_pending_alerts_when_sending_is_on(tmp_path, configured, monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    db = tmp_path / "w.db"
    with CandleStore(db) as s:
        save_settings(s, SENDING, WEDNESDAY)
        add(s, "waiting", kind="data")
    infos = []
    run_loop(FakeProvider(), ["EUR/USD"], ["H1"], timedelta(minutes=5), timedelta(minutes=30), db_path=db,
             cycles=1, sleep=lambda s: None, now=lambda: WEDNESDAY, on_cycle=infos.append)
    assert infos[0].alert_error is None
    assert len(configured.sent) == 1
    with CandleStore(db) as s:
        assert s.load_alerts()[0]["delivery"] == "sent"
