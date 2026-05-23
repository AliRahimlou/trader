from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any

import pandas as pd


def build_live_calibration(trade_log: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trade_log:
        strategy_id = str(trade.get("strategy_id") or "unknown")
        grouped[strategy_id].append(trade)

    calibration: dict[str, dict[str, float]] = {}
    for strategy_id, trades in grouped.items():
        if not trades:
            continue
        wins = sum(1 for trade in trades if float(trade.get("net_pnl") or 0.0) > 0)
        losses = len(trades) - wins
        avg_r = mean(float(trade.get("risk_multiple") or 0.0) for trade in trades)
        avg_pnl = mean(float(trade.get("net_pnl") or 0.0) for trade in trades)
        win_rate = wins / len(trades)
        raw_score = 50.0 + ((win_rate - 0.5) * 80.0) + (avg_r * 10.0)
        confidence = min(len(trades) / 15.0, 1.0)
        calibration[strategy_id] = {
            "samples": float(len(trades)),
            "wins": float(wins),
            "losses": float(losses),
            "win_rate": round(win_rate, 4),
            "avg_r": round(avg_r, 4),
            "avg_pnl": round(avg_pnl, 4),
            "confidence": round(confidence, 4),
            "score": round(_blend(50.0, _clamp(raw_score), confidence), 2),
        }
    return calibration


def build_prefilter_stage(features: dict[str, Any]) -> dict[str, Any]:
    price = float(features.get("price") or 0.0)
    dollar_volume = float(features.get("dollar_volume") or 0.0)
    session_volume = float(features.get("session_volume") or 0.0)
    spread_bps = float(features.get("spread_bps") or 0.0)
    day_range_pct = abs(float(features.get("day_range_pct") or 0.0))
    intraday_return_pct = abs(float(features.get("intraday_return_pct") or 0.0))
    gap_pct = abs(float(features.get("gap_pct") or 0.0))

    components = {
        "price_quality": 100.0 if price > 0 else 0.0,
        "liquidity": _scale(math.log10(dollar_volume + 1.0), 5.1, 9.7),
        "spread": _inverse_scale(spread_bps, 2.0, 60.0),
        "participation": _scale(math.log10(session_volume + 1.0), 4.0, 8.2),
        "volatility": _ideal_band(day_range_pct, 0.45, 5.2, 0.08, 12.0),
        "gap_context": _ideal_band(gap_pct, 0.12, 3.8, 0.0, 8.0),
        "momentum": _ideal_band(intraday_return_pct, 0.2, 3.4, 0.0, 8.0),
        "freshness": 100.0 if features.get("data_fresh") else 0.0,
    }
    score = _weighted_average(
        components,
        {
            "price_quality": 0.5,
            "liquidity": 1.3,
            "spread": 1.1,
            "participation": 1.0,
            "volatility": 0.9,
            "gap_context": 0.7,
            "momentum": 0.7,
            "freshness": 0.9,
        },
    )
    return {
        "score": round(score, 2),
        "components": {name: round(value, 2) for name, value in components.items()},
        "summary": _top_component_summary(components),
    }


def build_context_stage(candidate_features: dict[str, Any], daily_df: pd.DataFrame) -> dict[str, Any]:
    if daily_df.empty:
        return {
            "score": 0.0,
            "components": {},
            "feature_updates": {},
            "notes": ["Missing daily bars for deep context stage."],
        }

    recent_returns = daily_df["close"].pct_change().dropna().tail(20)
    recent_ranges = ((daily_df["high"] - daily_df["low"]) / daily_df["close"]).dropna().tail(20)
    recent_volumes = daily_df["volume"].tail(20)
    short_range = float(recent_ranges.tail(5).mean() * 100.0) if len(recent_ranges) >= 5 else float(recent_ranges.mean() * 100.0)
    long_range = float(recent_ranges.mean() * 100.0) if len(recent_ranges) else 0.0
    compression_ratio = (short_range / long_range) if long_range > 0 else 1.0
    trend_efficiency = _trend_efficiency(daily_df["close"].tail(15))
    return_sign_changes = _sign_change_ratio(recent_returns)

    atr_pct = abs(float(candidate_features.get("atr_pct") or 0.0))
    relative_volume = float(candidate_features.get("relative_volume") or 0.0)
    momentum_5d_pct = float(candidate_features.get("momentum_5d_pct") or 0.0)
    trend_pct = float(candidate_features.get("trend_pct") or 0.0)
    daily_bias = str(candidate_features.get("daily_bias") or "neutral")
    intraday_return_pct = abs(float(candidate_features.get("intraday_return_pct") or 0.0))
    distance_prev_high = abs(float(candidate_features.get("distance_prev_day_high_pct") or 99.0))
    distance_prev_low = abs(float(candidate_features.get("distance_prev_day_low_pct") or 99.0))
    nearest_key_level_pct = min(distance_prev_high, distance_prev_low)

    components = {
        "regime_clarity": _weighted_average(
            {
                "bias": 85.0 if daily_bias != "neutral" else 40.0,
                "trend_strength": _scale(abs(trend_pct), 0.3, 10.0),
                "trend_efficiency": trend_efficiency,
            },
            {"bias": 0.8, "trend_strength": 1.0, "trend_efficiency": 1.0},
        ),
        "trend": _directional_trend_score(daily_bias, trend_pct, momentum_5d_pct),
        "relative_volume": _ideal_band(relative_volume, 0.9, 4.5, 0.15, 8.0),
        "volatility_quality": _ideal_band(atr_pct, 0.35, 4.8, 0.05, 10.0),
        "compression_context": _inverse_scale(abs(compression_ratio - 0.78), 0.0, 0.95),
        "level_proximity": _inverse_scale(nearest_key_level_pct, 0.15, max(atr_pct * 1.8, 2.5)),
        "noise_control": _inverse_scale(return_sign_changes, 0.18, 0.95),
        "overextension_control": _inverse_scale(max(abs(momentum_5d_pct) + intraday_return_pct - max(atr_pct * 1.8, 4.0), 0.0), 0.0, 8.0),
    }
    score = _weighted_average(
        components,
        {
            "regime_clarity": 1.0,
            "trend": 1.1,
            "relative_volume": 1.0,
            "volatility_quality": 0.9,
            "compression_context": 0.8,
            "level_proximity": 0.8,
            "noise_control": 1.0,
            "overextension_control": 1.0,
        },
    )
    notes: list[str] = []
    if daily_bias == "neutral":
        notes.append("Daily bias is neutral, so setup confirmation must do more work.")
    if compression_ratio < 0.9:
        notes.append("Recent daily range has compressed versus the 20-day average.")
    if abs(momentum_5d_pct) > max(atr_pct * 1.8, 5.0):
        notes.append("Recent move may already be extended relative to normal range.")

    return {
        "score": round(score, 2),
        "components": {name: round(value, 2) for name, value in components.items()},
        "feature_updates": {
            "compression_ratio": round(compression_ratio, 4),
            "trend_efficiency": round(trend_efficiency, 2),
            "return_sign_change_ratio": round(return_sign_changes, 4),
            "nearest_key_level_pct": round(nearest_key_level_pct, 4),
        },
        "notes": notes,
    }


def build_portfolio_fit_stage(
    symbol: str,
    *,
    current_positions: dict[str, dict[str, Any]],
    current_active_trades: dict[str, dict[str, Any]],
    correlation_to_open_positions: float | None,
    max_concurrent_positions: int,
    correlation_threshold: float,
) -> dict[str, Any]:
    active_trade_count = len(current_active_trades)
    has_position = symbol.upper() in current_positions or symbol.upper() in current_active_trades
    capacity_score = 100.0
    if max_concurrent_positions > 0:
        capacity_score = _inverse_scale(max(active_trade_count - max_concurrent_positions + 1, 0), 0.0, max_concurrent_positions or 1)
        if not has_position and active_trade_count < max_concurrent_positions:
            capacity_score = 100.0
    duplication_score = 0.0 if has_position else 100.0
    correlation_score = 100.0
    if correlation_to_open_positions is not None and correlation_threshold > 0:
        correlation_score = _inverse_scale(
            max(correlation_to_open_positions - (correlation_threshold * 0.6), 0.0),
            0.0,
            max(1.0 - (correlation_threshold * 0.6), 0.01),
        )

    components = {
        "capacity": round(capacity_score, 2),
        "duplication": round(duplication_score, 2),
        "correlation": round(correlation_score, 2),
    }
    score = _weighted_average(components, {"capacity": 1.0, "duplication": 1.0, "correlation": 1.1})
    reasons: list[str] = []
    if has_position:
        reasons.append("symbol_already_held")
    if correlation_to_open_positions is not None and correlation_threshold > 0 and correlation_to_open_positions >= correlation_threshold:
        reasons.append("correlated_with_open_positions")
    if max_concurrent_positions > 0 and active_trade_count >= max_concurrent_positions and not has_position:
        reasons.append("portfolio_at_capacity")
    return {
        "score": round(score, 2),
        "components": components,
        "reasons": reasons,
    }


def score_signal_opportunity(
    signal: dict[str, Any],
    *,
    minute_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    candidate_features: dict[str, Any],
    calibration: dict[str, dict[str, float]],
) -> dict[str, Any]:
    metadata = dict(signal.get("metadata") or {})
    strategy_id = str(signal.get("strategy_id") or "unknown")
    direction = str(signal.get("direction") or "long")
    entry_price = float(signal.get("entry_reference_price") or 0.0)
    stop_price = float(signal.get("stop_price") or 0.0)
    target_price = float(signal.get("target_price") or 0.0)
    stop_distance_pct = (abs(entry_price - stop_price) / entry_price * 100.0) if entry_price > 0 else 0.0
    target_distance_pct = (abs(target_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
    rr_ratio = (target_distance_pct / stop_distance_pct) if stop_distance_pct > 0 else 0.0
    atr_pct = abs(float(candidate_features.get("atr_pct") or 0.0))
    trend_pct = float(candidate_features.get("trend_pct") or 0.0)
    momentum_5d_pct = float(candidate_features.get("momentum_5d_pct") or 0.0)
    intraday_return_pct = float(candidate_features.get("intraday_return_pct") or 0.0)
    relative_volume = float(candidate_features.get("relative_volume") or 0.0)
    daily_bias = str(candidate_features.get("daily_bias") or "neutral")
    spread_bps = float(candidate_features.get("spread_bps") or 0.0)
    recent_efficiency = _trend_efficiency(minute_df["close"].tail(20)) if not minute_df.empty else 0.0
    signal_age_bars = _signal_age_bars(signal.get("signal_time"), minute_df.index)
    calibration_profile = calibration.get(strategy_id, {})
    calibration_score = float(calibration_profile.get("score") or 50.0)
    calibration_confidence = float(calibration_profile.get("confidence") or 0.0)

    alignment_score = _signal_alignment_score(direction, daily_bias, trend_pct, momentum_5d_pct)
    execution_cleanliness = _weighted_average(
        {
            "spread": _inverse_scale(spread_bps, 2.0, 60.0),
            "risk_reward": _ideal_band(rr_ratio, 1.5, 3.8, 0.75, 6.0),
            "stop_efficiency": _ideal_band(stop_distance_pct, max(atr_pct * 0.25, 0.12), max(atr_pct * 0.95, 1.4), 0.04, max(atr_pct * 2.2, 3.0)),
            "actionability": _actionability_score(metadata, signal_age_bars),
        },
        {
            "spread": 1.0,
            "risk_reward": 1.0,
            "stop_efficiency": 0.9,
            "actionability": 1.0,
        },
    )
    structure_clarity = _structure_score(strategy_id, metadata, entry_price, atr_pct)
    displacement_quality = _displacement_score(metadata, atr_pct)
    freshness = _inverse_scale(signal_age_bars, 0.0, 6.0)
    noise_control = _noise_control_score(direction, intraday_return_pct, recent_efficiency)
    overextension_control = _overextension_control(direction, intraday_return_pct, momentum_5d_pct, atr_pct)
    opportunity_window = _opportunity_window_score(metadata)

    quality_components = {
        "structure": round(structure_clarity, 2),
        "displacement": round(displacement_quality, 2),
        "alignment": round(alignment_score, 2),
        "execution": round(execution_cleanliness, 2),
        "freshness": round(freshness, 2),
        "noise_control": round(noise_control, 2),
        "opportunity_window": round(opportunity_window, 2),
        "risk_reward": round(_ideal_band(rr_ratio, 1.5, 3.8, 0.75, 6.0), 2),
        "overextension_control": round(overextension_control, 2),
    }
    quality_score = _weighted_average(
        quality_components,
        {
            "structure": 1.2,
            "displacement": 1.0,
            "alignment": 1.2,
            "execution": 1.0,
            "freshness": 0.8,
            "noise_control": 0.9,
            "opportunity_window": 0.8,
            "risk_reward": 1.0,
            "overextension_control": 1.0,
        },
    )

    expectancy_components = {
        "quality": quality_score,
        "calibration": _blend(50.0, calibration_score, calibration_confidence),
        "participation": _ideal_band(relative_volume, 1.0, 4.5, 0.25, 8.0),
        "execution": execution_cleanliness,
        "overextension_control": overextension_control,
    }
    expectancy_score = _weighted_average(
        expectancy_components,
        {
            "quality": 1.4,
            "calibration": 0.8,
            "participation": 0.9,
            "execution": 1.0,
            "overextension_control": 0.9,
        },
    )

    return {
        "quality_score": round(quality_score, 2),
        "expectancy_score": round(expectancy_score, 2),
        "execution_score": round(execution_cleanliness, 2),
        "quality_components": quality_components,
        "expectancy_components": {name: round(value, 2) for name, value in expectancy_components.items()},
        "calibration": {
            "score": round(calibration_score, 2),
            "confidence": round(calibration_confidence, 2),
            "samples": int(calibration_profile.get("samples") or 0),
            "win_rate": round(float(calibration_profile.get("win_rate") or 0.0), 4),
            "avg_r": round(float(calibration_profile.get("avg_r") or 0.0), 4),
        },
        "summary": _signal_summary(strategy_id, quality_components, expectancy_score),
    }


def explain_ranked_candidates(candidates: list[Any]) -> None:
    for index, candidate in enumerate(candidates):
        next_candidates = candidates[index + 1 : index + 4]
        candidate.notes = list(candidate.notes)
        if not next_candidates:
            candidate.notes.append("Top of the current ranked board.")
            candidate.rank_reason = candidate.rank_reason if hasattr(candidate, "rank_reason") else None
            continue
        comparison_reason = build_relative_reason(candidate, next_candidates)
        if comparison_reason:
            candidate.notes.append(comparison_reason)
            setattr(candidate, "relative_ranking_reason", comparison_reason)


def build_relative_reason(candidate: Any, next_candidates: list[Any]) -> str:
    current_components = dict(getattr(candidate, "score_components", {}) or {})
    if not current_components:
        return ""
    comparisons: list[str] = []
    for rival in next_candidates[:2]:
        rival_components = dict(getattr(rival, "score_components", {}) or {})
        if not rival_components:
            continue
        deltas = sorted(
            (
                (name, float(current_components.get(name, 0.0)) - float(rival_components.get(name, 0.0)))
                for name in set(current_components) | set(rival_components)
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        winners = [name for name, delta in deltas if delta > 4.0][:3]
        if winners:
            comparisons.append(
                f"beat {getattr(rival, 'symbol', 'peer')} on {', '.join(winners)}"
            )
    return "; ".join(comparisons)


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    bounded = max(low, min(high, value))
    return ((bounded - low) / (high - low)) * 100.0


def _inverse_scale(value: float, low: float, high: float) -> float:
    return 100.0 - _scale(value, low, high)


def _ideal_band(value: float, ideal_low: float, ideal_high: float, outer_low: float, outer_high: float) -> float:
    if outer_high <= outer_low:
        return 0.0
    if ideal_low <= value <= ideal_high:
        return 100.0
    if value < ideal_low:
        return _scale(value, outer_low, ideal_low)
    return _inverse_scale(value, ideal_high, outer_high)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _blend(base: float, adjusted: float, confidence: float) -> float:
    confidence = max(0.0, min(1.0, confidence))
    return base + ((adjusted - base) * confidence)


def _weighted_average(values: dict[str, float], weights: dict[str, float] | None = None) -> float:
    if not values:
        return 0.0
    weights = weights or {name: 1.0 for name in values}
    total_weight = 0.0
    weighted_total = 0.0
    for name, value in values.items():
        weight = float(weights.get(name, 1.0))
        total_weight += weight
        weighted_total += float(value) * weight
    if total_weight <= 0:
        return 0.0
    return _clamp(weighted_total / total_weight)


def _top_component_summary(components: dict[str, float]) -> str:
    top = sorted(components.items(), key=lambda item: item[1], reverse=True)[:3]
    return ", ".join(name for name, _ in top)


def _trend_efficiency(series: pd.Series) -> float:
    if series.empty or len(series) < 3:
        return 0.0
    total_distance = float(series.diff().abs().sum())
    if total_distance <= 0:
        return 0.0
    net_distance = abs(float(series.iloc[-1] - series.iloc[0]))
    return _clamp((net_distance / total_distance) * 100.0)


def _sign_change_ratio(series: pd.Series) -> float:
    if series.empty or len(series) < 3:
        return 1.0
    signs = [1 if value > 0 else -1 if value < 0 else 0 for value in series]
    changes = 0
    transitions = 0
    for left, right in zip(signs, signs[1:]):
        if left == 0 or right == 0:
            continue
        transitions += 1
        if left != right:
            changes += 1
    if transitions <= 0:
        return 0.0
    return changes / transitions


def _directional_trend_score(daily_bias: str, trend_pct: float, momentum_5d_pct: float) -> float:
    direction_score = 45.0
    if daily_bias == "long":
        direction_score = 70.0 if trend_pct > 0 and momentum_5d_pct > -2.0 else 45.0
    elif daily_bias == "short":
        direction_score = 70.0 if trend_pct < 0 and momentum_5d_pct < 2.0 else 45.0
    strength_score = _scale(abs(trend_pct) + abs(momentum_5d_pct), 0.5, 14.0)
    return _weighted_average({"direction": direction_score, "strength": strength_score}, {"direction": 1.2, "strength": 1.0})


def _signal_alignment_score(direction: str, daily_bias: str, trend_pct: float, momentum_5d_pct: float) -> float:
    bias_score = 45.0
    if daily_bias == direction:
        bias_score = 100.0
    elif daily_bias == "neutral":
        bias_score = 55.0
    trend_score = 100.0 if (direction == "long" and trend_pct >= 0) or (direction == "short" and trend_pct <= 0) else 30.0
    momentum_score = 100.0 if (direction == "long" and momentum_5d_pct >= -1.5) or (direction == "short" and momentum_5d_pct <= 1.5) else 35.0
    return _weighted_average(
        {"bias": bias_score, "trend": trend_score, "momentum": momentum_score},
        {"bias": 1.2, "trend": 1.0, "momentum": 0.8},
    )


def _structure_score(strategy_id: str, metadata: dict[str, Any], entry_price: float, atr_pct: float) -> float:
    gap_atr_ratio = float(metadata.get("gap_atr_ratio") or 0.0)
    gap_pct = abs(float(metadata.get("gap_pct") or 0.0))
    signal_range_pct = abs(float(metadata.get("signal_bar_range_pct") or 0.0))
    if strategy_id == "break":
        return _weighted_average(
            {
                "gap_quality": _ideal_band(gap_atr_ratio, 0.25, 1.5, 0.0, 2.8),
                "breakout_clearance": _ideal_band(abs(float(metadata.get("breakout_clearance_pct") or 0.0)), 0.08, max(atr_pct * 0.5, 0.9), 0.0, max(atr_pct * 1.8, 3.0)),
                "opening_range_context": _ideal_band(abs(float(metadata.get("opening_range_width_pct") or 0.0)), max(atr_pct * 0.25, 0.2), max(atr_pct * 1.2, 1.8), 0.05, max(atr_pct * 2.4, 4.0)),
                "signal_range": _ideal_band(signal_range_pct, max(atr_pct * 0.35, 0.18), max(atr_pct * 1.4, 1.5), 0.05, max(atr_pct * 2.7, 4.0)),
            },
            {"gap_quality": 1.1, "breakout_clearance": 1.1, "opening_range_context": 0.9, "signal_range": 0.9},
        )
    return _weighted_average(
        {
            "gap_quality": _ideal_band(gap_atr_ratio, 0.18, 1.1, 0.0, 2.2),
            "sweep_depth": _ideal_band(abs(float(metadata.get("sweep_depth_pct") or 0.0)), max(atr_pct * 0.15, 0.08), max(atr_pct * 1.0, 1.2), 0.0, max(atr_pct * 2.0, 3.2)),
            "retrace_quality": _ideal_band(abs(float(metadata.get("retracement_fill_pct") or 0.0)), 20.0, 80.0, 0.0, 100.0),
            "midpoint_hold": _ideal_band(abs(float(metadata.get("midpoint_hold_pct") or 0.0)), 0.0, max(atr_pct * 0.45, 0.25), 0.0, max(atr_pct * 1.5, 2.0)),
        },
        {"gap_quality": 1.0, "sweep_depth": 1.1, "retrace_quality": 1.0, "midpoint_hold": 1.0},
    )


def _displacement_score(metadata: dict[str, Any], atr_pct: float) -> float:
    return _weighted_average(
        {
            "body": _scale(float(metadata.get("signal_bar_body_ratio") or 0.0), 0.15, 0.9),
            "close_location": _scale(float(metadata.get("signal_close_location") or 0.0), 0.35, 1.0),
            "range": _ideal_band(abs(float(metadata.get("signal_bar_range_pct") or 0.0)), max(atr_pct * 0.25, 0.12), max(atr_pct * 1.4, 1.5), 0.03, max(atr_pct * 2.8, 4.2)),
            "middle_body": _scale(float(metadata.get("middle_bar_body_ratio") or metadata.get("signal_bar_body_ratio") or 0.0), 0.15, 0.9),
        },
        {"body": 1.0, "close_location": 1.0, "range": 0.9, "middle_body": 0.8},
    )


def _actionability_score(metadata: dict[str, Any], signal_age_bars: int) -> float:
    return _weighted_average(
        {
            "signal_age": _inverse_scale(signal_age_bars, 0.0, 6.0),
            "session_timing": _ideal_band(float(metadata.get("bars_from_open") or 0.0), 5.0, 180.0, 0.0, 320.0),
        },
        {"signal_age": 1.2, "session_timing": 0.8},
    )


def _noise_control_score(direction: str, intraday_return_pct: float, trend_efficiency: float) -> float:
    directional_score = 100.0
    if direction == "long" and intraday_return_pct < -0.6:
        directional_score = 35.0
    if direction == "short" and intraday_return_pct > 0.6:
        directional_score = 35.0
    return _weighted_average(
        {"trend_efficiency": trend_efficiency, "directional": directional_score},
        {"trend_efficiency": 1.1, "directional": 0.9},
    )


def _overextension_control(direction: str, intraday_return_pct: float, momentum_5d_pct: float, atr_pct: float) -> float:
    move_pressure = abs(intraday_return_pct) + max(abs(momentum_5d_pct) - atr_pct, 0.0)
    return _inverse_scale(move_pressure, max(atr_pct * 0.8, 1.2), max(atr_pct * 3.2, 8.0))


def _opportunity_window_score(metadata: dict[str, Any]) -> float:
    if "breakout_clearance_pct" in metadata:
        return _ideal_band(abs(float(metadata.get("breakout_clearance_pct") or 0.0)), 0.08, 0.9, 0.0, 2.8)
    return _ideal_band(abs(float(metadata.get("midpoint_hold_pct") or 0.0)), 0.0, 0.6, 0.0, 2.0)


def _signal_summary(strategy_id: str, quality_components: dict[str, float], expectancy_score: float) -> str:
    top_drivers = [name for name, _ in sorted(quality_components.items(), key=lambda item: item[1], reverse=True)[:3]]
    if not top_drivers:
        return f"{strategy_id} signal"
    return f"{strategy_id} signal led by {', '.join(top_drivers)} with expectancy proxy {expectancy_score:.1f}"


def _signal_age_bars(signal_time: Any, index: pd.Index) -> int:
    if signal_time is None or index.empty:
        return 99
    try:
        signal_ts = pd.Timestamp(signal_time)
    except Exception:
        return 99
    if signal_ts.tzinfo is None and getattr(index, "tz", None) is not None:
        signal_ts = signal_ts.tz_localize(index.tz)
    elif signal_ts.tzinfo is not None and getattr(index, "tz", None) is not None:
        signal_ts = signal_ts.tz_convert(index.tz)
    positions = [i for i, value in enumerate(index) if value >= signal_ts]
    if not positions:
        return len(index)
    return max(len(index) - positions[0] - 1, 0)
