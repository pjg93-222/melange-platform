"""Fail-closed orchestration for the legislation pre-sync audit.

This controller only launches the audit/staging producers named in
``STAGE_SPECS``.  It never launches a production pipeline, edits a human
review ledger, selects a role-weight policy, or interacts with a CAPTCHA.

Exit codes:
    0  every pre-sync gate, including the isolated shadow recompute, passed
    2  configuration/orchestration error
    3  deterministic checkpoint written; explicit blockers remain
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

_FEC_DIR = Path(__file__).resolve().parent.parent
if str(_FEC_DIR) not in sys.path:
    sys.path.insert(0, str(_FEC_DIR))
from audit.drift_closure import CHANGED_COUNT, TOTAL, UNCHANGED_COUNT
from legislation import build_legislation_shadow_consumer_plan as CONSUMER_PLAN
from legislation import assemble_approved_legislation_generation as APPROVED_GENERATION
from legislation import build_acu_bill_title_corroboration_query_plan as ACU_TITLE_PLAN
from legislation import run_legislation_shadow_recompute as SHADOW_RUNNER
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS_ENRICHMENT
import audit.audit_billstatus_snapshot_divergence as BILLSTATUS_DIVERGENCE
from audit.live_relocations import resolve_live_source
from legislation import refresh_billstatus_snapshot_evidence as BILLSTATUS_REFRESH
from legislation import run_billstatus_bulk_enrichment as BILLSTATUS_WORKER
import audit.audit_billstatus_enrichment_completeness as BILLSTATUS_COMPLETENESS
import audit.audit_postrefresh_behavior_stance_semantics as BEHAVIOR_STANCE_SEMANTICS
import audit.audit_postrefresh_planning_dashboard_semantics as PLANNING_DASHBOARD_SEMANTICS
import audit.audit_formula_cache_freshness as FORMULA_CACHE_FRESHNESS


SCHEMA = "legislation-presync-audit-config-v1"
CHECKPOINT_SCHEMA = "legislation-presync-audit-checkpoint-v1"
STAGE_STATE_SCHEMA = "legislation-presync-stage-state-v1"
EXIT_ERROR = 2
EXIT_BLOCKED = 3
EXPECTED_QUERY_COUNT = 62
EXPECTED_SHADOW_OUTPUT_COUNT = 31
PRIMARY_INSTRUMENT = "Congress.gov Advanced Search - Legislation"
QUERY_SPECIFICITY_SCHEMA = "legislation-query-specificity-audit-v1"
EXPECTED_QUERY_SPECIFICITY_COUNTS = {
    "category_queries": 19,
    "coverage_probes": 43,
    "required_queries": 62,
    "title_alias_probes": 42,
    "ambiguous_short_act_probes": 16,
    "specific_title_probes": 26,
    "ambiguous_aliases_in_category_queries": 0,
    "generic_bare_act_category_terms": 0,
}
DECAY_AUDIT_SCHEMA = "legislation-congress-decay-propagation-audit-v1"
EXPECTED_STAGED_DECAY = {
    "116": 0.16,
    "117": 0.33,
    "118": 0.66,
    "119": 1.0,
}
EXPECTED_FROZEN_DECAY = {
    "116": 0.512,
    "117": 0.64,
    "118": 0.8,
    "119": 1.0,
}
EXPECTED_DECAY_CONTRACT_COUNT = 9
EXPECTED_DECAY_CONSUMER_COUNT = 16
EXPECTED_DECAY_PRODUCTION_FILE_COUNT = TOTAL
# 2026-08-05, Phase 2+3 relocation + bootstrap-bump re-attestation: the
# production live-source partition is exactly 47 drifted / 8 matched of 55.
EXPECTED_DECAY_PRODUCTION_LIVE_DRIFT_COUNT = CHANGED_COUNT
EXPECTED_DECAY_PRODUCTION_LIVE_MATCH_COUNT = UNCHANGED_COUNT
EXPECTED_BILLSTATUS_GAP_OUTPUTS = {
    "candidate_visibility_gaps": "candidate_visibility_gaps.ndjson",
    "field_completeness_gaps": "field_completeness_gaps.ndjson",
    "role_provenance_gaps": "role_provenance_gaps.ndjson",
}
DOWNSTREAM_DRIFT_POINTER_SCHEMA = (
    "post-weekly-refresh-downstream-drift-pointer-v1"
)
DOWNSTREAM_DRIFT_MANIFEST_SCHEMA = (
    "post-weekly-refresh-downstream-drift-manifest-v1"
)
DOWNSTREAM_DRIFT_FILE_SCHEMA = (
    "post-weekly-refresh-downstream-file-drift-v1"
)
DOWNSTREAM_DRIFT_GAP_SCHEMA = "post-weekly-refresh-semantic-gap-v1"
# 2026-08-05: Downstream closure pins re-attested after the Phase 3 bootstrap
# source-byte changes grew the exact allowance list 36 -> 47.
EXPECTED_DOWNSTREAM_DRIFT_COUNTS = {
    "declared_file_count": TOTAL,
    "changed_file_count": CHANGED_COUNT,
    "unchanged_file_count": UNCHANGED_COUNT,
}
WORKBOOK_OOXML_POINTER_SCHEMA = (
    "post-weekly-refresh-workbook-ooxml-semantics-pointer-v1"
)
WORKBOOK_OOXML_MANIFEST_SCHEMA = (
    "post-weekly-refresh-workbook-ooxml-semantics-manifest-v1"
)
WORKBOOK_OOXML_OUTPUT_SCHEMAS = {
    "cell_differences.ndjson": (
        "post-weekly-refresh-workbook-cell-difference-v1"
    ),
    "defined_name_differences.ndjson": (
        "post-weekly-refresh-workbook-defined-name-difference-v1"
    ),
    "package_part_differences.ndjson": (
        "post-weekly-refresh-workbook-package-part-difference-v1"
    ),
    "relationship_differences.ndjson": (
        "post-weekly-refresh-workbook-relationship-difference-v1"
    ),
    "sheet_comparison.ndjson": (
        "post-weekly-refresh-workbook-sheet-comparison-v1"
    ),
    "style_differences.ndjson": (
        "post-weekly-refresh-workbook-style-difference-v1"
    ),
}
WORKBOOK_OOXML_UNRESOLVED_SURFACES = {
    "formula_evaluation_and_recalculation",
    "live_formula_cache_freshness",
    "rendered_visual_equivalence",
    "external_link_runtime_availability",
    "application_behavior_for_charts_pivots_and_macros",
    "causal_attribution_to_legislation",
}
PLANNING_DASHBOARD_POINTER_SCHEMA = (
    "post-weekly-refresh-planning-dashboard-semantics-pointer-v1"
)
PLANNING_DASHBOARD_MANIFEST_SCHEMA = (
    "post-weekly-refresh-planning-dashboard-semantics-manifest-v1"
)
PLANNING_DASHBOARD_OUTPUT_SPECS = {
    "artifact_semantics": (
        "artifact_semantics.ndjson",
        {
            "json": "post-weekly-refresh-planning-json-semantics-v1",
            "html": "post-weekly-refresh-planning-html-semantics-v1",
        },
    ),
    "unresolved_semantics": (
        "unresolved_semantics.ndjson",
        {"gap": "post-weekly-refresh-planning-semantic-gap-v1"},
    ),
}
FORMULA_CACHE_EXPECTED_FORMULA_COUNT = 98_269
FORMULA_CACHE_REQUIRED_UNRESOLVED_SURFACES = {
    "postsave_candidate_exact_semantics",
    "live_stored_formula_cache_freshness",
}

ROOT = Path(__file__).resolve().parent.parent.parent
AUDIT_ROOT = ROOT / "audits" / "legfix5"


class PresyncError(RuntimeError):
    """A configuration, safety, or orchestration contract failed."""


@dataclass(frozen=True)
class StageSpec:
    script: str
    manifest_names: tuple[str, ...] = ("manifest.json",)
    blocking_exit_codes: tuple[int, ...] = ()


STAGE_SPECS: dict[str, StageSpec] = {
    "membership_triage": StageSpec("legislation/build_legislation_membership_triage.py"),
    "review_batches": StageSpec("legislation/run_legislation_review_batches.py"),
    "accepted_generation": StageSpec("build_accepted_legislation_generation.py"),
    "direction_ledger": StageSpec(
        "legislation/build_legislation_direction_ledger.py", blocking_exit_codes=(2,)
    ),
    "policy_scenarios": StageSpec("legislation/compute_legislation_policy_scenarios.py"),
    "numeric_plan": StageSpec(
        "legislation/build_legislation_workbook_numeric_plan.py",
        manifest_names=("numeric_plan.json",),
        blocking_exit_codes=(2,),
    ),
    "approved_generation": StageSpec(
        "legislation/assemble_approved_legislation_generation.py",
    ),
    "base_network": StageSpec("legislation/build_legislation_base_network.py"),
    "official_rollcalls": StageSpec(
        "legislation/collect_official_rollcall_events.py", blocking_exit_codes=(2,)
    ),
    "consumer_plan": StageSpec(
        "legislation/build_legislation_shadow_consumer_plan.py",
        manifest_names=("consumer_plan.json", "consumer_isolation_audit.json"),
        blocking_exit_codes=(2,),
    ),
    "shadow_recompute": StageSpec(
        "legislation/run_legislation_shadow_recompute.py",
        manifest_names=(
            "staged_acceptance_receipt.json",
            "shadow_recompute_audit.json",
        ),
        blocking_exit_codes=(2,),
    ),
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def _total_observation(validator):
    """Make an informational observation validator total (never raises).

    A malformed evidence row must not abort the pre-sync audit: the
    observation fails closed as STALE_OR_INVALID and never touches a gate.
    """

    @functools.wraps(validator)
    def wrapped(*args: Any, **kwargs: Any) -> tuple[bool, dict[str, Any]]:
        try:
            return validator(*args, **kwargs)
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            PresyncError,
        ) as exc:
            return False, {
                "status": "STALE_OR_INVALID",
                "evidence_current": False,
                "informational_only": True,
                "affects_gate_decisions": False,
                "can_satisfy_blocked_gate": False,
                "authoritative_for_legislation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "production_or_mainframe_written": False,
                "sync_or_promotion_performed": False,
                "issues": [
                    f"observation validator failed closed: "
                    f"{type(exc).__name__}: {exc}"
                ],
            }

    return wrapped


@_total_observation
def validate_post_weekly_refresh_downstream_drift(
    pointer_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Validate the current drift receipt as informational evidence only.

    The receipt describes an external weekly-refresh observation.  It cannot
    satisfy a pre-sync gate, authorize a write, or establish accepted
    legislation recomputation.
    """
    issues: list[str] = []
    state: dict[str, Any] = {
        "status": "MISSING",
        "evidence_current": False,
        "informational_only": True,
        "affects_gate_decisions": False,
        "can_satisfy_blocked_gate": False,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
        "pointer": (
            file_receipt(pointer_path)
            if pointer_path is not None and pointer_path.is_file()
            else None
        ),
        "manifest": None,
        "outputs": {},
        "audit_id": None,
        "observational_closure": None,
        "changed_file_names": [],
        "changed_file_names_sha256": None,
        "issues": issues,
    }
    if pointer_path is None or not pointer_path.is_file():
        issues.append("post-weekly-refresh downstream-drift pointer is missing")
        return False, state

    def required_false(
        value: dict[str, Any],
        keys: Sequence[str],
        *,
        label: str,
    ) -> None:
        for key in keys:
            if value.get(key) is not False:
                issues.append(f"{label}.{key} must be False")

    def reject_authority_sync_or_write_flags(
        value: Any,
        *,
        label: str,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                is_safety_flag = bool(
                    "authoritative" in normalized
                    or "sync" in normalized
                    or "promotion" in normalized
                    or "written" in normalized
                    or normalized.endswith("_write")
                    or normalized
                    in {
                        "accepted_legislation_recomputation",
                        "legislation_recomputation_accepted",
                        "network_executed",
                        "production_commands_executed",
                    }
                )
                if is_safety_flag and child is not False:
                    issues.append(
                        f"{label}.{key} grants authority or records "
                        "sync/write execution"
                    )
                reject_authority_sync_or_write_flags(
                    child,
                    label=f"{label}.{key}",
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_authority_sync_or_write_flags(
                    child,
                    label=f"{label}[{index}]",
                )

    def receipt_path(
        receipt: Any,
        *,
        expected: Path,
        label: str,
    ) -> Path | None:
        if not isinstance(receipt, dict):
            issues.append(f"{label} receipt is missing or malformed")
            return None
        raw_path = receipt.get("path")
        observed = (
            Path(raw_path).resolve()
            if isinstance(raw_path, str) and raw_path
            else None
        )
        expected = expected.resolve()
        recorded_size = (
            receipt.get("bytes")
            if receipt.get("bytes") is not None
            else receipt.get("size")
        )
        if observed != expected:
            issues.append(f"{label} receipt path drifted")
            return None
        if not expected.is_file():
            issues.append(f"{label} receipt target is missing")
            return None
        if recorded_size != expected.stat().st_size:
            issues.append(f"{label} receipt size drifted")
        if str(receipt.get("sha256") or "").casefold() != sha256_file(
            expected
        ):
            issues.append(f"{label} receipt hash drifted")
        return expected

    def read_rows(path: Path, *, label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            issues.append(f"{label} output is unreadable: {error}")
            return rows
        if any(not line.strip() for line in lines):
            issues.append(f"{label} output contains blank rows")
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                issues.append(
                    f"{label} output row {index} is invalid JSON: {error}"
                )
                continue
            if not isinstance(row, dict):
                issues.append(f"{label} output row {index} is not an object")
                continue
            rows.append(row)
        return rows

    try:
        pointer = read_object(pointer_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"downstream-drift pointer is invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state

    if pointer.get("schema") != DOWNSTREAM_DRIFT_POINTER_SCHEMA:
        issues.append("downstream-drift pointer schema drifted")
    audit_id = pointer.get("audit_id")
    if not (
        isinstance(audit_id, str)
        and len(audit_id) == 64
        and all(character in "0123456789abcdef" for character in audit_id)
    ):
        issues.append("downstream-drift pointer audit_id is malformed")
        audit_id = None
    state["audit_id"] = audit_id
    required_false(
        pointer,
        (
            "authoritative_for_legislation",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "sync_or_promotion_performed",
        ),
        label="pointer",
    )
    reject_authority_sync_or_write_flags(pointer, label="pointer")

    expected_snapshot = (
        pointer_path.resolve().parent / "snapshots" / str(audit_id or "")
    ).resolve()
    raw_snapshot = pointer.get("snapshot_dir")
    observed_snapshot = (
        Path(raw_snapshot).resolve()
        if isinstance(raw_snapshot, str) and raw_snapshot
        else None
    )
    if observed_snapshot != expected_snapshot:
        issues.append("downstream-drift pointer snapshot_dir drifted")
    expected_manifest = expected_snapshot / "manifest.json"
    manifest_path = receipt_path(
        pointer.get("manifest"),
        expected=expected_manifest,
        label="downstream-drift manifest",
    )
    if manifest_path is None:
        state["status"] = "STALE_OR_INVALID"
        return False, state
    state["manifest"] = file_receipt(manifest_path)
    try:
        manifest = read_object(manifest_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"downstream-drift manifest is invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state

    if manifest.get("schema") != DOWNSTREAM_DRIFT_MANIFEST_SCHEMA:
        issues.append("downstream-drift manifest schema drifted")
    if manifest.get("audit_id") != audit_id:
        issues.append("downstream-drift manifest audit_id drifted")
    if manifest.get("status") != "PASS_OBSERVATIONAL_NONAUTHORITATIVE":
        issues.append("downstream-drift manifest is not observational PASS")
    required_false(
        manifest,
        (
            "legislation_recomputation_accepted",
            "authoritative_for_legislation",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "production_or_mainframe_written",
            "sync_or_promotion_performed",
            "network_executed",
            "workbook_cell_level_semantic_comparison_resolved",
        ),
        label="manifest",
    )
    reject_authority_sync_or_write_flags(manifest, label="manifest")
    if (
        manifest.get(
            "all_changed_consumers_potentially_legislation_bearing"
        )
        is not True
    ):
        issues.append(
            "manifest does not retain all changed consumers as potentially "
            "legislation-bearing"
        )

    outputs = manifest.get("outputs")
    outputs = outputs if isinstance(outputs, dict) else {}
    output_specs = {
        "file_drift": ("file_drift.ndjson", TOTAL),
        "semantic_gap_ledger": (
            "semantic_gap_ledger.ndjson",
            CHANGED_COUNT,
        ),
    }
    rows_by_output: dict[str, list[dict[str, Any]]] = {}
    for label, (filename, expected_count) in output_specs.items():
        path = receipt_path(
            outputs.get(label),
            expected=expected_snapshot / filename,
            label=f"downstream-drift {label}",
        )
        receipt = outputs.get(label)
        if isinstance(receipt, dict) and receipt.get(
            "record_count"
        ) != expected_count:
            issues.append(
                f"downstream-drift {label} receipt record_count drifted"
            )
        if path is None:
            continue
        state["outputs"][label] = file_receipt(path)
        rows_by_output[label] = read_rows(path, label=label)
        if len(rows_by_output[label]) != expected_count:
            issues.append(
                f"downstream-drift {label} output row count drifted"
            )

    file_rows = rows_by_output.get("file_drift", [])
    gap_rows = rows_by_output.get("semantic_gap_ledger", [])
    file_names = [
        row.get("name")
        for row in file_rows
        if isinstance(row.get("name"), str) and row.get("name")
    ]
    if len(file_names) != len(file_rows) or len(set(file_names)) != len(
        file_names
    ):
        issues.append("file-drift output names are missing or duplicated")
    changed_names: set[str] = set()
    changed_count = 0
    unchanged_count = 0
    for index, row in enumerate(file_rows, start=1):
        if row.get("schema") != DOWNSTREAM_DRIFT_FILE_SCHEMA:
            issues.append(f"file-drift row {index} schema drifted")
        changed = row.get("changed")
        if not isinstance(changed, bool):
            issues.append(f"file-drift row {index} changed flag is malformed")
        elif row.get("byte_identical") is not (not changed):
            issues.append(
                f"file-drift row {index} changed/byte identity drifted"
            )
        elif changed:
            changed_count += 1
            name = row.get("name")
            if isinstance(name, str):
                changed_names.add(name)
            if row.get("potentially_legislation_bearing") is not True:
                issues.append(
                    f"changed file-drift row {index} lost observational "
                    "visibility"
                )
            if (
                row.get("attribution")
                != "external_scheduled_weekly_refresh_observation"
            ):
                issues.append(
                    f"changed file-drift row {index} attribution drifted"
                )
        else:
            unchanged_count += 1
        required_false(
            row,
            (
                "accepted_legislation_recomputation",
                "authoritative_for_scoring",
                "authoritative_for_sync_or_promotion",
            ),
            label=f"file_drift[{index}]",
        )
        reject_authority_sync_or_write_flags(
            row,
            label=f"file_drift[{index}]",
        )

    gap_names: list[str] = []
    for index, row in enumerate(gap_rows, start=1):
        if row.get("schema") != DOWNSTREAM_DRIFT_GAP_SCHEMA:
            issues.append(f"semantic-gap row {index} schema drifted")
        name = row.get("name")
        if isinstance(name, str) and name:
            gap_names.append(name)
        else:
            issues.append(f"semantic-gap row {index} name is missing")
        if row.get("potentially_legislation_bearing") is not True:
            issues.append(
                f"semantic-gap row {index} lost observational visibility"
            )
        required_false(
            row,
            (
                "accepted_legislation_recomputation",
                "authoritative_for_sync_or_promotion",
            ),
            label=f"semantic_gap_ledger[{index}]",
        )
        reject_authority_sync_or_write_flags(
            row,
            label=f"semantic_gap_ledger[{index}]",
        )
    if len(set(gap_names)) != len(gap_names):
        issues.append("semantic-gap output names are duplicated")
    if set(gap_names) != changed_names:
        issues.append(
            "semantic-gap output does not close over the exact changed set"
        )

    summary = manifest.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    for key, expected in EXPECTED_DOWNSTREAM_DRIFT_COUNTS.items():
        if summary.get(key) != expected:
            issues.append(f"downstream-drift summary.{key} must be {expected}")
    if (
        summary.get("potentially_legislation_bearing_changed_count")
        != CHANGED_COUNT
    ):
        issues.append(
            "downstream-drift summary changed visibility count must be "
            f"{CHANGED_COUNT}"
        )
    if summary.get("accepted_legislation_recomputation_count") != 0:
        issues.append(
            "downstream-drift summary accepted recomputation count must be 0"
        )
    closure = {
        "declared_file_count": len(file_rows),
        "changed_file_count": changed_count,
        "unchanged_file_count": unchanged_count,
        "semantic_gap_count": len(gap_rows),
        "declared_equals_changed_plus_unchanged": (
            len(file_rows) == changed_count + unchanged_count
        ),
        "semantic_gaps_equal_changed_set": (
            set(gap_names) == changed_names
            and len(gap_names) == len(changed_names)
        ),
    }
    state["observational_closure"] = closure
    state["changed_file_names"] = sorted(changed_names)
    state["changed_file_names_sha256"] = sha256_bytes(
        canonical_bytes(sorted(changed_names))
    )
    if not (
        closure["declared_file_count"] == TOTAL
        and closure["changed_file_count"] == CHANGED_COUNT
        and closure["unchanged_file_count"] == UNCHANGED_COUNT
        and closure["semantic_gap_count"] == CHANGED_COUNT
        and closure["declared_equals_changed_plus_unchanged"]
        and closure["semantic_gaps_equal_changed_set"]
    ):
        issues.append(
            "downstream-drift observational closure must be exactly "
            f"{TOTAL} = {CHANGED_COUNT} changed + "
            f"{UNCHANGED_COUNT} unchanged"
        )

    state.update(
        {
            "status": (
                "CURRENT_INFORMATIONAL"
                if not issues
                else "STALE_OR_INVALID"
            ),
            "evidence_current": not issues,
            "summary": summary,
            "issues": issues,
        }
    )
    return not issues, state


@_total_observation
def validate_post_weekly_refresh_behavior_stance_semantics(
    pointer_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Expose current domain semantics without changing any gate decision."""
    state: dict[str, Any] = {
        "status": "MISSING",
        "evidence_current": False,
        "informational_only": True,
        "affects_gate_decisions": False,
        "can_satisfy_blocked_gate": False,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
        "pointer": (
            file_receipt(pointer_path)
            if pointer_path is not None and pointer_path.is_file()
            else None
        ),
        "issues": [],
    }
    if pointer_path is None or not pointer_path.is_file():
        state["issues"].append(
            "post-weekly-refresh behavior/stance semantic pointer is missing"
        )
        return False, state
    try:
        evidence = BEHAVIOR_STANCE_SEMANTICS.validate_evidence(pointer_path)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        BEHAVIOR_STANCE_SEMANTICS.AuditError,
    ) as error:
        state.update(
            {
                "status": "STALE_OR_INVALID",
                "issues": [str(error)],
            }
        )
        return False, state
    state.update(evidence)
    state.update(
        {
            "status": "CURRENT_INFORMATIONAL",
            "evidence_current": True,
            "informational_only": True,
            "affects_gate_decisions": False,
            "can_satisfy_blocked_gate": False,
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
            "issues": [],
        }
    )
    return True, state


@_total_observation
def validate_post_weekly_refresh_planning_dashboard_semantics(
    pointer_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Validate planning/dashboard evidence without granting gate authority.

    The current pointer, immutable manifest, both output ledgers, bound
    downstream-drift receipts, and all frozen/live target hashes must close.
    Domain correctness and accepted-legislation causation remain unresolved
    for every target by design.
    """
    issues: list[str] = []
    targets = set(PLANNING_DASHBOARD_SEMANTICS.TARGETS)
    json_targets = set(PLANNING_DASHBOARD_SEMANTICS.JSON_TARGETS)
    html_targets = set(PLANNING_DASHBOARD_SEMANTICS.HTML_TARGETS)
    state: dict[str, Any] = {
        "status": "MISSING",
        "evidence_current": False,
        "informational_only": True,
        "affects_gate_decisions": False,
        "can_satisfy_blocked_gate": False,
        "adds_pass_conditions": False,
        "adds_gates": False,
        "satisfies_blockers": False,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "accepted_legislation_recomputation": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
        "pointer": (
            file_receipt(pointer_path)
            if pointer_path is not None and pointer_path.is_file()
            else None
        ),
        "manifest": None,
        "audit_id": None,
        "outputs": {},
        "bound_receipts": {},
        "summary": None,
        "target_hash_closure": {
            "required_target_count": len(targets),
            "verified_target_count": 0,
            "all_frozen_hashes_current": False,
            "all_live_hashes_current": False,
        },
        "unresolved_closure": {
            "required_target_count": len(targets),
            "causality_unresolved_target_count": 0,
            "domain_correctness_unresolved_target_count": 0,
            "all_targets_retain_both": False,
        },
        "issues": issues,
    }
    if pointer_path is None or not pointer_path.is_file():
        issues.append(
            "post-weekly-refresh planning/dashboard semantic pointer is "
            "missing"
        )
        return False, state

    def receipt_path(
        receipt: Any,
        *,
        expected: Path | None,
        label: str,
    ) -> Path | None:
        if not isinstance(receipt, dict):
            issues.append(f"{label} receipt is missing or malformed")
            return None
        raw_path = receipt.get("path")
        observed = (
            Path(raw_path).resolve()
            if isinstance(raw_path, str) and raw_path
            else None
        )
        if observed is None:
            issues.append(f"{label} receipt path is missing")
            return None
        if expected is not None and observed != expected.resolve():
            issues.append(f"{label} receipt path drifted")
            return None
        if not observed.is_file():
            issues.append(f"{label} receipt target is missing")
            return None
        recorded_size = (
            receipt.get("bytes")
            if receipt.get("bytes") is not None
            else receipt.get("size")
        )
        if recorded_size != observed.stat().st_size:
            issues.append(f"{label} receipt size drifted")
        if str(receipt.get("sha256") or "").casefold() != sha256_file(
            observed
        ):
            issues.append(f"{label} receipt hash drifted")
        return observed

    def receipt_identity(receipt: Any) -> tuple[Any, Any, Any]:
        if not isinstance(receipt, dict):
            return None, None, None
        return (
            str(Path(str(receipt.get("path") or "")).resolve()),
            (
                receipt.get("bytes")
                if receipt.get("bytes") is not None
                else receipt.get("size")
            ),
            str(receipt.get("sha256") or "").casefold(),
        )

    def required_false(
        value: dict[str, Any],
        keys: Sequence[str],
        *,
        label: str,
    ) -> None:
        for key in keys:
            if value.get(key) is not False:
                issues.append(f"{label}.{key} must be False")

    def reject_authority_sync_or_write_flags(
        value: Any,
        *,
        label: str,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                safety_flag = bool(
                    "authoritative" in normalized
                    or "sync" in normalized
                    or "promotion" in normalized
                    or "written" in normalized
                    or normalized
                    in {
                        "accepted_legislation_recomputation",
                        "legislation_recomputation_accepted",
                        "network_executed",
                        "production_or_mainframe_written",
                        "sync_or_promotion_performed",
                    }
                )
                if safety_flag and child is not False:
                    issues.append(
                        f"{label}.{key} grants authority or records "
                        "sync/write execution"
                    )
                reject_authority_sync_or_write_flags(
                    child,
                    label=f"{label}.{key}",
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_authority_sync_or_write_flags(
                    child,
                    label=f"{label}[{index}]",
                )

    def read_rows(path: Path, *, label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            issues.append(f"{label} output is unreadable: {error}")
            return rows
        if any(not line.strip() for line in lines):
            issues.append(f"{label} output contains blank rows")
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                issues.append(
                    f"{label} row {index} is invalid JSON: {error}"
                )
                continue
            if not isinstance(row, dict):
                issues.append(f"{label} row {index} is not an object")
                continue
            rows.append(row)
        return rows

    try:
        pointer = read_object(pointer_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"planning/dashboard pointer is invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state
    if pointer.get("schema") != PLANNING_DASHBOARD_POINTER_SCHEMA:
        issues.append("planning/dashboard pointer schema drifted")
    audit_id = pointer.get("audit_id")
    if not (
        isinstance(audit_id, str)
        and len(audit_id) == 64
        and all(character in "0123456789abcdef" for character in audit_id)
    ):
        issues.append("planning/dashboard pointer audit_id is malformed")
        audit_id = None
    state["audit_id"] = audit_id
    required_false(
        pointer,
        (
            "authoritative_for_legislation",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "network_executed",
            "production_or_mainframe_written",
            "sync_or_promotion_performed",
        ),
        label="pointer",
    )
    reject_authority_sync_or_write_flags(pointer, label="pointer")

    expected_snapshot = (
        pointer_path.resolve().parent / "snapshots" / str(audit_id or "")
    ).resolve()
    raw_snapshot = pointer.get("snapshot_dir")
    observed_snapshot = (
        Path(raw_snapshot).resolve()
        if isinstance(raw_snapshot, str) and raw_snapshot
        else None
    )
    if observed_snapshot != expected_snapshot:
        issues.append("planning/dashboard pointer snapshot_dir drifted")
    producer_path = receipt_path(
        pointer.get("producer"),
        expected=(
            _FEC_DIR / "audit" / "audit_postrefresh_planning_dashboard_semantics.py"
        ),
        label="planning/dashboard producer",
    )
    manifest_path = receipt_path(
        pointer.get("manifest"),
        expected=expected_snapshot / "manifest.json",
        label="planning/dashboard manifest",
    )
    if manifest_path is None:
        state["status"] = "STALE_OR_INVALID"
        return False, state
    state["manifest"] = file_receipt(manifest_path)
    try:
        manifest = read_object(manifest_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"planning/dashboard manifest is invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state
    if manifest.get("schema") != PLANNING_DASHBOARD_MANIFEST_SCHEMA:
        issues.append("planning/dashboard manifest schema drifted")
    if manifest.get("audit_id") != audit_id:
        issues.append("planning/dashboard manifest audit_id drifted")
    if manifest.get("status") != "PASS_OBSERVATIONAL_NONAUTHORITATIVE":
        issues.append("planning/dashboard manifest is not observational PASS")
    required_false(
        manifest,
        (
            "accepted_legislation_recomputation",
            "authoritative_for_legislation",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "network_executed",
            "production_or_mainframe_written",
            "sync_or_promotion_performed",
        ),
        label="manifest",
    )
    reject_authority_sync_or_write_flags(manifest, label="manifest")
    if receipt_identity(manifest.get("producer")) != receipt_identity(
        pointer.get("producer")
    ):
        issues.append("planning/dashboard producer binding drifted")

    bound_receipts = manifest.get("bound_receipts")
    bound_receipts = (
        bound_receipts if isinstance(bound_receipts, dict) else {}
    )
    expected_bound_receipts = {
        "downstream_drift_pointer",
        "downstream_drift_manifest",
        "downstream_file_drift",
    }
    if set(bound_receipts) != expected_bound_receipts:
        issues.append("planning/dashboard bound receipt inventory drifted")
    drift_pointer_path = receipt_path(
        bound_receipts.get("downstream_drift_pointer"),
        expected=None,
        label="planning/dashboard downstream drift pointer",
    )
    if (
        drift_pointer_path is not None
        and (
            drift_pointer_path.name != "current.json"
            or drift_pointer_path.parent.name
            != "post_weekly_refresh_downstream_drift"
        )
    ):
        issues.append(
            "planning/dashboard bound drift receipt is not a current pointer"
        )
    drift_manifest_path = receipt_path(
        bound_receipts.get("downstream_drift_manifest"),
        expected=None,
        label="planning/dashboard downstream drift manifest",
    )
    drift_file_path = receipt_path(
        bound_receipts.get("downstream_file_drift"),
        expected=None,
        label="planning/dashboard downstream file drift",
    )
    for label, path in (
        ("downstream_drift_pointer", drift_pointer_path),
        ("downstream_drift_manifest", drift_manifest_path),
        ("downstream_file_drift", drift_file_path),
    ):
        if path is not None:
            state["bound_receipts"][label] = file_receipt(path)

    drift_pointer: dict[str, Any] = {}
    drift_manifest: dict[str, Any] = {}
    drift_target_rows: list[dict[str, Any]] = []
    if drift_pointer_path is not None:
        drift_current, drift_state = (
            validate_post_weekly_refresh_downstream_drift(
                drift_pointer_path
            )
        )
        if not drift_current:
            issues.append(
                "bound downstream drift receipt is not current: "
                + "; ".join(drift_state["issues"])
            )
        try:
            (
                drift_pointer,
                drift_manifest,
                drift_target_rows,
            ) = PLANNING_DASHBOARD_SEMANTICS.validate_drift_pointer(
                drift_pointer_path
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            PLANNING_DASHBOARD_SEMANTICS.AuditError,
        ) as error:
            issues.append(
                f"planning/dashboard target drift closure failed: {error}"
            )
    if drift_pointer:
        observed_manifest_path = Path(
            str((drift_pointer.get("manifest") or {}).get("path") or "")
        ).resolve()
        observed_file_path = Path(
            str(
                ((drift_manifest.get("outputs") or {}).get("file_drift") or {})
                .get("path")
                or ""
            )
        ).resolve()
        if observed_manifest_path != drift_manifest_path:
            issues.append("bound downstream manifest linkage drifted")
        if observed_file_path != drift_file_path:
            issues.append("bound downstream file ledger linkage drifted")
        if manifest.get("downstream_drift_audit_id") != drift_pointer.get(
            "audit_id"
        ):
            issues.append("planning/dashboard downstream audit_id drifted")

    outputs = manifest.get("outputs")
    outputs = outputs if isinstance(outputs, dict) else {}
    if set(outputs) != set(PLANNING_DASHBOARD_OUTPUT_SPECS):
        issues.append("planning/dashboard output ledger inventory drifted")
    rows_by_output: dict[str, list[dict[str, Any]]] = {}
    for label, (filename, _) in sorted(
        PLANNING_DASHBOARD_OUTPUT_SPECS.items()
    ):
        path = receipt_path(
            outputs.get(label),
            expected=expected_snapshot / filename,
            label=f"planning/dashboard output {label}",
        )
        if path is None:
            continue
        rows = read_rows(path, label=label)
        rows_by_output[label] = rows
        state["outputs"][label] = file_receipt(path)
        receipt = outputs.get(label)
        if (
            not isinstance(receipt, dict)
            or receipt.get("record_count") != len(rows)
        ):
            issues.append(f"{label} receipt record_count drifted")
        reject_authority_sync_or_write_flags(rows, label=label)

    semantic_rows = rows_by_output.get("artifact_semantics", [])
    unresolved_rows = rows_by_output.get("unresolved_semantics", [])
    semantic_by_name = {
        row.get("name"): row
        for row in semantic_rows
        if isinstance(row.get("name"), str)
    }
    unresolved_by_name = {
        row.get("name"): row
        for row in unresolved_rows
        if isinstance(row.get("name"), str)
    }
    if (
        len(semantic_by_name) != len(semantic_rows)
        or set(semantic_by_name) != targets
    ):
        issues.append(
            "planning/dashboard semantic ledger target closure drifted"
        )
    if (
        len(unresolved_by_name) != len(unresolved_rows)
        or set(unresolved_by_name) != targets
    ):
        issues.append(
            "planning/dashboard unresolved ledger target closure drifted"
        )

    drift_by_name = {
        row.get("name"): row
        for row in drift_target_rows
        if isinstance(row.get("name"), str)
    }
    verified_target_count = 0
    frozen_current_count = 0
    live_current_count = 0
    causality_unresolved_count = 0
    domain_unresolved_count = 0
    for name in sorted(targets):
        semantic = semantic_by_name.get(name)
        gap = unresolved_by_name.get(name)
        drift = drift_by_name.get(name)
        if not isinstance(semantic, dict):
            continue
        expected_type = "json" if name in json_targets else "html"
        expected_schema = (
            "post-weekly-refresh-planning-json-semantics-v1"
            if expected_type == "json"
            else "post-weekly-refresh-planning-html-semantics-v1"
        )
        if (
            semantic.get("schema") != expected_schema
            or semantic.get("artifact_type") != expected_type
        ):
            issues.append(f"{name} semantic row schema/type drifted")
        required_false(
            semantic,
            (
                "authoritative_for_legislation",
                "authoritative_for_scoring",
                "authoritative_for_sync_or_promotion",
                "production_or_mainframe_written",
                "sync_or_promotion_performed",
            ),
            label=f"artifact_semantics[{name}]",
        )
        target_receipts_match = isinstance(drift, dict)
        for side in ("frozen", "live"):
            semantic_receipt = semantic.get(side)
            observed_target = receipt_path(
                semantic_receipt,
                expected=(
                    Path(str(drift.get(f"{side}_path") or "")).resolve()
                    if isinstance(drift, dict)
                    else None
                ),
                label=f"{name} {side}",
            )
            expected_identity = (
                (
                    str(
                        Path(
                            str(drift.get(f"{side}_path") or "")
                        ).resolve()
                    ),
                    drift.get(f"{side}_bytes"),
                    str(
                        drift.get(f"{side}_sha256") or ""
                    ).casefold(),
                )
                if isinstance(drift, dict)
                else (None, None, None)
            )
            if receipt_identity(semantic_receipt) != expected_identity:
                issues.append(
                    f"{name} {side} semantic/drift receipt mismatch"
                )
                target_receipts_match = False
            current_identity = (
                (
                    str(observed_target),
                    observed_target.stat().st_size,
                    sha256_file(observed_target),
                )
                if observed_target is not None
                else (None, None, None)
            )
            side_current = (
                receipt_identity(semantic_receipt) == current_identity
                and receipt_identity(semantic_receipt) == expected_identity
            )
            if side_current:
                if side == "frozen":
                    frozen_current_count += 1
                else:
                    live_current_count += 1
            else:
                target_receipts_match = False
        if target_receipts_match:
            verified_target_count += 1
        if semantic.get("accepted_legislation_causation_resolved") is False:
            causality_unresolved_count += 1
        else:
            issues.append(f"{name} causality was incorrectly resolved")
        domain_value = (
            (semantic.get("domain_closure") or {}).get(
                "domain_meaning_or_policy_correctness_resolved"
            )
            if expected_type == "json"
            else (semantic.get("html_semantics") or {}).get(
                "domain_meaning_or_policy_correctness_resolved"
            )
        )
        if domain_value is False:
            domain_unresolved_count += 1
        else:
            issues.append(
                f"{name} domain meaning/policy correctness was "
                "incorrectly resolved"
            )
        if not isinstance(gap, dict):
            continue
        required_false(
            gap,
            (
                "authoritative_for_legislation",
                "authoritative_for_scoring",
                "authoritative_for_sync_or_promotion",
                "sync_or_promotion_performed",
            ),
            label=f"unresolved_semantics[{name}]",
        )
        if (
            gap.get("schema")
            != "post-weekly-refresh-planning-semantic-gap-v1"
            or gap.get("resolved") is not False
        ):
            issues.append(f"{name} unresolved row was resolved or drifted")
        unresolved = gap.get("unresolved")
        unresolved = unresolved if isinstance(unresolved, list) else []
        if "domain meaning and policy correctness" not in unresolved:
            issues.append(f"{name} domain correctness gap was dropped")
        if (
            "causation by accepted legislation recomputation"
            not in unresolved
        ):
            issues.append(f"{name} legislation causation gap was dropped")

    state["target_hash_closure"] = {
        "required_target_count": len(targets),
        "verified_target_count": verified_target_count,
        "all_frozen_hashes_current": frozen_current_count == len(targets),
        "all_live_hashes_current": live_current_count == len(targets),
    }
    state["unresolved_closure"] = {
        "required_target_count": len(targets),
        "causality_unresolved_target_count": causality_unresolved_count,
        "domain_correctness_unresolved_target_count": domain_unresolved_count,
        "all_targets_retain_both": (
            causality_unresolved_count == len(targets)
            and domain_unresolved_count == len(targets)
            and all(
                isinstance(unresolved_by_name.get(name), dict)
                and "domain meaning and policy correctness"
                in unresolved_by_name[name].get("unresolved", [])
                and "causation by accepted legislation recomputation"
                in unresolved_by_name[name].get("unresolved", [])
                for name in targets
            )
        ),
    }
    if verified_target_count != len(targets):
        issues.append(
            "planning/dashboard frozen/live target hash closure is "
            f"{verified_target_count}/{len(targets)}"
        )
    if not state["unresolved_closure"]["all_targets_retain_both"]:
        issues.append(
            "planning/dashboard causality/domain gaps were not retained "
            f"for all {len(targets)} targets"
        )

    summary = manifest.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    state["summary"] = summary
    expected_summary = {
        "target_count": len(targets),
        "json_target_count": len(json_targets),
        "html_target_count": len(html_targets),
        "unresolved_target_count": len(targets),
        "domain_meaning_or_policy_correctness_resolved_count": 0,
        "accepted_legislation_causation_resolved_count": 0,
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            issues.append(
                f"planning/dashboard summary.{key} must be {expected}"
            )
    html_rows = [
        row
        for row in semantic_rows
        if row.get("artifact_type") == "html"
    ]
    payloads = [
        payload
        for row in html_rows
        for payload in (row.get("html_semantics") or {}).get("payloads", [])
        if isinstance(payload, dict)
    ]
    summary_closure = {
        "html_payload_count": len(payloads),
        "html_payload_external_identity_proven_count": sum(
            1
            for payload in payloads
            if payload.get("frozen_payload_matches_external") is True
            and payload.get("live_payload_matches_external") is True
        ),
        "html_payload_stripped_shell_equal_count": sum(
            1
            for row in html_rows
            if (row.get("html_semantics") or {}).get(
                "payload_stripped_shell_equal"
            )
            is True
        ),
    }
    for key, expected in summary_closure.items():
        if summary.get(key) != expected:
            issues.append(
                f"planning/dashboard summary.{key} does not close over "
                "the semantic ledger"
            )

    if producer_path is not None and outputs:
        artifact_receipt = outputs.get("artifact_semantics") or {}
        unresolved_receipt = outputs.get("unresolved_semantics") or {}
        identity = {
            "schema": (
                "post-weekly-refresh-planning-dashboard-semantics-identity-v1"
            ),
            "downstream_drift_audit_id": manifest.get(
                "downstream_drift_audit_id"
            ),
            "bound_receipts": bound_receipts,
            "producer": manifest.get("producer"),
            "target_names": sorted(targets),
            "artifact_semantics_sha256": artifact_receipt.get("sha256"),
            "artifact_semantics_bytes": artifact_receipt.get("bytes"),
            "unresolved_semantics_sha256": unresolved_receipt.get("sha256"),
            "unresolved_semantics_bytes": unresolved_receipt.get("bytes"),
            "authority": {
                "authoritative_for_legislation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
                "sync_or_promotion_performed": False,
                "production_or_mainframe_written": False,
                "network_executed": False,
            },
        }
        expected_audit_id = sha256_bytes(
            PLANNING_DASHBOARD_SEMANTICS.canonical_bytes(identity)
        )
        if audit_id != expected_audit_id:
            issues.append("planning/dashboard content audit_id drifted")

    state.update(
        {
            "status": (
                "CURRENT_INFORMATIONAL"
                if not issues
                else "STALE_OR_INVALID"
            ),
            "evidence_current": not issues,
            "issues": issues,
        }
    )
    return not issues, state


@_total_observation
def validate_post_weekly_refresh_workbook_ooxml_semantics(
    pointer_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Validate workbook OOXML evidence without granting gate authority.

    This validates the current pointer, immutable manifest, every output
    ledger, and every bound input (including the frozen and live workbooks).
    It deliberately retains all runtime, freshness, applicability, render,
    and causal-attribution gaps exposed by the producer.
    """
    issues: list[str] = []
    state: dict[str, Any] = {
        "status": "MISSING",
        "evidence_current": False,
        "informational_only": True,
        "affects_gate_decisions": False,
        "can_satisfy_blocked_gate": False,
        "adds_pass_conditions": False,
        "adds_gates": False,
        "satisfies_blockers": False,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "accepted_legislation_recomputation": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
        "pointer": (
            file_receipt(pointer_path)
            if pointer_path is not None and pointer_path.is_file()
            else None
        ),
        "manifest": None,
        "audit_id": None,
        "outputs": {},
        "bound_inputs": {},
        "summary": None,
        "unresolved_surfaces": [],
        "retained_blockers": {
            "formula_cache_freshness": False,
            "formula_evaluation_and_recalculation": False,
            "live_model_applicability": False,
            "live_layout_applicability": False,
            "render_and_runtime": False,
            "causal_attribution": False,
        },
        "issues": issues,
    }
    if pointer_path is None or not pointer_path.is_file():
        issues.append(
            "post-weekly-refresh workbook OOXML semantic pointer is missing"
        )
        return False, state

    def receipt_path(
        receipt: Any,
        *,
        expected: Path | None,
        label: str,
    ) -> Path | None:
        if not isinstance(receipt, dict):
            issues.append(f"{label} receipt is missing or malformed")
            return None
        raw_path = receipt.get("path")
        observed = (
            Path(raw_path).resolve()
            if isinstance(raw_path, str) and raw_path
            else None
        )
        if observed is None:
            issues.append(f"{label} receipt path is missing")
            return None
        if expected is not None and observed != expected.resolve():
            issues.append(f"{label} receipt path drifted")
            return None
        if not observed.is_file():
            issues.append(f"{label} receipt target is missing")
            return None
        recorded_size = (
            receipt.get("bytes")
            if receipt.get("bytes") is not None
            else receipt.get("size")
        )
        if recorded_size != observed.stat().st_size:
            issues.append(f"{label} receipt size drifted")
        if str(receipt.get("sha256") or "").casefold() != sha256_file(
            observed
        ):
            issues.append(f"{label} receipt hash drifted")
        return observed

    def required_false(
        value: dict[str, Any],
        keys: Sequence[str],
        *,
        label: str,
    ) -> None:
        for key in keys:
            if value.get(key) is not False:
                issues.append(f"{label}.{key} must be False")

    def reject_authority_sync_or_write_flags(
        value: Any,
        *,
        label: str,
    ) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                safety_flag = bool(
                    "authoritative" in normalized
                    or "sync" in normalized
                    or "promotion" in normalized
                    or "written" in normalized
                    or normalized
                    in {
                        "accepted_legislation_recomputation",
                        "legislation_recomputation_accepted",
                        "production_or_mainframe_written",
                        "sync_or_promotion_performed",
                    }
                )
                if safety_flag and child is not False:
                    issues.append(
                        f"{label}.{key} grants authority or records "
                        "sync/write execution"
                    )
                reject_authority_sync_or_write_flags(
                    child,
                    label=f"{label}.{key}",
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_authority_sync_or_write_flags(
                    child,
                    label=f"{label}[{index}]",
                )

    def read_rows(path: Path, *, label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            issues.append(f"{label} output is unreadable: {error}")
            return rows
        if any(not line.strip() for line in lines):
            issues.append(f"{label} output contains blank rows")
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                issues.append(
                    f"{label} row {index} is invalid JSON: {error}"
                )
                continue
            if not isinstance(row, dict):
                issues.append(f"{label} row {index} is not an object")
                continue
            rows.append(row)
        return rows

    try:
        pointer = read_object(pointer_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"workbook OOXML pointer is invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state
    if pointer.get("schema") != WORKBOOK_OOXML_POINTER_SCHEMA:
        issues.append("workbook OOXML pointer schema drifted")
    audit_id = pointer.get("audit_id")
    if not (
        isinstance(audit_id, str)
        and len(audit_id) == 64
        and all(character in "0123456789abcdef" for character in audit_id)
    ):
        issues.append("workbook OOXML pointer audit_id is malformed")
        audit_id = None
    state["audit_id"] = audit_id
    if pointer.get("observational_only") is not True:
        issues.append("workbook OOXML pointer must be observational only")
    required_false(
        pointer,
        (
            "authoritative_for_legislation",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "accepted_legislation_recomputation",
            "production_or_mainframe_written",
            "sync_or_promotion_performed",
        ),
        label="pointer",
    )
    reject_authority_sync_or_write_flags(pointer, label="pointer")

    expected_snapshot = (
        pointer_path.resolve().parent / "snapshots" / str(audit_id or "")
    ).resolve()
    raw_snapshot = pointer.get("snapshot_dir")
    observed_snapshot = (
        Path(raw_snapshot).resolve()
        if isinstance(raw_snapshot, str) and raw_snapshot
        else None
    )
    if observed_snapshot != expected_snapshot:
        issues.append("workbook OOXML pointer snapshot_dir drifted")
    receipt_path(
        pointer.get("producer"),
        expected=None,
        label="workbook OOXML producer",
    )
    manifest_path = receipt_path(
        pointer.get("manifest"),
        expected=expected_snapshot / "manifest.json",
        label="workbook OOXML manifest",
    )
    if manifest_path is None:
        state["status"] = "STALE_OR_INVALID"
        return False, state
    state["manifest"] = file_receipt(manifest_path)
    try:
        manifest = read_object(manifest_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"workbook OOXML manifest is invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state
    if manifest.get("schema") != WORKBOOK_OOXML_MANIFEST_SCHEMA:
        issues.append("workbook OOXML manifest schema drifted")
    if manifest.get("audit_id") != audit_id:
        issues.append("workbook OOXML manifest audit_id drifted")
    if manifest.get("status") != "PASS_OBSERVATIONAL_NONAUTHORITATIVE":
        issues.append("workbook OOXML manifest is not observational PASS")
    if manifest.get("observational_only") is not True:
        issues.append("workbook OOXML manifest must be observational only")
    required_false(
        manifest,
        (
            "authoritative_for_legislation",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "accepted_legislation_recomputation",
            "production_or_mainframe_written",
            "sync_or_promotion_performed",
            "formula_results_recalculated",
            "visual_render_performed",
        ),
        label="manifest",
    )
    reject_authority_sync_or_write_flags(manifest, label="manifest")
    if manifest.get("parser_supported_semantic_gap_closed") is not True:
        issues.append("parser-supported workbook semantic gap is not closed")

    bound_inputs = manifest.get("bound_inputs")
    bound_inputs = bound_inputs if isinstance(bound_inputs, dict) else {}
    expected_bound_inputs = {
        "baseline_manifest",
        "frozen_workbook",
        "live_workbook",
        "audited_layout_receipt",
        "workbook_model_receipt",
        "rank_drift_pointer",
    }
    if set(bound_inputs) != expected_bound_inputs:
        issues.append("workbook OOXML bound input inventory drifted")
    bound_paths: dict[str, Path] = {}
    for label in sorted(expected_bound_inputs):
        path = receipt_path(
            bound_inputs.get(label),
            expected=None,
            label=f"workbook OOXML bound input {label}",
        )
        if path is not None:
            bound_paths[label] = path
            state["bound_inputs"][label] = file_receipt(path)
    if (
        bound_paths.get("frozen_workbook")
        == bound_paths.get("live_workbook")
    ):
        issues.append("frozen and live workbook bindings must be distinct")

    outputs = manifest.get("outputs")
    outputs = outputs if isinstance(outputs, dict) else {}
    if set(outputs) != set(WORKBOOK_OOXML_OUTPUT_SCHEMAS):
        issues.append("workbook OOXML output ledger inventory drifted")
    rows_by_output: dict[str, list[dict[str, Any]]] = {}
    for filename, expected_schema in sorted(
        WORKBOOK_OOXML_OUTPUT_SCHEMAS.items()
    ):
        path = receipt_path(
            outputs.get(filename),
            expected=expected_snapshot / filename,
            label=f"workbook OOXML output {filename}",
        )
        if path is None:
            continue
        rows = read_rows(path, label=filename)
        rows_by_output[filename] = rows
        state["outputs"][filename] = file_receipt(path)
        receipt = outputs.get(filename)
        if (
            not isinstance(receipt, dict)
            or receipt.get("record_count") != len(rows)
        ):
            issues.append(f"{filename} receipt record_count drifted")
        for index, row in enumerate(rows, start=1):
            if row.get("schema") != expected_schema:
                issues.append(f"{filename} row {index} schema drifted")
            reject_authority_sync_or_write_flags(
                row,
                label=f"{filename}[{index}]",
            )
        identities = [canonical_bytes(row) for row in rows]
        if len(identities) != len(set(identities)):
            issues.append(f"{filename} contains duplicate rows")

    summary = manifest.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    state["summary"] = summary
    closure_counts = {
        "sheet_comparison.ndjson": (
            "baseline_sheet_count",
            "live_sheet_count",
        ),
        "cell_differences.ndjson": ("changed_cell_count",),
        "package_part_differences.ndjson": (
            "changed_package_part_count",
        ),
        "style_differences.ndjson": (
            "style_definition_mismatch_count",
        ),
        "defined_name_differences.ndjson": (
            "defined_name_mismatch_count",
        ),
        "relationship_differences.ndjson": (
            "relationship_mismatch_count",
        ),
    }
    for filename, summary_keys in closure_counts.items():
        row_count = len(rows_by_output.get(filename, []))
        for summary_key in summary_keys:
            if summary.get(summary_key) != row_count:
                issues.append(
                    f"workbook OOXML summary.{summary_key} does not "
                    f"close over {filename}"
                )
    frozen_receipt = bound_inputs.get("frozen_workbook")
    live_receipt = bound_inputs.get("live_workbook")
    if isinstance(frozen_receipt, dict) and summary.get(
        "baseline_sha256"
    ) != frozen_receipt.get("sha256"):
        issues.append("workbook OOXML baseline summary hash drifted")
    if isinstance(live_receipt, dict) and summary.get(
        "live_sha256"
    ) != live_receipt.get("sha256"):
        issues.append("workbook OOXML live summary hash drifted")

    unresolved = manifest.get("unresolved")
    unresolved = unresolved if isinstance(unresolved, list) else []
    unresolved_surfaces = {
        row.get("surface")
        for row in unresolved
        if isinstance(row, dict) and isinstance(row.get("surface"), str)
    }
    state["unresolved_surfaces"] = sorted(unresolved_surfaces)
    missing_surfaces = (
        WORKBOOK_OOXML_UNRESOLVED_SURFACES - unresolved_surfaces
    )
    if missing_surfaces:
        issues.append(
            "workbook OOXML unresolved surfaces were dropped: "
            + ", ".join(sorted(missing_surfaces))
        )
    evidence = manifest.get("bound_receipt_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    layout_evidence = evidence.get("audited_layout")
    layout_evidence = (
        layout_evidence if isinstance(layout_evidence, dict) else {}
    )
    model_evidence = evidence.get("workbook_model")
    model_evidence = (
        model_evidence if isinstance(model_evidence, dict) else {}
    )
    rank_evidence = evidence.get("rank_drift")
    rank_evidence = (
        rank_evidence if isinstance(rank_evidence, dict) else {}
    )
    evidence_receipt_bindings = {
        "audited_layout": (
            layout_evidence.get("receipt"),
            bound_inputs.get("audited_layout_receipt"),
        ),
        "workbook_model": (
            model_evidence.get("receipt"),
            bound_inputs.get("workbook_model_receipt"),
        ),
        "rank_drift": (
            rank_evidence.get("pointer"),
            bound_inputs.get("rank_drift_pointer"),
        ),
    }
    for label, (observed, expected) in evidence_receipt_bindings.items():
        if canonical_bytes(observed) != canonical_bytes(expected):
            issues.append(
                f"workbook OOXML {label} evidence receipt binding drifted"
            )
    frozen_hash = (
        frozen_receipt.get("sha256")
        if isinstance(frozen_receipt, dict)
        else None
    )
    for label, receipt_evidence in (
        ("audited_layout", layout_evidence),
        ("workbook_model", model_evidence),
    ):
        if receipt_evidence.get("bound_workbook_sha256") != frozen_hash:
            issues.append(
                f"workbook OOXML {label} frozen workbook binding drifted"
            )
    retained_blockers = {
        "formula_cache_freshness": bool(
            summary.get("live_formula_cache_freshness_proven") is False
            and isinstance(summary.get("formula_cache_freshness_status"), str)
            and summary["formula_cache_freshness_status"].startswith(
                "UNRESOLVED_"
            )
            and "live_formula_cache_freshness" in unresolved_surfaces
        ),
        "formula_evaluation_and_recalculation": bool(
            manifest.get("formula_results_recalculated") is False
            and "formula_evaluation_and_recalculation"
            in unresolved_surfaces
        ),
        "live_model_applicability": bool(
            model_evidence.get("applies_to_live_workbook") is False
            and model_evidence.get("status") == "BLOCKED"
        ),
        "live_layout_applicability": bool(
            layout_evidence.get("applies_to_live_workbook") is False
        ),
        "render_and_runtime": bool(
            manifest.get("visual_render_performed") is False
            and {
                "rendered_visual_equivalence",
                "external_link_runtime_availability",
                "application_behavior_for_charts_pivots_and_macros",
            }.issubset(unresolved_surfaces)
        ),
        "causal_attribution": (
            "causal_attribution_to_legislation" in unresolved_surfaces
        ),
    }
    state["retained_blockers"] = retained_blockers
    for label, retained in retained_blockers.items():
        if not retained:
            issues.append(
                f"workbook OOXML {label} blocker was not retained"
            )

    state.update(
        {
            "status": (
                "CURRENT_INFORMATIONAL"
                if not issues
                else "STALE_OR_INVALID"
            ),
            "evidence_current": not issues,
            "issues": issues,
        }
    )
    return not issues, state


@_total_observation
def validate_formula_cache_freshness_observation(
    pointer_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Validate isolated formula-cache evidence as informational only.

    The receipt proves only that two independently calculated temporary
    copies produced the same error-free cache ledger for the bound source
    bytes.  It cannot prove freshness of the unchanged live workbook, make
    either calculated copy promotion eligible, establish accepted-legislation
    causality, satisfy a blocker, add a gate, or authorize a write.
    """
    issues: list[str] = []
    state: dict[str, Any] = {
        "status": "MISSING",
        "evidence_current": False,
        "informational_only": True,
        "affects_gate_decisions": False,
        "can_satisfy_blocked_gate": False,
        "adds_pass_conditions": False,
        "adds_gates": False,
        "satisfies_blockers": False,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "accepted_legislation_recomputation": False,
        "accepted_legislation_causality_proven": False,
        "live_stored_formula_cache_freshness_proven": False,
        "calculated_copy_promotion_eligible": False,
        "live_or_production_workbook_written": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
        "pointer": (
            file_receipt(pointer_path)
            if pointer_path is not None and pointer_path.is_file()
            else None
        ),
        "manifest": None,
        "audit_id": None,
        "source_workbook": None,
        "outputs": {},
        "formula_closure": None,
        "retained_blockers": {
            "live_formula_cache_freshness": False,
            "calculated_copy_promotion_eligibility": False,
            "live_model_applicability": False,
            "live_layout_applicability": False,
            "render_and_runtime": False,
            "accepted_legislation_causality": False,
        },
        "issues": issues,
    }
    if pointer_path is None or not pointer_path.is_file():
        issues.append("formula-cache freshness pointer is missing")
        return False, state

    def valid_sha256(value: Any) -> bool:
        return bool(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def receipt_path(
        receipt: Any,
        *,
        expected: Path | None,
        label: str,
    ) -> Path | None:
        if not isinstance(receipt, dict):
            issues.append(f"{label} receipt is missing or malformed")
            return None
        raw_path = receipt.get("path")
        observed = (
            Path(raw_path).resolve()
            if isinstance(raw_path, str) and raw_path
            else None
        )
        if observed is None:
            issues.append(f"{label} receipt path is missing")
            return None
        if expected is not None and observed != expected.resolve():
            issues.append(f"{label} receipt path drifted")
            return None
        if not observed.is_file():
            issues.append(f"{label} receipt target is missing")
            return None
        recorded_size = (
            receipt.get("bytes")
            if receipt.get("bytes") is not None
            else receipt.get("size")
        )
        if recorded_size != observed.stat().st_size:
            issues.append(f"{label} receipt size drifted")
        if str(receipt.get("sha256") or "").casefold() != sha256_file(
            observed
        ):
            issues.append(f"{label} receipt hash drifted")
        return observed

    def read_json_object(path: Path, *, label: str) -> dict[str, Any] | None:
        try:
            value = read_object(path)
        except (OSError, PresyncError, TypeError, ValueError) as error:
            issues.append(f"{label} is invalid: {error}")
            return None
        if not isinstance(value, dict):
            issues.append(f"{label} is not an object")
            return None
        return value

    def required_false(
        value: dict[str, Any],
        keys: Sequence[str],
        *,
        label: str,
    ) -> None:
        for key in keys:
            if value.get(key) is not False:
                issues.append(f"{label}.{key} must be False")

    def reject_authority_or_resolution_claims(
        value: Any,
        *,
        label: str,
    ) -> None:
        explicit_resolution_claims = {
            "accepted_legislation_recomputation",
            "accepted_legislation_causality_proven",
            "calculated_copy_promotion_eligible",
            "live_stored_formula_cache_freshness_proven",
            "live_formula_cache_freshness_proven",
            "live_model_applicability_proven",
            "live_layout_applicability_proven",
            "rendered_visual_equivalence_proven",
            "runtime_equivalence_proven",
        }
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                safety_flag = bool(
                    "authoritative" in normalized
                    or "sync" in normalized
                    or "promotion" in normalized
                    or "written" in normalized
                    or normalized in explicit_resolution_claims
                )
                if safety_flag and child is not False:
                    issues.append(
                        f"{label}.{key} grants authority, records a write, "
                        "or resolves a retained blocker"
                    )
                reject_authority_or_resolution_claims(
                    child,
                    label=f"{label}.{key}",
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_authority_or_resolution_claims(
                    child,
                    label=f"{label}[{index}]",
                )

    pointer = read_json_object(pointer_path, label="formula-cache pointer")
    if pointer is None:
        state["status"] = "STALE_OR_INVALID"
        return False, state
    if pointer.get("schema") != FORMULA_CACHE_FRESHNESS.POINTER_SCHEMA:
        issues.append("formula-cache pointer schema drifted")
    audit_id = pointer.get("audit_id")
    if not valid_sha256(audit_id):
        issues.append("formula-cache pointer audit_id is malformed")
        audit_id = None
    state["audit_id"] = audit_id
    if (
        pointer.get("status")
        != "PASS_TEMP_CACHE_RECOMPUTATION_LIVE_AND_PROMOTION_GATES_UNCHANGED"
    ):
        issues.append("formula-cache pointer status drifted")
    if pointer.get("temporary_recalculation_passed") is not True:
        issues.append("formula-cache pointer lost temporary-copy PASS")
    required_false(
        pointer,
        (
            "live_stored_formula_cache_freshness_proven",
            "authoritative_for_sync_or_promotion",
            "live_or_production_workbook_written",
            "sync_or_promotion_performed",
        ),
        label="pointer",
    )
    reject_authority_or_resolution_claims(pointer, label="pointer")

    expected_snapshot = (
        pointer_path.resolve().parent / "snapshots" / str(audit_id or "")
    )
    manifest_path = receipt_path(
        pointer.get("manifest"),
        expected=expected_snapshot / "manifest.json",
        label="formula-cache manifest",
    )
    if manifest_path is None:
        state["status"] = "STALE_OR_INVALID"
        return False, state
    state["manifest"] = file_receipt(manifest_path)
    manifest = read_json_object(
        manifest_path,
        label="formula-cache manifest",
    )
    if manifest is None:
        state["status"] = "STALE_OR_INVALID"
        return False, state
    if manifest.get("schema") != FORMULA_CACHE_FRESHNESS.SCHEMA:
        issues.append("formula-cache manifest schema drifted")
    if manifest.get("audit_id") != audit_id:
        issues.append("formula-cache manifest audit_id drifted")
    if manifest.get("status") != pointer.get("status"):
        issues.append("formula-cache pointer/manifest status drifted")
    if manifest.get("observational_only") is not True:
        issues.append("formula-cache manifest must be observational only")
    required_false(
        manifest,
        (
            "authoritative_for_live_stored_formula_caches",
            "authoritative_for_scoring",
            "authoritative_for_sync_or_promotion",
            "live_or_production_workbook_written",
            "sync_or_promotion_performed",
        ),
        label="manifest",
    )
    reject_authority_or_resolution_claims(manifest, label="manifest")

    source_receipt = manifest.get("source_workbook")
    source_path = receipt_path(
        source_receipt,
        expected=None,
        label="formula-cache live source workbook",
    )
    if source_path is not None:
        state["source_workbook"] = file_receipt(source_path)
    if canonical_bytes(pointer.get("source_workbook")) != canonical_bytes(
        source_receipt
    ):
        issues.append("formula-cache pointer/source binding drifted")
    source_inventory = manifest.get("source_formula_cache_inventory")
    source_inventory = (
        source_inventory if isinstance(source_inventory, dict) else {}
    )
    if canonical_bytes(source_inventory.get("workbook")) != canonical_bytes(
        source_receipt
    ):
        issues.append("formula-cache source inventory binding drifted")

    calculations = manifest.get("independent_calculations")
    calculations = calculations if isinstance(calculations, dict) else {}
    inventories = manifest.get("calculated_formula_cache_inventories")
    inventories = inventories if isinstance(inventories, dict) else {}
    if set(calculations) != {"a", "b"}:
        issues.append("formula-cache calculation ledger inventory drifted")
    if set(inventories) != {"a", "b"}:
        issues.append("formula-cache output inventory ledger drifted")

    source_sha256 = (
        source_receipt.get("sha256")
        if isinstance(source_receipt, dict)
        else None
    )
    source_bytes = (
        source_receipt.get("bytes")
        if isinstance(source_receipt, dict)
        else None
    )
    engine_receipt_hashes: dict[str, str] = {}
    calculated_hashes: dict[str, str] = {}
    temp_source_hashes: dict[str, Any] = {}
    receipt_files: dict[str, Path] = {}
    engine_kinds: dict[str, str] = {}
    cache_hashes: dict[str, Any] = {}
    definition_hashes: dict[str, Any] = {}
    inventory_rows: list[dict[str, Any]] = [source_inventory]
    for calculation_id in ("a", "b"):
        calculation = calculations.get(calculation_id)
        calculation = (
            calculation if isinstance(calculation, dict) else {}
        )
        inventory = inventories.get(calculation_id)
        inventory = inventory if isinstance(inventory, dict) else {}
        inventory_rows.append(inventory)
        workbook_receipt = calculation.get("workbook")
        inventory_workbook_receipt = inventory.get("workbook")
        if canonical_bytes(workbook_receipt) != canonical_bytes(
            inventory_workbook_receipt
        ):
            issues.append(
                f"formula-cache calculation {calculation_id} workbook "
                "inventory binding drifted"
            )
        workbook_path = receipt_path(
            workbook_receipt,
            expected=None,
            label=f"formula-cache calculated copy {calculation_id}",
        )
        receipt_file = receipt_path(
            calculation.get("receipt"),
            expected=None,
            label=f"formula-cache calculation receipt {calculation_id}",
        )
        if workbook_path is not None:
            state["outputs"][
                f"calculated_copy_{calculation_id}"
            ] = file_receipt(workbook_path)
            calculated_hashes[calculation_id] = sha256_file(workbook_path)
        if receipt_file is None:
            continue
        state["outputs"][
            f"calculation_receipt_{calculation_id}"
        ] = file_receipt(receipt_file)
        engine_receipt_hashes[calculation_id] = sha256_file(receipt_file)
        receipt_files[calculation_id] = receipt_file
        engine_receipt = read_json_object(
            receipt_file,
            label=f"formula-cache calculation receipt {calculation_id}",
        )
        if engine_receipt is None:
            continue
        reject_authority_or_resolution_claims(
            engine_receipt,
            label=f"calculation_receipt_{calculation_id}",
        )
        # D-174: a receipt of the LibreOffice lane (ops/compute/
        # office_recalc.py) is judged by the LibreOffice rules below; every
        # other receipt, Excel's or not, by the Excel rules exactly as before.
        libreoffice = (
            engine_receipt.get("schema")
            == FORMULA_CACHE_FRESHNESS.LIBREOFFICE_ENGINE_SCHEMA
        )
        engine_kinds[calculation_id] = (
            "libreoffice" if libreoffice else "excel"
        )
        if (
            engine_receipt.get("schema")
            not in {
                FORMULA_CACHE_FRESHNESS.ENGINE_SCHEMA,
                FORMULA_CACHE_FRESHNESS.LIBREOFFICE_ENGINE_SCHEMA,
            }
            or engine_receipt.get("status")
            != "PASS_TEMP_COPY_RECALCULATED"
        ):
            issues.append(
                f"formula-cache calculation receipt {calculation_id} "
                "schema/status drifted"
            )
        required_false(
            engine_receipt,
            (
                "live_or_production_workbook_opened",
                "live_or_production_workbook_written",
                "macro_execution_allowed",
                "external_link_updates_allowed",
                "external_connections_allowed",
                "sync_or_promotion_performed",
            ),
            label=f"calculation_receipt_{calculation_id}",
        )
        if libreoffice:
            required_false(
                engine_receipt,
                ("canonical_workbook_written",),
                label=f"calculation_receipt_{calculation_id}",
            )
            if not (
                engine_receipt.get("view_copy") is True
                and engine_receipt.get("input_unchanged") is True
            ):
                issues.append(
                    f"formula-cache calculation {calculation_id} is not an "
                    "untouched input plus a view copy"
                )
        before = engine_receipt.get("temporary_copy_before")
        before = before if isinstance(before, dict) else {}
        after = engine_receipt.get("temporary_copy_after")
        after = after if isinstance(after, dict) else {}
        temp_source_hashes[calculation_id] = before.get("sha256")
        if (
            engine_receipt.get("source_expected_sha256") != source_sha256
            or before.get("sha256") != source_sha256
            or before.get("bytes") != source_bytes
        ):
            issues.append(
                f"formula-cache calculation {calculation_id} does not bind "
                "the unchanged live source bytes"
            )
        expected_workbook_path = (
            workbook_path.resolve() if workbook_path is not None else None
        )
        before_path = (
            Path(before["path"]).resolve()
            if isinstance(before.get("path"), str) and before.get("path")
            else None
        )
        after_path = (
            Path(after["path"]).resolve()
            if isinstance(after.get("path"), str) and after.get("path")
            else None
        )
        # The container's paths are not the host's: a LibreOffice copy is
        # bound by hash and size alone.
        paths_bound = expected_workbook_path is not None and (
            libreoffice
            or (
                before_path == expected_workbook_path
                and after_path == expected_workbook_path
            )
        )
        if (
            not paths_bound
            or after.get("sha256")
            != (
                workbook_receipt.get("sha256")
                if isinstance(workbook_receipt, dict)
                else None
            )
            or after.get("bytes")
            != (
                workbook_receipt.get("bytes")
                if isinstance(workbook_receipt, dict)
                else None
            )
        ):
            issues.append(
                f"formula-cache calculation {calculation_id} output receipt "
                "binding drifted"
            )
        safety = engine_receipt.get("safety_preflight")
        safety = safety if isinstance(safety, dict) else {}
        if not (
            safety.get(
                "safe_for_isolated_calculation"
                if libreoffice
                else "safe_for_isolated_excel_calculation"
            )
            is True
            and safety.get("formula_count")
            == FORMULA_CACHE_EXPECTED_FORMULA_COUNT
            and not safety.get("forbidden_package_parts")
            and not safety.get("unsafe_external_relationships")
            and not safety.get("unsafe_formula_examples")
            and not safety.get("external_formula_reference_examples")
            and not safety.get("defined_name_runtime_risks")
            and (not libreoffice or safety.get("array_formula_count") == 0)
        ):
            issues.append(
                f"formula-cache calculation {calculation_id} safety "
                "preflight drifted"
            )
        engine = engine_receipt.get("engine")
        engine = engine if isinstance(engine, dict) else {}
        isolated_excel = (
            engine.get("automation_security")
            == "msoAutomationSecurityForceDisable"
            and engine.get("update_links") == 0
            and engine.get("enable_events") is False
            and engine.get("display_alerts") is False
            and engine.get("ask_to_update_links") is False
            and engine.get("visible") is False
            and engine.get("calculation_request")
            in {"CalculateFull", "CalculateFullRebuild"}
            and engine.get("calculation_state_after") == 0
            and engine.get("ready_after") is True
            and engine.get("calculate_before_save") is False
        )
        isolated_libreoffice = (
            engine.get("product") == "LibreOffice"
            and FORMULA_CACHE_FRESHNESS._libreoffice_at_least(
                engine.get("version"), (24, 8)
            )
            and engine.get("automation_security")
            == "MacroExecutionMode.NEVER_EXECUTE"
            and engine.get("update_links") == 0
            and engine.get("enable_events") is False
            and engine.get("display_alerts") is False
            and engine.get("ask_to_update_links") is False
            and engine.get("visible") is False
            and engine.get("read_only") is True
            and engine.get("calculation_request") == "calculateAll"
            and engine.get("calculation_completed") is True
            and engine.get("calculate_before_save") is False
            and engine.get("document_stored") is False
        )
        if not (isolated_libreoffice if libreoffice else isolated_excel):
            issues.append(
                f"formula-cache calculation {calculation_id} engine "
                "isolation/completion drifted"
            )
        if canonical_bytes(calculation.get("engine")) != canonical_bytes(
            engine
        ):
            issues.append(
                f"formula-cache calculation {calculation_id} engine ledger "
                "drifted from its receipt"
            )
        if canonical_bytes(
            calculation.get("safety_preflight")
        ) != canonical_bytes(safety):
            issues.append(
                f"formula-cache calculation {calculation_id} safety ledger "
                "drifted from its receipt"
            )

    kinds = set(engine_kinds.values())
    if len(kinds) > 1:
        issues.append("formula-cache calculations mix engine kinds")
    if kinds == {"libreoffice"}:
        # Deterministic LibreOffice runs on one input write byte-identical
        # copies, and that equality is the determinism proof; what must
        # differ is the evidence of two runs: two receipt files.
        if (
            len(calculated_hashes) != 2
            or calculated_hashes["a"] != calculated_hashes["b"]
        ):
            issues.append(
                "formula-cache LibreOffice calculated copies must be "
                "byte-identical"
            )
        if (
            len(receipt_files) != 2
            or receipt_files["a"] == receipt_files["b"]
        ):
            issues.append(
                "formula-cache LibreOffice calculation receipts must be "
                "two distinct files"
            )
    elif len(set(calculated_hashes.values())) != len(calculated_hashes):
        issues.append("formula-cache calculated copies must be distinct files")
    if source_sha256 in set(calculated_hashes.values()):
        issues.append("formula-cache calculation was not materialized")
    for label, inventory in (
        ("source", source_inventory),
        ("a", inventories.get("a") or {}),
        ("b", inventories.get("b") or {}),
    ):
        formula_count = inventory.get("formula_count")
        cache_present = inventory.get("cache_element_present_count")
        blank_count = inventory.get("blank_cache_count")
        nonblank_count = inventory.get("nonblank_cache_count")
        if not (
            formula_count == FORMULA_CACHE_EXPECTED_FORMULA_COUNT
            and cache_present == FORMULA_CACHE_EXPECTED_FORMULA_COUNT
            and inventory.get("cache_element_missing_count") == 0
            and isinstance(blank_count, int)
            and isinstance(nonblank_count, int)
            and blank_count + nonblank_count
            == FORMULA_CACHE_EXPECTED_FORMULA_COUNT
        ):
            issues.append(f"formula-cache {label} formula-count ledger drifted")
        if (
            inventory.get("formula_cached_error_count") != 0
            or inventory.get("formula_cached_errors_by_token") != {}
        ):
            issues.append(f"formula-cache {label} error ledger is not empty")
        cache_hashes[label] = inventory.get("formula_cache_sha256")
        definition_hashes[label] = inventory.get(
            "formula_definition_sha256"
        )
        if not valid_sha256(cache_hashes[label]) or not valid_sha256(
            definition_hashes[label]
        ):
            issues.append(f"formula-cache {label} digest is malformed")
    if not (
        cache_hashes.get("a") == cache_hashes.get("b")
        and cache_hashes.get("a") != cache_hashes.get("source")
    ):
        issues.append("formula-cache deterministic cache digest closure failed")
    if definition_hashes.get("a") != definition_hashes.get("b"):
        issues.append(
            "formula-cache independent definition digest closure failed"
        )
    if not all(
        (inventories.get(label) or {}).get("nonblank_cache_count", 0) > 0
        for label in ("a", "b")
    ):
        issues.append("formula-cache calculation was not materialized")

    comparisons = manifest.get("semantic_comparisons")
    comparisons = comparisons if isinstance(comparisons, dict) else {}
    if set(comparisons) != {"source_vs_a", "source_vs_b", "a_vs_b"}:
        issues.append("formula-cache semantic-comparison ledger drifted")
    for label in ("source_vs_a", "source_vs_b"):
        comparison = comparisons.get(label)
        comparison = comparison if isinstance(comparison, dict) else {}
        if (
            comparison.get("formula_cache_changed_cell_count")
            != FORMULA_CACHE_EXPECTED_FORMULA_COUNT
            or comparison.get("logical_nonformula_mismatch_count") != 0
        ):
            issues.append(f"formula-cache {label} semantic closure drifted")
    a_vs_b = comparisons.get("a_vs_b")
    a_vs_b = a_vs_b if isinstance(a_vs_b, dict) else {}
    if not (
        a_vs_b.get("formula_cache_changed_cell_count") == 0
        and a_vs_b.get("formula_mismatch_count") == 0
        and a_vs_b.get("logical_nonformula_mismatch_count") == 0
        and a_vs_b.get("unsafe_cell_change_count") == 0
        and a_vs_b.get("unsafe_sheet_change_count") == 0
        and a_vs_b.get("static_semantics_preserved") is True
    ):
        issues.append("formula-cache independent semantic closure drifted")

    gates = manifest.get("gates")
    gates = gates if isinstance(gates, dict) else {}
    for key in (
        "source_hash_unchanged",
        "calculation_on_verified_temp_copies_only",
        "macros_events_connections_and_link_updates_disabled",
        "formula_count_parity",
        "current_input_bytes_bound_before_each_calculation",
        "independent_postsave_formula_definitions_identical",
        "source_to_postsave_logical_nonformula_values_identical",
        "independent_postsave_static_semantics_identical",
        "independent_formula_caches_identical",
        "formula_calculation_materialized",
        "calculated_formula_cache_error_count_zero",
        "temporary_recalculation_passed",
        "live_workbook_write_required_to_materialize_caches",
    ):
        if gates.get(key) is not True:
            issues.append(f"formula-cache gates.{key} must be True")
    # Excel rewrites the whole package on save, so for its copies "exact
    # semantics preserved" is False by construction.  A LibreOffice view copy
    # is the original package plus cached values: the audit may truthfully
    # record True there.  Either way no copy is promotion eligible.
    libreoffice_run = kinds == {"libreoffice"}
    required_false(
        gates,
        (
            "postsave_candidate_promotion_eligible",
            "live_stored_formula_cache_freshness_proven",
        )
        + (
            ()
            if libreoffice_run
            else ("postsave_candidate_exact_semantics_preserved",)
        ),
        label="gates",
    )
    if libreoffice_run and not isinstance(
        gates.get("postsave_candidate_exact_semantics_preserved"), bool
    ):
        issues.append(
            "formula-cache gates.postsave_candidate_exact_semantics_preserved "
            "must be recorded"
        )

    unresolved = manifest.get("unresolved")
    unresolved = unresolved if isinstance(unresolved, list) else []
    unresolved_surfaces = {
        row.get("surface")
        for row in unresolved
        if isinstance(row, dict) and isinstance(row.get("surface"), str)
    }
    missing_unresolved = (
        FORMULA_CACHE_REQUIRED_UNRESOLVED_SURFACES - unresolved_surfaces
    )
    if missing_unresolved:
        issues.append(
            "formula-cache unresolved surfaces were dropped: "
            + ", ".join(sorted(missing_unresolved))
        )

    if (
        len(engine_receipt_hashes) == 2
        and len(calculated_hashes) == 2
        and valid_sha256(source_sha256)
        and valid_sha256(cache_hashes.get("a"))
    ):
        expected_audit_id = sha256_bytes(
            FORMULA_CACHE_FRESHNESS.canonical_bytes(
                {
                    "schema": FORMULA_CACHE_FRESHNESS.SCHEMA,
                    "source_sha256": source_sha256,
                    "calculated_a_sha256": calculated_hashes.get("a"),
                    "calculated_b_sha256": calculated_hashes.get("b"),
                    "cache_sha256": cache_hashes.get("a"),
                    "engine_a_receipt_sha256": engine_receipt_hashes.get("a"),
                    "engine_b_receipt_sha256": engine_receipt_hashes.get("b"),
                }
            )
        )
        if audit_id != expected_audit_id:
            issues.append("formula-cache content audit_id drifted")

    formula_closure = {
        "engine_kind": (
            next(iter(kinds)) if len(kinds) == 1
            else "mixed" if kinds else None
        ),
        "formula_count": FORMULA_CACHE_EXPECTED_FORMULA_COUNT,
        "source_sha256": source_sha256,
        "temp_copy_source_sha256": {
            key: temp_source_hashes.get(key) for key in ("a", "b")
        },
        "both_temp_copy_source_hashes_match": (
            temp_source_hashes.get("a")
            == temp_source_hashes.get("b")
            == source_sha256
        ),
        "calculated_cache_sha256": {
            key: cache_hashes.get(key) for key in ("a", "b")
        },
        "deterministic_cache_digests_match": (
            cache_hashes.get("a") == cache_hashes.get("b")
        ),
        "formula_error_count": sum(
            int(inventory.get("formula_cached_error_count") or 0)
            for inventory in inventory_rows
        ),
        "live_source_bytes_unchanged": (
            source_path is not None
            and isinstance(source_receipt, dict)
            and sha256_file(source_path) == source_sha256
            and source_path.stat().st_size == source_bytes
        ),
        "verified_output_receipt_count": len(state["outputs"]),
        "required_output_receipt_count": 4,
    }
    state["formula_closure"] = formula_closure
    if not formula_closure["both_temp_copy_source_hashes_match"]:
        issues.append("formula-cache temp-copy source hash closure failed")
    if formula_closure["formula_error_count"] != 0:
        issues.append("formula-cache formula errors were recorded")
    if not formula_closure["live_source_bytes_unchanged"]:
        issues.append("formula-cache live source workbook bytes drifted")
    if formula_closure["verified_output_receipt_count"] != 4:
        issues.append("formula-cache output receipt closure failed")

    retained_blockers = {
        "live_formula_cache_freshness": bool(
            pointer.get("live_stored_formula_cache_freshness_proven") is False
            and gates.get("live_stored_formula_cache_freshness_proven")
            is False
            and "live_stored_formula_cache_freshness"
            in unresolved_surfaces
        ),
        "calculated_copy_promotion_eligibility": bool(
            gates.get("postsave_candidate_promotion_eligible") is False
            and (
                libreoffice_run
                or gates.get("postsave_candidate_exact_semantics_preserved")
                is False
            )
            and "postsave_candidate_exact_semantics"
            in unresolved_surfaces
        ),
        "live_model_applicability": True,
        "live_layout_applicability": True,
        "render_and_runtime": True,
        "accepted_legislation_causality": True,
    }
    state["retained_blockers"] = retained_blockers
    for label, retained in retained_blockers.items():
        if not retained:
            issues.append(f"formula-cache {label} blocker was not retained")

    state.update(
        {
            "status": (
                "CURRENT_INFORMATIONAL"
                if not issues
                else "STALE_OR_INVALID"
            ),
            "evidence_current": not issues,
            "issues": issues,
        }
    )
    return not issues, state


def validate_billstatus_snapshot_plan(
    plan_path: Path | None,
    *,
    strict_manifest_path: Path | None,
    discovery_records_path: Path | None,
    exclusion_ledger_path: Path | None,
    exclusion_provenance_paths: Sequence[Path] = (),
) -> tuple[bool, dict[str, Any]]:
    """Validate non-authoritative BILLSTATUS current-snapshot evidence."""
    issues: list[str] = []
    state: dict[str, Any] = {
        "status": "MISSING",
        "snapshot_only": True,
        "authoritative_for_discovery": False,
        "authoritative_for_semantic_exclusion": False,
        "authoritative_for_sync_or_promotion": False,
        "network_executed": False,
        "quorum_used": False,
        "plan": file_receipt(plan_path) if plan_path and plan_path.is_file() else None,
        "strict_manifest": (
            file_receipt(strict_manifest_path)
            if strict_manifest_path and strict_manifest_path.is_file()
            else None
        ),
        "candidate_partition": None,
        "issues": issues,
    }
    required = {
        "plan": plan_path,
        "strict manifest": strict_manifest_path,
        "Advanced Search records": discovery_records_path,
        "exclusion ledger": exclusion_ledger_path,
    }
    for label, path in required.items():
        if path is None or not path.is_file():
            issues.append(f"BILLSTATUS snapshot {label} is missing")
    for path in exclusion_provenance_paths:
        if not path.is_file():
            issues.append(
                f"BILLSTATUS snapshot exclusion provenance is missing: {path}"
            )
    if issues:
        return False, state
    assert plan_path is not None
    assert strict_manifest_path is not None
    assert discovery_records_path is not None
    assert exclusion_ledger_path is not None
    try:
        plan = read_object(plan_path)
        BILLSTATUS_ENRICHMENT.validate_snapshot_plan(
            plan, expected_strict_manifest=strict_manifest_path
        )
    except (
        OSError,
        PresyncError,
        BILLSTATUS_ENRICHMENT.ContractError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        issues.append(f"BILLSTATUS snapshot is stale or invalid: {error}")
        state["status"] = "STALE_OR_INVALID"
        return False, state

    inputs = plan.get("inputs") or {}
    records = inputs.get("advanced_search_records") or {}
    exclusion = inputs.get("exclusion_ledger") or {}
    provenance = inputs.get("exclusion_provenance") or []
    try:
        records_bound = (
            Path(str(records.get("path") or "")).resolve()
            == discovery_records_path.resolve()
        )
        exclusion_bound = (
            Path(str(exclusion.get("path") or "")).resolve()
            == exclusion_ledger_path.resolve()
        )
        provenance_bound = {
            Path(str(item.get("path") or "")).resolve()
            for item in provenance
            if isinstance(item, dict)
        } == {path.resolve() for path in exclusion_provenance_paths}
    except (OSError, TypeError, ValueError):
        records_bound = exclusion_bound = provenance_bound = False
    if not records_bound:
        issues.append("BILLSTATUS snapshot is bound to different discovery records")
    if not exclusion_bound:
        issues.append("BILLSTATUS snapshot is bound to a different exclusion ledger")
    if not provenance_bound:
        issues.append("BILLSTATUS snapshot exclusion provenance set drifted")

    partition = plan.get("candidate_partition")
    if not isinstance(partition, dict):
        issues.append("BILLSTATUS snapshot candidate partition is missing")
    else:
        values = [
            partition.get("all"),
            partition.get("top_level_enrichable"),
            partition.get("nested_only_amendments"),
        ]
        if not all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for value in values
        ):
            issues.append("BILLSTATUS snapshot candidate partition is malformed")
        elif values[0] != values[1] + values[2]:
            issues.append("BILLSTATUS snapshot candidate partition is not closed")
    state.update(
        {
            "status": "CURRENT" if not issues else "STALE_OR_INVALID",
            "plan": file_receipt(plan_path),
            "plan_sha256": sha256_file(plan_path),
            "strict_manifest": file_receipt(strict_manifest_path),
            "candidate_partition": partition,
            "type_sitemap_count": len(plan.get("type_sitemaps") or []),
            "network_executed": plan.get("network_executed"),
            "quorum_used": plan.get("quorum_used"),
            "issues": issues,
        }
    )
    return not issues, state


def validate_billstatus_divergence_audit(
    audit_path: Path | None,
    *,
    strict_manifest_path: Path | None,
    discovery_records_path: Path | None,
    memberships_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Validate current cumulative-vs-selected-query divergence evidence."""
    issues: list[str] = []
    required = {
        "audit": audit_path,
        "strict manifest": strict_manifest_path,
        "discovery records": discovery_records_path,
        "memberships": memberships_path,
    }
    for label, path in required.items():
        if path is None or not path.is_file():
            issues.append(f"BILLSTATUS divergence {label} is missing")
    state: dict[str, Any] = {
        "status": "MISSING",
        "audit": (
            file_receipt(audit_path)
            if audit_path and audit_path.is_file()
            else None
        ),
        "issues": issues,
    }
    if issues:
        return False, state
    assert audit_path is not None
    assert strict_manifest_path is not None
    assert discovery_records_path is not None
    assert memberships_path is not None
    try:
        audit = read_object(audit_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"BILLSTATUS divergence audit is invalid: {error}")
        return False, {**state, "status": "STALE_OR_INVALID"}

    if not (
        audit.get("schema") == BILLSTATUS_DIVERGENCE.SCHEMA
        and audit.get("snapshot_only") is True
        and audit.get("quorum_used") is False
        and audit.get("network_executed") is False
        and audit.get("candidate_exclusions_performed") is False
        and audit.get("candidate_deletions_performed") is False
        and audit.get("sync_or_promotion_performed") is False
        and audit.get("authoritative_for_discovery") is False
        and audit.get("authoritative_for_semantic_exclusion") is False
    ):
        issues.append("BILLSTATUS divergence safety contract drifted")

    inputs = audit.get("inputs") or {}
    for label, path, receipt in (
        (
            "records",
            discovery_records_path,
            inputs.get("advanced_search_records"),
        ),
        (
            "strict manifest",
            strict_manifest_path,
            inputs.get("selected_strict_manifest"),
        ),
        ("memberships", memberships_path, inputs.get("memberships")),
    ):
        if not isinstance(receipt, dict) or not (
            Path(str(receipt.get("path") or "")).resolve() == path.resolve()
            and receipt.get("sha256") == sha256_file(path)
            and receipt.get("bytes") == path.stat().st_size
        ):
            issues.append(f"BILLSTATUS divergence {label} receipt drifted")

    partition = audit.get("partition") or {}
    values = [
        partition.get("all"),
        partition.get("proven_by_selected_strict_query_artifacts"),
        partition.get("unproven_by_selected_strict_query_artifacts"),
        partition.get("historical_aggregate_proven_and_retained"),
        partition.get("unresolved_without_historical_aggregate_receipt"),
    ]
    if not all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        for value in values
    ):
        issues.append("BILLSTATUS divergence partition is malformed")
    elif not (
        values[0] == values[1] + values[2]
        and values[2] == values[3] + values[4]
        and partition.get("closed") is True
    ):
        issues.append("BILLSTATUS divergence partition is not lossless")

    aggregate = inputs.get("aggregate_manifest") or {}
    if not (
        aggregate.get("union_receipt_matches_current_corpus") is True
        and aggregate.get("membership_receipt_matches_current_ledger") is True
        and aggregate.get("declared_records_sha256")
        == aggregate.get("computed_records_sha256")
        and aggregate.get("declared_memberships_sha256")
        == aggregate.get("computed_memberships_sha256")
    ):
        issues.append("historical aggregate corpus/membership proof is stale")

    outputs = audit.get("outputs") or {}
    for label, expected_count in (
        (
            "proven_candidates",
            partition.get("proven_by_selected_strict_query_artifacts"),
        ),
        (
            "unproven_candidates",
            partition.get("unproven_by_selected_strict_query_artifacts"),
        ),
    ):
        receipt = outputs.get(label)
        path = Path(str((receipt or {}).get("path") or ""))
        if not isinstance(receipt, dict) or not (
            path.is_file()
            and receipt.get("sha256") == sha256_file(path)
            and receipt.get("bytes") == path.stat().st_size
            and receipt.get("record_count") == expected_count
        ):
            issues.append(f"BILLSTATUS divergence {label} ledger drifted")

    state = {
        "status": "CURRENT" if not issues else "STALE_OR_INVALID",
        "audit": file_receipt(audit_path),
        "classification": audit.get("status"),
        "partition": partition,
        "selected_query_labels_absent_from_aggregate_membership_arrays": (
            audit.get(
                "selected_query_labels_absent_from_aggregate_membership_arrays"
            )
        ),
        "candidate_retention_required": True,
        "semantic_exclusion_authorized": False,
        "network_executed": False,
        "sync_or_promotion_performed": False,
        "issues": issues,
    }
    return not issues, state


def validate_query_specificity_audit(
    audit_path: Path | None,
    *,
    registry_path: Path | None,
    strict_manifest_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Require a current, hash-bound proof that Act aliases are non-dispositive."""
    issues: list[str] = []
    required = {
        "audit": audit_path,
        "registry": registry_path,
        "selected strict manifest": strict_manifest_path,
    }
    for label, path in required.items():
        if path is None or not path.is_file():
            issues.append(f"query specificity {label} is missing")
    state: dict[str, Any] = {
        "status": "MISSING",
        "audit": (
            file_receipt(audit_path)
            if audit_path and audit_path.is_file()
            else None
        ),
        "issues": issues,
    }
    if issues:
        return False, state
    assert audit_path is not None
    assert registry_path is not None
    assert strict_manifest_path is not None
    try:
        audit = read_object(audit_path)
    except (OSError, PresyncError, TypeError, ValueError) as error:
        issues.append(f"query specificity audit is invalid: {error}")
        return False, {**state, "status": "STALE_OR_INVALID"}

    if not (
        audit.get("schema") == QUERY_SPECIFICITY_SCHEMA
        and audit.get("status") == "PASS"
        and audit.get("primary_discovery") == PRIMARY_INSTRUMENT
        and audit.get("quorum_used") is False
        and audit.get("semantic_exclusions_performed") is False
        and audit.get("blockers") == []
    ):
        issues.append("query specificity safety contract drifted")

    inputs = audit.get("inputs")
    inputs = inputs if isinstance(inputs, dict) else {}
    for label, expected, receipt in (
        ("registry", registry_path, inputs.get("registry")),
        (
            "selected strict manifest",
            strict_manifest_path,
            inputs.get("selected_strict_manifest"),
        ),
    ):
        receipt = receipt if isinstance(receipt, dict) else {}
        raw_path = receipt.get("path")
        observed_path = (
            Path(str(raw_path)).resolve()
            if isinstance(raw_path, str) and raw_path
            else None
        )
        recorded_size = (
            receipt.get("bytes")
            if receipt.get("bytes") is not None
            else receipt.get("size")
        )
        if not (
            observed_path == expected.resolve()
            and recorded_size == expected.stat().st_size
            and str(receipt.get("sha256") or "").casefold()
            == sha256_file(expected)
        ):
            issues.append(f"query specificity {label} receipt drifted")

    counts = audit.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    for key, expected in EXPECTED_QUERY_SPECIFICITY_COUNTS.items():
        if counts.get(key) != expected:
            issues.append(
                f"query specificity counts.{key} must be {expected!r}"
            )

    ambiguous = audit.get("ambiguous_title_aliases")
    specific = audit.get("specific_title_aliases")
    ambiguous = ambiguous if isinstance(ambiguous, list) else []
    specific = specific if isinstance(specific, list) else []
    if len(ambiguous) != EXPECTED_QUERY_SPECIFICITY_COUNTS[
        "ambiguous_short_act_probes"
    ]:
        issues.append("ambiguous short-Act proof ledger is incomplete")
    if len(specific) != EXPECTED_QUERY_SPECIFICITY_COUNTS[
        "specific_title_probes"
    ]:
        issues.append("specific title proof ledger is incomplete")
    seen_probe_ids: set[str] = set()
    for classification, rows in (("ambiguous", ambiguous), ("specific", specific)):
        for row in rows:
            if not isinstance(row, dict):
                issues.append(f"{classification} title proof row is malformed")
                continue
            probe_id = row.get("probe_id")
            if not isinstance(probe_id, str) or not probe_id:
                issues.append(f"{classification} title proof lacks probe_id")
            elif probe_id in seen_probe_ids:
                issues.append(f"duplicate title proof probe_id: {probe_id}")
            else:
                seen_probe_ids.add(probe_id)
            common_ok = bool(
                row.get("membership_non_dispositive") is True
                and row.get("exact_isolated_probe") is True
                and isinstance(row.get("parent_category_id"), str)
                and row.get("parent_category_id")
                and isinstance(row.get("title_alias"), str)
                and row.get("title_alias")
            )
            classification_ok = (
                row.get("ambiguous_short_act_alias") is True
                and row.get("present_in_parent_scoring_query") is False
                if classification == "ambiguous"
                else row.get("ambiguous_short_act_alias") is False
                and row.get("present_in_parent_scoring_query") is True
            )
            if not (common_ok and classification_ok):
                issues.append(
                    f"{classification} title proof contract drifted: "
                    f"{probe_id!r}"
                )
    if len(seen_probe_ids) != EXPECTED_QUERY_SPECIFICITY_COUNTS[
        "title_alias_probes"
    ]:
        issues.append("title proof probe identity set is incomplete")

    passed = not issues
    return passed, {
        "status": "PASS" if passed else "STALE_OR_INVALID",
        "passed": passed,
        "audit": file_receipt(audit_path),
        "counts": counts,
        "ambiguous_title_aliases_are_non_dispositive": (
            passed and len(ambiguous) == 16
        ),
        "generic_bare_act_scoring_terms": counts.get(
            "generic_bare_act_category_terms"
        ),
        "issues": issues,
    }


def billstatus_plan_semantic_identity(
    plan_path: Path,
    *,
    require_live_query_artifacts: bool = True,
) -> dict[str, Any]:
    """Recompute the exact query/candidate identity of one immutable plan."""

    resolved_plan = plan_path.resolve()
    if not resolved_plan.is_file():
        raise PresyncError(f"BILLSTATUS plan is missing: {resolved_plan}")
    plan = read_object(resolved_plan)
    snapshot = plan.get("snapshot_contract")
    if not isinstance(snapshot, dict):
        raise PresyncError("BILLSTATUS plan snapshot contract is missing")
    strict_receipt = snapshot.get("strict_manifest")
    if not isinstance(strict_receipt, dict):
        raise PresyncError("BILLSTATUS plan strict receipt is missing")
    strict_path = Path(str(strict_receipt.get("path") or "")).resolve()
    BILLSTATUS_ENRICHMENT.validate_fetch_plan(plan)
    if not (
        strict_path.is_file()
        and Path(str(strict_receipt.get("path") or "")).resolve()
        == strict_path
        and strict_receipt.get("bytes") == strict_path.stat().st_size
        and strict_receipt.get("sha256") == sha256_file(strict_path)
        and snapshot.get("snapshot_only") is True
        and snapshot.get("authoritative_for_discovery") is False
        and snapshot.get("authoritative_for_semantic_exclusion") is False
        and snapshot.get("authoritative_for_sync_or_promotion") is False
    ):
        raise PresyncError("BILLSTATUS plan/strict safety receipt drifted")
    if require_live_query_artifacts:
        BILLSTATUS_ENRICHMENT.validate_snapshot_plan(
            plan, expected_strict_manifest=strict_path
        )
    strict = read_object(strict_path)
    union = snapshot.get("strict_manifest_query_union")
    if not isinstance(union, dict):
        raise PresyncError("BILLSTATUS strict query union is missing")
    query_artifacts = union.get("query_artifacts")
    if not isinstance(query_artifacts, list):
        raise PresyncError("BILLSTATUS query artifact identity is missing")
    query_rows: list[dict[str, Any]] = []
    for item in query_artifacts:
        if not isinstance(item, dict):
            raise PresyncError("BILLSTATUS query artifact is malformed")
        query_id = str(item.get("query_id") or "")
        records_sha256 = str(item.get("records_sha256") or "").lower()
        record_count = item.get("record_count")
        if not (
            query_id
            and BILLSTATUS_ENRICHMENT.SHA256_RE.fullmatch(records_sha256)
            and isinstance(record_count, int)
            and not isinstance(record_count, bool)
            and record_count >= 0
        ):
            raise PresyncError(
                f"BILLSTATUS query logical receipt is invalid: {query_id}"
            )
        query_rows.append(
            {
                "query_id": query_id,
                "records_sha256": records_sha256,
                "record_count": record_count,
            }
        )
    query_rows.sort(key=lambda row: row["query_id"])
    manifest_query_rows = sorted(
        [
            {
                "query_id": str(item.get("query_id") or ""),
                "records_sha256": str(
                    item.get("records_sha256") or ""
                ).lower(),
                "record_count": item.get("result_total"),
            }
            for item in (strict.get("queries") or [])
            if isinstance(item, dict)
        ],
        key=lambda row: row["query_id"],
    )
    if query_rows != manifest_query_rows:
        raise PresyncError(
            "BILLSTATUS embedded query identity differs from strict manifest"
        )
    selected_query_ids = sorted(
        str(value) for value in (strict.get("selected_query_ids") or [])
    )
    if (
        len(selected_query_ids) != len(set(selected_query_ids))
        or not set(row["query_id"] for row in query_rows).issubset(
            selected_query_ids
        )
        or len(query_rows)
        != len(set(row["query_id"] for row in query_rows))
    ):
        raise PresyncError(
            "BILLSTATUS selected/query logical identity is not closed"
        )

    candidate_payload = {
        "selected_strict_candidate_count": union.get(
            "selected_strict_candidate_count"
        ),
        "cumulative_record_count": union.get("record_count"),
        "canonical_keys_sha256": union.get("canonical_keys_sha256"),
        "aggregate_rows_sha256": union.get("aggregate_rows_sha256"),
        "advanced_search_line_receipts_sha256": union.get(
            "advanced_search_line_receipts_sha256"
        ),
        "candidate_partition": plan.get("candidate_partition"),
        "record_tasks_sha256": BILLSTATUS_ENRICHMENT.stable_json_sha256(
            plan.get("record_tasks")
        ),
        "unsupported_candidates_sha256": (
            BILLSTATUS_ENRICHMENT.stable_json_sha256(
                plan.get("unsupported_candidates")
            )
        ),
    }
    for key in (
        "canonical_keys_sha256",
        "aggregate_rows_sha256",
        "advanced_search_line_receipts_sha256",
        "record_tasks_sha256",
        "unsupported_candidates_sha256",
    ):
        if not BILLSTATUS_ENRICHMENT.SHA256_RE.fullmatch(
            str(candidate_payload.get(key) or "").lower()
        ):
            raise PresyncError(
                f"BILLSTATUS candidate logical hash is invalid: {key}"
            )
    cumulative = union.get("cumulative_aggregate")
    cumulative_identity = None
    if cumulative is not None:
        if not isinstance(cumulative, dict):
            raise PresyncError(
                "BILLSTATUS cumulative aggregate identity is malformed"
            )
        cumulative_identity = {
            key: cumulative.get(key)
            for key in (
                "record_count",
                "records_sha256",
                "membership_count",
                "memberships_sha256",
            )
        }
        for key in ("records_sha256", "memberships_sha256"):
            if not BILLSTATUS_ENRICHMENT.SHA256_RE.fullmatch(
                str(cumulative_identity.get(key) or "").lower()
            ):
                raise PresyncError(
                    "BILLSTATUS cumulative logical hash is invalid: "
                    f"{key}"
                )
    payload = {
        "schema": "billstatus-plan-semantic-identity-v1",
        "primary_discovery": plan.get("primary_discovery"),
        "registry_sha256": strict.get("registry_sha256"),
        "completed_query_count": union.get("completed_query_count"),
        "selected_query_ids": selected_query_ids,
        "query_logical_receipts": query_rows,
        "query_logical_sha256": (
            BILLSTATUS_ENRICHMENT.stable_json_sha256(query_rows)
        ),
        "candidate_identity": candidate_payload,
        "candidate_logical_sha256": (
            BILLSTATUS_ENRICHMENT.stable_json_sha256(candidate_payload)
        ),
        "cumulative_identity": cumulative_identity,
    }
    payload["semantic_sha256"] = (
        BILLSTATUS_ENRICHMENT.stable_json_sha256(payload)
    )
    return payload


def build_billstatus_semantic_rollover_binding(
    *,
    current_plan_path: Path,
    reused_plan_path: Path,
) -> dict[str, Any]:
    """Bind receipt-distinct plans only when every logical input is equal."""

    current = billstatus_plan_semantic_identity(current_plan_path)
    reused = billstatus_plan_semantic_identity(
        reused_plan_path,
        require_live_query_artifacts=(
            current_plan_path.resolve() == reused_plan_path.resolve()
        ),
    )
    if current != reused:
        raise PresyncError(
            "BILLSTATUS plans are not semantically equivalent"
        )
    current_plan = read_object(current_plan_path)
    reused_plan = read_object(reused_plan_path)
    current_strict = (
        current_plan.get("snapshot_contract") or {}
    ).get("strict_manifest")
    reused_strict = (
        reused_plan.get("snapshot_contract") or {}
    ).get("strict_manifest")
    if not (
        isinstance(current_strict, dict)
        and isinstance(reused_strict, dict)
    ):
        raise PresyncError("BILLSTATUS rollover strict receipts are missing")
    return {
        "schema": "billstatus-semantic-rollover-binding-v1",
        "status": "PASS",
        "mode": (
            "EXACT_PLAN"
            if current_plan_path.resolve() == reused_plan_path.resolve()
            else "SEMANTIC_EQUIVALENCE"
        ),
        "current_plan": file_receipt(current_plan_path),
        "reused_plan": file_receipt(reused_plan_path),
        "current_strict_manifest": current_strict,
        "reused_strict_manifest": reused_strict,
        "semantic_identity": current,
        "query_logical_hashes_equal": True,
        "candidate_logical_hashes_equal": True,
        "physical_receipts_equal": (
            current_strict == reused_strict
        ),
        "network_reuse_only": True,
        "authoritative_for_discovery": False,
        "authoritative_for_semantic_exclusion": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "network_executed": False,
        "sync_or_promotion_performed": False,
    }


def billstatus_enrichment_evidence_paths(
    *,
    snapshot_plan_path: Path | None,
    fetch_root: Path | None,
    completeness_root: Path | None,
) -> tuple[Path | None, Path | None]:
    """Derive exact or uniquely semantic-equivalent offline evidence paths."""
    if not (
        snapshot_plan_path
        and snapshot_plan_path.is_file()
        and fetch_root
        and fetch_root.is_dir()
        and completeness_root
        and completeness_root.is_dir()
    ):
        return None, None
    run_id = BILLSTATUS_ENRICHMENT.stable_json_sha256(
        {
            "plan_sha256": sha256_file(snapshot_plan_path),
            "producer_sha256": sha256_file(
                Path(BILLSTATUS_WORKER.__file__)
            ),
        }
    )
    worker_manifest = fetch_root / run_id / "run_manifest.json"
    if worker_manifest.is_file():
        audit_id = BILLSTATUS_ENRICHMENT.stable_json_sha256(
            {
                "worker_manifest_sha256": sha256_file(worker_manifest),
                "producer_sha256": sha256_file(
                    Path(BILLSTATUS_COMPLETENESS.__file__)
                ),
            }
        )
        return (
            worker_manifest,
            completeness_root / audit_id / "completeness_audit.json",
        )

    candidates: list[tuple[Path, Path]] = []
    for candidate in sorted(fetch_root.glob("*/run_manifest.json")):
        try:
            worker = read_object(candidate)
            worker_plan = worker.get("plan") or {}
            old_plan_path = Path(
                str(worker_plan.get("path") or "")
            ).resolve()
            worker_producer = worker.get("producer") or {}
            producer_path = Path(BILLSTATUS_WORKER.__file__).resolve()
            if not (
                worker.get("schema")
                == "govinfo-billstatus-enrichment-run-v1"
                and worker.get("status") == "COMPLETE"
                and worker.get("snapshot_only") is True
                and worker.get("official_source_only") is True
                and worker.get("failures") == []
                and worker.get("production_write_performed") is False
                and worker.get("sync_or_promotion_performed") is False
                and worker.get("candidate_creation_performed") is False
                and worker.get("semantic_exclusion_performed") is False
                and old_plan_path.is_file()
                and worker_plan.get("bytes") == old_plan_path.stat().st_size
                and worker_plan.get("sha256") == sha256_file(old_plan_path)
                and Path(
                    str(worker_producer.get("path") or "")
                ).resolve()
                == producer_path
                and worker_producer.get("bytes")
                == producer_path.stat().st_size
                and worker_producer.get("sha256")
                == sha256_file(producer_path)
            ):
                continue
            build_billstatus_semantic_rollover_binding(
                current_plan_path=snapshot_plan_path,
                reused_plan_path=old_plan_path,
            )
            audit_id = BILLSTATUS_ENRICHMENT.stable_json_sha256(
                {
                    "worker_manifest_sha256": sha256_file(candidate),
                    "producer_sha256": sha256_file(
                        Path(BILLSTATUS_COMPLETENESS.__file__)
                    ),
                }
            )
            completeness = (
                completeness_root / audit_id / "completeness_audit.json"
            )
            if completeness.is_file():
                candidates.append((candidate, completeness))
        except (
            OSError,
            TypeError,
            ValueError,
            KeyError,
            PresyncError,
            BILLSTATUS_ENRICHMENT.ContractError,
        ):
            continue
    if len(candidates) == 1:
        return candidates[0]
    return worker_manifest, None


def validate_billstatus_enrichment_completeness(
    audit_path: Path | None,
    *,
    expected_plan_path: Path | None,
    expected_worker_manifest_path: Path | None,
) -> tuple[bool, dict[str, Any]]:
    """Validate exact post-fetch closure and lossless gap visibility."""
    issues: list[str] = []
    required = {
        "completeness audit": audit_path,
        "snapshot plan": expected_plan_path,
        "worker manifest": expected_worker_manifest_path,
    }
    for label, path in required.items():
        if path is None or not path.is_file():
            issues.append(f"BILLSTATUS {label} is missing")
    if issues:
        return False, {
            "status": "MISSING",
            "audit": (
                file_receipt(audit_path)
                if audit_path and audit_path.is_file()
                else None
            ),
            "issues": issues,
        }
    assert audit_path is not None
    assert expected_plan_path is not None
    assert expected_worker_manifest_path is not None
    try:
        audit = BILLSTATUS_COMPLETENESS.validate_audit(audit_path)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        BILLSTATUS_COMPLETENESS.CompletenessError,
    ) as error:
        return False, {
            "status": "STALE_OR_INVALID",
            "audit": file_receipt(audit_path),
            "issues": [str(error)],
        }
    inputs = audit.get("inputs") or {}
    try:
        worker = read_object(expected_worker_manifest_path)
    except (PresyncError, ValueError) as error:
        return False, {
            "status": "STALE_OR_INVALID",
            "audit": file_receipt(audit_path),
            "issues": [f"BILLSTATUS worker manifest is unreadable: {error}"],
        }
    worker_plan = worker.get("plan") or {}
    reused_plan_path = Path(
        str(worker_plan.get("path") or "")
    ).resolve()
    rollover_binding: dict[str, Any] | None = None
    try:
        rollover_binding = build_billstatus_semantic_rollover_binding(
            current_plan_path=expected_plan_path,
            reused_plan_path=reused_plan_path,
        )
    except (
        OSError,
        TypeError,
        ValueError,
        KeyError,
        PresyncError,
        BILLSTATUS_ENRICHMENT.ContractError,
    ) as error:
        issues.append(
            "BILLSTATUS worker plan is neither exact nor safely "
            f"semantic-equivalent: {error}"
        )
    for label, recorded, expected in (
        (
            "worker manifest",
            inputs.get("worker_manifest"),
            expected_worker_manifest_path,
        ),
        ("snapshot plan", inputs.get("snapshot_plan"), reused_plan_path),
        (
            "completeness producer",
            audit.get("producer"),
            Path(BILLSTATUS_COMPLETENESS.__file__),
        ),
    ):
        if not isinstance(recorded, dict) or not (
            expected.is_file()
            and Path(str(recorded.get("path") or "")).resolve()
            == expected.resolve()
            and recorded.get("bytes") == expected.stat().st_size
            and recorded.get("sha256") == sha256_file(expected)
        ):
            issues.append(f"BILLSTATUS completeness {label} receipt drifted")
    worker_producer = worker.get("producer")
    audit_worker_producer = inputs.get("worker_producer")
    current_worker_path = Path(BILLSTATUS_WORKER.__file__).resolve()
    worker_producer_current = bool(
        isinstance(worker_producer, dict)
        and Path(str(worker_producer.get("path") or "")).resolve()
        == current_worker_path
        and worker_producer.get("bytes") == current_worker_path.stat().st_size
        and worker_producer.get("sha256") == sha256_file(current_worker_path)
        and audit_worker_producer == worker_producer
    )
    if not worker_producer_current:
        issues.append(
            "BILLSTATUS completeness worker producer receipt drifted"
        )
    if not (
        worker.get("schema") == "govinfo-billstatus-enrichment-run-v1"
        and worker.get("status") == "COMPLETE"
        and worker.get("snapshot_only") is True
        and worker.get("official_source_only") is True
        and worker.get("failures") == []
        and worker.get("production_write_performed") is False
        and worker.get("sync_or_promotion_performed") is False
        and worker.get("candidate_creation_performed") is False
        and worker.get("semantic_exclusion_performed") is False
        and (worker.get("sitemaps") or {}).get("required") == 32
        and (worker.get("sitemaps") or {}).get("validated") == 32
        and Path(str(worker_plan.get("path") or "")).resolve()
        == reused_plan_path
        and reused_plan_path.is_file()
        and worker_plan.get("bytes") == reused_plan_path.stat().st_size
        and worker_plan.get("sha256") == sha256_file(reused_plan_path)
    ):
        issues.append("BILLSTATUS complete worker/plan contract drifted")

    outputs = audit.get("outputs")
    gap_ledger_counts = audit.get("gap_ledger_counts")
    gap_counts = audit.get("gap_counts")
    outputs = outputs if isinstance(outputs, dict) else {}
    gap_ledger_counts = (
        gap_ledger_counts if isinstance(gap_ledger_counts, dict) else {}
    )
    gap_counts = gap_counts if isinstance(gap_counts, dict) else {}
    audit_dir = audit_path.resolve().parent
    if set(outputs) != set(EXPECTED_BILLSTATUS_GAP_OUTPUTS):
        issues.append(
            "BILLSTATUS completeness gap-ledger output set drifted"
        )
    declared_ledger_counts: dict[str, int] = {}
    observed_gap_counts: dict[str, int] = {}
    for label, filename in EXPECTED_BILLSTATUS_GAP_OUTPUTS.items():
        receipt = outputs.get(label)
        declared_count = gap_ledger_counts.get(label)
        if not (
            isinstance(declared_count, int)
            and not isinstance(declared_count, bool)
            and declared_count >= 0
        ):
            issues.append(
                f"BILLSTATUS completeness {label} declared count is invalid"
            )
            continue
        declared_ledger_counts[label] = declared_count
        if not isinstance(receipt, dict):
            issues.append(
                f"BILLSTATUS completeness {label} receipt is missing"
            )
            continue
        ledger = Path(str(receipt.get("path") or "")).resolve()
        if not (
            ledger.parent == audit_dir
            and ledger.name == filename
            and ledger.is_file()
            and receipt.get("bytes") == ledger.stat().st_size
            and receipt.get("sha256") == sha256_file(ledger)
            and receipt.get("record_count") == declared_count
        ):
            issues.append(
                f"BILLSTATUS completeness {label} receipt/count drifted"
            )
            continue
        row_count = 0
        row_contract_current = True
        try:
            with ledger.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    row_count += 1
                    row = json.loads(line)
                    if not (
                        isinstance(row, dict)
                        and isinstance(row.get("canonical_key"), str)
                        and bool(row["canonical_key"])
                        and isinstance(row.get("gap_kind"), str)
                        and bool(row["gap_kind"])
                        and isinstance(row.get("field"), str)
                        and bool(row["field"])
                        and isinstance(row.get("scope"), str)
                        and bool(row["scope"])
                        and row.get("candidate_retained") is True
                        and row.get("absence_is_neutral") is True
                        and row.get("automatic_exclusion_authorized") is False
                        and row.get("automatic_scoring_authorized") is False
                    ):
                        row_contract_current = False
                    if isinstance(row, dict):
                        gap_kind = row.get("gap_kind")
                        if isinstance(gap_kind, str) and gap_kind:
                            observed_gap_counts[gap_kind] = (
                                observed_gap_counts.get(gap_kind, 0) + 1
                            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            row_contract_current = False
        if row_count != declared_count:
            issues.append(
                f"BILLSTATUS completeness {label} row count drifted"
            )
        if not row_contract_current:
            issues.append(
                f"BILLSTATUS completeness {label} row safety contract drifted"
            )
    declared_total = gap_ledger_counts.get("all_visible_gaps")
    if not (
        isinstance(declared_total, int)
        and not isinstance(declared_total, bool)
        and declared_total >= 0
        and len(declared_ledger_counts)
        == len(EXPECTED_BILLSTATUS_GAP_OUTPUTS)
        and sum(declared_ledger_counts.values()) == declared_total
    ):
        issues.append(
            "BILLSTATUS completeness aggregate gap-ledger count drifted"
        )
    if observed_gap_counts != gap_counts:
        issues.append(
            "BILLSTATUS completeness gap-kind counts drifted"
        )

    relationship_counts = audit.get("relationship_counts")
    relationship_counts = (
        relationship_counts
        if isinstance(relationship_counts, dict)
        else {}
    )
    relationship_values = [
        relationship_counts.get(key)
        for key in (
            "relationship_rows",
            "sponsor_items",
            "cosponsor_items",
            "sponsor_relationships",
            "original_cosponsor_relationships",
            "later_cosponsor_relationships",
            "relationships_with_bioguide",
            "relationships_missing_bioguide",
            "relationships_missing_full_name",
            "unresolved_role_relationships",
        )
    ]
    if not (
        all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for value in relationship_values
        )
        and relationship_counts["relationship_rows"]
        == relationship_counts["sponsor_items"]
        + relationship_counts["cosponsor_items"]
        == relationship_counts["sponsor_relationships"]
        + relationship_counts["original_cosponsor_relationships"]
        + relationship_counts["later_cosponsor_relationships"]
        + relationship_counts["unresolved_role_relationships"]
        == relationship_counts["relationships_with_bioguide"]
        + relationship_counts["relationships_missing_bioguide"]
        and relationship_counts["relationships_missing_full_name"]
        <= relationship_counts["relationship_rows"]
    ):
        issues.append(
            "BILLSTATUS completeness relationship partition drifted"
        )
    visible_gap_ledgers = {
        label: {
            **file_receipt(
                Path(str(outputs[label]["path"])).resolve()
            ),
            "record_count": outputs[label]["record_count"],
        }
        for label in EXPECTED_BILLSTATUS_GAP_OUTPUTS
        if (
            isinstance(outputs.get(label), dict)
            and Path(str(outputs[label].get("path") or "")).is_file()
        )
    }
    state = {
        "status": "CURRENT" if not issues else "STALE_OR_INVALID",
        "audit": file_receipt(audit_path),
        "worker_manifest": file_receipt(expected_worker_manifest_path),
        "worker_producer": (
            worker_producer if isinstance(worker_producer, dict) else None
        ),
        "semantic_rollover_binding": rollover_binding,
        "completeness_producer": audit.get("producer"),
        "closure": audit.get("closure"),
        "content_counts": audit.get("content_counts"),
        "relationship_counts": audit.get("relationship_counts"),
        "gap_counts": audit.get("gap_counts"),
        "gap_ledger_counts": audit.get("gap_ledger_counts"),
        "gap_ledgers": visible_gap_ledgers,
        "gap_ledger_output_set_closed": (
            set(visible_gap_ledgers)
            == set(EXPECTED_BILLSTATUS_GAP_OUTPUTS)
        ),
        "gap_rows_retained_neutral_and_non_scoring": not any(
            "row safety contract drifted" in issue for issue in issues
        ),
        "relationship_partition_closed": not any(
            "relationship partition drifted" in issue for issue in issues
        ),
        "absence_is_neutral": True,
        "automatic_scoring_performed": False,
        "candidate_exclusion_performed": False,
        "network_executed": False,
        "sync_or_promotion_performed": False,
        "issues": issues,
    }
    return not issues, state


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )
    if path.is_file():
        try:
            if path.read_bytes() == body:
                return
        except OSError:
            pass
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.05 * (2**attempt), 0.75))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PresyncError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PresyncError(f"JSON root must be an object: {path}")
    return value


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def safe_output(path: Path, workspace_root: Path) -> Path:
    resolved = path.resolve()
    audit_root = (
        workspace_root / "audits" / "legfix5" / "shadow_recompute"
    ).resolve()
    if not is_relative_to(resolved, audit_root) or resolved == audit_root:
        raise PresyncError(
            "--out must be a child of audits/legfix5/shadow_recompute"
        )
    return resolved


def resolve_input(
    raw: Any,
    *,
    workspace_root: Path,
    label: str,
    blockers: list[dict[str, Any]],
    kind: str = "file",
) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        blockers.append({"code": "missing_explicit_input", "input": label})
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = workspace_root / path
    path = path.resolve()
    exists = path.is_file() if kind == "file" else path.is_dir()
    if not exists:
        blockers.append(
            {
                "code": "explicit_input_not_found",
                "input": label,
                "path": str(path),
            }
        )
        return None
    return path


def get_path(config: dict[str, Any], dotted: str) -> Any:
    value: Any = config
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _decay_source_path(raw: Any, workspace_root: Path) -> Path | None:
    """Resolve a decay-audit source without allowing it outside the workspace."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = workspace_root / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    return (
        resolved
        if is_relative_to(resolved, workspace_root.resolve())
        else None
    )


def _decay_live_source_path(raw: Any, workspace_root: Path) -> Path | None:
    """Resolve a relocation-aware live source within the workspace."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        resolved = resolve_live_source(raw, workspace_root)
    except OSError:
        return None
    return (
        resolved
        if is_relative_to(resolved, workspace_root.resolve())
        else None
    )


def _decay_receipt_current(
    item: Any,
    *,
    workspace_root: Path,
    issues: list[str],
    label: str,
) -> str | None:
    """Validate one embedded file receipt and return its current SHA-256."""
    if not isinstance(item, dict):
        issues.append(f"{label}: file receipt is absent or malformed")
        return None
    path = _decay_source_path(item.get("path"), workspace_root)
    expected = str(item.get("sha256") or "").casefold()
    if path is None:
        issues.append(f"{label}: source path is absent or outside workspace")
        return None
    if not path.is_file():
        issues.append(f"{label}: source file is missing: {path}")
        return None
    if len(expected) != 64:
        issues.append(f"{label}: source SHA-256 is malformed")
        return None
    observed = sha256_file(path)
    if observed != expected:
        issues.append(
            f"{label}: source hash is stale "
            f"(expected {expected}, observed {observed})"
        )
        return None
    if item.get("size") is not None:
        try:
            expected_size = int(item["size"])
        except (TypeError, ValueError):
            issues.append(f"{label}: source size is malformed")
            return None
        if path.stat().st_size != expected_size:
            issues.append(f"{label}: source size is stale")
            return None
    return observed


def _normalized_decay_schedule(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return {
            str(int(congress)): float(weight)
            for congress, weight in value.items()
        }
    except (TypeError, ValueError):
        return None


def validate_decay_propagation_audit(
    receipt_path: Path | None,
    *,
    workspace_root: Path,
) -> tuple[bool, dict[str, Any]]:
    """Validate the static decay audit and every hash-bound source fail closed."""
    issues: list[str] = []
    if receipt_path is None or not receipt_path.is_file():
        return False, {
            "status": "MISSING",
            "passed": False,
            "issues": ["decay propagation audit receipt is missing"],
            "receipt": None,
        }
    try:
        audit = read_object(receipt_path)
    except PresyncError as exc:
        return False, {
            "status": "INVALID",
            "passed": False,
            "issues": [str(exc)],
            "receipt": file_receipt(receipt_path),
        }

    required_top_level = {
        "schema": DECAY_AUDIT_SCHEMA,
        "status": "PASS",
        "staging_only": True,
        "read_only_source_audit": True,
        "collector_executed": False,
        "production_or_mainframe_written": False,
        "promotion_authorized": False,
    }
    for key, expected in required_top_level.items():
        if audit.get(key) != expected:
            issues.append(f"{key} must be {expected!r}")

    schedules = audit.get("schedules")
    schedules = schedules if isinstance(schedules, dict) else {}
    if (
        _normalized_decay_schedule(schedules.get("staged_unapproved"))
        != EXPECTED_STAGED_DECAY
    ):
        issues.append("staged decay schedule is absent or changed")
    if (
        _normalized_decay_schedule(schedules.get("frozen_live"))
        != EXPECTED_FROZEN_DECAY
    ):
        issues.append("frozen production decay schedule is absent or changed")
    if schedules.get("schedules_equal") is not False:
        issues.append("staged and frozen schedules are not explicitly distinct")

    context = audit.get("acceptance_context")
    context = context if isinstance(context, dict) else {}
    required_context = {
        "snapshot_only": True,
        "authoritative_for_policy_approval": False,
        "authoritative_for_shadow_or_sync": False,
        "decay_propagation_static_gate_passed": True,
    }
    for key, expected in required_context.items():
        if context.get(key) != expected:
            issues.append(f"acceptance_context.{key} must be {expected!r}")
    for key in (
        "decay_policy_approved",
        "real_shadow_run_complete",
        "sync_allowed",
    ):
        if not isinstance(context.get(key), bool):
            issues.append(
                f"acceptance_context.{key} must be boolean snapshot metadata"
            )
    if audit.get("blocking_issues") != []:
        issues.append("decay audit reports blocking issues")

    staged = audit.get("staged_producer_contracts")
    staged = staged if isinstance(staged, list) else []
    staged_names = [
        item.get("name") for item in staged if isinstance(item, dict)
    ]
    if (
        len(staged) != EXPECTED_DECAY_CONTRACT_COUNT
        or len(set(staged_names)) != EXPECTED_DECAY_CONTRACT_COUNT
    ):
        issues.append("staged producer contracts are not exactly 9 distinct rows")
    staged_hashes: list[str] = []
    for index, item in enumerate(staged):
        label = f"staged_producer_contracts[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label}: row is malformed")
            continue
        if (
            item.get("passed") is not True
            or item.get("required_tokens_present") is not True
            or item.get("missing_tokens") != []
            or _normalized_decay_schedule(item.get("observed_schedule"))
            != EXPECTED_STAGED_DECAY
            or _normalized_decay_schedule(item.get("expected_schedule"))
            != EXPECTED_STAGED_DECAY
        ):
            issues.append(f"{label}: staged schedule contract did not pass")
        source_hash = _decay_receipt_current(
            item.get("source"),
            workspace_root=workspace_root,
            issues=issues,
            label=f"{label}.source",
        )
        if source_hash:
            staged_hashes.append(source_hash)

    consumers = audit.get("downstream_consumer_ledger")
    consumers = consumers if isinstance(consumers, list) else []
    consumer_names = [
        item.get("stage") for item in consumers if isinstance(item, dict)
    ]
    allowed_modes = {"direct", "mixed", "inherited", "intentionally_absent"}
    if (
        len(consumers) != EXPECTED_DECAY_CONSUMER_COUNT
        or len(set(consumer_names)) != EXPECTED_DECAY_CONSUMER_COUNT
    ):
        issues.append("downstream consumer ledger is not exactly 16 distinct rows")
    consumer_hashes: list[str] = []
    consumer_modes: list[list[str]] = []
    for index, item in enumerate(consumers):
        label = f"downstream_consumer_ledger[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label}: row is malformed")
            continue
        stage = item.get("stage")
        mode = item.get("mode")
        if not isinstance(stage, str) or mode not in allowed_modes:
            issues.append(f"{label}: stage or consumer classification is invalid")
        else:
            consumer_modes.append([stage, mode])
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources:
            issues.append(f"{label}: source receipts are absent")
            continue
        for source_index, source in enumerate(sources):
            source_hash = _decay_receipt_current(
                source,
                workspace_root=workspace_root,
                issues=issues,
                label=f"{label}.sources[{source_index}]",
            )
            if source_hash:
                consumer_hashes.append(source_hash)

    if audit.get("staged_consumer_using_old_schedule") != []:
        issues.append("one or more staged consumers still use the old schedule")

    manual = audit.get("manual_LTR_evidence")
    manual = manual if isinstance(manual, dict) else {}
    if (
        manual.get("passed") is not True
        or manual.get("stored_values_are_not_staged_authority") is not True
        or _normalized_decay_schedule(manual.get("staged_schedule_applied"))
        != EXPECTED_STAGED_DECAY
    ):
        issues.append("manual LTR decay provenance did not pass")
    manual_hash = _decay_receipt_current(
        manual.get("file"),
        workspace_root=workspace_root,
        issues=issues,
        label="manual_LTR_evidence.file",
    )
    _decay_receipt_current(
        manual.get("staged_canonicalization_source"),
        workspace_root=workspace_root,
        issues=issues,
        label="manual_LTR_evidence.staged_canonicalization_source",
    )

    production = audit.get("production_baseline")
    production = production if isinstance(production, dict) else {}
    if (
        production.get("passed") is not True
        or production.get("declared_file_count")
        != EXPECTED_DECAY_PRODUCTION_FILE_COUNT
        or production.get("frozen_copy_hash_match_count")
        != EXPECTED_DECAY_PRODUCTION_FILE_COUNT
        or production.get("frozen_copy_mismatches") != []
        or production.get("frozen_baseline_integrity_passed") is not True
        or production.get("production_no_cutover_gate_passed") is not True
        or production.get("production_cutover_to_staged_schedule_detected")
        is not False
    ):
        issues.append(
            "frozen baseline integrity or production no-cutover proof failed"
        )
    production_manifest_hash = _decay_receipt_current(
        production.get("manifest"),
        workspace_root=workspace_root,
        issues=issues,
        label="production_baseline.manifest",
    )
    checked_files = production.get("checked_files")
    checked_files = checked_files if isinstance(checked_files, list) else []
    if len(checked_files) != EXPECTED_DECAY_PRODUCTION_FILE_COUNT:
        issues.append(
            "production checked-file ledger is not exactly "
            f"{TOTAL} rows"
        )
    observed_live_drift_names: list[str] = []
    frozen_copy_match_count = 0
    live_source_match_count = 0
    for index, item in enumerate(checked_files):
        label = f"production_baseline.checked_files[{index}]"
        if not isinstance(item, dict):
            issues.append(f"{label}: row is malformed")
            continue
        expected = str(item.get("expected_sha256") or "").casefold()
        source = _decay_live_source_path(item.get("source"), workspace_root)
        frozen_copy = _decay_source_path(item.get("copy"), workspace_root)
        expected_size = item.get("expected_size")
        copy_integrity = item.get("frozen_copy_integrity_passed")
        source_matches = item.get("live_source_matches_frozen")
        source_drift = item.get("live_source_drift_observed")
        if (
            len(expected) != 64
            or str(item.get("copy_sha256") or "").casefold() != expected
            or copy_integrity is not True
        ):
            issues.append(f"{label}: frozen copy does not match manifest")
        elif frozen_copy is None or not frozen_copy.is_file():
            issues.append(f"{label}.copy: file is missing or unsafe")
        elif (
            sha256_file(frozen_copy) != expected
            or frozen_copy.stat().st_size != expected_size
            or item.get("copy_size") != expected_size
        ):
            issues.append(f"{label}.copy: current frozen hash or size is stale")
        else:
            frozen_copy_match_count += 1

        if (
            not isinstance(source_matches, bool)
            or not isinstance(source_drift, bool)
            or source_drift is source_matches
            or item.get("passed") is not source_matches
        ):
            issues.append(f"{label}: live drift classification is inconsistent")
            continue
        if source is None or not source.is_file():
            issues.append(f"{label}.source: file is missing or unsafe")
            continue
        current_source_hash = sha256_file(source)
        if (
            str(item.get("source_sha256") or "").casefold()
            != current_source_hash
            or item.get("source_size") != source.stat().st_size
        ):
            issues.append(f"{label}.source: current live receipt is stale")
            continue
        actual_source_matches = (
            current_source_hash == expected
            and source.stat().st_size == expected_size
        )
        if actual_source_matches is not source_matches:
            issues.append(f"{label}: live/frozen byte identity is misstated")
            continue
        if source_matches:
            live_source_match_count += 1
        else:
            observed_live_drift_names.append(source.name)

    declared_live_match_count = production.get("live_source_match_count")
    declared_live_drift_count = production.get("live_source_drift_count")
    declared_live_drift = production.get("live_source_drift")
    declared_live_drift = (
        declared_live_drift if isinstance(declared_live_drift, list) else []
    )
    declared_live_drift_names = [
        Path(str(item.get("source") or "")).name
        for item in declared_live_drift
        if isinstance(item, dict)
    ]
    if (
        frozen_copy_match_count != EXPECTED_DECAY_PRODUCTION_FILE_COUNT
        or declared_live_match_count
        != EXPECTED_DECAY_PRODUCTION_LIVE_MATCH_COUNT
        or live_source_match_count
        != EXPECTED_DECAY_PRODUCTION_LIVE_MATCH_COUNT
        or declared_live_drift_count
        != EXPECTED_DECAY_PRODUCTION_LIVE_DRIFT_COUNT
        or len(observed_live_drift_names)
        != EXPECTED_DECAY_PRODUCTION_LIVE_DRIFT_COUNT
        or declared_live_match_count + declared_live_drift_count
        != EXPECTED_DECAY_PRODUCTION_FILE_COUNT
        or sorted(declared_live_drift_names)
        != sorted(observed_live_drift_names)
        or production.get("source_and_copy_hash_match_count")
        != EXPECTED_DECAY_PRODUCTION_LIVE_MATCH_COUNT
        or production.get("source_and_copy_mismatches")
        != declared_live_drift
        or production.get("all_live_sources_and_frozen_copies_match_manifest")
        is not (declared_live_drift_count == 0)
        or production.get("live_source_drift_is_not_baseline_tampering")
        is not True
    ):
        issues.append("production live-drift partition is not exact")

    drift_state: dict[str, Any] = {
        "status": "NOT_REQUIRED",
        "evidence_current": True,
        "informational_only": True,
        "affects_gate_decisions": False,
        "changed_file_names": [],
        "issues": [],
    }
    if observed_live_drift_names:
        drift_pointer = (
            workspace_root
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_downstream_drift"
            / "current.json"
        )
        drift_current, drift_state = (
            validate_post_weekly_refresh_downstream_drift(drift_pointer)
        )
        if not drift_current:
            issues.append(
                "live source drift lacks a current, non-authoritative "
                "weekly-refresh observation"
            )
        elif sorted(
            Path(str(name)).name
            for name in (drift_state.get("changed_file_names") or [])
        ) != sorted(observed_live_drift_names):
            # 2026-08-05: downstream identities keep their manifest directory
            # prefixes (fec/... for the 22 relocated producer sources) while
            # the decay side observes basenames; the 55 basenames are unique,
            # so the closure compares on basename.
            issues.append(
                "weekly-refresh observation does not close over exact "
                "live source drift"
            )

    bill_rules = production.get("bill_rules")
    bill_rules = bill_rules if isinstance(bill_rules, dict) else {}
    if (
        bill_rules.get("passed") is not True
        or bill_rules.get("factors") != {"live": 0.8, "frozen_copy": 0.8}
        or _normalized_decay_schedule(bill_rules.get("computed_schedule"))
        != EXPECTED_FROZEN_DECAY
        or _normalized_decay_schedule(
            bill_rules.get("expected_frozen_schedule")
        )
        != EXPECTED_FROZEN_DECAY
    ):
        issues.append("production bill-rules freeze contract did not pass")
    for role in ("live", "frozen_copy"):
        _decay_receipt_current(
            bill_rules.get(role),
            workspace_root=workspace_root,
            issues=issues,
            label=f"production_baseline.bill_rules.{role}",
        )
    archive = production.get("archive_decay_column")
    archive = archive if isinstance(archive, dict) else {}
    if archive.get("passed") is not True or archive.get("mismatches") != {}:
        issues.append("frozen archive decay column did not pass")
    _decay_receipt_current(
        archive.get("file"),
        workspace_root=workspace_root,
        issues=issues,
        label="production_baseline.archive_decay_column.file",
    )
    live_archive = production.get("live_archive_decay_column")
    live_archive = live_archive if isinstance(live_archive, dict) else {}
    if (
        live_archive.get("passed") is not True
        or live_archive.get("mismatches") != {}
    ):
        issues.append("live archive no-cutover decay proof did not pass")
    _decay_receipt_current(
        live_archive.get("file"),
        workspace_root=workspace_root,
        issues=issues,
        label="production_baseline.live_archive_decay_column.file",
    )

    producer_hash = _decay_receipt_current(
        audit.get("producer"),
        workspace_root=workspace_root,
        issues=issues,
        label="producer",
    )
    summary = audit.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    expected_mode_counts = {
        "direct_stage_count": sum(row[1] == "direct" for row in consumer_modes),
        "mixed_stage_count": sum(row[1] == "mixed" for row in consumer_modes),
        "inherited_stage_count": sum(
            row[1] == "inherited" for row in consumer_modes
        ),
        "intentionally_absent_stage_count": sum(
            row[1] == "intentionally_absent" for row in consumer_modes
        ),
    }
    required_summary = {
        "staged_contract_count": EXPECTED_DECAY_CONTRACT_COUNT,
        "staged_contract_pass_count": EXPECTED_DECAY_CONTRACT_COUNT,
        "downstream_stage_count": EXPECTED_DECAY_CONSUMER_COUNT,
        "production_file_count": EXPECTED_DECAY_PRODUCTION_FILE_COUNT,
        "production_file_match_count": EXPECTED_DECAY_PRODUCTION_FILE_COUNT,
        "production_live_source_match_count": (
            EXPECTED_DECAY_PRODUCTION_LIVE_MATCH_COUNT
        ),
        "production_live_source_drift_count": (
            EXPECTED_DECAY_PRODUCTION_LIVE_DRIFT_COUNT
        ),
        "staged_old_schedule_consumer_count": 0,
        "production_cutover_detected": False,
        **expected_mode_counts,
    }
    for key, expected in required_summary.items():
        if summary.get(key) != expected:
            issues.append(f"summary.{key} must be {expected!r}")

    basis = audit.get("audit_basis")
    basis = basis if isinstance(basis, dict) else {}
    reconstructed_hashes = sorted(
        set(
            staged_hashes
            + consumer_hashes
            + [
                value
                for value in (
                    production_manifest_hash,
                    manual_hash,
                    producer_hash,
                )
                if value
            ]
        )
    )
    if (
        basis.get("source_sha256") != reconstructed_hashes
        or basis.get("source_count") != len(reconstructed_hashes)
    ):
        issues.append("audit_basis source hashes are absent, tampered, or stale")
    expected_basis_hash = sha256_bytes(
        canonical_bytes(
            {
                "schema": audit.get("schema"),
                "schedules": schedules,
                "source_sha256": reconstructed_hashes,
                "consumer_modes": consumer_modes,
            }
        )
    )
    if str(basis.get("sha256") or "").casefold() != expected_basis_hash:
        issues.append("audit_basis digest is absent or tampered")

    passed = not issues
    return passed, {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "issues": issues,
        "receipt": file_receipt(receipt_path),
        "schema": audit.get("schema"),
        "staged_contracts": {
            "passed": summary.get("staged_contract_pass_count"),
            "required": EXPECTED_DECAY_CONTRACT_COUNT,
        },
        "consumer_classification": {
            "classified": summary.get("downstream_stage_count"),
            "required": EXPECTED_DECAY_CONSUMER_COUNT,
        },
        "old_staged_consumers": summary.get(
            "staged_old_schedule_consumer_count"
        ),
        "production_freeze": {
            "matched": summary.get("production_file_match_count"),
            "required": EXPECTED_DECAY_PRODUCTION_FILE_COUNT,
            "cutover_detected": summary.get("production_cutover_detected"),
        },
        "live_drift": {
            "matched_frozen": summary.get(
                "production_live_source_match_count"
            ),
            "drifted": summary.get("production_live_source_drift_count"),
            "observed_names": sorted(observed_live_drift_names),
            "weekly_refresh_observation": drift_state,
            "baseline_integrity_affected": False,
            "cutover_inferred_from_byte_drift": False,
        },
        "acceptance_context": {
            "snapshot_only": context.get("snapshot_only"),
            "authoritative_for_policy_approval": context.get(
                "authoritative_for_policy_approval"
            ),
            "authoritative_for_shadow_or_sync": context.get(
                "authoritative_for_shadow_or_sync"
            ),
            "snapshot_values": {
                key: context.get(key)
                for key in (
                    "decay_policy_approved",
                    "real_shadow_run_complete",
                    "sync_allowed",
                )
            },
        },
        "audit_basis": basis,
    }


def query_is_exact(query: dict[str, Any]) -> bool:
    validation = query.get("validation") or {}
    return bool(
        validation.get("exact_result_total") is True
        and validation.get("unique_result_total") is True
        and validation.get("every_page_exact") is True
    )


def explicit_pass(value: dict[str, Any]) -> bool:
    status = str(value.get("status") or "").casefold()
    return bool(
        value.get("acceptance_passed") is True
        or value.get("promotion_gate_passed") is True
        or value.get("generation_complete") is True
        or value.get("scenario_computation_complete") is True
        or value.get("complete") is True
        or status in {"pass", "passed"}
    )


def _receipt_matches_file(
    receipt: Any,
    expected: Path,
    *,
    base_dir: Path | None = None,
) -> bool:
    """Return true only for a current hash/size receipt for ``expected``."""
    if not isinstance(receipt, dict):
        return False
    raw_path = receipt.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    observed = Path(raw_path)
    if not observed.is_absolute():
        if base_dir is None:
            return False
        observed = base_dir / observed
    try:
        observed = observed.resolve()
        expected = expected.resolve()
    except OSError:
        return False
    return bool(
        observed == expected
        and expected.is_file()
        and receipt.get("size") == expected.stat().st_size
        and str(receipt.get("sha256") or "").casefold()
        == sha256_file(expected)
    )


def validate_rollcall_identity_manifest(
    manifest: Any,
    *,
    expected_member_map: Path | None = None,
    expected_member_map_present: bool | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Validate optional-map binding and fail closed on every unresolved identity."""
    issues: list[str] = []
    value = manifest if isinstance(manifest, dict) else {}
    identity = value.get("member_identity_resolution")
    identity = identity if isinstance(identity, dict) else {}
    map_state = identity.get("supplemental_member_map")
    map_state = map_state if isinstance(map_state, dict) else {}
    map_present = map_state.get("present")
    map_receipt = map_state.get("receipt")

    if map_present is True:
        if map_state.get("status") != "present":
            issues.append("supplemental member-map status is not present")
        raw_path = (
            map_receipt.get("path")
            if isinstance(map_receipt, dict)
            else None
        )
        receipt_path = Path(raw_path).resolve() if raw_path else None
        if receipt_path is None or not _receipt_matches_file(
            map_receipt, receipt_path
        ):
            issues.append("supplemental member-map receipt is absent or stale")
    elif map_present is False:
        if (
            map_state.get("status") != "absent"
            or map_receipt is not None
        ):
            issues.append("absent supplemental member map is not bound as absent")
    else:
        issues.append("supplemental member-map presence is not explicit")

    if expected_member_map_present is not None and (
        map_present is not expected_member_map_present
    ):
        issues.append("supplemental member-map presence differs from orchestration")
    if expected_member_map is not None and not _receipt_matches_file(
        map_receipt, expected_member_map
    ):
        issues.append("supplemental member-map receipt differs from orchestration")

    status_counts = identity.get("resolution_status_counts")
    status_counts = status_counts if isinstance(status_counts, dict) else {}
    included_counts = identity.get("included_resolution_counts")
    included_counts = (
        included_counts if isinstance(included_counts, dict) else {}
    )
    numeric_fields = {
        "accepted_event_member_row_count": identity.get(
            "accepted_event_member_row_count"
        ),
        "included": status_counts.get("included", 0),
        "unmatched": status_counts.get("unmatched", 0),
        "ambiguous": status_counts.get("ambiguous", 0),
        "direct": included_counts.get("official_xml_bioguide"),
        "map": included_counts.get("supplemental_member_map"),
        "recorded_unmatched": identity.get("unmatched_member_row_count"),
        "recorded_ambiguous": identity.get("ambiguous_member_row_count"),
        "recorded_unresolved": identity.get(
            "unresolved_or_ambiguous_member_row_count"
        ),
    }
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in numeric_fields.values()
    ):
        issues.append("member identity resolution counts are absent or invalid")
    else:
        unresolved = numeric_fields["unmatched"] + numeric_fields["ambiguous"]
        if (
            numeric_fields["accepted_event_member_row_count"]
            != (
                numeric_fields["included"]
                + numeric_fields["unmatched"]
                + numeric_fields["ambiguous"]
            )
        ):
            issues.append("member identity status counts do not close")
        if numeric_fields["included"] != (
            numeric_fields["direct"] + numeric_fields["map"]
        ):
            issues.append("direct and supplemental-map resolution counts do not close")
        if (
            numeric_fields["recorded_unmatched"]
            != numeric_fields["unmatched"]
            or numeric_fields["recorded_ambiguous"]
            != numeric_fields["ambiguous"]
            or numeric_fields["recorded_unresolved"] != unresolved
        ):
            issues.append("recorded unresolved member counts are inconsistent")
        if unresolved != 0:
            issues.append("one or more accepted-event members are unresolved")

    if identity.get("all_accepted_event_members_uniquely_resolved") is not True:
        issues.append("unique resolution is not affirmed for every accepted-event member")
    if identity.get("fuzzy_matching_used") is not False:
        issues.append("fuzzy member matching is not explicitly disabled")
    if (
        value.get("acceptance_passed") is not True
        or value.get("blocking_issue_count") != 0
    ):
        issues.append("official roll-call manifest acceptance did not pass")

    passed = not issues
    return passed, {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "issues": issues,
        "supplemental_member_map": map_state,
        "accepted_event_member_row_count": identity.get(
            "accepted_event_member_row_count"
        ),
        "included_resolution_counts": included_counts,
        "resolution_status_counts": status_counts,
        "unresolved_or_ambiguous_member_row_count": identity.get(
            "unresolved_or_ambiguous_member_row_count"
        ),
        "all_accepted_event_members_uniquely_resolved": identity.get(
            "all_accepted_event_members_uniquely_resolved"
        ),
        "fuzzy_matching_used": identity.get("fuzzy_matching_used"),
    }


def _read_ndjson_objects(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PresyncError(f"cannot decode NDJSON {path}: {exc}") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PresyncError(
                f"invalid NDJSON {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise PresyncError(
                f"NDJSON row must be an object: {path}:{line_number}"
            )
        rows.append(row)
    return rows


def acu_title_query_plan_gate(
    final_path: Path | None,
    query_plan_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate the exhaustive, non-dispositive ACU title query plan.

    The title plan is required only after a final coverage manifest exists.
    A structurally valid BLOCKED plan is preserved in the audit, including its
    unresolved ledger, but cannot pass pre-sync acceptance.
    """
    blockers: list[dict[str, Any]] = []
    audit: dict[str, Any] = {
        "required_after_final_coverage_exists": True,
        "manifest_valid": False,
        "acceptance_passed": False,
        "unresolved_rows_visible": False,
        "absence_is_neutral": False,
        "automatic_decisions_disabled": False,
        "official_relevance_congress_gov_only": False,
    }
    if final_path is None:
        audit["required_now"] = False
        return audit, blockers

    final_path = final_path.resolve()
    audit["required_now"] = True
    plan_path = (
        query_plan_path.resolve()
        if query_plan_path is not None
        else (
            final_path.parent
            / "article_cross_reference"
            / "acu_title_query_plan"
            / "manifest.json"
        ).resolve()
    )
    audit["expected_manifest_path"] = str(plan_path)
    if not plan_path.is_file():
        blockers.append(
            {
                "code": "acu_title_query_plan_missing",
                "path": str(plan_path),
            }
        )
        return audit, blockers

    audit["manifest"] = file_receipt(plan_path)
    try:
        final = read_object(final_path)
        plan = read_object(plan_path)
        plan_dir = plan_path.parent.resolve()
        union = final.get("union")
        if not isinstance(union, dict):
            raise PresyncError("final manifest lacks union inputs")
        records_name = union.get("records_file")
        memberships_name = union.get("memberships_file")
        if not isinstance(records_name, str) or not records_name.strip():
            raise PresyncError("final manifest lacks union records_file")
        if not isinstance(memberships_name, str) or not memberships_name.strip():
            raise PresyncError("final manifest lacks union memberships_file")
        records_path = (final_path.parent / records_name).resolve()
        memberships_path = (final_path.parent / memberships_name).resolve()
        if not (
            is_relative_to(records_path, final_path.parent.resolve())
            and is_relative_to(memberships_path, final_path.parent.resolve())
            and records_path.is_file()
            and memberships_path.is_file()
        ):
            raise PresyncError("final union inputs are missing or escape the run")

        inputs = plan.get("inputs")
        outputs = plan.get("outputs")
        if not isinstance(inputs, dict) or not isinstance(outputs, dict):
            raise PresyncError("query-plan inputs/outputs must be objects")
        input_receipts_ok = bool(
            _receipt_matches_file(inputs.get("run_manifest"), final_path)
            and _receipt_matches_file(inputs.get("records"), records_path)
            and _receipt_matches_file(
                inputs.get("memberships"), memberships_path
            )
        )
        expected_output_names = {
            "queries",
            "candidate_coverage",
            "unresolved",
        }
        if set(outputs) != expected_output_names:
            raise PresyncError("query-plan output receipt set is incomplete")
        output_paths: dict[str, Path] = {}
        output_receipts_ok = True
        for name in sorted(expected_output_names):
            receipt = outputs.get(name)
            raw = receipt.get("path") if isinstance(receipt, dict) else None
            if not isinstance(raw, str) or not raw.strip():
                output_receipts_ok = False
                continue
            artifact = (plan_dir / raw).resolve()
            if not is_relative_to(artifact, plan_dir):
                output_receipts_ok = False
                continue
            output_paths[name] = artifact
            output_receipts_ok = bool(
                output_receipts_ok
                and _receipt_matches_file(
                    receipt,
                    artifact,
                    base_dir=plan_dir,
                )
            )
        if not input_receipts_ok or not output_receipts_ok:
            raise PresyncError("query-plan hash receipts are stale or invalid")

        query_rows = _read_ndjson_objects(output_paths["queries"])
        coverage_rows = _read_ndjson_objects(
            output_paths["candidate_coverage"]
        )
        unresolved_rows = _read_ndjson_objects(output_paths["unresolved"])
        record_rows = _read_ndjson_objects(records_path)
        membership_rows = _read_ndjson_objects(memberships_path)
        record_keys = [
            str(row.get("canonical_key") or "").strip().casefold()
            for row in record_rows
        ]
        membership_keys = {
            str(row.get("canonical_key") or "").strip().casefold()
            for row in membership_rows
        }
        membership_pairs = [
            (
                str(row.get("canonical_key") or "").strip().casefold(),
                str(row.get("query_id") or "").strip(),
            )
            for row in membership_rows
        ]
        if (
            "" in record_keys
            or len(record_keys) != len(set(record_keys))
            or "" in membership_keys
            or any(not query_id for _, query_id in membership_pairs)
            or len(membership_pairs) != len(set(membership_pairs))
            or membership_keys != set(record_keys)
        ):
            raise PresyncError(
                "final records/memberships are incomplete, duplicated, or "
                "do not cover the same candidate measures"
            )
        expected_keys = set(record_keys)
        coverage_keys = [
            str(row.get("canonical_key") or "").strip().casefold()
            for row in coverage_rows
        ]
        unresolved_keys = [
            str(row.get("canonical_key") or "").strip().casefold()
            for row in unresolved_rows
        ]
        if (
            "" in coverage_keys
            or "" in unresolved_keys
            or len(coverage_keys) != len(set(coverage_keys))
            or len(unresolved_keys) != len(set(unresolved_keys))
            or set(coverage_keys) & set(unresolved_keys)
            or set(coverage_keys) | set(unresolved_keys) != expected_keys
        ):
            raise PresyncError(
                "query-plan coverage/unresolved ledgers do not partition "
                "every discovered candidate measure"
            )

        contract = ACU_TITLE_PLAN.NON_DISPOSITIVE_CONTRACT
        all_rows = query_rows + coverage_rows + unresolved_rows
        row_contracts_ok = all(
            all(row.get(key) == value for key, value in contract.items())
            for row in all_rows
        )
        title_sources_ok = all(
            row.get("official_title_source")
            in {
                "congress_gov_official_title",
                "congress_gov_union_record_title",
            }
            for row in query_rows + coverage_rows
        )
        allowed_domains = {
            domain.domain for domain in ACU_TITLE_PLAN.OFFICIAL_DOMAINS
        }
        domains_ok = all(
            row.get("domain") in allowed_domains for row in query_rows
        )
        query_ids = [
            str(row.get("query_id") or "").strip() for row in query_rows
        ]
        query_rows_by_key: dict[str, list[str]] = {}
        for row, query_id in zip(query_rows, query_ids):
            key = str(row.get("canonical_key") or "").strip().casefold()
            query_rows_by_key.setdefault(key, []).append(query_id)
        query_partition_ok = bool(
            all(query_ids)
            and len(query_ids) == len(set(query_ids))
            and set(query_rows_by_key) == set(coverage_keys)
            and all(
                row.get("schema") == ACU_TITLE_PLAN.QUERY_SCHEMA
                for row in query_rows
            )
            and all(
                row.get("schema") == ACU_TITLE_PLAN.COVERAGE_SCHEMA
                and row.get("query_count")
                == len(query_rows_by_key.get(key, []))
                and sorted(row.get("query_ids") or [])
                == sorted(query_rows_by_key.get(key, []))
                for row, key in zip(coverage_rows, coverage_keys)
            )
            and all(
                row.get("schema") == ACU_TITLE_PLAN.UNRESOLVED_SCHEMA
                and not query_rows_by_key.get(key)
                for row, key in zip(unresolved_rows, unresolved_keys)
            )
        )
        manifest_contract_ok = plan.get("contracts") == contract
        absence_neutral = bool(
            contract.get("absence_is_neutral") is True
            and contract.get("absence_is_never_exclusion_evidence") is True
            and contract.get("absence_is_never_inclusion_evidence") is True
        )
        automatic_decisions_disabled = all(
            contract.get(key) is None
            for key in (
                "automatic_inclusion",
                "automatic_exclusion",
                "automatic_category_decision",
                "automatic_direction_decision",
                "sponsorship_role_assigned",
            )
        )
        official_relevance_only = bool(
            str(contract.get("official_relevance_authority") or "").startswith(
                "Congress.gov "
            )
            and contract.get("acu_evidence_cannot_override_official_relevance")
            is True
        )
        counts_ok = bool(
            plan.get("candidate_measure_count") == len(expected_keys)
            and plan.get("candidate_membership_count") == len(membership_rows)
            and plan.get("query_count") == len(query_rows)
            and plan.get("queryable_measure_count") == len(coverage_rows)
            and plan.get("unresolved_measure_count") == len(unresolved_rows)
        )
        unresolved_visible = bool(
            "unresolved" in outputs
            and len(unresolved_rows)
            == int(plan.get("unresolved_measure_count") or 0)
        )
        status = str(plan.get("status") or "").upper()
        status_consistent = bool(
            (status == "PASS" and not unresolved_rows)
            or (status == "BLOCKED" and bool(unresolved_rows))
        )
        structurally_valid = bool(
            plan.get("schema") == ACU_TITLE_PLAN.SCHEMA
            and plan.get("scope") == "all"
            and plan.get("stage") == "query_plan_only"
            and plan.get("network_requests_performed") == 0
            and plan.get("production_or_mainframe_written") is False
            and input_receipts_ok
            and output_receipts_ok
            and manifest_contract_ok
            and row_contracts_ok
            and title_sources_ok
            and domains_ok
            and query_partition_ok
            and absence_neutral
            and automatic_decisions_disabled
            and official_relevance_only
            and counts_ok
            and unresolved_visible
            and status_consistent
        )
        audit.update(
            {
                "status": status,
                "manifest_valid": structurally_valid,
                "input_receipts_valid": input_receipts_ok,
                "output_receipts_valid": output_receipts_ok,
                "candidate_measure_count": len(expected_keys),
                "queryable_measure_count": len(coverage_rows),
                "query_count": len(query_rows),
                "unresolved_measure_count": len(unresolved_rows),
                "unresolved_rows_visible": unresolved_visible,
                "unresolved_ledger": outputs["unresolved"],
                "all_candidate_measures_partitioned": (
                    set(coverage_keys) | set(unresolved_keys) == expected_keys
                ),
                "absence_is_neutral": absence_neutral,
                "automatic_decisions_disabled": (
                    automatic_decisions_disabled
                ),
                "official_relevance_congress_gov_only": (
                    official_relevance_only
                ),
                "sponsorship_roles_congress_gov_only": (
                    contract.get("sponsorship_role_authority")
                    == "Congress.gov relationship data only"
                ),
                "acceptance_passed": bool(
                    structurally_valid
                    and status == "PASS"
                    and not unresolved_rows
                ),
            }
        )
    except (OSError, ValueError, TypeError, AttributeError, PresyncError) as exc:
        audit["validation_error"] = str(exc)

    if audit["acceptance_passed"]:
        return audit, blockers
    if audit["manifest_valid"] and audit.get("unresolved_measure_count", 0) > 0:
        blockers.append(
            {
                "code": "acu_title_query_plan_unresolved",
                "unresolved_measure_count": audit["unresolved_measure_count"],
                "unresolved_ledger": audit["unresolved_ledger"],
                "absence_remains_neutral": True,
                "no_automatic_decision": True,
            }
        )
    else:
        blockers.append(
            {
                "code": "acu_title_query_plan_invalid",
                "path": str(plan_path),
                "validation_error": audit.get("validation_error"),
            }
        )
    return audit, blockers


def strict_manifest_exact_progress(
    manifest: dict[str, Any],
    *,
    registry_sha256: str,
    manifest_path: Path | None = None,
    artifact_cache: dict[Path, dict[str, Any] | None] | None = None,
) -> int | None:
    """Return distinct exact-query progress for a safe current-run artifact.

    Retry workers may publish a fresh empty manifest before they load an older
    checkpoint.  Such an artifact is valid evidence of zero progress, but it
    must never erase a registry-identical artifact that already proves more
    exact queries.
    """
    selected = manifest.get("selected_query_ids")
    queries = manifest.get("queries")
    if not (
        manifest.get("registry_sha256") == registry_sha256
        and manifest.get("primary_instrument") == PRIMARY_INSTRUMENT
        and manifest.get("quorum_used") is False
        and isinstance(selected, list)
        and len(selected) == EXPECTED_QUERY_COUNT
        and all(isinstance(query_id, str) and query_id for query_id in selected)
        and len(set(selected)) == EXPECTED_QUERY_COUNT
        and isinstance(queries, list)
    ):
        return None
    completed_ids: list[str] = []
    exact_ids: set[str] = set()
    selected_ids = set(selected)
    for row in queries:
        if not isinstance(row, dict):
            return None
        query_id = row.get("query_id")
        if (
            not isinstance(query_id, str)
            or not query_id
            or query_id not in selected_ids
        ):
            return None
        completed_ids.append(query_id)
        if query_is_exact(row):
            exact_ids.add(query_id)
    if len(completed_ids) != len(set(completed_ids)):
        return None
    if manifest_path is not None:
        run_dir = manifest_path.resolve().parent
        timeout = manifest.get("automatic_verification_timeout")
        if timeout is not None:
            if not isinstance(timeout, dict):
                return None
            raw_receipt = timeout.get("receipt_file")
            if not isinstance(raw_receipt, str) or not raw_receipt.strip():
                return None
            receipt_path = (run_dir / raw_receipt).resolve()
            if not (
                is_relative_to(receipt_path, run_dir)
                and receipt_path.is_file()
                and timeout.get("classification")
                == "automatic_verification_timeout"
                and timeout.get("interaction_performed") is False
                and timeout.get("captcha_bypass_attempted") is False
                and timeout.get("collection_stopped_without_result_acceptance")
                is True
                and str(timeout.get("receipt_sha256") or "").casefold()
                == sha256_file(receipt_path)
            ):
                return None

        cache = artifact_cache if artifact_cache is not None else {}
        for row in queries:
            raw_query_file = row.get("query_file")
            if (
                not isinstance(raw_query_file, str)
                or not raw_query_file.strip()
            ):
                return None
            query_path = (run_dir / raw_query_file).resolve()
            if not (
                is_relative_to(query_path, run_dir)
                and query_path.is_file()
            ):
                return None
            if query_path not in cache:
                try:
                    artifact = read_object(query_path)
                    records = artifact.get("records")
                    if not isinstance(records, list):
                        cache[query_path] = None
                    else:
                        record_keys = [
                            record.get("canonical_key")
                            if isinstance(record, dict)
                            else None
                            for record in records
                        ]
                        records_sha256 = sha256_bytes(
                            json.dumps(
                                records,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        )
                        cache[query_path] = {
                            "schema": artifact.get("schema"),
                            "query_id": artifact.get("query_id"),
                            "registry_sha256": artifact.get(
                                "registry_sha256"
                            ),
                            "result_total": artifact.get("result_total"),
                            "unique_record_count": artifact.get(
                                "unique_record_count"
                            ),
                            "page_count": artifact.get("page_count"),
                            "records_sha256": artifact.get("records_sha256"),
                            "computed_records_sha256": records_sha256,
                            "record_count": len(records),
                            "unique_key_count": len(set(record_keys)),
                            "record_keys_valid": all(
                                isinstance(key, str) and key
                                for key in record_keys
                            ),
                            "validation": artifact.get("validation"),
                        }
                except (OSError, PresyncError, TypeError, ValueError):
                    cache[query_path] = None
            summary = cache.get(query_path)
            if not isinstance(summary, dict):
                return None
            result_total = summary.get("result_total")
            if not (
                summary.get("schema")
                == "congress-advanced-search-query-v1"
                and summary.get("query_id") == row.get("query_id")
                and summary.get("registry_sha256") == registry_sha256
                and isinstance(result_total, int)
                and not isinstance(result_total, bool)
                and result_total >= 0
                and summary.get("record_count") == result_total
                and summary.get("unique_key_count") == result_total
                and summary.get("record_keys_valid") is True
                and summary.get("unique_record_count") == result_total
                and summary.get("result_total") == row.get("result_total")
                and summary.get("unique_record_count")
                == row.get("unique_record_count")
                and summary.get("page_count") == row.get("page_count")
                and summary.get("records_sha256")
                == summary.get("computed_records_sha256")
                == row.get("records_sha256")
                and query_is_exact(summary)
            ):
                return None
    return len(exact_ids)


def select_strict_manifest(
    config: dict[str, Any],
    *,
    workspace_root: Path,
    registry_path: Path | None,
    blockers: list[dict[str, Any]],
) -> Path | None:
    """Select maximum proven exact progress, then the freshest tied artifact."""
    raw_run = get_path(config, "coverage.strict_run")
    if raw_run is None:
        return resolve_input(
            get_path(config, "coverage.strict_manifest"),
            workspace_root=workspace_root,
            label="coverage.strict_manifest",
            blockers=blockers,
        )
    run_dir = resolve_input(
        raw_run,
        workspace_root=workspace_root,
        label="coverage.strict_run",
        blockers=blockers,
        kind="dir",
    )
    if not run_dir or not registry_path:
        if run_dir and not registry_path:
            add_blocker(blockers, "strict_run_waiting_for_registry")
        return None
    registry_hash = sha256_file(registry_path)
    candidates = [run_dir / "manifest.json"]
    candidates.extend(run_dir.glob("manifest.partial*.json"))
    matching: list[tuple[int, Path]] = []
    artifact_cache: dict[Path, dict[str, Any] | None] = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            manifest = read_object(candidate)
        except PresyncError:
            continue
        exact_progress = strict_manifest_exact_progress(
            manifest,
            registry_sha256=registry_hash,
            manifest_path=candidate,
            artifact_cache=artifact_cache,
        )
        if exact_progress is not None:
            matching.append((exact_progress, candidate))
    if not matching:
        add_blocker(
            blockers,
            "no_registry_matching_strict_manifest",
            strict_run=str(run_dir),
            registry_sha256=registry_hash,
        )
        return None
    _, selected = max(
        matching,
        key=lambda item: (
            item[0],
            item[1].stat().st_mtime_ns,
            item[1].name,
        ),
    )
    return selected


def coverage_gate(
    strict_path: Path | None,
    final_path: Path | None,
    registry_path: Path | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    audit: dict[str, Any] = {
        "strict_discovery_passed": False,
        "final_merged_passed": False,
    }
    strict_ok = False
    queries: list[Any] = []
    if strict_path and registry_path:
        strict = read_object(strict_path)
        registry_hash = sha256_file(registry_path)
        selected = list(strict.get("selected_query_ids") or [])
        queries = list(strict.get("queries") or [])
        completed = [
            row.get("query_id") for row in queries if isinstance(row, dict)
        ]
        strict_ok = bool(
            strict.get("status") == "complete"
            and strict.get("primary_instrument") == PRIMARY_INSTRUMENT
            and strict.get("quorum_used") is False
            and strict.get("registry_sha256") == registry_hash
            and len(selected) == len(set(selected)) == EXPECTED_QUERY_COUNT
            and len(queries) == EXPECTED_QUERY_COUNT
            and len(completed) == len(set(completed))
            and set(completed) == set(selected)
            and all(
                isinstance(row, dict) and query_is_exact(row) for row in queries
            )
        )
        audit["strict_manifest"] = file_receipt(strict_path)
        audit["registry"] = file_receipt(registry_path)
    elif registry_path:
        audit["registry"] = file_receipt(registry_path)
    audit["strict_discovery_passed"] = strict_ok
    audit["strict_query_count"] = len(queries)
    audit["strict_exact_query_count"] = sum(
        isinstance(row, dict) and query_is_exact(row) for row in queries
    )
    if not strict_ok:
        blockers.append(
            {
                "code": "strict_congress_advanced_coverage_incomplete",
                "expected_query_count": EXPECTED_QUERY_COUNT,
                "observed_query_count": len(queries),
                "exact_query_count": audit["strict_exact_query_count"],
            }
        )

    final_ok = False
    if not final_path:
        audit["final_manifest"] = None
    elif strict_path and strict_path.resolve() == final_path.resolve():
        final_ok = False
    else:
        final = read_object(final_path)
        final_status = str(final.get("status") or "").casefold()
        count = (
            final.get("record_count")
            or final.get("unique_record_count")
            or (final.get("union") or {}).get("unique_record_count")
            or (final.get("union") or {}).get("record_count")
        )
        gaps = (
            final.get("coverage_gap_count")
            if "coverage_gap_count" in final
            else final.get("unresolved_gap_count")
        )
        evidence = final.get("coverage_evidence")
        evidence_ok = isinstance(evidence, dict)
        if evidence_ok:
            for key in (
                "missed_candidates_ledger",
                "exclusion_ledger",
                "scope_and_exclusion_contract",
            ):
                item = evidence.get(key)
                if not isinstance(item, dict):
                    evidence_ok = False
                    break
                artifact = Path(str(item.get("path") or ""))
                if not artifact.is_absolute():
                    artifact = final_path.parent / artifact
                if not (
                    artifact.is_file()
                    and item.get("size") == artifact.stat().st_size
                    and str(item.get("sha256") or "").casefold()
                    == sha256_file(artifact)
                ):
                    evidence_ok = False
                    break
        evidence_ok = bool(
            evidence_ok
            and evidence.get("unresolved_gap_count") == 0
            and evidence.get("recovered_candidates_remain_visible") is True
            and evidence.get("semantic_exclusions_are_never_automatic") is True
        )
        final_ok = bool(
            final_status in {"complete", "pass", "passed"}
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 0
            and isinstance(gaps, int)
            and not isinstance(gaps, bool)
            and gaps == 0
            and evidence_ok
            and (explicit_pass(final) or final_status == "complete")
        )
        audit["final_record_count"] = count
        audit["final_unresolved_gap_count"] = gaps
        audit["final_coverage_evidence_passed"] = evidence_ok
    audit["final_merged_passed"] = final_ok
    if final_path:
        audit["final_manifest"] = file_receipt(final_path)
    if not final_ok:
        blockers.append(
            {
                "code": "final_historical_merged_coverage_incomplete",
                "strict_and_final_must_be_distinct": True,
            }
        )
    acu_title_plan, acu_title_blockers = acu_title_query_plan_gate(final_path)
    audit["acu_title_corroboration_query_plan"] = acu_title_plan
    audit["acu_title_query_plan_passed"] = acu_title_plan[
        "acceptance_passed"
    ]
    blockers.extend(acu_title_blockers)
    return audit, blockers


def process_liveness(pid: int) -> str:
    if pid <= 0:
        return "dead"
    if os.name == "nt":
        # os.kill(pid, 0) is not a dependable existence probe on Windows.
        # Querying a limited-information handle is read-only; access denied is
        # surfaced as unverifiable so a duplicate collector is never launched.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        access_denied = 5
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return (
                "unverifiable"
                if ctypes.get_last_error() == access_denied
                else "dead"
            )
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return "unverifiable"
            return "alive" if exit_code.value == still_active else "dead"
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return "alive"
    except PermissionError:
        return "unverifiable"
    except OSError:
        return "dead"


def process_alive(pid: int) -> bool:
    """Compatibility predicate; orchestration uses the tri-state function."""
    return process_liveness(pid) == "alive"


def coverage_resume_status(
    *,
    config: dict[str, Any],
    workspace_root: Path,
    out: Path,
    launch: bool,
) -> dict[str, Any]:
    settings = get_path(config, "coverage.resume")
    if not isinstance(settings, dict):
        return {"status": "not_configured", "captcha_bypass_attempted": False}
    raw_pid = settings.get("current_pid")
    if launch and not isinstance(raw_pid, int):
        raise PresyncError(
            "coverage resume launch requires an explicit current_pid "
            "(use 0 only after verifying no collector is running)"
        )
    pid = raw_pid if isinstance(raw_pid, int) else 0
    liveness = process_liveness(pid)
    if liveness == "alive":
        return {
            "status": "already_running",
            "pid": pid,
            "captcha_bypass_attempted": False,
        }
    if liveness == "unverifiable":
        return {
            "status": "process_liveness_unverifiable",
            "pid": pid,
            "launch_suppressed": True,
            "captcha_bypass_attempted": False,
        }
    # The launch receipt written by a previous run records the pid of the
    # collector that run started; config current_pid is static, so check it too.
    receipt_path = out / "coverage_resume.json"
    if receipt_path.is_file():
        try:
            previous = read_object(receipt_path)
        except PresyncError as exc:
            return {
                "status": "process_liveness_unverifiable",
                "pid": None,
                "launch_suppressed": True,
                "reason": f"prior launch receipt unreadable: {exc}",
                "captcha_bypass_attempted": False,
            }
        previous_pid = previous.get("pid")
        if isinstance(previous_pid, int) and not isinstance(previous_pid, bool):
            previous_liveness = process_liveness(previous_pid)
            if previous_liveness == "alive":
                return {
                    "status": "already_running",
                    "pid": previous_pid,
                    "captcha_bypass_attempted": False,
                }
            if previous_liveness == "unverifiable":
                return {
                    "status": "process_liveness_unverifiable",
                    "pid": previous_pid,
                    "launch_suppressed": True,
                    "captcha_bypass_attempted": False,
                }
    if not launch:
        return {
            "status": "launch_not_requested",
            "last_known_pid": pid or None,
            "captcha_bypass_attempted": False,
        }
    if settings.get("launch_when_stopped") is not True:
        raise PresyncError(
            "coverage resume launch requires launch_when_stopped=true"
        )
    script = Path(str(settings.get("script") or ""))
    if not script.is_absolute():
        script = workspace_root / script
    script = script.resolve()
    allowed = (
        workspace_root / "fec" / "run_legislation_specificity_followup.ps1"
    ).resolve()
    if script != allowed or not script.is_file():
        raise PresyncError(
            "coverage resume may only launch "
            "fec/run_legislation_specificity_followup.ps1"
        )
    delay = settings.get("retry_delay_seconds", 60)
    if not isinstance(delay, int) or not 5 <= delay <= 3600:
        raise PresyncError("coverage retry delay must be an integer from 5 to 3600")
    command = [
        str(
            Path(
                os.environ.get(
                    "SystemRoot", r"C:\Windows"
                )
            )
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        ),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-RetryDelaySeconds",
        str(delay),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    process = subprocess.Popen(
        command,
        cwd=workspace_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    receipt = {
        "status": "launched",
        "pid": process.pid,
        "command": command,
        "command_sha256": sha256_bytes(canonical_bytes(command)),
        "captcha_bypass_attempted": False,
        "browser_interaction_performed": False,
    }
    atomic_json(out / "coverage_resume.json", receipt)
    return receipt


def stage_fingerprint(
    command: Sequence[str], input_paths: Iterable[Path], producer: Path
) -> tuple[str, list[dict[str, Any]]]:
    receipts = [file_receipt(path) for path in sorted(set(input_paths))]
    value = {
        "command": list(command),
        "inputs": receipts,
        "producer": file_receipt(producer),
    }
    return sha256_bytes(canonical_bytes(value)), receipts


def _recursive_files(path: Path) -> list[Path]:
    resolved = path.resolve()
    if resolved.is_file():
        return [resolved]
    if not resolved.is_dir():
        return []
    return sorted(
        item.resolve()
        for item in resolved.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts
    )


def shadow_transitive_fingerprint_inputs(
    *,
    workspace_root: Path,
    explicit_inputs: Iterable[Path],
    accepted_generation: Path,
    direction_dir: Path,
    base_dir: Path,
    rollcall_dir: Path,
    baseline_manifest: Path,
) -> list[Path]:
    """Return the complete code/data closure that can affect a shadow PASS.

    This deliberately favors invalidating a reusable stage over allowing a
    stale acceptance receipt.  Every staged producer artifact, immutable
    background input, baseline copy, and executable source under ``fec`` is
    hash-bound into the presync stage fingerprint.
    """
    files: set[Path] = {
        Path(path).resolve() for path in explicit_inputs if Path(path).is_file()
    }
    for directory in (
        accepted_generation,
        direction_dir,
        base_dir,
        rollcall_dir,
    ):
        files.update(_recursive_files(directory))

    baseline_value = read_object(baseline_manifest)
    for row in baseline_value.get("files") or []:
        if not isinstance(row, dict):
            continue
        copy_path = Path(str(row.get("copy") or ""))
        if not copy_path.is_absolute():
            copy_path = baseline_manifest.parent / copy_path
        if not copy_path.is_file():
            raise PresyncError(
                f"shadow baseline closure file is missing: {copy_path}"
            )
        files.add(copy_path.resolve())

    for contract in SHADOW_RUNNER.IMMUTABLE_INPUT_CONTRACTS:
        source = (workspace_root / contract.path).resolve()
        if source.is_file():
            files.add(source)
        elif contract.required:
            raise PresyncError(
                f"shadow immutable input closure is missing: {contract.path}"
            )

    fec_root = (workspace_root / "fec").resolve()
    for source in fec_root.rglob("*"):
        if (
            source.is_file()
            and "__pycache__" not in source.parts
            and source.suffix.casefold()
            in {".py", ".cjs", ".mjs", ".js", ".ps1"}
        ):
            files.add(source.resolve())
    return sorted(files)


def locate_manifest(out_dir: Path, names: Sequence[str]) -> Path | None:
    for name in names:
        candidate = out_dir / name
        if candidate.is_file():
            return candidate
    return None


def run_stage(
    *,
    name: str,
    argv: list[str],
    inputs: list[Path],
    out_dir: Path,
    state_dir: Path,
    python_executable: Path,
    workspace_root: Path,
    resume: bool,
    timeout_seconds: int,
    allow_existing_output: bool = False,
) -> dict[str, Any]:
    spec = STAGE_SPECS[name]
    producer = (workspace_root / "fec" / spec.script).resolve()
    if not producer.is_file():
        raise PresyncError(f"staged producer is missing: {producer}")
    command = [str(python_executable), str(producer), *argv]
    fingerprint, receipts = stage_fingerprint(command, inputs, producer)
    state_path = state_dir / f"{name}.json"
    prior: dict[str, Any] | None = None
    if resume and state_path.is_file():
        prior = read_object(state_path)
        prior_manifest = Path(str(prior.get("manifest", {}).get("path") or ""))
        if (
            prior.get("schema") == STAGE_STATE_SCHEMA
            and prior.get("fingerprint") == fingerprint
            and prior_manifest.is_file()
            and prior.get("manifest", {}).get("sha256")
            == sha256_file(prior_manifest)
        ):
            return {**prior, "reused": True}
    superseded_output: Path | None = None
    if resume and out_dir.exists() and prior is not None:
        prior_fingerprint = str(prior.get("fingerprint") or "")
        if prior_fingerprint and prior_fingerprint != fingerprint:
            superseded_output = out_dir.with_name(
                f"{out_dir.name}.superseded.{prior_fingerprint[:12]}"
            )
            if superseded_output.exists():
                raise PresyncError(
                    "changed stage has an existing superseded output: "
                    f"{superseded_output}"
                )
            if (
                not out_dir.resolve().is_relative_to(
                    (workspace_root / "audits" / "legfix5").resolve()
                )
                or not superseded_output.resolve().is_relative_to(
                    (workspace_root / "audits" / "legfix5").resolve()
                )
            ):
                raise PresyncError(
                    "refusing to supersede a stage outside audits/legfix5"
                )
            out_dir.rename(superseded_output)
    if out_dir.exists() and not allow_existing_output:
        raise PresyncError(
            f"stage output already exists without a reusable receipt: {out_dir}"
        )
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        cwd=workspace_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    stdout_path = state_dir / f"{name}.stdout.txt"
    stderr_path = state_dir / f"{name}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    manifest_path = locate_manifest(out_dir, spec.manifest_names)
    if manifest_path is None:
        raise PresyncError(
            f"stage {name} emitted no expected manifest; exit={completed.returncode}"
        )
    if (
        completed.returncode != 0
        and completed.returncode not in spec.blocking_exit_codes
    ):
        raise PresyncError(
            f"stage {name} failed with exit code {completed.returncode}"
        )
    state = {
        "schema": STAGE_STATE_SCHEMA,
        "stage": name,
        "fingerprint": fingerprint,
        "command": command,
        "command_sha256": sha256_bytes(canonical_bytes(command)),
        "inputs": receipts,
        "exit_code": completed.returncode,
        "expected_blocking_exit": completed.returncode in spec.blocking_exit_codes,
        "manifest": file_receipt(manifest_path),
        "stdout": file_receipt(stdout_path),
        "stderr": file_receipt(stderr_path),
        "reused": False,
        "production_command_executed": False,
        "superseded_output": (
            str(superseded_output) if superseded_output else None
        ),
    }
    atomic_json(state_path, state)
    return state


def manifest_for(state: dict[str, Any]) -> dict[str, Any]:
    return read_object(Path(state["manifest"]["path"]))


def _receipt_path_current(
    value: Any, *, relative_to: Path | None = None
) -> Path | None:
    if not isinstance(value, dict):
        return None
    raw_path = str(value.get("path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    path = path.resolve()
    if not (
        path.is_file()
        and value.get("size") == path.stat().st_size
        and str(value.get("sha256") or "").casefold() == sha256_file(path)
    ):
        return None
    return path


def _run_manifest_graph_passes(value: Mapping[str, Any], path: Path) -> bool:
    if not (
        value.get("schema") == "legislation-shadow-isolated-run-v1"
        and value.get("production_read_only") is True
        and isinstance(value.get("outputs"), dict)
        and value.get("outputs")
        and isinstance(value.get("steps"), list)
        and value.get("steps")
    ):
        return False
    root = path.parent
    if _receipt_path_current(value.get("step_ledger"), relative_to=root) is None:
        return False
    for key, item in value["outputs"].items():
        if not isinstance(item, dict):
            return False
        output = (root / str(key)).resolve()
        if not (
            output.is_relative_to(root.resolve())
            and output.is_file()
            and item.get("size") == output.stat().st_size
            and str(item.get("byte_sha256") or "").casefold()
            == sha256_file(output)
        ):
            return False
    for step in value["steps"]:
        if not isinstance(step, dict):
            return False
        if (
            _receipt_path_current(step.get("stdout"), relative_to=root) is None
            or _receipt_path_current(step.get("stderr"), relative_to=root) is None
        ):
            return False
        for item in step.get("outputs") or []:
            if not isinstance(item, dict):
                return False
            output = (root / str(item.get("path") or "")).resolve()
            if not (
                output.is_relative_to(root.resolve())
                and output.is_file()
                and item.get("size") == output.stat().st_size
                and str(item.get("byte_sha256") or "").casefold()
                == sha256_file(output)
            ):
                return False
    return True


def shadow_acceptance_passed(state: dict[str, Any]) -> bool:
    """Deep-validate the complete PASS receipt graph before certification."""
    manifest_path = Path(str(state.get("manifest", {}).get("path") or ""))
    if manifest_path.name != "staged_acceptance_receipt.json":
        return False
    try:
        if _receipt_path_current(state.get("manifest")) != manifest_path.resolve():
            return False
        if any(_receipt_path_current(item) is None for item in state.get("inputs") or []):
            return False
        manifest = read_object(manifest_path)
        if not (
            manifest.get("schema") == SHADOW_RUNNER.RECEIPT_SCHEMA
            and manifest.get("status") == "PASS"
            and manifest.get("acceptance_passed") is True
            and manifest.get("idempotence_passed") is True
            and manifest.get("value_idempotence_passed") is True
            and manifest.get("byte_idempotence_passed") is True
            and manifest.get("old_new_delta_audit_passed") is True
            and manifest.get("production_baseline_immutable") is True
            and manifest.get("production_promotion_performed") is False
        ):
            return False
        audit_path = _receipt_path_current(manifest.get("audit"))
        if audit_path is None:
            return False
        audit = read_object(audit_path)
        if not (
            audit.get("schema") == SHADOW_RUNNER.AUDIT_SCHEMA
            and audit.get("status") == "PASS"
            and audit.get("acceptance_passed") is True
            and audit.get("production_read_only") is True
            and isinstance(audit.get("gates"), dict)
            and audit["gates"]
            and all(value is True for value in audit["gates"].values())
        ):
            return False
        for key in (
            "baseline_manifest",
            "consumer_plan",
            "idempotence_audit",
            "old_new_attribution",
        ):
            if _receipt_path_current(audit.get(key)) is None:
                return False
        run_paths: dict[str, Path] = {}
        run_output_counts: dict[str, int] = {}
        for key, receipt_value in (
            ("run_1", audit.get("run_1_manifest")),
            ("run_2", audit.get("run_2_manifest")),
        ):
            run_path = _receipt_path_current(receipt_value)
            if run_path is None:
                return False
            run_value = read_object(run_path)
            if not _run_manifest_graph_passes(run_value, run_path):
                return False
            run_paths[key] = run_path
            run_output_counts[key] = len(run_value["outputs"])
        if not (
            run_output_counts
            == {
                "run_1": EXPECTED_SHADOW_OUTPUT_COUNT,
                "run_2": EXPECTED_SHADOW_OUTPUT_COUNT,
            }
            and audit.get("run_1_output_count")
            == EXPECTED_SHADOW_OUTPUT_COUNT
            and audit.get("run_2_output_count")
            == EXPECTED_SHADOW_OUTPUT_COUNT
        ):
            return False
        if manifest.get("run_manifest_hashes") != {
            key: sha256_file(path) for key, path in run_paths.items()
        }:
            return False
        if manifest.get("baseline_manifest_sha256") != str(
            audit["baseline_manifest"]["sha256"]
        ):
            return False
        if manifest.get("consumer_plan_sha256") != str(
            audit["consumer_plan"]["sha256"]
        ):
            return False
        if manifest.get("idempotence_audit_sha256") != str(
            audit["idempotence_audit"]["sha256"]
        ):
            return False
        if manifest.get("old_new_attribution_sha256") != str(
            audit["old_new_attribution"]["sha256"]
        ):
            return False
        for group in ("coverage", "producers"):
            values = audit.get(group)
            if not isinstance(values, dict) or not values:
                return False
            for item in values.values():
                if not isinstance(item, dict):
                    return False
                receipt_value = item.get("manifest")
                if _receipt_path_current(receipt_value) is None:
                    return False
                for output in (item.get("outputs") or {}).values():
                    if _receipt_path_current(output) is None:
                        return False
        return True
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        PresyncError,
        json.JSONDecodeError,
    ):
        return False


def add_blocker(
    blockers: list[dict[str, Any]], code: str, **details: Any
) -> None:
    row = {"code": code, **details}
    if row not in blockers:
        blockers.append(row)


def existing_inputs(
    paths: Iterable[Path | None],
) -> list[Path]:
    return [path for path in paths if path is not None]


def validate_role_approval(
    config: dict[str, Any],
    workspace_root: Path,
    accepted_state: dict[str, Any],
    blockers: list[dict[str, Any]],
) -> tuple[Path | None, Path | None]:
    receipt_path = resolve_input(
        get_path(config, "shadow.role_weight_policy_receipt"),
        workspace_root=workspace_root,
        label="shadow.role_weight_policy_receipt",
        blockers=blockers,
    )
    approved_dir = resolve_input(
        get_path(config, "shadow.approved_accepted_generation"),
        workspace_root=workspace_root,
        label="shadow.approved_accepted_generation",
        blockers=blockers,
        kind="dir",
    )
    if not receipt_path or not approved_dir:
        add_blocker(blockers, "role_weight_policy_unresolved")
        return None, receipt_path
    receipt = read_object(receipt_path)
    approved_manifest_path = approved_dir / "manifest.json"
    if not approved_manifest_path.is_file():
        add_blocker(
            blockers,
            "approved_accepted_generation_manifest_missing",
            path=str(approved_manifest_path),
        )
        return None, receipt_path
    approved = read_object(approved_manifest_path)
    generated = manifest_for(accepted_state)
    generated_ledger = (
        Path(accepted_state["manifest"]["path"]).parent
        / str(generated.get("canonical_ledger_file") or "")
    )
    selected = approved.get("selected_role_weight_policy")
    valid = bool(
        receipt.get("schema") == APPROVED_GENERATION.ROLE_APPROVAL_SCHEMA
        and receipt.get("status") == "APPROVED"
        and receipt.get("human_approved") is True
        and receipt.get("approved_by_user") is True
        and isinstance(selected, str)
        and selected
        and receipt.get("selected_role_weight_policy") == selected
        and selected == "workbook_legacy_4_2_1"
        and receipt.get("selected_method_profile")
        == APPROVED_GENERATION.ROLE_METHOD_PROFILE_ID
        and receipt.get("selected_policy")
        == APPROVED_GENERATION.ROLE_METHOD_PROFILE_ID
        and receipt.get("consumer_methods")
        == APPROVED_GENERATION.ROLE_METHOD_PROFILE
        and receipt.get("applies_to")
        == list(APPROVED_GENERATION.ROLE_METHOD_PROFILE)
        and approved.get("role_weight_policy_reconciled") is True
        and approved.get("selected_method_profile")
        == APPROVED_GENERATION.ROLE_METHOD_PROFILE_ID
        and approved.get("consumer_role_methods")
        == APPROVED_GENERATION.ROLE_METHOD_PROFILE
        and approved.get("promotion_ready") is True
        and approved.get("canonical_ledger_sha256") == sha256_file(generated_ledger)
        and receipt.get("accepted_ledger_sha256") == sha256_file(generated_ledger)
    )
    if not valid:
        add_blocker(blockers, "role_weight_policy_approval_invalid")
        return None, receipt_path
    return approved_dir, receipt_path


def orchestrate(args: argparse.Namespace) -> dict[str, Any]:
    workspace_root = args.workspace_root.resolve()
    out = safe_output(args.out, workspace_root)
    config_path = args.config.resolve()
    config = read_object(config_path)
    if config.get("schema") != SCHEMA:
        raise PresyncError(f"config schema must be {SCHEMA!r}")
    if out.exists() and not args.resume:
        raise PresyncError("existing --out requires --resume")
    out.mkdir(parents=True, exist_ok=True)
    state_dir = out / "state"
    stages_root = out / "stages"
    state_dir.mkdir(parents=True, exist_ok=True)
    blockers: list[dict[str, Any]] = []
    stages: dict[str, Any] = {}
    _, downstream_drift_observation = (
        validate_post_weekly_refresh_downstream_drift(
            workspace_root
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_downstream_drift"
            / "current.json"
        )
    )
    _, behavior_stance_semantic_observation = (
        validate_post_weekly_refresh_behavior_stance_semantics(
            workspace_root
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_behavior_stance_semantics"
            / "current.json"
        )
    )
    _, planning_dashboard_semantic_observation = (
        validate_post_weekly_refresh_planning_dashboard_semantics(
            workspace_root
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_planning_dashboard_semantics"
            / "current.json"
        )
    )
    _, workbook_ooxml_semantic_observation = (
        validate_post_weekly_refresh_workbook_ooxml_semantics(
            workspace_root
            / "audits"
            / "legfix5"
            / "post_weekly_refresh_workbook_ooxml_semantics"
            / "current.json"
        )
    )
    _, formula_cache_freshness_observation = (
        validate_formula_cache_freshness_observation(
            workspace_root
            / "audits"
            / "legfix5"
            / "formula_cache_freshness"
            / "current.json"
        )
    )

    decay_receipt = resolve_input(
        get_path(config, "shadow.decay_propagation_audit"),
        workspace_root=workspace_root,
        label="shadow.decay_propagation_audit",
        blockers=blockers,
    )
    decay_ok, decay_gate = validate_decay_propagation_audit(
        decay_receipt,
        workspace_root=workspace_root,
    )
    if not decay_ok:
        add_blocker(
            blockers,
            "decay_propagation_audit_missing_tampered_or_stale",
            issues=decay_gate["issues"],
        )

    registry = resolve_input(
        get_path(config, "coverage.registry"),
        workspace_root=workspace_root,
        label="coverage.registry",
        blockers=blockers,
    )
    strict = select_strict_manifest(
        config,
        workspace_root=workspace_root,
        registry_path=registry,
        blockers=blockers,
    )
    final = resolve_input(
        get_path(config, "coverage.final_manifest"),
        workspace_root=workspace_root,
        label="coverage.final_manifest",
        blockers=blockers,
    )
    coverage, coverage_blockers = coverage_gate(strict, final, registry)
    blockers.extend(coverage_blockers)
    coverage["resume"] = coverage_resume_status(
        config=config,
        workspace_root=workspace_root,
        out=out,
        launch=bool(args.resume_coverage and not coverage["strict_discovery_passed"]),
    )
    if coverage["resume"]["status"] == "process_liveness_unverifiable":
        add_blocker(
            blockers,
            "process_liveness_unverifiable",
            pid=coverage["resume"]["pid"],
            duplicate_collector_launch_suppressed=True,
        )
    discovery_records = (
        strict.parent / "congress_advanced_records.ndjson" if strict else None
    )
    discovery_memberships = (
        strict.parent / "congress_advanced_memberships.ndjson"
        if strict
        else None
    )
    discovery_exclusions = (
        strict.parent / "exclusion_ledger.ndjson" if strict else None
    )
    reviewed_exclusions = (
        strict.parent
        / "candidate_membership_review"
        / "false_positive_exclusions.ndjson"
        if strict
        else None
    )
    membership_review = (
        strict.parent
        / "candidate_membership_review"
        / "membership_review_ledger.ndjson"
        if strict
        else None
    )
    coverage_gaps = (
        strict.parent / "coverage_gap_ledger.ndjson" if strict else None
    )
    cumulative_manifest = strict.parent / "manifest.json" if strict else None
    raw_bundle_root = get_path(config, "coverage.billstatus_snapshot_root")
    bundle_root: Path | None = None
    if isinstance(raw_bundle_root, str) and raw_bundle_root.strip():
        bundle_root = Path(raw_bundle_root)
        if not bundle_root.is_absolute():
            bundle_root = workspace_root / bundle_root
        bundle_root = bundle_root.resolve()
        allowed_bundle_root = (
            workspace_root / "audits" / "legfix5"
        ).resolve()
        if not (
            bundle_root != allowed_bundle_root
            and is_relative_to(bundle_root, allowed_bundle_root)
        ):
            add_blocker(
                blockers,
                "billstatus_snapshot_root_outside_audits",
                path=str(bundle_root),
            )
            bundle_root = None
    else:
        add_blocker(
            blockers,
            "missing_explicit_input",
            input="coverage.billstatus_snapshot_root",
        )
    bundle_state: dict[str, Any] = {
        "status": "MISSING",
        "created": False,
        "issues": [],
    }
    billstatus_plan: Path | None = None
    billstatus_divergence_path: Path | None = None
    if (
        bundle_root
        and strict
        and discovery_records
        and discovery_memberships
        and discovery_exclusions
        and reviewed_exclusions
        and membership_review
        and coverage_gaps
        and cumulative_manifest
        and registry
    ):
        try:
            refreshed = BILLSTATUS_REFRESH.ensure_snapshot_bundle(
                snapshot_root=bundle_root,
                strict_manifest_path=strict,
                records_path=discovery_records,
                memberships_path=discovery_memberships,
                exclusion_ledger_path=discovery_exclusions,
                exclusion_provenance_paths=(reviewed_exclusions,),
                cumulative_manifest_path=cumulative_manifest,
                registry_path=registry,
                review_ledger_path=membership_review,
                gap_ledger_path=coverage_gaps,
                enforce_workspace_audits=False,
            )
            billstatus_plan = Path(refreshed["snapshot_plan"])
            billstatus_divergence_path = Path(
                refreshed["divergence_audit"]
            )
            bundle_state = {
                **refreshed,
                "status": "CURRENT",
                "issues": [],
            }
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            BILLSTATUS_REFRESH.RefreshError,
            BILLSTATUS_ENRICHMENT.ContractError,
            BILLSTATUS_DIVERGENCE.AuditError,
        ) as error:
            bundle_state = {
                "status": "STALE_OR_INVALID",
                "created": False,
                "issues": [str(error)],
            }
            add_blocker(
                blockers,
                "billstatus_snapshot_bundle_refresh_failed_closed",
                error=str(error),
            )
    coverage["billstatus_snapshot_bundle"] = bundle_state
    billstatus_ok, billstatus_gate = validate_billstatus_snapshot_plan(
        billstatus_plan,
        strict_manifest_path=strict,
        discovery_records_path=discovery_records,
        exclusion_ledger_path=discovery_exclusions,
        exclusion_provenance_paths=(
            (reviewed_exclusions,) if reviewed_exclusions else ()
        ),
    )
    coverage["billstatus_snapshot_evidence"] = billstatus_gate
    if not billstatus_ok:
        add_blocker(
            blockers,
            "billstatus_snapshot_plan_missing_tampered_or_stale",
            issues=billstatus_gate["issues"],
        )
    billstatus_divergence_ok, billstatus_divergence = (
        validate_billstatus_divergence_audit(
            billstatus_divergence_path,
            strict_manifest_path=strict,
            discovery_records_path=discovery_records,
            memberships_path=discovery_memberships,
        )
    )
    coverage["billstatus_snapshot_divergence_evidence"] = (
        billstatus_divergence
    )
    if not billstatus_divergence_ok:
        add_blocker(
            blockers,
            "billstatus_divergence_audit_missing_tampered_or_stale",
            issues=billstatus_divergence["issues"],
        )
    billstatus_fetch_root = resolve_input(
        get_path(config, "coverage.billstatus_fetch_root"),
        workspace_root=workspace_root,
        label="coverage.billstatus_fetch_root",
        blockers=blockers,
        kind="dir",
    )
    billstatus_completeness_root = resolve_input(
        get_path(config, "coverage.billstatus_completeness_root"),
        workspace_root=workspace_root,
        label="coverage.billstatus_completeness_root",
        blockers=blockers,
        kind="dir",
    )
    (
        billstatus_worker_manifest,
        billstatus_completeness_path,
    ) = billstatus_enrichment_evidence_paths(
        snapshot_plan_path=billstatus_plan,
        fetch_root=billstatus_fetch_root,
        completeness_root=billstatus_completeness_root,
    )
    billstatus_completeness_ok, billstatus_completeness = (
        validate_billstatus_enrichment_completeness(
            billstatus_completeness_path,
            expected_plan_path=billstatus_plan,
            expected_worker_manifest_path=billstatus_worker_manifest,
        )
    )
    coverage["billstatus_enrichment_completeness_evidence"] = (
        billstatus_completeness
    )
    if not billstatus_completeness_ok:
        add_blocker(
            blockers,
            "billstatus_enrichment_completeness_missing_tampered_or_stale",
            issues=billstatus_completeness["issues"],
        )
    query_specificity_path = resolve_input(
        get_path(config, "coverage.query_specificity_audit"),
        workspace_root=workspace_root,
        label="coverage.query_specificity_audit",
        blockers=blockers,
    )
    query_specificity_ok, query_specificity = (
        validate_query_specificity_audit(
            query_specificity_path,
            registry_path=registry,
            strict_manifest_path=strict,
        )
    )
    coverage["query_specificity_evidence"] = query_specificity
    if not query_specificity_ok:
        add_blocker(
            blockers,
            "query_specificity_audit_missing_tampered_or_stale",
            issues=query_specificity["issues"],
        )

    common: dict[str, Path | None] = {}
    for key, kind in (
        ("membership.relationships", "dir"),
        ("membership.text_relevance", "dir"),
        ("membership.article_cross_reference", "dir"),
        ("membership.review_ledger", "file"),
        ("membership.reconciliation_report", "file"),
        ("membership.ratified_review_manifest", "file"),
    ):
        common[key] = resolve_input(
            get_path(config, key),
            workspace_root=workspace_root,
            label=key,
            blockers=blockers,
            kind=kind,
        )
    directory_manifests: dict[str, Path | None] = {}
    for key in (
        "membership.relationships",
        "membership.text_relevance",
        "membership.article_cross_reference",
    ):
        directory = common[key]
        candidate = directory / "manifest.json" if directory else None
        if candidate and candidate.is_file():
            directory_manifests[key] = candidate
        else:
            directory_manifests[key] = None
            if directory:
                add_blocker(
                    blockers,
                    "required_source_manifest_missing",
                    input=key,
                    path=str(candidate),
                )
    if not (
        coverage["strict_discovery_passed"]
        and coverage["final_merged_passed"]
        and query_specificity_ok
    ):
        add_blocker(blockers, "membership_stages_waiting_for_coverage")

    python_executable = Path(args.python_executable).resolve()
    if not python_executable.is_file():
        raise PresyncError(f"Python executable does not exist: {python_executable}")
    timeout = args.stage_timeout_seconds
    final_run = final.parent if final else None
    common_ready = bool(
        coverage["strict_discovery_passed"]
        and coverage["final_merged_passed"]
        and query_specificity_ok
        and final_run
        and registry
        and directory_manifests["membership.relationships"]
        and directory_manifests["membership.text_relevance"]
        and directory_manifests["membership.article_cross_reference"]
        and common["membership.review_ledger"]
    )
    if common_ready and not args.check_only:
        triage_out = stages_root / "membership_triage"
        triage_argv = [
            "--run",
            str(final_run),
            "--registry",
            str(registry),
            "--relationships",
            str(common["membership.relationships"]),
            "--text-relevance",
            str(common["membership.text_relevance"]),
            "--article-cross-reference",
            str(common["membership.article_cross_reference"]),
            "--review-ledger",
            str(common["membership.review_ledger"]),
            "--out",
            str(triage_out),
        ]
        stages["membership_triage"] = run_stage(
            name="membership_triage",
            argv=triage_argv,
            inputs=existing_inputs(
                [
                    final,
                    registry,
                    directory_manifests["membership.relationships"],
                    directory_manifests["membership.text_relevance"],
                    directory_manifests["membership.article_cross_reference"],
                    common["membership.review_ledger"],
                ]
            ),
            out_dir=triage_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        triage_manifest = manifest_for(stages["membership_triage"])
        if not (
            triage_manifest.get("staging_only") is True
            and triage_manifest.get("acceptance_for_scoring") is False
            and triage_manifest.get("reviewer_ratification_required") is True
        ):
            raise PresyncError("membership triage safety contract failed")

        batches_out = stages_root / "review_batches"
        # This producer has its own deterministic shard/checkpoint resume
        # contract.  Start it in resume mode even on the first invocation so a
        # killed process can continue with an identical command fingerprint.
        batches_out.mkdir(parents=True, exist_ok=True)
        batches_argv = [
            "--run",
            str(final_run),
            "--registry",
            str(registry),
            "--relationships",
            str(common["membership.relationships"]),
            "--text-relevance",
            str(common["membership.text_relevance"]),
            "--article-cross-reference",
            str(common["membership.article_cross_reference"]),
            "--review-ledger",
            str(common["membership.review_ledger"]),
            "--out",
            str(batches_out),
            "--batch-size",
            str(args.batch_size),
            "--resume",
        ]
        stages["review_batches"] = run_stage(
            name="review_batches",
            argv=batches_argv,
            inputs=existing_inputs(
                [
                    final,
                    registry,
                    directory_manifests["membership.relationships"],
                    directory_manifests["membership.text_relevance"],
                    directory_manifests["membership.article_cross_reference"],
                    common["membership.review_ledger"],
                ]
            ),
            out_dir=batches_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
            allow_existing_output=True,
        )
        batches_manifest = manifest_for(stages["review_batches"])
        if not (
            batches_manifest.get("complete") is True
            and batches_manifest.get("acceptance_for_scoring") is False
            and batches_manifest.get("human_review_and_ratification_required")
            is True
        ):
            raise PresyncError("review batch safety contract failed")

    ratified_manifest_path = common["membership.ratified_review_manifest"]
    ratified_ok = False
    if ratified_manifest_path:
        ratified_manifest = read_object(ratified_manifest_path)
        ratified_ok = bool(
            ratified_manifest.get("promotion_gate_passed") is True
            and ratified_manifest.get("unresolved_count") == 0
        )
    if not ratified_ok:
        add_blocker(
            blockers,
            "human_membership_review_not_ratified",
            machine_proposals_are_not_decisions=True,
        )

    relationships_manifest = directory_manifests["membership.relationships"]
    accepted_ready = bool(
        common_ready
        and ratified_ok
        and common["membership.reconciliation_report"]
        and relationships_manifest
        and relationships_manifest.is_file()
    )
    accepted_state: dict[str, Any] | None = None
    accepted_ledger: Path | None = None
    if accepted_ready and not args.check_only:
        accepted_out = stages_root / "accepted_generation"
        accepted_state = run_stage(
            name="accepted_generation",
            argv=[
                "--run-manifest",
                str(final),
                "--reconciliation-report",
                str(common["membership.reconciliation_report"]),
                "--review-manifest",
                str(ratified_manifest_path),
                "--relationships-manifest",
                str(relationships_manifest),
                "--out",
                str(accepted_out),
            ],
            inputs=existing_inputs(
                [
                    final,
                    common["membership.reconciliation_report"],
                    ratified_manifest_path,
                    relationships_manifest,
                ]
            ),
            out_dir=accepted_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["accepted_generation"] = accepted_state
        accepted_manifest = manifest_for(accepted_state)
        if not (
            accepted_manifest.get("status") == "complete"
            and accepted_manifest.get("upstream_acceptance_passed") is True
            and accepted_manifest.get("staged_only") is True
        ):
            raise PresyncError("accepted generation did not pass its upstream gates")
        accepted_ledger = accepted_out / str(
            accepted_manifest.get("canonical_ledger_file") or ""
        )
        if not accepted_ledger.is_file():
            raise PresyncError("accepted generation ledger is missing")

    direction_state: dict[str, Any] | None = None
    direction_ledger: Path | None = None
    direction_sources: list[tuple[str, Path]] = []
    for key, flag in (
        ("direction.bill_directions", "--bill-directions"),
        ("direction.archive_stance_evidence", "--archive-stance-evidence"),
        ("direction.press_cross_reference", "--press-cross-reference"),
    ):
        raw = get_path(config, key)
        if raw is not None:
            path = resolve_input(
                raw,
                workspace_root=workspace_root,
                label=key,
                blockers=blockers,
            )
            if path:
                direction_sources.append((flag, path))
    if not direction_sources:
        add_blocker(blockers, "explicit_direction_evidence_missing")
    direction_ok = False
    if accepted_ledger and direction_sources and not args.check_only:
        direction_out = stages_root / "direction_ledger"
        direction_argv = [
            "--accepted",
            str(accepted_ledger),
            "--out",
            str(direction_out),
        ]
        for flag, path in direction_sources:
            direction_argv.extend([flag, str(path)])
        direction_state = run_stage(
            name="direction_ledger",
            argv=direction_argv,
            inputs=[accepted_ledger, *(path for _, path in direction_sources)],
            out_dir=direction_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["direction_ledger"] = direction_state
        direction_manifest = manifest_for(direction_state)
        direction_ok = direction_manifest.get("acceptance_passed") is True
        direction_ledger = direction_out / "scoring_directions.ndjson"
        if not direction_ok:
            add_blocker(
                blockers,
                "direction_evidence_incomplete_or_conflicting",
                manifest=direction_state["manifest"],
            )

    base_ok = False
    base_state: dict[str, Any] | None = None
    if accepted_ledger and not args.check_only:
        base_out = stages_root / "base_network"
        base_argv = [
            "--accepted-ledger",
            str(accepted_ledger),
            "--out",
            str(base_out),
        ]
        base_inputs = [accepted_ledger]
        for key, flag in (
            ("network.roster", "--roster"),
            ("network.compare_old_workbook", "--compare-old-workbook"),
        ):
            raw = get_path(config, key)
            if raw is not None:
                path = resolve_input(
                    raw,
                    workspace_root=workspace_root,
                    label=key,
                    blockers=blockers,
                )
                if path:
                    base_argv.extend([flag, str(path)])
                    base_inputs.append(path)
        base_state = run_stage(
            name="base_network",
            argv=base_argv,
            inputs=base_inputs,
            out_dir=base_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["base_network"] = base_state
        base_ok = manifest_for(base_state).get("acceptance_passed") is True
        if not base_ok:
            add_blocker(blockers, "base_network_acceptance_failed")

    scenarios_ok = False
    scenarios_state: dict[str, Any] | None = None
    if accepted_ledger and direction_ok and direction_ledger and not args.check_only:
        scenarios_out = stages_root / "policy_scenarios"
        scenario_argv = [
            "--accepted-ledger",
            str(accepted_ledger),
            "--scoring-directions",
            str(direction_ledger),
            "--out",
            str(scenarios_out),
        ]
        scenario_inputs = [accepted_ledger, direction_ledger]
        baseline_raw = get_path(config, "direction.baseline_scenario")
        if baseline_raw is not None:
            baseline = resolve_input(
                baseline_raw,
                workspace_root=workspace_root,
                label="direction.baseline_scenario",
                blockers=blockers,
            )
            if baseline:
                scenario_argv.extend(["--baseline-scenario", str(baseline)])
                scenario_inputs.append(baseline)
        scenarios_state = run_stage(
            name="policy_scenarios",
            argv=scenario_argv,
            inputs=scenario_inputs,
            out_dir=scenarios_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["policy_scenarios"] = scenarios_state
        scenarios_manifest = manifest_for(scenarios_state)
        scenarios_ok = bool(
            scenarios_manifest.get("scenario_computation_complete") is True
            and scenarios_manifest.get("promotion_policy_selected") is None
            and scenarios_manifest.get("promotion_ready") is False
        )
        if not scenarios_ok:
            raise PresyncError("policy scenario non-selection contract failed")

    member_map_raw = get_path(config, "rollcall.member_map")
    member_map = (
        resolve_input(
            member_map_raw,
            workspace_root=workspace_root,
            label="rollcall.member_map",
            blockers=blockers,
        )
        if member_map_raw is not None
        else None
    )
    fixture: Path | None = None
    fixture_raw = get_path(config, "rollcall.fixture_manifest")
    if fixture_raw is not None:
        fixture = resolve_input(
            fixture_raw,
            workspace_root=workspace_root,
            label="rollcall.fixture_manifest",
            blockers=blockers,
        )
    api_env = get_path(config, "rollcall.congress_api_key_env")
    api_available = bool(
        api_env == "CONGRESS_API_KEY" and os.environ.get("CONGRESS_API_KEY")
    )
    action_source = str(
        get_path(config, "rollcall.action_source") or "auto"
    ).strip()
    allowed_action_sources = {"auto", "billstatus", "congress_api"}
    if action_source not in allowed_action_sources:
        add_blocker(
            blockers,
            "official_rollcall_action_source_invalid",
            observed=action_source,
            allowed=sorted(allowed_action_sources),
        )
    source_ready_for_launch = bool(
        action_source in allowed_action_sources
        and (
            action_source in {"auto", "billstatus"}
            or fixture
            or api_available
        )
    )
    if not source_ready_for_launch:
        add_blocker(
            blockers,
            "official_rollcall_source_unavailable",
            requirement=(
                "action_source auto/billstatus, fixture_manifest, or populated "
                "congress_api_key_env"
            ),
        )
    rollcall_source_gate: dict[str, Any] = {
        "status": "READY" if source_ready_for_launch else "BLOCKED",
        "passed": source_ready_for_launch,
        "requested_action_source": action_source,
        "billstatus_no_key_supported_measure_types": [
            "hconres",
            "hjres",
            "hr",
            "hres",
            "s",
            "sconres",
            "sjres",
            "sres",
        ],
        "billstatus_amendment_support": False,
        "congress_api_key_present": api_available,
        "fixture_manifest_present": fixture is not None,
        "accepted_amendment_policy": (
            "fail closed unless exhaustive official Congress.gov amendment "
            "actions are available through API or fixture"
        ),
    }
    rollcall_ok = False
    rollcall_state: dict[str, Any] | None = None
    rollcall_identity_gate: dict[str, Any] = {
        "status": "NOT_RUN",
        "passed": False,
        "issues": ["official roll-call stage has not run"],
        "supplemental_member_map": {
            "status": "present" if member_map else "absent",
            "present": member_map is not None,
            "receipt": file_receipt(member_map) if member_map else None,
        },
    }
    if (
        accepted_ledger
        and direction_ok
        and direction_ledger
        and source_ready_for_launch
        and not args.check_only
    ):
        rollcall_out = stages_root / "official_rollcalls"
        rollcall_argv = [
            "--accepted",
            str(accepted_ledger),
            "--out",
            str(rollcall_out),
            "--directions",
            str(direction_ledger),
            "--action-source",
            action_source,
        ]
        rollcall_inputs = [accepted_ledger, direction_ledger]
        billstatus_source = (
            workspace_root / "fec" / "legislation" / "official_billstatus_rollcall_source.py"
        )
        if billstatus_source.is_file():
            rollcall_inputs.append(billstatus_source)
        if member_map:
            rollcall_argv.extend(["--member-map", str(member_map)])
            rollcall_inputs.append(member_map)
        if fixture:
            rollcall_argv.extend(["--fixture-manifest", str(fixture)])
            rollcall_inputs.append(fixture)
        rollcall_state = run_stage(
            name="official_rollcalls",
            argv=rollcall_argv,
            inputs=rollcall_inputs,
            out_dir=rollcall_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["official_rollcalls"] = rollcall_state
        rollcall_ok, rollcall_identity_gate = (
            validate_rollcall_identity_manifest(
                manifest_for(rollcall_state),
                expected_member_map=member_map,
                expected_member_map_present=member_map is not None,
            )
        )
        if not rollcall_ok:
            add_blocker(
                blockers,
                "official_rollcall_acceptance_failed",
                manifest=rollcall_state["manifest"],
                identity_issues=rollcall_identity_gate["issues"],
            )

    consumer_ok = False
    consumer_plan: Path | None = None
    capabilities_raw = get_path(config, "shadow.capabilities_manifest")
    capabilities = (
        resolve_input(
            capabilities_raw,
            workspace_root=workspace_root,
            label="shadow.capabilities_manifest",
            blockers=blockers,
        )
        if capabilities_raw
        else None
    )
    if not args.check_only:
        consumer_out = stages_root / "consumer_plan"
        consumer_argv = [
            "--out",
            str(consumer_out),
            "--workspace-root",
            str(workspace_root),
        ]
        if capabilities:
            consumer_argv.extend(
                ["--capabilities-manifest", str(capabilities)]
            )
        consumer_inputs = [
            workspace_root / producer
            for producer in CONSUMER_PLAN.DEFAULT_PRODUCERS.values()
            if (workspace_root / producer).is_file()
        ]
        consumer_inputs.extend(
            path
            for path in (
                workspace_root
                / CONSUMER_PLAN.DEFAULT_SPREADSHEET_IMPORT_RECEIPT,
                workspace_root
                / (
                    CONSUMER_PLAN
                    .DEFAULT_ACCEPTED_WORKBOOK_PRODUCER_VERIFICATION
                ),
                workspace_root / CONSUMER_PLAN.DEFAULT_BASELINE_MANIFEST,
                capabilities,
            )
            if path and path.is_file()
        )
        consumer_state = run_stage(
            name="consumer_plan",
            argv=consumer_argv,
            inputs=consumer_inputs,
            out_dir=consumer_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["consumer_plan"] = consumer_state
        consumer_manifest = manifest_for(consumer_state)
        consumer_ok = consumer_manifest.get("status") == "PASS"
        consumer_plan = consumer_out / "consumer_plan.json"
        if not consumer_ok:
            add_blocker(
                blockers,
                "shadow_consumer_isolation_incomplete",
                spreadsheet_isolation_must_pass=True,
                manifest=consumer_state["manifest"],
            )

    baseline = resolve_input(
        get_path(config, "shadow.baseline_manifest"),
        workspace_root=workspace_root,
        label="shadow.baseline_manifest",
        blockers=blockers,
    )
    role_receipt = resolve_input(
        get_path(config, "shadow.role_weight_policy_receipt"),
        workspace_root=workspace_root,
        label="shadow.role_weight_policy_receipt",
        blockers=blockers,
    )
    computation_receipt = resolve_input(
        get_path(config, "shadow.computation_policy_receipt"),
        workspace_root=workspace_root,
        label="shadow.computation_policy_receipt",
        blockers=blockers,
    )
    audited_layout = resolve_input(
        get_path(config, "shadow.audited_workbook_layout"),
        workspace_root=workspace_root,
        label="shadow.audited_workbook_layout",
        blockers=blockers,
    )
    manual_evidence = resolve_input(
        get_path(config, "shadow.manual_bill_evidence"),
        workspace_root=workspace_root,
        label="shadow.manual_bill_evidence",
        blockers=blockers,
    )
    if not role_receipt:
        add_blocker(blockers, "role_weight_policy_unresolved")
    if not computation_receipt:
        add_blocker(blockers, "workbook_computation_policy_unresolved")
    if not audited_layout:
        add_blocker(blockers, "audited_workbook_layout_unavailable")
    if not manual_evidence:
        add_blocker(blockers, "manual_bill_evidence_unavailable")

    approved_accepted: Path | None = None
    approval_inputs_ready = bool(
        accepted_state
        and accepted_ledger
        and direction_state
        and direction_ok
        and direction_ledger
        and baseline
        and role_receipt
        and computation_receipt
        and audited_layout
        and manual_evidence
    )
    if approval_inputs_ready and not args.check_only:
        accepted_manifest_path = Path(
            accepted_state["manifest"]["path"]
        )
        direction_manifest_path = Path(
            direction_state["manifest"]["path"]
        )
        numeric_out = stages_root / "numeric_plan"
        numeric_path = numeric_out / "numeric_plan.json"
        numeric_state = run_stage(
            name="numeric_plan",
            argv=[
                "--accepted-ledger",
                str(accepted_ledger),
                "--directions-ledger",
                str(direction_ledger),
                "--manual-evidence",
                str(manual_evidence),
                "--layout",
                str(audited_layout),
                "--policy-approval",
                str(computation_receipt),
                "--baseline-manifest",
                str(baseline),
                "--out",
                str(numeric_path),
            ],
            inputs=[
                accepted_ledger,
                direction_ledger,
                manual_evidence,
                audited_layout,
                computation_receipt,
                baseline,
            ],
            out_dir=numeric_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["numeric_plan"] = numeric_state
        numeric_manifest = manifest_for(numeric_state)
        if numeric_manifest.get("status") != "PASS":
            add_blocker(
                blockers,
                "workbook_numeric_plan_blocked",
                manifest=numeric_state["manifest"],
            )
        else:
            approved_out = stages_root / "approved_accepted_generation"
            approved_state = run_stage(
                name="approved_generation",
                argv=[
                    "--accepted-manifest",
                    str(accepted_manifest_path),
                    "--accepted-ledger",
                    str(accepted_ledger),
                    "--direction-manifest",
                    str(direction_manifest_path),
                    "--direction-ledger",
                    str(direction_ledger),
                    "--manual-evidence",
                    str(manual_evidence),
                    "--role-policy-approval",
                    str(role_receipt),
                    "--computation-policy-approval",
                    str(computation_receipt),
                    "--audited-layout",
                    str(audited_layout),
                    "--numeric-plan",
                    str(numeric_path),
                    "--out",
                    str(approved_out),
                ],
                inputs=[
                    accepted_manifest_path,
                    accepted_ledger,
                    direction_manifest_path,
                    direction_ledger,
                    manual_evidence,
                    role_receipt,
                    computation_receipt,
                    audited_layout,
                    numeric_path,
                ],
                out_dir=approved_out,
                state_dir=state_dir,
                python_executable=python_executable,
                workspace_root=workspace_root,
                resume=args.resume,
                timeout_seconds=timeout,
            )
            stages["approved_generation"] = approved_state
            approved_manifest = manifest_for(approved_state)
            if not (
                approved_manifest.get("generation_complete") is True
                and approved_manifest.get("upstream_acceptance_passed") is True
                and approved_manifest.get("promotion_ready") is True
                and approved_manifest.get("role_weight_policy_reconciled")
                is True
                and approved_manifest.get("selected_method_profile")
                == APPROVED_GENERATION.ROLE_METHOD_PROFILE_ID
                and approved_manifest.get("consumer_role_methods")
                == APPROVED_GENERATION.ROLE_METHOD_PROFILE
                and bool(
                    approved_manifest.get("selected_role_weight_policy")
                )
            ):
                add_blocker(
                    blockers,
                    "approved_generation_assembly_failed",
                    manifest=approved_state["manifest"],
                )
            else:
                approved_accepted = approved_out
    elif accepted_state and not args.check_only:
        add_blocker(
            blockers,
            "approved_generation_inputs_incomplete",
        )

    shadow_ready = bool(
        approved_accepted
        and direction_ok
        and direction_state
        and base_ok
        and base_state
        and rollcall_ok
        and rollcall_state
        and scenarios_ok
        and consumer_ok
        and consumer_plan
        and baseline
        and decay_ok
        and decay_receipt
        and billstatus_ok
        and billstatus_divergence_ok
        and billstatus_completeness_ok
        and billstatus_plan
        and billstatus_divergence_path
        and billstatus_completeness_path
        and query_specificity_ok
        and query_specificity_path
        and strict
        and final
        and ratified_manifest_path
        and relationships_manifest
        and common["membership.reconciliation_report"]
    )
    shadow_ok = False
    if shadow_ready and not args.check_only:
        shadow_out = stages_root / "shadow_recompute"
        direction_dir = Path(direction_state["manifest"]["path"]).parent
        base_dir = Path(base_state["manifest"]["path"]).parent
        rollcall_dir = Path(rollcall_state["manifest"]["path"]).parent
        shadow_explicit_inputs = [
            strict,
            final,
            approved_accepted / "manifest.json",
            Path(direction_state["manifest"]["path"]),
            Path(base_state["manifest"]["path"]),
            Path(rollcall_state["manifest"]["path"]),
            baseline,
            consumer_plan,
            ratified_manifest_path,
            relationships_manifest,
            common["membership.reconciliation_report"],
            role_receipt,
            decay_receipt,
            billstatus_plan,
            billstatus_divergence_path,
            billstatus_completeness_path,
            query_specificity_path,
        ]
        shadow_closure_inputs = shadow_transitive_fingerprint_inputs(
            workspace_root=workspace_root,
            explicit_inputs=shadow_explicit_inputs,
            accepted_generation=approved_accepted,
            direction_dir=direction_dir,
            base_dir=base_dir,
            rollcall_dir=rollcall_dir,
            baseline_manifest=baseline,
        )
        shadow_state = run_stage(
            name="shadow_recompute",
            argv=[
                "--coverage-run",
                str(strict.parent),
                "--accepted-generation",
                str(approved_accepted),
                "--direction",
                str(direction_dir),
                "--base-network",
                str(base_dir),
                "--rollcall",
                str(rollcall_dir),
                "--baseline-manifest",
                str(baseline),
                "--consumer-plan",
                str(consumer_plan),
                "--strict-discovery",
                str(strict),
                "--final-merged",
                str(final),
                "--review",
                str(ratified_manifest_path),
                "--relationships",
                str(relationships_manifest),
                "--reconciliation",
                str(common["membership.reconciliation_report"]),
                "--out",
                str(shadow_out),
            ],
            inputs=shadow_closure_inputs,
            out_dir=shadow_out,
            state_dir=state_dir,
            python_executable=python_executable,
            workspace_root=workspace_root,
            resume=args.resume,
            timeout_seconds=timeout,
        )
        stages["shadow_recompute"] = shadow_state
        shadow_ok = shadow_acceptance_passed(shadow_state)
        if not shadow_ok:
            add_blocker(
                blockers,
                "shadow_recompute_acceptance_failed",
                manifest=shadow_state["manifest"],
            )
    elif not args.check_only:
        add_blocker(blockers, "shadow_recompute_upstream_gates_incomplete")

    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "status": "PASS" if shadow_ok and not blockers else "BLOCKED",
        "exit_code": 0 if shadow_ok and not blockers else EXIT_BLOCKED,
        "staged_only": True,
        "production_or_mainframe_written": False,
        "production_commands_executed": False,
        "captcha_bypass_attempted": False,
        "human_decisions_created_or_modified": False,
        "role_weight_policy_selected_by_orchestrator": False,
        "config": file_receipt(config_path),
        "orchestrator": file_receipt(Path(__file__).resolve()),
        "decay_propagation_gate": decay_gate,
        "rollcall_source_gate": rollcall_source_gate,
        "rollcall_identity_gate": rollcall_identity_gate,
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
        "coverage": coverage,
        "stages": stages,
        "blockers": sorted(
            blockers,
            key=lambda row: canonical_bytes(row),
        ),
        "resume_supported": True,
        "idempotence": (
            "stage outputs are reused only when command, producer, all explicit "
            "input hashes, and the recorded manifest hash are unchanged"
        ),
    }
    atomic_json(out / "checkpoint.json", checkpoint)
    return checkpoint


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run a fail-closed, staged-only legislation pre-sync audit."
    )
    result.add_argument("--config", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--resume", action="store_true")
    result.add_argument(
        "--resume-coverage",
        action="store_true",
        help=(
            "If strict coverage is incomplete, resume only the allowlisted "
            "Congress.gov wrapper declared in config; never handles CAPTCHA."
        ),
    )
    result.add_argument(
        "--check-only",
        action="store_true",
        help="Write a blocker checkpoint without launching staged producers.",
    )
    result.add_argument("--batch-size", type=int, default=250)
    result.add_argument("--stage-timeout-seconds", type=int, default=7200)
    result.add_argument("--python-executable", default=sys.executable)
    result.add_argument(
        "--workspace-root", type=Path, default=ROOT, help=argparse.SUPPRESS
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.batch_size <= 0:
        print("pre-sync audit failed closed: --batch-size must be positive")
        return EXIT_ERROR
    if args.stage_timeout_seconds <= 0:
        print(
            "pre-sync audit failed closed: --stage-timeout-seconds must be positive"
        )
        return EXIT_ERROR
    try:
        result = orchestrate(args)
    except (
        PresyncError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ) as exc:
        print(f"pre-sync audit failed closed: {exc}")
        return EXIT_ERROR
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
