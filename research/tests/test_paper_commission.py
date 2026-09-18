"""Paper adapter/CLI tests use an in-memory HTTP transport; sockets stay denied."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import pytest

from pivot.broker import AlpacaBroker
from pivot.execution import Executor, PAPER_SIGNAL_POLICY_VERSION, PAPER_SIGNAL_PURPOSE, signal_expiry, Waiting
from pivot.feeds import FeedError, ReadOnlyFeeds
from pivot.paper_broker import PAPER_HOST, PaperAlpacaBroker
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, enable, ready
from research.paper_commission import commission, paper_credentials, paper_store, synthetic_snapshot


class Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    def json(self):
        return deepcopy(self.body)


class PaperTransport:
    """Exercise production adapter serialization without making any request."""
    def __init__(self):
        self.exchange = FakeBroker()
        self.calls = []
    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)
    def request(self, method, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        assert url.startswith(PAPER_HOST + '/') or (method == 'GET' and url.startswith('https://data.alpaca.markets/'))
        self.calls.append((method, url))
        exchange = self.exchange
        path = '/' + url.split('/', 3)[3]
        if path == '/v2/account':
            return Response({**exchange.account(), 'id': 'paper-account-test-only'})
        if path == '/v2/positions':
            return Response(exchange.positions())
        if path == '/v2/clock':
            return Response(exchange.clock())
        if path == '/v2/assets/QQQ':
            return Response(exchange.asset('QQQ'))
        if path == '/v2/stocks/QQQ/quotes/latest':
            return Response({'symbol': 'QQQ', 'quote': exchange.quote('QQQ')})
        if path == '/v2/orders:by_client_order_id':
            order = exchange.lookup(kwargs['params']['client_order_id'])
            return Response(order, 200 if order else 404)
        if path == '/v2/orders':
            if method == 'GET':
                return Response(exchange.orders())
            return Response(exchange.submit(kwargs['json']))
        if path.startswith('/v2/orders/') and method == 'DELETE':
            exchange.cancel(path.removeprefix('/v2/orders/'))
            return Response(None, 204)
        raise AssertionError('Unexpected fake request')


@pytest.fixture
def paper(tmp_path):
    transport = PaperTransport()
    broker = PaperAlpacaBroker('paper-key-test-only', 'paper-secret-test-only', session=transport)
    store = Store(tmp_path/'paper.sqlite3')
    return broker, store, transport


def timing(transport):
    started = transport.exchange.at
    def sleep(seconds):
        transport.exchange.at += timedelta(seconds=seconds)
    return dict(now=lambda: transport.exchange.at,
                monotonic=lambda: (transport.exchange.at - started).total_seconds(), sleep=sleep)


def test_default_preflight_makes_only_read_requests_without_permission_or_order_changes(paper):
    broker, store, transport = paper
    before = store.control(), store.settings()
    report = commission(broker, store, **timing(transport))
    assert report['ready'] and report['status'] == 'preflight_only'
    assert report['account_mode'] == 'paper' and report['orders_authorized'] is False
    assert report['synthetic_signal'] and not report['strategy_validated'] and not report['vix_verified']
    assert before == (store.control(), store.settings())
    assert all(method == 'GET' for method, _ in transport.calls)


def test_explicit_paper_workflow_verifies_fractional_stop_and_cancel_safe_flatten(paper):
    broker, store, transport = paper
    report = commission(broker, store, run_paper=True, **timing(transport))
    assert report['status'] == 'workflow_verified'
    assert all(report[k] for k in ('entry_fill_verified', 'protection_verified', 'exit_fill_verified', 'flat_verified'))
    assert not report['profitability_validated']
    sent = transport.exchange.sent
    assert len(sent) == 3
    assert sent[0]['notional'] == '5.00' and sent[0]['side'] == 'buy'
    assert sent[1]['type'] == 'stop' and sent[1]['qty'] == '0.05'
    assert sent[2]['type'] == 'market' and sent[2]['side'] == 'sell' and sent[2]['qty'] == '0.05'
    assert not store.control()['enabled'] and store.active_trade() is None
    assert not transport.exchange.position_data
    assert 'paper-secret-test-only' not in json.dumps(report) and 'account_ref' not in json.dumps(report)


@pytest.mark.parametrize('amount', ['0', '5.01', '100', 'NaN', '1.001'])
def test_amount_ceiling_is_enforced_before_any_broker_request(paper, amount):
    broker, store, transport = paper
    with pytest.raises(ValueError):
        commission(broker, store, run_paper=True, amount=amount, **timing(transport))
    assert not transport.calls


@pytest.mark.parametrize('operation', ['account', 'positions', 'orders', 'clock', 'asset', 'quote', 'lookup', 'submit', 'cancel'])
def test_mutated_live_destination_is_rejected_before_any_request(paper, operation):
    broker, store, transport = paper
    broker.feeds.broker_url = 'https://api.alpaca.markets'
    args = () if operation in ('account', 'positions', 'orders', 'clock') else ({'symbol': 'QQQ'},) if operation == 'submit' else ('QQQ',)
    with pytest.raises(ValueError, match='destination'):
        getattr(broker, operation)(*args)
    assert not transport.calls


def test_paper_mode_rejects_an_ordinary_broker_and_default_executor_rejects_paper_account(paper):
    broker, store, transport = paper
    ordinary = AlpacaBroker(ReadOnlyFeeds({'APCA_API_BASE_URL': PAPER_HOST}, session=transport), session=transport)
    with pytest.raises(ValueError, match='fixed paper-only'):
        Executor(ordinary, store, expected_account_mode='paper')
    with pytest.raises(ValueError, match='not a live'):
        enable(Executor(broker, store))
    assert not transport.exchange.sent


def test_synthetic_commissioning_snapshot_never_admits_live_executor(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path/'isolated-fake.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    executor.tick(synthetic_snapshot(broker.quote('QQQ'), broker.at))
    assert not broker.sent and store.active_trade() is None
    assert 'cannot authorize a live entry' in executor.message


@pytest.mark.parametrize('placement', ['stripped_envelope', 'nested_candidate'])
def test_durable_synthetic_contract_is_rejected_by_live_even_without_envelope(tmp_path, placement):
    broker = FakeBroker()
    store = Store(tmp_path/'isolated-fake.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    synthetic = synthetic_snapshot(broker.quote('QQQ'), broker.at)
    synthetic.pop('commissioning_only')
    synthetic.pop('synthetic_evidence')
    with pytest.raises(Waiting):
        signal_expiry(synthetic['setup'], broker.at)
    if placement == 'nested_candidate':
        snapshot = ready()
        snapshot['setup']['entry_candidates'] = [deepcopy(snapshot['setup']), synthetic['setup']]
    else:
        snapshot = synthetic
    broker.account = lambda: pytest.fail('Synthetic live admission reached broker preflight')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'


@pytest.mark.parametrize('marker', ['commissioning_only', 'synthetic_evidence', 'commissioning_purpose'])
def test_live_nested_candidate_rejects_synthetic_purpose_even_with_live_policy(tmp_path, marker):
    broker = FakeBroker()
    store = Store(tmp_path/'isolated-fake.sqlite3')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    snapshot = ready()
    candidate = deepcopy(snapshot['setup'])
    candidate[marker] = PAPER_SIGNAL_PURPOSE if marker == 'commissioning_purpose' else True
    snapshot['setup']['entry_candidates'] = [candidate]
    broker.account = lambda: pytest.fail('Marked candidate reached broker preflight')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None


@pytest.mark.parametrize('change', ['missing_purpose', 'live_policy', 'live_event_id'])
def test_paper_mode_requires_its_distinct_durable_contract(paper, change):
    broker, store, transport = paper
    executor = Executor(broker, store, now=lambda: transport.exchange.at, expected_account_mode='paper')
    enable(executor)
    snapshot = synthetic_snapshot(broker.quote('QQQ'), transport.exchange.at)
    if change == 'missing_purpose':
        snapshot['setup'].pop('commissioning_purpose')
    elif change == 'live_policy':
        snapshot['setup']['policy_version'] = 'nasdaq-video-interpretation-v3'
    else:
        snapshot['setup']['event_id'] = snapshot['setup']['event_id'].replace('paper_ev1_', 'ev2_')
    executor.tick(snapshot)
    assert not transport.exchange.sent and store.active_trade() is None


def test_paper_executor_rejects_ordinary_strategy_evidence(paper):
    broker, store, transport = paper
    executor = Executor(broker, store, now=lambda: transport.exchange.at, expected_account_mode='paper')
    enable(executor)
    executor.tick(ready())
    assert not transport.exchange.sent
    assert 'explicitly labeled' in executor.message


@pytest.mark.parametrize('kind', ['position', 'order', 'foreign_runtime'])
def test_foreign_exposure_or_ledger_is_never_mutated(paper, kind):
    broker, store, transport = paper
    if kind == 'position':
        transport.exchange.position_data = [{'symbol': 'QQQ', 'qty': '1'}]
    elif kind == 'order':
        transport.exchange.book['foreign'] = {'symbol': 'QQQ', 'status': 'new', 'client_order_id': 'foreign'}
    else:
        assert store.reserve_trade({'id': 'foreign', 'stage': 'open', 'execution_mode': 'live', 'account_ref': 'other-account'})
    before = store.control(), deepcopy(transport.exchange.book), deepcopy(transport.exchange.position_data)
    with pytest.raises(ValueError):
        commission(broker, store, run_paper=True, **timing(transport))
    assert before == (store.control(), transport.exchange.book, transport.exchange.position_data)
    assert all(method == 'GET' for method, _ in transport.calls)


def test_closed_session_cannot_send_paper_order(paper):
    broker, store, transport = paper
    transport.exchange.open = False
    report = commission(broker, store, run_paper=True, **timing(transport))
    assert not report['ready'] and not transport.exchange.sent
    assert not store.control()['enabled']


def test_lost_paper_response_resumes_same_ledger_without_duplicate_entry(paper):
    broker, store, transport = paper
    transport.exchange.entry_mode = 'lost_accepted'
    report = commission(broker, store, run_paper=True, **timing(transport))
    assert report['status'] == 'workflow_verified'
    assert len([order for order in transport.exchange.sent if order['client_order_id'].endswith('-entry')]) == 1


def test_unknown_paper_response_remains_incomplete_and_durable_across_reruns(paper):
    broker, store, transport = paper
    transport.exchange.entry_mode = 'lost_unseen'
    report = commission(broker, store, run_paper=True, timeout_seconds=3, **timing(transport))
    assert report['status'] == 'incomplete' and report['active_stage'] == 'entering'
    assert len(transport.exchange.sent) == 1 and not store.control()['enabled']
    report = commission(broker, Store(store.path), run_paper=True, timeout_seconds=3, **timing(transport))
    assert report['status'] == 'incomplete' and len(transport.exchange.sent) == 1
    assert not store.control()['enabled']


def test_process_restart_protects_and_exits_only_owned_paper_trade(paper):
    broker, store, transport = paper
    executor = Executor(broker, store, now=lambda: transport.exchange.at, expected_account_mode='paper')
    store.save({'sizing_mode': 'target', 'target_dollars': '5.00'})
    enable(executor)
    executor.tick(synthetic_snapshot(broker.quote('QQQ'), transport.exchange.at))
    assert store.active_trade()['execution_mode'] == 'paper'
    assert store.active_trade()['signal']['policy_version'] == PAPER_SIGNAL_POLICY_VERSION
    assert store.active_trade()['signal']['commissioning_purpose'] == PAPER_SIGNAL_PURPOSE
    report = commission(broker, Store(store.path), run_paper=True, **timing(transport))
    assert report['resuming_owned_trade'] and report['status'] == 'workflow_verified'
    assert len(transport.exchange.sent) == 3 and not store.control()['enabled']


def test_credentials_require_separate_names_private_permissions_and_fixed_host(tmp_path):
    path = tmp_path/'explicit-paper.env'
    path.write_text('APCA_API_KEY_ID=live-key\nAPCA_API_SECRET_KEY=live-secret\n')
    path.chmod(0o600)
    with pytest.raises(ValueError, match='live key names'):
        paper_credentials(path)
    path.write_text('PAPER_APCA_API_KEY_ID=paper-key\nPAPER_APCA_API_SECRET_KEY=paper-secret\n')
    assert paper_credentials(path) == ('paper-key', 'paper-secret')
    path.chmod(0o644)
    with pytest.raises(ValueError, match='only by this user'):
        paper_credentials(path)
    path.chmod(0o600)
    path.write_text(path.read_text() + 'PAPER_APCA_API_BASE_URL=https://api.alpaca.markets\n')
    with pytest.raises(ValueError, match='destination'):
        paper_credentials(path)


def test_paper_store_cannot_select_production_or_unknown_existing_directory(tmp_path):
    with pytest.raises(ValueError, match='separate directory'):
        with paper_store(tmp_path/'pivot-v2'):
            pass
    directory = tmp_path/'paper-commission'
    directory.mkdir()
    (directory/'audit.sqlite3').write_text('unrelated ledger')
    with pytest.raises(ValueError, match='empty'):
        with paper_store(directory):
            pass
    assert (directory/'audit.sqlite3').read_text() == 'unrelated ledger'


def test_paper_store_has_durable_mode_marker_and_exclusive_process_lock(tmp_path):
    directory = tmp_path/'paper-commission'
    with paper_store(directory) as store:
        assert Path(store.path).name == 'paper.sqlite3'
        with pytest.raises(ValueError, match='Another paper'):
            with paper_store(directory):
                pass
    with paper_store(directory) as store:
        assert not store.control()['enabled']
    assert json.loads((directory/'environment.json').read_text())['account_mode'] == 'paper'
