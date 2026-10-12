"""contracts/graph-not-traced.json v1: /api/graph's not-traced fields (F4), asserted
against the producer (graph_data.graph_view).

A candidate view (activity or estimates) carries transfer_trace, transfer_index and
candidate_note_title; a direct view carries none. The scenarios below are the ones
the contract's examples were produced from, so each example is compared with what
the producer answers now; each negative control must be refused for its reason.
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import graph_data  # noqa: E402
from test_graph_not_traced import Fixture, bundle  # noqa: E402

CONTRACT = (Path(__file__).resolve().parents[2] / 'audits' / 'mentat_failings_20260915'
            / 'contracts' / 'graph-not-traced.json')
PINNED = ('filters', 'candidate_available', 'candidate_note', 'candidate_note_title', 'transfer_index',
          'transfer_trace', 'candidates')


def activity(case, units=None, **filters):
    with patch('downstream.inventory', return_value=units or {}):
        return case.view(mode='activity', **filters)


def saved_root(case):
    unit = {'committee_id': 'C00000003', 'cycle': '2026', 'receipts': [], 'expenditures': [],
            'disbursements': [{'line_number': '22', 'disbursement_amount': 100, 'disbursement_type': '24G',
                               'recipient_committee_id': 'C00000009', 'sub_id': 'd1'}],
            'totals': {'disbursements': 100.0}, '_source': 'fixture', '_sha256': 'f' * 64}
    return activity(case, {('2026', 'C00000003'): unit}, recipient='C00000003')


def stale_sheet(case):
    case.build(sheets=True)
    bundle(case.path, {'2026': 'stale'})
    return activity(case, recipient='C00000003')


def stale_estimate(case):
    bundle(case.path, {'2026': 'stale'}, rows=[('2026', 55)])
    return case.view(mode='estimates')


def partial_estimate(case):
    case.build(cycles=('2025-2026', '2023-2024'))
    bundle(case.path, {'2024': 'missing', '2026': 'current'}, rows=[('2024', 50), ('2026', 123)])
    return case.view(mode='estimates')


SCENARIOS = {
    'estimates with no audit bundle': lambda case: case.view(mode='estimates'),
    'estimates for a cycle whose transfer index is stale': stale_estimate,
    'estimates across two cycles, one missing its transfer index': partial_estimate,
    'drawer: no saved snapshot and no Transfer Paths sheet': lambda case: activity(case, recipient='C00000003'),
    'drawer: a Transfer Paths sheet, but a stale transfer index': stale_sheet,
    'drawer: a saved root, and one committee past it with no snapshot': saved_root,
    'direct view: no not-traced fields': lambda case: case.view(),
}


def produce():
    """{scenario name: the producer's answer}, each on its own fresh workbook."""
    answers = {}
    for name, build in SCENARIOS.items():
        case = Fixture('setUp')
        case.setUp()
        try:
            answers[name] = build(case)
        finally:
            case.doCleanups()
    return answers


def pinned(payload):
    return {field: payload.get(field) for field in PINNED}


class GraphNotTracedContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT.read_text(encoding='utf-8'))
        cls.examples = {example['name']: example['payload'] for example in cls.contract['examples']}
        cls.answers = produce()

    def test_every_scenario_holds_and_is_its_example(self):
        self.assertEqual(sorted(self.examples), sorted(SCENARIOS))
        for name, answer in self.answers.items():
            with self.subTest(scenario=name):
                self.assertEqual(graph_data.not_traced_problems(answer), [])
                self.assertEqual(pinned(answer), pinned(self.examples[name]))

    def test_every_example_validates(self):
        for name, payload in self.examples.items():
            with self.subTest(example=name):
                self.assertEqual(graph_data.not_traced_problems(payload), [])

    def test_every_negative_control_is_refused_for_its_stated_reason(self):
        self.assertGreaterEqual(len(self.contract['negative_controls']), 6)
        for control in self.contract['negative_controls']:
            problems = graph_data.not_traced_problems(copy.deepcopy(control['payload']))
            self.assertTrue(any(control['expect'] in problem for problem in problems),
                            f"{control['name']}: {problems}")


if __name__ == '__main__':
    unittest.main()
