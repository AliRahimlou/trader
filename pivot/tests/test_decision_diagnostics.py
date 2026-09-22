from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from pivot.diagnostics import build_decision_trace, evidence_fingerprint
from pivot.models import Bar, Market, MAG7
from pivot.service import Service
from pivot.store import DECISION_TRACE_RETENTION, Store
from pivot.strategy import analyze


from pivot.tests.test_strategy_v2 import NOW, setup_scenario


def candle(at, opening=100, high=102, low=98, close=101, minutes=60):
    return Bar(at, minutes, opening, high, low, close)


def scenario():
    qqq, leaders, vix, _ = setup_scenario('short', 'four_hour_retest')
    return {**leaders, 'QQQ': qqq}, vix


def trace(now=NOW):
    markets, vix = scenario()
    for market in markets.values():
        market.observed_at = now
    vix.observed_at = now
    setup = analyze(markets['QQQ'], markets, vix, now)
    return build_decision_trace(setup, markets, vix, now, live_permission=True)


def test_trace_preserves_actual_signals_and_never_authorizes_broker_orders():
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    before = deepcopy((markets, vix, setup))
    result = build_decision_trace(setup, markets, vix, NOW, live_permission=True, execution_available=True)
    assert (markets, vix, setup) == before
    assert analyze(markets['QQQ'], markets, vix, NOW) == setup
    assert result['signal_qualifies'] is True and setup['can_enter'] is False
    assert result['first_blocker'] is None
    assert result['execution']['live_money_enabled'] is True
    assert result['execution']['broker_admission_evaluated'] is False
    assert result['execution']['order_authorized_by_trace'] is False
    assert result['leader_counts'] == {'long': 0, 'short': 7}
    assert result['leaders']['AAPL']['reaction_at'] == NOW.isoformat()
    assert result['leaders']['AAPL']['age_minutes'] == 0
    assert result['leaders']['AAPL']['zones']
    assert result['vix']['expected_reaction'] == 'long'
    assert result['vix']['reason'] == 'expected reaction present'
    assert result['vix']['gate_reached'] is True
    assert result['version'] == 'decision-trace-v3'
    assert result['leaders']['AAPL']['input']['frames']['5']['recent'][-1]['minutes'] == 5
    assert result['setup']['event_id'] == setup['event_id']
    assert result['setup']['leader_evidence_valid_until'] == setup['leader_evidence_valid_until']
    assert result['setup']['leader_observation_valid_until'] == setup['leader_observation_valid_until']
    assert result['setup']['leader_rule'] == {'minimum_agree': 4, 'maximum_opposing': 1, 'persistence_minutes': 60}
    assert result['setup']['event_rule'] == {'sessions': 2}
    assert result['setup']['area_rule'] == {'zone_tolerance': 0.001, 'touches': 2, 'max_areas': 16}
    assert result['setup']['retest_at'] == setup['retest_at'] == setup['event_at']
    assert result['vix']['base_bars'] == 4 and result['vix']['base_range'] == 0.02
    assert all(zone['source'] for zone in result['vix']['zones'])
    assert result['leaders']['AAPL']['vote_source'] == '5m repeated interaction'
    assert result['market_context']['descriptive_only'] is True
    assert result['market_context']['entry_veto'] is False
    assert result['leaders']['AAPL']['evidence_valid_until']
    methods = {row['id']: row for row in result['strategies']}
    assert methods['four_hour_retest']['signal_qualifies'] is True
    assert methods['prior_day_sweep']['signal_qualifies'] is False
    assert methods['prior_day_sweep']['first_blocker']['name'] == 'Premarked levels'
    assert all(row['order_authorized_by_trace'] is False for row in methods.values())
    # Every method retains its reaction evidence without duplicating full area history.
    assert methods['four_hour_retest']['leader_evidence']['AAPL']['reaction_zone']
    assert methods['four_hour_retest']['leader_evidence']['AAPL']['vote_source'] == '5m repeated interaction'
    assert 'zones' not in methods['four_hour_retest']['leader_evidence']['AAPL']


def test_exact_first_signal_blocker_and_unreached_vix_evidence_are_distinct():
    markets, vix = scenario()
    for symbol in ('AAPL', 'MSFT'):
        markets[symbol].bars[5][-1] = candle(NOW, 100, 105, 89.9, 104, 5)
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    result = build_decision_trace(setup, markets, vix, NOW)
    failed = next(c for c in setup['checks'] if not c['passed'])
    assert result['first_blocker'] == {**failed, 'stage': 'signal'}
    assert result['first_blocker']['name'] == 'Magnificent Seven at their zones'
    assert result['leader_counts'] == {'long': 2, 'short': 5}
    assert not result['signal_qualifies'] and not result['vix']['gate_reached']
    assert result['vix']['reactions']  # Counterfactual evidence is labeled, not admitted as a passed gate.


def test_missing_and_stale_data_are_visible_without_invented_votes():
    result = build_decision_trace(None, {}, None, NOW)
    assert result['first_blocker']['name'] == 'Current Nasdaq observation'
    assert result['data_health']['ready'] is False
    assert len(result['leaders']) == 7
    assert all(row['vote'] is None and row['zones'] == [] for row in result['leaders'].values())
    assert result['vix']['fresh'] is False
    markets, vix = scenario()
    result = build_decision_trace(analyze(markets['QQQ'], markets, vix, NOW+timedelta(minutes=2)),
                                  markets, vix, NOW+timedelta(minutes=2))
    assert result['leaders']['AAPL']['reason'] == 'current 5-minute data missing'
    assert result['leaders']['AAPL']['input']['observed_at'] == NOW.isoformat()


def test_allowlist_excludes_account_credentials_raw_errors_and_quotes():
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    secret = 'PRIVATE_TOKEN_ACCOUNT_IDENTIFIER'
    setup.update(account_ref=secret, credentials=secret, quote={'bid': secret})
    markets['foreign-'+secret] = replace(markets['AAPL'], source=secret)
    health = {'ready': False, 'account_ref': secret,
        'stocks': {'status': 'needs_attention', 'error': secret, 'headers': secret,
                   'instruments': [{'symbol': 'QQQ', 'status': 'current', 'account_ref': secret, 'frames': []}]},
        'vix': {'status': 'blocked', 'error': secret, 'verification': {
            'api_key': secret, 'response': secret, 'latest_value': secret,
            'budget': {'used': 12, 'limit': 1000, 'remaining': 988, 'api_key': secret}}}}
    result = build_decision_trace(setup, markets, vix, NOW, data_health=health,
                                  broker_clock={'is_open': True, 'account_id': secret})
    serialized = json.dumps(result)
    assert secret not in serialized
    assert 'account_ref' not in serialized and 'api_key' not in serialized
    assert result['data_health']['vix']['verification']['budget']['remaining'] == 988
    assert result['data_health']['vix']['error_present'] is True


def test_poll_dedup_updates_receipt_times_but_new_evidence_or_checkpoint_gets_a_row(tmp_path):
    store = Store(tmp_path/'audit.db')
    first = store.record_decision(trace())
    same = trace(NOW+timedelta(seconds=30))
    assert evidence_fingerprint(same) == evidence_fingerprint(trace())
    second = store.record_decision(same)
    assert second['id'] == first['id'] and second['observation_count'] == 2
    assert second['first_observed_at'] == NOW.isoformat()
    assert second['last_observed_at'] == (NOW+timedelta(seconds=30)).isoformat()
    assert second['leaders']['AAPL']['input']['observed_at'] == second['last_observed_at']
    changed = deepcopy(same)
    changed['execution']['live_money_enabled'] = False
    third = store.record_decision(changed)
    assert third['id'] != second['id']
    next_checkpoint = trace(NOW+timedelta(minutes=15))
    fourth = store.record_decision(next_checkpoint)
    assert fourth['id'] not in {first['id'], third['id']}
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM decision_traces').fetchone()[0] == 3


def test_trace_migration_and_restart_preserve_existing_settings_and_events(tmp_path):
    path = tmp_path/'existing.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        db.execute('INSERT INTO settings VALUES(1, ?)', ('{"sizing_mode":"target","target_dollars":"50.00"}',))
    store = Store(path)
    original_control = store.control()
    store.event('existing_event', {'known': True})
    saved = store.record_decision(trace())
    reopened = Store(path)
    assert reopened.latest_decision() == saved
    assert reopened.settings()['target_dollars'] == '50.00'
    assert reopened.control() == original_control
    assert [event['kind'] for event in reopened.events()] == ['existing_event']
    service = Service(object(), reopened)
    assert service.state['decision_trace'] == saved and service.state['diagnostic_error'] is None
    restored = service.snapshot()['decision_trace']
    assert not restored['matches_current_analysis'] and not restored['current_at_snapshot']


@pytest.mark.parametrize('body', [
    {}, {'captured_at': None}, {'captured_at': 'not-a-timestamp'},
    {'captured_at': '2026-09-17T16:00:00'}, {'captured_at': 12345},
])
def test_malformed_restored_trace_cannot_break_snapshot_or_change_analysis(tmp_path, body):
    store = Store(tmp_path/'audit.db')
    with store.connect() as db:
        db.execute('INSERT INTO decision_traces(checkpoint_at,fingerprint,first_observed_at,last_observed_at,observation_count,body) '
                   'VALUES(?,?,?,?,1,?)',
                   (NOW.isoformat(), 'malformed-fixture', NOW.isoformat(), NOW.isoformat(), json.dumps(body)))
    service = Service(object(), store)
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    service.state.update(setup=deepcopy(setup), analysis_at=NOW.isoformat())
    state_before, control_before = deepcopy(service.state), store.control()
    snapshot = service.snapshot()
    assert snapshot['setup'] == setup
    assert service.state == state_before and store.control() == control_before
    assert snapshot['decision_trace']['matches_current_analysis'] is False
    assert snapshot['decision_trace']['current_at_snapshot'] is False
    assert snapshot['diagnostic_error'] == 'Saved decision trace timestamp is invalid; current analysis remains separate'
    assert snapshot['live_enabled'] is False


def test_retention_is_bounded_and_keeps_latest_observations(tmp_path):
    assert DECISION_TRACE_RETENTION >= 60*390
    store = Store(tmp_path/'audit.db')
    with store.connect() as db:
        db.executemany('INSERT INTO decision_traces(checkpoint_at,fingerprint,first_observed_at,last_observed_at,observation_count,body) '
                       'VALUES(?,?,?,?,1,?)',
                       ((str(i), str(i), '2020-01-01', '2020-01-01', '{}') for i in range(DECISION_TRACE_RETENTION+3)))
    newest = store.record_decision(trace())
    with store.connect() as db:
        count, oldest = db.execute('SELECT count(*),min(id) FROM decision_traces').fetchone()
    assert count == DECISION_TRACE_RETENTION and oldest == 5
    assert store.latest_decision() == newest


class MissingFeeds:
    def stocks(self, now):
        return {}

    def vix(self, now):
        return None


def test_service_persists_missing_data_and_exposes_latest_trace_in_snapshot(tmp_path):
    store = Store(tmp_path/'audit.db')
    service = Service(MissingFeeds(), store)
    service.refresh_analysis()
    snapshot = service.snapshot()
    assert snapshot['setup'] is None
    assert snapshot['decision_trace']['persisted'] is True
    assert snapshot['diagnostic_error'] is None
    assert snapshot['decision_trace']['first_blocker']['name'] == 'Current Nasdaq observation'
    assert snapshot['decision_trace']['current_at_snapshot'] is True
    assert {key: value for key, value in snapshot['decision_trace'].items()
            if key not in ('current_at_snapshot', 'matches_current_analysis')} == store.latest_decision()


def test_write_failure_is_visible_sanitized_and_does_not_modify_signal_or_permission(tmp_path, monkeypatch):
    store = Store(tmp_path/'audit.db')
    service = Service(MissingFeeds(), store)
    service.refresh_analysis()
    saved = deepcopy(service.state['decision_trace'])
    control = store.control()
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    service.state['setup'] = deepcopy(setup)
    def fail(_):
        raise OSError('private path API_KEY=do-not-disclose')
    monkeypatch.setattr(store, 'record_decision', fail)
    service._record_decision(markets, vix, NOW)
    assert service.state['setup'] == setup
    assert store.control() == control
    assert service.state['decision_trace'] == saved
    assert 'could not be saved' in service.state['diagnostic_error']
    assert 'API_KEY' not in service.state['diagnostic_error']
    assert 'do-not-disclose' not in service.state['diagnostic_error']


def test_diagnostic_generation_failure_also_preserves_setup(tmp_path, monkeypatch):
    service = Service(MissingFeeds(), Store(tmp_path/'audit.db'))
    markets, vix = scenario()
    setup = analyze(markets['QQQ'], markets, vix, NOW)
    service.state['setup'] = deepcopy(setup)
    def fail(*args, **kwargs):
        raise RuntimeError('private provider exception')
    monkeypatch.setattr('pivot.service.build_decision_trace', fail)
    service._record_decision(markets, vix, NOW)
    assert service.state['setup'] == setup and service.state['diagnostic_error']
    assert 'private provider' not in service.state['diagnostic_error']
