"""Local HTTP contracts for contribution analytics with stubbed source services.

Does not instantiate Console, index data, read real reports, run collectors, or
fetch geography. All service calls are replaced with explicit in-memory stubs.
"""
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
spec = importlib.util.spec_from_file_location("contribution_http_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

# PptxGenJS 4.0.1 UMD browser bundle, MIT, from the npm registry tarball
# (registry dist.shasum verified on download). Vendored at
# mentat/app/vendor/pptxgen.bundle.js; the console serves it to the
# briefing exporter and nothing rewrites it, so the served bytes are pinned.
PPTXGENJS_SHA256 = '4fb9eac5cfefb213e2d8743c2b7151025f31bfb3f834c73c12062916daa0f3f8'


class ContributionHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.report_root = Path(cls.temp.name)
        cls.console = SimpleNamespace(token="contribution-test-token", read_only=True, report_directory=cls.report_root,
                                      configured_root=str(cls.report_root), root=cls.report_root)
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.server.app = cls.console
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.console.read_only = True
        guard = patch.object(app.contribution_workbook_guard, 'verify_sources', return_value=[])
        self.guard = guard.start()
        self.addCleanup(guard.stop)
        self.patches = [patch.object(app.contribution_data, "catalog", return_value={"datasets": [{"id": "Banking", "available": True}]}),
                        patch.object(app.contribution_query, "query", return_value={"rows": [{"zip5": "02134", "amount": 12300}], "totals": {"amount": 12300}}),
                        patch.object(app.contribution_query, "records", return_value={"rows": [{"source_id": "fixture-1", "zip5": "02134"}]}),
                        patch.object(app.contribution_geo, "geo_catalog", return_value={"congresses": ["119", "120"]}),
                        patch.object(app.contribution_geo, "geography", return_value={"points": [{"zip5": "02134"}], "layers": {}, "coverage": {}})]
        self.catalog, self.query, self.records, self.geo_catalog, self.geo = [p.start() for p in self.patches]
        for p in self.patches: self.addCleanup(p.stop)

    def request(self, path, *, method="GET", data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=8)
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        default_headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        if method == "POST": default_headers.update({"X-Mentat-Token": self.console.token, "Content-Type": "application/json"})
        default_headers.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=default_headers)
            response = connection.getresponse()
            content = response.read()
            parsed = json.loads(content) if response.getheader("Content-Type") == "application/json" else content
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    def test_local_session_returns_token_and_read_only_state(self):
        status, headers, body = self.request("/api/contributions/session")
        self.assertEqual(status, 200)
        # The session now carries this console's industry scope: the echo of
        # ?industry= (absent here) and the registry the picker loads once.
        self.assertEqual(body, {"token": self.console.token, "read_only": True,
                                "industry": None, "industries": []})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.query.assert_not_called()

    def test_catalog_routes_use_configured_report_directory(self):
        status, _, body = self.request("/api/contributions/catalog?directory=C:/arbitrary")
        self.assertEqual(status, 200)
        self.assertEqual(body["datasets"][0]["id"], "Banking")
        # The route reads the report folder once and threads that one scan into
        # the catalogue producer, so the call carries the directory and the list.
        self.assertEqual(self.catalog.call_count, 1)
        self.assertEqual(self.catalog.call_args.args[0], self.report_root)
        # ...and into the guard, so the folder is scanned once for the whole request.
        self.guard.assert_called_once_with(self.report_root, strict=False, sources=[])
        status, _, body = self.request("/api/contributions/geography/catalog")
        self.assertEqual((status, body["congresses"]), (200, ["119", "120"]))
        self.geo_catalog.assert_called_once_with()

    def test_workbook_mismatch_disables_only_its_dataset_and_blocks_queries(self):
        self.catalog.return_value = {'datasets': [{'id':'Banking','available':True}, {'id':'Crypto','available':True}]}
        self.guard.return_value = [{'dataset':'Banking','matched':False,'error':'Published total changed.'}]
        status, _, body = self.request('/api/contributions/catalog')
        self.assertEqual(status,200)
        self.assertFalse(body['datasets'][0]['available'])
        self.assertTrue(body['datasets'][1]['available'])
        self.guard.side_effect = ValueError('Published total changed.')
        status, _, body = self.request('/api/contributions/query',method='POST',data={'dataset':'Banking'})
        self.assertEqual(status,400)
        self.assertIn('Published total changed',body['error'])
        self.query.assert_not_called()

    def test_query_is_bound_to_the_workbook_version_that_passed_the_guard(self):
        self.guard.return_value = [{'dataset':'Banking','matched':True,'workbook_sha256':'checked-version'}]
        self.query.return_value = {'status':'ready','rows':[],'provenance':{'workbook_sha256':'changed-version'}}
        status, _, body = self.request('/api/contributions/query',method='POST',data={'dataset':'Banking'})
        self.assertEqual(status,400)
        self.assertIn('source changed',body['error'])

    def test_same_workbook_cannot_switch_database_after_verification(self):
        self.guard.return_value = [{'dataset':'Banking','matched':True,'workbook_sha256':'book',
                                    'database_sha256':'checked-db'}]
        self.query.return_value = {'status':'ready','rows':[],
                                   'provenance':{'workbook_sha256':'book','database_sha256':'changed-db'}}
        status, _, body = self.request('/api/contributions/query',method='POST',data={'dataset':'Banking'})
        self.assertEqual(status,400)
        self.assertIn('source changed',body['error'])

    def test_preparation_response_keeps_verified_source_identity(self):
        self.guard.return_value = [{'dataset':'Banking','matched':True,'workbook_sha256':'book',
                                    'database_sha256':'db','source_financial_universe_verified':True,
                                    'source_proof_token':'proof'}]
        self.query.return_value = {'status':'preparing','retry_after_ms':1500,
                                   'provenance':{'workbook_sha256':'book','database_sha256':'db'}}
        status, _, body = self.request('/api/contributions/query',method='POST',data={'dataset':'Banking'})
        self.assertEqual(status,200)
        self.assertEqual(body['status'],'preparing')
        self.assertEqual(body['provenance']['source_proof_token'],'proof')
        self.assertTrue(body['provenance']['source_financial_universe_verified'])

    def test_records_of_a_registered_conduit_say_conduit_as_the_insights_route_does(self):
        # P2-1: ActBlue is C00401224. A store built before the label read Super PAC for it; the route says Conduit.
        self.records.return_value = {"rows": [{"source_id": "a", "committee_id": "C00401224", "recipient_category": "Super PAC"},
                                              {"source_id": "b", "committee_id": "C00000001", "recipient_category": "Super PAC"}]}
        status, _, body = self.request("/api/contributions/records", method="POST", data={"dataset": "Banking"})
        self.assertEqual(status, 200)
        self.assertEqual([r["recipient_category"] for r in body["rows"]], ["Conduit", "Super PAC"])
        # An answer with no rows (a build in progress) passes through untouched.
        self.records.return_value = {"status": "building"}
        status, _, body = self.request("/api/contributions/records", method="POST", data={"dataset": "Banking"})
        self.assertEqual((status, body["status"]), (200, "building"))

    def test_read_only_mode_allows_only_analytical_post_routes(self):
        request = {"dataset": "Banking", "basis": "original_observed", "dimensions": ["zip5"], "measures": ["amount"]}
        status, _, body = self.request("/api/contributions/query", method="POST", data=request)
        self.assertEqual(status, 200)
        self.assertEqual(body["totals"]["amount"], 12300)
        # The third argument is the request's one scan of the report folder (empty here).
        self.query.assert_called_once_with(self.report_root, request, [])
        status, _, _ = self.request("/api/contributions/records", method="POST", data=request)
        self.assertEqual(status, 200)
        self.records.assert_called_once_with(self.report_root, request, [])
        geometry = {"zips": ["02134"], "layers": ["states", "districts", "zip_areas"], "congress": "119", "bbox": [-72, 41, -70, 43]}
        status, _, _ = self.request("/api/contributions/geography", method="POST", data=geometry)
        self.assertEqual(status, 200)
        self.geo.assert_called_once_with(geometry)
        for path in ("/api/upload", "/api/start", "/api/keys", "/api/evidence-collect", "/api/run-archive"):
            with self.subTest(path=path):
                status, _, body = self.request(path, method="POST", data={})
                self.assertEqual(status, 403)
                self.assertIn("read-only", body["error"])

    def test_missing_or_wrong_tokens_never_invoke_analytics(self):
        for token in ("", "wrong-token"):
            for route in ("query", "records", "geography"):
                with self.subTest(token=token, route=route):
                    status, _, _ = self.request("/api/contributions/" + route, method="POST", data={}, headers={"X-Mentat-Token": token})
                    self.assertEqual(status, 403)
        self.query.assert_not_called(); self.records.assert_not_called(); self.geo.assert_not_called()

    def test_bad_hosts_and_foreign_origins_are_rejected(self):
        for host in ("evil.example", "127.0.0.1:1", "localhost.evil.example:" + str(self.server.server_port)):
            with self.subTest(host=host):
                self.assertEqual(self.request("/api/contributions/session", headers={"Host": host})[0], 403)
                self.assertEqual(self.request("/api/contributions/query", method="POST", data={}, headers={"Host": host})[0], 403)
        for origin in ("null", "https://evil.example", "http://127.0.0.1:1", f"https://localhost:{self.server.server_port}"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request("/api/contributions/query", method="POST", data={}, headers={"Origin": origin})[0], 403)
        self.query.assert_not_called()

    def test_both_local_origins_can_query(self):
        for host in ("127.0.0.1", "localhost"):
            status, _, _ = self.request("/api/contributions/query", method="POST", data={}, headers={"Host": f"{host}:{self.server.server_port}", "Origin": f"http://{host}:{self.server.server_port}"})
            self.assertEqual(status, 200)
        self.assertEqual(self.query.call_count, 2)

    def test_invalid_json_shape_length_and_unknown_routes_fail(self):
        for raw in (b"[]", b"null", b"false", b"{broken", b""):
            with self.subTest(raw=raw):
                self.assertEqual(self.request("/api/contributions/query", method="POST", raw=raw)[0], 400)
        # Declares a length it never sends: the refusal must answer at once,
        # never wait for bytes that are not coming.
        started = time.monotonic()
        status, _, _ = self.request("/api/contributions/query", method="POST", raw=b"{}", headers={"Content-Length": "7000001"})
        self.assertEqual(status, 400)
        self.assertLess(time.monotonic() - started, 3, "an oversized declaration must not stall the handler")
        self.assertEqual(self.request("/api/contributions/query")[0], 404)
        self.assertEqual(self.request("/api/contributions/unknown")[0], 404)
        self.query.assert_not_called()

    def test_service_validation_failure_is_useful_json(self):
        for error in (ValueError("Invalid dimension"), RuntimeError("Source fingerprint changed"), OSError("Source unavailable")):
            with self.subTest(error=error):
                self.query.side_effect = error
                status, _, body = self.request("/api/contributions/query", method="POST", data={})
                self.assertEqual(status, 400)
                self.assertEqual(body["error"], str(error))
        self.geo.side_effect = app.contribution_geo.GeographyError("Congress must be 119 or 120.")
        status, _, body = self.request("/api/contributions/geography", method="POST", data={"congress": "121"})
        self.assertEqual((status, body["error"]), (400, "Congress must be 119 or 120."))

    def test_assets_are_same_origin_and_accessible_in_read_only_mode(self):
        for route, mime in (("/contribution-studio", "text/html; charset=utf-8"), ("/contribution-studio.js", "text/javascript"),
                            ("/contribution-studio.css", "text/css"), ("/contribution-map.js", "text/javascript"),
                            ("/contribution-plot.js", "text/javascript"), ("/chart-export.js", "text/javascript")):
            with self.subTest(route=route):
                status, headers, body = self.request(route)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], mime)
                self.assertGreater(len(body), 50)
                self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/../server.py")[0], 404)

    def test_health_states_the_build_this_process_loaded_beside_the_one_on_disk(self):
        status, _, body = self.request('/api/health')
        self.assertEqual(status, 200)
        for field in ('git_sha', 'code_sha256', 'assets_sha256', 'on_disk_code_sha256_now',
                      'on_disk_assets_sha256_now', 'started_at', 'port', 'pid', 'busy',
                      'server_code_sha256', 'read_only'):
            with self.subTest(field=field):
                self.assertIn(field, body)
        self.assertEqual(body['code_sha256'], app.CODE_TREE_SHA256)
        self.assertEqual(body['assets_sha256'], app.ASSET_TREE_SHA256)
        # Nothing was edited between import and this request.
        self.assertEqual(body['on_disk_code_sha256_now'], body['code_sha256'])
        self.assertEqual(body['on_disk_assets_sha256_now'], body['assets_sha256'])
        self.assertNotEqual(body['code_sha256'], body['server_code_sha256'],
                            'the tree hash must not collapse to the old single-file hash')
        self.assertEqual((body['pid'], body['port']), (os.getpid(), self.server.server_port))
        self.assertIs(body['busy'], False)
        self.assertGreater(body['started_at'], 0)
        _, _, light = self.request('/api/session?light=1')
        self.assertEqual(light['code_sha256'], app.CODE_TREE_SHA256)
        self.assertEqual(light['build']['code_sha256'], app.CODE_TREE_SHA256)
        self.assertEqual(light['build']['pid'], os.getpid())

    def test_the_code_hash_covers_every_python_module_beside_the_server(self):
        directory = Path(app.__file__).resolve().parent
        names = sorted(path.name for path in directory.glob('*.py'))
        self.assertIn('server.py', names)
        self.assertIn('contribution_query.py', names)
        self.assertEqual([path.name for path in app.code_files()], names)
        self.assertIn(directory / 'index.html', app.asset_files())
        self.assertIn(directory / 'chart-studio.js', app.asset_files())

    def test_restart_is_refused_read_only_and_while_the_console_is_busy(self):
        status, _, body = self.request('/api/restart', method='POST', data={})
        self.assertEqual(status, 403)
        self.assertIn('read-only', body['error'])
        self.console.read_only = False
        self.console.busy = lambda: True
        self.addCleanup(self.console.__dict__.pop, 'busy', None)
        with patch.object(app.subprocess, 'Popen') as spawn:
            status, _, body = self.request('/api/restart', method='POST', data={})
        self.assertEqual(status, 409)
        self.assertIn('Collection is running. Restart after it finishes.', body['error'])
        spawn.assert_not_called()
        self.console.busy = lambda: False
        self.console.restart = lambda port: {'restarting': True, 'port': port}
        self.addCleanup(self.console.__dict__.pop, 'restart', None)
        # This process loaded exactly the build that is on disk, so there is
        # nothing to restart and the console says so instead of spawning a
        # launcher that would only reuse the process already running - which
        # left the reader watching a poll for a new pid that never came, and
        # two minutes later being told to close the tab.
        with patch.object(app.subprocess, 'Popen') as spawn:
            status, _, body = self.request('/api/restart', method='POST', data={})
        self.assertEqual(status, 200)
        self.assertEqual((body['restarting'], body['current']), (False, True))
        self.assertEqual(body['build']['code_sha256'], body['build']['on_disk_code_sha256_now'])
        self.assertEqual(body['build']['assets_sha256'], body['build']['on_disk_assets_sha256_now'])
        self.assertEqual(body['build']['pid'], os.getpid())
        spawn.assert_not_called()
        # Changed Python modules on disk: a real restart, as before.
        with patch.object(app, 'CODE_TREE_SHA256', 'a' * 64):
            status, _, body = self.request('/api/restart', method='POST', data={})
        self.assertEqual((status, body), (200, {'restarting': True, 'port': self.server.server_port}))
        # Changed interface files alone are enough on their own; the branch has
        # to read both trees, not just the Python one.
        with patch.object(app, 'ASSET_TREE_SHA256', 'b' * 64):
            status, _, body = self.request('/api/restart', method='POST', data={})
        self.assertEqual((status, body), (200, {'restarting': True, 'port': self.server.server_port}))

    def test_a_console_that_cannot_answer_busy_is_reported_busy(self):
        def unavailable():
            raise RuntimeError('collection state unreadable')
        self.console.read_only = False
        self.console.busy = unavailable
        self.addCleanup(self.console.__dict__.pop, 'busy', None)
        status, _, body = self.request('/api/restart', method='POST', data={})
        self.assertEqual(status, 409)
        _, _, health = self.request('/api/health')
        self.assertIs(health['busy'], True)

    def test_restart_hands_the_launcher_this_console_port_and_data_root(self):
        console = SimpleNamespace(configured_root='C:\\Mentat data', keys_file='', rscript='C:/R/Rscript.exe')
        with patch.object(app.subprocess, 'Popen') as spawn:
            answer = app.Console.restart(console, 8802)
        self.assertEqual(answer, {'restarting': True, 'port': 8802, 'pid': os.getpid()})
        command = spawn.call_args.args[0]
        self.assertTrue(command[0].lower().endswith('powershell.exe'), command[0])
        self.assertTrue(command[command.index('-File') + 1].endswith('Start-MentatApp.ps1'))
        self.assertIn('-NoBrowser', command)
        self.assertEqual(command[command.index('-Port') + 1], '8802')
        # Identity is project path plus data root: dropping it would open a
        # second console on another port instead of replacing this one.
        self.assertEqual(command[command.index('-DataRoot') + 1], console.configured_root)
        self.assertEqual(command[command.index('-RScriptPath') + 1], console.rscript)
        self.assertNotIn('-KeysFile', command)
        self.assertEqual(spawn.call_args.kwargs['cwd'], str(app.PROJECT))
        if os.name == 'nt':
            flags = spawn.call_args.kwargs['creationflags']
            # DETACHED_PROCESS gives powershell.exe no console and it exits
            # without running the launcher; the restart must not use it.
            self.assertTrue(flags & app.subprocess.CREATE_NO_WINDOW)
            self.assertTrue(flags & app.subprocess.CREATE_NEW_PROCESS_GROUP)
            self.assertFalse(flags & app.subprocess.DETACHED_PROCESS)

    def test_restart_keeps_this_consoles_interpreter(self):
        # Lane V: the launcher is handed -PythonPath sys.executable, so the replacement runs on the
        # interpreter this console runs on (under Mentat's venv, the venv's python.exe) rather than on
        # whatever the launcher would choose afresh. The second interpreter is the negative control: a
        # restart() that passed a fixed or freshly chosen path instead of this process's own fails it.
        console = SimpleNamespace(configured_root='C:\\Mentat data', keys_file='', rscript='')
        for running in (app.sys.executable, 'C:\\Elsewhere\\venv\\Scripts\\python.exe'):
            with self.subTest(interpreter=running), patch.object(app.sys, 'executable', running), \
                    patch.object(app.subprocess, 'Popen') as spawn:
                app.Console.restart(console, 8802)
            command = spawn.call_args.args[0]
            self.assertEqual(command.count('-PythonPath'), 1, command)
            self.assertEqual(command[command.index('-PythonPath') + 1], running)

    def test_a_refused_post_with_a_large_body_answers_cleanly(self):
        # A refusal that closed the socket with a megabyte still queued made
        # Windows send RST, and this client lost the response it was reading.
        payload = json.dumps({"dataset": "Banking", "filler": "x" * 1_000_000}).encode()
        self.assertGreater(len(payload), 1_000_000)
        for label, path, headers in (("read-only", "/api/upload", None),
                                     ("wrong token", "/api/contributions/query", {"X-Mentat-Token": "wrong-token"}),
                                     ("foreign origin", "/api/contributions/query", {"Origin": "https://evil.example"})):
            with self.subTest(refusal=label):
                self.console.read_only = label == "read-only"
                status, _, body = self.request(path, method="POST", raw=payload, headers=headers)
                self.assertEqual(status, 403)
                self.assertIn("error", body)
        self.query.assert_not_called()
        self.records.assert_not_called()

    def test_a_retired_pane_url_is_framed_by_nobody(self):
        # The 2026-09-13 split view relaxed frame-ancestors to 'self' for
        # /?studioPane=B|C|D so a pane could frame this same document. The strip
        # was retired on 2026-09-19 and nothing frames this console any more, so
        # every response is back to 'none' - including the old pane URL.
        for path in ('/', '/?studioPane=B', '/?studioPane=C', '/?studioPane=D', '/?studioPane=E'):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
                self.assertNotIn("frame-ancestors 'self'", headers["Content-Security-Policy"])

    def test_the_vendor_powerpoint_bundle_is_served_at_its_pinned_hash(self):
        status, headers, body = self.request('/vendor/pptxgen.bundle.js')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'text/javascript')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        # The bytes the browser receives, not the bytes on disk.
        self.assertEqual(hashlib.sha256(body).hexdigest(), PPTXGENJS_SHA256)

    def test_the_briefing_and_vendor_routes_are_allow_listed_with_their_media_types(self):
        directory = Path(app.__file__).resolve().parent
        for route, mime in (('/chart-briefing.js', 'text/javascript'),
                            ('/chart-briefing.css', 'text/css'),
                            ('/vendor/pptxgen.bundle.js', 'text/javascript')):
            with self.subTest(route=route):
                filename, declared = app.STATIC_ASSETS[route]
                self.assertEqual(declared, mime)
                status, headers, body = self.request(route)
                # The two chart-briefing files belong to the parallel client
                # lane. Until they land the route answers 404, not a 400
                # carrying a local path; once they land it serves them.
                if (directory / filename).is_file():
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Content-Type'], mime)
                    self.assertGreater(len(body), 50)
                else:
                    self.assertEqual((status, body), (404, {'error': 'Not found.'}))

    def test_busy_reports_console_owned_work_only(self):
        console = SimpleNamespace(preparing=set(), jobs=set(), processes={})
        self.assertFalse(app.Console.busy(console))
        console.jobs.add('snapshot-1')
        self.assertTrue(app.Console.busy(console))
        console.jobs.clear()
        console.preparing.add('upload-1')
        self.assertTrue(app.Console.busy(console))
        console.preparing.clear()
        console.processes['run-1'] = SimpleNamespace(poll=lambda: None)
        self.assertTrue(app.Console.busy(console))
        console.processes['run-1'] = SimpleNamespace(poll=lambda: 0)
        self.assertFalse(app.Console.busy(console))


if __name__ == "__main__": unittest.main()
