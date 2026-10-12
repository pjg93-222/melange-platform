"""Money conservation, accounting basis, grain and safe query regressions."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import contribution_data as c


class ContributionQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'facts.sqlite'
        self.source={'id':'Example','industry':'Example'}
        rows=[('a','same-person','01234',10000,10000,10000),
              ('b','same-person','01234',20000,20000,20000),
              ('c','other-person','99999',-5000,-5000,-5000),
              ('d','same-person','01234',10000,0,0)]
        with closing(sqlite3.connect(self.path)) as db:
            c._schema(db)
            for sid,person,zip5,raw,receipt,original in rows:
                value={k:'' for k in c.DIMENSIONS}
                value.update(source_id=sid,industry='Example',company='A',cycle='2023-2024',year='2024',date='2024-01-01',zip5=zip5,state='MA',contributor_key=person,raw_zip=zip5,raw_cents=raw,receipt_cents=receipt,original_cents=original,supported_cents=10000 if sid=='a' else 0,pac_cents=0,unresolved=int(sid!='a'),reasons='[]',evidence_ids='[]',source_url='',source_table='contributions')
                db.execute('INSERT INTO facts ('+','.join('"'+k+'"' for k in value)+') VALUES ('+','.join('?' for _ in value)+')',list(value.values()))
            metadata={'provenance':{},'totals':{'receipt':25000},'expected_receipt_cents':25000,'ledger_run_id':None,'identity_basis':'Reported donor identities'}
            db.execute('INSERT INTO metadata VALUES (?,?)',('manifest',json.dumps(metadata)));db.commit()

    def query(self,**request):
        basis,dims,measures,limit,offset=c._validate(request)
        return c._query_ready(self.path,request,self.source,basis,dims,measures,limit,offset)

    def test_zip_groups_conserve_integer_cents_and_distinct_counts(self):
        result=self.query(dimensions=['zip5'],measures=['amount','contributors','records'])
        self.assertEqual(sum(r['amount'] for r in result['rows']),25000)
        self.assertEqual(result['totals']['contributors'],2)
        self.assertEqual(result['totals']['records'],3)
        self.assertTrue(result['reconciliation']['conserved'])
        self.assertIn('01234',[r['zip5'] for r in result['rows']])

    def test_raw_and_supported_are_explicit_separate_bases(self):
        self.assertEqual(self.query(basis='raw')['totals']['amount'],35000)
        supported=self.query(basis='original_supported')
        self.assertEqual(supported['totals']['amount'],10000)
        self.assertFalse(supported['verified_unique'])

    def test_refunds_stay_signed_and_aggregations_use_underlying_observations(self):
        result=self.query(dimensions=[],measures=list(c.MEASURES))
        row=result['rows'][0]
        self.assertEqual(row['gross_amount'],30000)
        self.assertEqual(row['refunds'],-5000)
        self.assertEqual(row['min_amount'],-5000)
        self.assertEqual(row['max_amount'],20000)
        self.assertEqual(row['median_amount'],10000)
        self.assertEqual(row['average_amount'],8333)

    def test_filters_precede_aggregation_and_aggregate_filter_does_not_fake_total(self):
        result=self.query(dimensions=['zip5'],aggregate_filters=[{'field':'amount','op':'gte','value':10000}],limit=1)
        self.assertEqual(result['rows'],[{'zip5':'01234','amount':30000}])
        self.assertEqual(result['totals']['amount'],25000)
        self.assertEqual(result['reconciliation']['grouped_amount_cents'],30000)
        self.assertIsNone(result['reconciliation']['conserved'])

    def test_money_filters_are_cents_and_private_measure_filters_work(self):
        result=self.query(filters=[{'field':'amount','op':'gte','value':15000}])
        self.assertEqual(result['totals']['amount'],20000)
        with self.assertRaisesRegex(ValueError,'integer cents'):
            self.query(filters=[{'field':'amount','op':'gte','value':1.5}])
        result=self.query(dimensions=['zip5'],measures=['amount'],aggregate_filters=[{'field':'max_amount','op':'gte','value':15000}])
        self.assertEqual(result['rows'],[{'zip5':'01234','amount':30000}])
        self.assertNotIn('max_amount',[column['id'] for column in result['columns']])

    def test_distinct_total_is_not_sum_of_group_distinct_counts(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET zip5='99999' WHERE source_id='b'");db.commit()
        result=self.query(dimensions=['zip5'],measures=['contributors'])
        self.assertEqual(sum(r['contributors'] for r in result['rows']),3)
        self.assertEqual(result['totals']['contributors'],2)

    def test_pagination_is_explicit_and_does_not_truncate_total(self):
        result=self.query(dimensions=['zip5'],limit=1)
        self.assertEqual(result['group_count'],2)
        self.assertTrue(result['has_more'])
        self.assertEqual(result['totals']['amount'],25000)

    def test_sql_injection_and_invalid_basis_are_rejected(self):
        for request in ({'dimensions':['zip5; DROP TABLE facts']},{'basis':'unique_certified'},{'limit':0}):
            with self.assertRaises(ValueError):self.query(**request)
        with self.assertRaises(ValueError):self.query(filters=[{'field':'bogus','op':'eq','value':1}])

    def test_two_equal_gifts_are_not_deduplicated_without_evidence(self):
        rows=[{'sub_id':sid,'company':'A','industry':'Example','company_match_status':'accepted','entity_type':'IND','filing_form':'F3','line_number':'11AI','contribution_receipt_amount':100,'contribution_receipt_date':'2024-01-01','committee_id':'C00000001','contributor_name':'EXAMPLE, DONOR','cycle':2024,'current_receipt_cents':10000} for sid in ('1','2')]
        result=c.analyze(rows)
        self.assertEqual(sum(r['counted_cents'] for r in result['decisions']),20000)

    def test_payload_stream_handles_chunk_boundaries(self):
        path=Path(self.tmp.name)/'payload.json'
        rows=[{'source_id':str(i),'padding':'x'*500} for i in range(300)]
        path.write_text(json.dumps({'metadata':{'raw_rows':300},'raw_rows':rows,'other':[1]}))
        self.assertEqual(list(c._payload_rows(path)),rows)

    def test_source_coalescing_prefers_accepted_claim_across_companies(self):
        source_path=Path(self.tmp.name)/'source.sqlite'
        with closing(sqlite3.connect(source_path)) as db:
            db.execute('CREATE TABLE contributions(sub_id TEXT,company TEXT,industry TEXT,company_match_status TEXT,contribution_receipt_amount REAL,contribution_receipt_date TEXT,committee_id TEXT,contributor_name TEXT,entity_type TEXT,filing_form TEXT,line_number TEXT)')
            for company,status in [('A','rejected'),('B','accepted')]:
                db.execute('INSERT INTO contributions VALUES (?,?,?,?,?,?,?,?,?,?,?)',('same',company,'Example',status,100,'2024-01-01','C00000001','DONOR','IND','F3','11AI'))
            db.commit()
        with closing(sqlite3.connect(':memory:')) as target:
            c._stage_sources(target,{'database':source_path,'industry':'Example'},{})
            row=json.loads(target.execute('SELECT payload FROM canonical_sources').fetchone()[0])
            decision=c.analyze([row])['decisions'][0]
            self.assertEqual(decision['company'],'B')
            self.assertEqual(decision['counted_cents'],10000)
        with closing(sqlite3.connect(source_path)) as db:
            db.execute("UPDATE contributions SET company_match_status='rejected'");db.commit()
        with closing(sqlite3.connect(':memory:')) as target:
            c._stage_sources(target,{'database':source_path,'industry':'Example'},{})
            row=json.loads(target.execute('SELECT payload FROM canonical_sources').fetchone()[0])
            # A heuristic status alone is not contradictory employer evidence.
            decision=c.analyze([row])['decisions'][0]
            self.assertEqual(decision['counted_cents'],10000)
            self.assertEqual(decision['attribution_status'],'unverified')
        with closing(sqlite3.connect(source_path)) as db:
            db.execute('ALTER TABLE contributions ADD COLUMN company_match_reason TEXT')
            db.execute("UPDATE contributions SET company_match_reason='adjudicated: employer belongs to neither queried company'")
            db.commit()
        with closing(sqlite3.connect(':memory:')) as target:
            c._stage_sources(target,{'database':source_path,'industry':'Example'},{})
            row=json.loads(target.execute('SELECT payload FROM canonical_sources').fetchone()[0])
            self.assertEqual(c.analyze([row])['decisions'][0]['counted_cents'],0)

    def test_source_attribution_conflicts_are_preserved(self):
        source_path=Path(self.tmp.name)/'conflict.sqlite'
        with closing(sqlite3.connect(source_path)) as db:
            db.execute('CREATE TABLE contributions(sub_id TEXT,company TEXT,industry TEXT,company_match_status TEXT,contribution_receipt_amount REAL,contribution_receipt_date TEXT,committee_id TEXT,contributor_name TEXT)')
            db.execute("INSERT INTO contributions VALUES ('same','A','Example','accepted',100,'2024-01-01','C00000001','DONOR')")
            db.execute('CREATE TABLE attribution_conflicts(sub_id TEXT,incoming_company TEXT)')
            db.execute("INSERT INTO attribution_conflicts VALUES ('same','B')")
            db.execute('CREATE TABLE source_record_conflicts(sub_id TEXT,reason TEXT)')
            db.execute("INSERT INTO source_record_conflicts VALUES ('same','conflicting_source')");db.commit()
        with closing(sqlite3.connect(':memory:')) as target:
            c._stage_sources(target,{'database':source_path,'industry':'Example'},{})
            row=json.loads(target.execute('SELECT payload FROM canonical_sources').fetchone()[0])
            self.assertIn('B',[r.get('company') for r in row['attribution_claims']])
            self.assertEqual(row['source_conflicts'][0]['reason'],'conflicting_source')
            self.assertEqual(c.analyze([row])['decisions'][0]['bucket'],'conflicting_source_evidence')

    def test_cycle_snapshot_excludes_other_cycles_from_shared_source_database(self):
        source_path=Path(self.tmp.name)/'cycles.sqlite'
        with closing(sqlite3.connect(source_path)) as db, db:
            db.execute('CREATE TABLE contributions(sub_id TEXT,company TEXT,industry TEXT,contribution_receipt_amount REAL,contribution_receipt_date TEXT,committee_id TEXT,contributor_name TEXT,two_year_transaction_period INTEGER)')
            db.executemany('INSERT INTO contributions VALUES (?,?,?,?,?,?,?,?)',[
                ('selected','A','Example',100,'2024-01-01','C00000001','DONOR',2026),
                ('other','A','Example',900,'2026-01-01','C00000001','DONOR',2024)])
        with closing(sqlite3.connect(':memory:')) as target:
            c._stage_sources(target,{'database':source_path,'industry':'Example','cycle_label':'2023-2024'}, {})
            rows=[json.loads(row[0]) for row in target.execute('SELECT payload FROM canonical_sources')]
            self.assertEqual([row['sub_id'] for row in rows], ['selected'])
            self.assertEqual(rows[0]['cycle'], 2024)
            self.assertEqual(rows[0]['source_query_cycle'], 2026)

    def test_rejected_source_conflict_cannot_resurrect_money(self):
        rows=[{'sub_id':'same','company':'A','industry':'Example','company_match_status':'rejected',
            'company_match_reason':'adjudicated: verified employer is not company A',
            'contribution_receipt_amount':100,'contribution_receipt_date':'2024-01-01',
            'committee_id':'C00000001','contributor_name':'DONOR','contributor_zip':zip5,
            'source_table':'contributions','current_receipt_cents':0,'cycle':2024} for zip5 in ('01234','01235')]
        merged=c.coalesce_source_observations(rows)
        self.assertTrue(merged[0]['source_conflicts'])
        decision=c.analyze(merged)['decisions'][0]
        self.assertEqual(decision['counted_cents'],0)
        self.assertEqual(decision['bucket'],'excluded_company_match')

    def test_heuristic_rejection_and_zip_disagreement_do_not_erase_observation(self):
        rows=[{'sub_id':'same','company':'A','industry':'Example','company_match_status':'rejected',
            'company_match_reason':'employer_prefix_not_verified','contribution_receipt_amount':100,
            'contribution_receipt_date':'2024-01-01','committee_id':'C00000001','contributor_name':'DONOR',
            'contributor_zip':zip5,'source_table':'contributions','current_receipt_cents':0,'cycle':2024}
            for zip5 in ('01234','01235')]
        decision=c.analyze(c.coalesce_source_observations(rows))['decisions'][0]
        self.assertEqual(decision['counted_cents'],10000)
        self.assertEqual(decision['supported_unique_cents'],0)
        self.assertEqual(decision['bucket'],'conflicting_source_evidence')
        self.assertEqual(decision['attribution_status'],'unverified')

    def test_evidence_geography_requires_matching_identity_and_money(self):
        row={'sub_id':'x','company':'A','contributor_name':'DONOR','contribution_receipt_amount':100,'contribution_receipt_date':'2024-01-01','contributor_zip':''}
        remote={**row,'company':'untrusted','contributor_zip':'01234'}
        result=c._effective_row(row,{'x':remote})
        self.assertEqual(result['company'],'A');self.assertEqual(result['contributor_zip'],'01234')
        with self.assertRaises(ValueError):c._effective_row(row,{'x':{**remote,'contribution_receipt_amount':101}})
        with self.assertRaisesRegex(ValueError,'date is invalid'):
            c._record({**row,'contribution_receipt_date':'2024-02-30'},{},None)

    def test_running_process_cannot_label_old_imports_as_new_code(self):
        with patch.object(c,'LOADED_RULE_STAMPS',[]):
            with self.assertRaisesRegex(ValueError,'Restart the app'):c._token({})

    def test_evidence_epoch_ignores_read_locks_but_tracks_nonempty_wal(self):
        root=Path(self.tmp.name);evidence=root/'state/cross-reference/evidence.sqlite'
        evidence.parent.mkdir(parents=True);evidence.write_bytes(b'pinned evidence')
        source={'ledger':None}
        for key in ('workbook','database','payload','manifest'):
            source[key]=root/key;source[key].write_bytes(key.encode())
        with patch.object(c,'PROJECT',root):
            before=c._token(source)
            Path(str(evidence)+'-shm').write_bytes(b'read locks')
            Path(str(evidence)+'-wal').write_bytes(b'')
            self.assertEqual(c._token(source),before)
            Path(str(evidence)+'-wal').write_bytes(b'new evidence')
            self.assertNotEqual(c._token(source),before)


class PortableTokenTests(unittest.TestCase):
    """An index token names the same source on any machine: no absolute path, no ctime; code by content (CRLF = LF)."""
    STAMP=1_700_000_000_000_000_000
    CODE={'tools/rule_a.py':b'def a():\n    return 1\n','tools/rule_b.py':b'B = 2\n','inputs/rules.json':b'{\n  "r": 1\n}\n'}
    DATA={'reports/Example Industry Spending Overview.xlsx':b'book','.cache/payload.json':b'payload','.cache/manifest.json':b'{}',
          '.cache/hydration.json':b'{}','state/cross-reference/r1/ledger.sqlite':b'ledger','state/cross-reference/r1/report.json':b'{}'}

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)

    def machine(self,name,crlf=False):
        """A checkout and report folder under their own temp root, data mtimes pinned, optionally CRLF code."""
        base=Path(self.tmp.name)/name;project=base/'app-root'/'mentat'
        for relative,content in {**self.CODE,**self.DATA}.items():
            path=(base/relative if relative.startswith('reports/') else project/relative)
            path.parent.mkdir(parents=True,exist_ok=True)
            code=relative in self.CODE
            path.write_bytes(content.replace(b'\n',b'\r\n') if crlf and code else content)
            if not code:os.utime(path,ns=(self.STAMP,self.STAMP))
        elsewhere=base/('moved-'+name)/'mentat.db';elsewhere.parent.mkdir();elsewhere.write_bytes(b'database')
        os.utime(elsewhere,ns=(self.STAMP,self.STAMP))   # outside the report folder and the checkout: file name only
        source={'workbook':base/'reports/Example Industry Spending Overview.xlsx','database':elsewhere,
                'payload':project/'.cache/payload.json','manifest':project/'.cache/manifest.json',
                'hydration_manifest':project/'.cache/hydration.json',
                'ledger':project/'state/cross-reference/r1/ledger.sqlite'}
        return project,source,[project/relative for relative in self.CODE]

    def token(self,project,source,rules):
        stamps=[(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in rules]
        with patch.object(c,'PROJECT',project),patch.object(c,'RULE_PATHS',rules),patch.object(c,'LOADED_RULE_STAMPS',stamps):
            return c._token(source)

    def test_the_same_inputs_under_another_root_and_line_endings_give_one_token(self):
        first,second=self.machine('laptop'),self.machine('vps-longer-name',crlf=True)
        self.assertNotEqual(first[0],second[0])
        self.assertEqual((first[2][0].stat().st_size==second[2][0].stat().st_size),False,'the code really differs on disk')
        self.assertEqual(self.token(*first),self.token(*second))

    def test_a_data_files_size_or_mtime_or_a_rule_files_content_changes_the_token(self):
        project,source,rules=self.machine('laptop');before=self.token(project,source,rules)
        self.assertEqual(before,self.token(project,source,rules))
        for key in ('payload','ledger','database'):
            path=Path(source[key]);original=path.read_bytes()
            path.write_bytes(original+b'x');os.utime(path,ns=(self.STAMP,self.STAMP))     # size only
            self.assertNotEqual(self.token(project,source,rules),before,key+' size')
            path.write_bytes(original);os.utime(path,ns=(self.STAMP,self.STAMP))
            self.assertEqual(self.token(project,source,rules),before)
            os.utime(path,ns=(self.STAMP+1_000_000_000,self.STAMP+1_000_000_000))        # mtime only
            self.assertNotEqual(self.token(project,source,rules),before,key+' mtime')
            os.utime(path,ns=(self.STAMP,self.STAMP))
        rule=rules[1];rule.write_bytes(b'B = 3\n')                                       # same size, other content
        self.assertEqual(rule.stat().st_size,len(b'B = 2\n'))
        self.assertNotEqual(self.token(project,source,rules),before)
        rule.write_bytes(b'B = 2\n');os.utime(rule,ns=(self.STAMP,self.STAMP))           # a rule file's mtime alone is not in the token
        self.assertEqual(self.token(project,source,rules),before)

    def test_the_token_holds_no_absolute_path_or_ctime(self):
        project,source,rules=self.machine('laptop')
        with patch.object(c.json,'dumps',wraps=json.dumps) as dumped:
            self.token(project,source,rules)
        inputs,digests=dumped.call_args.args[0]
        self.assertEqual([entry[0] for entry in inputs],['Example Industry Spending Overview.xlsx','mentat.db','.cache/payload.json',
            '.cache/manifest.json','.cache/hydration.json','state/cross-reference/r1/ledger.sqlite','state/cross-reference/r1/report.json'])
        self.assertTrue(all(len(entry)==3 and entry[2]==self.STAMP for entry in inputs))   # name, size, mtime_ns
        self.assertEqual(len(digests),len(rules));self.assertTrue(all(len(d)==64 for d in digests))


class CompactReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'payload.json'
        self.raw={'sub_id':'x','company':'Example','contribution_receipt_amount':100,
            'contribution_receipt_date':'2024-01-01','contributor_name':'DONOR','committee_id':'C00000001'}
        self.receipt={'source_id':'x','amount':100,'contributor':'DONOR','committee_id':'C00000001',
            'cycle':2024,'include_in_analysis':'Yes'}

    def project(self,receipt=None,declared=True,proved=True):
        row=dict(self.receipt if receipt is None else receipt)
        metadata={'payload_compaction':{'mode':'formula_fact_required_fields',
            'raw_fields':list(self.receipt)}} if declared else {}
        self.path.write_text(json.dumps({'metadata':metadata,'raw_rows':[row]}))
        with closing(sqlite3.connect(':memory:')) as db:
            return c._Receipts(db,self.path,source_proof={'full_financial_universe_preserved':proved}).get('x')

    def record(self,receipt):
        return c._record(self.raw,{'counted_cents':10000},receipt)

    def test_declared_compact_cycle_uses_verified_source_date(self):
        row=self.record(self.project())
        self.assertEqual(row['date'],'2024-01-01')
        self.assertEqual(row['receipt_cents'],10000)

    def test_omission_requires_explicit_schema_and_complete_source_proof(self):
        for options in ({'declared':False},{'proved':False}):
            with self.subTest(options=options),self.assertRaisesRegex(ValueError,'source proof'):
                self.project(**options)

    def test_compact_cycle_and_other_identities_still_match_exactly(self):
        for change in ({'cycle':2022},{'amount':99},{'committee_id':'C00000002'},{'contributor':'OTHER'}):
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'differs financially'):
                self.record(self.project({**self.receipt,**change}))

    def test_present_dates_are_strict_even_with_compact_metadata(self):
        for value in ('2024-01-02','',None):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'differs financially'):
                self.record(self.project({**self.receipt,'date':value}))
        self.assertEqual(self.record(self.project({**self.receipt,'date':'2024-01-01'}))['receipt_cents'],10000)

    def test_invalid_compact_cycles_are_rejected(self):
        for value in (True,'2024',None,2025):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'valid election cycle'):
                self.project({**self.receipt,'cycle':value})


class CompanionDataWorkbookTests(unittest.TestCase):
    """The tall companion book is LINEAGE, never a substitute report source."""

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.reports=Path(self.tmp.name)/'Published Reports';self.reports.mkdir()
        c._workbook_source.cache_clear();self.addCleanup(c._workbook_source.cache_clear)

    def book(self,name,database_sha256):
        import openpyxl
        path=self.reports/name
        workbook=openpyxl.Workbook();sheet=workbook.active;sheet.title='About'
        sheet.append(['Key','Value'])
        # The single text run app/contribution_data._workbook_source scans for.
        sheet.append(['Lineage',f'Source DB SHA-256: {database_sha256}'])
        workbook.save(path);workbook.close()
        return path

    def sources(self):
        # The pinned-payload lookup fails in a fixture folder, which is the
        # point: `available` goes False and the companion keys must still be
        # attached, because they are evidence about the FILES, not about the
        # verified dataset.
        return c._sources(self.reports)

    def test_sibling_data_book_is_attached_with_a_matching_lineage_flag(self):
        digest='a'*64
        overview=self.book('Example Industry Spending Overview.xlsx',digest)
        data=self.book('Example Industry Spending Data.xlsx',digest)
        source=self.sources()[0]
        self.assertEqual(source['workbook'],overview)
        self.assertEqual(source['data_workbook'],data)
        self.assertEqual(source['data_workbook_sha256'],c._hash(data))
        self.assertTrue(source['data_lineage_matches'])
        provenance=c._provenance(source)
        self.assertEqual(provenance['data_workbook'],data.name)
        self.assertEqual(provenance['data_workbook_sha256'],c._hash(data))
        self.assertIs(provenance['data_lineage_matches'],True)

    def test_a_data_book_pinned_to_another_database_is_reported_not_accepted(self):
        self.book('Example Industry Spending Overview.xlsx','a'*64)
        self.book('Example Industry Spending Data.xlsx','b'*64)
        provenance=c._provenance(self.sources()[0])
        self.assertIs(provenance['data_lineage_matches'],False)

    def test_no_companion_book_reports_none_rather_than_a_mismatch(self):
        self.book('Example Industry Spending Overview.xlsx','a'*64)
        source=self.sources()[0]
        self.assertNotIn('data_workbook',source)
        provenance=c._provenance(source)
        self.assertIsNone(provenance['data_workbook'])
        self.assertIsNone(provenance['data_lineage_matches'])

    def test_the_data_book_is_never_listed_as_a_report_source_of_its_own(self):
        self.book('Example Industry Spending Overview.xlsx','a'*64)
        self.book('Example Industry Spending Data.xlsx','a'*64)
        self.assertEqual([s['id'] for s in self.sources()],['Example'])


if __name__=='__main__':unittest.main()
