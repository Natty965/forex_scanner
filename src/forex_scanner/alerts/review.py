"""Review of recorded alerts over the last few days: for tuning before switching sending on.

It only reads and suggests. It never changes a setting: you decide on the Alerts page.
"""

from collections import Counter
from datetime import datetime, timedelta

from .rules import is_rollover_candle
from .settings import AlertSettings

# Above this many sendable alerts per weekday, the alerts probably feel like noise.
BUSY_PER_DAY = 10


def review(store, now: datetime, s: AlertSettings, days: int = 7) -> dict:
    since = now - timedelta(days=days)
    alerts = [a for a in store.load_alerts(limit=5000) if a["created_at"] >= since and a["type"] != "test"]

    by_type = Counter(a["type"] for a in alerts)
    by_delivery = Counter(a["delivery"] for a in alerts)
    # "Would have been sent" if sending had been on: everything except held, suppressed, expired.
    sendable = [a for a in alerts if a["delivery"] in ("dry_run", "pending", "sent", "failed", "paused")]
    per_day = Counter(a["created_at"].astimezone(s.tz).strftime("%a %d %b") for a in sendable)
    weekdays = {a["created_at"].astimezone(s.tz).date() for a in alerts
                if a["created_at"].astimezone(s.tz).weekday() < 5}

    unusual = [a for a in alerts if a["type"] == "unusual_move"]
    unusual_by_item = Counter(f"{a['pair']} {a['timeframe']}" for a in unusual)
    rollover = [a for a in unusual if a["candle_time"] and is_rollover_candle(a["candle_time"])]
    unusual_by_hour = Counter(f"{a['created_at'].astimezone(s.tz):%H}:00" for a in unusual)

    event_lines = [line for a in alerts if a["type"] == "market_events" for line in a["data"].get("lines", [])]
    event_kinds = Counter(
        "trend change" if "Trend" in line else "EMA crossing" if "EMA" in line else "RSI zone" for line in event_lines)

    suggestions = []
    avg = len(sendable) / max(len(weekdays), 1)
    if unusual and len(rollover) / len(unusual) >= 0.3 and not s.skip_rollover_candle:
        suggestions.append(f"{len(rollover)} of {len(unusual)} unusual moves were the daily rollover candle "
                           "(17:00 New York). Consider ticking 'Skip the daily rollover candle'.")
    if avg > BUSY_PER_DAY:
        suggestions.append(f"About {avg:.0f} sendable alerts per weekday. Consider raising the unusual-move "
                           "threshold or removing M15.")
    if weekdays and not alerts:
        suggestions.append("No alerts at all: the thresholds may be too strict, or the worker wasn't running.")
    if by_delivery.get("suppressed"):
        suggestions.append(f"{by_delivery['suppressed']} alert(s) hit a daily limit. Check whether the limit "
                           "or the threshold should change.")
    if by_type.get("data"):
        suggestions.append(f"{by_type['data']} data/system alert(s): check the Data health page for the cause.")

    briefs = store.recent_brief_log(days + 1)
    return {
        "days": days, "since": since, "total": len(alerts), "weekdays_covered": len(weekdays),
        "by_type": dict(by_type), "by_delivery": dict(by_delivery),
        "sendable": len(sendable), "sendable_per_weekday": round(avg, 1), "sendable_per_day": dict(per_day),
        "unusual_by_item": dict(unusual_by_item.most_common()),
        "unusual_by_hour": dict(sorted(unusual_by_hour.items())),
        "unusual_rollover": len(rollover), "event_kinds": dict(event_kinds), "event_lines": len(event_lines),
        "briefs": briefs, "suggestions": suggestions or ["Nothing stands out. Tune to taste, or switch sending on."],
    }


def format_review(r: dict, s: AlertSettings) -> str:
    out = [f"ALERT REVIEW · last {r['days']} days (since {r['since'].astimezone(s.tz):%a %d %b %H:%M} {s.place})",
           f"Weekdays covered: {r['weekdays_covered']}   Alerts recorded: {r['total']}", ""]
    out.append(f"Would have been sent (if sending were on): {r['sendable']}  "
               f"(about {r['sendable_per_weekday']} per weekday)")
    for day, n in r["sendable_per_day"].items():
        out.append(f"  {day}: {n}")
    out.append("")
    out.append("By type:     " + (", ".join(f"{k} {v}" for k, v in r["by_type"].items()) or "none"))
    out.append("By delivery: " + (", ".join(f"{k} {v}" for k, v in r["by_delivery"].items()) or "none"))
    if r["unusual_by_item"]:
        out.append("Unusual moves by pair/timeframe: " + ", ".join(f"{k} {v}" for k, v in r["unusual_by_item"].items()))
        out.append("Unusual moves by hour: " + ", ".join(f"{k} {v}" for k, v in r["unusual_by_hour"].items()))
        out.append(f"  of which on the daily rollover candle: {r['unusual_rollover']}")
    if r["event_lines"]:
        out.append(f"Market event lines: {r['event_lines']} (" +
                   ", ".join(f"{k} {v}" for k, v in r["event_kinds"].items()) + ")")
    if r["briefs"]:
        out.append("Daily briefs: " + "; ".join(f"{b['day']} {b['status']}" for b in reversed(r["briefs"])))
    out.append("")
    out.append("Suggestions (nothing is changed automatically):")
    out += [f"  - {x}" for x in r["suggestions"]]
    return "\n".join(out)
