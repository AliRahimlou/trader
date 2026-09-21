"""Release verification must never reach a provider, even through another client."""
import socket
import pytest
import requests


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Deployment tests must not contact a network or broker')
    monkeypatch.setattr(requests.sessions.Session, 'request', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
