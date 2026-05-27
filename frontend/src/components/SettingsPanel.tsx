import { Settings } from 'lucide-react';
import { useState } from 'react';

interface Props {
  settings?: Record<string, unknown> | null;
  onSave: (payload: Record<string, unknown>) => void;
}

export function SettingsPanel({ settings, onSave }: Props) {
  const [maxDailyLoss, setMaxDailyLoss] = useState('3');
  const [maxTradeRisk, setMaxTradeRisk] = useState('1.5');
  const [maxOpenPositions, setMaxOpenPositions] = useState('2');
  const [brokerKey, setBrokerKey] = useState('');
  const [brokerSecret, setBrokerSecret] = useState('');
  const [dataKey, setDataKey] = useState('');
  const [mode, setMode] = useState('paper');
  const [tradingHours, setTradingHours] = useState('09:30-16:00 ET');
  const [notifications, setNotifications] = useState('email:false,sms:false');

  return (
    <section className="panel settings-panel">
      <div className="panel-title">
        <Settings size={20} />
        <h2>Settings</h2>
      </div>
      <div className="settings-grid">
        <label>
          Broker Key ID
          <input value={brokerKey} onChange={(event) => setBrokerKey(event.target.value)} />
        </label>
        <label>
          Broker Secret
          <input type="password" value={brokerSecret} onChange={(event) => setBrokerSecret(event.target.value)} />
        </label>
        <label>
          Market Data Key
          <input value={dataKey} onChange={(event) => setDataKey(event.target.value)} />
        </label>
        <label>
          Paper/Live Mode
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            <option value="paper">Paper</option>
            <option value="live">Live</option>
          </select>
        </label>
        <label>
          Max Daily Loss %
          <input value={maxDailyLoss} onChange={(event) => setMaxDailyLoss(event.target.value)} />
        </label>
        <label>
          Max Trade Risk %
          <input value={maxTradeRisk} onChange={(event) => setMaxTradeRisk(event.target.value)} />
        </label>
        <label>
          Max Open Positions
          <input value={maxOpenPositions} onChange={(event) => setMaxOpenPositions(event.target.value)} />
        </label>
        <label>
          Trading Hours
          <input value={tradingHours} onChange={(event) => setTradingHours(event.target.value)} />
        </label>
        <label>
          Notifications
          <input value={notifications} onChange={(event) => setNotifications(event.target.value)} />
        </label>
      </div>
      <div className="settings-footer">
        <span>Live enabled: {String(settings?.live_trading_enabled ?? false)}</span>
        <button
          onClick={() =>
            onSave({
              max_daily_loss: Number(maxDailyLoss),
              max_trade_risk: Number(maxTradeRisk),
              max_open_positions: Number(maxOpenPositions),
              broker_key_id: brokerKey,
              broker_secret: brokerSecret,
              market_data_key: dataKey,
              paper_live_mode: mode,
              trading_hours: tradingHours,
              notifications: { raw: notifications },
            })
          }
          type="button"
        >
          Save
        </button>
      </div>
    </section>
  );
}
