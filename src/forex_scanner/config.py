"""Project settings.

Secrets and per-machine choices come from the .env file (never from source code).
Milestone 0 only loads the file; real settings are added in later milestones.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = two folders above this file (src/forex_scanner/config.py -> project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

# Read .env if it exists. Values already set in the real environment take priority.
load_dotenv(PROJECT_ROOT / ".env")

DATA_PROVIDER = os.getenv("DATA_PROVIDER", "yfinance")

# Local database file (ignored by Git). Can be moved by setting FOREX_DB_PATH in .env.
DB_PATH = Path(os.getenv("FOREX_DB_PATH", DATA_DIR / "forex.db"))

# Currency pairs we follow (our own naming; each provider maps them to its symbols).
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY"]

# Timeframes the scanner works with by default.
TIMEFRAMES = ["M15", "H1", "D1"]

# Freshness labels for current prices (minutes since the newest 1-minute candle began).
LIVE_MAX_AGE_MINUTES = 5      # up to this: LIVE
DELAYED_MAX_AGE_MINUTES = 30  # up to this: DELAYED; beyond: STALE

# Basic trend detection (see analysis/trend.py for the method).
TREND_LOOKBACK = 50           # closed candles per judgement
TREND_THRESHOLD = 3.0         # minimum move, in average candle ranges, to call UP/DOWN

# Technical indicators (v0.2).
EMA_PERIODS = [20, 50, 200]
RSI_PERIOD = 14
ATR_PERIOD = 14
# Closed candles to fetch for indicators. EMA 200 needs ~1,004 to warm up (see
# analysis/indicators.py); 1,200 leaves a margin. Still one request per pair/timeframe.
# D1 is limited by the source to ~520 candles (2 years of trading days), so EMA 200
# on D1 shows "n/a". A source with longer daily history (e.g. OANDA) would fix that.
INDICATOR_HISTORY = 1200
# The monitor reports when the first EMA crosses the second.
EMA_CROSS = (20, 50)

# Monitor loop pacing.
MONITOR_INTERVAL_MINUTES = 5          # while the market is open (minimum 2)
MONITOR_CLOSED_INTERVAL_MINUTES = 30  # while the market is closed

# Being polite to free data sources.
REQUEST_DELAY_SECONDS = 1.0   # pause between requests
MAX_RETRIES = 2               # extra attempts after a temporary failure
