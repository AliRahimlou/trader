from __future__ import annotations

import argparse
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from alpaca_api import (
    AlpacaConfig,
    close_position,
    fetch_clock,
    fetch_stock_bars,
    format_order_summary,
    get_asset,
    get_order,
    get_position,
    list_orders,
    list_positions,
    period_to_start,
    submit_order,
    wait_for_order_terminal,
)
from backtest_utils import BacktestConfig, check_exit, configure_logging
from live_signals import TradeSignal, generate_break_signal, generate_pullback_signal

LOGGER = logging.getLogger(__name__)
ET_TZ = "America/New_York"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the FVG strategy live on Alpaca paper.")
    parser.add_argument("--symbol", default="SPY", help="Ticker symbol to trade.")
    parser.add_argument(
        "--strategy",
        choices=("break", "pullback"),
        default="break",
        help="Which strategy to run live.",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="Polling interval while waiting for new bars.")
    parser.add_argument("--entry-timeout-seconds", type=int, default=30, help="How long to wait for market orders to fill.")
    parser.add_argument("--minute-lookback", default="3d", help="1-minute bar lookback for each polling cycle.")
    parser.add_argument("--five-minute-lookback", default="15d", help="5-minute bar lookback for each polling cycle.")
    parser.add_argument("--daily-lookback", default="30d", help="Daily bar lookback for each polling cycle.")
    parser.add_argument("--risk-per-trade", type=float, default=100.0, help="Target dollar risk per trade.")
    parser.add_argument("--rr-ratio", type=float, default=2.0, help="Reward:risk ratio for the break strategy.")
    parser.add_argument("--value-per-point", type=float, default=1.0, help="P&L value of a one-point move per share or unit.")
    parser.add_argument("--commission-per-unit", type=float, default=0.0, help="Per-unit commission for realized PnL logging.")
    parser.add_argument("--min-gap-pct", type=float, default=0.0, help="Minimum FVG size as a decimal percentage of price.")
    parser.add_argument("--min-gap-atr", type=float, default=0.0, help="Minimum FVG size as a fraction of ATR(14).")
    parser.add_argument("--no-displacement", action="store_true", help="Disable the middle-candle displacement requirement.")
    parser.add_argument("--max-trades-per-day", type=int, default=2, help="Maximum new entries per trading day.")
    parser.add_argument("--flatten-at", default="15:55", help="ET cutoff for flattening any open position.")
    parser.add_argument("--no-fractional-long", action="store_true", help="Disable fractional sizing on long entries.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without submitting orders.")
    parser.add_argument("--reset-state", action="store_true", help="Delete any existing runner state before starting.")
    parser.add_argument("--state-file", help="Optional path to the JSON state file.")
    parser.add_argument("--alpaca-feed", choices=("iex", "sip", "boats", "overnight"), default=None, help="Override the Alpaca stock market data feed.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    config = build_alpaca_config(args)
    require_paper_account(config)
    strategy_config = BacktestConfig(
        risk_per_trade=args.risk_per_trade,
        rr_ratio=args.rr_ratio,
        value_per_point=args.value_per_point,
        commission_per_unit=args.commission_per_unit,
        min_gap_pct=args.min_gap_pct,
        min_gap_atr=args.min_gap_atr,
        require_displacement=not args.no_displacement,
    )
    state_path = build_state_path(args)
    if args.reset_state and state_path.exists():
        state_path.unlink()
    state = load_state(state_path, symbol=args.symbol, strategy=args.strategy)
    ensure_managed_account_state(config, args.symbol, state)

    LOGGER.info(
        "Starting live paper runner for %s strategy=%s dry_run=%s state=%s",
        args.symbol,
        args.strategy,
        args.dry_run,
        state_path,
    )

    try:
        while True:
            run_cycle(args, config, strategy_config, state, state_path)
            if args.once:
                return
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        LOGGER.info("Runner interrupted by user.")


def run_cycle(
    args: argparse.Namespace,
    alpaca_config: AlpacaConfig,
    strategy_config: BacktestConfig,
    state: dict[str, Any],
    state_path: Path,
) -> None:
    clock = fetch_clock(alpaca_config)
    timestamp = pd.Timestamp(clock["timestamp"]).tz_convert(ET_TZ)
    session_key = str(timestamp.date())
    trim_old_state(state, session_key)

    if not clock.get("is_open"):
        LOGGER.info("Market is closed at %s. Next open: %s", timestamp, clock.get("next_open"))
        save_state(state_path, state)
        return

    market_data = load_recent_market_data(args.symbol, alpaca_config, args)
    latest_bar_time = market_data["1m"].index.max()
    last_processed_bar = parse_ts(state.get("last_processed_bar"))
    if last_processed_bar is not None and latest_bar_time <= last_processed_bar:
        LOGGER.debug("No new 1-minute bar. Latest processed=%s current=%s", last_processed_bar, latest_bar_time)
        save_state(state_path, state)
        return

    LOGGER.info("Processing bar %s", latest_bar_time)
    synchronize_active_trade(alpaca_config, args.symbol, state)

    if state.get("active_trade"):
        maybe_exit_position(args, alpaca_config, strategy_config, state, latest_bar_time, market_data["1m"])
    elif timestamp.time() >= parse_hhmm(args.flatten_at):
        LOGGER.info("Past flatten cutoff %s ET. No new entries.", args.flatten_at)
    elif state["trade_counts"].get(session_key, 0) >= args.max_trades_per_day:
        LOGGER.info("Max trades reached for %s. Skipping new entries.", session_key)
    else:
        maybe_enter_position(args, alpaca_config, strategy_config, state, market_data)

    state["last_processed_bar"] = latest_bar_time.isoformat()
    save_state(state_path, state)


def maybe_enter_position(
    args: argparse.Namespace,
    alpaca_config: AlpacaConfig,
    strategy_config: BacktestConfig,
    state: dict[str, Any],
    market_data: dict[str, pd.DataFrame],
) -> None:
    signal = build_signal(args.strategy, market_data, strategy_config)
    if signal is None:
        LOGGER.debug("No entry signal.")
        return

    if state.get("last_signal_key") == signal.signal_key:
        LOGGER.debug("Signal %s already processed.", signal.signal_key)
        return

    asset = get_asset(alpaca_config, args.symbol)
    if not asset.get("tradable"):
        LOGGER.warning("Asset %s is not tradable.", args.symbol)
        return

    quantity = determine_order_quantity(signal, asset, strategy_config, allow_fractional_long=not args.no_fractional_long)
    if quantity <= 0:
        LOGGER.info("Signal %s skipped because calculated quantity is zero.", signal.signal_key)
        state["last_signal_key"] = signal.signal_key
        return

    if signal.direction == "short" and not asset.get("shortable"):
        LOGGER.warning("Short signal skipped because %s is not shortable.", args.symbol)
        state["last_signal_key"] = signal.signal_key
        return

    LOGGER.info(
        "Signal %s: %s qty=%s est_entry=%.4f stop=%.4f target=%.4f",
        signal.strategy_name,
        signal.reason,
        quantity,
        signal.estimated_entry_price,
        signal.stop_price,
        signal.target_price,
    )
    state["last_signal_key"] = signal.signal_key

    if args.dry_run:
        LOGGER.info("Dry run enabled. Entry order not submitted.")
        return

    order_side = "buy" if signal.direction == "long" else "sell"
    order = submit_order(
        alpaca_config,
        symbol=args.symbol,
        side=order_side,
        order_type="market",
        time_in_force="day",
        qty=quantity,
        client_order_id=build_client_order_id(signal.strategy_id),
    )
    LOGGER.info("Entry submitted: %s", format_order_summary(order))
    final_order = wait_for_order_terminal(
        alpaca_config,
        order["id"],
        timeout_seconds=args.entry_timeout_seconds,
    )
    LOGGER.info("Entry final: %s", format_order_summary(final_order))
    if final_order.get("status") != "filled":
        LOGGER.warning("Entry order did not fill. Final status=%s", final_order.get("status"))
        return

    entry_fill_price = float(final_order["filled_avg_price"])
    filled_qty = float(final_order["filled_qty"])
    active_trade = create_active_trade(signal, entry_fill_price, filled_qty, strategy_config, final_order["id"])
    state["active_trade"] = active_trade
    session_key = str(signal.signal_time.date())
    state["trade_counts"][session_key] = state["trade_counts"].get(session_key, 0) + 1


def maybe_exit_position(
    args: argparse.Namespace,
    alpaca_config: AlpacaConfig,
    strategy_config: BacktestConfig,
    state: dict[str, Any],
    latest_bar_time: pd.Timestamp,
    minute_df: pd.DataFrame,
) -> None:
    active_trade = state.get("active_trade")
    if not active_trade:
        return

    latest_bar = minute_df.iloc[-1]
    direction = active_trade["direction"]
    stop_price = float(active_trade["stop_price"])
    target_price = float(active_trade["target_price"])
    exit_price, reason = check_exit(latest_bar, direction, stop_price, target_price)

    if exit_price is None and latest_bar_time.time() >= parse_hhmm(args.flatten_at):
        reason = "flatten"

    if reason is None:
        LOGGER.debug("Open %s position still active.", direction)
        return

    LOGGER.info(
        "Exit triggered for %s because %s. stop=%.4f target=%.4f",
        args.symbol,
        reason,
        stop_price,
        target_price,
    )

    if args.dry_run:
        LOGGER.info("Dry run enabled. Exit order not submitted.")
        return

    close_order = close_position(alpaca_config, args.symbol)
    LOGGER.info("Close submitted: %s", format_order_summary(close_order))
    final_order = wait_for_order_terminal(
        alpaca_config,
        close_order["id"],
        timeout_seconds=args.entry_timeout_seconds,
    )
    LOGGER.info("Close final: %s", format_order_summary(final_order))
    if final_order.get("status") != "filled":
        LOGGER.warning("Close order did not fill. Final status=%s", final_order.get("status"))
        return

    exit_fill_price = float(final_order["filled_avg_price"])
    exit_qty = float(final_order["filled_qty"])
    trade_record = finalize_trade(active_trade, exit_fill_price, exit_qty, latest_bar_time, reason, strategy_config)
    state.setdefault("trade_log", []).append(trade_record)
    state["active_trade"] = None


def build_signal(
    strategy_name: str,
    market_data: dict[str, pd.DataFrame],
    strategy_config: BacktestConfig,
) -> TradeSignal | None:
    if strategy_name == "break":
        return generate_break_signal(market_data["1m"], market_data["5m"], config=strategy_config)
    return generate_pullback_signal(market_data["1m"], market_data["1d"], config=strategy_config)


def determine_order_quantity(
    signal: TradeSignal,
    asset: dict[str, Any],
    strategy_config: BacktestConfig,
    *,
    allow_fractional_long: bool,
) -> float:
    if signal.direction == "long" and allow_fractional_long and asset.get("fractionable"):
        stop_distance = abs(signal.estimated_entry_price - signal.stop_price)
        if stop_distance <= 0 or strategy_config.value_per_point <= 0:
            return 0.0
        raw_qty = strategy_config.risk_per_trade / (stop_distance * strategy_config.value_per_point)
        return float(format(raw_qty, ".6f"))
    return signal.quantity


def create_active_trade(
    signal: TradeSignal,
    entry_fill_price: float,
    filled_qty: float,
    strategy_config: BacktestConfig,
    order_id: str,
) -> dict[str, Any]:
    target_price = signal.target_price
    if signal.strategy_id == "break":
        if signal.direction == "long":
            target_price = entry_fill_price + (entry_fill_price - signal.stop_price) * strategy_config.rr_ratio
        else:
            target_price = entry_fill_price - (signal.stop_price - entry_fill_price) * strategy_config.rr_ratio

    return {
        "strategy_id": signal.strategy_id,
        "strategy_name": signal.strategy_name,
        "direction": signal.direction,
        "entry_time": signal.signal_time.isoformat(),
        "entry_fill_price": entry_fill_price,
        "quantity": filled_qty,
        "stop_price": signal.stop_price,
        "target_price": target_price,
        "entry_order_id": order_id,
        "signal_key": signal.signal_key,
        "reason": signal.reason,
    }


def finalize_trade(
    active_trade: dict[str, Any],
    exit_fill_price: float,
    exit_qty: float,
    exit_time: pd.Timestamp,
    reason: str,
    strategy_config: BacktestConfig,
) -> dict[str, Any]:
    quantity = min(float(active_trade["quantity"]), exit_qty)
    entry_fill_price = float(active_trade["entry_fill_price"])
    if active_trade["direction"] == "long":
        gross_points = exit_fill_price - entry_fill_price
    else:
        gross_points = entry_fill_price - exit_fill_price
    gross_pnl = gross_points * quantity * strategy_config.value_per_point
    commissions = quantity * strategy_config.commission_per_unit * 2
    net_pnl = gross_pnl - commissions
    return {
        **active_trade,
        "exit_time": exit_time.isoformat(),
        "exit_fill_price": exit_fill_price,
        "exit_quantity": exit_qty,
        "gross_pnl": gross_pnl,
        "commissions": commissions,
        "net_pnl": net_pnl,
        "reason": reason,
    }


def synchronize_active_trade(alpaca_config: AlpacaConfig, symbol: str, state: dict[str, Any]) -> None:
    active_trade = state.get("active_trade")
    if not active_trade:
        return

    try:
        position = get_position(alpaca_config, symbol)
    except RuntimeError:
        LOGGER.warning("No open broker position for %s. Clearing local active trade state.", symbol)
        state["active_trade"] = None
        return

    active_trade["quantity"] = float(position["qty"])


def ensure_managed_account_state(alpaca_config: AlpacaConfig, symbol: str, state: dict[str, Any]) -> None:
    active_trade = state.get("active_trade")
    open_positions = [position for position in list_positions(alpaca_config) if position["symbol"] == symbol.upper()]
    open_orders = [order for order in list_orders(alpaca_config, status="open", limit=100) if order["symbol"] == symbol.upper()]

    if active_trade is None and open_positions:
        raise SystemExit(
            f"Refusing to start because {symbol} already has an unmanaged open position. "
            "Close it manually or reset the state file if this runner should adopt it."
        )
    if active_trade is None and open_orders:
        raise SystemExit(
            f"Refusing to start because {symbol} already has unmanaged open orders. "
            "Cancel them manually before running this script."
        )


def load_recent_market_data(symbol: str, alpaca_config: AlpacaConfig, args: argparse.Namespace) -> dict[str, pd.DataFrame]:
    end = pd.Timestamp.now(tz="UTC")
    return {
        "1m": fetch_stock_bars(
            symbol,
            "1m",
            period_to_start(end, args.minute_lookback),
            end,
            config=alpaca_config,
        ),
        "5m": fetch_stock_bars(
            symbol,
            "5m",
            period_to_start(end, args.five_minute_lookback),
            end,
            config=alpaca_config,
        ),
        "1d": fetch_stock_bars(
            symbol,
            "1d",
            period_to_start(end, args.daily_lookback),
            end,
            config=alpaca_config,
        ),
    }


def build_alpaca_config(args: argparse.Namespace) -> AlpacaConfig:
    config = AlpacaConfig.from_env()
    if args.alpaca_feed:
        config = AlpacaConfig(
            api_key_id=config.api_key_id,
            api_secret_key=config.api_secret_key,
            trading_base_url=config.trading_base_url,
            data_base_url=config.data_base_url,
            feed=args.alpaca_feed,
        )
    return config


def require_paper_account(config: AlpacaConfig) -> None:
    if "paper-api.alpaca.markets" not in config.trading_base_url:
        raise SystemExit(
            f"Refusing to submit trades because APCA_API_BASE_URL is {config.trading_base_url}, not paper."
        )


def build_state_path(args: argparse.Namespace) -> Path:
    if args.state_file:
        return Path(args.state_file).expanduser().resolve()
    filename = f".live_state_{args.symbol.lower()}_{args.strategy}.json"
    return Path.cwd() / filename


def load_state(path: Path, *, symbol: str, strategy: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "symbol": symbol.upper(),
            "strategy": strategy,
            "last_processed_bar": None,
            "last_signal_key": None,
            "trade_counts": {},
            "trade_log": [],
            "active_trade": None,
        }

    state = json.loads(path.read_text())
    if state.get("symbol") != symbol.upper() or state.get("strategy") != strategy:
        raise SystemExit(
            f"State file {path} belongs to symbol={state.get('symbol')} strategy={state.get('strategy')}, "
            f"not symbol={symbol.upper()} strategy={strategy}."
        )
    state.setdefault("last_processed_bar", None)
    state.setdefault("last_signal_key", None)
    state.setdefault("trade_counts", {})
    state.setdefault("trade_log", [])
    state.setdefault("active_trade", None)
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def trim_old_state(state: dict[str, Any], session_key: str) -> None:
    state["trade_counts"] = {key: value for key, value in state.get("trade_counts", {}).items() if key >= session_key}


def parse_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    return pd.Timestamp(value)


def parse_hhmm(value: str) -> Any:
    time.strptime(value, "%H:%M")
    return pd.Timestamp(f"2000-01-01 {value}", tz=ET_TZ).time()


def build_client_order_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:18]}"


if __name__ == "__main__":
    main()
