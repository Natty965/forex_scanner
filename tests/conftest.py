"""Shared pytest settings."""

import pytest

from forex_scanner.storage import db as dbmod


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """Tests must never see (or use) the real bot token from .env. Tests that need Telegram
    set fake values themselves and point the client at a fake server on this PC."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


@pytest.fixture(autouse=True)
def unknown_process_liveness(request, monkeypatch):
    """Most lock tests use made-up process ids (111, 222...). Make the process check answer
    'unknown' for them so they exercise the time-based rules. Tests marked
    `real_process_check` use real processes and the real check."""
    if "real_process_check" not in request.keywords:
        monkeypatch.setattr(dbmod, "process_alive", lambda pid: None)
