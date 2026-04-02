import React from "react";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { useDashboard } from "../state/DashboardContext";

export default function PositionsPage() {
  const { positions, operatorMode, sendCommand, commandPending } = useDashboard();

  return (
    <div className="page-grid">
      <section className="panel panel-span-2">
        <h3>Positions</h3>
        {positions.length === 0 ? (
          <p className="subtle">No open positions.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>symbol</th>
                  <th>qty</th>
                  <th>avg entry</th>
                  <th>market value</th>
                  <th>unrealized PnL</th>
                  <th>manual close</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={position.symbol}>
                    <td>{position.symbol}</td>
                    <td>{position.qty}</td>
                    <td>{position.avg_entry_price || position.entry_fill_price || "n/a"}</td>
                    <td>{position.market_value || "n/a"}</td>
                    <td>{position.unrealized_pl || "n/a"}</td>
                    <td>
                      <ConfirmActionButton
                        disabled={!operatorMode || commandPending}
                        className="danger-button"
                        confirmText={`Close ${position.symbol} in paper mode?`}
                        onConfirm={() =>
                          sendCommand("close_symbol", { symbol: position.symbol }, { confirm: true })
                        }
                      >
                        Close
                      </ConfirmActionButton>
                    </td>
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
