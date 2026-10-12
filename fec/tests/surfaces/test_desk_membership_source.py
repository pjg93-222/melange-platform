# -*- coding: utf-8 -*-
"""Fixture-only contract for the SCRAPEB-1 / DE-016 membership-source dial.

FIXTURE ONLY.  Synthetic rows, no live tree read, no chain step run.

WHY THIS DOES NOT IMPORT ``make_member_desk``: that module executes on import.
Its module scope loads ``dashboard_data.json`` / ``category_stance.json``
(``:148-149``), reads a dozen optional data artifacts, and calls
``validate_live_repository`` (``:181-186``), which raises when the live
repository receipt is absent -- exactly the "import == execution" hazard this
repo bans.  So the pure shaping helper is lifted out of the shipped source with
``ast`` and executed on its own, which tests the real function body while
running none of the module.  ``shape_cumulative_membership_rows`` is written to
have NO free variables (its three constants are default arguments) precisely so
this works.

The default is the validated cumulative source under the 2026-09-20 defect
repair. Real verifier/history/merge cycles are tested in the companion
test_desk_membership_fixed_point.py suite.
"""
from __future__ import annotations

import ast
import os
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESK = os.path.join(FEC, 'surfaces', 'make_member_desk.py')
FUNC = 'shape_cumulative_membership_rows'

# The exact field set ``CRU.merge_collector_roster_updates`` reads off a sidecar
# row (verify_collector_roster_updates.py:198-237), plus the provenance fields
# an accepted row carries (``:568-592``).  A shaped row must be a sidecar row.
SIDECAR_FIELDS = {
    'schema', 'bill', 'congress', 'repository_key', 'member', 'bioguide',
    'role', 'join_date', 'withdrawn', 'areas', 'title', 'sponsor', 'summary',
    'action', 'ncos', 'url', 'source', 'verified_at', 'repository_sha256',
}

HISTORY_SCHEMA = 'sentinel-verified-membership-history-v1'


def _load_shaper():
    """Compile ONLY the shaping function out of the desk producer's source."""
    source = open(DESK, encoding='utf-8').read()
    tree = ast.parse(source, filename=DESK)
    node = next(
        (n for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name == FUNC),
        None,
    )
    assert node is not None, '%s is not defined at module scope in %s' % (FUNC, DESK)
    module = ast.Module(body=[node], type_ignores=[])
    namespace: dict = {}
    exec(compile(module, DESK, 'exec'), namespace)  # noqa: S102 - one vetted FunctionDef
    return namespace[FUNC], source


SHAPE, DESK_SOURCE = _load_shaper()


def history_row(**overrides) -> dict:
    row = {
        'schema': HISTORY_SCHEMA,
        'membership_key': 'smh1:fixture',
        'repository_key': '119:hr:1234',
        'bill': 'H.R.1234',
        'congress': '119',
        'member': 'Fixture Member',
        'bioguide': 'F000001',
        'role': 'cosponsor',
        'join_date': '2026-08-31',
        'areas': ['Regulatory Relief'],
        'title': 'A fixture act',
        'sponsor': 'Fixture Sponsor',
        'summary': 'A fixture summary.',
        'action': 'Referred to committee.',
        'ncos': 7,
        'url': 'https://example.invalid/hr1234',
        'source': 'congress.gov BILLSTATUS via bill_repository_live',
        'verified_at': '2026-09-06T20:30:09Z',
        'repository_sha256': 'c3af8298',
        'admission': {'kind': 'collector_verifier_delta'},
        'area_admissions': [{'area': 'Regulatory Relief'}],
    }
    row.update(overrides)
    return row


class ShapeCumulativeMembershipRows(unittest.TestCase):

    def test_an_accepted_history_row_becomes_a_sidecar_row(self):
        [shaped] = SHAPE([history_row()])
        self.assertEqual(set(shaped), SIDECAR_FIELDS)
        self.assertEqual(shaped['schema'], 'collector-roster-update-v1')
        self.assertEqual(shaped['bill'], 'H.R.1234')
        self.assertEqual(shaped['congress'], '119')
        self.assertEqual(shaped['member'], 'Fixture Member')
        self.assertEqual(shaped['bioguide'], 'F000001')
        self.assertEqual(shaped['role'], 'cosponsor')
        self.assertEqual(shaped['areas'], ['Regulatory Relief'])
        self.assertEqual(shaped['summary'], 'A fixture summary.')
        self.assertEqual(shaped['ncos'], 7)
        self.assertEqual(shaped['repository_key'], '119:hr:1234')
        self.assertEqual(shaped['verified_at'], '2026-09-06T20:30:09Z')

    def test_withdrawn_is_always_false_on_a_shaped_row(self):
        # History rows carry no `withdrawn` field at all; the merge helper tests
        # it (`:203`), so the shaped row must state it explicitly.
        [shaped] = SHAPE([history_row()])
        self.assertIs(shaped['withdrawn'], False)

    def test_a_withdrawn_row_is_dropped(self):
        self.assertEqual(SHAPE([history_row(withdrawn=True)]), [])

    def test_sponsor_role_requires_explicit_projection(self):
        # The standalone helper keeps the legacy role default. Only the validated
        # cumulative caller projects sponsors, with current official checks at merge.
        self.assertEqual(SHAPE([history_row(role='sponsor')]), [])
        self.assertEqual(SHAPE([history_row(role='SPONSOR')]), [])

    def test_validated_cumulative_projection_can_include_sponsors(self):
        [row] = SHAPE([history_row(role='SPONSOR')],
                      roles=('sponsor', 'original', 'cosponsor'))
        self.assertEqual(row['role'], 'sponsor')
        self.assertEqual(row['bioguide'], 'F000001')

    def test_role_case_and_padding_are_normalised(self):
        [shaped] = SHAPE([history_row(role='  Original  ')])
        self.assertEqual(shaped['role'], 'original')

    def test_a_foreign_schema_is_dropped(self):
        self.assertEqual(SHAPE([history_row(schema='some-other-v9')]), [])
        self.assertEqual(SHAPE([history_row(schema='')]), [])

    def test_non_dict_rows_are_dropped(self):
        self.assertEqual(SHAPE(['', 3, None, [], history_row()]), SHAPE([history_row()]))

    def test_rows_with_no_act_or_no_member_are_dropped(self):
        self.assertEqual(SHAPE([history_row(bill='')]), [])
        self.assertEqual(SHAPE([history_row(congress='')]), [])
        self.assertEqual(SHAPE([history_row(member='   ')]), [])

    def test_dedup_is_on_bill_congress_member_role(self):
        # Two history rows can name one desk act: the history's own key is
        # (repository_key, bioguide, role) while the desk collapses on
        # (bill, congress).  Keeping both would double-count the declared
        # source rows dashboard_integrity.postflight reconciles.
        rows = [
            history_row(repository_key='119:hr:1234'),
            history_row(repository_key='119:hr:1234-v2', bioguide='F000002'),
        ]
        self.assertEqual(len(SHAPE(rows)), 1)

    def test_a_different_role_on_the_same_act_is_kept(self):
        rows = [history_row(role='cosponsor'), history_row(role='original')]
        self.assertEqual(len(SHAPE(rows)), 2)

    def test_a_different_member_on_the_same_act_is_kept(self):
        rows = [history_row(), history_row(member='Other Member', bioguide='F000009')]
        self.assertEqual(len(SHAPE(rows)), 2)

    def test_order_is_deterministic_and_input_order_independent(self):
        rows = [
            history_row(bill='S.987', member='B Member'),
            history_row(bill='H.R.1234', member='A Member'),
            history_row(bill='H.R.1234', member='A Member', role='original'),
        ]
        forward = [(r['bill'], r['congress'], r['member'], r['role'])
                   for r in SHAPE(rows)]
        reverse = [(r['bill'], r['congress'], r['member'], r['role'])
                   for r in SHAPE(list(reversed(rows)))]
        self.assertEqual(forward, reverse)
        self.assertEqual(forward, sorted(forward))

    def test_areas_pass_through_and_a_malformed_areas_field_is_emptied(self):
        [shaped] = SHAPE([history_row(areas=['Payments', 'Regulatory Relief'])])
        self.assertEqual(shaped['areas'], ['Payments', 'Regulatory Relief'])
        [shaped] = SHAPE([history_row(areas='Payments')])
        self.assertEqual(shaped['areas'], [])
        [shaped] = SHAPE([history_row(areas=None)])
        self.assertEqual(shaped['areas'], [])

    def test_history_only_fields_do_not_leak_into_the_sidecar_row(self):
        [shaped] = SHAPE([history_row()])
        for leaked in ('membership_key', 'admission', 'area_admissions'):
            self.assertNotIn(leaked, shaped)

    def test_empty_input_is_empty_output(self):
        self.assertEqual(SHAPE([]), [])


class MembershipSourceDial(unittest.TestCase):
    """Source contract: cumulative is default; delta remains explicit rollback."""

    def test_the_dial_defaults_to_cumulative(self):
        self.assertIn(
            "DESK_MEMBERSHIP_SOURCE = (os.environ.get('DESK_MEMBERSHIP_SOURCE')"
            " or 'cumulative')",
            DESK_SOURCE,
        )

    def test_the_dial_refuses_an_unknown_value(self):
        self.assertIn(
            "if DESK_MEMBERSHIP_SOURCE not in ('delta', 'cumulative'):",
            DESK_SOURCE,
        )

    def test_the_dial_is_read_exactly_once(self):
        self.assertEqual(DESK_SOURCE.count("os.environ.get('DESK_MEMBERSHIP_SOURCE')"), 2)
        self.assertEqual(DESK_SOURCE.count('DESK_MEMBERSHIP_SOURCE = '), 1)

    def test_the_delta_path_still_names_the_sidecar(self):
        self.assertIn("'collector_roster_updates.ndjson'", DESK_SOURCE)
        self.assertIn('CRU.merge_collector_roster_updates(', DESK_SOURCE)

    def test_the_cumulative_path_names_the_step_12_history(self):
        self.assertIn("'sentinel_verified_membership_history.ndjson'", DESK_SOURCE)

    def test_the_merge_still_precedes_the_role_precedence_collapse(self):
        self.assertLess(
            DESK_SOURCE.index('CRU.merge_collector_roster_updates('),
            DESK_SOURCE.index('# collapse duplicate rows'),
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
