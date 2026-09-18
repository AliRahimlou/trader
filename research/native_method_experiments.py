"""Disconnected native F1 gate and matched-entry X1 exit research.

No broker, account, settings or order interface. Missing native observations and
missing executable prices are reported, never filled by fabricated candles.
"""
from collections import Counter
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import argparse
import json

from pivot.feeds import ET
from pivot.models import MAG7, timestamp
from research.current_method_experiments import (_dump, chronological_split,
    executable_observations, signal_expiry, stock_deadline)
from research.execution import ExecutionConfig, run_execution
from research.five_minute_candidates import (NativeFiveMinuteSeries,
    fast_location_candidates, freeze_vix_exit, vix_exit_decision, research_exit_reference)
from research.metrics import summarize
from research.offline import disconnected
from research.validate_v2 import _json, _calendar, _bars, at_time, ends, freeze_engine, load_inputs


def load_native(path, base):
    raw, digest = _json(path)
    if raw.get('schema') != 'pivot-native-qqq-vix-five-minute-v1':
        raise ValueError('Unknown native QQQ/VIX research dataset')
    sessions = _calendar(raw['sessions'])
    for day, session in sessions.items():
        if day not in base.sessions or base.sessions[day] != session:
            raise ValueError('Native calendar must match frozen input calendar')
    qp, vp = raw['qqq_provenance'], raw['vix_provenance']
    if (qp.get('source') != 'alpaca_iex' or qp.get('source_symbol') != 'QQQ'
            or qp.get('adjustment') != 'split' or qp.get('native_resolution_minutes') != 5):
        raise ValueError('Native split-adjusted IEX QQQ required')
    if (vp.get('source') != 'insightsentry' or vp.get('source_symbol') != 'CBOE:VIX'
            or vp.get('native_resolution_minutes') != 5
            or vp.get('zero_delay_metadata_verified_at_receipt') is not True):
        raise ValueError('Verified native actual VIX required')
    return SimpleNamespace(qqq=_bars(raw['qqq5'], 5, sessions),
        vix=_bars(raw['vix5'], 5, sessions), sessions=sessions,
        provenance={'qqq': qp, 'vix': vp}, input={'name': Path(path).name, 'sha256': digest})


def native_at(native, base, at, publication_delay_seconds=60):
    """Fixed warmup contract: QQQ prior+current session, VIX 14 calendar days.

    The current timestamp is a modeled receipt, not a historical arrival claim.
    A missing earlier candle cannot be excused merely by fresh latest candles.
    """
    at = timestamp(at)
    cutoff = at - timedelta(seconds=publication_delay_seconds)
    day = at.astimezone(ET).date().isoformat()
    previous = max((d for d in base.sessions if d < day), default=None)
    if not previous:
        raise ValueError('Previous session unavailable for native context')
    vstart = (at.astimezone(ET).date()-timedelta(days=14)).isoformat()
    qcalendar = {d:s for d,s in base.sessions.items() if previous <= d <= day}
    vcalendar = {d:s for d,s in base.sessions.items() if vstart <= d <= day}
    output = []
    for symbol, bars, calendar, provider, source_symbol in [
        ('QQQ', native.qqq, qcalendar, 'alpaca_iex', 'QQQ'),
        ('I:VIX', native.vix, vcalendar, 'insightsentry', 'CBOE:VIX')]:
        if not calendar or any(d not in native.sessions for d in calendar):
            raise ValueError(symbol + ':native_calendar_warmup_incomplete')
        expected = {end for session in calendar.values() for end in ends(session, 5) if end <= cutoff}
        first = min(s['open'] for s in calendar.values())
        actual = [b for b in bars if first < b.end <= cutoff]
        missing = expected - {b.end for b in actual}
        if missing:
            raise ValueError(symbol + ':native_history_missing_' + str(len(missing)))
        output.append(NativeFiveMinuteSeries(symbol, tuple(actual), at, provider,
            source_symbol, 5, True, True, receipt_is_simulated=True))
    return tuple(output)


def verify_baseline(directory, inputs, engine):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text())
    report = json.loads((directory/'report.json').read_text())
    filename = 'current_5_agree_1_opposing-checkpoints.json'
    for name in ('report.json', filename):
        if sha256((directory/name).read_bytes()).hexdigest() != manifest['outputs'][name]:
            raise ValueError('Baseline result hash mismatch')
    if report['inputs'] != inputs.inputs or report['engine'] != engine.identity:
        raise ValueError('Baseline engine/input identity differs')
    return report, json.loads((directory/filename).read_text()), {
        'report_sha256': manifest['outputs']['report.json'],
        'checkpoints_sha256': manifest['outputs'][filename]}


def matched_exit(engine, trade, qqq, series_at, session_close, cfg, publication_delay_seconds=60):
    """Apply X1 to an existing executed long entry without reinvesting proceeds.

    This compares one exit path at a fixed quantity and entry. It is not a new
    portfolio equity curve and never imports hypothetical entries as fills.
    """
    entered = timestamp(trade['entry_at'])
    try:
        anchor = freeze_vix_exit(engine, 'long', series_at(entered), entered)
    except ValueError as exc:
        return {'setup_id': trade['setup_id'], 'status': 'blocked_native_entry_context', 'reason': str(exc)}
    if anchor is None:
        return {'setup_id': trade['setup_id'], 'status': 'no_frozen_opposing_pivot'}
    blocked = []
    for bar in qqq:
        start = bar.end-timedelta(minutes=5)
        if start < entered or start >= session_close:
            continue
        if start.astimezone(ET).date() != entered.astimezone(ET).date():
            continue
        closing = start >= session_close-timedelta(minutes=cfg.flatten_minutes_before_close)
        decision = None
        try:
            decision = vix_exit_decision(anchor, series_at(start), start, publication_delay_seconds)
        except ValueError as exc:
            blocked.append({'at': start.isoformat(), 'reason': str(exc)})
        # Native VIX failure suppresses its discretionary exit, never protection.
        reference = ({'at': start.isoformat(), 'price': bar.open,
                      'reason': 'scheduled_session_close', 'ambiguous': False}
                     if closing else research_exit_reference(bar, 'long', trade['stop'], trade['target'],
                                                              decision, cfg.latency_seconds))
        if reference:
            haircut = (cfg.spread_bps/2+cfg.slippage_bps)/10000
            price = reference['price']*(1-haircut)
            qty = trade['quantity']
            fee = cfg.fee_per_order + cfg.fee_per_share*qty + qty*price*cfg.fee_bps/10000
            net = qty*(price-trade['entry_price'])-trade['entry_fee']-fee
            return {'setup_id': trade['setup_id'], 'status': 'matched_exit_observed',
                    'entry_at': entered.isoformat(), 'quantity': qty,
                    'exit': reference, 'exit_price': price, 'exit_fee': fee,
                    'net_pnl': net, 'baseline_net_pnl': trade['net_pnl'],
                    'net_difference': net-trade['net_pnl'], 'frozen_boundary': anchor.boundary,
                    'native_exit_blocks': blocked}
    return {'setup_id': trade['setup_id'], 'status': 'unresolved_exit', 'native_exit_blocks': blocked}


def run(base, native, engine, plan, baseline_dir, out):
    out = Path(out)
    if out.exists():
        raise ValueError('Refusing to overwrite an earlier experiment')
    out.mkdir(parents=True)
    baseline, old_records, baseline_identity = verify_baseline(baseline_dir, base, engine)
    policy = engine.strategy.AnalysisPolicy(**baseline['results']['current_5_agree_1_opposing']['policy'])
    observations = {variant:{method:[] for method in plan['methods']} for variant in ('baseline', 'F1')}
    counters, records = Counter(), []
    native_days = set()
    for old in old_records:
        at = timestamp(old['at'])
        day = at.astimezone(ET).date().isoformat()
        record = {'at': old['at'], 'base_context_complete': old['coverage']['complete']}
        records.append(record)
        if not old['coverage']['complete']:
            counters['incomplete_base_context'] += 1
            continue
        try:
            qqq5, vix5 = native_at(native, base, at, plan['fixed']['publication_delay_seconds'])
        except ValueError as exc:
            record.update(status='blocked_native_context', reason=str(exc))
            counters[str(exc).split('_missing_')[0]] += 1
            continue
        native_days.add(day)
        markets, vix15, *_ = at_time(base, at, engine)
        f1 = fast_location_candidates(engine, markets['QQQ'], {s:markets[s] for s in MAG7},
                qqq5, vix5, at, policy, plan['fixed']['publication_delay_seconds'])
        record.update(status='native_candle_evaluation', F1=f1)
        counters['native_context_checkpoints'] += 1
        if not f1['candidates']:
            counters['F1:no_location_event'] += 1
        for candidate in f1['candidates']:
            counters['F1:' + candidate['method'] + ':' + (candidate['blocker'] or 'qualified')] += 1
            if candidate['qualified']:
                observations['F1'][candidate['method']].append({
                    k:candidate[k] for k in ('event_id','direction','entry','stop','target','valid_until')}
                    | {'at': at.isoformat()})
        # Baseline qualification is reconstructed only at its known-qualified
        # checkpoints; validated frozen inputs and checkpoint output hashes agree.
        if any(m['signal_qualified'] for m in old['methods']):
            result = engine.strategy.analyze(markets['QQQ'], {s:markets[s] for s in MAG7}, vix15, at, policy=policy)
            deadline = stock_deadline(base, at, engine)
            for candidate in result.get('entry_candidates', []):
                observations['baseline'][candidate['strategy_id']].append({
                    k:candidate[k] for k in ('event_id','direction','entry','stop','target')}
                    | {'at':at.isoformat(), 'valid_until':signal_expiry(candidate, deadline).isoformat()})
        if at.hour == 15 and at.minute == 56:
            print(day, 'native candidates checked', flush=True)
    # A partial native session is not admitted as a full-session comparison.
    expected = Counter(timestamp(r['at']).astimezone(ET).date().isoformat() for r in old_records)
    complete = Counter(timestamp(r['at']).astimezone(ET).date().isoformat()
                       for r in records if r.get('status') == 'native_candle_evaluation')
    days = sorted(d for d in native_days if complete[d] == expected[d])
    phases = chronological_split(days, plan['split']['development_fraction']) if len(days) >= 2 else {}
    report = {'schema': 'pivot-native-method-experiments-v1', 'plan':plan, 'engine':engine.identity,
              'base_inputs':base.inputs, 'native_input':native.input, 'native_provenance':native.provenance,
              'baseline_result':baseline_identity, 'gate_counts':dict(counters),
              'complete_native_days':days, 'phases':phases, 'results':{},
              'future_holdout_performed':False, 'promotion':'blocked_no_untouched_execution_evidence',
              'known_history_only':True, 'live_arrivals_verified':False,
              'status':'completed_known_history_evaluation' if phases else 'blocked_insufficient_complete_native_sessions'}
    for variant, methods in observations.items():
        report['results'][variant] = {}
        for method, allrows in methods.items():
            rows = [r for r in allrows if timestamp(r['at']).astimezone(ET).date().isoformat() in days]
            detail = {'qualified_observations':len(rows),
                      'distinct_events':len({r['event_id'] for r in rows}),
                      'direction_counts':dict(Counter(r['direction'] for r in rows)), 'scenarios':{}}
            report['results'][variant][method] = detail
            for scenario, costs in plan['execution_scenarios'].items():
                detail['scenarios'][scenario] = {}
                for phase, phase_days in phases.items():
                    bars = [b for b in native.qqq if b.end.astimezone(ET).date().isoformat() in phase_days]
                    scoped = [r for r in rows if timestamp(r['at']).astimezone(ET).date().isoformat() in phase_days]
                    signals, rejected = executable_observations(scoped, bars, costs['latency_seconds'])
                    cfg = ExecutionConfig(starting_capital=plan['fixed']['starting_capital'],
                        trade_notional=plan['fixed']['purchase_target'], fractional_precision=plan['fixed']['fractional_precision'],
                        max_entry_drift=plan['fixed']['max_entry_drift'],
                        session_closes={d:base.sessions[d]['close'] for d in phase_days}, **costs)
                    execution = run_execution(bars, signals, cfg)
                    item = {'qualified_observations':len(scoped), 'eligible_observations':len(signals),
                            'deadline_rejections':rejected, 'metrics':summarize(execution)}
                    if variant == 'baseline':
                        def series_at(at):
                            return native_at(native, base, at, plan['fixed']['publication_delay_seconds'])[1]
                        item['X1_matched_entry_exits'] = [matched_exit(engine, trade, bars, series_at,
                            base.sessions[trade['entry_day']]['close'], cfg,
                            plan['fixed']['publication_delay_seconds']) for trade in execution['trades']]
                        item['X1_status'] = ('matched_entry_exit_comparison_only' if execution['trades']
                                             else 'not_evaluable_no_admissible_baseline_entries')
                    detail['scenarios'][scenario][phase] = item
                    _dump(out/f'{variant}-{method}-{scenario}-{phase}-execution.json', execution)
    _dump(out/'checkpoints.json', records)
    _dump(out/'observations.json', observations)
    _dump(out/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('native','warmup','legacy','five-minute','commit','baseline-result','plan','out'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    with disconnected():
        sources = [Path(__file__), *[Path('research')/name for name in (
            'current_method_experiments.py','five_minute_candidates.py','validate_v2.py','execution.py','metrics.py','offline.py','data.py')],
            Path('pivot/models.py'),Path('pivot/feeds.py')]
        hashes = {str(p):sha256(p.read_bytes()).hexdigest() for p in sources}
        plan_bytes = Path(args.plan).read_bytes()
        plan = json.loads(plan_bytes)
        if plan.get('version') != '2026-09-18-native-methods-v3':
            raise ValueError('Explicit native v3 experiment plan required')
        base = load_inputs(args.native,args.warmup,args.legacy)
        native = load_native(args.five_minute, base)
        engine = freeze_engine(args.commit)
        report = run(base,native,engine,plan,args.baseline_result,args.out)
        if hashes != {str(p):sha256(p.read_bytes()).hexdigest() for p in sources} or plan_bytes != Path(args.plan).read_bytes():
            raise RuntimeError('Research source or plan changed during the run')
        _dump(Path(args.out)/'manifest.json', {'plan_sha256':sha256(plan_bytes).hexdigest(),
            'source_hashes':hashes,'engine':engine.identity,'base_inputs':base.inputs,'native_input':native.input,
            'outputs':{p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(Path(args.out).iterdir()) if p.is_file()}})
        print(json.dumps({'status':report['status'],'days':report['complete_native_days'],'gates':report['gate_counts']}))


if __name__ == '__main__':
    main()
