"""Latest validated analysis inputs are exposed as plain objects for chart rendering only."""
from datetime import datetime, timedelta, timezone
import json

from pivot.feeds import FeedError
from pivot.models import Bar, Market, MAG7
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_stock_feeds import StockFixture, CALENDAR, NOW


CHECKED = NOW.replace(minute=46, second=20)


class ChartFeed(StockFixture):
    vix_provider = 'insightsentry'

    def __init__(self):
        super().__init__([{'date': '2026-09-11', 'open': '09:30', 'close': '16:00'}, *CALENDAR])
        self.vix_diagnostics = {'provider': 'insightsentry', 'timeframe': 'REAL-TIME',
                                'market_open': True, 'delay_seconds': 0}
        self.vix_fail = False

    def vix(self, now):
        if self.vix_fail:
            raise FeedError('Simulated VIX outage')
        latest = now.replace(minute=30, second=0, microsecond=0)
        bars = [Bar(latest-timedelta(minutes=15*i), 15, 17, 18, 16, 17) for i in (2, 1, 0)]
        return Market('I:VIX', {15: bars}, 'insightsentry', True, now, valid_until=latest+timedelta(seconds=990))


def frozen(monkeypatch, at):
    class FrozenDatetime(datetime):
        current = at
        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)
    monkeypatch.setattr('pivot.service.datetime', FrozenDatetime)
    return FrozenDatetime


def test_refresh_publishes_markets_vix_sessions_and_setup_outside_json_state(tmp_path, monkeypatch):
    frozen(monkeypatch, CHECKED)
    feed = ChartFeed()
    service = Service(feed, Store(tmp_path/'chart.sqlite3'))
    assert service.latest_inputs is None and service.chart_inputs() is None
    service.refresh_analysis()
    inputs = service.chart_inputs()
    assert set(inputs) == {'at', 'markets', 'vix', 'sessions', 'setup'}
    assert inputs['at'] == CHECKED.astimezone(timezone.utc)
    assert set(inputs['markets']) == {'QQQ', *MAG7}
    qqq = inputs['markets']['QQQ']
    assert isinstance(qqq, Market) and set(qqq.bars) == {15, 60, 240, 1440}
    assert [(b.end.hour, b.end.minute) for b in qqq.bars[240] if b.end.date().isoformat() == '2026-09-15'] == [(13, 30), (16, 0)]
    for symbol in MAG7:
        assert set(inputs['markets'][symbol].bars) == {5, 1440}
    assert inputs['vix'].symbol == 'I:VIX' and inputs['vix'].bars[15][-1].vwap is None
    assert inputs['sessions'] == feed.stock_sessions and set(inputs['sessions']) == {'2026-09-11', *[c['date'] for c in CALENDAR]}
    assert inputs['setup'] == service.state['setup'] and inputs['setup']['state']
    # Plain attributes only: the JSON-serialised state never carries Market objects.
    assert 'latest_inputs' not in service.state
    json.dumps(service.state, default=str)
    # chart_inputs() is a private deep copy; mutating it never reaches the service or the next copy.
    inputs['markets']['QQQ'].bars[240].clear()
    inputs['setup']['state'] = 'TAMPERED'
    again = service.chart_inputs()
    assert again['markets']['QQQ'].bars[240] and again['setup']['state'] != 'TAMPERED'
    assert again['markets']['QQQ'] is not service.latest_inputs['markets']['QQQ']


def test_failed_stock_refresh_keeps_the_previous_inputs_and_vix_outage_is_recorded(tmp_path, monkeypatch):
    clock = frozen(monkeypatch, CHECKED)
    feed = ChartFeed()
    service = Service(feed, Store(tmp_path/'chart.sqlite3'))
    service.refresh_analysis()
    first = service.chart_inputs()
    clock.current = CHECKED + timedelta(seconds=60)
    feed.fail = True
    service.refresh_analysis()
    assert service.state['setup'] is None and service.state['data_errors']
    assert service.chart_inputs()['at'] == first['at']
    feed.fail = False
    feed.vix_fail = True
    clock.current = CHECKED + timedelta(seconds=120)
    service.refresh_analysis()
    later = service.chart_inputs()
    assert later['at'] == (CHECKED + timedelta(seconds=120)).astimezone(timezone.utc)
    assert later['vix'] is None and later['setup'] == service.state['setup']
    assert any('VIX' in error for error in service.state['data_errors'])
