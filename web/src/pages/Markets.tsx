import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { PAGE_DATA_MAX_AGE_MS, useChart, useOverview, usePairAnalysis, usePairEvents } from "../api/client";
import type { ChartData, Indicators, Level, PairAnalysis, Structure, Trend } from "../api/types";
import { alignmentText, Badge, DataStatusBadge, DirectionTag, PriceStatusBadge } from "../components/Badge";
import { InfoTip } from "../components/InfoTip";
import { type ChartToggles, EMA_COLORS, PriceChart } from "../components/PriceChart";
import { candleLabel, fmtAge, fmtPips, fmtPrice, fmtSigned, fmtUtc } from "../lib/format";
import { useNow } from "../lib/useNow";

const TIMEFRAMES = ["M15", "H1", "D1"] as const;
const PAGE = 300;
const MAX_CANDLES = 5000;

function Watchlist({ active, tf }: { active: string; tf: string }) {
  const { data, isError, dataUpdatedAt } = useOverview();
  const now = useNow(5000);
  const tooOld = now - dataUpdatedAt > PAGE_DATA_MAX_AGE_MS;
  return (
    <nav className="card watchlist" aria-label="Pairs">
      <h2>Watchlist</h2>
      {isError || !data || tooOld ? (
        <p className="empty">{isError ? "API not reachable." : tooOld && data ? "Refreshing…" : "Loading…"}</p>
      ) : (
        data.pairs.map((p) => (
          <Link key={p.id} to={`/markets/${p.id}?tf=${tf}`} className={`wl-row${p.id === active ? " active" : ""}`}
            aria-current={p.id === active ? "page" : undefined}>
            <span className="wl-pair">{p.pair}</span>
            {p.price ? (
              <>
                <span className="num wl-price">{fmtPrice(p.price.price, p.pair)}</span>
                <PriceStatusBadge status={p.price.status} />
                {p.price.change_24h && (
                  <span className={`num wl-chg ${p.price.change_24h.pips >= 0 ? "chg-up" : "chg-down"}`}>
                    {fmtSigned(p.price.change_24h.percent, 2)}% 24h
                  </span>
                )}
              </>
            ) : (
              <span className="dim">no price yet</span>
            )}
          </Link>
        ))
      )}
    </nav>
  );
}

function Reasons({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return (
    <ul className="reasons">
      {items.map((r, i) => (
        <li key={i}>{r}</li>
      ))}
    </ul>
  );
}

function TrendBlock({ t }: { t: Trend }) {
  return (
    <section className="ap-section">
      <h3>
        Trend <InfoTip term="trend" />
      </h3>
      <div className="ap-headline">
        <DirectionTag direction={t.direction} />
        {t.move_pips != null && (
          <span className="num dim">
            line {fmtPips(t.move_pips)} ({fmtSigned(t.move_ranges, 1)}R)
          </span>
        )}
      </div>
      <Reasons items={t.reasons} />
    </section>
  );
}

function IndicatorBlock({ i, pair }: { i: Indicators; pair: string }) {
  return (
    <section className="ap-section">
      <h3>
        Indicators <InfoTip term="ema" />
      </h3>
      <table className="kv num">
        <tbody>
          {["20", "50", "200"].map((p) => (
            <tr key={p}>
              <th scope="row" style={{ color: EMA_COLORS[`ema${p}`] }}>EMA {p}</th>
              <td>{fmtPrice(i.emas[p], pair)}</td>
              <td className="dim">{i.distance_pips[p] == null ? "n/a" : `price ${fmtPips(i.distance_pips[p])}`}</td>
            </tr>
          ))}
          <tr>
            <th scope="row">EMAs</th>
            <td colSpan={2}>{alignmentText(i.alignment)}</td>
          </tr>
          <tr>
            <th scope="row">
              RSI 14 <InfoTip term="rsi" />
            </th>
            <td>{i.rsi == null ? "n/a" : i.rsi.toFixed(1)}</td>
            <td>{i.rsi_zone === "HIGH" || i.rsi_zone === "LOW" ? <Badge tone="notice">{i.rsi_zone}</Badge> : <span className="dim">{i.rsi_zone.toLowerCase()}</span>}</td>
          </tr>
          <tr>
            <th scope="row">
              ATR 14 <InfoTip term="atr" />
            </th>
            <td>{i.atr_pips == null ? "n/a" : `${i.atr_pips.toFixed(1)} pips`}</td>
            <td className="dim">typical {i.timeframe} candle</td>
          </tr>
        </tbody>
      </table>
      <Reasons items={i.reasons} />
    </section>
  );
}

const STRUCTURE_TEXT: Record<Structure["structure"], string> = {
  RISING: "Rising: higher highs and higher lows",
  FALLING: "Falling: lower highs and lower lows",
  MIXED: "Mixed: highs and lows disagree",
  "n/a": "n/a (not enough swings yet)",
};

function zone(lv: Level, pair: string): string {
  const lo = fmtPrice(lv.low, pair);
  const hi = fmtPrice(lv.high, pair);
  return lo === hi ? lo : `${lo}–${hi}`;
}

function LevelRows({ name, levels, pair }: { name: string; levels: Level[]; pair: string }) {
  if (!levels.length) {
    return (
      <tr>
        <th scope="row">{name}</th>
        <td colSpan={3} className="dim">none found</td>
      </tr>
    );
  }
  return (
    <>
      {levels.map((lv, n) => (
        <tr key={`${name}${n}`}>
          <th scope="row">{n === 0 ? name : ""}</th>
          <td>{zone(lv, pair)}</td>
          <td className="dim">{lv.touches}x</td>
          <td className="dim">{lv.side === "AT PRICE" ? "at price" : `${lv.distance_pips.toFixed(1)} pips`}</td>
        </tr>
      ))}
    </>
  );
}

export function StructureBlock({ s, pair }: { s: Structure; pair: string }) {
  const labels = s.swings.map((x) => x.label).filter(Boolean).slice(-4);
  return (
    <section className="ap-section" aria-label="Structure">
      <h3>
        Structure and levels <InfoTip term="structure" />
      </h3>
      <div className="ap-headline">
        <strong>{STRUCTURE_TEXT[s.structure]}</strong>
        {labels.length > 0 && <span className="num dim">last swings {labels.join(" ")}</span>}
      </div>
      <table className="kv num">
        <tbody>
          <LevelRows name="Resistance" levels={s.resistance} pair={pair} />
          {s.at_price.length > 0 && <LevelRows name="At price" levels={s.at_price} pair={pair} />}
          <LevelRows name="Support" levels={s.support} pair={pair} />
        </tbody>
      </table>
      <Reasons items={s.reasons} />
    </section>
  );
}

export function AnalysisPanel({ a }: { a: PairAnalysis }) {
  const ind = a.indicators;
  const status = ind?.status ?? a.trend?.status;
  return (
    <aside className="card analysis-panel" aria-label="Analysis">
      <header className="ap-head">
        <h2>
          Analysis · {a.pair} {a.timeframe}
        </h2>
        {status && <DataStatusBadge status={status} />}
      </header>
      {ind && <p className="meta">As of the {a.timeframe === "D1" ? "daily candle" : "candle that opened"} {ind.as_of_label}</p>}
      {status === "STALE" && (
        <p className="warn-box" role="alert">
          These results are not current: the worker hasn't refreshed them recently. Don't rely on them.
        </p>
      )}
      {!a.trend && !ind ? (
        <p className="empty">No analysis saved yet for this pair and timeframe.</p>
      ) : (
        <>
          {a.trend && <TrendBlock t={a.trend} />}
          {ind && <IndicatorBlock i={ind} pair={a.pair} />}
          {a.structure && <StructureBlock s={a.structure} pair={a.pair} />}
        </>
      )}
      <section className="means">
        <h3>What this means</h3>
        <p>A description of what prices have done: direction, averages, momentum, typical movement, and where price turned before.</p>
        <h3>What it doesn't mean</h3>
        <p>It doesn't predict the next move, levels often break, and none of it is a buy or sell signal.</p>
      </section>
    </aside>
  );
}

function CandleTable({ data }: { data: ChartData }) {
  const d = data.pair.endsWith("JPY") ? 3 : 5;
  const lines = Object.fromEntries(Object.entries(data.lines).map(([k, pts]) => [k, new Map(pts.map((p) => [p.time, p.value]))]));
  const rows = data.candles.slice(-100).reverse();
  const v = (name: string, t: number, digits: number) => {
    const x = lines[name]?.get(t);
    return x === undefined ? <span className="dim">n/a</span> : x.toFixed(digits);
  };
  return (
    <div className="table-wrap candle-table">
      <table className="matrix num">
        <thead>
          <tr>
            <th>Candle (UTC)</th><th>Open</th><th>High</th><th>Low</th><th>Close</th>
            <th>EMA20</th><th>EMA50</th><th>EMA200</th><th>RSI</th><th>ATR pips</th><th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.time} className={c.complete ? "" : "is-stale"}>
              <td>{candleLabel(c.time, data.timeframe)}</td>
              <td>{c.open.toFixed(d)}</td><td>{c.high.toFixed(d)}</td><td>{c.low.toFixed(d)}</td><td>{c.close.toFixed(d)}</td>
              <td>{v("ema20", c.time, d)}</td><td>{v("ema50", c.time, d)}</td><td>{v("ema200", c.time, d)}</td>
              <td>{v("rsi14", c.time, 1)}</td><td>{v("atr14", c.time, 1)}</td>
              <td>{c.complete ? "" : <span className="lg-forming">forming</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="footnote">Newest 100 of the loaded candles. n/a = indicator not warmed up yet, or a candle still forming.</p>
    </div>
  );
}

function PairEvents({ pid }: { pid: string }) {
  const { data, isError } = usePairEvents(pid);
  if (isError) return <p className="empty">API not reachable.</p>;
  if (!data) return <p className="empty">Loading…</p>;
  if (!data.events.length) return <p className="empty">No events saved for this pair yet.</p>;
  return (
    <ul className="list events">
      {data.events.map((e, n) => (
        <li key={n}>
          <span className="num dim">{fmtUtc(e.at)}</span>
          <strong>{e.timeframe}</strong>
          <span>{e.message}</span>
        </li>
      ))}
    </ul>
  );
}

function WarmupNote({ data }: { data: ChartData }) {
  const missing = Object.entries(data.warmup).filter(([, w]) => !w.ok);
  if (!missing.length) return null;
  return (
    <p className="meta">
      Not drawn (not enough history yet):{" "}
      {missing.map(([name, w]) => `${name.toUpperCase()} needs ${w.needed} closed candles, ${w.available} available`).join("; ")}.{" "}
      <InfoTip term="warmup" />
    </p>
  );
}

export function Markets() {
  const { pid = "EURUSD" } = useParams();
  const [params, setParams] = useSearchParams();
  const tf = TIMEFRAMES.includes(params.get("tf") as (typeof TIMEFRAMES)[number]) ? params.get("tf")! : "H1";
  const [limit, setLimit] = useState(PAGE);
  const [show, setShow] = useState<ChartToggles>({ ema: true, rsi: true, atr: true, levels: true });
  const [tab, setTab] = useState<"data" | "events">("data");
  const chart = useChart(pid, tf, limit);
  const analysis = usePairAnalysis(pid, tf);
  const now = useNow(5000);

  const chartTooOld = now - chart.dataUpdatedAt > PAGE_DATA_MAX_AGE_MS * 2;
  const data = chart.data && chart.data.id === pid && chart.data.timeframe === tf ? chart.data : undefined;

  return (
    <div className="markets">
      <Watchlist active={pid} tf={tf} />

      <section className="card chart-card">
        <header className="chart-head">
          <h1 className="chart-title">{data?.pair ?? pid}</h1>
          <div className="seg" role="group" aria-label="Timeframe">
            {TIMEFRAMES.map((t) => (
              <button key={t} type="button" className={`seg-btn${t === tf ? " active" : ""}`} aria-pressed={t === tf}
                onClick={() => { setParams({ tf: t }); setLimit(PAGE); }}>
                {t}
              </button>
            ))}
          </div>
          <InfoTip term="timeframe" />
          <div className="toggles" role="group" aria-label="Indicators shown">
            {(["ema", "rsi", "atr", "levels"] as const).map((k) => (
              <label key={k}>
                <input type="checkbox" checked={show[k]} onChange={(e) => setShow({ ...show, [k]: e.target.checked })} />
                {k.toUpperCase()}
              </label>
            ))}
          </div>
          {data?.status && <DataStatusBadge status={data.status} />}
        </header>

        {chart.isError ? (
          <div className="problem" role="alert">
            <h2>No chart shown</h2>
            <p>{chart.error instanceof Error ? chart.error.message : "The local API could not be reached."}</p>
          </div>
        ) : chartTooOld && data ? (
          <div className="problem" role="alert">
            <h2>Refreshing…</h2>
            <p>This chart's data is {fmtAge((now - chart.dataUpdatedAt) / 1000)} old, so it is hidden until new data arrives.</p>
          </div>
        ) : !data ? (
          <p className="empty chart-loading">Loading chart…</p>
        ) : data.count === 0 ? (
          <p className="empty">No candles stored yet for {data.pair} {tf}. The worker saves them on its next cycle.</p>
        ) : (
          <>
            <PriceChart data={data} show={show} />
            <div className="chart-foot">
              <span className="meta">
                {data.count} of {data.stored_candles} stored candles · times in UTC · the faded candle is still forming{" "}
                <InfoTip term="forming" /> · candles <InfoTip term="candle" />
              </span>
              <button type="button" className="btn btn-sm" disabled={limit >= Math.min(MAX_CANDLES, data.stored_candles) || chart.isFetching}
                onClick={() => setLimit(Math.min(MAX_CANDLES, limit + PAGE))}>
                {chart.isFetching ? "Loading…" : "Load older candles"}
              </button>
            </div>
            <WarmupNote data={data} />
            <p className="attribution">
              Charts: TradingView Lightweight Charts™, Copyright TradingView, Inc.{" "}
              <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer noopener">tradingview.com</a>
            </p>
          </>
        )}

        <div className="tabs" role="tablist" aria-label="Details">
          <button type="button" role="tab" aria-selected={tab === "data"} className={`tab${tab === "data" ? " active" : ""}`} onClick={() => setTab("data")}>
            Candle data
          </button>
          <button type="button" role="tab" aria-selected={tab === "events"} className={`tab${tab === "events" ? " active" : ""}`} onClick={() => setTab("events")}>
            Events
          </button>
        </div>
        <div role="tabpanel">
          {tab === "data" ? (data && !chart.isError && !chartTooOld ? <CandleTable data={data} /> : null) : <PairEvents pid={pid} />}
        </div>
      </section>

      {analysis.isError ? (
        <aside className="card analysis-panel"><p className="empty">Analysis not available: API not reachable.</p></aside>
      ) : analysis.data && now - analysis.dataUpdatedAt > PAGE_DATA_MAX_AGE_MS ? (
        <aside className="card analysis-panel"><p className="empty">Refreshing analysis…</p></aside>
      ) : analysis.data ? (
        <AnalysisPanel a={analysis.data} />
      ) : (
        <aside className="card analysis-panel"><p className="empty">Loading analysis…</p></aside>
      )}
    </div>
  );
}
