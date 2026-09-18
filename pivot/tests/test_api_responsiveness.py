import asyncio
from datetime import datetime, timezone
from threading import Event

import httpx

from pivot.api import create_app
from pivot.service import Service
from pivot.store import Store


def test_waiting_settings_save_does_not_block_health_request(tmp_path, monkeypatch):
    service = Service(None, Store(tmp_path / 'audit.db'))
    service.state.update(account={'buying_power': '100'},
                         account_at=datetime.now(timezone.utc).isoformat())
    entered, release, finished = Event(), Event(), Event()
    save_settings = service.save_settings

    def waiting_save(payload):
        entered.set()
        try:
            # Model entry-lock or SQLite contention, with a bounded fallback so
            # a regression cannot hang the test's own event loop indefinitely.
            release.wait(2)
            return save_settings(payload)
        finally:
            finished.set()

    monkeypatch.setattr(service, 'save_settings', waiting_save)

    async def exercise():
        transport = httpx.ASGITransport(app=create_app(service, background=False))
        async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
            save = asyncio.create_task(client.put('/api/settings',
                json={'sizing_mode': 'target', 'target_dollars': '5'},
                headers={'origin': 'http://127.0.0.1:5173', 'x-pivot-intent': 'settings'}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert not finished.is_set(), 'Settings save blocked the API event loop'
                health = await asyncio.wait_for(client.get('/api/health'), timeout=1)
                assert health.status_code == 200
                assert health.json()['ok'] is True
                assert not finished.is_set(), 'Health request waited for the settings save'
            finally:
                release.set()
                response = await asyncio.wait_for(save, timeout=2)
            assert response.status_code == 200
            assert response.json() == {'sizing_mode': 'target', 'target_dollars': '5.00'}

    asyncio.run(exercise())
    assert service.store.settings()['target_dollars'] == '5.00'
    assert service.store.control()['enabled'] is False
