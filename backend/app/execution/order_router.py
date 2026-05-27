from sqlalchemy.orm import Session

from backend.app.broker.base import Broker
from backend.app.broker.live_broker_placeholder import LiveBrokerPlaceholder
from backend.app.broker.paper_broker import PaperBroker
from backend.app.market_data.base import MarketDataProvider


def get_broker(db: Session, market_data: MarketDataProvider, mode: str) -> Broker:
    if mode == "paper":
        return PaperBroker(db, market_data)
    return LiveBrokerPlaceholder()
