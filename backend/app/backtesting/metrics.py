from math import sqrt
from statistics import mean, pstdev


def max_drawdown(equity_curve: list[dict]) -> float:
    peak = None
    worst = 0.0
    for point in equity_curve:
        equity = float(point["equity"])
        peak = equity if peak is None else max(peak, equity)
        if peak:
            worst = min(worst, equity / peak - 1)
    return round(worst, 4)


def calculate_metrics(starting_capital: float, equity_curve: list[dict], trades: list[dict]) -> dict:
    final_equity = equity_curve[-1]["equity"] if equity_curve else starting_capital
    returns = [point.get("return", 0.0) for point in equity_curve if "return" in point]
    wins = [trade for trade in trades if trade.get("pnl", 0) > 0]
    losses = [trade for trade in trades if trade.get("pnl", 0) < 0]
    gross_win = sum(trade["pnl"] for trade in wins)
    gross_loss = abs(sum(trade["pnl"] for trade in losses))
    sharpe = 0.0
    if len(returns) > 2 and pstdev(returns) > 0:
        sharpe = mean(returns) / pstdev(returns) * sqrt(252)
    return {
        "total_return": round(final_equity / starting_capital - 1, 4),
        "max_drawdown": max_drawdown(equity_curve),
        "win_rate": round(len(wins) / len(trades), 4) if trades else 0.0,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else (round(gross_win, 3) if gross_win else 0.0),
        "sharpe": round(sharpe, 3),
        "number_of_trades": len(trades),
        "average_trade": round(mean([trade.get("pnl", 0.0) for trade in trades]), 2) if trades else 0.0,
        "exposure_time": round(sum(1 for point in equity_curve if point.get("in_position")) / len(equity_curve), 4)
        if equity_curve
        else 0.0,
    }
