"""The prewarm harness end to end on real data with scratch state: plan -> run -> ledger -> status ->
verify for Community Banks, and a launched warmer that exits when its console is gone.

Opt-in (it reads the configured report folder and the real analytical index, read-only, and takes
about two minutes):  set MENTAT_PREWARM_INTEGRATION=1, then from mentat/tests
    python -m unittest test_prewarm_integration -v
Every harness process runs with a scratch --state-root under the system temp folder: all app state
is redirected there, and an audit hook refuses (and logs to <scratch>/prewarm-write-refusals.log)
any write to the Mentat checkout, the report folder or the console data root. The test requires that
log to stay empty, which is the proof the run changed nothing of Paul's state.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
APP = HERE.parent / 'app'
CHECKOUT = APP.parents[1]
PREWARM = Path(os.environ.get('PREWARM_UNDER_TEST') or APP / 'prewarm.py')
ENABLED = os.environ.get('MENTAT_PREWARM_INTEGRATION') == '1'
INDUSTRY = 'Community Banks'
# graph.js sparkRequest()'s return value, written out here independently of prewarm's builder.
SPARK_FIXED = {'basis': 'receipt', 'dimensions': ['company', 'quarter'], 'measures': ['amount'], 'aggregate_filters': [],
               'sort': [{'field': 'quarter', 'direction': 'asc'}], 'limit': 100000, 'offset': 0}
LEDGER_KEYS = {'schema', 'ts_utc', 'run_id', 'pid', 'op', 'task_id', 'kind', 'dataset', 'outcome', 'attempt',
               'consecutive_failures', 'seconds', 'root_cause_hint', 'retry', 'stop', 'error', 'key', 'response_path',
               'as_of', 'source_fp', 'code_fp', 'polls', 'statuses', 'free_mb', 'detail'}


def run_bounded(arguments, timeout, cwd=None, env=None):
    """A child with no pipes (DE-026): both streams on real temporary files, wait(timeout), and on an
    overrun the child's whole tree is killed before TimeoutExpired propagates."""
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



def kill_pid(pid):
    """Stop a process tree by pid (bounded), when it is still running."""
    if os.name != 'nt':
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        return
    killer = subprocess.Popen(['taskkill', '/F', '/T', '/PID', str(pid)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        killer.wait(timeout=30)
    except subprocess.TimeoutExpired:
        killer.kill()


@unittest.skipUnless(ENABLED, 'set MENTAT_PREWARM_INTEGRATION=1 (reads the real report folder and analytical index)')
class ScratchStateIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='prewarm-integration-', ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.scratch = Path(self._tmp.name) / 'state'
        self.events = Path(self._tmp.name) / 'events'

    def cli(self, *arguments, timeout=1200):
        code, out, err = run_bounded([sys.executable, str(PREWARM), *arguments, '--state-root', str(self.scratch)],
                                     timeout, cwd=str(CHECKOUT), env={**os.environ, 'GLASSBOX_EVENTS_DIR': str(self.events)})
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, out + err[-3000:])
        report = json.loads(lines[0])
        self.assertEqual(code, {'success': 0, 'warning': 1, 'error': 2}[report['status']], report)
        return report

    def refusals(self):
        log = self.scratch / 'prewarm-write-refusals.log'
        return log.read_text(encoding='utf-8') if log.exists() else ''

    def test_plan_warm_ledger_status_verify_on_scratch_state(self):
        plan = self.cli('plan', '--industries', INDUSTRY)
        self.assertEqual(plan['status'], 'success', plan)
        self.assertEqual(plan['metrics']['order'], [INDUSTRY])
        self.assertTrue(plan['metrics']['sources'][INDUSTRY]['index_present'])
        stored = json.loads((self.scratch / 'prewarm' / 'plan.json').read_text(encoding='utf-8'))
        spark = next(task['body'] for task in stored['tasks'] if task['kind'] == 'query_view')
        self.assertEqual({key: spark[key] for key in SPARK_FIXED}, SPARK_FIXED)
        self.assertEqual(spark['dataset'], INDUSTRY)
        cycle = [item for item in spark['filters'] if item['field'] == 'cycle']
        self.assertEqual(len(cycle), 1)
        start, end = map(int, re.fullmatch(r'(\d{4})-(\d{4})', cycle[0]['value']).groups())
        self.assertEqual((end - start, end % 2), (1, 0))
        default = next(task['body'] for task in stored['tasks'] if task['kind'] == 'insights_default')
        self.assertEqual(default, {'dataset': INDUSTRY, 'basis': 'receipt', 'panels': ['favorites', 'trends', 'changes']})

        first = self.cli('run', '--once', '--industries', INDUSTRY)
        self.assertEqual(first['status'], 'success', first)
        options = json.loads((self.scratch / 'prewarm' / 'options.json').read_text(encoding='utf-8'))
        companies = options[INDUSTRY]['receipt']['companies']
        self.assertGreaterEqual(len(companies), 2)

        # The console records the user's last scope; here, a company scope seen now.
        hint = {'dataset': INDUSTRY, 'basis': 'receipt', 'panels': ['favorites', 'trends', 'changes'], 'company': companies[1]}
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        hints = {'version': 1, 'updated_utc': now, 'requests': [{'body': hint, 'key': None, 'dataset': INDUSTRY, 'seen_utc': now}]}
        (self.scratch / 'prewarm' / 'hints.json').write_text(json.dumps(hints), encoding='utf-8')
        second = self.cli('run', '--once', '--industries', INDUSTRY, '--max-neighbors', '3')
        self.assertEqual(second['status'], 'success', second)

        rows = [json.loads(line) for line in (self.scratch / 'prewarm' / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), LEDGER_KEYS)
        self.assertEqual([row for row in rows if row['outcome'] in ('failed', 'quarantined')], [])
        successes = {row['task_id']: row for row in rows if row['outcome'] == 'succeeded'}
        final = json.loads((self.scratch / 'prewarm' / 'plan.json').read_text(encoding='utf-8'))
        neighbours = [task for task in final['tasks'] if task['kind'] == 'insights_neighbor']
        self.assertEqual(len(neighbours), 1 + 3 + 3)       # the hint's scope, 3 companies, 3 other bases
        self.assertTrue(all(task['id'] in successes for task in final['tasks']))
        for task in final['tasks']:
            self.assertTrue(Path(successes[task['id']]['response_path']).is_file(), task['id'])
        # The console would have written the hint's key; take it from the warm of that exact body.
        scope = next(task for task in neighbours if task['body'] == hint)
        hints['requests'][0]['key'] = successes[scope['id']]['key']
        (self.scratch / 'prewarm' / 'hints.json').write_text(json.dumps(hints), encoding='utf-8')

        status = self.cli('status')
        metrics = status['metrics']
        self.assertEqual(metrics['completion_rate'], 1.0, metrics)
        self.assertEqual(metrics['quarantined'], [])
        self.assertEqual(metrics['per_industry'][INDUSTRY]['query_view'], 'warm')
        self.assertEqual(metrics['per_industry'][INDUSTRY]['insights_default'], 'warm')
        self.assertEqual(metrics['per_industry'][INDUSTRY]['neighbors_warm'], 7)

        verify = self.cli('verify', '--sample', '3')
        self.assertEqual(verify['status'], 'success', verify)
        self.assertEqual(verify['metrics']['pass_at_1'], 1.0)
        self.assertEqual(verify['metrics']['key_mismatches'], 0)
        report = json.loads((self.scratch / 'prewarm' / 'verify.json').read_text(encoding='utf-8'))
        self.assertTrue(report['client_contract']['ok'])
        checks = [check for check in report['key_checks'] if check.get('match') is not None]
        self.assertTrue(any(check['check'] == 'hint' and check['match'] for check in checks))
        self.assertTrue(any(check['check'] == 'insights.js default' and check['match'] for check in checks))
        self.assertEqual(self.cli('status')['metrics']['pass_at_1'], 1.0)
        self.assertEqual(self.refusals(), '')
        emitted = [json.loads(path.read_text(encoding='utf-8')) for path in self.events.glob('evt-*.json')]
        kinds = {event['kind'] for event in emitted}
        self.assertTrue({'mentat.prewarm.run', 'mentat.prewarm.task', 'mentat.prewarm.plan', 'mentat.prewarm.verify'} <= kinds, kinds)
        self.assertTrue(all(event['source'] == 'mentat-prewarm' and event['lane'] == 'claude' for event in emitted))

    def test_a_launched_warmer_exits_when_its_console_is_gone(self):
        script = ('import importlib.util, sys; spec = importlib.util.spec_from_file_location("prewarm", sys.argv[1]);'
                  'm = importlib.util.module_from_spec(spec); sys.modules["prewarm"] = m; spec.loader.exec_module(m);'
                  'print(m.launch_warmer(data_root=sys.argv[2], read_only=False, console_port=1, state_root=sys.argv[3]), flush=True)')
        data_root = Path(self._tmp.name) / 'console-data'
        data_root.mkdir()
        code, out, err = run_bounded([sys.executable, '-c', script, str(PREWARM), str(data_root), str(self.scratch)], 120,
                                     cwd=str(CHECKOUT), env={**os.environ, 'GLASSBOX_EVENTS_DIR': str(self.events)})
        self.assertEqual(code, 0, err[-2000:])
        warmer = int(out.strip().splitlines()[-1])
        self.addCleanup(kill_pid, warmer)           # a warmer that outlives the test is stopped, never left behind
        sys.path.insert(0, str(APP))
        import prewarm
        deadline = time.monotonic() + 120
        while prewarm.pid_alive(warmer) and time.monotonic() < deadline:
            time.sleep(1)
        self.assertFalse(prewarm.pid_alive(warmer), 'the warmer outlived its console')
        logs = list((self.scratch / 'prewarm').glob('warmer-*.log'))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text(encoding='utf-8', errors='replace')
        final = json.loads([line for line in text.splitlines() if line.startswith('{')][-1])
        self.assertEqual(final['status'], 'success', text[-2000:])
        self.assertEqual(final['metrics']['root_cause_hint'], 'parent_gone')
        lock = json.loads((self.scratch / 'prewarm' / 'warmer.lock').read_text(encoding='utf-8'))
        self.assertEqual(lock['pid'], warmer)
        self.assertIn('released_utc', lock)
        self.assertEqual(self.refusals(), '')


if __name__ == '__main__':
    unittest.main()
