"""Proof that the autouse fixture in conftest.py blocks real network access (a planted call must fail loudly)."""
import httpx
import pytest

from conftest import NetworkDisabled


def test_planted_network_call_fails_loudly():
    with pytest.raises((NetworkDisabled, httpx.ConnectError)) as ei:
        httpx.post("https://polygon.gateway.tenderly.co", json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}, timeout=5)
    chain, e = [], ei.value
    while e is not None:
        chain.append(e); e = e.__cause__ or e.__context__
    assert any(isinstance(x, NetworkDisabled) for x in chain), [type(x).__name__ for x in chain]


def test_fixture_is_what_blocks(monkeypatch):
    """Control: the error comes from the fixture's patched socket layer (not from DNS failure or a firewall)."""
    import socket
    with pytest.raises(NetworkDisabled):
        socket.getaddrinfo("polygon.gateway.tenderly.co", 443)
    with pytest.raises(NetworkDisabled):
        socket.create_connection(("127.0.0.1", 9))
