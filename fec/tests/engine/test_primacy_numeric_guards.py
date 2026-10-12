"""Boundary contracts: invalid evidence must not acquire a valid board score."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tier_util as TU
from engine import per_area_grade as PAG


class ScoreDomain(unittest.TestCase):
    def score(self, factor=.5, grade=.5, mode='per_area_v1'):
        # Pins the D-032 'whole' product (RE-1's revert form) by name.
        return TU.primacy_scores({'M': {'leg': .5}}, {'M': factor},
                                 mode=mode, grades={'M': grade},
                                 grade_form='whole')['M']

    def test_current_positive_domain_keeps_product_and_monotonicity(self):
        for factor in (0, .1, .5, 1):
            values=[self.score(factor,g) for g in (0,.5,1)]
            self.assertEqual(values, sorted(values))
            self.assertAlmostEqual(values[0], .8*factor)
            self.assertAlmostEqual(values[-1], factor)

    def test_negative_factor_refused_only_for_grade_product(self):
        with self.assertRaisesRegex(ValueError, 'negative primacy factor'):
            self.score(-.5)
        self.assertEqual(self.score(-.5, mode='factor'), -.5)
        self.assertEqual(self.score(-.5, mode='legxfactor'), -.25)

    def test_invalid_factor_cannot_reach_sort(self):
        for mode in ('per_area_v1','factor','legxfactor'):
            for value in (float('nan'),float('inf'),float('-inf'),True,False,'broken'):
                with self.subTest(mode=mode,value=value), self.assertRaises(ValueError):
                    self.score(value,mode=mode)

    def test_invalid_grade_cannot_reach_product(self):
        for value in (float('nan'),float('inf'),float('-inf'),True,False,-.001,1.001,'broken'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.score(grade=value)

    def test_bad_legislative_numeric_is_not_a_roster_member(self):
        for value in (float('nan'),float('inf'),True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TU.primacy_scores({'M':{'leg':value}}, {'M':.5},
                                  mode='per_area_v1',grades={'M':.5})

    def test_artifact_loader_validates_grade_domain(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'grade.json'
            for value in (float('nan'),float('inf'),True,-.01,1.01):
                path.write_text(json.dumps({'schema':TU.PER_AREA_GRADE_SCHEMA,
                    'members':{'M':{'chamber':'House','grade_percentile':value}}}),encoding='utf-8')
                with self.subTest(value=value), self.assertRaises(ValueError):
                    TU.load_per_area_grade(root,path='grade.json')
            path.write_text(json.dumps({'schema':TU.PER_AREA_GRADE_SCHEMA,
                'members':{'M':{'chamber':'House','grade_percentile':.5}}}),encoding='utf-8')
            self.assertEqual(TU.load_per_area_grade(root,path='grade.json'),{'M':('House',.5)})


class ArtifactNumericBoundary(unittest.TestCase):
    invalid = (True, False, float('nan'), float('inf'), float('-inf'), 'broken')

    def factors(self, values, loader):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'factors.json').write_text(
                json.dumps({'members': {'M': values}}), encoding='utf-8')
            return loader(root, path='factors.json')

    def ledger(self, events, loader):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'events.ndjson').write_text(
                ''.join(json.dumps(event)+'\n' for event in events), encoding='utf-8')
            return loader(root, path='events.ndjson')

    def test_raw_factors_reject_booleans_and_nonfinite_before_coercion(self):
        for value in self.invalid:
            for field, loader in (('factor_shrunk', TU.load_primacy_factors),
                                  ('factor_ranked', TU.load_primacy_ranked)):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.factors({'factor_shrunk': .5, field: value}, loader)

    def test_ranked_fallback_also_validates_raw_shrunk_factor(self):
        for value in self.invalid + (None,):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.factors({'factor_shrunk': value}, TU.load_primacy_ranked)
        self.assertEqual(self.factors({'factor_ranked': None, 'factor_shrunk': -.5},
                                      TU.load_primacy_ranked), {'M': -.5})

    def test_valid_signed_factors_keep_their_existing_meaning(self):
        for value in (-.5, 0, .5):
            values = {'factor_shrunk': value, 'factor_ranked': value}
            self.assertEqual(self.factors(values, TU.load_primacy_factors), {'M': value})
            self.assertEqual(self.factors(values, TU.load_primacy_ranked), {'M': value})

    def test_raw_primary_credit_is_finite_nonnegative_and_not_boolean(self):
        for value in self.invalid + (None, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.factors({'mix': {'cu_primary': value}}, TU.load_primacy_cu_credit)
        for value in (0, .75, 10):
            self.assertEqual(self.factors({'mix': {'cu_primary': value}},
                                          TU.load_primacy_cu_credit), {'M': value})
        self.assertEqual(self.factors({}, TU.load_primacy_cu_credit), {'M': 0})

    def test_absent_artifacts_keep_explicit_missing_file_contract(self):
        with tempfile.TemporaryDirectory() as root:
            for loader in (TU.load_primacy_factors, TU.load_primacy_ranked,
                           TU.load_primacy_cu_credit, TU.load_open_breaches,
                           TU.load_delivery_credit):
                self.assertEqual(loader(root, path='absent.json'), {})

    def test_raw_conduct_fields_validate_before_coercion(self):
        for field in ('severity', 'leverage_mult'):
            for value in self.invalid + (None, -1):
                event = {'member': 'M', 'severity': 3, 'leverage_mult': 1, field: value}
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.ledger([event], TU.load_open_breaches)

    def test_zero_leverage_remains_zero_and_absence_defaults_to_one(self):
        event = {'member': 'M', 'severity': 3, 'leverage_mult': 0}
        loaded = self.ledger([event], TU.load_open_breaches)['M']
        self.assertEqual(loaded['effective_severity'], 0)
        self.assertEqual(loaded['event_detail'][0]['weighted_severity'], 0)
        self.assertEqual(loaded['event_detail'][0]['leverage_mult'], 0)
        event.pop('leverage_mult')
        self.assertEqual(self.ledger([event], TU.load_open_breaches)['M']['effective_severity'], 3)
        event.pop('severity')
        self.assertEqual(self.ledger([event], TU.load_open_breaches), {})

    def test_raw_direct_delivery_is_finite_nonnegative_and_not_boolean(self):
        for value in self.invalid + (-1,):
            event = {'member': 'M', 'delivered_credit': value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ledger([event], TU.load_delivery_credit)

    def test_derived_delivery_validates_each_active_quantity(self):
        for field in ('act_weight', 'instrument_mult'):
            for value in self.invalid + (None, -1):
                event = {'member': 'M', 'act_weight': 2, 'instrument_mult': 3, field: value}
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.ledger([event], TU.load_delivery_credit)

    def test_delivery_zero_and_null_direct_fallback_remain_explicit(self):
        event = {'member': 'M', 'delivered_credit': None, 'act_weight': 2, 'instrument_mult': 3}
        self.assertEqual(self.ledger([event], TU.load_delivery_credit)['M']['delivered'], 6)
        event['delivered_credit'] = 0
        self.assertEqual(self.ledger([event], TU.load_delivery_credit), {})
        self.assertEqual(self.ledger([{'member': 'M'}], TU.load_delivery_credit), {})
        event.pop('delivered_credit')
        event['act_weight'] = 0
        self.assertEqual(self.ledger([event], TU.load_delivery_credit), {})

    def test_closed_ledger_records_do_not_enter_active_numeric_domain(self):
        invalid_event = {'member': 'M', 'status': 'closed', 'severity': True,
                         'leverage_mult': float('nan'), 'delivered_credit': True}
        self.assertEqual(self.ledger([invalid_event], TU.load_open_breaches), {})
        self.assertEqual(self.ledger([invalid_event], TU.load_delivery_credit), {})

    def test_finite_inputs_cannot_overflow_product_or_aggregate(self):
        cases = (
            (TU.load_open_breaches, [{'member': 'M', 'severity': 1e308, 'leverage_mult': 2}]),
            (TU.load_open_breaches, [{'member': 'M', 'severity': 1e308}]*2),
            (TU.load_delivery_credit, [{'member': 'M', 'act_weight': 1e308, 'instrument_mult': 2}]),
            (TU.load_delivery_credit, [{'member': 'M', 'delivered_credit': 1e308}]*2),
        )
        for loader, events in cases:
            with self.subTest(loader=loader.__name__, events=events), self.assertRaises(ValueError):
                self.ledger(events, loader)


class KnownTenure(unittest.TestCase):
    today=date(2026,9,20)

    def years(self, terms):
        return PAG.years_served({'M':terms},'M',self.today)

    def test_missing_future_and_out_of_window_tenure_refused(self):
        for terms in ([],[('rep','2027-01-03','2029-01-03')],
                      [('rep','2017-01-03','2019-01-03')]):
            with self.subTest(terms=terms), self.assertRaisesRegex(ValueError,'no dated service'):
                self.years(terms)

    def test_known_first_day_keeps_short_service_floor(self):
        self.assertEqual(self.years([('rep','2026-09-20','2027-01-03')]),(.5,'service_dates'))

    def test_known_short_service_keeps_floor(self):
        self.assertEqual(self.years([('rep','2026-09-01','2027-01-03')]),(.5,'service_dates'))

    def test_bad_interval_not_silently_removed_from_denominator(self):
        valid=('rep','2025-01-03','2027-01-03')
        for bad in (('rep','bad','2027-01-03'),('rep',None,'2027-01-03'),
                    ('rep','2026-01-03','2025-01-03'),('rep','2025-01-03','2025-01-03')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.years([valid,bad])

    def test_overlap_is_counted_once(self):
        expected=(self.today-date(2021,1,3)).days/365.25
        actual,basis=self.years([('rep','2019-01-03','2023-01-03'),
                                ('sen','2022-01-03','2027-01-03')])
        self.assertAlmostEqual(actual,expected)
        self.assertEqual(basis,'service_dates')


class ServiceSpineBoundary(unittest.TestCase):
    today = date(2026, 9, 20)
    valid = "  - type: rep\n    start: '2025-01-03'\n    end: '2027-01-03'\n"

    def person(self, bioguide, terms):
        return "- id:\n    bioguide: %s\n  terms:\n%s" % (bioguide, terms)

    def read(self, current, historical=''):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'legislators-current.yaml').write_text(current, encoding='utf-8')
            Path(directory, 'legislators-historical.yaml').write_text(historical, encoding='utf-8')
            return PAG.read_service_spine(directory)

    def test_incomplete_term_cannot_hide_behind_another_valid_term(self):
        incomplete_dates = (
            "    end: '2025-01-03'\n",
            "    start: '2021-01-03'\n",
            "",
            "    start: ''\n    end: '2025-01-03'\n",
            "    start: '2021-01-03'\n    end: ''\n",
        )
        for kind in ('rep', 'sen'):
            for dates in incomplete_dates:
                for origin in ('current', 'historical'):
                    with self.subTest(kind=kind, dates=dates, origin=origin):
                        bad = "  - type: %s\n%s" % (kind, dates)
                        if origin == 'current':
                            spine = self.read(self.person('M000001', self.valid + bad))
                        else:
                            spine = self.read(self.person('M000001', self.valid),
                                              self.person('M000001', bad))
                        self.assertEqual(len(spine['M000001']), 2)
                        with self.assertRaisesRegex(ValueError, 'invalid service dates for M000001'):
                            PAG.years_served(spine, 'M000001', self.today)

    def test_incomplete_unranked_history_does_not_block_valid_ranked_member(self):
        spine = self.read(self.person('M000001', self.valid),
                          self.person('H000001', "  - type: sen\n    start: '1890-01-03'\n"))
        self.assertEqual(spine['H000001'], [('sen', '1890-01-03', None)])
        result = PAG.build(
            {'Ranked': {'bioguide': 'M000001', 'chamber': 'House'}},
            {}, {}, spine, {}, {'Ranked': 0.0}, {'Ranked': 0},
            ['Ranked'], self.today,
        )
        self.assertAlmostEqual(result['years']['Ranked'],
                               (self.today - date(2025, 1, 3)).days / 365.25)

    def test_valid_duplicate_terms_and_irrelevant_offices_keep_reader_contract(self):
        current = self.person('M000001', self.valid + "  - type: gov\n")
        spine = self.read(current, self.person('M000001', self.valid))
        self.assertEqual(spine['M000001'], [('rep', '2025-01-03', '2027-01-03')])


if __name__=='__main__':
    unittest.main()
