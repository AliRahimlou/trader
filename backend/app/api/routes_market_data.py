from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.broker.paper_broker import PaperBroker
from backend.app.db.session import get_db
from backend.app.market_data.live import DemoMarketDataProvider
from backend.app.schemas.bot import CandidateResponse
from backend.app.strategies.ranking import rank_candidates


router = APIRouter(prefix="/market", tags=["market"])


@router.get("/quote/{ticker}")
def quote(ticker: str) -> dict:
    market_data = DemoMarketDataProvider()
    q = market_data.get_latest_quote(ticker)
    return {
        "ticker": q.ticker,
        "bid": q.bid,
        "ask": q.ask,
        "last": q.last,
        "spread_bps": q.spread_bps,
        "timestamp": q.timestamp.isoformat(),
        "provider": q.provider,
    }


@router.get("/rankings", response_model=list[CandidateResponse])
def rankings(
    db: Session = Depends(get_db),
    ticker: str | None = Query(default=None),
    auto_pick: bool = Query(default=True),
    strategy: str = Query(default="auto_strategy"),
    risk_level: str = Query(default="balanced"),
    capital: float = Query(default=10_000, gt=0),
) -> list[CandidateResponse]:
    market_data = DemoMarketDataProvider()
    account = PaperBroker(db, market_data).get_account()
    candidates = rank_candidates(
        market_data=market_data,
        ticker=ticker.upper() if ticker else None,
        auto_pick=auto_pick,
        strategy_name=strategy,
        risk_level=risk_level,
        capital=capital,
        account=account,
    )
    return [CandidateResponse(**candidate.__dict__) for candidate in candidates]
