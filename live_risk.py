from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from live_scheduler import to_et_timestamp
from live_state import RunnerState
from strategy_signals import StrategySignal


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    approved_qty: float
    approved_notional: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    symbol: str
    current_positions: dict[str, dict[str, Any]]
    current_active_trades: dict[str, dict[str, Any]]
    max_concurrent_positions: int
    max_capital_deployed: float
    max_capital_per_symbol: float
    candidate_score: float
    top_candidate_score: float
    correlation_to_open_positions: float | None = None
    correlation_threshold: float = 0.0
    allocation_floor_fraction: float = 0.4


def evaluate_entry_risk(
    signal: StrategySignal,
    *,
    state: RunnerState,
    account: dict[str, Any],
    asset: dict[str, Any],
    now: pd.Timestamp,
    portfolio: PortfolioRiskSnapshot,
    max_position_qty: float,
    max_position_notional: float,
    max_daily_loss: float,
    max_trades_per_day: int,
    cooldown_minutes: int,
    one_position_per_symbol: bool,
    exit_mode: str,
    allow_fractional_long: bool,
) -> RiskDecision:
    reasons: list[str] = []
    approved_qty = float(signal.quantity)
    day_key = str(to_et_timestamp(now).date())
    realized_pnl = state.daily_realized_pnl.get(day_key, 0.0)
    trade_count = state.daily_trade_count.get(day_key, 0)
    symbol = portfolio.symbol.upper()
    current_position = portfolio.current_positions.get(symbol)
    current_trade = portfolio.current_active_trades.get(symbol)

    if not asset.get("tradable"):
        reasons.append("asset_not_tradable")

    if signal.direction == "short" and not asset.get("shortable"):
        reasons.append("asset_not_shortable")

    if one_position_per_symbol and (current_trade is not None or current_position is not None):
        reasons.append("one_position_per_symbol")

    active_trade_count = len(portfolio.current_active_trades)
    if portfolio.max_concurrent_positions > 0 and symbol not in portfolio.current_active_trades and active_trade_count >= portfolio.max_concurrent_positions:
        reasons.append("max_concurrent_positions_reached")

    if max_daily_loss > 0 and realized_pnl <= -abs(max_daily_loss):
        reasons.append("max_daily_loss_reached")

    if max_trades_per_day > 0 and trade_count >= max_trades_per_day:
        reasons.append("max_trades_per_day_reached")

    if cooldown_minutes > 0 and state.get_last_exit_at(symbol):
        last_exit_at = to_et_timestamp(state.get_last_exit_at(symbol))
        cooldown_until = last_exit_at + pd.Timedelta(minutes=cooldown_minutes)
        if to_et_timestamp(now) < cooldown_until:
            reasons.append("cooldown_active")

    if max_position_qty > 0:
        approved_qty = min(approved_qty, max_position_qty)

    if max_position_notional > 0 and signal.entry_reference_price > 0:
        max_qty_for_notional = max_position_notional / signal.entry_reference_price
        approved_qty = min(approved_qty, max_qty_for_notional)

    deployed_capital = 0.0
    for position in portfolio.current_positions.values():
        market_value = float(position.get("market_value") or 0.0)
        if market_value <= 0 and position.get("qty") and position.get("avg_entry_price"):
            market_value = abs(float(position["qty"]) * float(position["avg_entry_price"]))
        deployed_capital += abs(market_value)
    symbol_deployed_capital = 0.0
    if current_position is not None:
        symbol_deployed_capital = abs(float(current_position.get("market_value") or 0.0))

    capital_cap = float("inf")
    if portfolio.max_capital_deployed > 0:
        remaining_portfolio_capital = portfolio.max_capital_deployed - deployed_capital
        if remaining_portfolio_capital <= 0:
            reasons.append("max_capital_deployed_reached")
        capital_cap = min(capital_cap, remaining_portfolio_capital)
    if portfolio.max_capital_per_symbol > 0:
        remaining_symbol_capital = portfolio.max_capital_per_symbol - symbol_deployed_capital
        if remaining_symbol_capital <= 0:
            reasons.append("max_capital_per_symbol_reached")
        capital_cap = min(capital_cap, remaining_symbol_capital)

    if signal.direction == "short":
        approved_qty = float(int(approved_qty))

    if exit_mode == "bracket":
        approved_qty = float(int(approved_qty))

    if signal.direction == "long" and (not allow_fractional_long or not asset.get("fractionable")):
        approved_qty = float(int(approved_qty))

    if (
        portfolio.correlation_to_open_positions is not None
        and portfolio.correlation_threshold > 0
        and portfolio.correlation_to_open_positions >= portfolio.correlation_threshold
    ):
        reasons.append("correlated_exposure")

    buying_power = float(account.get("buying_power", 0) or 0)
    capital_cap = min(capital_cap, buying_power)
    if capital_cap != float("inf") and signal.entry_reference_price > 0:
        score_ratio = 1.0
        if portfolio.top_candidate_score > 0:
            score_ratio = min(max(portfolio.candidate_score / portfolio.top_candidate_score, 0.0), 1.0)
        allocation_fraction = portfolio.allocation_floor_fraction + ((1.0 - portfolio.allocation_floor_fraction) * score_ratio)
        capital_qty_cap = (capital_cap * allocation_fraction) / signal.entry_reference_price
        approved_qty = min(approved_qty, capital_qty_cap)

    estimated_notional = approved_qty * signal.entry_reference_price
    if estimated_notional > buying_power:
        reasons.append("insufficient_buying_power")

    if approved_qty <= 0:
        reasons.append("quantity_below_minimum")

    return RiskDecision(
        allowed=not reasons,
        approved_qty=approved_qty,
        approved_notional=max(0.0, approved_qty * signal.entry_reference_price),
        reasons=tuple(reasons),
    )
