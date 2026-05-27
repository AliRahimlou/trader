from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Mode = Literal["paper", "live"]
RiskLevel = Literal["conservative", "balanced", "aggressive"]
StrategyPreset = Literal[
    "trend_momentum",
    "mean_reversion",
    "breakout",
    "atlas_multi_agent",
    "auto_strategy",
]


class StartBotRequest(BaseModel):
    mode: Mode = "paper"
    ticker: str | None = None
    auto_pick: bool = False
    capital: float = Field(gt=0)
    risk_level: RiskLevel = "balanced"
    strategy: StrategyPreset = "auto_strategy"
    live_confirmation: str | None = None

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str | None) -> str | None:
        if not value:
            return None
        return value.strip().upper()


class BotSessionResponse(BaseModel):
    id: str
    mode: str
    ticker: str | None
    auto_pick: bool
    capital: float
    risk_level: str
    strategy: str
    status: str
    paused: bool
    stop_new_trades: bool
    emergency_stopped: bool
    current_ticker: str | None
    last_signal: str
    last_reason: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CandidateResponse(BaseModel):
    id: int | None = None
    ticker: str
    direction: str
    confidence: float
    strategy_score: float
    risk_score: float
    final_score: float
    entry_trigger: float | None
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None = None
    invalidation_condition: str
    expected_risk_reward: float
    suggested_position_size: int
    reason: str
    allowed: bool
    rejection_reason: str | None = None
    review: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    id: int
    event_type: str
    message: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class StatusResponse(BaseModel):
    session: BotSessionResponse
    candidates: list[CandidateResponse] = Field(default_factory=list)
    audit_log: list[AuditLogResponse] = Field(default_factory=list)
