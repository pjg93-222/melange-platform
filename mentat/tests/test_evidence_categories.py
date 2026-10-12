"""S7: committee categories, the committee-type filter and CORPORATE rows.

Written against the S7 brief (Paul's rulings of 2026-09-17) BEFORE the
implementation landed, and run first against the untouched tree to record the
RED baseline in receipts/S7_before.txt.

Four things are pinned here:
  * CORPORATE rows fold into Receipts (ruling 1);
  * a workbook's `Include in Analysis = No` makes a row `excluded` -- visible,
    with its reason, and out of every total (ruling 2);
  * a committee's category comes from the file's own category column, and only
    a committee with none is classified from the FEC committee master
    (rulings 3 and 4);
  * the catalog, the listing and the select response carry those fields, and an
    import read under the old rules re-reads itself on the first listing.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import evidence_csv as csv_data  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_MASTER = REPO_ROOT / 'mentat' / 'inputs' / 'fec_committee_master'


def rmtree_retry(path, attempts=10):
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                return
            time.sleep(0.1 * (attempt + 1))


@contextmanager
def temp_data_root():
    root = Path(tempfile.mkdtemp(prefix='mentat-evidence-categories-'))
    try:
        yield root
    finally:
        rmtree_retry(root)


@contextmanager
def temp_fixture_dir():
    root = Path(tempfile.mkdtemp(prefix='mentat-evidence-categories-fixtures-'))
    try:
        yield root
    finally:
        rmtree_retry(root)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
MASTER_COLUMNS = 15


def master_row(identifier, name, designation, committee_type, org_type):
    """One cm.txt row: 15 pipe-separated fields, no header, no quoting.

    Column numbers are the commission's: 1 CMTE_ID, 9 CMTE_DSGN, 10 CMTE_TP,
    13 ORG_TP.
    """
    fields = [''] * MASTER_COLUMNS
    fields[0], fields[1] = identifier, name
    fields[8], fields[9], fields[12] = designation, committee_type, org_type
    return '|'.join(fields)


# One row per branch of the classification rule, in the order the rule tries
# them, plus a header row (real cm.txt files have none; a copy saved from a
# spreadsheet can) and one committee that is deliberately absent.
MASTER_ROWS = [
    '|'.join(['CMTE_ID', 'CMTE_NM', 'TRES_NM', 'CMTE_ST1', 'CMTE_ST2', 'CMTE_CITY', 'CMTE_ST',
              'CMTE_ZIP', 'CMTE_DSGN', 'CMTE_TP', 'CMTE_PTY_AFFILIATION', 'CMTE_FILING_FREQ',
              'ORG_TP', 'CONNECTED_ORG_NM', 'CAND_ID']),
    master_row('C00000001', 'JOINT FUND', 'J', 'O', 'C'),      # designation J beats type O and ORG_TP C
    master_row('C00000002', 'LEADER PAC', 'D', 'N', 'M'),      # designation D beats ORG_TP M
    master_row('C00000003', 'STATE PARTY', 'U', 'X', ''),      # type X
    master_row('C00000004', 'SUPER PAC', 'U', 'O', ''),        # type O
    master_row('C00000005', 'CAREY PAC', 'U', 'V', ''),        # type V
    master_row('C00000006', 'HOUSE CAMPAIGN', 'U', 'H', ''),   # type H
    master_row('C00000007', 'AUTHORIZED CTE', 'A', 'N', ''),   # designation A
    master_row('C00000008', 'TRADE PAC', 'B', 'Q', 'T'),       # ORG_TP T
    master_row('C00000009', 'CORPORATE PAC', 'B', 'Q', 'C'),   # ORG_TP C
    master_row('C00000010', 'MEMBERSHIP PAC', 'B', 'Q', 'L'),  # ORG_TP L
    master_row('C00000011', 'NO RULE MATCHES', 'B', 'Q', ''),  # nothing matches
]

MASTER_EXPECTED = {
    'C00000001': ('JFC', 'fec_master:cm24'),
    'C00000002': ('Leadership PAC', 'fec_master:cm24'),
    'C00000003': ('Party Committee', 'fec_master:cm24'),
    'C00000004': ('Super PAC', 'fec_master:cm24'),
    'C00000005': ('Carey', 'fec_master:cm24'),
    'C00000006': ('Candidate', 'fec_master:cm24'),
    'C00000007': ('Candidate', 'fec_master:cm24'),
    'C00000008': ('Trade Association', 'fec_master:org_type:cm24'),
    'C00000009': ('Company PAC', 'fec_master:org_type:cm24'),
    'C00000010': ('Other PAC', 'fec_master:org_type:cm24'),
    'C00000011': ('Unclassified', 'fec_master:cm24'),
}


def write_master(inputs, cycles=('cm24',), rows=None):
    """Write cm.txt under `inputs`/fec_committee_master/<cycle>/, as cm.txt is
    written on disk: pipe-delimited, CRLF, no quoting."""
    for cycle in cycles:
        folder = Path(inputs) / 'fec_committee_master' / cycle
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'cm.txt').write_text('\r\n'.join(rows or MASTER_ROWS) + '\r\n', encoding='utf-8')
    return Path(inputs)


@contextmanager
def master_at(inputs):
    """Point evidence_csv at `inputs` for the committee master, cache cleared
    on both sides so neither this test nor the next reads a stale table."""
    csv_data.committee_master.cache_clear()
    try:
        with patch.object(csv_data, 'INPUTS', Path(inputs)):
            yield
    finally:
        csv_data.committee_master.cache_clear()


WORKBOOK_HEADER = ['Committee ID', 'Recipient', 'Amount', 'Date', 'Cycle', 'Record Type',
                   'Recipient Category', 'Include in Analysis', 'Inclusion Reason']


def workbook_rows():
    """Two committees whose rows exercise every rule at once.

    C00000004 SUPER PAC: 4 rows -- two CORPORATE counted as Receipts, one
    CONTRIBUTION counted, one CORPORATE excluded by the workbook.
    C00000008 TRADE PAC: 2 rows with no category in the file at all, so the
    committee master has to classify it.
    C00000005 CAREY PAC: 2 rows the file calls Unclassified, which is an
    absence, so the master classifies it after all.
    C00000011 NO RULE MATCHES: 1 Unclassified row the master cannot do
    better with, so the source has to name both.
    """
    return [
        ['C00000004', 'SUPER PAC', 25000000, '2026-01-05', '2026', 'CORPORATE', 'Super PAC', 'Yes', ''],
        ['C00000004', 'SUPER PAC', 15000000, '2026-01-06', '2026', 'CORPORATE', 'Super PAC', 'Yes', ''],
        ['C00000004', 'SUPER PAC', 5000, '2026-01-07', '2026', 'CONTRIBUTION', 'Super PAC', 'Yes', ''],
        ['C00000004', 'SUPER PAC', 23000000, '2026-01-08', '2026', 'CORPORATE', '', 'No', 'Exact five-key memo duplicate'],
        ['C00000008', 'TRADE PAC', 1000, '2026-02-05', '2026', 'CONTRIBUTION', '', 'Yes', ''],
        ['C00000008', 'TRADE PAC', 2000, '2026-02-06', '2026', 'CORPORATE', '', 'Yes', ''],
        ['C00000005', 'CAREY PAC', 3000, '2026-03-05', '2026', 'CONTRIBUTION', 'Unclassified', 'Yes', ''],
        ['C00000005', 'CAREY PAC', 4000, '2026-03-06', '2026', 'CONTRIBUTION', 'Unclassified', 'Yes', ''],
        ['C00000011', 'NO RULE PAC', 500, '2026-04-05', '2026', 'CONTRIBUTION', 'Unclassified', 'Yes', ''],
    ]


def write_two_sheet_workbook(path):
    """The shape the real industry workbooks have: several cycle sheets with
    the same columns, read as one table because no worksheet was chosen. A
    re-read that replays the reader's recorded sheet instead of the reader's
    recorded CHOICE reads only the first of them.
    """
    workbook = openpyxl.Workbook()
    rows = workbook_rows()
    for index, (title, group) in enumerate((('2023-2024', rows[:3]), ('2025-2026', rows[3:]))):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = title
        sheet.append(WORKBOOK_HEADER)
        for row in group:
            sheet.append(row)
    workbook.save(path)
    return path


def write_workbook(path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'Sheet1'
    sheet.append(WORKBOOK_HEADER)
    for row in workbook_rows():
        sheet.append(row)
    workbook.save(path)
    return path


def normalized(cells, columns=None, row_number=1):
    """normalize() one row given as {header: text}."""
    headers = list(cells)
    return csv_data.normalize(dict(cells), row_number, columns or [csv_data.key(name) for name in headers])


# ---------------------------------------------------------------------------
class CategoryVocabulary(unittest.TestCase):
    def test_vocabulary_matches_graph_data(self):
        # graph_data is imported INSIDE the test: evidence_csv must not import
        # it (S3e made the console's imports lazy on purpose), so the agreement
        # is asserted here rather than enforced by an import.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
        import graph_data
        self.assertEqual(list(csv_data.CATEGORIES), list(graph_data.CATEGORIES))

    def test_canonicalisation_table(self):
        cases = [
            ('Super PAC', 'Super PAC'),
            ('super pac', 'Super PAC'),
            ('  TRADE ASSOCIATION  ', 'Trade Association'),
            ('JFC', 'JFC'),
            ('Company PAC', 'Company PAC'),
            ('Leadership PAC', 'Leadership PAC'),
            ('Carey', 'Carey'),
            ('Other PAC', 'Other PAC'),
            ('Candidate', 'Candidate'),
            ('Party Committee', 'Party Committee'),
            ('Unclassified', 'Unclassified'),
            ('Hybrid super PAC', 'Unclassified'),   # unknown non-blank text
            ('001', 'Unclassified'),
            ('', ''),                                # blank stays blank
            ('   ', ''),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(csv_data.canonical_category(text), expected)

    def test_row_reads_every_category_header(self):
        for header in ('Recipient Category', 'Committee Category'):
            with self.subTest(header=header):
                row = normalized({'Committee ID': 'C00000004', 'Amount': '10', 'Date': '2026-01-05',
                                  'Record Type': 'CONTRIBUTION', header: 'super pac'})
                self.assertEqual(row['category'], 'Super PAC')

    def test_a_bare_category_column_is_not_a_committee_category(self):
        # An FEC bulk oppexp file has a CATEGORY column holding the
        # commission's purpose code. Reading it as a committee category would
        # canonicalise every row to Unclassified and, because the file beats
        # the master, stop the master being consulted for that whole import.
        row = normalized({'CMTE_ID': 'C00000004', 'TRANSACTION_AMT': '10', 'TRANSACTION_DT': '01052026',
                          'CATEGORY': '001', 'CATEGORY_DESC': 'Administrative'})
        self.assertEqual(row['category'], '')

    def test_row_without_a_category_column_is_blank(self):
        row = normalized({'Committee ID': 'C00000004', 'Amount': '10', 'Date': '2026-01-05',
                          'Record Type': 'CONTRIBUTION'})
        self.assertEqual(row['category'], '')


class CorporateRows(unittest.TestCase):
    def test_corporate_is_a_receipt(self):
        for text in ('CORPORATE', 'corporate', 'Corporate Giving', 'corporate_contribution'):
            with self.subTest(record_type=text):
                row = normalized({'Committee ID': 'C00000004', 'Amount': '25000000', 'Date': '2026-01-05',
                                  'Record Type': text})
                self.assertEqual(row['kind'], 'receipt')
                self.assertEqual(row['channel'], 'Receipts')
                self.assertEqual(row['status'], 'supported')
                self.assertEqual(row['issues'], [])

    def test_corporate_row_is_no_longer_unsupported(self):
        row = normalized({'Committee ID': 'C00000004', 'Amount': '25000000', 'Date': '2026-01-05',
                          'Record Type': 'CORPORATE'})
        self.assertNotIn('The CSV does not identify a supported receipt or activity type.', row['issues'])


class WorkbookExclusion(unittest.TestCase):
    def base(self, **extra):
        cells = {'Committee ID': 'C00000004', 'Amount': '23000000', 'Date': '2026-01-08',
                 'Record Type': 'CORPORATE'}
        cells.update(extra)
        return normalized(cells)

    def test_no_makes_the_row_excluded_with_its_reason(self):
        row = self.base(**{'Include in Analysis': 'No', 'Inclusion Reason': 'Exact five-key memo duplicate'})
        self.assertEqual(row['status'], 'excluded')
        self.assertEqual(row['kind'], 'receipt')
        self.assertEqual(row['channel'], 'Receipts')
        self.assertEqual(row['amount'], 23000000.0)
        self.assertIn('Excluded by the workbook: Exact five-key memo duplicate', row['issues'])

    def test_no_without_a_reason(self):
        row = self.base(**{'Include in Analysis': 'No'})
        self.assertEqual(row['status'], 'excluded')
        self.assertIn('Excluded by the workbook.', row['issues'])

    def test_every_false_spelling(self):
        for text in ('No', 'no', 'N', 'n', 'FALSE', 'false', 'False', '0'):
            with self.subTest(value=text):
                self.assertEqual(self.base(**{'Include in Analysis': text})['status'], 'excluded')

    def test_yes_blank_and_absent_are_unchanged(self):
        for text in ('Yes', 'yes', 'Y', 'TRUE', '1', ''):
            with self.subTest(value=text):
                row = self.base(**{'Include in Analysis': text})
                self.assertEqual(row['status'], 'supported')
                self.assertEqual(row['issues'], [])
        self.assertEqual(self.base()['status'], 'supported')

    def test_a_contribution_row_is_excluded_too(self):
        row = normalized({'Committee ID': 'C00000004', 'Amount': '5000', 'Date': '2026-01-07',
                          'Record Type': 'CONTRIBUTION', 'Include in Analysis': 'No',
                          'Inclusion Reason': 'Exact five-key memo duplicate'})
        self.assertEqual(row['status'], 'excluded')
        self.assertEqual(row['channel'], 'Receipts')

    def test_excluded_wins_over_other_issues(self):
        row = normalized({'Committee ID': 'C00000004', 'Amount': 'not a number', 'Date': '2026-01-08',
                          'Record Type': 'CORPORATE', 'Include in Analysis': 'No',
                          'Inclusion Reason': 'Exact five-key memo duplicate'})
        self.assertEqual(row['status'], 'excluded')
        self.assertIn('The observed amount is missing or not a finite number.', row['issues'])
        self.assertIn('Excluded by the workbook: Exact five-key memo duplicate', row['issues'])


class CommitteeMaster(unittest.TestCase):
    def test_every_rule_branch(self):
        with temp_fixture_dir() as fixtures:
            write_master(fixtures)
            with master_at(fixtures):
                for identifier, expected in MASTER_EXPECTED.items():
                    with self.subTest(committee=identifier):
                        self.assertEqual(csv_data.classify_committee(identifier), expected)

    def test_org_type_corporation_depends_on_whether_the_file_classifies(self):
        # 'Company PAC' in a Mentat workbook means one of the industry's OWN
        # companies' PACs, a label the file assigns. A PAC that is merely
        # registered to a corporation (C00468314 DEMOCRACY ENGINE, INC., PAC)
        # must not land in that bucket when the file is the one classifying.
        with temp_fixture_dir() as fixtures:
            write_master(fixtures)
            with master_at(fixtures):
                self.assertEqual(csv_data.classify_committee('C00000009'),
                                 ('Company PAC', 'fec_master:org_type:cm24'))
                self.assertEqual(csv_data.classify_committee('C00000009', True),
                                 ('Other PAC', 'fec_master:org_type:cm24'))
                # Every other branch is unaffected by the file having a column.
                for identifier in ('C00000001', 'C00000004', 'C00000008', 'C00000010'):
                    with self.subTest(committee=identifier):
                        self.assertEqual(csv_data.classify_committee(identifier, True),
                                         MASTER_EXPECTED[identifier])

    def test_committee_absent_from_the_master(self):
        with temp_fixture_dir() as fixtures:
            write_master(fixtures)
            with master_at(fixtures):
                category, source = csv_data.classify_committee('C00009999')
                self.assertEqual(category, 'Unclassified')
                self.assertEqual(source, 'not in the FEC committee master (cm24)')

    def test_newest_cycle_wins_and_older_cycles_fill_blanks(self):
        with temp_fixture_dir() as fixtures:
            # cm26 knows C00000004 as a Carey committee; cm24 still calls it a
            # Super PAC and is the only file that knows C00000008 at all.
            write_master(fixtures, cycles=('cm24',))
            folder = Path(fixtures) / 'fec_committee_master' / 'cm26'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'cm.txt').write_text(master_row('C00000004', 'SUPER PAC', 'U', 'V', '') + '\r\n',
                                           encoding='utf-8')
            with master_at(fixtures):
                self.assertEqual(csv_data.classify_committee('C00000004'), ('Carey', 'fec_master:cm26'))
                self.assertEqual(csv_data.classify_committee('C00000008'),
                                 ('Trade Association', 'fec_master:org_type:cm24'))
                self.assertEqual(csv_data.classify_committee('C00009999')[1],
                                 'not in the FEC committee master (cm24, cm26)')

    def test_no_master_directory(self):
        with temp_fixture_dir() as fixtures:
            with master_at(Path(fixtures) / 'absent'):
                self.assertEqual(csv_data.classify_committee('C00000004'),
                                 ('Unclassified', 'committee master not available'))

    def test_unreadable_master_never_raises(self):
        with temp_fixture_dir() as fixtures:
            folder = Path(fixtures) / 'fec_committee_master' / 'cm24'
            folder.mkdir(parents=True)
            (folder / 'cm.txt').write_bytes(b'\x00\x01\x02 not a master file\n')
            with master_at(fixtures):
                category, _ = csv_data.classify_committee('C00000004')
                self.assertEqual(category, 'Unclassified')

    @unittest.skipUnless((REAL_MASTER / 'cm26' / 'cm.txt').is_file(),
                         'the real FEC committee master is not in this checkout')
    def test_real_master_smoke(self):
        csv_data.committee_master.cache_clear()
        try:
            for identifier, expected in (('C00835959', 'Super PAC'),          # FAIRSHAKE, CMTE_TP O
                                         ('C00567412', 'Trade Association'),  # CHAMBER OF DIGITAL COMMERCE, ORG_TP T
                                         ('C00448696', 'Carey')):             # SENATE CONSERVATIVES FUND, CMTE_TP V
                with self.subTest(committee=identifier):
                    category, source = csv_data.classify_committee(identifier, True)
                    self.assertEqual(category, expected)
                    self.assertTrue(source.startswith('fec_master:'), source)
        finally:
            csv_data.committee_master.cache_clear()


class CommitteeCategory(unittest.TestCase):
    """The modal rule, over rows, with blanks and a tie."""

    def rows_for(self, categories):
        return [normalized({'Committee ID': 'C00000004', 'Amount': '10', 'Date': '2026-01-05',
                            'Record Type': 'CONTRIBUTION', 'Recipient Category': text}, row_number=index)
                for index, text in enumerate(categories, start=1)]

    def test_modal_category_ignores_blanks(self):
        rows = self.rows_for(['Super PAC', '', 'Super PAC', 'Candidate', ''])
        self.assertEqual(csv_data.modal_category(rows), 'Super PAC')

    def test_a_tie_is_broken_by_vocabulary_order(self):
        # Candidate is later in the vocabulary than Super PAC, so Super PAC wins.
        rows = self.rows_for(['Candidate', 'Super PAC'])
        self.assertEqual(csv_data.modal_category(rows), 'Super PAC')
        rows = self.rows_for(['Candidate', 'Trade Association'])
        self.assertEqual(csv_data.modal_category(rows), 'Trade Association')

    def test_all_blank_is_blank(self):
        self.assertEqual(csv_data.modal_category(self.rows_for(['', '', ''])), '')

    def test_unclassified_is_an_absence_not_a_category(self):
        # graph_data reads a missing recipient_category as the word
        # 'Unclassified', so the two mean the same thing and neither can
        # outvote a category the file actually named.
        self.assertEqual(csv_data.modal_category(self.rows_for(
            ['Unclassified', 'Unclassified', 'Unclassified', 'Unclassified', 'Unclassified', 'Carey'])),
            'Carey')
        self.assertEqual(csv_data.modal_category(self.rows_for(['Unclassified', 'Unclassified'])), '')


class StoreEndToEnd(unittest.TestCase):
    """One workbook through the real Store: catalog, listing and select."""

    def ingest(self, root, fixtures):
        store = csv_data.Store(root)
        job = store.wait(store.import_path(str(write_workbook(Path(fixtures) / 'overview.xlsx')))['id'],
                         timeout=180)
        self.assertEqual(job['state'], 'ready', job.get('message'))
        return store, job

    def test_catalog_listing_and_select(self):
        with temp_fixture_dir() as fixtures, temp_data_root() as root:
            write_master(fixtures)
            with master_at(fixtures):
                store, job = self.ingest(root, fixtures)
                catalog = store.catalog(job['id'])

                self.assertEqual(catalog['catalog_version'], csv_data.CATALOG_VERSION)
                self.assertEqual(catalog['row_count'], 9)
                self.assertEqual(catalog['supported_rows'], 8)
                self.assertEqual(catalog['excluded_rows'], 1)
                self.assertEqual(catalog['unsupported_rows'], 0)

                committees = {item['id']: item for item in catalog['committees']}
                self.assertEqual(committees['C00000004']['category'], 'Super PAC')
                self.assertEqual(committees['C00000004']['category_source'], 'file:Recipient Category')
                self.assertEqual(committees['C00000004']['excluded_rows'], 1)
                # No row of C00000008 carries a category, so the master answers.
                self.assertEqual(committees['C00000008']['category'], 'Trade Association')
                self.assertEqual(committees['C00000008']['category_source'], 'fec_master:org_type:cm24')
                self.assertEqual(committees['C00000008']['excluded_rows'], 0)
                # The file calls C00000005 Unclassified, which is an absence, so
                # the master is asked and knows better.
                self.assertEqual(committees['C00000005']['category'], 'Carey')
                self.assertEqual(committees['C00000005']['category_source'], 'fec_master:cm24')
                # Neither the file nor the master can classify C00000011, and the
                # source says so rather than implying one of them decided.
                self.assertEqual(committees['C00000011']['category'], 'Unclassified')
                self.assertEqual(committees['C00000011']['category_source'],
                                 'file:Recipient Category; fec_master:cm24')

                categories = {item['category']: item for item in catalog['categories']}
                self.assertEqual([item['category'] for item in catalog['categories']],
                                 [name for name in csv_data.CATEGORIES if name in categories])
                self.assertEqual(categories['Super PAC'],
                                 {'category': 'Super PAC', 'committees': 1, 'rows': 4,
                                  'supported_rows': 3, 'excluded_rows': 1})
                self.assertEqual(categories['Trade Association'],
                                 {'category': 'Trade Association', 'committees': 1, 'rows': 2,
                                  'supported_rows': 2, 'excluded_rows': 0})
                self.assertEqual(categories['Carey'],
                                 {'category': 'Carey', 'committees': 1, 'rows': 2,
                                  'supported_rows': 2, 'excluded_rows': 0})
                self.assertEqual(categories['Unclassified'],
                                 {'category': 'Unclassified', 'committees': 1, 'rows': 1,
                                  'supported_rows': 1, 'excluded_rows': 0})

                warning = next((text for text in catalog['warnings']
                                if text.startswith('Committee categories')), '')
                # Three committees were CHECKED against the master, not three
                # resolved by it: C00000011 was checked and stayed Unclassified.
                self.assertEqual(warning,
                                 "Committee categories come from this file's Recipient Category column; "
                                 '3 committees the file left blank or Unclassified were checked against '
                                 'the FEC committee master (cm24).')
                self.assertNotIn('Committee names and IDs are taken from this CSV; their registration and '
                                 'committee type have not been independently verified.', catalog['warnings'])

                page = store.select(job['id'], 'C00000004', '', 0, 100)
                totals = {item['channel']: (item['amount'], item['row_count']) for item in page['totals']}
                self.assertEqual(totals['Receipts'], (40005000.0, 3))
                self.assertEqual(page['supported_rows'], 3)
                self.assertEqual(page['excluded_rows'], 1)
                self.assertEqual(page['unsupported_rows'], 0)
                self.assertEqual(page['total_rows'], 4)
                self.assertEqual(catalog['row_count'],
                                 sum(item['rows'] for item in catalog['committees']))
                self.assertEqual(page['committee']['category'], 'Super PAC')
                self.assertEqual(page['committee']['category_source'], 'file:Recipient Category')
                excluded = [row for row in page['rows'] if row['status'] == 'excluded']
                self.assertEqual(len(excluded), 1)
                self.assertIn('Excluded by the workbook: Exact five-key memo duplicate', excluded[0]['issues'])
                self.assertEqual({row['category'] for row in page['rows']}, {'Super PAC', ''})

    def test_master_unavailable_never_fails_the_import(self):
        with temp_fixture_dir() as fixtures, temp_data_root() as root:
            with master_at(Path(fixtures) / 'absent'):
                store, job = self.ingest(root, fixtures)
                catalog = store.catalog(job['id'])
                committees = {item['id']: item for item in catalog['committees']}
                self.assertEqual(committees['C00000008']['category'], 'Unclassified')
                self.assertEqual(committees['C00000008']['category_source'], 'committee master not available')
                # The file said Unclassified here, so the source names both.
                self.assertEqual(committees['C00000011']['category_source'],
                                 'file:Recipient Category; committee master not available')
                self.assertTrue(any('not available' in text for text in catalog['warnings']))


class StaleCatalogReRead(unittest.TestCase):
    """An import ingested under the old rules re-reads itself on the first listing."""

    def stale(self, root, fixtures, writer=None):
        store = csv_data.Store(root)
        write = writer or write_workbook
        job = store.wait(store.import_path(str(write(Path(fixtures) / 'overview.xlsx')))['id'],
                         timeout=180)
        self.assertEqual(job['state'], 'ready', job.get('message'))
        record_path = store.record_path(job['id'])
        record = json.loads(record_path.read_text(encoding='utf-8'))
        record.pop('catalog_version', None)
        catalog = dict(record['catalog'])
        catalog.pop('catalog_version', None)
        for committee in catalog['committees']:
            committee.pop('category', None)
            committee.pop('category_source', None)
            committee.pop('excluded_rows', None)
        catalog.pop('categories', None)
        catalog.pop('excluded_rows', None)
        record['catalog'] = catalog
        record_path.write_text(json.dumps(record), encoding='utf-8')
        return store, job['id']

    def test_stale_import_is_re_read(self):
        with temp_fixture_dir() as fixtures, temp_data_root() as root:
            write_master(fixtures)
            with master_at(fixtures):
                store, identifier = self.stale(root, fixtures)
                first = store.sources()
                self.assertEqual(len(first), 1)
                self.assertIn(first[0]['state'], ('reading', 'ready'))
                deadline = time.time() + 180
                while time.time() < deadline:
                    listing = store.sources()
                    if listing and listing[0]['state'] == 'ready' and not listing[0]['reread']:
                        break
                    time.sleep(0.05)
                entry = store.sources()[0]
                self.assertEqual(entry['id'], identifier, 'the re-read must keep the same import id')
                self.assertEqual(entry['catalog_version'], csv_data.CATALOG_VERSION)
                self.assertTrue(entry['categories'], entry)
                self.assertEqual({item['category'] for item in entry['categories']},
                                 {'Super PAC', 'Trade Association', 'Carey', 'Unclassified'})
                self.assertEqual(entry['excluded_rows'], 1)
                self.assertFalse(entry['reread'])
                self.assertFalse(entry['stale'])
                # A second listing must not queue the work again.
                self.assertFalse(store.sources()[0]['reread'])

    def test_a_multi_sheet_workbook_is_re_read_with_the_same_sheets(self):
        # Regression: the record's `sheet` is the sheet the reader ended up on,
        # so replaying it read only the first cycle sheet of a workbook that
        # was originally read as all of them (measured on the real Crypto
        # workbook: 6,343 rows became 1,063 and FAIRSHAKE vanished).
        with temp_fixture_dir() as fixtures, temp_data_root() as root:
            write_master(fixtures)
            with master_at(fixtures):
                store, identifier = self.stale(root, fixtures, writer=write_two_sheet_workbook)
                record = json.loads(store.record_path(identifier).read_text(encoding='utf-8'))
                self.assertEqual(record['catalog']['row_count'], 9)
                self.assertGreater(len(record.get('sheets_read') or []), 1, record.get('sheets_read'))
                self.assertTrue(record['sheet'], 'the reader records the sheet it settled on')
                deadline = time.time() + 180
                while time.time() < deadline:
                    listing = store.sources()
                    if listing and not listing[0]['reread']:
                        break
                    time.sleep(0.05)
                entry = store.sources()[0]
                self.assertEqual(entry['catalog_version'], csv_data.CATALOG_VERSION,
                                 json.loads(store.record_path(identifier).read_text(encoding='utf-8'))
                                 .get('reread_message'))
                self.assertEqual(entry['row_count'], 9, 'every cycle sheet must be read again')
                self.assertEqual({item['id'] for item in entry['committees']},
                                 {'C00000004', 'C00000005', 'C00000008', 'C00000011'})
                self.assertEqual(store.select(identifier, 'C00000004')['total_rows'], 4)

    def test_a_re_read_that_loses_rows_is_refused(self):
        # The guard behind the regression above: if the reader options are ever
        # not reproduced, the import the reader already had must survive.
        with temp_fixture_dir() as fixtures, temp_data_root() as root:
            write_master(fixtures)
            with master_at(fixtures):
                store, identifier = self.stale(root, fixtures)
                record_path = store.record_path(identifier)
                record = json.loads(record_path.read_text(encoding='utf-8'))
                record['catalog']['row_count'] = 99
                record_path.write_text(json.dumps(record), encoding='utf-8')
                deadline = time.time() + 180
                while time.time() < deadline:
                    listing = store.sources()
                    if listing and not listing[0]['reread']:
                        break
                    time.sleep(0.05)
                entry = store.sources()[0]
                self.assertEqual(entry['catalog_version'], 0, 'the stale catalog must be kept')
                self.assertTrue(entry['stale'])
                self.assertEqual(entry['row_count'], 99, 'the old catalog is left exactly as it was')
                refused = json.loads(record_path.read_text(encoding='utf-8'))
                self.assertEqual(refused['reread_failed'], csv_data.CATALOG_VERSION)
                self.assertIn('not the 99 this import was stored with', refused['reread_message'])
                self.assertTrue(store.source_path(identifier, record.get('suffix', '')).is_file(),
                                'a refused re-read must not delete the stored file')
                self.assertFalse((store.directory(identifier) / 'rows.reread.sqlite').exists())
                # And it is not retried on every listing.
                self.assertFalse(store.sources()[0]['reread'])

    def test_read_only_console_lists_the_stale_entry_without_re_reading(self):
        with temp_fixture_dir() as fixtures, temp_data_root() as root:
            write_master(fixtures)
            with master_at(fixtures):
                _, identifier = self.stale(root, fixtures)
                preview = csv_data.Store(root, evict=False)
                entry = preview.sources()[0]
                self.assertEqual(entry['id'], identifier)
                self.assertTrue(entry['stale'])
                self.assertFalse(entry['reread'])
                self.assertEqual(entry['catalog_version'], 0)
                record = json.loads(preview.record_path(identifier).read_text(encoding='utf-8'))
                self.assertNotIn('catalog_version', record)


if __name__ == '__main__':
    unittest.main()
