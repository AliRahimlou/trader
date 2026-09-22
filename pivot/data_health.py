"""Per-input diagnostics. A connected endpoint is not the same as usable data."""
from datetime import timedelta
from math import isfinite
from .models import MAG7, timestamp
from .strategy import closed, vix_candles_fresh, CANDLE_PUBLICATION_GRACE_SECONDS
from .history_health import frame_gaps, bucket_ends
from .feeds import leader_history_sessions, LEADER_HISTORY_SESSIONS

LABELS = {5: '5-minute', 15: '15-minute', 60: '1-hour', 240: '4-hour', 1440: 'Daily'}
# Required frames gate readiness. The leader daily frame feeds the leader
# period levels (video: the leaders' indicator plots prior day/week/month
# levels) for sessions outside the five-minute window, so it is an entry
# input; app choice: it is reported but is not a readiness gate. A failed
# daily read keeps the last validated daily candles (see feeds.stocks).
REQUIRED_FRAMES = {'QQQ': (15, 60, 240, 1440), 'leader': (5,)}
REPORT_ONLY_FRAMES = {'QQQ': (), 'leader': (1440,)}
# A provider source timestamp this far ahead of the host clock is treated as
# clock skew rather than a future value (app choice; the videos say nothing
# about clocks). Shared by the VIX collectors; kept here because the frozen
# research engine bundles this module without the provider adapters.
SOURCE_CLOCK_SKEW_SECONDS = 5


def last_expected(sessions, minutes, now):
    """Last complete regular-session bucket, allowing 90 seconds for publication.

    Bucket ends follow ``history_health.bucket_ends``: the four-hour closing
    bucket (ending at the session close) is expected once the close has passed.
    """
    cutoff = now - timedelta(seconds=CANDLE_PUBLICATION_GRACE_SECONDS)
    candidates = [end for session in sessions.values()
                  for end in bucket_ends(timestamp(session['open']), timestamp(session['close']), minutes)
                  if end <= cutoff]
    return max(candidates) if candidates else None


def next_publication_deadline(sessions, minutes, latest):
    """When another full calendar candle must replace the current last candle."""
    if latest is None:
        return None
    candidates = []
    for session in sessions.values():
        ends = bucket_ends(timestamp(session['open']), timestamp(session['close']), minutes)
        following = next((end for end in ends if end > latest), None)
        if following is not None:
            candidates.append(following)
    return min(candidates) + timedelta(seconds=CANDLE_PUBLICATION_GRACE_SECONDS) if candidates else None


def stock_health(markets, sessions, now, *, error=None, fetch_seconds=None, refresh_mode=None, full_at=None,
                 leader_daily_error=None):
    instruments = []
    recent_sessions = leader_history_sessions(sessions)
    for symbol in ('QQQ', *MAG7):
        market = markets.get(symbol)
        frames = []
        kind = 'QQQ' if symbol == 'QQQ' else 'leader'
        # Nasdaq needs the resampled context used by its location methods.
        # Leaders use native five-minute areas/reactions; their daily frame
        # supplies older period levels and is reported, not a readiness gate.
        for minutes in REQUIRED_FRAMES[kind] + REPORT_ONLY_FRAMES[kind]:
            required = minutes in REQUIRED_FRAMES[kind]
            bars = closed(market, minutes, now) if market else []
            latest = bars[-1].end if bars else None
            frame_sessions = recent_sessions if minutes == 5 else sessions
            expected = last_expected(frame_sessions, minutes, now)
            deadline = next_publication_deadline(frame_sessions, minutes, latest)
            gaps = frame_gaps(bars, frame_sessions, minutes, now)
            if not bars:
                status, reason = 'missing', 'No validated completed candles'
                if not required and leader_daily_error:
                    reason += ': ' + leader_daily_error
            elif expected and latest < expected:
                status, reason = 'stale', 'Latest completed candle is behind the exchange calendar'
            elif gaps['missing_count']:
                status, reason = 'incomplete', gaps['reason']
            elif len(bars) < 3 and minutes != 1440:
                status, reason = 'incomplete', 'More history is required for level comparisons'
            else:
                status, reason = 'current', 'Completed candles available'
            if bars and not required and leader_daily_error:
                reason += '; the latest daily read failed, so the last validated daily candles are kept: ' + leader_daily_error
            if minutes == 5:
                scope = f'Last {LEADER_HISTORY_SESSIONS} trading sessions; native provider candles'
            elif not required:
                scope = '60 calendar days; native provider daily candles (period levels outside the 5-minute window; reported, not a readiness gate)'
            else:
                scope = '60 calendar days'
            frames.append({'minutes': minutes, 'label': LABELS[minutes], 'status': status, 'required': required,
                           'reason': reason, 'count': len(bars), 'latest_at': latest.isoformat() if latest else None,
                           'expected_at': expected.isoformat() if expected else None,
                           'valid_until': deadline.isoformat() if deadline else None,
                           'missing_count': gaps['missing_count'], 'first_missing_at': gaps['first_missing_at'],
                           'history_scope': scope,
                           'history_session_count': len(frame_sessions),
                           'history_from': min((timestamp(s['open']) for s in frame_sessions.values()), default=None).isoformat() if frame_sessions else None})
        deadlines = ([market.observed_at + timedelta(seconds=90)] if market else []) + [
            timestamp(frame['valid_until']) for frame in frames if frame['valid_until'] and frame['required']]
        valid_until = min(deadlines) if deadlines else None
        valid_age = market and 0 <= (now - market.observed_at).total_seconds() < 90 and valid_until > now
        okay = bool(market and market.realtime and valid_age
                    and all(f['status'] == 'current' for f in frames if f['required']))
        instruments.append({'symbol': symbol, 'status': 'current' if okay else 'needs_attention',
                            'source': market.source if market else None, 'frames': frames,
                            'observed_at': market.observed_at.isoformat() if market else None,
                            'valid_until': valid_until.isoformat() if valid_until else None})
    ready = not error and all(i['status'] == 'current' for i in instruments)
    source = next((m.source for m in markets.values()), None)
    return {'status': 'current' if ready else 'needs_attention', 'checked_at': now.isoformat(),
            'valid_until': min(timestamp(i['valid_until']) for i in instruments if i['valid_until']).isoformat() if ready else None,
            'coverage': 'IEX only; one exchange' if source == 'alpaca_iex' else 'Consolidated US exchanges' if source == 'alpaca_sip' else 'Unavailable',
            'source': source, 'instruments': instruments, 'error': error, 'fetch_seconds': fetch_seconds,
            'refresh_mode': refresh_mode, 'last_full_refresh_at': full_at.isoformat() if full_at else None,
            'frame_policy': (f'Leader 5-minute candles are native Alpaca data for the last {LEADER_HISTORY_SESSIONS} trading sessions; '
                             'leader daily candles are native Alpaca data over 60 calendar days; they supply the leader period levels for sessions the 5-minute window does not cover (sessions it covers use their regular-session 5-minute candles) and are reported, not a readiness gate; a failed daily read keeps the last validated daily candles; '
                             'QQQ 15-minute and higher context retains 60 calendar days. Full regular-session hourly buckets only; the closing half hour is excluded. '
                             '4-hour buckets are 09:30-13:30 and 13:30-16:00 New York: the closing bucket holds 150 minutes of data (less on an early close, where the buckets that fit are kept and the last ends at the close) but is still tagged 240 minutes. '
                             'Daily candles cover the actual full session, including early closes.')}


def vix_health(market, now, error=None, details=None):
    bars = closed(market, 15, now) if market else []
    latest = bars[-1].end if bars else None
    if (market and market.source == 'insightsentry') or (details or {}).get('provider') == 'insightsentry':
        verification = details or {}
        failure = error or verification.get('error')
        try:
            until = timestamp(market.valid_until)
            observed = timestamp(market.observed_at)
            delay = verification.get('delay_seconds', 0)
            valid = bool(market.symbol == 'I:VIX' and market.source == 'insightsentry' and
                         market.realtime is True and len(bars) >= 3 and observed <= now and
                         verification.get('timeframe') == 'REAL-TIME' and
                         not isinstance(delay, bool) and isinstance(delay, (int, float)) and delay == 0 and
                         verification.get('provider', 'insightsentry') == 'insightsentry')
            current = bool(not failure and valid and vix_candles_fresh(market, now))
        except (KeyError, TypeError, ValueError, AttributeError):
            until, valid, current = None, False, False
        market_closed = bool(not failure and valid and verification.get('market_open') is False)
        status = 'market_closed' if market_closed else 'current' if current else 'blocked'
        return {'status': status, 'checked_at': now.isoformat(),
                'source': 'InsightSentry · actual Cboe VIX', 'symbol': 'I:VIX', 'required': True,
                'latest_at': latest.isoformat() if latest else None, 'bar_count': len(bars),
                'valid_until': until.isoformat() if until else None,
                'candles_current': current and not market_closed, 'entry_quote_required': True,
                'error': failure or (None if status != 'blocked' else 'Fresh, completed actual VIX candles are required'),
                'verification': verification}
    try:
        value_at = timestamp((details or {})['latest_value_at'])
        valid_until = min(market.observed_at + timedelta(seconds=90),
                          value_at + timedelta(seconds=90), latest + timedelta(seconds=990))
        # The source stamp may sit within CLOCK_SKEW ahead of the host clock.
        verified = (details or {}).get('timeframe') == 'REAL-TIME' and -SOURCE_CLOCK_SKEW_SECONDS <= (now-value_at).total_seconds() <= 90
    except (KeyError, TypeError, ValueError, AttributeError):
        valid_until, verified = None, False
    current = bool(not error and market and market.symbol == 'I:VIX' and market.source == 'massive_indices' and market.realtime and latest and
                   verified and len(bars) >= 3 and
                   0 <= (now - market.observed_at).total_seconds() <= 90 and
                   0 <= (now - latest).total_seconds() <= 990)
    return {'status': 'current' if current else 'blocked', 'checked_at': now.isoformat(),
            'source': 'Massive · actual Cboe VIX', 'symbol': 'I:VIX', 'required': True,
            'latest_at': latest.isoformat() if latest else None, 'bar_count': len(bars),
            'valid_until': valid_until.isoformat() if valid_until else None,
            'error': error or (None if current else 'Fresh, completed actual VIX candles are required'),
            'verification': details or {}}


def expire_health(health, now):
    """Expire a copied health report between refreshes, including stalled downloads."""
    if not health:
        return
    stocks, vix = health['stocks'], health['vix']
    for instrument in stocks['instruments']:
        at = instrument.get('observed_at')
        until = instrument.get('valid_until')
        for frame in instrument.get('frames', []):
            frame_until = frame.get('valid_until')
            if frame.get('status') == 'current' and frame_until and now >= timestamp(frame_until):
                frame.update(status='stale', reason='A newer completed candle is due; waiting for a fresh update')
                # Non-gating frames (the leader daily frame) expire visibly but never gate readiness.
                if frame.get('required', True):
                    instrument['status'] = 'needs_attention'
        if (not at or not 0 <= (now-timestamp(at)).total_seconds() < 90
                or (until and now >= timestamp(until))):
            instrument['status'] = 'needs_attention'
    if any(i['status'] != 'current' for i in stocks['instruments']):
        stocks['status'] = 'needs_attention'
    until = vix.get('valid_until')
    if vix['status'] == 'current' and (not until or now > timestamp(until)):
        vix.update(status='blocked', error='Actual VIX verification expired; waiting for a fresh update')
        if 'candles_current' in vix:
            vix['candles_current'] = False
    health['ready'] = stocks['status'] == 'current' and vix['status'] == 'current'


def quote_health(quote, now, *, market_open, error=None):
    from .execution import checked_quote
    result = {'symbol': 'QQQ', 'status': 'unavailable', 'latest_at': None, 'bid': None, 'ask': None, 'error': error}
    if not quote:
        return result
    try:
        at=timestamp(quote['t']).isoformat()
        bid,ask=float(quote['bp']),float(quote['ap'])
        if not all(isfinite(price) and price>0 for price in (bid,ask)):
            raise ValueError('Invalid quote prices')
        result.update(latest_at=at, bid=bid, ask=ask)
        checked_quote(quote, now)
        result['status'] = 'current' if not error else 'unavailable'
    except (ValueError, KeyError, TypeError):
        result.update(status='stale' if market_open else 'market_closed',
                      error=error or ('Waiting for a fresh executable bid and ask' if market_open else None))
    return result
