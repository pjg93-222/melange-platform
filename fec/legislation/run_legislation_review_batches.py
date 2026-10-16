# -*- coding: utf-8 -*-
"""Create resumable, deterministic review batches from staged membership triage.

This is an execution/checkpoint layer, not a semantic classifier.  It invokes
``build_legislation_membership_triage.py`` against complete merged/enrichment
inputs, then shards the resulting immutable ledger by:

    review bucket -> category -> canonical measure key

Every shard has a deterministic receipt.  ``--resume`` reuses only shards whose
bytes and checkpoint exactly match the current triage generation; missing or
corrupt shards are rebuilt.  Changed source inputs, a changed batch size, extra
shards, incomplete enrichments, or any machine proposal that looks ratified
causes a fail-closed error.

No output is written to production or to the user decision ledger.  Machine
proposals remain ``disposition="unresolved"`` and
``adjudication_state="machine_proposed"`` in all review views.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Iterable

from legislation import build_legislation_membership_triage as TRIAGE


SCHEMA = "legislation-review-batches-v1"
DEFAULT_BATCH_SIZE = 250
VIEW_NAMES = (
    "all_memberships.ndjson",
    "ratified_inclusions.ndjson",
    "ratified_exclusions.ndjson",
    "unresolved_memberships.ndjson",
    "machine_proposals.ndjson",
    "proposed_inclusions.ndjson",
    "proposed_exclusions.ndjson",
)


class BatchError(RuntimeError):
    """A fail-closed batching, checkpoint, or completeness violation."""


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"cannot read JSON {path}: {exc}") from exc


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise BatchError(f"required NDJSON file is absent: {path}")
    rows = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BatchError(f"invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise BatchError(f"non-object row at {path}:{line_number}")
            rows.append(row)
    return rows


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def ndjson_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows
    ).encode("utf-8")


STALE_TEMP_RE = re.compile(r"^\..+\.tmp-\d+$")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
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


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, json_bytes(value))


def slug(value: Any) -> str:
    text = normalize(value).casefold()
    readable = re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "empty"
    return f"{readable[:64]}-{sha256_bytes(text.encode('utf-8'))[:8]}"


def measure_slug(value: Any) -> str:
    key = TRIAGE.canonical_key(value)
    return key.replace(":", "-")


def membership_identity(row: dict[str, Any]) -> str:
    return f"{TRIAGE.canonical_key(row.get('canonical_key'))}|{normalize(row.get('query_id'))}"


def assert_output_path(
    path: Path, workspace_root: Path, *, resume: bool
) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == workspace_root.resolve():
        raise BatchError("review batch output cannot be the workspace root")
    if not any(
        re.search(r"(audit|stag|review|batch)", part, re.I)
        for part in resolved.parts
    ):
        raise BatchError(
            "output must explicitly be an audit/staging/review/batch directory"
        )
    if resume and not resolved.is_dir():
        raise BatchError("--resume requires an existing batch output directory")
    if not resume and resolved.exists():
        raise BatchError(
            f"fresh batch output already exists; use --resume or a new directory: {resolved}"
        )
    return resolved


def validate_source_inputs(
    *,
    run_dir: Path,
    registry_path: Path,
    relationships_dir: Path,
    text_dir: Path,
    article_dir: Path,
    review_ledger_path: Path,
) -> dict[str, Any]:
    """Use the triage loader as the single complete-input gate."""
    try:
        return TRIAGE.load_inputs(
            run_dir=run_dir.resolve(),
            registry_path=registry_path.resolve(),
            relationships_dir=relationships_dir.resolve(),
            text_dir=text_dir.resolve(),
            article_dir=article_dir.resolve(),
            review_ledger_path=review_ledger_path.resolve(),
        )
    except TRIAGE.TriageError as exc:
        raise BatchError(f"final merged/enrichment inputs are not ready: {exc}") from exc


def current_input_hashes(inputs: dict[str, Any]) -> dict[str, str]:
    return {
        name: sha256_file(path)
        for name, path in sorted(inputs["paths"].items())
    }


def validate_triage_generation(
    triage_dir: Path, inputs: dict[str, Any]
) -> dict[str, Any]:
    manifest_path = triage_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema") != TRIAGE.SCHEMA:
        raise BatchError("staged triage generation has the wrong schema")
    if manifest.get("staging_only") is not True:
        raise BatchError("triage generation is not marked staging-only")
    if manifest.get("machine_proposals_are_scoring_eligible") is not False:
        raise BatchError("triage generation permits machine proposals to score")
    expected_hashes = current_input_hashes(inputs)
    observed_inputs = manifest.get("inputs") or {}
    for name, expected_hash in expected_hashes.items():
        observed = (observed_inputs.get(name) or {}).get("sha256")
        if observed != expected_hash:
            raise BatchError(
                f"source input changed since staged triage generation: {name}"
            )
    for relative, receipt in (manifest.get("outputs") or {}).items():
        path = triage_dir / relative
        if not path.is_file():
            raise BatchError(f"triage output is absent: {relative}")
        if path.stat().st_size != receipt.get("size"):
            raise BatchError(f"triage output size mismatch: {relative}")
        if sha256_file(path) != receipt.get("sha256"):
            raise BatchError(f"triage output hash mismatch: {relative}")
    return manifest


def prepare_triage_generation(
    *,
    out_dir: Path,
    inputs: dict[str, Any],
    run_dir: Path,
    registry_path: Path,
    relationships_dir: Path,
    text_dir: Path,
    article_dir: Path,
    review_ledger_path: Path,
    workspace_root: Path,
    resume: bool,
) -> tuple[Path, dict[str, Any]]:
    triage_dir = out_dir / "triage_generation"
    if triage_dir.exists():
        if not resume:
            raise BatchError("unexpected pre-existing triage generation")
        return triage_dir, validate_triage_generation(triage_dir, inputs)
    try:
        TRIAGE.build(
            run_dir=run_dir,
            registry_path=registry_path,
            relationships_dir=relationships_dir,
            text_dir=text_dir,
            article_dir=article_dir,
            review_ledger_path=review_ledger_path,
            out_dir=triage_dir,
            workspace_root=workspace_root,
        )
    except TRIAGE.TriageError as exc:
        raise BatchError(f"staged membership triage failed: {exc}") from exc
    return triage_dir, validate_triage_generation(triage_dir, inputs)


def validate_non_scoring_contract(
    ledger: list[dict[str, Any]], proposals: list[dict[str, Any]]
) -> None:
    for row in ledger:
        if row.get("triage_status") == TRIAGE.MACHINE_STATE:
            if row.get("disposition") != "unresolved":
                raise BatchError(
                    f"machine-proposed triage row is not unresolved: {membership_identity(row)}"
                )
            if row.get("adjudication_state") != TRIAGE.MACHINE_STATE:
                raise BatchError(
                    f"machine-proposed triage row has wrong adjudication state: "
                    f"{membership_identity(row)}"
                )
        if row.get("scoring_eligible_from_triage") is not False:
            raise BatchError(
                f"triage row can score: {membership_identity(row)}"
            )
    for proposal in proposals:
        if (
            proposal.get("disposition") != "unresolved"
            or proposal.get("adjudication_state") != TRIAGE.MACHINE_STATE
            or proposal.get("scoring_eligible") is not False
            or proposal.get("requires_human_ratification") is not True
        ):
            raise BatchError(
                "machine proposal violates non-scoring contract: "
                f"{membership_identity(proposal)}"
            )


def expected_shards(
    ledger: list[dict[str, Any]],
    batch_size: int,
    source_generation_id: str,
) -> list[dict[str, Any]]:
    if batch_size <= 0 or batch_size > 10000:
        raise BatchError("batch size must be between 1 and 10000")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in ledger:
        bucket = normalize(row.get("triage_bucket"))
        category = normalize(row.get("query_id"))
        if bucket not in TRIAGE.BUCKETS:
            raise BatchError(f"unknown review bucket in triage ledger: {bucket!r}")
        if not category:
            raise BatchError("triage row lacks query_id/category")
        grouped[(bucket, category)].append(row)

    shards = []
    for (bucket, category), rows in sorted(grouped.items()):
        rows.sort(
            key=lambda row: (
                TRIAGE.canonical_key(row["canonical_key"]),
                membership_identity(row),
            )
        )
        for offset in range(0, len(rows), batch_size):
            batch = rows[offset : offset + batch_size]
            index = offset // batch_size + 1
            first_key = TRIAGE.canonical_key(batch[0]["canonical_key"])
            last_key = TRIAGE.canonical_key(batch[-1]["canonical_key"])
            content = ndjson_bytes(batch)
            content_hash = sha256_bytes(content)
            shard_id = (
                f"{slug(bucket)}--{slug(category)}--{index:05d}--"
                f"{measure_slug(first_key)}--{measure_slug(last_key)}"
            )
            relative = (
                Path("shards")
                / slug(bucket)
                / slug(category)
                / f"{shard_id}.ndjson"
            )
            checkpoint_relative = (
                Path("checkpoints") / f"{shard_id}.checkpoint.json"
            )
            identities = [membership_identity(row) for row in batch]
            checkpoint = {
                "schema": SCHEMA,
                "source_triage_generation_id": source_generation_id,
                "shard_id": shard_id,
                "review_bucket": bucket,
                "category": category,
                "batch_index": index,
                "batch_size": batch_size,
                "row_count": len(batch),
                "first_measure_key": first_key,
                "last_measure_key": last_key,
                "membership_keys": identities,
                "content_sha256": content_hash,
                "complete": True,
                "machine_proposals_ratified": 0,
            }
            shards.append(
                {
                    "shard_id": shard_id,
                    "relative": relative,
                    "checkpoint_relative": checkpoint_relative,
                    "content": content,
                    "content_sha256": content_hash,
                    "checkpoint": checkpoint,
                    "rows": batch,
                }
            )
    return shards


def checkpoint_valid(out_dir: Path, shard: dict[str, Any]) -> bool:
    path = out_dir / shard["relative"]
    checkpoint_path = out_dir / shard["checkpoint_relative"]
    if not path.is_file() or not checkpoint_path.is_file():
        return False
    if sha256_file(path) != shard["content_sha256"]:
        return False
    try:
        observed = read_json(checkpoint_path)
    except BatchError:
        return False
    return observed == shard["checkpoint"]


def write_shards(
    *,
    out_dir: Path,
    shards: list[dict[str, Any]],
    source_generation_id: str,
    batch_size: int,
    resume: bool,
) -> dict[str, int]:
    expected_paths = {
        shard["relative"].as_posix() for shard in shards
    } | {
        shard["checkpoint_relative"].as_posix() for shard in shards
    }
    existing_paths = set()
    stale_temp_removed = 0
    for parent_name in ("shards", "checkpoints"):
        parent = out_dir / parent_name
        if parent.exists():
            for path in parent.rglob("*"):
                if not path.is_file():
                    continue
                if STALE_TEMP_RE.match(path.name):
                    # Leftover from a hard kill inside atomic_write; the
                    # shard/checkpoint it staged is rebuilt or revalidated.
                    try:
                        path.unlink()
                        stale_temp_removed += 1
                    except OSError:
                        pass
                    continue
                existing_paths.add(path.relative_to(out_dir).as_posix())
    unexpected = sorted(existing_paths - expected_paths)
    if unexpected:
        raise BatchError(
            "unexpected shard/checkpoint files prevent safe resume: "
            + ", ".join(unexpected[:5])
        )

    state_path = out_dir / "state.partial.json"
    if state_path.exists():
        state = read_json(state_path)
        if (
            state.get("schema") != SCHEMA
            or state.get("source_triage_generation_id")
            != source_generation_id
            or state.get("batch_size") != batch_size
        ):
            raise BatchError("partial-state configuration differs from this resume")
    elif resume and existing_paths:
        # Existing verified checkpoint files remain sufficient even when a
        # crash happened just before the partial state was replaced.
        state = {}
    else:
        state = {}

    reused = rebuilt = 0
    completed = []
    for shard in shards:
        if resume and checkpoint_valid(out_dir, shard):
            reused += 1
        else:
            atomic_write(out_dir / shard["relative"], shard["content"])
            atomic_json(
                out_dir / shard["checkpoint_relative"], shard["checkpoint"]
            )
            rebuilt += 1
        completed.append(shard["shard_id"])
        atomic_json(
            state_path,
            {
                "schema": SCHEMA,
                "source_triage_generation_id": source_generation_id,
                "batch_size": batch_size,
                "expected_shard_count": len(shards),
                "completed_shard_ids": completed,
            },
        )
    try:
        state_path.unlink()
    except FileNotFoundError:
        pass
    runtime = {"reused_shards": reused, "rebuilt_shards": rebuilt}
    if stale_temp_removed:
        runtime["stale_temp_files_removed"] = stale_temp_removed
    return runtime


def build_views(
    ledger: list[dict[str, Any]], proposals: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    ledger_sorted = sorted(
        ledger,
        key=lambda row: (
            normalize(row.get("triage_bucket")),
            normalize(row.get("query_id")),
            TRIAGE.canonical_key(row.get("canonical_key")),
        ),
    )
    proposal_sorted = sorted(
        proposals,
        key=lambda row: (
            normalize(row.get("proposed_disposition")),
            normalize(row.get("query_id")),
            TRIAGE.canonical_key(row.get("canonical_key")),
        ),
    )
    ratified = [
        row for row in ledger_sorted if row.get("triage_status") == "ratified_existing"
    ]
    views = {
        "all_memberships.ndjson": ledger_sorted,
        "ratified_inclusions.ndjson": [
            row for row in ratified if row.get("disposition") == "include"
        ],
        "ratified_exclusions.ndjson": [
            row
            for row in ratified
            if row.get("disposition") == "exclude_false_positive"
        ],
        "unresolved_memberships.ndjson": [
            row
            for row in ledger_sorted
            if row.get("disposition") == "unresolved"
        ],
        "machine_proposals.ndjson": proposal_sorted,
        "proposed_inclusions.ndjson": [
            row
            for row in proposal_sorted
            if row.get("proposed_disposition") == "include"
        ],
        "proposed_exclusions.ndjson": [
            row
            for row in proposal_sorted
            if row.get("proposed_disposition") == "exclude_false_positive"
        ],
    }
    return views


def write_views(out_dir: Path, views: dict[str, list[dict[str, Any]]]) -> None:
    if set(views) != set(VIEW_NAMES):
        raise BatchError("review view set is incomplete")
    for name in VIEW_NAMES:
        atomic_write(out_dir / "views" / name, ndjson_bytes(views[name]))


def completeness(
    *,
    ledger: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    shards: list[dict[str, Any]],
    views: dict[str, list[dict[str, Any]]],
    triage_manifest: dict[str, Any],
    out_dir: Path,
    batch_size: int,
) -> dict[str, Any]:
    source_identities = [membership_identity(row) for row in ledger]
    observed_identities = [
        membership_identity(row)
        for shard in shards
        for row in shard["rows"]
    ]
    source_counter = collections.Counter(source_identities)
    observed_counter = collections.Counter(observed_identities)
    missing = sorted((source_counter - observed_counter).elements())
    extra = sorted((observed_counter - source_counter).elements())
    duplicates = sorted(
        identity for identity, count in observed_counter.items() if count != 1
    )
    if missing or extra or duplicates:
        raise BatchError(
            f"shard completeness failed: missing={len(missing)}, "
            f"extra={len(extra)}, duplicates={len(duplicates)}"
        )
    if len(source_counter) != len(ledger):
        raise BatchError("source triage ledger contains duplicate membership keys")

    proposal_identities = [membership_identity(row) for row in proposals]
    orphan_proposals = sorted(set(proposal_identities) - set(source_counter))
    duplicate_proposals = sorted(
        identity
        for identity, count in collections.Counter(proposal_identities).items()
        if count != 1
    )
    if orphan_proposals or duplicate_proposals:
        raise BatchError("proposal-to-membership completeness failed")

    bucket_counts = dict(
        sorted(collections.Counter(row["triage_bucket"] for row in ledger).items())
    )
    triage_bucket_counts = {
        key: value
        for key, value in (triage_manifest.get("bucket_counts") or {}).items()
        if value
    }
    if bucket_counts != triage_bucket_counts:
        raise BatchError("batch bucket counts differ from source triage manifest")
    category_counts = dict(
        sorted(collections.Counter(row["query_id"] for row in ledger).items())
    )
    view_counts = {name: len(rows) for name, rows in sorted(views.items())}
    if view_counts["all_memberships.ndjson"] != len(ledger):
        raise BatchError("all-memberships view is incomplete")
    if view_counts["machine_proposals.ndjson"] != len(proposals):
        raise BatchError("machine-proposals view is incomplete")
    if (
        view_counts["proposed_inclusions.ndjson"]
        + view_counts["proposed_exclusions.ndjson"]
        != len(proposals)
    ):
        raise BatchError("proposed inclusion/exclusion views do not reconcile")
    if (
        view_counts["ratified_inclusions.ndjson"]
        + view_counts["ratified_exclusions.ndjson"]
        + view_counts["unresolved_memberships.ndjson"]
        != len(ledger)
    ):
        raise BatchError("ratified/unresolved membership views do not reconcile")

    shard_receipts = []
    for shard in shards:
        if not checkpoint_valid(out_dir, shard):
            raise BatchError(f"final shard checkpoint is invalid: {shard['shard_id']}")
        checkpoint_path = out_dir / shard["checkpoint_relative"]
        shard_receipts.append(
            {
                "shard_id": shard["shard_id"],
                "path": shard["relative"].as_posix(),
                "checkpoint_path": shard["checkpoint_relative"].as_posix(),
                "row_count": len(shard["rows"]),
                "content_sha256": shard["content_sha256"],
                "checkpoint_sha256": sha256_file(checkpoint_path),
            }
        )
    view_receipts = {
        name: {
            "path": f"views/{name}",
            "row_count": len(views[name]),
            "size": (out_dir / "views" / name).stat().st_size,
            "sha256": sha256_file(out_dir / "views" / name),
        }
        for name in VIEW_NAMES
    }
    return {
        "schema": SCHEMA,
        "source_triage_generation_id": triage_manifest["generation_id"],
        "source_triage_manifest_sha256": sha256_file(
            out_dir / "triage_generation" / "manifest.json"
        ),
        "source_input_hashes": {
            name: receipt["sha256"]
            for name, receipt in sorted(
                (triage_manifest.get("inputs") or {}).items()
            )
        },
        "deterministic": True,
        "generated_at": None,
        "staging_only": True,
        "semantic_classification_performed": False,
        "production_or_user_decision_ledgers_written": False,
        "machine_proposals_converted_to_ratified_decisions": 0,
        "machine_proposals_are_scoring_eligible": False,
        "batch_size": batch_size,
        "source_membership_count": len(ledger),
        "distinct_source_membership_count": len(source_counter),
        "sharded_membership_count": len(observed_identities),
        "missing_membership_count": len(missing),
        "extra_membership_count": len(extra),
        "duplicate_membership_count": len(duplicates),
        "proposal_count": len(proposals),
        "orphan_proposal_count": len(orphan_proposals),
        "duplicate_proposal_count": len(duplicate_proposals),
        "bucket_counts": bucket_counts,
        "category_counts": category_counts,
        "view_counts": view_counts,
        "shard_count": len(shards),
        "valid_checkpoint_count": len(shards),
        "shards": shard_receipts,
        "views": view_receipts,
        "complete": True,
        "acceptance_for_scoring": False,
        "human_review_and_ratification_required": True,
    }


def run(
    *,
    run_dir: Path,
    registry_path: Path,
    relationships_dir: Path,
    text_dir: Path,
    article_dir: Path,
    review_ledger_path: Path,
    out_dir: Path,
    batch_size: int = DEFAULT_BATCH_SIZE,
    resume: bool = False,
    workspace_root: Path = TRIAGE.ROOT,
) -> tuple[dict[str, Any], dict[str, int]]:
    out_dir = assert_output_path(out_dir, workspace_root, resume=resume)
    inputs = validate_source_inputs(
        run_dir=run_dir,
        registry_path=registry_path,
        relationships_dir=relationships_dir,
        text_dir=text_dir,
        article_dir=article_dir,
        review_ledger_path=review_ledger_path,
    )
    created = False
    if not resume:
        out_dir.mkdir(parents=True)
        created = True
    try:
        triage_dir, triage_manifest = prepare_triage_generation(
            out_dir=out_dir,
            inputs=inputs,
            run_dir=run_dir,
            registry_path=registry_path,
            relationships_dir=relationships_dir,
            text_dir=text_dir,
            article_dir=article_dir,
            review_ledger_path=review_ledger_path,
            workspace_root=workspace_root,
            resume=resume,
        )
        ledger = read_ndjson(triage_dir / "triage_ledger.ndjson")
        proposals = read_ndjson(triage_dir / "proposed_decisions.ndjson")
        if not ledger:
            raise BatchError("complete triage generation contains no memberships")
        validate_non_scoring_contract(ledger, proposals)
        shards = expected_shards(
            ledger, batch_size, triage_manifest["generation_id"]
        )

        prior_manifest_path = out_dir / "completeness_manifest.json"
        if resume and prior_manifest_path.exists():
            prior = read_json(prior_manifest_path)
            if prior.get("source_triage_generation_id") != triage_manifest.get(
                "generation_id"
            ):
                raise BatchError("prior batch manifest targets another triage generation")
            if prior.get("batch_size") != batch_size:
                raise BatchError("batch size differs from prior completed generation")

        runtime = write_shards(
            out_dir=out_dir,
            shards=shards,
            source_generation_id=triage_manifest["generation_id"],
            batch_size=batch_size,
            resume=resume,
        )
        views = build_views(ledger, proposals)
        write_views(out_dir, views)
        manifest = completeness(
            ledger=ledger,
            proposals=proposals,
            shards=shards,
            views=views,
            triage_manifest=triage_manifest,
            out_dir=out_dir,
            batch_size=batch_size,
        )
        atomic_json(out_dir / "completeness_manifest.json", manifest)
        # Alias for tooling that expects the conventional manifest name.  Both
        # files are byte-identical and deterministic.
        atomic_write(out_dir / "manifest.json", json_bytes(manifest))
        return manifest, runtime
    except BaseException:
        if created:
            shutil.rmtree(out_dir, ignore_errors=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Build deterministic, resumable, staging-only membership review batches."
        )
    )
    result.add_argument("--run", required=True, type=Path)
    result.add_argument("--registry", required=True, type=Path)
    result.add_argument("--relationships", required=True, type=Path)
    result.add_argument("--text-relevance", required=True, type=Path)
    result.add_argument("--article-cross-reference", required=True, type=Path)
    result.add_argument("--review-ledger", required=True, type=Path)
    result.add_argument("--out", required=True, type=Path)
    result.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    result.add_argument("--resume", action="store_true")
    result.add_argument(
        "--workspace-root", type=Path, default=TRIAGE.ROOT, help=argparse.SUPPRESS
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest, runtime = run(
            run_dir=args.run,
            registry_path=args.registry,
            relationships_dir=args.relationships,
            text_dir=args.text_relevance,
            article_dir=args.article_cross_reference,
            review_ledger_path=args.review_ledger,
            out_dir=args.out,
            batch_size=args.batch_size,
            resume=args.resume,
            workspace_root=args.workspace_root,
        )
    except (BatchError, TRIAGE.TriageError) as exc:
        print(f"run_legislation_review_batches: FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(args.out.resolve()),
                "complete": manifest["complete"],
                "memberships": manifest["source_membership_count"],
                "proposals": manifest["proposal_count"],
                "shards": manifest["shard_count"],
                "resume_activity": runtime,
                "acceptance_for_scoring": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
