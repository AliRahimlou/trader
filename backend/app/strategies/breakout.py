from backend.app.market_data import indicators
from backend.app.market_data.base import Bar
from backend.app.strategies.base import TradingSignal


class BreakoutStrategy:
    name = "breakout"

    def generate(self, ticker: str, bars: list[Bar], spy_bars: list[Bar] | None = None) -> TradingSignal:
        values = indicators.closes(bars)
        close = values[-1]
        high20 = indicators.rolling_high(bars[:-1], 20) if len(bars) > 21 else indicators.rolling_high(bars, 20)
        atr14 = indicators.atr(bars, 14) or close * 0.02
        expansion = indicators.volume_expansion(bars, 20)
        breakout_strength = (close - high20) / max(atr14, 0.01)
        score = indicators.clamp(0.45 + breakout_strength * 0.25 + (expansion - 1.0) * 0.25)
        direction = "BUY" if close >= high20 * 0.995 and expansion >= 1.05 and score >= 0.55 else "WAIT"
        entry = round(max(close, high20) * 1.001, 2)
        stop = round(close - atr14 * 1.6, 2)
        target = round(close + atr14 * 2.8, 2)
        return TradingSignal(
            ticker=ticker,
            strategy=self.name,
            direction=direction,
            confidence=score,
            score=score,
            entry_trigger=entry,
            stop_loss=stop,
            take_profit=target,
            trailing_stop=round(close - atr14 * 1.1, 2),
            invalidation_condition="Breakout level fails or volume dries up.",
            reason="Price is pressing a breakout with volume support." if direction == "BUY" else "Breakout confirmation is not present.",
            features={"high20": high20, "atr": atr14, "volume_expansion": expansion, "breakout_strength": breakout_strength},
        )
