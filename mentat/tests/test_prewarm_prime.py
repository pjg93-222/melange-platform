"""Value-ranked prime (RACF 3.4-3.6), /api/prime-status readiness and the launcher's wait loop.

Run from mentat/tests:  python -m unittest test_prewarm_prime
PRIME_TEST_PREWARM=<path> and PRIME_TEST_LAUNCHER=<path> pin the module and the launcher under test
(prime_negative_controls.py uses them to run this suite against deliberately broken variants).
"""
from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

APP = Path(__file__).resolve().parents[1] / 'app'
PREWARM_PATH = Path(os.environ.get('PRIME_TEST_PREWARM') or APP / 'prewarm.py')
LAUNCHER_PATH = Path(os.environ.get('PRIME_TEST_LAUNCHER') or APP / 'Start-MentatApp.ps1')


def _load_prewarm():
    spec = importlib.util.spec_from_file_location('prewarm_under_test', PREWARM_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prewarm = _load_prewarm()
TODAY = dt.date.today().isoformat()
NOW_UTC = dt.datetime.now(dt.timezone.utc).isoformat()
YESTERDAY_UTC = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()


def task(kind, dataset, body, index):
    return {'id': f'{kind}:{dataset}:{index}', 'kind': kind, 'dataset': dataset, 'body': body,
            'rank': [index], 'state': 'pending', 'source_fp': 'fp', 'index_present': True}


class PlannerRankingTests(unittest.TestCase):
    def tasks(self):
        return [task('query_view', 'A', {'dataset': 'A', 'q': 1}, 0),
                task('query_view', 'B', {'dataset': 'B', 'q': 1}, 1),
                task('insights_default', 'A', prewarm.insights_default_body('A'), 2),
                task('insights_default', 'B', prewarm.insights_default_body('B'), 3),
                task('insights_company', 'A', {**prewarm.insights_default_body('A'), 'company': 'X'}, 4),
                task('insights_company', 'B', {**prewarm.insights_default_body('B'), 'company': 'Y'}, 5)]

    def test_without_access_the_hints_order_is_untouched(self):
        tasks = self.tasks()
        ordered = prewarm.value_order(list(tasks), hints=[], access_rows=None)
        self.assertEqual([item['id'] for item in ordered], [item['id'] for item in tasks])
        self.assertNotIn('tier', ordered[0])

    def test_access_promotes_the_most_used_scope(self):
        body = {**prewarm.insights_default_body('B'), 'company': 'Y'}
        rows = [{'kind': 'insights_query', 'key': prewarm.access_key(body), 'body': body, 'score': 50.0,
                 'count': 20, 'last_utc': NOW_UTC, 'cost_s': 8.0, 'size': 200000}]
        ordered = prewarm.value_order(self.tasks(), hints=[], access_rows=rows, max_hot=3)
        self.assertEqual(ordered[0]['id'], 'insights_company:B:5')
        self.assertEqual(ordered[0]['tier'], 'hot')
        self.assertEqual([item['tier'] for item in ordered].count('hot'), 3)
        # The background keeps its original (hints-only) order.
        background = [item['id'] for item in ordered if item['tier'] == 'background']
        self.assertEqual(background, sorted(background, key=lambda name: int(name.rsplit(':', 1)[1])))

    def test_a_changed_source_reprimes_its_hot_items_first(self):
        ordered = prewarm.value_order(self.tasks(), hints=[], access_rows=None, reprime=('B',), max_hot=4)
        self.assertEqual(ordered[0]['dataset'], 'B')
        self.assertEqual(ordered[0]['tier'], 'reprime')
        self.assertTrue(all(item['tier'] != 'reprime' or item['dataset'] == 'B' for item in ordered))

    def test_build_plan_ranks_by_access_value(self):
        class Backend:
            def sources(self):
                return [{'dataset': name, 'available': True, 'verified': True, 'source_fp': 'fp' + name,
                         'index_present': True} for name in ('A', 'B')]

            def query_view_body(self, dataset):
                return {'dataset': dataset, 'q': 1}

        plain = prewarm.build_plan(Backend(), hints=[], today=TODAY, code_fp='c')
        self.assertEqual(plain['tasks'][0]['id'], prewarm.task_id('query_view', 'A', {'dataset': 'A', 'q': 1}))
        self.assertEqual(plain['ranking'], 'hints')
        rows = [{'kind': 'contribution_query', 'key': prewarm.access_key({'dataset': 'B', 'q': 1}),
                 'body': {'dataset': 'B', 'q': 1}, 'score': 30.0, 'count': 9, 'last_utc': NOW_UTC, 'cost_s': 25.0, 'size': 1000}]
        ranked = prewarm.build_plan(Backend(), hints=[], today=TODAY, code_fp='c', access_rows=rows)
        self.assertEqual(ranked['tasks'][0]['id'], prewarm.task_id('query_view', 'B', {'dataset': 'B', 'q': 1}))
        self.assertEqual(ranked['ranking'], 'value')

    def test_load_access_top_falls_back_without_access_stats(self):
        with mock.patch.dict(sys.modules, {'access_stats': None}):
            self.assertEqual(prewarm.load_access_top(), [])
        broken = types.SimpleNamespace(top=lambda **_: (_ for _ in ()).throw(RuntimeError('x')))
        self.assertEqual(prewarm.load_access_top(module=broken), [])


def console_access_kinds():
    """Every kind server.py passes to record_access (both branches of a conditional kind)."""
    def kinds(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return kinds(node.body) | kinds(node.orelse)
        return set()
    found = set()
    for node in ast.walk(ast.parse((APP / 'server.py').read_text(encoding='utf-8'))):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'record_access' and len(node.args) > 1:
            found |= kinds(node.args[1])
    return found


class WarmerAccessWindowTests(unittest.TestCase):
    """Review of batch 1, round 3 (2026-09-24): the warmer re-planned from access_stats.top() over every kind.
    The cache layers' own 'store/...' and 'cache/...' rows (no body; they rank no task) then filled its
    ACCESS_TOP_LIMIT window and pushed out the requests people made. The ledger here is a real access_stats
    ledger in a temporary folder; the ops are the CLI's own op_run and op_plan, with the Warmer and the
    backend replaced so that nothing is loaded or warmed."""

    class Backend:
        def sources(self):
            return [{'dataset': name, 'available': True, 'verified': True, 'source_fp': 'fp' + name,
                     'index_present': True} for name in ('A', 'B')]

        def query_view_body(self, dataset):
            return {'dataset': dataset, 'q': 1}

    def ledger(self, folder):
        """A real access_stats module on a ledger in `folder`: one request of each kind the console records,
        all for industry B, and more cache-layer rows than the window holds, each worth more than any request.
        (the module, the (kind, key) of every request)."""
        spec = importlib.util.spec_from_file_location('access_stats_under_test', APP / 'access_stats.py')
        stats = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stats)
        stats.configure(Path(folder) / 'access.sqlite')
        self.addCleanup(stats.configure, Path(folder) / 'closed.sqlite')   # closes its connections first
        requests = set()
        with mock.patch.dict(os.environ, {'MENTAT_ACCESS_STATS': '1'}):
            for kind in sorted(console_access_kinds()):
                body = {'dataset': 'B', 'q': 1} if kind == 'contribution_query' else {'industry': 'B', 'view': kind}
                stats.record(kind, prewarm.access_key(body), body=body, cost_s=1.0, size=100000)
                requests.add((kind, prewarm.access_key(body)))
            for index in range(prewarm.ACCESS_TOP_LIMIT + 20):
                stats.record(('cache/graph-views', 'store/results')[index % 2], f'{index:064x}', cost_s=30.0, size=1000)
        self.assertTrue(stats.flush())
        return stats, requests

    def test_the_warmer_ranks_from_every_request_and_no_cache_layer_row(self):
        kinds = console_access_kinds()
        self.assertTrue({'graph_view', 'graph_drawer', 'comparison_flows', 'insights_query', 'contribution_query'} <= kinds,
                        kinds)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        stats, requests = self.ledger(folder.name)
        captured = {}

        class Captured(Exception):
            pass

        class Warmer:
            def __init__(self, backend, state, **options):
                captured[options.get('op')] = options.get('access_top')

            def replan(self, reason):
                raise Captured(reason)

            def run(self):
                return 'ran'

        class Unloaded:
            def __init__(self, **options):
                pass

            def load(self):
                return self

        state_root = str(Path(folder.name) / 'state')
        with mock.patch.object(prewarm, 'Warmer', Warmer), mock.patch.object(prewarm, 'AppBackend', Unloaded):
            self.assertEqual(prewarm.op_run(prewarm._parser().parse_args(['run', '--state-root', state_root])), 'ran')
            with self.assertRaises(Captured):
                prewarm.op_plan(prewarm._parser().parse_args(['plan', '--state-root', state_root]))
        for op in ('run', 'plan'):
            with self.subTest(op=op), mock.patch.dict(sys.modules, {'access_stats': stats}):
                self.assertTrue(callable(captured.get(op)), f'{op} gives the warmer no access ranking')
                rows = captured[op]()
                self.assertEqual(sorted({row['kind'] for row in rows if '/' in row['kind']}), [],
                                 'cache-layer rows fill the window the warmer ranks by')
                self.assertEqual(sorted((row['kind'], row['key']) for row in rows), sorted(requests))
                # What the window decides: B's recorded query goes first, B being the industry last used.
                plan = prewarm.build_plan(self.Backend(), hints=[], today=TODAY, code_fp='c', access_rows=rows)
                self.assertEqual(plan['tasks'][0]['id'], prewarm.task_id('query_view', 'B', {'dataset': 'B', 'q': 1}))


class HotSetTests(unittest.TestCase):
    INDUSTRIES = ['A', 'B', 'C']

    def hints(self, seen=NOW_UTC):
        body = {**prewarm.insights_default_body('B'), 'company': 'Y'}
        return [{'body': body, 'key': 'k' * 64, 'dataset': 'B', 'seen_utc': seen, 'position': 0}]

    def test_most_recent_industry_ranks_first_hints_only(self):
        candidates, ranking = prewarm.prime_candidates(self.INDUSTRIES, self.hints(), [], TODAY)
        self.assertEqual(ranking['recent'], 'B')
        self.assertEqual(ranking['order'][0], 'B')
        self.assertEqual(ranking['weights'], {'B': 1.0 + prewarm.RECENT_INDUSTRY_BOOST, 'A': 0.5, 'C': round(1 / 3, 6)})
        hot, background, _ = prewarm.select_hot_set(candidates, max_items=4, budget_seconds=1000)
        self.assertEqual({item['industry'] for item in hot[:3]}, {'B'})
        self.assertIn('insights_scope', [item['kind'] for item in hot])
        kinds = {(item['kind'], item['industry']) for item in candidates}
        self.assertTrue({('graph_default', name) for name in self.INDUSTRIES} <= kinds)
        self.assertTrue({('comparison_flows', name) for name in self.INDUSTRIES} <= kinds)

    def test_an_earlier_days_hint_is_not_hot(self):
        candidates, _ = prewarm.prime_candidates(self.INDUSTRIES, self.hints(YESTERDAY_UTC), [], TODAY)
        self.assertNotIn('insights_scope', [item['kind'] for item in candidates])

    def test_budget_caps_items_and_seconds(self):
        candidates, _ = prewarm.prime_candidates(self.INDUSTRIES, self.hints(), [], TODAY)
        hot, background, spent = prewarm.select_hot_set(candidates, max_items=3, budget_seconds=1000)
        self.assertEqual(len(hot), 3)
        self.assertEqual(len(hot) + len(background), len(candidates))
        self.assertEqual([item['value'] for item in hot], sorted([item['value'] for item in hot], reverse=True))
        self.assertGreaterEqual(min(item['value'] for item in hot), max(item['value'] for item in background))
        hot, background, spent = prewarm.select_hot_set(candidates, max_items=50, budget_seconds=4.0)
        self.assertLessEqual(spent, 4.0)
        self.assertTrue(any(item['why'] == 'over the startup budget' for item in background))

    def test_recorded_views_join_the_ranking(self):
        params = {**prewarm.graph_default_params('C'), 'company': 'Z'}
        rows = [{'kind': 'graph_view', 'key': prewarm.access_key(params), 'body': params, 'score': 40.0,
                 'count': 12, 'last_utc': NOW_UTC, 'cost_s': 4.0, 'size': 50000}]
        candidates, ranking = prewarm.prime_candidates(self.INDUSTRIES, [], rows, TODAY)
        hot, _, _ = prewarm.select_hot_set(candidates, max_items=1, budget_seconds=1000)
        self.assertEqual((hot[0]['kind'], hot[0]['industry'], hot[0]['source']), ('graph_view', 'C', 'access'))
        self.assertEqual(ranking['recent'], 'C')


class FakeApp:
    def __init__(self, names, fail=()):
        self.report_directory = 'reports'
        self.names, self.fail, self.calls, self.status_at_background = names, set(fail), [], None
        self.hot_ids = set()

    def published_reports(self):
        return list(self.names)

    def graph_catalog(self, identifier, reports=None):
        return {'industries': [{'name': name, 'indices': [index]} for index, name in enumerate(self.names)]}

    def report_path(self, identifier, index):
        return f'report-{index}.xlsx'

    def graph(self, identifier, industry, filters, registry=None):
        self.calls.append(('graph', industry))
        if industry in self.fail:
            raise ValueError('broken view')
        if prewarm._prime['phase'] == 'background' and self.status_at_background is None:
            self.status_at_background = prewarm.prime_status()
        return {'industry': industry}

    def comparison_entry(self, identifier, industry, reports=None):
        self.calls.append(('flows', industry))
        return types.SimpleNamespace(hit='built')


def fake_modules(names):
    sources = [{'id': name, 'available': True} for name in names]
    return {'contribution_data': types.SimpleNamespace(_sources=lambda directory: sources),
            'contribution_workbook_guard': types.SimpleNamespace(
                verify_sources=lambda directory, dataset, strict=False: [{'dataset': dataset}]),
            'report_insights': types.SimpleNamespace(embedded_receipts=lambda stamp: None, stamp=lambda path: path),
            'snapshot_registry': types.SimpleNamespace(registry=lambda directory: {'snapshots': []}),
            'industry_scope': types.SimpleNamespace(label_or_value=lambda registry, value: value),
            'analysis_data': types.SimpleNamespace(_probe=lambda identity: None, _code_hash='c')}


class PrimeStatusTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(prewarm._prime)
        self.addCleanup(lambda: (prewarm._prime.clear(), prewarm._prime.update(self.saved)))

    def test_ready_counts_and_primed(self):
        prewarm._prime.update(phase='hot', items=[{'id': 'a', 'state': 'ready'}, {'id': 'b', 'state': 'pending'}])
        status = prewarm.prime_status()
        self.assertEqual((status['primed'], status['hot_total'], status['hot_ready'], status['phase']), (False, 2, 1, 'hot'))
        prewarm._prime['items'][1]['state'] = 'ready'
        self.assertFalse(prewarm.prime_status()['primed'], 'all ready but the hot phase is still running')
        prewarm._prime['phase'] = 'background'
        self.assertTrue(prewarm.prime_status()['primed'])
        prewarm._prime['items'][1]['state'] = 'needs_build'
        status = prewarm.prime_status()
        self.assertEqual((status['primed'], status['hot_ready']), (False, 1))
        self.assertEqual(set(status), {'primed', 'hot_total', 'hot_ready', 'phase', 'started_utc', 'primed_utc',
                                       'finished_utc', 'background_total', 'estimate_s', 'ranking', 'error', 'items'})

    def run_startup(self, app, names, max_items):
        with tempfile.TemporaryDirectory() as state, \
                mock.patch.dict(sys.modules, fake_modules(names)), \
                mock.patch.object(prewarm, 'DEFAULT_STATE_ROOT', Path(state)), \
                mock.patch.object(prewarm, 'HOT_MAX_ITEMS', max_items), \
                mock.patch.object(prewarm, 'load_access_top', lambda **_: []), \
                mock.patch.object(prewarm, 'free_physical_bytes', lambda: 64 * 1024 ** 3), \
                mock.patch.object(prewarm, 'emit', lambda *a, **k: None):
            hints_dir = Path(state) / 'prewarm'
            hints_dir.mkdir(parents=True)
            (hints_dir / 'hints.json').write_text(json.dumps({'version': 1, 'requests': [
                {'body': prewarm.insights_default_body('C'), 'key': 'x', 'dataset': 'C', 'seen_utc': YESTERDAY_UTC}]}))
            prewarm._prime.update(phase='starting', items=[])
            prewarm._startup_warm(True, app)

    def test_startup_primes_the_hot_set_before_anything_else(self):
        app = FakeApp(['A', 'B', 'C'])
        self.run_startup(app, ['A', 'B', 'C'], max_items=2)
        # C was used most recently: its comparison flows and default view are the hot set, primed first.
        self.assertEqual(app.calls[:2], [('flows', 'C'), ('graph', 'C')])
        self.assertEqual(app.status_at_background['primed'], True)
        self.assertEqual((app.status_at_background['hot_total'], app.status_at_background['hot_ready']), (2, 2))
        final = prewarm.prime_status()
        self.assertEqual(final['phase'], 'done')
        self.assertIn(('graph', 'A'), app.calls[2:])

    def test_a_failed_hot_item_is_not_ready(self):
        app = FakeApp(['A', 'B', 'C'], fail=['C'])
        self.run_startup(app, ['A', 'B', 'C'], max_items=2)
        status = prewarm.prime_status()
        self.assertEqual((status['primed'], status['hot_ready'], status['hot_total']), (False, 1, 2))
        self.assertEqual([item['state'] for item in status['items'] if item['kind'] == 'graph_default'], ['failed'])


class AccessRecorderTests(unittest.TestCase):
    def test_records_off_thread_and_never_raises(self):
        seen = []
        done = threading.Event()

        def record(kind, key, body=None, cost_s=None, size=None):
            seen.append((kind, key, body, cost_s, size))
            done.set()

        recorder = prewarm.AccessRecorder(record, maxsize=4)
        recorder.submit('graph_view', {'industry': 'A'}, cost_s=0.5, size=10)
        self.assertTrue(done.wait(5))
        self.assertEqual(seen[0], ('graph_view', prewarm.access_key({'industry': 'A'}), {'industry': 'A'}, 0.5, 10))
        failing = prewarm.AccessRecorder(lambda *a, **k: (_ for _ in ()).throw(OSError('disk')), maxsize=1)
        for _ in range(50):
            failing.submit('graph_view', {'industry': 'A'})   # a full queue drops; a failing record is counted
        self.assertGreaterEqual(failing.dropped + failing.failed, 1)

    def test_start_without_access_stats_is_off(self):
        with mock.patch.dict(sys.modules, {'access_stats': None}):
            self.assertIsNone(prewarm.AccessRecorder.start())


# ----------------------------------------------------------------------------- the launcher's wait loop
class Stub(BaseHTTPRequestHandler):
    scripts = {}
    hits = {}

    def log_message(self, *_):
        pass

    def do_GET(self):
        scenario = self.path.strip('/').split('/')[0]
        count = Stub.hits.get(scenario, 0)
        Stub.hits[scenario] = count + 1
        answer = Stub.scripts[scenario](count)
        if answer is None:
            body = b'{"error":"Not found."}'
            self.send_response(404)
        else:
            body = json.dumps(answer).encode()
            self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


DRIVER = r'''
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText($env:PRIME_LAUNCHER)
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$null, [ref]$null)
foreach ($definition in $ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and
    @('Get-MentatPrimeStatus', 'Wait-MentatPrimed') -contains $n.Name }, $true)) {
  . ([ScriptBlock]::Create($definition.Extent.Text))
}
$calls = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Wait-MentatPrimed' }, $true))
$guarded = @($calls | Where-Object {
  $node = $_.Parent; $inside = $false
  while ($node) {
    if ($node -is [Management.Automation.Language.IfStatementAst] -and $node.Clauses[0].Item1.Extent.Text -eq '-not $NoBrowser') { $inside = $true; break }
    $node = $node.Parent
  }
  $inside })
$results = [ordered]@{ calls = $calls.Count; guarded = $guarded.Count }
foreach ($scenario in ($env:PRIME_SCENARIOS -split ',')) {
  $watch = [Diagnostics.Stopwatch]::StartNew()
  $outcome = Wait-MentatPrimed ($env:PRIME_BASE + '/' + $scenario) ([int]$env:PRIME_TIMEOUT) ([int]$env:PRIME_POLL)
  $results[$scenario] = [ordered]@{ primed = $outcome.Primed; reason = $outcome.Reason; seconds = $watch.Elapsed.TotalSeconds }
}
[IO.File]::WriteAllText($env:PRIME_OUT, ($results | ConvertTo-Json -Depth 4))
'''


class LauncherWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Stub.scripts = {
            'third': lambda n: {'primed': n >= 2, 'hot_total': 3, 'hot_ready': min(n + 1, 3), 'phase': 'background' if n >= 2 else 'hot'},
            'never': lambda n: {'primed': False, 'hot_total': 3, 'hot_ready': 1, 'phase': 'hot'},
            'old': lambda n: None,
            'ended': lambda n: {'primed': False, 'hot_total': 3, 'hot_ready': 2, 'phase': 'background'},
        }
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.results = cls.run_driver('third,never,old,ended')

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    @classmethod
    def run_driver(cls, scenarios):
        folder = Path(tempfile.mkdtemp(prefix='prime-launcher-'))
        script, out = folder / 'driver.ps1', folder / 'out.json'
        script.write_text(DRIVER, encoding='utf-8')
        env = {**os.environ, 'PRIME_LAUNCHER': str(LAUNCHER_PATH), 'PRIME_BASE': f'http://127.0.0.1:{cls.server.server_port}',
               'PRIME_SCENARIOS': scenarios, 'PRIME_TIMEOUT': '6', 'PRIME_POLL': '200', 'PRIME_OUT': str(out)}
        with open(folder / 'stdout.txt', 'wb') as stdout, open(folder / 'stderr.txt', 'wb') as stderr:
            process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                        '-File', str(script)], stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=env)
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                subprocess.call(['taskkill', '/T', '/F', '/PID', str(process.pid)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                process.wait(timeout=10)
                return {'error': 'driver timed out'}
        if not out.exists():
            return {'error': (folder / 'stderr.txt').read_text(errors='replace')[-2000:]}
        return json.loads(out.read_text(encoding='utf-8-sig'))

    def test_waits_until_primed(self):
        result = self.results.get('third') or self.fail(self.results)
        self.assertEqual((result['primed'], result['reason']), (True, 'primed'))
        self.assertGreaterEqual(result['seconds'], 0.35)   # two 200 ms polls before the third answer
        self.assertLess(result['seconds'], 5.5)

    def test_times_out_and_opens_anyway(self):
        result = self.results.get('never') or self.fail(self.results)
        self.assertEqual((result['primed'], result['reason']), (False, 'timeout'))
        self.assertGreaterEqual(result['seconds'], 5.9)
        self.assertLess(result['seconds'], 10.0)

    def test_an_older_console_is_not_waited_for(self):
        result = self.results.get('old') or self.fail(self.results)
        self.assertEqual((result['primed'], result['reason']), (False, 'phase unsupported'))
        self.assertLess(result['seconds'], 4.0)   # well inside the 6 s timeout: no wait

    def test_an_ended_hot_phase_stops_the_wait(self):
        result = self.results.get('ended') or self.fail(self.results)
        self.assertEqual((result['primed'], result['reason']), (False, 'phase background'))
        self.assertLess(result['seconds'], 4.0)

    def test_only_the_window_path_waits(self):
        self.assertNotIn('error', self.results, self.results)
        self.assertEqual((self.results['calls'], self.results['guarded']), (1, 1),
                         'Wait-MentatPrimed must be called once, inside if (-not $NoBrowser)')


if __name__ == '__main__':
    unittest.main()
