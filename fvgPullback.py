from __future__ import annotations

import logging

import pandas as pd

from backtest_utils import (
    BacktestConfig,
    calculate_position_size,
    check_exit,
    get_fair_value_gap_bounds,
    get_fair_value_gap_direction,
    restrict_to_regular_hours,
    settle_trade,
)

LOGGER = logging.getLogger(__name__)


def get_daily_bias(daily_df: pd.DataFrame, current_date) -> str:
    history = daily_df[daily_df.index.date < current_date]
    if len(history) < 2:
        return "neutral"

    prev_day = history.iloc[-1]
    prev_prev_day = history.iloc[-2]
    hh_hl = (prev_day["high"] > prev_prev_day["high"]) and (prev_day["low"] > prev_prev_day["low"])
    lh_ll = (prev_day["high"] < prev_prev_day["high"]) and (prev_day["low"] < prev_prev_day["low"])
    if hh_hl:
        return "long"
    if lh_ll:
        return "short"
    return "neutral"


def get_previous_day_levels(daily_df: pd.DataFrame, current_date) -> tuple[float | None, float | None]:
    history = daily_df[daily_df.index.date < current_date]
    if history.empty:
        return None, None
    previous_day = history.iloc[-1]
    return float(previous_day["high"]), float(previous_day["low"])


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
        swept_prev_low = False
        swept_prev_high = False
        day_1min = day_data.copy()

        for i in range(2, len(day_1min) - 1):
            bar = day_1min.iloc[i]
            bar_time = day_1min.index[i]
            swept_prev_low = swept_prev_low or bar["low"] < prev_day_low
            swept_prev_high = swept_prev_high or bar["high"] > prev_day_high

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

            fvg_direction = get_fair_value_gap_direction(
                day_1min,
                i,
                min_gap_pct=config.min_gap_pct,
                min_gap_atr=config.min_gap_atr,
                require_displacement=config.require_displacement,
            )
            if fvg_direction is None:
                continue

            gap_low, gap_high = get_fair_value_gap_bounds(day_1min, i, fvg_direction)
            gap_midpoint = (gap_low + gap_high) / 2
            next_bar = day_1min.iloc[i + 1]
            next_time = day_1min.index[i + 1]

            if (
                bias == "long"
                and swept_prev_low
                and fvg_direction == "bullish"
                and bar["close"] > prev_day_low
                and next_bar["low"] <= gap_high
                and next_bar["close"] >= gap_midpoint
            ):
                entry_price = max(float(next_bar["open"]), gap_midpoint)
                stop_price = min(float(day_1min.iloc[: i + 1]["low"].min()), gap_low)
                target_price = prev_day_high
                trade_direction = "long"
            elif (
                bias == "short"
                and swept_prev_high
                and fvg_direction == "bearish"
                and bar["close"] < prev_day_high
                and next_bar["high"] >= gap_low
                and next_bar["close"] <= gap_midpoint
            ):
                entry_price = min(float(next_bar["open"]), gap_midpoint)
                stop_price = max(float(day_1min.iloc[: i + 1]["high"].max()), gap_high)
                target_price = prev_day_low
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
                    next_time,
                )
                continue

            open_trade = {
                "strategy": "Daily Sweep + Pullback FVG",
                "session_date": session_date,
                "direction": trade_direction,
                "entry_time": next_time,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "target_price": target_price,
                "quantity": quantity,
                "planned_risk": abs(entry_price - stop_price) * quantity * config.value_per_point,
            }

    return pd.DataFrame(trades)
