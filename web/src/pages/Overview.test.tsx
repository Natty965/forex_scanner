import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render as rtlRender, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { overview, pair } from "../test/fixtures";
import { Overview, OverviewView } from "./Overview";

const render = (ui: React.ReactNode) => rtlRender(<MemoryRouter>{ui}</MemoryRouter>);

function withQuery(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("Overview content", () => {
  it("shows every pair with correctly rounded prices and its status", () => {
    render(<OverviewView data={overview()} />);
    const eur = screen.getByRole("article", { name: "EUR/USD" });
    expect(within(eur).getByText("1.09842")).toBeInTheDocument(); // 5 decimals
    expect(within(eur).getByText("LIVE")).toBeInTheDocument();
    const jpy = screen.getByRole("article", { name: "USD/JPY" });
    expect(within(jpy).getByText("158.918")).toBeInTheDocument(); // JPY: 3 decimals
  });

  it("shows changes with sign, and marks a gap with an asterisk and footnote", () => {
    render(<OverviewView data={overview()} />);
    const eur = screen.getByRole("article", { name: "EUR/USD" });
    expect(within(eur).getByText("+10.0 pips (+0.09%)")).toBeInTheDocument();
    expect(within(eur).getByText("−20.4 pips (−0.18%)")).toBeInTheDocument();
    expect(screen.getByText(/No price at that exact time/)).toBeInTheDocument();
  });

  it("shows the trend matrix, conditions with reasons, and events", () => {
    render(<OverviewView data={overview()} />);
    expect(screen.getAllByText("EMAs stacked down").length).toBe(9);
    expect(screen.getByText("RSI 29.3: LOW zone (30 or less)")).toBeInTheDocument();
    expect(screen.getByText("This is not a signal.")).toBeInTheDocument();
    expect(screen.getByText("Trend DOWN -> SIDEWAYS")).toBeInTheDocument();
  });

  it("says there are no warnings without suggesting to trade", () => {
    render(<OverviewView data={overview()} />);
    expect(screen.getByText("No warnings right now. This is not a signal to trade.")).toBeInTheDocument();
  });

  it("lists trading conditions with their level and reason", () => {
    const data = overview({
      trading_conditions: [
        { kind: "weekend_close", level: "AVOID", pair: null, text: "Avoid new trades: the market closes for the weekend in 30 min", why: "Weekend gaps.", data: null },
      ],
    });
    render(<OverviewView data={data} />);
    const card = screen.getByRole("region", { name: "Trading conditions" });
    expect(within(card).getByText("AVOID:")).toBeInTheDocument();
    expect(within(card).getByText(/closes for the weekend in 30 min/)).toBeInTheDocument();
    expect(within(card).getByText("Weekend gaps.")).toBeInTheDocument();
  });

  it("dims stale prices and labels them STALE", () => {
    const data = overview({ pairs: [pair("EUR/USD", 1.1, { status: "STALE", age_seconds: 2700 })] });
    render(<OverviewView data={data} />);
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.getByText("1.10000").closest(".price")).toHaveClass("is-stale");
    expect(screen.getByText(/45m old/)).toBeInTheDocument();
  });

  it("says plainly when no price has been saved yet", () => {
    render(<OverviewView data={overview({ pairs: [pair("EUR/USD", 1.1, null)] })} />);
    expect(screen.getByText(/No price saved yet/)).toBeInTheDocument();
  });
});

describe("Overview data loading", () => {
  it("shows the data from the API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(overview()), { status: 200 })));
    withQuery(<Overview />);
    expect(await screen.findByText("1.09842")).toBeInTheDocument();
  });

  it("shows NO prices when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    withQuery(<Overview />);
    // the page retries once (about 1 s later) before giving up, so allow a few seconds
    expect(await screen.findByText("No data shown", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText(/not reachable/)).toBeInTheDocument();
    expect(screen.queryByText("1.09842")).not.toBeInTheDocument();
  });
});
