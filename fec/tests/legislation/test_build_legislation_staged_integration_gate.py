from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "fec" / "legislation" / "build_legislation_staged_integration_gate.py"
SPEC = importlib.util.spec_from_file_location("staged_integration_gate", MODULE_PATH)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


# The fixture deliberately does NOT read the module's role/derivation tables.
# It reproduces the contract independently so a regression in the module cannot
# quietly rewrite the fixture into agreement with itself.
REQUIRED_INPUT_ROLES: dict[str, tuple[str, ...]] = {
    "coverage_reconciliation": (
        "query_registry",
        "candidate_ledger",
        "membership_ledger",
    ),
    "evidence_completeness": (
        "candidate_evidence_ledger",
        "membership_evidence_ledger",
        "category_registry",
        "category_audit_ledger",
    ),
    "culling_validity_reversibility": (
        "candidate_partition_ledger",
        "membership_partition_ledger",
        "junk_ledger",
    ),
    "role_attribution": ("role_attribution_ledger",),
    "recomputation": ("shadow_recompute_receipt",),
    "regression_currentness_hash_binding": ("final_regression_receipt",),
}

# Small stand-ins for the shipped production counts.  The gate compares the
# declared value to the constant table AND to a value re-derived from the cited
# artifact, so shrinking both together exercises exactly the same code paths
# without writing 350k-row fixtures seven times per test class.
SCALED_COUNTS = {
    "registry_query_count": 3,
    "completed_query_count": 3,
    "candidate_count": 7,
    "membership_count": 11,
    "candidate_evidence_count": 7,
    "membership_evidence_count": 11,
    "category_count": 5,
    "candidate_category_audit_count": 35,
    "candidate_partition_count": 7,
    "membership_partition_count": 11,
    "reviewed_membership_count": 11,
}


class StagedIntegrationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_assertions = copy.deepcopy(GATE.GATE_ASSERTIONS)
        for assertions in GATE.GATE_ASSERTIONS.values():
            for name, value in SCALED_COUNTS.items():
                if name in assertions:
                    assertions[name] = value

    def tearDown(self) -> None:
        GATE.GATE_ASSERTIONS.clear()
        GATE.GATE_ASSERTIONS.update(self._original_assertions)

    # -- fixture helpers ----------------------------------------------------

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_ndjson(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(row, sort_keys=True) + "\n" for row in rows
            ),
            encoding="utf-8",
        )

    def _build_inputs(self, gate_dir: Path) -> dict[str, Path]:
        """Materialize one honest artifact per declared input role."""

        assertions = GATE.GATE_ASSERTIONS
        coverage = assertions["coverage_reconciliation"]
        evidence = assertions["evidence_completeness"]
        culling = assertions["culling_validity_reversibility"]
        paths: dict[str, Path] = {}

        def ndjson(role: str, rows: list[dict[str, object]]) -> None:
            path = gate_dir / f"{role}.ndjson"
            self._write_ndjson(path, rows)
            paths[role] = path

        ndjson(
            "query_registry",
            [
                {"query_id": f"q{index:03d}", "collection_status": "complete"}
                for index in range(coverage["registry_query_count"])
            ],
        )
        ndjson(
            "candidate_ledger",
            [
                {"canonical_key": f"119-hr-{index}"}
                for index in range(coverage["candidate_count"])
            ],
        )
        ndjson(
            "membership_ledger",
            [
                {"canonical_key": f"119-hr-{index}", "member": f"M{index}"}
                for index in range(coverage["membership_count"])
            ],
        )
        ndjson(
            "candidate_evidence_ledger",
            [
                {
                    "canonical_key": f"119-hr-{index}",
                    "required_evidence_status": "complete",
                }
                for index in range(evidence["candidate_evidence_count"])
            ],
        )
        ndjson(
            "membership_evidence_ledger",
            [
                {"canonical_key": f"119-hr-{index}", "evidence": True}
                for index in range(evidence["membership_evidence_count"])
            ],
        )
        ndjson(
            "category_registry",
            [
                {"category_id": f"c{index:02d}"}
                for index in range(evidence["category_count"])
            ],
        )
        ndjson(
            "category_audit_ledger",
            [
                {"canonical_key": f"119-hr-{index}", "category_id": "c00"}
                for index in range(
                    evidence["candidate_category_audit_count"]
                )
            ],
        )
        ndjson(
            "candidate_partition_ledger",
            [
                {"canonical_key": f"119-hr-{index}", "flagged": False}
                for index in range(culling["candidate_partition_count"])
            ],
        )
        ndjson(
            "membership_partition_ledger",
            [
                {
                    "canonical_key": f"119-hr-{index}",
                    "review_disposition": "include",
                    "flagged": False,
                }
                for index in range(culling["membership_partition_count"])
            ],
        )
        ndjson("junk_ledger", [{"canonical_key": "119-hr-0", "junk": False}])
        ndjson(
            "role_attribution_ledger",
            [
                {
                    "canonical_key": f"119-hr-{index}",
                    "role_attribution_status": "resolved",
                }
                for index in range(coverage["membership_count"])
            ],
        )

        shadow = gate_dir / "shadow_recompute_receipt.json"
        self._write_json(
            shadow,
            {
                "schema": "legislation-shadow-recompute-acceptance-v1",
                "acceptance_passed": True,
                "rank_drift_reviewed": True,
            },
        )
        paths["shadow_recompute_receipt"] = shadow

        regression = gate_dir / "final_regression_receipt.json"
        self._write_json(
            regression,
            {
                "schema": "legislation-final-regression-current-v1",
                "status": "PASS_CURRENT",
                "actual_implementation_regression_count": 0,
            },
        )
        paths["final_regression_receipt"] = regression
        return paths

    def _fixture(
        self,
        base: Path,
    ) -> tuple[dict[str, object], dict[str, Path], dict[str, Path]]:
        discovery_sha = "d" * 64
        accepted_sha = "a" * 64
        gate_dir = base / "audits" / "inputs"
        input_paths = self._build_inputs(gate_dir)
        references: dict[str, object] = {}
        receipt_paths: dict[str, Path] = {}
        for gate_id, assertions in GATE.GATE_ASSERTIONS.items():
            bindings: dict[str, str] = {}
            if gate_id in GATE.DISCOVERY_BOUND_GATES:
                bindings["discovery_generation_sha256"] = discovery_sha
            if gate_id in {
                "recomputation",
                "regression_currentness_hash_binding",
            }:
                bindings["accepted_generation_sha256"] = accepted_sha
            input_files = [
                {**GATE.file_receipt(input_paths[role]), "role": role}
                for role in REQUIRED_INPUT_ROLES[gate_id]
            ]
            receipt = {
                "schema": GATE.GATE_RECEIPT_SCHEMA,
                "gate_id": gate_id,
                "status": "PASS",
                "assertions": dict(assertions),
                "bindings": bindings,
                "input_files": input_files,
                "production_write_performed": False,
                "mainframe_write_performed": False,
                "sync_or_promotion_performed": False,
            }
            receipt_path = gate_dir / f"{gate_id}.receipt.json"
            self._write_json(receipt_path, receipt)
            receipt_paths[gate_id] = receipt_path
            references[gate_id] = GATE.file_receipt(receipt_path)

        regression_path = receipt_paths[
            "regression_currentness_hash_binding"
        ]
        regression = json.loads(regression_path.read_text(encoding="utf-8"))
        regression["bindings"]["recomputation_gate_receipt_sha256"] = (
            GATE.sha256_file(receipt_paths["recomputation"])
        )
        self._write_json(regression_path, regression)
        references["regression_currentness_hash_binding"] = GATE.file_receipt(
            regression_path
        )
        request = {
            "schema": GATE.REQUEST_SCHEMA,
            "candidate_count": GATE.EXPECTED_CANDIDATE_COUNT,
            "membership_count": GATE.EXPECTED_MEMBERSHIP_COUNT,
            "safety": {
                "integration_plan_only": True,
                "automatic_promotion_allowed": False,
                "production_write_performed": False,
                "mainframe_write_performed": False,
                "sync_or_promotion_performed": False,
            },
            "gates": references,
        }
        return request, receipt_paths, input_paths

    def _rebind(
        self,
        request: dict[str, object],
        gate_id: str,
        receipt_path: Path,
        receipt: dict[str, object],
    ) -> None:
        """Rewrite a gate receipt and refresh the request's hash reference."""

        self._write_json(receipt_path, receipt)
        gates = request["gates"]
        assert isinstance(gates, dict)
        gates[gate_id] = GATE.file_receipt(receipt_path)

    def _run(
        self, base: Path, request: dict[str, object]
    ) -> tuple[int, dict[str, object], dict[str, object]]:
        request_path = base / "audits" / "request.json"
        self._write_json(request_path, request)
        return GATE.run(
            request_path,
            base / "audits" / "integration",
            workspace_root=base,
        )

    # -- existing behaviour -------------------------------------------------

    def test_complete_current_bundle_produces_plan_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, _paths, _inputs = self._fixture(base)
            code, plan, receipt = self._run(base, request)
            self.assertEqual(plan["blockers"], [])
            self.assertEqual(code, 0)
            self.assertEqual(
                plan["status"], "READY_FOR_EXPLICIT_INTEGRATION_REVIEW"
            )
            self.assertTrue(plan["all_required_gates_passed"])
            self.assertTrue(plan["explicit_human_review_still_required"])
            self.assertFalse(plan["automatic_promotion_allowed"])
            self.assertFalse(plan["production_write_authorized"])
            self.assertFalse(plan["mainframe_write_authorized"])
            self.assertIsNone(plan["promotion_command"])
            self.assertFalse(receipt["sync_or_promotion_performed"])
            self.assertEqual(
                receipt["plan"]["sha256"],
                GATE.sha256_file(
                    base / "audits" / "integration" / "integration_plan.json"
                ),
            )

    def test_missing_gate_fails_closed_and_still_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, _paths, _inputs = self._fixture(base)
            del request["gates"]["role_attribution"]
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertEqual(plan["status"], "BLOCKED")
            self.assertIn("missing required gate: role_attribution", plan["blockers"])
            self.assertTrue(
                (base / "audits" / "integration" / "receipt.json").is_file()
            )
            self.assertFalse(plan["mainframe_write_performed"])

    def test_stale_cited_input_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, _inputs = self._fixture(base)
            coverage = json.loads(
                paths["coverage_reconciliation"].read_text(encoding="utf-8")
            )
            input_path = Path(coverage["input_files"][0]["path"])
            input_path.write_text('{"tampered":true}\n', encoding="utf-8")
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertTrue(
                any(
                    "coverage_reconciliation.input_files[0]" in item
                    for item in plan["blockers"]
                )
            )

    def test_false_semantic_assertion_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, _inputs = self._fixture(base)
            culling_path = paths["culling_validity_reversibility"]
            culling = json.loads(culling_path.read_text(encoding="utf-8"))
            culling["assertions"]["junk_is_reversible"] = False
            self._rebind(
                request, "culling_validity_reversibility", culling_path, culling
            )
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertIn(
                "culling_validity_reversibility: assertion "
                "junk_is_reversible expected True, found False",
                plan["blockers"],
            )

    def test_cross_gate_discovery_hash_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, _inputs = self._fixture(base)
            evidence_path = paths["evidence_completeness"]
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["bindings"]["discovery_generation_sha256"] = "e" * 64
            self._rebind(request, "evidence_completeness", evidence_path, evidence)
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertIn(
                "discovery generation binding differs across gates",
                plan["blockers"],
            )

    def test_regression_must_bind_current_recomputation_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, _inputs = self._fixture(base)
            regression_path = paths["regression_currentness_hash_binding"]
            regression = json.loads(regression_path.read_text(encoding="utf-8"))
            regression["bindings"]["recomputation_gate_receipt_sha256"] = "f" * 64
            self._rebind(
                request,
                "regression_currentness_hash_binding",
                regression_path,
                regression,
            )
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertIn(
                "regression receipt is not bound to the current "
                "recomputation gate receipt",
                plan["blockers"],
            )

    def test_output_is_restricted_to_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, _paths, _inputs = self._fixture(base)
            request_path = base / "audits" / "request.json"
            self._write_json(request_path, request)
            with self.assertRaises(GATE.GateError):
                GATE.run(
                    request_path,
                    base / "production",
                    workspace_root=base,
                )

    # -- DEFECT 1: assertions must be derived, not declared ------------------

    def test_forged_zero_unresolved_membership_is_caught_by_rederivation(
        self,
    ) -> None:
        """The headline hole: a receipt swearing 0 unresolved rows.

        The receipt keeps every declared value the constant table demands and
        every cited hash stays current.  Only the ledger it points at disagrees
        - it actually holds unreviewed membership.  Believing the declaration
        is exactly the failure mode that let a hand-authored receipt reach
        READY while the real ledger held 64,004 unresolved rows.
        """

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, inputs = self._fixture(base)
            ledger = inputs["membership_partition_ledger"]
            rows = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for row in rows[:4]:
                row["review_disposition"] = None
            self._write_ndjson(ledger, rows)

            culling_path = paths["culling_validity_reversibility"]
            culling = json.loads(culling_path.read_text(encoding="utf-8"))
            culling["input_files"] = [
                {**GATE.file_receipt(inputs[role]), "role": role}
                for role in REQUIRED_INPUT_ROLES[
                    "culling_validity_reversibility"
                ]
            ]
            self._rebind(
                request, "culling_validity_reversibility", culling_path, culling
            )

            code, plan, _receipt = self._run(base, request)
            self.assertEqual(plan["status"], "BLOCKED")
            self.assertEqual(code, 3)
            self.assertTrue(
                any(
                    "unresolved_membership_count" in item
                    and "derives to 4" in item
                    for item in plan["blockers"]
                ),
                plan["blockers"],
            )

    def test_forged_regression_failure_count_is_caught_by_rederivation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, inputs = self._fixture(base)
            receipt_file = inputs["final_regression_receipt"]
            self._write_json(
                receipt_file,
                {
                    "schema": "legislation-final-regression-current-v1",
                    "status": "FAIL_IMPLEMENTATION_REGRESSIONS",
                    "actual_implementation_regression_count": 6,
                },
            )
            regression_path = paths["regression_currentness_hash_binding"]
            regression = json.loads(
                regression_path.read_text(encoding="utf-8")
            )
            regression["input_files"] = [
                {**GATE.file_receipt(inputs[role]), "role": role}
                for role in REQUIRED_INPUT_ROLES[
                    "regression_currentness_hash_binding"
                ]
            ]
            self._rebind(
                request,
                "regression_currentness_hash_binding",
                regression_path,
                regression,
            )
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(plan["status"], "BLOCKED")
            self.assertEqual(code, 3)
            self.assertTrue(
                any(
                    "regression_failure_count" in item and "derives to 6" in item
                    for item in plan["blockers"]
                ),
                plan["blockers"],
            )
            self.assertTrue(
                any(
                    "full_regression_passed" in item for item in plan["blockers"]
                ),
                plan["blockers"],
            )

    def test_input_without_a_declared_role_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, _inputs = self._fixture(base)
            path = paths["role_attribution"]
            receipt = json.loads(path.read_text(encoding="utf-8"))
            for item in receipt["input_files"]:
                item.pop("role", None)
            self._rebind(request, "role_attribution", path, receipt)
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertTrue(
                any(
                    "must name the role they play" in item
                    for item in plan["blockers"]
                ),
                plan["blockers"],
            )

    def test_missing_required_input_role_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, paths, _inputs = self._fixture(base)
            path = paths["coverage_reconciliation"]
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt["input_files"] = [
                item
                for item in receipt["input_files"]
                if item.get("role") != "membership_ledger"
            ]
            self._rebind(request, "coverage_reconciliation", path, receipt)
            code, plan, _receipt = self._run(base, request)
            self.assertEqual(code, 3)
            self.assertIn(
                "coverage_reconciliation: required input role "
                "membership_ledger is absent or unusable; its assertions "
                "cannot be derived",
                plan["blockers"],
            )
            self.assertTrue(
                any(
                    "declared-not-derived" in item for item in plan["blockers"]
                ),
                plan["blockers"],
            )

    def test_plan_records_derived_versus_declared_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            request, _paths, _inputs = self._fixture(base)
            _code, plan, receipt = self._run(base, request)
            provenance = plan["assertion_provenance"]
            self.assertTrue(provenance["all_load_bearing_assertions_derived"])
            self.assertEqual(provenance["declared_not_derived_assertions"], [])
            self.assertEqual(
                provenance["derived_assertion_count"],
                provenance["load_bearing_assertion_count"],
            )
            self.assertGreater(provenance["load_bearing_assertion_count"], 0)
            self.assertEqual(receipt["assertion_provenance"], provenance)
            culling = plan["gates"]["culling_validity_reversibility"]
            self.assertEqual(
                culling["derived_assertions"]["unresolved_membership_count"][
                    "input_role"
                ],
                "membership_partition_ledger",
            )

    def test_every_numeric_assertion_is_load_bearing_and_has_a_derivation(
        self,
    ) -> None:
        for gate_id, assertions in self._original_assertions.items():
            self.assertIn(gate_id, GATE.GATE_REQUIRED_INPUT_ROLES)
            self.assertEqual(
                REQUIRED_INPUT_ROLES[gate_id],
                GATE.GATE_REQUIRED_INPUT_ROLES[gate_id],
            )
            derivations = GATE.GATE_DERIVATIONS.get(gate_id, {})
            for name, expected in assertions.items():
                if isinstance(expected, bool) or not isinstance(expected, int):
                    continue
                self.assertIn(name, GATE.load_bearing_assertions(gate_id))
                self.assertIn(name, derivations)
                role, method, _params = derivations[name]
                self.assertIn(role, GATE.GATE_REQUIRED_INPUT_ROLES[gate_id])
                self.assertIn(method, GATE.DERIVATION_METHODS)


if __name__ == "__main__":
    unittest.main()
