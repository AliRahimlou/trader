from backend.app.broker.base import AccountState, BrokerOrderResult, OrderIntent
from backend.app.core.config import get_settings
from backend.app.db import models


class LiveBrokerPlaceholder:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _raise_gate(self) -> None:
        raise RuntimeError(
            "Live trading adapter is intentionally disabled. Configure a real broker adapter, credentials, "
            "live mode, and confirmation gates before enabling live execution."
        )

    def get_account(self) -> AccountState:
        self._raise_gate()

    def submit_order(self, intent: OrderIntent) -> BrokerOrderResult:
        self._raise_gate()

    def cancel_all_orders(self, session_id: str | None = None) -> int:
        self._raise_gate()

    def close_position(self, position: models.Position, reason: str) -> BrokerOrderResult:
        self._raise_gate()
