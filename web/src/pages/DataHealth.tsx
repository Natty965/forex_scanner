import { PAGE_DATA_MAX_AGE_MS, useDataHealth } from "../api/client";
import type { DataHealth as DataHealthData, ItemHealth } from "../api/types";
import { Badge, WorkerBadge } from "../components/Badge";
import { InfoTip } from "../components/InfoTip";
import { fmtAge, fmtUtc, fmtUtcDay } from "../lib/format";
import { useNow } from "../lib/useNow";

const HEALTH: Record<ItemHealth, { tone: "live" | "stale" | "delayed" | "muted"; help: string }> = {
  OK: { tone: "live", help: "Refreshed by the worker recently." },
  FAILING: { tone: "stale", help: "The latest attempt to fetch this failed. See the error." },
  "NOT UPDATING": { tone: "delayed", help: "No successful fetch for a while: is the worker running?" },
  "NOT MONITORED": { tone: "muted", help: "Saved once (e.g. with the fetch command); the worker doesn't refresh it." },
};

function fmtBytes(n: number): string {
  return n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function WorkerCard({ d }: { d: DataHealthData }) {
  const w = d.worker;
  return (
    <section className="card">
      <header className="section-head">
        <h2>Worker</h2>
        <InfoTip term="worker" />
      </header>
      <div className="dh-worker">
        <WorkerBadge label={w.label} />
        {w.label === "NEVER STARTED" ? (
          <p className="empty">The worker has never run on this database. Start it with: python -m forex_scanner run</p>
        ) : (
          <table className="kv num">
            <tbody>
              <tr><th scope="row">Started</th><td>{fmtUtcDay(w.started_at)}</td></tr>
              <tr><th scope="row">Last cycle</th><td>{fmtUtcDay(w.last_cycle_at)}</td></tr>
              <tr><th scope="row">Next cycle</th><td>{w.next_run_at ? fmtUtcDay(w.next_run_at) : "none planned"}</td></tr>
              <tr><th scope="row">Cycles</th><td>{w.cycles}</td></tr>
              <tr><th scope="row">Last result</th><td>{w.message ?? "n/a"}</td></tr>
              {w.pid && <tr><th scope="row">Process</th><td>{w.pid}</td></tr>}
            </tbody>
          </table>
        )}
        {w.label === "NOT RESPONDING" && (
          <p className="warn-box">The worker claimed to be running but missed its schedule (it may have crashed, or the computer slept). Data on the other pages will turn STALE.</p>
        )}
      </div>
    </section>
  );
}

function StorageCard({ d }: { d: DataHealthData }) {
  return (
    <section className="card">
      <header className="section-head">
        <h2>Database</h2>
      </header>
      <table className="kv num">
        <tbody>
          <tr><th scope="row">File</th><td className="break">{d.database.path}</td></tr>
          <tr><th scope="row">Size</th><td>{fmtBytes(d.database.size_bytes)}</td></tr>
          <tr><th scope="row">Schema version</th><td>{d.database.schema_version}</td></tr>
          <tr><th scope="row">Fetches, last 24 h</th>
            <td>{d.fetches_24h.ok} ok · <span className={d.fetches_24h.failed ? "chg-down" : ""}>{d.fetches_24h.failed} failed</span></td></tr>
          <tr><th scope="row">Source</th><td>{d.source}</td></tr>
        </tbody>
      </table>
    </section>
  );
}

function ItemsTable({ d }: { d: DataHealthData }) {
  const items = [...d.items].sort((a, b) => Number(b.monitored) - Number(a.monitored) || a.pair.localeCompare(b.pair)
    || ["M1", "M5", "M15", "M30", "H1", "D1"].indexOf(a.timeframe) - ["M1", "M5", "M15", "M30", "H1", "D1"].indexOf(b.timeframe));
  return (
    <section className="card">
      <header className="section-head">
        <h2>Stored data</h2>
      </header>
      {items.length === 0 ? (
        <p className="empty">Nothing stored yet. The worker saves data on its first cycle.</p>
      ) : (
        <div className="table-wrap">
          <table className="matrix num">
            <thead>
              <tr><th>Pair</th><th>TF</th><th>Health</th><th>Candles</th><th>From</th><th>To</th><th>Last good fetch</th><th>Latest attempt</th></tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={`${i.pair}-${i.timeframe}`}>
                  <th scope="row">{i.pair}</th>
                  <td>{i.timeframe}</td>
                  <td><span title={HEALTH[i.health].help}><Badge tone={HEALTH[i.health].tone}>{i.health}</Badge></span></td>
                  <td>{i.candles.toLocaleString("en-GB")}</td>
                  <td>{i.first_candle?.slice(0, 10) ?? "n/a"}</td>
                  <td>{i.last_candle ? `${i.last_candle.slice(0, 10)} ${fmtUtc(i.last_candle)}` : "n/a"}</td>
                  <td>{i.last_ok_seconds_ago == null ? "never" : `${fmtAge(i.last_ok_seconds_ago)} ago`}</td>
                  <td>
                    {i.last_attempt_ok == null ? "n/a" : i.last_attempt_ok ? "OK" : <span className="chg-down" title={i.last_error ?? ""}>FAILED</span>}
                    {i.last_attempt && <span className="dim"> · {fmtUtc(i.last_attempt)}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FailuresCard({ d }: { d: DataHealthData }) {
  return (
    <section className="card">
      <header className="section-head">
        <h2>Recent failures</h2>
      </header>
      {d.recent_failures.length === 0 ? (
        <p className="empty">No failed fetches recorded.</p>
      ) : (
        <ul className="list failures">
          {d.recent_failures.map((f, n) => (
            <li key={n}>
              <div><span className="num dim">{fmtUtcDay(f.at)}</span> <strong>{f.pair} {f.timeframe}</strong></div>
              <div className="why break">{f.error}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SettingsCard({ d }: { d: DataHealthData }) {
  return (
    <section className="card">
      <header className="section-head">
        <h2>Settings (read-only)</h2>
      </header>
      <p className="meta">To change these: {d.settings.where_to_change}.</p>
      <div className="settings-grid">
        {d.settings.groups.map((g) => (
          <div key={g.name}>
            <h3 className="settings-group">{g.name}</h3>
            <table className="kv">
              <tbody>
                {g.items.map((it) => (
                  <tr key={it.key}>
                    <th scope="row">{it.key}</th>
                    <td>
                      <span className="num">{it.value}</span>
                      {it.note && <div className="dim small">{it.note}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </section>
  );
}

export function DataHealthView({ d }: { d: DataHealthData }) {
  return (
    <div className="data-health">
      <div className="dh-top">
        <WorkerCard d={d} />
        <StorageCard d={d} />
      </div>
      <ItemsTable d={d} />
      <div className="dh-top">
        <FailuresCard d={d} />
        <SettingsCard d={d} />
      </div>
    </div>
  );
}

export function DataHealth() {
  const { data, isError, error, dataUpdatedAt, isPending } = useDataHealth();
  const now = useNow(5000);
  if (isPending) return <p className="empty">Loading…</p>;
  if (isError) {
    return (
      <div className="problem" role="alert">
        <h2>No data shown</h2>
        <p>{error instanceof Error ? error.message : "The local API could not be reached."}</p>
      </div>
    );
  }
  if (now - dataUpdatedAt > PAGE_DATA_MAX_AGE_MS) return <p className="empty">Refreshing…</p>;
  return <DataHealthView d={data} />;
}
