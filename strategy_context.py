from __future__ import annotations

import pandas as pd


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
