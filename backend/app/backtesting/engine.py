from dataclasses import dataclass

from backend.app.backtesting.metrics import calculate_metrics
from backend.app.market_data.historical import DemoHistoricalMarketData
from backend.app.market_data.universe import get_tradable_universe
from backend.app.risk.position_sizing import calculate_position_size
from backend.app.strategies.ranking import _strategy_signal


@dataclass(frozen=True)
class BacktestResult:
    metrics: dict
    equity_curve: list[dict]
    trades: list[dict]


class BacktestEngine:
    def __init__(self) -> None:
        self.market_data = DemoHistoricalMarketData()

    def run(
        self,
        *,
        ticker: str | None,
        auto_pick: bool,
        strategy: str,
        risk_level: str,
        capital: float,
        days: int = 220,
        slippage_bps: float = 5,
        commission_per_share: float = 0.0,
    ) -> BacktestResult:
        symbols = [asset.ticker for asset in get_tradable_universe()[:8]] if auto_pick else [ticker or "SPY"]
        bars_by_symbol = {symbol: self.market_data.get_historical_bars(symbol, days=days) for symbol in symbols}
        spy_bars = self.market_data.get_historical_bars("SPY", days=days)
        cash = capital
        equity = capital
        position: dict | None = None
        equity_curve: list[dict] = []
        trades: list[dict] = []

        for index in range(60, days - 1):
            previous_equity = equity
            if position:
                bar = bars_by_symbol[position["ticker"]][index]
                exit_price = None
                exit_reason = None
                if bar.low <= position["stop_loss"]:
                    exit_price = position["stop_loss"]
                    exit_reason = "stop_loss"
                elif bar.high >= position["take_profit"]:
                    exit_price = position["take_profit"]
                    exit_reason = "take_profit"
                if exit_price is not None:
                    fill_price = exit_price * (1 - slippage_bps / 10_000)
                    pnl = (fill_price - position["entry_price"]) * position["qty"] - commission_per_share * position["qty"]
                    cash += position["qty"] * fill_price
                    trades.append(
                        {
                            "ticker": position["ticker"],
                            "entry_date": position["entry_date"],
                            "exit_date": bar.timestamp.isoformat(),
                            "entry_price": round(position["entry_price"], 2),
                            "exit_price": round(fill_price, 2),
                            "qty": position["qty"],
                            "pnl": round(pnl, 2),
                            "reason": exit_reason,
                        }
                    )
                    position = None

            if position is None:
                best_signal = None
                best_symbol = None
                for symbol in symbols:
                    history = bars_by_symbol[symbol][: index + 1]
                    spy_history = spy_bars[: index + 1]
                    signal = _strategy_signal(strategy, symbol, history, spy_history)
                    if signal.direction == "BUY" and (best_signal is None or signal.score > best_signal.score):
                        best_signal = signal
                        best_symbol = symbol
                if best_signal and best_symbol:
                    next_open = bars_by_symbol[best_symbol][index + 1].open
                    fill_price = next_open * (1 + slippage_bps / 10_000)
                    sizing = calculate_position_size(
                        entry_price=fill_price,
                        stop_loss=best_signal.stop_loss or 0,
                        capital_allocation=capital,
                        account_equity=equity,
                        buying_power=cash,
                        risk_level=risk_level,
                    )
                    if sizing.shares > 0 and sizing.notional <= cash:
                        cash -= sizing.shares * fill_price
                        position = {
                            "ticker": best_symbol,
                            "entry_date": bars_by_symbol[best_symbol][index + 1].timestamp.isoformat(),
                            "entry_price": fill_price,
                            "qty": sizing.shares,
                            "stop_loss": best_signal.stop_loss,
                            "take_profit": best_signal.take_profit,
                        }

            mark_value = 0.0
            if position:
                mark = bars_by_symbol[position["ticker"]][index].close
                mark_value = position["qty"] * mark
            equity = cash + mark_value
            equity_curve.append(
                {
                    "timestamp": spy_bars[index].timestamp.isoformat(),
                    "equity": round(equity, 2),
                    "return": round(equity / previous_equity - 1, 5) if previous_equity else 0,
                    "in_position": position is not None,
                }
            )

        return BacktestResult(calculate_metrics(capital, equity_curve, trades), equity_curve, trades)
