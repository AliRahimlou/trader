"""Disconnected tests for the optional, market-only native-history worker."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import stat
from threading import Event
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from pivot.api import create_app
from pivot.feeds import FeedError, ReadOnlyFeeds
from pivot.models import Bar
from pivot.native_capture import NativeCapture, OneRequestFeeds, encoded, read_qqq, write_once
from pivot.service import Service
from pivot.store import Store


NOW = datetime(2026, 9, 18, 15, 0, 31, tzinfo=timezone.utc)
SESSIONS = {'2026-09-18': {'open': NOW.replace(hour=13, minute=30, second=0),
                           'close': NOW.replace(hour=20, minute=0, second=0)}}
BAR = Bar(SESSIONS['2026-09-18']['open'] + timedelta(minutes=5),
          5, 500, 501, 499, 500.5, 100, 500)


def feeds():
    return ReadOnlyFeeds({'APCA_API_KEY_ID': 'PRIVATE-ALPACA-KEY',
                         'APCA_API_SECRET_KEY': 'PRIVATE-ALPACA-SECRET',
                         'VIX_PROVIDER': 'insightsentry'}, insight_cache=object())


class Reader:
    def __init__(self, calls, *, failure=None):
        self.calls, self.failure, self.closed = calls, failure, False
        self.session = SimpleNamespace(close=self.close)

    def close(self):
        self.closed = True

    def stock_bars(self, *args):
        self.calls.append(args)
        if self.failure:
            raise self.failure
        return {'QQQ': [BAR]}


def test_native_stock_capture_reuses_original_receipt_after_restart(tmp_path):
    calls = []
    reader = Reader(calls)
    capture = NativeCapture(feeds(), tmp_path/'history', clock=lambda: NOW,
                            reader_factory=lambda source: reader)
    first = capture._qqq(SESSIONS, NOW)
    assert reader.closed and len(calls) == 1
    assert calls[0] == (('QQQ',), 5, SESSIONS['2026-09-18']['open'], NOW, SESSIONS)
    restarted = NativeCapture(feeds(), tmp_path/'history', clock=lambda: NOW + timedelta(days=1),
                              reader_factory=lambda source: pytest.fail('A saved month cannot be re-requested'))
    assert restarted._qqq(SESSIONS, NOW + timedelta(days=1)) == first
    assert first['received_at'] == NOW.isoformat()
    assert first['bars'][0]['end'] == BAR.end.isoformat()
    assert first['normalized_sha256'] == sha256(encoded(first['bars'])).hexdigest()
    assert 'PRIVATE-' not in json.dumps(first)
    assert (tmp_path/'history').stat().st_mode & 0o777 == 0o700
    assert (tmp_path/'history'/'qqq5-v1-2026-09.json').stat().st_mode & 0o777 == 0o600


def test_unknown_stock_request_stays_consumed_after_restart(tmp_path):
    calls = []
    reader = Reader(calls, failure=FeedError('Response lost after request'))
    capture = NativeCapture(feeds(), tmp_path/'history', clock=lambda: NOW,
                            reader_factory=lambda source: reader)
    with pytest.raises(FeedError):
        capture._qqq(SESSIONS, NOW)
    restarted = NativeCapture(feeds(), tmp_path/'history', clock=lambda: NOW,
                              reader_factory=lambda source: pytest.fail('Unknown outcome must not retry'))
    with pytest.raises(ValueError, match='claim cannot be repeated'):
        restarted._qqq(SESSIONS, NOW)
    assert reader.closed and len(calls) == 1
    assert (tmp_path/'history'/'qqq5-v1-2026-09.claim').is_file()


def saved_qqq(tmp_path):
    collector = NativeCapture(feeds(), tmp_path/'history', clock=lambda: NOW,
                              reader_factory=lambda source: Reader([]))
    payload = collector._qqq(SESSIONS, NOW)
    return collector, tmp_path/'history'/'qqq5-v1-2026-09.json', payload


@pytest.mark.parametrize('damage', [
    'wrong_source', 'wrong_symbol', 'future_receipt', 'receipt_before_request',
    'bad_hash', 'off_grid', 'forming_bar', 'bad_ohlc', 'unexpected_bar_field',
])
def test_saved_stock_artifact_rejects_invalid_identity_content_and_timing(tmp_path, damage):
    _, path, payload = saved_qqq(tmp_path)
    if damage == 'wrong_source':
        payload['provenance']['source'] = 'made-up-proxy'
    elif damage == 'wrong_symbol':
        payload['provenance']['source_symbol'] = 'SPY'
    elif damage == 'future_receipt':
        payload['received_at'] = (NOW + timedelta(seconds=1)).isoformat()
    elif damage == 'receipt_before_request':
        payload['received_at'] = (NOW - timedelta(seconds=1)).isoformat()
    elif damage == 'bad_hash':
        payload['normalized_sha256'] = 'wrong'
    elif damage == 'off_grid':
        payload['bars'][0]['end'] = (BAR.end + timedelta(seconds=1)).isoformat()
    elif damage == 'forming_bar':
        payload['bars'][0]['end'] = NOW.replace(minute=5, second=0).isoformat()
    elif damage == 'bad_ohlc':
        payload['bars'][0]['low'] = 502
    else:
        payload['bars'][0]['account'] = 'PRIVATE-ACCOUNT'
    if damage != 'bad_hash':
        payload['normalized_sha256'] = sha256(encoded(payload['bars'])).hexdigest()
    path.write_bytes(encoded(payload))
    with pytest.raises(ValueError):
        read_qqq(path, NOW)


def test_saved_stock_artifact_strips_nonmarket_fields_and_keeps_original_timestamps(tmp_path):
    _, path, payload = saved_qqq(tmp_path)
    payload['account'] = {'key': 'PRIVATE-KEY', 'equity': 100}
    payload['settings'] = {'live': True}
    payload['sessions']['2026-09-18']['key'] = 'PRIVATE-KEY'
    path.write_bytes(encoded(payload))
    recovered = read_qqq(path, NOW + timedelta(days=1))
    assert 'PRIVATE-' not in json.dumps(recovered)
    assert 'account' not in recovered and 'settings' not in recovered
    assert recovered['sessions'] == {
        day: {key: value.isoformat() for key, value in row.items()}
        for day, row in SESSIONS.items()}
    assert recovered['received_at'] == NOW.isoformat()
    assert recovered['bars'] == payload['bars']


def test_saved_stock_artifact_rejects_symlinks_and_public_files(tmp_path):
    _, path, _ = saved_qqq(tmp_path)
    symlink = tmp_path/'alias.json'
    symlink.symlink_to(path)
    with pytest.raises(OSError):
        read_qqq(symlink, NOW)
    path.chmod(0o644)
    with pytest.raises(ValueError, match='Private regular'):
        read_qqq(path, NOW)


def test_claim_fsyncs_file_and_parent_before_return_and_refuses_overwrite(tmp_path, monkeypatch):
    fsync = os.fsync
    flushed = []
    def observed(fd):
        flushed.append(stat.S_IFMT(os.fstat(fd).st_mode))
        fsync(fd)
    monkeypatch.setattr(os, 'fsync', observed)
    target = tmp_path/'claim'
    write_once(target, {'claimed': True})
    assert flushed == [stat.S_IFREG, stat.S_IFDIR]
    with pytest.raises(FileExistsError):
        write_once(target, {'claimed': False})
    assert json.loads(target.read_text()) == {'claimed': True}


def test_stock_artifact_open_checks_special_files_without_blocking(tmp_path, monkeypatch):
    _, path, _ = saved_qqq(tmp_path)
    original = os.open
    def guarded(target, flags, *args, **kwargs):
        if target == path:
            assert flags & os.O_NONBLOCK
            assert flags & os.O_NOFOLLOW
        return original(target, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', guarded)
    assert read_qqq(path, NOW)['status'] == 'captured'


def test_one_request_reader_rejects_pagination_before_second_http_call():
    reader = OneRequestFeeds(feeds())
    calls = []
    def get(url, **kwargs):
        calls.append((url, deepcopy(kwargs)))
        return SimpleNamespace(status_code=200, json=lambda: {
            'bars': {'QQQ': [{'t': SESSIONS['2026-09-18']['open'].isoformat(),
                             'o': 500, 'h': 501, 'l': 499, 'c': 500.5, 'v': 100}]},
            'next_page_token': 'a-second-page'})
    reader.session.get = get
    try:
        with pytest.raises(FeedError, match='one-request'):
            reader.stock_bars(('QQQ',), 5, SESSIONS['2026-09-18']['open'], NOW, SESSIONS)
    finally:
        reader.session.close()
    assert len(calls) == 1
    assert calls[0][0] == 'https://data.alpaca.markets/v2/stocks/bars'
    assert calls[0][1]['params']['timeframe'] == '5Min'
    assert calls[0][1]['params']['feed'] == 'iex'
    assert calls[0][1]['allow_redirects'] is False


@pytest.mark.parametrize('provider,path', [
    ('alpaca', '/v2/account'), ('alpaca', '/v2/orders'),
    ('stocks', '/v2/stocks/QQQ/quotes/latest'), ('indices', '/v1/open-close'),
])
def test_one_request_reader_cannot_access_unrelated_endpoints(provider, path):
    reader = OneRequestFeeds(feeds())
    try:
        with pytest.raises(FeedError):
            reader.get(provider, path)
        assert reader.requests == 0
    finally:
        reader.session.close()


def test_capture_uses_shared_cache_and_does_not_block_status_or_spawn_twice(tmp_path, monkeypatch):
    import pivot.native_history as native_history
    entered, release = Event(), Event()
    calls, stock_calls = [], []
    source = feeds()
    def collect(cache, calendar, now, directory):
        calls.append((cache, deepcopy(calendar), now, directory))
        entered.set()
        assert release.wait(2), 'Test did not release isolated fake provider'
        return {'status': 'captured', 'bar_count': 1}
    monkeypatch.setattr(native_history, 'capture', collect)
    collector = NativeCapture(source, tmp_path/'history', clock=lambda: NOW,
                              reader_factory=lambda value: Reader(stock_calls))
    try:
        collector.start(SESSIONS, NOW)
        assert entered.wait(1)
        first_thread = collector.thread
        assert collector.status()['status'] == 'collecting'
        collector.start(SESSIONS, NOW)
        assert collector.thread is first_thread
    finally:
        release.set()
        collector.thread.join(timeout=2)
    assert not collector.thread.is_alive()
    assert len(calls) == len(stock_calls) == 1
    assert calls[0][0] is source.insight_cache()
    assert collector.status()['status'] == 'captured'
    assert collector.status()['live_entry_ready'] is False
    collector.start(SESSIONS, NOW + timedelta(hours=1))
    assert collector.thread is first_thread


def test_optional_collector_initialization_failure_preserves_service(tmp_path, monkeypatch):
    import pivot.native_capture as capture_module
    def unavailable(*args, **kwargs):
        raise OSError('Isolated private directory failure')
    monkeypatch.setattr(capture_module, 'NativeCapture', unavailable)
    store = Store(tmp_path/'orders.sqlite3')
    control, settings = store.control(), store.settings()
    service = Service(feeds(), store)
    assert service.native_capture is None
    assert service.executor is None
    assert service.threads == []
    assert store.control() == control and store.settings() == settings


def test_native_api_reads_never_start_collection_and_sanitize_missing_export(tmp_path):
    service = Service(None, Store(tmp_path/'orders.sqlite3'))
    control, settings = service.store.control(), service.store.settings()
    class Collector:
        thread = None
        def status(self):
            return {'status': 'waiting', 'research_only': True, 'live_entry_ready': False}
        def start(self, *args):
            pytest.fail('GET endpoints cannot cause collection')
        def export(self):
            raise ValueError('PRIVATE-ALPACA-KEY /private/runtime/path')
    service.native_capture = Collector()
    with TestClient(create_app(service, background=False)) as client:
        state = client.get('/api/native-history')
        assert state.status_code == 200 and state.json()['status'] == 'waiting'
        response = client.get('/api/native-history/export')
        assert response.status_code == 404
        assert 'PRIVATE-' not in response.text and '/private/' not in response.text
        assert response.headers['cache-control'] == 'no-store'
    assert service.store.control() == control and service.store.settings() == settings
