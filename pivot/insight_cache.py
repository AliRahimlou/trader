"""Read-only VIX collection with a durable, conservative free-plan budget.

Cached provider timestamps are never advanced by reads. Each scheduled history
slot gets one attempt, including failures; all kinds share a cross-process ledger.
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
        if payload.get('last_update') is not None:
            outer = _source_time(payload['last_update'], milliseconds=True)
            if not timedelta(0) <= now - outer <= timedelta(seconds=90):
                raise ValueError('Stale response')
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
            raise ValueError('Latest completed candle missing')
    if updated > now or (kind == 'quote' and now - updated > timedelta(seconds=90)):
        raise ValueError('Stale source')
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

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def diagnostics(self, now):
        now = _aware(now)
        with self._db() as db:
            used = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=?',
                              (now.strftime('%Y-%m'),)).fetchone()[0]
            last = db.execute('SELECT error, finished FROM insight_requests ORDER BY at DESC, id DESC LIMIT 1').fetchone()
            success = db.execute('SELECT MAX(received_at) FROM insight_cache').fetchone()[0]
        return {'source': 'insightsentry', 'symbol': SYMBOL, 'budget_month': now.strftime('%Y-%m'),
                'budget_used': used + self.reserved, 'requests_recorded': used,
                'reserved_requests': self.reserved, 'budget_limit': self.monthly_limit,
                'last_success_at': success, 'error': last[0] if last else None,
                'status': 'in_flight' if last and not last[1] else 'ready', 'next_refresh_at': None}

    def _cached(self, kind):
        with self._db() as db:
            row = db.execute('SELECT payload, received_at, source_updated_at FROM insight_cache WHERE kind=?', (kind,)).fetchone()
        if not row:
            return {'payload': None, 'received_at': None, 'source_updated_at': None}
        return {'payload': json.loads(row[0]), 'received_at': row[1], 'source_updated_at': row[2]}

    def _result(self, kind, now, status, next_at=None, error=None):
        # Diagnostics summarize the whole collector; a particular data envelope
        # must describe only its own operation, never another endpoint's error.
        if error is None:
            if status == 'unavailable':
                with self._db() as db:
                    row = db.execute('SELECT error FROM insight_requests WHERE kind=? '
                                     'ORDER BY at DESC, id DESC LIMIT 1', (kind,)).fetchone()
                error = row[0] if row else None
            elif status == 'budget_exhausted':
                error = 'The local monthly request allowance is exhausted.'
            elif status == 'rate_limited':
                error = 'The local request rate limit has been reached.'
        result = {**self.diagnostics(now), **self._cached(kind), 'status': status,
                  'next_refresh_at': next_at.isoformat() if next_at else None, 'error': error}
        return result

    def _reserve(self, kind, slot, now):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT finished, error FROM insight_requests WHERE kind=? AND slot=?', (kind, slot)).fetchone()
            if existing:
                return None, ('in_flight' if not existing[0] else 'unavailable' if existing[1] else 'cached')
            used = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=?', (now.strftime('%Y-%m'),)).fetchone()[0]
            if used + self.reserved >= self.monthly_limit:
                return None, 'budget_exhausted'
            recent = db.execute('SELECT COUNT(*) FROM insight_requests WHERE at>?',
                                (now.timestamp() - 60,)).fetchone()[0]
            if recent >= 5:
                return None, 'rate_limited'
            cursor = db.execute('INSERT INTO insight_requests (at,month,kind,slot) VALUES (?,?,?,?)',
                                (now.timestamp(), now.strftime('%Y-%m'), kind, slot))
            return cursor.lastrowid, None

    def _request(self, kind, slot, now, path, params, next_at, required_end=None):
        request_id, status = self._reserve(kind, slot, now)
        if request_id is None:
            return self._result(kind, now, status, next_at)
        try:
            payload = _get(self.session, self._key, path, params)
            received = _aware(self._clock())
            if received < now:
                raise ValueError('Clock moved backwards during collection')
            updated = _validate(kind, payload, received, required_end)
            raw = json.dumps(payload, allow_nan=False, separators=(',', ':'))
            if self._key in json.dumps(payload, ensure_ascii=False):
                raise ValueError('Reflected credential')
        except (ProbeError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            error = 'InsightSentry did not return valid, current VIX data.'
            with self._db() as db:
                db.execute('UPDATE insight_requests SET finished=1,error=? WHERE id=?', (error, request_id))
            return self._result(kind, now, 'unavailable', next_at, error)
        with self._db() as db:
            db.execute('INSERT INTO insight_cache VALUES (?,?,?,?) ON CONFLICT(kind) DO UPDATE SET '
                       'payload=excluded.payload, received_at=excluded.received_at, source_updated_at=excluded.source_updated_at '
                       'WHERE excluded.received_at > insight_cache.received_at',
                       (kind, raw, received.isoformat(), updated))
            db.execute('UPDATE insight_requests SET finished=1,error=NULL WHERE id=?', (request_id,))
        if kind == 'metadata':
            next_at = received + timedelta(hours=24)
        return self._result(kind, received, 'current', next_at, error='')

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
        # The latest completed session gets one catch-up attempt outside market
        # hours. Its slot is identical to the final regular-session collection.
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
        # A transient failure gets another chance next hour, while successful
        # metadata stays cached for a full day. Failed attempts share the budget.
        return self._request('metadata', now.strftime('%Y-%m-%dT%H'), now, PREFIX + '/info', None,
                             now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

    def quote(self, now):
        now = _aware(now)
        cached = self._cached('quote')
        if cached['source_updated_at']:
            source_at = _aware(cached['source_updated_at'])
            if timedelta(0) <= now - source_at < timedelta(seconds=90):
                return self._result('quote', now, 'cached', source_at + timedelta(seconds=90))
        # A ready setup gets one quote attempt per candle, not repeated polling
        # every time the execution loop sees the same blocked setup.
        slot = int(now.timestamp() // 900)
        result = self._request('quote', str(slot), now, '/v3/symbols/quotes', {'codes': SYMBOL},
                               datetime.fromtimestamp((slot + 1) * 900, timezone.utc))
        if result['status'] == 'cached':
            result['status'] = 'stale'
        return result

    def peek_quote(self, now):
        """Return the last quote and its actual freshness without spending quota."""
        now = _aware(now)
        result = self._result('quote', now, 'unavailable')
        if result['payload'] is None or result['source_updated_at'] is None:
            return result
        source_at = _aware(result['source_updated_at'])
        fresh = timedelta(0) <= now - source_at < timedelta(seconds=90)
        result['status'] = 'cached' if fresh else 'stale'
        result['error'] = None
        next_at = (source_at + timedelta(seconds=90) if fresh else
                   datetime.fromtimestamp((int(now.timestamp() // 900) + 1) * 900, timezone.utc))
        result['next_refresh_at'] = next_at.isoformat()
        return result
