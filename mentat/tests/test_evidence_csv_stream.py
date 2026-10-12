"""Streaming CSV evidence import (S3): fixtures built and summed from one plan,
contract-verified shapes, and the HTTP surface. Written against the S3 CONTRACT
in audits/mentat_failings_20260915/HANDOFF.md before the implementation landed;
run first against a pristine worktree of HEAD to record the RED baseline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
import urllib.parse
import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import evidence_csv as csv_data  # noqa: E402
import server as app  # noqa: E402
import fec_key_store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = REPO_ROOT / 'audits' / 'mentat_failings_20260915' / 'contracts'


def load_contract(name):
    return json.loads((CONTRACTS / f'{name}.json').read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# Exact messages, copied verbatim from the S3 CONTRACT. Hardcoded here rather
# than read off evidence_csv.py, so a producer that silently reworded a
# message (or never exported it as an importable name) shows up as a real
# assertion failure instead of an AttributeError that could be mistaken for
# "the implementation just isn't finished yet".
# ---------------------------------------------------------------------------
M_NAME = 'Choose a CSV file with a name ending in .csv.'
M_EMPTY = 'The CSV file is empty.'
M_UTF8 = 'Use a UTF-8 CSV file without invalid text characters.'
M_BYTES = 'Choose a CSV no larger than 500 MB.'
M_COLUMNS = 'The CSV needs unique nonempty column names, with no more than 200 columns.'
M_COMMITTEE_COLUMN = 'The CSV needs a Committee ID, CMTE_ID, or Source Committee ID column.'
M_ROWS = 'Choose a CSV containing no more than 5,000,000 rows.'
M_NO_COMMITTEE_ROWS = 'The CSV has no rows with one valid FEC committee ID (C followed by eight digits).'
M_BUSY = 'Another CSV is still being read. Wait for it to finish.'
M_PAGE = 'Choose a valid CSV row page (1–1,000 rows).'
M_INTERRUPTED = 'Reading was interrupted by a console restart. Choose the file again.'
M_COMMITTEE = 'Choose a committee in this imported CSV.'
M_CYCLE = 'Choose a cycle recorded in this CSV.'


def m_read(row_number):
    return f'The CSV could not be read at row {row_number:,}. Check its quoted fields and delimiter.'


def m_gone(identifier):
    return f'This CSV import is no longer on disk ({identifier}). Choose the file again.'


def m_short(received, expected):
    return f'The upload ended early: {received:,} of {expected:,} bytes arrived.'


def m_rows(limit):
    return f'Choose a CSV containing no more than {limit:,} rows.'


# ---------------------------------------------------------------------------
# Fixture: a single deterministic plan drives both the CSV text and the
# expected Decimal totals, so the two can never drift apart (the packet's
# DE-021 lesson applied to test authorship, not just cross-language contracts).
# ---------------------------------------------------------------------------
FIXTURE_COLUMNS = [
    'committee_id', 'committee_name', 'contribution_receipt_amount', 'contribution_receipt_date',
    'two_year_transaction_period', 'contributor_name', 'sub_id', 'disbursement_amount',
    'disbursement_date', 'expenditure_amount', 'expenditure_date', 'support_oppose',
    'net_included_amount', 'source_committee_id', 'candidate_id', 'candidate_name',
    'filler_1', 'filler_2', 'filler_3', 'filler_4',
]
RECEIPT_AMOUNT = Decimal('10.00')
DISBURSEMENT_AMOUNT = Decimal('5.00')
INDEPENDENT_AMOUNT = Decimal('7.50')
AGGREGATE_AMOUNT = Decimal('1234.56')
PRIMARY_COMMITTEE = 'C00000001'


def row_plan(index, total):
    """Deterministic description of fixture row `index` (1-based) of `total`.

    Cycles receipt -> disbursement -> independent_spending; every 37th row is
    an unassigned row (blank committee, excluded from every total); the LAST
    row is a reported_aggregate row. `row_line` renders exactly this plan to
    CSV text, and `expected_totals` sums exactly this plan -- the CSV text and
    the expectation can never independently drift.
    """
    cycle = '2026' if (index // 3) % 2 == 0 else '2024'
    if index == total:
        return {'kind': 'reported_aggregate', 'channel': 'Reported cycle aggregate · support',
                 'amount': AGGREGATE_AMOUNT, 'committee_id': PRIMARY_COMMITTEE, 'cycle': '2026', 'supported': True}
    if index % 37 == 0:
        return {'kind': 'unassigned', 'channel': None, 'amount': None,
                 'committee_id': '', 'cycle': cycle, 'supported': False}
    remainder = index % 3
    if remainder == 1:
        return {'kind': 'receipt', 'channel': 'Receipts', 'amount': RECEIPT_AMOUNT,
                 'committee_id': PRIMARY_COMMITTEE, 'cycle': cycle, 'supported': True}
    if remainder == 2:
        return {'kind': 'disbursement', 'channel': 'Disbursements', 'amount': DISBURSEMENT_AMOUNT,
                 'committee_id': PRIMARY_COMMITTEE, 'cycle': cycle, 'supported': True}
    direction = 'support' if (index // 3) % 2 == 0 else 'oppose'
    return {'kind': 'independent_spending', 'channel': f'Independent spending · {direction}',
            'amount': INDEPENDENT_AMOUNT, 'committee_id': PRIMARY_COMMITTEE, 'cycle': cycle, 'supported': True}


def row_line(index, total):
    plan = row_plan(index, total)
    cells = {name: '' for name in FIXTURE_COLUMNS}
    cells['sub_id'] = f'SA-{index}'
    cells['filler_1'], cells['filler_2'] = 'FILLER-A', 'FILLER-B'
    cells['filler_3'], cells['filler_4'] = 'FILLER-C', 'FILLER-D'
    kind = plan['kind']
    if kind == 'receipt':
        cells['committee_id'] = plan['committee_id']
        cells['committee_name'] = 'Example PAC'
        cells['contribution_receipt_amount'] = str(plan['amount'])
        cells['contribution_receipt_date'] = '2026-01-%02d' % (1 + index % 28)
        cells['two_year_transaction_period'] = plan['cycle']
        cells['contributor_name'] = f'"Contributor, {index}"'
    elif kind == 'disbursement':
        cells['committee_id'] = plan['committee_id']
        cells['committee_name'] = 'Example PAC'
        cells['disbursement_amount'] = str(plan['amount'])
        cells['disbursement_date'] = '2026-02-%02d' % (1 + index % 28)
        cells['two_year_transaction_period'] = plan['cycle']
    elif kind == 'independent_spending':
        cells['committee_id'] = plan['committee_id']
        cells['committee_name'] = 'Example PAC'
        cells['expenditure_amount'] = str(plan['amount'])
        cells['expenditure_date'] = '2026-03-%02d' % (1 + index % 28)
        cells['two_year_transaction_period'] = plan['cycle']
        cells['support_oppose'] = 'S' if plan['channel'].endswith('support') else 'O'
        cells['candidate_id'] = 'H0CA%05d' % (index % 100000)
        cells['candidate_name'] = f'Candidate {index}'
    elif kind == 'unassigned':
        cells['committee_name'] = 'Unassigned Example'
        cells['contribution_receipt_amount'] = str(RECEIPT_AMOUNT)
        cells['contribution_receipt_date'] = '2026-01-10'
        cells['two_year_transaction_period'] = plan['cycle']
    else:  # reported_aggregate
        cells['source_committee_id'] = plan['committee_id']
        cells['net_included_amount'] = str(plan['amount'])
        cells['support_oppose'] = 'Support'
        cells['candidate_id'] = 'H0CA00001'
        cells['candidate_name'] = 'Aggregate Candidate'
        cells['two_year_transaction_period'] = plan['cycle']
    return ','.join(cells[name] for name in FIXTURE_COLUMNS) + '\r\n'


def build_fixture_text(total_rows):
    header = ','.join(FIXTURE_COLUMNS) + '\r\n'
    return header + ''.join(row_line(index, total_rows) for index in range(1, total_rows + 1))


def expected_totals(total_rows, *, committee=PRIMARY_COMMITTEE, cycle=None):
    """Sum row_plan()'s own numbers exactly the way Store.select is expected to:
    only supported rows, filtered by committee (or 'unassigned') and optional
    cycle. Returns (totals_list, matching_row_count)."""
    totals, counts, matching = {}, {}, 0
    for index in range(1, total_rows + 1):
        plan = row_plan(index, total_rows)
        row_committee = plan['committee_id'] or 'unassigned'
        if row_committee != committee:
            continue
        if cycle and plan['cycle'] != cycle:
            continue
        matching += 1
        if plan['supported']:
            totals[plan['channel']] = totals.get(plan['channel'], Decimal('0')) + plan['amount']
            counts[plan['channel']] = counts.get(plan['channel'], 0) + 1
    ordered = [{'channel': channel, 'amount': float(amount), 'row_count': counts[channel]}
               for channel, amount in sorted(totals.items())]
    return ordered, matching


def wide_csv(column_count):
    headers = ['committee_id'] + [f'col{i}' for i in range(column_count - 1)]
    row = [PRIMARY_COMMITTEE] + ['x'] * (column_count - 1)
    return ','.join(headers) + '\r\n' + ','.join(row) + '\r\n'


def rmtree_retry(path, attempts=15, delay=0.2):
    """Windows can hold a delete handle on a just-closed sqlite file briefly;
    retry rather than fail the test on an unrelated cleanup race."""
    path = Path(path)
    if not path.exists():
        return
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


@contextmanager
def temp_data_root():
    root = Path(tempfile.mkdtemp(prefix='mentat-evidence-csv-'))
    try:
        yield root
    finally:
        rmtree_retry(root)


def start_console_server(root, *, read_only=True):
    with patch.object(app, 'report_directory', return_value=root), patch.object(app, 'environment_keys', return_value=[]):
        console = app.Console(root / 'state', read_only=read_only)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    server.app = console
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return console, server, worker


def stop_console_server(server, worker):
    server.shutdown()
    server.server_close()
    worker.join()


# ---------------------------------------------------------------------------
# Contract assertion helper: schema-by-example. Same key set, same JSON types,
# enum values for state -- and the negative_controls in both contract files
# must fail it, or the assertion has no teeth (brain DE-021).
# ---------------------------------------------------------------------------
def _type_family(value):
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    if value is None:
        return 'null'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, dict):
        return 'object'
    raise TypeError(f'unsupported example value type: {type(value)!r}')


def assert_shape(value, examples, enums=None, path='$'):
    """`examples` is a list of example payloads that together demonstrate every
    state a shape can take; they must all share one key set. `value` must have
    that same key set, and each field's JSON type family must match the type
    family of that field in at least one example (a field that is sometimes
    null and sometimes populated is validated as the union across the
    examples that show both). `enums`: {field_name: {allowed values}}, checked
    in addition to the type-family check. Raises AssertionError naming the
    failing path on any mismatch."""
    enums = enums or {}
    if not isinstance(value, dict):
        raise AssertionError(f'{path}: expected an object, got {type(value).__name__}')
    keyset = set(examples[0].keys())
    for example in examples[1:]:
        if set(example.keys()) != keyset:
            raise AssertionError(f'{path}: contract examples disagree on keys: '
                                  f'{sorted(example.keys())} vs {sorted(keyset)}')
    if set(value.keys()) != keyset:
        raise AssertionError(f'{path}: key set {sorted(value.keys())} != contract key set {sorted(keyset)}')
    for key in sorted(keyset):
        field_path = f'{path}.{key}'
        allowed = {_type_family(example[key]) for example in examples}
        actual = _type_family(value[key])
        if actual not in allowed:
            raise AssertionError(f'{field_path}: type {actual!r} not in contract types {sorted(allowed)}')
        if key in enums and value[key] not in enums[key]:
            raise AssertionError(f'{field_path}: value {value[key]!r} not in allowed set {sorted(enums[key])}')


def _group_by_keyset(examples):
    groups = {}
    for example in examples:
        groups.setdefault(frozenset(example.keys()), []).append(example)
    return groups


IMPORT_CONTRACT = load_contract('evidence-csv-import')
JOB_CONTRACT = load_contract('evidence-job-status')
_import_groups = _group_by_keyset(IMPORT_CONTRACT['examples'])
_job_groups = _group_by_keyset(JOB_CONTRACT['examples'])

# S3b: LIMITS gained the accepted suffixes and the JSON ceiling, and the JOB
# and the CATALOG gained the eight fields that say how an import was read
# (format/source_kind/source_path/sheet/sheets/encoding/delimiter/
# header_source). The producers answer those keys now, so the key sets this
# suite holds them to are the S3b ones; the examples behind them live in the
# same two contract files as before.
LIMITS_KEYS = frozenset({'max_bytes', 'max_columns', 'max_rows', 'max_bytes_message', 'max_columns_message',
                         'max_rows_message', 'accepted_suffixes', 'max_json_bytes'})
DESCRIPTIVE_KEYS = {'format', 'source_kind', 'source_path', 'sheet', 'sheets', 'sheets_read', 'encoding', 'delimiter',
                    'header_source'}
# S7: every catalog now says which categories its committees fall into, how
# many rows the workbook itself excluded, which rule set it was built under,
# and whether this console is re-reading it right now.
CATEGORY_KEYS = {'categories', 'excluded_rows', 'catalog_version', 'state', 'reread', 'stale', 'rows_read'}
# S6: the industry a Mentat workbook file name declares, derived at listing
# time (stored_catalog), so an import read before this field answers with it.
INDUSTRY_KEYS = {'industry'}
CATALOG_KEYS = frozenset({'id', 'name', 'sha256', 'row_count', 'committees', 'cycles', 'supported_rows',
                          'unsupported_rows', 'unassigned_rows', 'session_only', 'imported_at', 'bytes',
                          'warnings'} | DESCRIPTIVE_KEYS | CATEGORY_KEYS | INDUSTRY_KEYS)
SOURCES_LISTING_KEYS = frozenset({'sources', 'limits', 'session_only'})
SELECT_KEYS = frozenset({'source', 'committee', 'committees', 'cycles', 'selected_cycle', 'columns', 'rows',
                         'totals', 'supported_rows', 'excluded_rows', 'unsupported_rows', 'offset', 'limit',
                         'total_rows', 'has_more', 'warnings'})
ERROR_KEYS = frozenset({'error'})
# S6: one committee's figures for a pane, without its rows. tests/
# test_evidence_summary.py holds the full asserter and the producer checks; this
# copy exists so the v4 negative controls fail HERE too - IMPORT_DISPATCH raises
# KeyError, not an assertion, for a shape it does not know.
SUMMARY_KEYS = frozenset({'source', 'committee', 'cycles', 'selected_cycle', 'totals_by_cycle',
                          'totals', 'counterparties', 'counts'})
JOB_KEYS = frozenset({'id', 'name', 'state', 'bytes', 'bytes_read', 'rows_read', 'message', 'started_at',
                      'finished_at', 'catalog'} | DESCRIPTIVE_KEYS)
COLLECTOR_ACTIVE_KEYS = frozenset({'id', 'company', 'aliases', 'committees', 'cycles', 'state', 'pid', 'started_at'})
COLLECTOR_DONE_KEYS = COLLECTOR_ACTIVE_KEYS | {'finished_at', 'message'}

LIMITS_EXAMPLES = _import_groups[LIMITS_KEYS]
CATALOG_EXAMPLES = _import_groups[CATALOG_KEYS]
SOURCES_LISTING_EXAMPLES = _import_groups[SOURCES_LISTING_KEYS]
SELECT_EXAMPLES = _import_groups[SELECT_KEYS]
ERROR_EXAMPLES = _import_groups[ERROR_KEYS]
SUMMARY_EXAMPLES = _import_groups[SUMMARY_KEYS]
COMMITTEE_EXAMPLES = CATALOG_EXAMPLES[0]['committees']
# v5 (console redesign build 06, board B19): in the sources listing, each committee of
# an import that is not being re-read also carries the figure its pane leads with, for the
# PAC picker rail. The listing example's committees show both states (a figure, and none).
LISTED_FIGURE_KEYS = ('receipts', 'receipts_channel', 'receipts_by_cycle')
LISTED_COMMITTEE_EXAMPLES = next(example['sources'][0]['committees']
                                 for example in _import_groups[SOURCES_LISTING_KEYS] if example['sources'])
TOTAL_EXAMPLES = SELECT_EXAMPLES[0]['totals']
JOB_EXAMPLES = _job_groups[JOB_KEYS]
COLLECTOR_ACTIVE_EXAMPLES = _job_groups[COLLECTOR_ACTIVE_KEYS]
COLLECTOR_DONE_EXAMPLES = _job_groups[COLLECTOR_DONE_KEYS]

def category_warning(text):
    """True for the per-import sentence that replaced the static committee-type note.

    S7 builds warning 2 for the import in front of the reader - which column
    the categories came from, and how many committees the FEC committee
    master had to classify - so it cannot be a fixed string in a set.
    """
    return (text.startswith('Committee categories ')
            or text.startswith('The FEC committee master is not available')
            or text.startswith('The FEC committee master file '))


CATALOG_WARNINGS = {
    # S3b may add one more: a file decoded as Windows-1252, or an FEC bulk
    # header taken from somewhere other than the file itself. Neither can come
    # from the CSV fixtures in this suite, and both are asserted in
    # test_evidence_readers.py against the files that do produce them.
    'Imported observations are not verified coverage or donor-attributed PAC spending. Duplicate and amended '
    'rows are preserved; totals may overlap.',
    'Cycle aggregates have no inferred transaction date. Missing or ambiguous amounts, dates, or types remain '
    'unsupported.',
}

ERROR_LEAK_PATTERN = re.compile(r'invalid literal|Traceback|line \d+, in |NoneType|Errno|Decimal\(')

COLLECTOR_FAILURE_PATTERNS = (
    re.compile(r'^Collection failed: .+\. Full log: .+$', re.DOTALL),
    re.compile(r'^Could not start the evidence collector: .+$', re.DOTALL),
)


def assert_limits(value):
    assert_shape(value, LIMITS_EXAMPLES)


def assert_committee(value):
    # A category outside the shared vocabulary is a filter option the Graph
    # tab does not have, so the two tabs would hide different committees.
    assert_shape(value, COMMITTEE_EXAMPLES, enums={'category': set(csv_data.CATEGORIES) | {''}})


# The S3b vocabularies for the descriptive fields, checked here as well as in
# test_evidence_readers.py: the shared contract file carries a negative control
# for each of them, and a control that passes proves nothing.
CATALOG_ENUMS = {'session_only': {False}, 'state': {'ready', 'reading'},
                 'format': {'delimited', 'xlsx', 'json', 'fec_bulk'},
                 'source_kind': {'upload', 'path'},
                 'encoding': {'utf-8', 'utf-8-sig', 'utf-16', 'cp1252', ''},
                 'delimiter': {',', ';', '\t', '|', ''},
                 'header_source': {'row', 'header_path', 'inputs', 'bundled', ''}}


def assert_catalog(value):
    assert_shape(value, CATALOG_EXAMPLES, enums=CATALOG_ENUMS)
    for committee in value['committees']:
        assert_committee(committee)
    names = [item['category'] for item in value['categories']]
    if names != [name for name in csv_data.CATEGORIES if name in set(names)]:
        raise AssertionError(f'catalog.categories out of vocabulary order: {names}')
    for warning in value['warnings']:
        if warning not in CATALOG_WARNINGS and not category_warning(warning):
            raise AssertionError(f'unexpected catalog warning: {warning!r}')


def assert_listed_committee(value):
    assert_shape(value, LISTED_COMMITTEE_EXAMPLES, enums={'category': set(csv_data.CATEGORIES) | {''}})


def without_listed_figures(source):
    """The listed import as its CATALOG: every committee without the rail's figure."""
    return {**source, 'committees': [{key: item for key, item in committee.items() if key not in LISTED_FIGURE_KEYS}
                                     for committee in source['committees']]}


def assert_sources_listing(value):
    assert_shape(value, SOURCES_LISTING_EXAMPLES, enums={'session_only': {False}})
    assert_limits(value['limits'])
    for source in value['sources']:
        if source.get('state') == 'reading':
            # v5: an import being re-read lists its committees exactly as its CATALOG does.
            assert_catalog(source)
            continue
        for committee in source['committees']:
            assert_listed_committee(committee)
        assert_catalog(without_listed_figures(source))


def assert_total(value):
    assert_shape(value, TOTAL_EXAMPLES)


def assert_select(value):
    assert_shape(value, SELECT_EXAMPLES)
    assert_catalog(value['source'])
    assert_committee(value['committee'])
    for committee in value['committees']:
        assert_committee(committee)
    for total in value['totals']:
        assert_total(total)
    for row in value['rows']:
        if row['status'] not in ('supported', 'unsupported', 'excluded'):
            raise AssertionError(f"row {row.get('row_number')}: unknown status {row['status']!r}")
        # An excluded row is an amount deliberately left out of the totals;
        # the page has to be able to say whose decision that was.
        if row['status'] == 'excluded' and not any(
                str(issue).startswith('Excluded by the workbook') for issue in row['issues']):
            raise AssertionError(f"row {row.get('row_number')}: status excluded with no "
                                  f"'Excluded by the workbook' issue: {row['issues']!r}")


def assert_error_payload(value, expected=None):
    assert_shape(value, ERROR_EXAMPLES)
    message = value['error']
    if ERROR_LEAK_PATTERN.search(message):
        raise AssertionError(f'error payload leaks an implementation detail: {message!r}')
    if expected is not None and message != expected:
        raise AssertionError(f'error payload {message!r} != expected {expected!r}')


def assert_job(value):
    assert_shape(value, JOB_EXAMPLES, enums={'state': {'reading', 'ready', 'failed'}})
    ready = value['state'] == 'ready'
    has_catalog = value['catalog'] is not None
    if ready != has_catalog:
        raise AssertionError("JOB.catalog must be non-null iff state == 'ready' "
                              f"(state={value['state']!r}, catalog is {'not ' if has_catalog else ''}None)")
    if has_catalog:
        assert_catalog(value['catalog'])
    if value['state'] != 'failed' and value['message'] != '':
        raise AssertionError(f"JOB.message must be '' unless failed, got {value['message']!r}")


def assert_collector_job(value):
    group = COLLECTOR_DONE_EXAMPLES if 'finished_at' in value else COLLECTOR_ACTIVE_EXAMPLES
    assert_shape(value, group, enums={'state': {'collecting', 'completed', 'failed'}})
    if value['state'] == 'failed':
        if not any(pattern.match(value['message']) for pattern in COLLECTOR_FAILURE_PATTERNS):
            raise AssertionError('failed collector JOB message matches neither documented failure template '
                                  f'(missing "Full log: " and not a start-failure message): {value["message"]!r}')


def assert_summary(value):
    """SUMMARY: shape, the ten-counterparty ceiling, its order, and named rows."""
    assert_shape(value, SUMMARY_EXAMPLES, path='$summary')
    assert_shape(value['source'], [example['source'] for example in SUMMARY_EXAMPLES], path='$summary.source')
    assert_shape(value['committee'], [example['committee'] for example in SUMMARY_EXAMPLES],
                 enums={'category': set(csv_data.CATEGORIES) | {''}}, path='$summary.committee')
    assert_shape(value['counts'], [example['counts'] for example in SUMMARY_EXAMPLES], path='$summary.counts')
    totals = [total for example in SUMMARY_EXAMPLES for total in example['totals']]
    for index, total in enumerate(value['totals']):
        assert_shape(total, totals, path=f'$summary.totals[{index}]')
    for cycle, entries in (value['totals_by_cycle'] or {}).items():
        if not (isinstance(cycle, str) and cycle.isdigit()):
            raise AssertionError(f'$summary.totals_by_cycle: {cycle!r} is not a cycle string')
        for index, total in enumerate(entries):
            assert_shape(total, totals, path=f'$summary.totals_by_cycle[{cycle}][{index}]')
    parties = [party for example in SUMMARY_EXAMPLES for party in example['counterparties']]
    if len(value['counterparties']) > 10:
        raise AssertionError('$summary.counterparties: more than the documented top ten')
    previous = None
    for index, party in enumerate(value['counterparties']):
        assert_shape(party, parties, path=f'$summary.counterparties[{index}]')
        if not party['name']:
            raise AssertionError(f'$summary.counterparties[{index}].name is blank')
        if previous is not None and party['amount'] > previous:
            raise AssertionError('$summary.counterparties: not ordered by amount')
        previous = party['amount']
    if value['selected_cycle'] not in ([''] + list(value['cycles'])):
        raise AssertionError('$summary.selected_cycle is not blank and not one of cycles')


IMPORT_DISPATCH = {'catalog': assert_catalog, 'sources_listing': assert_sources_listing,
                   'select': assert_select, 'error': assert_error_payload,
                   'summary': assert_summary}
JOB_DISPATCH = {'job': assert_job, 'collector_job': assert_collector_job}


class EvidenceCSVStreamTests(unittest.TestCase):
    # -- streaming ingest correctness -------------------------------------------------
    def test_truncated_inside_quoted_field(self):
        good_rows = build_fixture_text(3)
        truncated = good_rows + 'C00000009,Example PAC,10.00,2026-01-09,2026,"Doe, Jane'
        with temp_data_root() as root:
            store = csv_data.Store(root)
            job = store.wait(store.ingest_text('quote-cut.csv', truncated)['id'])
            self.assertEqual(job['state'], 'failed')
            self.assertRegex(job['message'],
                              r'^The CSV could not be read at row [\d,]+\. Check its quoted fields and delimiter\.$')

    def test_truncated_mid_row_is_tolerated(self):
        total_rows = 40
        text = build_fixture_text(total_rows)
        truncated = text[:-5]  # cut inside the final (unquoted) row; no exception expected
        with temp_data_root() as root:
            store = csv_data.Store(root)
            job = store.wait(store.ingest_text('mid-row.csv', truncated)['id'])
            self.assertEqual(job['state'], 'ready')
            self.assertLessEqual(abs(job['catalog']['row_count'] - total_rows), 1)

    def test_201_columns_rejected(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            job = store.wait(store.ingest_text('wide201.csv', wide_csv(201))['id'])
            self.assertEqual(job['state'], 'failed')
            self.assertEqual(job['message'], M_COLUMNS)

    def test_200_columns_accepted(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            job = store.wait(store.ingest_text('wide200.csv', wide_csv(200))['id'])
            self.assertEqual(job['state'], 'ready')

    def test_oversize_rejected_while_streaming(self):
        # MAX_BYTES defaults to 500_000_000; writing that many bytes per test
        # run would be slow, so this narrows the cap for its own duration (the
        # module reads MAX_BYTES at call time, so Upload.write and
        # bytes_message() both follow the narrowed value) and restores it.
        original_max_bytes = csv_data.MAX_BYTES
        csv_data.MAX_BYTES = 2_000_000
        try:
            with temp_data_root() as root:
                store = csv_data.Store(root)
                upload = store.begin_upload('big.csv')
                chunk = b'a' * csv_data.CHUNK_BYTES
                total_chunks = (csv_data.MAX_BYTES // csv_data.CHUNK_BYTES) + 3
                accepted = 0
                with self.assertRaises(ValueError) as ctx:
                    for _ in range(total_chunks):
                        accepted += len(chunk)
                        upload.write(chunk)
                self.assertEqual(str(ctx.exception), csv_data.bytes_message())
                self.assertEqual(str(ctx.exception), 'Choose a CSV no larger than 2 MB.')
                self.assertLessEqual(accepted, csv_data.MAX_BYTES + csv_data.CHUNK_BYTES)
        finally:
            csv_data.MAX_BYTES = original_max_bytes

    def test_peak_memory_bounded(self):
        row_count = 40000
        text = build_fixture_text(row_count)
        data = text.encode('utf-8')
        with temp_data_root() as root:
            tracemalloc.start()
            try:
                store = csv_data.Store(root)
                upload = store.begin_upload('peak.csv')
                for offset in range(0, len(data), csv_data.CHUNK_BYTES):
                    upload.write(data[offset:offset + csv_data.CHUNK_BYTES])
                job = upload.finish()
                job = store.wait(job['id'])
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertEqual(job['state'], 'ready')
        self.assertLess(peak, 16 * 1024 * 1024,
                         f'peak traced memory {peak / 1e6:.1f} MB for a {len(data) / 1e6:.1f} MB file '
                         '(S0 baseline: 58.5 MB peak for a 4.95 MB file)')

    def test_max_rows_enforced_incrementally(self):
        original_max_rows = csv_data.MAX_ROWS
        csv_data.MAX_ROWS = 100
        try:
            text = build_fixture_text(500)
            with temp_data_root() as root:
                store = csv_data.Store(root)
                job = store.wait(store.ingest_text('too-many-rows.csv', text)['id'])
                self.assertEqual(job['state'], 'failed')
                self.assertEqual(job['message'], 'Choose a CSV containing no more than 100 rows.')
                self.assertLessEqual(job['rows_read'], 101)
        finally:
            csv_data.MAX_ROWS = original_max_rows

    def test_dedup_same_bytes_same_id(self):
        text = build_fixture_text(10)
        expected_id = 'csv-' + hashlib.sha256(text.encode('utf-8')).hexdigest()[:32]
        with temp_data_root() as root:
            store = csv_data.Store(root)
            first = store.wait(store.ingest_text('a.csv', text)['id'])
            second = store.ingest_text('a.csv', text)
            self.assertEqual(first['id'], expected_id)
            self.assertEqual(second['id'], expected_id)
            self.assertEqual(second['state'], 'ready')

    def test_eviction(self):
        original_keep = csv_data.KEEP_IMPORTS
        csv_data.KEEP_IMPORTS = 2
        try:
            with temp_data_root() as root:
                store = csv_data.Store(root)
                ids = []
                for index in range(3):
                    text = build_fixture_text(5 + index)  # distinct byte content per import
                    job = store.wait(store.ingest_text(f'import-{index}.csv', text)['id'])
                    ids.append(job['id'])
                    time.sleep(0.01)  # keep imported_at strictly increasing
                sources = store.sources()
                self.assertEqual(len(sources), 2)
                remaining = {source['id'] for source in sources}
                self.assertNotIn(ids[0], remaining)
                self.assertIn(ids[2], remaining)
                self.assertFalse((root / 'evidence-csv' / ids[0]).exists())
        finally:
            csv_data.KEEP_IMPORTS = original_keep

    def test_select_totals_are_decimal_exact(self):
        total_rows = 120
        text = build_fixture_text(total_rows)
        with temp_data_root() as root:
            store = csv_data.Store(root)
            job = store.wait(store.ingest_text('totals.csv', text)['id'])
            identifier = job['id']

            expected_all, expected_count = expected_totals(total_rows, committee=PRIMARY_COMMITTEE)
            selection = store.select(identifier, committee=PRIMARY_COMMITTEE, limit=csv_data.PAGE_LIMIT)
            self.assertEqual(selection['totals'], expected_all)
            self.assertEqual(selection['total_rows'], expected_count)
            self.assertFalse(selection['has_more'])

            expected_2026, expected_2026_count = expected_totals(total_rows, committee=PRIMARY_COMMITTEE, cycle='2026')
            selection_2026 = store.select(identifier, committee=PRIMARY_COMMITTEE, cycle='2026', limit=csv_data.PAGE_LIMIT)
            self.assertEqual(selection_2026['totals'], expected_2026)
            self.assertEqual(selection_2026['total_rows'], expected_2026_count)

            expected_unassigned, expected_unassigned_count = expected_totals(total_rows, committee='unassigned')
            selection_unassigned = store.select(identifier, committee='unassigned', limit=csv_data.PAGE_LIMIT)
            self.assertEqual(selection_unassigned['totals'], expected_unassigned)
            self.assertEqual(selection_unassigned['total_rows'], expected_unassigned_count)

    def test_page_arguments_validated(self):
        for offset, limit in (('abc', '200'), ('0', True), ('-1', '10'), ('0', '5000')):
            with self.subTest(offset=offset, limit=limit):
                with self.assertRaises(ValueError) as ctx:
                    csv_data.page_args(offset, limit)
                self.assertEqual(str(ctx.exception), M_PAGE)

    def test_gone_import(self):
        text = build_fixture_text(5)
        with temp_data_root() as root:
            store = csv_data.Store(root)
            job = store.wait(store.ingest_text('gone.csv', text)['id'])
            identifier = job['id']
            rmtree_retry(root / 'evidence-csv' / identifier)
            with self.assertRaises(ValueError) as ctx:
                store.select(identifier)
            self.assertEqual(str(ctx.exception), m_gone(identifier))
            self.assertNotIn(identifier, {source['id'] for source in store.sources()})

    def test_interrupted_reading_reports_failed(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            identifier = 'csv-' + ('0' * 32)
            fake_dir = root / 'evidence-csv' / identifier
            fake_dir.mkdir(parents=True)
            stale = {'id': identifier, 'name': 'stale.csv', 'state': 'reading', 'bytes': 100,
                     'bytes_read': 10, 'rows_read': 1, 'message': '', 'started_at': time.time(),
                     'finished_at': None, 'catalog': None, 'pid': 999999}
            (fake_dir / 'import.json').write_text(json.dumps(stale), encoding='utf-8')
            job = store.job(identifier)
            self.assertEqual(job['state'], 'failed')
            self.assertEqual(job['message'], M_INTERRUPTED)

    # -- contract ----------------------------------------------------------------------
    def test_contract_examples_against_producers(self):
        with temp_data_root() as root:
            store = csv_data.Store(root)
            assert_limits(csv_data.limits())

            text = build_fixture_text(40)
            job = store.wait(store.ingest_text('producers.csv', text)['id'])
            assert_job(job)
            self.assertEqual(job['state'], 'ready')

            catalog = store.catalog(job['id'])
            assert_catalog(catalog)

            listing = {'sources': store.sources(), 'limits': csv_data.limits(), 'session_only': False}
            assert_sources_listing(listing)

            selection = store.select(job['id'], committee=PRIMARY_COMMITTEE)
            assert_select(selection)

            assert_summary(store.summary(job['id'], committee=PRIMARY_COMMITTEE))
            assert_summary(store.summary(job['id'], committee=PRIMARY_COMMITTEE, cycle='2026'))

            # A fake collection.json, written the way Console.collect_evidence writes it.
            collecting = {'id': 'snapshot-' + uuid.uuid4().hex, 'company': 'Example Co', 'aliases': [],
                          'committees': [PRIMARY_COMMITTEE], 'cycles': ['2026'], 'state': 'collecting',
                          'pid': os.getpid(), 'started_at': time.time()}
            assert_collector_job(collecting)
            completed = {**collecting, 'state': 'completed', 'finished_at': time.time(), 'message': 'Snapshot ready.'}
            assert_collector_job(completed)
            failed_log = {**collecting, 'state': 'failed', 'finished_at': time.time(),
                          'message': 'Collection failed: FEC evidence request failed: HTTP 500; '
                                     'URL https://api.open.fec.gov/v1/x. Full log: '
                                     f'{root}/pac-evidence/{collecting["id"]}/collection.log'}
            assert_collector_job(failed_log)
            failed_start = {**collecting, 'state': 'failed', 'finished_at': time.time(),
                             'message': 'Could not start the evidence collector: [WinError 2] file not found'}
            assert_collector_job(failed_start)

        for control in IMPORT_CONTRACT['negative_controls']:
            with self.subTest(contract='evidence-csv-import', why=control['why']):
                with self.assertRaises(AssertionError):
                    IMPORT_DISPATCH[control['shape']](control['payload'])
        for control in JOB_CONTRACT['negative_controls']:
            with self.subTest(contract='evidence-job-status', why=control['why']):
                with self.assertRaises(AssertionError):
                    JOB_DISPATCH[control['shape']](control['payload'])

    # -- HTTP surface ------------------------------------------------------------------
    def test_http_streaming_roundtrip(self):
        with temp_data_root() as root:
            console, server, worker = start_console_server(root, read_only=True)
            origin = f'http://127.0.0.1:{server.server_port}'
            try:
                text = build_fixture_text(250)
                payload = text.encode('utf-8')
                headers = {'Content-Type': 'text/csv', 'X-Mentat-Token': console.token,
                           'X-Evidence-Name': urllib.parse.quote('roundtrip.csv'),
                           'Content-Length': str(len(payload))}
                with urlopen(Request(origin + '/api/evidence-csv', data=payload, headers=headers, method='POST')) as response:
                    self.assertEqual(response.status, 200)
                    job = json.load(response)
                self.assertIn(job['state'], ('reading', 'ready'))

                deadline = time.time() + 15
                while job['state'] == 'reading' and time.time() < deadline:
                    time.sleep(0.05)
                    with urlopen(origin + '/api/evidence-csv?job=' + job['id']) as response:
                        job = json.load(response)
                self.assertEqual(job['state'], 'ready')

                with urlopen(origin + '/api/evidence-csv?id=' + job['id'] + '&committee=' + PRIMARY_COMMITTEE
                             + '&offset=0&limit=200') as response:
                    selection = json.load(response)
                self.assertEqual(len(selection['rows']), 200)
                self.assertEqual(selection['limit'], 200)

                oversize_headers = {'X-Mentat-Token': console.token, 'X-Evidence-Name': 'oversize.csv',
                                    'Content-Length': str(csv_data.MAX_BYTES + 1), 'Content-Type': 'text/csv'}
                request = Request(origin + '/api/evidence-csv', data=b'', headers=oversize_headers, method='POST')
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(request)
                self.assertEqual(ctx.exception.code, 400)
                self.assertEqual(json.load(ctx.exception)['error'], csv_data.bytes_message())

                no_token = Request(origin + '/api/evidence-csv', data=b'a,b\n',
                                    headers={'Content-Type': 'text/csv', 'Content-Length': '4'}, method='POST')
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(no_token)
                self.assertEqual(ctx.exception.code, 403)

                with self.assertRaises(HTTPError) as ctx:
                    urlopen(origin + '/api/evidence-csv?id=' + job['id'] + '&offset=abc')
                self.assertEqual(ctx.exception.code, 400)
                self.assertEqual(json.load(ctx.exception)['error'], M_PAGE)

                with urlopen(origin + '/api/evidence-csv') as response:
                    body = json.load(response)
                self.assertIn('limits', body)
                self.assertIn('sources', body)
            finally:
                stop_console_server(server, worker)

    def test_legacy_json_import_still_works(self):
        with temp_data_root() as root:
            console, server, worker = start_console_server(root, read_only=True)
            origin = f'http://127.0.0.1:{server.server_port}'
            try:
                text = build_fixture_text(9)
                payload = json.dumps({'name': 'legacy.csv', 'text': text}).encode()
                headers = {'Content-Type': 'application/json', 'X-Mentat-Token': console.token}
                with urlopen(Request(origin + '/api/evidence-csv', data=payload, headers=headers)) as response:
                    self.assertEqual(response.status, 200)
                    catalog = json.load(response)
                self.assertIn('row_count', catalog)
                with urlopen(origin + '/api/evidence-csv?id=' + catalog['id'] + '&committee=' + PRIMARY_COMMITTEE) as response:
                    selection = json.load(response)
                self.assertGreater(len(selection['rows']), 0)
            finally:
                stop_console_server(server, worker)

    def test_keystore_failure_reported_once(self):
        with temp_data_root() as root:
            console, server, worker = start_console_server(root, read_only=False)
            origin = f'http://127.0.0.1:{server.server_port}'
            # The crypt() failure itself (what DPAPI actually raised)...
            keystore_crypt_error = 'Windows could not access the encrypted FEC key store for this user.'
            # ...is not the same string the HTTP layer surfaces for it: do_POST's
            # generic `except RuntimeError` handler (pre-existing, not part of
            # this lane) already rewords it for any non-analytical path,
            # including /api/evidence-collect. Assert THAT sentence, not a
            # verbatim echo of crypt()'s own message.
            keystore_post_error = 'The encrypted key store could not be accessed for this Windows user.'
            try:
                with patch.object(fec_key_store, 'crypt', side_effect=RuntimeError(keystore_crypt_error)):
                    with urlopen(origin + '/api/runs') as response:
                        self.assertEqual(response.status, 200)
                        first = json.load(response)
                    with urlopen(origin + '/api/runs') as response:
                        self.assertEqual(response.status, 200)
                        second = json.load(response)
                    self.assertFalse(first['key_status_available'])
                    self.assertFalse(second['key_status_available'])
                    self.assertEqual(first['key_status_message'], second['key_status_message'])

                    payload = json.dumps({'company': 'Example Co', 'committees': PRIMARY_COMMITTEE,
                                          'cycles': '2026'}).encode()
                    headers = {'Content-Type': 'application/json', 'X-Mentat-Token': console.token,
                               'Content-Length': str(len(payload))}
                    with self.assertRaises(HTTPError) as ctx:
                        urlopen(Request(origin + '/api/evidence-collect', data=payload, headers=headers))
                    self.assertEqual(ctx.exception.code, 400)
                    body = json.load(ctx.exception)
                    self.assertEqual(body['error'], keystore_post_error)
                    self.assertNotIn('Traceback', body['error'])
            finally:
                stop_console_server(server, worker)

    def test_collect_evidence_failure_message(self):
        with temp_data_root() as root:
            console, _server, _worker = start_console_server(root, read_only=False)
            # collect_evidence writes under PROJECT/.cache/pac-evidence; patch
            # PROJECT itself so this test never touches the real working tree.
            with patch.object(app, 'PROJECT', root):

                class FakeFailingProcess:
                    def __init__(self, log_handle):
                        self._log_handle = log_handle
                        self.pid = 4242

                    def wait(self):
                        self._log_handle.write('Collecting page 1 of 4...\n')
                        self._log_handle.write('FEC evidence request failed: HTTP 500; '
                                                'URL https://api.open.fec.gov/v1/x\n')
                        self._log_handle.flush()
                        return 1

                def fake_popen_failing(args, stdout=None, stderr=None, creationflags=0):
                    return FakeFailingProcess(stdout)

                def wait_for_terminal(identifier, timeout=10):
                    deadline = time.time() + timeout
                    record_path = root / '.cache' / 'pac-evidence' / identifier / 'collection.json'
                    record = {}
                    while time.time() < deadline:
                        if record_path.exists():
                            record = json.loads(record_path.read_text(encoding='utf-8'))
                            if record.get('state') != 'collecting':
                                return record
                        time.sleep(0.05)
                    return record

                with patch.object(app, 'load_keys', return_value=['k']), \
                        patch.object(app.subprocess, 'Popen', side_effect=fake_popen_failing):
                    job = console.collect_evidence({'company': 'Example Co', 'committees': PRIMARY_COMMITTEE,
                                                     'cycles': '2026'})
                    record = wait_for_terminal(job['id'])
                self.assertEqual(record.get('state'), 'failed')
                self.assertTrue(record['message'].startswith('Collection failed: FEC evidence request failed: HTTP 500'))
                self.assertIn('Full log: ', record['message'])

                with patch.object(app, 'load_keys', return_value=['k']), \
                        patch.object(app.subprocess, 'Popen', side_effect=OSError('boom')):
                    job = console.collect_evidence({'company': 'Example Co', 'committees': PRIMARY_COMMITTEE,
                                                     'cycles': '2026'})
                    record = wait_for_terminal(job['id'])
                self.assertEqual(record.get('state'), 'failed')
                self.assertEqual(record['message'], 'Could not start the evidence collector: boom')


if __name__ == '__main__':
    unittest.main()
