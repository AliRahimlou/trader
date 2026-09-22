"""4.5.0 review decisions: a plan stays at the level, and next-session retests keep their targets."""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pivot import strategy as st
from pivot.models import MAG7, Bar, Zone
from pivot.strategy import AnalysisPolicy, analyze
from pivot.tests.test_strategy_v2 import candle, leader_market, setup_scenario

ET = ZoneInfo('America/New_York')


def drifted(hours_after):
    """A four-hour retest short scenario, then hourly candles drifting up away from the area."""
    market, leaders, vix, now = setup_scenario('short', 'four_hour_retest')
    drift = [candle(now + timedelta(hours=k), 111 + k, 112 + k, 110.5 + k, 111.5 + k, 60) for k in range(1, hours_after + 1)]
    later = now + timedelta(hours=hours_after)
    moved = replace(market, bars={**market.bars, 60: market.bars[60] + drift}, observed_at=later)
    moved.bars[1440] = [candle(later - timedelta(days=1), 100, 130, 80, 100, 1440)]
    moved.previous_session = '2026-09-15'
    buying = {symbol: leader_market(symbol, 'long', at=later) for symbol in MAG7}
    base = setup_scenario('long')[2]
    vix_later = replace(base, observed_at=later, bars={15: [replace(b, end=b.end + timedelta(hours=hours_after)) for b in base.bars[15]]})
    return moved, buying, vix_later, later


def test_a_confirmed_event_that_drifted_away_from_the_area_produces_no_plan():
    market, leaders, vix, later = drifted(3)
    result = analyze(market, leaders, vix, later)
    zone = result['event_zone']
    assert market.bars[60][-1].low > zone['high']  # The latest candle no longer touches the area.
    assert result['state'] != 'SETUP_READY' and result['entry_candidates'] == []
    event_check = next(c for c in result['checks'] if c['name'] == 'Nasdaq level event')
    assert not event_check['passed'] and 'waiting for a return to the level' in event_check['detail']
    assert result['distance_from_area'] > 0.004
    # A generous proximity would have admitted it: the bound is what blocks it.
    loose = analyze(market, leaders, vix, later, AnalysisPolicy(retest_proximity=0.05))
    assert loose['state'] == 'SETUP_READY'


def test_proximity_validation():
    assert st.BASELINE_POLICY.retest_proximity == 0.004
    for bad in (0, -0.001, 0.06, True):
        try:
            AnalysisPolicy(retest_proximity=bad)
        except ValueError:
            continue
        raise AssertionError(f'accepted {bad!r}')


def test_next_session_retest_can_target_the_break_days_high_and_afternoon_area():
    tuesday = lambda h, m: datetime(2026, 9, 15, h, m, tzinfo=ET)
    wednesday = lambda h, m: datetime(2026, 9, 16, h, m, tzinfo=ET)
    event = Zone(599.4, 600.6, tuesday(8, 0), '4h repeated pivot')
    break_day_high = Zone(606.0, 606.0, tuesday(16, 0), 'previous-day high')
    afternoon_area = Zone(604.0, 604.2, tuesday(16, 0), '4h repeated pivot')
    entry_candle = Bar(wednesday(10, 30), 60, 600.9, 602.0, 600.3, 601.8)
    target, zone, reward_risk = st._target([event, break_day_high, afternoon_area], event, entry_candle,
                                           'long', 601.8, 599.29, st.BASELINE_POLICY)
    # 604.0 is only 0.88R away, so the break day's 606.00 high (1.67R) is the nearest qualifying target.
    assert target == 606.0 and zone is break_day_high and reward_risk > 1.6
    # A level formed during the entry candle itself is never a target.
    during = Zone(606.0, 606.0, wednesday(10, 0), 'previous-day high')
    assert st._target([event, during], event, entry_candle, 'long', 601.8, 599.29, st.BASELINE_POLICY) == (None, None, None)
    # The event's own area is still excluded.
    assert st._target([event], event, entry_candle, 'short', 601.8, 604.0, st.BASELINE_POLICY) == (None, None, None)
