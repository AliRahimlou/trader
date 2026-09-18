"""Disconnected, source-versioned experiments for the two current app methods.

The saved sample can test candle gates and conservative execution feasibility.
It cannot reconstruct missing five-minute QQQ/VIX candles or live arrival times.
This module never changes the production analyzer, settings, or broker state.
"""
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import argparse
import json

from pivot.feeds import ET
from pivot.models import timestamp
from research.execution import ExecutionConfig, TradeSignal, run_execution
from research.metrics import summarize, cash_benchmark
from research.offline import disconnected
from research.validate_v2 import at_time, checkpoint, ends, freeze_engine, load_inputs


def chronological_split(days, fraction=.6):
    ordered = sorted(days)
    if len(ordered) < 2 or len(set(ordered)) != len(ordered) or not 0 < fraction < 1:
        raise ValueError('At least two unique sessions and an interior split fraction are required')
    cut = min(len(ordered) - 1, max(1, int(len(ordered) * fraction)))
    return {'development': ordered[:cut], 'validation_known_history': ordered[cut:]}


def capabilities(inputs):
    """Only loader-validated native inputs qualify; never upsample existing bars."""
    qqq5 = inputs.stocks5.get('QQQ', [])
    vix5 = getattr(inputs, 'native_vix5', [])
    provenance = getattr(inputs, 'native_vix5_provenance', {})
    return {'native_qqq_5m': bool(qqq5) and all(b.minutes == 5 for b in qqq5),
            'native_actual_vix_5m': (bool(vix5) and all(b.minutes == 5 for b in vix5)
                and provenance.get('source_symbol') == 'CBOE:VIX'
                and provenance.get('native_resolution_minutes') == 5
                and provenance.get('zero_delay_metadata_verified_at_receipt') is True),
            'historical_live_receipts': False, 'historical_executable_quotes': False}


def proposed_readiness(plan, available):
    rows = {}
    for item in plan['proposed_variants']:
        missing = [name for name in item['requires'] if available.get(name) is not True]
        reasons = ['missing:' + name for name in missing]
        if item['implementation'] != 'implemented':
            reasons.append('isolated_candidate_implementation_pending')
        rows[item['id']] = {'ready': not reasons, 'status': 'blocked' if reasons else 'ready_for_offline_signal_evaluation',
                            'reasons': reasons, 'specification': item['specification'],
                            'entrypoint': item.get('entrypoint')}
    return rows


def signal_expiry(candidate, stock_valid_until):
    fields = ('event_expires_at', 'leader_evidence_valid_until', 'leader_observation_valid_until')
    values = [timestamp(candidate[name]) for name in fields]
    values.append(timestamp(stock_valid_until))
    return min(values)


def stock_deadline(inputs, at, engine):
    # Use the same historical calendar window as checkpoint. The full dataset
    # calendar includes future sessions and older history outside the window.
    markets, _, sessions, *_ = at_time(inputs, at, engine)
    health = engine.data_health.stock_health(markets, sessions, at)
    if health['status'] != 'current' or not health.get('valid_until'):
        raise ValueError('A qualified checkpoint must retain current scoped stock health')
    return health['valid_until']


def executable_observations(observations, bars, latency_seconds=0):
    """Discard expired observations before delegating to the OHLC simulator.

    The first opening at or after decision plus latency is the only available
    causal execution price. A later bar cannot renew the decision's deadline.
    This filter never uses a bar's future high/low/close to admit a signal.
    """
    if latency_seconds < 0:
        raise ValueError('Negative latency is not permitted')
    openings = [b.end - timedelta(minutes=b.minutes) for b in bars]
    if any(a >= b for a, b in zip(openings, openings[1:])):
        raise ValueError('Execution openings must be unique and ordered')
    admitted, rejected = [], []
    for row in observations:
        at, until = timestamp(row['at']), timestamp(row['valid_until'])
        first = bisect_left(openings, at + timedelta(seconds=latency_seconds))
        reason = None
        if first == len(openings):
            reason = 'no_execution_price_after_decision'
        elif openings[first].astimezone(ET).date() != at.astimezone(ET).date():
            reason = 'no_same_session_execution_price'
        elif openings[first] >= until:
            reason = 'available_open_after_signal_expiry'
        if reason:
            rejected.append({'setup_id': row['event_id'], 'at': at.isoformat(),
                             'valid_until': until.isoformat(), 'reason': reason,
                             'next_available_open': openings[first].isoformat() if first < len(openings) else None})
        else:
            admitted.append(TradeSignal(row['event_id'], at, row['direction'],
                                        row['entry'], row['stop'], row['target']))
    return admitted, rejected


def _dump(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def _proxy(engine, policy, captured):
    def analyze(*args, **kwargs):
        result = engine.strategy.analyze(*args, policy=policy, **kwargs)
        captured['result'] = result
        return result
    return SimpleNamespace(**{**vars(engine), 'strategy': SimpleNamespace(
        **{**vars(engine.strategy), 'analyze': analyze})})


def run(inputs, engine, plan, out):
    out = Path(out)
    if out.exists():
        raise ValueError('Refusing to overwrite a previous experiment directory')
    out.mkdir(parents=True)
    # Whole native evaluation sessions with actual VIX and QQQ execution bars.
    # Full rolling-history checks still run independently at each checkpoint.
    vix_ends = {b.end for b in inputs.vix}
    qqq_ends = {b.end for b in inputs.stocks15['QQQ']}
    days = [day for day in inputs.evaluation_days
            if set(ends(inputs.sessions[day], 15)) <= vix_ends & qqq_ends]
    phases = chronological_split(days, plan['split']['development_fraction'])
    report = {'schema': plan['schema'], 'plan': plan, 'engine': engine.identity,
              'inputs': inputs.inputs, 'provenance': inputs.provenance,
              'capabilities': capabilities(inputs), 'phases': phases, 'evaluation_days': days,
              'proposed_variants': proposed_readiness(plan, capabilities(inputs)),
              'results': {}, 'future_holdout': {**plan['future_holdout'], 'performed': False},
              'promotion': 'blocked: previously inspected short sample, no untouched holdout or observed execution costs',
              'limitations': [
                  'Candle receipt timestamps are simulated, not authentic historical live arrival records.',
                  'Split-adjusted IEX prices may incorporate subsequent corrections; IEX is one exchange.',
                  'No native QQQ/VIX five-minute series is reconstructed from fifteen-minute history.',
                  'Methods are simulated independently and must not be summed as a portfolio.',
                  'Short signals are recorded but rejected by the fractional long-only research executor.',
                  'Fifteen-minute execution openings can miss short signal deadlines; missing prices are not invented.',
                  'Simulated protection and fill assumptions are not broker commissioning.',
                  'No exact video formula or future profitability is established.']}
    for variant in plan['variants']:
        policy = engine.strategy.AnalysisPolicy(
            zone_tolerance=plan['fixed']['zone_tolerance'], persistence_bars=plan['fixed']['persistence_bars'],
            minimum_leaders=variant['minimum_leaders'], maximum_opposition=variant['maximum_opposition'])
        captured, observations, counters = {}, {m: [] for m in plan['methods']}, Counter()
        proxy = _proxy(engine, policy, captured)
        records = []
        for day in days:
            for end in ends(inputs.sessions[day], 5):
                at = end + timedelta(seconds=plan['fixed']['publication_delay_seconds'])
                # A close-plus-publication decision after the session close cannot enter.
                if at >= inputs.sessions[day]['close']:
                    continue
                captured.clear()
                record = checkpoint(inputs, at, proxy)
                records.append(record)
                counters['checkpoints'] += 1
                counters['complete_history_checkpoints'] += bool(record['coverage']['complete'])
                for method in record['methods']:
                    counters[method['id'] + ':' + (method['first_failed_gate'] or 'qualified')] += 1
                candidates = captured.get('result', {}).get('entry_candidates', [])
                if candidates:
                    valid_until = stock_deadline(inputs, at, engine)
                    for candidate in candidates:
                        if candidate['strategy_id'] not in observations:
                            continue
                        observations[candidate['strategy_id']].append(
                            {k: candidate[k] for k in ('event_id', 'direction', 'entry', 'stop', 'target')}
                            | {'at': at.isoformat(),
                               'valid_until': signal_expiry(candidate, valid_until).isoformat()})
            print(variant['id'], day, 'checked', flush=True)
        result = {'policy': asdict(policy), 'gate_counts': dict(counters), 'methods': {}}
        _dump(out / (variant['id'] + '-checkpoints.json'), records)
        for method, rows in observations.items():
            method_result = {'qualified_observations': len(rows),
                             'independent_qualified_events': len({r['event_id'] for r in rows}),
                             'direction_counts': dict(Counter(r['direction'] for r in rows)), 'scenarios': {}}
            for scenario, costs in plan['execution_scenarios'].items():
                phase_results = {}
                for phase, scoped_days in phases.items():
                    bars = [b for b in inputs.stocks15['QQQ']
                            if b.end.astimezone(ET).date().isoformat() in scoped_days]
                    scoped = [r for r in rows if timestamp(r['at']).astimezone(ET).date().isoformat() in scoped_days]
                    signals, rejected = executable_observations(scoped, bars, costs['latency_seconds'])
                    cfg = ExecutionConfig(
                        starting_capital=plan['fixed']['starting_capital'], trade_notional=plan['fixed']['purchase_target'],
                        fractional_precision=plan['fixed']['fractional_precision'],
                        max_entry_drift=plan['fixed']['max_entry_drift'],
                        session_closes={d: inputs.sessions[d]['close'] for d in scoped_days}, **costs)
                    execution = run_execution(bars, signals, cfg)
                    metrics = summarize(execution)
                    phase_results[phase] = {'metrics': metrics, 'cash_benchmark': cash_benchmark(cfg.starting_capital, len(scoped_days)),
                        'deadline_rejections': rejected, 'eligible_observations': len(signals),
                        'qualified_observations': len(scoped),
                        'independent_qualified_events': len({r['event_id'] for r in scoped}),
                        'status': 'inconclusive_no_untouched_execution_evidence'}
                    _dump(out / f'{variant["id"]}-{method}-{scenario}-{phase}-execution.json', execution)
                method_result['scenarios'][scenario] = phase_results
            result['methods'][method] = method_result
        report['results'][variant['id']] = result
        _dump(out / 'report.partial.json', report)
    _dump(out / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True)
    parser.add_argument('--warmup', required=True)
    parser.add_argument('--legacy', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--plan', default='research/current-method-plan.json')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    with disconnected():
        files = [Path(__file__), Path('research/validate_v2.py'), Path('research/data.py'),
                 Path('research/execution.py'), Path('research/metrics.py'), Path('research/offline.py'),
                 Path('research/five_minute_candidates.py'), Path('pivot/models.py'), Path('pivot/feeds.py')]
        source_hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in files}
        plan_bytes = Path(args.plan).read_bytes()
        plan = json.loads(plan_bytes)
        if plan.get('schema') != 'pivot-current-method-experiments-v1':
            raise ValueError('Unknown experiment plan')
        inputs = load_inputs(args.native, args.warmup, args.legacy)
        engine = freeze_engine(args.commit)
        if engine.identity['analysis_version'] != 'nasdaq-video-interpretation-v3':
            raise ValueError('This experiment requires the current v3 interpretation')
        report = run(inputs, engine, plan, args.out)
        if source_hashes != {str(p): sha256(p.read_bytes()).hexdigest() for p in files}:
            raise RuntimeError('Research source changed during this run; results are not verified')
        if Path(args.plan).read_bytes() != plan_bytes:
            raise RuntimeError('Experiment plan changed during this run; results are not verified')
        manifest = {'plan_sha256': sha256(plan_bytes).hexdigest(), 'engine': engine.identity,
                    'research_source_sha256': source_hashes,
                    'outputs': {p.name: sha256(p.read_bytes()).hexdigest()
                                for p in sorted(Path(args.out).iterdir()) if p.is_file()}}
        _dump(Path(args.out) / 'manifest.json', manifest)
        print(json.dumps({'days': report['evaluation_days'], 'phases': report['phases'],
                          'proposed_variants': report['proposed_variants'], 'promotion': report['promotion']}))


if __name__ == '__main__':
    main()
