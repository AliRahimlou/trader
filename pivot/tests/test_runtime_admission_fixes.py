"""Offline regressions for admission bookkeeping and saved evidence expiry."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest

from pivot.execution import Executor, Waiting
from pivot.feeds import FeedError
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready


@pytest.fixture
def runtime(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'runtime.sqlite3')
    store.save({'sizing_mode': 'target', 'target_dollars': '5.00'})
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def refreshed(snapshot, now):
    result = deepcopy(snapshot)
    result['analysis_at'] = now.isoformat()
    result['data_valid_until'] = (now + timedelta(seconds=90)).isoformat()
    return result


def abandon_after_account_read_failure(executor, broker, store):
    snapshot = ready()
    account = broker.account
    calls = []
    def fail_after_reservation():
        calls.append(True)
        if len(calls) == 2:
            raise FeedError('Offline account read failure after reservation')
        return account()
    broker.account = fail_after_reservation
    executor.tick(snapshot)
    assert not broker.sent
    assert store.active_trade()['ops']['entry']['state'] == 'prepared'
    broker.account = account
    broker.at += timedelta(seconds=11)
    executor.tick(refreshed(snapshot, broker.at))
    assert store.active_trade() is None and not broker.sent
    return snapshot


def test_never_submitted_reservation_can_retry_current_event_after_restart(runtime):
    executor, broker, store = runtime
    snapshot = abandon_after_account_read_failure(executor, broker, store)
    identity = executor._event_key(snapshot['setup'])
    assert store.trade_exists(identity) and not store.entry_consumed(identity)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(refreshed(snapshot, broker.at))
    assert len(broker.sent) == 2
    assert broker.sent[0]['notional'] == '5.00'
    assert broker.sent[1]['qty'] == '0.05' and broker.sent[1]['type'] == 'stop'
    assert store.entry_consumed(identity)
    assert store.active_trade()['signal']['event_id'] == snapshot['setup']['event_id']
    abandoned = [event for event in store.events() if event['kind'] == 'unsubmitted_entry_retried']
    assert len(abandoned) == 1
    assert abandoned[0]['detail']['id'] == identity
    assert abandoned[0]['detail']['ops']['entry']['state'] == 'prepared'
    assert abandoned[0]['detail']['stage'] == 'finished'
    assert abandoned[0]['detail']['reason'] == 'Entry expired before submission; no order sent'


@pytest.mark.parametrize('change', [
    lambda trade: trade['ops']['entry'].update(state='attempted'),
    lambda trade: trade['ops']['entry'].update(state='rejected'),
    lambda trade: trade['ops']['entry'].update(state='unknown'),
    lambda trade: trade['ops']['entry'].update(last_seen={'status': 'filled'}),
    lambda trade: trade['ops'].update(stop={'state': 'prepared'}),
    lambda trade: trade.update(filled_qty='0'),
    lambda trade: trade.update(reason='Unknown retirement reason'),
    lambda trade: trade['ops']['entry']['payload'].update(client_order_id='unverified'),
])
def test_attempted_unknown_or_contradictory_history_never_rearms(runtime, change):
    executor, broker, store = runtime
    snapshot = abandon_after_account_read_failure(executor, broker, store)
    identity = executor._event_key(snapshot['setup'])
    with store.connect() as db:
        import json
        trade = json.loads(db.execute('SELECT body FROM trades WHERE id=?', (identity,)).fetchone()[0])
    change(trade)
    store.save_trade(trade, finished=True)
    assert store.entry_consumed(identity)
    assert not store.reserve_trade({**trade, 'stage': 'entering'})
    executor.tick(refreshed(snapshot, broker.at))
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'setup_deduplication'
    assert not any(event['kind'] == 'unsubmitted_entry_retried' for event in store.events())


def test_retry_reservation_is_atomic_and_preserves_single_active_slot(runtime):
    executor, broker, store = runtime
    snapshot = abandon_after_account_read_failure(executor, broker, store)
    identity = executor._event_key(snapshot['setup'])
    replacement = {'id': identity, 'stage': 'entering'}
    assert store.reserve_trade({'id': 'another-event', 'stage': 'entering'})
    assert not store.reserve_trade(replacement)
    assert not any(event['kind'] == 'unsubmitted_entry_retried' for event in store.events())
    store.save_trade({'id': 'another-event', 'stage': 'finished'}, finished=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Store(store.path).reserve_trade(replacement), range(2)))
    assert sorted(results) == [False, True]
    assert store.active_trade()['id'] == identity
    assert len([event for event in store.events() if event['kind'] == 'unsubmitted_entry_retried']) == 1


def test_retry_still_requires_current_evidence_and_owner_permission(runtime):
    executor, broker, store = runtime
    snapshot = abandon_after_account_read_failure(executor, broker, store)
    snapshot['setup']['leader_evidence_valid_until'] = broker.at.isoformat()
    executor.tick(refreshed(snapshot, broker.at))
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'
    store.set_control(False)
    executor.tick(refreshed(ready(), broker.at))
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['outcome'] == 'disabled'


@pytest.mark.parametrize('deadline', [None, 'bad', NOW.replace(tzinfo=None).isoformat(),
    NOW.isoformat(), (NOW-timedelta(microseconds=1)).isoformat(),
    (NOW+timedelta(minutes=15, microseconds=1)).isoformat()])
def test_invalid_leader_evidence_deadline_blocks_before_broker_reads(runtime, deadline):
    executor, broker, store = runtime
    snapshot = ready()
    snapshot['setup']['leader_evidence_valid_until'] = deadline
    broker.account = lambda: pytest.fail('Expired/malformed leader evidence must precede broker reads')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'


def test_leader_evidence_expiring_during_broker_reads_cannot_reserve(runtime):
    executor, broker, store = runtime
    snapshot = ready()
    snapshot['setup']['leader_evidence_valid_until'] = (NOW+timedelta(seconds=1)).isoformat()
    quote = broker.quote
    def slow_quote(symbol):
        broker.at += timedelta(seconds=1)
        return quote(symbol)
    broker.quote = slow_quote
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None


def test_prepared_entry_cannot_outlive_its_leader_evidence(runtime):
    executor, broker, store = runtime
    snapshot = ready()
    snapshot['setup']['leader_evidence_valid_until'] = (NOW+timedelta(seconds=1)).isoformat()
    executor._manage = lambda trade: None
    executor.tick(snapshot)
    trade = store.active_trade()
    assert trade['signal']['leader_evidence_valid_until'] == snapshot['setup']['leader_evidence_valid_until']
    assert trade['data_valid_until'] == snapshot['setup']['leader_evidence_valid_until']
    assert not broker.sent
    broker.at += timedelta(seconds=1)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(snapshot)
    assert not broker.sent and store.active_trade() is None


def test_attempted_entry_recovers_and_protects_even_after_leader_evidence_expires(runtime):
    executor, broker, store = runtime
    snapshot = ready()
    snapshot['setup']['leader_evidence_valid_until'] = (NOW+timedelta(seconds=1)).isoformat()
    broker.entry_mode = 'lost_accepted'
    executor.tick(snapshot)
    assert len(broker.sent) == 1 and broker.position_data
    broker.at += timedelta(seconds=1)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert store.active_trade()['stage'] == 'open'


def prepared_reservation(runtime):
    executor, broker, store = runtime
    manage = executor._manage
    executor._manage = lambda trade: None
    executor.tick(ready())
    executor._manage = manage
    return deepcopy(store.active_trade())


@pytest.mark.parametrize('expiry_path', ['_manage', '_order'])
def test_stale_worker_cannot_retire_a_claimed_entry_or_rearm_an_unknown_post(runtime, expiry_path):
    executor, broker, store = runtime
    stale = prepared_reservation(runtime)
    # A second worker commits its claim before a POST whose response is lost.
    assert Store(store.path).claim_operation(stale['id'], 'entry', expected_entry=stale)
    broker.entry_mode = 'lost_accepted'
    with pytest.raises(FeedError):
        broker.submit(stale['ops']['entry']['payload'])
    broker.at += timedelta(seconds=11)
    with pytest.raises(Waiting, match='durable order reconciliation'):
        if expiry_path == '_manage':
            executor._manage(stale)
        else:
            executor._order(stale, 'entry')
    durable = store.active_trade()
    assert durable['ops']['entry']['state'] == 'attempted'
    assert store.entry_consumed(stale['id'])
    assert not store.reserve_trade(stale)
    assert not any(event['kind'] == 'trade_finished' for event in store.events())
    # The next worker loads the attempted intent and protects its actual fill.
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert store.active_trade()['stage'] == 'open'


@pytest.mark.parametrize('replace_reservation', [False, True])
def test_stale_entry_claim_cannot_post_after_retirement_or_replacement(runtime, replace_reservation):
    executor, broker, store = runtime
    stale = prepared_reservation(runtime)
    broker.at += timedelta(seconds=11)
    assert store.expire_prepared_entry(stale, broker.at.isoformat())
    replacement = deepcopy(stale)
    replacement['created_at'] = broker.at.isoformat()
    if replace_reservation:
        assert store.reserve_trade(replacement)
    # Resume a worker paused after its expiry check, just before atomic claim.
    with pytest.raises(Waiting, match='durable order reconciliation'):
        executor._order_admitted(stale, 'entry')
    assert not broker.sent
    assert store.active_trade() == (replacement if replace_reservation else None)
    if replace_reservation:
        # A stale expiry result cannot retire the new admission either.
        with pytest.raises(Waiting, match='durable order reconciliation'):
            executor._expire_entry(stale)
        assert store.active_trade() == replacement


def test_claim_and_expiry_have_one_atomic_winner(runtime):
    executor, broker, store = runtime
    trade = prepared_reservation(runtime)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claim = pool.submit(Store(store.path).claim_operation, trade['id'], 'entry', expected_entry=trade)
        expire = pool.submit(Store(store.path).expire_prepared_entry, trade, broker.at.isoformat())
        claimed, expired = claim.result(), expire.result()
    assert claimed != bool(expired)
    assert store.entry_consumed(trade['id']) is claimed
    assert len([event for event in store.events() if event['kind'] == 'trade_finished']) == int(not claimed)
    assert not broker.sent


@pytest.mark.parametrize('change', [
    {'leader_observation_at': None}, {'leader_observation_at': NOW.replace(tzinfo=None).isoformat()},
    {'leader_observation_at': (NOW+timedelta(seconds=1)).isoformat()},
    {'leader_observation_valid_until': None}, {'leader_observation_valid_until': 'not-a-time'},
    {'leader_observation_valid_until': (NOW+timedelta(seconds=391)).isoformat()},
    {'leader_observations_synchronized': False}, {'leader_observations_synchronized': 1},
])
def test_malformed_or_unsynchronized_observation_deadline_blocks_before_broker(runtime, change):
    executor, broker, store = runtime
    snapshot = ready()
    snapshot['setup'].update(change)
    broker.account = lambda: pytest.fail('Invalid observation contract must block before broker reads')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'


def aging_leader_snapshot(broker):
    snapshot = ready()
    broker.at = NOW+timedelta(seconds=380)
    return refreshed(snapshot, broker.at)


def test_saved_analysis_cannot_authorize_after_leader_input_deadline(runtime):
    executor, broker, store = runtime
    snapshot = aging_leader_snapshot(broker)
    broker.at += timedelta(seconds=10)
    broker.account = lambda: pytest.fail('Stale leader candles must block before broker reads')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    check = executor.execution_check
    assert check['gate'] == 'signal_age_policy'
    assert check['signal']['leader_evidence_valid_until'] == (NOW+timedelta(minutes=15)).isoformat()
    assert check['signal']['leader_observation_valid_until'] == (NOW+timedelta(seconds=390)).isoformat()


def test_broker_preflight_cannot_outlive_leader_input_deadline(runtime):
    executor, broker, store = runtime
    snapshot = aging_leader_snapshot(broker)
    quote = broker.quote
    def slow_quote(symbol):
        broker.at += timedelta(seconds=10)
        return quote(symbol)
    broker.quote = slow_quote
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None


@pytest.mark.parametrize('attempted', [False, True])
def test_prepared_expiry_and_attempted_recovery_respect_leader_input_deadline(runtime, attempted):
    executor, broker, store = runtime
    snapshot = aging_leader_snapshot(broker)
    if attempted:
        broker.entry_mode = 'lost_accepted'
    else:
        executor._manage = lambda trade: None
    executor.tick(snapshot)
    trade = store.active_trade()
    assert trade['data_valid_until'] == (NOW+timedelta(seconds=390)).isoformat()
    assert trade['signal']['leader_observation_at'] == NOW.isoformat()
    broker.at += timedelta(seconds=10)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(snapshot)
    if attempted:
        assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
        assert store.active_trade()['stage'] == 'open'
    else:
        assert not broker.sent and store.active_trade() is None


def test_calendar_data_deadline_is_exclusive_even_when_analysis_is_recent(runtime):
    executor, broker, store = runtime
    snapshot = ready()
    snapshot['data_valid_until'] = NOW.isoformat()
    broker.account = lambda: pytest.fail('Expired required input cannot reach broker preflight')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'data_expiry'


@pytest.mark.parametrize('expiry', ['input', 'reaction', 'observation', 'intent'])
def test_entry_claim_wait_consumes_expired_event_but_releases_slot_for_new_event(runtime, expiry):
    executor, broker, store = runtime
    snapshot = ready()
    if expiry == 'input':
        snapshot['data_valid_until'] = (NOW+timedelta(seconds=1)).isoformat()
    elif expiry == 'reaction':
        snapshot['setup']['leader_evidence_valid_until'] = (NOW+timedelta(seconds=1)).isoformat()
    elif expiry == 'observation':
        snapshot['setup']['leader_observation_at'] = (NOW-timedelta(seconds=389)).isoformat()
        snapshot['setup']['leader_observation_valid_until'] = (NOW+timedelta(seconds=1)).isoformat()
    claim = store.claim_operation
    def delayed_claim(*args, **kwargs):
        result = claim(*args, **kwargs)
        if result:
            broker.at += timedelta(seconds=11 if expiry == 'intent' else 1)
        return result
    store.claim_operation = delayed_claim
    executor.tick(snapshot)
    identity = executor._event_key(snapshot['setup'])
    assert not broker.sent and store.active_trade() is None
    assert store.entry_consumed(identity)
    assert executor.execution_check['trade']['operation_states']['entry']['state'] == 'aborted_before_submit'
    assert executor.execution_check['gate'] == 'final_data_expiry'
    # A proven no-POST abort consumes this event, without stranding later ones.
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(refreshed(snapshot, broker.at))
    assert not broker.sent and store.active_trade() is None
    assert store.entry_consumed(identity)
    restarted.tick(ready(broker.at))
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert store.active_trade()['id'] != identity
