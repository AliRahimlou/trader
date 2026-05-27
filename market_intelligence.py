from __future__ import annotations

from statistics import mean, median
from typing import Any


def build_strategy_edge_profiles(trade_log: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in trade_log[-120:]:
        strategy_id = str(trade.get("strategy_id") or "unknown")
        grouped.setdefault(strategy_id, []).append(trade)

    profiles: dict[str, dict[str, Any]] = {}
    for strategy_id, trades in grouped.items():
        if not trades:
            continue
        wins = sum(1 for trade in trades if _float(trade.get("net_pnl")) > 0)
        returns = [_float(trade.get("risk_multiple")) for trade in trades]
        avg_r = mean(returns) if returns else 0.0
        win_rate = wins / len(trades)
        confidence = min(len(trades) / 20.0, 1.0)
        raw_score = 50.0 + ((win_rate - 0.5) * 70.0) + (avg_r * 14.0)
        score = _blend(50.0, _clamp(raw_score), confidence)
        raw_weight = 1.0 + ((score - 50.0) / 40.0)
        profiles[strategy_id] = {
            "strategy_id": strategy_id,
            "samples": len(trades),
            "wins": wins,
            "losses": len(trades) - wins,
            "win_rate": round(win_rate, 4),
            "avg_r": round(avg_r, 4),
            "confidence": round(confidence, 4),
            "score": round(score, 2),
            "weight": round(_clamp(raw_weight, 0.3, 2.5), 3),
        }
    return profiles


def assess_market_regime(candidates: list[Any]) -> dict[str, Any]:
    usable = [
        candidate
        for candidate in candidates
        if not getattr(candidate, "exclusion_reasons", None)
        and _float(getattr(candidate, "features", {}).get("price")) > 0
    ]
    if not usable:
        return {
            "label": "unknown",
            "directional_bias": "neutral",
            "risk_level": "unknown",
            "score": 50.0,
            "risk_multiplier": 0.75,
            "evidence": ["No eligible scanner candidates have enough context yet."],
            "stats": {},
        }

    trends = [_float(candidate.features.get("trend_pct")) for candidate in usable]
    momentums = [_float(candidate.features.get("momentum_5d_pct")) for candidate in usable]
    atr_values = [abs(_float(candidate.features.get("atr_pct"))) for candidate in usable if _float(candidate.features.get("atr_pct")) > 0]
    relative_volumes = [_float(candidate.features.get("relative_volume")) for candidate in usable]
    spread_scores = [
        _inverse_scale(_float(candidate.features.get("spread_bps")), 2.0, 60.0)
        for candidate in usable
    ]
    biases = [str(candidate.features.get("daily_bias") or "neutral") for candidate in usable]

    long_share = biases.count("long") / len(usable)
    short_share = biases.count("short") / len(usable)
    neutral_share = biases.count("neutral") / len(usable)
    avg_trend = mean(trends) if trends else 0.0
    avg_momentum = mean(momentums) if momentums else 0.0
    median_atr = median(atr_values) if atr_values else 0.0
    avg_relative_volume = mean(relative_volumes) if relative_volumes else 0.0
    avg_spread_quality = mean(spread_scores) if spread_scores else 50.0

    breadth_score = _clamp(50.0 + ((long_share - short_share) * 65.0))
    momentum_score = _clamp(50.0 + (avg_momentum * 7.0))
    trend_score = _clamp(50.0 + (avg_trend * 5.5))
    volatility_control = _inverse_scale(median_atr, 1.4, 7.5)
    participation_score = _ideal_band(avg_relative_volume, 0.8, 2.8, 0.15, 6.0)

    score = _weighted_average(
        {
            "breadth": breadth_score,
            "momentum": momentum_score,
            "trend": trend_score,
            "volatility_control": volatility_control,
            "participation": participation_score,
            "spread_quality": avg_spread_quality,
        },
        {
            "breadth": 1.3,
            "momentum": 1.0,
            "trend": 1.0,
            "volatility_control": 0.9,
            "participation": 0.8,
            "spread_quality": 0.8,
        },
    )

    if long_share >= 0.55 and avg_momentum >= -0.25 and avg_trend >= -0.25:
        label = "risk_on"
        directional_bias = "long"
    elif short_share >= 0.45 or (avg_momentum <= -0.75 and avg_trend <= -0.75):
        label = "risk_off"
        directional_bias = "short"
    elif neutral_share >= 0.45 or abs(long_share - short_share) < 0.18:
        label = "mixed"
        directional_bias = "neutral"
    else:
        label = "transitional"
        directional_bias = "long" if long_share > short_share else "short"

    if median_atr >= 6.0 or score < 42.0:
        risk_level = "high"
        risk_multiplier = 0.7
    elif label in {"mixed", "transitional"} or median_atr >= 4.0:
        risk_level = "moderate"
        risk_multiplier = 0.9
    else:
        risk_level = "normal"
        risk_multiplier = 1.0

    evidence = [
        f"{long_share:.0%} long bias and {short_share:.0%} short bias across eligible candidates.",
        f"Average 5-day momentum is {avg_momentum:.2f}% with median ATR {median_atr:.2f}%.",
    ]
    if avg_spread_quality < 55.0:
        evidence.append("Average spread quality is weak, so execution selectivity should be higher.")
    if label == "risk_off":
        evidence.append("Short-side or defensive setups should outrank marginal longs.")
    elif label == "risk_on":
        evidence.append("Long-side momentum setups have the cleaner broad-market backdrop.")

    return {
        "label": label,
        "directional_bias": directional_bias,
        "risk_level": risk_level,
        "score": round(score, 2),
        "risk_multiplier": round(risk_multiplier, 2),
        "evidence": evidence,
        "stats": {
            "eligible_count": len(usable),
            "long_bias_share": round(long_share, 4),
            "short_bias_share": round(short_share, 4),
            "neutral_bias_share": round(neutral_share, 4),
            "avg_trend_pct": round(avg_trend, 4),
            "avg_momentum_5d_pct": round(avg_momentum, 4),
            "median_atr_pct": round(median_atr, 4),
            "avg_relative_volume": round(avg_relative_volume, 4),
            "avg_spread_quality": round(avg_spread_quality, 2),
        },
    }


def build_candidate_review(
    candidate: Any,
    *,
    market_regime: dict[str, Any],
    strategy_edges: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    features = dict(getattr(candidate, "features", {}) or {})
    stage_scores = dict(getattr(candidate, "stage_scores", {}) or {})
    stage_components = dict(getattr(candidate, "stage_components", {}) or {})
    best_signal = dict(getattr(candidate, "best_signal", {}) or {})
    strategy_id = str(best_signal.get("strategy_id") or "")
    direction = str(best_signal.get("direction") or features.get("daily_bias") or "neutral")
    strategy_profile = dict(strategy_edges.get(strategy_id) or {})

    regime_fit = _regime_fit_score(direction, features, market_regime)
    relative_strength = _relative_strength_score(candidate)
    signal_quality = _weighted_average(
        {
            "setup": _float(stage_scores.get("setup_quality")),
            "expectancy": _float(stage_scores.get("expectancy")),
            "execution": _float(stage_scores.get("execution")),
        },
        {"setup": 1.1, "expectancy": 1.2, "execution": 1.0},
    )
    if not best_signal:
        signal_quality = min(signal_quality, 35.0)
    strategy_edge = _blend(
        50.0,
        _float(strategy_profile.get("score"), 50.0),
        _float(strategy_profile.get("confidence")),
    )
    cro_review = _risk_review_score(candidate, market_regime=market_regime, strategy_edge=strategy_edge)
    components = {
        "regime_fit": round(regime_fit, 2),
        "relative_strength": round(relative_strength, 2),
        "signal_quality": round(signal_quality, 2),
        "strategy_edge": round(strategy_edge, 2),
        "cro_review": round(cro_review["score"], 2),
    }
    score = _weighted_average(
        components,
        {
            "regime_fit": 1.0,
            "relative_strength": 0.9,
            "signal_quality": 1.3,
            "strategy_edge": 0.8,
            "cro_review": 1.3,
        },
    )

    hard_flags = set(cro_review["hard_flags"])
    if not best_signal:
        decision = "watch"
    elif hard_flags or score < 42.0:
        decision = "block"
    elif score < 58.0 or cro_review["risk_flags"]:
        decision = "caution"
    else:
        decision = "approve"

    summary = _candidate_summary(candidate, market_regime, components, cro_review["risk_flags"], decision)
    return {
        "score": round(score, 2),
        "decision": decision,
        "components": components,
        "risk_flags": cro_review["risk_flags"],
        "hard_flags": list(hard_flags),
        "strategy_profile": strategy_profile,
        "summary": summary,
    }


def _regime_fit_score(direction: str, features: dict[str, Any], market_regime: dict[str, Any]) -> float:
    daily_bias = str(features.get("daily_bias") or "neutral")
    directional_bias = str(market_regime.get("directional_bias") or "neutral")
    label = str(market_regime.get("label") or "mixed")
    score = 62.0
    if direction in {"long", "short"} and directional_bias == direction:
        score += 24.0
    elif direction in {"long", "short"} and directional_bias in {"long", "short"} and directional_bias != direction:
        score -= 28.0
    if daily_bias == direction:
        score += 12.0
    elif daily_bias in {"long", "short"} and direction in {"long", "short"} and daily_bias != direction:
        score -= 18.0
    if label in {"mixed", "transitional"}:
        score -= 7.0
    if str(market_regime.get("risk_level")) == "high":
        score -= 10.0
    return _clamp(score)


def _relative_strength_score(candidate: Any) -> float:
    features = dict(getattr(candidate, "features", {}) or {})
    components = {
        "trend": _clamp(50.0 + (_float(features.get("trend_pct")) * 5.0)),
        "momentum": _clamp(50.0 + (_float(features.get("momentum_5d_pct")) * 6.0)),
        "participation": _ideal_band(_float(features.get("relative_volume")), 0.9, 3.5, 0.2, 7.0),
        "intraday": _clamp(50.0 + (_float(features.get("intraday_return_pct")) * 9.0)),
    }
    return _weighted_average(
        components,
        {"trend": 1.0, "momentum": 1.0, "participation": 1.0, "intraday": 0.7},
    )


def _risk_review_score(candidate: Any, *, market_regime: dict[str, Any], strategy_edge: float) -> dict[str, Any]:
    features = dict(getattr(candidate, "features", {}) or {})
    stage_scores = dict(getattr(candidate, "stage_scores", {}) or {})
    stage_components = dict(getattr(candidate, "stage_components", {}) or {})
    best_signal = dict(getattr(candidate, "best_signal", {}) or {})
    risk_flags: list[str] = []
    hard_flags: list[str] = []

    context = _float(stage_scores.get("context"))
    expectancy = _float(stage_scores.get("expectancy"))
    portfolio_fit = _float(stage_scores.get("portfolio_fit"), 100.0)
    execution = _float(stage_scores.get("execution"))
    spread_bps = _float(features.get("spread_bps"))
    atr_pct = abs(_float(features.get("atr_pct")))
    momentum_5d = abs(_float(features.get("momentum_5d_pct")))
    intraday_move = abs(_float(features.get("intraday_return_pct")))
    regime_fit = _regime_fit_score(str(best_signal.get("direction") or features.get("daily_bias") or "neutral"), features, market_regime)

    if best_signal and regime_fit < 42.0:
        risk_flags.append("regime_mismatch")
    if context and context < 45.0:
        risk_flags.append("weak_context")
    if best_signal and expectancy < 45.0:
        risk_flags.append("low_expectancy")
    if best_signal and execution < 45.0:
        risk_flags.append("execution_risk")
    if portfolio_fit < 58.0:
        risk_flags.append("portfolio_fit_risk")
    if strategy_edge < 42.0:
        risk_flags.append("low_strategy_edge")
    if spread_bps >= 45.0:
        risk_flags.append("wide_spread")
    if atr_pct > 0 and momentum_5d + intraday_move > max(atr_pct * 3.2, 8.0):
        risk_flags.append("overextended_move")
    if str(market_regime.get("risk_level")) == "high" and best_signal and regime_fit < 55.0:
        risk_flags.append("high_regime_risk")

    setup_components = dict(stage_components.get("setup_quality") or {})
    if _float(setup_components.get("noise_control"), 100.0) < 35.0:
        risk_flags.append("noisy_signal")
    if _float(setup_components.get("risk_reward"), 100.0) < 35.0:
        risk_flags.append("poor_risk_reward")

    if "wide_spread" in risk_flags and execution < 50.0:
        hard_flags.append("wide_spread")
    if {"regime_mismatch", "weak_context"}.issubset(risk_flags):
        hard_flags.append("regime_mismatch")
    if {"low_expectancy", "low_strategy_edge"}.issubset(risk_flags):
        hard_flags.append("low_expectancy")

    penalty = min(55.0, len(risk_flags) * 9.0 + len(hard_flags) * 16.0)
    base_score = _weighted_average(
        {
            "context": context or 50.0,
            "expectancy": expectancy or 50.0,
            "execution": execution or 50.0,
            "portfolio_fit": portfolio_fit,
            "spread": _inverse_scale(spread_bps, 2.0, 60.0),
        },
        {"context": 1.0, "expectancy": 1.1, "execution": 1.0, "portfolio_fit": 1.1, "spread": 0.8},
    )
    return {
        "score": round(_clamp(base_score - penalty), 2),
        "risk_flags": _dedupe(risk_flags),
        "hard_flags": _dedupe(hard_flags),
    }


def _candidate_summary(
    candidate: Any,
    market_regime: dict[str, Any],
    components: dict[str, float],
    risk_flags: list[str],
    decision: str,
) -> str:
    symbol = str(getattr(candidate, "symbol", "Candidate"))
    top_components = sorted(components.items(), key=lambda item: item[1], reverse=True)[:2]
    leaders = ", ".join(name.replace("_", " ") for name, _ in top_components) or "balanced inputs"
    regime = str(market_regime.get("label") or "mixed").replace("_", " ")
    if decision == "block":
        return f"{symbol} fails the ATLAS risk review in a {regime} regime; flags: {', '.join(risk_flags[:3]) or 'risk review'}."
    if decision == "caution":
        return f"{symbol} is tradable only with caution in a {regime} regime; strongest inputs are {leaders}."
    if decision == "watch":
        return f"{symbol} belongs on watch, but it still needs a live strategy trigger before execution."
    return f"{symbol} passes the ATLAS review in a {regime} regime, led by {leaders}."


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


def _weighted_average(values: dict[str, float], weights: dict[str, float] | None = None) -> float:
    if not values:
        return 0.0
    weights = weights or {name: 1.0 for name in values}
    total_weight = 0.0
    weighted_total = 0.0
    for name, value in values.items():
        weight = float(weights.get(name, 1.0))
        total_weight += weight
        weighted_total += _float(value) * weight
    if total_weight <= 0:
        return 0.0
    return _clamp(weighted_total / total_weight)


def _blend(base: float, adjusted: float, confidence: float) -> float:
    confidence = _clamp(confidence, 0.0, 1.0)
    return base + ((adjusted - base) * confidence)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _float(value)))


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
