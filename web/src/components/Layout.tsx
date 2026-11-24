import { Bell, BookOpen, ChartCandlestick, Database, FlaskConical, GraduationCap, LayoutDashboard, Lock, Zap } from "lucide-react";
import { type ReactNode, useCallback, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { PAGE_DATA_MAX_AGE_MS, useOverview } from "../api/client";
import { fmtAge, fmtLocal, fmtUtc, fmtUtcDay } from "../lib/format";
import { useNow } from "../lib/useNow";
import { Badge, WorkerBadge } from "./Badge";
import { InfoTip } from "./InfoTip";
import { LearnDrawer } from "./LearnDrawer";

function NavItem({ to, icon, label, locked }: { to: string; icon: ReactNode; label: string; locked?: boolean }) {
  return (
    <NavLink to={to} end={to === "/"} className={({ isActive }) => `nav-item${isActive ? " active" : ""}${locked ? " locked" : ""}`}>
      {icon}
      <span>{label}</span>
      {locked && <Lock size={12} className="nav-lock" aria-label="locked" />}
    </NavLink>
  );
}

function StatusStrip() {
  const { data, isError, dataUpdatedAt } = useOverview();
  const now = useNow(1000);
  const pageAgeMs = now - dataUpdatedAt;
  const current = data && !isError && pageAgeMs <= PAGE_DATA_MAX_AGE_MS;

  return (
    <div className="status-strip" role="status" aria-live="polite">
      {!current ? (
        <Badge tone="stale">{isError ? "API not reachable" : "Waiting for data"}</Badge>
      ) : (
        <>
          <span className="strip-item">
            <WorkerBadge label={data.worker.label} />
            <InfoTip term="worker" />
          </span>
          <span className="strip-item">
            {data.market.open ? (
              <Badge tone="live">Market open</Badge>
            ) : (
              <Badge tone="closed">Market closed · reopens {fmtUtcDay(data.market.next_open)}</Badge>
            )}
          </span>
          <span className="strip-item dim">
            Source <strong>{data.source}</strong> · updated {fmtAge(pageAgeMs / 1000)} ago
          </span>
        </>
      )}
      <span className="strip-clock num" title="Your computer's local time in brackets">
        {fmtUtc(new Date(now).toISOString())} <span className="dim">({fmtLocal(new Date(now))} local)</span>
      </span>
    </div>
  );
}

export function Layout() {
  const [learnOpen, setLearnOpen] = useState(false);
  const closeLearn = useCallback(() => setLearnOpen(false), []);
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <div className="brand-name">Forex Scanner</div>
            <div className="brand-sub">learning &amp; demo only</div>
          </div>
        </div>
        <nav aria-label="Main">
          <NavItem to="/" icon={<LayoutDashboard size={16} />} label="Overview" />
          <NavItem to="/markets" icon={<ChartCandlestick size={16} />} label="Markets" />
          <NavItem to="/alerts" icon={<Bell size={16} />} label="Alerts" />
          <div className="nav-section">Later</div>
          <NavItem to="/signals" icon={<Zap size={16} />} label="Signals" locked />
          <NavItem to="/journal" icon={<BookOpen size={16} />} label="Journal" locked />
          <NavItem to="/backtests" icon={<FlaskConical size={16} />} label="Backtests" locked />
          <div className="nav-section">System</div>
          <NavItem to="/data" icon={<Database size={16} />} label="Data health" />
        </nav>
        <p className="sidebar-note">No trading happens here. Nothing on this page is financial advice.</p>
      </aside>
      <div className="main">
        <header className="topbar">
          <StatusStrip />
          <button type="button" className="btn btn-sm learn-btn" onClick={() => setLearnOpen(true)}>
            <GraduationCap size={15} aria-hidden="true" /> Learn
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      <LearnDrawer open={learnOpen} onClose={closeLearn} />
    </div>
  );
}
