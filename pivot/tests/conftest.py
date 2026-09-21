import pytest
import requests
import socket

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args,**kwargs):
        raise AssertionError('Tests must not contact a live service')
    monkeypatch.setattr(requests.sessions.Session,'request',blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
