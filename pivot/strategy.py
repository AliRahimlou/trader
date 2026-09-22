"""Two independently evaluated Nasdaq location methods from the primary recordings.

Closed-bar geometry is an explicit interpretation; broker admission is checked separately.
No score, FVG, first-clip tape/VWAP rule, or ETF volatility fallback is imported.
"""
from datetime import datetime, time, timedelta, timezone
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from math import isfinite
from zoneinfo import ZoneInfo
from .models import Zone, MAG7, timestamp

ET = ZoneInfo('America/New_York')
ANALYSIS_VERSION = 'nasdaq-video-interpretation-v5'
LEADER_MINUTES = 5
VIX_MINUTES = 15
CANDLE_PUBLICATION_GRACE_SECONDS = 90
# Upper bounds shared with the executor's signal contract (pivot/execution.py).
MAX_PERSISTENCE_BARS = 24
MAX_EVENT_SESSIONS = 5
REGULAR_CLOSE = time(16)
# Hourly buckets that can end a regular or early-close session (12:30 on a
# 13:00 close, 15:30 on a full session, 16:00 if a closing half-hour bucket is
# ever emitted). The next session's first hourly bucket always ends 10:30.
HOURLY_SESSION_ENDS = ((12, 30), (15, 30), (16, 0))
HOURLY_SESSION_START = (10, 30)


@dataclass(frozen=True)
class AnalysisPolicy:
    """Declared interpretations, frozen before replay; not creator formulas.

    Every number here is an app choice; the recordings supply none of them.
    ``zone_tolerance`` is the half-width of a Nasdaq four-hour area and of a
    leader area, as a fraction of price. ``level_touches`` is how many
    nonadjacent interactions (the swing pivot itself counting as the first) a
    four-hour swing candidate needs before it is an established area, and
    ``max_areas`` caps the areas watched to the ones nearest the current
    price (video: a handful of hand-drawn lines). ``persistence_bars`` is how
    many latest closed five-minute leader candles may hold a still-valid
    reaction (12 = 60 minutes); ``minimum_leaders``/``maximum_opposition``
    translate "majority buying, majority selling, mixed = no trade" into a
    count. ``event_sessions`` is how many regular sessions (the origin's
    included) a break or sweep stays tradable; the video shows retests days
    after the break and states no limit. ``min_reward_risk`` is the smallest
    (target - entry) / (entry - stop) multiple an opposing premarked level
    must offer; ``vix_zone_tolerance`` is the half-width of an actual VIX
    swing area; ``vix_persistence_bars`` is how many of the latest closed
    fifteen-minute VIX candles may hold the opposite reaction;
    ``vix_base_bars``/``vix_base_range`` define a VIX consolidation base (a
    run of that many consecutive candles whose combined range is within that
    fraction of price); ``min_stop_fraction`` is the smallest stop distance a
    plan may use.
    """
    zone_tolerance: float = 0.001
    level_touches: int = 2
    max_areas: int = 16
    persistence_bars: int = 12
    minimum_leaders: int = 4
    maximum_opposition: int = 1
    event_sessions: int = 2
    min_reward_risk: float = 1.0
    vix_zone_tolerance: float = 0.01
    vix_persistence_bars: int = 2
    vix_base_bars: int = 4
    vix_base_range: float = 0.02
    min_stop_fraction: float = 0.001
    retest_proximity: float = 0.004

    def __post_init__(self):
        fractions = (self.zone_tolerance, self.min_reward_risk, self.vix_zone_tolerance, self.vix_base_range,
                     self.min_stop_fraction, self.retest_proximity)
        counts = (self.level_touches, self.max_areas, self.persistence_bars, self.minimum_leaders,
                  self.maximum_opposition, self.event_sessions, self.vix_persistence_bars, self.vix_base_bars)
        if (any(isinstance(v, bool) or not isinstance(v, (float, int)) or not isfinite(v) for v in fractions)
                or any(isinstance(v, bool) or not isinstance(v, int) for v in counts)):
            raise ValueError('Invalid analysis policy')
        if not (0 < self.zone_tolerance <= 0.01 and 1 <= self.level_touches <= 6 and 1 <= self.max_areas <= 64
                and 1 <= self.persistence_bars <= MAX_PERSISTENCE_BARS
                and 1 <= self.minimum_leaders <= 7 and 0 <= self.maximum_opposition < self.minimum_leaders
                and 1 <= self.event_sessions <= MAX_EVENT_SESSIONS
                and 0 < self.min_reward_risk <= 10 and 0 < self.vix_zone_tolerance <= 0.05
                and 1 <= self.vix_persistence_bars <= 8 and 2 <= self.vix_base_bars <= 16
                and 0 < self.vix_base_range <= 0.1 and 0 <= self.min_stop_fraction <= 0.05
                and 0 < self.retest_proximity <= 0.05):
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


def _frame_history(bars, before):
    """Ordered same-frame bars closed before ``before``; None when the input is invalid."""
    if not bars or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        return None, None
    minutes = bars[0].minutes
    if any(b.minutes != minutes for b in bars):
        return None, None
    return [b for b in bars if b.end < before], minutes


def interaction_zones(bars, before, tolerance=0.001):
    """Fixed historical anchors with repeated nonadjacent touches or crossings.

    Each closed bar introduces its low/high as candidates. First chronological
    anchor wins overlapping bands; bounds never move as later evidence arrives.
    A second interaction at least two bar indices later establishes the area.
    Both wick touches and body crossings count, without requiring a swing.
    This is an explicit approximation of manually marked areas, not a recovered
    supply/demand indicator. Leaders use actual 5m inputs; since v5 the QQQ
    four-hour areas come from ``swing_areas`` instead.
    """
    history, minutes = _frame_history(bars, before)
    if history is None:
        return []
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


def swing_areas(bars, before, tolerance=0.001, touches=2, limit=16, reference=None):
    """Four-hour areas from confirmed swing pivots that price met more than once.

    Video rule (V3 00:18-00:42): lines are drawn by hand on the four-hour
    chart "everywhere price has broken through or touched more than once",
    justified by scrolling back five to six weeks. App interpretation (v5): a
    candidate is a confirmed swing high (a bar's high above both neighbours)
    or swing low (below both), proposed once its right-hand neighbour has
    closed, spanning ±``tolerance`` around the pivot price. A candidate within
    2×``tolerance`` of any pivot already in a cluster merges into it. A
    cluster becomes an established area at its ``touches``-th nonadjacent
    interaction: the pivot bar counts as the first, and a later bar whose
    range touches the band or whose close crosses it (previous close on one
    side, this close on the other) counts as another when at least two bar
    indices after the previous one. Bounds freeze at establishment; later
    pivots merging in add touches only. At most ``limit`` established areas
    nearest ``reference`` (default: the latest close before ``before``) are
    returned, so the watched set is a handful of levels, as on his chart.
    Only bars closed before ``before`` are used: a later bar can neither
    create nor move an area, and ``established_at`` is the close at which the
    area became known.
    """
    history, minutes = _frame_history(bars, before)
    if not history:
        return []
    clusters = []

    def establish(cluster, at):
        if cluster['established_at'] is None and cluster['touches'] >= touches:
            cluster['established_at'] = at

    for index, bar in enumerate(history):
        previous = history[index - 1] if index else None
        for cluster in clusters:
            if index - cluster['last_index'] < 2:
                continue
            low, high = cluster['low'], cluster['high']
            touched = bar.low <= high and bar.high >= low
            crossed = previous is not None and ((previous.close < low and bar.close > high)
                                                or (previous.close > high and bar.close < low))
            if touched or crossed:
                cluster['touches'] += 1
                cluster['last_index'] = index
                establish(cluster, bar.end)
        if index < 2:
            continue
        # This bar is the right-hand neighbour that confirms the previous bar as a pivot.
        left, pivot = history[index - 2], history[index - 1]
        for price, is_pivot in ((pivot.high, pivot.high > left.high and pivot.high > bar.high),
                                (pivot.low, pivot.low < left.low and pivot.low < bar.low)):
            if not is_pivot:
                continue
            band = (price * (1 - tolerance), price * (1 + tolerance))
            cluster = next((c for c in clusters if any(abs(price / p - 1) <= tolerance * 2 for p in c['pivots'])), None)
            if cluster is None:
                cluster = {'pivots': [price], 'low': band[0], 'high': band[1], 'touches': 1,
                           'last_index': index - 1, 'established_at': None}
                clusters.append(cluster)
                establish(cluster, bar.end)
                continue
            cluster['pivots'].append(price)
            if cluster['established_at'] is None:
                cluster['low'], cluster['high'] = min(cluster['low'], band[0]), max(cluster['high'], band[1])
            if index - 1 - cluster['last_index'] >= 2:
                cluster['touches'] += 1
                cluster['last_index'] = index - 1
                establish(cluster, bar.end)
    established = [c for c in clusters if c['established_at'] is not None]
    reference = history[-1].close if reference is None else reference
    established.sort(key=lambda c: (abs((c['low'] + c['high']) / 2 - reference), c['low']))
    source = ('4h' if minutes == 240 else f'{minutes}m') + ' swing area'
    return sorted((Zone(c['low'], c['high'], c['established_at'], source, c['touches'])
                   for c in established[:limit]), key=lambda z: (z.low, z.high, z.established_at))


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


# --- leader period levels ------------------------------------------------------

def _session_date(bar):
    return bar.end.astimezone(ET).date()


def _session_rows(daily, fives, before):
    """One row per observed session: OHLC plus when each figure became known.

    A session the five-minute history covers from its 09:35 ET candle takes
    its open, high, low and close from those regular-session candles, even
    when a provider daily bar exists: the daily bar may carry prints the
    regular-session candles do not, and preferring it would make a level
    depend on whether the daily read succeeded. Daily bars (frames[1440],
    when the feed supplies them) give the high/low/close of the sessions
    outside the five-minute window. A five-minute session that does not
    start at 09:35 supplies no open and only fills a session the daily frame
    lacks, so a partial five-minute history never invents a session open.
    """
    rows = {}
    for bar in daily:
        if bar.end >= before:
            continue
        day = (bar.end - timedelta(seconds=1)).astimezone(ET).date()
        rows[day] = {'date': day, 'open': bar.open, 'high': bar.high, 'low': bar.low, 'close': bar.close,
                     'open_at': bar.end, 'close_at': bar.end, 'open_known': True}
    groups = {}
    for bar in fives:
        if bar.end < before:
            groups.setdefault(_session_date(bar), []).append(bar)
    for day, group in groups.items():
        first, last = group[0], group[-1]
        opened = first.end.astimezone(ET)
        open_known = (opened.hour, opened.minute) == (9, 35)
        if open_known or day not in rows:
            rows[day] = {'date': day, 'open': first.open if open_known else None,
                         'high': max(b.high for b in group), 'low': min(b.low for b in group),
                         'close': last.close, 'open_at': first.end, 'close_at': last.end, 'open_known': open_known}
    return [rows[day] for day in sorted(rows)]


def period_levels(market, bars, before, tolerance=0.001):
    """Leader period levels, as the video's key-levels indicator plots them.

    Video (V3 01:08-01:25): leader charts carry labelled prior-period levels
    (daily/weekly/monthly opens, previous week high/mid, Monday high/mid/low,
    daily high/low, VWAPs) and are read as "at supply, rejecting". App
    interpretation (v5): from the leader's own data, each of previous-session
    high/low/close, current session open, previous-week high/low/mid, the
    current week's Monday session high/low, week open (first candle of the
    week), and month open (first candle of the calendar month) becomes a
    ±``tolerance`` band established when the figure was known. A period level
    is skipped when the history cannot prove coverage (the previous week
    needs history back to its Monday; week and month opens need a session
    from before that period). Levels at one price are merged and their
    sources joined. The session VWAP is handled separately per candle.
    """
    daily = closed(market, 1440, before) if market is not None else []
    rows = _session_rows(daily, bars, before)
    if not bars:
        return []
    today = _session_date(bars[-1])
    complete = [r for r in rows if r['date'] < today]
    earliest = rows[0]['date'] if rows else None
    levels = []
    if complete:
        previous = complete[-1]
        levels += [(previous['high'], 'previous-session high', previous['close_at']),
                   (previous['low'], 'previous-session low', previous['close_at']),
                   (previous['close'], 'previous-session close', previous['close_at'])]
    current = next((r for r in rows if r['date'] == today), None)
    if current is not None and current['open_known']:
        levels.append((current['open'], 'session open', current['open_at']))
    week = today.isocalendar()[:2]
    monday = today - timedelta(days=today.weekday())
    previous_monday = monday - timedelta(days=7)
    previous_week = [r for r in complete if r['date'].isocalendar()[:2] == previous_monday.isocalendar()[:2]]
    if previous_week and earliest <= previous_monday:
        high, low = max(r['high'] for r in previous_week), min(r['low'] for r in previous_week)
        known = previous_week[-1]['close_at']
        levels += [(high, 'previous-week high', known), (low, 'previous-week low', known),
                   ((high + low) / 2, 'previous-week mid', known)]
    monday_row = next((r for r in complete if r['date'] == monday), None)
    if monday_row is not None:
        levels += [(monday_row['high'], 'Monday high', monday_row['close_at']),
                   (monday_row['low'], 'Monday low', monday_row['close_at'])]
    this_week = [r for r in rows if r['date'].isocalendar()[:2] == week and r['date'] <= today]
    if this_week and earliest < monday and this_week[0]['open_known']:
        levels.append((this_week[0]['open'], 'week open', this_week[0]['open_at']))
    this_month = [r for r in rows if (r['date'].year, r['date'].month) == (today.year, today.month) and r['date'] <= today]
    if this_month and (earliest.year, earliest.month) < (today.year, today.month) and this_month[0]['open_known']:
        levels.append((this_month[0]['open'], 'month open', this_month[0]['open_at']))
    merged = {}
    for price, source, known in levels:
        key = round(price, 6)
        if key in merged:
            merged[key] = (merged[key][0], merged[key][1] + ' / ' + source, min(merged[key][2], known))
        else:
            merged[key] = (price, source, known)
    return sorted((Zone(price * (1 - tolerance), price * (1 + tolerance), known, source, 1)
                   for price, source, known in merged.values()), key=lambda z: (z.low, z.high))


def session_vwap_zone(bars, index, tolerance=0.001):
    """The session VWAP known one full candle before ``bars[index]`` starts.

    Video: leader charts plot daily VWAP. App interpretation (v5): the
    cumulative Σ(vwap×volume)/Σvolume over the current session's five-minute
    candles up to and including the one before the previous candle, banded by
    ±``tolerance`` and established at that candle's close, so the level is
    fixed before the reaction candle opens. None when the candles carry no
    VWAP/volume, when the session has no earlier candle, or when the previous
    candle belongs to another session.
    """
    if index < 2 or _session_date(bars[index - 1]) != _session_date(bars[index]):
        return None
    day = _session_date(bars[index])
    session = [b for b in bars[:index - 1] if _session_date(b) == day]
    if not session or any(b.vwap is None or b.volume <= 0 for b in session):
        return None
    volume = sum(b.volume for b in session)
    price = sum(b.vwap * b.volume for b in session) / volume
    if not isfinite(price) or price <= 0:
        return None
    return Zone(price * (1 - tolerance), price * (1 + tolerance), session[-1].end, 'session VWAP', len(session))


def leader_areas(market, bars, before, tolerance=0.001):
    """Static leader areas: five-minute repeated interactions plus period levels."""
    return interaction_zones(bars, before, tolerance) + period_levels(market, bars, before, tolerance)


def leader_diagnostics(leaders, now, policy=BASELINE_POLICY, setup_at=None):
    """Five-minute location/rejection plus bounded, still-valid follow-through.

    Without a setup origin these are observations only; the origin is reported
    but does not discard reactions that began earlier in the same session
    (V2/V3 ask whether the leaders are rejecting their areas now, not whether
    they started after the Nasdaq candle). The persistence window
    (``policy.persistence_bars`` five-minute candles, 60 minutes by default),
    same-session rule, gap and close-through invalidation bound the
    evidence. Follow-through means the latest close is still beyond the area
    boundary in the vote direction; a one-cent pullback from the reaction
    close is not a lost vote. Areas are the five-minute repeated-interaction
    bands, the period levels and the session VWAP as it stood before each
    candle; the vote reports which source it came from. Conflicting active
    areas make a company neutral, with the conflict reported. No reaction is
    inferred from fifteen-minute bars.
    """
    rows = {}
    for symbol in MAG7:
        market = leaders.get(symbol)
        row = {'vote': None, 'vote_source': None, 'reason': 'current 5-minute data missing', 'zones': [],
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
        levels = leader_areas(market, bars, latest.end - timedelta(minutes=LEADER_MINUTES), policy.zone_tolerance)
        vwap_now = session_vwap_zone(bars, len(bars) - 1, policy.zone_tolerance)
        shown = levels + ([vwap_now] if vwap_now else [])
        row.update(reason='no zone reaction' if shown else 'no eligible zones',
                   zones=[{'low': z.low, 'high': z.high, 'source': z.source,
                           'established_at': z.established_at.isoformat(), 'touches': z.touches,
                           'touched_by_latest_bar': latest.low <= z.high and latest.high >= z.low}
                          for z in shown])
        if len(bars) < 2:
            continue
        evidence = []
        discarded = []
        # Scan independently per area. Otherwise iteration order could hide an
        # opposing reaction at a different area on a still-active setup. The
        # VWAP area is re-read per candle, as it stood before that candle.
        areas = [(zone, None) for zone in levels] + [(None, 'session VWAP')]
        for fixed, moving in areas:
            for index in range(len(bars)-1, 0, -1):
                current, previous = bars[index], bars[index-1]
                observed_age = (now - current.end).total_seconds() / 60
                if observed_age >= LEADER_MINUTES * policy.persistence_bars:
                    break
                if current.end.astimezone(ET).date() != now.astimezone(ET).date():
                    continue
                if current.end - previous.end != timedelta(minutes=LEADER_MINUTES):
                    continue
                zone = fixed if moving is None else session_vwap_zone(bars, index, policy.zone_tolerance)
                if zone is None:
                    continue
                vote = reaction(previous, current, zone)
                if vote is None:
                    continue
                tail = bars[index:]
                gap = any(b.end - a.end != timedelta(minutes=LEADER_MINUTES) for a, b in zip(tail, tail[1:]))
                invalidated = any(b.close < zone.low if vote == 'long' else b.close > zone.high
                                  for b in bars[index+1:])
                continuing = latest.close > zone.high if vote == 'long' else latest.close < zone.low
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
            row.update(vote=chosen['vote'], vote_source=zone.source, reaction_at=chosen['at'].isoformat(),
                       age_minutes=chosen['age'],
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
    """Missing data vetoes; a company without a reaction or with conflicting ones is neutral.

    Video: majority buying → long, majority selling → short, mixed → no
    trade. App interpretation: at least ``policy.minimum_leaders`` agreeing
    and at most ``policy.maximum_opposition`` opposing among the seven.
    """
    for symbol, row in rows.items():
        if row['reason'] == 'current 5-minute data missing':
            return False, f'{symbol}: {row["reason"]}'
    if _leader_observation_at(rows) is None:
        return False, 'Leader 5-minute candles are not synchronized; waiting for a common completed timestamp'
    votes = {symbol: row['vote'] for symbol, row in rows.items()}
    opposite = 'short' if direction == 'long' else 'long'
    okay = (sum(v == direction for v in votes.values()) >= policy.minimum_leaders
            and sum(v == opposite for v in votes.values()) <= policy.maximum_opposition)
    return okay, ', '.join(
        f'{symbol}: {vote + " at " + row["vote_source"] if vote else ("conflicting, neutral" if row.get("conflicting_reactions") else "no zone reaction, neutral")}'
        for (symbol, vote), row in zip(votes.items(), rows.values()))


def leader_confirmation(leaders, direction, now, policy=BASELINE_POLICY, setup_at=None):
    return _leader_confirmation(leader_diagnostics(leaders, now, policy, setup_at), direction, policy)


VIX_RULE_TEXT = 'VIX must rise from demand for a short, or fall from supply for a long'


def vix_base_zones(bars, before, base_bars=4, base_range=0.02):
    """Consolidation bases: where VIX sat before it launched (video: "look left").

    Video (V3 01:52-02:18): with no lines drawn, he scrolls the 15-minute VIX
    chart back two or three days to the consolidation base it spiked from
    and reads the current close against it. App interpretation (v5): any run
    of at least ``base_bars`` consecutive closed fifteen-minute candles whose
    combined high-low range is within ``base_range`` of price (measured
    against the run's low) is a base spanning [min low, max high],
    established at the run's last candle; overlapping bases merge into one
    (union of bounds, established when the later run completed). Only bars
    closed before ``before`` are used.
    """
    history, _ = _frame_history(bars, before)
    if not history or base_bars < 2:
        return []
    step = timedelta(minutes=history[0].minutes)
    runs = []
    for end in range(base_bars - 1, len(history)):
        window = history[end - base_bars + 1:end + 1]
        if any(b.end - a.end != step for a, b in zip(window, window[1:])):
            continue
        high, low = max(b.high for b in window), min(b.low for b in window)
        if (high - low) / low <= base_range:
            runs.append({'low': low, 'high': high, 'established_at': window[-1].end,
                         'bars': set(range(end - base_bars + 1, end + 1))})
    merged = []
    for run in sorted(runs, key=lambda r: (r['low'], r['high'])):
        target = next((m for m in merged if run['low'] <= m['high'] and run['high'] >= m['low']), None)
        if target is None:
            merged.append(run)
            continue
        target.update(low=min(target['low'], run['low']), high=max(target['high'], run['high']),
                      established_at=max(target['established_at'], run['established_at']),
                      bars=target['bars'] | run['bars'])
    return [Zone(m['low'], m['high'], m['established_at'], 'VIX consolidation base', len(m['bars'])) for m in merged]


VIX_PIVOT_SOURCE = 'VIX 15m repeated pivot'


def vix_areas(bars, before, policy=BASELINE_POLICY):
    """Repeated swing pivots (1% bands) plus consolidation bases known before ``before``."""
    # zones() is frozen with its 4h label; the VIX clusters are 15-minute pivots.
    return ([replace(zone, source=VIX_PIVOT_SOURCE) for zone in zones(bars, before, policy.vix_zone_tolerance)]
            + vix_base_zones(bars, before, policy.vix_base_bars, policy.vix_base_range))


def vix_reactions(bars, now, policy=BASELINE_POLICY):
    """Actual VIX area reactions on the latest closed fifteen-minute candles, with status.

    Video rule: VIX reacts at an area where it previously pivoted or based.
    App interpretation: the reaction candle may be any of the last
    ``policy.vix_persistence_bars`` closed candles of the current New York
    session, contiguous through the latest candle; areas are the repeated
    swing extremes and consolidation bases known before that candle; no
    later close may cross back through the area against the reaction, and
    the latest close must still sit beyond the area boundary in the reaction
    direction. The newest reaction per area wins. Each item carries
    direction, at, age (minutes to the latest close), zone and status, so
    traces can show discarded reactions beside admitted ones.
    """
    if len(bars) < 3:
        return []
    latest = bars[-1]
    day = now.astimezone(ET).date()
    step = timedelta(minutes=VIX_MINUTES)
    results = []
    for offset in range(policy.vix_persistence_bars):
        index = len(bars) - 1 - offset
        if index < 1:
            break
        current, previous = bars[index], bars[index - 1]
        tail = bars[index - 1:]
        # The scan cannot cross a missing/irregular candle or a session change.
        if (any(b.end - a.end != step for a, b in zip(tail, tail[1:]))
                or current.end.astimezone(ET).date() != day):
            break
        for zone in vix_areas(bars[:index], current.end - step, policy):
            direction = reaction(previous, current, zone)
            if direction is None or any(r['zone'] == zone for r in results):
                continue
            invalidated = any(b.close < zone.low if direction == 'long' else b.close > zone.high
                              for b in bars[index + 1:])
            continuing = latest.close > zone.high if direction == 'long' else latest.close < zone.low
            results.append({'direction': direction, 'at': current.end,
                            'age': (latest.end - current.end).total_seconds() / 60, 'zone': zone,
                            'status': ('invalidated by subsequent close through zone' if invalidated else
                                       'reaction has no current directional follow-through' if not continuing
                                       else 'active')})
    return results


def vix_diagnostics(vix, direction, now, policy=BASELINE_POLICY):
    """Actual-index confirmation with its evidence, shared by analysis and traces.

    ``direction`` is the Nasdaq trade direction (None before one is selected);
    the expected VIX reaction is the opposite. Only completed candles are
    used; the fresh actual-index quote is a separate broker-side admission.
    """
    expected = {'long': 'short', 'short': 'long'}.get(direction)
    result = {'okay': False, 'expected_reaction': expected, 'reaction_at': None, 'zone': None,
              'age_minutes': None, 'reactions': [], 'zones': [], 'fresh': vix_candles_fresh(vix, now),
              'persistence_bars': policy.vix_persistence_bars, 'zone_tolerance': policy.vix_zone_tolerance,
              'base_bars': policy.vix_base_bars, 'base_range': policy.vix_base_range,
              'reason': None, 'detail': VIX_RULE_TEXT}
    if not result['fresh']:
        return {**result, 'reason': 'fresh actual VIX data missing',
                'detail': 'Fresh actual VIX index data required; volatility ETFs and delayed data are not accepted'}
    bars = closed(vix, VIX_MINUTES, now)
    if len(bars) < 3:
        return {**result, 'reason': 'VIX history incomplete', 'detail': 'VIX history is incomplete'}
    result['zones'] = vix_areas(bars[:-2], bars[-1].end - timedelta(minutes=VIX_MINUTES), policy)
    result['reactions'] = reactions = vix_reactions(bars, now, policy)
    if not result['zones']:
        result['reason'] = 'no eligible zones'
    elif expected is None:
        result['reason'] = 'Nasdaq direction not selected; VIX gate not reached'
    else:
        matching = [r for r in reactions if r['direction'] == expected]
        active = [r for r in matching if r['status'] == 'active']
        clock = lambda at: at.astimezone(ET).strftime('%H:%M')
        if active:
            chosen = min(active, key=lambda r: (-r['at'].timestamp(), abs(bars[-1].close - r['zone'].mid), r['zone'].low))
            zone = chosen['zone']
            result.update(okay=True, reason='expected reaction present', reaction_at=chosen['at'],
                          zone=zone, age_minutes=chosen['age'],
                          detail=(f'VIX {expected} reaction at {zone.source} {zone.low:.2f}-{zone.high:.2f} on the '
                                  f'{clock(chosen["at"])} ET candle, {chosen["age"]:g} min before the latest close; '
                                  f'no later close crossed back through the area'))
        elif matching:
            newest = max(matching, key=lambda r: r['at'])
            result.update(reason='expected zone reaction absent',
                          detail=f'{VIX_RULE_TEXT}; the {expected} reaction on the {clock(newest["at"])} ET candle was {newest["status"]}')
        else:
            result.update(reason='expected zone reaction absent',
                          detail=(f'{VIX_RULE_TEXT}; no {expected} reaction within the last '
                                  f'{policy.vix_persistence_bars} closed 15-minute candles of this session'))
    return result


def vix_confirmation(vix, direction, now, policy=BASELINE_POLICY):
    evidence = vix_diagnostics(vix, direction, now, policy)
    return evidence['okay'], evidence['detail']


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


# --- Nasdaq location events ------------------------------------------------------

def event_expiry(origin_end, sessions=None):
    """End (16:00 ET) of the regular session ``sessions`` sessions after the origin's, the origin's counted.

    Video: no time limit is stated and retests are shown days after the
    break. App interpretation (v5): an event lives through the end of the
    next regular session (``sessions`` = 2). Sessions are counted on
    weekdays; an exchange holiday is not skipped, so an event that would
    cross one expires at that weekday's 16:00 ET, earlier rather than later.
    """
    sessions = BASELINE_POLICY.event_sessions if sessions is None else sessions
    day = origin_end.astimezone(ET).date()
    for _ in range(sessions - 1):
        day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    return datetime.combine(day, REGULAR_CLOSE, ET)


def _contiguous(previous, current):
    """Consecutive hourly buckets, treating the overnight session boundary as continuous.

    A missing hourly candle inside a session breaks contiguity; the step from
    a session's last bucket (12:30 early close, 15:30, or 16:00) to the next
    session's first bucket (10:30) does not.
    """
    if current.end - previous.end == timedelta(minutes=60):
        return True
    left, right = previous.end.astimezone(ET), current.end.astimezone(ET)
    return (left.date() < right.date() and (left.hour, left.minute) in HOURLY_SESSION_ENDS
            and (right.hour, right.minute) == HOURLY_SESSION_START)


def _event_identity(method, zone, origin):
    # Retest time, current leader direction, and order price cannot create a
    # second identity for the same initiating location event.
    payload = ['QQQ', method, float(zone.low).hex(), float(zone.high).hex(),
               zone.established_at.astimezone(timezone.utc).isoformat(),
               origin.end.astimezone(timezone.utc).isoformat()]
    return 'ev2_' + sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()


def location_events(bars, levels, method, now, policy=BASELINE_POLICY):
    """Latest bounded state per pre-existing area, reconstructed without writes.

    Break/retest (video: "we break and we trade the retest"): the first closed
    hourly crossing of an area edge starts the event; it confirms when a later
    hourly candle's range returns to the broken edge (an upward break: low at
    or below the area high; a downward break: high at or above the area low),
    whatever that candle's close direction; the trade direction is decided
    later by the leaders and VIX, as in the recording's short at a level just
    reclaimed. A close through the opposite area boundary invalidates it.
    Prior-day sweep: no reclaim is invented; a later close through the sweep
    candle's opposite extreme invalidates the location. Both live until
    ``event_expiry`` (the end of the next regular session by default), die on
    a missing hourly candle inside a session, and survive the session
    boundary. A fresh crossing is required to create a new event after
    invalidation/expiry. Identity is the area plus the origin candle.
    """
    if (method not in ('four_hour_retest', 'prior_day_sweep') or len(bars) < 2
            or any(b.minutes != 60 for b in bars)
            or any(a.end >= b.end for a, b in zip(bars, bars[1:]))):
        return []
    bars = [b for b in bars if b.end <= now]
    events = []
    sessions = policy.event_sessions
    expired_reason = f'The originating event reached the end of its {sessions}-session lifetime'
    for zone in levels:
        active = None
        for previous, current in zip(bars, bars[1:]):
            # A candle whose own lifetime has ended can neither start nor
            # advance an event that is still alive now.
            if event_expiry(current.end, sessions) <= now:
                continue
            start = current.end - timedelta(minutes=60)
            if zone.established_at >= start:
                continue
            contiguous = _contiguous(previous, current)
            if active is not None and active['state'] not in ('EXPIRED', 'INVALIDATED'):
                if current.end >= active['expires_at']:
                    active.update(state='EXPIRED', reason=expired_reason)
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
                        returned = (current.low <= zone.high if active['break_direction'] == 'long'
                                    else current.high >= zone.low)
                        if returned:
                            active.update(confirmed=current, state='CONFIRMING',
                                          reason='Hourly break confirmed by the return to the level')
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
                      'expires_at': event_expiry(current.end, sessions),
                      'state': 'CONFIRMING' if confirmed else 'WAITING_FOR_RETEST',
                      'reason': 'Previous-day boundary swept' if confirmed else 'Break observed; waiting for the hourly return to the level'}
        if active is not None:
            if now >= active['expires_at']:
                active.update(state='EXPIRED', reason=expired_reason)
            events.append(active)
    return events


def _zone_dict(zone):
    return {'low': zone.low, 'high': zone.high, 'source': zone.source,
            'established_at': zone.established_at.isoformat(), 'touches': zone.touches}


def _empty_result(method, label):
    return {'id': method, 'label': label, 'state': 'WATCHING', 'can_enter': False,
            'checks': [], 'levels': [], 'direction': None, 'entry': None, 'stop': None, 'target': None,
            'reward_risk': None, 'target_source': None, 'target_zone': None,
            'vix_reaction_at': None, 'vix_zone': None, 'vix_reaction_age_minutes': None,
            'event': None, 'event_at': None, 'retest_at': None, 'event_id': None, 'event_origin_at': None,
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
                  retest_at=confirmed.end.isoformat() if confirmed and result['id'] == 'four_hour_retest' else None,
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
    # Video: "we're near a pivotal area ... trade the retest". App interpretation
    # (4.5.0): a confirmed event only produces a plan while the latest closed
    # hourly candle still touches the event area or closes within
    # policy.retest_proximity of it; after price leaves, the event waits for
    # another return instead of entering far from the level.
    latest = bars[-1]
    distance = max(0.0, zone.low - latest.close, latest.close - zone.high)
    near = (latest.low <= zone.high and latest.high >= zone.low) or distance <= latest.close * policy.retest_proximity
    result['distance_from_area'] = round(distance / latest.close, 6) if latest.close else None
    if event['state'] == 'CONFIRMING' and not near:
        event_ok, event_detail = False, (f'{event["reason"]}; price is {distance / latest.close * 100:.2f}% from the area, '
                                         f'more than {policy.retest_proximity * 100:g}%: waiting for a return to the level')
    else:
        event_ok, event_detail = event['state'] == 'CONFIRMING', event['reason']
    if not _checked(result, 'Nasdaq level event', event_ok, event_detail):
        return _finish(result)
    confirmations = {d: _leader_confirmation(rows, d, policy) for d in ('long', 'short')}
    direction = next((d for d, (okay, _) in confirmations.items() if okay), None)
    if not _checked(result, 'Magnificent Seven at their zones', direction,
                    confirmations[direction][1] if direction else confirmations['long'][1]):
        return _finish(result)
    result['direction'] = direction
    vix_evidence = vix_diagnostics(vix, direction, now, policy)
    _checked(result, 'Actual VIX zone reaction', vix_evidence['okay'], vix_evidence['detail'])
    result.update(vix_reaction_at=vix_evidence['reaction_at'].isoformat() if vix_evidence['reaction_at'] else None,
                  vix_zone=_zone_dict(vix_evidence['zone']) if vix_evidence['zone'] else None,
                  vix_reaction_age_minutes=vix_evidence['age_minutes'])
    # Location direction is independent of trade direction, as in V3's short
    # after an upward break. The exit policy remains an app interpretation.
    current = bars[-1]
    result['entry'] = current.close
    stop = (min(zone.low, origin.low, confirmed.low, current.low) - .01 if direction == 'long' else
            max(zone.high, origin.high, confirmed.high, current.high) + .01)
    stop = round(stop, 2)
    risk = abs(current.close - stop)
    minimum_risk = current.close * policy.min_stop_fraction
    if risk < minimum_risk:
        # A stop a few cents away is noise, not a level: the plan would be
        # stopped by ordinary spread. App interpretation, not a video rule.
        target, target_zone, reward_risk = None, None, None
        detail = (f'Execution interpretation: the {risk:.2f} stop distance is below the minimum '
                  f'{policy.min_stop_fraction * 100:g}% of the entry price ({minimum_risk:.2f}); no plan')
    else:
        target, target_zone, reward_risk = _target(all_levels, zone, current, direction, current.close, stop, policy)
        detail = ((f'Execution interpretation: stop beyond event/zone; target {target_zone.source} at {reward_risk:.2f}R, '
                   f'the nearest opposing pre-existing level at least {policy.min_reward_risk:g}R away') if target is not None else
                  (f'Execution interpretation: no opposing pre-existing 4-hour or previous-day level offers at least '
                   f'{policy.min_reward_risk:g}R against the {risk:.2f} stop distance; '
                   f'the event area itself is never the target'))
    geometry = target is not None and (stop < current.close < target if direction == 'long' else target < current.close < stop)
    _checked(result, 'Stop and target', geometry, detail)
    result.update(stop=stop, target=target, reward_risk=reward_risk,
                  target_source=target_zone.source if target_zone else None,
                  target_zone=_zone_dict(target_zone) if target_zone else None)
    if all(c['passed'] for c in result['checks']):
        result.update(state='SETUP_READY', execution_blocker='Owner permission and current broker checks still required')
    return _finish(result)


def _target(levels, event_zone, origin, direction, entry, stop, policy):
    """Nearest opposing premarked level offering the declared minimum reward-to-risk.

    Video: take profit at the next pivotal area (qualitative). App
    interpretation (v4, widened in 4.5.0): candidates are 4-hour areas or
    previous-day levels established before the entry candle began (so a
    next-session retest can target the break day's high/low and afternoon
    areas; nothing formed during the entry candle counts), excluding the
    event's own area
    (a swept level cannot be its own target), beyond the entry in the trade
    direction, whose distance is at least ``policy.min_reward_risk`` times
    the stop distance. Nearer levels below the multiple are skipped for the
    next one. Returns (target, zone, reward_risk); all None when absent.
    """
    before_origin = origin.end - timedelta(minutes=60)
    risk = entry - stop if direction == 'long' else stop - entry
    if risk <= 0:
        return None, None, None
    candidates = []
    for level in levels:
        if level == event_zone or level.established_at >= before_origin:
            continue
        price = round(level.low if direction == 'long' else level.high, 2)
        reward = price - entry if direction == 'long' else entry - price
        if reward > 0 and reward / risk >= policy.min_reward_risk:
            candidates.append((reward, level.established_at, price, level))
    if not candidates:
        return None, None, None
    reward, _, price, level = min(candidates, key=lambda c: c[:2])
    return price, level, float(reward / risk)


def _rank(result):
    order = {'SETUP_READY': 6, 'CONFIRMING': 5, 'WAITING_FOR_RETEST': 4,
             'AT_LEVEL': 3, 'WATCHING': 2, 'INVALIDATED': 1, 'EXPIRED': 0}
    return (order.get(result['state'], 0), sum(c['passed'] for c in result['checks']),
            result.get('event_origin_at') or '', result.get('event_id') or '')


def analyze(market, leaders, vix, now, policy=BASELINE_POLICY):
    """Independently evaluate the two source location methods; never authorize orders.

    v5 (video-aligned) interpretations: four-hour areas are swing pivots
    touched or broken more than once (``swing_areas``); a break confirms on
    the hourly return to the level with the direction left to the leaders and
    VIX; events live to the end of the next regular session; leader areas add
    period levels and the session VWAP; four of seven leaders with at most
    one opposing confirm within a 60-minute window; VIX areas add
    consolidation bases. The minimum reward-to-risk target rule, QQQ
    completed-hour sampling and fifteen-minute VIX candles remain explicit
    app choices.
    """
    methods = [('four_hour_retest', '4-hour areas / hourly break and retest'),
               ('prior_day_sweep', 'Previous-day boundary sweep')]
    rows = []
    qualified = []
    candidate_diagnostics = []
    valid = market is not None and market.symbol == 'QQQ' and fresh(market, now, 60)
    bars = closed(market, 60, now) if valid else []
    all_levels = []
    if valid:
        levels = swing_areas(closed(market, 240, now), bars[-1].end, policy.zone_tolerance,
                             policy.level_touches, policy.max_areas, reference=bars[-1].close)
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
                        'Four-hour swing areas touched or broken more than once' if method == 'four_hour_retest' else 'Verified previous-session high and low'):
            rows.append(_finish(result))
            continue
        events = location_events(bars, method_levels, method, now, policy)
        if events:
            candidates = [_evaluate_event(result, event, all_levels, bars, leaders, vix, now, policy) for event in events]
            for candidate in candidates:
                candidate_diagnostics.append({key: candidate.get(key) for key in
                    ('id', 'event_id', 'event_origin_at', 'event_at', 'retest_at', 'event_expires_at', 'state', 'direction', 'checks')})
            qualified.extend(candidate for candidate in candidates if candidate['state'] == 'SETUP_READY')
            result = max(candidates, key=_rank)
            result['candidate_count'] = len(candidates)
        else:
            touched = any(bars[-1].low <= z.high and bars[-1].high >= z.low for z in method_levels)
            result['state'] = 'AT_LEVEL' if touched else 'WATCHING'
            _checked(result, 'Nasdaq level event', False,
                     'Wait for a closed-hour break of a pre-existing 4-hour swing area and the return to it' if method == 'four_hour_retest' else
                     'Wait for a fresh sweep of the previous-day high or low')
            _finish(result)
        rows.append(result)
    selected = max(rows, key=_rank)
    result = {key: value for key, value in selected.items() if key not in ('id', 'label')}
    result.update(strategy_id=selected['id'], strategies=rows, levels=[_zone_dict(z) for z in all_levels],
                  area_rule={'zone_tolerance': policy.zone_tolerance, 'touches': policy.level_touches,
                             'max_areas': policy.max_areas},
                  event_rule={'sessions': policy.event_sessions},
                  leader_rule={'minimum_agree': policy.minimum_leaders,
                               'maximum_opposing': policy.maximum_opposition,
                               'persistence_minutes': LEADER_MINUTES * policy.persistence_bars},
                  vix_rule={'zone_tolerance': policy.vix_zone_tolerance,
                            'persistence_minutes': VIX_MINUTES * policy.vix_persistence_bars,
                            'base_bars': policy.vix_base_bars, 'base_range': policy.vix_base_range},
                  exit_rule={'min_reward_risk': policy.min_reward_risk, 'min_stop_fraction': policy.min_stop_fraction})
    # Preserve every qualified opportunity for admission. A previously handled
    # event must not hide an unhandled area or the other independently valid
    # method. Only the executor knows durable consumption; analysis stays pure.
    result['entry_candidates'] = [
        {**{key: value for key, value in candidate.items()
            if key not in ('id', 'label', 'leader_evidence', 'levels', 'candidate_count')},
         'strategy_id': candidate['id']}
        for candidate in sorted(qualified, key=_rank, reverse=True)]
    result['candidate_diagnostics'] = candidate_diagnostics
    if result['state'] in ('EXPIRED', 'INVALIDATED'):
        result['state'] = 'WATCHING'
    return result
