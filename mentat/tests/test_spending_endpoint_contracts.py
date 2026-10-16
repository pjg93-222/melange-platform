from copy import deepcopy
from contextlib import closing
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import spending_endpoint_contracts as contracts


def payload(rows,count,*,family='F',page=1,size=2,cursor=None,exact=True):
    result={'results':rows,'pagination':{'count':count,'is_count_exact':exact,'per_page':size,
                                       'pages':(count+size-1)//size}}
    result['pagination'].update({'page':page} if family=='F' else {'last_indexes':cursor})
    return result


class ContractTest(unittest.TestCase):
    def test_queries_use_exact_cycle_and_distinct_pagination_families(self):
        first=contracts.query('F','C00000059',2024,per_page=2)
        self.assertEqual((first['cycle'],first['page'],first['sort']),(2024,1,'expenditure_date'))
        self.assertNotIn('two_year_transaction_period',first)
        second=contracts.query('F','C00000059',2024,cursor={'page':2})
        self.assertEqual(second['page'],2)
        h4=contracts.query('H4','C00000059',2024,cursor={'last_index':'9999999999999999991','last_event_purpose_date':'2024-01-02'})
        self.assertEqual(h4['cycle'],2024)
        self.assertNotIn('page',h4)
        self.assertEqual(h4['sort_hide_null'],'false')
        for kind,cursor in [('F',{'last_index':'1'}),('F',{'page':True}),('H4',{'last_index':'1'}),
                            ('H4',{'last_index':'1','last_event_purpose_date':None})]:
            with self.assertRaises(ValueError):contracts.query(kind,'C00000059',2024,cursor=cursor)

    def test_f_offset_two_pages_preserve_signed_zero_and_large_text_ids(self):
        rows=[{'sub_id':'9999999999999999991','expenditure_amount':Decimal('-12.50')},
              {'sub_id':'9999999999999999992','expenditure_amount':Decimal('0')},
              {'sub_id':'9999999999999999993','expenditure_amount':Decimal('10.25')}]
        first=payload(rows[:2],3);before=deepcopy(first)
        result=contracts.inspect_page('F',contracts.query('F','C00000059',2024,per_page=2),first)
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['next_cursor'],{'page':2})
        self.assertFalse(result['page_end_matches_count'])
        last=contracts.inspect_page('F',contracts.query('F','C00000059',2024,per_page=2,cursor=result['next_cursor']),
            payload(rows[2:],3,page=2),rows_before=2,expected_count=3,seen_ids=[r['sub_id'] for r in rows[:2]])
        self.assertTrue(last['page_end_matches_count'])
        self.assertEqual(first,before)
        self.assertEqual(rows[0]['expenditure_amount'],Decimal('-12.50'))

    def test_h4_seek_uses_complete_cursor_and_retains_unknown_dates(self):
        first=[{'sub_id':'9999999999999999991','disbursement_amount':Decimal('-1.25'),'event_purpose_date':'2024-01-02'}]
        data=payload(first,2,family='H4',size=1,cursor={'last_index':'9999999999999999991','last_event_purpose_date':'2024-01-02'})
        request=contracts.query('H4','C00000059',2024,per_page=1)
        result=contracts.inspect_page('H4',request,data)
        self.assertEqual(result['issues'],[])
        self.assertEqual(contracts.query('H4','C00000059',2024,per_page=1,cursor=result['next_cursor'])['last_index'],first[0]['sub_id'])
        unknown=[{'sub_id':'9999999999999999992','disbursement_amount':Decimal('0'),'event_purpose_date':None}]
        last=contracts.inspect_page('H4',request,payload(unknown,2,family='H4',size=1),rows_before=1,expected_count=2,seen_ids=[first[0]['sub_id']])
        self.assertTrue(last['page_end_matches_count'])
        self.assertIsNone(unknown[0]['event_purpose_date'])
        unsupported=payload(unknown,3,family='H4',size=1,cursor={'last_index':unknown[0]['sub_id'],'last_event_purpose_date':None})
        held=contracts.inspect_page('H4',request,unsupported,rows_before=1,expected_count=3)
        self.assertIn('incomplete_or_unsupported_cursor',held['issues'])
        self.assertFalse(held['page_end_matches_count'])

    def test_empty_actual_probe_shapes_are_only_page_observations(self):
        for family in ('F','H4'):
            request=contracts.query(family,'C00000059',2024,per_page=1)
            result=contracts.inspect_page(family,request,payload([],0,family=family,size=1))
            self.assertEqual(result['issues'],[])
            self.assertTrue(result['page_end_matches_count'])
            self.assertNotIn('coverage_complete',result)

    def test_estimated_or_missing_count_does_not_end_retrieval_with_valid_progress(self):
        row={'sub_id':'9999999999999999991','disbursement_amount':Decimal('0')}
        cursor={'last_index':row['sub_id'],'last_event_purpose_date':'2024-01-02'}
        seek_request=contracts.query('H4','C00000059',2024,per_page=1)
        estimated=payload([row],1,family='H4',size=1,cursor=cursor,exact=False)
        result=contracts.inspect_page('H4',seek_request,estimated)
        self.assertEqual(result['next_cursor'],cursor)
        self.assertFalse(result['page_end_matches_count'])
        missing=deepcopy(estimated);missing['pagination'].pop('count')
        missing['pagination']['is_count_exact']=True
        result=contracts.inspect_page('H4',seek_request,missing)
        self.assertEqual(result['next_cursor'],cursor)
        self.assertIn('invalid_count',result['issues'])
        exact_duplicate=payload([row],2,family='H4',size=1,cursor=cursor)
        result=contracts.inspect_page('H4',seek_request,exact_duplicate,rows_before=1,seen_ids=[row['sub_id']])
        self.assertEqual(result['next_cursor'],cursor)
        self.assertFalse(result['page_end_matches_count'])
        offset_request=contracts.query('F','C00000059',2024,per_page=1)
        estimated=payload([row],1,size=1,exact=False)
        result=contracts.inspect_page('F',offset_request,estimated)
        self.assertEqual(result['next_cursor'],{'page':2})
        self.assertFalse(result['page_end_matches_count'])
        missing=deepcopy(estimated);missing['pagination'].pop('count')
        result=contracts.inspect_page('F',offset_request,missing)
        self.assertEqual(result['next_cursor'],{'page':2})
        self.assertFalse(result['page_end_matches_count'])

    def test_h4_matched_exact_count_requires_supported_cursor_shape(self):
        row={'sub_id':'9999999999999999991','disbursement_amount':Decimal('-1.25'),'event_purpose_date':None}
        request=contracts.query('H4','C00000059',2024,per_page=1)
        for cursor in ([],{'last_index':'19'},{'last_index':True,'last_event_purpose_date':'2024-01-02'}):
            with self.subTest(cursor=cursor):
                result=contracts.inspect_page('H4',request,payload([row],1,family='H4',size=1,cursor=cursor))
                self.assertIn('incomplete_or_unsupported_cursor',result['issues'])
                self.assertFalse(result['page_end_matches_count'])
        for cursor in (None,{}, {'last_index':row['sub_id'],'last_event_purpose_date':None}):
            with self.subTest(final_nullable=cursor):
                result=contracts.inspect_page('H4',request,payload([row],1,family='H4',size=1,cursor=cursor))
                self.assertTrue(result['page_end_matches_count'])
        self.assertIsNone(row['event_purpose_date'])

    def test_h4_exact_final_page_with_pinned_null_sort_cursor_completes(self):
        # Pinned _get_index_values pops the NULL sort key and emits {last_index, sort_null_only: True}.
        known={'sub_id':'9999999999999999991','disbursement_amount':Decimal('1.00'),'event_purpose_date':'2024-01-02'}
        row={'sub_id':'9999999999999999992','disbursement_amount':Decimal('-1.25'),'event_purpose_date':None}
        cursor={'last_index':row['sub_id'],'sort_null_only':True}
        request=contracts.query('H4','C00000059',2024,per_page=1)
        single=contracts.inspect_page('H4',request,payload([row],1,family='H4',size=1,cursor=cursor))
        self.assertEqual((single['issues'],single['next_cursor'],single['page_end_matches_count']),([],{},True))
        last=contracts.inspect_page('H4',request,payload([row],2,family='H4',size=1,cursor=cursor),
                                    rows_before=1,expected_count=2,seen_ids=[known['sub_id']])
        self.assertEqual((last['issues'],last['next_cursor'],last['page_end_matches_count']),([],{},True))

    def test_h4_null_sort_cursor_mid_stream_is_never_followed(self):
        row={'sub_id':'9999999999999999991','disbursement_amount':Decimal('1.00'),'event_purpose_date':None}
        cursor={'last_index':row['sub_id'],'sort_null_only':True}
        request=contracts.query('H4','C00000059',2024,per_page=1)
        with self.assertRaises(ValueError):contracts.query('H4','C00000059',2024,per_page=1,cursor=cursor)
        for count,before in ((2,0),(3,1)):
            with self.subTest(count=count):
                result=contracts.inspect_page('H4',request,payload([row],count,family='H4',size=1,cursor=cursor),rows_before=before)
                self.assertIn('incomplete_or_unsupported_cursor',result['issues'])
                self.assertEqual(result['next_cursor'],{})
                self.assertFalse(result['page_end_matches_count'])

    def test_h4_null_sort_cursor_without_an_exact_matching_count_does_not_certify(self):
        row={'sub_id':'9999999999999999991','disbursement_amount':Decimal('1.00'),'event_purpose_date':None}
        cursor={'last_index':row['sub_id'],'sort_null_only':True}
        request=contracts.query('H4','C00000059',2024,per_page=1)
        estimated=payload([row],1,family='H4',size=1,cursor=cursor,exact=False)
        missing=deepcopy(estimated);missing['pagination'].pop('count');missing['pagination']['is_count_exact']=True
        over=payload([row],1,family='H4',size=1,cursor=cursor)
        for label,data,before in (('estimated',estimated,0),('missing',missing,0),('rows exceed count',over,1)):
            with self.subTest(label):
                result=contracts.inspect_page('H4',request,data,rows_before=before)
                self.assertIn('incomplete_or_unsupported_cursor',result['issues'])
                self.assertEqual(result['next_cursor'],{})
                self.assertFalse(result['page_end_matches_count'])

    def test_h4_null_sort_cursor_only_in_the_exact_pinned_shape_is_tolerated(self):
        row={'sub_id':'9999999999999999991','disbursement_amount':Decimal('1.00'),'event_purpose_date':None}
        request=contracts.query('H4','C00000059',2024,per_page=1)
        index=row['sub_id']
        for cursor in ({'last_index':index,'sort_null_only':False},{'last_index':index,'sort_null_only':1},
                       {'last_index':index,'sort_null_only':'true'},{'sort_null_only':True},
                       {'last_index':True,'sort_null_only':True},{'last_index':'x1','sort_null_only':True},
                       {'last_index':index,'sort_null_only':True,'last_event_purpose_date':None},
                       {'last_index':index,'sort_null_only':True,'sort':'payee_name'}):
            with self.subTest(cursor=cursor):
                result=contracts.inspect_page('H4',request,payload([row],1,family='H4',size=1,cursor=cursor))
                self.assertIn('incomplete_or_unsupported_cursor',result['issues'])
                self.assertFalse(result['page_end_matches_count'])

    def test_false_unknown_changed_and_malformed_metadata_never_prove_page_end(self):
        request=contracts.query('F','C00000059',2024,per_page=2)
        baseline=payload([{'sub_id':'19'}],1)
        cases=[]
        for key,value in [('is_count_exact',False),('is_count_exact',None),('count',True),('count',-1),('page',2),('pages',2),('per_page',True)]:
            item=deepcopy(baseline);item['pagination'][key]=value;cases.append(item)
        cases.extend([payload([{'sub_id':9999999999999999991}],1),payload([{'sub_id':'19'},{'sub_id':'19'}],2)])
        for item in cases:
            with self.subTest(item=item):
                result=contracts.inspect_page('F',request,item)
                self.assertTrue(result['issues']);self.assertFalse(result['page_end_matches_count'])
        changed=contracts.inspect_page('F',request,baseline,expected_count=2)
        self.assertIn('provider_count_changed',changed['issues'])
        repeated=contracts.inspect_page('F',request,baseline,seen_ids=['19'])
        self.assertIn('duplicate_source_id',repeated['issues'])
        short=contracts.inspect_page('F',request,payload([{'sub_id':'19'}],3))
        self.assertIn('numbered_page_length_mismatch',short['issues'])
        wrong_offset=contracts.inspect_page('F',contracts.query('F','C00000059',2024,per_page=2,cursor={'page':2}),
            payload([{'sub_id':'20'}],3,page=2),rows_before=1)
        self.assertIn('numbered_page_offset_mismatch',wrong_offset['issues'])

    def test_candidate_subordinate_and_source_are_not_paid_recipient_fields(self):
        f=contracts.binding('F');h4=contracts.binding('H4')
        self.assertEqual(f['paid_recipient_committee_fields'],())
        self.assertEqual(h4['paid_recipient_committee_fields'],())
        self.assertEqual(f['beneficiary_fields'],('candidate_id',))
        self.assertIn('subordinate_committee_id',f['source_or_reporting_fields'])
        self.assertEqual(len(f['contract_metadata_sha256']),64)

    def test_explicit_clean_activation_preserves_other_statuses_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='D069-inert-') as directory:
            path=Path(directory)/'frontier.sqlite'
            with closing(sqlite3.connect(path)) as db, db:
                db.row_factory=sqlite3.Row
                db.execute('CREATE TABLE endpoints(committee_id,cycle,kind,status,rows_stored,pages_stored,expected_count,request_cursor_json,coverage_issues_json)')
                for kind,status in [('F','unsupported'),('H4','unsupported'),('B','unknown'),('E_regular','complete'),('F','unknown')]:
                    committee='C00000002' if kind=='F' and status=='unknown' else 'C00000059'
                    db.execute('INSERT INTO endpoints VALUES(?,?,?, ?,0,0,NULL,?,?)',(committee,2024,kind,status,'{}','[]'))
                before={tuple(row) for row in db.execute("SELECT * FROM endpoints WHERE kind IN('B','E_regular') OR status='unknown'")}
                rows=[dict(row) for row in db.execute('SELECT * FROM endpoints')]
                self.assertEqual(contracts.activation_plan(rows,[])['changes'],[])
                ids=[contracts.binding(kind)['contract_id'] for kind in ('F','H4')]
                plan=contracts.activation_plan(rows,ids)
                self.assertEqual(len(plan['changes']),2)
                for change in plan['changes']:
                    db.execute("UPDATE endpoints SET status='pending' WHERE committee_id=? AND cycle=? AND kind=? AND status='unsupported'",tuple(change[k] for k in ('committee_id','cycle','kind')))
                self.assertEqual(before,{tuple(row) for row in db.execute("SELECT * FROM endpoints WHERE kind IN('B','E_regular') OR status='unknown'")})
                self.assertEqual(contracts.activation_plan([dict(row) for row in db.execute('SELECT * FROM endpoints')],ids)['changes'],[])

    def test_dirty_unsupported_checkpoint_is_held_without_reset(self):
        row={'committee_id':'C00000059','cycle':2024,'kind':'F','status':'unsupported','rows_stored':1,
             'pages_stored':1,'expected_count':None,'request_cursor_json':'{}','coverage_issues_json':'[]'}
        before=deepcopy(row)
        result=contracts.activation_plan([row],[contracts.binding('F')['contract_id']])
        self.assertEqual(result['changes'],[]);self.assertEqual(len(result['blocked']),1)
        self.assertEqual(row,before)
        row.update(rows_stored=None,pages_stored=0)
        self.assertTrue(contracts.activation_plan([row],[contracts.binding('F')['contract_id']])['blocked'])
        with self.assertRaises(ValueError):contracts.activation_plan([row],['unreviewed'])


if __name__=='__main__':unittest.main(verbosity=2)
