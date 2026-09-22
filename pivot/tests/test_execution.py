from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import pytest
from fastapi.testclient import TestClient
from pivot.api import create_app
from pivot.broker import AlpacaBroker, BrokerRejected
from pivot.execution import Executor, CHECKS
from pivot.feeds import FeedError, ReadOnlyFeeds
from pivot.policy import POLICY_VERSION
from pivot.strategy import ANALYSIS_VERSION
from pivot.service import Service
from pivot.store import Store

NOW = datetime(2026, 9, 16, 16, tzinfo=timezone.utc)


class FakeBroker:
    """In-memory exchange. No credentials, sockets, real orders or Alpaca calls."""
    def __init__(self):
        self.at = NOW
        self.account_data = dict(account_ref='fake-account-only', mode='live', status='ACTIVE', equity='1000', last_equity='1000',
            buying_power='1000', trading_blocked=False, account_blocked=False,
            trade_suspended_by_user=False, shorting_enabled=True)
        self.position_data = []
        self.book = {}
        self.sent, self.canceled = [], []
        self.bid, self.ask = '99.99', '100'
        self.entry_mode = 'filled'
        self.reject_stop = False
        self.fill_on_cancel = False
        self.open = True
        self.close_in = 14400

    def account(self): return deepcopy(self.account_data)
    def positions(self): return deepcopy(self.position_data)
    def orders(self): return [deepcopy(o) for o in self.book.values() if o['status'] not in ('filled','canceled','expired','rejected')]
    def clock(self): return {'timestamp': self.at.isoformat(), 'is_open': self.open, 'next_close': (self.at + timedelta(seconds=self.close_in)).isoformat()}
    def asset(self, symbol): return dict(symbol=symbol, status='active', tradable=True, fractionable=True, shortable=True, easy_to_borrow=True)
    def quote(self, symbol): return dict(t=self.at.isoformat(), bp=self.bid, ap=self.ask, bs=100, **{'as': 100})
    def lookup(self, client_id): return deepcopy(self.book.get(client_id))

    def fill(self, client_id, quantity=None):
        order = self.book[client_id]
        old = Decimal(order['filled_qty'])
        full = Decimal(order['qty'])
        total = full if quantity is None else Decimal(str(quantity))
        delta = total - old
        current = Decimal(self.position_data[0]['qty']) if self.position_data else Decimal(0)
        current += delta if order['side'] == 'buy' else -delta
        self.position_data = [dict(symbol='QQQ', qty=str(current), side='long' if current > 0 else 'short')] if current else []
        order.update(filled_qty=str(total), status='filled' if total == full else 'partially_filled', filled_avg_price='100')

    def submit(self, payload):
        self.sent.append(deepcopy(payload))
        cid = payload['client_order_id']
        if (self.reject_stop and payload['type'] == 'stop') or (self.entry_mode == 'reject' and cid.endswith('-entry')):
            raise BrokerRejected('test rejection')
        if self.entry_mode == 'lost_unseen' and cid.endswith('-entry'):
            raise FeedError('uncertain response')
        qty = str(Decimal(payload['notional']) / Decimal(self.ask)) if 'notional' in payload else payload['qty']
        self.book[cid] = {**payload, 'id': cid, 'qty': qty, 'filled_qty': '0', 'status': 'new'}
        if payload['type'] == 'market':
            if cid.endswith('-entry'):
                if self.entry_mode == 'partial': self.fill(cid, '.1')
                elif self.entry_mode not in ('pending',): self.fill(cid)
            else: self.fill(cid)
        if self.entry_mode == 'lost_accepted' and cid.endswith('-entry'):
            raise FeedError('uncertain response')
        return self.lookup(cid)

    def cancel(self, oid):
        self.canceled.append(oid)
        if self.fill_on_cancel:
            self.fill(oid)
        elif self.book[oid]['status'] != 'filled':
            self.book[oid]['status'] = 'canceled'


def identify_event(setup):
    """Canonical identity for manufactured admission inputs, excluding direction."""
    zone = setup['event_zone']
    canonical = ['QQQ', setup['strategy_id'], float(zone['low']).hex(), float(zone['high']).hex(),
                 datetime.fromisoformat(zone['established_at']).astimezone(timezone.utc).isoformat(),
                 datetime.fromisoformat(setup['event_origin_at']).astimezone(timezone.utc).isoformat()]
    setup['event_id'] = 'ev2_' + sha256(json.dumps(canonical, separators=(',', ':')).encode()).hexdigest()
    return setup


def ready(at=NOW, direction='long'):
    snapshot = dict(analysis_at=at.isoformat(), data_valid_until=(at+timedelta(seconds=90)).isoformat(), feeds={'vix': 'current'}, data_errors=[],
        setup=dict(state='SETUP_READY', checks=[dict(name=n, passed=True) for n in CHECKS],
                   policy_version=ANALYSIS_VERSION, strategy_id='prior_day_sweep',
                   event_zone={'low': 100.0, 'high': 100.0, 'source': 'previous-day high',
                               'established_at': (at-timedelta(days=1)).isoformat()},
                   event_origin_at=at.isoformat(), event_at=at.isoformat(),
                   event_expires_at=(at+timedelta(minutes=180)).isoformat(), latest_evidence_at=at.isoformat(),
                   leader_evidence_valid_until=(at+timedelta(minutes=15)).isoformat(),
                   leader_observation_at=at.isoformat(), leader_observations_synchronized=True,
                   leader_observation_valid_until=(at+timedelta(seconds=390)).isoformat(),
                   direction=direction, entry=100, stop=90 if direction=='long' else 110,
                   target=110 if direction=='long' else 90))
    identify_event(snapshot['setup'])
    return snapshot


@pytest.fixture
def engine(tmp_path):
    b = FakeBroker()
    s = Store(tmp_path/'execution.db')
    e = Executor(b, s, now=lambda:b.at)
    return e, b, s


def enable(e): e.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
def disable(e): e.set_live({'enabled': False, 'policy_version': POLICY_VERSION})


def test_default_off_and_policy_review_required_without_order_submission(engine):
    e,b,s=engine
    e.tick(ready()); assert not b.sent and not e.enabled()
    with pytest.raises(ValueError): e.set_live({'enabled':True,'policy_version':'old'})
    with pytest.raises(ValueError): e.set_live({'enabled':'true','policy_version':POLICY_VERSION})
    enable(e); assert e.enabled() and not b.sent
    assert Executor(b,Store(s.path)).enabled()
    disable(e); assert not e.enabled()


def test_enabled_can_wait_for_vix_without_sending_any_orders(engine):
    e,b,s=engine; enable(e)
    snapshot=ready(); snapshot['feeds']['vix']='unavailable'
    e.tick(snapshot)
    assert e.enabled() and 'VIX' in e.message and not b.sent


@pytest.mark.parametrize('change',[
    lambda s:s.update(analysis_at=(NOW-timedelta(seconds=91)).isoformat()),
    lambda s:s.update(analysis_at=(NOW+timedelta(seconds=1)).isoformat()),
    lambda s:s.update(data_valid_until=None),
    lambda s:s.update(data_valid_until=(NOW-timedelta(seconds=1)).isoformat()),
    lambda s:s.update(data_errors=['VIX delayed']),
    lambda s:s['setup'].update(state='CONFIRMING'),
    lambda s:s['setup']['checks'].pop(),
    lambda s:s['setup']['checks'][0].update(passed=False),
    lambda s:s['setup'].update(event_at=(NOW-timedelta(hours=2)).isoformat()),
    lambda s:s['setup'].update(policy_version='legacy'),
])
def test_missing_stale_or_unvalidated_evidence_never_sends_order(engine,change):
    e,b,s=engine; enable(e); snapshot=ready(); change(snapshot);e.tick(snapshot);assert not b.sent


def test_vix_verification_expiring_during_broker_reads_blocks_entry(engine):
    e,b,s=engine; enable(e)
    snapshot=ready();snapshot['data_valid_until']=(NOW+timedelta(seconds=1)).isoformat()
    original=b.quote
    def slow_quote(symbol):
        b.at+=timedelta(seconds=2)
        return original(symbol)
    b.quote=slow_quote
    e.tick(snapshot)
    assert not b.sent and s.active_trade() is None
    assert 'expired during broker checks' in e.message


def test_prepared_entry_cannot_outlive_its_data_verification(engine):
    e,b,s=engine;enable(e)
    snapshot=ready();snapshot['data_valid_until']=(NOW+timedelta(seconds=1)).isoformat()
    original=e._manage;e._manage=lambda trade:None
    e.tick(snapshot);e._manage=original
    assert s.active_trade() and not b.sent
    b.at+=timedelta(seconds=2)
    e.tick(ready())
    assert s.active_trade() is None and not b.sent


def test_prepared_entry_budget_starts_at_quote_receipt_not_exchange_timestamp(engine):
    e,b,s=engine;enable(e)
    good=b.quote
    def old_quote(symbol):
        quote=good(symbol)
        quote['t']=(NOW-timedelta(seconds=14)).isoformat()  # Fresh enough to read; nearly spent by exchange time.
        return quote
    b.quote=old_quote
    original=e._manage;e._manage=lambda trade:None
    e.tick(ready());e._manage=original
    assert s.active_trade() and not b.sent
    b.at+=timedelta(seconds=2)
    e.tick(ready())
    assert [p['type'] for p in b.sent]==['market','stop'] and s.active_trade()['stage']=='open'


def test_prepared_entry_never_outlives_its_ten_second_intent_window(engine):
    e,b,s=engine;enable(e)
    original=e._manage;e._manage=lambda trade:None
    e.tick(ready());e._manage=original
    assert s.active_trade() and not b.sent
    b.at+=timedelta(seconds=11)
    e.tick(ready())
    assert not b.sent and s.active_trade() is None


@pytest.mark.parametrize('change',[
    lambda b:setattr(b,'open',False),
    lambda b:setattr(b,'close_in',500),
    lambda b:b.account_data.update(trading_blocked=True),
    lambda b:b.account_data.update(buying_power='20'),
    lambda b:setattr(b,'position_data',[{'symbol':'AAPL','qty':'1'}]),
    lambda b:setattr(b,'ask','120'),
    lambda b:setattr(b,'bid','0'),
    lambda b:b.account_data.update(mode='paper'),
])
def test_broker_preflight_blocks_unusable_account_market_or_price(engine,change):
    e,b,s=engine; enable(e);change(b);e.tick(ready());assert not b.sent


@pytest.mark.parametrize('amount',['25.00','50.00','1000000.00'])
def test_target_dollars_and_broker_stop_then_target_exit(engine,amount):
    e,b,s=engine;b.account_data['buying_power']='2000000';s.save({'sizing_mode':'target','target_dollars':amount});enable(e)
    e.tick(ready())
    assert b.sent[0]['notional']==amount and 'qty' not in b.sent[0]
    assert b.sent[1]['type']=='stop' and b.sent[1]['time_in_force']=='day'
    assert Decimal(b.sent[1]['qty'])*100==Decimal(amount)
    disable(e)  # Off must continue managing exits.
    b.bid,b.ask='110','110.01'
    e.tick(ready());assert len(b.sent)==2 and b.canceled  # do not race cancellation with sell
    e.tick(ready());assert len(b.sent)==3 and b.sent[-1]['side']=='sell'
    assert b.sent[-1]['qty']==b.sent[1]['qty'] and 'notional' not in b.sent[-1]
    e.tick(ready());assert s.active_trade() is None and not b.position_data
    enable(e);e.tick(ready());assert len(b.sent)==3 # same event never re-entered


def test_restart_recovers_order_without_duplicate_submission(engine):
    e,b,s=engine;enable(e);b.entry_mode='lost_accepted';e.tick(ready());assert len(b.sent)==1
    recovered=Executor(b,Store(s.path),now=lambda:b.at)
    recovered.tick(ready())
    assert len(b.sent)==2 and b.sent[-1]['type']=='stop'
    recovered.tick(ready());assert len(b.sent)==2


def test_unseen_timeout_never_blindly_resubmits_even_after_restart(engine):
    e,b,s=engine;enable(e);b.entry_mode='lost_unseen';e.tick(ready())
    recovered=Executor(b,Store(s.path),now=lambda:b.at)
    for _ in range(3):recovered.tick(ready())
    assert len(b.sent)==1 and 'uncertain' in recovered.message


def test_partial_entry_cancels_remainder_before_protecting_actual_fill(engine):
    e,b,s=engine;enable(e);b.entry_mode='partial';e.tick(ready())
    assert len(b.sent)==1 and b.canceled
    e.tick(ready());assert b.sent[-1]['type']=='stop' and b.sent[-1]['qty']=='0.1'


def test_off_cancels_unfilled_entry_without_opening_more(engine):
    e,b,s=engine;enable(e);b.entry_mode='pending';e.tick(ready());disable(e)
    e.tick(ready());e.tick(ready())
    assert len(b.sent)==1 and b.canceled and not s.active_trade()


def test_stop_fill_during_cancel_does_not_oversell_or_create_short(engine):
    e,b,s=engine;enable(e);e.tick(ready());b.fill_on_cancel=True;b.bid,b.ask='110','110.01'
    e.tick(ready());e.tick(ready())
    assert len(b.sent)==2 and not b.position_data and s.active_trade() is None


def test_partial_stop_fill_exits_only_remaining_shares(engine):
    e,b,s=engine;enable(e);e.tick(ready());b.fill(b.sent[-1]['client_order_id'],'.1');b.bid,b.ask='110','110.01'
    e.tick(ready());e.tick(ready())
    assert Decimal(b.sent[-1]['qty'])==Decimal('.15') and not b.position_data


def test_rejected_stop_closes_filled_position_and_pauses_new_entries(engine):
    e,b,s=engine;enable(e);b.reject_stop=True;e.tick(ready())
    assert [p['type'] for p in b.sent]==['market','stop','market']
    assert b.sent[-1]['side']=='sell' and not e.enabled() and not b.position_data
    e.tick(ready());assert s.active_trade() is None


def test_rejected_entry_is_audited_not_retried(engine):
    e,b,s=engine;enable(e);b.entry_mode='reject';e.tick(ready());e.tick(ready())
    assert len(b.sent)==1 and not e.enabled() and not s.active_trade()
    assert any(event['kind']=='order_rejected' for event in s.events())


def test_short_requires_whole_target_and_borrow_then_covers(engine):
    e,b,s=engine;enable(e);e.tick(ready(direction='short'));assert not b.sent
    s.save({'sizing_mode':'target','target_dollars':'100'});b.bid,b.ask='100','100.01';e.tick(ready(direction='short'))
    assert b.sent[0]['qty']=='1' and b.sent[0]['side']=='sell' and 'notional' not in b.sent[0]
    assert b.sent[1]['side']=='buy'
    b.bid,b.ask='89.99','90';e.tick(ready());e.tick(ready());e.tick(ready())
    assert b.sent[-1]['side']=='buy' and not b.position_data and s.active_trade() is None


def test_session_close_exits_even_if_quotes_are_unavailable(engine):
    e,b,s=engine;enable(e);e.tick(ready());b.close_in=250
    b.quote=lambda *_: (_ for _ in ()).throw(FeedError('no quote'))
    e.tick(ready());e.tick(ready());e.tick(ready())
    assert b.sent[-1]['side']=='sell' and s.active_trade() is None


def test_external_position_changes_are_not_traded_over(engine):
    e,b,s=engine;enable(e);e.tick(ready());b.position_data[0]['qty']='2';b.bid,b.ask='110','110.01'
    e.tick(ready());assert len(b.sent)==2 and 'share count differs' in e.message


def test_api_live_control_persists_and_requires_correct_origin_and_intent(engine):
    e,b,s=engine;service=Service(b,s,broker=b)
    body={'enabled':True,'policy_version':POLICY_VERSION}
    with TestClient(create_app(service,background=False)) as c:
        assert c.put('/api/live',json=body).status_code==403
        h={'origin':'http://testserver','x-pivot-intent':'live-control'}
        assert c.put('/api/live',json=body,headers={**h,'origin':'https://evil.example'}).status_code==403
        assert c.put('/api/live',json=body,headers={**h,'x-pivot-intent':'settings'}).status_code==403
        r=c.put('/api/live',json=body,headers=h)
        assert r.status_code==200 and r.json()['live_enabled'] is True
        assert c.get('/api/health').json()['live_enabled'] is True
        assert not b.sent # user permission itself is not an order
        assert c.put('/api/live',json={**body,'enabled':False},headers=h).json()['live_enabled'] is False


def test_broker_adapter_does_not_redirect_or_leak_secrets_and_reads_after_cancel():
    class Session:
        def __init__(self): self.code=200;self.calls=[]
        def request(self,method,url,**kwargs):
            assert kwargs['allow_redirects'] is False
            self.calls.append((method,url,kwargs))
            return type('R',(),{'status_code':self.code,'json':lambda s:{'ok':True}})()
    session=Session();b=AlpacaBroker(ReadOnlyFeeds({'APCA_API_KEY_ID':'test-only'}),session)
    b.submit({'client_order_id':'fake'})
    assert session.calls[0][1]=='https://api.alpaca.markets/v2/orders'
    session.code=404;assert b.lookup('fake') is None
    session.code=422
    with pytest.raises(BrokerRejected):b.submit({})
    b.cancel('fake') # Only a later GET confirms cancellation.
    session.code=503
    with pytest.raises(FeedError,match='uncertain'):b.submit({})


def test_account_identity_change_cannot_reuse_saved_permission_or_manage_other_account(engine):
    e,b,s=engine;enable(e);b.account_data['account_ref']='different';e.tick(ready());assert not b.sent
    b.account_data['account_ref']='fake-account-only';e.tick(ready());assert len(b.sent)==2
    b.account_data['account_ref']='different';b.bid,b.ask='110','110.01';e.tick(ready());assert len(b.sent)==2 and not b.canceled


def test_stale_or_crossed_quotes_block_entry(engine):
    e,b,s=engine;enable(e)
    b.quote=lambda _:dict(t=(NOW-timedelta(seconds=16)).isoformat(),bp=100,ap=100.01,bs=1,**{'as':1})
    e.tick(ready());assert not b.sent
    b.quote=lambda _:dict(t=NOW.isoformat(),bp=101,ap=100,bs=1,**{'as':1})
    e.tick(ready());assert not b.sent


@pytest.mark.parametrize('quote', [None, {}, {'t':'invalid'},
    {'t':NOW.isoformat(),'bp':None,'ap':100,'bs':1,'as':1},
    {'t':NOW.isoformat(),'bp':'NaN','ap':100,'bs':1,'as':1}])
def test_malformed_quote_cannot_enter_or_leave_new_fill_without_protection(engine,quote):
    e,b,s=engine;enable(e)
    good_quote=b.quote;b.quote=lambda _:quote
    e.tick(ready());assert not b.sent
    calls=0
    def bad_after_entry(symbol):
        nonlocal calls
        calls+=1
        return good_quote(symbol) if calls==1 else quote
    b.quote=bad_after_entry
    e.tick(ready())
    assert [order['type'] for order in b.sent]==['market','stop']
    assert s.active_trade()['stage']=='open'


def test_quote_arriving_after_broker_reads_is_compared_to_current_time(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    b.bid,b.ask='110','110.01'
    old=b.quote
    def delayed_quote(symbol):
        b.at += timedelta(seconds=2)
        return old(symbol)
    b.quote=delayed_quote
    e.tick(ready());assert b.canceled


def test_off_remains_available_when_account_connection_fails(engine):
    e,b,s=engine;enable(e)
    b.account=lambda: (_ for _ in ()).throw(FeedError('offline'))
    disable(e);assert not e.enabled()


def test_store_reserves_single_position_and_claims_each_post_once(engine):
    e,b,s=engine;enable(e);b.entry_mode='lost_unseen';e.tick(ready())
    trade=s.active_trade()
    assert not s.claim_operation(trade['id'],'entry')
    assert not s.reserve_trade({**trade,'id':'another-setup'})


def test_expired_stop_is_not_recreated_as_a_second_exit(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    b.book[b.sent[-1]['client_order_id']]['status']='expired'
    e.tick(ready());e.tick(ready())
    assert [p['type'] for p in b.sent]==['market','stop','market'] and not s.active_trade()


def test_new_worker_never_submits_old_prepared_entry(engine):
    e,b,s=engine;enable(e)
    # Simulate a crash after the durable reservation, before the sole POST attempt.
    original=e._manage;e._manage=lambda t:None;e.tick(ready());e._manage=original
    assert s.active_trade() and not b.sent
    b.at+=timedelta(seconds=11)
    recovered=Executor(b,Store(s.path),now=lambda:b.at);recovered.tick(ready())
    assert not s.active_trade() and not b.sent


def test_foreign_qqq_order_blocks_position_mutations(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    b.book['manual']={'symbol':'QQQ','client_order_id':'manual','status':'new'}
    b.bid,b.ask='110','110.01';e.tick(ready())
    assert len(b.sent)==2 and not b.canceled and 'outside this app' in e.message


def test_attention_reconciles_manual_resolution_without_submitting_orders(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    trade=s.active_trade();trade.update(stage='attention',reason='Check Alpaca');s.save_trade(trade)
    e.tick(ready());assert s.active_trade() and len(b.sent)==2
    b.position_data=[]
    b.book[b.sent[-1]['client_order_id']]['status']='canceled'
    e.tick(ready())
    assert s.active_trade() is None and not e.enabled() and len(b.sent)==2
    assert any(event['kind']=='manual_resolution_confirmed' for event in s.events())


def test_unknown_submission_cannot_be_cleared_by_empty_position_snapshot(engine):
    e,b,s=engine;enable(e);b.entry_mode='lost_unseen';e.tick(ready())
    trade=s.active_trade();trade.update(stage='attention',reason='Resolve uncertain entry');s.save_trade(trade)
    e.tick(ready())
    assert s.active_trade() is not None and len(b.sent)==1 and not b.canceled


def test_replaced_entry_pauses_without_competing_with_owner_orders(engine):
    e,b,s=engine;enable(e);b.entry_mode='pending';e.tick(ready())
    b.book[b.sent[0]['client_order_id']]['status']='replaced'
    e.tick(ready())
    assert not e.enabled() and s.active_trade()['stage']=='attention'
    assert len(b.sent)==1 and not b.canceled and 'replaced' in e.message
    b.orders=lambda:[]  # Broker confirms no replacement or other QQQ order remains.
    e.tick(ready())
    assert s.active_trade() is None and not e.enabled() and len(b.sent)==1


def test_replaced_exit_pauses_without_competing_with_owner_orders(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    trade=s.active_trade();trade['stage']='exiting'
    b.book[b.sent[-1]['client_order_id']]['status']='canceled'
    payload={'symbol':'QQQ','side':'sell','qty':'.25','type':'market','time_in_force':'day','client_order_id':'test-exit'}
    trade['ops']['exit0']={'state':'attempted','payload':payload};s.save_trade(trade)
    b.book['test-exit']={**payload,'id':'test-exit','status':'replaced','filled_qty':'0'}
    e.tick(ready())
    assert not e.enabled() and s.active_trade()['stage']=='attention'
    assert len(b.sent)==2 and not b.canceled and 'replaced' in e.message


def test_confirmed_order_fill_evidence_survives_store_reload(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    saved=Store(s.path).active_trade()
    assert saved['ops']['entry']['last_seen']['filled_avg_price']=='100'
    assert saved['ops']['entry']['last_seen']['filled_qty']=='0.25'
    assert saved['ops']['stop']['last_seen']['status']=='new'


def test_working_replacement_is_detected_before_foreign_order_check(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    stop_id=b.sent[-1]['client_order_id'];b.book[stop_id]['status']='replaced'
    b.book['owner-replacement']={'symbol':'QQQ','client_order_id':'owner-replacement','status':'new'}
    e.tick(ready())
    assert not e.enabled() and s.active_trade()['stage']=='attention'
    assert 'replaced' in e.message and len(b.sent)==2 and not b.canceled
    b.position_data=[];b.book['owner-replacement']['status']='canceled'
    b.orders=lambda:[]
    e.tick(ready())
    assert s.active_trade() is None and not e.enabled() and len(b.sent)==2
    assert s.trade_results()[0]['status']=='unverified'


def test_manual_flatten_after_cancel_can_resolve_without_inventing_profit(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    b.position_data=[];b.book[b.sent[-1]['client_order_id']]['status']='canceled'
    e.tick(ready());assert s.active_trade()['stage']=='attention' and not e.enabled()
    e.tick(ready());assert s.active_trade() is None and len(b.sent)==2
    assert s.trade_results()[0]['gross_pnl'] is None


def test_completed_results_use_actual_fill_prices_and_keep_fees_unverified(engine):
    e,b,s=engine;enable(e);e.tick(ready())
    b.bid,b.ask='110','110.01'
    e.tick(ready());e.tick(ready())
    b.book[b.sent[-1]['client_order_id']]['filled_avg_price']='110'
    e.tick(ready())
    assert s.active_trade() is None
    result=Store(s.path).trade_results()[0]
    assert result['status']=='verified_gross' and Decimal(result['gross_pnl'])==Decimal('2.50')
    assert result['fees_status']=='not_reported' and 'account_ref' not in result


def test_settings_cannot_change_during_live_permission_or_open_trade(engine):
    e,b,s=engine;service=Service(b,s,broker=b);service.refresh_account();enable(service.executor)
    with pytest.raises(ValueError,match='Turn Socrates Off'):
        service.save_settings({'sizing_mode':'target','target_dollars':'50'})
    disable(service.executor)
    assert service.save_settings({'sizing_mode':'target','target_dollars':'50'})['target_dollars']=='50.00'
