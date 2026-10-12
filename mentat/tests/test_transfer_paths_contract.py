"""F14 (Paul approved option (a), 2026-09-26): the Transfer Paths shape, pinned in one contract.

tests/fixtures/transfer_paths_contract.json names the edge-per-cycle columns of the payload,
the Overview sheet, the tidy staging table and Data sheet, the transfer_path_detail table
(the root x committee x edge expansion, only in the staging store), and one small network
whose rows every producer must reproduce. Each group ends with a negative control: the old
per-root shape, or a producer that drifted, fails the same check.

Run from inside mentat/tests: python -m unittest test_transfer_paths_contract
"""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import gc
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
sys.path.insert(0, str(HERE.parent / 'app'))
import build_tidy_workbook  # noqa: E402
import enrich_downstream  # noqa: E402
from fec_transfer_graph import reachable  # noqa: E402
import graph_data  # noqa: E402
from report_insights import digest, stamp  # noqa: E402

CONTRACT = json.loads((HERE / 'fixtures' / 'transfer_paths_contract.json').read_text(encoding='utf-8'))
BUILDER = HERE.parent / 'tools' / 'crypto_workbook_builder.mjs'


def payload_violations(rows):
    """What keeps payload rows from the contract's shape: field order, and one row per edge."""
    problems = [f'row {index}: fields {list(row)}' for index, row in enumerate(rows)
                if list(row) != CONTRACT['payload_fields']]
    keys = [tuple(row.get(field) for field in CONTRACT['grain']) for row in rows]
    if len(keys) != len(set(keys)):
        problems.append(f'{len(keys) - len(set(keys))} repeated transfer(s)')
    return problems


def detail_violations(path):
    """What keeps a staging store's detail table from the contract."""
    detail = CONTRACT['detail']
    with closing(sqlite3.connect(str(path))) as database:
        columns = [[row[1], row[2]] for row in database.execute(f'PRAGMA table_info("{detail["table"]}")')]
        indexes = {row[1]: [column[2] for column in database.execute(f'PRAGMA index_info("{row[1]}")')]
                   for row in database.execute(f'PRAGMA index_list("{detail["table"]}")')}
    problems = [] if columns == detail['columns'] else [f'columns {columns}']
    problems += [f'index {name}' for name, fields in detail['indexes'] if indexes.get(name) != fields]
    return problems


def builder_fields(text):
    """The Overview builder's Transfer Paths [key, header] pairs, read from its source."""
    match = re.search(r'const transferPathFields = (\[\[.*?\]\]);', text)
    return json.loads(match[1]) if match else None


def staging_store(directory):
    path = Path(directory) / 'tidy.sqlite'
    with closing(sqlite3.connect(str(path))) as database:
        database.execute('CREATE TABLE contributions (source_id TEXT)')
        database.commit()
    return path


def run(payload, directory, staging=None):
    detail = enrich_downstream.TransferPathDetail(staging) if staging else None
    result = enrich_downstream.enrich(deepcopy(payload), Path(directory), detail)
    if detail is not None:
        rows = detail.close()
        enrich_downstream.stage(result, staging)
        return result, rows
    return result, None


class PayloadRows(unittest.TestCase):
    """The payload's transfer_paths: one row per transfer edge and cycle, as the contract says."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(gc.collect)
        self.result, _ = run(CONTRACT['case']['payload'], self.tmp.name)

    def test_rows_have_the_contract_fields_and_one_row_per_edge(self):
        self.assertEqual(payload_violations(self.result['transfer_paths']), [])

    def test_rows_match_the_case(self):
        found = {(row['source'], row['destination']): row for row in self.result['transfer_paths']}
        self.assertEqual(len(found), len(CONTRACT['case']['expected_rows']))
        for expected in CONTRACT['case']['expected_rows']:
            row = found[expected['source'], expected['destination']]
            for field, value in expected.items():
                self.assertEqual(row[field], value, (expected['source'], expected['destination'], field))
            self.assertEqual(row['date_rule'], 'earliest_arrival')
            self.assertEqual(row['carryover_status'], 'known')

    def test_every_edge_counts_the_roots_the_old_expansion_repeated_it_for(self):
        # Without carryover routes, the counts are exactly the old per-root rows collapsed.
        payload = deepcopy(CONTRACT['case']['payload'])
        edges = [edge for edge in payload['transfer_edges'] if edge['source_gate_source'] != 'carryover']
        old = reachable({'C90000021', 'C90000022'}, edges, max_depth=None, stop_nodes={'C90000025'})
        counts = {}
        for row in old:
            counts[row['source'], row['destination']] = counts.get((row['source'], row['destination']), 0) + 1
        found = {(row['source'], row['destination']): row['roots_reaching'] for row in self.result['transfer_paths']
                 if row['source_gate_source'] != 'carryover'}
        self.assertEqual(found, counts)

    def test_negative_control_the_old_per_root_rows_fail_the_contract(self):
        payload = deepcopy(CONTRACT['case']['payload'])
        old = reachable({'C90000021', 'C90000022'}, payload['transfer_edges'], max_depth=None, stop_nodes={'C90000025'})
        problems = payload_violations(old)
        self.assertTrue(any('repeated transfer' in problem for problem in problems), problems)
        self.assertTrue(any('fields' in problem for problem in problems), problems)


class StagingStore(unittest.TestCase):
    """The staging store: the edge table as the contract says, and the full expansion beside it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(gc.collect)
        self.store = staging_store(self.tmp.name)
        self.result, self.rows = run(CONTRACT['case']['payload'], self.tmp.name, self.store)

    def test_transfer_paths_table_has_the_contract_columns(self):
        with closing(sqlite3.connect(str(self.store))) as database:
            columns = [[row[1], row[2]] for row in database.execute('PRAGMA table_info("transfer_paths")')]
            count = database.execute('SELECT COUNT(*) FROM transfer_paths').fetchone()[0]
        self.assertEqual(columns, CONTRACT['staging_columns'])
        self.assertEqual(count, len(CONTRACT['case']['expected_rows']))

    def test_detail_table_holds_every_root_route(self):
        self.assertEqual(detail_violations(self.store), [])
        self.assertEqual(self.rows, CONTRACT['case']['expected_detail_rows'])
        with closing(sqlite3.connect(str(self.store))) as database:
            paths = {}
            for root, path in database.execute('SELECT funded_committee, representative_path FROM transfer_path_detail'):
                paths.setdefault(root, []).append(path)
        self.assertEqual({root: sorted(found) for root, found in paths.items()},
                         {root: sorted(found) for root, found in CONTRACT['case']['expected_detail_paths'].items()})

    def test_negative_control_a_store_without_the_detail_table_fails(self):
        folder = Path(self.tmp.name) / 'bare'
        folder.mkdir()
        self.assertTrue(detail_violations(staging_store(folder)))


class Workbooks(unittest.TestCase):
    """Both workbooks' Transfer Paths sheets read the contract's columns."""

    def test_overview_builder_writes_the_contract_columns(self):
        fields = builder_fields(BUILDER.read_text(encoding='utf-8'))
        self.assertEqual([key for key, _ in fields], CONTRACT['payload_fields'])
        self.assertEqual([header for _, header in fields], CONTRACT['overview_headers'])

    def test_data_workbook_registry_matches_the_staging_table(self):
        registry = build_tidy_workbook.TRANSFER_PATHS
        self.assertEqual([column for column, _, _ in registry], [column for column, _ in CONTRACT['staging_columns']])
        self.assertEqual([header for _, header, _ in registry], CONTRACT['data_headers'])
        sheet = next(sheet for sheet in build_tidy_workbook.SHEETS if sheet.table == 'transfer_paths')
        self.assertEqual(list(sheet.key), CONTRACT['data_key'])
        self.assertFalse(any(sheet.table == CONTRACT['detail']['table'] for sheet in build_tidy_workbook.SHEETS))

    def test_negative_control_the_old_builder_columns_fail(self):
        old = ('const transferPathFields = [["cycle","Cycle"],["root","Funded Committee"],["source","Source Committee"],'
               '["destination","Destination Committee"],["amount","Reported Net Transfer"],["depth","Depth"],'
               '["path","Representative Path"],["status","Traversal Status"]];')
        self.assertNotEqual([key for key, _ in builder_fields(old)], CONTRACT['payload_fields'])


class Console(unittest.TestCase):
    """Graph reads the new sheet through its normal reader, and queries the detail by root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.tmp.name)
        result, _ = run(CONTRACT['case']['payload'], self.tmp.name)
        self.rows = result['transfer_paths']

    def workbook(self, headers):
        import openpyxl
        # Not an '... Industry Spending Overview.xlsx' name: this reads the sheet alone.
        path = self.root / 'Transfer Paths Fixture.xlsx'
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'Transfer Paths'
        for row in [['Transfer Paths'], [], [f'{len(self.rows)} records; {len(self.rows)} shown. Complete destination data.'], [],
                    headers]:
            sheet.append(row)
        fields = dict(zip(CONTRACT['overview_headers'], CONTRACT['payload_fields']))
        for row in self.rows:
            sheet.append([row[fields[header]] if header in fields else None for header in headers])
        book.save(path)
        book.close()
        graph_data.report_transfers.cache_clear()
        return path

    def test_graph_reads_each_transfer_once_and_never_expands_a_candidate_gift(self):
        path = self.workbook(CONTRACT['overview_headers'])
        found = graph_data.report_transfers(stamp(path))
        expected = sorted((str(row['cycle']), row['source'], row['destination'], Decimal(str(row['amount'])))
                          for row in self.rows if row['status'] != 'candidate_committee')
        self.assertEqual(sorted((edge['cycle'], edge['source'], edge['destination'], edge['amount']) for edge in found),
                         expected)

    def test_negative_control_a_sheet_without_the_console_columns_is_refused(self):
        headers = [header for header in CONTRACT['overview_headers'] if header != 'Traversal Status']
        path = self.workbook(headers)
        with self.assertRaisesRegex(ValueError, 'required report columns'):
            graph_data.report_transfers(stamp(path))

    def test_detail_is_queried_from_the_pinned_store_and_refused_when_it_drifts(self):
        store = staging_store(self.root)
        run(CONTRACT['case']['payload'], self.root, store)
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'stage_sha256': digest(store)}), encoding='utf-8')
        workbook = self.workbook(CONTRACT['overview_headers'])
        with patch.object(graph_data, 'transfer_detail_source', return_value=(store, manifest)):
            rows = graph_data.transfer_path_detail(workbook, 2024, root='C90000022')
            self.assertEqual(sorted(row['representative_path'] for row in rows),
                             sorted(CONTRACT['case']['expected_detail_paths']['C90000022']))
            by_edge = graph_data.transfer_path_detail(workbook, 2024, source='C90000023', destination='C90000024')
            self.assertEqual(sorted(row['funded_committee'] for row in by_edge), ['C90000021', 'C90000022'])
            # Negative control: a store that no longer matches its build's fingerprint is refused.
            manifest.write_text(json.dumps({'stage_sha256': '0' * 64}), encoding='utf-8')
            graph_data._pinned_detail_store.cache_clear()
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                graph_data.transfer_path_detail(workbook, 2024, root='C90000022')


if __name__ == '__main__':
    unittest.main()
