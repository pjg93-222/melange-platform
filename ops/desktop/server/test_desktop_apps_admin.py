import os
from pathlib import Path
import tempfile
import unittest

from desktop_admin import Refused
from desktop_apps_admin import DesktopApps, DesktopUnavailable


class DesktopAppsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'installed'
        self.calls = []
        self.apps = DesktopApps(self.root, launch=lambda *args, **kwargs: self.calls.append((args, kwargs)), version=lambda _: '1.0.16')
        self.exe = self.root / 'Melange' / 'Melange.exe'
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b'fixture executable; never launched')

    def test_lists_actual_desktop_installations_without_docker_or_execution(self):
        rows = self.apps.handle('/desktop-apps', {})['applications']
        self.assertEqual([(r['appId'], r['installed']) for r in rows], [('melange', True), ('mentat', False), ('terrier', False)])
        self.assertEqual((rows[0]['version'], rows[0]['path']), ('1.0.16', str(self.exe)))
        self.assertEqual(self.calls, [])

    def test_launches_only_the_fixed_executable_without_shell_or_arguments(self):
        result = self.apps.handle('/desktop-apps/open', {'appId': 'melange'})
        self.assertTrue(result['ok'])
        args, kwargs = self.calls[0]
        self.assertEqual(args, ([str(self.exe)],))
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['cwd'], str(self.exe.parent))

    def test_arbitrary_paths_arguments_and_missing_install_are_refused(self):
        for value in ({'appId': '../other'}, {'appId': 'melange.exe'}, {'appId': []}, {'appId': 'melange', 'path': 'other.exe'}, {'appId': 'melange', 'args': ['--test']}):
            with self.assertRaises((ValueError, Refused)):
                self.apps.handle('/desktop-apps/open', value)
        with self.assertRaises(DesktopUnavailable):
            self.apps.handle('/desktop-apps/open', {'appId': 'mentat'})
        self.assertEqual(self.calls, [])

    def test_hard_link_is_not_treated_as_an_installed_app(self):
        os.link(self.exe, self.root / 'another.exe')
        with self.assertRaises(DesktopUnavailable):
            self.apps.handle('/desktop-apps/open', {'appId': 'melange'})
        self.assertEqual(self.calls, [])

    def test_launch_failure_is_reported_without_claiming_success(self):
        def fail(*args, **kwargs):
            raise PermissionError('blocked')
        self.apps.launch = fail
        with self.assertRaises(DesktopUnavailable):
            self.apps.handle('/desktop-apps/open', {'appId': 'melange'})


if __name__ == '__main__':
    unittest.main()
