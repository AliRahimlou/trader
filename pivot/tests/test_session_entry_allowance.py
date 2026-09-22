"""Per-family two-entry restriction, using SQLite and fake brokers only."""
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
import json
from multiprocessing import get_context

import pytest

from pivot.crypto_store import CryptoStore
from pivot.execution import Executor
from pivot.portfolio import PortfolioBlocked
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready


def intent(identity, symbol='QQQ', at=NOW):
    return {'id': identity, 'symbol': symbol, 'stage': 'entering', 'created_at': at.isoformat(), 'amount': '5.00',
            'ops': {'entry': {'state': 'prepared', 'payload': {'client_order_id': identity + '-entry'}}}}


def stock_claim(main, identity, at=NOW):
    trade = intent(identity, at=at)
    assert main.reserve_trade(trade)
    assert main.claim_operation(identity, 'entry', expected_entry=trade, attempted_at=at)
    trade['ops']['entry']['state'] = 'attempted'
    trade['stage'] = 'finished'
    main.save_trade(trade, finished=True)
    return trade


def crypto_claim(crypto, identity, symbol='BTC/USD', at=NOW):
    trade = intent(identity, symbol, at)
    assert crypto.reserve_trade(trade)
    assert crypto.claim_operation(trade, 'entry', attempted_at=at)
    trade['stage'] = 'finished'
    crypto.save_trade(trade, finished=True)
    return trade


def test_per_family_cap_survives_finishes_restarts_and_different_symbols(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    stock_claim(main, 's1')
    crypto_claim(crypto, 'c1')
    restarted = CryptoStore(main.path)
    # A Socrates attempt does not consume the crypto engine's allowance.
    crypto_claim(restarted, 'c2', 'ETH/USD')
    third = intent('c3', 'SOL/USD')
    assert restarted.reserve_trade(third)
    with pytest.raises(PortfolioBlocked, match='4H Range Reversal limit of two new entry attempts'):
        restarted.claim_operation(third, 'entry', attempted_at=NOW)
    assert restarted.get_trade('c3')['ops']['entry']['state'] == 'prepared'
    # The exhausted crypto family leaves the second Socrates attempt available.
    stock_claim(Store(main.path), 's2')
    fourth = intent('s3')
    assert main.reserve_trade(fourth)
    with pytest.raises(PortfolioBlocked, match='Socrates limit of two new entry attempts'):
        main.claim_operation('s3', 'entry', attempted_at=NOW)
    status = Store(main.path).session_entry_allowance(NOW)
    assert status['limit'] == 2 and status['used'] == 4 and status['remaining'] == 0
    assert status['families'] == {'socrates': {'used': 2, 'remaining': 0}, 'range_reversal': {'used': 2, 'remaining': 0}}


def test_reporting_shows_each_family_separately(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    empty = main.session_entry_allowance(NOW)
    assert empty['families'] == {'socrates': {'used': 0, 'remaining': 2}, 'range_reversal': {'used': 0, 'remaining': 2}}
    assert empty['used'] == 0 and empty['remaining'] == 4 and empty['scope'] == 'per_family'
    stock_claim(main, 's1')
    crypto_claim(crypto, 'c1')
    crypto_claim(crypto, 'c2', 'ETH/USD')
    status = main.session_entry_allowance(NOW)
    assert status['families'] == {'socrates': {'used': 1, 'remaining': 1}, 'range_reversal': {'used': 2, 'remaining': 0}}
    assert status['used'] == 3 and status['remaining'] == 1
    main.assert_session_entry_available(NOW, family='socrates')
    with pytest.raises(PortfolioBlocked, match='4H Range Reversal limit'):
        main.assert_session_entry_available(NOW, family='range_reversal')
    # The crypto engine's existing call site omits its family and is checked as crypto.
    with pytest.raises(PortfolioBlocked, match='4H Range Reversal limit'):
        main.assert_session_entry_available(NOW)
    with pytest.raises(PortfolioBlocked, match='unknown strategy family'):
        main.assert_session_entry_available(NOW, family='other')


def test_pending_unknown_rejected_and_aborted_claims_are_never_refunded(tmp_path):
    main = Store(tmp_path / 'app.db')
    first = stock_claim(main, 'unknown')
    first['ops']['entry']['state'] = 'rejected'
    main.save_trade(first, finished=True)
    second = stock_claim(main, 'no-network-after-claim')
    second['ops']['entry']['state'] = 'aborted_before_submit'
    main.save_trade(second, finished=True)
    assert main.session_entry_allowance(NOW)['families']['socrates']['remaining'] == 0
    third = intent('third')
    assert main.reserve_trade(third)
    with pytest.raises(PortfolioBlocked):
        main.claim_operation('third', 'entry', attempted_at=NOW)


def test_prepared_without_post_claim_does_not_use_allowance(tmp_path):
    main = Store(tmp_path / 'app.db')
    trade = intent('never-sent')
    assert main.reserve_trade(trade)
    trade['stage'] = 'finished'
    main.save_trade(trade, finished=True)
    assert main.session_entry_allowance(NOW)['used'] == 0
    stock_claim(main, 'first')
    stock_claim(main, 'second')
    assert main.session_entry_allowance(NOW)['used'] == 2


def test_backfills_legacy_stock_and_crypto_without_resetting_controls_or_history(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    legacy = intent('legacy')
    assert main.reserve_trade(legacy)
    legacy['ops']['entry']['state'] = 'attempted'
    main.save_trade(legacy, finished=True)
    before = main.deployment_permission()
    with main.connect() as db:
        assert db.execute('SELECT count(*) FROM session_entry_allowances').fetchone()[0] == 0
    # Read-only dashboard status must already count legacy risk, without writing.
    assert main.session_entry_allowance(NOW)['used'] == 1
    with main.connect() as db:
        assert db.execute('SELECT count(*) FROM session_entry_allowances').fetchone()[0] == 0
    crypto_claim(crypto, 'new')
    with main.connect() as db:
        assert db.execute('SELECT source FROM session_entry_allowances WHERE trade_id=?', ('legacy',)).fetchone()[0] == 'retained_legacy_attempt'
        assert json.loads(db.execute('SELECT body FROM trades WHERE id=?', ('legacy',)).fetchone()[0]) == legacy
    assert main.deployment_permission() == before


def test_two_legacy_attempts_already_exhaust_their_own_family_only(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    for identity, symbol in [('legacy-btc', 'BTC/USD'), ('legacy-eth', 'ETH/USD')]:
        trade = intent(identity, symbol)
        assert crypto.reserve_trade(trade)
        trade['ops']['entry']['state'] = 'attempted'
        crypto.save_trade(trade, finished=True)
    blocked = intent('fresh-crypto', 'SOL/USD')
    assert crypto.reserve_trade(blocked)
    with pytest.raises(PortfolioBlocked, match='two new entry attempts'):
        crypto.claim_operation(blocked, 'entry', attempted_at=NOW)
    assert main.session_entry_allowance(NOW)['families']['range_reversal'] == {'used': 2, 'remaining': 0}
    fresh = intent('fresh')
    assert main.reserve_trade(fresh)
    assert main.claim_operation('fresh', 'entry', attempted_at=NOW)
    assert main.session_entry_allowance(NOW)['used'] == 3


@pytest.mark.parametrize('broken', ['no_time', 'naive_time', 'unknown_state', 'contradictory_prepared', 'prepared_with_fill'])
def test_unverifiable_history_blocks_entries_but_not_stops(tmp_path, broken):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    legacy = intent('legacy')
    assert main.reserve_trade(legacy)
    legacy['ops']['entry']['state'] = 'attempted'
    if broken == 'no_time':
        del legacy['created_at']
    elif broken == 'naive_time':
        legacy['created_at'] = '2026-09-16T12:00:00'
    elif broken == 'unknown_state':
        legacy['ops']['entry']['state'] = 'garbled'
    elif broken == 'contradictory_prepared':
        legacy['ops']['entry'].update(state='prepared', last_seen={'status': 'filled'})
    else:
        legacy['ops']['entry']['state'] = 'prepared'
        legacy['filled_qty'] = '.1'
    main.save_trade(legacy, finished=True)
    fresh = intent('fresh', 'BTC/USD')
    assert crypto.reserve_trade(fresh)
    with pytest.raises(PortfolioBlocked, match='history needs reconciliation'):
        crypto.claim_operation(fresh, 'entry', attempted_at=NOW)
    assert main.session_entry_allowance(NOW)['status'] == 'blocked'
    fresh['ops']['stop'] = {'state': 'prepared', 'payload': {'client_order_id': 'protect'}}
    crypto.save_trade(fresh)
    assert crypto.claim_operation(fresh, 'stop', attempted_at=NOW)


def test_exhausted_limit_does_not_block_protection_or_exits(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    stock_claim(main, 'first')
    crypto_claim(crypto, 'second')
    for store, symbol in [(main, 'QQQ'), (crypto, 'BTC/USD')]:
        trade = intent('managed-' + symbol, symbol)
        trade['ops']['stop'] = {'state': 'prepared', 'payload': {}}
        trade['ops']['exit0'] = {'state': 'prepared', 'payload': {}}
        assert store.reserve_trade(trade)
        for name in ('stop', 'exit0'):
            assert store.claim_operation(trade['id'] if store is main else trade, name, attempted_at=NOW)
    assert main.session_entry_allowance(NOW)['used'] == 2


@pytest.mark.parametrize('before,after', [
    ('2026-09-22T03:59:40+00:00', '2026-09-22T04:00:00+00:00'),
    ('2026-03-08T04:59:40+00:00', '2026-03-08T05:00:00+00:00'),
    ('2026-11-01T03:59:40+00:00', '2026-11-01T04:00:00+00:00'),
])
def test_new_york_calendar_session_resets_including_dst_days(tmp_path, before, after):
    main = Store(tmp_path / 'app.db')
    stock_claim(main, 'old1', datetime.fromisoformat(before))
    stock_claim(main, 'old2', datetime.fromisoformat(before))
    assert main.session_entry_allowance(datetime.fromisoformat(after))['used'] == 0
    stock_claim(main, 'next-day', datetime.fromisoformat(after))
    assert main.session_entry_allowance(datetime.fromisoformat(after))['used'] == 1


def test_midnight_ambiguous_submit_reserves_both_sessions(tmp_path):
    main = Store(tmp_path / 'app.db')
    before = datetime.fromisoformat('2026-09-22T03:59:59+00:00')
    after = before + timedelta(seconds=2)
    stock_claim(main, 'crossing1', before)
    stock_claim(main, 'crossing2', before)
    assert main.session_entry_allowance(before)['used'] == 2
    assert main.session_entry_allowance(after)['used'] == 2
    fresh = intent('third', at=after)
    assert main.reserve_trade(fresh)
    with pytest.raises(PortfolioBlocked):
        main.claim_operation('third', 'entry', attempted_at=after)


@pytest.mark.parametrize('first,second', [
    ('2026-03-08T06:59:00+00:00', '2026-03-08T07:01:00+00:00'),
    ('2026-11-01T05:59:00+00:00', '2026-11-01T06:01:00+00:00'),
])
def test_dst_clock_change_does_not_reset_allowance(tmp_path, first, second):
    main = Store(tmp_path / 'app.db')
    stock_claim(main, 'first', datetime.fromisoformat(first))
    assert main.session_entry_allowance(datetime.fromisoformat(second))['used'] == 1


def _race_claim(path, identity, family):
    if family == 'socrates':
        store = Store(path)
        subject = identity
    else:
        store = CryptoStore(path)
        subject = store.get_trade(identity)
    try:
        return store.claim_operation(subject, 'entry', attempted_at=NOW)
    except PortfolioBlocked:
        return False


def test_separate_processes_compete_for_each_family_two_durable_slots(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    assert main.reserve_trade(intent('stock'))
    for i, symbol in enumerate(('BTC/USD', 'ETH/USD', 'SOL/USD')):
        assert crypto.reserve_trade(intent(f'crypto{i}', symbol))
    with ProcessPoolExecutor(max_workers=4, mp_context=get_context('fork')) as pool:
        futures = [pool.submit(_race_claim, main.path, identity, family) for identity, family in
                   [('stock', 'socrates')] + [(f'crypto{i}', 'range_reversal') for i in range(3)]]
        assert sum(f.result(timeout=20) for f in futures) == 3
    status = Store(main.path).session_entry_allowance(NOW)
    assert status['used'] == 3
    assert status['families'] == {'socrates': {'used': 1, 'remaining': 1}, 'range_reversal': {'used': 2, 'remaining': 0}}


def test_stale_stock_save_cannot_reclaim_old_identity_or_cross_day(tmp_path):
    main = Store(tmp_path / 'app.db')
    trade = intent('once')
    assert main.reserve_trade(trade)
    assert main.claim_operation('once', 'entry', attempted_at=NOW)
    main.save_trade(trade)  # Simulate a stale stock-ledger overwrite.
    with pytest.raises(PortfolioBlocked, match='already consumed'):
        main.claim_operation('once', 'entry', attempted_at=NOW + timedelta(days=1))


def test_stock_engine_third_entry_never_reaches_fake_broker(tmp_path):
    main = Store(tmp_path / 'app.db')
    stock_claim(main, 'earlier-stock-one')
    stock_claim(main, 'earlier-stock-two')
    crypto_claim(CryptoStore(main.path), 'earlier-crypto')
    broker = FakeBroker()
    executor = Executor(broker, main, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready())
    assert not broker.sent
    assert 'Socrates limit of two new entry attempts' in executor.message


def test_stock_engine_enters_after_two_crypto_attempts(tmp_path):
    main = Store(tmp_path / 'app.db')
    crypto = CryptoStore(main.path)
    crypto_claim(crypto, 'crypto-one')
    crypto_claim(crypto, 'crypto-two', 'ETH/USD')
    broker = FakeBroker()
    executor = Executor(broker, main, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready())
    assert [o['type'] for o in broker.sent] == ['market', 'stop']
    status = main.session_entry_allowance(broker.at)
    assert status['families'] == {'socrates': {'used': 1, 'remaining': 1}, 'range_reversal': {'used': 2, 'remaining': 0}}


def test_fake_unknown_post_retains_allowance_and_is_not_retried(tmp_path):
    main = Store(tmp_path / 'app.db')
    broker = FakeBroker()
    broker.entry_mode = 'lost_unseen'
    executor = Executor(broker, main, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready())
    assert len(broker.sent) == 1
    broker.at += timedelta(seconds=1)
    Executor(broker, Store(main.path), now=lambda: broker.at).tick(ready(broker.at))
    assert len(broker.sent) == 1
    assert main.session_entry_allowance(broker.at)['used'] == 1
