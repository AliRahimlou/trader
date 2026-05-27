from dataclasses import dataclass
from typing import Protocol

from backend.app.db import models


@dataclass(frozen=True)
class AccountState:
    mode: str
    equity: float
    cash: float
    buying_power: float


@dataclass(frozen=True)
class OrderIntent:
    session_id: str | None
    ticker: str
    side: str
    qty: int
    order_type: str
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    idempotency_key: str
    reason: str


@dataclass(frozen=True)
class BrokerOrderResult:
    accepted: bool
    broker_order_id: str | None
    status: str
    filled_qty: int = 0
    fill_price: float | None = None
    rejection_reason: str | None = None


class Broker(Protocol):
    def get_account(self) -> AccountState:
        ...

    def submit_order(self, intent: OrderIntent) -> BrokerOrderResult:
        ...

    def cancel_all_orders(self, session_id: str | None = None) -> int:
        ...

    def close_position(self, position: models.Position, reason: str) -> BrokerOrderResult:
        ...
