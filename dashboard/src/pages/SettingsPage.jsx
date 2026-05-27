import React, { useEffect, useState } from "react";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { useDashboard } from "../state/DashboardContext";

const BASIC_SETTINGS = [
  ["poll_seconds", "Refresh speed (seconds)", "How often the app checks for updates and new bars."],
  ["max_position_notional", "Maximum dollars per position", "The largest paper position size the bot is allowed to open."],
  ["max_daily_loss", "Daily loss limit", "If losses reach this amount, new entries should stop for the day."],
  ["max_trades_per_day", "Trade limit per day", "How many new trades the bot can open in one session."],
  ["cooldown_minutes", "Cooldown after exit", "How long the bot waits after closing a trade before re-entering."],
  ["flatten_at", "Flatten by time (ET)", "Time of day when the bot should be done holding positions."],
];

const ADVANCED_SETTINGS = [
  ["max_position_qty", "Maximum shares", "Hard cap on share size when notional sizing is available."],
  ["exit_mode", "Exit style", "Choose broker-held brackets or in-process exit handling."],
  ["risk_per_trade", "Risk per trade", "The internal risk budget used by the strategy engine."],
  ["rr_ratio", "Reward / risk ratio", "How far the target sits relative to the stop."],
  ["commission_per_unit", "Commission per share", "Used for more realistic paper PnL estimates."],
  ["min_gap_pct", "Minimum gap percent", "Minimum fair value gap size as a percent filter."],
  ["min_gap_atr", "Minimum gap ATR", "Minimum fair value gap size relative to ATR."],
];

const PROTECTION_SETTINGS = [
  ["protection_loss_lookback_trades", "Loss guard lookback", "How many recent closed trades the global loss guard reviews."],
  ["protection_loss_limit", "Global loss limit", "Pause new entries after this many losing trades inside the lookback."],
  ["protection_loss_lock_minutes", "Global lock minutes", "How long the bot waits after the latest loss guard trigger."],
  ["protection_max_intraday_drawdown", "Intraday drawdown guard", "Pause new entries if closed-trade paper PnL falls this far from today's peak."],
  ["protection_symbol_loss_limit", "Symbol loss limit", "Temporarily lock one symbol after this many recent losing trades."],
  ["protection_symbol_lock_minutes", "Symbol lock minutes", "How long an underperforming symbol stays locked."],
];

export default function SettingsPage() {
  const { config, overview, operatorMode, sendCommand, commandPending } = useDashboard();
  const [formState, setFormState] = useState({});
  const dryRunActive = Boolean(config?.dry_run);
  const runnerRunning = Boolean(overview?.runner_status?.running);
  const dataFeed = (overview?.health?.market_data_feed || config?.alpaca_feed || "iex").toUpperCase();
  const streamStatus = overview?.health?.market_stream || overview?.runner_status?.market_stream || {};

  useEffect(() => {
    if (!config) {
      return;
    }
    const next = {};
    [...BASIC_SETTINGS, ...ADVANCED_SETTINGS, ...PROTECTION_SETTINGS].forEach(([key]) => {
      next[key] = config[key] ?? "";
    });
    setFormState(next);
  }, [config]);

  const updateField = (key, value) => {
    setFormState((current) => ({ ...current, [key]: value }));
  };

  return (
    <div className="app-grid">
      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Trading defaults</h2>
            <p className="muted">Plain-English settings for how the paper bot should behave.</p>
          </div>
        </div>

        <div className={`dry-run-callout settings-dry-run ${dryRunActive ? "is-on" : "is-off"}`}>
          <strong>Dry run is {dryRunActive ? "ON" : "OFF"}</strong>
          <span>
            {dryRunActive
              ? "The runner may scan and manage the watchlist, but it will not submit Alpaca paper orders."
              : "The runner can submit Alpaca paper orders after a setup passes signal, buying-power, and risk checks."}
          </span>
        </div>

        <div className="settings-status-row">
          <div className="info-card tone-paper">
            <span>Market data feed</span>
            <strong>{dataFeed}</strong>
          </div>
          <div className={`info-card ${streamStatus.connected ? "tone-positive" : "tone-warn"}`}>
            <span>Market stream</span>
            <strong>{streamStatus.connected ? "Connected" : "REST fallback"}</strong>
          </div>
        </div>
        <p className="helper-text settings-action-note">
          Change `LIVE_PAPER_ALPACA_FEED` to `sip` only if your Alpaca account has SIP entitlement, then restart the backend.
        </p>

        <div className="settings-grid user-settings-grid">
          {BASIC_SETTINGS.map(([key, label, helper]) => (
            <label key={key}>
              <span>{label}</span>
              <input value={formState[key] ?? ""} onChange={(event) => updateField(key, event.target.value)} />
              <small className="helper-text">{helper}</small>
            </label>
          ))}
        </div>

        <div className="button-row">
          <ConfirmActionButton
            disabled={!operatorMode || commandPending}
            className="primary-button"
            confirmText="Apply updated runtime config?"
            onConfirm={() => sendCommand("apply_config", normalizePayload(formState), { confirm: true })}
          >
            Apply Runtime Config
          </ConfirmActionButton>
          <ConfirmActionButton
            disabled={!operatorMode || commandPending || runnerRunning || !config}
            confirmText={
              dryRunActive
                ? "Turn dry run OFF? The runner may submit Alpaca paper orders after valid signals and risk checks. This is only allowed while the runner is stopped."
                : "Turn dry run ON? The runner will scan only and will not submit paper orders. This is only allowed while the runner is stopped."
            }
            onConfirm={() => sendCommand("set_dry_run", { dry_run: !config?.dry_run }, { confirm: true })}
          >
            {dryRunActive ? "Turn Dry Run Off" : "Turn Dry Run On"}
          </ConfirmActionButton>
        </div>
        {runnerRunning && (
          <p className="helper-text settings-action-note">Stop the runner before changing dry run mode.</p>
        )}

        <details className="advanced-details">
          <summary>Advanced settings</summary>
          <div className="settings-grid user-settings-grid advanced-settings-grid">
            {ADVANCED_SETTINGS.map(([key, label, helper]) => (
              <label key={key}>
                <span>{label}</span>
                <input value={formState[key] ?? ""} onChange={(event) => updateField(key, event.target.value)} />
                <small className="helper-text">{helper}</small>
              </label>
            ))}
          </div>
          <h3 className="settings-subhead">Risk protections</h3>
          <div className="settings-grid user-settings-grid advanced-settings-grid">
            {PROTECTION_SETTINGS.map(([key, label, helper]) => (
              <label key={key}>
                <span>{label}</span>
                <input value={formState[key] ?? ""} onChange={(event) => updateField(key, event.target.value)} />
                <small className="helper-text">{helper}</small>
              </label>
            ))}
          </div>
          <pre className="json-block">{JSON.stringify(config, null, 2)}</pre>
        </details>
      </section>
    </div>
  );
}

function normalizePayload(formState) {
  return {
    poll_seconds: Number(formState.poll_seconds),
    max_position_qty: Number(formState.max_position_qty),
    max_position_notional: Number(formState.max_position_notional),
    max_daily_loss: Number(formState.max_daily_loss),
    max_trades_per_day: Number(formState.max_trades_per_day),
    cooldown_minutes: Number(formState.cooldown_minutes),
    protection_loss_lookback_trades: Number(formState.protection_loss_lookback_trades),
    protection_loss_limit: Number(formState.protection_loss_limit),
    protection_loss_lock_minutes: Number(formState.protection_loss_lock_minutes),
    protection_max_intraday_drawdown: Number(formState.protection_max_intraday_drawdown),
    protection_symbol_loss_limit: Number(formState.protection_symbol_loss_limit),
    protection_symbol_lock_minutes: Number(formState.protection_symbol_lock_minutes),
    flatten_at: String(formState.flatten_at),
    exit_mode: String(formState.exit_mode),
    risk_per_trade: Number(formState.risk_per_trade),
    rr_ratio: Number(formState.rr_ratio),
    commission_per_unit: Number(formState.commission_per_unit),
    min_gap_pct: Number(formState.min_gap_pct),
    min_gap_atr: Number(formState.min_gap_atr),
  };
}
