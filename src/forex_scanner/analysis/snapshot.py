"""Current price, recent changes and a freshness status for one pair.

Built from 1-minute candles. Yahoo does not say exactly when its latest price
arrived, so "price age" is measured from the START of the newest 1-minute
candle: the most cautious figure we can honestly give.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from forex_scanner.market import is_market_closed, pip_size
from forex_scanner.providers import CandleSet

LIVE = "LIVE"
DELAYED = "DELAYED"
STALE = "STALE"
MARKET_CLOSED = "MARKET CLOSED"

# If the reference price is this much older than the time we wanted, the change spans
# a gap (the weekend, or a hole in the source's data) and we flag it.
GAP_TOLERANCE = timedelta(minutes=15)


@dataclass(frozen=True)
class Change:
    reference_price: float
    reference_time: datetime   # open time of the candle the reference price came from
    pips: float
    percent: float
    spans_gap: bool            # True = reference is from well before the target time


@dataclass(frozen=True)
class PriceSnapshot:
    pair: str
    source: str
    price: float               # close of the newest 1-minute candle (indicative mid price)
    price_time: datetime       # open time of that candle (UTC)
    fetched_at: datetime
    status: str
    change_1h: Change | None   # None = not enough history to compare
    change_24h: Change | None

    @property
    def age(self) -> timedelta:
        return max(self.fetched_at - self.price_time, timedelta(0))


def freshness_status(age: timedelta, market_closed: bool, live_max: timedelta,
                     delayed_max: timedelta) -> str:
    if market_closed:
        return MARKET_CLOSED
    if age <= live_max:
        return LIVE
    if age <= delayed_max:
        return DELAYED
    return STALE


def status_at(snapshot: PriceSnapshot, when: datetime, live_max_minutes: float = 5,
              delayed_max_minutes: float = 30) -> tuple[str, timedelta]:
    """Freshness of a snapshot judged at time `when` (e.g. when a page is drawn), not when fetched.

    A dashboard can show cached data for minutes, or sit open in a browser; its label must
    reflect how old the price is NOW. Returns (status, age at `when`).
    """
    age = max(when - snapshot.price_time, timedelta(0))
    status = freshness_status(age, is_market_closed(when), timedelta(minutes=live_max_minutes),
                              timedelta(minutes=delayed_max_minutes))
    return status, age


def price_change(candles: pd.DataFrame, pair: str, window: timedelta) -> Change | None:
    """Compare the newest close with the close of the last candle at or before (newest - window)."""
    latest_time = candles.index[-1]
    target = latest_time - window
    earlier = candles[candles.index <= target]
    if earlier.empty:
        return None
    ref_time = earlier.index[-1]
    ref_price = float(earlier["close"].iloc[-1])
    price = float(candles["close"].iloc[-1])
    return Change(
        reference_price=ref_price,
        reference_time=ref_time.to_pydatetime(),
        pips=(price - ref_price) / pip_size(pair),
        percent=(price - ref_price) / ref_price * 100,
        spans_gap=(target - ref_time) > GAP_TOLERANCE,
    )


def build_snapshot(cs: CandleSet, live_max_minutes: float = 5, delayed_max_minutes: float = 30) -> PriceSnapshot:
    if cs.timeframe != "M1":
        raise ValueError("Snapshots need 1-minute candles (M1) for an accurate current price")
    c = cs.candles
    price_time = c.index[-1].to_pydatetime()
    age = max(cs.fetched_at - price_time, timedelta(0))
    return PriceSnapshot(
        pair=cs.pair,
        source=cs.source,
        price=float(c["close"].iloc[-1]),
        price_time=price_time,
        fetched_at=cs.fetched_at,
        status=freshness_status(age, is_market_closed(cs.fetched_at),
                                timedelta(minutes=live_max_minutes), timedelta(minutes=delayed_max_minutes)),
        change_1h=price_change(c, cs.pair, timedelta(hours=1)),
        change_24h=price_change(c, cs.pair, timedelta(hours=24)),
    )
