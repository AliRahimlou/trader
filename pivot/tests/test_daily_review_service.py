from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Event, Thread
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from pivot.api import create_app
from pivot.service import Service
from pivot.store import Store


class ReportingFeed:
    def __init__(self):
        self.ref = sha256(b'reporting-test').hexdigest()
        self.requests = []
        self.entered = self.release = None

    def account(self):
        return {'account_ref': self.ref, 'currency': 'USD', 'equity': '100.00', 'mode': 'live'}

    def get(self, provider, path, params=None):
        self.requests.append((provider, path, params))
        assert provider == 'alpaca' and path == '/v2/account/activities'
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(5)
        return []


def ready_service(tmp_path):
    feeds = ReportingFeed()
    service = Service(feeds, Store(tmp_path / 'audit.db'))
    now = datetime.now(timezone.utc)
    service.state.update(account=feeds.account(), account_at=now.isoformat())
    return service, feeds, now


def test_reports_are_durable_read_only_and_account_scoped(tmp_path):
    service, feeds, now = ready_service(tmp_path)
    permissions = service.store.deployment_permission()
    service.refresh_daily_review(now)
    day = now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    report = service.daily_review(day)
    assert report['day'] == day and report['revision'] >= 1
    assert set(report['families']) == {'socrates', 'range_reversal'}
    assert report['collection']['status'] == 'current'
    assert service.store.deployment_permission() == permissions
    count = len(feeds.requests)
    client = TestClient(create_app(service, background=False))
    assert client.get('/api/reviews', params={'day': day}).json()['revision'] == report['revision']
    assert client.get('/api/reviews/history').status_code == 200
    assert len(feeds.requests) == count, 'HTTP report reads must never query the broker'
    restarted = Service(feeds, Store(tmp_path / 'audit.db'))
    restarted.state.update(account=feeds.account(), account_at=now.isoformat())
    assert restarted.daily_review(day)['revision'] == report['revision']
    restarted.state['account'] = {'account_ref': sha256(b'other-account').hexdigest()}
    assert restarted.daily_review(day)['status'] == 'unavailable'


def test_report_refresh_does_not_block_api_or_order_controls(tmp_path):
    service, feeds, now = ready_service(tmp_path)
    feeds.entered, feeds.release = Event(), Event()
    worker = Thread(target=service.refresh_daily_review, args=(now,), daemon=True)
    worker.start()
    try:
        assert feeds.entered.wait(2)
        client = TestClient(create_app(service, background=False))
        assert client.get('/api/health').json()['ok'] is True
        assert client.get('/api/reviews').status_code == 200
        assert service.store.control()['enabled'] is False
    finally:
        feeds.release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_reporting_failure_is_visible_and_preserves_saved_report(tmp_path):
    service, feeds, now = ready_service(tmp_path)
    service.refresh_daily_review(now)
    saved = service.daily_review()
    service.state['account_error'] = 'Account temporarily unavailable'
    service.refresh_daily_review(now + timedelta(seconds=1))
    result = service.daily_review()
    assert result['revision'] == saved['revision']
    assert result['collection']['status'] == 'unavailable'
    assert result['collection']['account_status'] == 'unavailable'
    assert service.state['data_errors'] == []
    assert service.state['crypto_worker_error'] is None


def test_invalid_dates_and_limits_rejected_without_provider_calls(tmp_path):
    service, feeds, _ = ready_service(tmp_path)
    client = TestClient(create_app(service, background=False))
    for day in ['yesterday', '2026-02-30', '20260919', '2026-09-19T00:00:00']:
        assert client.get('/api/reviews', params={'day': day}).status_code == 422
    for limit in [0, 101, -1]:
        assert client.get('/api/reviews/history', params={'limit': limit}).status_code == 422
    assert feeds.requests == []


def test_stale_account_does_not_collect_into_an_unverified_identity(tmp_path):
    service, feeds, now = ready_service(tmp_path)
    service.state['account_at'] = (now - timedelta(minutes=5)).isoformat()
    service.refresh_daily_review(now)
    assert feeds.requests == []
    assert service.daily_review()['collection']['status'] == 'unavailable'


def test_snapshot_keeps_its_original_account_report_during_account_switch(tmp_path, monkeypatch):
    service, feeds, now = ready_service(tmp_path)
    service.refresh_daily_review(now)
    original_scope = service.daily_review()['account_scope']

    def switch_during_snapshot():
        service.state['account'] = {**feeds.account(), 'account_ref': sha256(b'other').hexdigest()}
        return {'status': 'unavailable'}

    monkeypatch.setattr(service.store, 'session_review', switch_during_snapshot)
    result = service.snapshot()
    assert result['account']['account_ref'] == feeds.ref
    assert result['daily_review']['account_scope'] == original_scope
    assert service.daily_review()['status'] == 'unavailable'
    assert service.daily_review()['collection']['status'] == 'waiting_account'


def test_late_fee_revises_a_report_older_than_regular_refresh_window(tmp_path):
    service, feeds, now = ready_service(tmp_path)
    earlier = now - timedelta(days=10)
    old_day = earlier.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    service.state['account_at'] = earlier.isoformat()
    service.refresh_daily_review(earlier)
    old_report = service.daily_review(old_day)
    service.state['account_at'] = now.isoformat()
    feeds.get = lambda *_args: [{'id': 'newly-posted-old-fee', 'activity_type': 'FEE',
                                'date': old_day, 'net_amount': '-0.025', 'status': 'executed'}]
    service.refresh_daily_review(now)
    updated = service.daily_review(old_day)
    assert updated['revision'] > old_report['revision']
    assert updated['accounting']['fees']['observed_usd_cost'] == '0.025'
    assert updated['accounting']['data_complete'] is False
    assert updated['accounting']['fees']['final'] is False
