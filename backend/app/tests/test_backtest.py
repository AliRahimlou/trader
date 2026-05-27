from backend.app.backtesting.engine import BacktestEngine


def test_backtest_produces_metrics_without_future_access():
    result = BacktestEngine().run(
        ticker="AAPL",
        auto_pick=False,
        strategy="trend_momentum",
        risk_level="balanced",
        capital=10_000,
        days=160,
    )
    assert "total_return" in result.metrics
    assert "max_drawdown" in result.metrics
    assert result.equity_curve
