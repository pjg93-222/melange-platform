"""Publishing the Mentat Pocket snapshot: validation, one job at a time, job state.

The two child scripts are stubbed. What is asserted here is the console's half
of the contract -- which scripts it starts, with which arguments, against its own
port, how the job state moves, and what a second caller sees while one runs. The
scripts themselves are exercised for real against a throwaway console elsewhere
in this lane; running them here would make this suite depend on a report.
"""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
spec = importlib.util.spec_from_file_location("publish_pocket_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

TOOLS = Path(app.PROJECT) / 'tools' / 'pocket'


class FakeProcess:
    """A child that finishes when told to, with a chosen exit code."""

    def __init__(self, code=0, gate=None, before=None):
        self.pid = 424242
        self.code = code
        self.gate = gate
        self.before = before

    def wait(self):
        if self.gate is not None and not self.gate.wait(20):
            raise AssertionError('the gated child was never released')
        if self.before is not None:
            self.before()
        return self.code


class PublishPocketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A worker thread holds publish.log open for the length of its job, and
        # Windows refuses to delete an open file, so cleanup is best effort.
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.data_root = Path(cls.temp.name) / 'data'
        cls.report_root = Path(cls.temp.name) / 'reports'
        cls.data_root.mkdir()
        cls.report_root.mkdir()
        with patch.object(app, 'report_directory', return_value=cls.report_root):
            cls.console = app.Console(str(cls.data_root), read_only=True)
        cls.console.read_only = False
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server.app = cls.console
        cls.console.port = cls.server.server_port
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        deadline = time.time() + 10
        while cls.console.jobs and time.time() < deadline:
            time.sleep(0.05)
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.console.read_only = False
        self.commands = []
        self.checks = []
        self.out = self.report_root / 'pocket'
        self.page = self.out / 'mentat-pocket.html'
        for name in ('mentat-pocket.html', 'icon-180.png', 'manifest.webmanifest',
                     'pocket-data.json', 'pocket-integrity.json'):
            if (self.out / name).exists():
                (self.out / name).unlink()
        catalog = patch.object(app.contribution_query.semantics, 'catalog',
                               return_value={'datasets': [{'id': 'Crypto'}, {'id': 'Banking'}]})
        self.catalog = catalog.start()
        self.addCleanup(catalog.stop)

    def tearDown(self):
        # A publish starts a daemon thread in server.py (guarded/publish) that
        # keeps calling app.subprocess.Popen/.run -- mocked fresh per test by
        # stub_children -- until it reaches 'done'/'failed' and, in its
        # finally clause, discards its id from self.console.jobs. A test that
        # starts a publish and returns without waiting for it leaves that
        # thread alive into the next test: the leaked thread then records its
        # Popen calls into whichever test's self.commands is current by the
        # time it gets scheduled, and races that test's setUp file cleanup
        # (e.g. unlinking icon-180.png) for a real Windows PermissionError.
        # self.console.jobs emptying is the closest signal to Thread.join()
        # this worker exposes, so wait for it here -- and fail loudly, rather
        # than silently resetting shared state -- if one is still running.
        deadline = time.time() + 20
        while self.console.jobs and time.time() < deadline:
            time.sleep(0.02)
        self.assertFalse(self.console.jobs,
                         'worker(s) still running at teardown: ' + ', '.join(sorted(self.console.jobs)))

    def write_page(self):
        self.page.parent.mkdir(parents=True, exist_ok=True)
        self.page.write_text('<!doctype html><html><body>pocket</body></html>', encoding='utf-8')

    def stub_children(self, *, extract_code=0, build_code=0, gate=None, build_writes=True,
                      selfcheck_code=0, selfcheck_out='ALL CHECKS PASSED\n'):
        def popen(command, **keywords):
            self.commands.append((list(command), dict(keywords)))
            if Path(command[1]).name == 'extract_pocket_data.py':
                return FakeProcess(extract_code, gate)
            return FakeProcess(build_code, None, self.write_page if build_writes else None)

        def run(command, **keywords):
            # The self-check is started with subprocess.run, not Popen, and
            # subprocess.run would otherwise reach the Popen stub above.
            self.checks.append((list(command), dict(keywords)))
            return SimpleNamespace(returncode=selfcheck_code, stdout=selfcheck_out, stderr='')

        for attribute, replacement in (('Popen', popen), ('run', run)):
            patcher = patch.object(app.subprocess, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, path, *, method='GET', data=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        body = json.dumps(data).encode() if data is not None else None
        headers = {'Host': '127.0.0.1:{}'.format(self.server.server_port)}
        if method == 'POST':
            headers.update({'X-Mentat-Token': self.console.token, 'Content-Type': 'application/json'})
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            parsed = json.loads(content) if response.getheader('Content-Type') == 'application/json' else content
            return response.status, dict(response.getheaders()), parsed
        finally:
            connection.close()

    def settled(self, identifier, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _, job = self.request('/api/publish/pocket?id=' + identifier)
            self.assertEqual(status, 200)
            if job['status'] in ('done', 'failed'):
                return job
            time.sleep(0.05)
        raise AssertionError('the publication never settled: ' + json.dumps(job))

    def test_a_publish_runs_the_extractor_then_the_builder_against_this_console(self):
        self.stub_children()
        status, _, body = self.request('/api/publish/pocket', method='POST', data={'datasets': ['Crypto']})
        self.assertEqual(status, 200)
        identifier = body['job']['id']
        self.assertRegex(identifier, '^publish-[a-f0-9]{32}$')
        self.assertEqual(body['job']['datasets'], ['Crypto'])

        job = self.settled(identifier)
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['output_path'], str(self.report_root / 'pocket' / 'mentat-pocket.html'))
        self.assertTrue(Path(job['output_path']).is_file())
        self.assertGreaterEqual(job['finished_at'], job['started_at'])
        self.assertEqual(json.loads((self.data_root / 'publish' / identifier / 'job.json')
                                    .read_text(encoding='utf-8'))['status'], 'done')

        self.assertEqual(len(self.commands), 2)
        extract, build = self.commands
        self.assertEqual(extract[0], [sys.executable, str(TOOLS / 'extract_pocket_data.py'),
                                      '--port', str(self.server.server_port),
                                      '--out-dir', str(self.report_root / 'pocket'),
                                      '--datasets', 'Crypto'])
        self.assertEqual(build[0], [sys.executable, str(TOOLS / 'build_page.py'),
                                    '--data', str(self.report_root / 'pocket' / 'pocket-data.json'),
                                    '--out', str(self.report_root / 'pocket' / 'mentat-pocket.html')])
        for command, keywords in self.commands:
            with self.subTest(script=Path(command[1]).name):
                self.assertEqual(keywords['creationflags'],
                                 subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.assertEqual(keywords['stderr'], subprocess.STDOUT)
        self.assertFalse(self.console.busy())

    def test_the_page_ships_with_its_icon_and_manifest_and_records_what_landed(self):
        # The page links both as siblings; publishing without them produces a
        # page a phone cannot install, which is what ride step 31 exercises.
        self.stub_children()
        identifier = self.request('/api/publish/pocket', method='POST',
                                  data={'datasets': ['Crypto']})[2]['job']['id']
        job = self.settled(identifier)
        self.assertEqual(job['status'], 'done')
        source = TOOLS
        for name in ('icon-180.png', 'manifest.webmanifest'):
            with self.subTest(asset=name):
                self.assertTrue((self.out / name).is_file())
                self.assertEqual((self.out / name).read_bytes(), (source / name).read_bytes())
        self.assertEqual(job['output_files'],
                         [str(self.out / 'mentat-pocket.html'), str(self.out / 'icon-180.png'),
                          str(self.out / 'manifest.webmanifest')])
        self.assertEqual(job['selfcheck'], 'pass')
        self.assertEqual(job['selfcheck_message'], 'ALL CHECKS PASSED')
        self.assertEqual(len(self.checks), 1)
        self.assertEqual(self.checks[0][0], [sys.executable, str(TOOLS / 'selfcheck.py'),
                                             '--dir', str(self.out)])

    def test_a_failed_self_check_is_recorded_without_failing_the_publish(self):
        self.stub_children(selfcheck_code=1, selfcheck_out='  FAIL  totals\n2 CHECK(S) FAILED\n')
        identifier = self.request('/api/publish/pocket', method='POST',
                                  data={'datasets': ['Crypto']})[2]['job']['id']
        job = self.settled(identifier)
        # Advisory, not fatal: the page was published and the verdict is visible.
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['selfcheck'], 'fail')
        self.assertEqual(job['selfcheck_message'], '2 CHECK(S) FAILED')
        self.assertTrue(self.page.is_file())

    def test_a_publish_that_never_built_a_page_runs_no_self_check(self):
        self.stub_children(extract_code=2)
        identifier = self.request('/api/publish/pocket', method='POST',
                                  data={'datasets': ['Crypto']})[2]['job']['id']
        job = self.settled(identifier)
        self.assertEqual(job['status'], 'failed')
        self.assertIsNone(job['selfcheck'])
        self.assertEqual(self.checks, [])
        # The assets were still copied, so output_files reports them honestly.
        self.assertEqual(job['output_files'],
                         [str(self.out / 'icon-180.png'), str(self.out / 'manifest.webmanifest')])

    def test_a_second_publish_while_one_is_running_is_a_conflict(self):
        gate = threading.Event()
        self.stub_children(gate=gate)
        self.addCleanup(gate.set)
        status, _, body = self.request('/api/publish/pocket', method='POST', data={'datasets': ['Crypto']})
        self.assertEqual(status, 200)
        identifier = body['job']['id']
        deadline = time.time() + 10
        while not self.commands and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(self.commands, 'the first publication never started its extractor')
        self.assertTrue(self.console.busy(), 'a running publication must make the console busy')

        status, _, refusal = self.request('/api/publish/pocket', method='POST', data={'datasets': ['Banking']})
        self.assertEqual(status, 409)
        self.assertIn('error', refusal)
        self.assertEqual(len(self.commands), 1)

        gate.set()
        self.assertEqual(self.settled(identifier)['status'], 'done')
        self.assertFalse(self.console.busy())
        status, _, body = self.request('/api/publish/pocket', method='POST',
                                       data={'datasets': ['Banking']})
        self.assertEqual(status, 200)
        # Wait for this one too: an unsettled publish leaves its worker
        # thread running past the end of the test -- see tearDown.
        self.assertEqual(self.settled(body['job']['id'])['status'], 'done')

    def test_a_child_that_fails_leaves_a_failed_job_that_names_its_log(self):
        self.stub_children(extract_code=3)
        identifier = self.request('/api/publish/pocket', method='POST',
                                  data={'datasets': ['Crypto']})[2]['job']['id']
        job = self.settled(identifier)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('publish.log', job['message'])
        self.assertFalse(Path(job['output_path']).exists())
        self.assertEqual(len(self.commands), 1, 'the page must not be built from a failed read')
        self.assertFalse(self.console.busy())

    def test_a_builder_that_writes_no_page_is_a_failure_even_at_exit_zero(self):
        self.stub_children(build_writes=False)
        identifier = self.request('/api/publish/pocket', method='POST',
                                  data={'datasets': ['Crypto']})[2]['job']['id']
        job = self.settled(identifier)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('not written', job['message'])

    def test_datasets_have_to_be_a_non_empty_list_of_catalog_ids(self):
        self.stub_children()
        for label, payload in (('missing', {}), ('empty', {'datasets': []}),
                               ('not a list', {'datasets': 'Crypto'}),
                               ('blank entry', {'datasets': ['  ']}),
                               ('unknown id', {'datasets': ['Crypto', 'Telecom']})):
            with self.subTest(datasets=label):
                status, _, body = self.request('/api/publish/pocket', method='POST', data=payload)
                self.assertEqual(status, 400)
                self.assertIn('error', body)
        self.assertEqual(self.commands, [])
        self.assertFalse(self.console.busy())

    def test_a_console_that_does_not_know_its_port_refuses_to_publish(self):
        self.stub_children()
        with patch.object(self.console, 'port', 0):
            status, _, body = self.request('/api/publish/pocket', method='POST', data={'datasets': ['Crypto']})
        self.assertEqual(status, 400)
        self.assertIn('Restart the console, then publish again.', body['error'])
        self.assertEqual(self.commands, [])

    def test_a_read_only_console_refuses_to_publish_and_still_reports_state(self):
        self.stub_children()
        identifier = self.request('/api/publish/pocket', method='POST',
                                  data={'datasets': ['Crypto']})[2]['job']['id']
        self.settled(identifier)
        self.console.read_only = True
        status, _, body = self.request('/api/publish/pocket', method='POST', data={'datasets': ['Crypto']})
        self.assertEqual(status, 403)
        self.assertIn('error', body)
        self.assertEqual(len(self.commands), 2, 'a refused publish must start no child')
        self.assertEqual(self.request('/api/publish/pocket?id=' + identifier)[2]['status'], 'done')

    def test_an_unknown_publication_is_404_and_a_malformed_one_is_400(self):
        self.assertEqual(self.request('/api/publish/pocket?id=publish-' + 'a' * 32)[0], 404)
        for bad in ('', 'nope', 'a' * 32, 'publish-' + 'A' * 32, '../../job'):
            with self.subTest(identifier=bad):
                self.assertEqual(self.request('/api/publish/pocket?id=' + bad)[0], 400)

    def test_the_port_the_console_was_given_is_the_port_it_publishes_against(self):
        # main() sets Console.port from the parsed --port before serve_forever;
        # this is the invariant the extractor depends on to reach its console.
        source = (APP / 'server.py').read_text(encoding='utf-8')
        self.assertIn('server.app.port = options.port or server.server_port', source)
        self.assertLess(source.index('server.app.port = options.port'), source.index('serve_forever()'))


if __name__ == '__main__':
    unittest.main()
