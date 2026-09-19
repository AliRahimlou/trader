"""Independent Bitcoin observations. No account, permission, or order interface."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat
from threading import Lock, Thread
from zoneinfo import ZoneInfo

import requests

from .feeds import FeedError
from .models import Bar, Market, timestamp
from .range_reversal import analyze, RULE_VERSION
from .crypto_markets import SYMBOLS

ET = ZoneInfo('America/New_York')
SOURCE = 'alpaca_crypto_us'
PROVENANCE = {'source': SOURCE, 'symbol': 'BTC/USD', 'timeframe_minutes': 5, 'native': True}
ARCHIVE_LIMIT = 64 * 1024 * 1024


class BitcoinBars:
    def __init__(self, headers, session=None, *, symbol='BTC/USD'):
        if symbol not in SYMBOLS:
            raise ValueError('Unsupported crypto data instrument')
        self.symbol = symbol
        self.headers = dict(headers)
        self.session = session or requests.Session()

    def read(self, now, *, clock):
        now = timestamp(now).astimezone(timezone.utc)
        start = now.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        try:
            response = self.session.get('https://data.alpaca.markets/v1beta3/crypto/us/bars',
                headers=self.headers, params={'symbols': self.symbol, 'timeframe': '5Min',
                    'start': start.isoformat(), 'end': now.isoformat(), 'sort': 'asc', 'limit': 1000},
                timeout=(3, 10), allow_redirects=False)
        except requests.RequestException:
            raise FeedError(self.symbol + ' candle connection unavailable') from None
        if response.status_code != 200:
            raise FeedError(self.symbol + ' candles could not be retrieved from Alpaca')
        try:
            raw = response.json()
            if (not isinstance(raw, dict) or raw.get('next_page_token')
                    or not isinstance(raw.get('bars'), dict) or set(raw['bars']) != {self.symbol}
                    or not isinstance(raw['bars'][self.symbol], list)):
                raise ValueError()
            bars, previous = [], None
            for row in raw['bars'][self.symbol]:
                at = timestamp(row['t']).astimezone(timezone.utc)
                if not start <= at <= now or (at-start).total_seconds() % 300:
                    raise ValueError()
                if previous is not None and at <= previous:
                    raise ValueError()
                previous = at
                values = [row[k] for k in ('o', 'h', 'l', 'c', 'v')]
                if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
                    raise ValueError()
                bar = Bar(at + timedelta(minutes=5), 5, *values)
                if bar.end <= now:
                    bars.append(bar)
            receipt = timestamp(clock()).astimezone(timezone.utc)
            if receipt < now or (receipt-now).total_seconds() > 90:
                raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError):
            raise FeedError(self.symbol + ' candles failed identity, ordering or timestamp validation') from None
        return Market(self.symbol, {5: bars}, SOURCE, True, receipt)


class RangeWatch:
    """A separate data-only loop; its failure cannot block Socrates execution."""
    def __init__(self, reader, archive_path, *, clock=None):
        self.reader, self.archive_path = reader, Path(archive_path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = Lock()
        self.thread = None
        self.market = None
        self.error = None
        self.last_at = None
        self.archive_status = 'Waiting for observations'
        self.symbol = getattr(reader, 'symbol', 'BTC/USD')
        self.provenance = {**PROVENANCE, 'symbol': self.symbol}

    def start(self, stop_event):
        if self.thread is not None:
            raise RuntimeError('Range observation worker already started')
        self.thread = Thread(target=self._loop, args=(stop_event,), name='pivot-range-' + self.symbol.split('/')[0].lower(), daemon=True)
        self.thread.start()

    def _loop(self, stop_event):
        while not stop_event.is_set():
            self.refresh()
            # Several bounded attempts within the 90-second entry window allow
            # for provider publication lag without replaying an expired signal.
            stop_event.wait(15)

    def refresh(self):
        try:
            market = self.reader.read(self.clock(), clock=self.clock)
            at = self.clock()
            result = analyze(market, at, provenance=self.provenance)
            with self.lock:
                self.market, self.error, self.last_at = market, None, at.isoformat()
            try:
                self._record(market, result)
                status = 'Recording observations'
            except Exception:
                status = 'Observation archive needs attention'
            with self.lock:
                self.archive_status = status
        except Exception:
            with self.lock:
                self.error = self.symbol + ' data could not be refreshed. Earlier observations cannot authorize a trade.'

    def _record(self, market, result):
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_WRONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.archive_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('Archive must be a regular private file')
            if info.st_size >= ARCHIVE_LIMIT:
                raise ValueError('Observation archive reached its size limit')
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        payload = {'rule_version':RULE_VERSION, 'provenance': self.provenance, 'bars': [asdict(bar) for bar in market.bars[5]]}
        encoded = json.dumps(payload, default=lambda value: value.isoformat(), sort_keys=True)
        digest = sha256(encoded.encode()).hexdigest()
        with sqlite3.connect(self.archive_path, timeout=2) as db:
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
            pages = ARCHIVE_LIMIT // page_size
            if pages < 1 or db.execute('PRAGMA page_count').fetchone()[0] > pages:
                raise ValueError('Observation archive reached its size limit')
            db.execute(f'PRAGMA max_page_count={pages}')
            db.execute('CREATE TABLE IF NOT EXISTS observations (digest TEXT PRIMARY KEY, received_at TEXT, inputs TEXT, analysis TEXT)')
            # Same closed candles are not another event. Provider corrections
            # change the digest and preserve a separate observation.
            db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?)',
                (digest, market.observed_at.isoformat(), encoded, json.dumps(result, sort_keys=True)))

    def snapshot(self):
        with self.lock:
            market, error, last_at, archive = deepcopy(self.market), self.error, self.last_at, self.archive_status
        now = self.clock()
        result = analyze(None if error else market, now, provenance=self.provenance)
        if error:
            result['detail'] = error
        result.update(data_source='Alpaca ' + self.symbol + ' · native five-minute candles',
                      last_refresh_at=last_at, archive_status=archive,
                      execution_note='Execution uses this strategy’s saved permission, purchase amount and current broker checks.')
        return result
