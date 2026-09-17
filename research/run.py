"""Offline experiment runner. No credentials, HTTP, broker clients, or live settings."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from math import floor
from pathlib import Path
import json
import sys

from pivot.models import timestamp
from pivot.feeds import ET, daily
from pivot.history_health import frame_gaps
from .data import load, audit, at_time, expected_ends
from .trace import decision
from .execution import TradeSignal, ExecutionConfig, run_execution
from .metrics import summarize, cash_benchmark
from .offline import disconnected


def _dump(path, data):
    Path(path).write_text(json.dumps(data,indent=2,allow_nan=False)+'\n')


def execution_config(plan, variant, clocks, scenario='base'):
    return ExecutionConfig(starting_capital=plan['fixed']['capital'],trade_notional=plan['fixed']['purchase_target'],
                           fractional_precision=plan['fixed']['fractional_precision'],
                           max_entry_drift=variant['max_entry_drift'],session_closes=clocks,
                           **plan['execution_scenarios'][scenario])


def gate_counts(record):
    """Separate individual gate truth from survival through all prior gates."""
    result=Counter({'checkpoints':1})
    surviving=not any(record['stock_missing_bars'].values()) and not record['vix']['missing_count']
    result['passed:data_coverage']+=int(surviving)
    for check in record['setup']['checks']:
        if check['passed']:result['individual_pass:'+check['name']]+=1
        surviving=surviving and check['passed']
        if surviving:result['passed:'+check['name']]+=1
    if record['first_failed_gate']:result['blocked:'+record['first_failed_gate']]+=1
    result['no_leader_zone_checkpoints']+=int(any(not row['zones'] for row in record['leaders'].values()))
    result['signal_qualified']+=int(record['signal_qualified'])
    result['long_simulation_admissible']+=int(record['simulation_admissible'])
    return result


def buy_hold(bars, cfg):
    if not bars: return {'name':'QQQ buy-and-hold','available':False}
    haircut=(cfg.spread_bps/2+cfg.slippage_bps)/10000
    entry=bars[0].open*(1+haircut)
    qty=floor(cfg.trade_notional/entry*10**cfg.fractional_precision)/10**cfg.fractional_precision
    entry_fee=cfg.fee_per_order+cfg.fee_per_share*qty+qty*entry*cfg.fee_bps/10000
    cash=cfg.starting_capital-qty*entry-entry_fee
    points=[];days={};peak=cfg.starting_capital;dd=0
    for bar in bars:
        price=bar.close*(1-haircut)
        exit_fee=cfg.fee_per_order+cfg.fee_per_share*qty+qty*price*cfg.fee_bps/10000
        equity=cash+qty*price-exit_fee
        peak=max(peak,equity);dd=max(dd,peak-equity)
        day=bar.end.astimezone(ET).date().isoformat();days[day]=equity
        points.append({'at':bar.end.isoformat(),'equity':equity})
    previous=cfg.starting_capital;daily_pnl={}
    for day,equity in days.items():daily_pnl[day]=equity-previous;previous=equity
    return {'name':'QQQ buy-and-hold','starting_capital':cfg.starting_capital,'initial_notional':qty*entry,
            'quantity':qty,'net_profit':points[-1]['equity']-cfg.starting_capital,
            'return_on_starting_capital':(points[-1]['equity']-cfg.starting_capital)/cfg.starting_capital,
            'return_on_initial_notional':(points[-1]['equity']-cfg.starting_capital)/(qty*entry),
            'max_drawdown_dollars':dd,'worst_day':min(daily_pnl.values()),'daily_pnl':daily_pnl,
            'turnover_dollars':qty*(entry+bars[-1].close*(1-haircut)),
            'market_session_exposure_fraction':1.0,'overnight_exposure':True,
            'assumptions':'One $25 fractional purchase at the period first open, final-close sale, same spread/slippage/fees. Remaining capital is cash. Dividends/tax/interest excluded; adjusted-price units as fetched.',
            'uncertainty':'One continuous investment is not a set of independent strategy trades.'}


def metrics(result, confidence=.95):
    out=summarize(result,confidence_level=confidence)
    points=result['equity']
    out['bar_end_exposure_fraction']=sum(p['quantity']>0 for p in points)/len(points) if points else 0
    out['average_bar_end_capital_deployed']=sum(p['exposure'] for p in points)/len(points) if points else 0
    out['closed_trade_exposure_minutes']=result['summary']['exposure_minutes_closed_trades']
    out['exposure_measure_limit']='Bar-end exposure misses positions opened and closed within one bar; exposure minutes assumes conservative OHLC timestamps.'
    return out


def _run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--plan',default='research/experiment-plan.json')
    args=parser.parse_args();out=Path(args.out)
    if out.exists():raise SystemExit('Output exists; refuse to overwrite experiment records')
    out.mkdir(parents=True)
    plan_bytes=Path(args.plan).read_bytes();plan=json.loads(plan_bytes)
    data,sessions,stocks,vix,dataset_hash=load(args.dataset)
    coverage=audit(sessions,stocks,vix);complete=coverage['complete_sessions']
    # Entire pre-existing VIX window must be available before the session starts.
    eligible=[]
    for day in complete:
        opened=sessions[day]['open'];oldest=(opened.astimezone(ET).date()-timedelta(days=14)).isoformat()
        scoped={d:s for d,s in sessions.items() if oldest<=d<day}
        if not frame_gaps(vix,scoped,15,opened)['missing_count']:eligible.append(day)
    if len(eligible)<2:raise SystemExit('Fewer than two complete sessions after VIX warmup; cannot split')
    cut=max(1,int(len(eligible)*.6));phases={'all':eligible,'development':eligible[:cut],'validation':eligible[cut:]}
    clocks={d:s['close'] for d,s in sessions.items()}
    report={'plan_sha256':sha256(plan_bytes).hexdigest(),'dataset_sha256':dataset_hash,'protocol_version':plan['version'],
            'python_version':sys.version.split()[0],
            'source_hashes':{str(p):sha256(p.read_bytes()).hexdigest() for p in sorted([*Path('research').glob('*.py'),
                             *[Path('pivot')/name for name in ('strategy.py','models.py','feeds.py','history_health.py','policy.py')]])},
            'coverage':coverage,'eligible_sessions':eligible,'excluded_for_vix_warmup':[d for d in complete if d not in eligible],
            'splits':phases,'final_untouched_test':'NOT RUN: future, frozen-policy data required',
            'results':{},'benchmarks':{},'gate_funnels':{},'walk_forward':[],
            'decision_evidence_limit':'Historical reconstruction uses assumed publication delay and cannot prove contemporaneous quotes, entitlement, order acceptance, or profitability.'}
    variants=plan['variants'];signals={v['id']:[] for v in variants};traces={v['id']:[] for v in variants}
    simple=[];seen_simple=set();counts={v['id']:Counter() for v in variants};examples={}
    trace_file=(out/'decisions.jsonl').open('w')
    for day in eligible:
        for end in expected_ends(sessions[day]):
            at=end+timedelta(seconds=plan['fixed']['decision_publication_delay_seconds'])
            markets,index=at_time(sessions,stocks,vix,at)
            hours=markets['QQQ'].bars[60]
            if day not in seen_simple and len(hours)>1 and hours[-1].end.astimezone(ET).date().isoformat()==day and hours[-1].close>hours[-1].open:
                simple.append(TradeSignal('hourly-momentum:'+day,at,'long',hours[-1].close,round(hours[-1].close*.99,2),round(hours[-1].close*1.02,2)))
                seen_simple.add(day)
            for variant in variants:
                record=decision(markets,index,sessions,at,variant);vid=variant['id'];traces[vid].append(record)
                trace_file.write(json.dumps(record,separators=(',',':'),allow_nan=False)+'\n')
                counts[vid].update(gate_counts(record))
                key='qualified' if record['signal_qualified'] else record['first_failed_gate'] or 'data'
                if (vid,key) not in examples:examples[vid,key]=record
                if record['signal_qualified'] and not record['vix']['missing_count'] and not any(record['stock_missing_bars'].values()):
                    s=record['setup'];signals[vid].append(TradeSignal(record['setup_id'],at,s['direction'],s['entry'],s['stop'],s['target']))
    trace_file.close()
    for vid, records in traces.items():
        counts[vid]['distinct_nasdaq_event_times']=len({r['setup']['event_at'] for r in records if r['setup'].get('event_at')})
    report['gate_funnels']={k:dict(v) for k,v in counts.items()}
    ledger=(out/'experiments.jsonl').open('w')
    confidence=1-plan['promotion']['family_alpha']/plan['promotion']['comparisons']
    for variant in variants:
        vid=variant['id'];report['results'][vid]={}
        for scenario,assumptions in plan['execution_scenarios'].items():
            cfg=execution_config(plan,variant,clocks,scenario)
            report['results'][vid][scenario]={}
            for phase,days in phases.items():
                bars=[b for b in stocks['QQQ'] if b.end.astimezone(ET).date().isoformat() in days]
                scoped=[s for s in signals[vid] if s.at.astimezone(ET).date().isoformat() in days]
                run=run_execution(bars,scoped,cfg);m=metrics(run,confidence)
                report['results'][vid][scenario][phase]=m
                experiment={'variant':variant,'scenario':scenario,'phase':phase,'days':days,'metrics':m,
                            'trade_count':len(run['trades']),'outcome':'inconclusive' if not m['uncertainty']['available'] else 'descriptive_only_not_promoted'}
                ledger.write(json.dumps(experiment,separators=(',',':'))+'\n')
                if phase=='all':_dump(out/f'{vid}-{scenario}-execution.json',run)
        # Fixed policies: the expanding training window is disclosed but never used to refit.
        cfg=execution_config(plan,variant,clocks)
        for i in range(4,len(eligible)):
            day=eligible[i]
            run=run_execution([b for b in stocks['QQQ'] if b.end.astimezone(ET).date().isoformat()==day],
                              [s for s in signals[vid] if s.at.astimezone(ET).date().isoformat()==day],cfg)
            report['walk_forward'].append({'variant':vid,'training_days':eligible[:i],'evaluation_day':day,
                                           'refitting':False,'metrics':metrics(run,confidence)})
    ledger.close()
    cfg=execution_config(plan,variants[0],clocks)
    for phase,days in phases.items():
        bars=[b for b in stocks['QQQ'] if b.end.astimezone(ET).date().isoformat() in days]
        run=run_execution(bars,[s for s in simple if s.at.astimezone(ET).date().isoformat() in days],cfg)
        report['benchmarks'][phase]={'cash':cash_benchmark(cfg.starting_capital,len(days)),
                                     'buy_hold':buy_hold(bars,cfg),'simple_hourly_momentum':metrics(run)}
    # Regime labels known before session open; never fitted to outcome.
    qdays=daily(stocks['QQQ'],sessions)
    regimes={}
    for day in eligible:
        prior_vix=[b for b in vix if b.end < sessions[day]['open']]
        prior_qqq=[b for b in qdays if b.end < sessions[day]['open']]
        level=prior_vix[-1].close if prior_vix else None
        trend='up' if len(prior_qqq)>1 and prior_qqq[-1].close>prior_qqq[-2].close else 'down_or_flat'
        regimes[day]={'vix_previous_close':level,'volatility':'below20' if level is not None and level<20 else '20plus','prior_daily_trend':trend}
    report['regimes']=regimes;report['regime_results']={}
    for vid in signals:
        cfg=execution_config(plan,next(v for v in variants if v['id']==vid),clocks)
        report['regime_results'][vid]={}
        for group in sorted({r['volatility']+'/'+r['prior_daily_trend'] for r in regimes.values()}):
            days=[d for d,r in regimes.items() if r['volatility']+'/'+r['prior_daily_trend']==group]
            run=run_execution([b for b in stocks['QQQ'] if b.end.astimezone(ET).date().isoformat() in days],
                              [s for s in signals[vid] if s.at.astimezone(ET).date().isoformat() in days],cfg)
            report['regime_results'][vid][group]=metrics(run,confidence)
    report['recurring_costs']={'current_data_subscription_dollars':0,'incremental_hosting_subscription_dollars':0,
                              'electricity_dollars':'unknown; not included','monthly_sensitivity_dollars':[0,5,20],
                              'note':'A recurring $5 cost requires $5 additional monthly net trading profit; zero-trade results cannot cover a positive cost.'}
    report['promotion']='NO STRATEGY PROMOTION: insufficient untouched evidence, missing approved loss limits, and uncommissioned paper/live execution.'
    report['accepted_examples']='See qualified trace only if present; otherwise no accepted real-data setup exists. Synthetic accepted examples are tests, not measured trades.'
    _dump(out/'examples.json',list(examples.values()));_dump(out/'results.json',report)
    _dump(out/'run-manifest.json',{'plan_sha256':report['plan_sha256'],'dataset_sha256':dataset_hash,
                                  'source_hashes':report['source_hashes'],'python_version':report['python_version'],
                                  'files':{p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}})
    print(json.dumps({'eligible_sessions':eligible,'gate_funnels':report['gate_funnels'],
                      'base_results':{v:report['results'][v]['base']['all'] for v in report['results']},'promotion':report['promotion']}))


def main():
    with disconnected():
        _run()


if __name__=='__main__':main()
