"""Owner readiness checklist for Socrates orders (4.5.1): read-only, plain language.

Fake brokers and invented state only; no network, no runtime databases, no orders.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from pivot.deployment import EntryGate
from pivot.models import MAG7
from pivot.policy import POLICY_VERSION
from pivot.readiness import ITEM_IDS, build_socrates_readiness
from pivot.service import ASSET_REFRESH_SECONDS, Service
from pivot.store import Store
from pivot.tests.test_execution import FakeBroker

NOW = datetime(2026, 9, 22, 15, tzinfo=timezone.utc)  # 11:00 New York, regular session.
GATE_NAMES = ('signal_', 'live_permission', 'deployment_hold', 'existing_exposure', 'entry_reservation',
              'Magnificent Seven', 'Premarked levels', 'Nasdaq level event', 'SETUP_READY')


def healthy():
    """Every checklist input in its best state, as the service would assemble it."""
    frames = lambda minutes: [{'minutes': m, 'label': label, 'status': 'current', 'required': True}
                              for m, label in minutes]
    instruments = [{'symbol': 'QQQ', 'status': 'current',
                    'frames': frames([(15, '15-minute'), (60, '1-hour'), (240, '4-hour'), (1440, 'Daily')])}]
    instruments += [{'symbol': symbol, 'status': 'current', 'frames': frames([(5, '5-minute')])} for symbol in MAG7]
    asset = lambda symbol: {'checked_at': NOW.isoformat(), 'error': None,
                            'asset': {'symbol': symbol, 'status': 'active', 'tradable': True, 'fractionable': True}}
    return {
        'execution_available': True,
        'control': {'enabled': True, 'policy': POLICY_VERSION, 'account_ref': 'acct'},
        'policy_version': POLICY_VERSION, 'socrates_selected': True, 'pause_reason': None,
        'account': {'account_ref': 'acct', 'mode': 'live', 'status': 'ACTIVE', 'buying_power': '92.10',
                    'trading_blocked': False, 'account_blocked': False, 'trade_suspended_by_user': False},
        'account_at': NOW.isoformat(), 'account_error': None,
        'target_dollars': '15.00', 'available_dollars': '92.10',
        'assets': {'QQQ': asset('QQQ'), 'PSQ': asset('PSQ')},
        'clock': {'timestamp': NOW.isoformat(), 'is_open': True,
                  'next_close': (NOW + timedelta(hours=5)).isoformat(),
                  'next_open': (NOW + timedelta(hours=22, minutes=30)).isoformat()},
        'data_health': {'stocks': {'status': 'current', 'instruments': instruments},
                        'vix': {'status': 'current', 'verification': {'budget': {'remaining': 400, 'limit': 900}}}},
        'entry_allowance': {'status': 'available', 'limit': 2,
                            'families': {'socrates': {'used': 0, 'remaining': 2},
                                         'range_reversal': {'used': 0, 'remaining': 2}}},
        'deployment_gate': {'configured': True, 'locked': False, 'hold_present': False},
        'exposure': {'state': 'clear'},
        'worker_health': {'workers': [{'name': name, 'status': 'running'}
                                      for name in ('account', 'data', 'execution', 'crypto_execution')]},
        'setup': {'state': 'SETUP_READY', 'direction': 'short', 'strategy_id': 'four_hour_retest',
                  'checks': [{'name': 'Stop and target', 'passed': True}]},
    }


def build(**changes):
    inputs = healthy()
    inputs.update(changes)
    return build_socrates_readiness(inputs, NOW)


def item(result, identifier):
    return next(row for row in result['items'] if row['id'] == identifier)


def test_contract_order_shape_and_ready_only_when_everything_is_ok():
    result = build()
    assert [row['id'] for row in result['items']] == list(ITEM_IDS) == [
        'live_permission', 'strategy_enabled', 'account', 'buying_power', 'instrument_qqq', 'instrument_psq',
        'market_session', 'data_qqq', 'data_leaders', 'data_vix', 'entry_allowance', 'deployment', 'exposure',
        'workers', 'setup']
    assert all(set(row) == {'id', 'label', 'status', 'detail', 'needs_owner'} for row in result['items'])
    assert all(row['status'] == 'ok' and row['needs_owner'] is False for row in result['items'])
    assert result['status'] == 'ready' and result['next_action'] is None and result['action'] is None
    assert set(result) == {'checked_at', 'status', 'headline', 'next_action', 'action', 'items'}
    assert datetime.fromisoformat(result['checked_at']) == NOW
    assert 'short (bought as PSQ)' in item(result, 'setup')['detail']
    assert '$92.10 is free' in item(result, 'buying_power')['detail'] and '$15.00' in item(result, 'buying_power')['detail']
    assert '400 of 900' in item(result, 'data_vix')['detail']
    assert item(result, 'market_session')['detail'] == 'The market is open until 4:00 PM ET.'


def test_wording_is_plain_language_without_internal_gate_names():
    for changes in ({}, {'control': {'enabled': False}}, {'socrates_selected': False},
                    {'setup': {'state': 'CONFIRMING', 'checks': [{'name': 'Magnificent Seven at their zones', 'passed': False}]}}):
        result = build(**changes)
        words = json.dumps([result['headline'], result['next_action'],
                            [(row['label'], row['detail']) for row in result['items']]])
        assert not any(name in words for name in GATE_NAMES)


@pytest.mark.parametrize('changes,identifier,action,control', [
    ({'control': {'enabled': True, 'policy': 'nasdaq-qqq-execution-v6-reward-risk-vix-persistence', 'account_ref': 'acct'}},
     'live_permission', 'Accept the updated Socrates rules: click Live money in the header, read, tick, confirm.', 'live'),
    ({'control': {'enabled': False, 'policy': None, 'account_ref': None}}, 'live_permission',
     'Turn Live money On in the header: read the rules, tick the box and confirm.', 'live'),
    ({'execution_available': False}, 'live_permission',
     'Ask for the Alpaca live trading connection to be configured on AllSpark.', None),
    ({'socrates_selected': False}, 'strategy_enabled', 'Turn Socrates back On in its card.', 'socrates'),
    ({'available_dollars': '9.99'}, 'buying_power', 'Deposit funds or lower the purchase size.', None),
    ({'account': {**healthy()['account'], 'trading_blocked': True}}, 'account', 'Check the account status in the Alpaca dashboard.', None),
    ({'exposure': {'state': 'blocked', 'symbols': ['PSQ']}}, 'exposure',
     'In Alpaca, close or cancel any position or order Pivot did not place.', None),
    ({'worker_health': {'workers': [{'name': 'execution', 'status': 'stalled'}, {'name': 'data', 'status': 'running'},
                                    {'name': 'account', 'status': 'running'}]}}, 'workers', 'Restart Pivot on AllSpark.', None),
])
def test_each_failure_blocks_with_a_plain_next_action(changes, identifier, action, control):
    result = build(**changes)
    assert item(result, identifier)['status'] == 'fail' and item(result, identifier)['needs_owner'] is True
    assert result['status'] == 'blocked' and result['next_action'] == action and result['action'] == control
    # Labels keep their own case in the headline.
    assert f'“{item(result, identifier)["label"]}”' in result['headline']


def test_first_failure_in_contract_order_sets_the_next_action():
    result = build(available_dollars='1.00', control={'enabled': True, 'policy': 'old', 'account_ref': 'acct'})
    assert [row['id'] for row in result['items'] if row['status'] == 'fail'] == ['live_permission', 'buying_power']
    assert result['next_action'].startswith('Accept the updated Socrates rules') and result['action'] == 'live'


def test_socrates_off_comes_before_the_live_review_that_lists_its_rules():
    # The Live money dialog lists the enabled strategies' rules, so it cannot come first.
    result = build(socrates_selected=False, available_dollars='1.00',
                   control={'enabled': True, 'policy': 'old', 'account_ref': 'acct'})
    assert [row['id'] for row in result['items'] if row['status'] == 'fail'] == [
        'live_permission', 'strategy_enabled', 'buying_power']
    assert result['next_action'] == ('Turn Socrates back On in its card, '
                                      'then accept the updated Socrates rules from Live money in the header.')
    assert result['action'] == 'socrates' and '“Socrates switched on”' in result['headline']
    off = build(socrates_selected=False, control={'enabled': False, 'policy': None, 'account_ref': None})
    assert off['next_action'] == 'Turn Socrates back On in its card, then turn Live money On in the header.'
    assert off['action'] == 'socrates'
    unconfigured = build(socrates_selected=False, execution_available=False)
    assert unconfigured['next_action'].startswith('Ask for the Alpaca live trading connection') and unconfigured['action'] is None


def test_app_pause_reason_is_shown_to_the_owner():
    result = build(socrates_selected=False, pause_reason='The broker rejected a PSQ entry order (HTTP 422).')
    assert item(result, 'strategy_enabled')['detail'] == (
        'Socrates is Off. Pivot paused it: The broker rejected a PSQ entry order (HTTP 422).')
    # After an app pause Alpaca is reviewed first, and no one-click button is offered.
    assert result['next_action'] == ('Check the QQQ and PSQ positions and orders in Alpaca first, '
                                     'then turn Socrates back On in its card.')
    assert result['action'] is None
    with_review = build(socrates_selected=False, pause_reason='Exit unconfirmed. Review Alpaca before enabling Socrates again.',
                        control={'enabled': True, 'policy': 'old', 'account_ref': 'acct'})
    assert with_review['next_action'].startswith('Check the QQQ and PSQ positions and orders in Alpaca first')
    assert with_review['next_action'].endswith('then accept the updated Socrates rules from Live money in the header.')
    assert with_review['action'] is None


def test_market_closed_and_final_half_hour_wait_without_blocking():
    closed = build(clock={**healthy()['clock'], 'is_open': False})
    assert item(closed, 'market_session')['status'] == 'info'
    assert item(closed, 'market_session')['detail'] == 'The market is closed. Next open: Wed Sep 23, 9:30 AM ET.'
    assert closed['status'] == 'waiting' and closed['headline'].startswith('All set for the next session')
    late = build(clock={**healthy()['clock'], 'next_close': (NOW + timedelta(minutes=20)).isoformat()})
    assert item(late, 'market_session')['status'] == 'info' and 'final 30 minutes' in item(late, 'market_session')['detail']
    assert late['status'] == 'waiting'
    assert late['headline'] == 'No new Socrates entries in the final 30 minutes of the session.'
    late_ready = build(clock={**healthy()['clock'], 'next_close': (NOW + timedelta(minutes=20)).isoformat()})
    assert late_ready['status'] != 'ready'


def test_stale_clock_is_a_warning_not_a_guess():
    result = build(clock={**healthy()['clock'], 'timestamp': (NOW - timedelta(minutes=5)).isoformat()})
    assert item(result, 'market_session')['status'] == 'warn' and result['status'] == 'waiting'


def test_waiting_for_a_setup_is_info_and_names_the_first_open_step():
    result = build(setup={'state': 'CONFIRMING', 'checks': [
        {'name': 'Nasdaq level event', 'passed': True},
        {'name': 'Magnificent Seven at their zones', 'passed': False},
        {'name': 'Actual VIX zone reaction', 'passed': False}]})
    row = item(result, 'setup')
    assert row['status'] == 'info'
    assert row['detail'] == ('QQQ is back at the level; checking the tech leaders and the VIX; '
                             'waiting for at least four of the seven tech leaders to agree on a direction.')
    assert result['status'] == 'waiting' and result['headline'] == 'Ready to trade; watching for a Socrates setup.'
    assert result['next_action'] is None


def test_active_trade_is_info_and_waits():
    result = build(exposure={'active_trade': {'symbol': 'PSQ', 'proxy': 'inverse_etf'}})
    assert item(result, 'exposure')['status'] == 'info'
    assert 'PSQ trade (the Socrates short via PSQ)' in item(result, 'exposure')['detail']
    assert result['status'] == 'waiting' and result['headline'].startswith('Managing the open Socrates trade')


def test_instruments_qqq_fail_psq_warn_and_missing_lookup_warns():
    untradable = {'checked_at': NOW.isoformat(), 'error': None,
                  'asset': {'symbol': 'X', 'status': 'inactive', 'tradable': False, 'fractionable': True}}
    result = build(assets={'QQQ': untradable, 'PSQ': untradable})
    assert item(result, 'instrument_qqq')['status'] == 'fail'
    assert item(result, 'instrument_psq')['status'] == 'warn' and 'short setups' in item(result, 'instrument_psq')['detail']
    whole = {'checked_at': NOW.isoformat(), 'error': None,
             'asset': {'symbol': 'PSQ', 'status': 'active', 'tradable': True, 'fractionable': False}}
    assert item(build(assets={**healthy()['assets'], 'PSQ': whole}), 'instrument_psq')['status'] == 'warn'
    failed = build(assets={'QQQ': {'error': 'QQQ could not be confirmed with Alpaca', 'asset': None}})
    assert item(failed, 'instrument_qqq')['status'] == item(failed, 'instrument_psq')['status'] == 'warn'
    assert failed['status'] == 'waiting'
    blocked_qqq = build(assets={'QQQ': untradable, 'PSQ': healthy()['assets']['PSQ']})
    assert blocked_qqq['status'] == 'waiting' and blocked_qqq['next_action'] is None
    assert blocked_qqq['headline'].startswith('Socrates is paused: Alpaca reports QQQ is not tradable')


def test_a_psq_restriction_does_not_hold_back_a_ready_long_setup():
    whole = {'checked_at': NOW.isoformat(), 'error': None,
             'asset': {'symbol': 'PSQ', 'status': 'active', 'tradable': True, 'fractionable': False}}
    long_setup = {**healthy()['setup'], 'direction': 'long'}
    result = build(assets={**healthy()['assets'], 'PSQ': whole}, setup=long_setup)
    assert result['status'] == 'ready' and item(result, 'instrument_psq')['status'] == 'info'
    assert 'not affected' in item(result, 'instrument_psq')['detail']
    short = build(assets={**healthy()['assets'], 'PSQ': whole})  # The healthy setup is a PSQ short.
    assert short['status'] == 'waiting' and item(short, 'instrument_psq')['status'] == 'warn'
    assert short['headline'] == 'A setup is ready, but an item below still needs to clear (1 item to watch).'


def test_an_event_already_traded_or_attempted_is_not_shown_as_ready():
    result = build(setup_consumed=True)
    assert item(result, 'setup') == {'id': 'setup', 'label': 'Socrates setup', 'status': 'info', 'needs_owner': False,
                                     'detail': 'This setup was already traded or attempted; waiting for the next one.'}
    assert result['status'] == 'waiting' and result['headline'] == 'Ready to trade; watching for a Socrates setup.'
    assert build(setup_consumed=False)['status'] == 'ready'


def test_data_problems_fail_during_the_session_and_warn_when_closed():
    health = healthy()['data_health']
    broken = deepcopy(health)
    broken['stocks']['instruments'][0].update(status='needs_attention')
    broken['stocks']['instruments'][0]['frames'][1]['status'] = 'stale'
    broken['stocks']['instruments'][2]['status'] = 'needs_attention'
    broken['vix'] = {'status': 'blocked', 'verification': {'budget': {'remaining': 400, 'limit': 900}}}
    open_result = build(data_health=broken)
    assert item(open_result, 'data_qqq') == {'id': 'data_qqq', 'label': 'QQQ price data', 'status': 'fail',
                                             'detail': 'Waiting for current QQQ 1-hour candles.', 'needs_owner': False}
    assert item(open_result, 'data_leaders')['status'] == 'fail' and MAG7[1] in item(open_result, 'data_leaders')['detail']
    assert item(open_result, 'data_vix')['status'] == 'fail'
    # Late data clears on its own: paused, not 'needs your action', and no self-contradicting restart advice.
    assert open_result['status'] == 'waiting' and open_result['next_action'] is None and open_result['action'] is None
    assert open_result['headline'] == ('Socrates is paused until QQQ price data is current again; '
                                       'Pivot keeps checking on its own.')
    closed = build(data_health=broken, clock={**healthy()['clock'], 'is_open': False})
    assert {item(closed, key)['status'] for key in ('data_qqq', 'data_leaders', 'data_vix')} == {'warn'}
    assert closed['status'] == 'waiting'


def test_vix_allowance_exhausted_blocks_and_low_allowance_warns():
    health = healthy()['data_health']
    empty = deepcopy(health)
    empty['vix']['verification']['budget']['remaining'] = 0
    exhausted = build(data_health=empty)
    assert item(exhausted, 'data_vix')['status'] == 'fail' and item(exhausted, 'data_vix')['needs_owner'] is False
    assert exhausted['status'] == 'waiting' and exhausted['next_action'] is None
    assert exhausted['headline'] == 'Socrates is paused: the free monthly VIX allowance is used up and resets next month.'
    # An owner step elsewhere still wins the headline and the status.
    both = build(data_health=empty, available_dollars='1.00')
    assert both['status'] == 'blocked' and both['next_action'] == 'Deposit funds or lower the purchase size.'
    low = deepcopy(health)
    low['vix']['verification']['budget']['remaining'] = 12
    assert item(build(data_health=low), 'data_vix')['status'] == 'warn'


def test_allowance_used_up_waits_and_unverifiable_allowance_blocks():
    used = build(entry_allowance={'status': 'available', 'limit': 2, 'families': {'socrates': {'used': 2, 'remaining': 0}}})
    assert item(used, 'entry_allowance')['status'] == 'warn' and used['status'] == 'waiting'
    assert used['headline'] == 'No new Socrates entries today: both attempts are used (1 item to watch).'
    unknown = build(entry_allowance={'status': 'blocked', 'families': None})
    assert item(unknown, 'entry_allowance')['status'] == 'fail' and unknown['status'] == 'blocked'


def test_update_hold_warns_and_unreadable_lock_blocks():
    held = build(deployment_gate={'hold_present': True, 'locked': False})
    assert item(held, 'deployment')['status'] == 'warn'
    assert held['headline'] == 'New Socrates entries wait while an app update is installed (1 item to watch).'
    assert item(build(deployment_gate={'hold_present': False, 'locked': False, 'error': 'entry_gate_unavailable'}),
                'deployment')['status'] == 'fail'
    assert item(build(deployment_gate=None), 'deployment')['status'] == 'ok'


def test_missing_inputs_degrade_to_warnings_never_raise():
    result = build_socrates_readiness({}, NOW)
    assert [row['id'] for row in result['items']] == list(ITEM_IDS)
    assert result['status'] == 'blocked'  # No execution connection and no permission.
    assert item(result, 'data_vix')['status'] == 'warn' and item(result, 'setup')['status'] == 'warn'


# --- Service integration -------------------------------------------------------


class CountingBroker(FakeBroker):
    def __init__(self):
        super().__init__()
        self.asset_reads = []
        self.asset_error = None

    def asset(self, symbol):
        self.asset_reads.append(symbol)
        if self.asset_error:
            raise self.asset_error
        return super().asset(symbol)


def service(tmp_path):
    broker = CountingBroker()
    broker.at = datetime.now(timezone.utc)
    return Service(broker, Store(tmp_path / 'readiness.db'), broker=broker), broker


def test_snapshot_carries_the_checklist_without_any_order_call(tmp_path):
    runtime, broker = service(tmp_path)
    runtime.refresh_account()
    snapshot = runtime.snapshot()
    readiness = snapshot['socrates_readiness']
    assert [row['id'] for row in readiness['items']] == list(ITEM_IDS)
    assert readiness['status'] == 'blocked' and readiness['next_action'].startswith('Turn Live money On')
    assert item(readiness, 'instrument_qqq')['status'] == item(readiness, 'instrument_psq')['status'] == 'ok'
    assert item(readiness, 'account')['status'] == 'ok' and item(readiness, 'buying_power')['status'] == 'ok'
    assert broker.sent == [] and broker.canceled == []


def test_review_required_and_app_pause_reach_the_checklist(tmp_path):
    runtime, broker = service(tmp_path)
    runtime.refresh_account()
    runtime.set_live({'enabled': True, 'policy_version': POLICY_VERSION})
    control = runtime.store.control()
    runtime.store.set_control(True, 'nasdaq-qqq-execution-v6-reward-risk-vix-persistence', control['account_ref'])
    runtime.store.pause_family('socrates', 'The broker rejected a QQQ entry order (HTTP 422); no order was created at Alpaca.')
    readiness = runtime.snapshot()['socrates_readiness']
    assert readiness['next_action'] == ('Check the QQQ and PSQ positions and orders in Alpaca first, then turn Socrates back On '
                                        'in its card, then accept the updated Socrates rules from Live money in the header.')
    assert readiness['action'] is None
    assert 'Pivot paused it: The broker rejected a QQQ entry order' in item(readiness, 'strategy_enabled')['detail']
    runtime.save_strategies({'socrates': {'enabled': True}})
    assert item(runtime.snapshot()['socrates_readiness'], 'strategy_enabled')['status'] == 'ok'
    assert broker.sent == []


def test_asset_lookups_are_cached_for_ten_minutes_success_or_failure(tmp_path):
    runtime, broker = service(tmp_path)
    start = datetime(2026, 9, 22, 15, tzinfo=timezone.utc)
    runtime.refresh_instruments(start)
    runtime.refresh_instruments(start + timedelta(seconds=ASSET_REFRESH_SECONDS - 1))
    assert broker.asset_reads == ['QQQ', 'PSQ'] and ASSET_REFRESH_SECONDS == 600
    broker.asset_error = RuntimeError('provider down')
    runtime.refresh_instruments(start + timedelta(seconds=ASSET_REFRESH_SECONDS))
    assert broker.asset_reads == ['QQQ', 'PSQ'] * 2
    runtime.refresh_instruments(start + timedelta(seconds=ASSET_REFRESH_SECONDS + 30))
    assert broker.asset_reads == ['QQQ', 'PSQ'] * 2  # A failure is not retried in a burst.
    readiness = runtime.snapshot()['socrates_readiness']
    assert item(readiness, 'instrument_qqq')['status'] == item(readiness, 'instrument_psq')['status'] == 'warn'
    assert broker.sent == []


def test_snapshot_never_reads_assets_itself(tmp_path):
    runtime, broker = service(tmp_path)
    for _ in range(3):
        runtime.snapshot()
    assert broker.asset_reads == []
    assert item(runtime.snapshot()['socrates_readiness'], 'instrument_qqq')['status'] == 'warn'


def test_foreign_psq_order_blocks_and_owned_trade_waits(tmp_path):
    runtime, broker = service(tmp_path)
    broker.book['manual'] = {'id': 'manual', 'client_order_id': 'manual', 'symbol': 'PSQ', 'side': 'buy',
                             'qty': '1', 'filled_qty': '0', 'status': 'new', 'type': 'limit'}
    runtime.refresh_account()
    readiness = runtime.snapshot()['socrates_readiness']
    assert item(readiness, 'exposure')['status'] == 'fail' and '(PSQ)' in item(readiness, 'exposure')['detail']
    assert broker.canceled == [] and broker.sent == []


def test_update_hold_from_the_entry_gate(tmp_path):
    runtime, broker = service(tmp_path)
    gate = EntryGate(tmp_path / 'deployment.lock')
    runtime.executor.entry_gate = gate
    runtime.refresh_account()
    assert item(runtime.snapshot()['socrates_readiness'], 'deployment')['status'] == 'ok'
    (tmp_path / 'entry-hold.json').write_text('{}')
    assert item(runtime.snapshot()['socrates_readiness'], 'deployment')['status'] == 'warn'


def test_builder_failure_degrades_to_a_blocked_summary(tmp_path, monkeypatch):
    runtime, _ = service(tmp_path)
    monkeypatch.setattr(runtime.store, 'control', lambda: (_ for _ in ()).throw(OSError('locked')))
    readiness = runtime.socrates_readiness({})
    assert readiness['status'] == 'blocked' and readiness['items'] == [] and readiness['next_action'] is None


def test_service_marks_a_ready_event_that_was_already_used(tmp_path):
    from pivot.execution import Executor
    runtime, broker = service(tmp_path)
    runtime.refresh_account()
    ready = {'state': 'SETUP_READY', 'direction': 'long', 'event_id': 'evt-1'}
    assert runtime._setup_consumed(ready) is False
    key = Executor._event_key(ready)
    runtime.store.entry_consumed = lambda identity: identity == key
    assert runtime._setup_consumed(ready) is True
    other = {'state': 'SETUP_READY', 'event_id': 'evt-2'}
    assert runtime._setup_consumed({**ready, 'entry_candidates': [ready, other]}) is False
    assert runtime._setup_consumed({**ready, 'entry_candidates': [ready]}) is True
    assert runtime._setup_consumed({'state': 'CONFIRMING', 'event_id': 'evt-1'}) is False
    assert runtime._setup_consumed({'state': 'SETUP_READY'}) is False  # No event id: never claimed as used.
    snapshot = runtime.snapshot()
    snapshot['setup'] = ready
    assert item(runtime.socrates_readiness(snapshot), 'setup')['detail'].startswith('This setup was already traded')
    assert broker.sent == []


def test_live_dialog_key_rules_match_the_backend_policy_they_summarise():
    """The five hand-written key points in the Live dialog must say what the accepted policy says."""
    from pathlib import Path
    from pivot.policy import POLICY
    source = (Path(__file__).resolve().parents[1] / 'web' / 'readiness.mjs').read_text()
    key_rules = source.split('export const SOCRATES_KEY_RULES=[', 1)[1].split('];', 1)[0]
    policy = ' '.join(POLICY['summary'])
    for shown, accepted in (('within 0.4%', 'within 0.4% of it'), ('4 of the 7', 'four of seven leaders must agree'),
                            ('at most 1 against', 'at most one opposing'), ('within 60 minutes', 'up to 60 minutes'),
                            ('end of the next session', 'end of the next regular session'),
                            ('1R minimum', 'at least as far away as the stop (1R)'), ('longs only (QQQ)', 'Longs only in this release'),
                            ('10:00 AM–12:00 PM ET', 'between 10:00 AM and 12:00 PM'), ('0.20% away', 'at least 0.20% away'),
                            ('more than 1.5% away', 'more than 1.5% from the live entry price'),
                            ('one Socrates position at a time', 'One position at a time'),
                            ('two entry attempts per New York session', 'two new entries per New York session'),
                            ('last 30 minutes', 'final 30 minutes')):
        assert shown in key_rules, shown
        assert accepted in policy, accepted
