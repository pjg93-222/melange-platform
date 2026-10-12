# -*- coding: utf-8 -*-
"""Focused tests for the pre-sync legislation readiness contract."""
from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from audit.drift_closure import CHANGED_COUNT, TOTAL, UNCHANGED_COUNT  # noqa: E402
import audit.audit_legislation_downstream_readiness as READINESS  # noqa: E402


class DownstreamReadinessTests(unittest.TestCase):
    def test_formula_cache_freshness_is_visible_but_non_gating(
        self,
    ) -> None:
        state = READINESS.formula_cache_freshness_observation_state()
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"], state["issues"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])
        self.assertFalse(state["authoritative_for_legislation"])
        self.assertFalse(state["authoritative_for_scoring"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])
        self.assertFalse(
            state["live_stored_formula_cache_freshness_proven"]
        )
        self.assertFalse(state["calculated_copy_promotion_eligible"])
        self.assertFalse(state["accepted_legislation_causality_proven"])
        self.assertTrue(all(state["retained_blockers"].values()))

    def test_formula_cache_freshness_missing_is_informational_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                READINESS,
                "FORMULA_CACHE_FRESHNESS_POINTER",
                Path(directory) / "missing.json",
            ):
                state = (
                    READINESS.formula_cache_freshness_observation_state()
                )
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["evidence_current"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])

    def test_workbook_ooxml_semantics_are_visible_but_non_gating(
        self,
    ) -> None:
        state = READINESS.workbook_ooxml_semantic_observation_state()
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"], state["issues"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])
        self.assertTrue(all(state["retained_blockers"].values()))
        self.assertFalse(state["authoritative_for_sync_or_promotion"])

    def test_workbook_ooxml_semantics_missing_is_informational_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                READINESS,
                "POST_WEEKLY_REFRESH_WORKBOOK_OOXML_SEMANTICS_POINTER",
                Path(directory) / "missing.json",
            ):
                state = (
                    READINESS.workbook_ooxml_semantic_observation_state()
                )
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["evidence_current"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])

    def test_behavior_stance_semantics_are_visible_but_non_gating(
        self,
    ) -> None:
        state = READINESS.behavior_stance_semantic_observation_state()
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"], state["issues"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertEqual(state["member_identity_closure"]["count"], 533)
        self.assertEqual(
            state["category_identity_order_closure"]["count"], 18
        )
        self.assertTrue(state["numeric_grouping_path_method_verified"])

    def test_behavior_stance_semantics_missing_is_informational_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                READINESS,
                "POST_WEEKLY_REFRESH_BEHAVIOR_STANCE_SEMANTICS_POINTER",
                Path(directory) / "missing.json",
            ):
                state = (
                    READINESS.behavior_stance_semantic_observation_state()
                )
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["evidence_current"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])

    def test_planning_dashboard_semantics_are_visible_but_non_gating(
        self,
    ) -> None:
        state = READINESS.planning_dashboard_semantic_observation_state()
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"], state["issues"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])
        self.assertFalse(state["authoritative_for_legislation"])
        self.assertFalse(state["authoritative_for_scoring"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])
        self.assertEqual(
            state["target_hash_closure"]["verified_target_count"],
            14,
        )
        self.assertTrue(
            state["unresolved_closure"]["all_targets_retain_both"]
        )

    def test_planning_dashboard_semantics_missing_is_informational_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                READINESS,
                "POST_WEEKLY_REFRESH_PLANNING_DASHBOARD_SEMANTICS_POINTER",
                Path(directory) / "missing.json",
            ):
                state = (
                    READINESS.planning_dashboard_semantic_observation_state()
                )
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["evidence_current"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertFalse(state["adds_pass_conditions"])
        self.assertFalse(state["adds_gates"])
        self.assertFalse(state["satisfies_blockers"])

    def test_downstream_drift_observation_is_current_but_non_gating(
        self,
    ) -> None:
        state = READINESS.downstream_drift_observation_state()
        self.assertEqual(state["status"], "CURRENT_INFORMATIONAL")
        self.assertTrue(state["evidence_current"], state["issues"])
        self.assertTrue(state["informational_only"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["can_satisfy_blocked_gate"])
        self.assertEqual(
            state["observational_closure"]["declared_file_count"],
            TOTAL,
        )
        self.assertEqual(
            state["observational_closure"]["changed_file_count"],
            CHANGED_COUNT,
        )
        self.assertEqual(
            state["observational_closure"]["unchanged_file_count"],
            UNCHANGED_COUNT,
        )

    def test_downstream_drift_observation_fails_closed_when_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                READINESS,
                "POST_WEEKLY_REFRESH_DOWNSTREAM_DRIFT_POINTER",
                Path(directory) / "missing.json",
            ):
                state = READINESS.downstream_drift_observation_state()
        self.assertEqual(state["status"], "MISSING")
        self.assertFalse(state["evidence_current"])
        self.assertFalse(state["affects_gate_decisions"])
        self.assertFalse(state["authoritative_for_sync_or_promotion"])

    def test_generated_at_is_deterministic_input_evidence_watermark(
        self,
    ) -> None:
        report = {
            "generated_at": None,
            "receipt": {
                "mtime": "2026-07-25T10:00:00-05:00",
            },
            "nested": [
                {"mtime": "2026-07-25T16:00:00+00:00"},
                {"generated_at": "2099-01-01T00:00:00+00:00"},
            ],
        }
        observed = READINESS.deterministic_generated_at(report)
        self.assertEqual("2026-07-25T16:00:00+00:00", observed)
        self.assertEqual(
            observed,
            READINESS.deterministic_generated_at(report),
        )

    def test_generated_at_fails_closed_without_input_receipt_mtime(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "no input mtimes"):
            READINESS.deterministic_generated_at({"generated_at": None})

    def test_unchanged_readiness_output_is_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            self.assertTrue(
                READINESS.write_text_if_changed(path, '{"status":"FAIL"}\n')
            )
            original_mtime = path.stat().st_mtime_ns
            self.assertFalse(
                READINESS.write_text_if_changed(path, '{"status":"FAIL"}\n')
            )
            self.assertEqual(original_mtime, path.stat().st_mtime_ns)
            self.assertTrue(
                READINESS.write_text_if_changed(path, '{"status":"PASS"}\n')
            )
            self.assertEqual('{"status":"PASS"}\n', path.read_text())

    def test_rollcall_source_readiness_binds_implementation_and_measure_audit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fec = root / "fec"
            stage = root / "stage"
            fec.mkdir()
            stage.mkdir()
            producer_dir = fec / "legislation"
            producer_dir.mkdir()
            collector = producer_dir / "collect_official_rollcall_events.py"
            billstatus = producer_dir / "official_billstatus_rollcall_source.py"
            collector.write_text("# collector\n", encoding="utf-8")
            billstatus.write_text("# billstatus\n", encoding="utf-8")
            match = stage / "rollcall_measure_match_audit.ndjson"
            match.write_text(
                json.dumps(
                    {
                        "canonical_key": "119:hr:1",
                        "measure_type": "hr",
                        "official_action_source": "billstatus",
                        "actions_fetched": True,
                        "error": None,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            supported = [
                "hconres",
                "hjres",
                "hr",
                "hres",
                "s",
                "sconres",
                "sjres",
                "sres",
            ]
            manifest = stage / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "accepted_measure_count": 1,
                        "official_action_source_policy": {
                            "billstatus_no_key_supported_measure_types": supported,
                            "billstatus_amendment_support": False,
                            "selected_source_counts": {"billstatus": 1},
                        },
                        "implementation_sources": [
                            READINESS.file_receipt(collector),
                            READINESS.file_receipt(billstatus),
                        ],
                        "outputs": {
                            match.name: READINESS.file_receipt(match),
                        },
                    }
                ),
                encoding="utf-8",
            )
            state = root / "official_rollcalls.json"
            state.write_text(
                json.dumps(
                    {
                        "schema": READINESS.PRESYNC.STAGE_STATE_SCHEMA,
                        "stage": "official_rollcalls",
                        "manifest": READINESS.file_receipt(manifest),
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(READINESS, "ROOT", root),
                mock.patch.object(READINESS, "ROLLCALL_STAGE_STATE", state),
            ):
                result = READINESS.official_rollcall_source_state()
                self.assertTrue(result["passed"], result["issues"])
                billstatus.write_text("# changed\n", encoding="utf-8")
                stale = READINESS.official_rollcall_source_state()
                self.assertFalse(stale["passed"])
                self.assertTrue(
                    any("stale" in issue for issue in stale["issues"])
                )

    def test_rollcall_identity_readiness_binds_current_absent_map_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "acceptance_passed": True,
                        "blocking_issue_count": 0,
                        "member_identity_resolution": {
                            "supplemental_member_map": {
                                "status": "absent",
                                "present": False,
                                "receipt": None,
                            },
                            "accepted_event_member_row_count": 1,
                            "resolution_status_counts": {"included": 1},
                            "included_resolution_counts": {
                                "official_xml_bioguide": 1,
                                "supplemental_member_map": 0,
                            },
                            "unmatched_member_row_count": 0,
                            "ambiguous_member_row_count": 0,
                            "unresolved_or_ambiguous_member_row_count": 0,
                            "all_accepted_event_members_uniquely_resolved": True,
                            "fuzzy_matching_used": False,
                        },
                    }
                ),
                encoding="utf-8",
            )
            state = root / "official_rollcalls.json"
            state.write_text(
                json.dumps(
                    {
                        "schema": READINESS.PRESYNC.STAGE_STATE_SCHEMA,
                        "stage": "official_rollcalls",
                        "manifest": READINESS.file_receipt(manifest),
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                READINESS, "ROLLCALL_STAGE_STATE", state
            ):
                result = READINESS.official_rollcall_identity_state()
            self.assertTrue(result["passed"], result["issues"])
            self.assertEqual(
                "absent",
                result["supplemental_member_map"]["status"],
            )
            self.assertEqual(
                1,
                result["included_resolution_counts"][
                    "official_xml_bioguide"
                ],
            )

    def test_live_discovery_selection_uses_presync_best_exact_progress(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "strict_run"
            query_dir = run / "queries"
            query_dir.mkdir(parents=True)
            registry = root / "registry.json"
            registry.write_text("{}", encoding="utf-8")
            registry_hash = READINESS.sha256(registry)
            selected_ids = [f"q{i:02d}" for i in range(62)]
            records_hash = READINESS.PRESYNC.sha256_bytes(b"[]")
            validation = {
                "exact_result_total": True,
                "unique_result_total": True,
                "every_page_exact": True,
            }
            query = query_dir / "q00.json"
            query.write_text(
                json.dumps(
                    {
                        "schema": "congress-advanced-search-query-v1",
                        "query_id": "q00",
                        "registry_sha256": registry_hash,
                        "result_total": 0,
                        "unique_record_count": 0,
                        "page_count": 0,
                        "records_sha256": records_hash,
                        "validation": validation,
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            base = {
                "schema": "acu-congress-advanced-pages-manifest-v1",
                "status": "running",
                "primary_instrument": READINESS.PRESYNC.PRIMARY_INSTRUMENT,
                "quorum_used": False,
                "registry_sha256": registry_hash,
                "selected_query_ids": selected_ids,
            }
            proven = run / "manifest.partial.proven.json"
            proven.write_text(
                json.dumps(
                    {
                        **base,
                        "queries": [
                            {
                                "query_id": "q00",
                                "query_file": "queries/q00.json",
                                "result_total": 0,
                                "unique_record_count": 0,
                                "page_count": 0,
                                "records_sha256": records_hash,
                                "validation": validation,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            empty_retry = run / "manifest.partial.empty.json"
            empty_retry.write_text(
                json.dumps({**base, "queries": []}),
                encoding="utf-8",
            )
            os.utime(
                proven,
                ns=(1_000_000_000, 1_000_000_000),
            )
            os.utime(
                empty_retry,
                ns=(2_000_000_000, 2_000_000_000),
            )
            selected, manifest = READINESS.current_discovery_manifest(
                run,
                registry,
            )
            self.assertEqual(selected, proven)
            self.assertEqual(
                [row["query_id"] for row in manifest["queries"]],
                ["q00"],
            )

    def test_acu_title_plan_requires_valid_resolved_neutral_contract(
        self,
    ) -> None:
        valid_state = {
            "acu_title_query_plan_passed": True,
            "acu_title_corroboration_query_plan": {
                "manifest_valid": True,
                "acceptance_passed": True,
                "unresolved_measure_count": 0,
                "unresolved_rows_visible": True,
                "absence_is_neutral": True,
                "automatic_decisions_disabled": True,
                "official_relevance_congress_gov_only": True,
                "sponsorship_roles_congress_gov_only": True,
            },
        }
        passed, details = READINESS.acu_title_query_plan_readiness(
            valid_state
        )
        self.assertTrue(passed)
        self.assertEqual(details["absence_role"], "neutral")
        self.assertFalse(details["automatic_inclusion_or_exclusion"])
        self.assertEqual(
            details["official_relevance_authority"],
            "Congress.gov only",
        )
        unresolved = json.loads(json.dumps(valid_state))
        unresolved["acu_title_query_plan_passed"] = False
        unresolved["acu_title_corroboration_query_plan"].update(
            {
                "acceptance_passed": False,
                "unresolved_measure_count": 3,
            }
        )
        passed, details = READINESS.acu_title_query_plan_readiness(
            unresolved
        )
        self.assertFalse(passed)
        self.assertEqual(details["unresolved_measure_count"], 3)

    def test_acu_title_plan_producer_is_in_readiness_inventory(self) -> None:
        producer = READINESS.REQUIRED_STAGED_PRODUCERS[
            "acu_title_corroboration_query_plan"
        ]
        self.assertEqual(
            producer.name,
            "build_acu_bill_title_corroboration_query_plan.py",
        )
        self.assertTrue(producer.is_file())

    def test_query_specificity_producer_is_in_readiness_inventory(self) -> None:
        producer = READINESS.REQUIRED_STAGED_PRODUCERS[
            "query_specificity_audit"
        ]
        self.assertEqual(
            producer.name,
            "audit_legislation_query_specificity.py",
        )
        self.assertTrue(producer.is_file())

    def test_assigned_literal_reads_only_the_requested_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "contract.py"
            source.write_text(
                "OTHER = {'116': 99}\n"
                "DECAY = {116: 0.16, 117: 0.33, 118: 0.66, 119: 1.0}\n",
                encoding="utf-8",
            )
            observed = READINESS.assigned_literal(source, "DECAY")
        self.assertEqual(
            READINESS.normalize_decay(observed),
            READINESS.EXPECTED_DECAY,
        )

    def test_nonliteral_or_malformed_decay_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "contract.py"
            source.write_text(
                "DECAY = dict([(116, 0.16)])\n",
                encoding="utf-8",
            )
            observed = READINESS.assigned_literal(source, "DECAY")
        self.assertIsNone(observed)
        self.assertIsNone(READINESS.normalize_decay({"bad": object()}))

    def test_presync_decay_checks_staged_producers_not_live_cutover(self) -> None:
        contract = READINESS.decay_contract()
        self.assertEqual(contract["expected"], READINESS.EXPECTED_DECAY)
        self.assertTrue(contract["staged_passed"])
        self.assertTrue(contract["propagation_audit_passed"])
        self.assertEqual(contract["propagation_audit"]["status"], "PASS")
        self.assertEqual(
            contract["propagation_audit"]["staged_contracts"],
            {"passed": 9, "required": 9},
        )
        self.assertEqual(
            contract["propagation_audit"]["consumer_classification"],
            {"classified": 16, "required": 16},
        )
        self.assertEqual(
            contract["propagation_audit"]["production_freeze"],
            {
                "matched": TOTAL,
                "required": TOTAL,
                "cutover_detected": False,
            },
        )
        self.assertTrue(contract["pre_sync_passed"])
        self.assertEqual(
            set(contract["staged_producers"]),
            {"accepted_generation", "base_network"},
        )
        for row in contract["staged_producers"].values():
            self.assertEqual(row["observed"], READINESS.EXPECTED_DECAY)
            self.assertTrue(row["passed"])
        self.assertIn("cutover_pending", contract["live_production"])

    def test_readiness_decay_gate_fails_closed_without_current_receipt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with mock.patch.object(
                READINESS,
                "DECAY_PROPAGATION_AUDIT",
                missing,
            ):
                contract = READINESS.decay_contract()
        self.assertTrue(contract["staged_passed"])
        self.assertFalse(contract["propagation_audit_passed"])
        self.assertFalse(contract["pre_sync_passed"])
        self.assertEqual(contract["propagation_audit"]["status"], "MISSING")

    def test_centrality_rank_contract_preserves_current_method(self) -> None:
        contract = READINESS.centrality_rank_contract()
        self.assertTrue(contract["passed"])
        self.assertEqual(contract["status"], "PRESERVED")
        self.assertEqual(
            contract["base_or_augmented_centrality_effective_rank_weight"],
            0.0,
        )
        self.assertFalse(contract["method_change_applied"])

    def test_spreadsheet_model_verification_requires_numeric_sublayer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            base = {
                "schema": (
                    "legislation-spreadsheet-model-verification-v1"
                ),
                "status": "PASS",
                "engine": "@oai/artifact-tool",
                "production_written": False,
                "workbook_edited": False,
                "input": {"unchanged_after_verification": True},
                "formula_error_scan": {"record_count": 0},
                "critical_numeric_sample": {
                    "passed": True,
                    "non_numeric_cells": 0,
                },
            }
            receipt.write_text(
                json.dumps(base), encoding="utf-8"
            )
            with mock.patch.object(
                READINESS,
                "SPREADSHEET_MODEL_VERIFICATION_RECEIPT",
                receipt,
            ):
                passed, _ = (
                    READINESS.spreadsheet_model_verification_state()
                )
                self.assertTrue(passed)
                base["critical_numeric_sample"] = {
                    "passed": False,
                    "non_numeric_cells": 171,
                }
                base["status"] = "BLOCKED"
                receipt.write_text(
                    json.dumps(base), encoding="utf-8"
                )
                passed, _ = (
                    READINESS.spreadsheet_model_verification_state()
                )
                self.assertFalse(passed)

    def test_spreadsheet_import_is_bound_to_frozen_workbook_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / READINESS.WORKBOOK_NAME
            workbook.write_bytes(b"frozen-workbook\n")
            baseline = root / "baseline.json"
            baseline.write_text(
                json.dumps(
                    {
                        "schema": "legislation-production-baseline-v1",
                        "files": [
                            {
                                "copy": str(workbook),
                                "sha256": READINESS.sha256(workbook),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            receipt = root / "import.json"
            payload = {
                "schema": (
                    "legislation-spreadsheet-engine-import-verification-v1"
                ),
                "status": "PASS",
                "engine": "@oai/artifact-tool",
                "import_completed": True,
                "production_written": False,
                "workbook_edited": False,
                "input": {
                    "sha256": READINESS.sha256(workbook),
                    "unchanged_after_import": True,
                },
                "missing_required_sheets": [],
            }
            receipt.write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch.object(
                    READINESS, "PRODUCTION_BASELINE", baseline
                ),
                mock.patch.object(
                    READINESS, "SPREADSHEET_IMPORT_RECEIPT", receipt
                ),
            ):
                passed, _ = READINESS.spreadsheet_import_state()
                self.assertTrue(passed)
                payload["input"]["sha256"] = "0" * 64
                receipt.write_text(json.dumps(payload), encoding="utf-8")
                passed, _ = READINESS.spreadsheet_import_state()
                self.assertFalse(passed)

    def test_workbook_recompute_requires_two_hash_bound_verified_runs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "role_policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "schema": READINESS.ROLE_APPROVAL_SCHEMA,
                        "status": "APPROVED",
                        "approved_by_user": True,
                        "human_approved": True,
                        "selected_policy": (
                            READINESS.ROLE_METHOD_PROFILE_ID
                        ),
                        "selected_method_profile": (
                            READINESS.ROLE_METHOD_PROFILE_ID
                        ),
                        "selected_role_weight_policy": (
                            "workbook_legacy_4_2_1"
                        ),
                        "consumer_methods": (
                            READINESS.ROLE_METHOD_PROFILE
                        ),
                        "applies_to": list(
                            READINESS.ROLE_METHOD_PROFILE
                        ),
                        "accepted_ledger_sha256": "a" * 64,
                    }
                ),
                encoding="utf-8",
            )
            generation = "a" * 64
            run_entries = {}
            receipt_paths = []
            for number in (1, 2):
                run_dir = root / f"run_{number}"
                run_dir.mkdir()
                workbook = run_dir / READINESS.WORKBOOK_NAME
                workbook.write_bytes(b"verified-workbook\n")
                workbook_hash = READINESS.sha256(workbook)
                producer_receipt = (
                    run_dir / READINESS.WORKBOOK_RECOMPUTE_RECEIPT_NAME
                )
                producer_receipt.write_text(
                    json.dumps(
                        {
                            "schema": (
                                "legislation-workbook-recompute-receipt-v1"
                            ),
                            "status": "PASS",
                            "approved_engine": "@oai/artifact-tool",
                            "staged_only": True,
                            "production_written": False,
                            "generation_id": generation,
                            "selected_role_weight_policy": (
                                "workbook_legacy_4_2_1"
                            ),
                            "selected_method_profile": (
                                READINESS.ROLE_METHOD_PROFILE_ID
                            ),
                            "consumer_role_methods": (
                                READINESS.ROLE_METHOD_PROFILE
                            ),
                            "role_policy_approval_sha256": (
                                READINESS.sha256(policy)
                            ),
                            "input_workbook": {"sha256": "b" * 64},
                            "output_workbook": {
                                "sha256": workbook_hash,
                            },
                            "artifact_tool_verification": {
                                "passed": True,
                                "required_sheets_present": True,
                                "formula_error_count": 0,
                                "non_numeric_critical_cells": 0,
                            },
                            "all_18_categories_recomputed": True,
                            "workbook_role_weights_verified": True,
                            "legislative_efficiency_definition_consistent": (
                                True
                            ),
                            "unexplained_cell_count": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                receipt_paths.append(producer_receipt)
                run_manifest = run_dir / "run_manifest.json"
                run_manifest.write_text(
                    json.dumps(
                        {
                            "schema": (
                                "legislation-shadow-isolated-run-v1"
                            ),
                            "generation_id": generation,
                            "outputs": {
                                READINESS.WORKBOOK_NAME: {
                                    "path": READINESS.WORKBOOK_NAME,
                                    "byte_sha256": workbook_hash,
                                    "size": workbook.stat().st_size,
                                },
                                READINESS.WORKBOOK_RECOMPUTE_RECEIPT_NAME: {
                                    "path": (
                                        READINESS
                                        .WORKBOOK_RECOMPUTE_RECEIPT_NAME
                                    ),
                                    "byte_sha256": READINESS.sha256(
                                        producer_receipt
                                    ),
                                    "size": producer_receipt.stat().st_size,
                                },
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                run_entries[f"run_{number}_manifest"] = (
                    READINESS.file_receipt(run_manifest)
                )
            audit = root / "audit.json"
            audit.write_text(
                json.dumps({"status": "PASS", **run_entries}),
                encoding="utf-8",
            )
            staged = root / "staged.json"
            staged.write_text(
                json.dumps(
                    {
                        "schema": (
                            "legislation-shadow-recompute-acceptance-v1"
                        ),
                        "status": "PASS",
                        "acceptance_passed": True,
                        "idempotence_passed": True,
                        "production_promotion_performed": False,
                        "audit": READINESS.file_receipt(audit),
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(
                    READINESS, "STAGED_ACCEPTANCE", staged
                ),
                mock.patch.object(
                    READINESS, "ROLE_POLICY_RECEIPT", policy
                ),
            ):
                passed, _ = READINESS.workbook_recompute_state()
                self.assertTrue(passed)
                receipt_paths[0].write_text(
                    '{"status":"PASS"}', encoding="utf-8"
                )
                passed, details = READINESS.workbook_recompute_state()
                self.assertFalse(passed)
                self.assertEqual(details["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
