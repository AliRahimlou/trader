"""v4 interpretation witnesses: reward-to-risk targets, persistent VIX areas, neutral leaders.

Every series is manufactured. VIX candles are priced near 15 with realistic
0.05-0.2 point ranges so the area-width change is exercised at index scale.
None of this is evidence that the videos define these numbers or that the
rules are profitable; the executor still owns permission and broker checks.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pivot import execution
from pivot.diagnostics import build_decision_trace
from pivot.models import Bar, Market, MAG7
from pivot.policy import POLICY, POLICY_VERSION
from pivot.rulebook import rulebook
from pivot.strategy import (ANALYSIS_VERSION, AnalysisPolicy, BASELINE_POLICY, analyze, leader_confirmation,
                            leader_diagnostics, vix_confirmation, vix_diagnostics)
from pivot.tests.test_strategy_v2 import NOW, candle, four_hour_history, leader_market, setup_scenario

ET = ZoneInfo('America/New_York')
SESSION = datetime(2026, 9, 16, 9, 30, tzinfo=ET)  # NOW is 12:00 ET on the same session.


# --- targets ---------------------------------------------------------------

def test_defaults_and_validation_of_the_v4_policy_fields():
    assert (BASELINE_POLICY.min_reward_risk, BASELINE_POLICY.vix_zone_tolerance,
            BASELINE_POLICY.vix_persistence_bars, BASELINE_POLICY.min_stop_fraction) == (1.0, 0.01, 2, 0.001)
    assert AnalysisPolicy(min_reward_risk=2, vix_zone_tolerance=0.01, vix_persistence_bars=1, min_stop_fraction=0)
    for bad in ({'min_reward_risk': 0}, {'min_reward_risk': float('nan')}, {'min_reward_risk': True},
                {'min_reward_risk': 11}, {'vix_zone_tolerance': 0}, {'vix_zone_tolerance': 0.06},
                {'vix_persistence_bars': 0}, {'vix_persistence_bars': 9}, {'vix_persistence_bars': 2.0},
                {'min_stop_fraction': -0.001}, {'min_stop_fraction': 0.06}, {'min_stop_fraction': True}):
        with pytest.raises(ValueError):
            AnalysisPolicy(**bad)


def test_target_never_uses_the_swept_level_even_when_it_offers_the_multiple():
    market, leaders, vix, now = setup_scenario('long')
    # The hour sweeps yesterday's 90 low and closes below it, so the swept
    # level itself sits 1.5 points above an 88.5 entry against a 0.51 stop
    # distance (2.9R). The old rule would have targeted it.
    market.bars[60][-1] = candle(now, 95, 96, 88, 88.5, 60)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == 'long'
    assert result['entry'] == 88.5 and result['stop'] == 87.99
    assert result['target'] == 120 and result['target_source'] == 'previous-day high'
    assert result['target_zone']['low'] == result['target_zone']['high'] == 120
    assert result['reward_risk'] == pytest.approx((120 - 88.5) / 0.51)
    assert 'previous-day high at 61.76R' in result['checks'][-1]['detail']


def test_nearest_levels_below_the_multiple_are_skipped_for_the_next_qualifying_level():
    market, leaders, vix, now = setup_scenario('long')
    market.bars[240] = four_hour_history()  # 4-hour areas near 105, 106 and 110 exist before the origin.
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY'
    assert any(z['source'] == '4h swing area' and 104 < z['low'] < 111 for z in result['levels'])
    # Entry 100, stop 87.99: the 4-hour swing areas at 106 and 110 offer 0.5-0.8R and are skipped.
    assert result['target'] == 120 and result['target_source'] == 'previous-day high'
    assert result['reward_risk'] == pytest.approx(20 / 12.01)
    # The multiple is a policy field: a lower threshold admits the nearest area.
    relaxed = analyze(market, leaders, vix, now, AnalysisPolicy(min_reward_risk=0.4))
    assert relaxed['target'] == 105.89 and relaxed['target_source'] == '4h swing area'
    assert relaxed['reward_risk'] == pytest.approx(5.89 / 12.01)
    assert relaxed['exit_rule'] == {'min_reward_risk': 0.4, 'min_stop_fraction': 0.001}


@pytest.mark.parametrize('direction', ['long', 'short'])
def test_no_level_at_the_required_multiple_fails_the_exit_check_without_a_target(direction):
    market, leaders, vix, now = setup_scenario(direction)
    # Yesterday's opposing extreme is 10 points away against a 12.01 stop distance (0.83R).
    market.bars[1440] = [candle(now - timedelta(days=1), high=110, low=90, minutes=1440)]
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'CONFIRMING' and result['direction'] == direction
    check = result['checks'][-1]
    assert check['name'] == 'Stop and target' and not check['passed']
    assert 'at least 1R' in check['detail'] and '12.01 stop distance' in check['detail']
    assert result['target'] is None and result['reward_risk'] is None and result['target_source'] is None
    assert result['entry_candidates'] == []
    assert analyze(market, leaders, vix, now, AnalysisPolicy(min_reward_risk=0.8))['state'] == 'SETUP_READY'


# --- actual VIX --------------------------------------------------------------

def vix_bar(minutes_after_open, opened, high, low, close):
    return Bar(SESSION + timedelta(minutes=minutes_after_open), 15, opened, high, low, close)


def vix_series(reaction=(14.93, 15.10, 14.85, 15.08), follow=((15.08, 15.12, 15.03, 15.06),),
               before_reaction=(14.95, 15.00, 14.90, 14.95)):
    """Contiguous 15-minute VIX candles 09:45-11:45 ET with repeated 14.80 lows.

    The swing lows at 10:00 and 10:45 form one repeated pivot area, established
    at 11:00 when the second swing is confirmed. The reaction candle closes at
    11:30, one candle before the 11:45 latest close (the baseline policy keeps
    a reaction for two closed candles).
    """
    rows = [(15.20, 15.30, 15.10, 15.15), (15.15, 15.20, 14.80, 14.90), (14.90, 15.05, 14.88, 15.00),
            (15.00, 15.10, 14.95, 15.02), (15.02, 15.05, 14.80, 14.92), (14.92, 15.00, 14.85, 14.95),
            before_reaction, reaction, *follow]
    return [vix_bar(15 * (i + 1), *row) for i, row in enumerate(rows)]


def vix_market(bars, now=NOW):
    return Market('I:VIX', {15: bars}, 'massive_indices', True, now)


def test_vix_reaction_one_candle_back_confirms_a_short_while_price_holds_beyond_the_area():
    vix = vix_market(vix_series())
    evidence = vix_diagnostics(vix, 'short', NOW)
    assert evidence['okay'] and evidence['reason'] == 'expected reaction present'
    assert evidence['reaction_at'] == SESSION + timedelta(hours=2) and evidence['age_minutes'] == 15
    zone = evidence['zone']
    assert zone.source == '4h repeated pivot' and zone.touches == 2
    assert zone.low == pytest.approx(14.80 * 0.99) and zone.high == pytest.approx(14.80 * 1.01)
    assert zone.established_at == SESSION + timedelta(minutes=90)
    assert '11:30 ET candle, 15 min before the latest close' in evidence['detail']
    # The same evidence cannot confirm a long, and a one-candle policy no longer sees it.
    assert vix_confirmation(vix, 'long', NOW) == (False, (
        'VIX must rise from demand for a short, or fall from supply for a long; no short reaction within the last '
        '2 closed 15-minute candles of this session'))
    assert not vix_confirmation(vix, 'short', NOW, AnalysisPolicy(vix_persistence_bars=1))[0]
    # Two candles back is outside the baseline window but inside a three-candle policy.
    older = vix_market(vix_series(follow=((15.08, 15.12, 15.03, 15.06), (15.06, 15.10, 15.04, 15.07))), NOW + timedelta(minutes=15))
    assert not vix_confirmation(older, 'short', NOW + timedelta(minutes=15))[0]
    assert vix_confirmation(older, 'short', NOW + timedelta(minutes=15), AnalysisPolicy(vix_persistence_bars=3))[0]


def test_vix_reaction_invalidated_by_a_later_close_through_the_area_fails_even_if_price_recovers():
    # The 11:45 candle closes through the area below the 11:30 long reaction,
    # which invalidates it even though the 11:45 candle is itself a short reaction.
    bars = vix_series(follow=((14.50, 14.55, 14.40, 14.45),))
    okay, detail = vix_confirmation(vix_market(bars), 'short', NOW)
    assert not okay
    assert 'the long reaction on the 11:30 ET candle was invalidated by subsequent close through zone' in detail
    evidence = vix_diagnostics(vix_market(bars), 'short', NOW)
    assert evidence['reason'] == 'expected zone reaction absent' and evidence['reaction_at'] is None
    assert [r['status'] for r in evidence['reactions'] if r['direction'] == 'long'] == [
        'invalidated by subsequent close through zone']


def test_vix_latest_close_back_inside_the_area_has_no_follow_through():
    bars = vix_series(follow=((15.06, 15.08, 14.85, 14.90),))
    okay, detail = vix_confirmation(vix_market(bars), 'short', NOW)
    assert not okay and 'reaction has no current directional follow-through' in detail


def test_vix_persistence_cannot_cross_a_missing_candle_or_the_previous_session():
    bars = vix_series()
    gapped = bars[:-1] + [Bar(bars[-1].end + timedelta(minutes=15), 15, *(15.06, 15.10, 15.04, 15.07))]
    vix = vix_market(gapped, NOW + timedelta(minutes=15))
    assert not vix_confirmation(vix, 'short', NOW + timedelta(minutes=15))[0]
    # A reaction on the previous session's candles is not carried into today.
    shifted = [Bar(b.end - timedelta(days=1), 15, b.open, b.high, b.low, b.close) for b in bars]
    yesterday = shifted + [Bar(bars[-1].end - timedelta(days=1) + timedelta(minutes=15 * (i + 1)), 15,
                               15.07, 15.09, 15.05, 15.07) for i in range(1)]
    assert not vix_confirmation(vix_market(yesterday, NOW - timedelta(days=1, minutes=-15)),
                                'short', NOW - timedelta(days=1, minutes=-15))[0]


def test_wider_vix_areas_see_a_reaction_the_tick_sized_band_misses_and_ignore_a_wick_only_touch():
    narrow, wide = AnalysisPolicy(vix_zone_tolerance=0.001), BASELINE_POLICY
    # A 0.15-point close-through above the repeated 14.80 lows: at 0.1% the
    # 14.79-14.81 band (about 1.5 ticks) is not even touched by the 14.85 low.
    held = vix_market(vix_series())
    assert not vix_confirmation(held, 'short', NOW, narrow)[0]
    assert vix_confirmation(held, 'short', NOW, wide)[0]
    narrow_zone = vix_diagnostics(held, 'short', NOW, narrow)['zones'][0]
    assert narrow_zone.high - narrow_zone.low == pytest.approx(0.0296)
    # A wick to 14.80 with a 14.90 close is a reaction for the tick-sized band
    # but still inside the 14.65-14.95 area, so the wider band does not react.
    wick = vix_market(vix_series(before_reaction=(14.95, 15.00, 14.85, 14.85), reaction=(14.85, 14.95, 14.80, 14.90),
                                 follow=((14.90, 14.95, 14.86, 14.92),)))
    assert vix_confirmation(wick, 'short', NOW, narrow)[0]
    assert not vix_confirmation(wick, 'short', NOW, wide)[0]
    assert vix_diagnostics(wick, 'short', NOW, wide)['reactions'] == []


def test_analysis_reports_the_vix_reaction_and_the_trace_stays_consistent():
    market, leaders, _, now = setup_scenario('short')
    vix = vix_market(vix_series())
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == 'short'
    assert result['vix_reaction_at'] == (SESSION + timedelta(hours=2)).isoformat()
    assert result['vix_reaction_age_minutes'] == 15 and result['vix_zone']['source'] == '4h repeated pivot'
    assert result['vix_rule'] == {'zone_tolerance': 0.01, 'persistence_minutes': 30, 'base_bars': 4, 'base_range': 0.02}
    trace = build_decision_trace(result, {**leaders, 'QQQ': market}, vix, now)
    assert trace['vix']['reason'] == 'expected reaction present' and trace['vix']['gate_reached']
    assert trace['vix']['reaction_at'] == result['vix_reaction_at']
    assert trace['vix']['zone']['low'] == result['vix_zone']['low'] and trace['vix']['age_minutes'] == 15
    assert trace['vix']['persistence_bars'] == 2 and trace['vix']['zone_tolerance'] == 0.01
    assert trace['setup']['reward_risk'] == result['reward_risk'] and trace['setup']['vix_reaction_at']
    assert [r['status'] for r in trace['vix']['reactions']] == ['active']


# --- leaders -----------------------------------------------------------------

def conflicting_leader(symbol='AAPL'):
    market = leader_market(symbol)
    market.bars[5] = [b for b in market.bars[5] if b.end < NOW - timedelta(minutes=15)]
    market.bars[5] += [candle(NOW - timedelta(minutes=15), 99, 100, 98, 99),
                       candle(NOW - timedelta(minutes=10), 99, 101, 89.9, 100),
                       candle(NOW - timedelta(minutes=5), 106, 107, 105.5, 106),
                       candle(NOW, 106, 110.2, 102, 103)]
    return market


def test_conflicting_company_is_neutral_and_five_agreeing_companies_still_confirm_the_setup():
    market, leaders, vix, now = setup_scenario('long')
    leaders['AAPL'] = conflicting_leader()
    leaders['TSLA'] = leader_market('TSLA', None)
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY' and result['direction'] == 'long'
    row = result['leader_evidence']['AAPL']
    assert row['vote'] is None and row['conflicting_reactions'] and row['reason'] == 'conflicting active area reactions'
    assert sum(r['vote'] == 'long' for r in result['leader_evidence'].values()) == 5
    leader_check = next(c for c in result['checks'] if c['name'] == 'Magnificent Seven at their zones')
    assert leader_check['passed'] and 'AAPL: conflicting, neutral' in leader_check['detail']
    # Missing five-minute data still vetoes the whole confirmation.
    leaders['MSFT'].bars.pop(5)
    assert analyze(market, leaders, vix, now)['state'] == 'CONFIRMING'


def test_reaction_before_the_nasdaq_origin_counts_in_the_same_session():
    market, leaders, vix, now = setup_scenario('long')
    origin = analyze(market, leaders, vix, now)['event_origin_at']
    assert origin == now.isoformat()
    for symbol in MAG7:
        early = leader_market(symbol, 'long', at=now - timedelta(minutes=5))
        early.bars[5].append(candle(now, 105, 105.05, 104.95, 105))
        early.observed_at = now
        leaders[symbol] = early
    result = analyze(market, leaders, vix, now)
    assert result['state'] == 'SETUP_READY'
    assert all(row['reaction_at'] == (now - timedelta(minutes=5)).isoformat() and row['reason'] == 'persistent reaction'
               for row in result['leader_evidence'].values())
    assert result['leader_evidence_valid_until'] == (now + timedelta(minutes=55)).isoformat()


def test_one_cent_pullback_keeps_the_vote_but_a_close_back_through_the_area_drops_it():
    def with_latest(bar):
        market = leader_market(at=NOW - timedelta(minutes=5))
        market.bars[5].append(bar)
        market.observed_at = NOW
        return leader_diagnostics({'AAPL': market}, NOW, setup_at=NOW - timedelta(minutes=20))['AAPL']
    held = with_latest(candle(NOW, 104, 104.05, 103.95, 103.99))
    assert held['vote'] == 'long' and held['reason'] == 'persistent reaction'
    assert held['reaction_zone']['high'] < 103.99
    # A latest close back inside the 89.91-90.09 area (touching no other area)
    # is a lost follow-through; a close below it invalidates the reaction.
    inside = with_latest(candle(NOW, 90.05, 90.08, 89.95, 90))
    assert inside['vote'] is None and inside['reason'] == 'reaction has no current directional follow-through'
    through = with_latest(candle(NOW, 89.5, 89.6, 89.2, 89.3))
    assert through['vote'] is None and through['reason'] == 'invalidated by subsequent close through zone'
    leaders = {symbol: leader_market(symbol) for symbol in MAG7}
    leaders['AAPL'] = leader_market(at=NOW - timedelta(minutes=5))
    leaders['AAPL'].bars[5].append(candle(NOW, 104, 104.05, 103.95, 103.99))
    leaders['AAPL'].observed_at = NOW
    assert leader_confirmation(leaders, 'long', NOW, setup_at=NOW - timedelta(minutes=20))[0]


# --- versions and owner-facing text ------------------------------------------

def test_versions_and_policy_text_keep_the_v4_rules_that_v5_retains():
    assert ANALYSIS_VERSION == 'nasdaq-video-interpretation-v5'
    assert POLICY_VERSION == 'nasdaq-qqq-execution-v7-video-aligned' == POLICY['version']
    text = ' '.join(POLICY['summary'])
    for phrase in ('at least as far away as the stop distance', 'the swept area itself is never the target',
                   'at least 0.1% of the entry price', '1% bands', 'either of the last two closed candles', 'is neutral',
                   'Each strategy may attempt two new entries per New York session',
                   'pauses the Socrates strategy only', 'final 30 minutes', 'time-based within the free allowance'):
        assert phrase in text, phrase
    book = rulebook()
    assert book['version'] == 'video-evidence-2026-09-22-v5'
    assert any('1R minimum' in line for line in book['unresolved'])
    assert any('neutral' in line for line in book['unresolved'])


def test_executor_signal_contract_names_the_current_analysis_version():
    assert execution.SIGNAL_POLICY_VERSION == ANALYSIS_VERSION


def test_a_stop_closer_than_the_minimum_distance_produces_no_plan():
    market, leaders, vix, now = setup_scenario('long')
    # A sweep candle whose low is one cent under the level: risk would be $0.02 on a $100 entry.
    market.bars[60][-1] = candle(now, 90.05, 90.10, 89.99, 90.00, 60)  # Sweeps yesterday's 90 low by a cent and closes two cents above the stop.
    result = analyze(market, leaders, vix, now)
    assert result['state'] != 'SETUP_READY' and result['target'] is None and result['reward_risk'] is None
    check = next(c for c in result['checks'] if c['name'] == 'Stop and target')
    assert not check['passed'] and 'below the minimum 0.1% of the entry price' in check['detail']
    assert 'Stop and target' in result['blockers']
    # Allowing any stop distance restores the plan.
    assert analyze(market, leaders, vix, now, AnalysisPolicy(min_stop_fraction=0))['state'] == 'SETUP_READY'
