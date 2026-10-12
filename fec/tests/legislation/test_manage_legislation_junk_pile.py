import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import manage_legislation_junk_pile as junk


T0 = "2026-07-26T20:00:00Z"
T1 = "2026-07-26T20:01:00Z"
EVIDENCE = [{"source": "official_text", "finding": "unrelated subject"}]
PROVENANCE = [{"source_url": "https://www.congress.gov/bill/119th-congress/house-bill/1"}]


def exclude(root: Path, candidate_id: str = "119:hr:1") -> dict:
    return junk.append_exclusion(
        root,
        candidate_id=candidate_id,
        candidate_payload={
            "canonical_key": candidate_id,
            "title": "Fixture bill",
            "memberships": ["fixture-query"],
        },
        rule_id="strict-subject-mismatch",
        rule_version="1",
        reason="Official text addresses an unrelated subject.",
        evidence=EVIDENCE,
        provenance=PROVENANCE,
        decided_by="fixture-reviewer",
        decided_at=T0,
        decision_id=f"exclude-{candidate_id}",
    )


class JunkPileTests(unittest.TestCase):
    def test_empty_initialization_culls_nothing_and_exports_pass_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            receipt_path = junk.export_current(root, audited_at=T0)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["counts"]["current_junk_pile"], 0)
            self.assertFalse(
                receipt["policy"]["scoring_sync_promotion_authorized"]
            )
            self.assertEqual((root / junk.DECISIONS_NAME).read_bytes(), b"")
            self.assertEqual((root / junk.RESTORATIONS_NAME).read_bytes(), b"")

    def test_exclusion_preserves_full_payload_rule_evidence_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            row = exclude(root)
            state = junk.compute_effective_state(root)
            self.assertEqual(len(state["current"]), 1)
            self.assertEqual(
                state["current"][0]["candidate_evidence"]["payload"]["title"],
                "Fixture bill",
            )
            self.assertEqual(row["rule"]["id"], "strict-subject-mismatch")
            self.assertEqual(row["evidence"], EVIDENCE)
            self.assertEqual(row["provenance"], PROVENANCE)
            self.assertTrue(row["candidate_retained_in_discovery_corpus"])

    def test_restoration_is_separate_and_returns_candidate_to_pool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            decision = exclude(root)
            junk.append_restoration(
                root,
                candidate_id="119:hr:1",
                target_decision_id=decision["decision_id"],
                reason="Human review found relevant operative language.",
                evidence=[{"finding": "section 4 is in scope"}],
                provenance=[{"review": "manual-second-pass"}],
                restored_by="fixture-reviewer",
                restored_at=T1,
                restoration_id="restore-119-hr-1",
            )
            state = junk.compute_effective_state(root)
            self.assertEqual(state["current"], [])
            self.assertEqual(len(state["restored"]), 1)
            self.assertEqual(len(state["exclusions"]), 1)
            self.assertEqual(len(state["restorations"]), 1)

    def test_duplicate_exclusion_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            exclude(root)
            with self.assertRaisesRegex(junk.JunkPileError, "already has"):
                exclude(root)

    def test_duplicate_restoration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            decision = exclude(root)
            kwargs = dict(
                candidate_id="119:hr:1",
                target_decision_id=decision["decision_id"],
                reason="Restore after review.",
                evidence=[{"finding": "relevant"}],
                provenance=[{"review": "manual"}],
                restored_by="fixture",
                restored_at=T1,
            )
            junk.append_restoration(
                root, restoration_id="restore-one", **kwargs
            )
            with self.assertRaisesRegex(junk.JunkPileError, "already restored"):
                junk.append_restoration(
                    root, restoration_id="restore-two", **kwargs
                )

    def test_pointer_hash_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            source = Path(directory) / "candidate.ndjson"
            source.write_text('{"canonical_key":"119:s:2"}\n', encoding="utf-8")
            junk.initialize(root, created_at=T0)
            junk.append_exclusion(
                root,
                candidate_id="119:s:2",
                pointer_only=True,
                source_path=source,
                source_locator={"kind": "ndjson_line", "line": 1},
                rule_id="fixture",
                rule_version="1",
                reason="Fixture exclusion.",
                evidence=EVIDENCE,
                provenance=PROVENANCE,
                decided_by="fixture",
                decided_at=T0,
                decision_id="exclude-pointer",
            )
            source.write_text('{"canonical_key":"119:s:999"}\n', encoding="utf-8")
            with self.assertRaisesRegex(junk.JunkPileError, "source hash drift"):
                junk.compute_effective_state(root)

    def test_record_mutation_fails_self_hash_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            exclude(root)
            ledger = root / junk.DECISIONS_NAME
            row = json.loads(ledger.read_text(encoding="utf-8"))
            row["reason"] = "silently changed"
            ledger.write_bytes(junk.canonical_json_bytes(row) + b"\n")
            with self.assertRaisesRegex(junk.JunkPileError, "content hash drift"):
                junk.compute_effective_state(root)

    def test_historical_receipt_blocks_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            exclude(root)
            junk.export_current(root, audited_at=T1)
            (root / junk.DECISIONS_NAME).write_bytes(b"")
            with self.assertRaisesRegex(junk.JunkPileError, "truncation"):
                junk.compute_effective_state(root)

    def test_repeated_export_of_same_state_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "junk"
            junk.initialize(root, created_at=T0)
            exclude(root)
            first = junk.export_current(root, audited_at=T1)
            before = hashlib.sha256(first.read_bytes()).hexdigest()
            second = junk.export_current(
                root, audited_at="2026-07-26T21:00:00Z"
            )
            self.assertEqual(first, second)
            self.assertEqual(before, hashlib.sha256(second.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
