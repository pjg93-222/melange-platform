from pathlib import Path
import ast
import json
import subprocess
import sys
import tempfile
import unittest

from build_runtime import HERE, BOOTSTRAP_FILES, POCKET_TEMPLATE, stage_file, stage_bootstrap


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
