from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import time

import pytest
import requests

from pivot.insight_cache import InsightCache, SYMBOL

NOW = datetime(2026, 9, 16, 15, 0, 31, tzinfo=timezone.utc)
SESSIONS = {'2026-09-16': {'open': NOW.replace(hour=13, minute=30, second=0),
                           'close': NOW.replace(hour=20, minute=0, second=0)}}


def series(now=NOW):
    start = SESSIONS['2026-09-16']['open']
    rows = []
    while start <= now:
        rows.append({'time': start.timestamp(), 'open': 16, 'high': 18, 'low': 15, 'close': 17})
        start += timedelta(minutes=15)
    return {'code': SYMBOL, 'bar_type': '15m', 'last_update': now.timestamp() * 1000,
            'bar_end': start.timestamp(), 'series': rows}


def quote(now=NOW):
    return {'data': [{'code': SYMBOL, 'delay_seconds': 0, 'last_price': 17,
                      'lp_time': now.timestamp() - 5}], 'last_update': now.timestamp() * 1000}


INFO = {'code': SYMBOL, 'type': 'INDEX', 'delay_seconds': 0}


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        yield json.dumps(self.payload).encode()


class Session:
    def __init__(self, *responses, delay=0):
        self.responses, self.calls, self.delay = iter(responses), [], delay
        self.lock = threading.Lock()

    def get(self, url, **options):
        with self.lock:
            self.calls.append((url, options))
            response = next(self.responses)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(response, Exception):
            raise response
        return Response(response)


def cache(tmp_path, session, **kwargs):
    # Simulated requests finish immediately in logical test time. Dedicated
    # receipt-clock tests below cover time advancing during a real request.
    reference = [NOW]
    provider = InsightCache(tmp_path / 'vix.sqlite3', 'test-key', session,
                            clock=lambda: reference[0], **kwargs)
    for name in ('history', 'quote', 'metadata'):
        operation = getattr(provider, name)
        def at_time(now, *args, operation=operation):
            reference[0] = now
            return operation(now, *args)
        setattr(provider, name, at_time)
    return provider


def test_history_persists_payload_and_source_times_without_repolling(tmp_path):
    session = Session(series())
    first = cache(tmp_path, session).history(NOW, SESSIONS)
    assert first['status'] == 'current'
    assert first['next_refresh_at'] == '2026-09-16T15:15:30+00:00'
    first['payload']['series'][-1]['close'] = 999
    second = cache(tmp_path, session).history(NOW + timedelta(minutes=4), SESSIONS)
    assert second['payload'] == series()
    assert second['source_updated_at'] == NOW.isoformat()
    assert second['received_at'] == NOW.isoformat()
    assert second['budget_used'] == 51
    assert len(session.calls) == 1
    url, options = session.calls[0]
    assert url == 'https://api.insightsentry.com/v3/symbols/CBOE%3AVIX/series'
    assert options['allow_redirects'] is False
    assert options['params']['dp'] == 1000
    assert 'test-key' not in json.dumps(second)
    assert (tmp_path / 'vix.sqlite3').stat().st_mode & 0o777 == 0o600


def test_bar_boundary_waits_30_seconds_then_collects_once(tmp_path):
    next_bar = NOW + timedelta(minutes=15)
    session = Session(series(), series(next_bar))
    provider = cache(tmp_path, session)
    provider.history(NOW, SESSIONS)
    provider.history(next_bar - timedelta(seconds=2), SESSIONS)
    assert len(session.calls) == 1
    assert provider.history(next_bar, SESSIONS)['status'] == 'current'
    assert len(session.calls) == 2


def test_transient_failure_recovers_once_after_backoff_across_restart(tmp_path):
    recovered_at = NOW + timedelta(seconds=30)
    session = Session(requests.ConnectionError('test-key secret'), series(recovered_at))
    first = cache(tmp_path, session).history(NOW, SESSIONS)
    assert first['status'] == 'unavailable'
    assert first['retry_at'] == recovered_at.isoformat()
    assert 'secret' not in first['error']
    restarted = cache(tmp_path, session)
    assert restarted.history(NOW + timedelta(seconds=29), SESSIONS)['status'] == 'retry_wait'
    assert len(session.calls) == 1
    assert restarted.history(recovered_at, SESSIONS)['status'] == 'current'
    assert restarted.history(recovered_at + timedelta(seconds=30), SESSIONS)['status'] == 'cached'
    assert len(session.calls) == 2
    assert restarted.diagnostics(NOW)['budget_used'] == 52
    assert restarted.diagnostics(NOW)['recovery_month_used'] == 1


def test_monthly_budget_persists_and_resets_by_calendar_month(tmp_path):
    session = Session(INFO, quote(), INFO)
    provider = cache(tmp_path, session, monthly_limit=3, reserved=1)
    provider.metadata(NOW)
    provider.quote(NOW)
    restarted = cache(tmp_path, session, monthly_limit=3, reserved=1)
    assert restarted.history(NOW, SESSIONS)['status'] == 'budget_exhausted'
    assert len(session.calls) == 2
    october = NOW.replace(month=10, day=1)
    assert restarted.metadata(october)['status'] == 'current'
    assert restarted.diagnostics(october)['budget_used'] == 2


def test_rolling_minute_limit_is_shared_between_process_instances(tmp_path):
    provider = cache(tmp_path, Session())
    for n in range(5):
        assert provider._reserve('history', str(n), NOW)[0] is not None
    other = cache(tmp_path, Session())
    assert other._reserve('metadata', '6', NOW)[1] == 'rate_limited'
    assert other._reserve('metadata', '6', NOW + timedelta(seconds=60))[0] is not None


def test_concurrent_collectors_reserve_only_one_request(tmp_path):
    session = Session(series(), delay=.05)
    providers = [cache(tmp_path, session) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda provider: provider.history(NOW, SESSIONS), providers))
    assert len(session.calls) == 1
    assert sum(result['status'] == 'current' for result in results) == 1
    assert cache(tmp_path, session).diagnostics(NOW)['requests_recorded'] == 1


def test_metadata_reuses_verified_identity_for_24_hours(tmp_path):
    session = Session(INFO, INFO)
    provider = cache(tmp_path, session)
    first = provider.metadata(NOW)
    later = provider.metadata(NOW + timedelta(hours=23))
    assert first['received_at'] == later['received_at']
    assert len(session.calls) == 1
    provider.metadata(NOW + timedelta(hours=24))
    assert len(session.calls) == 2


def test_failed_metadata_has_one_backoff_recovery_then_next_hour(tmp_path):
    session = Session(requests.Timeout('private'), requests.Timeout('private'), INFO)
    provider = cache(tmp_path, session)
    assert provider.metadata(NOW)['budget_used'] == 51
    restarted = cache(tmp_path, session)
    assert restarted.metadata(NOW + timedelta(seconds=29))['status'] == 'retry_wait'
    assert restarted.metadata(NOW + timedelta(seconds=30))['status'] == 'unavailable'
    assert restarted.metadata(NOW + timedelta(minutes=30))['status'] == 'unavailable'
    assert len(session.calls) == 2
    recovered = restarted.metadata(NOW + timedelta(hours=1))
    assert recovered['status'] == 'current'
    assert recovered['payload'] == INFO and recovered['budget_used'] == 53


@pytest.mark.parametrize('info', [dict(INFO, code='CBOE:VX1!'), dict(INFO, delay_seconds=900),
                               dict(INFO, delay_seconds=False), dict(INFO, type='CFD')])
def test_wrong_or_delayed_identity_is_not_cached(tmp_path, info):
    provider = cache(tmp_path, Session(info))
    result = provider.metadata(NOW)
    assert result['status'] == 'unavailable'
    assert result['payload'] is None


def test_quote_has_only_one_budgeted_refresh_per_candle(tmp_path):
    later = NOW + timedelta(seconds=100)
    session = Session(quote(), quote(later), quote(NOW + timedelta(minutes=15)))
    provider = cache(tmp_path, session)
    first = provider.quote(NOW)
    assert provider.quote(NOW + timedelta(seconds=40))['payload'] == first['payload']
    refreshed = provider.quote(later)
    assert refreshed['status'] == 'current'
    expired = provider.quote(NOW + timedelta(seconds=200))
    assert expired['status'] == 'stale'
    assert expired['source_updated_at'] == refreshed['source_updated_at']
    assert len(session.calls) == 2
    assert provider.quote(NOW + timedelta(minutes=15))['status'] == 'current'
    assert len(session.calls) == 3


def test_stale_quote_is_not_saved_and_only_has_one_recovery(tmp_path):
    session = Session(quote(NOW - timedelta(minutes=10)), quote(NOW - timedelta(minutes=10)))
    provider = cache(tmp_path, session)
    assert provider.quote(NOW)['payload'] is None
    assert provider.quote(NOW + timedelta(seconds=29))['status'] == 'retry_wait'
    assert provider.quote(NOW + timedelta(seconds=30))['payload'] is None
    assert provider.quote(NOW + timedelta(minutes=5))['status'] == 'unavailable'
    assert len(session.calls) == 2


def test_invalid_series_preserves_previous_data_and_timestamps(tmp_path):
    bad = series(NOW + timedelta(minutes=15))
    bad['series'][-1]['high'] = 1
    session = Session(series(), bad)
    provider = cache(tmp_path, session)
    first = provider.history(NOW, SESSIONS)
    second = provider.history(NOW + timedelta(minutes=15), SESSIONS)
    assert second['status'] == 'unavailable'
    assert first['payload'] == second['payload']
    assert first['source_updated_at'] == second['source_updated_at']


def test_missing_latest_completed_bar_is_rejected(tmp_path):
    result = cache(tmp_path, Session(series(NOW - timedelta(minutes=15)))).history(NOW, SESSIONS)
    assert result['status'] == 'unavailable'
    assert result['payload'] is None


def test_after_close_catch_up_happens_once_then_no_weekend_polling(tmp_path):
    closing = SESSIONS['2026-09-16']['close']
    payload = series(closing - timedelta(seconds=1))
    session = Session(payload)
    provider = cache(tmp_path, session)
    after_close = closing + timedelta(hours=1)
    # The final bar is only complete when the provider watermark reaches close.
    payload['last_update'] = closing.timestamp() * 1000
    result = provider.history(after_close, SESSIONS)
    assert result['status'] == 'current'
    assert result['source_updated_at'] == closing.isoformat()
    provider.history(after_close + timedelta(days=3), SESSIONS)
    assert len(session.calls) == 1


def test_failed_after_close_catch_up_not_retried_repeatedly(tmp_path):
    session = Session(requests.Timeout('private'), requests.Timeout('private'))
    provider = cache(tmp_path, session)
    provider.history(NOW.replace(hour=21), SESSIONS)
    provider.history(NOW.replace(hour=22), SESSIONS)
    provider.history(NOW.replace(hour=23), SESSIONS)
    assert len(session.calls) == 2


@pytest.mark.parametrize('sessions', [None, {}, {'bad': {'open': '2026-09-16T09:30:00', 'close': 'invalid'}}])
def test_calendar_required_before_any_history_request(tmp_path, sessions):
    session = Session()
    assert cache(tmp_path, session).history(NOW, sessions)['status'] == 'calendar_unavailable'
    assert session.calls == []


def test_credentials_never_enter_database_even_if_reflected(tmp_path):
    provider = cache(tmp_path, Session(dict(INFO, reflected='test-key')))
    assert provider.metadata(NOW)['status'] == 'unavailable'
    with sqlite3.connect(tmp_path / 'vix.sqlite3') as db:
        dump = '\n'.join(db.iterdump())
    assert 'test-key' not in dump


def test_freshness_uses_response_receipt_time_not_request_start(tmp_path):
    received = NOW + timedelta(seconds=10)
    session = Session(quote(NOW + timedelta(seconds=5)))
    provider = InsightCache(tmp_path / 'receipt.sqlite3', 'test-key', session, clock=lambda: received)
    result = provider.quote(NOW)
    assert result['status'] == 'current'
    assert result['received_at'] == received.isoformat()
    assert result['source_updated_at'] == NOW.isoformat()


def test_quote_that_becomes_stale_during_response_is_rejected(tmp_path):
    received = NOW + timedelta(seconds=100)
    provider = InsightCache(tmp_path / 'receipt.sqlite3', 'test-key', Session(quote()), clock=lambda: received)
    assert provider.quote(NOW)['payload'] is None


def test_quote_failure_does_not_taint_valid_metadata_or_history(tmp_path):
    session = Session(INFO, series(), requests.Timeout('private'))
    provider = cache(tmp_path, session)
    provider.metadata(NOW)
    provider.history(NOW, SESSIONS)
    failed = provider.quote(NOW)
    assert failed['status'] == 'unavailable'
    assert failed['error']
    assert provider.diagnostics(NOW)['error'] == failed['error']
    for result in (provider.metadata(NOW), provider.history(NOW, SESSIONS)):
        assert result['status'] == 'cached'
        assert result['error'] is None
    # Re-reading the failed quote retains its own failure and makes no retry.
    assert provider.quote(NOW)['error'] == failed['error']
    assert len(session.calls) == 3


def test_missing_quote_peek_neither_inherits_other_errors_nor_requests_data(tmp_path):
    session = Session(requests.Timeout('private'))
    provider = cache(tmp_path, session)
    provider.metadata(NOW)
    before = provider.diagnostics(NOW)
    result = provider.peek_quote(NOW)
    assert result['payload'] is None
    assert result['status'] == 'unavailable'
    assert result['error'] is None
    assert provider.diagnostics(NOW) == before
    assert len(session.calls) == 1


def test_quote_peek_exposes_fresh_and_stale_copy_without_requests_or_timestamp_changes(tmp_path):
    session = Session(quote())
    provider = cache(tmp_path, session)
    original = provider.quote(NOW)
    before = provider.diagnostics(NOW)
    fresh = provider.peek_quote(NOW + timedelta(seconds=40))
    assert fresh['status'] == 'cached'
    assert fresh['error'] is None
    fresh['payload']['data'][0]['last_price'] = 999
    stale = provider.peek_quote(NOW + timedelta(seconds=100))
    assert stale['status'] == 'stale'
    assert stale['payload'] == original['payload']
    assert stale['received_at'] == original['received_at']
    assert stale['source_updated_at'] == original['source_updated_at']
    assert provider.peek_quote(NOW - timedelta(seconds=10))['status'] == 'stale'
    assert provider.diagnostics(NOW) == before
    assert len(session.calls) == 1


@pytest.mark.parametrize('limit', [901, 1000, 0, True, 1.5])
def test_configuration_cannot_raise_free_plan_ceiling(tmp_path, limit):
    with pytest.raises(ValueError):
        cache(tmp_path, Session(), monthly_limit=limit, reserved=0)


def test_late_history_publication_recovers_without_accepting_missing_candle(tmp_path):
    at = NOW + timedelta(seconds=30)
    session = Session(series(NOW-timedelta(minutes=15)), series(at))
    provider = cache(tmp_path, session)
    first = provider.history(NOW, SESSIONS)
    assert first['payload'] is None and first['retry_at'] == at.isoformat()
    assert provider.history(at, SESSIONS)['status'] == 'current'
    assert len(session.calls) == 2


@pytest.mark.parametrize('kind,payload', [
    ('metadata', dict(INFO, code='CBOE:VX1!')),
    ('metadata', dict(INFO, delay_seconds=900)),
    ('metadata', dict(INFO, reflected='test-key')),
    ('history', dict(series(), code='VIXY')),
    ('history', dict(series(), last_update=(NOW+timedelta(seconds=1)).timestamp()*1000)),
    ('quote', dict(quote(), last_update=(NOW+timedelta(seconds=1)).timestamp()*1000)),
])
def test_invalid_responses_do_not_earn_in_slot_recovery(tmp_path, kind, payload):
    session = Session(payload)
    provider = cache(tmp_path, session)
    operation = getattr(provider, kind)
    args = (SESSIONS,) if kind == 'history' else ()
    first = operation(NOW, *args)
    assert first['status'] == 'unavailable' and first['retry_at'] is None
    assert operation(NOW+timedelta(minutes=2), *args)['status'] == 'unavailable'
    assert len(session.calls) == 1


def test_only_one_worker_can_claim_recovery(tmp_path):
    at = NOW+timedelta(seconds=30)
    session = Session(requests.Timeout(), series(at), delay=.02)
    cache(tmp_path, session).history(NOW, SESSIONS)
    providers = [cache(tmp_path, session) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda p:p.history(at, SESSIONS), providers))
    assert len(session.calls) == 2
    assert sum(r['status']=='current' for r in results) == 1
    assert providers[0].diagnostics(at)['recovery_day_used'] == 1


def test_unknown_request_claim_is_never_retried_after_restart(tmp_path):
    provider = cache(tmp_path, Session())
    slot = NOW.replace(second=0).isoformat()
    assert provider._reserve('history', slot, NOW)[0]
    restarted = cache(tmp_path, Session())
    result = restarted.history(NOW+timedelta(minutes=5), SESSIONS)
    assert result['status'] == 'in_flight'
    assert result['retry_at'] is None
    assert restarted.session.calls == []


def _consume_recovery(provider, at, slot):
    request, _ = provider._reserve('history', slot, at)
    with provider._db() as db:
        db.execute('UPDATE insight_requests SET finished=1,error=?,retryable=1,retry_at=? WHERE id=?',
                   ('temporary', at.timestamp(), request))
    recovery, status = provider._reserve('history', slot, at+timedelta(seconds=30))
    if recovery:
        with provider._db() as db:
            db.execute('UPDATE insight_requests SET finished=1,error=? WHERE id=?', ('failed recovery', recovery))
    return recovery, status


def test_daily_recovery_budget_is_shared_across_endpoints_and_restarts(tmp_path):
    provider = cache(tmp_path, Session())
    for i in range(4):
        assert _consume_recovery(provider, NOW+timedelta(minutes=2*i), str(i))[0]
    restarted = cache(tmp_path, Session())
    assert _consume_recovery(restarted, NOW+timedelta(minutes=10), 'last') == (None, 'recovery_budget_exhausted')
    assert restarted.diagnostics(NOW)['requests_recorded'] == 9
    assert restarted.diagnostics(NOW)['recovery_day_used'] == 4
    assert _consume_recovery(restarted, NOW+timedelta(days=1), 'tomorrow')[0]


def test_monthly_recovery_cap_does_not_add_to_total_allowance(tmp_path):
    provider = cache(tmp_path, Session())
    start = NOW.replace(day=1)
    for i in range(50):
        at = start+timedelta(days=i//4, minutes=(i%4)*2)
        assert _consume_recovery(provider, at, str(i))[0]
    at = start+timedelta(days=13)
    assert _consume_recovery(provider, at, 'last') == (None, 'recovery_budget_exhausted')
    assert provider.diagnostics(at)['recovery_month_used'] == 50
    assert provider.diagnostics(at)['budget_used'] == 151


def test_total_allowance_blocks_recovery_before_its_smaller_cap(tmp_path):
    provider = cache(tmp_path, Session(requests.Timeout(), quote()), monthly_limit=3, reserved=1)
    provider.metadata(NOW)
    provider.quote(NOW)
    result = provider.metadata(NOW+timedelta(seconds=30))
    assert result['status'] == 'budget_exhausted'
    assert len(provider.session.calls) == 2
    assert provider.diagnostics(NOW)['recovery_month_used'] == 0


def test_unknown_recovery_claim_is_consumed_without_third_attempt(tmp_path):
    provider = cache(tmp_path, Session(requests.Timeout()))
    provider.history(NOW, SESSIONS)
    slot = NOW.replace(second=0).isoformat()
    assert provider._reserve('history', slot, NOW+timedelta(seconds=30))[0]
    restarted = cache(tmp_path, Session())
    assert restarted.history(NOW+timedelta(minutes=5), SESSIONS)['status'] == 'in_flight'
    assert restarted.session.calls == []


def test_old_ledger_migration_preserves_unknown_claim_and_quota(tmp_path):
    path = tmp_path/'vix.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE insight_requests(id INTEGER PRIMARY KEY,at REAL NOT NULL,month TEXT NOT NULL,kind TEXT NOT NULL,slot TEXT NOT NULL,error TEXT,finished INTEGER NOT NULL DEFAULT 0,UNIQUE(kind,slot))')
        db.execute('INSERT INTO insight_requests(at,month,kind,slot) VALUES(?,?,?,?)',
                   (NOW.timestamp(), '2026-09', 'history', NOW.replace(second=0).isoformat()))
    provider = cache(tmp_path, Session())
    assert provider.history(NOW+timedelta(minutes=5), SESSIONS)['status'] == 'in_flight'
    assert provider.diagnostics(NOW)['budget_used'] == 51
    assert provider.session.calls == []


@pytest.mark.parametrize('status,retryable', [(401,False),(403,False),(429,True),(500,True),(503,True)])
def test_http_failures_have_typed_bounded_recovery(tmp_path, status, retryable):
    class HttpSession:
        def __init__(self): self.calls=0
        def get(self,*args,**kwargs):
            self.calls += 1
            response=Response({})
            response.status_code=status
            return response
    session=HttpSession()
    provider=cache(tmp_path,session)
    first=provider.metadata(NOW)
    assert bool(first['retry_at']) == retryable
    provider.metadata(NOW+timedelta(seconds=30))
    provider.metadata(NOW+timedelta(minutes=5))
    assert session.calls == (2 if retryable else 1)


def test_future_quote_with_stale_outer_timestamp_never_retries(tmp_path):
    payload=quote(NOW-timedelta(minutes=10))
    payload['data'][0]['lp_time']=(NOW+timedelta(seconds=1)).timestamp()
    provider=cache(tmp_path,Session(payload))
    assert provider.quote(NOW)['retry_at'] is None
    assert provider.quote(NOW+timedelta(minutes=2))['status']=='unavailable'
    assert len(provider.session.calls)==1


def test_recovery_is_subject_to_shared_rolling_rate_limit(tmp_path):
    provider=cache(tmp_path,Session(requests.Timeout(), INFO))
    provider.metadata(NOW)
    for i in range(4):
        assert provider._reserve('history',str(i),NOW)[0]
    result=provider.metadata(NOW+timedelta(seconds=30))
    assert result['status']=='rate_limited'
    assert result['retry_at']==(NOW+timedelta(seconds=60)).isoformat()
    assert len(provider.session.calls)==1
    assert provider.metadata(NOW+timedelta(seconds=60))['status']=='current'
