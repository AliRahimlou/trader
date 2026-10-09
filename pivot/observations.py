"""Private immutable analysis inputs. This archive has no trading authority.

Separate storage keeps compression/history queries out of the execution ledger.
Frame blobs are content-addressed; receipt times and later provider corrections
each belong to their original observation and never overwrite earlier evidence.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
from pathlib import Path
import sqlite3
import zlib

from .models import Bar, Market, MAG7, timestamp
from .diagnostics import SOURCES
from .sizing import decimal

ARCHIVE_VERSION = 'analysis-inputs-v1'
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_OBSERVATION_BYTES = 1024 * 1024
LEGACY_COMPRESSION_BATCH = 64


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def observation_bytes(body):
    """Read legacy JSON or bounded compressed JSON without changing its bytes."""
    if isinstance(body, str):
        raw = body.encode()
    elif isinstance(body, bytes):
        try:
            inflater = zlib.decompressobj()
            raw = inflater.decompress(body, MAX_OBSERVATION_BYTES + 1)
            if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                raise ValueError('Invalid compressed observation')
        except zlib.error:
            raise ValueError('Invalid compressed observation') from None
    else:
        raise ValueError('Invalid observation storage')
    if len(raw) > MAX_OBSERVATION_BYTES:
        raise ValueError('Observation exceeds archive limit')
    return raw


def engine_digest():
    root = Path(__file__).parent
    digest = sha256()
    for name in ('models.py', 'strategy.py', 'data_health.py', 'market_context.py', 'policy.py', 'diagnostics.py'):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


class ObservationArchive:
    def __init__(self, path, *, max_bytes=MAX_ARCHIVE_BYTES):
        self.path, self.max_bytes = str(path), max_bytes
        self.engine_hash = engine_digest()
        self._compression_cursor = 0
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS frames (hash TEXT PRIMARY KEY, body BLOB NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS observations '
                             '(id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, body TEXT NOT NULL);')
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=.1)

    @staticmethod
    def _occupied_bytes(db):
        # Lossless compression releases overflow pages for later inserts. SQLite
        # retains those free pages in the file, so file length is not usage.
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        free = db.execute('PRAGMA freelist_count').fetchone()[0]
        return (pages - free) * db.execute('PRAGMA page_size').fetchone()[0]

    def _compress_legacy(self, db):
        """Bounded, lossless maintenance; never drop a row or its receipt time."""
        rows = db.execute('SELECT id,body FROM observations WHERE id>? AND typeof(body)=\'text\' '
                          'ORDER BY id LIMIT ?', (self._compression_cursor, LEGACY_COMPRESSION_BATCH)).fetchall()
        if not rows and self._compression_cursor:
            self._compression_cursor = 0
            rows = db.execute('SELECT id,body FROM observations WHERE typeof(body)=\'text\' '
                              'ORDER BY id LIMIT ?', (LEGACY_COMPRESSION_BATCH,)).fetchall()
        for identity, body in rows:
            raw = observation_bytes(body)
            compressed = zlib.compress(raw)
            if len(compressed) < len(raw):
                db.execute('UPDATE observations SET body=? WHERE id=?', (compressed, identity))
        # Advance only after the caller commits the maintenance transaction.
        return rows[-1][0] if rows else self._compression_cursor

    def record(self, markets, vix, at, *, trace, sessions=None, settings=None, revision=''):
        blobs = {}
        def market_value(market):
            if market.symbol not in ('QQQ', *MAG7, 'I:VIX'):
                raise ValueError('Unsupported archive instrument')
            if market.previous_session is not None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', market.previous_session):
                raise ValueError('Invalid previous session')
            frames = {}
            for minutes, bars in sorted(market.bars.items()):
                raw = encoded([{'end': b.end.isoformat(), 'minutes': b.minutes,
                                'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close,
                                'volume': b.volume, 'vwap': b.vwap} for b in bars])
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Input frame exceeds archive limit')
                key = sha256(raw).hexdigest()
                blobs[key] = zlib.compress(raw)
                frames[str(minutes)] = key
            return {'symbol': market.symbol, 'source': market.source if market.source in SOURCES else 'unrecognized',
                    'realtime': market.realtime is True, 'observed_at': timestamp(market.observed_at).isoformat(),
                    'previous_session': market.previous_session,
                    'valid_until': timestamp(market.valid_until).isoformat() if market.valid_until else None,
                    'frames': frames}
        settings = settings or {}
        target = settings.get('target_dollars')
        target = str(decimal(target)) if target is not None else None
        if target is not None and not 1 <= decimal(target) <= decimal('1000000000000'):
            raise ValueError('Invalid archived target')
        # The decision builder owns nested evidence allowlists; admit only its
        # market/strategy fields here, never arbitrary account/control payloads.
        decision_keys = ('version', 'captured_at', 'checkpoint_at', 'setup', 'checks', 'strategies',
                         'candidate_diagnostics', 'entry_candidates', 'signal_qualifies', 'first_blocker',
                         'market_context', 'nasdaq', 'leaders', 'leader_counts', 'vix', 'data_health')
        decision = {key: trace[key] for key in decision_keys if key in trace}
        payload = {'version': ARCHIVE_VERSION, 'engine_hash': self.engine_hash,
                   'captured_at': timestamp(at).isoformat(), 'revision': revision if isinstance(revision, str) and len(revision) == 40 and all(c in '0123456789abcdef' for c in revision) else '',
                   'markets': {s: market_value(m) for s, m in markets.items() if s in ('QQQ', *MAG7)},
                   'vix': market_value(vix) if vix is not None else None,
                   'sessions': {day: {key: timestamp(row[key]).isoformat() for key in ('open', 'close')}
                                for day, row in (sessions or {}).items()},
                   'settings': {'sizing_mode':'target' if settings.get('sizing_mode') == 'target' else None,
                                'target_dollars':target},
                   'decision': decision}
        body = encoded(payload)
        if len(body) > MAX_OBSERVATION_BYTES:
            raise ValueError('Observation exceeds archive limit')
        compressed_body = zlib.compress(body)
        with self.connect() as db:
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            # Also bound actual file growth, including SQLite page overhead.
            # Existing allocated pages can be reused after compression without
            # VACUUM, a second full-size database, or deleting old evidence.
            db.execute(f'PRAGMA max_page_count={max(1, self.max_bytes // page_size)}')
            db.execute('BEGIN IMMEDIATE')
            def required_bytes():
                new_blobs = [blob for key, blob in blobs.items()
                             if db.execute('SELECT 1 FROM frames WHERE hash=?', (key,)).fetchone() is None]
                return len(compressed_body) * 2 + page_size * 2 + sum(
                    len(blob) + page_size * 2 for blob in new_blobs)

            if self._occupied_bytes(db) + required_bytes() > self.max_bytes:
                cursor = self._compress_legacy(db)
                # Preserve bounded compression progress even when another batch
                # is needed before a new observation can fit. The append below
                # remains its own atomic transaction with all referenced frames.
                db.commit()
                self._compression_cursor = cursor
                db.execute('BEGIN IMMEDIATE')
            if self._occupied_bytes(db) + required_bytes() > self.max_bytes:
                raise ValueError('Input archive is full; preserve/export history before increasing storage')
            for key, blob in blobs.items():
                db.execute('INSERT OR IGNORE INTO frames VALUES(?,?)', (key, blob))
            cursor = db.execute('INSERT INTO observations(captured_at,body) VALUES(?,?)', (payload['captured_at'], compressed_body))
            identity = cursor.lastrowid
        return {'id': identity, 'captured_at': payload['captured_at'], 'engine_hash': self.engine_hash,
                'version': ARCHIVE_VERSION, 'status': 'recording', 'private': True}

    def read(self, identity):
        """Reconstruct exactly the archived frames/receipt times, not current provider history."""
        with self.connect() as db:
            row = db.execute('SELECT body FROM observations WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('Observation not found')
            payload = json.loads(observation_bytes(row[0]))
            def market_value(value):
                if value is None:
                    return None
                bars = {}
                for minutes, key in value['frames'].items():
                    frame = db.execute('SELECT body FROM frames WHERE hash=?', (key,)).fetchone()
                    raw = zlib.decompress(frame[0])
                    if sha256(raw).hexdigest() != key:
                        raise ValueError('Archived frame integrity check failed')
                    bars[int(minutes)] = [Bar(**{**bar, 'end': timestamp(bar['end'])}) for bar in json.loads(raw)]
                return Market(value['symbol'], bars, value['source'], value['realtime'],
                              timestamp(value['observed_at']), value['previous_session'],
                              timestamp(value['valid_until']) if value['valid_until'] else None)
            markets = {symbol: market_value(value) for symbol, value in payload['markets'].items()}
            vix = market_value(payload['vix'])
        return payload, markets, vix

    def verify_replay(self, identity):
        from .strategy import analyze
        from .diagnostics import build_decision_trace
        payload, markets, vix = self.read(identity)
        if payload['engine_hash'] != engine_digest():
            raise ValueError('Archive engine differs; replay requires the recorded engine version')
        setup = analyze(markets.get('QQQ'), markets, vix, timestamp(payload['captured_at'])) if markets.get('QQQ') else {}
        reproduced = build_decision_trace(setup, markets, vix, timestamp(payload['captured_at']))
        fields = ('setup', 'checks', 'strategies', 'entry_candidates', 'candidate_diagnostics',
                  'signal_qualifies', 'first_blocker', 'market_context', 'nasdaq', 'leaders', 'leader_counts', 'vix')
        matches = all(reproduced.get(key) == payload['decision'].get(key) for key in fields)
        return {'observation_id': identity, 'matches': matches, 'engine_hash': payload['engine_hash'],
                'historical_only': True, 'scope': 'both strategy methods, all candidate checks and signal evidence; no broker replay'}
