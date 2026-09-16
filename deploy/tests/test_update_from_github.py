from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location('pivot_updater', Path(__file__).parents[1] / 'update_from_github.py')
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
NOW = datetime(2026, 9, 16, 21, tzinfo=timezone.utc)
OLD, NEW = 'a' * 40, 'b' * 40


def health(revision=OLD):
    return {'ok': True, 'legacy_loaded': False, 'live_enabled': False, 'revision': revision}


def snapshot(revision=OLD):
    return {'revision': revision, 'live_enabled': False, 'review_required': False,
            'account': {'mode': 'live'}, 'account_at': NOW.isoformat(), 'account_error': None,
            'positions': [], 'orders': [], 'execution': {'trade': None}}


def test_fresh_flat_disabled_snapshot_allows_replacement():
    assert updater.ready_to_replace(health(), snapshot(), NOW)[0] is True


@pytest.mark.parametrize('patch', [
    {'live_enabled': True}, {'live_enabled': None}, {'review_required': True}, {'review_required': None},
    {'account': None}, {'account_error': 'offline'}, {'positions': [{}]}, {'orders': [{}]},
    {'positions': None}, {'orders': None}, {'execution': {}}, {'execution': {'trade': {}}},
    {'account_at': None}, {'account_at': 'bad'}, {'account_at': NOW.replace(tzinfo=None).isoformat()},
    {'account_at': (NOW - timedelta(seconds=61)).isoformat()},
    {'account_at': (NOW + timedelta(seconds=1)).isoformat()},
])
def test_any_unverified_or_active_state_blocks_replacement(patch):
    assert updater.ready_to_replace(health(), {**snapshot(), **patch}, NOW)[0] is False


@pytest.mark.parametrize('patch', [{'ok': False}, {'legacy_loaded': True}, {'live_enabled': True}, {'live_enabled': None}])
def test_health_and_snapshot_must_agree_on_off(patch):
    assert updater.ready_to_replace({**health(), **patch}, snapshot(), NOW)[0] is False


def test_missing_snapshot_fields_are_not_treated_as_flat_or_off():
    for field in ('live_enabled', 'review_required', 'account', 'account_at', 'positions', 'orders', 'execution'):
        value = snapshot()
        value.pop(field)
        assert updater.ready_to_replace(health(), value, NOW)[0] is False


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    release = tmp_path / 'releases' / NEW
    (release / 'deploy').mkdir(parents=True)
    (release / 'deploy' / 'update_from_github.py').write_text('RELEASE = "tested"\n')
    state = {'health': health(), 'snapshot': snapshot(), 'rollouts': [], 'builds': [], 'waits': []}
    monkeypatch.setattr(updater, 'utcnow', lambda: NOW)
    monkeypatch.setattr(updater, 'fetch_revision', lambda root: NEW)
    monkeypatch.setattr(updater, 'current_image', lambda: f'pivot-video:{OLD}')
    monkeypatch.setattr(updater, 'release_directory', lambda root, revision: release)
    monkeypatch.setattr(updater, 'build_release', lambda directory, revision: state['builds'].append(revision) or f'pivot-video:{revision}')
    monkeypatch.setattr(updater, 'read_local', lambda path: deepcopy(state['health' if path.endswith('health') else 'snapshot']))
    monkeypatch.setattr(updater, 'compose_up', lambda root, image: state['rollouts'].append(image))
    monkeypatch.setattr(updater, 'wait_healthy', lambda revision: state['waits'].append(revision))
    return tmp_path, state


def status(root):
    return json.loads((root / 'data' / 'pivot-v2' / 'deployment-status.json').read_text())


def test_live_on_builds_but_never_stops_or_replaces_current_container(pipeline):
    root, state = pipeline
    state['snapshot']['live_enabled'] = True
    updater.update(root)
    assert state['builds'] == [NEW]
    assert state['rollouts'] == []
    assert status(root)['state'] == 'waiting_off'
    assert status(root)['candidate_revision'] == NEW


def test_open_trade_leaves_old_runner_running(pipeline):
    root, state = pipeline
    state['snapshot']['execution']['trade'] = {'stage': 'exiting'}
    updater.update(root)
    assert state['rollouts'] == []
    assert status(root)['state'] == 'blocked_exposure'


def test_safe_release_validates_new_revision_and_refreshes_installed_updater(pipeline):
    root, state = pipeline
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}']
    assert state['waits'] == [NEW]
    assert status(root)['state'] == 'updated'
    assert status(root)['active_revision'] == NEW
    assert status(root)['candidate_revision'] is None
    assert (root / 'config' / 'update_from_github.py').read_text() == 'RELEASE = "tested"\n'
    assert (root / 'config' / 'update_from_github.py').stat().st_mode & 0o777 == 0o600


def test_failed_replacement_rolls_back_and_does_not_repeat_failed_revision(pipeline, monkeypatch):
    root, state = pipeline
    def verify(revision):
        state['waits'].append(revision)
        if revision == NEW:
            raise updater.UpdateError('Health check failed')
    monkeypatch.setattr(updater, 'wait_healthy', verify)
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}', f'pivot-video:{OLD}']
    assert state['waits'] == [NEW, OLD]
    assert status(root)['state'] == 'rolled_back'
    assert not (root / 'config' / 'update_from_github.py').exists()
    updater.update(root)
    assert len(state['rollouts']) == 2
    assert status(root)['state'] == 'rolled_back'
    assert status(root)['checked_at'] == NOW.isoformat()


def test_rollback_rejection_survives_later_github_failure_and_status_changes(pipeline, monkeypatch):
    root, state = pipeline
    def verify(revision):
        if revision == NEW:
            raise updater.UpdateError('Health check failed')
    monkeypatch.setattr(updater, 'wait_healthy', verify)
    updater.update(root)
    assert updater.rejected_revision(root) == NEW
    def unavailable(root):
        raise updater.UpdateError('GitHub unavailable')
    monkeypatch.setattr(updater, 'fetch_revision', unavailable)
    with pytest.raises(updater.UpdateError):
        updater.update(root)
    assert status(root)['state'] == 'checking'
    updater.write_status(root, state='error', reason='GitHub unavailable')
    monkeypatch.setattr(updater, 'fetch_revision', lambda root: NEW)
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}', f'pivot-video:{OLD}']
    assert status(root)['state'] == 'rolled_back'


def test_status_reader_rejects_symlinks_large_files_and_pipes_without_blocking(tmp_path):
    plain = tmp_path / 'normal.json'
    plain.write_text('{"state":"current"}')
    linked = tmp_path / 'linked.json'
    linked.symlink_to(plain)
    assert updater.read_record(linked) == {}
    plain.write_text(' ' * 4097)
    assert updater.read_record(plain) == {}
    pipe = tmp_path / 'pipe'
    updater.os.mkfifo(pipe)
    assert updater.read_record(pipe) == {}


def test_revision_mismatch_never_stops_container(pipeline):
    root, state = pipeline
    state['health']['revision'] = NEW
    with pytest.raises(updater.UpdateError, match='disagree'):
        updater.update(root)
    assert state['rollouts'] == []


def test_build_only_never_touches_running_container(pipeline):
    root, state = pipeline
    updater.update(root, build_only=True)
    assert state['rollouts'] == []
    assert status(root)['state'] == 'built'


def test_missing_first_install_requires_manual_bootstrap(pipeline, monkeypatch):
    root, state = pipeline
    monkeypatch.setattr(updater, 'current_image', lambda: None)
    updater.update(root)
    assert state['rollouts'] == []
    assert status(root)['state'] == 'built'


def test_deployment_lock_held_during_preflight_rollout_and_validation(pipeline, monkeypatch):
    root, state = pipeline
    def assert_locked():
        with (root / 'data' / 'pivot-v2' / 'deployment.lock').open('a') as stream:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
    def read(path):
        assert_locked()
        return state['health' if path.endswith('health') else 'snapshot']
    monkeypatch.setattr(updater, 'read_local', read)
    monkeypatch.setattr(updater, 'compose_up', lambda root, image: assert_locked())
    monkeypatch.setattr(updater, 'wait_healthy', lambda revision: assert_locked())
    updater.update(root)
    with (root / 'data' / 'pivot-v2' / 'deployment.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)


def test_status_never_persists_extra_snapshot_or_secret_fields(tmp_path):
    updater.write_status(tmp_path, state='current', active_revision=OLD, account={'secret': 'x'}, api_key='x')
    assert 'secret' not in json.dumps(status(tmp_path))
    assert 'api_key' not in status(tmp_path)
    assert (tmp_path / 'data' / 'pivot-v2' / 'deployment-status.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('kind,name', [(tarfile.SYMTYPE, 'link'), (tarfile.LNKTYPE, 'link'), (tarfile.REGTYPE, '../outside')])
def test_archive_rejects_host_links_and_path_escape(tmp_path, monkeypatch, kind, name):
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode='w') as archive:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = '/private/config'
        archive.addfile(entry, io.BytesIO())
    monkeypatch.setattr(updater.subprocess, 'run', lambda *args, **kwargs: type('Result', (), {'stdout': content.getvalue()})())
    with pytest.raises(updater.UpdateError):
        updater.release_directory(tmp_path, NEW)
    assert not (tmp_path / 'releases' / NEW).exists()
