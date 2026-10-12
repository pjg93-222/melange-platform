"""Synthetic controls for the separate dated PAC dollar experiment."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('pac_under_test',Path(__file__).with_name('pac_nowcast.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def frame():
    return dict(sector='commercial_banks',chamber='House',target=2018,product='m18',
                issue='2018-07-20',cut='2018-06-30',
                history=[dict(cycle=y,final={'D':10000,'R':20000},observed={'D':5000,'R':10000}) for y in (2012,2014,2016)],
                observed={'D':6000,'R':9000})


def presence():
    return [dict(sector=s,cycle=y,chamber=c,first_due_any_raw=f'{y-1}-02-20',
                 party_classification_admissible='True',ledger_reconciled='True',party_blocked_rows='0')
            for s in m.SECTORS for y in m.YEARS for c in m.CHAMBERS]


class PacTests(unittest.TestCase):
    def test_fixed_formulas_by_hand(self):
        expected={'prior_party':(10000,20000),'party_r12_remaining':(11000,19000),
                  'party_pace':(12000,18000),'historical_remainder':(11000,19000),
                  'r12_pace_equal_blend':(11500,18500),'chamber_r12_remaining':(11000,19000)}
        for f,values in expected.items():
            result=m.predict(frame(),f)
            self.assertEqual(tuple(r['prediction_cents'] for r in result),values)
            self.assertTrue(all(r['fallback_reason'] is None for r in result))

    def test_target_truth_and_future_history_refused(self):
        bad=frame();bad['actual_cents']=1000000
        with self.assertRaises(m.DataError):m.predict(bad,'party_pace')
        bad=frame();bad['history'][0]['cycle']=2020
        with self.assertRaises(m.DataError):m.predict(bad,'party_pace')

    def test_input_unchanged_and_total_coherence(self):
        f=frame();original=copy.deepcopy(f)
        result=m.predict(f,'chamber_r12_remaining')
        self.assertEqual(f,original)
        self.assertEqual(sum(r['prediction_cents'] for r in result),15000+0.5*30000)

    def test_zero_completion_valid_remaining_invalid_pace(self):
        f=frame();f['history'][0]['observed']['D']=0
        remainder=m.predict(f,'party_r12_remaining')[0]
        self.assertEqual(remainder['prediction_cents'],16000)
        self.assertIsNone(remainder['fallback_reason'])
        pace=m.predict(f,'party_pace')[0]
        self.assertEqual(pace['prediction_cents'],10000)
        self.assertIn('undefined',pace['fallback_reason'])

    def test_refund_ratio_above_one_is_visible(self):
        f=frame()
        for h in f['history']:h['observed']['D']=12000
        result=m.predict(f,'party_r12_remaining')[0]
        self.assertEqual(result['prediction_cents'],6000)
        self.assertTrue(result['trace']['completion_capped'])

    def test_joint_negative_input_falls_back_both_parties(self):
        f=frame();f['observed']['D']=-1
        rows=m.predict(f,'chamber_r12_remaining')
        self.assertEqual([r['prediction_cents'] for r in rows],[10000,20000])
        self.assertTrue(all(r['fallback_reason'] for r in rows))

    def test_due_and_transaction_cut_are_both_applied(self):
        base=dict(sector='commercial_banks',cycle=2018,chamber='House',party='D',hs='1')
        rows=[dict(base,date='2018-06-30',due_available='2018-07-15',cents='100'),
              dict(base,date='2018-06-29',due_available='2018-08-01',cents='200'),
              dict(base,date='2018-07-01',due_available='2018-07-15',cents='300')]
        panel=m.Panel([dict(r,source_record_order=i) for i,r in enumerate(rows)],presence())
        self.assertEqual(panel.amount('commercial_banks',2018,'House','D',issue='2018-07-20',cut='2018-06-30'),100)
        self.assertEqual(panel.amount('commercial_banks',2018,'House','D'),600)

    def test_missing_presence_and_unclassified_refused_not_zero(self):
        p=presence();p[0]['party_classification_admissible']='False'
        with self.assertRaises(m.DataError):m.Panel([],p)
        p=presence();p[0]['first_due_any_raw']=''
        panel=m.Panel([],p)
        with self.assertRaises(m.DataError):panel.amount(p[0]['sector'],p[0]['cycle'],p[0]['chamber'],'D')

    def test_metrics_do_not_call_average_error_accuracy(self):
        rows=[dict(sector='a',party='D',actual_cents=100,prediction_cents=110,error_cents=10,absolute_error_cents=10,
                   ape=.1,within10=True,within20=True,fallback_reason=None),
              dict(sector='a',party='D',actual_cents=0,prediction_cents=40,error_cents=40,absolute_error_cents=40,
                   ape=None,within10=None,within20=None,fallback_reason='domain')]
        metric=m.metrics(rows)
        self.assertEqual(metric['wape'],.5)
        self.assertEqual(metric['within20_rate'],1)
        self.assertEqual(metric['zero_actual_false_positive_dollars'],.4)
        self.assertEqual(metric['fallback_count'],1)

    def test_initial_policy_and_fixed_tie_order(self):
        self.assertEqual(m.choose({f:[] for f in m.FAMILIES}),'party_r12_remaining')
        rows=[dict(sector=s,party=p,target=2018,chamber='House',product='m18',actual_cents=100,prediction_cents=100,error_cents=0,absolute_error_cents=0,
                 ape=0,within10=True,within20=True,fallback_reason=None) for s in m.SECTORS for p in m.PARTIES]
        self.assertEqual(m.choose({f:rows for f in m.FAMILIES}),'prior_party')
        with self.assertRaises(m.DataError):m.choose({f:rows[:-1] for f in m.FAMILIES})

    def test_selection_truth_excludes_late_due_amount(self):
        rows=[dict(sector='commercial_banks',cycle=2018,chamber='House',party='D',hs='1',
                   date='2018-06-30',due_available=d,cents=str(v)) for d,v in [('2018-07-15',100),('2023-01-31',500)]]
        panel=m.Panel([dict(r,source_record_order=i) for i,r in enumerate(rows)],presence())
        prediction=dict(sector='commercial_banks',target=2018,chamber='House',party='D',prediction_cents=110)
        self.assertEqual(m.score([prediction],panel,truth_issue='2020-07-20')[0]['actual_cents'],100)
        self.assertEqual(m.score([prediction],panel)[0]['actual_cents'],600)

    def test_missing_frame_refuses_entire_evaluation_grid(self):
        p=presence();p[0]['first_due_any_raw']=''
        with self.assertRaises(m.DataError):m.evaluate(m.Panel([],p))


if __name__=='__main__':unittest.main()
