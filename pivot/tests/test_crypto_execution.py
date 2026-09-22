"""Stateful fake exchange tests. No network, credentials or real orders."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import pytest

from pivot.broker import BrokerRejected
from pivot.crypto_execution import CryptoRangeExecutor, POLICY_VERSION, IOC_SETTLE_GRACE_SECONDS, checked_signal, CryptoWaiting, rounded
from pivot.crypto_store import CryptoStore
from pivot.feeds import FeedError
from pivot.models import Bar, Market
from pivot.range_reversal import analyze
from pivot.policy import POLICY_VERSION as MAIN_POLICY
from pivot.portfolio import Portfolio
from pivot.store import Store

D = Decimal
NOW = datetime(2026, 9, 19, 8, 10, 10, tzinfo=timezone.utc)


def signal(symbol='BTC/USD', at=NOW, *, short=False):
    origin = at.replace(hour=4, minute=0, second=0, microsecond=0)
    bars = [Bar(origin + timedelta(minutes=5*(i+1)), 5, 100, 110, 90, 100) for i in range(48)]
    closes = (111, 105) if short else (89, 95)
    for i, close in enumerate(closes):
        bars.append(Bar(origin + timedelta(minutes=245+i*5), 5, close, close+1, close-1, close))
    m = Market(symbol, {5: bars}, 'alpaca_crypto_us', True, at)
    return analyze(m, at, provenance={'source': m.source, 'symbol': symbol, 'native': True, 'timeframe_minutes': 5})


class FakeBroker:
    def __init__(self):
        self.at = NOW
        self.account_data = {'account_ref': 'test-crypto-account', 'mode': 'live', 'status': 'ACTIVE',
             'crypto_status': 'ACTIVE', 'non_marginable_buying_power': '100',
             'trading_blocked': False, 'account_blocked': False, 'trade_suspended_by_user': False}
        self.bid, self.ask = D('94.99'), D('95')
        self.book, self.holdings = {}, {}
        self.sent, self.canceled = [], []
        self.entry_mode = 'filled'
        self.stop_mode = 'new'
        self.exit_mode = 'filled'
        self.cancel_mode = 'cancel'
        self.fee = D('.0025')
        self.foreign_orders, self.foreign_positions = [], []
        self.lookup_errors = False
        self.quote_age = 0
        self.reject_stop = False
        self.asset_overrides = {}

    def account(self): return deepcopy(self.account_data)
    def asset(self, symbol):
        return {'symbol': symbol, 'class': 'crypto', 'status': 'active', 'tradable': True,
                'fractionable': True, 'shortable': False, 'marginable': False,
                'min_order_size': '.00001', 'min_trade_increment': '.000000001',
                'price_increment': '.000000001', **self.asset_overrides}
    def quote(self, symbol):
        return {'symbol': symbol, 'source': 'alpaca_crypto_us', 'bp': str(self.bid), 'ap': str(self.ask),
                'bs': '1', 'as': '1', 't': (self.at-timedelta(seconds=self.quote_age)).isoformat()}
    def orders(self):
        return deepcopy([o for o in self.book.values() if o['status'] not in ('filled', 'canceled', 'expired', 'rejected')] + self.foreign_orders)
    def positions(self):
        rows = []
        for symbol, qty in self.holdings.items():
            if qty:
                reserved = sum((D(o['qty'])-D(o['filled_qty']) for o in self.orders()
                                if o['symbol'] == symbol and o['side'] == 'sell'), D(0))
                rows.append({'symbol': symbol, 'qty': str(qty), 'qty_available': str(max(D(0), qty-reserved)), 'side': 'long'})
        return rows + deepcopy(self.foreign_positions)
    def lookup(self, cid):
        if self.lookup_errors:
            raise FeedError('Unreachable')
        return deepcopy(self.book.get(cid))
    def fill(self, cid, cumulative=None):
        o = self.book[cid]
        prior, requested = D(o['filled_qty']), D(o['qty'])
        fills = requested if cumulative is None else D(cumulative)
        delta = fills-prior
        if delta:
            change = delta if o['side'] == 'buy' else -delta
            if o['side'] == 'buy':
                change -= rounded(delta*self.fee, D('.000000001'))
            self.holdings[o['symbol']] = self.holdings.get(o['symbol'], D(0)) + change
        o.update(filled_qty=str(fills), filled_avg_price=str(self.ask if o['side'] == 'buy' else self.bid),
                 status='filled' if fills == requested else 'partially_filled')
    def submit(self, payload):
        self.sent.append(deepcopy(payload))
        cid = payload['client_order_id']
        if payload['side'] == 'buy' and self.entry_mode == 'reject' or payload['type'] == 'stop_limit' and self.reject_stop:
            raise BrokerRejected('Fixture rejection')
        if payload['side'] == 'buy' and self.entry_mode == 'lost_unseen':
            raise FeedError('Unknown outcome')
        o = self.book[cid] = {**payload, 'id': cid, 'status': 'new', 'filled_qty': '0', 'filled_avg_price': None}
        if payload['side'] == 'buy':
            if self.entry_mode in ('filled', 'lost_accepted'):
                self.fill(cid)
            elif self.entry_mode == 'partial':
                self.fill(cid, str(rounded(D(payload['qty'])/2, D('.000000001'))))
                o['status'] = 'canceled'
            elif self.entry_mode == 'partial_pending':
                self.fill(cid, str(rounded(D(payload['qty'])/2, D('.000000001'))))
            elif self.entry_mode == 'no_fill':
                o['status'] = 'canceled'
        elif payload['type'] == 'market':
            if self.exit_mode == 'filled': self.fill(cid)
            elif self.exit_mode == 'partial':
                self.fill(cid, str(rounded(D(payload['qty'])/2, D('.000000001'))))
                o['status'] = 'canceled'
        else:
            o['status'] = self.stop_mode
        if payload['side'] == 'buy' and self.entry_mode == 'lost_accepted':
            raise FeedError('Response lost after fill')
        return self.lookup(cid)
    def cancel(self, oid):
        self.canceled.append(oid)
        if self.cancel_mode == 'unknown': raise FeedError('Cancel uncertain')
        if self.cancel_mode == 'pending': return
        o = next(o for o in self.book.values() if o['id'] == oid)
        if self.cancel_mode == 'fill' and o['status'] != 'filled': self.fill(o['client_order_id'])
        elif o['status'] != 'filled': o['status'] = 'canceled'


@pytest.fixture
def engine(tmp_path):
    main = Store(tmp_path/'state.sqlite3')
    crypto = CryptoStore(main.path)
    portfolio = Portfolio(main)
    portfolio.register('range_reversal', crypto.active_trades)
    broker = FakeBroker()
    e = CryptoRangeExecutor(broker, crypto, main, portfolio, now=lambda: broker.at)
    main.set_control(True, MAIN_POLICY, broker.account_data['account_ref'])
    crypto.configure({'enabled': True, 'symbols': ['BTC/USD'], 'target_dollars': '5.00',
                      'policy': POLICY_VERSION, 'account_ref': broker.account_data['account_ref']})
    return e, broker, crypto, main, portfolio


def tick(e, symbol='BTC/USD'):
    e.tick({symbol: signal(symbol, e.now())})


def test_live_five_dollar_entry_receives_net_crypto_and_installs_native_stop(engine):
    e, b, store, _, _ = engine
    tick(e)
    assert len(b.sent) == 2, e.message
    buy, stop = b.sent
    assert buy['type'] == 'limit' and buy['time_in_force'] == 'ioc'
    assert D('4.95') <= D(buy['qty'])*D(buy['limit_price']) <= 5
    assert stop['type'] == 'stop_limit' and stop['time_in_force'] == 'gtc'
    assert D(stop['qty']) == b.holdings['BTC/USD'] < D(buy['qty'])
    trade = store.active_trade('BTC/USD')
    assert trade['stage'] == 'open' and D(trade['target']) == D('109')
    assert trade['ops']['stop']['last_seen']['status'] == 'new'


def test_view_has_no_execution_effect_and_repeated_poll_never_duplicates_entry(engine):
    e, b, store, _, _ = engine
    tick(e)
    for _ in range(5):
        e.snapshot(); tick(e)
    assert len(b.sent) == 2
    assert len(store.active_trades()) == 1


def test_target_cancels_stop_then_sells_net_position_and_finishes(engine):
    e, b, store, _, _ = engine
    tick(e)
    b.bid = b.ask = D('110')
    tick(e)
    assert len(b.canceled) == 1
    assert b.sent[-1]['type'] == 'market' and b.sent[-1]['side'] == 'sell'
    assert b.sent[-1]['qty'] == b.sent[1]['qty']
    assert b.holdings['BTC/USD'] == 0
    assert store.active_trades() == []
    assert store.history()[0]['stage'] == 'finished'


def test_native_stop_fill_finishes_without_second_exit(engine):
    e, b, store, _, _ = engine
    tick(e)
    b.fill(b.sent[1]['client_order_id'])
    tick(e)
    assert len(b.sent) == 2 and not store.active_trades()


def test_gap_below_stop_cancels_native_stop_then_market_exits(engine):
    e, b, store, _, _ = engine
    tick(e)
    b.bid = b.ask = D('80')
    tick(e)
    assert len(b.sent) == 3 and b.canceled
    assert not store.active_trades()


def test_fill_during_stop_cancel_does_not_oversell(engine):
    e, b, store, _, _ = engine
    tick(e)
    b.cancel_mode = 'fill'; b.bid = b.ask = D('110')
    tick(e)
    assert len(b.sent) == 2 and not store.active_trades()


@pytest.mark.parametrize('mode', ['pending', 'unknown'])
def test_unconfirmed_stop_cancel_never_sends_competing_sell(engine, mode):
    e, b, store, _, _ = engine
    tick(e)
    b.cancel_mode = mode; b.bid = b.ask = D('110')
    tick(e); tick(e)
    assert len(b.sent) == 2 and store.active_trade('BTC/USD')['stage'] == 'exiting'


def test_partial_entry_is_protected_for_only_actual_net_fill(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'partial'
    tick(e)
    trade = store.active_trade('BTC/USD')
    assert len(b.sent) == 2, e.message
    assert trade['partial_entry'] and D(b.sent[1]['qty']) == b.holdings['BTC/USD']
    assert D(b.sent[1]['qty']) < D(b.sent[0]['qty'])/2


def test_partial_unsettled_entry_cancels_before_protecting(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'partial_pending'
    tick(e)
    assert not b.canceled  # The venue gets a short grace to settle the immediate order.
    b.at += timedelta(seconds=IOC_SETTLE_GRACE_SECONDS); tick(e)
    assert b.canceled == [b.sent[0]['client_order_id']]
    assert len(b.sent) == 2 and store.active_trade('BTC/USD')['partial_entry']


def test_unknown_entry_submission_is_not_replayed_after_restart(engine):
    e, b, store, main, portfolio = engine
    b.entry_mode = 'lost_unseen'; tick(e)
    assert len(b.sent) == 1
    e = CryptoRangeExecutor(b, store, main, portfolio, now=lambda: b.at)
    b.at += timedelta(seconds=31)
    tick(e); tick(e)
    assert len(b.sent) == 1 and store.incidents()
    assert store.active_trade('BTC/USD')['ops']['entry']['state'] == 'attempted'


def test_unknown_accepted_entry_recovers_by_id_and_protects(engine):
    e, b, store, main, portfolio = engine
    b.entry_mode = 'lost_accepted'; tick(e)
    assert len(b.sent) == 1
    e = CryptoRangeExecutor(b, store, main, portfolio, now=lambda: b.at)
    tick(e)
    assert len(b.sent) == 2 and store.active_trade('BTC/USD')['stage'] == 'open'


def test_no_fill_consumes_event_and_releases_active_slot(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'no_fill'; tick(e); tick(e)
    assert len(b.sent) == 1 and not store.active_trades()


def test_rejected_entry_is_finished_and_incident_persists_without_repeating(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'reject'; tick(e); tick(e)
    assert len(b.sent) == 1 and not store.active_trades() and store.incidents()


def test_rejected_stop_triggers_cancellation_safe_market_recovery(engine):
    e, b, store, _, _ = engine
    b.reject_stop = True; tick(e)
    assert len(b.sent) == 3, e.message
    assert b.sent[-1]['type'] == 'market' and not store.active_trades() and store.incidents()


def test_off_keeps_existing_stop_and_target_management_running(engine):
    e, b, store, main, _ = engine
    tick(e)
    main.set_control(False)
    b.bid = b.ask = D('110')
    tick(e)
    assert not e.enabled() and len(b.sent) == 3 and not store.active_trades()


def test_crypto_family_off_cancels_unfinished_entry_then_protects_partial(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'partial_pending'; b.cancel_mode = 'pending'
    tick(e)
    store.configure({'enabled': False}); b.cancel_mode = 'cancel'; b.at += timedelta(seconds=5)
    tick(e)
    assert len(b.sent) == 2 and store.active_trade('BTC/USD')['stage'] == 'open'


def test_short_setup_is_reported_and_never_replaced_with_buy(engine):
    e, b, _, _, _ = engine
    e.tick({'BTC/USD': signal(short=True)})
    assert not b.sent and 'cannot open short' in e.message


@pytest.mark.parametrize('mutate', [
    lambda a: a.update(source='yahoo'),
    lambda a: a.update(signal_ready=False),
    lambda a: a.update(rule_version='old'),
    lambda a: a['current_event'].update(event_id='forged'),
    lambda a: a['current_event'].update(current=False),
    lambda a: a['current_event'].update(stop_basis='other'),
    lambda a: a['current_event'].update(target=200),
    lambda a: a['current_event'].update(stop=96),
    lambda a: a['range'].update(native_candle_count=47),
    lambda a: a['range'].update(high=94),
    lambda a: a.update(observed_at=(NOW-timedelta(seconds=91)).isoformat()),
    lambda a: a.update(analyzed_at=(NOW+timedelta(seconds=1)).isoformat()),
    lambda a: a.update(latest_bar_at=(NOW-timedelta(minutes=5)).isoformat()),
    lambda a: a['session'].update(start_at=(NOW-timedelta(hours=4)).isoformat()),
])
def test_untrusted_or_stale_signal_rejected_before_broker_orders(engine, mutate):
    e, b, _, _, _ = engine
    a = signal(); mutate(a)
    e.tick({'BTC/USD': a})
    assert not b.sent


@pytest.mark.parametrize('change', [
    lambda b: b.account_data.update(crypto_status='INACTIVE'),
    lambda b: b.account_data.update(trading_blocked=True),
    lambda b: b.account_data.update(mode='paper'),
    lambda b: b.account_data.update(account_ref='other'),
    lambda b: b.account_data.update(non_marginable_buying_power='4.99'),
    lambda b: setattr(b, 'quote_age', 16),
    lambda b: setattr(b, 'ask', D('96')),
    lambda b: setattr(b, 'bid', D('90')),
    lambda b: b.asset_overrides.update(min_order_size='1'),
    lambda b: b.asset_overrides.update(tradable=False),
    lambda b: b.asset_overrides.update(class_='unrelated', **{'class': 'us_equity'}),
])
def test_account_asset_price_size_and_data_blocks_send_nothing(engine, change):
    e, b, _, _, _ = engine; change(b); tick(e); assert not b.sent


def test_foreign_position_blocks_entry_without_adoption(engine):
    e, b, store, _, _ = engine
    b.holdings['BTC/USD'] = D('.01')
    tick(e)
    assert not b.sent and not store.active_trades()


def test_foreign_order_blocks_entry(engine):
    e, b, _, _, _ = engine
    b.foreign_orders = [{'id': 'manual', 'symbol': 'BTC/USD', 'client_order_id': 'manual', 'side': 'buy', 'status': 'new'}]
    tick(e); assert not b.sent


def test_two_symbols_can_hold_separate_protected_positions(engine):
    e, b, store, _, _ = engine
    store.configure({'symbols': ['BTC/USD', 'ETH/USD']})
    e.tick({s: signal(s) for s in ['BTC/USD', 'ETH/USD']})
    assert len(b.sent) == 2  # One eligible entry preflight per worker cycle.
    e.tick({s: signal(s) for s in ['BTC/USD', 'ETH/USD']})
    assert len(b.sent) == 4, e.message
    assert {t['symbol'] for t in store.active_trades()} == {'BTC/USD', 'ETH/USD'}


def test_foreign_position_change_during_open_trade_pauses_and_never_oversells(engine):
    e, b, store, _, _ = engine
    tick(e)
    b.holdings['BTC/USD'] += D('.1')
    b.bid = b.ask = D('110')
    tick(e)
    assert len(b.sent) == 2 and store.incidents()


def test_wrong_broker_order_identity_never_generates_an_exit(engine):
    e, b, store, _, _ = engine
    tick(e)
    b.book[b.sent[0]['client_order_id']]['symbol'] = 'ETH/USD'
    tick(e)
    assert len(b.sent) == 2 and store.incidents()


def test_partial_market_exit_reconciles_and_only_sells_remaining_quantity(engine):
    e, b, store, _, _ = engine
    tick(e); b.exit_mode = 'partial'; b.bid = b.ask = D('110')
    tick(e)
    remainder = b.holdings['BTC/USD']
    b.exit_mode = 'filled'; tick(e)
    assert D(b.sent[-1]['qty']) == remainder and not store.active_trades()
    assert b.sent[-1]['client_order_id'].endswith('-exit2')


def test_pending_market_exit_never_posts_duplicate_exit(engine):
    e, b, store, _, _ = engine
    tick(e); b.exit_mode = 'pending'; b.bid = b.ask = D('110')
    tick(e); tick(e); tick(e)
    assert len(b.sent) == 3 and store.active_trades()


def reserve_without_submit(e, b):
    original = e._manage
    e._manage = lambda _: None
    tick(e)
    e._manage = original
    assert not b.sent and e.store.active_trades()


def test_prepared_intent_expires_on_restart_without_post(engine):
    e, b, store, main, portfolio = engine
    reserve_without_submit(e, b)
    b.at += timedelta(seconds=11)
    e = CryptoRangeExecutor(b, store, main, portfolio, now=lambda: b.at)
    tick(e)
    assert not b.sent and not store.active_trades()


def test_off_on_generation_cannot_revive_prepared_intent(engine):
    e, b, store, main, _ = engine
    reserve_without_submit(e, b)
    main.set_control(False)
    main.set_control(True, MAIN_POLICY, b.account_data['account_ref'])
    tick(e)
    assert not b.sent and not store.active_trades()


def test_prepared_intent_rechecks_fresh_quote_and_external_exposure(engine):
    e, b, store, _, _ = engine
    reserve_without_submit(e, b)
    b.ask = D('95.05')  # Beyond the immediate-limit buffer.
    tick(e)
    assert not b.sent and not store.active_trades()


def test_main_control_changed_during_claim_is_never_submitted(engine, monkeypatch):
    e, b, store, main, _ = engine
    original = store.claim_operation
    def change(trade, name, **kw):
        main.set_control(False)
        return original(trade, name, **kw)
    monkeypatch.setattr(store, 'claim_operation', change)
    tick(e)
    assert not b.sent


def test_quote_expires_during_checks_cannot_submit(engine):
    e, b, _, _, _ = engine
    original = b.quote
    def stale(symbol):
        value = original(symbol); b.at += timedelta(seconds=16); return value
    b.quote = stale
    tick(e); assert not b.sent


def test_snapshot_never_exposes_account_reference_or_order_ids(engine):
    e, b, _, _, _ = engine
    tick(e)
    import json
    body = json.dumps(e.snapshot())
    assert 'test-crypto-account' not in body and 'client_order_id' not in body


def test_master_permission_cannot_be_borrowed_from_a_different_account(engine):
    e, b, store, main, _ = engine
    main.set_control(True, MAIN_POLICY, 'other-account')
    tick(e)
    assert not e.enabled() and not b.sent


def test_prepared_expiry_does_not_require_broker_connectivity(engine):
    e, b, store, _, _ = engine
    reserve_without_submit(e, b)
    b.at += timedelta(seconds=11)
    def unavailable(): raise FeedError('Offline')
    b.account = unavailable
    tick(e)
    assert not b.sent and not store.active_trades()


def test_pending_native_protection_records_incident_and_recovery_after_deadline(engine):
    e, b, store, _, _ = engine
    b.stop_mode = 'accepted'
    tick(e)
    assert len(b.sent) == 2
    b.at += timedelta(seconds=31)
    tick(e)
    assert len(b.sent) == 3 and not store.active_trades() and store.incidents()


def test_unresolved_partial_entry_deadline_survives_later_account_outage(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'partial_pending'; b.cancel_mode = 'unknown'
    tick(e)
    b.at += timedelta(seconds=IOC_SETTLE_GRACE_SECONDS); tick(e)  # Cancellation requested after the settle grace.
    b.at += timedelta(seconds=20); tick(e)
    b.at += timedelta(seconds=11)
    def unavailable(): raise FeedError('Offline')
    b.account = unavailable
    tick(e)
    assert len(b.sent) == 1 and store.incidents()


def test_stop_fill_between_order_and_position_reads_is_reconciled_without_false_incident(engine):
    e, b, store, _, _ = engine
    tick(e)
    original = b.positions
    first = True
    def racing():
        nonlocal first
        if first:
            first = False
            b.fill(b.sent[1]['client_order_id'])
        return original()
    b.positions = racing
    tick(e)
    assert not store.active_trades() and not store.incidents() and len(b.sent) == 2


def test_chart_two_r_target_smaller_than_trading_costs_does_not_enter(engine):
    e, b, _, _, _ = engine
    start = NOW.replace(hour=4, minute=0, second=0, microsecond=0)
    bars = [Bar(start+timedelta(minutes=5*(i+1)), 5, 100, 100.05, 99.98, 100) for i in range(48)]
    bars += [Bar(start+timedelta(minutes=245), 5, 99.97, 99.99, 99.96, 99.97),
             Bar(start+timedelta(minutes=250), 5, 100, 100.01, 99.99, 100)]
    market = Market('BTC/USD', {5: bars}, 'alpaca_crypto_us', True, NOW)
    analysis = analyze(market, NOW, provenance={'source': market.source, 'symbol': market.symbol,
                                               'timeframe_minutes': 5, 'native': True})
    assert analysis['signal_ready']
    b.bid, b.ask = D('99.99'), D('100')
    e.tick({'BTC/USD': analysis})
    assert not b.sent and 'fees' in e.message
