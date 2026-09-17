from copy import deepcopy

from fastapi.testclient import TestClient

from pivot.api import create_app
from pivot.service import Service
from pivot.store import Store


def test_execution_history_is_read_only_paginated_and_bounded(tmp_path):
    store = Store(tmp_path / 'audit.sqlite3')
    original = {'version': 'execution-check-v1', 'captured_at': '2026-09-17T16:00:00+00:00',
                'checkpoint_at': '2026-09-17T16:00:00+00:00', 'outcome': 'waiting',
                'gate': 'signal_leaders', 'historical_only': True, 'order_authorized': False}
    identities = []
    for index in range(3):
        record = deepcopy(original)
        record['checkpoint_at'] = f'2026-09-17T1{index}:00:00+00:00'
        identities.append(store.record_execution_check(record)['id'])
    control, settings = store.control(), store.settings()
    service = Service(None, store)
    with TestClient(create_app(service, background=False)) as client:
        first = client.get('/api/execution-checks?limit=2')
        assert first.status_code == 200
        assert first.headers['cache-control'] == 'no-store'
        page = first.json()
        assert page['historical_only'] is True
        assert [row['id'] for row in page['entries']] == identities[::-1][:2]
        assert page['next_before_id'] == identities[1]
        second = client.get(f'/api/execution-checks?limit=2&before_id={page["next_before_id"]}').json()
        assert [row['id'] for row in second['entries']] == identities[:1]
        assert second['next_before_id'] is None
        for query in ('limit=0', 'limit=101', 'limit=bad', 'before_id=0', 'before_id=-1', 'before_id=9223372036854775808'):
            assert client.get('/api/execution-checks?' + query).status_code == 422
        assert client.post('/api/execution-checks', json={}).status_code == 403
    assert store.control() == control and store.settings() == settings
    assert store.active_trade() is None


def test_empty_execution_history_is_explicitly_historical(tmp_path):
    service = Service(None, Store(tmp_path / 'audit.sqlite3'))
    with TestClient(create_app(service, background=False)) as client:
        page = client.get('/api/execution-checks').json()
        assert page['entries'] == []
        assert page['next_before_id'] is None
        assert page['historical_only'] is True
