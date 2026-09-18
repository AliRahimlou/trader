"""Authentic-format fixtures and denied sockets; no provider data or live orders."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
import requests

from pivot.insight_cache import InsightCache
from pivot.native_history import (EXPERIMENT, KIND, artifact_path, capture, read_artifact,
                                  snapshot, validate_artifact)
from pivot.tests.test_insight_cache import INFO, Session

NOW = datetime(2026, 9, 18, 17, 0, 31, tzinfo=timezone.utc)


def calendar():
    rows = {}
    for offset in range(25, -1, -1):
        at = NOW - timedelta(days=offset)
        if at.weekday() < 5 and at.date().isoformat() != '2026-09-07':
            rows[at.date().isoformat()] = {'open': at.replace(hour=13, minute=30, second=0),
                                          'close': at.replace(hour=20, minute=0, second=0)}
    return rows


def series():
    rows = []
    for session in calendar().values():
        at = session['open']
        while at < session['close'] and at <= NOW:
            rows.append({'time': at.timestamp(), 'open': 17, 'high': 18, 'low': 16, 'close': 17.5})
            at += timedelta(minutes=5)
    return {'code': 'CBOE:VIX', 'bar_type': '5m', 'last_update': NOW.timestamp()*1000,
            'bar_end': rows[-1]['time']+300, 'series': rows}


def provider(tmp_path, session, *, info=None, meta_at=NOW, **kwargs):
    cache = InsightCache(tmp_path/'insight.sqlite3', 'secret-native-test', session=session, **kwargs)
    with cache._db() as db:
        db.execute('INSERT OR REPLACE INTO insight_cache VALUES (?,?,?,?)',
                   ('metadata', json.dumps(INFO if info is None else info), meta_at.isoformat(), None))
        db.execute('INSERT OR REPLACE INTO insight_cache VALUES (?,?,?,?)',
                   ('history', '{"production":"unchanged"}', meta_at.isoformat(), meta_at.isoformat()))
    return cache


def saved_rows(cache):
    with cache._db() as db:
        return db.execute('SELECT * FROM insight_cache ORDER BY kind').fetchall()


def invoke(cache, tmp_path, *, at=NOW, sessions=None, **kwargs):
    return capture(cache, calendar() if sessions is None else sessions, at,
                   tmp_path/'native', clock=lambda: at, **kwargs)


def test_one_fixed_get_preserves_production_cache_and_original_receipts(tmp_path):
    network = Session(series())
    cache = provider(tmp_path, network)
    before = saved_rows(cache)
    report = invoke(cache, tmp_path)
    assert report['status'] == 'captured' and report['request_count'] == 1
    assert report['research_only'] and report['live_entry_ready'] is False
    assert report['coverage']['missing_count'] == 0
    assert len(network.calls) == 1 and network.calls[0][0].endswith('/CBOE%3AVIX/series')
    assert network.calls[0][1]['params'] == {'bar_type':'minute', 'bar_interval':5, 'dp':3000,
                                           'extended':'false', 'abbr':'false'}
    assert network.calls[0][1]['allow_redirects'] is False
    assert saved_rows(cache) == before
    artifact = read_artifact(NOW, tmp_path/'native')
    assert artifact['bars'][-1]['end'] == NOW.replace(second=0).isoformat()
    assert artifact['raw_response']['series'][-1]['time'] == NOW.replace(second=0).timestamp()
    assert len(artifact['raw_response']['series']) == len(artifact['bars']) + 1
    assert not artifact['provenance']['point_in_time_arrival_verified']
    assert artifact_path(NOW,tmp_path/'native').stat().st_mode & 0o777 == 0o600
    restarted = InsightCache(cache.path, 'secret-native-test', session=Session())
    later = NOW + timedelta(hours=1)
    again = invoke(restarted,tmp_path,at=later)
    assert again == snapshot(restarted,later,tmp_path/'native')
    assert again['received_at'] == NOW.isoformat() and len(network.calls) == 1
    assert 'secret-native-test' not in artifact_path(NOW,tmp_path/'native').read_text()


def test_two_collectors_share_one_durable_claim(tmp_path):
    network=Session(series(),delay=.05)
    one=provider(tmp_path,network)
    two=InsightCache(one.path,'secret-native-test',session=network)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda c:invoke(c,tmp_path),(one,two)))
    assert len(network.calls)==1
    assert {r['status'] for r in results} <= {'captured','claimed'}
    assert snapshot(two,NOW,tmp_path/'native')['status']=='captured'
    assert one.diagnostics(NOW)['requests_recorded']==1


@pytest.mark.parametrize('changed', ['proxy','interval','duplicate','reverse','future','bad_price','bad_end','secret'])
def test_invalid_native_response_is_consumed_without_retry_or_artifact(tmp_path, changed):
    raw=series()
    if changed=='proxy': raw['code']='CBOE:VX1!'
    elif changed=='interval': raw['bar_type']='1m'
    elif changed=='duplicate': raw['series'].insert(1,deepcopy(raw['series'][0]))
    elif changed=='reverse': raw['series'].reverse()
    elif changed=='future': raw['last_update']=(NOW+timedelta(seconds=1)).timestamp()*1000
    elif changed=='bad_price': raw['series'][0]['high']=True
    elif changed=='bad_end': raw['bar_end']+=300
    else: raw['private']='secret-native-test'
    network=Session(raw)
    cache=provider(tmp_path,network)
    before=saved_rows(cache)
    assert invoke(cache,tmp_path)['status']=='unavailable'
    assert invoke(cache,tmp_path,at=NOW+timedelta(minutes=20))['status']=='unavailable'
    assert len(network.calls)==1 and saved_rows(cache)==before
    assert not artifact_path(NOW,tmp_path/'native').exists()
    with cache._db() as db:
        rows=db.execute('SELECT finished,retryable,retry_at,error FROM insight_requests').fetchall()
    assert len(rows)==1 and rows[0][:3]==(1,0,None)
    assert 'secret-native-test' not in str(rows)


def test_transient_failure_does_not_gain_recovery_or_another_monthly_slot(tmp_path):
    network=Session(requests.Timeout('secret-native-test'))
    cache=provider(tmp_path,network)
    assert invoke(cache,tmp_path)['status']=='unavailable'
    assert invoke(cache,tmp_path,at=NOW+timedelta(days=1))['status']=='unavailable'
    assert len(network.calls)==1 and cache.diagnostics(NOW)['recovery_month_used']==0


def test_unknown_claim_survives_restart_and_cannot_issue_request(tmp_path):
    network=Session()
    cache=provider(tmp_path,network)
    request_id,error=cache._reserve(KIND,EXPERIMENT+':2026-09',NOW)
    assert request_id and error is None
    restarted=InsightCache(cache.path,'secret-native-test',session=network)
    assert invoke(restarted,tmp_path)['status']=='claimed'
    assert not network.calls and restarted.diagnostics(NOW)['requests_recorded']==1


def test_crash_after_provider_response_cannot_repeat_claim(tmp_path,monkeypatch):
    network=Session(series())
    cache=provider(tmp_path,network)
    def crash(*args):
        raise SystemExit('simulated power loss')
    monkeypatch.setattr('pivot.native_history._immutable_write',crash)
    with pytest.raises(SystemExit): invoke(cache,tmp_path)
    restarted=InsightCache(cache.path,'secret-native-test',session=network)
    assert invoke(restarted,tmp_path)['status']=='claimed'
    assert len(network.calls)==1 and restarted.diagnostics(NOW)['requests_recorded']==1


def test_delayed_receipt_cannot_complete_a_previously_forming_bar(tmp_path):
    cache=provider(tmp_path,Session(series()))
    later=NOW+timedelta(minutes=7)
    result=capture(cache,calendar(),NOW,tmp_path/'native',clock=lambda:later)
    assert result['status']=='incomplete' and result['coverage']['missing_count']==1
    data=read_artifact(later,tmp_path/'native')
    assert data['received_at']==later.isoformat()
    assert data['source_updated_at']==NOW.isoformat()
    assert data['bars'][-1]['end']==NOW.replace(second=0).isoformat()
    assert len(data['bars'])==len(data['raw_response']['series'])-1


def test_metadata_expiring_during_download_is_rejected_without_second_call(tmp_path):
    network=Session(series())
    cache=provider(tmp_path,network,meta_at=NOW-timedelta(hours=24,seconds=-1))
    result=capture(cache,calendar(),NOW,tmp_path/'native',clock=lambda:NOW+timedelta(seconds=2))
    assert result['status']=='unavailable' and len(network.calls)==1
    assert invoke(cache,tmp_path)['status']=='unavailable'


@pytest.mark.parametrize('quota', ['month','minute'])
def test_existing_global_allowance_blocks_before_network(tmp_path,quota):
    network=Session()
    cache=provider(tmp_path,network,monthly_limit=51 if quota=='month' else 900)
    count=1 if quota=='month' else 5
    for i in range(count):
        assert cache._reserve('history',str(i),NOW)[0]
    result=invoke(cache,tmp_path)
    assert result['status']==('budget_exhausted' if quota=='month' else 'rate_limited')
    assert not network.calls and cache.diagnostics(NOW)['requests_recorded']==count


@pytest.mark.parametrize('reason',['expired','future','wrong','delayed','calendar'])
def test_metadata_or_calendar_must_validate_without_spending_another_request(tmp_path,reason):
    network=Session()
    info=deepcopy(INFO)
    at=NOW
    if reason=='expired': at-=timedelta(hours=24)
    elif reason=='future': at+=timedelta(seconds=1)
    elif reason=='wrong': info['type']='CFD'
    elif reason=='delayed': info['delay_seconds']=900
    cache=provider(tmp_path,network,info=info,meta_at=at)
    assert invoke(cache,tmp_path,sessions={} if reason=='calendar' else None)['status']=='metadata_unavailable'
    assert not network.calls and cache.diagnostics(NOW)['requests_recorded']==0


def test_valid_gap_is_saved_as_incomplete_without_filling_or_retry(tmp_path):
    raw=series()
    missing=next(row for row in raw['series'] if datetime.fromtimestamp(row['time'],timezone.utc).date()==NOW.date())
    raw['series'].remove(missing)
    cache=provider(tmp_path,Session(raw))
    report=invoke(cache,tmp_path)
    assert report['status']=='incomplete' and report['coverage']['missing_count']==1
    artifact=read_artifact(NOW,tmp_path/'native')
    assert missing not in artifact['raw_response']['series']
    assert not any(row['end']==datetime.fromtimestamp(missing['time']+300,timezone.utc).isoformat() for row in artifact['bars'])
    assert invoke(cache,tmp_path)['status']=='incomplete'


@pytest.mark.parametrize('field',['raw','normalized','metadata','coverage','provenance','receipt'])
def test_read_integrity_rejects_modified_evidence(tmp_path,field):
    cache=provider(tmp_path,Session(series()))
    invoke(cache,tmp_path)
    data=read_artifact(NOW,tmp_path/'native')
    if field=='raw': data['raw_response']['series'][0]['close']+=.1
    elif field=='normalized': data['bars'][0]['close']+=.1
    elif field=='metadata': data['metadata']['delay_seconds']=900
    elif field=='coverage': data['coverage']['missing_count']=5
    elif field=='provenance': data['provenance']['point_in_time_arrival_verified']=True
    else: data['received_at']=(NOW-timedelta(days=1)).isoformat()
    with pytest.raises(ValueError): validate_artifact(data,NOW)


def test_existing_artifact_is_never_overwritten_and_storage_error_consumes_claim(tmp_path):
    directory=tmp_path/'native'; directory.mkdir()
    path=artifact_path(NOW,directory); path.write_text('prior immutable evidence')
    cache=provider(tmp_path,Session(series()))
    assert invoke(cache,tmp_path)['status']=='unavailable'
    assert path.read_text()=='prior immutable evidence'
    assert invoke(cache,tmp_path)['status']=='unavailable'
