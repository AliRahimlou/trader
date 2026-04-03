import React from "react";
import { Link } from "react-router-dom";
import { botStateLabel, formatCurrency, formatPercent, formatSignedCurrency, pnlTone } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

export default function OverviewPage() {
  const { overview, events, scannerStatus, watchlist } = useDashboard();
  const account = overview?.account || {};
  const positions = overview?.positions || [];
  const strategyStatus = overview?.strategy_status || {};
  const activeWatchlist = watchlist?.active_symbols || overview?.watchlist?.active_symbols || [];
  const watchlistEntries = watchlist?.entries || overview?.watchlist?.entries || [];
  const scannedCount = scannerStatus?.scanned_count ?? overview?.scanner_status?.scanned_count ?? 0;
  const universeCount = scannerStatus?.universe_count ?? overview?.scanner_status?.universe_count ?? 0;
  const portfolioValue = Number(account.portfolio_value || 0);
  const equity = Number(account.equity || portfolioValue);
  const lastEquity = Number(account.last_equity || equity);
  const totalPnl = equity - lastEquity;
  const totalPnlPercent = lastEquity ? (totalPnl / lastEquity) * 100.0 : 0.0;
  const recentActivity = events.slice(-6).reverse();
  const activePositionCount = positions.length;

  return (
    <div className="app-grid">
      <section className="hero-card hero-portfolio panel-span-2">
        <div>
          <p className="eyebrow">Portfolio</p>
          <h1>{formatCurrency(portfolioValue)}</h1>
          <p className={`hero-copy tone-${pnlTone(totalPnl)}`}>{formatSignedCurrency(totalPnl)} today · {formatPercent(totalPnlPercent)}</p>
          <div className="quick-action-row">
            <Link to="/trade" className="primary-link-button">Invest money</Link>
            <Link to="/positions" className="ghost-link-button">View positions</Link>
            <Link to="/bot" className="ghost-link-button">Bot status</Link>
          </div>
        </div>
        <div className="hero-side-grid">
          <InfoTile label="Cash" value={formatCurrency(account.cash)} />
          <InfoTile label="Buying power" value={formatCurrency(account.buying_power)} />
          <InfoTile label="Watchlist" value={String(activeWatchlist.length)} />
          <InfoTile label="Bot state" value={botStateLabel({ status_label: botState(strategyStatus, overview) })} />
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <h2>Account snapshot</h2>
            <p className="muted">The numbers most people care about first.</p>
          </div>
        </div>
        <div className="detail-card-grid">
          <InfoTile label="Portfolio value" value={formatCurrency(account.portfolio_value)} />
          <InfoTile label="Cash" value={formatCurrency(account.cash)} />
          <InfoTile label="Buying power" value={formatCurrency(account.buying_power)} />
          <InfoTile label="Today PnL" value={formatCurrency(strategyStatus.daily_realized_pnl)} tone={pnlTone(strategyStatus.daily_realized_pnl)} />
          <InfoTile label="Total PnL" value={formatSignedCurrency(totalPnl)} tone={pnlTone(totalPnl)} />
          <InfoTile label="Trades today" value={String(strategyStatus.daily_trade_count || 0)} />
        </div>
      </section>

      <section className="panel">
        <div className="section-head">
          <div>
            <h2>Trading status</h2>
            <p className="muted">See whether automation is ready, paused, or waiting.</p>
          </div>
        </div>
        <div className="status-spotlight">
          <strong>{botState(strategyStatus, overview)}</strong>
          <p>{statusReason(overview)}</p>
        </div>
        <dl className="plain-detail-list">
          <Detail label="Open positions" value={String(activePositionCount)} />
          <Detail label="Active watchlist" value={activeWatchlist.length ? activeWatchlist.join(", ") : "none"} />
          <Detail label="Universe scanned" value={universeCount ? `${scannedCount} of ${universeCount}` : "n/a"} />
          <Detail label="Last completed bar" value={overview?.runner_status?.latest_completed_bar_time || "n/a"} />
          <Detail label="Latest heartbeat" value={overview?.runner_status?.last_heartbeat || "n/a"} />
        </dl>
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Scanner watchlist</h2>
            <p className="muted">What the runner is actively ranking and monitoring right now.</p>
          </div>
          <Link to="/scanner" className="ghost-link">Manage scanner</Link>
        </div>
        {!watchlistEntries.length ? (
          <div className="empty-card">
            <h3>No active watchlist</h3>
            <p>The scanner has not published a watchlist yet, or nothing currently qualifies.</p>
          </div>
        ) : (
          <div className="position-card-grid">
            {watchlistEntries.slice(0, 6).map((entry) => (
              <div className="position-summary-card" key={entry.symbol}>
                <div className="position-summary-head">
                  <h3>{entry.symbol}</h3>
                  <span className="change-pill neutral">#{entry.rank || "-"}</span>
                </div>
                <div className="position-summary-grid">
                  <InfoTile label="Score" value={Number(entry.score || 0).toFixed(1)} compact />
                  <InfoTile label="Signals" value={String((entry.signals || []).length)} compact />
                  <InfoTile label="Reason" value={entry.watch_reason || "ranked"} compact />
                  <InfoTile label="Status" value={entry.pinned ? "Pinned" : entry.active_position ? "Open position" : entry.enabled ? "Eligible" : "Disabled"} compact />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Open positions</h2>
            <p className="muted">Your active exposure and unrealized profit or loss.</p>
          </div>
          <Link to="/positions" className="ghost-link">See all</Link>
        </div>
        {!positions.length ? (
          <div className="empty-card">
            <h3>No open positions</h3>
            <p>Your paper account is flat. Go to Trade to open a position when you are ready.</p>
          </div>
        ) : (
          <div className="position-card-grid">
            {positions.map((position) => (
              <div className="position-summary-card" key={position.symbol}>
                <div className="position-summary-head">
                  <h3>{position.symbol}</h3>
                  <span className={`change-pill ${pnlTone(position.unrealized_pl)}`}>{formatSignedCurrency(position.unrealized_pl)}</span>
                </div>
                <div className="position-summary-grid">
                  <InfoTile label="Shares" value={position.qty} compact />
                  <InfoTile label="Entry" value={formatCurrency(position.avg_entry_price)} compact />
                  <InfoTile label="Market value" value={formatCurrency(position.market_value)} compact />
                  <InfoTile label="Current price" value={formatCurrency(position.current_price)} compact />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Recent activity</h2>
            <p className="muted">Recent trades, fills, decisions, and safety events.</p>
          </div>
          <Link to="/activity" className="ghost-link">Full activity</Link>
        </div>
        <div className="activity-feed compact-activity-feed">
          {recentActivity.map((item) => (
            <div className="activity-item" key={item.id}>
              <div className={`event-dot tone-${item.level === "ERROR" ? "negative" : item.level === "WARNING" ? "warn" : "positive"}`} />
              <div>
                <strong>{item.message || item.event}</strong>
                <p className="muted">{item.symbol || "system"} · {item.ts}</p>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function botState(strategyStatus, overview) {
  if (overview?.runner_status?.startup_state === "starting") {
    return "Starting up";
  }
  if (!overview?.runner_status?.running) {
    return "Stopped";
  }
  if (overview?.runner_status?.paused_new_entries) {
    return "Paused";
  }
  if (!overview?.health?.market_open && !overview?.runner_status?.market_open) {
    return "Market closed";
  }
  if ((strategyStatus?.active_trades || []).length) {
    return "In position";
  }
  return "Waiting for signal";
}

function statusReason(overview) {
  if (overview?.runner_status?.paused_new_entries) {
    return "Automation is connected, but new entries are paused until you resume.";
  }
  if (!overview?.runner_status?.running) {
    return "The bot is stopped. You can still review positions and trade manually when allowed.";
  }
  return "The bot is connected and watching for the next valid setup.";
}

function InfoTile({ label, value, tone = "neutral", compact = false }) {
  return (
    <div className={`info-card tone-${tone} ${compact ? "info-card-compact" : ""}`}>
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

