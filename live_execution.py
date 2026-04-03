from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from alpaca_api import (
    AlpacaConfig,
    close_position,
    format_order_summary,
    get_order,
    get_position,
    list_orders,
    list_positions,
    submit_order,
    wait_for_order_terminal,
)
from backtest_utils import BacktestConfig
from live_logging import StructuredLogger
from live_scheduler import to_et_timestamp
from strategy_signals import StrategySignal

PENDING_ORDER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "replaced",
}


@dataclass
class ActiveTrade:
    symbol: str
    strategy_id: str
    strategy_name: str
    signal_key: str
    signal_time: str
    direction: str
    reason: str
    exit_mode: str
    status: str
    requested_qty: float
    filled_qty: float
    entry_reference_price: float
    entry_fill_price: float | None
    stop_price: float
    target_price: float
    entry_order_id: str
    entry_client_order_id: str | None
    entry_order_class: str
    take_profit_order_id: str | None = None
    stop_loss_order_id: str | None = None
    exit_order_id: str | None = None
    exit_client_order_id: str | None = None
    exit_reason: str | None = None
    opened_at: str | None = None
    updated_at: str | None = None
    trade_counted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TradeClosure:
    active_trade: ActiveTrade
    closed_at: str
    exit_price: float
    exit_qty: float
    exit_reason: str
    gross_pnl: float
    commissions: float
    net_pnl: float

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.active_trade.to_dict(),
            "status": "closed",
            "closed_at": self.closed_at,
            "exit_price": self.exit_price,
            "exit_qty": self.exit_qty,
            "exit_reason": self.exit_reason,
            "gross_pnl": self.gross_pnl,
            "commissions": self.commissions,
            "net_pnl": self.net_pnl,
        }


def ensure_paper_account(alpaca_config: AlpacaConfig) -> None:
    if "paper-api.alpaca.markets" not in alpaca_config.trading_base_url:
        raise RuntimeError(
            f"Refusing to trade because APCA_API_BASE_URL is {alpaca_config.trading_base_url}, not paper."
        )


def ensure_no_unmanaged_broker_state(
    alpaca_config: AlpacaConfig,
    *,
    symbol: str,
    active_trade: dict[str, Any] | None,
) -> None:
    open_positions = [p for p in list_positions(alpaca_config) if p["symbol"] == symbol]
    open_orders = [o for o in list_orders(alpaca_config, status="open", limit=100) if o["symbol"] == symbol]
    if active_trade is None and open_positions:
        raise RuntimeError(
            f"Found an unmanaged open position in {symbol}. Close it manually before starting the runner."
        )
    if active_trade is None and open_orders:
        raise RuntimeError(
            f"Found unmanaged open orders in {symbol}. Cancel them manually before starting the runner."
        )


def ensure_no_unmanaged_broker_state_multi(
    alpaca_config: AlpacaConfig,
    *,
    active_trades: dict[str, dict[str, Any]],
) -> None:
    tracked_symbols = {symbol.upper() for symbol in active_trades}
    unmanaged_positions = [
        position["symbol"]
        for position in list_positions(alpaca_config)
        if position["symbol"].upper() not in tracked_symbols
    ]
    unmanaged_orders = [
        order["symbol"]
        for order in list_orders(alpaca_config, status="open", limit=100)
        if order["symbol"].upper() not in tracked_symbols
    ]
    if unmanaged_positions:
        raise RuntimeError(
            "Found unmanaged open positions in {}. Close them manually before starting the runner.".format(
                ", ".join(sorted(unmanaged_positions))
            )
        )
    if unmanaged_orders:
        raise RuntimeError(
            "Found unmanaged open orders in {}. Cancel them manually before starting the runner.".format(
                ", ".join(sorted(unmanaged_orders))
            )
        )


def submit_entry(
    alpaca_config: AlpacaConfig,
    signal: StrategySignal,
    *,
    symbol: str,
    qty: float,
    exit_mode: str,
    entry_timeout_seconds: int,
    logger: StructuredLogger,
) -> ActiveTrade:
    order_class = "simple"
    take_profit = None
    stop_loss = None
    if exit_mode == "bracket":
        order_class = "bracket"
        take_profit = {"limit_price": signal.target_price}
        stop_loss = {"stop_price": signal.stop_price}

    side = "buy" if signal.direction == "long" else "sell"
    entry_order = submit_order(
        alpaca_config,
        symbol=symbol,
        side=side,
        order_type="market",
        time_in_force="day" if exit_mode == "in_process" else "gtc",
        qty=qty,
        client_order_id=f"{signal.strategy_id}-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d%H%M%S')}",
        order_class=order_class,
        take_profit=take_profit,
        stop_loss=stop_loss,
    )
    logger.emit(
        "entry_submitted",
        message=f"Submitted entry order: {format_order_summary(entry_order)}",
        symbol=symbol,
        strategy=signal.strategy_id,
        signal_key=signal.signal_key,
        qty=qty,
        exit_mode=exit_mode,
        entry_order_id=entry_order["id"],
    )
    final_order = wait_for_order_terminal(
        alpaca_config,
        entry_order["id"],
        timeout_seconds=entry_timeout_seconds,
    )
    logger.emit(
        "entry_order_update",
        message=f"Entry order update: {format_order_summary(final_order)}",
        symbol=symbol,
        strategy=signal.strategy_id,
        signal_key=signal.signal_key,
        status=final_order.get("status"),
        order_id=final_order["id"],
    )

    active_trade = ActiveTrade(
        symbol=symbol,
        strategy_id=signal.strategy_id,
        strategy_name=signal.strategy_name,
        signal_key=signal.signal_key,
        signal_time=signal.signal_time.isoformat(),
        direction=signal.direction,
        reason=signal.reason,
        exit_mode=exit_mode,
        status="pending_entry",
        requested_qty=qty,
        filled_qty=float(final_order.get("filled_qty") or 0),
        entry_reference_price=signal.entry_reference_price,
        entry_fill_price=float(final_order["filled_avg_price"]) if final_order.get("filled_avg_price") else None,
        stop_price=signal.stop_price,
        target_price=signal.target_price,
        entry_order_id=final_order["id"],
        entry_client_order_id=final_order.get("client_order_id"),
        entry_order_class=order_class,
        updated_at=pd.Timestamp.now(tz="UTC").isoformat(),
    )
    sync_trade_from_entry_order(alpaca_config, active_trade, logger=logger)
    return active_trade


def sync_trade_from_entry_order(
    alpaca_config: AlpacaConfig,
    active_trade: ActiveTrade,
    *,
    logger: StructuredLogger,
) -> None:
    order = get_order(alpaca_config, active_trade.entry_order_id, nested=True)
    active_trade.filled_qty = float(order.get("filled_qty") or active_trade.filled_qty or 0)
    active_trade.entry_fill_price = (
        float(order["filled_avg_price"]) if order.get("filled_avg_price") else active_trade.entry_fill_price
    )
    active_trade.updated_at = pd.Timestamp.now(tz="UTC").isoformat()

    for leg in order.get("legs", []) or []:
        if leg.get("type") == "limit":
            active_trade.take_profit_order_id = leg["id"]
        elif leg.get("type") in {"stop", "stop_limit"} or leg.get("stop_price") is not None:
            active_trade.stop_loss_order_id = leg["id"]

    if order.get("status") in {"filled", "partially_filled"}:
        try:
            position = get_position(alpaca_config, active_trade.symbol)
        except RuntimeError:
            position = None
        if position is not None:
            active_trade.status = "open"
            active_trade.filled_qty = float(position["qty"])
            active_trade.entry_fill_price = float(position["avg_entry_price"])
            active_trade.opened_at = active_trade.opened_at or pd.Timestamp.now(tz="UTC").isoformat()
            logger.emit(
                "position_open",
                message=(
                    f"Position open for {active_trade.symbol} qty={active_trade.filled_qty} "
                    f"avg={active_trade.entry_fill_price}"
                ),
                symbol=active_trade.symbol,
                strategy=active_trade.strategy_id,
                signal_key=active_trade.signal_key,
                qty=active_trade.filled_qty,
                entry_fill_price=active_trade.entry_fill_price,
            )
            return

    if order.get("status") in {"canceled", "expired", "rejected"}:
        active_trade.status = "entry_failed"
        logger.emit(
            "entry_failed",
            level="ERROR",
            message=f"Entry order failed: {format_order_summary(order)}",
            symbol=active_trade.symbol,
            strategy=active_trade.strategy_id,
            signal_key=active_trade.signal_key,
            status=order.get("status"),
        )


def reconcile_active_trade(
    alpaca_config: AlpacaConfig,
    active_trade_payload: dict[str, Any],
    *,
    logger: StructuredLogger,
    strategy_config: BacktestConfig,
) -> tuple[dict[str, Any] | None, TradeClosure | None]:
    active_trade = ActiveTrade(**active_trade_payload)

    if active_trade.status == "pending_entry":
        sync_trade_from_entry_order(alpaca_config, active_trade, logger=logger)
        if active_trade.status == "entry_failed":
            return None, None
        return active_trade.to_dict(), None

    try:
        position = get_position(alpaca_config, active_trade.symbol)
    except RuntimeError:
        position = None

    if active_trade.exit_mode == "bracket":
        parent = get_order(alpaca_config, active_trade.entry_order_id, nested=True)
        if position is not None:
            active_trade.filled_qty = float(position["qty"])
            active_trade.entry_fill_price = float(position["avg_entry_price"])
            active_trade.updated_at = pd.Timestamp.now(tz="UTC").isoformat()
            return active_trade.to_dict(), None

        filled_leg = next((leg for leg in parent.get("legs", []) or [] if leg.get("status") == "filled"), None)
        if filled_leg is None:
            raise RuntimeError(
                f"Position for {active_trade.symbol} disappeared but no filled exit leg was found. Failing safe."
            )

        if filled_leg.get("type") == "limit":
            exit_reason = "target"
        elif filled_leg.get("type") in {"stop", "stop_limit"} or filled_leg.get("stop_price") is not None:
            exit_reason = "stop"
        else:
            exit_reason = "broker_exit"
        closure = build_closure(
            active_trade,
            exit_price=float(filled_leg.get("filled_avg_price") or filled_leg.get("limit_price") or 0),
            exit_qty=float(filled_leg.get("filled_qty") or active_trade.filled_qty),
            exit_reason=exit_reason,
            closed_at=str(filled_leg.get("filled_at") or pd.Timestamp.now(tz="UTC").isoformat()),
            strategy_config=strategy_config,
        )
        logger.emit(
            "position_closed",
            message=(
                f"Bracket exit closed {active_trade.symbol} via {exit_reason} "
                f"net_pnl={closure.net_pnl:.2f}"
            ),
            symbol=active_trade.symbol,
            strategy=active_trade.strategy_id,
            signal_key=active_trade.signal_key,
            exit_reason=exit_reason,
            net_pnl=closure.net_pnl,
        )
        return None, closure

    if active_trade.exit_order_id:
        exit_order = get_order(alpaca_config, active_trade.exit_order_id)
        active_trade.updated_at = pd.Timestamp.now(tz="UTC").isoformat()
        if position is not None:
            active_trade.filled_qty = float(position["qty"])
            active_trade.entry_fill_price = float(position["avg_entry_price"])
            if exit_order.get("status") in PENDING_ORDER_STATUSES:
                active_trade.status = "pending_exit"
            else:
                active_trade.status = "open"
            return active_trade.to_dict(), None

        if exit_order.get("status") == "filled":
            closure = build_closure(
                active_trade,
                exit_price=float(exit_order["filled_avg_price"]),
                exit_qty=float(exit_order["filled_qty"]),
                exit_reason=active_trade.exit_reason or "flatten",
                closed_at=str(exit_order.get("filled_at") or pd.Timestamp.now(tz="UTC").isoformat()),
                strategy_config=strategy_config,
            )
            return None, closure
        if exit_order.get("status") in {"canceled", "rejected", "expired"}:
            raise RuntimeError(
                f"Exit order {active_trade.exit_order_id} failed with status={exit_order.get('status')}."
            )
        if exit_order.get("status") in PENDING_ORDER_STATUSES:
            raise RuntimeError(
                f"Position for {active_trade.symbol} is gone but exit order {active_trade.exit_order_id} "
                f"is still {exit_order.get('status')}. Failing safe."
            )

    if position is not None:
        active_trade.filled_qty = float(position["qty"])
        active_trade.entry_fill_price = float(position["avg_entry_price"])
        active_trade.status = "open"
        active_trade.updated_at = pd.Timestamp.now(tz="UTC").isoformat()
        return active_trade.to_dict(), None

    raise RuntimeError(
        f"Position for {active_trade.symbol} disappeared but the runner could not reconcile a completed exit."
    )


def request_flatten(
    alpaca_config: AlpacaConfig,
    active_trade_payload: dict[str, Any],
    *,
    logger: StructuredLogger,
    reason: str,
    entry_timeout_seconds: int,
) -> dict[str, Any]:
    active_trade = ActiveTrade(**active_trade_payload)
    close_order = close_position(alpaca_config, active_trade.symbol)
    logger.emit(
        "flatten_submitted",
        message=f"Submitted flatten order: {format_order_summary(close_order)}",
        symbol=active_trade.symbol,
        strategy=active_trade.strategy_id,
        signal_key=active_trade.signal_key,
        reason=reason,
        order_id=close_order["id"],
    )
    final_order = wait_for_order_terminal(
        alpaca_config,
        close_order["id"],
        timeout_seconds=entry_timeout_seconds,
    )
    logger.emit(
        "flatten_order_update",
        message=f"Flatten order update: {format_order_summary(final_order)}",
        symbol=active_trade.symbol,
        strategy=active_trade.strategy_id,
        signal_key=active_trade.signal_key,
        status=final_order.get("status"),
        order_id=final_order["id"],
    )
    active_trade.exit_order_id = final_order["id"]
    active_trade.exit_client_order_id = final_order.get("client_order_id")
    active_trade.exit_reason = reason
    active_trade.status = "pending_exit"
    active_trade.updated_at = pd.Timestamp.now(tz="UTC").isoformat()
    return active_trade.to_dict()


def build_closure(
    active_trade: ActiveTrade,
    *,
    exit_price: float,
    exit_qty: float,
    exit_reason: str,
    closed_at: str,
    strategy_config: BacktestConfig,
) -> TradeClosure:
    entry_fill_price = float(active_trade.entry_fill_price or active_trade.entry_reference_price)
    qty = min(float(active_trade.filled_qty or active_trade.requested_qty), exit_qty)
    if active_trade.direction == "long":
        gross_points = exit_price - entry_fill_price
    else:
        gross_points = entry_fill_price - exit_price
    gross_pnl = gross_points * qty * strategy_config.value_per_point
    commissions = qty * strategy_config.commission_per_unit * 2
    return TradeClosure(
        active_trade=active_trade,
        closed_at=str(to_et_timestamp(closed_at)),
        exit_price=exit_price,
        exit_qty=exit_qty,
        exit_reason=exit_reason,
        gross_pnl=gross_pnl,
        commissions=commissions,
        net_pnl=gross_pnl - commissions,
    )
