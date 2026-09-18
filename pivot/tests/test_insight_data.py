from copy import deepcopy
from datetime import timedelta

import pytest

from pivot.feeds import FeedError, ReadOnlyFeeds
from pivot.insight_data import load_history, confirm_quote
from pivot.strategy import vix_candles_fresh
from pivot.tests.test_insight_cache import NOW, SESSIONS, INFO, series, quote, Session, cache


class Saved:
    def __init__(self):
        self.info = INFO.copy()
        self.series = series()
        self.point = quote()
        self.received = NOW
        self.status = 'cached'
        self.quote_calls = 0

    def envelope(self, payload):
        return {'payload': deepcopy(payload), 'received_at': self.received.isoformat(),
                'source_updated_at': NOW.isoformat(), 'status': self.status,
                'next_refresh_at': (NOW + timedelta(minutes=15)).isoformat()}

    def metadata(self, now): return self.envelope(self.info)
    def history(self, now, sessions): return self.envelope(self.series)
    def quote(self, now):
        self.quote_calls += 1
        return self.envelope(self.point)
    def peek_quote(self, now): return {'payload': None}
    def diagnostics(self, now):
        return dict(budget_used=52, budget_limit=900, reserved_requests=50, requests_recorded=2)


def test_real_cache_pipeline_collects_once_and_preserves_source_times(tmp_path):
    network = Session(INFO, series())
    provider = cache(tmp_path, network)
    first, details = load_history(provider, NOW, SESSIONS, clock=lambda: NOW)
    later = NOW + timedelta(minutes=10)
    second, more = load_history(provider, later, SESSIONS, clock=lambda: later)
    assert first.observed_at == second.observed_at == NOW
    assert len(first.bars[15]) == 6 and first.bars[15][-1].end == NOW.replace(second=0)
    assert first.bars == second.bars and len(network.calls) == 2
    assert vix_candles_fresh(second, later)
    assert details['budget']['used'] == more['budget']['used'] == 52
    assert details['entry_quote_required'] and details['market_open']


def test_cached_forming_bar_never_becomes_completed_by_passage_of_time():
    provider = Saved()
    later = NOW.replace(minute=15, second=15)
    market, _ = load_history(provider, later, SESSIONS, clock=lambda: later)
    assert market.bars[15][-1].end == NOW.replace(second=0)
    # Once the grace window elapses, that cached forming bar cannot fill the gap.
    expired = later.replace(second=31) + timedelta(minutes=1)
    with pytest.raises(FeedError): load_history(provider, expired, SESSIONS, clock=lambda: expired)


@pytest.mark.parametrize('change', [
    lambda p: p.info.update(code='CBOE:VX1!'),
    lambda p: p.info.update(type='CFD'),
    lambda p: p.info.update(delay_seconds=900),
    lambda p: p.info.update(delay_seconds=False),
    lambda p: p.series.update(code='VIXY'),
    lambda p: p.series.update(bar_type='1m'),
    lambda p: p.series['series'].pop(2),
    lambda p: p.series['series'].reverse(),
    lambda p: p.series['series'][1].update(open=True),
    lambda p: p.series['series'][1].update(high=float('nan')),
    lambda p: p.series.update(last_update=(NOW + timedelta(seconds=1)).timestamp()*1000),
    lambda p: setattr(p, 'received', NOW + timedelta(seconds=1)),
    lambda p: setattr(p, 'status', 'budget_exhausted'),
    lambda p: setattr(p, 'status', 'unavailable'),
])
def test_malformed_or_unavailable_history_blocks_even_when_cached(change):
    provider = Saved(); change(provider)
    with pytest.raises(FeedError): load_history(provider, NOW, SESSIONS, clock=lambda: NOW)


def test_calendar_is_mandatory_and_entirely_missing_session_is_detected():
    for sessions in (None, {}, {**SESSIONS, '2026-09-15': {
            'open': SESSIONS['2026-09-16']['open']-timedelta(days=1),
            'close': SESSIONS['2026-09-16']['close']-timedelta(days=1)}}):
        with pytest.raises(FeedError): load_history(Saved(), NOW, sessions, clock=lambda: NOW)


def test_original_receipt_is_not_refreshed_and_metadata_expires():
    provider = Saved()
    later = NOW + timedelta(days=1)
    with pytest.raises(FeedError): load_history(provider, later, SESSIONS, clock=lambda: later)


def test_valid_closed_history_is_retained_for_display_without_claiming_freshness():
    provider = Saved()
    close = NOW.replace(hour=20, minute=0, second=31)
    provider.series = series(close)
    provider.received = close
    later = close + timedelta(hours=1)
    market, details = load_history(provider, later, SESSIONS, clock=lambda: later)
    assert market.bars[15][-1].end == close.replace(second=0)
    assert len(market.bars[15]) == 26 and not details['market_open']
    assert not vix_candles_fresh(market, later)


def test_source_watermark_prevents_premature_completion():
    provider = Saved()
    provider.series['last_update'] = NOW.replace(minute=0, second=0).timestamp()*1000 - 1
    provider.series['series'].pop()
    provider.series['bar_end'] = NOW.replace(second=0).timestamp()
    later = NOW + timedelta(minutes=2)
    with pytest.raises(FeedError): load_history(provider, later, SESSIONS, clock=lambda: later)


@pytest.mark.parametrize('change', [
    lambda p: p.point['data'][0].update(lp_time=(NOW-timedelta(seconds=91)).timestamp()),
    lambda p: p.point['data'][0].update(lp_time=(NOW+timedelta(seconds=1)).timestamp()),
    lambda p: p.point['data'][0].update(code='CAPITALCOM:VIX'),
    lambda p: p.point['data'][0].update(delay_seconds=900),
    lambda p: p.point['data'][0].update(last_price=float('inf')),
    lambda p: p.point['data'].append(p.point['data'][0]),
    lambda p: setattr(p, 'status', 'stale'),
    lambda p: setattr(p, 'status', 'budget_exhausted'),
])
def test_bad_entry_quote_cannot_be_used(change):
    provider = Saved(); change(provider)
    with pytest.raises(FeedError): confirm_quote(provider, NOW, clock=lambda: NOW)


def test_entry_quote_uses_actual_index_source_timestamp():
    provider = Saved()
    result = confirm_quote(provider, NOW, clock=lambda: NOW)
    assert result == {'source':'insightsentry', 'symbol':'I:VIX', 'delay_seconds':0,
                      'value':17, 'updated_at':(NOW-timedelta(seconds=5)).isoformat()}
    assert provider.quote_calls == 1


def test_provider_selection_uses_insightsentry_key_without_secret_in_diagnostics():
    assert ReadOnlyFeeds({}).vix_provider == 'massive'
    assert ReadOnlyFeeds({'INSIGHTSENTRY_API_KEY':'test-key'}).vix_provider == 'insightsentry'
    with pytest.raises(ValueError): ReadOnlyFeeds({'VIX_PROVIDER':'unverified'})


def test_analysis_waits_for_stock_calendar_and_accepts_post_download_receipt(tmp_path):
    from datetime import datetime, timezone
    from pivot.models import Bar, Market
    from pivot.service import Service
    from pivot.store import Store

    class Feeds:
        vix_provider = 'insightsentry'
        def stocks(self, now):
            self.stock_sessions = {}
            self.stock_started = now
            return {}
        def vix(self, now):
            assert hasattr(self, 'stock_sessions')
            assert now >= self.stock_started
            receipt = datetime.now(timezone.utc)
            end = receipt.replace(minute=receipt.minute//15*15, second=0, microsecond=0)
            bars = [Bar(end-timedelta(minutes=15*i),15,17,18,16,17) for i in (2,1,0)]
            self.vix_diagnostics = {'provider':'insightsentry','timeframe':'REAL-TIME','market_open':True,'delay_seconds':0}
            return Market('I:VIX',{15:bars},'insightsentry',True,receipt,valid_until=end+timedelta(seconds=990))

    feeds = Feeds()
    service = Service(feeds, Store(tmp_path/'state.db'))
    service.refresh_analysis()
    state = service.snapshot()
    assert state['data_health']['vix']['status'] == 'current'
    assert not state['data_errors'] and not state['live_enabled']
    assert not state['data_health']['ready']  # Missing equities still block entries.


def test_recovery_details_are_exposed_without_raw_provider_errors(tmp_path):
    import requests
    provider=cache(tmp_path,Session(requests.Timeout('private-secret')))
    report={}
    with pytest.raises(FeedError):
        load_history(provider,NOW,SESSIONS,clock=lambda:NOW,report=report)
    assert report['retry_at']==(NOW+timedelta(seconds=30)).isoformat()
    assert 'bounded recovery' in report['retry_reason']
    assert 'private-secret' not in str(report)
    assert report['budget']['recovery_day_used']==0
