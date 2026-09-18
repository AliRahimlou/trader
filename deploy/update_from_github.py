#!/usr/bin/env python3
"""Outbound-only, tested releases for one owner-controlled Pivot installation.

This process never submits orders or changes live-money permission. Installation
of the first container and migration of its private runtime are deliberate setup
steps. Gate-capable releases preserve saved permission while pausing only new
entries during a verified flat-account switch. Legacy Live-On workers cannot
bootstrap this protocol through a GitHub push alone.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from uuid import uuid4

REPOSITORY = 'https://github.com/AliRahimlou/trader.git'
SHA = re.compile(r'[a-f0-9]{40}\Z')
IMAGE = re.compile(r'pivot-video:([a-f0-9]{40})\Z')
CONTAINER = 'pivot-video'
STATUS_KEYS = {'state', 'reason', 'active_revision', 'candidate_revision', 'checked_at', 'deployed_at'}
TOKEN = re.compile(r'[a-f0-9]{64}\Z')
HOLD_ID = re.compile(r'[a-f0-9]{32}\Z')


class UpdateError(RuntimeError):
    """Only deliberately sanitized messages reach persistent deployment status."""


def utcnow():
    return datetime.now(timezone.utc)


def run(args, *, env=None, timeout=120, output=True):
    try:
        result = subprocess.run(args, check=True, timeout=timeout, env=env,
                                stdout=subprocess.PIPE if output else None,
                                stderr=subprocess.PIPE if output else None)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise UpdateError(f'{Path(args[0]).name} command failed; inspect the update service log') from None
    return result.stdout.decode('utf-8').strip() if output else ''


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_status(root, **values):
    # No account snapshots, URLs with credentials, subprocess output or secrets.
    status = {key: value for key, value in values.items() if key in STATUS_KEYS}
    status['checked_at'] = utcnow().isoformat()
    atomic_write(root / 'data' / 'pivot-v2' / 'deployment-status.json',
                 (json.dumps(status, indent=2, sort_keys=True) + '\n').encode())
    print(f"Pivot update: {status.get('state', 'unknown')} — {status.get('reason', '')}", flush=True)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Repeated record field')
        value[key] = item
    return value


def read_record(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
                return {}
            raw = stream.read(4097)
        if len(raw) > 4096:
            return {}
        data = json.loads(raw, object_pairs_hook=unique_object)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def prior_status(root):
    data = read_record(root / 'data' / 'pivot-v2' / 'deployment-status.json')
    return {key: value for key, value in data.items() if key in STATUS_KEYS}


def rejected_revision(root):
    revision = read_record(root / 'config' / 'rejected-release.json').get('revision')
    return revision if isinstance(revision, str) and SHA.fullmatch(revision) else None


def reject_revision(root, revision):
    # Separate from public status: later network errors must not forget that this
    # exact image already failed commissioning and start replacing the app again.
    atomic_write(root / 'config' / 'rejected-release.json',
                 (json.dumps({'revision': revision, 'rejected_at': utcnow().isoformat()}) + '\n').encode())


@contextmanager
def locked(path, *, blocking=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise UpdateError('Another update or live-money change is in progress') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def read_local(path):
    connection = http.client.HTTPConnection('127.0.0.1', 18011,
                                            timeout=60 if path == '/api/deployment-readiness' else 5)
    try:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if response.status != 200 or len(raw) > 2 * 1024 * 1024:
            raise UpdateError('The current app did not return a valid local health response')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (OSError, ValueError, http.client.HTTPException):
        raise UpdateError('The current app is unavailable; automatic replacement is paused') from None
    finally:
        connection.close()


def ready_to_replace(health, snapshot, now):
    """Legacy-Off bootstrap only; this check never permits a legacy Live-On swap."""
    if health.get('ok') is not True or health.get('legacy_loaded') is not False:
        return False, 'The current app has not passed its health check'
    if health.get('live_enabled') is not False or snapshot.get('live_enabled') is not False:
        return False, 'Waiting for you to turn Live money Off before installing this release'
    if snapshot.get('review_required') is not False:
        return False, 'Waiting for you to turn the saved live-money permission Off'
    if not isinstance(snapshot.get('account'), dict) or snapshot.get('account_error'):
        return False, 'Waiting for a verified account snapshot'
    try:
        at = datetime.fromisoformat(snapshot['account_at'].replace('Z', '+00:00'))
        if at.tzinfo is None or not 0 <= (now - at).total_seconds() <= 60:
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError):
        return False, 'Waiting for an account snapshot less than one minute old'
    if not isinstance(snapshot.get('positions'), list) or not isinstance(snapshot.get('orders'), list):
        return False, 'Position and order state could not be verified'
    if snapshot['positions'] or snapshot['orders']:
        return False, 'Waiting for all positions and orders to finish'
    execution = snapshot.get('execution')
    if not isinstance(execution, dict) or 'trade' not in execution or execution['trade'] is not None:
        return False, 'Waiting for the current trade lifecycle to finish'
    return True, 'Live money is Off and the fresh account has no positions or orders'


def hold_path(root):
    return root / 'data' / 'pivot-v2' / 'entry-hold.json'


def aware(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Timezone required')
    return at.astimezone(timezone.utc)


def valid_hold(value):
    try:
        required = {'version', 'id', 'previous_revision', 'candidate_revision', 'created_at', 'legacy_bootstrap'}
        optional = {'permission_token', 'saved_live_enabled'}
        return (isinstance(value, dict) and required <= value.keys() and not value.keys() - required - optional
                and value.get('version') == 'entry-hold-v1'
                and isinstance(value.get('id'), str) and bool(HOLD_ID.fullmatch(value['id']))
                and all(isinstance(value.get(k), str) and SHA.fullmatch(value[k])
                        for k in ('previous_revision', 'candidate_revision'))
                and isinstance(value['created_at'], str)
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value['created_at']) is not None
                and aware(value['created_at']) <= utcnow()
                and (value.get('permission_token') is None or
                     isinstance(value['permission_token'], str) and bool(TOKEN.fullmatch(value['permission_token'])))
                and ('saved_live_enabled' not in value or type(value['saved_live_enabled']) is bool)
                and type(value.get('legacy_bootstrap')) is bool)
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def save_hold(root, hold):
    atomic_write(hold_path(root), (json.dumps(hold, sort_keys=True) + '\n').encode())


def clear_hold(root, hold):
    # Never remove a malformed, replaced, or somebody else's recovery barrier.
    if read_record(hold_path(root)) != hold or not valid_hold(hold):
        raise UpdateError('Entry hold changed unexpectedly; recovery verification is required')
    hold_path(root).unlink()
    sync_directory(hold_path(root).parent)


def readiness_allowed(value, revision, hold, locked_at, now, *, permission_token=None, require_off=False):
    """Fresh, post-lock broker evidence plus acknowledgement of our durable hold."""
    if (value.get('ok') is not True or value.get('revision') != revision
            or value.get('deployment_protocol') != 'entry-gate-v1'):
        return False, 'The runtime has not verified this revision and entry-gate protocol'
    gate = value.get('gate')
    if (not isinstance(gate, dict) or gate.get('configured') is not True or gate.get('locked') is not True
            or gate.get('error') or gate.get('hold_present') is not True
            or gate.get('hold_valid') is not True or gate.get('hold_id') != hold['id']):
        return False, 'The runtime has not acknowledged the exclusive entry gate and hold'
    try:
        started, checked = aware(value['read_started_at']), aware(value['checked_at'])
        if not locked_at <= started <= checked <= now or (now - started).total_seconds() > 60:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, 'Waiting for fresh broker reads started after the entry gate was acquired'
    if value.get('account_ready') is not True or value.get('account_identity_matches') is not True:
        return False, 'Waiting for a verified account and matching saved account identity'
    if (any(type(value.get(k)) is not int or value[k] < 0 for k in ('positions_count', 'orders_count'))
            or type(value.get('active_trade')) is not bool):
        return False, 'Position, order and trade lifecycle state could not be verified'
    if value['positions_count'] or value['orders_count'] or value['active_trade']:
        return False, 'Waiting for all positions, orders and saved trade lifecycles to finish'
    token = value.get('permission_token')
    if (not isinstance(token, str) or not TOKEN.fullmatch(token)
            or any(type(value.get(k)) is not bool for k in ('saved_live_enabled', 'live_enabled', 'review_required'))):
        return False, 'Saved owner permission could not be verified'
    if permission_token is not None and token != permission_token:
        return False, 'Saved owner permission changed during the update; recovery verification is required'
    if require_off and (value['saved_live_enabled'] or value['live_enabled'] or value['review_required']):
        return False, 'Legacy bootstrap requires saved Live money permission to remain Off'
    return True, 'Entry admissions are held, broker state is fresh and flat, and owner permission is verified'


def fetch_revision(root):
    repository = root / 'repository'
    if not repository.exists():
        run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--no-checkout',
             '--filter=blob:none', REPOSITORY, str(repository)], timeout=300)
    remote = run(['git', '-C', str(repository), 'remote', 'get-url', 'origin'])
    if remote != REPOSITORY:
        raise UpdateError('Repository origin does not match the configured public GitHub repository')
    run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repository), 'fetch',
         '--prune', 'origin', 'main'], timeout=300)
    revision = run(['git', '-C', str(repository), 'rev-parse', 'refs/remotes/origin/main'])
    if not SHA.fullmatch(revision):
        raise UpdateError('GitHub did not resolve main to a valid commit')
    return revision


def release_directory(root, revision):
    if not SHA.fullmatch(revision):
        raise UpdateError('Invalid release revision')
    releases = root / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    target = releases / revision
    if target.is_dir():
        return target
    temporary = Path(tempfile.mkdtemp(prefix='.release-', dir=releases))
    try:
        try:
            archive = subprocess.run(['git', '-C', str(root / 'repository'), 'archive', revision],
                                     check=True, capture_output=True, timeout=120).stdout
        except (OSError, subprocess.SubprocessError):
            raise UpdateError('Could not export the selected GitHub release') from None
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
            # Releases contain source files, never links back to host configuration.
            for member in tar.getmembers():
                if not (member.isfile() or member.isdir()):
                    raise UpdateError('Release archive contains an unsupported link or special file')
                parts = Path(member.name).parts
                if Path(member.name).is_absolute() or '..' in parts:
                    raise UpdateError('Release archive contains an invalid path')
            tar.extractall(temporary, filter='data')
        if not (temporary / 'deploy' / 'Dockerfile.runtime').is_file():
            raise UpdateError('The selected release does not contain the hosted build definition')
        temporary.rename(target)
        return target
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def current_image():
    try:
        image = run(['docker', 'inspect', '--format', '{{.Config.Image}}', CONTAINER])
    except UpdateError:
        return None
    return image if IMAGE.fullmatch(image) else None


def build_release(release, revision):
    image = f'pivot-video:{revision}'
    try:
        label = run(['docker', 'image', 'inspect', '--format',
                     '{{index .Config.Labels "org.opencontainers.image.revision"}}', image])
        if label == revision:
            return image
    except UpdateError:
        pass
    run(['docker', 'build', '--pull', '--build-arg', f'PIVOT_REVISION={revision}',
         '-f', str(release / 'deploy' / 'Dockerfile.runtime'), '-t', image, str(release)],
        timeout=1200, output=False)
    return image


def compose_up(root, image):
    match = IMAGE.fullmatch(image)
    if not match:
        raise UpdateError('Refusing an unrecognized release image')
    compose = root / 'config' / 'compose.yaml'
    if not compose.is_file():
        raise UpdateError('The installed host compose configuration is missing')
    environment = {**os.environ, 'PIVOT_ROOT': str(root), 'PIVOT_IMAGE': image,
                   'PIVOT_REVISION': match.group(1)}
    run(['docker', 'compose', '-f', str(compose), 'up', '-d', '--no-build',
         '--force-recreate', '--no-deps', 'app'], env=environment, timeout=120, output=False)


def wait_healthy(revision, *, hold, locked_at, permission_token=None, legacy_off=False,
                 timeout=120, sleep=time.sleep, monotonic=time.monotonic):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            health = read_local('/api/health')
            if health.get('ok') is not True or health.get('legacy_loaded') is not False or health.get('revision') != revision:
                raise UpdateError('Replacement health identity is not verified')
            if 'ready' in health and health['ready'] is not True:
                raise UpdateError('Replacement workers have not completed their first healthy cycles')
            if health.get('deployment_protocol') == 'entry-gate-v1':
                readiness = read_local('/api/deployment-readiness')
                allowed, _ = readiness_allowed(readiness, revision, hold, locked_at, utcnow(),
                                               permission_token=permission_token,
                                               require_off=hold.get('legacy_bootstrap') is True)
                if allowed:
                    return readiness
            elif legacy_off:
                snapshot = read_local('/api/snapshot')
                allowed, _ = ready_to_replace(health, snapshot, utcnow())
                if allowed and snapshot.get('revision') == revision:
                    return None
        except UpdateError:
            pass
        sleep(3)
    raise UpdateError('The replacement did not pass gated revision, fresh account and permission checks')


def refresh_installed_updater(root, release):
    source = release / 'deploy' / 'update_from_github.py'
    try:
        data = source.read_bytes()
        # The Docker test stage has already tested this release. Reject malformed
        # code before replacing the stable path used by the next timer run.
        compile(data, str(source), 'exec')
        atomic_write(root / 'config' / 'update_from_github.py', data)
    except (OSError, SyntaxError):
        raise UpdateError('The app is updated but the installed updater could not be refreshed') from None


def refresh_installed_timer(root, release):
    source = release / 'deploy' / 'pivot-update.timer'
    installed = Path.home() / '.config' / 'systemd' / 'user' / 'pivot-update.timer'
    try:
        if not installed.is_file():
            raise UpdateError('The tested app is ready but its installed update timer requires host setup')
        data = source.read_bytes()
        if len(data) > 4096 or b'Unit=pivot-update.service' not in data:
            raise UpdateError('The selected update timer is invalid')
        marker = root / 'config' / 'update-timer.json'
        verified = {'version': 'update-timer-v1', 'sha256': sha256(data).hexdigest()}
        installed_matches = installed.read_bytes() == data
        if installed_matches and read_record(marker) == verified:
            return
        if not installed_matches:
            atomic_write(installed, data)
        run(['systemctl', '--user', 'daemon-reload'])
        run(['systemctl', '--user', 'restart', 'pivot-update.timer'])
        # A matching unit file alone does not prove systemd loaded it. Commit
        # only after both commands succeed so interrupted refreshes retry.
        atomic_write(marker, (json.dumps(verified, sort_keys=True) + '\n').encode())
    except OSError:
        raise UpdateError('The tested app is ready but the update timer could not be refreshed') from None


def recover_hold(root):
    """Resolve a crash barrier before GitHub checks or the already-current shortcut."""
    path = hold_path(root)
    if not os.path.lexists(path):
        return False
    with locked(root / 'data' / 'pivot-v2' / 'deployment.lock'):
        locked_at = utcnow()
        hold = read_record(path)
        image = current_image()
        revision = IMAGE.fullmatch(image).group(1) if image and IMAGE.fullmatch(image) else None
        token = hold.get('permission_token')
        legacy_off = (hold.get('legacy_bootstrap') is True and hold.get('saved_live_enabled') is False
                      and revision == hold.get('previous_revision'))
        if (not valid_hold(hold) or revision not in (hold.get('previous_revision'), hold.get('candidate_revision'))
                or (not isinstance(token, str) or not TOKEN.fullmatch(token)) and not legacy_off):
            write_status(root, state='recovery_required', reason='An unresolved entry hold requires verified host recovery; new entries remain paused',
                         active_revision=revision, candidate_revision=hold.get('candidate_revision') if valid_hold(hold) else None)
            raise UpdateError('Unresolved entry hold could not be matched to a verified running release and saved permission')
        try:
            wait_healthy(revision, hold=hold, locked_at=locked_at,
                         permission_token=token, legacy_off=legacy_off)
            if revision == hold['candidate_revision']:
                release = release_directory(root, revision)
                refresh_installed_updater(root, release)
                refresh_installed_timer(root, release)
            clear_hold(root, hold)
        except UpdateError:
            write_status(root, state='recovery_required', reason='Entry hold remains in place because crash recovery has not passed verification',
                         active_revision=revision, candidate_revision=hold['candidate_revision'])
            raise
        write_status(root, state='updated' if revision == hold['candidate_revision'] else 'built',
                     reason='Recovered a verified release; saved owner permission is unchanged',
                     active_revision=revision,
                     candidate_revision=None if revision == hold['candidate_revision'] else hold['candidate_revision'])
        return revision == hold['candidate_revision']


def update(root, *, build_only=False):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with locked(root / 'config' / 'update.lock'):
        if not build_only and recover_hold(root):
            return
        old_status = prior_status(root)
        write_status(root, state='checking', reason='Checking GitHub for a new release',
                     active_revision=old_status.get('active_revision'), candidate_revision=old_status.get('candidate_revision'))
        revision = fetch_revision(root)
        previous = current_image()
        previous_revision = IMAGE.fullmatch(previous).group(1) if previous else None
        if revision == previous_revision and not build_only:
            health = read_local('/api/health')
            if health.get('ok') is not True or health.get('legacy_loaded') is not False or health.get('revision') != revision:
                raise UpdateError('Installed revision exists but its health check failed')
            # Legacy updaters copied the new updater but left their old timer.
            # Reconcile the exact healthy installed release without replacing
            # its container, holding entries, or changing saved permission.
            refresh_installed_timer(root, release_directory(root, revision))
            write_status(root, state='current', reason='Running the latest GitHub release', active_revision=revision,
                         candidate_revision=None)
            return
        if not build_only and rejected_revision(root) == revision:
            health = read_local('/api/health')
            if (health.get('ok') is not True or health.get('legacy_loaded') is not False
                    or health.get('revision') != previous_revision):
                raise UpdateError('The rejected release remains blocked and the current app requires attention')
            write_status(root, state='rolled_back', reason='Keeping the healthy previous release; publish a new commit to replace the failed release',
                         active_revision=previous_revision, candidate_revision=revision)
            return
        release = release_directory(root, revision)
        write_status(root, state='building', reason='Testing the new release while the current app keeps running',
                     active_revision=previous_revision, candidate_revision=revision)
        image = build_release(release, revision)
        if build_only or previous is None:
            write_status(root, state='built', reason='Tested image ready; initial host installation is required' if previous is None else 'Tested image ready; deployment was not requested',
                         active_revision=previous_revision, candidate_revision=revision)
            return
        try:
            with locked(root / 'data' / 'pivot-v2' / 'deployment.lock'):
                locked_at = utcnow()
                health = read_local('/api/health')
                if health.get('revision') != previous_revision:
                    raise UpdateError('Current container and API revisions disagree; automatic replacement is paused')
                if health.get('ok') is not True or health.get('legacy_loaded') is not False:
                    raise UpdateError('Current app health is not verified; automatic replacement is paused')
                legacy = health.get('deployment_protocol') != 'entry-gate-v1'
                if legacy:
                    snapshot = read_local('/api/snapshot')
                    if snapshot.get('revision') != previous_revision:
                        raise UpdateError('Current container and API revisions disagree; automatic replacement is paused')
                    allowed, reason = ready_to_replace(health, snapshot, utcnow())
                    if not allowed:
                        live_or_unknown = (health.get('live_enabled') is not False or snapshot.get('live_enabled') is not False
                                           or snapshot.get('review_required') is not False)
                        write_status(root, state='bootstrap_required' if live_or_unknown else 'blocked_exposure',
                                     reason='Legacy Live-On runtime requires a one-time host bootstrap for the entry gate' if live_or_unknown else reason,
                                     active_revision=previous_revision, candidate_revision=revision)
                        return
                hold = {'version': 'entry-hold-v1', 'id': uuid4().hex, 'previous_revision': previous_revision,
                        'candidate_revision': revision, 'created_at': utcnow().isoformat(),
                        'permission_token': None, 'legacy_bootstrap': legacy}
                if legacy:
                    hold['saved_live_enabled'] = False
                if os.path.lexists(hold_path(root)):
                    raise UpdateError('An entry hold already exists; crash recovery must complete first')
                save_hold(root, hold)
                if not legacy:
                    try:
                        readiness = read_local('/api/deployment-readiness')
                        allowed, reason = readiness_allowed(readiness, previous_revision, hold, locked_at, utcnow())
                    except UpdateError:
                        # Nothing has been replaced; release only our own hold.
                        clear_hold(root, hold)
                        raise
                    if not allowed:
                        clear_hold(root, hold)
                        write_status(root, state='blocked_exposure', reason=reason,
                                     active_revision=previous_revision, candidate_revision=revision)
                        return
                    hold.update(permission_token=readiness['permission_token'], saved_live_enabled=readiness['saved_live_enabled'])
                    save_hold(root, hold)
                write_status(root, state='deploying', reason='Installing a tested release with new entries held and owner permission unchanged',
                             active_revision=previous_revision, candidate_revision=revision)
                try:
                    compose_up(root, image)
                    verified = wait_healthy(revision, hold=hold, locked_at=locked_at,
                                            permission_token=hold['permission_token'])
                    if legacy:
                        # The old Off-only runtime cannot expose a permission
                        # token. The candidate must prove saved permission is
                        # still Off, then bind its token before releasing hold.
                        hold['permission_token'] = verified['permission_token']
                        save_hold(root, hold)
                except UpdateError:
                    rejection_saved = True
                    try:
                        reject_revision(root, revision)
                    except OSError:
                        rejection_saved = False
                    try:
                        compose_up(root, previous)
                        wait_healthy(previous_revision, hold=hold, locked_at=locked_at,
                                     permission_token=hold['permission_token'], legacy_off=legacy)
                    except UpdateError:
                        write_status(root, state='recovery_required', reason='Replacement and rollback verification failed; entry hold remains in place',
                                     active_revision=previous_revision, candidate_revision=revision)
                        raise UpdateError('The replacement and rollback could not be verified; entry hold is retained') from None
                    if not rejection_saved:
                        raise UpdateError('Previous release restored but failed-release record could not be saved; entry hold is retained')
                    clear_hold(root, hold)
                    write_status(root, state='rolled_back', reason='The replacement failed checks; verified previous release restored with owner permission unchanged',
                                 active_revision=previous_revision, candidate_revision=revision)
                    return
                refresh_installed_updater(root, release)
                refresh_installed_timer(root, release)
                clear_hold(root, hold)
                write_status(root, state='updated', reason='The new release is verified; saved owner permission is unchanged',
                             active_revision=revision, candidate_revision=None, deployed_at=utcnow().isoformat())
        except UpdateError as exc:
            if str(exc) == 'Another update or live-money change is in progress':
                write_status(root, state='waiting_entry', reason='Waiting for the in-flight entry admission to finish before installing',
                             active_revision=previous_revision, candidate_revision=revision)
                return
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'pivot-trader')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    try:
        update(args.root, build_only=args.build_only)
    except UpdateError as exc:
        failure = prior_status(args.root)
        failure.update(state='recovery_required' if os.path.lexists(hold_path(args.root)) else 'error', reason=str(exc))
        write_status(args.root, **failure)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
