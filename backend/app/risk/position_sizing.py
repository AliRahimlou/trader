from dataclasses import dataclass
from math import floor


RISK_PROFILES: dict[str, dict[str, float]] = {
    "conservative": {"max_trade_risk_pct": 0.01, "max_daily_loss_pct": 0.02, "max_position_pct": 0.25},
    "balanced": {"max_trade_risk_pct": 0.015, "max_daily_loss_pct": 0.03, "max_position_pct": 0.35},
    "aggressive": {"max_trade_risk_pct": 0.02, "max_daily_loss_pct": 0.05, "max_position_pct": 0.50},
}


@dataclass(frozen=True)
class PositionSizeResult:
    shares: int
    notional: float
    risk_dollars: float
    reason: str


def profile_for(risk_level: str) -> dict[str, float]:
    return RISK_PROFILES.get(risk_level, RISK_PROFILES["balanced"])


def calculate_position_size(
    *,
    entry_price: float,
    stop_loss: float,
    capital_allocation: float,
    account_equity: float,
    buying_power: float,
    risk_level: str,
) -> PositionSizeResult:
    profile = profile_for(risk_level)
    per_share_risk = max(0.0, entry_price - stop_loss)
    if entry_price <= 0 or stop_loss <= 0 or per_share_risk <= 0:
        return PositionSizeResult(0, 0.0, 0.0, "Invalid entry/stop geometry.")

    risk_budget = account_equity * profile["max_trade_risk_pct"]
    max_position_notional = account_equity * profile["max_position_pct"]
    allocation_cap = min(capital_allocation, buying_power, max_position_notional)
    shares_by_risk = floor(risk_budget / per_share_risk)
    shares_by_cash = floor(allocation_cap / entry_price)
    shares = max(0, min(shares_by_risk, shares_by_cash))
    notional = shares * entry_price
    risk_dollars = shares * per_share_risk
    if shares <= 0:
        return PositionSizeResult(0, 0.0, 0.0, "Sizing rejected: allocation, risk budget, or buying power is too small.")
    return PositionSizeResult(
        shares=shares,
        notional=round(notional, 2),
        risk_dollars=round(risk_dollars, 2),
        reason=f"Sized by min(risk budget ${risk_budget:.2f}, allocation ${allocation_cap:.2f}).",
    )
