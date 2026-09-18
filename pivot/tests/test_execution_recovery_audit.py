"""Adversarial permission-generation and recovery checks using only FakeBroker."""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from pivot.execution import Executor, MANUAL_FLAT_CONFIRM_SECONDS, PARTIAL_ENTRY_CONFIRM_SECONDS
from pivot.feeds import FeedError
from pivot.policy import POLICY_VERSION
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, disable, enable, ready
from pivot.tests.test_runtime_admission_fixes import prepared_reservation


@pytest.fixture
def runtime(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'recovery.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


@pytest.mark.parametrize('direction,old_amount', [('long', '25.00'), ('short', '500.00')])
def test_lowered_purchase_target_invalidates_inflight_preflight(runtime, direction, old_amount):
    executor, broker, store = runtime
    service = Service(broker, store, broker=broker)
    service.executor = executor
    service.refresh_account()
    store.save({'sizing_mode': 'target', 'target_dollars': old_amount})
    broker.requires_vix_entry_quote = True
    def confirm(now):
        disable(executor)
        assert service.save_settings({'sizing_mode': 'target', 'target_dollars': '5'})['target_dollars'] == '5.00'
        enable(executor)
        return {'source': 'insightsentry', 'symbol': 'I:VIX', 'delay_seconds': 0,
                'value': 20, 'updated_at': broker.at.isoformat()}
    broker.confirm_vix_quote = confirm
    executor.tick(ready(direction=direction))
    assert not broker.sent and store.active_trade() is None
    assert 'changed during entry checks' in executor.message
    assert store.settings()['target_dollars'] == '5.00'
    broker.requires_vix_entry_quote = False
    executor.tick(ready())
    assert broker.sent[0]['notional'] == '5.00'


@pytest.mark.parametrize('change', ['same_settings', 'same_control', 'off_on'])
def test_authorization_generation_detects_identical_values_and_frozen_clock(runtime, monkeypatch, change):
    executor, broker, store = runtime
    import pivot.store as store_module
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(store_module, 'datetime', FrozenDatetime)
    enable(executor)
    before = store.entry_authorization()
    broker.requires_vix_entry_quote = True
    def confirm(now):
        if change == 'same_settings':
            store.save(store.settings())
        else:
            if change == 'off_on':
                disable(executor)
            enable(executor)
        return {'source': 'insightsentry', 'symbol': 'I:VIX', 'delay_seconds': 0,
                'value': 20, 'updated_at': broker.at.isoformat()}
    broker.confirm_vix_quote = confirm
    executor.tick(ready())
    after = store.entry_authorization()
    assert before['settings'] == after['settings'] and before['control'] == after['control']
    assert after['generation'] > before['generation']
    assert not broker.sent and store.active_trade() is None


def test_generation_is_rechecked_atomically_at_reservation(runtime):
    executor, broker, store = runtime
    reserve = store.reserve_trade
    def changed_at_reservation(trade, **kwargs):
        store.save({'sizing_mode': 'target', 'target_dollars': '5.00'})
        return reserve(trade, **kwargs)
    store.reserve_trade = changed_at_reservation
    executor.tick(ready())
    assert not broker.sent and store.active_trade() is None


def test_prepared_generation_change_cannot_claim_or_post_after_restart(runtime):
    executor, broker, store = runtime
    trade = prepared_reservation(runtime)
    store.save(store.settings())
    assert not store.claim_operation(trade['id'], 'entry', expected_entry=trade)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(ready())
    assert not broker.sent and store.active_trade() is None
    assert not store.entry_consumed(trade['id'])  # Never claimed: fresh preflight may retry.
    restarted.tick(ready())
    assert len(broker.sent) == 2


def test_authorization_change_after_claim_is_known_unsent_and_consumed(runtime):
    executor, broker, store = runtime
    claim = store.claim_operation
    def changed_after_claim(*args, **kwargs):
        result = claim(*args, **kwargs)
        if result:
            store.save(store.settings())
        return result
    store.claim_operation = changed_after_claim
    snapshot = ready()
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert store.entry_consumed(executor._event_key(snapshot['setup']))


@pytest.mark.parametrize('change', ['last_seen', 'filled_qty', 'extra_operation', 'stale_copy'])
def test_no_post_abort_cannot_erase_conflicting_durable_evidence(runtime, change):
    executor, broker, store = runtime
    trade = prepared_reservation(runtime)
    assert store.claim_operation(trade['id'], 'entry', expected_entry=trade)
    trade = store.active_trade()
    expected = deepcopy(trade)
    if change == 'last_seen':
        trade['ops']['entry']['last_seen'] = {'status': 'new'}
    elif change == 'filled_qty':
        trade['filled_qty'] = '0'
    elif change == 'extra_operation':
        trade['ops']['stop'] = {'state': 'prepared'}
    else:
        trade['reason'] = 'Different durable worker state'
    store.save_trade(trade)
    if change != 'stale_copy':
        expected = trade
    assert store.abort_claimed_entry(expected, broker.at.isoformat()) is None
    assert store.active_trade() == trade
    assert store.entry_consumed(trade['id'])


@pytest.mark.parametrize('delivery', ['lost_accepted', 'lost_unseen'])
def test_unknown_network_outcome_is_never_reclassified_as_known_no_post(runtime, delivery):
    executor, broker, store = runtime
    broker.entry_mode = delivery
    executor.tick(ready())
    assert len(broker.sent) == 1
    broker.at += timedelta(seconds=60)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(ready(broker.at))
    if delivery == 'lost_accepted':
        assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
        assert store.active_trade()['stage'] == 'open'
    else:
        assert len(broker.sent) == 1 and store.active_trade()['stage'] == 'entering'
        assert store.active_trade()['ops']['entry']['state'] == 'attempted'


def test_entry_only_manual_flatness_resolves_without_inventing_exit_economics(runtime):
    executor, broker, store = runtime
    broker.entry_mode = 'lost_accepted'
    executor.tick(ready())
    broker.position_data = []  # Owner's external close before any app exit intent.
    executor.tick({})
    since = store.active_trade()['flat_reconciliation_started_at']
    broker.at += timedelta(seconds=MANUAL_FLAT_CONFIRM_SECONDS - 1)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert store.active_trade()['stage'] == 'open' and restarted.enabled()
    assert store.active_trade()['flat_reconciliation_started_at'] == since
    broker.at += timedelta(seconds=1)
    restarted.tick({})
    assert store.active_trade()['stage'] == 'attention' and not restarted.enabled()
    restarted.tick({})
    assert store.active_trade() is None and len(broker.sent) == 1
    assert store.trade_results()[0]['status'] == 'unverified'
    assert store.trade_results()[0]['gross_pnl'] is None


def test_temporary_missing_position_recovers_with_protection_without_manual_resolution(runtime):
    executor, broker, store = runtime
    broker.entry_mode = 'lost_accepted'
    executor.tick(ready())
    held = deepcopy(broker.position_data)
    broker.position_data = []
    executor.tick({})
    assert store.active_trade().get('flat_reconciliation_started_at')
    broker.position_data = held
    broker.at += timedelta(seconds=MANUAL_FLAT_CONFIRM_SECONDS + 1)
    executor.tick({})
    assert store.active_trade()['stage'] == 'open' and executor.enabled()
    assert 'flat_reconciliation_started_at' not in store.active_trade()
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'


def test_unknown_entry_remains_uncertain_even_after_long_flatness(runtime):
    executor, broker, store = runtime
    broker.entry_mode = 'lost_unseen'
    executor.tick(ready())
    for _ in range(4):
        broker.at += timedelta(minutes=1)
        executor.tick({})
    assert store.active_trade()['stage'] == 'entering' and len(broker.sent) == 1
    assert 'flat_reconciliation_started_at' not in store.active_trade()
    assert not any(event['kind'] == 'manual_resolution_confirmed' for event in store.events())


def test_partial_fill_incident_survives_restart_and_manages_final_fill_safely(runtime):
    executor, broker, store = runtime
    broker.entry_mode = 'partial'
    cancel = broker.cancel
    broker.cancel = lambda oid: broker.canceled.append(oid)  # Still pending at venue.
    executor.tick(ready())
    first = deepcopy(store.active_trade()['partial_entry'])
    broker.at += timedelta(seconds=PARTIAL_ENTRY_CONFIRM_SECONDS - 1)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert restarted.enabled() and len(broker.sent) == 1
    broker.at += timedelta(seconds=1)
    restarted.tick({})
    assert not restarted.enabled() and len(broker.sent) == 1
    assert store.active_trade()['stage'] == 'entering'
    assert store.active_trade()['partial_entry']['confirmation_deadline'] == first['confirmation_deadline']
    assert 'cancellation is still unconfirmed' in restarted.message
    # Additional partial fill occurs while cancellation is uncertain.
    broker.fill(broker.sent[0]['client_order_id'], '.2')
    restarted.tick({})
    assert len(broker.sent) == 1
    assert store.active_trade()['partial_entry']['filled_qty'] == '0.2'
    assert len([event for event in store.events() if event['kind'] == 'partial_entry_needs_attention']) == 1
    broker.cancel = cancel
    restarted.tick({})  # Confirmed cancellation must be observed on next lookup.
    assert len(broker.sent) == 1
    restarted.tick({})
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert Decimal(broker.sent[1]['qty']) == Decimal('.2')
    assert store.active_trade()['stage'] == 'open' and not restarted.enabled()
    assert store.active_trade()['partial_entry']['resolved_at']
    assert len([event for event in store.events() if event['kind'] == 'partial_entry_reconciled']) == 1


@pytest.mark.parametrize('failed_read', ['account', 'clock', 'cancel'])
def test_partial_fill_incident_deadline_is_enforced_during_connection_failures(runtime, failed_read):
    executor, broker, store = runtime
    broker.entry_mode = 'partial'
    broker.cancel = lambda oid: None
    executor.tick(ready())
    broker.at += timedelta(seconds=PARTIAL_ENTRY_CONFIRM_SECONDS)
    def unavailable(*args):
        raise FeedError('Connection unavailable')
    setattr(broker, failed_read, unavailable)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert not restarted.enabled() and len(broker.sent) == 1
    assert 'cancellation is still unconfirmed' in restarted.message
    assert store.active_trade()['partial_entry']['raised_at']
    restarted.tick({})
    assert len([event for event in store.events() if event['kind'] == 'partial_entry_needs_attention']) == 1
