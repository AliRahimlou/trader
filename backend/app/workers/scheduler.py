import logging

from apscheduler.schedulers.background import BackgroundScheduler

from backend.app.core.config import get_settings
from backend.app.db.session import session_scope
from backend.app.workers.bot_loop import process_active_sessions


logger = logging.getLogger(__name__)
settings = get_settings()
_scheduler: BackgroundScheduler | None = None


def _job() -> None:
    with session_scope() as db:
        count = process_active_sessions(db)
        if count:
            logger.info("processed %s active bot sessions", count)


def start_embedded_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_job, "interval", seconds=settings.worker_interval_seconds, id="bot_loop", replace_existing=True)
    _scheduler.start()
    logger.info("embedded bot scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("bot scheduler stopped")
    _scheduler = None
