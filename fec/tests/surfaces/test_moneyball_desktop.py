import json
import os
import sys
import types
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from fec.desktop import moneyball_app
from fec.desktop.moneyball_app import load_config, ensure_service


class DesktopAppTests(unittest.TestCase):
    def test_config_requires_supported_local_service_and_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / 'fec/surfaces/staffer_live_server.py'
            server.parent.mkdir(parents=True)
            server.touch()
            python = root / 'python.exe'
            python.touch()
            config = root / 'app.json'
            data = dict(source_root=str(root), service_python=str(python), port=8841,
                        dashboard_file='Moneyball_Redesign.html')
            config.write_text(json.dumps(data))
            self.assertEqual(load_config(config)['port'], 8841)
            for field, value in [('port', True), ('port', 80), ('dashboard_file', '../../other.html')]:
                config.write_text(json.dumps({**data, field: value}))
                with self.assertRaises(ValueError):
                    load_config(config)

    @patch('fec.desktop.moneyball_app.subprocess.run')
    @patch('fec.desktop.moneyball_app.health')
    @patch('fec.desktop.moneyball_app.socket.socket')
    def test_existing_listener_must_be_moneyball_and_is_never_replaced(self, socket, health, run):
        socket.return_value.__enter__.return_value.connect_ex.return_value = 0
        self.assertEqual(ensure_service({'port': 8841}, Path('.')), 'REUSED_EXISTING_SERVICE')
        run.assert_not_called()
        health.side_effect = ValueError('Wrong service')
        with self.assertRaises(ValueError):
            ensure_service({'port': 8841}, Path('.'))
        run.assert_not_called()

    def _run_smoke(self, receipt, service, js):
        """Drive main() with a fake webview; return (window, receipt_exists)."""
        window = MagicMock()
        window.evaluate_js.side_effect = js
        fake = types.ModuleType('webview')
        fake.create_window = MagicMock(return_value=window)
        fake.settings = {}
        fake.start = lambda fn, **kw: fn()
        menu = types.ModuleType('webview.menu')
        menu.Menu = menu.MenuAction = MagicMock()
        menu.MenuSeparator = MagicMock()
        with tempfile.TemporaryDirectory() as local,                 patch.dict(sys.modules, {'webview': fake, 'webview.menu': menu}),                 patch.dict(os.environ, {'LOCALAPPDATA': local}),                 patch.object(sys, 'argv', ['app', '--smoke-test', str(receipt)]),                 patch.object(moneyball_app, 'load_config', return_value={'port': 8841}),                 patch.object(moneyball_app, 'acquire_instance', return_value=object()),                 patch.object(moneyball_app, 'ensure_service', service),                 patch.object(moneyball_app.ctypes, 'windll', MagicMock(), create=True),                 patch.object(moneyball_app.logging, 'basicConfig'):
            moneyball_app.main()
        return window

    def test_smoke_failure_creates_receipt_dir_and_destroys_window(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / 'new' / 'smoke.json'
            window = self._run_smoke(receipt, MagicMock(side_effect=ValueError('port held')), lambda s: None)
            self.assertEqual(json.loads(receipt.read_text())['status'], 'FAIL')
            window.destroy.assert_called_once()

    def test_smoke_failure_survives_missing_message_element(self):
        def js(script):
            if 'message' in script:
                raise RuntimeError('TypeError: null')
            return None
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / 'smoke.json'
            window = self._run_smoke(receipt, MagicMock(side_effect=ValueError('boom')), js)
            self.assertEqual(json.loads(receipt.read_text())['error'], 'boom')
            window.destroy.assert_called_once()

    def test_smoke_null_dom_evidence_reports_fail_not_attributeerror(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / 'smoke.json'
            with patch.object(moneyball_app.time, 'sleep'),                     patch.object(moneyball_app.time, 'monotonic', side_effect=[0, 1, 1000]):
                window = self._run_smoke(receipt, MagicMock(return_value='SPAWNED'), lambda s: 'null')
            data = json.loads(receipt.read_text())
            self.assertEqual((data['status'], data['dom']), ('FAIL', {}))
            window.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
