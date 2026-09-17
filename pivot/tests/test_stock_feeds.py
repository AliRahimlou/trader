"""Stock feed contract and cache checks; all provider access uses local fixtures."""
from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from pivot.feeds import ReadOnlyFeeds, FeedError, ET, daily, resample
from pivot.models import Bar, MAG7, timestamp
from pivot.strategy import prior_day_zones

SYMBOLS = ('QQQ', *MAG7)
NOW = datetime(2026, 9, 16, 12, 32, tzinfo=ET)
CALENDAR = [
    {'date': '2026-09-14', 'open': '09:30', 'close': '16:00'},
    {'date': '2026-09-15', 'open': '09:30', 'close': '16:00'},
    {'date': '2026-09-16', 'open': '09:30', 'close': '16:00'},
]


def raw_bar(at, price=100):
    return {'t': at.isoformat(), 'o': price, 'h': price + 1, 'l': price - 1,
            'c': price, 'v': 10, 'vw': price}


def source_bars(calendar, minutes=15):
    result = []
    for session in calendar:
        at = datetime.fromisoformat(session['date'] + 'T' + session['open']).replace(tzinfo=ET)
        end = datetime.fromisoformat(session['date'] + 'T' + session['close']).replace(tzinfo=ET)
        while at < end:
            result.append(raw_bar(at))
            at += timedelta(minutes=minutes)
    return result


class StockFixture(ReadOnlyFeeds):
    def __init__(self, calendar=None):
        super().__init__({})
        self.calendar = deepcopy(calendar or CALENDAR)
        self.data = {symbol: source_bars(self.calendar) for symbol in SYMBOLS}
        self.data5 = {symbol: source_bars(self.calendar, 5) for symbol in MAG7}
        self.calls = []
        self.fail = False
        self.fail_minutes = None

    def get(self, provider, path, params=None):
        self.calls.append((provider, path, deepcopy(params)))
        if path == '/v2/calendar':
            return [row for row in self.calendar if params['start'] <= row['date'] <= params['end']]
        assert provider == 'stocks' and path == '/v2/stocks/bars'
        if self.fail or self.fail_minutes == int(params['timeframe'].removesuffix('Min')):
            raise FeedError('Simulated stock outage')
        start, end = timestamp(params['start']), timestamp(params['end'])
        data = self.data5 if params['timeframe'] == '5Min' else self.data
        wanted = set(params['symbols'].split(','))
        return {'bars': {symbol: [deepcopy(b) for b in bars if start <= timestamp(b['t']) <= end]
                         for symbol, bars in data.items() if symbol in wanted}, 'next_page_token': None}

    @property
    def stock_calls(self):
        return [call[2] for call in self.calls if call[1] == '/v2/stocks/bars' and call[2]['timeframe'] == '15Min']

    @property
    def leader_calls(self):
        return [call[2] for call in self.calls if call[1] == '/v2/stocks/bars' and call[2]['timeframe'] == '5Min']


class Pages(ReadOnlyFeeds):
    def __init__(self, pages):
        super().__init__({})
        self.pages = iter(pages)

    def get(self, *args, **kwargs):
        return next(self.pages)


def fetch_pages(pages):
    return Pages(pages).stock_bars(['QQQ'], 15, NOW-timedelta(days=1), NOW)


def test_calendar_covers_entire_history_and_ongoing_candle_is_removed():
    feed = StockFixture()
    markets = feed.stocks(NOW)
    calendar_call = next(c[2] for c in feed.calls if c[1] == '/v2/calendar')
    assert calendar_call['start'] == '2026-07-18'
    assert calendar_call['end'] == '2026-09-16'
    assert set(markets) == set(SYMBOLS)
    for market in markets.values():
        assert market.bars[15][-1].end == NOW.replace(minute=30)
        assert all(bar.end <= NOW for bars in market.bars.values() for bar in bars)
        assert market.previous_session == '2026-09-15'
    assert feed.stock_refresh_mode == 'full'
    assert feed.stock_last_full_at == NOW
    assert feed.stock_fetch_seconds >= 0
    assert feed.stock_sessions['2026-09-16']['close'].hour == 16


def test_incremental_fetch_replaces_previous_and_current_session_and_preserves_older_data():
    feed = StockFixture()
    first = feed.stocks(NOW)
    corrected = datetime(2026, 9, 15, 10, 0, tzinfo=ET)
    for rows in feed.data.values():
        rows[:] = [raw_bar(timestamp(b['t']), 200) if timestamp(b['t']) == corrected else b for b in rows]
    second = feed.stocks(NOW + timedelta(minutes=1))
    assert timestamp(feed.stock_calls[-1]['start']) == datetime(2026, 9, 15, 9, 30, tzinfo=ET)
    assert len([c for c in feed.calls if c[1] == '/v2/calendar']) == 1
    assert second['QQQ'].bars[15][0] == first['QQQ'].bars[15][0]
    assert next(b for b in second['QQQ'].bars[15] if b.end == corrected + timedelta(minutes=15)).close == 200
    assert feed.stock_refresh_mode == 'incremental'
    assert feed.stock_last_full_at == NOW


def test_hourly_full_refresh_incorporates_older_corrections():
    feed = StockFixture()
    feed.stocks(NOW)
    feed.data['QQQ'][0] = raw_bar(timestamp(feed.data['QQQ'][0]['t']), 300)
    result = feed.stocks(NOW + timedelta(hours=1))
    assert timestamp(feed.stock_calls[-1]['start']).date().isoformat() == '2026-07-18'
    assert result['QQQ'].bars[15][0].close == 300
    assert feed.stock_refresh_mode == 'full'
    assert feed.stock_last_full_at == NOW + timedelta(hours=1)


def test_failed_or_missing_symbol_refresh_does_not_change_cache_or_claim_success():
    feed = StockFixture()
    feed.stocks(NOW)
    before = deepcopy(feed._stock_cache)
    feed.fail = True
    with pytest.raises(FeedError, match='outage'):
        feed.stocks(NOW + timedelta(minutes=1))
    assert feed._stock_cache == before
    feed.fail = False
    feed.data.pop('TSLA')
    with pytest.raises(FeedError, match='TSLA'):
        feed.stocks(NOW + timedelta(minutes=2))
    assert feed._stock_cache == before


def test_removed_overlap_candle_is_not_filled_from_cache():
    feed = StockFixture()
    feed.stocks(NOW)
    missing = datetime(2026, 9, 15, 10, 0, tzinfo=ET)
    feed.data['QQQ'] = [b for b in feed.data['QQQ'] if timestamp(b['t']) != missing]
    result = feed.stocks(NOW + timedelta(minutes=1))
    assert missing + timedelta(minutes=15) not in [b.end for b in result['QQQ'].bars[15]]
    assert '2026-09-15' not in [b.end.date().isoformat() for b in result['QQQ'].bars[1440]]


def test_day_change_refreshes_calendar_and_uses_previous_trading_session():
    friday = {'date': '2026-11-27', 'open': '09:30', 'close': '13:00'}
    monday = {'date': '2026-11-30', 'open': '09:30', 'close': '16:00'}
    feed = StockFixture([friday, monday])
    before = datetime(2026, 11, 29, 23, 59, tzinfo=ET)
    feed.stocks(before)
    result = feed.stocks(before + timedelta(minutes=2))
    assert len([c for c in feed.calls if c[1] == '/v2/calendar']) == 2
    assert timestamp(feed.stock_calls[-1]['start']) == datetime(2026, 11, 27, 9, 30, tzinfo=ET)
    assert result['QQQ'].previous_session == '2026-11-27'


def test_holidays_after_close_and_early_close_daily_extremes():
    calendar = [
        {'date': '2026-11-25', 'open': '09:30', 'close': '16:00'},
        {'date': '2026-11-27', 'open': '09:30', 'close': '13:00'},
        {'date': '2026-11-30', 'open': '09:30', 'close': '16:00'},
    ]
    feed = StockFixture(calendar)
    for rows in feed.data.values():
        rows.extend([raw_bar(datetime(2026, 11, 26, 10, tzinfo=ET)),
                     raw_bar(datetime(2026, 11, 27, 14, tzinfo=ET))])
        rows.sort(key=lambda b: timestamp(b['t']))
    result = feed.stocks(datetime(2026, 11, 30, 10, 32, tzinfo=ET))
    market = result['QQQ']
    early = [b for b in market.bars[15] if b.end.date().isoformat() == '2026-11-27']
    assert len(early) == 14
    assert not any(b.end.date().isoformat() == '2026-11-26' for b in market.bars[15])
    day = next(b for b in market.bars[1440] if b.end.date().isoformat() == '2026-11-27')
    assert day.end.hour == 13
    assert len([b for b in market.bars[60] if b.end.date().isoformat() == '2026-11-27']) == 3
    assert not [b for b in market.bars[240] if b.end.date().isoformat() == '2026-11-27']
    levels = prior_day_zones(market, datetime(2026, 11, 30, 10, 32, tzinfo=ET))
    assert [z.source for z in levels] == ['previous-day high', 'previous-day low']
    assert all(z.established_at == day.end for z in levels)


def test_full_buckets_policy_and_missing_quarter_remain_unchanged():
    feed = StockFixture()
    result = feed.stocks(NOW)
    session = [b for b in result['QQQ'].bars[15] if b.end.date().isoformat() == '2026-09-15']
    assert len(resample(session, 60, feed.stock_sessions)) == 6
    assert len(resample(session, 240, feed.stock_sessions)) == 1
    assert len(daily(session, feed.stock_sessions)) == 1
    assert not daily(session[:-1], feed.stock_sessions)


@pytest.mark.parametrize('raw', [None, [], {}, {'bars': None}, {'bars': []}, {'bars': {'QQQ': None}},
                                      {'bars': {'BAD': []}}, {'bars': {}}, {'bars': {'QQQ': []}}])
def test_missing_or_malformed_payload_fails_closed(raw):
    with pytest.raises(FeedError):
        fetch_pages([raw])


@pytest.mark.parametrize('times', [
    [NOW.replace(minute=0), NOW.replace(minute=0)],
    [NOW.replace(minute=15), NOW.replace(minute=0)],
    [NOW.replace(minute=7)],
])
def test_duplicate_out_of_order_and_unaligned_bars_are_rejected(times):
    with pytest.raises(FeedError):
        fetch_pages([{'bars': {'QQQ': [raw_bar(t) for t in times]}}])


def test_duplicate_across_pages_and_repeated_token_are_rejected():
    bar = raw_bar(NOW.replace(minute=0))
    with pytest.raises(FeedError, match='duplicated'):
        fetch_pages([{'bars': {'QQQ': [bar]}, 'next_page_token': 'next'}, {'bars': {'QQQ': [bar]}}])
    with pytest.raises(FeedError, match='Repeated'):
        fetch_pages([{'bars': {'QQQ': [bar]}, 'next_page_token': 'same'}, {'bars': {}, 'next_page_token': 'same'}])


@pytest.mark.parametrize('change', [lambda b: b.pop('c'), lambda b: b.update(t='2026-09-16T12:00:00'),
                                  lambda b: b.update(c=float('nan')), lambda b: b.update(v=-1)])
def test_invalid_candle_never_enters_cache(change):
    bar = raw_bar(NOW.replace(minute=0))
    change(bar)
    with pytest.raises(FeedError, match='invalid candle'):
        fetch_pages([{'bars': {'QQQ': [bar]}}])


@pytest.mark.parametrize('raw', [None, {}, [], [{'date': '2026-09-16'}],
                               [{'date': '2026-09-16', 'open': '16:00', 'close': '09:30'}]])
def test_missing_or_invalid_calendar_fails(raw):
    feed = Pages([raw])
    with pytest.raises(FeedError):
        feed.stock_calendar(NOW-timedelta(days=60), NOW)


def test_invalid_pagination_token_and_exhausted_history_fail_closed():
    with pytest.raises(FeedError, match='Invalid stock pagination'):
        fetch_pages([{'bars': {}, 'next_page_token': 123}])
    with pytest.raises(FeedError, match='truncated'):
        fetch_pages([{'bars': {}, 'next_page_token': str(i)} for i in range(64)])


def test_calendar_and_bars_keep_exchange_timezone_across_dst():
    calendar = [
        {'date': '2026-10-30', 'open': '09:30', 'close': '16:00'},
        {'date': '2026-11-02', 'open': '09:30', 'close': '16:00'},
    ]
    feed = StockFixture(calendar)
    markets = feed.stocks(datetime(2026, 11, 2, 12, 32, tzinfo=ET))
    assert feed.stock_sessions['2026-10-30']['open'].utcoffset() == timedelta(hours=-4)
    assert feed.stock_sessions['2026-11-02']['open'].utcoffset() == timedelta(hours=-5)
    hours = markets['QQQ'].bars[60]
    assert all(b.end.minute == 30 for b in hours)
    assert markets['QQQ'].previous_session == '2026-10-30'


def test_native_five_minute_leaders_are_distinct_from_fifteen_minute_context():
    feed = StockFixture()
    original = feed.data5['AAPL'][0]
    feed.data5['AAPL'][0] = raw_bar(timestamp(original['t']), 175)
    markets = feed.stocks(NOW)
    assert 5 not in markets['QQQ'].bars
    assert set(feed.leader_calls[0]['symbols'].split(',')) == set(MAG7)
    assert set(feed.stock_calls[0]['symbols'].split(',')) == set(SYMBOLS)
    for symbol in MAG7:
        bars = markets[symbol].bars[5]
        assert all(b.minutes == 5 and b.end <= NOW for b in bars)
        assert bars[-1].end == NOW.replace(minute=30)
        assert len(bars) == 3 * len(markets[symbol].bars[15])
    assert markets['AAPL'].bars[5][0].close == 175
    assert markets['AAPL'].bars[15][0].close == 100


def longer_calendar():
    # Labor Day is absent; these are actual fixture trading sessions.
    return [{'date': '2026-09-' + day, 'open': '09:30', 'close': '16:00'}
            for day in ('03', '04', '08', '09', '10', '11', '14', '15', '16')]


def test_five_minute_window_is_seven_actual_sessions_not_sixty_days():
    feed = StockFixture(longer_calendar())
    markets = feed.stocks(NOW)
    assert len(feed.stock_sessions) == 9 and len(feed.stock_leader_sessions) == 7
    assert timestamp(feed.leader_calls[0]['start']) == datetime(2026, 9, 8, 9, 30, tzinfo=ET)
    assert markets['AAPL'].bars[5][0].end == datetime(2026, 9, 8, 9, 35, tzinfo=ET)
    assert markets['AAPL'].bars[15][0].end == datetime(2026, 9, 3, 9, 45, tzinfo=ET)
    assert markets['AAPL'].bars[5][-1].end == NOW.replace(minute=30)


def test_both_histories_publish_atomically_when_five_minute_fetch_fails():
    feed = StockFixture()
    feed.stocks(NOW)
    before = deepcopy(feed._stock_cache)
    sessions_before = deepcopy(feed.stock_leader_sessions)
    feed.data['QQQ'][-30] = raw_bar(timestamp(feed.data['QQQ'][-30]['t']), 200)
    feed.fail_minutes = 5
    with pytest.raises(FeedError, match='outage'):
        feed.stocks(NOW + timedelta(minutes=1))
    assert feed._stock_cache == before
    assert feed.stock_leader_sessions == sessions_before
    assert feed.stock_last_full_at == NOW
    assert feed.stock_refresh_mode == 'full'


def test_five_minute_missing_symbol_and_bad_alignment_reject_the_whole_refresh():
    feed = StockFixture()
    feed.data5.pop('MSFT')
    with pytest.raises(FeedError, match='5-minute.*MSFT'):
        feed.stocks(NOW)
    assert not hasattr(feed, '_stock_cache')
    feed = StockFixture()
    first = timestamp(feed.data5['MSFT'][0]['t'])
    feed.data5['MSFT'][0] = raw_bar(first + timedelta(minutes=1))
    with pytest.raises(FeedError, match='aligned'):
        feed.stocks(NOW)
    assert not hasattr(feed, '_stock_cache')


def test_five_minute_overlap_corrections_and_hourly_refresh_update_actual_history():
    feed = StockFixture(longer_calendar())
    feed.stocks(NOW)
    recent = datetime(2026, 9, 15, 10, tzinfo=ET)
    old = datetime(2026, 9, 8, 10, tzinfo=ET)
    feed.data5['AAPL'] = [raw_bar(timestamp(b['t']), 200) if timestamp(b['t']) in (recent, old) else b
                          for b in feed.data5['AAPL']]
    increment = feed.stocks(NOW + timedelta(minutes=1))
    assert timestamp(feed.leader_calls[-1]['start']) == datetime(2026, 9, 15, 9, 30, tzinfo=ET)
    values = {b.end: b.close for b in increment['AAPL'].bars[5]}
    assert values[recent + timedelta(minutes=5)] == 200
    assert values[old + timedelta(minutes=5)] == 100
    refreshed = feed.stocks(NOW + timedelta(hours=1))
    assert next(b for b in refreshed['AAPL'].bars[5] if b.end == old + timedelta(minutes=5)).close == 200
    assert timestamp(feed.leader_calls[-1]['start']) == datetime(2026, 9, 8, 9, 30, tzinfo=ET)


def test_removed_five_minute_overlap_candle_is_not_resurrected_from_cache():
    feed = StockFixture()
    feed.stocks(NOW)
    missing = datetime(2026, 9, 15, 10, tzinfo=ET)
    feed.data5['AAPL'] = [b for b in feed.data5['AAPL'] if timestamp(b['t']) != missing]
    result = feed.stocks(NOW + timedelta(minutes=1))
    assert missing + timedelta(minutes=5) not in [b.end for b in result['AAPL'].bars[5]]
    assert missing + timedelta(minutes=15) in [b.end for b in result['AAPL'].bars[15]]


def test_five_minute_candles_use_calendar_early_close_and_holiday_filters():
    calendar = [{'date': '2026-11-25', 'open': '09:30', 'close': '16:00'},
                {'date': '2026-11-27', 'open': '09:30', 'close': '13:00'}]
    feed = StockFixture(calendar)
    for rows in feed.data5.values():
        rows.extend([raw_bar(datetime(2026, 11, 26, 10, tzinfo=ET)),
                     raw_bar(datetime(2026, 11, 27, 14, tzinfo=ET))])
        rows.sort(key=lambda b: timestamp(b['t']))
    markets = feed.stocks(datetime(2026, 11, 27, 15, tzinfo=ET))
    for symbol in MAG7:
        bars = markets[symbol].bars[5]
        assert len([b for b in bars if b.end.date().isoformat() == '2026-11-27']) == 42
        assert bars[-1].end == datetime(2026, 11, 27, 13, tzinfo=ET)
        assert not [b for b in bars if b.end.date().isoformat() == '2026-11-26']


def test_five_minute_paginated_history_requires_every_requested_leader():
    bar = raw_bar(NOW.replace(minute=25))
    pages = [{'bars': {s: [bar] for s in MAG7[:3]}, 'next_page_token': 'second'},
             {'bars': {s: [bar] for s in MAG7[3:]}}]
    result = Pages(pages).stock_bars(MAG7, 5, NOW-timedelta(days=1), NOW)
    assert set(result) == set(MAG7)
    assert all(rows[0].minutes == 5 and rows[0].end == NOW.replace(minute=30) for rows in result.values())


def test_health_reports_native_five_minute_scope_without_false_sixty_day_gaps():
    from pivot.data_health import stock_health
    feed = StockFixture(longer_calendar())
    markets = feed.stocks(NOW)
    health = stock_health(markets, feed.stock_sessions, NOW)
    assert health['status'] == 'current'
    for item in health['instruments']:
        if item['symbol'] == 'QQQ':
            assert [f['minutes'] for f in item['frames']] == [15, 60, 240, 1440]
            continue
        assert [f['minutes'] for f in item['frames']] == [5, 15, 240]
        five = item['frames'][0]
        assert five['status'] == 'current' and five['missing_count'] == 0
        assert five['history_session_count'] == 7
        assert five['history_from'] == datetime(2026, 9, 8, 9, 30, tzinfo=ET).isoformat()
        assert 'native provider' in five['history_scope']


def test_missing_initial_five_minute_history_and_stale_five_minute_data_block_readiness():
    from pivot.data_health import stock_health
    feed = StockFixture(longer_calendar())
    first_due = datetime(2026, 9, 8, 9, 30, tzinfo=ET)
    feed.data5['AAPL'] = [b for b in feed.data5['AAPL'] if timestamp(b['t']) != first_due]
    feed.data5['MSFT'] = [b for b in feed.data5['MSFT'] if timestamp(b['t']) < NOW.replace(minute=20)]
    markets = feed.stocks(NOW)
    health = stock_health(markets, feed.stock_sessions, NOW)
    by_symbol = {i['symbol']: i for i in health['instruments']}
    assert health['status'] == 'needs_attention'
    assert by_symbol['AAPL']['frames'][0]['status'] == 'incomplete'
    assert by_symbol['AAPL']['frames'][0]['missing_count'] == 1
    assert by_symbol['AAPL']['frames'][1]['status'] == 'current'
    assert by_symbol['MSFT']['frames'][0]['status'] == 'stale'
    assert by_symbol['MSFT']['frames'][1]['status'] == 'current'
