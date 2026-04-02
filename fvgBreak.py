from __future__ import annotations

import logging
from datetime import time

import pandas as pd

from backtest_utils import (
    BacktestConfig,
    calculate_position_size,
    check_exit,
    get_fair_value_gap_direction,
    restrict_to_regular_hours,
    settle_trade,
)

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

        opening_range = five_min_df[
            (five_min_df.index.date == session_date) & (five_min_df.index.time == time(9, 30))
        ]
        if opening_range.empty:
            LOGGER.debug("Skipping %s: no 09:30 opening-range candle", session_date)
            continue

        or_high = float(opening_range["high"].iloc[0])
        or_low = float(opening_range["low"].iloc[0])
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

            direction = get_fair_value_gap_direction(
                day_1min,
                i,
                min_gap_pct=config.min_gap_pct,
                min_gap_atr=config.min_gap_atr,
                require_displacement=config.require_displacement,
            )
            if direction is None:
                continue

            entry_bar = day_1min.iloc[i + 1]
            entry_time = day_1min.index[i + 1]
            entry_price = float(entry_bar["open"])

            if direction == "bullish" and bar["close"] > or_high:
                stop_price = or_low
                target_price = entry_price + (entry_price - stop_price) * config.rr_ratio
                trade_direction = "long"
            elif direction == "bearish" and bar["close"] < or_low:
                stop_price = or_high
                target_price = entry_price - (stop_price - entry_price) * config.rr_ratio
                trade_direction = "short"
            else:
                continue

            if target_price <= entry_price and trade_direction == "long":
                continue
            if target_price >= entry_price and trade_direction == "short":
                continue

            quantity = calculate_position_size(entry_price, stop_price, config)
            if quantity < 1:
                LOGGER.debug(
                    "Skipping %s %s at %s: quantity rounded to zero",
                    session_date,
                    trade_direction,
                    entry_time,
                )
                continue

            open_trade = {
                "strategy": "Opening Range + FVG",
                "session_date": session_date,
                "direction": trade_direction,
                "entry_time": entry_time,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "quantity": quantity,
                "planned_risk": abs(entry_price - stop_price) * quantity * config.value_per_point,
            }

    return pd.DataFrame(trades)
