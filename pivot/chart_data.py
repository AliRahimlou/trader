"""Look-left chart data for the Socrates view: a pure translation of analysis inputs.

Video rule (18:11 recording): the creator marks a handful of four-hour
swing levels by hand where price "broke through or touched more than once",
keeps the same lines on the one-hour chart, trades the retest of a break, and
"looks left" to confirm each line against five to six weeks of history; VIX
is read the same way on fifteen-minute candles. App interpretation: the
areas drawn here are the app's mechanical repeated-interaction bands with
their touch counts, its previous-day levels, the hourly events it is
tracking, and its VIX swing clusters and consolidation bases, so the owner
can compare them with the
lines he would draw. Nothing here authorizes or changes an order; it is
display data only, bounded in size and JSON-safe, and it degrades to empty
collections whenever an input field is missing.
"""
from datetime import datetime, timedelta, timezone
from math import isfinite

from .models import MAG7
from .strategy import BASELINE_POLICY, ET, VIX_MINUTES, leader_diagnostics, vix_areas

HOURLY_SESSIONS = 5
FOUR_HOUR_BARS = 30
VIX_SESSIONS = 3
LEADER_ZONES = 6
# Hard caps keep one response under ~600 bars even if a provider over-delivers.
BAR_LIMITS = {'60': 200, '240': FOUR_HOUR_BARS, 'vix': 120}


def _iso(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except ValueError:
            return None
    return None


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _field(source, name):
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _session_date(end):
    return (end - timedelta(seconds=1)).astimezone(ET).date().isoformat()


def _bars(market, minutes):
    """Validated, chronologically sorted Bar objects for one frame, or []."""
    frames = _field(market, 'bars') if market is not None else None
    rows = frames.get(minutes) if isinstance(frames, dict) else None
    if not isinstance(rows, (list, tuple)):
        return []
    valid = []
    for bar in rows:
        end = _field(bar, 'end')
        prices = [_number(_field(bar, name)) for name in ('open', 'high', 'low', 'close')]
        if isinstance(end, datetime) and end.tzinfo is not None and None not in prices:
            valid.append(bar)
    return sorted(valid, key=lambda bar: bar.end)


def _bar_dict(bar):
    return {'t': _iso(bar.end), 'o': float(bar.open), 'h': float(bar.high),
            'l': float(bar.low), 'c': float(bar.close)}


def _last_sessions(bars, count, limit):
    """Bars from the latest ``count`` session dates present, newest-limited."""
    dates = sorted({_session_date(bar.end) for bar in bars})[-count:]
    chosen = [bar for bar in bars if _session_date(bar.end) in dates]
    return chosen[-limit:], dates


def _level(value):
    low, high = _number(_field(value, 'low')), _number(_field(value, 'high'))
    if low is None or high is None or low <= 0 or low > high:
        return None
    touches = _field(value, 'touches')
    return {'low': low, 'high': high, 'source': str(_field(value, 'source') or 'area'),
            'established_at': _iso(_field(value, 'established_at')),
            'touches': touches if isinstance(touches, int) and not isinstance(touches, bool) and touches >= 0 else None}


def _levels(values):
    rows = [_level(value) for value in values] if isinstance(values, (list, tuple)) else []
    return sorted((row for row in rows if row is not None), key=lambda row: (row['low'], row['high']))


def _previous_day(levels, market, at):
    """Previous-day high/low from the analysis levels, else from daily bars."""
    highs = [row['high'] for row in levels if row['source'] == 'previous-day high']
    lows = [row['low'] for row in levels if row['source'] == 'previous-day low']
    if highs and lows:
        return {'high': highs[-1], 'low': lows[-1]}
    today = at.astimezone(ET).date()
    days = [bar for bar in _bars(market, 1440) if (bar.end - timedelta(seconds=1)).astimezone(ET).date() < today]
    if not days:
        return None
    return {'high': float(days[-1].high), 'low': float(days[-1].low), 'source': 'daily bar'}


def _events(setup, levels):
    rows = setup.get('strategies') if isinstance(setup.get('strategies'), list) else [setup]
    selected = setup.get('strategy_id')
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        zone = _level(row.get('event_zone'))
        if zone is None:
            continue
        if zone['touches'] is None:
            match = next((lvl for lvl in levels if lvl['low'] == zone['low'] and lvl['high'] == zone['high']), None)
            if match is not None:
                zone['touches'] = match['touches']
        method = row.get('id') or selected
        events.append({'method': method, 'label': row.get('label'), 'state': str(row.get('state') or 'WATCHING'),
                       'zone': zone, 'origin_at': _iso(row.get('event_origin_at')),
                       'retest_at': _iso(row.get('event_at')), 'expires_at': _iso(row.get('event_expires_at')),
                       'direction': row.get('direction') if row.get('direction') in ('long', 'short') else None,
                       'entry': _number(row.get('entry')), 'stop': _number(row.get('stop')),
                       'target': _number(row.get('target')), 'selected': method == selected})
    return events


def _vix(setup, market):
    bars = _bars(market, VIX_MINUTES)
    shown, dates = _last_sessions(bars, VIX_SESSIONS, BAR_LIMITS['vix'])
    zone_rows = setup.get('vix_zones')
    if isinstance(zone_rows, (list, tuple)):
        zone_list = _levels(zone_rows)
    elif len(bars) >= 3:
        # Same construction as the analysis (strategy.vix_diagnostics): swing
        # clusters and consolidation bases known before the two latest
        # candles, at the declared VIX tolerance and base rule (app interpretation).
        zone_list = _levels(vix_areas(bars[:-2], bars[-1].end - timedelta(minutes=VIX_MINUTES), BASELINE_POLICY))
    else:
        zone_list = []
    direction = setup.get('direction')
    reaction_zone = _level(setup.get('vix_zone'))
    reaction = ({'zone': reaction_zone, 'at': _iso(setup.get('vix_reaction_at')),
                 'direction': {'long': 'short', 'short': 'long'}.get(direction)}
                if reaction_zone is not None else None)
    return {'bars15': [_bar_dict(bar) for bar in shown], 'sessions': dates, 'zones': zone_list, 'reaction': reaction}


def _leaders(setup, markets, at):
    evidence = setup.get('leader_evidence')
    if not isinstance(evidence, dict):
        try:
            evidence = leader_diagnostics(markets if isinstance(markets, dict) else {}, at)
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
            evidence = {}
    result = {}
    for symbol in MAG7:
        row = evidence.get(symbol) if isinstance(evidence.get(symbol), dict) else {}
        latest = _number(row.get('latest_close'))
        if latest is None:
            bars = _bars((markets or {}).get(symbol) if isinstance(markets, dict) else None, 5)
            latest = float(bars[-1].close) if bars else None
        zone_list = _levels(row.get('zones'))
        if latest is not None:
            zone_list.sort(key=lambda z: (max(z['low'] - latest, latest - z['high'], 0.0), z['low']))
        reaction_zone = _level(row.get('reaction_zone'))
        vote = row.get('vote') if row.get('vote') in ('long', 'short') else None
        result[symbol] = {'vote': vote, 'reason': str(row.get('reason') or 'no observation'),
                          'source': reaction_zone['source'] if reaction_zone else None,
                          'reaction_zone': reaction_zone, 'reaction_at': _iso(row.get('reaction_at')),
                          'latest_close': latest, 'zones': zone_list[:LEADER_ZONES]}
    return result


def build(inputs, symbol='QQQ'):
    """JSON-safe look-left payload from Service.chart_inputs(); never raises on missing fields."""
    inputs = inputs if isinstance(inputs, dict) else {}
    markets = inputs.get('markets') if isinstance(inputs.get('markets'), dict) else {}
    setup = inputs.get('setup') if isinstance(inputs.get('setup'), dict) else {}
    sessions = inputs.get('sessions') if isinstance(inputs.get('sessions'), dict) else {}
    market = markets.get(symbol)
    hourly_all, four_hour_all = _bars(market, 60), _bars(market, 240)
    at = inputs.get('at')
    if not isinstance(at, datetime) or at.tzinfo is None:
        ends = [bars[-1].end for bars in (hourly_all, four_hour_all) if bars]
        at = max(ends) if ends else datetime.now(timezone.utc)
    hourly, dates = _last_sessions(hourly_all, HOURLY_SESSIONS, BAR_LIMITS['60'])
    four_hour = four_hour_all[-BAR_LIMITS['240']:]
    levels = _levels(setup.get('levels'))
    session_rows = []
    for day in dates:
        session = sessions.get(day) if isinstance(sessions.get(day), dict) else {}
        session_rows.append({'date': day, 'open': _iso(session.get('open')), 'close': _iso(session.get('close'))})
    return {'available': True, 'symbol': symbol, 'at': _iso(at), 'sessions': session_rows,
            'reference': float(hourly[-1].close) if hourly else None,
            'bars': {'60': [_bar_dict(bar) for bar in hourly], '240': [_bar_dict(bar) for bar in four_hour]},
            'levels': levels, 'previous_day': _previous_day(levels, market, at),
            'events': _events(setup, levels), 'vix': _vix(setup, inputs.get('vix')),
            'leaders': _leaders(setup, markets, at),
            'notes': ['QQQ bands are the app\'s \u00b10.1% areas around confirmed 4-hour swing pivots with at least two '
                      'non-adjacent interactions (count shown); the video draws these lines by hand at repeated swing levels.',
                      f'VIX areas are 15-minute repeated swing pivots (\u00b1{BASELINE_POLICY.vix_zone_tolerance:.0%}) and '
                      f'consolidation bases (at least {BASELINE_POLICY.vix_base_bars} candles within '
                      f'{BASELINE_POLICY.vix_base_range:.0%}), known before the two latest candles.',
                      'Stop and target lines are app execution choices; the recordings show neither.']}
