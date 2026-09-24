"""Live view of the open Socrates trade (4.5.2): read-only arithmetic, no broker calls.

The executor records the same bid and ask it uses to decide a target exit. This
module combines that quote with the durable trade record, the Alpaca position and
the broker clock, and returns what the owner watches:
- the entry and the current price;
- the profit or loss in dollars, percent and R (multiples of the planned risk);
- the distance to the stop and to the target;
- the three ways the position will be sold.
Missing or stale evidence is labelled, never filled in. Nothing here can change
a trade or an order.
"""
from datetime import datetime, timezone
from decimal import Decimal, DecimalException, ROUND_HALF_UP

from .models import timestamp
from .performance import socrates_labels

# Mirrors execution.closing(): the session-close exit starts five minutes before the close.
CLOSE_EXIT_SECONDS = 300
# A mark older than this reads as delayed. The executor itself acts only on quotes under 15 seconds old.
FRESH_SECONDS = 20
POSITION_FALLBACK_SECONDS = 60
TRACK_POINTS = 240
STAGES = {'entering': 'Buying', 'open': 'Holding', 'exiting': 'Selling', 'attention': 'Needs your attention'}
STOP_ORDER_TEXT = {
    'new': 'working at Alpaca', 'accepted': 'accepted by Alpaca', 'pending_new': 'being accepted by Alpaca',
    'partially_filled': 'partly filled', 'filled': 'filled: the stop sold the shares',
    'canceled': 'canceled', 'expired': 'expired', 'rejected': 'rejected by Alpaca',
    'pending_cancel': 'being canceled', 'done_for_day': 'done for the day', 'suspended': 'suspended',
}
WORKING_STOP = {'new', 'accepted', 'pending_new', 'partially_filled'}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (DecimalException, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _money(value, places='0.01'):
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP)) if value is not None else None


def _at(value):
    try:
        at = timestamp(value)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None
    return at if at.tzinfo is not None and at.utcoffset() is not None else None


def _ratio(part, whole, places='0.0001'):
    if part is None or whole is None or whole == 0:
        return None
    return str((part / whole).quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _entry_fill(trade, position):
    seen = ((trade.get('ops') or {}).get('entry') or {}).get('last_seen') or {}
    price, qty = _number(seen.get('filled_avg_price')), _number(trade.get('filled_qty') or seen.get('filled_qty'))
    source = 'fill'
    if (price is None or price <= 0) and position:
        price, source = _number(position.get('avg_entry_price')), 'position'
    if (qty is None or qty <= 0) and position:
        qty = _number(position.get('qty'))
    at = _at(seen.get('filled_at')) or _at(trade.get('created_at'))
    if price is None or price <= 0:
        price, source = None, None
    if qty is not None and qty <= 0:
        qty = None
    return price, qty, at, source


def _current(trade, mark, position, now):
    """Sale-side price: the bid for a held purchase (what selling would get), the ask for a short."""
    long = trade.get('direction') != 'short'
    if isinstance(mark, dict) and mark.get('trade_id') == trade.get('id') and mark.get('symbol') == trade.get('symbol'):
        price = _number(mark.get('bid') if long else mark.get('ask'))
        at = _at(mark.get('quote_at')) or _at(mark.get('at'))
        if price is not None and price > 0 and at is not None:
            age = (now - at).total_seconds()
            # While the manager is not reading quotes (selling, or quotes paused) the
            # account worker's position price, at most 15 seconds old, is newer.
            if age <= POSITION_FALLBACK_SECONDS or not _number((position or {}).get('current_price')):
                return {'price': price, 'bid': _number(mark.get('bid')), 'ask': _number(mark.get('ask')),
                        'at': at, 'age': age, 'source': 'quote'}
    price = _number((position or {}).get('current_price'))
    if price is not None and price > 0:
        return {'price': price, 'bid': None, 'ask': None, 'at': None, 'age': None, 'source': 'position'}
    return None


def _stop_order(trade, orders):
    op = (trade.get('ops') or {}).get('stop')
    if not isinstance(op, dict):
        return {'status': None, 'text': 'not placed yet: the app places it right after the purchase fills', 'working': False}
    if op.get('state') == 'prepared':
        return {'status': 'preparing', 'text': 'being placed', 'working': False}
    client_id = (op.get('payload') or {}).get('client_order_id')
    live = next((o for o in orders or [] if isinstance(o, dict) and client_id and o.get('client_order_id') == client_id), None)
    status = (live or {}).get('status') or (op.get('last_seen') or {}).get('status')
    text = STOP_ORDER_TEXT.get(status, status.replace('_', ' ') if isinstance(status, str) else 'status not confirmed yet')
    return {'status': status if isinstance(status, str) else None, 'text': text, 'working': status in WORKING_STOP}


def _session_exit(clock, now):
    close = _at((clock or {}).get('next_close'))
    if close is None or not (clock or {}).get('is_open'):
        return None
    exit_at = close.timestamp() - CLOSE_EXIT_SECONDS
    return {'at': datetime.fromtimestamp(exit_at, timezone.utc).isoformat(), 'close_at': close.isoformat(),
            'seconds_left': max(0, round(exit_at - now.timestamp()))}


def _track(track, trade, entry_price):
    points = [row for row in (track or []) if isinstance(row, dict) and row.get('trade_id') == trade.get('id')]
    if len(points) > TRACK_POINTS:
        step = len(points) / TRACK_POINTS
        points = [points[int(index * step)] for index in range(TRACK_POINTS - 1)] + [points[-1]]
    long = trade.get('direction') != 'short'
    out = []
    for row in points:
        price = _number(row.get('bid') if long else row.get('ask'))
        at = _at(row.get('quote_at')) or _at(row.get('at'))
        if price is not None and price > 0 and at is not None:
            out.append({'t': at.isoformat(), 'p': str(price)})
    return out


def build_live_trade(trade, *, mark=None, track=None, positions=None, orders=None, clock=None,
                     signal_quote=None, message=None, now=None):
    """Owner-facing summary of the one Socrates trade the app is managing; None when there is none."""
    if not isinstance(trade, dict) or trade.get('stage') not in STAGES:
        return None
    now = now or datetime.now(timezone.utc)
    symbol = trade.get('symbol') if isinstance(trade.get('symbol'), str) else None
    long = trade.get('direction') != 'short'
    position = next((p for p in positions or [] if isinstance(p, dict) and p.get('symbol') == symbol), None)
    entry_price, qty, entered_at, entry_source = _entry_fill(trade, position)
    stop, target = _number(trade.get('stop')), _number(trade.get('target'))
    current = _current(trade, mark, position, now)
    price = current['price'] if current else None
    labels = socrates_labels(trade)
    result = {
        'trade_id': trade.get('id') if isinstance(trade.get('id'), str) else None,
        'stage': trade['stage'], 'stage_text': STAGES[trade['stage']],
        'symbol': symbol, 'direction': 'short' if not long else 'long',
        'label': labels['label'] or (f'Socrates {symbol}' if symbol else 'Socrates trade'),
        'proxy': labels['proxy'] == 'inverse_etf',
        'signal_direction': labels['signal_direction'],
        'amount': _money(_number(trade.get('amount'))),
        'entry': {'price': str(entry_price) if entry_price else None, 'qty': str(qty) if qty else None,
                  'cost': _money(entry_price * qty) if entry_price and qty else None,
                  'at': entered_at.isoformat() if entered_at else None, 'source': entry_source},
        'current': None, 'pnl': None, 'stop': None, 'target': None, 'progress': None,
        'session_exit': _session_exit(clock, now), 'signal': None, 'exit': None, 'track': [],
        'message': message if isinstance(message, str) else None,
        'as_of': now.isoformat(),
    }
    if current:
        result['current'] = {'price': str(price), 'bid': str(current['bid']) if current['bid'] else None,
                             'ask': str(current['ask']) if current['ask'] else None,
                             'at': current['at'].isoformat() if current['at'] else None,
                             'source': current['source'],
                             'age_seconds': round(current['age']) if current['age'] is not None else None,
                             'fresh': current['source'] == 'quote' and current['age'] is not None and current['age'] <= FRESH_SECONDS}
    if entry_price and price:
        move = (price - entry_price) if long else (entry_price - price)
        risk = (entry_price - stop) if (stop and long) else (stop - entry_price) if stop else None
        result['pnl'] = {'dollars': _money(move * qty) if qty else None,
                         'per_share': str(move.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)),
                         'percent': _ratio(move * 100, entry_price, '0.01'),
                         'r': _ratio(move, risk, '0.01') if risk and risk > 0 else None}
    for name, level in (('stop', stop), ('target', target)):
        if level is None or level <= 0:
            continue
        row = {'price': str(level)}
        if entry_price and qty:
            change = (level - entry_price) if long else (entry_price - level)
            row['result_if_hit'] = _money(change * qty)
            row['result_percent'] = _ratio(change * 100, entry_price, '0.01')
        if price:
            # Positive: room left before the level is reached.
            gap = (price - level) if (name == 'stop') == long else (level - price)
            row['distance'] = str(gap.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
            row['distance_percent'] = _ratio(gap * 100, price, '0.01')
        result[name] = row
    if result['target'] is not None:
        from .strategy import KEY_LEVEL_NAMES
        source = trade.get('target_source') or (trade.get('signal_geometry') or {}).get('target_source')
        result['target']['source'] = KEY_LEVEL_NAMES.get(source, source) if isinstance(source, str) else None
    if result['stop'] is not None:
        result['stop']['order'] = _stop_order(trade, orders)
    if stop and target and price and stop != target:
        span = (target - stop) if long else (stop - target)
        where = ((price - stop) if long else (stop - price)) / span
        result['progress'] = str(max(Decimal(0), min(Decimal(1), where)).quantize(Decimal('0.001')))
    geometry = trade.get('signal_geometry') if isinstance(trade.get('signal_geometry'), dict) else None
    if result['proxy'] and geometry:
        qqq = _number((signal_quote or {}).get('bp'))
        result['signal'] = {'symbol': 'QQQ', 'direction': 'short', 'entry': geometry.get('entry'),
                            'stop': geometry.get('stop'), 'target': geometry.get('target'),
                            'current': str(qqq) if qqq and qqq > 0 else None,
                            'at': (signal_quote or {}).get('t') if qqq else None}
    if trade['stage'] == 'exiting':
        pending = trade.get('exit_pending') if isinstance(trade.get('exit_pending'), dict) else {}
        reason = trade.get('exit_reason') or trade.get('reason')
        result['exit'] = {'reason': reason if isinstance(reason, str) else 'Closing the position',
                          'started_at': pending.get('first_observed_at')}
    elif trade['stage'] == 'attention':
        result['exit'] = {'reason': trade.get('reason') if isinstance(trade.get('reason'), str) else 'Needs review in Alpaca',
                          'started_at': None}
    result['track'] = _track(track, trade, entry_price)
    return result


def build_last_trade(trade):
    """The most recent finished Socrates trade that bought shares: prices, result and why it sold.

    The gross result comes from performance.trade_result, which verifies every
    fill. The average prices shown next to it are read from the same broker fill
    records and are labelled unverified when that check fails.
    """
    if not isinstance(trade, dict) or trade.get('stage') != 'finished':
        return None
    from .performance import trade_result
    ops = trade.get('ops') if isinstance(trade.get('ops'), dict) else {}
    seen = (ops.get('entry') or {}).get('last_seen') or {}
    entry_qty, entry_price = _number(seen.get('filled_qty')), _number(seen.get('filled_avg_price'))
    if not entry_qty or entry_qty <= 0 or not entry_price or entry_price <= 0:
        return None
    exit_qty, exit_value, stop_filled = Decimal(0), Decimal(0), False
    for name, op in ops.items():
        if name == 'entry' or not isinstance(op, dict):
            continue
        fill = op.get('last_seen') or {}
        qty, price = _number(fill.get('filled_qty')), _number(fill.get('filled_avg_price'))
        if qty and qty > 0 and price and price > 0:
            exit_qty += qty
            exit_value += qty * price
            stop_filled = stop_filled or name == 'stop'
    result = trade_result(trade)
    reason = trade.get('exit_reason') if isinstance(trade.get('exit_reason'), str) else None
    if stop_filled and not reason:
        reason = 'The broker stop order sold the shares'
    exit_price = exit_value / exit_qty if exit_qty > 0 else None
    long = trade.get('direction') != 'short'
    percent = None
    if exit_price:
        move = (exit_price - entry_price) if long else (entry_price - exit_price)
        percent = _ratio(move * 100, entry_price, '0.01')
    entered = _at(seen.get('filled_at')) or _at(trade.get('created_at'))
    return {'trade_id': trade.get('id') if isinstance(trade.get('id'), str) else None,
            'label': result.get('label') or (f'Socrates {trade.get("symbol")}' if trade.get('symbol') else 'Socrates trade'),
            'symbol': result.get('symbol'), 'quantity': str(entry_qty),
            'entry_price': str(entry_price), 'entered_at': entered.isoformat() if entered else None,
            'exit_price': str(exit_price.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)) if exit_price else None,
            'completed_at': result.get('completed_at'),
            'gross_pnl': _money(_number(result.get('gross_pnl'))) if result.get('status') == 'verified_gross' else None,
            'percent': percent, 'status': result.get('status'), 'exit_reason': reason,
            'stop': trade.get('stop'), 'target': trade.get('target')}
