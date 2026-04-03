import React from "react";
import { Link } from "react-router-dom";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { useDashboard } from "../state/DashboardContext";

export default function StrategyMonitorPage() {
  const { overview, strategyStatus, scannerStatus, watchlist, operatorMode, sendCommand, commandPending, diagnostics } = useDashboard();
  const signals = strategyStatus?.latest_signals || [];
  const runnerStatus = overview?.runner_status || {};
  const watchlistEntries = watchlist?.entries || [];
  const activeWatchlist = watchlist?.active_symbols || [];
  const disabledSymbols = scannerStatus?.disabled_symbols || scannerStatus?.health?.disabled_symbols || [];
  const botLabel = !runnerStatus.running
    ? "Stopped"
    : runnerStatus.paused_new_entries
      ? "Paused"
      : (strategyStatus?.active_trades || []).length
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
          <InfoTile label="Watchlist" value={String(activeWatchlist.length)} />
          <InfoTile label="Next scan" value={scannerStatus?.next_scan_at || "n/a"} />
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
          <button disabled={!operatorMode || commandPending} onClick={() => sendCommand("refresh_scanner")}>Refresh scanner</button>
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

      <section className="panel">
        <div className="section-head">
          <div>
            <h2>Scanner status</h2>
            <p className="muted">Universe coverage, refresh timing, and watchlist health.</p>
          </div>
        </div>
        <dl className="plain-detail-list">
          <Detail label="Universe scanned" value={scannerStatus?.universe_count ? `${scannerStatus.scanned_count} of ${scannerStatus.universe_count}` : "n/a"} />
          <Detail label="Watchlist size" value={String(activeWatchlist.length)} />
          <Detail label="Pinned symbols" value={(scannerStatus?.pinned_symbols || []).join(", ") || "none"} />
          <Detail label="Disabled symbols" value={disabledSymbols.join(", ") || "none"} />
          <Detail label="Last scan" value={scannerStatus?.last_scan_at || "n/a"} />
          <Detail label="Next scan" value={scannerStatus?.next_scan_at || "n/a"} />
        </dl>
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Active watchlist</h2>
            <p className="muted">Pin, disable, or inspect the ranked symbols feeding the runner.</p>
          </div>
          <Link to="/scanner" className="ghost-link">Browse ranked scanner</Link>
        </div>
        {!watchlistEntries.length ? (
          <div className="empty-card">
            <h3>No watchlist entries yet</h3>
            <p>Run the scanner or start the runner to populate the active watchlist.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>symbol</th>
                  <th>rank</th>
                  <th>score</th>
                  <th>reason</th>
                  <th>signals</th>
                  <th>status</th>
                  <th>actions</th>
                </tr>
              </thead>
              <tbody>
                {watchlistEntries.map((entry) => (
                  <tr key={entry.symbol}>
                    <td>{entry.symbol}</td>
                    <td>{entry.rank || "-"}</td>
                    <td>{Number(entry.score || 0).toFixed(1)}</td>
                    <td>{entry.watch_reason || "ranked"}</td>
                    <td>{(entry.signals || []).length}</td>
                    <td>{entry.pinned ? "Pinned" : entry.active_position ? "Open position" : entry.enabled ? "Eligible" : "Disabled"}</td>
                    <td>
                      <div className="table-action-row">
                        <button disabled={!operatorMode || commandPending} type="button" className="ghost-button" onClick={() => sendCommand("pin_symbol", { symbol: entry.symbol, pinned: !entry.pinned })}>
                          {entry.pinned ? "Unpin" : "Pin"}
                        </button>
                        <button disabled={!operatorMode || commandPending} type="button" className="ghost-button" onClick={() => sendCommand("set_symbol_enabled", { symbol: entry.symbol, enabled: !entry.enabled })}>
                          {entry.enabled ? "Disable" : "Enable"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
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
                  <th>symbol</th>
                  <th>strategy</th>
                  <th>direction</th>
                  <th>signal time</th>
                  <th>allowed</th>
                  <th>rank</th>
                  <th>score</th>
                  <th>requested qty</th>
                  <th>approved qty</th>
                  <th>reasons</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((signal) => (
                  <tr key={signal.signal_key}>
                    <td>{signal.symbol || "n/a"}</td>
                    <td>{signal.strategy_id}</td>
                    <td>{signal.direction}</td>
                    <td>{signal.signal_time}</td>
                    <td>{String(signal.allowed)}</td>
                    <td>{signal.rank || "-"}</td>
                    <td>{signal.score ? Number(signal.score).toFixed(1) : "-"}</td>
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
  if ((strategyStatus?.active_trades || []).length) {
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
