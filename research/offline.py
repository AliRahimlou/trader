"""Defense in depth: replay must fail if it attempts any network connection."""
from contextlib import contextmanager
from unittest.mock import patch


def _blocked(*args, **kwargs):
    raise RuntimeError('Offline research cannot connect to a network or broker')


@contextmanager
def disconnected():
    with patch('socket.socket.connect', _blocked), patch('socket.socket.connect_ex', _blocked), \
            patch('socket.create_connection', _blocked), patch('requests.sessions.Session.request', _blocked):
        yield
