"""Exercise the real HTTP handler with a fake, non-persisting identity service."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest

from fec.surfaces.staffer_live_server import Handler


class FakeIdentityService:
    def __init__(self):
        self.calls = []
        self.failure = None

    def assertion_decision(self, payload):
        self.calls.append(payload)
        return {'ok': True, 'status': 'reindexing'}

    def identity_decision(self, primary, aliases, decision):
        self.calls.append((primary, aliases, decision))
        if self.failure:
            raise self.failure
        if primary != "person:primary" or aliases != ["person:alias"] or decision not in {"merge", "keep_separate"}:
            raise ValueError("Pair is not a displayed identity candidate")
        return {"ok": True, "status": "reindexing", "decision": decision}


class IdentityHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeIdentityService()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.dashboard = self.service
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def post(self, payload=None, *, path="/api/staffer-identity", body=None, headers=None):
        if body is None:
            body = json.dumps(payload if payload is not None else {
                "primary_id": "person:primary", "alias_ids": ["person:alias"], "decision": "merge"}).encode()
        request_headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"}
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request("POST", path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            result = json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data.decode()
            return response.status, result, dict(response.getheaders())
        finally:
            connection.close()

    def test_statement_panel_script_is_served_as_javascript(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request('GET', '/staffer-live/staffer-assertions.js')
            response = connection.getresponse()
            body = response.read().decode()
            self.assertEqual(response.status, 200)
            self.assertIn('application/javascript', response.getheader('Content-Type'))
            self.assertIn('assertion-records', body)
        finally:
            connection.close()

    def test_assertion_endpoint_uses_local_origin_gate(self):
        decision = {'assertion_id': 'statement', 'action': 'retract', 'effective_date': '2026-09-05'}
        status, result, _ = self.post(decision, path='/api/staffer-assertions')
        self.assertEqual(status, 202)
        self.assertEqual(self.service.calls, [decision])
        status, _, _ = self.post(decision, path='/api/staffer-assertions', headers={'Origin': 'https://unrelated.example'})
        self.assertEqual(status, 400)
        self.assertEqual(self.service.calls, [decision])

    def test_valid_displayed_pair_returns_202_and_exact_arguments(self):
        status, payload, headers = self.post()
        self.assertEqual(status, 202)
        self.assertEqual(payload, {"ok": True, "status": "reindexing", "decision": "merge"})
        self.assertEqual(self.service.calls, [("person:primary", ["person:alias"], "merge")])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_keep_separate_decision_is_preserved(self):
        status, payload, _ = self.post({"primary_id": "person:primary", "alias_ids": ["person:alias"], "decision": "keep_separate"},
                                       headers={"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(status, 202)
        self.assertEqual(payload["decision"], "keep_separate")

    def test_malformed_json_and_non_object_payloads_are_rejected_before_dispatch(self):
        for body in (b"{broken", b"[]", b"null", b'"string"'):
            with self.subTest(body=body):
                status, payload, _ = self.post(body=body)
                self.assertEqual(status, 400)
                self.assertIn("error", payload)
        self.assertEqual(self.service.calls, [])

    def test_content_type_and_size_limits_reject_before_dispatch(self):
        requests = [(b"{}", {"Content-Type": "text/plain"}),
                    (b"", {"Content-Length": "0"}),
                    (b"{}", {"Content-Length": "invalid"}),
                    (b"x" * 16385, {})]
        for body, headers in requests:
            with self.subTest(headers=headers, size=len(body)):
                status, payload, _ = self.post(body=body, headers=headers)
                self.assertEqual(status, 400)
                self.assertIn("error", payload)
        self.assertEqual(self.service.calls, [])

    def test_cross_site_wrong_origin_scheme_and_port_are_rejected(self):
        headers_list = [{"Origin": "https://example.test"},
                        {"Origin": f"https://127.0.0.1:{self.port}"},
                        {"Origin": "http://127.0.0.1:1"},
                        {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "cross-site"}]
        for headers in headers_list:
            with self.subTest(headers=headers):
                status, payload, _ = self.post(headers=headers)
                self.assertEqual(status, 400)
                self.assertIn("error", payload)
        self.assertEqual(self.service.calls, [])

    def test_foreign_host_header_is_refused_on_post_without_dispatch(self):
        # DNS rebinding: Origin is the page's own (rebound) name or absent, so Host is the only tell.
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}"):
            for path in ("/api/staffer-identity", "/unexpected"):
                with self.subTest(host=host, path=path):
                    status, payload, _ = self.post(path=path, headers={"Host": host})
                    self.assertEqual(status, 403)
                    self.assertEqual(payload, {"error": "Local access only."})
        self.assertEqual(self.service.calls, [])

    def test_loopback_host_names_with_the_bound_port_are_accepted_on_post(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"LocalHost:{self.port}", f"[::1]:{self.port}"):
            with self.subTest(host=host):
                status, _, _ = self.post(headers={"Host": host})
                self.assertEqual(status, 202)
        self.assertEqual(len(self.service.calls), 4)

    def test_foreign_host_header_is_refused_on_get_but_loopback_is_served(self):
        for host, expected in (("evil.example", 403), (f"evil.example:{self.port}", 403), (f"127.0.0.1:{self.port}", 200),
                               (f"localhost:{self.port}", 200)):
            with self.subTest(host=host):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
                try:
                    connection.request("GET", "/staffer-live/staffer-assertions.js", headers={"Host": host})
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, expected)
                    if expected == 403:
                        self.assertNotIn(b"assertion-records", body)
                finally:
                    connection.close()

    def test_unexpected_post_path_is_404_without_dispatch(self):
        for path in ("/api/staffer-route", "/api/staffer-identity/", "/unexpected"):
            with self.subTest(path=path):
                status, _, _ = self.post(path=path)
                self.assertEqual(status, 404)
        self.assertEqual(self.service.calls, [])

    def test_service_validation_error_is_400_and_save_failure_is_503(self):
        status, payload, _ = self.post({"primary_id": "person:unknown", "alias_ids": ["person:alias"], "decision": "merge"})
        self.assertEqual(status, 400)
        self.assertIn("displayed identity candidate", payload["error"])
        self.service.failure = OSError("fixture save unavailable")
        status, payload, _ = self.post()
        self.assertEqual(status, 503)
        self.assertIn("Could not save identity decision", payload["error"])


if __name__ == "__main__":
    unittest.main()
