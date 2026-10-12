"""S6: the per-committee summary endpoint and the listing's `industry` label.

Written against the S6 brief (Paul's rulings 1-7 on S6_DESIGN.md, 2026-09-17)
BEFORE the implementation landed, and run first against the untouched tree to
record the RED baseline in receipts/S6_before.txt.

Four things are pinned here:
  * `GET /api/evidence-csv?id=&committee=&summary=1` answers one committee's
    channel totals by cycle, its top counterparties, and its supported /
    excluded / unsupported counts, computed from the import's own sqlite;
  * a cycle in the request narrows the totals, the counterparties and the
    counts, and leaves `totals_by_cycle` whole so a pane can still draw the
    by-cycle bars;
  * an unknown committee is the existing M_COMMITTEE 400, and a request with
    no `summary` is the unchanged row page;
  * the listing gains `industry`, derived from a Mentat workbook file name and
    backfilled for catalogs that predate the field, so no re-read is needed.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import evidence_csv as csv_data  # noqa: E402
import server as app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / 'audits' / 'mentat_failings_20260915' / 'contracts'

try:
    import openpyxl
except ImportError:  # the launcher Python reads workbooks with python-calamine
    openpyxl = None

# Verbatim, so a reworded refusal is an assertion failure and not an
# AttributeError that could read as "not finished yet".
M_COMMITTEE = 'Choose a committee in this imported CSV.'
M_CYCLE = 'Choose a cycle recorded in this CSV.'

PRIMARY = 'C00000935'      # DCCC in the real workbooks; a party committee here
SECOND = 'C00835959'       # FAIRSHAKE in the real workbooks; a super PAC here
NOT_REPORTED = 'Not reported'


def rmtree_retry(path, attempts=15, delay=0.2):
    path = Path(path)
    if not path.exists():
        return
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            if attempt == attempts - 1:
                return
            time.sleep(delay)


@contextmanager
def temp_data_root():
    root = Path(tempfile.mkdtemp(prefix='mentat-evidence-summary-'))
    try:
        yield root
    finally:
        rmtree_retry(root)


@contextmanager
def temp_fixture_dir():
    root = Path(tempfile.mkdtemp(prefix='mentat-evidence-summary-files-'))
    try:
        yield root
    finally:
        rmtree_retry(root)


# ---------------------------------------------------------------------------
# One fixture plan. The CSV text and every expectation are rendered from the
# same list, so the file and the arithmetic cannot drift apart.
# ---------------------------------------------------------------------------
COLUMNS = ['Committee ID', 'Committee Name', 'Recipient Category', 'Record Type',
           'Include in Analysis', 'Inclusion Reason', 'Contributor Name',
           'Contribution Receipt Amount', 'Contribution Receipt Date',
           'Two Year Transaction Period', 'Disbursement Amount', 'Disbursement Date']

# (committee, category, contributor, amount, cycle, include, kind)
PLAN = [
    (PRIMARY, 'Party Committee', 'COINBASE', '5000.00', '2026', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'COINBASE', '3000.00', '2026', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'ARMSTRONG, BRIAN', '2500.00', '2026', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'POLLAK, JESSE', '1500.00', '2024', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', '', '900.00', '2024', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'TUSAR, GREGORY', '700.00', '2024', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'KNOWLTON, ALDEN', '600.00', '2022', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'CHOI, EMILIE', '550.00', '2022', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'HAAS, ALESIA', '500.00', '2022', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'CALVERT, KARA', '450.00', '2022', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'CANAVAN, MIKE', '400.00', '2022', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'NIELSEN, STERLING', '350.00', '2022', 'Yes', 'receipt'),
    (PRIMARY, 'Party Committee', 'MOSER, AMY', '300.00', '2022', 'Yes', 'receipt'),
    # Excluded by the workbook: visible, out of every total.
    (PRIMARY, 'Party Committee', 'COINBASE', '99999.00', '2026', 'No', 'receipt'),
    (PRIMARY, 'Party Committee', 'COINBASE', '88888.00', '2024', 'No', 'receipt'),
    # A disbursement, so the committee has a second channel.
    (PRIMARY, 'Party Committee', 'MEDIA BUY LLC', '1200.00', '2026', 'Yes', 'disbursement'),
    # A second committee, for the unknown-committee and per-committee scoping.
    (SECOND, 'Super PAC', 'COINBASE', '117092475.00', '2024', 'Yes', 'receipt'),
    (SECOND, 'Super PAC', 'COINBASE COMMERCE', '59881739.00', '2026', 'Yes', 'receipt'),
]
EXPECTED_COUNTERPARTY_COUNT = 10  # the plan gives PRIMARY 13 distinct names


def quoted(value):
    """A contributor name is "SURNAME, FORENAME"; an unquoted comma would
    split the row and make every following cell an extra cell."""
    text = str(value)
    return '"' + text.replace('"', '""') + '"' if ',' in text or '"' in text else text


def csv_text():
    lines = [','.join(COLUMNS)]
    for committee, category, contributor, amount, cycle, include, kind in PLAN:
        cells = {name: '' for name in COLUMNS}
        cells['Committee ID'] = committee
        cells['Committee Name'] = 'DCCC' if committee == PRIMARY else 'FAIRSHAKE'
        cells['Recipient Category'] = category
        cells['Record Type'] = 'CONTRIBUTION' if kind == 'receipt' else 'DISBURSEMENT'
        cells['Include in Analysis'] = include
        cells['Inclusion Reason'] = '' if include == 'Yes' else 'Exact five-key memo duplicate'
        cells['Contributor Name'] = contributor
        cells['Two Year Transaction Period'] = cycle
        if kind == 'receipt':
            cells['Contribution Receipt Amount'] = amount
            cells['Contribution Receipt Date'] = f'{int(cycle) - 1}-06-01'
        else:
            cells['Disbursement Amount'] = amount
            cells['Disbursement Date'] = f'{int(cycle) - 1}-07-01'
        lines.append(','.join(quoted(cells[name]) for name in COLUMNS))
    return '\r\n'.join(lines) + '\r\n'


def supported_plan(committee=PRIMARY, cycle=''):
    return [row for row in PLAN
            if row[0] == committee and row[5] == 'Yes' and (not cycle or row[4] == cycle)]


def expected_totals(committee=PRIMARY, cycle=''):
    buckets = {}
    for _, _, _, amount, row_cycle, _, kind in supported_plan(committee, cycle):
        channel = 'Receipts' if kind == 'receipt' else 'Disbursements'
        amount_total, count = buckets.get(channel, (Decimal('0'), 0))
        buckets[channel] = (amount_total + Decimal(amount), count + 1)
    return [{'channel': channel, 'amount': float(buckets[channel][0]), 'row_count': buckets[channel][1]}
            for channel in sorted(buckets)]


def expected_totals_by_cycle(committee=PRIMARY):
    cycles = sorted({row[4] for row in PLAN if row[0] == committee}, reverse=True)
    return {cycle: expected_totals(committee, cycle) for cycle in cycles
            if expected_totals(committee, cycle)}


def expected_counterparties(committee=PRIMARY, cycle=''):
    buckets = {}
    for _, _, contributor, amount, _, _, _ in supported_plan(committee, cycle):
        name = contributor or NOT_REPORTED
        amount_total, rows = buckets.get(name, (Decimal('0'), 0))
        buckets[name] = (amount_total + Decimal(amount), rows + 1)
    ordered = sorted(buckets.items(), key=lambda item: (-item[1][0], item[0]))
    return [{'name': name, 'amount': float(amount), 'rows': rows}
            for name, (amount, rows) in ordered[:EXPECTED_COUNTERPARTY_COUNT]]


def expected_counts(committee=PRIMARY, cycle=''):
    rows = [row for row in PLAN if row[0] == committee and (not cycle or row[4] == cycle)]
    supported = sum(1 for row in rows if row[5] == 'Yes')
    excluded = sum(1 for row in rows if row[5] != 'Yes')
    return {'supported': supported, 'excluded': excluded,
            'unsupported': len(rows) - supported - excluded}


def ingest_fixture(store, name='summary-fixture.csv'):
    job = store.wait(store.ingest_text(name, csv_text())['id'])
    if job['state'] != 'ready':
        raise AssertionError(f"fixture import failed: {job.get('message')}")
    return job['id']


def workbook_at(directory, name):
    """A real .xlsx, so the industry label comes off a real Mentat file name."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = '2025-2026'
    sheet.append(COLUMNS)
    for committee, category, contributor, amount, cycle, include, kind in PLAN:
        row = {key: '' for key in COLUMNS}
        row['Committee ID'] = committee
        row['Committee Name'] = 'DCCC' if committee == PRIMARY else 'FAIRSHAKE'
        row['Recipient Category'] = category
        row['Record Type'] = 'CONTRIBUTION' if kind == 'receipt' else 'DISBURSEMENT'
        row['Include in Analysis'] = include
        row['Contributor Name'] = contributor
        row['Two Year Transaction Period'] = cycle
        if kind == 'receipt':
            row['Contribution Receipt Amount'] = amount
        else:
            row['Disbursement Amount'] = amount
        sheet.append([row[key] for key in COLUMNS])
    path = Path(directory) / name
    book.save(path)
    return path


# ---------------------------------------------------------------------------
# HTTP harness, the same one the S3 and S7 suites use.
# ---------------------------------------------------------------------------
def start_console_server(root, *, read_only=True):
    with patch.object(app, 'report_directory', return_value=root), \
         patch.object(app, 'environment_keys', return_value=[]):
        console = app.Console(root / 'state', read_only=read_only)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    server.app = console
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return console, server, worker


def stop_console_server(server, worker):
    server.shutdown()
    server.server_close()
    worker.join(5)


def get_json(server, path):
    port = server.server_address[1]
    with urlopen(f'http://127.0.0.1:{port}{path}', timeout=30) as response:
        return response.status, json.loads(response.read().decode('utf-8'))


def get_error(server, path):
    port = server.server_address[1]
    try:
        with urlopen(f'http://127.0.0.1:{port}{path}', timeout=30) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode('utf-8'))


# ---------------------------------------------------------------------------
# 1. The industry label
# ---------------------------------------------------------------------------
class IndustryLabelTests(unittest.TestCase):
    def test_overview_workbook_name_gives_the_industry(self):
        self.assertEqual(csv_data.industry_of('Crypto Industry Spending Overview.xlsx'), 'Crypto')

    def test_data_workbook_name_gives_the_industry(self):
        self.assertEqual(csv_data.industry_of('Credit Union Industry Spending Data.xlsx'), 'Credit Union')

    def test_match_is_case_insensitive(self):
        self.assertEqual(csv_data.industry_of('property & casualty INDUSTRY SPENDING overview.XLSX'),
                         'property & casualty')

    def test_non_mentat_names_have_no_industry(self):
        for name in ['six.csv', 'schedule_a.csv', 'Crypto Industry Spending Overview.csv',
                     'Industry Spending Overview.xlsx', 'Crypto Industry Spending Summary.xlsx',
                     'Crypto Spending Overview.xlsx', '', 'rows.sqlite']:
            with self.subTest(name=name):
                self.assertEqual(csv_data.industry_of(name), '')

    def test_shortest_industry_wins_over_a_greedy_match(self):
        # `.+?` is lazy on purpose: a file that repeats the phrase must not
        # swallow the first copy into the label.
        self.assertEqual(csv_data.industry_of('Crypto Industry Spending Overview Industry Spending Data.xlsx'),
                         'Crypto Industry Spending Overview')

    def test_listing_and_catalog_carry_the_industry(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            self.assertEqual(store.catalog(identifier)['industry'], '')
            listing = store.sources()
            self.assertEqual(len(listing), 1)
            self.assertIn('industry', listing[0])
            self.assertEqual(listing[0]['industry'], '')

    @unittest.skipIf(openpyxl is None, 'openpyxl is not installed in this Python')
    def test_a_real_workbook_name_derives_the_industry(self):
        with temp_data_root() as root, temp_fixture_dir() as files:
            path = workbook_at(files, 'Crypto Industry Spending Overview.xlsx')
            store = csv_data.Store(root)
            job = store.wait(store.import_path(str(path))['id'])
            self.assertEqual(job['state'], 'ready', job.get('message'))
            self.assertEqual(store.catalog(job['id'])['industry'], 'Crypto')
            self.assertEqual(store.sources()[0]['industry'], 'Crypto')

    def test_industry_is_backfilled_for_a_catalog_that_predates_it(self):
        # The field is added by stored_catalog(), not written at ingest, so an
        # import on disk since S3 answers with it and never needs a re-read.
        record = {'id': 'csv-' + '0' * 32, 'name': 'Crypto Industry Spending Overview.xlsx',
                  'catalog_version': csv_data.CATALOG_VERSION, 'rows_read': 3,
                  'catalog': {'id': 'csv-' + '0' * 32, 'name': 'Crypto Industry Spending Overview.xlsx',
                              'row_count': 3, 'committees': [], 'cycles': []}}
        self.assertEqual(csv_data.stored_catalog(record)['industry'], 'Crypto')

    def test_industry_falls_back_to_the_source_path_basename(self):
        record = {'id': 'csv-' + '1' * 32, 'name': 'upload.xlsx', 'catalog_version': csv_data.CATALOG_VERSION,
                  'source_kind': 'path', 'source_path': r'D:\books\Fintech Industry Spending Data.xlsx',
                  'catalog': {'id': 'csv-' + '1' * 32, 'name': 'upload.xlsx', 'row_count': 1,
                              'committees': [], 'cycles': []}}
        self.assertEqual(csv_data.stored_catalog(record)['industry'], 'Fintech')

    def test_an_empty_record_still_yields_an_empty_catalog(self):
        self.assertEqual(csv_data.stored_catalog({'id': 'csv-x', 'catalog': None}), {})


# ---------------------------------------------------------------------------
# 2. Store.summary
# ---------------------------------------------------------------------------
class SummaryStoreTests(unittest.TestCase):
    def test_summary_shape_and_figures_for_all_cycles(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            summary = store.summary(identifier, PRIMARY)
            self.assertEqual(set(summary), {'source', 'committee', 'cycles', 'selected_cycle',
                                            'totals_by_cycle', 'totals', 'counterparties', 'counts'})
            self.assertEqual(set(summary['source']), {'id', 'name', 'industry'})
            self.assertEqual(summary['source']['id'], identifier)
            self.assertEqual(summary['source']['name'], 'summary-fixture.csv')
            self.assertEqual(summary['source']['industry'], '')
            self.assertEqual(set(summary['committee']),
                             {'id', 'name', 'category', 'category_source', 'rows',
                              'supported_rows', 'excluded_rows'})
            self.assertEqual(summary['committee']['id'], PRIMARY)
            self.assertEqual(summary['committee']['name'], 'DCCC')
            self.assertEqual(summary['committee']['category'], 'Party Committee')
            self.assertEqual(summary['committee']['rows'],
                             len([row for row in PLAN if row[0] == PRIMARY]))
            self.assertEqual(summary['selected_cycle'], '')
            self.assertEqual(summary['cycles'], ['2026', '2024', '2022'])
            self.assertEqual(summary['totals'], expected_totals(PRIMARY))
            self.assertEqual(summary['totals_by_cycle'], expected_totals_by_cycle(PRIMARY))
            self.assertEqual(summary['counts'], expected_counts(PRIMARY))

    def test_counterparties_are_the_top_ten_by_amount_over_supported_rows(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            summary = store.summary(ingest_fixture(store), PRIMARY)
            self.assertEqual(summary['counterparties'], expected_counterparties(PRIMARY))
            self.assertEqual(len(summary['counterparties']), EXPECTED_COUNTERPARTY_COUNT)
            names = [item['name'] for item in summary['counterparties']]
            # COINBASE's two supported receipts are one counterparty of 8,000,
            # and its two Include=No rows (99,999 + 88,888) are not in it.
            self.assertEqual(summary['counterparties'][0], {'name': 'COINBASE', 'amount': 8000.0, 'rows': 2})
            self.assertIn(NOT_REPORTED, names)
            self.assertNotIn('', names)
            amounts = [item['amount'] for item in summary['counterparties']]
            self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_a_cycle_narrows_totals_counterparties_and_counts(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            summary = store.summary(ingest_fixture(store), PRIMARY, '2024')
            self.assertEqual(summary['selected_cycle'], '2024')
            self.assertEqual(summary['totals'], expected_totals(PRIMARY, '2024'))
            self.assertEqual(summary['counterparties'], expected_counterparties(PRIMARY, '2024'))
            self.assertEqual(summary['counts'], expected_counts(PRIMARY, '2024'))
            # The bars stay whole so a pane can draw every cycle at once.
            self.assertEqual(summary['totals_by_cycle'], expected_totals_by_cycle(PRIMARY))
            self.assertEqual(summary['cycles'], ['2026', '2024', '2022'])

    def test_each_committee_is_summarised_alone(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            second = store.summary(identifier, SECOND)
            self.assertEqual(second['committee']['name'], 'FAIRSHAKE')
            self.assertEqual(second['committee']['category'], 'Super PAC')
            self.assertEqual(second['totals'], expected_totals(SECOND))
            self.assertEqual(second['counterparties'], expected_counterparties(SECOND))
            self.assertEqual(second['counts'], expected_counts(SECOND))

    def test_unknown_committee_is_the_existing_refusal(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            with self.assertRaises(ValueError) as caught:
                store.summary(identifier, 'C09999999')
            self.assertEqual(str(caught.exception), M_COMMITTEE)

    def test_unknown_cycle_is_the_existing_refusal(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            with self.assertRaises(ValueError) as caught:
                store.summary(identifier, PRIMARY, '1999')
            self.assertEqual(str(caught.exception), M_CYCLE)

    def test_a_blank_committee_takes_the_first_one_like_select(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            first = store.catalog(identifier)['committees'][0]['id']
            self.assertEqual(store.summary(identifier, '')['committee']['id'], first)


# ---------------------------------------------------------------------------
# 3. The HTTP surface
# ---------------------------------------------------------------------------
class SummaryRouteTests(unittest.TestCase):
    def test_summary_route_answers_the_same_payload(self):
        with temp_data_root() as root:
            store = csv_data.Store(root / 'state')
            identifier = ingest_fixture(store)
            console, server, worker = start_console_server(root)
            try:
                query = urllib.parse.urlencode({'id': identifier, 'committee': PRIMARY, 'summary': '1'})
                status, body = get_json(server, '/api/evidence-csv?' + query)
                self.assertEqual(status, 200)
                self.assertEqual(set(body), {'source', 'committee', 'cycles', 'selected_cycle',
                                             'totals_by_cycle', 'totals', 'counterparties', 'counts'})
                self.assertEqual(body['totals'], expected_totals(PRIMARY))
                self.assertEqual(body['counts'], expected_counts(PRIMARY))
                self.assertEqual(body['counterparties'], expected_counterparties(PRIMARY))
            finally:
                stop_console_server(server, worker)

    def test_summary_route_honours_the_cycle(self):
        with temp_data_root() as root:
            store = csv_data.Store(root / 'state')
            identifier = ingest_fixture(store)
            console, server, worker = start_console_server(root)
            try:
                query = urllib.parse.urlencode({'id': identifier, 'committee': PRIMARY,
                                                'cycle': '2026', 'summary': '1'})
                status, body = get_json(server, '/api/evidence-csv?' + query)
                self.assertEqual(status, 200)
                self.assertEqual(body['selected_cycle'], '2026')
                self.assertEqual(body['totals'], expected_totals(PRIMARY, '2026'))
            finally:
                stop_console_server(server, worker)

    def test_summary_route_refuses_an_unknown_committee_with_400(self):
        with temp_data_root() as root:
            store = csv_data.Store(root / 'state')
            identifier = ingest_fixture(store)
            console, server, worker = start_console_server(root)
            try:
                query = urllib.parse.urlencode({'id': identifier, 'committee': 'C09999999', 'summary': '1'})
                status, body = get_error(server, '/api/evidence-csv?' + query)
                self.assertEqual(status, 400)
                self.assertEqual(body, {'error': M_COMMITTEE})
            finally:
                stop_console_server(server, worker)

    def test_the_row_page_is_unchanged_without_summary(self):
        with temp_data_root() as root:
            store = csv_data.Store(root / 'state')
            identifier = ingest_fixture(store)
            console, server, worker = start_console_server(root)
            try:
                query = urllib.parse.urlencode({'id': identifier, 'committee': PRIMARY})
                status, body = get_json(server, '/api/evidence-csv?' + query)
                self.assertEqual(status, 200)
                self.assertIn('rows', body)
                self.assertIn('total_rows', body)
                self.assertNotIn('counterparties', body)
                self.assertEqual(body['committee']['id'], PRIMARY)
            finally:
                stop_console_server(server, worker)

    def test_the_listing_carries_industry_for_every_source(self):
        with temp_data_root() as root:
            store = csv_data.Store(root / 'state')
            ingest_fixture(store)
            console, server, worker = start_console_server(root)
            try:
                status, body = get_json(server, '/api/evidence-csv')
                self.assertEqual(status, 200)
                self.assertTrue(body['sources'])
                for item in body['sources']:
                    self.assertIn('industry', item)
            finally:
                stop_console_server(server, worker)


# ---------------------------------------------------------------------------
# 4. Contract evidence-csv-import.json v4
# ---------------------------------------------------------------------------
def load_contract(name):
    return json.loads((CONTRACTS / f'{name}.json').read_text(encoding='utf-8'))


def group_by_keyset(examples):
    groups = {}
    for example in examples:
        groups.setdefault(frozenset(example.keys()), []).append(example)
    return groups


def type_family(value):
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if isinstance(value, list):
        return 'list'
    if isinstance(value, dict):
        return 'dict'
    return 'null' if value is None else 'other'


def assert_shape(value, examples, enums=None, path='$'):
    """The same matcher test_evidence_csv_stream.py uses: one key set, and each
    field's JSON type family must be one an example shows."""
    keysets = {frozenset(example.keys()) for example in examples}
    assert len(keysets) == 1, f'{path}: contract examples disagree on keys'
    expected = next(iter(keysets))
    assert isinstance(value, dict), f'{path}: expected an object, got {type(value).__name__}'
    assert set(value.keys()) == set(expected), (
        f'{path}: keys differ - missing {sorted(set(expected) - set(value))}, '
        f'extra {sorted(set(value) - set(expected))}')
    for key in expected:
        families = {type_family(example[key]) for example in examples}
        assert type_family(value[key]) in families, (
            f'{path}.{key}: {type_family(value[key])} is not one of {sorted(families)}')
        if enums and key in enums:
            assert value[key] in enums[key], f'{path}.{key}: {value[key]!r} is out of vocabulary'


IMPORT_CONTRACT = load_contract('evidence-csv-import')
IMPORT_GROUPS = group_by_keyset(IMPORT_CONTRACT['examples'])
SUMMARY_KEYS = frozenset({'source', 'committee', 'cycles', 'selected_cycle',
                          'totals_by_cycle', 'totals', 'counterparties', 'counts'})


def assert_summary(value):
    examples = IMPORT_GROUPS[SUMMARY_KEYS]
    assert_shape(value, examples, path='$summary')
    assert_shape(value['source'], [example['source'] for example in examples], path='$summary.source')
    assert_shape(value['committee'], [example['committee'] for example in examples],
                 enums={'category': set(csv_data.CATEGORIES) | {''}}, path='$summary.committee')
    assert_shape(value['counts'], [example['counts'] for example in examples], path='$summary.counts')
    total_examples = [total for example in examples for total in example['totals']]
    for index, total in enumerate(value['totals']):
        assert_shape(total, total_examples, path=f'$summary.totals[{index}]')
    for cycle, totals in (value['totals_by_cycle'] or {}).items():
        assert isinstance(cycle, str) and cycle.isdigit(), (
            f'$summary.totals_by_cycle: {cycle!r} is not a cycle string')
        for index, total in enumerate(totals):
            assert_shape(total, total_examples, path=f'$summary.totals_by_cycle[{cycle}][{index}]')
    party_examples = [party for example in examples for party in example['counterparties']]
    assert len(value['counterparties']) <= 10, '$summary.counterparties: more than ten'
    previous = None
    for index, party in enumerate(value['counterparties']):
        assert_shape(party, party_examples, path=f'$summary.counterparties[{index}]')
        assert party['name'], f'$summary.counterparties[{index}].name is blank'
        if previous is not None:
            assert party['amount'] <= previous, '$summary.counterparties: not ordered by amount'
        previous = party['amount']
    for cycle in value['cycles']:
        assert isinstance(cycle, str), f'$summary.cycles: {cycle!r} is not a string'
    assert value['selected_cycle'] in ([''] + list(value['cycles'])), (
        '$summary.selected_cycle is not blank and not one of cycles')


SUMMARY_DISPATCH = {'summary': assert_summary}


class ContractTests(unittest.TestCase):
    # Integration pass (2026-09-23): v5 is console redesign build 06's listing figure for
    # the picker rail (board B19); re-homed from test_contract_version_is_four.
    def test_contract_version_is_five(self):
        self.assertEqual(IMPORT_CONTRACT['version'], 5)
        self.assertIn('Store.committee_figures', ' '.join(IMPORT_CONTRACT['produced_by']))

    def test_contract_names_the_summary_producer_and_consumer(self):
        produced = ' '.join(IMPORT_CONTRACT['produced_by'])
        consumed = ' '.join(IMPORT_CONTRACT['consumed_by'])
        self.assertIn('Store.summary', produced)
        self.assertIn('summary=1', produced)
        self.assertIn('evidence.js', consumed)

    def test_summary_examples_match_the_producer(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = ingest_fixture(store)
            assert_summary(store.summary(identifier, PRIMARY))
            assert_summary(store.summary(identifier, PRIMARY, '2024'))
            assert_summary(store.summary(identifier, SECOND))

    def test_catalog_examples_carry_industry(self):
        for example in IMPORT_CONTRACT['examples']:
            if 'committees' in example and 'row_count' in example and 'state' in example:
                with self.subTest(example=example.get('id')):
                    self.assertIn('industry', example)

    def test_summary_negative_controls_all_fail(self):
        controls = [control for control in IMPORT_CONTRACT['negative_controls']
                    if control['shape'] == 'summary']
        self.assertGreaterEqual(len(controls), 3)
        for control in controls:
            with self.subTest(why=control['why']):
                with self.assertRaises(AssertionError):
                    SUMMARY_DISPATCH[control['shape']](control['payload'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
