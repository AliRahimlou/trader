"""Native adapter and matched-entry exit fixtures, not market-return evidence."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from pivot.feeds import ET
from pivot.models import Bar, Zone
from research.execution import ExecutionConfig
from research.five_minute_candidates import NativeFiveMinuteSeries, VixExitAnchor
from research.native_method_experiments import load_native, native_at, matched_exit, verify_baseline


def at(day=18, hour=9, minute=30, second=0):
    return datetime(2026,9,day,hour,minute,second,tzinfo=ET)


def candle(end, price=100, low=99, high=101):
    return Bar(end,5,price,high,low,price)


def input_fixture():
    sessions = {at(d).date().isoformat():{'open':at(d),'close':at(d,9,45)} for d in (4,17,18)}
    bars = [candle(row['open']+timedelta(minutes=m)) for row in sessions.values() for m in (5,10,15)]
    raw = {'schema':'pivot-native-qqq-vix-five-minute-v1',
           'sessions':{d:{k:t.isoformat() for k,t in s.items()} for d,s in sessions.items()},
           'qqq5':[{**asdict(b),'end':b.end.isoformat()} for b in bars],
           'vix5':[{**asdict(b),'end':b.end.isoformat()} for b in bars],
           'qqq_provenance':{'source':'alpaca_iex','source_symbol':'QQQ','adjustment':'split','native_resolution_minutes':5},
           'vix_provenance':{'source':'insightsentry','source_symbol':'CBOE:VIX',
                            'native_resolution_minutes':5,'zero_delay_metadata_verified_at_receipt':True}}
    return raw, SimpleNamespace(sessions=sessions)


def test_native_loader_and_window_use_calendar_coverage_and_publication_cutoff(tmp_path):
    raw, base = input_fixture()
    path=tmp_path/'native.json'
    path.write_text(json.dumps(raw))
    native=load_native(path,base)
    qqq,vix=native_at(native,base,at(18,9,36))
    assert len(qqq.bars)==4 and len(vix.bars)==7
    assert qqq.bars[-1].end==at(18,9,35)
    assert qqq.receipt_is_simulated and vix.receipt_is_simulated
    # A future native bar in the file does not become available before publication.
    qqq,_=native_at(native,base,at(18,9,35,59))
    assert qqq.bars[-1].end==at(17,9,45)


@pytest.mark.parametrize('problem',['proxy','resolution','calendar','non_native_frame'])
def test_native_loader_rejects_wrong_identity_frame_and_calendar(tmp_path,problem):
    raw,base=input_fixture()
    if problem=='proxy': raw['vix_provenance']['source_symbol']='VX1!'
    if problem=='resolution': raw['vix_provenance']['native_resolution_minutes']=15
    if problem=='calendar': raw['sessions']['2026-09-18']['close']=at(18,10,0).isoformat()
    if problem=='non_native_frame': raw['vix5'][0]['minutes']=15
    path=tmp_path/'native.json'; path.write_text(json.dumps(raw))
    with pytest.raises(ValueError): load_native(path,base)


def test_native_window_rejects_fresh_tail_with_missing_warmup(tmp_path):
    raw,base=input_fixture()
    raw['vix5'].pop(0)
    path=tmp_path/'native.json'; path.write_text(json.dumps(raw))
    native=load_native(path,base)
    with pytest.raises(ValueError,match='I:VIX:native_history_missing_1'):
        native_at(native,base,at(18,9,36))


def test_baseline_hash_and_frozen_identity_must_match(tmp_path):
    report={'inputs':{'native':'fixture'},'engine':{'commit':'fixture'}}
    filename='current_5_agree_1_opposing-checkpoints.json'
    (tmp_path/'report.json').write_text(json.dumps(report)); (tmp_path/filename).write_text('[]')
    manifest={'outputs':{f:sha256((tmp_path/f).read_bytes()).hexdigest() for f in ('report.json',filename)}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    inputs=SimpleNamespace(inputs=report['inputs']); engine=SimpleNamespace(identity=report['engine'])
    assert verify_baseline(tmp_path,inputs,engine)[1]==[]
    with pytest.raises(ValueError,match='identity differs'):
        verify_baseline(tmp_path,inputs,SimpleNamespace(identity={'commit':'different'}))
    (tmp_path/filename).write_text('[{}]')
    with pytest.raises(ValueError,match='hash mismatch'): verify_baseline(tmp_path,inputs,engine)


def entry_fixture(monkeypatch):
    import research.native_method_experiments as module
    entered=at(18,12,0)
    anchor=VixExitAnchor('long',entered,95,Zone(94,95,entered-timedelta(days=1),'fixture'))
    monkeypatch.setattr(module,'freeze_vix_exit',lambda *_:anchor)
    trade={'setup_id':'fixture','entry_at':entered.isoformat(),'quantity':.05,'entry_price':100,
           'entry_fee':.01,'stop':90,'target':110,'net_pnl':.4}
    qqq=[candle(entered+timedelta(minutes=5),100),
         candle(entered+timedelta(minutes=10),101,100,102),
         candle(entered+timedelta(minutes=15),102,101,103)]
    vix=[candle(entered),candle(entered+timedelta(minutes=5),100,94,101)]
    def series(now):
        return NativeFiveMinuteSeries('I:VIX',tuple(b for b in vix if b.end<=now),now,
            'insightsentry','CBOE:VIX',5,True,True)
    return entered,trade,qqq,series


def test_matched_exit_waits_for_publication_then_next_open_and_accounts_costs(monkeypatch):
    entered,trade,bars,series=entry_fixture(monkeypatch)
    result=matched_exit(None,trade,bars,series,at(18,16,0),ExecutionConfig())
    assert result['status']=='matched_exit_observed'
    assert result['exit']['reason']=='vix_pivot'
    assert result['exit']['at']==(entered+timedelta(minutes=10)).isoformat()
    assert result['exit']['price']==102
    assert result['quantity']==trade['quantity']
    higher=matched_exit(None,trade,bars,series,at(18,16,0),ExecutionConfig(slippage_bps=10,fee_per_order=.03))
    assert higher['net_pnl']<result['net_pnl']
    assert result['net_difference']==pytest.approx(result['net_pnl']-trade['net_pnl'])


def test_missing_vix_does_not_disable_protective_exit(monkeypatch):
    entered,trade,bars,series=entry_fixture(monkeypatch)
    bars[1]=candle(entered+timedelta(minutes=10),89,88,91)
    def broken(now):
        if now>entered: raise ValueError('native gap')
        return series(now)
    result=matched_exit(None,trade,bars,broken,at(18,16,0),ExecutionConfig())
    assert result['exit']['reason']=='gap_stop' and result['exit']['price']==89
    assert result['native_exit_blocks']


def test_matched_exit_missing_closing_price_is_unresolved(monkeypatch):
    _,trade,bars,series=entry_fixture(monkeypatch)
    result=matched_exit(None,trade,bars[:1],series,at(18,16,0),ExecutionConfig())
    assert result['status']=='unresolved_exit'


def test_native_run_partitions_complete_sessions_and_keeps_expired_fills_out(tmp_path,monkeypatch):
    import research.native_method_experiments as module
    from pivot.models import MAG7
    from pathlib import Path
    raw,base=input_fixture()
    path=tmp_path/'native.json'; path.write_text(json.dumps(raw))
    native=load_native(path,base)
    base.inputs={'fixture':'only'}
    records=[{'at':at(d,9,m).isoformat(),'coverage':{'complete':True},'methods':[]}
             for d in (17,18) for m in (36,41)]
    baseline={'results':{'current_5_agree_1_opposing':{'policy':{}}}}
    monkeypatch.setattr(module,'verify_baseline',lambda *_:(baseline,records,{'fixture':'only'}))
    engine=SimpleNamespace(identity={'fixture':'only'},strategy=SimpleNamespace(AnalysisPolicy=lambda **_:None))
    monkeypatch.setattr(module,'at_time',lambda *_:({'QQQ':None,**{s:None for s in MAG7}},None))
    def f1(*args):
        now=args[5]
        return {'candidates':[{'event_id':'fixture-'+str(now.day),'method':'prior_day_sweep',
            'direction':'long','qualified':True,'blocker':None,'entry':100,'stop':90,'target':110,
            'valid_until':(now+timedelta(seconds=90)).isoformat()}]}
    monkeypatch.setattr(module,'fast_location_candidates',f1)
    plan=json.loads((Path(__file__).parents[1]/'native-method-plan.json').read_text())
    result=module.run(base,native,engine,plan,'fixture',tmp_path/'out')
    assert result['complete_native_days']==['2026-09-17','2026-09-18']
    assert result['phases']=={'development':['2026-09-17'],'validation_known_history':['2026-09-18']}
    assert result['results']['F1']['prior_day_sweep']['qualified_observations']==4
    assert result['results']['F1']['prior_day_sweep']['distinct_events']==2
    for phases in result['results']['F1']['prior_day_sweep']['scenarios'].values():
        for item in phases.values():
            assert item['eligible_observations']==0 and item['metrics']['trade_count']==0
            assert len(item['deadline_rejections'])==2
    assert result['future_holdout_performed'] is False and result['live_arrivals_verified'] is False
