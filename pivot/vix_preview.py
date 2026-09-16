"""Validate a saved research sample without creating a market-data feed.

This module reads a local evidence file only. It cannot produce an executable
Market, refresh a remote source, or establish live-data readiness.
"""
from datetime import date, datetime, timedelta, timezone
import json
from math import isfinite
from pathlib import Path
import re
from zoneinfo import ZoneInfo


SOURCE_URL = 'https://www.cnbc.com/quotes/.VIX'
COMPARISON_URL = 'https://finance.yahoo.com/chart/%5EVIX'
SAMPLE_PATH = Path(__file__).resolve().parents[1] / 'runtime/video-review-v2/cnbc-vix-scrape-20260916.json'
MAX_BYTES = 1024 * 1024
EASTERN = ZoneInfo('America/New_York')
CHICAGO = ZoneInfo('America/Chicago')
PRICE_FIELDS = ('open', 'high', 'low', 'close')
LIMITATIONS = (
    'Historical sample only; automatic collection is not running.',
    'Only recorded intervals have been checked; 14-calendar-day coverage is not verified.',
    'Chart timezone and interval-start interpretation were inferred, not confirmed by the provider.',
    'There is no provider publication watermark confirming candle completion or live freshness.',
    'Historical price matches do not establish continuous feed reliability.',
    'Ongoing CNBC collection requires provider authorization.',
)


def _result(status):
    messages = {
        'historical_sample': 'Saved chart observations only. This is not a live VIX feed and cannot enable trading.',
        'unavailable': 'The saved VIX research sample is unavailable.',
        'invalid': 'The saved VIX research sample could not be validated.',
    }
    return {
        'status': status, 'execution_eligible': False,
        'title': 'CNBC VIX research sample', 'message': messages[status],
        'captured_at': None, 'source_url': SOURCE_URL, 'quote': None,
        'candles': [], 'candle_count': 0, 'first_label': None, 'last_label': None,
        'overlap_matches': 0, 'overlap_count': 0, 'limitations': list(LIMITATIONS),
    }


def _aware(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError('Invalid recorded timestamp')
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Recorded timestamp requires a timezone')
    return at.astimezone(timezone.utc)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Invalid recorded date')
    return date.fromisoformat(value)


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('A numeric price is required')
    if not isfinite(value) or value <= 0:
        raise ValueError('Invalid price')
    return value


def _ohlc(item):
    values = {field: _price(item[field]) for field in PRICE_FIELDS}
    if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
        raise ValueError('Invalid price bounds')
    return values


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Invalid clock label')
    hour, minute = map(int, value.split(':'))
    if minute % 15:
        raise ValueError('Candle is not on a 15-minute boundary')
    return hour, minute


def _comparison_time(value, recorded_date, zone):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2} [A-Z]{3}', value):
        raise ValueError('Invalid comparison time')
    label, abbreviation = value.split()
    hour, minute = _clock(label)
    at = datetime.combine(recorded_date, datetime.min.time(), zone).replace(hour=hour, minute=minute)
    if abbreviation != at.tzname():
        raise ValueError('Comparison timezone does not match its date')
    return at


def preview_from_document(document, now):
    """Return display-only evidence or a sanitized validation failure.

    Price continuity is checked within each captured day. Cross-session gaps,
    full historical coverage, and candle finality are deliberately not inferred.
    Input fields claiming live status or eligibility are never used.
    """
    try:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Current time requires a timezone')
        now = now.astimezone(timezone.utc)
        if not isinstance(document, dict) or document['source'] != SOURCE_URL:
            raise ValueError('Unexpected source')
        if document['instrument'] != 'CBOE Volatility Index .VIX:Exchange':
            raise ValueError('Unexpected instrument')
        recorded_date = _date(document['date'])
        capture = document['candles_capture']
        start, end = _aware(capture['start']), _aware(capture['end'])
        if not start <= end <= now or end.astimezone(EASTERN).date() != recorded_date:
            raise ValueError('Invalid capture interval')
        quote = document['quote_sample']
        quote_at = _aware(quote['observed_at'])
        if quote_at > end or quote_at.astimezone(EASTERN).date() != recorded_date:
            raise ValueError('Invalid quote observation')
        display_time = quote['display_time']
        if not isinstance(display_time, str) or not 1 <= len(display_time) <= 120:
            raise ValueError('Invalid quote display label')
        quote_preview = {
            'value': _price(quote['value']), 'display_time': display_time,
            'observed_at': quote_at.isoformat(), 'status': 'historical',
        }
        raw_candles = document['candles']
        if not isinstance(raw_candles, list) or not 1 <= len(raw_candles) <= 10000:
            raise ValueError('Invalid candle collection')
        candles, indexed, previous = [], {}, None
        for item in raw_candles:
            if not isinstance(item, dict) or type(item['interval_minutes']) is not int or item['interval_minutes'] != 15:
                raise ValueError('Unexpected candle interval')
            day_label = item['display_date']
            if not isinstance(day_label, str) or not re.fullmatch(r'\d{2}/\d{2}', day_label):
                raise ValueError('Invalid candle date label')
            month, day = map(int, day_label.split('/'))
            candle_date = date(recorded_date.year, month, day)
            if candle_date > recorded_date:
                raise ValueError('Candle is after the capture date')
            hour, minute = _clock(item['display_time'])
            label_time = datetime(candle_date.year, month, day, hour, minute)
            if previous and (label_time <= previous or
                             (label_time.date() == previous.date() and label_time - previous != timedelta(minutes=15))):
                raise ValueError('Unordered, duplicate, or missing candle labels')
            # This bounds the inferred chart time; it does not prove candle finality.
            if label_time.replace(tzinfo=EASTERN) > end:
                raise ValueError('Candle label is after capture')
            previous = label_time
            prices = _ohlc(item)
            candles.append({'display_date': day_label, 'display_time': item['display_time'],
                            'interval_minutes': 15, **prices})
            indexed[(candle_date, item['display_time'])] = prices
        checks = document['overlap_checks']
        if not isinstance(checks, list) or len(checks) > len(candles):
            raise ValueError('Invalid comparisons')
        compared = set()
        for check in checks:
            if not isinstance(check, dict) or check['source'] != COMPARISON_URL or check['chart_timezone'] != 'America/Chicago':
                raise ValueError('Unexpected comparison source')
            check_date = _date(check['date'])
            check_at = _aware(check['captured_at'])
            if check_at > end or check_at.astimezone(EASTERN).date() != recorded_date:
                raise ValueError('Invalid comparison capture time')
            chicago_at = _comparison_time(check['display_time'], check_date, CHICAGO)
            eastern_at = _comparison_time(check['eastern_time'], check_date, EASTERN)
            if chicago_at != eastern_at or eastern_at > check_at:
                raise ValueError('Comparison clocks disagree')
            key = (eastern_at.date(), eastern_at.strftime('%H:%M'))
            if key in compared or key not in indexed or _ohlc(check) != indexed[key]:
                raise ValueError('Comparison is duplicated, missing, or mismatched')
            compared.add(key)
        result = _result('historical_sample')
        result.update(captured_at=end.isoformat(), quote=quote_preview, candles=candles,
                      candle_count=len(candles),
                      first_label=f"{candles[0]['display_date']} {candles[0]['display_time']}",
                      last_label=f"{candles[-1]['display_date']} {candles[-1]['display_time']}",
                      overlap_matches=len(compared), overlap_count=len(checks))
        return result
    except (KeyError, TypeError, ValueError, OverflowError):
        return _result('invalid')


def load_preview(path=None, now=None):
    """Read at most 1 MiB from the fixed evidence file (a path is injectable for tests)."""
    now = datetime.now(timezone.utc) if now is None else now
    try:
        with Path(SAMPLE_PATH if path is None else path).open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
    except (OSError, TypeError, ValueError):
        return _result('unavailable')
    if len(content) > MAX_BYTES:
        return _result('invalid')
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _result('invalid')
    return preview_from_document(document, now)
