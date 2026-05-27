import { useEffect, useState } from 'react';

import { api } from '../api/client';
import { AuditTimeline } from '../components/AuditTimeline';
import { BacktestPanel } from '../components/BacktestPanel';
import { OpenPositionCard } from '../components/OpenPositionCard';
import { OrdersTable } from '../components/OrdersTable';
import { RankedStocksTable } from '../components/RankedStocksTable';
import { RiskControls } from '../components/RiskControls';
import { SettingsPanel } from '../components/SettingsPanel';
import { StatusCard } from '../components/StatusCard';
import { TradeSetupForm } from '../components/TradeSetupForm';
import type { BacktestResult, BrokerStatus, Candidate, Order, Position, StartBotPayload, StatusResponse } from '../types/trading';

const defaultSetup: StartBotPayload = {
  mode: 'paper',
  ticker: null,
  auto_pick: true,
  capital: 10000,
  risk_level: 'balanced',
  strategy: 'auto_strategy',
  live_confirmation: null,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

export function DashboardPage() {
  const [setup, setSetup] = useState<StartBotPayload>(defaultSetup);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [account, setAccount] = useState<BrokerStatus | null>(null);
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [backtest, setBacktest] = useState<BacktestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAccountViews = async () => {
    const [nextPositions, nextOrders, nextAccount] = await Promise.all([api.getPositions(), api.getOrders(), api.getBrokerStatus()]);
    setPositions(nextPositions);
    setOrders(nextOrders);
    setAccount(nextAccount);
  };

  useEffect(() => {
    api.getSettings().then(setSettings).catch(() => undefined);
    refreshAccountViews().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!status?.session.id) return;
    const id = window.setInterval(() => {
      api
        .getStatus(status.session.id)
        .then((next) => {
          setStatus(next);
          setCandidates(next.candidates);
          return refreshAccountViews();
        })
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(id);
  }, [status?.session.id]);

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const handleStart = (payload: StartBotPayload) =>
    runAction(async () => {
      const next = await api.startBot(payload);
      setStatus(next);
      setCandidates(next.candidates);
      await refreshAccountViews();
    });

  const handlePreview = (payload: StartBotPayload) =>
    runAction(async () => {
      const next = await api.getRankings({
        ticker: payload.ticker || undefined,
        auto_pick: payload.auto_pick,
        strategy: payload.strategy,
        risk_level: payload.risk_level,
        capital: payload.capital,
      });
      setCandidates(next);
    });

  const handleSessionAction = (fn: (sessionId: string) => Promise<StatusResponse>) =>
    runAction(async () => {
      if (!status?.session.id) return;
      const next = await fn(status.session.id);
      setStatus(next);
      setCandidates(next.candidates);
      await refreshAccountViews();
    });

  const handleEmergency = () => {
    if (!window.confirm('Emergency stop cancels open orders and disables new trades. Continue?')) return;
    handleSessionAction(api.emergencyStop);
  };

  const handleClosePosition = (positionId: number) =>
    runAction(async () => {
      await api.closePosition(positionId);
      await refreshAccountViews();
      if (status?.session.id) setStatus(await api.getStatus(status.session.id));
    });

  const handleBacktest = () =>
    runAction(async () => {
      const result = await api.runBacktest(setup);
      setBacktest(result);
    });

  const handleSaveSettings = (payload: Record<string, unknown>) =>
    runAction(async () => {
      const result = await api.saveSettings(payload);
      setSettings(result);
    });

  return (
    <main className="app-shell">
      <StatusCard session={status?.session} />
      {error && <div className="error-banner">{error}</div>}
      <div className="workspace-grid">
        <TradeSetupForm
          session={status?.session}
          busy={busy}
          onStart={handleStart}
          onPreview={handlePreview}
          onPause={() => handleSessionAction(api.pauseBot)}
          onResume={() => handleSessionAction(api.resumeBot)}
          onStop={() => handleSessionAction(api.stopBot)}
          onEmergency={handleEmergency}
          onChange={setSetup}
        />
        <RiskControls session={status?.session} bestCandidate={candidates[0]} account={account} />
      </div>
      <RankedStocksTable candidates={candidates} />
      <div className="workspace-grid two">
        <OpenPositionCard positions={positions} onClose={handleClosePosition} />
        <AuditTimeline items={status?.audit_log || []} />
      </div>
      <OrdersTable orders={orders} />
      <div className="workspace-grid two">
        <BacktestPanel setup={setup} result={backtest} busy={busy} onRun={handleBacktest} />
        <SettingsPanel settings={settings} onSave={handleSaveSettings} />
      </div>
    </main>
  );
}
