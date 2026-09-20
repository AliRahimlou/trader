from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
import sqlite3

import pytest

from pivot.activity_ledger import ActivityLedger, NY


NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)
DAY = '2026-09-19'
REF = 'test-private-account-ref'


def account(ref=REF, equity='92'):
    return {'account_ref': ref, 'currency': 'USD', 'equity': equity}


class Feeds:
    """The only supported broker operations are read-only account and activity GETs."""
    def __init__(self, pages=None, ref=REF, accounts=None):
        self.pages = deepcopy([[]] if pages is None else pages)
        self.ref, self.accounts, self.calls, self.account_calls = ref, accounts, [], 0

    def account(self):
        self.account_calls += 1
        return deepcopy(self.accounts.pop(0)) if self.accounts else account(self.ref)

    def get(self, provider, path, params):
        assert (provider, path) == ('alpaca', '/v2/account/activities')
        self.calls.append(deepcopy(params))
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def fill(identity='fill-private-id', **overrides):
    return {'id': identity, 'activity_type': 'FILL', 'transaction_time': '2026-09-19T18:00:00Z',
            'type': 'fill', 'qty': '0.002', 'price': '2500', 'cum_qty': '999',
            'side': 'buy', 'symbol': 'ETH/USD', 'order_id': 'order-private-id', **overrides}


def fee(identity='fee-private-id', kind='CFEE', **overrides):
    return {'id': identity, 'activity_type': kind, 'date': DAY,
            'symbol': 'ETHUSD', 'qty': '-0.000195', 'price': '1884.5',
            'net_amount': '0', 'status': 'executed', **overrides}


def transfer(identity='transfer-private-id', kind='CSD', **overrides):
    return {'id': identity, 'activity_type': kind, 'date': DAY,
            'net_amount': '20' if kind == 'CSD' else '-10', 'status': 'executed', **overrides}


@pytest.fixture
def ledger(tmp_path):
    return ActivityLedger(tmp_path / 'activity.sqlite3')


def collect(ledger, rows, now=NOW, ref=REF):
    return ledger.refresh(Feeds([rows], ref), ref, now)


def two_equities(ledger, start=NOW - timedelta(hours=2), end=NOW - timedelta(hours=1),
                 first='92', last='96', ref=REF):
    ledger.record_equity(ref, account(ref, first), start)
    ledger.record_equity(ref, account(ref, last), end)


def test_uncollected_account_has_no_invented_zeroes(ledger):
    result = ledger.summary(REF, DAY, NOW)
    assert result['status'] == 'unavailable' and not result['data_complete']
    assert result['fills']['count'] is None
    assert result['fills']['executed_notional_usd'] is None
    assert result['fees']['observed_usd_cost'] is None
    assert result['cash_flows']['net_usd'] is None
    assert result['account_change']['equity_change_usd'] is None


def test_empty_success_means_observed_zero_not_verified_no_fees_or_profit(ledger):
    result = collect(ledger, [])
    assert result['status'] == 'current' and result['data_complete']
    assert Decimal(result['fills']['executed_notional_usd']) == 0
    assert Decimal(result['fees']['observed_usd_cost']) == 0
    assert result['fees']['status'] == 'pending' and result['fees']['final'] is False
    assert result['realized_net_pnl_usd'] is None and result['net_status'] == 'unverified'
    assert result['per_trade_net'] == []


def test_fill_notional_uses_incremental_quantity_not_cumulative_or_order_count(ledger):
    result = collect(ledger, [fill('partial-one', type='partial_fill'),
                              fill('partial-two', qty='0.001', type='partial_fill'),
                              fill('sell', side='sell', qty='0.003', price='2600')])
    assert result['fills']['count'] == 3
    assert Decimal(result['fills']['buy_notional_usd']) == Decimal('7.5')
    assert Decimal(result['fills']['sell_notional_usd']) == Decimal('7.8')
    assert Decimal(result['fills']['executed_notional_usd']) == Decimal('15.3')
    assert result['realized_net_pnl_usd'] is None


def test_fractional_fill_and_crypto_fee_keep_exact_decimal_evidence(ledger):
    quantity = '0.000000000000000000012345'
    price = '98765.432198765432198765432198'
    result = collect(ledger, [fill(qty=quantity, price=price), fee(qty='-' + quantity, price=price)])
    expected = '0.000000000000001219259260493759260493759260484310'
    assert Decimal(result['fills']['executed_notional_usd']) == Decimal(expected)
    assert Decimal(result['fees']['observed_usd_cost']) == Decimal(expected)


def test_documented_crypto_fee_is_valued_once_and_usd_refunds_reduce_costs(ledger):
    result = collect(ledger, [fee(), fee('usd', 'FEE', net_amount='-0.10'),
                              fee('rebate', 'FEE', net_amount='0.01')])
    assert result['fees']['activity_count'] == 3
    assert Decimal(result['fees']['observed_usd_cost']) == Decimal('0.4574775')
    assert result['fees']['status'] == 'observed_not_final' and not result['fees']['final']


@pytest.mark.parametrize('overrides', [
    {'price': None}, {'qty': 'NaN'}, {'price': 'Infinity'}, {'net_amount': '-1'},
    {'symbol': 'ETHBTC'}, {'currency': 'EUR'}, {'status': 'pending'},
    {'price': '0'}, {'qty': True}, {'qty': '1e9999999'},
])
def test_malformed_or_unvalued_crypto_fee_never_becomes_zero(ledger, overrides):
    result = collect(ledger, [fee(**overrides)])
    assert result['status'] == 'partial' and not result['data_complete']
    assert result['fees']['observed_usd_cost'] is None
    assert result['fees']['unresolved_count'] == 1
    assert result['fees']['status'] == 'unvalued_activities'


@pytest.mark.parametrize('overrides', [
    {'qty': '-1'}, {'side': 'unknown'}, {'price': None}, {'symbol': 'ETH/BTC'},
    {'currency': 'EUR'}, {'type': 'correction'}, {'symbol': 'ETHBTC'},
    {'symbol': 'BTCUSDT'}, {'quote_currency': 'BTC'}, {'asset_class': 'us_option'},
    {'symbol': 'TSLA260918C00300000'},
])
def test_invalid_fill_does_not_understate_executed_notional(ledger, overrides):
    result = collect(ledger, [fill('valid'), fill('bad', **overrides)])
    assert result['fills']['count'] == 2 and result['fills']['unresolved_count'] == 1
    assert result['fills']['executed_notional_usd'] is None


def test_cash_flows_are_separate_from_fills_fees_and_profit(ledger):
    result = collect(ledger, [fill(), fee(), transfer(), transfer('out', 'CSW')])
    assert Decimal(result['cash_flows']['deposits_usd']) == 20
    assert Decimal(result['cash_flows']['withdrawals_usd']) == 10
    assert Decimal(result['cash_flows']['net_usd']) == 10
    assert Decimal(result['fills']['executed_notional_usd']) == 5
    assert result['realized_net_pnl_usd'] is None


@pytest.mark.parametrize('item', [transfer(net_amount='-20'), transfer(kind='CSW', net_amount='10')])
def test_wrong_cash_flow_sign_is_unknown_not_corrected_silently(ledger, item):
    result = collect(ledger, [item])
    assert result['cash_flows']['net_usd'] is None
    assert result['cash_flows']['unresolved_count'] == 1


def test_pagination_boundary_duplicates_and_restart_are_deduplicated(tmp_path):
    ledger = ActivityLedger(tmp_path / 'ledger.db', page_size=2)
    first, second, third = fill('one'), fill('two'), fill('three')
    feeds = Feeds([[first, second], [second, third], []])
    result = ledger.refresh(feeds, REF, NOW)
    assert result['fills']['count'] == 3
    assert [call.get('page_token') for call in feeds.calls] == [None, 'two', 'three']
    assert feeds.account_calls == 2 and result['coverage']['pages_complete']
    reopened = ActivityLedger(ledger.path)
    result = collect(reopened, [first, second, third])
    assert result['fills']['count'] == 3
    assert Decimal(result['fills']['executed_notional_usd']) == 15


def test_late_fee_and_same_id_correction_replace_without_double_charging(ledger):
    initial = collect(ledger, [fill()])
    assert initial['fees']['status'] == 'pending'
    later = NOW + timedelta(minutes=5)
    result = collect(ledger, [fill(), fee(qty='-0.0001')], now=later)
    assert Decimal(result['fees']['observed_usd_cost']) == Decimal('0.18845')
    result = collect(ledger, [fill(), fee(qty='-0.0002')], now=later + timedelta(minutes=5))
    assert result['fees']['activity_count'] == 1
    assert Decimal(result['fees']['observed_usd_cost']) == Decimal('0.3769')


def test_late_posting_updates_prior_provider_day_without_assigning_to_fill_day(ledger):
    tomorrow = NOW + timedelta(days=1)
    collect(ledger, [fee()], now=tomorrow)
    today = ledger.summary(REF, DAY, tomorrow)
    next_day = ledger.summary(REF, '2026-09-20', tomorrow)
    assert today['fees']['activity_count'] == 1
    assert next_day['fees']['activity_count'] == 0
    assert today['fees']['final'] is False and next_day['fees']['final'] is False


def test_late_fee_older_than_fetch_window_surfaces_observed_cost_and_changed_day(ledger):
    old_day = '2026-09-01'
    result = collect(ledger, [fee(date=old_day)])
    assert result['changed_days'] == [old_day]
    older = ledger.summary(REF, old_day, NOW)
    assert older['status'] == 'partial' and not older['data_complete']
    assert older['fees']['activity_count'] == 1
    assert Decimal(older['fees']['observed_usd_cost']) == Decimal('0.3674775')
    assert older['fills']['count'] is None
    assert older['cash_flows']['net_usd'] is None
    assert collect(ledger, [fee(date=old_day)])['changed_days'] == []
    corrected = collect(ledger, [fee(date='2026-09-02')])
    assert corrected['changed_days'] == ['2026-09-01', '2026-09-02']
    assert ledger.summary(REF, old_day, NOW)['fees']['activity_count'] is None


def test_cross_id_correction_is_not_assumed_to_be_an_additional_fee(ledger):
    result = collect(ledger, [fee('old'), fee('new', previous_id='old')])
    assert result['fees']['observed_usd_cost'] is None
    assert result['fees']['unresolved_count'] == 1


@pytest.mark.parametrize('pages,max_pages', [
    ([[fill('one')], [fill('one')]], 5),
    ([[fill('one')], [fill('two')]], 2),
    ([[fill('one')], RuntimeError('private-token-and-url')], 5),
    ([[fill('one')], {'not': 'a list'}], 5),
])
def test_partial_pages_never_commit_half_a_refresh(tmp_path, pages, max_pages):
    ledger = ActivityLedger(tmp_path / 'ledger.db', page_size=1, max_pages=max_pages)
    result = ledger.refresh(Feeds(pages), REF, NOW)
    assert result['status'] == 'unavailable' and not result['data_complete']
    assert result['fills']['count'] is None
    assert 'private-token-and-url' not in json.dumps(result)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT count(*) FROM activity_records').fetchone()[0] == 0


def test_failure_preserves_previous_evidence_but_marks_it_stale(ledger):
    collect(ledger, [fill()])
    result = ledger.refresh(Feeds([RuntimeError('secret')]), REF, NOW + timedelta(minutes=5))
    assert result['status'] == 'stale' and not result['data_complete']
    assert result['fills']['count'] == 1
    assert result['last_success_at'] == NOW.isoformat()
    assert result['last_attempt_at'] == (NOW + timedelta(minutes=5)).isoformat()
    assert result['error'] and 'secret' not in result['error']
    assert collect(ledger, [fill()], now=NOW + timedelta(minutes=10))['status'] == 'current'


def test_identity_change_during_fetch_discards_results(ledger):
    feeds = Feeds([[fill()]], accounts=[account(), account('another-account')])
    result = ledger.refresh(feeds, REF, NOW)
    assert result['status'] == 'unavailable'
    assert result['fills']['count'] is None
    assert ledger.summary('another-account', DAY, NOW)['status'] == 'unavailable'


def test_initial_wrong_identity_or_currency_never_queries_activities(ledger):
    for wrong in [account('wrong'), {**account(), 'currency': 'EUR'}]:
        feeds = Feeds([[fill()]], accounts=[wrong])
        assert ledger.refresh(feeds, REF, NOW)['status'] == 'unavailable'
        assert feeds.calls == []


def test_different_accounts_keep_activity_ids_and_equity_separate(ledger):
    collect(ledger, [fill(qty='0.002')])
    other = collect(ledger, [fill(qty='0.004')], ref='second-account')
    two_equities(ledger)
    one = ledger.summary(REF, DAY, NOW)
    assert one['account_scope'] == sha256(REF.encode()).hexdigest()
    assert one['account_scope'] != other['account_scope']
    assert Decimal(one['fills']['executed_notional_usd']) == 5
    assert Decimal(other['fills']['executed_notional_usd']) == 10
    assert other['account_change']['start_at'] is None


@pytest.mark.parametrize('when,expected_after', [
    ('2026-03-10T12:00:00+00:00', '2026-03-03T04:59:59.999999+00:00'),
    ('2026-11-03T12:00:00+00:00', '2026-10-27T03:59:59.999999+00:00'),
])
def test_overlap_uses_eight_ny_calendar_dates_across_dst(ledger, when, expected_after):
    now = datetime.fromisoformat(when)
    feeds = Feeds()
    ledger.refresh(feeds, REF, now)
    params = feeds.calls[0]
    assert params['after'] == expected_after and params['until'] == when
    assert params['direction'] == 'asc' and params['page_size'] == 100


def test_execution_day_is_new_york_not_utc(ledger):
    collect(ledger, [fill(transaction_time='2026-09-19T02:00:00Z')])
    assert ledger.summary(REF, '2026-09-18', NOW)['fills']['count'] == 1
    assert ledger.summary(REF, DAY, NOW)['fills']['count'] == 0


@pytest.mark.parametrize('when', [None, 'bad', '2026-09-19', '2026-09-20T02:00:00Z'])
def test_unattributed_fill_dates_make_unknown_totals_visible(ledger, when):
    result = collect(ledger, [fill(transaction_time=when)])
    assert result['unattributed_activity_count'] == 1
    assert result['status'] == 'partial' and result['fills']['executed_notional_usd'] is None
    assert result['fees']['observed_usd_cost'] is None


def test_stale_collection_is_not_current_even_without_recorded_error(ledger):
    collect(ledger, [fill()])
    result = ledger.summary(REF, DAY, NOW + timedelta(minutes=16))
    assert result['status'] == 'stale' and not result['data_complete']
    assert result['fills']['count'] == 1 and result['error'] is None


def test_output_and_database_do_not_retain_broker_descriptions_or_raw_identities(ledger):
    result = collect(ledger, [fill(description='private bank account secret'), fee(description='private fee info')])
    text = json.dumps(result) + ledger.path.read_bytes().decode('latin1')
    for secret in [REF, 'fill-private-id', 'fee-private-id', 'order-private-id',
                   'private bank account secret', 'private fee info']:
        assert secret not in text
    assert os.stat(ledger.path).st_mode & 0o777 == 0o600


def test_equity_first_last_retained_despite_out_of_order_samples(ledger):
    two_equities(ledger)
    ledger.record_equity(REF, account(equity='95'), NOW - timedelta(minutes=90))
    collect(ledger, [])
    result = ledger.summary(REF, DAY, NOW)['account_change']
    assert result['start_at'] == (NOW - timedelta(hours=2)).isoformat()
    assert result['end_at'] == (NOW - timedelta(hours=1)).isoformat()
    assert result['start_equity_usd'] == '92' and result['end_equity_usd'] == '96'
    assert Decimal(result['equity_change_usd']) == 4
    assert Decimal(result['cash_flow_adjusted_change_usd']) == 4
    assert result['status'] == 'observed' and result['partial_day'] is True


def test_one_equity_sample_is_not_zero_account_movement(ledger):
    ledger.record_equity(REF, account(), NOW)
    result = collect(ledger, [])['account_change']
    assert result['start_equity_usd'] == '92'
    assert result['equity_change_usd'] is None
    assert result['cash_flow_adjusted_change_usd'] is None


def test_equity_movement_subtracts_only_transfers_inside_capture_interval(ledger):
    two_equities(ledger, last='111')
    rows = [transfer('before', transaction_time=(NOW - timedelta(hours=3)).isoformat()),
            transfer('at-start', transaction_time=(NOW - timedelta(hours=2)).isoformat()),
            transfer('inside', transaction_time=(NOW - timedelta(minutes=90)).isoformat()),
            transfer('at-end', 'CSW', transaction_time=(NOW - timedelta(hours=1)).isoformat(), net_amount='-2'),
            transfer('after', transaction_time=(NOW - timedelta(minutes=30)).isoformat()),
            fee()]
    result = collect(ledger, rows)['account_change']
    assert Decimal(result['equity_change_usd']) == 19
    assert Decimal(result['cash_flow_adjusted_change_usd']) == 1
    # Fee is already reflected in account equity; do not subtract it again.
    assert result['status'] == 'observed'


@pytest.mark.parametrize('item', [
    transfer(), transfer(date='2026-09-19T00:00:00Z'),
    transfer(kind='JNLC', transaction_time='2026-09-19T18:30:00Z'),
    transfer(transaction_time='bad'),
    transfer(transaction_time='2026-09-19T18:30:00Z', status='pending'),
])
def test_ambiguous_cash_flows_prevent_adjusted_account_change(ledger, item):
    two_equities(ledger)
    result = collect(ledger, [item])['account_change']
    assert Decimal(result['equity_change_usd']) == 4
    assert result['cash_flow_adjusted_change_usd'] is None
    assert result['status'] == 'cash_flows_unverified'


def test_unknown_activity_kind_does_not_allow_an_assumed_cash_flow_adjustment(ledger):
    two_equities(ledger)
    result = collect(ledger, [transfer(kind='FUTURETYPE', transaction_time='2026-09-19T18:30:00Z')])
    assert result['status'] == 'partial'
    assert result['account_change']['cash_flow_adjusted_change_usd'] is None


def test_future_effective_date_is_unattributed_evidence(ledger):
    result = collect(ledger, [fee(date='2030-01-01')])
    assert result['status'] == 'partial' and result['unattributed_activity_count'] == 1
    assert result['fees']['observed_usd_cost'] is None


def test_date_only_next_utc_day_transfer_ambiguity_is_not_missed(ledger):
    start = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    end, now = start + timedelta(hours=1), start + timedelta(hours=2)
    two_equities(ledger, start=start, end=end)
    collect(ledger, [transfer(date='2026-09-20')], now=now)
    result = ledger.summary(REF, DAY, now)['account_change']
    assert Decimal(result['equity_change_usd']) == 4
    assert result['cash_flow_adjusted_change_usd'] is None


def test_stale_or_not_yet_covered_account_change_does_not_assume_zero_transfers(ledger):
    collect(ledger, [])
    two_equities(ledger, end=NOW + timedelta(seconds=1))
    assert ledger.summary(REF, DAY, NOW)['account_change']['cash_flow_adjusted_change_usd'] is None
    later = NOW + timedelta(minutes=2)
    assert collect(ledger, [], now=later)['account_change']['cash_flow_adjusted_change_usd'] is not None
    result = ledger.summary(REF, DAY, later + timedelta(minutes=16))
    assert result['account_change']['cash_flow_adjusted_change_usd'] is None


def test_equity_storage_is_bounded_by_365_ny_dates(ledger):
    for offset in range(370):
        ledger.record_equity(REF, account(), NOW - timedelta(days=offset))
    with sqlite3.connect(ledger.path) as db:
        count, earliest, latest = db.execute('SELECT count(*),min(day),max(day) FROM activity_equity').fetchone()
    assert count == 365 and latest == DAY
    assert earliest == (NOW.astimezone(NY).date() - timedelta(days=364)).isoformat()


@pytest.mark.parametrize('bad', [account('different'), {**account(), 'currency': 'EUR'},
                                account(equity='NaN'), account(equity=True)])
def test_equity_requires_verified_account_currency_and_finite_value(ledger, bad):
    with pytest.raises(ValueError):
        ledger.record_equity(REF, bad, NOW)
    assert ledger.summary(REF, DAY, NOW)['account_change']['start_at'] is None


def test_symlink_ledger_path_is_refused(tmp_path):
    target = tmp_path / 'existing'
    target.write_text('leave untouched')
    link = tmp_path / 'link'
    link.symlink_to(target)
    with pytest.raises(OSError):
        ActivityLedger(link)
    assert target.read_text() == 'leave untouched'


def test_busy_refresh_does_not_start_a_second_broker_read(ledger):
    feeds = Feeds()
    ledger._refresh_lock.acquire()
    try:
        assert ledger.refresh(feeds, REF, NOW)['status'] == 'unavailable'
    finally:
        ledger._refresh_lock.release()
    assert feeds.account_calls == 0 and feeds.calls == []


def test_slow_complete_fetch_is_not_claimed_current(ledger, monkeypatch):
    times = iter([0, 1, 50])
    monkeypatch.setattr('pivot.activity_ledger.monotonic', lambda: next(times))
    assert ledger.refresh(Feeds(), REF, NOW)['status'] == 'unavailable'
