import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DataHealth } from "../api/types";
import { DataHealthView } from "./DataHealth";
import { PAGES, Placeholder } from "./Placeholder";

function health(over: Partial<DataHealth> = {}): DataHealth {
  const item = (pair: string, tf: string, extra = {}) => ({
    pair, timeframe: tf, candles: 1200, first_candle: "2026-09-11T01:00:00+00:00", last_candle: "2026-11-23T02:00:00+00:00",
    last_ok_fetch: "2026-11-23T02:52:25+00:00", last_ok_seconds_ago: 90, last_attempt: "2026-11-23T02:52:25+00:00",
    last_attempt_ok: true, last_error: null, health: "OK" as const, monitored: true, ...extra,
  });
  return {
    server_time: "2026-11-23T02:54:00+00:00", source: "yfinance",
    worker: { label: "RUNNING", alive: true, pid: 4242, started_at: "2026-11-23T02:52:00+00:00",
      last_cycle_at: "2026-11-23T02:52:25+00:00", next_run_at: "2026-11-23T02:57:25+00:00", cycles: 1, message: "12 ok, 0 failed" },
    database: { path: "C:\\forex\\data\\forex.db", size_bytes: 3_400_000, schema_version: 1 },
    fetches_24h: { ok: 150, failed: 2 },
    items: [
      item("EUR/USD", "H1"),
      item("GBP/USD", "H1", { health: "FAILING", last_attempt_ok: false, last_error: "Yahoo timed out" }),
      item("EUR/USD", "M5", { health: "NOT MONITORED", monitored: false }),
    ],
    recent_failures: [{ at: "2026-11-23T02:40:00+00:00", pair: "GBP/USD", timeframe: "H1", error: "Yahoo timed out" }],
    settings: { where_to_change: "src/forex_scanner/config.py", groups: [{ name: "Worker", items: [{ key: "Refresh while market open", value: "every 5 min" }] }] },
    ...over,
  };
}

describe("Data health", () => {
  it("shows the worker, database, every stored item and its health", () => {
    render(<DataHealthView d={health()} />);
    expect(screen.getByText("Worker running")).toBeInTheDocument();
    expect(screen.getByText("3.2 MB")).toBeInTheDocument();
    expect(screen.getByText("FAILING")).toBeInTheDocument();
    expect(screen.getByText("NOT MONITORED")).toBeInTheDocument();
    expect(screen.getByText("2 failed")).toBeInTheDocument();
  });

  it("lists recent failures with the exact error, and the settings read-only", () => {
    render(<DataHealthView d={health()} />);
    const failures = screen.getByRole("heading", { name: "Recent failures" }).closest("section")!;
    expect(within(failures).getByText("Yahoo timed out")).toBeInTheDocument();
    expect(screen.getByText("Settings (read-only)")).toBeInTheDocument();
    expect(screen.getByText("every 5 min")).toBeInTheDocument();
  });

  it("explains a worker that never started", () => {
    render(<DataHealthView d={health({ worker: { label: "NEVER STARTED", alive: false } })} />);
    expect(screen.getByText(/never run on this database/)).toBeInTheDocument();
  });
});

describe("Locked pages", () => {
  it("say what must happen first before signals appear", () => {
    render(<Placeholder {...PAGES.signals} locked />);
    expect(screen.getByText(/nothing on this dashboard is a signal/)).toBeInTheDocument();
    expect(screen.getByText("A backtest on past data, including trading costs (spread)")).toBeInTheDocument();
    expect(screen.getByText("Your review and approval of the results")).toBeInTheDocument();
  });
});
