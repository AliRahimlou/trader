"""Descriptive QQQ structure, independent of setup direction and order admission.

The last two strict, confirmed swing highs and lows define each frame. This is
an explicit measurement, not a recovered video formula or a forecast.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from .models import timestamp
from .strategy import fresh


ET = ZoneInfo('America/New_York')
METHOD = 'Two rising confirmed highs and lows: up; two falling highs and lows: down; otherwise mixed.'


def _closed_frame(market, minutes, now):
    if market is None:
        return [], None
    # Future candles cannot establish pivots or change a historical assessment.
    bars = [bar for bar in market.bars.get(minutes, []) if bar.end <= now]
    if any(bar.minutes != minutes for bar in bars):
        return [], 'History contains a candle with the wrong timeframe.'
    if any(a.end >= b.end for a, b in zip(bars, bars[1:])):
        return [], 'History contains duplicate or out-of-order candles.'
    return bars, None


def _gap_reason(bars, minutes, hourly):
    """Check observed-session continuity without inventing a holiday calendar.

    Full RTH hourly buckets are end-stamped 10:30 through 15:30 ET; early-close
    sessions can end after the 12:30 bucket. Four-hour buckets are two per
    session since 4.5.0: 09:30-13:30 and the closing bucket 13:30-16:00 (still
    tagged 240 minutes; an early close yields the buckets that fit, the last
    ending at the close). Completed hourly candles prove which four-hour
    buckets a session must have: a 13:30 hourly candle proves the first, any
    later hourly candle proves the closing one. An entirely absent session
    cannot be proven missing without the exchange calendar; service data
    health owns that check. This frame stays descriptive-only.
    """
    for previous, current in zip(bars, bars[1:]):
        left, right = previous.end.astimezone(ET), current.end.astimezone(ET)
        if minutes == 240:
            if left.date() == right.date():
                # Only the morning bucket may be followed by the closing bucket of its day.
                if (left.hour, left.minute) == (13, 30) and right > left:
                    continue
                return 'A completed candle is missing within an observed session.'
            # A new session begins with its first bucket: 13:30 or an earlier early close.
            if (right.hour, right.minute) <= (13, 30):
                continue
            return 'Candle spacing cannot establish continuous session history.'
        if current.end - previous.end == timedelta(minutes=minutes):
            continue
        if left.date() == right.date():
            return 'A completed candle is missing within an observed session.'
        valid_boundary = ((left.hour, left.minute) in ((12, 30), (15, 30))
                          and (right.hour, right.minute) == (10, 30))
        if not valid_boundary:
            return 'Candle spacing cannot establish continuous session history.'
    if bars and minutes == 240:
        by_day = {}
        for bar in bars:
            by_day.setdefault(bar.end.astimezone(ET).date(), []).append(bar.end.astimezone(ET))
        last_hourly_day = hourly[-1].end.astimezone(ET).date() if hourly else None
        for bar in hourly:
            if bar.end < bars[0].end:
                continue
            local = bar.end.astimezone(ET)
            ends = by_day.get(local.date(), [])
            if (local.hour, local.minute) == (13, 30) and not any((e.hour, e.minute) == (13, 30) for e in ends):
                return 'A four-hour candle is missing for an observed full session.'
            # The closing bucket is only proven due once a later session has been observed;
            # during the afternoon the hourly frame runs ahead of it by design.
            if ((local.hour, local.minute) > (13, 30) and local.date() < last_hourly_day
                    and not any(e >= local for e in ends)):
                return 'A four-hour closing candle is missing for an observed full session.'
    return None


def _frame(market, minutes, now, hourly, hourly_error, data_current):
    bars, error = _closed_frame(market, minutes, now)
    latest = bars[-1] if bars else None
    row = {'timeframe_minutes': minutes, 'label': 'Hourly structure' if minutes == 60 else 'Four-hour structure',
           'status': 'insufficient', 'direction': 'unknown', 'detail': 'Two confirmed swing highs and lows are required.',
           'as_of': now.isoformat(), 'latest_bar_at': latest.end.isoformat() if latest else None,
           'latest_close': latest.close if latest else None, 'structure_as_of': None,
           'pivots': {'highs': [], 'lows': []}, 'invalidation': None,
           'validation_price': None, 'validation_at': None,
           'descriptive_only': True, 'entry_veto': False}
    error = error or hourly_error or _gap_reason(bars, minutes, hourly)
    if error:
        row.update(status='invalid', detail=error)
        return row
    if not data_current:
        row.update(status='stale' if market else 'unavailable',
                   detail='Current completed QQQ hourly data is required to describe the market now.')
        return row
    for left, pivot, right in zip(bars, bars[1:], bars[2:]):
        if pivot.high > left.high and pivot.high > right.high:
            row['pivots']['highs'].append({'price': pivot.high, 'event_at': pivot.end.isoformat(),
                                          'confirmed_at': right.end.isoformat()})
        if pivot.low < left.low and pivot.low < right.low:
            row['pivots']['lows'].append({'price': pivot.low, 'event_at': pivot.end.isoformat(),
                                         'confirmed_at': right.end.isoformat()})
    highs, lows = (row['pivots'][kind][-2:] for kind in ('highs', 'lows'))
    row['pivots'].update(highs=highs, lows=lows)
    if len(highs) < 2 or len(lows) < 2:
        return row
    row['structure_as_of'] = max((pivot['confirmed_at'] for pivot in highs + lows), key=timestamp)
    rising = highs[-1]['price'] > highs[-2]['price'] and lows[-1]['price'] > lows[-2]['price']
    falling = highs[-1]['price'] < highs[-2]['price'] and lows[-1]['price'] < lows[-2]['price']
    direction = 'up' if rising else 'down' if falling else 'mixed'
    row.update(status='ready', direction=direction,
               detail=('Confirmed swing highs and lows are both rising.' if rising else
                       'Confirmed swing highs and lows are both falling.' if falling else
                       'Confirmed swing highs and lows do not share one direction.'))
    # Newer closed hourly prices can invalidate older four-hour structure;
    # never pretend that a four-hour candle is current just because the feed is.
    validation = max([latest, hourly[-1]], key=lambda bar: bar.end)
    row.update(validation_price=validation.close, validation_at=validation.end.isoformat())
    boundary = lows[-1]['price'] if rising else highs[-1]['price'] if falling else None
    if (rising and validation.close < boundary) or (falling and validation.close > boundary):
        row.update(direction='mixed',
                   detail='The latest completed price has broken the last confirmed swing ' + ('low.' if rising else 'high.'),
                   invalidation={'boundary': 'low' if rising else 'high', 'price': boundary,
                                 'at': validation.end.isoformat()})
    return row


def market_context(market, now):
    """Return descriptive 60/240-minute QQQ structure; never an entry veto.

    Freshness follows the hourly QQQ input, while each frame exposes its own
    candle timestamp. Missing/invalid inputs produce unknown, never a vote.
    The four-hour structure reads both session buckets (13:30 and the closing
    bucket), so afternoon extremes participate in confirmed swings.
    """
    hourly, error = _closed_frame(market, 60, now)
    error = error or _gap_reason(hourly, 60, hourly)
    if market is not None and market.symbol != 'QQQ':
        error = 'This measure requires QQQ, the declared Nasdaq ETF proxy.'
    data_current = bool(market and not error and fresh(market, now, 60))
    source = (market.source if market.source in ('alpaca_iex', 'alpaca_sip') else 'unrecognized') if market else None
    return {'symbol': 'QQQ', 'instrument_label': 'QQQ · Nasdaq ETF proxy',
            'source': source, 'as_of': now.isoformat(),
            'observed_at': market.observed_at.isoformat() if market else None,
            'descriptive_only': True, 'entry_veto': False, 'data_current': data_current,
            'status': 'current' if data_current else 'unavailable' if market is None else 'invalid' if error else 'stale',
            'detail': error or 'Describes confirmed price structure; it does not approve or block a trade.',
            'method': METHOD,
            'continuity_scope': 'Observed sessions only; full exchange-calendar coverage is checked separately by data health.',
            'frames': [_frame(market, minutes, now, hourly, error, data_current) for minutes in (60, 240)]}
