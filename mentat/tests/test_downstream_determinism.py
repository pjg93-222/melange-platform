"""Paul, 2026-09-24: "Deterministic". The Graph drawer's answer must not depend on the process.

downstream.network, activity and relationships are run on the same saved units in child processes
under different PYTHONHASHSEED values (the seed decides a set's iteration order): the answers must be
byte for byte the same, in the documented order (downstream._stable): units ascending by
(cycle, committee), a set of roots walked smallest first, a route tie won by the smallest root.
The children run the very downstream.py this process imported.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import downstream  # noqa: E402

SEEDS = ('0', '1', '2', '12345', '4294967295')
PACS = [f'C{index:08d}' for index in range(1, 13)]

CHILD = r'''
import importlib.util, json, sys
sys.path.insert(0, sys.argv[2])
spec = importlib.util.spec_from_file_location('downstream', sys.argv[1])
downstream = importlib.util.module_from_spec(spec)
sys.modules['downstream'] = downstream
spec.loader.exec_module(downstream)
PACS = json.loads(sys.argv[3])

def unit(cycle, committee, destinations=()):
    # A transfer to each destination; each PAC also spends for, and gives to, one candidate,
    # under its own spelling of the name, so the label kept depends on the order units are read.
    spends = committee in PACS
    return {'cycle': cycle, 'committee_id': committee, '_source': committee + '-' + cycle, '_sha256': committee,
            'receipts': [{'committee': {'name': committee + ' PAC'}}],
            'disbursements': [{'line_number': '22', 'recipient_committee_id': d, 'disbursement_amount': 100 + i,
                               'sub_id': committee + str(i)} for i, d in enumerate(destinations)]
                             + ([{'line_number': '23', 'recipient_committee_id': 'C99999999', 'candidate_id': 'H6XX00001',
                                  'candidate_name': 'SMITH, JANE via ' + committee, 'disbursement_amount': 5,
                                  'disbursement_type': '24K', 'sub_id': committee + 'k'}] if spends else []),
            'expenditures': [{'candidate_id': 'H6XX00001', 'candidate_name': 'SMITH, JANE (' + committee + ')',
                              'expenditure_amount': 10, 'support_oppose_indicator': 'S', 'sub_id': committee + 'e'}]
                            if spends else [],
            'totals': {'disbursements': 1, 'coverage_start_date': '2025-01-01', 'coverage_end_date': '2026-06-30'}}

units = {('2024', 'ROOT'): unit('2024', 'ROOT', PACS[:4]),
         ('2026', 'ROOT'): unit('2026', 'ROOT', PACS),
         ('2026', 'ALT'): unit('2026', 'ALT', PACS)}
units.update({(cycle, pac): unit(cycle, pac) for cycle in ('2024', '2026') for pac in PACS})
roots = {('2024', 'ROOT'), ('2026', 'ROOT'), ('2026', 'ALT')}   # a set, as graph_data passes its roots
found = downstream.network(roots, units)
rows = downstream.activity(found)
ties = downstream.relationships(rows, roots, found)
labels = {}
for row in rows:   # the label graph_data.grouped keeps: the last one it reads
    labels[row['candidate_id'], row['direction']] = row['candidate']
print(json.dumps({'units': [list(key) for key in found['units']],
                  'edges': found['edges'],
                  'rows': [[r['cycle'], r['committee'], r['candidate_id'], r['direction'], r['candidate'], str(r['amount'])]
                           for r in rows],
                  'labels': [[*key, value] for key, value in labels.items()],
                  'ties': [[t['cycle'], t['committee'], t['candidate_id'], t['direction'], t['route']] for t in ties],
                  'sources': downstream.sources(found), 'missing': found['missing']}))
'''


def answers():
    source = str(Path(downstream.__file__).resolve())
    result = {}
    for seed in SEEDS:
        done = subprocess.run([sys.executable, '-c', CHILD, source, str(APP), json.dumps(PACS)],
                              env={**os.environ, 'PYTHONHASHSEED': seed}, capture_output=True, timeout=300)
        if done.returncode:
            raise AssertionError(f'PYTHONHASHSEED={seed}: ' + done.stderr.decode(errors='replace'))
        result[seed] = done.stdout
    return result


class HashSeedIndependence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.answers = answers()

    def test_same_bytes_under_every_hash_seed(self):
        distinct = {}
        for seed, body in self.answers.items():
            distinct.setdefault(body, []).append(seed)
        self.assertEqual(len(distinct), 1, f'{len(distinct)} different answers by PYTHONHASHSEED: {list(distinct.values())}')

    def test_documented_order(self):
        value = json.loads(self.answers['0'])
        keys = ([('2024', 'ROOT'), ('2026', 'ALT'), ('2026', 'ROOT')]
                + [('2024', p) for p in PACS[:4]] + [('2026', p) for p in PACS])
        self.assertEqual(value['units'], [list(key) for key in sorted(keys)])
        # The smallest root is walked first: 2024 ROOT, then 2026 ALT, then 2026 ROOT.
        self.assertEqual([(e['cycle'], e['source']) for e in value['edges'] if not e.get('terminal_candidate')],
                         [('2024', 'ROOT')] * 4 + [('2026', 'ALT')] * 12 + [('2026', 'ROOT')] * 12)
        # Each candidate keeps the label of the last unit read, in that order.
        self.assertEqual(dict(((k, d), v) for k, d, v in value['labels']),
                         {('H6XX00001', 'Support'): 'SMITH, JANE (C00000012)',
                          ('H6XX00001', 'Contribution / Other'): 'SMITH, JANE via C00000012'})
        # A PAC reached from ALT and ROOT by equally short routes takes the smallest root's route.
        routes = {(t[0], t[1]): t[4] for t in value['ties']}
        self.assertEqual(routes['2026', 'C00000001'], ['ALT', 'C00000001'])
        self.assertEqual(routes['2024', 'C00000001'], ['ROOT', 'C00000001'])

    def test_ordered_roots_keep_their_own_order(self):
        # A list (evidence_geo, the tests) or snapshot_view's dict is walked exactly as before:
        # the stack pops the last root listed first.
        units = {('2026', 'A'): {'cycle': '2026', 'committee_id': 'A', 'receipts': [], 'expenditures': [],
                                 'disbursements': [{'line_number': '22', 'recipient_committee_id': 'X',
                                                    'disbursement_amount': 1}], 'totals': {'disbursements': 1}},
                 ('2024', 'A'): {'cycle': '2024', 'committee_id': 'A', 'receipts': [], 'expenditures': [],
                                 'disbursements': [{'line_number': '22', 'recipient_committee_id': 'Y',
                                                    'disbursement_amount': 1}], 'totals': {'disbursements': 1}}}
        found = downstream.network([('2024', 'A'), ('2026', 'A')], units)
        self.assertEqual([e['cycle'] for e in found['edges']], ['2026', '2024'])
        found = downstream.network(dict.fromkeys([('2026', 'A'), ('2024', 'A')]), units)
        self.assertEqual([e['cycle'] for e in found['edges']], ['2024', '2026'])


if __name__ == '__main__':
    unittest.main()
