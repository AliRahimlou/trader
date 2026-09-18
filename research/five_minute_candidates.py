"""Prespecified F1/X1 research algorithms; no broker, network, or live controls.

These emit research observations, never the production execution contract.
Native resolution and authentic-data provenance must be validated by a dataset
loader. Synthetic fixtures can exercise mechanics but cannot establish an edge.
"""
from dataclasses import dataclass
from datetime import timedelta, timezone
from hashlib import sha256
import json

from pivot.feeds import ET
from pivot.models import Bar, Zone, timestamp


@dataclass(frozen=True)
class NativeFiveMinuteSeries:
    symbol: str
    bars: tuple[Bar, ...]
    observed_at: object
    source: str
    source_symbol: str
    native_resolution_minutes: int
    zero_delay_metadata_verified: bool
    coverage_verified: bool
    receipt_is_simulated: bool = True


def native_closed(series, now):
    now, observed = timestamp(now), timestamp(series.observed_at)
    expected = {'QQQ': ('QQQ', {'alpaca_iex', 'alpaca_sip'}),
                'I:VIX': ('CBOE:VIX', {'insightsentry', 'massive_indices'})}
    source_symbol, providers = expected.get(series.symbol, (None, set()))
    if (series.native_resolution_minutes != 5 or series.source_symbol != source_symbol
            or series.source not in providers or series.zero_delay_metadata_verified is not True
            or series.coverage_verified is not True):
        raise ValueError('Verified native five-minute QQQ or actual spot VIX input required')
    bars = list(series.bars)
    if (any(type(b.minutes) is not int or b.minutes != 5 for b in bars)
            or any(a.end >= b.end for a, b in zip(bars, bars[1:]))):
        raise ValueError('Expected ordered unique native five-minute candles')
    bars = [b for b in bars if b.end <= now]
    if not bars or not 0 <= (now - observed).total_seconds() < 90:
        raise ValueError('Current native observation receipt required')
    for a, b in zip(bars, bars[1:]):
        if a.end.astimezone(ET).date() == b.end.astimezone(ET).date() and b.end-a.end != timedelta(minutes=5):
            raise ValueError('Missing intervening native five-minute candle')
    deadline = min(observed + timedelta(seconds=90), bars[-1].end + timedelta(minutes=5, seconds=90))
    if now >= deadline:
        raise ValueError('Native five-minute observation expired')
    return bars, deadline


def _identity(method, zone, origin):
    body = ['research-f1-v1', method, float(zone.low).hex(), float(zone.high).hex(),
            zone.established_at.astimezone(timezone.utc).isoformat(), origin.end.astimezone(timezone.utc).isoformat()]
    return 'research_f1_' + sha256(json.dumps(body, separators=(',', ':')).encode()).hexdigest()


def five_minute_events(bars, levels, method, now):
    """Same declared location predicates as current methods, on native 5m bars."""
    if method not in ('four_hour_retest', 'prior_day_sweep'):
        raise ValueError('Unknown research location method')
    if any(b.minutes != 5 for b in bars) or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        raise ValueError('Events require ordered native five-minute bars')
    now = timestamp(now)
    bars = [b for b in bars if b.end <= now]
    events, day = [], now.astimezone(ET).date()
    for zone in levels:
        active = None
        for previous, current in zip(bars, bars[1:]):
            if current.end.astimezone(ET).date() != day or zone.established_at >= current.end-timedelta(minutes=5):
                continue
            contiguous = (current.end-previous.end == timedelta(minutes=5)
                          or previous.end.astimezone(ET).date() != day)
            if active and active['state'] not in ('EXPIRED', 'INVALIDATED'):
                if current.end >= active['expires_at']:
                    active['state'] = 'EXPIRED'
                elif not contiguous:
                    active['state'] = 'INVALIDATED'
                else:
                    upward = active['break_direction'] == 'long'
                    edge = (zone.low if upward else zone.high) if method == 'four_hour_retest' else (
                        active['origin'].low if upward else active['origin'].high)
                    if (current.close < edge if upward else current.close > edge):
                        active['state'] = 'INVALIDATED'
                    elif active['confirmed'] is None:
                        retest = ((current.low <= zone.high < current.close and current.close > current.open)
                                  if upward else (current.high >= zone.low > current.close and current.close < current.open))
                        if retest:
                            active.update(confirmed=current, state='CONFIRMING')
            if active and active['state'] not in ('EXPIRED', 'INVALIDATED'):
                continue
            if not contiguous:
                continue
            if method == 'four_hour_retest':
                direction = ('long' if previous.close <= zone.high < current.close else
                             'short' if previous.close >= zone.low > current.close else None)
            else:
                direction = ('long' if zone.source == 'previous-day high' and previous.high <= zone.high < current.high else
                             'short' if zone.source == 'previous-day low' and previous.low >= zone.low > current.low else None)
            if direction:
                active = {'id': _identity(method, zone, current), 'zone': zone, 'origin': current,
                          'break_direction': direction, 'confirmed': current if method == 'prior_day_sweep' else None,
                          'state': 'CONFIRMING' if method == 'prior_day_sweep' else 'WAITING_FOR_RETEST',
                          'expires_at': current.end+timedelta(minutes=180)}
        if active:
            if now >= active['expires_at']:
                active['state'] = 'EXPIRED'
            events.append(active)
    return events


def fast_location_candidates(engine, higher_market, leaders, qqq5, vix5, now, policy=None,
                             publication_delay_seconds=60):
    """F1 full candle qualification, with frozen baseline area/leader functions.

    A caller must pass the explicitly frozen engine used by its experiment.
    Outputs deliberately omit the production policy/permission contract.
    """
    if (qqq5.symbol != 'QQQ' or vix5.symbol != 'I:VIX' or higher_market.symbol != 'QQQ'
            or publication_delay_seconds < 0):
        raise ValueError('F1 requires QQQ and actual spot VIX')
    bars, qdeadline = native_closed(qqq5, now)
    vbars, vdeadline = native_closed(vix5, now)
    result = {'research_only': True, 'can_enter': False, 'version': 'research-f1-v1', 'candidates': []}
    known_at = max(bars[-1].end, vbars[-1].end)+timedelta(seconds=publication_delay_seconds)
    if timestamp(now) < known_at:
        return {**result, 'blocker': 'awaiting_publication_allowance', 'known_at': known_at.isoformat()}
    strategy = engine.strategy
    policy = policy or strategy.BASELINE_POLICY
    areas = strategy.interaction_zones(strategy.closed(higher_market, 240, now), bars[-1].end, policy.zone_tolerance)
    prior = strategy.prior_day_zones(higher_market, now)
    all_levels = areas + prior
    vlevels = strategy.zones(vbars[:-2], vbars[-1].end-timedelta(minutes=5)) if len(vbars) >= 3 else []
    for method, levels in [('four_hour_retest', areas), ('prior_day_sweep', prior)]:
        for event in five_minute_events(bars, levels, method, now):
            row = {'method': method, 'event_id': event['id'], 'state': event['state'], 'qualified': False,
                   'research_only': True, 'can_enter': False, 'direction': None}
            result['candidates'].append(row)
            if event['state'] != 'CONFIRMING':
                row['blocker'] = 'location_event'
                continue
            votes = strategy.leader_diagnostics(leaders, now, policy, event['origin'].end)
            direction = next((d for d in ('long', 'short')
                              if strategy._leader_confirmation(votes, d, policy)[0]), None)
            if direction is None:
                row['blocker'] = 'leaders'
                continue
            row['direction'] = direction
            inverse = 'short' if direction == 'long' else 'long'
            if not any(strategy.reaction(vbars[-2], vbars[-1], zone) == inverse for zone in vlevels):
                row['blocker'] = 'native_five_minute_vix_reaction'
                continue
            current, origin, confirmed, zone = bars[-1], event['origin'], event['confirmed'], event['zone']
            targets = [z.low for z in all_levels if z.established_at < origin.end-timedelta(minutes=5)
                       and z.low > current.close] if direction == 'long' else [
                           z.high for z in all_levels if z.established_at < origin.end-timedelta(minutes=5)
                           and z.high < current.close]
            target = (min(targets) if direction == 'long' else max(targets)) if targets else None
            stop = round(min(zone.low, origin.low, confirmed.low, current.low)-.01 if direction == 'long' else
                         max(zone.high, origin.high, confirmed.high, current.high)+.01, 2)
            target = round(target, 2) if target is not None else None
            valid_geometry = target is not None and (stop < current.close < target if direction == 'long' else
                                                     target < current.close < stop)
            deadlines = [event['expires_at'], qdeadline, vdeadline]
            deadlines.extend(timestamp(v[k]) for v in votes.values() for k in ('observation_valid_until',)
                             if v.get(k))
            deadlines.extend(timestamp(v['evidence_valid_until']) for v in votes.values() if v['vote'])
            row.update(qualified=valid_geometry, blocker=None if valid_geometry else 'exit_geometry',
                       entry=current.close, stop=stop, target=target, valid_until=min(deadlines).isoformat(),
                       event_origin_at=origin.end.isoformat())
    return result


@dataclass(frozen=True)
class VixExitAnchor:
    direction: str
    entered_at: object
    boundary: float
    zone: Zone


def freeze_vix_exit(engine, direction, vix5, entered_at):
    """X1 freezes a previously known opposing VIX boundary at entry."""
    if direction not in ('long', 'short') or vix5.symbol != 'I:VIX':
        raise ValueError('X1 requires a QQQ direction and native actual VIX')
    bars, _ = native_closed(vix5, entered_at)
    levels = engine.strategy.zones(bars[:-2], bars[-1].end-timedelta(minutes=5))
    eligible = [z for z in levels if z.low > bars[-1].close] if direction == 'short' else [
        z for z in levels if z.high < bars[-1].close]
    if not eligible:
        return None
    zone = min(eligible, key=lambda z: z.low) if direction == 'short' else max(eligible, key=lambda z: z.high)
    return VixExitAnchor(direction, timestamp(entered_at), zone.low if direction == 'short' else zone.high, zone)


def vix_exit_decision(anchor, vix5, now, publication_delay_seconds=60):
    """A completed-bar touch proposes a later exit; no intrabar price invented."""
    if publication_delay_seconds < 0 or vix5.symbol != 'I:VIX':
        raise ValueError('Nonnegative delay and native actual VIX required')
    bars, deadline = native_closed(vix5, now)
    entered = timestamp(anchor.entered_at)
    if timestamp(now).astimezone(ET).date() != entered.astimezone(ET).date():
        return None
    for bar in bars:
        # The entire observation bar must follow entry; earlier intrabar touches
        # cannot be credited to a position that had not yet been opened.
        start = bar.end-timedelta(minutes=5)
        if start < entered or (bar.high < anchor.boundary if anchor.direction == 'short' else bar.low > anchor.boundary):
            continue
        decision_at = bar.end+timedelta(seconds=publication_delay_seconds)
        if decision_at > timestamp(now):
            return None
        until = min(deadline, bar.end+timedelta(minutes=5, seconds=90))
        return {'research_only': True, 'can_enter': False, 'version': 'research-x1-v1',
                'at': decision_at.isoformat(), 'valid_until': until.isoformat(),
                'status': 'current' if timestamp(now) < until else 'expired',
                'vix_bar_at': bar.end.isoformat(), 'boundary': anchor.boundary,
                'rule': 'completed five-minute bar touches frozen opposing VIX boundary'}
    return None


def research_exit_reference(bar, direction, stop, target, vix_decision=None, latency_seconds=0):
    """Resolve one future native QQQ bar without fabricating within-bar fills.

    Gap stop has priority. A known, unexpired VIX exit fills at this observed
    open; otherwise both QQQ bounds touched means stop first. Costs are applied
    separately by the experiment, never claimed to be an actual broker fill.
    """
    if bar.minutes != 5 or direction not in ('long', 'short') or latency_seconds < 0:
        raise ValueError('Native five-minute bar, direction and nonnegative latency required')
    start = bar.end-timedelta(minutes=5)
    gap_stop = bar.open <= stop if direction == 'long' else bar.open >= stop
    if gap_stop:
        return {'at': start.isoformat(), 'price': bar.open, 'reason': 'gap_stop', 'ambiguous': False}
    if vix_decision and vix_decision['status'] == 'current':
        known = timestamp(vix_decision['at'])+timedelta(seconds=latency_seconds)
        if known <= start < timestamp(vix_decision['valid_until']):
            return {'at': start.isoformat(), 'price': bar.open, 'reason': 'vix_pivot', 'ambiguous': False}
    stop_hit = bar.low <= stop if direction == 'long' else bar.high >= stop
    target_hit = bar.high >= target if direction == 'long' else bar.low <= target
    if stop_hit or target_hit:
        return {'at': bar.end.isoformat(), 'price': stop if stop_hit else target,
                'reason': 'stop' if stop_hit else 'target', 'ambiguous': stop_hit and target_hit}
    return None
