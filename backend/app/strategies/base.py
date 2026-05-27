from dataclasses import dataclass, field
from typing import Protocol

from backend.app.market_data.base import Bar


@dataclass(frozen=True)
class TradingSignal:
    ticker: str
    strategy: str
    direction: str
    confidence: float
    score: float
    entry_trigger: float | None
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    invalidation_condition: str
    reason: str
    features: dict[str, float] = field(default_factory=dict)


class Strategy(Protocol):
    name: str

    def generate(self, ticker: str, bars: list[Bar], spy_bars: list[Bar] | None = None) -> TradingSignal:
        ...


def risk_reward(entry: float | None, stop: float | None, target: float | None) -> float:
    if entry is None or stop is None or target is None:
        return 0.0
    risk = entry - stop
    reward = target - entry
    if risk <= 0:
        return 0.0
    return max(0.0, reward / risk)
