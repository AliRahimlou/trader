"""Find missing completed candles over the exact exchange-calendar history in use."""
from datetime import timedelta

from .models import timestamp

# Frames whose final bucket of a session may be shorter than the nominal length.
# Video rule: the creator marks four-hour levels on continuous futures candles,
# so an afternoon extreme is a four-hour extreme. App interpretation (4.5.0):
# the QQQ regular session is split into 09:30-13:30 and 13:30-16:00 buckets;
# the closing bucket carries only 150 minutes (fewer on an early close) but
# stays tagged 240 minutes so frame consumers keep working. Hourly and
# fifteen-minute frames remain full buckets only.
PARTIAL_CLOSING_FRAMES = (240,)


def bucket_ends(opening, closing, minutes):
    """Completed-bucket end times of one session, anchored at its open.

    Daily means the actual session close. Intraday frames list every full
    bucket; frames in ``PARTIAL_CLOSING_FRAMES`` also end a final, shorter
    bucket at the close (an early close before the first full bucket yields
    one partial bucket ending at that close). Other remainders are dropped.
    """
    if minutes == 1440:
        return [closing]
    ends = []
    end = opening + timedelta(minutes=minutes)
    while end <= closing:
        ends.append(end)
        end += timedelta(minutes=minutes)
    if minutes in PARTIAL_CLOSING_FRAMES and (not ends or ends[-1] < closing):
        ends.append(closing)
    return ends


def frame_gaps(bars, sessions, minutes, now):
    """Return calendar-relative gaps without inventing candles or market sessions.

    All supplied sessions participate, including sessions before the first candle
    or after the final candle. Daily means the actual session close. Intraday
    frames require a full bucket; overnight and short closing buckets do not count,
    except the four-hour closing bucket, which ends at the session close.
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
        expected.update(end for end in bucket_ends(opened, closed, minutes) if end <= cutoff)
    missing = expected - observed
    if missing:
        first = min(missing).isoformat()
        frame = 'daily' if minutes == 1440 else f'{minutes}-minute'
        result.update(missing_count=len(missing), first_missing_at=first,
                      reason=f'{len(missing)} completed {frame} candle(s) missing from the exchange calendar history')
    return result
