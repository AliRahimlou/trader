"""Durable stock payload reconciliation with stateful offline broker fixtures."""
from copy import deepcopy
from datetime import timedelta

import pytest

from pivot.execution import Executor, Waiting
from pivot.store import Store
from pivot.tests.test_execution import engine, enable, ready


@pytest.mark.parametrize('change', [
    {'type': 'limit'}, {'time_in_force': 'gtc'}, {'qty': '4'}, {'stop_price': '80'},
    {'filled_qty': '-1'}, {'filled_qty': '5'}, {'filled_qty': 'NaN'},
    {'id': 'different-broker-id'}, {'extended_hours': True}, {'order_class': 'bracket'},
    {'legs': [{'id': 'unowned-leg'}]}, {'status': None},
])
def test_inconsistent_stock_stop_is_not_accepted_as_protection(engine, change):
    executor, broker, store = engine
    enable(executor)
    executor.tick(ready())
    trade = store.active_trade()
    before = deepcopy(trade['ops']['stop']['last_seen'])
    broker.book[broker.sent[1]['client_order_id']].update(change)
    with pytest.raises(Waiting, match='Broker order'):
        executor._order(trade, 'stop')
    assert store.active_trade()['ops']['stop']['last_seen'] == before
    assert not store.control()['enabled']
    assert len(broker.sent) == 2 and not broker.canceled


def test_notional_request_must_still_match_notional(engine):
    executor, broker, store = engine
    enable(executor)
    executor.tick(ready())
    broker.book[broker.sent[0]['client_order_id']]['notional'] = '2500'
    with pytest.raises(Waiting, match='Broker order'):
        executor._order(store.active_trade(), 'entry')


@pytest.mark.parametrize('reported_qty', [None, '0.250000000'])
def test_notional_entry_accepts_broker_quantity_without_invented_share_target(engine, reported_qty):
    executor, broker, store = engine
    enable(executor)
    executor.tick(ready())
    broker.book[broker.sent[0]['client_order_id']]['qty'] = reported_qty
    result = executor._order(store.active_trade(), 'entry')
    assert result['filled_qty'] == '0.25' and store.control()['enabled']


def test_valid_broker_evidence_resumes_supervision_without_rearming_or_duplicate_post(engine):
    executor, broker, store = engine
    enable(executor)
    executor.tick(ready())
    cid = broker.sent[1]['client_order_id']
    original = deepcopy(broker.book[cid])
    broker.book[cid]['stop_price'] = '1'
    executor.tick({})
    assert not store.control()['enabled'] and len(broker.sent) == 2
    broker.book[cid] = original
    broker.at += timedelta(seconds=1)
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick({})
    assert store.active_trade()['stage'] == 'open'
    assert not store.control()['enabled'] and len(broker.sent) == 2
    assert store.active_trade()['order_validation']['stop']['resolved_at']


@pytest.mark.parametrize('order_type', ['stop', 'stop_limit', 'market'])
def test_buy_stop_allows_documented_conversion_but_retains_stop_and_quantity(engine, order_type):
    executor, broker, store = engine
    store.save({'sizing_mode': 'target', 'target_dollars': '100.00'})
    enable(executor)
    executor.tick(ready(direction='short'))
    assert len(broker.sent) == 2 and broker.sent[1]['side'] == 'buy'
    order = broker.book[broker.sent[1]['client_order_id']]
    order['type'] = order_type
    if order_type == 'stop_limit':
        order['limit_price'] = '112.75'  # Alpaca's documented 2.5% collar above $50.
    assert executor._order(store.active_trade(), 'stop')['type'] == order_type
    assert store.control()['enabled']


def test_buy_stop_rejects_changed_stop_even_when_type_conversion_is_legal(engine):
    executor, broker, store = engine
    store.save({'sizing_mode': 'target', 'target_dollars': '100.00'})
    enable(executor)
    executor.tick(ready(direction='short'))
    order = broker.book[broker.sent[1]['client_order_id']]
    order.update(type='stop_limit', stop_price='120', limit_price='123')
    with pytest.raises(Waiting, match='Broker order'):
        executor._order(store.active_trade(), 'stop')


def test_sell_stop_does_not_accept_stop_limit_substitution(engine):
    executor, broker, store = engine
    enable(executor)
    executor.tick(ready())
    order = broker.book[broker.sent[1]['client_order_id']]
    order.update(type='stop_limit', limit_price='89')
    with pytest.raises(Waiting, match='Broker order'):
        executor._order(store.active_trade(), 'stop')


def test_entry_response_mismatch_cannot_authorize_a_stop_or_second_entry_post(engine):
    executor, broker, store = engine
    original_submit = broker.submit
    def malformed(payload):
        response = original_submit(payload)
        if payload['client_order_id'].endswith('-entry'):
            response['time_in_force'] = 'gtc'
        return response
    broker.submit = malformed
    enable(executor)
    executor.tick(ready())
    assert len(broker.sent) == 1 and store.active_trade()['stage'] == 'entering'
    assert not store.control()['enabled']
    executor.tick({})  # Broker lookup now returns the genuine DAY order.
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert store.active_trade()['stage'] == 'open'
