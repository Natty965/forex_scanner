// Shapes of the local API's JSON (see src/forex_scanner/readmodel.py). Times are ISO strings in UTC.

export type PriceStatus = "LIVE" | "DELAYED" | "STALE" | "MARKET CLOSED";
export type DataStatus = "OK" | "STALE" | "MARKET CLOSED";
export type WorkerLabel = "RUNNING" | "STOPPED" | "NOT RESPONDING" | "NEVER STARTED";
export type Direction = "UP" | "DOWN" | "SIDEWAYS" | "UNKNOWN";
export type Alignment = "STACKED UP" | "STACKED DOWN" | "MIXED" | "n/a";
export type RsiZone = "HIGH" | "LOW" | "NEUTRAL" | "n/a";

export interface Worker {
  label: WorkerLabel;
  alive: boolean;
  pid?: number;
  started_at?: string | null;
  last_cycle_at?: string | null;
  next_run_at?: string | null;
  cycles?: number;
  message?: string | null;
}

export interface Market {
  open: boolean;
  next_open: string | null;
}

export interface Change {
  reference_price: number;
  reference_time: string;
  pips: number;
  percent: number;
  spans_gap: boolean;
}

export interface Price {
  pair: string;
  price: number;
  price_time: string;
  age_seconds: number;
  status: PriceStatus;
  change_1h: Change | null;
  change_24h: Change | null;
  computed_at: string;
}

interface AnalysisCommon {
  timeframe: string;
  status: DataStatus;
  as_of: string | null;
  as_of_label: string;
  computed_at: string;
  computed_seconds_ago: number;
}

export interface Trend extends AnalysisCommon {
  direction: Direction;
  move_pips: number | null;
  move_ranges: number | null;
  distance_pips: number | null;
  candles_used: number;
  reasons?: string[];
}

export interface Indicators extends AnalysisCommon {
  close: number | null;
  emas: Record<string, number | null>;
  distance_pips: Record<string, number | null>;
  alignment: Alignment;
  rsi: number | null;
  rsi_zone: RsiZone;
  atr: number | null;
  atr_pips: number | null;
  closed_candles: number;
  reasons?: string[];
}

export type StructureLabel = "RISING" | "FALLING" | "MIXED" | "n/a";

export interface SwingPoint {
  time: string;
  kind: "HIGH" | "LOW";
  price: number;
  label: string | null; // HH / LH / EH, HL / LL / EL
}

export interface Level {
  side: "SUPPORT" | "RESISTANCE" | "AT PRICE";
  price: number;
  low: number;
  high: number;
  touches: number;
  first_time: string;
  last_time: string;
  distance_pips: number;
  distance_atr: number;
}

export interface Structure extends AnalysisCommon {
  close: number | null;
  atr_pips: number | null;
  structure: StructureLabel;
  swings: SwingPoint[];
  last_high: SwingPoint | null;
  last_low: SwingPoint | null;
  resistance: Level[];
  support: Level[];
  at_price: Level[];
  candles_used: number;
  reasons: string[];
}

export interface ChartStructure {
  structure: StructureLabel;
  swings: { time: number; kind: "HIGH" | "LOW"; price: number; label: string | null }[];
  levels: Level[];
  reasons: string[];
}

export interface PairOverview {
  pair: string;
  id: string;
  price: Price | null;
  timeframes: Record<string, { trend: Trend | null; indicators: Indicators | null }>;
}

export interface Condition {
  pair: string | null;
  timeframe: string | null;
  kind: "rsi_zone" | "data";
  level: "notice" | "warning";
  text: string;
  why: string;
}

export interface TradingCondition {
  kind: "weekend_close" | "weekly_open" | "rollover" | "high_volatility" | "stale_data" | "market_closed";
  level: "AVOID" | "CAUTION" | "INFO";
  pair: string | null;
  text: string;
  why: string;
  data: Record<string, unknown> | null;
}

export interface MarketEvent {
  at: string;
  pair: string;
  timeframe: string;
  kind: "trend" | "ema_cross" | "rsi_zone";
  message: string;
}

export interface PairAnalysis {
  server_time: string;
  pair: string;
  id: string;
  timeframe: string;
  price: Price | null;
  trend: Trend | null;
  indicators: Indicators | null;
  structure: Structure | null;
}

export interface ChartCandle {
  time: number; // Unix seconds, UTC
  open: number;
  high: number;
  low: number;
  close: number;
  complete: boolean;
}

export interface LinePoint {
  time: number;
  value: number;
}

export interface ChartData {
  pair: string;
  id: string;
  timeframe: string;
  source: string;
  status: DataStatus | null;
  stored_candles: number;
  closed_candles: number;
  count: number;
  candles: ChartCandle[];
  lines: Record<string, LinePoint[]>; // ema20, ema50, ema200, rsi14, atr14 (ATR in pips)
  warmup: Record<string, { needed: number; available: number; ok: boolean }>;
  structure: ChartStructure | null;
}

export type ItemHealth = "OK" | "FAILING" | "NOT UPDATING" | "NOT MONITORED";

export interface DataHealth {
  server_time: string;
  source: string;
  worker: Worker;
  database: { path: string; size_bytes: number; schema_version: number };
  fetches_24h: { ok: number; failed: number };
  items: {
    pair: string;
    timeframe: string;
    candles: number;
    first_candle: string | null;
    last_candle: string | null;
    last_ok_fetch: string | null;
    last_ok_seconds_ago: number | null;
    last_attempt: string | null;
    last_attempt_ok: boolean | null;
    last_error: string | null;
    health: ItemHealth;
    monitored: boolean;
  }[];
  recent_failures: { at: string; pair: string; timeframe: string; error: string }[];
  settings: {
    where_to_change: string;
    groups: { name: string; items: { key: string; value: string; note?: string }[] }[];
  };
}

export type Delivery = "dry_run" | "held_quiet" | "paused" | "suppressed" | "pending" | "sent" | "failed" | "expired";

export interface TelegramStatus {
  configured: boolean;
  token_set: boolean;
  chat_set: boolean;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  backoff_until: string | null;
}

export interface AlertItem {
  id: number;
  created_at: string;
  type: "unusual_move" | "market_events" | "data" | "caution" | "brief" | "test";
  severity: "info" | "notice" | "warning";
  pair: string | null;
  timeframe: string | null;
  candle_time: string | null;
  title: string;
  body: string;
  data: Record<string, unknown>;
  delivery: Delivery;
  delivery_note: string | null;
  sent_at: string | null;
  attempts: number;
}

export interface AlertsResponse {
  server_time: string;
  mode: "dry_run" | "paused" | "sending";
  telegram_connected: boolean;
  telegram: TelegramStatus;
  timezone: string;
  alerts: AlertItem[];
  delivery_meaning: Record<Delivery, string>;
  briefs: { day: string; status: string; created_at: string; alert_id: number | null }[];
}

export interface AlertSettings {
  timezone: string;
  brief_time: string;
  quiet_start: string;
  quiet_end: string;
  dry_run: boolean;
  paused: boolean;
  unusual_enabled: boolean;
  unusual_timeframes: string[];
  unusual_range_atr: number;
  live_move_enabled: boolean;
  live_move_atr: number;
  unusual_cooldown_minutes: number;
  skip_rollover_candle: boolean;
  events_enabled: boolean;
  events_timeframes: string[];
  rsi_high: number;
  rsi_high_exit: number;
  rsi_low: number;
  rsi_low_exit: number;
  data_enabled: boolean;
  data_fail_cycles: number;
  caution_enabled: boolean;
  caution_kinds: string[];
  volatility_threshold: number;
  daily_caps: Record<string, number>;
}

export interface AlertSettingsResponse {
  settings: AlertSettings;
  defaults: AlertSettings;
  timeframes: string[];
}

export interface Overview {
  server_time: string;
  source: string;
  worker: Worker;
  market: Market;
  pairs: PairOverview[];
  conditions: Condition[];
  trading_conditions: TradingCondition[];
  events: MarketEvent[];
}
