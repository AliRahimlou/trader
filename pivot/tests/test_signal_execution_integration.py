"""Pure analyzer → in-memory broker lifecycle; never broker commissioning.

Prices and index evidence are manufactured fixtures. The fake account's live
label exercises permission checks only. These tests make no provider requests,
and cannot establish that today's market or the source videos supply a trade.
"""
from dataclasses import replace
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256

import pytest

from pivot.execution import Executor, SIGNAL_FIELDS
from pivot.policy import POLICY_VERSION
from pivot.store import Store
from pivot.strategy import analyze
from pivot.tests.test_strategy_v2 import NOW, candle, leader_market, setup_scenario
from pivot.tests.test_execution import ready, identify_event
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
    assert setup['policy_version'] == 'nasdaq-video-interpretation-v3'
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


def multiple_ready_snapshot(kind):
    """Real analyzer output from manufactured simultaneous opportunity fixtures."""
    market, leaders, vix, now = setup_scenario('short', 'four_hour_retest')
    if kind == 'methods':
        market.bars[1440] = [candle(now - timedelta(days=1), high=109.5, low=90, minutes=1440)]
        market.previous_session = '2026-09-15'
    else:
        market.bars[60] = [candle(now - timedelta(hours=2), 104, 105, 103, 104, 60),
                           candle(now - timedelta(hours=1), 104, 112, 103, 111, 60),
                           candle(now, 110.5, 112, 105, 111, 60)]
    snapshot = ready(now)
    snapshot['setup'] = analyze(market, leaders, vix, now)
    candidates = snapshot['setup']['entry_candidates']
    assert len(candidates) >= 2 and all(c['state'] == 'SETUP_READY' for c in candidates)
    assert snapshot['setup']['event_id'] == candidates[0]['event_id']
    assert len({c['event_id'] for c in candidates}) == len(candidates)
    assert (candidates[0]['strategy_id'] != candidates[1]['strategy_id']) == (kind == 'methods')
    return snapshot


def consumed_event(store, candidate):
    # A durable, already-finished attempt, without simulating another broker POST.
    key = sha256(f'{POLICY_VERSION}|QQQ|{candidate["event_id"]}'.encode()).hexdigest()[:24]
    trade = {'id': key, 'stage': 'finished'}
    assert store.reserve_trade(trade)
    store.save_trade(trade, finished=True)


def candidate_engine(tmp_path, snapshot):
    candidate = snapshot['setup']['entry_candidates'][0]
    reference = Decimal(str(candidate['entry']))
    executor, broker, store = engine(tmp_path, str(reference.quantize(Decimal('.01'))))
    broker.bid, broker.ask = str(reference), str(reference + Decimal('.01'))
    return executor, broker, store


@pytest.mark.parametrize('kind', ['methods', 'areas'])
def test_consumed_top_opportunity_cannot_starve_distinct_ready_analyzer_candidate(tmp_path, kind):
    snapshot = multiple_ready_snapshot(kind)
    original = deepcopy(snapshot)
    top, second = snapshot['setup']['entry_candidates'][:2]
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    consumed_event(store, top)
    executor.tick(snapshot)
    trade = store.active_trade()
    assert trade['signal']['event_id'] == second['event_id']
    assert len(broker.sent) == 2 and broker.sent[1]['type'] == 'stop'
    assert len(broker.confirmed) == 1
    assert snapshot == original  # Execution selection cannot rewrite analyzer evidence.
    check = store.latest_execution_check()
    assert check['signal']['event_id'] == top['event_id']
    assert check['selected_entry_signal']['event_id'] == second['event_id']
    assert check['selected_entry_signal']['strategy_id'] == second['strategy_id']
    assert check['trade']['id'] == trade['id']


@pytest.mark.parametrize('kind', ['methods', 'areas'])
def test_all_consumed_analyzer_candidates_stop_before_broker_reads(tmp_path, kind):
    snapshot = multiple_ready_snapshot(kind)
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    for candidate in snapshot['setup']['entry_candidates']:
        consumed_event(store, candidate)
    broker.account = lambda: pytest.fail('Consumed opportunities need no broker reads')
    executor.tick(snapshot)
    assert broker.sent == [] and broker.confirmed == [] and store.active_trade() is None
    assert executor.execution_check['gate'] == 'setup_deduplication'
    assert executor.execution_check['selected_entry_signal'] is None


@pytest.mark.parametrize('consume_top', [False, True])
@pytest.mark.parametrize('invalid', ['version', 'identity', 'checks', 'expiry_value', 'leader_expiry_value', 'direction', 'geometry'])
def test_every_alternative_contract_is_validated_before_any_broker_read(tmp_path, consume_top, invalid):
    snapshot = multiple_ready_snapshot('methods')
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    top, alternative = snapshot['setup']['entry_candidates']
    if consume_top:
        consumed_event(store, top)
    if invalid == 'version':
        alternative['policy_version'] = 'nasdaq-video-interpretation-v1'
    elif invalid == 'identity':
        alternative['event_id'] = 'ev2_' + 'a' * 64
    elif invalid == 'checks':
        alternative['checks'] = alternative['checks'][:-1]
    elif invalid == 'expiry_value':
        alternative['event_expires_at'] = None
    elif invalid == 'leader_expiry_value':
        alternative['leader_evidence_valid_until'] = 'unverified'
    elif invalid == 'direction':
        alternative['direction'] = None
    else:
        alternative['stop'] = float('nan')
    broker.account = lambda: pytest.fail('Invalid alternatives must stop before any broker reads')
    executor.tick(snapshot)
    assert broker.sent == [] and broker.confirmed == [] and store.active_trade() is None
    assert executor.execution_check['outcome'] == 'waiting'
    assert executor.execution_check['selected_entry_signal'] is None


def expire_candidate(candidate, field):
    if field == 'event':
        candidate.update(event_origin_at=(NOW - timedelta(minutes=180)).isoformat(),
                         event_expires_at=NOW.isoformat())
        identify_event(candidate)
    elif field == 'observation':
        candidate['leader_observation_at'] = (NOW-timedelta(seconds=390)).isoformat()
        candidate['leader_observation_valid_until'] = NOW.isoformat()
    else:
        candidate['leader_evidence_valid_until'] = NOW.isoformat()


@pytest.mark.parametrize('field', ['event', 'leader', 'observation'])
def test_expired_leading_event_cannot_hide_independently_current_candidate(tmp_path, field):
    snapshot = multiple_ready_snapshot('methods')
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    top, second = snapshot['setup']['entry_candidates']
    expire_candidate(top, field)
    snapshot['setup'].update({key: top[key] for key in SIGNAL_FIELDS})
    executor.tick(snapshot)
    assert len(broker.sent) == 2 and store.active_trade()['signal']['event_id'] == second['event_id']
    assert executor.execution_check['selected_entry_signal']['event_id'] == second['event_id']
    assert not store.trade_exists(executor._event_key(top))


def test_all_expired_candidates_stop_before_broker_reads(tmp_path):
    snapshot = multiple_ready_snapshot('methods')
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    for candidate in snapshot['setup']['entry_candidates']:
        expire_candidate(candidate, 'leader')
    broker.account = lambda: pytest.fail('Expired evidence cannot reach broker admission')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'signal_age_policy'


def test_expired_candidate_cannot_hide_malformed_geometry(tmp_path):
    snapshot = multiple_ready_snapshot('methods')
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    alternative = snapshot['setup']['entry_candidates'][1]
    expire_candidate(alternative, 'leader')
    alternative['stop'] = float('nan')
    broker.account = lambda: pytest.fail('Malformed contracts must not be skipped as expired')
    executor.tick(snapshot)
    assert not broker.sent and store.active_trade() is None
    assert executor.execution_check['gate'] == 'price_geometry'


@pytest.mark.parametrize('candidates', [None, {}, [], [None]])
def test_present_malformed_candidate_list_cannot_use_single_setup_fallback(tmp_path, candidates):
    snapshot = multiple_ready_snapshot('methods')
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    snapshot['setup']['entry_candidates'] = candidates
    executor.tick(snapshot)
    assert broker.sent == [] and broker.confirmed == [] and store.active_trade() is None
    assert executor.execution_check['outcome'] == 'waiting'


def test_selected_candidate_quote_failure_does_not_hop_to_another_opportunity(tmp_path):
    snapshot = multiple_ready_snapshot('methods')
    executor, broker, store = candidate_engine(tmp_path, snapshot)
    top, second = snapshot['setup']['entry_candidates']
    # The first short is structurally valid but its stop is inside the current
    # ask. The second opportunity has a wider stop and could pass this quote.
    snapshot['setup']['stop'] = top['stop'] = 111.005
    executor.tick(snapshot)
    assert broker.sent == [] and broker.confirmed == [] and store.active_trade() is None
    assert executor.execution_check['gate'] == 'price_geometry'
    assert executor.execution_check['selected_entry_signal']['event_id'] == top['event_id']
    consumed_event(store, top)
    executor.tick(snapshot)
    assert len(broker.sent) == 2 and store.active_trade()['signal']['event_id'] == second['event_id']
