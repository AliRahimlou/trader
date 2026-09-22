"""Narrow Alpaca execution adapter. Only the execution worker calls its mutations."""
from math import isfinite
import requests
from .feeds import FeedError
from .models import timestamp

# The Socrates signal is always read on QQQ. A SHORT setup cannot be sold on this
# cash account (no shorting; $15 cannot form a whole QQQ share), so it is
# executed by BUYING the -1x daily inverse ETF. App interpretation, not a video
# rule: the recordings say "Nasdaq short" and nothing about an ETF proxy.
SIGNAL_SYMBOL = 'QQQ'
PROXY_SYMBOL = 'PSQ'
PROXY_KIND = 'inverse_etf'
# Every position or order in one of these symbols may belong to Socrates; any
# other stock symbol is foreign exposure. Deployment flatness covers both.
PROXY_SYMBOLS = frozenset({SIGNAL_SYMBOL, PROXY_SYMBOL})


class BrokerRejected(FeedError):
    """A definitive POST rejection (HTTP 4xx): no order exists at the broker.

    Only the HTTP status and Alpaca's numeric error ``code`` are retained. The
    response ``message`` is never embedded: it can echo request or account
    details, and everything in this exception reaches the journal and alerts.
    """
    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status, self.code = status, code

    def detail(self):
        """Short owner-facing cause: 'HTTP 422, Alpaca error code 40310000'."""
        if self.status is None:
            return str(self)
        return f'HTTP {self.status}' + (f', Alpaca error code {self.code}' if self.code is not None else '')


def rejection(response, prefix):
    """Build a BrokerRejected from a 4xx POST response without leaking its body."""
    code = None
    try:
        body = response.json()
        if isinstance(body, dict) and type(body.get('code')) is int:
            code = body['code']
    except Exception:
        code = None
    return BrokerRejected(f'{prefix} (HTTP {response.status_code}' + (f', Alpaca error code {code}' if code is not None else '') + ')',
                          status=response.status_code, code=code)


class AlpacaBroker:
    def __init__(self, feeds, session=None):
        self.feeds = feeds
        self.session = session or requests.Session()

    def account(self): return self.feeds.account()
    def positions(self): return self.feeds.positions()
    def orders(self): return self.feeds.orders()
    def clock(self): return self.feeds.clock()
    def asset(self, symbol): return self.feeds.get('alpaca', '/v2/assets/' + symbol)
    def quote(self, symbol):
        """Latest NBBO for one owned execution symbol; QQQ keeps the shared feed path."""
        if symbol not in PROXY_SYMBOLS:
            raise ValueError('Only the configured QQQ signal and PSQ proxy instruments are supported')
        if symbol == SIGNAL_SYMBOL:
            return self.feeds.quote()
        return self._latest_stock_quote(symbol)

    def _latest_stock_quote(self, symbol):
        """Same validation as the QQQ feed quote, for the proxy symbol's own NBBO."""
        raw = self.feeds.get('stocks', f'/v2/stocks/{symbol}/quotes/latest', {'feed': self.feeds.feed})
        if not isinstance(raw, dict) or raw.get('symbol') != symbol or not isinstance(raw.get('quote'), dict):
            raise FeedError(f'Latest {symbol} quote response is invalid')
        try:
            quote = {'t': timestamp(raw['quote']['t']).isoformat()}
            for key in ('bp', 'ap', 'bs', 'as'):
                value = float(raw['quote'][key])
                if not isfinite(value) or value <= 0:
                    raise ValueError()
                quote[key] = value
            return quote
        except (KeyError, TypeError, ValueError, OverflowError):
            raise FeedError(f'Latest {symbol} quote contains invalid prices, sizes or timestamp') from None

    @property
    def requires_vix_entry_quote(self):
        return getattr(self.feeds, 'vix_provider', None) == 'insightsentry'

    def confirm_vix_quote(self, now):
        return self.feeds.confirm_vix_quote(now)

    def _request(self, method, path, *, payload=None, params=None):
        try:
            response = self.session.request(method, self.feeds.broker_url + path,
                headers=self.feeds.alpaca_headers, json=payload, params=params,
                timeout=(3, 10), allow_redirects=False)
        except requests.RequestException:
            raise FeedError('Broker response uncertain; reconciling by order identifier before any further action') from None
        if method == 'GET' and response.status_code == 404:
            return None
        if method == 'DELETE' and response.status_code in (204, 404, 422):
            # A cancellation response never proves the order was canceled; the worker reads it again.
            return None
        if method == 'POST' and response.status_code in (400, 401, 403, 422):
            raise rejection(response, 'Broker rejected the order')
        if response.status_code not in (200, 201):
            raise FeedError(f'Broker response uncertain (HTTP {response.status_code}); waiting for reconciliation')
        try:
            return response.json()
        except ValueError:
            raise FeedError('Broker returned an unreadable response; waiting for reconciliation') from None

    def lookup(self, client_id):
        return self._request('GET', '/v2/orders:by_client_order_id', params={'client_order_id': client_id})

    def submit(self, payload):
        return self._request('POST', '/v2/orders', payload=payload)

    def cancel(self, order_id):
        self._request('DELETE', '/v2/orders/' + order_id)
