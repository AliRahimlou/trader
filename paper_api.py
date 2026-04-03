from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api_schemas import (
    CommandRecord,
    CommandsResponse,
    ControlRequest,
    EventRecord,
    EventsResponse,
    OverviewResponse,
    TradePreviewRequest,
)
from live_config import PaperTradingConfig, config_from_env
from operator_store import OperatorStore
from paper_supervisor import EngineSupervisor


def create_app(config: PaperTradingConfig | None = None) -> FastAPI:
    config = config or config_from_env()
    store = OperatorStore(config.database_path)
    supervisor = EngineSupervisor(config, store)
    app = FastAPI(title="Paper Trading Control Plane", version="0.1.0")
    app.state.config = config
    app.state.store = store
    app.state.supervisor = supervisor

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000"],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("shutdown")
    def _shutdown() -> None:
        supervisor.shutdown()

    @app.get("/api/status")
    def get_status() -> dict[str, Any]:
        snapshot = _snapshot_or_default(store, "runner_status", {})
        snapshot["running"] = supervisor.is_running()
        return snapshot

    @app.get("/api/heartbeat")
    def get_heartbeat() -> dict[str, Any]:
        status = _snapshot_or_default(store, "runner_status", {})
        return {
            "running": supervisor.is_running(),
            "last_heartbeat": status.get("last_heartbeat"),
            "last_cycle_at": status.get("last_cycle_at"),
        }

    @app.get("/api/account")
    def get_account() -> dict[str, Any]:
        return _snapshot_or_default(store, "account", {})

    @app.get("/api/positions")
    def get_positions() -> list[dict[str, Any]]:
        return _snapshot_or_default(store, "positions", {"items": []}).get("items", [])

    @app.get("/api/orders")
    def get_orders() -> list[dict[str, Any]]:
        return _snapshot_or_default(store, "orders", {"items": []}).get("items", [])

    @app.get("/api/signals")
    def get_signals() -> dict[str, Any]:
        return _snapshot_or_default(store, "strategy_status", {})

    @app.get("/api/strategy-status")
    def get_strategy_status() -> dict[str, Any]:
        return _snapshot_or_default(store, "strategy_status", {})

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return _snapshot_or_default(store, "config", {})

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        health = _snapshot_or_default(store, "health", {})
        health["running"] = supervisor.is_running()
        return health

    @app.get("/api/diagnostics")
    def get_diagnostics() -> dict[str, Any]:
        return {
            "health": _snapshot_or_default(store, "health", {}),
            "runner_status": _snapshot_or_default(store, "runner_status", {}),
            "market_snapshot": _snapshot_or_default(store, "market_snapshot", {}),
            "state": _snapshot_or_default(store, "state", {}),
            "database_path": str(config.database_path),
        }

    @app.get("/api/overview", response_model=OverviewResponse)
    def get_overview() -> OverviewResponse:
        orders = _snapshot_or_default(store, "orders", {"items": []}).get("items", [])
        open_orders = [order for order in orders if order.get("status") not in {"filled", "canceled", "rejected"}]
        return OverviewResponse(
            runner_status=_snapshot_or_default(store, "runner_status", {}),
            health=_snapshot_or_default(store, "health", {}),
            account=_snapshot_or_default(store, "account", {}),
            positions=_snapshot_or_default(store, "positions", {"items": []}).get("items", []),
            open_orders=open_orders,
            strategy_status=_snapshot_or_default(store, "strategy_status", {}),
            commands=[CommandRecord.model_validate(item) for item in store.list_commands(limit=20)],
        )

    @app.get("/api/trade/context")
    def get_trade_context(symbol: str | None = None, chart_range: str = "1D") -> dict[str, Any]:
        try:
            return supervisor.engine.get_trade_context(symbol=symbol, chart_range=chart_range)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/trade/preview")
    def post_trade_preview(request: TradePreviewRequest) -> dict[str, Any]:
        try:
            return supervisor.engine.preview_manual_trade(
                symbol=request.symbol,
                side=request.side,
                amount_dollars=request.amount_dollars,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/events", response_model=EventsResponse)
    def get_events(
        limit: int = Query(default=200, le=1000),
        after_id: int = 0,
        level: str | None = None,
        event: str | None = None,
        symbol: str | None = None,
        strategy: str | None = None,
    ) -> EventsResponse:
        items = store.list_events(
            limit=limit,
            after_id=after_id,
            level=level,
            event=event,
            symbol=symbol,
            strategy=strategy,
        )
        return EventsResponse(items=[EventRecord.model_validate(item) for item in items])

    @app.get("/api/commands", response_model=CommandsResponse)
    def get_commands(limit: int = Query(default=100, le=500)) -> CommandsResponse:
        return CommandsResponse(items=[CommandRecord.model_validate(item) for item in store.list_commands(limit=limit)])

    @app.post("/api/controls/{command_type}", response_model=CommandRecord)
    def post_control(command_type: str, request: ControlRequest) -> CommandRecord:
        try:
            result = supervisor.execute_command(
                command_type,
                actor=request.actor,
                confirm=request.confirm,
                payload=request.payload,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandRecord.model_validate(result)

    @app.get("/api/events/stream")
    async def stream_events(request: Request, after_id: int = 0) -> StreamingResponse:
        async def event_generator():
            last_id = after_id
            while not await request.is_disconnected():
                events = store.list_events(after_id=last_id, limit=100)
                if events:
                    for item in events:
                        if await request.is_disconnected():
                            return
                        last_id = max(last_id, item["id"])
                        yield (
                            f"id: {item['id']}\n"
                            f"event: {item['event']}\n"
                            f"data: {json.dumps(item, sort_keys=True)}\n\n"
                        )
                else:
                    heartbeat = {
                        "event": "heartbeat",
                        "ts": _snapshot_or_default(store, "runner_status", {}).get("last_heartbeat"),
                        "running": supervisor.is_running(),
                    }
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat, sort_keys=True)}\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    frontend_dist = Path(__file__).resolve().parent / "dashboard" / "dist"
    if frontend_dist.exists():
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        def serve_frontend() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend_spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = frontend_dist / full_path
            if candidate.exists() and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend_dist / "index.html")

    return app


def _snapshot_or_default(store: OperatorStore, name: str, default: dict[str, Any]) -> dict[str, Any]:
    snapshot = store.get_snapshot(name)
    if snapshot is None:
        return default
    return snapshot["payload"]
