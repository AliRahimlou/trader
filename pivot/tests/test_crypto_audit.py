"""Independent fault-injection audit. Fake venue only: no sockets or credentials."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pivot.crypto_execution import CryptoRangeExecutor, POLICY_VERSION as CRYPTO_POLICY
from pivot.crypto_store import CryptoStore
from pivot.feeds import FeedError
from pivot.policy import POLICY_VERSION
from pivot.portfolio import Portfolio
from pivot.store import Store

NOW = datetime(2026, 9, 19, 12, 20, 5, tzinfo=timezone.utc)


class LifecycleVenue:
    """A position/open-order venue with explicit fee and cancellation events."""
    def __init__(self):
        self.at=NOW
        self.account_ref='audit-account'
        self.qty=Decimal('1')
        self.bid=Decimal('100')
        self.book={}
        self.sent=[]
        self.canceled=[]
        self.cancel_error=False
        self.submit_error_after_accept=False
        self.quote_error=False

    def account(self):
        return dict(mode='live',account_ref=self.account_ref,status='ACTIVE',crypto_status='ACTIVE',
                    trading_blocked=False,account_blocked=False,trade_suspended_by_user=False,
                    cash='1000',buying_power='1000',non_marginable_buying_power='1000')

    def positions(self):
        if not self.qty:
            return []
        held=sum((Decimal(o['qty'])-Decimal(o['filled_qty']) for o in self.book.values()
                  if o['side']=='sell' and o['status'] not in ('filled','canceled','expired','rejected')), Decimal('0'))
        return [dict(symbol='BTCUSD',side='long',qty=str(self.qty),qty_available=str(max(Decimal('0'),self.qty-held)))]

    def orders(self):
        return [deepcopy(o) for o in self.book.values() if o['status'] not in ('filled','canceled','expired','rejected')]

    def lookup(self,cid):
        return deepcopy(self.book.get(cid))

    def quote(self,symbol):
        if self.quote_error:
            raise FeedError('offline quote')
        return dict(symbol=symbol,source='alpaca_crypto_us',t=self.at.isoformat(),
                    bp=str(self.bid),ap=str(self.bid+Decimal('.01')),bs='10',**{'as':'10'})

    def submit(self,payload):
        self.sent.append(deepcopy(payload))
        order={**deepcopy(payload),'id':payload['client_order_id'],'status':'new','filled_qty':'0','filled_avg_price':None}
        self.book[order['client_order_id']]=order
        if payload['type']=='market':
            amount=Decimal(payload['qty'])
            assert amount<=self.qty, 'No test may silently oversell the account'
            self.qty-=amount
            order.update(status='filled',filled_qty=str(amount),filled_avg_price=str(self.bid))
        if self.submit_error_after_accept:
            raise FeedError('response lost after acceptance')
        return deepcopy(order)

    def cancel(self,oid):
        self.canceled.append(oid)
        if self.cancel_error:
            raise FeedError('cancel connection unavailable')
        self.book[oid]['status']='canceled'


def seeded(tmp_path, *, stop=True, entry_status='filled', filled='1'):
    main=Store(tmp_path/'audit.db')
    main.set_control(True,POLICY_VERSION,'audit-account')
    store=CryptoStore(main.path)
    store.configure({'enabled':True,'symbols':['BTC/USD'],'target_dollars':'100.00',
                     'policy':CRYPTO_POLICY,'account_ref':'audit-account'})
    portfolio=Portfolio(main)
    portfolio.register('range_reversal',store.active_trades)
    venue=LifecycleVenue()
    venue.qty=Decimal(filled)
    executor=CryptoRangeExecutor(venue,store,main,portfolio,now=lambda:venue.at)
    identity='a'*24
    entry_payload=dict(symbol='BTC/USD',side='buy',qty='1',type='limit',time_in_force='ioc',
                       limit_price='100',client_order_id=f'cr-{identity}-entry')
    entry={**entry_payload,'id':entry_payload['client_order_id'],'status':entry_status,
           'filled_qty':filled,'filled_avg_price':'100'}
    venue.book[entry_payload['client_order_id']]=deepcopy(entry)
    trade=dict(id=identity,family_id='range_reversal',symbol='BTC/USD',direction='long',stage='open',
               account_ref='audit-account',amount='100.00',stop='90',target='120',source_entry='100',
               qty_step='.00000001',price_step='.01',minimum_qty='.00001',created_at=NOW.isoformat(),
               expires_at=(NOW+timedelta(seconds=10)).isoformat(),filled_qty=filled,
               authorization={'crypto':store.control(),'global':main.entry_authorization()},
               ops={'entry':dict(state='attempted',payload=entry_payload,last_seen=entry)})
    if stop:
        payload=dict(symbol='BTC/USD',side='sell',qty=filled,type='stop_limit',time_in_force='gtc',
                     stop_price='90',limit_price='89.10',client_order_id=f'cr-{identity}-stop')
        order={**payload,'id':payload['client_order_id'],'status':'new','filled_qty':'0','filled_avg_price':None}
        venue.book[payload['client_order_id']]=deepcopy(order)
        trade['ops']['stop']=dict(state='attempted',payload=payload,last_seen=order,prepared_at=NOW.isoformat())
        trade['stop_op']='stop'
    else:
        trade['stage']='entering'
    assert store.reserve_trade(trade)
    return executor,venue,store,main,portfolio


def test_fee_posting_after_stop_placement_cancels_oversized_protection_and_closes(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    # Fees post after an accepted protection order; broker available stays zero.
    venue.qty=Decimal('.9975')
    for _ in range(4):
        executor.tick({})
        venue.at+=timedelta(seconds=5)
    assert venue.canceled, executor.message
    assert venue.qty==0, executor.message
    assert not store.active_trades()
    assert [Decimal(p['qty']) for p in venue.sent if p['type']=='market']==[Decimal('.9975')]


def test_repeated_partial_entry_cancel_network_failures_raise_persistent_incident(tmp_path):
    executor,venue,store,*_=seeded(tmp_path,stop=False,entry_status='partially_filled',filled='.25')
    venue.cancel_error=True
    executor.tick({})
    venue.at+=timedelta(seconds=31)
    executor.tick({})
    assert store.incidents(), 'Unprotected partial fill must not wait silently forever after failed cancels'
    assert not venue.sent
    assert store.active_trades()[0]['ops']['entry']['state']=='attempted'


def test_uncertain_exit_response_is_looked_up_after_restart_not_resubmitted(tmp_path):
    executor,venue,store,main,portfolio=seeded(tmp_path)
    venue.bid=Decimal('121')
    venue.submit_error_after_accept=True
    executor.tick({})
    assert venue.qty==0 and len(venue.sent)==1
    reboot=CryptoRangeExecutor(venue,CryptoStore(store.path),Store(main.path),portfolio,now=lambda:venue.at)
    for _ in range(3):
        reboot.tick({})
    assert len(venue.sent)==1 and not store.active_trades()


def test_partial_stop_fill_and_base_fee_close_only_remaining_owned_quantity(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    # Simulate already-reconciled net buy quantity with an exact protected stop.
    trade=store.active_trades()[0]
    trade['net_entry_qty']='.9975'
    trade['ops']['stop']['payload']['qty']='.9975'
    stop=venue.book[trade['ops']['stop']['payload']['client_order_id']]
    stop.update(qty='.9975',filled_qty='.5',status='partially_filled',filled_avg_price='89.5')
    trade['ops']['stop']['last_seen']=deepcopy(stop)
    store.save_trade(trade)
    venue.qty=Decimal('.4975')
    venue.bid=Decimal('89')
    for _ in range(3):
        executor.tick({})
    assert venue.qty==0 and not store.active_trades()
    assert [Decimal(p['qty']) for p in venue.sent]==[Decimal('.4975')]


def test_unconfirmed_stop_cancel_never_sends_competing_market_exit(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    venue.bid=Decimal('121')
    venue.cancel_error=True
    for _ in range(3):
        executor.tick({})
        venue.at+=timedelta(seconds=10)
    assert venue.qty==1 and not venue.sent
    assert store.active_trades()[0]['stage']=='exiting'


def test_recovered_prepared_stop_uses_current_net_quantity_before_first_post(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    trade=store.active_trades()[0]
    stop=trade['ops']['stop']
    venue.book.pop(stop['payload']['client_order_id'])
    stop['state']='prepared'
    stop.pop('last_seen')
    store.save_trade(trade)
    venue.qty=Decimal('.9975')
    for _ in range(3):
        executor.tick({})
    assert venue.sent, executor.message
    assert all(Decimal(order['qty'])<=Decimal('.9975') for order in venue.sent), 'Recovered stop must not POST pre-fee quantity'
    assert not store.incidents(), executor.message


def test_recovered_prepared_exit_rechecks_net_holdings_before_first_post(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    trade=store.active_trades()[0]
    stop=trade['ops']['stop']
    venue.book[stop['payload']['client_order_id']]['status']='canceled'
    stop['last_seen']['status']='canceled'
    trade.update(stage='exiting',exit_pending={'reason':'target','since':NOW.isoformat()},exit_op='exit')
    payload=dict(symbol='BTC/USD',side='sell',qty='1',type='market',time_in_force='gtc',
                 client_order_id=f'cr-{trade["id"]}-exit')
    trade['ops']['exit']={'state':'prepared','payload':payload,'prepared_at':NOW.isoformat()}
    store.save_trade(trade)
    venue.qty=Decimal('.9975')
    for _ in range(3):
        executor.tick({})
    assert all(Decimal(order['qty'])<=Decimal('.9975') for order in venue.sent), 'Recovered market exit must not POST old gross quantity'
    assert venue.qty==0 and not store.active_trades(), executor.message


class SharedAccountVenue:
    """One cash balance/order book used by the actual stock and crypto engines."""
    def __init__(self):
        self.at=datetime(2026,9,21,16,10,10,tzinfo=timezone.utc)
        self.cash=Decimal('100')
        self.book={}
        self.holdings={}
        self.sent=[]
        self.canceled=[]
        self.prices={'QQQ':(Decimal('99.99'),Decimal('100')),
                     'BTC/USD':(Decimal('94.99'),Decimal('95'))}

    def account(self):
        return dict(account_ref='shared-audit-account',mode='live',status='ACTIVE',crypto_status='ACTIVE',
                    equity='100',last_equity='100',cash=str(self.cash),buying_power=str(self.cash),
                    non_marginable_buying_power=str(self.cash),trading_blocked=False,account_blocked=False,
                    trade_suspended_by_user=False,shorting_enabled=False)

    def clock(self):
        return dict(timestamp=self.at.isoformat(),is_open=True,next_close=self.at.replace(hour=20,minute=0,second=0).isoformat())

    def asset(self,symbol):
        if symbol=='QQQ':
            return dict(symbol=symbol,status='active',tradable=True,fractionable=True,shortable=True,easy_to_borrow=True)
        return dict(symbol=symbol,status='active',tradable=True,fractionable=True,shortable=False,marginable=False,
                    **{'class':'crypto'},min_order_size='.00001',min_trade_increment='.000000001',price_increment='.000000001')

    def quote(self,symbol):
        bid,ask=self.prices[symbol]
        return dict(symbol=symbol,source='alpaca_crypto_us' if symbol!='QQQ' else 'alpaca_iex',
                    t=self.at.isoformat(),bp=str(bid),ap=str(ask),bs='10',**{'as':'10'})

    def positions(self):
        rows=[]
        for symbol,qty in self.holdings.items():
            if qty:
                reserved=sum((Decimal(o['qty'])-Decimal(o['filled_qty']) for o in self.orders()
                              if o['symbol']==symbol and o['side']=='sell'),Decimal('0'))
                rows.append(dict(symbol=symbol,qty=str(qty),qty_available=str(max(Decimal('0'),qty-reserved)),side='long'))
        return rows

    def orders(self):
        return [deepcopy(o) for o in self.book.values() if o['status'] not in ('filled','canceled','expired','rejected')]

    def lookup(self,cid):
        return deepcopy(self.book.get(cid))

    def fill(self,cid,cumulative=None):
        from pivot.crypto_execution import rounded
        o=self.book[cid]
        total=Decimal(o['qty']) if cumulative is None else Decimal(cumulative)
        delta=total-Decimal(o['filled_qty'])
        bid,ask=self.prices[o['symbol']]
        price=ask if o['side']=='buy' else bid
        crypto=o['symbol']!='QQQ'
        if o['side']=='buy':
            fee=rounded(delta*Decimal('.0025'),Decimal('.000000001')) if crypto else Decimal('0')
            self.holdings[o['symbol']]=self.holdings.get(o['symbol'],Decimal('0'))+delta-fee
            self.cash-=delta*price
        else:
            assert delta<=self.holdings[o['symbol']], 'Actual engine attempted to oversell shared account'
            self.holdings[o['symbol']]-=delta
            self.cash+=delta*price*(Decimal('.9975') if crypto else Decimal('1'))
        o.update(filled_qty=str(total),filled_avg_price=str(price),status='filled' if total==Decimal(o['qty']) else 'partially_filled')

    def submit(self,payload):
        self.sent.append(deepcopy(payload))
        cid=payload['client_order_id']
        qty=payload.get('qty') or str(Decimal(payload['notional'])/self.prices[payload['symbol']][1])
        self.book[cid]={**deepcopy(payload),'id':cid,'qty':qty,'status':'new','filled_qty':'0','filled_avg_price':None}
        if payload['type']=='market' or payload['side']=='buy' and payload['time_in_force']=='ioc':
            self.fill(cid)
        return self.lookup(cid)

    def cancel(self,oid):
        self.canceled.append(oid)
        if self.book[oid]['status']!='filled':
            self.book[oid]['status']='canceled'


def shared_engines(tmp_path):
    from pivot.execution import Executor
    main=Store(tmp_path/'shared.db')
    main.save({'sizing_mode':'target','target_dollars':'5.00'})
    store=CryptoStore(main.path)
    venue=SharedAccountVenue()
    portfolio=Portfolio(main)
    portfolio.register('range_reversal',store.active_trades)
    main.set_control(True,POLICY_VERSION,'shared-audit-account')
    store.configure({'enabled':True,'target_dollars':'5.00','symbols':['BTC/USD'],
                     'policy':CRYPTO_POLICY,'account_ref':'shared-audit-account'})
    stock=Executor(venue,main,now=lambda:venue.at)
    stock.portfolio=portfolio
    crypto=CryptoRangeExecutor(venue,store,main,portfolio,now=lambda:venue.at)
    return stock,crypto,venue,main,store,portfolio


def current_crypto_signal(at):
    from pivot.models import Bar,Market
    from pivot.range_reversal import analyze
    start=at.replace(hour=4,minute=0,second=0,microsecond=0)
    count=int((at-start).total_seconds()//300)
    bars=[Bar(start+timedelta(minutes=5*(i+1)),5,100,110,90,100) for i in range(count-2)]
    for i,close in enumerate((89,95)):
        bars.append(Bar(start+timedelta(minutes=5*(count-1+i)),5,close,close+1,close-1,close))
    market=Market('BTC/USD',{5:bars},'alpaca_crypto_us',True,at)
    return analyze(market,at,provenance={'source':'alpaca_crypto_us','symbol':'BTC/USD','native':True,'timeframe_minutes':5})


@pytest.mark.parametrize('first',['stock','crypto'])
def test_actual_stock_and_crypto_engines_share_cash_and_keep_independent_protection(tmp_path,first):
    from pivot.tests.test_execution import ready
    stock,crypto,venue,main,store,portfolio=shared_engines(tmp_path)
    actions={'stock':lambda:stock.tick(ready(venue.at)),
             'crypto':lambda:crypto.tick({'BTC/USD':current_crypto_signal(venue.at)})}
    actions[first]()
    assert len(venue.sent)==2, stock.message+' / '+crypto.message
    cash_after_first=venue.cash
    actions['crypto' if first=='stock' else 'stock']()
    assert len(venue.sent)==4, stock.message+' / '+crypto.message
    assert Decimal('89.99')<=venue.cash<=Decimal('90.01')<cash_after_first
    assert main.active_trade()['stage']=='open'
    assert store.active_trade('BTC/USD')['stage']=='open'
    assert len(venue.positions())==2
    assert {o['symbol'] for o in venue.orders()}=={'QQQ','BTC/USD'}
    assert len(portfolio.reservations('shared-audit-account'))==2
    crypto_qty=venue.holdings['BTC/USD']
    # Closing the stock target must not touch the independent crypto allocation.
    venue.prices['QQQ']=(Decimal('111'),Decimal('111'))
    for _ in range(4):
        actions['stock']()
    assert main.active_trade() is None, stock.message
    assert venue.holdings['BTC/USD']==crypto_qty
    assert len(venue.sent)==5 and venue.sent[-1]['symbol']=='QQQ'
    # Crypto retains the same stop/target until its own exit, then sells only net quantity.
    venue.prices['BTC/USD']=(Decimal('110'),Decimal('110'))
    actions['crypto']()
    assert not store.active_trades(), crypto.message
    assert all(qty==0 for qty in venue.holdings.values())
    assert len(venue.sent)==6 and venue.sent[-1]['symbol']=='BTC/USD'
    assert Decimal(venue.sent[-1]['qty'])==crypto_qty
    assert venue.cash>Decimal('100')


@pytest.mark.parametrize('first',['stock','crypto'])
def test_actual_engines_reject_manual_exposure_in_shared_account(tmp_path,first):
    from pivot.tests.test_execution import ready
    stock,crypto,venue,main,store,_=shared_engines(tmp_path)
    venue.holdings['MANUAL']=Decimal('1')
    actions={'stock':lambda:stock.tick(ready(venue.at)),
             'crypto':lambda:crypto.tick({'BTC/USD':current_crypto_signal(venue.at)})}
    actions[first](); actions['crypto' if first=='stock' else 'stock']()
    assert not venue.sent and not main.active_trade() and not store.active_trades()


def test_retry_does_not_restart_cancellation_deadline_when_account_read_fails(tmp_path):
    executor,venue,store,*_=seeded(tmp_path,stop=False,entry_status='partially_filled',filled='.25')
    venue.cancel_error=True
    executor.tick({})
    venue.at+=timedelta(seconds=20)
    executor.tick({})
    assert not store.incidents()
    venue.account=lambda:(_ for _ in ()).throw(FeedError('account unavailable'))
    venue.at+=timedelta(seconds=11)
    executor.tick({})
    assert store.incidents(), 'Cancel retry must not extend the first unresolved deadline'
    assert not venue.sent


def test_master_permission_for_another_account_cannot_authorize_crypto_entry(tmp_path):
    stock,crypto,venue,main,store,_=shared_engines(tmp_path)
    main.set_control(True,POLICY_VERSION,'different-alpaca-identity')
    crypto.tick({'BTC/USD':current_crypto_signal(venue.at)})
    assert not venue.sent, 'The master and crypto permission must identify the same shared Alpaca account'
    assert not store.active_trades()


def test_fee_sized_manual_topup_cannot_expand_previously_reconciled_ownership(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    trade=store.active_trades()[0]
    trade['net_entry_qty']='.9975'
    trade['ops']['stop']['payload']['qty']='.9975'
    trade['ops']['stop']['last_seen']['qty']='.9975'
    venue.book[trade['ops']['stop']['payload']['client_order_id']]['qty']='.9975'
    store.save_trade(trade)
    # Entry is already terminal and the net amount is known. A later unrelated
    # deposit happens to equal the original fee; it must not become app-owned.
    venue.qty=Decimal('1')
    executor.tick({})
    assert not venue.sent and not venue.canceled, 'Do not sell a manual top-up as if its buy fee was refunded'
    assert store.incidents()


def test_stop_fill_between_order_and_position_reads_reconciles_without_false_manual_incident(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    positions=venue.positions
    first=[True]
    def raced_positions():
        if first[0]:
            first[0]=False
            stop=next(order for order in venue.book.values() if order['type']=='stop_limit')
            stop.update(status='filled',filled_qty=stop['qty'],filled_avg_price='90')
            venue.qty=Decimal('0')
        return positions()
    venue.positions=raced_positions
    executor.tick({})
    assert not store.active_trades(), executor.message
    assert not venue.sent and not venue.canceled
    assert not store.incidents(), 'A legitimate native stop fill must not latch a manual-position incident'


def test_large_target_exit_can_finish_more_than_five_successful_capped_children(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    venue.bid=Decimal('1000000')
    for _ in range(8):
        executor.tick({})
        if not store.active_trades():
            break
    children=[order for order in venue.sent if order['type']=='market']
    assert len(children)>5, executor.message
    assert all(Decimal(order['qty'])*venue.bid<=Decimal('190000') for order in children)
    assert sum((Decimal(order['qty']) for order in children),Decimal('0'))==1
    assert venue.qty==0 and not store.active_trades(), executor.message
    assert not store.incidents()


def test_lost_final_capped_exit_is_reconciled_when_quotes_fail_afterward(tmp_path):
    executor,venue,store,*_=seeded(tmp_path)
    venue.bid=Decimal('300000')
    executor.tick({})
    assert 0<venue.qty<1 and len(venue.sent)==1
    venue.submit_error_after_accept=True
    executor.tick({})
    assert venue.qty==0 and len(venue.sent)==2
    assert store.active_trades(), 'Lost acknowledgement must remain durable until lookup proves the fill'
    venue.quote_error=True
    executor.tick({})
    assert not store.active_trades(), executor.message
    assert len(venue.sent)==2 and not store.incidents()
