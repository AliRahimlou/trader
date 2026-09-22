"""Offline entry commissioning for the InsightSentry execution policy."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256

import pytest

from pivot.broker import AlpacaBroker
from pivot.execution import Executor
from pivot.feeds import FeedError
from pivot.policy import POLICY_VERSION
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, ready


def proof(at=NOW):
    return {'source': 'insightsentry', 'symbol': 'I:VIX', 'delay_seconds': 0,
            'updated_at': at.isoformat(), 'value': 17.5}


class InsightBroker(FakeBroker):
    requires_vix_entry_quote = True

    def __init__(self):
        super().__init__()
        self.confirmed = []
        self.proof = proof()
        self.delay = 0
        self.actions = []

    def confirm_vix_quote(self, now):
        self.confirmed.append(now)
        self.actions.append('vix')
        self.at += timedelta(seconds=self.delay)
        if isinstance(self.proof, Exception):
            raise self.proof
        return deepcopy(self.proof)

    def submit(self, payload):
        self.actions.append('submit')
        return super().submit(payload)


@pytest.fixture
def engine(tmp_path):
    broker = InsightBroker()
    store = Store(tmp_path / 'insight-execution.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    executor.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
    return executor, broker, store


def test_ready_entry_obtains_fresh_actual_vix_before_submitting(engine):
    executor, broker, store = engine
    executor.tick(ready())
    assert len(broker.confirmed) == 1
    assert broker.actions[0] == 'vix'
    assert broker.sent[0]['client_order_id'].endswith('-entry')
    assert broker.sent[0]['notional'] == '25.00'
    assert broker.sent[1]['type'] == 'stop'
    assert store.active_trade()['stage'] == 'open'


@pytest.mark.parametrize('verification', [
    None, {},
    dict(proof(), source='cnbc'),
    dict(proof(), symbol='VXX'),
    dict(proof(), delay_seconds=900),
    dict(proof(), delay_seconds=False),
    {key: value for key, value in proof().items() if key != 'value'},
    dict(proof(), value=float('nan')),
    dict(proof(), value=float('inf')),
    dict(proof(), value=True),
    dict(proof(), updated_at=(NOW - timedelta(seconds=91)).isoformat()),
    dict(proof(), updated_at=(NOW + timedelta(seconds=6)).isoformat()),  # Beyond the 5 s skew allowance.
    dict(proof(), updated_at='malformed'),
    FeedError('Waiting for a current VIX quote'),
])
def test_missing_invalid_or_stale_vix_proof_never_sends_an_order(engine, verification):
    executor, broker, store = engine
    broker.proof = verification
    executor.tick(ready())
    assert len(broker.confirmed) == 1
    assert broker.sent == []
    assert store.active_trade() is None


def test_slow_vix_verification_cannot_outlive_qqq_quote_deadline(engine):
    executor, broker, store = engine
    broker.delay = 16
    broker.proof = proof(NOW + timedelta(seconds=16))
    executor.tick(ready())
    assert len(broker.confirmed) == 1
    assert broker.sent == []
    assert store.active_trade() is None
    assert 'expired during broker checks' in executor.message


@pytest.mark.parametrize('change', [
    lambda snapshot, broker: snapshot['feeds'].update(vix='unavailable'),
    lambda snapshot, broker: snapshot.update(data_errors=['Missing VIX candle']),
    lambda snapshot, broker: snapshot.update(analysis_at=(NOW - timedelta(seconds=91)).isoformat()),
    lambda snapshot, broker: snapshot['setup'].update(state='CONFIRMING'),
    lambda snapshot, broker: snapshot['setup']['checks'][0].update(passed=False),
    lambda snapshot, broker: setattr(broker, 'open', False),
    lambda snapshot, broker: setattr(broker, 'close_in', 300),
    lambda snapshot, broker: setattr(broker, 'position_data', [{'symbol': 'AAPL', 'qty': '1'}]),
    lambda snapshot, broker: broker.account_data.update(account_blocked=True),
    lambda snapshot, broker: broker.account_data.update(buying_power='1'),
    lambda snapshot, broker: setattr(broker, 'ask', '120'),
])
def test_otherwise_blocked_setup_does_not_spend_vix_quote_requests(engine, change):
    executor, broker, store = engine
    snapshot = ready()
    change(snapshot, broker)
    executor.tick(snapshot)
    assert broker.confirmed == []
    assert broker.sent == []
    assert store.active_trade() is None


def test_previously_handled_event_skips_vix_quote_check(engine):
    executor, broker, store = engine
    snapshot = ready()
    identity = f'{POLICY_VERSION}|QQQ|{snapshot["setup"]["event_id"]}'
    key = sha256(identity.encode()).hexdigest()[:24]
    trade = {'id': key, 'stage': 'finished'}
    assert store.reserve_trade(trade)
    store.save_trade(trade, finished=True)
    executor.tick(snapshot)
    assert broker.confirmed == []
    assert broker.sent == []
    assert 'already been handled' in executor.message


def test_previous_policy_permission_is_preserved_but_cannot_start_entries(engine):
    executor, broker, store = engine
    previous = store.set_control(True, 'nasdaq-qqq-execution-v1', 'fake-account-only')
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    snapshot = restarted.snapshot()
    assert snapshot['live_enabled'] is False
    assert snapshot['review_required'] is True
    assert snapshot['execution_available'] is True
    restarted.tick(ready())
    assert broker.sent == []
    assert broker.confirmed == []
    assert store.control() == previous
    restarted.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
    assert restarted.enabled() is True
    assert restarted.snapshot()['review_required'] is False
    assert broker.sent == []


def test_user_turning_off_during_vix_check_prevents_entry(engine):
    executor, broker, store = engine
    original = broker.confirm_vix_quote
    def turn_off(now):
        executor.set_live({'enabled': False, 'policy_version': POLICY_VERSION})
        return original(now)
    broker.confirm_vix_quote = turn_off
    executor.tick(ready())
    assert broker.confirmed
    assert broker.sent == []
    assert store.active_trade() is None


def test_alpaca_adapter_requests_quote_gate_only_for_insightsentry():
    class Feeds:
        vix_provider = 'insightsentry'

        def confirm_vix_quote(self, now):
            assert now == NOW
            return proof(now)
    feeds = Feeds()
    adapter = AlpacaBroker(feeds)
    assert adapter.requires_vix_entry_quote is True
    assert adapter.confirm_vix_quote(NOW) == proof()
    feeds.vix_provider = 'massive_indices'
    assert adapter.requires_vix_entry_quote is False
