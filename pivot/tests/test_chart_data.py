"""Look-left chart payload: pure over the chart_inputs() shape, bounded, JSON-safe, never raising."""
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

from pivot.chart_data import BAR_LIMITS, LEADER_ZONES, build
from pivot.models import MAG7, Bar, Market, Zone

ET = ZoneInfo('America/New_York')
NOW = datetime(2026, 9, 22, 15, 5, tzinfo=ET)


def _sessions(count, last=NOW):
    days = []
    day = last.date()
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def _bars(minutes, per_session, sessions, base=600.0, step=0.5):
    rows = []
    for index, day in enumerate(sessions):
        opened = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30)
        for slot in range(per_session):
            # Daily bars are end-stamped at the close, as the feed does.
            end = opened + timedelta(minutes=min(minutes, 390) * (slot + 1))
            price = base + index * 2 + slot * step
            rows.append(Bar(end, minutes, price, price + 1.5, price - 1.0, price + 0.5, 1000, price))
    return rows


def _market(symbol, frames, sessions):
    return Market(symbol, frames, 'alpaca', True, NOW, sessions[-2].isoformat())


def inputs(sessions=None):
    sessions = sessions or _sessions(8)
    qqq = _market('QQQ', {60: _bars(60, 6, sessions), 240: _bars(240, 1, sessions * 5),
                          15: _bars(15, 26, sessions), 1440: _bars(1440, 1, sessions, base=590)}, sessions)
    leaders = {symbol: _market(symbol, {5: _bars(5, 78, sessions[-2:], base=100 + i)}, sessions)
               for i, symbol in enumerate(MAG7)}
    vix = Market('I:VIX', {15: _bars(15, 27, sessions[-5:], base=18, step=0.05)}, 'insightsentry', True, NOW,
                 sessions[-2].isoformat(), NOW + timedelta(minutes=10))
    setup = {
        'strategy_id': 'four_hour_retest', 'direction': 'long',
        'levels': [{'low': 601.0, 'high': 602.2, 'source': '4h repeated interaction',
                    'established_at': (NOW - timedelta(days=9)).isoformat(), 'touches': 3},
                   {'low': 640.0, 'high': 641.3, 'source': '4h repeated interaction',
                    'established_at': (NOW - timedelta(days=4)).isoformat(), 'touches': 2},
                   {'low': 611.5, 'high': 611.5, 'source': 'previous-day high', 'established_at': (NOW - timedelta(days=1)).isoformat()},
                   {'low': 596.2, 'high': 596.2, 'source': 'previous-day low', 'established_at': (NOW - timedelta(days=1)).isoformat()}],
        'vix_zone': {'low': 18.3, 'high': 18.7, 'source': '4h repeated pivot', 'established_at': (NOW - timedelta(days=2)).isoformat()},
        'vix_reaction_at': (NOW - timedelta(minutes=20)).isoformat(),
        'strategies': [
            {'id': 'four_hour_retest', 'label': '4-hour areas / hourly break and retest', 'state': 'CONFIRMING',
             'event_zone': {'low': 601.0, 'high': 602.2, 'source': '4h repeated interaction',
                            'established_at': (NOW - timedelta(days=9)).isoformat()},
             'event_origin_at': (NOW - timedelta(hours=2)).isoformat(), 'event_at': (NOW - timedelta(hours=1)).isoformat(),
             'event_expires_at': (NOW + timedelta(hours=1)).isoformat(), 'direction': 'long',
             'entry': 603.4, 'stop': 600.9, 'target': 611.5},
            {'id': 'prior_day_sweep', 'label': 'Previous-day boundary sweep', 'state': 'WATCHING', 'event_zone': None}],
        'leader_evidence': {symbol: {'vote': 'long' if i < 5 else None, 'reason': 'current reaction', 'latest_close': 100.0 + i,
                                     'zones': [{'low': 100 + i + k * 0.4, 'high': 100.2 + i + k * 0.4, 'source': '5m repeated interaction',
                                                'established_at': (NOW - timedelta(hours=3)).isoformat(), 'touches': 2} for k in range(9)],
                                     'reaction_zone': {'low': 100 + i, 'high': 100.2 + i, 'source': '5m repeated interaction',
                                                       'established_at': (NOW - timedelta(hours=3)).isoformat()}}
                            for i, symbol in enumerate(MAG7)},
    }
    calendar = {day.isoformat(): {'open': datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30),
                                  'close': datetime.combine(day, datetime.min.time(), ET).replace(hour=16)} for day in sessions}
    return {'at': NOW, 'markets': {'QQQ': qqq, **leaders}, 'vix': vix, 'sessions': calendar, 'setup': setup}


def test_payload_is_json_safe_bounded_and_iso_stamped():
    payload = build(inputs())
    encoded = json.dumps(payload)
    assert json.loads(encoded) == payload
    assert payload['available'] is True and payload['symbol'] == 'QQQ'
    assert payload['at'] == NOW.astimezone(ZoneInfo('UTC')).isoformat()
    assert len(payload['bars']['60']) == 5 * 6, 'last five sessions of hourly candles'
    assert len(payload['bars']['240']) == 30
    assert len(payload['vix']['bars15']) == 3 * 27, 'last three VIX sessions'
    assert len(payload['sessions']) == 5 and payload['sessions'][-1]['date'] == NOW.date().isoformat()
    assert payload['sessions'][-1]['close'].endswith('+00:00')
    for bar in payload['bars']['60'] + payload['bars']['240'] + payload['vix']['bars15']:
        assert set(bar) == {'t', 'o', 'h', 'l', 'c'} and bar['t'].endswith('+00:00')
        assert bar['l'] <= min(bar['o'], bar['c']) <= max(bar['o'], bar['c']) <= bar['h']
    assert payload['reference'] == payload['bars']['60'][-1]['c']
    total = len(payload['bars']['60']) + len(payload['bars']['240']) + len(payload['vix']['bars15'])
    assert total <= sum(BAR_LIMITS.values()) <= 600


def test_levels_events_previous_day_and_vix_reaction_are_translated():
    payload = build(inputs())
    assert [row['source'] for row in payload['levels']] == [
        'previous-day low', '4h repeated interaction', 'previous-day high', '4h repeated interaction']
    assert payload['levels'][1]['touches'] == 3 and payload['levels'][0]['touches'] is None
    assert payload['previous_day'] == {'high': 611.5, 'low': 596.2}
    assert len(payload['events']) == 1, 'a method without an event zone draws nothing'
    event = payload['events'][0]
    assert event['method'] == 'four_hour_retest' and event['selected'] is True and event['state'] == 'CONFIRMING'
    assert event['zone']['touches'] == 3, 'touch count recovered from the matching level'
    assert (event['direction'], event['stop'], event['target'], event['entry']) == ('long', 600.9, 611.5, 603.4)
    assert event['origin_at'] < event['retest_at'] < event['expires_at']
    vix = payload['vix']
    assert vix['reaction']['direction'] == 'short', 'a QQQ long expects the opposite VIX reaction'
    assert vix['reaction']['zone']['low'] == 18.3 and vix['reaction']['at'].endswith('+00:00')
    assert isinstance(vix['zones'], list)
    for zone in vix['zones']:
        assert zone['source'] in ('VIX 15m repeated pivot', 'VIX consolidation base') and zone['touches'] >= 2


def test_leaders_keep_the_nearest_zones_only():
    payload = build(inputs())
    assert set(payload['leaders']) == set(MAG7)
    apple = payload['leaders']['AAPL']
    assert apple['vote'] == 'long' and apple['source'] == '5m repeated interaction'
    assert len(apple['zones']) == LEADER_ZONES
    distances = [max(z['low'] - apple['latest_close'], apple['latest_close'] - z['high'], 0) for z in apple['zones']]
    assert distances == sorted(distances)
    assert payload['leaders']['TSLA']['vote'] is None


def test_missing_fields_degrade_to_empty_collections():
    assert build(None)['bars'] == {'60': [], '240': []}
    empty = build({'at': NOW, 'markets': {}, 'vix': None, 'sessions': {}, 'setup': None})
    assert json.loads(json.dumps(empty)) == empty
    assert empty['levels'] == [] and empty['events'] == [] and empty['previous_day'] is None
    assert empty['vix'] == {'bars15': [], 'sessions': [], 'zones': [], 'reaction': None}
    assert all(row['vote'] is None and row['zones'] == [] for row in empty['leaders'].values())
    partial = inputs()
    partial['setup'] = {'levels': [{'low': 'bad', 'high': 1}, {'low': 5, 'high': 4}, {'low': 0, 'high': 1},
                                   {'low': 600, 'high': 601, 'touches': True}],
                        'strategies': 'not-a-list', 'leader_evidence': 'unexpected'}
    del partial['sessions']
    result = build(partial)
    assert result['levels'] == [{'low': 600.0, 'high': 601.0, 'source': 'area', 'established_at': None, 'touches': None}]
    assert result['events'] == []
    assert result['sessions'][0]['open'] is None and len(result['sessions']) == 5
    assert result['previous_day']['source'] == 'daily bar', 'previous-day fallback comes from the daily candle'
    assert result['previous_day']['high'] == 591.5 + 6 * 2 and set(result['leaders']) == set(MAG7)
    # Without analysis evidence the leaders are re-observed from their latest
    # closed five-minute candle (15:05 ET here), never from an unfinished one.
    assert result['leaders']['AAPL']['latest_close'] == 100 + 66 * 0.5 + 2 + 0.5
    assert result['leaders']['AAPL']['zones'] and len(result['leaders']['AAPL']['zones']) <= LEADER_ZONES
    json.dumps(result)


def test_zone_objects_and_missing_clock_are_accepted():
    data = inputs()
    data['at'] = None
    data['setup']['levels'] = [Zone(601.0, 602.2, NOW - timedelta(days=9), '4h repeated interaction', 4)]
    data['setup']['vix_zones'] = [Zone(18.0, 18.4, NOW - timedelta(days=3), '4h repeated pivot', 2)]
    payload = build(data)
    assert payload['levels'][0]['touches'] == 4
    assert payload['vix']['zones'] == [{'low': 18.0, 'high': 18.4, 'source': '4h repeated pivot',
                                        'established_at': (NOW - timedelta(days=3)).astimezone(ZoneInfo('UTC')).isoformat(), 'touches': 2}]
    assert payload['at'] == payload['bars']['60'][-1]['t'], 'the latest candle stands in for a missing clock'


def test_other_symbol_and_oversized_history_stay_bounded():
    data = inputs(_sessions(60))
    payload = build(data)
    assert len(payload['bars']['240']) == 30 and len(payload['bars']['60']) == 30
    assert len(payload['vix']['bars15']) == 81
    other = build(data, symbol='SPY')
    assert other['symbol'] == 'SPY' and other['bars'] == {'60': [], '240': []} and other['reference'] is None
