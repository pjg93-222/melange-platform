from pathlib import Path
import ast
import json
import subprocess
import sys
import tempfile
import unittest

from build_runtime import HERE, BOOTSTRAP_FILES, POCKET_TEMPLATE, selected_files, stage_file, stage_bootstrap


class TemplateStagingTests(unittest.TestCase):
    def test_packaged_district_helper_imports_without_source_or_host_site_packages(self):
        with tempfile.TemporaryDirectory() as folder:
            staged = Path(folder) / 'bootstrap'
            stage_bootstrap(staged)
            self.assertTrue((staged / 'terrier_district.py').is_file())
            script = ('import json,sys; from pathlib import Path; '
                      'sys.path.insert(0,sys.argv[1]); import terrier_district; '
                      'print(json.dumps({"file":str(Path(terrier_district.__file__).resolve()),'
                      '"callable":callable(terrier_district.validate_district)}))')
            result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(staged)],
                                    cwd=folder, capture_output=True, text=True, timeout=10, check=True)
            self.assertEqual(json.loads(result.stdout), {'file': str((staged / 'terrier_district.py').resolve()), 'callable': True})

    def test_all_local_backend_helper_imports_are_in_explicit_bootstrap(self):
        tree = ast.parse((HERE / 'desktop_backend.py').read_text(encoding='utf-8'))
        needed = {node.module + '.py' for node in ast.walk(tree)
                  if isinstance(node, ast.ImportFrom) and node.module
                  and (HERE / (node.module + '.py')).is_file()}
        self.assertTrue(needed.issubset(BOOTSTRAP_FILES), needed - set(BOOTSTRAP_FILES))

    def test_mentat_runtime_served_assets_outside_data_extensions_are_staged(self):
        # /template.csv (server.py STATIC_ASSETS) and the Pocket publisher's manifest.webmanifest
        # (server.py publish()) have no code extension, so only sourceFiles can keep them.
        spec = json.loads((HERE / 'runtime-manifest.json').read_text(encoding='utf-8'))['apps']['mentat']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in spec['sourceDirectories']:
                (root / relative).mkdir(parents=True, exist_ok=True)
            for relative in spec['sourceFiles']:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text('x', encoding='utf-8')
            staged = {path.as_posix() for path in selected_files(root, spec)}
        self.assertIn('mentat/app/template.csv', staged)
        self.assertIn('mentat/tools/pocket/manifest.webmanifest', staged)

    def test_terrier_ocr_assets_licences_and_parcel_evidence_are_staged(self):
        # These immutable assets were already required by the Terrier release gate.
        spec = json.loads((HERE / 'runtime-manifest.json').read_text(encoding='utf-8'))['apps']['terrier']
        ocr = 'frontend/dist/ocr/tesseract-6.0.1/'
        required = {ocr + name for name in ('eng.traineddata.gz', 'asset-manifest.json', 'LICENSE-tessdata.txt',
                                           'LICENSE-tesseract-core.txt', 'LICENSE-tesseract.js.txt', 'NOTICE.txt')}
        required.add('server/research/location_evidence/southington-municipal-parcels-2026-10-17.json')
        required.add('tools/compute_volatile.json')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in spec['sourceDirectories']:
                (root / relative).mkdir(parents=True, exist_ok=True)
            for relative in spec['sourceFiles']:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text('x', encoding='utf-8')
            staged = {path.as_posix() for path in selected_files(root, spec)}
        self.assertLessEqual(required, staged)

    def test_runtime_state_and_databases_are_never_staged(self):
        # Mentat's backend writes state/prewarm/access.sqlite (+ -wal/-shm) at run time; none may ship.
        spec = json.loads((HERE / 'runtime-manifest.json').read_text(encoding='utf-8'))['apps']['mentat']
        self.assertIn('mentat/state', spec['excludedSourcePrefixes'])
        planted = ['mentat/state/x.sqlite', 'mentat/state/prewarm/access.sqlite', 'mentat/state/prewarm/access.sqlite-wal',
                   'mentat/state/prewarm/access.sqlite-shm', 'mentat/state/prewarm/warm.py',
                   'mentat/app/cache/run.sqlite3', 'mentat/app/cache/run.sqlite3-wal', 'mentat/app/Data.SQLITE',
                   'mentat/app/cache/run.db', 'mentat/app/cache/run.DB-wal', 'mentat/app/cache/run.db-shm', 'mentat/app/cache/run.db-journal']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in planted + ['mentat/app/server.py']:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text('x', encoding='utf-8')
            # Even a source directory that spans the whole app tree cannot pick them up.
            for directories in (['mentat/app'], ['mentat']):
                staged = {path.as_posix() for path in selected_files(root, {**spec, 'sourceDirectories': directories, 'sourceFiles': []})}
                self.assertEqual(staged, {'mentat/app/server.py'}, directories)
            with self.assertRaisesRegex(RuntimeError, 'Runtime database'):
                selected_files(root, {**spec, 'sourceDirectories': ['mentat/app'], 'sourceFiles': ['mentat/state/x.sqlite']})

    def test_pocket_template_retains_code_and_removes_embedded_data(self):
        with tempfile.TemporaryDirectory() as folder:
            source, destination = Path(folder) / 'source.html', Path(folder) / 'output.html'
            original = 'before /*__DATA_START__*/{"private":"donor"}/*__DATA_END__*/ after'
            source.write_text(original, encoding='utf-8')
            stage_file(source, destination, Path(POCKET_TEMPLATE))
            self.assertEqual(destination.read_text(encoding='utf-8'), 'before /*__DATA_START__*/{}/*__DATA_END__*/ after')
            self.assertEqual(source.read_text(encoding='utf-8'), original)
            source.write_text(original + original, encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
                stage_file(source, destination, Path(POCKET_TEMPLATE))


if __name__ == '__main__':
    unittest.main()
