"""Native Windows argv parsing with synthetic identities only; never controls a process."""
import ast
import ctypes
import datetime as _dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(os.environ.get('REPIN_IDENTITY_SOURCE', str(ROOT / 'fec/surfaces/repin_staffer_live.py')))
tree = ast.parse(SOURCE.read_bytes())
names = {'RepinHalt', 'lookup_process_identity', '_windows_command_argv',
         'confirm_pid_is_sidecar', 'restart_sidecar', '_rollback'}
nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
assert len(nodes) == len(names)
ns = dict(ctypes=ctypes, _dt=_dt, json=json, os=os, Path=Path, shutil=shutil,
          subprocess=subprocess, Any=object, Callable=object,
          SERVER_JSON_RELATIVE=Path('fixture-server.json'))
exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])), str(SOURCE), 'exec'), ns)


@unittest.skipUnless(os.name == 'nt', 'Windows native command-line parser')
class ProcessIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='repin identity ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project with spaces'
        self.script = self.root / 'fec/surfaces/staffer_live_server.py'
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes(b'# synthetic\n')
        self.python = Path(self.tmp.name) / 'python executable.exe'
        self.python.write_bytes(b'synthetic fixture, never execute')
        self.argv = [str(self.python), str(self.script), '--source-root', str(self.root),
                     '--port', '8841', '--dashboard-file', 'Moneyball_Redesign.html']
        self.identity = dict(pid=1234, executable=str(self.python),
                             command_line=subprocess.list2cmdline(self.argv),
                             created_at='2026-10-15T00:00:00.1234567Z')

    def confirm(self, identity=None, lookup=None):
        return ns['confirm_pid_is_sidecar'](1234, lookup=lookup or (lambda _: dict(identity or self.identity)),
                                           source_root=self.root, port=8841, expected_python=self.python)

    def altered(self, argv):
        return dict(self.identity, command_line=subprocess.list2cmdline(argv))

    def test_exact_launcher_quoted_paths_and_both_dashboards(self):
        self.assertEqual(ns['_windows_command_argv'](self.identity['command_line']), self.argv)
        for dashboard in ('Moneyball.html', 'Moneyball_Redesign.html'):
            argv = self.argv[:]; argv[-1] = dashboard
            self.assertEqual(self.confirm(self.altered(argv)), subprocess.list2cmdline(argv))

    def test_native_parser_literal_quotes_are_not_split_on_spaces(self):
        self.assertEqual(ns['_windows_command_argv']('"C:\\Program Files\\Python\\python.exe" "C:\\Project With Spaces\\server.py" --port 8841'),
                         ['C:\\Program Files\\Python\\python.exe', 'C:\\Project With Spaces\\server.py', '--port', '8841'])
        for text in ('', 'x\x00ignored', 'x' * 32769):
            with self.subTest(text_length=len(text)), self.assertRaises(ValueError):
                ns['_windows_command_argv'](text)

    def test_marker_in_unrelated_argument_refused(self):
        decoy = self.root / 'unrelated.py'; decoy.write_bytes(b'# decoy')
        argv = self.argv[:]; argv[1] = str(decoy)
        argv += ['--note', 'staffer_live_server.py']
        with self.assertRaises(ns['RepinHalt']): self.confirm(self.altered(argv))

    def test_unknown_duplicate_reordered_or_missing_arguments_refused(self):
        variants = [self.argv + ['--debug'], self.argv + ['--port', '8841'], self.argv[:-2],
                    self.argv[:2] + self.argv[4:6] + self.argv[2:4] + self.argv[6:]]
        for argv in variants:
            with self.subTest(argv_length=len(argv)), self.assertRaises(ns['RepinHalt']):
                self.confirm(self.altered(argv))

    def test_wrong_executable_script_root_port_or_dashboard_refused(self):
        other = self.root / 'other'; other.mkdir()
        file = other / 'python.exe'; file.write_bytes(b'not executed')
        variants = [dict(self.identity, executable=str(file))]
        for index, value in [(0, str(file)), (1, str(file)), (3, str(other)), (5, '8842'), (7, '../Moneyball.html')]:
            argv = self.argv[:]; argv[index] = value; variants.append(self.altered(argv))
        for identity in variants:
            with self.subTest(field=identity['executable']), self.assertRaises(ns['RepinHalt']): self.confirm(identity)

    def test_malformed_identity_or_missing_creation_refused_without_sensitive_details(self):
        variants = [None, {}, dict(self.identity, pid=1235), dict(self.identity, created_at='bad'),
                    dict(self.identity, created_at='2026-10-15T00:00:00'), dict(self.identity, extra='value')]
        for identity in variants:
            with self.subTest(identity_type=type(identity).__name__), self.assertRaises(ns['RepinHalt']) as caught:
                self.confirm(lookup=lambda _: identity)
            self.assertEqual(caught.exception.detail, {'pid': 1234})
            self.assertNotIn('command_line', str(caught.exception))

    def test_detected_pid_reuse_between_identity_reads_refused(self):
        records = iter([self.identity, dict(self.identity, created_at='2026-10-15T00:00:01Z')])
        with self.assertRaises(ns['RepinHalt']): self.confirm(lookup=lambda _: next(records))

    def test_default_executable_resolver_matches_launcher_python(self):
        with patch.object(shutil, 'which', return_value=str(self.python)) as resolve:
            self.assertEqual(ns['confirm_pid_is_sidecar'](1234, lookup=lambda _: self.identity,
                             source_root=self.root, port=8841), self.identity['command_line'])
        resolve.assert_called_once_with('python')

    def test_cim_lookup_errors_are_refusals_and_pid_is_not_interpolatable(self):
        for error in (OSError('fixture'), subprocess.TimeoutExpired('fixture', 30)):
            with patch.object(subprocess, 'run', side_effect=error):
                self.assertIsNone(ns['lookup_process_identity'](1234))
        with patch.object(subprocess, 'run') as run:
            self.assertIsNone(ns['lookup_process_identity']('1234; anything'))
            run.assert_not_called()
        with patch.object(subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=json.dumps(self.identity))) as run:
            self.assertEqual(ns['lookup_process_identity'](1234), self.identity)
            self.assertIn('ExecutablePath', run.call_args.args[0][-1])
            self.assertIn('CreationDate', run.call_args.args[0][-1])

    def test_forward_and_rollback_callers_pass_exact_root_and_port(self):
        calls = []
        def refusal(pid, **kwargs):
            calls.append((pid, kwargs['source_root'], kwargs['port']))
            raise ns['RepinHalt']('synthetic refusal', exit_code=3)
        def forbidden(*args, **kwargs): raise AssertionError('process control must not happen')
        original = dict(ns)
        ns.update(confirm_pid_is_sidecar=refusal, port_in_use=lambda _: True, read_pid=lambda _: 1234,
                  kill_pid=forbidden, wait_port_free=forbidden, restore_backup=lambda *a: None,
                  start_sidecar=lambda *a: types.SimpleNamespace(returncode=0), read_new_pid=lambda *a: None)
        try:
            with self.assertRaises(ns['RepinHalt']): ns['restart_sidecar'](self.root, 8841, lookup=lambda _: self.identity)
            ns['_rollback'](self.root, Path('fixture'), Path('fixture-backup'), types.SimpleNamespace(port=8841), 1234)
        finally:
            ns.clear(); ns.update(original)
        self.assertEqual(calls, [(1234, self.root, 8841), (1234, self.root, 8841)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
