from dataclasses import asdict, dataclass

from backend.app.agents.cio_decision import CIOAgent
from backend.app.agents.cro_risk_review import CROAgent
from backend.app.agents.macro_regime import MacroRegimeAgent
from backend.app.agents.sector_ranker import SectorRankerAgent
from backend.app.broker.base import AccountState
from backend.app.market_data import indicators
from backend.app.market_data.base import MarketDataProvider
from backend.app.market_data.universe import UniverseAsset, get_asset, get_tradable_universe
from backend.app.risk.position_sizing import calculate_position_size
from backend.app.risk.risk_limits import validate_entry
from backend.app.strategies.atlas_multi_agent import AtlasMultiAgentStrategy
from backend.app.strategies.base import TradingSignal, risk_reward
from backend.app.strategies.breakout import BreakoutStrategy
from backend.app.strategies.mean_reversion import MeanReversionStrategy
from backend.app.strategies.trend_momentum import TrendMomentumStrategy


@dataclass(frozen=True)
class CandidateDraft:
    ticker: str
    direction: str
    confidence: float
    strategy_score: float
    risk_score: float
    final_score: float
    entry_trigger: float | None
    stop_loss: float | None
    take_profit: float | None
    trailing_stop: float | None
    invalidation_condition: str
    expected_risk_reward: float
    suggested_position_size: int
    reason: str
    allowed: bool
    rejection_reason: str | None
    review: dict


def _strategy_signal(strategy_name: str, ticker: str, bars, spy_bars) -> TradingSignal:
    strategies = {
        "trend_momentum": TrendMomentumStrategy(),
        "mean_reversion": MeanReversionStrategy(),
        "breakout": BreakoutStrategy(),
        "atlas_multi_agent": AtlasMultiAgentStrategy(),
    }
    if strategy_name == "auto_strategy":
        signals = [strategy.generate(ticker, bars, spy_bars) for strategy in strategies.values() if strategy.name != "atlas_multi_agent"]
        return max(signals, key=lambda signal: (signal.direction == "BUY", signal.score))
    return strategies.get(strategy_name, TrendMomentumStrategy()).generate(ticker, bars, spy_bars)


def _asset_for_ticker(ticker: str) -> UniverseAsset:
    return get_asset(ticker) or UniverseAsset(ticker.upper(), ticker.upper(), "Unknown", "SPY")


def _candidate_universe(ticker: str | None, auto_pick: bool) -> list[UniverseAsset]:
    if auto_pick:
        return get_tradable_universe()
    if not ticker:
        return []
    return [_asset_for_ticker(ticker)]


def rank_candidates(
    *,
    market_data: MarketDataProvider,
    ticker: str | None,
    auto_pick: bool,
    strategy_name: str,
    risk_level: str,
    capital: float,
    account: AccountState,
    max_open_positions: int = 1,
    open_positions: int = 0,
    max_results: int = 12,
) -> list[CandidateDraft]:
    macro = MacroRegimeAgent(market_data).evaluate()
    sector_agent = SectorRankerAgent(market_data)
    cro_agent = CROAgent()
    cio_agent = CIOAgent()
    spy_bars = market_data.get_historical_bars("SPY", days=140)
    spy_roc = indicators.rate_of_change(indicators.closes(spy_bars), 20)
    drafts: list[CandidateDraft] = []

    for asset in _candidate_universe(ticker, auto_pick):
        bars = market_data.get_historical_bars(asset.ticker, days=140)
        if len(bars) < 60:
            continue
        values = indicators.closes(bars)
        quote = market_data.get_latest_quote(asset.ticker)
        avg_volume = indicators.average_volume(bars, 20)
        vol = indicators.volatility(values, 20)
        atr14 = indicators.atr(bars, 14) or quote.last * 0.02
        previous_close = bars[-2].close
        gap_risk = abs(bars[-1].open - previous_close) / max(atr14, 0.01)
        relative_strength = indicators.rate_of_change(values, 20) - spy_roc

        rejection_reasons: list[str] = []
        if not asset.tradable:
            rejection_reasons.append("Not tradable.")
        if quote.last < 5:
            rejection_reasons.append("Price is below minimum filter.")
        if avg_volume < 750_000:
            rejection_reasons.append("Average volume is below minimum filter.")
        if quote.spread_bps > 35:
            rejection_reasons.append("Spread is above maximum filter.")
        if vol > 0.08:
            rejection_reasons.append("Volatility sanity check failed.")

        signal = _strategy_signal(strategy_name, asset.ticker, bars, spy_bars)
        rr = risk_reward(signal.entry_trigger, signal.stop_loss, signal.take_profit)
        sizing = calculate_position_size(
            entry_price=signal.entry_trigger or quote.last,
            stop_loss=signal.stop_loss or 0,
            capital_allocation=capital,
            account_equity=account.equity,
            buying_power=account.buying_power,
            risk_level=risk_level,
        )
        sector = sector_agent.evaluate(asset.sector_etf)
        risk_decision = validate_entry(
            quote=quote,
            account=account,
            size=sizing,
            stop_loss=signal.stop_loss,
            expected_risk_reward=rr,
            open_positions=open_positions,
            risk_level=risk_level,
            max_open_positions=max_open_positions,
            max_spread_bps=35,
            max_quote_age_seconds=90,
        )
        cro = cro_agent.review(
            direction=signal.direction,
            entry=signal.entry_trigger,
            stop=signal.stop_loss,
            take_profit=signal.take_profit,
            expected_risk_reward=rr,
            spread_bps=quote.spread_bps,
            max_spread_bps=35,
            quote_timestamp=quote.timestamp,
            max_quote_age_seconds=90,
            suggested_size=sizing.shares,
        )
        cio = cio_agent.decide(
            strategy_direction=signal.direction,
            strategy_score=signal.score,
            macro=macro,
            sector=sector,
            cro=cro,
            risk_level=risk_level,
        )

        risk_score = indicators.clamp(
            0.78
            - (quote.spread_bps / 35) * 0.18
            - min(vol / 0.08, 1) * 0.22
            - min(gap_risk / 2.5, 1) * 0.14
            + indicators.clamp(relative_strength * 5, -0.12, 0.12)
        )
        final_score = indicators.clamp(0.52 * signal.score + 0.2 * risk_score + 0.14 * macro.score + 0.14 * sector.score)
        all_reasons = rejection_reasons + risk_decision.reasons
        if cio.decision != "BUY":
            all_reasons.append(cio.reason)
        allowed = not all_reasons and cro.allowed and cio.decision == "BUY"
        reason = (
            f"{signal.reason} {macro.reason} {sector.reason} {cio.reason}"
            if allowed
            else f"{signal.reason} Review result: {'; '.join(all_reasons[:4])}"
        )
        drafts.append(
            CandidateDraft(
                ticker=asset.ticker,
                direction=signal.direction if signal.direction == "BUY" else "WAIT",
                confidence=round(final_score, 3),
                strategy_score=round(signal.score, 3),
                risk_score=round(risk_score, 3),
                final_score=round(final_score, 3),
                entry_trigger=signal.entry_trigger,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                trailing_stop=signal.trailing_stop,
                invalidation_condition=signal.invalidation_condition,
                expected_risk_reward=round(rr, 2),
                suggested_position_size=sizing.shares,
                reason=reason,
                allowed=allowed,
                rejection_reason="; ".join(all_reasons) if all_reasons else None,
                review={
                    "macro": asdict(macro),
                    "sector": asdict(sector),
                    "cro": asdict(cro),
                    "cio": asdict(cio),
                    "features": {
                        **signal.features,
                        "average_volume": avg_volume,
                        "spread_bps": quote.spread_bps,
                        "volatility_20d": vol,
                        "relative_strength_vs_spy": relative_strength,
                        "gap_risk_atr": gap_risk,
                        "sizing_reason": sizing.reason,
                    },
                },
            )
        )

    return sorted(drafts, key=lambda candidate: (candidate.allowed, candidate.final_score), reverse=True)[:max_results]
