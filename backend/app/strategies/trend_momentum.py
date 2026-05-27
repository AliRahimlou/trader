from backend.app.market_data import indicators
from backend.app.market_data.base import Bar
from backend.app.strategies.base import TradingSignal


class TrendMomentumStrategy:
    name = "trend_momentum"

    def generate(self, ticker: str, bars: list[Bar], spy_bars: list[Bar] | None = None) -> TradingSignal:
        values = indicators.closes(bars)
        close = values[-1]
        ma20 = indicators.sma(values, 20) or close
        ma50 = indicators.sma(values, 50) or close
        roc20 = indicators.rate_of_change(values, 20)
        rsi14 = indicators.rsi(values, 14)
        atr14 = indicators.atr(bars, 14) or close * 0.02
        volume_score = indicators.clamp((indicators.volume_expansion(bars, 20) - 0.8) / 1.4)
        trend_score = indicators.clamp((ma20 / ma50 - 0.98) * 12)
        momentum_score = indicators.clamp((roc20 + 0.04) * 6)
        score = indicators.clamp(0.45 * trend_score + 0.35 * momentum_score + 0.2 * volume_score)
        direction = "BUY" if ma20 > ma50 and roc20 > 0 and 42 <= rsi14 <= 76 and score >= 0.52 else "WAIT"
        entry = round(close * 1.001, 2)
        stop = round(close - atr14 * 1.8, 2)
        target = round(close + atr14 * 3.0, 2)
        return TradingSignal(
            ticker=ticker,
            strategy=self.name,
            direction=direction,
            confidence=score,
            score=score,
            entry_trigger=entry,
            stop_loss=stop,
            take_profit=target,
            trailing_stop=round(close - atr14 * 1.2, 2),
            invalidation_condition="20-day average loses the 50-day average or RSI becomes overextended.",
            reason="Trend momentum favors a long setup." if direction == "BUY" else "Trend momentum is not aligned yet.",
            features={"ma20": ma20, "ma50": ma50, "roc20": roc20, "rsi": rsi14, "atr": atr14},
        )
