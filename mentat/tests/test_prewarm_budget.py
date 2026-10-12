"""The wider warm plan: every company of every industry, Graph company sparklines, the other builders'
caches; and the response-cache budget that keeps warm entries from ever pushing out a response a click
stored (contribution_query_store._prune: newest 128 files / 128 MB per namespace)."""
import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_prewarm as base  # noqa: E402  (loads prewarm from PREWARM_UNDER_TEST or the app folder)

prewarm = base.prewarm
CORE = base.CORE
TODAY = '2026-09-24'
YESTERDAY = '2026-09-23'


def user_mtime(minutes_ago):
    return int((time.time() - minutes_ago * 60) * 1e9)


class Namespace:
    """A response folder with files of chosen mtimes (click responses or demoted warm entries)."""

    def __init__(self, root, name='insights'):
        self.dir = Path(root) / 'responses' / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def add(self, mtime_ns, size=100):
        self.count += 1
        path = self.dir / f'{self.count:04d}.json'
        path.write_bytes(b'x' * size)
        os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def files(self):
        return prewarm.response_files(self.dir)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ns = Namespace(self._tmp.name)

    def test_room_is_the_free_slots_less_one_for_a_concurrent_click(self):
        for index in range(100):
            self.ns.add(user_mtime(index))
        self.assertEqual(prewarm.warm_capacity(self.ns.files(), TODAY), 128 - 100 - 1)

    def test_a_full_namespace_of_click_responses_has_no_room(self):
        for index in range(127):
            self.ns.add(user_mtime(index))
        self.assertEqual(prewarm.warm_capacity(self.ns.files(), TODAY), 0)

    def test_earlier_days_warm_entries_are_recycled_but_not_todays(self):
        for index in range(120):
            self.ns.add(user_mtime(index))
        for position in range(5):
            self.ns.add(prewarm.demoted_mtime_ns(YESTERDAY, position))
        for position in range(3):
            self.ns.add(prewarm.demoted_mtime_ns(TODAY, position))
        # 128 files: 7 clicks' slots are gone; 5 of yesterday's warm entries may be pushed out, never today's.
        self.assertEqual(prewarm.warm_capacity(self.ns.files(), TODAY), 5 - 1)
        self.assertEqual(prewarm.warm_capacity(self.ns.files(), '2026-09-25'), 8 - 1)

    def test_the_byte_cap_counts_too(self):
        for index in range(10):
            self.ns.add(user_mtime(index), size=12 * 1024 * 1024)
        # 120 MB of click responses: 8 MB left, one 0.5-MB slot is the concurrent click's.
        self.assertEqual(prewarm.warm_capacity(self.ns.files(), TODAY, keep_bytes=128 * 1024 * 1024), 0)
        small = Namespace(self._tmp.name, 'small')
        for index in range(10):
            small.add(user_mtime(index), size=1024)
        self.assertEqual(prewarm.warm_capacity(small.files(), TODAY, keep_bytes=4 * 1024 * 1024), 6)

    def test_every_warm_write_the_capacity_allows_prunes_only_earlier_warm_entries(self):
        clicks = [self.ns.add(user_mtime(index)) for index in range(110)]
        old = [self.ns.add(prewarm.demoted_mtime_ns(YESTERDAY, position)) for position in range(15)]
        allowed = prewarm.warm_capacity(self.ns.files(), TODAY)
        for position in range(allowed):             # what store._prune does after each warm write
            path = self.ns.add(time.time_ns())
            prune(self.ns.dir)
            prewarm.demote([path], TODAY, position)
        self.assertTrue(all(path.exists() for path in clicks))
        self.assertEqual(prewarm.warm_capacity(self.ns.files(), TODAY), 0)
        extra = self.ns.add(time.time_ns())          # one more click after the warmer stopped
        prune(self.ns.dir)
        self.assertTrue(all(path.exists() for path in clicks) and extra.exists())

    def test_demoted_mtimes_order_by_day_then_priority_and_decode(self):
        first = prewarm.demoted_mtime_ns(TODAY, 0)
        later = prewarm.demoted_mtime_ns(TODAY, 57)
        older = prewarm.demoted_mtime_ns(YESTERDAY, 0)
        self.assertGreater(first, later)
        self.assertGreater(later, older)
        self.assertEqual(prewarm.demoted_day(first), TODAY)
        self.assertEqual(prewarm.demoted_day(older), YESTERDAY)
        self.assertIsNone(prewarm.demoted_day(time.time_ns()))
        self.assertLess(first, user_mtime(60 * 24 * 365 * 20))   # older than any real file

    def test_demote_then_promote_round_trip(self):
        path = self.ns.add(time.time_ns())
        self.assertEqual(prewarm.demote([path], TODAY, 3), [str(path)])
        self.assertEqual(prewarm.demote([path], TODAY, 3), [])   # already demoted
        self.assertEqual(prewarm.demoted_day(path.stat().st_mtime_ns), TODAY)
        seen = '2026-09-24T18:49:38.607415+00:00'
        self.assertEqual(prewarm.promote([(path, seen)]), [str(path)])
        self.assertIsNone(prewarm.demoted_day(path.stat().st_mtime_ns))
        self.assertEqual(path.stat().st_mtime_ns // 1000, prewarm._utc_ns(seen) // 1000)
        self.assertEqual(prewarm.promote([(path, seen)]), [])


def prune(directory):
    """contribution_query_store._prune, verbatim in effect: newest 128 and 128 MB kept."""
    files = sorted(((p.stat().st_mtime_ns, p.stat().st_size, p) for p in Path(directory).glob('*.json')), reverse=True)
    total = 0
    for index, (_, size, path) in enumerate(files):
        total += size
        if index >= 128 or total > 128 * 1024 * 1024:
            path.unlink()


class BudgetBackend(base.FakeBackend):
    """FakeBackend with the budget, the Graph company views and the other builders' hooks."""

    def __init__(self, tmp, room=None, spark_companies=(), hooks=(), **kwargs):
        super().__init__(tmp, **kwargs)
        self.room = dict(room or {})
        self.spark_companies = list(spark_companies)
        self.hooks = tuple(hooks)
        self.demoted = []
        self.promoted = []
        self.after_warm = None   # e.g. a click filling the namespace right after a task

    def response_capacity(self, namespace, today):
        return self.room.get(namespace, 1000)

    def response_room(self, kind, today):
        group = prewarm.response_group(kind)
        if group is None:
            return None
        return min(self.room.get(name, 1000) for name in prewarm.RESPONSE_NAMESPACES[group])

    def graph_company_bodies(self, dataset):
        return [(company, prewarm.spark_request(dataset, base.graph_filters('2026', company=company)))
                for company in self.spark_companies]

    def artifact_hooks(self):
        return self.hooks

    def demote_written(self, paths, day, position):
        self.demoted.append((list(paths), day, position))
        return list(paths)

    def promote_hinted(self, hints):
        self.promoted.append([hint['key'] for hint in hints])
        return []

    def warm(self, task, deadline=None, should_stop=None, retry=False, sleep=None):
        info = super().warm(task, deadline, should_stop, retry, sleep)
        if task['kind'] in prewarm.QUERY_KINDS or task['kind'] in prewarm.INSIGHTS_KINDS:
            info['written'] = [info['response_path']] if not task.get('view_only') else []
        if task.get('view_only'):
            info.update(response_path=None, view_path=info['response_path'] or str(self.tmp))
        if self.after_warm:
            self.after_warm(task)
        return info


class PlanTests(base.Harness):
    def setUp(self):
        super().setUp()
        self.backend = BudgetBackend(self.root, datasets=('Alpha', 'Beta', 'Gamma'),
                                     companies={'Alpha': ['A1', 'A2'], 'Beta': ['B1', 'B2', 'B3'], 'Gamma': ['G1']})

    def options(self, *datasets):
        return {name: {'receipt': {'source_fp': 'fp-' + name, 'companies': self.backend.companies[name]}}
                for name in datasets}

    def test_every_company_of_every_industry_after_the_defaults_in_hint_order(self):
        base.write_hints(self.state, ({'dataset': 'Beta', 'basis': 'receipt', 'panels': CORE}, 'k', '2026-09-24T12:00:00Z'))
        plan = self.plan(options_cache=self.options('Alpha', 'Beta', 'Gamma'))
        kinds = [task['kind'] for task in plan['tasks']]
        self.assertEqual(kinds[:6], ['query_view'] * 3 + ['insights_default'] * 3)
        companies = [(task['dataset'], task['body'].get('company')) for task in plan['tasks'] if task['kind'] == 'insights_company']
        # Beta (the newest hint) is covered by its neighbours; then Alpha and Gamma in catalog order.
        self.assertEqual(companies, [('Alpha', 'A1'), ('Alpha', 'A2'), ('Gamma', 'G1')])
        neighbours = [task['body'].get('company') for task in plan['tasks'] if task['kind'] == 'insights_neighbor'
                      and task['reason'] != 'newest hint: other basis']
        self.assertEqual(neighbours, ['B1', 'B2', 'B3'])
        company_bodies = [task['body'] for task in plan['tasks'] if task['kind'] == 'insights_company']
        self.assertEqual(company_bodies[0], {'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE, 'company': 'A1'})
        others = [index for index, task in enumerate(plan['tasks']) if task['reason'] == 'newest hint: other basis']
        last_company = max(index for index, kind in enumerate(kinds) if kind == 'insights_company')
        self.assertTrue(others and min(others) > last_company)        # the other bases come after every company
        self.assertEqual(plan['pending_companies'], [])

    def test_unknown_company_options_wait_and_trigger_a_replan(self):
        plan = self.plan(options_cache=self.options('Gamma'))
        self.assertEqual(plan['pending_companies'], ['Alpha', 'Beta'])
        self.assertEqual([task['body']['company'] for task in plan['tasks'] if task['kind'] == 'insights_company'], ['G1'])
        warmer = self.warmer()
        warmer.plan = plan
        self.assertIsNone(warmer.replan_trigger())
        warmer.remember_options({'dataset': 'Alpha', 'body': {'basis': 'receipt'}, 'source_fp': 'fp-Alpha'}, ['A1', 'A2'])
        self.assertEqual(warmer.replan_trigger(), 'companies_ready')

    def test_the_budget_is_spent_in_priority_order(self):
        self.backend.room = {'insights': 4, 'results': 1, 'summaries': 5}
        plan = self.plan(options_cache=self.options('Alpha', 'Beta', 'Gamma'))
        pending = [task for task in plan['tasks'] if task['state'] == 'pending']
        insights = [task for task in pending if prewarm.response_group(task['kind']) == 'insights']
        self.assertEqual([task['kind'] for task in insights], ['insights_default'] * 3 + ['insights_company'])
        over = [task for task in plan['tasks'] if task['state'] == 'over_budget']
        self.assertTrue(over and all(task['root_cause_hint'] == 'cache_budget' for task in over))
        self.assertEqual({task['kind'] for task in over}, {'insights_company'})
        views = [task for task in plan['tasks'] if task['kind'] == 'query_view']
        self.assertEqual([bool(task.get('view_only')) for task in views], [False, True, True])   # room 1 = min(1, 5)
        self.assertEqual(plan['budget']['insights'], {'room_at_plan': 4, 'planned_writes': 4, 'over_budget': len(over)})

    def test_graph_company_sparklines_and_artifact_kinds_are_planned_last(self):
        base.write_hints(self.state, ({'dataset': 'Gamma', 'basis': 'receipt', 'panels': CORE}, 'k', '2026-09-24T12:00:00Z'))
        self.backend.spark_companies = ['G1', 'G2']
        self.backend.hooks = ('comparison_flows', 'drawer_sidecars')
        self.backend.source('Beta')['index_present'] = False
        plan = self.plan()
        tail = [(task['kind'], task['dataset']) for task in plan['tasks'][-8:]]
        self.assertEqual(tail, [('graph_spark', 'Gamma'), ('graph_spark', 'Gamma'),
                                ('comparison_flows', 'Gamma'), ('comparison_flows', 'Alpha'), ('comparison_flows', 'Beta'),
                                ('drawer_sidecars', 'Gamma'), ('drawer_sidecars', 'Alpha'), ('drawer_sidecars', 'Beta')])
        spark = [task['body'] for task in plan['tasks'] if task['kind'] == 'graph_spark']
        self.assertEqual(spark[0]['filters'][0], {'field': 'company', 'op': 'eq', 'value': 'G1'})
        beta = {task['kind']: task['state'] for task in plan['tasks'] if task['dataset'] == 'Beta'}
        self.assertEqual(beta['insights_default'], 'blocked')          # no analytical index
        self.assertEqual(beta['drawer_sidecars'], 'pending')           # needs none
        self.assertEqual(prewarm.response_group('drawer_sidecars'), None)

    def test_graph_company_query_is_graph_js_request_params(self):
        query = prewarm.graph_company_query('Property & Casualty Insurance', 'Acme & Co', 2026)
        self.assertEqual(query, 'id=published-reports&industry=Property+%26+Casualty+Insurance&company=Acme+%26+Co&tier='
                                '&cycle=2026&funding=all&mode=direct&direction=Support&category=&recipient=')
        self.assertEqual(urllib.parse.parse_qs(prewarm.graph_company_query('A', 'B', '2024'))['cycle'], ['2024'])
        self.assertNotIn('cycle', urllib.parse.parse_qs(prewarm.graph_company_query('A', 'B', '')))
        self.assertNotIn('latest', prewarm.graph_company_query('A', 'B', 2026.0))
        self.assertIn('cycle=2026&', prewarm.graph_company_query('A', 'B', 2026.0))


class RunTests(base.Harness):
    def setUp(self):
        super().setUp()
        self.backend = BudgetBackend(self.root, datasets=('Alpha',), companies={'Alpha': ['A1', 'A2', 'A3']})

    def test_over_budget_tasks_are_skipped_never_failed_and_views_keep_their_projection(self):
        base.write_hints(self.state, ({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE, 'company': 'A1'}, 'k',
                                      '2026-09-24T12:00:00Z'))
        self.backend.room = {'insights': 3, 'results': 0, 'summaries': 0}
        # Clicks fill the Insights namespace right after the first task: the Insights tasks planned with room
        # must then be skipped at their own check, before they write anything.
        self.backend.after_warm = lambda task: self.backend.room.update(insights=0) if task['kind'] == 'query_view' else None
        report = self.warmer().run()
        self.assertEqual(report['status'], 'success', report)
        rows = self.ledger()
        skipped = [row for row in rows if row['outcome'] == 'skipped']
        self.assertTrue(skipped)
        self.assertTrue(all(row['root_cause_hint'] == 'cache_budget' and 'never quarantines' in row['stop'] for row in skipped))
        self.assertFalse([row for row in rows if row['outcome'] == 'failed'])
        self.assertEqual(prewarm.consecutive_failures(rows), {})
        view = next(row for row in rows if row['kind'] == 'query_view' and row['outcome'] == 'succeeded')
        self.assertIsNone(view['response_path'])
        self.assertTrue(view['detail']['view_path'])
        plan = prewarm.read_json(self.state.plan)
        states = {task['kind']: task['state'] for task in plan['tasks'] if task['kind'] != 'insights_company'}
        self.assertEqual(states['query_view'], 'warm')
        self.assertTrue(any(task['state'] == 'over_budget' for task in plan['tasks']))
        self.assertTrue(any(event[1] == 'skipped' and 'Over budget' in event[2] for event in self.events))
        # A view warmed as its projection only stays warm on the next run.
        calls = len(self.backend.calls)
        self.warmer().run()
        self.assertNotIn('query_view:alpha', ' '.join(task for task, _ in self.backend.calls[calls:]))

    def test_room_is_checked_again_just_before_each_task(self):
        warmer = self.warmer()
        warmer.plan = self.plan()
        task = next(task for task in warmer.plan['tasks'] if task['kind'] == 'insights_default')
        self.backend.room = {'insights': 0}
        self.assertEqual(warmer.execute(task), 'skipped')
        self.assertEqual(task['state'], 'over_budget')
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.ledger()[-1]['outcome'], 'skipped')

    def test_written_responses_are_demoted_with_the_plan_day_and_position(self):
        self.warmer().run()
        self.assertTrue(self.backend.demoted)
        positions = [position for _, _, position in self.backend.demoted]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(all(day == self.backend.today_value for _, day, _ in self.backend.demoted))
        succeeded = [row for row in self.ledger() if row['outcome'] == 'succeeded' and row['kind'] != 'query_view']
        self.assertTrue(all(row['detail'].get('demoted') == 1 for row in succeeded))

    def test_hinted_keys_are_offered_for_promotion(self):
        base.write_hints(self.state, ({'dataset': 'Alpha', 'basis': 'receipt', 'panels': CORE}, 'k-used', '2026-09-24T12:00:00Z'))
        self.warmer().run()
        self.assertTrue(self.backend.promoted and self.backend.promoted[0] == ['k-used'])

    def test_artifact_kinds_need_more_free_memory(self):
        warmer = self.warmer(free_bytes=lambda: prewarm.MIN_FREE_BYTES + 1)
        self.assertIsNone(warmer.resource_guard({'kind': 'insights_company'}))
        hint, detail = warmer.resource_guard({'kind': 'drawer_sidecars'})
        self.assertEqual(hint, 'low_memory')
        self.assertIn('drawer_sidecars', detail)
        roomy = self.warmer(free_bytes=lambda: prewarm.MIN_FREE_BYTES + prewarm.ARTIFACT_EXTRA_FREE_BYTES)
        self.assertIsNone(roomy.resource_guard({'kind': 'comparison_flows'}))


class WriteObserverTests(unittest.TestCase):
    def test_only_response_files_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = mock.Mock()
            store.ROOT = Path(tmp)
            written = []
            store._atomic = lambda path, payload: written.append(path)
            observer = prewarm.WriteObserver(store)
            store._atomic(Path(tmp) / 'responses' / 'insights' / 'a.json', b'{}')
            store._atomic(Path(tmp) / 'views' / 'b.json', b'{}')
            store._atomic(Path(tmp) / 'responses' / 'results' / 'c.json', b'{}')
            self.assertEqual(len(written), 3)
            self.assertEqual([Path(path).name for path in observer.responses()], ['a.json', 'c.json'])
            observer.reset()
            self.assertEqual(observer.responses(), [])


if __name__ == '__main__':
    unittest.main()
