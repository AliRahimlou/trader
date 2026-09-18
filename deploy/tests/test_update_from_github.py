from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest
from pivot.deployment import EntryGate

SPEC = importlib.util.spec_from_file_location('pivot_updater', Path(__file__).parents[1] / 'update_from_github.py')
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
REAL_TIMER_REFRESH = updater.refresh_installed_timer
NOW = datetime(2026, 9, 16, 21, tzinfo=timezone.utc)
OLD, NEW = 'a' * 40, 'b' * 40
PERMISSION = 'c' * 64


def health(revision=OLD):
    return {'ok': True, 'legacy_loaded': False, 'live_enabled': False, 'revision': revision}


def snapshot(revision=OLD):
    return {'revision': revision, 'live_enabled': False, 'review_required': False,
            'account': {'mode': 'live'}, 'account_at': NOW.isoformat(), 'account_error': None,
            'positions': [], 'orders': [], 'execution': {'trade': None}}


def hold_record(**patch):
    return {'version': 'entry-hold-v1', 'id': 'd' * 32, 'previous_revision': OLD,
            'candidate_revision': NEW, 'created_at': NOW.isoformat(),
            'permission_token': PERMISSION, 'saved_live_enabled': True,
            'legacy_bootstrap': False, **patch}


def readiness(revision=OLD, hold=None, **patch):
    return {'ok': True, 'revision': revision, 'deployment_protocol': 'entry-gate-v1',
            'read_started_at': NOW.isoformat(), 'checked_at': NOW.isoformat(),
            'gate': {'configured': True, 'locked': True, 'hold_present': True,
                     'hold_valid': True, 'hold_id': (hold or hold_record())['id']},
            'account_ready': True, 'account_identity_matches': True,
            'positions_count': 0, 'orders_count': 0, 'active_trade': False,
            'permission_token': PERMISSION, 'saved_live_enabled': False,
            'live_enabled': False, 'review_required': False, **patch}


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


def test_replacement_waits_for_worker_readiness_even_when_http_is_healthy(monkeypatch):
    calls=[]
    def read(path):
        calls.append(path)
        return {**health(NEW), 'ready':False, 'deployment_protocol':'entry-gate-v1'}
    monkeypatch.setattr(updater, 'read_local', read)
    ticks=iter([0,0,121])
    with pytest.raises(updater.UpdateError, match='replacement'):
        updater.wait_healthy(NEW,hold=hold_record(),locked_at=NOW,timeout=120,
                             sleep=lambda _:None,monotonic=lambda:next(ticks))
    assert calls == ['/api/health']


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    release = tmp_path / 'releases' / NEW
    (release / 'deploy').mkdir(parents=True)
    (release / 'deploy' / 'update_from_github.py').write_text('RELEASE = "tested"\n')
    state = {'health': {**health(), 'deployment_protocol': 'entry-gate-v1'}, 'snapshot': snapshot(),
             'revision': OLD, 'readiness': {}, 'revision_readiness': {}, 'rollouts': [],
             'builds': [], 'waits': [], 'timers': []}
    monkeypatch.setattr(updater, 'utcnow', lambda: NOW)
    monkeypatch.setattr(updater, 'fetch_revision', lambda root: NEW)
    monkeypatch.setattr(updater, 'current_image', lambda: f"pivot-video:{state['revision']}")
    monkeypatch.setattr(updater, 'release_directory', lambda root, revision: release)
    monkeypatch.setattr(updater, 'build_release', lambda directory, revision: state['builds'].append(revision) or f'pivot-video:{revision}')
    def read(path):
        if path == '/api/deployment-readiness':
            patch = {**state['readiness'], **state['revision_readiness'].get(state['revision'], {})}
            patch.setdefault('gate', EntryGate(tmp_path / 'data' / 'pivot-v2' / 'deployment.lock').status())
            return readiness(state['revision'], updater.read_record(updater.hold_path(tmp_path)), **patch)
        return deepcopy(state['health' if path.endswith('health') else 'snapshot'])
    def compose(root, image):
        # A fake container change only; never Docker or broker calls.
        state['rollouts'].append(image)
        state['revision'] = image.split(':')[1]
        state['health']['revision'] = state['revision']
        state['snapshot']['revision'] = state['revision']
        if state['revision'] == NEW:
            state['health']['deployment_protocol'] = 'entry-gate-v1'
    actual_wait = updater.wait_healthy
    def verify(revision, **kwargs):
        state['waits'].append(revision)
        ticks = iter([0, 0, 121])
        return actual_wait(revision, **kwargs, sleep=lambda delay: None, monotonic=lambda: next(ticks))
    monkeypatch.setattr(updater, 'read_local', read)
    monkeypatch.setattr(updater, 'compose_up', compose)
    monkeypatch.setattr(updater, 'wait_healthy', verify)
    monkeypatch.setattr(updater, 'refresh_installed_timer', lambda root, release: state['timers'].append(state['revision']))
    return tmp_path, state


def status(root):
    return json.loads((root / 'data' / 'pivot-v2' / 'deployment-status.json').read_text())


def test_legacy_live_on_builds_but_never_stops_or_replaces_current_container(pipeline):
    root, state = pipeline
    state['health'].pop('deployment_protocol')
    state['snapshot']['live_enabled'] = True
    updater.update(root)
    assert state['builds'] == [NEW]
    assert state['rollouts'] == []
    assert status(root)['state'] == 'bootstrap_required'
    assert status(root)['candidate_revision'] == NEW


def test_open_trade_leaves_old_runner_running(pipeline):
    root, state = pipeline
    state['readiness']['active_trade'] = True
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
    def verify(revision, **kwargs):
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
    def verify(revision, **kwargs):
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
    read_original = updater.read_local
    compose_original = updater.compose_up
    wait_original = updater.wait_healthy
    def assert_locked():
        with (root / 'data' / 'pivot-v2' / 'deployment.lock').open('a') as stream:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
    def read(path):
        assert_locked()
        return read_original(path)
    def compose(root, image):
        assert_locked()
        assert updater.valid_hold(updater.read_record(updater.hold_path(root)))
        return compose_original(root, image)
    def verify(revision, **kwargs):
        assert_locked()
        assert updater.hold_path(root).exists()
        return wait_original(revision, **kwargs)
    monkeypatch.setattr(updater, 'read_local', read)
    monkeypatch.setattr(updater, 'compose_up', compose)
    monkeypatch.setattr(updater, 'wait_healthy', verify)
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


@pytest.mark.parametrize('patch', [
    {'ok': False}, {'revision': NEW}, {'deployment_protocol': None},
    {'read_started_at': (NOW - timedelta(microseconds=1)).isoformat()},
    {'read_started_at': NOW.replace(tzinfo=None).isoformat()},
    {'checked_at': (NOW + timedelta(seconds=1)).isoformat()},
    {'checked_at': (NOW - timedelta(seconds=1)).isoformat()},
    {'account_ready': False}, {'account_identity_matches': False},
    {'positions_count': 1}, {'orders_count': 1}, {'active_trade': True},
    {'positions_count': False}, {'orders_count': -1}, {'active_trade': None},
    {'permission_token': None}, {'permission_token': 'bad'},
    {'live_enabled': None}, {'saved_live_enabled': None}, {'review_required': None},
])
def test_gated_readiness_rejects_unverified_old_future_or_exposed_state(patch):
    allowed, _ = updater.readiness_allowed(readiness(**patch), OLD, hold_record(), NOW, NOW)
    assert allowed is False


@pytest.mark.parametrize('patch', [
    {'configured': False}, {'locked': False}, {'hold_present': False}, {'hold_valid': False},
    {'hold_id': 'e' * 32}, {'error': 'entry_gate_unavailable'},
])
def test_readiness_requires_runtime_acknowledgement_of_own_hold(patch):
    value = readiness()
    value['gate'].update(patch)
    assert updater.readiness_allowed(value, OLD, hold_record(), NOW, NOW)[0] is False


def test_even_post_lock_reads_cannot_be_old():
    assert updater.readiness_allowed(readiness(), OLD, hold_record(), NOW, NOW + timedelta(seconds=61))[0] is False


def test_live_on_update_preserves_control_and_holds_entries_through_commissioning(pipeline, monkeypatch):
    root, state = pipeline
    state['health']['live_enabled'] = True
    state['readiness'].update(saved_live_enabled=True, live_enabled=True)
    control = root / 'data' / 'pivot-v2' / 'control.json'
    control.parent.mkdir(parents=True)
    original = b'{"live_enabled":true,"amount":5,"account_id":"private"}\n'
    control.write_bytes(original)
    verify_original = updater.wait_healthy
    def verify(revision, **kwargs):
        persisted = updater.read_record(updater.hold_path(root))
        assert persisted['permission_token'] == PERMISSION
        assert persisted['saved_live_enabled'] is True
        assert kwargs['permission_token'] == PERMISSION
        assert control.read_bytes() == original
        return verify_original(revision, **kwargs)
    monkeypatch.setattr(updater, 'wait_healthy', verify)
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}']
    assert not updater.hold_path(root).exists()
    assert control.read_bytes() == original
    assert state['timers'] == [NEW]


def test_policy_review_requirement_remains_allowed_without_rewriting_consent(pipeline):
    root, state = pipeline
    state['readiness'].update(saved_live_enabled=True, live_enabled=True)
    state['revision_readiness'][NEW] = {'review_required': True, 'live_enabled': False}
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}']
    assert status(root)['state'] == 'updated'


@pytest.mark.parametrize('patch', [{'positions_count': 1}, {'orders_count': 1}, {'active_trade': True},
                                  {'read_started_at': (NOW - timedelta(seconds=1)).isoformat()}])
def test_unready_preflight_releases_own_hold_without_container_swap(pipeline, patch):
    root, state = pipeline
    state['readiness'].update(patch)
    updater.update(root)
    assert state['rollouts'] == []
    assert not updater.hold_path(root).exists()
    assert status(root)['state'] == 'blocked_exposure'


def test_preflight_transport_failure_releases_only_own_hold(pipeline, monkeypatch):
    root, state = pipeline
    original = updater.read_local
    def unavailable(path):
        if path == '/api/deployment-readiness':
            raise updater.UpdateError('Readiness unavailable')
        return original(path)
    monkeypatch.setattr(updater, 'read_local', unavailable)
    with pytest.raises(updater.UpdateError):
        updater.update(root)
    assert state['rollouts'] == []
    assert not updater.hold_path(root).exists()


def test_permission_change_in_candidate_requires_verified_rollback(pipeline):
    root, state = pipeline
    state['readiness'].update(saved_live_enabled=True, live_enabled=True)
    state['revision_readiness'][NEW] = {'permission_token': 'e' * 64}
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}', f'pivot-video:{OLD}']
    assert status(root)['state'] == 'rolled_back'
    assert not updater.hold_path(root).exists()
    assert state['timers'] == []


def test_unverified_rollback_retains_durable_hold(pipeline, monkeypatch):
    root, state = pipeline
    state['revision_readiness'][NEW] = {'permission_token': 'e' * 64}
    compose_original = updater.compose_up
    # The permission subsequently changes on the persisted control as well.
    def compose(root, image):
        compose_original(root, image)
        if image.endswith(OLD):
            state['readiness']['permission_token'] = 'e' * 64
    monkeypatch.setattr(updater, 'compose_up', compose)
    with pytest.raises(updater.UpdateError, match='rollback'):
        updater.update(root)
    assert updater.hold_path(root).exists()
    assert updater.read_record(updater.hold_path(root))['permission_token'] == PERMISSION
    assert status(root)['state'] == 'recovery_required'


def test_inflight_shared_entry_lock_defers_update_without_hold(pipeline):
    root, state = pipeline
    path = root / 'data' / 'pivot-v2' / 'deployment.lock'
    path.parent.mkdir(parents=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        updater.update(root)
    assert status(root)['state'] == 'waiting_entry'
    assert state['rollouts'] == []
    assert not updater.hold_path(root).exists()


def test_legacy_off_can_bootstrap_but_candidate_must_acknowledge_gate(pipeline):
    root, state = pipeline
    state['health'].pop('deployment_protocol')
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}']
    assert status(root)['state'] == 'updated'
    assert not updater.hold_path(root).exists()


def test_legacy_bootstrap_never_activates_saved_permission(pipeline):
    root, state = pipeline
    state['health'].pop('deployment_protocol')
    state['revision_readiness'][NEW] = {'saved_live_enabled': True, 'live_enabled': True}
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}', f'pivot-video:{OLD}']
    assert status(root)['state'] == 'rolled_back'


def test_candidate_without_gate_protocol_cannot_resume_entries(pipeline, monkeypatch):
    root, state = pipeline
    read_original = updater.read_local
    def read(path):
        value = read_original(path)
        if path == '/api/health' and state['revision'] == NEW:
            value.pop('deployment_protocol', None)
        return value
    monkeypatch.setattr(updater, 'read_local', read)
    updater.update(root)
    assert state['rollouts'] == [f'pivot-video:{NEW}', f'pivot-video:{OLD}']
    assert status(root)['state'] == 'rolled_back'
    assert not updater.hold_path(root).exists()


def test_legacy_off_rollback_checks_old_flat_off_snapshot(pipeline, monkeypatch):
    root, state = pipeline
    state['health'].pop('deployment_protocol')
    state['revision_readiness'][NEW] = {'permission_token': None}
    read_original = updater.read_local
    snapshots = []
    def read(path):
        value = read_original(path)
        if path == '/api/health' and state['revision'] == OLD:
            value.pop('deployment_protocol', None)
        if path == '/api/snapshot':
            snapshots.append(state['revision'])
        return value
    monkeypatch.setattr(updater, 'read_local', read)
    updater.update(root)
    assert snapshots == [OLD, OLD]
    assert state['rollouts'] == [f'pivot-video:{NEW}', f'pivot-video:{OLD}']
    assert not updater.hold_path(root).exists()


def test_gate_health_failure_never_creates_hold_or_stops_container(pipeline):
    root, state = pipeline
    state['health']['ok'] = False
    with pytest.raises(updater.UpdateError, match='health'):
        updater.update(root)
    assert state['rollouts'] == []
    assert not updater.hold_path(root).exists()


def test_recover_candidate_hold_precedes_fetch_and_current_shortcut(pipeline, monkeypatch):
    root, state = pipeline
    state['revision'] = NEW
    state['health']['revision'] = NEW
    updater.save_hold(root, hold_record())
    def must_not_fetch(root):
        raise AssertionError('Recovery must precede GitHub fetch')
    monkeypatch.setattr(updater, 'fetch_revision', must_not_fetch)
    updater.update(root)
    assert state['waits'] == [NEW]
    assert state['rollouts'] == []
    assert not updater.hold_path(root).exists()
    assert status(root)['state'] == 'updated'


def test_crash_before_replacement_can_recover_previous_permission(pipeline, monkeypatch):
    root, state = pipeline
    updater.save_hold(root, hold_record())
    monkeypatch.setattr(updater, 'fetch_revision', lambda root: OLD)
    updater.update(root)
    assert state['waits'] == [OLD]
    assert not updater.hold_path(root).exists()
    assert state['rollouts'] == []
    assert status(root)['state'] == 'current'


@pytest.mark.parametrize('patch', [{'permission_token': None}, {'permission_token': 'e' * 64},
                                  {'id': 'bad'}, {'candidate_revision': 'bad'},
                                  {'created_at': (NOW + timedelta(seconds=1)).isoformat()}])
def test_crash_recovery_never_clears_unverifiable_hold(pipeline, monkeypatch, patch):
    root, state = pipeline
    updater.save_hold(root, hold_record(**patch))
    original = updater.hold_path(root).read_bytes()
    monkeypatch.setattr(updater, 'fetch_revision', lambda root: pytest.fail('Hold recovery must happen first'))
    with pytest.raises(updater.UpdateError):
        updater.update(root)
    assert updater.hold_path(root).read_bytes() == original
    assert state['rollouts'] == []
    assert status(root)['state'] == 'recovery_required'


def test_unexpected_running_revision_keeps_crash_hold(pipeline):
    root, state = pipeline
    updater.save_hold(root, hold_record())
    state['revision'] = 'f' * 40
    with pytest.raises(updater.UpdateError):
        updater.update(root)
    assert updater.hold_path(root).exists()
    assert state['waits'] == []


@pytest.mark.parametrize('malformation', ['unknown_key', 'duplicate_key', 'non_utc_time',
                                         'noncanonical_time', 'invalid_date', 'invalid_saved_flag',
                                         'invalid_legacy_flag', 'missing_created_at'])
def test_legacy_off_recovery_never_clears_malformed_hold(pipeline, malformation):
    root, state = pipeline
    state['health'].pop('deployment_protocol')
    value = hold_record(permission_token=None, saved_live_enabled=False, legacy_bootstrap=True)
    if malformation == 'unknown_key':
        value['unknown_field'] = 'unexpected'
    elif malformation == 'non_utc_time':
        value['created_at'] = '2026-09-16T20:00:00-01:00'
    elif malformation == 'noncanonical_time':
        value['created_at'] = '2026-09-16 21:00:00+00:00'
    elif malformation == 'invalid_date':
        value['created_at'] = '2026-99-99T21:00:00+00:00'
    elif malformation == 'invalid_saved_flag':
        value['saved_live_enabled'] = None
    elif malformation == 'invalid_legacy_flag':
        value['legacy_bootstrap'] = 1
    elif malformation == 'missing_created_at':
        value.pop('created_at')
    updater.save_hold(root, value)
    if malformation == 'duplicate_key':
        updater.hold_path(root).write_text('{"legacy_bootstrap":false,' + json.dumps(value)[1:])
    original = updater.hold_path(root).read_bytes()
    with pytest.raises(updater.UpdateError):
        updater.update(root)
    assert updater.hold_path(root).read_bytes() == original
    assert status(root)['state'] == 'recovery_required'
    assert state['waits'] == state['rollouts'] == []


def test_valid_legacy_off_crash_hold_recovers_without_changing_permission(pipeline, monkeypatch):
    root, state = pipeline
    state['health'].pop('deployment_protocol')
    updater.save_hold(root, hold_record(permission_token=None, saved_live_enabled=False, legacy_bootstrap=True))
    monkeypatch.setattr(updater, 'fetch_revision', lambda root: OLD)
    updater.update(root)
    assert state['waits'] == [OLD]
    assert state['rollouts'] == []
    assert not updater.hold_path(root).exists()
    assert status(root)['state'] == 'current'


def test_crash_hold_with_open_exposure_stays_until_flat(pipeline):
    root, state = pipeline
    updater.save_hold(root, hold_record())
    state['readiness']['positions_count'] = 1
    with pytest.raises(updater.UpdateError):
        updater.update(root)
    assert updater.hold_path(root).exists()
    assert state['rollouts'] == []


def test_clear_hold_never_removes_replaced_or_symlinked_barrier(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'utcnow', lambda: NOW)
    original = hold_record()
    updater.save_hold(tmp_path, {**original, 'id': 'e' * 32})
    with pytest.raises(updater.UpdateError):
        updater.clear_hold(tmp_path, original)
    updater.hold_path(tmp_path).unlink()
    target = tmp_path / 'private.json'
    target.write_text(json.dumps(original))
    updater.hold_path(tmp_path).symlink_to(target)
    with pytest.raises(updater.UpdateError):
        updater.clear_hold(tmp_path, original)
    assert updater.hold_path(tmp_path).is_symlink()


@pytest.fixture
def timer_installation(tmp_path, monkeypatch):
    release = tmp_path / 'release'
    (release / 'deploy').mkdir(parents=True)
    data = (Path(__file__).parents[1] / 'pivot-update.timer').read_bytes()
    (release / 'deploy' / 'pivot-update.timer').write_bytes(data)
    installed = tmp_path / '.config' / 'systemd' / 'user' / 'pivot-update.timer'
    installed.parent.mkdir(parents=True)
    installed.write_text('old timer')
    monkeypatch.setattr(updater.Path, 'home', lambda: tmp_path)
    commands = []
    monkeypatch.setattr(updater, 'run', lambda args: commands.append(args))
    return tmp_path, release, installed, data, commands


def test_timer_refreshed_only_from_verified_release_in_offline_fake_home(timer_installation):
    tmp_path, release, installed, data, commands = timer_installation
    updater.refresh_installed_timer(tmp_path, release)
    assert installed.read_bytes() == data
    assert b'OnUnitInactiveSec=30s' in data
    assert commands == [['systemctl', '--user', 'daemon-reload'], ['systemctl', '--user', 'restart', 'pivot-update.timer']]
    marker = updater.read_record(tmp_path / 'config' / 'update-timer.json')
    assert marker == {'version': 'update-timer-v1', 'sha256': updater.sha256(data).hexdigest()}
    updater.refresh_installed_timer(tmp_path, release)
    assert len(commands) == 2


@pytest.mark.parametrize('marker', [None, {'version': 'wrong', 'sha256': 'f' * 64},
                                    {'version': 'update-timer-v1', 'sha256': 'f' * 64}])
def test_matching_timer_bytes_without_success_marker_still_reload(timer_installation, marker):
    root, release, installed, data, commands = timer_installation
    installed.write_bytes(data)
    path = root / 'config' / 'update-timer.json'
    if marker is not None:
        updater.atomic_write(path, json.dumps(marker).encode())
    updater.refresh_installed_timer(root, release)
    assert len(commands) == 2
    assert updater.read_record(path)['sha256'] == updater.sha256(data).hexdigest()


def test_changed_timer_file_is_repaired_even_when_success_digest_matches(timer_installation):
    root, release, installed, data, commands = timer_installation
    updater.refresh_installed_timer(root, release)
    installed.write_text('OnUnitInactiveSec=5min\nUnit=pivot-update.service\n')
    updater.refresh_installed_timer(root, release)
    assert installed.read_bytes() == data
    assert len(commands) == 4


@pytest.mark.parametrize('failed_command', ['daemon-reload', 'restart'])
def test_partial_timer_command_failure_remains_retriable(timer_installation, monkeypatch, failed_command):
    root, release, installed, data, commands = timer_installation
    marker = root / 'config' / 'update-timer.json'
    original_marker = {'version': 'update-timer-v1', 'sha256': 'f' * 64}
    updater.atomic_write(marker, json.dumps(original_marker).encode())
    def failing(args):
        commands.append(args)
        if args[2] == failed_command:
            raise updater.UpdateError('Timer command failed')
    monkeypatch.setattr(updater, 'run', failing)
    with pytest.raises(updater.UpdateError):
        updater.refresh_installed_timer(root, release)
    assert installed.read_bytes() == data
    assert updater.read_record(marker) == original_marker
    monkeypatch.setattr(updater, 'run', lambda args: commands.append(args))
    prior_count = len(commands)
    updater.refresh_installed_timer(root, release)
    assert len(commands) == prior_count + 2
    assert updater.read_record(marker)['sha256'] == updater.sha256(data).hexdigest()


def test_failure_persisting_timer_success_retries_both_commands(timer_installation, monkeypatch):
    root, release, installed, data, commands = timer_installation
    original_write = updater.atomic_write
    def failing(path, content):
        if path.name == 'update-timer.json':
            raise OSError('Mock write failure')
        return original_write(path, content)
    monkeypatch.setattr(updater, 'atomic_write', failing)
    with pytest.raises(updater.UpdateError):
        updater.refresh_installed_timer(root, release)
    assert len(commands) == 2
    assert not (root / 'config' / 'update-timer.json').exists()
    monkeypatch.setattr(updater, 'atomic_write', original_write)
    updater.refresh_installed_timer(root, release)
    assert len(commands) == 4


def test_current_release_migrates_legacy_timer_without_container_or_live_changes(pipeline, timer_installation, monkeypatch):
    root, state = pipeline
    _, release, installed, data, commands = timer_installation
    state['revision'] = NEW
    state['health']['revision'] = NEW
    state['health']['live_enabled'] = True
    state['snapshot']['live_enabled'] = True
    control = root / 'data' / 'pivot-v2' / 'control.json'
    control.parent.mkdir(parents=True)
    original_control = b'{"enabled":true,"amount":5}\n'
    control.write_bytes(original_control)
    releases = []
    monkeypatch.setattr(updater, 'release_directory', lambda root, revision: releases.append(revision) or release)
    monkeypatch.setattr(updater, 'refresh_installed_timer', REAL_TIMER_REFRESH)
    original_read = updater.read_local
    def health_only(path):
        assert path == '/api/health', 'Timer-only reconciliation needs no broker readiness request'
        return original_read(path)
    monkeypatch.setattr(updater, 'read_local', health_only)
    updater.update(root)
    assert releases == [NEW] and installed.read_bytes() == data
    assert state['rollouts'] == state['builds'] == state['waits'] == []
    assert not updater.hold_path(root).exists()
    assert control.read_bytes() == original_control and state['health']['live_enabled'] is True
    assert status(root)['state'] == 'current'
    updater.update(root)
    assert len(commands) == 2
    assert control.read_bytes() == original_control


def test_unverified_current_release_cannot_reconcile_timer(pipeline, monkeypatch):
    root, state = pipeline
    state['revision'] = NEW
    state['health']['revision'] = NEW
    state['health']['ok'] = False
    with pytest.raises(updater.UpdateError, match='health'):
        updater.update(root)
    assert state['timers'] == state['rollouts'] == state['builds'] == []


def test_refresh_failure_after_candidate_health_keeps_recoverable_hold(pipeline, monkeypatch):
    root, state = pipeline
    def unavailable(root, release):
        raise updater.UpdateError('Timer refresh unavailable')
    monkeypatch.setattr(updater, 'refresh_installed_timer', unavailable)
    with pytest.raises(updater.UpdateError, match='Timer'):
        updater.update(root)
    assert state['revision'] == NEW
    assert updater.read_record(updater.hold_path(root))['permission_token'] == PERMISSION
    monkeypatch.setattr(updater, 'refresh_installed_timer', lambda root, release: None)
    updater.update(root)
    assert not updater.hold_path(root).exists()
    assert status(root)['state'] == 'updated'
