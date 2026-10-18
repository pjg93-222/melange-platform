"""Container path seams (D-173..D-182): every Windows location a compute job uses keeps today's value by default, and each
environment variable moves what it names. Synthetic folders only; no job runs."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for part in (ROOT / 'forecast' / 'contracts', ROOT / 'forecast' / 'anchors', ROOT / 'app', ROOT / 'tools'):
    sys.path.insert(0, str(part))
import api_company_cycle_fanout as fanout  # noqa: E402
import build_giving_anchors as bga  # noqa: E402
import fec_key_store  # noqa: E402
import glassbox as anchors_glassbox  # noqa: E402
import mentat_env  # noqa: E402
import receipt_path as receipt_paths  # noqa: E402

WINDOWS_RSCRIPT = 'C:/Program Files/R/R-4.6.0/bin/Rscript.exe'
WINDOWS_RLIB = 'C:/Users/pgoff/AppData/Local/R/win-library/4.6'
_server = None


def server():
    global _server
    if _server is None:
        spec = importlib.util.spec_from_file_location('compute_seams_server', ROOT / 'app' / 'server.py')
        _server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_server)
    return _server


class Env:
    """Context manager: these variables removed, then the given ones set."""
    def __init__(self, remove=(), **setting):
        self.remove, self.setting = remove, setting

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in (*self.remove, *self.setting)}
        for name in self.remove:
            os.environ.pop(name, None)
        os.environ.update(self.setting)

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class SeamTableTests(unittest.TestCase):
    def test_the_windows_defaults_are_pinned(self):
        self.assertEqual({k: v[0] for k, v in mentat_env.SEAMS.items()},
                         {'MENTAT_RSCRIPT': WINDOWS_RSCRIPT, 'MENTAT_R_LIBRARY': WINDOWS_RLIB})

    def test_a_set_variable_moves_the_value_and_an_empty_one_does_not(self):
        for name, (default, _) in mentat_env.SEAMS.items():
            with Env(remove=(name,)):
                self.assertEqual(mentat_env.seam(name), default)
            with Env(**{name: '/container/value'}):
                self.assertEqual(mentat_env.seam(name), '/container/value')
            with Env(**{name: '  '}):
                self.assertEqual(mentat_env.seam(name), default)

    def test_every_copy_of_a_default_matches_the_table(self):
        """R/paths.R, the PowerShell launchers' test and the compose file carry the same two defaults inline."""
        paths_r = (ROOT / 'R' / 'paths.R').read_text(encoding='utf-8')
        ps_test = (ROOT / 'tests' / 'Test-MentatPathSeams.ps1').read_text(encoding='utf-8')
        self.assertIn('"MENTAT_R_LIBRARY"', paths_r)
        self.assertIn(WINDOWS_RLIB, paths_r)
        for name, (default, _) in mentat_env.SEAMS.items():
            self.assertIn(name, ps_test)
            self.assertIn(default.replace('/', '\\'), ps_test)
        for relative in ('tools/build_industry_report.ps1', 'tools/generate_crypto_report.ps1', 'tools/parallel/Start-MentatResilientIndustryReports.ps1'):
            text = (ROOT / relative).read_text(encoding='utf-8')
            self.assertIn('$env:MENTAT_RSCRIPT', text, relative)
            self.assertIn(mentat_env.SEAMS['MENTAT_RSCRIPT'][0].replace('/', '\\'), text, relative)


class ConsoleAndFanoutTests(unittest.TestCase):
    def console(self, **kwargs):
        app = server()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.object(app, 'report_directory', return_value=Path(temp.name)):
            return app.Console(Path(temp.name) / 'console', read_only=True, **kwargs)

    def test_the_console_rscript_is_the_windows_path_unless_the_variable_or_the_argument_says_otherwise(self):
        with Env(remove=('MENTAT_RSCRIPT',)):
            self.assertEqual(self.console().rscript, WINDOWS_RSCRIPT)
        with Env(MENTAT_RSCRIPT='/usr/bin/Rscript'):
            self.assertEqual(self.console().rscript, '/usr/bin/Rscript')
            self.assertEqual(self.console(rscript='/explicit/Rscript').rscript, '/explicit/Rscript')

    def test_the_fanout_rscript_default_is_the_windows_path_unless_the_variable_says_otherwise(self):
        argv = ['--source-database', 'db', '--industry', 'x', '--run-directory', 'run']
        with Env(remove=('MENTAT_RSCRIPT',)):
            self.assertEqual(str(fanout.parse_arguments(argv).rscript_path).replace('\\', '/'), WINDOWS_RSCRIPT)
            self.assertEqual(fanout.parse_arguments(argv).rscript_path, Path(r'C:\Program Files\R\R-4.6.0\bin\Rscript.exe'))
        with Env(MENTAT_RSCRIPT='/usr/bin/Rscript'):
            self.assertEqual(fanout.parse_arguments(argv).rscript_path, Path('/usr/bin/Rscript'))
            self.assertEqual(fanout.parse_arguments(argv + ['--rscript-path', '/x/Rscript']).rscript_path, Path('/x/Rscript'))

    def test_the_r_library_follows_r_libs_user_then_localappdata(self):
        app = server()
        with tempfile.TemporaryDirectory() as base:
            lib = Path(base) / 'R' / 'win-library' / '4.6'
            lib.mkdir(parents=True)
            other = Path(base) / 'elsewhere'
            other.mkdir()
            self.assertEqual(app.r_library_path({'LOCALAPPDATA': base}, base, 'C:/R/R-4.6.0/bin/Rscript.exe'), str(lib.resolve()))
            self.assertEqual(app.r_library_path({'LOCALAPPDATA': base, 'R_LIBS_USER': str(other)}, base, ''), str(other.resolve()))


class AnchorSeamTests(unittest.TestCase):
    def test_the_lda_csv_lives_under_localappdata_melange(self):
        with Env(LOCALAPPDATA='/localappdata'):
            self.assertEqual(bga.default_lda_csv(), Path('/localappdata/Melange/lda_rebuild/derived/sector_spend/sector_spend.csv'))

    def test_the_report_directory_and_glass_box_folder_follow_their_variables(self):
        with tempfile.TemporaryDirectory() as base:
            with Env(MENTAT_REPORT_DIRECTORY=base):
                self.assertEqual(bga.report_directory(base), Path(base).resolve())
            with Env(ANCHORS_GLASSBOX_DIR=base):
                self.assertEqual(anchors_glassbox.events_dir(), Path(base))
            with Env(remove=('ANCHORS_GLASSBOX_DIR',)):
                self.assertEqual(anchors_glassbox.events_dir(), Path.home() / '.claude' / 'glassbox' / 'events')

    def test_the_active_fec_source_pointer_is_read_as_a_windows_path_everywhere_but_windows(self):
        pointer = r'C:\Users\pgoff\Mentat\mentat\.cache\repair-20260908-inputs'
        with tempfile.TemporaryDirectory() as base:
            (Path(base) / 'inputs').mkdir()
            (Path(base) / 'inputs' / 'active_fec_sources.json').write_text(json.dumps({'path': pointer}), encoding='utf-8')
            self.assertEqual(bga.fec_root(base), Path(pointer))                          # Windows: exactly as before
            self.assertEqual(bga.fec_root(base, '/explicit'), Path('/explicit'))
            with patch.object(receipt_paths, 'os', SimpleNamespace(name='posix', environ={})):
                rebased = bga.fec_root(base)
            self.assertEqual(rebased.relative_to(receipt_paths.PROJECT).as_posix(), '.cache/repair-20260908-inputs')


class OtherSeamTests(unittest.TestCase):
    def test_the_melange_and_inputs_roots_follow_their_variables(self):
        import analysis_integrations as integrations
        with tempfile.TemporaryDirectory() as base:
            with Env(MENTAT_MELANGE_ROOT=base, MENTAT_INPUTS_DIR=base):
                self.assertEqual(integrations._roots({}), (Path(base).resolve(), Path(base).resolve()))
            with Env(remove=('MENTAT_MELANGE_ROOT', 'MENTAT_INPUTS_DIR')):
                self.assertEqual(integrations._roots({}), ((ROOT / 'inputs').resolve(), integrations.DEFAULT_MELANGE.resolve()))
        self.assertEqual(integrations.DEFAULT_MELANGE, Path.home() / 'OneDrive' / 'Desktop' / 'Melange Systems')

    def test_the_prewarm_glass_box_folder_default_is_the_windows_path_and_the_variable_moves_it(self):
        import glassbox_emit
        try:
            with Env(remove=('GLASSBOX_EVENTS_DIR',)):
                importlib.reload(glassbox_emit)
                self.assertEqual(str(glassbox_emit.EVENTS_DIR).replace('\\', '/'), 'C:/Users/pgoff/.claude/glassbox/events')
            with Env(GLASSBOX_EVENTS_DIR='/glassbox/events'):
                importlib.reload(glassbox_emit)
                self.assertEqual(glassbox_emit.EVENTS_DIR, Path('/glassbox/events'))
        finally:
            importlib.reload(glassbox_emit)

    def test_the_key_store_path_follows_localappdata_and_the_container_reads_a_secret_file(self):
        with Env(LOCALAPPDATA='/localappdata'):
            self.assertEqual(fec_key_store.key_path(), Path('/localappdata/Mentat/credentials/fec-keys.dpapi'))
        with tempfile.TemporaryDirectory() as base:
            secret = Path(base) / 'fec_api_keys'
            secret.write_text('# comment\nfake-key-one\nfake-key-two,fake-key-one\n', encoding='utf-8')
            with Env(FEC_API_KEYS_FILE=str(secret)):
                self.assertEqual(fec_key_store.load_keys(), ['fake-key-one', 'fake-key-two'])
                self.assertEqual(fec_key_store.load_keys(Path(base) / 'absent.dpapi'), [])      # an explicit store path is never overridden
            with Env(remove=('FEC_API_KEYS_FILE',), LOCALAPPDATA=base):
                self.assertEqual(fec_key_store.load_keys(), [])                                  # no file, no store: nothing, as before


class LineEndingTests(unittest.TestCase):
    def test_the_pocket_page_is_written_with_crlf_on_every_platform(self):
        with tempfile.TemporaryDirectory() as base:
            data, page = Path(base) / 'pocket.json', Path(base) / 'out' / 'mentat-pocket.html'
            data.write_text('{"a": [1, 2], "note": "two\\nlines"}', encoding='utf-8')
            done = subprocess.run([sys.executable, str(ROOT / 'tools' / 'pocket' / 'build_page.py'), '--data', str(data), '--out', str(page)],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            raw = page.read_bytes()
            self.assertGreater(raw.count(b'\r\n'), 100)
            self.assertEqual(raw.count(b'\n'), raw.count(b'\r\n'), 'a bare LF')
            self.assertEqual(raw.count(b'\r'), raw.count(b'\r\n'), 'a bare CR')


@unittest.skipUnless(os.name == 'nt' and shutil.which('powershell.exe'), 'Windows PowerShell 5.1 launchers')
class LauncherTests(unittest.TestCase):
    def test_the_powershell_launcher_defaults(self):
        done = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tests' / 'Test-MentatPathSeams.ps1')],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('PASS: 15 launcher defaults', done.stdout)


if __name__ == '__main__':
    unittest.main()
