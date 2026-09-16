from datetime import datetime, timezone
import json

import pytest
import requests

from pivot.insight_probe import BASE, MAX_BYTES, ProbeError, run_probe


class Response:
    def __init__(self, payload=None, status=200, raw=None):
        self.status_code = status
        self.raw = raw if raw is not None else json.dumps(payload or {}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.raw), chunk_size):
            yield self.raw[offset:offset + chunk_size]


class Session:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_probe_is_bounded_to_four_read_only_calls_and_cannot_enable_trading():
    session = Session([Response() for _ in range(4)])
    now = datetime(2026, 9, 16, 19, 30, tzinfo=timezone.utc)
    result = run_probe('test-only-key', session=session, clock=lambda: now)
    assert len(session.calls) == 4
    assert [url for url, _ in session.calls] == [BASE + path for path in (
        '/v3/symbols/CBOE%3AVIX/info', '/v3/symbols/CBOE%3AVIX/session',
        '/v3/symbols/quotes', '/v3/symbols/CBOE%3AVIX/series')]
    for _, options in session.calls:
        assert options['allow_redirects'] is False
        assert options['timeout'] == (3, 10)
        assert options['headers'] == {'Authorization': 'Bearer test-only-key'}
    assert session.calls[-1][1]['params']['bar_interval'] == 15
    assert result['execution_eligible'] is False
    assert result['validation']['status'] == 'invalid_sample'
    assert 'test-only-key' not in json.dumps(result)


@pytest.mark.parametrize('status', [301, 302, 401, 403, 429, 500])
def test_access_or_redirect_failure_stops_without_retry(status):
    session = Session([Response(status=status)])
    with pytest.raises(ProbeError, match=f'HTTP {status}'):
        run_probe('test-only-key', session=session)
    assert len(session.calls) == 1


@pytest.mark.parametrize('raw', [b'[]', b'not-json', b'{"value":NaN}', b'\xff'])
def test_invalid_response_does_not_reach_validation(raw):
    session = Session([Response(raw=raw)])
    with pytest.raises(ProbeError, match='retrieved or decoded'):
        run_probe('test-only-key', session=session)


def test_transport_error_redacts_secret_and_provider_details():
    session = Session([requests.ConnectionError('test-only-key private details')])
    with pytest.raises(ProbeError) as error:
        run_probe('test-only-key', session=session)
    assert 'test-only-key' not in str(error.value)
    assert 'private details' not in str(error.value)


def test_reflected_credential_cannot_be_saved():
    session = Session([Response({'token': 'test-only-key'})])
    with pytest.raises(ProbeError, match='contained credentials'):
        run_probe('test-only-key', session=session)


def test_response_size_is_bounded():
    session = Session([Response(raw=b'x' * (MAX_BYTES + 1))])
    with pytest.raises(ProbeError, match='size limit'):
        run_probe('test-only-key', session=session)


@pytest.mark.parametrize('key', [None, '', ' ', 'unsafe\nkey'])
def test_missing_or_malformed_key_never_requests_data(key):
    session = Session([])
    with pytest.raises(ProbeError, match='Configure'):
        run_probe(key, session=session)
    assert session.calls == []


def test_mid_probe_failure_stops_remaining_requests():
    session = Session([Response(), Response(), Response(status=403)])
    with pytest.raises(ProbeError):
        run_probe('test-only-key', session=session)
    assert len(session.calls) == 3
