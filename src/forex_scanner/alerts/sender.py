"""The outbox: sends recorded alerts marked "pending" to Telegram, safely.

  - nothing is sent while dry run or pause is on
  - oldest first, at most about one message per second (Telegram's per-chat guidance)
  - Telegram unreachable or busy: wait 1, then 5, then 15 minutes (or Telegram's retry_after);
    after 3 failed attempts an alert is marked "failed" with the reason
  - wrong token / chat id / blocked bot: not retried, marked "failed" with the reason
  - an alert that has become too old to be useful is marked "expired" and NOT sent, so a
    delayed market alert can never arrive looking current (30 min for market alerts)
"""

import time
from collections.abc import Callable
from datetime import datetime, timedelta

from .settings import AlertSettings
from .telegram import TelegramClient, TelegramError, format_alert

MAX_ATTEMPTS = 3
BACKOFF_MINUTES = [1, 5, 15]
SEND_GAP_SECONDS = 1.1
EXPIRES_AFTER = {
    "unusual_move": timedelta(minutes=30),
    "market_events": timedelta(minutes=30),
    "data": timedelta(hours=6),
    "caution": timedelta(minutes=30),
    "brief": timedelta(hours=6),
    "test": timedelta(hours=1),
}
STATE = "telegram"


def deliver_pending(store, client: TelegramClient, chat_id: str, now: datetime, settings: AlertSettings,
                    sleep: Callable[[float], None] = time.sleep, max_messages: int = 10) -> list[dict]:
    """Send what is pending. Returns one result per alert handled: {id, outcome, note}."""
    if settings.dry_run or settings.paused:
        return []
    state = store.get_state(STATE, {}) or {}
    if state.get("backoff_until") and now < datetime.fromisoformat(state["backoff_until"]):
        return []                                            # Telegram had trouble: wait

    results, sent_something = [], False
    for alert in store.pending_alerts(limit=max_messages):
        age = now - alert["created_at"]
        if age > EXPIRES_AFTER.get(alert["type"], timedelta(hours=1)):
            note = f"expired: {int(age.total_seconds() // 60)} min old before it could be sent"
            store.update_alert_delivery(alert["id"], "expired", note, alert["attempts"])
            results.append({"id": alert["id"], "outcome": "expired", "note": note})
            continue
        try:
            for part in format_alert(alert["title"], alert["body"], alert["type"]):
                if sent_something:
                    sleep(SEND_GAP_SECONDS)
                client.send_message(chat_id, part)
                sent_something = True
        except TelegramError as exc:
            attempts = alert["attempts"] + 1
            if exc.retryable and attempts < MAX_ATTEMPTS:
                wait = exc.retry_after or BACKOFF_MINUTES[attempts - 1] * 60
                note = f"attempt {attempts} failed ({exc}); retrying after {wait} s"
                store.update_alert_delivery(alert["id"], "pending", note, attempts)
                state = {**state, "backoff_until": (now + timedelta(seconds=wait)).isoformat(),
                         "last_error": str(exc), "last_error_at": now.isoformat()}
                results.append({"id": alert["id"], "outcome": "retrying", "note": note})
            else:
                why = "gave up after 3 attempts" if exc.retryable else "not retried (check the bot token and chat id)"
                note = f"failed: {exc}; {why}"
                store.update_alert_delivery(alert["id"], "failed", note, attempts)
                state = {**state, "last_error": str(exc), "last_error_at": now.isoformat()}
                results.append({"id": alert["id"], "outcome": "failed", "note": note})
            store.set_state(STATE, state, now)
            break                                            # don't keep hammering a failing Telegram
        store.update_alert_delivery(alert["id"], "sent", None, alert["attempts"] + 1, sent_at=now)
        state = {**state, "backoff_until": None, "last_ok_at": now.isoformat()}
        store.set_state(STATE, state, now)
        results.append({"id": alert["id"], "outcome": "sent", "note": None})
    return results


def send_test_message(store, client: TelegramClient, chat_id: str, now: datetime, settings: AlertSettings) -> dict:
    """Send one test message right away (an explicit request, so dry run doesn't apply).
    It is recorded on the Alerts page like any other message."""
    title = "TEST MESSAGE"
    body = (f"Your Forex Scanner can reach this chat ({now.astimezone(settings.tz):%H:%M} {settings.place}).\n"
            f"Sending mode: {'dry run (nothing else is sent yet)' if settings.dry_run else 'on'}"
            f"{', PAUSED' if settings.paused else ''}.\n"
            "Learning and demo only. No trading happens here.")
    alert = {"key": f"test:{now:%Y%m%dT%H%M%S%f}", "created_at": now, "type": "test", "severity": "info",
             "title": title, "body": body, "delivery": "pending", "delivery_note": "test message"}
    alert_id = store.add_alert(alert)
    try:
        for part in format_alert(title, body, "test"):
            client.send_message(chat_id, part)
    except TelegramError as exc:
        store.update_alert_delivery(alert_id, "failed", f"failed: {exc}", 1)
        return {"ok": False, "error": str(exc), "id": alert_id}
    store.update_alert_delivery(alert_id, "sent", "test message", 1, sent_at=now)
    state = store.get_state(STATE, {}) or {}
    store.set_state(STATE, {**state, "last_ok_at": now.isoformat(), "backoff_until": None}, now)
    return {"ok": True, "error": None, "id": alert_id}
