"""The press recovery display answers only requests addressed to its own loopback name."""
import http.client
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collect import press_recovery_monitor as pm


@pytest.fixture
def port(monkeypatch, tmp_path):
    """Run the real main() on port 0 so the real nested handler answers."""
    servers = []

    class Captured(pm.ThreadingHTTPServer):
        def __init__(self, address, handler):
            super().__init__(("127.0.0.1", 0), handler)
            servers.append(self)

    monkeypatch.setattr(pm, "ThreadingHTTPServer", Captured)
    monkeypatch.setattr(sys, "argv", ["press_recovery_monitor", "--output", str(tmp_path)])
    thread = threading.Thread(target=pm.main, daemon=True)
    thread.start()
    while not servers and thread.is_alive():
        thread.join(timeout=.01)
    assert servers, "main() exited before binding"
    yield servers[0].server_address[1]
    servers[0].shutdown()
    servers[0].server_close()
    thread.join(timeout=2)


def get(port, path, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path, headers={"Host": host} if host is not None else {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1", "127.0.0.1:1"])
def test_foreign_host_is_refused(port, host):
    for path in ("/health", "/api/state", "/receipt"):
        status, body = get(port, path, host.format(port=port))
        assert status == 403
        assert b"melange-collection-monitor" not in body


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "[::1]:{port}", None])
def test_loopback_host_with_bound_port_is_served(port, host):
    status, body = get(port, "/health", host.format(port=port) if host else None)
    assert status == 200
    assert b"melange-collection-monitor" in body


def test_valid_host_unit():
    assert pm.valid_host("127.0.0.1:8876", 8876) and pm.valid_host("LOCALHOST:8876", 8876)
    assert not pm.valid_host(None, 8876) and not pm.valid_host("", 8876)
    assert not pm.valid_host("evil.example:8876", 8876) and not pm.valid_host("127.0.0.1:8877", 8876)
