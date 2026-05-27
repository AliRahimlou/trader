import type { BacktestResult, BrokerStatus, Candidate, Order, Position, StartBotPayload, StatusResponse } from '../types/trading';

const API_BASE = import.meta.env.VITE_TRADER_API_BASE_URL || 'http://127.0.0.1:8100';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  startBot: (payload: StartBotPayload) =>
    request<StatusResponse>('/bot/start', { method: 'POST', body: JSON.stringify(payload) }),
  pauseBot: (sessionId: string) => request<StatusResponse>(`/bot/${sessionId}/pause`, { method: 'POST' }),
  resumeBot: (sessionId: string) => request<StatusResponse>(`/bot/${sessionId}/resume`, { method: 'POST' }),
  stopBot: (sessionId: string) => request<StatusResponse>(`/bot/${sessionId}/stop`, { method: 'POST' }),
  emergencyStop: (sessionId: string) =>
    request<StatusResponse>(`/bot/${sessionId}/emergency-stop`, { method: 'POST' }),
  getStatus: (sessionId: string) => request<StatusResponse>(`/bot/${sessionId}/status`),
  getRankings: (query: { ticker?: string; auto_pick: boolean; strategy: string; risk_level: string; capital: number }) => {
    const params = new URLSearchParams({
      auto_pick: String(query.auto_pick),
      strategy: query.strategy,
      risk_level: query.risk_level,
      capital: String(query.capital),
    });
    if (query.ticker) params.set('ticker', query.ticker);
    return request<Candidate[]>(`/market/rankings?${params.toString()}`);
  },
  getPositions: () => request<Position[]>('/positions'),
  closePosition: (positionId: number) => request<Order>(`/positions/${positionId}/close`, { method: 'POST' }),
  getOrders: () => request<Order[]>('/orders'),
  getBrokerStatus: () => request<BrokerStatus>('/broker/status'),
  getSettings: () => request<Record<string, unknown>>('/settings'),
  saveSettings: (payload: Record<string, unknown>) =>
    request<Record<string, unknown>>('/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  runBacktest: (payload: { ticker?: string | null; auto_pick: boolean; strategy: string; risk_level: string; capital: number }) =>
    request<BacktestResult>('/backtest/run', { method: 'POST', body: JSON.stringify(payload) }),
};
