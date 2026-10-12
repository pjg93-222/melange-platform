"""Netlify draft -> verify -> publish against a threaded loopback fake API. No network."""
from __future__ import annotations

import hashlib
import http.server
import json
import threading
import unittest
from pathlib import Path

from ops.tests.test_release_common import Base
from ops.release_lib import core, web

TOKEN = "nfp_TOP_SECRET_TOKEN_123"
SITE = "site-1"


class FakeNetlify(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls: list[tuple] = []             # (method, path, auth header)
        self.known: set[str] = set()             # sha1s Netlify already has
        self.uploaded: dict[str, bytes] = {}     # path -> bytes
        self.files: dict[str, str] = {}          # path -> sha1 of the current draft
        self.by_sha: dict[str, bytes] = {}
        self.polls = 0
        self.version_lag = 0                     # prod answers 404 to this many /version.json fetches (a lagging CDN)
        self.redirect_to = None                  # every GET answers 302 to this URL
        self.production: dict[str, bytes] = {}
        self.reject_with = None                  # status for every upload
        self.flaky = 0                           # 503s before the first upload succeeds
        self.corrupt_draft = False
        self.restored = False
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def api(self):
        return f"http://127.0.0.1:{self.port}/api/v1"

    def required(self):
        return sorted({sha for sha in self.files.values() if sha not in self.known and sha not in self.by_sha})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body=b"", headers=None):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        s = self.server
        s.calls.append(("GET", self.path, self.headers.get("Authorization")))
        if s.redirect_to:
            return self.reply(302, b"", {"Location": s.redirect_to})
        if self.path == "/prod/version.json" and s.version_lag > 0:
            s.version_lag -= 1
            return self.reply(404, b"not yet")
        if self.path == "/api/v1/deploys/d1":
            s.polls += 1
            if s.polls == 1:
                return self.reply(200, {"id": "d1", "state": "preparing"})
            need = s.required()
            if need:
                return self.reply(200, {"id": "d1", "state": "prepared", "required": need})
            return self.reply(200, {"id": "d1", "state": "ready", "deploy_ssl_url": f"http://127.0.0.1:{s.port}/draft"})
        if self.path == f"/api/v1/sites/{SITE}":
            return self.reply(200, {"ssl_url": f"http://127.0.0.1:{s.port}/prod"})
        for prefix, store in (("/draft", None), ("/prod", s.production)):
            if self.path.startswith(prefix + "/"):
                path = self.path[len(prefix):]
                if path.endswith("/"):                  # like Netlify: a directory URL serves its index.html
                    path += "index.html"
                if store is None:
                    sha = s.files.get(path)
                    body = s.by_sha.get(sha) if sha else None
                    if body is not None and s.corrupt_draft and path == "/index.html":
                        body = body[:-1] + bytes([body[-1] ^ 1])
                else:
                    body = store.get(path)
                return self.reply(200, body) if body is not None else self.reply(404, b"nope")
        self.reply(404, {})

    def do_POST(self):
        s = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        s.calls.append(("POST", self.path, self.headers.get("Authorization")))
        if self.path == f"/api/v1/sites/{SITE}/deploys":
            request = json.loads(body)
            assert request["draft"] is True and request["async"] is True
            s.files = dict(request["files"])
            s.polls = 0
            return self.reply(200, {"id": "d1", "state": "preparing"})
        if self.path == f"/api/v1/sites/{SITE}/deploys/d1/restore":
            s.restored = True
            s.production = {path: s.by_sha[sha] for path, sha in s.files.items() if sha in s.by_sha}
            return self.reply(200, {"id": "d1", "state": "ready", "published_at": "x"})
        self.reply(404, {})

    def do_PUT(self):
        s = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        s.calls.append(("PUT", self.path, self.headers.get("Authorization")))
        if s.reject_with:
            return self.reply(s.reject_with, {"error": "too large"})
        if s.flaky > 0:
            s.flaky -= 1
            return self.reply(503, {})
        path = "/" + self.path.split("/files/", 1)[1]
        sha = hashlib.sha1(body).hexdigest()
        assert s.files.get(path) == sha, "uploaded bytes do not match the announced sha1"
        s.by_sha[sha] = body
        s.uploaded[path] = body
        self.reply(200, {})


class Sink(http.server.ThreadingHTTPServer):
    """Any host a redirect could name: records every request it receives, headers included."""
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), SinkHandler)
        self.seen: list[dict] = []
        threading.Thread(target=self.serve_forever, daemon=True).start()


class SinkHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.seen.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


class NetlifyTests(Base):
    def setUp(self):
        self.server = FakeNetlify()
        self.addCleanup(self.server.server_close)           # LIFO: shutdown first, then close the socket
        self.addCleanup(self.server.shutdown)
        root = self.tmpdir()
        self.index = root / "index.html"
        self.index.write_bytes(b"LOCKED-DESK-" + b"x" * 5000)
        self.pocket = root / "pocket.html"
        self.pocket.write_bytes(b"LOCKED-POCKET")
        self.version = root / "version.json"
        self.version.write_bytes(core.canonical_json({"release_id": "2026.10.12.1"}))
        self.files = {"/index.html": self.index, "/pocket/index.html": self.pocket, "/version.json": self.version}
        self.sleeps = []
        self.api = web.Netlify(core.Secret(TOKEN), SITE, api=self.server.api, sleep=self.sleeps.append)

    def backoffs(self):
        return [s for s in self.sleeps if s > 0]                              # zero-length sleeps are just the poll interval

    def methods(self, method):
        return [c for c in self.server.calls if c[0] == method]

    def test_happy_path_draft_verify_publish_and_reverify_production(self):
        result = self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertTrue(result["published"] and result["verified"])
        self.assertEqual(result["deploy_id"], "d1")
        self.assertEqual({p: self.server.production[p] for p in self.files}, {p: Path(f).read_bytes() for p, f in self.files.items()})
        self.assertEqual(len(self.methods("PUT")), 3)
        self.assertTrue(self.server.restored)
        order = [c[0] + " " + c[1].rsplit("/", 1)[-1] for c in self.server.calls if c[0] in ("POST", "PUT")]
        self.assertEqual(order[0], "POST deploys")
        self.assertEqual(order[-1], "POST restore")                       # publish is the very last mutation
        self.assertIn("published_utc", result)

    def test_only_required_files_are_uploaded(self):
        self.server.known = {hashlib.sha1(self.pocket.read_bytes()).hexdigest(), hashlib.sha1(self.version.read_bytes()).hexdigest()}
        self.server.by_sha = {sha: body for sha, body in ((hashlib.sha1(b).hexdigest(), b) for b in (self.pocket.read_bytes(), self.version.read_bytes()))}
        self.api.release(self.files, publish=False, release_id="2026.10.12.1", interval=0)
        self.assertEqual(sorted(self.server.uploaded), ["/index.html"])
        self.assertEqual(len(self.methods("PUT")), 1)

    def test_draft_serving_different_bytes_is_never_published(self):
        self.server.corrupt_draft = True
        with self.assertRaises(core.ReleaseError) as raised:
            self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertEqual(raised.exception.code, "NETLIFY_VERIFY")
        self.assertIn("/index.html", raised.exception.message)
        self.assertFalse(self.server.restored)
        self.assertFalse(any(c[1].endswith("/restore") for c in self.server.calls))

    def test_a_4xx_on_upload_is_rejected_final_with_no_retry_and_no_publish(self):
        self.server.reject_with = 413
        with self.assertRaises(web.Rejected) as raised:
            self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertEqual(raised.exception.code, "REJECTED")
        self.assertIn("413", raised.exception.message)
        self.assertEqual(len(self.methods("PUT")), 1)                      # never retried, never split or stripped
        self.assertFalse(self.server.restored)
        self.assertEqual(self.backoffs(), [])

    def test_publish_false_never_restores(self):
        result = self.api.release(self.files, publish=False, release_id="2026.10.12.1", interval=0)
        self.assertFalse(result["published"])
        self.assertTrue(result["verified"])
        self.assertFalse(self.server.restored)
        self.assertEqual(self.server.production, {})

    def test_5xx_is_retried_with_bounded_backoff_then_succeeds(self):
        self.server.flaky = 2
        self.api.release(self.files, publish=False, release_id="2026.10.12.1", interval=0)
        self.assertEqual(sorted(self.server.uploaded), sorted(self.files))
        self.assertEqual(self.backoffs(), [1, 2])                           # exponential, capped at 60
        self.assertTrue(all(s <= 60 for s in self.backoffs()))

    def test_persistent_5xx_gives_up(self):
        self.server.flaky = 99
        self.api.retries = 2
        with self.assertRaises(core.ReleaseError) as raised:
            self.api.release(self.files, publish=False, release_id="2026.10.12.1", interval=0)
        self.assertEqual(raised.exception.code, "NETLIFY_HTTP")
        self.assertEqual(len(self.methods("PUT")), 3)                      # first try + 2 retries

    def test_token_only_travels_in_the_authorization_header(self):
        self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        api_calls = [c for c in self.server.calls if c[1].startswith("/api/")]
        self.assertTrue(api_calls)
        for method, path, auth in api_calls:
            self.assertEqual(auth, "Bearer " + TOKEN)
            self.assertNotIn(TOKEN, path)
        self.assertTrue(all(c[2] is None for c in self.server.calls if not c[1].startswith("/api/")))   # draft/prod fetches send no token
        self.assertNotIn(TOKEN, repr(self.api.token) + str(self.api.token))

    def test_production_must_serve_the_release_version_after_publish(self):
        self.version.write_bytes(core.canonical_json({"release_id": "something-else"}))
        with self.assertRaises(core.ReleaseError) as raised:
            self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertEqual(raised.exception.code, "NETLIFY_VERIFY")

    def test_a_redirect_never_carries_the_token_or_an_access_secret_elsewhere(self):
        sink = Sink()
        self.addCleanup(sink.server_close)
        self.addCleanup(sink.shutdown)
        self.server.redirect_to = f"http://127.0.0.1:{sink.server_address[1]}/captured"
        with self.assertRaises(core.ReleaseError) as raised:
            self.api._call("GET", f"/sites/{SITE}")
        self.assertEqual(raised.exception.code, "NETLIFY_HTTP")
        self.assertIn("302", raised.exception.message)
        code, _body = web.http_get(f"http://127.0.0.1:{self.server.port}/status", {"CF-Access-Client-Secret": "SECRET"})
        self.assertEqual(code, 302)                                         # surfaced, not followed
        self.assertEqual(sink.seen, [])                                     # neither credential reached the other host
        code, _body = web.http_get(f"http://127.0.0.1:{self.server.port}/plain")      # no credential: a redirect is followed as before
        self.assertEqual((code, len(sink.seen)), (200, 1))
        self.assertNotIn("Authorization", sink.seen[0])

    def test_production_check_retries_with_backoff_while_the_cdn_catches_up(self):
        self.server.version_lag = 2                                         # the first verification fetches /version.json twice
        result = self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertTrue(result["published"])
        self.assertEqual(self.backoffs(), [2])

    def test_a_production_that_never_serves_the_release_gives_up_and_says_it_is_live(self):
        self.server.version_lag = 10 ** 6
        with self.assertRaises(core.ReleaseError) as raised:
            self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertEqual(raised.exception.code, "NETLIFY_VERIFY")
        self.assertIn("is live and unverified", raised.exception.message)
        self.assertEqual(self.backoffs(), [2, 4, 8, 16])                    # five attempts, bounded backoff
        self.assertEqual(len([c for c in self.server.calls if c[1].endswith("/restore")]), 1)    # published once, never again

    def test_a_version_json_that_is_not_json_is_a_failed_check_not_a_crash(self):
        self.version.write_bytes(b"<html>502 Bad Gateway</html>")
        with self.assertRaises(core.ReleaseError) as raised:
            self.api.release(self.files, publish=True, release_id="2026.10.12.1", interval=0)
        self.assertEqual(raised.exception.code, "NETLIFY_VERIFY")
        self.assertIsNone(web.served_release_id(b"<html>"))
        self.assertIsNone(web.served_release_id(b"[1]"))
        self.assertEqual(web.served_release_id(b'{"release_id": "x"}'), "x")

    def test_non_https_remote_urls_are_refused(self):
        with self.assertRaises(core.ReleaseError) as raised:
            web.Netlify(core.Secret(TOKEN), SITE, api="http://api.netlify.example/api/v1")._call("GET", "/x")
        self.assertEqual(raised.exception.code, "INSECURE_URL")
        with self.assertRaises(core.ReleaseError):
            web.http_get("http://example.com/version")


class ReceiptTests(Base):
    def test_receipt_is_extended_only_when_its_hash_is_the_uploaded_file(self):
        root = self.tmpdir()
        index = root / "index.html"
        index.write_bytes(b"locked")
        receipt = root / "dashboard_deployment_receipt.json"
        base = {"schema": "dashboard-deployment-receipt-v1", "status": "LOCAL_DEPLOY_ARTIFACT_READY_NOT_REMOTE",
                "local_deploy_artifact": {"sha256": core.sha256_bytes(b"locked")}, "contract": {"remote_deploy_confirmed": False,
                                                                                                "password_material_recorded": False}}
        receipt.write_text(json.dumps(base))
        web.confirm_receipt(receipt, index, "d1", "2026-10-12T00:00:00Z")
        done = json.loads(receipt.read_text())
        self.assertTrue(done["contract"]["remote_deploy_confirmed"])
        self.assertEqual((done["contract"]["remote_deploy_id"], done["contract"]["remote_published_utc"]), ("d1", "2026-10-12T00:00:00Z"))
        self.assertEqual(done["status"], "REMOTE_DEPLOY_CONFIRMED")
        self.assertFalse(done["contract"]["password_material_recorded"])                 # the rest is untouched
        self.assertFalse(list(root.glob("*.tmp")))
        receipt.write_text(json.dumps(base))
        index.write_bytes(b"a different build")
        with self.assertRaises(core.ReleaseError) as raised:
            web.confirm_receipt(receipt, index, "d2", "2026-10-12T00:00:00Z")
        self.assertEqual(raised.exception.code, "RECEIPT_MISMATCH")
        self.assertEqual(json.loads(receipt.read_text()), base)                           # left exactly as it was


if __name__ == "__main__":
    unittest.main()
