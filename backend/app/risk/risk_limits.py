from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.broker.base import AccountState
from backend.app.market_data.base import Quote
from backend.app.risk.position_sizing import PositionSizeResult, profile_for


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: list[str]


def validate_entry(
    *,
    quote: Quote,
    account: AccountState,
    size: PositionSizeResult,
    stop_loss: float | None,
    expected_risk_reward: float,
    open_positions: int,
    risk_level: str,
    max_open_positions: int,
    max_spread_bps: float,
    max_quote_age_seconds: int,
    realized_daily_pnl: float = 0.0,
) -> RiskDecision:
    reasons: list[str] = []
    profile = profile_for(risk_level)
    age = (datetime.now(timezone.utc) - quote.timestamp).total_seconds()
    if stop_loss is None:
        reasons.append("No trade without a stop loss.")
    if size.shares <= 0:
        reasons.append(size.reason)
    if size.notional > account.buying_power:
        reasons.append("Notional exceeds buying power.")
    if open_positions >= max_open_positions:
        reasons.append("Max open positions reached.")
    if quote.spread_bps > max_spread_bps:
        reasons.append("Quote spread is too wide.")
    if age > max_quote_age_seconds:
        reasons.append("Quote is stale.")
    if expected_risk_reward < 1.2:
        reasons.append("Expected risk/reward is below threshold.")
    if realized_daily_pnl < -(account.equity * profile["max_daily_loss_pct"]):
        reasons.append("Max daily loss reached; new trades are stopped.")
    return RiskDecision(not reasons, reasons)
