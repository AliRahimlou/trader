"""Synthetic event-admission and durable recovery boundaries; no real orders."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from pivot.execution import Executor
from pivot.store import Store
from pivot.tests.test_execution import NOW, FakeBroker, enable, ready, identify_event


@pytest.fixture
def engine(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'v2-admission.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


@pytest.mark.parametrize('changes', [
    {'policy_version': None}, {'policy_version': 'nasdaq-video-interpretation-v1'},
    {'strategy_id': 'unknown'}, {'strategy_id': []},
    {'event_id': None}, {'event_id': 'ev2_' + 'a' * 63}, {'event_id': 'ev2_' + 'A' * 64},
    {'event_id': 'ev2_' + 'a' * 64 + '\n'}, {'event_id': ['a']},
    {'event_id': 'ev2_' + '0' * 64}, {'event_zone': None},
    {'event_zone': {'low': float('nan'), 'high': 100, 'established_at': NOW.isoformat()}},
    {'event_zone': {'low': True, 'high': 100, 'established_at': NOW.isoformat()}},
    {'event_origin_at': None}, {'event_origin_at': NOW.replace(tzinfo=None).isoformat()},
    {'event_origin_at': (NOW + timedelta(seconds=1)).isoformat()},
    {'event_at': (NOW - timedelta(seconds=1)).isoformat()},
    {'latest_evidence_at': (NOW + timedelta(seconds=1)).isoformat()},
    {'latest_evidence_at': (NOW - timedelta(seconds=1)).isoformat()},
    {'event_expires_at': None}, {'event_expires_at': NOW.isoformat()},
    {'event_expires_at': (NOW + timedelta(minutes=181)).isoformat()},
])
def test_invalid_versioned_event_blocks_before_broker_reads(engine, changes):
    executor, broker, store = engine
    snapshot = ready()
    snapshot['setup'].update(changes)
    broker.account = lambda: pytest.fail('Invalid signal must stop before account reads')
    executor.tick(snapshot)
    assert broker.sent == [] and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'
    assert executor.execution_check['outcome'] == 'waiting'


@pytest.mark.parametrize('checks', [None, {}, [None], [{'name': [], 'passed': True}],
                                    ready()['setup']['checks'] + [ready()['setup']['checks'][0]]])
def test_malformed_or_duplicate_check_list_is_not_an_admission(engine, checks):
    executor, broker, store = engine
    snapshot = ready()
    snapshot['setup']['checks'] = checks
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['outcome'] == 'waiting'


def aged_event(snapshot, origin, event, evidence):
    snapshot['setup'].update(event_origin_at=origin.isoformat(), event_at=event.isoformat(),
        latest_evidence_at=evidence.isoformat(), event_expires_at=(origin + timedelta(minutes=180)).isoformat())
    identify_event(snapshot['setup'])
    return snapshot


def test_stable_old_confirmation_with_current_hourly_evidence_can_enter(engine):
    executor, broker, store = engine
    snapshot = aged_event(ready(), NOW - timedelta(minutes=150), NOW - timedelta(minutes=120), NOW)
    executor.tick(snapshot)
    assert len(broker.sent) == 2 and store.active_trade()['stage'] == 'open'
    assert store.active_trade()['signal']['event_id'] == snapshot['setup']['event_id']


def test_fresh_analysis_cannot_reanimate_stale_hourly_evidence(engine):
    executor, broker, store = engine
    snapshot = aged_event(ready(), NOW - timedelta(minutes=150), NOW - timedelta(minutes=120),
                         NOW - timedelta(seconds=3691))
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None


def test_zone_known_only_after_origin_bar_started_is_not_premarked(engine):
    executor, broker, store = engine
    snapshot = ready()
    snapshot['setup']['event_zone']['established_at'] = (NOW - timedelta(minutes=30)).isoformat()
    identify_event(snapshot['setup'])  # Even an internally consistent hash cannot admit future information.
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'


def test_equivalent_timestamp_offsets_keep_canonical_identity_valid(engine):
    executor, broker, store = engine
    snapshot = ready()
    local = timezone(timedelta(hours=-4))
    for key in ('event_origin_at', 'event_at', 'latest_evidence_at', 'event_expires_at'):
        snapshot['setup'][key] = datetime.fromisoformat(snapshot['setup'][key]).astimezone(local).isoformat()
    zone = snapshot['setup']['event_zone']
    zone['established_at'] = datetime.fromisoformat(zone['established_at']).astimezone(local).isoformat()
    executor.tick(snapshot)
    assert len(broker.sent) == 2 and store.active_trade()['stage'] == 'open'


def test_recent_event_from_previous_new_york_date_is_not_carried_forward(engine):
    executor, broker, store = engine
    broker.at = datetime(2026, 9, 17, 4, 30, tzinfo=timezone.utc)
    snapshot = aged_event(ready(broker.at), broker.at - timedelta(hours=1),
                         broker.at - timedelta(minutes=15), broker.at)
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None


def test_preflight_failure_does_not_consume_opportunity(engine):
    executor, broker, store = engine
    broker.bid, broker.ask = '101.99', '102'
    executor.tick(ready())
    assert not broker.sent and store.active_trade() is None
    broker.bid, broker.ask = '99.99', '100'
    executor.tick(ready())
    assert len(broker.sent) == 2 and store.active_trade()['stage'] == 'open'


def test_broker_rejection_consumes_event_even_after_restart_and_direction_flip(engine):
    executor, broker, store = engine
    broker.entry_mode = 'reject'
    executor.tick(ready())
    assert len(broker.sent) == 1 and store.active_trade() is None
    broker.entry_mode = 'filled'
    # The rejection paused Socrates (global Live stayed on); the owner re-selects it.
    assert store.control()['enabled'] is True and store.strategy_selection() == {'socrates': False}
    with store.connect() as db:
        db.execute('UPDATE strategy_selection SET body=? WHERE id=1', (json.dumps({'socrates': True}),))
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    assert restarted.enabled()
    reversed_signal = ready(direction='short')
    assert reversed_signal['setup']['event_id'] == ready()['setup']['event_id']
    restarted.tick(reversed_signal)
    assert len(broker.sent) == 1
    assert 'already been handled' in restarted.message


def test_stopout_then_later_retest_direction_flip_cannot_reenter_after_restart(engine):
    executor, broker, store = engine
    original = aged_event(ready(), NOW - timedelta(minutes=90), NOW - timedelta(minutes=60), NOW)
    executor.tick(original)
    broker.fill(broker.sent[1]['client_order_id'])
    executor.tick(original)
    assert store.active_trade() is None and not broker.position_data
    broker.at += timedelta(minutes=10)
    repeated = ready(broker.at, 'short')
    repeated['setup'].update({key: original['setup'][key] for key in
                             ('event_id', 'event_origin_at', 'event_at', 'event_expires_at', 'event_zone')})
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(repeated)
    assert len(broker.sent) == 2 and store.active_trade() is None
    assert 'already been handled' in restarted.message


def near_expiry():
    return aged_event(ready(), NOW - timedelta(minutes=180) + timedelta(seconds=1), NOW, NOW)


def test_event_expiring_during_quote_read_never_gets_reserved(engine):
    executor, broker, store = engine
    quote = broker.quote
    def slow_quote(symbol):
        broker.at += timedelta(seconds=1)
        return quote(symbol)
    broker.quote = slow_quote
    executor.tick(near_expiry())
    assert not broker.sent and store.active_trade() is None


@pytest.mark.parametrize('legacy', [False, True])
def test_expired_or_legacy_unsent_intent_is_retired_after_restart(engine, legacy):
    executor, broker, store = engine
    executor._manage = lambda trade: None
    executor.tick(near_expiry())
    trade = store.active_trade()
    assert trade and not broker.sent
    if legacy:
        trade.pop('signal')
        store.save_trade(trade)
    else:
        broker.at += timedelta(seconds=1)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(ready(broker.at))
    assert not broker.sent and store.active_trade() is None


def test_legacy_open_position_stays_managed_when_permission_requires_review(engine):
    executor, broker, store = engine
    executor.tick(ready())
    trade = store.active_trade()
    trade.pop('signal')  # Saved pre-v2 trade has no new admission metadata.
    store.save_trade(trade)
    store.set_control(True, 'nasdaq-qqq-execution-v3', 'fake-account-only')
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    assert not restarted.enabled()
    broker.fill(broker.sent[1]['client_order_id'])
    restarted.tick({})
    assert store.active_trade() is None and not broker.position_data
    assert len(broker.sent) == 2


@pytest.mark.parametrize('legacy', [False, True])
def test_uncertain_accepted_entry_recovery_ignores_expired_signal_but_never_resubmits(engine, legacy):
    executor, broker, store = engine
    broker.entry_mode = 'lost_accepted'
    executor.tick(near_expiry())
    assert len(broker.sent) == 1 and broker.position_data
    if legacy:
        trade = store.active_trade()
        trade.pop('signal')
        store.save_trade(trade)
    broker.at += timedelta(seconds=2)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert store.active_trade()['stage'] == 'open'
    assert sum(order['type'] == 'market' for order in broker.sent) == 1
