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


# Closing (13:30-16:00) bucket ranges per session since 4.5.0. The 15 high on
# the 15th is an afternoon extreme that only exists because of that bucket.
CLOSING = [(103, 95), (103, 95), (108, 95), (107, 94), (112, 105), (115, 101), (105, 97)]


def session_history():
    # Two four-hour buckets per RTH day, end-stamped 13:30 and 16:00 ET. The latest day is Friday.
    dates = [8, 9, 10, 11, 14, 15, 16]
    four_hour = []
    for day, (high, low), (closing_high, closing_low) in zip(dates, RANGES, CLOSING):
        four_hour.append(Bar(NOW.replace(day=day, hour=17), 240, (high+low)/2, high, low, (high+low)/2))
        four_hour.append(Bar(NOW.replace(day=day, hour=20, minute=0), 240, (closing_high+closing_low)/2,
                             closing_high, closing_low, (closing_high+closing_low)/2))
    hourly = [Bar(NOW.replace(day=day, hour=hour), 60, 103, 104, 102, 103)
              for day in dates for hour in range(14, 20)]
    now = NOW.replace(day=17, hour=14)
    hourly.append(Bar(now, 60, 103, 104, 102, 103))
    return hourly, four_hour, now


def test_afternoon_bucket_extremes_form_confirmed_four_hour_swings():
    hourly, four_hour, now = session_history()
    row = frame(market_context(market(hourly, four_hour, now), now), 240)
    assert row['status'] == 'ready' and row['direction'] == 'up'
    assert [pivot['price'] for pivot in row['pivots']['highs']] == [110, 115]
    assert [pivot['price'] for pivot in row['pivots']['lows']] == [94, 96]
    afternoon_high = row['pivots']['highs'][-1]
    assert datetime.fromisoformat(afternoon_high['event_at']) == NOW.replace(day=15, hour=20, minute=0)
    assert datetime.fromisoformat(afternoon_high['confirmed_at']) == NOW.replace(day=16, hour=17)
    assert [(bar.end.hour, bar.end.minute) for bar in four_hour[:2]] == [(17, 30), (20, 0)]  # 13:30 and 16:00 ET


def test_closing_bucket_is_only_due_after_a_later_session_is_observed():
    hourly, four_hour, now = session_history()
    # During the afternoon the hourly frame runs ahead of the closing bucket by design.
    afternoon = NOW.replace(day=16, hour=19)
    partial = [bar for bar in four_hour if bar.end <= afternoon]
    result = market_context(market(hourly, partial, afternoon), afternoon)
    assert result['data_current'] and frame(result, 240)['status'] == 'ready'
    # Once the next session has hourly candles, the missing closing bucket is a gap.
    without_closing = [bar for bar in four_hour if not (bar.end.day == 15 and bar.end.hour == 20)]
    row = frame(market_context(market(hourly, without_closing, now), now), 240)
    assert row['status'] == 'invalid' and 'closing candle is missing' in row['detail']
    # A session whose first bucket is not the 13:30 one cannot be followed by a closing bucket.
    shifted = list(four_hour)
    shifted[0] = Bar(NOW.replace(day=8, hour=16), 240, 100, 101, 99, 100)  # 12:30 ET
    row = frame(market_context(market(hourly, shifted, now), now), 240)
    assert row['status'] == 'invalid' and 'within an observed session' in row['detail']
    # A closing bucket that ends before the last observed hourly candle of its day is a gap.
    short_closing = list(four_hour)
    short_closing[1] = Bar(NOW.replace(day=8, hour=18), 240, 100, 101, 99, 100)  # 14:00 ET
    row = frame(market_context(market(hourly, short_closing, now), now), 240)
    assert row['status'] == 'invalid' and 'closing candle is missing' in row['detail']
    # A new session must begin with its first bucket (13:30 or an earlier early close).
    late_start = [bar for bar in four_hour if not (bar.end.day == 14 and bar.end.hour == 17)]
    row = frame(market_context(market(hourly, late_start, now), now), 240)
    assert row['status'] == 'invalid'


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
    # The 13:30 bucket on the 16th confirms the afternoon swing high of the 15th.
    confirming = four_hour[-2]
    assert confirming.end == NOW.replace(day=16, hour=17)
    before = confirming.end-timedelta(seconds=1)
    source = market(hourly, four_hour[:-2], before)
    initial = market_context(source, before)
    source.bars[240].append(confirming)
    assert market_context(source, before) == initial
    assert frame(initial, 240)['direction'] == 'unknown'
    source.observed_at = confirming.end
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
