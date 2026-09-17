"""Frozen v1 strategy witnesses. Current production coverage is test_strategy_v2.py."""
from datetime import datetime,timedelta,timezone
from dataclasses import replace
import pytest
from pivot.models import Bar,Market,Zone,MAG7
from research.baseline_v1 import zones,pivot_event,fresh,closed,leader_confirmation,vix_confirmation,analyze
from pivot.rulebook import rulebook

NOW=datetime(2026,9,16,16,tzinfo=timezone.utc)
def bar(at,o=100,h=102,l=98,c=101,minutes=60):
    return Bar(at,minutes,o,h,l,c,100,100)

def history():
    # Repeated confirmed pivot near 110, and repeated trough near 90.
    ranges=[(105,95),(110,94),(105,95),(106,90),(105,95),(110,94),(105,95),(106,90),(105,95)]
    return [bar(NOW-timedelta(hours=4*(14-i)),100,h,l,100,240) for i,(h,l) in enumerate(ranges)]

def test_pivots_require_preexisting_repeated_touches_and_no_future_leak():
    bars=history(); levels=zones(bars,NOW)
    assert any(z.low<90<z.high for z in levels)
    assert any(z.low<110<z.high for z in levels)
    assert all(z.touches>=2 and z.established_at<NOW for z in levels)
    assert zones(bars+[bar(NOW+timedelta(hours=4),100,111,89,100,240)],NOW)==levels
    assert not zones(bars[:3],NOW) # one touch cannot invent a repeated level

@pytest.mark.parametrize('direction', ['long','short'])
def test_hour_break_retest_is_ordered_and_zone_must_preexist(direction):
    z=Zone(99.9,100.1,NOW-timedelta(days=1),'4h repeated pivot')
    if direction=='long':
        bars=[bar(NOW-timedelta(hours=2),99,100,98,99),bar(NOW-timedelta(hours=1),99,103,98,102),bar(NOW,101,103,100,102)]
    else:
        bars=[bar(NOW-timedelta(hours=2),101,102,100,101),bar(NOW-timedelta(hours=1),101,102,97,98),bar(NOW,99,100,97,98)]
    event=pivot_event(bars,[z]);assert event[0]==direction and event[2]=='break and retest'
    assert pivot_event(bars,[replace(z,established_at=NOW)]) is None

@pytest.mark.parametrize('realtime,observed,end',[(False,0,0),(True,-1000,0),(True,10,0),(True,0,-8000)])
def test_delayed_stale_future_observation_cannot_qualify(realtime,observed,end):
    market=Market('QQQ',{60:[bar(NOW+timedelta(seconds=end))]},'alpaca_iex',realtime,NOW+timedelta(seconds=observed))
    assert not fresh(market,NOW,60)
    assert analyze(market,{},None,NOW)['state']=='WATCHING'

def test_open_bars_and_duplicate_or_unsorted_timestamps_are_not_used():
    market=Market('QQQ',{60:[bar(NOW),bar(NOW+timedelta(hours=1))]},'alpaca_iex',True,NOW)
    assert len(closed(market,60,NOW))==1
    market.bars[60]=[bar(NOW),bar(NOW)]
    assert closed(market,60,NOW)==[]

def leader(direction):
    # Touch precomputed supply/demand, then a measured directional reaction.
    previous=bar(NOW-timedelta(minutes=15),100,106,94,100,15)
    current=bar(NOW,100,105,89.9,104,15) if direction=='long' else bar(NOW,100,110.1,95,96,15)
    return Market('AAPL',{240:history(),15:[previous,current]},'alpaca_iex',True,NOW)

def test_leaders_are_seven_companies_and_conflicting_zone_reaction_blocks():
    assert len(MAG7)==7 and 'GOOG' not in MAG7
    leaders={s:replace(leader('long'),symbol=s) for s in MAG7}
    assert leader_confirmation(leaders,'long',NOW)[0]
    leaders['MSFT']=replace(leader('short'),symbol='MSFT')
    assert not leader_confirmation(leaders,'long',NOW)[0]
    leaders.pop('NVDA')
    assert 'NVDA' in leader_confirmation(leaders,'long',NOW)[1]

def test_actual_vix_is_required_without_etf_or_delayed_substitution():
    vix=Market('VIXY',{15:[bar(NOW,minutes=15)]},'massive_indices',True,NOW)
    assert not vix_confirmation(vix,'short',NOW)[0]
    assert not vix_confirmation(replace(vix,symbol='I:VIX',realtime=False),'short',NOW)[0]
    assert not vix_confirmation(None,'short',NOW)[0]

def test_prior_day_sweep_does_not_invent_close_back_requirement():
    # No 4h levels: V2 still detects the previous-day high sweep, but waits for leaders.
    daily=bar(NOW-timedelta(days=1),100,110,90,100,1440)
    market=Market('QQQ',{1440:[daily],60:[bar(NOW-timedelta(hours=1),105,109,103,108),bar(NOW,108,112,107,111)]},'alpaca_iex',True,NOW)
    market.previous_session='2026-09-15'
    result=analyze(market,{},None,NOW)
    assert result['state']=='CONFIRMING' and result['event']=='previous-day level sweep'
    assert not result['can_enter']

def test_rulebook_honors_latest_primary_sources_and_does_not_blend_first_clip():
    book=rulebook()
    assert book['architecture_status']=='two_independent_nasdaq_methods'
    assert all(r['group']=='nasdaq_sequence' for r in book['rules'])
    assert not any(r['id'].startswith('v1_') for r in book['rules'])
    assert not book['can_enter']

def test_primary_sequence_checks_leaders_then_vix_without_granting_broker_permission():
    market=Market('QQQ',{240:history(),60:[
        bar(NOW-timedelta(hours=2),108,109,107,108),
        bar(NOW-timedelta(hours=1),108,112,107,111),
        bar(NOW,110.5,112,110,111)]},'alpaca_iex',True,NOW)
    leaders={s:replace(leader('short'),symbol=s) for s in MAG7}
    # Direct VIX, at its own pre-existing demand, moving opposite Nasdaq direction.
    vb=[replace(b,minutes=15) for b in history()]
    vb += leader('long').bars[15]
    vix=Market('I:VIX',{15:vb},'massive_indices',True,NOW)
    result=analyze(market,leaders,vix,NOW)
    assert result['state']=='SETUP_READY'
    assert result['direction']=='short' # break up is not automatically a long
    assert result['target']<result['entry']<result['stop']
    assert result['can_enter'] is False
    missing=analyze(market,leaders,None,NOW)
    assert missing['state']=='CONFIRMING'
    assert any(not c['passed'] and c['name']=='Actual VIX zone reaction' for c in missing['checks'])
    assert not any('VWAP' in c['name'] or 'tape' in c['name'] for c in result['checks'])

def test_older_daily_candle_cannot_masquerade_as_previous_session():
    from research.baseline_v1 import prior_day_zones
    market=Market('QQQ',{1440:[bar(NOW-timedelta(days=2),minutes=1440)]},'alpaca_iex',True,NOW,previous_session='2026-09-15')
    assert prior_day_zones(market,NOW)==[]

def test_retest_can_arrive_after_multiple_hourly_candles():
    z=Zone(99.9,100.1,NOW-timedelta(days=2),'4h repeated pivot')
    bars=[bar(NOW-timedelta(hours=4),99,100,98,99),bar(NOW-timedelta(hours=3),99,103,98,102),
          bar(NOW-timedelta(hours=2),102,105,102,104),bar(NOW-timedelta(hours=1),104,105,102,103),
          bar(NOW,101,103,100,102)]
    assert pivot_event(bars,[z])[2]=='break and retest'
