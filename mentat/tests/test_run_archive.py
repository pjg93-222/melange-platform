"""Run archives hide history reversibly without editing collections or reports."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import server as app


class RunArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reports = self.root / 'Complete reports'
        self.reports.mkdir()
        for active in (patch.object(app, 'report_directory', return_value=self.reports),
                       patch.object(app, 'PROJECT', self.root),
                       patch.object(app, 'environment_keys', return_value=[]),
                       patch.object(app, 'key_status', return_value={'active_key_count':0})):
            active.start()
            self.addCleanup(active.stop)
        self.console = app.Console(self.root / 'state')
        self.next_id = 0

    def run_fixture(self, state='completed'):
        self.next_id += 1
        identifier = f'{self.next_id:032x}'
        folder = self.console.root / identifier
        folder.mkdir()
        app.atomic_json(folder / 'upload.json', {
            'id':identifier, 'name':'Fixture', 'created_at':self.next_id, 'state':state})
        (folder / 'preserved-evidence.txt').write_text('Keep original evidence.')
        return identifier

    def test_archive_survives_restart_and_restore_preserves_run_files_and_coverage(self):
        identifier = self.run_fixture()
        originals = {path:path.read_bytes() for path in (self.console.root / identifier).rglob('*') if path.is_file()}
        report = self.reports / 'Banking.xlsx'
        report.write_bytes(b'unchanged final report')
        self.assertEqual(self.console.archive_run({'id':identifier, 'archived':True}),
                         {'id':identifier, 'archived':True})
        restarted = app.Console(self.console.root)
        with patch.object(restarted, 'coverage_catalog', return_value=[]) as coverage:
            hidden = restarted.summary()
        self.assertEqual(hidden['runs'], [])
        self.assertEqual(hidden['archived_count'], 1)
        self.assertEqual(coverage.call_args.args[0][0]['id'], identifier)
        shown = restarted.summary(include_archived=True)
        self.assertTrue(shown['runs'][0]['archived'])
        self.assertTrue(shown['runs'][0]['can_archive'])
        self.assertEqual(shown['collection_directory'], str(self.console.root))
        self.assertEqual(shown['report_directory'], str(self.reports))
        restarted.archive_run({'id':identifier, 'archived':False})
        self.assertFalse(restarted.summary()['runs'][0]['archived'])
        self.assertEqual(restarted.summary()['archived_count'], 0)
        self.assertEqual(originals, {path:path.read_bytes() for path in originals})
        self.assertEqual(report.read_bytes(), b'unchanged final report')

    def test_active_or_unknown_runs_cannot_be_archived(self):
        for state in ('preparing', 'starting', 'running', 'queued', 'collecting', 'stopping', 'existing run'):
            with self.subTest(state=state):
                identifier = self.run_fixture(state)
                with self.assertRaisesRegex(ValueError, 'cannot be archived'):
                    self.console.archive_run({'id':identifier, 'archived':True})
        self.assertFalse((self.console.root / 'run-archives.json').exists())

    def test_preparing_or_owned_live_process_cannot_be_archived(self):
        identifier = self.run_fixture('ready')
        self.console.preparing.add(identifier)
        with self.assertRaises(ValueError):
            self.console.archive_run({'id':identifier, 'archived':True})
        self.console.preparing.clear()
        process = MagicMock()
        process.poll.return_value = None
        self.console.processes[identifier] = process
        with self.assertRaises(ValueError):
            self.console.archive_run({'id':identifier, 'archived':True})

    def test_saved_live_runner_blocks_archive_after_console_restart(self):
        identifier = self.run_fixture('completed')
        path = self.console.root / identifier / 'upload.json'
        manifest = app.read_json(path)
        manifest['pid'] = 123456
        app.atomic_json(path, manifest)
        restarted = app.Console(self.console.root)
        with patch.object(app, 'process_alive', return_value=True) as alive:
            with self.assertRaisesRegex(ValueError, 'cannot be archived'):
                restarted.archive_run({'id':identifier, 'archived':True})
        alive.assert_called_with(123456)

    def test_archived_roster_requires_restore_before_launch(self):
        identifier = self.run_fixture('ready')
        self.console.archive_run({'id':identifier, 'archived':True})
        with patch.object(app, 'load_keys', side_effect=AssertionError('must not prepare collection')):
            with self.assertRaisesRegex(ValueError, 'Restore this saved roster'):
                self.console.launch({'id':identifier, 'cycles':[2026]})

    def test_archive_http_requires_authorization_and_read_write_mode(self):
        identifier = self.run_fixture('stopped')
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        server.app = self.console
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        payload = json.dumps({'id':identifier, 'archived':True}).encode()
        headers = {'Content-Type':'application/json', 'X-Mentat-Token':self.console.token}
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(origin + '/api/run-archive', data=payload))
            self.assertEqual(error.exception.code, 403)
            self.console.read_only = True
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(origin + '/api/run-archive', data=payload, headers=headers))
            self.assertEqual(error.exception.code, 403)
            self.console.read_only = False
            with urlopen(Request(origin + '/api/run-archive', data=payload, headers=headers)) as response:
                self.assertTrue(json.load(response)['archived'])
            with urlopen(origin + '/api/runs') as response:
                self.assertEqual(json.load(response)['runs'], [])
            with urlopen(origin + '/api/runs?include_archived=1') as response:
                self.assertTrue(json.load(response)['runs'][0]['archived'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
