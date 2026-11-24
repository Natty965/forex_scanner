import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chartData } from "../test/fixtures";

// The real chart draws on a canvas, which the test environment doesn't have. Replace the
// library with a recorder and check what the page asks it to draw.
const calls = vi.hoisted(() => ({
  series: [] as { type: string; options: Record<string, unknown>; pane: number; data: unknown[]; priceLines: unknown[] }[],
  chartOptions: {} as Record<string, unknown>,
  onCrosshair: undefined as undefined | ((p: { time?: number }) => void),
  markers: [] as { time: number; position: string; text: string }[],
}));

vi.mock("lightweight-charts", () => {
  const makeSeries = (type: string, options: Record<string, unknown>, pane = 0) => {
    const rec = { type, options, pane, data: [] as unknown[], priceLines: [] as unknown[] };
    calls.series.push(rec);
    return {
      setData: (d: unknown[]) => { rec.data = d; },
      createPriceLine: (o: unknown) => { rec.priceLines.push(o); return o; },
      removePriceLine: (o: unknown) => { rec.priceLines = rec.priceLines.filter((x) => x !== o); },
    };
  };
  return {
    CandlestickSeries: "Candlestick",
    LineSeries: "Line",
    LineStyle: { Dashed: 2 },
    createSeriesMarkers: () => ({ setMarkers: (m: typeof calls.markers) => { calls.markers = m; } }),
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
  calls.markers = [];
});

const t0 = Date.UTC(2026, 10, 10, 0) / 1000;
const level = (side: "SUPPORT" | "RESISTANCE" | "AT PRICE", price: number, touches: number) => ({
  side, price, low: price, high: price, touches, first_time: "", last_time: "", distance_pips: 10, distance_atr: 1,
});
const withStructure = () =>
  chartData({
    structure: {
      structure: "RISING", reasons: [],
      swings: [{ time: t0 + 10 * 3600, kind: "HIGH", price: 1.102, label: "HH" }, { time: t0 + 20 * 3600, kind: "LOW", price: 1.1, label: "HL" }],
      levels: [level("RESISTANCE", 1.14, 7), level("SUPPORT", 1.12, 2)],
    },
  });

describe("PriceChart", () => {
  it("puts candles and EMAs in the main pane, RSI and ATR in their own panes", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true, levels: false }} />);
    const byPane = calls.series.map((s) => [s.type, s.pane]);
    expect(byPane).toEqual([["Candlestick", 0], ["Line", 0], ["Line", 0], ["Line", 0], ["Line", 1], ["Line", 2]]);
    const rsi = calls.series[4];
    expect(rsi.priceLines.map((p) => (p as { price: number }).price)).toEqual([70, 30]);
  });

  it("keeps the TradingView attribution logo on (licence requirement)", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true, levels: false }} />);
    expect((calls.chartOptions.layout as { attributionLogo: boolean }).attributionLogo).toBe(true);
  });

  it("draws the forming candle faded and leaves un-warmed lines empty", () => {
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true, levels: false }} />);
    const bars = calls.series[0].data as { color?: string }[];
    expect(bars).toHaveLength(300);
    expect(bars[298].color).toBeUndefined();                // closed: normal colours
    expect(bars[299].color).toMatch(/rgba\(.*0\.35\)/);     // forming: faded
    expect(calls.series[3].data).toEqual([]);               // EMA 200 not warmed up: nothing drawn
  });

  it("moves ATR up a pane when RSI is hidden, and draws no EMAs when they're off", () => {
    render(<PriceChart data={chartData()} show={{ ema: false, rsi: false, atr: true, levels: false }} />);
    expect(calls.series.map((s) => [s.type, s.pane])).toEqual([["Candlestick", 0], ["Line", 1]]);
  });

  it("hovering an older candle shows that candle's prices and indicator values", () => {
    const data = chartData();
    render(<PriceChart data={data} show={{ ema: true, rsi: true, atr: true, levels: false }} />);
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
    render(<PriceChart data={chartData()} show={{ ema: true, rsi: true, atr: true, levels: false }} />);
    expect(screen.getByText("forming")).toBeInTheDocument();
    expect(screen.getByText(/RSI n\/a/)).toBeInTheDocument();
    expect(screen.getByText(/2026-11-22 11:00 UTC/)).toBeInTheDocument();
  });

  it("draws levels as labelled dashed lines and swings as markers when LEVELS is on", () => {
    render(<PriceChart data={withStructure()} show={{ ema: false, rsi: false, atr: false, levels: true }} />);
    const lines = calls.series[0].priceLines as { price: number; title: string; lineStyle: number }[];
    expect(lines.map((l) => [l.price, l.title])).toEqual([[1.14, "R 7x"], [1.12, "S 2x"]]);
    expect(calls.markers.map((m) => [m.position, m.text])).toEqual([["aboveBar", "HH"], ["belowBar", "HL"]]);
  });

  it("draws no levels or swings when LEVELS is off", () => {
    render(<PriceChart data={withStructure()} show={{ ema: false, rsi: false, atr: false, levels: false }} />);
    expect(calls.series[0].priceLines).toEqual([]);
    expect(calls.markers).toEqual([]);
  });
});
