import React from "react";
import { useDashboard } from "../state/DashboardContext";

export default function StrategyMonitorPage() {
  const { strategyStatus } = useDashboard();
  const signals = strategyStatus?.latest_signals || [];

  return (
    <div className="page-grid">
      <section className="panel">
        <h3>Per-Strategy Status</h3>
        <div className="status-grid">
          {(strategyStatus?.strategies || []).map((strategy) => (
            <div key={strategy.strategy_id} className="mini-card">
              <span>{strategy.strategy_id}</span>
              <strong>{strategy.enabled ? "enabled" : "disabled"}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h3>Current Limits</h3>
        <dl className="detail-list">
          <Detail label="Cooldown Active" value={String(strategyStatus?.cooldown_active ?? false)} />
          <Detail label="Cooldown Until" value={strategyStatus?.cooldown_until || "n/a"} />
          <Detail label="Daily Trades" value={strategyStatus?.daily_trade_count ?? 0} />
          <Detail label="Daily Realized PnL" value={strategyStatus?.daily_realized_pnl ?? 0} />
          <Detail label="Max Daily Loss" value={strategyStatus?.max_daily_loss ?? "n/a"} />
          <Detail label="Max Trades / Day" value={strategyStatus?.max_trades_per_day ?? "n/a"} />
        </dl>
      </section>

      <section className="panel panel-span-2">
        <h3>Latest Signal Decisions</h3>
        {signals.length === 0 ? (
          <p className="subtle">No recent signal evaluations.</p>
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
