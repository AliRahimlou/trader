"""Observational decision evidence; no broker calls, permission changes or rules.

Only market evidence and explicit health fields are admitted. Provider errors,
account objects, credentials, quotes and raw responses are never copied.
"""
from copy import deepcopy
from hashlib import sha256
import json
from .models import MAG7, timestamp
from .market_context import market_context
from .strategy import closed, leader_diagnostics, vix_diagnostics

VERSION = 'decision-trace-v3'
SOURCES = {'alpaca_iex', 'alpaca_sip', 'insightsentry', 'massive_indices'}
SETUP_KEYS = ('state', 'strategy_id', 'direction', 'entry', 'stop', 'target', 'event', 'event_at', 'retest_at',
              'event_id', 'event_origin_at', 'event_expires_at', 'latest_evidence_at', 'policy_version',
              'leader_observation_at', 'leader_observations_synchronized', 'leader_evidence_valid_until',
              'leader_observation_valid_until', 'leader_rule', 'area_rule', 'event_rule', 'reward_risk',
              'target_source', 'target_zone', 'vix_reaction_at', 'vix_zone', 'vix_reaction_age_minutes',
              'vix_rule', 'exit_rule')
ZONE_KEYS = ('low', 'high', 'established_at', 'touches', 'source')
CHECK_NAMES = {'Current Nasdaq observation', 'Premarked levels', 'Nasdaq level event',
               'Magnificent Seven at their zones', 'Actual VIX zone reaction', 'Stop and target'}
HEALTH_STATUSES = {'current', 'missing', 'stale', 'incomplete', 'needs_attention', 'blocked', 'market_closed'}


def _time(value):
    return timestamp(value).isoformat() if value is not None else None


def _bar(bar):
    return {'end': bar.end.isoformat(), 'minutes': bar.minutes, 'open': bar.open,
            'high': bar.high, 'low': bar.low, 'close': bar.close}


def _zone(zone):
    return {'low': zone.low, 'high': zone.high, 'established_at': zone.established_at.isoformat(),
            'touches': zone.touches, 'source': zone.source}


def _leader_summary(rows):
    """Branch evidence without duplicating entire historical area lists per method."""
    keys = ('vote', 'vote_source', 'reason', 'timeframe_minutes', 'observational_only', 'latest_bar_at',
            'latest_close', 'reaction_at', 'age_minutes', 'conflicting_reactions', 'evidence_valid_until',
            'observation_valid_until')
    result = {}
    for symbol in MAG7:
        row = rows.get(symbol) or {}
        result[symbol] = {key: deepcopy(row.get(key)) for key in keys}
        zone = row.get('reaction_zone') or {}
        result[symbol]['reaction_zone'] = {key: deepcopy(zone.get(key)) for key in
                                           ('low', 'high', 'source', 'established_at')} if zone else None
    return result


def _market(market, now, frames):
    if market is None:
        return {'source': None, 'observed_at': None, 'realtime': False, 'frames': {}}
    result = {'source': market.source if market.source in SOURCES else 'unrecognized',
              'observed_at': market.observed_at.isoformat(), 'realtime': market.realtime is True, 'frames': {}}
    for minutes in frames:
        bars = closed(market, minutes, now)
        result['frames'][str(minutes)] = {'count': len(bars),
            'latest_at': bars[-1].end.isoformat() if bars else None,
            'recent': [_bar(b) for b in bars[-2:]] if minutes in (5, 15, 60) else []}
    return result


def _health(health):
    """Allowlist structural diagnostics; upstream error text can contain secrets."""
    health = health or {}
    stocks, vix = health.get('stocks') or {}, health.get('vix') or {}
    status = lambda value: value if value in HEALTH_STATUSES else 'unavailable'
    instruments = []
    for row in stocks.get('instruments', []):
        if row.get('symbol') not in ('QQQ', *MAG7):
            continue
        frames = [{key: f.get(key) for key in ('minutes', 'count', 'missing_count')} |
                  {key: _time(f.get(key)) for key in ('latest_at', 'expected_at', 'first_missing_at', 'valid_until')} |
                  {'status': status(f.get('status'))} for f in row.get('frames', [])]
        instruments.append({'symbol': row['symbol'], 'status': status(row.get('status')), 'frames': frames,
                            'valid_until': _time(row.get('valid_until'))})
    verification = vix.get('verification') or {}
    # No raw verification object or error text is retained.
    public_verification = {key: verification.get(key) for key in ('delay_seconds', 'market_open') if key in verification}
    public_verification.update({key: _time(verification.get(key)) for key in
        ('latest_value_at', 'quote_updated_at', 'source_updated_at', 'history_received_at',
         'metadata_received_at') if key in verification})
    budget = verification.get('budget') or {}
    public_verification['budget'] = {key: budget[key] for key in ('used', 'limit', 'remaining', 'reserved', 'actual_requests')
                                     if isinstance(budget.get(key), (int, float)) and not isinstance(budget.get(key), bool)}
    return {'ready': health.get('ready') is True,
            'stocks': {'status': status(stocks.get('status')), 'instruments': instruments,
                       'valid_until': _time(stocks.get('valid_until')),
                       'error_present': bool(stocks.get('error'))},
            'vix': {'status': status(vix.get('status')), 'latest_at': _time(vix.get('latest_at')),
                    'valid_until': _time(vix.get('valid_until')), 'bar_count': vix.get('bar_count'),
                    'error_present': bool(vix.get('error')), 'verification': public_verification}}


def build_decision_trace(setup, markets, vix, now, *, data_health=None, live_permission=None,
                         execution_available=False, broker_clock=None, account_at=None):
    """Describe the supplied analyzer result, without rerunning or modifying it.

    first_blocker is explicitly scoped to the signal analyzer. Passing it does
    not establish broker readiness; that independent admission is not evaluated.
    """
    setup = setup or {}
    checks = [{'name': c['name'], 'passed': c.get('passed') is True, 'detail': c.get('detail')}
              for c in setup.get('checks', []) if c.get('name') in CHECK_NAMES]
    first = next((deepcopy(c) for c in checks if not c['passed']), None)
    if not setup:
        first = {'name': 'Current Nasdaq observation', 'passed': False,
                 'detail': 'No validated Nasdaq market supplied to the analyzer'}
    signal_qualifies = bool(setup.get('state') == 'SETUP_READY' and setup.get('checks') and
                            all(c.get('passed') is True for c in setup['checks']))
    if first is not None:
        first['stage'] = 'signal'
    event_at = timestamp(setup['event_origin_at']) if setup.get('event_origin_at') else None
    leaders = deepcopy(setup.get('leader_evidence')) or leader_diagnostics(markets, now, setup_at=event_at)
    for symbol, row in leaders.items():
        row['input'] = _market(markets.get(symbol), now, (5,))
        row['age_at_observation_minutes'] = ((now - timestamp(row['reaction_at'])).total_seconds() / 60
                                             if row.get('reaction_at') else None)
    # The same persistent-reaction builder the analyzer used; discarded
    # reactions keep their status so an absent gate is explained, not inferred.
    vix_evidence = vix_diagnostics(vix, setup.get('direction'), now)
    reactions = [{'direction': r['direction'], 'at': r['at'].isoformat(), 'age_minutes': r['age'],
                  'status': r['status'], 'zone': _zone(r['zone'])} for r in vix_evidence['reactions']]
    qqq = markets.get('QQQ')
    # Five-minute checkpoints retain missing/stalled input evidence at the leader timeframe.
    checkpoint = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0)
    clock = broker_clock or {}
    trace = {'version': VERSION, 'captured_at': now.isoformat(), 'checkpoint_at': checkpoint.isoformat(),
             'setup': {key: deepcopy(setup.get(key)) for key in SETUP_KEYS}, 'checks': checks,
             'strategies': [],
             'candidate_diagnostics': [
                 {**{key: deepcopy(candidate.get(key)) for key in
                     ('id', 'event_id', 'event_origin_at', 'event_at', 'retest_at', 'event_expires_at', 'state', 'direction')},
                  'checks': [{key: check.get(key) for key in ('name', 'passed', 'detail')}
                             for check in candidate.get('checks', []) if check.get('name') in CHECK_NAMES]}
                 for candidate in setup.get('candidate_diagnostics', [])],
             'entry_candidates': [{key: deepcopy(candidate.get(key)) for key in SETUP_KEYS}
                                  for candidate in setup.get('entry_candidates', [])],
             'signal_qualifies': signal_qualifies, 'first_blocker': first,
             'market_context': market_context(qqq, now),
             'nasdaq': {'input': _market(qqq, now, (15, 60, 240, 1440)),
                       'eligible_zones': [{key: z.get(key) for key in (*ZONE_KEYS, 'source')}
                                          for z in setup.get('levels', [])]},
             'leaders': leaders,
             'leader_counts': {direction: sum(row.get('vote') == direction for row in leaders.values())
                               for direction in ('long', 'short')},
             'vix': {'input': _market(vix, now, (15,)), 'fresh': vix_evidence['fresh'],
                     'expected_reaction': vix_evidence['expected_reaction'],
                     'zones': [_zone(z) for z in vix_evidence['zones']], 'reactions': reactions,
                     'reason': vix_evidence['reason'],
                     'reaction_at': _time(vix_evidence['reaction_at']),
                     'zone': _zone(vix_evidence['zone']) if vix_evidence['zone'] else None,
                     'age_minutes': vix_evidence['age_minutes'],
                     'persistence_bars': vix_evidence['persistence_bars'],
                     'zone_tolerance': vix_evidence['zone_tolerance'],
                     'base_bars': vix_evidence['base_bars'], 'base_range': vix_evidence['base_range'],
                     'gate_reached': any(c['name'] == 'Actual VIX zone reaction' for c in checks)},
             'data_health': _health(data_health),
             'execution': {'live_money_enabled': live_permission if isinstance(live_permission, bool) else None,
                           'available': execution_available is True, 'broker_admission_evaluated': False,
                           'order_authorized_by_trace': False, 'account_observed_at': _time(account_at),
                           'regular_session_open': clock.get('is_open') if isinstance(clock.get('is_open'), bool) else None,
                           'clock_at': _time(clock.get('timestamp'))}}
    for method in setup.get('strategies', []):
        method_checks = [{'name': c['name'], 'passed': c.get('passed') is True, 'detail': c.get('detail')}
                         for c in method.get('checks', []) if c.get('name') in CHECK_NAMES]
        trace['strategies'].append({**{key: deepcopy(method.get(key)) for key in (*SETUP_KEYS, 'id', 'label')},
            'checks': method_checks,
            'first_blocker': next((deepcopy(c) for c in method_checks if not c['passed']), None),
            'signal_qualifies': bool(method.get('state') == 'SETUP_READY' and method_checks
                                     and all(c['passed'] for c in method_checks)),
            'leader_evidence': _leader_summary(method.get('leader_evidence') or {}),
            'order_authorized_by_trace': False})
    # Fail locally and visibly if any unexpected unserializable/nonfinite value enters evidence.
    json.dumps(trace, allow_nan=False)
    return trace


def evidence_fingerprint(trace):
    """Ignore poll-clock changes, retaining source candle times and all decisions.

    Stored body is refreshed on every observation, so newest source receipt times
    remain visible even when the same candle/evidence state is counted once.
    """
    ignored = {'captured_at', 'observed_at', 'as_of', 'age_at_observation_minutes', 'account_observed_at', 'clock_at'}
    receipt_deadline_paths = {('data_health', 'stocks'), ('data_health', 'stocks', 'instruments', '[]')}
    def stable(value, path=()):
        if isinstance(value, dict):
            return {key: stable(item, (*path, key)) for key, item in value.items()
                    if key not in ignored and not (key == 'valid_until' and path in receipt_deadline_paths)}
        if isinstance(value, list):
            return [stable(item, (*path, '[]')) for item in value]
        return value
    return sha256(json.dumps(stable(trace), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
