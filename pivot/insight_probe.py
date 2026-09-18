"""One-shot, read-only InsightSentry verification. Never enables an app feed."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values

from .insight_validation import validate_insight_vix

BASE = 'https://api.insightsentry.com'
MAX_BYTES = 2_000_000


class ProbeError(ValueError):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _get(session, key, path, params=None):
    try:
        with session.get(BASE + path, params=params,
                         headers={'Authorization': 'Bearer ' + key},
                         timeout=(3, 10), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ProbeError(f'InsightSentry returned HTTP {response.status_code}; verification stopped.',
                                 retryable=response.status_code == 429 or 500 <= response.status_code <= 599)
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=16384):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ProbeError('InsightSentry response exceeded the verification size limit.')
                chunks.append(chunk)
            raw = b''.join(chunks).decode('utf-8')
            if key in raw:
                raise ProbeError('Provider response contained credentials; it was not saved.')
            result = json.loads(raw, parse_constant=_reject_constant)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except ProbeError:
        raise
    except requests.RequestException:
        raise ProbeError('InsightSentry transport is temporarily unavailable.', retryable=True) from None
    except (ValueError, UnicodeError, TypeError):
        raise ProbeError('InsightSentry response could not be retrieved or decoded.') from None


def run_probe(key, *, session=None, clock=None):
    """Use exactly four data requests, stopping immediately on an access failure."""
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
        raise ProbeError('Configure INSIGHTSENTRY_API_KEY locally before running verification.')
    clock = clock or (lambda: datetime.now(timezone.utc))
    owned = session is None
    session = session or requests.Session()
    try:
        started = clock()
        info = _get(session, key, '/v3/symbols/CBOE%3AVIX/info')
        market_session = _get(session, key, '/v3/symbols/CBOE%3AVIX/session')
        quotes = _get(session, key, '/v3/symbols/quotes', {'codes': 'CBOE:VIX'})
        quote_received = clock()
        series = _get(session, key, '/v3/symbols/CBOE%3AVIX/series', {
            'bar_type': 'minute', 'bar_interval': 15, 'dp': 1000,
            'extended': 'false', 'abbr': 'false',
        })
        received = clock()
        return {
            'provider': 'InsightSentry', 'execution_eligible': False,
            'purpose': 'One-shot read-only verification; no trading feed is enabled',
            'started_at': started.isoformat(), 'received_at': received.isoformat(),
            'quote_received_at': quote_received.isoformat(), 'request_count': 4,
            'info': info, 'session': market_session, 'quotes': quotes, 'series': series,
            'validation': validate_insight_vix(info, quotes, series, received),
        }
    finally:
        if owned:
            session.close()


def main():
    root = Path(__file__).resolve().parents[1]
    key = os.environ.get('INSIGHTSENTRY_API_KEY') or dotenv_values(root / '.env').get('INSIGHTSENTRY_API_KEY')
    try:
        result = run_probe(key)
        output = root / 'runtime' / 'video-review-v2'
        output.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime('insightsentry-probe-%Y%m%dT%H%M%S%fZ.json')
        path = output / name
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
            handle.write('\n')
        print(json.dumps({'evidence_file': str(path), 'requests': 4, **result['validation']}, indent=2))
        return 0 if result['validation']['status'] == 'valid_sample' else 1
    except ProbeError as error:
        print(json.dumps({'status': 'unavailable', 'execution_eligible': False, 'message': str(error)}))
        return 1
    except OSError:
        print(json.dumps({'status': 'unavailable', 'execution_eligible': False,
                          'message': 'Could not read configuration or save private verification evidence.'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
