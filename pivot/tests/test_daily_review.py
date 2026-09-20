"""Daily reports are observations, never trading controls or fee guesses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os

import pytest

from pivot.crypto_store import CryptoStore
from pivot.daily_review import DailyReviewStore, build_review
from pivot.store import Store

NOW = datetime(2026, 9, 20, 16, tzinfo=timezone.utc)
ACCOUNT = 'private-account-A'
DAY = '2026-09-19'


@pytest.fixture
def stores(tmp_path):
    main = Store(tmp_path/'main.sqlite3')
    return main, CryptoStore(main.path)


def accounting(day=DAY, account=ACCOUNT, **changes):
    return {'account_scope': sha256(account.encode()).hexdigest(), 'day': day,
            'status': 'available', 'data_complete': True, 'currentness': 'current',
            'fees': {'observed_usd_cost': None, 'status': 'pending', 'final': False},
            'per_trade_net': [], **changes}


def report(stores, **kwargs):
    return build_review(*stores, kwargs.pop('accounting_summary', accounting()),
                        kwargs.pop('account_ref', ACCOUNT), kwargs.pop('day', DAY), kwargs.pop('now', NOW))


def lifecycle(identity='trade-1', account=ACCOUNT, family='socrates', created='2026-09-19T14:00:00+00:00',
              completed='2026-09-19T14:30:00+00:00', entry_price='100', exit_price='101'):
    symbol = 'QQQ' if family == 'socrates' else 'BTC/USD'
    def op(name, side, price, at):
        return {'state': 'attempted', 'attempted_at': at,
                'payload': {'client_order_id': identity+'-'+name, 'symbol': symbol, 'side': side},
                'last_seen': {'id': 'broker-'+identity+'-'+name, 'client_order_id': identity+'-'+name,
                              'symbol': symbol, 'side': side, 'status': 'filled', 'qty': '.05',
                              'filled_qty': '.05', 'filled_avg_price': price, 'filled_at': at}}
    return {'id': identity, 'account_ref': account, 'symbol': symbol, 'direction': 'long',
            'stage': 'finished' if completed else 'open', 'created_at': created, 'completed_at': completed,
            'signal': {'policy_version': 'frozen-video-v1', 'strategy_id': 'prior_day_sweep'},
            'filled_qty': '.05', 'reason': 'Target reached',
            'ops': {'entry': op('entry', 'buy', entry_price, created),
                    **({'exit0': op('exit0', 'sell', exit_price, completed)} if completed else {})}}


def insert_trade(stores, trade, crypto=False):
    store = stores[1] if crypto else stores[0]
    with store.connect() as db:
        if crypto:
            db.execute('INSERT INTO crypto_trades VALUES(?,?,?,?,?)',
                       (trade['id'], trade['symbol'], int(trade['stage']=='finished'), 0, json.dumps(trade)))
        else:
            db.execute('INSERT INTO trades VALUES(?,?,?)', (trade['id'], int(trade['stage']=='finished'), json.dumps(trade)))


def trace(stores, reason, at='2026-09-19T15:00:00+00:00'):
    value = {'captured_at': at, 'first_blocker': {'name': reason}}
    with stores[0].connect() as db:
        db.execute('INSERT INTO decision_traces VALUES(NULL,?,?,?,?,?,?)', (at, reason, at, at, 1, json.dumps(value)))


def test_no_trades_and_missing_fees_are_not_invented_profit(stores):
    before = stores[0].control(), stores[0].settings(), stores[1].control()
    result = report(stores)
    for family in result['families'].values():
        assert family['submission_attempts'] == family['closed_trades'] == 0
        assert family['wins'] == family['losses'] == 0
        assert family['verified_net_pnl'] is None and family['known_fees'] is None
        assert family['net_pnl_status'] == 'no_closed_trades'
    assert result['accounting']['fees']['observed_usd_cost'] is None
    assert result['period_status'] == 'day_ended' and result['historical_only']
    assert before == (stores[0].control(), stores[0].settings(), stores[1].control())
    assert result['missing_evidence'] and all('broker' not in row for row in result)


def test_actual_blockers_are_recorded_without_rule_change_recommendation(stores):
    trace(stores, 'Nasdaq level event')
    stores[1].record_decision({'checked_at': '2026-09-19T15:00:00+00:00', 'symbol': 'BTC/USD',
                              'signal_fresh': True, 'outcome': 'unsupported_direction',
                              'reason': 'A short setup is present. Alpaca crypto cannot open short positions.'})
    result = report(stores)
    assert result['families']['socrates']['blockers'] == [{'reason': 'Nasdaq level event', 'count': 1}]
    assert 'short setup' in result['families']['range_reversal']['blockers'][0]['reason']
    assert len([item for item in result['investigations'] if item['code']=='entry_blocker']) == 2
    assert all('rules' in item['reason'] for item in result['investigations'] if item['code']=='entry_blocker')


def test_gross_win_is_not_a_verified_net_win(stores):
    insert_trade(stores, lifecycle())
    result = report(stores, accounting_summary=accounting(fees={'observed_usd_cost': '.03', 'status': 'observed', 'final': False}))
    family = result['families']['socrates']
    assert family['trades'][0]['gross_pnl'] == '0.05'
    assert family['trades'][0]['gross_status'] == 'verified_gross'
    assert family['closed_trades'] == family['unverified_outcomes'] == 1
    assert family['wins'] == 0 and family['verified_net_pnl'] is None
    assert family['known_fees'] is None
    assert result['accounting']['fees']['observed_usd_cost'] == '0.03'
    assert result['accounting']['fees']['final'] is False


def test_only_uniquely_verified_attributed_net_outcomes_count(stores):
    for i in range(3):
        insert_trade(stores, lifecycle(identity=f'trade-{i}'))
    rows = [{'trade_id': f'trade-{i}', 'family_id': 'socrates', 'status': 'verified',
             'fees_status': 'verified', 'net_pnl_usd': net} for i, net in enumerate(('.04', '-.01', '0'))]
    family = report(stores, accounting_summary=accounting(per_trade_net=rows))['families']['socrates']
    assert (family['wins'], family['losses'], family['breakeven']) == (1, 1, 1)
    assert family['verified_net_pnl'] == '0.03' and family['net_pnl_status'] == 'complete'
    rows.append(deepcopy(rows[0]))
    family = report(stores, accounting_summary=accounting(per_trade_net=rows))['families']['socrates']
    assert family['wins'] == 0 and family['net_pnl_status'] == 'partial'


def test_partial_or_unresolved_lifecycle_is_not_a_completed_outcome(stores):
    trade = lifecycle(completed=None)
    trade['ops']['entry']['last_seen'].update(status='partially_filled', filled_qty='.02', filled_at=None)
    insert_trade(stores, trade)
    row = {'trade_id':trade['id'], 'family_id':'socrates', 'status':'verified', 'fees_status':'verified', 'net_pnl_usd':'1'}
    family = report(stores, accounting_summary=accounting(per_trade_net=[row]))['families']['socrates']
    assert family['closed_trades'] == family['wins'] == 0
    assert family['filled_entries'] is None and family['open_at_end'] is None
    assert family['trades'][0]['filled_qty'] == '0.02'
    assert family['trades'][0]['net_pnl'] is None


def test_prior_day_entry_exit_today_is_included_without_counting_new_entry(stores):
    trade = lifecycle(created='2026-09-18T23:00:00-04:00', completed='2026-09-19T00:30:00-04:00', family='range_reversal')
    insert_trade(stores, trade, crypto=True)
    family = report(stores)['families']['range_reversal']
    assert family['submission_attempts'] == family['filled_entries'] == 0
    assert family['closed_trades'] == 1 and family['trades'][0]['completed_on_day']
    assert family['trades'][0]['created_at'] == trade['created_at']


def test_new_york_day_and_later_exit_do_not_contaminate_previous_day(stores):
    trade = lifecycle(created='2026-09-19T23:30:00-04:00', completed='2026-09-20T00:05:00-04:00')
    insert_trade(stores, trade)
    prior = report(stores)['families']['socrates']
    assert prior['filled_entries'] == prior['open_at_end'] == 1
    assert prior['closed_trades'] == 0
    assert prior['trades'][0]['outcome'] == 'open'
    assert prior['trades'][0]['exit_price'] is None and prior['trades'][0]['completed_at'] is None
    current = report(stores, day='2026-09-20')['families']['socrates']
    assert current['closed_trades'] == 1 and current['filled_entries'] == 0


def test_current_day_and_dst_cutoff_are_explicit(stores):
    result = report(stores, day='2026-11-01', now=datetime(2026,11,1,6,30,tzinfo=timezone.utc))
    assert result['period_status'] == 'in_progress'
    assert result['as_of'] == '2026-11-01T06:30:00+00:00'
    with pytest.raises(ValueError, match='future'):
        report(stores, day='2026-11-02', now=datetime(2026,11,1,6,30,tzinfo=timezone.utc))


def test_account_isolation_and_payload_allowlist(stores):
    insert_trade(stores, lifecycle())
    insert_trade(stores, lifecycle(identity='other', account='private-account-B'))
    result = report(stores, accounting_summary=accounting(account='private-account-B', fees={'observed_usd_cost':'999'}))
    assert result['families']['socrates']['closed_trades'] == 1
    assert result['accounting']['status'] == 'unavailable'
    encoded = json.dumps(result)
    assert ACCOUNT not in encoded and 'private-account-B' not in encoded
    assert 'client_order_id' not in encoded and 'broker-trade' not in encoded and 'payload' not in encoded
    assert result['families']['socrates']['trades'][0]['source_references']
    assert result['families']['socrates']['trades'][0]['strategy_version'] == 'frozen-video-v1'


def test_rejected_unfilled_order_counts_attempt_not_loss(stores):
    trade = lifecycle()
    trade['ops'] = {'entry': trade['ops']['entry']}
    trade['ops']['entry']['last_seen'].update(status='rejected', filled_qty='0', filled_avg_price=None)
    insert_trade(stores, trade)
    family = report(stores)['families']['socrates']
    assert family['submission_attempts'] == 1 and family['closed_trades'] == family['losses'] == 0
    assert family['trades'][0]['outcome'] == 'unfilled'


def test_reports_persist_deduplicate_and_revise_late_costs(stores, tmp_path):
    path = tmp_path/'reviews.sqlite3'
    journal = DailyReviewStore(path)
    first = report(stores)
    saved = journal.save(ACCOUNT, first)
    assert saved['revision'] == 1
    first['generated_at'] = (NOW+timedelta(minutes=5)).isoformat()
    assert journal.save(ACCOUNT, first)['revision'] == 1
    later = report(stores, accounting_summary=accounting(fees={'observed_usd_cost':'.025', 'status':'observed'}))
    assert journal.save(ACCOUNT, later)['revision'] == 2
    reopened = DailyReviewStore(path)
    assert reopened.get(ACCOUNT, DAY, 1)['accounting']['fees']['observed_usd_cost'] is None
    assert reopened.get(ACCOUNT, DAY)['accounting']['fees']['observed_usd_cost'] == '0.025'
    assert reopened.history(ACCOUNT)[0]['revision'] == 2
    assert reopened.get('private-account-B', DAY) is None
    with pytest.raises(ValueError, match='account differs'):
        journal.save('private-account-B', first)
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_revision_and_day_retention_are_bounded(stores, tmp_path, monkeypatch):
    import pivot.daily_review as module
    monkeypatch.setattr(module, 'MAX_DAYS', 2)
    monkeypatch.setattr(module, 'MAX_REVISIONS', 2)
    journal = DailyReviewStore(tmp_path/'reviews.sqlite3')
    for day in ('2026-09-17', '2026-09-18', '2026-09-19'):
        for i in range(3):
            value = report(stores, day=day)
            value['missing_evidence'].append(str(i))
            journal.save(ACCOUNT, value)
    assert journal.get(ACCOUNT, '2026-09-17') is None
    assert journal.get(ACCOUNT, DAY, 1) is None and journal.get(ACCOUNT, DAY)['revision'] == 3
    assert len(journal.history(ACCOUNT, limit=2)) == 2


def test_private_storage_rejects_symlink(tmp_path):
    target = tmp_path/'actual'
    target.touch()
    link = tmp_path/'link'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='regular'):
        DailyReviewStore(link)


def test_refresh_clock_changes_do_not_create_new_report_revision(stores, tmp_path):
    journal = DailyReviewStore(tmp_path/'reviews.sqlite3')
    first = report(stores, accounting_summary=accounting(last_attempt_at=NOW.isoformat(), last_success_at=NOW.isoformat()))
    journal.save(ACCOUNT, first)
    later = report(stores, accounting_summary=accounting(last_attempt_at=(NOW+timedelta(minutes=5)).isoformat(),
                                                        last_success_at=(NOW+timedelta(minutes=5)).isoformat()))
    assert journal.save(ACCOUNT, later)['revision'] == 1
    later['accounting']['data_complete'] = False
    assert journal.save(ACCOUNT, later)['revision'] == 2


def test_previous_day_coverage_beyond_day_boundary_does_not_create_poll_revisions(stores, tmp_path):
    journal = DailyReviewStore(tmp_path/'reviews.sqlite3')
    early = accounting(coverage={'created_after':'2026-09-12T04:00:00Z', 'created_until':NOW.isoformat()})
    journal.save(ACCOUNT, report(stores, accounting_summary=early))
    early['coverage']['created_until'] = (NOW+timedelta(minutes=5)).isoformat()
    assert journal.save(ACCOUNT, report(stores, accounting_summary=early))['revision'] == 1


def test_original_crypto_exit_trigger_and_source_revision_survive_generic_completion(stores):
    trade = lifecycle(family='range_reversal')
    trade.update(reason='Crypto position fully closed and orders reconciled',
                 exit_pending={'reason': 'Crypto target reached'}, revision='a'*40,
                 authorization={'crypto': {'policy': 'range-spot-execution-v1'}})
    insert_trade(stores, trade, crypto=True)
    row = report(stores)['families']['range_reversal']['trades'][0]
    assert row['exit_reason'] == 'Crypto target reached' and row['exit_reason_status'] == 'recorded'
    assert row['revision'] == 'a'*40 and row['execution_policy_version'] == 'range-spot-execution-v1'


def test_native_stop_fill_supplies_factual_exit_evidence(stores):
    trade = lifecycle(family='range_reversal', exit_price='99')
    trade['ops']['stop'] = trade['ops'].pop('exit0')
    trade['reason'] = 'Crypto position fully closed and orders reconciled'
    insert_trade(stores, trade, crypto=True)
    row = report(stores)['families']['range_reversal']['trades'][0]
    assert row['exit_reason'] == 'Broker protective stop filled'
    assert row['exit_reason_status'] == 'fill_evidence' and row['exit_price'] == '99'
    assert row['net_pnl'] is None


def test_missing_exit_price_never_becomes_a_complete_execution_average(stores):
    trade = lifecycle()
    trade['ops']['exit1'] = deepcopy(trade['ops']['exit0'])
    trade['ops']['exit1']['last_seen']['filled_avg_price'] = None
    insert_trade(stores, trade)
    row = report(stores)['families']['socrates']['trades'][0]
    assert row['exit_price'] is None and row['gross_pnl'] is None
    assert row['outcome'] == 'closed_unverified'


def test_same_date_reports_for_two_accounts_remain_separate(stores, tmp_path):
    journal = DailyReviewStore(tmp_path/'reviews.sqlite3')
    journal.save(ACCOUNT, report(stores))
    second = report(stores, account_ref='private-account-B', accounting_summary=accounting(account='private-account-B'))
    journal.save('private-account-B', second)
    assert len(journal.history(ACCOUNT)) == len(journal.history('private-account-B')) == 1
    assert journal.get(ACCOUNT, DAY)['account_scope'] != journal.get('private-account-B', DAY)['account_scope']
