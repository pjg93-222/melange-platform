"""N1a -- DESK_PRESS_SOURCE: the desk reads the same press stream as the engine.

Packet: audits/laundry_list_closeout_20260918, FINDINGS.md SS A A1;
receipts laneA/receipts/r8_truncation_and_press_overlap.*, r9_engine_only_press.*,
fixN1/n1_desk_press_union.*.

THE DEFECT.  The ranking engine reads press through fec/press_stream.py (the historical
Draft Systems tone file plus data/stance/press_refresh_tone.csv); the member desk read
workbook sheet 'Press Release Articles'.  No script in the chain writes that sheet -- it
arrives from the external R pipeline -- so the desk showed a frozen corpus: 291 engine
rows, 221 of them scoring, were invisible on the desks of the members whose rank they
moved.

THE UNION, AND WHY THE SHEET WINS THE OVERLAP.  Under 'engine' the desk walks every sheet
row exactly as before and then walks the engine rows the sheet has no key for.  The row SET
is the union; the sheet supplies every row it has.  That precedence is MEASURED, not
cautious (receipt fixN1/n1_desk_press_union.out.txt): on the 2,376 keys both sources carry,
the sheet and the CSV disagree on date (2,138 keys -- the CSV stores 'M/D/YYYY', which the
desk's own fdate() blanks), on score_graded (1,207), on stance_src (493), and on the
DISPLAYED STANCE LABEL (57 keys, e.g. sheet 'oppose' vs CSV 'support').  Taking the CSV on
the overlap would blank dates, move graded labels and relabel 57 press stances -- and press
classification is the owner's domain, never a producer's.  So the sheet's bytes are what
renders for every row the desk already shows, and the engine only ADDS.

Synthetic fixtures only: no workbook, no corpus, no artifact, no MONEYBALL_ROOT.  Nothing
under fec/ is executed -- fec/surfaces/desk_press_source.py and fec/press_stream.py are
rules modules with no top-level I/O, and make_member_desk.py (import == execution) is read
as TEXT, the pattern test_crosspost_scoring.py established.
"""
import importlib
import os
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import press_stream as PS
import surfaces.desk_press_source as DPS

DESK_SOURCE = FEC / 'surfaces' / 'make_member_desk.py'
HELPER_SOURCE = FEC / 'surfaces' / 'desk_press_source.py'

# The sheet header, verbatim from 'Press Release Articles' (28 columns, probe receipt
# fixN1/n1_field_map.out.txt).  The two tone CSVs carry the same 28 names in the same
# order; the refresh file adds 'source_run' and 'source', which the desk never reads.
SHEET_HEADER = ('member', 'bioguide', 'chamber', 'state', 'party', 'date', 'title', 'url',
                'categories', 'relevance', 'score', 'score_graded', 'score_label', 'stance',
                'stance_sign', 'p_support', 'p_oppose', 'p_neutral', 'stance_conf', 'n_words',
                'lsd_pos', 'lsd_neg', 'lsd_net', 'lsd_net_norm', 'lsd_charge', 'stance_src',
                'press_value', 'recency_weight')
HEADER_INDEX = {h: i for i, h in enumerate(SHEET_HEADER)}

# Exactly the columns make_member_desk.load_press_and_bills reads with pv(r, k).
DESK_READ = ('member', 'bioguide', 'url', 'stance_src', 'score', 'stance_sign',
             'score_graded', 'title', 'stance', 'stance_conf', 'date', 'categories',
             'relevance')

ALIAS_A = 'https://www.britt.senate.gov/news/press-releases/a-long-descriptive-release-slug'
ALIAS_B = 'https://www.britt.senate.gov/press-releases/a-long-descriptive-release-slug'
FRESH = 'https://riley.house.gov/media/press-releases/riley-statement-on-interchange-fees'
DESKONLY = 'https://welch.senate.gov/news/press-releases/welch-only-on-the-sheet-today'


def sheet_row(**values):
    """One 'Press Release Articles' row as openpyxl hands it over: a 28-wide tuple."""
    row = [None] * len(SHEET_HEADER)
    for name, value in values.items():
        row[HEADER_INDEX[name]] = value
    return tuple(row)


def csv_row(**values):
    """One load_tone_rows() row: a dict keyed by the CSV header."""
    base = {name: '' for name in SHEET_HEADER}
    base.update(values)
    return base


def pv(row, key):
    """The desk's own accessor, copied from make_member_desk.py:1650."""
    return row[HEADER_INDEX[key]] if key in HEADER_INDEX else None


class DialFailsClosed(unittest.TestCase):
    """A dial that changes what the desk SHOWS must never fall through to a default."""

    def test_the_default_is_the_new_behaviour(self):
        self.assertEqual(DPS.DESK_PRESS_SOURCE,
                         (os.environ.get('DESK_PRESS_SOURCE') or 'engine').strip().lower())
        self.assertIn("os.environ.get('DESK_PRESS_SOURCE') or 'engine'",
                      HELPER_SOURCE.read_text(encoding='utf-8'))

    def test_an_unrecognised_value_fails_at_import(self):
        previous = os.environ.get('DESK_PRESS_SOURCE')
        try:
            for bad in ('workbook', 'both', 'ENGINE2', 'engine sheet', '0', '1'):
                os.environ['DESK_PRESS_SOURCE'] = bad
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        importlib.reload(DPS)
        finally:
            if previous is None:
                os.environ.pop('DESK_PRESS_SOURCE', None)
            else:
                os.environ['DESK_PRESS_SOURCE'] = previous
            importlib.reload(DPS)

    def test_an_empty_value_means_unset_like_every_other_dial(self):
        """(os.environ.get(...) or default) is the shipped pattern -- DESK_MEMBERSHIP_SOURCE,
        PRESS_URL_CANON and CROSSPOST_KEY_CHARS all read an empty string as 'not set'."""
        previous = os.environ.get('DESK_PRESS_SOURCE')
        try:
            os.environ['DESK_PRESS_SOURCE'] = ''
            self.assertEqual(importlib.reload(DPS).DESK_PRESS_SOURCE, 'engine')
        finally:
            if previous is None:
                os.environ.pop('DESK_PRESS_SOURCE', None)
            else:
                os.environ['DESK_PRESS_SOURCE'] = previous
            importlib.reload(DPS)

    def test_the_sheet_position_is_reachable_from_the_environment(self):
        previous = os.environ.get('DESK_PRESS_SOURCE')
        try:
            os.environ['DESK_PRESS_SOURCE'] = ' SHEET '
            reloaded = importlib.reload(DPS)
            self.assertEqual(reloaded.DESK_PRESS_SOURCE, 'sheet')
            rows = iter([sheet_row(url=FRESH, member='Josh Riley', bioguide='R000106')])
            out, ledger = reloaded.desk_press_rows(rows, HEADER_INDEX)
            self.assertIs(out, rows)
            self.assertEqual(ledger['press_source'], 'sheet')
        finally:
            if previous is None:
                os.environ.pop('DESK_PRESS_SOURCE', None)
            else:
                os.environ['DESK_PRESS_SOURCE'] = previous
            importlib.reload(DPS)

    def test_an_unrecognised_argument_fails_closed_too(self):
        for bad in ('workbook', 'both', 'sheets'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    DPS.desk_press_rows([], HEADER_INDEX, source=bad)

    def test_both_positions_are_named_in_the_error(self):
        with self.assertRaises(ValueError) as caught:
            DPS.desk_press_rows([], HEADER_INDEX, source='workbook')
        self.assertIn('engine', str(caught.exception))
        self.assertIn('sheet', str(caught.exception))


class SheetDialIsTodaysPath(unittest.TestCase):
    """DESK_PRESS_SOURCE='sheet' must be the pre-2026-09-19 path, not a re-implementation."""

    def test_the_iterable_is_handed_back_unchanged(self):
        rows = iter([sheet_row(url=FRESH, member='Josh Riley', bioguide='R000106')])
        out, ledger = DPS.desk_press_rows(rows, HEADER_INDEX, source='sheet')
        self.assertIs(out, rows)
        self.assertEqual(ledger['press_source'], 'sheet')

    def test_the_engine_stream_is_never_read(self):
        def explode(*args, **kwargs):
            raise AssertionError('load_tone_rows must not run under the sheet dial')
        DPS.desk_press_rows(iter([]), HEADER_INDEX, source='sheet', load_rows=explode)

    def test_the_sheet_ledger_claims_no_engine_rows(self):
        _, ledger = DPS.desk_press_rows(iter([]), HEADER_INDEX, source='sheet')
        self.assertEqual(ledger['schema'], DPS.PRESS_SOURCE_LEDGER_SCHEMA)
        self.assertEqual(ledger['press_engine_rows_appended'], 0)
        self.assertEqual(ledger['press_engine_rows_read'], 0)


class UnionRowSet(unittest.TestCase):
    """Nothing the desk shows today disappears; the engine only adds."""

    def setUp(self):
        self.sheet = [
            sheet_row(member='Katie Britt', bioguide='B001319', url=ALIAS_A,
                      title='Basel III', date='2023-11-15', stance='support',
                      stance_sign=1, score=0, score_graded=0.4, stance_conf=0.99,
                      categories='Interchange', relevance='credit-union mention',
                      stance_src='debate'),
            # the SECOND url form of the same release: A5 merged it out of the ENGINE
            # stream, and the desk has shown it since the R pipeline wrote it
            sheet_row(member='Katie Britt', bioguide='B001319', url=ALIAS_B,
                      title='Basel III', date='2023-11-15', stance='support',
                      stance_sign=1, score=0, score_graded=0.4, stance_conf=0.99,
                      categories='Interchange', relevance='credit-union mention',
                      stance_src='debate'),
            # a bill-cosponsorship pseudo-row: routed to legislation, never a release
            sheet_row(member='Katie Britt', bioguide='B001319', url='billstatus:119-S-1234',
                      title='S.1234', date='2025-04-01', stance_src='bill', score=1),
            # a row only the sheet has: it must survive untouched
            sheet_row(member='Peter Welch', bioguide='W000800', url=DESKONLY,
                      title='Sheet only', date='2024-06-02', stance='neutral',
                      stance_sign=0, stance_src='debate'),
        ]
        self.engine = [
            # already on the sheet under the OTHER url form
            csv_row(member='Katie Britt', bioguide='B001319', url=ALIAS_B,
                    title='Basel III', date='11/15/2023', stance='oppose', stance_sign='-1',
                    score='0', score_graded='0', stance_conf='0.5',
                    categories='Interchange', stance_src='human_label'),
            # engine only: this is the row the desk never showed
            csv_row(member='Josh Riley', bioguide='R000106', url=FRESH,
                    title='Riley on interchange', date='2026-07-06', stance='support',
                    stance_sign='1', score='1', score_graded='0.4', stance_conf='0.88',
                    categories='Interchange', relevance='tracked bill only',
                    stance_src='press_refresh_rules_v1'),
        ]
        self.rows, self.ledger = DPS.desk_press_rows(
            list(self.sheet), HEADER_INDEX, source='engine',
            load_rows=lambda ledger=None: self.engine)

    def test_every_sheet_row_is_still_walked_in_order(self):
        self.assertEqual(self.rows[:len(self.sheet)], self.sheet)

    def test_no_sheet_row_is_removed(self):
        for row in self.sheet:
            self.assertIn(row, self.rows)

    def test_the_engine_only_row_is_appended(self):
        self.assertEqual(len(self.rows), len(self.sheet) + 1)
        self.assertEqual(pv(self.rows[-1], 'url'), FRESH)

    def test_a_row_the_sheet_already_carries_is_not_appended_twice(self):
        urls = [pv(r, 'url') for r in self.rows]
        self.assertEqual(urls.count(ALIAS_B), 1)
        self.assertEqual(urls.count(ALIAS_A), 1)

    def test_the_overlap_renders_from_the_SHEET_bytes(self):
        """The measured reason: the CSV would relabel this row's stance oppose->support."""
        britt = [r for r in self.rows if pv(r, 'url') == ALIAS_B]
        self.assertEqual(len(britt), 1)
        self.assertEqual(pv(britt[0], 'stance'), 'support')
        self.assertEqual(pv(britt[0], 'stance_sign'), 1)
        self.assertEqual(pv(britt[0], 'stance_src'), 'debate')
        self.assertEqual(pv(britt[0], 'score_graded'), 0.4)
        self.assertEqual(pv(britt[0], 'date'), '2023-11-15')

    def test_the_release_key_matches_across_url_forms(self):
        """A5's release identity is what makes the alias pair one key, not two."""
        self.assertEqual(DPS.row_key(ALIAS_A, 'B001319'), DPS.row_key(ALIAS_B, 'B001319'))
        self.assertEqual(DPS.row_key(ALIAS_A, 'B001319'),
                         PS.tone_turn_key({'url': ALIAS_A, 'bioguide': 'B001319'}))

    def test_identity_is_per_member(self):
        self.assertNotEqual(DPS.row_key(FRESH, 'R000106'), DPS.row_key(FRESH, 'W000800'))

    def test_the_same_release_for_another_member_is_a_new_row(self):
        engine = [csv_row(member='Peter Welch', bioguide='W000800', url=ALIAS_A,
                          title='Basel III', date='2023-11-15', stance='support',
                          stance_sign='1')]
        rows, ledger = DPS.desk_press_rows(list(self.sheet), HEADER_INDEX, source='engine',
                                           load_rows=lambda ledger=None: engine)
        self.assertEqual(ledger['press_engine_rows_appended'], 1)
        self.assertEqual(pv(rows[-1], 'bioguide'), 'W000800')

    def test_a_billstatus_row_is_kept_and_never_matched(self):
        bills = [r for r in self.rows if str(pv(r, 'url')).startswith('billstatus:')]
        self.assertEqual(len(bills), 1)
        self.assertEqual(self.ledger['press_sheet_billstatus_rows'], 1)

    def test_an_appended_row_is_sheet_shaped(self):
        added = self.rows[-1]
        self.assertIsInstance(added, tuple)
        self.assertEqual(len(added), len(SHEET_HEADER))
        for field in DESK_READ:
            with self.subTest(field=field):
                self.assertIsNotNone(pv(added, field))
        self.assertEqual(pv(added, 'member'), 'Josh Riley')
        self.assertEqual(pv(added, 'relevance'), 'tracked bill only')
        self.assertEqual(pv(added, 'categories'), 'Interchange')

    def test_a_column_the_csv_lacks_stays_empty_and_is_never_invented(self):
        engine = [{'member': 'Josh Riley', 'bioguide': 'R000106', 'url': FRESH,
                   'title': 'No other columns at all'}]
        rows, _ = DPS.desk_press_rows([], HEADER_INDEX, source='engine',
                                      load_rows=lambda ledger=None: engine)
        self.assertIsNone(pv(rows[0], 'stance_src'))
        self.assertIsNone(pv(rows[0], 'score_graded'))
        self.assertEqual(pv(rows[0], 'title'), 'No other columns at all')

    def test_an_engine_row_with_no_url_is_dropped_before_the_desk_sees_it(self):
        """press_stream drops these too; a row with no url has no release identity."""
        engine = [csv_row(member='Josh Riley', bioguide='R000106', url='', title='No url')]
        rows, ledger = DPS.desk_press_rows([], HEADER_INDEX, source='engine',
                                           load_rows=lambda ledger=None: engine)
        self.assertEqual(rows, [])
        self.assertEqual(ledger['press_engine_rows_no_url'], 1)
        self.assertEqual(ledger['press_engine_rows_appended'], 0)


class SlugSharedByTwoReleases(unittest.TestCase):
    """A release key NOMINATES a match; the day decides it.  press_stream keeps a headline
    republished on another day as its own release (release_key_conflict_kept), and the frozen
    sheet may hold only the older one.  The newer one must reach the desk."""

    SLUG = 'https://vargas.house.gov/house-passes-the-credit-union-board-modernization-act'

    def _rows(self, sheet, engine):
        return DPS.desk_press_rows(iter(sheet), HEADER_INDEX, source='engine',
                                   load_rows=lambda ledger=None: engine)

    def test_a_later_release_under_the_same_slug_is_appended(self):
        import datetime
        sheet = [sheet_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG,
                           date=datetime.datetime(2022, 9, 30), title='House Passes ...')]
        engine = [csv_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG,
                          date='9/30/2022', title='House Passes ...'),
                  csv_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG + '-0',
                          date='1/30/2023', title='House Passes ...')]
        rows, ledger = self._rows(sheet, engine)
        self.assertEqual(len(rows), 2)
        self.assertEqual(pv(rows[1], 'url'), self.SLUG + '-0')
        self.assertEqual(pv(rows[1], 'date'), '2023-01-30')
        self.assertEqual(ledger['press_engine_rows_already_in_sheet'], 1)
        self.assertEqual(ledger['press_engine_rows_same_slug_other_day_appended'], 1)
        self.assertEqual(ledger['press_engine_rows_appended'], 1)

    def test_when_the_sheet_holds_both_days_nothing_is_appended(self):
        """The live state on 2026-09-19: the sheet carries both Vargas releases."""
        import datetime
        sheet = [sheet_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG,
                           date=datetime.datetime(2022, 9, 30)),
                 sheet_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG + '-0',
                           date=datetime.datetime(2023, 1, 30))]
        engine = [csv_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG,
                          date='9/30/2022'),
                  csv_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG + '-0',
                          date='1/30/2023')]
        rows, ledger = self._rows(sheet, engine)
        self.assertEqual(len(rows), 2)
        self.assertEqual(ledger['press_engine_rows_already_in_sheet'], 2)
        self.assertEqual(ledger['press_engine_rows_same_slug_other_day_appended'], 0)

    def test_an_unreadable_date_falls_back_to_the_key_and_never_duplicates(self):
        """'NA' on the engine side, or an empty cell on the sheet side, cannot disambiguate, so
        the key alone decides -- the pre-guard behaviour, and never a duplicate row."""
        import datetime
        sheet = [sheet_row(member='Mike Crapo', bioguide='C000880', url=FRESH, date=None)]
        engine = [csv_row(member='Mike Crapo', bioguide='C000880', url=FRESH, date='3/6/2018')]
        rows, ledger = self._rows(sheet, engine)
        self.assertEqual(len(rows), 1)
        sheet = [sheet_row(member='Mike Crapo', bioguide='C000880', url=FRESH,
                           date=datetime.datetime(2018, 3, 6))]
        engine = [csv_row(member='Mike Crapo', bioguide='C000880', url=FRESH, date='NA')]
        rows, ledger = self._rows(sheet, engine)
        self.assertEqual(len(rows), 1)
        self.assertEqual(ledger['press_engine_rows_same_slug_other_day_appended'], 0)

    def test_two_engine_only_releases_under_one_slug_are_both_appended(self):
        engine = [csv_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG,
                          date='2026-10-01'),
                  csv_row(member='Juan Vargas', bioguide='V000130', url=self.SLUG + '-0',
                          date='2027-02-01')]
        rows, ledger = self._rows([], engine)
        self.assertEqual([pv(r, 'date') for r in rows], ['2026-10-01', '2027-02-01'])
        self.assertEqual(ledger['press_engine_rows_appended'], 2)


class EngineDateMapping(unittest.TestCase):
    """fdate() wants 'YYYY-...' -- the base tone file stores 'M/D/YYYY'."""

    def test_a_us_date_becomes_the_shape_fdate_reads(self):
        self.assertEqual(DPS.engine_date('3/14/2025'), '2025-03-14')
        self.assertEqual(DPS.engine_date('12/1/2026'), '2026-12-01')
        self.assertEqual(DPS.engine_date('03/05/2026'), '2026-03-05')

    def test_an_iso_date_is_untouched(self):
        self.assertEqual(DPS.engine_date('2026-07-06'), '2026-07-06')

    def test_an_unparseable_date_is_left_exactly_as_it_is(self):
        """'NA' must stay non-numeric so fdate() blanks it and PDATE can recover it."""
        for value in ('NA', '', 'unknown', None, '2026', '1/2'):
            with self.subTest(value=value):
                self.assertEqual(DPS.engine_date(value), value)

    def test_a_datetime_is_passed_through(self):
        import datetime
        stamp = datetime.datetime(2025, 3, 14)
        self.assertIs(DPS.engine_date(stamp), stamp)

    def test_the_normalisation_is_counted(self):
        engine = [csv_row(member='Katie Britt', bioguide='B001319', url=ALIAS_A,
                          title='Basel III', date='11/15/2023'),
                  csv_row(member='Josh Riley', bioguide='R000106', url=FRESH,
                          title='Riley', date='2026-07-06')]
        rows, ledger = DPS.desk_press_rows([], HEADER_INDEX, source='engine',
                                           load_rows=lambda ledger=None: engine)
        self.assertEqual(ledger['press_engine_dates_normalised'], 1)
        self.assertEqual([pv(r, 'date') for r in rows], ['2023-11-15', '2026-07-06'])


class LedgerCloses(unittest.TestCase):
    """The desk's own press census has to close, or the accounting is theatre."""

    def setUp(self):
        self.sheet = [
            sheet_row(member='Katie Britt', bioguide='B001319', url=ALIAS_A, title='Basel III'),
            sheet_row(member='Katie Britt', bioguide='B001319', url=ALIAS_B, title='Basel III'),
            sheet_row(member='Katie Britt', bioguide='B001319', url='billstatus:119-S-1',
                      title='S.1'),
            sheet_row(member='Peter Welch', bioguide='W000800', url=DESKONLY, title='Sheet only'),
        ]
        self.engine = [
            csv_row(member='Katie Britt', bioguide='B001319', url=ALIAS_B, title='Basel III'),
            csv_row(member='Josh Riley', bioguide='R000106', url=FRESH, title='Riley'),
            csv_row(member='Peter Welch', bioguide='W000800', url='', title='No url'),
        ]
        self.rows, self.ledger = DPS.desk_press_rows(
            list(self.sheet), HEADER_INDEX, source='engine',
            load_rows=lambda ledger=None: self.engine)

    def test_the_schema_and_dial_are_recorded(self):
        self.assertEqual(self.ledger['schema'], 'desk-press-source-v1')
        self.assertEqual(self.ledger['press_source'], 'engine')

    def test_every_engine_row_read_is_accounted_for(self):
        self.assertEqual(
            self.ledger['press_engine_rows_read'],
            self.ledger['press_engine_rows_already_in_sheet']
            + self.ledger['press_engine_rows_appended']
            + self.ledger['press_engine_rows_no_url'])

    def test_the_sheet_terms_account_for_every_sheet_row(self):
        self.assertEqual(self.ledger['press_sheet_rows'], len(self.sheet))
        self.assertEqual(
            self.ledger['press_sheet_rows'],
            self.ledger['press_sheet_press_rows']
            + self.ledger['press_sheet_billstatus_rows'])

    def test_the_walked_total_is_the_row_count_the_desk_will_census(self):
        self.assertEqual(self.ledger['press_rows_walked'], len(self.rows))
        self.assertEqual(self.ledger['press_rows_walked'],
                         self.ledger['press_sheet_rows']
                         + self.ledger['press_engine_rows_appended'])

    def test_sheet_only_rows_are_disclosed_not_just_kept(self):
        """Two sheet keys (Britt's aliased pair is ONE key, Welch's release is the other);
        the engine covers Britt, so exactly one sheet key is engine-absent."""
        self.assertEqual(self.ledger['press_sheet_press_rows'], 3)
        self.assertEqual(self.ledger['press_sheet_keys_absent_from_engine'], 1)

    def test_the_a5_ingest_ledger_is_carried_through(self):
        """The press census chains: tone rows read -> returned -> added here."""
        captured = {}

        def loader(ledger=None):
            if ledger is not None:
                ledger.update({'schema': PS.TONE_DEDUP_LEDGER_SCHEMA, 'rows_read': 7,
                               'rows_returned': 2, 'duplicate_url_form_collapsed': 1})
            captured['called'] = True
            return self.engine[:2]

        _, ledger = DPS.desk_press_rows(list(self.sheet), HEADER_INDEX, source='engine',
                                        load_rows=loader)
        self.assertTrue(captured['called'])
        self.assertEqual(ledger['press_engine_tone_dedup']['rows_read'], 7)
        self.assertEqual(ledger['press_engine_tone_dedup']['schema'],
                         'press-tone-ingest-dedup-v1')


class DeskWiring(unittest.TestCase):
    """The producer cannot be imported -- import == execution -- so pin it as text."""

    def setUp(self):
        self.source = DESK_SOURCE.read_text(encoding='utf-8')

    def test_the_desk_walks_the_helper_not_the_sheet_generator(self):
        self.assertIn('_press_rows, _press_source_ledger = DPS.desk_press_rows(', self.source)
        self.assertIn('for r in _press_rows:', self.source)
        self.assertNotIn('for r in pw.iter_rows(min_row=2, values_only=True):', self.source)

    def test_the_helper_is_given_the_sheet_rows_and_the_sheet_header_index(self):
        self.assertIn('pw.iter_rows(min_row=2, values_only=True), ph)', self.source)

    def test_the_sheet_accessor_is_unchanged(self):
        self.assertIn("def pv(r, k): return r[ph[k]] if k in ph else None", self.source)

    def test_the_billstatus_routing_rule_is_unchanged(self):
        self.assertIn("if str(pv(r, 'url') or '').startswith('billstatus:'):", self.source)
        self.assertIn('press_billstatus_excluded += 1; continue', self.source)

    def test_the_source_split_reaches_the_media_audit(self):
        self.assertIn("'press_source_ledger':_press_source_ledger", self.source)

    def test_the_reconciliation_terms_still_come_from_the_same_counters(self):
        """dashboard_integrity reconciles press_source_rows against these three."""
        self.assertIn('press_source_rows += 1', self.source)
        self.assertIn("'press_source_rows':press_source_rows", self.source)
        self.assertIn("'press_displayed': sum(len(v) for v in arts.values())", self.source)

    def test_the_tone_inputs_are_declared_desk_inputs(self):
        integrity = (FEC / 'audit' / 'dashboard_integrity.py').read_text(encoding='utf-8')
        self.assertIn("env:DESK_PRESS_SOURCE", integrity)
        self.assertIn("data/stance/press_refresh_tone.csv", integrity)
        self.assertIn("data/stance/press_refresh_feed_receipt.json", integrity)
        self.assertIn("source_state['press_stream:BASE_TONE_PATH']", integrity)
        self.assertIn("os.path.join('fec','surfaces','desk_press_source.py')", integrity)
        self.assertIn("os.path.join('fec','press_stream.py')", integrity)

    def test_the_dial_is_catalogued(self):
        spec = (FEC.parent / 'MATHEMATICAL_SPECIFICATION.md').read_text(encoding='utf-8')
        self.assertIn('DESK_PRESS_SOURCE=engine', spec)
        skill = (FEC.parent / '.claude' / 'skills' / 'moneyball-config-and-flags'
                 / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('DESK_PRESS_SOURCE', skill)


if __name__ == '__main__':
    unittest.main(verbosity=2)
