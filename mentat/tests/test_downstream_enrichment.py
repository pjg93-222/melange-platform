from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from enrich_downstream import enrich


class EnrichmentTests(unittest.TestCase):
    def payload(self):
        # A CURRENT index by default: these tests exercise the allocation itself, which
        # a cycle without a current index no longer publishes (transfer index status, F2).
        return {'metadata':{'industry':'Fixture','transfer_coverage':[{'cycle':2024,'status':'indexed','counts':{}}]},
            'cycles':[{'cycle':2024}],'raw_rows':[],'committee_map':[],
            'downstream_activity':[],'candidate_committee_links':[]}

    def test_missing_candidate_counterpart_warns_and_preserves_unresolved_share(self):
        payload=self.payload()
        payload['metadata']['transfer_coverage']=[dict(cycle=2024,status='indexed',counts={})]
        payload['raw_rows']=[dict(cycle=2024,committee_id='PAC',amount=100,include_in_analysis='Yes',receipt_role='contribution')]
        payload['candidate_committee_links']=[dict(cycle=2024,committee_id='CMTE',candidate_id='H1',designation='P')]
        payload['transfer_edges']=[dict(cycle=2024,source='PAC',destination='CMTE',amount=90)]
        payload['downstream_activity']=[dict(cycle=2024,source_committee_id='PAC',candidate_id='H2',support_oppose='Oppose',preliminary_included_amount=10)]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertEqual(result['attribution_summary'][0]['estimated_opposition'],10)
        self.assertEqual(result['allocation_coverage'][0]['unresolved'],90)
        self.assertFalse(next(r for r in result['quality_checks'] if r['id']=='allocation_coverage')['passed'])

    def test_a_stale_or_malformed_source_still_blocks_the_whole_cycle(self):
        # Blocked now means NOTHING transfer-derived is published for the cycle, not
        # an unresolved share and 0.00 estimates (Paul's ruling 2026-09-25, F2).
        for source,status in ((dict(status='stale_transfer_index'),'stale'),
                              (dict(status='indexed',counts={'malformed_rows':2},unresolved_all_sources=True),'unresolved')):
            with self.subTest(source=source):
                payload=self.payload()
                payload['metadata']['transfer_coverage']=[dict(source,cycle=2024)]
                payload['raw_rows']=[dict(cycle=2024,committee_id='PAC',amount=100,include_in_analysis='Yes')]
                payload['downstream_activity']=[dict(cycle=2024,source_committee_id='PAC',candidate_id='H1',support_oppose='Oppose',preliminary_included_amount=10)]
                with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
                self.assertEqual(result['allocation_estimates'],[])
                self.assertEqual(result['allocation_coverage'],[])
                self.assertEqual(result['transfer_paths'],[])
                self.assertEqual(result['metadata']['transfer_index_status'],{'2024':status})
                check=next(r for r in result['quality_checks'] if r['id']=='transfer_index_2024')
                self.assertEqual((check['status'],check['passed']),(status,False))
                self.assertFalse(any(r['id']=='transfer_source_2024' for r in result['quality_checks']))

    def test_withheld_transfer_rows_take_a_share_and_leave_clean_activity_counted(self):
        # One ambiguous group no longer costs the committee its clean edges.
        payload=self.payload()
        payload['metadata']['transfer_coverage']=[dict(cycle=2024,status='indexed',
            counts={'unresolved_rows':2},unresolved_sources=['PAC'],
            unresolved_source_amounts={'PAC':90})]
        payload['raw_rows']=[dict(cycle=2024,committee_id='PAC',amount=100,include_in_analysis='Yes')]
        payload['downstream_activity']=[dict(cycle=2024,source_committee_id='PAC',candidate_id='H1',support_oppose='Oppose',preliminary_included_amount=10)]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertAlmostEqual(result['allocation_estimates'][0]['estimated_amount'],10)
        coverage=result['allocation_coverage'][0]
        self.assertAlmostEqual(coverage['unresolved_reasons']['unresolved_transfer_records'],90)
        self.assertAlmostEqual(coverage['estimated_candidate_activity']+coverage['unresolved'],100)
        self.assertFalse(next(r for r in result['quality_checks'] if r['id']=='transfer_source_2024')['passed'])

    def test_pas2_withheld_rows_are_a_share_of_the_committees_outflow(self):
        payload=self.payload()
        payload['raw_rows']=[dict(cycle=2024,committee_id='PAC',amount=100,include_in_analysis='Yes')]
        payload['downstream_activity']=[dict(cycle=2024,source_committee_id='PAC',candidate_id='H1',
            support_oppose='Oppose',preliminary_included_amount=10,unresolved_version_rows=2,
            unresolved_version_amount=90)]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertAlmostEqual(result['allocation_estimates'][0]['estimated_amount'],10)
        coverage=result['allocation_coverage'][0]
        self.assertAlmostEqual(coverage['unresolved_reasons']['unresolved_transfer_records'],90)
        self.assertAlmostEqual(coverage['estimated_candidate_activity']+coverage['unresolved'],100)

    def test_an_empty_amount_map_from_r_is_not_an_error(self):
        # jsonlite serializes an empty JSON object as [], not {}.
        payload=self.payload()
        payload['metadata']['transfer_coverage']=[dict(cycle=2024,status='indexed',counts={},
            unresolved_sources=[],unresolved_source_amounts=[])]
        payload['raw_rows']=[dict(cycle=2024,committee_id='PAC',amount=100,include_in_analysis='Yes')]
        payload['downstream_activity']=[dict(cycle=2024,source_committee_id='PAC',candidate_id='H1',support_oppose='Oppose',preliminary_included_amount=10)]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertAlmostEqual(result['allocation_estimates'][0]['estimated_amount'],100)

    def test_ambiguous_candidate_link_preserves_unresolved_funding(self):
        payload=self.payload()
        payload['raw_rows']=[{'cycle':2024,'committee_id':'C1','amount':125,'include_in_analysis':'Yes'}]
        payload['candidate_committee_links']=[{'cycle':2024,'committee_id':'C1','designation':'A','candidate_id':candidate} for candidate in ('H1','H2')]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertEqual(result['attribution_summary'],[])
        coverage=result['allocation_coverage'][0]
        self.assertEqual(coverage['unresolved'],125)
        self.assertEqual(coverage['model_funding'],125)
        self.assertEqual(coverage['estimated_candidate_activity'],0)
        self.assertFalse(result['quality_checks'][0]['passed'])

    def test_opposition_is_not_credited_to_the_target_or_a_rival(self):
        payload=self.payload()
        payload['raw_rows']=[{'cycle':2024,'committee_id':'PAC','amount':100,'include_in_analysis':'Yes'}]
        payload['downstream_activity']=[{'cycle':2024,'source_committee_id':'PAC','candidate_id':'H1','support_oppose':'Oppose','preliminary_included_amount':60},
            {'cycle':2024,'source_committee_id':'PAC','candidate_id':'H2','support_oppose':'Support','preliminary_included_amount':40}]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        rows={row['candidate_id']:row for row in result['attribution_summary']}
        self.assertEqual(rows['H1']['estimated_opposition'],60)
        self.assertEqual(rows['H1']['direct_plus_estimated_non_opposition'],0)
        self.assertEqual(rows['H2']['direct_plus_estimated_non_opposition'],40)

    def test_corporate_and_personal_funding_keep_separate_dated_cohorts(self):
        payload=self.payload()
        payload['raw_rows']=[{'cycle':2024,'committee_id':'PAC','amount':100,'company':'Acme',
            'employee_tier':tier,'date':day,'receipt_role':'contribution','include_in_analysis':'Yes'}
            for tier,day in [('Corporate','2024-12-20'),('Chief','2024-05-01')]]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertEqual(len(result['funding_cohorts']),2)
        by_type={row['funding_type']:row for row in result['funding_cohorts']}
        self.assertEqual(by_type['Corporate']['first_date'],'2024-12-20')
        self.assertEqual(by_type['Chief']['amount'],100)
        self.assertFalse(result['metadata']['company_cash_attribution_available'])

    def test_pac_source_and_pac_receipts_do_not_merge_with_treasury(self):
        payload=self.payload()
        payload['raw_rows']=[{'cycle':2024,'committee_id':'PAC','amount':100,'company':'Acme',
            'employee_tier':'Corporate','date':'2024-05-01','receipt_role':'contribution',
            'include_in_analysis':'Yes','recipient_category':'Company PAC',
            'funding_source_type':source,'funding_direction':'Into company PAC'}
            for source in ('Corporate treasury','PAC / committee')]
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        cohorts=result['funding_cohorts']
        self.assertEqual(len(cohorts),2)
        self.assertEqual({r['funding_source_type'] for r in cohorts},{'Corporate treasury','PAC / committee'})
        self.assertTrue(all(r['funding_direction']=='Into company PAC' and r['amount']==100 for r in cohorts))


if __name__=='__main__':unittest.main()
