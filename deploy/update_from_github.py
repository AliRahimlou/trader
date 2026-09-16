#!/usr/bin/env python3
"""Outbound-only, tested releases for one owner-controlled Pivot installation.

This process never submits orders or changes live-money permission. Installation
of the first container and migration of its private runtime are deliberate setup
steps. Later releases wait until Live money is Off and the fresh account is flat.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
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

REPOSITORY = 'https://github.com/AliRahimlou/trader.git'
SHA = re.compile(r'[a-f0-9]{40}\Z')
IMAGE = re.compile(r'pivot-video:([a-f0-9]{40})\Z')
CONTAINER = 'pivot-video'
STATUS_KEYS = {'state', 'reason', 'active_revision', 'candidate_revision', 'checked_at', 'deployed_at'}


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
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_status(root, **values):
    # No account snapshots, URLs with credentials, subprocess output or secrets.
    status = {key: value for key, value in values.items() if key in STATUS_KEYS}
    status['checked_at'] = utcnow().isoformat()
    atomic_write(root / 'data' / 'pivot-v2' / 'deployment-status.json',
                 (json.dumps(status, indent=2, sort_keys=True) + '\n').encode())
    print(f"Pivot update: {status.get('state', 'unknown')} — {status.get('reason', '')}", flush=True)


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
        data = json.loads(raw)
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
    connection = http.client.HTTPConnection('127.0.0.1', 18011, timeout=5)
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
    """Fail closed on missing fields, stale account state or any trading activity."""
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


def wait_healthy(revision, *, timeout=120, sleep=time.sleep, monotonic=time.monotonic):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        try:
            health = read_local('/api/health')
            snapshot = read_local('/api/snapshot')
            allowed, _ = ready_to_replace(health, snapshot, utcnow())
            if allowed and health.get('revision') == revision and snapshot.get('revision') == revision:
                return
        except UpdateError:
            pass
        sleep(3)
    raise UpdateError('The replacement did not pass revision, account and live-Off health checks')


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


def update(root, *, build_only=False):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with locked(root / 'config' / 'update.lock'):
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
        with locked(root / 'data' / 'pivot-v2' / 'deployment.lock'):
            health, snapshot = read_local('/api/health'), read_local('/api/snapshot')
            allowed, reason = ready_to_replace(health, snapshot, utcnow())
            if not allowed:
                write_status(root, state='waiting_off' if health.get('live_enabled') is not False or snapshot.get('live_enabled') is not False or snapshot.get('review_required') is not False else 'blocked_exposure', reason=reason, active_revision=previous_revision, candidate_revision=revision)
                return
            if health.get('revision') != previous_revision or snapshot.get('revision') != previous_revision:
                raise UpdateError('Current container and API revisions disagree; automatic replacement is paused')
            write_status(root, state='deploying', reason='Installing a tested release with Live money Off',
                         active_revision=previous_revision, candidate_revision=revision)
            try:
                compose_up(root, image)
                wait_healthy(revision)
            except UpdateError:
                rejection_saved = True
                try:
                    reject_revision(root, revision)
                except OSError:
                    # Still restore the previous runtime if the disk is full.
                    rejection_saved = False
                try:
                    compose_up(root, previous)
                    wait_healthy(previous_revision)
                except UpdateError:
                    write_status(root, state='error', reason='The replacement and rollback health checks failed; host attention is required',
                                 active_revision=previous_revision, candidate_revision=revision)
                    raise UpdateError('The replacement and rollback could not be verified') from None
                write_status(root, state='rolled_back', reason='The new release failed health checks; the previous release is running',
                             active_revision=previous_revision, candidate_revision=revision)
                if not rejection_saved:
                    raise UpdateError('The previous release is restored but the failed-release record could not be saved')
                return
            refresh_installed_updater(root, release)
            write_status(root, state='updated', reason='The new GitHub release is healthy; Live money remains Off',
                         active_revision=revision, candidate_revision=None, deployed_at=utcnow().isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home() / 'pivot-trader')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    try:
        update(args.root, build_only=args.build_only)
    except UpdateError as exc:
        failure = prior_status(args.root)
        failure.update(state='error', reason=str(exc))
        write_status(args.root, **failure)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
