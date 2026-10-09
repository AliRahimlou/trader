"""Session-rollover regressions for the declared two-session sweep lifetime.

All prices are synthetic; no external data or broker is used.
"""
from dataclasses import replace
from datetime import datetime, timedelta

from pivot.execution import signal_expiry
from pivot.models import Bar, MAG7, Market
from pivot.strategy import ET, AnalysisPolicy, analyze
from pivot.tests.test_strategy_v2 import leader_market, setup_scenario


def bar(day, hour, minute, opened, high, low, close, minutes=60):
    return Bar(datetime(2026, 9, day, hour, minute, tzinfo=ET), minutes, opened, high, low, close)


def scenario():
    monday = bar(14, 16, 0, 99, 100, 95, 99, 1440)
    tuesday = bar(15, 16, 0, 99.8, 102, 99, 100.4, 1440)
    hours = [bar(15, 14, 30, 99.8, 100, 99.5, 99.9),
             bar(15, 15, 30, 99.9, 100.8, 99.5, 100.4)]
    before = Market('QQQ', {60: hours, 1440: [monday]}, 'alpaca_iex', True, hours[-1].end,
                    previous_session='2026-09-14')
    current = bar(16, 10, 30, 100.4, 100.7, 100.1, 100.4)
    after = replace(before, bars={60: hours + [current], 1440: [monday, tuesday]},
                    observed_at=current.end, previous_session='2026-09-15')
    return before, after


def sweep(result):
    return next(row for row in result['strategies'] if row['id'] == 'prior_day_sweep')


def confirmation(now):
    leaders = {symbol: leader_market(symbol, 'long', at=now) for symbol in MAG7}
    _, _, vix, original = setup_scenario('long')
    vix = replace(vix, observed_at=now,
                  bars={15: [replace(row, end=row.end + (now - original)) for row in vix.bars[15]]})
    return leaders, vix


def test_sweep_keeps_its_identity_and_origin_zone_across_the_session_boundary():
    before, after = scenario()
    origin = sweep(analyze(before, {}, None, before.observed_at))
    carried = sweep(analyze(after, {}, None, after.observed_at))
    assert origin['state'] == carried['state'] == 'CONFIRMING'
    assert carried['event_id'] == origin['event_id']
    assert carried['event_zone'] == origin['event_zone']
    assert carried['event_origin_at'] == carried['event_at'] == before.observed_at.isoformat()
    assert carried['latest_evidence_at'] == after.observed_at.isoformat()
    assert carried['event_expires_at'] == datetime(2026, 9, 16, 16, tzinfo=ET).isoformat()


def test_carried_sweep_requires_fresh_confirmations_and_passes_the_current_signal_contract():
    before, market = scenario()
    now = market.observed_at
    leaders, vix = confirmation(now)
    result = analyze(market, leaders, vix, now)
    assert result['strategy_id'] == 'prior_day_sweep' and result['state'] == 'SETUP_READY'
    assert result['direction'] == 'long' and result['entry'] == market.bars[60][-1].close
    assert result['target'] == 102  # Today's previous-day high, known before this morning's entry.
    assert result['target_zone']['established_at'] == market.bars[1440][-1].end.isoformat()
    assert signal_expiry(result, now) == now + timedelta(minutes=6, seconds=30)
    assert result['can_enter'] is False
    old_leaders, old_vix = confirmation(before.observed_at)
    stale = analyze(market, old_leaders, old_vix, now)
    assert not stale['entry_candidates']
    assert 'Magnificent Seven at their zones' in sweep(stale)['blockers']
    no_vix = analyze(market, leaders, None, now)
    assert not no_vix['entry_candidates']
    assert 'Actual VIX zone reaction' in sweep(no_vix)['blockers']


def test_origin_session_daily_candle_cannot_rewrite_the_origin_zone():
    before, market = scenario()
    expected = sweep(analyze(before, {}, None, before.observed_at))['event_id']
    changed = replace(market.bars[1440][-1], high=150, low=90)
    market.bars[1440][-1] = changed
    assert sweep(analyze(market, {}, None, market.observed_at))['event_id'] == expected


def test_old_prior_day_level_cannot_start_a_new_sweep_in_the_next_session():
    _, market = scenario()
    # Tuesday never swept Monday's high; Wednesday sweeps 100 but not Tuesday's 102.
    market.bars[60][1] = replace(market.bars[60][1], high=100, close=99.9)
    assert sweep(analyze(market, {}, None, market.observed_at))['event_id'] is None


def test_carried_sweep_still_invalidates_on_price_or_missing_hourly_evidence():
    before, market = scenario()
    original = sweep(analyze(before, {}, None, before.observed_at))['event_id']
    market.bars[60][-1] = replace(market.bars[60][-1], low=99.1, close=99.2)
    invalidated = sweep(analyze(market, {}, None, market.observed_at))
    assert invalidated['event_id'] == original and invalidated['state'] == 'INVALIDATED'
    assert 'invalidation' in invalidated['checks'][-1]['detail']
    _, market = scenario()
    market.bars[60][-1] = replace(market.bars[60][-1], end=market.observed_at + timedelta(hours=1))
    market.observed_at = market.bars[60][-1].end
    missing = sweep(analyze(market, {}, None, market.observed_at))
    assert missing['event_id'] == original and missing['state'] == 'INVALIDATED'
    assert 'missing' in missing['checks'][-1]['detail']


def test_previous_session_verification_and_configured_expiry_still_apply():
    _, market = scenario()
    assert sweep(analyze(market, {}, None, market.observed_at, AnalysisPolicy(event_sessions=1)))['event_id'] is None
    market.previous_session = '2026-09-14'
    assert sweep(analyze(market, {}, None, market.observed_at))['event_id'] is None
