"""Two independently evaluated Nasdaq location methods from the primary recordings.

Closed-bar geometry is an explicit interpretation; broker admission is checked separately.
No score, FVG, first-clip tape/VWAP rule, or ETF volatility fallback is imported.
"""
from datetime import timedelta, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from zoneinfo import ZoneInfo
from .models import Zone, MAG7, timestamp

ET = ZoneInfo('America/New_York')
ANALYSIS_VERSION = 'nasdaq-video-interpretation-v3'
EVENT_LIFETIME_MINUTES = 180
LEADER_MINUTES = 5
CANDLE_PUBLICATION_GRACE_SECONDS = 90


@dataclass(frozen=True)
class AnalysisPolicy:
    """Declared v2 interpretations, frozen before replay; not creator formulas."""
    zone_tolerance: float = 0.001
    persistence_bars: int = 3
    minimum_leaders: int = 5
    maximum_opposition: int = 1

    def __post_init__(self):
        if (isinstance(self.zone_tolerance, bool) or not isinstance(self.zone_tolerance, (float, int))
                or not isfinite(self.zone_tolerance)
                or any(isinstance(v, bool) or not isinstance(v, int)
                       for v in (self.persistence_bars, self.minimum_leaders, self.maximum_opposition))):
            raise ValueError('Invalid analysis policy')
        if not (0 < self.zone_tolerance <= 0.01 and self.persistence_bars in (1, 2, 3)
                and 1 <= self.minimum_leaders <= 7 and 0 <= self.maximum_opposition < self.minimum_leaders):
            raise ValueError('Invalid analysis policy')


BASELINE_POLICY = AnalysisPolicy()


def closed(market, minutes, now):
    bars = market.bars.get(minutes, [])
    # Duplicates, wrong frames, future observations, or out-of-order source data are invalid.
    if any(b.minutes != minutes for b in bars) or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        return []
    return [b for b in bars if b.end <= now]


def fresh(market, now, minutes, grace=CANDLE_PUBLICATION_GRACE_SECONDS):
    bars = closed(market, minutes, now)
    return bool(market.realtime and 0 <= (now - market.observed_at).total_seconds() <= 90
                and bars and 0 <= (now - bars[-1].end).total_seconds() <= minutes * 60 + grace)


def zones(bars, before, tolerance=0.001):
    """Confirmed 4h swing extremes, clustered only from information known before entry.

    Two nonadjacent touches are required. The right-hand swing bar must already
    be closed before the zone can exist, preventing retrospective pivot detection.
    """
    history = [b for b in bars if b.end < before]
    points = []
    for i in range(1, len(history) - 1):
        a, b, c = history[i - 1:i + 2]
        if b.high > a.high and b.high >= c.high:
            points.append((b.high, c.end, i))
        if b.low < a.low and b.low <= c.low:
            points.append((b.low, c.end, i))
    groups = []
    for price, at, idx in sorted(points):
        group = next((g for g in groups if abs(price / g[0][0] - 1) <= tolerance * 2), None)
        if group is None:
            groups.append([(price, at, idx)])
        elif all(abs(idx - point[2]) >= 2 for point in group):
            group.append((price, at, idx))
    return [Zone(min(p[0] for p in g) * (1 - tolerance), max(p[0] for p in g) * (1 + tolerance),
                 max(p[1] for p in g), '4h repeated pivot', len(g)) for g in groups if len(g) >= 2]


def interaction_zones(bars, before, tolerance=0.001):
    """Fixed historical anchors with repeated nonadjacent touches or crossings.

    Each closed bar introduces its low/high as candidates. First chronological
    anchor wins overlapping bands; bounds never move as later evidence arrives.
    A second interaction at least two bar indices later establishes the area.
    Both wick touches and body crossings count, without requiring a swing.
    This is an explicit approximation of manually marked areas, not a recovered
    supply/demand indicator. Nasdaq uses 4h inputs; leaders use actual 5m inputs.
    """
    if not bars or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        return []
    minutes = bars[0].minutes
    if any(b.minutes != minutes for b in bars):
        return []
    history = [b for b in bars if b.end < before]
    anchors = []
    for index, bar in enumerate(history):
        for anchor in anchors:
            if (index - anchor['last_index'] >= 2
                    and bar.low <= anchor['high'] and bar.high >= anchor['low']):
                anchor['touches'] += 1
                anchor['last_index'] = index
                if anchor['established_at'] is None:
                    anchor['established_at'] = bar.end
        for price in (bar.low, bar.high):
            low, high = price * (1 - tolerance), price * (1 + tolerance)
            if any(low <= a['high'] and high >= a['low'] for a in anchors):
                continue
            anchors.append({'low': low, 'high': high, 'touches': 1,
                            'last_index': index, 'established_at': None})
    source = ('4h' if minutes == 240 else f'{minutes}m') + ' repeated interaction'
    return sorted((Zone(a['low'], a['high'], a['established_at'], source, a['touches'])
                   for a in anchors if a['established_at'] is not None),
                  key=lambda z: (z.low, z.high, z.established_at))


def prior_day_zones(market, now):
    today = now.astimezone(ET).date()
    days = [b for b in closed(market, 1440, now) if (b.end - timedelta(seconds=1)).astimezone(ET).date() < today]
    if not days or not market.previous_session:
        return []
    previous = days[-1]
    if previous.end.astimezone(ET).date().isoformat() != market.previous_session:
        return []
    # The provider supplies trading-session daily bars; weekends are not synthetic sessions.
    return [Zone(previous.high, previous.high, previous.end, 'previous-day high'),
            Zone(previous.low, previous.low, previous.end, 'previous-day low')]


def reaction(previous, current, zone):
    if zone.established_at >= current.end - timedelta(minutes=current.minutes):
        return None
    if current.low <= zone.high and current.high >= zone.low:
        if current.close > zone.high and current.close > current.open and current.close > previous.close:
            return 'long'
        if current.close < zone.low and current.close < current.open and current.close < previous.close:
            return 'short'
    return None


def leader_diagnostics(leaders, now, policy=BASELINE_POLICY, setup_at=None):
    """Five-minute location/rejection plus bounded, still-valid follow-through.

    Without a setup origin these are observations only. An admitted branch
    supplies its originating break time so unrelated earlier reactions cannot
    accumulate. No active/recent reaction is inferred from fifteen-minute bars.
    """
    rows = {}
    for symbol in MAG7:
        market = leaders.get(symbol)
        row = {'vote': None, 'reason': 'current 5-minute data missing', 'zones': [],
               'timeframe_minutes': LEADER_MINUTES, 'observational_only': setup_at is None,
               'latest_bar_at': None, 'reaction_at': None, 'age_minutes': None,
               'evidence_valid_until': None, 'observation_valid_until': None,
               'conflicting_reactions': False}
        rows[symbol] = row
        if market is None or not fresh(market, now, LEADER_MINUTES):
            continue
        bars = closed(market, LEADER_MINUTES, now)
        latest = bars[-1]
        observation_deadline = latest.end + timedelta(
            minutes=LEADER_MINUTES, seconds=CANDLE_PUBLICATION_GRACE_SECONDS)
        row.update(latest_bar_at=latest.end.isoformat(), latest_close=latest.close,
                   observation_valid_until=observation_deadline.isoformat())
        # Admission deadlines are exclusive even at the publication boundary.
        # A recently fetched receipt cannot extend an old candle's validity.
        if now >= observation_deadline:
            continue
        levels = interaction_zones(bars, latest.end - timedelta(minutes=LEADER_MINUTES), policy.zone_tolerance)
        row.update(reason='no zone reaction' if levels else 'no eligible zones',
                   zones=[{'low': z.low, 'high': z.high, 'source': z.source,
                           'established_at': z.established_at.isoformat(), 'touches': z.touches,
                           'touched_by_latest_bar': latest.low <= z.high and latest.high >= z.low}
                          for z in levels])
        if len(bars) < 2:
            continue
        evidence = []
        discarded = []
        # Scan independently per area. Otherwise iteration order could hide an
        # opposing reaction at a different area on a still-active setup.
        for zone in levels:
            for index in range(len(bars)-1, 0, -1):
                current, previous = bars[index], bars[index-1]
                observed_age = (now - current.end).total_seconds() / 60
                if observed_age >= LEADER_MINUTES * policy.persistence_bars:
                    break
                if (current.end.astimezone(ET).date() != now.astimezone(ET).date()
                        or (setup_at is not None and current.end < setup_at)):
                    continue
                if current.end - previous.end != timedelta(minutes=LEADER_MINUTES):
                    continue
                vote = reaction(previous, current, zone)
                if vote is None:
                    continue
                tail = bars[index:]
                gap = any(b.end - a.end != timedelta(minutes=LEADER_MINUTES) for a, b in zip(tail, tail[1:]))
                invalidated = any(b.close < zone.low if vote == 'long' else b.close > zone.high
                                  for b in bars[index+1:])
                continuing = latest.close >= current.close if vote == 'long' else latest.close <= current.close
                item = {'vote': vote, 'at': current.end,
                        'age': (latest.end - current.end).total_seconds() / 60, 'zone': zone}
                if gap or invalidated or not continuing:
                    reason = ('intervening 5-minute candle missing' if gap else
                              'invalidated by subsequent close through zone' if invalidated else
                              'reaction has no current directional follow-through')
                    discarded.append((item, reason))
                else:
                    evidence.append(item)
                # A newer reaction at this area replaces older evidence there.
                break
        directions = {item['vote'] for item in evidence}
        if len(directions) > 1:
            row.update(reason='conflicting active area reactions', conflicting_reactions=True)
            continue
        if evidence:
            # Most recent evidence first; nearest area breaks same-time ties.
            chosen = min(evidence, key=lambda e: (-e['at'].timestamp(), abs(latest.close-e['zone'].mid), e['zone'].low))
            zone = chosen['zone']
            row.update(vote=chosen['vote'], reaction_at=chosen['at'].isoformat(), age_minutes=chosen['age'],
                       evidence_valid_until=(chosen['at'] + timedelta(
                           minutes=LEADER_MINUTES * policy.persistence_bars)).isoformat(),
                       reason='current reaction' if chosen['at'] == latest.end else 'persistent reaction',
                       reaction_zone={'low': zone.low, 'high': zone.high, 'source': zone.source,
                                      'established_at': zone.established_at.isoformat()})
        elif discarded:
            chosen, reason = max(discarded, key=lambda item: item[0]['at'])
            row.update(reason=reason, reaction_at=chosen['at'].isoformat(), age_minutes=chosen['age'])
    return rows


def _leader_observation_at(rows):
    """Common current behavior observation; reaction times need not match."""
    # Provider publication can reach symbols at different times within the
    # freshness allowance. Compare their current behavior at one candle close;
    # their qualifying reactions may still occur on different earlier candles.
    try:
        observation_times = {timestamp(row['latest_bar_at']) for row in rows.values()}
    except (KeyError, TypeError, ValueError):
        return None
    if set(rows) != set(MAG7) or len(observation_times) != 1:
        return None
    return next(iter(observation_times)).astimezone(timezone.utc).isoformat()


def _leader_confirmation(rows, direction, policy):
    for symbol, row in rows.items():
        if row['reason'] == 'current 5-minute data missing' or row.get('conflicting_reactions'):
            return False, f'{symbol}: {row["reason"]}'
    if _leader_observation_at(rows) is None:
        return False, 'Leader 5-minute candles are not synchronized; waiting for a common completed timestamp'
    votes = {symbol: row['vote'] for symbol, row in rows.items()}
    opposite = 'short' if direction == 'long' else 'long'
    okay = (sum(v == direction for v in votes.values()) >= policy.minimum_leaders
            and sum(v == opposite for v in votes.values()) <= policy.maximum_opposition)
    return okay, ', '.join(f'{symbol}: {vote or "no zone reaction"}' for symbol, vote in votes.items())


def leader_confirmation(leaders, direction, now, policy=BASELINE_POLICY, setup_at=None):
    return _leader_confirmation(leader_diagnostics(leaders, now, policy, setup_at), direction, policy)


def vix_confirmation(vix, direction, now):
    if not vix_candles_fresh(vix, now):
        return False, 'Fresh actual VIX index data required; volatility ETFs and delayed data are not accepted'
    bars = closed(vix, 15, now)
    if len(bars) < 3:
        return False, 'VIX history is incomplete'
    levels = zones(bars[:-2], bars[-1].end - timedelta(minutes=15))
    expected = 'short' if direction == 'long' else 'long'
    okay = any(reaction(bars[-2], bars[-1], z) == expected for z in levels)
    return okay, 'VIX must rise from demand for a short, or fall from supply for a long'


def vix_candles_fresh(vix, now):
    """Closed history stays usable until the next candle is due, without re-dating it.

    InsightSentry's source quote is checked separately immediately before entry.
    Other markets retain the existing 90-second observation check.
    """
    if vix is None or vix.symbol != 'I:VIX' or not vix.realtime:
        return False
    if vix.source == 'massive_indices':
        return fresh(vix, now, 15)
    if vix.source != 'insightsentry' or vix.valid_until is None:
        return False
    bars = closed(vix, 15, now)
    return bool(bars and 0 <= (now - vix.observed_at).total_seconds() <= 990
                and now <= vix.valid_until
                and 0 <= (now - bars[-1].end).total_seconds() <= 990)



def _event_identity(method, zone, origin):
    # Retest time, current leader direction, and order price cannot create a
    # second identity for the same initiating location event.
    payload = ['QQQ', method, float(zone.low).hex(), float(zone.high).hex(),
               zone.established_at.astimezone(timezone.utc).isoformat(),
               origin.end.astimezone(timezone.utc).isoformat()]
    return 'ev2_' + sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


def location_events(bars, levels, method, now):
    """Latest bounded state per pre-existing area, reconstructed without writes.

    Break/retest: first closed crossing starts the event; a later hourly
    directional retest confirms it. A close through the opposite zone boundary
    invalidates it. Prior-day sweep: no reclaim is invented; a later close
    through the sweep candle's opposite extreme invalidates the location.
    Both expire 180 minutes after origin, on session change, or an hourly gap.
    A fresh crossing is required to create a new event after invalidation/expiry.
    """
    if (method not in ('four_hour_retest', 'prior_day_sweep') or len(bars) < 2
            or any(b.minutes != 60 for b in bars)
            or any(a.end >= b.end for a, b in zip(bars, bars[1:]))):
        return []
    bars = [b for b in bars if b.end <= now]
    events = []
    day = now.astimezone(ET).date()
    for zone in levels:
        active = None
        for previous, current in zip(bars, bars[1:]):
            if current.end.astimezone(ET).date() != day:
                continue
            start = current.end - timedelta(minutes=60)
            if zone.established_at >= start:
                continue
            contiguous = (current.end - previous.end == timedelta(minutes=60)
                          or previous.end.astimezone(ET).date() != day)
            if active is not None and active['state'] not in ('EXPIRED', 'INVALIDATED'):
                if current.end >= active['expires_at']:
                    active.update(state='EXPIRED', reason='The originating event reached its 180-minute limit')
                elif not contiguous:
                    active.update(state='INVALIDATED', reason='An intervening hourly candle is missing')
                else:
                    opposite_edge = (zone.low if active['break_direction'] == 'long' else zone.high)
                    if method == 'prior_day_sweep':
                        opposite_edge = (active['origin'].low if active['break_direction'] == 'long'
                                         else active['origin'].high)
                    invalid = (current.close < opposite_edge if active['break_direction'] == 'long'
                               else current.close > opposite_edge)
                    if invalid:
                        active.update(state='INVALIDATED', reason='A later close crossed the event invalidation boundary')
                    elif active['confirmed'] is None:
                        retested = ((current.low <= zone.high and current.close > zone.high and current.close > current.open)
                                    if active['break_direction'] == 'long' else
                                    (current.high >= zone.low and current.close < zone.low and current.close < current.open))
                        if retested:
                            active.update(confirmed=current, state='CONFIRMING', reason='Hourly break and retest confirmed')
            if active is not None and active['state'] not in ('EXPIRED', 'INVALIDATED'):
                continue
            if not contiguous:
                continue
            if method == 'four_hour_retest':
                direction = ('long' if previous.close <= zone.high < current.close else
                             'short' if previous.close >= zone.low > current.close else None)
            else:
                direction = ('long' if zone.source == 'previous-day high' and previous.high <= zone.high < current.high else
                             'short' if zone.source == 'previous-day low' and previous.low >= zone.low > current.low else None)
            if direction is None:
                continue
            confirmed = current if method == 'prior_day_sweep' else None
            active = {'id': _event_identity(method, zone, current), 'zone': zone, 'origin': current,
                      'confirmed': confirmed, 'break_direction': direction,
                      'expires_at': current.end + timedelta(minutes=EVENT_LIFETIME_MINUTES),
                      'state': 'CONFIRMING' if confirmed else 'WAITING_FOR_RETEST',
                      'reason': 'Previous-day boundary swept' if confirmed else 'Break observed; waiting for the hourly retest'}
        if active is not None:
            if now >= active['expires_at']:
                active.update(state='EXPIRED', reason='The originating event reached its 180-minute limit')
            events.append(active)
    return events


def _zone_dict(zone):
    return {'low': zone.low, 'high': zone.high, 'source': zone.source,
            'established_at': zone.established_at.isoformat()}


def _empty_result(method, label):
    return {'id': method, 'label': label, 'state': 'WATCHING', 'can_enter': False,
            'checks': [], 'levels': [], 'direction': None, 'entry': None, 'stop': None, 'target': None,
            'event': None, 'event_at': None, 'event_id': None, 'event_origin_at': None,
            'event_expires_at': None, 'latest_evidence_at': None,
            'leader_observation_at': None, 'leader_observations_synchronized': False,
            'leader_evidence_valid_until': None, 'leader_observation_valid_until': None,
            'policy_version': ANALYSIS_VERSION, 'execution_blocker': 'Waiting for the complete strategy setup'}


def _checked(result, name, passed, detail):
    result['checks'].append({'name': name, 'passed': bool(passed), 'detail': detail})
    return bool(passed)


def _finish(result):
    result['blockers'] = [c['name'] for c in result['checks'] if not c['passed']]
    return result


def _evaluate_event(base, event, all_levels, bars, leaders, vix, now, policy):
    result = {**base, 'checks': list(base['checks'])}
    origin, zone, confirmed = event['origin'], event['zone'], event['confirmed']
    result.update(state=event['state'], event_id=event['id'], event_origin_at=origin.end.isoformat(),
                  event_expires_at=event['expires_at'].isoformat(),
                  event_at=confirmed.end.isoformat() if confirmed else None,
                  event='break and retest' if result['id'] == 'four_hour_retest' else 'previous-day level sweep',
                  event_zone=_zone_dict(zone), latest_evidence_at=bars[-1].end.isoformat(),
                  event_invalidation_price=(zone.low if event['break_direction'] == 'long' else zone.high)
                  if result['id'] == 'four_hour_retest' else
                  (origin.low if event['break_direction'] == 'long' else origin.high))
    result['leader_evidence'] = leader_diagnostics(leaders, now, policy, origin.end)
    rows = result['leader_evidence']
    observation_at = _leader_observation_at(rows)
    # Reaction lifetime and the latest candle's publication allowance are
    # separate clocks. The executor must respect the earlier deadline between
    # polls, even when the original reaction itself remains within its window.
    evidence_deadlines = [timestamp(row['evidence_valid_until']) for row in rows.values() if row['vote']]
    observation_deadline = (timestamp(observation_at) + timedelta(
        minutes=LEADER_MINUTES, seconds=CANDLE_PUBLICATION_GRACE_SECONDS)) if observation_at else None
    result.update(leader_observation_at=observation_at,
                  leader_observations_synchronized=observation_at is not None,
                  leader_observation_valid_until=observation_deadline.isoformat() if observation_deadline else None,
                  leader_evidence_valid_until=min(evidence_deadlines).isoformat() if evidence_deadlines else None)
    if not _checked(result, 'Nasdaq level event', event['state'] == 'CONFIRMING', event['reason']):
        return _finish(result)
    confirmations = {d: _leader_confirmation(rows, d, policy) for d in ('long', 'short')}
    direction = next((d for d, (okay, _) in confirmations.items() if okay), None)
    if not _checked(result, 'Magnificent Seven at their zones', direction,
                    confirmations[direction][1] if direction else confirmations['long'][1]):
        return _finish(result)
    result['direction'] = direction
    okay, detail = vix_confirmation(vix, direction, now)
    _checked(result, 'Actual VIX zone reaction', okay, detail)
    # Location direction is independent of trade direction, as in V3's short
    # after an upward break. The exit policy remains an app interpretation.
    current = bars[-1]
    result['entry'] = current.close
    before_origin = origin.end - timedelta(minutes=60)
    targets = [z.low for z in all_levels if z.established_at < before_origin and z.low > current.close] if direction == 'long' else [
        z.high for z in all_levels if z.established_at < before_origin and z.high < current.close]
    target = (min(targets) if direction == 'long' else max(targets)) if targets else None
    stop = (min(zone.low, origin.low, confirmed.low, current.low) - .01 if direction == 'long' else
            max(zone.high, origin.high, confirmed.high, current.high) + .01)
    stop = round(stop, 2)
    target = round(target, 2) if target is not None else None
    geometry = target is not None and (stop < current.close < target if direction == 'long' else target < current.close < stop)
    _checked(result, 'Stop and target', geometry,
             'Execution interpretation: stop beyond event/zone; target nearest pre-existing 4-hour or previous-day level')
    result.update(stop=stop, target=target)
    if all(c['passed'] for c in result['checks']):
        result.update(state='SETUP_READY', execution_blocker='Owner permission and current broker checks still required')
    return _finish(result)


def _rank(result):
    order = {'SETUP_READY': 6, 'CONFIRMING': 5, 'WAITING_FOR_RETEST': 4,
             'AT_LEVEL': 3, 'WATCHING': 2, 'INVALIDATED': 1, 'EXPIRED': 0}
    return (order.get(result['state'], 0), sum(c['passed'] for c in result['checks']),
            result.get('event_origin_at') or '', result.get('event_id') or '')


def analyze(market, leaders, vix, now, policy=BASELINE_POLICY):
    """Independently evaluate the two source location methods; never authorize orders.

    Five-minute leaders, fixed interaction areas, and bounded event/reaction
    persistence are declared v2 interpretations. QQQ completed-hour sampling
    and unchanged actual-index fifteen-minute VIX confirmation remain explicit.
    """
    methods = [('four_hour_retest', '4-hour areas / hourly break and retest'),
               ('prior_day_sweep', 'Previous-day boundary sweep')]
    rows = []
    qualified = []
    valid = market is not None and market.symbol == 'QQQ' and fresh(market, now, 60)
    bars = closed(market, 60, now) if valid else []
    all_levels = []
    if valid:
        levels = interaction_zones(closed(market, 240, now), bars[-1].end, policy.zone_tolerance)
        previous = prior_day_zones(market, now)
        all_levels = levels + previous
    for method, label in methods:
        result = _empty_result(method, label)
        if not _checked(result, 'Current Nasdaq observation', valid,
                        'QQQ Nasdaq ETF proxy; fresh completed 1-hour candles required'):
            rows.append(_finish(result))
            continue
        method_levels = levels if method == 'four_hour_retest' else previous
        result.update(levels=[_zone_dict(z) for z in method_levels], latest_evidence_at=bars[-1].end.isoformat())
        if not _checked(result, 'Premarked levels', method_levels,
                        'Repeated historical 4-hour interactions' if method == 'four_hour_retest' else 'Verified previous-session high and low'):
            rows.append(_finish(result))
            continue
        events = location_events(bars, method_levels, method, now)
        if events:
            candidates = [_evaluate_event(result, event, all_levels, bars, leaders, vix, now, policy) for event in events]
            qualified.extend(candidate for candidate in candidates if candidate['state'] == 'SETUP_READY')
            result = max(candidates, key=_rank)
            result['candidate_count'] = len(candidates)
        else:
            touched = any(bars[-1].low <= z.high and bars[-1].high >= z.low for z in method_levels)
            result['state'] = 'AT_LEVEL' if touched else 'WATCHING'
            _checked(result, 'Nasdaq level event', False,
                     'Wait for a closed-hour break and retest at a pre-existing 4-hour area' if method == 'four_hour_retest' else
                     'Wait for a fresh sweep of the previous-day high or low')
            _finish(result)
        rows.append(result)
    selected = max(rows, key=_rank)
    result = {key: value for key, value in selected.items() if key not in ('id', 'label')}
    result.update(strategy_id=selected['id'], strategies=rows, levels=[_zone_dict(z) for z in all_levels],
                  leader_rule={'minimum_agree': policy.minimum_leaders,
                               'maximum_opposing': policy.maximum_opposition,
                               'persistence_minutes': LEADER_MINUTES * policy.persistence_bars})
    # Preserve every qualified opportunity for admission. A previously handled
    # event must not hide an unhandled area or the other independently valid
    # method. Only the executor knows durable consumption; analysis stays pure.
    result['entry_candidates'] = [
        {**{key: value for key, value in candidate.items()
            if key not in ('id', 'label', 'leader_evidence', 'levels', 'candidate_count')},
         'strategy_id': candidate['id']}
        for candidate in sorted(qualified, key=_rank, reverse=True)]
    if result['state'] in ('EXPIRED', 'INVALIDATED'):
        result['state'] = 'WATCHING'
    return result
