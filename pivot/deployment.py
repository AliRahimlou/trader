"""Cross-process entry admission and read-only deployment evidence.

The updater owns the exclusive lock and hold file. Trading only takes a shared
lock; a hold never changes saved permission or prevents management of exposure.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from threading import local

PROTOCOL = 'entry-gate-v1'
READINESS_MAX_SECONDS = 45


class DeploymentHold(ValueError):
    code = 'deployment_hold'

    def __init__(self):
        super().__init__('An app update is holding new entries. Existing positions continue their exits.')


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{' + str(length) + r'}', value) is not None


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('A timezone-aware clock is required')
    return value.astimezone(timezone.utc)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Repeated hold field')
        value[key] = item
    return value


class EntryGate:
    def __init__(self, lock_path, hold_path=None):
        self.lock_path = Path(lock_path)
        self.hold_path = Path(hold_path) if hold_path is not None else self.lock_path.parent / 'entry-hold.json'
        self._local = local()

    def _open_lock(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError('Deployment lock must be a regular file')
        return fd

    def _hold(self):
        result = {'hold_present': True, 'hold_id': None, 'hold_valid': False}
        try:
            fd = os.open(self.hold_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            # A dangling symlink is present even though opening its target would fail.
            if not os.path.lexists(self.hold_path):
                return {**result, 'hold_present': False}
            return result
        except OSError:
            return result
        try:
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                    return result
                raw = source.read(4097)
            if len(raw) > 4096:
                return result
            value = json.loads(raw, object_pairs_hook=_unique_object)
            required = {'version', 'id', 'previous_revision', 'candidate_revision', 'created_at'}
            optional = {'permission_token', 'saved_live_enabled', 'legacy_bootstrap'}
            if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
                return result
            if _hex(value.get('id'), 32):
                result['hold_id'] = value['id']
            created = datetime.fromisoformat(value['created_at'])
            valid = (value['version'] == 'entry-hold-v1' and _hex(value['id'], 32)
                     and _hex(value['previous_revision'], 40) and _hex(value['candidate_revision'], 40)
                     and isinstance(value['created_at'], str)
                     and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value['created_at']) is not None
                     and created.tzinfo is not None and created.utcoffset() == timedelta(0)
                     and (value.get('permission_token') is None or _hex(value['permission_token'], 64))
                     and all(key not in value or type(value[key]) is bool for key in ('saved_live_enabled', 'legacy_bootstrap')))
            result['hold_valid'] = bool(valid)
        except (OSError, ValueError, TypeError, OverflowError):
            pass
        return result

    def status(self):
        result = {'configured': True, 'locked': True, **self._hold()}
        fd = None
        try:
            fd = self._open_lock()
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return result
            result['locked'] = False
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            result['error'] = 'entry_gate_unavailable'
        finally:
            if fd is not None:
                os.close(fd)
        return result

    @contextmanager
    def admit(self):
        # Nested admission in one execution thread reuses its outer shared lock.
        # In particular it must not acquire deployment after holding entry_lock.
        if getattr(self._local, 'depth', 0):
            if self._hold()['hold_present']:
                raise DeploymentHold()
            self._local.depth += 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        fd = None
        try:
            try:
                fd = self._open_lock()
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                raise DeploymentHold() from None
            if self._hold()['hold_present']:
                raise DeploymentHold()
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0
        finally:
            if fd is not None:
                os.close(fd)


def _control_token(control):
    if (not isinstance(control, dict) or type(control.get('enabled')) is not bool
            or not (control.get('policy') is None or isinstance(control['policy'], str))
            or not (control.get('account_ref') is None or isinstance(control['account_ref'], str))):
        raise ValueError('Invalid saved permission')
    return sha256(json.dumps(control, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def deployment_readiness(executor, store, revision):
    """Fresh broker reads only. No snapshot cache, permission writes or order methods."""
    gate = getattr(executor, 'entry_gate', None) if executor is not None else None
    result = {'ok': False, 'revision': revision, 'deployment_protocol': PROTOCOL if gate else None,
              'read_started_at': None, 'checked_at': None,
              'gate': gate.status() if gate else {'configured': False, 'locked': False, 'hold_present': False, 'hold_id': None, 'hold_valid': False},
              'account_ready': None, 'account_identity_matches': None, 'positions_count': None,
              'orders_count': None, 'active_trade': None, 'permission_token': None,
              'saved_live_enabled': None, 'live_enabled': None, 'review_required': None}
    if executor is None:
        return {**result, 'error': 'execution_unavailable'}
    try:
        control = store.control()
        token = _control_token(control)
        from .policy import POLICY_VERSION
        result.update(permission_token=token, saved_live_enabled=control['enabled'],
                      live_enabled=control['enabled'] and control.get('policy') == POLICY_VERSION,
                      review_required=control['enabled'] and control.get('policy') != POLICY_VERSION)
        started = _utc(executor.now())
        result['read_started_at'] = started.isoformat()
        # Read directly after read_started_at; a prior service snapshot is not proof.
        account = executor.broker.account()
        positions = executor.broker.positions()
        orders = executor.broker.orders()
        trade = store.active_trade()
        after = store.control()
        checked = _utc(executor.now())
        result['checked_at'] = checked.isoformat()
        result['gate'] = gate.status() if gate else result['gate']
        if not 0 <= (checked - started).total_seconds() <= READINESS_MAX_SECONDS:
            return {**result, 'error': 'broker_reads_stale'}
        if (not isinstance(account, dict) or not isinstance(account.get('status'), str)
                or account.get('mode') not in ('live', 'paper')
                or any(type(account.get(key)) is not bool for key in ('trading_blocked', 'account_blocked', 'trade_suspended_by_user'))
                or not isinstance(positions, list) or not isinstance(orders, list)
                or any(not isinstance(item, dict) or not isinstance(item.get('symbol'), str) or not item['symbol'] for item in positions + orders)
                or (trade is not None and not isinstance(trade, dict))):
            return {**result, 'error': 'broker_state_invalid'}
        result.update(account_ready=account['status'] == 'ACTIVE' and account['mode'] == 'live'
                      and not any(account[key] for key in ('trading_blocked', 'account_blocked', 'trade_suspended_by_user')),
                      account_identity_matches=not control['enabled'] or bool(isinstance(account.get('account_ref'), str)
                        and account['account_ref'] and account.get('account_ref') == control.get('account_ref')),
                      positions_count=len(positions), orders_count=len(orders), active_trade=trade is not None)
        if _control_token(after) != token:
            return {**result, 'error': 'permission_changed'}
        result['ok'] = result['account_ready'] and result['account_identity_matches']
        if not result['ok']:
            result['error'] = 'account_not_ready' if not result['account_ready'] else 'account_identity_mismatch'
        return result
    except Exception:
        try:
            result['checked_at'] = _utc(executor.now()).isoformat()
        except Exception:
            pass
        return {**result, 'error': 'broker_read_failed'}
