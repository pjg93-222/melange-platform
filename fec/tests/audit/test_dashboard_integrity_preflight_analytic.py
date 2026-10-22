# -*- coding: utf-8 -*-
"""preflight(analytic_inputs_only=...): dashboard_data roster drift is a warning only on the analytic-inputs stage.

``make_member_desk.py --data --analytic-inputs-only`` runs before ``dashboard_data.json`` is rebuilt for the current
roster, so on the 533 -> 535 roster that file still names the old 533.  Everywhere else the equality stays a hard error.

FIXTURE ONLY: the canonical roster, the workbook sheets and the stance matrix are synthetic stand-ins.
"""
import os
import sys

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)

import audit.dashboard_integrity as di

NAMES = ['A', 'B', 'C']
DESK = open(os.path.join(FEC, 'surfaces', 'make_member_desk.py'), encoding='utf-8').read()


class Sheet:
    def __init__(self, names):
        self.names = names

    def cell(self, row, col):
        index = row - 3
        return type('Cell', (), {'value': self.names[index] if 0 <= index < len(self.names) else None})


class Workbook:
    def __init__(self, names):
        self.sheets = {'Rank & Allocation': Sheet(names), 'Press Release - Raw': Sheet(names)}

    def __getitem__(self, key):
        return self.sheets[key]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fixture_world(monkeypatch):
    monkeypatch.setattr(di.MC, 'members', lambda: list(NAMES))
    monkeypatch.setattr(di.openpyxl, 'load_workbook', lambda *a, **k: Workbook(NAMES))
    monkeypatch.setattr(di.roster_rows, 'rows', lambda sheet, root=None: range(3, 3 + len(NAMES)))


def category_stance():
    return {'cats': ['c1'], 'matrix': {n: {'c1': 'x'} for n in NAMES}, 'scores': {n: {'c1': 1} for n in NAMES}}


def dashboard(names):
    return {'name': list(names), 'issues': ['i1'], 'stance': ['x'] * len(names)}


def test_stale_dashboard_data_is_a_warning_in_the_analytic_stage():
    report = di.preflight(NAMES, category_stance(), dashboard(['A', 'B']), set(), analytic_inputs_only=True)
    assert report['status'] == 'pass' and report['errors'] == []
    [warning] = [w for w in report['warnings'] if 'dashboard_data roster differs' in w]
    assert 'missing=1' in warning and 'extras=0' in warning


def test_stale_dashboard_data_is_still_a_hard_error_by_default():
    with pytest.raises(di.IntegrityError, match='dashboard_data roster differs from canonical roster'):
        di.preflight(NAMES, category_stance(), dashboard(['A', 'B']), set())
    with pytest.raises(di.IntegrityError, match='dashboard_data roster differs from canonical roster'):
        di.preflight(NAMES, category_stance(), dashboard(['A', 'B']), set(), analytic_inputs_only=False)


def test_a_matching_dashboard_data_raises_no_warning_in_either_mode():
    for flag in (True, False):
        report = di.preflight(NAMES, category_stance(), dashboard(NAMES), set(), analytic_inputs_only=flag)
        assert not [w for w in report['warnings'] if 'dashboard_data' in w]


def test_the_analytic_stage_does_not_relax_any_other_roster_check():
    stale = category_stance()
    del stale['matrix']['C']
    with pytest.raises(di.IntegrityError, match='stance matrix roster differs'):
        di.preflight(NAMES, stale, dashboard(['A', 'B']), set(), analytic_inputs_only=True)
    with pytest.raises(di.IntegrityError, match='dashboard_data contains duplicate names'):
        di.preflight(NAMES, category_stance(), dashboard(['A', 'A', 'B']), set(), analytic_inputs_only=True)


def test_the_desk_passes_its_analytic_flag_to_preflight():
    assert 'DI.preflight(member_names, CS, SNA, ARCH, analytic_inputs_only=analytic_inputs_only)' in DESK
