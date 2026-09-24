"""Release-level switches that sit above saved owner permissions.

A flag never writes, clears or reinterprets a saved permission row. It only
limits what the running release may start. Pausing crypto keeps its saved
settings, incidents and ledgers intact, so the updater's permission fingerprint
is unchanged and management of any existing crypto position continues.
"""
import os
from datetime import time
from decimal import Decimal

CRYPTO_PAUSE_ENV = 'PIVOT_CRYPTO_PAUSED'
# 4.5.1: the owner asked for a Socrates-only focus. Crypto is paused by default.
CRYPTO_PAUSED_DEFAULT = True
CRYPTO_PAUSE_REASON = 'Socrates-only focus'
CRYPTO_PAUSED_MESSAGE = ('Crypto is paused: Socrates-only focus. '
                         'Existing crypto positions, if any, keep their exits.')
CRYPTO_PAUSED_REFUSAL = 'Crypto is paused in this release; Socrates-only focus'
_ON, _OFF = ('1', 'true'), ('0', 'false')


class CryptoPaused(ValueError):
    """A crypto setting change was refused because this release pauses crypto."""

    def __init__(self):
        super().__init__(CRYPTO_PAUSED_REFUSAL)


def _environment_value():
    value = os.environ.get(CRYPTO_PAUSE_ENV)
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if value in _ON:
        return True
    if value in _OFF:
        return False
    return None  # Unrecognized text cannot re-enable crypto; the code default applies.


def crypto_paused():
    """Read at call time so every worker sees the same current value."""
    value = _environment_value()
    return CRYPTO_PAUSED_DEFAULT if value is None else value


def pause_source():
    return 'code_default' if _environment_value() is None else 'environment'


def strategy_pause():
    """Snapshot contract: snapshot['strategy_pause']."""
    paused = crypto_paused()
    return {'range_reversal': {'paused': paused,
                               'reason': CRYPTO_PAUSE_REASON if paused else 'Crypto is available in this release.',
                               'source': pause_source()}}


# 4.6.0 Socrates execution rules (expert-panel decisions, 2026-09-24). They are
# part of the reviewed policy text; the owner re-accepts them with the version.
SHORTS_ENV = 'PIVOT_SOCRATES_SHORTS_LIVE'
# Test-only: restores the 4.5 execution rules so older scenario tests keep
# describing the behaviour they were written for. Never copied from video.env.
LEGACY_RULES_ENV = 'PIVOT_SOCRATES_LEGACY_RULES'
ENTRY_WINDOW = (time(10, 0), time(12, 0))  # New York time; Socrates' stated 10:00-12:00 window.
MAX_STOP_DISTANCE = Decimal('0.015')       # Skip an entry whose stop is more than 1.5% from the entry quote.
TARGET_FLOOR = Decimal('0.002')            # A target must be at least 0.20% away (covers the round-trip cost).


def _flag(name):
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return True if value in _ON else False if value in _OFF else None


def socrates_rules():
    """Active Socrates execution rules, read at call time.

    Default (4.6): entries only 10:00-12:00 ET, longs only (shorts need
    PIVOT_SOCRATES_SHORTS_LIVE=1; prior-day-sweep shorts are never traded),
    stop at most 1.5% from the entry quote, target = today's open or the next
    key level at least 0.20% away.
    """
    if _flag(LEGACY_RULES_ENV):
        return {'version': 'legacy', 'entry_window': None, 'shorts_live': True, 'sweep_shorts': True,
                'max_stop_distance': None, 'target_rule': 'plan', 'target_floor': TARGET_FLOOR}
    return {'version': '4.6', 'entry_window': ENTRY_WINDOW, 'shorts_live': _flag(SHORTS_ENV) is True,
            'sweep_shorts': False, 'max_stop_distance': MAX_STOP_DISTANCE, 'target_rule': 'next_key_level',
            'target_floor': TARGET_FLOOR}
