"""Display attachment contract without importing the desk's live data producers."""
import ast
import copy
import os
from pathlib import Path
import unittest
import unittest.mock
from types import SimpleNamespace
import re
import shutil
import subprocess
import sys


SOURCE = Path(__file__).resolve().parents[2] / 'surfaces' / 'make_member_desk.py'
sys.path.insert(0, str(SOURCE.parents[1]))
from surfaces.honors_publication import compact_honor_cell
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
FUNCTIONS = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
             and node.name in ('attach_honors_display', 'honors_generation_meta')]
# TIERNAME is lifted from the same source rather than imported, so this test
# still never executes the desk's data producers.
CONSTANTS = [node for node in TREE.body if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == 'TIERNAME' for t in node.targets)]
NAMESPACE = {'compact_honor_cell': compact_honor_cell, 'os': os}
exec(compile(ast.Module(body=CONSTANTS + FUNCTIONS, type_ignores=[]), str(SOURCE), 'exec'), NAMESPACE)
attach = NAMESPACE['attach_honors_display']
TIERNAME = NAMESPACE['TIERNAME']


class MemberDeskHonorsTest(unittest.TestCase):
    def setUp(self):
        self.bundle = {'cats': ['CFPB'], 'members': [{
            'name': 'Test Member', 'rank': 200, 'score': .99,
            'stances': 'C', 'overall_stance': 'C', 'adjrank': {'CFPB': 1},
            'radar': [.99], 'roi': .25,
        }]}
        self.honors = {
            'schema': 'rank-honors-v1', 'generation_id': 'fixed-fixture',
            'policy': {'champion_pct': 5}, 'as_of': '2026-09-12',
            'overall_policy': {'id': 'overall_primacy_standing_v2', 'ally_pct': 15,
                               'highly_persuadable_pct': 20, 'persuadable_pct': 50},
            'evidence_window': {'congresses': [117, 118, 119]},
            'overall_board': {'mode': 'factor', 'top13': []}, 'cats': ['CFPB'],
            'members': {'Test Member': {'member_id': 'T000001',
                'overall': {'recognition': None, 'rank': 150, 'priority_tier': 0,
                            'stance': 'Persuadable', 'stance_rank_start': 160,
                            'stance_rank_end': 160, 'stance_cohort_size': 533,
                            'stance_cutoffs': {'ally': 79, 'highly_persuadable': 106,
                                               'persuadable': 266},
                            'mixed_record': True, 'raw_overall_score': '12.3'},
                'fields': {'CFPB': {'recognition': None, 'rank_start': None,
                    'rank_end': None, 'action_count': 0,
                    'evidence_status': 'no_recorded_action'}}}},
        }

    def test_attachment_preserves_every_analytical_input(self):
        baseline = copy.deepcopy(self.bundle)
        field = self.honors['members']['Test Member']['fields']['CFPB']
        field.update({'member_id': 'T000001', 'category_id': 'CFPB',
                      'policy_id': 'field_rank_honors_v1', 'evidence_ids': ['event-1'],
                      'missing_tensor_measures': ['119:hr:1']})
        source = copy.deepcopy(self.honors)
        result = attach(self.bundle, self.honors)
        self.assertEqual(result['rank_honors']['generation_id'], 'fixed-fixture')
        self.assertEqual(result['rank_honors']['overall_policy'], self.honors['overall_policy'])
        public = result['members'][0]['honors']
        self.assertEqual(public['member_id'], 'T000001')
        # D1.c: one overall label. The published cell is the honors overall with
        # its standing replaced by the analytic band already on the bundle.
        self.assertEqual(public['overall'], {**self.honors['members']['Test Member']['overall'],
                                             'stance': TIERNAME[self.bundle['members'][0]['overall_stance']],
                                             'stance_reason': 'analytic_overall_band'})
        self.assertEqual(public['fields']['CFPB'], {key: field[key] for key in (
            'recognition', 'rank_start', 'rank_end', 'action_count', 'evidence_status',
        )})
        self.assertEqual(self.honors, source)
        result.pop('rank_honors')
        result['members'][0].pop('honors')
        self.assertEqual(result, baseline)

    def test_legacy_mode_is_exact_noop(self):
        baseline = copy.deepcopy(self.bundle)
        self.assertIs(attach(self.bundle, None), self.bundle)
        self.assertEqual(self.bundle, baseline)

    def test_stale_overall_policy_and_missing_standing_fail_closed(self):
        old = copy.deepcopy(self.honors)
        old.pop('overall_policy')
        with self.assertRaisesRegex(RuntimeError, 'overall behavioral standing policy'):
            attach(self.bundle, old)
        for key, value in [('stance', None), ('priority_tier', None),
                           ('priority_tier', True), ('priority_tier', 4)]:
            malformed = copy.deepcopy(self.honors)
            malformed['members']['Test Member']['overall'][key] = value
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(RuntimeError, 'overall priority/standing incomplete'):
                    attach(self.bundle, malformed)

    def test_public_overall_keeps_standing_proof_without_full_event_lists(self):
        overall = self.honors['members']['Test Member']['overall']
        overall.update({'evidence_ids': ['action-id'], 'missing_tensor_measures': ['119:hr:1'],
                        'opposition_rank_start': 4, 'opposition_rank_end': 5,
                        'opposition_cohort_size': 100,
                        'opposition_cutoffs': {'opponent': 5, 'highly_skeptical': 15, 'skeptic': 20},
                        'tensor_status': 'incomplete', 'tensor_tiebreak': None,
                        'neutral_action_count': 2, 'record_pattern': 'mixed',
                        'stance_boundary_tie': True, 'stance_reason': 'boundary_tie'})
        attach(self.bundle, self.honors)
        actual = self.bundle['members'][0]['honors']['overall']
        self.assertEqual(actual, {**{key: value for key, value in overall.items()
                                     if key not in ('evidence_ids', 'missing_tensor_measures')},
                                  'stance': 'Champion', 'stance_reason': 'analytic_overall_band'})
        actual['stance_cutoffs']['ally'] = -1
        self.assertEqual(overall['stance_cutoffs']['ally'], 79)

    def test_overall_label_legacy_dial_keeps_the_primacy_standing(self):
        with unittest.mock.patch.dict(os.environ, {'OVERALL_LABEL': 'legacy'}):
            attach(self.bundle, self.honors)
        actual = self.bundle['members'][0]['honors']['overall']
        self.assertEqual(actual['stance'], 'Persuadable')
        self.assertNotIn('stance_reason', actual)

    def test_unknown_overall_label_dial_fails_closed(self):
        with unittest.mock.patch.dict(os.environ, {'OVERALL_LABEL': 'whatever'}):
            with self.assertRaisesRegex(RuntimeError, 'Unknown OVERALL_LABEL'):
                attach(self.bundle, self.honors)

    def test_mismatched_roster_fails_instead_of_silent_legacy_labels(self):
        self.honors['members'].clear()
        with self.assertRaisesRegex(RuntimeError, 'roster'):
            attach(self.bundle, self.honors)

    def test_mismatched_category_and_missing_field_fail(self):
        self.honors['cats'] = ['Interchange']
        with self.assertRaisesRegex(RuntimeError, 'categories'):
            attach(self.bundle, self.honors)
        self.honors['cats'] = ['CFPB']
        self.honors['members']['Test Member']['fields'].clear()
        with self.assertRaisesRegex(RuntimeError, 'fields incomplete'):
            attach(self.bundle, self.honors)

    def test_page_generation_marker_is_explicit_and_escaped(self):
        metadata = NAMESPACE['honors_generation_meta']
        self.assertEqual(metadata(self.bundle), '')
        self.honors['generation_id'] = 'candidate-"quoted"'
        attach(self.bundle, self.honors)
        self.assertEqual(metadata(self.bundle),
                         '<meta name="melange-honors-generation" '
                         'content="candidate-&quot;quoted&quot;">')

    def test_internal_primacy_stage_cannot_render_legacy_html(self):
        build = copy.deepcopy(next(node for node in TREE.body
                                   if isinstance(node, ast.FunctionDef)
                                   and node.name == 'build_bundle'))
        build.body = build.body[:3] + [ast.Return(value=ast.Name(id='honors', ctx=ast.Load()))]
        module = ast.fix_missing_locations(ast.Module(body=[build], type_ignores=[]))
        calls = []
        namespace = {'sys': SimpleNamespace(argv=['desk', '--analytic-inputs-only']),
                     'B': 'fixture', 'load_honors': lambda root: calls.append(root) or 'active'}
        exec(compile(module, str(SOURCE), 'exec'), namespace)
        with self.assertRaisesRegex(RuntimeError, 'requires --data'):
            namespace['build_bundle']()
        namespace['sys'].argv.append('--data')
        self.assertIsNone(namespace['build_bundle']())
        self.assertFalse(calls)
        namespace['sys'].argv = ['desk', '--data']
        self.assertEqual(namespace['build_bundle'](), 'active')
        self.assertEqual(calls, ['fixture'])

    def test_browser_rendering_suite_executes_all_cases(self):
        node = shutil.which('node')
        if not node:
            bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            node = str(bundled) if bundled.is_file() else None
        self.assertIsNotNone(node, 'Node runtime required for the member desk rendering gate')
        completed = subprocess.run(
            [node, '--test', '--test-reporter=tap',
             str(Path(__file__).with_suffix('.cjs'))],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=60, check=False,
        )
        output = completed.stdout + '\n' + completed.stderr
        self.assertEqual(completed.returncode, 0, output)
        totals = {key: int(count) for key, count in re.findall(
            r'^# (tests|pass|fail|cancelled|skipped) (\d+)\s*$', output, re.MULTILINE,
        )}
        self.assertGreaterEqual(totals.get('tests', 0), 7, output)
        self.assertEqual(totals.get('pass'), totals['tests'], output)
        self.assertEqual(totals.get('fail'), 0, output)
        self.assertEqual(totals.get('cancelled'), 0, output)
        self.assertEqual(totals.get('skipped'), 0, output)


if __name__ == '__main__':
    unittest.main()
