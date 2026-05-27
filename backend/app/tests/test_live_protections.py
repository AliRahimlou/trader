import pandas as pd

from live_protections import ProtectionSettings, evaluate_trade_protections


def _settings(**overrides):
    values = {
        "loss_lookback_trades": 6,
        "loss_limit": 3,
        "loss_lock_minutes": 30,
        "max_intraday_drawdown": 150.0,
        "symbol_loss_limit": 2,
        "symbol_lock_minutes": 60,
    }
    values.update(overrides)
    return ProtectionSettings(**values)


def _trade(symbol, minutes_ago, pnl, now):
    return {
        "symbol": symbol,
        "closed_at": (now - pd.Timedelta(minutes=minutes_ago)).isoformat(),
        "net_pnl": pnl,
    }


def test_allows_when_no_protections_trigger():
    now = pd.Timestamp("2026-05-27 10:00", tz="America/New_York")

    decision = evaluate_trade_protections(
        symbol="AAPL",
        trade_log=[_trade("AAPL", 10, 25.0, now), _trade("MSFT", 5, -10.0, now)],
        now=now,
        settings=_settings(),
    )

    assert decision.allowed is True
    assert decision.reasons == ()


def test_global_loss_guard_blocks_after_recent_loss_cluster():
    now = pd.Timestamp("2026-05-27 10:00", tz="America/New_York")
    trades = [
        _trade("AAPL", 25, -10.0, now),
        _trade("MSFT", 20, -12.0, now),
        _trade("NVDA", 5, -8.0, now),
    ]

    decision = evaluate_trade_protections(
        symbol="SPY",
        trade_log=trades,
        now=now,
        settings=_settings(),
    )

    assert decision.allowed is False
    assert "loss_guard_active" in decision.reasons
    assert decision.locks[0]["scope"] == "global"


def test_expired_loss_guard_does_not_block():
    now = pd.Timestamp("2026-05-27 10:00", tz="America/New_York")
    trades = [
        _trade("AAPL", 80, -10.0, now),
        _trade("MSFT", 75, -12.0, now),
        _trade("NVDA", 70, -8.0, now),
    ]

    decision = evaluate_trade_protections(
        symbol="SPY",
        trade_log=trades,
        now=now,
        settings=_settings(loss_lock_minutes=30),
    )

    assert decision.allowed is True


def test_symbol_loss_guard_blocks_only_underperforming_symbol():
    now = pd.Timestamp("2026-05-27 10:00", tz="America/New_York")
    trades = [
        _trade("AAPL", 40, -10.0, now),
        _trade("AAPL", 5, -12.0, now),
    ]

    blocked = evaluate_trade_protections(
        symbol="AAPL",
        trade_log=trades,
        now=now,
        settings=_settings(loss_limit=0),
    )
    allowed = evaluate_trade_protections(
        symbol="MSFT",
        trade_log=trades,
        now=now,
        settings=_settings(loss_limit=0),
    )

    assert blocked.allowed is False
    assert "symbol_loss_guard_active" in blocked.reasons
    assert allowed.allowed is True


def test_intraday_drawdown_guard_blocks_after_falling_from_pnl_peak():
    now = pd.Timestamp("2026-05-27 13:00", tz="America/New_York")
    trades = [
        _trade("AAPL", 180, 200.0, now),
        _trade("MSFT", 30, -175.0, now),
    ]

    decision = evaluate_trade_protections(
        symbol="NVDA",
        trade_log=trades,
        now=now,
        settings=_settings(loss_limit=0, symbol_loss_limit=0, max_intraday_drawdown=150.0),
    )

    assert decision.allowed is False
    assert "intraday_drawdown_guard_active" in decision.reasons
