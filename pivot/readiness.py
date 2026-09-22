"""Plain-language Socrates order-readiness checklist for the owner (4.5.1).

Read-only presentation. It summarises state the service already holds (saved
permission, account snapshot, broker clock, data health, entry allowance,
update hold, exposure, worker progress, the current setup) plus one cached
asset lookup per Socrates instrument. Nothing here places, cancels or changes an
order, and nothing here decides whether an order may be sent: the executor
repeats every check against fresh broker reads before any POST.

Wording is for a non-technical owner: plain sentences, no internal gate names.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .models import MAG7, timestamp

NEW_YORK = ZoneInfo('America/New_York')
ITEM_IDS = ('live_permission', 'strategy_enabled', 'account', 'buying_power', 'instrument_qqq',
            'instrument_psq', 'market_session', 'data_qqq', 'data_leaders', 'data_vix',
            'entry_allowance', 'deployment', 'exposure', 'workers', 'setup')
STATUSES = ('ok', 'warn', 'fail', 'info')
# App choices for the checklist only; they never gate an order.
ACCOUNT_FRESH_SECONDS = 60
ENTRY_CUTOFF_SECONDS = 1800  # Mirrors execution.ENTRY_CUTOFF_SECONDS (no new entries in the final 30 minutes).
VIX_BUDGET_LOW = 60
WORKERS = (('execution', 'placing and managing orders'), ('data', 'reading market data'),
           ('account', 'reading the Alpaca account'))
RESTART_ACTION = 'If this does not clear within a few minutes, restart Pivot on AllSpark.'
SETUP_STATES = {
    'WATCHING': 'Watching the marked QQQ levels',
    'AT_LEVEL': 'QQQ is at a marked level',
    'WAITING_FOR_RETEST': 'QQQ broke a marked level; waiting for it to come back to it',
    'CONFIRMING': 'QQQ is back at the level; checking the tech leaders and the VIX',
    'SETUP_READY': 'A setup is ready',
}
SETUP_CHECKS = {
    'Current Nasdaq observation': 'waiting for fresh QQQ hourly candles',
    'Premarked levels': 'waiting for marked QQQ price levels',
    'Nasdaq level event': "waiting for QQQ to break a marked level and return to it, or to sweep the previous day's high or low",
    'Magnificent Seven at their zones': 'waiting for at least four of the seven tech leaders to agree on a direction',
    'Actual VIX zone reaction': 'waiting for the VIX to react the opposite way at one of its levels',
    'Stop and target': 'waiting for a stop and a target with at least as much reward as risk',
}
METHODS = {'four_hour_retest': 'break and return to a four-hour level', 'prior_day_sweep': "sweep of the previous day's high or low"}


def _item(identifier, label, status, detail, action=None):
    return {'id': identifier, 'label': label, 'status': status, 'detail': detail}, action


def _clock_text(value, *, with_day=False):
    at = timestamp(value).astimezone(NEW_YORK)
    hour = at.hour % 12 or 12
    text = f'{hour}:{at.minute:02d} {"AM" if at.hour < 12 else "PM"} ET'
    return (at.strftime('%a %b ') + str(at.day) + ', ' + text) if with_day else text


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount.is_finite() else None


def _dollars(amount):
    return f'${amount.quantize(Decimal(".01")):,}'


def _joined(parts):
    return parts[0] if len(parts) == 1 else ', '.join(parts[:-1]) + ' and ' + parts[-1]


def _fresh(at, now, seconds):
    try:
        return 0 <= (now - timestamp(at)).total_seconds() <= seconds
    except (TypeError, ValueError, OverflowError):
        return False


def _live_permission(inputs):
    control = inputs.get('control') or {}
    if not inputs.get('execution_available'):
        return _item('live_permission', 'Live money permission', 'fail',
                     'Order sending is not configured on this server.',
                     'Ask for the Alpaca live trading connection to be configured on AllSpark.')
    if control.get('enabled') is not True:
        return _item('live_permission', 'Live money permission', 'fail', 'Live money is Off, so no order can be sent.',
                     'Turn Live money On in the header: read the rules, tick the box and confirm.')
    if control.get('policy') != inputs.get('policy_version'):
        return _item('live_permission', 'Live money permission', 'fail',
                     'Live money is saved On, but the updated Socrates rules have not been accepted yet.',
                     'Accept the updated Socrates rules: click Live money in the header, read, tick, confirm.')
    return _item('live_permission', 'Live money permission', 'ok', 'Live money is On under the current Socrates rules.')


def _strategy_enabled(inputs):
    selected = inputs.get('socrates_selected')
    if selected is None:
        return _item('strategy_enabled', 'Socrates switched on', 'fail',
                     'The Socrates on/off setting could not be read, so new entries wait.', RESTART_ACTION)
    if selected is not True:
        reason = inputs.get('pause_reason')
        detail = ('Socrates is Off. Pivot paused it: ' + reason[:300]) if reason else 'Socrates is turned Off.'
        return _item('strategy_enabled', 'Socrates switched on', 'fail', detail, 'Turn Socrates back On in its card.')
    return _item('strategy_enabled', 'Socrates switched on', 'ok', 'Socrates is On.')


def _account(inputs, now):
    account = inputs.get('account')
    label = 'Alpaca account'
    if not isinstance(account, dict) or not account:
        return _item('account', label, 'warn', 'Waiting for the first Alpaca account update.')
    if account.get('mode') not in (None, 'live'):
        return _item('account', label, 'fail', 'Pivot is connected to a paper account, not the live account.',
                     'Connect the live Alpaca account on AllSpark.')
    if (account.get('status') != 'ACTIVE' or account.get('trading_blocked') is not False
            or account.get('account_blocked') is not False or account.get('trade_suspended_by_user') is not False):
        return _item('account', label, 'fail', 'Alpaca reports that this account cannot trade right now.',
                     'Check the account status in the Alpaca dashboard.')
    control = inputs.get('control') or {}
    if control.get('enabled') is True and control.get('account_ref') and account.get('account_ref') != control['account_ref']:
        return _item('account', label, 'fail', 'The connected Alpaca account is not the one Live money was accepted for.',
                     'Turn Live money Off and On again to confirm the connected account.')
    if inputs.get('account_error') or not _fresh(inputs.get('account_at'), now, ACCOUNT_FRESH_SECONDS):
        return _item('account', label, 'warn', 'The latest account update is delayed; showing the last confirmed one.')
    return _item('account', label, 'ok', 'The live account is active and allowed to trade.')


def _buying_power(inputs):
    label = 'Money available'
    target = _money(inputs.get('target_dollars'))
    available = _money(inputs.get('available_dollars'))
    if target is None:
        return _item('buying_power', label, 'warn', 'The Socrates purchase size could not be read.')
    if available is None:
        return _item('buying_power', label, 'warn', 'Waiting for a current account balance.')
    if available < target:
        return _item('buying_power', label, 'fail',
                     f'Only {_dollars(available)} is free; each Socrates purchase needs {_dollars(target)}.',
                     'Deposit funds or lower the purchase size.')
    return _item('buying_power', label, 'ok',
                 f'{_dollars(available)} is free; each Socrates purchase uses {_dollars(target)}.')


def _instrument(inputs, symbol):
    identifier = 'instrument_' + symbol.lower()
    long_side = symbol == 'QQQ'
    label = 'QQQ can be bought' if long_side else 'PSQ can be bought (for shorts)'
    role = 'long setups' if long_side else 'short setups (PSQ rises when the Nasdaq falls)'
    cached = (inputs.get('assets') or {}).get(symbol) or {}
    asset = cached.get('asset')
    if not isinstance(asset, dict):
        return _item(identifier, label, 'warn', f'Alpaca has not confirmed {symbol} yet; the order check repeats it before buying.')
    if asset.get('status') != 'active' or asset.get('tradable') is not True:
        detail = f'Alpaca reports {symbol} is not tradable right now, so {role} are skipped.'
        if long_side:
            return _item(identifier, label, 'fail', detail, 'Nothing to change in Pivot; check QQQ in the Alpaca dashboard.')
        return _item(identifier, label, 'warn', detail)
    if asset.get('fractionable') is not True:
        return _item(identifier, label, 'warn',
                     f'{symbol} can only be bought in whole shares right now; a small purchase may be skipped.')
    return _item(identifier, label, 'ok', f'{symbol} is active and can be bought in dollar amounts for {role}.')


def _market(inputs, now):
    label = 'Market hours'
    clock = inputs.get('clock')
    if not isinstance(clock, dict) or type(clock.get('is_open')) is not bool or not _fresh(clock.get('timestamp'), now, 90):
        return _item('market_session', label, 'warn', 'Waiting for a current market clock from Alpaca.'), None
    try:
        if clock['is_open']:
            closes = timestamp(clock['next_close'])
            if (closes - now).total_seconds() <= ENTRY_CUTOFF_SECONDS:
                return _item('market_session', label, 'info',
                             f'The market closes at {_clock_text(closes)}; no new entries in the final 30 minutes.'), True
            return _item('market_session', label, 'ok', f'The market is open until {_clock_text(closes)}.'), True
        return _item('market_session', label, 'info',
                     f'The market is closed. Next open: {_clock_text(clock["next_open"], with_day=True)}.'), False
    except (KeyError, TypeError, ValueError, OverflowError):
        state = 'open' if clock['is_open'] else 'closed'
        return _item('market_session', label, 'info', f'The market is {state}.'), clock['is_open']


def _data_problem(label, identifier, detail, market_open):
    if market_open:
        return _item(identifier, label, 'fail', detail, 'No action is needed if this clears within a few minutes. ' + RESTART_ACTION)
    return _item(identifier, label, 'warn', detail + ' It refreshes when the market is open.')


def _instruments(inputs):
    health = inputs.get('data_health')
    stocks = health.get('stocks') if isinstance(health, dict) else None
    rows = stocks.get('instruments') if isinstance(stocks, dict) else None
    return {row.get('symbol'): row for row in rows if isinstance(row, dict)} if isinstance(rows, list) else None


def _data_qqq(inputs, market_open):
    label = 'QQQ price data'
    rows = _instruments(inputs)
    if rows is None:
        return _item('data_qqq', label, 'warn', 'Waiting for the first market-data update.')
    row = rows.get('QQQ') or {}
    if row.get('status') == 'current':
        return _item('data_qqq', label, 'ok', 'QQQ 15-minute, hourly, four-hour and daily candles are current.')
    behind = [frame.get('label') for frame in row.get('frames') or [] if isinstance(frame, dict)
              and frame.get('required', True) and frame.get('status') != 'current' and frame.get('label')]
    detail = ('Waiting for current QQQ ' + ', '.join(behind).lower() + ' candles.') if behind else 'Waiting for a current QQQ update.'
    return _data_problem(label, 'data_qqq', detail, market_open)


def _data_leaders(inputs, market_open):
    label = 'Tech leaders data'
    rows = _instruments(inputs)
    if rows is None:
        return _item('data_leaders', label, 'warn', 'Waiting for the first market-data update.')
    behind = [symbol for symbol in MAG7 if (rows.get(symbol) or {}).get('status') != 'current']
    if not behind:
        return _item('data_leaders', label, 'ok', 'All seven tech leaders have current 5-minute candles.')
    return _data_problem(label, 'data_leaders', 'Waiting for current 5-minute candles from ' + ', '.join(behind) + '.', market_open)


def _data_vix(inputs, market_open):
    label = 'VIX data'
    health = inputs.get('data_health')
    vix = health.get('vix') if isinstance(health, dict) else None
    if not isinstance(vix, dict):
        return _item('data_vix', label, 'warn', 'Waiting for the first VIX update.')
    budget = ((vix.get('verification') or {}).get('budget') or {}) if isinstance(vix.get('verification'), dict) else {}
    remaining, limit = budget.get('remaining'), budget.get('limit')
    counted = type(remaining) is int and type(limit) is int
    allowance = f' {remaining} of {limit} free VIX requests are left this month.' if counted else ''
    if counted and remaining <= 0:
        return _item('data_vix', label, 'fail', 'The free monthly VIX allowance is used up, so entries wait until it resets.',
                     'Nothing to change in Pivot; the VIX allowance resets next month.')
    if vix.get('status') == 'current':
        status = 'warn' if counted and remaining < VIX_BUDGET_LOW else 'ok'
        return _item('data_vix', label, status, 'Actual VIX candles are current.' + allowance)
    if vix.get('status') == 'market_closed':
        return _item('data_vix', label, 'info', 'The VIX market is closed.' + allowance)
    return _data_problem(label, 'data_vix', 'Waiting for current actual VIX candles.' + allowance, market_open)


def _allowance(inputs):
    label = 'Entries left today'
    allowance = inputs.get('entry_allowance') or {}
    family = (allowance.get('families') or {}).get('socrates') if isinstance(allowance.get('families'), dict) else None
    if allowance.get('status') != 'available' or not isinstance(family, dict) or type(family.get('remaining')) is not int:
        return _item('entry_allowance', label, 'fail', 'Pivot could not confirm how many Socrates entries are left today.', RESTART_ACTION)
    limit = allowance.get('limit') if type(allowance.get('limit')) is int else 2
    if family['remaining'] <= 0:
        return _item('entry_allowance', label, 'warn',
                     'Both Socrates attempts for today are used; new entries resume next session.')
    return _item('entry_allowance', label, 'ok', f'{family["remaining"]} of {limit} Socrates attempts are left today.')


def _deployment(inputs):
    label = 'App updates'
    gate = inputs.get('deployment_gate')
    if not isinstance(gate, dict):
        return _item('deployment', label, 'ok', 'No app update is holding entries (update coordination is not used here).')
    if gate.get('error'):
        return _item('deployment', label, 'fail', 'Pivot cannot read the update lock, so new entries wait.', RESTART_ACTION)
    if gate.get('hold_present') or gate.get('locked'):
        return _item('deployment', label, 'warn', 'An app update is being installed; new entries wait until it finishes.')
    return _item('deployment', label, 'ok', 'No app update is holding entries.')


def _exposure(inputs):
    label = 'Open positions and orders'
    exposure = inputs.get('exposure') or {}
    trade = exposure.get('active_trade')
    if isinstance(trade, dict):
        symbol = trade.get('symbol') if trade.get('symbol') in ('QQQ', 'PSQ') else 'QQQ'
        via = ' (the Socrates short via PSQ)' if trade.get('proxy') else ''
        return _item('exposure', label, 'info',
                     f'Socrates is managing its {symbol} trade{via}; the next entry waits until it closes.')
    if exposure.get('state') == 'unknown':
        return _item('exposure', label, 'warn', 'Waiting for a current list of positions and orders.')
    if exposure.get('state') == 'blocked':
        symbols = ', '.join(exposure.get('symbols') or []) or 'the account'
        return _item('exposure', label, 'fail',
                     f'Alpaca shows a position or order Pivot cannot account for ({symbols}), so new entries wait.',
                     'In Alpaca, close or cancel any position or order Pivot did not place.')
    return _item('exposure', label, 'ok', 'No other position or order is in the way; one Socrates trade at a time.')


def _workers(inputs):
    label = 'Background workers'
    health = inputs.get('worker_health') or {}
    rows = {row.get('name'): row for row in health.get('workers') or [] if isinstance(row, dict)}
    failing, starting = [], []
    for name, text in WORKERS:
        status = (rows.get(name) or {}).get('status')
        if status == 'running':
            continue
        (starting if status in ('starting', 'not_started', None) else failing).append(text)
    if failing:
        return _item('workers', label, 'fail', 'Pivot has stopped ' + _joined(failing) + '.',
                     'Restart Pivot on AllSpark.')
    if starting:
        return _item('workers', label, 'warn', 'Pivot is still starting: ' + _joined(starting) + '.')
    return _item('workers', label, 'ok', 'Pivot is reading data, watching the account and managing orders.')


def _setup(inputs):
    label = 'Socrates setup'
    setup = inputs.get('setup')
    if not isinstance(setup, dict) or not setup.get('state'):
        return _item('setup', label, 'warn', 'Waiting for the first strategy analysis.'), False
    if setup['state'] == 'SETUP_READY':
        direction = 'long QQQ' if setup.get('direction') == 'long' else 'short (bought as PSQ)'
        method = METHODS.get(setup.get('strategy_id'), 'marked level')
        return _item('setup', label, 'ok', f'A {direction} setup is ready ({method}).'), True
    checks = setup.get('checks') if isinstance(setup.get('checks'), list) else []
    blocker = next((SETUP_CHECKS.get(check.get('name')) for check in checks if isinstance(check, dict)
                    and check.get('passed') is not True and check.get('name') in SETUP_CHECKS), None)
    state = SETUP_STATES.get(setup['state'], 'Watching the marked QQQ levels')
    return _item('setup', label, 'info', state + ('; ' + blocker if blocker else '') + '.'), False


def build_socrates_readiness(inputs, now=None):
    """Checklist in the contract order; status is blocked, waiting or ready.

    ``inputs`` is a plain dict assembled by the service; missing fields degrade
    to 'warn' items rather than raising.
    """
    now = now or datetime.now(timezone.utc)
    market, market_open = _market(inputs, now)
    setup, setup_ready = _setup(inputs)
    results = [
        _live_permission(inputs), _strategy_enabled(inputs), _account(inputs, now), _buying_power(inputs),
        _instrument(inputs, 'QQQ'), _instrument(inputs, 'PSQ'), market,
        _data_qqq(inputs, market_open is True), _data_leaders(inputs, market_open is True),
        _data_vix(inputs, market_open is True), _allowance(inputs), _deployment(inputs),
        _exposure(inputs), _workers(inputs), setup,
    ]
    items = [item for item, _ in results]
    failures = [(item, action) for item, action in results if item['status'] == 'fail']
    warnings = sum(item['status'] == 'warn' for item in items)
    if failures:
        status = 'blocked'
        first = failures[0][0]
        headline = f'Socrates cannot place orders yet: {first["label"].lower()} needs attention.'
    elif all(item['status'] == 'ok' for item in items) and setup_ready and market_open is True:
        status = 'ready'
        headline = 'A Socrates setup is ready; the order goes out after the final price checks.'
    else:
        status = 'waiting'
        if market_open is False:
            headline = 'All set for the next session; the market is closed.'
        elif isinstance((inputs.get('exposure') or {}).get('active_trade'), dict):
            headline = 'Managing the open Socrates trade; no new entry until it closes.'
        elif setup_ready:
            headline = 'A setup is ready, but an item below still needs to clear.'
        else:
            headline = 'Ready to trade; watching for a Socrates setup.'
        if warnings:
            headline = headline[:-1] + f' ({warnings} item{"s" if warnings != 1 else ""} to watch).'
    return {'checked_at': now.isoformat(), 'status': status, 'headline': headline,
            'next_action': failures[0][1] if failures else None, 'items': items}
