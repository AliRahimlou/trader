"""Family-scoped pauses, owner alerts, clock-skew tolerance and the 30-minute entry cutoff.

Fake brokers and SQLite only. The webhook is exercised through a monkeypatched
requests.post; the suite never configures a real URL.
"""
from datetime import timedelta
import json

import pytest
import requests

from pivot import alerts
from pivot.crypto_store import CryptoStore
from pivot.execution import (Executor, ENTRY_CUTOFF_SECONDS, CLOCK_SKEW_SECONDS, Waiting,
                             checked_quote, session_open)
from pivot.policy import POLICY_VERSION
from pivot.portfolio import Portfolio
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready


def engine(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'execution.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    return executor, broker, store


def kinds(store):
    return [event['kind'] for event in store.events()]


# --- family-scoped pause ------------------------------------------------------

def test_pause_family_deselects_socrates_in_one_transaction_and_leaves_global_live(tmp_path):
    store = Store(tmp_path / 'app.db')
    crypto = CryptoStore(store.path)
    store.set_control(True, POLICY_VERSION, 'owner-account')
    crypto.configure({'enabled': True, 'policy': 'range-spot-execution-v1', 'account_ref': 'owner-account'})
    before = store.entry_authorization()
    body = store.pause_family('socrates', 'Broker rejected a QQQ entry order.')
    assert body['family'] == 'socrates' and body['reason'] == 'Broker rejected a QQQ entry order.'
    assert store.strategy_selection() == {'socrates': False}
    assert store.control()['enabled'] is True and store.control()['policy'] == POLICY_VERSION
    assert crypto.control()['enabled'] is True
    after = store.entry_authorization()
    assert after['generation'] == before['generation'] + 1 and after['control'] == before['control']
    assert kinds(store)[0] == 'strategy_paused' and 'live_off' not in kinds(store)
    assert store.events()[0]['detail']['reason'] == 'Broker rejected a QQQ entry order.'
    # A repeated pause is a no-op: no generation churn, no duplicate journal entry.
    assert store.pause_family('socrates', 'Again') is None
    assert store.entry_authorization() == after and kinds(store).count('strategy_paused') == 1
    with pytest.raises(ValueError):
        store.pause_family('range_reversal', 'Crypto has its own control')
    with pytest.raises(ValueError):
        store.pause_family('socrates', '')


def test_executor_enabled_honours_the_durable_selection_without_a_portfolio(tmp_path):
    executor, broker, store = engine(tmp_path)
    enable(executor)
    assert executor.enabled()
    store.pause_family('socrates', 'test pause')
    assert not executor.enabled() and store.control()['enabled'] is True
    executor.tick(ready())
    assert not broker.sent and store.latest_execution_check()['outcome'] == 'disabled'
    # The owner's strategy toggle restores entries without touching global Live.
    with store.connect() as db:
        db.execute('UPDATE strategy_selection SET body=? WHERE id=1', (json.dumps({'socrates': True}),))
    assert executor.enabled()


def test_rejected_entry_pauses_socrates_only_and_crypto_keeps_its_permission(tmp_path):
    executor, broker, store = engine(tmp_path)
    crypto = CryptoStore(store.path)
    crypto.configure({'enabled': True, 'policy': 'range-spot-execution-v1', 'account_ref': 'fake-account-only'})
    portfolio = Portfolio(store)
    portfolio.register('range_reversal', crypto.active_trades)
    portfolio.enabled_predicate = lambda family: (store.strategy_selection()['socrates'] if family == 'socrates'
                                                  else crypto.control()['enabled'])
    executor.portfolio = portfolio
    enable(executor)
    broker.entry_mode = 'reject'
    executor.tick(ready())
    assert len(broker.sent) == 1 and not executor.enabled()
    assert store.control()['enabled'] is True, 'global Live stays as the owner set it'
    assert portfolio.family_enabled('range_reversal') and crypto.control()['enabled'] is True
    assert store.strategy_selection() == {'socrates': False}
    assert 'strategy_paused' in kinds(store) and 'order_rejected' in kinds(store) and 'live_off' not in kinds(store)
    paused = next(event for event in store.events() if event['kind'] == 'strategy_paused')
    assert 'rejected' in paused['detail']['reason'] and 'Socrates' in paused['detail']['reason']


@pytest.mark.parametrize('scenario', ['replaced_entry', 'rejected_stop', 'foreign_manual_flat', 'exit_unconfirmed'])
def test_every_former_global_off_path_now_pauses_the_family(tmp_path, scenario):
    executor, broker, store = engine(tmp_path)
    enable(executor)
    if scenario == 'replaced_entry':
        broker.entry_mode = 'pending'
        executor.tick(ready())
        broker.book[broker.sent[0]['client_order_id']]['status'] = 'replaced'
        executor.tick(ready())
        assert store.active_trade()['stage'] == 'attention'
    elif scenario == 'rejected_stop':
        broker.reject_stop = True
        executor.tick(ready())
        assert [p['type'] for p in broker.sent] == ['market', 'stop', 'market']
    elif scenario == 'foreign_manual_flat':
        executor.tick(ready())
        broker.position_data = []
        broker.book[broker.sent[-1]['client_order_id']]['status'] = 'canceled'
        executor.tick(ready())
        assert store.active_trade()['stage'] == 'attention'
        executor.tick(ready())
        assert store.active_trade() is None
        assert 'Socrates remains paused' in executor.message
        assert 'manual_resolution_confirmed' in kinds(store)
    else:
        executor.tick(ready())
        trade = store.active_trade()
        trade['stage'] = 'exiting'
        trade['exit_pending'] = {'first_observed_at': NOW.isoformat(),
                                 'confirmation_deadline': (NOW - timedelta(seconds=1)).isoformat()}
        store.save_trade(trade)
        broker.cancel = lambda oid: None  # Cancellation never confirms.
        executor.tick(ready())
        assert 'exit_needs_attention' in kinds(store)
    assert not executor.enabled()
    assert store.control()['enabled'] is True and store.control()['policy'] == POLICY_VERSION
    assert store.strategy_selection() == {'socrates': False}
    assert 'live_off' not in kinds(store) and kinds(store).count('strategy_paused') == 1


def test_pause_expires_a_prepared_entry_through_the_authorization_generation(tmp_path):
    executor, broker, store = engine(tmp_path)
    enable(executor)
    original = executor._manage
    executor._manage = lambda trade: None
    executor.tick(ready())
    executor._manage = original
    trade = store.active_trade()
    assert trade['ops']['entry']['state'] == 'prepared' and not broker.sent
    store.pause_family('socrates', 'paused before submission')
    executor.tick(ready())
    assert not broker.sent and store.active_trade() is None
    assert store.session_entry_allowance(broker.at)['families']['socrates']['used'] == 0


# --- alerts -------------------------------------------------------------------

class Posted:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail:
            raise requests.ConnectionError('simulated outage')
        return object()


def test_notify_is_a_no_op_without_the_webhook_and_posts_when_configured(monkeypatch):
    posted = Posted()
    monkeypatch.setattr(requests, 'post', posted)
    monkeypatch.delenv(alerts.ENV_VAR, raising=False)
    assert alerts.notify('order_rejected', {'symbol': 'QQQ'}) is None
    assert posted.calls == []
    monkeypatch.setenv(alerts.ENV_VAR, ' https://hooks.example.test/pivot ')
    thread = alerts.notify('order_rejected', {'symbol': 'QQQ'})
    thread.join(timeout=2)
    assert not thread.is_alive() and thread.daemon
    (url, kwargs), = posted.calls
    assert url == 'https://hooks.example.test/pivot'
    assert kwargs['timeout'] == alerts.TIMEOUT_SECONDS == 5
    assert kwargs['json']['kind'] == 'order_rejected' and kwargs['json']['body'] == {'symbol': 'QQQ'}
    assert kwargs['json']['at'].endswith('+00:00')
    monkeypatch.setenv(alerts.ENV_VAR, '   ')
    assert alerts.notify('order_rejected', {}) is None


def test_webhook_failures_are_swallowed(monkeypatch):
    monkeypatch.setattr(requests, 'post', Posted(fail=True))
    monkeypatch.setenv(alerts.ENV_VAR, 'https://hooks.example.test/pivot')
    thread = alerts.notify('strategy_paused', {'family': 'socrates'})
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_store_events_alert_only_for_attention_kinds(tmp_path, monkeypatch):
    posted = Posted()
    monkeypatch.setattr(requests, 'post', posted)
    monkeypatch.setenv(alerts.ENV_VAR, 'https://hooks.example.test/pivot')
    started = []
    real = alerts.notify
    monkeypatch.setattr(alerts, 'notify', lambda kind, body: started.append(real(kind, body)))
    monkeypatch.setattr('pivot.store.notify', alerts.notify)
    monkeypatch.setattr('pivot.crypto_store.notify', alerts.notify)
    store = Store(tmp_path / 'app.db')
    crypto = CryptoStore(store.path)
    for kind in ('entry_planned', 'trade_finished', 'entry_filled', 'settings_saved'):
        store.event(kind, {'symbol': 'QQQ'})
    assert started == []
    for kind in sorted(alerts.ALERT_KINDS):
        store.event(kind, {'symbol': 'QQQ', 'kind': kind})
    store.pause_family('socrates', 'alert test')
    crypto.incident('btc-stop', 'Crypto stop needs attention', symbol='BTC/USD', trade_id='abc')
    crypto.incident('btc-stop', 'Repeated while open', symbol='BTC/USD', trade_id='abc')  # Still open: no second alert.
    for thread in started:
        thread.join(timeout=2)
    sent = [call[1]['json']['kind'] for call in posted.calls]
    assert sorted(sent) == sorted([*alerts.ALERT_KINDS, 'strategy_paused', 'incident_opened'])
    incident = next(call[1]['json'] for call in posted.calls if call[1]['json']['body'].get('id') == 'btc-stop')
    assert incident['body']['message'] == 'Crypto stop needs attention'


def test_executor_rejection_alerts_without_blocking_the_tick(tmp_path, monkeypatch):
    posted = Posted()
    monkeypatch.setattr(requests, 'post', posted)
    monkeypatch.setenv(alerts.ENV_VAR, 'https://hooks.example.test/pivot')
    threads = []
    real = alerts.notify
    monkeypatch.setattr('pivot.store.notify', lambda kind, body: threads.append(real(kind, body)))
    executor, broker, store = engine(tmp_path)
    enable(executor)
    broker.entry_mode = 'reject'
    executor.tick(ready())
    for thread in threads:
        thread.join(timeout=2)
    assert sorted(call[1]['json']['kind'] for call in posted.calls) == ['order_rejected', 'strategy_paused']


# --- clock skew ---------------------------------------------------------------

@pytest.mark.parametrize('skew', [0, -1, -CLOCK_SKEW_SECONDS])
def test_broker_timestamps_slightly_ahead_of_the_host_are_current(skew):
    at = (NOW - timedelta(seconds=skew)).isoformat()
    assert session_open({'is_open': True, 'timestamp': at}, NOW)
    assert checked_quote({'t': at, 'bp': '99.99', 'ap': '100', 'bs': 1, 'as': 1}, NOW) is not None
    assert Executor._regular_session_state({'is_open': False, 'timestamp': at}, NOW) is False


def test_broker_timestamps_beyond_the_skew_allowance_stay_unknown():
    ahead = (NOW + timedelta(seconds=CLOCK_SKEW_SECONDS + 1)).isoformat()
    assert not session_open({'is_open': True, 'timestamp': ahead}, NOW)
    with pytest.raises(Waiting, match='current QQQ quote'):
        checked_quote({'t': ahead, 'bp': '99.99', 'ap': '100', 'bs': 1, 'as': 1}, NOW)
    assert Executor._regular_session_state({'is_open': True, 'timestamp': ahead}, NOW) is None
    stale = (NOW - timedelta(seconds=16)).isoformat()
    assert not session_open({'is_open': True, 'timestamp': stale}, NOW)


def test_host_clock_one_second_behind_alpaca_still_enters(tmp_path):
    executor, broker, store = engine(tmp_path)
    enable(executor)
    original_clock, original_quote = broker.clock, broker.quote
    broker.clock = lambda: {**original_clock(), 'timestamp': (broker.at + timedelta(seconds=1)).isoformat()}
    broker.quote = lambda symbol: {**original_quote(symbol), 't': (broker.at + timedelta(seconds=1)).isoformat()}
    executor.tick(ready())
    assert [o['type'] for o in broker.sent] == ['market', 'stop'] and store.active_trade()['stage'] == 'open'


# --- entry cutoff -------------------------------------------------------------

def test_no_new_entries_in_the_final_thirty_minutes(tmp_path):
    executor, broker, store = engine(tmp_path)
    enable(executor)
    broker.close_in = ENTRY_CUTOFF_SECONDS - 1
    executor.tick(ready())
    assert not broker.sent and executor.message == 'No new entries in the final 30 minutes of the market session'
    assert store.latest_execution_check()['gate'] == 'entry_cutoff'
    broker.close_in = ENTRY_CUTOFF_SECONDS + 60
    executor.tick(ready())
    assert [o['type'] for o in broker.sent] == ['market', 'stop']


def test_prepared_entry_recovered_inside_the_cutoff_expires_without_a_post(tmp_path):
    executor, broker, store = engine(tmp_path)
    enable(executor)
    original = executor._manage
    executor._manage = lambda trade: None
    executor.tick(ready())
    executor._manage = original
    assert store.active_trade()['ops']['entry']['state'] == 'prepared'
    broker.close_in = 1500  # 25 minutes: inside the new cutoff, outside the old ten-minute one.
    executor.tick(ready())
    assert not broker.sent and store.active_trade() is None
    assert 'expired before submission' in executor.message
