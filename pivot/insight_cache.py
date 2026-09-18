"""Read-only VIX collection with a durable, conservative free-plan budget.

Cached provider timestamps are never advanced by reads. Each scheduled history
slot gets at most two attempts; recovery uses the same cross-process allowance.
"""
import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .insight_probe import ProbeError, _get
from .insight_validation import _aware, _calendar

SYMBOL = 'CBOE:VIX'
PREFIX = '/v3/symbols/CBOE%3AVIX'
INTERVAL = timedelta(minutes=15)
GRACE = timedelta(seconds=30)
RETRY_BACKOFF = timedelta(seconds=30)
RECOVERY_MONTH_LIMIT = 50
RECOVERY_DAY_LIMIT = 4


class LatePublication(ValueError):
    """Well-formed actual-index data whose required observation is not published yet."""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid number')
    return value


def _source_time(value, *, milliseconds=False):
    return datetime.fromtimestamp(_number(value) / (1000 if milliseconds else 1), timezone.utc)


def _validate(kind, payload, now, required_end=None):
    """Validate identity, source freshness, and structure before durable storage."""
    if payload.get('error'):
        raise ValueError('Provider error')
    if kind == 'quote':
        rows = payload.get('data')
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('Ambiguous quote')
        row = rows[0]
        if (row.get('code') != SYMBOL or row.get('error') or
                _number(row['delay_seconds']) != 0 or _number(row['last_price']) <= 0):
            raise ValueError('Invalid quote')
        updated = _source_time(row['lp_time'])
        if updated > now:
            raise ValueError('Future source')
        if payload.get('last_update') is not None:
            outer = _source_time(payload['last_update'], milliseconds=True)
            if outer > now:
                raise ValueError('Future response')
            if now - outer > timedelta(seconds=90):
                raise LatePublication('Stale response')
    elif kind == 'metadata':
        if (payload.get('code') != SYMBOL or str(payload.get('type')).upper() != 'INDEX' or
                _number(payload['delay_seconds']) != 0):
            raise ValueError('Wrong instrument or entitlement')
        return None
    else:
        rows = payload.get('series')
        if (payload.get('code') != SYMBOL or payload.get('bar_type') != '15m' or
                not isinstance(rows, list) or not 1 <= len(rows) <= 1000):
            raise ValueError('Invalid series')
        updated = _source_time(payload['last_update'], milliseconds=True)
        if updated > now:
            raise ValueError('Future source')
        previous = None
        for row in rows:
            start = _source_time(row['time'])
            prices = [_number(row[field]) for field in ('open', 'high', 'low', 'close')]
            opening, high, low, close = prices
            if (not 0 < low <= min(opening, close) <= max(opening, close) <= high or
                    start.second or start.microsecond or start.minute % 15 or start > updated or
                    (previous is not None and start <= previous)):
                raise ValueError('Invalid candle')
            previous = start
        end = _source_time(payload['bar_end'])
        if end not in (previous + INTERVAL, previous + INTERVAL - timedelta(seconds=1)):
            raise ValueError('Invalid interval')
        if required_end is not None and not any(
                _source_time(row['time']) + INTERVAL == required_end <= updated for row in rows):
            raise LatePublication('Latest completed candle missing')
    if updated > now:
        raise ValueError('Future source')
    if kind == 'quote' and now - updated > timedelta(seconds=90):
        raise LatePublication('Stale source')
    return updated.isoformat()


class InsightCache:
    def __init__(self, path, key, session=None, *, monthly_limit=900, reserved=50, clock=None):
        if (isinstance(monthly_limit, bool) or not isinstance(monthly_limit, int) or
                not 1 <= monthly_limit <= 900 or isinstance(reserved, bool) or
                not isinstance(reserved, int) or not 0 <= reserved <= monthly_limit):
            raise ValueError('Invalid local request budget')
        if not isinstance(key, str) or not key or any(c.isspace() for c in key):
            raise ValueError('A local InsightSentry key is required')
        self.path, self._key = Path(path), key
        self.session = session if session is not None else requests.Session()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.monthly_limit, self.reserved = monthly_limit, reserved
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS insight_requests ('
                       'id INTEGER PRIMARY KEY, at REAL NOT NULL, month TEXT NOT NULL, '
                       'kind TEXT NOT NULL, slot TEXT NOT NULL, error TEXT, '
                       'finished INTEGER NOT NULL DEFAULT 0, UNIQUE(kind, slot))')
            db.execute('CREATE TABLE IF NOT EXISTS insight_cache ('
                       'kind TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at TEXT NOT NULL, '
                       'source_updated_at TEXT)')
            # Additive migration keeps every old quota claim consumed.
            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(insight_requests)')}
            for name, definition in (('base_slot', 'TEXT'), ('attempt', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('retryable', 'INTEGER NOT NULL DEFAULT 0'), ('retry_at', 'REAL')):
                if name not in columns:
                    db.execute(f'ALTER TABLE insight_requests ADD COLUMN {name} {definition}')
            db.execute('UPDATE insight_requests SET base_slot=slot WHERE base_slot IS NULL')

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def diagnostics(self, now):
        now = _aware(now)
        with self._db() as db:
            used = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=?',
                              (now.strftime('%Y-%m'),)).fetchone()[0]
            last = db.execute('SELECT error, finished FROM insight_requests ORDER BY at DESC, id DESC LIMIT 1').fetchone()
            success = db.execute('SELECT MAX(received_at) FROM insight_cache').fetchone()[0]
            recovery_month, recovery_day = self._recovery_counts(db, now)
        return {'source': 'insightsentry', 'symbol': SYMBOL, 'budget_month': now.strftime('%Y-%m'),
                'budget_used': used + self.reserved, 'requests_recorded': used,
                'reserved_requests': self.reserved, 'budget_limit': self.monthly_limit,
                'recovery_month_used': recovery_month, 'recovery_month_limit': RECOVERY_MONTH_LIMIT,
                'recovery_day_used': recovery_day, 'recovery_day_limit': RECOVERY_DAY_LIMIT,
                'last_success_at': success, 'error': last[0] if last else None,
                'status': 'in_flight' if last and not last[1] else 'ready', 'next_refresh_at': None}

    def _cached(self, kind):
        with self._db() as db:
            row = db.execute('SELECT payload, received_at, source_updated_at FROM insight_cache WHERE kind=?', (kind,)).fetchone()
        if not row:
            return {'payload': None, 'received_at': None, 'source_updated_at': None}
        return {'payload': json.loads(row[0]), 'received_at': row[1], 'source_updated_at': row[2]}

    def _result(self, kind, now, status, next_at=None, error=None, *, slot=None):
        # Diagnostics summarize the whole collector; a particular data envelope
        # must describe only its own operation, never another endpoint's error.
        if error is None:
            if status in ('unavailable', 'retry_wait'):
                with self._db() as db:
                    row = db.execute('SELECT error FROM insight_requests WHERE kind=? '
                                     'ORDER BY at DESC, id DESC LIMIT 1', (kind,)).fetchone()
                error = row[0] if row else None
            elif status == 'budget_exhausted':
                error = 'The local monthly request allowance is exhausted.'
            elif status == 'rate_limited':
                error = 'The local request rate limit has been reached.'
        retry_at, retry_reason = None, None
        if slot is not None:
            with self._db() as db:
                attempt = db.execute('SELECT finished,error,attempt,retryable,retry_at FROM insight_requests '
                                     'WHERE kind=? AND base_slot=? ORDER BY attempt DESC LIMIT 1', (kind, slot)).fetchone()
                recovery_month, recovery_day = self._recovery_counts(db, now)
                total_used = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=?',
                                        (now.strftime('%Y-%m'),)).fetchone()[0]
            if attempt:
                if not attempt[0]:
                    retry_reason = 'Request outcome unknown; this claim cannot be retried'
                elif status == 'budget_exhausted' or (status != 'current' and
                        (attempt[1] or kind == 'quote') and total_used + self.reserved >= self.monthly_limit):
                    retry_reason = 'Total monthly allowance exhausted; no recovery request is permitted'
                elif attempt[2] >= 1 and (attempt[1] or (kind == 'quote' and status == 'cached')):
                    retry_reason = 'Recovery attempt consumed; waiting for the next data slot'
                elif attempt[1] and not attempt[3]:
                    retry_reason = 'Invalid or unverified response; waiting for the next data slot'
                elif recovery_month >= RECOVERY_MONTH_LIMIT or recovery_day >= RECOVERY_DAY_LIMIT:
                    retry_reason = 'Recovery allowance exhausted; waiting for the next data slot'
                elif attempt[2] == 0 and attempt[1] and attempt[3]:
                    retry_at = datetime.fromtimestamp(attempt[4], timezone.utc)
                    retry_reason = 'Temporary failure; one bounded recovery attempt is available'
                    if next_at is None or retry_at < next_at:
                        next_at = retry_at
                elif kind == 'quote' and attempt[2] == 0 and not attempt[1] and status != 'current':
                    source_at = self._cached('quote')['source_updated_at']
                    if source_at:
                        retry_at = max(now, _aware(source_at) + timedelta(seconds=90))
                        retry_reason = 'One bounded refresh is available after the quote expires'
                        next_at = retry_at
        if status == 'rate_limited':
            with self._db() as db:
                oldest = db.execute('SELECT at FROM insight_requests ORDER BY at DESC LIMIT 1 OFFSET 4').fetchone()
            retry_at = datetime.fromtimestamp(oldest[0] + 60, timezone.utc) if oldest else now + RETRY_BACKOFF
            next_at = max(now, retry_at)
            retry_reason = 'Shared request rate limit; waiting before another attempt'
        result = {**self.diagnostics(now), **self._cached(kind), 'status': status,
                  'retry_at': retry_at.isoformat() if retry_at else None, 'retry_reason': retry_reason,
                  'next_refresh_at': next_at.isoformat() if next_at else None, 'error': error}
        return result

    def _recovery_counts(self, db, now):
        month = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=? AND attempt>0',
                           (now.strftime('%Y-%m'),)).fetchone()[0]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day = db.execute('SELECT COUNT(*) FROM insight_requests WHERE at>=? AND attempt>0', (start,)).fetchone()[0]
        return month, day

    def _reserve(self, kind, slot, now, *, refresh_success=False):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT finished,error,attempt,retryable,retry_at FROM insight_requests '
                                  'WHERE kind=? AND base_slot=? ORDER BY attempt DESC LIMIT 1', (kind, slot)).fetchone()
            attempt = 0
            if existing:
                finished, error, prior_attempt, retryable, retry_at = existing
                if not finished:
                    return None, 'in_flight'
                if not error and not refresh_success:
                    return None, 'cached'
                if prior_attempt >= 1 or (error and not retryable):
                    return None, 'unavailable' if error else 'cached'
                if error and now.timestamp() < retry_at:
                    return None, 'retry_wait'
                month, day = self._recovery_counts(db, now)
                if month >= RECOVERY_MONTH_LIMIT or day >= RECOVERY_DAY_LIMIT:
                    return None, 'recovery_budget_exhausted'
                attempt = 1
            used = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=?', (now.strftime('%Y-%m'),)).fetchone()[0]
            if used + self.reserved >= self.monthly_limit:
                return None, 'budget_exhausted'
            recent = db.execute('SELECT COUNT(*) FROM insight_requests WHERE at>?',
                                (now.timestamp() - 60,)).fetchone()[0]
            if recent >= 5:
                return None, 'rate_limited'
            claim_slot = slot if attempt == 0 else slot + ':recovery'
            cursor = db.execute('INSERT INTO insight_requests (at,month,kind,slot,base_slot,attempt) VALUES (?,?,?,?,?,?)',
                                (now.timestamp(), now.strftime('%Y-%m'), kind, claim_slot, slot, attempt))
            return cursor.lastrowid, None

    def _request(self, kind, slot, now, path, params, next_at, required_end=None, *, refresh_success=False):
        request_id, status = self._reserve(kind, slot, now, refresh_success=refresh_success)
        if request_id is None:
            return self._result(kind, now, status, next_at, slot=slot)
        try:
            payload = _get(self.session, self._key, path, params)
            received = _aware(self._clock())
            if received < now:
                raise ValueError('Clock moved backwards during collection')
            updated = _validate(kind, payload, received, required_end)
            raw = json.dumps(payload, allow_nan=False, separators=(',', ':'))
            if self._key in json.dumps(payload, ensure_ascii=False):
                raise ValueError('Reflected credential')
        except (ProbeError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            retryable = isinstance(exc, LatePublication) or (isinstance(exc, ProbeError) and exc.retryable)
            failed_at = max(now, _aware(self._clock()))
            error = ('InsightSentry VIX observation is not published yet.' if isinstance(exc, LatePublication) else
                     'InsightSentry connection is temporarily unavailable.' if retryable else
                     'InsightSentry did not return valid, current VIX data.')
            with self._db() as db:
                db.execute('UPDATE insight_requests SET finished=1,error=?,retryable=?,retry_at=? WHERE id=?',
                           (error, int(retryable), (failed_at + RETRY_BACKOFF).timestamp() if retryable else None, request_id))
            return self._result(kind, failed_at, 'unavailable', next_at, error, slot=slot)
        with self._db() as db:
            db.execute('INSERT INTO insight_cache VALUES (?,?,?,?) ON CONFLICT(kind) DO UPDATE SET '
                       'payload=excluded.payload, received_at=excluded.received_at, source_updated_at=excluded.source_updated_at '
                       'WHERE excluded.received_at > insight_cache.received_at',
                       (kind, raw, received.isoformat(), updated))
            db.execute('UPDATE insight_requests SET finished=1,error=NULL WHERE id=?', (request_id,))
        if kind == 'metadata':
            next_at = received + timedelta(hours=24)
        return self._result(kind, received, 'current', next_at, error='', slot=slot)

    def history(self, now, sessions):
        now = _aware(now)
        try:
            calendar = _calendar(sessions)
            if calendar is None:
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._result('history', now, 'calendar_unavailable', error='A verified trading calendar is required.')
        active = [(opening, closing) for opening, closing in calendar.values()
                  if opening <= now < closing + timedelta(minutes=2)]
        upcoming = [opening for opening, _ in calendar.values() if opening > now]
        next_open = min(upcoming) if upcoming else None
        if active:
            opening, closing = active[0]
            boundary = max(0, min(int((now - opening - GRACE) / INTERVAL), int((closing - opening) / INTERVAL)))
            required_end = opening + boundary * INTERVAL
            if not boundary:
                completed = [end for _, end in calendar.values() if end < opening]
                required_end = max(completed) if completed else None
            slot = (opening + boundary * INTERVAL).isoformat()
            next_at = opening + (boundary + 1) * INTERVAL + GRACE
            if next_at > closing + GRACE:
                next_at = next_open
        else:
            completed = [end for _, end in calendar.values() if end + GRACE <= now]
            if not completed:
                return self._result('history', now, 'market_closed', next_open)
            required_end = max(completed)
            slot, next_at = required_end.isoformat(), next_open
            cached = self._cached('history')['payload']
            if cached:
                try:
                    _validate('history', cached, now, required_end)
                    return self._result('history', now, 'cached', next_at)
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    pass
        # After-close catch-up uses the final session slot, including its single
        # bounded recovery allowance; repeated polling cannot create more claims.
        return self._request('history', slot, now, PREFIX + '/series',
                             {'bar_type': 'minute', 'bar_interval': 15, 'dp': 1000,
                              'extended': 'false', 'abbr': 'false'}, next_at, required_end)

    def metadata(self, now):
        now = _aware(now)
        cached = self._cached('metadata')
        if cached['received_at']:
            refresh = _aware(cached['received_at']) + timedelta(hours=24)
            if _aware(cached['received_at']) <= now < refresh:
                return self._result('metadata', now, 'cached', refresh)
        # Failed metadata has one bounded recovery, then a new slot next hour;
        # successful metadata stays cached for a full day. All calls share quota.
        return self._request('metadata', now.strftime('%Y-%m-%dT%H'), now, PREFIX + '/info', None,
                             now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

    def quote(self, now):
        now = _aware(now)
        cached = self._cached('quote')
        if cached['source_updated_at']:
            source_at = _aware(cached['source_updated_at'])
            if timedelta(0) <= now - source_at < timedelta(seconds=90):
                return self._result('quote', now, 'cached', source_at + timedelta(seconds=90))
        # At most one bounded recovery/refresh per candle shares the total budget.
        # Merely polling cannot extend the original quote's timestamp.
        slot = int(now.timestamp() // 900)
        result = self._request('quote', str(slot), now, '/v3/symbols/quotes', {'codes': SYMBOL},
                               datetime.fromtimestamp((slot + 1) * 900, timezone.utc), refresh_success=True)
        if result['status'] == 'cached':
            result['status'] = 'stale'
        return result

    def peek_quote(self, now):
        """Return the last quote and its actual freshness without spending quota."""
        now = _aware(now)
        result = self._result('quote', now, 'unavailable', slot=str(int(now.timestamp() // 900)))
        if result['payload'] is None or result['source_updated_at'] is None:
            return result
        source_at = _aware(result['source_updated_at'])
        fresh = timedelta(0) <= now - source_at < timedelta(seconds=90)
        result['status'] = 'cached' if fresh else 'stale'
        result['error'] = None
        if fresh:
            result.update(retry_at=None, retry_reason=None)
        next_at = (source_at + timedelta(seconds=90) if fresh else
                   datetime.fromtimestamp((int(now.timestamp() // 900) + 1) * 900, timezone.utc))
        result['next_refresh_at'] = result.get('retry_at') or next_at.isoformat()
        return result
