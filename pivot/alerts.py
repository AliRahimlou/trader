"""Optional outbound owner alerts for events that pause entries or need attention.

An app interpretation, not a video rule: the recordings say nothing about
operations. Delivery is best-effort and fully off the trading path. A missing
webhook means no delivery; a failing webhook is swallowed. Nothing here reads
broker state, and nothing here can change a permission or send an order.
"""
import os
from datetime import datetime, timezone
from threading import Thread

import requests

ENV_VAR = 'PIVOT_ALERT_WEBHOOK_URL'
TIMEOUT_SECONDS = 5
# Journal kinds worth an interruption: each one either pauses a strategy or
# leaves broker state the owner should look at. Routine fills are not alerted.
# 'protection_failed' and 'exit_needs_attention' are alerted in their own right
# because the strategy pause that accompanies them is a no-op once Socrates is
# already Off, and an unprotected or stalled live position must not go quiet.
ALERT_KINDS = frozenset({
    'strategy_paused', 'execution_needs_attention', 'partial_entry_needs_attention',
    'order_rejected', 'order_not_found', 'crypto_order_not_found', 'incident_opened',
    'protection_failed', 'exit_needs_attention',
})


def _deliver(url, payload):
    try:
        requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
    except Exception:
        pass  # Alerting can never raise into a worker or a request handler.


def notify(kind, body):
    """POST {kind, body, at} to the configured webhook from a daemon thread.

    The environment is read at call time so a container restart with a new
    value takes effect without code changes. Returns the delivery thread for
    callers that want to wait (tests), or None when alerting is not configured.
    """
    url = os.environ.get(ENV_VAR)
    if not isinstance(url, str) or not url.strip():
        return None
    payload = {'kind': str(kind), 'body': body, 'at': datetime.now(timezone.utc).isoformat()}
    thread = Thread(target=_deliver, args=(url.strip(), payload), name='pivot-alert', daemon=True)
    thread.start()
    return thread
