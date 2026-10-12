"""Earmark conduits (ActBlue, WinRed) are a 'Conduit', never a super PAC.

A published Telecom workbook built before 2026-09-08 labels both committees
'Super PAC' (R/report.R's registered_conduit rule, then); the console reads that
label from the workbook (Graph), from the contribution facts stores built from
its payload (Chart Studio, Insights) and from imports of the workbook (PAC
Evidence). These tests pin the one registry, the vocabulary, and every console
read that has to say 'Conduit' without a store being rebuilt.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'app'))
import contribution_data as data
import contribution_query as query
import contribution_query_store as store
import contribution_semantics as semantics
import evidence_csv as csv_data
import fec_citation
import graph_data
import records_sidecar as sidecar
from test_evidence_categories import MASTER_ROWS, master_at, master_row, normalized, temp_fixture_dir, write_master

ACTBLUE, WINRED = 'C00401224', 'C00694323'
OLD_VOCABULARY = ('Super PAC', 'Trade Association', 'JFC', 'Company PAC', 'Leadership PAC',
                  'Carey', 'Other PAC', 'Candidate', 'Party Committee', 'Unclassified')


class ConduitVocabulary(unittest.TestCase):
    def test_conduit_is_appended_so_no_existing_category_moves(self):
        self.assertEqual(csv_data.CATEGORIES[:10], OLD_VOCABULARY)
        self.assertEqual(csv_data.CATEGORIES[10:], ('Conduit',))
        self.assertEqual(csv_data.CONDUIT, 'Conduit')
        self.assertEqual(list(csv_data.CATEGORIES), graph_data.CATEGORIES)

    def test_one_registry_and_it_is_the_filing_link_registry(self):
        self.assertEqual(csv_data.EARMARK_CONDUITS, {ACTBLUE: 'ActBlue', WINRED: 'WinRed'})
        self.assertEqual(set(csv_data.EARMARK_CONDUITS), set(fec_citation.CONDUIT_COMMITTEES))

    def test_conduit_is_a_file_category_in_any_case(self):
        self.assertEqual(csv_data.canonical_category('conduit'), 'Conduit')
        self.assertEqual(csv_data.canonical_category('  CONDUIT '), 'Conduit')

    def test_conduit_category_helper(self):
        self.assertEqual(csv_data.conduit_category(ACTBLUE, 'Super PAC'), 'Conduit')
        self.assertEqual(csv_data.conduit_category('FEC:' + WINRED.lower(), 'Carey'), 'Conduit')
        self.assertEqual(csv_data.conduit_category('C00000003', 'Super PAC'), 'Super PAC')
        self.assertEqual(csv_data.conduit_category('', 'Candidate'), 'Candidate')


class ConduitClassification(unittest.TestCase):
    def test_registry_beats_designation_and_type_rules(self):
        rows = [*MASTER_ROWS,
                master_row(ACTBLUE, 'ACTBLUE', 'U', 'V', ''),     # the real master: hybrid, type V
                master_row(WINRED, 'WINRED', 'J', 'O', 'C')]      # designation J and type O would say JFC
        with temp_fixture_dir() as fixtures:
            write_master(fixtures, rows=rows)
            with master_at(fixtures):
                for identifier, name in ((ACTBLUE, 'ActBlue'), (WINRED, 'WinRed')):
                    for has_file in (False, True):
                        with self.subTest(committee=identifier, file=has_file):
                            category, source = csv_data.classify_committee(identifier, has_file)
                            self.assertEqual(category, 'Conduit')
                            self.assertEqual(source, 'earmark_conduit_registry:' + name)
                # An ordinary type V committee is still Carey.
                self.assertEqual(csv_data.classify_committee('C00000005'), ('Carey', 'fec_master:cm24'))

    def test_registry_needs_no_master(self):
        with temp_fixture_dir() as fixtures:
            with master_at(Path(fixtures) / 'absent'):
                self.assertEqual(csv_data.classify_committee(ACTBLUE),
                                 ('Conduit', 'earmark_conduit_registry:ActBlue'))
                self.assertEqual(csv_data.classify_committee(' ' + WINRED.lower() + ' ')[0], 'Conduit')

    def test_a_stale_file_category_does_not_override_the_registry(self):
        for stale in ('Super PAC', 'Carey', 'Unclassified'):
            with self.subTest(file_says=stale):
                self.assertEqual(csv_data.committee_category({stale: 22}, WINRED, 'Recipient Category'),
                                 ('Conduit', 'earmark_conduit_registry:WinRed', False))
        # A committee that is not registered keeps the file's word.
        self.assertEqual(csv_data.committee_category({'Super PAC': 22}, 'C00835959', 'Recipient Category'),
                         ('Super PAC', 'file:Recipient Category', False))

    def test_a_row_of_a_registered_conduit_is_a_conduit_row(self):
        row = normalized({'Committee ID': WINRED, 'Amount': '10', 'Date': '2026-01-05',
                          'Record Type': 'CONTRIBUTION', 'Recipient Category': 'Super PAC'})
        self.assertEqual(row['category'], 'Conduit')
        other = normalized({'Committee ID': 'C00000004', 'Amount': '10', 'Date': '2026-01-05',
                            'Record Type': 'CONTRIBUTION', 'Recipient Category': 'Super PAC'})
        self.assertEqual(other['category'], 'Super PAC')
        blank = normalized({'Committee ID': ACTBLUE, 'Amount': '10', 'Date': '2026-01-05',
                            'Record Type': 'CONTRIBUTION'})
        self.assertEqual(blank['category'], '')   # no category column: the catalog consults the registry

    def test_a_catalog_already_on_disk_reads_back_as_conduit_without_a_re_read(self):
        record = {'catalog': {
            'name': 'Telecom Industry Spending Overview.xlsx',
            'committees': [
                {'id': ACTBLUE, 'name': 'ACTBLUE', 'rows': 3, 'supported_rows': 3, 'excluded_rows': 0,
                 'category': 'Super PAC', 'category_source': 'file:Recipient Category'},
                {'id': 'C00835959', 'name': 'FAIRSHAKE', 'rows': 2, 'supported_rows': 1, 'excluded_rows': 1,
                 'category': 'Super PAC', 'category_source': 'file:Recipient Category'},
                {'id': WINRED, 'name': 'WINRED', 'rows': 4, 'supported_rows': 4, 'excluded_rows': 0,
                 'category': 'Carey', 'category_source': 'fec_master:cm24'}],
            'categories': [{'category': 'Super PAC', 'committees': 2, 'rows': 5, 'supported_rows': 4, 'excluded_rows': 1},
                           {'category': 'Carey', 'committees': 1, 'rows': 4, 'supported_rows': 4, 'excluded_rows': 0}]},
            'catalog_version': csv_data.CATALOG_VERSION}
        before = json.loads(json.dumps(record))
        catalog = csv_data.stored_catalog(record)
        by_id = {item['id']: item for item in catalog['committees']}
        self.assertEqual(by_id[ACTBLUE]['category'], 'Conduit')
        self.assertEqual(by_id[ACTBLUE]['category_source'], 'earmark_conduit_registry:ActBlue')
        self.assertEqual(by_id[WINRED]['category'], 'Conduit')
        self.assertEqual(by_id['C00835959']['category'], 'Super PAC')
        self.assertEqual(by_id['C00835959']['category_source'], 'file:Recipient Category')
        self.assertEqual(catalog['categories'], [
            {'category': 'Super PAC', 'committees': 1, 'rows': 2, 'supported_rows': 1, 'excluded_rows': 1},
            {'category': 'Conduit', 'committees': 2, 'rows': 7, 'supported_rows': 7, 'excluded_rows': 0}])
        self.assertEqual(record, before, 'the stored record is read, never rewritten')

    def test_rows_of_a_response_are_relabelled_in_place(self):
        rows = [{'committee_id': ACTBLUE, 'recipient_category': 'Super PAC'},
                {'committee_id': 'C00000003', 'recipient_category': 'Super PAC'},
                {'committee_id': WINRED, 'recipient_category': 'Carey'},
                {'committee_id': ACTBLUE}]
        self.assertIs(csv_data.relabel_conduit_rows(rows), rows)
        self.assertEqual([row.get('recipient_category') for row in rows], ['Conduit', 'Super PAC', 'Conduit', None])
        self.assertIsNone(csv_data.relabel_conduit_rows(None))


def graph_workbook(path):
    """A cycle sheet in the fact shape: ActBlue filed as a Super PAC beside a real one, a Master row that
    carries no committee (so only the Recipient rows can be relabelled), and the committee map."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = '2025-2026'
    for row in [['Telecom giving sample'], ['Facts'], [],
                ['Fact Type', 'Company', 'Employee Tier', 'Amount', 'Recipient Category', 'Recipient Key', 'Include in Analysis']]:
        sheet.append(row)
    sheet.append(['Recipient', 'Verizon', 'Employee', 600, 'Super PAC', 'FEC:' + ACTBLUE, 'Yes'])
    sheet.append(['Recipient', 'Verizon', 'Employee', 400, 'Super PAC', 'FEC:C00000003', 'Yes'])
    sheet.append(['Recipient', 'Verizon', 'Employee', 50, 'Carey', 'FEC:' + WINRED, 'Yes'])
    sheet.append(['Master', 'Verizon', 'Employee', 1000, 'Super PAC', None, 'Yes'])
    sheet.append(['Master', 'Verizon', 'Employee', 50, 'Carey', None, 'Yes'])
    maps = book.create_sheet('PAC & Candidate Map')
    for row in [['Mappings'], [], [], [], ['Cycle', 'Committee ID', 'Recipient Committee', 'Recipient Category',
                                         'Direct Candidate / Sponsor', 'Candidate ID(s)', 'Direct Party', 'Relationship']]:
        maps.append(row)
    for committee, name, category in ((ACTBLUE, 'ACTBLUE', 'Super PAC'), ('C00000003', 'MEMBERS FIRST', 'Super PAC')):
        maps.append(['2025-2026', committee, name, category, '', '', '', 'No direct candidate/officeholder link'])
    book.save(path)
    book.close()


class GraphReportFacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'Telecom.xlsx'
        graph_workbook(self.path)

    def test_report_facts_label_the_conduits_and_still_reconcile_to_the_workbook(self):
        report = graph_data.report_facts(graph_data.stamp(self.path))
        categories = {fact['recipient_id']: fact['category'] for fact in report['facts']}
        self.assertEqual(categories, {ACTBLUE: 'Conduit', 'C00000003': 'Super PAC', WINRED: 'Conduit'})
        amounts = {fact['category']: sum(f['amount'] for f in report['facts'] if f['category'] == fact['category'])
                   for fact in report['facts']}
        self.assertEqual({name: float(value) for name, value in amounts.items()}, {'Conduit': 650.0, 'Super PAC': 400.0})
        self.assertEqual({item['id']: item['category'] for item in report['committees']},
                         {ACTBLUE: 'Conduit', 'C00000003': 'Super PAC'})

    def test_the_graph_category_panel_says_conduit(self):
        view = graph_data.graph_view([self.path], {})
        self.assertEqual({row['id']: row['amount'] for row in view['categories']}, {'Conduit': 650, 'Super PAC': 400})
        only = graph_data.graph_view([self.path], {'category': 'Conduit'})
        self.assertEqual(sum(row['amount'] for row in only['organizations']), 650)


def seeded_index(path):
    """A contribution index (the real schema) with two conduits filed as Super PAC / Carey and two ordinary rows."""
    rows = [('a', ACTBLUE, 'ACTBLUE', 'Super PAC', 700000, 'D'), ('b', WINRED, 'WINRED', 'Super PAC', 240000, 'R'),
            ('c', 'C00000003', 'MEMBERS FIRST', 'Super PAC', 50000, 'D'),
            ('d', 'C00000005', 'CAREY PAC', 'Carey', 3000, 'R'), ('e', 'C00000009', 'A CANDIDATE', 'Candidate', 2500, 'D'),
            ('f', ACTBLUE, 'ACTBLUE', 'Carey', 1000, 'D')]
    with closing(sqlite3.connect(path)) as db:
        data._schema(db)
        for sid, committee, name, category, cents, party in rows:
            value = {key: '' for key in data.DIMENSIONS}
            value.update(source_id=sid, industry='Example', company='A', cycle='2025-2026', year='2026', date='2026-01-05',
                         zip5='01234', state='MA', contributor_key='p-' + sid, raw_zip='01234', raw_cents=cents,
                         receipt_cents=cents, original_cents=cents, supported_cents=cents, pac_cents=0, unresolved=0,
                         reasons='[]', evidence_ids='[]', source_url='', source_table='contributions',
                         committee_id=committee, recipient=name, recipient_category=category, party=party)
            db.execute('INSERT INTO facts (' + ','.join('"' + k + '"' for k in value) + ') VALUES (' + ','.join('?' for _ in value) + ')',
                       list(value.values()))
        metadata = {'provenance': {}, 'totals': {'receipt': sum(r[4] for r in rows)},
                    'expected_receipt_cents': sum(r[4] for r in rows), 'ledger_run_id': None, 'identity_basis': 'Reported donor identities'}
        db.execute('INSERT INTO metadata VALUES (?,?)', ('manifest', json.dumps(metadata)))
        db.commit()


class ConduitQueries(unittest.TestCase):
    """Chart Studio reads the stores as they are; the label is applied where the values are read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.path = root / 'facts.sqlite'
        seeded_index(self.path)
        self.source = {'id': 'Example', 'industry': 'Example'}
        patcher = patch.object(store, 'ROOT', root / 'query-cache')
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, **request):
        parts = semantics.validate(request)
        return query._query_ready(self.path, request, self.source, *parts)

    def by_category(self, **request):
        result = self.ask(dimensions=['recipient_category'], **request)
        return {row['recipient_category']: row['amount'] for row in result['rows']}, result

    def test_grouping_by_category_splits_the_conduits_out_of_super_pac_and_carey(self):
        groups, result = self.by_category()
        self.assertEqual(groups, {'Conduit': 941000, 'Super PAC': 50000, 'Carey': 3000, 'Candidate': 2500})
        self.assertEqual(result['totals']['amount'], 996500)
        self.assertEqual(sum(groups.values()), result['totals']['amount'])
        self.assertTrue(result['reconciliation']['conserved'])

    def test_the_conduit_row_is_the_committee_row(self):
        result = self.ask(dimensions=['committee_id', 'recipient_category'],
                          filters=[{'field': 'committee_id', 'op': 'eq', 'value': ACTBLUE}])
        self.assertEqual([(r['committee_id'], r['recipient_category'], r['amount']) for r in result['rows']],
                         [(ACTBLUE, 'Conduit', 701000)])

    def test_filters_on_the_category_use_the_same_label(self):
        cases = [({'op': 'eq', 'value': 'Conduit'}, 941000), ({'op': 'eq', 'value': 'Super PAC'}, 50000),
                 ({'op': 'eq', 'value': 'Carey'}, 3000), ({'op': 'in', 'value': ['Conduit', 'Candidate']}, 943500),
                 ({'op': 'ne', 'value': 'Super PAC'}, 946500), ({'op': 'contains', 'value': 'condu'}, 941000)]
        for condition, expected in cases:
            with self.subTest(condition=condition):
                result = self.ask(measures=['amount', 'records'], filters=[{'field': 'recipient_category', **condition}])
                self.assertEqual(result['totals']['amount'], expected)
                self.assertEqual(result['comparison']['receipt'], expected)

    def test_other_fields_are_untouched_beside_the_label(self):
        result = self.ask(dimensions=['recipient_category', 'party'], measures=['amount', 'records', 'contributors'])
        rows = {(r['recipient_category'], r['party']): (r['amount'], r['records'], r['contributors']) for r in result['rows']}
        self.assertEqual(rows[('Conduit', 'D')], (701000, 2, 2))
        self.assertEqual(rows[('Conduit', 'R')], (240000, 1, 1))
        self.assertEqual(rows[('Super PAC', 'D')], (50000, 1, 1))

    def test_a_question_that_never_names_the_category_is_planned_exactly_as_before(self):
        request = {'dimensions': ['company'], 'measures': ['amount']}
        plan = query._plan(request, *semantics.validate(request))
        self.assertNotIn('Conduit', plan['grouped_sql'])
        self.assertNotIn('Conduit', plan['summary_sql'])

    def test_the_relation_has_the_columns_of_the_tables_it_stands_in_for(self):
        with closing(sqlite3.connect(self.path)) as db:
            declared = [row[1] for row in db.execute('PRAGMA table_info(facts)')]
        self.assertEqual(semantics._index_columns(), declared)
        view = store.view(self.path)
        with closing(sqlite3.connect(view)) as db:
            full = [row[1] for row in db.execute('PRAGMA table_info(facts_full)')]
        self.assertEqual(semantics._relation_columns('facts_full'), full)

    def test_records_rows_say_conduit_and_filter_on_it(self):
        request = {'basis': 'receipt'}
        table = semantics.table('facts', ['recipient_category'], request, original=True)
        with closing(data._ro(self.path)) as db:
            rows = [dict(r) for r in db.execute('SELECT *,receipt_cents AS amount_cents FROM ' + table +
                                                ' WHERE recipient_category = ? ORDER BY source_id', ('Conduit',))]
            plain = [r[0] for r in db.execute('SELECT source_id FROM ' + table + " WHERE recipient_category = 'Super PAC'")]
        self.assertEqual([r['source_id'] for r in rows], ['a', 'b', 'f'])
        self.assertEqual({r['recipient_category'] for r in rows}, {'Conduit'})
        self.assertEqual(plain, ['c'])
        self.assertEqual(sorted(rows[0]), sorted(set(rows[0])), 'no duplicated column leaks into the record')

    def test_the_records_sidecar_pages_by_the_same_label(self):
        ident = sidecar.identity(self.path)
        sidecar.build(self.path, ident)
        ordered, _, _, _ = sidecar.location(ident)
        filters = [{'field': 'recipient_category', 'op': 'eq', 'value': 'Conduit'}]
        request = {'basis': 'receipt', 'filters': filters}
        where, params = semantics.row_filter(filters, 'receipt_cents')
        count, ids = sidecar.page(ordered, request, filters, where + ' AND receipt_cents != 0', params,
                                  'receipt_cents', False, 10, 0)
        self.assertEqual((count, sorted(ids)), (3, ['a', 'b', 'f']))

    def test_the_semantic_identity_moves_with_the_registry(self):
        before = semantics.SHA
        with patch.object(csv_data, 'EARMARK_CONDUITS', {**csv_data.EARMARK_CONDUITS, 'C00000099': 'New Platform'}):
            self.assertNotEqual(semantics.identity(), before)
        self.assertEqual(semantics.identity(), before)


def code_of(name):
    """A source file's text without its comment lines, for assertions about what the code does."""
    text = (HERE.parent / 'app' / name).read_text(encoding='utf-8')
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


class ServerShaping(unittest.TestCase):
    def test_insights_records_are_relabelled_before_they_are_sent(self):
        # analysis_data.py is part of every Insights analysis's code hash, so the label is applied to the
        # answer in the route instead of in the reader.
        text = code_of('server.py')
        route = text[text.index("if self.path in ('/api/insights/query', '/api/insights/records'):"):]
        route = route[:route.index("if self.path in ('/api/contributions/query'")]
        for needle in ('self.contribution_result(reader, data, sources)',
                       "evidence_csv.relabel_conduit_rows(result.get('rows'))", 'self.send(result)'):
            self.assertIn(needle, route)
        self.assertLess(route.index('self.contribution_result('), route.index('relabel_conduit_rows'))
        self.assertLess(route.index('relabel_conduit_rows'), route.index('self.send(result)'))

    def test_the_accounting_fingerprint_files_are_not_touched_by_the_label(self):
        # Editing contribution_data.py would change every store's token (RULE_PATHS) and orphan each one;
        # the label is applied where the values are read instead.
        self.assertNotIn('Conduit', code_of('contribution_data.py'))
        self.assertNotIn('evidence_csv', code_of('contribution_data.py'))
        self.assertTrue(any(path.name == 'contribution_data.py' for path in data.RULE_PATHS))
        self.assertFalse(any(path.name == 'contribution_semantics.py' for path in data.RULE_PATHS))


def comment_above(name, marker):
    """The consecutive comment lines directly above the first line of a source file that starts with `marker`."""
    lines = (HERE.parent / 'app' / name).read_text(encoding='utf-8').splitlines()
    end = next(i for i, line in enumerate(lines) if line.lstrip().startswith(marker))
    start = end
    while start and lines[start - 1].lstrip().startswith('#'):
        start -= 1
    return ' '.join(line.lstrip('# ').strip() for line in lines[start:end])


class SemanticsGuards(unittest.TestCase):
    """P2-4 and P2-5: what the identity and the conduit relation are, said where they are defined."""

    def test_the_sha_is_the_file_plus_the_registry_and_says_so(self):
        self.assertEqual(semantics.SHA, semantics.identity())
        self.assertNotEqual(semantics.SHA, data._hash(semantics.__file__), 'SHA is not the sha256 of the file alone')
        said = comment_above('contribution_semantics.py', 'SHA=identity()')
        self.assertIn('conduit', said)
        self.assertIn('not the sha256 of this file alone', said)

    def test_the_projections_own_facts_table_is_not_the_one_the_conduit_relation_wraps(self):
        projection = set(store.CORE) | {'contributor_key'} | set(store.MONEY)
        index, full = semantics._relation_columns('facts'), semantics._relation_columns('facts_full')
        for column in ('recipient_category', 'committee_id'):
            self.assertNotIn(column, projection)
            self.assertIn(column, index)
            self.assertIn(column, full)
        said = comment_above('contribution_semantics.py', "if base=='facts':return _index_columns()")
        self.assertIn("never the query projection's own 'facts' table", said)
        self.assertIn("facts_full", said)


if __name__ == '__main__':
    unittest.main()
