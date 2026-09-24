"""v5 witnesses for the 18:11 recording re-read: swing areas, return retests, two-session events,
leader period levels and VWAP, the four-of-seven quorum, the 60-minute window and VIX bases.

Every series is manufactured. None of this is evidence that the videos define
these numbers or that the rules are profitable; the executor still owns
permission and broker checks.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pivot import execution
from pivot.execution import signal_expiry
from pivot.models import Bar, Market, MAG7, Zone
from pivot.policy import POLICY, POLICY_VERSION
from pivot.rulebook import rulebook
from pivot.strategy import (ANALYSIS_VERSION, ET, MAX_PERSISTENCE_BARS, AnalysisPolicy, BASELINE_POLICY, analyze,
                            event_expiry, interaction_zones, leader_confirmation, leader_diagnostics, location_events,
                            period_levels, session_vwap_zone, swing_areas, vix_base_zones, vix_confirmation,
                            vix_diagnostics)
from pivot.tests.test_execution import ready
from pivot.tests.test_strategy_v2 import NOW, candle, four_hour_history, leader_market, setup_scenario

BASE = datetime(2026, 8, 3, 13, 30, tzinfo=ET)


def bar4(index, high, low, opened=None, close=None):
    opened = (high + low) / 2 if opened is None else opened
    close = (high + low) / 2 if close is None else close
    return Bar(BASE + timedelta(hours=4 * index), 240, opened, high, low, close)


def hourly(day, hour, minute, opened, high, low, close):
    return Bar(datetime(2026, 9, day, hour, minute, tzinfo=ET), 60, opened, high, low, close)


# --- four-hour swing areas ---------------------------------------------------------

def test_swing_areas_require_a_confirmed_pivot_unlike_the_leader_interaction_builder():
    rising = [bar4(i, 105 + i, 90 + i, 99 + i, 101 + i) for i in range(8)]
    assert interaction_zones(rising, NOW)  # every extreme is a candidate there
    assert swing_areas(rising, NOW) == []  # no bar's high or low beats both neighbours


def test_second_nonadjacent_interaction_establishes_a_swing_area_with_frozen_bounds():
    bars = [bar4(0, 105, 95), bar4(1, 110, 94), bar4(2, 105, 95), bar4(3, 105, 96), bar4(4, 105, 96),
            bar4(5, 109.95, 100), bar4(6, 111, 109.5, 110, 110.5), bar4(7, 108, 104, 107, 105)]
    after = bars[-1].end + timedelta(hours=4)
    # The 110 pivot (confirmed by bar 2) is only a candidate until bar 5 touches its band.
    assert swing_areas(bars[:5], after) == []
    [area] = swing_areas(bars[:6], after)
    assert area.source == '4h swing area' and area.touches == 2
    assert area.low == pytest.approx(110 * 0.999) and area.high == pytest.approx(110 * 1.001)
    assert area.established_at == bars[5].end
    # Bar 6 is adjacent to the last touch and does not count; bar 7 closes
    # across the band from above (a crossing) and counts as a third touch.
    [later] = swing_areas(bars, after)
    assert later.touches == 3 and (later.low, later.high, later.established_at) == (area.low, area.high, area.established_at)
    # A bar closed after the evaluation point can neither create nor move an area.
    future = bar4(8, 200, 1, 100, 150)
    assert swing_areas(bars + [future], after) == [later]
    assert swing_areas(bars + [future], future.end + timedelta(hours=4)) != [later]


def test_candidates_within_twice_the_tolerance_cluster_and_the_touch_count_is_a_policy_field():
    bars = [bar4(0, 99, 95), bar4(1, 100, 96), bar4(2, 99, 97), bar4(3, 99.5, 97), bar4(4, 100.15, 98),
            bar4(5, 99, 97), bar4(6, 99, 97), bar4(7, 100.2, 99)]
    after = bars[-1].end + timedelta(hours=4)
    # Default two touches: bar 4 touches the 100 band and establishes it before its own pivot merges.
    [two] = swing_areas(bars[:5], after)
    assert two.established_at == bars[4].end and two.high == pytest.approx(100.1)
    # Three touches: the 100.15 pivot merges first (0.15% away, within 0.2%),
    # widening the unestablished cluster, and bar 7 establishes it.
    assert swing_areas(bars[:7], after, touches=3) == []
    [three] = swing_areas(bars, after, touches=3)
    assert three.touches == 3 and three.established_at == bars[7].end
    assert three.low == pytest.approx(100 * 0.999) and three.high == pytest.approx(100.15 * 1.001)
    with pytest.raises(ValueError):
        AnalysisPolicy(level_touches=0)


def test_only_the_areas_nearest_the_reference_price_are_kept():
    bars = []
    for k in range(6):
        price = 100 + 2 * k
        for high in (price - 5, price, price - 5, price - 5, price - 0.02):
            bars.append(bar4(len(bars), high, 80))
    after = bars[-1].end + timedelta(hours=4)
    everything = swing_areas(bars, after, reference=105)
    assert [round(z.mid) for z in everything] == [100, 102, 104, 106, 108, 110]
    nearest = swing_areas(bars, after, limit=4, reference=105)
    assert [round(z.mid) for z in nearest] == [102, 104, 106, 108]
    for bad in ({'max_areas': 0}, {'max_areas': 65}, {'max_areas': 2.0}):
        with pytest.raises(ValueError):
            AnalysisPolicy(**bad)


def test_analyzer_uses_swing_areas_established_before_the_origin_and_reports_the_rule():
    market, leaders, vix, now = setup_scenario('short', 'four_hour_retest')
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY'
    assert {z['source'] for z in result['levels']} == {'4h swing area'}
    assert sorted(round(z['low']) for z in result['levels']) == [90, 94, 106, 110]
    assert result['area_rule'] == {'zone_tolerance': 0.001, 'touches': 2, 'max_areas': 16}
    assert result['event_zone']['low'] == pytest.approx(110 * 0.999)
    # With a cap of one, only the area nearest the latest close (111) survives.
    capped = analyze(market, leaders, vix, now, AnalysisPolicy(max_areas=1))
    assert [round(z['low']) for z in capped['levels']] == [110]


# --- retest by return, direction from leaders -------------------------------------

def test_a_return_to_the_broken_edge_confirms_the_event_whatever_the_candle_closes():
    zone = Zone(99.9, 100.1, NOW - timedelta(days=3), '4h swing area')
    bars = [candle(NOW - timedelta(hours=2), 99, 100, 98, 99, 60),
            candle(NOW - timedelta(hours=1), 99, 103, 98, 102, 60)]
    waiting = location_events(bars, [zone], 'four_hour_retest', NOW - timedelta(hours=1))[0]
    assert waiting['state'] == 'WAITING_FOR_RETEST' and waiting['break_direction'] == 'long'
    assert 'return to the level' in waiting['reason']
    # A candle that stays above the area is not a retest.
    away = bars + [candle(NOW, 102, 103, 101, 102.5, 60)]
    assert location_events(away, [zone], 'four_hour_retest', NOW)[0]['state'] == 'WAITING_FOR_RETEST'
    # A red candle whose low reaches the area high confirms (v4 needed a green close above it).
    fade = bars + [candle(NOW, 102, 102.5, 100.05, 100.3, 60)]
    event = location_events(fade, [zone], 'four_hour_retest', NOW)[0]
    assert event['state'] == 'CONFIRMING' and event['confirmed'] == fade[-1]
    assert event['id'] == waiting['id']
    # A close through the far edge before the return still invalidates and starts an opposite break.
    through = bars + [candle(NOW, 101, 101.5, 99, 99.5, 60)]
    fresh_break = location_events(through, [zone], 'four_hour_retest', NOW)[0]
    assert fresh_break['state'] == 'WAITING_FOR_RETEST' and fresh_break['break_direction'] == 'short'
    assert fresh_break['id'] != waiting['id']


def test_failed_break_short_takes_its_direction_from_the_leaders_and_vix():
    market, leaders, vix, now = setup_scenario('short', 'four_hour_retest')
    # Price broke above the 110 area and now returns to it on a red hourly candle.
    market.bars[60][-1] = candle(now, 111, 111.5, 110.0, 110.2, 60)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == 'short'
    assert result['event'] == 'break and retest' and result['retest_at'] == result['event_at'] == now.isoformat()
    assert result['entry'] == 110.2 and result['stop'] == 112.01 and result['target'] == 106.11
    assert result['event_invalidation_price'] == pytest.approx(110 * 0.999)
    leader_check = next(c for c in result['checks'] if c['name'] == 'Magnificent Seven at their zones')
    assert 'AAPL: short at 5m repeated interaction' in leader_check['detail']
    # The same location with buying leaders and a falling VIX is a long; the
    # previous-day high supplies the opposing target the swing areas lack above.
    long_leaders = {symbol: leader_market(symbol, 'long') for symbol in MAG7}
    long_vix = setup_scenario('long')[2]
    market.bars[1440] = [candle(now - timedelta(days=1), 100, 130, 80, 100, 1440)]
    market.previous_session = '2026-09-15'
    flipped = analyze(market, long_leaders, long_vix, now)
    assert flipped['state'] == 'SETUP_READY' and flipped['direction'] == 'long'
    assert flipped['strategy_id'] == 'four_hour_retest' and flipped['event_id'] == result['event_id']
    assert flipped['stop'] == 106.99 and flipped['target'] == 130


# --- two-session lifetime ---------------------------------------------------------

def test_event_expiry_is_the_close_of_the_next_weekday_session():
    wednesday = datetime(2026, 9, 16, 15, 30, tzinfo=ET)
    assert event_expiry(wednesday) == datetime(2026, 9, 17, 16, tzinfo=ET)
    assert event_expiry(wednesday, 1) == datetime(2026, 9, 16, 16, tzinfo=ET)
    friday = datetime(2026, 9, 18, 15, 30, tzinfo=ET)
    assert event_expiry(friday) == datetime(2026, 9, 21, 16, tzinfo=ET)
    assert event_expiry(friday, 3) == datetime(2026, 9, 22, 16, tzinfo=ET)
    assert event_expiry(friday.astimezone(timezone.utc)) == event_expiry(friday)
    for bad in ({'event_sessions': 0}, {'event_sessions': 6}, {'event_sessions': True}):
        with pytest.raises(ValueError):
            AnalysisPolicy(**bad)


def test_the_retest_can_arrive_in_the_next_session_but_not_across_a_missing_hourly_candle():
    zone = Zone(99.9, 100.1, datetime(2026, 9, 10, 16, tzinfo=ET), '4h swing area')
    tuesday_break = [hourly(15, 14, 30, 99, 100, 98, 99), hourly(15, 15, 30, 99, 103, 98, 102)]
    wednesday_return = hourly(16, 10, 30, 102, 103, 100.05, 101)
    now = datetime(2026, 9, 16, 12, tzinfo=ET)
    event = location_events(tuesday_break + [wednesday_return], [zone], 'four_hour_retest', now)[0]
    assert event['state'] == 'CONFIRMING' and event['origin'] == tuesday_break[-1]
    assert event['confirmed'] == wednesday_return
    assert event['expires_at'] == datetime(2026, 9, 16, 16, tzinfo=ET)
    # Wednesday's first hourly candle missing: the return on the 11:30 candle is not contiguous.
    late = location_events(tuesday_break + [hourly(16, 11, 30, 102, 103, 100.05, 101)], [zone], 'four_hour_retest', now)[0]
    assert late['state'] == 'INVALIDATED' and 'missing' in late['reason']
    # Tuesday's last hourly candle missing after the break: the boundary is not proven continuous.
    broken = [hourly(15, 13, 30, 99, 100, 98, 99), hourly(15, 14, 30, 99, 103, 98, 102), wednesday_return]
    assert location_events(broken, [zone], 'four_hour_retest', now)[0]['state'] == 'INVALIDATED'
    # An early-close session ending at 12:30 still joins the next 10:30 candle.
    early = [hourly(15, 11, 30, 99, 100, 98, 99), hourly(15, 12, 30, 99, 103, 98, 102), wednesday_return]
    assert location_events(early, [zone], 'four_hour_retest', now)[0]['state'] == 'CONFIRMING'
    # Thursday: the Tuesday origin has expired and reports nothing.
    assert location_events(tuesday_break + [wednesday_return], [zone], 'four_hour_retest',
                           datetime(2026, 9, 17, 10, tzinfo=ET)) == []


def test_next_session_retest_qualifies_in_analysis_and_passes_the_executor_contract():
    now = datetime(2026, 9, 16, 11, tzinfo=ET)  # 15:00 UTC, one hour before the shared NOW fixture
    bars = [hourly(15, 14, 30, 108, 109, 107, 108), hourly(15, 15, 30, 108, 112, 107, 111),
            hourly(16, 10, 30, 111, 112, 110.05, 110.5)]
    market = Market('QQQ', {240: four_hour_history(), 60: bars}, 'alpaca_iex', True, now)
    leaders = {symbol: leader_market(symbol, 'short', at=now) for symbol in MAG7}
    fixture = setup_scenario('short')[2]
    vix = replace(fixture, observed_at=now, bars={15: [replace(b, end=b.end - timedelta(hours=1)) for b in fixture.bars[15]]})
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == 'short'
    assert result['event_origin_at'] == bars[1].end.isoformat()
    assert result['retest_at'] == result['event_at'] == bars[2].end.isoformat()
    assert result['event_expires_at'] == datetime(2026, 9, 16, 16, tzinfo=ET).isoformat()
    assert result['event_rule'] == {'sessions': 2}
    assert execution.SIGNAL_POLICY_VERSION == ANALYSIS_VERSION
    deadline = signal_expiry(result, now)
    assert deadline == now + timedelta(seconds=390)  # the leader observation allowance is the earliest clock
    # The executor still refuses an expiry beyond the declared lifetime, one before its origin,
    # a leader deadline beyond the longest window, or hourly evidence from another New York date.
    for changes in ({'event_expires_at': (datetime(2026, 9, 17, 16, tzinfo=ET)).isoformat()},
                    {'event_expires_at': bars[1].end.isoformat()},
                    {'leader_evidence_valid_until': (now + timedelta(minutes=5 * MAX_PERSISTENCE_BARS, seconds=1)).isoformat()}):
        with pytest.raises(execution.Waiting):
            signal_expiry({**result, **changes}, now)
    with pytest.raises(execution.ExpiredSignal):
        signal_expiry({**result, 'event_at': bars[1].end.isoformat(), 'latest_evidence_at': bars[1].end.isoformat()}, now)
    with pytest.raises(execution.ExpiredSignal):
        signal_expiry(result, datetime(2026, 9, 16, 16, tzinfo=ET))
    snapshot = ready()
    snapshot['setup']['leader_evidence_valid_until'] = (NOW + timedelta(minutes=60)).isoformat()
    assert signal_expiry(snapshot['setup'], NOW) == NOW + timedelta(seconds=390)


# --- leader period levels and VWAP ------------------------------------------------

def daily_bar(month, day, k):
    end = datetime(2026, month, day, 16, tzinfo=ET)
    return Bar(end, 1440, 150 + k, 200 + k, 100 + k, 160 + k)


def session_bars(month, day, base, until=(16, 0), vwap=False):
    bars, at = [], datetime(2026, month, day, 9, 35, tzinfo=ET)
    end = datetime(2026, month, day, *until, tzinfo=ET)
    while at <= end:
        bars.append(Bar(at, 5, base + 0.5, base + 2, base - 2, base, 1000 if vwap else 0, base if vwap else None))
        at += timedelta(minutes=5)
    return bars


def sources(zones):
    return {zone.source: zone.mid for zone in zones}


def test_period_levels_from_daily_bars_with_a_five_minute_session_open():
    # Daily bars from Monday August 31 through Tuesday September 15; today is Wednesday the 16th.
    days = [(8, 31, 0)] + [(9, d, d) for d in (1, 2, 3, 4, 8, 9, 10, 11, 14, 15)]
    daily = [daily_bar(*row) for row in days]
    today = session_bars(9, 16, 300, until=(11, 0))
    market = Market('AAPL', {5: today, 1440: daily}, 'alpaca_iex', True, today[-1].end)
    levels = period_levels(market, today, today[-1].end - timedelta(minutes=5))
    found = sources(levels)
    assert found == pytest.approx({
        'previous-session high': 215, 'previous-session low': 115, 'previous-session close': 175,
        'session open': 300.5, 'previous-week high': 211, 'previous-week low': 108, 'previous-week mid': 159.5,
        'Monday high': 214, 'Monday low': 114, 'week open': 164, 'month open': 151})
    by_source = {zone.source: zone for zone in levels}
    assert by_source['previous-session high'].established_at == datetime(2026, 9, 15, 16, tzinfo=ET)
    assert by_source['previous-week mid'].established_at == datetime(2026, 9, 11, 16, tzinfo=ET)
    assert by_source['session open'].established_at == today[0].end
    assert by_source['month open'].established_at == datetime(2026, 9, 1, 16, tzinfo=ET)
    assert all(zone.high / zone.low == pytest.approx(1.001 / 0.999) and zone.touches == 1 for zone in levels)
    # Without history before this month or the previous Monday, those levels are skipped rather than guessed.
    short = Market('AAPL', {5: today, 1440: daily[6:]}, 'alpaca_iex', True, today[-1].end)
    trimmed = sources(period_levels(short, today, today[-1].end - timedelta(minutes=5)))
    assert set(trimmed) == {'previous-session high', 'previous-session low', 'previous-session close', 'session open',
                            'Monday high', 'Monday low', 'week open'}
    # The leader diagnostics expose the same areas beside the five-minute interaction bands.
    row = leader_diagnostics({'AAPL': market}, today[-1].end)['AAPL']
    assert {'previous-session high', 'month open'} <= {zone['source'] for zone in row['zones']}


def test_period_levels_from_five_minute_history_only_derive_what_the_window_covers():
    week = [(9, 8, 108), (9, 9, 109), (9, 10, 110), (9, 11, 111), (9, 14, 114), (9, 15, 115)]
    bars = [bar for month, day, base in week for bar in session_bars(month, day, base)]
    today = session_bars(9, 16, 116, until=(11, 0))
    history = bars + today
    market = Market('AAPL', {5: history}, 'alpaca_iex', True, today[-1].end)
    found = sources(period_levels(market, history, today[-1].end - timedelta(minutes=5)))
    # Seven sessions reach back to Tuesday the 8th: the previous week's Monday is not covered,
    # nor is any session from August, so previous-week and month-open levels are skipped.
    assert found == pytest.approx({'previous-session high': 117, 'previous-session low': 113,
                                   'previous-session close': 115, 'session open': 116.5,
                                   'Monday high': 116, 'Monday low': 112, 'week open': 114.5})
    # History back to Friday the 4th covers the previous week (Labor Day Monday absent from the calendar).
    longer = session_bars(9, 4, 104) + history
    found = sources(period_levels(Market('AAPL', {5: longer}, 'alpaca_iex', True, today[-1].end), longer,
                                  today[-1].end - timedelta(minutes=5)))
    # Yesterday's low and last week's high share one price, so one area carries both sources.
    assert found['previous-session low / previous-week high'] == pytest.approx(113)
    assert found['previous-week low'] == pytest.approx(106) and found['previous-week mid'] == pytest.approx(109.5)
    # A partial session that does not start at 09:35 supplies no open.
    partial = today[3:]
    assert 'session open' not in sources(period_levels(Market('AAPL', {5: bars + partial}, 'alpaca_iex', True, partial[-1].end),
                                                       bars + partial, partial[-1].end - timedelta(minutes=5)))
    # Missing frames[1440] on the manufactured v2 leader fixtures still yields the interaction areas only.
    assert {z['source'] for z in leader_diagnostics({'AAPL': leader_market()}, NOW)['AAPL']['zones']} == {'5m repeated interaction'}


def test_session_vwap_area_is_built_from_the_candles_before_the_previous_one_and_can_hold_the_vote():
    # The 09:35 candle opens at 99.6 so the session-open area sits apart from the VWAP area.
    flat = [Bar(datetime(2026, 9, 16, 9, 35, tzinfo=ET) + timedelta(minutes=5 * i), 5, 99.6 if i == 0 else 100,
                100.5, 99.5, 100, 1000, 100) for i in range(10)]
    dip = Bar(flat[-1].end + timedelta(minutes=5), 5, 100, 100.6, 99.4, 100, 1000, 100)
    previous = Bar(dip.end + timedelta(minutes=5), 5, 100, 100.4, 99.8, 99.95, 1000, 100)
    current = Bar(previous.end + timedelta(minutes=5), 5, 99.95, 100.6, 99.95, 100.5, 1000, 100.2)
    bars = flat + [dip, previous, current]
    zone = session_vwap_zone(bars, len(bars) - 1)
    assert zone.source == 'session VWAP' and zone.mid == pytest.approx(100) and zone.touches == 11
    assert zone.established_at == dip.end  # known a full candle before the reaction candle opens
    assert session_vwap_zone(bars, 1) is None and session_vwap_zone(bars, 2).touches == 1
    market = Market('AAPL', {5: bars}, 'alpaca_iex', True, current.end)
    row = leader_diagnostics({'AAPL': market}, current.end)['AAPL']
    assert row['vote'] == 'long' and row['vote_source'] == 'session VWAP'
    assert row['reaction_zone']['source'] == 'session VWAP' and row['reaction_at'] == current.end.isoformat()
    assert any(z['source'] == 'session VWAP' for z in row['zones'])
    # Candles without VWAP or volume carry no VWAP area, so the same tape has no vote.
    bare = Market('AAPL', {5: [replace(b, volume=0, vwap=None) for b in bars]}, 'alpaca_iex', True, current.end)
    assert leader_diagnostics({'AAPL': bare}, current.end)['AAPL']['vote'] is None


# --- quorum and window ----------------------------------------------------------

def test_four_agreeing_leaders_with_neutral_companies_confirm_and_mixed_evidence_does_not():
    assert (BASELINE_POLICY.minimum_leaders, BASELINE_POLICY.maximum_opposition, BASELINE_POLICY.persistence_bars) == (4, 1, 12)
    market, leaders, vix, now = setup_scenario('long')
    for symbol in MAG7[4:]:
        leaders[symbol] = leader_market(symbol, None)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == 'long'
    detail = next(c for c in result['checks'] if c['name'] == 'Magnificent Seven at their zones')['detail']
    assert 'TSLA: no zone reaction, neutral' in detail and 'AAPL: long at 5m repeated interaction' in detail
    assert result['leader_rule'] == {'minimum_agree': 4, 'maximum_opposing': 1, 'persistence_minutes': 60}
    leaders['META'] = leader_market('META', 'short')
    assert analyze(market, leaders, vix, now)['state'] == 'SETUP_READY'  # one opposing is allowed
    leaders['GOOGL'] = leader_market('GOOGL', 'short')
    assert analyze(market, leaders, vix, now)['state'] == 'CONFIRMING'  # majority against mixed evidence
    leaders['GOOGL'] = leader_market('GOOGL', None)
    leaders['AMZN'] = leader_market('AMZN', None)
    assert not leader_confirmation(leaders, 'long', now)[0]  # three agreeing is short of the quorum


def test_persistence_window_accepts_up_to_twenty_four_candles():
    assert AnalysisPolicy(persistence_bars=24) and AnalysisPolicy(persistence_bars=1)
    for bad in ({'persistence_bars': 0}, {'persistence_bars': 25}, {'persistence_bars': 12.0}):
        with pytest.raises(ValueError):
            AnalysisPolicy(**bad)


def held_leader(minutes_ago=55, closes=None):
    market = leader_market(at=NOW - timedelta(minutes=minutes_ago))
    for offset in range(minutes_ago - 5, -1, -5):
        at = NOW - timedelta(minutes=offset)
        market.bars[5].append(closes(at) if closes else candle(at, 106, 106.05, 105.95, 106))
    market.observed_at = NOW
    return market


def test_a_reaction_holds_for_sixty_minutes_and_a_close_back_through_the_area_ends_it_early():
    row = leader_diagnostics({'AAPL': held_leader()}, NOW, setup_at=NOW - timedelta(hours=2))['AAPL']
    assert row['vote'] == 'long' and row['age_minutes'] == 55 and row['reason'] == 'persistent reaction'
    assert row['evidence_valid_until'] == (NOW + timedelta(minutes=5)).isoformat()
    # One candle later the reaction is sixty minutes old and no longer counts.
    aged = held_leader()
    aged.bars[5].append(candle(NOW + timedelta(minutes=5), 106, 106.05, 105.95, 106))
    aged.observed_at = NOW + timedelta(minutes=5)
    expired = leader_diagnostics({'AAPL': aged}, NOW + timedelta(minutes=5), setup_at=NOW - timedelta(hours=2))['AAPL']
    assert expired['vote'] is None and expired['reason'] == 'no zone reaction'
    # A gap-down close below the area thirty minutes ago invalidates the vote for the rest of the window.

    def tape(at):
        if at < NOW - timedelta(minutes=30):
            return candle(at, 106, 106.05, 105.95, 106)
        if at == NOW - timedelta(minutes=30):
            return candle(at, 88.5, 89.5, 88, 89)
        return candle(at, 88.9, 89.5, 88.5, 89)
    invalid = leader_diagnostics({'AAPL': held_leader(closes=tape)}, NOW, setup_at=NOW - timedelta(hours=2))['AAPL']
    assert invalid['vote'] is None and invalid['reason'] == 'invalidated by subsequent close through zone'
    assert invalid['reaction_at'] == (NOW - timedelta(minutes=55)).isoformat()


# --- VIX consolidation bases -----------------------------------------------------

def vix_bar(index, opened, high, low, close):
    return Bar(datetime(2026, 9, 16, 9, 45, tzinfo=ET) + timedelta(minutes=15 * index), 15, opened, high, low, close)


def vix_base_series(reaction=(15.2, 15.6, 15.10, 15.5)):
    rows = [(15.00, 15.10, 14.95, 15.05), (15.05, 15.12, 14.98, 15.02), (15.02, 15.15, 15.00, 15.10),
            (15.10, 15.18, 15.04, 15.12),  # four candles within 1.5%: the base VIX launches from
            (15.12, 16.0, 15.10, 15.9), (15.9, 16.3, 15.7, 16.1), (16.1, 16.2, 15.6, 15.7), (15.7, 15.8, 15.3, 15.4),
            (15.4, 15.45, 15.15, 15.2), reaction]
    return [vix_bar(i, *row) for i, row in enumerate(rows)]


def test_vix_base_zones_are_runs_of_tight_candles_and_overlapping_runs_merge():
    bars = vix_base_series()
    [base] = vix_base_zones(bars, bars[-1].end)
    assert base.source == 'VIX consolidation base' and (base.low, base.high) == (14.95, 15.18)
    assert base.established_at == bars[3].end and base.touches == 4
    assert vix_base_zones(bars, bars[3].end) == []  # not known until the fourth candle has closed
    assert vix_base_zones(bars, bars[-1].end, base_range=0.01) == []  # 1.5% is too wide for a 1% policy
    assert vix_base_zones(bars, bars[-1].end, base_bars=5) == []  # the fifth candle is the spike
    flat = [vix_bar(i, 15.0, 15.1, 14.9, 15.0) for i in range(6)]
    [merged] = vix_base_zones(flat, flat[-1].end + timedelta(minutes=15))
    assert merged.touches == 6 and merged.established_at == flat[-1].end
    gapped = flat[:3] + [replace(b, end=b.end + timedelta(minutes=15)) for b in flat[3:]]
    assert vix_base_zones(gapped, gapped[-1].end + timedelta(minutes=15)) == []
    for bad in ({'vix_base_bars': 1}, {'vix_base_bars': 17}, {'vix_base_range': 0}, {'vix_base_range': 0.2}):
        with pytest.raises(ValueError):
            AnalysisPolicy(**bad)


def test_vix_buying_from_its_base_confirms_a_nasdaq_short_when_no_swing_pivot_area_exists():
    bars = vix_base_series()
    now = bars[-1].end
    vix = Market('I:VIX', {15: bars}, 'massive_indices', True, now)
    evidence = vix_diagnostics(vix, 'short', now)
    assert evidence['okay'] and evidence['zone'].source == 'VIX consolidation base'
    assert [z.source for z in evidence['zones']] == ['VIX consolidation base']
    assert 'VIX long reaction at VIX consolidation base 14.95-15.18' in evidence['detail']
    assert evidence['base_bars'] == 4 and evidence['base_range'] == 0.02
    assert not vix_confirmation(vix, 'long', now)[0]
    assert not vix_confirmation(vix, 'short', now, AnalysisPolicy(vix_base_range=0.01))[0]
    # A wick into the base with a close inside it is not a reaction.
    inside = Market('I:VIX', {15: vix_base_series(reaction=(15.2, 15.3, 15.05, 15.15))}, 'massive_indices', True, now)
    assert not vix_confirmation(inside, 'short', now)[0]


# --- owner-facing text -----------------------------------------------------------

def test_policy_text_states_the_video_aligned_rules_and_the_other_agents_lines():
    assert ANALYSIS_VERSION == 'nasdaq-video-interpretation-v5'
    assert POLICY_VERSION == 'nasdaq-qqq-execution-v8-socrates-4-6' == POLICY['version']
    text = ' '.join(POLICY['summary'])
    # 4.6: shorts are recorded but not traded by default; when re-enabled they still execute through PSQ.
    assert 'a short is executed by buying PSQ, the ProShares inverse (−1x) Nasdaq-100 ETF' in text
    assert 'PSQ is held intraday only' in text and 'previous-day-sweep shorts still never trade' in text
    assert 'Four-hour areas now include the afternoon (13:30–16:00) bucket.' in text
    for phrase in ('broken through or touched more than once', 'confirmed four-hour swing highs and lows',
                   'second nonadjacent interaction', '16 established areas nearest the current price',
                   'we break and we trade the retest', 'returns to the broken edge, whatever that candle closes',
                   'not the break direction', 'end of the next regular session', 'missing hourly candle inside a session',
                   'previous-session high, low and close', 'session VWAP', 'At least four of seven leaders',
                   'mixed → no trade', 'up to 60 minutes', 'consolidation bases', 'four consecutive 15-minute candles',
                   'look left', 'app choice', 'app interpretation',
                   'closes within 0.4% of it', 'established before the entry candle began',
                   'use their regular-session five-minute candles', 'keeps the last validated ones'):
        assert phrase in text, phrase
    assert '180 minutes' not in text and 'five of seven' not in text
    book = rulebook()
    assert book['version'] == 'video-evidence-2026-09-22-v5'
    assert any('two-session' in line for line in book['unresolved'])
    assert any('four of seven' in line and 'consolidation-base' in line for line in book['unresolved'])
    assert any('PSQ' in line for line in book['unresolved'])
    assert any('swing highs and lows' in rule['name'] for rule in book['rules'])
