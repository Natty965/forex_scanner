"""Small formatting helpers shared by the display modules."""

from datetime import datetime, timedelta

from forex_scanner.market import trading_date


def candle_label(candle_open: datetime | None, timeframe: str, long: bool = False) -> str:
    """How to name a candle on screen.

    Intraday candles: their open time. Daily candles: the trading day they belong to,
    because their open time (17:00 New York the evening before) looks like the wrong day.
    """
    if candle_open is None:
        return "-"
    if timeframe == "D1":
        d = trading_date(candle_open)
        return f"daily candle for {d:%a %Y-%m-%d}" if long else f"{d:%m-%d} day"
    return f"candle that opened {candle_open:%Y-%m-%d %H:%M} UTC" if long else f"{candle_open:%m-%d %H:%M}"


def format_age(age: timedelta) -> str:
    """Compact age, e.g. '15m', '3h 20m', '2d 1h'. Never negative (clock differences of a few
    seconds between steps show as 0m, not as 'in the future')."""
    minutes = max(int(age.total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def price_digits(pair: str) -> int:
    """JPY pairs are quoted to 3 decimals, most others to 5."""
    return 3 if pair.endswith("JPY") else 5
