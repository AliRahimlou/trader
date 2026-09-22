"""Bounded crypto admission with fake broker state; exits remain unrestricted."""
from collections import Counter
from datetime import timedelta
import pytest

from test_crypto_execution import engine, signal, tick
from pivot.crypto_markets import SYMBOLS
from pivot.crypto_store import CryptoStore


@pytest.fixture(autouse=True)
def crypto_engine_enabled(monkeypatch):
    """These tests exercise the crypto engine as it runs when re-enabled (PIVOT_CRYPTO_PAUSED=0).

    4.5.1 pauses crypto by default; test_crypto_pause.py covers the paused release.
    """
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')


def count_adapter_calls(broker):
    """Count adapter boundaries, excluding fake positions()/submit() helpers."""
    counts, depth = Counter(), [0]
    for name in ('account', 'positions', 'orders', 'clock', 'asset', 'quote', 'lookup', 'submit', 'cancel'):
        original = getattr(broker, name, None)
        if original is None:
            continue
        def counted(*args, _original=original, _name=name, **kwargs):
            if depth[0] == 0:
                counts[_name] += 1
            depth[0] += 1
            try:
                return _original(*args, **kwargs)
            finally:
                depth[0] -= 1
        setattr(broker, name, counted)
    return counts


def analyses(broker, symbols=SYMBOLS):
    return {symbol: signal(symbol, broker.at) for symbol in symbols}


def test_confirmed_exit_frees_capacity_but_not_the_session_entry_allowance(engine):
    e, b, store, main, _ = engine
    store.configure({'symbols': list(SYMBOLS)})
    controls = store.control(), main.control()
    e.tick(analyses(b))
    assert len(b.sent) == 2
    e.tick(analyses(b))
    assert len(b.sent) == 4
    assert {t['symbol'] for t in store.active_trades()} == {'BTC/USD', 'ETH/USD'}
    assert e.snapshot()['capacity'] == {'max_active': 2, 'active_count': 2}
    decision = store.decision_review(b.at)['latest_by_symbol']['SOL/USD']
    assert 'execution capacity is full' in decision['reason']
    assert decision['signal_fresh'] is True and decision['outcome'] == 'waiting'
    assert CryptoStore(store.path).control()['symbols'] == list(SYMBOLS)
    counts = count_adapter_calls(b)
    e.tick(analyses(b))
    # Only the two existing lifecycles are queried. Three waiting markets
    # consume no account/position/order/asset/quote requests of their own.
    assert counts == Counter(account=2, lookup=4, positions=2, orders=2, quote=2)
    b.fill(b.sent[1]['client_order_id'])
    e.tick(analyses(b))
    assert {t['symbol'] for t in store.active_trades()} == {'ETH/USD'}
    assert len([order for order in b.sent if order['side'] == 'buy']) == 2
    assert store.decision_review(b.at)['order_attempts'] == 2
    assert 'two new entry attempts' in store.decision_review(b.at)['latest_by_symbol']['SOL/USD']['reason']
    allowance = main.session_entry_allowance(b.at)
    assert allowance['families']['range_reversal']['remaining'] == 0
    assert allowance['families']['socrates']['remaining'] == 2  # Per family; Socrates is untouched.
    assert controls == (store.control(), main.control())


def test_repeated_failed_preflights_rotate_and_only_one_reads_broker_each_tick(engine):
    e, b, store, _, _ = engine
    store.configure({'symbols': list(SYMBOLS)})
    b.asset_overrides['tradable'] = False
    counts = count_adapter_calls(b)
    visited, original = [], b.asset
    def asset(symbol):
        visited.append(symbol)
        return original(symbol)
    b.asset = asset
    for index in range(7):
        counts.clear()
        e.tick(analyses(b))
        assert counts == Counter(account=1, positions=1, orders=1, asset=1)
        assert visited[-1] == SYMBOLS[index % len(SYMBOLS)]
        review = store.decision_review(b.at)
        assert sum('Waiting for next broker-check slot' in row['reason'] for row in review['latest_by_symbol'].values()) == 4
        b.at += timedelta(seconds=10)
    assert not b.sent and not store.active_trades()
    assert store.decision_review(b.at)['order_attempts'] == 0


def test_invalid_short_and_missing_signals_do_not_consume_broker_slot(engine):
    e, b, store, _, _ = engine
    store.configure({'symbols': list(SYMBOLS)})
    a = analyses(b)
    a['BTC/USD'] = signal('BTC/USD', b.at, short=True)
    a['ETH/USD'] = {}
    a['SOL/USD']['signal_ready'] = False
    counts = count_adapter_calls(b)
    e.tick(a)
    assert [row['symbol'] for row in b.sent if row['side'] == 'buy'] == ['LINK/USD']
    assert counts['asset'] == 1
    assert 'next broker-check slot' in store.decision_review(b.at)['latest_by_symbol']['XRP/USD']['reason']


def test_delayed_preflight_keeps_original_expiry(engine):
    e, b, store, _, _ = engine
    store.configure({'symbols': ['BTC/USD', 'ETH/USD']})
    b.asset_overrides['tradable'] = False
    e.tick(analyses(b))
    b.at += timedelta(seconds=91)
    counts = count_adapter_calls(b)
    e.tick(analyses(b))
    assert not counts and not b.sent
    assert 'current, complete' in store.decision_review(b.at)['latest_by_symbol']['ETH/USD']['reason']


def test_reduced_capacity_does_not_block_any_existing_position_exit(engine, monkeypatch):
    import pivot.crypto_execution as module
    e, b, store, _, _ = engine
    store.configure({'symbols': list(SYMBOLS)})
    for _ in range(2):
        e.tick(analyses(b))
    assert len(store.active_trades()) == 2
    monkeypatch.setattr(module, 'MAX_ACTIVE_CRYPTO_TRADES', 1)
    for stop in [row for row in b.sent if row['type'] == 'stop_limit']:
        b.fill(stop['client_order_id'])
    # Off still reconciles both positions even though they exceed the reduced cap.
    store.configure({'enabled': False})
    e.tick(analyses(b))
    assert not store.active_trades() and len(b.sent) == 4
    assert len(store.history()) == 2


def test_management_status_is_not_borrowed_from_another_market(engine, monkeypatch):
    e, b, store, _, _ = engine
    tick(e)
    e.message = 'Wrong unrelated market message'
    monkeypatch.setattr(e, '_manage', lambda trade: None)
    tick(e)
    decision = store.decision_review(b.at)['latest_by_symbol']['BTC/USD']
    assert decision['outcome'] == 'management'
    assert decision['reason'] == 'Reconciling BTC/USD crypto position and its saved orders.'
