"""Live trade view (4.5.2): the owner can watch the open Socrates trade.

Read-only presentation over records the app already keeps. FakeBroker only; no
network, and no test here may send, cancel or change an order to build the view.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from pivot.api import create_app
from pivot.execution import Executor, TRACK_SECONDS
from pivot.live_trade import build_last_trade, build_live_trade
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready

CLOCK = {'timestamp': NOW.isoformat(), 'is_open': True, 'next_close': (NOW + timedelta(hours=1)).isoformat()}


def open_trade(**extra):
    trade = {'id': 't1', 'stage': 'open', 'symbol': 'QQQ', 'direction': 'long', 'signal_symbol': 'QQQ',
             'signal_direction': 'long', 'amount': '15.00', 'stop': '99.00', 'target': '102.00', 'filled_qty': '0.15',
             'created_at': (NOW - timedelta(minutes=30)).isoformat(),
             'ops': {'entry': {'state': 'attempted', 'payload': {'client_order_id': 'c-entry'},
                               'last_seen': {'status': 'filled', 'filled_qty': '0.15', 'filled_avg_price': '100',
                                             'filled_at': (NOW - timedelta(minutes=29)).isoformat()}},
                     'stop': {'state': 'attempted', 'payload': {'client_order_id': 'c-stop'},
                              'last_seen': {'status': 'new', 'filled_qty': '0'}}}}
    trade.update(extra)
    return trade


def mark(bid, ask=None, at=NOW, trade_id='t1', symbol='QQQ'):
    return {'trade_id': trade_id, 'symbol': symbol, 'bid': bid, 'ask': ask or bid, 'quote_at': at.isoformat(), 'at': at.isoformat()}


def test_open_long_shows_profit_distances_and_every_way_it_sells():
    view = build_live_trade(open_trade(), mark=mark('101'), clock=CLOCK, now=NOW,
                            orders=[{'client_order_id': 'c-stop', 'status': 'new'}], message='Managing QQQ')
    assert view['stage_text'] == 'Holding' and view['label'] == 'Socrates long QQQ' and not view['proxy']
    assert view['entry'] == {'price': '100', 'qty': '0.15', 'cost': '15.00',
                             'at': (NOW - timedelta(minutes=29)).isoformat(), 'source': 'fill'}
    assert view['current']['price'] == '101' and view['current']['fresh'] and view['current']['source'] == 'quote'
    # Up $1 a share on 0.15 shares, 1% of the entry, and 1R because the stop is $1 below the entry.
    assert view['pnl'] == {'dollars': '0.15', 'per_share': '1.0000', 'percent': '1.00', 'r': '1.00'}
    assert view['stop']['distance'] == '2.0000' and view['stop']['result_if_hit'] == '-0.15'
    assert view['target']['distance'] == '1.0000' and view['target']['result_if_hit'] == '0.30'
    assert view['stop']['order'] == {'status': 'new', 'text': 'working at Alpaca', 'working': True}
    assert view['progress'] == '0.667'
    assert view['session_exit']['seconds_left'] == 3300 and view['session_exit']['at'] == (NOW + timedelta(minutes=55)).isoformat()
    assert view['message'] == 'Managing QQQ' and view['exit'] is None


def test_loss_and_stale_quote_fall_back_to_the_position_price():
    trade = open_trade()
    position = [{'symbol': 'QQQ', 'qty': '0.15', 'avg_entry_price': '100', 'current_price': '99.5'}]
    view = build_live_trade(trade, mark=mark('101', at=NOW - timedelta(minutes=5)), positions=position, clock=CLOCK, now=NOW)
    assert view['current']['source'] == 'position' and view['current']['price'] == '99.5' and not view['current']['fresh']
    assert view['pnl']['dollars'] == '-0.08' and view['pnl']['r'] == '-0.50' and view['pnl']['percent'] == '-0.50'
    # A mark from another trade is never shown for this one.
    view = build_live_trade(trade, mark=mark('150', trade_id='old'), positions=position, clock=CLOCK, now=NOW)
    assert view['current']['price'] == '99.5'


def test_psq_proxy_shows_the_qqq_signal_next_to_the_psq_prices():
    trade = open_trade(symbol='PSQ', signal_direction='short', proxy='inverse_etf', stop='34.65', target='35.70',
                       signal_geometry={'symbol': 'QQQ', 'direction': 'short', 'entry': '600', 'stop': '606', 'target': '588'})
    view = build_live_trade(trade, mark=mark('35.10', '35.11', symbol='PSQ'), clock=CLOCK, now=NOW,
                            signal_quote={'bp': 598.5, 't': NOW.isoformat()})
    assert view['proxy'] and view['label'] == 'Socrates short via PSQ (inverse QQQ)' and view['signal_direction'] == 'short'
    assert view['signal'] == {'symbol': 'QQQ', 'direction': 'short', 'entry': '600', 'stop': '606', 'target': '588',
                              'current': '598.5', 'at': NOW.isoformat()}
    assert view['current']['price'] == '35.10'


def test_selling_and_attention_stages_explain_why():
    trade = open_trade(stage='exiting', exit_reason='Target reached; closing the held shares',
                       exit_pending={'first_observed_at': NOW.isoformat()})
    view = build_live_trade(trade, clock=CLOCK, now=NOW)
    assert view['stage_text'] == 'Selling' and view['exit']['reason'].startswith('Target reached')
    assert view['current'] is None and view['pnl'] is None
    assert build_live_trade(open_trade(stage='finished'), now=NOW) is None and build_live_trade(None) is None
    assert build_live_trade(open_trade(stage='attention', reason='Check Alpaca'), now=NOW)['exit']['reason'] == 'Check Alpaca'


def test_executor_keeps_the_management_quote_and_a_thinned_track(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'live.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready())
    trade = store.active_trade()
    assert trade['stage'] == 'open'
    executor.tick(ready())  # places the stop
    for seconds in (5, 10, 15, 20, 30):
        broker.at = NOW + timedelta(seconds=seconds)
        executor.tick(ready(broker.at))
    latest, track = executor.live_marks()
    assert latest['trade_id'] == trade['id'] and latest['bid'] == '99.99' and latest['symbol'] == 'QQQ'
    spacing = [(datetime.fromisoformat(b['at']) - datetime.fromisoformat(a['at'])).total_seconds() for a, b in zip(track, track[1:])]
    assert spacing and all(gap >= TRACK_SECONDS for gap in spacing)
    view = build_live_trade(store.active_trade(), mark=latest, track=track, clock=broker.clock(), now=broker.at)
    assert view['track'] and view['track'][-1]['p'] == '99.99'
    assert [order['type'] for order in broker.sent] == ['market', 'stop']


def test_exit_reason_names_the_stop_or_the_target(tmp_path):
    for bid, expected in (('89', 'Stop price reached'), ('111', 'Target reached')):
        broker = FakeBroker()
        store = Store(tmp_path / f'exit-{bid}.db')
        executor = Executor(broker, store, now=lambda: broker.at)
        enable(executor)
        executor.tick(ready())
        executor.tick(ready())
        broker.bid, broker.ask = bid, str(float(bid) + .01)
        executor.tick(ready())
        started = next(row for row in store.events() if row['kind'] == 'exit_started')
        assert started['detail']['reason'] == expected + '; closing the held shares'


def test_last_trade_recap_uses_fill_prices_and_the_verified_result():
    trade = open_trade(stage='finished', completed_at=NOW.isoformat(), exit_reason='Target reached; closing the held shares')
    trade['ops']['stop']['last_seen'] = {'status': 'canceled', 'filled_qty': '0', 'symbol': 'QQQ', 'side': 'sell',
                                         'client_order_id': 'c-stop'}
    trade['ops']['stop']['payload'].update(symbol='QQQ', side='sell')
    trade['ops']['entry']['payload'].update(symbol='QQQ', side='buy')
    trade['ops']['entry']['last_seen'].update(symbol='QQQ', side='buy', client_order_id='c-entry')
    trade['ops']['exit1'] = {'state': 'attempted', 'payload': {'client_order_id': 'c-exit', 'symbol': 'QQQ', 'side': 'sell'},
                             'last_seen': {'status': 'filled', 'filled_qty': '0.15', 'filled_avg_price': '102',
                                           'symbol': 'QQQ', 'side': 'sell', 'client_order_id': 'c-exit'}}
    recap = build_last_trade(trade)
    assert recap['entry_price'] == '100' and recap['exit_price'] == '102.0000' and recap['percent'] == '2.00'
    assert recap['gross_pnl'] == '0.30' and recap['status'] == 'verified_gross'
    assert recap['exit_reason'].startswith('Target reached')
    trade['ops']['exit1']['last_seen']['filled_qty'] = '0'
    trade['ops']['stop']['last_seen'].update(status='filled', filled_qty='0.15', filled_avg_price='99')
    trade.pop('exit_reason')
    recap = build_last_trade(trade)
    assert recap['exit_reason'] == 'The broker stop order sold the shares' and recap['percent'] == '-1.00'
    assert build_last_trade(open_trade()) is None


def test_service_and_api_serve_the_view_without_any_order_call(tmp_path):
    broker = FakeBroker()
    broker.at = datetime.now(timezone.utc)
    runtime = Service(broker, Store(tmp_path / 'service.db'), broker=broker)
    runtime.refresh_account()
    assert runtime.snapshot()['live_trade'] is None and runtime.snapshot()['last_trade'] is None

    async def exercise():
        transport = httpx.ASGITransport(app=create_app(runtime, background=False))
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
            return await client.get('/api/live-trade')

    response = asyncio.run(exercise())
    assert response.status_code == 200
    body = response.json()
    assert body['live_trade'] is None and body['last_trade'] is None and body['live_trade_error'] is None and body['server_at']
    assert broker.sent == [] and broker.canceled == []


def test_a_failing_mark_never_blocks_the_target_exit(tmp_path, monkeypatch):
    broker = FakeBroker()
    store = Store(tmp_path / 'fail.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready())
    executor.tick(ready())
    monkeypatch.setattr(executor, '_mark_lock', None)  # any use of the lock now raises
    broker.bid, broker.ask = '111', '111.01'
    executor.tick(ready())
    assert any(row['kind'] == 'exit_started' for row in store.events()) and broker.canceled
    executor.tick(ready())  # the stop is canceled first, then the held shares are sold at market
    assert [order['type'] for order in broker.sent] == ['market', 'stop', 'market']
