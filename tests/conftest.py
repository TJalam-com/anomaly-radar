"""All tests run with outbound network disabled (QA 2026-09-26): a test that touched the real RPC could trip a 429 and stop
a live sweep. httpx.MockTransport never opens sockets, so legitimate tests are unaffected; anything that tries to
connect fails loudly with NetworkDisabled."""
import socket

import pytest


class NetworkDisabled(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(self, *args, **kwargs):
        raise NetworkDisabled(f"network disabled in tests: connect{args}")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: refuse(None, *a))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: refuse(None, *a))   # no DNS either
    yield
