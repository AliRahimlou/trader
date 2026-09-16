from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from pivot.insight_validation import validate_insight_vix


NOW = datetime(2026, 9, 16, 19, 37, 20, tzinfo=timezone.utc)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


@pytest.fixture
def sample():
    first = stamp('2026-09-16T13:30:00Z')
    bars = [{'time': first + 900 * n, 'open': 17.0, 'high': 18.0, 'low': 16.0, 'close': 17.5}
            for n in range(25)]
    return {
        'info': {'code': 'CBOE:VIX', 'type': 'INDEX', 'delay_seconds': 0},
        'quotes': {'last_update': stamp('2026-09-16T19:37:11Z') * 1000,
                   'data': [{'code': 'CBOE:VIX', 'last_price': 18.05,
                             'lp_time': stamp('2026-09-16T19:37:10Z'), 'delay_seconds': 0}]},
        'series': {'code': 'CBOE:VIX', 'bar_type': '15m', 'series': bars,
                   'last_update': stamp('2026-09-16T19:37:18Z') * 1000,
                   'bar_end': stamp('2026-09-16T19:45:00Z')},
    }


def validate(sample, **kwargs):
    return validate_insight_vix(**sample, now=kwargs.pop('now', NOW), **kwargs)


def test_valid_sample_excludes_forming_candle_without_enabling_execution(sample):
    before = deepcopy(sample)
    result = validate(sample)
    assert result['status'] == 'valid_sample'
    assert result['execution_eligible'] is False
    assert result['coverage_verified'] is False
    assert result['quote']['age_seconds'] == 10
    assert result['series']['completed_regular_count'] == 24
    assert result['series']['incomplete_count'] == 1
    assert result['series']['latest_completed_at'] == '2026-09-16T19:30:00+00:00'
    assert result['series']['bar_end_convention'] == 'exclusive'
    assert result['missing_count'] == 0
    assert sample == before


def test_inclusive_bar_end_is_supported_but_never_changes_completion(sample):
    sample['series']['bar_end'] -= 1
    result = validate(sample)
    assert result['status'] == 'valid_sample'
    assert result['series']['bar_end_convention'] == 'inclusive_second'
    assert result['series']['incomplete_count'] == 1


@pytest.mark.parametrize('value', [True, False, '18.05', None, float('inf'), float('-inf'), float('nan'), 0, -1])
def test_invalid_prices_fail_closed(sample, value):
    sample['quotes']['data'][0]['last_price'] = value
    result = validate(sample)
    assert result['status'] == 'invalid_sample'
    assert result['execution_eligible'] is False


@pytest.mark.parametrize('field', ['lp_time', 'last_update', 'bar_end', 'time'])
@pytest.mark.parametrize('value', [True, '1789587440', float('inf'), float('nan')])
def test_invalid_timestamp_numbers_fail_closed(sample, field, value):
    if field == 'lp_time':
        sample['quotes']['data'][0][field] = value
    elif field == 'time':
        sample['series']['series'][0][field] = value
    else:
        sample['series'][field] = value
    assert validate(sample)['status'] == 'invalid_sample'


@pytest.mark.parametrize('mutation', [
    lambda s: s['info'].update(code='CBOE:VX1!'),
    lambda s: s['info'].update(type='FUTURES'),
    lambda s: s['info'].update(error='secret-marker'),
    lambda s: s['info'].pop('delay_seconds'),
    lambda s: s['info'].update(delay_seconds=900),
    lambda s: s['info'].update(delay_seconds=False),
    lambda s: s['quotes']['data'][0].update(code='AMEX:VXX'),
    lambda s: s['quotes'].update(error='secret-marker'),
    lambda s: s['quotes']['data'][0].update(delay_seconds=-1),
    lambda s: s['quotes']['data'][0].update(delay_seconds=True),
    lambda s: s['quotes']['data'][0].pop('delay_seconds'),
    lambda s: s['quotes']['data'].append(deepcopy(s['quotes']['data'][0])),
    lambda s: s['quotes']['data'][0].update(lp_time=NOW.timestamp() - 91),
    lambda s: s['quotes']['data'][0].update(lp_time=NOW.timestamp() + 1),
    lambda s: s['quotes'].update(last_update=(NOW.timestamp() - 91) * 1000),
    lambda s: s['series'].update(code='CBOE:VX1!'),
    lambda s: s['series'].update(bar_type='1m'),
    lambda s: s['series'].update(bar_end=NOW.timestamp()),
    lambda s: s['series'].update(last_update=(NOW.timestamp() - 91) * 1000),
    lambda s: s['series']['series'].reverse(),
    lambda s: s['series']['series'].insert(1, deepcopy(s['series']['series'][0])),
    lambda s: s['series']['series'][0].update(time=s['series']['series'][0]['time'] + 1),
    lambda s: s['series']['series'][0].pop('open'),
    lambda s: s['series']['series'][0].update(open=float('nan')),
    lambda s: s['series']['series'][0].update(low=19),
])
def test_identity_delay_freshness_and_candle_failures(sample, mutation):
    mutation(sample)
    result = validate(sample)
    assert result['status'] == 'invalid_sample'
    assert result['execution_eligible'] is False


def test_intraday_gap_is_detected(sample):
    sample['series']['series'].pop(3)
    result = validate(sample)
    assert result['status'] == 'invalid_sample'
    assert result['missing_count'] == 1
    assert result['first_missing_at'] == '2026-09-16T14:30:00+00:00'


def test_entire_absent_session_is_detected_only_with_calendar(sample):
    sessions = {'2026-09-15': {'open': '2026-09-15T13:30:00Z', 'close': '2026-09-15T20:00:00Z'},
                '2026-09-16': {'open': '2026-09-16T13:30:00Z', 'close': '2026-09-16T20:00:00Z'}}
    result = validate(sample, sessions=sessions)
    assert result['status'] == 'invalid_sample'
    assert result['missing_count'] == 26
    assert result['coverage_verified'] is False
    unverified = validate(sample)
    assert unverified['status'] == 'valid_sample'
    assert unverified['coverage_verified'] is False
    assert any('entirely missing sessions' in text for text in unverified['limitations'])


def test_calendar_checks_session_edges(sample):
    sessions = {'2026-09-16': {'open': '2026-09-16T13:30:00Z', 'close': '2026-09-16T20:00:00Z'}}
    assert validate(sample, sessions=sessions)['coverage_verified'] is True
    sample['series']['series'].pop(0)
    result = validate(sample, sessions=sessions)
    assert result['status'] == 'invalid_sample'
    assert result['first_missing_at'] == '2026-09-16T13:45:00+00:00'


def test_pre_market_candle_is_excluded_without_creating_a_gap(sample):
    candle = deepcopy(sample['series']['series'][0])
    candle['time'] -= 900
    sample['series']['series'].insert(0, candle)
    result = validate(sample)
    assert result['status'] == 'valid_sample'
    assert result['series']['excluded_count'] == 1
    assert result['series']['completed_regular_count'] == 24


def test_candle_completion_uses_source_update_not_just_wall_clock(sample):
    now = datetime(2026, 9, 16, 19, 45, 5, tzinfo=timezone.utc)
    sample['series']['last_update'] = stamp('2026-09-16T19:44:59Z') * 1000
    sample['quotes']['last_update'] = now.timestamp() * 1000
    sample['quotes']['data'][0]['lp_time'] = now.timestamp()
    result = validate(sample, now=now)
    assert result['status'] == 'valid_sample'
    assert result['series']['latest_completed_at'] == '2026-09-16T19:30:00+00:00'
    assert result['series']['incomplete_count'] == 1


def test_missing_latest_completed_candle_is_rejected_after_publication_allowance(sample):
    sample['series']['series'] = sample['series']['series'][:-2]
    sample['series']['bar_end'] = stamp('2026-09-16T19:15:00Z')
    result = validate(sample)
    assert result['status'] == 'invalid_sample'
    assert 'latest completed' in result['message']


def test_early_close_calendar_controls_expectations(sample):
    sample['series']['series'] = sample['series']['series'][:14]
    sample['series']['bar_end'] = stamp('2026-09-16T17:00:00Z')
    sessions = {'2026-09-16': {'open': '2026-09-16T13:30:00Z', 'close': '2026-09-16T17:00:00Z'}}
    result = validate(sample, sessions=sessions)
    assert result['status'] == 'valid_sample'
    assert result['coverage_verified'] is True
    assert result['series']['latest_completed_at'] == '2026-09-16T17:00:00+00:00'


def test_injected_readiness_flags_and_errors_cannot_escape_into_result(sample):
    for section in sample.values():
        section.update(execution_eligible=True, status='current', token='secret-marker')
    result = validate(sample)
    assert result['status'] == 'valid_sample'
    assert result['execution_eligible'] is False
    assert 'secret-marker' not in json.dumps(result)
    sample['series']['series'] = 'secret-marker'
    result = validate(sample)
    assert result['status'] == 'invalid_sample'
    assert 'secret-marker' not in json.dumps(result)


@pytest.mark.parametrize('now', [None, 'secret-marker', datetime(2026, 9, 16), float('inf')])
def test_invalid_validation_clock_returns_sanitized_failure(sample, now):
    result = validate(sample, now=now)
    assert result['status'] == 'invalid_sample'
    assert 'secret-marker' not in json.dumps(result)


def test_aware_offset_clocks_are_normalized(sample):
    result = validate(sample, now=NOW.astimezone(timezone(timedelta(hours=-4))))
    assert result['checked_at'] == NOW.isoformat()


def test_weekend_break_is_not_an_intraday_gap(sample):
    prior = deepcopy(sample['series']['series'][0])
    prior['time'] = stamp('2026-09-11T19:45:00Z')
    sample['series']['series'].insert(0, prior)
    result = validate(sample)
    assert result['status'] == 'valid_sample'
    assert result['coverage_verified'] is False
    assert result['missing_count'] == 0


@pytest.mark.parametrize('sessions', [
    {},
    {'day': {'open': 'secret-marker', 'close': 'secret-marker'}},
    {'day': {'open': '2026-09-16T13:30:00', 'close': '2026-09-16T20:00:00'}},
    {'day': {'open': '2026-09-16T20:00:00Z', 'close': '2026-09-16T13:30:00Z'}},
    {'day': {'open': '2026-09-16T13:31:00Z', 'close': '2026-09-16T20:00:00Z'}},
])
def test_malformed_supplied_calendar_fails_without_echoing_values(sample, sessions):
    result = validate(sample, sessions=sessions)
    assert result['status'] == 'invalid_sample'
    assert 'secret-marker' not in json.dumps(result)


def test_standard_time_uses_new_york_offset_not_fixed_utc_hours(sample):
    now = datetime(2026, 11, 16, 20, 37, 20, tzinfo=timezone.utc)
    shift = now - NOW
    for candle in sample['series']['series']:
        candle['time'] += shift.total_seconds()
    sample['series']['bar_end'] += shift.total_seconds()
    sample['series']['last_update'] += shift.total_seconds() * 1000
    sample['quotes']['last_update'] += shift.total_seconds() * 1000
    sample['quotes']['data'][0]['lp_time'] += shift.total_seconds()
    result = validate(sample, now=now)
    assert result['status'] == 'valid_sample'
    assert result['series']['completed_regular_count'] == 24
    assert result['series']['first_completed_at'] == '2026-11-16T14:45:00+00:00'
    assert result['series']['latest_completed_at'] == '2026-11-16T20:30:00+00:00'
