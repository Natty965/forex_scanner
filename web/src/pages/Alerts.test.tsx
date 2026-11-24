import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AlertSettings, AlertsResponse } from "../api/types";
import { AlertHistory, Alerts, ModeBadge, Preferences } from "./Alerts";

const DEFAULTS: AlertSettings = {
  timezone: "Europe/London", brief_time: "07:00", quiet_start: "22:00", quiet_end: "07:00", dry_run: true, paused: false,
  unusual_enabled: true, unusual_timeframes: ["M15", "H1"], unusual_range_atr: 2.5, live_move_enabled: true,
  live_move_atr: 2, unusual_cooldown_minutes: 60, skip_rollover_candle: false, events_enabled: true, events_timeframes: ["H1", "D1"],
  rsi_high: 70, rsi_high_exit: 65, rsi_low: 30, rsi_low_exit: 35, data_enabled: true, data_fail_cycles: 3,
  caution_enabled: true, caution_kinds: ["weekend_close", "high_volatility"], volatility_threshold: 1.8,
  daily_caps: { unusual_move: 10, market_events: 12, data: 6, brief: 2 },
};

const ALERTS: AlertsResponse = {
  server_time: "2026-11-23T08:40:00+00:00", mode: "dry_run", telegram_connected: false, timezone: "Europe/London",
  telegram: { configured: false, token_set: false, chat_set: false, last_ok_at: null, last_error: null,
    last_error_at: null, backoff_until: null },
  alerts: [{
    id: 1, created_at: "2026-11-23T08:35:52+00:00", type: "brief", severity: "info", pair: null, timeframe: null,
    candle_time: null, title: "DAILY BRIEF · Mon 23 Nov 2026 (late)", body: "EUR/USD  1.09975  LIVE\nnot signals",
    data: {}, delivery: "dry_run", delivery_note: "dry run: recorded, not sent", sent_at: null, attempts: 0,
  }],
  delivery_meaning: { dry_run: "Recorded only: dry run is on, nothing is sent.", held_quiet: "", paused: "",
    suppressed: "", pending: "", sent: "", failed: "", expired: "" },
  briefs: [{ day: "2026-11-23", status: "created late", created_at: "2026-11-23T08:35:52+00:00", alert_id: 1 }],
};

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("Alert history", () => {
  it("lists alerts in the chosen timezone and expands one to show the full message", () => {
    render(<AlertHistory data={ALERTS} />);
    expect(screen.getByText("Mon 23 Nov, 08:35")).toBeInTheDocument();
    expect(screen.getByText("dry run")).toBeInTheDocument();
    expect(screen.queryByText(/not signals/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /DAILY BRIEF/ }));
    expect(screen.getByText(/EUR\/USD\s+1\.09975\s+LIVE/)).toBeInTheDocument();
    expect(screen.getByText(/Recorded only: dry run is on/)).toBeInTheDocument();
  });

  it("says plainly what the sending mode is", () => {
    const { rerender } = render(<ModeBadge mode="dry_run" telegram={false} />);
    expect(screen.getByText("DRY RUN · recorded, not sent")).toBeInTheDocument();
    rerender(<ModeBadge mode="sending" telegram={false} />);
    expect(screen.getByText(/Telegram not connected yet/)).toBeInTheDocument();
  });
});

describe("Preferences", () => {
  it("sends only the changed setting, as JSON", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ settings: { ...DEFAULTS, brief_time: "06:30" }, defaults: DEFAULTS, timeframes: ["M15", "H1", "D1"] })));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = wrap(<Preferences saved={DEFAULTS} defaults={DEFAULTS} timeframes={["M15", "H1", "D1"]} />);
    expect(screen.getByRole("button", { name: "No changes" })).toBeDisabled();
    fireEvent.change(container.querySelector('input[type="time"]')!, { target: { value: "06:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Save 1 change(s)" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/alert-settings");
    expect(init.method).toBe("PUT");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ brief_time: "06:30" });
  });

  it("shows the server's reasons when a change is refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: { message: "Nothing was changed.", problems: ["Unknown timezone 'Mars/Base' (use a name like Europe/London)."] } }),
      { status: 422 })));
    wrap(<Preferences saved={DEFAULTS} defaults={DEFAULTS} timeframes={["M15", "H1", "D1"]} />);
    fireEvent.change(screen.getByDisplayValue("Europe/London"), { target: { value: "Mars/Base" } });
    fireEvent.click(screen.getByRole("button", { name: "Save 1 change(s)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Nothing was changed.*Unknown timezone 'Mars\/Base'/);
  });
});

describe("Alerts page", () => {
  it("pause button sends paused: true", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PUT") return new Response(JSON.stringify({ settings: { ...DEFAULTS, paused: true }, defaults: DEFAULTS, timeframes: [] }));
      if (url.startsWith("/api/alert-settings")) return new Response(JSON.stringify({ settings: DEFAULTS, defaults: DEFAULTS, timeframes: [] }));
      return new Response(JSON.stringify(ALERTS));
    });
    vi.stubGlobal("fetch", fetchMock);
    wrap(<Alerts />);
    const pause = await screen.findByRole("button", { name: /Pause/ });
    await waitFor(() => expect(pause).toBeEnabled());
    fireEvent.click(pause);
    await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "PUT")).toBe(true));
    const put = fetchMock.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PUT")!;
    expect(JSON.parse((put[1] as RequestInit).body as string)).toEqual({ paused: true });
  });
});
