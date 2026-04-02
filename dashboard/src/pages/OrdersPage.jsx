import React from "react";
import { useDashboard } from "../state/DashboardContext";

export default function OrdersPage() {
  const { orders, commands } = useDashboard();

  return (
    <div className="page-grid">
      <section className="panel panel-span-2">
        <h3>Orders & Executions</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>id</th>
                <th>symbol</th>
                <th>side</th>
                <th>status</th>
                <th>qty</th>
                <th>filled qty</th>
                <th>filled avg</th>
                <th>time in force</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <td>{order.id}</td>
                  <td>{order.symbol}</td>
                  <td>{order.side}</td>
                  <td>{order.status}</td>
                  <td>{order.qty || order.notional || "n/a"}</td>
                  <td>{order.filled_qty}</td>
                  <td>{order.filled_avg_price || "n/a"}</td>
                  <td>{order.time_in_force}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h3>Recent Commands</h3>
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
      </section>
    </div>
  );
}
