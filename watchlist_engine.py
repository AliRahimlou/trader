from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_store import OperatorStore
from scanner_models import RankedSymbol, WatchlistEntry, WatchlistState


@dataclass
class WatchlistStateManager:
    store: OperatorStore

    def load(self) -> WatchlistState:
        snapshot = self.store.get_snapshot("watchlist_state")
        if snapshot is None:
            return WatchlistState()
        payload = snapshot["payload"]
        entries = {
            symbol: WatchlistEntry(**entry_payload)
            for symbol, entry_payload in payload.get("entries", {}).items()
        }
        return WatchlistState(
            version=int(payload.get("version", 1)),
            last_scan_at=payload.get("last_scan_at"),
            next_scan_at=payload.get("next_scan_at"),
            universe_count=int(payload.get("universe_count", 0)),
            scanned_count=int(payload.get("scanned_count", 0)),
            active_symbols=list(payload.get("active_symbols", [])),
            pinned_symbols=list(payload.get("pinned_symbols", [])),
            disabled_symbols=list(payload.get("disabled_symbols", [])),
            entries=entries,
            additions=list(payload.get("additions", [])),
            removals=list(payload.get("removals", [])),
            health=dict(payload.get("health", {})),
        )

    def build(
        self,
        ranked_symbols: list[RankedSymbol],
        *,
        pinned_symbols: list[str],
        disabled_symbols: list[str],
        active_position_symbols: list[str],
        now: str,
        next_scan_at: str,
        universe_count: int,
        scanned_count: int,
        watchlist_size: int,
        hold_buffer: int,
        health: dict[str, Any],
    ) -> WatchlistState:
        previous = self.load()
        rank_lookup = {candidate.symbol: index + 1 for index, candidate in enumerate(ranked_symbols)}
        ranked_lookup = {candidate.symbol: candidate for candidate in ranked_symbols}

        active_symbols: list[str] = []
        for symbol in active_position_symbols:
            _append_unique(active_symbols, symbol)
        for symbol in pinned_symbols:
            _append_unique(active_symbols, symbol)
        for candidate in ranked_symbols:
            if candidate.eligible and len([symbol for symbol in active_symbols if symbol not in active_position_symbols]) < watchlist_size:
                _append_unique(active_symbols, candidate.symbol)
        for symbol in previous.active_symbols:
            if symbol in disabled_symbols and symbol not in pinned_symbols and symbol not in active_position_symbols:
                continue
            if symbol in rank_lookup and rank_lookup[symbol] <= watchlist_size + hold_buffer:
                _append_unique(active_symbols, symbol)

        additions: list[dict[str, Any]] = []
        removals: list[dict[str, Any]] = []
        previous_symbols = set(previous.active_symbols)
        current_symbols = set(active_symbols)

        for symbol in sorted(current_symbols - previous_symbols):
            additions.append({
                "symbol": symbol,
                "reason": self._watch_reason(symbol, pinned_symbols, active_position_symbols, rank_lookup, watchlist_size),
                "ts": now,
            })
        for symbol in sorted(previous_symbols - current_symbols):
            removals.append({
                "symbol": symbol,
                "reason": "dropped_from_watchlist",
                "ts": now,
            })

        entries: dict[str, WatchlistEntry] = {}
        for symbol in active_symbols:
            candidate = ranked_lookup.get(symbol)
            previous_entry = previous.entries.get(symbol)
            added_at = previous_entry.added_at if previous_entry else now
            entries[symbol] = WatchlistEntry(
                symbol=symbol,
                rank=rank_lookup.get(symbol, 0),
                score=float(candidate.score if candidate else 0.0),
                watch_reason=self._watch_reason(symbol, pinned_symbols, active_position_symbols, rank_lookup, watchlist_size),
                pinned=symbol in pinned_symbols,
                enabled=symbol not in disabled_symbols,
                active_position=symbol in active_position_symbols,
                score_components=dict(candidate.score_components if candidate else {}),
                exclusion_reasons=list(candidate.exclusion_reasons if candidate else []),
                signals=list(candidate.signals if candidate else []),
                features=dict(candidate.features if candidate else {}),
                added_at=added_at,
                updated_at=now,
            )

        return WatchlistState(
            version=1,
            last_scan_at=now,
            next_scan_at=next_scan_at,
            universe_count=universe_count,
            scanned_count=scanned_count,
            active_symbols=active_symbols,
            pinned_symbols=list(pinned_symbols),
            disabled_symbols=list(disabled_symbols),
            entries=entries,
            additions=additions,
            removals=removals,
            health=health,
        )

    def persist(self, state: WatchlistState, ranked_symbols: list[RankedSymbol]) -> None:
        self.store.upsert_snapshot("watchlist_state", state.to_dict())
        self.store.upsert_snapshot(
            "watchlist",
            {
                "last_scan_at": state.last_scan_at,
                "next_scan_at": state.next_scan_at,
                "active_symbols": state.active_symbols,
                "pinned_symbols": state.pinned_symbols,
                "disabled_symbols": state.disabled_symbols,
                "entries": [entry.to_dict() for entry in state.entries.values()],
                "additions": state.additions,
                "removals": state.removals,
                "health": state.health,
            },
        )
        self.store.upsert_snapshot(
            "scanner_ranked",
            {
                "items": [candidate.to_dict() for candidate in ranked_symbols],
                "last_scan_at": state.last_scan_at,
                "next_scan_at": state.next_scan_at,
            },
        )
        self.store.upsert_snapshot(
            "scanner_status",
            {
                "last_scan_at": state.last_scan_at,
                "next_scan_at": state.next_scan_at,
                "universe_count": state.universe_count,
                "scanned_count": state.scanned_count,
                "active_watchlist": state.active_symbols,
                "pinned_symbols": state.pinned_symbols,
                "disabled_symbols": state.disabled_symbols,
                "health": state.health,
            },
        )

    def _watch_reason(
        self,
        symbol: str,
        pinned_symbols: list[str],
        active_position_symbols: list[str],
        rank_lookup: dict[str, int],
        watchlist_size: int,
    ) -> str:
        if symbol in active_position_symbols:
            return "open_position"
        if symbol in pinned_symbols:
            return "pinned_symbol"
        rank = rank_lookup.get(symbol, 0)
        if rank and rank <= watchlist_size:
            return "top_ranked"
        return "retained_buffer"


def _append_unique(symbols: list[str], symbol: str) -> None:
    normalized = symbol.upper()
    if normalized not in symbols:
        symbols.append(normalized)
