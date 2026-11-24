// Formatting helpers. The API sends full-precision numbers; rounding happens only here.

export function priceDigits(pair: string): number {
  return pair.endsWith("JPY") ? 3 : 5; // JPY pairs are quoted to 3 decimals, others to 5
}

export function fmtPrice(value: number | null | undefined, pair: string): string {
  return value == null ? "n/a" : value.toFixed(priceDigits(pair));
}

export function fmtSigned(value: number | null | undefined, digits = 1): string {
  if (value == null) return "n/a";
  const v = Number(value.toFixed(digits));
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`; // real minus sign
}

export function fmtPips(value: number | null | undefined): string {
  return value == null ? "n/a" : `${fmtSigned(value)} pips`;
}

/** "45s", "12m", "3h 20m", "2d 1h". Never negative. */
export function fmtAge(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** "14:05 UTC" from an ISO time. */
export function fmtUtc(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return "n/a";
  const d = new Date(iso);
  const t = d.toISOString().slice(11, withSeconds ? 19 : 16);
  return `${t} UTC`;
}

/** "Mon 23 Nov 14:05 UTC". */
export function fmtUtcDay(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  return `${day} ${fmtUtc(iso)}`;
}

/** Local time of this computer, e.g. "14:05". */
export function fmtLocal(date: Date): string {
  return date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

/** The Forex trading day a daily candle belongs to (17:00 New York -> 17:00 New York), as YYYY-MM-DD.
 *  Friday's daily candle opens on Thursday evening, so its open time alone looks like the wrong day. */
export function tradingDay(unixSeconds: number): string {
  const shifted = new Date((unixSeconds + 7 * 3600) * 1000); // +7 h makes 17:00 New York midnight
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(shifted);
  return parts; // en-CA formats as YYYY-MM-DD
}

/** How to name a candle on screen: open time for intraday, trading day for D1. */
export function candleLabel(unixSeconds: number, timeframe: string): string {
  if (timeframe === "D1") return `${tradingDay(unixSeconds)} (daily)`;
  return new Date(unixSeconds * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function secondsBetween(laterMs: number, iso: string): number {
  return (laterMs - new Date(iso).getTime()) / 1000;
}
