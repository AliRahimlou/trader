from __future__ import annotations

import logging

import pandas as pd

from backtest_utils import (
    BacktestConfig,
    check_exit,
    restrict_to_regular_hours,
    settle_trade,
)
from strategy_context import get_daily_bias, get_previous_day_levels
from strategy_signals import detect_pullback_setup, materialize_signal

LOGGER = logging.getLogger(__name__)


def run_strategy_video2(
    minute_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    *,
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    config = config or BacktestConfig()
    trades: list[dict[str, object]] = []
    minute_df = restrict_to_regular_hours(minute_df)
    minute_df = minute_df.copy()
    minute_df["session_date"] = minute_df.index.date

    for session_date, day_data in minute_df.groupby("session_date"):
        if len(day_data) < 20:
            continue

        bias = get_daily_bias(daily_df, session_date)
        if bias == "neutral":
            continue

        prev_day_high, prev_day_low = get_previous_day_levels(daily_df, session_date)
        if prev_day_high is None or prev_day_low is None:
            continue

        open_trade: dict[str, object] | None = None
        day_1min = day_data.copy()

        for i in range(2, len(day_1min) - 1):
            bar = day_1min.iloc[i]
            bar_time = day_1min.index[i]

            if open_trade is not None:
                exit_price, reason = check_exit(
                    bar,
                    direction=str(open_trade["direction"]),
                    stop_price=float(open_trade["stop_price"]),
                    target_price=float(open_trade["target_price"]),
                )
                if exit_price is not None and reason is not None:
                    trades.append(
                        settle_trade(
                            open_trade,
                            exit_price=exit_price,
                            exit_time=bar_time,
                            reason=reason,
                            config=config,
                        )
                    )
                    open_trade = None
                continue

            setup = detect_pullback_setup(
                day_1min,
                daily_df,
                i,
                config=config,
            )
            if setup is None:
                continue

            next_time = day_1min.index[i + 1]
            signal = materialize_signal(setup, setup.suggested_entry_price, config=config)
            if signal is None:
                continue

            open_trade = {
                "strategy": signal.strategy_name,
                "session_date": session_date,
                "direction": signal.direction,
                "entry_time": next_time,
                "entry_price": signal.entry_reference_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "quantity": signal.quantity,
                "planned_risk": abs(signal.entry_reference_price - signal.stop_price) * signal.quantity * config.value_per_point,
            }

    return pd.DataFrame(trades)
