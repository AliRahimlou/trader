"""Versioned settings and append-only audit events, isolated from legacy databases."""
import json
import sqlite3
from hashlib import sha256
from datetime import datetime, timezone, timedelta
from pathlib import Path
from copy import deepcopy
from time import monotonic
from zoneinfo import ZoneInfo

from .alerts import ALERT_KINDS, notify
from .portfolio import PortfolioBlocked

DEFAULTS = {'sizing_mode': 'target', 'target_dollars': '25.00'}
DECISION_TRACE_RETENTION = 24000  # Row bound; does not imply a guaranteed number of sessions.
EXECUTION_CHECK_RETENTION = 24000
# Explicit owner-approved restriction, never a tunable uplift. App interpretation:
# the limit applies PER strategy family, so a day of Socrates attempts cannot
# consume the crypto engine's attempts or the reverse. Legacy attempts already
# recorded under a family keep counting against that family only.
SESSION_ENTRY_LIMIT = 2
SESSION_ENTRY_FAMILIES = ('socrates', 'range_reversal')
FAMILY_LABELS = {'socrates': 'Socrates', 'range_reversal': '4H Range Reversal'}
SESSION_TIMEZONE = ZoneInfo('America/New_York')


def _family_used(rows, family, day):
    return sum(row['family'] == family and row['session_day'] == day for row in rows)


def _limit_reached(family):
    return PortfolioBlocked(f'The {FAMILY_LABELS.get(family, family)} limit of two new entry attempts for this '
                           'New York session is reached; the other strategy and existing positions are unaffected')


def _allowance_time(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Entry allowance timestamps must include a timezone')
    return at.astimezone(timezone.utc)


def _allowance_days(at):
    # Entry freshness is rechecked after claiming and permits at most ten seconds
    # from preparation. Reserve both dates if claim -> POST could cross midnight.
    return {at.astimezone(SESSION_TIMEZONE).date().isoformat(),
            (at + timedelta(seconds=10)).astimezone(SESSION_TIMEZONE).date().isoformat()}


def _create_entry_allowance_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS session_entry_allowances ('
               'family TEXT NOT NULL, trade_id TEXT NOT NULL, session_day TEXT NOT NULL,'
               'claimed_at TEXT NOT NULL, source TEXT NOT NULL,'
               'PRIMARY KEY(family,trade_id,session_day))')
    db.execute('CREATE INDEX IF NOT EXISTS entry_allowances_day ON session_entry_allowances(session_day)')


def _entry_allowance_rows(db):
    """Read durable claims plus all retained legacy attempts without modifying either.

    A finished/rejected/unknown attempt is never refunded. Legacy prepared intents
    remain unsent; malformed or contradictory evidence prevents new risk, without
    interfering with opening the database or managing an existing position.
    """
    rows = [dict(zip(('family', 'trade_id', 'session_day', 'claimed_at', 'source'), row))
            for row in db.execute('SELECT family,trade_id,session_day,claimed_at,source FROM session_entry_allowances')]
    covered = {(row['family'], row['trade_id']) for row in rows}
    for table, family in (('trades', 'socrates'), ('crypto_trades', 'range_reversal')):
        if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone():
            continue
        for identity, body in db.execute(f'SELECT id,body FROM {table}'):
            if (family, identity) in covered:
                continue
            try:
                trade = json.loads(body)
                entry = trade['ops']['entry']
                state = entry['state']
                if trade['id'] != identity:
                    raise ValueError
                if (state in ('prepared', 'aborted_before_submit') and not entry.get('last_seen')
                        and 'filled_qty' not in trade and trade.get('stage') in ('entering', 'finished')):
                    continue
                if state not in ('attempted', 'rejected'):
                    raise ValueError
                at = _allowance_time(entry.get('attempted_at') or trade['created_at'])
                for day in _allowance_days(at):
                    rows.append({'family': family, 'trade_id': identity, 'session_day': day,
                                 'claimed_at': at.isoformat(), 'source': 'retained_legacy_attempt'})
            except (KeyError, TypeError, ValueError, AttributeError):
                raise PortfolioBlocked('Session entry history needs reconciliation before any new entry') from None
    return rows


def _reserve_session_entry(db, trade, family, at):
    """Called inside the same write transaction as prepared -> attempted, before POST.

    Only the caller's own family is counted; the schema and the atomic claim are
    unchanged from the shared-limit version, so existing rows remain valid.
    """
    if family not in SESSION_ENTRY_FAMILIES:
        raise PortfolioBlocked('An unknown strategy family cannot claim a session entry')
    at = _allowance_time(at)
    rows = _entry_allowance_rows(db)
    identity = (family, trade['id'])
    # A stale ledger overwrite must not obtain a second POST claim for an identity
    # already irreversibly consumed, including an attempt on a previous date.
    if any((row['family'], row['trade_id']) == identity for row in rows):
        raise PortfolioBlocked('This entry already consumed its durable submission allowance; reconciliation is required')
    days = _allowance_days(at)
    if any(_family_used(rows, family, day) >= SESSION_ENTRY_LIMIT for day in days):
        raise _limit_reached(family)
    # Backfill in place, without changing controls, history, trade state or IDs.
    for row in rows:
        db.execute('INSERT OR IGNORE INTO session_entry_allowances VALUES(?,?,?,?,?)',
                   tuple(row[key] for key in ('family', 'trade_id', 'session_day', 'claimed_at', 'source')))
    for day in days:
        db.execute('INSERT INTO session_entry_allowances VALUES(?,?,?,?,?)',
                   (family, trade['id'], day, at.isoformat(), 'atomic_entry_claim'))


class Store:
    def __init__(self, path):
        self.path = str(path)
        self._session_cache = {}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            _create_entry_allowance_schema(db)
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
            db.executescript('CREATE TABLE IF NOT EXISTS execution_checks ('
                             'id INTEGER PRIMARY KEY, checkpoint_at TEXT NOT NULL, fingerprint TEXT NOT NULL,'
                             'first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,'
                             'observation_count INTEGER NOT NULL, body TEXT NOT NULL,'
                             'UNIQUE(checkpoint_at,fingerprint));'
                             'CREATE INDEX IF NOT EXISTS execution_check_recency '
                             'ON execution_checks(last_observed_at DESC,id DESC);')
            db.execute('INSERT OR IGNORE INTO control VALUES(1, ?)', (json.dumps({'enabled': False, 'policy': None}),))
            db.execute('INSERT OR IGNORE INTO settings VALUES(1, ?)', (json.dumps(DEFAULTS),))
            db.execute('CREATE TABLE IF NOT EXISTS authorization_generation '
                       '(id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO authorization_generation VALUES(1,0)')
            db.execute('CREATE TABLE IF NOT EXISTS strategy_selection (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO strategy_selection VALUES(1,?)', (json.dumps({'socrates': True}),))
            for table in ('decision_traces', 'execution_checks'):
                db.execute(f'CREATE INDEX IF NOT EXISTS {table}_session_time ON {table}(julianday(last_observed_at))')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def session_entry_allowance(self, now=None):
        """Strictly read-only status; retained legacy attempts count before migration.

        'limit' is per family. 'families' carries each family's used/remaining;
        the top-level 'used'/'remaining' are the totals across families, so an
        exhausted family shows remaining 0 under its own key only.
        """
        at = _allowance_time(now or datetime.now(timezone.utc))
        day = at.astimezone(SESSION_TIMEZONE).date().isoformat()
        result = {'session_day': day, 'timezone': str(SESSION_TIMEZONE), 'limit': SESSION_ENTRY_LIMIT,
                  'scope': 'per_family',
                  'accounting': 'Durable new-entry attempts, counted separately for each strategy, including rejected and unknown outcomes; exits do not count.'}
        try:
            with self.connect() as db:
                db.execute('BEGIN')
                rows = _entry_allowance_rows(db)
            families = {}
            for family in SESSION_ENTRY_FAMILIES:
                used = _family_used(rows, family, day)
                families[family] = {'used': used, 'remaining': max(0, SESSION_ENTRY_LIMIT - used)}
            return {**result, 'status': 'available', 'families': families,
                    'used': sum(f['used'] for f in families.values()),
                    'remaining': sum(f['remaining'] for f in families.values())}
        except PortfolioBlocked as exc:
            return {**result, 'status': 'blocked', 'used': None, 'remaining': 0, 'families': None, 'reason': str(exc)}

    def assert_session_entry_available(self, now, family):
        """Early planning gate; the same check is repeated atomically at POST claim.

        Both engines name their family. The claim inside claim_operation is
        authoritative for both, whatever this gate said.
        """
        if family not in SESSION_ENTRY_FAMILIES:
            raise PortfolioBlocked('An unknown strategy family cannot plan a session entry')
        with self.connect() as db:
            db.execute('BEGIN')
            rows = _entry_allowance_rows(db)
        if any(_family_used(rows, family, day) >= SESSION_ENTRY_LIMIT
               for day in _allowance_days(_allowance_time(now))):
            raise _limit_reached(family)

    def pause_family(self, family, reason):
        """Stop one strategy's new entries; the owner's global Live switch is untouched.

        App interpretation: a rejected, replaced or unreconciled QQQ order is a
        Socrates problem, so only Socrates is deselected. This is the same durable
        write as the owner's strategy toggle (strategy_selection plus an
        authorization generation bump, so any prepared plan expires), in one
        transaction with a 'strategy_paused' journal entry. Crypto keeps trading,
        and position management never consults the selection. An already paused
        family is left alone: no repeated events or generation churn. The owner
        re-enables Socrates from the strategy controls after reviewing Alpaca.
        """
        if family != 'socrates':
            raise ValueError('Only the Socrates family is paused through the main ledger')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('A pause needs a reason')
        at = datetime.now(timezone.utc).isoformat()
        body = {'family': family, 'reason': reason, 'at': at,
                'note': 'Global Live and the crypto strategy are unchanged; existing positions continue their exits.'}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            selection = json.loads(db.execute('SELECT body FROM strategy_selection WHERE id=1').fetchone()[0])
            if selection.get('socrates') is False:
                return None
            db.execute('UPDATE strategy_selection SET body=? WHERE id=1', (json.dumps({'socrates': False}),))
            db.execute('UPDATE authorization_generation SET generation=generation+1 WHERE id=1')
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)', (at, 'strategy_paused', json.dumps(body)))
        notify('strategy_paused', body)
        return body

    def settings(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])

    def strategy_selection(self):
        with self.connect() as db:
            result = json.loads(db.execute('SELECT body FROM strategy_selection WHERE id=1').fetchone()[0])
            if set(result) != {'socrates'} or type(result['socrates']) is not bool:
                raise ValueError('Strategy selection could not be validated')
            return result

    def deployment_permission(self):
        """One read transaction covers all permissions/settings during a rollout."""
        with self.connect() as db:
            db.execute('BEGIN')
            master = self._entry_authorization(db)
            selection = json.loads(db.execute('SELECT body FROM strategy_selection WHERE id=1').fetchone()[0])
            crypto_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crypto_control'").fetchone()
            crypto = json.loads(db.execute('SELECT body FROM crypto_control WHERE id=1').fetchone()[0]) if crypto_exists else None
            return {**master, 'strategy_selection':selection, 'crypto_control':crypto}

    @staticmethod
    def _entry_authorization(db):
        row = db.execute('SELECT (SELECT body FROM control WHERE id=1),'
                         '(SELECT body FROM settings WHERE id=1),'
                         '(SELECT generation FROM authorization_generation WHERE id=1)').fetchone()
        return {'control': json.loads(row[0]), 'settings': json.loads(row[1]), 'generation': row[2]}

    def entry_authorization(self):
        """One SQLite snapshot of the permission/settings generation used by a plan."""
        with self.connect() as db:
            return self._entry_authorization(db)

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

    def decision_history(self, limit=50, before_id=None):
        """Bounded read-only pages for remote audits; repeated observations keep one ID."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Decision limit must be between 1 and 100')
        if before_id is not None and (type(before_id) is not int or not 1 <= before_id <= 2**63 - 1):
            raise ValueError('Decision cursor must be a positive signed 64-bit integer')
        where = ' WHERE id < ?' if before_id is not None else ''
        params = ([before_id] if before_id is not None else []) + [limit + 1]
        with self.connect() as db:
            rows = db.execute('SELECT id,first_observed_at,last_observed_at,observation_count,body '
                              'FROM decision_traces' + where + ' ORDER BY id DESC LIMIT ?', params).fetchall()
        entries = [self._decision_row(row) for row in rows[:limit]]
        return {'entries': entries, 'next_before_id': entries[-1]['id'] if len(rows) > limit else None,
                'order': 'descending insertion ID; repeated evidence updates its existing row',
                'historical_only': True}

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

    def latest_execution_check(self):
        with self.connect() as db:
            row = db.execute('SELECT id,first_observed_at,last_observed_at,observation_count,body '
                             'FROM execution_checks ORDER BY last_observed_at DESC,id DESC LIMIT 1').fetchone()
        return self._decision_row(row)

    def session_review(self, day=None):
        """Distinct events seen in the retained journal, never a count of polling ticks."""
        from collections import Counter
        from datetime import date, timedelta
        from zoneinfo import ZoneInfo
        local = ZoneInfo('America/New_York')
        day = datetime.now(local).date() if day is None else date.fromisoformat(day)
        cached = self._session_cache.get(day.isoformat())
        if cached and monotonic() - cached[0] < 30:
            return deepcopy(cached[1])
        begin = datetime.combine(day, datetime.min.time(), local)
        end = begin + timedelta(days=1)
        params = (begin.isoformat(), end.isoformat())
        with self.connect() as db:
            traces = [json.loads(row[0]) for row in db.execute(
                'SELECT body FROM decision_traces WHERE julianday(last_observed_at)>=julianday(?) '
                'AND julianday(first_observed_at)<julianday(?) ORDER BY julianday(last_observed_at),id', params)]
            execution = [json.loads(row[0]) for row in db.execute(
                'SELECT body FROM execution_checks WHERE julianday(last_observed_at)>=julianday(?) '
                'AND julianday(first_observed_at)<julianday(?) ORDER BY julianday(last_observed_at),id', params)]
            trades = [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM trades WHERE julianday(json_extract(body,'$.created_at'))>=julianday(?) "
                "AND julianday(json_extract(body,'$.created_at'))<julianday(?)", params)]
        stage_names = ('Nasdaq level event', 'Magnificent Seven at their zones', 'Actual VIX zone reaction', 'Stop and target')
        passed = {name: set() for name in stage_names}
        events, blockers, checkpoints = set(), {}, set()
        history_complete = True
        for trace in traces:
            checkpoints.add(trace.get('checkpoint_at'))
            if 'candidate_diagnostics' not in trace:
                history_complete = False
            candidates = trace.get('candidate_diagnostics', trace.get('strategies', []))
            for candidate in candidates:
                identity = candidate.get('event_id')
                if not identity:
                    continue
                events.add(identity)
                checks = {check['name']: check.get('passed') is True for check in candidate.get('checks', [])}
                first = next((name for name in stage_names if not checks.get(name)), None)
                blockers[identity] = first or 'Signal qualified; broker checks separate'
                for name in stage_names:
                    if not checks.get(name):
                        break
                    passed[name].add(identity)
        broker_blocks = {}
        for check in execution:
            signal = check.get('selected_entry_signal') or check.get('signal') or {}
            identity = signal.get('event_id')
            if identity and check.get('outcome') in ('waiting', 'feed_error', 'invalid_data', 'unexpected_error', 'attention', 'order_rejected'):
                broker_blocks[identity] = check.get('gate', 'unknown')
            elif identity and check.get('outcome') == 'entry_planned':
                broker_blocks.pop(identity, None)
        submitted, filled, protected, finished = set(), set(), set(), set()
        for trade in trades:
            key = trade['id']
            entry = trade.get('ops', {}).get('entry', {})
            if entry.get('state') in ('attempted', 'rejected'):
                submitted.add(key)
            try:
                if float(entry.get('last_seen', {}).get('filled_qty', 0)) > 0:
                    filled.add(key)
            except (ValueError, TypeError):
                pass
            stop = trade.get('ops', {}).get('stop', {}).get('last_seen', {})
            if stop.get('status') in ('new', 'partially_filled', 'filled'):
                protected.add(key)
            if trade.get('stage') == 'finished':
                finished.add(key)
        today_ids = {trade['id'] for trade in trades}
        for check in execution:
            trade = check.get('trade') or {}
            if (trade.get('id') in today_ids and
                    trade.get('operation_states', {}).get('stop', {}).get('broker_status') in ('new', 'partially_filled', 'filled')):
                protected.add(trade['id'])
        result = {'status':'available', 'day':day.isoformat(), 'timezone':'America/New_York',
                'generated_at':datetime.now(timezone.utc).isoformat(), 'refresh_seconds':30,
                'historical_only':True, 'complete_candidate_coverage':history_complete,
                'scope':'Retained observations only; distinct events may pass checks at different observations. No-trade is not a profitability result.',
                'checkpoints':len(checkpoints), 'events_seen':len(events),
                'stages':[{'label':name, 'count':len(passed[name])} for name in stage_names],
                'latest_signal_blockers':dict(Counter(blockers.values())),
                'latest_execution_blockers':dict(Counter(broker_blocks.values())),
                'orders':{'submission_attempts':len(submitted), 'confirmed_entries':len(filled),
                          'confirmed_protection':len(protected), 'finished_lifecycles':len(finished)},
                'latest_checks':[{'at':t['captured_at'], 'strategy':t.get('setup', {}).get('strategy_id'),
                                  'blocker':(t.get('first_blocker') or {}).get('name') or 'Signal qualified; broker checks separate'}
                                 for t in traces[-8:][::-1]]}
        self._session_cache = {day.isoformat():(monotonic(), deepcopy(result))}
        return result

    def execution_history(self, limit=50, before_id=None):
        """Historical observations only; reading these cannot authorize an order."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Execution limit must be between 1 and 100')
        if before_id is not None and (type(before_id) is not int or not 1 <= before_id <= 2**63 - 1):
            raise ValueError('Execution cursor must be a positive signed 64-bit integer')
        where = ' WHERE id < ?' if before_id is not None else ''
        params = ([before_id] if before_id is not None else []) + [limit + 1]
        with self.connect() as db:
            rows = db.execute('SELECT id,first_observed_at,last_observed_at,observation_count,body '
                              'FROM execution_checks' + where + ' ORDER BY id DESC LIMIT ?', params).fetchall()
        entries = [self._decision_row(row) for row in rows[:limit]]
        return {'entries': entries, 'next_before_id': entries[-1]['id'] if len(rows) > limit else None,
                'order': 'descending insertion ID; repeated evidence updates its existing row',
                'historical_only': True}

    def record_execution_check(self, record):
        """Separate bounded diagnostics; callers isolate any failure from execution."""
        if record.get('version') != 'execution-check-v1':
            raise ValueError('Unsupported execution check version')
        body = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(body.encode()) > 16384:
            raise ValueError('Execution check exceeds storage limit')
        # A refreshed analysis/worker clock alone is not a new execution state.
        # The latest body preserves correlation times; first/last/count retain
        # the observed duration of an otherwise identical checkpoint state.
        evidence = {key: value for key, value in record.items() if key not in ('captured_at', 'analysis_at')}
        key = sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        at = record['captured_at']
        with self.connect() as db:
            # Diagnostics must not hold up position management behind a busy
            # ledger. A short wait fails into the executor's logging error flag.
            db.execute('PRAGMA busy_timeout=50')
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO execution_checks(checkpoint_at,fingerprint,first_observed_at,last_observed_at,observation_count,body) '
                       'VALUES(?,?,?,?,1,?) ON CONFLICT(checkpoint_at,fingerprint) DO UPDATE SET '
                       'last_observed_at=excluded.last_observed_at,observation_count=observation_count+1,body=excluded.body',
                       (record['checkpoint_at'], key, at, at, body))
            row = db.execute('SELECT id,first_observed_at,last_observed_at,observation_count,body '
                             'FROM execution_checks WHERE checkpoint_at=? AND fingerprint=?',
                             (record['checkpoint_at'], key)).fetchone()
            db.execute('DELETE FROM execution_checks WHERE id IN (SELECT id FROM execution_checks '
                       'ORDER BY last_observed_at DESC,id DESC LIMIT -1 OFFSET ?)', (EXECUTION_CHECK_RETENTION,))
        return self._decision_row(row)

    def save(self, settings):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE settings SET body=? WHERE id=1', (json.dumps(settings),))
            db.execute('UPDATE authorization_generation SET generation=generation+1 WHERE id=1')
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), 'settings_saved', json.dumps(settings)))

    def event(self, kind, body):
        with self.connect() as db:
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (datetime.now(timezone.utc).isoformat(), kind, json.dumps(body)))
        if kind in ALERT_KINDS:
            notify(kind, body)  # After commit; best-effort and never raises.

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
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE control SET body=? WHERE id=1', (json.dumps(value),))
            db.execute('UPDATE authorization_generation SET generation=generation+1 WHERE id=1')
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

    @staticmethod
    def _retryable_unsent_entry(row, identity):
        """Only a finished, durably never-attempted entry may be reserved again."""
        if row is None or row[0] != 1:
            return False
        try:
            trade = json.loads(row[1])
            operations = trade['ops']
            entry = operations['entry']
            return (trade['id'] == identity and trade['stage'] == 'finished'
                    and trade.get('reason') == 'Entry expired before submission; no order sent'
                    and 'filled_qty' not in trade and set(operations) == {'entry'}
                    and entry['state'] == 'prepared' and 'last_seen' not in entry
                    and entry['payload']['client_order_id'] == f'pvt-{identity}-entry')
        except (KeyError, TypeError, ValueError):
            return False

    def entry_consumed(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT finished,body FROM trades WHERE id=?', (identity,)).fetchone()
        return row is not None and not self._retryable_unsent_entry(row, identity)

    def trade_results(self):
        from .performance import trade_result
        with self.connect() as db:
            rows=db.execute('SELECT body FROM trades WHERE finished=1 ORDER BY rowid DESC LIMIT 20').fetchall()
        return [trade_result(json.loads(row[0])) for row in rows]

    def reserve_trade(self, trade, *, expected_authorization=None):
        # Unique signal + single active slot survive restarts and competing workers.
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if expected_authorization is not None and self._entry_authorization(db) != expected_authorization:
                    return False
                row = db.execute('SELECT finished,body FROM trades WHERE id=?', (trade['id'],)).fetchone()
                if row is None:
                    db.execute('INSERT INTO trades(id,finished,body) VALUES(?,0,?)', (trade['id'], json.dumps(trade)))
                elif self._retryable_unsent_entry(row, trade['id']):
                    # Preserve the abandoned reservation in the append-only
                    # journal in the same transaction as its replacement.
                    # Its client ID was never posted: claim_operation durably
                    # changes prepared -> attempted before every broker POST.
                    db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                               (datetime.now(timezone.utc).isoformat(), 'unsubmitted_entry_retried', row[1]))
                    db.execute('UPDATE trades SET finished=0,body=? WHERE id=?',
                               (json.dumps(trade), trade['id']))
                else:
                    return False
            return True
        except sqlite3.IntegrityError:
            return False

    def save_trade(self, trade, finished=False):
        with self.connect() as db:
            db.execute('UPDATE trades SET body=?,finished=? WHERE id=?', (json.dumps(trade), int(finished), trade['id']))

    def expire_prepared_entry(self, expected, completed_at):
        """Retire this exact reservation only while its durable entry is unsent."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT finished,body FROM trades WHERE id=?', (expected['id'],)).fetchone()
            if row is None or row[0] != 0:
                return None
            trade = json.loads(row[1])
            if (trade != expected or trade.get('stage') != 'entering'
                    or trade.get('ops', {}).get('entry', {}).get('state') != 'prepared'):
                return None
            trade.update(stage='finished', reason='Entry expired before submission; no order sent',
                         completed_at=completed_at)
            if not self._retryable_unsent_entry((1, json.dumps(trade)), trade['id']):
                return None
            db.execute('UPDATE trades SET body=?,finished=1 WHERE id=?', (json.dumps(trade), trade['id']))
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (completed_at, 'trade_finished', json.dumps({'symbol': trade['symbol'], 'reason': trade['reason']})))
            return trade

    def claim_operation(self, trade_id, name, *, expected_entry=None, attempted_at=None):
        """Durably mark the single allowed POST attempt before making a network call."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT finished,body FROM trades WHERE id=?', (trade_id,)).fetchone()
            if row is None or row[0] != 0:
                return False
            trade = json.loads(row[1])
            if name == 'entry' and expected_entry is not None and trade != expected_entry:
                return False
            if name == 'entry' and trade.get('authorization') is not None:
                if self._entry_authorization(db) != trade['authorization']:
                    return False
            if trade['ops'][name]['state'] != 'prepared':
                return False
            if name == 'entry':
                _reserve_session_entry(db, trade, 'socrates', attempted_at or datetime.now(timezone.utc))
            trade['ops'][name]['state'] = 'attempted'
            db.execute('UPDATE trades SET body=? WHERE id=?', (json.dumps(trade), trade_id))
            return True

    def abort_claimed_entry(self, expected, completed_at):
        """CAS for the submitting process's proven no-network-call result, never recovery guesses."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT finished,body FROM trades WHERE id=?', (expected['id'],)).fetchone()
            if row is None or row[0] != 0:
                return None
            trade = json.loads(row[1])
            entry = trade.get('ops', {}).get('entry', {})
            if (trade != expected or trade.get('stage') != 'entering'
                    or set(trade.get('ops', {})) != {'entry'} or entry.get('state') != 'attempted'
                    or 'last_seen' in entry or 'filled_qty' in trade):
                return None
            entry['state'] = 'aborted_before_submit'
            trade.update(stage='finished', completed_at=completed_at,
                         reason='Entry admission expired before broker submission; no order sent. This event remains consumed.')
            db.execute('UPDATE trades SET body=?,finished=1 WHERE id=?', (json.dumps(trade), trade['id']))
            db.execute('INSERT INTO events(at,kind,body) VALUES(?,?,?)',
                       (completed_at, 'trade_finished', json.dumps({'symbol': trade['symbol'], 'reason': trade['reason']})))
            return trade
