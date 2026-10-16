"""Copy an isolated Windows CPython environment into a private application runtime.

The interpreter, standard library, native extensions and package license files
stay together. This is a development build step, not a redistribution approval.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def bundle_python(destination):
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('This native builder requires Windows CPython 3.12')
    base, environment = Path(sys.base_prefix), Path(sys.prefix)
    configuration = environment / 'pyvenv.cfg'
    if base == environment or not configuration.is_file() or 'include-system-site-packages = false' not in configuration.read_text(encoding='utf-8').lower():
        raise RuntimeError('Use a dedicated virtual environment without global site packages')
    destination = Path(destination)
    destination.mkdir(parents=True)
    for name in ('python.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        source = base / name
        if not source.is_file():
            raise RuntimeError('Required CPython distribution file missing: ' + name)
        shutil.copy2(source, destination / name)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'test', 'tests', 'idlelib', 'ensurepip', 'site-packages')
    shutil.copytree(base / 'Lib', destination / 'Lib', ignore=ignore)
    shutil.copytree(base / 'DLLs', destination / 'DLLs', ignore=shutil.ignore_patterns('_test*', '*.pdb'))
    shutil.copytree(environment / 'Lib' / 'site-packages', destination / 'Lib' / 'site-packages',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests', 'test'))
    # Launchers use -E -s, and child jobs inherit PYTHONNOUSERSITE=1. This flag is
    # defense in depth; sitecustomize alone does not remove an already-added path.
    # Do not use an embeddable ._pth file: the existing tools require ordinary
    # script-directory and -c semantics when they launch their own interpreter.
    custom = destination / 'Lib' / 'site-packages' / 'sitecustomize.py'
    custom.write_text('import site\nsite.ENABLE_USER_SITE = False\n', encoding='utf-8')
    # pyvenv.cfg is deliberately absent: paths resolve from this private Python.
    clean = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
    clean['PYTHONNOUSERSITE'] = '1'
    result = subprocess.run([str(destination / 'python.exe'), '-s', '-c',
                             'import json,sys;print(json.dumps({"prefix":sys.prefix,"executable":sys.executable}))'],
                            env=clean, capture_output=True, text=True, timeout=30, check=True)
    import json
    facts = json.loads(result.stdout)
    if Path(facts['prefix']).resolve() != destination.resolve():
        raise RuntimeError('Bundled Python resolved outside its own installation')
    return facts
