from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fec.audit import audit_legislation_final_regression_currentness as audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def receipt(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


class FinalRegressionCurrentnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "fec" / "tests").mkdir(parents=True)
        (self.root / "fec" / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "fec" / "tests" / "test_worker.py").write_text(
            "def test_value(): pass\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _graph(self) -> tuple[Path, Path, Path]:
        historical_path = self.root / "historical" / "receipt.json"
        report_path = historical_path.with_name("report.md")
        report_path.parent.mkdir(parents=True)
        report_path.write_text("# Historical\n", encoding="utf-8")

        prior = self.root / "strict-prior.json"
        selected = self.root / "strict-current.json"
        timeout = self.root / "raw" / "timeout.json"
        timeout.parent.mkdir()
        write_json(prior, {"queries": 22})
        write_json(timeout, {"classification": "timeout"})
        write_json(
            selected,
            {
                "automatic_verification_timeout": {
                    "receipt_file": "raw/timeout.json",
                    "receipt_sha256": hashlib.sha256(timeout.read_bytes()).hexdigest(),
                    "interaction_performed": False,
                    "captcha_bypass_attempted": False,
                    "collection_stopped_without_result_acceptance": True,
                }
            },
        )

        producer = self.root / "fec" / "rollover.py"
        producer.write_text("# rollover\n", encoding="utf-8")
        partition = self.root / "partition.json"
        config = self.root / "config.json"
        write_json(partition, {"closed": True})
        write_json(config, {"staged": True})

        specificity = self.root / "specificity.json"
        write_json(
            specificity,
            {
                "schema": "legislation-query-specificity-audit-v1",
                "status": "PASS",
                "primary_discovery": "Congress.gov Advanced Search - Legislation",
                "quorum_used": False,
                "counts": {"generic_bare_act_category_terms": 0},
                "inputs": {"selected_strict_manifest": receipt(selected)},
            },
        )
        presync = self.root / "presync.json"
        write_json(
            presync,
            {
                "schema": "legislation-presync-audit-checkpoint-v1",
                "status": "BLOCKED",
                "staged_only": True,
                "production_or_mainframe_written": False,
                "blockers": [{"code": "coverage"}],
                "coverage": {
                    "strict_manifest": receipt(selected),
                    "strict_query_count": 22,
                },
            },
        )
        readiness = self.root / "readiness.json"
        write_json(
            readiness,
            {
                "schema": "legislation-downstream-readiness-v4",
                "sync_allowed": False,
                "hard_freeze": True,
                "production_is_read_only": True,
                "gates": {"coverage": False},
                "coverage": {"selected_discovery_manifest": receipt(selected)},
            },
        )
        plan = self.root / "plan.json"
        augmentation = self.root / "augmentation.json"
        write_json(plan, {"current": True})
        write_json(augmentation, {"authority": False})

        old_sha = "1" * 64
        historical = {
            "schema": audit.HISTORICAL_SCHEMA,
            "status": "BLOCKED_ACCEPTANCE_GATES_NO_IMPLEMENTATION_REGRESSIONS",
            "gate_hashes": {
                "strict_manifest_sha256": receipt(prior)["sha256"],
                "query_specificity_sha256": "2" * 64,
                "billstatus_snapshot_plan_sha256": "3" * 64,
                "structured_augmentation_pointer_sha256": "4" * 64,
                "presync_checkpoint_sha256": "5" * 64,
                "downstream_readiness_sha256": old_sha,
            },
            "authority": {
                "quorum_used": False,
                "scoring_authority": False,
                "scoring_input_allowed": False,
                "sync_or_promotion_performed": False,
                "production_or_mainframe_written": False,
            },
        }
        write_json(historical_path, historical)

        rollover_dir = self.root / "rollover" / ("a" * 64)
        rollover_path = rollover_dir / "rollover_receipt.json"
        outputs = {
            "query_specificity_current": receipt(specificity),
            "presync_checkpoint": receipt(presync),
            "downstream_readiness": receipt(readiness),
            "billstatus_snapshot_plan": receipt(plan),
            "structured_augmentation_pointer": receipt(augmentation),
        }
        rollover = {
            "schema": audit.ROLLOVER_RECEIPT_SCHEMA,
            "status": "PASS",
            "rollover_id": "a" * 64,
            "network_executed": False,
            "collector_process_modified": False,
            "candidate_exclusion_performed": False,
            "scoring_performed": False,
            "sync_or_promotion_performed": False,
            "production_write_performed": False,
            "input_address": {
                "prior_strict_manifest": receipt(prior),
                "selected_strict_manifest": receipt(selected),
                "partition_audit": receipt(partition),
                "presync_config": receipt(config),
                "producers": {"rollover": receipt(producer)},
                "timeout_receipt": {"sha256": receipt(timeout)["sha256"]},
            },
            "outputs": outputs,
        }
        write_json(rollover_path, rollover)
        pointer_path = self.root / "rollover" / "current.json"
        write_json(
            pointer_path,
            {
                "schema": audit.ROLLOVER_POINTER_SCHEMA,
                "rollover_id": "a" * 64,
                "producer": receipt(producer),
                "selected_strict_manifest": receipt(selected),
                "receipt": receipt(rollover_path),
                "network_executed": False,
                "sync_or_promotion_performed": False,
            },
        )
        return historical_path, report_path, pointer_path

    def _current_regression_pointer(self) -> Path:
        runner = self.root / "fec" / "legislation" / "run_legislation_final_regression.py"
        # The fixture models the Phase 3 nested layout; the package dir must exist.
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_text("# current runner\n", encoding="utf-8")
        inventory = audit.current_implementation_test_inventory(self.root)
        entries = [
            {
                "path": item["path"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
            for item in inventory["entries"]
        ]
        digest = audit.stable_sha256(entries)
        bound_inventory = {
            "scope": "test fixture",
            "entry_count": len(entries),
            "inventory_sha256": digest,
            "entries": entries,
        }
        input_address = {
            "runner": receipt(runner),
            "implementation_test_inventory_sha256": digest,
        }
        run_id = audit.stable_sha256(input_address)
        full_receipt_path = self.root / "current-regression" / run_id / "receipt.json"
        full_receipt = {
            "schema": audit.CURRENT_REGRESSION_SCHEMA,
            "run_id": run_id,
            "status": "BLOCKED_ACCEPTANCE_GATES_NO_IMPLEMENTATION_REGRESSIONS",
            "input_address": input_address,
            "source_test_inventory_before": bound_inventory,
            "source_test_inventory_after": bound_inventory,
            "source_test_inventory_stable": True,
            "compilation": {"failed": 0},
            "python_regression_suite": {
                "implementation_regression_failures": [],
            },
            "node_regression_suite": {"exit_code": 0},
            "actual_implementation_regression_count": 0,
            "explicit_acceptance_blocker_failure_count": 1,
            "authority": {
                "primary_discovery_instrument": (
                    "Congress.gov Advanced Search - Legislation"
                ),
                "quorum_used": False,
                "test_currentness_authority": True,
            },
            "safety": {
                "network_executed": False,
                "collector_process_modified": False,
                "candidate_exclusion_performed": False,
                "scoring_performed": False,
                "production_write_performed": False,
                "sync_or_promotion_performed": False,
            },
        }
        write_json(full_receipt_path, full_receipt)
        pointer_path = self.root / "current-regression" / "current.json"
        write_json(
            pointer_path,
            {
                "schema": audit.CURRENT_REGRESSION_POINTER_SCHEMA,
                "status": full_receipt["status"],
                "run_id": run_id,
                "runner": receipt(runner),
                "receipt": receipt(full_receipt_path),
                "source_test_inventory_sha256": digest,
                "source_test_inventory_stable": True,
                "actual_implementation_regression_count": 0,
                "explicit_acceptance_blocker_failure_count": 1,
                "network_executed": False,
                "scoring_performed": False,
                "production_write_performed": False,
                "sync_or_promotion_performed": False,
            },
        )
        return pointer_path

    def test_valid_semantic_rollover_still_blocks_unbound_historical_tests(self) -> None:
        historical, report, pointer = self._graph()
        result = audit.build_audit(
            historical_receipt_path=historical,
            historical_report_path=report,
            rollover_pointer_path=pointer,
            current_regression_pointer_path=self.root / "absent-current.json",
            inventory_root=self.root,
        )
        self.assertEqual(
            result["status"], "BLOCKED_UNBOUND_HISTORICAL_TEST_INVENTORY"
        )
        self.assertTrue(result["semantic_chain"]["semantic_gate_rollover_valid"])
        self.assertFalse(
            result["current_implementation_test_inventory"][
                "historical_tests_certified_current"
            ]
        )
        self.assertEqual(
            result["current_implementation_test_inventory"]["entry_count"], 3
        )

    def test_tampered_current_gate_fails_closed(self) -> None:
        historical, report, pointer = self._graph()
        pointer_value = json.loads(pointer.read_text(encoding="utf-8"))
        rollover_path = Path(pointer_value["receipt"]["path"])
        rollover = json.loads(rollover_path.read_text(encoding="utf-8"))
        current_gate = Path(
            rollover["outputs"]["query_specificity_current"]["path"]
        )
        current_gate.write_text("{}\n", encoding="utf-8")
        result = audit.build_audit(
            historical_receipt_path=historical,
            historical_report_path=report,
            rollover_pointer_path=pointer,
            current_regression_pointer_path=self.root / "absent-current.json",
            inventory_root=self.root,
        )
        self.assertEqual(result["status"], "FAIL_CLOSED_CURRENTNESS_VALIDATION")
        self.assertFalse(result["semantic_chain"]["semantic_gate_rollover_valid"])
        self.assertTrue(
            any("query_specificity_current" in issue for issue in result["validation_errors"])
        )

    def test_writer_emits_hash_current_pointer_without_rewriting_history(self) -> None:
        historical, report, rollover_pointer = self._graph()
        before = historical.read_bytes()
        result = audit.build_audit(
            historical_receipt_path=historical,
            historical_report_path=report,
            rollover_pointer_path=rollover_pointer,
            current_regression_pointer_path=self.root / "absent-current.json",
            inventory_root=self.root,
        )
        output = self.root / "output" / "currentness.json"
        pointer = self.root / "output" / "current.json"
        value = audit.write_audit(result, output_path=output, pointer_path=pointer)
        self.assertEqual(before, historical.read_bytes())
        self.assertTrue(audit.validate_file_receipt(value["audit"], expected=output)[0])
        self.assertFalse(value["historical_tests_certified_current"])
        self.assertFalse(value["current_tests_certified_current"])
        self.assertFalse(value["network_executed"])

    def test_current_full_regression_supersedes_unbound_historical_inventory(
        self,
    ) -> None:
        historical, report, rollover_pointer = self._graph()
        current_pointer = self._current_regression_pointer()
        result = audit.build_audit(
            historical_receipt_path=historical,
            historical_report_path=report,
            rollover_pointer_path=rollover_pointer,
            current_regression_pointer_path=current_pointer,
            inventory_root=self.root,
        )
        self.assertEqual(result["status"], "CURRENT")
        self.assertTrue(result["semantic_chain"]["semantic_gate_rollover_valid"])
        self.assertTrue(
            result["current_implementation_test_inventory"][
                "current_tests_certified_current"
            ]
        )
        self.assertFalse(
            result["current_implementation_test_inventory"][
                "historical_tests_certified_current"
            ]
        )
        self.assertEqual(result["blockers"], [])


if __name__ == "__main__":
    unittest.main()
