"""Contracts for the adopted diagnostic, without fitting named member seats."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine import primacy_press as PP, per_area_grade as PAG
from engine.primacy_method import BENEFICIARY_W


class ClassifiedPress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.roster = {'A': {'bioguide': 'A000001', 'chamber': 'House'}}

    def write(self, rows):
        path = self.root / PP.LEDGER
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ''.join(json.dumps(r)+'\n' for r in rows).encode()
        path.write_bytes(raw)
        (self.root / PP.RECEIPT).write_text(json.dumps({
            'schema': 'primacy-press-corpus-v1', 'sha256': hashlib.sha256(raw).hexdigest(),
            'class_weights': PP.CLASS_W, 'items': len(rows), 'members': 1,
            'crossposts': 'all', 'third_party': 'included', 'decay': 'none'}))

    def rows(self):
        # Repeated source IDs are valid admitted crossposts; occurrences differ.
        return [dict(id='same', occurrence=i, member='A', bioguide='A000001',
                     effective_class=k, intensity_scaled=str(w), direction=str(d))
                for i, (k,w,d) in enumerate([
                    ('CU_DIRECT',1,1), ('SHARED',2,-1),
                    ('OTHER_INDUSTRY',3,1), ('OFF_TOPIC',4,-1)], 1)]

    def test_all_admitted_mass_is_denominator_and_sign_is_applied_once(self):
        self.write(self.rows())
        p, n, _, _ = PP.load(self.roster, self.root)
        self.assertEqual(n['A'], 4)
        self.assertEqual(p['A'], 0.0)  # (+.1 -.1) / 1.0
        rows = self.rows(); rows[0]['intensity_scaled'] = '2'
        self.write(rows)
        self.assertAlmostEqual(PP.load(self.roster, self.root)[0]['A'], .1/1.1)

    def test_missing_or_tampered_ledger_fails_closed(self):
        with self.assertRaises(FileNotFoundError): PP.load(self.roster, self.root)
        self.write(self.rows())
        with (self.root / PP.LEDGER).open('ab') as h: h.write(b' ')
        with self.assertRaisesRegex(ValueError, 'mismatch'): PP.load(self.roster, self.root)

    def test_bad_identity_class_or_occurrence_fails_closed(self):
        for field, value in [('bioguide','B000001'), ('member','Old display name'), ('effective_class','UNKNOWN'),
                             ('occurrence',2), ('direction','0'), ('intensity_scaled','nan')]:
            with self.subTest(field=field):
                rows=self.rows(); rows[0][field]=value; self.write(rows)
                with self.assertRaises(ValueError): PP.load(self.roster,self.root)


class BeneficiaryGrade(unittest.TestCase):
    def test_opposition_is_not_sign_flipped_and_votes_are_unchanged(self):
        roster={'A':{'bioguide':'A000001','chamber':'House'}}
        area=PAG.CATEGORIES[0]
        acts={('A',area,'H.R.1','119'):{'role':'sponsor','sign':-1},
              ('A',area,'H.R.2','119'):{'role':'sponsor','sign':1},
              ('A',area,'H.R.3','119'):{'role':'sponsor','sign':1}}
        spine={'A000001':[('rep','2025-01-03','2027-01-03')]}
        result=PAG.build(roster,acts,{('H.R.1','119'):.5},spine,
                         {'A000001':{area:2}}, {'A':.9},{'A':4}, ['A'], date(2026,9,20),
                         beneficiary_weights={('H.R.1','119'):BENEFICIARY_W['oppose'],
                                              ('H.R.2','119'):BENEFICIARY_W['shared']})
        # Unknown act contributes zero; opposing act is negative, tensor intact.
        self.assertAlmostEqual(result['legislative']['A'][area], -.4*1.25+.4*.5+.4*2)
        self.assertEqual(result['press']['A'],0.0)  # gate counts items, not mass


if __name__ == '__main__':
    unittest.main()
