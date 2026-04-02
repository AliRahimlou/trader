from __future__ import annotations

import argparse
from dataclasses import replace

import pandas as pd

from alpaca_api import AlpacaConfig, fetch_account, fetch_market_data, format_account_summary
from backtest_utils import BacktestConfig, configure_logging, download_market_data, summarize_trades
from fvgBreak import run_strategy_video1
from fvgPullback import run_strategy_video2
from massive_api import MassiveConfig, fetch_market_data as fetch_massive_market_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run simple FVG backtests.")
    parser.add_argument("--symbol", default="SPY", help="Ticker symbol, e.g. SPY or ES=F")
    parser.add_argument(
        "--source",
        choices=("yfinance", "alpaca", "massive"),
        default="yfinance",
        help="Market data source.",
    )
    parser.add_argument(
        "--strategy",
        choices=("break", "pullback", "both"),
        default="both",
        help="Which strategy to run.",
    )
    parser.add_argument("--minute-period", default="7d", help="1-minute lookback for yfinance.")
    parser.add_argument("--five-minute-period", default="60d", help="5-minute lookback for yfinance.")
    parser.add_argument("--daily-period", default="1y", help="Daily lookback for yfinance.")
    parser.add_argument("--risk-per-trade", type=float, default=100.0, help="Dollar risk target per trade.")
    parser.add_argument("--rr-ratio", type=float, default=2.0, help="Reward:risk ratio for the break strategy.")
    parser.add_argument(
        "--value-per-point",
        type=float,
        default=1.0,
        help="P&L value of a one-point move per unit. Use 50 for ES, 20 for NQ micro, 1 for stocks.",
    )
    parser.add_argument(
        "--commission-per-unit",
        type=float,
        default=0.0,
        help="Commission per unit, per side.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=0.0,
        help="Slippage applied to entry and exit fills in basis points.",
    )
    parser.add_argument(
        "--min-gap-pct",
        type=float,
        default=0.0,
        help="Minimum FVG size as a decimal percentage of price.",
    )
    parser.add_argument(
        "--min-gap-atr",
        type=float,
        default=0.0,
        help="Minimum FVG size as a fraction of ATR(14).",
    )
    parser.add_argument(
        "--no-displacement",
        action="store_true",
        help="Disable the middle-candle displacement requirement in FVG detection.",
    )
    parser.add_argument(
        "--check-account",
        action="store_true",
        help="For Alpaca, print account details before running.",
    )
    parser.add_argument(
        "--account-only",
        action="store_true",
        help="For Alpaca, print account details and exit without running backtests.",
    )
    parser.add_argument(
        "--alpaca-feed",
        choices=("iex", "sip", "boats", "overnight"),
        default=None,
        help="Override the Alpaca stock market data feed.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    parser.add_argument(
        "--max-rows",
        type=int,
        default=10,
        help="How many recent trades to print per strategy.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)

    config = BacktestConfig(
        risk_per_trade=args.risk_per_trade,
        rr_ratio=args.rr_ratio,
        value_per_point=args.value_per_point,
        commission_per_unit=args.commission_per_unit,
        slippage_bps=args.slippage_bps,
        min_gap_pct=args.min_gap_pct,
        min_gap_atr=args.min_gap_atr,
        require_displacement=not args.no_displacement,
    )
    try:
        alpaca_config = build_alpaca_config(args) if args.source == "alpaca" else None
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        massive_config = build_massive_config(args) if args.source == "massive" else None
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if alpaca_config is not None and (args.check_account or args.account_only):
        account = fetch_account(alpaca_config)
        print(format_account_summary(account))
        if args.account_only:
            return

    market_data = load_market_data(args, alpaca_config=alpaca_config, massive_config=massive_config)

    if args.strategy in {"break", "both"}:
        report_strategy(
            "Opening Range + FVG",
            run_strategy_video1(market_data["1m"], market_data["5m"], config=config),
            args.max_rows,
        )

    if args.strategy in {"pullback", "both"}:
        report_strategy(
            "Daily Sweep + Pullback FVG",
            run_strategy_video2(market_data["1m"], market_data["1d"], config=config),
            args.max_rows,
        )


def load_market_data(
    args: argparse.Namespace,
    *,
    alpaca_config: AlpacaConfig | None,
    massive_config: MassiveConfig | None,
) -> dict[str, pd.DataFrame]:
    if args.source == "alpaca":
        assert alpaca_config is not None
        return fetch_market_data(
            args.symbol,
            minute_period=args.minute_period,
            five_min_period=args.five_minute_period,
            daily_period=args.daily_period,
            config=alpaca_config,
        )

    if args.source == "massive":
        assert massive_config is not None
        if args.check_account or args.account_only:
            raise SystemExit("--check-account and --account-only only apply to --source alpaca")
        return fetch_massive_market_data(
            args.symbol,
            minute_period=args.minute_period,
            five_min_period=args.five_minute_period,
            daily_period=args.daily_period,
            config=massive_config,
        )

    if args.check_account or args.account_only:
        raise SystemExit("--check-account and --account-only require --source alpaca")

    return download_market_data(
        args.symbol,
        minute_period=args.minute_period,
        five_min_period=args.five_minute_period,
        daily_period=args.daily_period,
    )


def build_alpaca_config(args: argparse.Namespace) -> AlpacaConfig:
    config = AlpacaConfig.from_env()
    if args.alpaca_feed:
        config = replace(config, feed=args.alpaca_feed)
    return config


def build_massive_config(args: argparse.Namespace) -> MassiveConfig:
    return MassiveConfig.from_env()


def report_strategy(name: str, trades_df: pd.DataFrame, max_rows: int) -> None:
    summary = summarize_trades(trades_df)
    print(f"\n=== {name} ===")
    print(
        "Trades={trades} Wins={wins} Losses={losses} WinRate={win_rate:.1%} "
        "GrossPnL=${gross_pnl:,.2f} Commissions=${commissions:,.2f} "
        "NetPnL=${net_pnl:,.2f} AvgR={avg_r_multiple:.2f}".format(**summary)
    )
    if trades_df.empty:
        print("No trades triggered.")
        return

    columns = [
        "entry_time",
        "exit_time",
        "direction",
        "entry_price",
        "exit_price",
        "quantity",
        "net_pnl",
        "reason",
    ]
    print(trades_df[columns].tail(max_rows).to_string(index=False))


if __name__ == "__main__":
    main()
