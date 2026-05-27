import { ShieldAlert, Wallet } from 'lucide-react';

import type { BotSession, BrokerStatus, Candidate } from '../types/trading';

interface Props {
  session?: BotSession | null;
  bestCandidate?: Candidate;
  account?: BrokerStatus | null;
}

export function RiskControls({ session, bestCandidate, account }: Props) {
  return (
    <section className="panel risk-panel">
      <div className="panel-title">
        <ShieldAlert size={20} />
        <h2>Risk Controls</h2>
      </div>
      <div className="risk-grid">
        <div>
          <span>Risk Level</span>
          <strong>{session?.risk_level || 'balanced'}</strong>
        </div>
        <div>
          <span>Stop Loss</span>
          <strong>{bestCandidate?.stop_loss ? `$${bestCandidate.stop_loss.toFixed(2)}` : '-'}</strong>
        </div>
        <div>
          <span>Take Profit</span>
          <strong>{bestCandidate?.take_profit ? `$${bestCandidate.take_profit.toFixed(2)}` : '-'}</strong>
        </div>
        <div>
          <span>Risk/Reward</span>
          <strong>{bestCandidate?.expected_risk_reward ?? '-'}</strong>
        </div>
        <div>
          <span>Position Size</span>
          <strong>{bestCandidate?.suggested_position_size ?? '-'}</strong>
        </div>
        <div>
          <span>Approval</span>
          <strong>{bestCandidate?.allowed ? 'Allowed' : 'Blocked/Waiting'}</strong>
        </div>
        <div>
          <span>Cash</span>
          <strong>{account ? `$${account.cash.toLocaleString()}` : '-'}</strong>
        </div>
        <div>
          <span>Buying Power</span>
          <strong>{account ? `$${account.buying_power.toLocaleString()}` : '-'}</strong>
        </div>
      </div>
      <div className="cash-strip">
        <Wallet size={18} />
        <span>{session ? `$${session.capital.toLocaleString()} allocated` : 'No active allocation'}</span>
      </div>
    </section>
  );
}
