"""A crypto POST lost in transit must not leave coins unprotected or a market blocked forever."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
import pytest
from pivot.crypto_execution import CryptoRangeExecutor, ORDER_NOT_FOUND_CONFIRM_SECONDS
from pivot.crypto_store import CryptoStore
from pivot.feeds import FeedError
from pivot.portfolio import Portfolio
from pivot.store import Store
from pivot.tests.test_crypto_execution import FakeBroker, tick, MAIN_POLICY, POLICY_VERSION


@pytest.fixture(autouse=True)
def crypto_engine_enabled(monkeypatch):
    """These tests exercise the crypto engine as it runs when re-enabled (PIVOT_CRYPTO_PAUSED=0).

    4.5.1 pauses crypto by default; test_crypto_pause.py covers the paused release.
    """
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')


class LossyBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.lose, self.pending, self.arrive_at = set(), {}, None

    @staticmethod
    def _kind(payload):
        return 'entry' if payload['side'] == 'buy' else payload['type']

    def submit(self, payload):
        kind = self._kind(payload)
        if kind in self.lose:
            self.lose.discard(kind)
            self.sent.append(deepcopy(payload))
            if self.arrive_at is not None:
                self.pending[payload['client_order_id']] = payload
            raise FeedError('connection dropped before the request was sent')
        return super().submit(payload)

    def lookup(self, cid):
        if cid in self.pending and self.at >= self.arrive_at:
            payload = self.pending.pop(cid)
            super().submit(payload)
            self.sent.pop()
        return super().lookup(cid)


def runtime(tmp_path):
    main = Store(tmp_path / 'state.sqlite3')
    crypto = CryptoStore(main.path)
    portfolio = Portfolio(main)
    portfolio.register('range_reversal', crypto.active_trades)
    broker = LossyBroker()
    executor = CryptoRangeExecutor(broker, crypto, main, portfolio, now=lambda: broker.at)
    main.set_control(True, MAIN_POLICY, broker.account_data['account_ref'])
    crypto.configure({'enabled': True, 'symbols': ['BTC/USD'], 'target_dollars': '5.00',
                      'policy': POLICY_VERSION, 'account_ref': broker.account_data['account_ref']})
    return executor, broker, crypto, main


def advance(executor, broker, seconds, step=10):
    for _ in range(seconds // step):
        broker.at += timedelta(seconds=step)
        tick(executor)


def test_lost_stop_post_closes_verified_exposure_after_the_window(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    broker.lose = {'stop_limit'}
    tick(executor)
    assert [o['type'] for o in broker.sent] == ['limit', 'stop_limit'] and broker.holdings['BTC/USD'] > 0
    advance(executor, broker, 30)
    assert len(broker.sent) == 2 and crypto.incidents()  # Unknown-order incident, no duplicate sell.
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert broker.sent[-1]['type'] == 'market' and broker.sent[-1]['side'] == 'sell'
    assert broker.holdings['BTC/USD'] == 0 and not crypto.active_trades()
    assert not [i for i in crypto.incidents() if i['id'].endswith(':stop_unknown')]
    assert 'crypto_order_not_found' in [event['kind'] for event in main.events()]


def test_lost_exit_post_is_followed_by_the_next_exit_child(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    tick(executor)
    broker.lose = {'market'}
    broker.bid = broker.ask = D('110')  # Target reached.
    tick(executor)
    assert broker.canceled and broker.sent[-1]['type'] == 'market'
    sales = len(broker.sent)
    advance(executor, broker, 30)
    assert len(broker.sent) == sales
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert len(broker.sent) == sales + 1 and broker.sent[-1]['type'] == 'market'
    assert broker.holdings['BTC/USD'] == 0 and not crypto.active_trades()


def test_lost_entry_post_finishes_and_frees_the_market_after_the_window(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    broker.entry_mode = 'lost_unseen'
    tick(executor)
    assert len(broker.sent) == 1 and crypto.active_trade('BTC/USD')['stage'] == 'entering'
    advance(executor, broker, 30)
    assert crypto.active_trade('BTC/USD')['stage'] == 'entering'
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert not crypto.active_trades() and len(broker.sent) == 1
    assert not [i for i in crypto.incidents() if i['id'].endswith(':entry_unknown')]
    assert main.session_entry_allowance(broker.at)['used'] == 1
    assert crypto.history()[0]['stage'] == 'finished'


def test_stop_that_arrives_late_is_adopted(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    broker.lose = {'stop_limit'}
    broker.arrive_at = broker.at + timedelta(seconds=25)
    tick(executor)
    advance(executor, broker, 30)
    assert broker.book[broker.sent[1]['client_order_id']]['status'] == 'new'
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert len(broker.sent) == 2 and broker.holdings['BTC/USD'] > 0
    assert 'crypto_order_not_found' not in [event['kind'] for event in main.events()]


def test_resolution_waits_while_coins_are_reserved_by_an_invisible_sell(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    broker.lose = {'stop_limit'}
    tick(executor)
    original = broker.positions
    def reserved_positions():
        rows = original()
        for row in rows:
            row['qty_available'] = '0'
        return rows
    broker.positions = reserved_positions
    advance(executor, broker, 30 + ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert len(broker.sent) == 2 and broker.holdings['BTC/USD'] > 0
    broker.positions = original
    advance(executor, broker, 20)
    assert broker.sent[-1]['type'] == 'market' and broker.holdings['BTC/USD'] == 0
