import type { ChartData, Indicators, Overview, PairAnalysis, PairOverview, Price, Trend } from "../api/types";

const T = "2026-11-25T12:00:00+00:00";

export function price(pair: string, value: number, over: Partial<Price> = {}): Price {
  return {
    pair, price: value, price_time: "2026-11-25T11:59:00+00:00", age_seconds: 60, status: "LIVE",
    change_1h: { reference_price: value - 0.001, reference_time: "2026-11-25T10:59:00+00:00", pips: 10, percent: 0.09, spans_gap: false },
    change_24h: { reference_price: value + 0.002, reference_time: "2026-11-22T21:59:00+00:00", pips: -20.4, percent: -0.18, spans_gap: true },
    computed_at: T, ...over,
  };
}

export function trend(tf: string, over: Partial<Trend> = {}): Trend {
  return {
    timeframe: tf, status: "OK", as_of: T, as_of_label: tf === "D1" ? "11-25 day" : "11-25 11:00", computed_at: T,
    computed_seconds_ago: 30, direction: "DOWN", move_pips: -60, move_ranges: -5, distance_pips: -30, candles_used: 50, ...over,
  };
}

export function indicators(tf: string, over: Partial<Indicators> = {}): Indicators {
  return {
    timeframe: tf, status: "OK", as_of: T, as_of_label: tf === "D1" ? "11-25 day" : "11-25 11:00", computed_at: T,
    computed_seconds_ago: 30, close: 1.1, emas: { "20": 1.1, "50": 1.101, "200": 1.105 },
    distance_pips: { "20": 1, "50": -9, "200": -49 }, alignment: "STACKED DOWN", rsi: 45.8, rsi_zone: "NEUTRAL",
    atr: 0.0062, atr_pips: 62, closed_candles: 1200, ...over,
  };
}

export function pair(name: string, value: number, priceOver: Partial<Price> | null = {}): PairOverview {
  return {
    pair: name, id: name.replace("/", ""),
    price: priceOver === null ? null : price(name, value, priceOver),
    timeframes: Object.fromEntries(["M15", "H1", "D1"].map((tf) => [tf, { trend: trend(tf), indicators: indicators(tf) }])),
  };
}

/** 300 hourly candles; the newest is still forming. EMA 200 not warmed up in this fixture. */
export function chartData(over: Partial<ChartData> = {}): ChartData {
  const start = Date.UTC(2026, 10, 10, 0) / 1000;
  const candles = Array.from({ length: 300 }, (_, i) => {
    const c = 1.1 + i * 0.0001;
    return { time: start + i * 3600, open: c, high: c + 0.0005, low: c - 0.0005, close: c + 0.00005, complete: i < 299 };
  });
  const closedTimes = candles.filter((c) => c.complete).map((c) => c.time);
  const line = (from: number, value: (i: number) => number) => closedTimes.slice(from).map((t, i) => ({ time: t, value: value(i) }));
  return {
    pair: "EUR/USD", id: "EURUSD", timeframe: "H1", source: "yfinance", status: "OK",
    stored_candles: 1300, closed_candles: 1299, count: 300, candles,
    lines: { ema20: line(0, (i) => 1.1 + i * 0.0001), ema50: line(0, (i) => 1.099 + i * 0.0001), ema200: [],
      rsi14: line(0, () => 61.24), atr14: line(0, () => 10.04) },
    warmup: {
      ema20: { needed: 104, available: 1299, ok: true }, ema50: { needed: 254, available: 1299, ok: true },
      ema200: { needed: 1004, available: 520, ok: false }, rsi14: { needed: 127, available: 1299, ok: true },
      atr14: { needed: 127, available: 1299, ok: true },
    },
    ...over,
  };
}

export function pairAnalysis(over: Partial<PairAnalysis> = {}): PairAnalysis {
  return {
    server_time: T, pair: "EUR/USD", id: "EURUSD", timeframe: "H1", price: price("EUR/USD", 1.09842),
    trend: { ...trend("H1"), reasons: ["The best-fit line fell 60.0 pips.", "Line fell at least 3 ranges: DOWN."] },
    indicators: { ...indicators("H1"), reasons: ["Price is above EMA 20 (1.10000) by 1.0 pips."] },
    ...over,
  };
}

export function overview(over: Partial<Overview> = {}): Overview {
  return {
    server_time: T, source: "yfinance",
    worker: { label: "RUNNING", alive: true, cycles: 3 },
    market: { open: true, next_open: null },
    pairs: [pair("EUR/USD", 1.0984183549880981), pair("GBP/USD", 1.3107191324234009), pair("USD/JPY", 158.91799926757812)],
    conditions: [{ pair: "EUR/USD", timeframe: "D1", kind: "rsi_zone", level: "notice", text: "RSI 29.3: LOW zone (30 or less)", why: "This is not a signal." }],
    trading_conditions: [],
    events: [{ at: T, pair: "GBP/USD", timeframe: "M15", kind: "trend", message: "Trend DOWN -> SIDEWAYS" }],
    ...over,
  };
}
