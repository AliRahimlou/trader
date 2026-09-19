"""Alpaca spot adapter with fixed destinations and crypto-specific order validation."""
from hashlib import sha256
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import quote
import requests
from .broker import BrokerRejected
from .feeds import FeedError
from .crypto_store import SYMBOLS
from .crypto_markets import ALIASES


def canonical_symbol(value):
    return ALIASES.get(value, value)


class CryptoBroker:
    def __init__(self, feeds, session=None):
        if feeds.broker_url not in ('https://api.alpaca.markets', 'https://paper-api.alpaca.markets'):
            raise ValueError('Unsupported Alpaca crypto host')
        self.base = feeds.broker_url
        self.headers = dict(feeds.alpaca_headers)
        self.session = session or requests.Session()

    def _request(self, method, path, *, payload=None, params=None, data=False):
        base = 'https://data.alpaca.markets' if data else self.base
        try:
            response = self.session.request(method, base + path, headers=self.headers,
                                            json=payload, params=params, timeout=(3, 10), allow_redirects=False)
        except requests.RequestException:
            raise FeedError('Crypto broker response unavailable; existing order intents will be reconciled') from None
        if method == 'GET' and response.status_code == 404 and path == '/v2/orders:by_client_order_id':
            return None
        if method == 'DELETE' and response.status_code in (204, 404, 422):
            return None  # Always confirm by GET before a competing sell.
        if method == 'POST' and response.status_code in (400, 401, 403, 422):
            raise BrokerRejected(f'Crypto broker rejected the order (HTTP {response.status_code})')
        if response.status_code not in (200, 201):
            raise FeedError(f'Crypto broker state unavailable (HTTP {response.status_code})')
        try:
            return response.json()
        except ValueError:
            raise FeedError('Crypto broker returned an unreadable response') from None

    def account(self):
        raw = self._request('GET', '/v2/account')
        if not isinstance(raw, dict) or not isinstance(raw.get('id'), str) or not raw['id']:
            raise FeedError('Crypto account identity is unavailable')
        fields = ('status', 'crypto_status', 'cash', 'equity', 'non_marginable_buying_power',
                  'trading_blocked', 'account_blocked', 'trade_suspended_by_user')
        return {**{k: raw.get(k) for k in fields},
                'account_ref': sha256((self.base + ':' + raw['id']).encode()).hexdigest(),
                'mode': 'live' if self.base == 'https://api.alpaca.markets' else 'paper'}

    def positions(self):
        raw = self._request('GET', '/v2/positions')
        if not isinstance(raw, list) or any(not isinstance(p, dict) for p in raw):
            raise FeedError('Crypto position reconciliation is unavailable')
        return [{**p, 'symbol': canonical_symbol(p.get('symbol'))} for p in raw]

    def orders(self):
        raw = self._request('GET', '/v2/orders', params={'status': 'open', 'nested': 'true', 'limit': 500})
        if not isinstance(raw, list) or len(raw) >= 500 or any(not isinstance(p, dict) for p in raw):
            raise FeedError('Complete open-order reconciliation is unavailable')
        return [{**p, 'symbol': canonical_symbol(p.get('symbol'))} for p in raw]

    def asset(self, symbol):
        if symbol not in SYMBOLS:
            raise ValueError('Unsupported crypto instrument')
        return self._request('GET', '/v2/assets/' + quote(symbol, safe=''))

    def quote(self, symbol):
        if symbol not in SYMBOLS:
            raise ValueError('Unsupported crypto instrument')
        raw = self._request('GET', '/v1beta3/crypto/us/latest/quotes', params={'symbols': symbol}, data=True)
        if not isinstance(raw, dict) or not isinstance(raw.get('quotes'), dict) or set(raw['quotes']) != {symbol}:
            raise FeedError('Current crypto bid and ask are unavailable')
        value = raw['quotes'][symbol]
        if not isinstance(value, dict):
            raise FeedError('Current crypto quote is invalid')
        return {**value, 'symbol': symbol, 'source': 'alpaca_crypto_us'}

    def lookup(self, client_id):
        if not isinstance(client_id, str) or not re.fullmatch(r'cr-[a-f0-9]{24}-[a-z0-9]+', client_id):
            raise ValueError('Invalid crypto client order identity')
        value = self._request('GET', '/v2/orders:by_client_order_id', params={'client_order_id': client_id})
        if isinstance(value, dict):
            value = {**value, 'symbol': canonical_symbol(value.get('symbol'))}
        return value

    def submit(self, payload):
        if not isinstance(payload, dict) or payload.get('symbol') not in SYMBOLS:
            raise ValueError('Unsupported crypto order')
        allowed = {'symbol', 'side', 'qty', 'type', 'time_in_force', 'limit_price', 'stop_price', 'client_order_id'}
        if set(payload) - allowed or payload.get('side') not in ('buy', 'sell'):
            raise ValueError('Unsupported crypto order fields')
        kind, tif = payload.get('type'), payload.get('time_in_force')
        if kind not in ('market', 'limit', 'stop_limit') or tif not in ('gtc', 'ioc'):
            raise ValueError('Unsupported crypto order type')
        if payload['side'] == 'buy' and (kind, tif) != ('limit', 'ioc'):
            raise ValueError('Crypto entries require a capped immediate limit order')
        required = ['qty'] + (['limit_price'] if kind in ('limit', 'stop_limit') else []) + (['stop_price'] if kind == 'stop_limit' else [])
        try:
            if any(not isinstance(payload.get(k), str) or not Decimal(payload[k]).is_finite() or Decimal(payload[k]) <= 0 for k in required):
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ValueError('Crypto order quantity or prices are invalid') from None
        cid = payload.get('client_order_id')
        if not isinstance(cid, str) or not re.fullmatch(r'cr-[a-f0-9]{24}-[a-z0-9]+', cid):
            raise ValueError('Invalid crypto client order identity')
        value = self._request('POST', '/v2/orders', payload=payload)
        if isinstance(value, dict):
            value = {**value, 'symbol': canonical_symbol(value.get('symbol'))}
        return value

    def cancel(self, order_id):
        if not isinstance(order_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', order_id):
            raise ValueError('Invalid broker order identity')
        self._request('DELETE', '/v2/orders/' + order_id)
