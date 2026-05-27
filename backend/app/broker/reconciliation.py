from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.broker.base import AccountState
from backend.app.db import models
from backend.app.market_data.base import MarketDataProvider


def reconcile_positions(db: Session, market_data: MarketDataProvider) -> AccountState:
    positions = db.execute(select(models.Position).where(models.Position.status == "open")).scalars().all()
    latest_snapshot = db.execute(select(models.BrokerAccountSnapshot).order_by(models.BrokerAccountSnapshot.created_at.desc())).scalar()
    cash = latest_snapshot.cash if latest_snapshot else 100_000.0
    position_value = 0.0
    for position in positions:
        quote = market_data.get_latest_quote(position.ticker)
        position.current_price = quote.last
        position_value += position.qty * quote.last
    equity = cash + position_value
    snapshot = models.BrokerAccountSnapshot(mode="paper", equity=equity, cash=cash, buying_power=cash)
    db.add(snapshot)
    return AccountState(mode="paper", equity=round(equity, 2), cash=round(cash, 2), buying_power=round(cash, 2))
