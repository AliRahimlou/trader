from pivot.execution import Executor
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker, ready, enable


def test_original_exit_trigger_survives_completion_and_restart(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / 'audit.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    executor.tick(ready())
    trade = store.active_trade()
    assert trade
    executor._start_exit(trade, 'Target reached')
    executor._start_exit(trade, 'Waiting for a confirmed exit')
    executor._finish(trade, 'Position closed and broker state reconciled')
    with Store(store.path).connect() as db:
        import json
        saved = json.loads(db.execute('SELECT body FROM trades WHERE id=?', (trade['id'],)).fetchone()[0])
    assert saved['reason'] == 'Position closed and broker state reconciled'
    assert saved['exit_reason'] == 'Target reached'
    events = [row for row in store.events() if row['kind'] == 'exit_started']
    assert all(row['detail']['trade_id'] == trade['id'] for row in events)
