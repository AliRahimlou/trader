from backend.app.market_data.base import Bar
from backend.app.strategies.base import TradingSignal
from backend.app.strategies.trend_momentum import TrendMomentumStrategy


class AtlasMultiAgentStrategy:
    name = "atlas_multi_agent"

    def __init__(self) -> None:
        self.base = TrendMomentumStrategy()

    def generate(self, ticker: str, bars: list[Bar], spy_bars: list[Bar] | None = None) -> TradingSignal:
        signal = self.base.generate(ticker, bars, spy_bars)
        return TradingSignal(
            ticker=signal.ticker,
            strategy=self.name,
            direction=signal.direction,
            confidence=signal.confidence,
            score=signal.score,
            entry_trigger=signal.entry_trigger,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            trailing_stop=signal.trailing_stop,
            invalidation_condition=signal.invalidation_condition,
            reason=f"ATLAS strategy desk starts from trend momentum: {signal.reason}",
            features=signal.features,
        )
