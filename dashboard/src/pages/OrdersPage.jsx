import React from "react";
import { formatCurrency } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

export default function OrdersPage() {
  const { orders, commands, events } = useDashboard();
  const activityFeed = [...events].slice(-20).reverse();

  return (
    <div className="app-grid">
      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Recent activity</h2>
            <p className="muted">A simple feed of trades, fills, bot decisions, and warnings.</p>
          </div>
        </div>
        <div className="activity-feed">
          {activityFeed.map((item) => (
            <div className="activity-item card-like" key={item.id}>
              <div className={`event-dot tone-${item.level === "ERROR" ? "negative" : item.level === "WARNING" ? "warn" : "positive"}`} />
              <div className="activity-body">
                <div className="activity-title-row">
                  <strong>{item.message || item.event}</strong>
                  <span className="muted">{item.ts}</span>
                </div>
                <p className="muted">{item.symbol || "System"}{item.strategy ? ` · ${item.strategy}` : ""}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Order history</h2>
            <p className="muted">Submitted, filled, canceled, or rejected paper orders.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>symbol</th>
                <th>side</th>
                <th>status</th>
                <th>size</th>
                <th>filled</th>
                <th>avg price</th>
                <th>submitted</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <td>{order.symbol}</td>
                  <td>{order.side}</td>
                  <td>{order.status}</td>
                  <td>{order.notional ? formatCurrency(order.notional) : order.qty || "n/a"}</td>
                  <td>{order.filled_qty || "0"}</td>
                  <td>{order.filled_avg_price ? formatCurrency(order.filled_avg_price) : "n/a"}</td>
                  <td>{order.submitted_at || order.created_at || "n/a"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel panel-span-2">
        <details className="advanced-details">
          <summary>Advanced command history</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>command</th>
                  <th>status</th>
                  <th>actor</th>
                  <th>updated</th>
                </tr>
              </thead>
              <tbody>
                {commands.map((command) => (
                  <tr key={command.id}>
                    <td>{command.command_type}</td>
                    <td>{command.status}</td>
                    <td>{command.actor}</td>
                    <td>{command.updated_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </section>
    </div>
  );
}
