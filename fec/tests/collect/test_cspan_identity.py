# -*- coding: utf-8 -*-
"""Regression tests for the C-SPAN attribution repair."""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from members.cspan_identity import CSpanIdentity, congress_for_date  # noqa: E402
from members.load_cspan import prepare, replace  # noqa: E402


class CSpanIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = [
            "Don Bacon", "Earl Carter", "Janice Schakowsky", "Bradley Schneider",
            "Henry Johnson", "Gabe Vasquez", "Luis Correa", "Jennifer Kiggans",
            "Dusty Johnson", "Carlos Gimenez", "Adriano Espaillat", "Harriet Hageman",
            "Darren Soto", "Mike Johnson",
        ]
        roster = [
            {"member": name, "bioguide": f"T{i:06d}", "chamber": "House"}
            for i, name in enumerate(names)
        ]
        service = {name: {116, 117, 118, 119} for name in names}
        cls.identity = CSpanIdentity(roster=roster, service=service)

    def attribute(self, label, title, date="2025-07-01"):
        expected = self.identity.expected_guest(title, date, [label])
        return self.identity.attribute(label, title, date, expected)

    def test_blank_role_does_not_control_member_identity(self):
        result = self.attribute("Don Bacon", "Representative Don Bacon on U.S. Policy")
        self.assertEqual("Don Bacon", result.member)
        self.assertEqual("member", result.status)

    def test_public_name_aliases(self):
        cases = [
            ("Buddy Carter", "Representative Buddy Carter on Trade", "Earl Carter"),
            ("Jan Schakowsky", "Rep. Jan Schakowsky on Health Care", "Janice Schakowsky"),
            ("Brad Schneider", "Representative Brad Schneider on Iran", "Bradley Schneider"),
            ("Hank Johnson Jr.", "Rep. Hank Johnson on Courts", "Henry Johnson"),
            ('Gabriel "Gabe" Vasquez', "Representative Gabe Vasquez on Energy", "Gabe Vasquez"),
            ("Lou Correa", "Rep. Lou Correa on Immigration", "Luis Correa"),
            ("Jen Kiggans", "Representative Jen Kiggans on Veterans", "Jennifer Kiggans"),
            ('Dustin "Dusty" Johnson', "Rep. Dusty Johnson on Agriculture", "Dusty Johnson"),
        ]
        for label, title, member in cases:
            with self.subTest(label=label):
                self.assertEqual(member, self.attribute(label, title).member)

    def test_middle_name_and_initial_are_allowed_but_full_name_is_required(self):
        self.assertEqual(
            "Adriano Espaillat",
            self.attribute("Adriano D. Espaillat", "Rep. Espaillat on Immigration").member,
        )
        self.assertIsNone(self.attribute("Espaillat", "Rep. Espaillat on Immigration").member)

    def test_title_confirmed_honorific_surname_label(self):
        result = self.attribute("REP. BACON", "Representative Bacon on Defense")
        self.assertEqual("Don Bacon", result.member)

    def test_ambiguous_title_surname_is_narrowed_by_full_speaker_label(self):
        expected = self.identity.expected_guest(
            "Representative Johnson on the House Agenda",
            "2025-07-01",
            ["Mike Johnson", "Greta Brawner"],
        )
        self.assertEqual("Mike Johnson", expected.member)

    def test_inserted_clip_member_is_rejected(self):
        result = self.attribute(
            "REP. SOTO",
            "Rep. Harriet Hageman on Los Angeles Protests and Trump Deportation Policies",
        )
        self.assertIsNone(result.member)
        self.assertIn(result.status, {"unresolved_label", "title_label_mismatch"})

    def test_host_and_caller_labels_are_rejected(self):
        for label in ("Greta Brawner", "CALLER", "ANNOUNCER"):
            with self.subTest(label=label):
                self.assertIsNone(self.attribute(label, "Representative Don Bacon on Defense").member)

    def test_tenure_gate_is_fail_closed(self):
        service = {"Don Bacon": {119}}
        identity = CSpanIdentity(
            roster=[{"member": "Don Bacon", "bioguide": "B001298", "chamber": "House"}],
            service=service,
        )
        expected = identity.expected_guest("Representative Don Bacon on Defense", "2020-01-08", ["Don Bacon"])
        self.assertIsNone(expected.member)
        self.assertEqual("title_guest_out_of_service", expected.status)

    def test_known_cspan_title_typo_is_auditable_alias(self):
        result = self.attribute("Carlos A. Gimenez", "Rep. Carlos Gimemez on U.S. Policy")
        self.assertEqual("Carlos Gimenez", result.member)

    def test_congress_date_boundary(self):
        self.assertEqual(116, congress_for_date("2021-01-02"))
        self.assertEqual(117, congress_for_date("2021-01-03"))

    def test_real_cspan_fixture_has_blank_role_but_resolves_member(self):
        path = os.path.join(ROOT, "cspan_out", "transcripts", "538994.json")
        if not os.path.exists(path):
            self.skipTest("workspace C-SPAN fixture is absent")
        with open(path, encoding="utf-8") as handle:
            rec = json.load(handle)
        member_turn = next(turn for turn in rec["turns"] if turn.get("name") == "Don Bacon")
        self.assertEqual("", member_turn.get("role") or "")
        live = CSpanIdentity(ROOT)
        expected = live.expected_guest(rec["title"], rec["date"], [turn.get("name") for turn in rec["turns"]])
        self.assertEqual("Don Bacon", live.attribute("Don Bacon", rec["title"], rec["date"], expected).member)


class InteractionProgramBucketTests(unittest.TestCase):
    def test_cspan_has_separate_program_bucket(self):
        path = os.path.join(FEC, "members", "interaction_features.py")
        with open(path, encoding="utf-8-sig") as handle:
            tree = ast.parse(handle.read(), filename=path)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "program_of")
        namespace = {"re": re}
        exec(compile(ast.Module(body=[function], type_ignores=[]), path, "exec"), namespace)
        self.assertEqual(
            "cspan:washington-journal",
            namespace["program_of"]("https://www.c-span.org/program/washington-journal/example/123"),
        )
        self.assertEqual("other", namespace["program_of"]("https://example.com/interview"))


class CSpanLoaderTests(unittest.TestCase):
    def test_prepare_and_transactional_cspan_only_replace(self):
        roster = [
            {"member": "Don Bacon", "bioguide": "B001298", "chamber": "House"},
            {"member": "Darren Soto", "bioguide": "S001200", "chamber": "House"},
        ]
        identity = CSpanIdentity(roster=roster, service={"Don Bacon": {116}, "Darren Soto": {116}})
        with tempfile.TemporaryDirectory() as tmp:
            directory = os.path.join(tmp, "cspan_out")
            transcripts = os.path.join(directory, "transcripts")
            os.makedirs(transcripts)
            speech = "These are enough words for a valid member turn in this fixture."
            fixture = {
                "id": "538994",
                "url": "https://www.c-span.org/program/washington-journal/example/538994",
                "title": "Representative Don Bacon on Defense",
                "date": "2020-01-08",
                "turns": [
                    {"name": "Greta Brawner", "role": "", "text": "This host introduction has enough words to pass the word threshold."},
                    {"name": "Don Bacon", "role": "", "text": speech},
                    {"name": "REP. SOTO", "role": "", "text": "This inserted clip has enough words but belongs to somebody else."},
                    {"name": "Don Bacon", "role": "", "text": speech},
                ],
            }
            fixture_path = os.path.join(transcripts, "538994.json")
            with open(fixture_path, "w", encoding="utf-8") as handle:
                json.dump(fixture, handle)

            rows, programs, summary = prepare(directory, identity=identity)
            self.assertEqual(1, summary["prepared_turns"])
            self.assertEqual("Don Bacon", rows[0][0])
            self.assertEqual(1, summary["status_counts"]["per_program_duplicate"])

            db_path = os.path.join(tmp, "test.db")
            con = sqlite3.connect(db_path)
            con.execute("CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,package TEXT,nwords INTEGER,text TEXT)")
            con.executemany("INSERT INTO speeches VALUES (?,?,?,?,?,?,?)", [
                ("Don Bacon", "2020-01-01", "INTERVIEW", "x", "https://www.c-span.org/program/old/1", 8, "old cspan row"),
                ("Don Bacon", "2020-01-01", "INTERVIEW", "x", "https://example.com/keep", 8, "non cspan row"),
            ])
            con.commit()
            con.close()

            before, after, backup = replace(db_path, rows, programs, os.path.join(tmp, "backups"))
            self.assertEqual(1, before["turns"])
            self.assertEqual(1, after["turns"])
            self.assertTrue(os.path.exists(backup))
            con = sqlite3.connect(db_path)
            try:
                packages = {row[0] for row in con.execute("SELECT package FROM speeches")}
            finally:
                con.close()
            self.assertEqual({"https://example.com/keep", fixture["url"]}, packages)


if __name__ == "__main__":
    unittest.main(verbosity=2)
