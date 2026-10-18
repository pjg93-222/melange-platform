"""Original analytical products must survive presentation changes unchanged."""
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'fec/surfaces/staffer_live/approved'


def payload(path):
    text = path.read_text(encoding='utf-8')
    return re.search(r'<script id=page-data type=application/json>(.*?)</script>', text, re.S)[1]


class OriginalWorkspaceTests(unittest.TestCase):
    def test_research_engines_and_payloads_are_original(self):
        for view, source in [('champions','lobby-champions.html'),('industry','lobby-network.html'),
                             ('research-bills','lobby-bills.html'),('interactions','lobby-interactions.html'),
                             ('cu-fintech','lobby-screen.html')]:
            with self.subTest(view=view):
                original, built = ROOT/source, OUT/f'original-{view}.html'
                self.assertEqual(payload(original), payload(built))
                scripts = lambda p: re.findall(r'<script>(.*?)</script>', p.read_text(encoding='utf-8'), re.S)
                self.assertEqual(scripts(original),scripts(built))

    def test_champions_uses_sector_boards_not_dossier_action_counts(self):
        original=json.loads(payload(ROOT/'lobby-champions.html'))
        self.assertEqual(original,json.loads((OUT/'data/champions.json').read_text(encoding='utf-8')))
        sectors=original['championsV2']['sectors']
        for sector,counts in {'credit_union':(50,48,25),'bank':(50,62,5),'crypto':(25,0,0),'fintech':(8,0,0)}.items():
            self.assertEqual(tuple(len(sectors[sector][board]) for board in ('champions','mixed','threats')), counts)
        first=sectors['credit_union']['champions'][0]
        self.assertEqual(first['name'],'Daniel Meuser')
        self.assertEqual(first['composite'],2.294056)
        for sector,names in [('credit_union',('Richard Durbin','Roger Marshall')),('bank',('Andy Barr','Pete Sessions'))]:
            threats={r['name'] for r in sectors[sector]['threats']}
            self.assertTrue(set(names)<=threats)
        crowned={r['name'] for s in sectors.values() for r in s['champions']}
        self.assertTrue({'Andy Barr','Pete Sessions'}.isdisjoint(crowned))

    def test_lazy_member_records_match_original_indices(self):
        source=(ROOT/'cu-member-desk.html').read_text(encoding='utf-8')
        core=json.JSONDecoder().raw_decode(source.split('const D=',1)[1])[0]
        support=json.loads((OUT/'data/desk-support.json').read_text(encoding='utf-8'))
        catalog=json.loads((OUT/'data/catalog.json').read_text(encoding='utf-8'))
        self.assertEqual([m['name'] for m in core['members']],[m['name'] for m in catalog['members']])
        self.assertEqual(len(core['members']),len(support['members_heavy']))
        self.assertNotIn('src="/cu-member-desk-profiles.js"',(OUT/'original-desk.html').read_text(encoding='utf-8'))
        for m in catalog['members']:
            full=json.loads((OUT/f"data/member-{m['id']}.json").read_text(encoding='utf-8'))
            self.assertEqual(m['name'],full['name'])
            self.assertEqual(full['bills'],support['members_heavy'][int(m['id'])]['bills'])


if __name__=='__main__':
    unittest.main()
