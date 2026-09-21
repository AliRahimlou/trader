"""Durable, separate crypto ownership and order intents in the shared app ledger."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo
from .crypto_markets import SYMBOLS
from .models import timestamp
from .store import _create_entry_allowance_schema, _reserve_session_entry

DECISION_LIMIT = 12000
NY = ZoneInfo('America/New_York')
DEFAULT_CONTROL = {'enabled': False, 'symbols': ['BTC/USD'], 'target_dollars': '5.00',
                   'policy': None, 'account_ref': None, 'generation': 0}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class ConcurrentChange(RuntimeError):
    """A stale worker must reload durable state instead of overwriting a claim."""


class CryptoStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            _create_entry_allowance_schema(db)
            db.executescript('''
                CREATE TABLE IF NOT EXISTS crypto_control (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS crypto_trades (id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                    finished INTEGER NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS crypto_one_active_symbol ON crypto_trades(symbol) WHERE finished=0;
                CREATE TABLE IF NOT EXISTS crypto_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL,
                    kind TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS crypto_incidents (id TEXT PRIMARY KEY, at TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS crypto_decisions (id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                    symbol TEXT NOT NULL, fingerprint TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS crypto_decisions_day_symbol ON crypto_decisions(day,symbol,id);
            ''')
            db.execute('INSERT OR IGNORE INTO crypto_control VALUES(1,?)', (_json(DEFAULT_CONTROL),))

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    @staticmethod
    def _control(db):
        return json.loads(db.execute('SELECT body FROM crypto_control WHERE id=1').fetchone()[0])

    def control(self, db=None):
        with self.connect() if db is None else nullcontext(db) as connection:
            return self._control(connection)

    def configure(self, settings, *, db=None):
        allowed = {'enabled', 'symbols', 'target_dollars', 'policy', 'account_ref', 'generation'}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError('Invalid crypto settings')
        own = db is None
        with self.connect() if own else nullcontext(db) as db:
            if own:
                db.execute('BEGIN IMMEDIATE')
            old = self._control(db)
            value = {**old, **deepcopy(settings), 'generation': old['generation'] + 1}
            if type(value['enabled']) is not bool:
                raise ValueError('Crypto permission must be true or false')
            symbols = value['symbols']
            if (not isinstance(symbols, list) or not 1 <= len(symbols) <= len(SYMBOLS)
                    or any(not isinstance(s, str) or s not in SYMBOLS for s in symbols) or len(set(symbols)) != len(symbols)):
                raise ValueError('Select one or more supported crypto markets')
            try:
                amount = Decimal(str(value['target_dollars']))
                if not amount.is_finite() or amount < 1 or amount > 200000 or amount != amount.quantize(Decimal('.01')):
                    raise ValueError
            except (InvalidOperation, ValueError):
                raise ValueError('Crypto purchase must be $1 to $200,000, in cents') from None
            value['target_dollars'] = format(amount, '.2f')
            if value['enabled'] and (not isinstance(value['policy'], str) or not value['policy']
                                     or not isinstance(value['account_ref'], str) or not value['account_ref']):
                raise ValueError('An approved crypto policy and account are required')
            db.execute('UPDATE crypto_control SET body=? WHERE id=1', (_json(value),))
            self._event(db, 'settings', value)
        return value

    @staticmethod
    def _event(db, kind, body):
        db.execute('INSERT INTO crypto_events(at,kind,body) VALUES(?,?,?)',
                   (datetime.now(timezone.utc).isoformat(), kind, _json(body)))
        db.execute('DELETE FROM crypto_events WHERE id IN '
                   '(SELECT id FROM crypto_events ORDER BY id DESC LIMIT -1 OFFSET 12000)')

    def event(self, kind, body):
        with self.connect() as db:
            self._event(db, kind, body)

    def events(self, limit=40):
        limit = min(max(int(limit), 1), 100)
        with self.connect() as db:
            return [{'at': at, 'kind': kind, 'detail': json.loads(body)} for at, kind, body in
                    db.execute('SELECT at,kind,body FROM crypto_events ORDER BY id DESC LIMIT ?', (limit,))]

    @staticmethod
    def _row(row):
        return {**json.loads(row[1]), '_version': row[0]} if row else None

    def get_trade(self, identity):
        with self.connect() as db:
            return self._row(db.execute('SELECT version,body FROM crypto_trades WHERE id=?', (identity,)).fetchone())

    def active_trades(self):
        with self.connect() as db:
            return [self._row(row) for row in db.execute(
                'SELECT version,body FROM crypto_trades WHERE finished=0 ORDER BY rowid')]

    def active_trade(self, symbol):
        with self.connect() as db:
            return self._row(db.execute('SELECT version,body FROM crypto_trades WHERE finished=0 AND symbol=?', (symbol,)).fetchone())

    def trade_exists(self, identity):
        return self.get_trade(identity) is not None

    def reserve_trade(self, trade, *, expected_control=None):
        """The unique event and active symbol are reserved atomically, before any POST."""
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if expected_control is not None and self._control(db) != expected_control:
                    return False
                if trade.get('authorization', {}).get('global') is not None:
                    from .store import Store
                    if Store._entry_authorization(db) != trade['authorization']['global']:
                        return False
                body = {k: v for k, v in trade.items() if k != '_version'}
                db.execute('INSERT INTO crypto_trades VALUES(?,?,0,0,?)', (trade['id'], trade['symbol'], _json(body)))
                self._event(db, 'entry_reserved', {'trade_id': trade['id'], 'symbol': trade['symbol'], 'amount': trade['amount']})
            trade['_version'] = 0
            return True
        except sqlite3.IntegrityError:
            return False

    def save_trade(self, trade, finished=False):
        """Compare-and-swap prevents stale management from resetting a claimed operation."""
        body = {k: v for k, v in trade.items() if k != '_version'}
        with self.connect() as db:
            cursor = db.execute('UPDATE crypto_trades SET body=?,finished=?,version=version+1 WHERE id=? AND version=?',
                                (_json(body), int(finished), trade['id'], trade['_version']))
            if cursor.rowcount != 1:
                raise ConcurrentChange('Crypto state changed; reloading the durable order ledger')
        trade['_version'] += 1

    def claim_operation(self, trade, name, *, expected_control=None, attempted_at=None):
        """One irreversible POST claim; attempted intents can only be looked up afterward."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version,body FROM crypto_trades WHERE id=? AND finished=0', (trade['id'],)).fetchone()
            saved = self._row(row)
            if saved != trade or saved['ops'][name]['state'] != 'prepared':
                return False
            if name == 'entry':
                if expected_control is not None and self._control(db) != expected_control:
                    return False
                if saved.get('authorization', {}).get('global') is not None:
                    from .store import Store
                    if Store._entry_authorization(db) != saved['authorization']['global']:
                        return False
                _reserve_session_entry(db, saved, 'range_reversal', attempted_at or datetime.now(timezone.utc))
            saved['ops'][name]['state'] = 'attempted'
            saved['ops'][name]['attempted_at'] = (attempted_at or datetime.now(timezone.utc)).isoformat()
            body = {k: v for k, v in saved.items() if k != '_version'}
            db.execute('UPDATE crypto_trades SET body=?,version=version+1 WHERE id=?', (_json(body), trade['id']))
        trade.update(saved)
        trade['_version'] += 1
        return True

    def incident(self, identity, message, *, symbol=None, trade_id=None):
        value = {'id': identity, 'message': message, 'symbol': symbol, 'trade_id': trade_id}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT resolved FROM crypto_incidents WHERE id=?', (identity,)).fetchone()
            at = datetime.now(timezone.utc).isoformat()
            db.execute('INSERT INTO crypto_incidents(id,at,resolved,body) VALUES(?,?,0,?) '
                       'ON CONFLICT(id) DO UPDATE SET resolved=0,body=excluded.body,'
                       'at=CASE WHEN crypto_incidents.resolved=1 THEN excluded.at ELSE crypto_incidents.at END',
                       (identity, at, _json(value)))
            if previous is None or previous[0] == 1:
                self._event(db, 'incident_opened' if previous is None else 'incident_reopened', value)
        return value

    def incidents(self):
        with self.connect() as db:
            return [{**json.loads(body), 'at': at} for at, body in db.execute(
                'SELECT at,body FROM crypto_incidents WHERE resolved=0 ORDER BY at DESC LIMIT 100')]

    def resolve_incident(self, identity, *, proof='Verified broker order reconciliation'):
        """For verified reconciliation only; toggling permission does not clear incidents."""
        with self.connect() as db:
            cursor = db.execute('UPDATE crypto_incidents SET resolved=1 WHERE id=? AND resolved=0', (identity,))
            if cursor.rowcount:
                self._event(db, 'incident_resolved', {'id': identity, 'proof': proof})
            return cursor.rowcount == 1

    def history(self, limit=20):
        with self.connect() as db:
            return [self._row(row) for row in db.execute(
                'SELECT version,body FROM crypto_trades WHERE finished=1 ORDER BY rowid DESC LIMIT ?', (min(100, max(1, int(limit))),))]

    def record_decision(self, decision):
        """Record changed live checks, not every quote refresh or reconstructed bar.

        Receipt/check timestamps are evidence in the row but not change triggers.
        A new completed candle, event, readiness, outcome or reason is a trigger.
        """
        fields = ('checked_at', 'symbol', 'state', 'event_id', 'direction', 'confirmation_at',
                  'latest_bar_at', 'observed_at', 'signal_fresh', 'outcome', 'reason')
        value = {key: decision.get(key) for key in fields}
        if value['symbol'] not in SYMBOLS or type(value['signal_fresh']) is not bool:
            raise ValueError('Invalid crypto decision')
        day = timestamp(value['checked_at']).astimezone(NY).date().isoformat()
        for key in fields:
            if key != 'signal_fresh' and value[key] is not None:
                if not isinstance(value[key], str):
                    raise ValueError('Invalid crypto decision field')
                value[key] = value[key][:1000 if key == 'reason' else 160]
        fingerprint = _json({key: value[key] for key in fields if key not in ('checked_at', 'observed_at')})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT fingerprint FROM crypto_decisions WHERE day=? AND symbol=? ORDER BY id DESC LIMIT 1',
                                  (day, value['symbol'])).fetchone()
            if previous and previous[0] == fingerprint:
                return False
            db.execute('INSERT INTO crypto_decisions(day,symbol,fingerprint,body) VALUES(?,?,?,?)',
                       (day, value['symbol'], fingerprint, _json(value)))
            db.execute('DELETE FROM crypto_decisions WHERE id IN '
                       '(SELECT id FROM crypto_decisions ORDER BY id DESC LIMIT -1 OFFSET ?)', (DECISION_LIMIT,))
        return True

    def decision_review(self, now=None):
        day = timestamp(now or datetime.now(timezone.utc)).astimezone(NY).date().isoformat()
        with self.connect() as db:
            rows = [json.loads(row[0]) for row in db.execute('SELECT body FROM crypto_decisions WHERE day=? ORDER BY id DESC', (day,))]
            trades = [json.loads(row[0]) for row in db.execute('SELECT body FROM crypto_trades')]
        latest, longs, shorts = {}, set(), set()
        for row in rows:
            latest.setdefault(row['symbol'], row)
            if row['signal_fresh'] and row.get('event_id'):
                if row.get('direction') == 'long': longs.add(row['event_id'])
                if row.get('direction') == 'short': shorts.add(row['event_id'])
        attempts = fills = 0
        for trade in trades:
            try:
                if timestamp(trade['created_at']).astimezone(NY).date().isoformat() != day:
                    continue
                entry = trade.get('ops', {}).get('entry', {})
                attempts += entry.get('state') in ('attempted', 'rejected')
                fills += Decimal(str(entry.get('last_seen', {}).get('filled_qty', '0'))) > 0
            except (KeyError, ValueError, TypeError, InvalidOperation):
                continue
        return {'status': 'available', 'day': day, 'scope': 'recorded_live_checks_only',
                'detail': 'Counts cover retained live worker checks only. Reconstructed chart history is not counted as checked live.',
                'checks_recorded': len(rows), 'fresh_setups': {'total': len(longs | shorts), 'long': len(longs), 'short': len(shorts)},
                'order_attempts': attempts, 'filled_entries': fills, 'latest_by_symbol': latest,
                'recent_checks': rows[:20], 'retention_limit': DECISION_LIMIT}
