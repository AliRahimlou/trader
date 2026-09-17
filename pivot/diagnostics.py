"""Observational decision evidence; no broker calls, permission changes or rules.

Only market evidence and explicit health fields are admitted. Provider errors,
account objects, credentials, quotes and raw responses are never copied.
"""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import json
from .models import MAG7, timestamp
from .strategy import closed, leader_diagnostics, reaction, vix_candles_fresh, zones

VERSION = 'decision-trace-v1'
SOURCES = {'alpaca_iex', 'alpaca_sip', 'insightsentry', 'massive_indices'}
SETUP_KEYS = ('state', 'direction', 'entry', 'stop', 'target', 'event', 'event_at', 'policy_version')
ZONE_KEYS = ('low', 'high', 'established_at', 'touches')
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
            'touches': zone.touches}


def _market(market, now, frames):
    if market is None:
        return {'source': None, 'observed_at': None, 'realtime': False, 'frames': {}}
    result = {'source': market.source if market.source in SOURCES else 'unrecognized',
              'observed_at': market.observed_at.isoformat(), 'realtime': market.realtime is True, 'frames': {}}
    for minutes in frames:
        bars = closed(market, minutes, now)
        result['frames'][str(minutes)] = {'count': len(bars),
            'latest_at': bars[-1].end.isoformat() if bars else None,
            'recent': [_bar(b) for b in bars[-2:]] if minutes in (15, 60) else []}
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
                  {key: _time(f.get(key)) for key in ('latest_at', 'expected_at', 'first_missing_at')} |
                  {'status': status(f.get('status'))} for f in row.get('frames', [])]
        instruments.append({'symbol': row['symbol'], 'status': status(row.get('status')), 'frames': frames})
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
    event_at = timestamp(setup['event_at']) if setup.get('event_at') else None
    leaders = leader_diagnostics(markets, now, setup_at=event_at)
    for symbol, row in leaders.items():
        row['input'] = _market(markets.get(symbol), now, (15, 240))
        row['age_at_observation_minutes'] = ((now - timestamp(row['reaction_at'])).total_seconds() / 60
                                             if row.get('reaction_at') else None)
    vix_bars = closed(vix, 15, now) if vix else []
    vix_fresh = vix_candles_fresh(vix, now)
    vix_zones = zones(vix_bars[:-2], vix_bars[-1].end - timedelta(minutes=15)) if len(vix_bars) >= 3 else []
    reactions = [{'direction': value, 'zone': _zone(zone)} for zone in vix_zones
                 if (value := reaction(vix_bars[-2], vix_bars[-1], zone))] if len(vix_bars) >= 3 else []
    expected = {'long': 'short', 'short': 'long'}.get(setup.get('direction'))
    vix_reason = ('fresh actual VIX data missing' if not vix_fresh else
                  'VIX history incomplete' if len(vix_bars) < 3 else
                  'no eligible zones' if not vix_zones else
                  'Nasdaq direction not selected; VIX gate not reached' if expected is None else
                  'expected reaction present' if any(r['direction'] == expected for r in reactions) else
                  'expected zone reaction absent')
    qqq = markets.get('QQQ')
    # Fifteen-minute wall-clock checkpoints also record missing/stalled inputs.
    checkpoint = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    clock = broker_clock or {}
    trace = {'version': VERSION, 'captured_at': now.isoformat(), 'checkpoint_at': checkpoint.isoformat(),
             'setup': {key: deepcopy(setup.get(key)) for key in SETUP_KEYS}, 'checks': checks,
             'signal_qualifies': signal_qualifies, 'first_blocker': first,
             'nasdaq': {'input': _market(qqq, now, (15, 60, 240, 1440)),
                       'eligible_zones': [{key: z.get(key) for key in (*ZONE_KEYS, 'source')}
                                          for z in setup.get('levels', [])]},
             'leaders': leaders,
             'leader_counts': {direction: sum(row.get('vote') == direction for row in leaders.values())
                               for direction in ('long', 'short')},
             'vix': {'input': _market(vix, now, (15,)), 'fresh': vix_fresh, 'expected_reaction': expected,
                     'zones': [_zone(z) for z in vix_zones], 'reactions': reactions, 'reason': vix_reason,
                     'gate_reached': any(c['name'] == 'Actual VIX zone reaction' for c in checks)},
             'data_health': _health(data_health),
             'execution': {'live_money_enabled': live_permission if isinstance(live_permission, bool) else None,
                           'available': execution_available is True, 'broker_admission_evaluated': False,
                           'order_authorized_by_trace': False, 'account_observed_at': _time(account_at),
                           'regular_session_open': clock.get('is_open') if isinstance(clock.get('is_open'), bool) else None,
                           'clock_at': _time(clock.get('timestamp'))}}
    # Fail locally and visibly if any unexpected unserializable/nonfinite value enters evidence.
    json.dumps(trace, allow_nan=False)
    return trace


def evidence_fingerprint(trace):
    """Ignore poll-clock changes, retaining source candle times and all decisions.

    Stored body is refreshed on every observation, so newest source receipt times
    remain visible even when the same candle/evidence state is counted once.
    """
    ignored = {'captured_at', 'observed_at', 'age_at_observation_minutes', 'account_observed_at', 'clock_at'}
    def stable(value):
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items() if key not in ignored}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    return sha256(json.dumps(stable(trace), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
