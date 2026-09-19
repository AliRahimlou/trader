"""Deployment must account for every strategy's saved authorization and intent."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest

from pivot.deployment import EntryGate, deployment_readiness, _control_token, _deployment_permission
from pivot.execution import Executor
from pivot.portfolio import Portfolio
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable


@pytest.fixture
def subject(tmp_path):
    store = Store(tmp_path/'main.db')
    broker = FakeBroker()
    executor = Executor(broker, store, now=lambda:broker.at)
    executor.entry_gate = EntryGate(tmp_path/'deployment.lock')
    portfolio = Portfolio(store)
    pending = []
    portfolio.register('range_reversal', lambda:deepcopy(pending))
    executor.portfolio = portfolio
    enable(executor)
    permission = {'strategy_selection':{'socrates':True}, 'crypto_control':{
        'enabled':True, 'target_dollars':'5.00', 'symbols':['BTC/USD'],
        'policy':'test-crypto-policy', 'account_ref':'private-crypto-identity'}, 'generation':1}
    store.deployment_permission = lambda:{'control':store.control(), **deepcopy(permission)}
    return executor, store, broker, pending, permission


def read(subject):
    executor, store, *_ = subject
    return deployment_readiness(executor, store, 'a'*40)


def test_crypto_prepared_and_uncertain_intents_prevent_flat_deployment(subject):
    executor, store, broker, pending, _ = subject
    pending.append({'id':'private-intent-id', 'account_ref':'fake-account-only', 'symbol':'BTC/USD',
                    'stage':'entering', 'amount':'5.00', 'ops':{'entry':{'state':'attempted'}}})
    result=read(subject)
    assert result['ok'] and result['active_trade'] is True
    assert result['positions_count']==result['orders_count']==0
    assert 'private-intent-id' not in json.dumps(result)
    assert 'private-crypto-identity' not in json.dumps(result)
    assert not broker.sent and not broker.canceled
    pending.clear()
    assert read(subject)['active_trade'] is False


@pytest.mark.parametrize('mutation',[
    lambda p:p['strategy_selection'].update(socrates=False),
    lambda p:p['crypto_control'].update(enabled=False),
    lambda p:p['crypto_control'].update(target_dollars='6.00'),
    lambda p:p['crypto_control'].update(symbols=['BTC/USD','ETH/USD']),
    lambda p:p['crypto_control'].update(account_ref='other-secret-identity'),
    lambda p:p.update(generation=2),
])
def test_all_strategy_permission_changes_during_reads_invalidate_deployment(subject,mutation):
    executor,store,broker,_,permission=subject
    before_control=store.control()
    def orders():
        mutation(permission)
        return []
    broker.orders=orders
    result=read(subject)
    assert not result['ok'] and result['error']=='permission_changed'
    assert store.control()==before_control
    assert 'secret' not in json.dumps(result)
    assert not broker.sent and not broker.canceled


def test_composite_permission_hash_is_stable_and_order_independent(subject):
    executor,store,broker,_,permission=subject
    first=read(subject)
    permission['crypto_control']=dict(reversed(list(permission['crypto_control'].items())))
    second=read(subject)
    assert first['permission_token']==second['permission_token']
    assert len(first['permission_token'])==64
    assert first['permission_token']!=_control_token(store.control())
    assert first['ok'] and second['ok']


def test_unreadable_crypto_ledger_does_not_claim_empty_account(subject):
    executor,store,broker,_,_=subject
    executor.portfolio.active_trades=lambda:(_ for _ in ()).throw(RuntimeError('secret-storage-error'))
    result=read(subject)
    assert not result['ok'] and result['error']=='broker_read_failed'
    assert result['active_trade'] is None
    assert result['positions_count'] is None and result['orders_count'] is None
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('value',[None,{},'unknown',[None]])
def test_malformed_aggregate_ledger_blocks_deployment(subject,value):
    executor,*_=subject
    executor.portfolio.active_trades=lambda:value
    assert read(subject)['ok'] is False


@pytest.mark.parametrize('patch',[
    {'strategy_selection':{'socrates':'true'}},
    {'crypto_control':{'enabled':'true'}},
    {'generation':True},
    {'generation':-1},
    {'settings':None},
    {'unexpected':'secret'},
])
def test_invalid_composite_permission_never_returns_readiness(subject,patch):
    *_, permission=subject
    permission.update(patch)
    result=read(subject)
    assert not result['ok'] and result['permission_token'] is None


def test_legacy_store_without_multi_strategy_method_retains_original_token():
    control={'enabled':False, 'policy':None, 'account_ref':None}
    store=SimpleNamespace(control=lambda:deepcopy(control))
    assert _deployment_permission(store)==(control,_control_token(control))
