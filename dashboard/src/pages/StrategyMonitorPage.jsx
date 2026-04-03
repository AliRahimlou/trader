import React from "react";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { useDashboard } from "../state/DashboardContext";

export default function StrategyMonitorPage() {
  const { overview, strategyStatus, operatorMode, sendCommand, commandPending, diagnostics } = useDashboard();
  const signals = strategyStatus?.latest_signals || [];
  const runnerStatus = overview?.runner_status || {};
  const botLabel = !runnerStatus.running
    ? "Stopped"
    : runnerStatus.paused_new_entries
      ? "Paused"
      : strategyStatus?.active_trade
        ? "In position"
        : runnerStatus.market_open
          ? "Waiting for signal"
          : "Market closed";

  return (
    <div className="app-grid">
      <section className="hero-card panel-span-2">
        <div>
          <p className="eyebrow">Bot status</p>
          <h1>{botLabel}</h1>
          <p className="hero-copy">{runnerStatus.startup_error || diagnostics?.health?.last_warning || statusReason(runnerStatus, strategyStatus)}</p>
        </div>
        <div className="hero-side-grid">
          <InfoTile label="Last heartbeat" value={runnerStatus.last_heartbeat || "n/a"} />
          <InfoTile label="Data freshness" value={runnerStatus.data_fresh ? "Fresh" : "Delayed"} tone={runnerStatus.data_fresh ? "positive" : "warn"} />
          <InfoTile label="Latest completed bar" value={runnerStatus.latest_completed_bar_time || "n/a"} />
          <InfoTile label="Trading state" value={runnerStatus.paused_new_entries ? "Paused" : "Active"} />
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <h2>Automation controls</h2>
            <p className="muted">Common controls up front. Risky actions still need confirmation.</p>
          </div>
        </div>
        <div className="button-grid action-grid">
          <ConfirmActionButton disabled={!operatorMode || commandPending || runnerStatus.running} className="primary-button" confirmText="Start the paper bot?" onConfirm={() => sendCommand("start_runner", {}, { confirm: true })}>
            Start bot
          </ConfirmActionButton>
          <ConfirmActionButton disabled={!operatorMode || commandPending || !runnerStatus.running} className="danger-button" confirmText="Stop the bot?" onConfirm={() => sendCommand("stop_runner", {}, { confirm: true })}>
            Stop bot
          </ConfirmActionButton>
          <button disabled={!operatorMode || commandPending || runnerStatus.paused_new_entries} onClick={() => sendCommand("pause_entries")}>Pause entries</button>
          <button disabled={!operatorMode || commandPending || !runnerStatus.paused_new_entries} onClick={() => sendCommand("resume_entries")}>Resume entries</button>
          <ConfirmActionButton disabled={!operatorMode || commandPending} className="danger-button" confirmText="Flatten all paper positions?" onConfirm={() => sendCommand("flatten_all", {}, { confirm: true })}>
            Flatten all
          </ConfirmActionButton>
          <ConfirmActionButton disabled={!operatorMode || commandPending} className="danger-button" confirmText="Cancel all open paper orders?" onConfirm={() => sendCommand("cancel_open_orders", {}, { confirm: true })}>
            Cancel open orders
          </ConfirmActionButton>
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <h2>Strategies</h2>
            <p className="muted">Turn individual strategies on or off without leaving the app.</p>
          </div>
        </div>
        <div className="status-grid">
          {(strategyStatus?.strategies || []).map((strategy) => (
            <div key={strategy.strategy_id} className="mini-card strategy-toggle-card">
              <span>{strategy.strategy_id}</span>
              <strong>{strategy.enabled ? "On" : "Off"}</strong>
              <button disabled={!operatorMode || commandPending} type="button" className="ghost-button" onClick={() => sendCommand("set_strategy_enabled", { strategy: strategy.strategy_id, enabled: !strategy.enabled })}>
                {strategy.enabled ? "Turn off" : "Turn on"}
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <h2>What the bot is doing</h2>
            <p className="muted">A simple explanation of the next thing it is waiting for.</p>
          </div>
        </div>
        <dl className="plain-detail-list">
          <Detail label="Cooldown active" value={strategyStatus?.cooldown_active ? "Yes" : "No"} />
          <Detail label="Cooldown until" value={strategyStatus?.cooldown_until || "n/a"} />
          <Detail label="Trades today" value={strategyStatus?.daily_trade_count ?? 0} />
          <Detail label="Daily realized PnL" value={strategyStatus?.daily_realized_pnl ?? 0} />
          <Detail label="Daily loss limit" value={strategyStatus?.max_daily_loss ?? "n/a"} />
          <Detail label="Trade limit" value={strategyStatus?.max_trades_per_day ?? "n/a"} />
        </dl>
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Recent signal decisions</h2>
            <p className="muted">Recent opportunities and why the bot accepted or skipped them.</p>
          </div>
        </div>
        {signals.length === 0 ? (
          <div className="empty-card">
            <h3>No recent signal evaluations</h3>
            <p>The bot has not recorded a fresh decision yet for the current session.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>strategy</th>
                  <th>direction</th>
                  <th>signal time</th>
                  <th>allowed</th>
                  <th>requested qty</th>
                  <th>approved qty</th>
                  <th>reasons</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((signal) => (
                  <tr key={signal.signal_key}>
                    <td>{signal.strategy_id}</td>
                    <td>{signal.direction}</td>
                    <td>{signal.signal_time}</td>
                    <td>{String(signal.allowed)}</td>
                    <td>{signal.requested_qty}</td>
                    <td>{signal.approved_qty}</td>
                    <td>{(signal.reasons || []).join(", ") || "passed"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel panel-span-2">
        <details className="advanced-details">
          <summary>Advanced diagnostics</summary>
          <pre className="json-block">{JSON.stringify(diagnostics, null, 2)}</pre>
        </details>
      </section>
    </div>
  );
}

function statusReason(runnerStatus, strategyStatus) {
  if (!runnerStatus.running) {
    return "The bot is currently stopped. Start it when you want automation to monitor the market.";
  }
  if (runnerStatus.paused_new_entries) {
    return "The bot is connected, but it will not open anything new until you resume entries.";
  }
  if (strategyStatus?.active_trade) {
    return "The bot is managing an open paper position and monitoring exits.";
  }
  if (!runnerStatus.market_open) {
    return "The market is closed, so the bot is waiting for the next session.";
  }
  return "The bot is ready and waiting for the next valid trade setup.";
}

function InfoTile({ label, value, tone = "neutral" }) {
  return (
    <div className={`info-card tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Detail({ label, value }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
