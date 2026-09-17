"""Execution evidence uses a fake broker only; no real credentials or requests."""
from copy import deepcopy
from datetime import timedelta
import json
import sqlite3

import pytest

from pivot.execution import Executor, Waiting
from pivot.feeds import FeedError
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready
from pivot.version import APP_VERSION


REVISION = 'a' * 40


@pytest.fixture
def diagnostic_engine(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'execution.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at, revision=REVISION)
    return executor, broker, store


def record(at=NOW, **updates):
    return {'version': 'execution-check-v1', 'captured_at': at.isoformat(),
            'checkpoint_at': at.replace(minute=at.minute // 15 * 15, second=0, microsecond=0).isoformat(),
            'analysis_at': at.isoformat(), 'outcome': 'waiting', 'gate': 'signal_leaders',
            'historical_only': True, 'order_authorized': False, **updates}


def waiting_signal(at=NOW):
    snapshot = ready(at)
    snapshot['setup']['event_at'] = NOW.isoformat()
    snapshot['setup']['state'] = 'CONFIRMING'
    for check in snapshot['setup']['checks']:
        if check['name'] == 'Magnificent Seven at their zones':
            check['passed'] = False
    return snapshot


def test_store_deduplicates_clock_only_updates_and_survives_restart(tmp_path):
    store = Store(tmp_path / 'checks.sqlite3')
    first = store.record_execution_check(record())
    later = NOW + timedelta(seconds=5)
    second = store.record_execution_check(record(later))
    assert second['id'] == first['id'] and second['observation_count'] == 2
    assert second['first_observed_at'] == NOW.isoformat()
    assert second['last_observed_at'] == second['analysis_at'] == later.isoformat()
    reopened = Store(store.path)
    assert reopened.latest_execution_check() == second
    assert len(reopened.execution_history()['entries']) == 1


@pytest.mark.parametrize('change', [
    {'gate': 'price_geometry'}, {'outcome': 'feed_error'}, {'revision': 'b' * 40},
    {'trade': {'id': 'b' * 24}}, {'checkpoint_at': (NOW + timedelta(minutes=15)).isoformat()},
])
def test_material_execution_context_changes_have_distinct_records(tmp_path, change):
    store = Store(tmp_path / 'checks.sqlite3')
    first = store.record_execution_check(record())
    second = store.record_execution_check(record(**change))
    assert first['id'] != second['id']


def test_retention_and_bounded_pagination_do_not_change_controls_or_trade_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr('pivot.store.EXECUTION_CHECK_RETENTION', 3)
    store = Store(tmp_path / 'checks.sqlite3')
    initial = store.control(), store.settings(), store.events()
    ids = [store.record_execution_check(record(NOW + timedelta(minutes=15 * i)))['id'] for i in range(5)]
    first = store.execution_history(2)
    assert [row['id'] for row in first['entries']] == ids[-1:-3:-1]
    second = store.execution_history(2, first['next_before_id'])
    assert [row['id'] for row in second['entries']] == ids[-3:-4:-1]
    assert second['next_before_id'] is None and first['historical_only'] is True
    assert (store.control(), store.settings(), store.events()) == initial
    assert store.active_trade() is None


@pytest.mark.parametrize('limit,cursor', [(0, None), (101, None), (True, None), (1, 0), (1, True), (1, 2**63)])
def test_invalid_history_limits_and_sqlite_overflow_rejected(tmp_path, limit, cursor):
    with pytest.raises(ValueError):
        Store(tmp_path / 'checks.sqlite3').execution_history(limit, cursor)


def test_invalid_or_oversized_record_cannot_enter_history(tmp_path):
    store = Store(tmp_path / 'checks.sqlite3')
    for payload in (record(version='unknown'), record(extra='x' * 16384), record(extra=float('nan'))):
        with pytest.raises(ValueError):
            store.record_execution_check(payload)
    assert store.execution_history()['entries'] == []


def test_busy_ledger_fails_diagnostic_write_promptly_without_mutation(tmp_path):
    store = Store(tmp_path / 'checks.sqlite3')
    with sqlite3.connect(store.path) as lock:
        lock.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError):
            store.record_execution_check(record())
    assert store.execution_history()['entries'] == []


def test_disabled_and_restarted_checks_have_explicit_freshness(diagnostic_engine):
    executor, broker, store = diagnostic_engine
    executor.tick(ready())
    check = executor.snapshot()['execution_check']
    assert check['outcome'] == 'disabled' and check['gate'] == 'live_permission'
    assert check['persisted'] and check['current_at_snapshot'] and check['from_current_process']
    assert check['app_version'] == APP_VERSION and check['revision'] == REVISION
    assert check['historical_only'] and check['order_authorized'] is False
    assert not broker.sent
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at, revision=REVISION)
    restored = restarted.snapshot()['execution_check']
    assert restored['persisted'] and not restored['from_current_process'] and not restored['current_at_snapshot']
    broker.at += timedelta(seconds=31)
    assert not executor.snapshot()['execution_check']['current_at_snapshot']
    assert 'current_at_snapshot' not in store.latest_execution_check()


def test_repeated_signal_waits_deduplicate_across_refreshes_and_restart(diagnostic_engine):
    executor, broker, store = diagnostic_engine
    enable(executor)
    executor.tick(waiting_signal())
    broker.at += timedelta(seconds=5)
    executor.tick(waiting_signal(broker.at))
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at, revision=REVISION)
    broker.at += timedelta(seconds=5)
    restarted.tick(waiting_signal(broker.at))
    rows = store.execution_history()['entries']
    assert len(rows) == 1 and rows[0]['observation_count'] == 3
    assert rows[0]['outcome'] == 'waiting' and rows[0]['gate'] == 'signal_leaders'
    assert rows[0]['signal']['first_failed_gate'] == 'signal_leaders'
    assert rows[0]['signal']['event_at'] == NOW.isoformat()
    assert rows[0]['analysis_at'] == broker.at.isoformat()
    assert not broker.sent


@pytest.mark.parametrize('failure,gate,outcome,kind', [
    (Waiting('secret provider response'), 'asset_eligibility', 'waiting', 'Waiting'),
    (FeedError('secret API token'), 'quote_read', 'feed_error', 'FeedError'),
    (ValueError('secret account object'), 'asset_eligibility', 'invalid_data', 'ValueError'),
    (RuntimeError('secret authorization header'), 'broker_snapshot', 'unexpected_error', 'RuntimeError'),
])
def test_failures_are_sanitized_and_attributed_before_submission(diagnostic_engine, monkeypatch, failure, gate, outcome, kind):
    executor, broker, store = diagnostic_engine
    enable(executor)
    def fail(*args):
        raise failure
    method = {'asset_eligibility': 'asset', 'quote_read': 'quote', 'broker_snapshot': 'account'}[gate]
    monkeypatch.setattr(broker, method, fail)
    snapshot = ready()
    snapshot.update(account={'secret': 'secret account'}, raw_provider={'secret': 'secret response'})
    snapshot['setup']['detail'] = 'secret strategy text'
    executor.tick(snapshot)
    check = store.latest_execution_check()
    assert (check['outcome'], check['gate'], check['exception_kind']) == (outcome, gate, kind)
    assert check['submission_attempted_this_tick'] is False
    assert 'secret' not in json.dumps(store.execution_history())
    assert not broker.sent and store.active_trade() is None


@pytest.mark.parametrize('mode,gate', [('spread', 'quote_spread'), ('stale', 'quote_stale'), ('malformed', 'quote_invalid')])
def test_quote_failure_codes_are_specific_without_recording_provider_body(diagnostic_engine, monkeypatch, mode, gate):
    executor, broker, store = diagnostic_engine
    enable(executor)
    quote = broker.quote('QQQ')
    if mode == 'spread':
        quote['ap'] = '101'
    elif mode == 'stale':
        quote['t'] = (NOW - timedelta(seconds=16)).isoformat()
    else:
        quote['bp'] = 'secret malformed provider value'
    monkeypatch.setattr(broker, 'quote', lambda symbol: quote)
    executor.tick(ready())
    assert store.latest_execution_check()['gate'] == gate
    assert 'secret' not in json.dumps(store.execution_history())
    assert not broker.sent


@pytest.mark.parametrize('failure', [RuntimeError('secret post-submit failure'), FeedError('secret uncertain response')])
def test_post_submission_uncertainty_records_attempt_and_restarts_without_duplicate(diagnostic_engine, monkeypatch, failure):
    executor, broker, store = diagnostic_engine
    enable(executor)
    original_submit = broker.submit
    def submit_then_fail(payload):
        original_submit(payload)
        raise failure
    monkeypatch.setattr(broker, 'submit', submit_then_fail)
    executor.tick(ready())
    check = store.latest_execution_check()
    assert check['submission_attempted_this_tick'] is True
    assert check['gate'] == 'order_submit'
    assert check['trade']['id'] == store.active_trade()['id']
    assert check['trade']['operation_states']['entry']['state'] == 'attempted'
    assert 'secret' not in json.dumps(check)
    assert 'No new order attempted' not in executor.message
    monkeypatch.setattr(broker, 'submit', original_submit)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at, revision=REVISION)
    restarted.tick(ready())
    assert len([order for order in broker.sent if order['client_order_id'].endswith('-entry')]) == 1
    assert store.active_trade()['stage'] == 'open'


def test_normal_entry_management_and_completion_are_recorded(diagnostic_engine):
    executor, broker, store = diagnostic_engine
    enable(executor)
    executor.tick(ready())
    first = store.latest_execution_check()
    assert first['outcome'] == 'entry_planned' and first['trade']['stage'] == 'open'
    assert first['trade']['operation_states']['entry']['broker_status'] == 'filled'
    assert first['trade']['operation_states']['stop']['broker_status'] == 'new'
    executor.tick(ready())
    assert store.latest_execution_check()['outcome'] == 'managing'
    broker.bid, broker.ask = '111', '111.01'
    for _ in range(4):
        executor.tick(ready())
        if store.active_trade() is None:
            break
    assert store.active_trade() is None
    assert store.latest_execution_check()['outcome'] == 'completed'
    assert store.latest_execution_check()['trade']['stage'] == 'finished'


def test_quote_read_failure_is_not_misattributed_after_successful_stop_placement(diagnostic_engine, monkeypatch):
    executor, broker, store = diagnostic_engine
    enable(executor)
    original_quote = broker.quote
    calls = 0
    def quote(symbol):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise FeedError('secret unavailable quote')
        return original_quote(symbol)
    monkeypatch.setattr(broker, 'quote', quote)
    executor.tick(ready())
    check = store.latest_execution_check()
    assert check['gate'] == 'quote_read' and check['outcome'] == 'feed_error'
    assert check['trade']['operation_states']['stop']['broker_status'] == 'new'


@pytest.mark.parametrize('failure_location', ['store', 'builder'])
def test_logging_failures_do_not_change_orders_permission_or_original_message(diagnostic_engine, monkeypatch, failure_location):
    executor, broker, store = diagnostic_engine
    executor.tick(ready())
    previous = deepcopy(store.latest_execution_check())
    def fail(*args):
        raise RuntimeError('secret diagnostic failure')
    monkeypatch.setattr(store if failure_location == 'store' else executor,
                        'record_execution_check' if failure_location == 'store' else '_record_execution_check', fail)
    enable(executor)
    executor.tick(ready())
    assert len(broker.sent) == 2 and store.active_trade()['stage'] == 'open'
    assert executor.enabled() is True
    assert executor.message == 'Protective stop submitted. Waiting for broker confirmation.'
    result = executor.snapshot()
    assert result['execution_diagnostic_error'] and 'secret' not in result['execution_diagnostic_error']
    assert result['execution_check']['id'] == previous['id']
    assert result['execution_check']['current_at_snapshot'] is False
    assert not executor.tick_lock.locked()


def test_load_failure_and_invalid_revision_are_safe(diagnostic_engine, monkeypatch):
    executor, broker, store = diagnostic_engine
    monkeypatch.setattr(store, 'latest_execution_check', lambda: (_ for _ in ()).throw(RuntimeError('secret load error')))
    restored = Executor(broker, store, now=lambda: broker.at, revision='secret not a revision')
    assert restored.snapshot()['execution_diagnostic_error'] and restored.execution_check is None
    restored.tick(ready())
    assert restored.snapshot()['execution_diagnostic_error'] is None
    assert restored.snapshot()['execution_check']['revision'] is None


def test_malformed_signal_that_crashes_evaluation_still_has_sanitized_error_evidence(diagnostic_engine):
    executor, broker, store = diagnostic_engine
    enable(executor)
    snapshot = ready()
    snapshot['setup']['checks'] = [{'name': ['secret invalid key'], 'passed': True}]
    executor.tick(snapshot)
    check = store.latest_execution_check()
    assert check['outcome'] == 'unexpected_error' and check['exception_kind'] == 'TypeError'
    assert 'secret' not in json.dumps(check)
    assert not broker.sent
