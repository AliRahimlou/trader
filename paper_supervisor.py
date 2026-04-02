from __future__ import annotations

import threading
from typing import Any

from operator_store import OperatorStore
from paper_engine import PaperTradingEngine


DANGEROUS_COMMANDS = {
    "start_runner",
    "stop_runner",
    "run_once",
    "flatten_all",
    "cancel_open_orders",
    "set_dry_run",
    "reset_runtime_overrides",
    "apply_config",
    "close_symbol",
    "smoke_test",
}


class EngineSupervisor:
    def __init__(self, config, store: OperatorStore) -> None:
        self.config = config
        self.store = store
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.engine = PaperTradingEngine(config, store)

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start_runner(self) -> dict[str, Any]:
        with self.lock:
            if self.is_running():
                raise RuntimeError("Runner is already active.")
            self.engine = PaperTradingEngine(self.config, self.store)
            self.thread = threading.Thread(target=self.engine.run_forever, daemon=True, name="paper-engine")
            self.thread.start()
            if not self.engine.wait_for_startup(self.config.startup_timeout_seconds):
                self.engine.request_stop()
                if self.thread is not None:
                    self.thread.join(timeout=1)
                    if not self.thread.is_alive():
                        self.thread = None
                failure_message = self.engine.startup_failure_message()
                if failure_message:
                    raise RuntimeError(f"Runner startup failed: {failure_message}")
                raise RuntimeError(
                    "Runner startup timed out after "
                    f"{self.config.startup_timeout_seconds:.1f}s. Stop was requested before trading began."
                )
            self.store.append_event(
                {
                    "event": "runner_start_requested",
                    "level": "INFO",
                    "message": "Runner thread started.",
                    "symbol": self.config.symbol,
                }
            )
            return {"running": True}

    def stop_runner(self) -> dict[str, Any]:
        with self.lock:
            if not self.is_running():
                raise RuntimeError("Runner is not active.")
            assert self.thread is not None
            self.engine.request_stop()
            self.thread.join(timeout=10)
            if not self.thread.is_alive():
                self.thread = None
            self.store.append_event(
                {
                    "event": "runner_stop_requested",
                    "level": "INFO",
                    "message": "Runner stop requested.",
                    "symbol": self.config.symbol,
                }
            )
            return {"running": self.is_running()}

    def shutdown(self) -> None:
        with self.lock:
            if not self.is_running():
                return
            self.engine.request_stop()
            assert self.thread is not None
            self.thread.join(timeout=10)
            if not self.thread.is_alive():
                self.thread = None

    def execute_command(
        self,
        command_type: str,
        *,
        actor: str,
        confirm: bool,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload or {}
        command = self.store.create_command(
            command_type=command_type,
            actor=actor,
            confirmed=confirm,
            payload=payload,
        )
        self.store.append_event(
            {
                "event": "audit_command_requested",
                "level": "INFO",
                "message": f"Operator requested {command_type}.",
                "symbol": self.config.symbol,
                "command_id": command["id"],
                "command_type": command_type,
                "actor": actor,
                "payload": payload,
            }
        )

        if command_type in DANGEROUS_COMMANDS and not confirm:
            updated = self.store.update_command(
                command["id"],
                status="rejected",
                result={"reason": "confirmation_required"},
            )
            self.store.append_event(
                {
                    "event": "audit_command_rejected",
                    "level": "WARNING",
                    "message": f"Rejected {command_type}: confirmation required.",
                    "symbol": self.config.symbol,
                    "command_id": command["id"],
                }
            )
            return updated or command

        try:
            result = self._apply_command(command_type, payload)
        except Exception as exc:
            updated = self.store.update_command(
                command["id"],
                status="failed",
                result={"error": str(exc)},
            )
            self.store.append_event(
                {
                    "event": "audit_command_failed",
                    "level": "ERROR",
                    "message": f"Command {command_type} failed: {exc}",
                    "symbol": self.config.symbol,
                    "command_id": command["id"],
                    "error": str(exc),
                }
            )
            return updated or command

        updated = self.store.update_command(
            command["id"],
            status="applied",
            result=result,
        )
        self.store.append_event(
            {
                "event": "audit_command_applied",
                "level": "INFO",
                "message": f"Command {command_type} applied.",
                "symbol": self.config.symbol,
                "command_id": command["id"],
                "result": result,
            }
        )
        return updated or command

    def _apply_command(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command_type == "start_runner":
            return self.start_runner()
        if command_type == "stop_runner":
            return self.stop_runner()
        if command_type == "pause_entries":
            return self.engine.set_pause_new_entries(True)
        if command_type == "resume_entries":
            return self.engine.set_pause_new_entries(False)
        if command_type == "flatten_all":
            return self.engine.flatten_all()
        if command_type == "cancel_open_orders":
            return self.engine.cancel_open_orders()
        if command_type == "set_symbol_enabled":
            return self.engine.set_symbol_enabled(payload["symbol"], bool(payload["enabled"]))
        if command_type == "set_strategy_enabled":
            return self.engine.set_strategy_enabled(payload["strategy"], bool(payload["enabled"]))
        if command_type == "set_dry_run":
            return self.engine.set_dry_run(bool(payload["dry_run"]))
        if command_type == "reset_runtime_overrides":
            return self.engine.reset_runtime_overrides()
        if command_type == "apply_config":
            return self.engine.apply_runtime_settings(payload)
        if command_type == "close_symbol":
            return self.engine.close_symbol(payload["symbol"])
        if command_type == "run_once":
            if self.is_running():
                raise RuntimeError("Cannot run once while the continuous runner is active.")
            self.engine = PaperTradingEngine(self.config, self.store)
            self.engine.run_once()
            return {"ran_once": True}
        if command_type == "smoke_test":
            if self.is_running():
                raise RuntimeError("Stop the continuous runner before running a smoke test.")
            self.engine = PaperTradingEngine(self.config, self.store)
            self.engine.run_smoke_test()
            return {"smoke_test": "completed"}
        raise RuntimeError(f"Unsupported command: {command_type}")
