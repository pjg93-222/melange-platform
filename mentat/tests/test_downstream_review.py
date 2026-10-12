from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tools')]
import downstream
from enrich_downstream import enrich
from fec_transfer_graph import allocate
from pac_evidence import collect_network, summarize, temporal_routes


def unit():
    return dict(committee_id='PAC',cycle=2024,receipts=[],disbursements=[],
        expenditures=[dict(sub_id='1',candidate_id='H0KY06104',candidate_name='Barr',
            expenditure_amount=100,support_oppose_indicator='S',is_notice=False,expenditure_date='2024-02-01')],
        totals=dict(disbursements=100),_source='fixture')


class DownstreamReviewTests(unittest.TestCase):
    def test_exact_source_repeat_deduplicates_but_equal_separate_observations_remain(self):
        u=unit();r=u['expenditures'][0]
        u['expenditures'] += [deepcopy(r),dict(r,sub_id='2'),dict(r,sub_id='3',is_notice=True)]
        found=downstream.network([('2024','PAC')],{('2024','PAC'):u})
        self.assertEqual(downstream.activity(found)[0]['amount'],200)
        u['expenditures'].append(dict(r,expenditure_amount=999))
        with self.assertRaisesRegex(ValueError,'one source ID'):
            downstream.activity(found)

    def test_unknown_candidate_dollars_remain_observed_but_cannot_be_allocated_to_person(self):
        u=unit();u['expenditures'][0]['candidate_id']=''
        observed=downstream.activity(downstream.network([('2024','PAC')],{('2024','PAC'):u}))
        self.assertEqual(observed[0]['amount'],100)
        assigned,coverage=allocate({'PAC':100},[],[
            dict(source='PAC',candidate='',direction='Support',amount=60),
            dict(source='PAC',candidate='H1',direction='Support',amount=40)])
        self.assertEqual(assigned[0]['estimated_amount'],40)
        self.assertEqual(coverage[0]['unresolved_reasons']['unidentified_candidate_activity'],60)

    def test_snapshot_only_candidate_activity_is_disclosed_without_fabricated_workbook_zero(self):
        merged,found=downstream.merge_activity([], [('2024','PAC')],{('2024','PAC'):unit()})
        self.assertEqual(len(merged),1)
        self.assertIsNone(found['measurement_comparisons'][0]['workbook_amount'])
        self.assertEqual(found['measurement_comparisons'][0]['snapshot_amount'],100)

    def test_terminal_candidate_row_does_not_hide_separate_pac_route(self):
        u=unit();u['disbursements']=[dict(line_number='23',recipient_committee_id='CHILD',
            candidate_id='H1',disbursement_amount=20),dict(line_number='22',recipient_committee_id='CHILD',disbursement_amount=10)]
        found=downstream.network([('2024','PAC')],{('2024','PAC'):u})
        self.assertIn(('2024','CHILD'),found['missing'])
        self.assertEqual(sorted(r['amount'] for r in found['edges']),[10,20])

    def test_conflicting_committee_units_in_one_file_do_not_choose_first_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'snapshot';folder.mkdir()
            u=unit();other=deepcopy(u);other['totals']['disbursements']=200
            (folder/'snapshots.json').write_text(json.dumps([u,other]))
            with self.assertRaisesRegex(ValueError,'Conflicting committee'):
                downstream.inventory(directory)

    def test_company_refunds_do_not_erase_another_companys_route_evidence(self):
        # A current index: a cycle with no transfer coverage publishes nothing transfer-derived (F2).
        payload=dict(metadata=dict(industry='Fixture',transfer_coverage=[dict(cycle=2024,status='indexed',counts={})]),cycles=[dict(cycle=2024)],
            raw_rows=[dict(cycle=2024,company=company,committee_id='ROOT',amount=value,include_in_analysis='Yes')
                      for company,value in [('Positive',100),('Refund',-200)]],committee_map=[],downstream_activity=[],
            transfer_edges=[dict(cycle=2024,source='ROOT',destination='CHILD',amount=50)])
        with tempfile.TemporaryDirectory() as directory:result=enrich(payload,Path(directory))
        self.assertEqual(result['transfer_paths'][0]['destination'],'CHILD')
        self.assertEqual(result['allocation_coverage'][0]['input_net_funding'],-100)

    def test_failed_descendant_preserves_root_and_consumes_collection_budget(self):
        root=unit();root['disbursements']=[dict(recipient_committee_id=c,line_number='22',disbursement_amount=10) for c in ['B','C']]
        def collect(client,c,y):
            if c=='PAC':return root
            raise ValueError('Incomplete FEC pagination')
        with patch('pac_evidence.collect_committee',side_effect=collect) as capture:
            units,missing=collect_network(None,[('PAC',2024)],max_units=2)
        self.assertEqual(len(units),1);self.assertEqual(capture.call_count,2)
        self.assertEqual([r['status'] for r in missing],['collection_failed','collection_limit'])
        with patch('pac_evidence.collect_committee',side_effect=ValueError('root unavailable')):
            with self.assertRaisesRegex(ValueError,'root unavailable'):collect_network(None,[('PAC',2024)])

    def test_candidate_schedule_b_gifts_are_preserved_with_their_own_direction(self):
        u=unit();u['disbursements']=[dict(sub_id='b1',recipient_committee_id='PCC',candidate_id='H1',
            line_number='23',disbursement_amount=20,disbursement_date='2024-02-01')]
        evidence=summarize([u],'Fixture',[])
        gifts=[r for r in evidence['candidate_activity'] if r['direction']=='Contribution / Other']
        self.assertEqual(gifts[0]['committee_spending'],20)
        self.assertEqual(evidence['committee_finances'][0]['regular_ie_total'],100)

    def test_temporal_search_discloses_depth_limit(self):
        receipt=dict(sub_id='1',company='Fixture',cycle=2024,committee_id='A',date='2024-01-01',
            amount=100,included_company_contribution=True,role='contribution')
        evidence=dict(receipts=[receipt],candidate_activity=[],transfers=[
            dict(cycle=2024,source=a,destination=b,date='2024-02-01',amount=10) for a,b in zip('ABCD','BCDE')])
        self.assertEqual(temporal_routes(evidence)[0]['status'],'depth_limit')

    def test_candidate_payment_without_committee_id_survives_all_measurements(self):
        u=unit();u['disbursements']=[dict(sub_id='b1',candidate_id='H1',line_number='23',
            disbursement_amount=25,disbursement_date='2024-01-02')]
        observed=downstream.activity(downstream.network([('2024','PAC')],{('2024','PAC'):u}))
        self.assertEqual(sum(r['amount'] for r in observed),125)
        evidence=summarize([u],'Fixture',[])
        self.assertEqual(sum(r['committee_spending'] for r in evidence['candidate_activity']),125)
        self.assertEqual(evidence['transfers'],[])
        import evidence_geo
        with patch.object(evidence_geo,'place_candidate') as locate:
            evidence_geo.snapshot_spent(u,evidence,2024)
        self.assertEqual(locate.call_count,2)
        self.assertEqual(locate.call_args.kwargs['direction'],'')

    def test_candidate_payments_are_terminal_in_temporal_routes(self):
        receipt=dict(sub_id='1',company='Fixture',cycle=2024,committee_id='A',date='2024-01-01',
            amount=100,included_company_contribution=True,role='contribution')
        evidence=dict(receipts=[receipt],candidate_activity=[dict(cycle=2024,committee_id='B',
            committee_spending=100,first_date='2024-03-01',last_date='2024-03-01',
            candidate_id_as_filed='H2',candidate_name_as_filed='Other',direction='Support')],
            transfers=[dict(cycle=2024,source='A',destination='B',date='2024-02-01',amount=10,candidate_id='H1')])
        self.assertEqual(temporal_routes(evidence)[0]['status'],'no_dated_route_in_observed_scope')

if __name__=='__main__':unittest.main()
