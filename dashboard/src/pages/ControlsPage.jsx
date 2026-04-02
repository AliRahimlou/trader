import React from "react";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { useDashboard } from "../state/DashboardContext";

export default function ControlsPage() {
  const { overview, config, strategyStatus, operatorMode, sendCommand, commandPending } = useDashboard();
  const symbol = overview?.runner_status?.symbol || "SPY";
  const symbolEnabled = overview?.runner_status?.enabled_symbols?.[symbol] ?? true;
  const controlsDisabled = !operatorMode || commandPending;

  return (
    <div className="page-grid">
      <section className="panel">
        <h3>Runner Controls</h3>
        <div className="button-grid">
          <ConfirmActionButton
            disabled={controlsDisabled}
            className="primary-button"
            confirmText="Start the paper runner? The backend will become responsible for live paper decisions."
            onConfirm={() => sendCommand("start_runner", {}, { confirm: true })}
          >
            Start Runner
          </ConfirmActionButton>
          <ConfirmActionButton
            disabled={controlsDisabled}
            className="danger-button"
            confirmText="Stop the runner?"
            onConfirm={() => sendCommand("stop_runner", {}, { confirm: true })}
          >
            Stop Runner
          </ConfirmActionButton>
          <ConfirmActionButton
            disabled={controlsDisabled}
            confirmText="Run a single paper cycle now?"
            onConfirm={() => sendCommand("run_once", {}, { confirm: true })}
          >
            Run Once
          </ConfirmActionButton>
          <ConfirmActionButton
            disabled={controlsDisabled}
            confirmText="Run the paper smoke test? This submits a tiny real paper order."
            onConfirm={() => sendCommand("smoke_test", {}, { confirm: true })}
          >
            Smoke Test
          </ConfirmActionButton>
        </div>
      </section>

      <section className="panel">
        <h3>Execution Controls</h3>
        <div className="button-grid">
          <button
            disabled={controlsDisabled || overview?.runner_status?.paused_new_entries}
            onClick={() => sendCommand("pause_entries")}
          >
            Pause Entries
          </button>
          <button
            disabled={controlsDisabled || !overview?.runner_status?.paused_new_entries}
            onClick={() => sendCommand("resume_entries")}
          >
            Resume Entries
          </button>
          <ConfirmActionButton
            disabled={controlsDisabled}
            className="danger-button"
            confirmText="Flatten all paper positions?"
            onConfirm={() => sendCommand("flatten_all", {}, { confirm: true })}
          >
            Flatten All
          </ConfirmActionButton>
          <ConfirmActionButton
            disabled={controlsDisabled}
            className="danger-button"
            confirmText="Cancel all open paper orders?"
            onConfirm={() => sendCommand("cancel_open_orders", {}, { confirm: true })}
          >
            Cancel Open Orders
          </ConfirmActionButton>
        </div>
      </section>

      <section className="panel">
        <h3>Runtime Overrides</h3>
        <p className="subtle">
          Persisted operator overrides survive restarts. Reset clears the local override snapshot and restores configured defaults.
        </p>
        <dl className="detail-list compact-detail-list">
          <Detail label="active" value={String(config?.runtime_overrides_active ?? false)} />
          <Detail
            label="override keys"
            value={(config?.runtime_override_keys || []).join(", ") || "none"}
          />
        </dl>
        <ConfirmActionButton
          disabled={controlsDisabled || !(config?.runtime_overrides_active ?? false)}
          className="danger-button"
          confirmText="Reset all persisted runtime overrides back to configured defaults?"
          onConfirm={() => sendCommand("reset_runtime_overrides", {}, { confirm: true })}
        >
          Reset Overrides
        </ConfirmActionButton>
      </section>

      <section className="panel">
        <h3>Symbols</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>symbol</th>
                <th>enabled</th>
                <th>toggle</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{symbol}</td>
                <td>{String(symbolEnabled)}</td>
                <td>
                  <button
                    disabled={controlsDisabled}
                    onClick={() =>
                      sendCommand("set_symbol_enabled", { symbol, enabled: !symbolEnabled })
                    }
                  >
                    {symbolEnabled ? "Disable" : "Enable"}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h3>Strategies</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>strategy</th>
                <th>enabled</th>
                <th>toggle</th>
              </tr>
            </thead>
            <tbody>
              {(strategyStatus?.strategies || []).map((strategy) => (
                <tr key={strategy.strategy_id}>
                  <td>{strategy.strategy_id}</td>
                  <td>{String(strategy.enabled)}</td>
                  <td>
                    <button
                      disabled={controlsDisabled}
                      onClick={() =>
                        sendCommand("set_strategy_enabled", {
                          strategy: strategy.strategy_id,
                          enabled: !strategy.enabled,
                        })
                      }
                    >
                      {strategy.enabled ? "Disable" : "Enable"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
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
