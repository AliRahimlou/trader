from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.app.broker.base import BrokerOrderResult
from backend.app.core.config import get_settings
from backend.app.core.time_utils import utc_now
from backend.app.db import models


def _latest_cash(db: Session) -> float:
    snapshot = db.execute(
        select(models.BrokerAccountSnapshot).order_by(desc(models.BrokerAccountSnapshot.created_at)).limit(1)
    ).scalar_one_or_none()
    return snapshot.cash if snapshot else get_settings().default_starting_cash


def apply_fill(db: Session, order: models.Order, result: BrokerOrderResult) -> None:
    if result.filled_qty <= 0 or result.fill_price is None:
        return

    fill = models.Fill(
        order_id=order.id,
        ticker=order.ticker,
        side=order.side,
        qty=result.filled_qty,
        price=result.fill_price,
        commission=0.0,
    )
    db.add(fill)
    cash = _latest_cash(db)
    notional = result.filled_qty * result.fill_price

    if order.side == "buy":
        position = db.execute(
            select(models.Position)
            .where(models.Position.session_id == order.session_id, models.Position.ticker == order.ticker, models.Position.status == "open")
            .order_by(desc(models.Position.opened_at))
        ).scalar_one_or_none()
        if position:
            total_qty = position.qty + result.filled_qty
            position.avg_entry_price = round(
                ((position.avg_entry_price * position.qty) + notional) / max(total_qty, 1),
                2,
            )
            position.qty = total_qty
            position.current_price = result.fill_price
        else:
            db.add(
                models.Position(
                    session_id=order.session_id,
                    ticker=order.ticker,
                    qty=result.filled_qty,
                    avg_entry_price=result.fill_price,
                    current_price=result.fill_price,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    trailing_stop=order.trailing_stop,
                    status="open",
                )
            )
        cash -= notional
    else:
        position = db.execute(
            select(models.Position)
            .where(models.Position.session_id == order.session_id, models.Position.ticker == order.ticker, models.Position.status == "open")
            .order_by(desc(models.Position.opened_at))
        ).scalar_one_or_none()
        if position:
            position.qty = max(0, position.qty - result.filled_qty)
            position.current_price = result.fill_price
            if position.qty == 0:
                position.status = "closed"
                position.closed_at = utc_now()
        cash += notional

    open_positions = db.execute(select(models.Position).where(models.Position.status == "open")).scalars().all()
    position_value = sum(position.qty * position.current_price for position in open_positions)
    db.add(
        models.BrokerAccountSnapshot(
            mode="paper",
            equity=round(cash + position_value, 2),
            cash=round(cash, 2),
            buying_power=round(cash, 2),
            payload={"last_fill_order_id": order.id},
        )
    )
