import { Pause, Play } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { ApiRejected, useAlertSettings, useAlerts, useSaveAlertSettings, useSendTestMessage } from "../api/client";
import type { AlertItem, AlertSettings, AlertsResponse, Delivery } from "../api/types";
import { Badge } from "../components/Badge";

const TYPE_LABEL: Record<AlertItem["type"], string> = {
  unusual_move: "Unusual move",
  market_events: "Market events",
  data: "Data / system",
  caution: "Caution (risk warning)",
  brief: "Daily brief",
  test: "Test message",
};

const CAUTION_KINDS: [string, string][] = [
  ["weekend_close", "Weekend close"],
  ["high_volatility", "High volatility"],
  ["weekly_open", "Weekly open"],
  ["rollover", "Daily rollover"],
];

const DELIVERY_TONE: Record<Delivery, "live" | "delayed" | "stale" | "closed" | "muted" | "notice"> = {
  dry_run: "muted",
  held_quiet: "closed",
  paused: "delayed",
  suppressed: "muted",
  pending: "notice",
  sent: "live",
  failed: "stale",
  expired: "muted",
};

export function TelegramLine({ t, timeZone }: { t: AlertsResponse["telegram"]; timeZone: string }) {
  const send = useSendTestMessage();
  if (!t.configured) {
    return (
      <p className="meta tg-line">
        Telegram: not set up ({!t.token_set ? "no bot token" : "no chat id"} in .env). Run{" "}
        <code>python -m forex_scanner telegram-setup</code> for the steps.
      </p>
    );
  }
  return (
    <div className="tg-line">
      <span className="meta">
        Telegram: set up.
        {t.last_ok_at && ` Last sent ${fmtIn(t.last_ok_at, timeZone)}.`}
        {t.last_error && <span className="chg-down"> Last problem: {t.last_error}</span>}
      </span>
      <button type="button" className="btn btn-sm" disabled={send.isPending} onClick={() => send.mutate()}>
        {send.isPending ? "Sending…" : "Send test message"}
      </button>
      {send.data?.ok && <span className="saved" role="status">Test message sent. Check Telegram.</span>}
      {(send.data && !send.data.ok) || send.error ? (
        <span className="chg-down" role="alert">Not sent: {send.data?.error ?? send.error?.message}</span>
      ) : null}
    </div>
  );
}

function fmtIn(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(iso));
}

export function ModeBadge({ mode, telegram }: { mode: AlertsResponse["mode"]; telegram: boolean }) {
  if (mode === "paused") return <Badge tone="delayed">PAUSED · nothing is sent</Badge>;
  if (mode === "dry_run") return <Badge tone="muted">DRY RUN · recorded, not sent</Badge>;
  return telegram ? <Badge tone="live">SENDING</Badge> : <Badge tone="notice">SENDING ON · Telegram not connected yet</Badge>;
}

// ---------- history ----------

export function AlertHistory({ data }: { data: AlertsResponse }) {
  const [open, setOpen] = useState<number | null>(null);
  if (data.alerts.length === 0) {
    return <p className="empty">No alerts match. The worker records them as conditions occur.</p>;
  }
  return (
    <ul className="alert-list">
      {data.alerts.map((a) => (
        <li key={a.id} className={`alert-row sev-${a.severity}`}>
          <button type="button" className="alert-head" aria-expanded={open === a.id} onClick={() => setOpen(open === a.id ? null : a.id)}>
            <span className="num dim">{fmtIn(a.created_at, data.timezone)}</span>
            <span className="alert-type">{TYPE_LABEL[a.type]}</span>
            <span className="alert-title">{a.title}</span>
            <span title={`${data.delivery_meaning[a.delivery]}${a.delivery_note ? ` (${a.delivery_note})` : ""}`}>
              <Badge tone={DELIVERY_TONE[a.delivery]}>{a.delivery.replace("_", " ")}</Badge>
            </span>
          </button>
          {open === a.id && (
            <div className="alert-detail">
              <pre className="alert-body">{a.body}</pre>
              <p className="meta">
                {data.delivery_meaning[a.delivery]}
                {a.delivery_note ? ` (${a.delivery_note})` : ""}
              </p>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---------- preferences ----------

function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>
        <span className="field-label">{label}</span>
        {children}
      </label>
      {help && <p className="field-help">{help}</p>}
    </div>
  );
}

function diff(draft: AlertSettings, saved: AlertSettings): Partial<AlertSettings> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(draft) as (keyof AlertSettings)[]) {
    if (JSON.stringify(draft[k]) !== JSON.stringify(saved[k])) out[k] = draft[k];
  }
  return out as Partial<AlertSettings>;
}

export function Preferences({ saved, defaults, timeframes }: { saved: AlertSettings; defaults: AlertSettings; timeframes: string[] }) {
  const [draft, setDraft] = useState<AlertSettings>(saved);
  const save = useSaveAlertSettings();
  useEffect(() => setDraft(saved), [saved]);
  const changes = diff(draft, saved);
  const changed = Object.keys(changes).length > 0;
  const set = <K extends keyof AlertSettings>(k: K, v: AlertSettings[K]) => setDraft({ ...draft, [k]: v });
  const num = (k: keyof AlertSettings) => (e: React.ChangeEvent<HTMLInputElement>) => set(k, Number(e.target.value) as never);
  const toggleTf = (k: "unusual_timeframes" | "events_timeframes", tf: string) =>
    set(k, draft[k].includes(tf) ? draft[k].filter((x) => x !== tf) : [...draft[k], tf]);
  const toggleKind = (k: string) =>
    set("caution_kinds", draft.caution_kinds.includes(k) ? draft.caution_kinds.filter((x) => x !== k) : [...draft.caution_kinds, k]);
  const problems = save.error instanceof ApiRejected ? save.error.problems : save.error ? [save.error.message] : [];

  return (
    <form className="prefs" onSubmit={(e) => { e.preventDefault(); if (changed) save.mutate(changes); }}>
      <fieldset>
        <legend>Sending</legend>
        <Field label="Dry run" help="Record alerts on this page but send nothing. Recommended until you've reviewed a week of alerts.">
          <input type="checkbox" checked={draft.dry_run} onChange={(e) => set("dry_run", e.target.checked)} />
        </Field>
        <Field label="Paused" help="Emergency stop: nothing is sent while paused (alerts are still recorded).">
          <input type="checkbox" checked={draft.paused} onChange={(e) => set("paused", e.target.checked)} />
        </Field>
      </fieldset>

      <fieldset>
        <legend>Timing</legend>
        <Field label="Timezone" help="Used for the brief time, quiet hours and times in messages. Example: Europe/London.">
          <input type="text" value={draft.timezone} onChange={(e) => set("timezone", e.target.value)} />
        </Field>
        <Field label="Daily brief time" help="Created at or after this time on weekdays.">
          <input type="time" value={draft.brief_time} onChange={(e) => set("brief_time", e.target.value)} />
        </Field>
        <Field label="Quiet hours from" help="Alerts in this window are held and listed in the next morning's brief.">
          <input type="time" value={draft.quiet_start} onChange={(e) => set("quiet_start", e.target.value)} />
        </Field>
        <Field label="Quiet hours until">
          <input type="time" value={draft.quiet_end} onChange={(e) => set("quiet_end", e.target.value)} />
        </Field>
      </fieldset>

      <fieldset>
        <legend>Unusual moves</legend>
        <Field label="Enabled">
          <input type="checkbox" checked={draft.unusual_enabled} onChange={(e) => set("unusual_enabled", e.target.checked)} />
        </Field>
        <Field label="Timeframes" help="M15 reacts fastest but is noisiest.">
          <span className="tf-checks">
            {timeframes.map((tf) => (
              <label key={tf}><input type="checkbox" checked={draft.unusual_timeframes.includes(tf)} onChange={() => toggleTf("unusual_timeframes", tf)} /> {tf}</label>
            ))}
          </span>
        </Field>
        <Field label="Candle range at least (x ATR)" help="2.5 means a candle at least 2.5 times the usual size. Higher = fewer alerts.">
          <input type="number" step="0.1" min="0.5" max="20" value={draft.unusual_range_atr} onChange={num("unusual_range_atr")} />
        </Field>
        <Field label="Fast 60-minute move">
          <input type="checkbox" checked={draft.live_move_enabled} onChange={(e) => set("live_move_enabled", e.target.checked)} />
        </Field>
        <Field label="Fast move at least (x hourly ATR)">
          <input type="number" step="0.1" min="0.5" max="20" value={draft.live_move_atr} onChange={num("live_move_atr")} />
        </Field>
        <Field label="Cooldown (minutes)" help="After an alert, the same pair and rule stay quiet this long.">
          <input type="number" step="5" min="0" max="1440" value={draft.unusual_cooldown_minutes} onChange={num("unusual_cooldown_minutes")} />
        </Field>
        <Field label="Skip the daily rollover candle" help="The candle opening at 17:00 New York (22:00 UTC in winter) often looks unusual only because trading is thin. Tick to ignore it.">
          <input type="checkbox" checked={draft.skip_rollover_candle} onChange={(e) => set("skip_rollover_candle", e.target.checked)} />
        </Field>
      </fieldset>

      <fieldset>
        <legend>Market events</legend>
        <Field label="Enabled" help="Trend changes, EMA 20/50 crossings and RSI zone changes, batched into one alert per cycle.">
          <input type="checkbox" checked={draft.events_enabled} onChange={(e) => set("events_enabled", e.target.checked)} />
        </Field>
        <Field label="Timeframes">
          <span className="tf-checks">
            {timeframes.map((tf) => (
              <label key={tf}><input type="checkbox" checked={draft.events_timeframes.includes(tf)} onChange={() => toggleTf("events_timeframes", tf)} /> {tf}</label>
            ))}
          </span>
        </Field>
        <Field label="RSI high zone: enter at / leave below" help="Two levels stop RSI hovering near 70 from flapping in and out.">
          <span className="pair-inputs">
            <input type="number" step="1" value={draft.rsi_high} onChange={num("rsi_high")} aria-label="RSI high enter" />
            <input type="number" step="1" value={draft.rsi_high_exit} onChange={num("rsi_high_exit")} aria-label="RSI high leave" />
          </span>
        </Field>
        <Field label="RSI low zone: enter at / leave above">
          <span className="pair-inputs">
            <input type="number" step="1" value={draft.rsi_low} onChange={num("rsi_low")} aria-label="RSI low enter" />
            <input type="number" step="1" value={draft.rsi_low_exit} onChange={num("rsi_low_exit")} aria-label="RSI low leave" />
          </span>
        </Field>
      </fieldset>

      <fieldset>
        <legend>Data problems</legend>
        <Field label="Enabled">
          <input type="checkbox" checked={draft.data_enabled} onChange={(e) => set("data_enabled", e.target.checked)} />
        </Field>
        <Field label="Alert after this many failed cycles" help="Each cycle is 5 minutes, so 3 = about 15 minutes of no data.">
          <input type="number" step="1" min="1" max="50" value={draft.data_fail_cycles} onChange={num("data_fail_cycles")} />
        </Field>
      </fieldset>

      <fieldset>
        <legend>Caution warnings</legend>
        <Field label="Enabled" help="Risk warnings such as 'avoid new trades: the weekend close is near'. They never say when to trade.">
          <input type="checkbox" checked={draft.caution_enabled} onChange={(e) => set("caution_enabled", e.target.checked)} />
        </Field>
        <Field label="Send as alerts" help="All of them always show on the Overview and in the brief. The rollover happens every day, so it is off here by default.">
          <span className="tf-checks">
            {CAUTION_KINDS.map(([k, label]) => (
              <label key={k}><input type="checkbox" checked={draft.caution_kinds.includes(k)} onChange={() => toggleKind(k)} /> {label}</label>
            ))}
          </span>
        </Field>
        <Field label="High volatility at (x normal)" help="1.8 means recent hourly candles are 1.8 times their usual size over the last 20 days.">
          <input type="number" step="0.1" min="1.2" max="10" value={draft.volatility_threshold} onChange={num("volatility_threshold")} />
        </Field>
      </fieldset>

      <fieldset>
        <legend>Daily limits</legend>
        {Object.keys(draft.daily_caps).map((t) => (
          <Field key={t} label={TYPE_LABEL[t as AlertItem["type"]] ?? t} help={t === "unusual_move" ? "Alerts over the limit are recorded as 'suppressed'." : undefined}>
            <input type="number" step="1" min="0" max="500" value={draft.daily_caps[t]}
              onChange={(e) => set("daily_caps", { ...draft.daily_caps, [t]: Number(e.target.value) })} />
          </Field>
        ))}
      </fieldset>

      {problems.length > 0 && (
        <div className="warn-box" role="alert">
          <strong>Nothing was changed:</strong>
          <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
        </div>
      )}
      {save.isSuccess && !changed && <p className="saved" role="status">Saved. The worker uses the new settings from its next cycle.</p>}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!changed || save.isPending}>
          {save.isPending ? "Saving…" : changed ? `Save ${Object.keys(changes).length} change(s)` : "No changes"}
        </button>
        <button type="button" className="btn" disabled={!changed} onClick={() => { setDraft(saved); save.reset(); }}>Undo changes</button>
        <button type="button" className="btn" onClick={() => setDraft(defaults)}>Back to defaults</button>
      </div>
    </form>
  );
}

// ---------- page ----------

export function Alerts() {
  const [tab, setTab] = useState<"history" | "prefs" | "briefs">("history");
  const [type, setType] = useState("");
  const [delivery, setDelivery] = useState("");
  const alerts = useAlerts(type, delivery);
  const settings = useAlertSettings();
  const save = useSaveAlertSettings();
  const data = alerts.data;
  const paused = settings.data?.settings.paused ?? false;

  return (
    <div className="alerts-page">
      <header className="alerts-head card">
        <h1>Alerts</h1>
        {data && <ModeBadge mode={data.mode} telegram={data.telegram_connected} />}
        <button type="button" className="btn btn-sm" disabled={!settings.data || save.isPending}
          onClick={() => save.mutate({ paused: !paused })}>
          {paused ? <><Play size={14} aria-hidden="true" /> Resume</> : <><Pause size={14} aria-hidden="true" /> Pause</>}
        </button>
        <p className="meta alerts-note">
          Alerts describe what prices did. They are not signals. Nothing is sent while dry run or pause is on.
        </p>
        {data && <TelegramLine t={data.telegram} timeZone={data.timezone} />}
      </header>

      <div className="tabs" role="tablist" aria-label="Alerts">
        {([["history", "History"], ["prefs", "Preferences"], ["briefs", "Daily briefs"]] as const).map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} className={`tab${tab === k ? " active" : ""}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>

      <section className="card" role="tabpanel">
        {tab === "history" && (
          <>
            <div className="filters">
              <label>Type{" "}
                <select value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="">all</option>
                  {Object.entries(TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </label>
              <label>Delivery{" "}
                <select value={delivery} onChange={(e) => setDelivery(e.target.value)}>
                  <option value="">all</option>
                  {Object.keys(DELIVERY_TONE).map((d) => <option key={d} value={d}>{d.replace("_", " ")}</option>)}
                </select>
              </label>
            </div>
            {alerts.isError ? <p className="empty">API not reachable.</p> : !data ? <p className="empty">Loading…</p> : <AlertHistory data={data} />}
          </>
        )}
        {tab === "prefs" && (settings.isError ? <p className="empty">API not reachable.</p> : !settings.data ? <p className="empty">Loading…</p> : (
          <Preferences saved={settings.data.settings} defaults={settings.data.defaults} timeframes={settings.data.timeframes} />
        ))}
        {tab === "briefs" && (!data ? <p className="empty">Loading…</p> : data.briefs.length === 0 ? (
          <p className="empty">No daily brief yet. The first one is created at the brief time on a weekday.</p>
        ) : (
          <table className="kv">
            <tbody>
              {data.briefs.map((b) => (
                <tr key={b.day}><th scope="row" className="num">{b.day}</th><td>{b.status}</td></tr>
              ))}
            </tbody>
          </table>
        ))}
      </section>
    </div>
  );
}
