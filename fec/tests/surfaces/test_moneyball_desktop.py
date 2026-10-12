import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

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


if __name__ == '__main__':
    unittest.main()
