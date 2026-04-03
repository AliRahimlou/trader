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

export default function SettingsPage() {
  const { config, operatorMode, sendCommand, commandPending } = useDashboard();
  const [formState, setFormState] = useState({});

  useEffect(() => {
    if (!config) {
      return;
    }
    const next = {};
    [...BASIC_SETTINGS, ...ADVANCED_SETTINGS].forEach(([key]) => {
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
            disabled={!operatorMode || commandPending}
            confirmText="Toggle dry-run mode? This is only allowed while the runner is stopped."
            onConfirm={() => sendCommand("set_dry_run", { dry_run: !config?.dry_run }, { confirm: true })}
          >
            Toggle Dry Run
          </ConfirmActionButton>
        </div>

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
    flatten_at: String(formState.flatten_at),
    exit_mode: String(formState.exit_mode),
    risk_per_trade: Number(formState.risk_per_trade),
    rr_ratio: Number(formState.rr_ratio),
    commission_per_unit: Number(formState.commission_per_unit),
    min_gap_pct: Number(formState.min_gap_pct),
    min_gap_atr: Number(formState.min_gap_atr),
  };
}
