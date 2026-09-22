from datetime import datetime, timedelta, timezone
from dataclasses import replace
import pytest
from pivot.models import Bar, Market, MAG7, timestamp
from pivot.data_health import last_expected, stock_health, vix_health
from pivot.index_data import load_vix
from pivot.feeds import FeedError

NOW = datetime(2026,9,16,16,5,tzinfo=timezone.utc)


def test_expected_complete_candles_respect_calendar_early_closes_and_overnight():
    sessions={'a':{'open':NOW.replace(day=15,hour=13,minute=30),'close':NOW.replace(day=15,hour=17,minute=0)},
              'b':{'open':NOW.replace(hour=13,minute=30),'close':NOW.replace(hour=20,minute=0)}}
    assert last_expected(sessions,15,NOW)==NOW.replace(minute=0)
    assert last_expected(sessions,60,NOW)==NOW.replace(hour=15,minute=30)
    assert last_expected(sessions,1440,NOW)==NOW.replace(day=15,hour=17,minute=0)
    # The early-close session's partial 4h bucket ends at its close; today's first bucket is not due yet.
    assert last_expected(sessions,240,NOW)==NOW.replace(day=15,hour=17,minute=0)
    assert last_expected(sessions,240,NOW.replace(hour=17,minute=31,second=30))==NOW.replace(hour=17,minute=30)
    assert last_expected(sessions,240,NOW.replace(hour=20,minute=1,second=29))==NOW.replace(hour=17,minute=30)
    assert last_expected(sessions,240,NOW.replace(hour=20,minute=1,second=30))==NOW.replace(hour=20,minute=0)


def test_leader_daily_frame_is_reported_but_never_gates_readiness():
    from pivot.data_health import expire_health
    markets, sessions, now = current_native_stock_inputs()
    health = stock_health(markets, sessions, now)
    assert health['status'] == 'current'
    frames = {f['minutes']: f for f in next(i for i in health['instruments'] if i['symbol'] == 'NVDA')['frames']}
    assert frames[1440]['status'] == 'current' and frames[1440]['required'] is False
    assert frames[5]['required'] is True
    assert timestamp(frames[1440]['latest_at']).date().isoformat() == '2026-09-15'
    # Yesterday's daily candle is 'current' until the next close has passed.
    assert timestamp(frames[1440]['valid_until']) == sessions['2026-09-16']['close'] + timedelta(seconds=90)
    # Missing, stale or gapped daily context is visible but does not block entries.
    markets['NVDA'].bars[1440] = markets['NVDA'].bars[1440][:-1]
    markets['AAPL'].bars.pop(1440)
    markets['MSFT'].bars[1440] = markets['MSFT'].bars[1440][1:]
    health = stock_health(markets, sessions, now)
    assert health['status'] == 'current'
    by_symbol = {i['symbol']: i for i in health['instruments']}
    assert all(by_symbol[s]['status'] == 'current' for s in MAG7)
    assert next(f for f in by_symbol['NVDA']['frames'] if f['minutes'] == 1440)['status'] == 'stale'
    assert next(f for f in by_symbol['AAPL']['frames'] if f['minutes'] == 1440)['status'] == 'missing'
    assert next(f for f in by_symbol['MSFT']['frames'] if f['minutes'] == 1440)['status'] == 'incomplete'
    # An expired daily deadline never flips the instrument either; the instrument deadline ignores it.
    copied = {'stocks': health, 'vix': {'status': 'current', 'valid_until': (now + timedelta(hours=1)).isoformat()}}
    expire_health(copied, sessions['2026-09-16']['close'] + timedelta(seconds=89))
    assert copied['stocks']['status'] == 'needs_attention'  # Intraday frames are due by then; the daily one is not the cause.
    tesla = next(i for i in copied['stocks']['instruments'] if i['symbol'] == 'TSLA')
    assert next(f for f in tesla['frames'] if f['minutes'] == 1440)['status'] == 'current'
    frames = {f['minutes']: f for f in next(i for i in stock_health(markets, sessions, now)['instruments'] if i['symbol'] == 'TSLA')['frames']}
    assert timestamp(next(i for i in health['instruments'] if i['symbol'] == 'TSLA')['valid_until']) < timestamp(frames[1440]['valid_until'])


def test_all_eight_symbols_are_audited_and_missing_one_cannot_look_connected():
    bars=[Bar(NOW-timedelta(minutes=15*i+5),15,100,101,99,100) for i in reversed(range(4))]
    markets={symbol:Market(symbol,{15:bars,60:[replace(b,minutes=60) for b in bars],240:[replace(b,minutes=240) for b in bars],1440:[replace(b,minutes=1440) for b in bars]},'alpaca_iex',True,NOW) for symbol in ('QQQ',*MAG7)}
    for symbol in MAG7:
        markets[symbol].bars[5] = [Bar(NOW-timedelta(minutes=5*i),5,100,101,99,100) for i in reversed(range(4))]
    healthy=stock_health(markets,{},NOW)
    assert healthy['status']=='current' and healthy['coverage']=='IEX only; one exchange'
    markets.pop('NVDA')
    broken=stock_health(markets,{},NOW)
    assert len(broken['instruments'])==8 and broken['status']=='needs_attention'
    assert next(i for i in broken['instruments'] if i['symbol']=='NVDA')['frames'][0]['status']=='missing'


def test_stale_download_and_missing_vix_cannot_report_ready():
    assert stock_health({}, {}, NOW)['status']=='needs_attention'
    assert vix_health(None,NOW,error='plan denied')['status']=='blocked'
    assert vix_health(None,NOW,error='plan denied')['error']=='plan denied'


class IndexFeed:
    def __init__(self):
        self.calls=[]
        self.point={'ticker':'I:VIX','type':'indices','timeframe':'REAL-TIME','value':20,'last_updated':int(NOW.timestamp()*1e9)}
        self.bars=[{'t':int((NOW.replace(minute=0)-timedelta(minutes=15*i)).timestamp()*1000),'o':20,'h':21,'l':19,'c':20} for i in reversed(range(5))]
        self.raw={'status':'OK','ticker':'I:VIX','results':self.bars}
    def get(self,provider,path,params=None):
        self.calls.append(path)
        if 'snapshot' in path:return {'status':'OK','results':[self.point]}
        return self.raw


def test_vix_requires_explicit_realtime_entitlement_and_discards_partial_bar():
    feed=IndexFeed();result=load_vix(feed,NOW,received_at=NOW)
    assert result.realtime and len(result.bars[15])==4
    assert result.bars[15][-1].end==NOW.replace(minute=0)
    assert feed.vix_diagnostics['timeframe']=='REAL-TIME'


@pytest.mark.parametrize('field,value', [('timeframe','DELAYED'),('timeframe',None),('ticker','I:NDX'),('type','stocks'),('error','NOT_ENTITLED'),('value',float('nan')),('last_updated',int((NOW-timedelta(minutes=15)).timestamp()*1e9)),('last_updated',int((NOW+timedelta(seconds=6)).timestamp()*1e9))])
def test_vix_snapshot_http_success_is_not_enough(field,value):
    feed=IndexFeed();feed.point[field]=value
    with pytest.raises(FeedError):load_vix(feed,NOW,received_at=NOW)
    assert len(feed.calls)==1


@pytest.mark.parametrize('change',[
    lambda f:f.raw.update(status='DELAYED'),
    lambda f:f.raw.update(ticker='I:NDX'),
    lambda f:f.raw.update(results=None),
    lambda f:f.bars.append(f.bars[-1]),
    lambda f:f.raw.update(next_url='https://foreign.example/collect'),
    lambda f:f.raw.update(results=[]),
])
def test_vix_malformed_delayed_or_incomplete_history_is_rejected(change):
    feed=IndexFeed();change(feed)
    with pytest.raises(FeedError):load_vix(feed,NOW,received_at=NOW)


def test_provider_secret_is_not_forwarded_to_pagination_destination():
    feed=IndexFeed();feed.raw['next_url']='https://foreign.example/v2/aggs/ticker/I:VIX/range/15/minute/x/y?apiKey=private'
    with pytest.raises(FeedError) as error:load_vix(feed,NOW,received_at=NOW)
    assert 'private' not in str(error.value) and len(feed.calls)==2


def test_quote_health_separates_live_stale_closed_and_missing():
    from pivot.data_health import quote_health
    q={'t':NOW.isoformat(),'bp':100,'ap':100.01,'bs':1,'as':1}
    assert quote_health(q,NOW,market_open=True)['status']=='current'
    assert quote_health(q,NOW+timedelta(seconds=16),market_open=True)['status']=='stale'
    assert quote_health(q,NOW+timedelta(hours=1),market_open=False)['status']=='market_closed'
    assert quote_health(None,NOW,market_open=True)['status']=='unavailable'


@pytest.mark.parametrize('bad_price', [None, float('nan'), float('inf'), 'bad'])
def test_invalid_quote_diagnostics_remain_json_serializable(bad_price):
    import json
    from pivot.data_health import quote_health
    quote={'t':NOW.isoformat(),'bp':bad_price,'ap':100,'bs':1,'as':1}
    health=quote_health(quote,NOW,market_open=True)
    assert health['status']!='current' and health['bid'] is None
    json.dumps(health,allow_nan=False)


def test_old_stock_download_is_visible_even_when_its_candles_exist():
    bars=[Bar(NOW-timedelta(minutes=15*i+5),15,100,101,99,100) for i in reversed(range(4))]
    m=Market('QQQ',{15:bars,60:[replace(b,minutes=60) for b in bars],240:[replace(b,minutes=240) for b in bars],1440:[replace(b,minutes=1440) for b in bars]},'alpaca_iex',True,NOW-timedelta(seconds=91))
    result=stock_health({'QQQ':m},{},NOW)
    assert result['instruments'][0]['status']=='needs_attention'


def test_multi_page_vix_history_keeps_order_and_never_forwards_embedded_key():
    class Paginated(IndexFeed):
        def get(self,provider,path,params=None):
            if 'snapshot' in path:return super().get(provider,path,params)
            self.calls.append(path)
            if not params.get('cursor'):
                return {'status':'OK','ticker':'I:VIX','results':self.bars[:2],
                        'next_url':'https://api.massive.com/v2/aggs/ticker/I:VIX/range/15/minute/x/y?cursor=page2&apiKey=do-not-forward'}
            assert 'apiKey' not in params
            return {'status':'OK','ticker':'I:VIX','results':self.bars[2:]}
    assert len(load_vix(Paginated(),NOW,received_at=NOW).bars[15])==4


@pytest.mark.parametrize('symbol,source', [('VIXY','massive_indices'),('I:VIX','unverified')])
def test_proxy_or_unverified_source_cannot_satisfy_actual_vix_health(symbol,source):
    bar=Bar(NOW.replace(minute=0),15,20,21,19,20)
    market=Market(symbol,{15:[bar]},source,True,NOW)
    assert vix_health(market,NOW)['status']=='blocked'


def test_shared_quote_adapter_validates_the_requested_instrument():
    from pivot.feeds import ReadOnlyFeeds
    from pivot.broker import AlpacaBroker
    feeds=ReadOnlyFeeds({})
    payload={'symbol':'QQQ','quote':{'t':NOW.isoformat(),'bp':100,'ap':101,'bs':1,'as':1,'private':'excluded'}}
    feeds.get=lambda *args:payload
    broker=AlpacaBroker(feeds)
    assert broker.quote('QQQ')=={k:v for k,v in payload['quote'].items() if k!='private'}
    payload['symbol']='SPY'
    with pytest.raises(FeedError): broker.quote('QQQ')
    with pytest.raises(ValueError): broker.quote('SPY')


@pytest.mark.parametrize('change', [{'bp':float('nan')},{'ap':float('inf')},{'bs':0},{'as':None},{'t':'bad'}])
def test_quote_adapter_rejects_malformed_values_before_snapshot_storage(change):
    from pivot.feeds import ReadOnlyFeeds
    feeds=ReadOnlyFeeds({})
    quote={'t':NOW.isoformat(),'bp':100,'ap':101,'bs':1,'as':1,**change}
    feeds.get=lambda *args:{'symbol':'QQQ','quote':quote}
    with pytest.raises(FeedError):feeds.quote()


def test_vix_snapshot_within_clock_skew_is_accepted_and_health_stays_current():
    feed=IndexFeed()
    feed.point['last_updated']=int((NOW+timedelta(seconds=5)).timestamp()*1e9)
    market=load_vix(feed,NOW,received_at=NOW)
    assert feed.vix_diagnostics['latest_value_at']==(NOW+timedelta(seconds=5)).isoformat()
    assert vix_health(market,NOW,details=feed.vix_diagnostics)['status']=='current'
    assert len(feed.calls)==2


def test_vix_freshness_uses_provider_timestamp_not_just_download_time():
    feed=IndexFeed()
    feed.point['last_updated']=int((NOW-timedelta(seconds=89)).timestamp()*1e9)
    market=load_vix(feed,NOW,received_at=NOW)
    assert vix_health(market,NOW,details=feed.vix_diagnostics)['status']=='current'
    assert vix_health(market,NOW+timedelta(seconds=2),details=feed.vix_diagnostics)['status']=='blocked'


def test_health_expires_between_downloads():
    from pivot.data_health import expire_health
    health={'stocks':{'status':'current','instruments':[{'status':'current','observed_at':NOW.isoformat()}]},
            'vix':{'status':'current','valid_until':(NOW+timedelta(seconds=1)).isoformat()},'ready':True}
    expire_health(health,NOW+timedelta(seconds=2))
    assert not health['ready'] and health['vix']['status']=='blocked'
    assert health['stocks']['status']=='current'
    expire_health(health,NOW+timedelta(seconds=91))
    assert health['stocks']['status']=='needs_attention'


def test_missing_old_candle_invalidates_history_even_with_current_latest_bar():
    start=NOW.replace(hour=14,minute=0)
    bars=[Bar(start+timedelta(minutes=15*i),15,100,101,99,100) for i in (1,3,4)]
    sessions={'test':{'open':start,'close':start+timedelta(hours=1)}}
    market=Market('QQQ',{15:bars},'alpaca_iex',True,NOW)
    result=stock_health({'QQQ':market},sessions,NOW)
    frame=result['instruments'][0]['frames'][0]
    assert frame['status']=='incomplete' and frame['missing_count']==1
    assert result['status']=='needs_attention'


def current_native_stock_inputs():
    from pivot.tests.test_stock_feeds import StockFixture, CALENDAR, NOW as STOCK_NOW
    # Three prior full sessions supply the existing minimum four-hour history.
    calendar = [{'date': '2026-09-11', 'open': '09:30', 'close': '16:00'}, *CALENDAR]
    feed = StockFixture(calendar)
    return feed.stocks(STOCK_NOW), feed.stock_sessions, STOCK_NOW


@pytest.mark.parametrize('legacy_frame', [15, 240])
def test_unused_leader_context_cannot_block_complete_native_five_minute_inputs(legacy_frame):
    markets, sessions, now = current_native_stock_inputs()
    assert stock_health(markets, sessions, now)['status'] == 'current'
    for symbol in MAG7:
        # Even malformed optional legacy context cannot become an entry gate.
        markets[symbol].bars[legacy_frame] = markets[symbol].bars[5][:1]
    health = stock_health(markets, sessions, now)
    assert health['status'] == 'current'
    assert all([frame['minutes'] for frame in item['frames']] == [5, 1440]
               for item in health['instruments'] if item['symbol'] in MAG7)


@pytest.mark.parametrize('required_frame', [15, 60, 240, 1440])
def test_each_nasdaq_location_input_remains_required(required_frame):
    markets, sessions, now = current_native_stock_inputs()
    markets['QQQ'].bars.pop(required_frame)
    health = stock_health(markets, sessions, now)
    assert health['status'] == 'needs_attention'
    qqq = next(item for item in health['instruments'] if item['symbol'] == 'QQQ')
    assert next(frame for frame in qqq['frames'] if frame['minutes'] == required_frame)['status'] == 'missing'


def test_missing_historical_native_leader_candle_still_blocks_even_with_current_last_bar():
    markets, sessions, now = current_native_stock_inputs()
    markets['NVDA'].bars[5].pop(1)
    health = stock_health(markets, sessions, now)
    assert health['status'] == 'needs_attention'
    frame = next(item for item in health['instruments'] if item['symbol'] == 'NVDA')['frames'][0]
    assert frame['minutes'] == 5 and frame['status'] == 'incomplete' and frame['missing_count'] == 1
