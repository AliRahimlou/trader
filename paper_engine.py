from __future__ import annotations

import threading
import time
from dataclasses import asdict, replace
from typing import Any

import pandas as pd

from alpaca_api import (
    cancel_order,
    close_position,
    fetch_account,
    fetch_clock,
    fetch_latest_quote,
    fetch_latest_trade,
    fetch_stock_bars,
    format_account_summary,
    format_order_summary,
    get_asset,
    get_position,
    list_orders,
    list_positions,
    period_to_start,
    submit_order,
    wait_for_order_terminal,
)
from live_config import PaperTradingConfig, build_alpaca_config
from live_execution import (
    ensure_no_unmanaged_broker_state,
    ensure_paper_account,
    reconcile_active_trade,
    request_flatten,
    submit_entry,
)

CHART_RANGE_MAP = {
    "1D": ("1m", "1d"),
    "1W": ("5m", "7d"),
    "1M": ("1d", "30d"),
}
from live_logging import StructuredLogger
from live_risk import evaluate_entry_risk
from live_scheduler import StaleDataError, parse_hhmm, session_key, to_et_timestamp, validate_latest_bar
from live_state import RunnerState, StateStore
from market_data_cache import MarketContextCache
from operator_store import OperatorStore
from strategy_signals import (
    StrategySignal,
    detect_break_setup,
    detect_pullback_setup,
    get_opening_range_bar,
    materialize_signal,
)


class PaperTradingEngine:
    def __init__(self, config: PaperTradingConfig, store: OperatorStore) -> None:
        self.base_config = config
        self.store = store
        self.stop_event = threading.Event()
        self.startup_complete = threading.Event()
        self.lock = threading.RLock()
        self.running = False
        self.startup_state = "idle"
        self.startup_error: str | None = None
        self.last_error: str | None = None
        self.last_warning: str | None = None
        self.last_completed_bar_time: str | None = None
        self.last_cycle_at: str | None = None
        self.last_heartbeat_at: str | None = None
        self.market_open = False
        self.data_fresh = False
        self.broker_connected = False
        self.auth_ok = False
        self.reconciliation_ok = False
        self.runner_mode = "demo" if config.demo_mode else "paper"
        self.latest_account: dict[str, Any] = {}
        self.latest_positions: list[dict[str, Any]] = []
        self.latest_orders: list[dict[str, Any]] = []
        self.latest_signal_records: list[dict[str, Any]] = []
        self.latest_market_snapshot: dict[str, Any] = {}

        self.runtime_flags = self._default_runtime_flags()
        self.runtime_settings = self._default_runtime_settings()
        self._load_runtime_overrides()

        self.state_store = StateStore(self.effective_config().state_path)
        self.state = self.state_store.load(
            symbol=self.base_config.symbol,
            strategies=list(self.effective_config().strategies),
        )
        self.state.prune(self.base_config.keep_state_days)

        self.event_logger = StructuredLogger(
            self.effective_config().log_path,
            on_event=self.store.append_event,
        )
        self.alpaca_config = None if config.demo_mode else build_alpaca_config(config)
        self.market_cache = None
        if not config.demo_mode and self.alpaca_config is not None:
            self.market_cache = MarketContextCache(
                symbol=config.symbol,
                alpaca_config=self.alpaca_config,
                minute_lookback=config.minute_lookback,
                five_minute_lookback=config.five_minute_lookback,
                daily_lookback=config.daily_lookback,
                minute_refresh_window=config.minute_refresh_window,
                five_minute_refresh_window=config.five_minute_refresh_window,
                daily_refresh_window=config.daily_refresh_window,
            )

        self._demo_counter = 0
        self._demo_price = 500.0
        self._publish_all_snapshots()

    def _default_runtime_flags(self) -> dict[str, Any]:
        return {
            "dry_run": bool(self.base_config.dry_run or self.base_config.demo_mode),
            "paused_new_entries": False,
            "enabled_symbols": {self.base_config.symbol: True},
            "enabled_strategies": {strategy_id: True for strategy_id in self.base_config.strategies},
        }

    def _default_runtime_settings(self) -> dict[str, Any]:
        return {
            "poll_seconds": self.base_config.poll_seconds,
            "max_position_qty": self.base_config.max_position_qty,
            "max_position_notional": self.base_config.max_position_notional,
            "max_daily_loss": self.base_config.max_daily_loss,
            "max_trades_per_day": self.base_config.max_trades_per_day,
            "cooldown_minutes": self.base_config.cooldown_minutes,
            "flatten_at": self.base_config.flatten_at,
            "exit_mode": self.base_config.exit_mode,
            "risk_per_trade": self.base_config.strategy_config.risk_per_trade,
            "rr_ratio": self.base_config.strategy_config.rr_ratio,
            "commission_per_unit": self.base_config.strategy_config.commission_per_unit,
            "min_gap_pct": self.base_config.strategy_config.min_gap_pct,
            "min_gap_atr": self.base_config.strategy_config.min_gap_atr,
            "require_displacement": self.base_config.strategy_config.require_displacement,
        }

    def effective_config(self) -> PaperTradingConfig:
        strategy_config = replace(
            self.base_config.strategy_config,
            risk_per_trade=float(self.runtime_settings["risk_per_trade"]),
            rr_ratio=float(self.runtime_settings["rr_ratio"]),
            commission_per_unit=float(self.runtime_settings["commission_per_unit"]),
            min_gap_pct=float(self.runtime_settings["min_gap_pct"]),
            min_gap_atr=float(self.runtime_settings["min_gap_atr"]),
            require_displacement=bool(self.runtime_settings["require_displacement"]),
        )
        return replace(
            self.base_config,
            poll_seconds=float(self.runtime_settings["poll_seconds"]),
            max_position_qty=float(self.runtime_settings["max_position_qty"]),
            max_position_notional=float(self.runtime_settings["max_position_notional"]),
            max_daily_loss=float(self.runtime_settings["max_daily_loss"]),
            max_trades_per_day=int(self.runtime_settings["max_trades_per_day"]),
            cooldown_minutes=int(self.runtime_settings["cooldown_minutes"]),
            flatten_at=str(self.runtime_settings["flatten_at"]),
            exit_mode=str(self.runtime_settings["exit_mode"]),
            strategy_config=strategy_config,
            dry_run=bool(self.runtime_flags["dry_run"]),
        )

    def run_forever(self) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError("Runner is already active.")
            self.running = True
            self.stop_event.clear()
            self.startup_complete.clear()
            self.startup_state = "starting"
            self.startup_error = None
            self.last_error = None
            self._publish_all_snapshots()

        try:
            self._startup()
            with self.lock:
                self.startup_state = "ready"
                self.startup_complete.set()
                self._publish_all_snapshots()
            while not self.stop_event.is_set():
                self.run_cycle()
                if self.stop_event.wait(self.effective_config().poll_seconds):
                    break
        except KeyboardInterrupt:
            if not self.base_config.demo_mode and self.alpaca_config is not None:
                self._attempt_fail_safe_flatten(reason="operator_interrupt")
            self.event_logger.emit(
                "runner_shutdown",
                message="Runner interrupted by user.",
                symbol=self.base_config.symbol,
            )
        except StaleDataError as exc:
            self.last_error = str(exc)
            self.startup_error = self.startup_error or str(exc)
            if self.startup_state == "starting":
                self.startup_state = "failed"
            self.startup_complete.set()
            if not self.base_config.demo_mode and self.alpaca_config is not None:
                self._attempt_fail_safe_flatten(reason="stale_data_fail_safe")
            self.event_logger.emit(
                "stale_data_halt",
                level="ERROR",
                message=f"Runner halted because market data is stale: {exc}",
                symbol=self.base_config.symbol,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.startup_error = self.startup_error or str(exc)
            if self.startup_state == "starting":
                self.startup_state = "failed"
            self.startup_complete.set()
            if not self.base_config.demo_mode and self.alpaca_config is not None:
                self._attempt_fail_safe_flatten(reason="runner_exception_fail_safe")
            self.event_logger.emit(
                "runner_error",
                level="ERROR",
                message=f"Runner stopped with error: {exc}",
                symbol=self.base_config.symbol,
                error=str(exc),
            )
            raise
        finally:
            with self.lock:
                self.running = False
                self.startup_complete.set()
                self.state_store.save(self.state)
                self._publish_all_snapshots()

    def run_once(self) -> None:
        with self.lock:
            self.running = True
            self.stop_event.clear()
            self.startup_complete.clear()
            self.startup_state = "starting"
            self.startup_error = None
            self.last_error = None
            self._publish_all_snapshots()
        try:
            self._startup()
            with self.lock:
                self.startup_state = "ready"
                self.startup_complete.set()
                self._publish_all_snapshots()
            self.run_cycle()
        except StaleDataError as exc:
            self.last_error = str(exc)
            self.startup_error = self.startup_error or str(exc)
            if self.startup_state == "starting":
                self.startup_state = "failed"
            self.startup_complete.set()
            if not self.base_config.demo_mode and self.alpaca_config is not None:
                self._attempt_fail_safe_flatten(reason="stale_data_fail_safe")
            self.event_logger.emit(
                "stale_data_halt",
                level="ERROR",
                message=f"Runner halted because market data is stale: {exc}",
                symbol=self.base_config.symbol,
                error=str(exc),
            )
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.startup_error = self.startup_error or str(exc)
            if self.startup_state == "starting":
                self.startup_state = "failed"
            self.startup_complete.set()
            if not self.base_config.demo_mode and self.alpaca_config is not None:
                self._attempt_fail_safe_flatten(reason="runner_exception_fail_safe")
            self.event_logger.emit(
                "runner_error",
                level="ERROR",
                message=f"Runner stopped with error: {exc}",
                symbol=self.base_config.symbol,
                error=str(exc),
            )
            raise
        finally:
            with self.lock:
                self.running = False
                self.startup_complete.set()
                self.state_store.save(self.state)
                self._publish_all_snapshots()

    def wait_for_startup(self, timeout_seconds: float) -> bool:
        completed = self.startup_complete.wait(timeout_seconds)
        return completed and self.startup_state == "ready"

    def startup_failure_message(self) -> str | None:
        return self.startup_error or self.last_error

    def request_stop(self) -> None:
        self.stop_event.set()

    def set_pause_new_entries(self, paused: bool) -> dict[str, Any]:
        with self.lock:
            self.runtime_flags["paused_new_entries"] = paused
            self._persist_runtime_overrides()
            self.event_logger.emit(
                "entries_pause_updated",
                message=f"Pause new entries set to {paused}.",
                paused=paused,
                symbol=self.base_config.symbol,
            )
            self._publish_all_snapshots()
            return {"paused_new_entries": paused}

    def set_symbol_enabled(self, symbol: str, enabled: bool) -> dict[str, Any]:
        symbol = symbol.upper()
        if symbol != self.base_config.symbol:
            raise RuntimeError(f"Symbol {symbol} is not configured in this runner.")
        with self.lock:
            self.runtime_flags["enabled_symbols"][symbol] = enabled
            self._persist_runtime_overrides()
            self.event_logger.emit(
                "symbol_enabled_updated",
                message=f"Symbol {symbol} enabled={enabled}",
                symbol=symbol,
                enabled=enabled,
            )
            self._publish_all_snapshots()
            return {"symbol": symbol, "enabled": enabled}

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> dict[str, Any]:
        if strategy_id not in self.runtime_flags["enabled_strategies"]:
            raise RuntimeError(f"Strategy {strategy_id} is not configured.")
        with self.lock:
            self.runtime_flags["enabled_strategies"][strategy_id] = enabled
            self._persist_runtime_overrides()
            self.event_logger.emit(
                "strategy_enabled_updated",
                message=f"Strategy {strategy_id} enabled={enabled}",
                strategy=strategy_id,
                symbol=self.base_config.symbol,
                enabled=enabled,
            )
            self._publish_all_snapshots()
            return {"strategy": strategy_id, "enabled": enabled}

    def set_dry_run(self, dry_run: bool) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("Dry-run mode can only be changed while the runner is stopped.")
        with self.lock:
            self.runtime_flags["dry_run"] = dry_run or self.base_config.demo_mode
            self._persist_runtime_overrides()
            self.state_store = StateStore(self.effective_config().state_path)
            self.state = self.state_store.load(
                symbol=self.base_config.symbol,
                strategies=list(self.effective_config().strategies),
            )
            self.event_logger = StructuredLogger(
                self.effective_config().log_path,
                on_event=self.store.append_event,
            )
            self.event_logger.emit(
                "dry_run_updated",
                message=f"Dry run set to {self.runtime_flags['dry_run']}.",
                symbol=self.base_config.symbol,
                dry_run=self.runtime_flags["dry_run"],
            )
            self._publish_all_snapshots()
            return {"dry_run": self.runtime_flags["dry_run"]}

    def reset_runtime_overrides(self) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("Runtime overrides can only be reset while the runner is stopped.")
        with self.lock:
            self.runtime_flags = self._default_runtime_flags()
            self.runtime_settings = self._default_runtime_settings()
            self.store.delete_snapshot("runtime_overrides")
            self.state_store = StateStore(self.effective_config().state_path)
            self.state = self.state_store.load(
                symbol=self.base_config.symbol,
                strategies=list(self.effective_config().strategies),
            )
            self.state.prune(self.base_config.keep_state_days)
            self.event_logger = StructuredLogger(
                self.effective_config().log_path,
                on_event=self.store.append_event,
            )
            self.event_logger.emit(
                "runtime_overrides_reset",
                message="Runtime overrides reset to configured defaults.",
                symbol=self.base_config.symbol,
            )
            self._publish_all_snapshots()
            return {
                "reset": True,
                "runtime_overrides_active": False,
            }

    def get_trade_context(self, symbol: str | None = None, chart_range: str = "1D") -> dict[str, Any]:
        symbol = (symbol or self.base_config.symbol).upper()
        chart_range = chart_range.upper()
        if chart_range not in CHART_RANGE_MAP:
            chart_range = "1D"

        with self.lock:
            if self.base_config.demo_mode:
                return self._demo_trade_context(symbol, chart_range)

            assert self.alpaca_config is not None
            account = fetch_account(self.alpaca_config)
            trade = fetch_latest_trade(self.alpaca_config, symbol)
            quote = fetch_latest_quote(self.alpaca_config, symbol)
            asset = get_asset(self.alpaca_config, symbol)
            position = self._lookup_position(symbol)
            chart = self._load_chart_payload(symbol, chart_range)
            can_manual_trade, manual_reason = self._manual_trade_availability(symbol)

            latest_price = float(trade["p"])
            prior_close = chart["points"][0]["close"] if chart["points"] else latest_price
            absolute_change = latest_price - prior_close
            percent_change = (absolute_change / prior_close * 100.0) if prior_close else 0.0
            position_summary = self._position_trade_summary(position)

            return {
                "symbol": symbol,
                "configured_symbol": self.base_config.symbol,
                "paper_only": True,
                "manual_trading_enabled": can_manual_trade,
                "manual_trading_reason": manual_reason,
                "manual_trade_warning": self._manual_position_warning(symbol, position),
                "quote": {
                    "last_price": latest_price,
                    "bid": float(quote["bp"]),
                    "ask": float(quote["ap"]),
                    "timestamp": trade["t"],
                    "absolute_change": absolute_change,
                    "percent_change": percent_change,
                },
                "account": {
                    "cash": float(account.get("cash") or 0.0),
                    "buying_power": float(account.get("buying_power") or 0.0),
                    "portfolio_value": float(account.get("portfolio_value") or 0.0),
                    "equity": float(account.get("equity") or account.get("portfolio_value") or 0.0),
                    "last_equity": float(account.get("last_equity") or account.get("equity") or 0.0),
                },
                "position": position,
                "position_summary": position_summary,
                "asset": {
                    "tradable": bool(asset.get("tradable")),
                    "fractionable": bool(asset.get("fractionable")),
                    "shortable": bool(asset.get("shortable")),
                    "easy_to_borrow": bool(asset.get("easy_to_borrow")),
                },
                "chart": chart,
                "bot": {
                    "status_label": self._bot_status_label(),
                    "status_reason": self._bot_status_reason(),
                    "running": self.running,
                    "paused": self.runtime_flags["paused_new_entries"],
                    "market_open": self.market_open,
                    "data_fresh": self.data_fresh,
                    "last_heartbeat": self.last_heartbeat_at,
                    "latest_completed_bar_time": self.last_completed_bar_time,
                    "enabled_strategies": [
                        strategy_id
                        for strategy_id, enabled in self.runtime_flags["enabled_strategies"].items()
                        if enabled
                    ],
                },
            }

    def preview_manual_trade(self, symbol: str, side: str, amount_dollars: float) -> dict[str, Any]:
        symbol = symbol.upper()
        side = side.lower().strip()
        amount_dollars = float(amount_dollars)

        if side not in {"buy", "sell"}:
            raise RuntimeError("Manual trade side must be buy or sell.")

        with self.lock:
            if self.base_config.demo_mode:
                price = round(self._demo_price, 2)
                account = self._demo_account_payload()
                asset = {"tradable": True, "fractionable": True}
                position = self._demo_position_for_symbol(symbol)
            else:
                assert self.alpaca_config is not None
                trade = fetch_latest_trade(self.alpaca_config, symbol)
                price = float(trade["p"])
                account = fetch_account(self.alpaca_config)
                asset = get_asset(self.alpaca_config, symbol)
                position = self._lookup_position(symbol)

            can_submit, submit_reason = self._manual_trade_availability(symbol)
            warnings: list[str] = []
            if amount_dollars <= 0:
                can_submit = False
                submit_reason = "Enter an amount greater than $0."

            estimated_qty = amount_dollars / price if price > 0 else 0.0
            use_notional = side == "buy" and bool(asset.get("fractionable", False))
            if not bool(asset.get("fractionable", False)):
                estimated_qty = float(int(estimated_qty))

            if side == "sell":
                held_qty = float(position.get("qty") or 0.0) if position else 0.0
                if held_qty <= 0:
                    can_submit = False
                    submit_reason = "You need an open long position before selling from this trade ticket."
                estimated_qty = min(held_qty, estimated_qty or held_qty)
                use_notional = False
                if estimated_qty < held_qty and amount_dollars > price * held_qty:
                    warnings.append("Sell preview was capped at your current position size.")

            estimated_notional = amount_dollars if use_notional else estimated_qty * price
            buying_power = float(account.get("buying_power") or 0.0)
            if side == "buy" and estimated_notional > buying_power + 0.01:
                can_submit = False
                submit_reason = "Amount is above your available buying power."

            if not bool(asset.get("tradable", False)):
                can_submit = False
                submit_reason = f"{symbol} is not tradable in the connected paper account."

            if estimated_qty <= 0 and not use_notional:
                can_submit = False
                submit_reason = submit_reason or "Amount is too small for the current share price."

            if symbol == self.base_config.symbol:
                warnings.append("Manual positions on the automation symbol should be closed before restarting the bot.")

            return {
                "symbol": symbol,
                "configured_symbol": self.base_config.symbol,
                "side": side,
                "amount_dollars": amount_dollars,
                "estimated_price": price,
                "estimated_qty": estimated_qty,
                "estimated_notional": estimated_notional,
                "buying_power": buying_power,
                "position_qty": float(position.get("qty") or 0.0) if position else 0.0,
                "paper_only": True,
                "can_submit": can_submit,
                "submit_reason": submit_reason,
                "warnings": warnings,
                "use_notional": use_notional,
            }

    def execute_manual_trade(self, symbol: str, side: str, amount_dollars: float) -> dict[str, Any]:
        with self.lock:
            preview = self.preview_manual_trade(symbol, side, amount_dollars)
            if not preview["can_submit"]:
                raise RuntimeError(preview["submit_reason"] or "Manual trade cannot be submitted.")

            symbol = preview["symbol"]
            side = preview["side"]

            if self.base_config.demo_mode:
                order_id = f"demo-manual-{int(time.time())}"
                self.event_logger.emit(
                    "manual_trade_submitted",
                    message=f"Demo manual {side} order submitted for {symbol}.",
                    symbol=symbol,
                    side=side,
                    amount_dollars=preview["amount_dollars"],
                    estimated_qty=preview["estimated_qty"],
                    order_id=order_id,
                )
                result = {
                    "order_id": order_id,
                    "status": "filled",
                    "symbol": symbol,
                    "side": side,
                    "filled_qty": preview["estimated_qty"],
                    "filled_avg_price": preview["estimated_price"],
                }
                self.event_logger.emit(
                    "manual_trade_update",
                    message=f"Demo manual {side} order filled for {symbol}.",
                    symbol=symbol,
                    side=side,
                    order_id=order_id,
                    status="filled",
                )
                return result

            assert self.alpaca_config is not None
            client_order_id = f"manual-{side}-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d%H%M%S')}"
            if preview["use_notional"]:
                order = submit_order(
                    self.alpaca_config,
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    time_in_force="day",
                    notional=preview["estimated_notional"],
                    client_order_id=client_order_id,
                )
            else:
                order = submit_order(
                    self.alpaca_config,
                    symbol=symbol,
                    side=side,
                    order_type="market",
                    time_in_force="day",
                    qty=preview["estimated_qty"],
                    client_order_id=client_order_id,
                )

            self.event_logger.emit(
                "manual_trade_submitted",
                message=f"Manual {side} order submitted for {symbol}: {format_order_summary(order)}",
                symbol=symbol,
                side=side,
                amount_dollars=preview["amount_dollars"],
                estimated_qty=preview["estimated_qty"],
                order_id=order["id"],
            )
            terminal_order = wait_for_order_terminal(
                self.alpaca_config,
                order["id"],
                timeout_seconds=self.effective_config().entry_timeout_seconds,
            )
            self.event_logger.emit(
                "manual_trade_update",
                message=f"Manual {side} order update for {symbol}: {format_order_summary(terminal_order)}",
                symbol=symbol,
                side=side,
                order_id=terminal_order["id"],
                status=terminal_order.get("status"),
            )
            self._refresh_broker_snapshots()
            self._publish_all_snapshots()
            return {
                "order_id": terminal_order["id"],
                "status": terminal_order.get("status"),
                "symbol": symbol,
                "side": side,
                "filled_qty": float(terminal_order.get("filled_qty") or 0.0),
                "filled_avg_price": float(terminal_order.get("filled_avg_price") or 0.0),
            }

    def apply_runtime_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = set(self.runtime_settings.keys())
        unknown = sorted(set(settings) - allowed_keys)
        if unknown:
            raise RuntimeError(f"Unsupported config keys: {', '.join(unknown)}")
        if self.running and self.state.active_trade is not None:
            raise RuntimeError("Cannot change runtime risk settings while an active trade is open.")

        normalized = dict(settings)
        if "exit_mode" in normalized and normalized["exit_mode"] not in {"bracket", "in_process"}:
            raise RuntimeError("exit_mode must be 'bracket' or 'in_process'.")
        if "flatten_at" in normalized:
            parse_hhmm(str(normalized["flatten_at"]))
        for numeric_key in {
            "poll_seconds",
            "max_position_qty",
            "max_position_notional",
            "max_daily_loss",
            "max_trades_per_day",
            "cooldown_minutes",
            "risk_per_trade",
            "rr_ratio",
            "commission_per_unit",
            "min_gap_pct",
            "min_gap_atr",
        }:
            if numeric_key in normalized:
                normalized[numeric_key] = float(normalized[numeric_key])
        if "max_trades_per_day" in normalized:
            normalized["max_trades_per_day"] = int(normalized["max_trades_per_day"])
        if "cooldown_minutes" in normalized:
            normalized["cooldown_minutes"] = int(normalized["cooldown_minutes"])

        with self.lock:
            for key, value in normalized.items():
                self.runtime_settings[key] = value
            self._persist_runtime_overrides()
            self.event_logger.emit(
                "runtime_settings_updated",
                message="Runtime settings updated.",
                symbol=self.base_config.symbol,
                updated_keys=sorted(normalized),
                settings=normalized,
            )
            self._publish_all_snapshots()
            return self._config_snapshot()

    def flatten_all(self, *, reason: str = "operator_flatten") -> dict[str, Any]:
        if self.base_config.demo_mode:
            self.event_logger.emit(
                "demo_flatten_all",
                message="Demo flatten-all requested.",
                symbol=self.base_config.symbol,
                reason=reason,
            )
            self.state.active_trade = None
            self._publish_all_snapshots()
            return {"flattened": True, "demo_mode": True}

        assert self.alpaca_config is not None
        with self.lock:
            result: dict[str, Any] = {"flattened": False}
            if self.state.active_trade is not None:
                self.state.active_trade = request_flatten(
                    self.alpaca_config,
                    self.state.active_trade,
                    logger=self.event_logger,
                    reason=reason,
                    entry_timeout_seconds=self.effective_config().entry_timeout_seconds,
                )
                result["flattened"] = True
            else:
                try:
                    position = get_position(self.alpaca_config, self.base_config.symbol)
                except RuntimeError:
                    position = None
                if position is not None:
                    close_order = close_position(self.alpaca_config, self.base_config.symbol)
                    self.event_logger.emit(
                        "flatten_submitted",
                        message=f"Submitted operator flatten order: {format_order_summary(close_order)}",
                        symbol=self.base_config.symbol,
                        reason=reason,
                        order_id=close_order["id"],
                    )
                    result["flattened"] = True
                    result["order_id"] = close_order["id"]
            self._refresh_broker_snapshots()
            self._publish_all_snapshots()
            return result

    def close_symbol(self, symbol: str) -> dict[str, Any]:
        if symbol.upper() != self.base_config.symbol:
            raise RuntimeError(f"Symbol {symbol} is not configured in this runner.")
        return self.flatten_all(reason="operator_close_symbol")

    def cancel_open_orders(self) -> dict[str, Any]:
        if self.base_config.demo_mode:
            self.event_logger.emit(
                "demo_cancel_orders",
                message="Demo cancel-open-orders requested.",
                symbol=self.base_config.symbol,
            )
            return {"canceled_order_ids": [], "demo_mode": True}

        assert self.alpaca_config is not None
        canceled: list[str] = []
        with self.lock:
            for order in list_orders(self.alpaca_config, status="open", limit=100):
                if order["symbol"] != self.base_config.symbol:
                    continue
                cancel_order(self.alpaca_config, order["id"])
                canceled.append(order["id"])
                self.event_logger.emit(
                    "order_canceled",
                    message=f"Canceled open order {order['id']} for {self.base_config.symbol}.",
                    symbol=self.base_config.symbol,
                    order_id=order["id"],
                )
            self._refresh_broker_snapshots()
            self._publish_all_snapshots()
        return {"canceled_order_ids": canceled}

    def run_smoke_test(self) -> None:
        if self.base_config.demo_mode:
            self.event_logger.emit(
                "smoke_test_complete",
                message="Demo smoke test completed.",
                symbol=self.base_config.symbol,
                notional=self.effective_config().smoke_test_notional,
            )
            return

        assert self.alpaca_config is not None
        clock = fetch_clock(self.alpaca_config)
        if not clock.get("is_open"):
            raise RuntimeError("Smoke test requires the market to be open.")

        symbol = (self.effective_config().smoke_test_symbol or self.base_config.symbol).upper()
        asset = get_asset(self.alpaca_config, symbol)
        if not asset.get("tradable"):
            raise RuntimeError(f"{symbol} is not tradable for the smoke test.")
        if not asset.get("fractionable"):
            raise RuntimeError(f"{symbol} is not fractionable; smoke test uses a notional order.")

        if self.effective_config().dry_run:
            self.event_logger.emit(
                "smoke_test_dry_run",
                message=f"Dry run: would submit smoke test order for {symbol}.",
                symbol=symbol,
                notional=self.effective_config().smoke_test_notional,
            )
            return

        entry_order = submit_order(
            self.alpaca_config,
            symbol=symbol,
            side="buy",
            order_type="market",
            time_in_force="day",
            notional=self.effective_config().smoke_test_notional,
            client_order_id=f"smoke-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d%H%M%S')}",
        )
        self.event_logger.emit(
            "smoke_test_entry_submitted",
            message=f"Smoke test entry submitted: {format_order_summary(entry_order)}",
            symbol=symbol,
            order_id=entry_order["id"],
        )
        filled_entry = wait_for_order_terminal(
            self.alpaca_config,
            entry_order["id"],
            timeout_seconds=self.effective_config().entry_timeout_seconds,
        )
        self.event_logger.emit(
            "smoke_test_entry_update",
            message=f"Smoke test entry update: {format_order_summary(filled_entry)}",
            symbol=symbol,
            order_id=filled_entry["id"],
            status=filled_entry.get("status"),
        )
        if filled_entry.get("status") != "filled":
            raise RuntimeError(f"Smoke test entry did not fill. Status={filled_entry.get('status')}")

        close_order = close_position(self.alpaca_config, symbol)
        self.event_logger.emit(
            "smoke_test_exit_submitted",
            message=f"Smoke test exit submitted: {format_order_summary(close_order)}",
            symbol=symbol,
            order_id=close_order["id"],
        )
        filled_exit = wait_for_order_terminal(
            self.alpaca_config,
            close_order["id"],
            timeout_seconds=self.effective_config().entry_timeout_seconds,
        )
        self.event_logger.emit(
            "smoke_test_exit_update",
            message=f"Smoke test exit update: {format_order_summary(filled_exit)}",
            symbol=symbol,
            order_id=filled_exit["id"],
            status=filled_exit.get("status"),
        )
        if filled_exit.get("status") != "filled":
            raise RuntimeError(f"Smoke test exit did not fill. Status={filled_exit.get('status')}")
        self._refresh_broker_snapshots()
        self._publish_all_snapshots()
        self.event_logger.emit(
            "smoke_test_complete",
            message=f"Smoke test completed for {symbol}.",
            symbol=symbol,
            notional=self.effective_config().smoke_test_notional,
        )

    def run_cycle(self) -> None:
        with self.lock:
            self.last_heartbeat_at = pd.Timestamp.now(tz="UTC").isoformat()
            if self.base_config.demo_mode:
                self._run_demo_cycle()
                return
            self._run_real_cycle()

    def _startup(self) -> None:
        config = self.effective_config()
        self.state_store = StateStore(config.state_path)
        self.state = self.state_store.load(
            symbol=self.base_config.symbol,
            strategies=list(config.strategies),
        )
        self.state.prune(config.keep_state_days)
        self.event_logger = StructuredLogger(
            config.log_path,
            on_event=self.store.append_event,
        )
        if self.base_config.demo_mode:
            self.reconciliation_ok = True
            self.auth_ok = True
            self.broker_connected = True
            self.event_logger.emit(
                "runner_start",
                message=f"Demo runner started for {self.base_config.symbol}.",
                symbol=self.base_config.symbol,
                dry_run=config.dry_run,
                demo_mode=True,
            )
            self._publish_all_snapshots()
            return

        assert self.alpaca_config is not None
        ensure_paper_account(self.alpaca_config)
        account = fetch_account(self.alpaca_config)
        self.latest_account = account
        self.auth_ok = True
        self.broker_connected = True
        self.event_logger.emit(
            "runner_start",
            message=f"Runner started for {self.base_config.symbol}. {format_account_summary(account)}",
            symbol=self.base_config.symbol,
            strategies=list(config.strategies),
            dry_run=config.dry_run,
            once=config.once,
            exit_mode=config.exit_mode,
            state_path=str(config.state_path),
            log_path=str(config.log_path),
            database_path=str(config.database_path),
        )
        self._startup_reconcile()
        self._refresh_broker_snapshots()
        self._publish_all_snapshots()

    def _startup_reconcile(self) -> None:
        if self.base_config.demo_mode:
            self.reconciliation_ok = True
            return

        assert self.alpaca_config is not None
        config = self.effective_config()
        ensure_no_unmanaged_broker_state(
            self.alpaca_config,
            symbol=self.base_config.symbol,
            active_trade=self.state.active_trade,
        )
        if self.state.active_trade is None:
            self.reconciliation_ok = True
            self.event_logger.emit(
                "startup_reconcile",
                message=f"No active persisted trade for {self.base_config.symbol}.",
                symbol=self.base_config.symbol,
            )
            return

        updated_trade, closure = reconcile_active_trade(
            self.alpaca_config,
            self.state.active_trade,
            logger=self.event_logger,
            strategy_config=config.strategy_config,
        )
        self.state.active_trade = updated_trade
        if closure is not None:
            self._record_closure(closure.to_dict())
            self.event_logger.emit(
                "startup_trade_closed",
                message=(
                    f"Startup reconciliation closed {self.base_config.symbol} via {closure.exit_reason} "
                    f"net_pnl={closure.net_pnl:.2f}"
                ),
                symbol=self.base_config.symbol,
                exit_reason=closure.exit_reason,
                net_pnl=closure.net_pnl,
                closed_at=closure.closed_at,
            )
        elif self.state.active_trade is not None:
            self._maybe_count_trade(self.state.active_trade)
        self.reconciliation_ok = True

    def _run_real_cycle(self) -> None:
        assert self.alpaca_config is not None
        config = self.effective_config()
        clock = fetch_clock(self.alpaca_config)
        now = to_et_timestamp(clock["timestamp"])
        self.last_cycle_at = pd.Timestamp.now(tz="UTC").isoformat()
        self.market_open = bool(clock.get("is_open"))
        self.state.prune(config.keep_state_days)

        if not clock.get("is_open"):
            self.data_fresh = False
            self.event_logger.emit(
                "market_closed",
                message=f"Market closed at {now}. Next open: {clock.get('next_open')}",
                symbol=self.base_config.symbol,
                now=str(now),
                next_open=clock.get("next_open"),
            )
            self.state_store.save(self.state)
            self._refresh_broker_snapshots()
            self._publish_all_snapshots()
            return

        market_data = self.market_cache.refresh(pd.Timestamp(clock["timestamp"]))
        latest_bar_time = market_data["1m"].index.max()
        validate_latest_bar(now, latest_bar_time, max_bar_age_seconds=config.max_bar_age_seconds)
        self.data_fresh = True
        self.last_completed_bar_time = latest_bar_time.isoformat()
        self._log_market_snapshot(latest_bar_time, market_data)

        last_processed_bar = to_et_timestamp(self.state.last_processed_bar) if self.state.last_processed_bar else None
        if last_processed_bar is not None and latest_bar_time <= last_processed_bar:
            self.event_logger.emit(
                "bar_unchanged",
                level="DEBUG",
                message=f"No new 1-minute bar. Latest processed={last_processed_bar} current={latest_bar_time}",
                symbol=self.base_config.symbol,
                latest_bar_time=str(latest_bar_time),
            )
            self._refresh_broker_snapshots()
            self._publish_all_snapshots()
            self.state_store.save(self.state)
            return

        if self.state.active_trade is not None:
            self._reconcile_open_trade()

        if self.state.active_trade is None:
            ensure_no_unmanaged_broker_state(
                self.alpaca_config,
                symbol=self.base_config.symbol,
                active_trade=None,
            )

        if now.time() >= parse_hhmm(config.flatten_at):
            if self.state.active_trade is not None:
                self._handle_flatten(reason="end_of_day_flatten")
            else:
                self.event_logger.emit(
                    "flatten_window",
                    message=f"Past flatten cutoff {config.flatten_at} ET; no new entries.",
                    symbol=self.base_config.symbol,
                    now=str(now),
                )
            self.state.last_processed_bar = latest_bar_time.isoformat()
            self.state_store.save(self.state)
            self._refresh_broker_snapshots()
            self._publish_all_snapshots()
            return

        if self.state.active_trade is not None and config.exit_mode == "in_process":
            self._maybe_request_in_process_exit(market_data["1m"])

        if self.state.active_trade is None:
            self._maybe_submit_new_entry(market_data, now)

        self.state.last_processed_bar = latest_bar_time.isoformat()
        self.state_store.save(self.state)
        self._refresh_broker_snapshots()
        self._publish_all_snapshots()

    def _run_demo_cycle(self) -> None:
        config = self.effective_config()
        now = pd.Timestamp.now(tz="America/New_York")
        latest_bar_time = now.floor("min") - pd.Timedelta(minutes=1)
        self.last_cycle_at = pd.Timestamp.now(tz="UTC").isoformat()
        self.last_completed_bar_time = latest_bar_time.isoformat()
        self.market_open = parse_hhmm("09:30") <= now.time() <= parse_hhmm("16:00")
        self.data_fresh = True
        self.auth_ok = True
        self.broker_connected = True
        self.latest_account = {
            "account_number": "DEMO-PAPER",
            "status": "ACTIVE",
            "cash": "100000",
            "buying_power": "200000",
            "portfolio_value": "100250",
            "equity": "100250",
        }
        self.latest_market_snapshot = {
            "latest_bar_time": latest_bar_time.isoformat(),
            "latest_trade_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
            "latest_quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
            "latest_trade_price": round(self._demo_price, 2),
            "bid": round(self._demo_price - 0.02, 2),
            "ask": round(self._demo_price + 0.02, 2),
            "rows_1m": 120,
            "rows_5m": 50,
            "rows_1d": 30,
        }
        if self.state.last_processed_bar == latest_bar_time.isoformat():
            self._publish_all_snapshots()
            return

        self._demo_counter += 1
        self._demo_price += 0.35 if self._demo_counter % 2 == 0 else -0.18
        self.latest_signal_records = []

        if self.state.active_trade is not None and self._demo_counter % 2 == 0:
            closure = {
                **self.state.active_trade,
                "status": "closed",
                "closed_at": pd.Timestamp.now(tz="America/New_York").isoformat(),
                "exit_price": round(self._demo_price + 0.4, 2),
                "exit_qty": float(self.state.active_trade["filled_qty"]),
                "exit_reason": "demo_target",
                "gross_pnl": 12.5,
                "commissions": 0.0,
                "net_pnl": 12.5,
            }
            self._record_closure(closure)
            self.event_logger.emit(
                "trade_closed",
                message="Demo trade closed.",
                symbol=self.base_config.symbol,
                exit_reason="demo_target",
                net_pnl=12.5,
            )
        elif self.state.active_trade is None and not self.runtime_flags["paused_new_entries"]:
            signal = {
                "strategy_id": "break",
                "strategy_name": "Opening Range + FVG",
                "direction": "long",
                "signal_time": latest_bar_time.isoformat(),
                "signal_key": f"demo-break-{latest_bar_time.isoformat()}",
                "entry_reference_price": round(self._demo_price, 2),
                "stop_price": round(self._demo_price - 1.2, 2),
                "target_price": round(self._demo_price + 2.4, 2),
                "requested_qty": 10.0,
                "approved_qty": 10.0,
                "allowed": True,
                "reasons": [],
            }
            self.latest_signal_records = [signal]
            self.event_logger.emit(
                "signal_evaluated",
                message=f"Evaluated demo signal {signal['signal_key']} allowed=True qty=10",
                symbol=self.base_config.symbol,
                strategy=signal["strategy_id"],
                signal_key=signal["signal_key"],
                direction=signal["direction"],
                stop_price=signal["stop_price"],
                target_price=signal["target_price"],
                requested_qty=10.0,
                approved_qty=10.0,
                reasons=[],
            )
            if not config.dry_run:
                self.event_logger.emit(
                    "entry_submitted",
                    message="Submitted demo entry order.",
                    symbol=self.base_config.symbol,
                    strategy="break",
                    signal_key=signal["signal_key"],
                    qty=10.0,
                    exit_mode=config.exit_mode,
                    entry_order_id=f"demo-entry-{self._demo_counter}",
                )
                self.state.active_trade = {
                    "symbol": self.base_config.symbol,
                    "strategy_id": "break",
                    "strategy_name": "Opening Range + FVG",
                    "signal_key": signal["signal_key"],
                    "signal_time": latest_bar_time.isoformat(),
                    "direction": "long",
                    "reason": "demo_signal",
                    "exit_mode": config.exit_mode,
                    "status": "open",
                    "requested_qty": 10.0,
                    "filled_qty": 10.0,
                    "entry_reference_price": signal["entry_reference_price"],
                    "entry_fill_price": signal["entry_reference_price"],
                    "stop_price": signal["stop_price"],
                    "target_price": signal["target_price"],
                    "entry_order_id": f"demo-entry-{self._demo_counter}",
                    "entry_client_order_id": f"demo-entry-{self._demo_counter}",
                    "entry_order_class": "simple",
                    "take_profit_order_id": None,
                    "stop_loss_order_id": None,
                    "exit_order_id": None,
                    "exit_client_order_id": None,
                    "exit_reason": None,
                    "opened_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "updated_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "trade_counted": False,
                }
                self._maybe_count_trade(self.state.active_trade)
            else:
                self.event_logger.emit(
                    "entry_dry_run",
                    message="Dry run: would submit demo entry order.",
                    symbol=self.base_config.symbol,
                    strategy="break",
                    signal_key=signal["signal_key"],
                    qty=10.0,
                )

        self.state.last_processed_bar = latest_bar_time.isoformat()
        self.latest_positions = [self.state.active_trade] if self.state.active_trade is not None else []
        self.latest_orders = []
        self.state_store.save(self.state)
        self._publish_all_snapshots()

    def _log_market_snapshot(self, latest_bar_time: pd.Timestamp, market_data: dict[str, pd.DataFrame]) -> None:
        assert self.alpaca_config is not None
        trade = fetch_latest_trade(self.alpaca_config, self.base_config.symbol)
        quote = fetch_latest_quote(self.alpaca_config, self.base_config.symbol)
        self.latest_market_snapshot = {
            "latest_bar_time": str(latest_bar_time),
            "latest_trade_timestamp": trade["t"],
            "latest_trade_price": trade["p"],
            "latest_quote_timestamp": quote["t"],
            "bid": quote["bp"],
            "ask": quote["ap"],
            "rows_1m": len(market_data["1m"]),
            "rows_5m": len(market_data["5m"]),
            "rows_1d": len(market_data["1d"]),
        }
        self.event_logger.emit(
            "market_snapshot",
            level="DEBUG",
            message=(
                f"Fresh bars loaded for {self.base_config.symbol}. latest_bar={latest_bar_time} "
                f"latest_trade={trade['t']} latest_quote={quote['t']}"
            ),
            symbol=self.base_config.symbol,
            **self.latest_market_snapshot,
        )

    def _maybe_submit_new_entry(
        self,
        market_data: dict[str, pd.DataFrame],
        now: pd.Timestamp,
    ) -> None:
        assert self.alpaca_config is not None
        config = self.effective_config()

        if not self.runtime_flags["enabled_symbols"].get(self.base_config.symbol, True):
            self.event_logger.emit(
                "symbol_disabled",
                message=f"Symbol {self.base_config.symbol} is disabled; skipping new entries.",
                symbol=self.base_config.symbol,
            )
            return

        if self.runtime_flags["paused_new_entries"]:
            self.event_logger.emit(
                "entries_paused",
                message=f"New entries are paused for {self.base_config.symbol}.",
                symbol=self.base_config.symbol,
            )
            return

        if not config.strategies:
            self.event_logger.emit(
                "strategies_disabled",
                message=f"All strategies are disabled for {self.base_config.symbol}.",
                symbol=self.base_config.symbol,
            )
            return

        latest_trade = fetch_latest_trade(self.alpaca_config, self.base_config.symbol)
        reference_price = float(latest_trade["p"])
        account = fetch_account(self.alpaca_config)
        asset = get_asset(self.alpaca_config, self.base_config.symbol)
        signals = self._collect_latest_signals(market_data, reference_price)
        self.latest_signal_records = []

        if not signals:
            self.event_logger.emit(
                "no_signal",
                level="DEBUG",
                message=f"No live signal for {self.base_config.symbol} on bar {market_data['1m'].index.max()}",
                symbol=self.base_config.symbol,
            )
            return

        for signal in signals:
            signal_day_key = session_key(signal.signal_time)
            if self.state.is_signal_processed(signal_day_key, signal.signal_key):
                self.event_logger.emit(
                    "duplicate_signal_skipped",
                    level="DEBUG",
                    message=f"Duplicate signal skipped: {signal.signal_key}",
                    symbol=self.base_config.symbol,
                    signal_key=signal.signal_key,
                )
                continue

            risk_decision = evaluate_entry_risk(
                signal,
                state=self.state,
                account=account,
                asset=asset,
                now=now,
                max_position_qty=config.max_position_qty,
                max_position_notional=config.max_position_notional,
                max_daily_loss=config.max_daily_loss,
                max_trades_per_day=config.max_trades_per_day,
                cooldown_minutes=config.cooldown_minutes,
                one_position_per_symbol=config.one_position_per_symbol,
                exit_mode=config.exit_mode,
                allow_fractional_long=config.allow_fractional_long,
            )
            self.state.mark_signal_processed(signal_day_key, signal.signal_key)
            signal_record = {
                "strategy_id": signal.strategy_id,
                "strategy_name": signal.strategy_name,
                "direction": signal.direction,
                "signal_time": signal.signal_time.isoformat(),
                "signal_key": signal.signal_key,
                "entry_reference_price": signal.entry_reference_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "requested_qty": signal.quantity,
                "approved_qty": risk_decision.approved_qty,
                "allowed": risk_decision.allowed,
                "reasons": list(risk_decision.reasons),
            }
            self.latest_signal_records.append(signal_record)
            self.event_logger.emit(
                "signal_evaluated",
                message=(
                    f"Evaluated {signal.strategy_name} signal {signal.signal_key} "
                    f"allowed={risk_decision.allowed} qty={risk_decision.approved_qty}"
                ),
                symbol=self.base_config.symbol,
                strategy=signal.strategy_id,
                signal_key=signal.signal_key,
                direction=signal.direction,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                requested_qty=signal.quantity,
                approved_qty=risk_decision.approved_qty,
                reasons=list(risk_decision.reasons),
            )
            if not risk_decision.allowed:
                continue

            approved_signal = replace(signal, quantity=risk_decision.approved_qty)
            if config.dry_run:
                self.event_logger.emit(
                    "entry_dry_run",
                    message=(
                        f"Dry run: would submit {approved_signal.direction} {self.base_config.symbol} "
                        f"qty={approved_signal.quantity} for {approved_signal.strategy_name}"
                    ),
                    symbol=self.base_config.symbol,
                    strategy=approved_signal.strategy_id,
                    signal_key=approved_signal.signal_key,
                    qty=approved_signal.quantity,
                )
                return

            active_trade = submit_entry(
                self.alpaca_config,
                approved_signal,
                symbol=self.base_config.symbol,
                qty=approved_signal.quantity,
                exit_mode=config.exit_mode,
                entry_timeout_seconds=config.entry_timeout_seconds,
                logger=self.event_logger,
            )
            if active_trade.status == "entry_failed":
                raise RuntimeError(f"Entry order failed for {self.base_config.symbol}; stopping the runner.")
            active_trade_payload = active_trade.to_dict()
            self._maybe_count_trade(active_trade_payload)
            self.state.active_trade = active_trade_payload
            return

    def _collect_latest_signals(
        self,
        market_data: dict[str, pd.DataFrame],
        reference_price: float,
    ) -> list[StrategySignal]:
        config = self.effective_config()
        signals: list[StrategySignal] = []
        minute_df = market_data["1m"]
        session_date = minute_df.index[-1].date()
        session_1m = minute_df[minute_df.index.date == session_date].copy()
        enabled_strategies = [
            strategy_id
            for strategy_id in config.strategies
            if self.runtime_flags["enabled_strategies"].get(strategy_id, True)
        ]
        strategy_order = {strategy_id: index for index, strategy_id in enumerate(enabled_strategies)}

        if "break" in enabled_strategies:
            break_session = session_1m[session_1m.index.time > parse_hhmm("09:35")]
            if len(break_session) >= 3:
                opening_range_bar = get_opening_range_bar(market_data["5m"], session_date)
                setup = detect_break_setup(
                    break_session,
                    opening_range_bar,
                    len(break_session) - 1,
                    config=config.strategy_config,
                )
                signal = materialize_signal(setup, reference_price, config=config.strategy_config)
                if signal is not None:
                    signals.append(signal)

        if "pullback" in enabled_strategies and len(session_1m) >= 4:
            setup = detect_pullback_setup(
                session_1m,
                market_data["1d"],
                len(session_1m) - 2,
                config=config.strategy_config,
            )
            signal = materialize_signal(setup, reference_price, config=config.strategy_config)
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda signal: (strategy_order[signal.strategy_id], signal.signal_time))
        return signals

    def _reconcile_open_trade(self) -> None:
        assert self.alpaca_config is not None
        updated_trade, closure = reconcile_active_trade(
            self.alpaca_config,
            self.state.active_trade,
            logger=self.event_logger,
            strategy_config=self.effective_config().strategy_config,
        )
        self.state.active_trade = updated_trade
        if closure is not None:
            self._record_closure(closure.to_dict())
            self.event_logger.emit(
                "trade_closed",
                message=(
                    f"Trade closed for {self.base_config.symbol} via {closure.exit_reason} "
                    f"net_pnl={closure.net_pnl:.2f}"
                ),
                symbol=self.base_config.symbol,
                exit_reason=closure.exit_reason,
                net_pnl=closure.net_pnl,
                closed_at=closure.closed_at,
            )
            return

        if self.state.active_trade is not None:
            self._maybe_count_trade(self.state.active_trade)

    def _maybe_request_in_process_exit(self, minute_df: pd.DataFrame) -> None:
        assert self.alpaca_config is not None
        active_trade = self.state.active_trade
        if active_trade is None or active_trade.get("status") != "open" or active_trade.get("exit_order_id"):
            return

        latest_bar = minute_df.iloc[-1]
        exit_reason = None
        if active_trade["direction"] == "long":
            if latest_bar["low"] <= active_trade["stop_price"]:
                exit_reason = "stop"
            elif latest_bar["high"] >= active_trade["target_price"]:
                exit_reason = "target"
        else:
            if latest_bar["high"] >= active_trade["stop_price"]:
                exit_reason = "stop"
            elif latest_bar["low"] <= active_trade["target_price"]:
                exit_reason = "target"

        if exit_reason is None:
            return

        if self.effective_config().dry_run:
            self.event_logger.emit(
                "exit_dry_run",
                message=f"Dry run: would flatten {self.base_config.symbol} because {exit_reason}",
                symbol=self.base_config.symbol,
                reason=exit_reason,
            )
            return

        self.state.active_trade = request_flatten(
            self.alpaca_config,
            active_trade,
            logger=self.event_logger,
            reason=exit_reason,
            entry_timeout_seconds=self.effective_config().entry_timeout_seconds,
        )

    def _handle_flatten(self, *, reason: str) -> None:
        assert self.alpaca_config is not None
        if self.state.active_trade is None:
            return
        if self.state.active_trade.get("exit_order_id"):
            self.event_logger.emit(
                "flatten_already_pending",
                message=f"Flatten already pending for {self.base_config.symbol}; skipping duplicate request.",
                symbol=self.base_config.symbol,
                reason=reason,
                order_id=self.state.active_trade["exit_order_id"],
            )
            return
        if self.effective_config().dry_run:
            self.event_logger.emit(
                "flatten_dry_run",
                message=f"Dry run: would flatten {self.base_config.symbol} because {reason}",
                symbol=self.base_config.symbol,
                reason=reason,
            )
            return
        self.state.active_trade = request_flatten(
            self.alpaca_config,
            self.state.active_trade,
            logger=self.event_logger,
            reason=reason,
            entry_timeout_seconds=self.effective_config().entry_timeout_seconds,
        )

    def _attempt_fail_safe_flatten(self, *, reason: str) -> None:
        assert self.alpaca_config is not None
        config = self.effective_config()
        if config.dry_run or config.exit_mode != "in_process" or self.state.active_trade is None:
            return
        if self.state.active_trade.get("exit_order_id"):
            return
        try:
            self.state.active_trade = request_flatten(
                self.alpaca_config,
                self.state.active_trade,
                logger=self.event_logger,
                reason=reason,
                entry_timeout_seconds=config.entry_timeout_seconds,
            )
            self.event_logger.emit(
                "fail_safe_flatten_requested",
                level="WARNING",
                message=f"Submitted fail-safe flatten for {self.base_config.symbol} because {reason}.",
                symbol=self.base_config.symbol,
                reason=reason,
                order_id=self.state.active_trade["exit_order_id"],
            )
        except Exception as exc:
            self.event_logger.emit(
                "fail_safe_flatten_error",
                level="ERROR",
                message=f"Failed to submit fail-safe flatten for {self.base_config.symbol}: {exc}",
                symbol=self.base_config.symbol,
                reason=reason,
                error=str(exc),
            )

    def _record_closure(self, closure: dict[str, Any]) -> None:
        self.state.trade_log.append(closure)
        close_day_key = session_key(closure["closed_at"])
        self.state.add_realized_pnl(close_day_key, float(closure["net_pnl"]))
        self.state.last_exit_at = closure["closed_at"]
        self.state.active_trade = None

    def _maybe_count_trade(self, active_trade_payload: dict[str, Any]) -> None:
        if active_trade_payload.get("status") != "open" or active_trade_payload.get("trade_counted"):
            return
        self.state.increment_trade_count(
            session_key(active_trade_payload.get("opened_at") or active_trade_payload["signal_time"])
        )
        active_trade_payload["trade_counted"] = True

    def _refresh_broker_snapshots(self) -> None:
        if self.base_config.demo_mode:
            return

        assert self.alpaca_config is not None
        self.latest_account = fetch_account(self.alpaca_config)
        self.latest_positions = list_positions(self.alpaca_config)
        self.latest_orders = list_orders(self.alpaca_config, status="all", limit=100)
        self.auth_ok = True
        self.broker_connected = True

    def _runner_status_snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "mode": self.runner_mode,
            "paper_only": True,
            "startup_state": self.startup_state,
            "startup_error": self.startup_error,
            "runtime_overrides_active": self._runtime_overrides_active(),
            "runtime_override_keys": self._runtime_override_keys(),
            "symbol": self.base_config.symbol,
            "configured_strategies": list(self.base_config.strategies),
            "enabled_symbols": self.runtime_flags["enabled_symbols"],
            "enabled_strategies": self.runtime_flags["enabled_strategies"],
            "dry_run": self.runtime_flags["dry_run"],
            "paused_new_entries": self.runtime_flags["paused_new_entries"],
            "market_open": self.market_open,
            "data_fresh": self.data_fresh,
            "last_heartbeat": self.last_heartbeat_at,
            "last_cycle_at": self.last_cycle_at,
            "latest_completed_bar_time": self.last_completed_bar_time,
            "active_trade": self.state.active_trade,
            "last_error": self.last_error,
            "last_warning": self.last_warning,
        }

    def _strategy_status_snapshot(self) -> dict[str, Any]:
        day_key = session_key(pd.Timestamp.now(tz="America/New_York"))
        cooldown_until = None
        cooldown_active = False
        if self.state.last_exit_at:
            cooldown_until_ts = to_et_timestamp(self.state.last_exit_at) + pd.Timedelta(
                minutes=self.effective_config().cooldown_minutes
            )
            cooldown_until = cooldown_until_ts.isoformat()
            cooldown_active = pd.Timestamp.now(tz="America/New_York") < cooldown_until_ts

        return {
            "symbol": self.base_config.symbol,
            "strategies": [
                {
                    "strategy_id": strategy_id,
                    "enabled": self.runtime_flags["enabled_strategies"].get(strategy_id, False),
                }
                for strategy_id in self.base_config.strategies
            ],
            "latest_signals": self.latest_signal_records,
            "active_trade": self.state.active_trade,
            "daily_trade_count": self.state.daily_trade_count.get(day_key, 0),
            "daily_realized_pnl": self.state.daily_realized_pnl.get(day_key, 0.0),
            "cooldown_active": cooldown_active,
            "cooldown_until": cooldown_until,
            "max_daily_loss": self.effective_config().max_daily_loss,
            "max_trades_per_day": self.effective_config().max_trades_per_day,
            "paused_new_entries": self.runtime_flags["paused_new_entries"],
            "latest_completed_bar_time": self.last_completed_bar_time,
        }

    def _health_snapshot(self) -> dict[str, Any]:
        return {
            "paper_only": True,
            "demo_mode": self.base_config.demo_mode,
            "auth_ok": self.auth_ok,
            "broker_connected": self.broker_connected,
            "market_data_connected": self.data_fresh or self.base_config.demo_mode,
            "market_open": self.market_open,
            "data_fresh": self.data_fresh,
            "last_heartbeat": self.last_heartbeat_at,
            "latest_completed_bar_time": self.last_completed_bar_time,
            "reconciliation_ok": self.reconciliation_ok,
            "last_error": self.last_error,
            "last_warning": self.last_warning,
        }

    def _config_snapshot(self) -> dict[str, Any]:
        config = self.effective_config()
        return {
            "symbol": config.symbol,
            "strategies": list(config.strategies),
            "configured_strategies": list(self.base_config.strategies),
            "poll_seconds": config.poll_seconds,
            "entry_timeout_seconds": config.entry_timeout_seconds,
            "minute_lookback": config.minute_lookback,
            "five_minute_lookback": config.five_minute_lookback,
            "daily_lookback": config.daily_lookback,
            "minute_refresh_window": config.minute_refresh_window,
            "five_minute_refresh_window": config.five_minute_refresh_window,
            "daily_refresh_window": config.daily_refresh_window,
            "max_bar_age_seconds": config.max_bar_age_seconds,
            "max_position_qty": config.max_position_qty,
            "max_position_notional": config.max_position_notional,
            "max_daily_loss": config.max_daily_loss,
            "max_trades_per_day": config.max_trades_per_day,
            "one_position_per_symbol": config.one_position_per_symbol,
            "cooldown_minutes": config.cooldown_minutes,
            "flatten_at": config.flatten_at,
            "exit_mode": config.exit_mode,
            "allow_fractional_long": config.allow_fractional_long,
            "risk_per_trade": config.strategy_config.risk_per_trade,
            "rr_ratio": config.strategy_config.rr_ratio,
            "commission_per_unit": config.strategy_config.commission_per_unit,
            "min_gap_pct": config.strategy_config.min_gap_pct,
            "min_gap_atr": config.strategy_config.min_gap_atr,
            "require_displacement": config.strategy_config.require_displacement,
            "dry_run": config.dry_run,
            "demo_mode": config.demo_mode,
            "runtime_overrides_active": self._runtime_overrides_active(),
            "runtime_override_keys": self._runtime_override_keys(),
            "database_path": str(config.database_path),
            "log_path": str(config.log_path),
            "state_path": str(config.state_path),
            "enabled_symbols": self.runtime_flags["enabled_symbols"],
            "enabled_strategies": self.runtime_flags["enabled_strategies"],
            "paused_new_entries": self.runtime_flags["paused_new_entries"],
        }

    def _runtime_override_keys(self) -> list[str]:
        override_keys: list[str] = []
        default_flags = self._default_runtime_flags()
        for key, value in self.runtime_flags.items():
            if value != default_flags[key]:
                override_keys.append(key)
        default_settings = self._default_runtime_settings()
        for key, value in self.runtime_settings.items():
            if value != default_settings[key]:
                override_keys.append(key)
        return sorted(override_keys)

    def _runtime_overrides_active(self) -> bool:
        return bool(self._runtime_override_keys())

    def _load_chart_payload(self, symbol: str, chart_range: str) -> dict[str, Any]:
        timeframe, period = CHART_RANGE_MAP[chart_range]
        end = pd.Timestamp.now(tz="UTC")
        start = period_to_start(end, period)
        assert self.alpaca_config is not None
        bars = fetch_stock_bars(symbol, timeframe, start, end, config=self.alpaca_config)
        points = [
            {
                "time": index.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
            for index, row in bars.tail(120).iterrows()
        ]
        markers = []
        for order in list_orders(self.alpaca_config, status="all", limit=25):
            if order.get("symbol") != symbol:
                continue
            marker_time = order.get("filled_at") or order.get("submitted_at") or order.get("created_at")
            if not marker_time:
                continue
            markers.append(
                {
                    "time": marker_time,
                    "label": order.get("side", "order").upper(),
                    "status": order.get("status"),
                    "price": float(order.get("filled_avg_price") or order.get("limit_price") or order.get("stop_price") or 0.0),
                }
            )
        return {
            "range": chart_range,
            "timeframe": timeframe,
            "points": points,
            "markers": markers,
        }

    def _demo_trade_context(self, symbol: str, chart_range: str) -> dict[str, Any]:
        now = pd.Timestamp.now(tz="America/New_York")
        points = []
        price = self._demo_price
        step_minutes = 1 if chart_range == "1D" else 30 if chart_range == "1W" else 240
        total_points = 60 if chart_range == "1D" else 40
        for index in range(total_points):
            timestamp = (now - pd.Timedelta(minutes=step_minutes * (total_points - index))).tz_convert("UTC")
            drift = ((index % 7) - 3) * 0.18
            close_price = round(price + drift, 2)
            points.append(
                {
                    "time": timestamp.isoformat(),
                    "open": round(close_price - 0.22, 2),
                    "high": round(close_price + 0.4, 2),
                    "low": round(close_price - 0.45, 2),
                    "close": close_price,
                    "volume": float(1000 + index * 13),
                }
            )
        latest_price = points[-1]["close"]
        can_manual_trade, manual_reason = self._manual_trade_availability(symbol)
        position = self._demo_position_for_symbol(symbol)
        return {
            "symbol": symbol,
            "configured_symbol": self.base_config.symbol,
            "paper_only": True,
            "manual_trading_enabled": can_manual_trade,
            "manual_trading_reason": manual_reason,
            "manual_trade_warning": self._manual_position_warning(symbol, position),
            "quote": {
                "last_price": latest_price,
                "bid": round(latest_price - 0.03, 2),
                "ask": round(latest_price + 0.03, 2),
                "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                "absolute_change": latest_price - points[0]["close"],
                "percent_change": ((latest_price - points[0]["close"]) / points[0]["close"]) * 100.0,
            },
            "account": self._demo_account_payload(),
            "position": position,
            "position_summary": self._position_trade_summary(position),
            "asset": {
                "tradable": True,
                "fractionable": True,
                "shortable": False,
                "easy_to_borrow": False,
            },
            "chart": {
                "range": chart_range,
                "timeframe": CHART_RANGE_MAP[chart_range][0],
                "points": points,
                "markers": [],
            },
            "bot": {
                "status_label": self._bot_status_label(),
                "status_reason": self._bot_status_reason(),
                "running": self.running,
                "paused": self.runtime_flags["paused_new_entries"],
                "market_open": self.market_open,
                "data_fresh": self.data_fresh,
                "last_heartbeat": self.last_heartbeat_at,
                "latest_completed_bar_time": self.last_completed_bar_time,
                "enabled_strategies": [
                    strategy_id
                    for strategy_id, enabled in self.runtime_flags["enabled_strategies"].items()
                    if enabled
                ],
            },
        }

    def _manual_trade_availability(self, symbol: str) -> tuple[bool, str | None]:
        if symbol != self.base_config.symbol:
            return False, f"Trading is currently enabled only for {self.base_config.symbol}."
        if self.running:
            return False, "Stop the bot before placing a manual trade so positions stay in sync."
        if self.effective_config().dry_run:
            return False, "Turn off dry run before placing a manual paper trade."
        if not self.runtime_flags["enabled_symbols"].get(symbol, False):
            return False, f"{symbol} is disabled in automation controls right now."
        return True, None

    def _manual_position_warning(self, symbol: str, position: dict[str, Any] | None) -> str | None:
        if symbol != self.base_config.symbol:
            return None
        if position is not None and self.state.active_trade is None:
            return "Manual positions on the bot symbol should be closed before restarting automation."
        return None

    def _lookup_position(self, symbol: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        for position in self.latest_positions:
            if position.get("symbol") == symbol:
                return position
        if self.base_config.demo_mode:
            return self._demo_position_for_symbol(symbol)
        assert self.alpaca_config is not None
        try:
            return get_position(self.alpaca_config, symbol)
        except RuntimeError:
            return None

    def _demo_account_payload(self) -> dict[str, float]:
        return {
            "cash": 100000.0,
            "buying_power": 200000.0,
            "portfolio_value": 100250.0,
            "equity": 100250.0,
            "last_equity": 100000.0,
        }

    def _demo_position_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        if symbol != self.base_config.symbol or self.state.active_trade is None:
            return None
        trade = self.state.active_trade
        return {
            "symbol": symbol,
            "qty": trade.get("filled_qty", 0),
            "side": "long",
            "avg_entry_price": trade.get("entry_fill_price"),
            "market_value": round(float(trade.get("filled_qty", 0)) * self._demo_price, 2),
            "current_price": self._demo_price,
            "unrealized_pl": round((self._demo_price - float(trade.get("entry_fill_price", self._demo_price))) * float(trade.get("filled_qty", 0)), 2),
        }

    def _position_trade_summary(self, position: dict[str, Any] | None) -> dict[str, Any] | None:
        if position is None:
            return None
        active_trade = self.state.active_trade if self.state.active_trade and self.state.active_trade.get("symbol") == position.get("symbol") else None
        opened_at = active_trade.get("opened_at") if active_trade else None
        time_in_trade_minutes = None
        if opened_at:
            time_in_trade_minutes = int((pd.Timestamp.now(tz="UTC") - pd.Timestamp(opened_at)).total_seconds() // 60)
        return {
            "entry_price": float(position.get("avg_entry_price") or active_trade.get("entry_fill_price") or 0.0),
            "current_price": float(position.get("current_price") or 0.0),
            "unrealized_pnl": float(position.get("unrealized_pl") or 0.0),
            "realized_pnl": float(position.get("realized_intraday_pl") or 0.0),
            "time_in_trade_minutes": time_in_trade_minutes,
            "stop_price": active_trade.get("stop_price") if active_trade else None,
            "target_price": active_trade.get("target_price") if active_trade else None,
            "opened_at": opened_at,
        }

    def _bot_status_label(self) -> str:
        if self.startup_state == "starting":
            return "Starting"
        if self.startup_state == "failed":
            return "Needs attention"
        if not self.running:
            return "Stopped"
        if self.runtime_flags["paused_new_entries"]:
            return "Paused"
        if not self.market_open:
            return "Market closed"
        if not self.data_fresh:
            return "Data delayed"
        if self.state.active_trade is not None:
            return "In position"
        return "Waiting for signal"

    def _bot_status_reason(self) -> str:
        if self.startup_error:
            return self.startup_error
        if self.runtime_flags["paused_new_entries"]:
            return "Automation is connected but new entries are paused."
        if not self.running:
            return "The bot is not running right now."
        if not self.market_open:
            return "The market is closed, so the bot is waiting for the next session."
        if not self.data_fresh:
            return "Live market data is stale, so trading is blocked until it refreshes."
        if self.state.active_trade is not None:
            return "A paper position is open and the runner is managing it."
        return "Everything is connected and the bot is waiting for the next valid setup."

    def _publish_all_snapshots(self) -> None:
        self.store.upsert_snapshot("runner_status", self._runner_status_snapshot())
        self.store.upsert_snapshot("health", self._health_snapshot())
        self.store.upsert_snapshot("config", self._config_snapshot())
        self.store.upsert_snapshot("strategy_status", self._strategy_status_snapshot())
        self.store.upsert_snapshot("state", asdict(self.state))
        self.store.upsert_snapshot("market_snapshot", self.latest_market_snapshot)
        self.store.upsert_snapshot("account", self.latest_account)
        self.store.upsert_snapshot("positions", {"items": self.latest_positions})
        self.store.upsert_snapshot("orders", {"items": self.latest_orders})

    def _load_runtime_overrides(self) -> None:
        snapshot = self.store.get_snapshot("runtime_overrides")
        if snapshot is None:
            return
        payload = snapshot["payload"]
        flags = payload.get("flags", {})
        settings = payload.get("settings", {})
        for key, value in flags.items():
            if key in self.runtime_flags:
                self.runtime_flags[key] = value
        for key, value in settings.items():
            if key in self.runtime_settings:
                self.runtime_settings[key] = value

    def _persist_runtime_overrides(self) -> None:
        self.store.upsert_snapshot(
            "runtime_overrides",
            {
                "flags": self.runtime_flags,
                "settings": self.runtime_settings,
            },
        )
