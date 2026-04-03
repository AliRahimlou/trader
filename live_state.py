from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunnerState:
    symbol: str
    strategies: list[str]
    version: int = 2
    last_processed_bars: dict[str, str] = field(default_factory=dict)
    processed_signal_keys: dict[str, list[str]] = field(default_factory=dict)
    daily_realized_pnl: dict[str, float] = field(default_factory=dict)
    daily_trade_count: dict[str, int] = field(default_factory=dict)
    last_exit_at_by_symbol: dict[str, str] = field(default_factory=dict)
    active_trades: dict[str, dict[str, Any]] = field(default_factory=dict)
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    def mark_signal_processed(self, day_key: str, signal_key: str) -> None:
        signals = self.processed_signal_keys.setdefault(day_key, [])
        if signal_key not in signals:
            signals.append(signal_key)

    def is_signal_processed(self, day_key: str, signal_key: str) -> bool:
        return signal_key in self.processed_signal_keys.get(day_key, [])

    def increment_trade_count(self, day_key: str) -> None:
        self.daily_trade_count[day_key] = self.daily_trade_count.get(day_key, 0) + 1

    def add_realized_pnl(self, day_key: str, pnl: float) -> None:
        self.daily_realized_pnl[day_key] = self.daily_realized_pnl.get(day_key, 0.0) + pnl

    def get_last_processed_bar(self, symbol: str) -> str | None:
        return self.last_processed_bars.get(symbol.upper())

    def set_last_processed_bar(self, symbol: str, value: str) -> None:
        self.last_processed_bars[symbol.upper()] = value

    def get_last_exit_at(self, symbol: str) -> str | None:
        return self.last_exit_at_by_symbol.get(symbol.upper())

    def set_last_exit_at(self, symbol: str, value: str) -> None:
        self.last_exit_at_by_symbol[symbol.upper()] = value

    def get_active_trade(self, symbol: str) -> dict[str, Any] | None:
        return self.active_trades.get(symbol.upper())

    def set_active_trade(self, trade_payload: dict[str, Any]) -> None:
        symbol = str(trade_payload.get("symbol") or "").upper()
        if not symbol:
            raise RuntimeError("Active trade payload is missing a symbol.")
        self.active_trades[symbol] = trade_payload

    def clear_active_trade(self, symbol: str) -> None:
        self.active_trades.pop(symbol.upper(), None)

    def iter_active_trades(self) -> list[dict[str, Any]]:
        return [self.active_trades[symbol] for symbol in sorted(self.active_trades)]

    @property
    def active_trade(self) -> dict[str, Any] | None:
        return self.get_active_trade(self.symbol)

    @active_trade.setter
    def active_trade(self, value: dict[str, Any] | None) -> None:
        if value is None:
            self.clear_active_trade(self.symbol)
            return
        payload = dict(value)
        payload.setdefault("symbol", self.symbol)
        self.set_active_trade(payload)

    @property
    def last_exit_at(self) -> str | None:
        return self.get_last_exit_at(self.symbol)

    @last_exit_at.setter
    def last_exit_at(self, value: str | None) -> None:
        if value is None:
            self.last_exit_at_by_symbol.pop(self.symbol, None)
            return
        self.set_last_exit_at(self.symbol, value)

    @property
    def last_processed_bar(self) -> str | None:
        return self.get_last_processed_bar(self.symbol)

    @last_processed_bar.setter
    def last_processed_bar(self, value: str | None) -> None:
        if value is None:
            self.last_processed_bars.pop(self.symbol, None)
            return
        self.set_last_processed_bar(self.symbol, value)

    def prune(self, keep_days: int = 10) -> None:
        ordered_days = sorted(self.daily_trade_count.keys())
        if len(ordered_days) <= keep_days:
            return
        stale_days = set(ordered_days[:-keep_days])
        self.daily_trade_count = {k: v for k, v in self.daily_trade_count.items() if k not in stale_days}
        self.daily_realized_pnl = {k: v for k, v in self.daily_realized_pnl.items() if k not in stale_days}
        self.processed_signal_keys = {
            k: v for k, v in self.processed_signal_keys.items() if k not in stale_days
        }


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self, *, symbol: str, strategies: list[str]) -> RunnerState:
        if not self.path.exists():
            return RunnerState(symbol=symbol.upper(), strategies=list(strategies))

        try:
            payload = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"State file {self.path} is not valid JSON.") from exc
        state = RunnerState(**_migrate_payload(payload, symbol=symbol.upper(), strategies=list(strategies)))
        if state.symbol != symbol.upper():
            raise RuntimeError(f"State file belongs to {state.symbol}, not {symbol.upper()}.")
        if state.strategies != list(strategies):
            raise RuntimeError(
                f"State file strategies {state.strategies} do not match requested {list(strategies)}."
            )
        return state

    def save(self, state: RunnerState) -> None:
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))
        temp_path.replace(self.path)


def _migrate_payload(payload: dict[str, Any], *, symbol: str, strategies: list[str]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated.setdefault("version", 2)
    legacy_active_trade = migrated.pop("active_trade", None)
    legacy_last_exit_at = migrated.pop("last_exit_at", None)
    legacy_last_processed_bar = migrated.pop("last_processed_bar", None)

    migrated.setdefault("last_processed_bars", {})
    if legacy_last_processed_bar and symbol not in migrated["last_processed_bars"]:
        migrated["last_processed_bars"][symbol] = legacy_last_processed_bar

    migrated.setdefault("last_exit_at_by_symbol", {})
    if legacy_last_exit_at and symbol not in migrated["last_exit_at_by_symbol"]:
        migrated["last_exit_at_by_symbol"][symbol] = legacy_last_exit_at

    migrated.setdefault("active_trades", {})
    if legacy_active_trade is not None:
        legacy_symbol = str(legacy_active_trade.get("symbol") or symbol).upper()
        migrated["active_trades"].setdefault(legacy_symbol, legacy_active_trade)

    migrated.setdefault("symbol", symbol)
    migrated.setdefault("strategies", list(strategies))
    migrated.setdefault("processed_signal_keys", {})
    migrated.setdefault("daily_realized_pnl", {})
    migrated.setdefault("daily_trade_count", {})
    migrated.setdefault("trade_log", [])
    return migrated
