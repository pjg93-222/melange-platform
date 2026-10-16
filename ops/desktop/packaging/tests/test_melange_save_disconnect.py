"""Transport failures must not retry or misreport an already committed save."""
from email.message import Message
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from fec.surfaces import staffer_assertions, staffer_identity_resolution as identity
from fec.surfaces.staffer_live_server import Handler


class Writer:
    def __init__(self, fail_at=None, error=BrokenPipeError):
        self.fail_at, self.error, self.writes = fail_at, error, []

    def write(self, data):
        self.writes.append(data)
        if self.fail_at == len(self.writes):
            raise self.error('fixture transport disconnected')
        return len(data)


class SaveDisconnectTests(unittest.TestCase):
    def handler(self, writer, operation):
        payload = json.dumps({'assertion_id': 'fixture', 'action': 'retract',
                              'effective_date': '2020-01-02'}).encode()
        handler = Handler.__new__(Handler)
        handler.path = '/api/staffer-assertions'
        handler.command, handler.request_version = 'POST', 'HTTP/1.1'
        handler.requestline = 'POST /api/staffer-assertions HTTP/1.1'
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        handler.headers['Origin'] = 'http://127.0.0.1:8765'
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(payload))
        handler.rfile, handler.wfile = io.BytesIO(payload), writer
        handler.server = SimpleNamespace(server_port=8765, server_address=('127.0.0.1', 8765),
            dashboard=SimpleNamespace(assertion_decision=operation))
        return handler

    def fixture(self, root):
        path = Path(root) / 'assertions.json'
        path.write_text(json.dumps({'coworker_assertions': [
            {'id': 'fixture', 'as_of': '2020-01-01', 'status': 'active',
             'source_person_ids': ['fixture-person'], 'assertion': 'Synthetic statement'}]}), encoding='utf-8')
        return path

    def test_committed_save_is_not_retried_after_header_or_body_disconnect(self):
        for fail_at in (1, 2):
            for error in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                with self.subTest(fail_at=fail_at, error=error), tempfile.TemporaryDirectory() as root:
                    path = self.fixture(root)
                    calls = []
                    def operation(payload):
                        calls.append(1)
                        return staffer_assertions.record_coworker_decision(
                            payload['assertion_id'], payload['action'], payload['effective_date'])
                    writer = Writer(fail_at, error)
                    with patch.object(identity, 'USER_ASSERTIONS', path):
                        self.handler(writer, operation).do_POST()
                    record = json.loads(path.read_text(encoding='utf-8'))['coworker_assertions'][0]
                    self.assertEqual(record['status'], 'retracted')
                    self.assertEqual(len(record['events']), 1)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(len(writer.writes), fail_at)
                    self.assertNotIn(b'503', b''.join(writer.writes))

    def test_storage_failure_remains_visible_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            before = path.read_bytes()
            writer = Writer()
            def operation(payload):
                return staffer_assertions.record_coworker_decision(
                    payload['assertion_id'], payload['action'], payload['effective_date'])
            with patch.object(identity, 'USER_ASSERTIONS', path), \
                 patch.object(staffer_assertions.os, 'replace', side_effect=OSError('fixture disk unavailable')):
                self.handler(writer, operation).do_POST()
            self.assertEqual(path.read_bytes(), before)
            self.assertIn(b'503', writer.writes[0])
            self.assertIn(b'Could not save', writer.writes[1])
            self.assertEqual(list(Path(root).iterdir()), [path])

    def test_success_acknowledges_only_the_completed_durable_save(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            writer = Writer()
            sequence = []
            real_fsync = staffer_assertions.os.fsync
            real_replace = staffer_assertions.os.replace
            def fsync(fd):
                real_fsync(fd)
                sequence.append('fsync')
            def replace(source, target):
                self.assertFalse(writer.writes)
                real_replace(source, target)
                sequence.append('replace')
            def operation(payload):
                return staffer_assertions.record_coworker_decision(
                    payload['assertion_id'], payload['action'], payload['effective_date'])
            with patch.object(identity, 'USER_ASSERTIONS', path), \
                 patch.object(staffer_assertions.os, 'fsync', fsync), \
                 patch.object(staffer_assertions.os, 'replace', replace):
                self.handler(writer, operation).do_POST()
            self.assertEqual(sequence, ['fsync', 'replace'])
            self.assertIn(b'202', writer.writes[0])
            self.assertEqual(json.loads(writer.writes[1])['action'], 'retract')


if __name__ == '__main__': unittest.main()
