"""contracts/evidence-geo.json v4, asserted against the producer (evidence_geo.geography).

v4 documents the four fields lane B added - transfers_not_itemized and its total
(F6), transfer_rule (F8) and reload_rule (F11) - and the three item 7 of F4
added: each unlocated line's `kind`, `reversals_total` and `share_base` (a
refund is a reversal outside the shares, and shares are of positive located
money). The scenarios below are the ones the v4 examples were generated from,
so each example is compared with what the producer emits now; the negative
controls must be refused for their stated reasons (contracts/README.md).
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import evidence_geo  # noqa: E402
from test_evidence_geo import (CONTRACT, GeoFixture, LaneBFixture, disbursement,  # noqa: E402
                               expenditure, receipt)

V4_FIELDS = ('transfers_not_itemized', 'transfers_not_itemized_total', 'transfer_rule', 'reload_rule',
             'reversals_total', 'share_base')
V4_CONTROLS = ('is not its summary less its itemized rows', 'transfers_not_itemized_total does not add up',
               'is not a row count', "names transaction types other than the transfer index's",
               'reload dollars with no reload rows', 'nets below zero and is not marked a reversal',
               'share_base.places is not the positive located total', 'reversals_total does not add up')


def seat(amount, sub_id, **fields):
    return expenditure(amount, 'H6CA12000', 'S', candidate_office='H', candidate_office_state='CA',
                       candidate_office_district='12', sub_id=sub_id, **fields)


def network_units():
    """A FAIRSHAKE-shaped network: the root reports 65,000 on line 22 and itemizes
    53,000 of it (two typed transfers to saved PACs, one untyped line-22 row to an
    unsaved one); one recipient's Schedule E row was loaded twice by the FEC."""
    root = {'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [], 'expenditures': [],
            'disbursements': [
                disbursement('22', 30000, recipient_committee_id='C00836221', recipient_name='DEFEND AMERICAN JOBS',
                             disbursement_type='24G', sub_id='n-b1'),
                disbursement('22', 21000, recipient_committee_id='C00848440', recipient_name='PROTECT PROGRESS',
                             disbursement_type='24G', sub_id='n-b2'),
                disbursement('22', 2000, recipient_committee_id='C00571703', recipient_name='SENATE LEADERSHIP FUND',
                             sub_id='n-b3')],
            'totals': {'disbursements': 65000.0, 'transfers_to_affiliated_committee': 65000.0,
                       'coverage_end_date': '2026-07-31'}}
    spend = seat(40000, 'n-e1', report_type='M6', transaction_id='SE.1', file_number='5',
                 expenditure_date='2026-03-01', payee_name='MEDIA')
    jobs = {'committee_id': 'C00836221', 'cycle': 2026, 'receipts': [], 'disbursements': [],
            'expenditures': [spend, dict(spend, sub_id='n-e1-reload')],
            'totals': {'disbursements': 40000.0, 'coverage_end_date': '2026-07-31'}}
    progress = {'committee_id': 'C00848440', 'cycle': 2026, 'receipts': [], 'disbursements': [],
                'expenditures': [seat(21000, 'n-e2')],
                'totals': {'disbursements': 21000.0, 'coverage_end_date': '2026-07-31'}}
    return [root, jobs, progress]


def refund_units():
    """A CULAC-shaped refund: a 22Z filed on line 23 at -5,000 (not a transfer by
    type, placed nowhere) beside 1,000 of candidate spending, and - as CULAC 2024's
    OH-02 does - a place that nets below zero: a -2,500 24K refund placed at its
    committee's state (VA)."""
    return [{'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [],
             'expenditures': [seat(1000, 'r-e1')],
             'disbursements': [disbursement('23', -5000, recipient_committee_id='C00493072', recipient_name='DAKOTA PAC',
                                            disbursement_type='22Z', sub_id='r-b1'),
                               disbursement('21B', -2500, recipient_committee_id='C00571703',
                                            recipient_name='SENATE LEADERSHIP FUND', disbursement_type='24K',
                                            sub_id='r-b2')],
             'totals': {'disbursements': -6500.0}}]


def lane_b_payloads():
    """{example name: producer payload} for the two spent scenarios, on LaneBFixture's world."""
    world = LaneBFixture('setUp')
    world.setUp()
    try:
        world.write('v4-network', network_units())
        world.write('v4-refund', refund_units())
        return {'snapshot spent across a saved network: transfers not itemized, the transfer and reload censuses (v4)':
                    world.spend('v4-network'),
                'snapshot spent with a refund: a reversal outside the shares (v4)': world.spend('v4-refund')}
    finally:
        world.doCleanups()


def raised_payloads():
    """{example name: producer payload} for a raised map, on GeoFixture's world."""
    world = GeoFixture('setUp')
    world.setUp()
    try:
        return {'snapshot raised: shares of located and ZIP-placed receipts (v4)':
                    evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', '2024', 'raised',
                                           snapshot_root=world.snapshot_root)}
    finally:
        world.doCleanups()


def cut(value, like):
    """value cut to the shape of like (dict keys like names, recursively; lists
    item by item): an added field is not a difference, a renamed or changed one is."""
    if isinstance(like, dict) and isinstance(value, dict):
        return {key: cut(item, like[key]) for key, item in value.items() if key in like}
    if isinstance(like, list) and isinstance(value, list):
        return [cut(item, like[index]) if index < len(like) else item for index, item in enumerate(value)]
    return value


def projection(payload):
    """What v4 pins: totals, every unlocated line with its kind, and the v4 fields."""
    return {'reported_total': payload['reported_total'], 'located_total': payload['located_total'],
            'unlocated_total': payload['unlocated_total'],
            'unlocated': sorted((line['reason'], line['amount'], line['kind']) for line in payload['unlocated']),
            **{field: payload.get(field) for field in V4_FIELDS}}


class ContractV4(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT.read_text(encoding='utf-8'))
        cls.examples = {example['name']: example['payload'] for example in cls.contract['examples']}
        cls.produced = {**lane_b_payloads(), **raised_payloads()}

    def test_the_contract_is_v4_and_its_rules_name_every_new_field(self):
        # v4 or later: a later version (F12's dating) keeps every v4 field and rule.
        self.assertGreaterEqual(self.contract['version'], 4)
        rules = ' '.join(self.contract['rules'])
        for field in V4_FIELDS + ('kind',):
            self.assertIn(field, rules)

    def test_the_producer_emits_what_each_v4_example_records(self):
        for name, payload in self.produced.items():
            with self.subTest(example=name):
                self.assertIn(name, self.examples)
                self.assertEqual(evidence_geo.contract_problems(payload), [])
                pinned = projection(self.examples[name])
                self.assertEqual(cut(projection(payload), pinned), pinned)

    def test_the_v4_examples_carry_every_new_field_and_validate(self):
        for name, payload in self.examples.items():
            if name.endswith('(v4)'):
                with self.subTest(example=name):
                    self.assertEqual([field for field in V4_FIELDS if field not in payload], [])
                    self.assertEqual(evidence_geo.contract_problems(payload), [])

    def test_the_network_example_discloses_the_gap_and_counts_the_reload_once(self):
        payload = self.produced['snapshot spent across a saved network: transfers not itemized, '
                                'the transfer and reload censuses (v4)']
        self.assertEqual(payload['transfers_not_itemized_total'], 12000)
        self.assertEqual((payload['transfer_rule']['legs_by_line_fallback'],
                          payload['transfer_rule']['amount_by_line_fallback']), (1, 2000))
        self.assertEqual((payload['reload_rule']['reload_rows'], payload['reload_rule']['reload_amount']),
                         (1, 40000))

    def test_a_refund_is_a_reversal_outside_the_shares_and_the_shares_stand(self):
        payload = self.produced['snapshot spent with a refund: a reversal outside the shares (v4)']
        self.assertEqual([(line['amount'], line['kind']) for line in payload['unlocated']], [(-5000, 'reversal')])
        self.assertEqual(payload['reversals_total'], -5000)
        # The base is the positive located spending: the -2,500 place is a
        # reversal with no share, not a reason to withdraw every share.
        self.assertEqual(payload['share_base'], {'places': 1000, 'zips': None})
        self.assertEqual(payload['located_total'], -1500)
        transfers = next(kind for kind in payload['kinds'] if kind['kind'] == 'transfers')
        self.assertEqual([(row['key'], row['amount']) for row in transfers['states']], [('VA', -2500)])

    def test_a_raised_map_names_both_bases(self):
        payload = self.produced['snapshot raised: shares of located and ZIP-placed receipts (v4)']
        self.assertEqual(payload['share_base']['places'], payload['located_total'])
        self.assertEqual(payload['share_base']['zips'],
                         sum(row['amount'] for row in payload['by_zip']) + payload['zip_truncated_amount'])

    def test_every_new_negative_control_is_present_and_refused_for_its_reason(self):
        controls = {control['expect']: control for control in self.contract['negative_controls']}
        for expect in V4_CONTROLS:
            with self.subTest(expect=expect):
                self.assertIn(expect, controls)
                problems = evidence_geo.contract_problems(controls[expect]['payload'])
                self.assertTrue(any(expect in problem for problem in problems), problems)


if __name__ == '__main__':
    unittest.main()
