import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

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

    @staticmethod
    def reparse(*marked):
        """Make these paths report the reparse-point attribute, as OneDrive placeholders and real junctions do."""
        real = Path.lstat

        def lstat(path, *args, **kwargs):
            info = real(path, *args, **kwargs)
            return types.SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if path in marked else info
        return mock.patch.object(Path, 'lstat', lstat)

    def test_a_reparse_point_above_the_install_root_does_not_hide_the_app(self):
        with self.reparse(*self.root.parents):      # a redirected Documents folder, or a OneDrive placeholder, somewhere above the root
            row = self.apps.handle('/desktop-apps', {})['applications'][0]
            self.assertEqual((row['installed'], row['path']), (True, str(self.exe)))
            self.assertTrue(self.apps.handle('/desktop-apps/open', {'appId': 'melange'})['ok'])

    def test_a_link_at_or_under_the_install_root_is_refused(self):
        for name, marked in (('the root', self.root), ('the app folder', self.exe.parent), ('the executable', self.exe)):
            with self.subTest(name), self.reparse(marked):
                self.assertFalse(self.apps.handle('/desktop-apps', {})['applications'][0]['installed'])
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
