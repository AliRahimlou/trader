from dataclasses import dataclass

from backend.app.agents.cro_risk_review import CROReview
from backend.app.agents.macro_regime import MacroRegime
from backend.app.agents.sector_ranker import SectorStrength


@dataclass(frozen=True)
class CIODecision:
    decision: str
    score: float
    reason: str


class CIOAgent:
    def decide(
        self,
        *,
        strategy_direction: str,
        strategy_score: float,
        macro: MacroRegime,
        sector: SectorStrength,
        cro: CROReview,
        risk_level: str,
    ) -> CIODecision:
        if not cro.allowed:
            return CIODecision("HOLD", min(strategy_score, cro.score), cro.reason)
        if strategy_direction != "BUY":
            return CIODecision("WAIT", strategy_score, "Strategy signal is not actionable yet.")
        if macro.label == "risk_off" and risk_level != "aggressive":
            return CIODecision("HOLD", 0.35, "CIO rejected new long exposure in a risk-off market.")
        score = 0.5 * strategy_score + 0.25 * macro.score + 0.25 * sector.score
        if score >= 0.62:
            return CIODecision("BUY", score, "CIO approved: strategy, macro, sector, and risk checks align.")
        return CIODecision("WAIT", score, "CIO is waiting for stronger alignment before entering.")
