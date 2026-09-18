"""Native-five-minute manufactured witnesses; no authentic-return claims."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from pivot import strategy
from pivot.models import Bar, Market, MAG7, Zone
from pivot.tests.test_strategy_v2 import NOW, candle, four_hour_history, leader_market
from research.five_minute_candidates import (NativeFiveMinuteSeries, native_closed, five_minute_events,
    fast_location_candidates, freeze_vix_exit, vix_exit_decision, research_exit_reference, VixExitAnchor)


ENGINE = SimpleNamespace(strategy=strategy)


def native(symbol, bars, at=NOW, **changes):
    return NativeFiveMinuteSeries(symbol, tuple(bars), at,
        'alpaca_iex' if symbol == 'QQQ' else 'insightsentry',
        'QQQ' if symbol == 'QQQ' else 'CBOE:VIX', 5, True, True, **changes)


def vix_history(direction='short', now=NOW):
    ranges = [(105, 95), (110, 94), (105, 95), (106, 90), (105, 95),
              (110, 94), (105, 95), (106, 90), (105, 95), (106, 94), (105, 95), (106, 94)]
    bars = [candle(now-timedelta(minutes=5*(13-i)), high=h, low=l) for i, (h, l) in enumerate(ranges)]
    bars.append(candle(now-timedelta(minutes=5), high=106, low=94))
    bars.append(candle(now, high=110.1, low=95, close=96) if direction == 'short' else
                candle(now, high=105, low=89.9, close=104))
    return bars


@pytest.mark.parametrize('direction', ['long', 'short'])
@pytest.mark.parametrize('method', ['prior_day_sweep', 'four_hour_retest'])
def test_f1_complete_native_five_minute_setup_both_methods_and_directions(direction, method):
    leaders = {s: leader_market(s, direction) for s in MAG7}
    if method == 'prior_day_sweep':
        bars = [candle(NOW-timedelta(minutes=5), high=104, low=96),
                candle(NOW, 99, 101, 88, 100) if direction == 'long' else candle(NOW, 101, 112, 99, 100)]
        higher = Market('QQQ', {1440: [candle(NOW-timedelta(days=1), high=110, low=90, minutes=1440)],
                               240: []}, 'alpaca_iex', True, NOW, previous_session='2026-09-15')
    else:
        bars = ([candle(NOW-timedelta(minutes=10), 92, 93, 91, 92),
                 candle(NOW-timedelta(minutes=5), 92, 93, 88, 89), candle(NOW, 89.5, 90, 88, 89)]
                if direction == 'long' else [candle(NOW-timedelta(minutes=10), 108, 109, 107, 108),
                candle(NOW-timedelta(minutes=5), 108, 112, 107, 111), candle(NOW, 110.5, 112, 110, 111)])
        higher = Market('QQQ', {240: four_hour_history()}, 'alpaca_iex', True, NOW)
    index = native('I:VIX', vix_history('short' if direction == 'long' else 'long'))
    early = fast_location_candidates(ENGINE, higher, leaders, native('QQQ', bars), index, NOW)
    assert early['blocker'] == 'awaiting_publication_allowance' and early['candidates'] == []
    result = fast_location_candidates(ENGINE, higher, leaders, native('QQQ', bars), index, NOW+timedelta(seconds=60))
    qualified = [r for r in result['candidates'] if r['qualified'] and r['method'] == method]
    assert qualified and all(r['direction'] == direction for r in qualified)
    assert result['research_only'] is True and result['can_enter'] is False
    assert all(r['can_enter'] is False and r['event_id'].startswith('research_f1_') for r in qualified)
    assert 'entry_candidates' not in result and 'policy_version' not in result


@pytest.mark.parametrize('bad', ['frame', 'proxy', 'unverified', 'gap', 'stale', 'duplicate'])
def test_native_contract_fails_closed(bad):
    series = native('I:VIX', vix_history())
    if bad == 'frame': series = replace(series, bars=(Bar(NOW, 15, 100, 101, 99, 100),))
    if bad == 'proxy': series = replace(series, source_symbol='CAPITALCOM:VIX')
    if bad == 'unverified': series = replace(series, coverage_verified=False)
    if bad == 'gap': series = replace(series, bars=series.bars[:2]+series.bars[3:])
    if bad == 'stale': series = replace(series, observed_at=NOW-timedelta(seconds=90))
    if bad == 'duplicate': series = replace(series, bars=series.bars+(series.bars[-1],))
    with pytest.raises(ValueError): native_closed(series, NOW)


def test_future_bars_cannot_confirm_f1_retest_or_create_current_vix_levels():
    zone = Zone(99.9, 100.1, NOW-timedelta(days=1), '4h repeated interaction')
    bars = [candle(NOW-timedelta(minutes=10), 99, 100, 98, 99),
            candle(NOW-timedelta(minutes=5), 99, 103, 98, 102), candle(NOW, 101, 104, 100, 103)]
    before = NOW-timedelta(seconds=1)
    event = five_minute_events(bars, [zone], 'four_hour_retest', before)[0]
    assert event['state'] == 'WAITING_FOR_RETEST' and event['confirmed'] is None
    assert five_minute_events(bars, [zone], 'four_hour_retest', NOW)[0]['state'] == 'CONFIRMING'
    late = replace(zone, established_at=bars[1].end-timedelta(minutes=4))
    assert five_minute_events(bars, [late], 'four_hour_retest', NOW) == []


def test_f1_same_event_keeps_identity_expiry_and_gap_invalidates():
    zone = Zone(99.9, 100.1, NOW-timedelta(days=1), '4h repeated interaction')
    bars = [candle(NOW-timedelta(minutes=10), 99, 100, 98, 99),
            candle(NOW-timedelta(minutes=5), 99, 103, 98, 102), candle(NOW, 101, 104, 100, 103)]
    original = five_minute_events(bars, [zone], 'four_hour_retest', NOW)[0]
    later = NOW+timedelta(minutes=5)
    repeated = five_minute_events(bars+[candle(later, 102, 105, 100, 104)], [zone], 'four_hour_retest', later)[0]
    assert repeated['id'] == original['id'] and repeated['expires_at'] == original['expires_at']
    gap = five_minute_events(bars+[candle(later+timedelta(minutes=5), 102, 105, 100, 104)],
                             [zone], 'four_hour_retest', later+timedelta(minutes=5))[0]
    assert gap['state'] == 'INVALIDATED'
    assert five_minute_events(bars, [zone], 'four_hour_retest', original['expires_at'])[0]['state'] == 'EXPIRED'


def test_x1_freezes_nearest_opposing_pivot_from_entry_prefix_only():
    initial = native('I:VIX', vix_history('long'))
    first = freeze_vix_exit(ENGINE, 'short', initial, NOW)
    assert first is not None and first.boundary > initial.bars[-1].close
    future = candle(NOW+timedelta(minutes=5), 104, 1000, 1, 104)
    second = freeze_vix_exit(ENGINE, 'short', replace(initial, bars=initial.bars+(future,)), NOW)
    assert first == second
    assert first.zone.established_at < NOW-timedelta(minutes=5)


def test_x1_touch_is_not_known_before_close_plus_publication_and_does_not_need_rejection():
    zone = Zone(109, 110, NOW-timedelta(days=1), 'prior VIX wicks')
    anchor = VixExitAnchor('short', NOW, 109, zone)
    # A wick reaches the frozen upper boundary while closing below it.
    bars = [candle(NOW, 104, 105, 103, 104), candle(NOW+timedelta(minutes=5), 104, 109.1, 103, 104)]
    before = NOW+timedelta(minutes=5, seconds=59)
    assert vix_exit_decision(anchor, native('I:VIX', bars, before), before) is None
    after = before+timedelta(seconds=1)
    decision = vix_exit_decision(anchor, native('I:VIX', bars, after), after)
    assert decision['at'] == after.isoformat() and decision['status'] == 'current'
    assert decision['can_enter'] is False
    # No decision is attributed to a touch that occurred in a bar spanning entry.
    late_entry = replace(anchor, entered_at=NOW+timedelta(seconds=1))
    assert vix_exit_decision(late_entry, native('I:VIX', bars, after), after) is None


def test_x1_long_uses_lower_boundary_and_never_carries_across_session():
    anchor = VixExitAnchor('long', NOW, 95, Zone(94, 95, NOW-timedelta(days=1), 'prior VIX wicks'))
    end = NOW+timedelta(minutes=5)
    bars = [candle(NOW), candle(end, 100, 101, 94.9, 100)]
    at = end+timedelta(minutes=1)
    assert vix_exit_decision(anchor, native('I:VIX', bars, at), at)['boundary'] == 95
    tomorrow = NOW+timedelta(days=1)
    assert vix_exit_decision(anchor, native('I:VIX', [candle(tomorrow)], tomorrow), tomorrow) is None


def test_x1_next_observed_open_only_with_gap_stop_priority_and_expiry():
    start = NOW+timedelta(minutes=5)
    decision = {'at': start.isoformat(), 'valid_until': (start+timedelta(minutes=2)).isoformat(), 'status': 'current'}
    qqq = candle(start+timedelta(minutes=5), 100, 111, 94, 100)
    assert research_exit_reference(qqq, 'long', 95, 110, decision)['reason'] == 'vix_pivot'
    gap = candle(start+timedelta(minutes=5), 94, 111, 93, 100)
    assert research_exit_reference(gap, 'long', 95, 110, decision)['reason'] == 'gap_stop'
    # If publication comes after this open, OHLC cannot create an earlier fill.
    later = {**decision, 'at': (start+timedelta(seconds=1)).isoformat()}
    adverse = research_exit_reference(qqq, 'long', 95, 110, later)
    assert adverse['reason'] == 'stop' and adverse['ambiguous'] is True
    expired = {**decision, 'valid_until': start.isoformat()}
    assert research_exit_reference(qqq, 'long', 95, 110, expired)['reason'] == 'stop'
