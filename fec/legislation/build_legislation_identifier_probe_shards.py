#!/usr/bin/env python3
"""Plan and consolidate isolated Congress.gov identifier-probe shards.

Planning is read-only with respect to an existing collector run.  It binds
every preserved query and page artifact by hash, assigns every other registry
query to exactly one shard, and records the active global cooldown.  The
consolidator refuses partial, overlapping, stale, or mutated inputs and
publishes a new output directory without replacing any source evidence.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import audit.audit_congress_collector_resume_continuity as continuity


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = (
    ROOT
    / "source_data"
    / "legislation"
    / "acu_legislation_identifier_probe_registry.json"
)
DEFAULT_PRESERVED_RUN = (
    ROOT / "audits" / "legfix5" / "identifier_probe_pages_20260725"
)
DEFAULT_PLAN = (
    ROOT
    / "audits"
    / "legfix5"
    / "identifier_probe_parallel_20260726"
    / "shard_plan.json"
)
DEFAULT_SHARD_ROOT = (
    ROOT / "audits" / "legfix5" / "identifier_probe_shards_20260726"
)
DEFAULT_CONSOLIDATED = (
    ROOT / "audits" / "legfix5" / "identifier_probe_pages_parallel_20260726"
)
PLAN_SCHEMA = "acu-legislation-identifier-shard-plan-v1"
CONSOLIDATION_SCHEMA = "acu-legislation-identifier-shard-consolidation-v1"
QUERY_SCHEMA = "congress-advanced-search-query-v1"
MANIFEST_SCHEMA = "acu-congress-advanced-pages-manifest-v1"
COOLDOWN_SCHEMA = "congress-automatic-verification-cooldown-v1"
STRICT_ZERO_SURFACE = "strict-main-v2"
QUERY_FIELDS = continuity.QUERY_SEMANTIC_FIELDS


class ShardValidationError(RuntimeError):
    """Raised when a shard input cannot be accepted without weakening gates."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    return continuity.sha256_file(path)


def canonical_hash(value: Any) -> str:
    return continuity.canonical_json_sha256(value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # A concurrent scanner can hold the freshly written file open; retry the
        # replace before failing, then raise.
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.05 * (2**attempt), 0.75))
    finally:
        if temporary.exists():
            temporary.unlink()


def relative_to_root(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(ROOT)
    except ValueError as exc:
        raise ShardValidationError(
            f"path must stay within workspace: {path}"
        ) from exc
    return relative.as_posix()


def resolve_workspace_path(value: str | Path) -> Path:
    path = Path(value)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    relative_to_root(resolved)
    return resolved


def registry_state(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = read_json(path)
    entries = [
        *registry.get("categories", []),
        *registry.get("coverage_probes", []),
    ]
    query_ids = [entry.get("id") for entry in entries]
    if (
        registry.get("schema")
        != "acu-congress-advanced-identifier-probes-v1"
        or registry.get("category_count") != 210
        or len(entries) != 210
        or len(set(query_ids)) != 210
        or any(not isinstance(query_id, str) for query_id in query_ids)
    ):
        raise ShardValidationError(
            "identifier registry is not the exact 210-query registry"
        )
    return registry, entries


def manifest_entry(artifact: dict[str, Any], query_file: str) -> dict[str, Any]:
    return {
        **{field: artifact.get(field) for field in QUERY_FIELDS},
        "query_file": query_file,
    }


def validate_query(
    run_dir: Path,
    query_file: str,
    registry_hash: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    query_path = (run_dir / query_file).resolve()
    try:
        query_path.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ShardValidationError(
            f"query artifact escapes run directory: {query_file}"
        ) from exc
    if not query_path.is_file():
        raise ShardValidationError(f"missing query artifact: {query_file}")
    artifact = read_json(query_path)
    query_id = entry["id"]
    errors: list[str] = []
    if artifact.get("schema") != QUERY_SCHEMA:
        errors.append("unexpected_query_schema")
    if artifact.get("query_id") != query_id:
        errors.append("query_id_mismatch")
    if artifact.get("registry_sha256") != registry_hash:
        errors.append("registry_sha256_mismatch")
    if artifact.get("advanced_search_query") != entry.get(
        "advanced_search_query"
    ):
        errors.append("advanced_search_query_mismatch")
    if (
        artifact.get("result_total") == 0
        and artifact.get("zero_result_surface_validation")
        != STRICT_ZERO_SURFACE
    ):
        errors.append("zero_result_surface_not_strict_main_v2")
    if (
        artifact.get("result_transport") == "single_result_redirect"
        and artifact.get("validation", {}).get(
            "single_result_redirect_proven"
        )
        is not True
    ):
        errors.append("single_result_redirect_not_proven")
    result = continuity.validate_current_query(
        run_dir, manifest_entry(artifact, query_file)
    )
    errors.extend(result["errors"])
    if errors:
        raise ShardValidationError(
            f"{query_id} failed query/page validation: "
            + ", ".join(sorted(set(errors)))
        )
    page_files = [page["file"] for page in artifact.get("pages", [])]
    return {
        "query_id": query_id,
        "query_file": query_file,
        "query_file_sha256": sha256_file(query_path),
        "records_sha256": artifact["records_sha256"],
        "result_total": artifact["result_total"],
        "page_count": artifact["page_count"],
        "page_files": page_files,
        "page_artifact_file_hash_binding_sha256": result[
            "page_artifact_file_hash_binding_sha256"
        ],
    }


def load_cooldown(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "automatic_verification_cooldown.json"
    if not path.is_file():
        return {
            "state": "absent_at_plan_time",
            "path": relative_to_root(path),
            "sha256": None,
            "next_retry_not_before": None,
        }
    value = read_json(path)
    if (
        value.get("schema") != COOLDOWN_SCHEMA
        or value.get("status") != "active"
        or not isinstance(value.get("next_retry_not_before"), str)
        or value.get("policy", {}).get(
            "automatic_refresh_or_reload_during_cooldown"
        )
        is not False
        or value.get("policy", {}).get("captcha_bypass_allowed") is not False
    ):
        raise ShardValidationError("global cooldown state is not valid")
    return {
        "state": "active_at_plan_time",
        "path": relative_to_root(path),
        "sha256": sha256_file(path),
        "consecutive_timeouts": value.get("consecutive_timeouts"),
        "cooldown_ms": value.get("cooldown_ms"),
        "next_retry_not_before": value["next_retry_not_before"],
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    registry_path = resolve_workspace_path(args.registry)
    preserved_run = resolve_workspace_path(args.preserved_run)
    plan_path = resolve_workspace_path(args.plan)
    shard_root = resolve_workspace_path(args.shard_root)
    consolidated = resolve_workspace_path(args.consolidated)
    registry, entries = registry_state(registry_path)
    registry_hash = sha256_file(registry_path)

    preserved: list[dict[str, Any]] = []
    preserved_ids: set[str] = set()
    for entry in entries:
        query_file = f"queries/{entry['id']}.json"
        if not (preserved_run / query_file).is_file():
            continue
        receipt = validate_query(
            preserved_run, query_file, registry_hash, entry
        )
        preserved.append(receipt)
        preserved_ids.add(entry["id"])
    if len(preserved) < args.expected_preserved:
        raise ShardValidationError(
            f"only {len(preserved)} valid preserved queries; "
            f"expected at least {args.expected_preserved}"
        )

    remaining = [entry["id"] for entry in entries if entry["id"] not in preserved_ids]
    if not remaining:
        raise ShardValidationError("there are no remaining queries to shard")
    if args.shards < 2 or args.shards > 8:
        raise ShardValidationError("--shards must be between 2 and 8")
    if args.shards > len(remaining):
        raise ShardValidationError("more shards than remaining queries")

    shard_ids: list[list[str]] = [[] for _ in range(args.shards)]
    for index, query_id in enumerate(remaining):
        shard_ids[index % args.shards].append(query_id)
    shards: list[dict[str, Any]] = []
    for index, query_ids in enumerate(shard_ids, 1):
        shard_name = f"shard-{index:02d}"
        shards.append(
            {
                "shard_id": shard_name,
                "query_count": len(query_ids),
                "query_ids": query_ids,
                "query_ids_sha256": canonical_hash(query_ids),
                "output_dir": relative_to_root(shard_root / shard_name),
                "profile_dir": str(
                    Path(args.profile_root).resolve() / shard_name
                ),
                "delay_ms": args.delay_ms * args.shards,
                "headless": True,
            }
        )

    all_partition_ids = [
        *[row["query_id"] for row in preserved],
        *[query_id for shard in shards for query_id in shard["query_ids"]],
    ]
    registry_ids = [entry["id"] for entry in entries]
    if len(all_partition_ids) != 210 or set(all_partition_ids) != set(registry_ids):
        raise ShardValidationError("plan partition is not the exact registry")
    if len(all_partition_ids) != len(set(all_partition_ids)):
        raise ShardValidationError("plan partition overlaps")

    plan = {
        "schema": PLAN_SCHEMA,
        "status": "READY_NOT_LAUNCHED",
        "created_at": utc_now(),
        "primary_instrument": "Congress.gov Advanced Search - Legislation",
        "quorum_used": False,
        "registry": {
            "path": relative_to_root(registry_path),
            "sha256": registry_hash,
            "query_count": 210,
            "query_ids_sha256": canonical_hash(registry_ids),
        },
        "global_cooldown": load_cooldown(preserved_run),
        "preserved": {
            "source_run": relative_to_root(preserved_run),
            "query_count": len(preserved),
            "query_ids_sha256": canonical_hash(
                [row["query_id"] for row in preserved]
            ),
            "queries": preserved,
            "policy": (
                "Read-only source. Consolidation copies hash-identical query "
                "and page artifacts into a new output and never overwrites it."
            ),
        },
        "shards": shards,
        "partition": {
            "registry_query_count": 210,
            "preserved_query_count": len(preserved),
            "collection_query_count": len(remaining),
            "shard_count": len(shards),
            "disjoint": True,
            "complete": True,
            "all_query_ids_sha256": canonical_hash(sorted(all_partition_ids)),
        },
        "consolidated_output": relative_to_root(consolidated),
        "acceptance": {
            "all_210_query_ids_required": True,
            "complete_shard_manifests_required": True,
            "query_and_page_revalidation_required": True,
            "registry_and_plan_hash_binding_required": True,
            "partial_downstream_use_allowed": False,
            "source_overwrite_allowed": False,
            "semantic_culling_allowed": False,
        },
    }
    atomic_write_json(plan_path, plan)
    plan_hash = sha256_file(plan_path)
    plan_path.with_suffix(plan_path.suffix + ".sha256").write_text(
        f"{plan_hash}  {plan_path.name}\n", encoding="ascii"
    )
    return plan


def verify_plan(plan_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sidecar = plan_path.with_suffix(plan_path.suffix + ".sha256")
    if not sidecar.is_file():
        raise ShardValidationError("plan SHA-256 sidecar is missing")
    tokens = sidecar.read_text(encoding="ascii").strip().split()
    if len(tokens) != 2 or tokens[0] != sha256_file(plan_path):
        raise ShardValidationError("plan SHA-256 sidecar mismatch")
    plan = read_json(plan_path)
    if plan.get("schema") != PLAN_SCHEMA:
        raise ShardValidationError("unexpected plan schema")
    registry_path = resolve_workspace_path(plan["registry"]["path"])
    registry, entries = registry_state(registry_path)
    if sha256_file(registry_path) != plan["registry"]["sha256"]:
        raise ShardValidationError("registry changed after plan creation")
    registry_ids = [entry["id"] for entry in entries]
    preserved_ids = [
        row["query_id"] for row in plan["preserved"]["queries"]
    ]
    shard_ids = [
        query_id
        for shard in plan["shards"]
        for query_id in shard["query_ids"]
    ]
    combined = [*preserved_ids, *shard_ids]
    if (
        len(combined) != 210
        or len(set(combined)) != 210
        or set(combined) != set(registry_ids)
        or plan["partition"].get("disjoint") is not True
        or plan["partition"].get("complete") is not True
    ):
        raise ShardValidationError("plan is not an exact disjoint 210-query partition")
    return plan, entries


def natural_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", value)
    )


def query_manifest_projection(
    artifact: dict[str, Any], query_file: str
) -> dict[str, Any]:
    optional = (
        "label",
        "scoring_category",
        "record_kind",
        "parent_category_id",
        "parent_category_label",
        "identifier_text",
        "title_alias",
        "ambiguous_short_act_alias",
        "membership_non_dispositive",
    )
    return {
        **{field: artifact.get(field) for field in optional},
        **{field: artifact.get(field) for field in QUERY_FIELDS},
        "query_file": query_file,
    }


def write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def copy_bound_file(source: Path, destination: Path, expected_hash: str) -> None:
    if sha256_file(source) != expected_hash:
        raise ShardValidationError(f"source artifact changed: {source}")
    if destination.exists():
        raise ShardValidationError(f"copy target already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if sha256_file(destination) != expected_hash:
        raise ShardValidationError(f"copied artifact hash mismatch: {destination}")


def coverage_visibility(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for record in records:
        common = {
            "canonical_key": record["canonical_key"],
            "display_number": record.get("display_number"),
            "title": record.get("title"),
            "result_url": record.get("result_url"),
            "disposition": "included_unreviewed_candidate",
        }
        if record.get("window_status") == "boundary_extension_before_window":
            gaps.append(
                {
                    "gap_kind": "boundary_extension_included",
                    **common,
                    "explanation": (
                        "Predates the exact six-year start but falls in the "
                        "116th Congress. Retained under the boundary-extension "
                        "rule."
                    ),
                }
            )
        if record.get("window_status") == "date_unknown":
            gaps.append(
                {
                    "gap_kind": "date_unknown_requires_review",
                    **common,
                    "explanation": (
                        "Congress.gov result card did not expose a parseable "
                        "initial-action date. Retained rather than silently "
                        "dropped."
                    ),
                }
            )
    return sorted(
        gaps,
        key=lambda row: natural_key(
            f"{row['gap_kind']}:{row['canonical_key']}"
        ),
    )


def consolidate(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = resolve_workspace_path(args.plan)
    plan, entries = verify_plan(plan_path)
    registry_path = resolve_workspace_path(plan["registry"]["path"])
    registry = read_json(registry_path)
    registry_hash = plan["registry"]["sha256"]
    entry_by_id = {entry["id"]: entry for entry in entries}
    output = resolve_workspace_path(
        args.output or plan["consolidated_output"]
    )
    if output.exists():
        raise ShardValidationError(
            f"consolidated output already exists; refusing overwrite: {output}"
        )

    source_by_query: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    preserved_run = resolve_workspace_path(plan["preserved"]["source_run"])
    for receipt in plan["preserved"]["queries"]:
        query_id = receipt["query_id"]
        validated = validate_query(
            preserved_run,
            receipt["query_file"],
            registry_hash,
            entry_by_id[query_id],
        )
        for field in (
            "query_file_sha256",
            "records_sha256",
            "page_artifact_file_hash_binding_sha256",
        ):
            if validated[field] != receipt[field]:
                raise ShardValidationError(
                    f"preserved query changed after planning: {query_id}:{field}"
                )
        source_by_query[query_id] = (
            preserved_run,
            receipt["query_file"],
            validated,
        )

    source_manifests: list[dict[str, Any]] = []
    for shard in plan["shards"]:
        run_dir = resolve_workspace_path(shard["output_dir"])
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ShardValidationError(
                f"incomplete shard (manifest missing): {shard['shard_id']}"
            )
        manifest = read_json(manifest_path)
        expected_ids = shard["query_ids"]
        observed_ids = [row.get("query_id") for row in manifest.get("queries", [])]
        if (
            manifest.get("schema") != MANIFEST_SCHEMA
            or manifest.get("status") != "complete"
            or manifest.get("registry_sha256") != registry_hash
            or manifest.get("selected_query_ids") != expected_ids
            or observed_ids != expected_ids
            or len(set(observed_ids)) != len(observed_ids)
        ):
            raise ShardValidationError(
                f"shard manifest partition/status mismatch: {shard['shard_id']}"
            )
        source_manifests.append(
            {
                "shard_id": shard["shard_id"],
                "path": relative_to_root(manifest_path),
                "sha256": sha256_file(manifest_path),
                "query_count": len(observed_ids),
            }
        )
        by_id = {row["query_id"]: row for row in manifest["queries"]}
        for query_id in expected_ids:
            query_file = by_id[query_id].get("query_file")
            validated = validate_query(
                run_dir,
                query_file,
                registry_hash,
                entry_by_id[query_id],
            )
            source_by_query[query_id] = (run_dir, query_file, validated)

    registry_ids = [entry["id"] for entry in entries]
    if set(source_by_query) != set(registry_ids) or len(source_by_query) != 210:
        raise ShardValidationError(
            "validated sources do not cover every registry query exactly once"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.staging-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        query_rows: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        source_receipts: list[dict[str, Any]] = []
        for query_id in registry_ids:
            source_run, query_file, validation = source_by_query[query_id]
            artifact = read_json(source_run / query_file)
            destination_query = stage / "queries" / f"{query_id}.json"
            copy_bound_file(
                source_run / query_file,
                destination_query,
                validation["query_file_sha256"],
            )
            for page in artifact.get("pages", []):
                page_file = page["file"]
                source_page = source_run / page_file
                copy_bound_file(
                    source_page, stage / page_file, sha256_file(source_page)
                )
            copied_validation = validate_query(
                stage,
                f"queries/{query_id}.json",
                registry_hash,
                entry_by_id[query_id],
            )
            if (
                copied_validation["query_file_sha256"]
                != validation["query_file_sha256"]
                or copied_validation[
                    "page_artifact_file_hash_binding_sha256"
                ]
                != validation["page_artifact_file_hash_binding_sha256"]
            ):
                raise ShardValidationError(
                    f"copied query/page binding changed: {query_id}"
                )
            query_rows.append(
                query_manifest_projection(
                    artifact, f"queries/{query_id}.json"
                )
            )
            artifacts.append(artifact)
            source_receipts.append(
                {
                    "query_id": query_id,
                    "source_run": relative_to_root(source_run),
                    **validation,
                }
            )

        record_map: dict[str, dict[str, Any]] = {}
        memberships: list[dict[str, Any]] = []
        for artifact in artifacts:
            for source in artifact["records"]:
                membership = {
                    "query_id": artifact["query_id"],
                    "label": artifact.get("label"),
                    "scoring_category": artifact.get("scoring_category"),
                    "record_kind": artifact.get("record_kind"),
                    "parent_category_id": artifact.get("parent_category_id"),
                    "parent_category_label": artifact.get(
                        "parent_category_label"
                    ),
                    "identifier_text": artifact.get("identifier_text"),
                    "title_alias": artifact.get("title_alias"),
                    "ambiguous_short_act_alias": artifact.get(
                        "ambiguous_short_act_alias"
                    ),
                    "membership_non_dispositive": artifact.get(
                        "membership_non_dispositive"
                    ),
                    "source_paragraph": artifact.get("source_paragraph"),
                    "canonical_key": source["canonical_key"],
                    "source_page": source.get("source_page"),
                    "source_rank": source.get("source_rank"),
                    "result_url": source.get("result_url"),
                }
                memberships.append(membership)
                if source["canonical_key"] not in record_map:
                    record = copy.deepcopy(source)
                    record.pop("source_query_id", None)
                    record.pop("source_page", None)
                    record.pop("source_rank", None)
                    record["matched_scoring_categories"] = []
                    record["matched_monitor_categories"] = []
                    record["matched_coverage_probes"] = []
                    record_map[source["canonical_key"]] = record
                record = record_map[source["canonical_key"]]
                if artifact.get("record_kind") == "coverage_probe":
                    target = record["matched_coverage_probes"]
                elif artifact.get("scoring_category"):
                    target = record["matched_scoring_categories"]
                else:
                    target = record["matched_monitor_categories"]
                target.append(artifact["query_id"])

        records = sorted(
            record_map.values(),
            key=lambda row: natural_key(row["canonical_key"]),
        )
        for record in records:
            for field in (
                "matched_scoring_categories",
                "matched_monitor_categories",
                "matched_coverage_probes",
            ):
                record[field] = sorted(set(record[field]))
        memberships.sort(
            key=lambda row: natural_key(
                f"{row['query_id']}:{row['canonical_key']}"
            )
        )
        gaps = coverage_visibility(records)
        exclusions: list[dict[str, Any]] = []

        records_path = stage / "congress_advanced_records.ndjson"
        memberships_path = stage / "congress_advanced_memberships.ndjson"
        gaps_path = stage / "coverage_gap_ledger.ndjson"
        exclusions_path = stage / "exclusion_ledger.ndjson"
        write_ndjson(records_path, records)
        write_ndjson(memberships_path, memberships)
        write_ndjson(gaps_path, gaps)
        write_ndjson(exclusions_path, exclusions)
        scope = {
            "automated_semantic_exclusions": 0,
            "included_congresses": [116, 117, 118, 119],
            "exact_required_window": ["2020-07-25", "2026-07-25"],
            "boundary_extension": (
                "Pre-2020-07-25 records in the 116th Congress are included "
                "and flagged."
            ),
            "later_review_rule": (
                "Any semantic exclusion must be appended to "
                "exclusion_ledger.ndjson with reviewer, timestamp, "
                "disposition, and reason."
            ),
        }
        scope_path = stage / "scope_and_exclusion_contract.json"
        atomic_write_json(scope_path, scope)

        counts_by_congress = dict(
            sorted(Counter(str(row["congress"]) for row in records).items())
        )
        counts_by_type = dict(
            sorted(Counter(str(row["type"]) for row in records).items())
        )
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "status": "complete",
            "started_at": plan["created_at"],
            "completed_at": utc_now(),
            "primary_instrument": plan["primary_instrument"],
            "quorum_used": False,
            "registry_file": plan["registry"]["path"],
            "registry_sha256": registry_hash,
            "collection_contract": registry["collection_contract"],
            "page_contract": {
                "page_size": 250,
                "page_sort": "documentNumber:asc",
                "exact_reconciliation_required": True,
                "discovery_transport": (
                    "Official Congress.gov result pages"
                ),
            },
            "selected_query_ids": registry_ids,
            "queries": query_rows,
            "union": {
                "unique_record_count": len(records),
                "membership_count": len(memberships),
                "counts_by_congress": counts_by_congress,
                "counts_by_type": counts_by_type,
                "scoring_candidate_count": sum(
                    bool(row["matched_scoring_categories"]) for row in records
                ),
                "monitor_candidate_count": sum(
                    bool(row["matched_monitor_categories"]) for row in records
                ),
                "recall_probe_count": sum(
                    bool(row["matched_coverage_probes"]) for row in records
                ),
                "records_file": records_path.name,
                "records_sha256": continuity.javascript_json_sha256(records),
                "memberships_file": memberships_path.name,
                "memberships_sha256": continuity.javascript_json_sha256(
                    memberships
                ),
                "coverage_visibility": {
                    "recall_only_unclassified_count": 0,
                    "boundary_extension_included_count": sum(
                        row["gap_kind"] == "boundary_extension_included"
                        for row in gaps
                    ),
                    "date_unknown_requires_review_count": sum(
                        row["gap_kind"] == "date_unknown_requires_review"
                        for row in gaps
                    ),
                    "automated_semantic_exclusion_count": 0,
                    "gap_ledger_file": gaps_path.name,
                    "gap_ledger_sha256": continuity.javascript_json_sha256(
                        gaps
                    ),
                    "exclusion_ledger_file": exclusions_path.name,
                    "exclusion_ledger_sha256": (
                        continuity.javascript_json_sha256(exclusions)
                    ),
                    "scope_contract_file": scope_path.name,
                },
            },
            "consolidation": {
                "schema": CONSOLIDATION_SCHEMA,
                "plan_file": relative_to_root(plan_path),
                "plan_sha256": sha256_file(plan_path),
                "source_manifests": source_manifests,
                "preserved_query_count": len(
                    plan["preserved"]["queries"]
                ),
                "sharded_query_count": sum(
                    shard["query_count"] for shard in plan["shards"]
                ),
                "validated_query_count": len(query_rows),
                "query_ids_exact_registry_order": [
                    row["query_id"] for row in query_rows
                ]
                == registry_ids,
                "source_query_receipts_sha256": canonical_hash(
                    source_receipts
                ),
                "no_source_overwrite": True,
                "no_semantic_culling": True,
                "acceptance_passed": True,
            },
        }
        manifest_path = stage / "manifest.json"
        atomic_write_json(manifest_path, manifest)
        (stage / "manifest.json.sha256").write_text(
            f"{sha256_file(manifest_path)}  manifest.json\n",
            encoding="ascii",
        )
        atomic_write_json(
            stage / "consolidation_receipt.json",
            {
                "schema": CONSOLIDATION_SCHEMA,
                "status": "PASS",
                "created_at": utc_now(),
                "plan_sha256": sha256_file(plan_path),
                "registry_sha256": registry_hash,
                "query_count": 210,
                "query_ids_sha256": canonical_hash(registry_ids),
                "query_source_receipts": source_receipts,
                "source_query_receipts_sha256": canonical_hash(
                    source_receipts
                ),
                "output_files": {
                    path.name: {
                        "size": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                    for path in (
                        manifest_path,
                        records_path,
                        memberships_path,
                        gaps_path,
                        exclusions_path,
                        scope_path,
                    )
                },
                "acceptance_passed": True,
            },
        )
        # The staged directory holds ~70 files a scanner may still have open; a
        # directory rename is denied while any of them is held.
        for attempt in range(8):
            try:
                stage.rename(output)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(0.05 * (2**attempt), 0.75))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    plan.add_argument(
        "--preserved-run", type=Path, default=DEFAULT_PRESERVED_RUN
    )
    plan.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    plan.add_argument("--shard-root", type=Path, default=DEFAULT_SHARD_ROOT)
    plan.add_argument(
        "--consolidated", type=Path, default=DEFAULT_CONSOLIDATED
    )
    plan.add_argument("--profile-root", default=r"C:\tmp\congress-idprobe-parallel")
    plan.add_argument("--shards", type=int, default=3)
    plan.add_argument("--delay-ms", type=int, default=1500)
    plan.add_argument("--expected-preserved", type=int, default=55)
    merge = subparsers.add_parser("consolidate")
    merge.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    merge.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "plan":
        plan = build_plan(args)
        print(
            "READY_NOT_LAUNCHED: "
            f"{plan['preserved']['query_count']} preserved + "
            f"{plan['partition']['collection_query_count']} sharded = 210"
        )
    else:
        manifest = consolidate(args)
        print(
            "PASS: exact 210-query consolidation; "
            f"{manifest['union']['unique_record_count']} unique candidates; "
            f"{manifest['union']['membership_count']} memberships"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
