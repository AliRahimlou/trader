from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def setup_console_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


@dataclass
class StructuredLogger:
    path: Path
    logger_name: str = "live_paper"
    on_event: Callable[[dict[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.console = logging.getLogger(self.logger_name)

    def emit(
        self,
        event: str,
        *,
        level: str = "INFO",
        message: str | None = None,
        **fields: Any,
    ) -> None:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "level": level,
            **fields,
        }
        if message:
            payload["message"] = message
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        if self.on_event is not None:
            self.on_event(payload)

        if message:
            getattr(self.console, level.lower())(message)
