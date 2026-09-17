"""Independent pipeline review: immutable input boundaries and decision identity."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo
import json

import pytest
import requests

from pivot.models import Bar, MAG7
from research.data import at_time, encode_bar, expected_ends, load
from research import trace

ET = ZoneInfo('America/New_York')


@pytest.fixture(autouse=True)
def prohibit_real_requests(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Pipeline tests must never contact a provider')
    monkeypatch.setattr(requests.sessions.Session, 'request', blocked)


def dataset(day='2026-09-17', close='16:00'):
    opened = datetime.fromisoformat(day + 'T09:30').replace(tzinfo=ET)
    closed = datetime.fromisoformat(day + 'T' + close).replace(tzinfo=ET)
    sessions = {day: {'open': opened, 'close': closed}}
    bars = [encode_bar(Bar(end, 15, 100, 101, 99, 100)) for end in expected_ends(sessions[day])]
    return {'schema': 'pivot-research-bars-v1',
            'sessions': {day: {k: v.isoformat() for k, v in sessions[day].items()}},
            'stocks': {symbol: deepcopy(bars) for symbol in ('QQQ', *MAG7)}, 'vix': deepcopy(bars)}


def save(path, data):
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize('day,close,expected', [
    ('2026-03-06', '16:00', 26), ('2026-03-09', '16:00', 26),
    ('2026-11-27', '13:00', 14),
])
def test_calendar_load_accepts_dst_and_early_close_without_rewriting_input(tmp_path, day, close, expected):
    path = save(tmp_path / 'data.json', dataset(day, close))
    content = path.read_bytes()
    _, sessions, stocks, vix, digest = load(path)
    assert len(stocks['QQQ']) == len(vix) == expected
    assert stocks['QQQ'][-1].end == sessions[day]['close']
    assert digest == sha256(content).hexdigest() and path.read_bytes() == content


@pytest.mark.parametrize('change', [
    lambda d: d.update(sessions={}),
    lambda d: d['sessions']['2026-09-17'].update(close='2026-09-17T09:00:00-04:00'),
    lambda d: d['sessions']['2026-09-17'].update(close='2026-09-18T16:00:00-04:00'),
    lambda d: d['sessions']['2026-09-17'].update(close='2026-09-17T16:01:00-04:00'),
    lambda d: d['sessions']['2026-09-17'].update(open='2026-09-17T08:00:00-04:00'),
    lambda d: d['stocks']['QQQ'][0].update(end='2026-09-17T09:46:00-04:00'),
    lambda d: d['vix'][0].update(end='2026-09-17T09:30:00-04:00'),
    lambda d: d['vix'][-1].update(end='2026-09-17T16:15:00-04:00'),
    lambda d: d['stocks']['AAPL'][0].update(minutes=15.0),
    lambda d: d['stocks']['QQQ'].append(deepcopy(d['stocks']['QQQ'][-1])),
])
def test_invalid_session_or_candle_cannot_enter_replay(tmp_path, change):
    data = dataset()
    change(data)
    with pytest.raises(ValueError):
        load(save(tmp_path / 'invalid.json', data))


@pytest.fixture
def trace_fixture(monkeypatch):
    data = dataset()
    sessions = {day: {key: datetime.fromisoformat(value) for key, value in row.items()}
                for day, row in data['sessions'].items()}
    bars = [Bar(datetime.fromisoformat(row['end']), 15, 100, 101, 99, 100) for row in data['vix']]
    at = sessions['2026-09-17']['open'] + timedelta(hours=1, seconds=60)
    markets, vix = at_time(sessions, {symbol: bars for symbol in ('QQQ', *MAG7)}, bars, at)
    setup = {'state': 'SETUP_READY', 'event': 'synthetic event',
             'event_at': (at - timedelta(seconds=60)).isoformat(), 'direction': 'long',
             'entry': 100, 'stop': 99, 'target': 101,
             'checks': [{'name': 'first gate', 'passed': True}]}
    monkeypatch.setattr(trace, 'analyze', lambda *args: deepcopy(setup))
    monkeypatch.setattr(trace, 'leader_diagnostics', lambda *args: {})
    monkeypatch.setattr(trace, 'vix_confirmation', lambda *args: (True, 'synthetic'))
    variant = {'id': 'baseline', 'zone_tolerance': .001, 'persistence_bars': 1,
               'minimum_leaders': 4, 'maximum_opposition': 0}
    return setup, lambda: trace.decision(markets, vix, sessions, at, variant)


def test_decision_identity_is_one_attempt_per_event_direction_like_production(trace_fixture):
    setup, decision = trace_fixture
    initial = decision()['setup_id']
    setup.update(entry=100.25, stop=99.25, target=101.25)
    assert decision()['setup_id'] == initial
    setup['direction'] = 'short'
    assert decision()['setup_id'] != initial


def test_gate_survival_does_not_count_later_passes_after_a_failure(trace_fixture):
    setup, decision = trace_fixture
    setup['state'] = 'CONFIRMING'
    setup['checks'] = [{'name': 'location', 'passed': True},
                       {'name': 'VIX', 'passed': False},
                       {'name': 'stop and target', 'passed': True}]
    record = decision()
    assert record['first_failed_gate'] == 'VIX'
    assert record['passed_gates'] == ['location']
    assert not record['signal_qualified'] and not record['simulation_admissible']
