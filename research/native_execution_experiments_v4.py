"""Prespecified execution-only refinement of hashed native v3 observations.

Signal logic never changes here. Genuine one-minute IEX prices refine execution
observations; incomplete sessions cannot produce full-session return claims.
"""
from collections import Counter
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import argparse
import json

from pivot.feeds import ET
from pivot.models import timestamp
from research.current_method_experiments import _dump, chronological_split, executable_observations
from research.execution import ExecutionConfig, run_execution
from research.five_minute_candidates import freeze_vix_exit, vix_exit_decision
from research.metrics import summarize
from research.native_method_experiments import load_native, native_at
from research.one_minute_execution import load_execution
from research.offline import disconnected
from research.validate_v2 import freeze_engine, load_inputs


def one_minute_exit_reference(bar, stop, target, decision=None, latency_seconds=0):
    if bar.minutes!=1 or latency_seconds<0:
        raise ValueError('Genuine one-minute execution bar required')
    start=bar.end-timedelta(minutes=1)
    if bar.open<=stop:
        return {'at':start.isoformat(),'price':bar.open,'reason':'gap_stop','ambiguous':False}
    if decision and decision['status']=='current':
        if timestamp(decision['at'])+timedelta(seconds=latency_seconds)<=start<timestamp(decision['valid_until']):
            return {'at':start.isoformat(),'price':bar.open,'reason':'vix_pivot','ambiguous':False}
    stop_hit,target_hit=bar.low<=stop,bar.high>=target
    if stop_hit or target_hit:
        return {'at':bar.end.isoformat(),'price':stop if stop_hit else target,
                'reason':'stop' if stop_hit else 'target','ambiguous':stop_hit and target_hit}
    return None


def matched_exit_one_minute(engine,trade,bars,series_at,session_close,cfg,publication_delay_seconds=60):
    entered=timestamp(trade['entry_at'])
    try: anchor=freeze_vix_exit(engine,'long',series_at(entered),entered)
    except ValueError as exc:
        return {'setup_id':trade['setup_id'],'status':'blocked_native_entry_context','reason':str(exc)}
    if anchor is None: return {'setup_id':trade['setup_id'],'status':'no_frozen_opposing_pivot'}
    expected=entered; blocked=[]
    for bar in bars:
        start=bar.end-timedelta(minutes=1)
        if start<entered or start>=session_close: continue
        if start.astimezone(ET).date()!=entered.astimezone(ET).date(): continue
        if start!=expected:
            return {'setup_id':trade['setup_id'],'status':'unresolved_execution_gap','first_missing_open':expected.isoformat()}
        expected=bar.end
        decision=None
        try: decision=vix_exit_decision(anchor,series_at(start),start,publication_delay_seconds)
        except ValueError as exc: blocked.append({'at':start.isoformat(),'reason':str(exc)})
        closing=start>=session_close-timedelta(minutes=cfg.flatten_minutes_before_close)
        ref=({'at':start.isoformat(),'price':bar.open,'reason':'scheduled_session_close','ambiguous':False}
             if closing else one_minute_exit_reference(bar,trade['stop'],trade['target'],decision,cfg.latency_seconds))
        if ref:
            price=ref['price']*(1-(cfg.spread_bps/2+cfg.slippage_bps)/10000)
            qty=trade['quantity']
            fee=cfg.fee_per_order+cfg.fee_per_share*qty+qty*price*cfg.fee_bps/10000
            net=qty*(price-trade['entry_price'])-trade['entry_fee']-fee
            return {'setup_id':trade['setup_id'],'status':'matched_exit_observed','entry_at':entered.isoformat(),
                'quantity':qty,'exit':ref,'exit_price':price,'exit_fee':fee,'net_pnl':net,
                'baseline_net_pnl':trade['net_pnl'],'net_difference':net-trade['net_pnl'],
                'frozen_boundary':anchor.boundary,'native_exit_blocks':blocked}
    return {'setup_id':trade['setup_id'],'status':'unresolved_exit','native_exit_blocks':blocked}


def verified_observations(directory,base,native,engine):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text())
    for name in ('report.json','observations.json'):
        if sha256((directory/name).read_bytes()).hexdigest()!=manifest['outputs'][name]:
            raise ValueError('Native v3 result hash mismatch')
    report=json.loads((directory/'report.json').read_text())
    if (report['plan']['version']!='2026-09-18-native-methods-v3' or report['engine']!=engine.identity
            or report['base_inputs']!=base.inputs or report['native_input']!=native.input):
        raise ValueError('Native v3 signal/input identity differs')
    return report,json.loads((directory/'observations.json').read_text()),manifest['outputs']


def evaluate(base,native,engine,plan,signal_report,observations,bars,complete_execution_days):
    signal_days=signal_report['complete_native_days']
    full_days=sorted(set(signal_days)&set(complete_execution_days))
    phases=chronological_split(full_days,plan['split']['development_fraction']) if len(full_days)>=2 else {}
    report={'schema':'pivot-native-method-experiments-v4','plan':plan,'engine':engine.identity,
        'complete_native_signal_days':signal_days,'complete_execution_days':complete_execution_days,
        'complete_comparison_days':full_days,'phases':phases,'results':{},
        'status':'known_history_evaluation' if phases else 'blocked_incomplete_execution_sessions',
        'future_holdout_performed':False,'historical_live_arrivals_verified':False,
        'promotion':'blocked_no_untouched_execution_evidence',
        'limitations':['Native one-minute IEX OHLC is not NBBO or actual broker fills.',
          'No missing bars are filled and no live receipt times are fabricated.',
          'Fixed source signal rules, deadlines, costs and publication delay are unchanged.',
          'Short signals may have an observed opening but the fractional research executor is long-only.',
          'X1 compares exits for the same baseline executed entry and quantity, without reinvesting proceeds.',
          'Full-session return metrics are unavailable when native execution observations have gaps.']}
    for variant,methods in observations.items():
        report['results'][variant]={}
        for method,allrows in methods.items():
            rows=[r for r in allrows if timestamp(r['at']).astimezone(ET).date().isoformat() in signal_days]
            detail={'qualified_observations':len(rows),'distinct_events':len({r['event_id'] for r in rows}),
                    'direction_counts':dict(Counter(r['direction'] for r in rows)),'scenarios':{}}
            report['results'][variant][method]=detail
            for scenario,costs in plan['execution_scenarios'].items():
                # These are only observed opening/deadline diagnostics. They do
                # not claim a position could be held safely through data gaps.
                available,rejected=executable_observations(rows,bars,costs['latency_seconds'])
                item={'observed_opening_within_deadline':len(available),
                      'opening_direction_counts':dict(Counter(s.direction for s in available)),
                      'distinct_events_with_observed_opening':len({s.setup_id for s in available}),
                      'deadline_rejections':rejected,'performance':{}}
                detail['scenarios'][scenario]=item
                for phase,days in phases.items():
                    scoped=[r for r in rows if timestamp(r['at']).astimezone(ET).date().isoformat() in days]
                    execution_bars=[b for b in bars if b.end.astimezone(ET).date().isoformat() in days]
                    signals,_=executable_observations(scoped,execution_bars,costs['latency_seconds'])
                    cfg=ExecutionConfig(starting_capital=plan['fixed']['starting_capital'],
                        trade_notional=plan['fixed']['purchase_target'],fractional_precision=plan['fixed']['fractional_precision'],
                        max_entry_drift=plan['fixed']['max_entry_drift'],
                        session_closes={d:base.sessions[d]['close'] for d in days},**costs)
                    result=run_execution(execution_bars,signals,cfg)
                    evaluated={'metrics':summarize(result),'execution':result}
                    if variant=='baseline':
                        def series_at(at): return native_at(native,base,at,plan['fixed']['publication_delay_seconds'])[1]
                        evaluated['X1_matched_entry_exits']=[matched_exit_one_minute(engine,t,execution_bars,series_at,
                            base.sessions[t['entry_day']]['close'],cfg,plan['fixed']['publication_delay_seconds'])
                            for t in result['trades']]
                    item['performance'][phase]=evaluated
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('native','warmup','legacy','five-minute','execution-one-minute','native-result','commit','plan','out'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    out=Path(args.out)
    if out.exists(): raise ValueError('Refusing to overwrite a previous experiment')
    with disconnected():
        paths=[Path(__file__),*[Path('research')/f for f in ('one_minute_execution.py','native_method_experiments.py',
            'five_minute_candidates.py','current_method_experiments.py','validate_v2.py','execution.py','metrics.py','data.py','offline.py')],
            Path('pivot/feeds.py'),Path('pivot/models.py')]
        hashes={str(p):sha256(p.read_bytes()).hexdigest() for p in paths}
        plan_bytes=Path(args.plan).read_bytes(); plan=json.loads(plan_bytes)
        if plan.get('version')!='2026-09-18-native-methods-v4-execution-only': raise ValueError('Explicit v4 plan required')
        base=load_inputs(args.native,args.warmup,args.legacy)
        native=load_native(args.five_minute,base); engine=freeze_engine(args.commit)
        previous,observations,previous_hashes=verified_observations(args.native_result,base,native,engine)
        bars,complete,identity=load_execution(args.execution_one_minute,base)
        report=evaluate(base,native,engine,plan,previous,observations,bars,complete)
        report.update(native_signal_output_hashes=previous_hashes,execution_input=identity,
                      base_inputs=base.inputs,native_input=native.input)
        if hashes!={str(p):sha256(p.read_bytes()).hexdigest() for p in paths} or plan_bytes!=Path(args.plan).read_bytes():
            raise RuntimeError('Research source or plan changed during evaluation')
        out.mkdir(parents=True); _dump(out/'report.json',report)
        _dump(out/'manifest.json',{'plan_sha256':sha256(plan_bytes).hexdigest(),'source_hashes':hashes,
            'report_sha256':sha256((out/'report.json').read_bytes()).hexdigest(),
            'execution_input':identity,'native_signal_output_hashes':previous_hashes})
        print(json.dumps({'status':report['status'],'complete_comparison_days':report['complete_comparison_days']}))


if __name__=='__main__': main()
