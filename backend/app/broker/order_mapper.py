from backend.app.broker.base import OrderIntent
from backend.app.db import models


def model_to_intent(order: models.Order) -> OrderIntent:
    return OrderIntent(
        session_id=order.session_id,
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
