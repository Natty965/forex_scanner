import { X } from "lucide-react";
import { useEffect, useRef } from "react";

import { GLOSSARY, type GlossaryKey } from "../lib/glossary";

const SECTIONS: { title: string; items: [string, GlossaryKey][] }[] = [
  {
    title: "Market basics",
    items: [["Candle", "candle"], ["Timeframe", "timeframe"], ["Forming candle", "forming"], ["Pip", "pip"],
      ["Price shown here", "price"], ["Spread", "spread"], ["Trading sessions", "sessions"]],
  },
  {
    title: "What the scanner measures",
    items: [["Trend", "trend"], ["EMA alignment", "ema"], ["RSI", "rsi"], ["ATR", "atr"], ["Warm-up", "warmup"],
      ["Change since 1h / 24h", "change"]],
  },
  {
    title: "Freshness",
    items: [["LIVE", "LIVE"], ["DELAYED", "DELAYED"], ["STALE", "STALE"], ["MARKET CLOSED", "MARKET CLOSED"],
      ["CURRENT (analysis)", "OK"], ["Worker", "worker"]],
  },
  {
    title: "On the Overview",
    items: [["Worth noticing", "conditions"], ["Events", "events"]],
  },
];

/** A side panel with every explanation in one place. Closes with Esc or the X button. */
export function LearnDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Learn">
        <header className="drawer-head">
          <h1>Learn</h1>
          <button ref={closeBtn} type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </header>
        <p className="dim">
          Plain-English explanations of everything on this dashboard. Everything here describes what prices have done.
          Nothing here predicts prices or tells you to buy or sell.
        </p>
        {SECTIONS.map((s) => (
          <section key={s.title} className="drawer-section">
            <h2>{s.title}</h2>
            <dl>
              {s.items.map(([label, key]) => (
                <div key={key} className="term">
                  <dt>{label}</dt>
                  <dd>{GLOSSARY[key]}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        <section className="drawer-section">
          <h2>Credits</h2>
          <p className="dim">
            Prices: Yahoo Finance via the free, unofficial <code>yfinance</code> library (may be delayed or have gaps).
            Charts: TradingView Lightweight Charts™, Copyright TradingView, Inc.,{" "}
            <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer noopener">tradingview.com</a> (Apache 2.0).
            Fonts: Inter and JetBrains Mono (SIL Open Font License).
          </p>
        </section>
      </aside>
    </>
  );
}
