"""Read-only VIX collection with a durable, conservative free-plan budget.

Cached provider timestamps are never advanced by reads. Every request is
claimed durably before it is sent, so a crash or unknown outcome consumes its
claim. The slot policy is time based (app choice, not a video rule):

* A data slot is one 15-minute candle (history), one hour (metadata) or one
  900-second bucket (entry quotes). Within a slot, another attempt may follow
  a retryable failure after ``RETRY_BACKOFF`` for as long as the slot lasts,
  up to ``SLOT_ATTEMPT_LIMIT`` claims per slot in total.
* A late publication (well-formed data whose required candle or fresh quote
  is not out yet) is the normal provider behaviour; its retries are bounded
  only by the slot cap and the monthly headroom below. A genuine transport or
  HTTP failure is a *recovery* and is additionally capped by
  ``RECOVERY_DAY_LIMIT`` and ``RECOVERY_MONTH_LIMIT``. Invalid or unverified
  responses never earn another attempt in the same slot.
* Entry quotes may succeed at most ``QUOTE_REFRESH_LIMIT`` times per slot
  (the first quote included); a transient failure is retryable after the
  backoff inside the slot.

Monthly headroom arithmetic (InsightSentry Free: 1,000 requests/month,
5/minute). The local ceiling is ``monthly_limit`` = 900 with ``reserved`` = 50
kept for manual probes, so the app itself sends at most 850 per month. First
attempts of every slot are admitted until that ceiling. Retries are admitted
only while the scheduled remainder of the month still fits::

    used + reserved + PROJECTED_DAILY_REQUESTS × remaining weekdays < monthly_limit

where ``PROJECTED_DAILY_REQUESTS`` = 30 covers one trading day: 26 regular
session candle closes (6.5 h / 15 min), one after-close catch-up, one
metadata refresh and two entry quotes. Remaining weekdays count today. A
23-weekday month therefore starts with 900 − 50 − 690 = 160 requests of retry
headroom; each scheduled day spends about 30 and releases 30 of projection,
so the headroom shrinks only by retries actually made. When it is gone, retries
stop while scheduled first attempts continue to the hard ceiling. The rolling
5-per-minute check applies to every claim.
"""
import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .data_health import SOURCE_CLOCK_SKEW_SECONDS
from .insight_probe import ProbeError, _get
from .insight_validation import _aware, _calendar

SYMBOL = 'CBOE:VIX'
PREFIX = '/v3/symbols/CBOE%3AVIX'
INTERVAL = timedelta(minutes=15)
GRACE = timedelta(seconds=30)
RETRY_BACKOFF = timedelta(seconds=30)
# A source timestamp this far ahead of the host clock is clock skew, not fraud.
CLOCK_SKEW = timedelta(seconds=SOURCE_CLOCK_SKEW_SECONDS)
SLOT_ATTEMPT_LIMIT = 6
QUOTE_REFRESH_LIMIT = 4
RECOVERY_MONTH_LIMIT = 120
RECOVERY_DAY_LIMIT = 12
PROJECTED_DAILY_REQUESTS = 30


class LatePublication(ValueError):
    """Well-formed actual-index data whose required observation is not published yet."""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid number')
    return value


def _source_time(value, *, milliseconds=False):
    return datetime.fromtimestamp(_number(value) / (1000 if milliseconds else 1), timezone.utc)


def _projected_need(now):
    """Scheduled requests still expected this month: weekdays from today through month end."""
    day = now.date()
    last = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    weekdays = sum(1 for offset in range((last - day).days + 1)
                   if (day + timedelta(days=offset)).weekday() < 5)
    return weekdays * PROJECTED_DAILY_REQUESTS


def _validate(kind, payload, now, required_end=None):
    """Validate identity, source freshness, and structure before durable storage.

    Source timestamps up to ``CLOCK_SKEW`` ahead of the host clock are accepted;
    anything further ahead is still rejected as a future source.
    """
    if payload.get('error'):
        raise ValueError('Provider error')
    horizon = now + CLOCK_SKEW
    if kind == 'quote':
        rows = payload.get('data')
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('Ambiguous quote')
        row = rows[0]
        if (row.get('code') != SYMBOL or row.get('error') or
                _number(row['delay_seconds']) != 0 or _number(row['last_price']) <= 0):
            raise ValueError('Invalid quote')
        updated = _source_time(row['lp_time'])
        if updated > horizon:
            raise ValueError('Future source')
        if payload.get('last_update') is not None:
            outer = _source_time(payload['last_update'], milliseconds=True)
            if outer > horizon:
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
        if updated > horizon:
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
    if updated > horizon:
        raise ValueError('Future source')
    if kind == 'quote' and now - updated > timedelta(seconds=90):
        raise LatePublication('Stale source')
    return updated.isoformat()


def _quote_fresh(now, source_at):
    return -CLOCK_SKEW <= now - source_at < timedelta(seconds=90)


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
            # Additive migration keeps every old quota claim consumed. Legacy
            # extra attempts predate the late/recovery split and stay counted
            # as recoveries, which is the conservative reading.
            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(insight_requests)')}
            for name, definition in (('base_slot', 'TEXT'), ('attempt', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('retryable', 'INTEGER NOT NULL DEFAULT 0'), ('retry_at', 'REAL'),
                                     ('late', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('recovery', 'INTEGER NOT NULL DEFAULT 0')):
                if name not in columns:
                    db.execute(f'ALTER TABLE insight_requests ADD COLUMN {name} {definition}')
                    if name == 'recovery':
                        db.execute('UPDATE insight_requests SET recovery=1 WHERE attempt>0')
            db.execute('UPDATE insight_requests SET base_slot=slot WHERE base_slot IS NULL')

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def _headroom(self, db, now):
        """Retry headroom after the scheduled remainder of the month is set aside."""
        used = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=?',
                          (now.strftime('%Y-%m'),)).fetchone()[0]
        projected = _projected_need(now)
        return used, projected, self.monthly_limit - used - self.reserved - projected

    def diagnostics(self, now):
        now = _aware(now)
        with self._db() as db:
            used, projected, headroom = self._headroom(db, now)
            last = db.execute('SELECT error, finished FROM insight_requests ORDER BY at DESC, id DESC LIMIT 1').fetchone()
            success = db.execute('SELECT MAX(received_at) FROM insight_cache').fetchone()[0]
            recovery_month, recovery_day = self._recovery_counts(db, now)
        return {'source': 'insightsentry', 'symbol': SYMBOL, 'budget_month': now.strftime('%Y-%m'),
                'budget_used': used + self.reserved, 'requests_recorded': used,
                'reserved_requests': self.reserved, 'budget_limit': self.monthly_limit,
                'projected_month_need': projected, 'retry_headroom': max(0, headroom),
                'slot_attempt_limit': SLOT_ATTEMPT_LIMIT, 'quote_refresh_limit': QUOTE_REFRESH_LIMIT,
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

    def _attempts(self, db, kind, slot):
        return db.execute('SELECT finished,error,attempt,retryable,retry_at,late FROM insight_requests '
                          'WHERE kind=? AND base_slot=? ORDER BY attempt', (kind, slot)).fetchall()

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
            elif status == 'headroom_reserved':
                error = 'The remaining monthly allowance is reserved for scheduled collection.'
            elif status == 'rate_limited':
                error = 'The local request rate limit has been reached.'
        retry_at, retry_reason = None, None
        if slot is not None:
            with self._db() as db:
                rows = self._attempts(db, kind, slot)
                recovery_month, recovery_day = self._recovery_counts(db, now)
                total_used, _, headroom = self._headroom(db, now)
            if rows:
                finished, failure, _, retryable, retry_stamp, late = rows[-1]
                successes = sum(1 for row in rows if row[0] and not row[1])
                pending = status != 'current' and (failure or kind == 'quote')
                if not finished:
                    retry_reason = 'Request outcome unknown; this claim cannot be retried'
                elif status == 'budget_exhausted' or (pending and total_used + self.reserved >= self.monthly_limit):
                    retry_reason = 'Total monthly allowance exhausted; no recovery request is permitted'
                elif failure and not retryable:
                    retry_reason = 'Invalid or unverified response; waiting for the next data slot'
                elif pending and len(rows) >= SLOT_ATTEMPT_LIMIT:
                    retry_reason = 'Slot attempt limit reached; waiting for the next data slot'
                elif pending and not failure and successes >= QUOTE_REFRESH_LIMIT:
                    retry_reason = 'Quote refresh limit reached; waiting for the next data slot'
                elif pending and headroom <= 0:
                    retry_reason = 'Monthly headroom is reserved for scheduled collection; waiting for the next data slot'
                elif failure and not late and (recovery_month >= RECOVERY_MONTH_LIMIT or recovery_day >= RECOVERY_DAY_LIMIT):
                    retry_reason = 'Recovery allowance exhausted; waiting for the next data slot'
                elif failure and retryable:
                    retry_at = datetime.fromtimestamp(retry_stamp, timezone.utc)
                    retry_reason = ('Publication pending; another attempt follows the backoff' if late else
                                    'Temporary failure; a bounded recovery attempt is available')
                    if next_at is None or retry_at < next_at:
                        next_at = retry_at
                elif pending and not failure:
                    source_at = self._cached('quote')['source_updated_at']
                    if source_at:
                        retry_at = max(now, _aware(source_at) + timedelta(seconds=90))
                        retry_reason = 'A bounded refresh is available after the quote expires'
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
        """Genuine-error recoveries only; late-publication retries are not counted."""
        month = db.execute('SELECT COUNT(*) FROM insight_requests WHERE month=? AND recovery=1',
                           (now.strftime('%Y-%m'),)).fetchone()[0]
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        day = db.execute('SELECT COUNT(*) FROM insight_requests WHERE at>=? AND recovery=1', (start,)).fetchone()[0]
        return month, day

    def _reserve(self, kind, slot, now, *, refresh_success=False):
        """Claim one request durably, or say why none is permitted right now.

        Every branch runs inside BEGIN IMMEDIATE so concurrent collectors in any
        process see the same ledger; an unfinished claim stays in flight forever.
        """
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = self._attempts(db, kind, slot)
            attempt, recovery = 0, 0
            if rows:
                finished, error, prior, retryable, retry_at, late = rows[-1]
                if not finished:
                    return None, 'in_flight'
                if not error and not refresh_success:
                    return None, 'cached'
                if error and not retryable:
                    return None, 'unavailable'
                if len(rows) >= SLOT_ATTEMPT_LIMIT:
                    return None, 'unavailable' if error else 'cached'
                if not error and sum(1 for row in rows if row[0] and not row[1]) >= QUOTE_REFRESH_LIMIT:
                    return None, 'cached'
                if error and now.timestamp() < retry_at:
                    return None, 'retry_wait'
                recovery = int(bool(error) and not late)
                if recovery:
                    month, day = self._recovery_counts(db, now)
                    if month >= RECOVERY_MONTH_LIMIT or day >= RECOVERY_DAY_LIMIT:
                        return None, 'recovery_budget_exhausted'
                attempt = prior + 1
            used, _, headroom = self._headroom(db, now)
            if used + self.reserved >= self.monthly_limit:
                return None, 'budget_exhausted'
            if attempt and headroom <= 0:
                return None, 'headroom_reserved'
            recent = db.execute('SELECT COUNT(*) FROM insight_requests WHERE at>?',
                                (now.timestamp() - 60,)).fetchone()[0]
            if recent >= 5:
                return None, 'rate_limited'
            claim_slot = slot if attempt == 0 else slot + ':recovery' + ('' if attempt == 1 else str(attempt))
            cursor = db.execute('INSERT INTO insight_requests (at,month,kind,slot,base_slot,attempt,recovery) '
                                'VALUES (?,?,?,?,?,?,?)',
                                (now.timestamp(), now.strftime('%Y-%m'), kind, claim_slot, slot, attempt, recovery))
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
            late = isinstance(exc, LatePublication)
            retryable = late or (isinstance(exc, ProbeError) and exc.retryable)
            failed_at = max(now, _aware(self._clock()))
            error = ('InsightSentry VIX observation is not published yet.' if late else
                     'InsightSentry connection is temporarily unavailable.' if retryable else
                     'InsightSentry did not return valid, current VIX data.')
            with self._db() as db:
                db.execute('UPDATE insight_requests SET finished=1,error=?,retryable=?,retry_at=?,late=? WHERE id=?',
                           (error, int(retryable), (failed_at + RETRY_BACKOFF).timestamp() if retryable else None,
                            int(late), request_id))
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
        # After-close catch-up uses the final session slot, with the same
        # per-slot attempt cap; repeated polling cannot create more claims.
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
        # Failed metadata retries after the backoff within its hourly slot, then
        # a new slot next hour; successful metadata stays cached for a full day.
        return self._request('metadata', now.strftime('%Y-%m-%dT%H'), now, PREFIX + '/info', None,
                             now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

    def quote(self, now):
        now = _aware(now)
        cached = self._cached('quote')
        if cached['source_updated_at']:
            source_at = _aware(cached['source_updated_at'])
            if _quote_fresh(now, source_at):
                return self._result('quote', now, 'cached', source_at + timedelta(seconds=90))
        # A stale quote may be refreshed up to QUOTE_REFRESH_LIMIT successes per
        # candle slot; merely polling cannot extend the original quote's timestamp.
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
        fresh = _quote_fresh(now, source_at)
        result['status'] = 'cached' if fresh else 'stale'
        result['error'] = None
        if fresh:
            result.update(retry_at=None, retry_reason=None)
        next_at = (source_at + timedelta(seconds=90) if fresh else
                   datetime.fromtimestamp((int(now.timestamp() // 900) + 1) * 900, timezone.utc))
        result['next_refresh_at'] = result.get('retry_at') or next_at.isoformat()
        return result
