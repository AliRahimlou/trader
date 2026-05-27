from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.broker.paper_broker import PaperBroker
from backend.app.db.session import get_db
from backend.app.market_data.live import DemoMarketDataProvider


router = APIRouter(prefix="/broker", tags=["broker"])


@router.get("/status")
def broker_status(db: Session = Depends(get_db)) -> dict:
    account = PaperBroker(db, DemoMarketDataProvider()).get_account()
    return {
        "provider": "paper",
        "connected": True,
        "mode": account.mode,
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
    }
