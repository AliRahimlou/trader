from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pivot.models import Bar
from research.execution import ExecutionConfig, TradeSignal, run_execution
from research.metrics import summarize, cash_benchmark


def make_run(days=5):
    tz = ZoneInfo("America/New_York")
    first = datetime(2026, 8, 1, 10, tzinfo=tz)
    bars, signals = [], []
    for n in range(days):
        at = first + timedelta(days=n)
        signals.append(TradeSignal(f"s{n}", at, "long", 100, 90, 120))
        # A complete synthetic path is required for an uncensored interval.
        for minutes in range(15, 346, 15):
            bars.append(Bar(at + timedelta(minutes=minutes), 15, 100, 101, 99, 100))
        end = at.replace(hour=16)
        price = 102 if n % 2 == 0 else 99
        bars.append(Bar(end, 15, price, price + 1, price - 1, price))
    return run_execution(bars, signals, ExecutionConfig(spread_bps=0, slippage_bps=0, fee_per_order=0))


def test_metrics_actual_cash_expectancy_and_outlier_dependency():
    result = summarize(make_run())
    assert result["trade_count"] == 5
    assert result["independent_setup_count"] == 5
    assert result["realized_net_profit"] == pytest.approx(1)
    assert result["net_profit"] == pytest.approx(1)
    assert result["expectancy_per_trade"] == pytest.approx(.2)
    assert result["win_rate"] == .6
    assert result["return_on_starting_capital"] == pytest.approx(1 / 92.05)
    assert result["realized_net_without_best_trade"] == pytest.approx(.5)
    assert result["realized_net_without_best_day"] == pytest.approx(.5)
    assert result["max_losing_streak"] == 1
    assert result["max_drawdown_dollars"] == pytest.approx(.25)
    assert result["worst_day"] == pytest.approx(-.25)


def test_small_sample_never_gets_confidence_interval():
    result = summarize(make_run(10))
    assert not result["uncertainty"]["available"]
    assert "interval" not in result["uncertainty"]
    assert "Only 10" in result["uncertainty"]["reason"]


def test_bootstrap_is_reproducible_and_clustered_by_day():
    run = make_run(25)
    first = summarize(run, seed=123, bootstrap_samples=300)
    second = summarize(run, seed=123, bootstrap_samples=300)
    assert first == second
    assert first["uncertainty"]["available"]
    assert first["uncertainty"]["net_pnl_interval"][0] < first["uncertainty"]["net_pnl_interval"][1]
    assert first["uncertainty"]["expectancy_per_trade_interval"][0] == pytest.approx(first["uncertainty"]["net_pnl_interval"][0] / 25)


def test_bonferroni_confidence_supported_and_widens_interval():
    run = make_run(25)
    usual = summarize(run)["uncertainty"]["net_pnl_interval"]
    family = summarize(run, confidence_level=1 - .05 / 7)["uncertainty"]["net_pnl_interval"]
    assert family[0] <= usual[0]
    assert family[1] >= usual[1]


def test_idle_days_included_but_zero_trades_not_edge():
    run = make_run(25)
    run = run_execution([Bar(datetime(2026, 8, n + 1, 16, tzinfo=ZoneInfo("America/New_York")),
                             15, 100, 101, 99, 100) for n in range(25)], [])
    metrics = summarize(run)
    assert metrics["observed_sessions"] == 25
    assert metrics["expectancy_per_trade"] is None
    assert not metrics["uncertainty"]["available"]
    assert metrics["win_rate"] is None


def test_unresolved_exposure_invalidates_confidence():
    run = make_run(25)
    run["daily_equity"][0]["unresolved_exposure"] = True
    assert not summarize(run)["uncertainty"]["available"]


def test_missing_held_price_path_invalidates_otherwise_large_sample():
    run = make_run(25)
    run['summary']['price_path_complete'] = False
    result = summarize(run)
    assert result['uncertainty']['available'] is False
    assert 'Missing intraday prices' in result['uncertainty']['reason']


def test_costs_reconciled():
    run = make_run()
    # All price frictions explicitly zero in this fixture.
    assert summarize(run)["closed_trade_spread_slippage_cost"] == 0
    assert summarize(run)["closed_trade_fees"] == 0
    assert cash_benchmark(92.05, 5)["net_profit"] == 0
