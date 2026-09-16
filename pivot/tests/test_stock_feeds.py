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


def source_bars(calendar):
    result = []
    for session in calendar:
        at = datetime.fromisoformat(session['date'] + 'T' + session['open']).replace(tzinfo=ET)
        end = datetime.fromisoformat(session['date'] + 'T' + session['close']).replace(tzinfo=ET)
        while at < end:
            result.append(raw_bar(at))
            at += timedelta(minutes=15)
    return result


class StockFixture(ReadOnlyFeeds):
    def __init__(self, calendar=None):
        super().__init__({})
        self.calendar = deepcopy(calendar or CALENDAR)
        self.data = {symbol: source_bars(self.calendar) for symbol in SYMBOLS}
        self.calls = []
        self.fail = False

    def get(self, provider, path, params=None):
        self.calls.append((provider, path, deepcopy(params)))
        if path == '/v2/calendar':
            return [row for row in self.calendar if params['start'] <= row['date'] <= params['end']]
        assert provider == 'stocks' and path == '/v2/stocks/bars'
        if self.fail:
            raise FeedError('Simulated stock outage')
        start, end = timestamp(params['start']), timestamp(params['end'])
        return {'bars': {symbol: [deepcopy(b) for b in bars if start <= timestamp(b['t']) <= end]
                         for symbol, bars in self.data.items()}, 'next_page_token': None}

    @property
    def stock_calls(self):
        return [call[2] for call in self.calls if call[1] == '/v2/stocks/bars']


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
