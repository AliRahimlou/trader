from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SignalResponse(BaseModel):
    id: int
    session_id: str | None
    ticker: str
    strategy: str
    direction: str
    confidence: float
    score: float
    reason: str
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
