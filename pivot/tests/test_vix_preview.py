from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from pivot.vix_preview import MAX_BYTES, load_preview, preview_from_document


NOW = datetime(2026, 9, 16, 20, tzinfo=timezone.utc)


@pytest.fixture
def document():
    # Portable synthetic evidence: CI must never depend on private runtime files.
    prices = {'open':16.8, 'high':16.9, 'low':16.7, 'close':16.8}
    start = NOW.replace(hour=9, minute=30)
    return {
        'purpose':'Synthetic validation fixture, not recorded market evidence',
        'source':'https://www.cnbc.com/quotes/.VIX',
        'instrument':'CBOE Volatility Index .VIX:Exchange', 'date':'2026-09-16',
        'quote_sample':{'observed_at':'2026-09-16T19:00:00Z', 'display_time':'3:00 PM EDT', 'value':17.5},
        'candles_capture':{'start':'2026-09-16T19:00:00Z', 'end':'2026-09-16T19:10:41.107Z'},
        'candles':[{'display_date':'09/16','display_time':(start+timedelta(minutes=15*i)).strftime('%H:%M'),
                    'interval_minutes':15,**prices} for i in range(22)],
        'overlap_checks':[{'source':'https://finance.yahoo.com/chart/%5EVIX',
                           'chart_timezone':'America/Chicago', 'date':'2026-09-16',
                           'display_time':f'13:{minute} CDT', 'eastern_time':f'14:{minute} EDT',
                           'captured_at':'2026-09-16T19:00:00Z', 'match':True, **prices}
                          for minute in ('00','15')],
    }


def test_synthetic_sample_is_valid_but_always_historical(document, tmp_path):
    result = preview_from_document(document, NOW)
    assert result['status'] == 'historical_sample'
    assert result['execution_eligible'] is False
    assert result['quote']['status'] == 'historical'
    assert result['quote']['value'] == 17.5
    assert result['candle_count'] == 22
    assert result['first_label'] == '09/16 09:30'
    assert result['last_label'] == '09/16 14:45'
    assert result['overlap_matches'] == result['overlap_count'] == 2
    assert result['captured_at'] == '2026-09-16T19:10:41.107000+00:00'
    sample = tmp_path / 'sample.json'
    sample.write_text(json.dumps(document))
    assert load_preview(sample, now=NOW) == result


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), True, False, 0, -1, '16.74'])
def test_nonfinite_nonnumeric_and_nonpositive_prices_are_rejected(document, value):
    document['candles'][0]['open'] = value
    assert preview_from_document(document, NOW)['status'] == 'invalid'


@pytest.mark.parametrize('mutation', [
    lambda d: d['candles'].pop(2),
    lambda d: d['candles'].insert(1, deepcopy(d['candles'][0])),
    lambda d: d['candles'].reverse(),
    lambda d: d['candles'][0].update(interval_minutes=5),
    lambda d: d['candles'][0].update(interval_minutes=True),
    lambda d: d['candles'][0].update(display_time='09:31'),
    lambda d: d['candles'][0].update(display_date='09/17'),
    lambda d: d['candles'][0].update(low=20),
    lambda d: d.update(instrument='VIX futures'),
    lambda d: d.update(source='https://example.com/VIX'),
    lambda d: d.update(date='2026-09-15'),
    lambda d: d['candles_capture'].update(end='2026-09-16T21:00:00Z'),
    lambda d: d['candles_capture'].update(start='2026-09-16T19:15:00Z'),
    lambda d: d['candles_capture'].update(end='2026-09-16T19:10:41'),
    lambda d: d['quote_sample'].update(observed_at='2026-09-16T21:00:00Z'),
    lambda d: d.update(candles=[]),
])
def test_invalid_identity_history_and_timestamps_fail_closed(document, mutation):
    mutation(document)
    result = preview_from_document(document, NOW)
    assert result['status'] == 'invalid'
    assert result['execution_eligible'] is False
    assert result['candles'] == []


def test_overlap_is_recomputed_instead_of_trusting_recorded_flag(document):
    document['overlap_checks'][0]['match'] = False
    assert preview_from_document(document, NOW)['overlap_matches'] == 2
    document['overlap_checks'][0].update(match=True, close=16.85)
    assert preview_from_document(document, NOW)['status'] == 'invalid'


@pytest.mark.parametrize('mutation', [
    lambda d: d['overlap_checks'][0].update(source='https://example.com'),
    lambda d: d['overlap_checks'][0].update(display_time='13:15 CST'),
    lambda d: d['overlap_checks'][0].update(eastern_time='14:30 EDT'),
    lambda d: d['overlap_checks'][0].update(captured_at='2026-09-16T21:00:00Z'),
    lambda d: d['overlap_checks'].append(deepcopy(d['overlap_checks'][0])),
])
def test_comparison_provenance_duplicates_and_clocks_are_checked(document, mutation):
    mutation(document)
    assert preview_from_document(document, NOW)['status'] == 'invalid'


def test_cross_day_break_is_allowed_without_claiming_full_history(document):
    earlier = deepcopy(document['candles'][:2])
    for candle in earlier:
        candle['display_date'] = '09/15'
    document['candles'] = earlier + document['candles']
    result = preview_from_document(document, NOW)
    assert result['status'] == 'historical_sample'
    assert result['candle_count'] == 24
    assert any('14-calendar-day coverage is not verified' in line for line in result['limitations'])
    assert result['execution_eligible'] is False


def test_fresh_timestamps_and_injected_live_claims_cannot_grant_eligibility(document):
    document.update(status='current', execution_eligible=True, realtime=True,
                    timeframe='REAL-TIME', limitations=['Safe to trade'], source_url='https://example.com')
    document['quote_sample'].update(status='live', execution_eligible=True)
    capture_end = datetime.fromisoformat(document['candles_capture']['end'].replace('Z', '+00:00'))
    original = deepcopy(document)
    result = preview_from_document(document, capture_end + timedelta(seconds=1))
    assert result['status'] == 'historical_sample'
    assert result['execution_eligible'] is False
    assert result['quote']['status'] == 'historical'
    assert result['source_url'] == 'https://www.cnbc.com/quotes/.VIX'
    assert 'Safe to trade' not in result['limitations']
    assert document == original


@pytest.mark.parametrize('document', [None, [], {}, {'source': 'private-token'}, {'source': True}])
def test_malformed_document_returns_a_stable_safe_schema(document):
    result = preview_from_document(document, NOW)
    assert result['status'] == 'invalid'
    assert result['execution_eligible'] is False
    assert 'private-token' not in json.dumps(result)
    assert result.keys() == load_preview('/definitely/missing', NOW).keys()


def test_missing_file_and_corrupt_file_do_not_expose_path_or_error(tmp_path):
    secret_path = tmp_path / 'private-token.json'
    missing = load_preview(secret_path, NOW)
    assert missing['status'] == 'unavailable'
    secret_path.write_text('{"private-token":')
    invalid = load_preview(secret_path, NOW)
    assert invalid['status'] == 'invalid'
    assert 'private-token' not in json.dumps([missing, invalid])


def test_oversized_file_is_rejected(tmp_path):
    sample = tmp_path / 'large.json'
    sample.write_bytes(b' ' * (MAX_BYTES + 1))
    assert load_preview(sample, NOW)['status'] == 'invalid'


def test_current_time_must_be_timezone_aware(document):
    assert preview_from_document(document, NOW.replace(tzinfo=None))['status'] == 'invalid'
