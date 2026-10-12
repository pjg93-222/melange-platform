# -*- coding: utf-8 -*-
"""Both invocation modes of the staffer live surfaces stay working.

The lane's modules are imported two ways and BOTH have to keep resolving:

  package mode  ``fec.surfaces.staffer_x`` -- how the kernel suites, the
                dashboard producers and every other in-repo consumer reach
                them (only the repo ROOT is on sys.path);
  script mode   ``python fec/surfaces/staffer_live_server.py`` -- how
                start_staffer_live.ps1 and the MelangeStafferLiveSidecar task
                launch the 8841 service (only fec/surfaces is on sys.path,
                the cwd is foreign, and there is no package context at all).

Until 2026-09-06 script mode was served by a bare ``from staffer_x import y``
fallback arm, which audit_stale_imports.py fails as a stale flat-layout
reference (42 findings). The fallback now bootstraps the repo root onto
sys.path and imports package-qualified, so this suite is the regression that
keeps the FIX from silently breaking the SIDECAR: delete the bootstrap and
check (b) dies with ``ModuleNotFoundError: No module named 'fec'``.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_staffer_surface_import_modes.py
"""
import atexit
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(FEC)
SURFACES = os.path.join(FEC, 'surfaces')
AUDIT = os.path.join(FEC, 'audit', 'audit_stale_imports.py')

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name
          + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def lane_modules():
    """Every staffer surface module, plus the lane's installer."""
    names = sorted(os.path.basename(p)[:-3]
                   for p in glob.glob(os.path.join(SURFACES, 'staffer_*.py'))
                   if not os.path.basename(p).startswith('test_'))
    names.append('install_staffer_live')
    return names


def clean_env(pythonpath=None):
    """A child env with the harness's own PYTHONPATH stripped.

    run_tests.py hands some lanes ``PYTHONPATH=<root>;<fec>``. Inheriting that
    would put the repo root on the child's path for free and make the
    script-mode check vacuously green, so it is removed here and set back only
    where the check is explicitly ABOUT the repo root being visible.
    """
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if pythonpath:
        env['PYTHONPATH'] = pythonpath
    return env


# A PRIVATE EMPTY directory, never the SHARED system temp dir. `python -c` and
# `python <script>` both put the cwd (or the script's dir) on sys.path, so any
# stray module another process left in the system temp dir shadows a stdlib one
# for every probe below. OBSERVED 2026-09-13: a leftover inspect.py sitting in
# %TEMP% made four lane modules report "module 'inspect' has no attribute
# 'get_annotations'" and turned check (a) red for a reason that has nothing to do
# with this lane. The cwd still has to be FOREIGN to the repo, which an empty
# scratch directory is; it is now also EMPTY, which the shared temp dir is not.
_FOREIGN_CWD = tempfile.mkdtemp(prefix='staffer-import-modes-')
atexit.register(shutil.rmtree, _FOREIGN_CWD, True)


def run(argv, env):
    return subprocess.run(argv, cwd=_FOREIGN_CWD, env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          errors='replace', timeout=600)


def main():
    names = lane_modules()

    # ------------------------------------------------ (a) package mode
    print('[a] importable as fec.surfaces.<name>')
    ok('the lane is non-empty (a bad glob must not pass vacuously)',
       len(names) >= 20, len(names))
    # Only the repo ROOT on sys.path: neither fec/ nor fec/surfaces, so a
    # surviving flat sibling import would fail here rather than ride in on the
    # harness's path.
    code = (
        'import importlib, json\n'
        'bad = []\n'
        'for n in %r:\n'
        '    try:\n'
        '        importlib.import_module("fec.surfaces." + n)\n'
        '    except Exception as exc:\n'
        '        bad.append(n + " -> " + type(exc).__name__ + ": " + str(exc))\n'
        'print("BAD=" + json.dumps(bad))\n' % (names,)
    )
    result = run([sys.executable, '-c', code], clean_env(pythonpath=ROOT))
    marker = [ln for ln in (result.stdout or '').splitlines()
              if ln.startswith('BAD=')]
    ok('the package-mode probe ran', result.returncode == 0 and marker,
       ((result.stdout or '') + (result.stderr or '')).strip()[-400:])
    failed = json.loads(marker[0][4:]) if marker else ['probe did not report']
    ok('every staffer surface imports under its package name',
       not failed, '; '.join(failed[:4]))

    # ------------------------------------------------ (b) script mode
    # No PYTHONPATH, foreign cwd: sys.path[0] is fec/surfaces and NOTHING else
    # of this repo is reachable -- the sidecar's exact condition.
    print('[b] runnable as a direct script from fec/surfaces')
    for module in ('staffer_live_service.py', 'staffer_routing_evidence.py'):
        result = run([sys.executable, os.path.join(SURFACES, module)],
                     clean_env())
        ok('%s executes with only fec/surfaces on sys.path' % module,
           result.returncode == 0,
           ((result.stdout or '') + (result.stderr or '')).strip()[-400:])
    # These two guard a __main__ block that would start the 8841 server or run
    # the installer, so the body is executed under a non-__main__ run_name --
    # still no package context, which is what makes the relative arm fail over.
    probe = (
        'import runpy, sys\n'
        'sys.path[:] = [r"%s"] + [p for p in sys.path[1:] if r"%s" not in p]\n'
        'runpy.run_path(r"%s", run_name="_import_probe_")\n'
        'print("LOADED")\n'
    )
    for module in ('staffer_live_server.py', 'install_staffer_live.py'):
        code = probe % (SURFACES, ROOT, os.path.join(SURFACES, module))
        result = run([sys.executable, '-c', code], clean_env())
        ok('%s loads with no package context' % module,
           result.returncode == 0 and 'LOADED' in (result.stdout or ''),
           ((result.stdout or '') + (result.stderr or '')).strip()[-400:])

    # ------------------------------------------------ (c) tripwire guard
    # Scoped to this lane's files: another lane's finding must not make this
    # suite flap, and this suite must not silently absorb one either.
    print('[c] audit_stale_imports reports nothing for this lane')
    result = run([sys.executable, AUDIT, '--json'], clean_env())
    ok('the tripwire produced JSON', result.stdout.strip().startswith('{'),
       ((result.stdout or '') + (result.stderr or '')).strip()[-400:])
    report = json.loads(result.stdout)
    lane_paths = set(n + '.py' for n in names)
    mine = [f for f in report['findings']
            if f['file'].replace('\\', '/').startswith('fec/surfaces/')
            and os.path.basename(f['file'].replace('\\', '/')) in lane_paths]
    ok('the ledger actually covers this lane (a shrunk ledger must not pass)',
       report['ledger_size'] > 400, report['ledger_size'])
    ok('zero stale imports across the staffer surfaces', not mine,
       '; '.join('%(file)s:%(line)s [%(module)s]' % f for f in mine[:6]))

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
