from datetime import datetime

from pydantic import BaseModel


class OrderResponse(BaseModel):
    id: str
    session_id: str | None
    ticker: str
    side: str
    order_type: str
    qty: int
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    status: str
    broker_order_id: str | None
    reason: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
