"""Command line entry point.

    python -m forex_scanner                 -> environment check
    python -m forex_scanner candles         -> latest EUR/USD M15 candles
    python -m forex_scanner candles --pair GBP/USD --timeframe H1 --count 20
    python -m forex_scanner fetch           -> all pairs x M15/H1/D1, summary table
    python -m forex_scanner fetch --timeframes H1 --count max
    python -m forex_scanner prices          -> current prices, 1h/24h change, freshness
    python -m forex_scanner trend           -> UP/DOWN/SIDEWAYS per pair and timeframe, with reasons
    python -m forex_scanner indicators      -> EMA 20/50/200, RSI 14, ATR 14 with explanations
    python -m forex_scanner structure       -> swing highs/lows, support and resistance levels
    python -m forex_scanner monitor         -> keep refreshing prices, trends, indicators (Ctrl+C to stop)
    python -m forex_scanner worker          -> collect + analyse + save every 5 min (for the dashboard)
    python -m forex_scanner run             -> worker + dashboard at http://localhost:8000 (+ /api/docs)
    python -m forex_scanner dashboard       -> same as run, and opens the dashboard in your browser
    python -m forex_scanner alerts          -> recorded alerts (--show ID, --settings, --review)
    python -m forex_scanner brief           -> preview today's daily brief
    python -m forex_scanner telegram-setup  -> check the bot token, find your chat id
    python -m forex_scanner telegram-test   -> send one test message
    python -m forex_scanner stored          -> what is in the local database
"""

import argparse
import os
import sys

from forex_scanner import __version__, config
from forex_scanner.providers import ProviderError, get_provider
from forex_scanner.providers.base import TIMEFRAME_DURATIONS


def cmd_check() -> int:
    print(f"Forex Scanner v{__version__} (learning/demo only, no real trading)")
    print(f"Python      : {sys.version.split()[0]}")
    print(f"Interpreter : {sys.executable}")
    print(f"Project root: {config.PROJECT_ROOT}")
    print(f".env found  : {(config.PROJECT_ROOT / '.env').exists()}")
    print(f"Provider    : {config.DATA_PROVIDER}")
    print("Environment OK. Try: python -m forex_scanner prices   (or --help for all commands)")
    return 0


def cmd_candles(pair: str, timeframe: str, count: int) -> int:
    from forex_scanner.display.candles_table import print_candles

    try:
        candle_set = get_provider(config.DATA_PROVIDER).get_candles(pair, timeframe, count)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        print("No prices shown, because there is no fresh data to show.", file=sys.stderr)
        return 1
    print_candles(candle_set)
    return 0


def cmd_fetch(pairs: list[str], timeframes: list[str], count: int | None, save: bool) -> int:
    from forex_scanner.display.fetch_summary import print_fetch_summary
    from forex_scanner.fetcher import fetch_many
    from forex_scanner.storage import CandleStore

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    results = fetch_many(provider, pairs, timeframes, count,
                         delay_seconds=config.REQUEST_DELAY_SECONDS, max_retries=config.MAX_RETRIES)

    saved = None
    if save:
        saved = {}
        with CandleStore(config.DB_PATH) as store:
            for r in results:
                if r.ok:
                    saved[(r.pair, r.timeframe)] = store.save(r.candle_set)
                else:
                    store.log_failure(provider.name, r.pair, r.timeframe, r.error)
    print_fetch_summary(results, saved)
    if save:
        print(f"Saved to {config.DB_PATH}")
    return 0 if all(r.ok for r in results) else 1


def _open_store(save: bool):
    from contextlib import nullcontext

    from forex_scanner.storage import CandleStore

    return CandleStore(config.DB_PATH) if save else nullcontext(None)


def cmd_prices(pairs: list[str], save: bool) -> int:
    from forex_scanner.display.prices_table import print_prices
    from forex_scanner.scan import get_prices

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    with _open_store(save) as store:
        snapshots, failures = get_prices(provider, pairs, store)
    print_prices(snapshots, failures, config.LIVE_MAX_AGE_MINUTES, config.DELAYED_MAX_AGE_MINUTES)
    return 0 if not failures else 1


def cmd_trend(pairs: list[str], timeframes: list[str], save: bool, explain: bool) -> int:
    from forex_scanner.display.trend_table import print_trends
    from forex_scanner.scan import get_trends

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    with _open_store(save) as store:
        rows, failures = get_trends(provider, pairs, timeframes, store)
    print_trends(rows, failures, provider.name, explain=explain)
    return 0 if not failures else 1


def cmd_indicators(pairs: list[str], timeframes: list[str], save: bool, explain: bool) -> int:
    from forex_scanner.display.indicators_table import print_indicators
    from forex_scanner.scan import get_indicators

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    with _open_store(save) as store:
        rows, failures = get_indicators(provider, pairs, timeframes, store)
    print_indicators(rows, failures, provider.name, config.EMA_PERIODS, explain=explain)
    return 0 if not failures else 1


def cmd_structure(pairs: list[str], timeframes: list[str], save: bool, explain: bool) -> int:
    from forex_scanner.display.structure_table import print_structure
    from forex_scanner.scan import get_structure

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    with _open_store(save) as store:
        rows, failures = get_structure(provider, pairs, timeframes, store)
    print_structure(rows, failures, provider.name, explain=explain)
    return 0 if not failures else 1


def cmd_monitor(pairs: list[str], timeframes: list[str], interval_min: float, save: bool,
                cycles: int | None, force: bool) -> int:
    from datetime import timedelta

    from forex_scanner.monitor import run_monitor
    from forex_scanner.storage import WorkerBusy

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        run_monitor(
            provider, pairs, timeframes,
            interval=timedelta(minutes=interval_min),
            closed_interval=timedelta(minutes=config.MONITOR_CLOSED_INTERVAL_MINUTES),
            live_max=config.LIVE_MAX_AGE_MINUTES, delayed_max=config.DELAYED_MAX_AGE_MINUTES,
            db_path=config.DB_PATH if save else None, cycles=cycles, force=force,
        )
    except WorkerBusy as exc:
        print(f"NOT STARTED: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_worker(pairs: list[str], timeframes: list[str], interval_min: float, cycles: int | None,
               force: bool) -> int:
    """Background data collection: fetch, analyse and save on a schedule, one line per cycle."""
    from datetime import timedelta

    from forex_scanner.storage import WorkerBusy
    from forex_scanner.worker import print_cycle_line, run_loop

    try:
        provider = get_provider(config.DATA_PROVIDER)
    except ProviderError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        done = run_loop(
            provider, pairs, timeframes,
            interval=timedelta(minutes=interval_min),
            closed_interval=timedelta(minutes=config.MONITOR_CLOSED_INTERVAL_MINUTES),
            db_path=config.DB_PATH, cycles=cycles, force=force,
            on_start=lambda: print(
                f"Worker started: {', '.join(pairs)} x {', '.join(['M1 prices', *timeframes])}, every "
                f"{interval_min:g} min while the market is open. Saving to {config.DB_PATH}. Ctrl+C to stop."),
            on_cycle=lambda info: print_cycle_line(info),
            on_stop=lambda n: print(f"Worker stopped after {n} cycle(s)."),
        )
    except WorkerBusy as exc:
        print(f"NOT STARTED: {exc}", file=sys.stderr)
        return 1
    if cycles is not None:
        print(f"Worker finished {done} cycle(s).")
    return 0


def cmd_run(interval_min: float, port: int, with_worker: bool, force: bool, open_browser: bool = False) -> int:
    """Worker (background thread) + local API and dashboard (foreground), in one process.
    Ctrl+C stops both."""
    import threading
    import webbrowser
    from datetime import timedelta

    import uvicorn

    from forex_scanner.api.app import create_app
    from forex_scanner.storage import WorkerBusy
    from forex_scanner.worker import print_cycle_line, run_loop

    stop = threading.Event()

    def interruptible_sleep(seconds: float) -> None:
        if stop.wait(seconds):              # Ctrl+C pressed: end the worker loop cleanly
            raise KeyboardInterrupt

    def worker_main() -> None:
        try:
            provider = get_provider(config.DATA_PROVIDER)
            run_loop(
                provider, config.PAIRS, config.TIMEFRAMES,
                interval=timedelta(minutes=interval_min),
                closed_interval=timedelta(minutes=config.MONITOR_CLOSED_INTERVAL_MINUTES),
                db_path=config.DB_PATH, force=force, sleep=interruptible_sleep,
                on_start=lambda: print(f"Worker started: every {interval_min:g} min while the market is open."),
                on_cycle=lambda info: print_cycle_line(info),
                on_stop=lambda n: print(f"Worker stopped after {n} cycle(s)."),
            )
        except WorkerBusy as exc:
            print(f"Worker NOT started: {exc}\nThe API still serves whatever that other worker saves.")
        except Exception as exc:            # keep the API up; the status endpoint will show the worker stopped
            print(f"Worker stopped by an error: {exc}")

    thread = None
    if with_worker:
        thread = threading.Thread(target=worker_main, name="worker")
        thread.start()
    from forex_scanner.api.app import WEB_DIST

    url = f"http://localhost:{port}"
    print(f"Dashboard: {url}   API docs: {url}/api/docs   (this PC only). Press Ctrl+C to stop.")
    if not (WEB_DIST / "index.html").is_file():
        print("Note: the dashboard page isn't built yet. Run once:  cd web; npm install; npm run build")
    if open_browser:
        threading.Timer(2.0, webbrowser.open, args=[url]).start()
    try:
        uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
    finally:
        stop.set()
        if thread is not None and thread.is_alive():
            print("Stopping the worker (finishing any download in progress)...")
            thread.join()
    return 0


def parse_interval(value: str) -> float:
    minutes = float(value)
    if minutes < 2:
        raise argparse.ArgumentTypeError("interval must be at least 2 minutes (be polite to the free source)")
    return minutes


def cmd_alerts_review(days: int) -> int:
    from datetime import datetime, timezone

    from forex_scanner.alerts.review import format_review, review
    from forex_scanner.alerts.settings import load_settings
    from forex_scanner.storage import CandleStore

    with CandleStore(config.DB_PATH) as store:
        s = load_settings(store)
        print(format_review(review(store, datetime.now(timezone.utc), s, days), s))
    return 0


def cmd_alerts(limit: int, show: int | None, settings: bool) -> int:
    """List recorded alerts (newest first), show one in full, or show the alert settings."""
    from dataclasses import asdict

    from rich import box
    from rich.console import Console
    from rich.table import Table

    from forex_scanner.alerts.settings import load_settings
    from forex_scanner.storage import CandleStore

    console = Console()
    with CandleStore(config.DB_PATH) as store:
        s = load_settings(store)
        if settings:
            for k, v in asdict(s).items():
                console.print(f"{k:26} {v}")
            console.print("\nChangeable on the Alerts page (milestone A3).")
            return 0
        alerts = store.load_alerts(limit=10_000 if show else limit)
    if show is not None:
        a = next((x for x in alerts if x["id"] == show), None)
        if a is None:
            print(f"No alert with id {show}.", file=sys.stderr)
            return 1
        console.print(f"[bold]#{a['id']} {a['title']}[/]  ({a['type']}, {a['severity']})")
        console.print(a["body"])
        console.print(f"\nDelivery: {a['delivery']}" + (f" ({a['delivery_note']})" if a["delivery_note"] else ""))
        return 0
    mode = "DRY RUN: nothing is sent" if s.dry_run else ("PAUSED" if s.paused else "sending on")
    table = Table(title=f"Alerts, newest first ({mode}) · times in {s.timezone}", box=box.SIMPLE_HEAD)
    for col in ("#", "Time", "Type", "Pair / TF", "Title", "Delivery"):
        table.add_column(col)
    for a in alerts:
        table.add_row(str(a["id"]), f"{a['created_at'].astimezone(s.tz):%m-%d %H:%M}", a["type"],
                      " ".join(x for x in (a["pair"], a["timeframe"]) if x) or "-", a["title"], a["delivery"])
    console.print(table)
    if not alerts:
        console.print("No alerts recorded yet. The worker records them as conditions occur.")
    console.print("Show one in full: python -m forex_scanner alerts --show <#>")
    return 0


def cmd_brief() -> int:
    """Preview today's daily brief from the worker's saved data. Records and sends nothing."""
    from datetime import datetime, timezone

    from forex_scanner import readmodel
    from forex_scanner.alerts.brief import compose_brief
    from forex_scanner.alerts.settings import load_settings
    from forex_scanner.storage import CandleStore
    from forex_scanner.storage.records import indicators_from, snapshot_from, trend_from

    now = datetime.now(timezone.utc)
    source = config.DATA_PROVIDER
    with CandleStore(config.DB_PATH) as store:
        s = load_settings(store)
        recs = store.load_analysis(source)
        if not recs:
            print("Nothing saved yet. Start the worker first: python -m forex_scanner run")
            return 1
        snapshots = [snapshot_from(r.payload) for r in recs if r.kind == "price"]
        trends = [(trend_from(r.payload), readmodel.analysis_status(r, now)) for r in recs if r.kind == "trend"]
        inds = [(indicators_from(r.payload), readmodel.analysis_status(r, now)) for r in recs if r.kind == "indicators"]
        today = store.get_brief_log(f"{now.astimezone(s.tz):%Y-%m-%d}")
        found = readmodel.stored_trading_conditions(store, source, now)
    from forex_scanner.alerts.brief import brief_due_at

    due = brief_due_at(now.astimezone(s.tz), s)
    c = compose_brief(now, s, snapshots, trends, inds, conditions=found)
    print(f"PREVIEW (not recorded, not sent) · built from the worker's saved data at {now:%H:%M} UTC\n")
    print(c.title)
    print(c.body)
    if today:
        status = today["status"]
    elif now < due:
        status = f"not yet (due {s.brief_time} {s.place})"
    else:
        status = (f"not created yet: it was due at {s.brief_time} {s.place}. A running worker creates it "
                  "on its next cycle if that is within 3 hours of the due time; otherwise today is skipped.")
    print(f"\nToday's scheduled brief: {status}")
    return 0


def cmd_telegram_setup() -> int:
    """Check the bot token and list the chats that have messaged the bot, to find your chat id."""
    from forex_scanner.alerts.telegram import TelegramClient, TelegramError, token_from_env

    token = token_from_env()
    if not token:
        print("No TELEGRAM_BOT_TOKEN in .env yet.\n"
              "1. In Telegram, open @BotFather, send /newbot and follow the steps.\n"
              "2. Copy the token it gives you into .env:  TELEGRAM_BOT_TOKEN=...\n"
              "3. Send any message (e.g. 'hi') to your new bot, then run this command again.")
        return 1
    client = TelegramClient(token)
    try:
        me = client.get_me()
        print(f"Token OK: bot @{me.get('username')} ({me.get('first_name')}).")
        updates = client.get_updates()
    except TelegramError as exc:
        print(f"Telegram refused or couldn't be reached: {exc}", file=sys.stderr)
        return 1
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat
    if not chats:
        print("No messages found. Send any message to your bot in Telegram, then run this again.")
        return 1
    print("\nChats that have messaged your bot:")
    for cid, c in chats.items():
        who = c.get("username") and f"@{c['username']}" or c.get("first_name") or c.get("title") or "?"
        print(f"  chat id {cid}   ({c.get('type')}, {who})")
    current = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if current:
        print(f"\nTELEGRAM_CHAT_ID in .env is {current}: "
              f"{'matches one above.' if current in {str(c) for c in chats} else 'NOT in the list above, check it.'}")
    else:
        print("\nPut YOUR chat id into .env as  TELEGRAM_CHAT_ID=<id>  (the bot will only ever send there).")
    return 0


def cmd_telegram_test() -> int:
    """Send one test message to the configured chat."""
    from datetime import datetime, timezone

    from forex_scanner.alerts.sender import send_test_message
    from forex_scanner.alerts.settings import load_settings
    from forex_scanner.alerts.telegram import TelegramClient, TelegramConfig
    from forex_scanner.storage import CandleStore

    cfg = TelegramConfig.from_env()
    if cfg is None:
        print("Telegram isn't set up: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are needed in .env. "
              "Run: python -m forex_scanner telegram-setup", file=sys.stderr)
        return 1
    with CandleStore(config.DB_PATH) as store:
        r = send_test_message(store, TelegramClient(cfg.token), cfg.chat_id, datetime.now(timezone.utc),
                              load_settings(store))
    print("Test message sent. Check Telegram." if r["ok"] else f"Not sent: {r['error']}")
    return 0 if r["ok"] else 1


def cmd_stored() -> int:
    from forex_scanner.display.stored_summary import print_stored_summary
    from forex_scanner.storage import CandleStore

    if not config.DB_PATH.exists():
        print(f"No database yet at {config.DB_PATH}. Run: python -m forex_scanner fetch")
        return 1
    with CandleStore(config.DB_PATH) as store:
        print_stored_summary(store.summary(), config.DB_PATH)
    return 0


def parse_count(value: str) -> int | None:
    """'max' means as much history as the source allows."""
    if value.lower() == "max":
        return None
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("count must be at least 1, or 'max'")
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forex_scanner", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    c = sub.add_parser("candles", help="fetch and print recent candles")
    c.add_argument("--pair", default="EUR/USD", choices=config.PAIRS)
    c.add_argument("--timeframe", default="M15", choices=list(TIMEFRAME_DURATIONS))
    c.add_argument("--count", type=parse_count, default=10)

    f = sub.add_parser("fetch", help="fetch several pairs/timeframes and show a summary")
    f.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    f.add_argument("--timeframes", nargs="+", default=config.TIMEFRAMES, choices=list(TIMEFRAME_DURATIONS))
    f.add_argument("--count", type=parse_count, default=200, help="number of candles, or 'max'")
    f.add_argument("--no-save", action="store_true", help="do not write to the local database")

    p = sub.add_parser("prices", help="current prices, 1h/24h change and freshness")
    p.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    p.add_argument("--no-save", action="store_true", help="do not write to the local database")

    t = sub.add_parser("trend", help="basic trend (UP/DOWN/SIDEWAYS) with reasons")
    t.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    t.add_argument("--timeframes", nargs="+", default=config.TIMEFRAMES, choices=list(TIMEFRAME_DURATIONS))
    t.add_argument("--brief", action="store_true", help="table only, no explanations")
    t.add_argument("--no-save", action="store_true", help="do not write to the local database")

    ind = sub.add_parser("indicators", help="EMA 20/50/200, RSI 14, ATR 14 with explanations")
    ind.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    ind.add_argument("--timeframes", nargs="+", default=config.TIMEFRAMES, choices=list(TIMEFRAME_DURATIONS))
    ind.add_argument("--brief", action="store_true", help="table only, no explanations")
    ind.add_argument("--no-save", action="store_true", help="do not write to the local database")

    st = sub.add_parser("structure", help="swing highs/lows, support and resistance levels, with reasons")
    st.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    st.add_argument("--timeframes", nargs="+", default=config.TIMEFRAMES, choices=list(TIMEFRAME_DURATIONS))
    st.add_argument("--brief", action="store_true", help="table only, no explanations")
    st.add_argument("--no-save", action="store_true", help="do not write to the local database")

    m = sub.add_parser("monitor", help="keep refreshing prices, trends and indicators until Ctrl+C")
    m.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    m.add_argument("--timeframes", nargs="+", default=config.TIMEFRAMES, choices=list(TIMEFRAME_DURATIONS))
    m.add_argument("--interval", type=parse_interval, default=config.MONITOR_INTERVAL_MINUTES,
                   help="minutes between refreshes while the market is open (minimum 2)")
    m.add_argument("--cycles", type=int, default=None, help="stop after this many refreshes")
    m.add_argument("--no-save", action="store_true",
                   help="do not write to the local database (also skips the one-worker check)")
    m.add_argument("--force", action="store_true", help="start even if another worker seems to be running")

    w = sub.add_parser("worker", help="collect and analyse data on a schedule (for the dashboard)")
    w.add_argument("--pairs", nargs="+", default=config.PAIRS, choices=config.PAIRS)
    w.add_argument("--timeframes", nargs="+", default=config.TIMEFRAMES, choices=list(TIMEFRAME_DURATIONS))
    w.add_argument("--interval", type=parse_interval, default=config.MONITOR_INTERVAL_MINUTES,
                   help="minutes between cycles while the market is open (minimum 2)")
    w.add_argument("--cycles", type=int, default=None, help="stop after this many cycles")
    w.add_argument("--force", action="store_true", help="start even if another worker seems to be running")

    r = sub.add_parser("run", help="start the worker and the local API together (Ctrl+C stops both)")
    r.add_argument("--port", type=int, default=8000)
    r.add_argument("--interval", type=parse_interval, default=config.MONITOR_INTERVAL_MINUTES,
                   help="worker minutes between cycles while the market is open (minimum 2)")
    r.add_argument("--no-worker", action="store_true", help="API only (serve what is already saved)")
    r.add_argument("--force", action="store_true", help="start the worker even if another seems to be running")

    d = sub.add_parser("dashboard", help="same as 'run', and opens the dashboard in your browser")
    d.add_argument("--port", type=int, default=8000)
    d.add_argument("--interval", type=parse_interval, default=config.MONITOR_INTERVAL_MINUTES,
                   help="worker minutes between cycles while the market is open (minimum 2)")
    d.add_argument("--force", action="store_true", help="start the worker even if another seems to be running")

    al = sub.add_parser("alerts", help="list recorded alerts (v0.4: recorded only, nothing sent yet)")
    al.add_argument("--limit", type=int, default=30)
    al.add_argument("--show", type=int, default=None, metavar="ID", help="show one alert in full")
    al.add_argument("--settings", action="store_true", help="show the current alert settings")
    al.add_argument("--review", action="store_true", help="summary of recent alerts, with tuning suggestions")
    al.add_argument("--days", type=int, default=7, help="days covered by --review (default 7)")

    sub.add_parser("brief", help="preview today's daily brief (records and sends nothing)")
    sub.add_parser("telegram-setup", help="check the bot token and find your chat id")
    sub.add_parser("telegram-test", help="send one test message to your Telegram chat")
    sub.add_parser("stored", help="show what is saved in the local database")
    args = parser.parse_args(argv)

    if args.command == "candles":
        return cmd_candles(args.pair, args.timeframe, args.count)
    if args.command == "fetch":
        return cmd_fetch(args.pairs, args.timeframes, args.count, save=not args.no_save)
    if args.command == "prices":
        return cmd_prices(args.pairs, save=not args.no_save)
    if args.command == "trend":
        return cmd_trend(args.pairs, args.timeframes, save=not args.no_save, explain=not args.brief)
    if args.command == "indicators":
        return cmd_indicators(args.pairs, args.timeframes, save=not args.no_save, explain=not args.brief)
    if args.command == "structure":
        return cmd_structure(args.pairs, args.timeframes, save=not args.no_save, explain=not args.brief)
    if args.command == "monitor":
        return cmd_monitor(args.pairs, args.timeframes, args.interval, save=not args.no_save,
                           cycles=args.cycles, force=args.force)
    if args.command == "worker":
        return cmd_worker(args.pairs, args.timeframes, args.interval, args.cycles, args.force)
    if args.command == "run":
        return cmd_run(args.interval, args.port, with_worker=not args.no_worker, force=args.force)
    if args.command == "dashboard":
        return cmd_run(args.interval, args.port, with_worker=True, force=args.force, open_browser=True)
    if args.command == "alerts":
        if args.review:
            return cmd_alerts_review(args.days)
        return cmd_alerts(args.limit, args.show, args.settings)
    if args.command == "brief":
        return cmd_brief()
    if args.command == "telegram-setup":
        return cmd_telegram_setup()
    if args.command == "telegram-test":
        return cmd_telegram_test()
    if args.command == "stored":
        return cmd_stored()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
