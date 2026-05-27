from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyWeight:
    name: str
    weight: float


class JANUSWeighting:
    def weights(self, recent_performance: dict[str, float] | None = None) -> list[StrategyWeight]:
        performance = recent_performance or {}
        baseline = {
            "trend_momentum": 1.0,
            "mean_reversion": 1.0,
            "breakout": 1.0,
        }
        adjusted = {
            name: max(0.5, min(1.5, baseline[name] + performance.get(name, 0.0)))
            for name in baseline
        }
        total = sum(adjusted.values())
        return [StrategyWeight(name, value / total) for name, value in adjusted.items()]
