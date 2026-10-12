import csv
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest


FEC=Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0,str(FEC))

from moneyball_core import build_party_map


def write_members(path,rows):
    with path.open('w',encoding='utf-8',newline='') as fh:
        out=csv.DictWriter(fh,fieldnames=['bioguide_id','icpsr','party_code'])
        out.writeheader(); out.writerows(rows)


def run_map(congress_rows):
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); pairs=[]
        for congress,rows in congress_rows:
            path=td/('HS%d_members.csv'%congress)
            write_members(path,rows); pairs.append((congress,path))
        receipt=StringIO()
        with redirect_stdout(receipt):
            party=build_party_map(pairs)
    return party,receipt.getvalue()


class VoteviewPartyMapTests(unittest.TestCase):
    def test_larger_icpsr_last_defeats_first_wins(self):
        party,receipt=run_map([(119,[
            {'bioguide_id':'SWITCHA','icpsr':'22345','party_code':'100'},
            {'bioguide_id':'SWITCHA','icpsr':'92345','party_code':'328'},
        ])])

        self.assertEqual(party['SWITCHA'],'I')
        self.assertIn('bioguide=SWITCHA',receipt)
        self.assertIn("icpsrs=['22345', '92345']",receipt)
        self.assertIn("party_codes=['100', '328']",receipt)
        self.assertIn('chosen_icpsr=92345',receipt)
        self.assertIn('chosen_party_code=328',receipt)
        self.assertIn('rule=largest numeric icpsr',receipt)

    def test_larger_icpsr_first_defeats_last_row_wins(self):
        party,receipt=run_map([(119,[
            {'bioguide_id':'SWITCHB','icpsr':'93456','party_code':'328'},
            {'bioguide_id':'SWITCHB','icpsr':'23456','party_code':'200'},
        ])])

        self.assertEqual(party['SWITCHB'],'I')
        self.assertIn('chosen_icpsr=93456',receipt)
        self.assertIn('chosen_party_code=328',receipt)
        self.assertIn('rule=largest numeric icpsr',receipt)

    def test_single_row_member_is_unchanged(self):
        party,receipt=run_map([(119,[
            {'bioguide_id':'SINGLE1','icpsr':'34567','party_code':'200'},
        ])])

        self.assertEqual(party['SINGLE1'],'R')
        self.assertEqual(receipt,'')

    def test_newer_congress_keeps_precedence(self):
        party,receipt=run_map([
            (119,[{'bioguide_id':'CROSS01','icpsr':'45678','party_code':'100'}]),
            (118,[{'bioguide_id':'CROSS01','icpsr':'45678','party_code':'200'}]),
        ])

        self.assertEqual(party['CROSS01'],'D')
        self.assertEqual(receipt,'')

    def test_identical_icpsrs_different_parties_report_arbitrary_file_order(self):
        party,receipt=run_map([(119,[
            {'bioguide_id':'TIE0001','icpsr':'56789','party_code':'200'},
            {'bioguide_id':'TIE0001','icpsr':'56789','party_code':'100'},
        ])])

        self.assertEqual(party['TIE0001'],'R')
        self.assertIn('chosen_icpsr=56789',receipt)
        self.assertIn('chosen_party_code=200',receipt)
        self.assertIn('rule=identical icpsr - file order, ARBITRARY',receipt)
        self.assertNotIn('rule=largest numeric icpsr',receipt)

    def test_identical_icpsrs_same_party_print_nothing(self):
        party,receipt=run_map([(119,[
            {'bioguide_id':'QUIET01','icpsr':'67890','party_code':'100'},
            {'bioguide_id':'QUIET01','icpsr':'67890','party_code':'100'},
        ])])

        self.assertEqual(party['QUIET01'],'D')
        self.assertEqual(receipt,'')

    def test_non_numeric_icpsr_keeps_file_order_and_reports_rule(self):
        party,receipt=run_map([(119,[
            {'bioguide_id':'TEXT001','icpsr':'not-number','party_code':'200'},
            {'bioguide_id':'TEXT001','icpsr':'78901','party_code':'100'},
        ])])

        self.assertEqual(party['TEXT001'],'R')
        self.assertIn('chosen_icpsr=not-number',receipt)
        self.assertIn('chosen_party_code=200',receipt)
        self.assertIn('rule=file order (non-numeric icpsr)',receipt)


if __name__=='__main__': unittest.main()
