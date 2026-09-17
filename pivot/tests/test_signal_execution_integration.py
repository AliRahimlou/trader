"""Pure analyzer → in-memory broker lifecycle; never broker commissioning.

Prices and index evidence are manufactured fixtures. The fake account's live
label exercises permission checks only. These tests make no provider requests,
and cannot establish that today's market or the source videos supply a trade.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from pivot.execution import Executor
from pivot.models import Market
from pivot.policy import POLICY_VERSION
from pivot.store import Store
from pivot.strategy import analyze
from pivot.tests.test_current_rules_audit import NOW, candle, confirmations, old_zones, sweep_market
from pivot.tests.test_execution import ready
from pivot.tests.test_insight_execution import InsightBroker


def break_retest_market(direction):
    # Reuse the already accepted direction-independent break/retest geometry
    # from the rule-audit witnesses, with no prior-day sweep branch available.
    if direction == 'long':
        bars = [candle(NOW - timedelta(hours=2), 92, 93, 91, 92, 60),
                candle(NOW - timedelta(hours=1), 92, 93, 88, 89, 60),
                candle(NOW, 89.5, 90, 88, 89, 60)]
    else:
        bars = [candle(NOW - timedelta(hours=2), 108, 109, 107, 108, 60),
                candle(NOW - timedelta(hours=1), 108, 112, 107, 111, 60),
                candle(NOW, 110.5, 112, 110, 111, 60)]
    return Market('QQQ', {240: old_zones(), 60: bars}, 'alpaca_iex', True, NOW)


def analyzed_snapshot(direction, method='previous-day level sweep'):
    leaders, vix = confirmations(direction)
    # Use the current actual-index provider's admission branch, with explicitly
    # manufactured observations. No external entitlement or price is implied.
    vix = replace(vix, source='insightsentry', valid_until=NOW + timedelta(seconds=990))
    market = sweep_market(direction) if method == 'previous-day level sweep' else break_retest_market(direction)
    setup = analyze(market, leaders, vix, NOW)
    assert setup['event'] == method
    assert setup['state'] == 'SETUP_READY'
    assert all(check['passed'] for check in setup['checks'])
    assert setup['direction'] == direction
    assert setup['can_enter'] is False
    snapshot = ready()
    snapshot['setup'] = setup
    return snapshot


def engine(tmp_path, amount):
    broker = InsightBroker()
    store = Store(tmp_path / 'synthetic-integration.sqlite3')
    store.save({'sizing_mode': 'target', 'target_dollars': amount})
    executor = Executor(broker, store, now=lambda: broker.at)
    executor.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
    return executor, broker, store


@pytest.mark.parametrize('method', ['previous-day level sweep', 'break and retest'])
@pytest.mark.parametrize('direction,amount,entry_side,exit_side', [
    pytest.param('long', '5.00', 'buy', 'sell', id='five-dollar-long'),
    pytest.param('long', '25.00', 'buy', 'sell', id='twenty-five-dollar-long'),
    pytest.param('short', None, 'sell', 'buy', id='supported-whole-share-short'),
])
def test_analyzed_signal_completes_exact_quantity_protected_lifecycle(
        tmp_path, method, direction, amount, entry_side, exit_side):
    snapshot = analyzed_snapshot(direction, method)
    reference = Decimal(str(snapshot['setup']['entry']))
    # The synthetic short account has both permission and enough buying power
    # for one full share; this does not assert the owner's small target can short.
    if amount is None:
        amount = str(reference.quantize(Decimal('.01')))
    executor, broker, store = engine(tmp_path, amount)
    broker.bid, broker.ask = ((str(reference - Decimal('.01')), str(reference)) if direction == 'long'
                             else (str(reference), str(reference + Decimal('.01'))))

    executor.tick(snapshot)
    assert broker.actions[0] == 'vix'
    assert len(broker.confirmed) == 1
    assert len(broker.sent) == 2
    entry, protection = broker.sent
    assert entry['type'] == 'market' and entry['side'] == entry_side
    if direction == 'long':
        assert entry['notional'] == amount and 'qty' not in entry
    else:
        assert Decimal(entry['qty']) == 1 and 'notional' not in entry
    held_quantity = abs(Decimal(broker.position_data[0]['qty']))
    assert protection['type'] == 'stop' and protection['side'] == exit_side
    assert protection['time_in_force'] == 'day'
    assert Decimal(protection['stop_price']) == Decimal(str(snapshot['setup']['stop']))
    assert Decimal(protection['qty']) == held_quantity
    assert store.active_trade()['stage'] == 'open'

    executor.set_live({'enabled': False, 'policy_version': POLICY_VERSION})
    target = Decimal(str(snapshot['setup']['target']))
    broker.bid, broker.ask = ((str(target), str(target + Decimal('.01'))) if direction == 'long'
                             else (str(target - Decimal('.01')), str(target)))
    executor.tick(snapshot)
    assert len(broker.sent) == 2  # Cancellation confirmation precedes any exit.
    assert broker.canceled == [protection['client_order_id']]
    executor.tick(snapshot)
    assert len(broker.sent) == 3
    exit_order = broker.sent[-1]
    assert exit_order['type'] == 'market' and exit_order['side'] == exit_side
    assert Decimal(exit_order['qty']) == held_quantity
    executor.tick(snapshot)
    assert broker.position_data == [] and broker.orders() == []
    assert store.active_trade() is None
    assert not executor.enabled()
    # Re-enabling cannot duplicate the already completed analyzed setup.
    executor.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
    executor.tick(snapshot)
    assert len(broker.sent) == 3
    assert len(broker.confirmed) == 1


@pytest.mark.parametrize('amount', ['5.00', '25.00'])
def test_valid_short_signal_is_not_executable_at_owner_small_dollar_target(tmp_path, amount):
    snapshot = analyzed_snapshot('short')
    executor, broker, store = engine(tmp_path, amount)
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert broker.confirmed == []  # Sizing fails before spending a VIX quote.
    assert 'No supported share quantity' in executor.message


def test_actual_analyzer_leader_failure_never_reaches_broker_submission(tmp_path):
    leaders, vix = confirmations('long')
    for market in leaders.values():
        market.bars[240] = []
    snapshot = ready()
    snapshot['setup'] = analyze(sweep_market('long'), leaders, vix, NOW)
    assert snapshot['setup']['state'] == 'CONFIRMING'
    executor, broker, store = engine(tmp_path, '5.00')
    executor.tick(snapshot)
    assert not broker.sent and not broker.confirmed
    assert store.active_trade() is None
    assert 'magnificent seven' in executor.message.lower()
