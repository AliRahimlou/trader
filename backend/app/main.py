from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api import routes_backtest, routes_bot, routes_broker, routes_market_data, routes_settings
from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging
from backend.app.db.session import init_db
from backend.app.workers.scheduler import start_embedded_scheduler, stop_scheduler


configure_logging()
settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://127.0.0.1:5174", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_bot.router)
app.include_router(routes_backtest.router)
app.include_router(routes_broker.router)
app.include_router(routes_market_data.router)
app.include_router(routes_settings.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    if settings.embed_worker:
        start_embedded_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_scheduler()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
