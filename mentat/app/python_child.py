"""Start a Python child as ONE process, inside Mentat's venv or not.

On Windows a venv's Scripts/python.exe is CPython's venv launcher: a small separate process that starts
the base interpreter named in pyvenv.cfg with __PYVENV_LAUNCHER__ naming itself, then waits for it. A
child started through it is two processes. Popen.pid, Popen's handle and anything set through that
handle (priority, EcoQoS) then belong to the launcher, while the interpreter's own os.getpid() is what
lands in locks, markers and /api/health; and the files the child inherited stay open until the launcher
has noticed the exit.

multiprocessing avoids the launcher the same way (bpo-35797, Lib/multiprocessing/popen_spawn_win32.py):
start sys._base_executable with __PYVENV_LAUNCHER__ set to sys.executable. The interpreter reads the
venv's pyvenv.cfg exactly as it would through the launcher, so its sys.executable, sys.prefix and
site-packages are the venv's, and it removes the variable at start-up, so its own children do not
inherit it. Outside a Windows venv (the system Python, the Codex runtime) nothing changes.
tools/mentat_python.psm1 (Get-MentatPythonLaunch) applies the same rule to the console the launcher
starts.
"""
import os
import sys

LAUNCHER_VARIABLE = '__PYVENV_LAUNCHER__'


def _same_file(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def command(env=None):
    """(executable, env) for subprocess.Popen that start this interpreter's environment as one process.

    `env` is what the caller would hand Popen (None: inherit this process's environment). Under a
    Windows venv the answer is sys._base_executable and a copy of that environment with
    __PYVENV_LAUNCHER__ = sys.executable; anywhere else it is (sys.executable, env) unchanged.
    """
    base = getattr(sys, '_base_executable', '') or ''
    if (os.name != 'nt' or not sys.executable or not base or _same_file(base, sys.executable)
            or not os.path.isfile(base)):
        return sys.executable, env
    environment = dict(os.environ if env is None else env)
    environment[LAUNCHER_VARIABLE] = sys.executable
    return base, environment


def argv(arguments, env=None):
    """(arguments, env) for Popen: a leading sys.executable becomes command()'s executable and env.

    Any other command line is returned as given, with env unchanged.
    """
    arguments = list(arguments)
    if arguments and isinstance(arguments[0], str) and arguments[0] == sys.executable:
        arguments[0], env = command(env)
    return arguments, env
