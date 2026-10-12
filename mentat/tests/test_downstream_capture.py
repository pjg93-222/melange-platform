import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pac_evidence import collect_network


class CaptureTests(unittest.TestCase):
    def unit(self, committee, cycle):
        routes = {'A':['B','C'],'B':['D'],'C':['D'],'D':['A','E'],'E':['F']}
        return {'committee_id':committee,'cycle':cycle,'disbursements':[
            {'recipient_committee_id':dest,'line_number':'22','disbursement_amount':10}
            for dest in routes.get(committee,[])] + [
            {'recipient_committee_id':'PCC','candidate_id':'H0KY06104','line_number':'23','disbursement_amount':3},
            {'recipient_committee_id':'VENDOR','line_number':'21B','disbursement_amount':90}]}

    def test_recursive_capture_is_unique_cycle_scoped_and_candidate_terminal(self):
        with patch('pac_evidence.collect_committee',side_effect=lambda client,c,y:self.unit(c,y)) as collect:
            units,missing=collect_network(None,[('A',2024),('A',2026)],max_units=20)
        self.assertEqual(len(units),12)
        self.assertEqual(collect.call_count,12)
        self.assertEqual(missing,[])
        self.assertEqual({r['committee_id'] for r in units},set('ABCDEF'))

    def test_budget_discloses_each_missing_cycle_instead_of_zero_spending(self):
        with patch('pac_evidence.collect_committee',side_effect=lambda client,c,y:self.unit(c,y)):
            units,missing=collect_network(None,[('A',2024),('A',2026)],max_units=2)
        self.assertEqual(len(units),2)
        self.assertEqual(len(missing),4)
        self.assertEqual({r['cycle'] for r in missing},{2024,2026})
        self.assertTrue(all(r['status']=='collection_limit' for r in missing))

if __name__ == '__main__':
    unittest.main()
