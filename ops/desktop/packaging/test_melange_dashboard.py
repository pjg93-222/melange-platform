import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from desktop_backend import configure_melange_dashboard


class MelangeDashboardSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='melange-dashboard-selection-')
        self.addCleanup(self.temp.cleanup)
        # Resolved, as configure_melange_dashboard resolves: a TEMP given as an 8.3 short path (GitHub's Windows
        # runners use C:\Users\RUNNER~1\...) must not make the long and short spellings of one directory compare unequal.
        self.base = Path(self.temp.name).resolve()
        self.data = self.base / 'data'
        self.state = self.base / 'profile'
        self.data.mkdir()
        self.state.mkdir()
        self.module = SimpleNamespace(ASSETS=self.base / 'installed-assets')

    def select(self, name='Moneyball_Redesign.html', **changes):
        config = {'schemaVersion': 1, 'dataRoot': str(self.data), 'dashboardFile': name}
        config.update(changes)
        (self.state / 'dashboard-selection.json').write_text(json.dumps(config), encoding='utf-8')

    def interface(self):
        page = self.data / 'Moneyball_Redesign.html'
        page.write_text('<!doctype html><!-- selected existing workspace -->', encoding='utf-8')
        assets = self.data / 'fec' / 'surfaces' / 'staffer_live' / 'approved'
        assets.mkdir(parents=True)
        for name in ('mlk-boot.js', 'app.css', 'ui-kit.css', 'forecasts.css', 'ui-kit.js', 'app.js', 'research.js', 'workspace.js'):
            (assets / name).write_text('/* existing local fixture */', encoding='utf-8')
        return page, assets

    def test_fresh_default_keeps_bundled_asset_path(self):
        self.assertEqual(configure_melange_dashboard(self.module, self.data, self.state), 'Moneyball.html')
        self.assertEqual(self.module.ASSETS, self.base / 'installed-assets')
        self.assertEqual(list(self.state.iterdir()), [])

    def test_existing_selection_keeps_all_workspace_bytes_unchanged(self):
        page, assets = self.interface()
        self.select()
        before = {path: path.read_bytes() for path in self.base.rglob('*') if path.is_file()}
        self.assertEqual(configure_melange_dashboard(self.module, self.data, self.state), page.name)
        self.assertEqual(self.module.ASSETS, assets.parent)
        self.assertEqual({path: path.read_bytes() for path in self.base.rglob('*') if path.is_file()}, before)

    def test_wrong_root_and_path_names_are_rejected(self):
        self.interface()
        for name in ('../Moneyball_Redesign.html', r'..\Moneyball_Redesign.html', str(self.data / 'Moneyball_Redesign.html'), 'other.html'):
            with self.subTest(name=name):
                self.select(name)
                with self.assertRaises(ValueError):
                    configure_melange_dashboard(self.module, self.data, self.state)
        self.select(dataRoot=str(self.base / 'different'))
        with self.assertRaises(ValueError):
            configure_melange_dashboard(self.module, self.data, self.state)

    def test_explicit_missing_dashboard_or_asset_fails_without_fallback(self):
        self.select()
        with self.assertRaises(ValueError):
            configure_melange_dashboard(self.module, self.data, self.state)
        page, assets = self.interface()
        (assets / 'app.js').unlink()
        with self.assertRaises(ValueError):
            configure_melange_dashboard(self.module, self.data, self.state)
        self.assertTrue(page.is_file())
        self.assertEqual(self.module.ASSETS, self.base / 'installed-assets')

    def test_selection_schema_is_bounded_and_strict(self):
        for config in ([], {'schemaVersion': True}, {'schemaVersion': 2}):
            (self.state / 'dashboard-selection.json').write_text(json.dumps(config), encoding='utf-8')
            with self.assertRaises(ValueError):
                configure_melange_dashboard(self.module, self.data, self.state)
        (self.state / 'dashboard-selection.json').write_text(' ' * 4097, encoding='utf-8')
        with self.assertRaises(ValueError):
            configure_melange_dashboard(self.module, self.data, self.state)


if __name__ == '__main__':
    unittest.main()
