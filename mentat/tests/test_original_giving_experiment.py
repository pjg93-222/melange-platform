import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from original_giving_experiment import trial

def row(sid,amount=100,committee='C00000001',file=10,txn=None):
    return dict(sub_id=sid,industry='Fixture',baseline_company='Acme',cycle='2024',committee_id=committee,
                file_num=str(file),transaction_id=txn or sid,contributor_name='PERSON, ONE',
                contribution_receipt_amount=str(amount),contribution_receipt_date='2024-01-01',
                current_receipt_cents=str(amount*100))
def evidence(r,**changes):
    return {**r,'file_number':int(r['file_num']),'filing_form':'F3X','memoed_subtotal':False,
            'contributor_zip':'12345','entity_type':'IND','pdf_url':'https://docquery.fec.gov/fixture',**changes}
def header(file,original=None,order=''):
    return dict(file_num=file,original_file_num=original,amendment_number=order,
                committee_id='C00000001',filing_form='F3X')

class OriginalGivingTests(unittest.TestCase):
    def test_proven_amendment_counts_corrected_amount_once(self):
        a=row('old',100,txn='TX');b=row('new',125,file=11,txn='TX')
        result=trial([a,b],{r['sub_id']:evidence(r) for r in (a,b)}, {'10':header(10),'11':header(11,10,'1')})
        self.assertEqual(result['trial_cents'],12500)
        self.assertEqual(result['changes'][0]['retained_source_id'],'new')

    def test_repeated_id_in_unrelated_reports_is_not_automatically_duplicate(self):
        a=row('a',txn='TX');b=row('b',file=11,txn='TX')
        self.assertEqual(trial([a,b],{r['sub_id']:evidence(r) for r in (a,b)},
                               {'10':header(10),'11':header(11)})['trial_cents'],20000)

    def test_equal_amounts_need_reciprocal_route_and_unique_candidate(self):
        a=row('origin');b=row('destination',committee='C00000002',file=12)
        e={'origin':evidence(a,receipt_type_full='EARMARKED FOR TEST (C00000002)',committee={'name':'EXAMPLE CONDUIT'}),
           'destination':evidence(b)}
        self.assertEqual(trial([a,b],e,{})['trial_cents'],20000)
        e['destination']['memo_text']='EARMARKED THROUGH EXAMPLE CONDUIT'
        self.assertEqual(trial([a,b],e,{})['trial_cents'],10000)
        c=row('second_gift',committee='C00000002',file=12)
        e['second_gift']=evidence(c,memo_text='EARMARKED THROUGH EXAMPLE CONDUIT')
        self.assertEqual(trial([a,b,c],e,{})['trial_cents'],30000)

    def test_route_must_not_remove_same_amount_to_another_recipient(self):
        a=row('a');b=row('b',committee='C00000002');c=row('c',committee='C00000003')
        e={'a':evidence(a,receipt_type_full='EARMARKED FOR TEST (C00000002)',committee={'name':'CONDUIT'}),
           'b':evidence(b,memo_text='EARMARKED FROM CONDUIT'),'c':evidence(c)}
        result=trial([a,b,c],e,{})
        self.assertEqual(result['trial_cents'],20000)
        self.assertEqual(result['counted_cents']['c'],10000)

    def test_employee_pac_inflow_and_pac_outflow_are_separate(self):
        a=row('employee',committee='C00000009');b=row('pac',committee='C00000002')
        e={'employee':evidence(a),'pac':evidence(b,entity_type='PAC',contributor_id='C00000009',contributor={'committee_id':'C00000009'})}
        result=trial([a,b],e,{})
        self.assertEqual(result['trial_cents'],10000)
        self.assertEqual(sum(r['amount_cents'] for r in result['pac_activity']),10000)

    def test_missing_evidence_and_negative_adjustments_are_preserved(self):
        rows=[row('unknown'),row('signed',-25)]
        self.assertEqual(trial(rows,{}, {})['trial_cents'],7500)

    def test_order_invariance_and_conflicting_facts(self):
        a=row('a');b=row('b',file=11)
        e={r['sub_id']:evidence(r) for r in (a,b)}
        self.assertEqual(trial([a,b],e,{})['counted_cents'],trial([b,a],e,{})['counted_cents'])
        with self.assertRaisesRegex(ValueError,'Duplicate'):trial([a,a],e,{})
        e['a']['contribution_receipt_amount']=99
        with self.assertRaisesRegex(ValueError,'amount conflict'):trial([a,b],e,{})

if __name__=='__main__':unittest.main()
