"""Optimized query equivalence and deterministic access-path regressions."""
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
import test_contribution_data as legacy


class OptimizedContributionQueryTests(unittest.TestCase):
    setUp=legacy.ContributionQueryTests.setUp

    def result(self,request,optimized=True):
        parts=data._validate(request)
        result=(query._query_ready if optimized else data._query_ready)(self.path,request,self.source,*parts)
        result['provenance'].pop('query_engine_sha256',None)
        result['provenance'].pop('query_projection_sha256',None)
        result['provenance'].pop('field_semantics_sha256',None)
        # Catalog metadata is additive; compare the original numerical/column
        # contract independently from the new semantic-catalog tests.
        for column in result['columns']:
            for key in ['label','data_type','table','discrete','is_time','geographic_role','description','aggregation']:column.pop(key,None)
            if column['role']=='dimension':column['type']='category';column.pop('unit',None)
        if result['basis']=='original_supported':
            result['basis_label']='Supported original giving (gross subset)'
            for column in result['columns']:
                if column['id']=='amount':column['name']='Supported gross amount'
        return result

    def equivalent(self,request):
        self.assertEqual(self.result(request),self.result(request,False))

    def test_every_basis_and_measure_preserves_values_counts_and_units(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET original_cents=7500,pac_cents=3000,zip5='',contributor_key=NULL WHERE source_id='d'")
            db.execute("UPDATE facts SET company='B',cycle='2021-2022',state='NJ',pac_cents=-1000,zip5='' WHERE source_id='c'")
            db.commit()
        for basis in data.BASES:
            for dims in ([],['zip5'],['company','cycle','zip5']):
                for measures in (['amount'],['records','contributors'],list(data.MEASURES)):
                    with self.subTest(basis=basis,dims=dims,measures=measures):
                        self.equivalent({'basis':basis,'dimensions':dims,'measures':measures})

    def test_row_filters_aggregate_filters_and_sort_use_the_same_population(self):
        for basis in data.BASES:
            for filters in ([],[{'field':'amount','op':'gte','value':-5000}],
                            [{'field':'company','op':'in','value':['A','B']}],
                            [{'field':'zip5','op':'contains','value':'123'}]):
                for aggregate in ([],[{'field':'max_amount','op':'gte','value':15000}],
                                  [{'field':'contributors','op':'gte','value':1}]):
                    with self.subTest(basis=basis,filters=filters,aggregate=aggregate):
                        self.equivalent({'basis':basis,'dimensions':['zip5'],'measures':['amount'],
                            'filters':filters,'aggregate_filters':aggregate,'sort':[{'field':'records','direction':'asc'}]})

    def test_pagination_empty_pages_and_empty_populations_retain_global_receipts(self):
        for offset in (0,1,2,100):
            for dims in ([],['zip5']):
                for filters in ([],[{'field':'company','op':'eq','value':'No match'}]):
                    self.equivalent({'dimensions':dims,'limit':1,'offset':offset,'filters':filters})

    def test_invalid_requests_are_rejected_by_both_paths(self):
        cases=[{'basis':'fictional'},{'dimensions':['zip5','zip5']},{'dimensions':['DROP TABLE facts']},
               {'measures':[]},{'limit':0},{'offset':-1},{'filters':None},
               {'filters':[{'field':'amount','op':'gte','value':1.5}]},
               {'aggregate_filters':'bad'},{'aggregate_filters':[{'field':'bad','op':'eq','value':1}]},
               {'sort':[{'field':'median_amount'}]},{'sort':[{'field':'amount','direction':'sideways'}]}]
        for request in cases:
            with self.subTest(request=request):
                for optimized in (False,True):
                    with self.assertRaises(ValueError):self.result(request,optimized)

    def test_public_query_keeps_pending_contract_and_loaded_code_identity(self):
        pending={'status':'preparing','retry_after_ms':1500}
        with patch.object(data,'_source',return_value=self.source),patch.object(data,'_ready',return_value=(None,pending)):
            self.assertIs(query.query(self.path.parent,{'dataset':'Example'}),pending)
        parts=data._validate({})
        result=query._query_ready(self.path,{},self.source,*parts)
        self.assertEqual(result['provenance']['query_engine_sha256'],query.LOADED_SHA256)
        with patch.object(query,'LOADED_STAMP',('old',0,0,0)):
            with self.assertRaisesRegex(ValueError,'Restart the app'):query.query(self.path.parent,{})

    def test_supported_basis_names_its_gross_subset_without_changing_money(self):
        request={'basis':'original_supported'}
        result=query._query_ready(self.path,request,self.source,*data._validate(request))
        self.assertEqual(result['basis_label'],'Supported original giving (gross subset)')
        self.assertEqual(next(c['name'] for c in result['columns'] if c['id']=='amount'),'Supported gross amount')
        self.assertEqual(result['totals']['amount'],10000)

    def test_explicit_retry_reaches_the_existing_index_lifecycle(self):
        pending={'status':'preparing'}
        for retry in (True,False,'true',None):
            with self.subTest(retry=retry):
                with patch.object(data,'_source',return_value=self.source),patch.object(data,'_ready',return_value=(None,pending)) as ready:
                    self.assertIs(query.query(self.path.parent,{'dataset':'Example','retry':retry}),pending)
                    ready.assert_called_once_with(self.source,retry=retry is True)

    def test_amount_only_grouping_is_materialized_once_without_private_distinct(self):
        request={'dimensions':['zip5'],'measures':['amount']};plan=query._plan(request,*data._validate(request))
        self.assertEqual(plan['group_measures'],['amount'])
        self.assertNotIn('COUNT(DISTINCT',plan['grouped_sql'])
        with closing(data._ro(self.path)) as db:
            details=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+plan['grouped_sql'],plan['grouped_params'])]
        self.assertEqual(sum('SCAN facts' in d for d in details),1)
        self.assertFalse(any('facts_zip' in d for d in details))
        self.assertIn('MATERIALIZE grouped',details)
        self.assertIn('MATERIALIZE selected',details)
        request['aggregate_filters']=[{'field':'contributors','op':'gte','value':1}]
        self.assertIn('contributors',query._plan(request,*data._validate(request))['group_measures'])

    def test_selective_zip_filter_keeps_index_access(self):
        request={'dimensions':['company'],'filters':[{'field':'zip5','op':'eq','value':'01234'}]}
        plan=query._plan(request,*data._validate(request))
        with closing(data._ro(self.path)) as db:
            details=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+plan['grouped_sql'],plan['grouped_params'])]
        self.assertTrue(any('SEARCH facts USING INDEX facts_zip' in d for d in details))

    def test_repeated_work_reduction_uses_vm_steps_not_unstable_wall_time(self):
        with closing(sqlite3.connect(self.path)) as db:
            row=dict(zip([r[1] for r in db.execute('PRAGMA table_info(facts)')],db.execute('SELECT * FROM facts LIMIT 1').fetchone()))
            columns=list(row)
            rows=[]
            for i in range(6000):
                value={**row,'source_id':'toy'+str(i),'zip5':str(10000+i%200),'contributor_key':'donor'+str(i%1500)}
                rows.append([value[k] for k in columns])
            db.executemany('INSERT INTO facts ('+','.join('"'+k+'"' for k in columns)+') VALUES ('+','.join('?' for _ in columns)+')',rows);db.commit()
        factory=data._ro;counts={}
        for optimized in (False,True):
            calls=[0]
            def connection(path):
                db=factory(path)
                def tick():calls[0]+=1;return 0
                db.set_progress_handler(tick,100)
                return db
            with patch.object(data,'_ro',side_effect=connection):
                result=self.result({'dimensions':['zip5'],'limit':50},optimized)
            counts[optimized]=calls[0]*100
            if not optimized:reference=result
            else:self.assertEqual(result,reference)
        self.assertLess(counts[True],counts[False]*.8,counts)


if __name__=='__main__':unittest.main()
