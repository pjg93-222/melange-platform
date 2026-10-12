# -*- coding: utf-8 -*-
"""Guard the shipped lobbying coverage claims against silent text drift."""

import ast
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
NOTE_SPECS = (
    (
        "fec/lobbying/bill_lobbying_sidecar.py:COVERAGE_NOTE",
        Path("fec/lobbying/bill_lobbying_sidecar.py"),
        "COVERAGE_NOTE",
        None,
    ),
    (
        "fec/lobbying/build_network_2021_rebuild.py:BILL_INDEX_HONESTY[0]",
        Path("fec/lobbying/build_network_2021_rebuild.py"),
        "BILL_INDEX_HONESTY",
        0,
    ),
    (
        "fec/surfaces/make_lobby_interactions_html.py:COVERAGE_NOTE",
        Path("fec/surfaces/make_lobby_interactions_html.py"),
        "COVERAGE_NOTE",
        None,
    ),
    (
        "fec/surfaces/make_lobby_network_html.py:CITATION_NOTE",
        Path("fec/surfaces/make_lobby_network_html.py"),
        "CITATION_NOTE",
        None,
    ),
)
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?%")
POOLED_WINDOW = "pooled across the 2021–2026 window"
SELECTION_CLAIM = "not a random sample"
SELECTION_CLAIM_NOTES = {
    "fec/surfaces/make_lobby_interactions_html.py:COVERAGE_NOTE",
    "fec/surfaces/make_lobby_network_html.py:CITATION_NOTE",
}


def _joined_literal(label, relative_path, constant, element):
    path = REPO_ROOT / relative_path
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssertionError(f"{label}: source must be readable: {exc}") from exc

    try:
        module = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise AssertionError(f"{label}: source must parse as Python: {exc}") from exc

    assignments = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == constant
            for target in node.targets
        )
    ]
    if len(assignments) != 1:
        raise AssertionError(
            f"{label}: expected exactly one Assign for {constant}, "
            f"found {len(assignments)}"
        )

    try:
        value = ast.literal_eval(assignments[0].value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{label}: value must be a literal: {exc}") from exc

    if element is not None:
        if not isinstance(value, (tuple, list)):
            raise AssertionError(
                f"{label}: {constant} must be a literal sequence before indexing"
            )
        try:
            value = value[element]
        except IndexError as exc:
            raise AssertionError(
                f"{label}: {constant} must contain element {element}"
            ) from exc

    if not isinstance(value, str):
        raise AssertionError(f"{label}: extracted joined literal must be a string")
    return value


def _notes():
    return {
        label: _joined_literal(label, relative_path, constant, element)
        for label, relative_path, constant, element in NOTE_SPECS
    }


class CoverageClaimTextTests(unittest.TestCase):
    def test_notes_are_extractable_joined_non_empty_literals(self):
        for label, note in _notes().items():
            self.assertTrue(
                note.strip(),
                f"{label}: extracted joined literal must be non-empty",
            )

    def test_all_notes_state_the_same_percentage_set(self):
        percentages = {
            label: set(PERCENT_RE.findall(note))
            for label, note in _notes().items()
        }
        for label, figures in percentages.items():
            self.assertTrue(
                figures,
                f"{label}: joined literal must state at least one percentage",
            )
        self.assertEqual(
            len({frozenset(figures) for figures in percentages.values()}),
            1,
            f"{', '.join(percentages)}: distinct percentage sets must agree",
        )

    def test_no_note_contains_the_stale_percentage(self):
        for label, note in _notes().items():
            self.assertNotIn(
                "23%",
                note,
                f"{label}: joined literal must not contain stale 23%",
            )

    def test_every_note_names_the_pooled_window_basis(self):
        for label, note in _notes().items():
            self.assertIn(
                POOLED_WINDOW,
                note,
                f"{label}: joined literal must name the pooled 2021–2026 window",
            )

    def test_selection_claim_stays_on_the_two_specific_surface_notes(self):
        for label, note in _notes().items():
            if label in SELECTION_CLAIM_NOTES:
                self.assertIn(
                    SELECTION_CLAIM,
                    note,
                    f"{label}: joined literal must retain the not-a-random-sample claim",
                )
            else:
                self.assertNotIn(
                    SELECTION_CLAIM,
                    note,
                    f"{label}: selection claim must not migrate from the two surface notes",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
