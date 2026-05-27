import { BarChart3, Play } from 'lucide-react';

import type { BacktestResult, StartBotPayload } from '../types/trading';

interface Props {
  setup: StartBotPayload;
  result?: BacktestResult | null;
  busy: boolean;
  onRun: () => void;
}

const metricLabels: Record<string, string> = {
  total_return: 'Total Return',
  max_drawdown: 'Max Drawdown',
  win_rate: 'Win Rate',
  profit_factor: 'Profit Factor',
  sharpe: 'Sharpe',
  number_of_trades: 'Trades',
  average_trade: 'Avg Trade',
  exposure_time: 'Exposure',
};

export function BacktestPanel({ setup, result, busy, onRun }: Props) {
  return (
    <section className="panel backtest-panel">
      <div className="panel-title">
        <BarChart3 size={20} />
        <h2>Backtest</h2>
        <button disabled={busy} onClick={onRun} type="button" title="Run backtest">
          <Play size={18} /> Run
        </button>
      </div>
      <div className="backtest-meta">
        <span>{setup.auto_pick ? 'Auto-Pick' : setup.ticker || 'Ticker'}</span>
        <span>{setup.strategy.replaceAll('_', ' ')}</span>
        <span>${setup.capital.toLocaleString()}</span>
      </div>
      <div className="metrics-grid">
        {Object.entries(metricLabels).map(([key, label]) => (
          <div key={key}>
            <span>{label}</span>
            <strong>{result?.metrics?.[key] ?? '-'}</strong>
          </div>
        ))}
      </div>
      <div className="equity-strip">
        {(result?.equity_curve || []).slice(-42).map((point) => (
          <span
            key={point.timestamp}
            style={{ height: `${Math.max(8, Math.min(64, (point.equity / setup.capital) * 36))}px` }}
            title={`$${point.equity}`}
          />
        ))}
      </div>
    </section>
  );
}
