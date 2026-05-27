import { XCircle } from 'lucide-react';

import type { Position } from '../types/trading';

interface Props {
  positions: Position[];
  onClose: (positionId: number) => void;
}

export function OpenPositionCard({ positions, onClose }: Props) {
  const open = positions.filter((position) => position.status === 'open');
  return (
    <section className="panel">
      <div className="panel-title">
        <h2>Open Position</h2>
      </div>
      {open.length === 0 && <p className="muted-copy">No open bot position.</p>}
      {open.map((position) => {
        const pnl = (position.current_price - position.avg_entry_price) * position.qty;
        return (
          <div className="position-row" key={position.id}>
            <div>
              <strong>{position.ticker}</strong>
              <span>
                {position.qty} shares at ${position.avg_entry_price.toFixed(2)}
              </span>
            </div>
            <div>
              <span>P&L</span>
              <strong className={pnl >= 0 ? 'gain' : 'loss'}>${pnl.toFixed(2)}</strong>
            </div>
            <div>
              <span>Stop</span>
              <strong>{position.stop_loss ? `$${position.stop_loss.toFixed(2)}` : '-'}</strong>
            </div>
            <div>
              <span>Target</span>
              <strong>{position.take_profit ? `$${position.take_profit.toFixed(2)}` : '-'}</strong>
            </div>
            <button className="danger compact" onClick={() => onClose(position.id)} type="button" title="Close position">
              <XCircle size={18} /> Close
            </button>
          </div>
        );
      })}
    </section>
  );
}
