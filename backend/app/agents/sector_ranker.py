from dataclasses import dataclass

from backend.app.market_data import indicators
from backend.app.market_data.base import MarketDataProvider


@dataclass(frozen=True)
class SectorStrength:
    sector_etf: str
    score: float
    relative_strength: float
    reason: str


class SectorRankerAgent:
    def __init__(self, market_data: MarketDataProvider) -> None:
        self.market_data = market_data

    def evaluate(self, sector_etf: str) -> SectorStrength:
        sector_bars = self.market_data.get_historical_bars(sector_etf, days=90)
        spy_bars = self.market_data.get_historical_bars("SPY", days=90)
        sector_roc = indicators.rate_of_change(indicators.closes(sector_bars), 20)
        spy_roc = indicators.rate_of_change(indicators.closes(spy_bars), 20)
        relative = sector_roc - spy_roc
        score = indicators.clamp(0.5 + relative * 6)
        if relative > 0.01:
            reason = f"{sector_etf} is outperforming SPY over 20 sessions."
        elif relative < -0.01:
            reason = f"{sector_etf} is lagging SPY over 20 sessions."
        else:
            reason = f"{sector_etf} is moving roughly in line with SPY."
        return SectorStrength(sector_etf, score, relative, reason)
