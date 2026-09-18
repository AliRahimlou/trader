"""One quota-accounted native VIX research capture; no live-feed authority.

The server's existing InsightCache ledger owns the allowance. A stable monthly
claim is consumed before the one GET, including failures and unknown outcomes.
Nothing here changes the production fifteen-minute cache or trading settings.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile

from .feeds import ET
from .history_health import frame_gaps
from .insight_cache import PREFIX, SYMBOL, _number, _source_time, _validate
from .insight_probe import ProbeError, _get
from .insight_validation import _aware, _calendar
from .models import Bar

SCHEMA = 'pivot-native-vix-five-minute-capture-v1'
EXPERIMENT = 'native-vix5-v1'
KIND = 'research_vix5'
MAX_ARTIFACT_BYTES = 8_000_000
PARAMS = {'bar_type': 'minute', 'bar_interval': 5, 'dp': 3000,
          'extended': 'false', 'abbr': 'false'}
FAILURE = 'Native VIX research capture failed; this monthly request will not be retried.'


def _json(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(data):
    return sha256(_json(data).encode()).hexdigest()


def artifact_path(now, output_dir):
    return Path(output_dir) / (EXPERIMENT + '-' + _aware(now).strftime('%Y-%m') + '.json')


def _claim(cache, now):
    with cache._db() as db:
        return db.execute('SELECT id,finished,error FROM insight_requests WHERE kind=? AND slot=?',
                          (KIND, EXPERIMENT + ':' + now.strftime('%Y-%m'))).fetchone()


def _base(now, state, detail):
    return {'schema': 'native-vix-research-status-v1', 'status': state, 'detail': detail,
            'experiment': EXPERIMENT, 'month': now.strftime('%Y-%m'),
            'research_only': True, 'live_entry_ready': False, 'source': 'insightsentry',
            'source_symbol': SYMBOL, 'native_resolution_minutes': 5}


def _series(raw, received, sessions):
    """Validate authentic native bars; keep only originally closed RTH candles."""
    if (not isinstance(raw, dict) or raw.get('error') or raw.get('code') != SYMBOL
            or raw.get('bar_type') != '5m' or not isinstance(raw.get('series'), list)
            or not 1 <= len(raw['series']) <= 3000):
        raise ValueError('Actual native five-minute VIX history required')
    updated = _source_time(raw['last_update'], milliseconds=True)
    if updated > received:
        raise ValueError('Future provider timestamp')
    bars, previous = [], None
    watermark = min(updated, received)
    for row in raw['series']:
        start = _source_time(row['time'])
        opening, high, low, close = [_number(row[key]) for key in ('open', 'high', 'low', 'close')]
        if (not 0 < low <= min(opening, close) <= max(opening, close) <= high
                or start.second or start.microsecond or start.minute % 5 or start > updated
                or (previous is not None and start <= previous)):
            raise ValueError('Invalid native VIX candle')
        # Index volume is not required, but supplied values cannot be malformed.
        volume = _number(row.get('volume', 0))
        if volume < 0:
            raise ValueError('Invalid native VIX volume')
        previous = start
        end = start + timedelta(minutes=5)
        session = sessions.get(start.astimezone(ET).date().isoformat())
        if session and session['open'] <= start < end <= session['close'] and end <= watermark:
            bars.append(Bar(end, 5, opening, high, low, close, volume))
    if _source_time(raw['bar_end']) not in (previous + timedelta(minutes=5),
                                          previous + timedelta(minutes=5, seconds=-1)):
        raise ValueError('Invalid native VIX interval end')
    return bars, updated


def _encode(bar):
    return {'end': bar.end.isoformat(), 'minutes': 5, 'open': bar.open, 'high': bar.high,
            'low': bar.low, 'close': bar.close, 'volume': bar.volume}


def _sessions(raw):
    parsed = _calendar(raw)
    if not parsed:
        raise ValueError('Verified exchange calendar required')
    return {day.isoformat(): {'open': opening, 'close': closing}
            for day, (opening, closing) in parsed.items()}


def _coverage(bars, sessions, received):
    first = (received.astimezone(ET).date() - timedelta(days=14)).isoformat()
    today = received.astimezone(ET).date().isoformat()
    scoped = {day: value for day, value in sessions.items() if first <= day <= today}
    gaps = frame_gaps(bars, scoped, 5, received)
    rows = []
    for day, session in sorted(sessions.items()):
        if session['close'] > received - timedelta(seconds=90):
            continue
        missing = frame_gaps(bars, {day: session}, 5, received)['missing_count']
        rows.append({'day': day, 'missing_count': missing, 'complete': missing == 0})
    # An undersized supplied calendar cannot assert full warmup coverage.
    calendar_covers_window = bool(sessions and min(sessions) <= first and max(sessions) >= today)
    return {'required_from': first, 'through': today, 'calendar_covers_window': calendar_covers_window,
            'missing_count': gaps['missing_count'], 'first_missing_at': gaps['first_missing_at'],
            'complete': bool(scoped and bars and calendar_covers_window and not gaps['missing_count']),
            'complete_sessions': [row['day'] for row in rows if row['complete']], 'sessions': rows}


def _immutable_write(path, payload):
    directory = path.parent
    if directory.is_symlink():
        raise ValueError('Research output cannot be a symbolic link')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    fd, temporary = tempfile.mkstemp(prefix='.native-vix-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(_json(payload) + '\n')
            output.flush()
            os.fsync(output.fileno())
        # Never overwrite an existing immutable capture, even after a crash.
        os.link(temporary, path)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.unlink(temporary)


def read_artifact(now, output_dir):
    """Read a bounded private artifact and verify source identity and normalization."""
    now = _aware(now)
    path = artifact_path(now, output_dir)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        metadata = os.fstat(source.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_ARTIFACT_BYTES
                or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077):
            raise ValueError('Research artifact must be bounded and private')
        raw = source.read(MAX_ARTIFACT_BYTES + 1)
    artifact = validate_artifact(json.loads(raw), now)
    if _aware(artifact['requested_at']).strftime('%Y-%m') != now.strftime('%Y-%m'):
        raise ValueError('Artifact does not match its monthly filename')
    return artifact


def validate_artifact(artifact, now):
    """Pure integrity check for authenticated exports and disconnected importers."""
    now = _aware(now)
    received = _aware(artifact['received_at'])
    requested = _aware(artifact['requested_at'])
    meta_received = _aware(artifact['metadata_received_at'])
    if (artifact.get('schema') != SCHEMA or artifact.get('experiment') != EXPERIMENT
            or artifact.get('research_only') is not True or artifact.get('live_entry_ready') is not False
            or not requested <= received <= now
            or not timedelta(0) <= received - meta_received < timedelta(hours=24)):
        raise ValueError('Invalid research artifact identity or receipt')
    _validate('metadata', artifact['metadata'], meta_received)
    if artifact['metadata_sha256'] != _digest(artifact['metadata']):
        raise ValueError('Metadata digest mismatch')
    sessions = _sessions(artifact['sessions'])
    bars, updated = _series(artifact['raw_response'], received, sessions)
    expected = [_encode(bar) for bar in bars]
    if (artifact['raw_response_sha256'] != _digest(artifact['raw_response'])
            or artifact['source_updated_at'] != updated.isoformat()
            or artifact['bars'] != expected or artifact['normalized_sha256'] != _digest(expected)
            or artifact['coverage'] != _coverage(bars, sessions, received)
            or artifact['provenance'] != _provenance() or artifact['request'] != _request_description()):
        raise ValueError('Research artifact provenance or content mismatch')
    return artifact


def _provenance():
    return {'source': 'insightsentry', 'source_symbol': SYMBOL, 'native_resolution_minutes': 5,
            'zero_delay_metadata_verified_at_receipt': True, 'point_in_time_arrival_verified': False}


def _request_description():
    return {'symbol': SYMBOL, **PARAMS}


def snapshot(cache, now, output_dir):
    """Read-only restart status. A claimed but unknown request is never retried."""
    now = _aware(now)
    claim = _claim(cache, now)
    if not claim:
        return _base(now, 'not_captured', 'Native five-minute VIX research history has not been captured.')
    if not claim[1]:
        return _base(now, 'claimed', 'One request was claimed; a running or unknown outcome cannot be retried.')
    if claim[2]:
        return _base(now, 'unavailable', FAILURE)
    try:
        artifact = read_artifact(now, output_dir)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return _base(now, 'artifact_unavailable', 'The consumed request has no validated local research artifact.')
    complete = artifact['coverage']['complete']
    return {**_base(now, 'captured' if complete else 'incomplete',
                    'Native history saved for isolated research; historical arrivals are not verified.' if complete else
                    'Authentic native history was saved, but required calendar coverage is incomplete.'),
            'request_count': 1, 'received_at': artifact['received_at'],
            'source_updated_at': artifact['source_updated_at'], 'bar_count': len(artifact['bars']),
            'raw_response_sha256': artifact['raw_response_sha256'],
            'coverage': artifact['coverage']}


def capture(cache, sessions, now, output_dir, *, clock=None):
    """Consume at most one existing-budget request per UTC month; never retry it."""
    now = _aware(now)
    if _claim(cache, now):
        return snapshot(cache, now, output_dir)
    clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        calendar = _sessions(sessions)
        metadata = cache._cached('metadata')
        meta_received = _aware(metadata['received_at'])
        if not timedelta(0) <= now - meta_received < timedelta(hours=24):
            raise ValueError('Fresh verified metadata required')
        _validate('metadata', metadata['payload'], meta_received)
        info = {key: metadata['payload'][key] for key in ('code', 'type', 'delay_seconds')}
        if cache._key in _json(metadata['payload']):
            raise ValueError('Reflected credential')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return _base(now, 'metadata_unavailable', 'A verified calendar and fresh cached actual-index metadata are required; no request was made.')
    request_id, status = cache._reserve(KIND, EXPERIMENT + ':' + now.strftime('%Y-%m'), now)
    if request_id is None:
        return (snapshot(cache, now, output_dir) if _claim(cache, now) else
                _base(now, status, 'Research waits for the existing shared request allowance; no request was made.'))
    try:
        payload = _get(cache.session, cache._key, PREFIX + '/series', dict(PARAMS))
        received = _aware(clock())
        if received < now or not timedelta(0) <= received - meta_received < timedelta(hours=24):
            raise ValueError('Collection receipt or metadata expired')
        if cache._key in _json(payload):
            raise ValueError('Reflected credential')
        bars, updated = _series(payload, received, calendar)
        encoded = [_encode(bar) for bar in bars]
        artifact = {'schema': SCHEMA, 'experiment': EXPERIMENT, 'research_only': True,
                    'live_entry_ready': False, 'requested_at': now.isoformat(), 'received_at': received.isoformat(),
                    'source_updated_at': updated.isoformat(), 'request': _request_description(),
                    'metadata': info, 'metadata_received_at': meta_received.isoformat(),
                    'metadata_sha256': _digest(info), 'raw_response': payload, 'raw_response_sha256': _digest(payload),
                    'sessions': {day: {key: value.isoformat() for key, value in session.items()}
                                 for day, session in calendar.items()},
                    'bars': encoded, 'normalized_sha256': _digest(encoded), 'provenance': _provenance(),
                    'coverage': _coverage(bars, calendar, received)}
        _immutable_write(artifact_path(now, output_dir), artifact)
    except (ProbeError, ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
        with cache._db() as db:
            db.execute('UPDATE insight_requests SET finished=1,error=?,retryable=0,retry_at=NULL WHERE id=?',
                       (FAILURE, request_id))
        return _base(now, 'unavailable', FAILURE)
    with cache._db() as db:
        db.execute('UPDATE insight_requests SET finished=1,error=NULL,retryable=0,retry_at=NULL WHERE id=?', (request_id,))
    return snapshot(cache, received, output_dir)
