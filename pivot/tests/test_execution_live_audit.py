"""Broker-free regressions for stalled exits and recovery of unsent entries."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from pivot.execution import EXIT_CONFIRM_SECONDS, Executor
from pivot.feeds import FeedError
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, enable, ready


@pytest.fixture
def runtime(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'execution-audit.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def restart(runtime):
    _, broker, store = runtime
    return Executor(broker, Store(store.path), now=lambda: broker.at)


def prepared_entry(runtime):
    executor, broker, store = runtime
    manage = executor._manage
    executor._manage = lambda trade: None
    executor.tick(ready())
    executor._manage = manage
    assert not broker.sent
    assert store.active_trade()['ops']['entry']['state'] == 'prepared'
    return store.active_trade()


def pending_exit(runtime, filled='.1'):
    executor, broker, store = runtime
    executor.tick(ready())
    submit = broker.submit

    def hold_exit(payload):
        if '-exit' not in payload['client_order_id']:
            return submit(payload)
        broker.sent.append(deepcopy(payload))
        cid = payload['client_order_id']
        broker.book[cid] = {**payload, 'id': cid, 'filled_qty': '0', 'status': 'new'}
        if Decimal(filled):
            broker.fill(cid, filled)
        return broker.lookup(cid)

    broker.submit = hold_exit
    broker.bid, broker.ask = '110', '110.01'
    executor.tick({})  # Request stop cancellation.
    executor.tick({})  # Confirm cancellation and send exactly one exit.
    assert store.active_trade()['stage'] == 'exiting'
    assert len(broker.sent) == 3
    assert broker.book[broker.sent[1]['client_order_id']]['status'] == 'canceled'
    return broker.sent[-1]['client_order_id']


@pytest.mark.parametrize('filled', ['0', '.1'])
def test_pending_exit_deadline_survives_restart_and_never_duplicates_order(runtime, filled):
    executor, broker, store = runtime
    pending_exit(runtime, filled)
    original = deepcopy(store.active_trade()['exit_pending'])
    broker.at += timedelta(seconds=EXIT_CONFIRM_SECONDS - 1)
    recovered = restart(runtime)
    recovered.tick({})
    assert recovered.enabled()
    assert len(broker.sent) == 3
    broker.at += timedelta(seconds=1)
    recovered.tick({})
    trade = store.active_trade()
    assert not recovered.enabled()
    assert trade['stage'] == 'exiting'
    assert trade['exit_pending']['confirmation_deadline'] == original['confirmation_deadline']
    assert trade['exit_pending']['raised_at'] == broker.at.isoformat()
    assert 'Check the QQQ position' in recovered.message
    assert recovered.snapshot()['execution']['trade']['exit_pending']['raised_at']
    assert recovered.execution_check['outcome'] == 'attention'
    assert len(broker.sent) == 3
    assert Decimal(broker.position_data[0]['qty']) == Decimal('.25') - Decimal(filled)
    # Re-enabling cannot suppress an unresolved incident or duplicate its event.
    enable(recovered)
    recovered.tick({})
    assert not recovered.enabled()
    assert len([event for event in store.events() if event['kind'] == 'exit_needs_attention']) == 1
    assert len(broker.sent) == 3


def test_stuck_stop_cancellation_escalates_then_reconciles_without_competing_sale(runtime):
    executor, broker, store = runtime
    executor.tick(ready())
    stop_id = broker.sent[1]['client_order_id']

    def pending_cancel(order_id):
        broker.canceled.append(order_id)
        broker.book[order_id]['status'] = 'pending_cancel'

    broker.cancel = pending_cancel
    broker.bid, broker.ask = '110', '110.01'
    executor.tick({})
    original_deadline = store.active_trade()['exit_pending']['confirmation_deadline']
    broker.at += timedelta(seconds=EXIT_CONFIRM_SECONDS)
    recovered = restart(runtime)
    recovered.tick({})
    assert not recovered.enabled()
    assert len(broker.sent) == 2
    assert store.active_trade()['exit_pending']['confirmation_deadline'] == original_deadline
    # A late stop fill must be reconciled rather than followed by a second sale.
    broker.fill(stop_id)
    recovered.tick({})
    assert store.active_trade() is None and not broker.position_data
    assert len(broker.sent) == 2 and not recovered.enabled()
    assert len([event for event in store.events() if event['kind'] == 'exit_reconciled']) == 1


def test_partial_exit_eventual_fill_finishes_while_new_entries_remain_paused(runtime):
    executor, broker, store = runtime
    exit_id = pending_exit(runtime)
    broker.at += timedelta(seconds=EXIT_CONFIRM_SECONDS)
    recovered = restart(runtime)
    recovered.tick({})
    broker.fill(exit_id)
    recovered.tick({})
    assert store.active_trade() is None and not broker.position_data
    assert not recovered.enabled() and len(broker.sent) == 3
    assert len([event for event in store.events() if event['kind'] == 'exit_reconciled']) == 1


@pytest.mark.parametrize('failed_read', ['account', 'clock', 'positions', 'orders', 'lookup'])
def test_exit_deadline_and_incident_survive_broker_read_failures(runtime, failed_read):
    executor, broker, store = runtime
    pending_exit(runtime)
    broker.at += timedelta(seconds=EXIT_CONFIRM_SECONDS)

    def unavailable(*args):
        raise FeedError('Isolated fake outage')

    setattr(broker, failed_read, unavailable)
    recovered = restart(runtime)
    recovered.tick({})
    assert not recovered.enabled() and len(broker.sent) == 3
    assert store.active_trade()['stage'] == 'exiting'
    assert store.active_trade()['exit_pending']['raised_at']
    assert 'Check the QQQ position' in recovered.message
    recovered.tick({})
    assert len([event for event in store.events() if event['kind'] == 'exit_needs_attention']) == 1


def test_old_exiting_trade_receives_one_durable_deadline(runtime):
    executor, broker, store = runtime
    pending_exit(runtime)
    trade = store.active_trade()
    trade.pop('exit_pending')
    store.save_trade(trade)
    recovered = restart(runtime)
    recovered.tick({})
    deadline = store.active_trade()['exit_pending']['confirmation_deadline']
    broker.at += timedelta(seconds=EXIT_CONFIRM_SECONDS)
    recovered = restart(runtime)
    recovered.tick({})
    assert not recovered.enabled()
    assert store.active_trade()['exit_pending']['confirmation_deadline'] == deadline
    assert len(broker.sent) == 3


@pytest.mark.parametrize('symbol', ['QQQ', 'AAPL'])
def test_recovered_prepared_entry_never_adds_to_foreign_position(runtime, symbol):
    executor, broker, store = runtime
    trade = prepared_entry(runtime)
    broker.position_data = [{'symbol': symbol, 'qty': '1', 'side': 'long'}]
    recovered = restart(runtime)
    recovered.tick({})
    assert not broker.sent
    assert store.active_trade()['ops']['entry']['state'] == 'prepared'
    assert store.active_trade()['id'] == trade['id']
    assert 'existing broker positions' in recovered.message
    assert recovered.execution_check['gate'] == 'existing_exposure'
    # Clearing the unrelated exposure permits normal admission, without
    # extending the original validity window or consuming an attempted ID.
    broker.position_data = []
    recovered.tick({})
    assert [order['type'] for order in broker.sent] == ['market', 'stop']


def test_recovered_prepared_entry_waits_for_foreign_order(runtime):
    executor, broker, store = runtime
    prepared_entry(runtime)
    broker.book['foreign'] = {'id': 'foreign', 'client_order_id': 'foreign',
                              'symbol': 'QQQ', 'status': 'new', 'side': 'buy', 'qty': '1', 'filled_qty': '0'}
    recovered = restart(runtime)
    recovered.tick({})
    assert not broker.sent and not broker.canceled
    assert store.active_trade()['ops']['entry']['state'] == 'prepared'


@pytest.mark.parametrize('change', [
    {'status': 'ACCOUNT_UPDATED'}, {'trading_blocked': True}, {'account_blocked': True},
    {'trade_suspended_by_user': True}, {'mode': 'paper'}, {'account_ref': 'another-account'},
])
def test_recovered_prepared_entry_rechecks_account_permission(runtime, change):
    executor, broker, store = runtime
    prepared_entry(runtime)
    broker.account_data.update(change)
    restart(runtime).tick({})
    assert not broker.sent
    assert store.active_trade()['ops']['entry']['state'] == 'prepared'


@pytest.mark.parametrize('failed_read', ['account', 'positions', 'orders'])
def test_prepared_recheck_failure_is_unsent_and_does_not_consume_claim(runtime, failed_read):
    executor, broker, store = runtime
    prepared_entry(runtime)
    original = getattr(broker, failed_read)

    def unavailable(*args):
        raise FeedError('Isolated fake outage')

    setattr(broker, failed_read, unavailable)
    recovered = restart(runtime)
    recovered.tick({})
    assert not broker.sent and store.active_trade()['ops']['entry']['state'] == 'prepared'
    setattr(broker, failed_read, original)
    broker.at += timedelta(seconds=11)
    recovered.tick({})
    assert not broker.sent and store.active_trade() is None


def test_attempted_entry_reconciliation_does_not_apply_new_entry_exposure_gate(runtime):
    executor, broker, store = runtime
    broker.entry_mode = 'lost_accepted'
    executor.tick(ready())
    assert len(broker.sent) == 1 and broker.position_data
    broker.account_data['trading_blocked'] = True
    recovered = restart(runtime)
    recovered.tick({})
    assert [order['type'] for order in broker.sent] == ['market', 'stop']
    assert store.active_trade()['stage'] == 'open'


@pytest.mark.parametrize('block', ['position', 'account_flag', 'account_identity', 'account_unavailable'])
def test_expired_prepared_entry_retires_despite_persistent_broker_block(runtime, block):
    executor, broker, store = runtime
    trade = prepared_entry(runtime)
    if block == 'position':
        broker.position_data = [{'symbol': 'QQQ', 'qty': '1', 'side': 'long'}]
    elif block == 'account_flag':
        broker.account_data['trading_blocked'] = True
    elif block == 'account_identity':
        broker.account_data['account_ref'] = 'another-account'
    else:
        def unavailable():
            raise FeedError('Isolated fake outage')
        broker.account = unavailable
    recovered = restart(runtime)
    recovered.tick({})
    assert not broker.sent and store.active_trade() is not None
    broker.at += timedelta(seconds=11)
    recovered.tick({})
    assert not broker.sent and store.active_trade() is None
    assert not store.entry_consumed(trade['id'])
