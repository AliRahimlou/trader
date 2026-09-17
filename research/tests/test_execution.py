from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

import pytest

from pivot.models import Bar
from research.execution import ExecutionConfig, TradeSignal, run_execution


TZ = ZoneInfo("America/New_York")


def dt(clock, day="2026-09-01"):
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=TZ)


def bar(clock, o=100, h=101, l=99, c=100, *, day="2026-09-01", minutes=15):
    return Bar(dt(clock, day), minutes, o, h, l, c)


def signal(at="10:00", *, day="2026-09-01", direction="long", setup="s1", stop=95, target=110):
    return TradeSignal(setup, dt(at, day), direction, 100, stop, target)


def frictionless(**kwargs):
    return ExecutionConfig(spread_bps=0, slippage_bps=0, fee_per_order=0, **kwargs)


def test_entry_is_next_bar_open_never_signal_bar_price():
    result = run_execution([bar("10:00", 80, 101, 79, 100),
                            bar("10:15", 101, 103, 100, 102),
                            bar("16:00", 102, 103, 101, 102)],
                           [signal()], frictionless())
    trade = result["trades"][0]
    assert trade["entry_at"] == dt("10:00").isoformat()
    assert trade["entry_price"] == 101
    assert trade["exit_at"] == dt("15:45").isoformat()
    assert trade["reason"] == "scheduled_session_close"
    json.dumps(result)


def test_latency_requires_next_available_open_not_invented_intrabar_fill():
    result = run_execution([bar("10:15"), bar("10:30", 102, 103, 101, 102), bar("16:00")],
                           [signal()], frictionless(latency_seconds=60, max_entry_drift=None))
    assert result["trades"][0]["entry_at"] == dt("10:15").isoformat()
    assert result["trades"][0]["entry_price"] == 102


def test_stop_first_when_both_levels_touched():
    result = run_execution([bar("10:15", 100, 115, 90, 105)], [signal()], frictionless())
    trade = result["trades"][0]
    assert trade["exit_price"] == 95
    assert trade["reason"] == "stop"
    assert trade["ohlc_ambiguous"]
    assert trade["net_pnl"] < 0


def test_gap_stop_uses_worse_open_not_stale_stop():
    result = run_execution([bar("10:15"), bar("10:30", 90, 92, 89, 91)], [signal()], frictionless())
    assert result["trades"][0]["exit_price"] == 90


def test_short_rejected_and_no_leverage():
    bars = [bar("10:15"), bar("16:00")]
    result = run_execution(bars, [signal(direction="short")], frictionless())
    assert not result["trades"]
    assert result["rejections"][0]["reason"] == "short_or_unknown_direction_disabled"
    result = run_execution(bars, [signal()], frictionless(starting_capital=20))
    assert result["rejections"][0]["reason"] == "insufficient_buying_power"
    assert result["summary"]["ending_cash"] == 20


def test_fees_spread_and_slippage_reduce_flat_price_result():
    bars = [bar("10:15"), bar("16:00")]
    clean = run_execution(bars, [signal()], frictionless())
    costly = run_execution(bars, [signal()], ExecutionConfig())
    assert clean["summary"]["net_pnl"] == 0
    assert costly["summary"]["net_pnl"] < -.02
    t = costly["trades"][0]
    assert t["fees"] == .02
    assert t["entry_price"] > 100 > t["exit_price"]
    assert costly["summary"]["ending_cash"] == pytest.approx(92.05 + t["net_pnl"])


def test_deterministic_partial_fill_and_protection_fault_are_adverse():
    result = run_execution([bar("10:15", 100, 120, 97, 110)], [signal()],
                           frictionless(entry_fill_fraction=.5, protection_failure_every_n=1))
    trade = result["trades"][0]
    assert trade["quantity"] == .125
    assert trade["exit_price"] == 97
    assert trade["net_pnl"] < 0
    assert trade["reason"] == "protection_failure_low_bound"
    assert any(e["kind"] == "unfilled_remainder_canceled" for e in result["events"])


def test_rejected_setup_does_not_retry():
    result = run_execution([bar("10:15"), bar("10:30")],
                           [signal(), signal("10:15")], frictionless(reject_every_n=1))
    assert [r["reason"] for r in result["rejections"]] == ["simulated_broker_rejection", "duplicate_setup"]


def test_one_position_and_no_new_entry_on_same_exit_bar():
    result = run_execution([bar("10:15"), bar("10:30", 100, 111, 99, 100)],
                           [signal(), signal("10:15", setup="s2")], frictionless())
    assert len(result["trades"]) == 1
    assert result["rejections"][0]["reason"] == "one_position_limit"


def test_early_close_calendar_flatten_at_open_of_spanning_bar():
    cfg = frictionless(session_closes={"2026-09-01": dt("13:00")})
    result = run_execution([bar("10:15"), bar("13:00", 102, 110, 90, 95)], [signal()], cfg)
    assert result["trades"][0]["exit_at"] == dt("12:45").isoformat()
    assert result["trades"][0]["exit_price"] == 102


def test_final_bar_one_minute_uses_scheduled_1555_open():
    result = run_execution([bar("10:01", minutes=1), bar("15:56", minutes=1)],
                           [signal()], frictionless())
    assert result["trades"][0]["exit_at"] == dt("15:55").isoformat()


def test_zero_close_buffer_still_flattens_conservatively():
    result = run_execution([bar("10:15"), bar("16:00")], [signal()],
                           frictionless(flatten_minutes_before_close=0))
    assert result["trades"][0]["exit_at"] == dt("15:45").isoformat()
    assert not result["summary"]["unresolved_exposure"]


def test_missing_close_data_explicitly_unresolved():
    result = run_execution([bar("10:15")], [signal()], frictionless())
    assert result["summary"]["unresolved_exposure"]
    assert result["daily_equity"][0]["unresolved_exposure"]
    assert not result["trades"]
    assert result["events"][-1]["kind"] == "unresolved_exposure_at_data_end"


def test_split_adjustment_prevents_false_overnight_loss_in_missing_data_stress():
    bars = [bar("10:15"), bar("09:45", 50, 51, 49, 50, day="2026-09-02")]
    result = run_execution(bars, [signal()], frictionless(split_factors={"2026-09-02": 2}))
    trade = result["trades"][0]
    assert trade["quantity"] == .5
    assert trade["net_pnl"] == 0
    assert trade["reason"] == "missing_close_data_next_open"
    assert result["daily_equity"][0]["unresolved_exposure"]


def test_minimum_size_and_fractional_precision():
    result = run_execution([bar("10:15")], [signal()], frictionless(fractional_precision=0))
    assert result["rejections"][0]["reason"] == "below_minimum_size"


def test_duplicate_or_overlapping_bars_fail():
    with pytest.raises(ValueError, match="overlap"):
        run_execution([bar("10:15"), bar("10:15")], [], frictionless())


def test_price_gap_can_invalidate_signal_before_entry():
    result = run_execution([bar("10:15", 120, 121, 119, 120)], [signal()], frictionless())
    assert result["rejections"][0]["reason"] == "gap_invalidated_risk_levels"


def test_entry_drift_limit_and_after_cost_geometry():
    result = run_execution([bar("10:15", 101, 102, 100, 101)], [signal()],
                           frictionless(max_entry_drift=.005))
    assert result["rejections"][0]["reason"] == "entry_drift_limit"
    result = run_execution([bar("10:15")], [signal(target=100.01)], ExecutionConfig())
    assert result["rejections"][0]["reason"] == "gap_invalidated_risk_levels"


def test_preflight_drift_skip_can_qualify_same_setup_on_later_snapshot():
    bars = [bar("10:15", 102, 103, 101, 102), bar("10:30"), bar("16:00")]
    result = run_execution(bars, [signal(), signal("10:15")], frictionless())
    assert len(result["trades"]) == 1
    assert result["trades"][0]["entry_at"] == dt("10:15").isoformat()
    assert [r["reason"] for r in result["rejections"]] == ["entry_drift_limit"]
    assert result["summary"]["attempted_setup_count"] == 1
    assert len([e for e in result["events"] if e["kind"] == "entry"]) == 1


def test_preflight_skips_do_not_advance_simulated_submission_rejection_cadence():
    bars = [bar("10:15", 102, 103, 101, 102), bar("10:30"), bar("16:00")]
    result = run_execution(bars, [signal(), signal("10:15")], frictionless(reject_every_n=2))
    assert len(result["trades"]) == 1  # First real submission; drift skip was not an order.
    assert result["summary"]["attempted_setup_count"] == 1


def test_future_or_previous_day_signals_do_not_fill():
    result = run_execution([bar("10:15")],
                           [signal(day="2026-08-31"), signal("11:00", setup="future")], frictionless())
    assert not result["trades"]
    assert {r["reason"] for r in result["rejections"]} == {"expired_session", "no_executable_bar_after_signal"}
