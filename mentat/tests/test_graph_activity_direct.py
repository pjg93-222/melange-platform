"""Downstream activity is not the industry's giving: the Graph's activity answer carries
the industry's own direct amount beside each candidate's reached amount, and Direct
contributions is the default candidate view everywhere a default is chosen."""
from pathlib import Path
import re
import sys
import tempfile
import unittest
from urllib.parse import parse_qs

import openpyxl

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
from graph_data import graph_view
from graph_fixture import add_graph_sheets
import prewarm

# Telecom 2025-26, attribution_summary.csv `direct`: Hagerty 100, Reed 0. The fixture's
# Candidate One is the direct-giving candidate (90 direct, 810 reached); the Reed stand-in
# is reached by a PAC the industry funded but never gave to directly.
REED = ['2025-2026', 'C00000003', 'S6RI00163', 'REED, JOHN F.', 'D', 'Support', 153994]


class ActivityDirectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'Giving.xlsx'
        book = openpyxl.Workbook()
        add_graph_sheets(book)
        book['Downstream Activity'].append(REED)
        book['Downstream Activity']['A3'] = '101 source rows condensed to 5 committee-candidate-activity groups'
        book.save(self.path)
        book.close()

    def view(self, **filters):
        return graph_view([self.path], filters)

    def test_direct_is_the_default_candidate_view(self):
        value = graph_view([self.path], {})
        self.assertEqual(value['filters']['mode'], 'direct')
        self.assertEqual([(r['id'], r['amount']) for r in value['candidates']], [('H0CA00001', 90)])
        self.assertNotIn('direct', value['candidates'][0], 'a direct row is the direct amount already')
        self.assertEqual(self.view(mode='')['filters']['mode'], 'direct')

    def test_the_candidate_view_select_and_first_load_default_to_direct(self):
        for name in ('index.html', 'templates/view_graph.html'):
            html = (APP / name).read_text(encoding='utf-8')
            select = re.search(r'<select id="graph-mode">(.*?)</select>', html, re.S).group(1)
            options = re.findall(r'<option value="([^"]+)"([^>]*)>', select)
            self.assertEqual([value for value, _ in options], ['direct', 'activity', 'estimates'], name)
            self.assertFalse([value for value, attributes in options if 'selected' in attributes], name)
        for query in (prewarm.graph_first_load_query('Telecom'), prewarm.graph_company_query('Telecom', 'X', '2026')):
            self.assertEqual(parse_qs(query)['mode'], ['direct'])

    def test_activity_rows_carry_direct_beside_reached(self):
        rows = {r['id']: r for r in self.view(mode='activity')['candidates']}
        self.assertEqual({k: (r['amount'], r['direct']) for k, r in rows.items()},
                         {'S6RI00163': (153994, 0), 'H0CA00001': (810, 90)})
        # Nothing but the added field changed on a row.
        self.assertEqual(set(rows['H0CA00001']), {'id', 'label', 'amount', 'party', 'direct'})
        self.assertEqual(rows['S6RI00163']['direct'], 0.0)

    def test_direct_is_the_direct_view_amount_for_the_same_filters(self):
        for filters in ({}, {'company': 'Harborlight Credit Union'}, {'company': 'Civic Horizon Bank', 'tier': 'Employees'},
                        {'funding': 'personal'}, {'funding': 'corporate'}, {'company': 'Harborlight Credit Union', 'tier': 'Chief'}):
            direct = {r['id']: r['amount'] for r in self.view(**filters)['candidates']}
            activity = self.view(mode='activity', **filters)['candidates']
            self.assertTrue(activity, filters)
            for row in activity:
                self.assertEqual(row['direct'], direct.get(row['id'], 0), (filters, row['id']))
        chief = {r['id']: (r['amount'], r['direct']) for r in
                 self.view(mode='activity', company='Harborlight Credit Union', tier='Chief')['candidates']}
        self.assertEqual(chief, {'H0CA00001': (33, 100)})
        corporate = {r['id']: (r['amount'], r['direct']) for r in self.view(mode='activity', funding='corporate')['candidates']}
        self.assertEqual(corporate, {'S6RI00163': (153994, 0), 'H0CA00001': (777, 0)})

    def test_a_category_or_recipient_filter_carries_no_direct_rather_than_a_false_zero(self):
        # Those filters leave the direct view with no candidate rows at all, so a 0
        # would say the industry gave nothing. The reached amounts are unchanged.
        for filters in ({'category': 'Super PAC'}, {'recipient': 'C00000003'}):
            rows = self.view(mode='activity', **filters)['candidates']
            self.assertEqual({r['id']: r['amount'] for r in rows}, {'S6RI00163': 153994, 'H0CA00001': 777}, filters)
            self.assertFalse([r for r in rows if 'direct' in r], filters)

    def test_each_direction_side_carries_the_same_direct_as_its_ranking(self):
        value = self.view(mode='activity', direction='Oppose')
        self.assertEqual([(r['id'], r['amount'], r['direct']) for r in value['candidates']], [('H0CA00001', 999, 90)])
        # The side groups are the single-direction rankings (test_native_scan holds the same line).
        self.assertEqual(value['candidates_by_direction']['Oppose'], value['candidates'])
        sides = value['candidates_by_direction']
        self.assertEqual([(r['id'], r['direct']) for r in sides['Support']], [('S6RI00163', 0), ('H0CA00001', 90)])
        for rows in sides.values():
            for row in rows:
                self.assertIn('direct', row)
        # A narrowed request (the drawer's second hop) has none, on the ranking or on any side.
        narrowed = self.view(mode='activity', recipient='C00000003')
        self.assertTrue(narrowed['candidates_by_direction']['Support'])
        for rows in [narrowed['candidates'], *narrowed['candidates_by_direction'].values()]:
            for row in rows:
                self.assertNotIn('direct', row)


if __name__ == '__main__':
    unittest.main()
