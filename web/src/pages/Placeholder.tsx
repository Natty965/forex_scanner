import { Lock } from "lucide-react";

interface PageInfo {
  title: string;
  text: string;
  willShow?: string[];
  before?: string[];
}

/** A page that exists in the navigation but isn't available yet, with an honest explanation. */
export function Placeholder({ title, text, willShow, before, locked }: PageInfo & { locked?: boolean }) {
  return (
    <section className="card placeholder">
      <h1>
        {locked && <Lock size={18} aria-hidden="true" />} {title}
      </h1>
      <p>{text}</p>
      {willShow && (
        <>
          <h2>What will be here</h2>
          <ul>{willShow.map((s) => <li key={s}>{s}</li>)}</ul>
        </>
      )}
      {before && (
        <>
          <h2>What has to happen first</h2>
          <ol>{before.map((s) => <li key={s}>{s}</li>)}</ol>
        </>
      )}
    </section>
  );
}

export const PAGES: Record<"signals" | "journal" | "backtests", PageInfo> = {
  signals: {
    title: "Signals",
    text: "Locked. No strategy exists yet, so nothing on this dashboard is a signal, including trends, EMA alignment and RSI zones.",
    willShow: [
      "Potential setups (forming, clearly marked as not a signal)",
      "Strategy signals with entry zone, stop-loss, take-profit, risk/reward, reasons and what would invalidate them",
      "Cancelled or invalidated setups",
    ],
    before: [
      "Market structure: support and resistance (v0.5)",
      "A written strategy with exact, testable rules",
      "A backtest on past data, including trading costs (spread)",
      "Your review and approval of the results",
    ],
  },
  journal: {
    title: "Journal",
    text: "Locked. This project never places real trades; the journal will record demo trades.",
    willShow: ["Demo trades with entry, exit, result and notes", "Win/loss statistics and drawdown"],
    before: ["Demo trading integration", "Risk calculator"],
  },
  backtests: {
    title: "Backtests",
    text: "Locked. Backtests test a strategy on past data, and they come before any signal is shown.",
    willShow: ["Strategy performance on historical data", "Win rate, average result, maximum drawdown", "Results by pair and timeframe"],
    before: ["A written strategy to test", "Trading-cost assumptions (spread), since our prices are mid prices"],
  },
};
