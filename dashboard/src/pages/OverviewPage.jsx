import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { formatCurrency, formatPercent, formatSignedCurrency, pnlTone } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

export default function OverviewPage() {
  const {
    overview,
    events,
    scannerStatus,
    scannerRanked,
    watchlist,
    config,
    operatorMode,
    sendCommand,
    commandPending,
    refreshAll,
    diagnostics,
  } = useDashboard();
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
  const dryRunActive = Boolean(overview?.runner_status?.dry_run ?? config?.dry_run);
  const marketSnapshot = diagnostics?.market_snapshot || {};
  const marketStream = overview?.runner_status?.market_stream || overview?.health?.market_stream || {};
  const dataFeed = (overview?.health?.market_data_feed || overview?.runner_status?.market_data_feed || config?.alpaca_feed || marketSnapshot.feed || "iex").toUpperCase();
  const latestQuoteTime = latestTimestamp(marketSnapshot.latest_quote_times);
  const liveDataAge = marketSnapshot.max_live_data_age_seconds;
  const launchSymbols = useMemo(
    () => Array.from(new Set([
      ...(scannerRanked || []).slice(0, 8).map((candidate) => candidate.symbol),
      ...activeWatchlist,
      overview?.runner_status?.symbol,
      "SPY",
      "QQQ",
    ].filter(Boolean))).slice(0, 12),
    [activeWatchlist, overview?.runner_status?.symbol, scannerRanked],
  );

  return (
    <div className="app-grid">
      <section className="hero-card bot-launch-hero panel-span-2">
        <div>
          <p className="eyebrow">Bot launchpad</p>
          <h1>{botState(strategyStatus, overview)}</h1>
          <p className="hero-copy">{statusReason(overview)}</p>
          <div className={`dry-run-callout ${dryRunActive ? "is-on" : "is-off"}`}>
            <strong>Dry run is {dryRunActive ? "ON" : "OFF"}</strong>
            <span>
              {dryRunActive
                ? "The bot can scan, rank, and log decisions, but it will not submit paper orders."
                : "The bot may submit Alpaca paper orders after a valid setup passes every risk check."}
            </span>
          </div>
          <div className="quick-action-row">
            <Link to="/scanner" className="ghost-link-button">Review picks</Link>
            <Link to="/positions" className="ghost-link-button">View positions</Link>
            <Link to="/bot" className="ghost-link-button">Advanced controls</Link>
          </div>
        </div>
        <BotLaunchPanel
          activeWatchlist={activeWatchlist}
          commandPending={commandPending}
          config={config}
          launchSymbols={launchSymbols}
          operatorMode={operatorMode}
          overview={overview}
          refreshAll={refreshAll}
          scannerStatus={scannerStatus}
          sendCommand={sendCommand}
          watchlist={watchlist}
        />
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
          <InfoTile label="Bot max per trade" value={formatCurrency(config?.max_position_notional)} />
          <InfoTile label="Order mode" value={dryRunActive ? "Dry run ON" : "Paper orders ON"} tone={dryRunActive ? "warn" : "positive"} />
          <InfoTile label="Data feed" value={dataFeed} tone={dataFeed === "SIP" ? "positive" : "paper"} />
          <InfoTile label="Live data age" value={liveDataAge == null ? "n/a" : `${Number(liveDataAge).toFixed(1)}s`} tone={overview?.health?.data_fresh ? "positive" : "warn"} />
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
          <Detail label="Dry run" value={dryRunActive ? "ON - scans only, no paper orders" : "OFF - paper orders can submit"} />
          <Detail label="Market stream" value={marketStream.connected ? `Connected (${dataFeed})` : `REST fallback (${dataFeed})`} />
          <Detail label="Last quote" value={latestQuoteTime || "n/a"} />
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

function BotLaunchPanel({
  activeWatchlist,
  commandPending,
  config,
  launchSymbols,
  operatorMode,
  overview,
  refreshAll,
  scannerStatus,
  sendCommand,
  watchlist,
}) {
  const [mode, setMode] = useState(() => window.localStorage.getItem("paper-bot-launch-mode") || "auto");
  const [symbol, setSymbol] = useState(launchSymbols[0] || "SPY");
  const [amount, setAmount] = useState(String(Number(config?.max_position_notional || 500)));
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const runnerStatus = overview?.runner_status || {};
  const dryRunActive = Boolean(runnerStatus.dry_run ?? config?.dry_run);
  const amountNumber = Number(amount) || 0;
  const pinnedSymbols = scannerStatus?.pinned_symbols || watchlist?.pinned_symbols || [];

  useEffect(() => {
    if (!launchSymbols.includes(symbol) && launchSymbols[0]) {
      setSymbol(launchSymbols[0]);
    }
  }, [launchSymbols, symbol]);

  useEffect(() => {
    if (config?.max_position_notional != null) {
      setAmount(String(Number(config.max_position_notional)));
    }
  }, [config?.max_position_notional]);

  useEffect(() => {
    window.localStorage.setItem("paper-bot-launch-mode", mode);
  }, [mode]);

  const launchBot = async () => {
    setStatus("");
    setError("");
    if (amountNumber <= 0) {
      setError("Enter a dollar amount above zero.");
      return;
    }
    try {
      const riskBudget = Math.max(10, Math.min(amountNumber, Math.round(amountNumber * 0.05)));
      await sendCommand(
        "apply_config",
        {
          max_position_notional: amountNumber,
          max_capital_per_symbol: amountNumber,
          max_concurrent_positions: mode === "focus" ? 1 : Number(config?.max_concurrent_positions || 3),
          watchlist_size: mode === "focus" ? 1 : Math.max(Number(config?.watchlist_size || 10), 5),
          risk_per_trade: riskBudget,
        },
        { confirm: true },
      );

      if (mode === "focus") {
        await sendCommand("pin_symbol", { symbol, pinned: true });
        await sendCommand("set_symbol_enabled", { symbol, enabled: true });
      } else {
        for (const pinnedSymbol of pinnedSymbols) {
          await sendCommand("pin_symbol", { symbol: pinnedSymbol, pinned: false });
        }
      }

      await sendCommand("refresh_scanner");
      if (!runnerStatus.running) {
        await sendCommand("start_runner", {}, { confirm: true });
      } else if (runnerStatus.paused_new_entries) {
        await sendCommand("resume_entries");
      }
      await refreshAll();
      setStatus(mode === "focus" ? `${symbol} is armed. The bot will enter only when a strategy signal passes risk checks.` : "The bot is scanning the ranked market board.");
    } catch (requestError) {
      setError(requestError.message || String(requestError));
    }
  };

  return (
    <div className="bot-launch-card">
      <div className="segmented-control">
        <button type="button" className={mode === "auto" ? "segmented-active" : ""} onClick={() => setMode("auto")}>
          Bot picks
        </button>
        <button type="button" className={mode === "focus" ? "segmented-active" : ""} onClick={() => setMode("focus")}>
          Pick stock
        </button>
      </div>

      {mode === "focus" ? (
        <label>
          <span>Stock</span>
          <input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} placeholder="SPY" />
        </label>
      ) : (
        <div className="bot-launch-picks">
          <span>Current watchlist</span>
          <strong>{activeWatchlist.length ? activeWatchlist.slice(0, 6).join(", ") : "Scanner will choose"}</strong>
        </div>
      )}

      <label>
        <span>Max dollars per bot trade</span>
        <input value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} placeholder="500" />
      </label>

      <div className="chip-row">
        {[100, 500, 1000, 2500].map((value) => (
          <button key={value} type="button" className="chip" onClick={() => setAmount(String(value))}>
            {formatCurrency(value, { maximumFractionDigits: 0 })}
          </button>
        ))}
      </div>

      {mode === "focus" && (
        <div className="chip-row">
          {launchSymbols.map((candidateSymbol) => (
            <button key={candidateSymbol} type="button" className={`chip ${candidateSymbol === symbol ? "chip-active" : ""}`} onClick={() => setSymbol(candidateSymbol)}>
              {candidateSymbol}
            </button>
          ))}
        </div>
      )}

      <div className={`dry-run-callout compact ${dryRunActive ? "is-on" : "is-off"}`}>
        <strong>Dry run is {dryRunActive ? "ON" : "OFF"}</strong>
        <span>
          {dryRunActive
            ? "Start/update will scan and monitor only. No paper orders will be sent."
            : "Start/update can send Alpaca paper orders after signal and risk checks pass."}
        </span>
      </div>

      {status && <div className="inline-banner success">{status}</div>}
      {error && <div className="inline-banner error">{error}</div>}

      <button type="button" className="primary-button bot-launch-button" disabled={!operatorMode || commandPending || amountNumber <= 0} onClick={launchBot}>
        {runnerStatus.running ? "Update bot setup" : "Start bot"}
      </button>
      <p className="muted bot-launch-note">
        Paper trading only. Dry run controls whether paper orders are actually submitted.
      </p>
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

function latestTimestamp(valuesBySymbol) {
  if (!valuesBySymbol || typeof valuesBySymbol !== "object") {
    return null;
  }
  return Object.values(valuesBySymbol)
    .filter(Boolean)
    .sort()
    .at(-1) || null;
}
