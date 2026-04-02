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
    reasons: tuple[str, ...]


def evaluate_entry_risk(
    signal: StrategySignal,
    *,
    state: RunnerState,
    account: dict[str, Any],
    asset: dict[str, Any],
    now: pd.Timestamp,
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

    if not asset.get("tradable"):
        reasons.append("asset_not_tradable")

    if signal.direction == "short" and not asset.get("shortable"):
        reasons.append("asset_not_shortable")

    if one_position_per_symbol and state.active_trade is not None:
        reasons.append("one_position_per_symbol")

    if max_daily_loss > 0 and realized_pnl <= -abs(max_daily_loss):
        reasons.append("max_daily_loss_reached")

    if max_trades_per_day > 0 and trade_count >= max_trades_per_day:
        reasons.append("max_trades_per_day_reached")

    if cooldown_minutes > 0 and state.last_exit_at:
        last_exit_at = to_et_timestamp(state.last_exit_at)
        cooldown_until = last_exit_at + pd.Timedelta(minutes=cooldown_minutes)
        if to_et_timestamp(now) < cooldown_until:
            reasons.append("cooldown_active")

    if max_position_qty > 0:
        approved_qty = min(approved_qty, max_position_qty)

    if max_position_notional > 0 and signal.entry_reference_price > 0:
        max_qty_for_notional = max_position_notional / signal.entry_reference_price
        approved_qty = min(approved_qty, max_qty_for_notional)

    if signal.direction == "short":
        approved_qty = float(int(approved_qty))

    if exit_mode == "bracket":
        approved_qty = float(int(approved_qty))

    if signal.direction == "long" and (not allow_fractional_long or not asset.get("fractionable")):
        approved_qty = float(int(approved_qty))

    buying_power = float(account.get("buying_power", 0) or 0)
    estimated_notional = approved_qty * signal.entry_reference_price
    if estimated_notional > buying_power:
        reasons.append("insufficient_buying_power")

    if approved_qty <= 0:
        reasons.append("quantity_below_minimum")

    return RiskDecision(allowed=not reasons, approved_qty=approved_qty, reasons=tuple(reasons))
