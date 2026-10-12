from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "fec" / "audit" / "audit_legislation_decay_propagation.py"
SPEC = importlib.util.spec_from_file_location("decay_audit", SOURCE)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class DecayPropagationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.value = AUDIT.build_audit()

    def test_staged_and_frozen_schedules_remain_distinct(self) -> None:
        self.assertEqual(self.value["schedules"]["staged_unapproved"], AUDIT.STAGED)
        self.assertEqual(self.value["schedules"]["frozen_live"], AUDIT.FROZEN)
        self.assertFalse(self.value["schedules"]["schedules_equal"])
        self.assertEqual(
            self.value["staged_consumer_using_old_schedule"], []
        )

    def test_all_sixteen_consumers_have_explicit_decay_semantics(self) -> None:
        rows = self.value["downstream_consumer_ledger"]
        self.assertEqual(len(rows), 16)
        self.assertEqual(len({row["stage"] for row in rows}), 16)
        self.assertEqual(
            {row["mode"] for row in rows},
            {"direct", "mixed", "inherited", "intentionally_absent"},
        )
        absent = {
            row["stage"]
            for row in rows
            if row["mode"] == "intentionally_absent"
        }
        self.assertEqual(
            absent, {"recompute_say_do", "recompute_say_do_second_pass"}
        )

    def test_manual_ltr_is_canonicalized_without_mutating_stored_provenance(
        self,
    ) -> None:
        manual = self.value["manual_LTR_evidence"]
        self.assertTrue(manual["stored_values_match_prior_live_schedule"])
        self.assertTrue(manual["stored_values_are_not_staged_authority"])
        self.assertEqual(manual["staged_schedule_applied"], AUDIT.STAGED)

    def test_production_is_frozen_and_not_cut_over(self) -> None:
        production = self.value["production_baseline"]
        self.assertTrue(production["passed"])
        self.assertEqual(production["declared_file_count"], 55)
        self.assertEqual(production["frozen_copy_hash_match_count"], 55)
        self.assertEqual(production["frozen_copy_mismatches"], [])
        # The immutable copies remain the production authority. Deliberate
        # staged source changes are visible drift, not baseline tampering.
        self.assertGreaterEqual(production["live_source_drift_count"], 1)
        self.assertTrue(
            production["live_source_drift_is_not_baseline_tampering"]
        )
        self.assertFalse(
            production["production_cutover_to_staged_schedule_detected"]
        )
        self.assertEqual(
            production["bill_rules"]["computed_schedule"], AUDIT.FROZEN
        )

    def test_artifact_is_hash_bound_and_workflow_snapshot_is_not_authority(
        self,
    ) -> None:
        self.assertEqual(self.value["status"], "PASS")
        self.assertTrue(self.value["read_only_source_audit"])
        self.assertFalse(self.value["promotion_authorized"])
        context = self.value["acceptance_context"]
        self.assertTrue(context["snapshot_only"])
        self.assertFalse(context["authoritative_for_policy_approval"])
        self.assertFalse(context["authoritative_for_shadow_or_sync"])
        self.assertFalse(context["sync_allowed"])
        self.assertEqual(self.value["blocking_issues"], [])
        self.assertEqual(len(self.value["audit_basis"]["sha256"]), 64)
        with tempfile.TemporaryDirectory(dir=ROOT / "audits" / "legfix5") as tmp:
            out = Path(tmp)
            AUDIT.write_artifacts(out, self.value)
            written = json.loads(
                (out / "decay_propagation_audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                written["audit_basis"]["sha256"],
                self.value["audit_basis"]["sha256"],
            )
            self.assertTrue((out / "DECAY_PROPAGATION_AUDIT.md").is_file())


if __name__ == "__main__":
    unittest.main()
