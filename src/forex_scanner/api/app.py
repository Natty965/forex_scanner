"""The local API. Read-only: it serves what the worker saved and NEVER fetches market data.

Start it (together with the worker) with:  python -m forex_scanner run
Interactive documentation:                 http://localhost:8000/api/docs

Every response is built at request time, so freshness labels describe the data as it is NOW.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from forex_scanner import __version__, config, readmodel
from forex_scanner.storage import CandleStore

# Where `npm run build` (in web/) puts the finished dashboard.
WEB_DIST = config.PROJECT_ROOT / "web" / "dist"

NOT_BUILT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Forex Scanner: dashboard not built</title>
<style>body{font-family:system-ui,sans-serif;background:#0b0e14;color:#d6dbe4;max-width:40rem;margin:4rem auto;
padding:0 1rem;line-height:1.5}code{background:#1a1f2b;padding:.1rem .35rem;border-radius:4px}a{color:#6ea8fe}</style>
</head><body><h1>The dashboard hasn't been built yet</h1>
<p>The API is running (<a href="/api/docs">API documentation</a>), but the web page needs to be built once:</p>
<p><code>cd web</code><br><code>npm install</code><br><code>npm run build</code></p>
<p>Then reload this page.</p></body></html>"""

DESCRIPTION = """
Access to the Forex scanner's saved data, for the local dashboard.

* **Learning and demo only.** No trading. Nothing here is financial advice.
* The API never fetches market data itself; the worker does.
* Freshness (`status`) is judged at the moment of each request.
* The only thing that can be changed is the alert preferences, and only from the dashboard itself.
"""

# Hosts this API answers to. Anything else (e.g. a website's own name pointed at this PC,
# a trick called DNS rebinding) is refused with "400 Invalid host header".
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
# The Vite development server (npm run dev) is also allowed to make changes.
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


def require_dashboard_origin(request: Request) -> None:
    """Changes must come from the dashboard page itself, as JSON.

    Browsers always send an Origin header with a change request (PUT/POST), and a different
    website can't fake it. Requiring JSON also means a browser would first ask permission
    (a "preflight"), which this API never grants to other websites.
    """
    origin = request.headers.get("origin")
    own = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin is None or (origin != own and origin not in DEV_ORIGINS):
        raise HTTPException(403, "Changes are only accepted from the dashboard itself.")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "Send changes as JSON.")


def create_app(db_path: Path | str | None = None, source: str | None = None,
               now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
               web_dist: Path | None = None) -> FastAPI:
    db_path = db_path or config.DB_PATH
    source = source or config.DATA_PROVIDER
    web_dist = (web_dist or WEB_DIST).resolve()

    app = FastAPI(title="Forex Scanner API", version=__version__, description=DESCRIPTION,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    def store() -> Iterator[CandleStore]:
        # One short-lived connection per request (safe with WAL). FastAPI may run this setup and the
        # cleanup below in different threads, hence check_same_thread=False (see CandleStore).
        s = CandleStore(db_path, check_same_thread=False)
        try:
            yield s
        finally:
            s.close()

    Store = Annotated[CandleStore, Depends(store)]   # "give this endpoint a database connection"

    def resolve_pair(pid: str) -> str:
        pair = readmodel.pair_from_id(pid)
        if pair is None:
            known = ", ".join(readmodel.pair_id(p) for p in config.PAIRS)
            raise HTTPException(404, f"Unknown pair '{pid}'. Known: {known}")
        return pair

    def check_timeframe(timeframe: str) -> str:
        if timeframe not in config.TIMEFRAMES:
            raise HTTPException(422, f"Unknown timeframe '{timeframe}'. Use one of: {', '.join(config.TIMEFRAMES)}")
        return timeframe

    @app.get("/api/health", tags=["status"], summary="Is the API up?")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/status", tags=["status"],
             summary="Worker health, market open/closed, configured pairs and freshness thresholds")
    def status(s: Store) -> dict:
        return readmodel.status_view(s, source, now())

    @app.get("/api/data-health", tags=["status"],
             summary="Worker, stored data per pair/timeframe, recent failures, and the current settings")
    def data_health(s: Store) -> dict:
        return readmodel.data_health_view(s, source, now())

    @app.get("/api/overview", tags=["market"],
             summary="Prices, trends and indicators for every pair and timeframe, plus recent events")
    def overview(s: Store) -> dict:
        return readmodel.overview_view(s, source, now())

    @app.get("/api/pairs/{pid}/analysis", tags=["market"],
             summary="Price, trend and indicators for one pair and timeframe, with plain-English reasons")
    def pair_analysis(s: Store, pid: str,
                      timeframe: str = Query("H1", description="M15, H1 or D1")) -> dict:
        return readmodel.pair_analysis_view(s, source, resolve_pair(pid), check_timeframe(timeframe), now())

    @app.get("/api/pairs/{pid}/candles", tags=["market"],
             summary="Stored candles for one pair and timeframe, oldest first")
    def candles(s: Store, pid: str, timeframe: str = Query("H1", description="M15, H1 or D1"),
                limit: int = Query(300, ge=1, le=5000)) -> dict:
        return readmodel.candles_view(s, source, resolve_pair(pid), check_timeframe(timeframe), limit)

    @app.get("/api/pairs/{pid}/chart", tags=["market"],
             summary="Candles plus EMA, RSI and ATR lines for a chart (closed candles, warm-up respected)")
    def chart(s: Store, pid: str, timeframe: str = Query("H1", description="M15, H1 or D1"),
              limit: int = Query(300, ge=10, le=5000, description="newest candles to return")) -> dict:
        return readmodel.chart_view(s, source, resolve_pair(pid), check_timeframe(timeframe), limit, now())

    @app.get("/api/events", tags=["market"],
             summary="Detected events (trend changes, EMA crossings, RSI zones), newest first")
    def events(s: Store, limit: int = Query(50, ge=1, le=1000),
               pair: str | None = Query(None, description="e.g. EURUSD")) -> dict:
        return readmodel.events_view(s, source, limit, resolve_pair(pair) if pair else None)

    # ---------- alerts ----------

    @app.get("/api/alerts", tags=["alerts"], summary="Recorded alerts (newest first), sending mode, daily briefs")
    def alerts(s: Store, limit: int = Query(200, ge=1, le=1000),
               type: str | None = Query(None, description="unusual_move, market_events, data or brief"),  # noqa: A002
               delivery: str | None = Query(None, description="e.g. dry_run, held_quiet, pending")) -> dict:
        return readmodel.alerts_view(s, now(), limit, type, delivery)

    @app.get("/api/alert-settings", tags=["alerts"], summary="Current alert preferences and their defaults")
    def get_alert_settings(s: Store) -> dict:
        return readmodel.alert_settings_view(s)

    @app.put("/api/alert-settings", tags=["alerts"], dependencies=[Depends(require_dashboard_origin)],
             summary="Change alert preferences (dashboard only). Send only the settings to change.")
    def put_alert_settings(s: Store, changes: Annotated[dict, Body()]) -> dict:
        from forex_scanner.alerts.settings import apply_changes, load_settings, save_settings

        new, problems = apply_changes(load_settings(s), changes)
        if problems:
            raise HTTPException(422, {"message": "Nothing was changed.", "problems": problems})
        save_settings(s, new, now())
        return readmodel.alert_settings_view(s)

    @app.get("/api/telegram", tags=["alerts"], summary="Is Telegram set up, and how did the last sends go?")
    def telegram_status(s: Store) -> dict:
        return readmodel.telegram_view(s)

    @app.post("/api/telegram/test", tags=["alerts"], dependencies=[Depends(require_dashboard_origin)],
              summary="Send one test message to your Telegram chat (dashboard only)")
    def telegram_test(s: Store) -> dict:
        from forex_scanner.alerts.sender import send_test_message
        from forex_scanner.alerts.settings import load_settings
        from forex_scanner.alerts.telegram import TelegramClient, TelegramConfig

        cfg = TelegramConfig.from_env()
        if cfg is None:
            raise HTTPException(409, "Telegram isn't set up yet. Run: python -m forex_scanner telegram-setup")
        return send_test_message(s, TelegramClient(cfg.token), cfg.chat_id, now(), load_settings(s))

    # ---------- the dashboard (React build) ----------
    # Defined last, so every real /api route above takes priority.

    @app.get("/api/{rest:path}", include_in_schema=False)
    def api_not_found(rest: str) -> None:
        raise HTTPException(404, f"No API endpoint /api/{rest}. See /api/docs")

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str):
        index = web_dist / "index.html"
        if not index.is_file():
            return HTMLResponse(NOT_BUILT_PAGE, status_code=503)
        candidate = (web_dist / path).resolve()
        # Serve real files (scripts, styles, fonts) only from inside the build folder.
        if path and candidate.is_file() and candidate.is_relative_to(web_dist):
            return FileResponse(candidate)
        # Any other path (/, /markets, /alerts...) is a page of the app: index.html handles it.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
