"""v3's installed updater must admit v4 once without downgrading later updates."""
import importlib.util
from pathlib import Path

import pytest

from pivot.crypto_store import CryptoStore
from pivot.deployment import EntryGate, _control_token, _deployment_permission, deployment_readiness
from pivot.execution import Executor
from pivot.portfolio import Portfolio
from pivot.store import Store
from pivot.tests.test_deployment_gate import exclusive_process, hold, CANDIDATE, REVISION
from pivot.tests.test_execution import FakeBroker, NOW, enable

SPEC=importlib.util.spec_from_file_location('migration_updater',Path(__file__).parents[2]/'deploy'/'update_from_github.py')
UPDATER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATER)


@pytest.fixture
def migration(tmp_path):
    store=Store(tmp_path/'main.db')
    crypto=CryptoStore(store.path)
    broker=FakeBroker()
    executor=Executor(broker,store,now=lambda:broker.at)
    executor.portfolio=Portfolio(store)
    executor.portfolio.register('range_reversal',crypto.active_trades)
    executor.entry_gate=EntryGate(tmp_path/'deployment.lock')
    enable(executor)
    legacy=_control_token(store.control())
    record=hold(executor.entry_gate,permission_token=legacy)
    return executor,store,crypto,broker,record,legacy


def test_v3_installed_updater_accepts_unchanged_v4_candidate_then_returns_to_composite(migration):
    executor,store,crypto,broker,record,legacy=migration
    composite=_deployment_permission(store)[1]
    assert composite!=legacy
    original=store.deployment_permission()
    with exclusive_process(executor.entry_gate.lock_path):
        result=deployment_readiness(executor,store,CANDIDATE)
        assert result['permission_token']==legacy and result['ok']
        # These exact strict pre/post checks are used by the installed v3 updater;
        # it copies the candidate updater only after this check succeeds.
        allowed,reason=UPDATER.readiness_allowed(result,CANDIDATE,record,NOW,NOW,permission_token=legacy)
        assert allowed,reason
        assert 'permission_token' not in executor.entry_gate.status()
    executor.entry_gate.hold_path.unlink()
    result=deployment_readiness(executor,store,CANDIDATE)
    assert result['permission_token']==composite
    assert store.deployment_permission()==original
    assert not broker.sent and not broker.canceled


def test_following_v4_update_requires_composite_family_permission(migration):
    executor,store,crypto,broker,record,legacy=migration
    composite=_deployment_permission(store)[1]
    record=hold(executor.entry_gate,permission_token=composite)
    with exclusive_process(executor.entry_gate.lock_path):
        first=deployment_readiness(executor,store,CANDIDATE)
        assert first['permission_token']==composite
        crypto.configure({'target_dollars':'6.00'})
        changed=deployment_readiness(executor,store,CANDIDATE)
        assert changed['permission_token'] not in (composite,legacy)
        assert not UPDATER.readiness_allowed(changed,CANDIDATE,record,NOW,NOW,permission_token=composite)[0]


@pytest.mark.parametrize('mutate',[
    lambda store,crypto:crypto.configure({'target_dollars':'6.00'}),
    lambda store,crypto:crypto.configure({'enabled':False}),
    lambda store,crypto:crypto.configure({'symbols':['BTC/USD','ETH/USD']}),
    lambda store,crypto:store.event('strategy_settings',{'socrates':{'enabled':True}}),
])
def test_modified_family_configuration_cannot_use_legacy_migration_bridge(migration,mutate):
    executor,store,crypto,broker,record,legacy=migration
    mutate(store,crypto)
    with exclusive_process(executor.entry_gate.lock_path):
        result=deployment_readiness(executor,store,CANDIDATE)
        assert result['permission_token']!=legacy
        assert not UPDATER.readiness_allowed(result,CANDIDATE,record,NOW,NOW,permission_token=legacy)[0]


def test_family_change_during_legacy_candidate_broker_reads_fails_readiness(migration):
    executor,store,crypto,broker,record,legacy=migration
    def orders():
        crypto.configure({'target_dollars':'6.00'})
        return []
    broker.orders=orders
    with exclusive_process(executor.entry_gate.lock_path):
        result=deployment_readiness(executor,store,CANDIDATE)
    assert not result['ok'] and result['error']=='permission_changed'
    assert not broker.sent


def test_restored_default_boolean_event_during_reads_still_invalidates_migration(migration):
    executor,store,crypto,broker,record,legacy=migration
    broker.orders=lambda:(store.event('strategy_settings',{'socrates':{'enabled':True}}) or [])
    with exclusive_process(executor.entry_gate.lock_path):
        result=deployment_readiness(executor,store,CANDIDATE)
    assert not result['ok'] and result['error']=='permission_changed'


def test_legacy_bridge_requires_exclusive_lock_and_exact_candidate_revision(migration):
    executor,store,crypto,broker,record,legacy=migration
    assert deployment_readiness(executor,store,CANDIDATE)['permission_token']!=legacy
    with exclusive_process(executor.entry_gate.lock_path):
        assert deployment_readiness(executor,store,REVISION)['permission_token']!=legacy
        assert deployment_readiness(executor,store,'d'*40)['permission_token']!=legacy


def test_changed_master_permission_never_uses_original_legacy_token(migration):
    executor,store,crypto,broker,record,legacy=migration
    store.set_control(False)
    with exclusive_process(executor.entry_gate.lock_path):
        result=deployment_readiness(executor,store,CANDIDATE)
    assert result['permission_token']!=legacy
    assert not UPDATER.readiness_allowed(result,CANDIDATE,record,NOW,NOW,permission_token=legacy)[0]
