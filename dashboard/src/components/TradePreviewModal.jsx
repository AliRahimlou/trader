import React from "react";
import { formatCurrency, formatNumber } from "../formatters";

export default function TradePreviewModal({ preview, pending, onCancel, onConfirm }) {
  if (!preview) {
    return null;
  }

  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal-card" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <p className="eyebrow">Paper Trade Preview</p>
            <h3>
              {preview.side === "buy" ? "Buy" : "Sell"} {preview.symbol}
            </h3>
          </div>
          <button type="button" className="ghost-icon-button" onClick={onCancel}>
            Close
          </button>
        </div>

        <div className="preview-grid">
          <PreviewRow label="Amount to invest" value={formatCurrency(preview.amount_dollars)} />
          <PreviewRow label="Estimated price" value={formatCurrency(preview.estimated_price)} />
          <PreviewRow label="Estimated shares" value={formatNumber(preview.estimated_qty, 6)} />
          <PreviewRow label="Estimated order value" value={formatCurrency(preview.estimated_notional)} />
          <PreviewRow label="Available buying power" value={formatCurrency(preview.buying_power)} />
          <PreviewRow label="Current position" value={formatNumber(preview.position_qty, 6)} />
        </div>

        {!!preview.warnings?.length && (
          <div className="info-stack">
            {preview.warnings.map((warning) => (
              <div className="inline-banner warn" key={warning}>
                {warning}
              </div>
            ))}
          </div>
        )}

        {!preview.can_submit && <div className="inline-banner error">{preview.submit_reason}</div>}

        <div className="modal-actions">
          <button type="button" className="ghost-button" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="primary-button" disabled={!preview.can_submit || pending} onClick={onConfirm}>
            {pending ? "Submitting..." : "Confirm Paper Trade"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PreviewRow({ label, value }) {
  return (
    <div className="preview-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}