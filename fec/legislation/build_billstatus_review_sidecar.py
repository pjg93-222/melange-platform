#!/usr/bin/env python3
"""Adapt completed official BILLSTATUS receipts into review-only evidence.

The adapter is intentionally downstream of Congress.gov Advanced Search
discovery.  It cannot add candidates, make semantic decisions, ratify review,
or authorize scoring.  Its outputs are a hash-bound, immutable evidence
sidecar compatible with the existing sponsorship/measure-metadata consumers.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable

try:
    from lane_file_io import open_ndjson
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson


ROOT = Path(__file__).resolve().parents[2]
AUDIT_ROOT = (
    ROOT / "audits" / "legfix5" / "billstatus_bulk_enrichment"
)
DEFAULT_PRESYNC = (
    ROOT
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "presync_current"
    / "checkpoint.json"
)
SCHEMA = "govinfo-billstatus-review-sidecar-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractError(RuntimeError):
    """Raised when the staged evidence chain is incomplete or inconsistent."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def html_to_text(value: Any) -> str | None:
    source = str(value or "").strip()
    if not source:
        return None
    parser = _TextExtractor()
    parser.feed(source)
    parser.close()
    return compact(html.unescape(" ".join(parser.parts)))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"Expected a JSON object: {path}")
    return value


def iter_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    with open_ndjson(path) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ContractError(
                    f"Expected object at {path}:{line_number}"
                )
            yield value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            count += 1
    return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_item(
    path: Path, *, record_count: int | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if record_count is not None:
        result["record_count"] = record_count
    return result


def require_file_item(item: dict[str, Any], label: str) -> Path:
    path = Path(str(item.get("path") or "")).resolve()
    expected = str(item.get("sha256") or "").casefold()
    if not path.is_file():
        raise ContractError(f"{label} is missing: {path}")
    if not SHA256_RE.fullmatch(expected):
        raise ContractError(f"{label} lacks an exact SHA-256")
    observed = sha256_file(path)
    if observed != expected:
        raise ContractError(
            f"{label} SHA-256 mismatch: expected {expected}, got {observed}"
        )
    return path


def default_input_paths() -> tuple[Path, Path, Path]:
    checkpoint = read_json(DEFAULT_PRESYNC)
    coverage = checkpoint.get("coverage") or {}
    bundle = coverage.get("billstatus_snapshot_bundle") or {}
    evidence = coverage.get("billstatus_enrichment_completeness_evidence") or {}
    plan = Path(str(bundle.get("snapshot_plan") or "")).resolve()
    worker = Path(
        str((evidence.get("worker_manifest") or {}).get("path") or "")
    ).resolve()
    completeness = Path(
        str((evidence.get("audit") or {}).get("path") or "")
    ).resolve()
    for path, label in (
        (plan, "snapshot plan"),
        (worker, "worker manifest"),
        (completeness, "completeness audit"),
    ):
        if not path.is_file():
            raise ContractError(
                f"Current pre-sync checkpoint does not identify a valid "
                f"{label}: {path}"
            )
    return plan, worker, completeness


def title_rank(title_type: Any) -> tuple[int, str]:
    value = compact(title_type).casefold()
    if value == "official title as introduced":
        rank = 0
    elif "official title" in value and "introduced" in value:
        rank = 1
    elif "official title" in value:
        rank = 2
    elif value == "display title":
        rank = 3
    elif "short title" in value and "introduced" in value:
        rank = 4
    elif "short title" in value:
        rank = 5
    else:
        rank = 6
    return rank, value


def select_official_title(
    titles: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    candidates = [
        (title_rank(row.get("title_type")), index, row)
        for index, row in enumerate(titles)
        if compact(row.get("title"))
    ]
    if not candidates:
        return None, None
    _, _, selected = min(candidates)
    return compact(selected["title"]), compact(selected.get("title_type"))


def select_summary(
    summaries: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    candidates = [
        row
        for row in summaries
        if html_to_text(row.get("text"))
    ]
    if not candidates:
        return None, None
    selected = max(
        candidates,
        key=lambda row: (
            compact(row.get("update_date")),
            compact(row.get("action_date")),
            compact(row.get("version_code")),
        ),
    )
    return html_to_text(selected.get("text")), selected


def role_counts(record: dict[str, Any]) -> dict[str, int]:
    relationships = [
        *record.get("sponsors", []),
        *record.get("cosponsors", []),
    ]
    return {
        "Sponsor": sum(
            row.get("relationship_role") == "Sponsor"
            for row in relationships
        ),
        "Original Cosponsor": sum(
            row.get("relationship_role") == "Original Cosponsor"
            for row in relationships
        ),
        "Later Cosponsor": sum(
            row.get("relationship_role") in {"Cosponsor", "Later Cosponsor"}
            and row.get("original_cosponsor") is False
            for row in relationships
        ),
        "withdrawn": sum(
            row.get("relationship_status") == "withdrawn"
            for row in relationships
        ),
        "unresolved_role": sum(
            row.get("relationship_role")
            not in {
                "Sponsor",
                "Original Cosponsor",
                "Cosponsor",
                "Later Cosponsor",
            }
            for row in relationships
        ),
    }


def make_edge(
    candidate: dict[str, Any],
    parsed: dict[str, Any],
    relationship: dict[str, Any],
) -> dict[str, Any]:
    explicit_role = relationship.get("relationship_role")
    if explicit_role == "Sponsor":
        role = "Sponsor"
        original_cosponsor = None
    elif explicit_role == "Original Cosponsor":
        role = "Cosponsor"
        original_cosponsor = True
    elif (
        explicit_role in {"Cosponsor", "Later Cosponsor"}
        and relationship.get("original_cosponsor") is False
    ):
        role = "Cosponsor"
        original_cosponsor = False
        explicit_role = "Later Cosponsor"
    else:
        raise ContractError(
            f"Unresolved official relationship role for "
            f"{parsed['canonical_key']}: {explicit_role!r}"
        )
    provenance = parsed.get("provenance") or {}
    return {
        "canonical_key": parsed["canonical_key"],
        "congress": parsed["identity"]["congress"],
        "type": parsed["identity"]["type"],
        "number": parsed["identity"]["number"],
        "display_number": candidate.get("display_number"),
        "title": candidate.get("title") or candidate.get("purpose"),
        "role": role,
        "explicit_role": explicit_role,
        "source_relationship_role": relationship.get(
            "relationship_role"
        ),
        "member": relationship.get("full_name"),
        "member_url": None,
        "bioguide_id": relationship.get("bioguide_id"),
        "chamber_label": None,
        "party": relationship.get("party"),
        "state": relationship.get("state"),
        "district": relationship.get("district"),
        "sponsorship_date": relationship.get("sponsorship_date"),
        "original_cosponsor": original_cosponsor,
        "relationship_status": relationship.get("relationship_status"),
        "withdrawn_date": relationship.get("withdrawn_date"),
        "withdrawal_explanation": None,
        "role_provenance": relationship.get("role_provenance"),
        "role_provenance_complete": relationship.get(
            "role_provenance_complete"
        ),
        "source_url": provenance.get("source_url"),
        "official_source_sha256": provenance.get("source_sha256"),
        "evidence_scope": "review_prefill_only",
        "membership_disposition_authority": False,
        "ratification_authority": False,
        "scoring_authority": False,
    }


def validate_input_chain(
    plan_path: Path,
    worker_path: Path,
    completeness_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = read_json(plan_path)
    worker = read_json(worker_path)
    completeness = read_json(completeness_path)
    if plan.get("schema") != "govinfo-billstatus-enrichment-fetch-plan-v1":
        raise ContractError("Unexpected snapshot plan schema")
    if (
        plan.get("primary_discovery")
        != "Congress.gov Advanced Search - Legislation"
        or plan.get("quorum_used") is not False
    ):
        raise ContractError(
            "The snapshot is not bound exclusively to Congress.gov "
            "Advanced Search discovery"
        )
    if worker.get("status") != "COMPLETE":
        raise ContractError("BILLSTATUS worker is not complete")
    if worker.get("official_source_only") is not True:
        raise ContractError("BILLSTATUS worker is not official-source-only")
    for forbidden in (
        "production_write_performed",
        "sync_or_promotion_performed",
        "candidate_creation_performed",
        "semantic_exclusion_performed",
    ):
        if worker.get(forbidden) is not False:
            raise ContractError(f"Worker violated staged-only contract: {forbidden}")
    if sha256_file(plan_path) != (worker.get("plan") or {}).get("sha256"):
        raise ContractError("Worker is not bound to the selected snapshot plan")
    if completeness.get("status") != "PASS_WITH_VISIBLE_GAPS":
        raise ContractError("Completeness audit did not pass with visible gaps")
    if (
        (completeness.get("inputs") or {})
        .get("worker_manifest", {})
        .get("sha256")
        != sha256_file(worker_path)
    ):
        raise ContractError("Completeness audit worker hash is stale")
    if (
        (completeness.get("inputs") or {})
        .get("snapshot_plan", {})
        .get("sha256")
        != sha256_file(plan_path)
    ):
        raise ContractError("Completeness audit plan hash is stale")
    closure = completeness.get("closure") or {}
    required_closure = (
        "candidate_partition_closed",
        "planned_key_set_equals_receipt_key_set",
        "all_raw_hashes_validated",
        "all_parsed_hashes_validated",
        "all_xml_identities_validated",
    )
    if any(closure.get(field) is not True for field in required_closure):
        raise ContractError("Completeness audit lacks exact hash/key closure")
    for forbidden in (
        "production_write_performed",
        "sync_or_promotion_performed",
        "candidate_creation_performed",
        "candidate_exclusion_performed",
        "automatic_scoring_performed",
    ):
        if completeness.get(forbidden) is not False:
            raise ContractError(
                f"Completeness evidence violated staged-only contract: {forbidden}"
            )
    if completeness.get("absence_is_neutral") is not True:
        raise ContractError("Completeness evidence treats an absence as dispositive")
    for label, item in (completeness.get("outputs") or {}).items():
        require_file_item(item, f"completeness output {label}")
    return plan, worker, completeness


def validate_existing_sidecar(out_dir: Path, expected_id: str) -> dict[str, Any]:
    manifest_path = out_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("sidecar_id") != expected_id:
        raise ContractError(f"Existing sidecar identity mismatch: {out_dir}")
    for item in (manifest.get("outputs") or {}).values():
        require_file_item(item, "existing sidecar output")
    return manifest


def build_sidecar(
    *,
    plan_path: Path,
    worker_path: Path,
    completeness_path: Path,
    out_root: Path,
    pointer_path: Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    plan_path = plan_path.resolve()
    worker_path = worker_path.resolve()
    completeness_path = completeness_path.resolve()
    out_root = out_root.resolve()
    plan, worker, completeness = validate_input_chain(
        plan_path, worker_path, completeness_path
    )
    producer_path = Path(__file__).resolve()
    bindings = {
        "schema": SCHEMA,
        "snapshot_plan_sha256": sha256_file(plan_path),
        "worker_manifest_sha256": sha256_file(worker_path),
        "completeness_audit_sha256": sha256_file(completeness_path),
        "producer_sha256": sha256_file(producer_path),
    }
    sidecar_id = hashlib.sha256(
        json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    out_dir = out_root / sidecar_id
    if out_dir.exists():
        manifest = validate_existing_sidecar(out_dir, sidecar_id)
        created = False
    else:
        candidate_file = require_file_item(
            plan["inputs"]["advanced_search_records"],
            "Advanced Search candidate corpus",
        )
        candidates = {
            row["canonical_key"]: row for row in iter_ndjson(candidate_file)
        }
        if len(candidates) != plan["candidate_partition"]["all"]:
            raise ContractError("Candidate corpus contains duplicate/missing keys")
        task_keys = {
            row["canonical_key"] for row in plan.get("record_tasks", [])
        }
        unsupported_keys = {
            row["canonical_key"]
            for row in plan.get("unsupported_candidates", [])
        }
        if task_keys | unsupported_keys != set(candidates):
            raise ContractError("Plan does not partition the exact candidate set")
        if task_keys & unsupported_keys:
            raise ContractError("Plan candidate partitions overlap")

        receipt_by_key: dict[str, dict[str, Any]] = {}
        for receipt in worker.get("record_receipts", []):
            key = receipt.get("canonical_key")
            if key in receipt_by_key:
                raise ContractError(f"Duplicate worker receipt: {key}")
            receipt_by_key[key] = receipt
        if set(receipt_by_key) != task_keys:
            raise ContractError("Worker receipts do not equal planned top-level keys")

        tmp_dir = out_root / f".{sidecar_id}.tmp.{os.getpid()}"
        tmp_dir.mkdir(parents=True, exist_ok=False)
        metadata_rows: list[dict[str, Any]] = []
        edge_rows: list[dict[str, Any]] = []
        aggregate_roles = {
            "Sponsor": 0,
            "Original Cosponsor": 0,
            "Later Cosponsor": 0,
            "withdrawn": 0,
            "unresolved_role": 0,
        }
        for key in sorted(task_keys):
            receipt = receipt_by_key[key]
            parsed_path = require_file_item(
                {
                    "path": receipt.get("parsed_path"),
                    "sha256": receipt.get("parsed_sha256"),
                },
                f"parsed receipt {key}",
            )
            parsed = read_json(parsed_path)
            if parsed.get("canonical_key") != key:
                raise ContractError(f"Parsed identity mismatch for {key}")
            candidate = candidates[key]
            titles = parsed.get("titles") or []
            summaries = parsed.get("summaries") or []
            official_title, official_title_type = select_official_title(titles)
            summary, selected_summary = select_summary(summaries)
            counts = role_counts(parsed)
            for name, count in counts.items():
                aggregate_roles[name] += count
            provenance = parsed.get("provenance") or {}
            sponsors = [
                {
                    "bioguide_id": row.get("bioguide_id"),
                    "full_name": row.get("full_name"),
                    "relationship_role": row.get("relationship_role"),
                    "relationship_status": row.get("relationship_status"),
                    "role_provenance": row.get("role_provenance"),
                    "source_url": provenance.get("source_url"),
                }
                for row in parsed.get("sponsors", [])
            ]
            metadata_rows.append(
                {
                    "canonical_key": key,
                    "official_title": official_title,
                    "official_title_type": official_title_type,
                    "official_title_selection_rule": (
                        "prefer_official_title_as_introduced_then_official_"
                        "title_then_display_title_then_short_title"
                    ),
                    "all_official_titles": titles,
                    "summary": summary,
                    "summary_source": selected_summary,
                    "summary_selection_rule": (
                        "latest_nonempty_official_summary_by_update_action_version"
                    ),
                    "canonical_url": candidate.get("canonical_url"),
                    "official_source_url": provenance.get("source_url"),
                    "official_source_sha256": provenance.get("source_sha256"),
                    "parsed_evidence_sha256": receipt.get("parsed_sha256"),
                    "relationship_role_counts": counts,
                    "official_sponsors": sponsors,
                    "relationship_evidence_available": True,
                    "relationship_evidence_scope": "review_prefill_only",
                    "membership_disposition_authority": False,
                    "ratification_authority": False,
                    "scoring_authority": False,
                }
            )
            for relationship in [
                *parsed.get("sponsors", []),
                *parsed.get("cosponsors", []),
            ]:
                edge_rows.append(
                    make_edge(candidate, parsed, relationship)
                )

        expected_roles = completeness.get("relationship_counts") or {}
        comparisons = {
            "Sponsor": "sponsor_relationships",
            "Original Cosponsor": "original_cosponsor_relationships",
            "Later Cosponsor": "later_cosponsor_relationships",
            "withdrawn": "withdrawn_relationships",
            "unresolved_role": "unresolved_role_relationships",
        }
        for actual_name, expected_name in comparisons.items():
            if aggregate_roles[actual_name] != expected_roles.get(expected_name):
                raise ContractError(
                    f"Role count drift for {actual_name}: "
                    f"{aggregate_roles[actual_name]} != "
                    f"{expected_roles.get(expected_name)}"
                )

        edge_rows.sort(
            key=lambda row: (
                row["canonical_key"],
                row["role"],
                row.get("bioguide_id") or "",
                row.get("relationship_status") or "",
                row.get("sponsorship_date") or "",
            )
        )
        metadata_rows.sort(key=lambda row: row["canonical_key"])

        unresolved_rows = [
            {
                "canonical_key": key,
                "disposition": (
                    "retained_unresolved_no_standalone_top_level_package"
                ),
                "reason": (
                    "Nested amendments have no supported standalone GovInfo "
                    "BILLSTATUS package in this adapter."
                ),
                "candidate_retained": True,
                "absence_is_neutral": True,
                "membership_disposition_authority": False,
                "ratification_authority": False,
                "scoring_authority": False,
                "plan_sha256": bindings["snapshot_plan_sha256"],
            }
            for key in sorted(unsupported_keys)
        ]

        metadata_path = tmp_dir / "measure_metadata.ndjson"
        edges_path = tmp_dir / "member_measure_edges.ndjson"
        unresolved_path = (
            tmp_dir / "relationship_verification_ledger.ndjson"
        )
        gaps_path = tmp_dir / "source_gap_ledger.ndjson"
        metadata_count = write_ndjson(metadata_path, metadata_rows)
        edge_count = write_ndjson(edges_path, edge_rows)
        unresolved_count = write_ndjson(unresolved_path, unresolved_rows)

        def all_gap_rows() -> Iterable[dict[str, Any]]:
            for label, item in sorted(completeness["outputs"].items()):
                source_path = require_file_item(
                    item, f"completeness output {label}"
                )
                for row in iter_ndjson(source_path):
                    yield {
                        **row,
                        "source_gap_ledger": label,
                        "source_gap_ledger_sha256": item["sha256"],
                        "candidate_retained": True,
                        "absence_is_neutral": True,
                        "membership_disposition_authority": False,
                        "ratification_authority": False,
                        "scoring_authority": False,
                    }

        gap_count = write_ndjson(gaps_path, all_gap_rows())
        if gap_count != completeness["gap_ledger_counts"]["all_visible_gaps"]:
            raise ContractError("Sidecar gap ledger is not lossless")

        outputs = {
            "edges": file_item(edges_path, record_count=edge_count),
            "verification_ledger": file_item(
                unresolved_path, record_count=unresolved_count
            ),
            "measure_metadata": file_item(
                metadata_path, record_count=metadata_count
            ),
            "source_gap_ledger": file_item(
                gaps_path, record_count=gap_count
            ),
        }
        # The files are about to move atomically from the private build
        # directory to the immutable content-addressed directory.  Bind the
        # manifest to those final locations while retaining hashes computed
        # from the exact bytes being moved.
        for item in outputs.values():
            item["path"] = str((out_dir / Path(item["path"]).name).resolve())
        manifest = {
            "schema": "congress-sponsorship-enrichment-manifest-v1",
            "adapter_schema": SCHEMA,
            "status": "complete",
            "sidecar_id": sidecar_id,
            "snapshot_only": True,
            "partial_discovery_snapshot": True,
            "official_source_only": True,
            "primary_source": (
                "U.S. Government Publishing Office / Congress.gov BILLSTATUS"
            ),
            "review_prefill_only": True,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
            "candidate_creation_performed": False,
            "semantic_exclusion_performed": False,
            "ratified_decision_performed": False,
            "automatic_scoring_performed": False,
            "membership_disposition_authority": False,
            "ratification_authority": False,
            "scoring_authority": False,
            "absence_is_neutral": True,
            "acceptance_passed": False,
            "all_relationships_reconciled": False,
            "top_level_relationship_evidence_complete": True,
            "amendment_relationship_support": False,
            "bindings": {
                **bindings,
                "snapshot_plan": file_item(plan_path),
                "worker_manifest": file_item(worker_path),
                "completeness_audit": file_item(completeness_path),
                "advanced_search_candidate_corpus": file_item(candidate_file),
                "completeness_gap_sources": completeness["outputs"],
            },
            "selected_record_count": len(candidates),
            "top_level_metadata_count": metadata_count,
            "edge_count": edge_count,
            "edge_counts": {
                "sponsors": aggregate_roles["Sponsor"],
                "original_cosponsors": aggregate_roles[
                    "Original Cosponsor"
                ],
                "later_cosponsors": aggregate_roles["Later Cosponsor"],
                "withdrawn_cosponsors": aggregate_roles["withdrawn"],
            },
            "unresolved_measure_count": unresolved_count,
            "visible_gap_count": gap_count,
            "edges_file": edges_path.name,
            "edges_sha256": outputs["edges"]["sha256"],
            "verification_ledger_file": unresolved_path.name,
            "verification_ledger_sha256": outputs[
                "verification_ledger"
            ]["sha256"],
            "measure_metadata_file": metadata_path.name,
            "measure_metadata_sha256": outputs[
                "measure_metadata"
            ]["sha256"],
            "source_gap_ledger_file": gaps_path.name,
            "source_gap_ledger_sha256": outputs[
                "source_gap_ledger"
            ]["sha256"],
            "outputs": outputs,
        }
        write_json(tmp_dir / "manifest.json", manifest)
        out_root.mkdir(parents=True, exist_ok=True)
        tmp_dir.rename(out_dir)
        created = True

    if pointer_path is not None:
        pointer_path = pointer_path.resolve()
        write_json(
            pointer_path,
            {
                "schema": "govinfo-billstatus-review-sidecar-pointer-v1",
                "sidecar_id": sidecar_id,
                "sidecar_dir": str(out_dir),
                "manifest": file_item(out_dir / "manifest.json"),
                "review_prefill_only": True,
                "sync_or_promotion_performed": False,
            },
        )
    return out_dir, manifest, created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--worker-manifest", type=Path)
    parser.add_argument("--completeness", type=Path)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=AUDIT_ROOT / "review_sidecars",
    )
    parser.add_argument(
        "--pointer",
        type=Path,
        default=AUDIT_ROOT / "review_sidecar_current.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    defaults = (
        default_input_paths()
        if not (args.plan and args.worker_manifest and args.completeness)
        else None
    )
    plan = args.plan or defaults[0]
    worker = args.worker_manifest or defaults[1]
    completeness = args.completeness or defaults[2]
    out_dir, manifest, created = build_sidecar(
        plan_path=plan,
        worker_path=worker,
        completeness_path=completeness,
        out_root=args.out_root,
        pointer_path=args.pointer,
    )
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "created": created,
                "sidecar_id": manifest["sidecar_id"],
                "sidecar_dir": str(out_dir),
                "top_level_metadata_count": manifest[
                    "top_level_metadata_count"
                ],
                "edge_count": manifest["edge_count"],
                "unresolved_measure_count": manifest[
                    "unresolved_measure_count"
                ],
                "visible_gap_count": manifest["visible_gap_count"],
                "review_prefill_only": True,
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
