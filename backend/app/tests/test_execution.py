from types import SimpleNamespace

from backend.app.db import models
from backend.app.execution.engine import ExecutionEngine
from backend.app.market_data.live import DemoMarketDataProvider


def test_paper_execution_stores_intent_and_fill(db_session):
    market_data = DemoMarketDataProvider()
    quote = market_data.get_latest_quote("AAPL")
    session = models.BotSession(
        mode="paper",
        ticker="AAPL",
        auto_pick=False,
        capital=10_000,
        risk_level="balanced",
        strategy="trend_momentum",
        status="WAITING",
    )
    db_session.add(session)
    db_session.flush()
    candidate = SimpleNamespace(
        ticker="AAPL",
        suggested_position_size=1,
        entry_trigger=quote.ask,
        stop_loss=quote.ask * 0.97,
        take_profit=quote.ask * 1.06,
        trailing_stop=quote.ask * 0.98,
        reason="test order",
    )
    order = ExecutionEngine(db_session, market_data).submit_candidate_order(session, candidate)
    db_session.commit()
    assert order.status == "filled"
    assert db_session.query(models.Fill).count() == 1
    assert db_session.query(models.Position).count() == 1
