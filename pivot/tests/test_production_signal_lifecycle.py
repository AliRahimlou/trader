"""Production-rule analyzer to fake broker lifecycle; no provider or live account.

The candle fixture and service freshness envelope are manufactured. This proves
the current analyzer contract can reach execution under the production rules,
including recovery, and makes no claim about market opportunities or returns.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from pivot import feature_flags
from pivot.execution import Executor, SIGNAL_POLICY_VERSION
from pivot.policy import POLICY_VERSION
from pivot.store import Store
from pivot.strategy import ANALYSIS_VERSION, analyze
from pivot.tests.test_insight_execution import InsightBroker, proof
from pivot.tests.test_sweep_session_lifetime import confirmation, scenario


pytestmark = pytest.mark.rules46


@pytest.mark.parametrize('entry_mode', ['filled', 'lost_accepted'])
def test_carried_sweep_trades_and_recovers_under_production_rules(tmp_path, entry_mode):
    _, market = scenario()
    now = market.observed_at
    leaders, vix = confirmation(now)
    vix = replace(vix, source='insightsentry', valid_until=now + timedelta(seconds=990))
    setup = analyze(market, leaders, vix, now)
    assert feature_flags.socrates_rules()['version'] == '4.6'
    assert SIGNAL_POLICY_VERSION == ANALYSIS_VERSION == setup['policy_version']
    assert setup['state'] == 'SETUP_READY' and setup['direction'] == 'long'
    snapshot = {'analysis_at': now.isoformat(), 'data_valid_until': (now + timedelta(seconds=90)).isoformat(),
                'feeds': {'vix': 'current'}, 'data_errors': [], 'setup': setup}
    broker = InsightBroker()
    broker.at, broker.proof, broker.entry_mode = now, proof(now), entry_mode
    entry = Decimal(str(setup['entry']))
    broker.bid, broker.ask = str(entry - Decimal('.01')), str(entry)
    store = Store(tmp_path / 'production-lifecycle.db')
    store.save({'sizing_mode': 'target', 'target_dollars': '15.00'})
    executor = Executor(broker, store, now=lambda: broker.at)
    executor.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
    executor.tick(snapshot)
    if entry_mode == 'lost_accepted':
        assert len(broker.sent) == 1 and broker.position_data
        executor = Executor(broker, Store(store.path), now=lambda: broker.at)
        executor.tick(snapshot)
    assert broker.actions[0] == 'vix' and len(broker.confirmed) == 1
    assert [(order['symbol'], order['side'], order['type']) for order in broker.sent] == [
        ('QQQ', 'buy', 'market'), ('QQQ', 'sell', 'stop')]
    assert broker.sent[0]['notional'] == '15.00'
    quantity = broker.position_data[0]['qty']
    assert broker.sent[1]['qty'] == quantity and broker.sent[1]['time_in_force'] == 'day'
    trade = store.active_trade()
    assert trade['stage'] == 'open' and trade['signal']['event_id'] == setup['event_id']
    assert trade['target'] == '102.00' and trade['target_source'] == 'previous-day high'
    target = Decimal(trade['target'])
    broker.bid, broker.ask = str(target), str(target + Decimal('.01'))
    executor.tick(snapshot)
    assert len(broker.sent) == 2 and broker.canceled == [broker.sent[1]['client_order_id']]
    executor.tick(snapshot)
    assert len(broker.sent) == 3 and broker.sent[-1]['side'] == 'sell'
    assert broker.sent[-1]['qty'] == quantity
    executor.tick(snapshot)
    assert store.active_trade() is None and broker.position_data == [] and broker.orders() == []
    executor = Executor(broker, Store(store.path), now=lambda: broker.at)
    executor.tick(snapshot)
    assert len(broker.sent) == 3 and len(broker.confirmed) == 1
    assert 'already been handled' in executor.message
