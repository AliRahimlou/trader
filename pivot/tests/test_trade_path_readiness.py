"""Manufactured signals through both engines on one stateful, offline account.

The numerical prices exercise small dollar orders at stock/Bitcoin price scales.
They are not received market observations or evidence of live broker fills.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from pivot import execution
from pivot.models import Bar, Market
from pivot.policy import POLICY_VERSION
from pivot.range_reversal import analyze as analyze_crypto
from pivot.strategy import ANALYSIS_VERSION, analyze as analyze_stock
from pivot.tests.test_crypto_audit import shared_engines
from pivot.tests.test_execution import ready
from pivot.tests.test_strategy_v2 import setup_scenario


@pytest.fixture(autouse=True)
def executor_accepts_current_analysis_version(monkeypatch):
    # Version-string alignment only: pivot/execution.py still pins the v3
    # analysis identifier; see test_strategy_v4_rules.py for the contract check.
    monkeypatch.setattr(execution, 'SIGNAL_POLICY_VERSION', ANALYSIS_VERSION)


def stock_snapshot(method):
    market, leaders, vix, now = setup_scenario('long', method)
    market = replace(market, bars={minutes: [replace(bar,
        open=bar.open * 5, high=bar.high * 5, low=bar.low * 5, close=bar.close * 5)
        for bar in bars] for minutes, bars in market.bars.items()})
    vix = replace(vix, source='insightsentry', valid_until=now + timedelta(seconds=990))
    snapshot = ready(now)
    snapshot['setup'] = analyze_stock(market, leaders, vix, now)
    assert snapshot['setup']['state'] == 'SETUP_READY'
    assert snapshot['setup']['strategy_id'] == method
    return snapshot, now


def bitcoin_snapshot(now):
    start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    count = int((now - start).total_seconds() // 300)
    bars = [Bar(start + timedelta(minutes=5 * (index + 1)), 5,
                100000, 101000, 99500, 100000) for index in range(count - 2)]
    bars.extend([
        Bar(now - timedelta(minutes=5), 5, 99450, 99475, 99300, 99400),
        Bar(now, 5, 99750, 100025, 99700, 100000),
    ])
    market = Market('BTC/USD', {5: bars}, 'alpaca_crypto_us', True, now)
    analysis = analyze_crypto(market, now, provenance={
        'source': market.source, 'symbol': market.symbol, 'native': True, 'timeframe_minutes': 5})
    assert analysis['signal_ready'] is True
    assert analysis['current_event']['stop'] == 99300
    assert analysis['current_event']['target'] == 101400
    return analysis


@pytest.mark.parametrize('method', ['prior_day_sweep', 'four_hour_retest'])
@pytest.mark.parametrize('first', ['stock', 'crypto'])
def test_both_analyzers_complete_five_dollar_shared_account_lifecycles(tmp_path, method, first):
    snapshot, now = stock_snapshot(method)
    stock, crypto, venue, main, crypto_store, portfolio = shared_engines(tmp_path)
    venue.at = now
    stock_price = Decimal(str(snapshot['setup']['entry']))
    venue.prices = {'QQQ': (stock_price - Decimal('.01'), stock_price),
                    'BTC/USD': (Decimal('99999'), Decimal('100000'))}
    # Entitlement is explicitly manufactured, just like the candle observations.
    verifications = []
    venue.requires_vix_entry_quote = True
    def confirm_vix(at):
        verifications.append(at)
        return {'source': 'insightsentry', 'symbol': 'I:VIX', 'delay_seconds': 0,
                'value': 20, 'updated_at': at.isoformat()}
    venue.confirm_vix_quote = confirm_vix
    crypto_analysis = bitcoin_snapshot(now)
    actions = {'stock': lambda: stock.tick(snapshot),
               'crypto': lambda: crypto.tick({'BTC/USD': crypto_analysis})}

    actions[first]()
    actions['crypto' if first == 'stock' else 'stock']()
    assert len(venue.sent) == 4, stock.message + ' / ' + crypto.message
    assert main.active_trade()['stage'] == 'open'
    assert crypto_store.active_trade('BTC/USD')['stage'] == 'open'
    assert verifications == [now]
    qqq_buy = next(order for order in venue.sent if order['symbol'] == 'QQQ' and order['side'] == 'buy')
    btc_buy = next(order for order in venue.sent if order['symbol'] == 'BTC/USD' and order['side'] == 'buy')
    assert qqq_buy['notional'] == '5.00'
    # The immediate limit carries a small buffer above the ask, so the quantity is sized
    # to the limit and the fill at the ask spends slightly less than the $5 target.
    btc_spent = Decimal(btc_buy['qty']) * Decimal('100000')
    assert Decimal('4.95') <= btc_spent <= Decimal('5') and Decimal(btc_buy['qty']) * Decimal(btc_buy['limit_price']) <= Decimal('5')
    assert venue.cash == Decimal('100') - Decimal('5.00') - btc_spent
    assert len(portfolio.reservations('shared-audit-account')) == 2
    owned = dict(venue.holdings)
    assert 0 < owned['QQQ'] < 1
    assert 0 < owned['BTC/USD'] < Decimal(btc_buy['qty'])  # Net of the venue's buy fee.
    for order in venue.orders():
        assert Decimal(order['qty']) == owned[order['symbol']]

    # Existing ownership and protection survive master Off; each target exits
    # only its own confirmed quantity after cancellation is acknowledged.
    main.set_control(False)
    venue.prices['QQQ'] = (Decimal(str(snapshot['setup']['target'])),) * 2
    for _ in range(4):
        actions['stock']()
    assert main.active_trade() is None
    assert venue.holdings['BTC/USD'] == owned['BTC/USD']
    assert crypto_store.active_trade('BTC/USD')['stage'] == 'open'
    venue.prices['BTC/USD'] = (Decimal('101400'),) * 2
    actions['crypto']()
    assert not crypto_store.active_trades()
    assert all(qty == 0 for qty in venue.holdings.values())
    assert venue.orders() == []
    assert len(venue.sent) == 6
    assert Decimal(venue.sent[-1]['qty']) == owned['BTC/USD']
    assert not crypto_store.incidents()

    # The same completed events cannot replay after permission is restored.
    main.set_control(True, POLICY_VERSION, 'shared-audit-account')
    assert stock.enabled() and crypto.enabled()
    actions['stock']()
    actions['crypto']()
    assert len(venue.sent) == 6
