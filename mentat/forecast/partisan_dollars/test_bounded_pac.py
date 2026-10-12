"""Synthetic interval controls; no historical data read or model selection."""
import copy
import importlib.util
import itertools
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('bounded_under_test', Path(__file__).with_name('bounded_pac.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def bound(lo, hi=None):
    return m.interval(lo, lo if hi is None else hi)


def frame():
    return dict(sector='commercial_banks', chamber='House', target=2018, horizon='m18',
        issue='2018-07-20', cut='2018-06-30',
        history=[dict(cycle=y, final={'D': bound(10000), 'R': bound(20000)},
                      observed={'D': bound(5000), 'R': bound(10000)},
                      remaining={'D': bound(5000), 'R': bound(10000)}) for y in (2012, 2014, 2016)],
        observed={'D': bound(6000), 'R': bound(9000)})


def presence():
    return [dict(sector=s, cycle=y, chamber=c, ledger_reconciled='True', sector_raw_rows='1',
                 first_due_any_raw=f'{y-1}-02-20') for s in m.SECTORS for y in m.YEARS for c in m.CHAMBERS]


def ledger(cents, party, chamber='House', year=2018, order=1, due='2018-07-15', day='2018-06-30'):
    return dict(sector='commercial_banks', cycle=year, chamber=chamber, party=party, cents=cents,
                source_record_order=order, hs=1, date=day, due_available=due)


def v3_row(cents=100, order=1):
    return dict(ledger(cents, 'D', order=order), candidate_id='H00000001',
                original_office_discrepancy='False', canonical_candidate_id='H00000001',
                canonical_chamber='House', canonical_party='D', canonical_classification_status='resolved',
                canonical_office_resolution_status='no_discrepancy', canonical_hs_eligibility='include_hs',
                canonical_evidence_key='2018:H00000001:C00000001', canonical_office_evidence_key='')


def manifest(n):
    return dict(schema_version='pac-canonical-v3', status='HOLD', final_ledger_written=True,
                counting_equal=True, all_original_fields_equal=True, fitting_or_scoring_performed=False,
                ledger_sha256='a'*64, presence_sha256='b'*64, approved_rule_sha256='c'*64,
                evidence_sha256='d'*64, source_manifest_sha256='e'*64, original_rows=n, output_rows=n)


def canonical_panel(rows):
    return m.Panel(rows, presence(), manifest(len(rows)), 'a'*64, 'b'*64)


class BoundsTests(unittest.TestCase):
    def test_presidential_representation_alias_excluded_without_reassignment(self):
        row = v3_row()
        row.update(chamber='Presidential', hs=0, canonical_chamber='President', canonical_hs_eligibility='exclude_hs')
        panel = canonical_panel([row])
        self.assertEqual(panel.excluded_canonical_rows, 1)
        self.assertEqual(panel.pooled_uncertainty_row_count, 0)
        self.assertIn(('Presidential', 'President', 'excluded', 0), panel.routing_bridge)
        row['canonical_chamber'] = 'House'
        row['canonical_hs_eligibility'] = 'include_hs'
        with self.assertRaises(m.BoundsError):
            canonical_panel([row])

    def test_v3_missing_evidence_invalid_enum_and_flag_refused(self):
        row = v3_row(); row['canonical_office_resolution_status'] = 'source_resolved'
        with self.assertRaises(m.BoundsError): canonical_panel([row])
        row = v3_row(); row['canonical_office_resolution_status'] = 'probably_resolved'
        with self.assertRaises(m.BoundsError): canonical_panel([row])
        row = v3_row(); row['original_office_discrepancy'] = 'maybe'
        with self.assertRaises(m.BoundsError): canonical_panel([row])
        row = v3_row(); row['canonical_evidence_key'] = ''
        with self.assertRaises(m.BoundsError): canonical_panel([row])

    def test_v3_resolved_old_flag_is_not_repooled(self):
        row = v3_row(); row.update(original_office_discrepancy='True', canonical_chamber='Senate',
            canonical_office_resolution_status='source_resolved',
            canonical_office_evidence_key='2018:H00000001:C00000001')
        original = copy.deepcopy(row)
        panel = canonical_panel([row])
        self.assertEqual(panel.amount('commercial_banks', 2018, 'Senate', 'D')['known_cents'], 100)
        self.assertEqual(panel.amount('commercial_banks', 2018, 'House', 'D')['hi_cents'], 0)
        self.assertEqual(panel.pooled_uncertainty_row_count, 0)
        self.assertEqual(row, original)

    def test_v3_original_nonhs_enters_and_canonical_president_leaves_with_bridge(self):
        enter = v3_row(100, 1); enter.update(hs=0, chamber='President', canonical_office_resolution_status='source_resolved',
            canonical_office_evidence_key='2018:H00000001:C00000001')
        leave = v3_row(30, 2); leave.update(canonical_chamber='President', canonical_hs_eligibility='exclude_hs',
            canonical_office_resolution_status='source_resolved', canonical_office_evidence_key='2018:H00000001:C00000001')
        panel = canonical_panel([enter, leave])
        self.assertEqual(panel.amount('commercial_banks', 2018, 'House', 'D')['known_cents'], 100)
        self.assertEqual((panel.original_counted_rows, panel.original_counted_cents), (2, 130))
        self.assertEqual((panel.excluded_canonical_rows, panel.excluded_canonical_cents), (1, 30))
        self.assertEqual(sum(v['cents'] for v in panel.routing_bridge.values()), 130)

    def test_v3_unresolved_nonhs_pooled_even_with_known_party(self):
        row = v3_row(); row.update(hs=0, chamber='President', canonical_chamber='Unknown',
            canonical_office_resolution_status='unresolved', canonical_hs_eligibility='unresolved_potential_hs',
            canonical_classification_status='unresolved')
        panel = canonical_panel([row])
        for chamber in m.CHAMBERS:
            for party in m.PARTIES:
                out = panel.amount('commercial_banks', 2018, chamber, party)
                self.assertEqual((out['lo_cents'], out['hi_cents']), (0, 100))

    def test_v3_office_evidence_identity_and_scope_consistency(self):
        row = v3_row(); row.update(canonical_office_resolution_status='source_resolved',
            canonical_office_evidence_key='2020:H00000001:C00000001')
        with self.assertRaises(m.BoundsError): canonical_panel([row])

    def test_v3_exact_recipient_source_can_resolve_missing_original_candidate(self):
        row = v3_row(); row.update(candidate_id='', hs=0, chamber='Unknown',
            canonical_office_resolution_status='source_resolved',
            canonical_office_evidence_key='2018::C00000001')
        panel = canonical_panel([row])
        self.assertEqual(panel.amount('commercial_banks', 2018, 'House', 'D')['known_cents'], 100)
        row['canonical_office_evidence_key'] = '2018:H00000099:C00000001'
        with self.assertRaises(m.BoundsError): canonical_panel([row])
        row = v3_row(); row['canonical_hs_eligibility'] = 'exclude_hs'
        with self.assertRaises(m.BoundsError): canonical_panel([row])

    def test_v3_manifest_rejects_inventory_hash_tamper_and_row_loss(self):
        receipt = manifest(1); receipt['final_ledger_written'] = False
        with self.assertRaises(m.BoundsError): m.Panel([v3_row()], presence(), receipt, 'a'*64, 'b'*64)
        with self.assertRaises(m.BoundsError): m.Panel([v3_row()], presence(), manifest(1), 'x'*64, 'b'*64)
        with self.assertRaises(m.BoundsError): m.Panel([v3_row()], presence(), manifest(2), 'a'*64, 'b'*64)
        receipt = manifest(1); receipt['approved_rule_sha256'] = ''
        with self.assertRaises(m.BoundsError): m.Panel([v3_row()], presence(), receipt, 'a'*64, 'b'*64)

    def test_exact_intervals_reduce_to_fixed_formulas(self):
        for family, expected in [('prior_party', (10000, 20000)), ('party_R12_remaining', (11000, 19000)), ('historical_remainder', (11000, 19000))]:
            result = m.predict(frame(), family)
            self.assertEqual(tuple(r['prediction_lo_cents'] for r in result), expected)
            self.assertEqual(tuple(r['prediction_hi_cents'] for r in result), expected)
            self.assertTrue(all(r['uncertainty_rule_fallback'] is None for r in result))

    def test_input_not_mutated(self):
        original = frame(); expected = copy.deepcopy(original)
        for f in m.FAMILIES:
            m.predict(original, f)
        self.assertEqual(original, expected)

    def test_unknown_negative_and_positive_pool_over_both_chambers(self):
        panel = m.Panel([ledger(100, 'D'), ledger(-20, 'Unknown', 'Senate', order=2), ledger(30, 'Unknown', 'House', order=3)], presence())
        house = panel.amount('commercial_banks', 2018, 'House', 'D')
        senate = panel.amount('commercial_banks', 2018, 'Senate', 'R')
        self.assertEqual((house['lo_cents'], house['hi_cents']), (80, 130))
        self.assertEqual((senate['lo_cents'], senate['hi_cents']), (-20, 30))
        self.assertEqual(house['unknown_net_cents'], 10)
        self.assertEqual(house['unknown_rows'], 2)

    def test_due_and_transaction_cuts_apply_to_unknowns_too(self):
        rows = [ledger(100, 'D'), ledger(20, 'Unknown', order=2, due='2018-08-01'),
                ledger(30, 'Unknown', order=3, day='2018-07-01'), ledger(-5, 'Unknown', order=4)]
        panel = m.Panel(rows, presence())
        result = panel.amount('commercial_banks', 2018, 'House', 'D', issue='2018-07-20', cut='2018-06-30')
        self.assertEqual((result['lo_cents'], result['hi_cents']), (95, 100))

    def test_known_office_discrepancy_is_pooled_once_over_both_chambers(self):
        row = dict(ledger(100, 'D'), party_office_discrepancy='True')
        panel = m.Panel([row], presence())
        for chamber in m.CHAMBERS:
            for party in m.PARTIES:
                result = panel.amount('commercial_banks', 2018, chamber, party)
                self.assertEqual((result['lo_cents'], result['hi_cents']), (0, 100))
                self.assertEqual(result['known_cents'], 0)
        self.assertEqual(panel.pooled_uncertainty_row_count, 1)

    def test_direct_remaining_cancels_identical_uncertain_rows(self):
        rows = [ledger(100, 'Unknown'), ledger(-20, 'Unknown', order=2),
                ledger(30, 'Unknown', order=3, day='2018-09-01', due='2018-10-20'),
                ledger(500, 'Unknown', order=4, day='2018-10-01', due='2023-01-31')]
        panel = m.Panel(rows, presence())
        remaining = panel.remaining('commercial_banks', 2018, 'House', 'D', '2020-07-20', '2018-07-20', '2018-06-30')
        self.assertEqual((remaining['lo_cents'], remaining['hi_cents']), (0, 30))
        self.assertEqual(remaining['unknown_rows'], 1)

    def test_historical_final_excludes_rows_due_after_target_issue(self):
        rows = [ledger(100, 'D', year=2016, due='2017-01-31', day='2016-06-30'),
                ledger(500, 'D', year=2016, order=2, due='2023-01-31', day='2016-06-30')]
        panel = m.Panel(rows, presence())
        features = panel.frame('commercial_banks', 'House', 2018, 'm18')
        self.assertEqual(features['history'][-1]['final']['D']['hi_cents'], 100)
        self.assertEqual(panel.amount('commercial_banks', 2016, 'House', 'D')['hi_cents'], 600)

    def test_missing_presence_is_not_zero(self):
        p = presence(); p[0]['first_due_any_raw'] = ''
        panel = m.Panel([], p)
        with self.assertRaises(m.BoundsError):
            panel.amount(p[0]['sector'], p[0]['cycle'], p[0]['chamber'], 'D')
        with self.assertRaises(m.BoundsError):
            m.evaluate(panel)

    def test_unknown_labels_and_duplicate_rows_refused(self):
        with self.assertRaises(m.BoundsError):
            m.Panel([ledger(10, 'not-a-party')], presence())
        with self.assertRaises(m.BoundsError):
            m.Panel([ledger(10, 'D'), ledger(20, 'R')], presence())

    def test_r12_interval_contains_every_positive_box_corner(self):
        f = frame()
        for h in f['history']:
            h['final']['D'] = bound(90, 110)
            h['observed']['D'] = bound(40, 60)
        f['observed']['D'] = bound(45, 65)
        output = m.predict(f, 'party_R12_remaining')[0]
        # All eight feature endpoints; prior B is tied to the third final F.
        for values in itertools.product((90, 110), (90, 110), (90, 110), (40, 60), (40, 60), (40, 60), (45, 65)):
            fs, os, current = values[:3], values[3:6], values[6]
            completion = min(1, ((os[0]/fs[0])*(os[1]/fs[1])*(os[2]/fs[2])) ** (1/3))
            point = current + (1-completion)*fs[-1]
            self.assertGreaterEqual(point + 1e-12, output['prediction_lo_cents'])
            self.assertLessEqual(point - 1e-12, output['prediction_hi_cents'])

    def test_r12_zero_completion_and_cap(self):
        f = frame()
        for h in f['history']:
            h['observed']['D'] = bound(0)
            h['observed']['R'] = bound(24000)
        rows = m.predict(f, 'party_R12_remaining')
        self.assertEqual(rows[0]['prediction_lo_cents'], 16000)
        self.assertEqual(rows[1]['prediction_lo_cents'], 9000)
        self.assertEqual(rows[0]['trace']['completion_lo'], 0)
        self.assertEqual(rows[1]['trace']['completion_hi'], 1)

    def test_r12_domain_ambiguity_is_explicit_full_prior_fallback(self):
        f = frame(); f['history'][0]['final']['D'] = bound(0, 10000)
        f['history'][-1]['final']['D'] = bound(8000, 12000)
        result = m.predict(f, 'party_R12_remaining')[0]
        self.assertEqual((result['prediction_lo_cents'], result['prediction_hi_cents']), (8000, 12000))
        self.assertIsNotNone(result['uncertainty_rule_fallback'])

    def test_historical_remainder_bounds_and_negative_box_fallback(self):
        f = frame()
        for h in f['history']:
            h['final']['D'] = bound(90, 110)
            h['observed']['D'] = bound(40, 60)
            h['remaining']['D'] = bound(30, 70)
        f['observed']['D'] = bound(45, 65)
        result = m.predict(f, 'historical_remainder')[0]
        self.assertEqual((result['prediction_lo_cents'], result['prediction_hi_cents']), (75, 135))
        f['observed']['D'] = bound(-40, 65)
        result = m.predict(f, 'historical_remainder')[0]
        self.assertEqual((result['prediction_lo_cents'], result['prediction_hi_cents']), (90, 110))
        self.assertIsNotNone(result['uncertainty_rule_fallback'])

    def test_certification_inequalities_and_error_bound(self):
        prediction = m.predict(frame(), 'prior_party')[0]
        prediction.update(prediction_lo_cents=90, prediction_hi_cents=110, prediction_width_cents=20)
        result = m.certify(prediction, bound(100))
        self.assertTrue(result['certified_within10'])
        self.assertAlmostEqual(result['ape_upper_bound'], .1)
        result = m.certify(prediction, bound(90, 110))
        self.assertFalse(result['certified_within10'])
        self.assertFalse(result['certified_within20'])
        self.assertAlmostEqual(result['ape_upper_bound'], 20/90)
        self.assertEqual(result['absolute_error_upper_cents'], 20)

    def test_zero_crossing_truth_not_certified_not_counted_as_actual_miss(self):
        prediction = m.predict(frame(), 'prior_party')[0]
        result = m.certify(prediction, bound(-10, 100))
        self.assertFalse(result['certified_within20'])
        self.assertIsNone(result['ape_upper_bound'])
        self.assertNotIn('actual_miss', result)
        self.assertEqual(m.metrics([result])['nonpositive_truth_lower_n'], 1)

    def test_future_truth_and_incomplete_party_features_refused(self):
        f = frame(); f['actual'] = bound(123)
        with self.assertRaises(m.BoundsError): m.predict(f, 'prior_party')
        f = frame(); f['history'][0]['cycle'] = 2020
        with self.assertRaises(m.BoundsError): m.predict(f, 'prior_party')
        f = frame(); del f['observed']['R']
        with self.assertRaises(m.BoundsError): m.predict(f, 'prior_party')

    def test_full_grid_retains_zero_and_domain_fallback_cells(self):
        result = m.evaluate(m.Panel([], presence()))
        self.assertEqual(len(result['predictions']), 960)
        for h in m.HORIZONS:
            for f in m.FAMILIES:
                self.assertEqual(sum(r['horizon'] == h and r['family'] == f for r in result['predictions']), 80)
        self.assertEqual(result['strict_classification_admission'], 'HOLD')
        self.assertFalse(result['live_activation'])
        self.assertTrue(all(not r['certified_within20'] for r in result['predictions']))
        whole = [r for r in result['metrics'] if all(r[k] == 'ALL' for k in ('horizon', 'sector', 'chamber', 'party', 'target'))]
        self.assertEqual(len(whole), 3)
        self.assertTrue(all(r['n'] == 320 for r in whole))


if __name__ == '__main__':
    unittest.main()
