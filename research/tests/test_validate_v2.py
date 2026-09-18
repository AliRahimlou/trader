from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import gzip
import json

import pytest

from pivot import data_health, feeds, history_health, models, strategy
from pivot.models import Bar, MAG7
from research.validate_v2 import (ENGINE_FILES, METHODS, SYMBOLS, _bars, at_time, checkpoint,
                                  ends, freeze_engine, load_inputs, merge_bars, summarize, validate)

AT = datetime(2026, 9, 16, 11, 30, tzinfo=feeds.ET)


def engine():
    return SimpleNamespace(models=models, strategy=strategy, feeds=feeds, data_health=data_health,
                           history_health=history_health, identity={'commit': 'synthetic-test'})


def candle(end, minutes=15, price=100):
    return Bar(end, minutes, price, price + 1, price - 1, price, 10)


@pytest.fixture(scope='module')
def inputs():
    opening = (AT - timedelta(days=68)).replace(hour=9, minute=30)
    sessions = {}
    while opening.date() <= (AT + timedelta(days=1)).date():
        if opening.weekday() < 5:
            sessions[opening.date().isoformat()] = {'open': opening, 'close': opening.replace(hour=16, minute=0)}
        opening += timedelta(days=1)
    fifteen = [candle(at) for session in sessions.values() for at in ends(session, 15)]
    five = [candle(at, 5) for session in list(sessions.values())[-14:] for at in ends(session, 5)]
    return SimpleNamespace(sessions=sessions, stocks15={s: fifteen for s in SYMBOLS},
                           stocks5={s: five for s in MAG7}, vix=fifteen,
                           calendar_start=min(sessions), evaluation_days=['2026-09-16', '2026-09-17'],
                           inputs={}, provenance={})


def test_point_in_time_windows_exclude_future_and_use_seven_sessions(inputs):
    markets, index, sessions, leader_sessions, _ = at_time(inputs, AT, engine())
    assert len(leader_sessions) == 7
    assert max(sessions) == '2026-09-16'
    assert min(sessions) >= (AT.date() - timedelta(days=60)).isoformat()
    leader_start = min(s['open'] for s in leader_sessions.values())
    for market in markets.values():
        assert all(bar.end <= AT for bars in market.bars.values() for bar in bars)
        if market.symbol in MAG7:
            assert market.bars[5][0].end == leader_start + timedelta(minutes=5)
            assert market.bars[5][-1].end == AT
    assert all(bar.end <= AT for bar in index.bars[15])
    assert markets['QQQ'].previous_session == '2026-09-15'
    assert 5 not in markets['QQQ'].bars


def test_future_prices_cannot_change_a_past_checkpoint(inputs):
    changed = deepcopy(inputs)
    changed.stocks15 = {s: [bar if bar.end <= AT else candle(bar.end, 15, 500) for bar in bars]
                        for s, bars in inputs.stocks15.items()}
    changed.stocks5 = {s: [bar if bar.end <= AT else candle(bar.end, 5, 500) for bar in bars]
                       for s, bars in inputs.stocks5.items()}
    changed.vix = [bar if bar.end <= AT else candle(bar.end, 15, 500) for bar in inputs.vix]
    assert checkpoint(inputs, AT, engine()) == checkpoint(changed, AT, engine())


def test_missing_initial_five_minute_history_remains_unknown(inputs):
    changed = deepcopy(inputs)
    _, _, _, recent, _ = at_time(inputs, AT, engine())
    first = min(s['open'] for s in recent.values()) + timedelta(minutes=5)
    changed.stocks5['AAPL'] = [bar for bar in changed.stocks5['AAPL'] if bar.end != first]
    row = checkpoint(changed, AT, engine())
    assert row['evaluation'] == 'unknown_incomplete_history'
    assert row['methods'] == []
    assert row['coverage']['missing_frames'][0]['first_missing_at'] == first.isoformat()


def test_exact_cutoff_five_minute_bar_is_required_for_replay(inputs):
    changed = deepcopy(inputs)
    changed.stocks5['AAPL'] = [bar for bar in changed.stocks5['AAPL'] if bar.end != AT]
    row = checkpoint(changed, AT, engine())
    assert not row['coverage']['complete']
    assert row['coverage']['missing_frames'][0]['missing_count'] == 1


def test_missing_old_context_is_detected_even_with_current_stock_candles(inputs):
    changed = deepcopy(inputs)
    markets, _, _, _, _ = at_time(inputs, AT, engine())
    first = markets['QQQ'].bars[15][0].end
    changed.stocks15['QQQ'] = [bar for bar in changed.stocks15['QQQ'] if bar.end != first]
    row = checkpoint(changed, AT, engine())
    assert not row['coverage']['complete']
    assert any(g['minutes'] == 15 and g['first_missing_at'] == first.isoformat() for g in row['coverage']['missing_frames'])


def test_missing_vix_never_uses_a_proxy_or_fabricated_candle(inputs):
    changed = deepcopy(inputs)
    changed.vix = [bar for bar in inputs.vix if bar.end != AT]
    row = checkpoint(changed, AT, engine())
    assert row['evaluation'] == 'unknown_incomplete_history'
    report = validate(changed, engine(), '2026-09-17')
    assert report['summary']['checkpoint_count'] == 0
    assert report['days'] == [{'day': '2026-09-16', 'evaluation': 'excluded_incomplete_actual_vix_session', 'missing_vix_candles': 1}]


def test_summary_counts_persistent_events_once_without_implying_trades():
    branches = [{'id': method, 'state': 'SETUP_READY', 'signal_qualified': True,
                 'first_failed_gate': None, 'event_id': method + '_one_event'} for method in METHODS]
    result = summarize([{'coverage': {'complete': True}, 'methods': branches}] * 3)
    for row in result['methods'].values():
        assert row['evaluated_checkpoints'] == row['signal_qualified_checkpoints'] == 3
        assert row['distinct_qualified_events'] == 1


def test_two_methods_and_simulation_limit_are_explicit(inputs):
    row = checkpoint(inputs, AT, engine())
    assert row['coverage']['complete']
    assert {r['id'] for r in row['methods']} == set(METHODS)
    assert not row['live_arrival_verified'] and not row['broker_admission_evaluated']
    assert checkpoint(inputs, AT, engine()) == row


def test_replay_is_deterministic_and_excludes_today(inputs, monkeypatch):
    # Keep this orchestration regression cheap; the actual checkpoint is tested above.
    monkeypatch.setattr('research.validate_v2.checkpoint', lambda inputs, at, engine:
                        {'at': at.isoformat(), 'coverage': {'complete': True}, 'methods': []})
    a = validate(inputs, engine(), '2026-09-17')
    b = validate(inputs, engine(), '2026-09-17')
    assert a == b
    assert a['summary']['checkpoint_count'] == 78
    assert [d['day'] for d in a['days']] == ['2026-09-16']
    assert all(r['at'].startswith('2026-09-16') for r in a['records'])


def test_overlap_replacement_does_not_resurrect_deleted_candles():
    old = [candle(AT - timedelta(minutes=30)), candle(AT - timedelta(minutes=15)), candle(AT)]
    revised = [candle(AT, price=200)]
    result, corrections = merge_bars(old, revised, AT - timedelta(minutes=30), AT)
    assert result == [old[0], revised[0]]
    assert corrections == 1


def test_native_frame_relabeling_duplicates_and_off_grid_are_rejected(inputs):
    session = inputs.sessions['2026-09-16']
    def encode(bar):
        return {**bar.__dict__, 'end': bar.end.isoformat()}
    with pytest.raises(ValueError, match='genuine'):
        _bars([encode(candle(AT, 15))], 5, {'2026-09-16': session})
    with pytest.raises(ValueError, match='unique'):
        _bars([encode(candle(AT, 5))] * 2, 5, {'2026-09-16': session})
    with pytest.raises(ValueError, match='grid'):
        _bars([encode(candle(AT + timedelta(minutes=1), 5))], 5, {'2026-09-16': session})


def test_frozen_engine_uses_committed_sources_without_git_or_runtime_files(monkeypatch):
    # A synthetic Git transport makes this test portable to the Docker build,
    # whose source context deliberately has no .git or private market datasets.
    source_root = Path(__file__).resolve().parents[2] / 'pivot'
    sources = {f'pivot/{name}': (source_root / name).read_bytes() for name in ENGINE_FILES}
    def fake_git(args, **kwargs):
        raw = ('a' * 40 + '\n').encode() if 'rev-parse' in args else sources[args[-1].split(':', 1)[1]]
        return SimpleNamespace(stdout=raw)
    monkeypatch.setattr('research.validate_v2.subprocess.run', fake_git)
    frozen = freeze_engine('frozen', source_root.parent)
    assert frozen.strategy is not strategy
    assert frozen.strategy.ANALYSIS_VERSION == strategy.ANALYSIS_VERSION
    assert len(frozen.identity['files_sha256']) == 5
    assert frozen.data_health.closed is frozen.strategy.closed


def test_current_replay_ignores_unused_leader_context_but_legacy_requires_it(inputs):
    changed = deepcopy(inputs)
    changed.stocks15['AAPL'] = []
    current = checkpoint(changed, AT, engine())
    assert current['coverage']['stock_health'] == 'current'
    assert current['coverage']['complete']
    assert current['evaluation'] == 'retrospective_candle_rules'
    legacy = engine()
    legacy.strategy = SimpleNamespace(ANALYSIS_VERSION='nasdaq-video-interpretation-v2')
    old = checkpoint(changed, AT, legacy)
    assert old['evaluation'] == 'unknown_incomplete_history'
    assert {r['minutes'] for r in old['coverage']['missing_frames']} == {15, 240}
