from backend.app.backtesting.engine import BacktestEngine


def run_walk_forward(
    *,
    ticker: str | None,
    auto_pick: bool,
    strategy: str,
    risk_level: str,
    capital: float,
) -> dict:
    engine = BacktestEngine()
    windows = [140, 180, 220]
    results = [
        engine.run(ticker=ticker, auto_pick=auto_pick, strategy=strategy, risk_level=risk_level, capital=capital, days=days).metrics
        for days in windows
    ]
    return {"windows": windows, "metrics": results}
