import React from "react";
import { useDashboard } from "../state/DashboardContext";

export default function OverviewPage() {
  const { overview } = useDashboard();
  const account = overview?.account || {};
  const positions = overview?.positions || [];
  const orders = overview?.open_orders || [];
  const strategyStatus = overview?.strategy_status || {};

  return (
    <div className="page-grid">
      <section className="panel panel-hero">
        <div className="stat-grid">
          <StatCard label="Cash" value={account.cash || "n/a"} />
          <StatCard label="Buying Power" value={account.buying_power || "n/a"} />
          <StatCard label="Portfolio Value" value={account.portfolio_value || "n/a"} />
          <StatCard label="Today PnL" value={strategyStatus.daily_realized_pnl ?? "n/a"} />
          <StatCard label="Last Bar" value={overview?.runner_status?.latest_completed_bar_time || "n/a"} />
          <StatCard label="Trades Today" value={strategyStatus.daily_trade_count ?? 0} />
        </div>
      </section>

      <section className="panel">
        <h3>Runner Summary</h3>
        <dl className="detail-list">
          <Detail label="Paper Only" value={String(overview?.runner_status?.paper_only ?? true)} />
          <Detail label="Symbol" value={overview?.runner_status?.symbol || "n/a"} />
          <Detail label="Configured Strategies" value={(overview?.runner_status?.configured_strategies || []).join(", ") || "n/a"} />
          <Detail label="Enabled Strategies" value={Object.entries(overview?.runner_status?.enabled_strategies || {}).filter(([, enabled]) => enabled).map(([name]) => name).join(", ") || "none"} />
          <Detail label="Paused Entries" value={String(overview?.runner_status?.paused_new_entries ?? false)} />
          <Detail label="Latest Heartbeat" value={overview?.runner_status?.last_heartbeat || "n/a"} />
        </dl>
      </section>

      <section className="panel">
        <h3>Open Positions</h3>
        <DataTable
          columns={["symbol", "qty", "avg_entry_price", "market_value", "unrealized_pl"]}
          rows={positions}
          emptyMessage="No open positions."
        />
      </section>

      <section className="panel">
        <h3>Open Orders</h3>
        <DataTable
          columns={["id", "symbol", "side", "status", "qty", "filled_qty", "filled_avg_price"]}
          rows={orders}
          emptyMessage="No open orders."
        />
      </section>
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="stat-card">
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

function DataTable({ columns, rows, emptyMessage }) {
  if (!rows.length) {
    return <p className="subtle">{emptyMessage}</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.id || row.symbol || index}>
              {columns.map((column) => (
                <td key={column}>{String(row[column] ?? "n/a")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
