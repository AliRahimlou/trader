from copy import deepcopy

from fastapi.testclient import TestClient

from pivot.api import create_app
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_decision_diagnostics import trace


def test_remote_history_is_bounded_paginated_and_read_only(tmp_path):
    store = Store(tmp_path / 'audit.sqlite3')
    original = trace()
    identities = []
    for index in range(3):
        item = deepcopy(original)
        item['checkpoint_at'] = f'2026-09-17T1{index}:00:00+00:00'
        identities.append(store.record_decision(item)['id'])
    control, settings = store.control(), store.settings()
    service = Service(None, store)
    with TestClient(create_app(service, background=False)) as client:
        first = client.get('/api/decisions?limit=2').json()
        assert [e['id'] for e in first['entries']] == identities[::-1][:2]
        assert first['next_before_id'] == identities[1]
        assert first['historical_only'] is True
        second = client.get(f'/api/decisions?limit=2&before_id={first["next_before_id"]}').json()
        assert [e['id'] for e in second['entries']] == identities[:1]
        assert second['next_before_id'] is None
        for query in ('limit=0', 'limit=101', 'before_id=0', 'limit=bad', 'before_id=-1', 'before_id=9223372036854775808'):
            assert client.get('/api/decisions?' + query).status_code == 422
        assert client.post('/api/decisions', json={}).status_code == 403
    assert store.control() == control and store.settings() == settings
    assert store.active_trade() is None


def test_history_deduplication_and_empty_page(tmp_path):
    store = Store(tmp_path / 'audit.sqlite3')
    assert store.decision_history()['entries'] == []
    first = store.record_decision(trace())
    second = store.record_decision(trace())
    history = store.decision_history()
    assert first['id'] == second['id']
    assert len(history['entries']) == 1
    assert history['entries'][0]['observation_count'] == 2
