from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.app.broker.paper_broker import PaperBroker
from backend.app.core.config import get_settings
from backend.app.core.security import live_confirmation_valid
from backend.app.core.time_utils import utc_now
from backend.app.db import models
from backend.app.db.session import get_db
from backend.app.execution.engine import ExecutionEngine
from backend.app.execution.order_router import get_broker
from backend.app.market_data.live import DemoMarketDataProvider
from backend.app.risk.kill_switch import emergency_stop
from backend.app.risk.position_sizing import profile_for
from backend.app.schemas.bot import AuditLogResponse, BotSessionResponse, CandidateResponse, StartBotRequest, StatusResponse
from backend.app.schemas.order import OrderResponse
from backend.app.schemas.position import PositionResponse
from backend.app.workers.bot_loop import BotLoop


router = APIRouter(tags=["bot"])


def _session_or_404(db: Session, session_id: str) -> models.BotSession:
    session = db.get(models.BotSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Bot session not found")
    return session


def _status_payload(db: Session, session: models.BotSession) -> StatusResponse:
    candidates = db.execute(
        select(models.CandidateRanking)
        .where(models.CandidateRanking.session_id == session.id)
        .order_by(desc(models.CandidateRanking.created_at), desc(models.CandidateRanking.final_score))
        .limit(12)
    ).scalars().all()
    audit = db.execute(
        select(models.AuditLog)
        .where(models.AuditLog.session_id == session.id)
        .order_by(desc(models.AuditLog.created_at))
        .limit(30)
    ).scalars().all()
    return StatusResponse(
        session=BotSessionResponse.model_validate(session),
        candidates=[CandidateResponse.model_validate(candidate) for candidate in candidates],
        audit_log=[AuditLogResponse.model_validate(row) for row in audit],
    )


@router.post("/bot/start", response_model=StatusResponse)
def start_bot(payload: StartBotRequest, db: Session = Depends(get_db)) -> StatusResponse:
    if not payload.auto_pick and not payload.ticker:
        raise HTTPException(status_code=400, detail="Choose a ticker or enable Auto-Pick.")
    if payload.mode == "live" and not live_confirmation_valid(payload.mode, payload.live_confirmation):
        raise HTTPException(status_code=403, detail="Live trading requires configuration and the exact confirmation phrase.")
    if payload.mode == "live":
        settings = get_settings()
        if not (settings.alpaca_key_id and settings.alpaca_secret_key):
            raise HTTPException(status_code=403, detail="Live trading requires configured broker credentials.")
        raise HTTPException(status_code=501, detail="Live broker execution is a safety placeholder and is not enabled in this build.")

    market_data = DemoMarketDataProvider()
    status = market_data.get_status()
    if not status.connected or not status.fresh:
        raise HTTPException(status_code=503, detail=f"Market data unavailable: {status.message}")

    broker = PaperBroker(db, market_data)
    account = broker.get_account()
    if payload.capital > account.buying_power:
        raise HTTPException(status_code=400, detail="Capital allocation exceeds buying power.")

    profile = profile_for(payload.risk_level)
    max_open_positions = {"conservative": 1, "balanced": 2, "aggressive": 3}[payload.risk_level]
    session = models.BotSession(
        mode=payload.mode,
        ticker=payload.ticker,
        auto_pick=payload.auto_pick,
        capital=payload.capital,
        risk_level=payload.risk_level,
        strategy=payload.strategy,
        status="WAITING",
        live_confirmed=payload.mode == "live",
        last_signal="WAIT",
        last_reason="Session created. Worker will scan and manage trades.",
    )
    db.add(session)
    db.flush()
    db.add(models.StrategyConfig(session_id=session.id, strategy=payload.strategy, parameters={}))
    db.add(
        models.RiskConfig(
            session_id=session.id,
            risk_level=payload.risk_level,
            max_trade_risk_pct=profile["max_trade_risk_pct"],
            max_daily_loss_pct=profile["max_daily_loss_pct"],
            max_position_pct=profile["max_position_pct"],
            max_open_positions=max_open_positions,
            max_spread_bps=35,
            max_quote_age_seconds=90,
        )
    )
    db.add(
        models.AuditLog(
            session_id=session.id,
            event_type="session_started",
            message="Paper session started." if payload.mode == "paper" else "Live session requested and gated.",
            payload=payload.model_dump(exclude={"live_confirmation"}),
        )
    )
    BotLoop(db).run_once(session.id)
    db.commit()
    db.refresh(session)
    return _status_payload(db, session)


@router.post("/bot/{session_id}/pause", response_model=StatusResponse)
def pause_bot(session_id: str, db: Session = Depends(get_db)) -> StatusResponse:
    session = _session_or_404(db, session_id)
    session.paused = True
    session.status = "WAITING"
    session.last_reason = "Paused by user."
    db.add(models.AuditLog(session_id=session.id, event_type="paused", message="Bot paused by user.", payload={}))
    db.commit()
    db.refresh(session)
    return _status_payload(db, session)


@router.post("/bot/{session_id}/resume", response_model=StatusResponse)
def resume_bot(session_id: str, db: Session = Depends(get_db)) -> StatusResponse:
    session = _session_or_404(db, session_id)
    if session.emergency_stopped:
        raise HTTPException(status_code=409, detail="Emergency-stopped sessions cannot resume.")
    session.paused = False
    session.status = "WAITING"
    session.last_reason = "Resumed by user."
    db.add(models.AuditLog(session_id=session.id, event_type="resumed", message="Bot resumed by user.", payload={}))
    BotLoop(db).run_once(session.id)
    db.commit()
    db.refresh(session)
    return _status_payload(db, session)


@router.post("/bot/{session_id}/stop", response_model=StatusResponse)
def stop_new_trades(session_id: str, db: Session = Depends(get_db)) -> StatusResponse:
    session = _session_or_404(db, session_id)
    session.stop_new_trades = True
    session.status = "WAITING"
    session.last_reason = "Stopped new trades. Existing position management remains active."
    db.add(models.AuditLog(session_id=session.id, event_type="stop_new_trades", message=session.last_reason, payload={}))
    db.commit()
    db.refresh(session)
    return _status_payload(db, session)


@router.post("/bot/{session_id}/emergency-stop", response_model=StatusResponse)
def emergency_stop_bot(session_id: str, db: Session = Depends(get_db)) -> StatusResponse:
    session = _session_or_404(db, session_id)
    market_data = DemoMarketDataProvider()
    broker = get_broker(db, market_data, session.mode)
    emergency_stop(db, broker, session, "Emergency stop requested by user. Open orders cancelled and new trades disabled.")
    db.commit()
    db.refresh(session)
    return _status_payload(db, session)


@router.get("/bot/{session_id}/status", response_model=StatusResponse)
def bot_status(session_id: str, db: Session = Depends(get_db)) -> StatusResponse:
    return _status_payload(db, _session_or_404(db, session_id))


@router.get("/bot/{session_id}/audit-log", response_model=list[AuditLogResponse])
def audit_log(session_id: str, db: Session = Depends(get_db)) -> list[AuditLogResponse]:
    _session_or_404(db, session_id)
    rows = db.execute(
        select(models.AuditLog).where(models.AuditLog.session_id == session_id).order_by(desc(models.AuditLog.created_at)).limit(100)
    ).scalars().all()
    return [AuditLogResponse.model_validate(row) for row in rows]


@router.get("/positions", response_model=list[PositionResponse])
def positions(db: Session = Depends(get_db)) -> list[PositionResponse]:
    rows = db.execute(select(models.Position).order_by(desc(models.Position.opened_at))).scalars().all()
    return [PositionResponse.model_validate(row) for row in rows]


@router.post("/positions/{position_id}/close", response_model=OrderResponse)
def close_position(position_id: int, db: Session = Depends(get_db)) -> OrderResponse:
    position = db.get(models.Position, position_id)
    if position is None or position.status != "open":
        raise HTTPException(status_code=404, detail="Open position not found")
    session = db.get(models.BotSession, position.session_id) if position.session_id else None
    if session is None:
        raise HTTPException(status_code=404, detail="Owning bot session not found")
    order = ExecutionEngine(db, DemoMarketDataProvider()).close_position(session, position, "Position closed by user.")
    session.last_signal = "SELL"
    session.last_reason = "Position closed by user."
    session.updated_at = utc_now()
    db.commit()
    db.refresh(order)
    return OrderResponse.model_validate(order)


@router.get("/orders", response_model=list[OrderResponse])
def orders(db: Session = Depends(get_db)) -> list[OrderResponse]:
    rows = db.execute(select(models.Order).order_by(desc(models.Order.created_at)).limit(100)).scalars().all()
    return [OrderResponse.model_validate(row) for row in rows]
