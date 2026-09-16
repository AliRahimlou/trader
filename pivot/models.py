"""Validated market observations. All timestamps are aware and bars are end-stamped."""
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

Direction = Literal['long', 'short']
MAG7 = ('AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'TSLA')


def timestamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp must include its timezone')
    return result


@dataclass(frozen=True)
class Bar:
    end: datetime
    minutes: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0
    vwap: float | None = None

    def __post_init__(self):
        timestamp(self.end)
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(isfinite(v) for v in values) or self.minutes <= 0 or self.volume < 0:
            raise ValueError('Invalid bar values')
        if not 0 < self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError('Invalid OHLC ordering')
        if self.vwap is not None and (not isfinite(self.vwap) or self.vwap <= 0):
            raise ValueError('Invalid VWAP')


@dataclass(frozen=True)
class Zone:
    low: float
    high: float
    established_at: datetime
    source: str
    touches: int = 2

    @property
    def mid(self):
        return (self.low + self.high) / 2


@dataclass
class Market:
    symbol: str
    bars: dict[int, list[Bar]]
    source: str
    realtime: bool
    observed_at: datetime
    previous_session: str | None = None
    valid_until: datetime | None = None
