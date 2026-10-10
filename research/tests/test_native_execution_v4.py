from copy import deepcopy
from datetime import datetime,timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pivot.feeds import ET
from pivot.models import Bar,Zone
from research.execution import ExecutionConfig
from research.five_minute_candidates import NativeFiveMinuteSeries,VixExitAnchor
from research.native_execution_experiments_v4 import evaluate,matched_exit_one_minute,one_minute_exit_reference

PLAN=json.loads((Path(__file__).parents[1]/'native-execution-plan-v4.json').read_text())


def at(day=9,hour=9,minute=30): return datetime(2026,9,day,hour,minute,tzinfo=ET)


def bar(end,price=100,low=99,high=101): return Bar(end,1,price,high,low,price)


def fixture():
    days=['2026-09-09','2026-09-10']
    sessions={d:{'open':at(n),'close':at(n,9,45)} for d,n in zip(days,(9,10))}
    bars=[bar(at(day)+timedelta(minutes=i)) for day in (9,10) for i in range(1,16)]
    rows=[{'event_id':'fixture-'+str(day),'at':at(day,9,36).isoformat(),
        'valid_until':(at(day,9,36)+timedelta(seconds=90)).isoformat(),
        'direction':'long','entry':100,'stop':95,'target':110} for day in (9,10)]
    return SimpleNamespace(sessions=sessions),days,bars,{'F1':{'prior_day_sweep':rows}}


def test_one_minute_prices_refine_execution_without_moving_signal_or_deadline():
    base,days,bars,observations=fixture(); before=deepcopy(observations)
    result=evaluate(base,None,SimpleNamespace(identity={}),PLAN,{'complete_native_days':days},observations,bars,days)
    scenarios=result['results']['F1']['prior_day_sweep']['scenarios']
    assert observations==before
    assert scenarios['base']['observed_opening_within_deadline']==2
    for phase in ('development','validation_known_history'):
        entry=scenarios['base']['performance'][phase]['execution']['trades'][0]
        assert datetime.fromisoformat(entry['entry_at']).minute==36
        assert scenarios['higher_cost']['performance'][phase]['metrics']['net_profit']<scenarios['base']['performance'][phase]['metrics']['net_profit']
    assert result['future_holdout_performed'] is False


def test_partial_sessions_only_produce_opening_diagnostics_never_fabricated_returns():
    base,days,bars,observations=fixture()
    bars=[b for b in bars if b.end.minute!=33]
    result=evaluate(base,None,SimpleNamespace(identity={}),PLAN,{'complete_native_days':days},observations,bars,[])
    assert result['status']=='blocked_incomplete_execution_sessions' and result['phases']=={}
    for item in result['results']['F1']['prior_day_sweep']['scenarios'].values():
        assert item['observed_opening_within_deadline']==2
        assert item['performance']=={}


def test_short_opening_observation_does_not_mean_fractional_short_is_executable():
    base,days,bars,observations=fixture()
    for row in observations['F1']['prior_day_sweep']:
        row.update(direction='short',stop=110,target=95)
    result=evaluate(base,None,SimpleNamespace(identity={}),PLAN,{'complete_native_days':days},observations,bars,days)
    item=result['results']['F1']['prior_day_sweep']['scenarios']['base']
    assert item['opening_direction_counts']=={'short':2}
    assert all(p['metrics']['trade_count']==0 for p in item['performance'].values())


def test_one_minute_exit_never_backdates_publication_or_accepts_expired_touch():
    start=at(9,12,6); candle=bar(start+timedelta(minutes=1),low=89,high=111)
    decision={'status':'current','at':start.isoformat(),'valid_until':(start+timedelta(seconds=90)).isoformat()}
    assert one_minute_exit_reference(candle,90,110,decision)['reason']=='vix_pivot'
    late={**decision,'at':(start+timedelta(seconds=1)).isoformat()}
    assert one_minute_exit_reference(candle,90,110,late)['reason']=='stop'
    expired={**decision,'valid_until':start.isoformat()}
    assert one_minute_exit_reference(candle,90,110,expired)['ambiguous']
    gap=bar(start+timedelta(minutes=1),price=89,low=88,high=111)
    assert one_minute_exit_reference(gap,90,110,decision)['reason']=='gap_stop'


def test_x1_five_minute_touch_executes_at_later_native_one_minute_open(monkeypatch):
    import research.native_execution_experiments_v4 as module
    entered=at(9,12,0)
    anchor=VixExitAnchor('long',entered,95,Zone(94,95,entered-timedelta(days=1),'fixture'))
    monkeypatch.setattr(module,'freeze_vix_exit',lambda *_:anchor)
    vix=[Bar(entered,5,100,101,99,100),Bar(entered+timedelta(minutes=5),5,100,101,94,100)]
    def series(now): return NativeFiveMinuteSeries('I:VIX',tuple(b for b in vix if b.end<=now),now,
        'insightsentry','CBOE:VIX',5,True,True)
    trade={'setup_id':'fixture','entry_at':entered.isoformat(),'quantity':.05,'entry_price':100,
           'entry_fee':.01,'stop':90,'target':110,'net_pnl':.2}
    bars=[bar(entered+timedelta(minutes=i+1),price=100+i*.1,low=99,high=105) for i in range(12)]
    result=matched_exit_one_minute(None,trade,bars,series,at(9,16,0),ExecutionConfig())
    assert result['exit']['at']==at(9,12,6).isoformat()
    assert result['exit']['reason']=='vix_pivot' and result['exit']['price']==pytest.approx(100.6)
    gaps=matched_exit_one_minute(None,trade,bars[:3]+bars[4:],series,at(9,16,0),ExecutionConfig())
    assert gaps['status']=='unresolved_execution_gap'
