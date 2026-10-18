"""Inert controls for tools/build_bulk_activity_index.py (D076). Synthetic bulk files in a temporary directory; no network, no credential, no Mentat state.

The builder turns verified FEC bulk files into a standalone JSON ordering hint. These controls pin what it counts (and what it must not), that every source is hash- and row-verified
before a single row is trusted, that cycle is the FILE's cycle, that coverage is only ever `complete` when every required source file was ingested, that nothing is ever written
for an unobserved committee (no zero rows), and that the output is deterministic and never overwrites.
"""
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import build_bulk_activity_index as bx   # noqa: E402

A, B, C, D, E = 'C00000001', 'C00000002', 'C00000003', 'C00000004', 'C00000005'


def oth_line(filer, kind, other, memo='', report='Q1', amount='100'):
    # 21 fields: CMTE_ID AMNDT_IND RPT_TP PGI IMAGE_NUM TRANSACTION_TP ENTITY_TP NAME CITY STATE ZIP EMPLOYER OCCUPATION TRANSACTION_DT AMT OTHER_ID TRAN_ID FILE_NUM MEMO_CD MEMO_TEXT SUB_ID
    return '|'.join([filer, 'N', report, 'G2024', '2024', kind, 'ORG', 'NAME', 'CITY', 'ST', '00000', '', '', '01012031', amount, other, 'T1', '1', memo, '', '4000']).encode()


def pas2_line(filer, kind, other='', report='Q1'):
    # 22 fields: ... OTHER_ID then CAND_ID
    return '|'.join([filer, 'N', report, 'G2024', '2024', kind, 'ORG', 'NAME', 'CITY', 'ST', '00000', '', '', '01012024', '10', other, '', 'T1', '1', '', '', '4000']).encode()


def oppexp_line(filer, date='01012031'):
    return ('|'.join([filer, 'N', '2024', 'Q1', '2024', '17', 'F3', 'SB', 'PAYEE', 'CITY', 'ST', '00000', date, '10', 'G2024', 'PURPOSE', '', '', '', '', 'ORG', '4000', '1', 'T1', '']) + '|').encode()


def ie_text(spenders, year='2024', header=None):
    import csv
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(header or bx.IE_HEADER)
    for spender in spenders:
        row = [''] * len(bx.IE_HEADER)
        row[2], row[20] = spender, year
        writer.writerow(row)
    return out.getvalue().encode()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D076-builder-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sha(self, data):
        return hashlib.sha256(data).hexdigest()

    def put(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def oppexp(self, cycle, filers, **extra):
        body = b'\n'.join(oppexp_line(f) for f in filers) + b'\n'
        path = self.root / ('oppexp%d.zip' % cycle)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('oppexp.txt', body)
        return dict(id='oppexp%d' % cycle, kind='oppexp', cycle=cycle, path=str(path), sha256=self.sha(path.read_bytes()), member='oppexp.txt', member_sha256=self.sha(body),
                    rows=len(filers), vintage_asof='2026-10-12T00:00:00Z', **extra)

    def flat(self, kind, cycle, lines, **extra):
        body = b'\n'.join(lines) + b'\n'
        path = self.put('%s%d.txt' % (kind, cycle), body)
        return dict(id='%s%d' % (kind, cycle), kind=kind, cycle=cycle, path=str(path), sha256=self.sha(body), rows=len(lines), vintage_asof='2026-09-06T00:00:00Z', **extra)

    def ie(self, cycle, spenders, **extra):
        body = ie_text(spenders, str(cycle))
        path = self.put('ie%d.csv' % cycle, body)
        return dict(id='ie%d' % cycle, kind='ie', cycle=cycle, path=str(path), sha256=self.sha(body), rows=len(spenders), vintage_asof='2026-10-17T00:00:00Z', **extra)

    def full_2024(self):
        return [
            self.oppexp(2024, [A, A, B, 'NOT-A-COMMITTEE']),
            self.flat('oth', 2024, [oth_line(A, '24K', C), oth_line(A, '24K', C, memo='X'), oth_line(B, '22Z', D), oth_line(B, '24G', B),               # self transfer: skipped
                                    oth_line(C, '24K', 'H0AA00001'), oth_line(C, '24K', ''),                                                    # candidate / blank counterparty: not a committee
                                    oth_line(D, '18K', A), oth_line(D, '24E', ''), oth_line(E, '24Z', A)]),
            self.flat('pas2', 2024, [pas2_line(A, '24K', C), pas2_line(C, '24Z', D), pas2_line(D, '24E', report='Q3'), pas2_line(D, '24A', report='M5'), pas2_line(E, '24A', report='24'),
                                     pas2_line(E, '24E', report='48'), pas2_line(A, '24C'), pas2_line(A, '24F'), pas2_line(A, '24N')]),
            self.ie(2024, [E, E, 'NOT-A-COMMITTEE'])]

    def build(self, entries):
        return bx.build(entries, '2026-10-17T00:00:00Z')

    def rows(self, index):
        return {(c, y, k): (n, per) for c, y, k, n, per in index['rows']}


class Mapping(Fixture):
    def test_each_source_maps_to_the_documented_kind_and_only_that(self):
        got = self.rows(self.build(self.full_2024()))
        self.assertEqual(got[(A, 2024, 'B')], (2 + 2 + 1, {'oppexp2024': 2, 'oth2024': 2, 'pas22024': 1}), 'oppexp 2 + OTH 24K twice (memo counted) + PAS2 24K')
        self.assertEqual(got[(B, 2024, 'B')], (1 + 1, {'oppexp2024': 1, 'oth2024': 1}), 'OTH 22Z counts; the OTH 24G self transfer does not')
        self.assertEqual(got[(C, 2024, 'B')], (1, {'pas22024': 1}), 'PAS2 24Z counts; OTH 24K to a candidate id or a blank counterparty does not')
        self.assertEqual(got[(E, 2024, 'B')], (1, {'oth2024': 1}), 'OTH 24Z to a committee')
        self.assertNotIn((D, 2024, 'B'), got, 'OTH 18K (a receipt) and 24E are never B evidence')
        self.assertEqual(got[(D, 2024, 'E_regular')], (2, {'pas22024': 2}), 'PAS2 24E and 24A on regular reports')
        self.assertEqual(got[(E, 2024, 'E_notice')], (2, {'ie2024': 2}), 'E_notice is the IE file and only the IE file')
        self.assertEqual(got[(E, 2024, 'E_regular')], (2, {'pas22024': 2}), 'PAS2 24A on RPT_TP 24 and 24E on RPT_TP 48 are E_regular by ruling, not E_notice')
        self.assertNotIn((A, 2024, 'E_regular'), got, 'PAS2 24C / 24F / 24N are Schedule F / communication costs, not B and not E')
        self.assertEqual({k for k in got if k[0] == 'NOT-A-COMMITTEE'}, set(), 'a filer that is not a committee id is skipped and counted, never indexed')

    def test_cycle_is_the_files_cycle_never_a_transaction_date(self):
        index = self.build([self.oppexp(2022, [A])])
        self.assertEqual(list(self.rows(index)), [(A, 2022, 'B')], 'the row dates 2031 but sits in the 2022 file')

    def test_skipped_rows_are_counted_in_the_provenance_not_dropped_silently(self):
        index = self.build(self.full_2024())
        record = {source['id']: source for source in index['provenance']['sources']}
        self.assertEqual(record['oppexp2024']['rows_skipped'], {'filer_is_not_a_committee_id': 1})
        self.assertEqual(record['oth2024']['rows_skipped'], {'self_transfer': 1, 'counterparty_or_filer_is_not_a_committee_id': 2})
        self.assertEqual(record['pas22024']['rows_skipped'], {'other_family_F': 1, 'other_family_communication_costs': 2})
        self.assertEqual(record['ie2024']['rows_skipped'], {'spender_is_not_a_committee_id': 1})
        self.assertEqual(record['oth2024']['transaction_type_census']['24E'], 1)
        self.assertEqual(record['pas22024']['independent_expenditure_rows_by_report_class'], {'notice': 2, 'regular': 2})
        self.assertTrue(all(source['hashes_match_recorded'] for source in record.values()))

    def test_no_zero_row_is_ever_written(self):
        index = self.build(self.full_2024())
        self.assertTrue(all(n >= 1 and per and sum(per.values()) == n and all(v >= 1 for v in per.values()) for _, _, _, n, per in index['rows']))
        self.assertTrue(index['provenance']['no_row_is_not_zero'])

    def test_rows_are_sorted_unique_and_only_the_three_kinds(self):
        rows = self.build(self.full_2024())['rows']
        keys = [tuple(r[:3]) for r in rows]
        self.assertEqual(keys, sorted(set(keys)))
        self.assertEqual({k for _, _, k, _, _ in rows}, {'B', 'E_regular', 'E_notice'})


class Coverage(Fixture):
    def test_complete_only_when_every_required_source_was_ingested(self):
        coverage = self.build(self.full_2024())['coverage']
        self.assertTrue(all(coverage[kind]['2024']['complete'] for kind in ('B', 'E_regular', 'E_notice')))
        self.assertTrue(not any(coverage[kind][cycle]['complete'] for kind in coverage for cycle in ('2022', '2026')), 'no file for those cycles')
        self.assertEqual(coverage['B']['2022']['missing_source_kinds'], ['oppexp', 'oth', 'pas2'])

    def test_a_missing_source_kind_makes_that_kind_incomplete_but_keeps_its_observed_rows(self):
        entries = [e for e in self.full_2024() if e['kind'] != 'ie']
        index = self.build(entries)
        self.assertEqual(index['coverage']['E_notice']['2024'], {'complete': False, 'ingested': [], 'missing_source_kinds': ['ie']})
        self.assertTrue(index['coverage']['B']['2024']['complete'] and index['coverage']['E_regular']['2024']['complete'])
        self.assertNotIn((E, 2024, 'E_notice'), self.rows(index), 'no IE file, no E_notice row (PAS2 never produces one)')
        without_oth = self.build([e for e in self.full_2024() if e['kind'] != 'oth'])
        self.assertEqual(without_oth['coverage']['B']['2024'], {'complete': False, 'ingested': ['oppexp2024', 'pas22024'], 'missing_source_kinds': ['oth']})
        self.assertIn((A, 2024, 'B'), self.rows(without_oth), 'positive evidence stands even when the zero reading is not licensed')

    def test_a_pas2_independent_expenditure_is_never_e_notice_whatever_its_report_type(self):
        only = self.build([self.flat('pas2', 2024, [pas2_line(A, '24E', report='24'), pas2_line(A, '24A', report='48'), pas2_line(A, '24E', report='Q1')])])
        self.assertEqual(self.rows(only), {(A, 2024, 'E_regular'): (3, {'pas22024': 3})})
        record = only['provenance']['sources'][0]
        self.assertEqual(record['independent_expenditure_rows_by_report_class'], {'notice': 2, 'regular': 1}, 'the report classes stay on record')

    def test_vintage_is_the_oldest_and_newest_source(self):
        vintage = self.build(self.full_2024())['provenance']['vintage']
        self.assertEqual(vintage, {'oldest_asof': '2026-09-06T00:00:00Z', 'newest_asof': '2026-10-17T00:00:00Z'})


class Verification(Fixture):
    def refused(self, entries, text):
        with self.assertRaises(ValueError) as caught:
            self.build(entries)
        self.assertIn(text, str(caught.exception))

    def test_a_container_hash_mismatch_refuses(self):
        entries = self.full_2024()
        entries[1]['sha256'] = '0' * 64
        self.refused(entries, 'differs from the recorded')

    def test_a_zip_member_hash_mismatch_refuses(self):
        entries = self.full_2024()
        entries[0]['member_sha256'] = '1' * 64
        self.refused(entries, 'member sha256')

    def test_a_row_count_that_differs_from_the_verified_record_refuses(self):
        entries = self.full_2024()
        entries[2]['rows'] += 1
        self.refused(entries, 'the verified record says')

    def test_a_zip_with_another_member_list_refuses(self):
        entries = self.full_2024()
        with zipfile.ZipFile(entries[0]['path'], 'w') as archive:
            archive.writestr('other.txt', b'x')
            archive.writestr('oppexp.txt', b'y')
        entries[0]['sha256'] = self.sha(Path(entries[0]['path']).read_bytes())
        self.refused(entries, 'instead of exactly')

    def test_an_ie_header_other_than_the_reviewed_columns_refuses(self):
        body = ie_text([A], header=bx.IE_HEADER[:-1] + ['something_else'])
        path = self.put('ie2024.csv', body)
        entry = dict(id='ie2024', kind='ie', cycle=2024, path=str(path), sha256=self.sha(body), vintage_asof='2026-10-17T00:00:00Z')
        self.refused([entry], 'header differs')

    def test_duplicate_ids_or_two_files_for_one_kind_and_cycle_refuse(self):
        one = self.oppexp(2024, [A])
        self.refused([one, dict(one)], 'duplicate source ids')
        two = dict(one, id='other')
        self.refused([one, two], 'two sources for one')

    def test_unknown_kind_cycle_or_malformed_hash_refuses(self):
        one = self.oppexp(2024, [A])
        for edit in ({'kind': 'cm'}, {'cycle': 2020}, {'sha256': 'abc'}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.build([dict(one, **edit)])

    def test_cli_writes_one_new_file_never_overwrites_and_writes_nothing_on_refusal(self):
        entries = self.full_2024()
        manifest = self.put('sources.json', json.dumps(entries).encode())
        out = self.root / 'index.json'
        with redirect_stdout(io.StringIO()) as stdout:
            bx.main(['--sources', str(manifest), '--out', str(out), '--built-utc', '2026-10-17T00:00:00Z'])
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary['sha256'], self.sha(out.read_bytes()))
        with self.assertRaises(SystemExit):
            bx.main(['--sources', str(manifest), '--out', str(out)])
        entries[1]['sha256'] = '0' * 64
        bad = self.put('bad.json', json.dumps(entries).encode())
        target = self.root / 'bad-index.json'
        with self.assertRaises(ValueError):
            bx.main(['--sources', str(bad), '--out', str(target)])
        self.assertFalse(target.exists())


class Output(Fixture):
    def dump(self, entries):
        return (json.dumps(self.build(entries), sort_keys=True, separators=(',', ':')) + chr(10)).encode()

    def test_output_is_strict_json_with_the_documented_header_and_provenance(self):
        document = json.loads(self.dump(self.full_2024()))
        self.assertEqual((document['schema_version'], document['kind']), (1, 'mentat-bulk-activity-index'))
        self.assertEqual(document['provenance']['builder']['sha256'], hashlib.sha256(Path(bx.__file__).read_bytes()).hexdigest())
        self.assertIn('not API coverage', document['provenance']['statement'])
        self.assertEqual(document['stats']['rows'], len(document['rows']))
        self.assertEqual(document['provenance']['cycle_rule'][:20], "the bulk file's own ")

    def test_two_builds_of_the_same_inputs_are_byte_identical_and_rows_do_not_depend_on_source_order(self):
        entries = self.full_2024()
        self.assertEqual(self.dump(entries), self.dump(entries))
        self.assertEqual(json.dumps(self.build(entries)['rows']), json.dumps(self.build(list(reversed(entries)))['rows']))


if __name__ == '__main__':
    unittest.main()
