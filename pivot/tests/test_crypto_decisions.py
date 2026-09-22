"""Durable live-check evidence; no credentials, network or real broker writes."""
from copy import deepcopy
from datetime import timedelta

from test_crypto_execution import engine, signal, tick, NOW, CryptoRangeExecutor, D
from pivot.crypto_store import CryptoStore


def test_unchanged_checks_and_receipt_refreshes_do_not_write_poll_duplicates(engine):
    e, b, store, main, _ = engine
    controls = deepcopy(main.control()), deepcopy(store.control())
    for _ in range(5):
        e.tick({'BTC/USD': signal(at=b.at, short=True)})
        b.at += timedelta(seconds=10)
    review = e.snapshot()['decision_review']
    assert review['checks_recorded'] == 1
    assert review['fresh_setups'] == {'total': 1, 'long': 0, 'short': 1}
    assert review['order_attempts'] == review['filled_entries'] == 0
    assert review['latest_by_symbol']['BTC/USD']['outcome'] == 'unsupported_direction'
    assert 'cannot open short' in review['latest_by_symbol']['BTC/USD']['reason']
    assert b.sent == b.canceled == [] and controls == (main.control(), store.control())


def test_checks_survive_restart_without_becoming_new_checkpoints(engine):
    e, b, store, main, portfolio = engine
    a = signal(short=True);e.tick({'BTC/USD': a})
    reopened = CryptoRangeExecutor(b, CryptoStore(store.path), main, portfolio, now=lambda: b.at)
    assert reopened.snapshot()['decision_review'] == e.snapshot()['decision_review']
    reopened.tick({'BTC/USD': a})
    assert reopened.snapshot()['decision_review']['checks_recorded'] == 1
    assert not b.sent


def test_new_closed_candle_and_changed_blocker_are_retained(engine):
    e, b, store, _, _ = engine
    a = {'state': 'WATCHING', 'latest_bar_at': NOW.isoformat()}
    e.tick({'BTC/USD': a});e.tick({'BTC/USD': a})
    a['latest_bar_at'] = (NOW + timedelta(minutes=5)).isoformat()
    e.tick({'BTC/USD': a})
    assert store.decision_review(b.at)['checks_recorded'] == 2
    store.configure({'enabled': False});e.tick({'BTC/USD': a})
    review = store.decision_review(b.at)
    assert review['checks_recorded'] == 3
    assert review['latest_by_symbol']['BTC/USD']['outcome'] == 'paused'
    assert not b.sent


def test_expiring_same_signal_records_changed_readiness_not_an_additional_setup(engine):
    e, b, store, _, _ = engine
    e.tick({'BTC/USD': signal(short=True)})
    b.at += timedelta(seconds=91)
    e.tick({'BTC/USD': signal(at=b.at, short=True)})
    review = store.decision_review(b.at)
    assert review['checks_recorded'] == 2 and review['fresh_setups']['short'] == 1
    assert review['recent_checks'][0]['signal_fresh'] is False
    assert not b.sent


def test_reconstructed_history_is_not_counted_as_observed_live_setups(engine):
    e, b, store, _, _ = engine
    a = signal(short=True)
    assert a['candidates']
    a.update(current_event=None, signal_ready=False, state='WATCHING')
    e.tick({'BTC/USD': a})
    review = store.decision_review(b.at)
    assert review['fresh_setups']['total'] == 0
    assert review['scope'] == 'recorded_live_checks_only'
    assert not b.sent


def test_day_scope_follows_new_york_midnight(engine):
    e, b, store, _, _ = engine
    b.at = NOW.replace(hour=3, minute=59, second=59)
    e.tick({})
    yesterday = store.decision_review(b.at)
    assert yesterday['day'] == '2026-09-18' and yesterday['checks_recorded'] == 1
    b.at += timedelta(seconds=2)
    assert store.decision_review(b.at)['checks_recorded'] == 0
    e.tick({})
    assert store.decision_review(b.at)['day'] == '2026-09-19'
    assert store.decision_review(b.at)['checks_recorded'] == 1
    assert not b.sent


def test_real_entry_proof_is_distinct_from_signal_and_repeated_management_checks(engine):
    e, b, store, _, _ = engine
    tick(e)
    for _ in range(3): tick(e)
    review = store.decision_review(b.at)
    assert review['fresh_setups'] == {'total': 1, 'long': 1, 'short': 0}
    assert review['order_attempts'] == review['filled_entries'] == 1
    assert len(b.sent) == 2  # One fake entry, one fake protective stop.
    assert review['checks_recorded'] == 2  # Entry decision, then management.


def test_rejected_order_is_an_attempt_but_not_a_fill(engine):
    e, b, store, _, _ = engine
    b.entry_mode = 'reject';tick(e)
    review = store.decision_review(b.at)
    assert review['order_attempts'] == 1 and review['filled_entries'] == 0
    assert review['recent_checks'][0]['outcome'] == 'entry_rejected'
    tick(e)
    assert store.decision_review(b.at)['recent_checks'][0]['outcome'] == 'incident_paused'
    assert len(b.sent) == 1


def test_pre_submit_price_change_retains_no_order_reason_without_counting_attempt(engine):
    e, b, store, _, _ = engine
    original, calls = b.quote, []
    def moving(symbol):
        calls.append(symbol)
        if len(calls) == 2: b.ask += D('.05')  # Beyond the immediate-limit buffer.
        return original(symbol)
    b.quote = moving;tick(e)
    review = store.decision_review(b.at)
    assert review['order_attempts'] == review['filled_entries'] == 0
    assert review['recent_checks'][0]['outcome'] == 'entry_skipped'
    assert 'price changed before submission' in review['recent_checks'][0]['reason']
    assert not b.sent


def test_decision_retention_is_bounded_and_latest_symbol_record_is_retained(engine, monkeypatch):
    import pivot.crypto_store as module
    monkeypatch.setattr(module, 'DECISION_LIMIT', 3)
    e, b, store, _, _ = engine
    for i in range(6): e.tick({'BTC/USD': {'state': 'WATCHING', 'latest_bar_at': (NOW+timedelta(minutes=5*i)).isoformat()}})
    review = store.decision_review(b.at)
    assert review['checks_recorded'] == 3 and review['retention_limit'] == 3
    assert review['recent_checks'][0]['latest_bar_at'] == (NOW+timedelta(minutes=25)).isoformat()


def test_logging_failure_is_visible_but_does_not_interrupt_position_protection(engine, monkeypatch):
    e, b, store, _, _ = engine
    def unavailable(*args): raise OSError('Fixture unavailable')
    monkeypatch.setattr(store, 'record_decision', unavailable)
    tick(e)
    assert len(b.sent) == 2 and store.active_trade('BTC/USD')['stage'] == 'open'
    assert e.snapshot()['decision_review']['status'] == 'degraded'


def test_optional_solana_five_dollar_lifecycle_uses_separate_saved_selection(engine):
    e, b, store, main, _ = engine
    store.configure({'symbols': ['SOL/USD']});controls = main.control(), store.control()
    tick(e, 'SOL/USD')
    assert len(b.sent) == 2 and all(row['symbol'] == 'SOL/USD' for row in b.sent)
    assert store.active_trade('SOL/USD')['stage'] == 'open'
    b.bid = b.ask = D('110');tick(e, 'SOL/USD')
    assert not store.active_trades() and len(b.sent) == 3
    assert store.decision_review(b.at)['order_attempts'] == store.decision_review(b.at)['filled_entries'] == 1
    assert controls == (main.control(), store.control())
