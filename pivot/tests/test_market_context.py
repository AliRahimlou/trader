"""Point-in-time structure witnesses, not trading-performance evidence."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from pivot.market_context import market_context
from pivot.models import Bar, Market


NOW = datetime(2026, 9, 18, 19, 30, tzinfo=timezone.utc)
RANGES = [(104, 95), (110, 96), (105, 94), (108, 97), (112, 98),
          (109, 96), (109, 98)]


def bars(minutes=60, ranges=RANGES, end=NOW):
    return [Bar(end-timedelta(minutes=minutes*(len(ranges)-index-1)), minutes,
                (high+low)/2, high, low, (high+low)/2)
            for index, (high, low) in enumerate(ranges)]


def market(hourly=None, four_hour=None, now=NOW):
    return Market('QQQ', {60: bars() if hourly is None else hourly,
                          240: bars(240) if four_hour is None else four_hour},
                  'alpaca_iex', True, now)


def frame(result, minutes=60):
    return next(row for row in result['frames'] if row['timeframe_minutes'] == minutes)


def test_upward_structure_has_confirmed_pivot_timestamps_and_no_entry_permission():
    result = market_context(market(), NOW)
    hourly = frame(result)
    assert hourly['direction'] == 'up' and hourly['status'] == 'ready'
    assert [pivot['price'] for pivot in hourly['pivots']['highs']] == [110, 112]
    assert [pivot['price'] for pivot in hourly['pivots']['lows']] == [94, 96]
    for pivot in hourly['pivots']['highs'] + hourly['pivots']['lows']:
        assert datetime.fromisoformat(pivot['confirmed_at']) - datetime.fromisoformat(pivot['event_at']) == timedelta(hours=1)
    assert result['descriptive_only'] is True and result['entry_veto'] is False
    assert hourly['descriptive_only'] is True and hourly['entry_veto'] is False
    assert result['source'] == 'alpaca_iex' and 'proxy' in result['instrument_label']


def test_downward_and_mixed_structure():
    downward = [(210-low, 210-high) for high, low in RANGES]
    assert frame(market_context(market(bars(ranges=downward)), NOW))['direction'] == 'down'
    mixed = RANGES[:5] + [(109, 93), (110, 98)]
    assert frame(market_context(market(bars(ranges=mixed)), NOW))['direction'] == 'mixed'


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_latest_close_breaks_structural_boundary_before_another_pivot_is_confirmed(direction):
    ranges = RANGES if direction == 'up' else [(210-low, 210-high) for high, low in RANGES]
    history = bars(ranges=ranges, end=NOW-timedelta(hours=1))
    close = 95 if direction == 'up' else 115
    history.append(Bar(NOW, 60, close, close+1, close-1, close))
    row = frame(market_context(market(history), NOW))
    assert row['direction'] == 'mixed'
    assert row['invalidation']['boundary'] == ('low' if direction == 'up' else 'high')
    assert row['invalidation']['at'] == NOW.isoformat()


def test_equal_extrema_and_insufficient_history_are_not_strict_swings():
    flat = [(105, 95)] * 8
    row = frame(market_context(market(bars(ranges=flat)), NOW))
    assert row['direction'] == 'unknown' and row['pivots'] == {'highs': [], 'lows': []}
    row = frame(market_context(market(bars()[:3], now=bars()[2].end), bars()[2].end))
    assert row['direction'] == 'unknown'


def test_future_right_hand_bar_cannot_confirm_a_pivot_or_change_prior_result():
    history = bars()
    before = NOW-timedelta(hours=1)
    source = market(history[:-1], now=before)
    initial = market_context(source, before)
    source.bars[60].append(history[-1])
    assert market_context(source, before) == initial
    assert len(frame(initial)['pivots']['lows']) == 1
    source.observed_at = NOW
    assert len(frame(market_context(source, NOW))['pivots']['lows']) == 2


@pytest.mark.parametrize('malformation', ['missing', 'duplicate', 'reversed', 'wrong_frame'])
def test_invalid_hourly_history_never_produces_structure(malformation):
    history = bars()
    if malformation == 'missing':
        history.pop(3)
    elif malformation == 'duplicate':
        history.insert(3, history[3])
    elif malformation == 'reversed':
        history[3], history[4] = history[4], history[3]
    else:
        history[3] = replace(history[3], minutes=15)
    result = market_context(market(history), NOW)
    assert result['status'] == 'invalid'
    assert all(row['direction'] == 'unknown' for row in result['frames'])


def test_missing_feed_or_stale_hourly_source_does_not_retain_direction():
    assert market_context(None, NOW)['status'] == 'unavailable'
    source = market()
    source.observed_at = NOW-timedelta(seconds=91)
    result = market_context(source, NOW)
    assert result['status'] == 'stale' and not result['data_current']
    assert all(row['direction'] == 'unknown' for row in result['frames'])


def session_history():
    # One full four-hour bucket per RTH day. The latest day is Friday.
    dates = [8, 9, 10, 11, 14, 15, 16]
    four_hour = [Bar(NOW.replace(day=day, hour=17), 240, (high+low)/2,
                     high, low, (high+low)/2) for day, (high, low) in zip(dates, RANGES)]
    hourly = [Bar(NOW.replace(day=day, hour=hour), 60, 103, 104, 102, 103)
              for day in dates for hour in range(14, 20)]
    now = NOW.replace(day=17, hour=14)
    hourly.append(Bar(now, 60, 103, 104, 102, 103))
    return hourly, four_hour, now


def test_older_four_hour_candle_uses_current_hourly_freshness_and_keeps_its_timestamp():
    hourly, four_hour, now = session_history()
    result = market_context(market(hourly, four_hour, now), now)
    row = frame(result, 240)
    assert result['data_current'] and row['direction'] == 'up'
    assert row['latest_bar_at'] == four_hour[-1].end.isoformat()
    assert row['validation_at'] == now.isoformat()
    assert now-four_hour[-1].end > timedelta(hours=4)


def test_current_hourly_close_can_invalidate_older_four_hour_structure():
    hourly, four_hour, now = session_history()
    hourly[-1] = Bar(now, 60, 95, 96, 94, 95)
    row = frame(market_context(market(hourly, four_hour, now), now), 240)
    assert row['direction'] == 'mixed' and row['invalidation']['boundary'] == 'low'


def test_missing_four_hour_bar_on_an_observed_full_session_is_not_bridged():
    hourly, four_hour, now = session_history()
    four_hour.pop(3)
    row = frame(market_context(market(hourly, four_hour, now), now), 240)
    assert row['status'] == 'invalid' and row['direction'] == 'unknown'
    assert 'missing' in row['detail']


def test_four_hour_right_hand_candle_is_not_available_before_its_close():
    hourly, four_hour, _ = session_history()
    before = four_hour[-1].end-timedelta(seconds=1)
    source = market(hourly, four_hour[:-1], before)
    initial = market_context(source, before)
    source.bars[240].append(four_hour[-1])
    assert market_context(source, before) == initial
    assert frame(initial, 240)['direction'] == 'unknown'
    source.observed_at = four_hour[-1].end
    assert frame(market_context(source, source.observed_at), 240)['direction'] == 'up'


def test_short_session_does_not_require_a_nonexistent_four_hour_candle():
    hourly, four_hour, now = session_history()
    hourly = [bar for bar in hourly if bar.end.day != 15 or bar.end.hour <= 16]
    four_hour = [bar for bar in four_hour if bar.end.day != 15]
    result = market_context(market(hourly, four_hour, now), now)
    assert result['data_current']
    assert frame(result, 240)['status'] == 'insufficient'


def test_missing_last_hour_before_next_session_is_not_bridged():
    hourly, four_hour, now = session_history()
    hourly = [bar for bar in hourly if not (bar.end.day == 15 and bar.end.hour == 19)]
    result = market_context(market(hourly, four_hour, now), now)
    assert result['status'] == 'invalid'
    assert all(row['direction'] == 'unknown' for row in result['frames'])


def test_current_fetch_cannot_disguise_old_hourly_prices():
    source = market()
    later = NOW+timedelta(hours=2)
    source.observed_at = later
    result = market_context(source, later)
    assert result['status'] == 'stale'
    assert all(row['direction'] == 'unknown' for row in result['frames'])


def test_unknown_source_text_is_not_copied_to_diagnostics():
    source = market()
    source.source = 'secret-provider-token'
    result = market_context(source, NOW)
    assert result['source'] == 'unrecognized'
    assert 'secret-provider-token' not in json.dumps(result)


def test_non_qqq_input_cannot_be_presented_as_the_nasdaq_proxy():
    source = market()
    source.symbol = 'AAPL'
    result = market_context(source, NOW)
    assert result['status'] == 'invalid'
    assert all(row['direction'] == 'unknown' for row in result['frames'])
