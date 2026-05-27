from backend.app.market_data import indicators
from backend.app.market_data.base import Bar
from backend.app.strategies.base import TradingSignal


class MeanReversionStrategy:
    name = "mean_reversion"

    def generate(self, ticker: str, bars: list[Bar], spy_bars: list[Bar] | None = None) -> TradingSignal:
        values = indicators.closes(bars)
        close = values[-1]
        ma20 = indicators.sma(values, 20) or close
        rsi14 = indicators.rsi(values, 14)
        atr14 = indicators.atr(bars, 14) or close * 0.02
        distance = (ma20 - close) / max(atr14, 0.01)
        score = indicators.clamp((40 - rsi14) / 28 + distance * 0.16)
        direction = "BUY" if rsi14 < 38 and close < ma20 and distance > 0.5 and score >= 0.45 else "WAIT"
        entry = round(close * 1.0005, 2)
        stop = round(close - atr14 * 1.5, 2)
        target = round(max(ma20, close + atr14 * 1.7), 2)
        return TradingSignal(
            ticker=ticker,
            strategy=self.name,
            direction=direction,
            confidence=score,
            score=score,
            entry_trigger=entry,
            stop_loss=stop,
            take_profit=target,
            trailing_stop=None,
            invalidation_condition="Price fails to reclaim mean or closes below stop.",
            reason="Oversold move offers a mean-reversion setup." if direction == "BUY" else "Mean-reversion edge is not strong enough.",
            features={"ma20": ma20, "rsi": rsi14, "atr": atr14, "mean_distance_atr": distance},
        )
