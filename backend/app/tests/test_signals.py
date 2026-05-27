from backend.app.market_data.live import DemoMarketDataProvider
from backend.app.strategies.breakout import BreakoutStrategy
from backend.app.strategies.mean_reversion import MeanReversionStrategy
from backend.app.strategies.trend_momentum import TrendMomentumStrategy


def test_strategy_signals_have_defined_exit_rules():
    market_data = DemoMarketDataProvider()
    bars = market_data.get_historical_bars("AAPL", days=140)
    for strategy in [TrendMomentumStrategy(), MeanReversionStrategy(), BreakoutStrategy()]:
        signal = strategy.generate("AAPL", bars)
        assert signal.direction in {"BUY", "WAIT"}
        assert signal.entry_trigger is not None
        assert signal.stop_loss is not None
        assert signal.take_profit is not None
        assert signal.invalidation_condition
