"""Calendar publication deadlines expire saved inputs independently of receipt age."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy

from pivot.data_health import expire_health, next_publication_deadline, stock_health
from pivot.diagnostics import build_decision_trace, evidence_fingerprint
from pivot.models import Bar, Market, timestamp
from pivot.service import Service
from pivot.store import Store
from pivot.tests.test_stock_feeds import StockFixture, CALENDAR, NOW


CHECKED = NOW.replace(minute=46, second=20)
DEADLINE = CHECKED+timedelta(seconds=10)


class DelayedNasdaqFeed(StockFixture):
    vix_provider = 'insightsentry'

    def __init__(self):
        super().__init__([{'date': '2026-09-11', 'open': '09:30', 'close': '16:00'}, *CALENDAR])
        # The12:45 QQQ15m candle has not published yet. Native leaders are current.
        self.data['QQQ'] = [bar for bar in self.data['QQQ'] if timestamp(bar['t']) < CHECKED.replace(minute=30, second=0)]
        self.vix_diagnostics = {'provider': 'insightsentry', 'timeframe': 'REAL-TIME',
                                'market_open': True, 'delay_seconds': 0}

    def vix(self, now):
        latest = now.replace(minute=30, second=0, microsecond=0)
        bars = [Bar(latest-timedelta(minutes=15*i), 15, 17, 18, 16, 17) for i in (2, 1, 0)]
        return Market('I:VIX', {15: bars}, 'insightsentry', True, now,
                      valid_until=latest+timedelta(seconds=990))


def test_qqq_calendar_cutoff_caps_fresh_receipt_and_expires_copied_health():
    feed = DelayedNasdaqFeed()
    markets = feed.stocks(CHECKED)
    stocks = stock_health(markets, feed.stock_sessions, CHECKED)
    assert stocks['status'] == 'current'
    assert timestamp(stocks['valid_until']) == DEADLINE
    qqq = stocks['instruments'][0]
    assert timestamp(qqq['frames'][0]['valid_until']) == DEADLINE
    assert all(timestamp(row['valid_until']) > DEADLINE for row in stocks['instruments'][1:])
    health = {'stocks': deepcopy(stocks), 'vix': {'status': 'current', 'valid_until': (CHECKED+timedelta(seconds=90)).isoformat()}}
    expire_health(health, DEADLINE)
    assert health['ready'] is False and health['stocks']['status'] == 'needs_attention'
    assert health['stocks']['instruments'][0]['frames'][0]['status'] == 'stale'
    assert stocks['status'] == 'current'  # Only the copied snapshot was expired.
    assert stock_health(markets, feed.stock_sessions, DEADLINE)['status'] == 'needs_attention'


def test_service_persists_calendar_deadline_and_snapshot_expires_at_boundary(tmp_path, monkeypatch):
    class FrozenDatetime(datetime):
        current = CHECKED
        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)
    monkeypatch.setattr('pivot.service.datetime', FrozenDatetime)
    service = Service(DelayedNasdaqFeed(), Store(tmp_path/'deadline.sqlite3'))
    service.refresh_analysis()
    assert service.state['data_health']['ready'] is True
    assert timestamp(service.state['data_valid_until']) == DEADLINE
    assert timestamp(service.state['decision_trace']['data_health']['stocks']['valid_until']) == DEADLINE
    FrozenDatetime.current = DEADLINE
    snapshot = service.snapshot()
    assert snapshot['data_health']['ready'] is False
    assert snapshot['data_health']['stocks']['instruments'][0]['frames'][0]['status'] == 'stale'
    assert service.state['data_health']['ready'] is True


def test_publication_deadlines_respect_early_close_daily_and_full_buckets():
    opened = CHECKED.replace(hour=9, minute=30, second=0)
    closed = opened.replace(hour=13, minute=0)
    tomorrow = opened+timedelta(days=1)
    sessions = {'short': {'open': opened, 'close': closed},
                'next': {'open': tomorrow, 'close': tomorrow.replace(hour=16, minute=0)}}
    assert next_publication_deadline(sessions, 60, opened.replace(hour=11)) == opened.replace(hour=12)+timedelta(seconds=90)
    assert next_publication_deadline(sessions, 60, opened.replace(hour=12, minute=30)) == tomorrow+timedelta(minutes=60, seconds=90)
    # The early close owes a partial four-hour bucket at its close; a full day owes 13:30 and then the close.
    assert next_publication_deadline(sessions, 240, opened-timedelta(days=1)) == closed+timedelta(seconds=90)
    assert next_publication_deadline(sessions, 240, closed) == tomorrow+timedelta(minutes=240, seconds=90)
    assert next_publication_deadline(sessions, 240, tomorrow+timedelta(minutes=240)) == tomorrow.replace(hour=16, minute=0)+timedelta(seconds=90)
    assert next_publication_deadline(sessions, 240, tomorrow.replace(hour=16, minute=0)) is None
    assert next_publication_deadline(sessions, 1440, opened-timedelta(days=1)) == closed+timedelta(seconds=90)


def test_global_deadline_orders_instants_with_different_utc_offsets():
    feed = DelayedNasdaqFeed()
    markets = feed.stocks(CHECKED)
    markets['AAPL'].observed_at = CHECKED.astimezone(timezone.utc)-timedelta(seconds=85)
    stocks = stock_health(markets, feed.stock_sessions, CHECKED)
    assert timestamp(stocks['valid_until']) == CHECKED+timedelta(seconds=5)


def test_receipt_deadlines_refresh_saved_body_without_fragmenting_evidence(tmp_path):
    feed = DelayedNasdaqFeed()
    markets = feed.stocks(NOW)
    store = Store(tmp_path/'receipts.sqlite3')
    def record(at):
        for market in markets.values():
            market.observed_at = at
        stocks = stock_health(markets, feed.stock_sessions, at)
        assert stocks['status'] == 'current'
        return build_decision_trace(None, markets, None, at,
            data_health={'ready': False, 'stocks': stocks, 'vix': {'status': 'blocked'}})
    first = record(NOW)
    later = record(NOW+timedelta(seconds=30))
    first_stocks, later_stocks = first['data_health']['stocks'], later['data_health']['stocks']
    assert first_stocks['valid_until'] != later_stocks['valid_until']
    assert first_stocks['instruments'][0]['valid_until'] != later_stocks['instruments'][0]['valid_until']
    assert evidence_fingerprint(first) == evidence_fingerprint(later)
    saved_first, saved_later = store.record_decision(first), store.record_decision(later)
    assert saved_first['id'] == saved_later['id'] and saved_later['observation_count'] == 2
    assert saved_later['data_health']['stocks'] == later_stocks
    changed = deepcopy(later)
    changed['data_health']['stocks']['instruments'][0]['frames'][0]['valid_until'] = CHECKED.isoformat()
    assert evidence_fingerprint(changed) != evidence_fingerprint(later)
    changed = deepcopy(later)
    changed['setup']['leader_observation_valid_until'] = CHECKED.isoformat()
    assert evidence_fingerprint(changed) != evidence_fingerprint(later)
