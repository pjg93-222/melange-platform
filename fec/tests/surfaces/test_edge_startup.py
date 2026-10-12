import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('edge_startup', Path(__file__).parents[2] / 'desktop/launch_moneyball_edge.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        script = self.root / 'fec/surfaces/staffer_live_server.py'
        script.parent.mkdir(parents=True)
        script.write_text('')
        (self.root / 'Moneyball_Redesign.html').write_text('dashboard')
        (self.root / 'python.exe').write_text('')
        self.config = {'source_root': str(self.root), 'port': 8841,
                       'service_python': str(self.root / 'python.exe')}

    def test_healthy_service_reused_without_launch(self):
        with patch.object(app.socket, 'socket') as sock, patch.object(app, 'health') as health, patch.object(app.subprocess, 'Popen') as launch:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            health.return_value = {'ready': True}
            self.assertEqual(app.ensure_service(self.config), 'REUSED_EXISTING_SERVICE')
            launch.assert_not_called()

    def test_unknown_listener_not_replaced(self):
        with patch.object(app.socket, 'socket') as sock, patch.object(app, 'health', side_effect=ValueError('wrong service')), patch.object(app.subprocess, 'Popen') as launch:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(ValueError, 'wrong service'):
                app.ensure_service(self.config)
            launch.assert_not_called()

    def test_cold_start_uses_python_and_opens_during_indexing(self):
        process = MagicMock(pid=123)
        process.poll.return_value = None
        with patch.object(app.socket, 'socket') as sock, patch.object(app, 'health', return_value={'ready': False}), patch.object(app.subprocess, 'Popen', return_value=process) as launch:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            self.assertEqual(app.ensure_service(self.config), 'STARTED_LOCAL_SERVICE')
            command = launch.call_args.args[0]
            self.assertEqual(Path(command[0]).name, 'python.exe')
            self.assertEqual(Path(command[1]).name, 'staffer_live_server.py')
            self.assertNotIn('powershell.exe', command)
            self.assertTrue((self.root / 'audits/staffer_live_runtime/server.json').is_file())

    def test_crashed_child_reports_failure_without_waiting(self):
        process = MagicMock(pid=123)
        process.poll.return_value = 1
        with patch.object(app.socket, 'socket') as sock, patch.object(app.subprocess, 'Popen', return_value=process), patch.object(app.time, 'sleep') as sleep:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 1
            with self.assertRaisesRegex(RuntimeError, 'exited during startup'):
                app.ensure_service(self.config)
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
