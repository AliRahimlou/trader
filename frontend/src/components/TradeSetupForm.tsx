import { Pause, Play, RefreshCw, Search, ShieldAlert, Square } from 'lucide-react';
import { useEffect, useState } from 'react';

import type { BotSession, RiskLevel, StartBotPayload, StrategyPreset, TradingMode } from '../types/trading';

interface Props {
  session?: BotSession | null;
  busy: boolean;
  onStart: (payload: StartBotPayload) => void;
  onPreview: (payload: StartBotPayload) => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onEmergency: () => void;
  onChange: (payload: StartBotPayload) => void;
}

const strategies: Array<{ value: StrategyPreset; label: string }> = [
  { value: 'trend_momentum', label: 'Trend Momentum' },
  { value: 'mean_reversion', label: 'Mean Reversion' },
  { value: 'breakout', label: 'Breakout' },
  { value: 'atlas_multi_agent', label: 'Atlas Multi-Agent' },
  { value: 'auto_strategy', label: 'Auto Strategy' },
];

export function TradeSetupForm({ session, busy, onStart, onPreview, onPause, onResume, onStop, onEmergency, onChange }: Props) {
  const [mode, setMode] = useState<TradingMode>('paper');
  const [autoPick, setAutoPick] = useState(true);
  const [ticker, setTicker] = useState('AAPL');
  const [capital, setCapital] = useState(10000);
  const [riskLevel, setRiskLevel] = useState<RiskLevel>('balanced');
  const [strategy, setStrategy] = useState<StrategyPreset>('auto_strategy');
  const [confirmation, setConfirmation] = useState('');

  const payload: StartBotPayload = {
    mode,
    ticker: autoPick ? null : ticker,
    auto_pick: autoPick,
    capital,
    risk_level: riskLevel,
    strategy,
    live_confirmation: mode === 'live' ? confirmation : null,
  };

  useEffect(() => {
    onChange(payload);
  }, [mode, autoPick, ticker, capital, riskLevel, strategy, confirmation]);

  const isPaused = Boolean(session?.paused);
  const hasSession = Boolean(session?.id);

  return (
    <section className="panel setup-panel" aria-label="Trade setup">
      <div className="panel-title">
        <Search size={20} />
        <h2>Start Bot</h2>
      </div>

      <div className="field-row">
        <label>Mode</label>
        <div className="segmented">
          <button className={mode === 'paper' ? 'active' : ''} onClick={() => setMode('paper')} type="button">
            Paper Trading
          </button>
          <button className={mode === 'live' ? 'active danger-text' : ''} onClick={() => setMode('live')} type="button">
            Live Trading
          </button>
        </div>
      </div>

      <div className="field-row">
        <label>Stock</label>
        <div className="segmented">
          <button className={autoPick ? 'active' : ''} onClick={() => setAutoPick(true)} type="button">
            Auto-Pick
          </button>
          <button className={!autoPick ? 'active' : ''} onClick={() => setAutoPick(false)} type="button">
            Pick Stock
          </button>
        </div>
      </div>

      {!autoPick && (
        <div className="field-row">
          <label htmlFor="ticker">Ticker</label>
          <input id="ticker" value={ticker} onChange={(event) => setTicker(event.target.value.toUpperCase())} />
        </div>
      )}

      <div className="field-row">
        <label htmlFor="capital">Amount</label>
        <input id="capital" type="number" min={100} value={capital} onChange={(event) => setCapital(Number(event.target.value))} />
      </div>

      <div className="field-row">
        <label htmlFor="risk">Risk</label>
        <select id="risk" value={riskLevel} onChange={(event) => setRiskLevel(event.target.value as RiskLevel)}>
          <option value="conservative">Conservative</option>
          <option value="balanced">Balanced</option>
          <option value="aggressive">Aggressive</option>
        </select>
      </div>

      <div className="field-row">
        <label htmlFor="strategy">Strategy</label>
        <select id="strategy" value={strategy} onChange={(event) => setStrategy(event.target.value as StrategyPreset)}>
          {strategies.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </div>

      {mode === 'live' && (
        <div className="field-row">
          <label htmlFor="live-confirm">Confirm</label>
          <input
            id="live-confirm"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder="I understand live trading risk"
          />
        </div>
      )}

      <div className="action-grid">
        <button className="primary" disabled={busy} onClick={() => onStart(payload)} type="button" title="Start bot">
          <Play size={18} /> Start Bot
        </button>
        <button disabled={busy} onClick={() => onPreview(payload)} type="button" title="Refresh rankings">
          <RefreshCw size={18} /> Rank
        </button>
        <button disabled={!hasSession || busy} onClick={isPaused ? onResume : onPause} type="button" title={isPaused ? 'Resume' : 'Pause'}>
          {isPaused ? <Play size={18} /> : <Pause size={18} />} {isPaused ? 'Resume' : 'Pause'}
        </button>
        <button disabled={!hasSession || busy} onClick={onStop} type="button" title="Stop new trades">
          <Square size={18} /> Stop New Trades
        </button>
        <button className="danger" disabled={!hasSession || busy} onClick={onEmergency} type="button" title="Emergency stop">
          <ShieldAlert size={18} /> Emergency Stop
        </button>
      </div>
    </section>
  );
}
