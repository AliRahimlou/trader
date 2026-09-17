import socket
import requests
import pytest
from research.offline import disconnected


def test_offline_guard_rejects_http_and_raw_connections():
    with disconnected():
        with pytest.raises(RuntimeError,match='Offline research'):
            requests.get('https://api.alpaca.markets/v2/account')
        with pytest.raises(RuntimeError,match='Offline research'):
            socket.create_connection(('127.0.0.1',1))
        with socket.socket() as connection:
            with pytest.raises(RuntimeError,match='Offline research'):
                connection.connect(('127.0.0.1',1))
