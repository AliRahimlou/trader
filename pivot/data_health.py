"""Per-input diagnostics. A connected endpoint is not the same as usable data."""
from datetime import timedelta
from math import isfinite
from .models import MAG7, timestamp
from .strategy import closed, vix_candles_fresh
from .history_health import frame_gaps

LABELS = {15: '15-minute', 60: '1-hour', 240: '4-hour', 1440: 'Daily'}


def last_expected(sessions, minutes, now):
    """Last complete regular-session bucket, allowing 90 seconds for publication."""
    cutoff = now - timedelta(seconds=90)
    candidates = []
    for session in sessions.values():
        opening, closing = timestamp(session['open']), timestamp(session['close'])
        if minutes == 1440:
            if closing <= cutoff: candidates.append(closing)
            continue
        end = opening + timedelta(minutes=minutes)
        while end <= closing and end <= cutoff:
            candidates.append(end)
            end += timedelta(minutes=minutes)
    return max(candidates) if candidates else None


def stock_health(markets, sessions, now, *, error=None, fetch_seconds=None, refresh_mode=None, full_at=None):
    instruments = []
    for symbol in ('QQQ', *MAG7):
        market = markets.get(symbol)
        frames = []
        for minutes in ((15, 60, 240, 1440) if symbol == 'QQQ' else (15, 240)):
            bars = closed(market, minutes, now) if market else []
            latest = bars[-1].end if bars else None
            expected = last_expected(sessions, minutes, now)
            gaps = frame_gaps(bars, sessions, minutes, now)
            if not bars:
                status, reason = 'missing', 'No validated completed candles'
            elif expected and latest < expected:
                status, reason = 'stale', 'Latest completed candle is behind the exchange calendar'
            elif gaps['missing_count']:
                status, reason = 'incomplete', gaps['reason']
            elif len(bars) < 3 and minutes != 1440:
                status, reason = 'incomplete', 'More history is required for level comparisons'
            else:
                status, reason = 'current', 'Completed candles available'
            frames.append({'minutes': minutes, 'label': LABELS[minutes], 'status': status,
                           'reason': reason, 'count': len(bars), 'latest_at': latest.isoformat() if latest else None,
                           'expected_at': expected.isoformat() if expected else None,
                           'missing_count': gaps['missing_count'], 'first_missing_at': gaps['first_missing_at']})
        valid_age = market and 0 <= (now - market.observed_at).total_seconds() <= 90
        okay = bool(market and market.realtime and valid_age and all(f['status'] == 'current' for f in frames))
        instruments.append({'symbol': symbol, 'status': 'current' if okay else 'needs_attention',
                            'source': market.source if market else None, 'frames': frames,
                            'observed_at': market.observed_at.isoformat() if market else None})
    ready = not error and all(i['status'] == 'current' for i in instruments)
    source = next((m.source for m in markets.values()), None)
    return {'status': 'current' if ready else 'needs_attention', 'checked_at': now.isoformat(),
            'coverage': 'IEX only; one exchange' if source == 'alpaca_iex' else 'Consolidated US exchanges' if source == 'alpaca_sip' else 'Unavailable',
            'source': source, 'instruments': instruments, 'error': error, 'fetch_seconds': fetch_seconds,
            'refresh_mode': refresh_mode, 'last_full_refresh_at': full_at.isoformat() if full_at else None,
            'frame_policy': 'Full regular-session hourly and 4-hour buckets only; the short closing bucket is excluded. Daily candles cover the actual full session, including early closes.'}


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
        verified = (details or {}).get('timeframe') == 'REAL-TIME' and 0 <= (now-value_at).total_seconds() <= 90
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
        if not at or not 0 <= (now-timestamp(at)).total_seconds() <= 90:
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
