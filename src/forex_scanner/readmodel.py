"""Read side: turn what the worker saved into JSON-ready views for the API (and later Telegram).

Nothing here fetches market data. Freshness is always judged at the moment of the request:
  - prices: the same LIVE / DELAYED / STALE / MARKET CLOSED rule as the terminal (status_at)
  - trend and indicator results: STALE if the worker has not refreshed them recently
  - the worker itself: RUNNING / STOPPED / NOT RESPONDING / NEVER STARTED
So a stopped worker can never leave the dashboard showing old data as current.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

from forex_scanner import __version__, config
from forex_scanner.analysis.snapshot import status_at
from forex_scanner.analysis.trend import DATA_MARKET_CLOSED, DATA_OK, DATA_STALE
from forex_scanner.display.formatting import candle_label
from forex_scanner.market import is_market_closed, next_open, pip_size
from forex_scanner.storage import AnalysisRecord, CandleStore
from forex_scanner.storage.records import indicators_from, jsonable, snapshot_from, structure_from, trend_from

# Trend/indicator results older than this are not current: the worker normally refreshes
# them every MONITOR_INTERVAL_MINUTES, so this allows two missed cycles plus a margin.
ANALYSIS_STALE_AFTER = timedelta(minutes=2 * config.MONITOR_INTERVAL_MINUTES + 3)

WORKER_RUNNING, WORKER_STOPPED = "RUNNING", "STOPPED"
WORKER_NOT_RESPONDING, WORKER_NEVER_STARTED = "NOT RESPONDING", "NEVER STARTED"


# ---------- pair ids for URLs ("EUR/USD" <-> "EURUSD") ----------

def pair_id(pair: str) -> str:
    return pair.replace("/", "")


def pair_from_id(pid: str) -> str | None:
    return next((p for p in config.PAIRS if pair_id(p) == pid.upper()), None)


# ---------- freshness ----------

def analysis_status(rec: AnalysisRecord, now: datetime) -> str:
    """OK / STALE / MARKET CLOSED for a saved trend or indicator result, judged at `now`."""
    if is_market_closed(now):
        return DATA_MARKET_CLOSED
    if now - rec.computed_at > ANALYSIS_STALE_AFTER:
        return DATA_STALE
    return rec.data_status if rec.data_status in (DATA_OK, DATA_STALE) else DATA_STALE


def worker_info(store: CandleStore, now: datetime) -> dict:
    st = store.worker_status()
    if st is None:
        label = WORKER_NEVER_STARTED
    elif CandleStore.worker_alive(st, now):
        label = WORKER_RUNNING
    elif st["state"] == "running":
        label = WORKER_NOT_RESPONDING          # claimed to run but missed its schedule (crashed or asleep)
    else:
        label = WORKER_STOPPED
    out = {"label": label, "alive": label == WORKER_RUNNING}
    if st is not None:
        out |= {k: st[k] for k in ("pid", "started_at", "last_cycle_at", "next_run_at", "cycles", "message")}
    return jsonable(out)


def _ago(now: datetime, then: datetime | None) -> float | None:
    return None if then is None else max((now - then).total_seconds(), 0.0)


# ---------- views ----------

def price_view(rec: AnalysisRecord, now: datetime) -> dict:
    snap = snapshot_from(rec.payload)
    status, age = status_at(snap, now, config.LIVE_MAX_AGE_MINUTES, config.DELAYED_MAX_AGE_MINUTES)
    return jsonable({
        "pair": snap.pair, "price": snap.price, "price_time": snap.price_time,
        "age_seconds": age.total_seconds(), "status": status,
        "change_1h": snap.change_1h, "change_24h": snap.change_24h,
        "computed_at": rec.computed_at,
    })


def _analysis_common(rec: AnalysisRecord, now: datetime) -> dict:
    return {"timeframe": rec.timeframe, "status": analysis_status(rec, now),
            "as_of": rec.as_of, "as_of_label": candle_label(rec.as_of, rec.timeframe),
            "computed_at": rec.computed_at, "computed_seconds_ago": _ago(now, rec.computed_at)}


def trend_view(rec: AnalysisRecord, now: datetime, detailed: bool = False) -> dict:
    t = trend_from(rec.payload)
    out = _analysis_common(rec, now) | {
        "direction": t.direction, "move_pips": t.move_pips, "move_ranges": t.move_ranges,
        "distance_pips": t.distance_pips, "candles_used": t.candles_used,
    }
    if detailed:
        out |= {"last_close": t.last_close, "average": t.average, "reasons": t.reasons}
    return jsonable(out)


def indicators_view(rec: AnalysisRecord, now: datetime, detailed: bool = False) -> dict:
    s = indicators_from(rec.payload)
    out = _analysis_common(rec, now) | {
        "close": s.close, "emas": s.emas, "distance_pips": {p: s.distance_pips(p) for p in s.emas},
        "alignment": s.alignment, "rsi": s.rsi, "rsi_zone": s.rsi_zone,
        "atr": s.atr, "atr_pips": s.atr_pips, "closed_candles": s.closed_candles,
    }
    if detailed:
        out |= {"reasons": s.reasons}
    return jsonable(out)


def notable_conditions(pairs: list[dict], worker: dict) -> list[dict]:
    """Short list of things worth noticing, each with a plain-English 'why'.

    Uses only CURRENT data: stale results are reported as a data problem, never as a market
    condition. These describe the market; they are not signals.
    """
    out: list[dict] = []
    if not worker["alive"]:
        out.append({"pair": None, "timeframe": None, "kind": "data", "level": "warning",
                    "text": f"Worker {worker['label'].lower()}: nothing is being updated",
                    "why": "Prices and analysis come from the worker. Without it they get older and are "
                           "marked STALE. Start it with: python -m forex_scanner run"})
    for p in pairs:
        price = p["price"]
        if price is not None and price["status"] in ("DELAYED", "STALE"):
            mins = round(price["age_seconds"] / 60)
            out.append({"pair": p["pair"], "timeframe": None, "kind": "data", "level": "warning",
                        "text": f"Price {price['status']} ({mins} min old)",
                        "why": "The data source has not delivered a new price recently. "
                               "Don't treat this price as current."})
        for tf, v in p["timeframes"].items():
            ind = v["indicators"]
            if ind is None or ind["status"] != "OK" or ind["rsi_zone"] not in ("HIGH", "LOW"):
                continue
            high = ind["rsi_zone"] == "HIGH"
            out.append({
                "pair": p["pair"], "timeframe": tf, "kind": "rsi_zone", "level": "notice",
                "text": f"RSI {ind['rsi']:.1f}: {'HIGH zone (70+)' if high else 'LOW zone (30 or less)'}",
                "why": (f"Recent {'up' if high else 'down'}-moves have been much larger than "
                        f"{'down' if high else 'up'}-moves (often called "
                        f"'{'overbought' if high else 'oversold'}'). This is not a signal: in strong "
                        f"trends RSI can stay {'high' if high else 'low'} for a long time."),
            })
    return out


def _by_key(records: list[AnalysisRecord]) -> dict[tuple[str, str, str], AnalysisRecord]:
    return {(r.pair, r.timeframe, r.kind): r for r in records}


def status_view(store: CandleStore, source: str, now: datetime) -> dict:
    closed = is_market_closed(now)
    return jsonable({
        "server_time": now, "version": __version__, "source": source,
        "market": {"open": not closed, "next_open": next_open(now) if closed else None},
        "worker": worker_info(store, now),
        "schema_version": store.schema_version,
        "pairs": [{"pair": p, "id": pair_id(p)} for p in config.PAIRS],
        "timeframes": config.TIMEFRAMES,
        "thresholds": {"live_max_minutes": config.LIVE_MAX_AGE_MINUTES,
                       "delayed_max_minutes": config.DELAYED_MAX_AGE_MINUTES,
                       "analysis_stale_after_minutes": ANALYSIS_STALE_AFTER.total_seconds() / 60},
    })


def stored_trading_conditions(store: CandleStore, source: str, now: datetime) -> list:
    """Current trading conditions (alerts/conditions.py) from what the worker saved.
    Volatility uses a pair's stored H1 candles only while its H1 analysis is current (OK)."""
    from forex_scanner.alerts.conditions import trading_conditions
    from forex_scanner.alerts.settings import load_settings

    recs = _by_key(store.load_analysis(source))
    price_status, h1 = {}, {}
    for p in config.PAIRS:
        price = recs.get((p, "M1", "price"))
        if price is not None:
            price_status[p] = status_at(snapshot_from(price.payload), now, config.LIVE_MAX_AGE_MINUTES,
                                        config.DELAYED_MAX_AGE_MINUTES)[0]
        trend = recs.get((p, "H1", "trend"))
        if trend is not None and analysis_status(trend, now) == DATA_OK:
            h1[p] = store.load(source, p, "H1", limit=config.INDICATOR_HISTORY)
    return trading_conditions(now, h1, price_status, load_settings(store).volatility_threshold)


def trading_conditions_view(conditions: list) -> list[dict]:
    return [jsonable({"kind": c.kind, "level": c.level, "pair": c.pair, "text": c.text, "why": c.why,
                      "data": c.data}) for c in conditions]


def overview_view(store: CandleStore, source: str, now: datetime) -> dict:
    """Everything the Overview page needs, in one response."""
    recs = _by_key(store.load_analysis(source))
    pairs = []
    for p in config.PAIRS:
        price = recs.get((p, "M1", "price"))
        tfs = {}
        for tf in config.TIMEFRAMES:
            t, i = recs.get((p, tf, "trend")), recs.get((p, tf, "indicators"))
            tfs[tf] = {"trend": trend_view(t, now) if t else None,
                       "indicators": indicators_view(i, now) if i else None}
        pairs.append({"pair": p, "id": pair_id(p), "price": price_view(price, now) if price else None,
                      "timeframes": tfs})
    worker = worker_info(store, now)
    closed = is_market_closed(now)
    return jsonable({"server_time": now, "source": source, "worker": worker,
                     "market": {"open": not closed, "next_open": next_open(now) if closed else None},
                     "pairs": pairs, "conditions": notable_conditions(pairs, worker),
                     "trading_conditions": trading_conditions_view(stored_trading_conditions(store, source, now)),
                     "events": [jsonable(e) for e in store.load_events(source, limit=10)]})


def structure_view(rec: AnalysisRecord, now: datetime) -> dict:
    """Saved swing structure and support/resistance levels (v0.5 B2), with freshness judged now."""
    s = structure_from(rec.payload)
    return jsonable(_analysis_common(rec, now) | {
        "close": s.close, "atr_pips": None if s.atr is None else s.atr / pip_size(s.pair),
        "structure": s.structure, "swings": s.swings, "last_high": s.last_high, "last_low": s.last_low,
        "resistance": s.resistance, "support": s.support, "at_price": s.at_price,
        "candles_used": s.candles_used, "reasons": s.reasons,
    })


def pair_analysis_view(store: CandleStore, source: str, pair: str, timeframe: str, now: datetime) -> dict:
    recs = _by_key(store.load_analysis(source))
    price, t, i = (recs.get((pair, "M1", "price")), recs.get((pair, timeframe, "trend")),
                   recs.get((pair, timeframe, "indicators")))
    st = recs.get((pair, timeframe, "structure"))
    return jsonable({
        "server_time": now, "pair": pair, "id": pair_id(pair), "timeframe": timeframe,
        "price": price_view(price, now) if price else None,
        "trend": trend_view(t, now, detailed=True) if t else None,
        "indicators": indicators_view(i, now, detailed=True) if i else None,
        "structure": structure_view(st, now) if st else None,
    })


def candles_view(store: CandleStore, source: str, pair: str, timeframe: str, limit: int) -> dict:
    """Stored candles, oldest first. `complete` False = the candle was still forming when saved."""
    df = store.load(source, pair, timeframe, limit=limit)
    candles = [{"time": ts, "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "complete": bool(r.complete), "fetched_at": r.fetched_at}
               for ts, r in zip(df.index, df.itertuples(), strict=True)]
    return jsonable({"pair": pair, "id": pair_id(pair), "timeframe": timeframe, "source": source,
                     "count": len(candles), "candles": candles,
                     "note": "Stored history, oldest first. Volume is not available from this source."})


def chart_view(store: CandleStore, source: str, pair: str, timeframe: str, limit: int, now: datetime) -> dict:
    """Candles plus EMA / RSI / ATR lines for a chart, from stored history.

    Same functions and rules as the CLI: indicators use CLOSED candles only, and a line has no
    points until its warm-up is complete (never a number that merely looks accurate). They are
    calculated over all stored history, then the newest `limit` candles are returned.
    The still-forming candle (if any) is included but flagged, and has no indicator values.
    Times are Unix seconds (UTC), as the chart library expects.
    """
    import pandas as pd

    from forex_scanner.analysis.indicators import atr, drop_warmup, ema, rsi, warmup_candles

    df = store.load(source, pair, timeframe)
    closed = df[df["complete"]]
    pip = pip_size(pair)

    def points(series: pd.Series, scale: float = 1.0) -> list[dict]:
        tail = series.dropna()
        tail = tail[tail.index >= window_start] if window_start is not None else tail
        return [{"time": int(ts.timestamp()), "value": float(v) / scale} for ts, v in tail.items()]

    window = df.tail(limit)
    window_start = window.index[0] if len(window) else None

    lines: dict[str, list[dict]] = {}
    warmup: dict[str, dict] = {}

    def add(name: str, kind: str, period: int, values: pd.Series, scale: float = 1.0) -> None:
        need = warmup_candles(kind, period)
        warmup[name] = {"needed": need, "available": len(closed), "ok": len(closed) >= need}
        lines[name] = points(drop_warmup(values, need), scale) if len(closed) else []

    if len(closed):
        for p in config.EMA_PERIODS:
            add(f"ema{p}", "ema", p, ema(closed["close"], p))
        add(f"rsi{config.RSI_PERIOD}", "rsi", config.RSI_PERIOD, rsi(closed["close"], config.RSI_PERIOD))
        add(f"atr{config.ATR_PERIOD}", "atr", config.ATR_PERIOD, atr(closed, config.ATR_PERIOD), scale=pip)

    candles = [{"time": int(ts.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "complete": bool(r.complete)} for ts, r in zip(window.index, window.itertuples(), strict=True)]
    structure = chart_structure(df, pair, timeframe, window_start)
    rec = next((r for r in store.load_analysis(source, kind="indicators")
                if r.pair == pair and r.timeframe == timeframe), None)
    return jsonable({
        "pair": pair, "id": pair_id(pair), "timeframe": timeframe, "source": source,
        "status": analysis_status(rec, now) if rec else None,
        "stored_candles": len(df), "closed_candles": len(closed), "count": len(candles),
        "candles": candles, "lines": lines, "warmup": warmup, "structure": structure,
        "atr_unit": "pips", "time_zone": "UTC",
        "note": "Indicator lines use closed candles only and start once their warm-up is complete.",
    })


def chart_structure(df, pair: str, timeframe: str, window_start) -> dict | None:
    """Swing points and levels for the chart, from the SAME stored candles it draws (same functions
    as the worker and the `structure` command). Swings are those inside the chart window."""
    from forex_scanner.analysis.structure import analyse_structure, find_swings, label_swings

    if df.empty:
        return None
    s = analyse_structure(df, pair, timeframe, config.ATR_PERIOD)
    if s.atr is None:
        return {"structure": s.structure, "swings": [], "levels": [], "reasons": s.reasons}
    closed = df[df["complete"].astype(bool)].tail(s.candles_used)
    swings = label_swings(find_swings(closed), 0.1 * s.atr)
    start = int(window_start.timestamp()) if window_start is not None else 0
    return {
        "structure": s.structure,
        "swings": [{"time": int(x.time.timestamp()), "kind": x.kind, "price": x.price, "label": x.label}
                   for x in swings if int(x.time.timestamp()) >= start],
        "levels": [jsonable(lv) for lv in (*s.resistance, *s.at_price, *s.support)],
        "reasons": [],
    }


MONITORED_TIMEFRAMES = ["M1", *config.TIMEFRAMES]   # what the worker refreshes every cycle


def settings_view() -> dict:
    """Current configuration, read-only, with where to change it."""
    return {
        "where_to_change": "src/forex_scanner/config.py (and .env for the data source / database "
                           "location), then restart the worker",
        "groups": [
            {"name": "Data", "items": [
                {"key": "Data source", "value": config.DATA_PROVIDER, "note": "DATA_PROVIDER in .env"},
                {"key": "Pairs", "value": ", ".join(config.PAIRS)},
                {"key": "Timeframes", "value": ", ".join(config.TIMEFRAMES),
                 "note": "plus 1-minute candles for prices"},
                {"key": "Database", "value": str(config.DB_PATH), "note": "FOREX_DB_PATH in .env"},
            ]},
            {"name": "Worker", "items": [
                {"key": "Refresh while market open", "value": f"every {config.MONITOR_INTERVAL_MINUTES:g} min"},
                {"key": "Refresh while market closed",
                 "value": f"every {config.MONITOR_CLOSED_INTERVAL_MINUTES:g} min"},
                {"key": "Pause between requests", "value": f"{config.REQUEST_DELAY_SECONDS:g} s"},
                {"key": "Retries after a temporary failure", "value": str(config.MAX_RETRIES)},
            ]},
            {"name": "Freshness", "items": [
                {"key": "LIVE", "value": f"price at most {config.LIVE_MAX_AGE_MINUTES:g} min old"},
                {"key": "DELAYED", "value": f"up to {config.DELAYED_MAX_AGE_MINUTES:g} min old"},
                {"key": "Analysis counts as STALE after",
                 "value": f"{ANALYSIS_STALE_AFTER.total_seconds() / 60:g} min without a worker update"},
            ]},
            {"name": "Analysis", "items": [
                {"key": "Trend", "value": f"last {config.TREND_LOOKBACK} closed candles, "
                                          f"UP/DOWN at {config.TREND_THRESHOLD:g} average ranges"},
                {"key": "EMAs", "value": ", ".join(str(p) for p in config.EMA_PERIODS)},
                {"key": "RSI / ATR periods", "value": f"{config.RSI_PERIOD} / {config.ATR_PERIOD}"},
                {"key": "EMA crossing events", "value": f"EMA {config.EMA_CROSS[0]} vs EMA {config.EMA_CROSS[1]}"},
                {"key": "Candles fetched for indicators", "value": f"{config.INDICATOR_HISTORY} (D1: all available)"},
            ]},
        ],
    }


def data_health_view(store: CandleStore, source: str, now: datetime) -> dict:
    """Is data arriving? What's stored? What has failed recently? Plus the current settings."""
    summary = store.summary()
    items = []
    for row in summary.itertuples():
        if row.source != source:
            continue
        last_ok = _parse(row.last_ok_fetch)
        attempt_ok = None if _missing(row.last_attempt_ok) else bool(row.last_attempt_ok)
        monitored = row.timeframe in MONITORED_TIMEFRAMES and row.pair in config.PAIRS
        if attempt_ok is False:
            health = "FAILING"
        elif not monitored:
            health = "NOT MONITORED"          # e.g. saved once with `fetch`; the worker doesn't refresh it
        elif last_ok is None or now - last_ok > ANALYSIS_STALE_AFTER:
            health = "NOT UPDATING"
        else:
            health = "OK"
        items.append({
            "pair": row.pair, "timeframe": row.timeframe, "candles": int(row.candles),
            "first_candle": _parse(row.first_candle), "last_candle": _parse(row.last_candle),
            "last_ok_fetch": last_ok, "last_ok_seconds_ago": _ago(now, last_ok),
            "last_attempt": _parse(row.last_attempt), "last_attempt_ok": attempt_ok,
            "last_error": None if _missing(row.last_error) else row.last_error,
            "health": health, "monitored": monitored,
        })
    db_file = Path(store.path)
    size = sum(p.stat().st_size for p in (db_file, db_file.with_name(db_file.name + "-wal")) if p.exists())
    return jsonable({
        "server_time": now, "source": source, "worker": worker_info(store, now),
        "database": {"path": str(db_file), "size_bytes": size, "schema_version": store.schema_version},
        "fetches_24h": store.fetch_counts(source, now - timedelta(hours=24)),
        "items": items,
        "recent_failures": store.recent_failures(source, limit=20),
        "settings": settings_view(),
    })


ALERT_DELIVERY_MEANING = {
    "dry_run": "Recorded only: dry run is on, nothing is sent.",
    "held_quiet": "Arrived during quiet hours: held for the morning summary.",
    "paused": "Recorded while sending was paused.",
    "suppressed": "Not sent: today's limit for this type was reached.",
    "pending": "Waiting to be sent to Telegram (needs Telegram set up, and dry run and pause off).",
    "sent": "Sent to Telegram.",
    "failed": "Sending failed (see the note).",
    "expired": "Not sent: it was too old to be useful by the time it could be sent.",
}


def telegram_view(store: CandleStore) -> dict:
    """Is Telegram set up, and how did the last sends go? Never includes the token."""
    from forex_scanner.alerts.telegram import TelegramConfig, token_from_env

    cfg = TelegramConfig.from_env()
    st = store.get_state("telegram", {}) or {}
    return {
        "configured": cfg is not None,
        "token_set": token_from_env() is not None,
        "chat_set": bool(os.getenv("TELEGRAM_CHAT_ID", "").strip()),
        "last_ok_at": st.get("last_ok_at"),
        "last_error": st.get("last_error"),
        "last_error_at": st.get("last_error_at"),
        "backoff_until": st.get("backoff_until"),
    }


def alerts_view(store: CandleStore, now: datetime, limit: int = 200, alert_type: str | None = None,
                delivery: str | None = None) -> dict:
    """Recorded alerts (newest first), the current sending mode, and recent daily-brief outcomes."""
    from forex_scanner.alerts.settings import load_settings

    s = load_settings(store)
    alerts = store.load_alerts(limit=5000 if delivery else limit, alert_type=alert_type)
    if delivery:
        alerts = [a for a in alerts if a["delivery"] == delivery][:limit]
    for a in alerts:
        a.pop("key", None)
    return jsonable({
        "server_time": now,
        "mode": "paused" if s.paused else "dry_run" if s.dry_run else "sending",
        "telegram_connected": telegram_view(store)["configured"],
        "telegram": telegram_view(store),
        "timezone": s.timezone,
        "alerts": alerts,
        "delivery_meaning": ALERT_DELIVERY_MEANING,
        "briefs": store.recent_brief_log(14),
    })


def alert_settings_view(store: CandleStore) -> dict:
    from dataclasses import asdict

    from forex_scanner.alerts.settings import AlertSettings, load_settings

    return {"settings": asdict(load_settings(store)), "defaults": asdict(AlertSettings()),
            "timeframes": config.TIMEFRAMES}


def _missing(value) -> bool:
    """True for None and for NaN (how pandas represents an empty database value)."""
    return value is None or (isinstance(value, float) and value != value)


def _parse(text) -> datetime | None:
    """Database time text ('2026-11-20T21:45:00Z') or NaN -> aware datetime or None."""
    if _missing(text):
        return None
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def events_view(store: CandleStore, source: str, limit: int, pair: str | None = None) -> dict:
    events = store.load_events(source, limit=limit, pair=pair)
    return jsonable({"count": len(events), "events": events})
