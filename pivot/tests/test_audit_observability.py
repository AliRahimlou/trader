from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi.testclient import TestClient

from pivot.api import create_app
from pivot.diagnostics import build_decision_trace
from pivot.models import Bar
from pivot.observations import ObservationArchive
from pivot.service import Service
from pivot.store import Store
from pivot.strategy import analyze
from pivot.worker_health import WorkerHealth, next_tick
from pivot.tests.test_decision_diagnostics import scenario
from pivot.tests.test_strategy_v2 import NOW


def test_input_archive_replays_original_receipts_and_keeps_corrections(tmp_path):
    markets, vix = scenario()
    archive = ObservationArchive(tmp_path / 'inputs.db')
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    trace = build_decision_trace(setup, markets, vix, NOW)
    first = archive.record(markets, vix, NOW, trace=trace, settings={'target_dollars':'5.00', 'api_key':'SECRET'})
    assert archive.verify_replay(first['id'])['matches']
    original = deepcopy(markets['AAPL'].bars[5][-1])
    old = markets['AAPL'].bars[5][-1]
    markets['AAPL'].bars[5][-1] = Bar(old.end, 5, old.open, old.high + .1, old.low, old.close, old.volume)
    markets['AAPL'].observed_at = NOW + timedelta(seconds=30)
    next_at = NOW + timedelta(seconds=30)
    second = archive.record(markets, vix, next_at, trace=build_decision_trace(analyze(markets['QQQ'], markets, vix, next_at), markets, vix, next_at))
    payload, recovered, _ = archive.read(first['id'])
    assert recovered['AAPL'].bars[5][-1] == original
    assert recovered['AAPL'].observed_at != markets['AAPL'].observed_at
    assert 'SECRET' not in json.dumps(payload)
    assert archive.read(second['id'])[1]['AAPL'].bars[5][-1].high == old.high + .1
    assert archive.verify_replay(first['id'])['matches']
    with archive.connect() as db:
        # Only the corrected frame is additional; all identical history is shared.
        count = db.execute('SELECT count(*) FROM frames').fetchone()[0]
    expected = set()
    for value in payload['markets'].values():
        expected.update(value['frames'].values())
    expected.update(payload['vix']['frames'].values())
    assert count == len(expected) + 1


def test_archive_storage_failure_does_not_change_signal_or_live_permission(tmp_path):
    store = Store(tmp_path / 'audit.db')
    service = Service(None, store)
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    service.state.update(setup=deepcopy(setup))
    service.archive.max_bytes = 1
    control = store.control()
    service._record_decision(markets, vix, NOW)
    assert service.state['decision_trace']['persisted']
    assert service.state['input_archive']['status'] == 'unavailable'
    assert service.state['setup'] == setup and store.control() == control
    with service.archive.connect() as db:
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == 0


def test_summary_write_failure_does_not_drop_independent_input_observation(tmp_path, monkeypatch):
    store = Store(tmp_path / 'audit.db')
    service = Service(None, store)
    markets, vix = scenario()
    service.state['setup'] = analyze(markets['QQQ'], markets, vix, NOW)
    service._record_decision(markets, vix, NOW)
    previous_trace = deepcopy(service.state['decision_trace'])
    previous_archive = deepcopy(service.state['input_archive'])
    control = store.control()
    def unavailable(trace):
        raise RuntimeError('Isolated decision ledger write failure')
    monkeypatch.setattr(store, 'record_decision', unavailable)
    later = NOW + timedelta(seconds=30)
    setup = analyze(markets['QQQ'], markets, vix, later)
    service.state.update(setup=deepcopy(setup), analysis_at=later.isoformat())
    service._record_decision(markets, vix, later)
    assert service.state['diagnostic_error']
    assert service.state['decision_trace'] == previous_trace
    archived = service.state['input_archive']
    assert archived['status'] == 'recording' and archived['id'] != previous_archive['id']
    assert archived['captured_at'] == later.isoformat()
    payload, _, _ = service.archive.read(archived['id'])
    assert payload['captured_at'] == later.isoformat()
    with service.archive.connect() as db:
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == 2
    assert service.state['setup'] == setup and store.control() == control


def test_trace_construction_failure_marks_summary_and_archive_unavailable(tmp_path, monkeypatch):
    import pivot.service as service_module
    store = Store(tmp_path / 'audit.db')
    service = Service(None, store)
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    service.state['setup'] = deepcopy(setup)
    service._record_decision(markets, vix, NOW)
    previous_trace = deepcopy(service.state['decision_trace'])
    control = store.control()
    def unavailable(*args, **kwargs):
        raise RuntimeError('Isolated trace construction failure')
    monkeypatch.setattr(service_module, 'build_decision_trace', unavailable)
    service._record_decision(markets, vix, NOW + timedelta(seconds=30))
    assert service.state['diagnostic_error']
    assert service.state['decision_trace'] == previous_trace
    assert service.state['input_archive']['status'] == 'unavailable'
    with service.archive.connect() as db:
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0] == 1
    assert service.state['setup'] == setup and store.control() == control


def test_archive_detects_corruption_and_wrong_engine(tmp_path):
    markets, vix = scenario()
    archive = ObservationArchive(tmp_path / 'inputs.db')
    trace = build_decision_trace(analyze(markets['QQQ'], markets, vix, NOW), markets, vix, NOW)
    identity = archive.record(markets, vix, NOW, trace=trace)['id']
    with archive.connect() as db:
        body = json.loads(db.execute('SELECT body FROM observations WHERE id=?', (identity,)).fetchone()[0])
        body['engine_hash'] = 'wrong'
        db.execute('UPDATE observations SET body=? WHERE id=?', (json.dumps(body), identity))
    with pytest.raises(ValueError, match='engine differs'):
        archive.verify_replay(identity)


def test_replay_checks_unselected_method_evidence_and_private_permissions(tmp_path):
    markets, vix = scenario()
    archive = ObservationArchive(tmp_path/'inputs.db')
    trace = build_decision_trace(analyze(markets['QQQ'], markets, vix, NOW), markets, vix, NOW)
    trace['account'] = {'api_key':'SECRET'}
    identity = archive.record(markets, vix, NOW, trace=trace)['id']
    assert tmp_path.joinpath('inputs.db').stat().st_mode & 0o777 == 0o600
    with archive.connect() as db:
        body = json.loads(db.execute('SELECT body FROM observations WHERE id=?', (identity,)).fetchone()[0])
        assert 'SECRET' not in json.dumps(body)
        body['decision']['strategies'][1]['checks'][0]['passed'] = not body['decision']['strategies'][1]['checks'][0]['passed']
        db.execute('UPDATE observations SET body=? WHERE id=?', (json.dumps(body), identity))
    assert archive.verify_replay(identity)['matches'] is False


def test_worker_readiness_distinguishes_stall_startup_provider_failure_and_restart():
    tick = [100.]
    health = WorkerHealth(['account', 'data', 'execution'], timer=lambda:tick[0])
    assert not health.snapshot()['ready']
    for name in ('account', 'data', 'execution'):
        health.begin(name)
    assert {row['status'] for row in health.snapshot()['workers']} == {'starting'}
    for name in ('account', 'data', 'execution'):
        health.finish(name)
    assert health.snapshot()['ready']
    tick[0] += 31
    rows = {row['name']:row for row in health.snapshot()['workers']}
    assert rows['execution']['status'] == 'stalled'
    assert rows['data']['status'] == 'running'
    health.begin('execution'); health.finish('execution', failed=True)
    assert not health.snapshot()['ready']
    health.begin('execution'); health.finish('execution')
    assert health.snapshot()['ready']
    assert not health.snapshot({'account', 'data'})['ready']


@pytest.mark.parametrize('elapsed,expected', [(0,60),(20,60),(30,60),(90,91)])
def test_scheduler_does_not_add_fetch_duration_to_interval(elapsed, expected):
    assert next_tick(0,60,elapsed) == expected


def test_http_liveness_does_not_claim_unstarted_workers_ready(tmp_path):
    service = Service(None, Store(tmp_path/'audit.db'))
    with TestClient(create_app(service, background=False)) as client:
        live = client.get('/api/health').json()
        assert live['ok'] is True and live['ready'] is False
        status = client.get('/api/worker-health')
        assert status.status_code == 503
        assert all(row['status'] == 'stopped' for row in status.json()['workers'])


def test_session_funnel_counts_distinct_events_not_polls_and_has_all_rejections(tmp_path):
    store = Store(tmp_path/'audit.db')
    markets, vix = scenario()
    trace = build_decision_trace(analyze(markets['QQQ'], markets, vix, NOW), markets, vix, NOW)
    assert len(trace['candidate_diagnostics']) > 0
    for seconds in (0,10,30):
        value = deepcopy(trace)
        value['captured_at'] = (NOW + timedelta(seconds=seconds)).isoformat()
        store.record_decision(value)
    review = store.session_review(NOW.date().isoformat())
    distinct = {row['event_id'] for row in trace['candidate_diagnostics']}
    assert review['events_seen'] == len(distinct)
    assert review['checkpoints'] == 1
    assert review['complete_candidate_coverage'] is True
    assert review['orders']['submission_attempts'] == 0
    assert review['historical_only'] is True
    assert len(review['latest_signal_blockers']) >= 1
    before = store.control()
    with TestClient(create_app(Service(None, store), background=False)) as client:
        assert client.get('/api/session-review?day=2026-99-99').status_code == 422
        assert client.get('/api/session-review?day='+NOW.date().isoformat()).json()['events_seen'] == len(distinct)
    assert store.control() == before


def test_session_funnel_marks_old_summary_coverage_as_partial(tmp_path):
    store = Store(tmp_path/'audit.db')
    trace = build_decision_trace(None, {}, None, NOW)
    trace.pop('candidate_diagnostics')
    store.record_decision(trace)
    assert store.session_review(NOW.date().isoformat())['complete_candidate_coverage'] is False


def test_latest_blockers_use_observation_time_after_dedup_a_b_a(tmp_path):
    store = Store(tmp_path/'audit.db')
    markets, vix = scenario()
    trace = build_decision_trace(analyze(markets['QQQ'], markets, vix, NOW), markets, vix, NOW)
    a = deepcopy(trace)
    a['candidate_diagnostics'][0]['checks'] = [{'name':'Nasdaq level event','passed':False,'detail':'A'}]
    b = deepcopy(a)
    b['candidate_diagnostics'][0]['checks'] = [
        {'name':'Nasdaq level event','passed':True,'detail':'B'},
        {'name':'Magnificent Seven at their zones','passed':False,'detail':'B'}]
    for index, value in enumerate((a,b,a)):
        value['captured_at'] = (NOW + timedelta(seconds=index*10)).isoformat()
        store.record_decision(value)
    review = store.session_review(NOW.date().isoformat())
    assert review['latest_signal_blockers'].get('Nasdaq level event') == 1
    assert review['latest_checks'][0]['at'] == a['captured_at']
