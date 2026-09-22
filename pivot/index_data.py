"""Actual VIX history with explicit real-time provenance and completed-bar validation."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from urllib.parse import urlsplit, parse_qsl
from .models import Bar, Market
from .feeds import FeedError
from .data_health import SOURCE_CLOCK_SKEW_SECONDS


def load_vix(feeds, now, *, received_at=None):
    feeds.vix_diagnostics = {'checked_at': now.isoformat(), 'timeframe': 'unverified'}
    # Successful aggregate requests alone do not prove a real-time subscription.
    snapshot = feeds.get('indices', '/v3/snapshot/indices', {'ticker': 'I:VIX'})
    items = snapshot.get('results') if isinstance(snapshot, dict) else None
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'OK' or not isinstance(items, list):
        raise FeedError('VIX snapshot is missing or malformed')
    matches = [i for i in items if isinstance(i, dict) and i.get('ticker') == 'I:VIX']
    if len(matches) != 1:
        raise FeedError('Actual VIX was not returned by the index provider')
    point = matches[0]
    if point.get('error'):
        raise FeedError('Actual VIX access is not included in the current index plan')
    feeds.vix_diagnostics['timeframe'] = point.get('timeframe', 'unverified')
    if point.get('type') != 'indices':
        raise FeedError('VIX snapshot did not identify an actual index')
    if point.get('timeframe') != 'REAL-TIME':
        raise FeedError('Provider labels actual VIX delayed or unverified; real-time access is required')
    try:
        seen = received_at or datetime.now(timezone.utc)
        updated = datetime.fromtimestamp(point['last_updated'] / 1_000_000_000, timezone.utc)
        value = float(point['value'])
        # A source stamp within CLOCK_SKEW ahead of the host clock is skew, not a future value.
        if not isfinite(value) or value <= 0 or not -SOURCE_CLOCK_SKEW_SECONDS <= (seen - updated).total_seconds() <= 90:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise FeedError('Actual VIX snapshot is stale, future-dated or invalid') from None
    feeds.vix_diagnostics.update(latest_value_at=updated.isoformat(), latest_value=value)
    start = (now - timedelta(days=14)).date().isoformat()
    path = f'/v2/aggs/ticker/I:VIX/range/15/minute/{start}/{now.date().isoformat()}'
    params = {'sort': 'asc', 'limit': 50000}
    bars, previous, seen_pages = [], None, set()
    for _ in range(16):
        raw = feeds.get('indices', path, params)
        if not isinstance(raw, dict) or raw.get('status') != 'OK' or raw.get('ticker') != 'I:VIX' or not isinstance(raw.get('results'), list):
            raise FeedError('VIX candle response is missing, delayed or malformed')
        try:
            for item in raw['results']:
                at = datetime.fromtimestamp(item['t'] / 1000, timezone.utc)
                if at.second or at.microsecond or at.minute % 15 or (previous and at <= previous):
                    raise ValueError()
                previous = at
                bar = Bar(at + timedelta(minutes=15), 15, item['o'], item['h'], item['l'], item['c'])
                if bar.end <= now:
                    bars.append(bar)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise FeedError('VIX candles contain invalid timestamps, duplicates or invalid prices') from None
        link = raw.get('next_url')
        if not link:
            break
        if not isinstance(link, str) or link in seen_pages:
            raise FeedError('VIX history pagination did not complete')
        seen_pages.add(link)
        parsed = urlsplit(link)
        if parsed.scheme != 'https' or parsed.netloc != 'api.massive.com' or not parsed.path.startswith('/v2/aggs/ticker/I:VIX/range/15/minute/'):
            raise FeedError('Invalid VIX history continuation; credentials were not forwarded')
        path = parsed.path
        params = {k: v for k, v in parse_qsl(parsed.query) if k.lower() != 'apikey'}
    else:
        raise FeedError('VIX history was truncated')
    if len(bars) < 3 or not 0 <= (now - bars[-1].end).total_seconds() <= 990:
        raise FeedError('Completed VIX candles are missing or stale')
    feeds.vix_diagnostics.update(completed_bars=len(bars), latest_candle_at=bars[-1].end.isoformat())
    return Market('I:VIX', {15: bars}, 'massive_indices', True, now)
