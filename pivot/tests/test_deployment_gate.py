"""Deployment admission races use temporary files, child processes and a fake broker."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from pivot.api import Hosting, create_app
from pivot.deployment import DeploymentHold, EntryGate, PROTOCOL, _control_token
from pivot.execution import Executor
from pivot.feeds import FeedError
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, NOW, enable, ready

REVISION = 'a' * 40
CANDIDATE = 'b' * 40


def hold(gate, **changes):
    value = {'version': 'entry-hold-v1', 'id': 'c' * 32,
             'previous_revision': REVISION, 'candidate_revision': CANDIDATE,
             'created_at': NOW.isoformat(), 'permission_token': None,
             'saved_live_enabled': True, 'legacy_bootstrap': False, **changes}
    gate.hold_path.write_text(json.dumps(value))
    return value


@contextmanager
def exclusive_process(path):
    script = """import fcntl,sys
with open(sys.argv[1], 'a') as lock:
 fcntl.flock(lock, fcntl.LOCK_EX)
 print('locked',flush=True)
 sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, '-c', script, str(path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        yield
    finally:
        child.communicate('release\n', timeout=5)
        assert child.returncode == 0


def exclusive_is_blocked(path):
    script = """import fcntl,sys
with open(sys.argv[1], 'a') as lock:
 try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
 except BlockingIOError: sys.exit(0)
 sys.exit(1)
"""
    return subprocess.run([sys.executable, '-c', script, str(path)], timeout=5).returncode == 0


@pytest.fixture
def gated(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'ledger.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at, revision=REVISION)
    executor.entry_gate = EntryGate(tmp_path / 'deployment.lock')
    return executor, broker, store


def test_gate_shared_and_exclusive_admission_are_cross_process(tmp_path):
    gate = EntryGate(tmp_path / 'deployment.lock')
    assert gate.status() == {'configured': True, 'locked': False, 'hold_present': False,
                             'hold_id': None, 'hold_valid': False}
    with gate.admit():
        assert exclusive_is_blocked(gate.lock_path)
        with gate.admit():
            assert exclusive_is_blocked(gate.lock_path)
    assert not exclusive_is_blocked(gate.lock_path)
    with exclusive_process(gate.lock_path):
        assert gate.status()['locked'] is True
        with pytest.raises(DeploymentHold) as caught:
            with gate.admit():
                pytest.fail('Exclusive updater must block entry')
        assert caught.value.code == 'deployment_hold'
    with gate.admit():
        pass


def test_valid_hold_survives_updater_process_exit_and_never_changes_permission(gated):
    executor, broker, store = gated
    enable(executor)
    original = store.control(), store.settings()
    gate = executor.entry_gate
    value = hold(gate, permission_token='d' * 64)
    assert gate.status()['hold_valid'] is True and gate.status()['hold_id'] == value['id']
    executor.tick(ready())
    assert not broker.sent and store.active_trade() is None
    assert store.latest_execution_check()['gate'] == 'deployment_hold'
    assert (store.control(), store.settings()) == original
    assert gate.hold_path.exists()


@pytest.mark.parametrize('malformation', ['empty', 'bad_json', 'oversized', 'wrong_version', 'bad_id',
                                         'bad_revision', 'naive_time', 'bad_token', 'unknown_key',
                                         'duplicate_field', 'symlink', 'dangling_symlink', 'directory', 'fifo'])
def test_any_present_malformed_or_special_hold_blocks(tmp_path, malformation):
    import os
    gate = EntryGate(tmp_path / 'deployment.lock')
    if malformation in ('symlink', 'dangling_symlink'):
        target = tmp_path / 'target.json'
        if malformation == 'symlink':
            target.write_text('{}')
        gate.hold_path.symlink_to(target)
    elif malformation == 'directory':
        gate.hold_path.mkdir()
    elif malformation == 'fifo':
        os.mkfifo(gate.hold_path)
    elif malformation in ('empty', 'bad_json', 'oversized'):
        gate.hold_path.write_text({'empty': '', 'bad_json': '{', 'oversized': 'x' * 4097}[malformation])
    elif malformation == 'duplicate_field':
        value = hold(gate)
        gate.hold_path.write_text('{"id":"' + 'd' * 32 + '",' + json.dumps(value)[1:])
    else:
        changes = {'wrong_version': {'version': 'wrong'}, 'bad_id': {'id': 'secret'},
                   'bad_revision': {'candidate_revision': 'secret'}, 'naive_time': {'created_at': '2026-09-17T12:00:00'},
                   'bad_token': {'permission_token': 'secret'}, 'unknown_key': {'secret': 'private'}}
        hold(gate, **changes[malformation])
    status = gate.status()
    assert status['hold_present'] and not status['hold_valid']
    assert 'secret' not in json.dumps(status) and 'private' not in json.dumps(status)
    with pytest.raises(DeploymentHold):
        with gate.admit():
            pytest.fail('Present hold must block')


def test_symlink_lock_cannot_create_a_different_admission_domain(tmp_path):
    target = tmp_path / 'target'
    target.touch()
    path = tmp_path / 'deployment.lock'
    path.symlink_to(target)
    gate = EntryGate(path)
    assert gate.status()['error'] == 'entry_gate_unavailable'
    with pytest.raises(DeploymentHold):
        with gate.admit():
            pytest.fail('Invalid lock cannot admit')


def test_hold_blocks_new_entry_before_broker_reads_or_reservation(gated, monkeypatch):
    executor, broker, store = gated
    enable(executor)
    hold(executor.entry_gate)
    def forbidden(*args):
        pytest.fail('Entry gate must precede broker reads and reservation')
    monkeypatch.setattr(broker, 'account', forbidden)
    monkeypatch.setattr(store, 'reserve_trade', forbidden)
    executor.tick(ready())
    assert not broker.sent and store.active_trade() is None
    assert store.latest_execution_check()['gate'] == 'deployment_hold'


def test_entry_keeps_shared_admission_through_reads_claim_and_post(gated, monkeypatch):
    executor, broker, store = gated
    enable(executor)
    checked = []
    for owner, name in ((broker, 'account'), (store, 'reserve_trade'), (store, 'claim_operation'), (broker, 'submit')):
        original = getattr(owner, name)
        def inspected(*args, original=original, name=name, **kwargs):
            assert exclusive_is_blocked(executor.entry_gate.lock_path)
            checked.append(name)
            return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, inspected)
    executor.tick(ready())
    assert {'account', 'reserve_trade', 'claim_operation', 'submit'} <= set(checked)
    assert len(broker.sent) == 2 and store.active_trade()['stage'] == 'open'
    assert not exclusive_is_blocked(executor.entry_gate.lock_path)


def prepare_without_submission(executor, snapshot):
    original = executor._manage
    executor._manage = lambda trade: None
    try:
        executor.tick(snapshot)
    finally:
        executor._manage = original


def test_recovered_prepared_entry_is_blocked_but_expires_under_hold(gated):
    executor, broker, store = gated
    enable(executor)
    prepare_without_submission(executor, ready())
    assert store.active_trade()['ops']['entry']['state'] == 'prepared' and not broker.sent
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.entry_gate = EntryGate(executor.entry_gate.lock_path)
    hold(restarted.entry_gate)
    with exclusive_process(restarted.entry_gate.lock_path):
        restarted.tick(ready())
        assert not broker.sent and store.active_trade()['ops']['entry']['state'] == 'prepared'
        assert store.latest_execution_check()['gate'] == 'deployment_hold'
        broker.at += timedelta(seconds=11)
        restarted.tick(ready(broker.at))
        assert store.active_trade() is None and not broker.sent
    assert restarted.enabled() and restarted.entry_gate.hold_path.exists()


def test_direct_recovered_first_post_cannot_bypass_gate(gated):
    executor, broker, store = gated
    enable(executor)
    prepare_without_submission(executor, ready())
    hold(executor.entry_gate)
    with pytest.raises(DeploymentHold):
        executor._order(store.active_trade(), 'entry')
    assert store.active_trade()['ops']['entry']['state'] == 'prepared' and not broker.sent


def test_recovered_prepared_entry_holds_shared_admission_during_first_post(gated, monkeypatch):
    executor, broker, store = gated
    enable(executor)
    prepare_without_submission(executor, ready())
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.entry_gate = EntryGate(executor.entry_gate.lock_path)
    original = broker.submit
    checked = []
    def submit(payload):
        if payload['client_order_id'].endswith('-entry'):
            assert exclusive_is_blocked(restarted.entry_gate.lock_path)
            checked.append('entry')
        return original(payload)
    monkeypatch.setattr(broker, 'submit', submit)
    restarted.tick(ready())
    assert checked == ['entry'] and store.active_trade()['stage'] == 'open'


def test_existing_entry_lookup_protection_and_exit_continue_under_hold(gated, monkeypatch):
    executor, broker, store = gated
    enable(executor)
    broker.entry_mode = 'lost_accepted'
    executor.tick(ready())
    assert len(broker.sent) == 1 and store.active_trade()['stage'] == 'entering'
    hold(executor.entry_gate)
    with exclusive_process(executor.entry_gate.lock_path):
        executor.tick(ready())
        assert store.active_trade()['stage'] == 'open'
        assert len(broker.sent) == 2 and broker.sent[-1]['type'] == 'stop'
        broker.bid, broker.ask = '111', '111.01'
        for _ in range(5):
            executor.tick(ready())
            if store.active_trade() is None:
                break
        assert store.active_trade() is None
        assert len([order for order in broker.sent if order['client_order_id'].endswith('-entry')]) == 1
        assert len(broker.sent) == 3 and not broker.position_data
    assert executor.enabled()


def app_fixture(tmp_path):
    broker = FakeBroker()
    service = Service(None, Store(tmp_path / 'api.sqlite3'), broker)
    service.executor.now = lambda: broker.at
    app = create_app(service, background=False, hosting=Hosting(revision=REVISION),
                     deployment_lock=tmp_path / 'deployment.lock')
    return app, service, broker


def test_readiness_is_fresh_read_only_and_identifies_exclusive_hold(tmp_path):
    app, service, broker = app_fixture(tmp_path)
    enable(service.executor)
    gate = service.executor.entry_gate
    hold(gate)
    original = service.store.control(), service.store.settings(), service.store.events()
    calls = []
    for name in ('account', 'positions', 'orders'):
        original_read = getattr(broker, name)
        def record_read(original_read=original_read, name=name):
            calls.append(name)
            return original_read()
        setattr(broker, name, record_read)
    with TestClient(app) as client, exclusive_process(gate.lock_path):
        assert client.get('/api/health').json()['deployment_protocol'] == PROTOCOL
        response = client.get('/api/deployment-readiness')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        payload = response.json()
        assert payload['ok'] and payload['gate']['locked'] and payload['gate']['hold_valid']
        assert payload['read_started_at'] == payload['checked_at'] == NOW.isoformat()
        assert payload['permission_token'] == _control_token(original[0])
        assert payload['positions_count'] == payload['orders_count'] == 0
        assert payload['active_trade'] is False
        assert payload['saved_live_enabled'] and payload['live_enabled'] and not payload['review_required']
        assert payload['account_ready'] and payload['account_identity_matches']
        assert calls == ['account', 'positions', 'orders']
        assert 'fake-account-only' not in response.text and 'account_ref' not in response.text
        assert client.post('/api/deployment-readiness').status_code == 403
    assert not broker.sent and not broker.canceled
    assert original == (service.store.control(), service.store.settings(), service.store.events())


def test_snapshot_reports_current_sanitized_gate_without_broker_requests(tmp_path):
    app, service, broker = app_fixture(tmp_path)
    gate = service.executor.entry_gate
    original = service.store.control(), service.store.settings(), service.store.events()
    def forbidden(*args):
        pytest.fail('Snapshot must not call the broker')
    for name in ('account', 'positions', 'orders', 'submit', 'cancel'):
        setattr(broker, name, forbidden)
    with TestClient(app) as client:
        assert client.get('/api/snapshot').json()['deployment_gate']['hold_present'] is False
        hold(gate)
        with exclusive_process(gate.lock_path):
            response = client.get('/api/snapshot')
            shown = response.json()['deployment_gate']
            assert shown['configured'] and shown['locked'] and shown['hold_valid']
            assert str(tmp_path) not in response.text and 'permission_token' not in response.text
        shown = client.get('/api/snapshot').json()['deployment_gate']
        assert not shown['locked'] and shown['hold_present'] and shown['hold_valid']
        gate.hold_path.write_text('{"secret":"do-not-expose"}')
        response = client.get('/api/snapshot')
        shown = response.json()['deployment_gate']
        assert shown['hold_present'] and not shown['hold_valid']
        assert 'do-not-expose' not in response.text
        gate.hold_path.unlink()
        gate.lock_path.unlink()
        gate.lock_path.symlink_to(tmp_path / 'unavailable')
        shown = client.get('/api/snapshot').json()['deployment_gate']
        assert shown['configured'] and shown['locked'] and shown['error'] == 'entry_gate_unavailable'
    assert original == (service.store.control(), service.store.settings(), service.store.events())


@pytest.mark.parametrize('method,value', [('account', None), ('account', {'status': 'ACTIVE'}),
    ('positions', None), ('positions', [None]), ('orders', {}), ('orders', [{'symbol': None}])])
def test_malformed_broker_readiness_never_claims_flat(tmp_path, method, value):
    app, service, broker = app_fixture(tmp_path)
    setattr(broker, method, lambda: deepcopy(value))
    with TestClient(app) as client:
        payload = client.get('/api/deployment-readiness').json()
    assert not payload['ok'] and payload['error'] == 'broker_state_invalid'
    assert payload['positions_count'] is None and payload['orders_count'] is None
    assert payload['active_trade'] is None and not broker.sent


@pytest.mark.parametrize('case', ['failure', 'identity', 'permission_changed', 'slow', 'future', 'blocked'])
def test_readiness_rejects_failures_identity_changes_and_stale_reads(tmp_path, case):
    app, service, broker = app_fixture(tmp_path)
    enable(service.executor)
    expected = {'failure': 'broker_read_failed', 'identity': 'account_identity_mismatch',
                'permission_changed': 'permission_changed', 'slow': 'broker_reads_stale',
                'future': 'broker_reads_stale', 'blocked': 'account_not_ready'}[case]
    if case == 'failure':
        broker.account = lambda: (_ for _ in ()).throw(FeedError('secret token'))
    elif case == 'identity':
        broker.account_data['account_ref'] = 'secret-other-account'
    elif case == 'blocked':
        broker.account_data['trading_blocked'] = True
    else:
        def orders():
            if case == 'permission_changed':
                service.store.set_control(False)
            else:
                broker.at += timedelta(seconds=46 if case == 'slow' else -1)
            return []
        broker.orders = orders
    with TestClient(app) as client:
        response = client.get('/api/deployment-readiness')
        payload = response.json()
    assert not payload['ok'] and payload['error'] == expected
    assert 'secret' not in response.text and not broker.sent
    if case in ('failure', 'slow', 'future'):
        assert payload['positions_count'] is None and payload['orders_count'] is None


def test_exposure_and_review_state_are_explicit_and_protocol_requires_configuration(tmp_path):
    app, service, broker = app_fixture(tmp_path)
    broker.position_data = [{'symbol': 'QQQ', 'qty': '1'}]
    service.store.set_control(True, 'old-policy', 'fake-account-only')
    with TestClient(app) as client:
        payload = client.get('/api/deployment-readiness').json()
    assert payload['ok'] and payload['positions_count'] == 1
    assert payload['review_required'] and payload['saved_live_enabled'] and not payload['live_enabled']
    broker.position_data = []
    assert service.store.reserve_trade({'id': 'd' * 24, 'stage': 'entering'})
    with TestClient(app) as client:
        payload = client.get('/api/deployment-readiness').json()
    assert payload['ok'] and payload['positions_count'] == 0 and payload['active_trade'] is True
    plain = Service(None, Store(tmp_path / 'plain.sqlite3'), FakeBroker())
    with TestClient(create_app(plain, background=False)) as client:
        assert 'deployment_protocol' not in client.get('/api/health').json()
        assert client.get('/api/deployment-readiness').json()['gate']['configured'] is False
