from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from access_store import AccessStore
from desktop_admin import Store, Refused
from windows_admin_notifier import Tray, InstanceMutex, assert_protected_state


class WindowsNotifierTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'state'
        publisher=Store(self.root);publisher.init();AccessStore(publisher).initialize()

    def test_current_user_system_acl_required_without_repair(self):
        self.assertEqual(assert_protected_state(self.root),self.root.resolve())
        subprocess.run(['icacls',str(self.root/'access.sqlite3'),'/grant','*S-1-1-0:R'],check=True,capture_output=True)
        before=(self.root/'access.sqlite3').read_bytes()
        with self.assertRaises(Refused):assert_protected_state(self.root)
        self.assertEqual((self.root/'access.sqlite3').read_bytes(),before)

    def test_duplicate_native_notifier_does_not_take_ownership(self):
        first=InstanceMutex(self.root.resolve())
        try:
            self.assertTrue(first.acquired)
            self.assertFalse(Tray(self.root,1).run())
        finally:first.close()

    def test_click_launches_fixed_hidden_python_dashboard_no_request_arguments(self):
        tray=Tray(self.root,1)
        with patch('windows_admin_notifier.subprocess.Popen') as launch:
            tray.launch_dashboard();tray.launch_dashboard()
        launch.assert_called_once()
        args=launch.call_args.args[0]
        self.assertEqual(Path(args[0]).name,'pythonw.exe')
        self.assertEqual(Path(args[1]).name,'desktop_admin.py')
        self.assertEqual(args[2:],['--state',str(self.root.resolve()),'access-dashboard'])
        self.assertEqual(launch.call_args.kwargs['creationflags'],subprocess.CREATE_NO_WINDOW)
