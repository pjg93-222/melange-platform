import json
from pathlib import Path
import tempfile
import unittest
from desktop_audit import DesktopAudit
from desktop_backend import GuardedWSGI


class AuditTests(unittest.TestCase):
    def test_write_events_are_private_and_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = DesktopAudit(Path(temporary), 'terrier', 'co-05')
            calls = []
            def application(environment, start):
                calls.append(True)
                start('201 Created', [])
                return [b'ok']
            guarded = GuardedWSGI(application, 'secret', 'terrier', audit)
            environment = {'HTTP_X_DESKTOP_TOKEN': 'secret', 'REQUEST_METHOD': 'POST',
                           'PATH_INFO': '/private/person@example.com', 'QUERY_STRING': 'secret=not-recorded'}
            self.assertEqual(list(guarded(environment, lambda *args: None)), [b'ok'])
            self.assertEqual(len(calls), 1)
            records = [json.loads(line) for line in audit.file.read_text().splitlines()]
            self.assertEqual([event['phase'] for event in records], ['attempt', 'completed'])
            self.assertEqual(records[-1]['status'], 201)
            self.assertNotIn('person@example.com', audit.file.read_text())
            self.assertNotIn('not-recorded', audit.file.read_text())
            DesktopAudit(Path(temporary), 'terrier', 'co-05')
            audit.file.write_text(audit.file.read_text().replace('201', '200'))
            with self.assertRaisesRegex(RuntimeError, 'integrity'):
                DesktopAudit(Path(temporary), 'terrier', 'co-05')


if __name__ == '__main__':
    unittest.main()
