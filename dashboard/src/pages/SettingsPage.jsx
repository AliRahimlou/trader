import React, { useEffect, useState } from "react";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { useDashboard } from "../state/DashboardContext";

const EDITABLE_KEYS = [
  "poll_seconds",
  "max_position_qty",
  "max_position_notional",
  "max_daily_loss",
  "max_trades_per_day",
  "cooldown_minutes",
  "flatten_at",
  "exit_mode",
  "risk_per_trade",
  "rr_ratio",
  "commission_per_unit",
  "min_gap_pct",
  "min_gap_atr",
];

export default function SettingsPage() {
  const { config, operatorMode, sendCommand, commandPending } = useDashboard();
  const [formState, setFormState] = useState({});

  useEffect(() => {
    if (!config) {
      return;
    }
    const next = {};
    EDITABLE_KEYS.forEach((key) => {
      next[key] = config[key] ?? "";
    });
    setFormState(next);
  }, [config]);

  const updateField = (key, value) => {
    setFormState((current) => ({ ...current, [key]: value }));
  };

  return (
    <div className="page-grid">
      <section className="panel panel-span-2">
        <h3>Effective Runtime Config</h3>
        <div className="settings-grid">
          {EDITABLE_KEYS.map((key) => (
            <label key={key}>
              <span>{key}</span>
              <input value={formState[key] ?? ""} onChange={(event) => updateField(key, event.target.value)} />
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
