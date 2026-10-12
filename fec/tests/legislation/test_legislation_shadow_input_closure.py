from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import run_legislation_dashboard_member_desk as DASH  # noqa: E402
from legislation import run_legislation_network_dependents as NETWORK  # noqa: E402
from legislation import run_legislation_shadow_recompute as SHADOW  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_bytes(path: Path, body: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body if body is not None else path.as_posix().encode())
    return path


def receipt(path: Path, root: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": digest(path),
    }


def source_context(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    accepted_manual = write_bytes(
        root / "accepted" / "manual_bill_evidence.csv",
        b"Category,Bill\nCFPB,LTR-CANONICAL\n",
    )
    baseline_desk = write_bytes(
        root / "baseline" / "member_desk_bundle.json",
        b'{"members":[],"source":"frozen"}\n',
    )
    producers = {
        "accepted_generation": {
            "outputs": {
                "manual_bill_evidence": {
                    "path": str(accepted_manual),
                    "size": accepted_manual.stat().st_size,
                    "sha256": digest(accepted_manual),
                }
            }
        }
    }
    baseline = {
        "member_desk": {
            "path": baseline_desk,
            "size": baseline_desk.stat().st_size,
            "sha256": digest(baseline_desk),
        }
    }
    return producers, baseline


def generation_ledger(run_dir: Path, generation_id: str) -> dict[str, object]:
    value = {
        "schema": SHADOW.STEP_LEDGER_SCHEMA,
        "status": "RUNNING",
        "staged_only": True,
        "production_written": False,
        "generation_id": generation_id,
        "completed_steps": [],
    }
    write_json(run_dir / SHADOW.STEP_LEDGER_NAME, value)
    return value


def add_step(
    ledger: dict[str, object],
    name: str,
    paths: list[Path],
    run_dir: Path,
) -> None:
    ledger["completed_steps"].append(
        {
            "name": name,
            "outputs": {
                path.relative_to(run_dir).as_posix(): {
                    "sha256": digest(path),
                    "size": path.stat().st_size,
                }
                for path in paths
            },
        }
    )
    write_json(run_dir / SHADOW.STEP_LEDGER_NAME, ledger)


class ImmutableInputClosureTests(unittest.TestCase):
    def test_accepted_gate_requires_exact_consumer_specific_methods(self) -> None:
        manifest = {
            "schema": SHADOW.ACCEPTED_MANIFEST_SCHEMA,
            "generation_complete": True,
            "upstream_acceptance_passed": True,
            "promotion_ready": True,
            "role_weight_policy_reconciled": True,
            "staged_only": True,
            "production_artifacts_written": False,
            "selected_role_weight_policy": "workbook_legacy_4_2_1",
            "selected_method_profile": SHADOW.ROLE_METHOD_PROFILE_ID,
            "consumer_role_methods": SHADOW.ROLE_METHOD_PROFILE,
        }
        self.assertTrue(SHADOW.accepted_generation_gate(manifest))
        global_only = dict(manifest)
        global_only.pop("selected_method_profile")
        global_only.pop("consumer_role_methods")
        self.assertFalse(SHADOW.accepted_generation_gate(global_only))
        altered = json.loads(json.dumps(manifest))
        altered["consumer_role_methods"]["say_do"]["weights"]["sponsor"] = 4.0
        self.assertFalse(SHADOW.accepted_generation_gate(altered))
        proposal = json.loads(json.dumps(manifest))
        proposal["proposal_only"] = True
        proposal["scoring_authority"] = False
        self.assertFalse(SHADOW.accepted_generation_gate(proposal))

    def test_review_gate_rejects_prefill_authority_markers(self) -> None:
        review = {
            "schema": SHADOW.REVIEW_MANIFEST_SCHEMA,
            "promotion_gate_passed": True,
            "discovery_records_are_never_deleted": True,
            "automated_semantic_exclusion": False,
            "official_text_signals_require_review_before_inclusion": True,
            "resolved_decisions_require_reproducible_official_evidence": True,
        }
        self.assertTrue(SHADOW.authoritative_review_gate(review))
        review.update(
            {
                "review_prefill_only": True,
                "proposal_only": True,
                "ratification_authority": False,
                "scoring_authority": False,
            }
        )
        self.assertFalse(SHADOW.authoritative_review_gate(review))

    def test_hash_valid_sidecar_output_is_not_materialized(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_json(
                root / "accepted" / "manifest.json", {}
            )
            sidecar = write_bytes(
                root
                / "partial_review_prefill"
                / "snapshot"
                / "membership_review_proposals.ndjson",
                b'{"proposal_only":true}\n',
            )
            with self.assertRaisesRegex(
                SHADOW.ShadowError, "non-authoritative",
            ):
                SHADOW.output_receipts(
                    manifest_path,
                    {
                        "outputs": {
                            "canonical_ledger": {
                                "path": str(sidecar),
                                "sha256": digest(sidecar),
                                "size": sidecar.stat().st_size,
                            }
                        }
                    },
                    "accepted_generation",
                )

    def test_missing_required_congestion_blocks_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            run_dir = root / "run"
            workspace.mkdir()
            run_dir.mkdir()
            producers, baseline = source_context(root)
            contracts = (
                SHADOW.ImmutableInputContract(
                    "data/engine/congestion.json", ("behavior",)
                ),
                SHADOW.ImmutableInputContract(
                    "optional.json", ("display",), required=False
                ),
            )
            with self.assertRaisesRegex(
                SHADOW.ShadowError, "data/engine/congestion.json"
            ):
                SHADOW.stage_immutable_consumer_inputs(
                    run_dir,
                    producers=producers,
                    baseline=baseline,
                    workspace_root=workspace,
                    contracts=contracts,
                )
            self.assertFalse((run_dir / "data" / "engine" / "congestion.json").exists())
            self.assertFalse(
                (run_dir / "manual_bill_evidence.csv").exists()
            )

    def test_full_declared_input_fixture_hash_stages_every_layer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            run_dir = root / "run"
            workspace.mkdir()
            run_dir.mkdir()
            for contract in SHADOW.IMMUTABLE_INPUT_CONTRACTS:
                if contract.required:
                    write_bytes(
                        workspace / contract.path,
                        f"source:{contract.path}\n".encode(),
                    )
            producers, baseline = source_context(root)
            result = SHADOW.stage_immutable_consumer_inputs(
                run_dir,
                producers=producers,
                baseline=baseline,
                workspace_root=workspace,
            )

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(
                len({row.path for row in SHADOW.IMMUTABLE_INPUT_CONTRACTS}),
                len(SHADOW.IMMUTABLE_INPUT_CONTRACTS),
            )
            for contract in SHADOW.IMMUTABLE_INPUT_CONTRACTS:
                self.assertEqual(
                    (run_dir / contract.path).is_file(),
                    contract.required,
                )
            self.assertEqual(
                (run_dir / "manual_bill_evidence.csv").read_bytes(),
                b"Category,Bill\nCFPB,LTR-CANONICAL\n",
            )
            self.assertTrue(
                (
                    run_dir
                    / "inputs"
                    / "immutable"
                    / "member_desk_bundle.json"
                ).is_file()
            )
            self.assertFalse((run_dir / "member_desk_bundle.json").exists())
            self.assertEqual(
                result["absent_optional"],
                sorted(
                    contract.path
                    for contract in SHADOW.IMMUTABLE_INPUT_CONTRACTS
                    if not contract.required
                ),
            )

    def test_transient_member_desk_exists_only_for_first_say_do(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            run_dir = root / "run"
            workspace.mkdir()
            run_dir.mkdir()
            write_bytes(workspace / "data" / "engine" / "congestion.json")
            producers, baseline = source_context(root)
            immutable = SHADOW.stage_immutable_consumer_inputs(
                run_dir,
                producers=producers,
                baseline=baseline,
                workspace_root=workspace,
                contracts=(
                    SHADOW.ImmutableInputContract(
                        "data/engine/congestion.json", ("behavior",)
                    ),
                ),
            )
            ledger = generation_ledger(run_dir, "a" * 64)
            prepared = SHADOW.prepare_step_prerequisites(
                run_dir,
                step_name=SHADOW.SAY_DO_FIRST_STEP,
                ledger=ledger,
                materialized={"immutable": immutable},
            )
            self.assertTrue((run_dir / "member_desk_bundle.json").is_file())
            SHADOW.release_step_transients(
                run_dir,
                step_name=SHADOW.SAY_DO_FIRST_STEP,
                prepared=prepared,
            )
            self.assertFalse((run_dir / "member_desk_bundle.json").exists())
            self.assertTrue(
                (
                    run_dir
                    / "inputs"
                    / "immutable"
                    / "member_desk_bundle.json"
                ).is_file()
            )

    def test_dashboard_requires_accepted_canonical_manual_copy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            manual = write_bytes(
                run_dir / "manual_bill_evidence.csv", b"accepted\n"
            )
            row = {
                "source_group": "accepted_generation",
                "logical_name": "manual_bill_evidence",
                "generation_bound": True,
                "materialized_path": "manual_bill_evidence.csv",
                "materialized_sha256": digest(manual),
            }
            write_json(
                run_dir / "input_materialization.json",
                {
                    "schema":
                        "legislation-shadow-input-materialization-v1",
                    "status": "PASS",
                    "production_written": False,
                    "records": [row],
                },
            )
            self.assertEqual(
                DASH.validate_prestaged_manual_evidence(run_dir)["sha256"],
                digest(manual),
            )
            row["materialized_sha256"] = "0" * 64
            write_json(
                run_dir / "input_materialization.json",
                {
                    "schema":
                        "legislation-shadow-input-materialization-v1",
                    "status": "PASS",
                    "production_written": False,
                    "records": [row],
                },
            )
            with self.assertRaisesRegex(
                DASH.DashboardBranchError, "hash mismatch"
            ):
                DASH.validate_prestaged_manual_evidence(run_dir)


class FirstPassPrerequisiteTests(unittest.TestCase):
    def test_pillar_prerequisite_is_generation_bound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            generation_id = "b" * 64
            ledger = generation_ledger(run_dir, generation_id)
            workbook = write_bytes(
                run_dir / "Moneyball Main Version - Blended.xlsx"
            )
            snapshot = write_json(
                run_dir / "data" / "engine" / "pillar_inputs_snapshot.json",
                {"O": {}, "E": {}, "F": {}},
            )
            write_json(run_dir / "data/members/formal_visibility_roles.json", {})
            workbook_receipt = write_json(
                run_dir / "legislation_workbook.receipt.json",
                {
                    "schema":
                        "legislation-workbook-recompute-receipt-v1",
                    "status": "PASS",
                    "staged_only": True,
                    "production_written": False,
                    "generation_id": generation_id,
                    "outputs": {
                        workbook.name: receipt(workbook, run_dir),
                        "data/engine/pillar_inputs_snapshot.json": receipt(
                            snapshot, run_dir
                        ),
                    },
                },
            )
            add_step(
                ledger,
                "recompute_legislative_raw_normalized_sublayer",
                [workbook, workbook_receipt],
                run_dir,
            )
            path = SHADOW.build_pillars_ranks_prerequisites(
                run_dir, ledger
            )
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["status"], "PASS")
            self.assertEqual(value["generation_id"], generation_id)
            self.assertEqual(
                set(value["artifacts"]),
                {
                    "Moneyball Main Version - Blended.xlsx",
                    # 2026-08-04: Prerequisite paths are repo-layout-bound; Phase 2 moved this artifact.
                    "data/engine/pillar_inputs_snapshot.json",
                    "data/members/formal_visibility_roles.json",
                },
            )

    def test_missing_pillar_input_blocks_prerequisite_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            ledger = generation_ledger(run_dir, "b" * 64)
            with self.assertRaisesRegex(
                SHADOW.ShadowError, "prerequisite source is missing"
            ):
                SHADOW.build_pillars_ranks_prerequisites(run_dir, ledger)
            self.assertFalse(
                (run_dir / "pillars_ranks_prerequisites.json").exists()
            )

    def test_full_network_prerequisite_fixture_validates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            generation_id = "c" * 64
            ledger = generation_ledger(run_dir, generation_id)
            by_step: dict[str, list[Path]] = {}
            for logical_name, contract in NETWORK.REQUIRED_ARTIFACTS.items():
                path = write_bytes(
                    run_dir / contract.path,
                    f"{logical_name}\n".encode(),
                )
                if not contract.generation_bound:
                    continue
                if logical_name == "say_do_first_pass":
                    continue
                step = SHADOW.NETWORK_FIRST_SOURCE_STEPS[contract.path]
                by_step.setdefault(step, []).append(path)
            for step, paths in by_step.items():
                add_step(ledger, step, paths, run_dir)

            say_do = run_dir / "say_do_gap.json"
            say_do_receipt = write_json(
                run_dir / "intermediate/say_do_first_pass.receipt.json",
                {
                    "schema": "legislation-say-do-pass-receipt-v1",
                    "status": "PASS",
                    "pass": "first",
                    "generation_id": generation_id,
                    "root_output": receipt(say_do, run_dir),
                },
            )
            add_step(
                ledger,
                SHADOW.SAY_DO_FIRST_STEP,
                [say_do_receipt],
                run_dir,
            )
            path = SHADOW.build_network_dependents_prerequisites(
                run_dir, ledger
            )
            validated = NETWORK.validate_prerequisites(run_dir, path)
            self.assertEqual(validated["generation_id"], generation_id)
            self.assertEqual(
                set(validated["artifacts"]),
                set(NETWORK.REQUIRED_ARTIFACTS),
            )


if __name__ == "__main__":
    unittest.main()
