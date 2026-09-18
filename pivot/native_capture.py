"""Bounded provider evidence collection, entirely separate from order decisions.

One native QQQ request and one quota-reserved native VIX request per monthly
experiment slot. A claim with an unknown result stays consumed after restart.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import stat
from pathlib import Path
from threading import Lock, Thread

from .feeds import ET, FeedError, ReadOnlyFeeds
from .models import Bar, timestamp
from .insight_validation import _calendar

SCHEMA = 'pivot-native-capture-v1'
QQQ_SCHEMA = 'pivot-native-qqq-five-minute-capture-v1'
QQQ_PROVENANCE = {'source': 'alpaca_iex', 'source_symbol': 'QQQ', 'adjustment': 'split',
                  'native_resolution_minutes': 5}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def write_once(path, value):
    # A partial write is deliberately not replaced by another provider request.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
        handle.write(encoded(value))
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_qqq(path, now):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'r') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 2_000_000
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError('Private regular native stock artifact required')
        result = json.load(handle)
    if (result.get('schema') != QQQ_SCHEMA or result.get('provenance') != QQQ_PROVENANCE
            or result.get('research_only') is not True or result.get('status') != 'captured'):
        raise ValueError('Native stock identity or purpose differs')
    received, requested = timestamp(result['received_at']), timestamp(result['requested_at'])
    if not requested <= received <= now:
        raise ValueError('Invalid native stock receipt')
    calendar = _calendar(result['sessions'])
    if not calendar:
        raise ValueError('Native stock calendar is missing')
    rows = result['bars']
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 10000
            or result.get('normalized_sha256') != sha256(encoded(rows)).hexdigest()):
        raise ValueError('Native stock evidence failed integrity verification')
    previous = None
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'end','minutes','open','high','low','close','volume','vwap'}:
            raise ValueError('Invalid native stock fields')
        bar = Bar(**{**row, 'end': timestamp(row['end'])})
        session = calendar.get(bar.end.astimezone(ET).date())
        start = bar.end - timedelta(minutes=5)
        if (bar.minutes != 5 or not session or not session[0] <= start < bar.end <= session[1]
                or (start-session[0]).total_seconds() % 300 or bar.end > requested
                or (previous is not None and bar.end <= previous)):
            raise ValueError('Invalid native stock timing')
        previous = bar.end
    return {key: result[key] for key in ('schema','research_only','status','requested_at','received_at',
                                        'provenance','bars','normalized_sha256')} | {
        'sessions': {day.isoformat(): {'open': row[0].isoformat(), 'close': row[1].isoformat()}
                     for day, row in calendar.items()}}


class OneRequestFeeds(ReadOnlyFeeds):
    """A private read-only session that cannot paginate into extra requests."""
    def __init__(self, source):
        super().__init__({'APCA_API_KEY_ID': source.alpaca_headers['APCA-API-KEY-ID'],
                          'APCA_API_SECRET_KEY': source.alpaca_headers['APCA-API-SECRET-KEY'],
                          'APCA_API_FEED': source.feed})
        self.requests = 0

    def get(self, provider, path, params=None):
        if provider != 'stocks' or path != '/v2/stocks/bars' or self.requests:
            raise FeedError('The one-request native stock capture limit was reached')
        self.requests += 1
        return super().get(provider, path, params)


class NativeCapture:
    def __init__(self, feeds, directory, *, clock=None, reader_factory=OneRequestFeeds):
        self.feeds, self.directory = feeds, Path(directory)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.reader_factory = reader_factory
        self.lock, self.thread = Lock(), None
        self.summary = {'status': 'waiting', 'research_only': True, 'live_entry_ready': False,
                        'detail': 'Waiting for current production data before collecting validation history.'}
        self.completed_month = None
        if self.directory.is_symlink():
            raise ValueError('Native capture directory cannot be a symbolic link')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)

    def status(self):
        with self.lock:
            return dict(self.summary)

    def start(self, sessions, now):
        with self.lock:
            if (self.thread and self.thread.is_alive()) or self.completed_month == now.strftime('%Y-%m'):
                return
            self.summary = {**self.summary, 'status': 'collecting',
                            'detail': 'Collecting native five-minute history for separate strategy validation.'}
            self.thread = Thread(target=self._run, args=(dict(sessions), now),
                                 name='pivot-native-history', daemon=True)
            self.thread.start()

    def _qqq(self, sessions, now):
        month = now.strftime('%Y-%m')
        artifact = self.directory / f'qqq5-v1-{month}.json'
        claim = self.directory / f'qqq5-v1-{month}.claim'
        if artifact.exists():
            return read_qqq(artifact, self.clock())
        try:
            write_once(claim, {'started_at': now.isoformat(), 'research_only': True})
        except FileExistsError:
            raise ValueError('Native stock request outcome is unknown; its claim cannot be repeated') from None
        if self.feeds.feed != 'iex':
            raise ValueError('Native comparison requires the existing free IEX feed')
        reader = self.reader_factory(self.feeds)
        try:
            bars = reader.stock_bars(('QQQ',), 5, min(row['open'] for row in sessions.values()), now, sessions)['QQQ']
        finally:
            reader.session.close()
        received = self.clock()
        if received < now:
            raise ValueError('Capture clock moved backwards')
        rows = [{**asdict(bar), 'end': bar.end.isoformat()} for bar in bars]
        result = {'schema': QQQ_SCHEMA, 'research_only': True, 'status': 'captured',
                  'requested_at': now.isoformat(), 'received_at': received.isoformat(),
                  'provenance': dict(QQQ_PROVENANCE),
                  'bars': rows, 'normalized_sha256': sha256(encoded(rows)).hexdigest(),
                  'sessions': {day: {key: timestamp(row[key]).isoformat() for key in ('open','close')}
                               for day, row in sessions.items()}}
        write_once(artifact, result)
        return result

    def _run(self, sessions, now):
        from .native_history import capture
        stage = 'calendar'
        try:
            start_day = (now.astimezone(ET).date() - timedelta(days=26)).isoformat()
            calendar = {day: row for day, row in sessions.items() if start_day <= day <= now.astimezone(ET).date().isoformat()}
            if not calendar:
                raise ValueError('Verified calendar is unavailable')
            stage = 'vix_history'
            vix = capture(self.feeds.insight_cache(), calendar, now, self.directory)
            stage = 'qqq_history'
            qqq = self._qqq(calendar, now)
            result = {'status': 'captured' if vix.get('status') == 'captured' else 'incomplete',
                      'research_only': True, 'live_entry_ready': False,
                      'qqq_candles': len(qqq['bars']), 'vix': vix,
                      'qqq_received_at': qqq['received_at'],
                      'detail': ('Native history is saved for offline checks; this does not enable a faster live strategy.'
                                 if vix.get('status') == 'captured' else
                                 'Native history is incomplete. ' + vix.get('detail', 'VIX capture is unavailable.'))}
        except Exception:
            result = {'status': 'unavailable', 'research_only': True, 'live_entry_ready': False,
                      'failure_stage': stage,
                      'detail': 'Native validation history could not be completed. Production data checks remain separate.'}
            try:
                write_once(self.directory / f'capture-failure-v1-{now.strftime("%Y-%m")}.json',
                           {**result, 'observed_at': self.clock().isoformat()})
            except OSError:
                pass
        with self.lock:
            self.summary = result
            # A failed/unknown provider claim cannot trigger automatic retries.
            # Metadata or global-rate waits may be checked on a later normal cycle.
            if (result.get('status') in ('captured', 'unavailable') or
                    result.get('vix', {}).get('status') in ('captured','incomplete','claimed','unavailable','artifact_unavailable')):
                self.completed_month = now.strftime('%Y-%m')

    def export(self, now=None):
        from .native_history import read_artifact
        now = now or self.clock()
        qpath = self.directory / f'qqq5-v1-{now.strftime("%Y-%m")}.json'
        if not qpath.is_file() or qpath.stat().st_size > 2_000_000:
            raise ValueError('Native stock history is unavailable')
        qqq = read_qqq(qpath, now)
        vix = read_artifact(now, self.directory)
        received = max(timestamp(qqq['received_at']), timestamp(vix['received_at']))
        return {'schema': SCHEMA, 'captured_at': received.isoformat(), 'exported_at': self.clock().isoformat(),
                'research_only': True, 'live_entry_ready': False,
                'sessions': qqq['sessions'], 'qqq': qqq, 'vix': vix}
