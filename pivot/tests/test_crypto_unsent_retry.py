"""Counterfactual native-bar signal + recorded BTC increments/quote scale, no broker I/O."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP

import pytest

from pivot.crypto_execution import CryptoRangeExecutor, POLICY_VERSION, IOC_LIMIT_BUFFER, rounded
from pivot.crypto_store import CryptoStore, ConcurrentChange
from pivot.models import Bar, Market
from pivot.policy import POLICY_VERSION as MAIN_POLICY
from pivot.portfolio import Portfolio
from pivot.range_reversal import analyze
from pivot.store import Store
from pivot.tests.test_crypto_execution import FakeBroker


@pytest.fixture(autouse=True)
def crypto_engine_enabled(monkeypatch):
    """These tests exercise the crypto engine as it runs when re-enabled (PIVOT_CRYPTO_PAUSED=0).

    4.5.1 pauses crypto by default; test_crypto_pause.py covers the paused release.
    """
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')


D = Decimal
AT = datetime(2026, 9, 22, 8, 10, 10, tzinfo=timezone.utc)
BTC_ASSET = {'symbol': 'BTC/USD', 'class': 'crypto', 'status': 'active', 'tradable': True,
             'fractionable': True, 'shortable': False, 'marginable': False,
             'min_order_size': '0.000012346', 'min_trade_increment': '0.000000001',
             'price_increment': '0.000000001'}


def plausible_signal(at):
    """Invented complete bars, never presented as a historically available setup."""
    start = AT.replace(hour=4, minute=0, second=0)
    bars = [Bar(start + timedelta(minutes=5*(i+1)), 5, 87100, 87700, 86500, 87100) for i in range(48)]
    # The outside candle low (the stop) sits 1.3% below the confirmation close so the
    # net-expectancy cost gate admits the plan; narrower stops are skipped by policy.
    bars += [Bar(start + timedelta(minutes=245), 5, 86520, 86530, 85500, 86450),
             Bar(start + timedelta(minutes=250), 5, 86450, 86660, 86400, 86630)]
    market = Market('BTC/USD', {5: bars}, 'alpaca_crypto_us', True, at)
    return analyze(market, at, provenance={'source': market.source, 'symbol': market.symbol,
                                         'native': True, 'timeframe_minutes': 5})


class IncrementCheckingBroker(FakeBroker):
    def __init__(self, bump=True):
        super().__init__()
        self.at = AT
        # Numbers were observed in a read-only broker audit on 2026-09-21;
        # timestamps, bars and order fills here are entirely counterfactual.
        self.bid, self.ask = D('86619.797'), D('86646.456')
        self.account_data['non_marginable_buying_power'] = '100.00'
        self.quote_count, self.bump = 0, bump

    def asset(self, symbol):
        assert symbol == 'BTC/USD'
        return deepcopy(BTC_ASSET)

    def quote(self, symbol):
        self.quote_count += 1
        if self.bump and self.quote_count == 2:
            self.ask += D('30')  # Beyond the immediate-limit buffer, still inside the original 0.1% drift rule.
        result = super().quote(symbol)
        return {**result, 'bs': '.00099874', 'as': '.0010043'}

    def submit(self, payload):
        qty = D(payload['qty'])
        assert qty > 0 and qty % D(BTC_ASSET['min_trade_increment']) == 0
        for name in ('stop_price', 'limit_price'):
            if name in payload:
                assert D(payload[name]) > 0 and D(payload[name]) % D(BTC_ASSET['price_increment']) == 0
        if payload['side'] == 'buy':
            assert qty >= D(BTC_ASSET['min_order_size'])
            assert D('4.95') <= qty * D(payload['limit_price']) <= D('5.00')
            assert payload['type'] == 'limit' and payload['time_in_force'] == 'ioc'
            assert D(payload['limit_price']) >= self.ask
        else:
            assert qty <= self.holdings['BTC/USD']
        return super().submit(payload)


def runtime(tmp_path, *, bump=True):
    main = Store(tmp_path/'app.db')
    crypto = CryptoStore(main.path)
    broker = IncrementCheckingBroker(bump)
    main.set_control(True, MAIN_POLICY, broker.account_data['account_ref'])
    crypto.configure({'enabled': True, 'policy': POLICY_VERSION, 'account_ref': broker.account_data['account_ref']})
    portfolio = Portfolio(main)
    portfolio.register('range_reversal', crypto.active_trades)
    executor = CryptoRangeExecutor(broker, crypto, main, portfolio, now=lambda: broker.at)
    return executor, broker, crypto, main


def delay_broker_reads(broker, seconds):
    """Advance the fake clock per outer adapter read, never wait or use a network."""
    clock = {'seconds': seconds, 'depth': 0, 'reads': []}
    def delayed(name, original):
        def read(*args, **kwargs):
            if clock['depth'] == 0:
                broker.at += timedelta(seconds=clock['seconds'])
                clock['reads'].append(name)
            clock['depth'] += 1
            try:
                return original(*args, **kwargs)
            finally:
                clock['depth'] -= 1
        return read
    for name in ('account', 'positions', 'orders', 'asset', 'quote', 'lookup'):
        setattr(broker, name, delayed(name, getattr(broker, name)))
    return clock


def test_tiny_ask_move_before_any_post_can_replan_original_still_fresh_signal(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    signal = plausible_signal(broker.at)
    assert signal['signal_ready'] and signal['current_event']['target'] == 88890
    executor.tick({'BTC/USD': signal})
    assert not broker.sent
    abandoned = crypto.history()[0]
    assert abandoned['ops']['entry']['state'] == 'prepared'
    assert main.session_entry_allowance(broker.at)['used'] == 0
    broker.at += timedelta(seconds=1)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert len(broker.sent) == 2  # One fake entry and its quantity-matched protection.
    assert crypto.active_trade('BTC/USD')['id'] == abandoned['id']
    assert main.session_entry_allowance(broker.at)['used'] == 1
    assert D(broker.sent[0]['limit_price']) == rounded(D('86676.456') * (1 + IOC_LIMIT_BUFFER), D('.000000001'), ROUND_UP)


def test_same_metadata_and_cash_support_five_dollar_protected_entry(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path, bump=False)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert [row['type'] for row in broker.sent] == ['limit', 'stop_limit']
    assert crypto.active_trade('BTC/USD')['stage'] == 'open'
    assert main.session_entry_allowance(broker.at)['used'] == 1


def test_slow_reads_expire_prepared_plan_without_consuming_signal_or_allowance(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path, bump=False)
    clock = delay_broker_reads(broker, 2.1)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert broker.at == AT + timedelta(seconds=21)
    assert clock['reads'] == ['account', 'positions', 'orders', 'asset', 'quote',
                              'account', 'account', 'positions', 'orders', 'quote']
    old = crypto.history()[0]
    assert old['stage'] == 'finished' and old['ops']['entry']['state'] == 'prepared'
    assert not broker.sent and not crypto.entry_consumed(old['id'])
    assert main.session_entry_allowance(broker.at)['used'] == 0
    clock['seconds'] = 0
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert len(broker.sent) == 2 and crypto.active_trade('BTC/USD')['id'] == old['id']
    assert main.session_entry_allowance(broker.at)['used'] == 1


def test_deadline_crossed_during_irreversible_claim_remains_consumed(tmp_path, monkeypatch):
    executor, broker, crypto, main = runtime(tmp_path, bump=False)
    claim = crypto.claim_operation
    def late_claim(trade, name, **kwargs):
        result = claim(trade, name, **kwargs)
        broker.at += timedelta(seconds=11)
        return result
    monkeypatch.setattr(crypto, 'claim_operation', late_claim)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    old = crypto.history()[0]
    assert old['ops']['entry']['state'] == 'aborted_before_submit'
    assert not broker.sent and crypto.entry_consumed(old['id'])
    assert main.session_entry_allowance(broker.at)['used'] == 1
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert not broker.sent and main.session_entry_allowance(broker.at)['used'] == 1


def test_unsent_retry_cannot_extend_original_confirmation_window(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    first = crypto.history()[0]
    broker.at = AT.replace(minute=11, second=30)  # Exactly the original close + 90 seconds.
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert not broker.sent and crypto.history()[0]['_version'] == first['_version']
    assert main.session_entry_allowance(broker.at)['used'] == 0


@pytest.mark.parametrize('mode', ['lost_unseen', 'lost_accepted', 'reject', 'no_fill', 'partial'])
def test_every_actually_attempted_entry_remains_consumed(tmp_path, mode):
    executor, broker, crypto, main = runtime(tmp_path, bump=False)
    broker.entry_mode = mode
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    trade = (crypto.active_trades() or crypto.history())[0]
    assert crypto.entry_consumed(trade['id'])
    broker.at += timedelta(seconds=1)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert sum(order['side'] == 'buy' for order in broker.sent) == 1
    assert main.session_entry_allowance(broker.at)['used'] == 1


@pytest.mark.parametrize('evidence', ['attempted_at', 'last_seen', 'uncertain_since', 'filled_qty', 'allowance_claim'])
def test_contradictory_unsent_evidence_never_becomes_retryable(tmp_path, evidence):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    old = crypto.history()[0]
    if evidence == 'allowance_claim':
        with main.connect() as db:
            db.execute('INSERT INTO session_entry_allowances VALUES(?,?,?,?,?)',
                       ('range_reversal', old['id'], '2026-09-22', broker.at.isoformat(), 'atomic_entry_claim'))
    elif evidence == 'filled_qty':
        old[evidence] = '0'
        crypto.save_trade(old, finished=True)
    else:
        old['ops']['entry'][evidence] = {} if evidence == 'last_seen' else broker.at.isoformat()
        crypto.save_trade(old, finished=True)
    assert crypto.entry_consumed(old['id'])
    broker.at += timedelta(seconds=1)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert not broker.sent


def test_restart_replans_never_submitted_intent_and_stale_worker_cannot_overwrite(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    stale = crypto.history()[0]
    reopened_main, reopened_crypto = Store(main.path), CryptoStore(crypto.path)
    portfolio = Portfolio(reopened_main)
    portfolio.register('range_reversal', reopened_crypto.active_trades)
    restarted = CryptoRangeExecutor(broker, reopened_crypto, reopened_main, portfolio, now=lambda: broker.at)
    broker.at += timedelta(seconds=1)
    restarted.tick({'BTC/USD': plausible_signal(broker.at)})
    assert len(broker.sent) == 2
    with pytest.raises(ConcurrentChange):
        crypto.save_trade(stale, finished=True)
    assert not crypto.claim_operation(stale, 'entry', attempted_at=broker.at)
    assert any(event['kind'] == 'unsubmitted_entry_retried' for event in crypto.events())


def test_changed_settings_retire_old_plan_and_require_fresh_authorization(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path, bump=False)
    manage = executor._manage
    executor._manage = lambda trade: None
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    original = crypto.active_trade('BTC/USD')
    old_generation = original['authorization']['crypto']['generation']
    crypto.configure({'enabled': False})
    executor._manage = manage
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert not broker.sent and not crypto.entry_consumed(original['id'])
    assert main.session_entry_allowance(broker.at)['used'] == 0
    # This is a fake local owner action only; no production permission is changed.
    crypto.configure({'enabled': True})
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    fresh = crypto.active_trade('BTC/USD')
    assert fresh['authorization']['crypto']['generation'] > old_generation
    assert len(broker.sent) == 2


def test_store_cannot_rearm_same_id_with_a_later_confirmation(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    old = crypto.history()[0]
    changed = deepcopy(old)
    changed.update(stage='entering', reason=None)
    changed['signal']['current_event']['confirmation_at'] = (AT + timedelta(minutes=5)).isoformat()
    assert not crypto.reserve_trade(changed, expected_control=crypto.control())
    assert crypto.history()[0] == old


def test_retry_still_obeys_exhausted_family_two_entry_allowance(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    with main.connect() as db:
        for identity in ('already-one', 'already-two'):
            db.execute('INSERT INTO session_entry_allowances VALUES(?,?,?,?,?)',
                       ('range_reversal', identity, '2026-09-22', broker.at.isoformat(), 'atomic_entry_claim'))
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert not broker.sent and main.session_entry_allowance(broker.at)['families']['range_reversal']['used'] == 2
    assert 'two new entry attempts' in executor.message


def test_socrates_attempts_do_not_consume_the_crypto_allowance(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    with main.connect() as db:
        for identity in ('stock-one', 'stock-two'):
            db.execute('INSERT INTO session_entry_allowances VALUES(?,?,?,?,?)',
                       ('socrates', identity, '2026-09-22', broker.at.isoformat(), 'atomic_entry_claim'))
    for _ in range(3):
        executor.tick({'BTC/USD': plausible_signal(broker.at)})
    assert broker.sent and broker.sent[0]['side'] == 'buy', executor.message
    allowance = main.session_entry_allowance(broker.at)
    assert allowance['families'] == {'socrates': {'used': 2, 'remaining': 0}, 'range_reversal': {'used': 1, 'remaining': 1}}


def test_concurrent_replans_reserve_one_version_without_overwriting_each_other(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    old = crypto.history()[0]
    candidate = deepcopy(old)
    candidate.update(stage='entering', reason=None)
    control = crypto.control()
    def reserve():
        return CryptoStore(crypto.path).reserve_trade(deepcopy(candidate), expected_control=control)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        assert sum(future.result() for future in futures) == 1
    assert crypto.active_trade('BTC/USD')['_version'] == old['_version'] + 1
    assert main.session_entry_allowance(broker.at)['used'] == 0
    assert len([event for event in crypto.events() if event['kind'] == 'unsubmitted_entry_retried']) == 1


def test_retry_cannot_move_old_event_to_a_different_account(tmp_path):
    executor, broker, crypto, main = runtime(tmp_path)
    executor.tick({'BTC/USD': plausible_signal(broker.at)})
    old = crypto.history()[0]
    changed = deepcopy(old)
    changed.update(stage='entering', reason=None, account_ref='some-other-account')
    assert not crypto.reserve_trade(changed, expected_control=crypto.control())
    assert crypto.history()[0] == old and not broker.sent
