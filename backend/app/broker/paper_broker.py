from uuid import uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.app.broker.base import AccountState, BrokerOrderResult, OrderIntent
from backend.app.core.config import get_settings
from backend.app.db import models
from backend.app.market_data.base import MarketDataProvider


class PaperBroker:
    def __init__(self, db: Session, market_data: MarketDataProvider) -> None:
        self.db = db
        self.market_data = market_data
        self.settings = get_settings()

    def _latest_cash(self) -> float:
        snapshot = self.db.execute(
            select(models.BrokerAccountSnapshot).order_by(desc(models.BrokerAccountSnapshot.created_at)).limit(1)
        ).scalar_one_or_none()
        return snapshot.cash if snapshot else self.settings.default_starting_cash

    def get_account(self) -> AccountState:
        cash = self._latest_cash()
        positions = self.db.execute(select(models.Position).where(models.Position.status == "open")).scalars().all()
        position_value = 0.0
        for position in positions:
            quote = self.market_data.get_latest_quote(position.ticker)
            position.current_price = quote.last
            position_value += position.qty * quote.last
        equity = cash + position_value
        return AccountState(mode="paper", equity=round(equity, 2), cash=round(cash, 2), buying_power=round(cash, 2))

    def submit_order(self, intent: OrderIntent) -> BrokerOrderResult:
        if intent.qty <= 0:
            return BrokerOrderResult(False, None, "rejected", rejection_reason="zero-share orders are rejected")

        quote = self.market_data.get_latest_quote(intent.ticker)
        side = intent.side.lower()
        if side not in {"buy", "sell"}:
            return BrokerOrderResult(False, None, "rejected", rejection_reason="unsupported side")

        if intent.order_type == "limit":
            if side == "buy" and (intent.limit_price is None or intent.limit_price < quote.ask):
                return BrokerOrderResult(True, f"paper-{uuid4()}", "open")
            if side == "sell" and (intent.limit_price is None or intent.limit_price > quote.bid):
                return BrokerOrderResult(True, f"paper-{uuid4()}", "open")

        fill_price = quote.ask if side == "buy" else quote.bid
        if intent.order_type == "market":
            fill_price = quote.ask if side == "buy" else quote.bid
        elif intent.limit_price is not None:
            fill_price = min(intent.limit_price, quote.ask) if side == "buy" else max(intent.limit_price, quote.bid)

        if side == "buy":
            account = self.get_account()
            if account.buying_power < fill_price * intent.qty:
                return BrokerOrderResult(False, None, "rejected", rejection_reason="insufficient buying power")

        if side == "sell":
            position = self.db.execute(
                select(models.Position)
                .where(models.Position.ticker == intent.ticker, models.Position.status == "open")
                .order_by(desc(models.Position.opened_at))
            ).scalar_one_or_none()
            if position is None or position.qty < intent.qty:
                return BrokerOrderResult(False, None, "rejected", rejection_reason="no matching open paper position")

        return BrokerOrderResult(
            accepted=True,
            broker_order_id=f"paper-{uuid4()}",
            status="filled",
            filled_qty=int(intent.qty),
            fill_price=round(fill_price, 2),
        )

    def cancel_all_orders(self, session_id: str | None = None) -> int:
        query = select(models.Order).where(models.Order.status.in_(["intent", "open", "accepted"]))
        if session_id:
            query = query.where(models.Order.session_id == session_id)
        orders = self.db.execute(query).scalars().all()
        for order in orders:
            order.status = "cancelled"
        return len(orders)

    def close_position(self, position: models.Position, reason: str) -> BrokerOrderResult:
        intent = OrderIntent(
            session_id=position.session_id,
            ticker=position.ticker,
            side="sell",
            qty=position.qty,
            order_type="market",
            limit_price=None,
            stop_loss=None,
            take_profit=None,
            trailing_stop=None,
            idempotency_key=f"close-{position.id}-{uuid4()}",
            reason=reason,
        )
        return self.submit_order(intent)
