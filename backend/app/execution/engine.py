from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.broker.base import OrderIntent
from backend.app.db import models
from backend.app.execution.fill_handler import apply_fill
from backend.app.execution.order_router import get_broker
from backend.app.market_data.base import MarketDataProvider


class ExecutionEngine:
    def __init__(self, db: Session, market_data: MarketDataProvider) -> None:
        self.db = db
        self.market_data = market_data

    def submit_candidate_order(self, session: models.BotSession, candidate) -> models.Order:
        order_type = "market" if session.mode == "paper" else "limit"
        order = models.Order(
            session_id=session.id,
            ticker=candidate.ticker,
            side="buy",
            order_type=order_type,
            qty=candidate.suggested_position_size,
            limit_price=candidate.entry_trigger if order_type == "limit" else None,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            trailing_stop=candidate.trailing_stop,
            status="intent",
            idempotency_key=f"{session.id}:buy:{candidate.ticker}:{uuid4()}",
            reason=candidate.reason,
        )
        self.db.add(order)
        self.db.flush()

        broker = get_broker(self.db, self.market_data, session.mode)
        intent = OrderIntent(
            session_id=session.id,
            ticker=order.ticker,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            trailing_stop=order.trailing_stop,
            idempotency_key=order.idempotency_key,
            reason=order.reason,
        )
        result = broker.submit_order(intent)
        order.broker_order_id = result.broker_order_id
        order.status = result.status
        if not result.accepted:
            order.reason = result.rejection_reason or order.reason
            self.db.add(
                models.AuditLog(
                    session_id=session.id,
                    event_type="order_rejected",
                    message=order.reason,
                    payload={"ticker": order.ticker, "qty": order.qty},
                )
            )
            return order

        if result.status == "filled":
            apply_fill(self.db, order, result)
            self.db.add(
                models.AuditLog(
                    session_id=session.id,
                    event_type="order_filled",
                    message=f"Filled {order.qty} {order.ticker} at {result.fill_price}.",
                    payload={"order_id": order.id, "broker_order_id": result.broker_order_id},
                )
            )
        else:
            self.db.add(
                models.AuditLog(
                    session_id=session.id,
                    event_type="order_open",
                    message=f"Order is open for {order.ticker}.",
                    payload={"order_id": order.id, "broker_order_id": result.broker_order_id},
                )
            )
        return order

    def close_position(self, session: models.BotSession, position: models.Position, reason: str) -> models.Order:
        order = models.Order(
            session_id=session.id,
            ticker=position.ticker,
            side="sell",
            order_type="market" if session.mode == "paper" else "limit",
            qty=position.qty,
            limit_price=None,
            stop_loss=None,
            take_profit=None,
            trailing_stop=None,
            status="intent",
            idempotency_key=f"{session.id}:sell:{position.ticker}:{uuid4()}",
            reason=reason,
        )
        self.db.add(order)
        self.db.flush()
        broker = get_broker(self.db, self.market_data, session.mode)
        result = broker.submit_order(
            OrderIntent(
                session_id=session.id,
                ticker=order.ticker,
                side=order.side,
                qty=order.qty,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_loss=None,
                take_profit=None,
                trailing_stop=None,
                idempotency_key=order.idempotency_key,
                reason=reason,
            )
        )
        order.broker_order_id = result.broker_order_id
        order.status = result.status
        if result.status == "filled":
            apply_fill(self.db, order, result)
        self.db.add(
            models.AuditLog(
                session_id=session.id,
                event_type="position_exit",
                message=reason,
                payload={"order_id": order.id, "ticker": position.ticker, "status": result.status},
            )
        )
        return order
