"""Bounded offline gate validation of a frozen production v2 interpretation.

This is a retrospective candle replay, never a broker simulator or return estimate.
Inputs are actual saved provider candles. Future bars and partial history cannot
qualify a checkpoint. Run with ``python -m research.validate_v2 --help``.
"""
from collections import Counter
from dataclasses import asdict
from datetime import date, time, timedelta
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace
import argparse
import gzip
import json
import subprocess
import sys

from pivot.models import Bar, MAG7, timestamp
from pivot.feeds import ET
from research.data import load as load_legacy
from research.offline import disconnected

SYMBOLS = ('QQQ', *MAG7)
METHODS = ('four_hour_retest', 'prior_day_sweep')
ENGINE_FILES = ('models.py', 'strategy.py', 'history_health.py', 'feeds.py', 'data_health.py')


def _json(path):
    content = Path(path).read_bytes()
    data = json.loads(gzip.decompress(content) if str(path).endswith('.gz') else content)
    return data, sha256(content).hexdigest()


def _calendar(raw):
    sessions = {}
    for day, row in sorted(raw.items()):
        if date.fromisoformat(day).isoformat() != day or set(row) != {'open', 'close'}:
            raise ValueError('Expected calendar dates with open and close')
        opened, closed = (timestamp(row[key]).astimezone(ET) for key in ('open', 'close'))
        if (opened.date().isoformat() != day or closed.date().isoformat() != day
                or opened.time() != time(9, 30) or not opened < closed
                or closed.time() > time(16) or (closed - opened).total_seconds() % 900):
            raise ValueError('Unsupported exchange session')
        sessions[day] = {'open': opened, 'close': closed}
    if not sessions:
        raise ValueError('An actual exchange calendar is required')
    return sessions


def ends(session, minutes):
    at = session['open'] + timedelta(minutes=minutes)
    while at <= session['close']:
        yield at
        at += timedelta(minutes=minutes)


def _bars(rows, minutes, sessions):
    bars = [Bar(**{**row, 'end': timestamp(row['end'])}) for row in rows]
    if (any(type(b.minutes) is not int or b.minutes != minutes for b in bars)
            or any(a.end >= b.end for a, b in zip(bars, bars[1:]))):
        raise ValueError('Expected genuine, ordered, unique native candles')
    permitted = {end for session in sessions.values() for end in ends(session, minutes)}
    if any(bar.end not in permitted for bar in bars):
        raise ValueError('Native candle is outside the exchange calendar or frame grid')
    return bars


def _provenance(data):
    p = data['stock_provenance']
    if p['source'] not in ('alpaca_iex', 'Alpaca IEX') or p['adjustment'] != 'split':
        raise ValueError('Validation inputs must share the actual IEX split-adjusted source')


def merge_bars(older, newer, replace_start=None, replace_end=None):
    """Prefer newer observations on overlap; record corrections, never fill gaps."""
    indexed = {bar.end: bar for bar in older}
    changed = sum(bar.end in indexed and indexed[bar.end] != bar for bar in newer)
    if replace_start is not None and replace_end is not None:
        indexed = {at: bar for at, bar in indexed.items()
                   if not replace_start <= bar.end - timedelta(minutes=bar.minutes) < replace_end}
    indexed.update({bar.end: bar for bar in newer})
    return [indexed[at] for at in sorted(indexed)], changed


def load_inputs(native_path, warmup_path, legacy_path):
    native, native_sha = _json(native_path)
    warmup, warmup_sha = _json(warmup_path)
    if native.get('schema') != 'pivot-native-five-minute-validation-v1':
        raise ValueError('Unknown native validation dataset')
    if warmup.get('schema') != 'pivot-native-five-minute-warmup-v1':
        raise ValueError('Unknown native warmup dataset')
    legacy, old_sessions, old_stocks, vix, legacy_sha = load_legacy(legacy_path)
    for data in (native, warmup, legacy):
        _provenance(data)
    vp = legacy['vix_provenance']
    if vp.get('source_symbol') != 'CBOE:VIX' or vp.get('zero_delay_metadata_verified_at_receipt') is not True:
        raise ValueError('Saved actual VIX provenance is required')
    sessions = dict(old_sessions)
    native_calendar, warmup_calendar = _calendar(native['sessions']), _calendar(warmup['sessions'])
    for calendar in (native_calendar, warmup_calendar):
        for day, session in calendar.items():
            if day in sessions and sessions[day] != session:
                raise ValueError('Input exchange calendars disagree')
            sessions[day] = session
    sessions = dict(sorted(sessions.items()))
    if set(native['stocks']) != set(SYMBOLS) or set(warmup['stocks']) != set(MAG7):
        raise ValueError('All required stock symbols must be present')
    stocks15, stocks5, corrections = {}, {}, {}
    for symbol in SYMBOLS:
        bars15 = _bars(native['stocks'][symbol]['15'], 15, native_calendar)
        stocks15[symbol], corrections[symbol] = merge_bars(
            old_stocks[symbol], bars15, min(s['open'] for s in native_calendar.values()),
            timestamp(native['requested_at']))
        if symbol in MAG7:
            # The later warmup request covers the whole native 5m window, so it
            # replaces that history. Deleted/corrected candles are not resurrected.
            stocks5[symbol] = _bars(warmup['stocks'][symbol], 5, warmup_calendar)
            _bars(native['stocks'][symbol]['5'], 5, native_calendar)
    return SimpleNamespace(sessions=sessions, stocks15=stocks15, stocks5=stocks5, vix=vix,
                           evaluation_days=sorted(native['leader_sessions']),
                           calendar_start=min(old_sessions),
                           inputs={'native': {'name': Path(native_path).name, 'sha256': native_sha},
                                   'warmup': {'name': Path(warmup_path).name, 'sha256': warmup_sha},
                                   'legacy': {'name': Path(legacy_path).name, 'sha256': legacy_sha}},
                           provenance={'native': native['stock_provenance'], 'warmup': warmup['stock_provenance'],
                                       'legacy': legacy['stock_provenance'], 'vix': vp,
                                       'stock15_overlap_corrections': corrections,
                                       'precedence': 'Older actual IEX 15m prefix; newer native 15m replaces overlap. Later 14-session native 5m request supplies all leader candles.'})


def freeze_engine(commit, repo=None):
    """Load the committed production modules in an isolated in-memory package."""
    repo = Path(repo or Path(__file__).resolve().parents[1])
    def git(*args):
        result = subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
        return result.stdout
    commit = git('rev-parse', '--verify', commit + '^{commit}').decode().strip()
    package_name = '_pivot_validation_' + commit[:16]
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package
    modules, hashes = {}, {}
    for filename in ENGINE_FILES:
        raw = git('show', f'{commit}:pivot/{filename}')
        name = filename[:-3]
        module = ModuleType(package_name + '.' + name)
        module.__package__ = package_name
        module.__file__ = f'{commit}:pivot/{filename}'
        sys.modules[module.__name__] = module
        setattr(package, name, module)
        exec(compile(raw, module.__file__, 'exec'), module.__dict__)
        modules[name], hashes['pivot/' + filename] = module, sha256(raw).hexdigest()
    if modules['strategy'].ANALYSIS_VERSION != 'nasdaq-video-interpretation-v2':
        raise ValueError('The frozen commit must contain production strategy v2')
    return SimpleNamespace(**modules, identity={'commit': commit, 'files_sha256': hashes,
                           'analysis_version': modules['strategy'].ANALYSIS_VERSION,
                           'policy': asdict(modules['strategy'].BASELINE_POLICY)})


def at_time(inputs, at, engine):
    """Production rolling windows, with receipt time explicitly simulated.

    Calendar availability is real, prices may contain later source corrections.
    Assigning observed_at=at permits candle analysis, not historical live admission.
    """
    at = timestamp(at)
    day = at.astimezone(ET).date().isoformat()
    start = (at.astimezone(ET) - timedelta(days=60)).replace(hour=0, minute=0, second=0, microsecond=0)
    sessions = {d: s for d, s in inputs.sessions.items() if start.date().isoformat() <= d <= day}
    leader_sessions = engine.feeds.leader_history_sessions(sessions)
    leader_start = min(s['open'] for s in leader_sessions.values())
    previous = max((d for d in sessions if d < day), default=None)
    markets = {}
    for symbol in SYMBOLS:
        bars = [b for b in inputs.stocks15[symbol] if b.end <= at and b.end - timedelta(minutes=15) >= start]
        frames = {15: bars, 60: engine.feeds.resample(bars, 60, sessions),
                  240: engine.feeds.resample(bars, 240, sessions), 1440: engine.feeds.daily(bars, sessions)}
        if symbol in MAG7:
            frames[5] = [b for b in inputs.stocks5[symbol]
                         if b.end <= at and b.end - timedelta(minutes=5) >= leader_start]
        markets[symbol] = engine.models.Market(symbol, frames, 'alpaca_iex', True, at, previous_session=previous)
    oldest = at.astimezone(ET).date() - timedelta(days=14)
    vix_sessions = {d: s for d, s in inputs.sessions.items() if oldest.isoformat() <= d <= day}
    bars = [b for b in inputs.vix if b.end <= at and b.end.astimezone(ET).date() >= oldest]
    index = engine.models.Market('I:VIX', {15: bars}, 'insightsentry', True, at,
                                valid_until=bars[-1].end + timedelta(seconds=990) if bars else at)
    return markets, index, sessions, leader_sessions, vix_sessions


def checkpoint(inputs, at, engine):
    markets, vix, sessions, leader_sessions, vix_sessions = at_time(inputs, at, engine)
    health = engine.data_health.stock_health(markets, sessions, at)
    incomplete = []
    for symbol, market in markets.items():
        for minutes in ((15, 60, 240, 1440) if symbol == 'QQQ' else (5, 15, 240)):
            calendar = leader_sessions if minutes == 5 else sessions
            # Include the exact cutoff candle: eventual historical completeness
            # does not prove it was published within the live 90-second allowance.
            gaps = engine.history_health.frame_gaps(market.bars[minutes], calendar, minutes, at + timedelta(seconds=90))
            if gaps['missing_count']:
                incomplete.append({'symbol': symbol, 'minutes': minutes, **gaps})
    vix_gaps = engine.history_health.frame_gaps(vix.bars[15], vix_sessions, 15, at + timedelta(seconds=90))
    if vix_gaps['missing_count']:
        incomplete.append({'symbol': 'I:VIX', 'minutes': 15, **vix_gaps})
    enough_calendar = inputs.calendar_start <= (at.astimezone(ET).date() - timedelta(days=60)).isoformat()
    coverage = {'complete': bool(not incomplete and enough_calendar and len(leader_sessions) == 7
                                 and health['status'] == 'current'),
                'calendar_covers_60_day_start': enough_calendar, 'leader_session_count': len(leader_sessions),
                'context_session_count': len(sessions), 'vix_session_count': len(vix_sessions),
                'missing_frames': incomplete, 'stock_health': health['status']}
    record = {'at': at.isoformat(), 'coverage': coverage, 'methods': [],
              'live_arrival_verified': False, 'broker_admission_evaluated': False}
    if not coverage['complete']:
        record['evaluation'] = 'unknown_incomplete_history'
        return record
    result = engine.strategy.analyze(markets['QQQ'], {s: markets[s] for s in MAG7}, vix, at)
    record['evaluation'] = 'retrospective_candle_rules'
    record['selected_method'] = result['strategy_id']
    record['vix'] = {'latest_at': vix.bars[15][-1].end.isoformat() if vix.bars[15] else None,
                     'long_trade_confirmation': engine.strategy.vix_confirmation(vix, 'long', at)[0],
                     'short_trade_confirmation': engine.strategy.vix_confirmation(vix, 'short', at)[0]}
    for branch in result['strategies']:
        evidence = branch.get('leader_evidence', {})
        record['methods'].append({'id': branch['id'], 'state': branch['state'],
                                  'first_failed_gate': next((c['name'] for c in branch['checks'] if not c['passed']), None),
                                  'checks': branch['checks'], 'direction': branch['direction'],
                                  'signal_qualified': branch['state'] == 'SETUP_READY',
                                  'event_id': branch.get('event_id'), 'event_origin_at': branch.get('event_origin_at'),
                                  'event_expires_at': branch.get('event_expires_at'),
                                  'leader_votes': {s: row['vote'] for s, row in evidence.items()},
                                  'leader_reasons': {s: row['reason'] for s, row in evidence.items()},
                                  'levels_count': len(branch.get('levels', []))})
    return record


def summarize(records):
    summary = {'checkpoint_count': len(records),
               'complete_history_checkpoints': sum(r['coverage']['complete'] for r in records),
               'unknown_incomplete_history_checkpoints': sum(not r['coverage']['complete'] for r in records),
               'methods': {}}
    for method in METHODS:
        rows = [branch for r in records for branch in r['methods'] if branch['id'] == method]
        qualified = [r for r in rows if r['signal_qualified']]
        summary['methods'][method] = {'evaluated_checkpoints': len(rows),
                                      'first_failed_gate_counts': dict(sorted(Counter(r['first_failed_gate'] or 'All candle checks passed' for r in rows).items())),
                                      'state_counts': dict(sorted(Counter(r['state'] for r in rows).items())),
                                      'signal_qualified_checkpoints': len(qualified),
                                      'distinct_qualified_events': len({r['event_id'] for r in qualified if r['event_id']})}
    return summary


def validate(inputs, engine, before_day, *, progress=None):
    before_day = date.fromisoformat(before_day).isoformat()
    vix_ends = {b.end for b in inputs.vix}
    records, days = [], []
    for day in inputs.evaluation_days:
        if day >= before_day:
            continue
        session = inputs.sessions[day]
        missing_vix = len(set(ends(session, 15)) - vix_ends)
        if missing_vix:
            days.append({'day': day, 'evaluation': 'excluded_incomplete_actual_vix_session',
                         'missing_vix_candles': missing_vix})
            continue
        samples = [checkpoint(inputs, at, engine) for at in ends(session, 5)]
        records.extend(samples)
        days.append({'day': day, 'evaluation': 'sampled', **summarize(samples)})
        if progress:
            progress(day, days[-1])
    return {'schema': 'pivot-production-v2-gate-validation-v1', 'strategy': engine.identity,
            'analysis_script_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
            'inputs': inputs.inputs, 'provenance': inputs.provenance, 'before_day_exclusive': before_day,
            'checkpoint_minutes': 5, 'days': days, 'summary': summarize(records), 'records': records,
            'limitations': [
                'Pre-known retrospective evaluation of six recent sessions is not an unseen holdout or evidence of profitability.',
                'Every candle is cut off at the simulated checkpoint; observed_at is a simulation clock, not historical arrival evidence.',
                'Split-adjusted IEX candles may contain later provider corrections; IEX is one exchange, not consolidated trades.',
                'Actual VIX history is reused without new requests; historic candles do not prove a fresh executable VIX quote was available.',
                'Full required history and current stock readiness are checked before analyzing each sample. Missing history remains unknown.',
                'Repeated qualified checkpoints can describe one persistent event; unique event counts are reported separately.',
                'No broker admission, account buying power, quotes/spread, short eligibility, order execution, fills, fees, slippage, P&L or profitability are tested.',
                'The frozen v2 settings were not tuned by this replay. Source interpretation and live commissioning remain separate validation tasks.'
            ]}


def markdown(report):
    summary = report['summary']
    lines = ['# Production v2 candle-rule validation', '',
             f"Frozen production commit: `{report['strategy']['commit']}`.", '',
             f"{summary['checkpoint_count']} five-minute checkpoints; {summary['complete_history_checkpoints']} with complete required history; "
             f"{summary['unknown_incomplete_history_checkpoints']} unknown because history was incomplete.", '',
             '| Method | Evaluated | Signal-qualified checkpoints | Distinct qualified events |',
             '|---|---:|---:|---:|']
    for method, row in summary['methods'].items():
        lines.append(f"| {method} | {row['evaluated_checkpoints']} | {row['signal_qualified_checkpoints']} | {row['distinct_qualified_events']} |")
    lines += ['', '## First failed candle checks', '']
    for method, row in summary['methods'].items():
        lines.append(f"- **{method}:** " + '; '.join(f'{gate}: {count}' for gate, count in row['first_failed_gate_counts'].items()) + '.')
    lines += ['', '## Session coverage', '']
    for row in report['days']:
        lines.append(f"- {row['day']}: " + (f"{row['checkpoint_count']} sampled, {row['complete_history_checkpoints']} complete-history checkpoints."
                                             if row['evaluation'] == 'sampled' else f"excluded; {row['missing_vix_candles']} actual VIX candles missing."))
    lines += ['', '## Scope and limits', ''] + ['- ' + line for line in report['limitations']]
    lines += ['', '## Input hashes', ''] + [f"- {row['name']}: `{row['sha256']}`" for row in report['inputs'].values()]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--warmup', required=True, type=Path)
    parser.add_argument('--legacy', required=True, type=Path)
    parser.add_argument('--strategy-commit', required=True)
    parser.add_argument('--before-day', required=True, help='Exclusive ISO day; omit today from historical validation')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    with disconnected():
        inputs = load_inputs(args.native, args.warmup, args.legacy)
        engine = freeze_engine(args.strategy_commit)
        report = validate(inputs, engine, args.before_day,
                          progress=lambda day, row: print(f"{day}: {row['complete_history_checkpoints']}/{row['checkpoint_count']} complete", flush=True))
        args.output.mkdir(parents=True, exist_ok=True)
        for name, content in [('report.json', json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n'),
                              ('REPORT.md', markdown(report))]:
            with (args.output / name).open('x') as output:
                output.write(content)
            (args.output / name).chmod(0o600)
        print(json.dumps(report['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
