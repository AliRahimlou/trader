"""Audit fixes: skipped shorts are not data errors, the submission budget starts at quote receipt,
and a one-tick crypto ask uptick does not retire an immediate limit entry."""
from datetime import timedelta
from decimal import Decimal as D, ROUND_UP
from pivot.crypto_execution import IOC_LIMIT_BUFFER, IOC_SETTLE_GRACE_SECONDS, MAX_ENTRY_DRIFT, rounded
from pivot.execution import Executor
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, ready, enable, NOW
from pivot.tests.test_crypto_execution import engine as crypto_engine, tick as crypto_tick  # noqa: F401


def test_unsizable_short_is_a_skipped_setup_not_invalid_data(tmp_path):
    broker = FakeBroker()
    broker.account_data['shorting_enabled'] = False
    store = Store(tmp_path / 'x.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready(direction='short'))
    assert not broker.sent and executor.enabled()
    assert 'cannot short' in executor.message
    check = store.execution_history(1, None)['entries'][0]
    assert check['outcome'] == 'waiting' and check['gate'] == 'short_eligibility'
    broker.account_data['shorting_enabled'] = True
    broker.at += timedelta(seconds=1)
    executor.tick(ready(at=broker.at, direction='short'))  # A $25 target cannot short one whole $100 share.
    assert not broker.sent and 'whole QQQ shares' in executor.message
    check = store.execution_history(1, None)['entries'][0]
    assert check['outcome'] == 'waiting' and check['gate'] == 'purchase_size'


class SlowBroker(FakeBroker):
    """Every broker read costs one second; the quote is already nine seconds old."""
    def _tax(self):
        self.at += timedelta(seconds=1)
    def account(self): self._tax(); return super().account()
    def positions(self): self._tax(); return super().positions()
    def orders(self): self._tax(); return super().orders()
    def clock(self): self._tax(); return super().clock()
    def quote(self, symbol):
        self._tax()
        q = super().quote(symbol)
        q['t'] = (self.at - timedelta(seconds=9)).isoformat()
        return q


def test_slow_broker_reads_after_an_older_quote_still_submit_the_entry(tmp_path):
    broker = SlowBroker()
    store = Store(tmp_path / 'x.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    for _ in range(3):
        executor.tick(ready(at=broker.at))
        if broker.sent:
            break
        broker.at += timedelta(seconds=5)
    assert [o['type'] for o in broker.sent] == ['market', 'stop'], executor.message


def test_one_tick_ask_uptick_before_submission_does_not_retire_the_crypto_entry(crypto_engine):
    e, b, store, _, _ = crypto_engine
    original_quote, calls = b.quote, []
    def quote_then_uptick(symbol):
        calls.append(symbol)
        result = original_quote(symbol)
        if len(calls) == 1:
            b.ask = b.ask + D('.01')  # One tick up between preflight and the pre-submit recheck.
        return result
    b.quote = quote_then_uptick
    crypto_tick(e)
    buys = [o for o in b.sent if o['side'] == 'buy']
    assert len(buys) == 1, e.message
    limit = D(buys[0]['limit_price'])
    assert b.ask == D('95.01') and limit >= b.ask
    assert limit == rounded(D('95') * (1 + IOC_LIMIT_BUFFER), D('.000000001'), ROUND_UP)
    assert limit <= D('95') * (1 + MAX_ENTRY_DRIFT)
    assert D('4.95') <= D(buys[0]['qty']) * limit <= D('5')
    assert store.active_trade('BTC/USD')['stage'] == 'open'


def test_pending_ioc_response_is_not_cancelled_before_the_venue_settles(crypto_engine):
    e, b, store, _, _ = crypto_engine
    b.entry_mode = 'partial_pending'  # POST response still pending: half filled so far.
    crypto_tick(e)
    assert not b.canceled and len(b.sent) == 1, e.message
    assert 'settling' in e.message
    b.fill(b.sent[0]['client_order_id'])  # The venue completes the immediate order moments later.
    b.at += timedelta(seconds=IOC_SETTLE_GRACE_SECONDS)
    crypto_tick(e)
    assert not b.canceled and b.sent[-1]['type'] == 'stop_limit'
    trade = store.active_trade('BTC/USD')
    assert trade['stage'] == 'open' and not trade.get('partial_entry')


def test_pending_ioc_response_is_cancelled_after_the_grace_or_when_live_is_off(crypto_engine):
    e, b, store, _, _ = crypto_engine
    b.entry_mode = 'partial_pending'
    crypto_tick(e)
    assert not b.canceled
    b.at += timedelta(seconds=IOC_SETTLE_GRACE_SECONDS)
    crypto_tick(e)
    assert b.canceled == [b.sent[0]['client_order_id']] and b.sent[-1]['type'] == 'stop_limit'
    assert store.active_trade('BTC/USD')['partial_entry']
