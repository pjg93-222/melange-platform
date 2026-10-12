# -*- coding: utf-8 -*-
"""fec/surfaces/desk_server.py: the static desk server, over a real socket on 127.0.0.1:0.

Every case starts the server in a thread against a temp DESK_ROOT and speaks raw HTTP to it, so
the request target reaches the server exactly as written (percent-escapes, backslashes, "//x" and an
absolute-form target are not normalised by a client library).  The enforced cases sign tokens with an
RSA key generated at run time (`cryptography`, pinned in requirements.txt) and a JWKS written to the
temp dir; no key, PEM or token is ever committed.  Skips those cases without `cryptography`.

Runs as:  python -m pytest ops/tests/test_desk_server.py -q   or   python ops/tests/test_desk_server.py
"""
import base64
import contextlib
import gzip
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "fec"))
import access_gate  # noqa: E402
from surfaces import desk_server  # noqa: E402

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False

DESK = b"<!doctype html><title>desk</title>" + b"d" * 2000
LOBBY = b"<!doctype html><title>lobby</title>"
GZ_ONLY_PAGE = b"<!doctype html><title>zipped</title>" + b"z" * 5000
SECRET = b"TOP-SECRET-OUTSIDE-THE-ROOT"


def raw(port, method="GET", target="/", headers=(), body=b"", host="127.0.0.1"):
    """One HTTP/1.1 exchange on a fresh socket; returns (status, {lower-case header: value}, body bytes)."""
    lines = ["%s %s HTTP/1.1" % (method, target), "Host: " + host, "Connection: close"]
    lines += ["%s: %s" % h for h in headers]
    if body:
        lines.append("Content-Length: %d" % len(body))
    req = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(req)
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    head, _, payload = buf.partition(b"\r\n\r\n")
    status_line, *hdr = head.decode("latin-1").split("\r\n")
    return int(status_line.split()[1]), {k.lower(): v.strip() for k, _, v in (h.partition(":") for h in hdr)}, payload


class Served(unittest.TestCase):
    """A server on an ephemeral loopback port over a temp root; subclasses choose the access config."""
    env = {}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.root = os.path.join(self.tmp, "root")
        self.outside = os.path.join(self.tmp, "outside")
        for d in (self.root, self.outside, os.path.join(self.root, "downloads", "2026.10.12.1")):
            os.makedirs(d)
        self.write("Moneyball.html", DESK)
        self.write("lobby-network.html", LOBBY)
        self.write("lobby-zipped.html", GZ_ONLY_PAGE)
        self.write("lobby-zipped.html.gz", gzip.compress(GZ_ONLY_PAGE, mtime=0))
        self.write("INVITED_BUILD.json", b'{"governance":"pending"}')
        self.write("downloads/2026.10.12.1/kit.zip", b"PK-fake-zip")
        self.write("downloads/2026.10.12.1/.hidden", b"nope")
        with open(os.path.join(self.outside, "secret.txt"), "wb") as f:
            f.write(SECRET)
        with open(os.path.join(self.outside, "secret.html"), "wb") as f:
            f.write(SECRET)
        self.cfg = self.make_config()
        self.server = desk_server.make_server(self.root, self.cfg, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.log = io.StringIO()
        redirect = contextlib.redirect_stderr(self.log)   # the audit lines; the server thread writes to sys.stderr
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def make_config(self):
        return access_gate.AccessConfig.from_env(dict(self.env))

    def write(self, rel, data):
        path = os.path.join(self.root, *rel.split("/"))
        with open(path, "wb") as f:
            f.write(data)
        return path

    def get(self, target="/", **kw):
        return raw(self.port, "GET", target, **kw)

    def audit(self):
        return [json.loads(line) for line in self.log.getvalue().splitlines() if line.startswith("{")]


class NotEnforced(Served):
    def test_root_serves_the_desk_with_correct_headers(self):
        status, h, body = self.get("/")
        self.assertEqual((status, body), (200, DESK))
        self.assertEqual(h["content-type"], "text/html; charset=utf-8")
        self.assertEqual(h["content-length"], str(len(DESK)))
        st = os.stat(os.path.join(self.root, "Moneyball.html"))
        self.assertEqual(h["etag"], '"%x-%x"' % (st.st_size, st.st_mtime_ns))
        self.assertTrue(h["last-modified"].endswith(" GMT"))
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertEqual(h["referrer-policy"], "no-referrer")
        self.assertEqual(h["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(h["cross-origin-resource-policy"], "same-origin")
        self.assertNotIn("cache-control", h)            # only the enforced (hosted) server adds no-store
        self.assertNotIn("content-security-policy", h)   # left to the page
        self.assertEqual(h["server"], "melange-desk")

    def test_query_string_is_ignored(self):
        status, _, body = self.get("/?v=industry")
        self.assertEqual((status, body), (200, DESK))

    def test_lobby_allowlist(self):
        self.assertEqual(self.get("/lobby-network.html")[::2], (200, LOBBY))
        for target in ("/lobby-interactions.html",        # valid slug, no such file
                       "/lobby-NETWORK.html", "/lobby-.html", "/lobby-a_b.html", "/lobby-a.b.html", "/lobby-%6eetwork.html",
                       "/lobby-" + "a" * 65 + ".html", "/lobby-network.htm", "/Lobby-network.html", "/lobby-network.html/",
                       "/lobby-network.html.gz", "/lobby-network.html%00.png"):
            self.assertEqual(self.get(target)[0], 404, target)
        self.assertEqual(self.get("/lobby-" + "a" * 64 + ".html")[0], 404)   # 64 is legal; the file just is not there

    def test_only_the_five_routes_exist(self):
        for target in ("/Moneyball.html", "/INVITED_BUILD.json", "/index.html", "/downloads", "/downloads/", "/downloads/latest",
                       "/downloads/latest/", "/downloads/2026.10.12.1/", "/healthz/", "/version/", "/robots.txt", "/lobby-network"):
            status, _, body = self.get(target)
            self.assertEqual(status, 404, target)
            self.assertNotIn(b"governance", body)

    def test_downloads_route_and_regex(self):
        status, h, body = self.get("/downloads/2026.10.12.1/kit.zip")
        self.assertEqual((status, body), (200, b"PK-fake-zip"))
        self.assertEqual(h["content-type"], "application/zip")
        self.assertEqual(h["content-disposition"], 'attachment; filename="kit.zip"')
        for bad in ("/downloads/2026.10.12/kit.zip", "/downloads/2026.10.12.x/kit.zip", "/downloads/latest2/kit.zip",
                    "/downloads/LATEST/kit.zip", "/downloads/2026.10.12.1/kit.zip/", "/downloads/2026.10.12.1/a/b",
                    "/downloads/2026.10.12.1/" + "k" * 129, "/downloads/2026.10.12.1/.hidden", "/downloads/2026.10.12.1/..",
                    "/downloads/2026.10.12.1/.", "/downloads/2026.10.12.1/kit%2ezip", "/downloads/2026.10.12.1/ki%74.zip",
                    "/downloads/2026.10.12.1/kit.zip%00", "/downloads/2026.10.12.1/kit.zip;x", "/downloads//kit.zip",
                    "/downloads/2026.10.12.9/kit.zip", "/downloads/latest/kit.zip"):
            self.assertEqual(self.get(bad)[0], 404, bad)

    def test_traversal_attempts_never_reach_a_file(self):
        targets = ["/%2e%2e/outside/secret.txt", "/%2E%2E/outside/secret.txt", "/../outside/secret.txt", "/./../outside/secret.txt",
                   "//etc/passwd", "//outside/secret.txt", "/lobby-..%2foutside%2fsecret.html", "/lobby-../outside/secret.html",
                   "/downloads/latest/..%2f..%2foutside%2fsecret.txt", "/downloads/..%5c..%5coutside%5csecret.txt",
                   "/downloads/2026.10.12.1/..", "/downloads/2026.10.12.1/..%2fsecret.txt", "/downloads/2026.10.12.1/%2e%2e",
                   "/downloads/2026.10.12.1/" + "..\\" * 2 + "secret.txt", "\\outside\\secret.txt", "/..\\outside\\secret.txt",
                   "/%2fetc%2fpasswd", "/%252e%252e/secret.txt", "/lobby-network.html/../../outside/secret.txt",
                   "http://127.0.0.1/", "http://evil.example/Moneyball.html", "http://127.0.0.1/../outside/secret.txt",
                   "*", "/.", "/..", "/./", "/%00"]
        for target in targets:
            try:
                status, _, body = self.get(target)
            except (ValueError, IndexError, ConnectionError):
                continue   # a request the server refuses to even parse is also safe
            self.assertIn(status, (400, 404), target)
            self.assertNotIn(SECRET, body, target)
        self.assertEqual(self.get("/?/../../outside/secret.txt")[2], DESK)   # a "?" starts the query: this is just "/"

    def dirlink(self, link, target):
        """A directory symlink, or on Windows without the privilege a junction (same resolve() behaviour)."""
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt" or subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True).returncode:
                self.skipTest("neither symlinks nor junctions are available here")

    def test_directory_link_escape_is_blocked(self):
        self.dirlink(os.path.join(self.root, "downloads", "latest"), self.outside)
        self.dirlink(os.path.join(self.root, "downloads", "2099.01.01.1"), os.path.join(self.root, "downloads", "2026.10.12.1"))
        self.assertEqual(self.get("/downloads/latest/secret.txt")[0], 404)
        self.assertEqual(self.get("/downloads/latest/secret.html")[0], 404)
        self.assertNotIn(SECRET, self.get("/downloads/latest/secret.txt")[2])
        self.assertEqual(self.get("/downloads/2099.01.01.1/kit.zip")[::2], (200, b"PK-fake-zip"))   # a link that stays inside is fine

    def test_file_symlink_escape_is_blocked(self):
        try:
            os.symlink(os.path.join(self.outside, "secret.html"), os.path.join(self.root, "lobby-evil.html"))
            os.symlink(os.path.join(self.outside, "secret.txt"), os.path.join(self.root, "lobby-network.html.gz"))
        except (OSError, NotImplementedError):
            self.skipTest("file symlinks are not available here")
        self.assertEqual(self.get("/lobby-evil.html")[0], 404)
        status, h, body = self.get("/lobby-network.html", headers=[("Accept-Encoding", "gzip")])   # an escaping .gz is not used
        self.assertEqual((status, body), (200, LOBBY))
        self.assertNotIn("content-encoding", h)

    def test_other_methods_are_405_and_change_nothing(self):
        before = sorted(os.listdir(self.root))
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT", "PROPFIND", "BREW"):
            status, h, body = raw(self.port, method, "/", body=b"x=1&y=" + b"A" * 5000)
            self.assertEqual(status, 405, method)
            self.assertEqual(h["allow"], "GET, HEAD", method)
            self.assertNotIn(DESK, body)
        self.assertEqual(raw(self.port, "POST", "/lobby-network.html")[0], 405)
        self.assertEqual(raw(self.port, "PUT", "/downloads/latest/x.zip", body=b"zzz")[0], 405)
        self.assertEqual(sorted(os.listdir(self.root)), before)
        with open(os.path.join(self.root, "Moneyball.html"), "rb") as f:
            self.assertEqual(f.read(), DESK)

    def test_head_has_headers_and_no_body(self):
        for target in ("/", "/lobby-network.html", "/healthz", "/version", "/downloads/2026.10.12.1/kit.zip", "/nothing-here"):
            g_status, g_h, g_body = self.get(target)
            h_status, h_h, h_body = raw(self.port, "HEAD", target)
            self.assertEqual((h_status, h_body), (g_status, b""), target)
            self.assertEqual(h_h["content-length"], g_h["content-length"], target)
            self.assertEqual(h_h["content-type"], g_h["content-type"], target)
            self.assertEqual(h_h.get("etag"), g_h.get("etag"), target)

    def test_gzip_negotiation(self):
        page = "/lobby-zipped.html"
        plain = self.get(page)
        self.assertEqual(plain[2], GZ_ONLY_PAGE)
        self.assertNotIn("content-encoding", plain[1])
        self.assertEqual(plain[1]["vary"], "Accept-Encoding")
        for accept in ("gzip", "gzip, deflate, br", "br;q=1.0, gzip;q=0.5", "GZIP", "x-gzip", "*", "deflate, *;q=0.1"):
            status, h, body = self.get(page, headers=[("Accept-Encoding", accept)])
            self.assertEqual((status, h.get("content-encoding"), h["vary"]), (200, "gzip", "Accept-Encoding"), accept)
            self.assertEqual(gzip.decompress(body), GZ_ONLY_PAGE, accept)
            self.assertEqual(h["content-length"], str(len(body)), accept)
            self.assertEqual(h["content-type"], "text/html; charset=utf-8", accept)
            self.assertTrue(h["etag"].endswith('-gz"') and h["etag"] != plain[1]["etag"], accept)
        for accept in ("identity", "br", "gzip;q=0", "gzip;q=0.0, *", "gzip; q=0", "deflate", ""):
            status, h, body = self.get(page, headers=[("Accept-Encoding", accept)])
            self.assertEqual((status, body), (200, GZ_ONLY_PAGE), accept)
            self.assertNotIn("content-encoding", h, accept)
        self.assertEqual(raw(self.port, "HEAD", page, headers=[("Accept-Encoding", "gzip")])[1]["content-encoding"], "gzip")
        # a page with no sidecar never claims to vary or to be gzipped
        h = self.get("/lobby-network.html", headers=[("Accept-Encoding", "gzip")])[1]
        self.assertNotIn("content-encoding", h)
        self.assertNotIn("vary", h)

    def test_a_gz_older_than_its_page_is_never_served(self):
        page = "/lobby-zipped.html"
        base, side = os.path.join(self.root, "lobby-zipped.html"), os.path.join(self.root, "lobby-zipped.html.gz")
        stamp = os.stat(base).st_mtime_ns
        os.utime(side, ns=(stamp, stamp - 5_000_000_000))              # the page was rebuilt after the .gz: leftover
        status, h, body = self.get(page, headers=[("Accept-Encoding", "gzip")])
        self.assertEqual((status, body), (200, GZ_ONLY_PAGE))
        self.assertNotIn("content-encoding", h)
        self.assertNotIn("vary", h)                                      # a stale sidecar is as good as none
        os.utime(side, ns=(stamp, stamp))                                # equal is fine: the build wrote them together
        self.assertEqual(self.get(page, headers=[("Accept-Encoding", "gzip")])[1]["content-encoding"], "gzip")
        os.utime(side, ns=(stamp, stamp + 5_000_000_000))
        self.assertEqual(self.get(page, headers=[("Accept-Encoding", "gzip")])[1]["content-encoding"], "gzip")

    def test_the_access_check_runs_once_per_request_for_every_method(self):
        calls = []
        real = desk_server.DeskHandler.access_check

        def counting(handler):
            calls.append(handler.command)
            return real(handler)
        with mock.patch.object(desk_server.DeskHandler, "access_check", counting):
            self.get("/")
            raw(self.port, "HEAD", "/lobby-network.html")
            raw(self.port, "POST", "/", body=b"x=1")
        self.assertEqual(calls, ["GET", "HEAD", "POST"])

    def test_content_disposition_is_the_route_name_even_through_a_symlink(self):
        real = desk_server.DeskHandler._resolve

        def as_a_symlink(handler, rel):   # what resolve() does to downloads/<id>/alias.zip when it links to kit.zip
            return real(handler, rel.replace("alias.zip", "kit.zip"))
        with mock.patch.object(desk_server.DeskHandler, "_resolve", as_a_symlink):
            status, h, body = self.get("/downloads/2026.10.12.1/alias.zip")
        self.assertEqual((status, body), (200, b"PK-fake-zip"))
        self.assertEqual(h["content-disposition"], 'attachment; filename="alias.zip"')    # not kit.zip, the file it resolves to
        self.assertEqual(h["content-type"], "application/zip")

    def test_a_handler_crash_logs_the_exception_type_and_no_address_or_traceback(self):
        with mock.patch.object(desk_server.DeskHandler, "_route", side_effect=RuntimeError("secret detail in the message")):
            try:
                self.get("/")
            except (ConnectionError, IndexError):   # the crash closes the socket with nothing sent
                pass
            deadline = time.time() + 5
            while "server error" not in self.log.getvalue() and time.time() < deadline:
                time.sleep(0.02)
        text = self.log.getvalue()
        self.assertIn("server error: RuntimeError", text)
        for leak in ("Traceback", "127.0.0.1", "secret detail", "File \""):
            self.assertNotIn(leak, text, leak)
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_gz_without_its_page_is_not_a_page(self):
        self.write("lobby-orphan.html.gz", gzip.compress(b"orphan"))
        self.assertEqual(self.get("/lobby-orphan.html", headers=[("Accept-Encoding", "gzip")])[0], 404)

    def test_healthz_and_version(self):
        self.assertEqual(self.get("/healthz")[::2], (200, b"ok\n"))
        status, h, body = self.get("/version")
        self.assertEqual((status, h["content-type"]), (200, "application/json"))
        self.assertEqual(json.loads(body), access_gate.version_payload("melange-desk"))
        self.assertEqual(json.loads(body)["app"], "melange-desk")

    def test_audit_line_has_no_query_and_no_ip(self):
        self.get("/lobby-network.html?secret=hunter2&email=a%40b.test")
        self.get("/nothing-here?token=abc")
        raw(self.port, "POST", "/?x=queryinpost", body=b"q")
        raw(self.port, "GET", "garbage with spaces and no version?q=leak")   # malformed: http.server's own error path
        lines = self.audit()
        self.assertGreaterEqual(len(lines), 3)
        text = self.log.getvalue()
        for leak in ("hunter2", "token=abc", "queryinpost", "127.0.0.1", "a%40b", "q=leak"):
            self.assertNotIn(leak, text, leak)
        by_route = {(r["method"], r["route"]): r for r in lines}
        ok = by_route[("GET", "/lobby-network.html")]
        self.assertEqual((ok["status"], ok["app"], ok["user"]), (200, "melange-desk", "-"))
        self.assertEqual(by_route[("GET", "/nothing-here")]["status"], 404)
        self.assertEqual(by_route[("POST", "/")]["status"], 405)

    def test_oversized_and_broken_requests_get_a_plain_error(self):
        try:   # the server answers 414 and closes with the rest unread, which a client may see as a reset
            status, h, _ = self.get("/" + "a" * 70000)
            self.assertEqual(status, 414)
            self.assertEqual(h["x-content-type-options"], "nosniff")
        except ConnectionError:
            pass
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(b"\x00\x01\x02 not http\r\n\r\n")
            data = s.recv(4096)
        self.assertIn(data, (b"", b"error\n"))   # an unparseable version is answered HTTP/0.9 style: the bare body, no headers
        self.assertEqual(self.get("/healthz")[0], 200)   # and the server is still up


@unittest.skipUnless(HAVE_CRYPTO, "cryptography is not installed")
class Enforced(Served):
    """BIND_HOST stays loopback (so the socket is local) but ACCESS_ENFORCE=1 turns the gate on."""
    AUD = "desk-aud-tag"
    ISS = "https://example-team.cloudflareaccess.com"
    EMAIL = "analyst@example.test"

    def make_config(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        nums = self.key.public_key().public_numbers()
        b64 = lambda raw_: base64.urlsafe_b64encode(raw_).rstrip(b"=").decode()
        be = lambda n: n.to_bytes((n.bit_length() + 7) // 8, "big")
        jwks = os.path.join(self.tmp, "certs.json")
        with open(jwks, "w", encoding="utf-8") as f:
            json.dump({"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256", "n": b64(be(nums.n)), "e": b64(be(nums.e))}]}, f)
        return access_gate.AccessConfig.from_env({
            "BIND_HOST": "127.0.0.1", "ACCESS_ENFORCE": "1", "ACCESS_AUD": self.AUD, "ACCESS_ISSUER": self.ISS,
            "ACCESS_JWKS_FILE": jwks, "ALLOWED_HOSTS": "desk.example.test"})

    def token(self, **over):
        claims = {"aud": [self.AUD], "iss": self.ISS, "exp": int(time.time()) + 3600, "nbf": int(time.time()) - 10,
                  "email": self.EMAIL}
        claims.update(over)
        enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
        signing = enc({"alg": "RS256", "kid": "k1", "typ": "JWT"}) + "." + enc(claims)
        sig = self.key.sign(signing.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return signing + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def authed(self, tok=None):
        return [("Cf-Access-Jwt-Assertion", tok or self.token())]

    def test_no_jwt_is_403_on_every_route_and_method(self):
        self.assertTrue(self.cfg.enforced)
        for method, target in (("GET", "/"), ("GET", "/lobby-network.html"), ("GET", "/downloads/2026.10.12.1/kit.zip"),
                               ("GET", "/nothing-here"), ("HEAD", "/"), ("POST", "/")):
            status, _, body = raw(self.port, method, target)
            self.assertEqual(status, 403, (method, target))
            self.assertNotIn(DESK, body)
            self.assertNotIn(LOBBY, body)

    def test_valid_jwt_is_served_with_no_store(self):
        status, h, body = self.get("/", headers=self.authed())
        self.assertEqual((status, body), (200, DESK))
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(self.get("/lobby-network.html", headers=self.authed())[::2], (200, LOBBY))
        self.assertEqual(self.get("/downloads/2026.10.12.1/kit.zip", headers=self.authed())[0], 200)
        self.assertEqual(self.get("/nothing-here", headers=self.authed())[0], 404)
        self.assertEqual(raw(self.port, "POST", "/", headers=self.authed(), body=b"x")[0], 405)
        self.assertEqual(raw(self.port, "HEAD", "/", headers=self.authed())[2], b"")

    def test_bad_tokens_are_403(self):
        good = self.token()
        flipped = good[:-4] + ("AAAA" if not good.endswith("AAAA") else "BBBB")
        for label, tok in (("flipped signature", flipped), ("wrong aud", self.token(aud=["other"])),
                           ("wrong iss", self.token(iss="https://evil.cloudflareaccess.com")),
                           ("expired", self.token(exp=int(time.time()) - 3600)), ("no email", self.token(email="")),
                           ("garbage", "not.a.jwt"), ("alg none", "eyJhbGciOiJub25lIn0.e30.")):
            self.assertEqual(self.get("/", headers=self.authed(tok))[0], 403, label)

    def test_jwt_in_a_cookie_does_not_count(self):
        self.assertEqual(self.get("/", headers=[("Cookie", "CF_Authorization=" + self.token())])[0], 403)

    def test_unlisted_host_is_403_even_with_a_valid_jwt(self):
        self.assertEqual(self.get("/", headers=self.authed(), host="evil.example")[0], 403)
        self.assertEqual(self.get("/", headers=self.authed(), host="desk.example.test")[0], 200)

    def test_healthz_from_loopback_needs_no_token_but_a_proxied_request_does(self):
        self.assertEqual(self.get("/healthz")[::2], (200, b"ok\n"))
        self.assertEqual(self.get("/version")[0], 200)
        self.assertEqual(self.get("/healthz", headers=[("Cf-Connecting-Ip", "203.0.113.9")])[0], 403)
        self.assertEqual(self.get("/healthz", headers=[("X-Forwarded-For", "203.0.113.9")])[0], 403)
        self.assertEqual(self.get("/healthz", headers=[("Cf-Connecting-Ip", "203.0.113.9")] + self.authed())[0], 200)

    def test_audit_names_the_user_never_the_query(self):
        self.get("/lobby-network.html?q=sensitive", headers=self.authed())
        self.get("/lobby-network.html?q=sensitive")
        text = self.log.getvalue()
        self.assertNotIn("sensitive", text)
        self.assertNotIn("127.0.0.1", text)
        rows = self.audit()
        self.assertIn((self.EMAIL, 200), [(r["user"], r["status"]) for r in rows])
        self.assertIn(("-", 403), [(r["user"], r["status"]) for r in rows])


class Startup(unittest.TestCase):
    def test_main_refuses_a_bad_root_and_a_non_loopback_bind_without_access_settings(self):
        env = {"DESK_ROOT": os.path.join(tempfile.gettempdir(), "no-such-desk-root-xyz")}
        old = {k: os.environ.get(k) for k in ("DESK_ROOT", "DESK_PORT", "BIND_HOST", "ACCESS_AUD", "ACCESS_ISSUER", "ACCESS_JWKS_FILE", "ACCESS_ENFORCE")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        for k in old:
            os.environ.pop(k, None)
        os.environ.update(env)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(desk_server.main(), 2)
        self.assertIn("DESK_ROOT", err.getvalue())
        os.environ["BIND_HOST"] = "0.0.0.0"
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as cm:
            desk_server.main()
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("ACCESS_AUD", err.getvalue())

    def test_accepts_gzip_parser(self):
        yes = ("gzip", "GZip", "gzip;q=0.001", "br, gzip", "*", "x-gzip", "gzip ; q=1")
        no = ("", None, "identity", "gzip;q=0", "gzip;q=0.0", "gzip;q=junk", "*;q=0", "gzip;q=0, *")
        for h in yes:
            self.assertTrue(desk_server.accepts_gzip(h), h)
        for h in no:
            self.assertFalse(desk_server.accepts_gzip(h), h)


if __name__ == "__main__":
    unittest.main()
