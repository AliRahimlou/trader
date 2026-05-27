export type TradingMode = 'paper' | 'live';
export type RiskLevel = 'conservative' | 'balanced' | 'aggressive';
export type StrategyPreset =
  | 'trend_momentum'
  | 'mean_reversion'
  | 'breakout'
  | 'atlas_multi_agent'
  | 'auto_strategy';

export interface StartBotPayload {
  mode: TradingMode;
  ticker?: string | null;
  auto_pick: boolean;
  capital: number;
  risk_level: RiskLevel;
  strategy: StrategyPreset;
  live_confirmation?: string | null;
}

export interface BotSession {
  id: string;
  mode: TradingMode;
  ticker: string | null;
  auto_pick: boolean;
  capital: number;
  risk_level: RiskLevel;
  strategy: StrategyPreset;
  status: string;
  paused: boolean;
  stop_new_trades: boolean;
  emergency_stopped: boolean;
  current_ticker: string | null;
  last_signal: string;
  last_reason: string;
  created_at: string;
  updated_at: string;
}

export interface Candidate {
  id?: number | null;
  ticker: string;
  direction: string;
  confidence: number;
  strategy_score: number;
  risk_score: number;
  final_score: number;
  entry_trigger: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  trailing_stop: number | null;
  invalidation_condition: string;
  expected_risk_reward: number;
  suggested_position_size: number;
  reason: string;
  allowed: boolean;
  rejection_reason: string | null;
  review: Record<string, unknown>;
}

export interface AuditLog {
  id: number;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface StatusResponse {
  session: BotSession;
  candidates: Candidate[];
  audit_log: AuditLog[];
}

export interface Position {
  id: number;
  session_id: string | null;
  ticker: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  stop_loss: number | null;
  take_profit: number | null;
  trailing_stop: number | null;
  status: string;
  opened_at: string;
  closed_at: string | null;
}

export interface Order {
  id: string;
  session_id: string | null;
  ticker: string;
  side: string;
  order_type: string;
  qty: number;
  limit_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  trailing_stop: number | null;
  status: string;
  broker_order_id: string | null;
  reason: string;
  created_at: string;
  updated_at: string;
}

export interface BacktestResult {
  id: string;
  metrics: Record<string, number>;
  equity_curve: Array<{ timestamp: string; equity: number; return: number; in_position: boolean }>;
  trades: Array<Record<string, unknown>>;
}

export interface BrokerStatus {
  provider: string;
  connected: boolean;
  mode: string;
  equity: number;
  cash: number;
  buying_power: number;
}
