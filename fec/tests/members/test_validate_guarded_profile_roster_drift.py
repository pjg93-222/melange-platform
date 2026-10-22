# -*- coding: utf-8 -*-
"""Guarded profile artifacts built before the roster refresh (533 -> 535): tolerate the refresh's own drift, nothing else.

``validate_guarded_profile_artifacts`` asserted the audio, external-gold E and profile-basis member sets against the sitting
roster with plain set equality / subset tests, so the pre-refresh artifacts failed the moment one member left and three arrived.
``roster_mismatch`` now allows an artifact that predates the refresh to lack roster ARRIVALS and still carry DEPARTED members,
names both loudly, and flags every other difference.

FIXTURE ONLY: a synthetic roster.json stands in for the sitting roster; no data plane is read.
"""
import json
import os
import sys

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)

import members.validate_guarded_profile_artifacts as gp
import roster as roster_module

SOURCE = open(os.path.join(FEC, 'members', 'validate_guarded_profile_artifacts.py'), encoding='utf-8').read()
ROWS = [
    {'member': 'Continuing', 'bioguide': 'C000001', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'Also Continuing', 'bioguide': 'C000002', 'chamber': 'Senate', 'term_start': '2021-01-03'},
    {'member': 'Special Election Arrival', 'bioguide': 'N000001', 'chamber': 'House', 'term_start': '2026-01-10'},
    {'member': 'Flagged Thin', 'bioguide': 'N000002', 'chamber': 'House', 'term_start': '2023-01-03', 'thin_evidence': True},
]
DEPARTED = [{'member': 'Resigned', 'bioguide': 'D000001', 'departed_on': '2026-09-01'}]
ROSTER = {row['member'] for row in ROWS}


@pytest.fixture(autouse=True)
def sitting_roster(tmp_path, monkeypatch):
    os.makedirs(tmp_path / 'data' / 'engine')
    (tmp_path / 'data' / 'engine' / 'roster.json').write_text(json.dumps(
        {'schema': roster_module.SCHEMA, 'members': ROWS, 'departed': DEPARTED}), encoding='utf-8')
    real = roster_module.drift
    monkeypatch.setattr(gp.R, 'drift', lambda current, covered: real(current, covered, str(tmp_path)))


def test_a_full_roster_artifact_may_lack_arrivals_and_carry_departed_members_loudly(capsys):
    names = {'Continuing', 'Also Continuing', 'Resigned'}
    assert gp.roster_mismatch('profile basis', ROSTER, names, complete=True) is False
    out = capsys.readouterr().out
    assert '!!' in out and 'profile basis' in out
    assert 'Special Election Arrival' in out and 'Flagged Thin' in out and 'Resigned' in out


def test_a_full_roster_artifact_missing_a_continuing_member_is_a_mismatch():
    assert gp.roster_mismatch('profile basis', ROSTER, {'Continuing', 'Special Election Arrival', 'Flagged Thin'},
                              complete=True) is True


def test_a_full_roster_artifact_with_an_unknown_extra_is_a_mismatch():
    assert gp.roster_mismatch('profile basis', ROSTER, ROSTER | {'Stranger'}, complete=True) is True


def test_an_exact_match_is_silent(capsys):
    assert gp.roster_mismatch('profile basis', ROSTER, ROSTER, complete=True) is False
    assert capsys.readouterr().out == ''


def test_a_partial_coverage_artifact_may_lack_anyone_without_noise(capsys):
    assert gp.roster_mismatch('external-gold E', ROSTER, {'Continuing'}, complete=False) is False
    assert capsys.readouterr().out == ''


def test_a_partial_coverage_artifact_may_still_carry_a_departed_member_loudly(capsys):
    assert gp.roster_mismatch('audio', ROSTER, {'Continuing', 'Resigned'}, complete=False) is False
    out = capsys.readouterr().out
    assert '!!' in out and 'Resigned' in out


def test_a_partial_coverage_artifact_with_an_unknown_extra_is_a_mismatch():
    assert gp.roster_mismatch('audio', ROSTER, {'Continuing', 'Stranger'}, complete=False) is True


def test_validate_all_applies_the_rule_to_all_three_roster_checks_with_unchanged_messages():
    body = SOURCE.split('def validate_all(', 1)[1]
    for label in ('audio', 'external-gold E', 'profile basis'):
        assert "roster_mismatch('%s'" % label in body, label
    for message in ("'audio contains unknown members'", "'external-gold E coverage/roster mismatch'",
                    "'profile basis roster mismatch'"):
        assert message in body, message
    assert 'len(em)<450' in body        # the E-gold coverage floor is not loosened
