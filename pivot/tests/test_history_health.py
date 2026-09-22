"""Calendar completeness checks use local candles only."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from pivot.history_health import bucket_ends, frame_gaps
from pivot.models import Bar

ET = ZoneInfo('America/New_York')


def session(day, close='16:00'):
    return {'open': datetime.fromisoformat(day + 'T09:30').replace(tzinfo=ET),
            'close': datetime.fromisoformat(day + 'T' + close).replace(tzinfo=ET)}


def candles(sessions, minutes):
    return [Bar(end, minutes, 100, 101, 99, 100)
            for s in sessions.values() for end in bucket_ends(s['open'], s['close'], minutes)]


def test_bucket_ends_split_the_four_hour_frame_and_keep_full_buckets_elsewhere():
    full, early, short = session('2026-09-16'), session('2026-11-27', '13:00'), session('2026-11-27', '13:30')
    clock = lambda ends: [(e.hour, e.minute) for e in ends]
    assert clock(bucket_ends(full['open'], full['close'], 240)) == [(13, 30), (16, 0)]
    assert clock(bucket_ends(early['open'], early['close'], 240)) == [(13, 0)]
    assert clock(bucket_ends(short['open'], short['close'], 240)) == [(13, 30)]
    assert clock(bucket_ends(full['open'], full['close'], 60))[-1] == (15, 30)
    assert clock(bucket_ends(early['open'], early['close'], 60)) == [(10, 30), (11, 30), (12, 30)]
    assert len(bucket_ends(full['open'], full['close'], 15)) == 26
    assert bucket_ends(early['open'], early['close'], 1440) == [early['close']]


@pytest.mark.parametrize('minutes,expected_count', [(15, 26), (60, 6), (240, 2), (1440, 1)])
def test_all_provided_sessions_are_required_from_their_first_bucket(minutes, expected_count):
    sessions = {'2026-09-15': session('2026-09-15'), '2026-09-16': session('2026-09-16')}
    bars = candles(sessions, minutes)
    now = sessions['2026-09-16']['close'] + timedelta(minutes=2)
    assert frame_gaps(bars, sessions, minutes, now)['missing_count'] == 0
    # The missing first session is detected even though no supplied candle dates it.
    result = frame_gaps(bars[expected_count:], sessions, minutes, now)
    assert result['missing_count'] == expected_count
    assert result['first_missing_at'] == bars[0].end.isoformat()
    assert 'exchange calendar history' in result['reason']


def test_missing_middle_and_final_candles_are_detected_and_not_filled():
    sessions = {'2026-09-16': session('2026-09-16')}
    bars = candles(sessions, 15)
    incomplete = [bar for i, bar in enumerate(bars) if i not in (9, 25)]
    result = frame_gaps(incomplete, sessions, 15, sessions['2026-09-16']['close'] + timedelta(minutes=2))
    assert result['missing_count'] == 2
    assert result['first_missing_at'] == bars[9].end.isoformat()
    assert len(incomplete) == 24


def test_publication_grace_excludes_not_yet_due_or_future_candles():
    sessions = {'2026-09-16': session('2026-09-16')}
    now = sessions['2026-09-16']['open'] + timedelta(minutes=15, seconds=89)
    assert frame_gaps([], sessions, 15, now)['missing_count'] == 0
    result = frame_gaps([], sessions, 15, now + timedelta(seconds=1))
    assert result['missing_count'] == 1
    assert result['first_missing_at'] == (sessions['2026-09-16']['open'] + timedelta(minutes=15)).isoformat()


def test_complete_early_close_holiday_and_weekend_do_not_create_false_gaps():
    # Thursday holiday and weekend are deliberately absent from the broker calendar.
    sessions = {'2026-11-25': session('2026-11-25'), '2026-11-27': session('2026-11-27', '13:00'),
                '2026-11-30': session('2026-11-30')}
    now = sessions['2026-11-30']['close'] + timedelta(minutes=2)
    for minutes in (15, 60, 240, 1440):
        result = frame_gaps(candles(sessions, minutes), sessions, minutes, now)
        assert result == {'missing_count': 0, 'first_missing_at': None, 'reason': None}
    early = {'2026-11-27': sessions['2026-11-27']}
    assert frame_gaps([], early, 15, now)['missing_count'] == 14
    assert frame_gaps([], early, 60, now)['missing_count'] == 3
    # The early close still owes its single partial four-hour bucket, ending at the close.
    assert frame_gaps([], early, 240, now) == {'missing_count': 1, 'first_missing_at': early['2026-11-27']['close'].isoformat(),
                                               'reason': '1 completed 240-minute candle(s) missing from the exchange calendar history'}
    assert frame_gaps([], early, 1440, now)['first_missing_at'] == early['2026-11-27']['close'].isoformat()
    full = {'2026-11-25': sessions['2026-11-25']}
    closing_only = [bar for bar in candles(full, 240) if bar.end.hour == 16]
    result = frame_gaps(closing_only, full, 240, now)
    assert result['missing_count'] == 1 and result['first_missing_at'] == full['2026-11-25']['open'].replace(hour=13, minute=30).isoformat()
    # The closing bucket is only due after the close plus the publication allowance.
    at_close = full['2026-11-25']['close']
    assert frame_gaps(candles(full, 240)[:1], full, 240, at_close + timedelta(seconds=89))['missing_count'] == 0
    assert frame_gaps(candles(full, 240)[:1], full, 240, at_close + timedelta(seconds=90))['missing_count'] == 1


def test_missing_prior_daily_session_is_detected_even_with_older_history():
    sessions = {'2026-09-15': session('2026-09-15'), '2026-09-16': session('2026-09-16')}
    bars = candles(sessions, 1440)
    now = sessions['2026-09-16']['close'] + timedelta(minutes=2)
    result = frame_gaps(bars[:1], sessions, 1440, now)
    assert result['missing_count'] == 1
    assert result['first_missing_at'] == bars[-1].end.isoformat()


def test_no_calendar_does_not_invent_gaps_for_synthetic_fixtures():
    assert frame_gaps([], {}, 15, datetime.now(timezone.utc)) == {
        'missing_count': 0, 'first_missing_at': None, 'reason': None}


def test_offsets_are_compared_as_instants_across_daylight_saving_change():
    sessions = {'2026-10-30': session('2026-10-30'), '2026-11-02': session('2026-11-02')}
    bars = [Bar(b.end.astimezone(timezone.utc), b.minutes, b.open, b.high, b.low, b.close)
            for b in candles(sessions, 60)]
    now = sessions['2026-11-02']['close'] + timedelta(minutes=2)
    assert frame_gaps(bars, sessions, 60, now)['missing_count'] == 0
    assert frame_gaps(bars[1:], sessions, 60, now)['missing_count'] == 1


def test_wrong_timeframe_or_unaligned_candles_cannot_cover_missing_expected_bucket():
    sessions = {'2026-09-16': session('2026-09-16')}
    first = sessions['2026-09-16']['open'] + timedelta(minutes=15)
    bars = [Bar(first, 60, 100, 101, 99, 100), Bar(first + timedelta(seconds=1), 15, 100, 101, 99, 100)]
    assert frame_gaps(bars, sessions, 15, first + timedelta(seconds=90))['missing_count'] == 1


def test_empty_history_reports_every_expected_completed_bucket():
    sessions = {'2026-09-16': session('2026-09-16')}
    now = sessions['2026-09-16']['close'] + timedelta(minutes=2)
    assert frame_gaps([], sessions, 15, now)['missing_count'] == 26


@pytest.mark.parametrize('minutes', [0, -1, 15.5, True])
def test_invalid_duration_cannot_loop_forever(minutes):
    with pytest.raises(ValueError):
        frame_gaps([], {'2026-09-16': session('2026-09-16')}, minutes, datetime.now(timezone.utc))
