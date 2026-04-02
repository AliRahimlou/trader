from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunnerState:
    symbol: str
    strategies: list[str]
    version: int = 1
    last_processed_bar: str | None = None
    processed_signal_keys: dict[str, list[str]] = field(default_factory=dict)
    daily_realized_pnl: dict[str, float] = field(default_factory=dict)
    daily_trade_count: dict[str, int] = field(default_factory=dict)
    last_exit_at: str | None = None
    active_trade: dict[str, Any] | None = None
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
        state = RunnerState(**payload)
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
