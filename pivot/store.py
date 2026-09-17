"""Versioned settings and append-only audit events, isolated from legacy databases."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULTS = {'sizing_mode': 'target', 'target_dollars': '25.00'}
DECISION_TRACE_RETENTION = 24000  # >=60 regular sessions even at one changed state per minute.


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL);')
            db.executescript('CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, finished INTEGER NOT NULL, body TEXT NOT NULL);'
                             'CREATE UNIQUE INDEX IF NOT EXISTS one_active_trade ON trades(finished) WHERE finished=0;')
            db.executescript('CREATE TABLE IF NOT EXISTS decision_traces ('
                             'id INTEGER PRIMARY KEY, checkpoint_at TEXT NOT NULL, fingerprint TEXT NOT NULL,'
                             'first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,'
                             'observation_count INTEGER NOT NULL, body TEXT NOT NULL,'
                             'UNIQUE(checkpoint_at,fingerprint));'
                             'CREATE INDEX IF NOT EXISTS decision_trace_recency '
                             'ON decision_traces(last_observed_at DESC,id DESC);')
            db.execute('INSERT OR IGNORE INTO control VALUES(1, ?)', (json.dumps({'enabled': False, 'policy': None}),))
            db.execute('INSERT OR IGNORE INTO settings VALUES(1, ?)', (json.dumps(DEFAULTS),))

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def settings(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])

    @staticmethod
    def _decision_row(row):
        if row is None:
            return None
        identity, first, last, count, body = row
        return {**json.loads(body), 'id': identity, 'first_observed_at': first,
                'last_observed_at': last, 'observation_count': count, 'persisted': True}

    def latest_decision(self):
        with self.connect() as db:
            row = db.execute('SELECT id,first_observed_at,last_observed_at,observation_count,body '
                             'FROM decision_traces ORDER BY last_observed_at DESC,id DESC LIMIT 1').fetchone()
        return self._decision_row(row)

    def record_decision(self, trace):
        """One durable row per checkpoint/evidence state; existing ledgers migrate in place."""
        from .diagnostics import VERSION, evidence_fingerprint
        if trace.get('version') != VERSION:
            raise ValueError('Unsupported decision trace version')
        body = json.dumps(trace, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(body.encode()) > 512000:
            raise ValueError('Decision trace exceeds storage limit')
        key, at = evidence_fingerprint(trace), trace['captured_at']
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO decision_traces(checkpoint_at,fingerprint,first_observed_at,last_observed_at,observation_count,body) '
                       'VALUES(?,?,?,?,1,?) ON CONFLICT(checkpoint_at,fingerprint) DO UPDATE SET '
                       'last_observed_at=excluded.last_observed_at,observation_count=observation_count+1,body=excluded.body',
                       (trace['checkpoint_at'], key, at, at, body))
            row = db.execute('SELECT id,first_observed_at,last_observed_at,observation_count,body '
                             'FROM decision_traces WHERE checkpoint_at=? AND fingerprint=?',
                             (trace['checkpoint_at'], key)).fetchone()
            db.execute('DELETE FROM decision_traces WHERE id IN (SELECT id FROM decision_traces '
                       'ORDER BY last_observed_at DESC,id DESC LIMIT -1 OFFSET ?)', (DECISION_TRACE_RETENTION,))
        return self._decision_row(row)

    def save(self, settings):
        with self.connect() as db:
            db.execute('UPDATE settings SET body=? WHERE id=1', (json.dumps(settings),))
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), 'settings_saved', json.dumps(settings)))

    def event(self, kind, body):
        with self.connect() as db:
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), kind, json.dumps(body)))

    def events(self):
        with self.connect() as db:
            return [{'at': at, 'kind': kind, 'detail': json.loads(body)} for at, kind, body in
                    db.execute('SELECT at,kind,body FROM events ORDER BY id DESC LIMIT 40')]

    def control(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT body FROM control WHERE id=1').fetchone()[0])

    def set_control(self, enabled, policy=None, account_ref=None):
        value = {'enabled': enabled, 'policy': policy, 'account_ref': account_ref, 'at': datetime.now(timezone.utc).isoformat()}
        with self.connect() as db:
            db.execute('UPDATE control SET body=? WHERE id=1', (json.dumps(value),))
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (value['at'], 'live_on' if enabled else 'live_off', json.dumps(value)))
        return value

    def active_trade(self):
        with self.connect() as db:
            row = db.execute('SELECT body FROM trades WHERE finished=0').fetchone()
            return json.loads(row[0]) if row else None

    def trade_exists(self, identity):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM trades WHERE id=?', (identity,)).fetchone() is not None

    def trade_results(self):
        from .performance import trade_result
        with self.connect() as db:
            rows=db.execute('SELECT body FROM trades WHERE finished=1 ORDER BY rowid DESC LIMIT 20').fetchall()
        return [trade_result(json.loads(row[0])) for row in rows]

    def reserve_trade(self, trade):
        # Unique signal + single active slot survive restarts and competing workers.
        try:
            with self.connect() as db:
                db.execute('INSERT INTO trades(id,finished,body) VALUES(?,0,?)', (trade['id'], json.dumps(trade)))
            return True
        except sqlite3.IntegrityError:
            return False

    def save_trade(self, trade, finished=False):
        with self.connect() as db:
            db.execute('UPDATE trades SET body=?,finished=? WHERE id=?', (json.dumps(trade), int(finished), trade['id']))

    def claim_operation(self, trade_id, name):
        """Durably mark the single allowed POST attempt before making a network call."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM trades WHERE id=?', (trade_id,)).fetchone()
            trade = json.loads(row[0])
            if trade['ops'][name]['state'] != 'prepared':
                return False
            trade['ops'][name]['state'] = 'attempted'
            db.execute('UPDATE trades SET body=? WHERE id=?', (json.dumps(trade), trade_id))
            return True
