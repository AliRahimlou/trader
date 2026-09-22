"""Socrates order payloads against Alpaca Trading API v2 rules (4.5.1 audit).

Every payload the executor can POST (QQQ and PSQ entries by notional or whole
shares, DAY sell stops with fractional quantities, DAY market sells) is checked
by ``order_payload_violations``; the fake venue also rejects the sequences Alpaca
rejects (an order while an opposite-side order is open in the symbol, a sale
while the shares are held by an open stop). FakeBroker only; no network.
See docs/order-path-audit-4.5.1.md.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN

import pytest

from pivot.execution import (CLIENT_ORDER_ID_MAX, Executor, order_payload_violations, proxy_translation,
                             quantity_text, valid_price_increment)
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, identify_event, ready

OPEN = {'new', 'accepted', 'pending_new', 'partially_filled', 'pending_cancel', 'held'}


class AlpacaLikeBroker(FakeBroker):
    """FakeBroker with Alpaca's 9-decimal fills, slow cancels and order-sequence rejections."""
    def __init__(self):
        super().__init__()
        self.violations = []
        self.slow_cancel = False

    def submit(self, payload):
        problems = order_payload_violations(payload)
        opened = [o for o in self.book.values() if o['symbol'] == payload['symbol'] and o['status'] in OPEN]
        if any(o['side'] != payload['side'] for o in opened):
            problems.append('potential wash trade: opposite-side order open in ' + payload['symbol'])
        if payload['side'] == 'sell' and any(o['side'] == 'sell' for o in opened):
            problems.append('shares already held by an open sell order in ' + payload['symbol'])
        self.violations.extend(problems)
        return super().submit(payload)

    def fill(self, client_id, quantity=None):
        order = self.book[client_id]
        if Decimal(order['qty']).as_tuple().exponent < -9:
            order['qty'] = str(Decimal(order['qty']).quantize(Decimal('.000000001'), rounding=ROUND_DOWN))
        super().fill(client_id, quantity)

    def cancel(self, oid):
        if self.slow_cancel and self.book[oid]['status'] not in ('filled', 'canceled'):
            self.canceled.append(oid)
            self.book[oid]['status'] = 'pending_cancel'
            return
        super().cancel(oid)


def runtime(tmp_path, *, bid='99.99', ask='100'):
    broker = AlpacaLikeBroker()
    broker.bid, broker.ask = bid, ask
    store = Store(tmp_path / 'conformance.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def settle(executor, broker, ticks=4):
    for _ in range(ticks):
        broker.at += timedelta(seconds=5)
        executor.tick({})  # Management only: no new setup once the trade finishes.


@pytest.mark.parametrize('direction,exit_kind', [('long', 'target'), ('long', 'session_close'),
                                                 ('short', 'target'), ('short', 'stop')])
def test_every_lifecycle_payload_conforms_and_identifiers_are_unique(tmp_path, direction, exit_kind):
    executor, broker, store = runtime(tmp_path, bid='100' if direction == 'short' else '99.99',
                                      ask='100.01' if direction == 'short' else '100')
    executor.tick(ready(direction=direction))
    symbol = 'PSQ' if direction == 'short' else 'QQQ'
    assert [(o['symbol'], o['side'], o['type']) for o in broker.sent] == [(symbol, 'buy', 'market'), (symbol, 'sell', 'stop')]
    if exit_kind == 'target':
        if direction == 'short':
            broker.quotes['PSQ'] = ('38.50', '38.51')
        else:
            broker.bid, broker.ask = '110', '110.01'
    elif exit_kind == 'stop':
        broker.quotes['PSQ'] = ('31.50', '31.51')
    else:
        broker.close_in = 250
    broker.slow_cancel = True
    settle(executor, broker, 2)
    sells = [o for o in broker.sent if o['type'] == 'market' and o['side'] == 'sell']
    assert sells == [], 'No sale may be sent while the protective stop is still open'
    for order in broker.book.values():
        if order['status'] == 'pending_cancel':
            order['status'] = 'canceled'
    settle(executor, broker, 3)
    assert broker.sent[-1]['side'] == 'sell' and broker.sent[-1]['type'] == 'market'
    assert store.active_trade() is None and not broker.position_data
    assert broker.violations == []
    identifiers = [o['client_order_id'] for o in broker.sent]
    assert len(set(identifiers)) == len(identifiers)
    assert all(len(identifier) <= CLIENT_ORDER_ID_MAX for identifier in identifiers)
    entry, stop = broker.sent[0], broker.sent[1]
    assert entry['notional'] == '25.00' and 'qty' not in entry and entry['time_in_force'] == 'day'
    assert entry['extended_hours'] is False and stop['extended_hours'] is False
    assert valid_price_increment(stop['stop_price']) and Decimal(stop['qty']) == Decimal(broker.sent[-1]['qty'])


def test_whole_share_proxy_entry_conforms(tmp_path):
    executor, broker, store = runtime(tmp_path, bid='100', ask='100.01')
    store.save({'sizing_mode': 'target', 'target_dollars': '100.00'})
    broker.assets['PSQ'] = {'fractionable': False}
    broker.quotes['PSQ'] = ('33.29', '33.30')
    executor.tick(ready(direction='short'))
    assert broker.sent[0]['qty'] == '3' and 'notional' not in broker.sent[0]
    assert broker.sent[1]['qty'] == '3' and broker.violations == []


def test_entry_never_buys_while_an_own_sell_stop_is_open(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready())
    assert store.active_trade()['stage'] == 'open' and len(broker.sent) == 2
    fresh = ready(NOW + timedelta(minutes=1))
    fresh['setup']['event_zone'] = {**fresh['setup']['event_zone'], 'low': 100.5, 'high': 100.5}
    identify_event(fresh['setup'])
    broker.at = NOW + timedelta(minutes=1)
    executor.tick(fresh)
    assert len(broker.sent) == 2 and broker.violations == []


def test_entry_with_a_sub_penny_stop_is_skipped_before_any_order(tmp_path):
    executor, broker, store = runtime(tmp_path)
    snapshot = ready()
    snapshot['setup']['stop'] = 89.995
    executor.tick(snapshot)
    assert broker.sent == [] and store.active_trade() is None
    assert 'whole cents' in executor.message


@pytest.mark.parametrize('ask', ['8.37', '19.99', '33.2', '35', '41.07', '72.44'])
@pytest.mark.parametrize('geometry', [(100, 110, 90), (675.12, 677.93, 668.44), (500, '500.51', '498.99')])
def test_translated_psq_stop_and_target_are_whole_cents(ask, geometry):
    _, stop, target = proxy_translation(ask, *geometry)
    assert valid_price_increment(stop) and valid_price_increment(target)


def test_quantity_text_is_plain_decimal():
    assert str(Decimal('0.0000001')) == '1E-7'  # Why the helper exists.
    assert quantity_text('0.0000001') == '0.0000001'
    assert quantity_text('-0.714285714') == '0.714285714'
    assert order_payload_violations({'symbol': 'QQQ', 'side': 'sell', 'type': 'market', 'time_in_force': 'day',
                                     'extended_hours': False, 'qty': '1E-7', 'client_order_id': 'pvt-x-exit0'})


BASE = {'symbol': 'QQQ', 'side': 'buy', 'type': 'market', 'time_in_force': 'day', 'extended_hours': False,
        'notional': '15.00', 'client_order_id': 'pvt-' + 'a' * 24 + '-entry'}
STOP = {'symbol': 'PSQ', 'side': 'sell', 'type': 'stop', 'time_in_force': 'day', 'extended_hours': False,
        'qty': '0.431034482', 'stop_price': '31.50', 'client_order_id': 'pvt-' + 'a' * 24 + '-stop'}


def test_conforming_payloads_have_no_violations():
    assert order_payload_violations(BASE) == [] and order_payload_violations(STOP) == []
    assert order_payload_violations({**BASE, 'qty': '3'}) != []  # Both qty and notional present.
    whole = {key: value for key, value in BASE.items() if key != 'notional'}
    assert order_payload_violations({**whole, 'qty': '3'}) == []


@pytest.mark.parametrize('change', [
    {'time_in_force': 'gtc'}, {'extended_hours': True}, {'type': 'limit'}, {'symbol': 'SQQQ'},
    {'notional': '0.99'}, {'notional': '15.001'}, {'side': 'sell'}, {'client_order_id': 'x' * 129},
    {'qty': '1'}, {'stop_price': '99.00'},
])
def test_entry_payload_rule_breaks_are_reported(change):
    assert order_payload_violations({**BASE, **change})


@pytest.mark.parametrize('change', [
    {'stop_price': '31.505'}, {'stop_price': '0'}, {'qty': '0.4310344821'}, {'qty': '0'}, {'side': 'buy'},
    {'notional': '15.00'}, {'time_in_force': 'gtc'}, {'extended_hours': True},
])
def test_stop_payload_rule_breaks_are_reported(change):
    assert order_payload_violations({**STOP, **change})


def test_price_increments_follow_alpaca_sub_penny_rules():
    assert valid_price_increment('31.50') and valid_price_increment('0.1234')
    assert not valid_price_increment('31.505') and not valid_price_increment('0.12345')
    assert not valid_price_increment('-1') and not valid_price_increment('abc')
