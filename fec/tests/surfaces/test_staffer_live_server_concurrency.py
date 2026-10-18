"""The live server takes a browser's burst of parallel requests without refusing any (audit r4 mel-a F1), and serves
large workspace assets from a cache keyed by file version without changing a byte, an ETag or a refusal."""
import gzip
import hashlib
import http.client
import os
import selectors
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from fec.surfaces import staffer_live_server as live
from fec.surfaces.staffer_live_server import Handler, WorkspaceServer


def serve(server):
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    return thread


def stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def stalled(base):
    """The server class with its accept loop held for 0.4 s at the first connection, as the staffer service's work
    holds it in production, so a burst lands on the listen queue before anything is accepted."""
    class Stalled(base):
        stalls = 1

        def process_request(self, request, client_address):
            if self.stalls:
                self.stalls -= 1
                time.sleep(0.4)
            return super().process_request(request, client_address)
    return Stalled


def burst(port, path, n, timeout=30):
    """n connections opened back to back without waiting, each sending one GET; returns outcome -> count."""
    selector, outcome, pending = selectors.DefaultSelector(), {}, {}
    request = f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n'.encode()
    for _ in range(n):
        sock = socket.socket()
        sock.setblocking(False)
        sock.connect_ex(('127.0.0.1', port))
        pending[sock] = b''
        selector.register(sock, selectors.EVENT_WRITE)

    def done(sock, result):
        outcome[result] = outcome.get(result, 0) + 1
        selector.unregister(sock)
        sock.close()
        pending.pop(sock, None)

    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        for key, events in selector.select(timeout=0.5):
            sock = key.fileobj
            if events & selectors.EVENT_WRITE and key.events == selectors.EVENT_WRITE:
                error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    done(sock, 'refused' if error in (10061, 111) else f'error {error}')
                    continue
                try:
                    sock.sendall(request)
                except OSError as exc:
                    done(sock, f'send failed {getattr(exc, "winerror", None) or exc.errno}')
                    continue
                selector.modify(sock, selectors.EVENT_READ)
            elif events & selectors.EVENT_READ:
                try:
                    chunk = sock.recv(65536)
                except OSError as exc:
                    done(sock, f'reset {getattr(exc, "winerror", exc)}')
                    continue
                if chunk:
                    pending[sock] += chunk
                else:
                    done(sock, 'ok' if pending[sock].startswith(b'HTTP/1.0 200') else 'status')
    for sock in list(pending):
        done(sock, 'timeout')
    return outcome


@pytest.fixture
def approved(tmp_path, monkeypatch):
    assets = tmp_path / 'assets'
    (assets / 'approved' / 'data').mkdir(parents=True)
    (assets / 'approved' / 'small.js').write_bytes(b'window.small=1;\n')
    (assets / 'private.json').write_bytes(b'{"private":true}')
    monkeypatch.setattr(live, 'ASSETS', assets)
    live._approved_cache.clear()
    return assets / 'approved'


def test_the_listener_queue_holds_a_browser_burst():
    assert WorkspaceServer.request_queue_size >= 128
    assert WorkspaceServer.daemon_threads is True
    assert issubclass(WorkspaceServer, ThreadingHTTPServer)


def test_a_burst_during_a_stalled_accept_loop_is_queued_not_refused(approved, tmp_path):
    server = stalled(WorkspaceServer)(('127.0.0.1', 0), Handler)
    server.dashboard = SimpleNamespace(source_root=tmp_path)
    thread = serve(server)
    try:
        outcome = burst(server.server_address[1], '/staffer-live/approved/small.js', 80)
    finally:
        stop(server, thread)
    assert outcome == {'ok': 80}


@pytest.mark.skipif(os.name != 'nt', reason='Windows resets a connection that finds the listen queue full')
def test_negative_control_the_default_queue_refuses_that_burst(approved, tmp_path):
    """Proves the burst test can see the defect: socketserver's default backlog of 5 drops part of the same burst at
    the connection level. Windows 11 reports the drop at connect (10061, 'refused'); on the windows-2022 runner the same
    drop surfaced as a failed send (run 38978074293: ok 32, send failed 48). Either is the defect; a bad status or a
    timeout is not."""
    server = stalled(ThreadingHTTPServer)(('127.0.0.1', 0), Handler)
    assert server.request_queue_size == 5
    server.dashboard = SimpleNamespace(source_root=tmp_path)
    thread = serve(server)
    try:
        outcome = burst(server.server_address[1], '/staffer-live/approved/small.js', 80)
    finally:
        stop(server, thread)
    dropped = {k: v for k, v in outcome.items() if k != 'ok'}
    assert dropped and all(k == 'refused' or k.startswith(('send failed', 'reset')) for k in dropped), outcome


def get(port, path, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_large_assets_are_cached_per_version_with_unchanged_bytes_and_etags(approved, tmp_path):
    big = approved / 'original-desk.html'
    first = (b'<!doctype html><title>desk</title>' + os.urandom(200000).hex().encode())
    big.write_bytes(first)
    server = WorkspaceServer(('127.0.0.1', 0), Handler)
    server.dashboard = SimpleNamespace(source_root=tmp_path)
    thread = serve(server)
    port = server.server_address[1]
    path = '/staffer-live/approved/original-desk.html'
    try:
        status, headers, body = get(port, path, {'Accept-Encoding': 'gzip'})
        assert status == 200 and gzip.decompress(body) == first
        assert headers['ETag'] == '"clean-' + hashlib.sha256(first).hexdigest()[:24] + '"'
        assert headers['Content-Encoding'] == 'gzip' and headers['Cache-Control'] == 'no-cache'
        assert len(live._approved_cache) == 1
        status, headers2, plain = get(port, path)                       # a client without gzip gets the raw bytes
        assert status == 200 and plain == first and headers2['ETag'] == headers['ETag'] and 'Content-Encoding' not in headers2
        assert get(port, path, {'If-None-Match': headers['ETag']})[0] == 304
        assert len(live._approved_cache) == 1                          # every request after the first is a cache hit
        second = first.replace(b'desk', b'DESK', 1)                    # same size: only the version stamp differs
        big.write_bytes(second)
        stamp = big.stat().st_mtime_ns + 10_000_000
        os.utime(big, ns=(stamp, stamp))
        status, headers3, body = get(port, path, {'Accept-Encoding': 'gzip', 'If-None-Match': headers['ETag']})
        assert status == 200 and gzip.decompress(body) == second
        assert headers3['ETag'] == '"clean-' + hashlib.sha256(second).hexdigest()[:24] + '"' != headers['ETag']
        assert get(port, '/staffer-live/approved/data/../small.js')[0] == 200   # resolves inside approved/: served
        assert get(port, '/staffer-live/approved/../private.json')[0] == 404    # outside approved/: refused
        assert get(port, '/staffer-live/approved/missing.js')[0] == 404
    finally:
        stop(server, thread)


def test_the_cache_is_bounded_and_skips_a_file_rewritten_while_read(approved, monkeypatch):
    monkeypatch.setattr(live, 'APPROVED_CACHE_BYTES', 1)
    for name in ('a.js', 'b.js', 'c.js'):
        (approved / name).write_bytes(name.encode() * 1000)
        live.approved_payload(approved / name)
    assert len(live._approved_cache) == 1                               # oldest entries evicted down to the budget
    live._approved_cache.clear()
    target = approved / 'moving.js'
    target.write_bytes(b'x' * 1000)
    real = type(target).read_bytes

    def rewritten(self):
        data = real(self)
        if self == target:
            self.write_bytes(b'y' * 2000)
        return data
    monkeypatch.setattr(type(target), 'read_bytes', rewritten)
    zipped, _etag, size = live.approved_payload(target)
    assert gzip.decompress(zipped) == b'x' * 1000 and size == 1000
    assert not live._approved_cache                                     # served once, never cached
