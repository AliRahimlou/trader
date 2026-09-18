"""Actual VIX history for analysis, with a separate current quote before entry."""
from datetime import datetime, timedelta, timezone

from .feeds import FeedError, ET
from .history_health import frame_gaps
from .insight_cache import _validate
from .insight_validation import _aware, _calendar
from .models import Bar, Market


def _payload(envelope):
    if envelope.get('status') not in ('current', 'cached') or not envelope.get('payload'):
        reason = {'budget_exhausted': 'The free VIX request allowance is exhausted',
                  'rate_limited': 'The free VIX request rate limit was reached',
                  'in_flight': 'A VIX refresh is already running',
                  'retry_wait': 'Waiting for the bounded VIX recovery time',
                  'recovery_budget_exhausted': 'The VIX recovery allowance is exhausted for this period',
                  'stale': 'The saved VIX quote is no longer current'}.get(
                      envelope.get('status'), 'Current InsightSentry VIX data is unavailable')
        raise FeedError(reason + '; new entries must wait')
    return envelope['payload'], _aware(envelope['received_at'])


def diagnostics(cache, now, history=None):
    """Only whitelisted fields reach the browser. This never fetches a quote."""
    state = cache.diagnostics(now)
    details = {'provider': 'insightsentry', 'source_symbol': 'CBOE:VIX',
               'entry_quote_required': True,
               'budget': {'used': state['budget_used'], 'limit': state['budget_limit'],
                          'remaining': max(0, state['budget_limit'] - state['budget_used']),
                          'reserved': state['reserved_requests'],
                          'actual_requests': state['requests_recorded'],
                          **{key: state[key] for key in ('recovery_month_used', 'recovery_month_limit',
                             'recovery_day_used', 'recovery_day_limit') if key in state}}}
    if history:
        details.update(history_status=history['status'], history_received_at=history.get('received_at'),
                       source_updated_at=history.get('source_updated_at'),
                       next_refresh_at=history.get('next_refresh_at'),
                       retry_at=history.get('retry_at'), retry_reason=history.get('retry_reason'))
    quote = cache.peek_quote(now)
    details.update(entry_quote_retry_at=quote.get('retry_at'),
                   entry_quote_retry_reason=quote.get('retry_reason'))
    if quote.get('payload'):
        try:
            _validate('quote', quote['payload'], _aware(quote['received_at']))
            row = quote['payload']['data'][0]
            details.update(latest_value=row['last_price'], latest_value_at=quote['source_updated_at'],
                           quote_status=quote['status'])
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            pass
    return details


def load_history(cache, now, sessions, *, clock=None, report=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    report = {} if report is None else report
    try:
        now = _aware(now)
        calendar = _calendar(sessions)
        if not calendar:
            raise ValueError('A trading calendar is required')
        first_day = now.astimezone(ET).date() - timedelta(days=14)
        scoped = {day.isoformat(): {'open': opening, 'close': closing}
                  for day, (opening, closing) in calendar.items() if day >= first_day}
        if not scoped:
            raise ValueError('Recent trading calendar missing')
        meta_envelope = cache.metadata(now)
        report.update(diagnostics(cache, _aware(clock())))
        report.update(retry_at=meta_envelope.get('retry_at'), retry_reason=meta_envelope.get('retry_reason'))
        info, meta_received = _payload(meta_envelope)
        checked_at = _aware(clock())
        if not timedelta(0) <= checked_at - meta_received < timedelta(hours=24):
            raise ValueError('Metadata verification expired')
        _validate('metadata', info, meta_received)
        history = cache.history(checked_at, scoped)
        report.update(diagnostics(cache, _aware(clock()), history))
        raw, received = _payload(history)
        checked_at = _aware(clock())
        if received > checked_at:
            raise ValueError('Future receipt')
        updated = _aware(_validate('history', raw, received))
        # Completion is fixed at original collection time. A cached forming bar
        # cannot become a completed bar just because the wall clock advanced.
        watermark = min(received, updated)
        bars = []
        for row in raw['series']:
            start = datetime.fromtimestamp(row['time'], timezone.utc)
            end = start + timedelta(minutes=15)
            session = scoped.get(start.astimezone(ET).date().isoformat())
            if (session and session['open'] <= start < end <= session['close']
                    and end <= watermark):
                bars.append(Bar(end, 15, row['open'], row['high'], row['low'], row['close']))
        gaps = frame_gaps(bars, scoped, 15, checked_at)
        if len(bars) < 3 or gaps['missing_count']:
            raise ValueError('Completed regular-session VIX candles are missing')
        deadline = min(bars[-1].end + timedelta(seconds=990), meta_received + timedelta(hours=24))
        market = Market('I:VIX', {15: bars}, 'insightsentry', True, received, valid_until=deadline)
        details = diagnostics(cache, checked_at, history)
        details.update(timeframe='REAL-TIME', delay_seconds=0,
                       metadata_received_at=meta_received.isoformat(),
                       completed_bars=len(bars), missing_count=0,
                       latest_candle_at=bars[-1].end.isoformat(),
                       market_open=any(s['open'] <= checked_at < s['close'] for s in scoped.values()))
        report.update(details)
        return market, details
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
        raise FeedError('InsightSentry VIX history could not be verified; waiting for complete actual-index data') from None


def confirm_quote(cache, now, *, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        envelope = cache.quote(_aware(now))
        raw, received = _payload(envelope)
        checked_at = _aware(clock())
        if received > checked_at:
            raise ValueError('Future receipt')
        updated = _aware(_validate('quote', raw, checked_at))
        return {'source': 'insightsentry', 'symbol': 'I:VIX', 'updated_at': updated.isoformat(),
                'value': raw['data'][0]['last_price'], 'delay_seconds': 0}
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, OSError):
        raise FeedError('Waiting for a fresh actual VIX quote within the free data allowance') from None
