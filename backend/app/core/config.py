from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="Atlas-Inspired Trading Bot", alias="TRADER_APP_NAME")
    env: str = Field(default="local", alias="TRADER_ENV")
    database_url: str = Field(default="sqlite:///./runtime/trader_app.db", alias="TRADER_DATABASE_URL")
    frontend_origin: str = Field(default="http://127.0.0.1:5174", alias="TRADER_FRONTEND_ORIGIN")
    default_starting_cash: float = Field(default=100_000.0, alias="TRADER_DEFAULT_STARTING_CASH")
    embed_worker: bool = Field(default=True, alias="TRADER_EMBED_WORKER")
    worker_interval_seconds: int = Field(default=5, alias="TRADER_WORKER_INTERVAL_SECONDS")
    market_data_provider: Literal["demo"] = Field(default="demo", alias="TRADER_MARKET_DATA_PROVIDER")
    broker_provider: Literal["paper", "live_placeholder"] = Field(default="paper", alias="TRADER_BROKER_PROVIDER")
    live_trading_enabled: bool = Field(default=False, alias="TRADER_LIVE_TRADING_ENABLED")
    live_confirmation_phrase: str = Field(
        default="I understand live trading risk",
        alias="TRADER_LIVE_CONFIRMATION_PHRASE",
    )
    alpaca_key_id: str | None = Field(default=None, alias="TRADER_ALPACA_KEY_ID")
    alpaca_secret_key: str | None = Field(default=None, alias="TRADER_ALPACA_SECRET_KEY")
    alpaca_base_url: str = Field(default="https://paper-api.alpaca.markets", alias="TRADER_ALPACA_BASE_URL")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
