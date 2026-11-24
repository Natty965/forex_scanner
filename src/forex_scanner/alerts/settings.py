"""Alert preferences: defaults here, your changes stored in the database (settings table).

Defaults approved on 2026-11-23: Europe/London, daily brief 07:00, quiet hours 22:00-07:00,
dry run on (nothing sent until you switch it off), unusual moves on M15/H1 at 2.5x ATR,
market events on H1/D1 batched, data problems on, quiet hours hold everything.
v0.5 (B1): caution alerts (risk warnings, never trade signals) for the weekend close and high
volatility; the other conditions show on the dashboard and in the brief only.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from forex_scanner import config

SETTINGS_NAME = "alerts"
ALERT_TYPES = ("unusual_move", "market_events", "data", "caution", "brief")
# trading conditions that can be sent as caution alerts (see alerts/conditions.py)
CAUTION_KINDS = ("weekend_close", "weekly_open", "rollover", "high_volatility")


@dataclass
class AlertSettings:
    # when and where
    timezone: str = "Europe/London"
    brief_time: str = "07:00"
    quiet_start: str = "22:00"           # quiet hours: alerts are held, then sent as one summary
    quiet_end: str = "07:00"
    dry_run: bool = True                 # record alerts but send nothing
    paused: bool = False                 # emergency stop for sending

    # unusual moves
    unusual_enabled: bool = True
    unusual_timeframes: list[str] = field(default_factory=lambda: ["M15", "H1"])
    unusual_range_atr: float = 2.5       # closed candle's high-low range >= this x ATR
    live_move_enabled: bool = True
    live_move_atr: float = 2.0           # price moved >= this x the H1 ATR within 60 minutes
    unusual_cooldown_minutes: int = 60
    # The first candle of each Forex day (opening 17:00 New York = 22:00 UTC in winter) often has
    # a wide range because trading is thin at the daily rollover. True = never alert on it.
    skip_rollover_candle: bool = False

    # market events (trend change, EMA crossing, RSI zone), batched per cycle
    events_enabled: bool = True
    events_timeframes: list[str] = field(default_factory=lambda: ["H1", "D1"])
    rsi_high: float = 70.0
    rsi_high_exit: float = 65.0          # hysteresis: "left HIGH" only below this
    rsi_low: float = 30.0
    rsi_low_exit: float = 35.0

    # data problems
    data_enabled: bool = True
    data_fail_cycles: int = 3            # consecutive cycles in which every request failed

    # caution alerts: risk warnings ("avoid new trades now"), never signals to trade
    caution_enabled: bool = True
    caution_kinds: list[str] = field(default_factory=lambda: ["weekend_close", "high_volatility"])
    volatility_threshold: float = 1.8    # recent hourly ranges >= this x their 20-day normal

    # at most this many alerts of each type per local day (the rest are recorded as suppressed)
    daily_caps: dict[str, int] = field(default_factory=lambda: {
        "unusual_move": 10, "market_events": 12, "data": 6, "caution": 6, "brief": 2})

    # ---------- helpers ----------

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def place(self) -> str:
        """'Europe/London' -> 'London' (for messages)."""
        return self.timezone.split("/")[-1].replace("_", " ")

    def in_quiet_hours(self, when: datetime) -> bool:
        local = when.astimezone(self.tz).time()
        start, end = _hhmm(self.quiet_start), _hhmm(self.quiet_end)
        if start == end:
            return False                                 # no quiet hours
        if start < end:
            return start <= local < end
        return local >= start or local < end             # wraps past midnight (e.g. 22:00-07:00)

    def local_midnight(self, when: datetime) -> datetime:
        local = when.astimezone(self.tz)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _hhmm(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def validate(s: AlertSettings) -> list[str]:
    """Problems with these settings, in plain English (empty list = fine)."""
    problems = []
    try:
        ZoneInfo(s.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"Unknown timezone '{s.timezone}' (use a name like Europe/London).")
    for name in ("brief_time", "quiet_start", "quiet_end"):
        try:
            _hhmm(getattr(s, name))
        except (ValueError, AttributeError):
            problems.append(f"{name} must be a time like 07:00.")
    for name in ("unusual_timeframes", "events_timeframes"):
        bad = [tf for tf in getattr(s, name) if tf not in config.TIMEFRAMES]
        if bad:
            problems.append(f"{name}: unknown timeframe(s) {', '.join(bad)}; use {', '.join(config.TIMEFRAMES)}.")
    for name in ("unusual_range_atr", "live_move_atr"):
        if not 0.5 <= getattr(s, name) <= 20:
            problems.append(f"{name} must be between 0.5 and 20.")
    if not (0 < s.rsi_low < s.rsi_low_exit < s.rsi_high_exit < s.rsi_high < 100):
        problems.append("RSI levels must satisfy 0 < low < low exit < high exit < high < 100.")
    bad = [k for k in s.caution_kinds if k not in CAUTION_KINDS]
    if bad:
        problems.append(f"caution_kinds: unknown kind(s) {', '.join(bad)}; use {', '.join(CAUTION_KINDS)}.")
    if not 1.2 <= s.volatility_threshold <= 10:
        problems.append("volatility_threshold must be between 1.2 and 10.")
    if not 1 <= s.data_fail_cycles <= 50:
        problems.append("data_fail_cycles must be between 1 and 50.")
    if not 0 <= s.unusual_cooldown_minutes <= 24 * 60:
        problems.append("unusual_cooldown_minutes must be between 0 and 1440.")
    if any(t not in ALERT_TYPES or not 0 <= n <= 500 for t, n in s.daily_caps.items()):
        problems.append("daily_caps must map alert types to numbers between 0 and 500.")
    return problems


def apply_changes(current: AlertSettings, changes: dict) -> tuple[AlertSettings | None, list[str]]:
    """Merge `changes` (e.g. from the Alerts page) into `current`, checking names and types first.
    Returns (new settings, []) or (None, problems)."""
    defaults = asdict(AlertSettings())
    problems = []
    for name, value in changes.items():
        if name not in defaults:
            problems.append(f"Unknown setting '{name}'.")
            continue
        expected = defaults[name]
        if isinstance(expected, bool):
            ok = isinstance(value, bool)
        elif isinstance(expected, int):
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif isinstance(expected, float):
            ok = isinstance(value, int | float) and not isinstance(value, bool)
        elif isinstance(expected, str):
            ok = isinstance(value, str)
        elif isinstance(expected, list):
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        else:                                     # daily_caps
            ok = isinstance(value, dict) and all(
                isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in value.items())
        if not ok:
            problems.append(f"'{name}' has the wrong kind of value ({value!r}).")
    if problems:
        return None, problems
    merged = {**asdict(current), **changes}
    for name in ("unusual_range_atr", "live_move_atr", "volatility_threshold",
                 "rsi_high", "rsi_high_exit", "rsi_low", "rsi_low_exit"):
        merged[name] = float(merged[name])
    new = AlertSettings(**merged)
    problems = validate(new)
    return (None, problems) if problems else (new, [])


def load_settings(store) -> AlertSettings:
    """Defaults, overridden by whatever is stored. Unknown or invalid stored values fall back to defaults."""
    stored = store.get_setting(SETTINGS_NAME, {}) or {}
    known = {f.name for f in fields(AlertSettings)}
    s = AlertSettings(**{k: v for k, v in stored.items() if k in known})
    s.daily_caps = {**AlertSettings().daily_caps, **s.daily_caps}   # caps for types added later
    return s if not validate(s) else AlertSettings()


def save_settings(store, s: AlertSettings, at: datetime) -> list[str]:
    """Validate and store. Returns the problems (nothing is stored if there are any)."""
    problems = validate(s)
    if not problems:
        store.set_setting(SETTINGS_NAME, asdict(s), at)
    return problems
