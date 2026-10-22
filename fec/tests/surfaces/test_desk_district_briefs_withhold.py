# -*- coding: utf-8 -*-
"""Roster-drift contract for the member desk's district briefs (E2E attempt 4: ``missing=3 extras=1``).

FIXTURE ONLY.  ``make_member_desk`` executes on import (see test_desk_membership_source.py), so ``_district_briefs_for``
is lifted out of the shipped source with ``ast`` and run against a synthetic brief corpus and a synthetic roster.json.

The corpus was ratified on the 533 roster.  After the roster refresh (one departure, three arrivals) the desk must
WITHHOLD the arrivals' briefs and DROP the departed member's, and only those; every other mismatch still raises.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from types import SimpleNamespace

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import roster as roster_module

DESK = os.path.join(FEC, 'surfaces', 'make_member_desk.py')
FUNC = '_district_briefs_for'
DESK_SOURCE = open(DESK, encoding='utf-8').read()


def _lift():
    tree = ast.parse(DESK_SOURCE, filename=DESK)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == FUNC)
    return ast.Module(body=[node], type_ignores=[])


CODE = compile(_lift(), DESK, 'exec')

ROWS = [
    {'member': 'A', 'bioguide': 'X000001', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'B', 'bioguide': 'X000002', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'C', 'bioguide': 'X000003', 'chamber': 'Senate', 'term_start': '2021-01-03'},
    {'member': 'New', 'bioguide': 'X000004', 'chamber': 'House', 'term_start': '2026-06-01'},
]
DEPARTED = [{'member': 'Old', 'bioguide': 'X000005', 'departed_on': '2026-08-01'}]


@pytest.fixture()
def root(tmp_path):
    os.makedirs(tmp_path / 'data' / 'engine')
    (tmp_path / 'data' / 'engine' / 'roster.json').write_text(json.dumps(
        {'schema': roster_module.SCHEMA, 'members': ROWS, 'departed': DEPARTED}), encoding='utf-8')
    return str(tmp_path)


def brief(words=220):
    return {'ok': True, 'p1': 'one', 'p2': 'two', 'p3': 'three', 'words': words, 'figures': []}


def document(names, **counts):
    n = len(names)
    return {'schema': 'district-demographic-briefs-v1', 'status': 'RATIFIED_FOR_DASHBOARD',
            'counts': dict({'roster': n, 'accepted': n, 'missing': 0}, **counts),
            'evidence_sha256': 'e' * 64, 'briefs': {name: brief() for name in names}}


def run(root, doc, member_names):
    notices = []
    namespace = {
        'DISTRICT_BRIEFS': doc,
        'roster': SimpleNamespace(drift=lambda current, covered: roster_module.drift(current, covered, root)),
        'print': lambda *a, **k: notices.append(' '.join(str(x) for x in a)),
    }
    exec(CODE, namespace)  # noqa: S102 - one vetted FunctionDef
    return namespace[FUNC](member_names), notices


def test_a_corpus_that_covers_the_roster_attaches_everyone_and_says_nothing(root):
    (kept, meta), notices = run(root, document(['A', 'B', 'C', 'New']), ['A', 'B', 'C', 'New'])
    assert set(kept) == {'A', 'B', 'C', 'New'} and not notices
    assert meta['coverage'] == {'roster': 4, 'attached': 4, 'withheld': [], 'dropped': []}
    assert 'briefs' not in meta and meta['counts'] == {'roster': 4, 'accepted': 4, 'missing': 0}


def test_arrivals_are_withheld_and_departed_members_dropped_without_raising(root):
    (kept, meta), notices = run(root, document(['A', 'B', 'C', 'Old']), ['A', 'B', 'C', 'New'])
    assert set(kept) == {'A', 'B', 'C'}
    assert meta['coverage'] == {'roster': 4, 'attached': 3, 'withheld': ['New'], 'dropped': ['Old']}
    [line] = notices
    assert line.startswith('  !! district briefs: roster drift') and 'WITHHELD' in line and 'DROPPED' in line
    assert 'New' in line and 'Old' in line


def test_a_missing_non_arrival_still_raises(root):
    # C is a continuing member: losing its brief is a corpus defect, not roster drift
    with pytest.raises(RuntimeError, match='district brief roster differs from the member desk: missing=1 extras=0'):
        run(root, document(['A', 'B']), ['A', 'B', 'C'])


def test_an_extra_that_is_not_departed_still_raises(root):
    with pytest.raises(RuntimeError, match='missing=0 extras=1'):
        run(root, document(['A', 'B', 'Stranger']), ['A', 'B'])


def test_a_malformed_row_for_a_present_member_still_raises(root):
    doc = document(['A', 'B', 'Old'])
    doc['briefs']['B'] = brief(words=100)
    with pytest.raises(RuntimeError, match='district brief is incomplete or invalid for B'):
        run(root, doc, ['A', 'B', 'New'])


def test_the_departed_members_row_is_dropped_before_validation(root):
    doc = document(['A', 'B', 'Old'])
    doc['briefs']['Old'] = {'ok': False}
    (kept, _), _ = run(root, doc, ['A', 'B', 'New'])
    assert set(kept) == {'A', 'B'}


@pytest.mark.parametrize('counts', [{'accepted': 2}, {'roster': 2}, {'missing': 1}])
def test_coverage_counts_must_still_describe_the_corpus(root, counts):
    with pytest.raises(RuntimeError, match='district brief coverage counts are not complete'):
        run(root, document(['A', 'B', 'Old'], **counts), ['A', 'B', 'New'])


def test_schema_and_status_gates_are_unchanged(root):
    doc = document(['A', 'B'])
    doc['status'] = 'DRAFT'
    with pytest.raises(RuntimeError, match='not ratified'):
        run(root, doc, ['A', 'B'])


# --- call sites (make_member_desk executes on import, so these are source contracts) -------------------------------

def test_the_bundle_attaches_a_brief_only_where_one_exists_and_reports_coverage():
    assert "'district_brief': {" in DESK_SOURCE            # test_dashboard_adjustments.py asserts this literal
    assert "if name in district_briefs else None" in DESK_SOURCE
    assert "'district_briefs': district_briefs_meta['coverage']" in DESK_SOURCE


def test_what_the_desk_produces_is_what_the_postflight_accepts(root):
    """Producer/postflight contract: _district_briefs_for's output, shaped as the bundle shapes it, passes district_brief_errors."""
    import audit.dashboard_integrity as di
    names = ['A', 'B', 'C', 'New']
    (kept, meta), _ = run(root, document(['A', 'B', 'C', 'Old']), names)
    members = [{'name': name, 'district_brief': {key: kept[name][key] for key in ('p1', 'p2', 'p3', 'words', 'figures')}
                if name in kept else None} for name in names]
    errors, warnings, attached = di.district_brief_errors(members, meta, names)
    assert errors == [] and attached == 3 and len(warnings) == 1
    # and with no drift at all
    (kept, meta), _ = run(root, document(names), names)
    members = [{'name': name, 'district_brief': dict(kept[name])} for name in names]
    assert di.district_brief_errors(members, meta, names) == ([], [], 4)
