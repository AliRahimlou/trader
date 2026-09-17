"""Immutable, secret-free market datasets; historical availability is not live proof."""
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import gzip
import json
import sqlite3

from pivot.models import Bar, Market, MAG7, timestamp
from pivot.feeds import ET, resample, daily


def encode_bar(bar):
    return {**bar.__dict__, 'end': bar.end.isoformat()}


def decode_bar(row):
    return Bar(**{**row, 'end': timestamp(row['end'])})


def load(path):
    content = Path(path).read_bytes()
    raw = gzip.decompress(content) if str(path).endswith('.gz') else content
    data = json.loads(raw)
    if data.get('schema') != 'pivot-research-bars-v1':
        raise ValueError('Unknown dataset schema')
    sessions = {day: {k: timestamp(v) for k, v in session.items()} for day, session in data['sessions'].items()}
    if not sessions:
        raise ValueError('An exchange-session calendar is required')
    for day, session in sessions.items():
        if date.fromisoformat(day).isoformat() != day or set(session) != {'open', 'close'}:
            raise ValueError('Expected ISO dates and session open/close timestamps')
        opened, closed = (session[key].astimezone(ET) for key in ('open', 'close'))
        if (opened.date().isoformat() != day or closed.date().isoformat() != day
                or opened.time() != time(9, 30) or not opened < closed
                or closed.time() > time(16)
                or (closed - opened).total_seconds() % 900):
            raise ValueError('Unsupported or inconsistent regular-session calendar')
    stocks = {symbol: [decode_bar(b) for b in data['stocks'][symbol]] for symbol in ('QQQ', *MAG7)}
    vix = [decode_bar(b) for b in data['vix']]
    permitted = {end for session in sessions.values() for end in expected_ends(session)}
    for bars in [*stocks.values(), vix]:
        if any(type(b.minutes) is not int or b.minutes != 15 for b in bars) or any(a.end >= b.end for a, b in zip(bars, bars[1:])):
            raise ValueError('Expected ordered unique 15-minute candles')
        if any(b.end not in permitted for b in bars):
            raise ValueError('Candle is outside the calendar or off the regular-session grid')
    return data, sessions, stocks, vix, sha256(content).hexdigest()


def expected_ends(session):
    at = session['open'] + timedelta(minutes=15)
    while at <= session['close']:
        yield at
        at += timedelta(minutes=15)


def audit(sessions, stocks, vix):
    if not vix:
        raise ValueError('Actual VIX candles are required')
    first, last = vix[0].end.astimezone(ET).date().isoformat(), vix[-1].end.astimezone(ET).date().isoformat()
    indexes = {s: {b.end for b in bars} for s, bars in {**stocks, 'VIX': vix}.items()}
    rows = []
    for day, session in sorted(sessions.items()):
        if not first <= day <= last:
            continue
        expected = set(expected_ends(session))
        missing = {s: len(expected - ends) for s, ends in indexes.items()}
        rows.append({'day': day, 'expected_bars': len(expected), 'missing': missing,
                     'complete': not any(missing.values())})
    return {'sessions': rows, 'complete_sessions': [r['day'] for r in rows if r['complete']],
            'coverage_first': first, 'coverage_last': last,
            'limitations': ['IEX is one exchange, not consolidated trades.',
                           'Subsequently fetched split-adjusted history is not a live arrival-time log.',
                           'Historical VIX candles do not prove a <=90-second entry quote was obtainable.',
                           'No historical NBBO spreads, order-book liquidity or actual fills in this dataset.',
                           'RTH-only and 09:30-anchored full-hour/4h buckets are app conventions.']}


def at_time(sessions, stocks, vix, at):
    """Reconstruct production rolling windows with only closed bars at the cutoff.

    observed_at below is a *simulation clock*, not fabricated historical receipt
    evidence. Live arrival/quote admission remains explicitly unverified.
    """
    start = (at.astimezone(ET)-timedelta(days=60)).replace(hour=0,minute=0,second=0,microsecond=0)
    day = at.astimezone(ET).date().isoformat()
    previous = max((d for d in sessions if d < day), default=None)
    markets = {}
    for symbol, source in stocks.items():
        bars = [b for b in source if b.end <= at and b.end-timedelta(minutes=15) >= start]
        markets[symbol] = Market(symbol, {15: bars, 60: resample(bars, 60, sessions),
                                         240: resample(bars, 240, sessions), 1440: daily(bars, sessions)},
                                 'alpaca_iex', True, at, previous_session=previous)
    oldest = at.astimezone(ET).date()-timedelta(days=14)
    bars = [b for b in vix if b.end <= at and b.end.astimezone(ET).date() >= oldest]
    index = Market('I:VIX', {15: bars}, 'insightsentry', True, at,
                   valid_until=bars[-1].end+timedelta(seconds=990) if bars else at)
    return markets, index


def cached_vix(path):
    """Read an existing database without invoking collector or consuming quota."""
    from pivot.insight_cache import _validate
    uri = Path(path).resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri, uri=True) as db:
        rows = {kind: (payload, received, updated) for kind, payload, received, updated in db.execute(
            'SELECT kind,payload,received_at,source_updated_at FROM insight_cache')}
    if 'metadata' not in rows or 'history' not in rows:
        raise ValueError('Verified cached VIX metadata and history required')
    meta, meta_received, _ = rows['metadata']
    _validate('metadata', json.loads(meta), timestamp(meta_received))
    raw, received, updated = rows['history']
    payload = json.loads(raw)
    source_at = timestamp(_validate('history', payload, timestamp(received)))
    watermark = min(timestamp(received), source_at)
    bars = []
    for row in payload['series']:
        end = datetime.fromtimestamp(row['time'], timezone.utc)+timedelta(minutes=15)
        if end <= watermark:
            bars.append(Bar(end, 15, row['open'], row['high'], row['low'], row['close']))
    return bars, {'source': 'InsightSentry actual CBOE:VIX cached history', 'source_symbol': payload['code'],
                  'metadata_received_at': meta_received, 'received_at': received,
                  'source_updated_at': updated, 'raw_history_sha256': sha256(raw.encode()).hexdigest(),
                  'zero_delay_metadata_verified_at_receipt': True, 'new_vix_requests': 0}
