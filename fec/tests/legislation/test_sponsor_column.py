# -*- coding: utf-8 -*-
"""Unit tests for legislation/sponsor_column.py -- the bill-level Sponsor column.

The bill archive carries a bill-level ``Sponsor`` on every row (10,181 of 10,181 in
congress_bills_archive_20260825_115305.xlsx) and the producer never read it, so a named
sponsor who holds no sponsor-role member row contributed nothing to the stance matrix.
Measured cost: Tim Scott x Member Business Lending, +6.24 of a published 10.76
(audits/laundry_list_closeout_20260918/F1/receipts/s3_s4_missing_weight.txt).

Everything here is synthetic.  The rule objects are the producer's own -- bill_rules for
the category/Congress grain and members.member_identity.Resolver for the name -- because
the fix is only correct if it agrees with the gates it feeds; both modules are pure and
are already imported at module level by fec/tests/run_tests.py.  No archive is read, no
producer under fec/legislation/ is imported, and nothing is written.
"""
import os
import sys
import traceback


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import bill_rules as BR  # noqa: E402
from members.member_identity import Resolver  # noqa: E402
from legislation import sponsor_column as SC  # noqa: E402


# A four-member synthetic roster.  Two Scotts make a bare surname ambiguous; 'Mary Absent'
# is the member who never appears in a Sponsor cell.
ROSTER = ['Tim Scott', 'Rick Scott', 'Anna Luna', 'Mary Absent']
RESOLVE = Resolver(ROSTER).resolve

# The archive's own column names, 'All Bills' sheet header order:
#   Section Role Member Name Title Sponsor CosponsorCount Committee LastAction
#   Congress DecayWeight DetailLink bill_summary
BILL_FIELDS = {'Name': 'S.3992', 'Title': 'A fixture measure', 'CosponsorCount': 4,
               'Committee': 'Senate - Banking', 'LastAction': 'Referred to committee',
               'Congress': 118, 'DecayWeight': 0.8,
               'DetailLink': 'https://example.invalid/s3992',
               'bill_summary': 'A fixture summary.', 'ManualDirection': ''}


def row(section, role, member, **changes):
    value = dict(BILL_FIELDS)
    value.update({'Section': section, 'Role': role, 'Member': member,
                  'Sponsor': 'Scott, Tim'})
    value.update(changes)
    return value


def synthesise(rows, environ=None):
    return SC.sponsor_rows_from_column(rows, RESOLVE, BR.canonical_category, BR._congress,
                                       environ={} if environ is None else environ)


MBL = '16 Member Business Lending'
MORTGAGE = '17 Mortgage Lending & Housing'


def test_named_sponsor_with_no_row_in_the_cell_gets_exactly_one_row():
    rows = [row(MBL, 'cosponsor', 'Anna Luna'),
            row(MBL, 'original-cosponsor', 'Mary Absent')]
    extra = synthesise(rows)
    assert len(extra) == 1, extra
    assert extra[0]['Member'] == 'Scott, Tim', extra[0]
    assert BR.canonical_role(extra[0]['Role']) == 'sponsor', extra[0]


def test_one_row_per_cell_no_matter_how_many_member_rows_ride_on_it():
    rows = [row(MBL, 'cosponsor', 'Anna Luna') for _ in range(37)]
    assert len(synthesise(rows)) == 1


def test_sponsor_already_in_the_cell_synthesises_nothing():
    for role in ('sponsor', 'cosponsor', 'original-cosponsor'):
        rows = [row(MBL, role, 'Tim Scott'), row(MBL, 'cosponsor', 'Anna Luna')]
        assert synthesise(rows) == [], role
    # The archive spells members the same way it spells sponsors; resolution, not string
    # equality, is what decides whether the member is already present.
    rows = [row(MBL, 'sponsor', 'Scott, Tim')]
    assert synthesise(rows) == []


def test_sponsor_present_only_under_another_section_gets_the_missing_section_only():
    # The S.762 pattern: a sponsor row exists under other sections but not under the
    # section whose cell the matrix actually sums.
    rows = [row(MORTGAGE, 'sponsor', 'Tim Scott', Name='S.762', Congress=117,
                DecayWeight=0.64),
            row(MBL, 'cosponsor', 'Anna Luna', Name='S.762', Congress=117,
                DecayWeight=0.64)]
    extra = synthesise(rows)
    assert len(extra) == 1, extra
    assert BR.canonical_category(extra[0]['Section']) == 'Member Business Lending', extra[0]
    assert extra[0]['Name'] == 'S.762' and str(extra[0]['Congress']) == '117', extra[0]


def test_sections_that_share_one_canonical_category_are_one_cell():
    rows = [row('16 Member Business Lending', 'cosponsor', 'Anna Luna'),
            row('Member Business Lending', 'cosponsor', 'Mary Absent')]
    assert len(synthesise(rows)) == 1


def test_ambiguous_sponsor_names_resolve_to_nothing():
    rows = [row(MBL, 'cosponsor', 'Anna Luna', Sponsor='Scott')]
    assert RESOLVE('Scott') is None
    assert synthesise(rows) == []


def test_sponsors_outside_the_roster_resolve_to_nothing():
    for cell in ('Armstrong, Kelly', 'Scott, David', 'Nobody, At All'):
        rows = [row(MBL, 'cosponsor', 'Anna Luna', Sponsor=cell)]
        assert RESOLVE(cell) is None, cell
        assert synthesise(rows) == [], cell


def test_empty_sponsor_cells_synthesise_nothing():
    for cell in ('', '   ', None, float('nan'), 'nan', 'None'):
        rows = [row(MBL, 'cosponsor', 'Anna Luna', Sponsor=cell)]
        assert synthesise(rows) == [], repr(cell)


def test_congressgov_party_state_brackets_and_honorifics_are_stripped():
    for cell in ('Scott, Tim [Sen.-R-SC]', 'Sen. Scott, Tim [R-SC]', 'Scott, Tim'):
        rows = [row(MBL, 'cosponsor', 'Anna Luna', Sponsor=cell)]
        extra = synthesise(rows)
        assert len(extra) == 1, (cell, extra)
        assert RESOLVE(extra[0]['Member']) == 'Tim Scott', (cell, extra[0])


def test_rows_whose_section_maps_to_no_category_are_skipped():
    rows = [row('Credit Union-Bank Competitive Dynamics', 'cosponsor', 'Anna Luna'),
            row('', 'cosponsor', 'Mary Absent')]
    assert BR.canonical_category('Credit Union-Bank Competitive Dynamics') is None
    assert synthesise(rows) == []


def test_the_synthesised_row_carries_the_provenance_marker():
    rows = [row(MBL, 'cosponsor', 'Anna Luna')]
    extra = synthesise(rows)
    assert extra[0][SC.SOURCE_COLUMN] == SC.SOURCE, extra[0]
    assert SC.SOURCE == 'archive-sponsor-column'


def test_the_synthesised_row_copies_the_cells_bill_level_fields():
    template = row(MBL, 'cosponsor', 'Anna Luna')
    extra = synthesise([template])
    assert len(extra) == 1
    for field, value in BILL_FIELDS.items():
        assert extra[0][field] == value, field
    assert extra[0]['Section'] == template['Section']
    assert extra[0]['Sponsor'] == template['Sponsor']
    # Only the member, the role and the provenance marker differ from the template.
    assert ({key for key in extra[0] if extra[0][key] != template.get(key)}
            == {'Member', 'Role', SC.SOURCE_COLUMN}), extra[0]


def test_the_dial_defaults_to_the_fix_and_only_0_reverts_it():
    assert SC.DIAL == 'LEG_SPONSOR_FROM_COLUMN'
    assert SC.enabled({}) is True
    assert SC.enabled({SC.DIAL: '1'}) is True
    assert SC.enabled({SC.DIAL: '0'}) is False
    # Strict on the revert side, exactly like legacy_direction_enabled: a value that is
    # not '0' leaves the ratified default in force rather than silently reverting.
    for value in ('', 'no', 'false', '00', '2'):
        assert SC.enabled({SC.DIAL: value}) is True, value


def test_the_dial_off_synthesises_nothing():
    rows = [row(MBL, 'cosponsor', 'Anna Luna')]
    assert len(synthesise(rows, environ={})) == 1
    assert synthesise(rows, environ={SC.DIAL: '0'}) == []


def test_the_helper_never_mutates_the_rows_it_is_given():
    rows = [row(MBL, 'cosponsor', 'Anna Luna')]
    before = [dict(item) for item in rows]
    synthesise(rows)
    assert rows == before
    assert len(rows) == 1


def test_two_cells_of_two_bills_each_get_their_own_row():
    rows = [row(MBL, 'cosponsor', 'Anna Luna', Name='S.3992', Congress=118),
            row(MBL, 'cosponsor', 'Anna Luna', Name='S.3382', Congress=117,
                DecayWeight=0.64),
            row(MBL, 'cosponsor', 'Anna Luna', Name='S.3992', Congress=117)]
    extra = synthesise(rows)
    assert {(item['Name'], BR._congress(item['Congress'])) for item in extra} == {
        ('S.3992', '118'), ('S.3382', '117'), ('S.3992', '117')}, extra


def test_the_same_bill_in_two_congresses_is_two_cells():
    rows = [row(MBL, 'sponsor', 'Tim Scott', Name='S.3992', Congress=118),
            row(MBL, 'cosponsor', 'Anna Luna', Name='S.3992', Congress=117)]
    extra = synthesise(rows)
    assert len(extra) == 1 and BR._congress(extra[0]['Congress']) == '117', extra


def test_congress_is_normalised_the_way_the_producers_event_key_is():
    # read_csv/read_excel hand 117 over as 117.0; the producer keys events on
    # BR._congress, so 117 and 117.0 are one cell here too.
    rows = [row(MBL, 'sponsor', 'Tim Scott', Congress=118.0),
            row(MBL, 'cosponsor', 'Anna Luna', Congress=118)]
    assert synthesise(rows) == []


def test_bill_identity_matches_the_producers_event_key_grain():
    # event_key uses str(Name or '').strip().lower(); ' s.3992 ' is the same bill.
    rows = [row(MBL, 'sponsor', 'Tim Scott', Name=' s.3992 '),
            row(MBL, 'cosponsor', 'Anna Luna', Name='S.3992')]
    assert synthesise(rows) == []
    # Verbatim, including how a missing Name renders: the producer keys it on 'nan', so a
    # cell grouped here can never be a cell the row loop's dedup does not also see.
    for value, expected in (('S.3992', 's.3992'), (' s.3992 ', 's.3992'), (None, ''),
                            ('', ''), (float('nan'), 'nan'), (0, '')):
        assert SC.bill_identity(value) == str(value or '').strip().lower(), value
        assert SC.bill_identity(value) == expected, (value, SC.bill_identity(value))


def test_unresolvable_member_rows_cannot_stand_in_for_the_sponsor():
    # A row whose Member the producer would report as unmatched is not evidence that the
    # sponsor is present: the producer drops it before the cell is ever scored.
    rows = [row(MBL, 'cosponsor', 'Delegate From Guam')]
    assert RESOLVE('Delegate From Guam') is None
    assert len(synthesise(rows)) == 1


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print('PASS %s' % fn.__name__)
        except BaseException:
            failed += 1
            print('FAIL %s' % fn.__name__)
            traceback.print_exc()
    print('test_sponsor_column: %d passed, %d failed' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
