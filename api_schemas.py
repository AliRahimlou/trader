from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EventRecord(BaseModel):
    id: int
    ts: str
    event: str
    level: str
    message: str | None = None
    symbol: str | None = None
    strategy: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandRecord(BaseModel):
    id: int
    created_at: str
    updated_at: str
    command_type: str
    actor: str
    confirmed: bool
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class SnapshotEnvelope(BaseModel):
    name: str
    updated_at: str
    payload: dict[str, Any]


class ControlRequest(BaseModel):
    actor: str = "local-operator"
    confirm: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class OverviewResponse(BaseModel):
    runner_status: dict[str, Any]
    health: dict[str, Any]
    account: dict[str, Any]
    positions: list[dict[str, Any]]
    open_orders: list[dict[str, Any]]
    strategy_status: dict[str, Any]
    commands: list[CommandRecord]


class HealthResponse(BaseModel):
    payload: dict[str, Any]


class EventsResponse(BaseModel):
    items: list[EventRecord]


class CommandsResponse(BaseModel):
    items: list[CommandRecord]
