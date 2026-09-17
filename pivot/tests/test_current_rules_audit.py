"""Offline witnesses for the September 17 rule audit, not performance evidence.

These expose the interpreter's boundaries and regress the engineering exit
consistency fix: both classes of premarked levels can supply a target. The
videos do not specify exits; this is not evidence of a source-defined target.
Manufactured candles below intentionally isolate analysis from provider health.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pivot.models import Bar, MAG7, Market
from pivot.sizing import purchase_plan
from pivot.strategy import analyze, leader_confirmation, zones


NOW = datetime(2026, 9, 16, 16, tzinfo=timezone.utc)


def candle(at, opened=100, high=105, low=95, close=100, minutes=15):
    return Bar(at, minutes, opened, high, low, close)


def old_zones():
    ranges = [(105, 95), (110, 94), (105, 95), (106, 90), (105, 95),
              (110, 94), (105, 95), (106, 90), (105, 95)]
    return [candle(NOW - timedelta(hours=4 * (14 - i)), high=high, low=low, minutes=240)
            for i, (high, low) in enumerate(ranges)]


def confirmations(direction):
    previous = candle(NOW - timedelta(minutes=15))
    current = (candle(NOW, high=105, low=89.9, close=104) if direction == 'long'
               else candle(NOW, high=110.1, low=95, close=96))
    leaders = {symbol: Market(symbol, {240: old_zones(), 15: [previous, current]},
                              'alpaca_iex', True, NOW) for symbol in MAG7}
    inverse = (candle(NOW, high=110.1, low=95, close=96) if direction == 'long'
               else candle(NOW, high=105, low=89.9, close=104))
    vix = Market('I:VIX', {15: [replace(b, minutes=15) for b in old_zones()] + [previous, inverse]},
                 'massive_indices', True, NOW)
    return leaders, vix


def sweep_market(direction, daily_high=110, daily_low=90, four_hour=False):
    prior = candle(NOW - timedelta(hours=1), 100, 104, 96, 100, 60)
    current = (candle(NOW, 99, 101, 88, 100, 60) if direction == 'long'
               else candle(NOW, 101, 112, 99, 100, 60))
    return Market('QQQ', {
        1440: [candle(NOW - timedelta(days=1), high=daily_high, low=daily_low, minutes=1440)],
        60: [prior, current], 240: old_zones() if four_hour else [],
    }, 'alpaca_iex', True, NOW, previous_session='2026-09-15')


@pytest.mark.parametrize('direction,target', [('long', 110), ('short', 90)])
def test_prior_day_sweep_can_target_opposing_previous_day_level_without_four_hour_zones(direction, target):
    market = sweep_market(direction)
    result = analyze(market, *confirmations(direction), NOW)
    assert result['event'] == 'previous-day level sweep'
    assert result['direction'] == direction
    assert all(c['passed'] for c in result['checks'])
    assert result['state'] == 'SETUP_READY' and result['target'] == target
    assert result['can_enter'] is False


@pytest.mark.parametrize('direction,daily_high,daily_low,target', [
    ('long', 108, 92, 105.89),  # The four-hour area is nearer than yesterday's high.
    ('long', 104, 92, 104),     # Yesterday's high is nearer than the four-hour area.
    ('short', 108, 90, 94.09),  # The four-hour area is nearer than yesterday's low.
    ('short', 108, 96, 96),     # Yesterday's low is nearer than the four-hour area.
])
def test_target_is_nearest_eligible_level_in_trade_direction(direction, daily_high, daily_low, target):
    market = sweep_market(direction, daily_high, daily_low, four_hour=True)
    result = analyze(market, *confirmations(direction), NOW)
    assert result['state'] == 'SETUP_READY'
    assert result['target'] == target
    assert result['target'] > result['entry'] if direction == 'long' else result['target'] < result['entry']


@pytest.mark.parametrize('direction,target', [('long', 105.89), ('short', 94.09)])
def test_future_or_wrong_previous_session_daily_levels_cannot_supply_a_target(direction, target):
    market = sweep_market(direction, four_hour=True)
    # The future daily bar would place a closer target at 101/99, but is not
    # closed. The older completed day is not the expected previous session.
    market.bars[1440] = [
        candle(NOW - timedelta(days=2), high=101, low=99, minutes=1440),
        candle(NOW + timedelta(days=1), high=101, low=99, minutes=1440),
    ]
    if direction == 'long':
        market.bars[60] = [candle(NOW - timedelta(hours=2), 92, 93, 91, 92, 60),
                           candle(NOW - timedelta(hours=1), 92, 93, 88, 89, 60),
                           candle(NOW, 99, 101, 89, 100, 60)]
    else:
        market.bars[60] = [candle(NOW - timedelta(hours=2), 108, 109, 107, 108, 60),
                           candle(NOW - timedelta(hours=1), 108, 112, 107, 111, 60),
                           candle(NOW, 101, 111, 99, 100, 60)]
    result = analyze(market, *confirmations(direction), NOW)
    assert result['state'] == 'SETUP_READY' and result['target'] == target
    assert not any(z['source'].startswith('previous-day') for z in result['levels'])


@pytest.mark.parametrize('direction', ['long', 'short'])
def test_audit_both_directions_can_qualify_without_equating_break_with_trade_direction(direction):
    """No universal logic deadlock; opposite-direction reactions can qualify."""
    if direction == 'long':
        bars = [candle(NOW - timedelta(hours=2), 92, 93, 91, 92, 60),
                candle(NOW - timedelta(hours=1), 92, 93, 88, 89, 60),
                candle(NOW, 89.5, 90, 88, 89, 60)]
    else:
        bars = [candle(NOW - timedelta(hours=2), 108, 109, 107, 108, 60),
                candle(NOW - timedelta(hours=1), 108, 112, 107, 111, 60),
                candle(NOW, 110.5, 112, 110, 111, 60)]
    market = Market('QQQ', {240: old_zones(), 60: bars}, 'alpaca_iex', True, NOW)
    result = analyze(market, *confirmations(direction), NOW)
    assert result['state'] == 'SETUP_READY' and result['direction'] == direction
    assert result['can_enter'] is False  # Analysis is never broker permission.


def test_audit_five_dollars_is_supported_for_fractional_longs_but_not_whole_share_shorts():
    """A broker restriction, not a missing signal: $5 cannot short a $500 share."""
    assert purchase_plan('5.00', '500', '92.05', 'long', True)['quantity'] == '0.010000000'
    with pytest.raises(ValueError, match='No supported share quantity'):
        purchase_plan('5.00', '500', '92.05', 'short', True)


def test_audit_three_available_reactions_cannot_pass_four_company_requirement():
    """A company without a generated zone is neutral, not missing-data failure."""
    leaders, _ = confirmations('long')
    for symbol in MAG7[3:]:
        leaders[symbol].bars[240] = []
    assert all(leaders[s].realtime for s in MAG7)
    assert leader_confirmation(leaders, 'long', NOW)[0] is False


def test_audit_repeated_crossings_without_swing_extremes_produce_no_zone():
    """Video crossings and the extrema algorithm are distinct construction rules."""
    bars = [candle(NOW - timedelta(hours=4 * (9 - i)), high=105 + i, low=90 + i, minutes=240)
            for i in range(8)]
    assert all(b.low < 100 < b.high for b in bars)
    assert zones(bars, NOW) == []
