import {
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  type MouseEventParams,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ChartCandle, ChartData } from "../api/types";
import { candleLabel, priceDigits } from "../lib/format";

// Chart colours (mirrors tokens.css; the chart library needs plain values).
const C = {
  bg: "#11161f",
  grid: "#1a2130",
  text: "#8d97a8",
  border: "#232b3a",
  up: "#26c281",
  down: "#ef5350",
  upFaded: "rgba(38,194,129,0.35)",
  downFaded: "rgba(239,83,80,0.35)",
  ema20: "#f0c36b",
  ema50: "#6ea8fe",
  ema200: "#c58bff",
  rsi: "#38c7d9",
  atr: "#8d97a8",
  guide: "#3a4458",
};

export const EMA_COLORS: Record<string, string> = { ema20: C.ema20, ema50: C.ema50, ema200: C.ema200 };

export interface ChartToggles {
  ema: boolean;
  rsi: boolean;
  atr: boolean;
}

interface Legend {
  candle: ChartCandle;
  values: Record<string, number | undefined>;
}

function toBar(c: ChartCandle) {
  const base = { time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close };
  if (c.complete) return base;
  const faded = c.close >= c.open ? C.upFaded : C.downFaded; // still forming: drawn faded
  return { ...base, color: faded, wickColor: faded, borderColor: faded };
}

export function PriceChart({ data, show }: { data: ChartData; show: ChartToggles }) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<{ candles?: ISeriesApi<"Candlestick">; lines: Record<string, ISeriesApi<"Line">> }>({
    lines: {},
  });
  const digits = priceDigits(data.pair);

  // Fast lookup for the legend: time -> candle, and time -> value for each line.
  const lookup = useMemo(() => {
    const candles = new Map(data.candles.map((c) => [c.time, c]));
    const lines: Record<string, Map<number, number>> = {};
    for (const [name, pts] of Object.entries(data.lines)) lines[name] = new Map(pts.map((p) => [p.time, p.value]));
    return { candles, lines };
  }, [data]);
  const lookupRef = useRef(lookup);
  lookupRef.current = lookup;

  const latest = data.candles.at(-1);
  const legendAt = (time: number | undefined): Legend | null => {
    const lk = lookupRef.current;
    const candle = time === undefined ? undefined : lk.candles.get(time);
    if (!candle) return null;
    const values: Record<string, number | undefined> = {};
    for (const [name, m] of Object.entries(lk.lines)) values[name] = m.get(time!);
    return { candle, values };
  };
  const [hovered, setHovered] = useState<Legend | null>(null);
  const legend = hovered ?? legendAt(latest?.time);

  // Create the chart (only when the layout changes, so zoom and scroll survive data refreshes).
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: C.bg },
        textColor: C.text,
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        fontSize: 11,
        attributionLogo: true, // required by the TradingView Lightweight Charts licence: keep it
        panes: { separatorColor: C.border, enableResize: true },
      },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.border },
      timeScale: { borderColor: C.border, timeVisible: data.timeframe !== "D1", secondsVisible: false },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    const refs: typeof seriesRef.current = { lines: {} };
    refs.candles = chart.addSeries(CandlestickSeries, {
      upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false,
      priceFormat: { type: "price", precision: digits, minMove: 10 ** -digits },
    });
    const line = (color: string, pane: number, precision: number) =>
      chart.addSeries(
        LineSeries,
        { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false,
          priceFormat: { type: "price", precision, minMove: 10 ** -precision } },
        pane,
      );
    if (show.ema) {
      for (const name of ["ema20", "ema50", "ema200"]) refs.lines[name] = line(EMA_COLORS[name], 0, digits);
    }
    let pane = 1;
    if (show.rsi) {
      const rsi = line(C.rsi, pane, 1);
      for (const level of [70, 30]) {
        rsi.createPriceLine({ price: level, color: C.guide, lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: String(level) });
      }
      refs.lines.rsi14 = rsi;
      pane += 1;
    }
    if (show.atr) {
      refs.lines.atr14 = line(C.atr, pane, 1);
      pane += 1;
    }
    const panes = chart.panes();
    panes[0]?.setStretchFactor(3);
    for (const p of panes.slice(1)) p.setStretchFactor(1);
    seriesRef.current = refs;

    const onMove = (param: MouseEventParams) => setHovered(param.time === undefined ? null : legendAt(param.time as number));
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
      chartRef.current = null;
    };
  }, [show.ema, show.rsi, show.atr, digits, data.timeframe, data.pair]);

  // Put data in (on first load and every refresh).
  const firstLoad = useRef(true);
  useEffect(() => {
    const { candles, lines } = seriesRef.current;
    candles?.setData(data.candles.map(toBar));
    for (const [name, s] of Object.entries(lines)) {
      s.setData((data.lines[name] ?? []).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    }
    if (firstLoad.current && chartRef.current && data.candles.length) {
      const n = data.candles.length;
      chartRef.current.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + 3 });
      firstLoad.current = false;
    }
  }, [data, show.ema, show.rsi, show.atr]);
  useEffect(() => {
    firstLoad.current = true; // new pair/timeframe: show the latest candles again
  }, [data.pair, data.timeframe]);

  return (
    <div className="chart-wrap">
      {legend && <ChartLegend legend={legend} data={data} show={show} />}
      <div ref={box} className="chart-box" data-testid="price-chart" />
    </div>
  );
}

function fmt(v: number | undefined, digits: number): string {
  return v === undefined ? "n/a" : v.toFixed(digits);
}

function ChartLegend({ legend, data, show }: { legend: Legend; data: ChartData; show: ChartToggles }) {
  const { candle: c, values } = legend;
  const d = priceDigits(data.pair);
  return (
    <div className="chart-legend num" aria-live="off">
      <div>
        <span className="lg-time">{candleLabel(c.time, data.timeframe)}</span>
        {!c.complete && <span className="lg-forming">forming</span>}
      </div>
      <div>
        O {c.open.toFixed(d)} H {c.high.toFixed(d)} L {c.low.toFixed(d)} C {c.close.toFixed(d)}
      </div>
      {show.ema && (
        <div>
          {["ema20", "ema50", "ema200"].map((n) => (
            <span key={n} style={{ color: EMA_COLORS[n] }} className="lg-item">
              EMA{n.slice(3)} {c.complete ? fmt(values[n], d) : "n/a"}
            </span>
          ))}
        </div>
      )}
      {(show.rsi || show.atr) && (
        <div>
          {show.rsi && <span className="lg-item" style={{ color: C.rsi }}>RSI {c.complete ? fmt(values.rsi14, 1) : "n/a"}</span>}
          {show.atr && <span className="lg-item">ATR {c.complete ? fmt(values.atr14, 1) : "n/a"} pips</span>}
        </div>
      )}
    </div>
  );
}
