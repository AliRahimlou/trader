"""One Nasdaq workflow from the two primary recordings.

Closed-bar geometry is an explicit interpretation; broker admission is checked separately.
No score, FVG, first-clip tape/VWAP rule, or ETF volatility fallback is imported.
"""
from datetime import timedelta
from dataclasses import dataclass
from math import isfinite
from zoneinfo import ZoneInfo
from .models import Zone, MAG7

ET = ZoneInfo('America/New_York')


@dataclass(frozen=True)
class AnalysisPolicy:
    """Explicit research parameters. The running app uses unchanged defaults."""
    zone_tolerance: float = 0.001
    persistence_bars: int = 1
    minimum_leaders: int = 4
    maximum_opposition: int = 0

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


def fresh(market, now, minutes, grace=90):
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


def pivot_event(bars, levels):
    if len(bars) < 3:
        return None
    retest = bars[-1]
    for zone in levels:
        # Retests can follow several candles. A close through the opposite side
        # invalidates a continuation; the latest bar must supply the reaction.
        for i in range(len(bars)-2, 0, -1):
            before, broken = bars[i-1:i+1]
            if zone.established_at >= broken.end-timedelta(minutes=broken.minutes):
                continue
            between = bars[i+1:-1]
            if before.close <= zone.high < broken.close and all(b.close >= zone.low for b in between) and retest.low <= zone.high and retest.close > zone.high and retest.close > retest.open:
                return ('long', zone, 'break and retest', retest)
            if before.close >= zone.low > broken.close and all(b.close <= zone.high for b in between) and retest.high >= zone.low and retest.close < zone.low and retest.close < retest.open:
                return ('short', zone, 'break and retest', retest)
        broken=bars[-2]
        if zone.established_at >= broken.end-timedelta(minutes=broken.minutes):
            continue
        if broken.high > zone.high and retest.close < zone.low and retest.close < retest.open and retest.high >= zone.low:
            return ('short', zone, 'sweep and reclaim', retest)
        if broken.low < zone.low and retest.close > zone.high and retest.close > retest.open and retest.low <= zone.high:
            return ('long', zone, 'sweep and reclaim', retest)
    return None


def leader_diagnostics(leaders, now, policy=BASELINE_POLICY, setup_at=None):
    """Point-in-time votes with evidence, including rejected and expired reactions.

    Persistent votes belong to one completed Nasdaq event and session. The most
    recent reaction replaces an older one; a close through its opposite zone
    boundary invalidates it. A zone is reconstructed at the reaction's start,
    so later confirming pivots cannot justify an earlier vote.
    """
    rows = {}
    for symbol in MAG7:
        market = leaders.get(symbol)
        if market is None or not fresh(market, now, 15):
            rows[symbol] = {'vote': None, 'reason': 'current 15-minute data missing', 'zones': []}
            continue
        bars = closed(market, 15, now)
        four_hour = closed(market, 240, now)
        levels = zones(four_hour, bars[-1].end - timedelta(minutes=15), policy.zone_tolerance)
        row = {'vote': None, 'reason': 'no zone reaction' if levels else 'no eligible zones',
               'latest_bar_at': bars[-1].end.isoformat(), 'reaction_at': None, 'age_minutes': None,
               'latest_close': bars[-1].close,
               'zones': [{'low': z.low, 'high': z.high, 'established_at': z.established_at.isoformat(),
                          'touches': z.touches, 'touched_by_latest_bar': bars[-1].low <= z.high and bars[-1].high >= z.low} for z in levels]}
        rows[symbol] = row
        if len(bars) < 2:
            continue
        if policy.persistence_bars > 1 and setup_at is None:
            row['reason'] = 'no active Nasdaq event; persistent votes are not accumulated'
            continue
        for idx in range(len(bars)-1, max(0, len(bars)-policy.persistence_bars-1), -1):
            current, previous = bars[idx], bars[idx-1]
            if policy.persistence_bars > 1 and (current.end.astimezone(ET).date() != now.astimezone(ET).date()
                    or current.end < setup_at):
                continue
            # A missing candle cannot lengthen the declared persistence window.
            age = (bars[-1].end-current.end).total_seconds()/60
            if age > 15*(policy.persistence_bars-1):
                continue
            known = levels if idx == len(bars)-1 else zones(four_hour, current.end-timedelta(minutes=15), policy.zone_tolerance)
            found = next(((d, z) for z in known if (d := reaction(previous, current, z))), None)
            if found is None:
                continue
            vote, zone = found
            row.update(reaction_at=current.end.isoformat(), age_minutes=age,
                       reaction_zone={'low':zone.low,'high':zone.high,'established_at':zone.established_at.isoformat()})
            invalidated = any(b.close < zone.low if vote == 'long' else b.close > zone.high for b in bars[idx+1:])
            row.update(vote=None if invalidated else vote,
                       reason='invalidated by subsequent close through zone' if invalidated else 'current reaction' if age == 0 else 'persistent reaction')
            break
    return rows


def leader_confirmation(leaders, direction, now, policy=BASELINE_POLICY, setup_at=None):
    rows = leader_diagnostics(leaders, now, policy, setup_at)
    for symbol, row in rows.items():
        if row['reason'] == 'current 15-minute data missing':
            return False, f'{symbol}: current 15-minute data missing'
    votes = {symbol: row['vote'] for symbol, row in rows.items()}
    opposite = 'short' if direction == 'long' else 'long'
    matches = sum(v == direction for v in votes.values())
    # Distinct companies; GOOG is not counted a second time. Contradictory rejection denies entry.
    okay = matches >= policy.minimum_leaders and sum(v == opposite for v in votes.values()) <= policy.maximum_opposition
    detail = ', '.join(f'{s}: {d or "no zone reaction"}' for s, d in votes.items())
    return okay, detail


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



def analyze(market, leaders, vix, now, policy=BASELINE_POLICY):
    """One Nasdaq method from V2/V3. No V1 tape/VWAP or unrelated indicators.

    Rules not specified numerically by the videos are versioned interpretations,
    never described as verbatim instructions. The result is a setup for review,
    not permission for an order.
    """
    checks=[]
    def check(name, passed, detail):
        checks.append({'name':name,'passed':bool(passed),'detail':detail})
        return bool(passed)
    result={'state':'WATCHING','can_enter':False,'checks':checks,'levels':[],
            'direction':None,'entry':None,'stop':None,'target':None,'event_at':None,
            'policy_version':'nasdaq-video-interpretation-v1','execution_blocker':'Waiting for the complete strategy setup'}
    if not check('Current Nasdaq observation',market.symbol=='QQQ' and fresh(market,now,60),
                 'Analysis uses QQQ as a Nasdaq ETF proxy; fresh completed 1-hour candles required'):
        return result
    bars=closed(market,60,now)
    # Premarked before the start of the potential event, not constructed around its outcome.
    before=bars[-1].end-timedelta(minutes=120)
    levels=zones(closed(market,240,now),before,policy.zone_tolerance)
    previous=prior_day_zones(market,now)
    result['levels']=[{'low':z.low,'high':z.high,'source':z.source,'established_at':z.established_at.isoformat()} for z in levels+previous]
    if not check('Premarked levels',levels or previous,'Repeated 4-hour levels and previous-day extremes are marked before the event'):
        return result
    event=pivot_event(bars,levels)
    # V2 describes a sweep as a break above/below yesterday's boundary. Do not
    # invent a mandatory close-back-inside or blend in the old daily-sweep detector.
    if event is None and len(bars)>=2:
        prior,current=bars[-2:]
        for zone in previous:
            if zone.established_at>=current.end-timedelta(minutes=60):
                continue
            if (zone.source=='previous-day high' and prior.high<=zone.high<current.high) or (zone.source=='previous-day low' and prior.low>=zone.low>current.low):
                event=(None,zone,'previous-day level sweep',current)
                break
    if event is None:
        current=bars[-1]
        touched=any(current.low<=z.high and current.high>=z.low for z in levels+previous)
        broken=len(bars)>1 and any((bars[-2].close<=z.high<current.close) or (bars[-2].close>=z.low>current.close) for z in levels)
        result['state']='WAITING_FOR_RETEST' if broken else 'AT_LEVEL' if touched else 'WATCHING'
        check('Nasdaq level event',False,'Wait for a break/retest at a 4-hour level, or a sweep of a previous-day extreme')
        return result
    _,zone,kind,bar=event
    result.update(state='CONFIRMING',event=kind,event_at=bar.end.isoformat(),entry=bar.close)
    check('Nasdaq level event',True,kind+' · '+zone.source)
    confirmations={direction:leader_confirmation(leaders,direction,now,policy,bar.end) for direction in ('long','short')}
    direction=next((d for d,(okay,_) in confirmations.items() if okay),None)
    if not check('Magnificent Seven at their zones',direction, confirmations[direction][1] if direction else confirmations['long'][1]):
        return result
    # The videos select direction from leader/volatility confirmation. A break
    # above a Nasdaq level is not automatically a long; V3 illustrates a short.
    result['direction']=direction
    okay,detail=vix_confirmation(vix,direction,now)
    check('Actual VIX zone reaction',okay,detail)
    targets=[z.low for z in levels if z.low>bar.close] if direction=='long' else [z.high for z in levels if z.high<bar.close]
    target=(min(targets) if direction=='long' else max(targets)) if targets else None
    stop=min(zone.low,bar.low)-0.01 if direction=='long' else max(zone.high,bar.high)+0.01
    # This exit policy is an explicit implementation proposal, not specified in V2/V3.
    geometry=target is not None and (stop<bar.close<target if direction=='long' else target<bar.close<stop)
    check('Stop and target',geometry,'Execution policy: stop beyond event/zone, target next premarked opposing level; the videos omit exit rules')
    result.update(stop=round(stop,2),target=round(target,2) if target else None)
    if all(c['passed'] for c in checks):
        result.update(state='SETUP_READY', execution_blocker='Owner permission and current broker checks still required')
    return result
