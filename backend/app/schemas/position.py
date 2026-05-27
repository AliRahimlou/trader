from datetime import datetime

from pydantic import BaseModel


class PositionResponse(BaseModel):
    id: int
    session_id: str | None
    ticker: str
    qty: int
    avg_entry_price: float
    current_price: float
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    status: str
    opened_at: datetime
    closed_at: datetime | None

    model_config = {"from_attributes": True}
