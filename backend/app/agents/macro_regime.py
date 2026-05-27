from dataclasses import dataclass

from backend.app.market_data import indicators
from backend.app.market_data.base import MarketDataProvider


@dataclass(frozen=True)
class MacroRegime:
    label: str
    score: float
    reason: str


class MacroRegimeAgent:
    def __init__(self, market_data: MarketDataProvider) -> None:
        self.market_data = market_data

    def evaluate(self) -> MacroRegime:
        bars = self.market_data.get_historical_bars("SPY", days=120)
        values = indicators.closes(bars)
        ma20 = indicators.sma(values, 20) or values[-1]
        ma50 = indicators.sma(values, 50) or values[-1]
        roc20 = indicators.rate_of_change(values, 20)
        if ma20 > ma50 and roc20 > 0.01:
            return MacroRegime("risk_on", 0.85, "SPY trend and 20-day momentum are positive.")
        if ma20 < ma50 and roc20 < -0.02:
            return MacroRegime("risk_off", 0.25, "SPY trend and 20-day momentum are negative.")
        return MacroRegime("neutral", 0.55, "Broad market regime is mixed.")
