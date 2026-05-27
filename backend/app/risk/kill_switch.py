from sqlalchemy.orm import Session

from backend.app.broker.base import Broker
from backend.app.core.time_utils import utc_now
from backend.app.db import models


def emergency_stop(db: Session, broker: Broker, session: models.BotSession, reason: str) -> int:
    cancelled = broker.cancel_all_orders(session.id)
    session.status = "ERROR"
    session.emergency_stopped = True
    session.stop_new_trades = True
    session.paused = True
    session.stopped_at = utc_now()
    db.add(
        models.AuditLog(
            session_id=session.id,
            event_type="emergency_stop",
            message=reason,
            payload={"cancelled_orders": cancelled},
        )
    )
    return cancelled
