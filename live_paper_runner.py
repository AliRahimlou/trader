from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pandas as pd

from alpaca_api import (
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
    period_to_start,
    submit_order,
    wait_for_order_terminal,
)
from live_config import PaperTradingConfig, build_alpaca_config, build_argument_parser, config_from_args
from live_execution import (
    ensure_no_unmanaged_broker_state,
    ensure_paper_account,
    reconcile_active_trade,
    request_flatten,
    submit_entry,
)
from live_logging import StructuredLogger, setup_console_logging
from live_risk import evaluate_entry_risk
from live_scheduler import StaleDataError, parse_hhmm, session_key, to_et_timestamp, validate_latest_bar
from live_state import RunnerState, StateStore
from strategy_signals import (
    StrategySignal,
    detect_break_setup,
    detect_pullback_setup,
    get_opening_range_bar,
    materialize_signal,
)


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()
    trading_config = config_from_args(args)
    setup_console_logging(args.verbose)
    event_logger = StructuredLogger(trading_config.log_path)
    alpaca_config = build_alpaca_config(trading_config)
    ensure_paper_account(alpaca_config)

    if not trading_config.dry_run and not trading_config.paper_confirm:
        raise SystemExit("Refusing to run paper execution without --paper-confirm.")

    state_store = StateStore(trading_config.state_path)
    if trading_config.reset_state and trading_config.state_path.exists():
        trading_config.state_path.unlink()
    state = state_store.load(symbol=trading_config.symbol, strategies=list(trading_config.strategies))
    state.prune(trading_config.keep_state_days)

    account = fetch_account(alpaca_config)
    event_logger.emit(
        "runner_start",
        message=f"Runner started for {trading_config.symbol}. {format_account_summary(account)}",
        symbol=trading_config.symbol,
        strategies=list(trading_config.strategies),
        dry_run=trading_config.dry_run,
        once=trading_config.once,
        exit_mode=trading_config.exit_mode,
        state_path=str(trading_config.state_path),
        log_path=str(trading_config.log_path),
    )

    try:
        startup_reconcile(alpaca_config, trading_config, state, event_logger)
        state_store.save(state)

        if trading_config.smoke_test:
            run_smoke_test(alpaca_config, trading_config, event_logger)
            return

        while True:
            run_cycle(alpaca_config, trading_config, state, state_store, event_logger)
            if trading_config.once:
                return
            time.sleep(trading_config.poll_seconds)
    except KeyboardInterrupt:
        attempt_fail_safe_flatten(
            alpaca_config,
            trading_config,
            state,
            event_logger,
            reason="operator_interrupt",
        )
        event_logger.emit("runner_shutdown", message="Runner interrupted by user.", symbol=trading_config.symbol)
    except StaleDataError as exc:
        attempt_fail_safe_flatten(
            alpaca_config,
            trading_config,
            state,
            event_logger,
            reason="stale_data_fail_safe",
        )
        event_logger.emit(
            "stale_data_halt",
            level="ERROR",
            message=f"Runner halted because market data is stale: {exc}",
            symbol=trading_config.symbol,
            error=str(exc),
        )
        raise SystemExit(str(exc)) from exc
    except Exception as exc:
        attempt_fail_safe_flatten(
            alpaca_config,
            trading_config,
            state,
            event_logger,
            reason="runner_exception_fail_safe",
        )
        event_logger.emit(
            "runner_error",
            level="ERROR",
            message=f"Runner stopped with error: {exc}",
            symbol=trading_config.symbol,
            error=str(exc),
        )
        raise
    finally:
        state_store.save(state)


def startup_reconcile(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    event_logger: StructuredLogger,
) -> None:
    ensure_no_unmanaged_broker_state(
        alpaca_config,
        symbol=trading_config.symbol,
        active_trade=state.active_trade,
    )
    if state.active_trade is None:
        event_logger.emit(
            "startup_reconcile",
            message=f"No active persisted trade for {trading_config.symbol}.",
            symbol=trading_config.symbol,
        )
        return

    updated_trade, closure = reconcile_active_trade(
        alpaca_config,
        state.active_trade,
        logger=event_logger,
        strategy_config=trading_config.strategy_config,
    )
    state.active_trade = updated_trade
    if closure is not None:
        record_closure(state, closure.to_dict())
        event_logger.emit(
            "startup_trade_closed",
            message=(
                f"Startup reconciliation closed {trading_config.symbol} via {closure.exit_reason} "
                f"net_pnl={closure.net_pnl:.2f}"
            ),
            symbol=trading_config.symbol,
            exit_reason=closure.exit_reason,
            net_pnl=closure.net_pnl,
            closed_at=closure.closed_at,
        )
        return

    if state.active_trade is not None:
        maybe_count_trade(state, state.active_trade)
    event_logger.emit(
        "startup_reconcile",
        message=f"Startup reconciliation completed for {trading_config.symbol}.",
        symbol=trading_config.symbol,
        has_active_trade=state.active_trade is not None,
        active_trade_status=(state.active_trade or {}).get("status"),
    )


def run_cycle(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    state_store: StateStore,
    event_logger: StructuredLogger,
) -> None:
    clock = fetch_clock(alpaca_config)
    now = to_et_timestamp(clock["timestamp"])
    state.prune(trading_config.keep_state_days)

    if not clock.get("is_open"):
        event_logger.emit(
            "market_closed",
            message=f"Market closed at {now}. Next open: {clock.get('next_open')}",
            symbol=trading_config.symbol,
            now=str(now),
            next_open=clock.get("next_open"),
        )
        state_store.save(state)
        return

    market_data = load_market_context(alpaca_config, trading_config, clock["timestamp"])
    latest_bar_time = market_data["1m"].index.max()
    validate_latest_bar(now, latest_bar_time, max_bar_age_seconds=trading_config.max_bar_age_seconds)
    log_market_snapshot(alpaca_config, trading_config.symbol, latest_bar_time, market_data, event_logger)

    last_processed_bar = to_et_timestamp(state.last_processed_bar) if state.last_processed_bar else None
    if last_processed_bar is not None and latest_bar_time <= last_processed_bar:
        event_logger.emit(
            "bar_unchanged",
            level="DEBUG",
            message=f"No new 1-minute bar. Latest processed={last_processed_bar} current={latest_bar_time}",
            symbol=trading_config.symbol,
            latest_bar_time=str(latest_bar_time),
        )
        state_store.save(state)
        return

    if state.active_trade is not None:
        reconcile_open_trade(alpaca_config, trading_config, state, event_logger)

    if state.active_trade is None:
        ensure_no_unmanaged_broker_state(
            alpaca_config,
            symbol=trading_config.symbol,
            active_trade=None,
        )

    if now.time() >= parse_hhmm(trading_config.flatten_at):
        if state.active_trade is not None:
            handle_flatten(alpaca_config, trading_config, state, event_logger, reason="end_of_day_flatten")
        else:
            event_logger.emit(
                "flatten_window",
                message=f"Past flatten cutoff {trading_config.flatten_at} ET; no new entries.",
                symbol=trading_config.symbol,
                now=str(now),
            )
        state.last_processed_bar = latest_bar_time.isoformat()
        state_store.save(state)
        return

    if state.active_trade is not None and trading_config.exit_mode == "in_process":
        maybe_request_in_process_exit(alpaca_config, trading_config, state, market_data["1m"], event_logger)

    if state.active_trade is None:
        maybe_submit_new_entry(alpaca_config, trading_config, state, market_data, now, event_logger)

    state.last_processed_bar = latest_bar_time.isoformat()
    state_store.save(state)


def load_market_context(
    alpaca_config,
    trading_config: PaperTradingConfig,
    clock_timestamp: str,
) -> dict[str, pd.DataFrame]:
    end = _to_utc_timestamp(clock_timestamp)
    return {
        "1m": fetch_stock_bars(
            trading_config.symbol,
            "1m",
            period_to_start(end, trading_config.minute_lookback),
            end,
            config=alpaca_config,
        ),
        "5m": fetch_stock_bars(
            trading_config.symbol,
            "5m",
            period_to_start(end, trading_config.five_minute_lookback),
            end,
            config=alpaca_config,
        ),
        "1d": fetch_stock_bars(
            trading_config.symbol,
            "1d",
            period_to_start(end, trading_config.daily_lookback),
            end,
            config=alpaca_config,
        ),
    }


def log_market_snapshot(
    alpaca_config,
    symbol: str,
    latest_bar_time: pd.Timestamp,
    market_data: dict[str, pd.DataFrame],
    event_logger: StructuredLogger,
) -> None:
    trade = fetch_latest_trade(alpaca_config, symbol)
    quote = fetch_latest_quote(alpaca_config, symbol)
    event_logger.emit(
        "market_snapshot",
        level="DEBUG",
        message=(
            f"Fresh bars loaded for {symbol}. latest_bar={latest_bar_time} "
            f"latest_trade={trade['t']} latest_quote={quote['t']}"
        ),
        symbol=symbol,
        latest_bar_time=str(latest_bar_time),
        latest_trade_timestamp=trade["t"],
        latest_trade_price=trade["p"],
        latest_quote_timestamp=quote["t"],
        bid=quote["bp"],
        ask=quote["ap"],
        rows_1m=len(market_data["1m"]),
        rows_5m=len(market_data["5m"]),
        rows_1d=len(market_data["1d"]),
    )


def maybe_submit_new_entry(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    market_data: dict[str, pd.DataFrame],
    now: pd.Timestamp,
    event_logger: StructuredLogger,
) -> None:
    latest_trade = fetch_latest_trade(alpaca_config, trading_config.symbol)
    reference_price = float(latest_trade["p"])
    account = fetch_account(alpaca_config)
    asset = get_asset(alpaca_config, trading_config.symbol)
    signals = collect_latest_signals(market_data, trading_config, reference_price)

    if not signals:
        event_logger.emit(
            "no_signal",
            level="DEBUG",
            message=f"No live signal for {trading_config.symbol} on bar {market_data['1m'].index.max()}",
            symbol=trading_config.symbol,
        )
        return

    for signal in signals:
        signal_day_key = session_key(signal.signal_time)
        if state.is_signal_processed(signal_day_key, signal.signal_key):
            event_logger.emit(
                "duplicate_signal_skipped",
                level="DEBUG",
                message=f"Duplicate signal skipped: {signal.signal_key}",
                symbol=trading_config.symbol,
                signal_key=signal.signal_key,
            )
            continue

        risk_decision = evaluate_entry_risk(
            signal,
            state=state,
            account=account,
            asset=asset,
            now=now,
            max_position_qty=trading_config.max_position_qty,
            max_position_notional=trading_config.max_position_notional,
            max_daily_loss=trading_config.max_daily_loss,
            max_trades_per_day=trading_config.max_trades_per_day,
            cooldown_minutes=trading_config.cooldown_minutes,
            one_position_per_symbol=trading_config.one_position_per_symbol,
            exit_mode=trading_config.exit_mode,
            allow_fractional_long=trading_config.allow_fractional_long,
        )
        state.mark_signal_processed(signal_day_key, signal.signal_key)

        event_logger.emit(
            "signal_evaluated",
            message=(
                f"Evaluated {signal.strategy_name} signal {signal.signal_key} "
                f"allowed={risk_decision.allowed} qty={risk_decision.approved_qty}"
            ),
            symbol=trading_config.symbol,
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
        if trading_config.dry_run:
            event_logger.emit(
                "entry_dry_run",
                message=(
                    f"Dry run: would submit {approved_signal.direction} {trading_config.symbol} "
                    f"qty={approved_signal.quantity} for {approved_signal.strategy_name}"
                ),
                symbol=trading_config.symbol,
                strategy=approved_signal.strategy_id,
                signal_key=approved_signal.signal_key,
                qty=approved_signal.quantity,
            )
            return

        active_trade = submit_entry(
            alpaca_config,
            approved_signal,
            symbol=trading_config.symbol,
            qty=approved_signal.quantity,
            exit_mode=trading_config.exit_mode,
            entry_timeout_seconds=trading_config.entry_timeout_seconds,
            logger=event_logger,
        )
        if active_trade.status == "entry_failed":
            raise RuntimeError(f"Entry order failed for {trading_config.symbol}; stopping the runner.")

        active_trade_payload = active_trade.to_dict()
        maybe_count_trade(state, active_trade_payload)
        state.active_trade = active_trade_payload
        return


def maybe_count_trade(state: RunnerState, active_trade_payload: dict[str, Any]) -> None:
    if active_trade_payload.get("status") != "open" or active_trade_payload.get("trade_counted"):
        return
    state.increment_trade_count(session_key(active_trade_payload.get("opened_at") or active_trade_payload["signal_time"]))
    active_trade_payload["trade_counted"] = True


def collect_latest_signals(
    market_data: dict[str, pd.DataFrame],
    trading_config: PaperTradingConfig,
    reference_price: float,
) -> list[StrategySignal]:
    signals: list[StrategySignal] = []
    minute_df = market_data["1m"]
    session_date = minute_df.index[-1].date()
    session_1m = minute_df[minute_df.index.date == session_date].copy()
    strategy_order = {strategy_id: index for index, strategy_id in enumerate(trading_config.strategies)}

    if "break" in trading_config.strategies:
        break_session = session_1m[session_1m.index.time > parse_hhmm("09:35")]
        if len(break_session) >= 3:
            opening_range_bar = get_opening_range_bar(market_data["5m"], session_date)
            setup = detect_break_setup(
                break_session,
                opening_range_bar,
                len(break_session) - 1,
                config=trading_config.strategy_config,
            )
            signal = materialize_signal(setup, reference_price, config=trading_config.strategy_config)
            if signal is not None:
                signals.append(signal)

    if "pullback" in trading_config.strategies and len(session_1m) >= 4:
        setup = detect_pullback_setup(
            session_1m,
            market_data["1d"],
            len(session_1m) - 2,
            config=trading_config.strategy_config,
        )
        signal = materialize_signal(setup, reference_price, config=trading_config.strategy_config)
        if signal is not None:
            signals.append(signal)

    signals.sort(key=lambda signal: (strategy_order[signal.strategy_id], signal.signal_time))
    return signals


def reconcile_open_trade(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    event_logger: StructuredLogger,
) -> None:
    updated_trade, closure = reconcile_active_trade(
        alpaca_config,
        state.active_trade,
        logger=event_logger,
        strategy_config=trading_config.strategy_config,
    )
    state.active_trade = updated_trade

    if closure is not None:
        record_closure(state, closure.to_dict())
        event_logger.emit(
            "trade_closed",
            message=(
                f"Trade closed for {trading_config.symbol} via {closure.exit_reason} "
                f"net_pnl={closure.net_pnl:.2f}"
            ),
            symbol=trading_config.symbol,
            exit_reason=closure.exit_reason,
            net_pnl=closure.net_pnl,
            closed_at=closure.closed_at,
        )
        return

    if state.active_trade is not None:
        maybe_count_trade(state, state.active_trade)


def maybe_request_in_process_exit(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    minute_df: pd.DataFrame,
    event_logger: StructuredLogger,
) -> None:
    active_trade = state.active_trade
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

    if trading_config.dry_run:
        event_logger.emit(
            "exit_dry_run",
            message=f"Dry run: would flatten {trading_config.symbol} because {exit_reason}",
            symbol=trading_config.symbol,
            reason=exit_reason,
        )
        return

    state.active_trade = request_flatten(
        alpaca_config,
        active_trade,
        logger=event_logger,
        reason=exit_reason,
        entry_timeout_seconds=trading_config.entry_timeout_seconds,
    )


def handle_flatten(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    event_logger: StructuredLogger,
    *,
    reason: str,
) -> None:
    if state.active_trade is None:
        return
    if state.active_trade.get("exit_order_id"):
        event_logger.emit(
            "flatten_already_pending",
            message=f"Flatten already pending for {trading_config.symbol}; skipping duplicate request.",
            symbol=trading_config.symbol,
            reason=reason,
            order_id=state.active_trade["exit_order_id"],
        )
        return
    if trading_config.dry_run:
        event_logger.emit(
            "flatten_dry_run",
            message=f"Dry run: would flatten {trading_config.symbol} because {reason}",
            symbol=trading_config.symbol,
            reason=reason,
        )
        return
    state.active_trade = request_flatten(
        alpaca_config,
        state.active_trade,
        logger=event_logger,
        reason=reason,
        entry_timeout_seconds=trading_config.entry_timeout_seconds,
    )


def attempt_fail_safe_flatten(
    alpaca_config,
    trading_config: PaperTradingConfig,
    state: RunnerState,
    event_logger: StructuredLogger,
    *,
    reason: str,
) -> None:
    if trading_config.dry_run or trading_config.exit_mode != "in_process" or state.active_trade is None:
        return
    if state.active_trade.get("exit_order_id"):
        return

    try:
        state.active_trade = request_flatten(
            alpaca_config,
            state.active_trade,
            logger=event_logger,
            reason=reason,
            entry_timeout_seconds=trading_config.entry_timeout_seconds,
        )
        event_logger.emit(
            "fail_safe_flatten_requested",
            level="WARNING",
            message=f"Submitted fail-safe flatten for {trading_config.symbol} because {reason}.",
            symbol=trading_config.symbol,
            reason=reason,
            order_id=state.active_trade["exit_order_id"],
        )
    except Exception as exc:
        event_logger.emit(
            "fail_safe_flatten_error",
            level="ERROR",
            message=f"Failed to submit fail-safe flatten for {trading_config.symbol}: {exc}",
            symbol=trading_config.symbol,
            reason=reason,
            error=str(exc),
        )


def run_smoke_test(alpaca_config, trading_config: PaperTradingConfig, event_logger: StructuredLogger) -> None:
    clock = fetch_clock(alpaca_config)
    if not clock.get("is_open"):
        raise RuntimeError("Smoke test requires the market to be open.")

    symbol = (trading_config.smoke_test_symbol or trading_config.symbol).upper()
    asset = get_asset(alpaca_config, symbol)
    if not asset.get("tradable"):
        raise RuntimeError(f"{symbol} is not tradable for the smoke test.")
    if not asset.get("fractionable"):
        raise RuntimeError(f"{symbol} is not fractionable; smoke test uses a notional order.")

    if trading_config.dry_run:
        event_logger.emit(
            "smoke_test_dry_run",
            message=f"Dry run: would submit smoke test order for {symbol}.",
            symbol=symbol,
            notional=trading_config.smoke_test_notional,
        )
        return

    entry_order = submit_order(
        alpaca_config,
        symbol=symbol,
        side="buy",
        order_type="market",
        time_in_force="day",
        notional=trading_config.smoke_test_notional,
        client_order_id=f"smoke-{pd.Timestamp.now(tz='UTC').strftime('%Y%m%d%H%M%S')}",
    )
    event_logger.emit(
        "smoke_test_entry_submitted",
        message=f"Smoke test entry submitted: {format_order_summary(entry_order)}",
        symbol=symbol,
        order_id=entry_order["id"],
    )
    filled_entry = wait_for_order_terminal(
        alpaca_config,
        entry_order["id"],
        timeout_seconds=trading_config.entry_timeout_seconds,
    )
    event_logger.emit(
        "smoke_test_entry_update",
        message=f"Smoke test entry update: {format_order_summary(filled_entry)}",
        symbol=symbol,
        order_id=filled_entry["id"],
        status=filled_entry.get("status"),
    )
    if filled_entry.get("status") != "filled":
        raise RuntimeError(f"Smoke test entry did not fill. Status={filled_entry.get('status')}")

    close_order = close_position(alpaca_config, symbol)
    event_logger.emit(
        "smoke_test_exit_submitted",
        message=f"Smoke test exit submitted: {format_order_summary(close_order)}",
        symbol=symbol,
        order_id=close_order["id"],
    )
    filled_exit = wait_for_order_terminal(
        alpaca_config,
        close_order["id"],
        timeout_seconds=trading_config.entry_timeout_seconds,
    )
    event_logger.emit(
        "smoke_test_exit_update",
        message=f"Smoke test exit update: {format_order_summary(filled_exit)}",
        symbol=symbol,
        order_id=filled_exit["id"],
        status=filled_exit.get("status"),
    )
    if filled_exit.get("status") != "filled":
        raise RuntimeError(f"Smoke test exit did not fill. Status={filled_exit.get('status')}")

    try:
        get_position(alpaca_config, symbol)
        raise RuntimeError(f"Smoke test expected no open position in {symbol}, but one still exists.")
    except RuntimeError:
        event_logger.emit(
            "smoke_test_complete",
            message=f"Smoke test completed for {symbol}.",
            symbol=symbol,
            notional=trading_config.smoke_test_notional,
        )


def record_closure(state: RunnerState, closure: dict[str, Any]) -> None:
    state.trade_log.append(closure)
    close_day_key = session_key(closure["closed_at"])
    state.add_realized_pnl(close_day_key, float(closure["net_pnl"]))
    state.last_exit_at = closure["closed_at"]
    state.active_trade = None


def _to_utc_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


if __name__ == "__main__":
    main()
