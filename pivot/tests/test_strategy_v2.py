"""Manufactured v2 contract witnesses; no video-equivalence or return claims.

All leader evidence is made from actual five-minute Bar objects at five-minute
intervals. These fixtures do not relabel fifteen-minute OHLC as smaller bars.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pivot.models import Bar, Market, MAG7, Zone
from pivot.strategy import (ANALYSIS_VERSION, CANDLE_PUBLICATION_GRACE_SECONDS, ET, AnalysisPolicy, analyze,
                            event_expiry, interaction_zones, leader_confirmation, leader_diagnostics, location_events)

NOW = datetime(2026, 9, 16, 16, tzinfo=timezone.utc)  # 12:00 ET, Wednesday
# v5 fixture: the frozen three-candle leader window keeps the older wall-clock
# witnesses about deadlines readable; the 60-minute default has its own tests.
SHORT_WINDOW = AnalysisPolicy(persistence_bars=3)


def candle(at, opened=100, high=105, low=95, close=100, minutes=5):
    return Bar(at, minutes, opened, high, low, close)


def four_hour_history():
    ranges = [(105, 95), (110, 94), (105, 95), (106, 90), (105, 95),
              (110, 94), (105, 95), (106, 90), (105, 95)]
    return [candle(NOW-timedelta(hours=4*(14-i)), high=h, low=l, minutes=240)
            for i, (h, l) in enumerate(ranges)]


def leader_market(symbol='AAPL', direction='long', at=NOW):
    ranges = [(105, 95), (110, 94), (105, 95), (106, 90), (105, 95),
              (110, 94), (105, 95), (106, 90), (105, 95), (106, 94), (105, 95), (106, 94)]
    history = [candle(at-timedelta(minutes=5*(13-i)), high=h, low=l)
               for i, (h, l) in enumerate(ranges)]
    history.append(candle(at-timedelta(minutes=5), high=106, low=94))
    current = (candle(at, high=105, low=89.9, close=104) if direction == 'long' else
               candle(at, high=110.1, low=95, close=96) if direction == 'short' else candle(at))
    return Market(symbol, {5: history+[current], 240: four_hour_history()}, 'alpaca_iex', True, at)


def setup_scenario(direction='long', method='prior_day_sweep'):
    """Return (QQQ market, leader markets, actual-index fixture, now)."""
    leaders = {symbol: leader_market(symbol, direction) for symbol in MAG7}
    # VIX retains the pre-v2 fifteen-minute area/reaction interpretation.
    history = [replace(bar, minutes=15) for bar in four_hour_history()]
    previous = candle(NOW-timedelta(minutes=15), high=106, low=94, minutes=15)
    inverse = (candle(NOW, high=110.1, low=95, close=96, minutes=15) if direction == 'long' else
               candle(NOW, high=105, low=89.9, close=104, minutes=15))
    vix = Market('I:VIX', {15: history+[previous, inverse]}, 'massive_indices', True, NOW)
    if method == 'prior_day_sweep':
        prior = candle(NOW-timedelta(hours=1), high=104, low=96, minutes=60)
        current = (candle(NOW, 99, 101, 88, 100, 60) if direction == 'long' else
                   candle(NOW, 101, 112, 99, 100, 60))
        # The opposing previous-day level sits 20 points from the 100 entry
        # against a 12.01 stop distance (1.67R), so the v4 target rule admits it.
        daily = (candle(NOW-timedelta(days=1), high=120, low=90, minutes=1440) if direction == 'long' else
                 candle(NOW-timedelta(days=1), high=110, low=80, minutes=1440))
        market = Market('QQQ', {1440: [daily], 60: [prior, current], 240: []}, 'alpaca_iex', True, NOW,
                        previous_session='2026-09-15')
    elif method == 'four_hour_retest':
        bars = ([candle(NOW-timedelta(hours=2), 92, 93, 91, 92, 60),
                 candle(NOW-timedelta(hours=1), 92, 93, 88, 89, 60),
                 candle(NOW, 89.5, 90, 88, 89, 60)] if direction == 'long' else
                [candle(NOW-timedelta(hours=2), 108, 109, 107, 108, 60),
                 candle(NOW-timedelta(hours=1), 108, 112, 107, 111, 60),
                 candle(NOW, 110.5, 112, 110, 111, 60)])
        market = Market('QQQ', {240: four_hour_history(), 60: bars}, 'alpaca_iex', True, NOW)
    else:
        raise ValueError('Unknown manufactured method')
    return market, leaders, vix, NOW


@pytest.mark.parametrize('direction', ['long', 'short'])
@pytest.mark.parametrize('method', ['four_hour_retest', 'prior_day_sweep'])
def test_each_method_accepts_genuine_five_minute_confirmation_without_order_permission(direction, method):
    market, leaders, vix, now = setup_scenario(direction, method)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY'
    assert result['strategy_id'] == method and result['direction'] == direction
    assert result['policy_version'] == ANALYSIS_VERSION
    assert result['can_enter'] is False
    assert len(result['event_id']) == 68 and result['event_id'].startswith('ev2_')
    assert all(check['passed'] for check in result['checks'])
    assert {row['id'] for row in result['strategies']} == {'four_hour_retest', 'prior_day_sweep'}
    assert all(row['timeframe_minutes'] == 5 for row in result['leader_evidence'].values())


def test_crossing_areas_are_premarked_without_future_bars_or_moving_bands():
    # Monotonic extremes contain repeated body crossings but no local swing.
    bars = [candle(NOW-timedelta(hours=4*(8-i)), 99, 105+i, 90+i, 101, 240) for i in range(7)]
    levels = interaction_zones(bars, NOW)
    assert levels and all(z.touches >= 2 for z in levels)
    future = candle(NOW+timedelta(hours=4), 100, 200, 1, 150, 240)
    assert interaction_zones(bars+[future], NOW) == levels
    prefix = interaction_zones(bars[:3], NOW)
    assert prefix
    for zone in prefix:
        same = next(z for z in levels if (z.low, z.high) == (zone.low, zone.high))
        assert same.established_at == zone.established_at
    assert interaction_zones(bars[:2], NOW) == []  # adjacent occupancy is insufficient
    assert all(left.high < right.low for left, right in zip(levels, levels[1:]))


def test_fifteen_minute_prices_cannot_replace_missing_five_minute_leaders():
    market, leaders, vix, now = setup_scenario()
    for leader in leaders.values():
        leader.bars[15] = [candle(now-timedelta(minutes=15), minutes=15),
                           candle(now, high=105, low=89.9, close=104, minutes=15)]
        leader.bars.pop(5)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'CONFIRMING'
    assert result['checks'][-1]['name'] == 'Magnificent Seven at their zones'
    assert 'current 5-minute data missing' in result['checks'][-1]['detail']


def test_current_rejection_persists_without_latest_zone_touch_and_expires_by_wall_clock():
    market = leader_market(at=NOW-timedelta(minutes=10))
    market.observed_at = NOW
    market.bars[5] += [candle(NOW-timedelta(minutes=5), 105, 105.05, 104.95, 105),
                       candle(NOW, 106, 106.05, 105.95, 106)]
    row = leader_diagnostics({'AAPL': market}, NOW, SHORT_WINDOW, setup_at=NOW-timedelta(minutes=20))['AAPL']
    assert row['vote'] == 'long' and row['age_minutes'] == 10
    assert row['reaction_at'] == (NOW-timedelta(minutes=10)).isoformat()
    assert row['reason'] == 'persistent reaction'
    zone = row['reaction_zone']
    assert market.bars[5][-1].low > zone['high']
    later = NOW+timedelta(minutes=5)
    market.observed_at = later
    expired = leader_diagnostics({'AAPL': market}, later, SHORT_WINDOW, setup_at=NOW-timedelta(minutes=20))['AAPL']
    assert expired['vote'] is None
    # The v5 default window (twelve candles) keeps the same reaction for an hour.
    assert leader_diagnostics({'AAPL': market}, later, setup_at=NOW-timedelta(minutes=20))['AAPL']['vote'] == 'long'


def test_persistent_reaction_cannot_cross_a_missing_five_minute_bar():
    market = leader_market(at=NOW-timedelta(minutes=10))
    market.observed_at = NOW
    market.bars[5].append(candle(NOW, 104, 108, 104, 106))
    row = leader_diagnostics({'AAPL': market}, NOW, setup_at=NOW-timedelta(minutes=20))['AAPL']
    assert row['vote'] is None and row['reason'] == 'intervening 5-minute candle missing'


def test_later_close_through_area_invalidates_reaction_even_if_price_returns():
    market = leader_market(at=NOW-timedelta(minutes=10))
    market.observed_at = NOW
    market.bars[5] += [candle(NOW-timedelta(minutes=5), 88, 89, 87, 88),
                       candle(NOW, 106, 106.05, 105.95, 106)]
    row = leader_diagnostics({'AAPL': market}, NOW, setup_at=NOW-timedelta(minutes=20))['AAPL']
    assert row['vote'] is None
    assert row['reason'] == 'invalidated by subsequent close through zone'


def test_leader_evidence_before_event_counts_but_prior_session_is_not_carried():
    # v4: a same-session reaction that began before the Nasdaq origin still
    # describes current rejection; the origin only marks observational scope.
    market = leader_market(at=NOW-timedelta(minutes=5))
    market.observed_at = NOW
    market.bars[5].append(candle(NOW, 106, 106.05, 105.95, 106))
    row = leader_diagnostics({'AAPL': market}, NOW, setup_at=NOW)['AAPL']
    assert row['vote'] == 'long' and row['reaction_at'] == (NOW-timedelta(minutes=5)).isoformat()
    assert row['observational_only'] is False
    assert leader_diagnostics({'AAPL': market}, NOW)['AAPL']['observational_only'] is True
    # Move a manufactured midnight boundary without pretending it is RTH data.
    old = datetime(2026, 9, 17, 3, 55, tzinfo=timezone.utc)
    overnight = leader_market(at=old)
    overnight.observed_at = old+timedelta(minutes=5)
    overnight.bars[5].append(candle(overnight.observed_at, 106, 106.05, 105.95, 106))
    assert leader_diagnostics({'AAPL': overnight}, overnight.observed_at)['AAPL']['vote'] is None


def event_bars():
    return [candle(NOW-timedelta(hours=2), 99, 100, 98, 99, 60),
            candle(NOW-timedelta(hours=1), 99, 103, 98, 102, 60),
            candle(NOW, 101, 104, 100, 103, 60)]


def event_zone():
    return Zone(99.9, 100.1, NOW-timedelta(days=3), '4h swing area')


def test_retests_keep_origin_identity_first_confirmation_and_fixed_expiry():
    bars, zone = event_bars(), event_zone()
    first = location_events(bars, [zone], 'four_hour_retest', NOW)[0]
    later = NOW+timedelta(hours=1)
    second = location_events(bars+[candle(later, 102, 105, 100, 104, 60)], [zone], 'four_hour_retest', later)[0]
    assert first['id'] == second['id']
    assert first['origin'] == second['origin'] == bars[-2]
    assert first['confirmed'] == second['confirmed'] == bars[-1]
    # v5: the event lives to the end of the next regular session (Thursday 16:00 ET).
    expiry = datetime(2026, 9, 17, 16, tzinfo=ET)
    assert first['expires_at'] == second['expires_at'] == event_expiry(bars[-2].end) == expiry
    assert second['state'] == 'CONFIRMING'
    assert location_events(bars, [zone], 'four_hour_retest', expiry-timedelta(seconds=1))[0]['state'] == 'CONFIRMING'
    # Once its lifetime has ended the origin candle can no longer report an event at all.
    assert location_events(bars, [zone], 'four_hour_retest', expiry) == []
    # A one-session policy expires at the origin session's close instead.
    assert location_events(bars, [zone], 'four_hour_retest', NOW+timedelta(hours=4),
                           AnalysisPolicy(event_sessions=1)) == []
    assert location_events(bars, [zone], 'four_hour_retest', NOW+timedelta(hours=3, minutes=59),
                           AnalysisPolicy(event_sessions=1))[0]['expires_at'] == datetime(2026, 9, 16, 16, tzinfo=ET)


def test_unclosed_future_retest_and_late_constructed_area_do_not_qualify():
    bars, zone = event_bars(), event_zone()
    before_close = NOW-timedelta(seconds=1)
    result = location_events(bars, [zone], 'four_hour_retest', before_close)[0]
    assert result['state'] == 'WAITING_FOR_RETEST' and result['confirmed'] is None
    late = replace(zone, established_at=bars[-2].end-timedelta(minutes=59))
    assert location_events(bars, [late], 'four_hour_retest', NOW) == []


def test_event_invalidates_on_opposite_close_hourly_gap_and_session_change():
    bars, zone = event_bars(), event_zone()
    later = NOW+timedelta(hours=1)
    invalid = location_events(bars+[candle(later, 99, 100, 97, 98, 60)], [zone], 'four_hour_retest', later)[0]
    # This new opposite crossing is a new origin, never the old setup revived.
    assert invalid['id'] != location_events(bars, [zone], 'four_hour_retest', NOW)[0]['id']
    assert invalid['state'] == 'WAITING_FOR_RETEST'
    gap_bars = bars[:2]+[candle(later, 102, 105, 100, 104, 60)]
    gap = location_events(gap_bars, [zone], 'four_hour_retest', later)[0]
    assert gap['state'] == 'INVALIDATED' and 'missing' in gap['reason']
    # v5: the next session still carries the event; the one after does not.
    assert location_events(bars, [zone], 'four_hour_retest', NOW+timedelta(days=1))[0]['state'] == 'CONFIRMING'
    assert location_events(bars, [zone], 'four_hour_retest', NOW+timedelta(days=2)) == []


def test_previous_day_sweep_holds_its_origin_and_has_explicit_invalidation():
    market, _, _, now = setup_scenario('short')
    zone = Zone(110, 110, now-timedelta(days=1), 'previous-day high')
    first = location_events(market.bars[60], [zone], 'prior_day_sweep', now)[0]
    later = now+timedelta(hours=1)
    following = market.bars[60]+[candle(later, 101, 109, 100, 103, 60)]
    active = location_events(following, [zone], 'prior_day_sweep', later)[0]
    assert active['id'] == first['id'] and active['confirmed'] == first['confirmed']
    broken = market.bars[60]+[candle(later, 100, 101, 96, 98, 60)]
    assert location_events(broken, [zone], 'prior_day_sweep', later)[0]['state'] == 'INVALIDATED'


def test_both_methods_are_reported_even_when_four_hour_method_also_passes():
    market, leaders, vix, now = setup_scenario('short', 'four_hour_retest')
    market.bars[1440] = [candle(now-timedelta(days=1), high=109.5, low=90, minutes=1440)]
    market.previous_session = '2026-09-15'
    result = analyze(market, leaders, vix, now)
    branches = {row['id']: row for row in result['strategies']}
    assert all(row['state'] == 'SETUP_READY' for row in branches.values())
    assert branches['four_hour_retest']['event_id'] != branches['prior_day_sweep']['event_id']
    assert result['strategy_id'] in branches
    assert {c['strategy_id'] for c in result['entry_candidates']} == set(branches)
    assert {c['event_id'] for c in result['entry_candidates']} >= {r['event_id'] for r in branches.values()}
    assert all('leader_evidence' not in c and 'entry_candidates' not in c for c in result['entry_candidates'])


def test_all_ready_areas_survive_selection_for_durable_executor_deduplication():
    market, leaders, vix, now = setup_scenario('short', 'four_hour_retest')
    market.bars[60] = [candle(now-timedelta(hours=2), 104, 105, 103, 104, 60),
                       candle(now-timedelta(hours=1), 104, 112, 103, 111, 60),
                       candle(now, 110.5, 112, 105, 111, 60)]
    result = analyze(market, leaders, vix, now)
    candidates = [c for c in result['entry_candidates'] if c['strategy_id'] == 'four_hour_retest']
    assert len(candidates) >= 2
    assert len({c['event_id'] for c in candidates}) == len(candidates)
    assert all(c['state'] == 'SETUP_READY' and all(check['passed'] for check in c['checks']) for c in candidates)
    assert result['event_id'] == candidates[0]['event_id']


def test_a_waiting_four_hour_method_cannot_hide_a_ready_previous_day_sweep():
    market, leaders, vix, now = setup_scenario('long')
    market.bars[240] = four_hour_history()
    result = analyze(market, leaders, vix, now)
    branches = {row['id']: row for row in result['strategies']}
    assert branches['four_hour_retest']['state'] != 'SETUP_READY'
    assert branches['prior_day_sweep']['state'] == 'SETUP_READY'
    assert result['strategy_id'] == 'prior_day_sweep' and result['state'] == 'SETUP_READY'


def test_event_id_is_independent_of_leader_direction_and_future_target_geometry():
    market, leaders, vix, now = setup_scenario('long')
    first = analyze(market, leaders, vix, now)
    # Opposite leader confirmation changes admission direction, not Nasdaq event.
    leaders = {symbol: leader_market(symbol, 'short') for symbol in MAG7}
    second = analyze(market, leaders, None, now)
    assert first['event_id'] == second['event_id']
    assert first['direction'] == 'long' and second['direction'] == 'short'
    future = candle(now+timedelta(hours=4), 100, 101, 99, 100, 240)
    market.bars[240].append(future)
    assert analyze(market, leaders, None, now)['event_id'] == first['event_id']


@pytest.mark.parametrize('direction', ['long', 'short'])
@pytest.mark.parametrize('agreeing,opposing,expected', [
    (4, 1, True), (4, 2, False), (3, 1, False), (3, 0, False),
    (7, 0, True), (6, 1, True), (5, 1, True), (5, 2, False), (4, 0, True),
])
def test_leader_vote_requires_four_companies_and_allows_one_opposing(direction, agreeing, opposing, expected):
    opposite = 'short' if direction == 'long' else 'long'
    leaders = {symbol: leader_market(symbol, direction if i < agreeing else
                                    opposite if i < agreeing+opposing else None)
               for i, symbol in enumerate(MAG7)}
    assert leader_confirmation(leaders, direction, NOW)[0] is expected


def test_five_agreeing_and_one_opposing_cannot_hide_a_missing_seventh_leader():
    leaders = {symbol: leader_market(symbol, 'long' if i < 5 else 'short')
               for i, symbol in enumerate(MAG7[:6])}
    okay, reason = leader_confirmation(leaders, 'long', NOW)
    assert not okay and reason == 'TSLA: current 5-minute data missing'


@pytest.mark.parametrize('direction', ['long', 'short'])
def test_five_one_neutral_can_qualify_when_the_other_strategy_gates_pass(direction):
    market, leaders, vix, now = setup_scenario(direction)
    opposite = 'short' if direction == 'long' else 'long'
    leaders['GOOGL'] = leader_market('GOOGL', opposite)
    leaders['TSLA'] = leader_market('TSLA', None)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == direction
    assert sum(row['vote'] == direction for row in result['leader_evidence'].values()) == 5
    assert sum(row['vote'] == opposite for row in result['leader_evidence'].values()) == 1


def test_leader_confirmation_does_not_combine_different_latest_candle_times():
    now = NOW + timedelta(seconds=30)
    leaders = {
        symbol: leader_market(symbol, 'long' if i < 5 else None,
                              at=NOW if i < 5 else NOW-timedelta(minutes=5))
        for i, symbol in enumerate(MAG7)
    }
    for market in leaders.values():
        market.observed_at = now
    rows = leader_diagnostics(leaders, now)
    assert all(row['latest_bar_at'] for row in rows.values())  # each passes freshness
    assert len({row['latest_bar_at'] for row in rows.values()}) == 2
    okay, reason = leader_confirmation(leaders, 'long', now)
    assert not okay and 'not synchronized' in reason
    # Once the other companies publish the same closing timestamp, admission
    # uses the current evidence without rewinding an already available candle.
    for symbol in MAG7[5:]:
        leaders[symbol] = leader_market(symbol, None)
        leaders[symbol].observed_at = now
    assert leader_confirmation(leaders, 'long', now)[0]


def test_common_observation_time_does_not_require_simultaneous_reactions():
    leaders = {symbol: leader_market(symbol, None) for symbol in MAG7}
    for symbol, age in zip(MAG7[:5], (0, 5, 10, 0, 5)):
        market = leader_market(symbol, at=NOW-timedelta(minutes=age))
        for minutes in range(age-5, -1, -5):
            market.bars[5].append(candle(NOW-timedelta(minutes=minutes), 106, 106.05, 105.95, 106))
        market.observed_at = NOW
        leaders[symbol] = market
    rows = leader_diagnostics(leaders, NOW, setup_at=NOW-timedelta(minutes=20))
    assert {rows[symbol]['age_minutes'] for symbol in MAG7[:5]} == {0, 5, 10}
    assert len({row['latest_bar_at'] for row in rows.values()}) == 1
    assert leader_confirmation(leaders, 'long', NOW, setup_at=NOW-timedelta(minutes=20))[0]
    # Equivalent timezone representations still describe the same observation.
    eastern = leaders['AAPL']
    from zoneinfo import ZoneInfo
    eastern.bars[5] = [replace(bar, end=bar.end.astimezone(ZoneInfo('America/New_York')))
                       for bar in eastern.bars[5]]
    assert leader_confirmation(leaders, 'long', NOW, setup_at=NOW-timedelta(minutes=20))[0]


def test_ready_candidate_expires_with_its_earliest_counted_leader_reaction():
    market, leaders, vix, now = setup_scenario('long', 'four_hour_retest')
    # Three of the five agreeing reactions are ten minutes old, so their
    # shared deadline ends the four-company quorum when it passes.
    for symbol, age in zip(MAG7, (10, 10, 10, 0, 0, None, None)):
        leader = leader_market(symbol, 'long' if age is not None else None,
                               at=NOW-timedelta(minutes=age or 0))
        for minutes in range((age or 0)-5, -1, -5):
            leader.bars[5].append(candle(NOW-timedelta(minutes=minutes), 106, 106.05, 105.95, 106))
        leader.observed_at = now
        leaders[symbol] = leader
    result = analyze(market, leaders, vix, now, SHORT_WINDOW)
    deadline = now+timedelta(minutes=5)
    assert result['state'] == 'SETUP_READY'
    assert result['leader_observations_synchronized'] is True
    assert result['leader_observation_at'] == now.isoformat()
    assert result['leader_evidence_valid_until'] == deadline.isoformat()
    assert all(candidate['leader_evidence_valid_until'] == deadline.isoformat()
               for candidate in result['entry_candidates'])
    assert result['leader_evidence']['AAPL']['evidence_valid_until'] == deadline.isoformat()
    assert result['leader_evidence']['TSLA']['evidence_valid_until'] is None
    # Refreshing receipts cannot extend the selected reaction's wall-clock life.
    for observed_at in (deadline-timedelta(seconds=1), deadline):
        for observed in (market, vix, *leaders.values()):
            observed.observed_at = observed_at
        checked = analyze(market, leaders, vix, observed_at, SHORT_WINDOW)
        if observed_at < deadline:
            assert checked['state'] == 'SETUP_READY'
            assert checked['leader_evidence_valid_until'] == deadline.isoformat()
        else:
            assert checked['state'] != 'SETUP_READY'
            assert checked['leader_evidence']['AAPL']['vote'] is None


@pytest.mark.parametrize('persistence_bars', [1, 3, 12, 24])
def test_candidate_leader_deadline_uses_the_declared_persistence_policy(persistence_bars):
    market, leaders, vix, now = setup_scenario()
    policy = AnalysisPolicy(persistence_bars=persistence_bars)
    result = analyze(market, leaders, vix, now, policy)
    assert result['state'] == 'SETUP_READY'
    assert result['leader_evidence_valid_until'] == (now+timedelta(minutes=5*persistence_bars)).isoformat()
    assert result['leader_rule'] == {'minimum_agree': 4, 'maximum_opposing': 1,
                                     'persistence_minutes': 5*persistence_bars}
    assert analyze(market, leaders, vix, now)['leader_rule']['persistence_minutes'] == 60


def test_candidate_deadline_includes_the_permitted_opposing_vote():
    market, leaders, vix, now = setup_scenario('long', 'four_hour_retest')
    opposing = leader_market('GOOGL', 'short', at=now-timedelta(minutes=10))
    opposing.bars[5] += [candle(now-timedelta(minutes=5), 94, 94.05, 93.95, 94),
                         candle(now, 94, 94.05, 93.95, 94)]
    opposing.observed_at = now
    leaders['GOOGL'] = opposing
    leaders['TSLA'] = leader_market('TSLA', None)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY'
    assert result['leader_evidence']['GOOGL']['vote'] == 'short'
    assert result['leader_evidence_valid_until'] == (now+timedelta(minutes=50)).isoformat()


def test_candidate_exposes_observation_expiry_before_the_reaction_window_ends():
    market, leaders, vix, base = setup_scenario()
    checked_at = base+timedelta(minutes=6, seconds=20)
    for item in (market, vix, *leaders.values()):
        item.observed_at = checked_at
    result = analyze(market, leaders, vix, checked_at)
    observation_deadline = base+timedelta(minutes=5, seconds=CANDLE_PUBLICATION_GRACE_SECONDS)
    reaction_deadline = base+timedelta(minutes=60)
    assert result['state'] == 'SETUP_READY'
    assert result['leader_evidence_valid_until'] == reaction_deadline.isoformat()
    assert result['leader_observation_valid_until'] == observation_deadline.isoformat()
    assert all(row['observation_valid_until'] == observation_deadline.isoformat()
               for row in result['leader_evidence'].values())
    assert all(candidate['leader_observation_valid_until'] == observation_deadline.isoformat()
               for candidate in result['entry_candidates'])
    for at in (observation_deadline-timedelta(seconds=1), observation_deadline,
               observation_deadline+timedelta(seconds=1)):
        for item in (market, vix, *leaders.values()):
            item.observed_at = at
        later = analyze(market, leaders, vix, at)
        if at < observation_deadline:
            assert later['state'] == 'SETUP_READY'
            assert later['leader_observation_valid_until'] == observation_deadline.isoformat()
        else:
            assert later['state'] != 'SETUP_READY'
            assert all(row['vote'] is None for row in later['leader_evidence'].values())
            assert 'current 5-minute data missing' in later['checks'][-1]['detail']


def test_new_candle_refreshes_observation_deadline_without_extending_reaction_age():
    market, leaders, vix, base = setup_scenario()
    checked_at = base+timedelta(minutes=6, seconds=40)
    for leader in leaders.values():
        leader.bars[5].append(candle(base+timedelta(minutes=5), 106, 106.05, 105.95, 106))
    for item in (market, vix, *leaders.values()):
        item.observed_at = checked_at
    result = analyze(market, leaders, vix, checked_at)
    assert result['state'] == 'SETUP_READY'
    assert result['leader_observation_at'] == (base+timedelta(minutes=5)).isoformat()
    assert result['leader_observation_valid_until'] == (
        base+timedelta(minutes=10, seconds=CANDLE_PUBLICATION_GRACE_SECONDS)).isoformat()
    assert result['leader_evidence_valid_until'] == (base+timedelta(minutes=60)).isoformat()
    assert all(row['reaction_at'] == base.isoformat() for row in result['leader_evidence'].values())


def test_conflicting_active_reactions_within_one_company_are_neutral_not_a_veto():
    market = leader_market()
    market.bars[5] = [b for b in market.bars[5] if b.end < NOW-timedelta(minutes=15)]
    market.bars[5] += [candle(NOW-timedelta(minutes=15), 99, 100, 98, 99),
                       candle(NOW-timedelta(minutes=10), 99, 101, 89.9, 100),
                       candle(NOW-timedelta(minutes=5), 106, 107, 105.5, 106),
                       candle(NOW, 106, 110.2, 102, 103)]
    row = leader_diagnostics({'AAPL': market}, NOW, setup_at=NOW-timedelta(minutes=20))['AAPL']
    assert row['vote'] is None and row['conflicting_reactions']
    assert row['reason'] == 'conflicting active area reactions'
    # v4: the mixed company is skipped (V2: mixed evidence, no trade from it),
    # so five agreeing companies still confirm; the conflict stays reported.
    leaders = {symbol: leader_market(symbol) for symbol in MAG7}
    leaders['AAPL'] = market
    leaders['TSLA'] = leader_market('TSLA', None)
    okay, reason = leader_confirmation(leaders, 'long', NOW, setup_at=NOW-timedelta(minutes=20))
    assert okay and 'AAPL: conflicting, neutral' in reason
    # v5: four agreeing plus the neutral company meets the quorum; three do not.
    leaders['GOOGL'] = leader_market('GOOGL', None)
    assert leader_confirmation(leaders, 'long', NOW, setup_at=NOW-timedelta(minutes=20))[0]
    leaders['META'] = leader_market('META', None)
    assert not leader_confirmation(leaders, 'long', NOW, setup_at=NOW-timedelta(minutes=20))[0]


def test_future_five_minute_area_or_price_cannot_change_earlier_vote():
    market = leader_market()
    before = leader_diagnostics({'AAPL': market}, NOW)
    market.bars[5] += [candle(NOW+timedelta(minutes=5), 105, 200, 1, 199),
                       candle(NOW+timedelta(minutes=10), 199, 200, 1, 2)]
    assert leader_diagnostics({'AAPL': market}, NOW) == before


def test_same_bar_poll_keeps_evidence_age_stable_until_wall_clock_expiry():
    market = leader_market()
    first = leader_diagnostics({'AAPL': market}, NOW)
    market.observed_at = NOW+timedelta(seconds=30)
    assert leader_diagnostics({'AAPL': market}, market.observed_at) == first


def test_vix_area_reaction_and_freshness_primitives_retain_frozen_v1_safeguards():
    # v4 changes which candles may hold the VIX reaction and the area width,
    # not VIX entitlement, provider freshness, the inverse-reaction geometry
    # or the swing-area construction itself (vix_confirmation is versioned).
    import inspect
    from pivot import strategy
    from research import baseline_v1
    for name in ('zones', 'reaction', 'vix_candles_fresh'):
        current, frozen = getattr(strategy, name), getattr(baseline_v1, name)
        assert inspect.getsource(current) == inspect.getsource(frozen)


def test_historical_baseline_keeps_fifteen_minute_votes_and_v1_identity_separate():
    from research.baseline_v1 import analyze as historical_analyze
    from research.synthetic_example import accepted_long
    old = accepted_long()
    assert old['setup']['policy_version'] == 'nasdaq-video-interpretation-v1'
    assert old['setup']['state'] == 'SETUP_READY'
    market, leaders, vix, now = setup_scenario()
    for leader in leaders.values():
        leader.bars[15] = [candle(now-timedelta(minutes=15), minutes=15),
                           candle(now, high=105, low=89.9, close=104, minutes=15)]
        leader.bars.pop(5)
    assert historical_analyze(market, leaders, vix, now)['state'] == 'SETUP_READY'
    assert analyze(market, leaders, vix, now)['state'] != 'SETUP_READY'
