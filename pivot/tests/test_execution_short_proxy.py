"""Inverse-ETF short proxy (4.5.0): a Socrates SHORT is executed by BUYING PSQ.

App interpretation, not a video rule: the recordings short Nasdaq futures. This
cash account cannot short and $15 cannot form a whole QQQ share, so the QQQ
short geometry is mirrored onto a PSQ purchase. FakeBroker only; no network.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from pivot.broker import AlpacaBroker, PROXY_KIND, PROXY_SYMBOL, PROXY_SYMBOLS, SIGNAL_SYMBOL
from pivot.execution import EXECUTION_GATES, Executor, ORDER_NOT_FOUND_CONFIRM_SECONDS, proxy_translation
from pivot.feeds import FeedError
from pivot.portfolio import Portfolio, PortfolioBlocked
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready
from pivot.tests.test_lost_order_resolution import LossyBroker, advance


def runtime(tmp_path, broker=None):
    broker = broker or FakeBroker()
    broker.bid, broker.ask = '100', '100.01'  # A short's QQQ reference is the bid; geometry 110 / 100 / 90.
    store = Store(tmp_path / 'proxy.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def kinds(store):
    return [event['kind'] for event in store.events()]


def event(store, kind):
    return next(row['detail'] for row in store.events() if row['kind'] == kind)


def test_owned_symbols_and_gates_name_the_proxy():
    assert PROXY_SYMBOLS == frozenset({'QQQ', 'PSQ'}) and SIGNAL_SYMBOL == 'QQQ' and PROXY_SYMBOL == 'PSQ'
    assert 'proxy_eligibility' in EXECUTION_GATES
    assert not {'short_eligibility', 'short_reserve'} & EXECUTION_GATES


@pytest.mark.parametrize('ask,entry,stop,target,expected', [
    ('35', 100, 110, 90, ('35', '31.50', '38.50')),
    ('33.20', 675, 690, 660, ('33.20', '32.46', '33.94')),
    ('35', '100', '100.10', '99.80', ('35', '34.97', '35.07')),  # 0.1% stop and 0.2% target survive cent rounding.
])
def test_proxy_translation_mirrors_fractional_distances_and_rounds_to_cents(ask, entry, stop, target, expected):
    assert tuple(map(str, proxy_translation(ask, entry, stop, target))) == expected


@pytest.mark.parametrize('geometry', [(100, 90, 110), (100, 100, 90), (100, 110, 100), (0, 110, 90)])
def test_proxy_translation_rejects_non_short_geometry(geometry):
    with pytest.raises(ValueError):
        proxy_translation('35', *geometry)


def test_short_entry_records_both_geometries_for_the_journal_and_diagnostics(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready(direction='short'))
    trade = store.active_trade()
    assert trade['stage'] == 'open' and trade['symbol'] == 'PSQ' and trade['direction'] == 'long'
    assert (trade['signal_symbol'], trade['signal_direction'], trade['proxy']) == ('QQQ', 'short', PROXY_KIND)
    assert trade['signal_geometry'] == {'symbol': 'QQQ', 'direction': 'short', 'entry': '100', 'stop': '110', 'target': '90'}
    assert trade['proxy_geometry']['symbol'] == 'PSQ' and trade['proxy_geometry']['reference'] == '35'
    assert (trade['proxy_geometry']['stop'], trade['proxy_geometry']['target']) == (trade['stop'], trade['target']) == ('31.50', '38.50')
    assert 'not a video rule' in trade['proxy_geometry']['note']
    planned = event(store, 'entry_planned')
    assert planned['symbol'] == 'PSQ' and planned['signal_direction'] == 'short' and planned['proxy'] == PROXY_KIND
    filled = event(store, 'entry_filled')
    assert filled['symbol'] == 'PSQ' and filled['signal_symbol'] == 'QQQ' and filled['proxy'] == PROXY_KIND
    check = store.execution_history(1, None)['entries'][0]
    assert check['trade']['symbol'] == 'PSQ' and check['trade']['direction'] == 'long'
    assert check['trade']['signal_symbol'] == 'QQQ' and check['trade']['signal_direction'] == 'short'
    assert check['trade']['proxy'] == PROXY_KIND
    snapshot = executor.snapshot()['execution']['trade']
    assert snapshot['symbol'] == 'PSQ' and snapshot['signal_direction'] == 'short' and snapshot['proxy'] == PROXY_KIND
    assert snapshot['proxy_geometry']['stop'] == '31.50' and snapshot['signal_geometry']['stop'] == '110'
    executor.tick(ready())
    assert 'Managing PSQ (inverse-ETF proxy for the QQQ short)' in executor.message


def test_long_setups_are_unchanged_and_never_read_the_proxy_quote(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.bid, broker.ask = '99.99', '100'
    read = []
    original = broker.quote
    broker.quote = lambda symbol: read.append(symbol) or original(symbol)
    executor.tick(ready())
    assert read == ['QQQ', 'QQQ']  # Entry admission, then management after the fill; never PSQ.
    assert [(o['symbol'], o['side'], o['type']) for o in broker.sent] == [('QQQ', 'buy', 'market'), ('QQQ', 'sell', 'stop')]
    assert broker.sent[0]['notional'] == '25.00'
    trade = store.active_trade()
    assert trade['symbol'] == 'QQQ' and trade['direction'] == trade['signal_direction'] == 'long'
    assert trade['proxy'] is None and trade['proxy_geometry'] is None
    assert trade['signal_geometry'] == {'symbol': 'QQQ', 'direction': 'long', 'entry': '100', 'stop': '90', 'target': '110'}
    assert (trade['stop'], trade['target']) == ('90', '110')


def test_non_fractionable_proxy_buys_whole_shares_within_one_percent_of_the_target(tmp_path):
    executor, broker, store = runtime(tmp_path)
    store.save({'sizing_mode': 'target', 'target_dollars': '100.00'})
    broker.assets['PSQ'] = {'fractionable': False}
    broker.quotes['PSQ'] = ('33.29', '33.30')  # Three whole shares = $99.90, inside 1% of $100.
    executor.tick(ready(direction='short'))
    assert broker.sent[0]['symbol'] == 'PSQ' and broker.sent[0]['qty'] == '3' and 'notional' not in broker.sent[0]
    assert broker.sent[1] ['type'] == 'stop' and broker.sent[1]['qty'] == '3' and broker.sent[1]['side'] == 'sell'
    assert store.active_trade()['stage'] == 'open' and store.active_trade()['amount'] == '100.00'


@pytest.mark.parametrize('exit_kind', ['stop', 'target', 'session_close'])
def test_proxy_position_exits_are_sales_of_psq(tmp_path, exit_kind):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready(direction='short'))
    assert broker.position_data == [{'symbol': 'PSQ', 'qty': str(Decimal('25') / Decimal('35')), 'side': 'long'}]
    if exit_kind == 'stop':
        broker.quotes['PSQ'] = ('31.50', '31.51')  # PSQ bid at the translated stop.
    elif exit_kind == 'target':
        broker.quotes['PSQ'] = ('38.50', '38.51')
    else:
        broker.close_in = 250
    for _ in range(3):
        executor.tick(ready())
    assert broker.canceled == [broker.sent[1]['client_order_id']]
    assert broker.sent[-1] == {**broker.sent[-1], 'symbol': 'PSQ', 'side': 'sell', 'type': 'market'}
    assert Decimal(broker.sent[-1]['qty']) == Decimal('25') / Decimal('35')
    assert not broker.position_data and store.active_trade() is None
    assert event(store, 'exit_started')['symbol'] == 'PSQ' and event(store, 'trade_finished')['symbol'] == 'PSQ'
    assert not any(o['symbol'] == 'QQQ' for o in broker.sent)


def test_lost_psq_stop_post_resolves_with_the_trades_symbol(tmp_path):
    executor, broker, store = runtime(tmp_path, LossyBroker())
    broker.lose = {'stop'}
    executor.tick(ready(direction='short'))
    assert [(o['symbol'], o['type']) for o in broker.sent] == [('PSQ', 'market'), ('PSQ', 'stop')] and broker.position_data
    advance(executor, broker, 30)
    assert len(broker.sent) == 2  # No duplicate stop while the outcome is uncertain.
    # An unrelated QQQ order in the book is not evidence about the PSQ stop.
    broker.book['other'] = {'id': 'other', 'client_order_id': 'someone-else', 'symbol': 'QQQ', 'side': 'sell',
                            'qty': '1', 'filled_qty': '0', 'status': 'new', 'type': 'limit', 'time_in_force': 'day'}
    advance(executor, broker, ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert broker.sent[-1]['symbol'] == 'PSQ' and broker.sent[-1]['side'] == 'sell' and broker.sent[-1]['type'] == 'market'
    assert not broker.position_data and store.active_trade() is None
    assert event(store, 'order_not_found')['symbol'] == 'PSQ' and event(store, 'protection_failed')['symbol'] == 'PSQ'
    assert not executor.enabled()


def test_lost_psq_stop_resolution_waits_while_a_foreign_psq_order_exists(tmp_path):
    executor, broker, store = runtime(tmp_path, LossyBroker())
    broker.lose = {'stop'}
    executor.tick(ready(direction='short'))
    broker.book['foreign'] = {'id': 'foreign', 'client_order_id': 'someone-else', 'symbol': 'PSQ', 'side': 'sell',
                              'qty': '1', 'filled_qty': '0', 'status': 'new', 'type': 'limit', 'time_in_force': 'day'}
    advance(executor, broker, 30 + ORDER_NOT_FOUND_CONFIRM_SECONDS)
    # The unexplained PSQ order keeps the lost stop unresolved: no duplicate stop,
    # no competing sale, the position stays held and the owner is pointed at PSQ.
    assert len(broker.sent) == 2 and 'order_not_found' not in kinds(store) and broker.position_data
    assert store.active_trade()['stage'] == 'exiting' and store.active_trade()['protection_failure']
    assert 'Check the PSQ position and orders in Alpaca now' in executor.message
    assert event(store, 'protection_failed')['symbol'] == 'PSQ' and not executor.enabled()


@pytest.mark.parametrize('direction', ['long', 'short'])
@pytest.mark.parametrize('foreign', ['PSQ', 'QQQ'])
def test_foreign_positions_in_either_owned_symbol_block_entries(tmp_path, direction, foreign):
    executor, broker, store = runtime(tmp_path)
    broker.position_data = [{'symbol': foreign, 'qty': '1', 'side': 'long'}]
    executor.tick(ready(direction=direction))
    assert not broker.sent and store.active_trade() is None
    assert 'existing broker positions' in executor.message
    portfolio = Portfolio(store)
    executor.portfolio = portfolio
    executor.tick(ready(direction=direction))
    assert not broker.sent and 'reconciliation' in executor.message
    check = store.execution_history(1, None)['entries'][0]
    assert check['gate'] == 'existing_exposure'


def test_short_entry_counts_once_against_the_socrates_allowance(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready(direction='short'))
    assert store.session_entry_allowance(broker.at)['families'] == {
        'socrates': {'used': 1, 'remaining': 1}, 'range_reversal': {'used': 0, 'remaining': 2}}
    broker.quotes['PSQ'] = ('38.50', '38.51')
    for _ in range(3):
        executor.tick(ready())
    assert store.active_trade() is None
    assert store.session_entry_allowance(broker.at)['families']['socrates'] == {'used': 1, 'remaining': 1}


@pytest.mark.parametrize('case', ['stale', 'spread', 'below_stop'])
def test_proxy_quote_follows_the_same_freshness_spread_and_geometry_rules(tmp_path, case):
    executor, broker, store = runtime(tmp_path)
    snapshot = ready(direction='short')
    if case == 'stale':
        original = broker.quote
        def quote(symbol):
            result = original(symbol)
            if symbol == 'PSQ':
                result['t'] = (broker.at - timedelta(seconds=16)).isoformat()
            return result
        broker.quote = quote
        expected, gate = 'current PSQ quote', 'quote_stale'
    elif case == 'spread':
        broker.quotes['PSQ'] = ('34.80', '35')
        expected, gate = 'PSQ spread', 'quote_spread'
    else:
        broker.quotes['PSQ'] = ('34.90', '35')  # 0.29% spread; the 0.2% translated stop sits above the bid.
        snapshot['setup'].update(stop=100.2, target=99.8)
        expected, gate = 'PSQ is not between its translated stop and target', 'price_geometry'
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None and executor.enabled()
    assert expected in executor.message
    check = store.execution_history(1, None)['entries'][0]
    assert check['outcome'] == 'waiting' and check['gate'] == gate


def test_qqq_drift_still_gates_a_short_before_the_proxy_is_read(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.bid, broker.ask = '98.50', '98.51'  # 1.5% below the signal reference, still inside 90-110.
    read = []
    original = broker.quote
    broker.quote = lambda symbol: read.append(symbol) or original(symbol)
    executor.tick(ready(direction='short'))
    assert not broker.sent and 'moved more than 1%' in executor.message and read == ['QQQ']
    broker.bid, broker.ask = '111', '111.01'  # QQQ above the short's stop: no proxy entry either.
    broker.at += timedelta(seconds=1)
    executor.tick(ready(at=broker.at, direction='short'))
    assert not broker.sent and 'left the entry area' in executor.message and read == ['QQQ', 'QQQ']


def test_rejected_psq_entry_pauses_socrates_naming_the_proxy(tmp_path):
    executor, broker, store = runtime(tmp_path)
    broker.entry_mode = 'reject'
    executor.tick(ready(direction='short'))
    assert len(broker.sent) == 1 and broker.sent[0]['symbol'] == 'PSQ' and store.active_trade() is None
    assert not executor.enabled() and store.control()['enabled'] is True
    assert 'The broker rejected a PSQ entry order' in event(store, 'strategy_paused')['reason']
    assert event(store, 'order_rejected')['symbol'] == 'PSQ'
    assert store.session_entry_allowance(broker.at)['families']['socrates']['used'] == 1


def test_manual_resolution_of_a_psq_trade_is_confirmed_flat_on_psq(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready(direction='short'))
    broker.position_data = []
    broker.book[broker.sent[-1]['client_order_id']]['status'] = 'canceled'
    executor.tick(ready())
    trade = store.active_trade()
    assert trade['stage'] == 'attention' and 'PSQ is flat' in trade['reason']
    assert event(store, 'execution_needs_attention')['symbol'] == 'PSQ'
    broker.position_data = [{'symbol': 'QQQ', 'qty': '1', 'side': 'long'}]  # Unrelated QQQ exposure is not PSQ evidence.
    executor.tick(ready())
    assert store.active_trade() is None and 'Socrates remains paused' in executor.message
    assert event(store, 'manual_resolution_confirmed')['symbol'] == 'PSQ'
    assert len(broker.sent) == 2 and not executor.enabled()


def test_recovered_prepared_psq_entry_submits_with_its_own_symbol(tmp_path):
    executor, broker, store = runtime(tmp_path)
    original_submit = broker.submit
    def prepare_only(payload):
        raise RuntimeError('crash before the request left this process')
    broker.submit = prepare_only
    executor.tick(ready(direction='short'))
    trade = store.active_trade()
    assert trade is not None and trade['symbol'] == 'PSQ' and trade['ops']['entry']['state'] == 'attempted'
    # The claimed-but-unsent entry resolves through the lost-request path and never re-POSTs.
    broker.submit = original_submit
    advance(executor, broker, 30 + ORDER_NOT_FOUND_CONFIRM_SECONDS)
    assert store.active_trade() is None and not broker.sent


class ProxyFeeds:
    """Offline stand-in for ReadOnlyFeeds: records paths, serves canned responses."""
    feed = 'iex'

    def __init__(self, response):
        self.response, self.calls = response, []

    def quote(self):
        self.calls.append('feeds.quote')
        return {'t': NOW.isoformat(), 'bp': 99.99, 'ap': 100.0, 'bs': 1.0, 'as': 1.0}

    def get(self, provider, path, params=None):
        self.calls.append((provider, path, params))
        if isinstance(self.response, Exception):
            raise self.response
        return deepcopy(self.response)


def test_broker_adapter_reads_the_proxy_quote_from_its_own_symbol_path():
    feeds = ProxyFeeds({'symbol': 'PSQ', 'quote': {'t': NOW.isoformat(), 'bp': '34.99', 'ap': '35', 'bs': '3', 'as': '4'}})
    broker = AlpacaBroker(feeds, session=object())
    assert broker.quote('QQQ')['ap'] == 100.0 and feeds.calls == ['feeds.quote']
    quote = broker.quote('PSQ')
    assert feeds.calls[-1] == ('stocks', '/v2/stocks/PSQ/quotes/latest', {'feed': 'iex'})
    assert quote == {'t': NOW.isoformat(), 'bp': 34.99, 'ap': 35.0, 'bs': 3.0, 'as': 4.0}
    with pytest.raises(ValueError):
        broker.quote('SQQQ')


@pytest.mark.parametrize('response', [
    {'symbol': 'QQQ', 'quote': {'t': NOW.isoformat(), 'bp': '34.99', 'ap': '35', 'bs': '3', 'as': '4'}},
    {'symbol': 'PSQ', 'quote': {'t': NOW.isoformat(), 'bp': '0', 'ap': '35', 'bs': '3', 'as': '4'}},
    {'symbol': 'PSQ', 'quote': {'t': 'soon', 'bp': '34.99', 'ap': '35', 'bs': '3', 'as': '4'}},
    {'symbol': 'PSQ', 'quote': 'nan'}, [], FeedError('stocks: HTTP 500'),
])
def test_broker_adapter_rejects_invalid_proxy_quotes_as_feed_errors(response):
    broker = AlpacaBroker(ProxyFeeds(response), session=object())
    with pytest.raises(FeedError):
        broker.quote('PSQ')


class LedgerStub:
    def __init__(self, path, trade):
        self.path, self.trade = path, trade

    def active_trade(self):
        return deepcopy(self.trade)


def open_trade(symbol):
    trade = {'id': 'stock1', 'account_ref': 'fake-account-only', 'symbol': symbol, 'direction': 'long',
             'stage': 'open', 'amount': '25.00', 'filled_qty': '1', 'ops': {}}
    for name, side, status in [('entry', 'buy', 'filled'), ('stop', 'sell', 'new')]:
        payload = {'symbol': symbol, 'side': side, 'qty': '1', 'client_order_id': f'pvt-stock1-{name}'}
        seen = {**payload, 'id': f'stock1-{name}', 'filled_qty': '1' if name == 'entry' else '0', 'status': status}
        trade['ops'][name] = {'state': 'attempted', 'payload': payload, 'last_seen': seen}
    return trade


def test_portfolio_registers_psq_as_a_socrates_owned_symbol(tmp_path):
    portfolio = Portfolio(LedgerStub(tmp_path / 'ledger.db', open_trade('PSQ')))
    assert portfolio.owned_symbols('socrates') == PROXY_SYMBOLS and portfolio.owned_symbols('other') is None
    rows = portfolio.active_trades()
    assert [(row['family_id'], row['symbol']) for row in rows] == [('socrates', 'PSQ')]
    positions = [{'symbol': 'PSQ', 'qty': '1', 'side': 'long'}]
    orders = [deepcopy(rows[0]['ops']['stop']['last_seen'])]
    portfolio.register('range_reversal', lambda: [])
    # Crypto may enter beside the protected PSQ position exactly as beside a QQQ one.
    portfolio.assert_exposure('fake-account-only', positions, orders, requesting_family='range_reversal', symbol='BTC/USD')
    # The instrument is claimed; a second Socrates slot is refused by its single-row ledger, not here.
    with pytest.raises(PortfolioBlocked):
        portfolio.assert_exposure('fake-account-only', positions, orders, requesting_family='socrates', symbol='PSQ')
    with pytest.raises(PortfolioBlocked):
        portfolio.assert_exposure('fake-account-only', [], [], requesting_family='socrates', symbol='SQQQ')
    with pytest.raises(PortfolioBlocked):  # A crypto entry cannot claim a Socrates-owned instrument either.
        portfolio.assert_exposure('fake-account-only', positions, orders, requesting_family='range_reversal', symbol='PSQ')


def test_portfolio_fails_closed_on_a_socrates_ledger_row_outside_its_owned_symbols(tmp_path):
    portfolio = Portfolio(LedgerStub(tmp_path / 'ledger.db', open_trade('SQQQ')))
    with pytest.raises(PortfolioBlocked):
        portfolio.active_trades()
    with pytest.raises(PortfolioBlocked):
        portfolio.assert_exposure('fake-account-only', [], [], requesting_family='socrates', symbol='QQQ')
    fresh = Portfolio(LedgerStub(tmp_path / 'fresh.db', None))
    for symbols in (set(), {''}, {1}, ['PSQ'], 'PSQ'):
        with pytest.raises(ValueError):
            fresh.register('range_reversal', lambda: [], symbols=symbols)
    fresh.register('range_reversal', lambda: [], symbols={'BTC/USD'})
    assert fresh.owned_symbols('range_reversal') == frozenset({'BTC/USD'})
