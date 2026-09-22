"""A broker POST lost in transit must not leave QQQ exposure unmanaged forever."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pivot.execution import Executor, ORDER_NOT_FOUND_CONFIRM_SECONDS
from pivot.feeds import FeedError
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, ready, enable, NOW


class LossyBroker(FakeBroker):
    """Drops selected POSTs before they reach the venue; can deliver them later."""
    def __init__(self):
        super().__init__()
        self.lose, self.pending, self.arrive_at = set(), {}, None

    @staticmethod
    def _kind(payload):
        return 'entry' if payload['client_order_id'].endswith('-entry') else payload['type']

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
            self.sent.pop()  # Delivery of the original request is not a second submission.
        return super().lookup(cid)


def runtime(tmp_path):
    broker = LossyBroker()
    store = Store(tmp_path / 'execution.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def advance(executor, broker, seconds, step=5):
    for _ in range(seconds // step):
        broker.at += timedelta(seconds=step)
        executor.tick({})


def test_lost_stop_post_closes_the_unprotected_position_after_the_window(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.lose = {'stop'}
    executor.tick(ready())
    assert [o['type'] for o in broker.sent] == ['market', 'stop'] and broker.position_data
    advance(executor, broker, 30)
    assert len(broker.sent) == 2  # No duplicate stop while the outcome is uncertain.
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert broker.sent[-1]['type'] == 'market' and broker.sent[-1]['side'] == 'sell'
    assert not broker.position_data and store.active_trade() is None
    kinds = [event['kind'] for event in store.events()]
    assert 'order_not_found' in kinds and 'trade_finished' in kinds
    assert not executor.enabled()  # Protection failure still pauses new entries.


def test_lost_exit_post_is_followed_by_a_second_exit(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready())
    broker.lose = {'market'}
    broker.bid = broker.ask = '111'  # Target reached.
    executor.tick({})  # Cancel the stop first.
    assert broker.canceled and len(broker.sent) == 2
    executor.tick({})  # Confirmed cancellation; the market exit POST is lost.
    assert broker.sent[-1]['type'] == 'market' and broker.sent[-1]['side'] == 'sell'
    sales = len(broker.sent)
    advance(executor, broker, 30)
    assert len(broker.sent) == sales  # No competing sale while the first is uncertain.
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert len(broker.sent) == sales + 1 and broker.sent[-1]['type'] == 'market'
    assert not broker.position_data and store.active_trade() is None


def test_lost_entry_post_finishes_without_a_fill_and_keeps_the_allowance_consumed(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.entry_mode = 'lost_unseen'
    executor.tick(ready())
    assert len(broker.sent) == 1 and store.active_trade()['stage'] == 'entering'
    advance(executor, broker, 30)
    assert store.active_trade()['stage'] == 'entering'
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert store.active_trade() is None and len(broker.sent) == 1
    assert store.session_entry_allowance(broker.at)['used'] == 1
    assert executor.enabled()  # A request that never arrived is not a rejection.
    broker.entry_mode = 'filled'
    executor.tick(ready(broker.at))  # A new opportunity; the consumed event is not replayed.
    assert [o['type'] for o in broker.sent] == ['market', 'market', 'stop'] and store.active_trade()['stage'] == 'open'


def test_stop_that_arrives_late_is_adopted_instead_of_closing_the_position(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.lose = {'stop'}
    broker.arrive_at = NOW + timedelta(seconds=25)
    executor.tick(ready())
    advance(executor, broker, 30)
    assert broker.book[broker.sent[1]['client_order_id']]['status'] == 'new'
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert len(broker.sent) == 2 and broker.position_data
    assert 'order_not_found' not in [event['kind'] for event in store.events()]


def test_resolution_waits_while_an_unknown_qqq_order_or_position_exists(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.entry_mode = 'lost_unseen'
    executor.tick(ready())
    broker.position_data = [dict(symbol='QQQ', qty='0.02', side='long')]
    advance(executor, broker, 30 + ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert store.active_trade()['stage'] == 'entering' and len(broker.sent) == 1
    broker.position_data = []
    broker.book['foreign'] = {'id': 'foreign', 'client_order_id': 'someone-else', 'symbol': 'QQQ', 'side': 'sell',
                              'qty': '1', 'filled_qty': '0', 'status': 'new', 'type': 'limit', 'time_in_force': 'day'}
    advance(executor, broker, 10)
    assert store.active_trade()['stage'] == 'entering'
    del broker.book['foreign']
    advance(executor, broker, 10)
    assert store.active_trade() is None
