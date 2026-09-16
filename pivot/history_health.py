"""Find missing completed candles over the exact exchange-calendar history in use."""
from datetime import timedelta

from .models import timestamp


def frame_gaps(bars, sessions, minutes, now):
    """Return calendar-relative gaps without inventing candles or market sessions.

    All supplied sessions participate, including sessions before the first candle
    or after the final candle. Daily means the actual session close. Intraday
    frames require a full bucket; overnight and short closing buckets do not count.
    A 90-second publication allowance applies to each expected closing timestamp.
    No calendar means no gap inference, for standalone synthetic observations.
    """
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes <= 0:
        raise ValueError('A positive candle duration is required')
    result = {'missing_count': 0, 'first_missing_at': None, 'reason': None}
    if not sessions:
        return result
    cutoff = timestamp(now) - timedelta(seconds=90)
    observed = {timestamp(bar.end) for bar in bars if bar.minutes == minutes}
    expected = set()
    for session in sessions.values():
        opened, closed = timestamp(session['open']), timestamp(session['close'])
        if closed <= opened:
            raise ValueError('Trading session must close after its opening')
        if minutes == 1440:
            if closed <= cutoff:
                expected.add(closed)
            continue
        end = opened + timedelta(minutes=minutes)
        while end <= closed and end <= cutoff:
            expected.add(end)
            end += timedelta(minutes=minutes)
    missing = expected - observed
    if missing:
        first = min(missing).isoformat()
        frame = 'daily' if minutes == 1440 else f'{minutes}-minute'
        result.update(missing_count=len(missing), first_missing_at=first,
                      reason=f'{len(missing)} completed {frame} candle(s) missing from the exchange calendar history')
    return result
