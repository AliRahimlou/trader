"""Explicit broker-paper workflow commissioning; not a strategy or return test.

Default: read-only broker preflight. --run-paper permits one synthetic QQQ long
entry of at most $5, protective-stop verification, and cancellation-safe flattening
through the production Executor. No live trading destination is accepted.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_UP
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from dotenv import dotenv_values

from pivot.execution import (CHECKS, Executor, WORKING_PROTECTION, PAPER_SIGNAL_POLICY_VERSION,
                             PAPER_SIGNAL_PURPOSE, checked_quote, closing, session_open)
from pivot.paper_broker import PAPER_HOST, PaperAlpacaBroker
from pivot.policy import POLICY_VERSION
from pivot.sizing import decimal, purchase_plan
from pivot.store import Store

SCHEMA = 'paper-commission-v1'


def paper_credentials(path):
    """Explicit private file and explicit paper names; never read the live .env."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16384:
            raise ValueError('Paper credentials must be a small regular file')
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError('Paper credentials must be owned by this user and readable only by this user')
        values = dotenv_values(stream=source, interpolate=False)
    if values.get('PAPER_APCA_API_BASE_URL', PAPER_HOST) != PAPER_HOST:
        raise ValueError('Paper commissioning refuses any other broker destination')
    key, secret = values.get('PAPER_APCA_API_KEY_ID'), values.get('PAPER_APCA_API_SECRET_KEY')
    if not key or not secret:
        raise ValueError('PAPER_APCA_API_KEY_ID and PAPER_APCA_API_SECRET_KEY are required; live key names are not accepted')
    return key, secret


def write_report(path, report):
    fd, temporary = tempfile.mkstemp(prefix='.paper-report-', dir=Path(path).parent)
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump(report, target, indent=2, sort_keys=True, allow_nan=False)
            target.write('\n')
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def paper_store(directory):
    """Separate named directory, identifying marker, database and process lock."""
    directory = Path(directory).absolute()
    if directory.name != 'paper-commission' or directory.is_symlink():
        raise ValueError('Use a separate directory named paper-commission, never the production runtime')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = directory / 'environment.json'
    expected = {'schema': SCHEMA, 'account_mode': 'paper', 'broker_host': PAPER_HOST}
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != expected:
            raise ValueError('The existing directory is not an identified paper commissioning runtime')
    else:
        if any(directory.iterdir()):
            raise ValueError('The new paper commissioning directory must be empty')
        write_report(marker, expected)
    database, lock_path = directory / 'paper.sqlite3', directory / 'worker.lock'
    if database.is_symlink():
        raise ValueError('The paper ledger cannot be a symbolic link')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('The paper process lock must be a regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another paper commissioning process owns this runtime') from None
        yield Store(database)
    finally:
        os.close(fd)


def synthetic_snapshot(quote, now):
    """Manufactured, explicitly labeled admission fixture; no VIX claim or strategy validation.

    Fields satisfy the production lifecycle contract only. They must never be
    reused for trading research, stored as real source evidence, or sent to a live
    executor. The command enforces the fixed paper adapter/environment boundary.
    """
    bid, ask = checked_quote(quote, now)
    stop = (bid * decimal('.98')).quantize(decimal('.01'), rounding=ROUND_DOWN)
    target = (ask * decimal('1.02')).quantize(decimal('.01'), rounding=ROUND_UP)
    zone = {'low': float(bid), 'high': float(ask), 'source': 'synthetic commissioning fixture',
            'established_at': (now - timedelta(days=1)).isoformat()}
    identity = [PAPER_SIGNAL_POLICY_VERSION, PAPER_SIGNAL_PURPOSE,
                'QQQ', 'prior_day_sweep', float(zone['low']).hex(), float(zone['high']).hex(),
                zone['established_at'], now.isoformat()]
    setup = {'state': 'SETUP_READY', 'checks': [{'name': name, 'passed': True} for name in sorted(CHECKS)],
             'policy_version': PAPER_SIGNAL_POLICY_VERSION, 'commissioning_purpose': PAPER_SIGNAL_PURPOSE,
             'strategy_id': 'prior_day_sweep',
             'event_zone': zone, 'event_id': 'paper_ev1_' + sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest(),
             'event_origin_at': now.isoformat(), 'event_at': now.isoformat(),
             'event_expires_at': (now + timedelta(minutes=180)).isoformat(), 'latest_evidence_at': now.isoformat(),
             'leader_evidence_valid_until': (now + timedelta(minutes=15)).isoformat(),
             'leader_observation_at': now.isoformat(), 'leader_observations_synchronized': True,
             'leader_observation_valid_until': (now + timedelta(seconds=390)).isoformat(),
             'direction': 'long', 'entry': float(ask), 'stop': float(stop), 'target': float(target)}
    return {'commissioning_only': True, 'synthetic_evidence': True, 'analysis_at': now.isoformat(),
            'data_valid_until': (now + timedelta(seconds=90)).isoformat(), 'feeds': {'vix': 'current'},
            'data_errors': [], 'setup': setup}


def preflight(broker, store, now, amount='5.00'):
    if not isinstance(broker, PaperAlpacaBroker):
        raise ValueError('Commissioning requires the fixed paper-only adapter')
    broker.assert_paper_only()
    amount = decimal(amount)
    if not 1 <= amount <= 5 or amount != amount.quantize(decimal('.01')):
        raise ValueError('Paper commissioning amount must be between $1 and $5 with at most two decimals')
    account, positions, orders, clock = broker.account(), broker.positions(), broker.orders(), broker.clock()
    Executor._account_ready(account)
    if account.get('mode') != 'paper' or not account.get('account_ref'):
        raise ValueError('The paper account identity could not be verified')
    active = store.active_trade()
    if active:
        if active.get('execution_mode') != 'paper' or active.get('account_ref') != account['account_ref']:
            raise ValueError('The saved trade belongs to a different execution environment or account')
        ours = {op['payload']['client_order_id'] for op in active['ops'].values()}
        if any(p['symbol'] != 'QQQ' for p in positions) or any(o.get('client_order_id') not in ours for o in orders):
            raise ValueError('Foreign paper exposure must be resolved outside this commissioning workflow')
    elif positions or orders:
        raise ValueError('The paper account must have no positions or working orders before a new test')
    at = now()
    ready = session_open(clock, at) and (active is not None or not closing(clock, at, 600))
    quote = None
    if ready and not active:
        asset, quote = broker.asset('QQQ'), broker.quote('QQQ')
        if asset.get('symbol') != 'QQQ' or asset.get('status') != 'active' or asset.get('tradable') is not True or asset.get('fractionable') is not True:
            raise ValueError('Paper QQQ must be tradable and fractionable')
        _, ask = checked_quote(quote, now())
        purchase_plan(amount, ask, account['buying_power'])
    return {'schema': SCHEMA, 'account_mode': 'paper', 'broker_host': PAPER_HOST,
            'ready': ready, 'resuming_owned_trade': bool(active), 'target_dollars': str(amount.quantize(decimal('.01'))),
            'reason': 'Paper workflow is ready' if ready else 'Waiting for regular market hours outside the final entry cutoff',
            'strategy_validated': False, 'profitability_validated': False, 'synthetic_signal': True,
            'vix_verified': False}, quote


def commission(broker, store, *, run_paper=False, amount='5.00', timeout_seconds=120,
               now=None, monotonic=time.monotonic, sleep=time.sleep):
    """Bounded paper-only workflow. Uncertain outcomes remain in the ledger for recovery."""
    if type(run_paper) is not bool or not 1 <= timeout_seconds <= 180:
        raise ValueError('Paper workflow requires an explicit flag and a timeout of at most 180 seconds')
    now = now or (lambda: datetime.now(timezone.utc))
    report, quote = preflight(broker, store, now, amount)
    report.update(checked_at=now().isoformat(), orders_authorized=run_paper,
                  status='preflight_only', entry_fill_verified=False, protection_verified=False,
                  flat_verified=False, exit_fill_verified=False)
    if not run_paper or not report['ready']:
        return report
    executor = Executor(broker, store, now=now, expected_account_mode='paper')
    active = store.active_trade()
    deadline = monotonic() + timeout_seconds
    latest = active
    try:
        if not active:
            store.save({'sizing_mode': 'target', 'target_dollars': report['target_dollars']})
            executor.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
            executor.tick(synthetic_snapshot(quote, now()))
            latest = executor._diagnostic_trade
        # No further entry is permitted, including after restart/reconciliation.
        executor.set_live({'enabled': False, 'policy_version': POLICY_VERSION})
        while monotonic() < deadline:
            active = store.active_trade()
            if active is None:
                break
            latest = active
            entry = active.get('ops', {}).get('entry', {}).get('last_seen') or {}
            report['entry_fill_verified'] |= decimal(entry.get('filled_qty') or '0') > 0
            stop = active.get('ops', {}).get('stop', {}).get('last_seen') or {}
            report['protection_verified'] |= stop.get('status') in WORKING_PROTECTION
            if active['stage'] == 'open' and stop.get('status') in WORKING_PROTECTION:
                executor._start_exit(active, 'Paper commissioning workflow complete; closing only its owned shares')
            executor.tick({})
            latest = executor._diagnostic_trade or latest
            if store.active_trade() is None:
                break
            sleep(min(2, max(0, deadline - monotonic())))
        active = store.active_trade()
        if latest:
            report['operations'] = {name: {'state': op.get('state'),
                                          'broker_status': (op.get('last_seen') or {}).get('status'),
                                          'filled_qty': (op.get('last_seen') or {}).get('filled_qty')}
                                    for name, op in latest.get('ops', {}).items()}
            report['exit_fill_verified'] = any(name != 'entry' and decimal((op.get('last_seen') or {}).get('filled_qty') or '0') > 0
                                              for name, op in latest.get('ops', {}).items())
        report['flat_verified'] = active is None and not broker.positions() and not broker.orders()
        passed = all(report[k] for k in ('entry_fill_verified', 'protection_verified', 'exit_fill_verified', 'flat_verified'))
        report.update(status='workflow_verified' if passed else 'incomplete', finished_at=now().isoformat(),
                      active_stage=active.get('stage') if active else None,
                      message=executor.message,
                      next_action=None if passed else 'Inspect the paper account and saved ledger; rerun with the same directory to reconcile. No unknown order will be blindly retried.')
        return report
    finally:
        executor.set_live({'enabled': False, 'policy_version': POLICY_VERSION})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paper-env', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--run-paper', action='store_true', help='Authorize the bounded synthetic paper workflow; never real orders')
    parser.add_argument('--amount', default='5.00')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args(argv)
    try:
        key, secret = paper_credentials(args.paper_env)
        broker = PaperAlpacaBroker(key, secret)
        with paper_store(args.state_dir) as store:
            report = commission(broker, store, run_paper=args.run_paper, amount=args.amount, timeout_seconds=args.timeout)
            write_report(args.state_dir / 'commission-report.json', report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report['ready'] and report['status'] in ('preflight_only', 'workflow_verified') else 2
    except Exception:
        # Do not print provider response bodies, credentials, or arbitrary exceptions.
        print(json.dumps({'schema': SCHEMA, 'status': 'failed', 'account_mode': 'paper',
                          'message': 'Paper commissioning could not complete. Check the explicit private paper credentials, market session, and isolated ledger. No live destination is permitted.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
