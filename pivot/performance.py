"""Verified gross trade results from cumulative, confirmed broker fill observations.

No fee data is supplied by this ledger. These results are never net profit.
Unknown execution outcomes deliberately produce no profit figure.
"""
from decimal import Decimal, DecimalException, localcontext
import re

from .models import timestamp

TERMINAL = frozenset({'filled', 'canceled', 'expired', 'rejected'})


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing broker quantity or price')
    try:
        value = Decimal(str(value))
    except DecimalException:
        raise ValueError('Invalid broker quantity or price') from None
    if not value.is_finite():
        raise ValueError('Non-finite broker quantity or price')
    return value


def _fill(op, symbol, side, client_ids, broker_ids):
    """Read one operation's latest cumulative fill exactly once."""
    if not isinstance(op, dict):
        raise ValueError('Invalid operation')
    if op.get('state') == 'prepared':
        if op.get('last_seen'):
            raise ValueError('Unsubmitted operation cannot have broker fills')
        return Decimal(0), Decimal(0)
    if op.get('state') not in ('attempted', 'rejected'):
        raise ValueError('Unknown operation state')
    payload = op.get('payload')
    if not isinstance(payload, dict):
        raise ValueError('Missing order identity')
    client_id = payload.get('client_order_id')
    if not isinstance(client_id, str) or not client_id or client_id in client_ids:
        raise ValueError('Missing or duplicated order identity')
    client_ids.add(client_id)
    if payload.get('symbol') != symbol or payload.get('side') != side:
        raise ValueError('Order intent does not match the trade')
    seen = op.get('last_seen')
    if not isinstance(seen, dict):
        raise ValueError('Order outcome is unverified')
    if seen.get('symbol') != symbol or seen.get('side') != side or seen.get('status') not in TERMINAL:
        raise ValueError('Order outcome or identity is unverified')
    # Optional response identifiers are checked when a provider includes them.
    if seen.get('client_order_id') not in (None, client_id):
        raise ValueError('Broker order identity differs from its intent')
    broker_id = seen.get('id')
    if broker_id is not None:
        if not isinstance(broker_id, str) or not broker_id or broker_id in broker_ids:
            raise ValueError('Duplicated broker order')
        broker_ids.add(broker_id)
    quantity = _number(seen.get('filled_qty'))
    if quantity < 0:
        raise ValueError('Filled quantity cannot be negative')
    if seen.get('qty') is not None:
        ordered = _number(seen['qty'])
        if ordered < quantity or ordered < 0:
            raise ValueError('Filled quantity exceeds its order')
    if quantity == 0:
        if seen['status'] == 'filled':
            raise ValueError('A filled order must identify its filled shares')
        return quantity, Decimal(0)
    if op['state'] == 'rejected' or seen['status'] == 'rejected':
        raise ValueError('Rejected order has inconsistent fills')
    price = _number(seen.get('filled_avg_price'))
    if price <= 0:
        raise ValueError('Filled order needs a positive average price')
    return quantity, quantity * price


PROXY_LABEL = 'Socrates short via PSQ (inverse QQQ)'


def socrates_labels(trade):
    """How a Socrates trade reads to the owner: the QQQ signal and the traded instrument.

    Since 4.5.0 a QQQ short setup is executed by buying PSQ, the -1x inverse
    ETF. Such a record is a PSQ purchase ('symbol' PSQ, 'direction' long) of a
    QQQ short signal; it must never read as a plain 'PSQ long'. Only known
    values pass through; anything else is None.
    """
    trade = trade if isinstance(trade, dict) else {}
    symbol = trade.get('symbol') if trade.get('symbol') in ('QQQ', 'PSQ') else None
    signal_symbol = 'QQQ' if trade.get('signal_symbol') == 'QQQ' or symbol == 'QQQ' else None
    signal_direction = trade.get('signal_direction') if trade.get('signal_direction') in ('long', 'short') else None
    proxy = trade.get('proxy') == 'inverse_etf' and symbol == 'PSQ' and signal_direction == 'short'
    if signal_direction is None and symbol == 'QQQ' and trade.get('direction') in ('long', 'short'):
        signal_direction = trade['direction']  # Records before 4.5.0 traded QQQ in the signal's direction.
    if proxy:
        label = PROXY_LABEL
    elif symbol == 'QQQ' and signal_direction:
        label = f'Socrates {signal_direction} QQQ'
    else:
        label = None
    return {'label': label, 'signal_symbol': signal_symbol, 'signal_direction': signal_direction,
            'proxy': 'inverse_etf' if proxy else None}


def trade_result(trade):
    """Return a small display-safe result, with no P&L for uncertain outcomes.

    Completed quantity must match entry fills and the sum of all final exit fills.
    Cumulative fills are taken once per unique operation, never once per poll.
    Manual reconciliation, replacement, unfinished orders or missing fill prices
    cannot establish profit. Unfilled terminal operations need no average price.
    """
    trade = trade if isinstance(trade, dict) else {}
    symbol = trade.get('symbol')
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9./-]{0,14}', symbol):
        symbol = None
    direction = trade.get('direction') if trade.get('direction') in ('long', 'short') else None
    try:
        completed_at = timestamp(trade['completed_at']).isoformat()
    except (KeyError, TypeError, ValueError, OverflowError):
        completed_at = None
    result = {'symbol': symbol, 'direction': direction, 'completed_at': completed_at,
              'quantity': None, 'gross_pnl': None, 'status': 'unverified',
              'fees_status': 'not_reported', **socrates_labels(trade)}
    if trade.get('stage') != 'finished' or trade.get('manual_reconciliation') or not symbol or not direction or not completed_at:
        return result
    try:
        ops = trade['ops']
        if not isinstance(ops, dict) or 'entry' not in ops:
            return result
        entry_side = 'buy' if direction == 'long' else 'sell'
        exit_side = 'sell' if direction == 'long' else 'buy'
        client_ids, broker_ids = set(), set()
        with localcontext() as context:
            context.prec = 128
            quantity, entry_value = _fill(ops['entry'], symbol, entry_side, client_ids, broker_ids)
            if quantity <= 0:
                return result
            result['quantity'] = str(quantity)
            if trade.get('filled_qty') is not None and _number(trade['filled_qty']) != quantity:
                return result
            exit_quantity, exit_value = Decimal(0), Decimal(0)
            for name, op in ops.items():
                if name == 'entry':
                    continue
                filled, value = _fill(op, symbol, exit_side, client_ids, broker_ids)
                exit_quantity += filled
                exit_value += value
            if exit_quantity != quantity:
                return result
            gross = exit_value - entry_value if direction == 'long' else entry_value - exit_value
            result.update(gross_pnl=str(gross), status='verified_gross')
    except (KeyError, TypeError, ValueError, DecimalException, OverflowError):
        # Do not invent a break-even result when a broker field is absent or invalid.
        pass
    return result
