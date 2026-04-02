from __future__ import annotations

import logging
from datetime import time

import pandas as pd

from backtest_utils import (
    BacktestConfig,
    check_exit,
    restrict_to_regular_hours,
    settle_trade,
)
from strategy_signals import detect_break_setup, get_opening_range_bar, materialize_signal

LOGGER = logging.getLogger(__name__)


def run_strategy_video1(
    minute_df: pd.DataFrame,
    five_min_df: pd.DataFrame,
    *,
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    config = config or BacktestConfig()
    trades: list[dict[str, object]] = []
    minute_df = restrict_to_regular_hours(minute_df)
    five_min_df = restrict_to_regular_hours(five_min_df)
    minute_df = minute_df.copy()
    minute_df["session_date"] = minute_df.index.date

    for session_date, day_data in minute_df.groupby("session_date"):
        if len(day_data) < 20:
            continue

        opening_range_bar = get_opening_range_bar(five_min_df, session_date)
        if opening_range_bar is None:
            LOGGER.debug("Skipping %s: no 09:30 opening-range candle", session_date)
            continue

        day_1min = day_data[day_data.index.time > time(9, 35)].copy()
        if len(day_1min) < 10:
            continue

        open_trade: dict[str, object] | None = None
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

            setup = detect_break_setup(
                day_1min,
                opening_range_bar,
                i,
                config=config,
            )
            if setup is None:
                continue

            entry_bar = day_1min.iloc[i + 1]
            entry_time = day_1min.index[i + 1]
            entry_price = float(entry_bar["open"])
            signal = materialize_signal(setup, entry_price, config=config)
            if signal is None:
                continue

            open_trade = {
                "strategy": signal.strategy_name,
                "session_date": session_date,
                "direction": signal.direction,
                "entry_time": entry_time,
                "entry_price": entry_price,
                "stop_price": signal.stop_price,
                "target_price": signal.target_price,
                "quantity": signal.quantity,
                "planned_risk": abs(entry_price - signal.stop_price) * signal.quantity * config.value_per_point,
            }

    return pd.DataFrame(trades)
