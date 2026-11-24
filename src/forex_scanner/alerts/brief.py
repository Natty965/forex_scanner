"""The daily brief: one message summarising every pair, built only from current data.

Used by the alert engine (scheduled, recorded) and by `python -m forex_scanner brief` (preview).
Every number comes from the same analysis the dashboard shows. Data that isn't current is said
to be not current, never shown as if it were.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from forex_scanner import config
from forex_scanner.analysis.indicator_summary import IndicatorSummary
from forex_scanner.analysis.snapshot import MARKET_CLOSED, PriceSnapshot, status_at
from forex_scanner.analysis.trend import DATA_OK, TrendResult
from forex_scanner.market import is_market_closed

from .rules import Candidate, fmt_price
from .settings import AlertSettings

ARROW = {"UP": "UP", "DOWN": "DOWN", "SIDEWAYS": "sideways", "UNKNOWN": "n/a"}

# Approximate session opens in each city's local time (they follow their own clocks' summer time).
SESSIONS = [("London", "Europe/London", 8), ("New York", "America/New_York", 8)]


def brief_due_at(day_local: datetime, s: AlertSettings) -> datetime:
    """The brief time on a given local day, as an aware datetime."""
    hour, minute = (int(x) for x in s.brief_time.split(":"))
    return day_local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _session_line(now: datetime, s: AlertSettings) -> str:
    parts = []
    local_day = now.astimezone(s.tz).date()
    for name, zone, hour in SESSIONS:
        tz = ZoneInfo(zone)
        opens = datetime(local_day.year, local_day.month, local_day.day, hour, 0, tzinfo=tz)
        parts.append(f"{name} opens {opens.astimezone(timezone.utc):%H:%M} UTC "
                     f"({opens.astimezone(s.tz):%H:%M} {s.place})")
    return "Today: " + ", ".join(parts) + "."


def compose_brief(now: datetime, s: AlertSettings, snapshots: list[PriceSnapshot],
                  trends: list[tuple[TrendResult, str]], indicators: list[tuple[IndicatorSummary, str]],
                  held: list[dict] | None = None, late_by: timedelta | None = None,
                  conditions: list | None = None) -> Candidate:
    local = now.astimezone(s.tz)
    snaps = {x.pair: x for x in snapshots}
    trend_by = {(t.pair, t.timeframe): (t, st) for t, st in trends}
    ind_by = {(i.pair, i.timeframe): (i, st) for i, st in indicators}

    lines = []
    if late_by is not None:
        lines.append(f"(Late: due at {s.brief_time} {s.place}, created {local:%H:%M}; "
                     "the worker wasn't running at the usual time.)")
    lines.append(f"Data: {config.DATA_PROVIDER} · {'market open' if not is_market_closed(now) else 'market CLOSED'}")
    lines.append("")
    if conditions is not None:
        lines.append("Trading conditions (risk warnings, not signals):")
        if conditions:
            lines += [f"  {c.level}: {c.text}" for c in conditions]
        else:
            lines.append("  No warnings right now. This is not a signal to trade.")
        lines.append("")

    for pair in config.PAIRS:
        snap = snaps.get(pair)
        if snap is None:
            lines += [f"{pair}: no current price (the last fetch failed).", ""]
            continue
        status, age = status_at(snap, now, config.LIVE_MAX_AGE_MINUTES, config.DELAYED_MAX_AGE_MINUTES)
        ch = snap.change_24h
        gap = " *" if ch is not None and ch.spans_gap else ""
        change = "24h n/a" if ch is None else f"24h {ch.pips:+.1f} pips ({ch.percent:+.2f}%){gap}"
        lines.append(f"{pair}  {fmt_price(pair, snap.price)}  {status}"
                     + (f" ({int(age.total_seconds() // 60)} min old)" if status not in ("LIVE", MARKET_CLOSED) else "")
                     + f"  {change}")
        trend_parts = []
        for tf in config.TIMEFRAMES:
            t = trend_by.get((pair, tf))
            trend_parts.append(f"{tf} {ARROW[t[0].direction] if t and t[1] == DATA_OK else 'n/a'}")
        lines.append("  Trend   " + " · ".join(trend_parts))
        d1 = ind_by.get((pair, "D1"))
        if d1 and d1[1] == DATA_OK:
            i = d1[0]
            align = {"STACKED UP": "stacked up", "STACKED DOWN": "stacked down"}.get(i.alignment, i.alignment.lower())
            lines.append(f"  D1 EMAs {align}")
            if i.rsi is not None:
                zone = {"HIGH": ", HIGH zone (often called 'overbought')",
                        "LOW": ", LOW zone (often called 'oversold')"}.get(i.rsi_zone, "")
                lines.append(f"  D1 RSI  {i.rsi:.1f}{zone}")
            if i.atr_pips is not None:
                lines.append(f"  Typical daily range ≈ {i.atr_pips:.0f} pips (ATR)")
        else:
            lines.append("  D1 indicators not current.")
        lines.append("")

    if any(x.change_24h and x.change_24h.spans_gap for x in snapshots):
        lines.append("* compared with the last price before a gap (e.g. the weekend).")
    if held:
        lines.append(f"Held during quiet hours ({len(held)}):")
        lines += [f"  {a['created_at'].astimezone(s.tz):%H:%M} {a['title']}" for a in held[:10]]
        if len(held) > 10:
            lines.append(f"  ... and {len(held) - 10} more (see the Alerts page)")
        lines.append("")
    lines.append(_session_line(now, s))
    lines.append("Economic calendar: not available yet (no verified free source).")
    lines.append("Descriptions of recent prices, not predictions or signals.")

    return Candidate(
        key=f"brief:{local:%Y-%m-%d}", type="brief", severity="info", market=True,
        title=f"DAILY BRIEF · {local:%a %d %b %Y}" + (" (late)" if late_by is not None else ""),
        body="\n".join(lines).strip(),
        data={"local_date": f"{local:%Y-%m-%d}", "late_minutes": None if late_by is None
              else round(late_by.total_seconds() / 60), "held": len(held or [])},
    )
