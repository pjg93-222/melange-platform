"""Negative controls for the response-cache lane's tests (test_response_cache, test_view_cache_disk,
test_comparison_flows_cache).

Each variant below breaks one rule of response_cache.py, view_cache.py, comparison_data.py or proclock.py
(the cross-process lock rule response_cache takes its locks through). The
variant module is written to an overlay folder (<tmp>/mentat/app, so a variant's own PROJECT-relative
paths stay inside the temporary folder) that MENTAT_CACHE_TEST_OVERLAY puts in front of mentat/app;
the three suites then run in a fresh interpreter (and their child interpreters inherit the overlay).

PASS requires: the suites pass untouched, with an unmodified relocated copy of the modules and with that
copy's shorter build wait (below); every variant is caught (at least one test FAILs or ERRORs; a variant
that cannot even be imported is INVALID, not caught); and every test fails under at least one variant.

The shorter build wait (review of batch 1, round 3, 2026-09-24). Since review H2 a lock this process holds
is live to this process too, so under R-lock-leak (the release in _lead's `finally` removed) every later
build of a leaked key in the same process waits the full WAIT_SECONDS (120 s) before StillBuilding. The
three suites then outran TIMEOUT instead of failing their assertions. R-lock-leak therefore also sets
WAIT_SECONDS to FAST_WAIT_SECONDS, and the control 'relocated, fast wait' runs that same wait without the
defect: the variant still differs from a passing control in its one defect only. A run that does not finish
is still counted as caught; it is listed under the pseudo-test '<run>'.

    python response_cache_negative_controls.py [--only ID,ID] [--out result.json]

Subprocesses write to real files and are waited on with a timeout and tree-killed (brain DE-026).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

TESTS = Path(__file__).resolve().parent
APP = TESTS.parent / 'app'
SUITES = ('test_response_cache', 'test_view_cache_disk', 'test_comparison_flows_cache')
MODULES = ('response_cache.py', 'view_cache.py', 'comparison_data.py', 'proclock.py')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
TIMEOUT = 900
# R-lock-leak's second edit and the 'relocated, fast wait' control (module docstring).
FAST_WAIT_SECONDS = 5
FAST_WAIT = ('WAIT_SECONDS = 120\n', f'WAIT_SECONDS = {FAST_WAIT_SECONDS}\n')

R, V, C, P = 'response_cache.py', 'view_cache.py', 'comparison_data.py', 'proclock.py'
VARIANTS = [
    ('R-thread-flight', R, [('            leader = flight is None\n', '            leader = True\n')]),
    # The lock rule lives in proclock.py since review M3 (2026-09-24); its variants replace R-no-excl,
    # R-alive-always, R-no-own-pid-rule, R-unreadable-stale and R-unlink-any. R-no-age-rule is inverted:
    # H2 removed the age rule, so P-age-rule puts one back and must be caught.
    ('P-no-excl', P, [('os.O_CREAT | os.O_EXCL | os.O_WRONLY', 'os.O_CREAT | os.O_TRUNC | os.O_WRONLY')]),
    ('P-alive-always', P, [("    if current is None:\n        return False, 'process gone'\n",
                            "    if current is None:\n        return True, 'process gone'\n")]),
    ('P-recycle-blind', P, [("        return False, 'pid recycled: its process was created after the lock'\n",
                             "        return True, 'pid recycled: its process was created after the lock'\n")]),
    ('P-age-rule', P, [("    pid = info['pid']\n    recorded = ",
                        "    age = _seconds(info.get('started_utc'))\n"
                        "    age = _seconds(info.get('created')) if age is None else age\n"
                        "    if age is not None and _now() - age > 30 * 60:\n"
                        "        return False, 'older than 30 minutes'\n"
                        "    pid = info['pid']\n    recorded = ")]),
    ('P-no-own-pid-rule', P, [("        return False, 'left behind by this process, which no longer holds it'\n",
                               "        return True, 'left behind by this process, which no longer holds it'\n")]),
    ('P-unreadable-stale', P, [('    if _now() - written < UNREADABLE_STALE_SECONDS:\n', '    if False:\n')]),
    ('P-release-any', P, [('    if expected is not None:\n        return raw == expected\n',
                           '    if expected is not None:\n        return True\n')]),
    ('R-fifo-memory', R, [('        _memory.move_to_end((namespace, key))\n        touch', '        touch')]),
    ('R-memory-unbounded', R, [('        while _memory and _memory_used > MEMORY_BYTES:', '        while False:')]),
    ('R-disk-cap-ignored', R, [('    if total <= limit:\n        return\n', '    return\n')]),
    # R-evict-newest and R-no-disk-write were re-cut to the current code (review of batch 1, round 3,
    # 2026-09-24): the eviction by value (_eviction_order) replaced the LRU sort, and the build's _note now
    # comes between `if folder is not None:` and _disk_put. The old anchors occurred 0 times (INVALID).
    ('R-evict-newest', R, [('    return sorted(found.items(), key=rank)\n', '    return sorted(found.items(), key=rank, reverse=True)\n')]),
    ('R-no-gzip-sha', R, [(" or hashlib.sha256(data).hexdigest() != meta['gzip_sha256']:", ':')]),
    ('R-no-size-check', R, [('            if len(body) != self.body_bytes:\n', '            if False:\n')]),
    ('R-key-ignores-deps', R, [("'key_parts': key_parts, 'deps': deps})", "'key_parts': key_parts, 'deps': None})")]),
    ('R-non-atomic', R, [("        with open(temporary, 'wb') as stream:\n            stream.write(data)\n        _replace(temporary, target)\n",
                          "        with open(target, 'wb') as stream:\n            stream.write(data)\n")]),
    ('R-weak-etag', R, [("        self.etag = '\"' + body_sha256 + '\"'", "        self.etag = 'W/\"' + body_sha256 + '\"'")]),
    ('R-strict-if-none-match', R, [("        if tag.startswith('W/'):\n            tag = tag[2:]\n", '')]),
    ('R-ascii-body', R, [("    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()",
                          "    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()")]),
    ('R-gzip-level', R, [('GZIP_LEVEL = 1\n', 'GZIP_LEVEL = 6\n')]),
    ('R-code-not-revalidated', R, [('                if _stat_state(path) != recorded:', '                if False:')]),
    ('R-no-start-rule', R, [('        if state[2] >= started or state[3] >= started:', '        if False:')]),
    ('R-persist-ignored', R, [('        if persist is None or persist(value):', '        if True:')]),
    ('R-closure-top-level', R, [('    for node in ast.walk(tree):', '    for node in tree.body:')]),
    ('R-closure-sys-path-only', R, [('        path = _source_of(name, folders)', '        path = _source_of(name)')]),
    ('R-closure-no-tools', R, [("(Path(first).parent, Path(first).parent.parent / 'tools', Path(__file__).resolve().parent)",
                                '(Path(first).parent, Path(__file__).resolve().parent)')]),
    ('R-lock-leak', R, [('    finally:\n        if claimed is not None:\n            _unlink(lock, claimed)\n', '    finally:\n        pass\n'),
                        FAST_WAIT]),
    ('R-enabled-always', R, [('    return ROOT is not None', '    return True')]),
    ('R-root-default-on', R, [('\nROOT = None\n', '\nROOT = DEFAULT_ROOT\n')]),
    ('R-no-disk-write', R, [('                _disk_put(folder, namespace, key, entry, key_parts, deps)\n', '                pass\n')]),
    ('R-no-write-count', R, [("    _count(namespace, 'disk_writes')", '    pass')]),
    ('R-extra-ignored', R, [("canonical({'code': digest, 'extra': extra})", "canonical({'code': digest})")]),
    ('V-no-content', V, [("    deps = {'content_sha256': content, 'code': code}", "    deps = {'code': code}")]),
    ('V-persist-all', V, [('persist=persist, memory=False)', 'persist=None, memory=False)')]),
    ('V-code-none-ignored', V, [('        if code is None:\n            return None\n        signatures',
                                 "        if code is None:\n            code = 'unknown'\n        signatures")]),
    ('V-plain-tuple', V, [('    return FileSignature(result)', '    return tuple(result)')]),
    ('V-list-signature', V, [('    return FileSignature(result)', '    return list(result)')]),
    ('V-every-builder', V, [('    tier = DISK_TIERS.get(module)\n', "    tier = DISK_TIERS.get(module) or DISK_TIERS['graph_data']\n")]),
    ('V-no-enabled-check', V, [('    if not response_cache.enabled():\n        return None\n    module', '    module')]),
    ('V-no-signature-no-content', V, [
        ('        key_parts = _plain(key)', '        key_parts = _plain(tuple(part for part in key if not isinstance(part, FileSignature)))'),
        ("    deps = {'content_sha256': content, 'code': code}", "    deps = {'code': code}")]),
    ('C-issues-stored', C, [("        return not moved[-1] and not value.get('issues')", '        return not moved[-1]')]),
    ('C-moved-stored', C, [("        return not moved[-1] and not value.get('issues')", "        return not value.get('issues')")]),
    ('C-no-content', C, [("    deps = {'stamps': [list(version) for version in stamps], 'content_sha256': content,",
                          "    deps = {'stamps': [list(version) for version in stamps],")]),
    ('C-no-stamps-no-content', C, [("    deps = {'stamps': [list(version) for version in stamps], 'content_sha256': content,\n            'code': code or 'this process only'}",
                                    "    deps = {'code': code or 'this process only'}")]),
    ('C-disk-despite-code', C, [('memory=steady, disk=code is not None)', 'memory=steady, disk=True)')]),
    ('C-shared-object', C, [("    return entry.value if entry.hit == 'built' else json.loads(entry.body)",
                             "    return globals().setdefault('_SHARED', {}).setdefault(entry.key, entry.value if entry.hit == 'built' else json.loads(entry.body))")]),
    ('C-ascii-body', C, [('    return response_cache.get_or_compute(FLOWS_NAMESPACE, key_parts, deps, compute, persist=persist,',
                          "    return response_cache.get_or_compute(FLOWS_NAMESPACE, key_parts, deps, compute, persist=persist, encode=lambda v: json.dumps(v, separators=(',', ':')).encode(),")]),
    ('C-dedupe-input', C, [('    paths = [Path(path) for path in paths]\n', '    paths = list(dict.fromkeys(Path(path) for path in paths))\n')]),
    ('C-no-industry', C, [("    key_parts = {'industry': industry, 'reports': [version[0] for version in stamps]}",
                           "    key_parts = {'reports': [version[0] for version in stamps]}")]),
]

DRIVER = r'''
import json, os, sys, unittest
out, names = sys.argv[1], sys.argv[2:]
# Pin the three modules before any suite loads: a suite that imports server.py puts mentat/app back
# in front of the overlay, and a module first imported after that would come from mentat/app.
overlay = os.environ.get('MENTAT_CACHE_TEST_OVERLAY')
sys.path[:0] = [path for path in (overlay, os.path.join(os.path.dirname(os.getcwd()), 'app')) if path]
try:
    import response_cache, view_cache, comparison_data, proclock
except Exception as error:
    with open(out, 'w', encoding='utf-8') as stream:
        json.dump({name: {'import_error': repr(error)} for name in names}, stream)
    raise SystemExit(0)
results = {'_modules': {m.__name__: m.__file__ for m in (response_cache, view_cache, comparison_data, proclock)}}
for name in names:
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(name)
    except Exception as error:
        results[name] = {'import_error': repr(error)}
        continue
    ids = []
    def walk(item):
        for test in item:
            if isinstance(test, unittest.TestSuite):
                walk(test)
            else:
                ids.append(test.id())
    walk(suite)
    if any(i.startswith('unittest.loader._FailedTest') for i in ids):
        results[name] = {'import_error': 'module did not import'}
        continue
    result = unittest.TestResult()
    suite.run(result)
    outcomes = {i: 'ok' for i in ids}
    tracebacks = {}
    for kind, items in (('FAIL', result.failures), ('ERROR', result.errors)):
        for test, trace in items:
            test_id = getattr(test, 'test_case', test).id()
            outcomes[test_id] = kind
            tracebacks.setdefault(test_id, trace[-1200:])
    for test, reason in result.skipped:
        outcomes[getattr(test, 'test_case', test).id()] = 'skipped'
    results[name] = {'outcomes': outcomes, 'tracebacks': tracebacks}
with open(out, 'w', encoding='utf-8') as stream:
    json.dump(results, stream, indent=1)
'''


def kill_tree(process):
    if process.poll() is None:
        killer = subprocess.Popen(['taskkill', '/F', '/T', '/PID', str(process.pid)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        try:
            killer.wait(timeout=60)
        except subprocess.TimeoutExpired:
            killer.kill()
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()


def run(overlay, work):
    """{suite: {'outcomes': {test id: ok|FAIL|ERROR|skipped}} | {'import_error'}} for one overlay."""
    out, log = work / 'result.json', work / 'run.log'
    environment = dict(os.environ)
    environment.pop('MENTAT_CACHE_TEST_OVERLAY', None)
    if overlay is not None:
        environment['MENTAT_CACHE_TEST_OVERLAY'] = str(overlay)
    with open(log, 'wb') as stream:
        process = subprocess.Popen([sys.executable, '-c', DRIVER, str(out), *SUITES], cwd=str(TESTS), env=environment,
                                   stdout=stream, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        try:
            process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            kill_tree(process)
            return {'timeout': True}
    if not out.exists():
        return {'crashed': log.read_text(encoding='utf-8', errors='replace')[-2000:]}
    result = json.loads(out.read_text(encoding='utf-8'))
    loaded = result.pop('_modules', {})
    expected = str(overlay) if overlay is not None else str(APP)
    if loaded and any(not str(path).startswith(expected) for path in loaded.values()):
        return {'not_loaded': loaded}
    return result


def failing(result):
    return sorted(test for suite in result.values() if isinstance(suite, dict)
                  for test, outcome in suite.get('outcomes', {}).items() if outcome in ('FAIL', 'ERROR'))


def main():
    only = None
    if '--only' in sys.argv:
        only = set(sys.argv[sys.argv.index('--only') + 1].split(','))
    target = Path(sys.argv[sys.argv.index('--out') + 1]) if '--out' in sys.argv else None
    report = {'controls': {}, 'variants': {}, 'coverage': {}}
    started = time.time()
    with tempfile.TemporaryDirectory(prefix='cache-negctl-') as scratch:
        scratch = Path(scratch)
        # Controls: the untouched suites, an unmodified relocated copy of all the modules, and that copy with
        # R-lock-leak's shorter build wait and nothing else (module docstring).
        relocated = scratch / 'relocated' / 'mentat' / 'app'
        fast = scratch / 'relocated-fast-wait' / 'mentat' / 'app'
        for folder in (relocated, fast):
            folder.mkdir(parents=True)
            for name in MODULES:
                shutil.copyfile(APP / name, folder / name)
        text = (APP / R).read_text(encoding='utf-8')
        if text.count(FAST_WAIT[0]) != 1:
            raise SystemExit(f'{R}: {FAST_WAIT[0]!r} occurs {text.count(FAST_WAIT[0])} times; the fast-wait control '
                             'cannot be built')
        (fast / R).write_text(text.replace(*FAST_WAIT), encoding='utf-8', newline='\n')
        for label, folder, overlay in (('untouched', 'control-untouched', None),
                                       ('relocated copy', 'control-relocated-copy', relocated),
                                       ('relocated, fast wait', 'control-relocated-fast-wait', fast)):
            work = scratch / folder
            work.mkdir()
            result = run(overlay, work)
            bad = failing(result) or [key for key in ('timeout', 'crashed', 'not_loaded') if key in result] or \
                [suite for suite, value in result.items() if isinstance(value, dict) and 'import_error' in value]
            report['controls'][label] = {'pass': not bad, 'failing': bad,
                                         'tests': sum(len(v.get('outcomes', {})) for v in result.values() if isinstance(v, dict))}
            print(f"control {label:20s} {'PASS' if not bad else 'FAIL'} "
                  f"({report['controls'][label]['tests']} tests) {bad or ''}", flush=True)
        all_tests = set()
        for index, (identifier, module, edits) in enumerate(VARIANTS, 1):
            if only and identifier not in only:
                continue
            overlay = scratch / f'v{index:02d}' / 'mentat' / 'app'
            overlay.mkdir(parents=True)
            # All the modules travel together, as in the relocated control, so a variant differs
            # from that control in its one edit only.
            for name in MODULES:
                shutil.copyfile(APP / name, overlay / name)
            text = (APP / module).read_text(encoding='utf-8')
            invalid = None
            for old, new in edits:
                count = text.count(old)
                if count != 1:
                    invalid = f'anchor occurs {count} times: {old[:60]!r}'
                    break
                text = text.replace(old, new)
            if invalid:
                report['variants'][identifier] = {'state': 'INVALID', 'reason': invalid}
                print(f'{identifier:28s} INVALID {invalid}', flush=True)
                continue
            (overlay / module).write_text(text, encoding='utf-8', newline='\n')
            work = overlay.parents[1]
            began = time.time()
            result = run(overlay, work)
            if 'timeout' in result or 'crashed' in result:
                state, failed = 'CAUGHT (run did not finish)', ['<run>']
            elif 'not_loaded' in result:
                state, failed = 'INVALID (variant not the module under test)', []
            elif any(isinstance(v, dict) and 'import_error' in v for v in result.values()):
                state, failed = 'INVALID (variant does not import)', []
            else:
                failed = failing(result)
                state = 'CAUGHT' if failed else 'MISSED'
            for suite in result.values():
                if isinstance(suite, dict):
                    all_tests.update(suite.get('outcomes', {}))
            for test in failed:
                report['coverage'].setdefault(test, []).append(identifier)
            report['variants'][identifier] = {'module': module, 'state': state, 'failed': failed,
                                              'seconds': round(time.time() - began, 1),
                                              'first_trace': next((t for s in result.values() if isinstance(s, dict)
                                                                   for t in s.get('tracebacks', {}).values()), '')[-400:]}
            print(f'{identifier:28s} {state:10s} {len(failed):2d} failing ({report["variants"][identifier]["seconds"]} s)'
                  f'  {", ".join(t.rsplit(".", 1)[-1] for t in failed[:4])}{" ..." if len(failed) > 4 else ""}', flush=True)
        uncovered = sorted(test for test in all_tests if test not in report['coverage'])
        report['uncovered_tests'] = uncovered
        caught = [v for v in report['variants'].values() if v['state'].startswith('CAUGHT')]
        verdict = (all(c['pass'] for c in report['controls'].values()) and not uncovered
                   and len(caught) == len(report['variants']))
        report['verdict'] = 'NEGATIVE CONTROLS PASS' if verdict else 'NEGATIVE CONTROLS FAIL'
        report['seconds'] = round(time.time() - started, 1)
    print(f"\n{len(caught)}/{len(report['variants'])} variants caught; {len(all_tests) - len(uncovered)}/{len(all_tests)} tests "
          f"fail under at least one variant; uncovered: {uncovered or 'none'}", flush=True)
    for test, variants in sorted(report['coverage'].items()):
        # '<run>' (a run that did not finish) names no test: it is printed as it is.
        label = test if test.count('.') < 2 else test.rsplit('.', 2)[-2] + '.' + test.rsplit('.', 1)[-1]
        print(f'  {label:95s} <- {", ".join(variants)}', flush=True)
    print(report['verdict'], flush=True)
    if target:
        target.write_text(json.dumps(report, indent=1), encoding='utf-8')
    return 0 if verdict else 1


if __name__ == '__main__':
    sys.exit(main())
