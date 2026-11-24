"""Basic Forex market facts: trading hours and pip sizes.

Trading week: the market opens Sunday 17:00 New York time and closes Friday
17:00 New York time. Using New York time means daylight-saving changes are
handled automatically (in UTC the open is 22:00 in winter, 21:00 in summer).
Bank holidays (e.g. 25 December) are NOT modelled; thin or missing prices then
show up as DELAYED/STALE, which is the honest result.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")


def closed_mask(index: pd.DatetimeIndex) -> pd.Series:
    """True for each timestamp inside the weekend closure (Fri 17:00 -> Sun 17:00 New York).

    Shifting New York time forward 7 hours turns 17:00 into midnight, so the
    closed window becomes exactly "Saturday or Sunday" after the shift.
    """
    shifted = index.tz_convert(NEW_YORK) + pd.Timedelta(hours=7)
    return pd.Series(shifted.day_of_week >= 5, index=index)


def is_market_closed(when: datetime) -> bool:
    return bool(closed_mask(pd.DatetimeIndex([pd.Timestamp(when)])).iloc[0])


def next_open(when: datetime) -> datetime:
    """Next weekly open (Sunday 17:00 New York) after `when`, returned in the same timezone."""
    ny = when.astimezone(NEW_YORK)
    days_to_sunday = (6 - ny.weekday()) % 7
    candidate = (ny + timedelta(days=days_to_sunday)).replace(hour=17, minute=0, second=0, microsecond=0)
    if candidate <= ny:
        candidate += timedelta(days=7)
    return candidate.astimezone(when.tzinfo)


def trading_date(candle_open: datetime) -> date:
    """The Forex trading day a daily candle belongs to.

    A Forex day runs 17:00 -> 17:00 New York time, so Friday's daily candle opens on
    Thursday evening (e.g. 22:00 UTC in winter). Shifting 7 hours makes 17:00 midnight.
    """
    return (pd.Timestamp(candle_open).tz_convert(NEW_YORK) + pd.Timedelta(hours=7)).date()


def pip_size(pair: str) -> float:
    """A pip is the standard unit of price movement: 0.01 for JPY pairs, 0.0001 for most others."""
    return 0.01 if pair.endswith("JPY") else 0.0001
