"""backfill_attribution.reconcile: insert what is missing, re-label only a same-surname swap, never touch anything else.
Loads only the pure functions (no live roster, database or network)."""
import ast
import collections
from pathlib import Path
import unicodedata

import pytest


@pytest.fixture(scope='module')
def tool():
    path = Path(__file__).resolve().parents[2] / 'collect/backfill_attribution.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'contains', 'covers', 'surname', 'reconcile'}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    fold = lambda s: ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
    scope = {'HEAD': 120, 'collections': collections, 'CR': type('CR', (), {'_fold': staticmethod(fold)})}
    exec(compile(ast.Module(body=fns, type_ignores=[]), str(path), 'exec'), scope)
    return scope


def row(member, text): return (member, '2026-09-16', 'CREC', 'house', 'CREC-2026-09-16', len(text.split()), text)


A = 'Mr. Speaker, I rise today to honor the workers of my district. ' * 4
B = 'Madam Speaker, I rise in support of the bill before us and urge adoption. ' * 4


def test_missing_turn_is_inserted_and_a_covered_one_is_not(tool):
    ins, reowned, unrep, relabel = tool['reconcile']([row('Austin Scott', A), row('Lois Frankel', B)], [(1, 'Lois Frankel', B)])
    assert [r[0] for r in ins] == ['Austin Scott'] and not reowned and not unrep and not relabel


def test_second_run_changes_nothing(tool):
    stored = [(1, 'Austin Scott', A), (2, 'Lois Frankel', B)]
    assert tool['reconcile']([row('Austin Scott', A), row('Lois Frankel', B)], stored) == ([], [], [], [])


def test_segmentation_difference_is_not_a_new_turn(tool):
    # old resolver let the next speaker's words run into this row; the new parse cuts it shorter: same speech
    ins, *_ = tool['reconcile']([row('Austin Scott', A)], [(1, 'Austin Scott', A + B)])
    assert ins == []


def test_same_surname_swap_is_relabelled(tool):
    ins, reowned, unrep, relabel = tool['reconcile']([row('Jesús García', A)], [(7, 'Sylvia Garcia', A)])
    assert relabel == [(7, 'Sylvia Garcia', 'Jesús García')] and len(reowned) == 1 and not unrep
    assert ins == []   # the UPDATEd row is that turn; inserting it as well would store it twice


def test_swap_to_a_member_who_already_has_the_row_is_not_relabelled(tool):
    # relabelling would create an exact duplicate of Jesus's row
    stored = [(7, 'Sylvia Garcia', A), (8, 'Jesús García', A)]
    _, reowned, _, relabel = tool['reconcile']([row('Jesús García', A)], stored)
    assert relabel == [] and len(reowned) == 1


def test_a_merged_stored_row_is_not_relabelled_to_the_owner_of_one_turn_inside_it(tool):
    # old parse ran Sylvia's turn and the next speaker's together; the new parse finds Jesus's turn INSIDE that blob.  Relabelling the whole blob
    # to Jesus would hand him words he never said: report only.
    ins, reowned, unrep, relabel = tool['reconcile']([row('Jesús García', A)], [(7, 'Sylvia Garcia', B + A)])
    assert relabel == [] and len(reowned) == 1


def test_different_surname_owner_is_report_only(tool):
    _, reowned, unrep, relabel = tool['reconcile']([row('Clay Higgins', A)], [(3, 'Brian Higgins', B), (4, 'Sanford Bishop', A)])
    assert relabel == [] and [o[0][0] for o in reowned] == [4] and [o[0][0] for o in unrep] == [3]


def test_stored_row_the_parse_refuses_is_reported_not_deleted(tool):
    _, reowned, unrep, relabel = tool['reconcile']([], [(9, 'Al Green', A)])
    assert not reowned and not relabel and unrep == [((9, 'Al Green', A), None)]


def test_a_line_repeated_within_a_package_is_inserted_once(tool):
    # the corpus audit caps exact (member, package, text) surplus; the old rows of a boilerplate line are not duplicated further
    ins, *_ = tool['reconcile']([row('Austin Scott', A), row('Austin Scott', A), row('Austin Scott', B)], [])
    assert [r[6] for r in ins] == [A, B]
