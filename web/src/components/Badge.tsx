import type { ReactNode } from "react";

import type { Alignment, DataStatus, Direction, PriceStatus, WorkerLabel } from "../api/types";

type Tone = "live" | "delayed" | "stale" | "closed" | "muted" | "up" | "down" | "notice";

export function Badge({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

const PRICE_TONE: Record<PriceStatus, Tone> = {
  LIVE: "live",
  DELAYED: "delayed",
  STALE: "stale",
  "MARKET CLOSED": "closed",
};

export function PriceStatusBadge({ status }: { status: PriceStatus }) {
  return <Badge tone={PRICE_TONE[status]}>{status}</Badge>;
}

const DATA_TONE: Record<DataStatus, Tone> = { OK: "live", STALE: "stale", "MARKET CLOSED": "closed" };

export function DataStatusBadge({ status }: { status: DataStatus }) {
  return <Badge tone={DATA_TONE[status]}>{status === "OK" ? "CURRENT" : status}</Badge>;
}

const WORKER_TONE: Record<WorkerLabel, Tone> = {
  RUNNING: "live",
  STOPPED: "delayed",
  "NOT RESPONDING": "stale",
  "NEVER STARTED": "muted",
};

export function WorkerBadge({ label }: { label: WorkerLabel }) {
  return <Badge tone={WORKER_TONE[label]}>Worker {label.toLowerCase()}</Badge>;
}

/** Trend direction: arrow + word, so it never relies on colour alone. */
export function DirectionTag({ direction }: { direction: Direction }) {
  const map: Record<Direction, [string, string]> = {
    UP: ["▲", "dir-up"],
    DOWN: ["▼", "dir-down"],
    SIDEWAYS: ["▬", "dir-flat"],
    UNKNOWN: ["?", "dir-flat"],
  };
  const [arrow, cls] = map[direction];
  return (
    <span className={`dir ${cls}`}>
      <span aria-hidden="true">{arrow}</span> {direction}
    </span>
  );
}

export function alignmentText(a: Alignment): string {
  return { "STACKED UP": "EMAs stacked up", "STACKED DOWN": "EMAs stacked down", MIXED: "EMAs mixed", "n/a": "EMAs n/a" }[a];
}
