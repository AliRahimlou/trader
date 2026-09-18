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


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


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
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS frames (hash TEXT PRIMARY KEY, body BLOB NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS observations '
                             '(id INTEGER PRIMARY KEY, captured_at TEXT NOT NULL, body TEXT NOT NULL);')
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=.1)

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
        if len(body) > 1024 * 1024:
            raise ValueError('Observation exceeds archive limit')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            # Conservative allowance includes metadata/index overhead. Never silently discard history.
            if size + len(body) * 2 + sum(len(blob) + 8192 for blob in blobs.values()) > self.max_bytes:
                raise ValueError('Input archive is full; preserve/export history before increasing storage')
            for key, blob in blobs.items():
                db.execute('INSERT OR IGNORE INTO frames VALUES(?,?)', (key, blob))
            cursor = db.execute('INSERT INTO observations(captured_at,body) VALUES(?,?)', (payload['captured_at'], body.decode()))
            identity = cursor.lastrowid
        return {'id': identity, 'captured_at': payload['captured_at'], 'engine_hash': self.engine_hash,
                'version': ARCHIVE_VERSION, 'status': 'recording', 'private': True}

    def read(self, identity):
        """Reconstruct exactly the archived frames/receipt times, not current provider history."""
        with self.connect() as db:
            row = db.execute('SELECT body FROM observations WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('Observation not found')
            payload = json.loads(row[0])
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
