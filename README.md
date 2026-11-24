# Forex AI Assistant: Market Scanner (v0.3)

A personal, learning-focused Forex market scanner. It watches EUR/USD, GBP/USD and
USD/JPY, stores their price history locally, shows current prices with clear freshness
labels, and describes trends and technical indicators (EMA, RSI, ATR) with plain-English
reasons, in the terminal and in a local web dashboard.

**Quick start** (after the setup below): `python -m forex_scanner dashboard` opens the
dashboard at http://localhost:8000.

> **Learning and demo only.** This project contains no order or trading code. It never
> places trades or connects to a trading account. Trend labels describe past prices;
> they are not predictions, signals or financial advice.

Everything is free: Python, open-source libraries, and Yahoo Finance data via `yfinance`.

---

## 1. Setup (Windows, PowerShell)

Requires Python 3.12 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-lock.txt   # exact tested versions (or requirements.txt for latest)
pip install -e .
Copy-Item .env.example .env
```

If PowerShell refuses to run `Activate.ps1`, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Every new terminal: `.\.venv\Scripts\Activate.ps1` first.

**Dashboard page (once, and again after changing anything in `web/`).** It needs
[Node.js LTS](https://nodejs.org), free, only for building the page:

```powershell
winget install --id OpenJS.NodeJS.LTS --exact   # then open a NEW terminal
cd web
npm install
npm run build
cd ..
```

Check that it works:

```powershell
python -m forex_scanner        # environment check
pytest                         # offline tests (fast, no internet)
pytest -m live                 # a few tests against the real data source
```

## 2. Commands

| Command | What it does |
|---|---|
| `python -m forex_scanner prices` | Current price, 1h and 24h change (pips and %), freshness status |
| `python -m forex_scanner trend` | UP / DOWN / SIDEWAYS per pair on M15, H1, D1, with reasons (`--brief` hides them) |
| `python -m forex_scanner indicators` | EMA 20/50/200, RSI 14, ATR 14 per pair on M15, H1, D1, with explanations (`--brief` hides them) |
| `python -m forex_scanner structure` | Swing highs/lows, structure (RISING / FALLING / MIXED) and the nearest support and resistance levels per pair on M15, H1, D1, with explanations (`--brief` hides them) |
| `python -m forex_scanner monitor` | Repeats `prices` + `trend` + `indicators` every 5 min until Ctrl+C, listing changes as events (`--interval`, `--cycles`) |
| `python -m forex_scanner fetch` | Downloads history for all pairs/timeframes into the database (`--count max` for everything available) |
| `python -m forex_scanner worker` | Background data collection every 5 min: fetch, analyse, save results and events (one line per cycle). The dashboard will read what it saves |
| `python -m forex_scanner run` | Worker **and** dashboard together in one process: http://localhost:8000 (API docs at `/api/docs`). This PC only. Ctrl+C stops both |
| `python -m forex_scanner dashboard` | Same as `run`, and opens the dashboard in your browser |
| `python -m forex_scanner alerts` | Recorded alerts, newest first (`--show ID` for one in full, `--settings` for the alert settings). v0.4 in progress: nothing is sent yet |
| `python -m forex_scanner brief` | Preview today's daily brief from the saved data (records and sends nothing) |
| `python -m forex_scanner telegram-setup` / `telegram-test` | Check the bot token and find your chat id / send one test message |
| `python -m forex_scanner stored` | What the database holds, and whether the latest fetch for each item worked |
| `python -m forex_scanner candles` | Raw candles for one pair/timeframe (`--pair`, `--timeframe`, `--count`) |

`prices`, `trend`, `indicators`, `monitor` and `fetch` save what they download (`--no-save` to skip).
Commands exit with code 1 if any request failed.

Pairs: EUR/USD, GBP/USD, USD/JPY. Timeframes: M1, M5, M15, M30, H1, D1. All times are UTC.

**Daily candles are labelled by trading day** (e.g. `11-20 day`). A Forex day runs from
17:00 to 17:00 New York time, so Friday's daily candle actually opens on Thursday evening
(22:00 UTC in winter, 21:00 UTC in summer). Showing that open time would look like the
wrong day. The raw `candles` command still shows open times.

## 3. How to read the output

### Freshness (prices)

Yahoo doesn't say exactly when its latest price arrived, so **price age** is measured
from the *start* of the newest 1-minute candle, the most cautious figure available.

| Status | Meaning |
|---|---|
| LIVE | market open, price at most 5 minutes old |
| DELAYED | market open, 5–30 minutes old |
| STALE | market open, older than 30 minutes: the source is not updating |
| MARKET CLOSED | Friday 17:00 to Sunday 17:00 New York time. The last price before the close |
| FAILED | the fetch failed. **No price is shown**, only the error |

A `*` on a change means the source had no price at that exact time, so it was compared
with the last earlier price: before the weekend (e.g. on Monday morning "24h ago" was
Sunday), or before a gap in the data (Yahoo's 1-minute data has occasional holes). Prices are indicative mid quotes,
not prices you could trade at. A **pip** is 0.0001 (0.01 for JPY pairs).

### Trend

For each pair and timeframe, using the last 50 **closed** candles (a forming candle is ignored):

1. **Move:** a best-fit straight line through the closes. How far it travels is
   measured in *average candle ranges* (R), so pairs and timeframes compare fairly.
2. **Position:** is the last close above or below the 50-candle average?

| Label | Rule |
|---|---|
| UP | line rose at least 3R **and** price is above the average |
| DOWN | line fell at least 3R **and** price is below the average |
| SIDEWAYS | smaller move, or the two measures disagree (possible turn) |
| UNKNOWN | fewer than 50 closed candles |

The 3R threshold came from testing about 2,000 real historical windows; it labels roughly
half as trending. The **Data** column says whether the candles behind the label are
current (OK), STALE, or from a MARKET CLOSED period. `trend` always fetches fresh data.

### Monitor pacing

- Market open: every `--interval` minutes (default 5, minimum 2). That's 12 requests per
  cycle, 1 second apart. Trend and indicators share one download per pair and timeframe.
- Market closed: every 30 minutes, showing when the market reopens.
- Every request failing: waits twice as long each time (up to 30 min), showing the error.
- Your PC must stay on with the window open.

**Events** are changes since the previous cycle, for each pair and timeframe:

| Event | Example |
|---|---|
| Trend change | `GBP/USD M15: Trend DOWN -> SIDEWAYS` |
| EMA crossing | `EUR/USD H1: EMA 20 crossed above EMA 50` |
| RSI zone | `USD/JPY M15: RSI entered the HIGH zone (70+) (71.2)` / `RSI left the ...` |

The first cycle only sets the starting point. A failed fetch or an `n/a` value is never
counted as a change: the last known state is kept until real data returns. EMAs within
0.1 pip of each other don't count as crossed, so tiny wobbles don't produce a stream of
events. Events usually appear when a new candle closes. Nothing is sent anywhere yet;
`events.py` is where Telegram alerts will connect later.

### Worker (v0.3)

```powershell
python -m forex_scanner worker
```

The worker is the **only** part that fetches market data on a schedule. The dashboard (and
later Telegram) only read what it saves. Every cycle it:

1. fetches prices (1-minute candles) and M15/H1/D1 history: 12 requests, 1 second apart;
2. saves candles and the **latest analysis** (price, trend, indicators) to the database;
3. saves **events** (trend changes, EMA crossings, RSI zones) permanently, along with what it
   last knew, so after a restart it still notices changes that happened while it was off;
4. records a **heartbeat**. That also stops a second worker or `monitor` from starting on the
   same database, since both would fetch everything twice. If a worker crashes, its claim
   expires by itself a few minutes after its next cycle was due; `--force` overrides the check.

`monitor` is the same loop with the full terminal tables, so it saves results and takes the
same lock. Pacing is unchanged: every 5 min while the market is open, 30 min while closed,
and longer waits when every request fails.

### Local API (v0.3)

```powershell
python -m forex_scanner run              # worker + API
python -m forex_scanner run --no-worker  # API only, serving what is already saved
```

A small **read-only** web API (FastAPI) that the React dashboard will use. Interactive
documentation, where you can try every endpoint, is at http://localhost:8000/api/docs.

| Endpoint | Returns |
|---|---|
| `GET /api/alerts?type=&delivery=` | recorded alerts (newest first), sending mode, daily-brief outcomes |
| `GET /api/alert-settings` | current alert preferences and their defaults |
| `PUT /api/alert-settings` | change preferences: **dashboard only**, JSON, only the settings to change |
| `GET /api/data-health` | worker, database, stored data per pair/timeframe, recent failures, current settings |
| `GET /api/status` | worker health (RUNNING / STOPPED / NOT RESPONDING / NEVER STARTED), market open/closed, pairs, freshness thresholds |
| `GET /api/overview` | price, trend and indicators for every pair and timeframe, plus the 10 latest events |
| `GET /api/pairs/EURUSD/analysis?timeframe=H1` | one pair/timeframe in detail, with the plain-English reasons |
| `GET /api/pairs/EURUSD/candles?timeframe=H1&limit=300` | stored candles, oldest first (`complete=false` = was still forming) |
| `GET /api/pairs/EURUSD/chart?timeframe=H1&limit=300` | candles plus EMA/RSI/ATR lines for a chart (closed candles, warm-up respected) |
| `GET /api/events?limit=50&pair=EURUSD` | saved events, newest first |

- **It never fetches market data.** Only the worker does. A test enforces this.
- **Freshness is judged at the moment of each request.** Prices use the usual LIVE / DELAYED /
  STALE / MARKET CLOSED rule. Trend and indicator results become STALE if the worker hasn't
  refreshed them for 13 minutes (e.g. it was stopped).
- **Listens on 127.0.0.1 only**, so other devices on your network can't reach it. No login, as
  it's a single-user tool on your PC.
- Prices are returned with full precision; the dashboard rounds them for display.
- If the worker is **killed** (not stopped with Ctrl+C), it shows RUNNING until 3 minutes after
  its next cycle was due, then NOT RESPONDING. A new worker is refused until then unless you
  use `--force`.
- The documentation page loads its scripts from a public CDN (jsDelivr) when you open it. The
  API itself and the dashboard don't depend on it.
- If a worker's process has gone (e.g. its terminal window was closed instead of Ctrl+C), a
  new worker can start immediately: the lock checks whether that process still exists.

### Dashboard (v0.3)

```powershell
python -m forex_scanner dashboard     # worker + dashboard, opens http://localhost:8000
```

A React page served by the local API. **Overview** (so far):

- **Status strip:** worker running or not, market open or closed (and when it reopens), when
  the page last updated, and the time in UTC and your local time.
- **Pair cards:** price (5 decimals, 3 for JPY), freshness badge, 1h and 24h change, trend on
  M15/H1/D1, typical daily range (ATR).
- **Trend & indicators matrix:** trend, EMA alignment and RSI for every pair and timeframe;
  stale cells are dimmed and labelled.
- **Worth noticing:** RSI zones (current data only) and data problems, each with a reason.
- **Recent events:** saved by the worker.
- **"?" buttons** explain each term in plain English.

**Markets** (`/markets/EURUSD?tf=H1`), a three-column workspace:

- **Watchlist:** each pair's price and freshness; click to switch.
- **Chart** (TradingView Lightweight Charts): candles with EMA 20/50/200, then an RSI pane
  (dashed 70/30 lines) and an ATR pane (pips). M15 / H1 / D1 buttons; EMA / RSI / ATR switches.
  Hover any candle for its exact prices and indicator values. The still-forming candle is drawn
  faded and gets no indicator values. Lines start only once warmed up; the page says what isn't
  drawn and why (e.g. EMA 200 on D1). "Load older candles" goes back up to 5,000 candles. Zoom
  and scroll survive the automatic refresh. Times are UTC; daily candles are labelled by trading
  day.
- **Candle data tab:** the newest 100 loaded candles with OHLC and every indicator value.
  **Events tab:** this pair's saved events.
- **Analysis panel:** trend and indicators with the plain-English reasons, and "what this means
  / doesn't mean".

Checked on live data: for all 3 pairs × M15/H1/D1, the CLI `indicators` command, the analysis
panel and the chart line showed **identical values** (45 of 45).

**Learn** (button at top right): every explanation in one place, plus credits.

**Data health** (`/data`): is the worker running (and its last and next cycle); database size
and fetches in the last 24 h; every stored pair and timeframe with its health (OK, FAILING, NOT
UPDATING, or NOT MONITORED for data saved once with `fetch`); recent failures with the exact
error; and all current settings, read-only, with where to change them.

**Signals, Journal, Backtests** are locked. Each says what it will show and what has to happen
first. For Signals: market structure, a written strategy, a backtest including trading costs,
and your approval.

Honesty rules: every status is judged by the API **at the moment of each request**. If the API
can't be reached, or the page's data is more than 90 seconds old (e.g. after sleep), **no
prices are shown**, only what's wrong. The same applies to the chart and analysis panel. The page
refreshes every 30 seconds (the chart every 60); it never fetches market data. Fonts are bundled,
so the page loads nothing from outside this PC. Signals, Journal and Backtests stay locked until
their features exist.

Chart credit: TradingView Lightweight Charts™, Copyright TradingView, Inc.,
https://www.tradingview.com/ (Apache 2.0). Its licence asks for this notice and a link; the
chart's TradingView logo and the credit line under the chart provide them.

Working on the page itself: run `python -m forex_scanner run`, then in `web/` run
`npm run dev` and open http://localhost:5173 (changes appear instantly). `npm test` runs the
page's tests. Run `npm run build` when done.

### Alerts (v0.4, in progress: recorded, not sent yet)

After every cycle the worker runs an **alert engine**. For now it only **records** alerts (dry
run); Telegram comes in milestone A4. See them with `python -m forex_scanner alerts`.

| Alert | When |
|---|---|
| Unusual move | a just-closed M15 or H1 candle's range is at least 2.5x its normal size (ATR) |
| Fast move | a live price moved at least 2x the hourly ATR within 60 minutes |
| Market events | trend changes, EMA 20/50 crossings and RSI zone changes on H1 and D1, **batched** into one alert per cycle |
| Data problem / recovered | every request failed for 3 cycles in a row, and again when data returns |
| Worker was off | the worker restarts after a gap, so a quiet period isn't mistaken for a quiet market |
| Daily brief | once a day at 07:00 London: every pair's price, freshness, 24h change, trend on M15/H1/D1, D1 EMAs/RSI/ATR, today's London and New York session opens, and any alerts held overnight |

**Daily brief rules:** created by the worker's first cycle at or after the brief time. If the
computer was off then, it is still created (marked **late**) when the worker next runs within
3 hours; otherwise that day is skipped. Saturdays and Sundays are skipped (market closed). It waits
for fresh data rather than using old numbers, and it is never held by quiet hours, because it *is*
the morning summary. Each day's outcome (created, created late, skipped and why) is logged.
Preview today's brief any time with `python -m forex_scanner brief` (records and sends nothing).

How it avoids noise and mistakes:

- Each alert has an identity (rule + pair + timeframe + candle), so it is **never recorded twice**.
- **Cooldown** of 60 minutes per unusual-move rule and pair; **daily limits** per type (extra ones
  are recorded as "suppressed", so you can still see them).
- **RSI hysteresis:** enters HIGH at 70 but only counts as leaving below 65 (LOW: 30 / 35).
- Closed, **recent** candles only; nothing about the market while it is closed or the data isn't
  current; **the first candle after a gap** (weekend or data hole) is skipped, because its range
  includes the jump across the gap.
- **Quiet hours** (22:00-07:00 Europe/London by default): alerts are held for one summary afterwards.
- A problem in the alert engine is shown in the worker's output but **never stops data collection**.

Each alert ends with "This describes what price did. It is not a signal."

**Alerts page** (`/alerts` in the dashboard): a mode badge (DRY RUN / PAUSED / SENDING), a
**Pause** button, and three tabs:

- **History:** every recorded alert in your timezone, filterable by type and delivery status;
  click one to read the full message and why it was (or wasn't) sent.
- **Preferences:** every setting, with a plain-English explanation: dry run, pause, timezone,
  brief time, quiet hours, unusual-move thresholds and timeframes, market-event timeframes, RSI
  levels, data-problem threshold, caution warnings, daily limits. Only changed settings are sent; invalid values are
  refused with the reasons and nothing changes. The worker uses new settings from its next cycle.
- **Daily briefs:** each day's outcome (created, created late, skipped and why).

**Telegram** (v0.4, milestone A4):

1. In Telegram, open **@BotFather**, send `/newbot` and follow the steps (free). Copy the token
   into `.env` as `TELEGRAM_BOT_TOKEN=...`. Never share it or put it in code.
2. Send any message to your new bot, then run `python -m forex_scanner telegram-setup`. It checks
   the token and shows your chat id. Put it into `.env` as `TELEGRAM_CHAT_ID=...`.
3. Restart the worker, then press **Send test message** on the Alerts page (or run
   `python -m forex_scanner telegram-test`).
4. Alerts are sent only when **dry run is off and sending isn't paused**.

How sending works: oldest first, about one message per second (Telegram's guidance); if Telegram
is down or busy it waits 1, 5, then 15 minutes (or as long as Telegram asks) and marks the alert
**failed** after 3 attempts; a wrong token or chat id isn't retried. **Market alerts more than 30
minutes old by the time they could be sent are marked "expired" and not sent**, so a delayed alert
never arrives looking current. The bot sends only to your one chat, never acts on incoming
messages, and the token never appears in logs, errors or the dashboard. Messages go through
Telegram's servers (normal bot chats aren't end-to-end encrypted), so they contain market
information only.

**Reviewing before switching sending on:** `python -m forex_scanner alerts --review` summarises
the last 7 days (`--days N` to change): how many alerts would have been sent per weekday, by type,
pair, timeframe and hour, how many hit the daily rollover candle, market-event kinds, daily briefs,
and suggestions. It never changes a setting. The "Skip the daily rollover candle" preference
(off by default) ignores the candle opening at 17:00 New York, which often looks unusual only
because trading is thin.

**Security of the writable endpoints** (`PUT /api/alert-settings`, `POST /api/telegram/test`): the API answers only to
`localhost` / `127.0.0.1` (requests under any other host name get 400, which blocks "DNS
rebinding"), and changes are accepted only from the dashboard's own page, as JSON (anything
else gets 403 / 415). Other websites open in your browser therefore can't change your settings.

A replay of real stored data (18-20 Nov 2026, every 15 minutes) produced about **8 alerts a
day**, a third of them in quiet hours, with no duplicates or flapping.

### Trading conditions: no-trade warnings (v0.5, milestone B1)

Risk warnings for a beginner on a demo account: times and situations when it is usually wiser
**not to open new trades**. They never say when to trade: "no warnings" is **not** a signal, and
the scanner has no tested strategy yet (that is v0.6). Times follow New York, where the Forex day ends.

| Warning | When | Level |
|---|---|---|
| Weekend close coming | Friday 16:00-17:00 New York (21:00-22:00 UTC in winter) | AVOID new trades |
| Data not current | a price is DELAYED or STALE while the market is open | AVOID acting on it |
| Market just reopened | Sunday 17:00-18:00 New York | CAUTION |
| Daily rollover | 16:45-17:15 New York, Monday-Thursday | CAUTION |
| High volatility | a pair's last 14 closed H1 candles average at least **1.8x** their normal high-low range (median over the last ~20 days) | CAUTION |
| Market closed | the weekend | info |

Volatility uses the plain high-to-low range of candles, not ATR: ATR includes the jump across the
weekend gap and would make every Monday look "volatile". It uses stored H1 candles only while the
H1 analysis is current. On the real data of the last 20 days, 1.8x was reached in about 1% of hours
(EUR/USD), so the warning is rare by design.

Where they appear:
- **Overview:** a "Trading conditions" card, always visible.
- **Daily brief** (and the `brief` preview): a "Trading conditions" section.
- **Caution alerts** (new alert type `caution`): by default only **weekend close** and **high
  volatility**; the rollover happens every day, so it stays on the page and in the brief unless you
  tick it under *Alerts → Preferences → Caution warnings*. A scheduled warning is alerted at most
  once per day; a volatility warning once per episode (it re-arms only after the ratio drops below
  0.85 x the threshold, so a ratio hovering near 1.8 doesn't repeat). Daily limit: 6. Caution
  alerts expire after 30 minutes if they couldn't be sent.

### Market structure: swings and levels (v0.5, milestone B2)

`python -m forex_scanner structure` (the worker also saves it every cycle, for the dashboard in B3):

- **Swing high:** a closed candle whose high is above the highs of the 3 candles before it and not
  below the 3 after it (swing low: the mirror image). It is **confirmed only after those 3 later
  candles have closed**, so a swing never appears and then disappears; the price of that honesty
  is that the newest swing is always at least 3 candles old.
- **Labels:** each swing is compared with the previous one of the same kind: HH / LH (higher /
  lower high), HL / LL (higher / lower low), EH / EL (equal, within 0.1 x ATR).
- **Structure:** last high HH and last low HL = **RISING**; LH and LL = **FALLING**; anything else
  = **MIXED** (often a range, or a turn in progress).
- **Support / resistance levels:** swing points from the last 300 closed candles that lie within
  0.5 x ATR of each other form one zone; a zone needs at least **2 touches**. Highs and lows are
  combined, because old resistance often becomes support. The nearest 3 zones above the close are
  resistance, the nearest 3 below are support, and a zone within 0.25 x ATR of the close is "at
  price". Each shows its touches and its distance in pips and in ATR. When price is above (or
  below) every level found, it says so instead of inventing one.

Fast timeframes (M15) give many small, close-together levels; H1 and D1 levels are the more
meaningful ones. Levels describe where price turned in the past: price often breaks through them,
and they are not entries, targets or signals.

## 4. Data source: yfinance (tested November 2026)

| Timeframe | History available |
|---|---|
| M1 | about 7 days |
| M5 / M15 / M30 | about 60 days |
| H1 and D1 | about 2 years (D1: about 520 trading days) |

Known problems and how the app handles them:

- **Unofficial and free.** It may be delayed, have gaps, or break without notice. Every
  failure is shown; nothing falls back silently.
- **No volume** for Forex. Stored as unknown (NULL) and shown as `n/a`, never as 0.
- **Yahoo's daily candles are wrong for FX** (their "close" is roughly the day's opening
  price). D1 candles are therefore built from hourly candles, using the standard Forex
  day that ends at 17:00 New York time.
- **A flat candle after the Friday close.** Candles stamped while the market is closed
  are discarded.
- **No candles for Sunday 22:00–24:00 UTC** (the first two hours of the week). Prices
  then correctly show STALE.
- **Quote timestamp is unreliable.** Freshness is judged only from candle times.
- Candles with missing or impossible prices are dropped and reported, never repaired.
- Bank holidays are not modelled; prices then show DELAYED or STALE.

### Indicators (v0.2)

| Column | Meaning |
|---|---|
| vs EMA20 / 50 / 200 | close minus that EMA, in pips. Positive = price above the average |
| EMAs | **STACKED UP**: EMA 20 > 50 > 200 (recent prices averaged higher than older ones, typical of a rising market). **STACKED DOWN**: the reverse. **MIXED**: not in order, or within 0.1 pip of each other |
| RSI | 0–100. **HIGH** = 70 or more (often called "overbought"), **LOW** = 30 or less ("oversold"). Shown in a neutral colour on purpose: in strong trends RSI can stay high or low for a long time |
| ATR | how far a typical candle has moved (high to low, including gaps), in pips. Says nothing about direction |

Values come from closed candles only. Each run fetches fresh data: 1,200 candles for M15 and
H1, everything available for D1. `n/a` means not enough history (next section).

### Indicator warm-up (v0.2)

EMA, RSI and ATR start from a "seed" (a plain average of the first few candles) whose
influence fades with each new candle. A value counts as trustworthy only once the seed's
influence is below about 0.03%. Before that it is shown as `n/a`, never as a number that
merely looks accurate.

| Indicator | Closed candles needed | M15 | H1 | D1 (~520 available) |
|---|---|---|---|---|
| RSI 14 / ATR 14 | 127 | yes | yes | yes |
| EMA 20 | 104 | yes | yes | yes |
| EMA 50 | 254 | yes | yes | yes |
| EMA 200 | 1,004 | yes | yes | **n/a** |

These numbers come from the formula in `analysis/indicators.py` and were checked on real
data: on 3 pairs × M15/H1 at about 400 points, a history of this length matched the full
history to within 1% of a typical candle (EMA), 0.1 RSI points and 0.5% (ATR). EMA 200
really needs about 1,000 candles; 800 was not enough. Indicators fetch 1,200 candles per
timeframe (one request each).

## 5. Local database (`data/forex.db`, not in Git)

- `candles`: one row per candle, keyed by **source + pair + timeframe + open time**.
  Different sources are never mixed. Re-fetching never creates duplicates. A candle whose
  prices changed (e.g. it was still forming) is updated and counted as "revised". The
  database itself rejects impossible prices.
- `fetch_log`: every fetch attempt, **including failures**, with counts and warnings.
- `analysis_latest` (v0.3): the newest price, trend and indicator result for each pair and
  timeframe, as JSON, with the time it was computed. Readers re-judge freshness themselves.
- `market_events` and `event_state` (v0.3): every detected event, and the tracker's memory.
- `worker_status` (v0.3): one row saying whether a worker is running, its heartbeat and next cycle.

The database has a **schema version** and upgrades itself when opened. A v0.2 database
becomes version 1 with all its candles kept. Each upgrade is all-or-nothing. It uses SQLite's
WAL mode so the dashboard can read while the worker writes.

The database is history, not a live feed: current prices always come from a new fetch.
Delete the file to start fresh; `fetch --count max` rebuilds about 2 years of history.

## 6. Architecture

Data flows one way through small, separate layers:

```
provider  ->  fetcher  ->  storage  ->  analysis  ->  display
(yfinance)    (retries,    (SQLite)     (snapshot,    (rich tables)
               pacing)                   trend)
                     \___ scan.py: shared fetch -> save -> analyse steps ___/
                                         |
                          __main__.py (commands)  /  monitor.py (loop)
```

```
src/forex_scanner/
  config.py            all settings (thresholds, pairs, pacing); secrets come from .env
  market.py            trading hours (New York time), next open, pip sizes
  fetcher.py           many pairs x timeframes, retries, pause between requests
  scan.py              fetch -> save -> analyse, shared by commands and monitor
  worker.py            the scheduled fetch -> analyse -> save loop (the only scheduled fetcher)
  monitor.py           the worker loop with full terminal tables
  readmodel.py         read side: saved results -> JSON views, freshness judged at read time
  api/app.py           the local read-only API (FastAPI)
  events.py            what changed since the last cycle (trend, EMA cross, RSI zone)
  alerts/              settings.py (preferences), rules.py (what's worth telling you),
                       brief.py (daily brief), engine.py (duplicates, cooldowns, limits,
                       quiet hours, dry run, brief schedule)
  providers/
    base.py            the contract: DataProvider, CandleSet, ProviderError
    yfinance_provider.py
  storage/db.py        SQLite: candles, fetch log, latest analysis, events, worker status, upgrades
  storage/records.py   analysis results to and from JSON
  analysis/
    snapshot.py        current price, changes, freshness status
    trend.py           UP / DOWN / SIDEWAYS
    indicators.py      EMA, RSI, ATR math and warm-up rule
    indicator_summary.py  latest readings + plain-English reasons
  display/             terminal tables (no logic)
tests/                 offline tests with fakes, plus a few `live` tests
web/                   the dashboard (React + TypeScript + Vite)
  src/api/             API types and data loading (refresh every 30 s)
  src/components/      layout, badges, "?" explanations, PriceChart, Learn drawer
  src/pages/           Overview, Markets (+ tests); placeholders for later pages
  src/lib/             formatting, glossary
  src/styles/          design tokens and styles (plain CSS)
```

**The candle contract.** Every provider returns a `CandleSet`: a pandas DataFrame indexed
by candle **open time in UTC**, oldest first, with columns
`open, high, low, close, volume, complete`, plus `source`, `fetched_at`, `warnings`.
`volume` is NaN when unknown, and `complete` is False for a candle still forming.
Analysis code only relies on this shape, never on a specific provider.

## 7. Extending it (v0.2 and later)

- **Indicators:** `analysis/indicators.py` has `ema()`, `rsi()` and `atr()`. Each takes
  candles and returns a Series. Pass only `complete` candles. New indicators go in the
  same file, tested against a slow textbook version (see `tests/test_indicators.py`).
- **Dashboard:** new data goes through `readmodel.py` (one JSON view per page) and
  `api/app.py` (a read-only endpoint), then a page in `web/src/pages/`. Calculations stay
  in Python, so Telegram can reuse them later.
- **Another data source (OANDA Practice):** add `providers/oanda_provider.py`
  implementing `DataProvider.get_candles`, register it in `providers/__init__.py`, put
  the token in `.env` (never in code), and set `DATA_PROVIDER=oanda`. Use read-only
  endpoints only. Stored data stays separate by source automatically.
- **Alerts (Telegram):** `events.py` already produces `MarketEvent`s (trend changes,
  EMA crossings, RSI zones); an alert module would receive those. The bot token goes in `.env`.
- **New command:** add a `cmd_...` function and a sub-parser in `__main__.py`, reusing
  `scan.py` for fetch -> save -> analyse.

## 8. Roadmap

- [x] **v0.1 Market scanner:** data, storage, prices, freshness, basic trend, monitor
- [x] **v0.2 Technical indicators:** EMA 20/50/200, RSI 14, ATR 14 (math checked against
      textbook definitions and an independent library; measured warm-up rule; `indicators`
      command; trend, EMA-cross and RSI-zone events in `monitor`)
- [x] **v0.3 Dashboard:** one worker writes, a read-only FastAPI + React page reads. Worker
      with saved analysis and events; local API; Overview; Markets (charts, EMA/RSI/ATR, history,
      analysis panel, Learn); Data health with read-only settings; locked future pages
- [ ] **v0.4 Alerts and Telegram** (in progress)
  - [x] A1 Alert engine: rules, identities, cooldowns, caps, hysteresis, quiet hours; recorded only
  - [x] A2 Daily brief (schedule, late rule, weekends, preview command)
  - [x] A3 Alerts page and editable preferences (protected write endpoint)
  - [x] A4 Telegram delivery (first real test message delivered 2026-11-23)
  - [ ] A5 Dry-run week (started 23 Nov 2026; review tool ready), tuning, switch on, release 0.4.0
- [ ] **v0.5 No-trade warnings and market structure** (in progress)
  - [x] B1 Trading conditions: weekend close and open, daily rollover, high volatility, stale data
        (Overview card, brief section, caution alerts)
  - [x] B2 Swing highs/lows, structure and support/resistance levels (`structure` command; saved by the worker)
  - [ ] B3 Levels on the chart and in the analysis
  - [ ] B4 Level-break events
  - [ ] B5 Release 0.5.0
- [ ] **v0.6 One written strategy + backtest**: exact rules, tested on ~2 years of H1 data
      including spread costs; you review the results before going further
- [ ] **v0.7 Forward test**: setups and signals recorded live for 2-4 weeks, not sent; compared
      with the backtest
- [ ] **v0.8 Signals to Telegram + risk calculator** (demo account only): entry zone, stop-loss,
      take-profit, risk/reward, reasons, invalidation, position size; "setup cancelled" messages.
      Switched on only with your explicit OK. A signal means "the tested rules' conditions are
      met", never a guarantee
- [ ] AI analyst explaining conditions from the app's own data
- [ ] Demo trading integration
- [ ] Trading journal
