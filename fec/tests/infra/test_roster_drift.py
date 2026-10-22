# -*- coding: utf-8 -*-
"""roster.arrivals() / roster.drift(): how an artifact built before a roster refresh may differ from the sitting roster.

Fixture-only: a synthetic roster.json under tmp_path, passed as ``root``.  Nothing reads the live data plane.
"""
import json
import os
import sys

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)

import roster

ROWS = [
    {'member': 'Continuing', 'bioguide': 'C000001', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'Opened With 119th', 'bioguide': 'C000002', 'chamber': 'House', 'term_start': '2025-01-03'},
    {'member': 'No Term Start', 'bioguide': 'C000003', 'chamber': 'Senate'},
    {'member': 'Special Election Arrival', 'bioguide': 'N000001', 'chamber': 'House', 'term_start': '2026-01-10'},
    {'member': 'Flagged Thin', 'bioguide': 'N000002', 'chamber': 'House', 'term_start': '2023-01-03', 'thin_evidence': True},
]
DEPARTED = [{'member': 'Resigned', 'bioguide': 'D000001', 'departed_on': '2026-09-01'}]
CURRENT = [row['member'] for row in ROWS]


@pytest.fixture()
def root(tmp_path):
    os.makedirs(tmp_path / 'data' / 'engine')
    (tmp_path / 'data' / 'engine' / 'roster.json').write_text(json.dumps(
        {'schema': roster.SCHEMA, 'members': ROWS, 'departed': DEPARTED}), encoding='utf-8')
    return str(tmp_path)


def test_arrivals_are_thin_evidence_or_a_term_started_after_the_119th_opened(root):
    # strictly after 2025-01-03: a term that began ON the opening day, or has no start date, is continuing
    assert roster.arrivals(root) == {'Special Election Arrival', 'Flagged Thin'}


def test_missing_arrivals_are_withheld_and_departed_extras_dropped_without_error(root):
    covered = ['Continuing', 'Opened With 119th', 'No Term Start', 'Resigned']
    assert roster.drift(CURRENT, covered, root) == (
        {'Special Election Arrival', 'Flagged Thin'}, {'Resigned'}, set(), set())


def test_a_covered_arrival_is_not_withheld(root):
    covered = CURRENT
    assert roster.drift(CURRENT, covered, root) == (set(), set(), set(), set())


def test_a_missing_continuing_member_is_a_real_mismatch(root):
    covered = ['Continuing', 'No Term Start']
    withheld, dropped, bad_missing, bad_extra = roster.drift(CURRENT, covered, root)
    assert bad_missing == {'Opened With 119th'}
    assert withheld == {'Special Election Arrival', 'Flagged Thin'} and not dropped and not bad_extra


def test_an_extra_name_that_is_not_departed_is_a_real_mismatch(root):
    covered = CURRENT + ['Stranger', 'Resigned']
    withheld, dropped, bad_missing, bad_extra = roster.drift(CURRENT, covered, root)
    assert bad_extra == {'Stranger'} and dropped == {'Resigned'} and not withheld and not bad_missing
