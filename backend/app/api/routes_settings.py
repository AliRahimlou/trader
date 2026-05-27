from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.core.config import get_settings
from backend.app.core.security import redact_secret
from backend.app.risk.position_sizing import RISK_PROFILES


router = APIRouter(tags=["settings"])
_SETTINGS_OVERRIDES: dict = {}


class SettingsUpdate(BaseModel):
    broker_key_id: str | None = None
    broker_secret: str | None = None
    market_data_key: str | None = None
    paper_live_mode: str | None = None
    max_daily_loss: float | None = None
    max_trade_risk: float | None = None
    max_open_positions: int | None = None
    trading_hours: str | None = None
    notifications: dict | None = None

    model_config = {"extra": "allow"}


@router.get("/settings")
def get_app_settings() -> dict:
    settings = get_settings()
    return {
        "mode_default": "paper",
        "market_data_provider": settings.market_data_provider,
        "broker_provider": settings.broker_provider,
        "live_trading_enabled": settings.live_trading_enabled,
        "live_confirmation_phrase_required": bool(settings.live_confirmation_phrase),
        "alpaca_key_id": redact_secret(settings.alpaca_key_id),
        "risk_profiles": RISK_PROFILES,
        "overrides": _SETTINGS_OVERRIDES,
    }


@router.put("/settings")
def update_app_settings(payload: SettingsUpdate) -> dict:
    data = payload.model_dump(exclude_none=True)
    if data.get("broker_secret"):
        data["broker_secret"] = "***"
    if data.get("market_data_key"):
        data["market_data_key"] = redact_secret(str(data["market_data_key"]))
    _SETTINGS_OVERRIDES.update(data)
    return {"saved": True, "settings": _SETTINGS_OVERRIDES}
