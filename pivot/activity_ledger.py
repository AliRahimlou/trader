"""Read-only, account-scoped broker evidence. Account movement is not trade P&L.

Activity queries filter creation dates; fee rows may arrive after their economic
date. Every refresh overlaps eight New York dates and commits only complete,
identity-verified pagination. No activity endpoint supplies fee finality.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

from .models import timestamp

NY = ZoneInfo('America/New_York')
UTC = timezone.utc
LOOKBACK_DATES = 8
FRESH_SECONDS = 900
MAX_REFRESH_SECONDS = 45
TRANSFER_TYPES = {'ACATC', 'ACATS', 'FOPT', 'JNL', 'JNLC', 'JNLS', 'TRANS', 'MEM'}
KNOWN_TYPES = TRANSFER_TYPES | {
    'FILL', 'CFEE', 'FEE', 'CSD', 'CSW', 'CGD', 'DIV', 'DIVCGL', 'DIVCGS',
    'DIVFEE', 'DIVFT', 'DIVNRA', 'DIVROC', 'DIVTW', 'DIVTXEX', 'INT', 'INTNRA',
    'INTTW', 'MA', 'NC', 'OPASN', 'OPEXP', 'OPXRC', 'PTC', 'PTR', 'REORG',
    'SC', 'SSO', 'SSP',
}
NON_USD_CRYPTO_ALIAS = re.compile(
    r'(?:BTC|ETH|SOL|AVAX|LTC|BCH|DOGE|DOT|LINK|AAVE|UNI|SHIB|USDC|USDT|DAI|'
    r'BNB|XRP|ADA|ALGO|BAT|CRV|GRT|MKR|PEPE|SUSHI|TRX|XLM|XTZ|YFI|ZEC)'
    r'(?:BTC|ETH|USDT|USDC|EUR|GBP|JPY)')


def _at(value):
    return timestamp(value).astimezone(UTC)


def _iso(value):
    return _at(value).isoformat()


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Use a YYYY-MM-DD activity date')
    return date.fromisoformat(value)


def _scope(account_ref):
    if not isinstance(account_ref, str) or not 1 <= len(account_ref) <= 256:
        raise ValueError('A verified account reference is required')
    return sha256(account_ref.encode()).hexdigest()


def _decimal(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 100:
        raise ValueError('Invalid activity amount')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid activity amount') from None
    if not result.is_finite() or result.copy_abs() > Decimal('1e24') or result.as_tuple().exponent < -24 or positive and result <= 0:
        raise ValueError('Invalid activity amount')
    return result


def _text(value):
    return format(value, 'f') if value is not None else None


def _product(left, right):
    with localcontext() as context:
        context.prec = 128
        return left * right


def _sum(values):
    with localcontext() as context:
        context.prec = 128
        return sum(values, Decimal('0'))


def _normalize(row, now):
    """Retain allowlisted accounting fields, never descriptions or raw identities."""
    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not 1 <= len(row['id']) <= 256:
        raise ValueError('Activity identity could not be validated')
    kind = row.get('activity_type')
    if not isinstance(kind, str) or kind not in KNOWN_TYPES:
        kind = 'UNKNOWN'
    value = {'key': sha256(row['id'].encode()).hexdigest(), 'activity_type': kind,
             'day': None, 'at': None, 'date_only': False, 'amount': None,
             'notional': None, 'side': None, 'issues': []}
    try:
        when = row.get('transaction_time') if kind == 'FILL' else row.get('transaction_time') or row.get('date')
        if isinstance(when, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', when):
            if kind == 'FILL':
                raise ValueError()
            value.update(day=_day(when).isoformat(), date_only=True)
        elif kind != 'FILL' and not row.get('transaction_time'):
            # A non-trade activity's `date` is an effective date, even when
            # serialized as midnight. It does not establish transfer timing.
            at = _at(when)
            value.update(day=at.date().isoformat(), date_only=True)
        else:
            at = _at(when)
            if at > now:
                raise ValueError()
            value.update(at=_iso(at), day=at.astimezone(NY).date().isoformat())
        if value['date_only'] and _day(value['day']) > now.date():
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        value.update(day=None, at=None, date_only=False)
        value['issues'].append('activity_date_unverified')
    try:
        if row.get('currency', 'USD') != 'USD' or row.get('quote_currency', 'USD') != 'USD':
            raise ValueError()
        if kind == 'FILL':
            symbol = row.get('symbol')
            if (row.get('side') not in ('buy', 'sell') or not isinstance(symbol, str)
                    or not re.fullmatch(r'[A-Z0-9._-]{1,24}(?:/USD)?', symbol)
                    or NON_USD_CRYPTO_ALIAS.fullmatch(symbol)
                    or re.fullmatch(r'[A-Z.]{1,6}\d{6}[CP]\d{8}', symbol)
                    or row.get('asset_class', 'us_equity') not in ('us_equity', 'crypto')
                    or row.get('asset_class') == 'crypto' and not symbol.endswith('USD')):
                raise ValueError()
            if row.get('type', 'fill') not in ('fill', 'partial_fill'):
                raise ValueError()
            value.update(side=row['side'], notional=_text(_product(_decimal(row.get('qty'), positive=True),
                                                                 _decimal(row.get('price'), positive=True))))
            if isinstance(row.get('order_id'), str) and 0 < len(row['order_id']) <= 256:
                value['order_key'] = sha256(row['order_id'].encode()).hexdigest()
        elif kind == 'CFEE':
            # The documented crypto fee is an asset debit, with net_amount=0.
            # Never also subtract net_amount or an estimated schedule charge.
            if (row.get('status', 'executed') != 'executed'
                    or not isinstance(row.get('symbol'), str)
                    or not re.fullmatch(r'[A-Z0-9._-]{1,20}/?USD', row['symbol'])
                    or _decimal(row.get('net_amount')) != 0):
                raise ValueError()
            value['amount'] = _text(_product(_decimal(row.get('qty')).copy_negate(), _decimal(row.get('price'), positive=True)))
        elif kind == 'FEE':
            if row.get('status', 'executed') != 'executed':
                raise ValueError()
            value['amount'] = _text(_decimal(row.get('net_amount')).copy_negate())
        elif kind in ('CSD', 'CSW'):
            amount = _decimal(row.get('net_amount'))
            if (row.get('status', 'executed') != 'executed'
                    or kind == 'CSD' and amount < 0 or kind == 'CSW' and amount > 0):
                raise ValueError()
            value['amount'] = _text(amount)
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        value['issues'].append('activity_value_unverified')
        value['amount'] = value['notional'] = None
    if kind == 'UNKNOWN':
        value['issues'].append('activity_type_unverified')
    if row.get('previous_id'):
        value['issues'].append('activity_correction_unverified')
    return value


class ActivityLedger:
    def __init__(self, path, *, page_size=100, max_pages=10):
        if type(page_size) is not int or not 1 <= page_size <= 100 or type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise ValueError('Activity pagination limits are invalid')
        self.path, self.page_size, self.max_pages = Path(path), page_size, max_pages
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Activity ledger must be a regular file')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self._refresh_lock = Lock()
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS activity_records (
                    account_scope TEXT NOT NULL, activity_key TEXT NOT NULL,
                    day TEXT, body TEXT NOT NULL, seen_at TEXT NOT NULL,
                    PRIMARY KEY(account_scope,activity_key));
                CREATE INDEX IF NOT EXISTS activity_records_day ON activity_records(account_scope,day);
                CREATE TABLE IF NOT EXISTS activity_refresh (
                    account_scope TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS activity_equity (
                    account_scope TEXT NOT NULL, day TEXT NOT NULL,
                    first_at TEXT NOT NULL, first_equity TEXT NOT NULL,
                    last_at TEXT NOT NULL, last_equity TEXT NOT NULL,
                    PRIMARY KEY(account_scope,day));
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _metadata(self, db, scope):
        row = db.execute('SELECT body FROM activity_refresh WHERE account_scope=?', (scope,)).fetchone()
        return json.loads(row[0]) if row else {}

    def refresh(self, feeds, account_ref, now):
        scope, now = _scope(account_ref), _at(now)
        if not self._refresh_lock.acquire(blocking=False):
            return {**self.summary(account_ref, now.astimezone(NY).date().isoformat(), now), 'changed_days': []}
        beginning = datetime.combine(now.astimezone(NY).date() - timedelta(days=LOOKBACK_DATES - 1),
                                     datetime.min.time(), NY).astimezone(UTC)
        params = {'after': _iso(beginning - timedelta(microseconds=1)), 'until': _iso(now),
                  'direction': 'asc', 'page_size': self.page_size}
        started, records, tokens, changed_days = monotonic(), {}, set(), set()
        try:
            for step in ('before', 'after'):
                account = feeds.account()
                if account.get('account_ref') != account_ref or account.get('currency') != 'USD':
                    raise ValueError('Connected activity account could not be verified')
                if step == 'after':
                    break
                for _ in range(self.max_pages):
                    if monotonic() - started > MAX_REFRESH_SECONDS:
                        raise ValueError('Activity refresh time limit reached')
                    page = feeds.get('alpaca', '/v2/account/activities', dict(params))
                    if not isinstance(page, list) or len(page) > self.page_size:
                        raise ValueError('Invalid activity page')
                    if not page:
                        break
                    for row in page:
                        value = _normalize(row, now)
                        records[value['key']] = value
                    token = page[-1]['id']
                    if token in tokens:
                        raise ValueError('Activity pagination did not advance')
                    tokens.add(token)
                    if len(page) < self.page_size:
                        break
                    params['page_token'] = token
                else:
                    raise ValueError('Activity pagination limit reached')
            if monotonic() - started > MAX_REFRESH_SECONDS:
                raise ValueError('Activity refresh time limit reached')
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for key, value in records.items():
                    prior = db.execute('SELECT day,body FROM activity_records WHERE account_scope=? AND activity_key=?', (scope, key)).fetchone()
                    body = json.dumps(value, sort_keys=True)
                    if not prior or prior[1] != body:
                        changed_days.update(day for day in (prior[0] if prior else None, value['day']) if day)
                    db.execute('INSERT INTO activity_records VALUES(?,?,?,?,?) '
                               'ON CONFLICT(account_scope,activity_key) DO UPDATE SET day=excluded.day,body=excluded.body,seen_at=excluded.seen_at',
                               (scope, key, value['day'], body, _iso(now)))
                metadata = {'last_success_at': _iso(now), 'last_attempt_at': _iso(now), 'error': None,
                            'created_after': _iso(beginning), 'created_until': _iso(now), 'pages_complete': True}
                db.execute('INSERT INTO activity_refresh VALUES(?,?) ON CONFLICT(account_scope) DO UPDATE SET body=excluded.body',
                           (scope, json.dumps(metadata, sort_keys=True)))
        except Exception:
            changed_days.clear()
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                metadata = self._metadata(db, scope)
                metadata.update(last_attempt_at=_iso(now), error='Broker activity collection is incomplete or the account could not be verified. Earlier evidence is retained.')
                db.execute('INSERT INTO activity_refresh VALUES(?,?) ON CONFLICT(account_scope) DO UPDATE SET body=excluded.body',
                           (scope, json.dumps(metadata, sort_keys=True)))
        finally:
            self._refresh_lock.release()
        return {**self.summary(account_ref, now.astimezone(NY).date().isoformat(), now),
                'changed_days': sorted(changed_days)}

    def record_equity(self, account_ref, account, observed_at):
        scope, at = _scope(account_ref), _at(observed_at)
        if not isinstance(account, dict) or account.get('account_ref') != account_ref or account.get('currency') != 'USD':
            raise ValueError('Equity observation requires the verified USD account')
        equity = _decimal(account.get('equity'))
        day, iso = at.astimezone(NY).date().isoformat(), _iso(at)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT first_at,first_equity,last_at,last_equity FROM activity_equity WHERE account_scope=? AND day=?', (scope, day)).fetchone()
            first_at, first_equity, last_at, last_equity = existing or (iso, _text(equity), iso, _text(equity))
            if iso <= first_at:
                first_at, first_equity = iso, _text(equity)
            if iso >= last_at:
                last_at, last_equity = iso, _text(equity)
            db.execute('INSERT OR REPLACE INTO activity_equity VALUES(?,?,?,?,?,?)',
                       (scope, day, first_at, first_equity, last_at, last_equity))
            newest = db.execute('SELECT MAX(day) FROM activity_equity WHERE account_scope=?', (scope,)).fetchone()[0]
            cutoff = (_day(newest) - timedelta(days=364)).isoformat()
            db.execute('DELETE FROM activity_equity WHERE account_scope=? AND day<?', (scope, cutoff))

    def summary(self, account_ref, day, now):
        scope, requested, now = _scope(account_ref), _day(day), _at(now)
        with self._connect() as db:
            metadata = self._metadata(db, scope)
            rows = [json.loads(row[0]) for row in db.execute('SELECT body FROM activity_records WHERE account_scope=? AND (day=? OR day IS NULL)', (scope, day))]
            equity = db.execute('SELECT first_at,first_equity,last_at,last_equity FROM activity_equity WHERE account_scope=? AND day=?', (scope, day)).fetchone()
            movements = [json.loads(row[0]) for row in db.execute('SELECT body FROM activity_records WHERE account_scope=? AND (day BETWEEN ? AND ? OR day IS NULL)',
                          (scope, day, (requested + timedelta(days=1)).isoformat()))]
        beginning = datetime.combine(requested, datetime.min.time(), NY).astimezone(UTC)
        covered = bool(metadata.get('last_success_at') and _at(metadata['created_after']) <= beginning <= _at(metadata['created_until']))
        fresh = bool(covered and not metadata.get('error') and 0 <= (now - _at(metadata['last_success_at'])).total_seconds() <= FRESH_SECONDS)
        unattributed = sum(row['day'] is None for row in rows)
        day_rows = [row for row in rows if row['day'] == day]
        fills = [row for row in day_rows if row['activity_type'] == 'FILL']
        fees = [row for row in day_rows if row['activity_type'] in ('FEE', 'CFEE')]
        flows = [row for row in day_rows if row['activity_type'] in ('CSD', 'CSW')]
        unresolved_fills = sum(bool(row['issues']) for row in fills)
        unresolved_fees = sum(bool(row['issues']) for row in fees)
        unresolved_flows = sum(bool(row['issues']) for row in flows)
        issues = unattributed + sum(bool(row['issues']) for row in day_rows)
        known = bool(covered or day_rows)
        known_fills, known_fees, known_flows = (bool(covered or items) for items in (fills, fees, flows))
        def total(items, key):
            return _sum(Decimal(row[key]) for row in items if row[key] is not None)
        buy, sell = (total([row for row in fills if row['side'] == side], 'notional') for side in ('buy', 'sell'))
        observed_fees = total(fees, 'amount')
        deposits = total([row for row in flows if row['activity_type'] == 'CSD'], 'amount')
        withdrawals = total([row for row in flows if row['activity_type'] == 'CSW'], 'amount').copy_negate()
        status = 'unavailable' if not known else 'partial' if not covered else 'stale' if not fresh else 'partial' if issues else 'current'
        fee_status = 'unavailable' if not known_fees else 'unvalued_activities' if unresolved_fees or unattributed else 'observed_not_final' if fees else 'pending'
        result = {
            'account_scope': scope, 'day': day, 'as_of': _iso(now), 'status': status,
            'data_complete': bool(fresh and not issues), 'last_success_at': metadata.get('last_success_at'),
            'last_attempt_at': metadata.get('last_attempt_at'), 'error': metadata.get('error'),
            'coverage': {key: metadata.get(key) for key in ('created_after', 'created_until', 'pages_complete')},
            'fills': {'count': len(fills) if known_fills else None, 'unresolved_count': unresolved_fills + unattributed,
                      'buy_notional_usd': _text(buy) if known_fills and not unresolved_fills and not unattributed else None,
                      'sell_notional_usd': _text(sell) if known_fills and not unresolved_fills and not unattributed else None,
                      'executed_notional_usd': _text(_sum((buy, sell))) if known_fills and not unresolved_fills and not unattributed else None},
            'fees': {'activity_count': len(fees) if known_fees else None, 'observed_usd_cost': _text(observed_fees) if known_fees and not unresolved_fees and not unattributed else None,
                     'unresolved_count': unresolved_fees + unattributed, 'status': fee_status, 'final': False,
                     'day_basis': 'provider_activity_date',
                     'detail': 'Observed signed USD fee costs only; rebates reduce costs. Fees can post later and may relate to a different trade date. No fee-finality marker is available.'},
            'cash_flows': {'deposits_usd': _text(deposits) if known_flows and not unresolved_flows and not unattributed else None,
                           'withdrawals_usd': _text(withdrawals) if known_flows and not unresolved_flows and not unattributed else None,
                           'net_usd': _text(_sum((deposits, withdrawals.copy_negate()))) if known_flows and not unresolved_flows and not unattributed else None,
                           'unresolved_count': unresolved_flows + unattributed,
                           'unclassified_transfer_count': sum(row['activity_type'] in TRANSFER_TYPES for row in day_rows)},
            'unattributed_activity_count': unattributed, 'realized_net_pnl_usd': None,
            'net_status': 'unverified', 'per_trade_net': [],
            'detail': 'Fills use New York execution dates; fee and transfer dates follow broker activity evidence. Totals describe observed evidence; dates outside the collection window have incomplete history. Executed notional and cash flows are not profit. Opening inventory and linked final fees are not established.',
        }
        result['account_change'] = self._account_change(equity, movements, metadata, fresh)
        return result

    @staticmethod
    def _account_change(equity, movements, metadata, fresh):
        result = {'status': 'unavailable', 'currency': 'USD', 'partial_day': True,
                  'start_at': None, 'end_at': None, 'start_equity_usd': None, 'end_equity_usd': None,
                  'equity_change_usd': None, 'cash_flow_adjusted_change_usd': None,
                  'detail': 'Two captured account observations are required. This measure is account movement, not realized trading profit.'}
        if not equity:
            return result
        first_at, first_value, last_at, last_value = equity
        result.update(start_at=first_at, end_at=last_at, start_equity_usd=first_value, end_equity_usd=last_value)
        if first_at == last_at:
            return result
        start, end = _at(first_at), _at(last_at)
        change = _sum((_decimal(last_value), _decimal(first_value).copy_negate()))
        result.update(status='cash_flows_unverified', equity_change_usd=_text(change),
                      detail='Captured account movement includes unrealized changes. Transfer timing/completeness is not yet established; it is not realized trading profit.')
        if not (fresh and metadata.get('created_after') and _at(metadata['created_after']) <= start
                and end <= _at(metadata['created_until'])):
            return result
        net_flow = Decimal('0')
        boundary_dates = {start.astimezone(NY).date().isoformat(), end.astimezone(NY).date().isoformat(),
                          start.date().isoformat(), end.date().isoformat()}
        for row in movements:
            if row['activity_type'] not in TRANSFER_TYPES | {'CSD', 'CSW', 'UNKNOWN'}:
                continue
            if row['day'] is None or row['date_only'] and row['day'] in boundary_dates:
                return result
            if row['at'] and start < _at(row['at']) <= end:
                if row['activity_type'] in TRANSFER_TYPES | {'UNKNOWN'} or row['issues'] or row['amount'] is None:
                    return result
                net_flow = _sum((net_flow, Decimal(row['amount'])))
        result.update(status='observed', cash_flow_adjusted_change_usd=_text(_sum((change, net_flow.copy_negate()))),
                      detail='Change between the displayed captured observations, less verified timed cash deposits/withdrawals. Includes unrealized movement and all account activity; not strategy profit, realized P&L, or a complete-day return.')
        return result
