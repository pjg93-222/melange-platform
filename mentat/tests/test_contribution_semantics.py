"""Expanded fields and charts preserve individual-observation semantics."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import contribution_data as data
import contribution_query as query
import contribution_semantics as semantics
from test_contribution_data import ContributionQueryTests

class SemanticTests(unittest.TestCase):
    setUp=ContributionQueryTests.setUp
    def result(self,**request):return query._query_ready(self.path,request,self.source,*semantics.validate(request))
    def test_metadata_memory_workspace_is_scoped_to_narrow_small_sources(self):
        for refs,expected in [(['company'],1),(['reported_filing_form'],2),(list(semantics.RAW)[:5],1)]:
            with closing(sqlite3.connect(':memory:')) as db:
                db.execute('PRAGMA temp_store=FILE')
                with query._metadata_workspace(db,{'database':self.path},refs):
                    self.assertEqual(db.execute('PRAGMA temp_store').fetchone()[0],expected)
        with closing(sqlite3.connect(':memory:')) as db, patch.object(Path,'stat') as stat:
            stat.return_value.st_size=1024**3+1
            db.execute('PRAGMA temp_store=FILE')
            with query._metadata_workspace(db,{'database':self.path},['reported_filing_form']):
                self.assertEqual(db.execute('PRAGMA temp_store').fetchone()[0],1)
    def test_every_added_dimension_preserves_amount_and_count(self):
        for dimension in list(semantics.AUDIT)+list(semantics.DERIVED):
            with self.subTest(dimension=dimension):
                result=self.result(dimensions=[dimension],measures=['amount','records'])
                self.assertEqual(sum(r['amount'] for r in result['rows']),25000)
                self.assertEqual(sum(r['records'] for r in result['rows']),3)
                self.assertTrue(result['reconciliation']['conserved'])
    def test_histogram_signed_boundaries_and_width(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET receipt_cents=-1 WHERE source_id='c'");db.commit()
        r=self.result(dimensions=['amount_bin'],measures=['amount','records'],bin_width_cents=10000,sort=[{'field':'amount_bin','direction':'asc'}])
        self.assertEqual([x['amount_bin'] for x in r['rows']],['-10000','10000','20000'])
        self.assertEqual(r['totals']['amount'],29999)
        r=self.result(dimensions=['amount_bin'],measures=['records'],bin_width_cents=20000)
        self.assertEqual(sum(x['records'] for x in r['rows']),3)
    def test_quantiles_are_from_observations_and_exclude_zero_basis(self):
        r=self.result(dimensions=[],measures=['q1_amount','q3_amount','median_amount','stddev_amount','records'])
        self.assertEqual(r['rows'][0]['q1_amount'],2500)
        self.assertEqual(r['rows'][0]['q3_amount'],15000)
        self.assertEqual(r['rows'][0]['median_amount'],10000)
        self.assertEqual(r['totals']['records'],3)
        self.assertEqual(r['totals']['stddev_amount'],10274)
    def test_dates_distincts_and_physical_audit_relationship(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET date='2024-04-02',committee_id='C1',company='B' WHERE source_id='b'")
            db.execute("UPDATE facts SET committee_id='C1' WHERE source_id='a'");db.commit()
        r=self.result(dimensions=['source_id','quarter'],measures=list(semantics.EXTRA))
        self.assertEqual(r['totals']['committees'],1)
        self.assertEqual(r['totals']['companies'],2)
        self.assertEqual(r['totals']['active_days'],2)
        self.assertEqual(r['totals']['first_date'],'2024-01-01')
        self.assertEqual(r['totals']['last_date'],'2024-04-02')
        self.assertEqual(r['totals']['span_days'],92)
        self.assertEqual(next(row['quarter'] for row in r['rows'] if row['source_id']=='b'),'2024-Q2')
    def test_derived_and_audit_filters_apply_to_population_and_drill(self):
        for field,value in [('raw_zip','01234'),('source_id','a'),('month','2024-01'),('amount_bin','10000'),('review_required','False')]:
            filters=[{'field':field,'op':'eq','value':value}]
            r=self.result(dimensions=['company'],filters=filters)
            with patch.object(data,'_source',return_value=self.source),patch.object(data,'_ready',return_value=(self.path,None)),patch.object(data,'_token',return_value='fixture'),patch.object(data,'_provenance',return_value={}):
                detail=query.records(self.path.parent,{'filters':filters})
            self.assertEqual(sum(row['amount_cents'] for row in detail['rows']),r['totals']['amount'])
            self.assertEqual(detail['total_records'],r['totals']['records'])
    def test_metadata_and_existing_saved_field_order(self):
        fs=semantics.fields();keys=[f['id'] for f in fs]
        expected=[k for k in data.DIMENSIONS if k not in data.DESTINATIONS]+list(data.MEASURES)
        self.assertEqual(keys[:len(expected)],expected)
        self.assertEqual(keys[-len(data.DESTINATIONS):],list(data.DESTINATIONS))
        self.assertEqual(len(keys),len(set(keys)))
        byid={f['id']:f for f in fs}
        self.assertEqual(byid['zip5']['data_type'],'string');self.assertEqual(byid['zip5']['role'],'dimension')
        self.assertEqual(byid['review_required']['data_type'],'boolean')
        self.assertEqual(byid['amount']['unit'],'cents');self.assertEqual(byid['first_date']['unit'],'date')
        self.assertTrue(all(f['description'] for f in fs))
    def test_invalid_width_or_field_cannot_enter_sql(self):
        for width in [0,-1,1.5,True,'100',10**20]:
            with self.assertRaises(ValueError):semantics.validate({'bin_width_cents':width})
        with self.assertRaises(ValueError):self.result(dimensions=['source_id); DROP TABLE facts'])
    def test_source_index_bytes_unchanged(self):
        before=data._hash(self.path)
        self.result(dimensions=['source_id','month','raw_zip'],measures=['q1_amount','committees'])
        self.assertEqual(data._hash(self.path),before)
    def test_reported_metadata_relationship_never_multiplies_money(self):
        source=Path(self.tmp.name)/'source.sqlite';self.source['database']=source
        with closing(sqlite3.connect(source)) as db:
            db.execute('CREATE TABLE contributions(sub_id TEXT,industry TEXT,filing_form TEXT,memo_code TEXT)')
            db.executemany('INSERT INTO contributions VALUES (?,?,?,?)',[('a','Example','F3','Y'),('a','Example','F3','N'),('b','Example','F3X',None),('c','Another industry','F1','X')])
            db.commit()
        before=data._hash(self.path)
        r=self.result(dimensions=['reported_filing_form','reported_memo_code'],measures=['amount','records'])
        self.assertEqual(r['totals']['amount'],25000);self.assertEqual(sum(x['records'] for x in r['rows']),3)
        self.assertEqual(sum(x['amount'] for x in r['rows']),25000)
        a=next(x for x in r['rows'] if x['reported_filing_form']=='F3')
        self.assertEqual(set(json.loads(a['reported_memo_code'])),{'Y','N'})
        self.assertEqual(next(x['amount'] for x in r['rows'] if x['reported_filing_form']==''),-5000)
        filters=[{'field':'reported_filing_form','op':'eq','value':'F3'}]
        self.assertEqual(self.result(dimensions=['company'],filters=filters)['totals']['amount'],10000)
        with patch.object(data,'_source',return_value=self.source),patch.object(data,'_ready',return_value=(self.path,None)),patch.object(data,'_token',return_value='fixture'),patch.object(data,'_provenance',return_value={}):
            detail=query.records(self.path.parent,{'filters':filters,'dimensions':['reported_memo_code']})
        self.assertEqual(detail['total_records'],1);self.assertEqual(set(json.loads(detail['rows'][0]['reported_memo_code'])),{'Y','N'})
        self.assertEqual(data._hash(self.path),before)
    def test_reported_metadata_wal_cannot_reuse_a_cached_result(self):
        source=Path(self.tmp.name)/'source.sqlite';self.source['database']=source
        with closing(sqlite3.connect(source)) as db:db.execute('CREATE TABLE contributions(sub_id TEXT,industry TEXT,filing_form TEXT)')
        self.result(dimensions=['reported_filing_form'])
        Path(str(source)+'-wal').write_bytes(b'active mutation')
        with self.assertRaisesRegex(ValueError,'active writes'):self.result(dimensions=['reported_filing_form'])
    def test_declared_unique_source_key_avoids_redundant_metadata_grouping(self):
        source=Path(self.tmp.name)/'source.sqlite';self.source['database']=source
        with closing(sqlite3.connect(source)) as db:
            db.execute('CREATE TABLE contributions(sub_id TEXT PRIMARY KEY,industry TEXT,filing_form TEXT)')
            db.execute("INSERT INTO contributions VALUES ('a','Example','F3')");db.commit()
        with closing(sqlite3.connect(':memory:',uri=True)) as db:
            relation=semantics.raw_relation(db,self.source,{'reported_filing_form'})
            self.assertNotIn('GROUP BY',relation);self.assertIn('NOT INDEXED',relation)
        result=self.result(dimensions=['reported_filing_form'])
        self.assertEqual(sum(r['amount'] for r in result['rows']),25000)
        self.assertEqual(next(r['amount'] for r in result['rows'] if r['reported_filing_form']=='F3'),10000)

if __name__=='__main__':unittest.main()
