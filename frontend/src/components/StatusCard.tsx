import { Activity, CircleAlert, CircleCheck, Clock3 } from 'lucide-react';

import type { BotSession } from '../types/trading';

interface Props {
  session?: BotSession | null;
}

const statusIcon = (status: string) => {
  if (status === 'ERROR') return <CircleAlert size={20} />;
  if (status === 'IN POSITION') return <CircleCheck size={20} />;
  if (status === 'SCANNING' || status === 'EXITING') return <Activity size={20} />;
  return <Clock3 size={20} />;
};

export function StatusCard({ session }: Props) {
  const status = session?.status || 'OFF';
  return (
    <section className="panel status-panel" aria-label="Bot status">
      <div>
        <div className={`status-badge status-${status.toLowerCase().replaceAll(' ', '-')}`}>
          {statusIcon(status)}
          <span>{status}</span>
        </div>
        <h1>Trading Bot</h1>
        <p>{session?.last_reason || 'Paper trading is ready. Live trading stays locked until explicitly confirmed.'}</p>
      </div>
      <div className="status-grid">
        <div>
          <span>Mode</span>
          <strong>{session?.mode?.toUpperCase() || 'PAPER'}</strong>
        </div>
        <div>
          <span>Signal</span>
          <strong>{session?.last_signal || 'WAIT'}</strong>
        </div>
        <div>
          <span>Ticker</span>
          <strong>{session?.current_ticker || session?.ticker || 'AUTO'}</strong>
        </div>
        <div>
          <span>Capital</span>
          <strong>{session ? `$${session.capital.toLocaleString()}` : '$0'}</strong>
        </div>
      </div>
    </section>
  );
}
