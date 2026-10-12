# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import copy
from pathlib import Path
import re
import unittest


FEC = Path(__file__).resolve().parents[2]
PRODUCER = FEC / "surfaces" / "make_member_desk.py"
TEMPLATE = FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"


def load_personal_display_helpers():
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8-sig"), filename=str(PRODUCER))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            if any(name.startswith("_PERSONAL_DEDUPE_") for name in names):
                selected.append(node)
            elif names & {
                "_PERSONAL_SOURCE_QUALITY",
                "_PERSONAL_CONFIDENCE_QUALITY",
                "_PERSONAL_DISPLAY_REPLACEMENTS",
            }:
                selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name.startswith("_personal_")
            or node.name == "_html_with_personal_dedupe"
        ):
            selected.append(node)
    namespace = {"re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PRODUCER), "exec"), namespace)
    return namespace


def item(claim, evidence, date, *, category="family", eligible=True):
    return {
        "category": category,
        "claim": claim,
        "evidence": evidence,
        "source": "INTERVIEW",
        "date": date,
        "locator": "https://example.test/%s" % date,
        "url": "https://example.test/%s" % date,
        "confidence": "medium",
        "display_eligible": eligible,
        "narrative_eligible": False,
    }


class PersonalDisplayDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = load_personal_display_helpers()
        cls.prepare = staticmethod(cls.helpers["_personal_for_display"])

    def test_scalise_like_consolation_variants_collapse_without_touching_raw_items(self):
        rows = [
            item(
                "Not many know that, while I was fighting for my life, Donald Trump "
                "was one of the first to come console my family at the hospital.",
                "Donald Trump came to the hospital and consoled my family while I recovered.",
                "2024-07-17",
            ),
            item(
                "They consoled my wife, Jennifer.",
                "One of the first people there was Donald Trump, who came to the hospital "
                "while I fought for my life; he and Melania consoled my family and wife Jennifer.",
                "2020-08-24",
            ),
            item(
                "When I was shot, he came to the hospital with Melania, the first lady, "
                "and consoled my wife, and was wonderful to my family.",
                "Donald Trump and Melania came to the hospital; Melania consoled my wife.",
                "2020-08-24",
            ),
            item(
                "I took my son duck hunting after I recovered.",
                "I went duck hunting with my son for the first time after leaving the hospital.",
                "2017-12-21",
            ),
            item(
                "A broad political reference that failed the precision gate.",
                "Raw evidence remains available.",
                "2025-01-01",
                eligible=False,
            ),
        ]
        personal = {"items": rows, "n_items": len(rows)}
        before = copy.deepcopy(personal)

        prepared = self.prepare("Steve Scalise", personal)

        self.assertIs(prepared["items"], rows)
        self.assertEqual(personal, before)
        self.assertEqual(prepared["items"], before["items"])
        self.assertEqual(prepared["display_dedupe"]["suppressed_count"], 2)
        self.assertEqual(prepared["display_dedupe"]["suppressed_indices"], [1, 2])
        self.assertEqual(
            prepared["display_dedupe"]["clusters"],
            [{"representative_index": 0, "suppressed_indices": [1, 2]}],
        )

    def test_same_category_but_distinct_experiences_are_not_merged(self):
        rows = [
            item(
                "Donald Trump visited me at the hospital after the shooting.",
                "Donald Trump visited after the baseball-field shooting.",
                "2024-07-17",
            ),
            item(
                "My wife helped me recover at home after the shooting.",
                "My wife managed my recovery after I returned home.",
                "2020-08-24",
            ),
            item(
                "Melania Trump sent my family flowers.",
                "Melania Trump sent flowers during my recovery.",
                "2020-08-24",
            ),
        ]

        prepared = self.prepare("Example Member", {"items": rows})

        self.assertEqual(prepared["display_dedupe"]["suppressed_count"], 0)
        self.assertEqual(prepared["display_dedupe"]["clusters"], [])

    def test_comfort_and_console_are_treated_as_the_same_supported_event(self):
        rows = [
            item(
                "Donald Trump came to console my family at the hospital.",
                "Donald Trump came to the hospital to console my family.",
                "2024-07-17",
            ),
            item(
                "Donald Trump comforted my family at the hospital.",
                "Donald Trump comforted my family during the hospital stay.",
                "2020-08-24",
            ),
        ]

        prepared = self.prepare("Example Member", {"items": rows})

        self.assertEqual(prepared["display_dedupe"]["suppressed_count"], 1)
        self.assertEqual(prepared["display_dedupe"]["suppressed_indices"], [1])

    def test_opposite_polarity_claims_are_never_collapsed(self):
        rows = [
            item(
                "I've never been a big fan of Tulsi Gabbard.",
                "I've never been a big fan of Tulsi Gabbard.",
                "2026-06-11",
                category="hobbies_sports",
            ),
            item(
                "I'm a big fan of Tulsi Gabbard.",
                "I'm a big fan of Tulsi Gabbard.",
                "2026-06-11",
                category="hobbies_sports",
            ),
        ]

        prepared = self.prepare("Mark Warner", {"items": rows})

        self.assertEqual(prepared["display_dedupe"]["suppressed_count"], 0)
        self.assertEqual(prepared["display_dedupe"]["clusters"], [])

    def test_different_named_relatives_are_never_collapsed(self):
        rows = [
            item(
                "My son John attends Duke University and studies computer science.",
                "My son John attends Duke University and studies computer science.",
                "2025-08-01",
            ),
            item(
                "My son James attends Duke University and studies computer science.",
                "My son James attends Duke University and studies computer science.",
                "2025-08-01",
            ),
        ]

        prepared = self.prepare("Example Member", {"items": rows})

        self.assertEqual(prepared["display_dedupe"]["suppressed_count"], 0)
        self.assertEqual(prepared["display_dedupe"]["clusters"], [])

    def test_render_patch_reports_each_suppression_class_separately(self):
        rendered = self.helpers["_html_with_personal_dedupe"](
            TEMPLATE.read_text(encoding="utf-8")
        )

        self.assertIn("sourceEligible=allItems.filter", rendered)
        self.assertIn("nearDupes=dedupeSuppressed.size", rendered)
        self.assertIn("semantically repeated personal detail", rendered)
        self.assertIn("raw evidence retained", rendered)
        self.assertIn("biography duplicate", rendered)
        self.assertIn("personal-detail precision gate", rendered)


if __name__ == "__main__":
    unittest.main()
