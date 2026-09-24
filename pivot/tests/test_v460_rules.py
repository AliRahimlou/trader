"""4.6.0 Socrates execution rules, as production runs them (expert-panel decisions 2026-09-24).

- Target: today's open when it is in the trade direction at least 0.20% from the
  live entry quote, else the nearest key level or pre-existing area 0.20% away.
- New entries only 10:00-12:00 ET.
- Longs only (shorts need PIVOT_SOCRATES_SHORTS_LIVE=1; prior-day-sweep shorts never trade).
- Skip an entry whose stop is more than 1.5% from the entry quote.
FakeBroker only; no network.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from pivot import feature_flags
from pivot.execution import EXECUTION_GATES, Executor, socrates_target
from pivot.models import Bar, Market
from pivot.policy import POLICY, POLICY_VERSION
from pivot.store import Store
from pivot.strategy import key_levels
from pivot.tests.test_execution import FakeBroker, NOW, enable, identify_event, ready

pytestmark = pytest.mark.rules46
ET = ZoneInfo('America/New_York')
IN_WINDOW = NOW - timedelta(hours=1)  # NOW is 12:00 ET; 11:00 ET is inside the window.


def runtime(tmp_path, at=IN_WINDOW):
    broker = FakeBroker()
    broker.at = at
    store = Store(tmp_path / 'rules46.db')
    executor = Executor(broker, store, now=lambda: broker.at)
    enable(executor)
    return executor, broker, store


def long_setup(at=IN_WINDOW, stop=99.0, exit_levels=None):
    snapshot = ready(at)
    snapshot['setup'].update(stop=stop, exit_levels=exit_levels if exit_levels is not None else [])
    return snapshot


def test_production_defaults_are_the_panel_decisions():
    rules = feature_flags.socrates_rules()
    assert rules['version'] == '4.6' and rules['shorts_live'] is False and rules['sweep_shorts'] is False
    assert [t.isoformat() for t in rules['entry_window']] == ['10:00:00', '12:00:00']
    assert rules['max_stop_distance'] == Decimal('0.015') and rules['target_floor'] == Decimal('0.002')
    assert {'entry_window', 'stop_distance'} <= EXECUTION_GATES
    assert POLICY_VERSION == 'nasdaq-qqq-execution-v8-socrates-4-6'
    text = ' '.join(POLICY['summary'])
    for words in ('between 10:00 AM and 12:00 PM', 'Longs only', 'more than 1.5%', "today\'s 09:30 open", 'at least 0.20%'):
        assert words in text, words


def test_target_prefers_the_daily_open_then_the_nearest_level_past_the_floor():
    levels = [{'price': 741.5, 'source': 'daily_open'}, {'price': 740.4, 'source': 'previous_day_high'},
              {'price': 745.0, 'source': 'four-hour area'}, {'price': 'bad', 'source': 'x'}, {'source': 'missing'}]
    assert socrates_target('long', '739.90', levels) == (Decimal('741.50'), 'daily_open')
    # The open is only 0.05% away: the nearest level past 0.20% wins instead.
    assert socrates_target('long', '741.10', levels) == (Decimal('745.00'), 'four-hour area')
    assert socrates_target('short', '742.00', levels) == (Decimal('740.40'), 'previous_day_high')
    assert socrates_target('long', '746.00', levels) is None and socrates_target('long', '740', None) is None


def bars_for(day, opens, start='09:30', count=26, step=0.1):
    """Regular-session 15-minute candles for one ET day: price rises by `step` each candle from `opens`."""
    first = datetime.fromisoformat(f'{day}T{start}').replace(tzinfo=ET)
    rows = []
    for i in range(count):
        o = opens + i * step
        rows.append(Bar(first + timedelta(minutes=15 * (i + 1)), 15, o, o + 0.5, o - 0.5, o + step))
    return rows


def test_key_levels_come_only_from_candles_known_at_the_time():
    # Previous week Mon 9/14 - Fri 9/18, this week Mon 9/21 and today Tue 9/22 up to 11:00 ET.
    days = [('2026-09-14', 700), ('2026-09-15', 702), ('2026-09-16', 704), ('2026-09-17', 706), ('2026-09-18', 708),
            ('2026-09-21', 710), ('2026-09-22', 720)]
    rows = [bar for day, price in days for bar in bars_for(day, price)]
    now = datetime(2026, 9, 22, 11, 0, 30, tzinfo=ET)
    market = Market('QQQ', {15: rows}, 'alpaca_iex', True, now)
    got = {row['source']: row['price'] for row in key_levels(market, now)}
    assert got['daily_open'] == 720 and got['previous_day_high'] == round(710 + 25 * 0.1 + 0.5, 2)
    assert got['previous_day_low'] == 709.5
    # Before 13:30 the previous four-hour bucket is yesterday's afternoon (13:30-16:00: candles 16..25).
    assert got['previous_4h_low'] == round(710 + 16 * 0.1 - 0.5, 2) and got['previous_4h_high'] == round(710 + 25 * 0.1 + 0.5, 2)
    assert got['week_open'] == 710 and got['monday_high'] == round(710 + 25 * 0.1 + 0.5, 2) and got['monday_low'] == 709.5
    assert got['previous_week_high'] == round(708 + 25 * 0.1 + 0.5, 2) and got['previous_week_low'] == 699.5
    assert got['month_open'] == 700
    # Nothing from after `now` leaks in: today's later candles are excluded.
    assert all(row['price'] < 723 for row in key_levels(market, now) if row['source'] == 'daily_open')
    # A session without its 09:30 candle contributes no open.
    late = Market('QQQ', {15: bars_for('2026-09-22', 720, start='09:45', count=5)}, 'alpaca_iex', True, now)
    assert 'daily_open' not in {row['source'] for row in key_levels(late, now)}


def test_long_entry_uses_the_daily_open_as_its_target(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(long_setup(exit_levels=[{'price': 100.5, 'source': 'daily_open'}, {'price': 101.0, 'source': 'previous_day_high'}]))
    trade = store.active_trade()
    assert trade and trade['symbol'] == 'QQQ' and trade['target'] == '100.50' and trade['target_source'] == 'daily_open'
    assert trade['signal_geometry']['plan_target'] == '110' and trade['signal_geometry']['target'] == '100.50'
    assert [order['type'] for order in broker.sent] == ['market', 'stop']


def test_without_a_qualifying_level_the_plan_target_is_kept(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(long_setup(exit_levels=[{'price': 100.1, 'source': 'daily_open'}]))  # only 0.1% away
    assert store.active_trade()['target'] == '110'


@pytest.mark.parametrize('clock', ['09:45', '12:00', '14:30'])
def test_no_new_entries_outside_10_to_12_new_york_time(tmp_path, clock):
    at = datetime.fromisoformat(f'2026-09-16T{clock}:00').replace(tzinfo=ET).astimezone(timezone.utc)
    executor, broker, store = runtime(tmp_path, at)
    executor.tick(long_setup(at))
    assert broker.sent == [] and store.active_trade() is None
    assert 'only between 10:00 AM and 12:00 PM ET' in executor.message


def test_shorts_are_not_traded_and_a_long_behind_a_short_still_is(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready(IN_WINDOW, direction='short'))
    assert broker.sent == [] and 'longs (QQQ) only' in executor.message
    short, long_ = ready(IN_WINDOW, direction='short'), long_setup()
    snapshot = {**long_, 'setup': {**short['setup'], 'entry_candidates': [short['setup'], long_['setup']]}}
    executor.tick(snapshot)
    assert store.active_trade()['symbol'] == 'QQQ' and broker.sent[0]['symbol'] == 'QQQ'


def test_shorts_switch_allows_retest_shorts_but_never_sweep_shorts(tmp_path, monkeypatch):
    monkeypatch.setenv('PIVOT_SOCRATES_SHORTS_LIVE', '1')
    executor, broker, store = runtime(tmp_path)
    executor.tick(ready(IN_WINDOW, direction='short'))  # prior_day_sweep short
    assert broker.sent == []
    retest = ready(IN_WINDOW, direction='short')
    retest['setup'].update(strategy_id='four_hour_retest', stop=101.0)
    identify_event(retest['setup'])
    executor.tick(retest)
    assert store.active_trade()['symbol'] == 'PSQ'


def test_an_entry_with_a_stop_more_than_1_5_percent_away_is_skipped(tmp_path):
    executor, broker, store = runtime(tmp_path)
    executor.tick(long_setup(stop=98.0))  # 2% from the 100 ask
    assert broker.sent == [] and 'more than 1.5% from the entry price' in executor.message
    executor.tick(long_setup(stop=98.6))  # 1.4%
    assert store.active_trade()['stop'] == '98.6'


def test_readiness_explains_the_window(tmp_path):
    from pivot.readiness import _market
    early = datetime(2026, 9, 16, 9, 40, tzinfo=ET).astimezone(timezone.utc)
    clock = {'is_open': True, 'timestamp': early.isoformat(), 'next_close': (early + timedelta(hours=6, minutes=20)).isoformat()}
    (item, _), is_open, no_entries = _market({'clock': clock}, early)
    assert is_open is True and no_entries is True and item['status'] == 'info' and '10:00 AM–12:00 PM ET' in item['detail']
    inside = datetime(2026, 9, 16, 10, 40, tzinfo=ET).astimezone(timezone.utc)
    (item, _), _, no_entries = _market({'clock': {**clock, 'timestamp': inside.isoformat()}}, inside)
    assert no_entries is False and item['status'] == 'ok' and 'until 12:00 PM ET' in item['detail']


def test_analyzer_attaches_exit_levels_to_every_ready_candidate():
    from pivot.tests.test_signal_execution_integration import multiple_ready_snapshot
    candidates = multiple_ready_snapshot('methods')['setup']['entry_candidates']
    for candidate in candidates:
        levels = candidate['exit_levels']
        assert isinstance(levels, list) and levels, candidate['strategy_id']
        assert all(isinstance(row['price'], (int, float)) and row['price'] > 0 and isinstance(row['source'], str) for row in levels)
        # The event's own area is never an exit candidate.
        zone = candidate['event_zone']
        assert not any(row['source'] == zone['source'] and row['price'] in (round(zone['low'], 2), round(zone['high'], 2))
                       for row in levels)
        assert 'exit_areas' not in candidate
