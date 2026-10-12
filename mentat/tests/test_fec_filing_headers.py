import json, sqlite3, sys, tempfile, unittest
from pathlib import Path
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from fec_filing_headers import parse_header, fetch_header, apply_header

def fixture(form='F3X',amendment='A',original='FEC-99',version='8.5'):
    report=['']*30
    report[:3]=[form+amendment,'C00000001','TEST COMMITTEE']
    code,start,end={'F3X':(9,13,14),'F3':(11,15,16),'F3P':(11,15,16),'F4':(10,11,12)}[form]
    report[code]='Q1';report[start]='20240101';report[end]='20240331'
    return ('\x1c'.join(['HDR','FEC',version,'Fixture','1',original,'1',''])+'\r\n'+'\x1c'.join(report)+'\r\n').encode()

class FilingHeaderTests(unittest.TestCase):
    def test_form_and_original_reference_follow_filing_not_committee_name(self):
        for form in ('F3X','F3','F3P','F4'):
            with self.subTest(form=form):
                h=parse_header(fixture(form=form),100)
                self.assertEqual((h['filing_form'],h['original_file_num'],h['coverage_end']),(form,99,'2024-03-31'))

    def test_unsupported_and_truncated_data_remain_unresolved(self):
        for data in (fixture(version='8.2'),fixture()[:100],b'<html>not found</html>\n\n'):
            with self.subTest(data=data[:20]),self.assertRaises(ValueError):parse_header(data,100)

    def test_cache_is_reused_and_checked(self):
        with tempfile.TemporaryDirectory() as d:
            first=fetch_header(100,d,lambda url:fixture())
            self.assertEqual(fetch_header(100,d,lambda url:self.fail('Unneeded network request')),first)
            p=Path(d)/'100.json';value=json.loads(p.read_text());value['header']['filing_form']='F3';p.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'interpretation'):fetch_header(100,d)

    def test_84_common_header_layout_and_unsupported_evidence_preservation(self):
        self.assertEqual(parse_header(fixture(version='8.4'),100)['original_file_num'],99)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'Unsupported'):
                fetch_header(100,d,lambda url:fixture(version='8.2'))
            saved=json.loads((Path(d)/'100.review.json').read_text())
            self.assertEqual(bytes.fromhex(saved['prefix_hex']),fixture(version='8.2'))
            with self.assertRaisesRegex(ValueError,'Unsupported'):
                fetch_header(100,d,lambda url:self.fail('Unsupported evidence must also be reused'))

    def test_83_recovers_only_literal_form_without_inventing_relationships(self):
        for form in ('F3','F3X','F3P','F4'):
            h=parse_header(fixture(form=form,version='8.3'),100)
            self.assertEqual(h['filing_form'],form)
            self.assertFalse(h['relationship_evidence'])
            self.assertIsNone(h['original_file_num'])
            self.assertIsNone(h['coverage_end'])

    def test_control_whitespace_and_omitted_empty_amendment_fields(self):
        data=fixture(version='8.4 ',original='FEC- 99')
        self.assertEqual(parse_header(data,100)['original_file_num'],99)
        data=fixture(amendment='N',original='')
        first,rest=data.split(b'\n',1)
        first=b'\x1c'.join(first.split(b'\x1c')[:5])
        h=parse_header(first+b'\n'+rest,100)
        self.assertEqual(h['filing_form'],'F3X')
        self.assertIsNone(h['original_file_num'])

    def test_fill_only_form_preserves_dollars_and_relationship_unknowns(self):
        with tempfile.TemporaryDirectory() as d,closing(sqlite3.connect(':memory:')) as c:
            for table in ('contributions','corporate_giving'):
                c.execute(f'CREATE TABLE {table}(sub_id TEXT,committee_id TEXT,filing_form TEXT,file_num INTEGER,company_match_status TEXT,amount REAL,back_reference_transaction_id TEXT)')
            c.execute("INSERT INTO contributions VALUES('1','C00000001',NULL,100,'accepted',123,NULL)")
            c.execute('CREATE TABLE receipt_metadata_history(source_id TEXT,source_table TEXT,evidence_source TEXT,changes_json TEXT)')
            ev=fetch_header(100,d,lambda url:fixture())
            with c:self.assertEqual(apply_header(c,ev),1)
            with c:self.assertEqual(apply_header(c,ev),0)
            self.assertEqual(c.execute('SELECT filing_form,amount,back_reference_transaction_id FROM contributions').fetchone(),('F3X',123,None))
            self.assertEqual(c.execute('SELECT count(*) FROM receipt_metadata_history').fetchone()[0],1)
            c.execute("INSERT INTO corporate_giving VALUES('2','C00000002',NULL,100,'accepted',200,NULL)")
            with self.assertRaisesRegex(ValueError,'committee conflicts'):
                with c:apply_header(c,ev)

if __name__=='__main__':unittest.main()
