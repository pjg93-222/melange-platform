# -*- coding: utf-8 -*-
"""Fail-closed pre-sync audit for the legislation refresh.

This is deliberately read-only with respect to production.  It inventories the
strict Congress.gov run, review/enrichment/reconciliation gates, the selected
production artifacts, and the known downstream producer/orchestration
contracts.  It writes only the requested audit JSON/Markdown files.

The report is not a scoring producer.  ``sync_allowed`` can become true only
after the coverage gate passes and the staged recomputation publishes its own
acceptance receipt.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import glob
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from legislation import run_legislation_presync_audit as PRESYNC
ROOT = HERE.parent
DEFAULT_DISCOVERY_RUN = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_RUN = ROOT / "audits" / "legfix5" / "congress_advanced_final_20260725"
DEFAULT_OUT = ROOT / "audits" / "legfix5" / "downstream_readiness"
REGISTRY = ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"

EXPECTED_DECAY = {"116": 0.16, "117": 0.33, "118": 0.66, "119": 1.0}
ROLE_APPROVAL_SCHEMA = "legislation-role-method-profile-approval-v1"
ROLE_METHOD_PROFILE_ID = "consumer_specific_legacy_preservation"
ROLE_METHOD_PROFILE = {
    "archive_leg": {
        "method": "fixed_role_weights",
        "weights": {
            "sponsor": 3.0,
            "original_cosponsor": 2.0,
            "cosponsor": 1.0,
        },
    },
    "workbook_grid": {
        "method": "fixed_role_weights",
        "weights": {
            "sponsor": 4.0,
            "original_cosponsor": 2.0,
            "cosponsor": 1.0,
        },
    },
    "stance": {
        "method": "fixed_role_weights",
        "weights": {
            "sponsor": 1.0,
            "original_cosponsor": 1.0,
            "cosponsor": 1.0,
        },
    },
    "say_do": {
        "method": "delivery_multiplier",
        "weights": {
            "sponsor": 1.0,
            "original_cosponsor": 0.5,
            "cosponsor": 0.25,
        },
    },
    "base_network": {
        "method": "cosponsor_count_normalized_edge",
        "original_cosponsor_numerator": 1.0,
        "cosponsor_numerator": 0.5,
        "denominator": "sqrt(current_cosponsor_count)",
    },
}
REQUIRED_PIPELINE_STEPS = (
    "build_archive_legislative.py",
    "behavior_panel.py",
    "behavioral_dispositions.py",
    "coop_v2_candidate.py",
    "receptivity_layer.py",
    "build_multilayer_network.py",
    "per_category_stance.py",
    "say_do_gap.py",
)
REQUIRED_STAGED_PRODUCERS = {
    "acu_title_corroboration_query_plan": (
        ROOT / "fec" / "legislation" / "build_acu_bill_title_corroboration_query_plan.py"
    ),
    "accepted_corpus_importer": ROOT / "fec" / "build_accepted_legislation_generation.py",
    "base_network_and_centrality": ROOT / "fec" / "legislation" / "build_legislation_base_network.py",
    "type_aware_direction_ledger": ROOT / "fec" / "legislation" / "build_legislation_direction_ledger.py",
    "official_rollcall_ingestion": ROOT / "fec" / "legislation" / "collect_official_rollcall_events.py",
    "official_billstatus_action_source": (
        ROOT / "fec" / "legislation" / "official_billstatus_rollcall_source.py"
    ),
    "billstatus_bulk_enrichment_snapshot": (
        ROOT / "fec" / "legislation" / "stage_billstatus_bulk_enrichment.py"
    ),
    "query_specificity_audit": (
        ROOT / "fec" / "audit" / "audit_legislation_query_specificity.py"
    ),
    "official_rollcall_aggregate": (
        ROOT / "fec" / "legislation" / "materialize_legislation_rollcall_aggregate.py"
    ),
    "base_network_prebehavior_adapter": (
        ROOT / "fec" / "legislation" / "materialize_legislation_base_and_bipartisan.py"
    ),
    "accepted_archive_and_workbook_adapter": (
        ROOT / "fec" / "legislation" / "build_accepted_legislation_workbook.py"
    ),
    "legislative_workbook_recompute": (
        ROOT / "fec" / "legislation" / "recompute_legislative_workbook.py"
    ),
    "legislative_workbook_audited_layout": (
        ROOT / "fec" / "legislation" / "extract_legislation_workbook_audited_layout.py"
    ),
    "legislative_workbook_numeric_plan": (
        ROOT / "fec" / "legislation" / "build_legislation_workbook_numeric_plan.py"
    ),
    "approved_generation_assembler": (
        ROOT / "fec" / "legislation" / "assemble_approved_legislation_generation.py"
    ),
    "membership_triage": ROOT / "fec" / "legislation" / "build_legislation_membership_triage.py",
    "review_batch_orchestrator": ROOT / "fec" / "legislation" / "run_legislation_review_batches.py",
    "role_weight_policy_scenarios": ROOT / "fec" / "legislation" / "compute_legislation_policy_scenarios.py",
    "shadow_consumer_isolation_audit": (
        ROOT / "fec" / "legislation" / "build_legislation_shadow_consumer_plan.py"
    ),
    "presync_audit_orchestrator": (
        ROOT / "fec" / "legislation" / "run_legislation_presync_audit.py"
    ),
    "isolated_say_do_first_pass": (
        ROOT / "fec" / "legislation" / "run_legislation_say_do_first_pass.py"
    ),
    "isolated_say_do_second_pass": (
        ROOT / "fec" / "legislation" / "run_legislation_say_do_second_pass.py"
    ),
    "isolated_pillars_and_ranks": (
        ROOT / "fec" / "legislation" / "run_legislation_pillars_and_ranks.py"
    ),
    "isolated_network_dependents": (
        ROOT / "fec" / "legislation" / "run_legislation_network_dependents.py"
    ),
    "isolated_dashboard_member_desk": (
        ROOT / "fec" / "legislation" / "run_legislation_dashboard_member_desk.py"
    ),
    "isolated_second_pass_dependents": (
        ROOT / "fec" / "legislation" / "run_legislation_second_pass_dependents.py"
    ),
    "staged_recompute_runner": ROOT / "fec" / "legislation" / "run_legislation_shadow_recompute.py",
}
STAGED_ACCEPTANCE = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "staged_acceptance_receipt.json"
)
SHADOW_STAGE_STATE = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "state"
    / "shadow_recompute.json"
)
ROLLCALL_STAGE_STATE = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "state"
    / "official_rollcalls.json"
)
COMPUTATION_POLICY_RECEIPT = (
    ROOT
    / "audits"
    / "legfix5"
    / "manual_inputs"
    / "legislation_workbook_computation_policy_approval.json"
)
AUDITED_LAYOUT = (
    ROOT
    / "audits"
    / "legfix5"
    / "spreadsheet_engine_verification"
    / "audited_layout_sidecar_ooxml_v1"
    / "audited_member_layout.json"
)
NUMERIC_PLAN = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "stages"
    / "numeric_plan"
    / "numeric_plan.json"
)
PRODUCTION_BASELINE = (
    ROOT
    / "audits"
    / "legfix5"
    / "production_baseline_full_downstream"
    / "baseline_manifest.json"
)
DECAY_PROPAGATION_AUDIT = (
    ROOT
    / "audits"
    / "legfix5"
    / "decay_propagation"
    / "decay_propagation_audit.json"
)
BILLSTATUS_SNAPSHOT_ROOT = (
    ROOT
    / "audits"
    / "legfix5"
    / "billstatus_bulk_enrichment"
    / "snapshots"
)
BILLSTATUS_FETCH_ROOT = (
    ROOT
    / "audits"
    / "legfix5"
    / "billstatus_bulk_enrichment"
    / "fetch_runs"
)
BILLSTATUS_COMPLETENESS_ROOT = (
    ROOT
    / "audits"
    / "legfix5"
    / "billstatus_bulk_enrichment"
    / "completeness"
)
QUERY_SPECIFICITY_AUDIT = (
    ROOT
    / "audits"
    / "legfix5"
    / "query_specificity"
    / "current"
    / "query_specificity_audit.json"
)
POST_WEEKLY_REFRESH_DOWNSTREAM_DRIFT_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "post_weekly_refresh_downstream_drift"
    / "current.json"
)
POST_WEEKLY_REFRESH_BEHAVIOR_STANCE_SEMANTICS_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "post_weekly_refresh_behavior_stance_semantics"
    / "current.json"
)
POST_WEEKLY_REFRESH_PLANNING_DASHBOARD_SEMANTICS_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "post_weekly_refresh_planning_dashboard_semantics"
    / "current.json"
)
POST_WEEKLY_REFRESH_WORKBOOK_OOXML_SEMANTICS_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "post_weekly_refresh_workbook_ooxml_semantics"
    / "current.json"
)
FORMULA_CACHE_FRESHNESS_POINTER = (
    ROOT
    / "audits"
    / "legfix5"
    / "formula_cache_freshness"
    / "current.json"
)
CONSUMER_ISOLATION_AUDIT = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "consumer_isolation_audit.json"
)
ROLE_POLICY_RECEIPT = (
    ROOT
    / "audits"
    / "legfix5"
    / "manual_inputs"
    / "role_weight_policy_approval.json"
)
SPREADSHEET_IMPORT_RECEIPT = (
    ROOT
    / "audits"
    / "legfix5"
    / "spreadsheet_engine_verification"
    / "import_receipt.json"
)
SPREADSHEET_MODEL_VERIFICATION_RECEIPT = (
    ROOT
    / "audits"
    / "legfix5"
    / "spreadsheet_engine_verification"
    / "model_verification"
    / "model_verification_receipt.json"
)
WORKBOOK_NAME = "Moneyball Main Version - Blended.xlsx"
WORKBOOK_RECOMPUTE_RECEIPT_NAME = "legislation_workbook.receipt.json"


def downstream_drift_observation_state() -> dict[str, Any]:
    """Expose validated weekly-refresh drift without granting gate authority."""
    _, state = PRESYNC.validate_post_weekly_refresh_downstream_drift(
        POST_WEEKLY_REFRESH_DOWNSTREAM_DRIFT_POINTER
    )
    return state


def behavior_stance_semantic_observation_state() -> dict[str, Any]:
    """Expose domain-semantic drift as informational evidence only."""
    _, state = (
        PRESYNC.validate_post_weekly_refresh_behavior_stance_semantics(
            POST_WEEKLY_REFRESH_BEHAVIOR_STANCE_SEMANTICS_POINTER
        )
    )
    return state


def planning_dashboard_semantic_observation_state() -> dict[str, Any]:
    """Expose planning/dashboard semantics as informational evidence only."""
    _, state = (
        PRESYNC.validate_post_weekly_refresh_planning_dashboard_semantics(
            POST_WEEKLY_REFRESH_PLANNING_DASHBOARD_SEMANTICS_POINTER
        )
    )
    return state


def workbook_ooxml_semantic_observation_state() -> dict[str, Any]:
    """Expose workbook OOXML semantics as informational evidence only."""
    _, state = (
        PRESYNC.validate_post_weekly_refresh_workbook_ooxml_semantics(
            POST_WEEKLY_REFRESH_WORKBOOK_OOXML_SEMANTICS_POINTER
        )
    )
    return state


def formula_cache_freshness_observation_state() -> dict[str, Any]:
    """Expose isolated temp-copy cache evidence as informational only."""
    _, state = PRESYNC.validate_formula_cache_freshness_observation(
        FORMULA_CACHE_FRESHNESS_POINTER
    )
    return state


def acu_title_query_plan_readiness(
    coverage_gate_audit: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Expose the title-plan gate without treating press absence as evidence."""
    raw = coverage_gate_audit.get("acu_title_corroboration_query_plan")
    state = dict(raw) if isinstance(raw, dict) else {}
    passed = bool(
        coverage_gate_audit.get("acu_title_query_plan_passed") is True
        and state.get("manifest_valid") is True
        and state.get("acceptance_passed") is True
        and state.get("unresolved_measure_count") == 0
        and state.get("unresolved_rows_visible") is True
        and state.get("absence_is_neutral") is True
        and state.get("automatic_decisions_disabled") is True
        and state.get("official_relevance_congress_gov_only") is True
        and state.get("sponsorship_roles_congress_gov_only") is True
    )
    return passed, {
        **state,
        "readiness_passed": passed,
        "positive_evidence_role": "corroborative_only",
        "absence_role": "neutral",
        "automatic_inclusion_or_exclusion": False,
        "official_relevance_authority": "Congress.gov only",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--discovery-run",
        type=Path,
        default=DEFAULT_DISCOVERY_RUN,
        help="strict 62-query Congress.gov Advanced Search generation",
    )
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime": dt.datetime.fromtimestamp(
            stat.st_mtime, tz=dt.timezone.utc
        ).isoformat(),
        "sha256": sha256(path),
    }


def deterministic_generated_at(value: Any) -> str:
    """Return a stable evidence watermark for an audit-only receipt.

    ``generated_at`` used to be the wall-clock time of each check-only run,
    which made an otherwise identical readiness receipt change bytes on every
    invocation.  Freshness is enforced by the current hash/size bindings
    embedded in the report, so the newest observed input-receipt mtime is the
    appropriate deterministic display timestamp.
    """
    observed: list[dt.datetime] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "mtime" and isinstance(child, str):
                    try:
                        parsed = dt.datetime.fromisoformat(
                            child.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass
                    else:
                        if parsed.tzinfo is None:
                            parsed = parsed.replace(tzinfo=dt.timezone.utc)
                        observed.append(parsed.astimezone(dt.timezone.utc))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if not observed:
        raise ValueError(
            "readiness receipt has no input mtimes for deterministic "
            "generated_at"
        )
    return max(observed).isoformat()


def write_text_if_changed(path: Path, content: str) -> bool:
    """Write audit output only when its exact bytes differ."""
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
    path.write_text(content, encoding="utf-8")
    return True


def newest(paths: list[Path]) -> Path | None:
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def newest_augmented_or_base(paths: list[Path]) -> Path | None:
    augmented = [
        path for path in paths if "AUG" in path.name.upper()
    ]
    return newest(augmented or paths)


def current_discovery_manifest(
    run_dir: Path, registry_path: Path
) -> tuple[Path | None, dict[str, Any] | None]:
    """Use the pre-sync selector; never fall back to newest-file selection."""
    selection_blockers: list[dict[str, Any]] = []
    selected = PRESYNC.select_strict_manifest(
        {"coverage": {"strict_run": str(run_dir.resolve())}},
        workspace_root=ROOT,
        registry_path=registry_path.resolve(),
        blockers=selection_blockers,
    )
    if selected is None or selection_blockers:
        return None, None
    manifest = read_json(selected)
    if manifest is None:
        return None, None
    exact_progress = PRESYNC.strict_manifest_exact_progress(
        manifest,
        registry_sha256=sha256(registry_path),
        manifest_path=selected,
    )
    if exact_progress is None:
        return None, None
    return selected, manifest


def billstatus_snapshot_state(
    strict_manifest_path: Path | None,
    *,
    discovery_run: Path = DEFAULT_DISCOVERY_RUN,
) -> dict[str, Any]:
    """Expose current, non-authoritative BILLSTATUS snapshot evidence."""
    if strict_manifest_path is None:
        return {
            "status": "MISSING",
            "passed": False,
            "divergence_passed": False,
            "issues": ["selected strict manifest is missing"],
        }
    try:
        bundle = PRESYNC.BILLSTATUS_REFRESH.ensure_snapshot_bundle(
            snapshot_root=BILLSTATUS_SNAPSHOT_ROOT,
            strict_manifest_path=strict_manifest_path,
            records_path=(
                discovery_run / "congress_advanced_records.ndjson"
            ),
            memberships_path=(
                discovery_run / "congress_advanced_memberships.ndjson"
            ),
            exclusion_ledger_path=(
                discovery_run / "exclusion_ledger.ndjson"
            ),
            exclusion_provenance_paths=(
                discovery_run
                / "candidate_membership_review"
                / "false_positive_exclusions.ndjson",
            ),
            cumulative_manifest_path=discovery_run / "manifest.json",
            registry_path=REGISTRY,
            review_ledger_path=(
                discovery_run
                / "candidate_membership_review"
                / "membership_review_ledger.ndjson"
            ),
            gap_ledger_path=(
                discovery_run / "coverage_gap_ledger.ndjson"
            ),
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        PRESYNC.BILLSTATUS_REFRESH.RefreshError,
        PRESYNC.BILLSTATUS_ENRICHMENT.ContractError,
        PRESYNC.BILLSTATUS_DIVERGENCE.AuditError,
    ) as error:
        return {
            "status": "STALE_OR_INVALID",
            "passed": False,
            "divergence_passed": False,
            "issues": [str(error)],
        }
    snapshot_plan = Path(bundle["snapshot_plan"])
    divergence_audit = Path(bundle["divergence_audit"])
    passed, state = PRESYNC.validate_billstatus_snapshot_plan(
        snapshot_plan,
        strict_manifest_path=strict_manifest_path,
        discovery_records_path=(
            discovery_run / "congress_advanced_records.ndjson"
        ),
        exclusion_ledger_path=discovery_run / "exclusion_ledger.ndjson",
        exclusion_provenance_paths=(
            discovery_run
            / "candidate_membership_review"
            / "false_positive_exclusions.ndjson",
        ),
    )
    divergence_passed, divergence = (
        PRESYNC.validate_billstatus_divergence_audit(
            divergence_audit,
            strict_manifest_path=strict_manifest_path,
            discovery_records_path=(
                discovery_run / "congress_advanced_records.ndjson"
            ),
            memberships_path=(
                discovery_run / "congress_advanced_memberships.ndjson"
            ),
        )
    )
    worker_manifest, completeness_audit = (
        PRESYNC.billstatus_enrichment_evidence_paths(
            snapshot_plan_path=snapshot_plan,
            fetch_root=BILLSTATUS_FETCH_ROOT,
            completeness_root=BILLSTATUS_COMPLETENESS_ROOT,
        )
    )
    completeness_passed, completeness = (
        PRESYNC.validate_billstatus_enrichment_completeness(
            completeness_audit,
            expected_plan_path=snapshot_plan,
            expected_worker_manifest_path=worker_manifest,
        )
    )
    return {
        **state,
        "passed": passed and divergence_passed and completeness_passed,
        "bundle": bundle,
        "divergence_evidence": divergence,
        "divergence_passed": divergence_passed,
        "enrichment_completeness_evidence": completeness,
        "enrichment_completeness_passed": completeness_passed,
    }


def query_exact(query: dict[str, Any]) -> bool:
    validation = query.get("validation") or {}
    return bool(
        validation.get("exact_result_total")
        and validation.get("unique_result_total")
        and validation.get("every_page_exact")
    )


def source_contains(path: Path, token: str) -> bool:
    try:
        return token in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def approved_role_policy() -> tuple[bool, dict[str, Any] | None]:
    value = read_json(ROLE_POLICY_RECEIPT)
    passed = bool(
        value
        and value.get("schema") == ROLE_APPROVAL_SCHEMA
        and value.get("status") == "APPROVED"
        and value.get("approved_by_user") is True
        and value.get("human_approved") is True
        and value.get("selected_policy") == ROLE_METHOD_PROFILE_ID
        and value.get("selected_method_profile")
        == ROLE_METHOD_PROFILE_ID
        and value.get("selected_role_weight_policy")
        == "workbook_legacy_4_2_1"
        and value.get("consumer_methods") == ROLE_METHOD_PROFILE
        and value.get("applies_to") == list(ROLE_METHOD_PROFILE)
        and isinstance(value.get("accepted_ledger_sha256"), str)
        and len(value["accepted_ledger_sha256"]) == 64
    )
    return passed, value


def frozen_baseline_workbook() -> dict[str, Any] | None:
    manifest = read_json(PRODUCTION_BASELINE)
    rows = (manifest or {}).get("files") or []
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and Path(str(row.get("copy") or row.get("source") or "")).name
        == WORKBOOK_NAME
    ]
    if len(matches) != 1:
        return None
    row = matches[0]
    path = Path(str(row.get("copy") or row.get("source") or ""))
    expected = str(row.get("sha256") or "").casefold()
    if not path.is_file() or sha256(path) != expected:
        return None
    return {**file_receipt(path), "manifest_sha256": expected}


def spreadsheet_import_state() -> tuple[bool, dict[str, Any] | None]:
    value = read_json(SPREADSHEET_IMPORT_RECEIPT)
    baseline = frozen_baseline_workbook()
    passed = bool(
        value
        and baseline
        and value.get("schema")
        == "legislation-spreadsheet-engine-import-verification-v1"
        and value.get("status") == "PASS"
        and value.get("engine") == "@oai/artifact-tool"
        and value.get("import_completed") is True
        and value.get("production_written") is False
        and value.get("workbook_edited") is False
        and (value.get("input") or {}).get("unchanged_after_import") is True
        and str((value.get("input") or {}).get("sha256") or "").casefold()
        == baseline["sha256"]
        and value.get("missing_required_sheets") == []
    )
    return passed, value


def spreadsheet_model_verification_state(
) -> tuple[bool, dict[str, Any] | None]:
    value = read_json(SPREADSHEET_MODEL_VERIFICATION_RECEIPT)
    formula_scan = (value or {}).get("formula_error_scan") or {}
    numeric_sample = (value or {}).get("critical_numeric_sample") or {}
    passed = bool(
        value
        and value.get("schema")
        == "legislation-spreadsheet-model-verification-v1"
        and value.get("status") == "PASS"
        and value.get("engine") == "@oai/artifact-tool"
        and value.get("production_written") is False
        and value.get("workbook_edited") is False
        and (value.get("input") or {}).get(
            "unchanged_after_verification"
        )
        is True
        and formula_scan.get("record_count") == 0
        and numeric_sample.get("passed") is True
        and numeric_sample.get("non_numeric_cells") == 0
    )
    return passed, value


def _verified_receipt_path(
    item: Any, *, parent: Path | None = None
) -> Path | None:
    if not isinstance(item, dict) or not item.get("path"):
        return None
    path = Path(str(item["path"]))
    if not path.is_absolute() and parent is not None:
        path = parent / path
    path = path.resolve()
    expected = str(
        item.get("sha256") or item.get("byte_sha256") or ""
    ).casefold()
    if not path.is_file() or sha256(path) != expected:
        return None
    if item.get("size") is not None and path.stat().st_size != int(
        item["size"]
    ):
        return None
    return path


def official_rollcall_identity_state() -> dict[str, Any]:
    """Require a current staged receipt with every member uniquely resolved."""
    state = read_json(ROLLCALL_STAGE_STATE)
    manifest_path = _verified_receipt_path((state or {}).get("manifest"))
    manifest = read_json(manifest_path) if manifest_path else None
    passed, details = PRESYNC.validate_rollcall_identity_manifest(manifest)
    stage_current = bool(
        state
        and state.get("schema") == PRESYNC.STAGE_STATE_SCHEMA
        and state.get("stage") == "official_rollcalls"
        and manifest_path
    )
    return {
        **details,
        "passed": bool(stage_current and passed),
        "status": "PASS" if stage_current and passed else "FAIL",
        "stage_state": file_receipt(ROLLCALL_STAGE_STATE),
        "manifest": file_receipt(manifest_path) if manifest_path else None,
        "stage_state_current": stage_current,
    }


def _read_ndjson_objects(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rows = []
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return []
                rows.append(value)
    except (OSError, ValueError):
        return []
    return rows


def official_rollcall_source_state() -> dict[str, Any]:
    """Verify the staged action source and its current implementation closure."""
    state = read_json(ROLLCALL_STAGE_STATE)
    manifest_path = _verified_receipt_path((state or {}).get("manifest"))
    manifest = read_json(manifest_path) if manifest_path else None
    policy = (
        manifest.get("official_action_source_policy")
        if isinstance(manifest, dict)
        else None
    )
    issues = []
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
    if not isinstance(policy, dict):
        issues.append("official action source policy receipt is absent")
        policy = {}
    if policy.get("billstatus_no_key_supported_measure_types") != supported:
        issues.append("BILLSTATUS type allowlist is absent or changed")
    if policy.get("billstatus_amendment_support") is not False:
        issues.append("BILLSTATUS amendments must remain unsupported")
    counts = policy.get("selected_source_counts")
    if not isinstance(counts, dict):
        issues.append("selected action source counts are absent")
        counts = {}
    if int(counts.get("unavailable", 0) or 0):
        issues.append("one or more accepted measures lacks an official action source")
    accepted_count = int((manifest or {}).get("accepted_measure_count") or 0)
    if sum(int(value) for value in counts.values()) != accepted_count:
        issues.append("action source counts do not cover every accepted measure")

    expected_sources = {
        str((ROOT / "fec" / "legislation" / "collect_official_rollcall_events.py").resolve()),
        str(
            (
                ROOT / "fec" / "legislation" / "official_billstatus_rollcall_source.py"
            ).resolve()
        ),
    }
    implementation = (manifest or {}).get("implementation_sources")
    if not isinstance(implementation, list):
        issues.append("official action implementation hashes are absent")
        implementation = []
    observed_sources = set()
    for receipt in implementation:
        if not isinstance(receipt, dict):
            continue
        path = Path(str(receipt.get("path") or "")).resolve()
        observed_sources.add(str(path))
        if (
            not path.is_file()
            or receipt.get("sha256") != sha256(path)
            or receipt.get("size") != path.stat().st_size
        ):
            issues.append(f"official action implementation is stale: {path}")
    if observed_sources != expected_sources:
        issues.append("official action implementation closure is incomplete")

    outputs = (manifest or {}).get("outputs") or {}
    match_path = _verified_receipt_path(
        outputs.get("rollcall_measure_match_audit.ndjson"),
        parent=manifest_path.parent if manifest_path else None,
    )
    match_rows = _read_ndjson_objects(match_path)
    if len(match_rows) != accepted_count:
        issues.append("measure action-source audit does not cover accepted measures")
    supported_set = set(supported)
    for row in match_rows:
        measure_type = str(row.get("measure_type") or "").casefold()
        selected = row.get("official_action_source")
        if row.get("actions_fetched") is not True or row.get("error"):
            issues.append(
                f"official actions are incomplete for {row.get('canonical_key')}"
            )
        if measure_type not in supported_set and selected == "billstatus":
            issues.append(
                f"amendment incorrectly used BILLSTATUS: {row.get('canonical_key')}"
            )
    stage_current = bool(
        state
        and state.get("schema") == PRESYNC.STAGE_STATE_SCHEMA
        and state.get("stage") == "official_rollcalls"
        and manifest_path
    )
    if not stage_current:
        issues.append("official roll-call stage state is absent or stale")
    passed = not issues
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "issues": sorted(set(issues)),
        "stage_state": file_receipt(ROLLCALL_STAGE_STATE),
        "manifest": file_receipt(manifest_path) if manifest_path else None,
        "policy": policy,
        "measure_action_source_audit": file_receipt(match_path),
        "congress_api_key_structurally_required": False,
        "accepted_metadata_latest_action_alone_sufficient": False,
        "required_action_authority": (
            "complete exact-identity BILLSTATUS for bills/resolutions; complete "
            "official Congress.gov actions for accepted amendments"
        ),
    }


def workbook_recompute_state() -> tuple[bool, dict[str, Any] | None]:
    """Validate both generation-bound workbook receipts from staged acceptance."""

    staged = read_json(STAGED_ACCEPTANCE)
    if not (
        staged
        and staged.get("schema")
        == "legislation-shadow-recompute-acceptance-v1"
        and staged.get("status") == "PASS"
        and staged.get("acceptance_passed") is True
        and staged.get("idempotence_passed") is True
        and staged.get("production_promotion_performed") is False
    ):
        return False, {
            "status": "ABSENT_OR_UNACCEPTED",
            "reason": "staged acceptance receipt is absent or incomplete",
        }
    audit_path = _verified_receipt_path(staged.get("audit"))
    audit = read_json(audit_path) if audit_path else None
    if not audit or audit.get("status") != "PASS":
        return False, {
            "status": "INVALID",
            "reason": "staged recompute audit is absent or hash-invalid",
        }

    policy_hash = (
        sha256(ROLE_POLICY_RECEIPT)
        if ROLE_POLICY_RECEIPT.is_file()
        else None
    )
    run_results: list[dict[str, Any]] = []
    for label in ("run_1_manifest", "run_2_manifest"):
        run_path = _verified_receipt_path(audit.get(label))
        run = read_json(run_path) if run_path else None
        issues: list[str] = []
        if not run or run.get("schema") != "legislation-shadow-isolated-run-v1":
            issues.append("run manifest absent, hash-invalid, or wrong schema")
            run_results.append(
                {"run": label, "passed": False, "issues": issues}
            )
            continue
        generation_id = str(run.get("generation_id") or "").casefold()
        outputs = run.get("outputs") or {}
        receipt_entry = outputs.get(WORKBOOK_RECOMPUTE_RECEIPT_NAME)
        receipt_path = _verified_receipt_path(
            receipt_entry, parent=run_path.parent
        )
        value = read_json(receipt_path) if receipt_path else None
        workbook_entry = outputs.get(WORKBOOK_NAME)
        workbook_path = _verified_receipt_path(
            workbook_entry, parent=run_path.parent
        )
        verification = (value or {}).get("artifact_tool_verification") or {}
        output_workbook = (value or {}).get("output_workbook") or {}
        required = {
            "schema": "legislation-workbook-recompute-receipt-v1",
            "status": "PASS",
            "approved_engine": "@oai/artifact-tool",
            "staged_only": True,
            "production_written": False,
            "all_18_categories_recomputed": True,
            "legislative_efficiency_definition_consistent": True,
            "workbook_role_weights_verified": True,
            "unexplained_cell_count": 0,
        }
        if not value:
            issues.append("workbook recompute receipt absent or hash-invalid")
        else:
            issues.extend(
                f"{key}={value.get(key)!r}"
                for key, expected in required.items()
                if value.get(key) != expected
            )
            if str(value.get("generation_id") or "").casefold() != generation_id:
                issues.append("receipt generation_id does not match run")
            if not value.get("selected_role_weight_policy"):
                issues.append("selected role-weight policy is absent")
            if (
                value.get("selected_method_profile")
                != ROLE_METHOD_PROFILE_ID
                or value.get("consumer_role_methods")
                != ROLE_METHOD_PROFILE
            ):
                issues.append(
                    "consumer-specific role method profile is absent/stale"
                )
            if not policy_hash or str(
                value.get("role_policy_approval_sha256") or ""
            ).casefold() != policy_hash:
                issues.append("role-policy approval hash is absent or stale")
            if (
                not workbook_path
                or str(output_workbook.get("sha256") or "").casefold()
                != str((workbook_entry or {}).get("byte_sha256") or "").casefold()
            ):
                issues.append(
                    "receipt output workbook is not bound to run output"
                )
            if verification.get("passed") is not True:
                issues.append("artifact-tool verification did not pass")
            if verification.get("required_sheets_present") is not True:
                issues.append("required workbook sheets were not verified")
            if verification.get("formula_error_count") != 0:
                issues.append("formula errors remain in staged workbook")
            if verification.get("non_numeric_critical_cells") != 0:
                issues.append(
                    "non-numeric legislation-dependent cells remain"
                )
        run_results.append(
            {
                "run": label,
                "passed": not issues,
                "issues": issues,
                "manifest": file_receipt(run_path),
                "receipt": file_receipt(receipt_path),
                "workbook": file_receipt(workbook_path),
                "details": value,
            }
        )
    passed = len(run_results) == 2 and all(
        result["passed"] for result in run_results
    )
    return passed, {
        "status": "PASS" if passed else "FAIL",
        "staged_acceptance": file_receipt(STAGED_ACCEPTANCE),
        "runs": run_results,
    }


def centrality_rank_contract() -> dict[str, Any]:
    rank_source = ROOT / "fec" / "engine" / "compute_pillars.py"
    wrapper = ROOT / "fec" / "legislation" / "run_legislation_pillars_and_ranks.py"
    live_tokens = (
        "infp=E[m]/emax",
        "influence = workbook committee score only",
    )
    wrapper_tokens = (
        "base/AUG centrality does not modify rank",
        "stale_snapshot_fallback_used",
        "snapshot_matches_workbook_cache",
    )
    live_present = all(source_contains(rank_source, token) for token in live_tokens)
    wrapper_present = all(source_contains(wrapper, token) for token in wrapper_tokens)
    return {
        "status": "PRESERVED" if live_present and wrapper_present else "DRIFT",
        "passed": live_present and wrapper_present,
        "base_centrality_formula": (
            "0.60 normalized weighted out-degree + 0.25 normalized "
            "betweenness + 0.15 normalized eigenvector; renormalized to 0-100"
        ),
        "augmented_network_centrality": "not computed",
        "rank_influence_pillar": "workbook committee score E",
        "base_or_augmented_centrality_effective_rank_weight": 0.0,
        "method_change_applied": False,
        "reason": "user requested the same computation method",
    }


def assigned_literal(path: Path, variable: str) -> Any:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == variable
            for target in targets
        ):
            continue
        try:
            return ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return None
    return None


def normalize_decay(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        normalized = {
            str(int(key)): float(weight) for key, weight in value.items()
        }
    except (TypeError, ValueError):
        return None
    return normalized


def decay_contract() -> dict[str, Any]:
    bill_rules = (ROOT / "fec" / "bill_rules.py").read_text(
        encoding="utf-8", errors="replace"
    )
    legacy_factor_present = "CONGRESS_DECAY_FACTOR = 0.8" in bill_rules
    staged_sources = {
        "accepted_generation": (
            ROOT / "fec" / "legislation" / "build_accepted_legislation_generation.py",
            "DECAY_WEIGHT",
        ),
        "base_network": (
            ROOT / "fec" / "legislation" / "build_legislation_base_network.py",
            "DECAY",
        ),
    }
    staged = {
        name: {
            "path": str(path),
            "observed": normalize_decay(assigned_literal(path, variable)),
        }
        for name, (path, variable) in staged_sources.items()
    }
    for item in staged.values():
        item["passed"] = item["observed"] == EXPECTED_DECAY
    staged_passed = all(item["passed"] for item in staged.values())
    audit_passed, audit_state = PRESYNC.validate_decay_propagation_audit(
        DECAY_PROPAGATION_AUDIT,
        workspace_root=ROOT,
    )
    return {
        "expected": EXPECTED_DECAY,
        "staged_producers": staged,
        "staged_passed": staged_passed,
        "propagation_audit": audit_state,
        "propagation_audit_passed": audit_passed,
        "live_production": {
            "legacy_factor_present": legacy_factor_present,
            "cutover_pending": legacy_factor_present,
            "changed_before_sync": not legacy_factor_present,
        },
        "pre_sync_passed": staged_passed and audit_passed,
    }


def artifact_inventory(root: Path) -> dict[str, Any]:
    archives = [Path(path) for path in glob.glob(str(root / "congress_bills_archive_*.xlsx"))]
    networks = [Path(path) for path in glob.glob(str(root / "congress_cosponsor_network_*.xlsx"))]
    bases = [path for path in networks if "AUG" not in path.name.upper()]
    archive_mtime = newest(archives)
    archive_lexical = sorted(archives)[-1] if archives else None
    base = newest(bases)
    engine_network = newest_augmented_or_base(networks)
    named = {
        "archive_leg": root / "archive_leg.json",
        "legislation_routing_audit": root / "legislation_routing_audit.json",
        "vote_category_lean": root / "vote_category_lean.json",
        "behavior_panel": root / "behavior_panel.json",
        "behavioral_dispositions": root / "behavioral_dispositions.json",
        "bipartisan": root / "bipartisan.json",
        "coop_v2_candidate": root / "coop_v2_candidate.json",
        "member_receptivity": root / "member_receptivity.json",
        "category_stance": root / "category_stance.json",
        "pillar_inputs_snapshot": root / "data" / "engine" / "pillar_inputs_snapshot.json",
        "ranks_python": root / "ranks_python.json",
        "say_do_gap": root / "say_do_gap.json",
        "workbook": root / "Moneyball Main Version - Blended.xlsx",
        "member_desk_bundle": root / "member_desk_bundle.json",
    }
    return {
        "archive_selected_by_mtime": file_receipt(archive_mtime),
        "archive_selected_lexically_by_build_archive_legislative": file_receipt(
            archive_lexical
        ),
        "archive_selection_methods_agree": (
            archive_mtime == archive_lexical and archive_mtime is not None
        ),
        "base_network_selected_by_mtime": file_receipt(base),
        "engine_network_selected_augmented_first": file_receipt(engine_network),
        "fresh_base_would_be_hidden_until_augmented_rebuild": bool(
            engine_network and "AUG" in engine_network.name.upper()
        ),
        "artifacts": {
            name: file_receipt(path) for name, path in named.items()
        },
    }


def production_baseline_state() -> dict[str, Any]:
    manifest = read_json(PRODUCTION_BASELINE)
    if not manifest:
        return {
            "manifest": None,
            "passed": False,
            "missing": ["baseline_manifest"],
            "changed": [],
            "checked_file_count": 0,
        }
    missing: list[str] = []
    changed: list[dict[str, Any]] = []
    checked_sources = 0
    checked_copies = 0
    for entry in manifest.get("files") or []:
        source = Path(str(entry.get("source") or ""))
        frozen_copy = Path(str(entry.get("copy") or ""))
        expected = str(entry.get("sha256") or "").lower()
        if not source.is_file():
            missing.append(str(source))
        else:
            checked_sources += 1
            observed = sha256(source)
            if observed != expected:
                changed.append(
                    {
                        "role": "production_source",
                        "path": str(source),
                        "expected_sha256": expected,
                        "observed_sha256": observed,
                    }
                )
        if not frozen_copy.is_file():
            missing.append(str(frozen_copy))
        else:
            checked_copies += 1
            observed = sha256(frozen_copy)
            if observed != expected:
                changed.append(
                    {
                        "role": "frozen_copy",
                        "path": str(frozen_copy),
                        "expected_sha256": expected,
                        "observed_sha256": observed,
                    }
                )
    expected_count = int(manifest.get("file_count") or 0)
    return {
        "manifest": file_receipt(PRODUCTION_BASELINE),
        "passed": (
            manifest.get("schema") == "legislation-production-baseline-v1"
            and expected_count > 0
            and checked_sources == expected_count
            and checked_copies == expected_count
            and not missing
            and not changed
        ),
        "missing": missing,
        "changed": changed,
        "checked_file_count": checked_sources + checked_copies,
        "checked_source_count": checked_sources,
        "checked_frozen_copy_count": checked_copies,
        "expected_file_count": expected_count,
    }


def shadow_stage_binding_state() -> dict[str, Any]:
    state = read_json(SHADOW_STAGE_STATE)
    required = {
        "role_method_profile_approval": ROLE_POLICY_RECEIPT,
        "computation_policy_approval": COMPUTATION_POLICY_RECEIPT,
        "decay_propagation_audit": DECAY_PROPAGATION_AUDIT,
        "query_specificity_audit": QUERY_SPECIFICITY_AUDIT,
        "audited_member_layout": AUDITED_LAYOUT,
        "numeric_plan": NUMERIC_PLAN,
        "consumer_plan": (
            ROOT
            / "audits"
            / "legfix5"
            / "shadow_recompute"
            / "consumer_plan.json"
        ),
        "production_baseline_manifest": PRODUCTION_BASELINE,
    }
    input_receipts = {
        Path(str(item.get("path") or "")).resolve(): item
        for item in (state or {}).get("inputs") or []
        if isinstance(item, dict) and item.get("path")
    }
    bindings: dict[str, Any] = {}
    for label, path in required.items():
        resolved = path.resolve()
        receipt_value = input_receipts.get(resolved)
        current = file_receipt(resolved)
        bindings[label] = {
            "path": str(resolved),
            "exists": resolved.is_file(),
            "current_receipt": current,
            "staged_input_receipt": receipt_value,
            "bound_to_current_shadow_stage": bool(
                current
                and receipt_value
                and current.get("sha256") == receipt_value.get("sha256")
                and current.get("size") == receipt_value.get("size")
            ),
        }
    deep_acceptance = bool(
        state
        and PRESYNC.shadow_acceptance_passed(state)
        and all(
            item["bound_to_current_shadow_stage"]
            for item in bindings.values()
        )
    )
    return {
        "state": file_receipt(SHADOW_STAGE_STATE),
        "manifest": (
            (state or {}).get("manifest")
            if isinstance((state or {}).get("manifest"), dict)
            else None
        ),
        "bindings": bindings,
        "all_required_bindings_current": all(
            item["bound_to_current_shadow_stage"]
            for item in bindings.values()
        ),
        "deep_acceptance_passed": deep_acceptance,
    }


def main() -> int:
    args = parse_args()
    discovery_run_dir = args.discovery_run.resolve()
    run_dir = args.run.resolve()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    registry_hash = sha256(REGISTRY)
    discovery_path, discovery = current_discovery_manifest(
        discovery_run_dir, REGISTRY
    )
    selected_ids = list((discovery or {}).get("selected_query_ids") or [])
    completed_queries = list((discovery or {}).get("queries") or [])
    discovery_complete = bool(
        discovery
        and discovery.get("status") == "complete"
        and len(selected_ids) == len(set(selected_ids)) == 62
        and len(completed_queries) == 62
        and all(query_exact(query) for query in completed_queries)
    )

    final_manifest_path = run_dir / "manifest.json"
    final_manifest = read_json(final_manifest_path)
    coverage_gate_audit: dict[str, Any] = {}
    coverage_gate_blockers: list[dict[str, Any]] = []
    if discovery_path is not None:
        coverage_gate_audit, coverage_gate_blockers = PRESYNC.coverage_gate(
            discovery_path,
            final_manifest_path if final_manifest else None,
            REGISTRY,
        )
    discovery_complete = bool(
        discovery_complete
        and coverage_gate_audit.get("strict_discovery_passed") is True
    )
    billstatus_snapshot = billstatus_snapshot_state(
        discovery_path,
        discovery_run=discovery_run_dir,
    )
    (
        query_specificity_passed,
        query_specificity_state,
    ) = PRESYNC.validate_query_specificity_audit(
        QUERY_SPECIFICITY_AUDIT,
        registry_path=REGISTRY,
        strict_manifest_path=discovery_path,
    )
    final_generation_complete = bool(
        final_manifest
        and coverage_gate_audit.get("final_merged_passed") is True
    )
    (
        acu_title_query_plan_passed,
        acu_title_query_plan_state,
    ) = acu_title_query_plan_readiness(coverage_gate_audit)

    review_path = run_dir / "candidate_membership_review" / "manifest.json"
    review = read_json(review_path)
    review_current = bool(
        review
        and final_generation_complete
        and final_manifest_path.exists()
        and review.get("registry_sha256") == registry_hash
        and review.get("discovery_manifest_sha256")
        == sha256(final_manifest_path)
    )
    membership_passed = bool(
        review_current
        and review.get("promotion_gate_passed")
        and review.get("unresolved_count") == 0
    )

    relationships_path = run_dir / "relationships" / "manifest.json"
    relationships = read_json(relationships_path)
    relationships_passed = bool(
        relationships and relationships.get("status") == "complete"
    )

    reconciliation_path = (
        run_dir / "reconciliation" / "reconciliation_report.json"
    )
    reconciliation = read_json(reconciliation_path)
    coverage_acceptance_passed = bool(
        reconciliation
        and (reconciliation.get("acceptance") or {}).get("passed")
    )

    rollcall_source = ROOT / "votes" / "cu_rollcall_votes_clean.csv"
    rollcall_event_ledger = (
        run_dir / "rollcalls" / "rollcall_event_ledger.ndjson"
    )
    rollcall_match_audit = (
        run_dir / "rollcalls" / "rollcall_measure_match_audit.ndjson"
    )
    rollcall_identity = official_rollcall_identity_state()
    rollcall_action_source = official_rollcall_source_state()
    shadow_binding = shadow_stage_binding_state()
    staged_recompute_passed = shadow_binding["deep_acceptance_passed"]
    baseline_state = production_baseline_state()
    consumer_isolation = read_json(CONSUMER_ISOLATION_AUDIT)
    consumer_isolation_passed = bool(
        consumer_isolation
        and consumer_isolation.get("status") == "PASS"
        and consumer_isolation.get("plan_emitted") is True
        and consumer_isolation.get("runnable_plan_present") is True
        and consumer_isolation.get("production_consumers_executed") is False
        and consumer_isolation.get("production_written") is False
    )
    decay_state = decay_contract()
    downstream_drift_observation = downstream_drift_observation_state()
    behavior_stance_semantic_observation = (
        behavior_stance_semantic_observation_state()
    )
    planning_dashboard_semantic_observation = (
        planning_dashboard_semantic_observation_state()
    )
    workbook_ooxml_semantic_observation = (
        workbook_ooxml_semantic_observation_state()
    )
    formula_cache_freshness_observation = (
        formula_cache_freshness_observation_state()
    )

    run_pipeline = ROOT / "fec" / "run_pipeline.py"
    weekly_refresh = ROOT / "fec" / "weekly_refresh.py"
    pipeline_contract = {
        step: {
            "in_run_pipeline": source_contains(run_pipeline, step),
            "in_weekly_refresh": source_contains(weekly_refresh, step),
        }
        for step in REQUIRED_PIPELINE_STEPS
    }
    producers = {
        name: {
            "path": str(path),
            "exists": path.exists(),
        }
        for name, path in REQUIRED_STAGED_PRODUCERS.items()
    }
    producer_gate = all(item["exists"] for item in producers.values())
    role_policy_passed, role_policy = approved_role_policy()
    spreadsheet_import_passed, spreadsheet_import = spreadsheet_import_state()
    (
        spreadsheet_model_passed,
        spreadsheet_model,
    ) = spreadsheet_model_verification_state()
    workbook_recompute_passed, workbook_recompute = workbook_recompute_state()
    centrality_contract = centrality_rank_contract()

    gates = {
        "strict_discovery_complete": discovery_complete,
        "final_merged_generation_complete": final_generation_complete,
        "acu_title_corroboration_query_plan_current_and_resolved": (
            acu_title_query_plan_passed
        ),
        "billstatus_bulk_enrichment_snapshot_current": billstatus_snapshot[
            "passed"
        ],
        "query_specificity_current_and_passed": (
            query_specificity_passed
        ),
        "production_baseline_still_frozen": baseline_state["passed"],
        "membership_review_current_and_resolved": membership_passed,
        "relationship_enrichment_complete": relationships_passed,
        "coverage_reconciliation_acceptance_passed": coverage_acceptance_passed,
        "aggressive_decay_staged_for_presync": decay_state["pre_sync_passed"],
        "decay_propagation_audit_current_and_passed": decay_state[
            "propagation_audit_passed"
        ],
        "accepted_corpus_and_downstream_producers_exist": producer_gate,
        "role_weight_policy_explicitly_approved": role_policy_passed,
        "approved_spreadsheet_engine_import_passed": (
            spreadsheet_import_passed
        ),
        "accepted_workbook_legislative_recompute_complete": (
            workbook_recompute_passed
        ),
        "accepted_workbook_live_computation_verified": (
            workbook_recompute_passed
        ),
        "legislative_efficiency_definition_consistent": (
            workbook_recompute_passed
        ),
        "centrality_rank_method_preserved": centrality_contract["passed"],
        "shadow_consumer_plan_structural_only": consumer_isolation_passed,
        "shadow_consumer_isolation_executed_and_passed": (
            staged_recompute_passed
        ),
        "shadow_current_policy_layout_numeric_bound": shadow_binding[
            "all_required_bindings_current"
        ],
        "official_rollcall_event_ledger_exists": rollcall_event_ledger.exists(),
        "rollcall_measure_match_audit_exists": rollcall_match_audit.exists(),
        "official_rollcall_member_identity_complete": rollcall_identity[
            "passed"
        ],
        "official_rollcall_action_source_complete": rollcall_action_source[
            "passed"
        ],
        "staged_recompute_acceptance_passed": staged_recompute_passed,
    }
    sync_allowed = all(gates.values())

    report = {
        "schema": "legislation-downstream-readiness-v4",
        "generated_at": None,
        "production_is_read_only": True,
        "sync_allowed": sync_allowed,
        "hard_freeze": not sync_allowed,
        "gates": gates,
        "coverage": {
            "strict_discovery_run": str(discovery_run_dir),
            "final_merged_run": str(run_dir),
            "registry": str(REGISTRY),
            "registry_sha256": registry_hash,
            "selected_discovery_manifest": (
                file_receipt(discovery_path) if discovery_path else None
            ),
            "status": (discovery or {}).get("status"),
            "selected_query_count": len(selected_ids),
            "completed_query_count": len(completed_queries),
            "completed_query_ids": [
                query.get("query_id") for query in completed_queries
            ],
            "all_completed_queries_exact": bool(completed_queries)
            and all(query_exact(query) for query in completed_queries),
            "final_merged_manifest": file_receipt(final_manifest_path),
            "final_merged_generation_complete": final_generation_complete,
            "strengthened_coverage_gate": coverage_gate_audit,
            "strengthened_coverage_blockers": coverage_gate_blockers,
            "acu_title_corroboration_query_plan": (
                acu_title_query_plan_state
            ),
            "billstatus_bulk_enrichment_snapshot": billstatus_snapshot,
            "query_specificity_audit": query_specificity_state,
            "membership_review_manifest": file_receipt(review_path),
            "membership_review_current_for_selected_manifest": review_current,
            "membership_counts": {
                "included": (review or {}).get("included_count"),
                "excluded_false_positive": (review or {}).get(
                    "false_positive_exclusion_count"
                ),
                "unresolved": (review or {}).get("unresolved_count"),
            },
            "relationships_manifest": file_receipt(relationships_path),
            "reconciliation_report": file_receipt(reconciliation_path),
        },
        "decay_contract": decay_state,
        "rollcalls": {
            "legacy_external_clean_csv": file_receipt(rollcall_source),
            "legacy_external_clean_csv_missing": not rollcall_source.exists(),
            "official_event_ledger": file_receipt(rollcall_event_ledger),
            "measure_match_audit": file_receipt(rollcall_match_audit),
            "staged_member_identity_resolution": rollcall_identity,
            "staged_official_action_source": rollcall_action_source,
            "aggregate_without_event_provenance_is_not_accepted": True,
        },
        "missing_or_staged_producers": producers,
        "pipeline_contract": pipeline_contract,
        "artifact_inventory": artifact_inventory(ROOT),
        "production_baseline_state": baseline_state,
        "post_weekly_refresh_downstream_drift": (
            downstream_drift_observation
        ),
        "post_weekly_refresh_behavior_stance_semantics": (
            behavior_stance_semantic_observation
        ),
        "post_weekly_refresh_planning_dashboard_semantics": (
            planning_dashboard_semantic_observation
        ),
        "post_weekly_refresh_workbook_ooxml_semantics": (
            workbook_ooxml_semantic_observation
        ),
        "formula_cache_freshness": formula_cache_freshness_observation,
        "shadow_consumer_isolation_audit": file_receipt(
            CONSUMER_ISOLATION_AUDIT
        ),
        "shadow_stage_binding": shadow_binding,
        "staged_acceptance_receipt": shadow_binding["manifest"],
        "required_role_interpretation": {
            "Sponsor,current": "sponsor",
            "Cosponsor,current,original_cosponsor=true": "original-cosponsor",
            "Cosponsor,current,original_cosponsor=false": "cosponsor",
            "Cosponsor,withdrawn": "retained-audit-only-zero-score",
        },
        "required_role_weights": {
            "status": (
                "APPROVED" if role_policy_passed
                else "UNRESOLVED_POLICY_CONFLICT"
            ),
            "archive_leg": "3/2/1",
            "workbook_grid": "approximately 4/2/1",
            "apply_leg_stance": "1/1/1",
            "say_do": "1/.5/.25",
            "base_network": (
                "original=1, later=.5, then divided by sqrt(current "
                "cosponsor count)"
            ),
            "approval_receipt": file_receipt(ROLE_POLICY_RECEIPT),
            "approval": role_policy,
        },
        "computation_acceptance": {
            "approved_spreadsheet_engine_import": {
                "passed": spreadsheet_import_passed,
                "receipt": file_receipt(SPREADSHEET_IMPORT_RECEIPT),
                "details": spreadsheet_import,
            },
            "current_workbook_live_computation": {
                "passed": spreadsheet_model_passed,
                "receipt": file_receipt(
                    SPREADSHEET_MODEL_VERIFICATION_RECEIPT
                ),
                "details": spreadsheet_model,
                "required_repair": (
                    "materialize and verify legislation-dependent sublayer "
                    "and downstream values in the isolated accepted-generation "
                    "workbook"
                    if not spreadsheet_model_passed
                    else None
                ),
                "promotion_gate": False,
                "diagnostic_only": True,
            },
            "accepted_workbook_recompute": {
                "passed": workbook_recompute_passed,
                "details": workbook_recompute,
                "promotion_gate": True,
            },
            "centrality_and_rank": centrality_contract,
            "rank_snapshot_policy": {
                "stale_snapshot_fallback_allowed": False,
                "required_wrapper": file_receipt(
                    ROOT / "fec" / "legislation" / "run_legislation_pillars_and_ranks.py"
                ),
                "requires_complete_formula_cache": True,
                "requires_snapshot_cache_value_parity": True,
                "requires_generation_hash_binding": True,
            },
            "rollcall_primary_formula": (
                "sum(vote_value * explicit_direction_sign) / n over unique "
                "directional official roll-call votes"
            ),
            "rollcall_congress_decay": (
                "reported separately as a diagnostic; not substituted into "
                "the legacy-compatible primary lean without policy approval"
            ),
            "behavior_identity": (
                "must be unique by Congress/type/number/category/member "
                "Bioguide with strongest active role only"
            ),
            "say_do_two_pass_required": True,
        },
        "known_missing_bridges": [
            "accepted review ledger -> production archive/workbook role and raw sheets",
            "approved spreadsheet engine -> 18-category raw/normalized/sublayer workbook recomputation",
            "explicit role-weight policy -> all role-sensitive constructs",
            "fresh legislation generation -> complete two-pass downstream acceptance receipt",
        ],
    }
    report["generated_at"] = deterministic_generated_at(report)

    json_path = out_dir / "downstream_readiness.json"
    write_text_if_changed(
        json_path,
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    )
    markdown = [
        "# Legislation downstream readiness",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Sync allowed: **{'YES' if sync_allowed else 'NO'}**",
        f"- Strict queries completed: **{len(completed_queries)}/62**",
        f"- Strict discovery status: **{(discovery or {}).get('status', 'missing')}**",
        f"- Final merged generation: **{'complete' if final_generation_complete else 'not complete'}**",
        f"- Frozen production baseline: **{'unchanged' if baseline_state['passed'] else 'DRIFT/ABSENT'}**",
        f"- Review unresolved: **{(review or {}).get('unresolved_count', 'unknown')}**",
        f"- Relationship enrichment: **{'complete' if relationships_passed else 'not complete'}**",
        f"- Reconciliation acceptance: **{'PASS' if coverage_acceptance_passed else 'FAIL/ABSENT'}**",
        f"- Decay propagation audit: **{'PASS' if decay_state['propagation_audit_passed'] else 'FAIL/STALE/ABSENT'}**",
        (
            "- Post-weekly-refresh downstream drift evidence: "
            f"**{downstream_drift_observation['status']}** "
            "(informational only; no gate authority)"
        ),
        (
            "- Behavior/stance semantic drift evidence: "
            f"**{behavior_stance_semantic_observation['status']}** "
            "(informational only; no gate authority)"
        ),
        (
            "- Planning/dashboard semantic drift evidence: "
            f"**{planning_dashboard_semantic_observation['status']}** "
            "(informational only; causality/domain-correctness gaps retained)"
        ),
        (
            "- Workbook OOXML semantic drift evidence: "
            f"**{workbook_ooxml_semantic_observation['status']}** "
            "(informational only; formula/runtime/applicability blockers "
            "retained)"
        ),
        (
            "- Isolated formula-cache recomputation evidence: "
            f"**{formula_cache_freshness_observation['status']}** "
            "(informational only; live-cache, promotion, applicability, "
            "runtime, and causality blockers retained)"
        ),
        (
            "- Official roll-call action source: "
            f"**{'PASS' if rollcall_action_source['passed'] else 'FAIL/STALE/ABSENT'}** "
            "(BILLSTATUS no-key for bills/resolutions; amendments fail closed)"
        ),
        (
            "- Official roll-call member identity: "
            f"**{'PASS' if rollcall_identity['passed'] else 'FAIL/STALE/ABSENT'}**"
        ),
        f"- Isolated consumer steps: **{(consumer_isolation or {}).get('passing_step_count', 0)}/{(consumer_isolation or {}).get('required_step_count', 16)}**",
        f"- Role-weight policy: **{'approved' if role_policy_passed else 'unresolved'}**",
        f"- Approved spreadsheet import: **{'PASS' if spreadsheet_import_passed else 'FAIL/ABSENT'}**",
        f"- Current workbook live computation: **{'PASS' if spreadsheet_model_passed else 'BLOCKED'}**",
        f"- Accepted workbook recomputation: **{'PASS' if workbook_recompute_passed else 'FAIL/ABSENT'}**",
        (
            "- Centrality/rank treatment: **preserved** "
            "(base/AUG centrality effective rank weight remains 0; "
            "committee score remains the influence pillar)"
            if centrality_contract["passed"]
            else "- Centrality/rank treatment: **DRIFT**"
        ),
        "",
        "## Gates",
        "",
    ]
    markdown.extend(
        f"- [{'x' if passed else ' '}] `{name}`"
        for name, passed in gates.items()
    )
    markdown.extend(
        [
            "",
            "## Hard rule",
            "",
            "No production archive, workbook, network, roll-call aggregate, rank authority, "
            "dashboard, or mainframe publication may be replaced until every gate above is true.",
            "",
        ]
    )
    write_text_if_changed(
        out_dir / "DOWNSTREAM_READINESS.md",
        "\n".join(markdown),
    )
    print(
        "downstream readiness: sync_allowed=%s | strict=%d/62 | unresolved=%s"
        % (
            sync_allowed,
            len(completed_queries),
            (review or {}).get("unresolved_count"),
        )
    )
    print(json_path)
    return 0 if sync_allowed else 2


if __name__ == "__main__":
    raise SystemExit(main())
