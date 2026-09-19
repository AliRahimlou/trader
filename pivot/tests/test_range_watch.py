"""Disconnected provider and archive checks for the observation-only BTC worker."""
from copy import deepcopy
from datetime import timedelta
import json
import os
import sqlite3
from threading import Event

import pytest
import requests

import pivot.range_watch as watch_module
from pivot.feeds import FeedError
from pivot.range_reversal import analyze
from pivot.range_watch import BitcoinBars, PROVENANCE, RangeWatch
from pivot.tests.test_range_reversal import sample


PRIVATE = 'PRIVATE-TEST-CREDENTIAL'


class Response:
    def __init__(self, payload, status=200, error=None):
        self.payload, self.status_code, self.error = payload, status, error

    def json(self):
        if self.error:
            raise self.error
        return deepcopy(self.payload)


class Session:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def get(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        if self.error:
            raise self.error
        return self.response


def provider_input():
    market, now, _ = sample([111, 105])
    rows = [{'t': (bar.end - timedelta(minutes=5)).isoformat(),
             'o': bar.open, 'h': bar.high, 'l': bar.low, 'c': bar.close, 'v': bar.volume}
            for bar in market.bars[5]]
    return {'bars': {'BTC/USD': rows}, 'next_page_token': None}, market, now


def test_provider_uses_one_fixed_native_request_and_keeps_only_closed_bars():
    payload, expected, now = provider_input()
    forming = deepcopy(payload['bars']['BTC/USD'][-1])
    forming.update(t=expected.bars[5][-1].end.isoformat(), h=99999)
    payload['bars']['BTC/USD'].append(forming)
    session = Session(Response(payload))
    headers = {'APCA-API-KEY-ID': PRIVATE, 'APCA-API-SECRET-KEY': PRIVATE}
    receipt = now + timedelta(seconds=2)
    market = BitcoinBars(headers, session).read(now, clock=lambda: receipt)
    assert market.symbol == 'BTC/USD' and market.source == PROVENANCE['source']
    assert market.realtime is True and market.observed_at == receipt
    assert market.bars == expected.bars
    assert len(session.calls) == 1
    url, call = session.calls[0]
    assert url == 'https://data.alpaca.markets/v1beta3/crypto/us/bars'
    assert call['params'] == {'symbols': 'BTC/USD', 'timeframe': '5Min',
        'start': (expected.bars[5][0].end - timedelta(minutes=5)).isoformat(),
        'end': now.isoformat(), 'sort': 'asc', 'limit': 1000}
    assert call['timeout'] == (3, 10) and call['allow_redirects'] is False
    assert call['headers'] == headers


@pytest.mark.parametrize('damage', [
    'not_object', 'missing_bars', 'wrong_symbol', 'extra_symbol', 'not_list', 'pagination',
    'duplicate', 'unordered', 'off_grid', 'previous_day', 'future_start', 'naive_timestamp',
    'boolean_price', 'text_price', 'nan_price', 'negative_volume', 'bad_ohlc', 'missing_price',
])
def test_invalid_provider_evidence_is_rejected_without_retry_or_secret_leak(damage):
    payload, _, now = provider_input()
    rows = payload['bars']['BTC/USD']
    if damage == 'not_object':
        payload = []
    elif damage == 'missing_bars':
        payload = {'detail': PRIVATE}
    elif damage == 'wrong_symbol':
        payload['bars'] = {'ETH/USD': rows}
    elif damage == 'extra_symbol':
        payload['bars']['ETH/USD'] = []
    elif damage == 'not_list':
        payload['bars']['BTC/USD'] = {'secret': PRIVATE}
    elif damage == 'pagination':
        payload['next_page_token'] = PRIVATE
    elif damage == 'duplicate':
        rows.insert(1, deepcopy(rows[0]))
    elif damage == 'unordered':
        rows[0], rows[1] = rows[1], rows[0]
    elif damage == 'off_grid':
        rows[0]['t'] = (now.replace(hour=4, minute=0, second=1)).isoformat()
    elif damage == 'previous_day':
        rows[0]['t'] = (now - timedelta(days=1)).isoformat()
    elif damage == 'future_start':
        rows[-1]['t'] = (now + timedelta(minutes=5)).isoformat()
    elif damage == 'naive_timestamp':
        rows[0]['t'] = now.replace(tzinfo=None).isoformat()
    elif damage == 'boolean_price':
        rows[0]['c'] = True
    elif damage == 'text_price':
        rows[0]['c'] = PRIVATE
    elif damage == 'nan_price':
        rows[0]['c'] = float('nan')
    elif damage == 'negative_volume':
        rows[0]['v'] = -1
    elif damage == 'bad_ohlc':
        rows[0]['l'] = 1000
    else:
        del rows[0]['h']
    session = Session(Response(payload))
    with pytest.raises(FeedError) as failure:
        BitcoinBars({'key': PRIVATE}, session).read(now, clock=lambda: now)
    assert PRIVATE not in str(failure.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize('offset', [-1, 91])
def test_receipt_before_request_or_over_fetch_deadline_is_rejected(offset):
    payload, _, now = provider_input()
    with pytest.raises(FeedError):
        BitcoinBars({}, Session(Response(payload))).read(now, clock=lambda: now + timedelta(seconds=offset))


@pytest.mark.parametrize('failure', ['network', 'http', 'json'])
def test_connection_http_and_json_failures_are_sanitized(failure):
    payload, _, now = provider_input()
    session = (Session(error=requests.Timeout(PRIVATE)) if failure == 'network' else
               Session(Response({'error': PRIVATE}, status=403)) if failure == 'http' else
               Session(Response(payload, error=ValueError(PRIVATE))))
    with pytest.raises(FeedError) as caught:
        BitcoinBars({'key': PRIVATE}, session).read(now, clock=lambda: now)
    assert PRIVATE not in str(caught.value)
    assert len(session.calls) == 1


class Reader:
    def __init__(self, market):
        self.market, self.failure, self.calls = market, None, 0

    def read(self, now, *, clock):
        self.calls += 1
        if self.failure:
            raise self.failure
        result = deepcopy(self.market)
        result.observed_at = clock()
        return result


def watcher(tmp_path):
    market, now, _ = sample([111, 105])
    clock = [now]
    reader = Reader(market)
    watch = RangeWatch(reader, tmp_path / 'range.sqlite3', clock=lambda: clock[0])
    return watch, reader, clock


def test_archive_deduplicates_identical_bars_and_preserves_provider_corrections(tmp_path):
    watch, reader, clock = watcher(tmp_path)
    watch.refresh()
    first_receipt = clock[0].isoformat()
    clock[0] += timedelta(seconds=10)
    watch.refresh()
    with sqlite3.connect(watch.archive_path) as db:
        first = db.execute('SELECT received_at, inputs, analysis FROM observations').fetchall()
    assert len(first) == 1 and first[0][0] == first_receipt
    object.__setattr__(reader.market.bars[5][0], 'high', 111)
    watch.refresh()
    with sqlite3.connect(watch.archive_path) as db:
        rows = db.execute('SELECT inputs, analysis FROM observations').fetchall()
    assert len(rows) == 2
    assert {json.loads(row[0])['bars'][0]['high'] for row in rows} == {110, 111}
    assert all(json.loads(row[0])['provenance'] == PROVENANCE for row in rows)
    assert all(json.loads(row[1])['execution_status'] == 'signal' for row in rows)
    assert watch.archive_path.stat().st_mode & 0o777 == 0o600
    assert watch.snapshot()['archive_status'] == 'Recording observations'


def test_snapshot_expires_without_refetch_and_failure_cannot_reuse_old_candidates(tmp_path):
    watch, reader, clock = watcher(tmp_path)
    watch.refresh()
    snapshot = watch.snapshot()
    assert snapshot['state'] == 'SETUP_OBSERVED'
    assert snapshot['signal_ready'] is True and snapshot['execution_status'] == 'signal'
    snapshot['range']['high'] = 100000
    assert watch.snapshot()['range']['high'] == 110
    clock[0] += timedelta(seconds=91)
    assert watch.snapshot()['state'] == 'DATA_WAITING'
    assert watch.snapshot()['candidates'] == []
    assert reader.calls == 1
    reader.failure = RuntimeError(PRIVATE)
    watch.refresh()
    failed = watch.snapshot()
    assert failed['state'] == 'DATA_WAITING' and not failed['candidates']
    assert PRIVATE not in json.dumps(failed)
    reader.failure = None
    watch.refresh()
    assert watch.snapshot()['state'] == 'SETUP_OBSERVED'
    assert watch.snapshot()['signal_ready'] is False


def test_archive_failure_leaves_current_analysis_observational_and_visible(tmp_path, monkeypatch):
    watch, _, _ = watcher(tmp_path)
    monkeypatch.setattr(watch_module, 'ARCHIVE_LIMIT', 1)
    watch.refresh()
    result = watch.snapshot()
    assert result['state'] == 'SETUP_OBSERVED' and result['signal_ready'] is True
    assert result['archive_status'] == 'Observation archive needs attention'


def test_archive_symlink_is_rejected_without_modifying_target(tmp_path):
    watch, reader, clock = watcher(tmp_path)
    target = tmp_path / 'owner-file'
    target.write_text('keep this unchanged')
    watch.archive_path.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        watch._record(reader.market, analyze(reader.market, clock[0], provenance=PROVENANCE))
    assert target.read_text() == 'keep this unchanged'


def test_fifo_archive_is_rejected_without_a_blocking_open(tmp_path, monkeypatch):
    watch, reader, clock = watcher(tmp_path)
    os.mkfifo(watch.archive_path)
    original_open = os.open

    def checked_open(path, flags, *args, **kwargs):
        if os.fspath(path) == str(watch.archive_path):
            assert flags & os.O_NONBLOCK, 'Archive open can block indefinitely on a FIFO'
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(watch_module.os, 'open', checked_open)
    with pytest.raises((ValueError, OSError)):
        watch._record(reader.market, analyze(reader.market, clock[0], provenance=PROVENANCE))


def test_archive_cannot_grow_past_cap_during_insert(tmp_path, monkeypatch):
    watch, reader, clock = watcher(tmp_path)
    limit = 32 * 1024
    monkeypatch.setattr(watch_module, 'ARCHIVE_LIMIT', limit)
    rejected = False
    for revision in range(20):
        object.__setattr__(reader.market.bars[5][0], 'high', 110 + revision)
        try:
            watch._record(reader.market, analyze(reader.market, clock[0], provenance=PROVENANCE))
        except (ValueError, sqlite3.DatabaseError):
            rejected = True
            break
        assert watch.archive_path.stat().st_size <= limit
    assert rejected, 'Repeated new observations must reach the configured archive limit'
    assert watch.archive_path.stat().st_size <= limit


def test_observer_thread_stops_after_current_read_and_cannot_be_started_twice(tmp_path):
    watch, reader, _ = watcher(tmp_path)
    stopped = Event()
    read = reader.read

    def stop_after_read(now, *, clock):
        result = read(now, clock=clock)
        stopped.set()
        return result

    reader.read = stop_after_read
    watch.start(stopped)
    watch.thread.join(timeout=2)
    assert not watch.thread.is_alive() and reader.calls == 1
    assert watch.snapshot()['signal_ready'] is True
    with pytest.raises(RuntimeError, match='already started'):
        watch.start(stopped)
