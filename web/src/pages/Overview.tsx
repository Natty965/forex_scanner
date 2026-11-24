import { AlertTriangle, Info } from "lucide-react";
import { Link } from "react-router-dom";

import { PAGE_DATA_MAX_AGE_MS, useOverview } from "../api/client";
import type { Change, Condition, MarketEvent, TradingCondition, Overview as OverviewData, PairOverview } from "../api/types";
import {
  alignmentText,
  Badge,
  DataStatusBadge,
  DirectionTag,
  PriceStatusBadge,
} from "../components/Badge";
import { InfoTip } from "../components/InfoTip";
import { fmtAge, fmtPips, fmtPrice, fmtSigned, fmtUtc } from "../lib/format";
import { useNow } from "../lib/useNow";

function ChangeLine({ label, change }: { label: string; change: Change | null }) {
  if (!change) {
    return (
      <div className="change">
        <span className="change-label">{label}</span> <span className="dim">n/a (not enough history)</span>
      </div>
    );
  }
  const dir = change.pips > 0 ? "up" : change.pips < 0 ? "down" : "flat";
  return (
    <div className="change">
      <span className="change-label">{label}</span>{" "}
      <span className={`num chg-${dir}`}>
        {fmtPips(change.pips)} ({fmtSigned(change.percent, 2)}%)
      </span>
      {change.spans_gap && (
        <span className="gap-mark" title={`Compared with the last earlier price, at ${fmtUtc(change.reference_time)}`}>
          *
        </span>
      )}
    </div>
  );
}

function PairCard({ p }: { p: PairOverview }) {
  const d1 = p.timeframes["D1"]?.indicators;
  return (
    <article className="card pair-card" aria-label={p.pair}>
      <header className="card-head">
        <h3 className="pair-name">
          <Link to={`/markets/${p.id}`} title={`Open ${p.pair} chart`}>{p.pair}</Link>
        </h3>
        {p.price && (
          <span className="head-badge">
            <PriceStatusBadge status={p.price.status} />
            <InfoTip term={p.price.status} />
          </span>
        )}
      </header>

      {!p.price ? (
        <p className="empty">No price saved yet. The worker saves one on its first cycle.</p>
      ) : (
        <>
          <div className={`price num${p.price.status === "STALE" ? " is-stale" : ""}`}>
            {fmtPrice(p.price.price, p.pair)}
            <InfoTip term="price" />
          </div>
          <ChangeLine label="1h" change={p.price.change_1h} />
          <ChangeLine label="24h" change={p.price.change_24h} />
          <div className="meta">
            Price time {fmtUtc(p.price.price_time)} · {fmtAge(p.price.age_seconds)} old
          </div>
        </>
      )}

      <div className="tf-row" aria-label="Trend by timeframe">
        {["M15", "H1", "D1"].map((tf) => {
          const t = p.timeframes[tf]?.trend;
          return (
            <div key={tf} className={`tf-chip${t && t.status === "STALE" ? " is-stale" : ""}`}>
              <span className="tf-name">{tf}</span>
              {t ? <DirectionTag direction={t.direction} /> : <span className="dim">no data</span>}
            </div>
          );
        })}
      </div>
      {d1?.atr_pips != null && d1.status !== "STALE" && (
        <div className="meta">
          Typical daily range ≈ <span className="num">{d1.atr_pips.toFixed(0)} pips</span> <InfoTip term="atr" />
        </div>
      )}
    </article>
  );
}

function TrendMatrix({ pairs }: { pairs: PairOverview[] }) {
  const tfs = ["M15", "H1", "D1"];
  return (
    <section className="card">
      <header className="section-head">
        <h2>Trend &amp; indicators</h2>
        <span className="section-tips">
          Trend <InfoTip term="trend" /> EMAs <InfoTip term="ema" /> RSI <InfoTip term="rsi" />
        </span>
      </header>
      <div className="table-wrap">
        <table className="matrix">
          <thead>
            <tr>
              <th scope="col">Pair</th>
              {tfs.map((tf) => (
                <th key={tf} scope="col">
                  {tf}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pairs.map((p) => (
              <tr key={p.id}>
                <th scope="row">{p.pair}</th>
                {tfs.map((tf) => {
                  const t = p.timeframes[tf]?.trend;
                  const i = p.timeframes[tf]?.indicators;
                  if (!t || !i) return <td key={tf} className="dim">no data</td>;
                  const stale = t.status === "STALE" || i.status === "STALE";
                  return (
                    <td key={tf} className={stale ? "is-stale" : ""}>
                      <div>
                        <DirectionTag direction={t.direction} />
                      </div>
                      <div className="cell-sub">{alignmentText(i.alignment)}</div>
                      <div className="cell-sub num">
                        RSI {i.rsi == null ? "n/a" : i.rsi.toFixed(1)}
                        {(i.rsi_zone === "HIGH" || i.rsi_zone === "LOW") && <Badge tone="notice">{i.rsi_zone}</Badge>}
                      </div>
                      <div className="cell-foot">
                        <span className="dim">as of {i.as_of_label}</span>
                        {t.status !== "OK" && <DataStatusBadge status={t.status} />}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

const TRADING_CLASS: Record<TradingCondition["level"], string> = { AVOID: "warning", CAUTION: "notice", INFO: "info" };

function TradingConditions({ items }: { items: TradingCondition[] }) {
  return (
    <section className="card" aria-label="Trading conditions">
      <header className="section-head">
        <h2>Trading conditions</h2>
        <InfoTip term="trading" />
      </header>
      {items.length === 0 ? (
        <p className="empty">No warnings right now. This is not a signal to trade.</p>
      ) : (
        <ul className="list">
          {items.map((c, n) => (
            <li key={n} className={`cond cond-${TRADING_CLASS[c.level]}`}>
              {c.level === "INFO" ? <Info size={15} aria-hidden="true" /> : <AlertTriangle size={15} aria-hidden="true" />}
              <div>
                <div>
                  <strong>{c.level}: </strong>
                  {c.text}
                </div>
                <div className="why">{c.why}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="card-note">Risk warnings only. The scanner has no tested strategy yet, so it never says when to trade.</p>
    </section>
  );
}

function Conditions({ items }: { items: Condition[] }) {
  return (
    <section className="card">
      <header className="section-head">
        <h2>Worth noticing</h2>
        <InfoTip term="conditions" />
      </header>
      {items.length === 0 ? (
        <p className="empty">Nothing unusual right now.</p>
      ) : (
        <ul className="list">
          {items.map((c, n) => (
            <li key={n} className={`cond cond-${c.level}`}>
              {c.level === "warning" ? <AlertTriangle size={15} aria-hidden="true" /> : <Info size={15} aria-hidden="true" />}
              <div>
                <div>
                  {c.pair && (
                    <strong>
                      {c.pair}
                      {c.timeframe ? ` ${c.timeframe}` : ""}:{" "}
                    </strong>
                  )}
                  {c.text}
                </div>
                <div className="why">{c.why}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Events({ items }: { items: MarketEvent[] }) {
  return (
    <section className="card">
      <header className="section-head">
        <h2>Recent events</h2>
        <InfoTip term="events" />
      </header>
      {items.length === 0 ? (
        <p className="empty">
          No events yet. They appear when a trend changes, EMA 20 crosses EMA 50, or RSI enters or leaves its 70/30
          zones, usually when a new candle closes.
        </p>
      ) : (
        <ul className="list events">
          {items.map((e, n) => (
            <li key={n}>
              <span className="num dim">{fmtUtc(e.at)}</span>
              <strong>
                {e.pair} {e.timeframe}
              </strong>
              <span>{e.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function OverviewView({ data }: { data: OverviewData }) {
  return (
    <div className="overview">
      <div className="pair-grid">
        {data.pairs.map((p) => (
          <PairCard key={p.id} p={p} />
        ))}
      </div>
      {data.pairs.some((p) => p.price && (p.price.change_1h?.spans_gap || p.price.change_24h?.spans_gap)) && (
        <p className="footnote">* No price at that exact time (e.g. the weekend, or a gap in the data), so the change is measured from the last earlier price.</p>
      )}
      <div className="two-col">
        <TrendMatrix pairs={data.pairs} />
        <div className="stack">
          <TradingConditions items={data.trading_conditions} />
          <Conditions items={data.conditions} />
          <Events items={data.events} />
        </div>
      </div>
    </div>
  );
}

export function Overview() {
  const { data, isError, error, isPending, dataUpdatedAt, refetch } = useOverview();
  const now = useNow(5000);

  if (isPending) return <p className="empty">Loading…</p>;
  if (isError) {
    return (
      <div className="problem" role="alert">
        <h2>No data shown</h2>
        <p>{error instanceof Error ? error.message : "The local API could not be reached."}</p>
        <p className="dim">Prices are hidden rather than showing old values as if they were current.</p>
        <button type="button" className="btn" onClick={() => refetch()}>
          Try again
        </button>
      </div>
    );
  }
  if (now - dataUpdatedAt > PAGE_DATA_MAX_AGE_MS) {
    return (
      <div className="problem" role="alert">
        <h2>Refreshing…</h2>
        <p>This page's data is {fmtAge((now - dataUpdatedAt) / 1000)} old (the computer may have been asleep), so it is hidden until new data arrives.</p>
      </div>
    );
  }
  return <OverviewView data={data} />;
}
