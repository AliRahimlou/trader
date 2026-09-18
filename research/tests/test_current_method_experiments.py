"""Synthetic fixtures verify mechanics, never claim measured market returns."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pivot.feeds import ET
from pivot.models import Bar
from research.current_method_experiments import (
    capabilities, chronological_split, executable_observations, proposed_readiness, signal_expiry, stock_deadline)
from research.execution import ExecutionConfig, run_execution


PLAN = json.loads((Path(__file__).parents[1] / 'current-method-plan.json').read_text())


def at(hour, minute, second=0, day=18):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=ET)


def bar(end, minutes=15, price=100, low=99, high=101):
    return Bar(end, minutes, price, high, low, price)


def observation(observed=None, until=None, **changes):
    return {'event_id': 'fixture-event', 'at': (observed or at(10, 0)).isoformat(),
            'valid_until': (until or at(10, 2)).isoformat(), 'direction': 'long',
            'entry': 100., 'stop': 95., 'target': 110., **changes}


def test_split_is_chronological_disjoint_nonempty_and_not_random():
    split = chronological_split(['2026-09-16', '2026-09-09', '2026-09-10', '2026-09-11', '2026-09-14'])
    assert split == {'development': ['2026-09-09', '2026-09-10', '2026-09-11'],
                     'validation_known_history': ['2026-09-14', '2026-09-16']}
    for days, fraction in [([], .6), (['2026-09-18'], .6), (['2026-09-18'] * 2, .6),
                            (['2026-09-17', '2026-09-18'], 1.)]:
        with pytest.raises(ValueError):
            chronological_split(days, fraction)


def test_saved_fifteen_minute_vix_cannot_enable_native_five_minute_candidates():
    inputs = SimpleNamespace(stocks5={'AAPL': [bar(at(10, 0), 5)]},
                             native_vix5=[bar(at(10, 0), 15)],
                             native_vix5_provenance={'source_symbol': 'CBOE:VIX',
                                'native_resolution_minutes': 5, 'zero_delay_metadata_verified_at_receipt': True})
    available = capabilities(inputs)
    assert available['native_qqq_5m'] is False
    assert available['native_actual_vix_5m'] is False
    for state in proposed_readiness(PLAN, available).values():
        assert state['ready'] is False
        assert 'missing:native_actual_vix_5m' in state['reasons']
        assert 'missing:native_qqq_5m' in state['reasons']


def test_native_five_minute_proxy_or_unverified_entitlement_does_not_qualify():
    inputs = SimpleNamespace(stocks5={'QQQ': [bar(at(10, 0), 5)]},
                             native_vix5=[bar(at(10, 0), 5)],
                             native_vix5_provenance={'source_symbol': 'VXX',
                                 'native_resolution_minutes': 5, 'zero_delay_metadata_verified_at_receipt': True})
    assert capabilities(inputs)['native_actual_vix_5m'] is False
    inputs.native_vix5_provenance['source_symbol'] = 'CBOE:VIX'
    inputs.native_vix5_provenance['zero_delay_metadata_verified_at_receipt'] = False
    assert capabilities(inputs)['native_actual_vix_5m'] is False


def test_data_availability_does_not_claim_unimplemented_candidate_is_ready():
    available = {'native_qqq_5m': True, 'native_actual_vix_5m': True}
    incomplete = deepcopy(PLAN)
    for variant in incomplete['proposed_variants']:
        variant['implementation'] = 'specified_not_implemented'
    assert all(x['ready'] is False for x in proposed_readiness(incomplete, available).values())
    assert all(x['reasons'] == ['isolated_candidate_implementation_pending']
               for x in proposed_readiness(incomplete, available).values())
    assert all(x['status'] == 'ready_for_offline_signal_evaluation'
               for x in proposed_readiness(PLAN, available).values())


def test_earliest_evidence_deadline_limits_signal_not_new_receipt():
    candidate = {'event_expires_at': at(12, 0).isoformat(),
                 'leader_evidence_valid_until': at(10, 15).isoformat(),
                 'leader_observation_valid_until': at(10, 6, 30).isoformat()}
    assert signal_expiry(candidate, at(10, 2).isoformat()) == at(10, 2)
    assert signal_expiry(candidate, at(10, 20).isoformat()) == at(10, 6, 30)
    with pytest.raises((TypeError, ValueError)):
        signal_expiry({**candidate, 'event_expires_at': None}, at(10, 2).isoformat())


def test_deadline_uses_same_scoped_calendar_as_candle_validation(monkeypatch):
    import research.current_method_experiments as module
    scoped, markets = {'2026-09-18': 'scoped'}, {'QQQ': 'fixture'}
    inputs = SimpleNamespace(sessions={'future_and_extra_history': 'must_not_use'})
    monkeypatch.setattr(module, 'at_time', lambda *args: (markets, None, scoped, None, None))
    def health(actual_markets, actual_sessions, now):
        assert actual_markets is markets and actual_sessions is scoped
        return {'status': 'current', 'valid_until': at(10, 2).isoformat()}
    engine = SimpleNamespace(data_health=SimpleNamespace(stock_health=health))
    assert stock_deadline(inputs, at(10, 0), engine) == at(10, 2).isoformat()


def test_open_from_bar_ending_at_decision_cannot_be_reused():
    bars = [bar(at(10, 0)), bar(at(10, 15)), bar(at(10, 30))]
    # A 10:01 decision cannot fill at the 10:00 open. The next observed open
    # is 10:15, which is already too late; no synthetic 10:01 price exists.
    signals, rejected = executable_observations([observation(at(10, 1), at(10, 2, 30))], bars)
    assert signals == []
    assert rejected[0]['reason'] == 'available_open_after_signal_expiry'
    assert rejected[0]['next_available_open'] == at(10, 15).isoformat()


def test_deadline_equality_is_expired_and_latency_is_respected():
    bars = [bar(at(10, 15)), bar(at(10, 30))]
    row = observation(at(9, 59), at(10, 0))
    assert executable_observations([row], bars)[1][0]['reason'] == 'available_open_after_signal_expiry'
    row = observation(at(9, 59, 30), at(10, 2))
    assert len(executable_observations([row], bars)[0]) == 1
    assert executable_observations([row], bars, 60)[0] == []


def test_no_same_day_price_is_not_silently_filled_next_session():
    row = observation(at(15, 59), at(16, 2))
    signals, rejected = executable_observations([row], [bar(at(9, 45, day=21))])
    assert signals == []
    assert rejected[0]['reason'] == 'no_same_session_execution_price'


def test_future_ohlc_does_not_change_execution_time_admission():
    row = observation(at(9, 59), at(10, 2))
    normal = [bar(at(10, 15))]
    volatile = [bar(at(10, 15), low=10, high=1000)]
    assert executable_observations([row], normal) == executable_observations([row], volatile)


def test_cost_scenarios_reduce_fixture_returns_and_do_not_resize_to_available_cash():
    bars = [bar(at(10, 15), low=99, high=111), bar(at(16, 0))]
    signals, rejected = executable_observations([observation(at(10, 0), at(10, 2))], bars)
    assert not rejected
    base = run_execution(bars, signals, ExecutionConfig(starting_capital=100, trade_notional=5,
                          **PLAN['execution_scenarios']['base']))
    expensive = run_execution(bars, signals, ExecutionConfig(starting_capital=100, trade_notional=5,
                               **PLAN['execution_scenarios']['higher_cost']))
    assert len(base['trades']) == len(expensive['trades']) == 1
    assert expensive['trades'][0]['net_pnl'] < base['trades'][0]['net_pnl']
    assert expensive['trades'][0]['fees'] > base['trades'][0]['fees']
    insufficient = run_execution(bars, signals, ExecutionConfig(starting_capital=4, trade_notional=5))
    assert not insufficient['trades']
    assert insufficient['rejections'][0]['reason'] == 'insufficient_buying_power'


def test_same_bar_both_boundaries_remains_stop_first_and_duplicates_do_not_reenter():
    bars = [bar(at(10, 15), low=90, high=111), bar(at(10, 30)), bar(at(16, 0))]
    first = observation(at(10, 0), at(10, 2))
    second = observation(at(10, 15), at(10, 17))
    signals, _ = executable_observations([first, second], bars)
    result = run_execution(bars, signals, ExecutionConfig(starting_capital=100, trade_notional=5))
    assert len(result['trades']) == 1
    assert result['trades'][0]['reason'] == 'stop'
    assert result['trades'][0]['ohlc_ambiguous'] is True
    assert result['rejections'][0]['reason'] == 'duplicate_setup'


def test_phase_simulations_cannot_keep_position_across_holdout_boundary():
    # A complete development session has an observed close-spanning bar;
    # each partition independently starts with the declared research budget.
    bars = [bar(at(10, 15)), bar(at(16, 0))]
    signals, _ = executable_observations([observation(at(10, 0), at(10, 2))], bars)
    cfg = ExecutionConfig(starting_capital=100, trade_notional=5)
    development = run_execution(bars, signals, cfg)
    validation = run_execution([bar(at(10, 15, day=21)), bar(at(16, 0, day=21))], [], cfg)
    assert development['open_position'] is None
    assert development['trades'][0]['reason'] == 'scheduled_session_close'
    assert validation['summary']['ending_equity'] == 100
    assert validation['open_position'] is None
