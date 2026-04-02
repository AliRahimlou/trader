from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable.")


class OperatorStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT,
                    symbol TEXT,
                    strategy TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS snapshots (
                    name TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT
                );
                """
            )

    def append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        ts = str(payload.get("ts") or utc_now_iso())
        event = str(payload.get("event") or "unknown")
        level = str(payload.get("level") or "INFO")
        message = payload.get("message")
        symbol = _string_or_none(payload.get("symbol"))
        strategy = _string_or_none(payload.get("strategy") or payload.get("strategy_id"))
        extra_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"ts", "event", "level", "message", "symbol", "strategy", "strategy_id"}
        }

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (ts, event, level, message, symbol, strategy, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    event,
                    level,
                    message,
                    symbol,
                    strategy,
                    json_dumps(extra_payload),
                ),
            )
            event_id = int(cursor.lastrowid)
        return {
            "id": event_id,
            "ts": ts,
            "event": event,
            "level": level,
            "message": message,
            "symbol": symbol,
            "strategy": strategy,
            "payload": extra_payload,
        }

    def list_events(
        self,
        *,
        limit: int = 200,
        after_id: int = 0,
        level: str | None = None,
        event: str | None = None,
        symbol: str | None = None,
        strategy: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["id > ?"]
        params: list[Any] = [after_id]
        if level:
            clauses.append("level = ?")
            params.append(level)
        if event:
            clauses.append("event = ?")
            params.append(event)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if strategy:
            clauses.append("strategy = ?")
            params.append(strategy)
        params.append(limit)

        query = f"""
            SELECT id, ts, event, level, message, symbol, strategy, payload_json
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT ?
        """
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._event_from_row(row) for row in reversed(rows)]

    def upsert_snapshot(self, name: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO snapshots (name, updated_at, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (name, utc_now_iso(), json_dumps(payload)),
            )

    def get_snapshot(self, name: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT name, updated_at, payload_json FROM snapshots WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return {
            "name": row["name"],
            "updated_at": row["updated_at"],
            "payload": json.loads(row["payload_json"]),
        }

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT name, updated_at, payload_json FROM snapshots ORDER BY name ASC"
            ).fetchall()
        return [
            {"name": row["name"], "updated_at": row["updated_at"], "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def delete_snapshot(self, name: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM snapshots WHERE name = ?", (name,))

    def create_command(
        self,
        *,
        command_type: str,
        actor: str,
        confirmed: bool,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        serialized_payload = payload or {}
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO commands (created_at, updated_at, command_type, actor, confirmed, status, payload_json)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    now,
                    now,
                    command_type,
                    actor,
                    1 if confirmed else 0,
                    json_dumps(serialized_payload),
                ),
            )
            command_id = int(cursor.lastrowid)
        return self.get_command(command_id) or {}

    def get_command(self, command_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, created_at, updated_at, command_type, actor, confirmed, status, payload_json, result_json
                FROM commands
                WHERE id = ?
                """,
                (command_id,),
            ).fetchone()
        if row is None:
            return None
        return self._command_from_row(row)

    def list_commands(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, updated_at, command_type, actor, confirmed, status, payload_json, result_json
                FROM commands
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._command_from_row(row) for row in rows]

    def update_command(
        self,
        command_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE commands
                SET updated_at = ?, status = ?, result_json = ?
                WHERE id = ?
                """,
                (utc_now_iso(), status, json_dumps(result or {}), command_id),
            )
        return self.get_command(command_id)

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "ts": row["ts"],
            "event": row["event"],
            "level": row["level"],
            "message": row["message"],
            "symbol": row["symbol"],
            "strategy": row["strategy"],
            "payload": json.loads(row["payload_json"]),
        }

    def _command_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "command_type": row["command_type"],
            "actor": row["actor"],
            "confirmed": bool(row["confirmed"]),
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else {},
        }


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
