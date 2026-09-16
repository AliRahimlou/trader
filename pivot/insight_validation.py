"""Pure validation of InsightSentry VIX samples, never an execution feed."""
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo


SYMBOL = 'CBOE:VIX'
EASTERN = ZoneInfo('America/New_York')
INTERVAL = timedelta(minutes=15)
ALLOWANCE = timedelta(seconds=90)
LIMITATIONS = (
    'A validated sample does not establish a continuous live feed.',
    'This diagnostic cannot enable trading or submit broker orders.',
    'Regular-session comparisons use 09:30–16:00 America/New_York; supplied calendars can shorten sessions.',
)


class _Invalid(ValueError):
    pass


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise _Invalid('Invalid numeric value in the VIX sample.')
    if positive and value <= 0:
        raise _Invalid('Invalid price in the VIX sample.')
    return value


def _epoch(value, *, milliseconds=False):
    value = _number(value)
    return datetime.fromtimestamp(value / 1000 if milliseconds else value, timezone.utc)


def _aware(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None or at.utcoffset() is None:
        raise _Invalid('An aware timestamp is required for validation.')
    return at.astimezone(timezone.utc)


def _fresh(at, now):
    if not timedelta(0) <= now - at <= ALLOWANCE:
        raise _Invalid('The VIX observation is stale or future-dated.')


def _regular_bounds(day):
    return (datetime.combine(day, time(9, 30), EASTERN).astimezone(timezone.utc),
            datetime.combine(day, time(16), EASTERN).astimezone(timezone.utc))


def _calendar(sessions):
    if sessions is None:
        return None
    if not isinstance(sessions, dict) or not sessions or len(sessions) > 10000:
        raise _Invalid('The supplied trading calendar is invalid.')
    result = {}
    for session in sessions.values():
        opening, closing = _aware(session['open']), _aware(session['close'])
        day = opening.astimezone(EASTERN).date()
        regular_open, regular_close = _regular_bounds(day)
        if (day.weekday() >= 5 or day in result or
                not regular_open <= opening < closing <= regular_close or
                opening.second or opening.microsecond or opening.minute % 15 or
                closing.second or closing.microsecond or closing.minute % 15):
            raise _Invalid('The supplied trading calendar is invalid.')
        result[day] = (opening, closing)
    return result


def _expected(calendar, now):
    cutoff = now - ALLOWANCE
    if calendar is None:
        # This fallback knows weekdays, not exchange holidays. It cannot verify
        # full calendar coverage; that limitation is exposed in every result.
        day = cutoff.astimezone(EASTERN).date()
        calendar = {day - timedelta(days=n): _regular_bounds(day - timedelta(days=n))
                    for n in range(8) if (day - timedelta(days=n)).weekday() < 5}
    expected = set()
    for opening, closing in calendar.values():
        end = opening + INTERVAL
        while end <= min(closing, cutoff):
            expected.add(end)
            end += INTERVAL
    return expected


def _result(status, message):
    return {'status': status, 'execution_eligible': False, 'source': 'insightsentry',
            'symbol': SYMBOL, 'message': message, 'checked_at': None,
            'quote': None, 'series': None, 'coverage_verified': False,
            'missing_count': None, 'first_missing_at': None,
            'limitations': list(LIMITATIONS)}


def validate_insight_vix(info, quotes, series, now, *, sessions=None):
    """Validate response content without network access, storage, or side effects.

    ``quotes`` is the full REST response containing a ``data`` list. Optional
    ``sessions`` uses the app's date-keyed mapping of aware ``open``/``close``
    timestamps. Calendar coverage means only the explicitly supplied sessions.
    Unknown input fields, including execution flags, never grant eligibility.
    """
    try:
        now = _aware(now)
        calendar = _calendar(sessions)
        if (not isinstance(info, dict) or info.get('code') != SYMBOL or
                info.get('error') or not isinstance(info.get('type'), str) or info['type'].upper() != 'INDEX'):
            raise _Invalid('The sample does not identify the actual Cboe VIX index.')
        if _number(info['delay_seconds']) != 0:
            raise _Invalid('The provider does not identify this VIX sample as real-time.')
        if (not isinstance(quotes, dict) or quotes.get('error') or not isinstance(quotes.get('data'), list) or
                len(quotes['data']) != 1):
            raise _Invalid('The VIX quote response is missing or ambiguous.')
        quote = quotes['data'][0]
        if not isinstance(quote, dict) or quote.get('code') != SYMBOL or quote.get('error'):
            raise _Invalid('The VIX quote response identifies a different or unavailable instrument.')
        if _number(quote['delay_seconds']) != 0:
            raise _Invalid('The provider does not identify this VIX sample as real-time.')
        value, quote_at = _number(quote['last_price'], positive=True), _epoch(quote['lp_time'])
        _fresh(quote_at, now)
        if quotes.get('last_update') is not None:
            _fresh(_epoch(quotes['last_update'], milliseconds=True), now)
        if (not isinstance(series, dict) or series.get('code') != SYMBOL or
                series.get('bar_type') != '15m' or series.get('error') or
                not isinstance(series.get('series'), list) or not 1 <= len(series['series']) <= 30000):
            raise _Invalid('The expected 15-minute VIX history is missing or invalid.')
        updated = _epoch(series['last_update'], milliseconds=True)
        _fresh(updated, now)
        watermark = min(now, updated)
        completed, represented, previous = [], {}, None
        partial_count = excluded_count = 0
        for raw in series['series']:
            start = _epoch(raw['time'])
            if (start.second or start.microsecond or start.minute % 15 or start > watermark or
                    (previous is not None and start <= previous)):
                raise _Invalid('VIX candle timestamps are invalid, duplicated, or unordered.')
            previous = start
            prices = {field: _number(raw[field], positive=True) for field in ('open', 'high', 'low', 'close')}
            if not prices['low'] <= min(prices['open'], prices['close']) <= max(prices['open'], prices['close']) <= prices['high']:
                raise _Invalid('VIX candle price bounds are invalid.')
            end = start + INTERVAL
            day = start.astimezone(EASTERN).date()
            opening, closing = _regular_bounds(day)
            if calendar is not None and day in calendar:
                opening, closing = calendar[day]
            if day.weekday() >= 5 or not opening <= start < end <= closing:
                excluded_count += 1
                continue
            if end > watermark:
                partial_count += 1
                continue
            completed.append(end)
            represented.setdefault(day, set()).add(end)
        bar_end = _epoch(series['bar_end'])
        if bar_end == previous + INTERVAL:
            end_convention = 'exclusive'
        elif bar_end == previous + INTERVAL - timedelta(seconds=1):
            end_convention = 'inclusive_second'
        else:
            raise _Invalid('The latest VIX bar end does not match its 15-minute interval.')
        if len(completed) < 3:
            raise _Invalid('At least three completed regular-session VIX candles are required.')
        expected = _expected(calendar, now)
        if not expected or completed[-1] < max(expected):
            raise _Invalid('The latest completed regular-session VIX candle is missing.')
        missing = expected - set(completed) if calendar is not None else set()
        # Also inspect represented days outside a supplied calendar range.
        for ends in represented.values():
            end = min(ends)
            while end <= max(ends):
                if end not in ends:
                    missing.add(end)
                end += INTERVAL
        result = _result('valid_sample', 'The recorded VIX sample passed the diagnostic checks; it is not an execution feed.')
        result.update(checked_at=now.isoformat(),
                      quote={'value': value, 'updated_at': quote_at.isoformat(),
                             'age_seconds': (now - quote_at).total_seconds(), 'delay_seconds': 0},
                      series={'bar_type': '15m', 'received_count': len(series['series']),
                              'completed_regular_count': len(completed), 'excluded_count': excluded_count,
                              'incomplete_count': partial_count, 'updated_at': updated.isoformat(),
                              'latest_completed_at': completed[-1].isoformat(),
                              'first_completed_at': completed[0].isoformat(),
                              'expected_latest_at': max(expected).isoformat(),
                              'bar_end': bar_end.isoformat(), 'bar_end_convention': end_convention},
                      coverage_verified=calendar is not None and not missing,
                      missing_count=len(missing), first_missing_at=min(missing).isoformat() if missing else None)
        if calendar is None:
            result['limitations'].append('Full exchange-calendar coverage is unverified; checks between represented candles do not detect entirely missing sessions or history edges.')
        else:
            result['limitations'].append('Calendar coverage is checked only for the supplied session range.')
        if missing:
            result.update(status='invalid_sample', message='Completed VIX candles are missing within the checked history.')
        return result
    except _Invalid as error:
        return _result('invalid_sample', str(error))
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, OSError):
        return _result('invalid_sample', 'The VIX sample is missing required fields or contains invalid values.')
