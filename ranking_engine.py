from __future__ import annotations

import math
from typing import Any


def component_weights(config: object) -> dict[str, float]:
    return {
        "liquidity": float(config.scanner_weight_liquidity),
        "volatility": float(config.scanner_weight_volatility),
        "momentum": float(config.scanner_weight_momentum),
        "gap": float(config.scanner_weight_gap),
        "trend": float(config.scanner_weight_trend),
        "setup": float(config.scanner_weight_setup),
        "spread": float(config.scanner_weight_spread),
        "freshness": float(config.scanner_weight_freshness),
    }


def build_score_components(features: dict[str, Any], *, signal_count: int) -> dict[str, float]:
    dollar_volume = max(float(features.get("dollar_volume") or 0.0), 0.0)
    atr_pct = abs(float(features.get("atr_pct") or 0.0))
    intraday_return = abs(float(features.get("intraday_return_pct") or 0.0))
    gap_pct = abs(float(features.get("gap_pct") or 0.0))
    trend_pct = float(features.get("trend_pct") or 0.0)
    spread_bps = max(float(features.get("spread_bps") or 0.0), 0.0)
    relative_volume = max(float(features.get("relative_volume") or 0.0), 0.0)
    data_fresh = bool(features.get("data_fresh", False))

    liquidity = _scale(math.log10(dollar_volume + 1.0), 5.0, 9.5)
    volatility = _scale(atr_pct, 0.35, 8.0)
    momentum = max(_scale(intraday_return, 0.0, 3.5), _scale(relative_volume, 0.5, 4.0))
    gap = _scale(gap_pct, 0.0, 4.5)
    trend = _signed_scale(trend_pct, 0.0, 8.0)
    setup = 0.0 if signal_count <= 0 else min(100.0, 55.0 + signal_count * 25.0)
    spread = 100.0 - _scale(spread_bps, 2.0, 60.0)
    freshness = 100.0 if data_fresh else 0.0

    return {
        "liquidity": round(liquidity, 2),
        "volatility": round(volatility, 2),
        "momentum": round(momentum, 2),
        "gap": round(gap, 2),
        "trend": round(trend, 2),
        "setup": round(setup, 2),
        "spread": round(max(spread, 0.0), 2),
        "freshness": round(freshness, 2),
    }


def total_score(components: dict[str, float], *, config: object) -> float:
    weights = component_weights(config)
    total_weight = sum(weights.values()) or 1.0
    weighted_score = sum(float(components.get(name, 0.0)) * weight for name, weight in weights.items())
    return round(weighted_score / total_weight, 2)


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    bounded = max(low, min(high, value))
    return ((bounded - low) / (high - low)) * 100.0


def _signed_scale(value: float, low: float, high: float) -> float:
    magnitude = _scale(abs(value), low, high)
    if value >= 0:
        return 50.0 + (magnitude / 2.0)
    return 50.0 - (magnitude / 2.0)
