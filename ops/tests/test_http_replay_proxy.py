"""Tests for ops/compute/http_replay.py's proxy mode (R, Node and any client that honours HTTPS_PROXY), against fake HTTP and
HTTPS servers on loopback. No real network: the "upstream" is a ThreadingHTTPServer, TLS certificates come from the harness's own
throwaway CA (so the proxy-facing CA and the upstream CA are two different authorities, as in real life).

Clients: Python's urllib driven only by environment variables (what an unmodified R/curl child sees), http.client with a tunnel
and keep-alive, and the curl command line when present (libcurl is what R's httr uses).
"""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("cryptography", reason="proxy mode needs cryptography")

OPS = Path(__file__).resolve().parents[1]
RUNNER = OPS / "compute" / "http_replay.py"
_spec = importlib.util.spec_from_file_location("http_replay", RUNNER)
HR = importlib.util.module_from_spec(_spec)
sys.modules["http_replay"] = HR
_spec.loader.exec_module(HR)

SECRET = "tok-" + "1a2b3c4d5e6f" * 2
BINARY = bytes(range(256)) * 5
BIG = b"0123456789abcdef" * 4096


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        srv = self.server
        srv.hits.append(self.path)
        parts = urlsplit(self.path)
        if parts.path == "/ok":
            self._send(200, b'{"ok": true}', [("Content-Type", "application/json"), ("Set-Cookie", "sid=" + SECRET), ("X-Multi", "a"), ("X-Multi", "b")])
        elif parts.path == "/redir":
            self._send(302, b"", [("Location", "/ok")])
        elif parts.path == "/missing":
            self._send(404, b"not here")
        elif parts.path == "/seq":
            srv.seq += 1
            self._send(200, str(srv.seq).encode())
        elif parts.path == "/binary":
            self._send(200, BINARY, [("Content-Type", "application/octet-stream")])
        elif parts.path == "/gz":
            self._send(200, gzip.compress(b"compressed body " * 50, mtime=0), [("Content-Encoding", "gzip"), ("Content-Type", "text/plain")])
        elif parts.path == "/chunked":
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(0, len(BIG), 10000):
                piece = BIG[i:i + 10000]
                self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
            self.wfile.write(b"0\r\n\r\n")
        elif parts.path == "/echo-key":
            self._send(200, ("you sent " + parse_qs(parts.query)["api_key"][0]).encode())
        else:
            self._send(404, b"unknown")

    def do_HEAD(self):
        self.server.hits.append("HEAD " + self.path)
        self.send_response(200)
        self.send_header("Content-Length", "4321")
        self.send_header("X-Head", "yes")
        self.end_headers()

    def do_POST(self):
        self.server.hits.append(self.path)
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self._send(200, hashlib.sha256(data).hexdigest().encode())


def _serve(tmp_path, tls: bool):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.hits, srv.seq, srv.ca = [], 0, None
    if tls:
        authority = HR.Authority(tmp_path / "upstream-ca")
        srv.socket = authority.context_for("127.0.0.1").wrap_socket(srv.socket, server_side=True)
        srv.ca = authority.ca_pem
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base = f"{'https' if tls else 'http'}://127.0.0.1:{srv.server_address[1]}"
    return srv


@pytest.fixture()
def https_server(tmp_path):
    srv = _serve(tmp_path, tls=True)
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def http_server(tmp_path):
    srv = _serve(tmp_path, tls=False)
    yield srv
    srv.shutdown()
    srv.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Session:
    """A recorded or replayed proxy for the duration of a with-block, plus a urllib client that talks only to it."""

    def __init__(self, mode, directory, upstream_ca=None, secrets=()):
        self.harness = HR.Harness(mode, directory, secrets=list(secrets), patch=False)
        self.dir = Path(directory)
        self.upstream_ca = upstream_ca

    def __enter__(self):
        self.tmp = self.dir.parent / (self.dir.name + "-ca-" + self.harness.mode)
        self.harness.__enter__()
        self.authority = HR.Authority(self.tmp)
        self.proxy = HR.ProxyServer(self.harness, self.authority, self.upstream_ca).start()
        ctx = ssl.create_default_context(cafile=str(self.authority.ca_pem))
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.ProxyHandler({"http": self.proxy.url, "https": self.proxy.url}), urllib.request.HTTPSHandler(context=ctx))
        self.ctx = ctx
        return self

    def __exit__(self, *exc):
        self.proxy.stop()
        return self.harness.__exit__(*exc)

    def get(self, url, data=None, headers=()):
        req = urllib.request.Request(url, data=data, headers=dict(headers))
        try:
            resp = self.opener.open(req, timeout=15)
        except urllib.error.HTTPError as err:
            resp = err
        body = resp.read()
        status = getattr(resp, "status", None) or resp.code
        keep = {k.lower(): v for k, v in resp.headers.items() if k.lower() in ("content-type", "location", "content-encoding", "x-head", "x-replay-miss", "x-replay-recorded-error")}
        return status, keep, body


def drive(session, base):
    """The requests a collector would make: JSON with a key in the URL and an Authorization header, a redirect it does not
    follow, a 404, a counter read three times, a POST, binary, gzip, chunked and a HEAD."""
    out = []
    out.append(session.get(f"{base}/ok?b=2&api_key={SECRET}&a=1", headers={"Authorization": "Bearer " + SECRET, "X-Api-Key": SECRET}))
    out.append(session.get(f"{base}/redir"))
    out.append(session.get(f"{base}/missing"))
    out += [session.get(f"{base}/seq") for _ in range(3)]
    out.append(session.get(f"{base}/echo-key?api_key={SECRET}"))
    out.append(session.get(f"{base}/binary"))
    out.append(session.get(f"{base}/gz"))
    out.append(session.get(f"{base}/chunked"))
    out.append(session.get(f"{base}/seq", data=b"post-body"))
    return out


def tree_bytes(directory: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file())


# ---------------------------------------------------------------- record, then replay with the upstream gone

@pytest.mark.parametrize("scheme", ["https", "http"])
def test_replay_serves_the_recorded_responses_byte_for_byte_and_never_touches_the_upstream(scheme, https_server, http_server, tmp_path):
    server = https_server if scheme == "https" else http_server
    cassette = tmp_path / "cas"
    with Session("record", cassette, server.ca, secrets=[SECRET]) as rec:
        live = drive(rec, server.base)
    assert [status for status, _, _ in live][:3] == [200, 302, 404] and len(server.hits) == 11
    assert live[0][2] == b'{"ok": true}' and live[1][1]["location"] == "/ok" and live[9][2] == BIG   # the chunked body arrives whole
    assert gzip.decompress(live[8][2]).startswith(b"compressed body")          # the client decodes: the proxy passed the raw gzip through
    assert live[6][2] == b"you sent " + SECRET.encode()                        # the collector saw the live bytes ...
    hits = len(server.hits)
    with Session("replay", cassette, secrets=[SECRET]) as rep:
        again = drive(rep, server.base)
        report = rep.harness.report()
    expected = list(live)
    expected[6] = (200, live[6][1], b"you sent REDACTED")                      # ... the cassette holds the scrubbed copy
    assert again == expected                                                   # status, the headers a collector reads, and every body byte
    assert len(server.hits) == hits                                            # replay never reached the upstream
    assert report["misses"] == [] and report["unused"] == [] and report["served"] == 11
    assert SECRET.encode() not in tree_bytes(cassette)                         # URL parameter, Authorization, X-Api-Key, Set-Cookie, reflected body


def test_the_cassette_keys_are_the_in_process_hooks_keys_so_either_hook_can_replay_the_other(https_server, tmp_path):
    cassette = tmp_path / "cas"
    with Session("record", cassette, https_server.ca, secrets=[SECRET]) as rec:
        rec.get(f"{https_server.base}/ok?b=2&api_key={SECRET}&a=1")
    (line,) = [json.loads(x) for x in (cassette / "cassette.jsonl").read_text(encoding="utf-8").splitlines()]
    assert line["key"] == f"GET {https_server.base}/ok?a=1&api_key=REDACTED&b=2 -"
    headers = {k.lower(): v for k, v in line["response"]["headers"]}
    assert headers["set-cookie"] == "REDACTED" and "connection" not in headers


def test_a_request_the_cassette_does_not_hold_is_a_marked_502_and_counts_as_a_miss(https_server, tmp_path):
    cassette = tmp_path / "cas"
    with Session("record", cassette, https_server.ca) as rec:
        rec.get(f"{https_server.base}/binary")
    with Session("replay", cassette) as rep:
        status, headers, body = rep.get(f"{https_server.base}/never")
        assert status == 502 and headers["x-replay-miss"] == "1" and b"/never" in body
        assert rep.get(f"{https_server.base}/binary")[0] == 200
        assert len(rep.harness.report()["misses"]) == 1


def test_an_upstream_that_refuses_the_connection_is_recorded_and_replayed_as_the_same_502(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]           # nothing listens here once the probe closes
    cassette = tmp_path / "cas"
    with Session("record", cassette) as rec:
        live = rec.get(f"http://127.0.0.1:{dead}/x")
    with Session("replay", cassette) as rep:
        again = rep.get(f"http://127.0.0.1:{dead}/x")
    assert live[0] == again[0] == 502 and again[1]["x-replay-recorded-error"] == live[1]["x-replay-recorded-error"] == "builtins.ConnectionRefusedError"


def test_head_keeps_the_upstream_content_length_and_sends_no_body(https_server, tmp_path):
    cassette = tmp_path / "cas"
    request = lambda s: s.opener.open(urllib.request.Request(f"{https_server.base}/anything", method="HEAD"), timeout=15)
    with Session("record", cassette, https_server.ca) as rec:
        live = request(rec)
        assert live.headers["Content-Length"] == "4321" and live.read() == b""
    with Session("replay", cassette) as rep:
        again = request(rep)
        assert again.headers["Content-Length"] == "4321" and again.headers["X-Head"] == "yes" and again.read() == b""


def test_one_tunnel_carries_many_keep_alive_requests_like_libcurl_does(https_server, tmp_path):
    cassette = tmp_path / "cas"
    host, port = "127.0.0.1", https_server.server_address[1]

    def burst(session):
        conn = HTTPSConnection("127.0.0.1", session.proxy.server_address[1], context=session.ctx, timeout=15)
        conn.set_tunnel(host, port)
        bodies = []
        for _ in range(4):
            conn.request("GET", "/seq")
            bodies.append(conn.getresponse().read())
        conn.close()
        return bodies

    with Session("record", cassette, https_server.ca) as rec:
        live = burst(rec)
    with Session("replay", cassette) as rep:
        assert burst(rep) == live == [b"1", b"2", b"3", b"4"]


# ---------------------------------------------------------------- the leaf certificates

def test_leaf_certificates_verify_for_dns_names_and_ip_literals_against_the_ca_only(tmp_path):
    authority = HR.Authority(tmp_path / "ca")
    for host in ("api.example.test", "127.0.0.1", "::1"):
        left, right = socket.socketpair()
        server = authority.context_for(host)
        wrapped: list = []
        thread = threading.Thread(target=lambda: wrapped.append(server.wrap_socket(left, server_side=True)))
        thread.start()
        client = ssl.create_default_context(cafile=str(authority.ca_pem)).wrap_socket(right, server_hostname=host)
        thread.join(10)
        assert client.getpeercert()["subjectAltName"]
        client.close()
        wrapped[0].close()
    left, right = socket.socketpair()                                       # another name on the same leaf is rejected
    thread = threading.Thread(target=lambda: _quiet(lambda: authority.context_for("a.test").wrap_socket(left, server_side=True)))
    thread.start()
    with pytest.raises(ssl.SSLCertVerificationError):
        ssl.create_default_context(cafile=str(authority.ca_pem)).wrap_socket(right, server_hostname="b.test")
    thread.join(10)
    assert (tmp_path / "ca" / "http-replay-ca.pem").is_file() and not list((tmp_path / "ca").glob("leaf-*"))   # no key lingers on disk


def _quiet(call):
    try:
        call()
    except OSError:
        pass


# ---------------------------------------------------------------- the command-line wrapper, driven only by the environment

CLIENT = '''
import hashlib, json, sys, urllib.error, urllib.request
base, out = sys.argv[1], sys.argv[2]
extra = sys.argv[3:]
result = {}
for path in ["/ok?x=1", "/seq", "/seq", "/missing", "/binary", "/gz"] + extra:
    try:
        with urllib.request.urlopen(base + path, timeout=15) as r:
            result[path + str(len(result))] = [r.status, hashlib.sha256(r.read()).hexdigest()]
    except urllib.error.HTTPError as e:
        result[path + str(len(result))] = [e.code, hashlib.sha256(e.read()).hexdigest()]
with open(out, "w", encoding="utf-8", newline="\\n") as fh:
    json.dump(result, fh, sort_keys=True)
'''


def cli(*args):
    return subprocess.run([sys.executable, str(RUNNER), "proxy", *map(str, args)], capture_output=True, text=True, timeout=120)


def test_wrapper_runs_an_unmodified_client_that_only_reads_proxy_and_ca_variables(https_server, tmp_path):
    client = tmp_path / "client.py"
    client.write_text(CLIENT, encoding="utf-8")
    cassette, out_a, out_b = tmp_path / "cas", tmp_path / "a.json", tmp_path / "b.json"
    rec = cli("record", cassette, "--upstream-cafile", https_server.ca, "--no-proxy", "", "--", sys.executable, client, https_server.base, out_a)
    assert rec.returncode == 0, rec.stderr
    hits = len(https_server.hits)
    assert hits == 6 and "6 interactions" in rec.stderr
    rep = cli("replay", cassette, "--no-proxy", "", "--", sys.executable, client, https_server.base, out_b)
    assert rep.returncode == 0, rep.stderr
    assert len(https_server.hits) == hits and out_a.read_bytes() == out_b.read_bytes()      # D-177: identical output from identical responses
    assert "served 6, missed 0, unused 0" in rep.stderr
    miss = cli("replay", cassette, "--no-proxy", "", "--", sys.executable, client, https_server.base, out_b, "/never")
    assert miss.returncode == 3 and "/never" in miss.stderr and len(https_server.hits) == hits
    short = tmp_path / "short.py"
    short.write_text("import sys, urllib.request\nurllib.request.urlopen(sys.argv[1] + '/binary').read()\n", encoding="utf-8")
    unused = cli("replay", cassette, "--no-proxy", "", "--", sys.executable, short, https_server.base)
    assert unused.returncode == 4 and "unused" in unused.stderr
    assert cli("replay", cassette, "--no-proxy", "", "--allow-unused", "--", sys.executable, short, https_server.base).returncode == 0


def test_wrapper_passes_the_childs_exit_code_through_and_refuses_a_missing_command(https_server, tmp_path):
    failing = tmp_path / "failing.py"
    failing.write_text("import sys, urllib.request\nurllib.request.urlopen(sys.argv[1] + '/binary').read()\nsys.exit(7)\n", encoding="utf-8")
    assert cli("record", tmp_path / "cas", "--upstream-cafile", https_server.ca, "--no-proxy", "", "--", sys.executable, failing, https_server.base).returncode == 7
    assert cli("replay", tmp_path / "cas", "--no-proxy", "", "--", sys.executable, failing, https_server.base).returncode == 7
    assert cli("record", tmp_path / "x", "--").returncode == 2
    assert cli("record", tmp_path / "y", "--", "definitely-not-a-program-7f3a").returncode == 2


def test_default_no_proxy_keeps_loopback_services_direct(https_server, tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import os, sys\nsys.stdout.write('|'.join(os.environ[k] for k in ('NO_PROXY', 'https_proxy', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'CURL_SSL_BACKEND')))\n", encoding="utf-8")
    done = cli("record", tmp_path / "cas", "--", sys.executable, probe)
    no_proxy, proxy, ca, curl_ca, backend = done.stdout.split("|")
    assert no_proxy == "localhost,127.0.0.1,::1" and proxy.startswith("http://127.0.0.1:")
    assert ca.endswith("http-replay-ca.pem") and ca == curl_ca
    assert backend == "openssl"            # Windows R's libcurl defaults to Schannel, which rejects the throwaway CA (seen with R 4.6.0)


@pytest.mark.skipif(shutil.which("curl") is None, reason="no curl on PATH")
def test_libcurl_the_client_behind_rs_httr_round_trips_through_the_proxy(https_server, tmp_path):
    cassette = tmp_path / "cas"

    def fetch(session, *extra):
        cmd = ["curl", "-sS", "--noproxy", "", "--proxy", session.proxy.url, "--cacert", str(session.authority.ca_pem), "--ssl-no-revoke", "-D", "-", *extra]
        done = subprocess.run(cmd, capture_output=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return done.stdout

    url = lambda path: f"{https_server.base}{path}"
    with Session("record", cassette, https_server.ca, secrets=[SECRET]) as rec:
        live = [fetch(rec, url(f"/ok?api_key={SECRET}")), fetch(rec, url("/chunked")), fetch(rec, "--compressed", url("/gz")),
                fetch(rec, "-X", "POST", "--data-binary", "p=1", url("/x"))]
    hits = len(https_server.hits)
    assert b"200 OK" in live[0] and b'{"ok": true}' in live[0] and live[1].endswith(BIG)
    with Session("replay", cassette, secrets=[SECRET]) as rep:
        again = [fetch(rep, url("/ok?api_key=other")), fetch(rep, url("/chunked")), fetch(rep, "--compressed", url("/gz")),
                 fetch(rep, "-X", "POST", "--data-binary", "p=1", url("/x"))]
    strip = lambda raw: [ln for ln in raw.split(b"\r\n") if not ln.lower().startswith((b"date:", b"set-cookie:", b"server:"))]
    assert [strip(x) for x in again] == [strip(x) for x in live]
    assert len(https_server.hits) == hits


# ---------------------------------------------------------------- the real thing: R's httr (libcurl) through the proxy

R_CLIENT = '''
args <- commandArgs(trailingOnly = TRUE)
suppressMessages(library(httr))
for (path in c("/ok?x=1", "/seq", "/seq", "/missing", "/gz", "/chunked")) {
  r <- GET(paste0(args[1], path), timeout(30))
  body <- content(r, "raw")
  cat(sprintf("%s %d %d %.0f\n", path, status_code(r), length(body), sum(as.numeric(body))))
}
'''


def _rscript():
    found = shutil.which("Rscript") or next(iter(sorted(Path("C:/Program Files/R").glob("R-*/bin/Rscript.exe"))), None)
    if not found:
        pytest.skip("no Rscript")
    probe = subprocess.run([str(found), "-e", "library(httr)"], capture_output=True, text=True, timeout=120)
    if probe.returncode:
        pytest.skip("R has no httr")
    return str(found)


def test_r_httr_replays_to_the_same_output_with_the_upstream_gone(https_server, tmp_path):
    rscript = _rscript()
    client = tmp_path / "client.R"
    client.write_text(R_CLIENT, encoding="utf-8")
    cassette = tmp_path / "cas"
    rec = cli("record", cassette, "--upstream-cafile", https_server.ca, "--no-proxy", "", "--", rscript, client, https_server.base)
    assert rec.returncode == 0, rec.stderr + rec.stdout
    assert "6 interactions" in rec.stderr and rec.stdout.count("\n") == 6
    hits = len(https_server.hits)
    rep = cli("replay", cassette, "--no-proxy", "", "--", rscript, client, https_server.base)
    assert rep.returncode == 0, rep.stderr + rep.stdout
    assert rep.stdout == rec.stdout and len(https_server.hits) == hits and "served 6, missed 0, unused 0" in rep.stderr
