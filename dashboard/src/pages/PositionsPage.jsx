import React from "react";
import ConfirmActionButton from "../components/ConfirmActionButton";
import { formatCurrency, formatMinutes, formatNumber, pnlTone } from "../formatters";
import { useDashboard } from "../state/DashboardContext";

export default function PositionsPage() {
  const { positions, strategyStatus, operatorMode, sendCommand, commandPending } = useDashboard();
  const activeTrade = strategyStatus?.active_trade;

  return (
    <div className="app-grid">
      <section className="panel panel-span-2">
        <div className="section-head">
          <div>
            <h2>Your positions</h2>
            <p className="muted">Everything open right now, with clear exit actions.</p>
          </div>
        </div>
        {positions.length === 0 ? (
          <div className="empty-card">
            <h3>No positions open</h3>
            <p>When you open a paper trade, the details will appear here along with a simple exit button.</p>
          </div>
        ) : (
          <div className="position-detail-grid">
            {positions.map((position) => {
              const tradeMeta = activeTrade?.symbol === position.symbol ? activeTrade : null;
              const minutesOpen = tradeMeta?.opened_at ? Math.floor((Date.now() - new Date(tradeMeta.opened_at).getTime()) / 60000) : null;
              return (
                <div className="position-detail-card" key={position.symbol}>
                  <div className="position-summary-head">
                    <div>
                      <p className="eyebrow">Open position</p>
                      <h3>{position.symbol}</h3>
                    </div>
                    <span className={`change-pill ${pnlTone(position.unrealized_pl)}`}>{formatCurrency(position.unrealized_pl)}</span>
                  </div>

                  <div className="detail-card-grid">
                    <DetailTile label="Shares" value={formatNumber(position.qty, 6)} />
                    <DetailTile label="Entry price" value={formatCurrency(position.avg_entry_price || tradeMeta?.entry_fill_price)} />
                    <DetailTile label="Current price" value={formatCurrency(position.current_price)} />
                    <DetailTile label="Market value" value={formatCurrency(position.market_value)} />
                    <DetailTile label="Unrealized PnL" value={formatCurrency(position.unrealized_pl)} tone={pnlTone(position.unrealized_pl)} />
                    <DetailTile label="Realized today" value={formatCurrency(position.realized_intraday_pl)} tone={pnlTone(position.realized_intraday_pl)} />
                    <DetailTile label="Time in trade" value={minutesOpen ? formatMinutes(minutesOpen) : "n/a"} />
                    <DetailTile label="Stop / target" value={tradeMeta?.stop_price ? `${formatCurrency(tradeMeta.stop_price)} / ${formatCurrency(tradeMeta.target_price)}` : "No active stop or target"} />
                  </div>

                  <div className="button-row left-align">
                    <ConfirmActionButton
                      disabled={!operatorMode || commandPending}
                      className="danger-button"
                      confirmText={`Exit ${position.symbol} now in paper mode?`}
                      onConfirm={() => sendCommand("close_symbol", { symbol: position.symbol }, { confirm: true })}
                    >
                      Exit now
                    </ConfirmActionButton>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

function DetailTile({ label, value, tone = "neutral" }) {
  return (
    <div className={`info-card tone-${tone} info-card-compact`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
