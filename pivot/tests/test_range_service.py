"""New observational family stays outside Socrates state and execution."""
from copy import deepcopy
from types import SimpleNamespace
import pytest

from pivot.feeds import ReadOnlyFeeds
from pivot.service import Service
from pivot.store import Store


@pytest.fixture(autouse=True)
def crypto_engine_enabled(monkeypatch):
    """These tests exercise the crypto engine as it runs when re-enabled (PIVOT_CRYPTO_PAUSED=0).

    4.5.1 pauses crypto by default; test_crypto_pause.py covers the paused release.
    """
    monkeypatch.setenv('PIVOT_CRYPTO_PAUSED', '0')


def test_snapshot_observer_failure_keeps_account_and_permission_available(tmp_path):
    app = Service(None, Store(tmp_path/'state.db'))
    app.state['account'] = {'equity':'100.00'}
    app.state['setup'] = {'state':'WAITING', 'checks':[]}
    before = deepcopy(app.state)
    permission = app.store.control()
    def broken():
        raise RuntimeError('SECRET provider details')
    app.range_watch = SimpleNamespace(snapshot=broken)
    result = app.snapshot()
    family = result['strategy_families']['range_reversal']
    assert family['state'] == 'DATA_WAITING' and family['signal_ready'] is False
    assert 'temporarily unavailable' in family['detail']
    assert 'SECRET' not in str(result)
    assert result['account'] == before['account'] and result['setup'] == before['setup']
    assert app.state == before and app.store.control() == permission


def test_observations_never_enter_executor_snapshot(tmp_path):
    app = Service(None, Store(tmp_path/'state.db'))
    app.state['setup'] = {'state':'WAITING', 'checks':[]}
    received = []
    app.executor = SimpleNamespace(tick=lambda value:received.append(value), snapshot=lambda:{})
    app.range_watch = SimpleNamespace(snapshot=lambda:{'state':'SETUP_OBSERVED','signal_ready':True})
    result = app.snapshot()
    assert result['strategy_families']['range_reversal']['state'] == 'SETUP_OBSERVED'
    app.refresh_execution()
    assert 'strategy_families' not in received[0]
    assert received[0]['setup']['state'] == 'WAITING'


def test_optional_observer_startup_failure_cannot_abort_core_workers(tmp_path, monkeypatch):
    class FakeThread:
        def __init__(self, **kwargs):
            self.name, self.started = kwargs['name'], False
        def start(self): self.started = True
        def is_alive(self): return self.started
        def join(self, timeout): pass
    monkeypatch.setattr('pivot.service.Thread', FakeThread)
    app = Service(None, Store(tmp_path/'state.db'))
    def broken(_): raise RuntimeError('SECRET startup failure')
    app.range_watch = SimpleNamespace(start=broken, thread=None)
    app.start()
    assert len(app.threads) == 3 and all(thread.started for thread in app.threads)
    result = app.snapshot()
    assert 'could not be started' in result['strategy_families']['range_reversal']['detail']
    assert 'SECRET' not in str(result)
    app.stop()


def test_observer_constructor_failure_is_reported_without_affecting_core(tmp_path, monkeypatch):
    def broken(*args, **kwargs): raise RuntimeError('SECRET constructor failure')
    monkeypatch.setattr('pivot.range_watch.RangeWatch', broken)
    app = Service(ReadOnlyFeeds({}), Store(tmp_path/'state.db'))
    result = app.snapshot()
    assert 'could not be initialized' in result['strategy_families']['range_reversal']['detail']
    assert 'SECRET' not in str(result)
    assert not app.state['data_errors']


def test_reader_uses_separate_session_and_no_broker_interface(tmp_path):
    feeds = ReadOnlyFeeds({})
    app = Service(feeds, Store(tmp_path/'state.db'))
    assert app.range_watch is not None
    assert app.range_watch.reader.session is not feeds.session
    assert app.range_watch.reader.headers == feeds.alpaca_headers
    assert app.range_watch.reader.headers is not feeds.alpaca_headers
    assert not hasattr(app.range_watch, 'broker')


def test_one_extra_market_constructor_failure_does_not_disable_other_markets(tmp_path, monkeypatch):
    from pivot.range_watch import RangeWatch
    from pivot.crypto_markets import SYMBOLS
    def constructor(reader, path):
        if reader.symbol == 'SOL/USD':
            raise RuntimeError('PRIVATE provider failure')
        return RangeWatch(reader, path)
    monkeypatch.setattr('pivot.range_watch.RangeWatch', constructor)
    app = Service(ReadOnlyFeeds({}), Store(tmp_path/'state.db'))
    assert app.range_watch is not None and app.range_watch_error is None
    assert set(app.extra_range_watches) == set(SYMBOLS) - {'BTC/USD', 'SOL/USD'}
    snapshot = app.range_analyses()
    assert set(snapshot) == set(SYMBOLS)
    assert 'SOL/USD data worker could not be initialized' in snapshot['SOL/USD']['detail']
    assert 'could not be initialized' not in snapshot['BTC/USD']['detail']
    assert 'PRIVATE' not in str(snapshot)
    assert app.crypto_store.control()['symbols'] == ['BTC/USD']
