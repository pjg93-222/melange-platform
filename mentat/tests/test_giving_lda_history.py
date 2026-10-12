"""The lobbying history the Giving Forecast page draws (catalog.anchors.industries.<id>.lda_history) against Melange's own LDA aggregation.

The page draws Melange's stacked LDA card (hired firms = income_total, in-house = expenses_total, never added by the data; the partial year hatched), so every number it is
given must be the number Melange prints. Melange's card reads fec/lobbying/sector_spend.py's output; this file reads that same output's JSON (sector_spend.json, beside the CSV the
anchors builder reads) and compares dollar for dollar, to the cent, for every industry that has a Melange-LDA sector. The reconciliation is skipped where Melange's output or the
published anchors are not on the machine; the shape tests are hermetic.
"""
import copy
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'forecast' / 'anchors'))
sys.path.insert(0, str(ROOT / 'forecast' / 'contracts'))
import build_giving_anchors as bga  # noqa: E402
import giving_forecast as gf  # noqa: E402
from test_giving_cycle_model import ANCHORS, CycleWorld  # noqa: E402

MELANGE_CSV = bga.default_lda_csv()
MELANGE_JSON = MELANGE_CSV.with_name('sector_spend.json')
MELANGE_RECEIPTS = MELANGE_CSV.parents[1] / 'fullscope_receipts.json'          # the anchors builder's own derivation (Melange's data-root layout)
LD2_DEADLINES = {'Q1': (0, 4, 20), 'Q2': (0, 7, 20), 'Q3': (0, 10, 20), 'Q4': (1, 1, 20)}          # (years after, month, day): the oracle keeps its own copy of the rule
cents = lambda value: round(float(value) * 100)       # noqa: E731  (the CSV carries up to two decimals)


def melange_as_of(year):
    """The producer's sweep date of `year` (fullscope_receipts.json, scope_sweeps.<year>.sweep_through_dt_posted), read straight from the file."""
    with MELANGE_RECEIPTS.open(encoding='utf-8-sig') as stream:
        return date.fromisoformat(json.load(stream)['scope_sweeps'][str(year)]['sweep_through_dt_posted'][:10])


def melange_years(sector):
    """{year: Melange's annual entry} of one sector in sector_spend.json (the card's own rows)."""
    with MELANGE_JSON.open(encoding='utf-8-sig') as stream:
        return {entry['year']: entry for entry in json.load(stream)['sectors'][sector]['years']}


def with_counts(anchors):
    """A copy of the contract's real anchors with the counts block a rebuilt snapshot carries (bank sector, hand-made numbers)."""
    out = copy.deepcopy(anchors)
    out['industries']['commercial_banks']['lda']['counts'] = {'2023': {'organizations': 92, 'lobbyists': 447}, '2024': {'organizations': 90, 'lobbyists': 440},
                                                               '2025': {'organizations': 91, 'lobbyists': 445}, '2026': {'organizations': 88, 'lobbyists': 390}}
    return out


class HistoryShapeTests(unittest.TestCase):
    def test_a_ready_block_gives_annual_rows_the_partial_row_and_the_source(self):
        lda = with_counts(ANCHORS)['industries']['commercial_banks']['lda']
        history = gf._lda_history(lda)
        fees, house = lda['series']['income_total'], lda['series']['expenses_total']
        self.assertEqual([row['year'] for row in history['annual']], sorted(int(y) for y in fees['annual']))
        for row in history['annual']:
            self.assertEqual((row['hired_firms'], row['in_house']), (fees['annual'][str(row['year'])], house['annual'][str(row['year'])]))
            self.assertEqual(set(row), {'year', 'hired_firms', 'in_house', 'orgs', 'lobbyists'})
        partial = history['partial']
        self.assertEqual((partial['year'], partial['through_quarter'], partial['hired_firms'], partial['in_house']),
                         (fees['partial']['year'], fees['partial']['through_quarter'], fees['partial']['total'], house['partial']['total']))
        self.assertEqual((partial['orgs'], partial['lobbyists']), (88, 390))
        self.assertEqual([(r['year'], r['orgs'], r['lobbyists']) for r in history['annual'] if r['year'] == 2025], [(2025, 91, 445)])
        self.assertEqual((history['sector'], history['source_file'], history['source_sha256'], history['through']), (lda['sector'], lda['source_file'], lda['source_sha256'], lda['through']))

    def test_the_two_series_are_never_added_in_the_history(self):
        history = gf._lda_history(with_counts(ANCHORS)['industries']['commercial_banks']['lda'])
        self.assertTrue(all('total' not in row for row in history['annual'] + [history['partial']]))

    def test_a_snapshot_without_counts_leaves_them_out_and_an_industry_without_a_sector_has_no_history(self):
        banks = gf._lda_history(copy.deepcopy(ANCHORS)['industries']['commercial_banks']['lda'])
        self.assertTrue(all(row['orgs'] is None and row['lobbyists'] is None for row in banks['annual'] + [banks['partial']]))
        self.assertIsNone(gf._lda_history(ANCHORS['industries']['property_casualty']['lda']))

    def test_the_catalog_carries_it_per_industry_beside_the_anchor_status(self):
        with tempfile.TemporaryDirectory() as tmp, CycleWorld(tmp, anchors=with_counts(ANCHORS)):
            industries = gf.catalog('2026-10-07')['anchors']['industries']
        self.assertEqual(industries['commercial_banks']['lda_history']['partial']['lobbyists'], 390)
        self.assertEqual(industries['commercial_banks']['lda_history'], gf._lda_history(with_counts(ANCHORS)['industries']['commercial_banks']['lda']))
        self.assertIsNone(industries['property_casualty']['lda_history'])


@unittest.skipUnless(MELANGE_JSON.is_file() and MELANGE_RECEIPTS.is_file(), 'Melange sector_spend.json or fullscope_receipts.json is not on this machine')
class MelangeReconciliationTests(unittest.TestCase):
    """The published anchors' history equals Melange's sector_spend.json, year by year, to the cent."""

    @classmethod
    def setUpClass(cls):
        path = gf.anchor_path()
        if not path.is_file():
            raise unittest.SkipTest('no published anchor snapshot')
        with path.open(encoding='utf-8-sig') as stream:
            cls.snapshot = json.load(stream)
        cls.histories = {ident: gf._lda_history(ind['lda']) for ident, ind in cls.snapshot['industries'].items()}
        cls.ready = {ident: h for ident, h in cls.histories.items() if h}

    def test_there_are_industries_to_reconcile(self):
        self.assertTrue({'commercial_banks', 'credit_unions', 'telecom'} <= set(self.ready), sorted(self.ready))

    def test_history_equals_sector_spend_json_to_the_cent(self):
        for ident, history in self.ready.items():
            with self.subTest(industry=ident):
                years = melange_years(history['sector'])
                self.assertEqual(sorted(years), [row['year'] for row in history['annual']] + ([history['partial']['year']] if history['partial'] else []), 'the same years')
                for row in history['annual'] + ([history['partial']] if history['partial'] else []):
                    entry = years[row['year']]
                    partial = row is history['partial']
                    self.assertEqual(entry['is_partial_year'], partial, str(row['year']))
                    self.assertEqual(cents(row['hired_firms']), cents(entry['income_total']), f"{ident} {row['year']} hired firms")
                    self.assertEqual(cents(row['in_house']), cents(entry['expenses_total']), f"{ident} {row['year']} in-house")
                    self.assertEqual(row['orgs'], entry['distinct_organizations'], f"{ident} {row['year']} organizations")
                    self.assertEqual(row['lobbyists'], entry['distinct_named_lobbyists'], f"{ident} {row['year']} named lobbyists")
                if history['partial']:
                    year, as_of = history['partial']['year'], melange_as_of(history['partial']['year'])
                    last = max(q['quarter'] for q in years[year]['quarters']                                    # a filing AND its LD-2 deadline on or before the sweep date
                               if q['filings'] and date(year + LD2_DEADLINES[q['quarter']][0], *LD2_DEADLINES[q['quarter']][1:]) <= as_of)
                    self.assertEqual(history['partial']['through_quarter'], last)

    def test_the_snapshot_was_built_from_the_csv_beside_that_json(self):
        import hashlib
        sha = hashlib.sha256(MELANGE_CSV.read_bytes()).hexdigest()
        for ident, history in self.ready.items():
            self.assertEqual(history['source_sha256'], sha, f'{ident}: rebuild the anchors (build_giving_anchors.py); the snapshot predates the Melange output')


if __name__ == '__main__':
    unittest.main()
