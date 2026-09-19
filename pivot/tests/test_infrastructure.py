from datetime import datetime,timedelta,timezone
from pathlib import Path
from decimal import Decimal
import ast
import pytest
from fastapi.testclient import TestClient
from pivot.models import Bar,Market
from pivot.feeds import ReadOnlyFeeds,FeedError,resample,daily
from pivot.sizing import purchase_plan
from pivot.store import Store
from pivot.service import Service
from pivot.api import create_app

NOW=datetime(2026,9,16,16,tzinfo=timezone.utc)
class FakeFeeds:
    def account(self):return {'equity':'100','last_equity':'99','buying_power':'100','mode':'live'}
    def positions(self):return []
    def orders(self):return []
    def clock(self):return {'is_open':True}
    def stocks(self,now):return {}
    def vix(self,now):raise FeedError('data access is not included in the current plan')

@pytest.fixture
def service(tmp_path):
    s=Service(FakeFeeds(),Store(tmp_path/'audit.db'));s.refresh_account();return s

@pytest.mark.parametrize('amount,price,qty',[(25,500,'0.050000000'),(50,125,'0.400000000'),(1000000,100,'10000.000000000')])
def test_targets_use_dollars_including_fractional_planning(amount,price,qty):
    plan=purchase_plan(amount,price,max(100,amount))
    assert plan['quantity']==qty
    assert Decimal(plan['planned_dollars'])==amount

@pytest.mark.parametrize('amount,price,power',[(0,100,100),('NaN',100,100),(100,0,100),(25,100,20),(25.001,100,100)])
def test_invalid_amounts_and_insufficient_funds_rejected(amount,price,power):
    with pytest.raises(ValueError):purchase_plan(amount,price,power)

def test_short_whole_share_constraint_is_not_silently_undersized():
    with pytest.raises(ValueError):purchase_plan(25,13,100,direction='short',fractionable=False)
    assert purchase_plan(25,12.5,100,'short',False)['quantity']=='2'

def test_resampling_requires_complete_contiguous_closed_intervals():
    start=datetime(2026,9,16,13,30,tzinfo=timezone.utc)
    bars=[Bar(start+timedelta(minutes=15*i),15,100,101,99,100,10,100) for i in range(1,27)]
    assert len(resample(bars,60))==6
    assert len(resample(bars,240))==1
    assert len(daily(bars))==1
    assert len(resample(bars[1:],60))==5
    assert not daily(bars[:-1])

def test_read_only_adapter_sanitizes_errors_and_does_not_redirect_credentials():
    class Session:
        def get(self,url,**kwargs):
            assert url.startswith('https://api.massive.com/')
            assert kwargs['allow_redirects'] is False
            return type('R',(),{'status_code':403,'text':'private secret token'})()
    f=ReadOnlyFeeds({'MASSIVE_API_KEY':'test'},Session())
    with pytest.raises(FeedError,match='not included') as e:f.vix(NOW)
    assert 'secret' not in str(e.value)
    with pytest.raises(ValueError):ReadOnlyFeeds({'APCA_API_BASE_URL':'https://attacker.example'})

def test_pagination_and_delayed_indices_are_not_labeled_live():
    class Session:
        def get(self,url,**kwargs):
            return type('R',(),{'status_code':200,'json':lambda self:{'status':'DELAYED','results':[{'t':int(NOW.timestamp()*1000)-900000,'o':20,'h':21,'l':19,'c':20}]}})()
    with pytest.raises(FeedError):
        ReadOnlyFeeds({'MASSIVE_API_KEY':'test'},Session()).vix(NOW)

def test_settings_persist_without_live_flags_or_legacy_import(service):
    p={'sizing_mode':'target','target_dollars':'25'}
    assert service.save_settings(p)['target_dollars']=='25.00'
    assert Store(service.store.path).settings()['target_dollars']=='25.00'
    assert service.snapshot()['live_enabled'] is False
    assert service.snapshot()['legacy_loaded'] is False
    with pytest.raises(ValueError):service.save_settings({**p,'live_enabled':True})
    with pytest.raises(ValueError):service.save_settings({**p,'target_dollars':'101'})
    service.state['positions']=[{'symbol':'QQQ'}]
    with pytest.raises(ValueError):service.save_settings(p)

def test_stale_account_settings_fail_and_account_errors_preserve_age(service):
    service.state['account_at']=(datetime.now(timezone.utc)-timedelta(seconds=61)).isoformat()
    with pytest.raises(ValueError):service.save_settings({'sizing_mode':'target','target_dollars':'25'})
    def fail():raise RuntimeError('secret')
    service.feeds.account=fail
    service.refresh_account()
    assert 'secret' not in service.snapshot()['account_error']
    assert service.snapshot()['account']['equity']=='100'

def test_api_has_no_legacy_or_order_routes_and_requires_same_origin(service):
    with TestClient(create_app(service,background=False)) as client:
        assert client.get('/api/health').json()['legacy_loaded'] is False
        assert client.get('/api/snapshot').json()['rulebook']['can_enter'] is False
        for path in ('/api/orders','/api/controls/resume_entries','/api/bullpen/perps/bot/start'):
            assert client.post(path).status_code in (403,404,405)
        body={'sizing_mode':'target','target_dollars':'50'}
        assert client.put('/api/settings',json=body).status_code==403
        headers={'origin':'http://testserver','x-pivot-intent':'settings'}
        assert client.put('/api/settings',json=body,headers=headers).status_code==200
        assert client.get('/api/snapshot').json()['settings']['target_dollars']=='50.00'
        assert client.put('/api/settings',json=body,headers={**headers,'origin':'https://evil.example'}).status_code==403
        assert client.get('/api/snapshot',headers={'host':'evil.example'}).status_code==400

def test_new_runtime_has_no_legacy_imports_and_only_executor_calls_broker_mutations():
    forbidden={'paper_engine','paper_api','paper_supervisor','live_config','strategy_signals','scanner_engine','bullpen_autobot','reference_policy'}
    for path in Path('pivot').glob('*.py'):
        tree=ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):assert not any(n.name.split('.')[0] in forbidden for n in node.names)
            if isinstance(node,ast.ImportFrom):assert (node.module or '').split('.')[0] not in forbidden
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
                receiver = node.func.value
                if isinstance(receiver, ast.Attribute) and receiver.attr == 'broker' and node.func.attr in ('submit', 'cancel'):
                    assert path.name in {'execution.py', 'crypto_execution.py'}

def test_legitimate_bar_pagination_can_exceed_ten_pages():
    class Feeds(ReadOnlyFeeds):
        def __init__(self):self.feed='iex';self.n=0
        def get(self,*args):
            self.n+=1
            return {'bars':{} if self.n<12 else {'QQQ':[{'t':(NOW-timedelta(minutes=15)).isoformat(),'o':100,'h':101,'l':99,'c':100,'v':10}]},'next_page_token':str(self.n) if self.n<12 else None}
    f=Feeds()
    assert len(f.stock_bars(['QQQ'],15,NOW-timedelta(days=60),NOW)['QQQ'])==1
    assert f.n==12
