"""Historical characterization of frozen v1 Nasdaq interpretation boundaries.

These manufactured prices preserve what the pre-v2 code did, not what the
video creator intended or which trades would be profitable. Current v2
boundaries are tested separately. No broker is instantiated.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo


from pivot.history_health import frame_gaps
from pivot.models import Bar, Market, Zone
from research.baseline_v1 import EXECUTION_POLICY_VERSION as POLICY_VERSION
from research.baseline_v1 import analyze, pivot_event, zones


ET = ZoneInfo('America/New_York')
NOW = datetime(2026, 9, 17, 10, 30, tzinfo=ET)


def candle(end, opened=100, high=105, low=95, close=100, minutes=60):
    return Bar(end, minutes, opened, high, low, close)


def repeated_extrema():
    ranges = [(105, 95), (110, 94), (105, 95), (106, 90), (105, 95),
              (110, 94), (105, 95), (106, 90), (105, 95)]
    return [candle(NOW - timedelta(hours=4 * (14 - i)), high=high, low=low, minutes=240)
            for i, (high, low) in enumerate(ranges)]


def previous_day_market():
    previous_close = (NOW - timedelta(days=1)).replace(hour=16, minute=0)
    daily = candle(previous_close, high=110, low=90, minutes=1440)
    prior = candle(previous_close - timedelta(minutes=30))
    sweep = candle(NOW, 99, 102, 88, 101)
    return Market('QQQ', {1440: [daily], 60: [prior, sweep], 240: []},
                  'alpaca_iex', True, NOW, previous_session='2026-09-16')


def test_repeated_crossings_without_local_extrema_create_no_current_algorithm_area():
    # V3 00:18–00:35 discusses repeated touches OR crossings. The current
    # extrema-only interpretation intentionally represents only a subset.
    bars = [candle(NOW - timedelta(hours=4 * (9 - i)), high=105 + i, low=90 + i, minutes=240)
            for i in range(8)]
    assert all(bar.low < 100 < bar.high for bar in bars)
    assert zones(bars, NOW) == []


def test_previous_day_sweep_expires_when_the_next_closed_hour_is_not_an_event():
    # Neither primary recording specifies this exact confirmation lifetime.
    # It is latest-hour state replacement, not evidence of a stalled worker.
    market = previous_day_market()
    first = analyze(market, {}, None, NOW)
    before_next_close = NOW + timedelta(minutes=59)
    still_current = analyze(replace(market, observed_at=before_next_close), {}, None, before_next_close)
    assert first['event'] == 'previous-day level sweep'
    assert first['state'] == still_current['state'] == 'CONFIRMING'
    assert first['event_at'] == still_current['event_at'] == NOW.isoformat()

    next_close = NOW + timedelta(hours=1)
    next_market = replace(market, observed_at=next_close,
                          bars={**market.bars, 60: market.bars[60] + [candle(next_close, 101, 104, 100, 103)]})
    expired = analyze(next_market, {}, None, next_close)
    assert expired['event_at'] is None
    assert expired['state'] == 'WATCHING'
    assert expired['checks'][-1]['name'] == 'Nasdaq level event'
    assert expired['checks'][-1]['passed'] is False


def test_four_hour_event_hides_an_overlapping_previous_day_sweep_in_current_single_branch_result():
    # Both clips' initial-location variants can qualify together. The current
    # analyzer uses a fallback priority, not independent source attribution.
    market = previous_day_market()
    hours = [candle(NOW - timedelta(hours=2), 99, 105, 98, 100),
             candle(NOW - timedelta(hours=1), 100, 109, 88, 100),
             candle(NOW, 100, 112, 89, 111)]
    market.bars.update({240: repeated_extrema(), 60: hours})
    selected = analyze(market, {}, None, NOW)
    independently_scoped = analyze(replace(market, bars={**market.bars, 240: []}), {}, None, NOW)
    assert selected['event'] == 'sweep and reclaim'
    assert independently_scoped['event'] == 'previous-day level sweep'
    assert selected['event_at'] == independently_scoped['event_at']


def test_frozen_v1_retests_of_one_break_had_different_order_identities():
    # The execution key is POLICY_VERSION|QQQ|event_at|direction. Current
    # event_at means the reaction candle, not the original break or zone.
    # This permits a fresh intent after a later retest; it is not itself an
    # observed duplicate order or an explanation for today's lack of trades.
    zone = Zone(99.9, 100.1, NOW - timedelta(days=30), 'fixture repeated pivot')
    bars = [candle(NOW - timedelta(hours=2), 99, 100, 98, 99),
            candle(NOW - timedelta(hours=1), 99, 103, 98, 102),
            candle(NOW, 101, 104, 100, 103)]
    first = pivot_event(bars, [zone])
    second = pivot_event(bars + [candle(NOW + timedelta(hours=1), 102, 105, 100, 104)], [zone])
    assert first[2] == second[2] == 'break and retest'
    assert first[1] == second[1] == zone
    assert first[3].end != second[3].end
    identity = lambda event: sha256(f'{POLICY_VERSION}|QQQ|{event[3].end.isoformat()}|long'.encode()).hexdigest()[:24]
    assert identity(first) != identity(second)
    # v2 executor dedup is tested independently; old v1 admission is not replayed.


def test_break_retest_has_no_originating_break_age_or_session_reset_limit():
    # Unlike the latest reaction's freshness check, the originating break can
    # be much older. The recordings do not specify a numerical maximum age.
    start = datetime(2026, 9, 9, 9, 30, tzinfo=ET)
    sessions, bars = {}, []
    for offset in range(8):
        opened = start + timedelta(days=offset)
        if opened.weekday() >= 5:
            continue
        sessions[opened.date().isoformat()] = {'open': opened, 'close': opened.replace(hour=16, minute=0)}
        for hour in range(1, 7):
            bars.append(candle(opened + timedelta(hours=hour), 103, 105, 102, 104))
    bars[0] = candle(bars[0].end, 99, 100, 98, 99)
    bars[1] = candle(bars[1].end, 99, 103, 98, 102)
    bars[-1] = candle(bars[-1].end, 101, 104, 100, 103)
    zone = Zone(99.9, 100.1, start - timedelta(days=30), 'fixture repeated pivot')
    # Every expected regular-session full hour is present; missing data is
    # not the reason the helper accepts this 172-hour-old originating break.
    assert frame_gaps(bars, sessions, 60, bars[-1].end + timedelta(seconds=90))['missing_count'] == 0
    result = pivot_event(bars, [zone])
    assert result[2] == 'break and retest'
    assert result[3].end == bars[-1].end
    assert (result[3].end - bars[1].end).total_seconds() / 3600 == 172
