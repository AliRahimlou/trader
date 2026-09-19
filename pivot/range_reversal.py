"""Signal interpretation of the September 19 four-hour-range video.

This pure analyzer never authorizes orders. The day anchor and first-outside-bar
stop are declared provisional conventions, not recovered creator formulas.
Only native five-minute data is admitted; missing prices are never invented.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from zoneinfo import ZoneInfo

from .models import Market, timestamp

NY = ZoneInfo('America/New_York')
UTC = timezone.utc
RULE_VERSION = 'range-reversal-v1'
FAMILY_ID = 'range_reversal'
SOURCES = {'alpaca_crypto', 'alpaca_crypto_us', 'alpaca_iex', 'alpaca_sip'}
STEP = timedelta(minutes=5)
MAX_RECEIPT_SECONDS = 90
PUBLICATION_GRACE_SECONDS = 90
INTERPRETATION_WARNINGS = (
    'Signals require separate account, cost, execution and ownership checks before an order. Signal detection alone is not an order.',
    'Provisional range convention: New York midnight plus four elapsed hours. Selecting New York display time does not establish the creator’s four-hour candle anchor. DST changes the displayed range-end hour.',
    'Provisional stop convention: the extreme of the first five-minute candle closing outside the range. The narration also refers to the breakout move; those interpretations can differ.',
    'The discretionary closer stop for a large excursion has no numerical definition and is not implemented.',
    'Daily or weekly trend preference is optional in the narration and is not an entry filter here.',
    'A close beyond the opposite boundary without a close inside resets the pending excursion; this is an explicit interpretation.',
    'Repeated fresh excursions are separate opportunities. Position ownership and shared buying power are managed separately.',
)


def _iso(value):
    return value.astimezone(UTC).isoformat()


def _base(now):
    return {'family_id': FAMILY_ID, 'label': '4H Range Reversal',
            'rule_version': RULE_VERSION, 'signal_ready': False, 'signal_valid_until': None,
            'execution_status': 'signal', 'state': 'DATA_WAITING',
            'detail': 'Waiting for validated native five-minute data.',
            'analyzed_at': _iso(now), 'symbol': None, 'source': None,
            'observed_at': None, 'latest_bar_at': None, 'range': None,
            'observations': [], 'candidates': [], 'current_event': None,
            'interpretation_warnings': list(INTERPRETATION_WARNINGS)}


def _valid_bar(bar):
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    return (type(bar.minutes) is int and bar.minutes == 5
            and all(not isinstance(v, bool) and isinstance(v, (int, float)) and isfinite(v) for v in values)
            and bar.volume >= 0
            and 0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high)


def analyze(market: Market | None, now, *, provenance=None):
    """Return source-stamped observations, never an executable signal.

    ``provenance`` must explicitly describe native five-minute bars with source,
    symbol, timeframe_minutes=5 and native=True. Market.bars[5] contains aware,
    end-stamped bars; extra derived frames are ignored. Complete day-to-current
    coverage is required, with a 90-second publication allowance for only the
    newest completed candle. ``now`` must be timezone-aware.
    """
    now = timestamp(now).astimezone(UTC)
    result = _base(now)
    local = now.astimezone(NY)
    start = datetime.combine(local.date(), datetime.min.time(), NY).astimezone(UTC)
    day_end = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), NY).astimezone(UTC)
    range_end = start + timedelta(hours=4)
    result['session'] = {'date': local.date().isoformat(), 'timezone': str(NY),
                         'start_at': _iso(start), 'end_at': _iso(day_end),
                         'range_end_at': _iso(range_end), 'anchor': 'provisional_new_york_midnight',
                         'range_duration_minutes': 240}
    try:
        if (not isinstance(market, Market) or market.realtime is not True
                or market.source not in SOURCES or not isinstance(market.symbol, str) or not market.symbol
                or not isinstance(provenance, dict)
                or provenance.get('source') != market.source
                or provenance.get('symbol') != market.symbol
                or type(provenance.get('timeframe_minutes')) is not int
                or provenance['timeframe_minutes'] != 5 or provenance.get('native') is not True):
            result['detail'] = 'A verified native five-minute source is required; derived or substitute candles are not accepted.'
            return result
        observed = timestamp(market.observed_at).astimezone(UTC)
        result.update(symbol=market.symbol, source=market.source, observed_at=_iso(observed))
        if not 0 <= (now - observed).total_seconds() <= MAX_RECEIPT_SECONDS:
            result['detail'] = 'The provider receipt is stale or in the future; waiting for current data.'
            return result
        raw = market.bars.get(5)
        if not isinstance(raw, list):
            result['detail'] = 'Native five-minute candles are missing.'
            return result
        # Inspect timestamps and native frames before filtering. Future candle
        # prices are never used to mark the range or produce an observation.
        stamps = [timestamp(bar.end).astimezone(UTC) for bar in raw]
        if (any(type(bar.minutes) is not int or bar.minutes != 5 for bar in raw)
                or any(a >= b for a, b in zip(stamps, stamps[1:]))
                or any(at.second or at.microsecond or at.minute % 5 for at in stamps)):
            result['detail'] = 'Five-minute candles have duplicate, unordered or off-grid timestamps, or the wrong timeframe.'
            return result
        bars = [(bar, at) for bar, at in zip(raw, stamps) if start < at <= now and at <= day_end]
        if any(not _valid_bar(bar) for bar, _ in bars):
            result['detail'] = 'Five-minute candle prices are invalid.'
            return result
        if any(at > observed for _, at in bars):
            result['detail'] = 'A completed candle is dated after its provider receipt; waiting for consistent source timestamps.'
            return result
        if market.valid_until is not None and timestamp(market.valid_until).astimezone(UTC) <= now:
            result['detail'] = 'The validated source deadline has expired; waiting for current data.'
            return result
        expected = start + int((now - start).total_seconds() // 300) * STEP
        latest = bars[-1][1] if bars else start
        if bars:
            result['latest_bar_at'] = _iso(latest)
        publication_wait = (expected > start and latest == expected - STEP
                            and 0 <= (now - expected).total_seconds() <= PUBLICATION_GRACE_SECONDS)
        if latest != expected and not publication_wait:
            result['detail'] = 'The latest completed five-minute candle is missing or stale.'
            return result
        if (len(bars) != int((latest - start).total_seconds() // 300)
                or any(at != start + (index + 1) * STEP for index, (_, at) in enumerate(bars))):
            result['detail'] = 'Five-minute coverage has a gap. Waiting for complete native candles; no prices are filled in.'
            return result
        if now < range_end or latest < range_end:
            result.update(state='RANGE_FORMING',
                          detail='Waiting for all 48 native five-minute candles of the first four-hour range to close.')
            return result
        opening = [bar for bar, at in bars if at <= range_end]
        if len(opening) != 48:
            result['detail'] = 'The first four-hour range requires exactly 48 completed native five-minute candles.'
            return result
        high, low = max(bar.high for bar in opening), min(bar.low for bar in opening)
        result['range'] = {'high': high, 'low': low, 'start_at': _iso(start), 'end_at': _iso(range_end),
                           'native_candle_count': 48, 'provisional': True}
        if high <= low:
            result['detail'] = 'The completed four-hour range has no price width.'
            return result
        result.update(state='WATCHING', detail='Watching for a five-minute close outside the range, then a later close strictly inside.')
        pending = None
        for bar, at in bars[48:]:
            side = 'above' if bar.close > high else 'below' if bar.close < low else None
            inside = low < bar.close < high
            if pending and side and side != pending['breakout_side']:
                pending.update(status='INVALIDATED', invalidated_at=_iso(at),
                               detail='Price closed beyond the opposite boundary without first closing inside the range.')
                pending = None
            if pending is None and side:
                identity = '|'.join((RULE_VERSION, market.source, market.symbol, _iso(start), _iso(at), side))
                pending = {'event_id': 'rr1_' + sha256(identity.encode()).hexdigest()[:24],
                           'status': 'WAITING_FOR_REENTRY', 'symbol': market.symbol, 'source': market.source,
                           'breakout_side': side, 'direction': 'short' if side == 'above' else 'long',
                           'breakout_at': _iso(at), 'breakout_close': bar.close,
                           'stop': bar.high if side == 'above' else bar.low,
                           'stop_basis': 'first_outside_candle_extreme', 'confirmation_at': None,
                           'entry': None, 'target': None, 'current': False,
                           'signal_ready': False, 'entry_valid_until': None, 'rule_version': RULE_VERSION}
                result['observations'].append(pending)
            elif pending and inside:
                risk = abs(bar.close - pending['stop'])
                target = bar.close + (2 * risk if pending['direction'] == 'long' else -2 * risk)
                pending.update(confirmation_at=_iso(at), entry=bar.close, target=target,
                               risk_per_unit=risk, reward_to_risk=2, current=at == latest,
                               observed_at=_iso(observed))
                deadline = min(at + timedelta(seconds=90), observed + timedelta(seconds=90), day_end)
                pending.update(entry_valid_until=_iso(deadline), signal_ready=at == latest and now < deadline)
                if risk <= 0 or not isfinite(target) or target <= 0:
                    pending.update(status='INVALID_GEOMETRY', signal_ready=False, detail='The declared stop and 2R target do not produce valid positive prices.')
                else:
                    pending.update(status='CONFIRMED', detail='A completed outside close was followed by a later completed close strictly inside.')
                    result['candidates'].append(pending.copy())
                pending = None
        if pending:
            pending['current'] = True
            result.update(state='OUTSIDE_RANGE', current_event=pending.copy(),
                          detail='An outside close is recorded. Waiting for a later five-minute close strictly back inside the range.')
        elif result['observations'] and result['observations'][-1].get('current'):
            event = result['observations'][-1]
            result['current_event'] = event.copy()
            result.update(state='SETUP_OBSERVED' if event['status'] == 'CONFIRMED' else 'DATA_WAITING',
                          signal_ready=event['status'] == 'CONFIRMED' and event['signal_ready'],
                          signal_valid_until=event.get('entry_valid_until'),
                          detail=(('A reversal has confirmed. The execution worker checks account, price, costs and ownership before entry.' if event['signal_ready'] else 'This reversal’s entry window has expired. Waiting for a new outside-and-inside cycle.')
                                  if event['status'] == 'CONFIRMED' else event['detail']))
        return result
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        result.update(state='DATA_WAITING', detail='Native candle metadata or prices could not be validated.',
                      signal_ready=False, signal_valid_until=None, range=None, observations=[], candidates=[], current_event=None)
        return result
