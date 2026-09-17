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
from pivot.policy import POLICY_VERSION
from pivot.store import Store
from pivot.strategy import analyze
from pivot.tests.test_strategy_v2 import NOW, candle, leader_market, setup_scenario
from pivot.tests.test_execution import ready
from pivot.tests.test_insight_execution import InsightBroker


def analyzed_snapshot(direction, method='prior_day_sweep'):
    market, leaders, vix, now = setup_scenario(direction, method)
    # Use the current actual-index provider's admission branch, with explicitly
    # manufactured observations. No external entitlement or price is implied.
    vix = replace(vix, source='insightsentry', valid_until=NOW + timedelta(seconds=990))
    setup = analyze(market, leaders, vix, now)
    assert setup['strategy_id'] == method
    assert setup['state'] == 'SETUP_READY'
    assert all(check['passed'] for check in setup['checks'])
    assert setup['direction'] == direction
    assert setup['can_enter'] is False
    assert setup['policy_version'] == 'nasdaq-video-interpretation-v2'
    assert all(row['timeframe_minutes'] == 5 for row in setup['leader_evidence'].values())
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


@pytest.mark.parametrize('method', ['prior_day_sweep', 'four_hour_retest'])
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
    market, leaders, vix, now = setup_scenario('long')
    for leader in leaders.values():
        leader.bars[5] = leader.bars[5][-2:]
    snapshot = ready()
    snapshot['setup'] = analyze(market, leaders, vix, now)
    assert snapshot['setup']['state'] == 'CONFIRMING'
    executor, broker, store = engine(tmp_path, '5.00')
    executor.tick(snapshot)
    assert not broker.sent and not broker.confirmed
    assert store.active_trade() is None
    assert 'magnificent seven' in executor.message.lower()


def test_actual_later_retest_and_reversed_leaders_keep_identity_after_stopout_and_restart(tmp_path):
    market, leaders, vix, now = setup_scenario('long', 'four_hour_retest')
    # An additional genuine pre-existing daily range supplies an exit level on
    # either side. It does not create another location event at these prices.
    market = replace(market, bars={**market.bars, 1440: [candle(
        now - timedelta(days=1), high=120, low=80, minutes=1440)]}, previous_session='2026-09-15')
    original = analyze(market, leaders, vix, now)
    assert original['state'] == 'SETUP_READY' and original['direction'] == 'long'
    snapshot = ready(now)
    snapshot['setup'] = original
    executor, broker, store = engine(tmp_path, '25.00')
    reference = Decimal(str(original['entry']))
    broker.bid, broker.ask = str(reference - Decimal('.01')), str(reference)
    executor.tick(snapshot)
    assert len(broker.sent) == 2
    broker.fill(broker.sent[1]['client_order_id'])
    executor.tick(snapshot)
    assert not broker.position_data and store.active_trade() is None

    later = now + timedelta(hours=1)
    retest = replace(market.bars[60][-1], end=later)
    later_market = replace(market, bars={**market.bars, 60: market.bars[60] + [retest]}, observed_at=later)
    later_leaders = {symbol: leader_market(symbol, 'short', later) for symbol in leaders}
    inverse_vix = setup_scenario('short')[2]
    inverse_vix = replace(inverse_vix, observed_at=later, bars={15: inverse_vix.bars[15][:-2] +
        [replace(bar, end=bar.end + timedelta(hours=1)) for bar in inverse_vix.bars[15][-2:]]})
    repeated = analyze(later_market, later_leaders, inverse_vix, later)
    assert repeated['state'] == 'SETUP_READY' and repeated['direction'] == 'short'
    assert repeated['strategy_id'] == original['strategy_id'] == 'four_hour_retest'
    assert repeated['event_id'] == original['event_id']
    assert repeated['event_at'] == original['event_at']
    assert repeated['latest_evidence_at'] != original['latest_evidence_at']
    snapshot = ready(later)
    snapshot['setup'] = repeated
    broker.at = later
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(snapshot)
    assert len(broker.sent) == 2 and len(broker.confirmed) == 1
    assert 'already been handled' in restarted.message


@pytest.mark.parametrize('method', ['prior_day_sweep', 'four_hour_retest'])
@pytest.mark.parametrize('entry_mode', ['lost_accepted', 'partial'])
def test_actual_signal_fill_recovery_protects_only_owned_quantity_after_restart(tmp_path, method, entry_mode):
    snapshot = analyzed_snapshot('long', method)
    executor, broker, store = engine(tmp_path, '25.00')
    reference = Decimal(str(snapshot['setup']['entry']))
    broker.bid, broker.ask = str(reference - Decimal('.01')), str(reference)
    broker.entry_mode = entry_mode
    executor.tick(snapshot)
    assert len(broker.sent) == 1 and broker.position_data
    owned = abs(Decimal(broker.position_data[0]['qty']))
    restarted = Executor(broker, Store(store.path), now=lambda: broker.at)
    restarted.tick(snapshot)
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert Decimal(broker.sent[1]['qty']) == owned
    assert store.active_trade()['stage'] == 'open'
    assert len(broker.confirmed) == 1


@pytest.mark.parametrize('method', ['prior_day_sweep', 'four_hour_retest'])
def test_actual_ready_signal_with_stale_analysis_cannot_start_order(tmp_path, method):
    snapshot = analyzed_snapshot('long', method)
    snapshot['analysis_at'] = (NOW - timedelta(seconds=91)).isoformat()
    executor, broker, store = engine(tmp_path, '25.00')
    executor.tick(snapshot)
    assert broker.sent == [] and broker.confirmed == [] and store.active_trade() is None
    assert executor.execution_check['gate'] == 'analysis_freshness'
