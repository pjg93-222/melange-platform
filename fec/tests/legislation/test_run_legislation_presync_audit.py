from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from audit.drift_closure import CHANGED_COUNT, TOTAL, UNCHANGED_COUNT


MODULE_PATH = FEC / "legislation" / "run_legislation_presync_audit.py"
SPEC = importlib.util.spec_from_file_location("presync", MODULE_PATH)
assert SPEC and SPEC.loader
PRESYNC = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PRESYNC
SPEC.loader.exec_module(PRESYNC)


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def byte_receipt(path: Path) -> dict[str, object]:
    receipt = PRESYNC.file_receipt(path)
    return {
        "path": receipt["path"],
        "bytes": receipt["size"],
        "sha256": receipt["sha256"],
    }


def write_ndjson(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return path


def write_downstream_drift_fixture(root: Path) -> Path:
    # Fixture models the re-attested pinned live closure as of 2026-08-05;
    # its split comes from audit.drift_closure.
    audit_id = "a" * 64
    out_root = root / "post_weekly_refresh_downstream_drift"
    snapshot = out_root / "snapshots" / audit_id
    file_rows: list[dict[str, object]] = []
    gap_rows: list[dict[str, object]] = []
    for index in range(TOTAL):
        changed = index < CHANGED_COUNT
        name = f"artifact-{index:02d}.json"
        file_rows.append(
            {
                "schema": PRESYNC.DOWNSTREAM_DRIFT_FILE_SCHEMA,
                "name": name,
                "changed": changed,
                "byte_identical": not changed,
                "attribution": (
                    "external_scheduled_weekly_refresh_observation"
                    if changed
                    else "frozen_live_byte_identity"
                ),
                "potentially_legislation_bearing": changed,
                "accepted_legislation_recomputation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
            }
        )
        if changed:
            gap_rows.append(
                {
                    "schema": PRESYNC.DOWNSTREAM_DRIFT_GAP_SCHEMA,
                    "name": name,
                    "potentially_legislation_bearing": True,
                    "accepted_legislation_recomputation": False,
                    "authoritative_for_sync_or_promotion": False,
                }
            )
    file_output = write_ndjson(snapshot / "file_drift.ndjson", file_rows)
    gap_output = write_ndjson(
        snapshot / "semantic_gap_ledger.ndjson",
        gap_rows,
    )
    file_output_receipt = byte_receipt(file_output)
    file_output_receipt["record_count"] = TOTAL
    gap_output_receipt = byte_receipt(gap_output)
    gap_output_receipt["record_count"] = CHANGED_COUNT
    manifest = write_json(
        snapshot / "manifest.json",
        {
            "schema": PRESYNC.DOWNSTREAM_DRIFT_MANIFEST_SCHEMA,
            "audit_id": audit_id,
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "outputs": {
                "file_drift": file_output_receipt,
                "semantic_gap_ledger": gap_output_receipt,
            },
            "summary": {
                "declared_file_count": TOTAL,
                "changed_file_count": CHANGED_COUNT,
                "unchanged_file_count": UNCHANGED_COUNT,
                "potentially_legislation_bearing_changed_count": (
                    CHANGED_COUNT
                ),
                "accepted_legislation_recomputation_count": 0,
            },
            "all_changed_consumers_potentially_legislation_bearing": True,
            "workbook_cell_level_semantic_comparison_resolved": False,
            "legislation_recomputation_accepted": False,
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
            "network_executed": False,
        },
    )
    return write_json(
        out_root / "current.json",
        {
            "schema": PRESYNC.DOWNSTREAM_DRIFT_POINTER_SCHEMA,
            "audit_id": audit_id,
            "snapshot_dir": str(snapshot.resolve()),
            "manifest": byte_receipt(manifest),
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "sync_or_promotion_performed": False,
        },
    )


def reseal_downstream_drift_manifest(pointer_path: Path) -> None:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest = Path(pointer["manifest"]["path"])
    pointer["manifest"] = byte_receipt(manifest)
    write_json(pointer_path, pointer)


def clone_workbook_ooxml_fixture(root: Path) -> Path:
    source_pointer_path = (
        MODULE_PATH.parents[2]
        / "audits"
        / "legfix5"
        / "post_weekly_refresh_workbook_ooxml_semantics"
        / "current.json"
    )
    source_pointer = json.loads(
        source_pointer_path.read_text(encoding="utf-8")
    )
    source_manifest = json.loads(
        Path(source_pointer["manifest"]["path"]).read_text(encoding="utf-8")
    )
    audit_id = source_pointer["audit_id"]
    out_root = root / "post_weekly_refresh_workbook_ooxml_semantics"
    snapshot = out_root / "snapshots" / audit_id
    snapshot.mkdir(parents=True)
    for filename, receipt in source_manifest["outputs"].items():
        destination = snapshot / filename
        shutil.copyfile(Path(receipt["path"]), destination)
        cloned_receipt = byte_receipt(destination)
        cloned_receipt["record_count"] = receipt["record_count"]
        source_manifest["outputs"][filename] = cloned_receipt
    manifest_path = write_json(snapshot / "manifest.json", source_manifest)
    source_pointer["snapshot_dir"] = str(snapshot.resolve())
    source_pointer["manifest"] = byte_receipt(manifest_path)
    return write_json(out_root / "current.json", source_pointer)


def reseal_workbook_ooxml_manifest(pointer_path: Path) -> None:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest_path = Path(pointer["manifest"]["path"])
    pointer["manifest"] = byte_receipt(manifest_path)
    write_json(pointer_path, pointer)


def clone_planning_dashboard_fixture(root: Path) -> Path:
    source_pointer_path = (
        MODULE_PATH.parents[2]
        / "audits"
        / "legfix5"
        / "post_weekly_refresh_planning_dashboard_semantics"
        / "current.json"
    )
    source_pointer = json.loads(
        source_pointer_path.read_text(encoding="utf-8")
    )
    source_manifest = json.loads(
        Path(source_pointer["manifest"]["path"]).read_text(encoding="utf-8")
    )
    audit_id = source_pointer["audit_id"]
    out_root = root / "post_weekly_refresh_planning_dashboard_semantics"
    snapshot = out_root / "snapshots" / audit_id
    snapshot.mkdir(parents=True)
    for label, receipt in source_manifest["outputs"].items():
        destination = snapshot / Path(receipt["path"]).name
        shutil.copyfile(Path(receipt["path"]), destination)
        cloned_receipt = byte_receipt(destination)
        cloned_receipt["record_count"] = receipt["record_count"]
        source_manifest["outputs"][label] = cloned_receipt
    manifest_path = write_json(snapshot / "manifest.json", source_manifest)
    source_pointer["snapshot_dir"] = str(snapshot.resolve())
    source_pointer["manifest"] = byte_receipt(manifest_path)
    return write_json(out_root / "current.json", source_pointer)


def reseal_planning_dashboard_manifest(pointer_path: Path) -> None:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest_path = Path(pointer["manifest"]["path"])
    pointer["manifest"] = byte_receipt(manifest_path)
    write_json(pointer_path, pointer)


def clone_formula_cache_fixture(
    root: Path,
    *,
    engine_mutation: str | None = None,
) -> Path:
    source_pointer_path = (
        MODULE_PATH.parents[2]
        / "audits"
        / "legfix5"
        / "formula_cache_freshness"
        / "current.json"
    )
    pointer = json.loads(
        source_pointer_path.read_text(encoding="utf-8")
    )
    manifest = json.loads(
        Path(pointer["manifest"]["path"]).read_text(encoding="utf-8")
    )
    receipt_root = root / "calculation_receipts"
    receipt_root.mkdir(parents=True)
    for calculation_id in ("a", "b"):
        calculation = manifest["independent_calculations"][calculation_id]
        engine_receipt = json.loads(
            Path(calculation["receipt"]["path"]).read_text(
                encoding="utf-8-sig"
            )
        )
        if calculation_id == "a" and engine_mutation == "temp_source_hash":
            engine_receipt["temporary_copy_before"]["sha256"] = "0" * 64
        if calculation_id == "a" and engine_mutation == "sync_flag":
            engine_receipt["sync_or_promotion_performed"] = True
        cloned_receipt = write_json(
            receipt_root / f"{calculation_id}.json",
            engine_receipt,
        )
        calculation["receipt"] = byte_receipt(cloned_receipt)
    identity = {
        "schema": PRESYNC.FORMULA_CACHE_FRESHNESS.SCHEMA,
        "source_sha256": manifest["source_workbook"]["sha256"],
        "calculated_a_sha256": manifest[
            "calculated_formula_cache_inventories"
        ]["a"]["workbook"]["sha256"],
        "calculated_b_sha256": manifest[
            "calculated_formula_cache_inventories"
        ]["b"]["workbook"]["sha256"],
        "cache_sha256": manifest[
            "calculated_formula_cache_inventories"
        ]["a"]["formula_cache_sha256"],
        "engine_a_receipt_sha256": manifest[
            "independent_calculations"
        ]["a"]["receipt"]["sha256"],
        "engine_b_receipt_sha256": manifest[
            "independent_calculations"
        ]["b"]["receipt"]["sha256"],
    }
    audit_id = PRESYNC.sha256_bytes(
        PRESYNC.FORMULA_CACHE_FRESHNESS.canonical_bytes(identity)
    )
    snapshot = root / "formula_cache_freshness" / "snapshots" / audit_id
    manifest["audit_id"] = audit_id
    manifest_path = write_json(snapshot / "manifest.json", manifest)
    pointer["audit_id"] = audit_id
    pointer["manifest"] = byte_receipt(manifest_path)
    return write_json(
        root / "formula_cache_freshness" / "current.json",
        pointer,
    )


def reseal_formula_cache_manifest(pointer_path: Path) -> None:
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest_path = Path(pointer["manifest"]["path"])
    pointer["manifest"] = byte_receipt(manifest_path)
    write_json(pointer_path, pointer)


def write_final_with_acu_title_plan(
    root: Path,
    *,
    title: str = "Specific Credit Union Modernization Act",
) -> tuple[Path, dict[str, object]]:
    records = root / "records.ndjson"
    memberships = root / "memberships.ndjson"
    records.write_text(
        json.dumps(
            {
                "canonical_key": "119:hr:7",
                "official_title": title,
                "title": title,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    memberships.write_text(
        json.dumps(
            {
                "canonical_key": "119:hr:7",
                "query_id": "charter_modernization_governance",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    missed = root / "coverage_gap_ledger.ndjson"
    missed.write_text("", encoding="utf-8")
    exclusions = root / "exclusion_ledger.ndjson"
    exclusions.write_text("", encoding="utf-8")
    scope = write_json(root / "scope.json", {"scope": "all"})
    final = write_json(
        root / "manifest.json",
        {
            "status": "complete",
            "record_count": 100,
            "coverage_gap_count": 0,
            "union": {
                "records_file": records.name,
                "memberships_file": memberships.name,
            },
            "coverage_evidence": {
                "missed_candidates_ledger": PRESYNC.file_receipt(missed),
                "exclusion_ledger": PRESYNC.file_receipt(exclusions),
                "scope_and_exclusion_contract": PRESYNC.file_receipt(scope),
                "unresolved_gap_count": 0,
                "recovered_candidates_remain_visible": True,
                "semantic_exclusions_are_never_automatic": True,
            },
        },
    )
    plan = PRESYNC.ACU_TITLE_PLAN.build(
        run_dir=root,
        out_dir=(
            root
            / "article_cross_reference"
            / "acu_title_query_plan"
        ),
        scope="all",
        review_ledger=None,
    )
    return final, plan


class PresyncAuditTests(unittest.TestCase):
    def test_atomic_json_does_not_rewrite_identical_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            real_replace = PRESYNC.os.replace
            with mock.patch.object(
                PRESYNC.os, "replace", wraps=real_replace
            ) as replace:
                PRESYNC.atomic_json(path, {"status": "BLOCKED"})
                blocked_bytes = path.read_bytes()
                self.assertEqual(1, replace.call_count)

                PRESYNC.atomic_json(path, {"status": "BLOCKED"})
                self.assertEqual(blocked_bytes, path.read_bytes())
                self.assertEqual(1, replace.call_count)

                PRESYNC.atomic_json(path, {"status": "PASS"})
                self.assertNotEqual(blocked_bytes, path.read_bytes())
                self.assertEqual(2, replace.call_count)

    def test_formula_cache_receipt_is_current_and_informational(self) -> None:
        pointer = (
            MODULE_PATH.parents[2]
            / "audits"
            / "legfix5"
            / "formula_cache_freshness"
            / "current.json"
        )
        passed, state = (
            PRESYNC.validate_formula_cache_freshness_observation(pointer)
        )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(
            state["audit_id"],
            "505ad7f5aff56f0e548f8c87a79899b7227e99ef6fccf871d53ba6942b021061",
        )
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])
        self.assertFalse(state["authoritative_for_legislation"])
        self.assertFalse(state["authoritative_for_scoring"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])
        self.assertFalse(state["accepted_legislation_recomputation"])
        self.assertFalse(state["accepted_legislation_causality_proven"])
        self.assertFalse(
            state["live_stored_formula_cache_freshness_proven"]
        )
        self.assertFalse(state["calculated_copy_promotion_eligible"])
        self.assertFalse(state["live_or_production_workbook_written"])
        self.assertFalse(state["production_or_mainframe_written"])
        self.assertFalse(state["sync_or_promotion_performed"])
        self.assertEqual(
            state["formula_closure"]["formula_count"],
            98_726,
        )
        self.assertTrue(
            state["formula_closure"][
                "both_temp_copy_source_hashes_match"
            ]
        )
        self.assertTrue(
            state["formula_closure"][
                "deterministic_cache_digests_match"
            ]
        )
        # Ledger hash re-pinned at the 2026-08-04 workbook re-attestation (sha 5001e7df...); a and b must remain identical -- that equality IS the determinism proof.
        self.assertEqual(
            state["formula_closure"]["calculated_cache_sha256"],
            {
                "a": (
                    "837d0f32b5c85c113df937497aba398e325372525dd4ffe2bdf8d055cc237c7f"
                ),
                "b": (
                    "837d0f32b5c85c113df937497aba398e325372525dd4ffe2bdf8d055cc237c7f"
                ),
            },
        )
        self.assertEqual(state["formula_closure"]["formula_error_count"], 0)
        self.assertTrue(
            state["formula_closure"]["live_source_bytes_unchanged"]
        )
        self.assertEqual(
            state["formula_closure"]["verified_output_receipt_count"],
            4,
        )
        self.assertTrue(all(state["retained_blockers"].values()))

    def test_formula_cache_receipt_validation_is_read_only_and_idempotent(
        self,
    ) -> None:
        pointer = (
            MODULE_PATH.parents[2]
            / "audits"
            / "legfix5"
            / "formula_cache_freshness"
            / "current.json"
        )
        before_mtime = pointer.stat().st_mtime_ns
        first_passed, first = (
            PRESYNC.validate_formula_cache_freshness_observation(pointer)
        )
        second_passed, second = (
            PRESYNC.validate_formula_cache_freshness_observation(pointer)
        )
        self.assertTrue(first_passed)
        self.assertTrue(second_passed)
        self.assertEqual(first, second)
        self.assertEqual(pointer.stat().st_mtime_ns, before_mtime)

    def test_formula_cache_receipt_missing_has_no_gate_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            passed, state = (
                PRESYNC.validate_formula_cache_freshness_observation(
                    Path(temporary) / "missing.json"
                )
            )
        self.assertFalse(passed)
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])

    def test_formula_cache_binding_rejects_adversarial_manifest_drift(
        self,
    ) -> None:
        mutations = (
            "pointer_manifest_hash",
            "pointer_sync",
            "manifest_authority",
            "live_cache_claim",
            "promotion_claim",
            "causality_claim",
            "output_receipt_hash",
            "cache_digest",
            "formula_count",
            "formula_error",
            "semantic_mismatch",
            "dropped_live_blocker",
            "dropped_promotion_blocker",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    pointer_path = clone_formula_cache_fixture(
                        Path(temporary)
                    )
                    pointer = json.loads(
                        pointer_path.read_text(encoding="utf-8")
                    )
                    manifest_path = Path(pointer["manifest"]["path"])
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if mutation == "pointer_manifest_hash":
                        pointer["manifest"]["sha256"] = "0" * 64
                        write_json(pointer_path, pointer)
                    elif mutation == "pointer_sync":
                        pointer["sync_or_promotion_performed"] = True
                        write_json(pointer_path, pointer)
                    elif mutation == "manifest_authority":
                        manifest["authoritative_for_scoring"] = True
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "live_cache_claim":
                        manifest["gates"][
                            "live_stored_formula_cache_freshness_proven"
                        ] = True
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "promotion_claim":
                        manifest["gates"][
                            "postsave_candidate_promotion_eligible"
                        ] = True
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "causality_claim":
                        manifest[
                            "accepted_legislation_causality_proven"
                        ] = True
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "output_receipt_hash":
                        manifest["independent_calculations"]["a"][
                            "receipt"
                        ]["sha256"] = "0" * 64
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "cache_digest":
                        manifest[
                            "calculated_formula_cache_inventories"
                        ]["a"]["formula_cache_sha256"] = "0" * 64
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "formula_count":
                        manifest[
                            "calculated_formula_cache_inventories"
                        ]["a"]["formula_count"] = 98_725
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "formula_error":
                        inventory = manifest[
                            "calculated_formula_cache_inventories"
                        ]["a"]
                        inventory["formula_cached_error_count"] = 1
                        inventory["formula_cached_errors_by_token"] = {
                            "#REF!": 1
                        }
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    elif mutation == "semantic_mismatch":
                        manifest["semantic_comparisons"]["a_vs_b"][
                            "formula_mismatch_count"
                        ] = 1
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    else:
                        surface = (
                            "live_stored_formula_cache_freshness"
                            if mutation == "dropped_live_blocker"
                            else "postsave_candidate_exact_semantics"
                        )
                        manifest["unresolved"] = [
                            row
                            for row in manifest["unresolved"]
                            if row["surface"] != surface
                        ]
                        write_json(manifest_path, manifest)
                        reseal_formula_cache_manifest(pointer_path)
                    passed, state = (
                        PRESYNC.validate_formula_cache_freshness_observation(
                            pointer_path
                        )
                    )
                self.assertFalse(passed)
                self.assertEqual(state["status"], "STALE_OR_INVALID")
                self.assertTrue(state["issues"])
                self.assertFalse(state["affects_gate_decisions"])
                self.assertFalse(state["can_satisfy_blocked_gate"])
                self.assertFalse(state["adds_pass_conditions"])
                self.assertFalse(state["adds_gates"])
                self.assertFalse(state["satisfies_blockers"])

    def test_formula_cache_binding_rejects_engine_source_or_sync_drift(
        self,
    ) -> None:
        for mutation in ("temp_source_hash", "sync_flag"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    pointer = clone_formula_cache_fixture(
                        Path(temporary),
                        engine_mutation=mutation,
                    )
                    passed, state = (
                        PRESYNC.validate_formula_cache_freshness_observation(
                            pointer
                        )
                    )
                self.assertFalse(passed)
                self.assertEqual(state["status"], "STALE_OR_INVALID")
                self.assertTrue(state["issues"])
                self.assertFalse(state["affects_gate_decisions"])
                self.assertFalse(state["can_satisfy_blocked_gate"])

    def test_workbook_ooxml_semantics_are_current_and_informational(
        self,
    ) -> None:
        pointer = (
            MODULE_PATH.parents[2]
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_workbook_ooxml_semantics"
            / "current.json"
        )
        passed, state = (
            PRESYNC.validate_post_weekly_refresh_workbook_ooxml_semantics(
                pointer
            )
        )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])
        self.assertFalse(state["authoritative_for_legislation"])
        self.assertFalse(state["authoritative_for_scoring"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])
        self.assertTrue(all(state["retained_blockers"].values()))
        self.assertEqual(
            set(state["unresolved_surfaces"]),
            PRESYNC.WORKBOOK_OOXML_UNRESOLVED_SURFACES,
        )
        self.assertEqual(
            state["summary"]["baseline_sha256"],
            state["bound_inputs"]["frozen_workbook"]["sha256"],
        )
        self.assertEqual(
            state["summary"]["live_sha256"],
            state["bound_inputs"]["live_workbook"]["sha256"],
        )

    def test_workbook_ooxml_semantics_missing_has_no_gate_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            passed, state = (
                PRESYNC.validate_post_weekly_refresh_workbook_ooxml_semantics(
                    Path(temporary) / "missing.json"
                )
            )
        self.assertFalse(passed)
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])

    def test_workbook_ooxml_binding_rejects_adversarial_drift(
        self,
    ) -> None:
        mutations = (
            "pointer_manifest_hash",
            "output_receipt_hash",
            "live_workbook_hash",
            "manifest_authority",
            "output_authority",
            "dropped_formula_freshness_blocker",
            "formula_recalculated",
            "output_row_schema",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    pointer_path = clone_workbook_ooxml_fixture(
                        Path(temporary)
                    )
                    pointer = json.loads(
                        pointer_path.read_text(encoding="utf-8")
                    )
                    manifest_path = Path(pointer["manifest"]["path"])
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if mutation == "pointer_manifest_hash":
                        pointer["manifest"]["sha256"] = "0" * 64
                        write_json(pointer_path, pointer)
                    elif mutation == "output_receipt_hash":
                        manifest["outputs"][
                            "cell_differences.ndjson"
                        ]["sha256"] = "0" * 64
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    elif mutation == "live_workbook_hash":
                        manifest["bound_inputs"]["live_workbook"][
                            "sha256"
                        ] = "0" * 64
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    elif mutation == "manifest_authority":
                        manifest["authoritative_for_scoring"] = True
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    elif mutation == "output_authority":
                        output_path = Path(
                            manifest["outputs"][
                                "cell_differences.ndjson"
                            ]["path"]
                        )
                        rows = [
                            json.loads(line)
                            for line in output_path.read_text(
                                encoding="utf-8"
                            ).splitlines()
                        ]
                        rows[0]["authoritative_for_scoring"] = True
                        write_ndjson(output_path, rows)
                        receipt = byte_receipt(output_path)
                        receipt["record_count"] = len(rows)
                        manifest["outputs"][
                            "cell_differences.ndjson"
                        ] = receipt
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    elif mutation == "dropped_formula_freshness_blocker":
                        manifest["unresolved"] = [
                            row
                            for row in manifest["unresolved"]
                            if row["surface"] != "live_formula_cache_freshness"
                        ]
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    elif mutation == "formula_recalculated":
                        manifest["formula_results_recalculated"] = True
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    else:
                        output_path = Path(
                            manifest["outputs"][
                                "cell_differences.ndjson"
                            ]["path"]
                        )
                        rows = [
                            json.loads(line)
                            for line in output_path.read_text(
                                encoding="utf-8"
                            ).splitlines()
                        ]
                        rows[0]["schema"] = "adversarial-schema"
                        write_ndjson(output_path, rows)
                        receipt = byte_receipt(output_path)
                        receipt["record_count"] = len(rows)
                        manifest["outputs"][
                            "cell_differences.ndjson"
                        ] = receipt
                        write_json(manifest_path, manifest)
                        reseal_workbook_ooxml_manifest(pointer_path)
                    passed, state = (
                        PRESYNC
                        .validate_post_weekly_refresh_workbook_ooxml_semantics(
                            pointer_path
                        )
                    )
                self.assertFalse(passed)
                self.assertEqual(state["status"], "STALE_OR_INVALID")
                self.assertTrue(state["issues"])
                self.assertFalse(state["affects_gate_decisions"])
                self.assertFalse(state["can_satisfy_blocked_gate"])

    def test_behavior_stance_semantics_are_current_and_informational(
        self,
    ) -> None:
        pointer = (
            Path(__file__).resolve().parents[3]
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_behavior_stance_semantics"
            / "current.json"
        )
        passed, state = (
            PRESYNC.validate_post_weekly_refresh_behavior_stance_semantics(
                pointer
            )
        )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["authoritative_for_legislation"])
        self.assertFalse(state["authoritative_for_scoring"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])
        self.assertEqual(state["member_identity_closure"]["count"], 533)
        self.assertEqual(
            state["category_identity_order_closure"]["count"], 18
        )

    def test_behavior_stance_semantics_missing_has_no_gate_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            passed, state = (
                PRESYNC.validate_post_weekly_refresh_behavior_stance_semantics(
                    Path(temporary) / "missing.json"
                )
            )
        self.assertFalse(passed)
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])

    def test_planning_dashboard_semantics_are_current_and_informational(
        self,
    ) -> None:
        pointer = (
            Path(__file__).resolve().parents[3]
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_planning_dashboard_semantics"
            / "current.json"
        )
        passed, state = (
            PRESYNC
            .validate_post_weekly_refresh_planning_dashboard_semantics(
                pointer
            )
        )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(
            state["audit_id"],
            "502d200024094a5b9096c6c42f82cc16993f4cd67266ba3549b4390d88bc6b85",
        )
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])
        self.assertFalse(state["authoritative_for_legislation"])
        self.assertFalse(state["authoritative_for_scoring"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])
        self.assertFalse(state["production_or_mainframe_written"])
        self.assertFalse(state["sync_or_promotion_performed"])
        self.assertEqual(
            state["target_hash_closure"],
            {
                "required_target_count": 14,
                "verified_target_count": 14,
                "all_frozen_hashes_current": True,
                "all_live_hashes_current": True,
            },
        )
        self.assertEqual(
            state["unresolved_closure"],
            {
                "required_target_count": 14,
                "causality_unresolved_target_count": 14,
                "domain_correctness_unresolved_target_count": 14,
                "all_targets_retain_both": True,
            },
        )

    def test_planning_dashboard_semantics_missing_has_no_gate_authority(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            passed, state = (
                PRESYNC
                .validate_post_weekly_refresh_planning_dashboard_semantics(
                    Path(temporary) / "missing.json"
                )
            )
        self.assertFalse(passed)
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])

    def test_planning_dashboard_binding_rejects_adversarial_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clean_pointer = clone_planning_dashboard_fixture(Path(temporary))
            clean_passed, clean_state = (
                PRESYNC
                .validate_post_weekly_refresh_planning_dashboard_semantics(
                    clean_pointer
                )
            )
        self.assertTrue(clean_passed, clean_state["issues"])
        mutations = (
            "pointer_manifest_hash",
            "bound_drift_hash",
            "frozen_target_hash",
            "dropped_causality_gap",
            "resolved_domain_correctness",
            "output_authority",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    pointer_path = clone_planning_dashboard_fixture(
                        Path(temporary)
                    )
                    pointer = json.loads(
                        pointer_path.read_text(encoding="utf-8")
                    )
                    manifest_path = Path(pointer["manifest"]["path"])
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if mutation == "pointer_manifest_hash":
                        pointer["manifest"]["sha256"] = "0" * 64
                        write_json(pointer_path, pointer)
                    elif mutation == "bound_drift_hash":
                        manifest["bound_receipts"][
                            "downstream_drift_pointer"
                        ]["sha256"] = "0" * 64
                        write_json(manifest_path, manifest)
                        reseal_planning_dashboard_manifest(pointer_path)
                    else:
                        output_label = (
                            "unresolved_semantics"
                            if mutation == "dropped_causality_gap"
                            else "artifact_semantics"
                        )
                        output_path = Path(
                            manifest["outputs"][output_label]["path"]
                        )
                        rows = [
                            json.loads(line)
                            for line in output_path.read_text(
                                encoding="utf-8"
                            ).splitlines()
                        ]
                        if mutation == "frozen_target_hash":
                            rows[0]["frozen"]["sha256"] = "0" * 64
                        elif mutation == "dropped_causality_gap":
                            rows[0]["unresolved"].remove(
                                "causation by accepted legislation "
                                "recomputation"
                            )
                        elif mutation == "resolved_domain_correctness":
                            row = rows[0]
                            semantics = (
                                row["domain_closure"]
                                if row["artifact_type"] == "json"
                                else row["html_semantics"]
                            )
                            semantics[
                                "domain_meaning_or_policy_correctness_resolved"
                            ] = True
                        else:
                            rows[0]["authoritative_for_scoring"] = True
                        write_ndjson(output_path, rows)
                        receipt = byte_receipt(output_path)
                        receipt["record_count"] = len(rows)
                        manifest["outputs"][output_label] = receipt
                        write_json(manifest_path, manifest)
                        reseal_planning_dashboard_manifest(pointer_path)
                    passed, state = (
                        PRESYNC
                        .validate_post_weekly_refresh_planning_dashboard_semantics(
                            pointer_path
                        )
                    )
                self.assertFalse(passed)
                self.assertEqual(state["status"], "STALE_OR_INVALID")
                self.assertTrue(state["issues"])
                self.assertFalse(state["affects_gate_decisions"])
                self.assertFalse(state["can_satisfy_blocked_gate"])
                self.assertFalse(state["adds_pass_conditions"])
                self.assertFalse(state["adds_gates"])
                self.assertFalse(state["satisfies_blockers"])

    def test_downstream_drift_binding_is_current_and_informational(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer = write_downstream_drift_fixture(Path(temporary))
            passed, state = (
                PRESYNC.validate_post_weekly_refresh_downstream_drift(
                    pointer
                )
            )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertEqual(
            state["observational_closure"],
            {
                "declared_file_count": TOTAL,
                "changed_file_count": CHANGED_COUNT,
                "unchanged_file_count": UNCHANGED_COUNT,
                "semantic_gap_count": CHANGED_COUNT,
                "declared_equals_changed_plus_unchanged": True,
                "semantic_gaps_equal_changed_set": True,
            },
        )
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])

    def test_downstream_drift_binding_rejects_adversarial_receipts_and_flags(
        self,
    ) -> None:
        mutations = (
            "pointer_manifest_hash",
            "manifest_closure",
            "output_receipt_hash",
            "pointer_sync_flag",
            "output_authority_flag",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    pointer_path = write_downstream_drift_fixture(
                        Path(temporary)
                    )
                    pointer = json.loads(
                        pointer_path.read_text(encoding="utf-8")
                    )
                    manifest_path = Path(pointer["manifest"]["path"])
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if mutation == "pointer_manifest_hash":
                        pointer["manifest"]["sha256"] = "0" * 64
                        write_json(pointer_path, pointer)
                    elif mutation == "manifest_closure":
                        manifest["summary"]["changed_file_count"] = (
                            CHANGED_COUNT - 1
                        )
                        write_json(manifest_path, manifest)
                        reseal_downstream_drift_manifest(pointer_path)
                    elif mutation == "output_receipt_hash":
                        manifest["outputs"]["file_drift"]["sha256"] = (
                            "0" * 64
                        )
                        write_json(manifest_path, manifest)
                        reseal_downstream_drift_manifest(pointer_path)
                    elif mutation == "pointer_sync_flag":
                        pointer["sync_or_promotion_performed"] = True
                        write_json(pointer_path, pointer)
                    else:
                        output_path = Path(
                            manifest["outputs"]["file_drift"]["path"]
                        )
                        rows = [
                            json.loads(line)
                            for line in output_path.read_text(
                                encoding="utf-8"
                            ).splitlines()
                        ]
                        rows[0]["authoritative_for_scoring"] = True
                        write_ndjson(output_path, rows)
                        output_receipt = byte_receipt(output_path)
                        output_receipt["record_count"] = TOTAL
                        manifest["outputs"]["file_drift"] = output_receipt
                        write_json(manifest_path, manifest)
                        reseal_downstream_drift_manifest(pointer_path)
                    passed, state = (
                        PRESYNC.validate_post_weekly_refresh_downstream_drift(
                            pointer_path
                        )
                    )
                self.assertFalse(passed)
                self.assertEqual(state["status"], "STALE_OR_INVALID")
                self.assertTrue(state["issues"])
                self.assertFalse(state["affects_gate_decisions"])

    def test_decay_propagation_receipt_is_required_and_hash_current(
        self,
    ) -> None:
        workspace = MODULE_PATH.parents[2]
        receipt = (
            workspace
            / "audits"
            / "legfix5"
            / "decay_propagation"
            / "decay_propagation_audit.json"
        )
        passed, state = PRESYNC.validate_decay_propagation_audit(
            receipt,
            workspace_root=workspace,
        )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(state["staged_contracts"], {"passed": 9, "required": 9})
        self.assertEqual(
            state["consumer_classification"],
            {"classified": 16, "required": 16},
        )
        self.assertEqual(state["old_staged_consumers"], 0)
        self.assertEqual(
            state["production_freeze"],
            {
                "matched": TOTAL,
                "required": TOTAL,
                "cutover_detected": False,
            },
        )
        self.assertEqual(
            state["acceptance_context"],
            {
                "snapshot_only": True,
                "authoritative_for_policy_approval": False,
                "authoritative_for_shadow_or_sync": False,
                "snapshot_values": {
                    "decay_policy_approved": False,
                    "real_shadow_run_complete": False,
                    "sync_allowed": False,
                },
            },
        )

        missing, missing_state = PRESYNC.validate_decay_propagation_audit(
            workspace / "missing-decay-audit.json",
            workspace_root=workspace,
        )
        self.assertFalse(missing)
        self.assertEqual(missing_state["status"], "MISSING")

    def test_decay_propagation_receipt_rejects_tampering_and_stale_hashes(
        self,
    ) -> None:
        workspace = MODULE_PATH.parents[2]
        source = (
            workspace
            / "audits"
            / "legfix5"
            / "decay_propagation"
            / "decay_propagation_audit.json"
        )
        payload = json.loads(source.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=workspace / "tmp") as temporary:
            receipt = Path(temporary) / "decay.json"
            tampered = json.loads(json.dumps(payload))
            tampered["summary"]["production_file_match_count"] = TOTAL - 1
            write_json(receipt, tampered)
            passed, state = PRESYNC.validate_decay_propagation_audit(
                receipt,
                workspace_root=workspace,
            )
            self.assertFalse(passed)
            self.assertTrue(
                any(
                    "production_file_match_count" in issue
                    or f"clean {TOTAL}/{TOTAL}" in issue
                    for issue in state["issues"]
                )
            )

            stale = json.loads(json.dumps(payload))
            stale["staged_producer_contracts"][0]["source"]["sha256"] = (
                "0" * 64
            )
            write_json(receipt, stale)
            passed, state = PRESYNC.validate_decay_propagation_audit(
                receipt,
                workspace_root=workspace,
            )
            self.assertFalse(passed)
            self.assertTrue(
                any("source hash is stale" in issue for issue in state["issues"])
            )
            self.assertTrue(
                any("audit_basis" in issue for issue in state["issues"])
            )

    def test_decay_workflow_snapshot_values_are_not_gate_authority(
        self,
    ) -> None:
        workspace = MODULE_PATH.parents[2]
        source = (
            workspace
            / "audits"
            / "legfix5"
            / "decay_propagation"
            / "decay_propagation_audit.json"
        )
        payload = json.loads(source.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(dir=workspace / "tmp") as temporary:
            receipt = Path(temporary) / "decay.json"
            payload["acceptance_context"].update(
                {
                    "decay_policy_approved": True,
                    "real_shadow_run_complete": True,
                    "sync_allowed": True,
                }
            )
            write_json(receipt, payload)
            passed, state = PRESYNC.validate_decay_propagation_audit(
                receipt,
                workspace_root=workspace,
            )
            self.assertTrue(passed, state["issues"])
            self.assertEqual(
                state["acceptance_context"]["snapshot_values"],
                {
                    "decay_policy_approved": True,
                    "real_shadow_run_complete": True,
                    "sync_allowed": True,
                },
            )

            payload["acceptance_context"][
                "authoritative_for_shadow_or_sync"
            ] = True
            write_json(receipt, payload)
            passed, state = PRESYNC.validate_decay_propagation_audit(
                receipt,
                workspace_root=workspace,
            )
            self.assertFalse(passed)
            self.assertIn(
                (
                    "acceptance_context.authoritative_for_shadow_or_sync "
                    "must be False"
                ),
                state["issues"],
            )

    def test_presync_config_declares_decay_propagation_gate(self) -> None:
        config = json.loads(
            (
                MODULE_PATH.parents[2]
                / "audits"
                / "legfix5"
                / "presync_audit_config.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            config["shadow"]["decay_propagation_audit"],
            (
                "audits/legfix5/decay_propagation/"
                "decay_propagation_audit.json"
            ),
        )
        self.assertEqual(
            config["coverage"]["query_specificity_audit"],
            (
                "audits/legfix5/query_specificity/current/"
                "query_specificity_audit.json"
            ),
        )
        self.assertNotIn("member_map", config["rollcall"])
        self.assertEqual("auto", config["rollcall"]["action_source"])

    def test_query_specificity_gate_is_hash_bound_and_fail_closed(self) -> None:
        workspace = MODULE_PATH.parents[2]
        audit = (
            workspace
            / "audits"
            / "legfix5"
            / "query_specificity"
            / "current"
            / "query_specificity_audit.json"
        )
        registry = (
            workspace
            / "source_data"
            / "legislation"
            / "acu_legislation_search_registry.json"
        )
        payload = json.loads(audit.read_text(encoding="utf-8"))
        strict = Path(
            payload["inputs"]["selected_strict_manifest"]["path"]
        )
        passed, state = PRESYNC.validate_query_specificity_audit(
            audit,
            registry_path=registry,
            strict_manifest_path=strict,
        )
        self.assertTrue(passed, state["issues"])
        self.assertEqual(
            state["generic_bare_act_scoring_terms"],
            0,
        )
        self.assertTrue(
            state["ambiguous_title_aliases_are_non_dispositive"]
        )

        with tempfile.TemporaryDirectory(dir=workspace / "tmp") as temporary:
            tampered_path = Path(temporary) / "specificity.json"
            tampered = json.loads(json.dumps(payload))
            tampered["counts"][
                "ambiguous_aliases_in_category_queries"
            ] = 1
            write_json(tampered_path, tampered)
            passed, state = PRESYNC.validate_query_specificity_audit(
                tampered_path,
                registry_path=registry,
                strict_manifest_path=strict,
            )
            self.assertFalse(passed)
            self.assertTrue(
                any(
                    "ambiguous_aliases_in_category_queries"
                    in issue
                    for issue in state["issues"]
                )
            )

            stale = json.loads(json.dumps(payload))
            stale["inputs"]["selected_strict_manifest"]["sha256"] = (
                "0" * 64
            )
            write_json(tampered_path, stale)
            passed, state = PRESYNC.validate_query_specificity_audit(
                tampered_path,
                registry_path=registry,
                strict_manifest_path=strict,
            )
            self.assertFalse(passed)
            self.assertIn(
                "query specificity selected strict manifest receipt drifted",
                state["issues"],
            )

    def test_rollcall_identity_gate_accepts_absent_or_current_map_only(self) -> None:
        base = {
            "acceptance_passed": True,
            "blocking_issue_count": 0,
            "member_identity_resolution": {
                "supplemental_member_map": {
                    "status": "absent",
                    "present": False,
                    "receipt": None,
                },
                "accepted_event_member_row_count": 2,
                "resolution_status_counts": {"included": 2},
                "included_resolution_counts": {
                    "official_xml_bioguide": 2,
                    "supplemental_member_map": 0,
                },
                "unmatched_member_row_count": 0,
                "ambiguous_member_row_count": 0,
                "unresolved_or_ambiguous_member_row_count": 0,
                "all_accepted_event_members_uniquely_resolved": True,
                "fuzzy_matching_used": False,
            },
        }
        passed, state = PRESYNC.validate_rollcall_identity_manifest(
            base,
            expected_member_map_present=False,
        )
        self.assertTrue(passed, state["issues"])

        unresolved = json.loads(json.dumps(base))
        resolution = unresolved["member_identity_resolution"]
        resolution["accepted_event_member_row_count"] = 3
        resolution["resolution_status_counts"]["ambiguous"] = 1
        resolution["ambiguous_member_row_count"] = 1
        resolution["unresolved_or_ambiguous_member_row_count"] = 1
        resolution["all_accepted_event_members_uniquely_resolved"] = False
        passed, state = PRESYNC.validate_rollcall_identity_manifest(
            unresolved,
            expected_member_map_present=False,
        )
        self.assertFalse(passed)
        self.assertTrue(
            any("unresolved" in issue for issue in state["issues"])
        )

        with tempfile.TemporaryDirectory() as temporary:
            member_map = Path(temporary) / "members.csv"
            member_map.write_text(
                "bioguide_id,lis_member_id\nA000001,S001\n",
                encoding="utf-8",
            )
            present = json.loads(json.dumps(base))
            identity = present["member_identity_resolution"]
            identity["supplemental_member_map"] = {
                "status": "present",
                "present": True,
                "receipt": PRESYNC.file_receipt(member_map),
            }
            identity["included_resolution_counts"] = {
                "official_xml_bioguide": 1,
                "supplemental_member_map": 1,
            }
            passed, state = PRESYNC.validate_rollcall_identity_manifest(
                present,
                expected_member_map=member_map,
                expected_member_map_present=True,
            )
            self.assertTrue(passed, state["issues"])

    def test_numeric_plan_and_approved_generation_are_explicit_stages(
        self,
    ) -> None:
        numeric = PRESYNC.STAGE_SPECS["numeric_plan"]
        approved = PRESYNC.STAGE_SPECS["approved_generation"]
        self.assertEqual(
            numeric.script,
            "legislation/build_legislation_workbook_numeric_plan.py",
        )
        self.assertEqual(numeric.manifest_names, ("numeric_plan.json",))
        self.assertIn(2, numeric.blocking_exit_codes)
        self.assertEqual(
            approved.script,
            "legislation/assemble_approved_legislation_generation.py",
        )
        source = (
            Path(PRESYNC.__file__).read_text(encoding="utf-8")
        )
        for flag in (
            "--manual-evidence",
            "--role-policy-approval",
            "--computation-policy-approval",
            "--audited-layout",
            "--numeric-plan",
        ):
            self.assertIn(flag, source)

    def test_output_is_restricted_to_legfix5(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = (
                root / "audits" / "legfix5" / "shadow_recompute" / "presync"
            )
            self.assertEqual(PRESYNC.safe_output(allowed, root), allowed.resolve())
            with self.assertRaises(PRESYNC.PresyncError):
                PRESYNC.safe_output(root / "production", root)
            with self.assertRaises(PRESYNC.PresyncError):
                PRESYNC.safe_output(root / "audits" / "legfix5", root)
            with self.assertRaises(PRESYNC.PresyncError):
                PRESYNC.safe_output(
                    root / "audits" / "legfix5" / "presync", root
                )

    def test_coverage_gate_requires_all_62_exact_and_distinct_final(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / "registry.json"
            registry.write_text("{}", encoding="utf-8")
            query_ids = [f"q{i:02d}" for i in range(62)]
            queries = [
                {
                    "query_id": query_id,
                    "result_total": 1,
                    "unique_record_count": 1,
                    "page_count": 1,
                    "validation": {
                        "exact_result_total": True,
                        "unique_result_total": True,
                        "every_page_exact": True,
                    },
                }
                for query_id in query_ids
            ]
            strict = write_json(
                root / "strict.json",
                {
                    "status": "complete",
                    "primary_instrument": PRESYNC.PRIMARY_INSTRUMENT,
                    "quorum_used": False,
                    "registry_sha256": PRESYNC.sha256_file(registry),
                    "selected_query_ids": query_ids,
                    "queries": queries,
                },
            )
            final, _ = write_final_with_acu_title_plan(root)
            audit, blockers = PRESYNC.coverage_gate(strict, final, registry)
            self.assertTrue(audit["strict_discovery_passed"])
            self.assertTrue(audit["final_merged_passed"])
            self.assertTrue(audit["acu_title_query_plan_passed"])
            self.assertEqual(blockers, [])
            queries.pop()
            write_json(strict, {**json.loads(strict.read_text()), "queries": queries})
            audit, blockers = PRESYNC.coverage_gate(strict, final, registry)
            self.assertFalse(audit["strict_discovery_passed"])
            self.assertIn(
                "strict_congress_advanced_coverage_incomplete",
                {row["code"] for row in blockers},
            )
            _, blockers = PRESYNC.coverage_gate(strict, strict, registry)
            self.assertIn(
                "final_historical_merged_coverage_incomplete",
                {row["code"] for row in blockers},
            )

    def test_acu_title_plan_is_hash_bound_and_unresolved_stays_visible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            valid_root = Path(temporary) / "valid"
            valid_root.mkdir()
            final, plan = write_final_with_acu_title_plan(valid_root)
            audit, blockers = PRESYNC.acu_title_query_plan_gate(final)
            self.assertTrue(audit["manifest_valid"])
            self.assertTrue(audit["acceptance_passed"])
            self.assertTrue(audit["absence_is_neutral"])
            self.assertTrue(audit["automatic_decisions_disabled"])
            self.assertTrue(audit["official_relevance_congress_gov_only"])
            self.assertEqual(blockers, [])

            query_path = (
                valid_root
                / "article_cross_reference"
                / "acu_title_query_plan"
                / str(plan["outputs"]["queries"]["path"])
            )
            query_path.write_text("{}\n", encoding="utf-8")
            audit, blockers = PRESYNC.acu_title_query_plan_gate(final)
            self.assertFalse(audit["manifest_valid"])
            self.assertIn(
                "acu_title_query_plan_invalid",
                {row["code"] for row in blockers},
            )

            unresolved_root = Path(temporary) / "unresolved"
            unresolved_root.mkdir()
            final, plan = write_final_with_acu_title_plan(
                unresolved_root,
                title="Act",
            )
            self.assertEqual(plan["status"], "BLOCKED")
            audit, blockers = PRESYNC.acu_title_query_plan_gate(final)
            self.assertTrue(audit["manifest_valid"])
            self.assertFalse(audit["acceptance_passed"])
            self.assertEqual(audit["unresolved_measure_count"], 1)
            self.assertTrue(audit["unresolved_rows_visible"])
            self.assertTrue(audit["absence_is_neutral"])
            self.assertIn(
                "acu_title_query_plan_unresolved",
                {row["code"] for row in blockers},
            )

    def test_acu_title_plan_is_required_only_after_final_exists(self) -> None:
        audit, blockers = PRESYNC.acu_title_query_plan_gate(None)
        self.assertFalse(audit["required_now"])
        self.assertEqual(blockers, [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            final = write_json(
                root / "manifest.json",
                {"status": "complete"},
            )
            audit, blockers = PRESYNC.acu_title_query_plan_gate(final)
            self.assertTrue(audit["required_now"])
            self.assertIn(
                "acu_title_query_plan_missing",
                {row["code"] for row in blockers},
            )

    def test_incomplete_strict_is_audited_when_final_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = root / "registry.json"
            registry.write_text("{}", encoding="utf-8")
            query_ids = [f"q{i:02d}" for i in range(12)]
            strict = write_json(
                root / "strict.json",
                {
                    "status": "partial",
                    "primary_instrument": PRESYNC.PRIMARY_INSTRUMENT,
                    "quorum_used": False,
                    "registry_sha256": PRESYNC.sha256_file(registry),
                    "selected_query_ids": [f"q{i:02d}" for i in range(62)],
                    "queries": [
                        {
                            "query_id": query_id,
                            "validation": {
                                "exact_result_total": True,
                                "unique_result_total": True,
                                "every_page_exact": True,
                            },
                        }
                        for query_id in query_ids
                    ],
                },
            )
            audit, blockers = PRESYNC.coverage_gate(strict, None, registry)
            self.assertEqual(audit["strict_query_count"], 12)
            self.assertEqual(audit["strict_exact_query_count"], 12)
            self.assertEqual(
                audit["strict_manifest"]["sha256"],
                PRESYNC.sha256_file(strict),
            )
            codes = {row["code"] for row in blockers}
            self.assertIn("strict_congress_advanced_coverage_incomplete", codes)
            self.assertIn("final_historical_merged_coverage_incomplete", codes)

    def test_strict_run_selects_maximum_exact_progress_then_freshness(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "strict_run"
            run.mkdir()
            registry = root / "registry.json"
            registry.write_text("{}", encoding="utf-8")
            registry_hash = PRESYNC.sha256_file(registry)
            selected_ids = [f"q{i:02d}" for i in range(62)]
            query_dir = run / "queries"
            query_dir.mkdir()
            empty_records_hash = PRESYNC.sha256_bytes(b"[]")
            exact_validation = {
                "exact_result_total": True,
                "unique_result_total": True,
                "every_page_exact": True,
            }
            for query_id in ("q00", "q01"):
                write_json(
                    query_dir / f"{query_id}.json",
                    {
                        "schema": "congress-advanced-search-query-v1",
                        "query_id": query_id,
                        "registry_sha256": registry_hash,
                        "result_total": 0,
                        "unique_record_count": 0,
                        "page_count": 0,
                        "records_sha256": empty_records_hash,
                        "validation": exact_validation,
                        "records": [],
                    },
                )

            def partial(
                path: Path,
                query_ids: list[str],
                **extra: object,
            ) -> Path:
                return write_json(
                    path,
                    {
                        "status": "failed",
                        "primary_instrument": PRESYNC.PRIMARY_INSTRUMENT,
                        "quorum_used": False,
                        "registry_sha256": registry_hash,
                        "selected_query_ids": selected_ids,
                        "queries": [
                            {
                                "query_id": query_id,
                                "query_file": f"queries/{query_id}.json",
                                "result_total": 0,
                                "unique_record_count": 0,
                                "page_count": 0,
                                "records_sha256": empty_records_hash,
                                "validation": exact_validation,
                            }
                            for query_id in query_ids
                        ],
                        **extra,
                    },
                )

            write_json(
                run / "manifest.json",
                {
                    "registry_sha256": "0" * 64,
                    "queries": [],
                },
            )
            older_progress = partial(
                run / "manifest.partial.1.json",
                ["q00", "q01"],
            )
            newer_equal_progress = partial(
                run / "manifest.partial.2.json",
                ["q00", "q01"],
            )
            newest_empty_retry = partial(
                run / "manifest.partial.3.json",
                [],
            )
            receipt = write_json(
                run / "raw" / "timeout.json",
                {"status": "active"},
            )
            stale_receipt = partial(
                run / "manifest.partial.3a.json",
                ["q00", "q01"],
                automatic_verification_timeout={
                    "classification": "automatic_verification_timeout",
                    "receipt_file": str(
                        receipt.relative_to(run).as_posix()
                    ),
                    "receipt_sha256": "0" * 64,
                    "interaction_performed": False,
                    "captcha_bypass_attempted": False,
                    "collection_stopped_without_result_acceptance": True,
                },
            )
            corrupt_duplicate = partial(
                run / "manifest.partial.4.json",
                ["q00", "q00", "q01"],
            )
            os.utime(
                older_progress,
                ns=(1_000_000_000, 1_000_000_000),
            )
            os.utime(
                newer_equal_progress,
                ns=(2_000_000_000, 2_000_000_000),
            )
            os.utime(
                newest_empty_retry,
                ns=(3_000_000_000, 3_000_000_000),
            )
            os.utime(
                stale_receipt,
                ns=(3_500_000_000, 3_500_000_000),
            )
            os.utime(
                corrupt_duplicate,
                ns=(4_000_000_000, 4_000_000_000),
            )
            blockers: list[dict[str, object]] = []
            selected = PRESYNC.select_strict_manifest(
                {"coverage": {"strict_run": str(run)}},
                workspace_root=root,
                registry_path=registry,
                blockers=blockers,
            )
            self.assertEqual(selected, newer_equal_progress)
            self.assertEqual(blockers, [])
            manifest = json.loads(selected.read_text(encoding="utf-8"))
            self.assertEqual(
                PRESYNC.strict_manifest_exact_progress(
                    manifest,
                    registry_sha256=registry_hash,
                    manifest_path=selected,
                ),
                2,
            )

    def test_check_only_writes_blockers_and_distinct_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "audits" / "legfix5").mkdir(parents=True)
            config = write_json(
                root / "config.json",
                {"schema": PRESYNC.SCHEMA, "coverage": {}},
            )
            args = argparse.Namespace(
                config=config,
                out=(
                    root
                    / "audits"
                    / "legfix5"
                    / "shadow_recompute"
                    / "presync"
                ),
                resume=False,
                resume_coverage=False,
                check_only=True,
                batch_size=25,
                stage_timeout_seconds=10,
                python_executable=str(Path(__file__).resolve()),
                workspace_root=root,
            )
            result = PRESYNC.orchestrate(args)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["exit_code"], PRESYNC.EXIT_BLOCKED)
            self.assertFalse(result["production_or_mainframe_written"])
            self.assertFalse(result["captcha_bypass_attempted"])
            self.assertTrue((args.out / "checkpoint.json").is_file())
            self.assertIn(
                "human_membership_review_not_ratified",
                {row["code"] for row in result["blockers"]},
            )
            self.assertIn(
                "decay_propagation_audit_missing_tampered_or_stale",
                {row["code"] for row in result["blockers"]},
            )
            self.assertEqual(
                result["decay_propagation_gate"]["status"],
                "MISSING",
            )
            self.assertEqual(
                result["post_weekly_refresh_downstream_drift"]["status"],
                "MISSING",
            )
            self.assertFalse(
                result["post_weekly_refresh_downstream_drift"][
                    "affects_gate_decisions"
                ]
            )
            self.assertEqual(
                result[
                    "post_weekly_refresh_workbook_ooxml_semantics"
                ]["status"],
                "MISSING",
            )
            self.assertFalse(
                result[
                    "post_weekly_refresh_workbook_ooxml_semantics"
                ]["affects_gate_decisions"]
            )
            self.assertEqual(
                result[
                    "post_weekly_refresh_planning_dashboard_semantics"
                ]["status"],
                "MISSING",
            )
            self.assertFalse(
                result[
                    "post_weekly_refresh_planning_dashboard_semantics"
                ]["affects_gate_decisions"]
            )
            self.assertNotIn(
                "workbook_ooxml_semantics",
                {
                    row["code"]
                    for row in result["blockers"]
                },
            )
            self.assertNotIn(
                "planning_dashboard_semantics",
                {
                    row["code"]
                    for row in result["blockers"]
                },
            )
            self.assertTrue(result["rollcall_source_gate"]["passed"])
            self.assertEqual(
                "auto",
                result["rollcall_source_gate"]["requested_action_source"],
            )
            self.assertNotIn(
                "official_rollcall_source_unavailable",
                {row["code"] for row in result["blockers"]},
            )
            self.assertNotIn(
                "rollcall.member_map",
                {
                    row.get("input")
                    for row in result["blockers"]
                    if row.get("code") in {
                        "missing_explicit_input",
                        "explicit_input_not_found",
                    }
                },
            )

    def test_resume_reuses_only_hash_identical_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            producer = root / "fec" / "fake.py"
            producer.parent.mkdir()
            producer.write_text("# staged fixture\n", encoding="utf-8")
            input_path = root / "input.json"
            input_path.write_text("{}", encoding="utf-8")
            output = root / "audits" / "legfix5" / "run" / "stages" / "fake"
            output.mkdir(parents=True)
            manifest = write_json(output / "manifest.json", {"status": "PASS"})
            state_dir = root / "audits" / "legfix5" / "run" / "state"
            command = [str(Path(__file__).resolve()), str(producer), "--out", str(output)]
            fingerprint, receipts = PRESYNC.stage_fingerprint(
                command, [input_path], producer
            )
            state = {
                "schema": PRESYNC.STAGE_STATE_SCHEMA,
                "stage": "fake",
                "fingerprint": fingerprint,
                "command": command,
                "inputs": receipts,
                "manifest": PRESYNC.file_receipt(manifest),
            }
            write_json(state_dir / "fake.json", state)
            original = PRESYNC.STAGE_SPECS
            PRESYNC.STAGE_SPECS = {
                **original,
                "fake": PRESYNC.StageSpec("fake.py"),
            }
            try:
                with mock.patch.object(
                    PRESYNC.subprocess, "run", side_effect=AssertionError("ran")
                ):
                    reused = PRESYNC.run_stage(
                        name="fake",
                        argv=["--out", str(output)],
                        inputs=[input_path],
                        out_dir=output,
                        state_dir=state_dir,
                        python_executable=Path(__file__).resolve(),
                        workspace_root=root,
                        resume=True,
                        timeout_seconds=10,
                    )
                self.assertTrue(reused["reused"])
                input_path.write_text('{"changed":true}', encoding="utf-8")
                def fake_run(*_args: object, **_kwargs: object) -> object:
                    output.mkdir(parents=True)
                    write_json(output / "manifest.json", {"status": "PASS"})
                    return PRESYNC.subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout=b"reran\n",
                        stderr=b"",
                    )

                with mock.patch.object(
                    PRESYNC.subprocess, "run", side_effect=fake_run
                ):
                    rerun = PRESYNC.run_stage(
                        name="fake",
                        argv=["--out", str(output)],
                        inputs=[input_path],
                        out_dir=output,
                        state_dir=state_dir,
                        python_executable=Path(__file__).resolve(),
                        workspace_root=root,
                        resume=True,
                        timeout_seconds=10,
                    )
                self.assertFalse(rerun["reused"])
                self.assertTrue(output.is_dir())
                self.assertTrue(
                    output.with_name(
                        f"{output.name}.superseded.{fingerprint[:12]}"
                    ).is_dir()
                )
                self.assertNotEqual(rerun["fingerprint"], fingerprint)
            finally:
                PRESYNC.STAGE_SPECS = original

    def test_shadow_fingerprint_closes_code_artifacts_and_immutable_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fec = root / "fec"
            fec.mkdir()
            code = fec / "consumer.py"
            code.write_text("VALUE = 1\n", encoding="utf-8")
            immutable = root / "data" / "engine" / "congestion.json"
            immutable.parent.mkdir(parents=True, exist_ok=True)
            immutable.write_text('{"v":1}', encoding="utf-8")
            explicit = root / "consumer_plan.json"
            explicit.write_text("{}", encoding="utf-8")
            staged_dirs = []
            for name in ("accepted", "direction", "base", "rollcall"):
                directory = root / name
                directory.mkdir()
                (directory / "manifest.json").write_text(
                    f'{{"name":"{name}"}}', encoding="utf-8"
                )
                staged_dirs.append(directory)
            frozen = root / "frozen.json"
            frozen.write_text("frozen", encoding="utf-8")
            baseline = write_json(
                root / "baseline.json",
                {"files": [{"copy": str(frozen)}]},
            )
            contracts = (
                PRESYNC.SHADOW_RUNNER.ImmutableInputContract(
                    "data/engine/congestion.json", ("behavior",)
                ),
            )
            with mock.patch.object(
                PRESYNC.SHADOW_RUNNER,
                "IMMUTABLE_INPUT_CONTRACTS",
                contracts,
            ):
                closure = PRESYNC.shadow_transitive_fingerprint_inputs(
                    workspace_root=root,
                    explicit_inputs=[explicit, baseline],
                    accepted_generation=staged_dirs[0],
                    direction_dir=staged_dirs[1],
                    base_dir=staged_dirs[2],
                    rollcall_dir=staged_dirs[3],
                    baseline_manifest=baseline,
                )
            self.assertIn(code.resolve(), closure)
            self.assertIn(immutable.resolve(), closure)
            self.assertIn(frozen.resolve(), closure)
            first, _ = PRESYNC.stage_fingerprint(
                ["python", "shadow.py"], closure, code
            )
            immutable.write_text('{"v":2}', encoding="utf-8")
            second, _ = PRESYNC.stage_fingerprint(
                ["python", "shadow.py"], closure, code
            )
            self.assertNotEqual(first, second)

    def test_role_approval_cannot_be_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "generated"
            ledger = generated / "accepted.ndjson"
            ledger.parent.mkdir()
            ledger.write_text("{}\n", encoding="utf-8")
            manifest = write_json(
                generated / "manifest.json",
                {
                    "canonical_ledger_file": ledger.name,
                    "canonical_ledger_sha256": PRESYNC.sha256_file(ledger),
                },
            )
            accepted_state = {"manifest": PRESYNC.file_receipt(manifest)}
            blockers: list[dict[str, object]] = []
            result, receipt = PRESYNC.validate_role_approval(
                {"shadow": {}}, root, accepted_state, blockers
            )
            self.assertIsNone(result)
            self.assertIsNone(receipt)
            self.assertIn(
                "role_weight_policy_unresolved",
                {row["code"] for row in blockers},
            )

    def test_coverage_resume_is_allowlisted_and_never_handles_captcha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "fec" / "run_legislation_specificity_followup.ps1"
            script.parent.mkdir()
            script.write_text("# fixture", encoding="utf-8")
            out = root / "audits" / "legfix5" / "presync"
            out.mkdir(parents=True)
            config = {
                "coverage": {
                    "resume": {
                        "script": str(script),
                        "current_pid": 0,
                        "launch_when_stopped": True,
                        "retry_delay_seconds": 5,
                    }
                }
            }
            process = mock.Mock(pid=1234)
            with mock.patch.object(PRESYNC.subprocess, "Popen", return_value=process):
                receipt = PRESYNC.coverage_resume_status(
                    config=config,
                    workspace_root=root,
                    out=out,
                    launch=True,
                )
            self.assertEqual(receipt["status"], "launched")
            self.assertFalse(receipt["captcha_bypass_attempted"])
            self.assertFalse(receipt["browser_interaction_performed"])
            config["coverage"]["resume"]["script"] = str(root / "evil.ps1")
            with self.assertRaises(PRESYNC.PresyncError):
                PRESYNC.coverage_resume_status(
                    config=config,
                    workspace_root=root,
                    out=out,
                    launch=True,
                )

    def test_unverifiable_positive_pid_never_launches_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out = root / "audits" / "legfix5" / "shadow_recompute" / "presync"
            out.mkdir(parents=True)
            config = {
                "coverage": {
                    "resume": {
                        "current_pid": 24028,
                        "launch_when_stopped": True,
                    }
                }
            }
            with (
                mock.patch.object(
                    PRESYNC,
                    "process_liveness",
                    return_value="unverifiable",
                ),
                mock.patch.object(
                    PRESYNC.subprocess,
                    "Popen",
                    side_effect=AssertionError("duplicate collector launched"),
                ),
            ):
                receipt = PRESYNC.coverage_resume_status(
                    config=config,
                    workspace_root=root,
                    out=out,
                    launch=True,
                )
            self.assertEqual(
                receipt["status"], "process_liveness_unverifiable"
            )
            self.assertTrue(receipt["launch_suppressed"])

    def test_current_process_liveness_is_read_only_and_alive(self) -> None:
        self.assertEqual(PRESYNC.process_liveness(os.getpid()), "alive")

    def test_shadow_pass_requires_pass_only_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            failure = write_json(
                out / "shadow_recompute_audit.json",
                {
                    "status": "FAIL",
                    "acceptance_passed": False,
                    "staged_acceptance_receipt_emitted": False,
                },
            )
            found = PRESYNC.locate_manifest(
                out,
                PRESYNC.STAGE_SPECS["shadow_recompute"].manifest_names,
            )
            self.assertEqual(found, failure)
            self.assertFalse(
                PRESYNC.shadow_acceptance_passed(
                    {"manifest": PRESYNC.file_receipt(found)}
                )
            )
            # Even a malformed audit claiming acceptance can never certify PASS.
            write_json(
                failure,
                {"status": "PASS", "acceptance_passed": True},
            )
            self.assertFalse(
                PRESYNC.shadow_acceptance_passed(
                    {"manifest": PRESYNC.file_receipt(failure)}
                )
            )
            receipt = write_json(
                out / "staged_acceptance_receipt.json",
                {
                    "status": "PASS",
                    "acceptance_passed": True,
                    "production_promotion_performed": False,
                },
            )
            found = PRESYNC.locate_manifest(
                out,
                PRESYNC.STAGE_SPECS["shadow_recompute"].manifest_names,
            )
            self.assertEqual(found, receipt)
            self.assertFalse(
                PRESYNC.shadow_acceptance_passed(
                    {"manifest": PRESYNC.file_receipt(found)}
                )
            )


if __name__ == "__main__":
    unittest.main()
