"""Release 4.5.0 review fixes: PSQ exits anchored at the live QQQ bid, leader period
levels that do not depend on whether one daily read succeeded, and the Look-left VIX
panel drawing the consolidation bases the analysis uses. Offline fixtures only."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pivot.chart_data import build
from pivot.models import MAG7, Bar, Market
from pivot.strategy import VIX_PIVOT_SOURCE, period_levels
from pivot.tests.test_execution import ready
from pivot.tests.test_execution_short_proxy import runtime
from pivot.tests.test_stock_feeds import NOW as FEED_NOW, StockFixture

ET = ZoneInfo('America/New_York')


# --- PSQ proxy: distances from the live QQQ bid ----------------------------------

def test_psq_stop_and_target_mirror_the_structural_levels_from_the_live_qqq_bid(tmp_path):
    executor, broker, store = runtime(tmp_path)
    # Plan reference 100 (the hourly close), stop 110, target 90. QQQ now trades at
    # 99.20 bid, 0.8% below the reference: inside the 1% drift allowance and the
    # stop/target geometry, so the entry is admitted.
    broker.bid, broker.ask = '99.20', '99.21'
    executor.tick(ready(direction='short'))
    trade = store.active_trade()
    assert trade['symbol'] == 'PSQ' and trade['signal_geometry']['entry'] == '100'
    # Stop 110 is 10.887% above the bid and target 90 is 9.274% below it, so PSQ
    # (ask 35) gets a stop 10.887% below and a target 9.274% above: 31.19 / 38.25.
    # Anchored at the stale 100 reference they would have been 31.50 / 38.50.
    assert (trade['stop'], trade['target']) == ('31.19', '38.25')
    assert trade['proxy_geometry']['signal_price'] == '99.20'
    assert 'live QQQ bid' in trade['proxy_geometry']['note']


def test_psq_translation_equals_the_plan_when_the_bid_is_the_reference(tmp_path):
    executor, broker, store = runtime(tmp_path)  # bid 100 = reference 100
    executor.tick(ready(direction='short'))
    trade = store.active_trade()
    assert (trade['stop'], trade['target']) == ('31.50', '38.50')
    assert trade['proxy_geometry']['signal_price'] == '100'


# --- leader period levels: regular-session data first, daily outages retained ----

def _session(day, base, high=None):
    bars, at = [], datetime(2026, 9, day, 9, 35, tzinfo=ET)
    while at <= datetime(2026, 9, day, 16, tzinfo=ET):
        bars.append(Bar(at, 5, base, base + 1, base - 1, base + 0.5, 1000, base))
        at += timedelta(minutes=5)
    if high is not None:
        bars[10] = Bar(bars[10].end, 5, base, high, base - 1, base + 0.5, 1000, base)
    return bars


def test_a_session_the_five_minute_window_covers_uses_its_regular_session_candles():
    history = _session(14, 100) + _session(15, 110, high=115)
    today = [bar for bar in _session(16, 120)][:12]
    fives = history + today
    # The provider daily bar for the 15th carries a high (130) and low (90) no
    # regular-session candle printed; it must not become the previous-session level.
    daily = [Bar(datetime(2026, 9, 15, 16, tzinfo=ET), 1440, 104, 130, 90, 111)]
    at = today[-1].end - timedelta(minutes=5)
    with_daily = period_levels(Market('AAPL', {5: fives, 1440: daily}, 'alpaca_iex', True, today[-1].end), fives, at)
    without = period_levels(Market('AAPL', {5: fives}, 'alpaca_iex', True, today[-1].end), fives, at)
    found = {zone.source: zone.mid for zone in with_daily}
    assert found['previous-session high'] == 115 and found['previous-session low'] == 109
    assert found['previous-session close'] == 110.5
    assert with_daily == without


def test_a_failed_daily_read_keeps_the_last_validated_daily_candles():
    feed = StockFixture()
    first = feed.stocks(FEED_NOW)
    assert all(first[symbol].bars[1440] for symbol in MAG7)
    before = {symbol: period_levels(first[symbol], first[symbol].bars[5], FEED_NOW) for symbol in MAG7}
    feed.fail_minutes = 1440
    for later, mode in ((FEED_NOW + timedelta(minutes=1), 'incremental'), (FEED_NOW + timedelta(hours=1), 'full')):
        markets = feed.stocks(later)
        assert feed.stock_refresh_mode == mode and 'outage' in feed.stock_leader_daily_error
        for symbol in MAG7:
            assert markets[symbol].bars[1440] == first[symbol].bars[1440]
            assert period_levels(markets[symbol], markets[symbol].bars[5], FEED_NOW) == before[symbol]
    from pivot.data_health import stock_health
    health = stock_health(markets, feed.stock_sessions, FEED_NOW + timedelta(hours=1),
                          leader_daily_error=feed.stock_leader_daily_error)
    daily = next(f for i in health['instruments'] if i['symbol'] == 'AAPL' for f in i['frames'] if f['minutes'] == 1440)
    assert daily['count'] == len(first['AAPL'].bars[1440]) and 'last validated daily candles' in daily['reason']


# --- Look-left VIX panel ---------------------------------------------------------

def test_look_left_vix_panel_draws_consolidation_bases_and_labels_vix_pivots():
    start = datetime(2026, 9, 21, 9, 45, tzinfo=ET)
    closes = ([15.0, 15.05, 15.1, 15.05, 15.0, 15.1]         # a flat base
              + [15.8, 16.6, 17.4, 16.4, 17.5, 16.3, 17.45]  # the launch, with repeated highs near 17.45
              + [16.9, 16.8, 16.7, 16.6, 16.5])
    bars = []
    for i, close in enumerate(closes):
        flat = i < 6
        bars.append(Bar(start + timedelta(minutes=15 * i), 15, close, close + (0.05 if flat else 0.2),
                        close - (0.05 if flat else 0.2), close))
    at = bars[-1].end + timedelta(minutes=1)
    vix = Market('I:VIX', {15: bars}, 'insightsentry', True, at, None, at + timedelta(minutes=10))
    payload = build({'at': at, 'markets': {}, 'vix': vix, 'sessions': {}, 'setup': {}})
    sources = {zone['source'] for zone in payload['vix']['zones']}
    assert 'VIX consolidation base' in sources
    base = next(zone for zone in payload['vix']['zones'] if zone['source'] == 'VIX consolidation base')
    assert base['low'] == 14.95 and base['high'] == 15.15
    assert '4h repeated pivot' not in sources
    assert VIX_PIVOT_SOURCE in sources
    assert any('consolidation bases' in note for note in payload['notes'])
