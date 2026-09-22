"""4.5.1 Socrates-only focus: crypto is paused by default, never deleted or rewritten.

No credentials, network or real orders. Saved permission rows are compared
byte-for-byte so the updater's fingerprint cannot change because of the pause.
"""
from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pivot import feature_flags
from pivot.api import create_app
from pivot.crypto_execution import POLICY_VERSION as CRYPTO_POLICY
from pivot.deployment import _deployment_permission
from pivot.feeds import ReadOnlyFeeds
from pivot.portfolio import Portfolio
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_crypto_execution import engine, tick  # noqa: F401 (fixture)
from pivot.tests.test_execution import FakeBroker as StockBroker, enable, ready

HEADERS = {'origin': 'http://testserver', 'x-pivot-intent': 'settings'}
BROKER_METHODS = ('account', 'positions', 'orders', 'clock', 'asset', 'quote', 'lookup', 'submit', 'cancel')


def pause(monkeypatch):
    monkeypatch.delenv(feature_flags.CRYPTO_PAUSE_ENV, raising=False)


def record_calls(broker):
    calls = []
    for name in BROKER_METHODS:
        original = getattr(broker, name, None)
        if original is None:
            continue
        def counted(*args, _original=original, _name=name, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)
        setattr(broker, name, counted)
    return calls


def saved_rows(main, crypto):
    return deepcopy((main.control(), main.strategy_selection(), crypto.control(), main.deployment_permission()))


@pytest.mark.parametrize('value, paused, source', [
    (None, True, 'code_default'), ('0', False, 'environment'), ('false', False, 'environment'),
    (' FALSE ', False, 'environment'), ('1', True, 'environment'), ('true', True, 'environment'),
    ('maybe', True, 'code_default'), ('', True, 'code_default')])
def test_flag_defaults_to_paused_and_only_explicit_values_change_it(monkeypatch, value, paused, source):
    if value is None:
        monkeypatch.delenv('PIVOT_CRYPTO_PAUSED', raising=False)
    else:
        monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', value)
    assert feature_flags.CRYPTO_PAUSED_DEFAULT is True
    assert feature_flags.crypto_paused() is paused and feature_flags.pause_source() == source
    row = feature_flags.strategy_pause()['range_reversal']
    assert row['paused'] is paused and row['source'] == source and isinstance(row['reason'], str)
    assert (row['reason'] == 'Socrates-only focus') is paused


def test_server_copies_the_private_env_value_into_the_process_environment(monkeypatch, tmp_path):
    from pivot import server
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', 'placeholder')
    monkeypatch.delenv('PIVOT_CRYPTO_PAUSED')  # Recorded, so the value main() sets is undone afterwards.
    monkeypatch.setattr(server, 'dotenv_values', lambda path: {'PIVOT_CRYPTO_PAUSED': '0'})
    class Stop(Exception):
        pass
    def stop(*args, **kwargs):
        raise Stop
    monkeypatch.setattr(server.Hosting, 'from_values', stop)
    monkeypatch.setattr('sys.argv', ['pivot'])
    with pytest.raises(Stop):
        server.main()
    assert feature_flags.crypto_paused() is False and feature_flags.pause_source() == 'environment'


def test_paused_executor_with_no_trade_makes_no_broker_call_and_changes_no_permission(engine, monkeypatch):
    e, b, crypto, main, portfolio = engine
    pause(monkeypatch)
    calls = record_calls(b)
    before = saved_rows(main, crypto)
    allowance = main.session_entry_allowance(e.now())
    for _ in range(3):
        tick(e)  # A current, valid long signal is present on every tick.
    assert calls == [] and b.sent == [] and crypto.active_trades() == []
    assert saved_rows(main, crypto) == before and crypto.control()['enabled'] is True
    assert main.session_entry_allowance(e.now())['families'] == allowance['families']
    assert not e.enabled() and not portfolio.family_enabled('range_reversal')
    view = e.snapshot()
    assert view['paused'] is True and view['live_enabled'] is False
    assert view['message'] == ('Crypto is paused: Socrates-only focus. '
                               'Existing crypto positions, if any, keep their exits.')
    review = crypto.decision_review(e.now())
    assert review['latest_by_symbol']['BTC/USD']['outcome'] == 'paused'
    assert review['checks_recorded'] == 1, 'an unchanged paused check is recorded once, not every tick'


def test_pause_during_entry_checks_sends_nothing(engine, monkeypatch):
    e, b, crypto, main, _ = engine
    original = b.quote
    def pause_while_checking(symbol):
        pause(monkeypatch)
        return original(symbol)
    b.quote = pause_while_checking
    tick(e)
    assert b.sent == [] and crypto.active_trades() == []
    assert 'paused' in e.snapshot()['message']


def test_paused_executor_still_manages_an_existing_trade_to_its_exit(engine, monkeypatch):
    e, b, crypto, main, _ = engine
    tick(e)  # Opened before this release (or with the override), with native protection.
    assert len(b.sent) == 2 and crypto.active_trade('BTC/USD')['stage'] == 'open'
    pause(monkeypatch)
    before = saved_rows(main, crypto)
    calls = record_calls(b)
    tick(e)
    assert calls, 'an active crypto trade keeps its reconciliation reads'
    assert len(b.sent) == 2 and crypto.active_trade('BTC/USD')['stage'] == 'open'
    # The pause note never hides the status of a position that is still being managed.
    e.message = 'Managing BTC/USD; stop working.'
    assert e.snapshot()['message'] == ('Crypto is paused: Socrates-only focus. Existing crypto positions, '
                                       'if any, keep their exits. Managing BTC/USD; stop working.')
    b.bid = b.ask = D('110')
    tick(e)
    assert [o['side'] for o in b.sent] == ['buy', 'sell', 'sell'] and b.sent[-1]['type'] == 'market'
    assert b.holdings['BTC/USD'] == 0 and crypto.active_trades() == []
    assert crypto.history()[0]['stage'] == 'finished'
    assert saved_rows(main, crypto) == before
    # Flat again: the next paused tick is broker-silent and plans nothing.
    calls.clear()
    b.bid, b.ask = D('94.99'), D('95')
    tick(e)
    assert calls == [] and len(b.sent) == 3


def test_paused_executor_still_protects_via_native_stop_fill(engine, monkeypatch):
    e, b, crypto, _, _ = engine
    tick(e)
    pause(monkeypatch)
    b.fill(b.sent[1]['client_order_id'])
    tick(e)
    assert len(b.sent) == 2 and not crypto.active_trades()


def test_environment_override_re_enables_the_engine(engine, monkeypatch):
    e, b, crypto, _, portfolio = engine
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', 'false')
    tick(e)
    assert [o['side'] for o in b.sent] == ['buy', 'sell'] and portfolio.family_enabled('range_reversal')
    assert e.snapshot()['paused'] is False


def test_portfolio_treats_paused_crypto_as_off_and_socrates_as_selected(tmp_path, monkeypatch):
    pause(monkeypatch)
    portfolio = Portfolio(Store(tmp_path / 'state.db'))
    portfolio.enabled_predicate = lambda family: True
    assert portfolio.family_enabled('socrates') is True
    assert portfolio.family_enabled('range_reversal') is False
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')
    assert portfolio.family_enabled('range_reversal') is True
    def broken():
        raise RuntimeError('flag unreadable')
    portfolio.crypto_paused = broken
    assert portfolio.family_enabled('range_reversal') is False, 'an unreadable flag fails closed'


def test_socrates_still_enters_with_crypto_paused_and_crypto_selected(tmp_path, monkeypatch):
    pause(monkeypatch)
    broker = StockBroker()
    service = Service(None, Store(tmp_path / 'state.db'), broker)
    service.executor.now = lambda: broker.at
    service.crypto_store.configure({'enabled': True, 'policy': CRYPTO_POLICY,
                                    'account_ref': broker.account()['account_ref']})
    enable(service.executor)
    assert service.portfolio.family_enabled('socrates') and not service.portfolio.family_enabled('range_reversal')
    service.executor.tick(ready())
    assert broker.sent and broker.sent[0]['symbol'] == 'QQQ' and broker.sent[0]['side'] == 'buy'


class FakeThread:
    def __init__(self, **kwargs):
        self.name, self.started = kwargs['name'], False
    def start(self): self.started = True
    def is_alive(self): return self.started
    def join(self, timeout): pass


def test_paused_service_starts_no_crypto_observer_and_reports_ready_workers(tmp_path, monkeypatch):
    from pivot.broker import AlpacaBroker
    pause(monkeypatch)
    monkeypatch.setattr('pivot.service.Thread', FakeThread)
    started = []
    monkeypatch.setattr('pivot.range_watch.RangeWatch.start', lambda self, event: started.append(self.symbol))
    feeds = ReadOnlyFeeds({})
    app = Service(feeds, Store(tmp_path / 'state.db'), broker=AlpacaBroker(feeds))
    assert app.crypto_observers_paused is True
    assert app.range_watch is None and app.extra_range_watches == {} and app.range_watch_error is None
    assert app.crypto_executor is not None, 'management-only crypto worker remains available'
    app.start()
    names = {thread.name for thread in app.threads}
    assert {'pivot-account', 'pivot-data', 'pivot-execution', 'pivot-crypto_execution'} <= names
    assert not any(name.startswith('pivot-range') for name in names) and started == []
    registered = {row['name'] for row in app.worker_health()['workers']}
    assert registered == {'account', 'data', 'execution', 'crypto_execution'}
    for name in registered:
        app.workers.begin(name)
        app.workers.finish(name)
    assert app.worker_health()['ready'] is True
    app.stop()


def test_unpaused_service_still_builds_observers(tmp_path, monkeypatch):
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')
    app = Service(ReadOnlyFeeds({}), Store(tmp_path / 'state.db'))
    assert app.crypto_observers_paused is False and app.range_watch is not None


def test_paused_crypto_execution_tick_uses_local_analyses_only(tmp_path, monkeypatch):
    pause(monkeypatch)
    app = Service(ReadOnlyFeeds({}), Store(tmp_path / 'state.db'))
    received = []
    app.crypto_executor = SimpleNamespace(tick=lambda analyses: received.append(analyses))
    app.refresh_crypto_execution()
    assert received and received[0]['BTC/USD']['state'] == 'DATA_WAITING'


def paused_snapshot_service(tmp_path):
    broker = StockBroker()
    service = Service(None, Store(tmp_path / 'state.db'), broker)
    service.state['setup'] = {'state': 'WAITING', 'checks': []}
    return service, broker


def test_snapshot_reports_the_pause_contract(tmp_path, monkeypatch):
    pause(monkeypatch)
    service, _ = paused_snapshot_service(tmp_path)
    result = service.snapshot()
    assert result['strategy_pause'] == {'range_reversal': {'paused': True, 'reason': 'Socrates-only focus',
                                                           'source': 'code_default'}}
    assert result['portfolio']['range_reversal']['paused'] is True
    assert result['crypto_execution']['paused'] is True
    assert result['crypto_execution']['message'] == ('Crypto is paused: Socrates-only focus. '
                                                     'Existing crypto positions, if any, keep their exits.')
    assert result['strategy_families']['range_reversal'] == {
        'family_id': 'range_reversal', 'label': '4H Range Reversal', 'state': 'PAUSED',
        'detail': 'Paused: Socrates-only focus.'}
    assert result['strategy_families']['socrates']['analysis'] == {'state': 'WAITING', 'checks': []}


def test_snapshot_reports_executor_pause_with_a_real_crypto_executor(engine, monkeypatch, tmp_path):
    e, _, _, _, _ = engine
    pause(monkeypatch)
    service, _ = paused_snapshot_service(tmp_path)
    service.crypto_executor = e
    result = service.snapshot()
    assert result['crypto_execution']['paused'] is True and result['crypto_execution']['live_enabled'] is False
    assert result['crypto_execution']['message'].startswith('Crypto is paused')


def test_snapshot_keeps_a_managed_crypto_position_status_while_paused(tmp_path, monkeypatch):
    pause(monkeypatch)
    service, _ = paused_snapshot_service(tmp_path)
    note = 'Crypto is paused: Socrates-only focus. Existing crypto positions, if any, keep their exits.'
    managed = {'message': note + ' Managing BTC/USD; stop working.', 'at': None, 'trades': [{'id': 't1'}], 'incidents': []}
    service.crypto_executor = SimpleNamespace(snapshot=lambda: deepcopy(managed))
    assert service.snapshot()['crypto_execution']['message'] == managed['message']
    stale = {'message': 'Fixture ready', 'at': None, 'trades': [], 'incidents': []}
    service.crypto_executor = SimpleNamespace(snapshot=lambda: deepcopy(stale))
    assert service.snapshot()['crypto_execution']['message'] == note


def test_snapshot_with_override_keeps_the_full_crypto_family(tmp_path, monkeypatch):
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')
    service, _ = paused_snapshot_service(tmp_path)
    result = service.snapshot()
    assert result['strategy_pause']['range_reversal'] == {
        'paused': False, 'reason': 'Crypto is available in this release.', 'source': 'environment'}
    assert result['portfolio']['range_reversal']['paused'] is False
    assert result['crypto_execution']['paused'] is False
    family = result['strategy_families']['range_reversal']
    assert family['state'] == 'DATA_WAITING' and 'analyses' in family


def api_service(tmp_path):
    main = StockBroker()
    service = Service(None, Store(tmp_path / 'state.db'), main)
    account = {**main.account(), 'crypto_status': 'ACTIVE', 'non_marginable_buying_power': '100'}
    reads = []
    def crypto_account():
        reads.append('account')
        return deepcopy(account)
    service.crypto_executor = SimpleNamespace(broker=SimpleNamespace(account=crypto_account), entry_gate=None,
        snapshot=lambda: {'message': 'Fixture ready', 'at': None, 'trades': [], 'incidents': []})
    return service, main, reads


@pytest.mark.parametrize('payload', [
    {'range_reversal': {'enabled': True, 'target_dollars': '5.00', 'symbols': ['BTC/USD'], 'policy_version': CRYPTO_POLICY}},
    {'range_reversal': {'enabled': False, 'target_dollars': '6.00'}},
    {'range_reversal': {'target_dollars': '6.00'}},
    {'socrates': {'enabled': True}, 'range_reversal': {'enabled': True, 'policy_version': CRYPTO_POLICY}}])
def test_strategies_endpoint_refuses_crypto_changes_while_paused(tmp_path, monkeypatch, payload):
    pause(monkeypatch)
    service, broker, reads = api_service(tmp_path)
    service.crypto_store.configure({'enabled': True, 'policy': CRYPTO_POLICY, 'account_ref': 'fake-account-only'})
    service.save_strategies({'socrates': {'enabled': False}})
    before = saved_rows(service.store, service.crypto_store)
    events = service.store.events()
    with TestClient(create_app(service, background=False)) as client:
        response = client.put('/api/strategies', json=payload, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()['detail'] == 'Crypto is paused in this release; Socrates-only focus'
    assert saved_rows(service.store, service.crypto_store) == before
    assert service.store.events() == events and reads == [] and broker.sent == []


def test_socrates_changes_through_the_same_endpoint_still_work_while_paused(tmp_path, monkeypatch):
    pause(monkeypatch)
    service, broker, reads = api_service(tmp_path)
    crypto_before = service.crypto_store.control()
    with TestClient(create_app(service, background=False)) as client:
        off = client.put('/api/strategies', json={'socrates': {'enabled': False}}, headers=HEADERS)
        assert off.status_code == 200 and off.json()['portfolio']['socrates']['enabled'] is False
        on = client.put('/api/strategies', json={'socrates': {'enabled': True}}, headers=HEADERS)
    assert on.status_code == 200
    body = on.json()
    assert body['portfolio']['socrates']['enabled'] is True and body['strategy_pause']['range_reversal']['paused']
    assert service.store.strategy_selection() == {'socrates': True}
    assert service.crypto_store.control() == crypto_before and reads == [] and broker.sent == []


def test_crypto_changes_through_the_endpoint_work_again_with_the_override(tmp_path, monkeypatch):
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')
    service, _, _ = api_service(tmp_path)
    with TestClient(create_app(service, background=False)) as client:
        response = client.put('/api/strategies', json={'range_reversal': {
            'enabled': True, 'target_dollars': '5.00', 'symbols': ['BTC/USD'], 'policy_version': CRYPTO_POLICY}},
            headers=HEADERS)
    assert response.status_code == 200 and response.json()['portfolio']['range_reversal']['enabled'] is True


def test_pause_does_not_change_deployment_permission_or_readiness(tmp_path, monkeypatch):
    broker = StockBroker()
    service = Service(None, Store(tmp_path / 'api.sqlite3'), broker)
    service.executor.now = lambda: broker.at
    service.crypto_store.configure({'enabled': True, 'policy': CRYPTO_POLICY, 'account_ref': 'fake-account-only'})
    enable(service.executor)
    app = create_app(service, background=False, deployment_lock=tmp_path / 'deployment.lock')
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')
    unpaused_token = _deployment_permission(service.store)[1]
    with TestClient(app) as client:
        unpaused = client.get('/api/deployment-readiness').json()
        pause(monkeypatch)
        paused = client.get('/api/deployment-readiness').json()
        client.get('/api/snapshot')
        assert client.put('/api/strategies', json={'range_reversal': {'target_dollars': '6.00'}},
                          headers=HEADERS).status_code == 409
        after = client.get('/api/deployment-readiness').json()
    assert _deployment_permission(service.store)[1] == unpaused_token
    for payload in (paused, after):
        assert payload == unpaused
    assert paused['ok'] is True and paused['permission_token'] == unpaused_token
    assert paused['saved_live_enabled'] is True and paused['live_enabled'] is True


def test_turning_crypto_off_is_allowed_while_paused(tmp_path, monkeypatch):
    """Off only removes risk, so it is the one crypto change accepted while paused."""
    pause(monkeypatch)
    service, broker, reads = api_service(tmp_path)
    service.crypto_store.configure({'enabled': True, 'policy': CRYPTO_POLICY, 'account_ref': 'fake-account-only'})
    with TestClient(create_app(service, background=False)) as client:
        response = client.put('/api/strategies', json={'range_reversal': {'enabled': False}}, headers=HEADERS)
    assert response.status_code == 200
    assert service.crypto_store.control()['enabled'] is False
    assert broker.sent == []
