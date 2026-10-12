"""Behavior/rank boundary, sign, artifact isolation and fail-closed regressions."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from stance.rank_honors_policy import POLICY, classify_field, number, rank_cutoffs, overall_records
from stance.build_field_action_evidence import canonical_measure, integer, _deduplicate, Snapshot, vote_actions, vote_direction, _vote_mapping
from stance.build_rank_honors import build_honors, serialized
from stance.honors_views import load_honors, load_generation, validate_honors, POINTER_SCHEMA


def action(i, score=1, *, tensor_position=1, measure=None, alignment=1, kind='legislative', category='Field'):
    bio = f'M{i:06d}'
    measure = measure or f'119:hr:{i + 1}'
    base = f'{kind}|{bio}|{measure}'
    return {'member_id': bio, 'category_id': category, 'measure_id': measure,
            'evidence_id': base + '|' + category, 'overall_event_id': base,
            'weight': str(score), 'decay': '1', 'alignment': alignment,
            'position_on_measure': tensor_position, 'tensor_position_scoped': True, 'kind': kind,
            'congress': 119, 'role': 'vote' if kind == 'vote' else 'sponsor'}


def classify(actions, ids=None, tensor=None):
    return classify_field(ids or [a['member_id'] for a in actions], actions, tensor or {})


def fixture():
    roster = {f'Member {i:02}': {'member_id': f'M{i:06d}', 'chamber': 'House' if i % 2 else 'Senate'} for i in range(20)}
    acts = [action(i, 20 - i) for i in range(20)]
    doc = build_honors(roster, ['Field'], acts, {}, list(roster), '2026-09-12',
                       board_metadata={'mode': 'factor', 'generation': 'fixture', 'top13': list(roster)[:13], 'order': list(roster)})
    doc['generation_id'] = 'a' * 24
    doc['inputs_sha256'] = {'fixture-input.json': hashlib.sha256(b'{}').hexdigest()}
    evidence = {'schema': 'field-action-evidence-v1', 'generation_id': doc['generation_id'], 'actions': acts, 'tensor': {}}
    return doc, evidence


class RankHonorsPolicyTests(unittest.TestCase):
    def test_strict_floor_cutoffs_and_exclusive_bands(self):
        for size in (0, 1, 4, 5, 6, 7, 18, 19, 20, 37, 100):
            self.assertEqual(rank_cutoffs(size), dict(champion=size * 5 // 100, ally=size * 15 // 100, highly_persuadable=size * 20 // 100))
            acts = [action(i, size - i) for i in range(size)]
            rows = classify(acts) if acts else classify_field([], [], {})
            actual = {label: sum(r['recognition'] == label for r in rows.values()) for label in ('Champion', 'Ally', 'Highly Persuadable')}
            self.assertEqual(actual, {'Champion': size * 5 // 100, 'Ally': size * 15 // 100 - size * 5 // 100,
                                      'Highly Persuadable': size * 20 // 100 - size * 15 // 100})

    def test_zero_population_and_no_action_do_not_get_rank(self):
        rows = classify_field(['M000001'], [], {})
        self.assertEqual(rows['M000001']['cohort_size'], 0)
        self.assertIsNone(rows['M000001']['rank_start'])
        self.assertEqual(rows['M000001']['evidence_status'], 'no_recorded_action')

    def test_tie_crossing_champion_boundary_falls_as_group_to_ally(self):
        acts = [action(i, 100 - i if i not in (4, 5, 6) else 96) for i in range(100)]
        rows = classify(acts)
        for i in (4, 5, 6):
            row = rows[f'M{i:06d}']
            self.assertEqual((row['rank_start'], row['rank_end'], row['recognition']), (5, 7, 'Ally'))
            self.assertTrue(row['boundary_tie'])

    def test_tie_crossing_hp_boundary_has_no_honor(self):
        acts = [action(i, 100 - i if i not in (19, 20) else 81) for i in range(100)]
        rows = classify(acts)
        self.assertEqual(rows['M000019']['reason'], 'unresolved_boundary_tie')
        self.assertIsNone(rows['M000019']['recognition'])

    def test_all_tied_cannot_receive_an_honor(self):
        rows = classify([action(i) for i in range(100)])
        self.assertTrue(all(r['rank_end'] == 100 and r['recognition'] is None for r in rows.values()))

    def test_opposition_and_neutral_stay_in_denominator_without_positive_honors(self):
        for sign in (-1, 0):
            rows = classify([action(i, i + 1, alignment=sign) for i in range(20)])
            self.assertTrue(all(r['recognition'] is None and r['cohort_size'] == 20 and r['action_count'] == 1 for r in rows.values()))

    def test_cancelled_record_is_observed_and_mixed(self):
        a = action(1)
        b = action(1, alignment=-1, measure='119:hr:99')
        r = classify([a, b], ['M000001'])['M000001']
        self.assertEqual((r['evidence_status'], r['raw_field_score'], r['alignment']), ('observed', '0', 'Mixed actions'))

    def test_tensor_never_reverses_unequal_primary_scores(self):
        acts = [action(1, 2), action(2, 1)]
        rows = classify(acts, tensor={acts[0]['measure_id']: '-1000000', acts[1]['measure_id']: '1000000'})
        self.assertEqual(rows['M000001']['rank_start'], 1)

    def test_tensor_missing_for_one_member_disables_whole_primary_block(self):
        acts = [action(1), action(2), action(3)]
        tensor = {acts[0]['measure_id']: '1', acts[1]['measure_id']: '0'}
        rows = classify(acts, tensor=tensor)
        self.assertTrue(all((r['rank_start'], r['rank_end']) == (1, 3) for r in rows.values()))
        self.assertTrue(all(r['tensor_status'] == 'tensor_unavailable_for_tie' for r in rows.values()))

    def test_zero_tensor_is_valid_and_sign_reverses_exactly_once(self):
        acts = [action(1, tensor_position=-1), action(2)]
        tensor = {acts[0]['measure_id']: '-0.5', acts[1]['measure_id']: '0'}
        rows = classify(acts, tensor=tensor)
        self.assertEqual((rows['M000001']['tensor_tiebreak'], rows['M000001']['rank_start']), ('0.5', 1))
        self.assertEqual(rows['M000002']['tensor_status'], 'complete')

    def test_unscoped_letter_cannot_supply_tensor(self):
        acts = [action(1, kind='letter'), action(2)]
        acts[0]['tensor_position_scoped'] = False
        rows = classify(acts, tensor={a['measure_id']: str(i) for i, a in enumerate(acts)})
        self.assertEqual(rows['M000002']['rank_end'], 2)

    def test_fixed_inputs_reorder_identically_and_inactive_people_do_not_change_cohort(self):
        acts = [action(i, i + 1) for i in range(20)]
        baseline = classify(acts)
        other = classify(list(reversed(acts)), list(reversed(list(baseline))) + ['M999999'])
        self.assertEqual(baseline, {m: other[m] for m in baseline})

    def test_nonfinite_and_bool_arithmetic_rejected(self):
        for value in ('NaN', 'Infinity', True, None, 'bad'):
            with self.assertRaises(ValueError):
                number(value)

    def test_binary_float_noise_is_not_a_merit_difference(self):
        self.assertEqual(number(2.4000000000000004), number('2.4'))

    def test_typed_measure_keys_separate_type_and_congress(self):
        self.assertNotEqual(canonical_measure('H.R.1', 118), canonical_measure('H.J.Res.1', 118))
        self.assertNotEqual(canonical_measure('H.R.1', 118), canonical_measure('H.R.1', 119))
        self.assertEqual(canonical_measure('H.Amdt.9', 119), '119:hamdt:9')
        with self.assertRaises(ValueError):
            canonical_measure('LTR-118-TAX', 119)
        for bad in ('119.9', '119.0000000000001', True, 'NaN'):
            with self.assertRaises(ValueError):
                canonical_measure('H.R.1', bad)
        for bad in ('0.5', '-1.1', True):
            with self.assertRaises(ValueError):
                integer(bad)

    def test_explicit_neutral_vote_mapping_is_distinct_from_missing(self):
        self.assertEqual(vote_direction('119:hr:1', 13, {}, {'119:hr:1': 0}, {4, 14}), 0)
        self.assertEqual(vote_direction('119:hr:1', 13, {('119:hr:1', 13): 0}, {}, {4, 14}), 0)
        self.assertIsNone(vote_direction('119:hr:1', 13, {}, {}, {4, 14}))

    def test_vote_receipt_missing_mapping_source_hash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'omits a required'):
            _vote_mapping(None, {'inputs_sha256': {}}, {})

    def _cast_code_actions(self, dial):
        rows = [{'congress': '119', 'chamber': 'House', 'rollnumber': '1',
                 'bioguide': f'M{i:06d}', 'date': '2026-01-01', 'vote_question': 'On Passage',
                 'bill_number': 'H.R.1', 'canonical_key': '119:hr:1', 'cast_code': str(i),
                 'vote': 'Yea' if i in (1, 2, 3) else 'Nay' if i in (4, 5, 6) else 'Present'} for i in range(10)]
        producer = (Path(__file__).resolve().parents[2] / 'legislation/build_vote_category.py').read_bytes()
        class Source:
            def json(self, path):
                return {'schema': 'vote-category-refresh-v1', 'status': 'PASS',
                        'outputs_sha256': {'cu_rollcall_votes_clean.csv': 'ok', 'vote_category_lean.json': 'ok'}}
            def digest(self, path):
                return 'ok'
            def rows(self, path):
                return rows
            def read(self, path):
                return producer
        roster = {str(i): {'member_id': f'M{i:06d}'} for i in range(10)}
        counts = Counter()
        with patch.dict(os.environ, {'VOTE_DIRECTION': dial}):
            with patch('stance.build_field_action_evidence._vote_mapping', return_value={('119:hr:1', 'CFPB'): 0}):
                return vote_actions(Source(), roster, ['CFPB'], {}, date(2026, 9, 12), counts), roster

    def test_legacy_dial_admits_only_the_historical_cast_codes(self):
        actions, roster = self._cast_code_actions('legacy')
        self.assertEqual({a['cast_code'] for a in actions}, {1, 4})
        self.assertEqual(len(actions), 2)
        self.assertTrue(all(a['alignment'] == 0 for a in actions))
        classified = classify_field([r['member_id'] for r in roster.values()], actions, {})
        self.assertEqual(classified['M000001']['cohort_size'], 2)
        self.assertEqual(classified['M000001']['evidence_status'], 'observed')

    def test_actual_vote_codes_only_and_neutral_vote_counts_as_action(self):
        # D7: the honors lane now admits the analytic producer's own Yea/Nay sets
        # ({1,2,3} / {4,5,6}); Present/NotVoting stay out. The pre-D7 pin of
        # {1, 4} is kept above under VOTE_DIRECTION=legacy.
        actions, roster = self._cast_code_actions('honors')
        self.assertEqual({a['cast_code'] for a in actions}, {1, 2, 3, 4, 5, 6})
        self.assertEqual({a['position_on_measure'] for a in actions}, {1, -1})
        self.assertEqual(len(actions), 6)
        self.assertTrue(all(a['alignment'] == 0 for a in actions))
        classified = classify_field([r['member_id'] for r in roster.values()], actions, {})
        self.assertEqual(classified['M000001']['cohort_size'], 6)
        self.assertEqual(classified['M000001']['evidence_status'], 'observed')

    def test_duplicate_ingestion_and_role_upgrade_keep_one_preferred_action(self):
        a = action(1)
        b = {**a, 'weight': '3', 'role': 'sponsor'}
        from collections import Counter
        deduped = _deduplicate([a, a, b], Counter())
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]['weight'], '3')
        with self.assertRaises(ValueError):
            _deduplicate([a, {**a, 'alignment': -1}], Counter())

    def test_overall_requires_exact_active_top13_action(self):
        roster = {str(i): {'member_id': f'M{i:06d}'} for i in range(20)}
        acts = [action(i) for i in range(13)]
        rows = overall_records(roster, acts, list(roster))
        self.assertEqual(sum(r['recognition'] == 'Champion' for r in rows.values()), 13)
        with self.assertRaises(ValueError):
            overall_records(roster, acts[:-1], list(roster))
        with self.assertRaises(ValueError):
            overall_records(roster, acts, list(roster)[:12])

    def test_overall_deduplicates_multicategory_event_and_refuses_conflicting_direction(self):
        roster = {str(i): {'member_id': f'M{i:06d}'} for i in range(13)}
        acts = [action(i) for i in range(13)]
        copy = {**acts[0], 'category_id': 'Other', 'evidence_id': acts[0]['evidence_id'] + '|Other'}
        rows = overall_records(roster, acts + [copy], list(roster))
        self.assertEqual(rows['0']['action_count'], 1)
        with self.assertRaises(ValueError):
            overall_records(roster, acts + [{**copy, 'alignment': -1}], list(roster))


class HonorsArtifactTests(unittest.TestCase):
    def test_swapped_rank_order_and_corrupt_overall_evidence_rejected(self):
        doc, evidence = fixture()
        broken = deepcopy(doc)
        a, b = [broken['members'][name]['fields']['Field'] for name in ('Member 00', 'Member 19')]
        for key in ('rank_start', 'rank_end', 'recognition', 'reason'):
            a[key], b[key] = b[key], a[key]
        with self.assertRaises(ValueError):
            validate_honors(broken, evidence)
        for key, bad in (('action_count', 999), ('evidence_ids', []), ('alignment', 'Opposing actions')):
            broken = deepcopy(doc)
            broken['members']['Member 00']['overall'][key] = bad
            with self.assertRaises(ValueError):
                validate_honors(broken, evidence)

    def test_shared_validation_and_action_reference_integrity(self):
        doc, evidence = fixture()
        validate_honors(doc, evidence)
        broken = deepcopy(doc)
        broken['members']['Member 00']['fields']['Field']['action_count'] = 0
        with self.assertRaises(ValueError):
            validate_honors(broken, evidence)

    def test_threshold_change_does_not_mutate_inputs_or_other_field(self):
        doc, evidence = fixture()
        original = deepcopy(evidence)
        policy = {**POLICY, 'champion_pct': 1}
        roster = {n: {'member_id': m['member_id'], 'chamber': m['chamber']} for n, m in doc['members'].items()}
        changed = build_honors(roster, ['Field'], evidence['actions'], {}, list(roster), '2026-09-12', policy=policy)
        self.assertEqual(evidence, original)
        self.assertEqual([r['overall'] for r in doc['members'].values()], [r['overall'] for r in changed['members'].values()])
        for name in roster:
            self.assertEqual(doc['members'][name]['fields']['Field']['raw_field_score'], changed['members'][name]['fields']['Field']['raw_field_score'])

    def test_missing_source_snapshot_fails_and_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = Snapshot(root)
            with self.assertRaises(FileNotFoundError):
                snapshot.read('missing.json')
            p = Path(root) / 'input.json'
            p.write_text('{}')
            snapshot.read(p)
            p.write_text('{"changed":1}')
            with self.assertRaises(ValueError):
                snapshot.verify()

    def test_active_loader_checks_hashes_generations_and_explicit_legacy(self):
        doc, evidence = fixture()
        with tempfile.TemporaryDirectory() as root:
            base = Path(root) / 'data/stance/rank_honors'
            (Path(root) / 'fixture-input.json').write_bytes(b'{}')
            directory = base / doc['generation_id']
            directory.mkdir(parents=True)
            hashes = {}
            for name, value in [('rank_honors.json', doc), ('field_action_evidence.json', evidence),
                                ('transition_report.json', {'generation_id': doc['generation_id']})]:
                raw = serialized(value)
                (directory / name).write_bytes(raw)
                hashes[name] = hashlib.sha256(raw).hexdigest()
            (base / 'active.json').write_bytes(serialized({'schema': POINTER_SCHEMA, 'generation_id': doc['generation_id'], 'files_sha256': hashes}))
            # The fixture board is seated on 'factor', so this block sets the dial explicitly.
            # The loader no longer carries its own 'factor' default (D-030/D-031): it compares
            # against tier_util's ruled default, so leaving the dial unset here would exercise
            # only the staleness gate and never the hash and generation checks below.
            with patch.dict(os.environ, {'HONORS_MODE': 'active', 'PRIMACY_BOARD': 'factor'}):
                self.assertEqual(load_honors(root), doc)
                with patch.dict(os.environ, {'PRIMACY_BOARD': 'legxfactor'}):
                    with self.assertRaises(ValueError):
                        load_honors(root)
                (Path(root) / 'fixture-input.json').write_bytes(b'{"changed":true}')
                with self.assertRaises(ValueError):
                    load_honors(root)
                (Path(root) / 'fixture-input.json').write_bytes(b'{}')
                (directory / 'rank_honors.json').write_text('{}')
                with self.assertRaises(ValueError):
                    load_honors(root)
            with patch.dict(os.environ, {'HONORS_MODE': 'legacy'}):
                self.assertIsNone(load_honors(root))

    def test_generation_mismatch_refused(self):
        doc, evidence = fixture()
        evidence['generation_id'] = 'b' * 24
        with self.assertRaises(ValueError):
            validate_honors(doc, evidence)

    def test_no_implicit_legacy_fallback_on_missing_active(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'HONORS_MODE': 'active'}):
            with self.assertRaises(FileNotFoundError):
                load_honors(root)


if __name__ == '__main__':
    unittest.main()
