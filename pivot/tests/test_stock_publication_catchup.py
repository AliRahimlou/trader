"""Bounded stock publication recovery; fake provider and clock, no live services."""
from datetime import datetime, timedelta, timezone

import pytest

from pivot.feeds import FeedError, ET
from pivot.models import timestamp
from pivot.service import Service, publication_catchup_bucket
from pivot.store import Store
from pivot.tests.test_stock_feeds import StockFixture

AT = datetime(2026, 9, 16, 11, 30, 31, tzinfo=ET)


class Feeds(StockFixture):
    def __init__(self, *, symbol='AAPL', lag=True, recover=True, fail_second=False, calendar=None):
        super().__init__(calendar)
        self.vix_provider = 'insightsentry'
        self.invocations, self.vix_calls = [], []
        self.recover, self.fail_second = recover, fail_second
        source = self.data if symbol == 'QQQ' else self.data5
        self.original = list(source[symbol])
        self.source, self.symbol = source, symbol
        if lag:
            minutes = 15 if symbol == 'QQQ' else 5
            self.remove(AT.replace(second=0)-timedelta(minutes=minutes))

    def remove(self, at):
        self.source[self.symbol] = [bar for bar in self.source[self.symbol] if timestamp(bar['t']) != at]

    def stocks(self, now):
        self.invocations.append(now)
        if len(self.invocations) == 2 and self.fail_second:
            raise FeedError('Synthetic stock catch-up failure')
        result = super().stocks(now)
        if self.recover:
            self.source[self.symbol] = list(self.original)
        return result

    def vix(self, now):
        self.vix_calls.append(now)
        return None


class Event:
    def __init__(self, clock, stop=False):
        self.clock, self.stop, self.waits = clock, stop, []
    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock[0] += timedelta(seconds=seconds)
        return self.stop


def service(tmp_path, monkeypatch, feeds, at=AT, stop=False):
    clock = [at]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0].astimezone(tz or timezone.utc)
    monkeypatch.setattr('pivot.service.datetime', Clock)
    app = Service(feeds, Store(tmp_path/'state.db'))
    app.stop_event = Event(clock, stop)
    app._record_decision = lambda *args: None
    return app, clock


@pytest.mark.parametrize('symbol', ['AAPL', 'QQQ'])
def test_late_native_candle_has_one_stock_only_catchup(tmp_path, monkeypatch, symbol):
    feeds = Feeds(symbol=symbol)
    app, clock = service(tmp_path, monkeypatch, feeds)
    app.refresh_analysis()
    assert len(feeds.invocations) == 2 and len(feeds.vix_calls) == 1
    assert feeds.invocations[1] == AT+timedelta(seconds=5)
    assert app.stop_event.waits == [5]
    row = next(item for item in app.state['observations'] if item['symbol'] == symbol)
    assert timestamp(row['at']) == AT.replace(second=0)
    health = next(item for item in app.state['data_health']['stocks']['instruments'] if item['symbol'] == symbol)
    assert timestamp(health['observed_at']) == feeds.invocations[1]


def test_current_inputs_do_not_trigger_extra_collection(tmp_path, monkeypatch):
    feeds=Feeds(lag=False)
    app,_=service(tmp_path,monkeypatch,feeds)
    app.refresh_analysis()
    assert len(feeds.invocations)==len(feeds.vix_calls)==1
    assert app.stop_event.waits==[]


def test_still_lagging_input_cannot_catchup_twice_in_same_bucket(tmp_path, monkeypatch):
    feeds=Feeds(recover=False)
    app,clock=service(tmp_path,monkeypatch,feeds)
    app.refresh_analysis()
    clock[0]+=timedelta(seconds=20)
    app.refresh_analysis()
    assert len(feeds.invocations)==3 and len(feeds.vix_calls)==2
    assert app.stop_event.waits==[5]


def test_historical_gap_is_not_treated_as_publication_lag(tmp_path, monkeypatch):
    feeds=Feeds(lag=False,recover=False)
    feeds.remove(AT.replace(day=14,hour=10,minute=0,second=0))
    app,_=service(tmp_path,monkeypatch,feeds)
    app.refresh_analysis()
    assert len(feeds.invocations)==1 and app.stop_event.waits==[]


def test_failed_initial_read_is_not_retried(tmp_path, monkeypatch):
    feeds=Feeds()
    feeds.fail=True
    app,_=service(tmp_path,monkeypatch,feeds)
    app.refresh_analysis()
    assert len(feeds.invocations)==1 and len(feeds.vix_calls)==1
    assert app.stop_event.waits==[]


def test_failed_catchup_does_not_publish_first_read_with_new_receipt(tmp_path, monkeypatch):
    feeds=Feeds(fail_second=True)
    app,_=service(tmp_path,monkeypatch,feeds)
    app.refresh_analysis()
    assert len(feeds.invocations)==2 and len(feeds.vix_calls)==1
    assert app.state['observations']==[]
    assert app.state['setup'] is None
    assert not app.state['data_health']['ready']
    assert all(i['observed_at'] is None for i in app.state['data_health']['stocks']['instruments'])


def test_stop_interrupts_catchup_before_stock_or_insight_requests(tmp_path, monkeypatch):
    feeds=Feeds()
    app,_=service(tmp_path,monkeypatch,feeds,stop=True)
    app.refresh_analysis()
    assert len(feeds.invocations)==1 and len(feeds.vix_calls)==0
    assert app.state['analysis_at'] is None


@pytest.mark.parametrize('at', [AT.replace(second=20), AT.replace(minute=33),
                               AT.replace(hour=16,minute=0), AT.replace(hour=9,minute=30)])
def test_only_regular_session_publication_window_can_qualify(at):
    feeds=Feeds(recover=False)
    markets=feeds.stocks(at)
    assert publication_catchup_bucket(markets,feeds.stock_sessions,at) is None


def test_early_close_prevents_after_close_catchup():
    feeds=Feeds(recover=False,calendar=[{'date':'2026-09-16','open':'09:30','close':'13:00'}])
    at=AT.replace(hour=13,minute=0)
    markets=feeds.stocks(at)
    assert publication_catchup_bucket(markets,feeds.stock_sessions,at) is None


def test_missing_or_invalid_instruments_never_earn_catchup():
    feeds=Feeds(recover=False)
    markets=feeds.stocks(AT)
    del markets['MSFT']
    assert publication_catchup_bucket(markets,feeds.stock_sessions,AT) is None
