from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class CROReview:
    allowed: bool
    score: float
    flags: list[str]
    reason: str


class CROAgent:
    def review(
        self,
        *,
        direction: str,
        entry: float | None,
        stop: float | None,
        take_profit: float | None,
        expected_risk_reward: float,
        spread_bps: float,
        max_spread_bps: float,
        quote_timestamp: datetime,
        max_quote_age_seconds: int,
        suggested_size: int,
    ) -> CROReview:
        flags: list[str] = []
        now = datetime.now(timezone.utc)
        quote_age = max(0.0, (now - quote_timestamp).total_seconds())
        if direction != "BUY":
            flags.append("strategy_not_buy")
        if entry is None or stop is None:
            flags.append("missing_stop_or_entry")
        if take_profit is None:
            flags.append("missing_take_profit")
        if expected_risk_reward < 1.2:
            flags.append("weak_risk_reward")
        if spread_bps > max_spread_bps:
            flags.append("wide_spread")
        if quote_age > max_quote_age_seconds:
            flags.append("stale_quote")
        if suggested_size <= 0:
            flags.append("zero_share_order")

        allowed = not flags
        score = max(0.0, 1.0 - len(flags) * 0.18)
        reason = "CRO approved the setup." if allowed else f"CRO blocked or downgraded: {', '.join(flags)}."
        return CROReview(allowed, score, flags, reason)
