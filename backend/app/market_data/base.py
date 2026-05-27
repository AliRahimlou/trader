from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Quote:
    ticker: str
    bid: float
    ask: float
    last: float
    spread_bps: float
    timestamp: datetime
    provider: str = "demo"


@dataclass(frozen=True)
class MarketDataStatus:
    provider: str
    connected: bool
    fresh: bool
    message: str
    checked_at: datetime


class MarketDataProvider(Protocol):
    def get_historical_bars(self, ticker: str, days: int = 180) -> list[Bar]:
        ...

    def get_latest_quote(self, ticker: str) -> Quote:
        ...

    def get_status(self) -> MarketDataStatus:
        ...
