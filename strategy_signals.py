from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

from backtest_utils import (
    BacktestConfig,
    calculate_position_size,
    get_fair_value_gap_bounds,
    get_fair_value_gap_direction,
)
from strategy_context import get_daily_bias, get_previous_day_levels


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_name: str
    direction: str
    signal_time: pd.Timestamp
    entry_reference_price: float
    stop_price: float
    target_price: float
    quantity: float
    reason: str

    @property
    def signal_key(self) -> str:
        return f"{self.strategy_id}:{self.direction}:{self.signal_time.isoformat()}"


@dataclass(frozen=True)
class SetupCandidate:
    strategy_id: str
    strategy_name: str
    direction: str
    signal_time: pd.Timestamp
    stop_price: float
    target_price: float | None
    suggested_entry_price: float
    reason: str


def get_opening_range_bar(
    five_min_df: pd.DataFrame,
    session_date,
) -> pd.Series | None:
    opening_range = five_min_df[
        (five_min_df.index.date == session_date) & (five_min_df.index.time == time(9, 30))
    ]
    if opening_range.empty:
        return None
    return opening_range.iloc[0]


def detect_break_setup(
    session_1m_df: pd.DataFrame,
    opening_range_bar: pd.Series | None,
    signal_idx: int,
    *,
    config: BacktestConfig,
) -> SetupCandidate | None:
    if opening_range_bar is None or signal_idx < 2 or signal_idx >= len(session_1m_df):
        return None

    bar = session_1m_df.iloc[signal_idx]
    signal_time = session_1m_df.index[signal_idx]
    direction = get_fair_value_gap_direction(
        session_1m_df,
        signal_idx,
        min_gap_pct=config.min_gap_pct,
        min_gap_atr=config.min_gap_atr,
        require_displacement=config.require_displacement,
    )
    if direction is None:
        return None

    suggested_entry_price = float(bar["close"])
    or_high = float(opening_range_bar["high"])
    or_low = float(opening_range_bar["low"])

    if direction == "bullish" and bar["close"] > or_high:
        return SetupCandidate(
            strategy_id="break",
            strategy_name="Opening Range + FVG",
            direction="long",
            signal_time=signal_time,
            stop_price=or_low,
            target_price=None,
            suggested_entry_price=suggested_entry_price,
            reason="Bullish FVG closed above opening-range high",
        )
    if direction == "bearish" and bar["close"] < or_low:
        return SetupCandidate(
            strategy_id="break",
            strategy_name="Opening Range + FVG",
            direction="short",
            signal_time=signal_time,
            stop_price=or_high,
            target_price=None,
            suggested_entry_price=suggested_entry_price,
            reason="Bearish FVG closed below opening-range low",
        )
    return None


def detect_pullback_setup(
    session_1m_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    fvg_idx: int,
    *,
    config: BacktestConfig,
) -> SetupCandidate | None:
    if fvg_idx < 2 or fvg_idx + 1 >= len(session_1m_df):
        return None

    session_date = session_1m_df.index[-1].date()
    bias = get_daily_bias(daily_df, session_date)
    if bias == "neutral":
        return None

    prev_day_high, prev_day_low = get_previous_day_levels(daily_df, session_date)
    if prev_day_high is None or prev_day_low is None:
        return None

    bar = session_1m_df.iloc[fvg_idx]
    next_bar = session_1m_df.iloc[fvg_idx + 1]
    signal_time = session_1m_df.index[fvg_idx + 1]
    fvg_direction = get_fair_value_gap_direction(
        session_1m_df,
        fvg_idx,
        min_gap_pct=config.min_gap_pct,
        min_gap_atr=config.min_gap_atr,
        require_displacement=config.require_displacement,
    )
    if fvg_direction is None:
        return None

    swept_prev_low = bool((session_1m_df.iloc[: fvg_idx + 1]["low"] < prev_day_low).any())
    swept_prev_high = bool((session_1m_df.iloc[: fvg_idx + 1]["high"] > prev_day_high).any())
    gap_low, gap_high = get_fair_value_gap_bounds(session_1m_df, fvg_idx, fvg_direction)
    gap_midpoint = (gap_low + gap_high) / 2

    if (
        bias == "long"
        and swept_prev_low
        and fvg_direction == "bullish"
        and bar["close"] > prev_day_low
        and next_bar["low"] <= gap_high
        and next_bar["close"] >= gap_midpoint
    ):
        return SetupCandidate(
            strategy_id="pullback",
            strategy_name="Daily Sweep + Pullback FVG",
            direction="long",
            signal_time=signal_time,
            stop_price=min(float(session_1m_df.iloc[: fvg_idx + 1]["low"].min()), gap_low),
            target_price=float(prev_day_high),
            suggested_entry_price=max(float(next_bar["open"]), gap_midpoint),
            reason="Daily low sweep with bullish pullback FVG",
        )

    if (
        bias == "short"
        and swept_prev_high
        and fvg_direction == "bearish"
        and bar["close"] < prev_day_high
        and next_bar["high"] >= gap_low
        and next_bar["close"] <= gap_midpoint
    ):
        return SetupCandidate(
            strategy_id="pullback",
            strategy_name="Daily Sweep + Pullback FVG",
            direction="short",
            signal_time=signal_time,
            stop_price=max(float(session_1m_df.iloc[: fvg_idx + 1]["high"].max()), gap_high),
            target_price=float(prev_day_low),
            suggested_entry_price=min(float(next_bar["open"]), gap_midpoint),
            reason="Daily high sweep with bearish pullback FVG",
        )

    return None


def materialize_signal(
    setup: SetupCandidate | None,
    entry_price: float | None,
    *,
    config: BacktestConfig,
) -> StrategySignal | None:
    if setup is None:
        return None

    reference_entry_price = float(entry_price) if entry_price is not None else float(setup.suggested_entry_price)
    if setup.direction == "long" and reference_entry_price <= setup.stop_price:
        return None
    if setup.direction == "short" and reference_entry_price >= setup.stop_price:
        return None

    if setup.target_price is None:
        if setup.direction == "long":
            target_price = reference_entry_price + (reference_entry_price - setup.stop_price) * config.rr_ratio
        else:
            target_price = reference_entry_price - (setup.stop_price - reference_entry_price) * config.rr_ratio
    else:
        target_price = float(setup.target_price)

    if setup.direction == "long" and target_price <= reference_entry_price:
        return None
    if setup.direction == "short" and target_price >= reference_entry_price:
        return None

    quantity = float(calculate_position_size(reference_entry_price, setup.stop_price, config))
    if quantity < 1:
        return None

    return StrategySignal(
        strategy_id=setup.strategy_id,
        strategy_name=setup.strategy_name,
        direction=setup.direction,
        signal_time=setup.signal_time,
        entry_reference_price=reference_entry_price,
        stop_price=float(setup.stop_price),
        target_price=target_price,
        quantity=quantity,
        reason=setup.reason,
    )
