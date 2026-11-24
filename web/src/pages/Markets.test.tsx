import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LearnDrawer } from "../components/LearnDrawer";
import { chartData, indicators, overview, pairAnalysis } from "../test/fixtures";
import { AnalysisPanel, Markets } from "./Markets";

// No canvas in tests: replace the chart with a marker.
vi.mock("../components/PriceChart", () => ({
  EMA_COLORS: { ema20: "#fff", ema50: "#fff", ema200: "#fff" },
  PriceChart: () => <div data-testid="price-chart" />,
}));

afterEach(() => vi.unstubAllGlobals());

function renderMarkets(responses: Record<string, unknown> | "down") {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (responses === "down") throw new TypeError("Failed to fetch");
    const key = Object.keys(responses).find((k) => url.startsWith(k));
    return new Response(JSON.stringify(key ? responses[key] : {}), { status: key ? 200 : 404 });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/markets/EURUSD?tf=H1"]}>
        <Routes>
          <Route path="/markets/:pid" element={<Markets />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Analysis panel", () => {
  it("shows the same numbers and reasons as the CLI, rounded for display", () => {
    render(<AnalysisPanel a={pairAnalysis()} />);
    expect(screen.getByText("1.10000")).toBeInTheDocument();             // EMA 20
    expect(screen.getByText("1.10500")).toBeInTheDocument();             // EMA 200
    expect(screen.getByText("45.8")).toBeInTheDocument();                // RSI
    expect(screen.getByText("62.0 pips")).toBeInTheDocument();           // ATR
    expect(screen.getByText("Line fell at least 3 ranges: DOWN.")).toBeInTheDocument();
    expect(screen.getByText(/none of it is a buy or sell signal/)).toBeInTheDocument();
  });

  it("warns when the analysis is not current", () => {
    render(<AnalysisPanel a={pairAnalysis({ indicators: { ...indicators("H1"), status: "STALE" } })} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/not current/);
  });

  it("shows n/a for an EMA that hasn't warmed up", () => {
    const ind = { ...indicators("D1"), emas: { "20": 1.1, "50": 1.1, "200": null }, distance_pips: { "20": 1, "50": 1, "200": null } };
    render(<AnalysisPanel a={pairAnalysis({ timeframe: "D1", indicators: ind })} />);
    expect(screen.getAllByText("n/a").length).toBeGreaterThanOrEqual(2);
  });
});

describe("Markets page", () => {
  it("shows chart, candle table and analysis from the API", async () => {
    renderMarkets({ "/api/overview": overview(), "/api/pairs/EURUSD/chart": chartData(), "/api/pairs/EURUSD/analysis": pairAnalysis(), "/api/events": { count: 0, events: [] } });
    expect(await screen.findByTestId("price-chart")).toBeInTheDocument();
    expect(screen.getByText(/EMA200 needs 1004 closed candles, 520 available/)).toBeInTheDocument();
    expect(screen.getAllByText("forming").length).toBe(1);                // newest table row
    expect(await screen.findByText("Line fell at least 3 ranges: DOWN.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "tradingview.com" })).toHaveAttribute("href", "https://www.tradingview.com/");
  });

  it("shows no chart and no numbers when the API is down", async () => {
    renderMarkets("down");
    expect(await screen.findByText("No chart shown", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByTestId("price-chart")).not.toBeInTheDocument();
    expect(screen.getByText(/Analysis not available/)).toBeInTheDocument();
  });
});

describe("Learn drawer", () => {
  it("explains terms, credits the chart library, and closes with Escape", () => {
    const onClose = vi.fn();
    render(<LearnDrawer open onClose={onClose} />);
    expect(screen.getByRole("dialog", { name: "Learn" })).toBeInTheDocument();
    expect(screen.getByText("Forming candle")).toBeInTheDocument();
    expect(screen.getByText(/Copyright TradingView, Inc\./)).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
