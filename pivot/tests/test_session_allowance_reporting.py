from fastapi.testclient import TestClient
from pivot.api import create_app
from pivot.service import Service
from pivot.store import Store


def test_snapshot_allowance_read_does_not_change_saved_permission(tmp_path):
    store = Store(tmp_path / 'audit.db')
    service = Service(None, store)
    permission = store.deployment_permission()
    with TestClient(create_app(service, background=False)) as client:
        result = client.get('/api/snapshot').json()
    assert result['entry_allowance']['limit'] == 2
    assert result['entry_allowance']['used'] == 0
    assert result['entry_allowance']['remaining'] == 2
    assert store.deployment_permission() == permission


def test_allowance_reporting_failure_does_not_hide_broker_or_management(tmp_path, monkeypatch):
    store = Store(tmp_path / 'audit.db')
    service = Service(None, store)
    service.state['positions'] = [{'symbol': 'QQQ', 'qty': '0.01'}]
    def unavailable():
        raise OSError('simulated inaccessible allowance table')
    monkeypatch.setattr(store, 'session_entry_allowance', unavailable)
    result = service.snapshot()
    assert result['entry_allowance']['status'] == 'blocked'
    assert result['entry_allowance']['used'] is None
    assert result['positions'] == [{'symbol': 'QQQ', 'qty': '0.01'}]
    assert 'worker_health' in result
