from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import requests
import pytest
from pivot.crypto_broker import CryptoBroker
from pivot.broker import BrokerRejected
from pivot.feeds import FeedError


class Response:
    def __init__(self, code=200, body=None): self.status_code, self.body = code, body
    def json(self):
        if isinstance(self.body, Exception): raise self.body
        return deepcopy(self.body)


class Session:
    def __init__(self, response): self.response, self.calls = response, []
    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if isinstance(self.response, Exception): raise self.response
        return self.response


def broker(response):
    feeds = SimpleNamespace(broker_url='https://api.alpaca.markets',
                            alpaca_headers={'APCA-API-KEY-ID': 'fixture-key', 'APCA-API-SECRET-KEY': 'fixture-secret'})
    session = Session(response)
    return CryptoBroker(feeds, session), session


def entry():
    return dict(symbol='BTC/USD', side='buy', type='limit', time_in_force='ioc', qty='.001',
                limit_price='5000', client_order_id='cr-'+'a'*24+'-entry')


def test_fixed_hosts_no_redirects_and_account_identity_matches_equity_feed_hash():
    b, session = broker(Response(body={'id': 'fake-id', 'crypto_status': 'ACTIVE', 'non_marginable_buying_power': '5'}))
    account = b.account()
    assert account['account_ref'] == sha256(b'https://api.alpaca.markets:fake-id').hexdigest()
    assert account['mode'] == 'live' and 'id' not in account
    _, url, kw = session.calls[0]
    assert url == 'https://api.alpaca.markets/v2/account' and kw['allow_redirects'] is False
    assert kw['timeout'] == (3, 10)


def test_crypto_quote_uses_fixed_data_host_and_explicit_provenance():
    b, session = broker(Response(body={'quotes': {'BTC/USD': {'t':'now','bp':'1','ap':'2'}}}))
    q = b.quote('BTC/USD')
    assert q['source'] == 'alpaca_crypto_us' and q['symbol'] == 'BTC/USD'
    assert session.calls[0][1] == 'https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes'


def test_assets_use_encoded_pair_path():
    b, session = broker(Response(body={}))
    b.asset('BTC/USD')
    assert session.calls[0][1].endswith('/v2/assets/BTC%2FUSD')


def test_position_and_order_aliases_are_normalized():
    b, _ = broker(Response(body=[{'symbol':'BTCUSD', 'qty_available':'.1'}]))
    assert b.positions()[0]['symbol'] == 'BTC/USD'
    assert b.orders()[0]['symbol'] == 'BTC/USD'


def test_possible_truncation_of_open_orders_fails_closed():
    b, _ = broker(Response(body=[{}]*500))
    with pytest.raises(FeedError): b.orders()


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(symbol='DOGE/USD'), lambda p: p.update(side='short'),
    lambda p: p.update(type='market'), lambda p: p.update(time_in_force='day'),
    lambda p: p.update(qty='NaN'), lambda p: p.update(qty='0'),
    lambda p: p.update(order_class='bracket'), lambda p: p.update(notional='5'),
    lambda p: p.update(client_order_id='../../orders'),
])
def test_invalid_mutation_payload_never_reaches_transport(mutate):
    b, session = broker(Response(body={}))
    p = entry(); mutate(p)
    with pytest.raises(ValueError): b.submit(p)
    assert not session.calls


def test_sell_market_and_stop_limit_are_supported():
    b, session = broker(Response(body={}))
    p = entry(); p.update(side='sell', type='market', time_in_force='gtc'); p.pop('limit_price')
    b.submit(p)
    p.update(type='stop_limit', stop_price='4900', limit_price='4851')
    b.submit(p)
    assert len(session.calls) == 2


@pytest.mark.parametrize('status', [400,401,403,422])
def test_definitive_post_rejection_is_distinct_from_unknown(status):
    b, _ = broker(Response(status, {'message':'fixture-secret should never leak'}))
    with pytest.raises(BrokerRejected) as exc: b.submit(entry())
    assert 'fixture-secret' not in str(exc.value)


@pytest.mark.parametrize('response', [Response(500), Response(302), Response(200, ValueError('fixture-secret')), requests.ConnectionError('fixture-secret')])
def test_unknown_outcome_is_sanitized(response):
    b, _ = broker(response)
    with pytest.raises(FeedError) as exc: b.submit(entry())
    assert not isinstance(exc.value, BrokerRejected) and 'fixture-secret' not in str(exc.value)


def test_order_lookup_404_is_unknown_and_cancel_422_is_not_confirmation():
    b, session = broker(Response(404))
    assert b.lookup(entry()['client_order_id']) is None
    session.response = Response(422)
    assert b.cancel('test-order') is None


def test_bad_cancel_identity_cannot_escape_order_path():
    b, session = broker(Response(204))
    with pytest.raises(ValueError): b.cancel('../account')
    assert not session.calls
