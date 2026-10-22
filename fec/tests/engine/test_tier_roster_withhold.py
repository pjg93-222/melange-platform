# -*- coding: utf-8 -*-
"""Roster-drift contract for the primacy board (E2E attempt 6: ``no primacy factor for 'Aisha Wahab'``).

``primacy_factors.json`` is rebuilt from the bundle AFTER the desk's analytic-inputs step, so a roster arrival cannot have a
factor when ``legislative_press_tiers`` first sees it.  Ruling: factor-only board, no neutral default -- the arrival is WITHHELD
from the board with a logged ``!!`` line.  A continuing member without a factor must still raise.

``legislative_press_tiers`` is not driven end to end (its overrides path is code-root-relative); the helper and the call-site
literal are pinned instead.
"""
import json
import os
import sys

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import roster as roster_module
import tier_util

SOURCE = open(os.path.join(FEC, 'tier_util.py'), encoding='utf-8').read()

ROWS = [
    {'member': 'A', 'bioguide': 'X000001', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'B', 'bioguide': 'X000002', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'New', 'bioguide': 'X000004', 'chamber': 'House', 'term_start': '2026-06-01'},
]
DEPARTED = [{'member': 'Old', 'bioguide': 'X000005', 'departed_on': '2026-08-01'}]


@pytest.fixture()
def root(tmp_path):
    os.makedirs(tmp_path / 'data' / 'engine')
    (tmp_path / 'data' / 'engine' / 'roster.json').write_text(json.dumps(
        {'schema': roster_module.SCHEMA, 'members': ROWS, 'departed': DEPARTED}), encoding='utf-8')
    return str(tmp_path)


def pcts(*names):
    return {n: {'leg': 50.0} for n in names}


def test_an_arrival_without_a_factor_is_withheld_and_logged(root, capsys):
    members = ['A', 'B', 'New']
    kept, withheld = tier_util.withhold_roster_arrivals(members, pcts(*members), {'A': 1.0, 'B': 2.0}, root)
    assert withheld == ['New'] and set(kept) == {'A', 'B'}
    lines = [l for l in capsys.readouterr().out.splitlines() if '!! primacy board' in l]
    assert len(lines) == 1 and 'WITHHELD' in lines[0] and 'New' in lines[0]
    assert set(tier_util.primacy_scores(kept, {'A': 1.0, 'B': 2.0}, mode='factor')) == {'A', 'B'}


def test_a_missing_non_arrival_stays_and_primacy_scores_still_raises(root, capsys):
    members = ['A', 'B', 'New']
    factors = {'A': 1.0, 'New': 3.0}          # B is a continuing member: a missing factor is a defect, not drift
    kept, withheld = tier_util.withhold_roster_arrivals(members, pcts(*members), factors, root)
    assert withheld == [] and set(kept) == {'A', 'B', 'New'}
    assert '!!' not in capsys.readouterr().out
    with pytest.raises(ValueError, match="no primacy factor for 'B'"):
        tier_util.primacy_scores(kept, factors, mode='factor')


def test_no_drift_changes_nothing_and_says_nothing(root, capsys):
    members = ['A', 'B', 'New']
    original = pcts(*members)
    kept, withheld = tier_util.withhold_roster_arrivals(
        members, original, {'A': 1.0, 'B': 2.0, 'New': 3.0}, root)
    assert withheld == [] and kept == original
    assert capsys.readouterr().out == ''


def test_no_drift_never_reads_roster_json(tmp_path, capsys):
    members = ['A', 'B']
    original = pcts(*members)
    kept, withheld = tier_util.withhold_roster_arrivals(members, original, {'A': 1.0, 'B': 2.0}, str(tmp_path))
    assert kept is original and withheld == [] and capsys.readouterr().out == ''


def test_a_departed_member_in_factors_is_not_drift(root, capsys):
    members = ['A', 'B', 'New']
    factors = {'A': 1.0, 'B': 2.0, 'New': 3.0, 'Old': 4.0}
    kept, withheld = tier_util.withhold_roster_arrivals(members, pcts(*members), factors, root)
    assert withheld == [] and set(kept) == set(members) and capsys.readouterr().out == ''


def test_legislative_press_tiers_filters_before_scoring():
    call = "withhold_roster_arrivals(members, rp.get('pcts', {}), factors, root)"
    assert call in SOURCE
    assert SOURCE.index(call) < SOURCE.index('all_scores = primacy_scores(')
