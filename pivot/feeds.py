"""Read-only provider adapters. Credentials never enter snapshots or error messages."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone, time, date
from zoneinfo import ZoneInfo
import os
from pathlib import Path
from hashlib import sha256
from math import isfinite
from time import monotonic
import requests
from .models import Bar, Market, MAG7, timestamp
from .history_health import bucket_ends

ET = ZoneInfo('America/New_York')
LEADER_HISTORY_SESSIONS = 7
STOCK_CACHE_SCHEMA = 'required-frames-v4'


def leader_history_sessions(sessions):
    """Actual recent sessions for native five-minute leader history."""
    return dict(sorted(sessions.items())[-LEADER_HISTORY_SESSIONS:])


class FeedError(RuntimeError):
    pass


class ReadOnlyFeeds:
    def __init__(self, env=None, session=None, *, insight_cache=None):
        env = os.environ if env is None else env
        self.session = session or requests.Session()
        self.alpaca_headers = {'APCA-API-KEY-ID': env.get('APCA_API_KEY_ID', ''),
                               'APCA-API-SECRET-KEY': env.get('APCA_API_SECRET_KEY', '')}
        # Fixed destinations: never forward saved credentials to an arbitrary .env URL.
        raw = env.get('APCA_API_BASE_URL', 'https://api.alpaca.markets').rstrip('/')
        if raw not in ('https://api.alpaca.markets', 'https://paper-api.alpaca.markets'):
            raise ValueError('Unsupported Alpaca host')
        self.broker_url = raw
        self.feed = env.get('APCA_API_FEED', 'iex').lower()
        if self.feed not in ('iex', 'sip'):
            raise ValueError('Unsupported stock feed')
        self.massive_key = env.get('MASSIVE_API_KEY') or env.get('MASSIVE_API', '')
        self.vix_provider = env.get('VIX_PROVIDER') or ('insightsentry' if env.get('INSIGHTSENTRY_API_KEY') else 'massive')
        if self.vix_provider not in ('insightsentry', 'massive'):
            raise ValueError('Unsupported VIX provider')
        self._insight_key = env.get('INSIGHTSENTRY_API_KEY', '')
        self._insight_cache = insight_cache

    def get(self, provider, path, params=None):
        if provider == 'alpaca':
            base, headers = self.broker_url, self.alpaca_headers
        elif provider == 'stocks':
            base, headers = 'https://data.alpaca.markets', self.alpaca_headers
        elif provider == 'indices':
            if not self.massive_key:
                raise FeedError('Index data credentials are not configured')
            base, headers = 'https://api.massive.com', {'Authorization': 'Bearer ' + self.massive_key}
        else:
            raise ValueError('Unsupported provider')
        try:
            response = self.session.get(base + path, headers=headers, params=params, timeout=(3, 10), allow_redirects=False)
        except requests.RequestException:
            raise FeedError(f'{provider}: connection unavailable') from None
        if response.status_code != 200:
            message = 'data access is not included in the current plan' if response.status_code == 403 else f'HTTP {response.status_code}'
            raise FeedError(f'{provider}: {message}')
        try:
            return response.json()
        except ValueError:
            raise FeedError(f'{provider}: invalid response') from None

    def account(self):
        raw = self.get('alpaca', '/v2/account')
        keys = ('equity', 'last_equity', 'cash', 'buying_power', 'non_marginable_buying_power', 'crypto_status', 'currency', 'status', 'trading_blocked', 'account_blocked', 'trade_suspended_by_user', 'shorting_enabled')
        return {**{k: raw.get(k) for k in keys}, 'account_ref': sha256((self.broker_url + ':' + raw['id']).encode()).hexdigest(), 'mode': 'live' if self.broker_url == 'https://api.alpaca.markets' else 'paper'}

    def positions(self):
        keys = ('symbol', 'qty', 'qty_available', 'asset_class', 'side', 'avg_entry_price', 'current_price', 'market_value', 'unrealized_pl', 'unrealized_plpc')
        return [{k: p.get(k) for k in keys} for p in self.get('alpaca', '/v2/positions')]

    def orders(self):
        raw = self.get('alpaca', '/v2/orders', {'status': 'open', 'nested': 'true', 'limit': 500})
        keys = ('id', 'symbol', 'side', 'qty', 'filled_qty', 'status', 'type', 'limit_price', 'stop_price', 'client_order_id', 'legs')
        return [{k: p.get(k) for k in keys} for p in raw]

    def clock(self):
        raw = self.get('alpaca', '/v2/clock')
        return {k: raw.get(k) for k in ('timestamp', 'is_open', 'next_open', 'next_close')}

    def stock_bars(self, symbols, minutes, start, end, sessions=None, *, required=True):
        """Fetch ordered, completed regular-session bars without filling missing trades.

        Intraday candles must align to their session open. Daily candles (1440)
        are the provider's own trading-day bars, stamped at the start of the day;
        each completed one is end-stamped at that session's close, and the
        ongoing day is discarded until the close has passed. Native ``v`` and
        ``vw`` are kept on the Bar so consumers can build session VWAP. A symbol
        with no completed candle fails the read unless ``required`` is False
        (report-only context), in which case it is simply absent from the result.
        """
        output = defaultdict(list)
        wanted = set(symbols)
        if not wanted or len(wanted) != len(symbols) or not isinstance(minutes, int) or minutes <= 0:
            raise ValueError('Invalid stock bar request')
        timeframe = '1Day' if minutes == 1440 else f'{minutes}Min'
        params = {'symbols': ','.join(symbols), 'timeframe': timeframe, 'start': start.isoformat(),
                  'end': end.isoformat(), 'adjustment': 'split', 'feed': self.feed, 'sort': 'asc', 'limit': 10000}
        seen, last_at = set(), {}
        for _ in range(64):
            raw = self.get('stocks', '/v2/stocks/bars', params)
            if not isinstance(raw, dict) or not isinstance(raw.get('bars'), dict):
                raise FeedError('Stock bars response is incomplete')
            if set(raw['bars']) - wanted:
                raise FeedError('Stock bars response contains unexpected symbols')
            for symbol, bars in raw['bars'].items():
                if not isinstance(bars, list):
                    raise FeedError('Stock bars response is invalid')
                for b in bars:
                    try:
                        at = timestamp(b['t'])
                        bar = Bar(at + timedelta(minutes=minutes), minutes,
                                  b['o'], b['h'], b['l'], b['c'], b['v'], b.get('vw'))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        raise FeedError('Stock bars contain an invalid candle') from None
                    if symbol in last_at and at <= last_at[symbol]:
                        raise FeedError('Stock bars are duplicated or out of order')
                    last_at[symbol] = at
                    local = at.astimezone(ET)
                    session = session_for(local.date(), sessions)
                    if session is None:
                        continue
                    if minutes == 1440:
                        if local >= session['close']:
                            raise FeedError('Stock candle is not aligned to its session')
                        bar = replace(bar, end=session['close'])
                    else:
                        if not session['open'] <= local < session['close']:
                            continue
                        if (local - session['open']).total_seconds() % (minutes * 60):
                            raise FeedError('Stock candle is not aligned to its session')
                    # An ongoing provider candle is never treated as a closed observation.
                    if bar.end > end or bar.end > session['close']:
                        continue
                    output[symbol].append(bar)
            token = raw.get('next_page_token')
            if token is None or token == '':
                missing = [symbol for symbol in symbols if not output.get(symbol)]
                if missing and required:
                    label = 'daily' if minutes == 1440 else f'{minutes}-minute'
                    raise FeedError(f'Completed {label} stock history missing: ' + ', '.join(missing))
                return dict(output)
            if not isinstance(token, str):
                raise FeedError('Invalid stock pagination token')
            if token in seen:
                raise FeedError('Repeated stock pagination token')
            seen.add(token)
            params['page_token'] = token
        raise FeedError('Stock history was truncated; no signals may use it')

    def stock_calendar(self, start, end):
        raw = self.get('alpaca', '/v2/calendar',
                       {'start': start.astimezone(ET).date().isoformat(),
                        'end': end.astimezone(ET).date().isoformat()})
        if not isinstance(raw, list) or not raw:
            raise FeedError('Trading-session calendar is unavailable')
        sessions = {}
        try:
            for row in raw:
                day = date.fromisoformat(row['date'])
                opened, closed = time.fromisoformat(row['open']), time.fromisoformat(row['close'])
                if opened.tzinfo or closed.tzinfo or opened >= closed or row['date'] in sessions:
                    raise ValueError('Invalid session')
                if not start.astimezone(ET).date() <= day <= end.astimezone(ET).date():
                    raise ValueError('Session outside request')
                sessions[row['date']] = {'open': datetime.combine(day, opened, ET),
                                         'close': datetime.combine(day, closed, ET)}
        except (KeyError, TypeError, ValueError):
            raise FeedError('Trading-session calendar is invalid') from None
        return dict(sorted(sessions.items()))

    def stocks(self, now):
        """Validated QQQ context frames plus native leader five-minute and daily candles.

        Leader daily candles (frames[1440]; video: the leaders' period-level
        indicator plots prior day/week/month opens, highs and lows) are
        report-only context: a failed daily read is recorded in
        ``stock_leader_daily_error`` and leaves that frame out, never failing
        the required refresh. App choice: nothing in the videos makes a daily
        candle an entry input.
        """
        started = monotonic()
        try:
            # Only QQQ needs the long 15-minute history used for higher frames.
            # Leaders use native five-minute observations plus provider daily bars.
            symbols = ('QQQ',)
            # Start at a session boundary so the earliest historical day is complete.
            start = (now.astimezone(ET) - timedelta(days=60)).replace(hour=0, minute=0, second=0, microsecond=0)
            cache = getattr(self, '_stock_cache', None)
            full = (not cache or cache.get('schema') != STOCK_CACHE_SCHEMA
                    or 'leader_bars' not in cache or cache['feed'] != self.feed or now < cache['at']
                    or (now - cache['full_at']).total_seconds() >= 3600)
            if full or cache['at'].astimezone(ET).date() != now.astimezone(ET).date():
                sessions = self.stock_calendar(start, now)
            else:
                sessions = self.stock_sessions
            session_days = list(sessions)
            leader_sessions = leader_history_sessions(sessions)
            leader_start = next(iter(leader_sessions.values()))['open']
            # Re-read both the previous and current trading session, including corrections.
            overlap_day = session_days[-2] if len(session_days) >= 2 else session_days[0]
            overlap = sessions[overlap_day]['open']
            fetch_start = start if full else overlap
            leader_fetch_start = leader_start if full else max(leader_start, overlap)
            # Provider daily candles are stamped at the start of the day, before the
            # session open, so the overlap re-read starts at that day's midnight.
            daily_full = full or not all(cache.get('leader_daily', {}).get(symbol) for symbol in MAG7)
            daily_fetch_start = start if daily_full else overlap.replace(hour=0, minute=0, second=0, microsecond=0)
            # Genuine provider five-minute candles are fetched separately; fifteen-minute
            # candles are never split, relabeled or forward-filled into faster input.
            with ThreadPoolExecutor(max_workers=3) as pool:
                context_future = pool.submit(self.stock_bars, symbols, 15, fetch_start, now, sessions)
                leaders_future = pool.submit(self.stock_bars, MAG7, 5, leader_fetch_start, now, leader_sessions)
                daily_future = pool.submit(self.stock_bars, MAG7, 1440, daily_fetch_start, now, sessions, required=False)
                fetched, leader_fetched = context_future.result(), leaders_future.result()
                try:
                    daily_fetched, daily_error = daily_future.result(), None
                except FeedError as exc:
                    daily_fetched, daily_error = {}, str(exc)

            def started_at(bar):
                if bar.minutes != 1440:
                    return bar.end - timedelta(minutes=bar.minutes)
                day = (bar.end - timedelta(seconds=1)).astimezone(ET).date().isoformat()
                return sessions[day]['open'] if day in sessions else None

            context, leader_context, daily_context = {}, {}, {}
            for minutes, names, incoming, target, cache_key, earliest, refetched in (
                (15, symbols, fetched, context, 'bars', start, full),
                (5, MAG7, leader_fetched, leader_context, 'leader_bars', leader_start, full),
                (1440, MAG7, daily_fetched, daily_context, 'leader_daily', start, daily_full),
            ):
                for symbol in names:
                    retained = [] if refetched else [bar for bar in cache[cache_key].get(symbol, [])
                                                     if started_at(bar) is not None and earliest <= started_at(bar) < overlap]
                    # Required frames raised above when a symbol was absent; the
                    # report-only daily frame may simply lack a symbol.
                    combined = retained + incoming.get(symbol, [])
                    # Every frame history must validate before any cache is published.
                    if any(a.end >= b.end for a, b in zip(combined, combined[1:])):
                        raise FeedError('Merged stock history is duplicated or out of order')
                    if combined:
                        target[symbol] = combined
            previous_days = [day for day in session_days if day < now.astimezone(ET).date().isoformat()]
            previous_session = previous_days[-1] if previous_days else None
            result = {}
            for symbol, bars in context.items():
                frames = {15: bars, 60: resample(bars, 60, sessions),
                          240: resample(bars, 240, sessions), 1440: daily(bars, sessions)}
                result[symbol] = Market(symbol, frames, f'alpaca_{self.feed}', True, now,
                                        previous_session=previous_session)
            for symbol, bars in leader_context.items():
                frames = {5: bars}
                if daily_context.get(symbol):
                    frames[1440] = daily_context[symbol]
                result[symbol] = Market(symbol, frames, f'alpaca_{self.feed}', True, now,
                                        previous_session=previous_session)
            # Only successful, fully validated results replace the cache. A failed refresh
            # raises to the caller; cached data is never relabeled as a successful fetch.
            self._stock_cache = {'schema': STOCK_CACHE_SCHEMA, 'feed': self.feed, 'bars': {s: list(b) for s, b in context.items()},
                                 'leader_bars': {s: list(b) for s, b in leader_context.items()},
                                 'leader_daily': {s: list(b) for s, b in daily_context.items()},
                                 'at': now, 'full_at': now if full else cache['full_at']}
            self.stock_sessions = sessions
            self.stock_leader_sessions = leader_sessions
            self.stock_last_full_at = self._stock_cache['full_at']
            self.stock_refresh_mode = 'full' if full else 'incremental'
            self.stock_leader_daily_error = daily_error
            return result
        finally:
            self.stock_fetch_seconds = monotonic() - started

    def quote(self):
        raw = self.get('stocks', '/v2/stocks/QQQ/quotes/latest', {'feed': self.feed})
        if not isinstance(raw, dict) or raw.get('symbol') != 'QQQ' or not isinstance(raw.get('quote'), dict):
            raise FeedError('Latest QQQ quote response is invalid')
        try:
            quote={'t':timestamp(raw['quote']['t']).isoformat()}
            for key in ('bp','ap','bs','as'):
                value=float(raw['quote'][key])
                if not isfinite(value) or value<=0:
                    raise ValueError()
                quote[key]=value
            return quote
        except (KeyError, TypeError, ValueError, OverflowError):
            raise FeedError('Latest QQQ quote contains invalid prices, sizes or timestamp') from None

    def vix(self, now):
        if self.vix_provider == 'insightsentry':
            from .insight_data import load_history, diagnostics
            cache = self.insight_cache()
            self.vix_diagnostics = diagnostics(cache, now)
            market, self.vix_diagnostics = load_history(cache, now, getattr(self, 'stock_sessions', None), report=self.vix_diagnostics)
            return market
        from .index_data import load_vix
        return load_vix(self, now)

    def insight_cache(self):
        if self._insight_cache is None:
            from .insight_cache import InsightCache
            if not self._insight_key:
                raise FeedError('InsightSentry credentials are not configured')
            path = Path(__file__).resolve().parents[1] / 'runtime' / 'pivot-v2' / 'vix-insight.sqlite3'
            self._insight_cache = InsightCache(path, self._insight_key)
        return self._insight_cache

    def confirm_vix_quote(self, now):
        if self.vix_provider != 'insightsentry':
            raise FeedError('The configured VIX provider does not support this quote check')
        from .insight_data import confirm_quote
        return confirm_quote(self.insight_cache(), now)


def merge(bars, minutes, end):
    volume = sum(b.volume for b in bars)
    vw = sum(b.vwap * b.volume for b in bars) / volume if volume and all(b.vwap is not None for b in bars) else None
    return Bar(end, minutes, bars[0].open, max(b.high for b in bars), min(b.low for b in bars), bars[-1].close, volume, vw)


def session_for(day, sessions=None):
    """Optional calendar preserves the aggregation helpers' standalone API."""
    if sessions is not None:
        return sessions.get(day.isoformat())
    if day.weekday() >= 5:
        return None
    return {'open': datetime.combine(day, time(9, 30), ET),
            'close': datetime.combine(day, time(16), ET)}


def resample(bars, minutes, sessions=None):
    """Aggregate base candles into session-anchored buckets; never fill a missing candle.

    Buckets follow ``history_health.bucket_ends``: full buckets only for hourly
    frames (the 15:30-16:00 remainder is dropped), while the four-hour frame
    also emits the closing bucket (13:30-16:00 on a full day, 150 minutes of
    data still tagged ``minutes=240``; on an early close the buckets that fit,
    the last one partial and ending at the close). Every base candle of a
    bucket must be present. Video rule: four-hour extremes come from continuous
    futures candles; the two-bucket split is the app's regular-session reading.
    """
    if not bars:
        return []
    base = bars[0].minutes
    if minutes <= 0 or minutes % base or any(b.minutes != base for b in bars):
        raise ValueError('Cannot resample incompatible frames')
    groups = defaultdict(list)
    for bar in bars:
        start = (bar.end - timedelta(minutes=base)).astimezone(ET)
        session = session_for(start.date(), sessions)
        if session is None or not session['open'] <= start < bar.end <= session['close']:
            continue
        origin = session['open']
        offset = (start - origin).total_seconds() / 60
        if offset % base:
            continue
        ends = bucket_ends(origin, session['close'], minutes)
        index = int(offset) // minutes
        # Remainders without a bucket (the closing half hour for hourly frames) are dropped.
        if index < len(ends):
            groups[(origin + timedelta(minutes=index * minutes), ends[index])].append(bar)
    output = []
    for (start, end), group in sorted(groups.items()):
        expected, at = [], start + timedelta(minutes=base)
        while at <= end:
            expected.append(at)
            at += timedelta(minutes=base)
        if [b.end for b in group] == expected:
            output.append(merge(group, minutes, end))
    return output


def daily(bars, sessions=None):
    groups = defaultdict(list)
    for b in bars:
        if b.minutes != 15:
            raise ValueError('Daily aggregation requires fifteen-minute bars')
        day = (b.end - timedelta(seconds=1)).astimezone(ET).date()
        session = session_for(day, sessions)
        if session and session['open'] < b.end <= session['close']:
            groups[day].append(b)
    output = []
    for day, group in sorted(groups.items()):
        session = session_for(day, sessions)
        minutes = (session['close'] - session['open']).total_seconds() / 60
        if minutes <= 0 or minutes % 15:
            continue
        expected = [session['open'] + timedelta(minutes=15 * i) for i in range(1, int(minutes / 15) + 1)]
        if [b.end for b in group] == expected:
            output.append(merge(group, 1440, session['close']))
    return output
