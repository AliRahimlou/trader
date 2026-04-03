from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    name: str = ""
    exchange: str | None = None
    asset_class: str = "us_equity"
    tradable: bool = True
    shortable: bool = False
    fractionable: bool = False
    easy_to_borrow: bool = False
    is_etf: bool = False
    is_leveraged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RankedSymbol:
    symbol: str
    score: float = 0.0
    rank: int = 0
    eligible: bool = False
    score_components: dict[str, float] = field(default_factory=dict)
    exclusion_reasons: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    signals: list[dict[str, Any]] = field(default_factory=list)
    asset: dict[str, Any] = field(default_factory=dict)
    status: str = "scanned"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WatchlistEntry:
    symbol: str
    rank: int
    score: float
    watch_reason: str
    pinned: bool = False
    enabled: bool = True
    active_position: bool = False
    score_components: dict[str, float] = field(default_factory=dict)
    exclusion_reasons: list[str] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    added_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WatchlistState:
    version: int = 1
    last_scan_at: str | None = None
    next_scan_at: str | None = None
    universe_count: int = 0
    scanned_count: int = 0
    active_symbols: list[str] = field(default_factory=list)
    pinned_symbols: list[str] = field(default_factory=list)
    disabled_symbols: list[str] = field(default_factory=list)
    entries: dict[str, WatchlistEntry] = field(default_factory=dict)
    additions: list[dict[str, Any]] = field(default_factory=list)
    removals: list[dict[str, Any]] = field(default_factory=list)
    health: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entries"] = {symbol: entry.to_dict() for symbol, entry in self.entries.items()}
        return payload


@dataclass
class ScanResult:
    last_scan_at: str
    next_scan_at: str
    universe_count: int
    scanned_count: int
    ranked_symbols: list[RankedSymbol]
    watchlist_state: WatchlistState
    health: dict[str, Any]

    def to_status_payload(self) -> dict[str, Any]:
        return {
            "last_scan_at": self.last_scan_at,
            "next_scan_at": self.next_scan_at,
            "universe_count": self.universe_count,
            "scanned_count": self.scanned_count,
            "health": self.health,
            "active_watchlist": self.watchlist_state.active_symbols,
            "pinned_symbols": self.watchlist_state.pinned_symbols,
        }
