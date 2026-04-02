from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

from backtest_utils import BacktestConfig, calculate_position_size, get_fair_value_gap_bounds, get_fair_value_gap_direction, restrict_to_regular_hours
from fvgPullback import get_daily_bias, get_previous_day_levels


@dataclass(frozen=True)
class TradeSignal:
    strategy_id: str
    strategy_name: str
    direction: str
    signal_time: pd.Timestamp
    estimated_entry_price: float
    stop_price: float
    target_price: float
    quantity: float
    reason: str

    @property
    def signal_key(self) -> str:
        return f"{self.strategy_id}:{self.direction}:{self.signal_time.isoformat()}"


def generate_break_signal(
    minute_df: pd.DataFrame,
    five_min_df: pd.DataFrame,
    *,
    config: BacktestConfig,
) -> TradeSignal | None:
    minute_df = restrict_to_regular_hours(minute_df)
    five_min_df = restrict_to_regular_hours(five_min_df)
    if minute_df.empty or five_min_df.empty:
        return None

    session_date = minute_df.index[-1].date()
    opening_range = five_min_df[
        (five_min_df.index.date == session_date) & (five_min_df.index.time == time(9, 30))
    ]
    if opening_range.empty:
        return None

    day_1min = minute_df[minute_df.index.date == session_date]
    day_1min = day_1min[day_1min.index.time > time(9, 35)].copy()
    if len(day_1min) < 3:
        return None

    i = len(day_1min) - 1
    bar = day_1min.iloc[i]
    signal_time = day_1min.index[i]
    direction = get_fair_value_gap_direction(
        day_1min,
        i,
        min_gap_pct=config.min_gap_pct,
        min_gap_atr=config.min_gap_atr,
        require_displacement=config.require_displacement,
    )
    if direction is None:
        return None

    estimated_entry_price = float(bar["close"])
    or_high = float(opening_range["high"].iloc[0])
    or_low = float(opening_range["low"].iloc[0])

    if direction == "bullish" and bar["close"] > or_high:
        trade_direction = "long"
        stop_price = or_low
        target_price = estimated_entry_price + (estimated_entry_price - stop_price) * config.rr_ratio
        reason = "Bullish FVG closed above opening-range high"
    elif direction == "bearish" and bar["close"] < or_low:
        trade_direction = "short"
        stop_price = or_high
        target_price = estimated_entry_price - (stop_price - estimated_entry_price) * config.rr_ratio
        reason = "Bearish FVG closed below opening-range low"
    else:
        return None

    quantity = float(calculate_position_size(estimated_entry_price, stop_price, config))
    if quantity < 1:
        return None

    return TradeSignal(
        strategy_id="break",
        strategy_name="Opening Range + FVG",
        direction=trade_direction,
        signal_time=signal_time,
        estimated_entry_price=estimated_entry_price,
        stop_price=float(stop_price),
        target_price=float(target_price),
        quantity=quantity,
        reason=reason,
    )


def generate_pullback_signal(
    minute_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    *,
    config: BacktestConfig,
) -> TradeSignal | None:
    minute_df = restrict_to_regular_hours(minute_df)
    if minute_df.empty or daily_df.empty:
        return None

    session_date = minute_df.index[-1].date()
    day_1min = minute_df[minute_df.index.date == session_date].copy()
    if len(day_1min) < 3:
        return None

    bias = get_daily_bias(daily_df, session_date)
    if bias == "neutral":
        return None

    prev_day_high, prev_day_low = get_previous_day_levels(daily_df, session_date)
    if prev_day_high is None or prev_day_low is None:
        return None

    i = len(day_1min) - 2
    bar = day_1min.iloc[i]
    next_bar = day_1min.iloc[i + 1]
    signal_time = day_1min.index[i + 1]
    fvg_direction = get_fair_value_gap_direction(
        day_1min,
        i,
        min_gap_pct=config.min_gap_pct,
        min_gap_atr=config.min_gap_atr,
        require_displacement=config.require_displacement,
    )
    if fvg_direction is None:
        return None

    swept_prev_low = bool((day_1min.iloc[: i + 1]["low"] < prev_day_low).any())
    swept_prev_high = bool((day_1min.iloc[: i + 1]["high"] > prev_day_high).any())
    gap_low, gap_high = get_fair_value_gap_bounds(day_1min, i, fvg_direction)
    gap_midpoint = (gap_low + gap_high) / 2
    estimated_entry_price = float(next_bar["close"])

    if (
        bias == "long"
        and swept_prev_low
        and fvg_direction == "bullish"
        and bar["close"] > prev_day_low
        and next_bar["low"] <= gap_high
        and next_bar["close"] >= gap_midpoint
    ):
        trade_direction = "long"
        stop_price = min(float(day_1min.iloc[: i + 1]["low"].min()), gap_low)
        target_price = float(prev_day_high)
        reason = "Daily low sweep with bullish pullback FVG"
    elif (
        bias == "short"
        and swept_prev_high
        and fvg_direction == "bearish"
        and bar["close"] < prev_day_high
        and next_bar["high"] >= gap_low
        and next_bar["close"] <= gap_midpoint
    ):
        trade_direction = "short"
        stop_price = max(float(day_1min.iloc[: i + 1]["high"].max()), gap_high)
        target_price = float(prev_day_low)
        reason = "Daily high sweep with bearish pullback FVG"
    else:
        return None

    if trade_direction == "long" and target_price <= estimated_entry_price:
        return None
    if trade_direction == "short" and target_price >= estimated_entry_price:
        return None

    quantity = float(calculate_position_size(estimated_entry_price, stop_price, config))
    if quantity < 1:
        return None

    return TradeSignal(
        strategy_id="pullback",
        strategy_name="Daily Sweep + Pullback FVG",
        direction=trade_direction,
        signal_time=signal_time,
        estimated_entry_price=estimated_entry_price,
        stop_price=float(stop_price),
        target_price=float(target_price),
        quantity=quantity,
        reason=reason,
    )
