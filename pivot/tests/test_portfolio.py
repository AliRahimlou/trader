"""Offline multi-strategy admission: no credentials, sockets or broker mutations."""
from copy import deepcopy
from threading import Event, Thread
import pytest

from pivot.execution import Executor
from pivot.portfolio import Portfolio, PortfolioBlocked, canonical_symbol
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, ready, enable


def held(symbol='BTC/USD', identity='crypto-1', *, quantity='.00005', net=None):
    gross = quantity
    trade = {'id': identity, 'account_ref':'fake-account-only', 'symbol':symbol, 'direction':'long',
             'stage':'open', 'amount':'5.00', 'filled_qty':gross, 'ops':{}}
    for name, side, status in [('entry','buy','filled'), ('stop','sell','new')]:
        qty = net or gross if name == 'stop' else gross
        payload = {'symbol':symbol, 'side':side, 'qty':qty, 'client_order_id':identity+'-'+name}
        seen = {**payload, 'id': identity+'-'+name, 'filled_qty':gross if name == 'entry' else '0', 'status':status}
        trade['ops'][name] = {'state':'attempted', 'payload':payload, 'last_seen':seen}
    if net is not None:
        trade['net_entry_qty'] = net
    return trade


def exposure(trade):
    quantity = trade.get('net_entry_qty', trade['filled_qty'])
    return ([{'symbol':trade['symbol'].replace('/',''), 'qty':quantity, 'side':trade['direction']}],
            [deepcopy(trade['ops'][trade.get('stop_op','stop')]['last_seen'])])


@pytest.fixture
def portfolio(tmp_path):
    store = Store(tmp_path/'main.sqlite3')
    active = []
    coordinator = Portfolio(store)
    coordinator.register('range_reversal', lambda: deepcopy(active))
    return coordinator, store, active


def check(p, positions, orders, **kwargs):
    return p.assert_exposure('fake-account-only', positions, orders,
                             requesting_family='socrates', symbol='QQQ', **kwargs)


def test_admission_allows_flat_and_reentrant_scope(portfolio):
    p, _, _ = portfolio
    with p.admit():
        with p.admit():
            check(p, [], [])
            assert p.available('fake-account-only', '15') == '15'
    with pytest.raises(PortfolioBlocked):
        p.available('fake-account-only', '15')


def test_budget_is_shared_and_durable_through_restart(portfolio):
    p, store, active = portfolio
    active.append(held())
    prepared = {'id':'stock1', 'account_ref':'fake-account-only', 'symbol':'QQQ', 'amount':'25', 'stage':'entering'}
    assert store.reserve_trade(prepared)
    with p.admit():
        assert p.available('fake-account-only', '100') == '70.00'
        assert p.available('fake-account-only', '10') == '0'
    reboot = Portfolio(Store(store.path))
    reboot.register('range_reversal', lambda: deepcopy(active))
    with reboot.admit():
        assert reboot.available('fake-account-only', '100') == '70.00'
    store.save_trade(prepared, finished=True)
    with p.admit():
        assert p.available('fake-account-only', '100') == '95.00'


def test_two_process_coordinators_cannot_admit_at_once(portfolio):
    p, store, _ = portfolio
    other = Portfolio(store)
    with p.admit():
        with pytest.raises(PortfolioBlocked):
            with other.admit():
                pytest.fail('Second process admission must not proceed')
    with other.admit():
        pass


def test_threads_serialize_the_entire_planning_interval(portfolio):
    p, _, _ = portfolio
    waiting, entered, finished = Event(), Event(), Event()
    def second():
        waiting.set()
        with p.admit():
            entered.set()
        finished.set()
    with p.admit():
        thread = Thread(target=second)
        thread.start()
        assert waiting.wait(1)
        assert not entered.is_set()
    assert finished.wait(1)
    thread.join(1)
    assert entered.is_set()


def test_protected_app_owned_crypto_can_coexist_with_socrates(portfolio):
    p, _, active = portfolio
    active.append(held(net='.0000499'))
    check(p, *exposure(active[0]))


@pytest.mark.parametrize('mutation', [
    lambda t,p,o: p[0].update(qty='.1'),
    lambda t,p,o: p[0].update(side='short'),
    lambda t,p,o: p.append(deepcopy(p[0])),
    lambda t,p,o: o[0].update(client_order_id='manual'),
    lambda t,p,o: o[0].update(id='outside-replacement'),
    lambda t,p,o: o[0].update(qty='.00001'),
    lambda t,p,o: o[0].update(status='pending_cancel'),
    lambda t,p,o: o[0].update(legs=[{'symbol':'QQQ'}]),
    lambda t,p,o: o.clear(),
    lambda t,p,o: p.clear(),
    lambda t,p,o: t.update(stage='entering'),
    lambda t,p,o: t.update(stage='attention'),
    lambda t,p,o: t.update(stage='exiting'),
    lambda t,p,o: t['ops']['entry']['last_seen'].update(status='partially_filled'),
    lambda t,p,o: t['ops']['stop']['last_seen'].update(status='accepted'),
    lambda t,p,o: t.update(net_entry_qty='.00006'),
    lambda t,p,o: t.update(account_ref='another-account'),
])
def test_unproven_exposure_never_gets_a_crypto_exemption(portfolio, mutation):
    p, _, active = portfolio
    active.append(held())
    positions, orders = exposure(active[0])
    mutation(active[0],positions,orders)
    with pytest.raises(PortfolioBlocked):
        check(p,positions,orders)


def test_unknown_holdings_and_pending_unknown_intents_block(portfolio):
    p, _, active = portfolio
    with pytest.raises(PortfolioBlocked):
        check(p,[{'symbol':'BTCUSD','qty':'.00005','side':'long'}],[])
    active.append(held())
    active[0]['stage']='entering'
    with pytest.raises(PortfolioBlocked):
        check(p,[],[])


def test_same_instrument_cannot_be_shared_but_own_unsent_claim_can_recover(portfolio):
    p, store, active = portfolio
    stock = held('QQQ','stock')
    assert store.reserve_trade(stock)
    with pytest.raises(PortfolioBlocked):
        check(p,*exposure(stock))
    # Exclusion removes only this family's intent, never foreign current holdings.
    check(p,[],[], excluding_trade_id='stock')
    with pytest.raises(PortfolioBlocked):
        check(p,*exposure(stock), excluding_trade_id='stock')


def test_crypto_can_allow_a_protected_stock_while_entering_different_asset(portfolio):
    p, store, _ = portfolio
    stock = held('QQQ','stock', quantity='.05')
    assert store.reserve_trade(stock)
    p.assert_exposure('fake-account-only', *exposure(stock), requesting_family='range_reversal',symbol='BTC/USD')


def test_numbered_replacement_stop_uses_durable_current_stop_name(portfolio):
    p, _, active = portfolio
    trade = held()
    trade['ops']['stop2'] = trade['ops'].pop('stop')
    trade['stop_op']='stop2'
    active.append(trade)
    check(p,*exposure(trade))


def test_bad_ledger_fails_closed_and_does_not_expose_exception(portfolio):
    p, _, _ = portfolio
    p.register('bad', lambda: (_ for _ in ()).throw(RuntimeError('secret-api-key')))
    with pytest.raises(PortfolioBlocked, match='ledger is unavailable') as error:
        p.active_trades()
    assert 'secret' not in str(error.value)


def test_invalid_allocator_fields_fail_closed(portfolio):
    p, _, active = portfolio
    active.append(held())
    active[0]['amount']='NaN'
    with p.admit(), pytest.raises(PortfolioBlocked):
        p.available('fake-account-only','100')


def test_late_registration_and_symlink_lock_are_rejected(portfolio,tmp_path):
    p, store, _ = portfolio
    with p.admit():
        pass
    with pytest.raises(ValueError):
        p.register('late',lambda:[])
    path=tmp_path/'lock-link'
    path.symlink_to(tmp_path/'elsewhere')
    with pytest.raises(PortfolioBlocked):
        with Portfolio(store,lock_path=path).admit():
            pass
    assert not (tmp_path/'elsewhere').exists()


def test_only_known_crypto_aliases_normalize():
    assert canonical_symbol('BTCUSD') == 'BTC/USD'
    assert canonical_symbol('QQQ') == 'QQQ'
    assert canonical_symbol('ABCUSD') == 'ABCUSD'


def test_disabled_socrates_still_manages_owned_position(portfolio):
    p, store, _ = portfolio
    broker=FakeBroker()
    executor=Executor(broker,store,now=lambda:broker.at)
    executor.portfolio=p
    enable(executor)
    executor.tick(ready())
    assert store.active_trade()['stage']=='open' and len(broker.sent)==2
    p.enabled_predicate=lambda family:False
    assert not executor.enabled() and store.control()['enabled'] is True
    broker.bid=broker.ask='111'
    for _ in range(4):
        executor.tick(ready())
    assert store.active_trade() is None and len(broker.sent)==3


def test_disabled_socrates_never_enters_and_unknown_control_fails_closed(portfolio):
    p, store, _ = portfolio
    broker=FakeBroker(); executor=Executor(broker,store,now=lambda:broker.at)
    executor.portfolio=p
    enable(executor)
    p.enabled_predicate=lambda family:False
    executor.tick(ready())
    assert not broker.sent
    p.enabled_predicate=lambda family:(_ for _ in ()).throw(RuntimeError('unreadable'))
    assert not executor.enabled()


def test_socrates_hook_keeps_crypto_and_qqq_positions_separate(portfolio):
    p, store, active = portfolio
    active.append(held())
    crypto_positions, crypto_orders = exposure(active[0])
    class SharedBroker(FakeBroker):
        def positions(self): return super().positions()+deepcopy(crypto_positions)
        def orders(self): return super().orders()+deepcopy(crypto_orders)
    broker=SharedBroker(); executor=Executor(broker,store,now=lambda:broker.at)
    executor.portfolio=p
    enable(executor)
    executor.tick(ready())
    assert store.active_trade()['stage']=='open', executor.message
    assert [order['symbol'] for order in broker.sent]==['QQQ','QQQ']
    assert crypto_positions==exposure(active[0])[0]


def test_socrates_cannot_spend_reserved_other_strategy_allocation(portfolio):
    p, store, active = portfolio
    active.append(held())
    positions, orders=exposure(active[0])
    class SharedBroker(FakeBroker):
        def positions(self): return deepcopy(positions)
        def orders(self): return deepcopy(orders)
    broker=SharedBroker(); broker.account_data['buying_power']='27'
    executor=Executor(broker,store,now=lambda:broker.at); executor.portfolio=p
    enable(executor); executor.tick(ready())
    assert not broker.sent and store.active_trade() is None
