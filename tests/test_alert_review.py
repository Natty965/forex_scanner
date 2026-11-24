"""A5 preparation: the rollover-candle switch and the weekly review report."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from forex_scanner.alerts import rules
from forex_scanner.alerts.review import format_review, review
from forex_scanner.alerts.settings import AlertSettings
from forex_scanner.providers import CandleSet
from forex_scanner.storage import CandleStore

UTC = timezone.utc
S = AlertSettings()


def m15_set_ending_with(last_open: datetime, last_range_pips=30.0, n=200):
    idx = pd.date_range(end=pd.Timestamp(last_open), periods=n, freq="15min")
    close = np.full(n, 1.1)
    half = np.full(n, 0.0002)
    half[-1] = last_range_pips * 0.0001 / 2
    df = pd.DataFrame({"open": close, "high": close + half, "low": close - half, "close": close,
                       "volume": np.nan, "complete": True}, index=idx)
    return {("EUR/USD", "M15"): CandleSet("EUR/USD", "M15", "fake", last_open, df, False)}


def test_rollover_candle_detection_follows_new_york_time():
    assert rules.is_rollover_candle(datetime(2026, 11, 24, 22, 0, tzinfo=UTC))       # winter: 17:00 NY
    assert rules.is_rollover_candle(datetime(2026, 7, 7, 21, 0, tzinfo=UTC))         # summer: 17:00 NY
    assert not rules.is_rollover_candle(datetime(2026, 11, 24, 22, 15, tzinfo=UTC))


def test_rollover_switch():
    rollover = datetime(2026, 11, 24, 22, 0, tzinfo=UTC)                              # Tuesday
    now = rollover + timedelta(minutes=16)
    sets, statuses = m15_set_ending_with(rollover), {("EUR/USD", "M15"): "OK"}
    assert len(rules.unusual_ranges(sets, statuses, now, S)) == 1                      # off by default
    assert rules.unusual_ranges(sets, statuses, now, replace(S, skip_rollover_candle=True)) == []
    normal = datetime(2026, 11, 24, 14, 0, tzinfo=UTC)
    assert len(rules.unusual_ranges(m15_set_ending_with(normal), statuses, normal + timedelta(minutes=16),
                                    replace(S, skip_rollover_candle=True))) == 1      # other candles unaffected


def test_review_counts_and_suggests_without_changing_anything(tmp_path):
    now = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)                                     # Friday
    with CandleStore(tmp_path / "r.db") as store:
        n = 0
        for day in range(3):                                                             # Tue-Thu
            for hour, rollover in ((22, True), (14, False)):
                created = datetime(2026, 11, 24 + day, hour, 16, tzinfo=UTC)
                store.add_alert({"key": f"u{n}", "created_at": created, "type": "unusual_move", "severity": "notice",
                                 "pair": "GBP/USD", "timeframe": "M15", "title": "UNUSUAL", "body": "...",
                                 "candle_time": created - timedelta(minutes=16),
                                 "delivery": "held_quiet" if rollover else "dry_run"})
                n += 1
        store.add_alert({"key": "ev", "created_at": now - timedelta(hours=2), "type": "market_events",
                         "severity": "info", "title": "EVENTS", "body": "...", "delivery": "dry_run",
                         "data": {"lines": ["EUR/USD H1: Trend UP -> SIDEWAYS", "USD/JPY D1: RSI left the LOW zone"]}})
        store.add_alert({"key": "t", "created_at": now, "type": "test", "severity": "info", "title": "TEST",
                         "body": "...", "delivery": "sent"})
        r = review(store, now, S, days=7)
        assert r["total"] == 7                                      # the test message isn't counted
        assert r["sendable"] == 4                                   # held ones wouldn't be sent immediately
        assert r["unusual_rollover"] == 3
        assert r["event_kinds"] == {"trend change": 1, "RSI zone": 1}
        assert any("rollover" in x for x in r["suggestions"])
        text = format_review(r, S)
        assert "Would have been sent (if sending were on): 4" in text and "nothing is changed" in text
        from forex_scanner.alerts.settings import load_settings
        assert load_settings(store) == AlertSettings()              # review changed nothing
