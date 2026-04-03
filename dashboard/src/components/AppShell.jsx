import React from "react";
import { NavLink } from "react-router-dom";
import { formatCurrency } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

const NAV_ITEMS = [
  ["Home", "/"],
  ["Scanner", "/scanner"],
  ["Trade", "/trade"],
  ["Positions", "/positions"],
  ["Activity", "/activity"],
  ["Bot", "/bot"],
  ["Settings", "/settings"],
];

export default function AppShell({ children }) {
  const {
    health,
    overview,
    streamConnected,
    error,
    operatorMode,
    setOperatorMode,
    commandPending,
    activeCommandType,
    commandStatus,
  } =
    useDashboard();
  const startupState = overview?.runner_status?.startup_state || "idle";
  const runnerState = startupState === "starting"
    ? "starting"
    : startupState === "failed"
      ? "failed"
      : overview?.runner_status?.running
        ? "running"
        : "stopped";
  const runnerTone = runnerState === "running" ? "ok" : runnerState === "starting" ? "info" : runnerState === "failed" ? "warn" : "neutral";
  const startupTone = startupState === "ready" ? "ok" : startupState === "starting" ? "info" : startupState === "failed" ? "warn" : "neutral";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-scroll">
          <div className="brand-block">
            <p className="eyebrow">Paper Investing</p>
            <h1>Paper Invest</h1>
            <p className="subtle">Practice trades, monitor automation, and stay inside paper-only guardrails.</p>
          </div>

          <div className="sidebar-status">
            <StatusPill label="Mode" value={overview?.runner_status?.mode === "paper" ? "paper only" : overview?.runner_status?.mode || "paper"} tone="paper" />
            <StatusPill label="Stream" value={streamConnected ? "connected" : "reconnecting"} tone={streamConnected ? "ok" : "warn"} />
            <StatusPill label="Market" value={health?.market_open ? "open" : "closed"} tone={health?.market_open ? "ok" : "neutral"} />
            <StatusPill label="Data" value={health?.data_fresh ? "fresh" : "delayed"} tone={health?.data_fresh ? "ok" : "warn"} />
          </div>

          <nav className="nav-list">
            {NAV_ITEMS.map(([label, href]) => (
              <NavLink
                key={href}
                to={href}
                className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="sidebar-footer">
          <button
            type="button"
            className={`operator-toggle ${operatorMode ? "operator-on" : ""}`}
            onClick={() => setOperatorMode(!operatorMode)}
          >
            {operatorMode ? "Action mode enabled" : "View only mode"}
          </button>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <div className="topbar-title-row">
              <h2>Your paper portfolio</h2>
              <span className="paper-badge">Paper trading</span>
            </div>
            <p className="subtle">Latest heartbeat: {overview?.runner_status?.last_heartbeat || "n/a"}</p>
          </div>
          <div className="topbar-right">
            <StatusPill label="Portfolio" value={formatCurrency(overview?.account?.portfolio_value || 0)} tone="paper" />
            <StatusPill
              label="Runner"
              value={runnerState}
              tone={runnerTone}
            />
            <StatusPill label="Startup" value={startupState} tone={startupTone} />
            <StatusPill
              label="Entries"
              value={overview?.runner_status?.paused_new_entries ? "paused" : "active"}
              tone={overview?.runner_status?.paused_new_entries ? "warn" : "ok"}
            />
            <StatusPill
              label="Command"
              value={commandPending ? activeCommandType || "pending" : "idle"}
              tone={commandPending ? "warn" : "neutral"}
            />
          </div>
        </header>

        {(error || health?.last_error || health?.last_warning || overview?.runner_status?.startup_error || commandStatus.phase === "error") && (
          <div className={`banner ${health?.last_error || error || overview?.runner_status?.startup_error || commandStatus.phase === "error" ? "banner-error" : "banner-warn"}`}>
            {commandStatus.phase === "error"
              ? commandStatus.message
              : error || health?.last_error || overview?.runner_status?.startup_error || health?.last_warning}
          </div>
        )}

        {commandStatus.phase === "pending" && (
          <div className="banner banner-info">{commandStatus.message}</div>
        )}

        {children}
      </main>
    </div>
  );
}

function StatusPill({ label, value, tone = "neutral" }) {
  return (
    <div className={`status-pill tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
