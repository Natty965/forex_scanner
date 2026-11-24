import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chartData } from "../test/fixtures";

// The real chart draws on a canvas, which the test environment doesn't have. Replace the
// library with a recorder and check what the page asks it to draw.
const calls = vi.hoisted(() => ({
  series: [] as { type: string; options: Record<string, unknown>; pane: number; data: unknown[]; priceLines: unknown[] }[],
  chartOptions: {} as Record<string, unknown>,
  onCrosshair: undefined as undefined | ((p: { time?: number }) => void),
}));

vi.mock("lightweight-charts", () => {
  const makeSeries = (type: string, options: Record<string, unknown>, pane = 0) => {
    const rec = { type, options, pane, data: [] as unknown[], priceLines: [] as unknown[] };
    calls.series.push(rec);
    return {
      setData: (d: unknown[]) => { rec.data = d; },
      createPriceLine: (o: unknown) => { rec.priceLines.push(o); },
    };
  };
  return {
    CandlestickSeries: "Candlestick",
    LineSeries: "Line",
    LineStyle: { Dashed: 2 },
    createChart: (_el: unknown, options: Record<string, unknown>) => {
      calls.chartOptions = options;
      return {
        addSeries: (def: string, opts: Record<string, unknown>, pane?: number) => makeSeries(def, opts, pane),
        panes: () => [{ setStretchFactor: () => {} }, { setStretchFactor: () => {} }, { setStretchFactor: () => {} }],
        subscribeCrosshairMove: (fn: (p: { time?: number }) => void) => { calls.onCrosshair = fn; },
        unsubscribeCrosshairMove: () => {},
        timeScale: () => ({ setVisibleLogicalRange: () => {} }),
        remove: () => {},
      };
    },
  };
});

import { PriceChart } from "./PriceChart";

beforeEach(() => {
  calls.series = [];
});

describe("PriceChart", () => {
  it("puts candles and EMAs in the main pane, RSI and ATR in their own panes", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true }} />);
    const byPane = calls.series.map((s) => [s.type, s.pane]);
    expect(byPane).toEqual([["Candlestick", 0], ["Line", 0], ["Line", 0], ["Line", 0], ["Line", 1], ["Line", 2]]);
    const rsi = calls.series[4];
    expect(rsi.priceLines.map((p) => (p as { price: number }).price)).toEqual([70, 30]);
  });

  it("keeps the TradingView attribution logo on (licence requirement)", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true }} />);
    expect((calls.chartOptions.layout as { attributionLogo: boolean }).attributionLogo).toBe(true);
  });

  it("draws the forming candle faded and leaves un-warmed lines empty", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true }} />);
    const bars = calls.series[0].data as { color?: string }[];
    expect(bars).toHaveLength(300);
    expect(bars[298].color).toBeUndefined();                // closed: normal colours
    expect(bars[299].color).toMatch(/rgba\(.*0\.35\)/);     // forming: faded
    expect(calls.series[3].data).toEqual([]);               // EMA 200 not warmed up: nothing drawn
  });

  it("moves ATR up a pane when RSI is hidden, and draws no EMAs when they're off", () => {
    render(<PriceChart data={chartData()} show={{ ema: false, rsi: false, atr: true }} />);
    expect(calls.series.map((s) => [s.type, s.pane])).toEqual([["Candlestick", 0], ["Line", 1]]);
  });

  it("hovering an older candle shows that candle's prices and indicator values", () => {
    const data = chartData();
    render(<PriceChart data={data} show={{ ema: true, rsi: true, atr: true }} />);
    const old = data.candles[100];
    act(() => calls.onCrosshair!({ time: old.time }));
    expect(screen.getByText(new Date(old.time * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC")).toBeInTheDocument();
    expect(screen.getByText(`O ${old.open.toFixed(5)} H ${old.high.toFixed(5)} L ${old.low.toFixed(5)} C ${old.close.toFixed(5)}`)).toBeInTheDocument();
    const ema20 = data.lines.ema20.find((p) => p.time === old.time)!.value;
    expect(screen.getByText(`EMA20 ${ema20.toFixed(5)}`)).toBeInTheDocument();
    expect(screen.getByText("RSI 61.2")).toBeInTheDocument();
    expect(screen.getByText("ATR 10.0 pips")).toBeInTheDocument();
    expect(screen.queryByText("forming")).not.toBeInTheDocument();
    act(() => calls.onCrosshair!({}));                              // mouse leaves: back to the newest candle
    expect(screen.getByText("forming")).toBeInTheDocument();
  });

  it("legend shows the newest candle; a forming candle gets no indicator values", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true }} />);
    expect(screen.getByText("forming")).toBeInTheDocument();
    expect(screen.getByText(/RSI n\/a/)).toBeInTheDocument();
    expect(screen.getByText(/2026-11-22 11:00 UTC/)).toBeInTheDocument();
  });
});
