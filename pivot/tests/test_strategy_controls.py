"""Owner strategy selection is durable and separate from global live permission."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pivot.api import create_app
from pivot.crypto_execution import POLICY_VERSION as CRYPTO_POLICY
from pivot.feeds import FeedError
from pivot.policy import POLICY_VERSION
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker

HEADERS = {'origin':'http://testserver', 'x-pivot-intent':'settings'}


def app_service(tmp_path):
    main = FakeBroker()
    service = Service(None, Store(tmp_path/'state.db'), main)
    account = {**main.account(), 'crypto_status':'ACTIVE', 'non_marginable_buying_power':'100'}
    crypto_broker = SimpleNamespace(account=lambda:deepcopy(account))
    service.crypto_executor = SimpleNamespace(broker=crypto_broker, entry_gate=None,
        snapshot=lambda:{'message':'Fixture ready', 'at':None, 'trades':[], 'incidents':[]})
    return service, main, account


def enable_crypto(service):
    return service.save_strategies({'range_reversal':{'enabled':True, 'target_dollars':'5.00',
        'symbols':['BTC/USD'], 'policy_version':CRYPTO_POLICY}})


def test_family_enable_is_durable_without_changing_master_or_placing_order(tmp_path):
    service, broker, _ = app_service(tmp_path)
    before = service.store.control()
    result = enable_crypto(service)
    assert result['portfolio']['range_reversal']['enabled'] is True
    assert result['live_enabled'] is False
    assert service.store.control() == before and broker.sent == []
    reopened = Service(None, Store(service.store.path))
    assert reopened.crypto_store.control()['enabled'] is True
    assert reopened.crypto_store.control()['target_dollars'] == '5.00'


def test_master_on_remains_on_when_one_family_disabled_and_views_are_reads(tmp_path):
    service, broker, _ = app_service(tmp_path)
    service.set_live({'enabled':True, 'policy_version':POLICY_VERSION})
    enable_crypto(service)
    master = service.store.control()
    result = service.save_strategies({'socrates':{'enabled':False}})
    assert result['live_enabled'] is True and not service.executor.enabled()
    assert result['portfolio']['socrates']['enabled'] is False
    assert result['portfolio']['range_reversal']['enabled'] is True
    with TestClient(create_app(service, background=False)) as client:
        for _ in range(3):
            assert client.get('/api/snapshot').json()['live_enabled'] is True
        assert client.get('/api/health').json()['live_enabled'] is True
    assert service.store.control() == master and broker.sent == []


def test_both_families_configured_atomically_and_pending_authorization_expires(tmp_path):
    service, broker, _ = app_service(tmp_path)
    service.save_strategies({'socrates':{'enabled':False}})
    before = service.store.entry_authorization()
    result = service.save_strategies({'socrates':{'enabled':True}, 'range_reversal':{
        'enabled':True, 'policy_version':CRYPTO_POLICY, 'target_dollars':'5.00', 'symbols':['BTC/USD','ETH/USD']}})
    assert result['portfolio']['socrates']['enabled'] and result['portfolio']['range_reversal']['enabled']
    assert service.store.entry_authorization()['generation'] > before['generation']
    saved = service.store.deployment_permission()
    with pytest.raises(ValueError):
        service.save_strategies({'socrates':{'enabled':False}, 'range_reversal':{'target_dollars':'200001.00'}})
    assert service.store.deployment_permission() == saved
    assert broker.sent == []


def test_family_off_succeeds_without_provider_and_does_not_change_master(tmp_path):
    service, broker, _ = app_service(tmp_path)
    enable_crypto(service)
    service.set_live({'enabled':True, 'policy_version':POLICY_VERSION})
    master = service.store.control()
    def unavailable(): raise FeedError('fixture outage')
    service.crypto_executor.broker.account = unavailable
    result = service.save_strategies({'range_reversal':{'enabled':False}})
    assert result['portfolio']['range_reversal']['enabled'] is False
    assert result['live_enabled'] is True and service.store.control() == master


@pytest.mark.parametrize('account_change', [
    {'crypto_status':'INACTIVE'}, {'trading_blocked':True}, {'account_blocked':True},
    {'mode':'paper'}, {'account_ref':None}, {'trade_suspended_by_user':True}])
def test_crypto_activation_verifies_reviewed_live_account(tmp_path, account_change):
    service, _, account = app_service(tmp_path)
    account.update(account_change)
    with pytest.raises(ValueError): enable_crypto(service)
    assert service.crypto_store.control()['enabled'] is False


def test_changed_crypto_account_requires_new_review_after_disable(tmp_path):
    service, _, account = app_service(tmp_path)
    enable_crypto(service)
    account['account_ref'] = 'different-fixture-account'
    with pytest.raises(ValueError, match='account changed'):
        service.save_strategies({'range_reversal':{'target_dollars':'6.00'}})
    service.save_strategies({'range_reversal':{'enabled':False}})
    enable_crypto(service)
    assert service.crypto_store.control()['account_ref'] == 'different-fixture-account'


@pytest.mark.parametrize('payload', [{}, {'view':'all'}, {'socrates':{'enabled':'true'}},
    {'range_reversal':{'enabled':True}}, {'range_reversal':{'enabled':True,'policy_version':'old'}},
    {'range_reversal':{'target_dollars':'0'}}, {'range_reversal':{'symbols':[]}},
    {'range_reversal':{'symbols':['BTC/USD','BTC/USD']}}, {'range_reversal':{'symbols':['DOGE/USD']}},
    {'range_reversal':{'symbols':[{}]}}, {'range_reversal':{'account_ref':'injected'}}])
def test_invalid_or_unreviewed_strategy_changes_are_atomic(tmp_path, payload):
    service, _, _ = app_service(tmp_path)
    before = service.store.deployment_permission()
    with pytest.raises((ValueError, TypeError)):
        service.save_strategies(payload)
    assert service.store.deployment_permission() == before


def test_strategy_endpoint_checks_origin_and_returns_complete_scoped_snapshot(tmp_path):
    service, _, _ = app_service(tmp_path)
    with TestClient(create_app(service, background=False)) as client:
        payload = {'range_reversal':{'enabled':True,'policy_version':CRYPTO_POLICY}}
        assert client.put('/api/strategies', json=payload).status_code == 403
        assert client.put('/api/strategies', json=payload, headers={**HEADERS,'origin':'https://foreign.example'}).status_code == 403
        response = client.put('/api/strategies', json=payload, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body['portfolio']['range_reversal']['enabled'] is True
        assert body['portfolio']['range_reversal']['capabilities'] == {'long':True,'short':False}
        assert 'account_ref' not in body['portfolio']['range_reversal']
        assert body['live_enabled'] is False and body['app_version']


def test_master_off_preserves_selections_for_later_owner_enable(tmp_path):
    service, _, _ = app_service(tmp_path)
    enable_crypto(service)
    service.set_live({'enabled':True, 'policy_version':POLICY_VERSION})
    saved = service.crypto_store.control()
    result = service.set_live({'enabled':False, 'policy_version':POLICY_VERSION})
    assert result['live_enabled'] is False
    assert service.crypto_store.control() == saved
    assert service.store.strategy_selection()['socrates'] is True


def test_reconciliation_endpoint_is_protected_and_never_changes_permission(tmp_path):
    service, broker, _ = app_service(tmp_path)
    calls = []
    def recover():
        calls.append('read-only recovery')
        return {'message':'An unresolved broker order remains.', 'recovered':[], 'remaining':[{'id':'fixture'}]}
    service.crypto_executor.recover_incidents = recover
    before = service.store.deployment_permission()
    with TestClient(create_app(service, background=False)) as client:
        assert client.post('/api/crypto/reconcile', json={}).status_code == 403
        assert client.post('/api/crypto/reconcile', json={}, headers={**HEADERS, 'origin':'https://foreign.example'}).status_code == 403
        assert client.post('/api/crypto/reconcile', json={'clear_all':True}, headers=HEADERS).status_code == 422
        result = client.post('/api/crypto/reconcile', json={}, headers=HEADERS)
        assert result.status_code == 200 and 'portfolio' in result.json()
        assert result.json()['crypto_reconciliation']['message'] == 'An unresolved broker order remains.'
        assert calls == ['read-only recovery']
    assert service.store.deployment_permission() == before and broker.sent == []


def test_reconciliation_provider_failure_does_not_clear_incidents(tmp_path):
    service, _, _ = app_service(tmp_path)
    service.crypto_store.incident('fixture-incident', 'Fixture requires review')
    def unavailable(): raise FeedError('private provider detail')
    service.crypto_executor.recover_incidents = unavailable
    with TestClient(create_app(service, background=False)) as client:
        result = client.post('/api/crypto/reconcile', json={}, headers=HEADERS)
        assert result.status_code == 503 and 'private provider detail' not in result.text
    assert len(service.crypto_store.incidents()) == 1


@pytest.mark.parametrize('payload', [
    {'socrates':{'enabled':False}},
    {'range_reversal':{'enabled':False}},
    {'range_reversal':{'target_dollars':'6.00'}},
])
def test_all_family_settings_wait_during_installation_but_master_off_works(tmp_path, payload):
    import fcntl
    service, _, _ = app_service(tmp_path)
    service.set_live({'enabled':True, 'policy_version':POLICY_VERSION})
    path = tmp_path/'deployment.lock'
    with TestClient(create_app(service, background=False, deployment_lock=path)) as client:
        before = service.store.deployment_permission()
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = client.put('/api/strategies', json=payload, headers=HEADERS)
            assert result.status_code == 409
            assert service.store.deployment_permission() == before
            result = client.put('/api/live', json={'enabled':False, 'policy_version':POLICY_VERSION},
                headers={**HEADERS, 'x-pivot-intent':'live-control'})
            assert result.status_code == 200 and result.json()['live_enabled'] is False
