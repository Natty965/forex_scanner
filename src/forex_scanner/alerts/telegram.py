"""Telegram delivery: a tiny client for the Telegram Bot API, and message formatting.

Checked against the Bot API documentation (version 10.5, 22 Nov 2026):
  - methods are called at https://api.telegram.org/bot<token>/<method>, JSON bodies allowed
  - sendMessage text: 1-4096 characters; parse_mode "HTML" needs <, > and & escaped
  - avoid more than one message per second in a single chat; over the limit Telegram
    answers 429 with parameters.retry_after (seconds to wait)
  - getUpdates doesn't work while a webhook is set

Safety:
  - the bot token comes only from .env and is NEVER included in errors or logs
  - messages go to ONE chat: TELEGRAM_CHAT_ID from .env. Incoming messages are never acted on.
  - no extra packages: Python's built-in urllib does the HTTPS calls
"""

import html
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

API_BASE = "https://api.telegram.org"
MAX_LEN = 4000                 # a little under Telegram's 4096 limit, leaving room for the header
TIMEOUT_SECONDS = 15


class TelegramError(Exception):
    """A failed Telegram call. `retryable` = trying again later may work."""

    def __init__(self, message: str, retryable: bool, retry_after: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str

    @classmethod
    def from_env(cls) -> "TelegramConfig | None":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        return cls(token, chat) if token and chat else None


def token_from_env() -> str | None:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None


def mask(text: str, token: str) -> str:
    """Remove the token from any text before it is shown or stored."""
    return text.replace(token, "<token>") if token else text


class TelegramClient:
    def __init__(self, token: str, base: str | None = None, timeout: float = TIMEOUT_SECONDS):
        self._token = token
        self.base = (base or API_BASE).rstrip("/")         # read at creation, so tests can point it elsewhere
        self.timeout = timeout

    def call(self, method: str, params: dict | None = None) -> dict | list:
        url = f"{self.base}/bot{self._token}/{method}"
        data = json.dumps(params or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                raise TelegramError(f"Telegram answered HTTP {exc.code}", retryable=exc.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TelegramError(mask(f"Telegram not reachable: {reason}", self._token), retryable=True) from None
        if not body.get("ok"):
            code = body.get("error_code")
            desc = mask(str(body.get("description", "unknown error")), self._token)
            retry_after = (body.get("parameters") or {}).get("retry_after")
            retryable = code == 429 or (isinstance(code, int) and code >= 500)
            raise TelegramError(f"Telegram error {code}: {desc}", retryable=retryable, retry_after=retry_after)
        return body["result"]

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self) -> list:
        return self.call("getUpdates", {"limit": 100, "timeout": 0})

    def send_message(self, chat_id: str, text_html: str) -> int:
        result = self.call("sendMessage", {
            "chat_id": chat_id, "text": text_html, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
        return int(result["message_id"])


# ---------- formatting ----------

ICONS = {"unusual_move": "⚡", "market_events": "🔔", "data": "⚠️", "caution": "🛑", "brief": "📋", "test": "✅"}


def format_alert(title: str, body: str, alert_type: str) -> list[str]:
    """Alert -> one or more HTML messages, each within Telegram's length limit.

    Everything from the alert is escaped, so prices or text containing < > & can't break the
    formatting. Long messages are split at line breaks.
    """
    icon = ICONS.get(alert_type, "•")
    head = f"{icon} <b>{html.escape(title)}</b>"
    lines = [html.escape(line) for line in body.splitlines()]
    parts, current = [], head
    for line in lines:
        candidate = current + "\n" + line
        if len(candidate) > MAX_LEN:
            parts.append(current)
            current = f"<i>(continued)</i>\n{line}"[:MAX_LEN]
        else:
            current = candidate
    parts.append(current)
    return parts
