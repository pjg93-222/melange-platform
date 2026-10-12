#!/usr/bin/env python3
"""Seal the frozen/live downstream drift inventory without granting authority."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
from typing import Any
import uuid

try:
    from .drift_closure import (
        CHANGED_COUNT,
        EXPECTED_CHANGED_NAMES,
        TOTAL,
        UNCHANGED_COUNT,
    )
    from .live_relocations import LIVE_RELOCATIONS, resolve_live_source
except ImportError:
    try:
        from fec.audit.drift_closure import (
            CHANGED_COUNT,
            EXPECTED_CHANGED_NAMES,
            TOTAL,
            UNCHANGED_COUNT,
        )
        from fec.audit.live_relocations import (
            LIVE_RELOCATIONS,
            resolve_live_source,
        )
    except ModuleNotFoundError:  # Direct script execution from fec/audit.
        from drift_closure import (  # stale-ok: guarded script-execution fallback after explicit dir insert
            CHANGED_COUNT,
            EXPECTED_CHANGED_NAMES,
            TOTAL,
            UNCHANGED_COUNT,
        )
        from live_relocations import LIVE_RELOCATIONS, resolve_live_source  # stale-ok: guarded script-execution fallback after explicit dir insert


ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = (
    ROOT / "audits" / "legfix5" / "production_baseline_full_downstream"
)
DEFAULT_BASELINE_MANIFEST = BASELINE_DIR / "baseline_manifest.json"
DEFAULT_DECAY_AUDIT = (
    ROOT / "audits" / "legfix5" / "decay_propagation"
    / "decay_propagation_audit.json"
)
DEFAULT_RANK_POINTER = (
    ROOT / "audits" / "legfix5" / "post_weekly_refresh_rank_drift"
    / "current.json"
)
DEFAULT_LAYOUT_RECEIPT = (
    ROOT / "audits" / "legfix5" / "spreadsheet_engine_verification"
    / "audited_layout_sidecar_ooxml_v1" / "audited_member_layout.json"
)
DEFAULT_MODEL_RECEIPT = (
    ROOT / "audits" / "legfix5" / "spreadsheet_engine_verification"
    / "model_verification" / "model_verification_receipt.json"
)
DEFAULT_OUT_ROOT = (
    ROOT / "audits" / "legfix5" / "post_weekly_refresh_downstream_drift"
)

STAGE_BY_NAME = {
    "Moneyball Main Version - Blended.xlsx": (
        "recompute_legislative_raw_normalized_sublayer"
    ),
    "behavior_panel.json": "recompute_behavior_panel",
    "behavioral_dispositions.json": "recompute_behavioral_dispositions",
    "member_receptivity.json": "recompute_receptivity",
    "category_stance.json": "recompute_category_stance",
    "ranks_python.json": "recompute_pillars_and_ranks",
    "electoral_plays.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "per_topic_alloc.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "per_topic_coalition.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "budget_frontier.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "budget_frontier_electoral.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "optimize_result.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "im_allocation.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "salience_advisory.json": (
        "recompute_coalitions_flips_budget_optimizer_allocation"
    ),
    "dashboard_data.json": "recompute_dashboard_and_member_desk",
    "Moneyball Dashboard.html": "recompute_dashboard_and_member_desk",
    "member_desk_bundle.json": "recompute_dashboard_and_member_desk",
    "dashboard_exclusion_reconciliation.json": (
        "recompute_dashboard_and_member_desk"
    ),
    "Moneyball.html": "recompute_dashboard_and_member_desk",
    "cu-member-desk.html": "recompute_dashboard_and_member_desk",
}


class AuditError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def file_item(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Missing bound receipt: {path}")
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def validate_file_item(item: Any, label: str) -> Path:
    require(isinstance(item, dict), f"{label} receipt missing")
    path = Path(str(item.get("path") or ""))
    require(
        path.is_absolute() and path.is_file(),
        f"{label} receipt path invalid",
    )
    require(
        item.get("bytes") == path.stat().st_size
        and item.get("sha256") == sha256_file(path),
        f"{label} receipt drift",
    )
    return path.resolve()


def root_relative_name(value: Any, root: Path) -> str:
    path = Path(str(value))
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AuditError(f"Source path is outside the project root: {path}") from exc


def read_json(path: Path) -> Any:
    require(path.is_file(), f"Missing JSON input: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuditError(f"Unreadable JSON input {path}: {exc}") from exc


def _json_path(parent: str, component: str) -> str:
    if not parent:
        return component
    if component.startswith("["):
        return f"{parent}{component}"
    return f"{parent}.{component}"


def json_diff_evidence(frozen_path: Path, live_path: Path) -> dict[str, Any]:
    frozen = read_json(frozen_path)
    live = read_json(live_path)
    counts = {
        "added_key_or_index_paths": 0,
        "removed_key_or_index_paths": 0,
        "type_changed_paths": 0,
        "value_changed_paths": 0,
        "equal_subtrees": 0,
    }
    samples: list[dict[str, Any]] = []

    def sample(path: str, kind: str) -> None:
        if len(samples) < 100:
            samples.append({"path": path or "$", "change": kind})

    def compare(old: Any, new: Any, path: str = "") -> None:
        if type(old) is not type(new):
            counts["type_changed_paths"] += 1
            sample(path, "type_changed")
            return
        if isinstance(old, dict):
            old_keys = set(old)
            new_keys = set(new)
            for key in sorted(old_keys - new_keys):
                counts["removed_key_or_index_paths"] += 1
                sample(_json_path(path, str(key)), "removed")
            for key in sorted(new_keys - old_keys):
                counts["added_key_or_index_paths"] += 1
                sample(_json_path(path, str(key)), "added")
            for key in sorted(old_keys & new_keys):
                compare(old[key], new[key], _json_path(path, str(key)))
            return
        if isinstance(old, list):
            common = min(len(old), len(new))
            for index in range(common):
                compare(old[index], new[index], _json_path(path, f"[{index}]"))
            for index in range(common, len(old)):
                counts["removed_key_or_index_paths"] += 1
                sample(_json_path(path, f"[{index}]"), "removed")
            for index in range(common, len(new)):
                counts["added_key_or_index_paths"] += 1
                sample(_json_path(path, f"[{index}]"), "added")
            return
        if old == new:
            counts["equal_subtrees"] += 1
        else:
            counts["value_changed_paths"] += 1
            sample(path, "value_changed")

    compare(frozen, live)
    frozen_keys = sorted(frozen) if isinstance(frozen, dict) else []
    live_keys = sorted(live) if isinstance(live, dict) else []
    changed_path_count = sum(
        counts[key]
        for key in (
            "added_key_or_index_paths",
            "removed_key_or_index_paths",
            "type_changed_paths",
            "value_changed_paths",
        )
    )
    return {
        "method": "recursive_structure_and_scalar_value_comparison",
        "frozen_root_type": type(frozen).__name__,
        "live_root_type": type(live).__name__,
        "frozen_top_level_key_count": len(frozen_keys),
        "live_top_level_key_count": len(live_keys),
        "added_top_level_keys": sorted(set(live_keys) - set(frozen_keys)),
        "removed_top_level_keys": sorted(set(frozen_keys) - set(live_keys)),
        **counts,
        "changed_path_count": changed_path_count,
        "changed_path_sample": samples,
        "domain_semantics_resolved": False,
        "semantic_coverage": "STRUCTURE_AWARE_VALUE_DIFF_NONAUTHORITATIVE",
    }


def normalize_html(body: bytes) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AuditError(f"HTML is not UTF-8: {exc}") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip().encode("utf-8")


def html_diff_evidence(frozen_path: Path, live_path: Path) -> dict[str, Any]:
    frozen = normalize_html(frozen_path.read_bytes())
    live = normalize_html(live_path.read_bytes())
    common = min(len(frozen), len(live))
    offset = next(
        (index for index in range(common) if frozen[index] != live[index]),
        common if frozen != live else None,
    )
    return {
        "method": "utf8_line_ending_and_whitespace_normalized_hash",
        "frozen_normalized_bytes": len(frozen),
        "live_normalized_bytes": len(live),
        "frozen_normalized_sha256": sha256_bytes(frozen),
        "live_normalized_sha256": sha256_bytes(live),
        "normalized_content_equal": frozen == live,
        "first_differing_normalized_byte_offset": offset,
        "rendered_dom_or_interaction_semantics_resolved": False,
        "semantic_coverage": "NORMALIZED_CONTENT_DIFF_NONAUTHORITATIVE",
    }


def classify_unchanged(name: str) -> str:
    if name.endswith(".py"):
        return "frozen_pipeline_source"
    if "rollcall" in name or name == "vote_category_lean.json":
        return "official_rollcall_projection"
    if "network" in name:
        return "base_or_augmented_network"
    if name in {"bipartisan.json", "coop_v2_candidate.json"}:
        return "cooperation_or_bipartisanship"
    if name == "say_do_gap.json":
        return "say_do"
    if name in {"archive_leg.json", "legislation_routing_audit.json"}:
        return "legislation_archive_or_routing"
    return "frozen_upstream_or_downstream_artifact"


def cleanup_temporary(path: Path, expected_parent: Path) -> None:
    path = path.resolve()
    expected_parent = expected_parent.resolve()
    require(
        path.parent == expected_parent
        and path.name.startswith(".downstream-drift."),
        f"Unsafe temporary cleanup target: {path}",
    )

    def onexc(function: Any, target: str, error: BaseException) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(target)

    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def write_if_changed(path: Path, body: bytes) -> bool:
    """Atomically update a small pointer while preserving true idempotence."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == body:
        return False
    require(
        not path.exists() or path.is_file(),
        f"Pointer target is not a file: {path}",
    )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(body)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


def build_audit(
    *,
    root: Path = ROOT,
    baseline_manifest_path: Path = DEFAULT_BASELINE_MANIFEST,
    decay_audit_path: Path = DEFAULT_DECAY_AUDIT,
    rank_pointer_path: Path = DEFAULT_RANK_POINTER,
    layout_receipt_path: Path = DEFAULT_LAYOUT_RECEIPT,
    model_receipt_path: Path = DEFAULT_MODEL_RECEIPT,
    out_root: Path = DEFAULT_OUT_ROOT,
    expected_file_count: int = TOTAL,
    expected_changed_names: frozenset[str] = EXPECTED_CHANGED_NAMES,
) -> tuple[Path, dict[str, Any], bool]:
    manifest = read_json(baseline_manifest_path)
    decay = read_json(decay_audit_path)
    require(isinstance(manifest, dict), "Baseline manifest must be an object")
    require(isinstance(decay, dict), "Decay audit must be an object")
    files = manifest.get("files")
    require(isinstance(files, list), "Baseline manifest files must be a list")
    require(
        manifest.get("file_count") == expected_file_count == len(files),
        "Incomplete baseline file closure",
    )

    decay_baseline = decay.get("production_baseline")
    require(isinstance(decay_baseline, dict), "Decay audit baseline missing")
    decay_manifest = decay_baseline.get("manifest")
    require(
        isinstance(decay_manifest, dict),
        "Decay baseline-manifest receipt missing",
    )
    decay_manifest_path = Path(str(decay_manifest.get("path") or ""))
    if not decay_manifest_path.is_absolute():
        decay_manifest_path = root / decay_manifest_path
    require(
        decay_manifest_path.resolve() == baseline_manifest_path.resolve()
        and decay_manifest.get("sha256")
        == sha256_file(baseline_manifest_path)
        and decay_manifest.get("size")
        == baseline_manifest_path.stat().st_size,
        "Decay baseline-manifest receipt drift",
    )
    decay_checked = decay_baseline.get("checked_files")
    require(
        isinstance(decay_checked, list)
        and len(decay_checked) == expected_file_count,
        f"Decay checked-file ledger does not close over {TOTAL} files",
    )
    checked_by_index = {
        item.get("index"): item
        for item in decay_checked
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    require(
        len(checked_by_index) == expected_file_count
        and set(checked_by_index) == set(range(expected_file_count)),
        "Decay checked-file index closure is invalid",
    )
    decay_mismatches = decay_baseline.get("source_and_copy_mismatches")
    require(isinstance(decay_mismatches, list), "Decay mismatch ledger missing")
    require(
        all(isinstance(item, dict) for item in decay_mismatches),
        "Decay mismatch ledger contains an invalid row",
    )
    decay_changed_names = {
        root_relative_name(item.get("source") or "", root)
        for item in decay_mismatches
    }

    rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    observed_changed: set[str] = set()
    for index, item in enumerate(files):
        require(isinstance(item, dict), "Invalid baseline file row")
        source_text = item.get("source")
        copy_text = item.get("copy")
        expected_hash = item.get("sha256")
        expected_size = item.get("size")
        require(
            all(isinstance(value, str) and value for value in (
                source_text,
                copy_text,
                expected_hash,
            ))
            and isinstance(expected_size, int)
            and expected_size >= 0,
            "Incomplete baseline file row",
        )
        recorded_source = Path(source_text)
        source = resolve_live_source(recorded_source, root)
        copy = Path(copy_text)
        if not copy.is_absolute():
            copy = root / copy
        name = root_relative_name(recorded_source, root)
        require(name not in seen_names, f"Duplicate baseline name: {name}")
        seen_names.add(name)
        require(copy.is_file(), f"Missing frozen file: {copy}")
        require(source.is_file(), f"Missing live file: {source}")
        frozen_hash = sha256_file(copy)
        live_hash = sha256_file(source)
        require(
            frozen_hash == expected_hash
            and copy.stat().st_size == expected_size,
            f"Frozen hash tampering detected: {name}",
        )
        changed = frozen_hash != live_hash
        checked = checked_by_index[index]
        checked_source = resolve_live_source(
            checked.get("source") or "",
            root,
        )
        checked_copy = Path(str(checked.get("copy") or ""))
        if not checked_copy.is_absolute():
            checked_copy = root / checked_copy
        require(
            checked_source.resolve() == source.resolve()
            and checked_copy.resolve() == copy.resolve()
            and checked.get("expected_sha256") == expected_hash
            and checked.get("copy_sha256") == frozen_hash
            and checked.get("source_sha256") == live_hash
            and checked.get("passed") is (not changed),
            f"Decay checked-file receipt drift: {name}",
        )
        if changed:
            observed_changed.add(name)
        row: dict[str, Any] = {
            "schema": "post-weekly-refresh-downstream-file-drift-v1",
            "name": name,
            "frozen_path": str(copy.resolve()),
            "live_path": str(source.resolve()),
            "frozen_bytes": copy.stat().st_size,
            "live_bytes": source.stat().st_size,
            "frozen_sha256": frozen_hash,
            "live_sha256": live_hash,
            "byte_identical": not changed,
            "changed": changed,
            "declared_stage": (
                STAGE_BY_NAME.get(name)
                if changed
                else classify_unchanged(name)
            ),
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
        if changed and source.suffix.lower() == ".json":
            row["semantic_evidence"] = json_diff_evidence(copy, source)
            row["existing_semantic_receipt"] = (
                "post_weekly_refresh_rank_drift"
                if name == "ranks_python.json"
                else None
            )
        elif changed and source.suffix.lower() in {".html", ".htm"}:
            row["semantic_evidence"] = html_diff_evidence(copy, source)
            row["existing_semantic_receipt"] = None
        elif changed and source.suffix.lower() == ".xlsx":
            row["semantic_evidence"] = {
                "method": "byte_hash_plus_bound_layout_model_and_rank_receipts",
                "cell_level_semantic_comparison_resolved": False,
                "semantic_coverage": (
                    "WORKBOOK_CELL_LEVEL_SEMANTICS_EXPLICITLY_UNRESOLVED"
                ),
            }
            row["existing_semantic_receipt"] = None
        else:
            row["semantic_evidence"] = {
                "method": "exact_byte_identity",
                "semantic_drift_absent_by_byte_identity": True,
                "semantic_coverage": "COMPLETE_BYTE_IDENTITY",
            }
            row["existing_semantic_receipt"] = None
        rows.append(row)

    require(
        seen_names
        == {root_relative_name(item["source"], root) for item in files},
        "Incomplete file-name closure",
    )
    require(
        observed_changed == expected_changed_names,
        f"Incomplete {CHANGED_COUNT}/{UNCHANGED_COUNT} changed-file closure",
    )
    require(
        decay_changed_names == expected_changed_names,
        "Decay audit mismatch ledger does not close over exact changed set",
    )
    mismatch_by_name = {
        root_relative_name(item.get("source") or "", root): item
        for item in decay_mismatches
    }
    require(
        len(mismatch_by_name) == len(expected_changed_names),
        "Decay mismatch ledger contains duplicate or extra rows",
    )
    for name in expected_changed_names:
        mismatch = mismatch_by_name[name]
        checked = next(
            row
            for row in decay_checked
            if root_relative_name(row.get("source") or "", root) == name
        )
        require(
            mismatch == checked and mismatch.get("passed") is False,
            f"Decay mismatch receipt drift: {name}",
        )
    unchanged_count = len(rows) - len(observed_changed)
    require(
        len(observed_changed) == CHANGED_COUNT
        and unchanged_count == UNCHANGED_COUNT,
        (
            f"Expected exact {CHANGED_COUNT} changed / "
            f"{UNCHANGED_COUNT} unchanged closure"
        ),
    )
    require(
        decay_baseline.get("declared_file_count") == TOTAL
        and decay_baseline.get("source_and_copy_hash_match_count")
        == UNCHANGED_COUNT,
        (
            f"Decay audit does not prove {UNCHANGED_COUNT}/{TOTAL} byte "
            "closure"
        ),
    )
    # 2026-08-04: The decay writer's "passed" was decoupled from live-file drift
    # during staged-promotion prep; this old coupling rejected a correct receipt.
    # Baseline corruption or production cutover still fails here via passed=False.
    require(
        decay_baseline.get("passed") is True
        and decay_baseline.get(
            "all_live_sources_and_frozen_copies_match_manifest"
        )
        is False,
        "Decay audit must show intact frozen baseline + no cutover alongside observed live drift",
    )

    rank_pointer = read_json(rank_pointer_path)
    require(
        isinstance(rank_pointer, dict)
        and rank_pointer.get("schema")
        == "post-weekly-refresh-rank-drift-pointer-v1"
        and rank_pointer.get("authoritative_for_legislation_ranks") is False
        and rank_pointer.get("authoritative_for_scoring") is False
        and rank_pointer.get("authoritative_for_sync_or_promotion") is False
        and rank_pointer.get("sync_or_promotion_performed") is False,
        "Rank-drift pointer safety contract drift",
    )
    rank_manifest_path = validate_file_item(
        rank_pointer.get("manifest"),
        "Rank-drift manifest",
    )
    rank_manifest = read_json(rank_manifest_path)
    require(
        isinstance(rank_manifest, dict)
        and rank_manifest.get("schema")
        == "post-weekly-refresh-rank-drift-manifest-v1"
        and rank_manifest.get("audit_id") == rank_pointer.get("audit_id")
        and rank_manifest.get("status")
        == "PASS_OBSERVATIONAL_NONAUTHORITATIVE"
        and rank_manifest.get("authoritative_for_legislation_ranks") is False
        and rank_manifest.get("authoritative_for_scoring") is False
        and rank_manifest.get("authoritative_for_sync_or_promotion") is False
        and rank_manifest.get("legislation_recomputation_accepted") is False
        and rank_manifest.get("legislation_rank_cutover_performed") is False
        and rank_manifest.get("production_write_performed") is False
        and rank_manifest.get("sync_or_promotion_performed") is False
        and rank_manifest.get("network_executed") is False,
        "Rank-drift manifest safety contract drift",
    )

    bound_receipts = {
        "baseline_manifest": file_item(baseline_manifest_path),
        "decay_audit": file_item(decay_audit_path),
        "rank_drift_pointer": file_item(rank_pointer_path),
        "audited_layout_receipt": file_item(layout_receipt_path),
        "workbook_model_receipt": file_item(model_receipt_path),
    }
    producer = file_item(Path(__file__).resolve())
    rows.sort(key=lambda row: (not row["changed"], row["name"].casefold()))
    rows_body = b"".join(canonical_bytes(row) for row in rows)
    gap_rows = [
        {
            "schema": "post-weekly-refresh-semantic-gap-v1",
            "name": row["name"],
            "declared_stage": row["declared_stage"],
            "semantic_coverage": row["semantic_evidence"]["semantic_coverage"],
            "domain_semantics_resolved": (
                row["name"] == "ranks_python.json"
            ),
            "existing_semantic_receipt": row["existing_semantic_receipt"],
            "potentially_legislation_bearing": True,
            "accepted_legislation_recomputation": False,
            "authoritative_for_sync_or_promotion": False,
        }
        for row in rows
        if row["changed"]
    ]
    gaps_body = b"".join(canonical_bytes(row) for row in gap_rows)
    summary = {
        "declared_file_count": len(rows),
        "changed_file_count": len(observed_changed),
        "unchanged_file_count": unchanged_count,
        "json_changed_file_count": sum(
            row["changed"] and row["name"].endswith(".json") for row in rows
        ),
        "html_changed_file_count": sum(
            row["changed"] and row["name"].lower().endswith((".html", ".htm"))
            for row in rows
        ),
        "workbook_changed_file_count": sum(
            row["changed"] and row["name"].endswith(".xlsx") for row in rows
        ),
        "changed_with_existing_domain_semantic_receipt_count": 1,
        "changed_without_existing_domain_semantic_receipt_count": (
            CHANGED_COUNT - 1
        ),
        "potentially_legislation_bearing_changed_count": CHANGED_COUNT,
        "accepted_legislation_recomputation_count": 0,
        "workbook_cell_level_semantics_unresolved_count": 1,
    }
    identity = {
        "schema": "post-weekly-refresh-downstream-drift-identity-v1",
        "bound_receipts": bound_receipts,
        "producer": producer,
        "rows_sha256": sha256_bytes(rows_body),
        "semantic_gap_ledger_sha256": sha256_bytes(gaps_body),
        "summary": summary,
    }
    audit_id = sha256_bytes(canonical_bytes(identity))
    snapshot_root = out_root / "snapshots"
    snapshot_dir = snapshot_root / audit_id
    out_root.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    temporary = snapshot_root / f".downstream-drift.{uuid.uuid4().hex}"
    temporary.mkdir()
    created = False
    try:
        rows_path = temporary / "file_drift.ndjson"
        gap_path = temporary / "semantic_gap_ledger.ndjson"
        rows_path.write_bytes(rows_body)
        gap_path.write_bytes(gaps_body)
        output_rows = file_item(rows_path)
        output_rows["path"] = str((snapshot_dir / rows_path.name).resolve())
        output_rows["record_count"] = len(rows)
        output_gaps = file_item(gap_path)
        output_gaps["path"] = str((snapshot_dir / gap_path.name).resolve())
        output_gaps["record_count"] = len(gap_rows)
        output_manifest = {
            "schema": "post-weekly-refresh-downstream-drift-manifest-v1",
            "audit_id": audit_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "bound_receipts": bound_receipts,
            "producer": producer,
            "outputs": {
                "file_drift": output_rows,
                "semantic_gap_ledger": output_gaps,
            },
            "summary": summary,
            "attribution": (
                "External scheduled weekly refresh observation; causation "
                "is not inferred as accepted legislation recomputation."
            ),
            "all_changed_consumers_potentially_legislation_bearing": True,
            "workbook_cell_level_semantic_comparison_resolved": False,
            "legislation_recomputation_accepted": False,
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
            "network_executed": False,
        }
        (temporary / "manifest.json").write_bytes(pretty_bytes(output_manifest))
        if snapshot_dir.exists():
            existing = read_json(snapshot_dir / "manifest.json")
            require(
                isinstance(existing, dict)
                and existing.get("audit_id") == audit_id,
                "Existing downstream-drift manifest identity mismatch",
            )
            require(
                all(
                    existing.get(key) == value
                    for key, value in output_manifest.items()
                    if key != "generated_at"
                ),
                "Existing downstream-drift manifest contract drift",
            )
            require(
                sha256_file(snapshot_dir / rows_path.name)
                == identity["rows_sha256"],
                "Existing file-drift rows hash mismatch",
            )
            require(
                sha256_file(snapshot_dir / gap_path.name)
                == identity["semantic_gap_ledger_sha256"],
                "Existing semantic-gap rows hash mismatch",
            )
            for label, expected in (
                ("file_drift", output_rows),
                ("semantic_gap_ledger", output_gaps),
            ):
                existing_item = (existing.get("outputs") or {}).get(label)
                existing_path = validate_file_item(
                    existing_item,
                    f"Existing downstream-drift {label}",
                )
                require(
                    existing_item == expected
                    and existing_path
                    == Path(str(expected["path"])).resolve()
                    and existing_item.get("record_count")
                    == expected["record_count"],
                    f"Existing downstream-drift {label} receipt drift",
                )
            output_manifest = existing
        else:
            os.replace(temporary, snapshot_dir)
            created = True
    finally:
        if temporary.exists():
            cleanup_temporary(temporary, snapshot_root)

    pointer = {
        "schema": "post-weekly-refresh-downstream-drift-pointer-v1",
        "audit_id": audit_id,
        "snapshot_dir": str(snapshot_dir.resolve()),
        "manifest": file_item(snapshot_dir / "manifest.json"),
        "producer": producer,
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "sync_or_promotion_performed": False,
    }
    pointer_path = out_root / "current.json"
    pointer_body = pretty_bytes(pointer)
    if pointer_path.exists():
        existing_pointer = read_json(pointer_path)
        if (
            isinstance(existing_pointer, dict)
            and existing_pointer.get("audit_id") == audit_id
        ):
            require(
                existing_pointer == pointer,
                "Existing downstream-drift current pointer drift",
            )
    write_if_changed(pointer_path, pointer_body)
    return snapshot_dir, output_manifest, created


def main() -> int:
    snapshot_dir, manifest, created = build_audit()
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "created": created,
                "audit_id": manifest["audit_id"],
                "snapshot_dir": str(snapshot_dir),
                "summary": manifest["summary"],
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
