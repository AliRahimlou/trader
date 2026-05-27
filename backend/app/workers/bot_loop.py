from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.app.broker.reconciliation import reconcile_positions
from backend.app.core.time_utils import utc_now
from backend.app.db import models
from backend.app.execution.engine import ExecutionEngine
from backend.app.execution.order_router import get_broker
from backend.app.market_data.live import DemoMarketDataProvider
from backend.app.risk.position_sizing import profile_for
from backend.app.risk.stop_manager import evaluate_exit
from backend.app.strategies.ranking import rank_candidates


class BotLoop:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.market_data = DemoMarketDataProvider()

    def run_once(self, session_id: str) -> None:
        session = self.db.get(models.BotSession, session_id)
        if session is None:
            return
        if session.emergency_stopped or session.status in {"OFF", "ERROR"}:
            return
        if session.paused:
            session.status = "WAITING"
            session.last_reason = "Bot is paused by user."
            return

        broker = get_broker(self.db, self.market_data, session.mode)
        account = broker.get_account()
        reconcile_positions(self.db, self.market_data)

        open_positions = self.db.execute(
            select(models.Position).where(models.Position.session_id == session.id, models.Position.status == "open")
        ).scalars().all()
        execution = ExecutionEngine(self.db, self.market_data)
        if open_positions:
            session.status = "IN POSITION"
            for position in open_positions:
                quote = self.market_data.get_latest_quote(position.ticker)
                position.current_price = quote.last
                exit_decision = evaluate_exit(position, quote)
                if exit_decision.should_exit:
                    session.status = "EXITING"
                    execution.close_position(session, position, exit_decision.reason)
                    session.last_signal = "SELL"
                    session.last_reason = exit_decision.reason
                    session.current_ticker = position.ticker
                    return
            session.last_signal = "HOLD"
            session.last_reason = "Monitoring open position against stop, target, trailing stop, and risk rules."
            session.current_ticker = open_positions[0].ticker
            return

        if session.stop_new_trades:
            session.status = "WAITING"
            session.last_signal = "WAIT"
            session.last_reason = "New trades are stopped for this session."
            return

        session.status = "SCANNING"
        self.db.add(
            models.AuditLog(
                session_id=session.id,
                event_type="scan_started",
                message="Scanning manual ticker." if not session.auto_pick else "Scanning stock universe.",
                payload={"ticker": session.ticker, "auto_pick": session.auto_pick},
            )
        )
        self.db.flush()

        profile = profile_for(session.risk_level)
        risk_config = self.db.execute(
            select(models.RiskConfig).where(models.RiskConfig.session_id == session.id)
        ).scalar_one_or_none()
        max_open_positions = risk_config.max_open_positions if risk_config else 1
        candidates = rank_candidates(
            market_data=self.market_data,
            ticker=session.ticker,
            auto_pick=session.auto_pick,
            strategy_name=session.strategy,
            risk_level=session.risk_level,
            capital=session.capital,
            account=account,
            max_open_positions=max_open_positions,
            open_positions=len(open_positions),
        )

        for candidate in candidates:
            self.db.add(
                models.CandidateRanking(
                    session_id=session.id,
                    ticker=candidate.ticker,
                    direction=candidate.direction,
                    confidence=candidate.confidence,
                    strategy_score=candidate.strategy_score,
                    risk_score=candidate.risk_score,
                    final_score=candidate.final_score,
                    entry_trigger=candidate.entry_trigger,
                    stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit,
                    trailing_stop=candidate.trailing_stop,
                    invalidation_condition=candidate.invalidation_condition,
                    expected_risk_reward=candidate.expected_risk_reward,
                    suggested_position_size=candidate.suggested_position_size,
                    reason=candidate.reason,
                    allowed=candidate.allowed,
                    rejection_reason=candidate.rejection_reason,
                    review=candidate.review,
                )
            )

        best = next((candidate for candidate in candidates if candidate.allowed), None)
        if best is None:
            session.status = "WAITING"
            session.last_signal = "WAIT"
            session.last_reason = candidates[0].rejection_reason if candidates else "No eligible candidate found."
            self.db.add(
                models.AuditLog(
                    session_id=session.id,
                    event_type="trade_skipped",
                    message=session.last_reason,
                    payload={"risk_profile": profile, "candidates": [candidate.ticker for candidate in candidates[:5]]},
                )
            )
            return

        order = execution.submit_candidate_order(session, best)
        session.current_ticker = best.ticker
        session.last_signal = "BUY" if order.status == "filled" else "WAIT"
        session.status = "IN POSITION" if order.status == "filled" else "WAITING"
        session.last_reason = best.reason if order.status != "rejected" else order.reason
        session.updated_at = utc_now()


def process_active_sessions(db: Session) -> int:
    sessions = db.execute(
        select(models.BotSession)
        .where(models.BotSession.status.in_(["SCANNING", "WAITING", "IN POSITION", "EXITING"]))
        .order_by(desc(models.BotSession.updated_at))
    ).scalars().all()
    loop = BotLoop(db)
    for session in sessions:
        loop.run_once(session.id)
    return len(sessions)
