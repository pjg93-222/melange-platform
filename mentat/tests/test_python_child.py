"""python_child: a Python child that is one process, in this interpreter's environment (lane V, 2026-09-26).

Under a Windows venv, Scripts/python.exe is CPython's venv launcher, a second process in front of the
interpreter. python_child.command() starts the base interpreter with __PYVENV_LAUNCHER__ instead, as
multiprocessing does (bpo-35797). Pinned here under whichever Python runs the suite (Mentat's venv, the
system Python, the Codex runtime):

  One process        Popen.pid is the child's own os.getpid().
  Same environment   the child's sys.executable and sys.prefix are this process's, and jinja2 and openpyxl
                     load from the same files (or fail to import alike).
  No leak            the child's os.environ has no __PYVENV_LAUNCHER__.
  Negative control   under a venv, the same base interpreter WITHOUT the variable is not the venv: the
                     variable is what carries it, so the pins above cannot pass by accident.
Outside a venv command() is (sys.executable, env) unchanged. Children write to real files and are waited
on with a timeout (brain DE-026).
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
import python_child  # noqa: E402

IN_VENV = sys.prefix != sys.base_prefix
REPORT = r'''
import json, os, sys
modules = {}
for name in ('jinja2', 'openpyxl'):
    try:
        modules[name] = __import__(name).__file__
    except ImportError as error:
        modules[name] = 'ImportError'
print(json.dumps({'pid': os.getpid(), 'executable': sys.executable, 'prefix': sys.prefix,
                  'launcher_variable': os.environ.get('__PYVENV_LAUNCHER__'), 'modules': modules}))
'''


def here_modules():
    found = {}
    for name in ('jinja2', 'openpyxl'):
        try:
            found[name] = __import__(name).__file__
        except ImportError:
            found[name] = 'ImportError'
    return found


def report(arguments, env=None):
    """(Popen pid, the child's JSON report)."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        child = subprocess.Popen(arguments, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            code = child.wait(timeout=120)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=30)
            raise
        out.seek(0)
        err.seek(0)
        text, problem = out.read().decode('utf-8', 'replace'), err.read().decode('utf-8', 'replace')
    if code != 0:
        raise AssertionError(f'the child exited {code}: {problem[-2000:]}')
    return child.pid, json.loads(text.strip().splitlines()[-1])


def same(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


class PythonChildTests(unittest.TestCase):
    def test_the_child_is_one_process_in_this_interpreters_environment(self):
        executable, env = python_child.command()
        pid, seen = report([executable, '-c', REPORT], env)
        self.assertEqual(seen['pid'], pid, 'Popen.pid must be the interpreter itself')
        self.assertTrue(same(seen['executable'], sys.executable), seen['executable'])
        self.assertTrue(same(seen['prefix'], sys.prefix), seen['prefix'])
        self.assertEqual({name: path if path == 'ImportError' else os.path.normcase(path)
                          for name, path in seen['modules'].items()},
                         {name: path if path == 'ImportError' else os.path.normcase(path)
                          for name, path in here_modules().items()})
        self.assertIsNone(seen['launcher_variable'], 'the interpreter clears the variable at start-up')

    def test_argv_rewrites_only_this_interpreter(self):
        arguments, env = python_child.argv([sys.executable, '-c', 'pass'], {'KEEP': '1'})
        executable, expected = python_child.command({'KEEP': '1'})
        self.assertEqual(arguments, [executable, '-c', 'pass'])
        self.assertEqual(env, expected)
        self.assertEqual(env['KEEP'], '1')
        other = ['powershell.exe', '-NoProfile']
        self.assertEqual(python_child.argv(other, None), (other, None))

    def test_outside_a_venv_nothing_changes(self):
        if IN_VENV and os.name == 'nt':
            executable, env = python_child.command()
            self.assertTrue(same(executable, sys._base_executable))
            self.assertEqual(env[python_child.LAUNCHER_VARIABLE], sys.executable)
            self.assertNotIn(python_child.LAUNCHER_VARIABLE, os.environ, 'the caller environment is not changed')
        else:
            self.assertEqual(python_child.command(), (sys.executable, None))
            self.assertEqual(python_child.command({'A': '1'}), (sys.executable, {'A': '1'}))

    @unittest.skipUnless(IN_VENV and os.name == 'nt', 'a Windows venv')
    def test_negative_control_the_base_without_the_variable_is_not_the_venv(self):
        env = {key: value for key, value in os.environ.items() if key != python_child.LAUNCHER_VARIABLE}
        pid, seen = report([sys._base_executable, '-c', REPORT], env)
        self.assertEqual(seen['pid'], pid)
        self.assertTrue(same(seen['prefix'], sys.base_prefix), seen['prefix'])
        self.assertFalse(same(seen['prefix'], sys.prefix), 'without the variable the child must leave the venv')


if __name__ == '__main__':
    unittest.main()
