from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import time

import pytest
import requests

from pivot.insight_cache import (InsightCache, SYMBOL, CLOCK_SKEW, SLOT_ATTEMPT_LIMIT, QUOTE_REFRESH_LIMIT,
                                 RECOVERY_DAY_LIMIT, RECOVERY_MONTH_LIMIT, _projected_need)

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


def test_failed_metadata_retries_after_each_backoff_within_its_hour(tmp_path):
    # Updated from the old one-recovery pin: genuine errors now retry every
    # RETRY_BACKOFF inside the hourly slot, each counted as a recovery.
    session = Session(requests.Timeout('private'), requests.Timeout('private'), INFO)
    provider = cache(tmp_path, session)
    assert provider.metadata(NOW)['budget_used'] == 51
    restarted = cache(tmp_path, session)
    assert restarted.metadata(NOW + timedelta(seconds=29))['status'] == 'retry_wait'
    assert restarted.metadata(NOW + timedelta(seconds=30))['status'] == 'unavailable'
    assert restarted.metadata(NOW + timedelta(seconds=59))['status'] == 'retry_wait'
    assert len(session.calls) == 2
    recovered = restarted.metadata(NOW + timedelta(minutes=30))
    assert recovered['status'] == 'current'
    assert recovered['payload'] == INFO and recovered['budget_used'] == 53
    assert restarted.diagnostics(NOW)['recovery_day_used'] == 2
    assert restarted.metadata(NOW + timedelta(hours=1))['status'] == 'cached'
    assert len(session.calls) == 3


@pytest.mark.parametrize('info', [dict(INFO, code='CBOE:VX1!'), dict(INFO, delay_seconds=900),
                               dict(INFO, delay_seconds=False), dict(INFO, type='CFD')])
def test_wrong_or_delayed_identity_is_not_cached(tmp_path, info):
    provider = cache(tmp_path, Session(info))
    result = provider.metadata(NOW)
    assert result['status'] == 'unavailable'
    assert result['payload'] is None


def test_quote_refreshes_are_capped_per_candle_slot(tmp_path):
    # Updated from the old single-refresh pin: QUOTE_REFRESH_LIMIT successful
    # quotes per 900-second slot, the first included; a fresh quote is reused.
    stamps = [NOW + timedelta(seconds=100 * n) for n in range(QUOTE_REFRESH_LIMIT)]
    session = Session(*[quote(at) for at in stamps], quote(NOW + timedelta(minutes=15)))
    provider = cache(tmp_path, session)
    first = provider.quote(NOW)
    assert provider.quote(NOW + timedelta(seconds=40))['payload'] == first['payload']
    for at in stamps[1:]:
        refreshed = provider.quote(at)
        assert refreshed['status'] == 'current' and refreshed['source_updated_at'] == (at - timedelta(seconds=5)).isoformat()
    assert len(session.calls) == QUOTE_REFRESH_LIMIT
    expired = provider.quote(stamps[-1] + timedelta(seconds=100))
    assert expired['status'] == 'stale'
    assert expired['source_updated_at'] == refreshed['source_updated_at']
    assert 'refresh limit' in expired['retry_reason']
    assert len(session.calls) == QUOTE_REFRESH_LIMIT
    assert provider.diagnostics(NOW)['recovery_day_used'] == 0
    assert provider.quote(NOW + timedelta(minutes=15))['status'] == 'current'
    assert len(session.calls) == QUOTE_REFRESH_LIMIT + 1


def test_stale_quote_is_not_saved_and_slot_attempts_are_capped(tmp_path):
    # Updated from the old one-recovery pin: a stale (late) quote may be retried
    # after each backoff until SLOT_ATTEMPT_LIMIT claims exist in the slot.
    stale = quote(NOW - timedelta(minutes=10))
    session = Session(*[stale] * (SLOT_ATTEMPT_LIMIT + 1))
    provider = cache(tmp_path, session)
    assert provider.quote(NOW)['payload'] is None
    assert provider.quote(NOW + timedelta(seconds=29))['status'] == 'retry_wait'
    for n in range(1, SLOT_ATTEMPT_LIMIT):
        result = provider.quote(NOW + timedelta(seconds=30 * n))
        assert result['status'] == 'unavailable' and result['payload'] is None
    assert len(session.calls) == SLOT_ATTEMPT_LIMIT
    capped = provider.quote(NOW + timedelta(minutes=5))
    assert capped['status'] == 'unavailable' and capped['retry_at'] is None
    assert 'attempt limit' in capped['retry_reason']
    assert len(session.calls) == SLOT_ATTEMPT_LIMIT
    # Late publications are the provider's normal behaviour, not recoveries.
    assert provider.diagnostics(NOW)['recovery_day_used'] == 0
    assert provider.quote(NOW + timedelta(minutes=15))['status'] == 'unavailable'
    assert len(session.calls) == SLOT_ATTEMPT_LIMIT + 1


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


def test_failed_after_close_catch_up_is_bounded_by_the_slot_attempt_cap(tmp_path):
    # Updated from the old two-attempt pin: the final session slot lasts until
    # the next open, so overnight polling can spend at most SLOT_ATTEMPT_LIMIT.
    session = Session(*[requests.Timeout('private')] * SLOT_ATTEMPT_LIMIT)
    provider = cache(tmp_path, session)
    for minute in range(0, 10 * (SLOT_ATTEMPT_LIMIT + 2), 10):
        provider.history(NOW.replace(hour=21) + timedelta(minutes=minute), SESSIONS)
    provider.history(NOW.replace(hour=23), SESSIONS)
    assert len(session.calls) == SLOT_ATTEMPT_LIMIT
    assert provider.diagnostics(NOW)['recovery_day_used'] == SLOT_ATTEMPT_LIMIT - 1


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
    assert provider.peek_quote(NOW - timedelta(seconds=10))['status'] == 'cached'  # Within clock skew.
    assert provider.peek_quote(NOW - timedelta(seconds=11))['status'] == 'stale'
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
    ('history', dict(series(), last_update=(NOW+CLOCK_SKEW+timedelta(seconds=1)).timestamp()*1000)),
    ('quote', dict(quote(), last_update=(NOW+CLOCK_SKEW+timedelta(seconds=1)).timestamp()*1000)),
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
    for i in range(RECOVERY_DAY_LIMIT):
        assert _consume_recovery(provider, NOW+timedelta(minutes=2*i), str(i))[0]
    restarted = cache(tmp_path, Session())
    assert _consume_recovery(restarted, NOW+timedelta(minutes=30), 'last') == (None, 'recovery_budget_exhausted')
    assert restarted.diagnostics(NOW)['requests_recorded'] == 2 * RECOVERY_DAY_LIMIT + 1
    assert restarted.diagnostics(NOW)['recovery_day_used'] == RECOVERY_DAY_LIMIT == 12
    assert _consume_recovery(restarted, NOW+timedelta(days=1), 'tomorrow')[0]


def test_monthly_recovery_cap_does_not_add_to_total_allowance(tmp_path):
    provider = cache(tmp_path, Session())
    start = NOW.replace(day=1)
    for i in range(RECOVERY_MONTH_LIMIT):
        at = start+timedelta(days=i//RECOVERY_DAY_LIMIT, minutes=(i%RECOVERY_DAY_LIMIT)*2)
        assert _consume_recovery(provider, at, str(i))[0]
    at = start+timedelta(days=13)
    assert _consume_recovery(provider, at, 'last') == (None, 'recovery_budget_exhausted')
    assert provider.diagnostics(at)['recovery_month_used'] == RECOVERY_MONTH_LIMIT == 120
    assert provider.diagnostics(at)['budget_used'] == 2 * RECOVERY_MONTH_LIMIT + 51


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
    assert session.calls == (3 if retryable else 1)


def test_future_quote_with_stale_outer_timestamp_never_retries(tmp_path):
    payload=quote(NOW-timedelta(minutes=10))
    payload['data'][0]['lp_time']=(NOW+CLOCK_SKEW+timedelta(seconds=1)).timestamp()
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


def test_third_attempt_after_two_late_publications_succeeds_without_recovery_use(tmp_path):
    late = series(NOW - timedelta(minutes=15))
    published = NOW + timedelta(seconds=60)
    session = Session(late, late, series(published))
    provider = cache(tmp_path, session)
    first = provider.history(NOW, SESSIONS)
    assert first['status'] == 'unavailable' and first['retry_at'] == (NOW + timedelta(seconds=30)).isoformat()
    assert 'Publication pending' in first['retry_reason']
    assert provider.history(NOW + timedelta(seconds=15), SESSIONS)['status'] == 'retry_wait'
    second = provider.history(NOW + timedelta(seconds=30), SESSIONS)
    assert second['status'] == 'unavailable' and second['retry_at'] == (NOW + timedelta(seconds=60)).isoformat()
    third = cache(tmp_path, session).history(published, SESSIONS)
    assert third['status'] == 'current' and third['source_updated_at'] == published.isoformat()
    assert len(session.calls) == 3
    state = provider.diagnostics(published)
    assert state['requests_recorded'] == 3
    assert state['recovery_day_used'] == 0 and state['recovery_month_used'] == 0
    assert provider.history(published + timedelta(seconds=30), SESSIONS)['status'] == 'cached'
    assert len(session.calls) == 3


def test_history_slot_attempt_cap_holds_across_restarts(tmp_path):
    late = series(NOW - timedelta(minutes=15))
    session = Session(*[late] * (SLOT_ATTEMPT_LIMIT + 1))
    provider = cache(tmp_path, session)
    for n in range(SLOT_ATTEMPT_LIMIT):
        assert provider.history(NOW + timedelta(seconds=30 * n), SESSIONS)['status'] == 'unavailable'
    restarted = cache(tmp_path, session)
    capped = restarted.history(NOW + timedelta(seconds=30 * SLOT_ATTEMPT_LIMIT), SESSIONS)
    assert capped['status'] == 'unavailable' and capped['retry_at'] is None
    assert 'attempt limit' in capped['retry_reason']
    assert len(session.calls) == SLOT_ATTEMPT_LIMIT == 6
    # The next candle slot starts fresh.
    assert restarted.history(NOW + timedelta(minutes=15), SESSIONS)['status'] == 'unavailable'
    assert len(session.calls) == SLOT_ATTEMPT_LIMIT + 1


def test_late_retries_share_the_rolling_minute_limit_with_other_claims(tmp_path):
    late = series(NOW - timedelta(minutes=15))
    provider = cache(tmp_path, Session(late, late, series(NOW + timedelta(seconds=90))))
    provider.history(NOW, SESSIONS)
    for n in range(4):
        assert provider._reserve('research', str(n), NOW + timedelta(seconds=1))[0]
    limited = provider.history(NOW + timedelta(seconds=30), SESSIONS)
    assert limited['status'] == 'rate_limited'
    assert limited['retry_at'] == (NOW + timedelta(seconds=60)).isoformat()
    assert len(provider.session.calls) == 1
    assert provider.history(NOW + timedelta(seconds=61), SESSIONS)['status'] == 'unavailable'
    assert len(provider.session.calls) == 2


def test_projected_need_counts_remaining_weekdays_including_today():
    assert _projected_need(NOW) == 11 * 30  # Wed Sep 16 through Wed Sep 30, 2026.
    assert _projected_need(NOW.replace(day=1)) == 22 * 30
    assert _projected_need(NOW.replace(day=30)) == 30
    assert _projected_need(datetime(2026, 2, 28, tzinfo=timezone.utc)) == 0  # Saturday.


def test_monthly_headroom_projection_blocks_retries_but_not_first_attempts(tmp_path):
    late = series(NOW - timedelta(minutes=15))
    # 1 used + 50 reserved + 330 projected == 381: no retry headroom remains.
    provider = cache(tmp_path, Session(late, INFO, late), monthly_limit=381, reserved=50)
    first = provider.history(NOW, SESSIONS)
    assert first['status'] == 'unavailable' and first['retry_at'] is None
    assert 'reserved for scheduled collection' in first['retry_reason']
    blocked = provider.history(NOW + timedelta(seconds=30), SESSIONS)
    assert blocked['status'] == 'headroom_reserved' and blocked['retry_at'] is None
    assert 'reserved for scheduled collection' in blocked['retry_reason']
    assert blocked['error'] == 'The remaining monthly allowance is reserved for scheduled collection.'
    assert provider.diagnostics(NOW)['retry_headroom'] == 0
    assert provider.diagnostics(NOW)['projected_month_need'] == 330
    # Scheduled first attempts still proceed to the hard ceiling.
    assert provider.metadata(NOW + timedelta(seconds=31))['status'] == 'current'
    assert len(provider.session.calls) == 2
    roomier = cache(tmp_path, provider.session, monthly_limit=383, reserved=50)
    assert roomier.history(NOW + timedelta(seconds=32), SESSIONS)['status'] == 'unavailable'
    assert len(provider.session.calls) == 3


def test_quote_transient_failure_is_retryable_after_backoff_within_slot(tmp_path):
    at = NOW + timedelta(seconds=30)
    provider = cache(tmp_path, Session(requests.Timeout('private'), quote(at)))
    failed = provider.quote(NOW)
    assert failed['status'] == 'unavailable' and failed['retry_at'] == at.isoformat()
    assert 'bounded recovery' in failed['retry_reason']
    assert provider.quote(NOW + timedelta(seconds=29))['status'] == 'retry_wait'
    assert provider.quote(at)['status'] == 'current'
    assert provider.diagnostics(at)['recovery_day_used'] == 1


def test_source_timestamps_within_clock_skew_are_accepted_and_fresh(tmp_path):
    ahead = NOW + CLOCK_SKEW
    point = quote(ahead + timedelta(seconds=5))
    point['last_update'] = ahead.timestamp() * 1000
    session = Session(dict(series(), last_update=ahead.timestamp() * 1000), point)
    provider = cache(tmp_path, session)
    history = provider.history(NOW, SESSIONS)
    assert history['status'] == 'current' and history['source_updated_at'] == ahead.isoformat()
    point = provider.quote(NOW)
    assert point['status'] == 'current' and point['source_updated_at'] == ahead.isoformat()
    assert provider.quote(NOW + timedelta(seconds=1))['status'] == 'cached'
    assert provider.peek_quote(NOW)['status'] == 'cached'
    assert len(session.calls) == 2


def test_legacy_recovery_claims_migrate_as_counted_recoveries(tmp_path):
    path = tmp_path / 'vix.sqlite3'
    slot = NOW.replace(second=0).isoformat()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE insight_requests(id INTEGER PRIMARY KEY,at REAL NOT NULL,month TEXT NOT NULL,'
                   'kind TEXT NOT NULL,slot TEXT NOT NULL,error TEXT,finished INTEGER NOT NULL DEFAULT 0,'
                   'base_slot TEXT,attempt INTEGER NOT NULL DEFAULT 0,retryable INTEGER NOT NULL DEFAULT 0,'
                   'retry_at REAL,UNIQUE(kind,slot))')
        db.execute('INSERT INTO insight_requests(at,month,kind,slot,error,finished,base_slot,attempt,retryable,retry_at) '
                   'VALUES(?,?,?,?,?,1,?,0,1,?)', (NOW.timestamp(), '2026-09', 'history', slot, 'temporary', slot, NOW.timestamp()))
        db.execute('INSERT INTO insight_requests(at,month,kind,slot,error,finished,base_slot,attempt) '
                   'VALUES(?,?,?,?,?,1,?,1)', (NOW.timestamp() + 30, '2026-09', 'history', slot + ':recovery', 'failed', slot))
    provider = cache(tmp_path, Session())
    assert provider.diagnostics(NOW)['recovery_day_used'] == 1
    assert provider.diagnostics(NOW)['requests_recorded'] == 2
    again = cache(tmp_path, Session())
    assert again.diagnostics(NOW)['recovery_day_used'] == 1
