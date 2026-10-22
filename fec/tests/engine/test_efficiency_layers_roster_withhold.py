# -*- coding: utf-8 -*-
"""Roster-drift contract for efficiency_layers (E2E attempt 6: ``current-member input coverage failure: panel=3``).

The frozen LDA ``panel.csv`` and ``member_receptivity.json`` predate the roster refresh, so a roster arrival has no row in
either.  Ruling: the arrival is WITHHELD from the layer (no row in ``efficiency_layers.json``, one logged ``!!`` line); any
non-arrival gap still raises the original ``current-member input coverage failure``.
"""
import json
import os
import sys

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import roster as roster_module
from engine import efficiency_layers as EL

SOURCE = open(os.path.join(FEC, 'engine', 'efficiency_layers.py'), encoding='utf-8').read()

ROWS = [
    {'member': 'A', 'bioguide': 'X000001', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'B', 'bioguide': 'X000002', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'New', 'bioguide': 'X000004', 'chamber': 'House', 'term_start': '2026-06-01'},
]


@pytest.fixture()
def root(tmp_path):
    os.makedirs(tmp_path / 'data' / 'engine')
    (tmp_path / 'data' / 'engine' / 'roster.json').write_text(json.dumps(
        {'schema': roster_module.SCHEMA, 'members': ROWS, 'departed': []}), encoding='utf-8')
    return str(tmp_path)


def test_an_arrival_missing_panel_and_receptivity_is_withheld_and_logged(root, capsys):
    panel, rec, pricing = EL._coverage_withhold(['New'], ['New'], [], root)
    assert panel == rec == pricing == []
    lines = [l for l in capsys.readouterr().out.splitlines() if '!! efficiency layers' in l]
    assert len(lines) == 1 and 'roster drift; 1 WITHHELD' in lines[0] and 'New' in lines[0]


def test_a_non_arrival_gap_is_kept_so_the_check_still_raises(root, capsys):
    panel, rec, pricing = EL._coverage_withhold(['B', 'New'], [], ['A'], root)
    assert panel == ['B'] and rec == [] and pricing == ['A']
    assert len(capsys.readouterr().out.splitlines()) == 1     # New still logged as withheld


def test_no_gap_never_reads_roster_json(tmp_path, capsys):
    assert EL._coverage_withhold([], [], [], str(tmp_path)) == ([], [], [])   # tmp_path has no roster.json
    assert capsys.readouterr().out == ''


def test_a_non_arrival_only_gap_logs_nothing(root, capsys):
    assert EL._coverage_withhold(['B'], [], [], root) == (['B'], [], [])
    assert capsys.readouterr().out == ''


def test_withhold_precedes_the_raise_in_build_from_files():
    call = '_coverage_withhold(missing_panel, missing_receptivity, missing_pricing'
    assert call in SOURCE
    assert SOURCE.index(call) < SOURCE.index('current-member input coverage failure')
