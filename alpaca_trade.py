from __future__ import annotations

import argparse
import time
import uuid

from alpaca_api import (
    AlpacaConfig,
    cancel_order,
    close_position,
    fetch_account,
    fetch_clock,
    format_account_summary,
    format_order_summary,
    format_position_summary,
    get_asset,
    get_order,
    get_position,
    list_orders,
    list_positions,
    submit_order,
    wait_for_order_terminal,
)
from backtest_utils import configure_logging

SMOKE_TEST_CANDIDATES = ["SPY", "AAPL", "MSFT", "QQQ", "IWM"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Alpaca paper trading.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("account", help="Show account summary.")
    subparsers.add_parser("clock", help="Show market clock.")

    positions_parser = subparsers.add_parser("positions", help="List open positions.")
    positions_parser.add_argument("--symbol", help="Show just one position.")

    orders_parser = subparsers.add_parser("orders", help="List orders.")
    orders_parser.add_argument("--status", default="open", help="Order status filter.")
    orders_parser.add_argument("--limit", type=int, default=20, help="Max orders to fetch.")

    submit_parser = subparsers.add_parser("submit", help="Submit a paper order.")
    add_common_order_args(submit_parser)
    submit_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        help="Poll order status for N seconds after submit.",
    )

    close_parser = subparsers.add_parser("close", help="Close a position by symbol.")
    close_parser.add_argument("--symbol", required=True, help="Symbol to close.")
    close_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=30,
        help="Poll the generated closing order for N seconds.",
    )

    smoke_parser = subparsers.add_parser(
        "smoke-test",
        help="Submit a tiny paper order, wait for fill, then close it.",
    )
    smoke_parser.add_argument("--symbol", help="Optional symbol override.")
    smoke_parser.add_argument(
        "--notional",
        type=float,
        default=10.0,
        help="Dollar notional for the paper test order.",
    )
    smoke_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=45,
        help="How long to wait for entry and exit orders to fill.",
    )
    smoke_parser.add_argument(
        "--hold-seconds",
        type=float,
        default=2.0,
        help="How long to wait after fill before closing the position.",
    )
    return parser.parse_args()


def add_common_order_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", required=True, help="Ticker symbol.")
    parser.add_argument("--side", choices=("buy", "sell"), required=True, help="Order side.")
    parser.add_argument("--type", dest="order_type", default="market", help="Order type.")
    parser.add_argument("--time-in-force", default="day", help="Time in force.")
    parser.add_argument("--qty", type=float, help="Share quantity.")
    parser.add_argument("--notional", type=float, help="Dollar notional.")
    parser.add_argument("--limit-price", type=float, help="Limit price.")
    parser.add_argument("--stop-price", type=float, help="Stop price.")
    parser.add_argument("--extended-hours", action="store_true", help="Allow extended-hours fill.")


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)
    try:
        config = AlpacaConfig.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.command in {"submit", "close", "smoke-test"}:
        require_paper_account(config)

    if args.command == "account":
        print(format_account_summary(fetch_account(config)))
        return

    if args.command == "clock":
        print(fetch_clock(config))
        return

    if args.command == "positions":
        handle_positions(args, config)
        return

    if args.command == "orders":
        handle_orders(args, config)
        return

    if args.command == "submit":
        handle_submit(args, config)
        return

    if args.command == "close":
        handle_close(args, config)
        return

    if args.command == "smoke-test":
        handle_smoke_test(args, config)
        return


def handle_positions(args: argparse.Namespace, config: AlpacaConfig) -> None:
    if args.symbol:
        try:
            print(format_position_summary(get_position(config, args.symbol)))
        except RuntimeError as exc:
            if "position does not exist" in str(exc):
                print(f"No open position in {args.symbol.upper()}.")
                return
            raise
        return

    positions = list_positions(config)
    if not positions:
        print("No open positions.")
        return
    for position in positions:
        print(format_position_summary(position))


def handle_orders(args: argparse.Namespace, config: AlpacaConfig) -> None:
    orders = list_orders(config, status=args.status, limit=args.limit)
    if not orders:
        print("No matching orders.")
        return
    for order in orders:
        print(format_order_summary(order))


def handle_submit(args: argparse.Namespace, config: AlpacaConfig) -> None:
    order = submit_order(
        config,
        symbol=args.symbol,
        side=args.side,
        order_type=args.order_type,
        time_in_force=args.time_in_force,
        qty=args.qty,
        notional=args.notional,
        limit_price=args.limit_price,
        stop_price=args.stop_price,
        extended_hours=args.extended_hours,
        client_order_id=build_client_order_id("manual"),
    )
    print("Submitted:", format_order_summary(order))
    if args.wait_seconds > 0:
        final_order = wait_for_order_terminal(config, order["id"], timeout_seconds=args.wait_seconds)
        print("Final:", format_order_summary(final_order))


def handle_close(args: argparse.Namespace, config: AlpacaConfig) -> None:
    close_order = close_position(config, args.symbol)
    print("Close submitted:", format_order_summary(close_order))
    if args.wait_seconds > 0:
        final_order = wait_for_order_terminal(config, close_order["id"], timeout_seconds=args.wait_seconds)
        print("Close final:", format_order_summary(final_order))


def handle_smoke_test(args: argparse.Namespace, config: AlpacaConfig) -> None:
    clock = fetch_clock(config)
    if not clock.get("is_open"):
        raise SystemExit(
            "Market is closed, so a day market order may stay accepted until the next session. "
            "Run `alpaca_trade.py clock` and try again during market hours."
        )

    symbol = args.symbol or choose_smoke_test_symbol(config)
    asset = get_asset(config, symbol)
    if not asset.get("tradable"):
        raise SystemExit(f"{symbol} is not tradable in this account.")

    if not asset.get("fractionable"):
        raise SystemExit(f"{symbol} is not fractionable, so the smoke test needs a qty-based order instead.")

    print(f"Smoke test symbol={symbol} notional=${args.notional:.2f}")
    entry_order = submit_order(
        config,
        symbol=symbol,
        side="buy",
        order_type="market",
        time_in_force="day",
        notional=args.notional,
        client_order_id=build_client_order_id("smoke-entry"),
    )
    print("Entry submitted:", format_order_summary(entry_order))

    final_entry = wait_for_order_terminal(config, entry_order["id"], timeout_seconds=args.wait_seconds)
    print("Entry final:", format_order_summary(final_entry))
    if final_entry.get("status") != "filled":
        status = final_entry.get("status")
        if status not in {"canceled", "expired", "rejected"}:
            cancel_order(config, final_entry["id"])
            final_entry = get_order(config, final_entry["id"])
            print("Entry canceled:", format_order_summary(final_entry))
        raise SystemExit(f"Smoke test entry did not fill. Final status={status}")

    time.sleep(args.hold_seconds)
    close_order = close_position(config, symbol)
    print("Exit submitted:", format_order_summary(close_order))

    final_exit = wait_for_order_terminal(config, close_order["id"], timeout_seconds=args.wait_seconds)
    print("Exit final:", format_order_summary(final_exit))
    if final_exit.get("status") != "filled":
        raise SystemExit(f"Smoke test exit did not fill. Final status={final_exit.get('status')}")

    try:
        position = get_position(config, symbol)
    except RuntimeError:
        position = None
    if position is None:
        print("Smoke test complete: position fully closed.")
    else:
        print("Residual position:", format_position_summary(position))


def build_client_order_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:18]}"


def choose_smoke_test_symbol(config: AlpacaConfig) -> str:
    open_positions = {position["symbol"] for position in list_positions(config)}
    open_orders = {order["symbol"] for order in list_orders(config, status="open", limit=100)}
    blocked_symbols = open_positions | open_orders

    for candidate in SMOKE_TEST_CANDIDATES:
        if candidate not in blocked_symbols:
            return candidate
    raise SystemExit(
        "Could not find a clean smoke-test symbol. Pass --symbol explicitly after reviewing your paper account."
    )


def require_paper_account(config: AlpacaConfig) -> None:
    if "paper-api.alpaca.markets" not in config.trading_base_url:
        raise SystemExit(
            f"Refusing to submit trades because APCA_API_BASE_URL is {config.trading_base_url}, not paper."
        )


if __name__ == "__main__":
    main()
