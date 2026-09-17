"""Adversarial execution research: in-memory fills, with HTTP blocked by conftest.

These are offline state-machine checks, never paper/live commissioning evidence.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from pivot.execution import Executor, PROTECTION_CONFIRM_SECONDS
from pivot.feeds import FeedError
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, enable, ready


@pytest.fixture
def audit_engine(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'audit.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def test_25_dollar_fractional_fill_and_exit_use_actual_nine_decimal_quantity(audit_engine):
    executor, broker, store = audit_engine
    broker.bid, broker.ask = '674.99', '675.00'
    actual_quantity = Decimal('0.037037037')
    original_fill = broker.fill

    def fill(client_id, quantity=None):
        if client_id.endswith('-entry'):
            broker.book[client_id]['qty'] = str(actual_quantity)
        original_fill(client_id, quantity)

    broker.fill = fill
    snapshot = ready()
    snapshot['setup'].update(entry=675, stop=660, target=690)
    executor.tick(snapshot)
    assert broker.sent[0]['notional'] == '25.00'
    assert 'qty' not in broker.sent[0]
    assert Decimal(broker.sent[1]['qty']) == actual_quantity
    assert broker.sent[1]['time_in_force'] == 'day'
    broker.bid, broker.ask = '690', '690.01'
    executor.tick(snapshot)
    executor.tick(snapshot)
    executor.tick(snapshot)
    assert Decimal(broker.sent[-1]['qty']) == actual_quantity
    assert not broker.position_data and store.active_trade() is None


def test_25_dollar_target_does_not_admit_unavailable_fractional_short(audit_engine):
    executor, broker, store = audit_engine
    broker.bid, broker.ask = '675.00', '675.01'
    snapshot = ready(direction='short')
    snapshot['setup'].update(entry=675, stop=690, target=660)
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert 'quantity' in executor.message


@pytest.mark.parametrize('purpose', ['entry', 'stop', 'exit0'])
@pytest.mark.parametrize('delivery', ['submit_response', 'later_lookup'])
def test_broker_reported_rejection_pauses_new_entries_once_and_preserves_management(
        audit_engine, purpose, delivery):
    executor, broker, store = audit_engine
    original_submit = broker.submit

    def submit(payload):
        is_test_order = payload['client_order_id'].endswith('-' + purpose)
        if not is_test_order:
            return original_submit(payload)
        # A broker can accept HTTP/JSON then reject at the venue. The fake must
        # not execute an order that we are about to report as rejected.
        broker.sent.append(deepcopy(payload))
        client_id = payload['client_order_id']
        quantity = (str(Decimal(payload['notional']) / Decimal(broker.ask))
                    if 'notional' in payload else payload['qty'])
        broker.book[client_id] = {
            **payload, 'id': client_id, 'qty': quantity, 'filled_qty': '0',
            'status': 'rejected' if delivery == 'submit_response' else 'new',
        }
        return broker.lookup(client_id)

    broker.submit = submit
    executor.tick(ready())
    if purpose == 'exit0':
        broker.bid, broker.ask = '110', '110.01'
        executor.tick(ready())  # Await stop cancellation before exit submission.
        executor.tick(ready())
    rejected_id = next(order['client_order_id'] for order in broker.sent
                       if order['client_order_id'].endswith('-' + purpose))
    broker.book[rejected_id]['status'] = 'rejected'
    for _ in range(3):
        executor.tick(ready())
    executor = Executor(broker, Store(store.path), now=lambda: broker.at)
    executor.tick(ready())
    assert not executor.enabled()
    rejected_events = [event for event in store.events() if event['kind'] == 'order_rejected']
    assert len(rejected_events) == 1
    assert len([order for order in broker.sent if order['client_order_id'] == rejected_id]) == 1
    if purpose == 'entry':
        assert not broker.position_data and store.active_trade() is None
    elif purpose == 'stop':
        assert not broker.position_data and store.active_trade() is None
        assert broker.sent[-1]['type'] == 'market' and broker.sent[-1]['side'] == 'sell'
    else:
        assert broker.position_data and store.active_trade()['stage'] == 'attention'
        assert not any(order['client_order_id'].endswith('-exit1') for order in broker.sent)


@pytest.mark.parametrize('accepted', [False, True])
def test_stop_timeout_never_resubmits_after_restart_or_sells_against_unknown_stop(
        audit_engine, accepted):
    executor, broker, store = audit_engine
    original_submit = broker.submit

    def uncertain_stop(payload):
        if payload['type'] != 'stop':
            return original_submit(payload)
        if accepted:
            original_submit(payload)
        else:
            broker.sent.append(deepcopy(payload))
        raise FeedError('Protective order response uncertain')

    broker.submit = uncertain_stop
    executor.tick(ready())
    assert len(broker.sent) == 2 and broker.position_data
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    for _ in range(3):
        recovered.tick(ready())
    assert len(broker.sent) == 2 and not broker.canceled
    assert store.active_trade()['stage'] == 'open'
    if not accepted:
        broker.bid, broker.ask = '110', '110.01'
        recovered.tick(ready())
        assert 'uncertain' in recovered.message.lower()
        assert len(broker.sent) == 2 and not broker.canceled
    else:
        assert store.active_trade()['ops']['stop']['last_seen']['status'] == 'new'


def test_partial_stop_fill_during_delayed_cancel_exits_only_reconciled_remainder(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']

    def delayed_cancel(order_id):
        broker.canceled.append(order_id)
        broker.book[order_id]['status'] = 'pending_cancel'

    broker.cancel = delayed_cancel
    broker.bid, broker.ask = '110', '110.01'
    executor.tick(ready())
    assert len(broker.sent) == 2
    broker.fill(stop_id, '.1')
    broker.book[stop_id]['status'] = 'pending_cancel'
    executor.tick(ready())
    assert len(broker.sent) == 2 and Decimal(broker.position_data[0]['qty']) == Decimal('.15')
    broker.book[stop_id]['status'] = 'canceled'
    executor.tick(ready())
    executor.tick(ready())
    assert len(broker.sent) == 3
    assert Decimal(broker.sent[-1]['qty']) == Decimal('.15')
    assert not broker.position_data and store.active_trade() is None


def test_rejected_stop_with_partial_fill_keeps_fill_evidence_and_closes_only_remainder(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.fill(stop_id, '.1')
    broker.book[stop_id]['status'] = 'rejected'
    executor.tick(ready())
    executor.tick(ready())
    assert not executor.enabled() and not broker.position_data
    assert Decimal(broker.sent[-1]['qty']) == Decimal('.15')
    assert store.active_trade() is None
    assert len([event for event in store.events() if event['kind'] == 'order_rejected']) == 1


def test_day_stop_expiry_outside_session_waits_then_flattens_on_reopening(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = 'expired'
    broker.open = False
    executor.tick(ready())
    assert len(broker.sent) == 2 and broker.position_data
    assert 'outside the regular session' in executor.message
    broker.at += timedelta(days=1)
    broker.open = True
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready(broker.at))
    recovered.tick(ready(broker.at))
    assert [order['type'] for order in broker.sent] == ['market', 'stop', 'market']
    assert not broker.position_data and store.active_trade() is None


def test_exit_timeout_is_reconciled_after_restart_without_second_sale(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    original_submit = broker.submit

    def uncertain_exit(payload):
        response = original_submit(payload)
        if '-exit' in payload['client_order_id']:
            raise FeedError('Exit response uncertain')
        return response

    broker.submit = uncertain_exit
    broker.bid, broker.ask = '110', '110.01'
    executor.tick(ready())
    executor.tick(ready())
    assert len(broker.sent) == 3 and not broker.position_data
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready())
    assert store.active_trade() is None and len(broker.sent) == 3


@pytest.mark.parametrize('status', ['suspended', 'done_for_day', 'pending_cancel', 'pending_replace', 'calculated'])
def test_nonworking_stop_immediately_pauses_and_waits_for_cancel_before_sale(audit_engine, status):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = status
    executor.tick(ready())
    assert not executor.enabled() and len(broker.sent) == 2
    assert store.active_trade()['stage'] == 'exiting'
    assert broker.canceled == [stop_id]
    executor.tick(ready())
    executor.tick(ready())
    assert len(broker.sent) == 3 and not broker.position_data
    assert store.active_trade() is None and not executor.enabled()


@pytest.mark.parametrize('status', ['accepted', 'pending_new', 'accepted_for_bidding', 'stopped', 'future_unknown_status'])
def test_unconfirmed_stop_deadline_survives_restart_and_exits_after_cancel(audit_engine, status):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = status
    original_deadline = store.active_trade()['ops']['stop']['confirmation_deadline']
    executor.tick(ready())
    assert 'not yet confirmed' in executor.message
    broker.at += timedelta(seconds=PROTECTION_CONFIRM_SECONDS - 1)
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready())
    assert recovered.enabled() and len(broker.sent) == 2 and not broker.canceled
    assert store.active_trade()['ops']['stop']['confirmation_deadline'] == original_deadline
    broker.at += timedelta(seconds=1)
    recovered.tick(ready())
    assert not recovered.enabled() and broker.canceled == [stop_id] and len(broker.sent) == 2
    recovered.tick(ready())
    recovered.tick(ready())
    assert store.active_trade() is None and not broker.position_data and len(broker.sent) == 3


def test_old_stop_intent_gets_only_one_persisted_observation_grace_period(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = 'pending_new'
    trade = store.active_trade()
    del trade['ops']['stop']['confirmation_deadline']
    store.save_trade(trade)
    executor.tick(ready())
    first_deadline = store.active_trade()['ops']['stop']['confirmation_deadline']
    broker.at += timedelta(seconds=PROTECTION_CONFIRM_SECONDS)
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready())
    assert not recovered.enabled()
    assert store.active_trade()['ops']['stop']['confirmation_deadline'] == first_deadline
    assert len(broker.sent) == 2 and broker.canceled == [stop_id]


@pytest.mark.parametrize('unknown_kind', ['not_found', 'read_timeout'])
def test_unknown_stop_past_deadline_pauses_without_competing_sale(audit_engine, unknown_kind):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    original_lookup = broker.lookup

    def unknown(client_id):
        if client_id == stop_id:
            if unknown_kind == 'read_timeout':
                raise FeedError('Connection unavailable')
            return None
        return original_lookup(client_id)

    broker.lookup = unknown
    broker.at += timedelta(seconds=PROTECTION_CONFIRM_SECONDS)
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    for _ in range(3):
        recovered.tick(ready())
    assert not recovered.enabled() and len(broker.sent) == 2 and not broker.canceled
    assert store.active_trade()['stage'] == 'exiting' and broker.position_data
    assert 'Alpaca now' in recovered.message and 'uncertain' in recovered.message
    # Once its identity/status becomes known, recovery still cancels first.
    broker.lookup = original_lookup
    recovered.tick(ready())
    assert len(broker.sent) == 2 and broker.canceled == [stop_id]
    recovered.tick(ready())
    recovered.tick(ready())
    assert store.active_trade() is None and len(broker.sent) == 3 and not broker.position_data


def test_uncertain_protection_cancel_keeps_position_isolated_and_calls_for_review(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = 'suspended'

    def uncertain_cancel(order_id):
        broker.canceled.append(order_id)
        raise FeedError('Cancel response lost')

    broker.cancel = uncertain_cancel
    executor.tick(ready())
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready())
    assert not recovered.enabled() and len(broker.sent) == 2 and broker.position_data
    assert store.active_trade()['stage'] == 'exiting'
    assert 'Alpaca now' in recovered.message and 'uncertain' in recovered.message
    broker.fill(stop_id)  # A stop can still fill despite the lost cancel response.
    recovered.tick(ready())
    assert len(broker.sent) == 2 and not broker.position_data and store.active_trade() is None


def test_protection_timeout_is_enforced_even_when_quotes_are_unavailable(audit_engine):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = 'pending_new'
    broker.at += timedelta(seconds=PROTECTION_CONFIRM_SECONDS)
    broker.quote = lambda _: (_ for _ in ()).throw(FeedError('No quote'))
    executor.tick(ready())
    assert not executor.enabled() and len(broker.sent) == 2 and broker.canceled == [stop_id]
    executor.tick(ready())
    executor.tick(ready())
    assert not broker.position_data and store.active_trade() is None


@pytest.mark.parametrize('resumed_state', ['market_closed', 'exiting'])
def test_recovered_unsubmitted_stop_never_creates_an_ineligible_or_unneeded_order(audit_engine, resumed_state):
    executor, broker, store = audit_engine
    original_order = executor._order

    def crash_before_stop_post(trade, name):
        if name == 'stop':
            raise FeedError('Simulated interruption after preparing protection')
        return original_order(trade, name)

    executor._order = crash_before_stop_post
    executor.tick(ready())
    assert len(broker.sent) == 1 and store.active_trade()['ops']['stop']['state'] == 'prepared'
    if resumed_state == 'market_closed':
        broker.open = False
    else:
        trade = store.active_trade()
        trade['stage'] = 'exiting'
        store.save_trade(trade)
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready())
    assert not any(order['type'] == 'stop' for order in broker.sent)
    if resumed_state == 'market_closed':
        assert len(broker.sent) == 1 and broker.position_data
        assert 'outside the regular session' in recovered.message
    else:
        recovered.tick(ready())
        assert len(broker.sent) == 2 and not broker.position_data and store.active_trade() is None


@pytest.mark.parametrize('failed_read', ['account', 'clock', 'positions'])
def test_early_broker_read_failure_does_not_extend_known_unconfirmed_stop_deadline(audit_engine, failed_read):
    executor, broker, store = audit_engine
    executor.tick(ready())
    stop_id = broker.sent[-1]['client_order_id']
    broker.book[stop_id]['status'] = 'pending_new'
    executor.tick(ready())  # The app has observed and persisted the pending state.
    assert store.active_trade()['ops']['stop']['last_seen']['status'] == 'pending_new'
    broker.at += timedelta(seconds=PROTECTION_CONFIRM_SECONDS + 1)
    original = getattr(broker, failed_read)

    def unavailable(*args):
        raise FeedError('Broker read unavailable')

    setattr(broker, failed_read, unavailable)
    recovered = Executor(broker, Store(store.path), now=lambda: broker.at)
    recovered.tick(ready())
    assert not recovered.enabled() and store.active_trade()['stage'] == 'exiting'
    assert store.active_trade()['protection_failure']
    assert len(broker.sent) == 2 and not broker.canceled
    assert 'Alpaca now' in recovered.message
    setattr(broker, failed_read, original)
    broker.account_data['account_ref'] = 'different-account'
    recovered.tick(ready())
    assert len(broker.sent) == 2 and not broker.canceled
    assert 'different Alpaca connection' in recovered.message
    broker.account_data['account_ref'] = 'fake-account-only'
    recovered.tick(ready())
    assert len(broker.sent) == 2 and broker.canceled == [stop_id]
    recovered.tick(ready())
    recovered.tick(ready())
    assert not broker.position_data and store.active_trade() is None


@pytest.mark.parametrize('protected_state', ['working', 'manual_attention'])
def test_early_read_failure_cannot_reclassify_working_protection_or_manual_attention(audit_engine, protected_state):
    executor, broker, store = audit_engine
    executor.tick(ready())
    if protected_state == 'manual_attention':
        trade = store.active_trade()
        trade.update(stage='attention', reason='Foreign order requires owner review')
        trade['ops']['stop']['last_seen']['status'] = 'pending_new'
        store.save_trade(trade)
        store.set_control(False)
    broker.at += timedelta(seconds=PROTECTION_CONFIRM_SECONDS + 1)
    broker.account = lambda: (_ for _ in ()).throw(FeedError('Account unavailable'))
    executor.tick(ready())
    trade = store.active_trade()
    assert 'protection_failure' not in trade
    assert trade['stage'] == ('open' if protected_state == 'working' else 'attention')
    assert executor.enabled() is (protected_state == 'working')
    assert len(broker.sent) == 2 and not broker.canceled
