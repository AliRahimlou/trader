"""Production direction policy must explain waits without inventing an attempt."""
import pytest

from pivot.readiness import build_socrates_readiness
from pivot.service import Service
from pivot.tests.test_socrates_readiness import NOW, healthy, item

pytestmark = pytest.mark.rules46


def test_disabled_shorts_are_not_reported_as_previously_attempted():
    setup = healthy()['setup']
    setup['event_id'] = 'unattempted-short'
    service = Service.__new__(Service)
    # No eligible candidates means there should be no history lookup at all.
    assert service._setup_consumed(setup) is False
    inputs = healthy()
    inputs.update(setup=setup, setup_consumed=service._setup_consumed(setup))
    result = build_socrates_readiness(inputs, NOW)
    assert 'longs (QQQ) only' in item(result, 'setup')['detail']
    assert 'already traded' not in item(result, 'setup')['detail']
    assert item(result, 'instrument_psq')['status'] == 'info'


def test_ready_card_describes_allowed_long_behind_disallowed_short():
    inputs = healthy()
    short = dict(inputs['setup'])
    long = dict(short, direction='long', strategy_id='prior_day_sweep')
    inputs['setup']['entry_candidates'] = [short, long]
    # PSQ availability is irrelevant when shorts are disabled.
    inputs['assets']['PSQ'] = {'asset': None}
    result = build_socrates_readiness(inputs, NOW)
    assert result['status'] == 'ready'
    assert 'long QQQ' in item(result, 'setup')['detail']
    assert 'sweep' in item(result, 'setup')['detail']
    assert item(result, 'instrument_psq')['status'] == 'info'


def test_short_override_still_waives_psq_for_the_selected_long(monkeypatch):
    monkeypatch.setenv('PIVOT_SOCRATES_SHORTS_LIVE', '1')
    inputs = healthy()
    short = dict(inputs['setup'], strategy_id='prior_day_sweep')
    long = dict(short, direction='long', strategy_id='four_hour_retest')
    inputs['setup'] = dict(short, entry_candidates=[short, long])
    inputs['assets']['PSQ'] = {'asset': None}
    result = build_socrates_readiness(inputs, NOW)
    assert result['status'] == 'ready'
    assert 'long QQQ' in item(result, 'setup')['detail']
    assert item(result, 'instrument_psq')['status'] == 'info'
    assert 'ready long setup buys QQQ' in item(result, 'instrument_psq')['detail']
    # An eligible retest short really needs PSQ and must retain that warning.
    inputs['setup'] = dict(short, strategy_id='four_hour_retest')
    result = build_socrates_readiness(inputs, NOW)
    assert result['status'] == 'waiting'
    assert item(result, 'instrument_psq')['status'] == 'warn'
