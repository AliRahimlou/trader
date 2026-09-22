"""Net-expectancy cost gate and the gap-tolerant range contract (range-spot-execution-v2).

Realistic Bitcoin scale, fake broker, no network. The 7% stop geometry in
test_crypto_execution passes any cost gate; these fixtures use stop distances
the September 2026 replay actually produced (median 0.12%).
"""
from datetime import timedelta
from decimal import Decimal, ROUND_UP

import pytest

from pivot.crypto_execution import (EXIT_ALLOWANCE, FEE, IOC_LIMIT_BUFFER, MIN_NET_REWARD_RISK, POLICY, POLICY_VERSION,
                                    checked_signal, minimum_stop_distance, net_expectancy, percent, rounded)
from pivot.models import Bar, Market
from pivot.range_reversal import OPENING_BARS_MINIMUM, RULE_VERSION, analyze
from pivot.tests.test_crypto_execution import NOW, engine, signal  # noqa: F401 (fixture)

D = Decimal
BTC_ENTRY = D('81500')
TICK = D('.000000001')


def bitcoin_signal(stop_fraction, at=NOW):
    """Opening range 81,450-82,000; a close below, then a buy confirmation at 81,500."""
    origin = at.replace(hour=4, minute=0, second=0, microsecond=0)
    stop = BTC_ENTRY * (1 - stop_fraction)
    bars = [Bar(origin + timedelta(minutes=5 * (i + 1)), 5, 81700, 82000, 81450, 81700) for i in range(48)]
    bars += [Bar(origin + timedelta(minutes=245), 5, 81460, 81470, float(stop), 81430),
             Bar(origin + timedelta(minutes=250), 5, 81440, 81520, 81420, float(BTC_ENTRY))]
    market = Market('BTC/USD', {5: bars}, 'alpaca_crypto_us', True, at)
    analysis = analyze(market, at, provenance={'source': market.source, 'symbol': 'BTC/USD', 'native': True, 'timeframe_minutes': 5})
    assert analysis['signal_ready'] and D(str(analysis['current_event']['stop'])) == stop
    return analysis


def geometry(stop_fraction, ask=BTC_ENTRY):
    limit = rounded(ask * (1 + IOC_LIMIT_BUFFER), TICK, ROUND_UP)
    stop = BTC_ENTRY * (1 - stop_fraction)
    return limit, stop, BTC_ENTRY + 2 * (BTC_ENTRY - stop)


def independent_gate(limit, stop, target):
    kept = (1 - FEE) * (1 - FEE)
    return target / limit * kept - 1, 1 - stop * (1 - EXIT_ALLOWANCE) / limit * kept


def prepare(engine_parts):
    e, b, store, _, _ = engine_parts
    store.configure({'target_dollars': '15.00'})
    b.bid, b.ask = D('81499'), BTC_ENTRY
    return e, b, store


@pytest.mark.parametrize('stop_fraction,admitted', [(D('.001'), False), (D('.005'), False), (D('.012'), True)])
def test_realistic_bitcoin_stop_distances_against_the_net_expectancy_gate(engine, stop_fraction, admitted):
    e, b, store = prepare(engine)
    e.tick({'BTC/USD': bitcoin_signal(stop_fraction)})
    limit, stop, target = geometry(stop_fraction)
    win, loss = net_expectancy(limit, stop, target)
    assert (win, loss) == independent_gate(limit, stop, target)
    if admitted:
        assert win > 0 and win >= MIN_NET_REWARD_RISK * loss
        assert [row['type'] for row in b.sent] == ['limit', 'stop_limit'], e.message
        assert D(b.sent[0]['limit_price']) == limit
        assert D('14.85') <= D(b.sent[0]['qty']) * limit <= 15
        assert store.active_trade('BTC/USD')['stage'] == 'open'
        return
    assert win <= 0 or win < MIN_NET_REWARD_RISK * loss
    assert not b.sent and not store.active_trades()
    message = e.market_messages['BTC/USD']
    assert message.startswith('Skipped: net reward after fees')
    assert f'{percent(win)}% versus net risk {percent(loss)}% of the {limit} limit' in message
    assert f'the stop is {percent(stop_fraction)}% below entry' in message
    implied = minimum_stop_distance(BTC_ENTRY, limit)
    assert D('.0116') < implied < D('.0117')
    assert f'needs at least {percent(implied)}% at this ratio' in message
    assert 'need reward ≥ 1.0 × risk' in message
    # The recorded live check carries the same numbers for the owner's review.
    latest = store.decision_review(NOW)['latest_by_symbol']['BTC/USD']
    assert latest['reason'] == message
    assert latest['outcome'] == 'waiting' and latest['signal_fresh'] is True and latest['direction'] == 'long'


def test_net_expectancy_numbers_for_the_three_reference_stops():
    rows = {}
    for fraction in (D('.001'), D('.005'), D('.012')):
        win, loss = net_expectancy(*geometry(fraction))
        rows[fraction] = (percent(win), percent(loss))
    assert rows[D('.001')] == ('-0.33', '0.73')  # A 2R target 0.2% away cannot cover both taker fees.
    assert rows[D('.005')] == ('0.47', '1.13')   # Positive, but the net reward is below the net risk.
    assert rows[D('.012')] == ('1.86', '1.82')   # Net reward at least the net risk.
    limit = geometry(D('.012'))[0]
    assert percent(minimum_stop_distance(BTC_ENTRY, limit)) == '1.16'
    assert minimum_stop_distance(BTC_ENTRY, BTC_ENTRY) < minimum_stop_distance(BTC_ENTRY, limit)
    assert minimum_stop_distance(BTC_ENTRY, BTC_ENTRY * 2) > D('.5')


@pytest.mark.parametrize('ratio,stop_fraction,admitted', [(D('0'), D('.005'), True), (D('2.5'), D('.012'), False)])
def test_reward_risk_floor_constant_is_honoured(engine, monkeypatch, ratio, stop_fraction, admitted):
    e, b, store = prepare(engine)
    monkeypatch.setattr('pivot.crypto_execution.MIN_NET_REWARD_RISK', ratio)
    e.tick({'BTC/USD': bitcoin_signal(stop_fraction)})
    if admitted:
        assert [row['type'] for row in b.sent] == ['limit', 'stop_limit'], e.message
    else:
        assert minimum_stop_distance(BTC_ENTRY, BTC_ENTRY) is None  # No 2R distance satisfies 2.5x after fees.
        assert not b.sent and 'unattainable distance' in e.market_messages['BTC/USD']


def test_negative_net_reward_is_reported_even_when_the_ratio_would_pass(monkeypatch):
    monkeypatch.setattr('pivot.crypto_execution.MIN_NET_REWARD_RISK', D('0'))
    win, loss = net_expectancy(*geometry(D('.001')))
    assert win < 0 < loss


def test_old_rule_version_is_refused_by_the_execution_contract():
    analysis = signal()
    assert RULE_VERSION == 'range-reversal-v2' and analysis['rule_version'] == RULE_VERSION
    for stale in ('range-reversal-v1', 'range-reversal-v3'):
        forged = signal(); forged['rule_version'] = stale; forged['current_event']['rule_version'] = stale
        with pytest.raises(Exception, match='Waiting for a current, complete'):
            checked_signal(forged, 'BTC/USD', NOW)


@pytest.mark.parametrize('count,accepted', [(43, False), (44, True), (47, True), (48, True), (49, False)])
def test_opening_candle_count_contract(engine, count, accepted):
    e, b, store, _, _ = engine
    analysis = signal(); analysis['range']['native_candle_count'] = count
    assert OPENING_BARS_MINIMUM == 44
    e.tick({'BTC/USD': analysis})
    assert bool(b.sent) is accepted, e.message


def test_missing_opening_candle_in_native_bars_still_produces_an_executable_signal(engine):
    e, b, store, _, _ = engine
    origin = NOW.replace(hour=4, minute=0, second=0, microsecond=0)
    bars = [Bar(origin + timedelta(minutes=5 * (i + 1)), 5, 100, 110, 90, 100) for i in range(48) if i != 17]
    bars += [Bar(origin + timedelta(minutes=245), 5, 89, 90, 88, 89), Bar(origin + timedelta(minutes=250), 5, 95, 96, 94, 95)]
    market = Market('BTC/USD', {5: bars}, 'alpaca_crypto_us', True, NOW)
    analysis = analyze(market, NOW, provenance={'source': market.source, 'symbol': 'BTC/USD', 'native': True, 'timeframe_minutes': 5})
    assert analysis['signal_ready'] and analysis['range']['native_candle_count'] == 47
    e.tick({'BTC/USD': analysis})
    assert len(b.sent) == 2, e.message
    assert store.active_trade('BTC/USD')['signal']['range']['native_candle_count'] == 47


def test_policy_text_describes_the_v2_rules():
    assert POLICY_VERSION == POLICY['version'] == 'range-spot-execution-v2'
    text = ' '.join(POLICY['summary'])
    assert 'at least 44' in text and 'Missing five-minute candles are tolerated' in text
    assert 'net reward after both taker fees' in text and 'at least the net risk' in text
    assert 'own allowance of two entry attempts per New York session' in text
