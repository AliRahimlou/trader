"""Hosted routes and browser origin restrictions, without broker or network access."""
from datetime import datetime, timedelta, timezone
import json
import os
import pytest
from fastapi.testclient import TestClient

from pivot.api import Hosting, create_app, deployment_status


class ServiceStub:
    executor = None

    def __init__(self):
        self.changes = []

    def stop(self):
        pass

    def worker_health(self):
        return {'ready':False, 'workers':[]}

    def snapshot(self):
        return {'live_enabled': False, 'private_account': 'owner-only fixture'}

    def save_settings(self, payload):
        self.changes.append(('settings', payload))
        return payload

    def set_live(self, payload):
        self.changes.append(('live', payload))
        return self.snapshot()


@pytest.mark.parametrize('origin', [
    'http://media.example.com', 'https://media.example.com/',
    'https://media.example.com/pivot', 'https://media.example.com?key=private',
    'https://media.example.com#fragment', 'https://user:secret@media.example.com',
    'https://*.example.com', 'https://media.example.com:bad',
    'https://media.example.com:443:1', 'https://media.example.com\\evil',
    'https://media..example.com', 'https://-media.example.com',
    'https://media.example.com\n', '//media.example.com',
])
def test_public_origin_is_a_single_strict_https_origin(origin):
    with pytest.raises(ValueError, match='PUBLIC_ORIGIN'):
        Hosting(public_origin=origin)


@pytest.mark.parametrize('prefix', ['/', 'pivot', '/pivot/', '/pivot//app', '/pivot/../app',
                                    '//evil.example', '/pivot?x=1', '/pivot%2fapp', '/pivot#app'])
def test_prefix_cannot_redirect_or_escape(prefix):
    with pytest.raises(ValueError, match='URL_PREFIX'):
        Hosting(url_prefix=prefix)


def test_hosted_configuration_is_explicit_and_safe_to_serialize():
    config = Hosting.from_values({'PUBLIC_ORIGIN': 'https://media.example.com', 'URL_PREFIX': '/pivot',
                                  'RUNTIME_NAME': 'AllSpark', 'INSIGHTSENTRY_API_KEY': 'private-key'})
    assert config.describe()['mode'] == 'hosted'
    assert 'AllSpark' in config.describe()['message']
    assert 'private-key' not in str(config.describe())
    assert Hosting().describe()['mode'] == 'local'


def test_deployment_revision_is_validated_and_reported_without_other_environment_values():
    revision = 'ab' * 20
    config = Hosting.from_values({'PUBLIC_ORIGIN': 'https://media.example.com',
        'PIVOT_RUNTIME_NAME': 'AllSpark', 'PIVOT_REVISION': revision, 'PRIVATE_KEY': 'hidden'})
    with TestClient(create_app(ServiceStub(), background=False, hosting=config)) as client:
        for route in ('/api/health', '/api/snapshot'):
            response = client.get(route)
            assert response.json()['revision'] == revision
            assert response.json()['app_version'] == '4.1.0'
            assert 'hidden' not in response.text
    with pytest.raises(ValueError, match='PIVOT_REVISION'):
        Hosting.from_values({'PIVOT_REVISION': 'accidental-secret-not-a-commit'})


@pytest.mark.parametrize('path_prefix', ['', '/pivot'])
def test_hosted_prefix_supports_stripped_and_preserved_proxy_paths(path_prefix):
    service = ServiceStub()
    config = Hosting('https://media.example.com', '/pivot', 'AllSpark')
    with TestClient(create_app(service, background=False, hosting=config), base_url='http://media.example.com') as client:
        # Caddy's handle_path strips /pivot. Preserved paths also work with root_path.
        response = client.get(path_prefix + '/api/snapshot')
        assert response.status_code == 200
        assert response.json()['hosting']['name'] == 'AllSpark'
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-frame-options'] == 'DENY'
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        for path in ('/', '/app.js', '/style.css', '/model.mjs'):
            assert client.get(path_prefix + path).status_code == 200
        for route, intent in [('settings', 'settings'), ('live', 'live-control')]:
            response = client.put(path_prefix + '/api/' + route, json={'test': True}, headers={
                'Origin': 'https://media.example.com', 'X-Pivot-Intent': intent})
            assert response.status_code == 200
        assert len(service.changes) == 2


@pytest.mark.parametrize('origin', [None, 'https://evil.example', 'http://media.example.com',
                                   'https://media.example.com.evil.example',
                                   'http://localhost:5173', 'http://127.0.0.1:5173',
                                   'https://media.example.com/pivot', 'null'])
def test_hosted_mutations_require_the_exact_configured_origin(origin):
    service = ServiceStub()
    config = Hosting('https://media.example.com', '/pivot')
    with TestClient(create_app(service, background=False, hosting=config), base_url='http://media.example.com') as client:
        headers = {'X-Pivot-Intent': 'live-control'}
        if origin is not None:
            headers['Origin'] = origin
        assert client.put('/api/live', json={}, headers=headers).status_code == 403
        assert service.changes == []


def test_hosted_wrong_intent_foreign_host_and_cors_are_rejected():
    service = ServiceStub()
    config = Hosting('https://media.example.com', '/pivot')
    with TestClient(create_app(service, background=False, hosting=config), base_url='http://media.example.com') as client:
        assert client.put('/api/live', json={}, headers={
            'Origin': config.public_origin, 'X-Pivot-Intent': 'settings'}).status_code == 403
        assert client.get('/api/snapshot', headers={'Host': 'evil.example'}).status_code == 400
        response = client.options('/api/live', headers={
            'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PUT',
            'Access-Control-Request-Headers': 'Content-Type,X-Pivot-Intent'})
        assert response.status_code == 400
        assert 'access-control-allow-origin' not in response.headers
        assert service.changes == []


def test_local_preview_and_backend_same_origin_remain_supported():
    service = ServiceStub()
    with TestClient(create_app(service, background=False), base_url='http://127.0.0.1:8011') as client:
        for origin in ('http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8011'):
            assert client.put('/api/settings', json={}, headers={
                'Origin': origin, 'X-Pivot-Intent': 'settings'}).status_code == 200
        assert client.get('/api/snapshot').json()['hosting']['mode'] == 'local'


def test_server_rejects_wildcard_listener_without_explicit_hosted_configuration(monkeypatch):
    from pivot import server
    monkeypatch.setattr('sys.argv', ['pivot', '--host', '0.0.0.0'])
    monkeypatch.setattr(server, 'dotenv_values', lambda _: {})
    monkeypatch.delenv('PUBLIC_ORIGIN', raising=False)
    monkeypatch.delenv('URL_PREFIX', raising=False)
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 2


def test_deployment_lock_rejects_live_enable_but_allows_disable(tmp_path):
    import fcntl
    path = tmp_path / 'deployment.lock'
    service = ServiceStub()
    with TestClient(create_app(service, background=False, deployment_lock=path)) as client:
        headers = {'Origin': 'http://testserver', 'X-Pivot-Intent': 'live-control'}
        with path.open('a') as deployment:
            fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)
            response = client.put('/api/live', json={'enabled': True}, headers=headers)
            assert response.status_code == 409
            assert response.json()['detail'] == 'Update in progress; wait for installation to finish'
            assert service.changes == []
            assert client.put('/api/live', json={'enabled': False}, headers=headers).status_code == 200
        assert client.put('/api/live', json={'enabled': True}, headers=headers).status_code == 200
        assert [payload['enabled'] for _, payload in service.changes] == [False, True]


def test_live_enable_holds_shared_lock_until_permission_change_completes(tmp_path):
    import fcntl
    path = tmp_path / 'deployment.lock'

    class ConcurrentService(ServiceStub):
        def set_live(self, payload):
            with path.open('a') as deployment:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return super().set_live(payload)

    service = ConcurrentService()
    with TestClient(create_app(service, background=False, deployment_lock=path)) as client:
        assert client.put('/api/live', json={'enabled': True}, headers={
            'Origin': 'http://testserver', 'X-Pivot-Intent': 'live-control'}).status_code == 200
    with path.open('a') as deployment:
        fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize('state', ['waiting_off', 'bootstrap_required', 'waiting_entry', 'recovery_required'])
def test_remote_update_status_exposes_only_validated_display_fields(tmp_path, state):
    now = datetime.now(timezone.utc)
    path = tmp_path / 'deployment-status.json'
    path.write_text(json.dumps({'state': state, 'checked_at': now.isoformat(),
        'active_revision': 'a' * 40, 'candidate_revision': 'b' * 40,
        'reason': 'secret credentials in command output', 'private_key': 'secret-api-key'}))
    with TestClient(create_app(ServiceStub(), background=False,
        hosting=Hosting('https://media.example.com', '/pivot'), deployment_status_path=path)) as client:
        response = client.get('/api/snapshot')
        status = response.json()['deployment']
        assert status['state'] == state
        assert 'turn Live money Off' not in status['message']
        if state in ('waiting_off', 'bootstrap_required'):
            assert status['message'] == 'Update queued: the installed updater needs a one-time upgrade.'
        assert status['active_revision'] == 'a' * 40
        assert status['candidate_revision'] == 'b' * 40
        assert 'secret' not in response.text
        assert 'reason' not in status


@pytest.mark.parametrize('payload', [None, [], {'state': []}, {'state': 'arbitrary secret'},
    {'state': 'current'}, {'state': 'current', 'checked_at': 'private-token'},
    {'state': 'current', 'checked_at': '2026-09-16T16:00:00'},
    {'state': 'current', 'checked_at': '2099-09-16T16:00:00+00:00'}])
def test_invalid_deployment_status_is_not_displayed(payload, tmp_path):
    path = tmp_path / 'deployment-status.json'
    path.write_text(json.dumps(payload))
    result = deployment_status(path, datetime(2026, 9, 16, 17, tzinfo=timezone.utc))
    assert result == {'state': 'unavailable', 'message': 'Update status is unavailable.'}


def test_update_status_reads_are_bounded_and_do_not_follow_links_or_pipes(tmp_path):
    target = tmp_path / 'private.json'
    target.write_text('x' * 4097)
    assert deployment_status(target)['state'] == 'unavailable'
    target.write_bytes(b'\xff')
    assert deployment_status(target)['state'] == 'unavailable'
    link = tmp_path / 'deployment-status.json'
    link.symlink_to(target)
    assert deployment_status(link)['state'] == 'unavailable'
    pipe = tmp_path / 'pipe'
    os.mkfifo(pipe)
    assert deployment_status(pipe)['state'] == 'unavailable'
    assert deployment_status(tmp_path)['state'] == 'unavailable'
    assert deployment_status(tmp_path / 'missing.json')['state'] == 'unavailable'


def test_old_update_check_is_not_reported_as_up_to_date(tmp_path):
    now = datetime(2026, 9, 16, 17, tzinfo=timezone.utc)
    path = tmp_path / 'deployment-status.json'
    path.write_text(json.dumps({'state': 'current', 'checked_at': (now-timedelta(minutes=11)).isoformat(),
                               'candidate_revision': 'not-a-commit-private-value'}))
    result = deployment_status(path, now)
    assert result['state'] == 'overdue'
    assert 'candidate_revision' not in result
    assert 'private' not in str(result)


def test_tested_build_can_run_until_the_updaters_service_timeout(tmp_path):
    now = datetime(2026, 9, 16, 17, tzinfo=timezone.utc)
    path = tmp_path / 'deployment-status.json'
    path.write_text(json.dumps({'state': 'building', 'checked_at': (now-timedelta(minutes=20)).isoformat()}))
    assert deployment_status(path, now)['state'] == 'building'
    assert deployment_status(path, now+timedelta(minutes=11))['state'] == 'overdue'


def test_local_snapshot_does_not_read_host_deployment_status(tmp_path):
    path = tmp_path / 'deployment-status.json'
    path.write_text(json.dumps({'state': 'current', 'checked_at': datetime.now(timezone.utc).isoformat()}))
    with TestClient(create_app(ServiceStub(), background=False, deployment_status_path=path)) as client:
        assert 'deployment' not in client.get('/api/snapshot').json()
