"""Release-level switches that sit above saved owner permissions.

A flag never writes, clears or reinterprets a saved permission row. It only
limits what the running release may start. Pausing crypto keeps its saved
settings, incidents and ledgers intact, so the updater's permission fingerprint
is unchanged and management of any existing crypto position continues.
"""
import os

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
