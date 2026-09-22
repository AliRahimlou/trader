"""Gross result checks use only synthetic confirmed broker observations."""
from copy import deepcopy
from decimal import Decimal

import pytest

from pivot.performance import trade_result


DONE = '2026-09-16T17:00:00+00:00'


def operation(name, side, qty, price, status='filled', ordered=None):
    return {'state': 'attempted',
            'payload': {'symbol': 'QQQ', 'side': side, 'client_order_id': 'test-' + name},
            'last_seen': {'symbol': 'QQQ', 'side': side, 'status': status,
                          'qty': str(ordered if ordered is not None else qty),
                          'filled_qty': str(qty), 'filled_avg_price': price,
                          'updated_at': DONE, 'filled_at': DONE}}


def finished(direction='long'):
    entry_side, exit_side = ('buy', 'sell') if direction == 'long' else ('sell', 'buy')
    return {'symbol': 'QQQ', 'direction': direction, 'stage': 'finished', 'completed_at': DONE,
            'filled_qty': '0.25', 'ops': {
                'entry': operation('entry', entry_side, '0.25', '100'),
                'exit0': operation('exit0', exit_side, '0.25', '104'),
            }}


def test_confirmed_long_and_short_use_opposite_cash_flow():
    long = trade_result(finished())
    short = trade_result(finished('short'))
    assert long == {'symbol': 'QQQ', 'direction': 'long', 'completed_at': DONE,
                    'quantity': '0.25', 'gross_pnl': '1.00', 'status': 'verified_gross', 'fees_status': 'not_reported',
                    'label': 'Socrates long QQQ', 'signal_symbol': 'QQQ', 'signal_direction': 'long', 'proxy': None}
    assert Decimal(short['gross_pnl']) == Decimal('-1')
    assert short['status'] == 'verified_gross'
    assert 'net_pnl' not in long


def test_partial_stop_and_later_exit_sum_final_cumulative_fills_once():
    t = finished()
    t['ops']['stop'] = operation('stop', 'sell', '0.10', '90', status='canceled', ordered='0.25')
    t['ops']['exit0'] = operation('exit0', 'sell', '0.15', '110')
    result = trade_result(t)
    assert result['status'] == 'verified_gross'
    assert Decimal(result['gross_pnl']) == Decimal('0.5')
    assert trade_result(t) == result  # Re-reading a final observation does not accumulate it again.


def test_partial_canceled_entry_uses_actual_quantity_not_requested_quantity():
    t = finished()
    t['filled_qty'] = '0.1'
    t['ops']['entry'] = operation('entry', 'buy', '0.1', '100', status='canceled', ordered='0.25')
    t['ops']['exit0'] = operation('exit0', 'sell', '0.1', '101')
    result = trade_result(t)
    assert result['quantity'] == '0.1'
    assert Decimal(result['gross_pnl']) == Decimal('0.1')


def test_exact_decimal_arithmetic_preserves_fractional_gross_before_display_rounding():
    t = finished()
    t['filled_qty'] = '0.123456789'
    t['ops']['entry'] = operation('entry', 'buy', t['filled_qty'], '123.123456789')
    t['ops']['exit0'] = operation('exit0', 'sell', t['filled_qty'], '123.223456789')
    assert Decimal(trade_result(t)['gross_pnl']) == Decimal('0.0123456789')


@pytest.mark.parametrize('status', ['canceled', 'expired', 'rejected'])
def test_known_unfilled_terminal_orders_do_not_require_average_prices(status):
    t = finished()
    t['ops']['stop'] = operation('stop', 'sell', '0', None, status=status, ordered='0.25')
    assert trade_result(t)['status'] == 'verified_gross'


def test_prepared_unsubmitted_exit_is_ignored_but_inconsistent_prepared_fill_is_not():
    t = finished()
    t['ops']['stop'] = {'state': 'prepared', 'payload': {'symbol': 'QQQ', 'side': 'sell', 'client_order_id': 'test-stop'}}
    assert trade_result(t)['status'] == 'verified_gross'
    t['ops']['stop']['last_seen'] = operation('stop', 'sell', '0.25', '110')['last_seen']
    assert trade_result(t)['status'] == 'unverified'


@pytest.mark.parametrize('change', [
    lambda t: t.update(manual_reconciliation=True),
    lambda t: t.update(stage='open'),
    lambda t: t.pop('completed_at'),
    lambda t: t.update(completed_at='2026-09-16T17:00:00'),
    lambda t: t['ops']['exit0'].pop('last_seen'),
    lambda t: t['ops']['exit0']['last_seen'].update(status='replaced'),
    lambda t: t['ops']['exit0']['last_seen'].update(status='partially_filled'),
    lambda t: t['ops']['exit0']['last_seen'].update(status='new'),
    lambda t: t['ops']['exit0']['last_seen'].update(filled_avg_price=None),
    lambda t: t['ops']['entry']['last_seen'].update(filled_avg_price='NaN'),
    lambda t: t['ops']['exit0']['last_seen'].update(filled_avg_price='Infinity'),
    lambda t: t['ops']['exit0']['last_seen'].update(filled_avg_price='0'),
    lambda t: t['ops']['entry']['last_seen'].update(filled_qty='-1'),
    lambda t: t['ops']['exit0']['last_seen'].update(filled_qty='0.1'),
    lambda t: t['ops']['exit0']['last_seen'].update(filled_qty='0.5', qty='0.5'),
    lambda t: t['ops']['exit0']['last_seen'].update(qty='0.1'),
    lambda t: t['ops']['exit0']['last_seen'].update(symbol='AAPL'),
    lambda t: t['ops']['exit0']['last_seen'].update(side='buy'),
    lambda t: t['ops']['exit0']['payload'].update(side='buy'),
    lambda t: t['ops']['exit0']['last_seen'].update(client_order_id='different-intent'),
    lambda t: t['ops']['exit0'].update(state='unknown'),
    lambda t: t.update(filled_qty='0.2'),
])
def test_uncertain_missing_malformed_or_mismatched_fills_never_report_profit(change):
    t = finished()
    change(t)
    result = trade_result(t)
    assert result['status'] == 'unverified'
    assert result['gross_pnl'] is None
    assert result['fees_status'] == 'not_reported'


def test_duplicate_operation_identifiers_cannot_double_count_a_fill():
    t = finished()
    t['ops']['exit0'] = operation('exit0', 'sell', '0.125', '104')
    t['ops']['exit1'] = deepcopy(t['ops']['exit0'])
    assert trade_result(t)['status'] == 'unverified'
    t['ops']['exit1']['payload']['client_order_id'] = 'test-exit1'
    t['ops']['exit0']['last_seen']['id'] = 'same-broker-order'
    t['ops']['exit1']['last_seen']['id'] = 'same-broker-order'
    assert trade_result(t)['status'] == 'unverified'


def test_unfilled_entry_does_not_invent_break_even_profit():
    t = finished()
    t['ops']['entry'] = operation('entry', 'buy', '0', None, status='canceled', ordered='0.25')
    t['ops'].pop('exit0')
    result = trade_result(t)
    assert result['status'] == 'unverified' and result['gross_pnl'] is None


@pytest.mark.parametrize('raw', [None, [], {}, {'symbol': '<script>', 'direction': 'sideways'},
                               {'symbol': {'private': 'value'}, 'completed_at': 'bad'}])
def test_sanitized_summary_never_exposes_arbitrary_fields(raw):
    result = trade_result(raw)
    assert set(result) == {'symbol', 'direction', 'completed_at', 'quantity', 'gross_pnl', 'status', 'fees_status',
                           'label', 'signal_symbol', 'signal_direction', 'proxy'}
    assert result['symbol'] is None and result['gross_pnl'] is None
    assert result['label'] is result['signal_symbol'] is result['signal_direction'] is result['proxy'] is None


def test_result_computation_does_not_modify_the_trade():
    t = finished()
    before = deepcopy(t)
    trade_result(t)
    assert t == before


def proxy_trade():
    """A 4.5.0 QQQ short executed by buying PSQ: the record is a PSQ purchase."""
    t = finished()
    t.update(symbol='PSQ', signal_symbol='QQQ', signal_direction='short', proxy='inverse_etf')
    for op in t['ops'].values():
        op['payload']['symbol'] = op['last_seen']['symbol'] = 'PSQ'
    return t


def test_psq_proxy_result_reads_as_socrates_short_not_psq_long():
    result = trade_result(proxy_trade())
    assert result['label'] == 'Socrates short via PSQ (inverse QQQ)'
    assert (result['symbol'], result['direction']) == ('PSQ', 'long')  # The executed purchase stays visible.
    assert (result['signal_symbol'], result['signal_direction'], result['proxy']) == ('QQQ', 'short', 'inverse_etf')
    assert result['status'] == 'verified_gross' and Decimal(result['gross_pnl']) == Decimal('1')


@pytest.mark.parametrize('change', [{'proxy': None}, {'signal_direction': 'long'}, {'symbol': 'QQQ'}, {'proxy': 'leveraged'}])
def test_proxy_label_requires_consistent_proxy_evidence(change):
    t = proxy_trade()
    t.update(change)
    assert trade_result(t)['label'] != 'Socrates short via PSQ (inverse QQQ)'
