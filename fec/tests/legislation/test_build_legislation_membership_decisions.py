from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from fec.legislation import build_legislation_membership_decisions as builder
from fec.legislation.legislation_culling_integration_contract import MEMBERSHIP_SCHEMA


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def ledger_row(
    key: str,
    query_id: str,
    disposition: str,
    *,
    decision_source: str = "explicit_review_decision",
    adjudication_state: str | None = "reverified_preexisting_project_decision",
    **extra,
) -> dict:
    row = {
        "canonical_key": key,
        "query_id": query_id,
        "disposition": disposition,
        "decision_source": decision_source,
        "adjudication_state": adjudication_state,
        "article_absence_is_never_exclusion_evidence": True,
        "official_text_evidence_state": None,
        "evidence_sources_checked": [],
    }
    row.update(extra)
    return row


class MembershipDecisionProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def run_build(
        self, ledger_rows: list[dict], membership_rows: list[dict]
    ) -> dict:
        ledger = self.root / "ledger.ndjson"
        memberships = self.root / "memberships.ndjson"
        write_ndjson(ledger, ledger_rows)
        write_ndjson(memberships, membership_rows)
        args = builder.parse_args(
            [
                "--ledger", str(ledger),
                "--memberships", str(memberships),
                "--out", str(self.root / "out"),
            ]
        )
        return builder.build(args)

    def emitted(self) -> list[dict]:
        path = self.root / "out" / "membership_decisions.ndjson"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def dropped(self) -> list[dict]:
        path = self.root / "out" / "dropped_edges.ndjson"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_unresolved_rows_are_never_emitted(self) -> None:
        receipt = self.run_build(
            [
                ledger_row("117:hr:1", "q_a", "unresolved"),
                ledger_row("117:hr:1", "q_b", "include"),
            ],
            [
                {"canonical_key": "117:hr:1", "query_id": "q_a"},
                {"canonical_key": "117:hr:1", "query_id": "q_b"},
            ],
        )
        self.assertEqual(receipt["counts"]["unresolved_skipped"], 1)
        self.assertEqual(
            [row["query_id"] for row in self.emitted()], ["q_b"]
        )

    def test_automated_triage_is_reported_as_automated(self) -> None:
        # An automated inclusion must never be laundered into a decision that
        # the culling engine would treat as human-ratified.
        self.run_build(
            [
                ledger_row(
                    "117:hr:1",
                    "q_a",
                    "include",
                    decision_source="automated_named_outline_inclusion",
                    adjudication_state=None,
                )
            ],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        row = self.emitted()[0]
        self.assertIs(row["automated_final_decision"], True)
        self.assertIsNone(row["adjudication_state"])
        self.assertEqual(row["schema"], MEMBERSHIP_SCHEMA)

    def test_explicit_review_decision_is_not_marked_automated(self) -> None:
        self.run_build(
            [ledger_row("117:hr:1", "q_a", "exclude_false_positive")],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        row = self.emitted()[0]
        self.assertIs(row["automated_final_decision"], False)
        self.assertEqual(
            row["adjudication_state"], "reverified_preexisting_project_decision"
        )

    def test_edges_outside_discovery_universe_are_dropped_and_logged(self) -> None:
        receipt = self.run_build(
            [
                ledger_row("117:hr:1", "q_a", "include"),
                ledger_row("118:hr:9", "q_ghost", "exclude_false_positive"),
            ],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertEqual(
            receipt["counts"]["outside_discovery_universe_dropped"], 1
        )
        self.assertEqual(len(self.emitted()), 1)
        dropped = self.dropped()
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["canonical_key"], "118:hr:9")
        self.assertIn("not in the discovery membership universe", dropped[0]["reason"])

    def test_duplicate_resolved_edges_are_dropped_not_silently_merged(self) -> None:
        receipt = self.run_build(
            [
                ledger_row("117:hr:1", "q_a", "include"),
                ledger_row("117:hr:1", "q_a", "exclude_false_positive"),
            ],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertEqual(receipt["counts"]["duplicate_edges_dropped"], 1)
        self.assertEqual(len(self.emitted()), 1)
        self.assertEqual(len(self.dropped()), 1)

    def test_absence_flag_defaults_to_the_safe_direction(self) -> None:
        # The ledger asserts the policy "absence is never exclusion evidence".
        # When that policy holds, the decision reports that absence was NOT
        # used; when it is false, the decision reports that it was, which
        # blocks finalization.
        self.run_build(
            [ledger_row("117:hr:1", "q_a", "exclude_false_positive")],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertIs(
            self.emitted()[0]["absence_used_as_exclusion_evidence"], False
        )

        self.setUp()
        self.run_build(
            [
                ledger_row(
                    "117:hr:1",
                    "q_a",
                    "exclude_false_positive",
                    article_absence_is_never_exclusion_evidence=False,
                )
            ],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertIs(
            self.emitted()[0]["absence_used_as_exclusion_evidence"], True
        )

    def test_full_text_rule_out_requires_complete_evidence_state(self) -> None:
        self.run_build(
            [ledger_row("117:hr:1", "q_a", "exclude_false_positive")],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertIs(self.emitted()[0]["full_text_rule_out_complete"], False)

        self.setUp()
        self.run_build(
            [
                ledger_row(
                    "117:hr:1",
                    "q_a",
                    "exclude_false_positive",
                    official_text_evidence_state="complete",
                )
            ],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertIs(self.emitted()[0]["full_text_rule_out_complete"], True)

    def test_output_is_deterministic_and_sorted(self) -> None:
        rows = [
            ledger_row("117:hr:2", "q_b", "include"),
            ledger_row("117:hr:1", "q_a", "include"),
        ]
        memberships = [
            {"canonical_key": "117:hr:2", "query_id": "q_b"},
            {"canonical_key": "117:hr:1", "query_id": "q_a"},
        ]
        first = self.run_build(rows, memberships)
        first_sha = first["outputs"]["membership_decisions.ndjson"]["sha256"]
        emitted = [
            (row["canonical_key"], row["query_id"]) for row in self.emitted()
        ]
        self.assertEqual(emitted, sorted(emitted))

        self.setUp()
        second = self.run_build(list(reversed(rows)), memberships)
        self.assertEqual(
            second["outputs"]["membership_decisions.ndjson"]["sha256"],
            first_sha,
        )

    def test_receipt_declares_no_upgrade_and_no_corpus_write(self) -> None:
        receipt = self.run_build(
            [ledger_row("117:hr:1", "q_a", "include")],
            [{"canonical_key": "117:hr:1", "query_id": "q_a"}],
        )
        self.assertEqual(
            receipt["safety"],
            {
                "decisions_upgraded_or_completed": False,
                "unresolved_rows_emitted": False,
                "culling_performed": False,
                "main_corpus_write_performed": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
