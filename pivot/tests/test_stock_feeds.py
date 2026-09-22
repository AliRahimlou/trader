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


def daily_bars(calendar):
    # Alpaca stamps a trading-day candle at midnight New York, before the open.
    return [raw_bar(datetime.fromisoformat(session['date'] + 'T00:00').replace(tzinfo=ET)) for session in calendar]


TIMEFRAMES = {'15Min': 15, '5Min': 5, '1Day': 1440}


class StockFixture(ReadOnlyFeeds):
    def __init__(self, calendar=None):
        super().__init__({})
        self.calendar = deepcopy(calendar or CALENDAR)
        self.data = {symbol: source_bars(self.calendar) for symbol in SYMBOLS}
        self.data5 = {symbol: source_bars(self.calendar, 5) for symbol in MAG7}
        self.data1d = {symbol: daily_bars(self.calendar) for symbol in MAG7}
        self.calls = []
        self.fail = False
        self.fail_minutes = None

    def get(self, provider, path, params=None):
        self.calls.append((provider, path, deepcopy(params)))
        if path == '/v2/calendar':
            return [row for row in self.calendar if params['start'] <= row['date'] <= params['end']]
        assert provider == 'stocks' and path == '/v2/stocks/bars'
        minutes = TIMEFRAMES[params['timeframe']]
        if self.fail or self.fail_minutes == minutes:
            raise FeedError('Simulated stock outage')
        start, end = timestamp(params['start']), timestamp(params['end'])
        data = {5: self.data5, 15: self.data, 1440: self.data1d}[minutes]
        wanted = set(params['symbols'].split(','))
        return {'bars': {symbol: [deepcopy(b) for b in bars if start <= timestamp(b['t']) <= end]
                         for symbol, bars in data.items() if symbol in wanted}, 'next_page_token': None}

    @property
    def stock_calls(self):
        return [call[2] for call in self.calls if call[1] == '/v2/stocks/bars' and call[2]['timeframe'] == '15Min']

    @property
    def leader_calls(self):
        return [call[2] for call in self.calls if call[1] == '/v2/stocks/bars' and call[2]['timeframe'] == '5Min']

    @property
    def daily_calls(self):
        return [call[2] for call in self.calls if call[1] == '/v2/stocks/bars' and call[2]['timeframe'] == '1Day']


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
        assert market.bars[15 if market.symbol == 'QQQ' else 5][-1].end == NOW.replace(minute=30)
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
    feed.data5.pop('TSLA')
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
    # Early close: the buckets that fit, the last one partial and ending at the close.
    early_four_hour = [b for b in market.bars[240] if b.end.date().isoformat() == '2026-11-27']
    assert [(b.end.hour, b.end.minute, b.minutes) for b in early_four_hour] == [(13, 0, 240)]
    assert [(b.end.hour, b.end.minute) for b in market.bars[240] if b.end.date().isoformat() == '2026-11-25'] == [(13, 30), (16, 0)]
    levels = prior_day_zones(market, datetime(2026, 11, 30, 10, 32, tzinfo=ET))
    assert [z.source for z in levels] == ['previous-day high', 'previous-day low']
    assert all(z.established_at == day.end for z in levels)


def test_full_buckets_policy_and_missing_quarter_remain_unchanged():
    feed = StockFixture()
    result = feed.stocks(NOW)
    session = [b for b in result['QQQ'].bars[15] if b.end.date().isoformat() == '2026-09-15']
    assert len(resample(session, 60, feed.stock_sessions)) == 6
    assert len(resample(session, 240, feed.stock_sessions)) == 2
    assert len(daily(session, feed.stock_sessions)) == 1
    assert not daily(session[:-1], feed.stock_sessions)


def test_four_hour_frame_has_morning_and_closing_buckets_per_full_session():
    """App interpretation of the creator's continuous four-hour candles (video 00:18):
    09:30-13:30 and 13:30-16:00 buckets, the second tagged 240 with 150 minutes of data."""
    feed = StockFixture()
    result = feed.stocks(NOW)
    session = [b for b in result['QQQ'].bars[15] if b.end.date().isoformat() == '2026-09-15']
    for index, bar in enumerate(session):
        if bar.end.hour == 15 and bar.end.minute == 0:
            session[index] = Bar(bar.end, 15, 100, 140, 60, 100, 10, 100)  # Afternoon extreme.
    buckets = resample(session, 240, feed.stock_sessions)
    assert [(b.end.hour, b.end.minute, b.minutes) for b in buckets] == [(13, 30, 240), (16, 0, 240)]
    morning, closing = buckets
    assert morning.open == session[0].open and morning.close == session[15].close
    assert closing.open == session[16].open and closing.close == session[-1].close
    assert (closing.high, closing.low) == (140, 60) and (morning.high, morning.low) == (101, 99)
    assert closing.volume == 10 * 10 and morning.volume == 16 * 10
    # A missing fifteen-minute candle removes only its own bucket.
    without_afternoon = [b for b in session if not (b.end.hour == 14 and b.end.minute == 15)]
    assert [(b.end.hour, b.end.minute) for b in resample(without_afternoon, 240, feed.stock_sessions)] == [(13, 30)]
    without_morning = [b for b in session if not (b.end.hour == 10 and b.end.minute == 0)]
    assert [(b.end.hour, b.end.minute) for b in resample(without_morning, 240, feed.stock_sessions)] == [(16, 0)]
    # The hourly frame keeps full buckets only: the 15:30-16:00 remainder is still dropped.
    assert [(b.end.hour, b.end.minute) for b in resample(session, 60, feed.stock_sessions)][-1] == (15, 30)
    # Two completed sessions before NOW (12:32 on the 16th): two buckets each; today's first bucket is not closed.
    assert len(result['QQQ'].bars[240]) == 2 * 2
    assert len(feed.stocks(NOW.replace(hour=16, minute=1))['QQQ'].bars[240]) == 2 * 3


def test_early_close_before_first_full_bucket_yields_one_partial_four_hour_bar():
    calendar = [{'date': '2026-11-27', 'open': '09:30', 'close': '13:00'},
                {'date': '2026-11-30', 'open': '09:30', 'close': '13:30'}]
    feed = StockFixture(calendar)
    result = feed.stocks(datetime(2026, 11, 30, 15, 0, tzinfo=ET))
    buckets = result['QQQ'].bars[240]
    assert [(b.end.date().isoformat(), b.end.hour, b.end.minute, b.minutes) for b in buckets] == [
        ('2026-11-27', 13, 0, 240), ('2026-11-30', 13, 30, 240)]
    assert buckets[0].volume == 14 * 10 and buckets[1].volume == 16 * 10


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
    assert set(feed.stock_calls[0]['symbols'].split(',')) == {'QQQ'}
    for symbol in MAG7:
        bars = markets[symbol].bars[5]
        assert all(b.minutes == 5 and b.end <= NOW for b in bars)
        assert bars[-1].end == NOW.replace(minute=30)
        assert len(bars) == 3 * len(markets['QQQ'].bars[15])
        assert set(markets[symbol].bars) == {5, 1440}
    assert markets['AAPL'].bars[5][0].close == 175
    assert markets['QQQ'].bars[15][0].close == 100


def test_native_leader_candles_keep_provider_volume_and_vwap():
    """Session VWAP (the leaders' indicator in the video) needs the native ``v`` and ``vw`` fields."""
    feed = StockFixture()
    first = timestamp(feed.data5['NVDA'][0]['t'])
    feed.data5['NVDA'][0] = {**raw_bar(first, 120), 'v': 4321, 'vw': 120.37}
    feed.data5['NVDA'][1] = {**raw_bar(first + timedelta(minutes=5), 121), 'v': 0}
    del feed.data5['NVDA'][1]['vw']
    markets = feed.stocks(NOW)
    bars = markets['NVDA'].bars[5]
    assert (bars[0].volume, bars[0].vwap) == (4321, 120.37)
    assert (bars[1].volume, bars[1].vwap) == (0, None)
    assert all(b.volume == 10 and b.vwap == 100 for b in bars[2:])
    assert all(b.volume == 10 and b.vwap == 100 for b in markets['AAPL'].bars[1440])


def test_leader_daily_bars_are_fetched_once_for_all_leaders_and_end_at_the_session_close():
    feed = StockFixture()
    markets = feed.stocks(NOW)
    assert len(feed.daily_calls) == 1
    call = feed.daily_calls[0]
    assert set(call['symbols'].split(',')) == set(MAG7) and call['timeframe'] == '1Day'
    assert call['start'] == feed.stock_calls[0]['start']
    for symbol in MAG7:
        days = markets[symbol].bars[1440]
        # The ongoing day (NOW is 12:32) is not a completed daily candle.
        assert [(b.end.date().isoformat(), b.end.hour, b.minutes) for b in days] == [
            ('2026-09-14', 16, 1440), ('2026-09-15', 16, 1440)]
        assert all(b.end <= NOW for b in days)
    assert 1440 in markets['QQQ'].bars and markets['QQQ'].bars[1440][-1].end.date().isoformat() == '2026-09-15'
    assert feed.stock_leader_daily_error is None
    after_close = feed.stocks(NOW.replace(hour=16, minute=0, second=30))
    assert after_close['AAPL'].bars[1440][-1].end == NOW.replace(hour=16, minute=0, second=0)


def test_leader_daily_bar_is_kept_and_corrected_across_incremental_refreshes():
    feed = StockFixture(longer_calendar())
    first = feed.stocks(NOW)
    assert [b.end.date().isoformat() for b in first['MSFT'].bars[1440]][0] == '2026-09-03'
    corrected = datetime(2026, 9, 15, 0, 0, tzinfo=ET)
    old = datetime(2026, 9, 8, 0, 0, tzinfo=ET)
    feed.data1d['MSFT'] = [raw_bar(timestamp(b['t']), 300) if timestamp(b['t']) in (corrected, old) else b
                           for b in feed.data1d['MSFT']]
    second = feed.stocks(NOW + timedelta(minutes=1))
    assert timestamp(feed.daily_calls[-1]['start']) == datetime(2026, 9, 15, 0, 0, tzinfo=ET)
    values = {b.end.date().isoformat(): b.close for b in second['MSFT'].bars[1440]}
    assert values['2026-09-15'] == 300 and values['2026-09-08'] == 100
    assert list(values) == [b.end.date().isoformat() for b in first['MSFT'].bars[1440]]
    third = feed.stocks(NOW + timedelta(hours=1))
    assert {b.end.date().isoformat(): b.close for b in third['MSFT'].bars[1440]}['2026-09-08'] == 300
    # A withdrawn overlap-day candle is not resurrected from the cache; older days remain.
    feed.data1d['MSFT'] = [b for b in feed.data1d['MSFT'] if timestamp(b['t']) != corrected]
    fourth = feed.stocks(NOW + timedelta(hours=1, minutes=1))
    assert feed.stock_refresh_mode == 'incremental' and feed.stock_leader_daily_error is None
    days = [b.end.date().isoformat() for b in fourth['MSFT'].bars[1440]]
    assert '2026-09-15' not in days and days[-1] == '2026-09-14' and days[0] == '2026-09-03'
    assert [b.end.date().isoformat() for b in fourth['AAPL'].bars[1440]][-1] == '2026-09-15'


def test_failed_leader_daily_read_is_reported_without_failing_the_required_refresh():
    from pivot.data_health import stock_health
    feed = StockFixture()
    feed.fail_minutes = 1440
    markets = feed.stocks(NOW)
    assert set(markets) == set(SYMBOLS)
    assert all(set(markets[symbol].bars) == {5} for symbol in MAG7)
    assert 'outage' in feed.stock_leader_daily_error
    assert feed._stock_cache['leader_daily'] == {}
    health = stock_health(markets, feed.stock_sessions, NOW, leader_daily_error=feed.stock_leader_daily_error)
    assert health['status'] == 'current'
    daily_frame = next(f for i in health['instruments'] if i['symbol'] == 'AAPL' for f in i['frames'] if f['minutes'] == 1440)
    assert daily_frame['status'] == 'missing' and daily_frame['required'] is False and 'outage' in daily_frame['reason']
    feed.fail_minutes = None
    recovered = feed.stocks(NOW + timedelta(minutes=1))
    assert feed.stock_refresh_mode == 'incremental'
    assert timestamp(feed.daily_calls[-1]['start']).date().isoformat() == '2026-07-18'
    assert len(recovered['AAPL'].bars[1440]) == 2 and feed.stock_leader_daily_error is None
    # One leader without provider daily candles is reported missing; the others keep theirs.
    feed.data1d.pop('TSLA')
    partial = feed.stocks(NOW + timedelta(hours=1))
    assert feed.stock_refresh_mode == 'full' and feed.stock_leader_daily_error is None
    assert 1440 not in partial['TSLA'].bars and all(len(partial[s].bars[1440]) == 2 for s in MAG7 if s != 'TSLA')
    health = stock_health(partial, feed.stock_sessions, NOW + timedelta(hours=1))
    assert health['status'] == 'current'
    tesla = next(f for i in health['instruments'] if i['symbol'] == 'TSLA' for f in i['frames'] if f['minutes'] == 1440)
    assert tesla['status'] == 'missing' and tesla['required'] is False


def test_daily_candle_stamped_after_the_close_is_rejected():
    feed = StockFixture()
    feed.data1d['AAPL'][0] = raw_bar(datetime(2026, 9, 14, 16, 0, tzinfo=ET))
    markets = feed.stocks(NOW)
    assert 'aligned' in feed.stock_leader_daily_error and 1440 not in markets['AAPL'].bars


def test_unused_legacy_leader_history_cannot_break_required_data_refresh():
    feed = StockFixture()
    feed.data = {'QQQ': feed.data['QQQ']}
    markets = feed.stocks(NOW)
    assert set(markets) == set(SYMBOLS)
    assert set(feed._stock_cache['bars']) == {'QQQ'}
    assert all(set(markets[symbol].bars) == {5, 1440} for symbol in MAG7)
    assert all(call['symbols'] == 'QQQ' for call in feed.stock_calls)


def test_previous_cache_layout_forces_a_full_required_frame_refresh():
    feed = StockFixture()
    feed.stocks(NOW)
    feed._stock_cache.pop('schema')
    feed.stocks(NOW + timedelta(minutes=1))
    assert feed.stock_refresh_mode == 'full'
    assert feed._stock_cache['schema'] == 'required-frames-v4'
    feed._stock_cache['schema'] = 'required-frames-v3'
    feed.stocks(NOW + timedelta(minutes=2))
    assert feed.stock_refresh_mode == 'full'


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
    assert markets['QQQ'].bars[15][0].end == datetime(2026, 9, 3, 9, 45, tzinfo=ET)
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
    assert missing + timedelta(minutes=15) in [b.end for b in result['QQQ'].bars[15]]


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
            assert all(f['required'] for f in item['frames'])
            continue
        assert [(f['minutes'], f['required']) for f in item['frames']] == [(5, True), (1440, False)]
        five, day = item['frames']
        assert five['status'] == 'current' and five['missing_count'] == 0
        assert five['history_session_count'] == 7
        assert five['history_from'] == datetime(2026, 9, 8, 9, 30, tzinfo=ET).isoformat()
        assert 'native provider' in five['history_scope']
        assert day['status'] == 'current' and day['history_session_count'] == 9
        assert 'report only' in day['history_scope']
        assert timestamp(day['latest_at']) == datetime(2026, 9, 15, 16, 0, tzinfo=ET)


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
    assert by_symbol['QQQ']['status'] == 'current'
    assert by_symbol['MSFT']['frames'][0]['status'] == 'stale'
    assert markets['QQQ'].bars[15][-1].end == NOW.replace(minute=30)
