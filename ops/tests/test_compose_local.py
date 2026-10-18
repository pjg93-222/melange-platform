"""Policy tests for ops/compose.local.yaml (the local serve profile) and its loopback relay, ops/local/relay.py.

Skipped with a message when PyYAML is missing. No Docker needed: the file is parsed, never rendered."""
from __future__ import annotations

import importlib.util
import re
import socket
import threading
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
yaml = pytest.importorskip("yaml", reason="PyYAML not installed; local compose policy not checked")

DOC = yaml.safe_load((OPS / "compose.local.yaml").read_text(encoding="utf-8"))
PAULS_PORTS = {8767, 8775, 8777, 8801, 8802, 8806, 8807, 8812, 8823, 8841, 8843, 8876, 38443}


def test_every_service_is_hardened_and_reached_only_through_host_loopback():
    services = DOC["services"]
    assert "cloudflared" not in services
    for name, s in services.items():
        assert s["read_only"] is True, name
        assert s["user"] == "10001:10001", name
        assert s["cap_drop"] == ["ALL"], name
        assert s["security_opt"] == ["no-new-privileges:true"], name
        assert s["tmpfs"] and s["pids_limit"], name
        assert re.match(r"^\$\{IMAGE_[A-Z_]+:\?", s["image"]), name
        assert "privileged" not in s, name
        assert "network_mode" not in s or s["network_mode"].startswith("service:"), name
        env = s.get("environment", {})
        assert env.get("ACCESS_ENFORCE", "0") == "0", name
        assert not [k for k in env if k.startswith("ACCESS_") and k != "ACCESS_ENFORCE"], name
        for v in s.get("volumes", []):
            if isinstance(v, dict) and v["type"] == "bind":
                assert v["read_only"] is True, (name, v["source"])
        if "network_mode" in s:
            assert "ports" not in s, name
        for port in s.get("ports", []):
            m = re.fullmatch(r"127\.0\.0\.1:\$\{(LOCAL_[A-Z_]+_PORT):-(\d+)\}:\$\{\1:-\2\}", port)
            assert m, (name, port)
            assert 18000 <= int(m.group(2)) <= 18999 and int(m.group(2)) not in PAULS_PORTS, (name, port)
    for name, net in DOC["networks"].items():
        assert not net.get("internal"), name       # an internal network cannot publish a port


def test_the_relay_copies_bytes_unchanged_and_the_app_sees_a_loopback_peer():
    spec = importlib.util.spec_from_file_location("local_relay", OPS / "local" / "relay.py")
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    request = b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:18802\r\n\r\n"
    app = socket.create_server(("127.0.0.1", 0))
    seen = []

    def echo():
        conn, peer = app.accept()
        with conn:
            seen.append(peer[0])
            data = b""
            while chunk := conn.recv(65536):
                data += chunk
            conn.sendall(data)

    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        try:
            relay.serve(listener, app.getsockname()[1])
        except OSError:      # the listener is closed at the end of the test
            pass

    threading.Thread(target=echo, daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    try:
        with socket.create_connection(listener.getsockname(), timeout=5) as client:
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            echoed = b""
            while chunk := client.recv(65536):
                echoed += chunk
    finally:
        listener.close()
        app.close()
    assert echoed == request
    assert seen == ["127.0.0.1"]
