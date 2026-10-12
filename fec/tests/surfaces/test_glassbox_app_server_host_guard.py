"""The Glass Box server answers only requests addressed to its own loopback name, and never leaks a traceback."""
import http.client
import json
import threading

import pytest

from fec.surfaces import glassbox_app_server as gb


class _Store:
    events_dir = "events"

    def snapshot_rows(self):
        raise RuntimeError("secret-internal-path C:\\private\\thing")


@pytest.fixture
def port(monkeypatch):
    monkeypatch.setattr(gb.Handler, "store", _Store())
    monkeypatch.setattr(gb.Handler, "started_at", 0.0)
    server = gb.AppServer(("127.0.0.1", 0), gb.Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def get(port, path, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path, headers={"Host": host} if host is not None else {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1", "127.0.0.1:1", "127.0.0.1.evil.example:{port}"])
def test_foreign_host_is_refused(port, host):
    status, body = get(port, "/api/health", host.format(port=port))
    assert status == 403
    assert json.loads(body) == {"error": "Local access only."}


@pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "[::1]:{port}"])
def test_loopback_host_with_bound_port_is_served(port, host):
    status, body = get(port, "/api/health", host.format(port=port))
    assert status == 200
    assert json.loads(body)["port"] == port


def test_default_client_host_header_is_served(port):
    assert get(port, "/api/health")[0] == 200


def test_server_error_response_carries_no_traceback_and_logs_it_locally(port, capsys):
    status, body = get(port, "/api/rows?secret=token")
    assert status == 500
    assert json.loads(body) == {"error": "server", "detail": "Internal server error."}
    assert b"Traceback" not in body and b"secret-internal-path" not in body
    err = capsys.readouterr().err
    assert "Traceback" in err and "secret-internal-path" in err
    assert "token" not in err  # the query string (shutdown token lives there) is never logged
