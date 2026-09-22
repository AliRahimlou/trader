"""Readiness and account-scope regressions use an in-memory broker only."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from pivot.deployment import EntryGate
from pivot.execution import Executor
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, disable, enable, ready


ACCOUNT = 'a' * 64
OTHER_ACCOUNT = 'b' * 64


@pytest.fixture
def runtime(tmp_path):
    broker = FakeBroker()
    broker.account_data['account_ref'] = ACCOUNT
    store = Store(tmp_path / 'readiness.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def snapshot(broker, *, opened=True, vix='current'):
    state = ready(broker.at)
    state.update(account=broker.account(), account_at=broker.at.isoformat(), account_error=None,
                 clock={**broker.clock(), 'is_open': opened})
    state['feeds']['vix'] = vix
    return state


def test_closed_session_precedes_unavailable_vix_without_broker_reads(runtime, monkeypatch):
    executor, broker, store = runtime
    state = snapshot(broker, opened=False, vix='unavailable')
    before = deepcopy(store.entry_authorization())
    for name in ('account', 'positions', 'orders', 'clock', 'quote', 'asset'):
        monkeypatch.setattr(broker, name, lambda *args: pytest.fail('Closed entry wait needs no broker reads'))
    executor.tick(state)
    check = store.latest_execution_check()
    assert (check['gate'], check['outcome'], check['regular_session_open']) == ('market_session', 'waiting', False)
    assert check['account_ref'] == ACCOUNT and check['trade'] is None
    assert 'regular market session' in executor.message
    assert not broker.sent and not broker.canceled and store.active_trade() is None
    assert store.entry_authorization() == before


@pytest.mark.parametrize('clock', [
    None, {}, {'is_open': False}, {'is_open': False, 'timestamp': 'invalid'},
    {'is_open': False, 'timestamp': NOW.replace(tzinfo=None).isoformat()},
    {'is_open': False, 'timestamp': (NOW - timedelta(seconds=16)).isoformat()},
    {'is_open': False, 'timestamp': (NOW + timedelta(seconds=6)).isoformat()},  # Beyond the 5 s skew allowance.
    {'is_open': 0, 'timestamp': NOW.isoformat()},
    {'is_open': 'false', 'timestamp': NOW.isoformat()},
    {'is_open': None, 'timestamp': NOW.isoformat()},
])
def test_unknown_or_stale_clock_preserves_existing_vix_wait(runtime, clock):
    executor, broker, store = runtime
    state = snapshot(broker, vix='unavailable')
    state['clock'] = clock
    executor.tick(state)
    check = store.latest_execution_check()
    assert check['gate'] == 'vix_candles' and check['regular_session_open'] is None
    assert not broker.sent and not broker.canceled


def test_open_session_still_requires_vix(runtime):
    executor, broker, store = runtime
    executor.tick(snapshot(broker, vix='unavailable'))
    check = store.latest_execution_check()
    assert check['gate'] == 'vix_candles' and check['regular_session_open'] is True
    assert check['account_ref'] == ACCOUNT and not broker.sent


@pytest.mark.parametrize('permission', ['disabled', 'deployment_hold'])
def test_closed_session_does_not_hide_disabled_permission_or_deployment(runtime, tmp_path, permission):
    executor, broker, store = runtime
    if permission == 'disabled':
        disable(executor)
    else:
        executor.entry_gate = EntryGate(tmp_path / 'entry.lock')
        # An invalid hold also blocks new entries until deployment resolves it.
        executor.entry_gate.hold_path.write_text('{}')
    before = deepcopy(store.entry_authorization())
    executor.tick(snapshot(broker, opened=False, vix='unavailable'))
    check = store.latest_execution_check()
    assert check['gate'] == ('live_permission' if permission == 'disabled' else 'deployment_hold')
    assert check['outcome'] == ('disabled' if permission == 'disabled' else 'waiting')
    assert check['account_ref'] == ACCOUNT
    assert store.entry_authorization() == before and not broker.sent


@pytest.mark.parametrize('change', [
    {'account_at': None}, {'account_at': 'invalid'},
    {'account_at': (NOW - timedelta(seconds=31)).isoformat()},
    {'account_at': (NOW + timedelta(seconds=1)).isoformat()},
    {'account_error': 'last confirmed snapshot'}, {'account': None},
    {'account': {'account_ref': ACCOUNT, 'mode': 'paper'}},
    {'account': {'account_ref': 'raw-account-secret', 'mode': 'live'}},
    {'account': {'account_ref': ['secret'], 'mode': 'live'}},
])
def test_preentry_checks_do_not_attribute_stale_missing_or_unsafe_account(runtime, change):
    executor, broker, store = runtime
    state = snapshot(broker, vix='unavailable')
    state.update(change)
    executor.tick(state)
    check = store.latest_execution_check()
    assert check['account_ref'] is None and check['gate'] == 'vix_candles'
    assert 'secret' not in json.dumps(check) and not broker.sent


def test_preentry_account_switch_keeps_separate_diagnostic_records(runtime):
    executor, broker, store = runtime
    executor.tick(snapshot(broker, vix='unavailable'))
    first = store.latest_execution_check()
    broker.account_data['account_ref'] = OTHER_ACCOUNT
    executor.tick(snapshot(broker, vix='unavailable'))
    second = store.latest_execution_check()
    assert first['account_ref'] == ACCOUNT and second['account_ref'] == OTHER_ACCOUNT
    assert first['id'] != second['id'] and len(store.execution_history()['entries']) == 2
    assert store.control()['account_ref'] == ACCOUNT and not broker.sent


def test_fresh_direct_account_overrides_snapshot_before_identity_block(runtime):
    executor, broker, store = runtime
    state = snapshot(broker)
    broker.account_data['account_ref'] = OTHER_ACCOUNT
    executor.tick(state)
    check = store.latest_execution_check()
    assert check['gate'] == 'account_identity' and check['account_ref'] == OTHER_ACCOUNT
    assert not broker.sent and store.active_trade() is None


def test_direct_unsafe_identity_does_not_fall_back_to_old_snapshot(runtime):
    executor, broker, store = runtime
    state = snapshot(broker)
    broker.account_data['account_ref'] = 'raw-account-secret'
    executor.tick(state)
    check = store.latest_execution_check()
    assert check['gate'] == 'account_identity' and check['account_ref'] is None
    assert 'secret' not in json.dumps(check) and not broker.sent


def test_direct_broker_identity_is_available_without_service_snapshot(runtime):
    executor, broker, store = runtime
    executor.tick(ready())
    check = store.latest_execution_check()
    assert check['account_ref'] == ACCOUNT and check['regular_session_open'] is True
    assert check['trade']['id'] == store.active_trade()['id']
    assert store.active_trade()['stage'] == 'open'


def test_restart_does_not_reuse_prior_tick_account_identity(runtime):
    executor, broker, store = runtime
    executor.tick(snapshot(broker, vix='unavailable'))
    restored = Executor(broker, Store(store.path), now=lambda: broker.at)
    state = ready()
    state['feeds']['vix'] = 'unavailable'
    restored.tick(state)
    assert store.latest_execution_check()['account_ref'] is None
    assert not broker.sent


def test_existing_trade_keeps_owner_identity_after_broker_switch(runtime):
    executor, broker, store = runtime
    executor.tick(snapshot(broker))
    broker.account_data['account_ref'] = OTHER_ACCOUNT
    executor.tick(snapshot(broker, opened=False, vix='unavailable'))
    check = store.latest_execution_check()
    assert check['account_ref'] == ACCOUNT and check['trade']['id'] == store.active_trade()['id']
    assert 'different Alpaca connection' in executor.message
    assert len(broker.sent) == 2 and not broker.canceled


def test_closed_snapshot_cannot_block_management_or_protective_exit(runtime):
    executor, broker, store = runtime
    executor.tick(snapshot(broker))
    trade = store.active_trade()
    stop_id = trade['ops']['stop']['payload']['client_order_id']
    broker.book[stop_id]['status'] = 'rejected'
    state = snapshot(broker, opened=False, vix='unavailable')
    executor.tick(state)
    check = store.latest_execution_check()
    assert check['regular_session_open'] is True  # Management's current broker read wins.
    assert store.active_trade()['protection_failure'] and not executor.enabled()
    assert len(broker.sent) == 3 and '-exit' in broker.sent[-1]['client_order_id']
    assert broker.position_data == []


def test_closed_session_continues_existing_protective_order_reconciliation(runtime):
    executor, broker, store = runtime
    executor.tick(snapshot(broker))
    stop_id = store.active_trade()['ops']['stop']['payload']['client_order_id']
    broker.open = False
    broker.book[stop_id]['status'] = 'partially_filled'
    executor.tick(snapshot(broker, opened=False, vix='unavailable'))
    check = store.latest_execution_check()
    assert check['regular_session_open'] is False and check['trade']
    assert check['trade']['operation_states']['stop']['broker_status'] == 'partially_filled'
    assert 'Position still open outside' in executor.message
    assert len(broker.sent) == 2 and not broker.canceled
