# -*- coding: utf-8 -*-
"""Desk postflight contract for district briefs when the roster refresh WITHHELD some (E2E attempt 4 follow-on).

``make_member_desk._district_briefs_for`` withholds the briefs of roster arrivals the ratified corpus predates and drops the
departed.  ``dashboard_integrity.district_brief_errors`` is the postflight half: a withheld member must carry NO brief, every
other member must carry a valid one, and the corpus accounting is anchored to the roster minus the withheld.

FIXTURE ONLY: synthetic members and corpus meta, no data plane.
"""
import os
import sys

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)

import audit.dashboard_integrity as di

PRODUCER = open(os.path.join(FEC, 'audit', 'dashboard_integrity.py'), encoding='utf-8').read()


def brief(words=220):
    return {'p1': 'one', 'p2': 'two', 'p3': 'three', 'words': words, 'figures': []}


def member(name, **kw):
    return dict({'name': name, 'district_brief': brief()}, **kw)


def meta(roster_rows, attached, withheld=(), dropped=(), **over):
    """Corpus meta as the desk producer returns it: the corpus holds attached + dropped rows."""
    n = attached + len(dropped)
    doc = {'schema': 'district-demographic-briefs-v1', 'status': 'RATIFIED_FOR_DASHBOARD',
           'counts': {'roster': n, 'accepted': n, 'missing': 0},
           'coverage': {'roster': roster_rows, 'attached': attached,
                        'withheld': list(withheld), 'dropped': list(dropped)}}
    doc.update(over)
    return doc


ROSTER = ['A', 'B', 'New']


def test_a_withheld_arrival_with_no_brief_is_accepted_with_a_warning():
    members = [member('A'), member('B'), member('New', district_brief=None)]
    errors, warnings, count = di.district_brief_errors(members, meta(3, 2, withheld=['New']), ROSTER)
    assert errors == [] and count == 2
    assert len(warnings) == 1 and 'New' in warnings[0] and 'WITHHELD' in warnings[0]


def test_dropped_departed_rows_count_toward_the_corpus_not_the_desk():
    members = [member('A'), member('B'), member('New', district_brief=None)]
    errors, _, count = di.district_brief_errors(
        members, meta(3, 2, withheld=['New'], dropped=['Old']), ROSTER)
    assert errors == [] and count == 2


def test_a_corpus_that_covers_everyone_needs_no_warning():
    members = [member(name) for name in ROSTER]
    errors, warnings, count = di.district_brief_errors(members, meta(3, 3), ROSTER)
    assert errors == [] and warnings == [] and count == 3


def test_a_withheld_member_must_not_carry_a_brief():
    members = [member('A'), member('B'), member('New')]
    errors, _, _ = di.district_brief_errors(members, meta(3, 2, withheld=['New']), ROSTER)
    assert any('withheld' in e and 'New' in e for e in errors)


def test_a_missing_brief_for_a_member_who_was_not_withheld_is_an_error():
    members = [member('A'), member('B', district_brief=None), member('New', district_brief=None)]
    errors, _, count = di.district_brief_errors(members, meta(3, 2, withheld=['New']), ROSTER)
    assert any('missing or malformed for B' in e for e in errors) and count == 1


def test_withholding_a_name_outside_the_roster_is_an_error():
    members = [member('A'), member('B'), member('New', district_brief=None)]
    errors, _, _ = di.district_brief_errors(members, meta(3, 2, withheld=['New', 'Ghost']), ROSTER)
    assert any('Ghost' in e for e in errors)


def test_a_withheld_count_that_does_not_account_for_the_corpus_is_an_error():
    members = [member('A'), member('B'), member('New', district_brief=None)]
    wrong = meta(3, 2, withheld=['New'])
    wrong['counts'] = {'roster': 3, 'accepted': 3, 'missing': 0}      # corpus claims 3 rows but only 2 were attached
    errors, _, _ = di.district_brief_errors(members, wrong, ROSTER)
    assert 'ratified district brief corpus is absent or incomplete' in errors


def test_corpus_schema_and_status_and_missing_are_still_enforced():
    members = [member(name) for name in ROSTER]
    for over in ({'schema': 'x'}, {'status': 'DRAFT'}):
        errors, _, _ = di.district_brief_errors(members, meta(3, 3, **over), ROSTER)
        assert 'ratified district brief corpus is absent or incomplete' in errors
    bad = meta(3, 3)
    bad['counts']['missing'] = 1
    assert 'ratified district brief corpus is absent or incomplete' in di.district_brief_errors(members, bad, ROSTER)[0]


def test_word_band_and_paragraph_gates_are_unchanged():
    for words, ok in ((199, False), (200, True), (250, True), (251, False)):
        members = [member('A'), member('B', district_brief=brief(words)), member('New')]
        errors, _, _ = di.district_brief_errors(members, meta(3, 3), ROSTER)
        assert (not errors) is ok, words
    empty = [member('A'), member('B', district_brief=dict(brief(), p2='  ')), member('New')]
    assert di.district_brief_errors(empty, meta(3, 3), ROSTER)[0]


def test_postflight_delegates_to_the_helper():
    body = PRODUCER.split('def postflight(', 1)[1]
    assert 'district_brief_errors(members' in body
    assert "district brief evidence fingerprint is missing or malformed" in body
