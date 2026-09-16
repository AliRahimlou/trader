from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from pivot.data_health import expire_health, vix_health
from pivot.models import Bar, Market


NOW = datetime(2026, 9, 16, 16, 5, tzinfo=timezone.utc)


@pytest.fixture
def history():
    bars = [Bar(NOW.replace(minute=0)-timedelta(minutes=15*n), 15, 20, 21, 19, 20)
            for n in (2, 1, 0)]
    return Market('I:VIX', {15: bars}, 'insightsentry', True,
                  NOW-timedelta(minutes=4), valid_until=NOW.replace(minute=16, second=30))


@pytest.fixture
def details():
    return {'provider': 'insightsentry', 'timeframe': 'REAL-TIME', 'market_open': True,
            'history_received_at': (NOW-timedelta(minutes=4)).isoformat(),
            'budget': {'used': 10, 'limit': 1000, 'remaining': 990}}


def test_completed_history_is_ready_without_claiming_a_quote_is_fresh(history, details):
    before = deepcopy(history), deepcopy(details)
    health = vix_health(history, NOW, details=details)
    assert health['status'] == 'current'
    assert health['candles_current'] is True
    assert health['entry_quote_required'] is True
    assert health['source'] == 'InsightSentry · actual Cboe VIX'
    assert health['latest_at'] == '2026-09-16T16:00:00+00:00'
    assert health['valid_until'] == history.valid_until.isoformat()
    assert health['error'] is None
    assert 'latest_value_at' not in health['verification']
    assert (history, details) == before


def test_an_old_quote_does_not_invalidate_completed_history_or_get_refreshed(history, details):
    old = (NOW-timedelta(minutes=20)).isoformat()
    details.update(latest_value_at=old, latest_value=20)
    health = vix_health(history, NOW, details=details)
    assert health['status'] == 'current'
    assert health['verification']['latest_value_at'] == old
    assert health['entry_quote_required'] is True


@pytest.mark.parametrize('change', [
    {'source': 'unverified'},
    {'source': 'massive_indices'},
    {'symbol': 'VXX'},
    {'realtime': False},
    {'realtime': 1},
    {'observed_at': NOW+timedelta(seconds=1)},
    {'observed_at': NOW-timedelta(seconds=991)},
    {'valid_until': NOW-timedelta(seconds=1)},
    {'valid_until': None},
])
def test_wrong_identity_delayed_stale_or_future_history_is_blocked(history, details, change):
    health = vix_health(replace(history, **change), NOW, details=details)
    assert health['status'] == 'blocked'
    assert health['candles_current'] is False
    assert health['error']


@pytest.mark.parametrize('changes', [
    {'timeframe': 'DELAYED'}, {'timeframe': None}, {'provider': 'unverified'}, {'error': 'Access denied'},
    {'delay_seconds': 900}, {'delay_seconds': False}, {'delay_seconds': '0'},
])
def test_invalid_metadata_never_reports_candles_ready(history, details, changes):
    details.update(changes)
    assert vix_health(history, NOW, details=details)['status'] == 'blocked'


@pytest.mark.parametrize('bars', ['few', 'duplicate', 'reversed', 'stale'])
def test_invalid_or_insufficient_completed_bars_are_blocked(history, details, bars):
    original = history.bars[15]
    replacement = original[:2] if bars == 'few' else [*original, original[-1]] if bars == 'duplicate' else list(reversed(original)) if bars == 'reversed' else [replace(b, end=b.end-timedelta(hours=1)) for b in original]
    assert vix_health(replace(history, bars={15: replacement}), NOW, details=details)['status'] == 'blocked'


def test_market_closed_preserves_saved_history_without_execution_readiness(history, details):
    later = NOW+timedelta(hours=6)
    details['market_open'] = False
    health = vix_health(history, later, details=details)
    assert health['status'] == 'market_closed'
    assert health['candles_current'] is False
    assert health['bar_count'] == 3
    assert health['error'] is None
    report = {'stocks': {'status': 'current', 'instruments': []}, 'vix': health, 'ready': False}
    expire_health(report, later+timedelta(seconds=1))
    assert report['vix']['status'] == 'market_closed'
    assert report['ready'] is False


def test_closed_session_cannot_hide_missing_data_or_errors(history, details):
    details['market_open'] = False
    for market, error in [(None, None), (history, 'Access denied'),
                          (replace(history, source='unverified'), None),
                          (replace(history, observed_at=NOW+timedelta(seconds=1)), None),
                          (replace(history, bars={15: []}), None)]:
        assert vix_health(market, NOW, error=error, details=details)['status'] == 'blocked'


def test_history_expiry_updates_the_status_and_candle_flag(history, details):
    health = vix_health(history, NOW, details=details)
    report = {'stocks': {'status': 'current', 'instruments': []}, 'vix': health, 'ready': True}
    expire_health(report, history.valid_until+timedelta(seconds=1))
    assert report['vix']['status'] == 'blocked'
    assert report['vix']['candles_current'] is False
    assert report['ready'] is False
    assert health['verification']['history_received_at'] == details['history_received_at']
