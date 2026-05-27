import time

from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging
from backend.app.db.session import init_db, session_scope
from backend.app.workers.bot_loop import process_active_sessions


def main() -> None:
    configure_logging()
    init_db()
    settings = get_settings()
    while True:
        with session_scope() as db:
            process_active_sessions(db)
        time.sleep(settings.worker_interval_seconds)


if __name__ == "__main__":
    main()
