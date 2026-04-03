from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from alpaca_api import (
    AlpacaConfig,
    fetch_multi_stock_bars,
    fetch_stock_snapshots,
    fetch_latest_trade,
    period_to_start,
)
from live_scheduler import parse_hhmm, validate_latest_bar
from market_data_cache import MarketContextCache
from ranking_engine import build_score_components, total_score
from scanner_models import RankedSymbol, ScanResult, WatchlistState
from strategy_context import get_daily_bias, get_previous_day_levels
from strategy_signals import StrategySignal, detect_break_setup, detect_pullback_setup, get_opening_range_bar, materialize_signal
from universe_manager import UniverseManager
from watchlist_engine import WatchlistStateManager


@dataclass
class ScannerEngine:
    config: object
    store: object
    alpaca_config: AlpacaConfig | None

    def __post_init__(self) -> None:
        self.universe_manager = UniverseManager(self.config, self.alpaca_config)
        self.watchlist_manager = WatchlistStateManager(self.store)
        self.market_caches: dict[str, MarketContextCache] = {}
        self.latest_result: ScanResult | None = None
        self._universe_rotation_offset = 0

    def load_cached_state(self) -> None:
        cached_status = self.store.get_snapshot("scanner_status")
        cached_ranked = self.store.get_snapshot("scanner_ranked")
        cached_watchlist = self.store.get_snapshot("watchlist_state")
        if cached_status is None or cached_ranked is None or cached_watchlist is None:
            return
        watchlist_state = self.watchlist_manager.load()
        ranked = [RankedSymbol(**payload) for payload in cached_ranked["payload"].get("items", [])]
        self.latest_result = ScanResult(
            last_scan_at=str(cached_status["payload"].get("last_scan_at") or ""),
            next_scan_at=str(cached_status["payload"].get("next_scan_at") or ""),
            universe_count=int(cached_status["payload"].get("universe_count", 0)),
            scanned_count=int(cached_status["payload"].get("scanned_count", 0)),
            ranked_symbols=ranked,
            watchlist_state=watchlist_state,
            health=dict(cached_status["payload"].get("health", {})),
        )

    def fallback_result(
        self,
        now: pd.Timestamp,
        *,
        error: Exception | str | None = None,
        retry_seconds: int | None = None,
    ) -> ScanResult:
        now_utc = now if now.tzinfo else now.tz_localize("UTC")
        retry_window = max(int(retry_seconds or 0), int(self.config.universe_refresh_seconds), 60)
        next_scan_at = (now_utc + pd.Timedelta(seconds=retry_window)).isoformat()

        if self.latest_result is not None:
            ranked_symbols = list(self.latest_result.ranked_symbols)
            watchlist_state = self.latest_result.watchlist_state
            universe_count = int(self.latest_result.universe_count)
            scanned_count = int(self.latest_result.scanned_count)
            last_scan_at = self.latest_result.last_scan_at
            health = dict(self.latest_result.health)
        else:
            ranked_symbols = []
            watchlist_state = self.watchlist_manager.load()
            universe_count = int(watchlist_state.universe_count)
            scanned_count = int(watchlist_state.scanned_count)
            last_scan_at = watchlist_state.last_scan_at or now_utc.isoformat()
            health = dict(watchlist_state.health)

        watchlist_state.last_scan_at = watchlist_state.last_scan_at or last_scan_at
        watchlist_state.next_scan_at = next_scan_at
        watchlist_state.additions = []
        watchlist_state.removals = []
        health.update(
            {
                "fallback": True,
                "last_error": str(error) if error is not None else health.get("last_error"),
                "retry_after_seconds": retry_window,
            }
        )
        if "healthy" not in health:
            health["healthy"] = bool(ranked_symbols or watchlist_state.active_symbols)
        watchlist_state.health = health

        result = ScanResult(
            last_scan_at=last_scan_at,
            next_scan_at=next_scan_at,
            universe_count=universe_count,
            scanned_count=scanned_count,
            ranked_symbols=ranked_symbols,
            watchlist_state=watchlist_state,
            health=health,
        )
        self.latest_result = result
        return result

    def should_refresh(self, now: pd.Timestamp, *, force: bool = False) -> bool:
        if force or self.latest_result is None or not self.latest_result.last_scan_at:
            return True
        next_scan = pd.Timestamp(self.latest_result.next_scan_at)
        if next_scan.tzinfo is None:
            next_scan = next_scan.tz_localize("UTC")
        return now.tz_convert("UTC") >= next_scan.tz_convert("UTC")

    def refresh(
        self,
        *,
        now: pd.Timestamp,
        market_open: bool,
        runtime_flags: dict[str, Any],
        state: object,
        latest_positions: list[dict[str, Any]],
        logger: object,
        force: bool = False,
    ) -> ScanResult:
        now_utc = now.tz_convert("UTC")
        if not self.should_refresh(now_utc, force=force):
            assert self.latest_result is not None
            return self.latest_result

        if self.alpaca_config is None:
            result = self._refresh_demo(now_utc, runtime_flags=runtime_flags, latest_positions=latest_positions)
        else:
            result = self._refresh_real(
                now_utc,
                market_open=market_open,
                runtime_flags=runtime_flags,
                state=state,
                latest_positions=latest_positions,
            )

        self.watchlist_manager.persist(result.watchlist_state, result.ranked_symbols)
        self.latest_result = result
        logger.emit(
            "scanner_cycle_complete",
            level="INFO",
            message=(
                f"Scanner refreshed universe={result.universe_count} scanned={result.scanned_count} "
                f"watchlist={','.join(result.watchlist_state.active_symbols) or 'none'}"
            ),
            universe_count=result.universe_count,
            scanned_count=result.scanned_count,
            watchlist=result.watchlist_state.active_symbols,
            last_scan_at=result.last_scan_at,
            next_scan_at=result.next_scan_at,
        )
        for addition in result.watchlist_state.additions:
            logger.emit(
                "watchlist_symbol_added",
                level="INFO",
                message=f"Added {addition['symbol']} to active watchlist ({addition['reason']}).",
                symbol=addition["symbol"],
                reason=addition["reason"],
            )
        for removal in result.watchlist_state.removals:
            logger.emit(
                "watchlist_symbol_removed",
                level="INFO",
                message=f"Removed {removal['symbol']} from active watchlist ({removal['reason']}).",
                symbol=removal["symbol"],
                reason=removal["reason"],
            )
        return result

    def ranked_payload(self, *, limit: int = 50) -> dict[str, Any]:
        ranked = [] if self.latest_result is None else [candidate.to_dict() for candidate in self.latest_result.ranked_symbols[:limit]]
        return {
            "items": ranked,
            "last_scan_at": self.latest_result.last_scan_at if self.latest_result else None,
            "next_scan_at": self.latest_result.next_scan_at if self.latest_result else None,
        }

    def watchlist_payload(self) -> dict[str, Any]:
        if self.latest_result is None:
            return self.watchlist_manager.load().to_dict()
        return self.latest_result.watchlist_state.to_dict()

    def status_payload(self) -> dict[str, Any]:
        if self.latest_result is None:
            snapshot = self.store.get_snapshot("scanner_status")
            return {} if snapshot is None else snapshot["payload"]
        return self.latest_result.to_status_payload()

    def ensure_symbol_market_data(self, symbol: str, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
        symbol = symbol.upper()
        if self.alpaca_config is None:
            raise RuntimeError("Scanner market data is unavailable in demo mode.")
        cache = self.market_caches.get(symbol)
        if cache is None:
            cache = MarketContextCache(
                symbol=symbol,
                alpaca_config=self.alpaca_config,
                minute_lookback=self.config.minute_lookback,
                five_minute_lookback=self.config.five_minute_lookback,
                daily_lookback=self.config.daily_lookback,
                minute_refresh_window=self.config.minute_refresh_window,
                five_minute_refresh_window=self.config.five_minute_refresh_window,
                daily_refresh_window=self.config.daily_refresh_window,
            )
            self.market_caches[symbol] = cache
        return cache.refresh(end)

    def _snapshot_symbols_for_refresh(
        self,
        universe_members: dict[str, Any],
        *,
        pinned_symbols: list[str],
        position_symbols: list[str],
    ) -> list[str]:
        available_symbols = sorted(universe_members)
        max_symbols = int(self.config.universe_max_symbols)
        if max_symbols <= 0 or max_symbols >= len(available_symbols):
            return available_symbols

        watchlist_state: WatchlistState
        if self.latest_result is not None:
            watchlist_state = self.latest_result.watchlist_state
        else:
            watchlist_state = self.watchlist_manager.load()

        selected_symbols: list[str] = []
        priority_symbols = [
            *position_symbols,
            *pinned_symbols,
            *watchlist_state.active_symbols,
            *self.config.configured_symbols,
        ]
        for symbol in priority_symbols:
            normalized_symbol = str(symbol or "").upper()
            if normalized_symbol in universe_members and normalized_symbol not in selected_symbols:
                selected_symbols.append(normalized_symbol)
            if len(selected_symbols) >= max_symbols:
                return selected_symbols[:max_symbols]

        remaining_budget = max_symbols - len(selected_symbols)
        if remaining_budget <= 0:
            return selected_symbols

        remaining_symbols = [symbol for symbol in available_symbols if symbol not in selected_symbols]
        if not remaining_symbols:
            return selected_symbols

        start_index = self._universe_rotation_offset % len(remaining_symbols)
        rotated_symbols = remaining_symbols[start_index:] + remaining_symbols[:start_index]
        selected_symbols.extend(rotated_symbols[:remaining_budget])
        self._universe_rotation_offset = (start_index + remaining_budget) % len(remaining_symbols)
        return selected_symbols

    def _refresh_demo(
        self,
        now: pd.Timestamp,
        *,
        runtime_flags: dict[str, Any],
        latest_positions: list[dict[str, Any]],
    ) -> ScanResult:
        symbols = list(self.config.configured_symbols)
        ranked: list[RankedSymbol] = []
        for index, symbol in enumerate(symbols):
            score = max(0.0, 82.0 - index * 7.5)
            features = {
                "price": round(100.0 + index * 12.0, 2),
                "gap_pct": round(0.4 + index * 0.12, 2),
                "intraday_return_pct": round(0.8 - index * 0.1, 2),
                "atr_pct": round(1.2 + index * 0.2, 2),
                "relative_volume": round(1.0 + index * 0.1, 2),
                "spread_bps": round(6.0 + index, 2),
                "dollar_volume": float(2_000_000 + index * 500_000),
                "data_fresh": True,
            }
            candidate = RankedSymbol(
                symbol=symbol,
                score=score,
                eligible=runtime_flags.get("enabled_symbols", {}).get(symbol, True),
                score_components=build_score_components(features, signal_count=1 if index < 2 else 0),
                exclusion_reasons=[] if runtime_flags.get("enabled_symbols", {}).get(symbol, True) else ["disabled_override"],
                features=features,
                signals=[{"strategy_id": "break", "direction": "long", "reason": "demo_setup"}] if index < 2 else [],
                asset={"tradable": True, "fractionable": True, "shortable": True},
            )
            ranked.append(candidate)
        ranked.sort(key=lambda item: (-item.score, item.symbol))
        for index, candidate in enumerate(ranked, start=1):
            candidate.rank = index
        disabled_symbols = sorted(symbol for symbol, enabled in runtime_flags.get("enabled_symbols", {}).items() if not enabled)
        pinned_symbols = sorted(runtime_flags.get("pinned_symbols", []))
        watchlist_state = self.watchlist_manager.build(
            ranked,
            pinned_symbols=pinned_symbols,
            disabled_symbols=disabled_symbols,
            active_position_symbols=[position["symbol"] for position in latest_positions],
            now=now.isoformat(),
            next_scan_at=(now + pd.Timedelta(seconds=self.config.universe_refresh_seconds)).isoformat(),
            universe_count=len(symbols),
            scanned_count=len(ranked),
            watchlist_size=self.config.watchlist_size,
            hold_buffer=self.config.watchlist_hold_buffer,
            health={"healthy": True, "demo_mode": True, "failures": 0},
        )
        return ScanResult(
            last_scan_at=now.isoformat(),
            next_scan_at=(now + pd.Timedelta(seconds=self.config.universe_refresh_seconds)).isoformat(),
            universe_count=len(symbols),
            scanned_count=len(ranked),
            ranked_symbols=ranked,
            watchlist_state=watchlist_state,
            health={"healthy": True, "demo_mode": True, "failures": 0},
        )

    def _refresh_real(
        self,
        now: pd.Timestamp,
        *,
        market_open: bool,
        runtime_flags: dict[str, Any],
        state: object,
        latest_positions: list[dict[str, Any]],
    ) -> ScanResult:
        assert self.alpaca_config is not None
        universe = self.universe_manager.load()
        universe_members = {member.symbol: member for member in universe}
        disabled_symbols = sorted(symbol for symbol, enabled in runtime_flags.get("enabled_symbols", {}).items() if not enabled)
        pinned_symbols = sorted(runtime_flags.get("pinned_symbols", []))
        position_symbols = sorted({str(position.get("symbol") or "").upper() for position in latest_positions if position.get("symbol")})
        snapshot_symbols = self._snapshot_symbols_for_refresh(
            universe_members,
            pinned_symbols=pinned_symbols,
            position_symbols=position_symbols,
        )
        snapshots = fetch_stock_snapshots(snapshot_symbols, config=self.alpaca_config)

        quick_candidates: list[RankedSymbol] = []
        for symbol in snapshot_symbols:
            member = universe_members[symbol]
            snapshot = snapshots.get(symbol, {})
            candidate = self._build_quick_candidate(symbol, member, snapshot, runtime_flags, market_open=market_open)
            quick_candidates.append(candidate)

        scan_symbols = self._select_scan_symbols(quick_candidates, pinned_symbols=pinned_symbols, position_symbols=position_symbols)
        daily_frames = fetch_multi_stock_bars(
            scan_symbols,
            "1d",
            period_to_start(now, self.config.daily_lookback),
            now,
            config=self.alpaca_config,
        )

        candidates: list[RankedSymbol] = []
        for candidate in quick_candidates:
            if candidate.symbol not in scan_symbols:
                continue
            daily_frame = daily_frames.get(candidate.symbol)
            self._enrich_with_daily_features(candidate, daily_frame)
            candidates.append(candidate)

        shortlist_symbols = self._build_shortlist(candidates, pinned_symbols=pinned_symbols, position_symbols=position_symbols)
        for candidate in candidates:
            if candidate.symbol not in shortlist_symbols:
                candidate.score_components = build_score_components(candidate.features, signal_count=0)
                candidate.score = total_score(candidate.score_components, config=self.config)
                continue
            try:
                market_data = self.ensure_symbol_market_data(candidate.symbol, now)
                latest_bar_time = market_data["1m"].index.max()
                if market_open:
                    validate_latest_bar(now.tz_convert("America/New_York"), latest_bar_time, max_bar_age_seconds=self.config.max_bar_age_seconds)
                    candidate.features["data_fresh"] = True
                reference_price = float(fetch_latest_trade(self.alpaca_config, candidate.symbol)["p"])
                signals = evaluate_strategy_signals(
                    symbol=candidate.symbol,
                    market_data=market_data,
                    reference_price=reference_price,
                    strategies=self.config.strategies,
                    strategy_config=self.config.strategy_config,
                    enabled_strategies=[
                        strategy_id
                        for strategy_id in self.config.strategies
                        if runtime_flags.get("enabled_strategies", {}).get(strategy_id, True)
                    ],
                )
                candidate.signals = [serialize_signal(signal) for signal in signals]
                self._enrich_with_intraday_features(candidate, market_data)
            except Exception as exc:
                candidate.features["data_fresh"] = False
                candidate.exclusion_reasons.append(f"scanner_error:{exc}")
                candidate.notes.append(str(exc))
            candidate.score_components = build_score_components(candidate.features, signal_count=len(candidate.signals))
            candidate.score = total_score(candidate.score_components, config=self.config)
            candidate.eligible = not candidate.exclusion_reasons

        candidates.sort(key=lambda item: (-item.score, -float(item.features.get("dollar_volume") or 0.0), item.symbol))
        for index, candidate in enumerate(candidates, start=1):
            candidate.rank = index

        health = {
            "healthy": any(candidate.eligible for candidate in candidates),
            "market_open": market_open,
            "failures": sum(1 for candidate in candidates if any(reason.startswith("scanner_error:") for reason in candidate.exclusion_reasons)),
            "disabled_symbols": disabled_symbols,
        }
        watchlist_state = self.watchlist_manager.build(
            candidates,
            pinned_symbols=pinned_symbols,
            disabled_symbols=disabled_symbols,
            active_position_symbols=position_symbols,
            now=now.isoformat(),
            next_scan_at=(now + pd.Timedelta(seconds=self.config.universe_refresh_seconds)).isoformat(),
            universe_count=len(universe),
            scanned_count=len(candidates),
            watchlist_size=self.config.watchlist_size,
            hold_buffer=self.config.watchlist_hold_buffer,
            health=health,
        )
        return ScanResult(
            last_scan_at=now.isoformat(),
            next_scan_at=(now + pd.Timedelta(seconds=self.config.universe_refresh_seconds)).isoformat(),
            universe_count=len(universe),
            scanned_count=len(candidates),
            ranked_symbols=candidates,
            watchlist_state=watchlist_state,
            health=health,
        )

    def _build_quick_candidate(
        self,
        symbol: str,
        member: object,
        snapshot: dict[str, Any],
        runtime_flags: dict[str, Any],
        *,
        market_open: bool,
    ) -> RankedSymbol:
        latest_trade = snapshot.get("latestTrade") or {}
        latest_quote = snapshot.get("latestQuote") or {}
        daily_bar = snapshot.get("dailyBar") or {}
        prev_daily = snapshot.get("prevDailyBar") or {}
        minute_bar = snapshot.get("minuteBar") or {}
        previous_close = float(prev_daily.get("c") or 0.0)
        price = float(daily_bar.get("c") or latest_trade.get("p") or previous_close or 0.0)
        session_open = float(daily_bar.get("o") or previous_close or price or 0.0)
        session_high = float(daily_bar.get("h") or price or 0.0)
        session_low = float(daily_bar.get("l") or price or 0.0)
        session_volume = float(daily_bar.get("v") or minute_bar.get("v") or prev_daily.get("v") or 0.0)
        bid = float(latest_quote.get("bp") or 0.0)
        ask = float(latest_quote.get("ap") or 0.0)
        spread_bps = ((ask - bid) / price * 10_000.0) if price > 0 and bid > 0 and ask > 0 else 0.0
        intraday_return_pct = ((price - session_open) / session_open * 100.0) if session_open else 0.0
        gap_pct = ((session_open - previous_close) / previous_close * 100.0) if previous_close else 0.0
        day_range_pct = ((session_high - session_low) / price * 100.0) if price else 0.0
        features = {
            "price": round(price, 4),
            "previous_close": round(previous_close, 4),
            "session_open": round(session_open, 4),
            "session_high": round(session_high, 4),
            "session_low": round(session_low, 4),
            "session_volume": session_volume,
            "spread_bps": round(spread_bps, 4),
            "intraday_return_pct": round(intraday_return_pct, 4),
            "gap_pct": round(gap_pct, 4),
            "day_range_pct": round(day_range_pct, 4),
            "dollar_volume": round(price * session_volume, 2),
            "data_fresh": bool(latest_trade or daily_bar or prev_daily),
            "market_open": market_open,
        }
        exclusions: list[str] = []
        if not member.tradable:
            exclusions.append("asset_not_tradable")
        if price <= 0:
            exclusions.append("missing_price")
        if self.config.scanner_min_price > 0 and price < self.config.scanner_min_price:
            exclusions.append("below_min_price")
        if self.config.scanner_max_price > 0 and price > self.config.scanner_max_price:
            exclusions.append("above_max_price")
        if self.config.scanner_max_spread_bps > 0 and spread_bps > self.config.scanner_max_spread_bps:
            exclusions.append("spread_too_wide")
        if member.is_leveraged and self.config.exclude_leveraged_etfs:
            exclusions.append("leveraged_etf_excluded")
        if runtime_flags.get("enabled_symbols", {}).get(symbol, True) is False:
            exclusions.append("disabled_override")
        return RankedSymbol(
            symbol=symbol,
            eligible=not exclusions,
            exclusion_reasons=exclusions,
            features=features,
            asset={
                "name": member.name,
                "exchange": member.exchange,
                "tradable": member.tradable,
                "shortable": member.shortable,
                "fractionable": member.fractionable,
                "easy_to_borrow": member.easy_to_borrow,
                "is_etf": member.is_etf,
                "is_leveraged": member.is_leveraged,
            },
        )

    def _select_scan_symbols(
        self,
        candidates: list[RankedSymbol],
        *,
        pinned_symbols: list[str],
        position_symbols: list[str],
    ) -> list[str]:
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (-float(item.features.get("dollar_volume") or 0.0), item.symbol),
        )
        symbols: list[str] = []
        for symbol in pinned_symbols + position_symbols:
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        for candidate in sorted_candidates:
            if candidate.symbol not in symbols:
                symbols.append(candidate.symbol)
            if len(symbols) >= self.config.universe_max_symbols:
                break
        return symbols

    def _build_shortlist(
        self,
        candidates: list[RankedSymbol],
        *,
        pinned_symbols: list[str],
        position_symbols: list[str],
    ) -> list[str]:
        shortlist: list[str] = []
        for symbol in pinned_symbols + position_symbols:
            if symbol and symbol not in shortlist:
                shortlist.append(symbol)
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (-float(item.features.get("dollar_volume") or 0.0), item.symbol),
        )
        target = self.config.watchlist_size + self.config.watchlist_hold_buffer + 4
        for candidate in sorted_candidates:
            if candidate.symbol not in shortlist:
                shortlist.append(candidate.symbol)
            if len(shortlist) >= target:
                break
        return shortlist

    def _enrich_with_daily_features(self, candidate: RankedSymbol, daily_frame: pd.DataFrame | None) -> None:
        if daily_frame is None or daily_frame.empty:
            candidate.exclusion_reasons.append("missing_daily_bars")
            return
        closes = daily_frame["close"].tail(20)
        average_daily_volume = float(daily_frame["volume"].tail(20).mean()) if not daily_frame.empty else 0.0
        atr_pct = float((((daily_frame["high"] - daily_frame["low"]) / daily_frame["close"]).tail(14).mean()) * 100.0)
        trend_pct = ((float(candidate.features.get("price") or 0.0) / float(closes.mean())) - 1.0) * 100.0 if len(closes) else 0.0
        relative_volume = (float(candidate.features.get("session_volume") or 0.0) / average_daily_volume) if average_daily_volume > 0 else 0.0
        momentum_5d = 0.0
        if len(daily_frame) >= 6:
            momentum_5d = ((float(candidate.features.get("price") or 0.0) / float(daily_frame["close"].iloc[-6])) - 1.0) * 100.0
        bias = get_daily_bias(daily_frame, daily_frame.index[-1].date()) if len(daily_frame) >= 3 else "neutral"
        prev_day_high, prev_day_low = get_previous_day_levels(daily_frame, daily_frame.index[-1].date())
        price = float(candidate.features.get("price") or 0.0)
        candidate.features.update(
            {
                "average_daily_volume": round(average_daily_volume, 2),
                "atr_pct": round(atr_pct, 4),
                "trend_pct": round(trend_pct, 4),
                "relative_volume": round(relative_volume, 4),
                "momentum_5d_pct": round(momentum_5d, 4),
                "daily_bias": bias,
                "distance_prev_day_high_pct": round(((price - prev_day_high) / price * 100.0), 4) if prev_day_high and price else None,
                "distance_prev_day_low_pct": round(((price - prev_day_low) / price * 100.0), 4) if prev_day_low and price else None,
            }
        )
        if self.config.scanner_min_avg_daily_volume > 0 and average_daily_volume < self.config.scanner_min_avg_daily_volume:
            candidate.exclusion_reasons.append("below_min_average_volume")

    def _enrich_with_intraday_features(self, candidate: RankedSymbol, market_data: dict[str, pd.DataFrame]) -> None:
        minute_df = market_data["1m"]
        session_date = minute_df.index[-1].date()
        session_df = minute_df[minute_df.index.date == session_date]
        if session_df.empty:
            candidate.exclusion_reasons.append("missing_session_bars")
            return
        candidate.features["session_range_expansion_pct"] = round(((session_df["high"].max() - session_df["low"].min()) / max(float(candidate.features.get("price") or 1.0), 1e-6)) * 100.0, 4)
        candidate.features["recent_momentum_pct"] = round(((session_df["close"].iloc[-1] / session_df["close"].iloc[max(0, len(session_df) - 6)]) - 1.0) * 100.0, 4) if len(session_df) >= 6 else 0.0


def evaluate_strategy_signals(
    symbol: str,
    market_data: dict[str, pd.DataFrame],
    reference_price: float,
    *,
    strategies: tuple[str, ...],
    strategy_config: object,
    enabled_strategies: list[str] | None = None,
) -> list[StrategySignal]:
    minute_df = market_data["1m"]
    session_date = minute_df.index[-1].date()
    session_1m = minute_df[minute_df.index.date == session_date].copy()
    enabled = list(enabled_strategies or strategies)
    strategy_order = {strategy_id: index for index, strategy_id in enumerate(enabled)}
    signals: list[StrategySignal] = []

    if "break" in enabled:
        break_session = session_1m[session_1m.index.time > parse_hhmm("09:35")]
        if len(break_session) >= 3:
            opening_range_bar = get_opening_range_bar(market_data["5m"], session_date)
            setup = detect_break_setup(
                break_session,
                opening_range_bar,
                len(break_session) - 1,
                config=strategy_config,
            )
            signal = materialize_signal(setup, reference_price, config=strategy_config)
            if signal is not None:
                signals.append(signal)

    if "pullback" in enabled and len(session_1m) >= 4:
        setup = detect_pullback_setup(
            session_1m,
            market_data["1d"],
            len(session_1m) - 2,
            config=strategy_config,
        )
        signal = materialize_signal(setup, reference_price, config=strategy_config)
        if signal is not None:
            signals.append(signal)

    signals.sort(key=lambda signal: (strategy_order.get(signal.strategy_id, 99), signal.signal_time))
    return signals


def serialize_signal(signal: StrategySignal) -> dict[str, Any]:
    return {
        "strategy_id": signal.strategy_id,
        "strategy_name": signal.strategy_name,
        "direction": signal.direction,
        "signal_time": signal.signal_time.isoformat(),
        "signal_key": signal.signal_key,
        "entry_reference_price": signal.entry_reference_price,
        "stop_price": signal.stop_price,
        "target_price": signal.target_price,
        "requested_qty": signal.quantity,
        "approved_qty": signal.quantity,
        "allowed": True,
        "reasons": [],
        "reason": signal.reason,
    }