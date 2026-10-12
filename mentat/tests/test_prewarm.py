"""The prewarm harness with fakes: client bodies, plan ordering, guards, the recovery contract,
quarantine, date and hint re-plans, the single-instance lock, observation and CLI shapes, Glass Box
emission, the in-console entry points and verify's comparison helpers.

No report folder, analytical index or console is touched. Run from mentat/tests:
    python -m unittest test_prewarm -v
PREWARM_UNDER_TEST / GLASSBOX_UNDER_TEST point the suite at a mutated copy
(prewarm_negative_controls.py uses them to show every test fails on a broken variant).
"""
import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1] / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
import python_child  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


glassbox_emit = _load('glassbox_emit', os.environ.get('GLASSBOX_UNDER_TEST') or APP / 'glassbox_emit.py')
prewarm = _load('prewarm', os.environ.get('PREWARM_UNDER_TEST') or APP / 'prewarm.py')
PREWARM_FILE = Path(prewarm.__file__)
CORE = ['favorites', 'trends', 'changes']
GIB = 1024 ** 3


def run_bounded(arguments, timeout, cwd=None, env=None):
    """A child with no pipes (DE-026): both streams on real temporary files, wait(timeout), and on an
    overrun the child's whole tree is killed before TimeoutExpired propagates."""
    arguments, env = python_child.argv(arguments, env)   # one process under Mentat's venv too
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        child = subprocess.Popen(arguments, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            code = child.wait(timeout=timeout)
        except BaseException:
            kill_tree(child)
            raise
        out.seek(0)
        err.seek(0)
        return code, out.read().decode('utf-8', 'replace'), err.read().decode('utf-8', 'replace')


def kill_tree(child):
    if child.poll() is not None:
        return
    if os.name == 'nt':
        killer = subprocess.Popen(['taskkill', '/F', '/T', '/PID', str(child.pid)], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            killer.wait(timeout=30)
        except subprocess.TimeoutExpired:
            killer.kill()
    else:
        child.kill()
    try:
        child.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def spawn(arguments):
    """A long-lived child whose stdout is a real file; (child, path of that file)."""
    handle, name = tempfile.mkstemp(suffix='.out')
    os.close(handle)
    arguments, env = python_child.argv(arguments)   # one process: the pid a lock names is this child's own
    with open(name, 'wb') as stream:
        child = subprocess.Popen(arguments, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.DEVNULL)
    return child, Path(name)


def first_line(path, timeout=60):
    """The child's first printed line, read from its file within `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
        if chr(10) in text:
            return text.splitlines()[0].strip()
        time.sleep(0.05)
    raise AssertionError(f'the child printed nothing within {timeout} s')


def exited_pid():
    """The pid of a process that has already exited."""
    code, out, _ = run_bounded([sys.executable, '-c', 'import os; print(os.getpid())'], 60)
    return int(out.strip())


def stop(child, path):
    kill_tree(child)
    try:
        Path(path).unlink()
    except OSError:
        pass


def next_day(day):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(float(seconds), 0.001)


def graph_filters(cycle='2026', **overrides):
    value = {'company': '', 'tier': '', 'cycle': cycle, 'category': '', 'recipient': '', 'funding': 'all',
             'mode': 'direct', 'direction': 'Support'}
    value.update(overrides)
    return value


class FakeBackend:
    """The backend interface the Warmer and build_plan use, with no Mentat module behind it."""

    def __init__(self, tmp, datasets=('Alpha', 'Beta', 'Gamma'), companies=None, today=None):
        self.tmp = Path(tmp)
        self.sources_list = [{'dataset': name, 'available': True, 'verified': True, 'message': None,
                              'source_fp': 'fp-' + name, 'index_present': True} for name in datasets]
        self.companies = {'Alpha': ['A1', 'A2', 'A3']} if companies is None else companies
        self.code_fp = 'code-1'
        self.calls = []
        self.fail = {}
        self.today_value = today or prewarm.local_today()

    def source(self, dataset):
        return next(item for item in self.sources_list if item['dataset'] == dataset)

    def sources(self):
        return copy.deepcopy(self.sources_list)

    def code_fingerprint(self):
        return self.code_fp

    def release_graph_memory(self):
        pass

    def query_view_body(self, dataset):
        return prewarm.spark_request(dataset, graph_filters())

    def warm(self, task, deadline=None, should_stop=None, retry=False, sleep=None):
        self.calls.append((task['id'], bool(retry)))
        failure = self.fail.get(task['id']) or self.fail.get(task['kind'])
        if failure is not None:
            raise failure() if isinstance(failure, type) else failure
        response = self.tmp / ('response-' + prewarm.digest(task['id'])[:16] + '.json')
        response.write_text('{}', encoding='utf-8')
        info = {'status': 'ready', 'key': 'key-' + task['id'], 'response_path': str(response), 'polls': 0,
                'statuses': ['ready'], 'as_of': None}
        if task['kind'] != 'query_view':
            info['as_of'] = task['body'].get('as_of') or self.today_value
            companies = self.companies.get(task['dataset'])
            if companies is not None:
                info['companies_list'] = list(companies)
                info['companies'] = len(companies)
        return info


def write_hints(state, *entries):
    state.ensure()
    requests = [{'body': body, 'key': key, 'dataset': body['dataset'], 'seen_utc': seen} for body, key, seen in entries]
    state.hints.write_text(json.dumps({'version': 1, 'updated_utc': '2026-09-24T12:00:00Z', 'requests': requests}),
                           encoding='utf-8')


class Harness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state = prewarm.StateDir(self.root / 'state')
        self.events = []
        self.backend = FakeBackend(self.root)

    def warmer(self, backend=None, **overrides):
        backend = backend or self.backend
        clock = overrides.pop('clock', None) or Clock()
        options = dict(today=lambda: backend.today_value, monotonic=clock.monotonic, sleep=clock.sleep,
                       free_bytes=lambda: 8 * GIB, console_probe=lambda url: (False, {}),
                       code_stamps=lambda: {'prewarm.py': [1, 1]},
                       emitter=lambda kind, status, summary, **fields: self.events.append((kind, status, summary, fields)),
                       set_priority=False, once=True)
        options.update(overrides)
        return prewarm.Warmer(backend, self.state, **options)

    def ledger(self):
        return prewarm.Ledger(self.state).read()

    def plan(self, **overrides):
        options = dict(hints=prewarm.read_hints(self.state.hints), today=self.backend.today_value, code_fp='code-1')
        options.update(overrides)
        return prewarm.build_plan(self.backend, **options)


class ClientContractTests(Harness):
    def test_bodies_are_the_clients_bodies(self):
        check = prewarm.client_contract_check(APP)
        self.assertTrue(check['ok'], check['missing'])
        self.assertEqual(prewarm.insights_default_body('Alpha'), {'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE})
        self.assertEqual(prewarm.graph_first_load_query('Property & Casualty Insurance'),
                         'id=published-reports&industry=Property+%26+Casualty+Insurance&company=&tier=&cycle='
                         '&funding=all&mode=direct&direction=Support&category=&recipient=&latest=1')
        body = prewarm.spark_request('Alpha', graph_filters('2026'))
        self.assertEqual(json.dumps(body, separators=(',', ':')),
                         '{"dataset":"Alpha","basis":"receipt","dimensions":["company","quarter"],"measures":["amount"],'
                         '"filters":[{"field":"cycle","op":"eq","value":"2025-2026"}],"aggregate_filters":[],'
                         '"sort":[{"field":"quarter","direction":"asc"}],"limit":100000,"offset":0}')
        personal = prewarm.spark_request('Alpha', graph_filters('', company='Acme', funding='personal'), 'Super PAC', 'C00000001')
        self.assertEqual(personal['filters'], [{'field': 'company', 'op': 'eq', 'value': 'Acme'},
                                               {'field': 'funding_source', 'op': 'eq', 'value': 'employee_original'},
                                               {'field': 'recipient_category', 'op': 'eq', 'value': 'Super PAC'},
                                               {'field': 'committee_id', 'op': 'eq', 'value': 'C00000001'}])
        self.assertEqual(prewarm.spark_request('Alpha', graph_filters(funding='corporate'))['filters'][-1]['value'],
                         'corporate_original')
        self.assertIsNone(prewarm.spark_request('Alpha', graph_filters(tier='Chief')))
        self.assertEqual(prewarm.insights_poll_delay(0, 300), 0.3)
        self.assertEqual(prewarm.insights_poll_delay(0, None), 1.5)
        self.assertEqual(prewarm.insights_poll_delay(0, 50), 0.2)
        self.assertEqual(prewarm.insights_poll_delay(40, 300), 5.0)
        self.assertEqual(prewarm.spark_poll_delay(None), 1.0)

    def test_contract_reports_client_drift(self):
        copy_dir = self.root / 'client'
        (copy_dir / 'templates').mkdir(parents=True)
        for name in prewarm.CLIENT_CONTRACT:
            text = (APP / name).read_text(encoding='utf-8')
            if name == 'insights.js':
                text = text.replace("const CORE = ['favorites', 'trends', 'changes'];", "const CORE = ['favorites', 'trends'];")
            (copy_dir / name).write_text(text, encoding='utf-8')
        check = prewarm.client_contract_check(copy_dir)
        self.assertFalse(check['ok'])
        self.assertEqual([item['file'] for item in check['missing']], ['insights.js'])


class PlanTests(Harness):
    def test_kind_order_then_newest_hint_first(self):
        write_hints(self.state,
                    ({'dataset': 'Gamma', 'basis': 'receipt', 'panels': CORE}, 'k1', '2026-09-24T12:05:00Z'),
                    ({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE}, 'k2', '2026-09-24T12:01:00Z'))
        plan = self.plan()
        self.assertEqual(plan['order'], ['Gamma', 'Alpha', 'Beta'])
        kinds = [(task['kind'], task['dataset']) for task in plan['tasks']]
        self.assertEqual(kinds[:6], [('query_view', 'Gamma'), ('query_view', 'Alpha'), ('query_view', 'Beta'),
                                     ('insights_default', 'Gamma'), ('insights_default', 'Alpha'),
                                     ('insights_default', 'Beta')])
        self.assertTrue(all(kind == 'insights_neighbor' for kind, _ in kinds[6:]))
        self.assertEqual(plan['newest_hint']['dataset'], 'Gamma')
        self.assertTrue(all(task['state'] == 'pending' for task in plan['tasks']))

    def test_neighbours_follow_the_client_select_then_the_other_bases(self):
        hint = {'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE, 'company': 'A2'}
        write_hints(self.state, (hint, 'k', '2026-09-24T12:00:00Z'))
        options = {'Alpha': {'receipt': {'source_fp': 'fp-Alpha', 'companies': ['A1', 'A2', 'A3']}}}
        plan = self.plan(options_cache=options)
        neighbours = [task['body'] for task in plan['tasks'] if task['kind'] == 'insights_neighbor']
        self.assertEqual(neighbours[0], hint)                                   # the hint's own scope first
        # 'All covered companies' is the body without a company key: the default body, warmed once.
        self.assertNotIn({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE}, neighbours)
        self.assertEqual([body.get('company') for body in neighbours[1:3]], ['A1', 'A3'])
        self.assertEqual([(body.get('company'), body['basis']) for body in neighbours[3:]],
                         [('A2', 'original_observed'), ('A2', 'original_supported'), ('A2', 'pac_activity')])
        self.assertIsNone(plan['pending_expansion'])
        capped = self.plan(options_cache=options, max_company_neighbors=1)
        companies = [task['body'].get('company') for task in capped['tasks'] if task['reason'] == 'newest hint: other company']
        self.assertEqual(companies, ['A1'])

    def test_unknown_companies_wait_for_the_hint_scope_result(self):
        write_hints(self.state, ({'dataset': 'Alpha', 'basis': 'pac_activity', 'panels': CORE}, 'k', '2026-09-24T12:00:00Z'))
        plan = self.plan()
        self.assertEqual(plan['pending_expansion'], {'dataset': 'Alpha', 'basis': 'pac_activity'})
        stale = {'Alpha': {'pac_activity': {'source_fp': 'fp-OLD', 'companies': ['A1']}}}
        self.assertEqual(self.plan(options_cache=stale)['pending_expansion'], {'dataset': 'Alpha', 'basis': 'pac_activity'})

    def test_unverified_sources_are_skipped_and_missing_indexes_blocked(self):
        self.backend.source('Beta')['verified'] = False
        self.backend.source('Beta')['message'] = 'Beta: Published workbook totals differ'
        self.backend.source('Gamma')['index_present'] = False
        plan = self.plan()
        self.assertEqual({task['dataset'] for task in plan['tasks']}, {'Alpha', 'Gamma'})
        self.assertEqual([item['reason'] for item in plan['skipped']], ['source_unverified'])
        gamma = [task for task in plan['tasks'] if task['dataset'] == 'Gamma']
        self.assertTrue(gamma and all(task['state'] == 'blocked' and task['root_cause_hint'] == 'index_missing' for task in gamma))
        allowed = self.plan(allow_index_builds=True)
        self.assertTrue(all(task['state'] == 'pending' for task in allowed['tasks'] if task['dataset'] == 'Gamma'))

    def test_a_request_is_planned_once(self):
        write_hints(self.state, ({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE}, 'k', '2026-09-24T12:00:00Z'))
        plan = self.plan(options_cache={'Alpha': {'receipt': {'source_fp': 'fp-Alpha', 'companies': []}}})
        bodies = [(task['dataset'], prewarm.canonical(task['body'])) for task in plan['tasks'] if task['kind'] != 'query_view']
        self.assertEqual(len(bodies), len(set(bodies)))
        default = [task for task in plan['tasks'] if task['kind'] == 'insights_default' and task['dataset'] == 'Alpha']
        self.assertEqual(len(default), 1)

    def test_hints_file_is_read_defensively(self):
        self.state.ensure()
        self.state.hints.write_text(json.dumps({'version': 1, 'requests': [
            {'body': {'dataset': 'Alpha', 'retry': True, 'basis': 'receipt'}, 'key': 'old', 'dataset': 'Alpha', 'seen_utc': '2026-09-24T10:00:00Z'},
            'not an object', {'body': 'nope'}, {'body': {'basis': 'receipt'}},
            {'body': {'dataset': 'Beta'}, 'key': 7, 'dataset': 'Beta', 'seen_utc': '2026-09-24T11:00:00Z'}]}), encoding='utf-8')
        hints = prewarm.read_hints(self.state.hints)
        self.assertEqual([hint['dataset'] for hint in hints], ['Beta', 'Alpha'])   # newest first by seen_utc
        self.assertNotIn('retry', hints[1]['body'])
        self.assertIsNone(hints[0]['key'])
        self.state.hints.write_text(json.dumps({'version': 2, 'requests': []}), encoding='utf-8')
        self.assertEqual(prewarm.read_hints(self.state.hints), [])


class RunLoopTests(Harness):
    def test_run_warms_every_task_once_and_records_it(self):
        write_hints(self.state, ({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE, 'company': 'A1'}, 'k', '2026-09-24T12:00:00Z'))
        report = self.warmer().run()
        self.assertEqual(report['status'], 'success', report)
        ids = [task_id for task_id, _ in self.backend.calls]
        self.assertEqual(len(ids), len(set(ids)))
        plan = prewarm.read_json(self.state.plan)
        self.assertTrue(plan['tasks'])
        self.assertTrue(all(task['state'] == 'warm' for task in plan['tasks']))
        rows = self.ledger()
        self.assertEqual(sorted(row['task_id'] for row in rows if row['outcome'] == 'succeeded'), sorted(ids))
        # The hint scope's result carried company options, so the neighbours were expanded in-run.
        self.assertIn('A2', [task['body'].get('company') for task in plan['tasks']])
        status = prewarm.status_report(self.state, today=self.backend.today_value)
        self.assertEqual(status['completion_rate'], 1.0)
        self.assertFalse(status['warmer']['running'])
        self.assertTrue(any(event[0] == 'mentat.prewarm.run' and event[1] == 'running' for event in self.events))
        self.assertTrue(any(event[0] == 'mentat.prewarm.task' and event[1] == 'completed' for event in self.events))
        self.assertTrue(any(event[0] == 'mentat.prewarm.run' and 'exit' in event[2].lower() for event in self.events))

    def test_already_warm_tasks_are_not_rerun(self):
        self.warmer().run()
        first = len(self.backend.calls)
        self.warmer().run()
        self.assertEqual(len(self.backend.calls), first)

    def test_low_memory_and_a_busy_console_pause_without_counting_as_failures(self):
        cases = [
            # The first reading is the pre-plan check (planning waits for the same floor); the second
            # is the first task's guard.
            ('low_memory', dict(free_bytes=iter([8 * GIB, 1 * GIB]).__next__), 'MB free physical memory'),
            ('console_busy', dict(console='http://127.0.0.1:1', console_probe=iter([(True, {})]).__next__),
             'console reports busy'),
            ('console_busy', dict(console='http://127.0.0.1:1', console_probe=iter([(None, 'refused')]).__next__),
             'did not answer /api/health'),
        ]
        for hint, overrides, detail in cases:
            with self.subTest(detail=detail):
                self.state = prewarm.StateDir(self.root / ('state-' + detail.replace(' ', '-').replace('/', '')))
                self.backend = FakeBackend(self.root)
                probe = overrides.pop('free_bytes', None) or overrides.pop('console_probe')
                if 'console' in overrides:
                    overrides['console_probe'] = lambda url, probe=probe: self._once(probe, (False, {}))
                else:
                    overrides['free_bytes'] = lambda probe=probe: self._once(probe, 8 * GIB)
                report = self.warmer(**overrides).run()
                self.assertEqual(report['status'], 'success')
                rows = self.ledger()
                paused = [row for row in rows if row['outcome'] == 'paused']
                self.assertEqual([row['root_cause_hint'] for row in paused], [hint])
                self.assertIn(detail, paused[0]['error'])
                self.assertEqual(paused[0]['retry'], 'retry after 60 s')
                self.assertIn('never quarantines', paused[0]['stop'])
                self.assertEqual([row['outcome'] for row in rows].count('resumed'), 1)
                self.assertFalse([row for row in rows if row['outcome'] == 'failed'])
                self.assertEqual(prewarm.consecutive_failures(rows), {})
                # 3 query views, 3 defaults and Alpha's 3 companies (every company of every industry,
                # planned once Alpha's default result names them).
                self.assertEqual(len(self.backend.calls), 9)

    @staticmethod
    def _once(source, then):
        try:
            return source()
        except StopIteration:
            return then

    def test_memory_threshold_is_one_and_a_half_gigabytes(self):
        warmer = self.warmer(free_bytes=lambda: prewarm.MIN_FREE_BYTES)
        self.assertIsNone(warmer.resource_guard())
        warmer = self.warmer(free_bytes=lambda: prewarm.MIN_FREE_BYTES - 1)
        self.assertEqual(warmer.resource_guard()[0], 'low_memory')
        self.assertEqual(prewarm.MIN_FREE_BYTES, int(1.5 * GIB))

    def test_parent_gone_exits_cleanly_and_releases_the_lock(self):
        alive = iter([True, True, True])
        report = self.warmer(parent_pid=999999, parent_alive=lambda: next(alive, False), start_delay=0).run()
        self.assertEqual(report['status'], 'success')
        self.assertEqual(report['metrics']['root_cause_hint'], 'parent_gone')
        self.assertIn('released_utc', prewarm.WarmerLock(self.state.lock).holder())
        lock = prewarm.WarmerLock(self.state.lock)
        self.addCleanup(lock.release)
        self.assertTrue(lock.acquire({'pid': 1})[0])

    def test_code_change_exits_after_the_current_task(self):
        stamps = {'value': {'prewarm.py': [1, 1]}}

        class Changing(FakeBackend):
            def warm(inner, task, **kwargs):
                info = FakeBackend.warm(inner, task, **kwargs)
                stamps['value'] = {'prewarm.py': [2, 2]}
                return info

        backend = Changing(self.root)
        report = self.warmer(backend, code_stamps=lambda: stamps['value']).run()
        self.assertEqual(report['metrics']['root_cause_hint'], 'code_changed')
        self.assertEqual(len(backend.calls), 1)

    def test_code_changed_raised_by_a_task_ends_the_run(self):
        self.backend.fail['query_view'] = ValueError('Insights code changed. Restart Mentat to load the current analysis rules.')
        report = self.warmer().run()
        self.assertEqual(report['metrics']['root_cause_hint'], 'code_changed')
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.ledger()[-1]['stop'], 'exit cleanly; the console relaunches the warmer with the new code')

    def test_three_consecutive_failures_quarantine_until_the_fingerprint_changes(self):
        failing = self.plan()['tasks'][4]    # insights_default for Beta (catalog order, no hints)
        self.assertEqual((failing['kind'], failing['dataset']), ('insights_default', 'Beta'))
        self.backend.fail[failing['id']] = ValueError('boom')
        report = self.warmer().run()
        self.assertEqual(report['status'], 'success')
        attempts = [retry for task_id, retry in self.backend.calls if task_id == failing['id']]
        self.assertEqual(attempts, [False, True, True])        # a retry asks like the client's Try again
        rows = [row for row in self.ledger() if row['task_id'] == failing['id']]
        self.assertEqual([row['outcome'] for row in rows], ['failed', 'failed', 'failed', 'quarantined'])
        self.assertEqual([row['consecutive_failures'] for row in rows[:3]], [1, 2, 3])
        self.assertTrue(all(row['root_cause_hint'] == 'unexpected_exception:ValueError' and row['retry'] and row['stop']
                            for row in rows[:3]))
        quarantine = prewarm.read_json(self.state.quarantine)
        self.assertEqual(quarantine[failing['id']]['source_fp'], 'fp-Beta')
        # A new warmer (a restart) keeps it quarantined: it is not attempted again.
        calls = len(self.backend.calls)
        self.warmer().run()
        self.assertEqual(len(self.backend.calls), calls)
        # The source fingerprint changes: the quarantine is released and the task runs again.
        self.backend.fail.clear()
        self.backend.source('Beta')['source_fp'] = 'fp-Beta-2'
        plan = self.plan(quarantine=prewarm.read_json(self.state.quarantine))
        self.assertIn(failing['id'], plan['released_quarantine'])
        self.warmer().run()
        self.assertIn((failing['id'], False), self.backend.calls[calls:])
        self.assertNotIn(failing['id'], prewarm.read_json(self.state.quarantine))

    def test_a_code_change_releases_the_quarantine_too(self):
        failing = self.plan()['tasks'][0]
        quarantine = {failing['id']: {'source_fp': failing['source_fp'], 'code_fp': 'code-1'}}
        self.assertEqual(self.plan(quarantine=quarantine)['tasks'][0]['state'], 'quarantined')
        self.assertIn(failing['id'], self.plan(quarantine=quarantine, code_fp='code-2')['released_quarantine'])

    def test_date_rollover_rewarms_insights_but_not_query_views(self):
        self.warmer().run()
        first = {task_id for task_id, _ in self.backend.calls}
        self.backend.today_value = next_day(self.backend.today_value)
        self.backend.calls.clear()
        self.warmer().run()
        again = {task_id for task_id, _ in self.backend.calls}
        self.assertTrue(again)
        self.assertTrue(all(task_id.startswith('insights_') for task_id in again))
        self.assertEqual(again, {task_id for task_id in first if task_id.startswith('insights_')})

    def test_the_date_changing_mid_run_triggers_a_replan(self):
        warmer = self.warmer()
        warmer.replan('start')
        self.assertIsNone(warmer.replan_trigger())
        self.backend.today_value = next_day(self.backend.today_value)
        self.assertEqual(warmer.replan_trigger(), 'date_changed')

    def test_a_new_newest_hint_triggers_a_replan(self):
        clock = Clock()
        warmer = self.warmer(clock=clock)
        warmer.replan('start')
        write_hints(self.state, ({'dataset': 'Gamma', 'basis': 'receipt', 'panels': CORE}, 'k', '2026-09-24T12:00:00Z'))
        clock.sleep(prewarm.HINTS_DEBOUNCE_SECONDS + 1)
        self.assertEqual(warmer.replan_trigger(), 'hints_changed')

    def test_a_source_change_is_seen(self):
        warmer = self.warmer()
        warmer.replan('start')
        self.assertFalse(warmer.sources_changed())
        self.backend.source('Alpha')['source_fp'] = 'fp-Alpha-2'
        self.assertTrue(warmer.sources_changed())


class RecoveryTests(unittest.TestCase):
    def test_root_cause_table(self):
        class Locked(OSError):
            winerror = 32
        cases = [
            (MemoryError(), 'low_memory'),
            (ValueError('Insights code changed. Restart Mentat to load the current analysis rules.'), 'code_changed'),
            (ValueError('Accounting code changed. Restart the app to load the current accounting rules.'), 'code_changed'),
            (ValueError('Field definitions changed. Restart the app to load current semantics.'), 'code_changed'),
            (ValueError(prewarm.INDEX_REFUSED), 'index_missing'),
            (prewarm.IndexMissing('x'), 'index_missing'),
            (ValueError('Contribution source changed while querying. Retry against the current source.'), 'source_changed_during_build'),
            (ValueError('An Insights source has changed or has active writes. Retry after collection is stable.'), 'source_changed_during_build'),
            (ValueError('The recovered source has active uncheckpointed writes.'), 'source_changed_during_build'),
            (ValueError('Source lineage changed during verification. Retry against stable source files.'), 'source_changed_during_build'),
            (sqlite3.OperationalError('database is locked'), 'lock_held_by_other_process'),
            (Locked('in use'), 'lock_held_by_other_process'),
            (prewarm.LockHeld('x'), 'lock_held_by_other_process'),
            (prewarm.Stop('parent_gone', 'x'), 'parent_gone'),
            (ValueError('The pinned source database changed; reconcile its lineage before querying.'), 'unexpected_exception:ValueError'),
            (KeyError('x'), 'unexpected_exception:KeyError'),
        ]
        for error, expected in cases:
            self.assertEqual(prewarm.classify(error), expected, str(error))

    def test_every_hint_has_a_safe_retry_and_a_stop(self):
        for hint in (*prewarm.ROOT_CAUSES, 'unexpected_exception:ValueError'):
            retry, stop = prewarm.recovery(hint, 1)
            self.assertTrue(retry and stop, hint)
        self.assertEqual(prewarm.recovery('index_missing')[0], 'do not retry until the source fingerprint changes')
        self.assertEqual(prewarm.recovery('source_changed_during_build', 1)[0], 'retry once now')
        self.assertEqual(prewarm.recovery('source_changed_during_build', 2)[0], 'retry after 60 s')
        self.assertIn('exit cleanly', prewarm.recovery('code_changed')[1])
        self.assertIn('exit cleanly', prewarm.recovery('parent_gone')[1])
        self.assertIn('quarantine', prewarm.recovery('unexpected_exception:KeyError', 2)[1])
        self.assertEqual(prewarm.retry_delay('retry once now'), 0.0)
        self.assertEqual(prewarm.retry_delay('retry after 60 s'), 60.0)
        self.assertIsNone(prewarm.retry_delay('do not retry'))


class ObservationTests(Harness):
    KEYS = {'schema', 'ts_utc', 'run_id', 'pid', 'op', 'task_id', 'kind', 'dataset', 'outcome', 'attempt',
            'consecutive_failures', 'seconds', 'root_cause_hint', 'retry', 'stop', 'error', 'key', 'response_path',
            'as_of', 'source_fp', 'code_fp', 'polls', 'statuses', 'free_mb', 'detail'}

    def test_every_observation_has_the_contract_shape(self):
        self.backend.fail['query_view'] = ValueError('Contribution source changed while querying. Retry against the current source.')
        self.warmer().run()
        rows = self.ledger()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), self.KEYS)
            json.dumps(row)
        failures = [row for row in rows if row['outcome'] in ('failed', 'paused', 'quarantined')]
        self.assertTrue(failures)
        for row in failures:
            hint = row['root_cause_hint']
            self.assertTrue(hint in prewarm.ROOT_CAUSES or hint.startswith('unexpected_exception:'), hint)
            self.assertTrue(row['retry'])
            self.assertTrue(row['stop'])
        first = next(row for row in rows if row['outcome'] == 'failed')
        self.assertEqual((first['root_cause_hint'], first['retry']), ('source_changed_during_build', 'retry once now'))

    def test_ledger_rotates_at_its_limit(self):
        ledger = prewarm.Ledger(self.state, limit=2000)
        for index in range(60):
            ledger.append({'task_id': f't{index}', 'outcome': 'succeeded', 'padding': 'x' * 40})
        self.assertTrue(self.state.ledger_old.exists())
        self.assertLessEqual(self.state.ledger.stat().st_size, 2000)
        rows = ledger.read()
        self.assertEqual(rows[-1]['task_id'], 't59')
        self.assertGreater(len(rows), 1)


class LockTests(Harness):
    def test_single_instance_and_clean_release(self):
        first, second = prewarm.WarmerLock(self.state.lock), prewarm.WarmerLock(self.state.lock)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertEqual(first.acquire({'pid': 1111, 'n': 1}), (True, None))
        acquired, holder = second.acquire({'pid': 2222, 'n': 2})
        self.assertFalse(acquired)
        self.assertEqual(holder['n'], 1)
        first.release()
        acquired, stale = second.acquire({'pid': 2222, 'n': 2})
        self.assertTrue(acquired)
        self.assertIsNone(stale)          # a clean release (by pid 1111) is not a stale lock
        second.release()

    def test_a_dead_holder_is_stale_and_taken_over(self):
        dead = exited_pid()
        self.state.ensure()
        self.state.lock.write_text(json.dumps({'pid': dead, 'started_utc': 'x'}), encoding='utf-8')
        self.assertFalse(prewarm.pid_alive(dead))
        lock = prewarm.WarmerLock(self.state.lock)
        self.addCleanup(lock.release)
        acquired, stale = lock.acquire({'pid': os.getpid()})
        self.assertTrue(acquired)
        self.assertEqual(stale['pid'], dead)

    def test_a_live_holder_in_another_process_keeps_the_lock_until_it_dies(self):
        script = ('import sys, time; sys.path.insert(0, sys.argv[1]); import importlib.util as u;'
                  'spec = u.spec_from_file_location("prewarm", sys.argv[2]); m = u.module_from_spec(spec); spec.loader.exec_module(m);'
                  'lock = m.WarmerLock(sys.argv[3]); print(lock.acquire({"pid": __import__("os").getpid()})[0], flush=True); time.sleep(120)')
        child, out = spawn([sys.executable, '-c', script, str(APP), str(PREWARM_FILE), str(self.state.lock)])
        try:
            self.assertEqual(first_line(out), 'True')
            acquired, holder = prewarm.WarmerLock(self.state.lock).acquire({'pid': os.getpid()})
            self.assertFalse(acquired)
            self.assertEqual(holder['pid'], child.pid)
            report = self.warmer().run()
            self.assertEqual(report['status'], 'warning')
            self.assertEqual(self.backend.calls, [])
        finally:
            stop(child, out)
        lock = prewarm.WarmerLock(self.state.lock)
        self.addCleanup(lock.release)
        acquired, stale = lock.acquire({'pid': os.getpid()})
        self.assertTrue(acquired)
        self.assertEqual(stale['pid'], child.pid)


    def test_a_new_console_waits_for_the_orphaned_warmer_of_the_old_one(self):
        # The old warmer's console is gone (a dead parent pid) but it still holds the lock, finishing a
        # build; the new warmer waits for it instead of exiting and leaving the new console unwarmed.
        dead_parent = exited_pid()
        script = ('import os, sys, time; import importlib.util as u;'
                  'spec = u.spec_from_file_location("prewarm", sys.argv[1]); m = u.module_from_spec(spec); spec.loader.exec_module(m);'
                  'lock = m.WarmerLock(sys.argv[2]);'
                  'print(lock.acquire({"pid": os.getpid(), "parent_pid": int(sys.argv[3]), "parent_started": 1})[0], flush=True);'
                  'time.sleep(120)')
        orphan, out = spawn([sys.executable, '-c', script, str(PREWARM_FILE), str(self.state.lock), str(dead_parent)])
        self.addCleanup(stop, orphan, out)
        self.assertEqual(first_line(out), 'True')
        timer = threading.Timer(1.5, kill_tree, args=(orphan,))
        timer.start()
        self.addCleanup(timer.cancel)
        clock = Clock()

        def sleep(seconds):
            time.sleep(0.05)
            clock.sleep(seconds)

        report = self.warmer(clock=clock, sleep=sleep).run()
        self.assertEqual(report['status'], 'success', report)
        self.assertTrue(self.backend.calls)


class StatusTests(Harness):
    def test_status_metrics(self):
        write_hints(self.state, ({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE}, 'k', '2026-09-24T12:00:00Z'))
        failing = self.plan()['tasks'][4]
        self.backend.fail[failing['id']] = KeyError('missing')
        self.warmer().run()
        metrics = prewarm.status_report(self.state, today=self.backend.today_value)
        planned = metrics['tasks_planned']
        self.assertEqual(metrics['tasks_warm'], planned - 1)
        self.assertEqual(metrics['completion_rate'], round((planned - 1) / planned, 4))
        self.assertEqual([item['task_id'] for item in metrics['quarantined']], [failing['id']])
        self.assertEqual(metrics['retries_per_task']['max'], 3)
        self.assertEqual(metrics['retries_per_task']['by_task'], {failing['id']: 3})
        self.assertEqual(metrics['seconds_per_success']['current_plan']['count'], planned - 1)
        self.assertEqual(len(metrics['last_errors']), 4)
        self.assertEqual(metrics['last_errors'][0]['outcome'], 'quarantined')
        self.assertEqual(metrics['per_industry'][failing['dataset']]['insights_default'], 'quarantined')
        self.assertEqual(metrics['per_industry']['Alpha']['query_view'], 'warm')
        self.assertEqual(metrics['next_tasks'], [])
        prewarm.atomic_write_json(self.state.verify, {'pass_at_1': 0.75, 'created_utc': 'x'})
        self.assertEqual(prewarm.status_report(self.state, today=self.backend.today_value)['pass_at_1'], 0.75)
        self.assertTrue(prewarm.status_report(self.state, today=next_day(self.backend.today_value))['plan_stale'])


class CliTests(Harness):
    def cli(self, *arguments):
        code, out, err = run_bounded([sys.executable, str(PREWARM_FILE), *arguments], 120,
                                     env={**os.environ, 'GLASSBOX_EVENTS_DIR': str(self.root / 'events')})
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, out + err[-2000:])
        report = json.loads(lines[0])
        self.assertEqual(set(report), {'status', 'summary', 'next_actions', 'artifacts', 'metrics'})
        self.assertEqual(code, {'success': 0, 'warning': 1, 'error': 2}[report['status']])
        return report

    def test_every_op_prints_one_json_object(self):
        self.assertEqual(self.cli('status', '--state-root', str(self.root / 'scratch'))['status'], 'warning')
        refused = self.cli('bench', '--base', 'http://127.0.0.1:8806', '--state-root', str(self.root / 'scratch'))
        self.assertEqual(refused['status'], 'error')
        self.assertIn('8806', refused['summary'])
        self.assertEqual(self.cli('warm', '--state-root', str(self.root / 'scratch'))['status'], 'error')


class GlassboxTests(unittest.TestCase):
    def test_event_files_match_the_stream_format(self):
        with tempfile.TemporaryDirectory() as directory:
            first = glassbox_emit.emit('mentat.prewarm.task', 'running', 'Start x', run_id='r', current=1, total=3,
                                       events_dir=directory)
            second = glassbox_emit.emit('mentat.prewarm.task', 'completed', 'Done x', run_id='r', events_dir=directory)
            names = sorted(path.name for path in Path(directory).iterdir())
            self.assertEqual(len(names), 2)
            self.assertNotEqual(first, second)
            pattern = re.compile(r'^evt-\d{8}T\d{6}\d{7}Z-([0-9a-f]{10})\.json$')
            for name in names:
                match = pattern.match(name)
                self.assertTrue(match, name)
                self.assertTrue(match.group(1).startswith(f'{os.getpid() & 0xFFFFFF:06x}'))
            raw = (Path(directory) / (first + '.json')).read_bytes()
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
            event = json.loads(raw)
            self.assertEqual(event['schema_version'], 1)
            self.assertEqual((event['lane'], event['source'], event['kind'], event['status']),
                             ('claude', 'mentat-prewarm', 'mentat.prewarm.task', 'running'))
            self.assertEqual((event['current'], event['total']), (1, 3))
            for key in ('id', 'timestamp_utc', 'run_id', 'correlation_id', 'parent_id', 'stage', 'summary', 'detail',
                        'cwd', 'duration_ms', 'bytes', 'metadata'):
                self.assertIn(key, event)
            later = json.loads((Path(directory) / (second + '.json')).read_bytes())
            self.assertNotIn('current', later)
            self.assertNotIn('total', later)

    def test_emission_failure_never_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / 'file'
            blocker.write_text('x')
            self.assertIsNone(glassbox_emit.emit('k', 'info', 's', events_dir=blocker / 'events'))


class ConsoleEntryPointTests(Harness):
    def test_startup_warm_is_idempotent_never_raises_and_emits(self):
        class App:
            report_directory = self.root / 'reports'

            def published_reports(self):
                raise RuntimeError('reports unavailable')

        (self.root / 'reports').mkdir()
        emitted = []
        with mock.patch.dict(prewarm._startup, {'thread': None, 'state': 'not started', 'steps': [], 'errors': []}), \
                mock.patch.object(prewarm, 'emit', side_effect=lambda *a, **k: emitted.append(a)):
            thread = prewarm.start_startup_warm(read_only=True, app=App())
            self.assertIs(prewarm.start_startup_warm(read_only=False, app=None), thread)
            thread.join(timeout=60)
            self.assertFalse(thread.is_alive())
            status = prewarm.startup_warm_status()
            self.assertIn(status['state'], ('done', 'failed'))
            self.assertTrue(any(error['step'] == 'published_reports' for error in status['errors']))
            self.assertTrue(any('Startup warm' in args[2] for args in emitted))

    def test_launch_warmer_is_never_used_by_a_read_only_console(self):
        launched = []
        with mock.patch.object(prewarm.subprocess, 'Popen', side_effect=lambda *a, **k: launched.append(a)),                 mock.patch.object(prewarm, 'emit'):
            result = prewarm.launch_warmer(data_root=self.root, read_only=True, console_port=8899,
                                           state_root=self.root / 'state')
        self.assertIsNone(result)
        self.assertEqual(launched, [])
        self.assertFalse((self.root / 'state' / 'prewarm').exists())

    def test_launch_warmer_uses_real_file_handles_and_below_normal_priority(self):
        seen = {}

        class FakePopen:
            def __init__(self, command, **kwargs):
                seen['command'], seen['kwargs'] = command, kwargs
                seen['stdout_fileno'] = kwargs['stdout'].fileno()
                self.pid = 4242

        with mock.patch.object(prewarm.subprocess, 'Popen', FakePopen), mock.patch.object(prewarm, 'emit'):
            pid = prewarm.launch_warmer(data_root=self.root / 'data', read_only=False, console_port=8899,
                                        state_root=self.root / 'state')
        self.assertEqual(pid, 4242)
        # The warmer is one process in this interpreter's environment (python_child): under Mentat's venv the
        # venv's base interpreter, with __PYVENV_LAUNCHER__ naming sys.executable.
        executable, environment = python_child.command()
        if environment is None:
            self.assertIsNone(seen['kwargs']['env'])
        else:
            self.assertEqual(seen['kwargs']['env'][python_child.LAUNCHER_VARIABLE], sys.executable)
        self.assertEqual(seen['command'][:9], [executable, str(PREWARM_FILE.resolve()), 'run', '--data-root',
                                               str(self.root / 'data'), '--parent-pid', str(os.getpid()),
                                               '--console', 'http://127.0.0.1:8899'])
        self.assertEqual(seen['kwargs']['cwd'], os.getcwd())
        self.assertIs(seen['kwargs']['stdout'], seen['kwargs']['stderr'])
        self.assertIsInstance(seen['stdout_fileno'], int)
        if os.name == 'nt':
            self.assertEqual(seen['kwargs']['creationflags'], 0x00004000 | 0x08000000)
        self.assertTrue((self.root / 'state' / 'prewarm' / f'warmer-{os.getpid()}.log').exists())


class ResultCodeTests(unittest.TestCase):
    """P2-7: collection_gaps.py (the Graph payload's gaps) and evidence_csv.py (the Conduit label and the
    category vocabulary) decide warmed results, so a change to either re-plans like any other result code."""

    def test_the_gap_and_label_modules_are_result_code(self):
        self.assertLessEqual({'collection_gaps.py', 'evidence_csv.py'}, set(prewarm.RESULT_CODE))
        for name in ('collection_gaps.py', 'evidence_csv.py'):
            self.assertTrue((APP / name).is_file(), name)

    def test_the_code_fingerprint_moves_when_either_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in prewarm.RESULT_CODE:
                (Path(folder) / name).write_text('x', encoding='utf-8')
            for name in ('collection_gaps.py', 'evidence_csv.py'):
                with self.subTest(name):
                    before = prewarm.code_fingerprint(app_dir=folder)
                    (Path(folder) / name).write_text('a changed module', encoding='utf-8')
                    self.assertNotEqual(prewarm.code_fingerprint(app_dir=folder), before)


class VerifyHelperTests(Harness):
    def test_only_the_journals_own_clock_and_counters_are_ignored(self):
        payload = {'history': {'inspected_at': 'a', 'newly_inspected_versions': 3, 'observations_inspected': 9},
                   'source_history': [{'first_seen': 'x'}], 'panels': {'favorites': {'rows': [{'amount_cents': 1}]}}}
        stripped = prewarm.strip_volatile(payload)
        self.assertEqual(stripped, {'history': {'observations_inspected': 9},
                                    'panels': {'favorites': {'rows': [{'amount_cents': 1}]}}})
        self.assertIn('inspected_at', payload['history'])      # the input is not mutated

    def test_first_difference_names_the_json_path(self):
        left = {'panels': {'favorites': {'rows': [{'amount_cents': 1}, {'amount_cents': 2}]}}, 'a': 1}
        right = copy.deepcopy(left)
        self.assertIsNone(prewarm.first_difference(left, right))
        right['panels']['favorites']['rows'][1]['amount_cents'] = 3
        self.assertEqual(prewarm.first_difference(left, right), '$.panels.favorites.rows[1].amount_cents')
        self.assertEqual(prewarm.first_difference({'a': 1}, {'a': 1, 'b': 2}), '$.b')
        self.assertEqual(prewarm.first_difference([1], [1, 2]), '$[1]')
        self.assertEqual(prewarm.first_difference({'a': '1'}, {'a': 1}), '$.a')

    def test_a_tampered_cached_payload_is_refused(self):
        path = self.root / 'r.json'
        payload = json.dumps({'status': 'ready'})
        import hashlib
        path.write_text(json.dumps({'key': 'k', 'sha256': hashlib.sha256(payload.encode()).hexdigest(), 'payload': payload}))
        self.assertEqual(prewarm.read_cached_payload(path), ('k', {'status': 'ready'}))
        path.write_text(json.dumps({'key': 'k', 'sha256': '0' * 64, 'payload': payload}))
        with self.assertRaises(ValueError):
            prewarm.read_cached_payload(path)

    def test_samples_are_todays_warmed_insights_round_robin_over_industries(self):
        rows = []
        for dataset in ('A', 'B'):
            for index in range(3):
                rows.append({'outcome': 'succeeded', 'kind': 'insights_neighbor', 'task_id': f'{dataset}{index}',
                             'dataset': dataset, 'as_of': '2026-09-24', 'key': 'k', 'response_path': 'p'})
        rows.append({'outcome': 'succeeded', 'kind': 'query_view', 'task_id': 'q', 'dataset': 'A', 'as_of': None,
                     'key': 'k', 'response_path': 'p'})
        # Yesterday's scope sorts first, so only the as_of rule keeps it out of the sample.
        rows.append({'outcome': 'succeeded', 'kind': 'insights_default', 'task_id': 'A-old', 'dataset': 'A',
                     'as_of': '2026-09-23', 'key': 'k', 'response_path': 'p'})
        chosen = prewarm.verify_samples(rows, '2026-09-24', 3)
        self.assertEqual([row['task_id'] for row in chosen], ['A0', 'B0', 'A1'])
        self.assertEqual([row['task_id'] for row in prewarm.verify_samples(rows, '2026-09-24', 5, {'B2'})], ['B2'])


if __name__ == '__main__':
    unittest.main()
