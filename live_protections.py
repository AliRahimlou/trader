from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from live_scheduler import session_key, to_et_timestamp


@dataclass(frozen=True)
class ProtectionDecision:
    allowed: bool
    reasons: tuple[str, ...]
    locks: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "locks": [dict(lock) for lock in self.locks],
        }


@dataclass(frozen=True)
class ProtectionSettings:
    loss_lookback_trades: int
    loss_limit: int
    loss_lock_minutes: int
    max_intraday_drawdown: float
    symbol_loss_limit: int
    symbol_lock_minutes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_trade_protections(
    *,
    symbol: str,
    trade_log: list[dict[str, Any]],
    now: pd.Timestamp,
    settings: ProtectionSettings,
) -> ProtectionDecision:
    """Freqtrade-inspired paper-trading protections.

    These guards are deliberately simple and explainable: they only use closed trade
    history already known to the bot and never alter stops, targets, or live orders.
    """

    normalized_symbol = symbol.upper()
    now_et = to_et_timestamp(now)
    closed_trades = _closed_trades_before_now(trade_log, now_et)
    locks: list[dict[str, Any]] = []

    global_loss_lock = _global_loss_guard(closed_trades, now_et, settings)
    if global_loss_lock is not None:
        locks.append(global_loss_lock)

    symbol_loss_lock = _symbol_loss_guard(normalized_symbol, closed_trades, now_et, settings)
    if symbol_loss_lock is not None:
        locks.append(symbol_loss_lock)

    drawdown_lock = _intraday_drawdown_guard(closed_trades, now_et, settings)
    if drawdown_lock is not None:
        locks.append(drawdown_lock)

    reasons = tuple(dict.fromkeys(str(lock["reason"]) for lock in locks))
    return ProtectionDecision(
        allowed=not reasons,
        reasons=reasons,
        locks=tuple(locks),
    )


def summarize_trade_protections(
    *,
    symbols: list[str],
    trade_log: list[dict[str, Any]],
    now: pd.Timestamp,
    settings: ProtectionSettings,
) -> dict[str, Any]:
    decisions = [
        evaluate_trade_protections(
            symbol=symbol,
            trade_log=trade_log,
            now=now,
            settings=settings,
        )
        for symbol in sorted({item.upper() for item in symbols if item})
    ]
    locks_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for decision in decisions:
        for lock in decision.locks:
            key = (
                str(lock.get("scope") or ""),
                str(lock.get("symbol") or ""),
                str(lock.get("reason") or ""),
            )
            locks_by_key[key] = lock

    locks = sorted(
        locks_by_key.values(),
        key=lambda item: (str(item.get("scope") or ""), str(item.get("symbol") or ""), str(item.get("reason") or "")),
    )
    reasons = sorted({str(lock.get("reason")) for lock in locks})
    return {
        "active": bool(locks),
        "reasons": reasons,
        "locks": locks,
        "settings": settings.to_dict(),
    }


def _closed_trades_before_now(trade_log: list[dict[str, Any]], now_et: pd.Timestamp) -> list[dict[str, Any]]:
    closed: list[dict[str, Any]] = []
    for trade in trade_log:
        closed_at_raw = trade.get("closed_at")
        if not closed_at_raw:
            continue
        try:
            closed_at = to_et_timestamp(closed_at_raw)
        except Exception:
            continue
        if closed_at <= now_et:
            closed.append({**trade, "_closed_at_et": closed_at})
    return sorted(closed, key=lambda item: item["_closed_at_et"])


def _global_loss_guard(
    closed_trades: list[dict[str, Any]],
    now_et: pd.Timestamp,
    settings: ProtectionSettings,
) -> dict[str, Any] | None:
    if settings.loss_limit <= 0 or settings.loss_lock_minutes <= 0:
        return None
    lookback = max(settings.loss_lookback_trades, settings.loss_limit)
    recent = closed_trades[-lookback:]
    losses = [trade for trade in recent if float(trade.get("net_pnl") or 0.0) < 0.0]
    if len(losses) < settings.loss_limit:
        return None
    last_loss = losses[-1]
    unlock_at = last_loss["_closed_at_et"] + pd.Timedelta(minutes=settings.loss_lock_minutes)
    if now_et >= unlock_at:
        return None
    return {
        "scope": "global",
        "reason": "loss_guard_active",
        "message": (
            f"{len(losses)} of the last {len(recent)} closed trades lost money. "
            "New entries are locked temporarily."
        ),
        "losses": len(losses),
        "lookback_trades": len(recent),
        "unlock_at": unlock_at.isoformat(),
    }


def _symbol_loss_guard(
    symbol: str,
    closed_trades: list[dict[str, Any]],
    now_et: pd.Timestamp,
    settings: ProtectionSettings,
) -> dict[str, Any] | None:
    if settings.symbol_loss_limit <= 0 or settings.symbol_lock_minutes <= 0:
        return None
    symbol_trades = [trade for trade in closed_trades if str(trade.get("symbol") or "").upper() == symbol]
    lookback = max(settings.loss_lookback_trades, settings.symbol_loss_limit)
    recent = symbol_trades[-lookback:]
    losses = [trade for trade in recent if float(trade.get("net_pnl") or 0.0) < 0.0]
    if len(losses) < settings.symbol_loss_limit:
        return None
    last_loss = losses[-1]
    unlock_at = last_loss["_closed_at_et"] + pd.Timedelta(minutes=settings.symbol_lock_minutes)
    if now_et >= unlock_at:
        return None
    return {
        "scope": "symbol",
        "symbol": symbol,
        "reason": "symbol_loss_guard_active",
        "message": (
            f"{symbol} has {len(losses)} recent losing paper trades. "
            "This symbol is locked temporarily."
        ),
        "losses": len(losses),
        "lookback_trades": len(recent),
        "unlock_at": unlock_at.isoformat(),
    }


def _intraday_drawdown_guard(
    closed_trades: list[dict[str, Any]],
    now_et: pd.Timestamp,
    settings: ProtectionSettings,
) -> dict[str, Any] | None:
    if settings.max_intraday_drawdown <= 0:
        return None
    day_key = session_key(now_et)
    same_day = [trade for trade in closed_trades if session_key(trade["_closed_at_et"]) == day_key]
    if not same_day:
        return None

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in same_day:
        cumulative += float(trade.get("net_pnl") or 0.0)
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    if max_drawdown < abs(settings.max_intraday_drawdown):
        return None

    next_session = (now_et.normalize() + pd.Timedelta(days=1)) + pd.Timedelta(hours=9, minutes=30)
    return {
        "scope": "global",
        "reason": "intraday_drawdown_guard_active",
        "message": (
            f"Closed-trade drawdown from today's paper PnL peak is ${max_drawdown:.2f}. "
            "New entries are locked until the next session."
        ),
        "drawdown": round(max_drawdown, 2),
        "limit": abs(settings.max_intraday_drawdown),
        "unlock_at": next_session.isoformat(),
    }
