"""One watched-market registry, with exact source and ownership identities."""
from copy import deepcopy
from pathlib import Path
import ast
import re

import pytest

from pivot.crypto_broker import canonical_symbol as broker_symbol
from pivot.crypto_markets import MARKETS, SYMBOLS
from pivot.crypto_store import CryptoStore
from pivot.feeds import FeedError
from pivot.portfolio import canonical_symbol as portfolio_symbol
from pivot.range_reversal import analyze
from pivot.range_watch import BitcoinBars
from pivot.tests.test_range_watch import Response, Session, provider_input


def test_registry_identifies_the_video_market_without_enabling_adaptations(tmp_path):
    assert tuple(row['symbol'] for row in MARKETS) == SYMBOLS
    assert len(set(SYMBOLS)) == len(SYMBOLS)
    assert {row['symbol'] for row in MARKETS if row['video_market']} == {'BTC/USD'}
    assert all(isinstance(row['name'], str) and row['name'].strip() for row in MARKETS)
    store = CryptoStore(tmp_path/'new-account.sqlite3')
    assert store.control()['symbols'] == ['BTC/USD']
    assert store.control()['enabled'] is False


def test_browser_market_controls_match_the_validated_backend_catalog():
    web = Path(__file__).parents[1] / 'web'
    module = (web/'strategy-families.mjs').read_text()
    declaration = re.search(r'export const CRYPTO_MARKETS=(\[.*?\]);', module)
    assert declaration is not None
    assert tuple(ast.literal_eval(declaration.group(1))) == SYMBOLS
    page = (web/'index.html').read_text()
    for symbol in SYMBOLS:
        assert 'id="range-' + symbol.split('/')[0].lower() + '"' in page


@pytest.mark.parametrize('symbol', SYMBOLS)
def test_each_watched_market_loads_its_own_native_candles_and_signal(symbol):
    payload, expected, now = provider_input()
    payload['bars'] = {symbol: payload['bars']['BTC/USD']}
    session = Session(Response(payload))
    market = BitcoinBars({}, session, symbol=symbol).read(now, clock=lambda:now)
    assert market.symbol == symbol and market.bars == expected.bars
    assert session.calls[0][1]['params']['symbols'] == symbol
    result = analyze(market, now, provenance={
        'source':market.source, 'symbol':symbol, 'native':True, 'timeframe_minutes':5})
    assert result['state'] == 'SETUP_OBSERVED'
    assert result['symbol'] == symbol and result['signal_ready'] is True


@pytest.mark.parametrize('symbol', SYMBOLS)
def test_cross_market_response_cannot_be_used_for_requested_strategy(symbol):
    payload, _, now = provider_input()
    other = next(value for value in SYMBOLS if value != symbol)
    payload['bars'] = {other: payload['bars']['BTC/USD']}
    reader = BitcoinBars({}, Session(Response(payload)), symbol=symbol)
    with pytest.raises(FeedError):
        reader.read(now, clock=lambda:now)


@pytest.mark.parametrize('symbol', SYMBOLS)
def test_pair_and_position_alias_have_one_portfolio_ownership_identity(symbol):
    # Alpaca positions omit the slash; order and data endpoints use the pair.
    position = symbol.replace('/', '')
    assert broker_symbol(position) == portfolio_symbol(position) == symbol
    assert broker_symbol(symbol) == portfolio_symbol(symbol) == symbol


def test_expanding_watched_registry_preserves_saved_trade_selection(tmp_path):
    path = tmp_path/'saved-account.sqlite3'
    store = CryptoStore(path)
    store.configure({'symbols':['ETH/USD'], 'target_dollars':'5.00'})
    before = deepcopy(store.control())
    reopened = CryptoStore(path)
    assert reopened.control() == before
    assert reopened.control()['symbols'] == ['ETH/USD']
    # Explicitly saving all markets remains an independent setting operation.
    reopened.configure({'symbols':list(SYMBOLS)})
    assert reopened.control()['symbols'] == list(SYMBOLS)
    assert reopened.control()['enabled'] is False


@pytest.mark.parametrize('symbol', ['DOGE/USD', 'USDC/USD', 'BTC/USDT', 'QQQ', 'XRPUSD'])
def test_unreviewed_instrument_is_not_accepted_by_native_data_reader(symbol):
    with pytest.raises(ValueError):
        BitcoinBars({}, symbol=symbol)
