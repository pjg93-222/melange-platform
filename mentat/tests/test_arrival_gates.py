"""F12: the downstream network walk follows dates - earliest arrival.

Paul approved 2026-09-25. gate(root) is its cycle window's first day and all of a
root's activity counts; a transfer X -> Y dated t is traversable only if t >=
gate(X), and gate(Y) is the earliest such t; a reached committee's rows count from
its gate on; back-edges never re-add a root; an undated row counts, flagged
date_unverified (D-040). The helper is tools/arrival_gates.py; downstream.network,
downstream.activity and evidence_geo.network_spent read it.

Run from inside mentat/tests: python -m unittest test_arrival_gates
"""
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import arrival_gates  # noqa: E402
import downstream  # noqa: E402
import evidence_geo  # noqa: E402


def pay(destination, value, date, code='24K'):
    return {'line_number': '22', 'recipient_committee_id': destination, 'recipient_name': destination + ' PAC',
            'disbursement_type': code, 'disbursement_amount': value, 'disbursement_date': date,
            'sub_id': f'{destination}-{value}-{date}'}


def spend(value, date, candidate='H0KY06104', sub=''):
    return {'candidate_id': candidate, 'candidate_name': candidate, 'expenditure_amount': value,
            'support_oppose_indicator': 'S', 'expenditure_date': date, 'sub_id': sub or f'ie-{value}-{date}'}


def unit(committee, payments=(), spends=(), year='2024'):
    rows = list(payments)
    return {'committee_id': committee, 'cycle': year, '_source': 'fixture', 'receipts': [],
            'disbursements': rows, 'expenditures': list(spends),
            'totals': {'disbursements': float(sum(row['disbursement_amount'] for row in rows)
                                              + sum(row['expenditure_amount'] for row in spends))}}


def saved(*units):
    return {(u['cycle'], u['committee_id']): u for u in units}


def placed():
    """Candidate seats are not this file's subject: every candidate lands in KY-06."""
    return (patch.object(evidence_geo.district_geo, 'district_for_candidate', return_value='KY-06'),
            patch.object(evidence_geo.district_geo, 'district_exists', return_value=True))


def spent(units, root='R', notes=None):
    first, second = placed()
    with first, second:
        found = downstream.network([('2024', root)], units)
        return (found, *evidence_geo.network_spent(found, {}, '2024',
                                                     **({'notes': notes} if notes is not None else {})))


def by_committee(rows):
    totals = {}
    for row in rows:
        totals[row['committee']] = totals.get(row['committee'], 0) + row['amount']
    return totals


class HelperTests(unittest.TestCase):
    def test_a_cycle_window_starts_on_the_first_day_of_its_odd_year(self):
        self.assertEqual(arrival_gates.cycle_start('2024'), '2023-01-01')
        self.assertEqual(arrival_gates.cycle_start(2026), '2025-01-01')
        self.assertEqual(arrival_gates.cycle_start('2023-2024'), '2023-01-01')

    def test_a_row_date_must_be_a_real_day(self):
        self.assertEqual(arrival_gates.row_date({'disbursement_date': '2024-02-29T00:00:00'}, 'disbursements'),
                         '2024-02-29')
        self.assertEqual(arrival_gates.row_date({'disbursement_date': '2023-02-29'}, 'disbursements'), '')
        self.assertEqual(arrival_gates.row_date({'expenditure_date': None, 'dissemination_date': '2024-05-01'},
                                                'expenditures'), '2024-05-01')
        self.assertEqual(arrival_gates.row_date({}, 'expenditures'), '')

    def test_gates_fall_to_the_earliest_arrival_and_loops_end(self):
        legs = {('2024', 'R'): [(('2024', 'B'), '2024-05-01', Decimal(10)), (('2024', 'C'), '2024-01-10', Decimal(5))],
                ('2024', 'C'): [(('2024', 'B'), '2024-02-01', Decimal(3))],
                ('2024', 'B'): [(('2024', 'C'), '2024-06-01', Decimal(1)), (('2024', 'R'), '2023-06-01', Decimal(2))]}
        gates = arrival_gates.earliest_arrival({('2024', 'R'): '2023-01-01'}, lambda key: legs.get(key, []))
        self.assertEqual(gates, {('2024', 'R'): '2023-01-01', ('2024', 'C'): '2024-01-10',
                                 ('2024', 'B'): '2024-02-01'})


class DateImpossibleBranch(unittest.TestCase):
    """SLF 2024: $895,000 to Conservative Americans on 2023-12-04, whose only onward
    payment - to America Fund - is dated 2023-02-08. America Fund must not count."""

    def units(self):
        return saved(unit('R', [pay('CA', 895000, '2023-12-04')], [spend(1000, '2024-03-01', 'S4OH00001')]),
                     unit('CA', [pay('AF', 41727.99, '2023-02-08')]),
                     unit('AF', [pay('R', 5000000, '2024-02-01'), pay('X', 4650000, '2024-03-01')],
                          [spend(700000, '2024-04-01')]))

    def test_a_branch_dated_before_the_money_reached_it_is_left_out(self):
        found = downstream.network([('2024', 'R')], self.units())
        self.assertEqual(sorted(key[1] for key in found['keys']), ['CA', 'R'])
        self.assertEqual([(e['source'], e['destination'], e['amount']) for e in found['edges']],
                         [('R', 'CA', 895000.0)])
        self.assertEqual(found['gates'], {('2024', 'CA'): '2023-12-04', ('2024', 'R'): '2023-01-01'})
        self.assertEqual(found['excluded_before_gate'],
                         [{'cycle': '2024', 'source': 'CA', 'destination': 'AF', 'amount': 41727.99, 'rows': 1,
                           'last_date': '2023-02-08', 'gate': '2023-12-04'}])
        self.assertEqual(by_committee(downstream.activity(found)), {'R': Decimal(1000)})

    def test_the_spent_map_counts_none_of_it(self):
        notes = {}
        found, buckets, missing, total = spent(self.units(), notes=notes)
        # R's own 896,000; CA's 41,727.99 predates CA's gate; America Fund is not reached.
        self.assertEqual(total, Decimal('1000'))
        self.assertEqual(buckets['candidates'].total(), 1000)
        rule = notes['date_rule']
        self.assertEqual((rule['rows_before_gate'], rule['amount_before_gate']), (1, 41727.99))
        self.assertEqual(rule['before_gate'], [{'committee': 'CA', 'cycle': '2024', 'committee_name': 'CA',
                                                'gate': '2023-12-04', 'rows': 1, 'amount': 41727.99}])


class Loops(unittest.TestCase):
    """A loop ends and counts each committee's money once; a back-edge never re-adds the root."""

    def units(self):
        return saved(unit('R', [pay('B', 100, '2024-03-01')], [spend(500, '2024-01-15')]),
                     unit('B', [pay('C', 60, '2024-04-01')], [spend(30, '2024-04-10'), spend(7, '2024-02-01')]),
                     unit('C', [pay('B', 25, '2024-05-01'), pay('R', 10, '2024-06-01')], [spend(40, '2024-05-10')]))

    def test_a_loop_counts_every_committee_once_from_its_gate(self):
        found = downstream.network([('2024', 'R')], self.units())
        self.assertEqual(found['gates'], {('2024', 'B'): '2024-03-01', ('2024', 'C'): '2024-04-01',
                                          ('2024', 'R'): '2023-01-01'})
        # B's $7 is dated before R's money reached B; R's own $500 counts once.
        self.assertEqual(by_committee(downstream.activity(found)),
                         {'R': Decimal(500), 'B': Decimal(30), 'C': Decimal(40)})
        self.assertEqual(sorted((e['source'], e['destination']) for e in found['edges']),
                         [('B', 'C'), ('C', 'B'), ('C', 'R'), ('R', 'B')])

    def test_the_spent_map_removes_every_internal_transfer_once(self):
        found, buckets, missing, total = spent(self.units())
        # R 600 + B 97 + C 75 reported; 195 of transfers are internal; B's 7 predates B.
        self.assertEqual(total, Decimal(570))
        self.assertEqual(buckets['candidates'].total(), 570)
        self.assertEqual(missing.total(), 0)


class UndatedRows(unittest.TestCase):
    """D-040: a row that cannot be ordered keeps its dollars and says so."""

    def units(self):
        return saved(unit('R', [pay('B', 100, '2024-03-01')]),
                     unit('B', [pay('D', 20, '')], [spend(40, ''), spend(10, '2024-01-01')]),
                     unit('D', [], [spend(20, '2024-01-05')]))

    def test_an_undated_row_is_counted_and_flagged(self):
        found = downstream.network([('2024', 'R')], self.units())
        # The undated transfer lets money through at B's own gate.
        self.assertEqual(found['gates'][('2024', 'D')], '2024-03-01')
        edge = next(e for e in found['edges'] if e['destination'] == 'D')
        self.assertEqual(edge['date_unverified'], 20.0)
        rows = downstream.activity(found)
        b = next(row for row in rows if row['committee'] == 'B')
        self.assertEqual((b['amount'], b['date_unverified_rows']), (Decimal(40), 1))
        self.assertTrue(b['filings'][0]['date_unverified'])
        self.assertEqual(by_committee(rows), {'B': Decimal(40)})

    def test_the_spent_map_counts_the_undated_dollars_and_flags_them(self):
        notes = {}
        found, buckets, missing, total = spent(self.units(), notes=notes)
        self.assertEqual(total, Decimal(40))
        self.assertEqual((notes['date_rule']['rows_date_unverified'], notes['date_rule']['amount_date_unverified']),
                         (2, 60.0))
        self.assertEqual(notes['date_rule']['amount_before_gate'], 30.0)


class NormalChain(unittest.TestCase):
    """A chain whose money always arrives before it is spent reads exactly as before."""

    def units(self):
        return saved(unit('R', [pay('B', 60, '2024-01-10'), pay('B', 40, '2024-05-01')], [spend(300, '2024-01-02')]),
                     unit('B', [pay('C', 10, '2024-03-01')], [spend(80, '2024-02-01')]),
                     unit('C', [], [spend(10, '2024-04-01')]))

    def test_a_chain_in_date_order_is_unchanged(self):
        found = downstream.network([('2024', 'R')], self.units())
        self.assertEqual([(e['source'], e['destination'], e['amount']) for e in found['edges']],
                         [('R', 'B', 100.0), ('B', 'C', 10.0)])
        self.assertEqual(found['excluded_before_gate'], [])
        self.assertEqual(by_committee(downstream.activity(found)),
                         {'R': Decimal(300), 'B': Decimal(80), 'C': Decimal(10)})
        notes = {}
        found, buckets, missing, total = spent(self.units(), notes=notes)
        self.assertEqual(total, Decimal(390))
        self.assertEqual((notes['date_rule']['rows_before_gate'], notes['date_rule']['rows_date_unverified']), (0, 0))

    def test_a_network_built_without_gates_counts_everything(self):
        rows = downstream.activity({'units': self.units()})
        self.assertEqual(by_committee(rows), {'R': Decimal(300), 'B': Decimal(80), 'C': Decimal(10)})


import json  # noqa: E402

CASES = Path(__file__).resolve().parent / 'fixtures' / 'arrival_gates_cases.json'


def fixture_case(name):
    return next(case for case in json.loads(CASES.read_text(encoding='utf-8'))['cases'] if case['name'] == name)


def fixture_units(case):
    """The shared fixture's saved units as the collector saves them."""
    units = {}
    for number, item in enumerate(case['units']):
        payments = [{'line_number': '22', 'recipient_committee_id': leg['to'], 'recipient_name': leg['to'],
                     'disbursement_type': '24G', 'disbursement_amount': leg['amount'], 'disbursement_date': leg['date'],
                     'sub_id': f'{number}-t{index}'} for index, leg in enumerate(item.get('transfers') or [])]
        spends = [{'candidate_id': 'H0KY06104', 'candidate_name': 'BARR', 'expenditure_amount': row['amount'],
                   'support_oppose_indicator': 'S', 'expenditure_date': row['date'], 'sub_id': f'{number}-s{index}'}
                  for index, row in enumerate(item.get('spending') or [])]
        units[item['cycle'], item['committee']] = unit(item['committee'], payments, spends, item['cycle'])
    return units


def run_case(case, carryover=None):
    root = (case['root']['cycle'], case['root']['committee'])
    mode = carryover or case.get('carryover', 'onward')
    # 'onward' is the default every console caller gets: call it without the keyword.
    found = downstream.network([root], fixture_units(case), **({} if mode == 'onward' else {'carryover': mode}))
    counted = {}
    for row in downstream.activity(found):
        key = f"{row['cycle']}|{row['committee']}"
        counted[key] = counted.get(key, 0) + row['amount']
    return found, {key: value for key, value in counted.items() if value}


class Carryover(unittest.TestCase):
    """Carryover, option (b) (Paul approved 2026-09-25): "a root's transfers in the previous
    cycle also open the recipient's gate in the next cycle." Every case is pinned in
    tests/fixtures/arrival_gates_cases.json, which lane A's R port reads too."""

    def check(self, name, carryover=None):
        case = fixture_case(name)
        found, counted = run_case(case, carryover)
        expect = case['expect']
        gates = {'|'.join(key): [gate, found['gate_sources'][key]] for key, gate in found['gates'].items()}
        for key, value in expect['gates'].items():
            self.assertEqual(gates.get(key), value, f'{name}: {key}')
        for key in expect.get('not_reached', []):
            self.assertNotIn(tuple(key.split('|')), found['keys'], f'{name}: {key}')
        self.assertEqual({key: float(value) for key, value in counted.items()},
                         {key: float(value) for key, value in expect['counted_spending'].items()}, name)
        self.assertEqual(found['carryover']['status'], expect['carryover_status'], name)
        self.assertEqual(['|'.join((unit_['cycle'], unit_['committee'])) for unit_ in found['carryover']['missing_units']],
                         expect['missing_units'], name)
        return found

    def test_carryover_opens_a_gate(self):
        found = self.check('carryover_opens_a_gate')
        opened = found['carryover']['opened']
        self.assertEqual([(line['committee'], line['label'], line['opened_by'][0]['first_date']) for line in opened],
                         [('C90000002', 'gate opened by 2023-2024 transfers', '2024-03-01')])
        # A previous-cycle transfer is disclosed, never a cycle-C flow.
        self.assertTrue(all(edge['cycle'] == '2026' for edge in found['edges']))

    def test_no_carryover_without_a_previous_cycle_transfer(self):
        self.check('no_carryover_without_a_previous_cycle_transfer')

    def test_one_cycle_back_only(self):
        self.check('one_cycle_back_only')

    def test_unknown_when_the_previous_cycle_is_not_saved(self):
        self.check('unknown_when_the_previous_cycle_is_not_saved')

    def test_every_shared_fixture_case(self):
        for case in json.loads(CASES.read_text(encoding='utf-8'))['cases']:
            with self.subTest(case=case['name']):
                self.check(case['name'])

    def test_the_two_variants_differ_only_beyond_the_roots_own_recipients(self):
        case = fixture_case('onward_previous_cycle_transfers_open_gates')
        onward, direct = run_case(case, 'onward'), run_case(case, 'direct')
        self.assertEqual(onward[1], {'2026|C90000002': Decimal(120)})
        self.assertEqual(direct[1], {'2026|C90000002': Decimal(70)})
        self.assertEqual(downstream.network([('2026', 'C90000001')], fixture_units(case), carryover='off')['carryover'],
                         {'rule': 'off', 'opened': [], 'missing_units': [], 'status': 'off'})


from test_evidence_geo import LaneBFixture, disbursement, expenditure  # noqa: E402


class SpentMapPayload(LaneBFixture):
    """The route's own payload: the left-out dollars are out of every total, and said."""

    def test_the_page_says_what_the_date_rule_left_out(self):
        slf = {'committee_id': 'C00571703', 'cycle': 2026, 'receipts': [],
               'disbursements': [disbursement('22', 895000, recipient_committee_id='C00820308', sub_id='s1',
                                              disbursement_type='24K', disbursement_date='2025-12-04')],
               'expenditures': [expenditure(1000, 'H6XX00000', 'S', candidate_office='H', candidate_office_state='CA',
                                            candidate_office_district='12', expenditure_date='2026-03-01')],
               'totals': {'disbursements': 896000.0}}
        conservative = {'committee_id': 'C00820308', 'cycle': 2026, 'receipts': [], 'expenditures': [],
                        'disbursements': [disbursement('22', 41727.99, recipient_committee_id='C00782664', sub_id='c1',
                                                       disbursement_type='24K', disbursement_date='2025-02-08')],
                        'totals': {'disbursements': 41727.99}}
        america = {'committee_id': 'C00782664', 'cycle': 2026, 'receipts': [], 'disbursements': [],
                   'expenditures': [expenditure(700000, 'H6XX00000', 'S', candidate_office='H',
                                                candidate_office_state='CA', candidate_office_district='12',
                                                expenditure_date='2026-04-01')],
                   'totals': {'disbursements': 700000.0}}
        self.write('slf', [slf, conservative, america])
        payload = self.spend('slf', 'C00571703')
        self.assertEqual(payload['reported_total'], 1000)
        self.assertEqual(payload['downstream_committees'], ['C00571703', 'C00820308'])
        self.assertEqual((payload['date_rule']['rows_before_gate'], payload['date_rule']['amount_before_gate']),
                         (1, 41727.99))
        self.assertIn("Date rule: $41,727.99 in 1 rows of reached committees' activity", payload['reason'])
        self.assertEqual(evidence_geo.contract_problems(payload), [])
        self.assertEqual({row['committee']: row.get('gate') for row in payload['downstream_sources']},
                         {'C00571703': '2025-01-01', 'C00820308': '2025-12-04'})

    def test_carried_over_spending_counts_on_the_map_and_is_disclosed(self):
        # FAIRSHAKE 2026: Protect Progress spent in 2025 cash FAIRSHAKE sent it in 2024.
        root = {'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [], 'expenditures': [],
                'disbursements': [disbursement('22', 1000, recipient_committee_id='C00848440', sub_id='f26',
                                               disbursement_type='24G', disbursement_date='2026-02-09')],
                'totals': {'disbursements': 1000.0}}
        before = {'committee_id': 'C00835959', 'cycle': 2024, 'receipts': [], 'expenditures': [],
                  'disbursements': [disbursement('22', 5000, recipient_committee_id='C00848440', sub_id='f24',
                                                 disbursement_type='24G', disbursement_date='2024-02-16')],
                  'totals': {'disbursements': 5000.0}}
        pp_before = {'committee_id': 'C00848440', 'cycle': 2024, 'receipts': [], 'expenditures': [],
                     'disbursements': [], 'totals': {'disbursements': 0.0}}
        pp = {'committee_id': 'C00848440', 'cycle': 2026, 'receipts': [], 'disbursements': [],
              'expenditures': [expenditure(500, 'H6XX00000', 'S', candidate_office='H', candidate_office_state='CA',
                                           candidate_office_district='12', expenditure_date='2025-06-01'),
                               expenditure(300, 'H6XX00001', 'S', candidate_office='H', candidate_office_state='CA',
                                           candidate_office_district='12', expenditure_date='2026-03-01')],
              'totals': {'disbursements': 800.0}}
        self.write('fs', [root, before, pp_before, pp])
        payload = self.spend('fs')
        self.assertEqual(payload['reported_total'], 800)
        carried = payload['date_rule']['carryover']
        self.assertEqual((carried['status'], [line['committee'] for line in carried['opened']]),
                         ('known', ['C00848440']))
        self.assertEqual({gate['committee']: gate['gate_source'] for gate in payload['date_rule']['gates']},
                         {'C00835959': 'root', 'C00848440': 'carryover'})
        self.assertIn("Carryover: the previous cycle's transfers opened the gate of 1 committee(s)", payload['reason'])
        self.assertEqual(evidence_geo.contract_problems(payload), [])


if __name__ == '__main__':
    unittest.main()
