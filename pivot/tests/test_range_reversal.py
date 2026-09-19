from datetime import datetime, timedelta, timezone
from copy import deepcopy
from zoneinfo import ZoneInfo

import pytest

from pivot.models import Bar, Market
from pivot.range_reversal import analyze, RULE_VERSION

UTC = timezone.utc
NY = ZoneInfo('America/New_York')


def start(day='2026-09-19'):
    return datetime.fromisoformat(day).replace(tzinfo=NY).astimezone(UTC)


def candle(end, close=100, high=110, low=90, minutes=5):
    return Bar(end, minutes, 100 if low <= 100 <= high else close, high, low, close)


def sample(closes=(), *, day='2026-09-19', prices=None):
    origin = start(day)
    bars = [candle(origin + timedelta(minutes=5 * (i + 1))) for i in range(48)]
    for i, close in enumerate(closes):
        low, high = min(close, 100) - 1, max(close, 100) + 1
        if prices and i in prices:
            low, high = prices[i]
        bars.append(candle(origin + timedelta(minutes=245 + i * 5), close, high, low))
    now = bars[-1].end + timedelta(seconds=10)
    market = Market('BTC/USD', {5: bars}, 'alpaca_crypto_us', True, now)
    provenance = {'symbol': market.symbol, 'source': market.source, 'timeframe_minutes': 5, 'native': True}
    return market, now, provenance


def run(closes=(), **kwargs):
    market, now, provenance = sample(closes, **kwargs)
    return analyze(market, now, provenance=provenance)


@pytest.mark.parametrize('closes,direction,stop,target', [([111, 105], 'short', 112, 91), ([89, 95], 'long', 88, 109)])
def test_outside_then_inside_records_exact_first_candle_stop_and_two_r(closes, direction, stop, target):
    result = run(closes)
    event = result['current_event']
    assert result['state'] == 'SETUP_OBSERVED'
    assert result['signal_ready'] is True and result['execution_status'] == 'signal'
    assert event['direction'] == direction
    assert (event['entry'], event['stop'], event['target']) == (closes[-1], stop, target)
    assert event['reward_to_risk'] == 2
    assert event['breakout_at'] < event['confirmation_at']
    assert event['signal_ready'] is True
    assert event['entry_valid_until'] == result['signal_valid_until']
    assert result['candidates'] == [event]
    assert result['range']['native_candle_count'] == 48
    assert result['rule_version'] == RULE_VERSION
    assert len(result['interpretation_warnings']) >= 6


def test_wick_outside_without_close_does_not_start_event():
    result = run([105, 95], prices={0: (89, 120), 1: (80, 111)})
    assert result['state'] == 'WATCHING'
    assert result['observations'] == result['candidates'] == []


@pytest.mark.parametrize('boundary', [90, 110])
def test_close_on_boundary_is_neither_breakout_nor_reentry(boundary):
    assert run([boundary])['observations'] == []
    result = run([111, boundary])
    assert result['state'] == 'OUTSIDE_RANGE'
    assert result['candidates'] == []


def test_same_side_later_extreme_does_not_move_declared_stop():
    result = run([111, 130, 105])
    assert result['candidates'][0]['stop'] == 112
    assert result['candidates'][0]['target'] == 91
    assert len(result['observations']) == 1


def test_opposite_outside_close_replaces_excursion_without_inventing_reentry():
    result = run([111, 89, 95])
    assert [event['status'] for event in result['observations']] == ['INVALIDATED', 'CONFIRMED']
    assert len(result['candidates']) == 1
    assert result['candidates'][0]['direction'] == 'long'


def test_repeated_fresh_excursions_are_independent_but_polling_is_stable():
    market, now, provenance = sample([111, 105, 111, 105, 89, 95])
    first = analyze(market, now, provenance=provenance)
    second = analyze(market, now + timedelta(seconds=10), provenance=provenance)
    ids = [event['event_id'] for event in first['candidates']]
    assert len(ids) == len(set(ids)) == 3
    assert ids == [event['event_id'] for event in second['candidates']]
    assert [event['current'] for event in first['candidates']] == [False, False, True]


def test_older_signal_remains_history_and_does_not_stay_ready():
    result = run([111, 105, 100])
    assert result['state'] == 'WATCHING'
    assert result['current_event'] is None
    assert len(result['candidates']) == 1 and result['candidates'][0]['current'] is False


def test_unclosed_future_prices_cannot_change_range_or_generate_signal():
    market, now, provenance = sample([111])
    before = analyze(market, now, provenance=provenance)
    market.bars[5].append(candle(market.bars[5][-1].end + timedelta(minutes=5), 100, 10000, 1))
    after = analyze(market, now, provenance=provenance)
    assert after == before


def test_forming_four_hour_range_never_exposes_provisional_levels():
    market, now, provenance = sample()
    market.bars[5] = market.bars[5][:47]
    now = market.bars[5][-1].end + timedelta(seconds=10)
    market.observed_at = now
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'RANGE_FORMING'
    assert result['range'] is None and not result['candidates']


@pytest.mark.parametrize('day,local_end,elapsed_day_hours', [('2026-03-08', 5, 23), ('2026-11-01', 3, 25), ('2026-09-19', 4, 24)])
def test_four_elapsed_hours_and_new_york_day_boundaries_across_dst(day, local_end, elapsed_day_hours):
    result = run([111, 105], day=day)
    session = result['session']
    beginning = datetime.fromisoformat(session['start_at'])
    ending = datetime.fromisoformat(session['end_at'])
    range_end = datetime.fromisoformat(result['range']['end_at'])
    assert (ending - beginning).total_seconds() == elapsed_day_hours * 3600
    assert (range_end - beginning).total_seconds() == 4 * 3600
    assert range_end.astimezone(NY).hour == local_end
    assert result['range']['native_candle_count'] == 48
    assert result['state'] == 'SETUP_OBSERVED'


def test_previous_day_outside_close_cannot_create_today_reversal():
    market, now, provenance = sample([100])
    previous = candle(start() - timedelta(minutes=5), 130, 131, 99)
    market.bars[5].insert(0, previous)
    result = analyze(market, now, provenance=provenance)
    assert result['observations'] == []
    assert result['state'] == 'WATCHING'


def test_new_midnight_resets_previous_pending_event_and_requires_new_range():
    market, _, provenance = sample([111])
    now = start('2026-09-20') + timedelta(seconds=10)
    market.observed_at = now
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'RANGE_FORMING'
    assert result['range'] is None and not result['observations']


@pytest.mark.parametrize('index', [0, 20, 47, 48])
def test_missing_range_or_excursion_candle_blocks_all_current_evidence(index):
    market, now, provenance = sample([111, 105, 100])
    del market.bars[5][index]
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING'
    assert not result['candidates']
    assert 'gap' in result['detail']


@pytest.mark.parametrize('kind', ['duplicate', 'unordered', 'off_grid', 'wrong_frame'])
def test_invalid_timestamp_or_frame_cannot_generate_observation(kind):
    market, now, provenance = sample([111, 105])
    if kind == 'duplicate':
        market.bars[5].insert(5, market.bars[5][5])
    elif kind == 'unordered':
        market.bars[5][0], market.bars[5][1] = market.bars[5][1], market.bars[5][0]
    elif kind == 'off_grid':
        market.bars[5][0] = candle(market.bars[5][0].end + timedelta(seconds=1))
    else:
        market.bars[5][0] = candle(market.bars[5][0].end, minutes=15)
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING' and result['candidates'] == []


@pytest.mark.parametrize('provenance_change', [None, {'native': False}, {'native': 1}, {'timeframe_minutes': 15}, {'source': 'unverified'}, {'symbol': 'QQQ'}])
def test_native_provenance_is_required_and_derived_fifteen_minute_splits_are_not_accepted(provenance_change):
    market, now, provenance = sample([111, 105])
    if provenance_change is None:
        provenance = None
    else:
        provenance.update(provenance_change)
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING' and not result['observations']


def test_fifteen_minute_only_market_cannot_be_used_as_five_minutes():
    market, now, provenance = sample([111, 105])
    market.bars = {15: market.bars[5]}
    assert analyze(market, now, provenance=provenance)['state'] == 'DATA_WAITING'


@pytest.mark.parametrize('offset', [-91, 1])
def test_stale_or_future_receipt_is_not_current(offset):
    market, now, provenance = sample([111, 105])
    market.observed_at = now + timedelta(seconds=offset)
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING' and not result['candidates']


def test_publication_grace_only_allows_latest_completed_candle_to_be_missing():
    market, now, provenance = sample([111])
    now = market.bars[5][-1].end + timedelta(minutes=5, seconds=90)
    market.observed_at = now
    assert analyze(market, now, provenance=provenance)['state'] == 'OUTSIDE_RANGE'
    market.observed_at = now + timedelta(seconds=1)
    assert analyze(market, now + timedelta(seconds=1), provenance=provenance)['state'] == 'DATA_WAITING'


def test_completed_candle_cannot_postdate_its_provider_receipt():
    market, now, provenance = sample([111, 105])
    market.observed_at = market.bars[5][-1].end - timedelta(seconds=1)
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING'
    assert 'provider receipt' in result['detail']


def test_expired_explicit_provider_deadline_blocks_observations():
    market, now, provenance = sample([111, 105])
    market.valid_until = now
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING'
    assert result['candidates'] == []


def test_no_vix_leader_or_optional_trend_requirement_and_no_input_mutation():
    market, now, provenance = sample([111, 105])
    original = deepcopy((market, provenance))
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'SETUP_OBSERVED'
    assert (market, provenance) == original


def test_extreme_short_geometry_cannot_show_a_negative_price_target():
    result = run([200, 100])
    assert result['state'] == 'DATA_WAITING'
    assert result['candidates'] == []
    assert result['current_event']['status'] == 'INVALID_GEOMETRY'


@pytest.mark.parametrize('change', ['not_realtime', 'invalid_source', 'invalid_bar', 'naive_receipt'])
def test_unvalidated_data_never_qualifies_a_signal(change):
    market, now, provenance = sample([111, 105])
    if change == 'not_realtime':
        market.realtime = False
    elif change == 'invalid_source':
        market.source = provenance['source'] = 'unknown'
    elif change == 'invalid_bar':
        object.__setattr__(market.bars[5][-1], 'close', float('nan'))
    else:
        market.observed_at = now.replace(tzinfo=None)
    result = analyze(market, now, provenance=provenance)
    assert result['state'] == 'DATA_WAITING'
    assert result['signal_ready'] is False and result['execution_status'] == 'signal'
    assert result['interpretation_warnings']


def test_missing_market_is_a_visible_data_wait():
    result = analyze(None, start())
    assert result['state'] == 'DATA_WAITING' and result['signal_ready'] is False


def test_naive_analysis_clock_is_rejected():
    with pytest.raises(ValueError, match='timezone'):
        analyze(None, datetime(2026, 9, 19))


def test_fresh_receipt_cannot_extend_confirmation_entry_deadline():
    market, now, provenance = sample([89, 95])
    confirmation = market.bars[5][-1].end
    before = confirmation + timedelta(seconds=89)
    market.observed_at = before
    ready = analyze(market, before, provenance=provenance)
    assert ready['signal_ready'] is True
    assert ready['signal_valid_until'] == (confirmation + timedelta(seconds=90)).isoformat()
    expired = confirmation + timedelta(seconds=90)
    market.observed_at = expired
    result = analyze(market, expired, provenance=provenance)
    assert result['state'] == 'SETUP_OBSERVED'
    assert result['signal_ready'] is False
    assert result['current_event']['signal_ready'] is False
    assert 'expired' in result['detail']
