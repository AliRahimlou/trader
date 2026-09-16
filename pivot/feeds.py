"""Read-only provider adapters. Credentials never enter snapshots or error messages."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone, time, date
from zoneinfo import ZoneInfo
import os
from pathlib import Path
from hashlib import sha256
from math import isfinite
from time import monotonic
import requests
from .models import Bar, Market, MAG7, timestamp

ET = ZoneInfo('America/New_York')


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
        keys = ('equity', 'last_equity', 'cash', 'buying_power', 'currency', 'status', 'trading_blocked', 'account_blocked', 'trade_suspended_by_user', 'shorting_enabled')
        return {**{k: raw.get(k) for k in keys}, 'account_ref': sha256((self.broker_url + ':' + raw['id']).encode()).hexdigest(), 'mode': 'live' if self.broker_url == 'https://api.alpaca.markets' else 'paper'}

    def positions(self):
        keys = ('symbol', 'qty', 'side', 'avg_entry_price', 'current_price', 'market_value', 'unrealized_pl', 'unrealized_plpc')
        return [{k: p.get(k) for k in keys} for p in self.get('alpaca', '/v2/positions')]

    def orders(self):
        raw = self.get('alpaca', '/v2/orders', {'status': 'open', 'nested': 'true', 'limit': 500})
        keys = ('id', 'symbol', 'side', 'qty', 'filled_qty', 'status', 'type', 'limit_price', 'stop_price', 'client_order_id', 'legs')
        return [{k: p.get(k) for k in keys} for p in raw]

    def clock(self):
        raw = self.get('alpaca', '/v2/clock')
        return {k: raw.get(k) for k in ('timestamp', 'is_open', 'next_open', 'next_close')}

    def stock_bars(self, symbols, minutes, start, end, sessions=None):
        """Fetch ordered, completed regular-session bars without filling missing trades."""
        output = defaultdict(list)
        wanted = set(symbols)
        if not wanted or len(wanted) != len(symbols) or not isinstance(minutes, int) or minutes <= 0:
            raise ValueError('Invalid stock bar request')
        params = {'symbols': ','.join(symbols), 'timeframe': f'{minutes}Min', 'start': start.isoformat(),
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
                    if session is None or not session['open'] <= local < session['close']:
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
                if missing:
                    raise FeedError('Completed stock history missing: ' + ', '.join(missing))
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
        started = monotonic()
        try:
            symbols = ('QQQ', *MAG7)
            # Start at a session boundary so the earliest historical day is complete.
            start = (now.astimezone(ET) - timedelta(days=60)).replace(hour=0, minute=0, second=0, microsecond=0)
            cache = getattr(self, '_stock_cache', None)
            full = (not cache or cache['feed'] != self.feed or now < cache['at']
                    or (now - cache['full_at']).total_seconds() >= 3600)
            if full or cache['at'].astimezone(ET).date() != now.astimezone(ET).date():
                sessions = self.stock_calendar(start, now)
            else:
                sessions = self.stock_sessions
            self.stock_sessions = sessions
            session_days = list(sessions)
            # Re-read both the previous and current trading session, including corrections.
            overlap_day = session_days[-2] if len(session_days) >= 2 else session_days[0]
            overlap = sessions[overlap_day]['open']
            fetch_start = start if full else overlap
            fetched = self.stock_bars(symbols, 15, fetch_start, now, sessions=sessions)
            context = {}
            for symbol in symbols:
                retained = [] if full else [bar for bar in cache['bars'][symbol]
                                            if start <= bar.end - timedelta(minutes=15) < overlap]
                combined = retained + fetched[symbol]
                # Validate the merged result before replacing any cached history.
                if any(a.end >= b.end for a, b in zip(combined, combined[1:])):
                    raise FeedError('Merged stock history is duplicated or out of order')
                context[symbol] = combined
            previous_days = [day for day in session_days if day < now.astimezone(ET).date().isoformat()]
            previous_session = previous_days[-1] if previous_days else None
            result = {}
            for symbol, bars in context.items():
                frames = {15: bars, 60: resample(bars, 60, sessions),
                          240: resample(bars, 240, sessions), 1440: daily(bars, sessions)}
                result[symbol] = Market(symbol, frames, f'alpaca_{self.feed}', True, now,
                                        previous_session=previous_session)
            # Only successful, fully validated results replace the cache. A failed refresh
            # raises to the caller; cached data is never relabeled as a successful fetch.
            self._stock_cache = {'feed': self.feed, 'bars': {s: list(b) for s, b in context.items()}, 'at': now,
                                 'full_at': now if full else cache['full_at']}
            self.stock_last_full_at = self._stock_cache['full_at']
            self.stock_refresh_mode = 'full' if full else 'incremental'
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
        end = origin + timedelta(minutes=(int(offset) // minutes + 1) * minutes)
        # Preserve the approved full-hour/full-four-hour policy at shortened closes.
        if end <= session['close']:
            groups[end].append(bar)
    output = []
    for end, group in sorted(groups.items()):
        expected = [end - timedelta(minutes=base * i) for i in reversed(range(minutes // base))]
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
