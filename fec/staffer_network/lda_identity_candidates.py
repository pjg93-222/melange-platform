# -*- coding: utf-8 -*-
"""Build deterministic, review-required LDA identity candidates.

Exact normalized-name equality is blocking evidence for review, never proof of
identity.  This module deliberately emits no score and performs no merge.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

try:
    from .lda_source_native_corpus import _government_terms
    from .politico_normalize import normalize_name
    from .publication import _validate_record_semantics
    from .schema_validation import validate_instance
    from .source_manifest import canonical_json_bytes, sha256_file
except ImportError:  # direct script invocation
    from staffer_network.lda_source_native_corpus import _government_terms
    from staffer_network.politico_normalize import normalize_name
    from staffer_network.publication import _validate_record_semantics
    from staffer_network.schema_validation import validate_instance
    from staffer_network.source_manifest import canonical_json_bytes, sha256_file


SCHEMA_ID = "staffer-network-lda-identity-candidate-v1"
RECEIPT_SCHEMA_ID = "staffer-network-lda-identity-candidate-receipt-v1"
METHOD = "NORMALIZED_NAME_EQUALITY_REVIEW_CANDIDATES_ONLY"
METHOD_VERSION = "lda-identity-normalized-name-candidates-v2"
NAME_NORMALIZER_VERSION = "politico-normalize-name-v1"
NAME_NORMALIZER_LOSSY_RULES = (
    "NFKD_ASCII_DROP_NONDECOMPOSABLE",
    "CASEFOLD",
    "NON_ALNUM_TO_SPACE",
    "DROP_GENERATIONAL_SUFFIX_JR_SR_II_III_IV",
)
SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schemas"


class LdaIdentityCandidateError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LdaIdentityCandidateError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LdaIdentityCandidateError(f"{path} is not a JSON object")
    return value


def _rows(path: Path) -> Iterator[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise LdaIdentityCandidateError(
                        f"{path}:{line_number} is not an object"
                    )
                yield row
    except (OSError, json.JSONDecodeError) as exc:
        raise LdaIdentityCandidateError(f"cannot stream {path}: {exc}") from exc


def _require_bound_file(
    path: Path,
    entries: Any,
    *,
    selector_key: str,
    selector_value: str,
) -> None:
    if not isinstance(entries, list):
        raise LdaIdentityCandidateError(f"receipt lacks file inventory for {path.name}")
    matches = [
        row for row in entries
        if isinstance(row, dict) and row.get(selector_key) == selector_value
    ]
    if len(matches) != 1:
        raise LdaIdentityCandidateError(f"receipt file binding is ambiguous for {path.name}")
    entry = matches[0]
    if entry.get("bytes") != path.stat().st_size or entry.get("sha256") != sha256_file(path):
        raise LdaIdentityCandidateError(f"receipt file binding failed for {path.name}")


def _write_sorted_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    ordered = sorted(
        rows,
        key=lambda row: (
            str(row["lda_source_person_id"]),
            str(row["matched_source_system_id"]),
            str(row["matched_source_entity_id"]),
        ),
    )
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            for row in ordered:
                stream.write(canonical_json_bytes(row))
    os.replace(temporary, path)
    return len(ordered)


def _target_rows(
    politico_root: Path, source_native_root: Path
) -> Iterator[dict[str, Any]]:
    for row in _rows(politico_root / "persons.ndjson"):
        yield {
            "source_system_id": "politico-pro-staffer-directory",
            "source_entity_id": row.get("source_person_id"),
            "display_name": row.get("display_name"),
            "names": [(row.get("display_name"), "DISPLAY_NAME")],
        }
    for row in _rows(source_native_root / "quorum_legacy_source_native_seed.ndjson"):
        yield {
            "source_system_id": "quorum-legacy-staffers",
            "source_entity_id": row.get("source_entity_id"),
            "display_name": row.get("display_name"),
            "names": [(row.get("display_name"), "DISPLAY_NAME")],
        }
    for row in _rows(source_native_root / "littlesis_entities.ndjson.gz"):
        types = row.get("types")
        if not isinstance(types, list) or not any(
            str(value).casefold() == "person" for value in types
        ):
            continue
        aliases = row.get("aliases")
        alias_values = aliases if isinstance(aliases, list) else []
        yield {
            "source_system_id": "littlesis",
            "source_entity_id": row.get("source_entity_id"),
            "display_name": row.get("name"),
            "names": [(row.get("name"), "DISPLAY_NAME")]
            + [(value, "ALIAS") for value in alias_values],
        }


def build_candidates(
    lda_corpus_root: str | Path,
    politico_root: str | Path,
    source_native_root: str | Path,
    output_root: str | Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    lda_root = Path(lda_corpus_root)
    politico = Path(politico_root)
    native = Path(source_native_root)
    output = Path(output_root)
    if output.exists() and any(output.iterdir()):
        raise LdaIdentityCandidateError(f"output root is nonempty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    lda_receipt_path = lda_root / "lda_source_native_corpus_receipt.json"
    politico_receipt_path = politico / "normalization_receipt.json"
    native_receipt_path = native / "source_native_corpus_receipt.json"
    lda_receipt = _read_json(lda_receipt_path)
    politico_receipt = _read_json(politico_receipt_path)
    native_receipt = _read_json(native_receipt_path)
    if (
        lda_receipt.get("schema_id")
        != "staffer-network-lda-source-native-corpus-receipt-v1"
        or
        lda_receipt.get("status") != "PASS"
        or lda_receipt.get("canonical_identity_merges") != 0
        or lda_receipt.get("name_matches_are_candidates_only") is not True
    ):
        raise LdaIdentityCandidateError("LDA corpus is not a sealed zero-merge PASS")
    if (
        politico_receipt.get("schema_id")
        != "politico-directory-normalization-receipt-v1"
        or politico_receipt.get("status") != "PASS"
        or politico_receipt.get("canonical_entity_merges") != 0
        or native_receipt.get("schema_id")
        != "staffer-network-source-native-corpus-receipt-v1"
        or native_receipt.get("status") != "PASS"
        or native_receipt.get("canonical_entity_merges") != 0
        or native_receipt.get("cross_source_identity_assertions") != 0
        or native_receipt.get("identity_resolution_advanced") is not False
    ):
        raise LdaIdentityCandidateError("target corpus receipts are not PASS")
    _require_bound_file(
        lda_root / "lda_lobbyist_assertions.ndjson.gz", lda_receipt.get("files"),
        selector_key="role", selector_value="lobbyist_assertions",
    )
    _require_bound_file(
        politico / "persons.ndjson", politico_receipt.get("files"),
        selector_key="path", selector_value="persons.ndjson",
    )
    _require_bound_file(
        native / "quorum_legacy_source_native_seed.ndjson",
        native_receipt.get("record_files"), selector_key="role",
        selector_value="quorum-legacy-source-native-seed",
    )
    _require_bound_file(
        native / "littlesis_entities.ndjson.gz", native_receipt.get("record_files"),
        selector_key="role", selector_value="littlesis-entities",
    )

    upstream_receipt_hashes = {
        "lda": sha256_file(lda_receipt_path),
        "politico": sha256_file(politico_receipt_path),
        "source_native": sha256_file(native_receipt_path),
    }
    source_binding_sha256 = hashlib.sha256(canonical_json_bytes({
        "method_version": METHOD_VERSION,
        "upstream_receipt_sha256": upstream_receipt_hashes,
    })).hexdigest()

    lda_by_name: dict[str, set[str]] = defaultdict(set)
    lda_variants_by_name: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    lda_rows: dict[str, dict[str, Any]] = {}
    for row in _rows(lda_root / "lda_lobbyist_assertions.ndjson.gz"):
        source_id = row.get("source_person_id")
        variants = row.get("name_variants")
        if not isinstance(source_id, str) or not isinstance(variants, list):
            raise LdaIdentityCandidateError("invalid LDA lobbyist assertion")
        lda_rows[source_id] = row
        for value in variants:
            key = normalize_name(str(value))
            if key:
                lda_by_name[key].add(source_id)
                lda_variants_by_name[key][source_id].add(str(value))
    if len(lda_rows) != lda_receipt.get("lobbyists"):
        raise LdaIdentityCandidateError("LDA lobbyist count does not reconcile")

    target_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_counts: Counter[str] = Counter()
    for target in _target_rows(politico, native):
        system = target.get("source_system_id")
        entity_id = target.get("source_entity_id")
        display = target.get("display_name")
        if not all(isinstance(value, str) and value for value in (system, entity_id, display)):
            raise LdaIdentityCandidateError("invalid target person row")
        target_counts[system] += 1
        evidence_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
        for raw_name, basis in target["names"]:
            if not isinstance(raw_name, str):
                continue
            key = normalize_name(raw_name)
            if not key or key not in lda_by_name:
                continue
            evidence = {"basis": basis, "raw_target_name": raw_name}
            if evidence not in evidence_by_key[key]:
                evidence_by_key[key].append(evidence)
        for key, name_evidence in sorted(evidence_by_key.items()):
            target_by_name[key].append({
                "source_system_id": system,
                "source_entity_id": entity_id,
                "display_name": display,
                "target_name_evidence": sorted(
                    name_evidence,
                    key=lambda row: (row["basis"], row["raw_target_name"].casefold(), row["raw_target_name"]),
                ),
            })

    pairs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key in sorted(set(lda_by_name) & set(target_by_name)):
        lda_ids = sorted(lda_by_name[key])
        targets = target_by_name[key]
        per_system = Counter(row["source_system_id"] for row in targets)
        multiplicity_by_source = {
            source: per_system.get(source, 0)
            for source in (
                "littlesis", "politico-pro-staffer-directory",
                "quorum-legacy-staffers",
            )
        }
        for lda_id in lda_ids:
            for target in targets:
                pair_key = (
                    lda_id,
                    target["source_system_id"],
                    target["source_entity_id"],
                )
                aggregate = pairs.setdefault(pair_key, {
                    "match_evidence": {},
                    "lda_name_collision_max": 0,
                    "target_name_collision_max": 0,
                    "target_cross_source_multiplicity_max": 0,
                    "target_multiplicity_by_source_max": {
                        source: 0 for source in multiplicity_by_source
                    },
                    "target": target,
                })
                aggregate["match_evidence"][key] = {
                    "normalized_name": key,
                    "raw_lda_name_variants": sorted(
                        lda_variants_by_name[key][lda_id],
                        key=lambda value: (value.casefold(), value),
                    ),
                    "target_name_evidence": target["target_name_evidence"],
                    "lda_name_multiplicity": len(lda_ids),
                    "target_multiplicity_total": len(targets),
                    "target_multiplicity_by_source": multiplicity_by_source,
                }
                aggregate["lda_name_collision_max"] = max(
                    aggregate["lda_name_collision_max"], len(lda_ids)
                )
                aggregate["target_name_collision_max"] = max(
                    aggregate["target_name_collision_max"],
                    per_system[target["source_system_id"]],
                )
                aggregate["target_cross_source_multiplicity_max"] = max(
                    aggregate["target_cross_source_multiplicity_max"], len(targets)
                )
                for source, count in multiplicity_by_source.items():
                    aggregate["target_multiplicity_by_source_max"][source] = max(
                        aggregate["target_multiplicity_by_source_max"][source], count
                    )

    candidates: list[dict[str, Any]] = []
    for (lda_id, system, entity_id), aggregate in sorted(pairs.items()):
        lda = lda_rows[lda_id]
        target = aggregate["target"]
        if (
            not isinstance(lda.get("display_name"), str)
            or not isinstance(lda.get("covered_position_support_count"), int)
        ):
            raise LdaIdentityCandidateError(f"{lda_id}: incomplete lobbyist assertion")
        digest = hashlib.sha256(
            (
                f"{source_binding_sha256}\0{METHOD_VERSION}\0{lda_id}\0"
                f"{system}\0{entity_id}"
            ).encode("utf-8")
        ).hexdigest()
        values = lda.get("covered_position_values")
        government_hit = any(
            _government_terms(str(value))
            for value in (values if isinstance(values, list) else [])
        )
        candidates.append({
            "schema_id": SCHEMA_ID,
            "candidate_id": "lda:identity-candidate:" + digest,
            "method_version": METHOD_VERSION,
            "source_binding_sha256": source_binding_sha256,
            "lda_source_person_id": lda_id,
            "lda_display_name": lda["display_name"],
            "lda_covered_position_support_count": lda["covered_position_support_count"],
            "lda_government_term_hit": government_hit,
            "matched_source_system_id": system,
            "matched_source_entity_id": entity_id,
            "matched_display_name": target["display_name"],
            "match_evidence": [
                aggregate["match_evidence"][key]
                for key in sorted(aggregate["match_evidence"])
            ],
            "lda_name_collision_max": aggregate["lda_name_collision_max"],
            "target_name_collision_max": aggregate["target_name_collision_max"],
            "target_cross_source_multiplicity_max": aggregate[
                "target_cross_source_multiplicity_max"
            ],
            "target_multiplicity_by_source_max": dict(sorted(
                aggregate["target_multiplicity_by_source_max"].items()
            )),
            "review_status": "REVIEW_REQUIRED",
            "confidence": None,
            "canonical_merge": False,
        })

    candidate_schema = _read_json(
        SCHEMA_DIRECTORY / "lda_identity_candidate.schema.json"
    )
    receipt_schema = _read_json(
        SCHEMA_DIRECTORY / "lda_identity_candidate_receipt.schema.json"
    )
    for row in candidates:
        validate_instance(row, candidate_schema)
        _validate_record_semantics(row, row["schema_id"])
    candidate_path = output / "lda_identity_candidates.ndjson.gz"
    expected_count = _write_sorted_gzip(candidate_path, candidates)
    persisted_rows = list(_rows(candidate_path))
    for row in persisted_rows:
        validate_instance(row, candidate_schema)
        _validate_record_semantics(row, row["schema_id"])
    candidate_count = len(persisted_rows)
    if candidate_count != expected_count:
        raise LdaIdentityCandidateError("persisted candidate count does not reconcile")
    by_system = Counter(row["matched_source_system_id"] for row in persisted_rows)
    unique_lda_by_system = {
        system: len({
            row["lda_source_person_id"] for row in persisted_rows
            if row["matched_source_system_id"] == system
        })
        for system in sorted(by_system)
    }
    receipt = {
        "schema_id": RECEIPT_SCHEMA_ID,
        "generated_at": generated_at or _now(),
        "status": "PASS",
        "method": METHOD,
        "method_version": METHOD_VERSION,
        "name_normalizer_version": NAME_NORMALIZER_VERSION,
        "name_normalizer_lossy_rules": list(NAME_NORMALIZER_LOSSY_RULES),
        "source_binding_sha256": source_binding_sha256,
        "lda_corpus_receipt_sha256": upstream_receipt_hashes["lda"],
        "politico_normalization_receipt_sha256": upstream_receipt_hashes["politico"],
        "source_native_corpus_receipt_sha256": upstream_receipt_hashes["source_native"],
        "lda_lobbyists": len(lda_rows),
        "target_person_rows": dict(sorted(target_counts.items())),
        "candidate_count": candidate_count,
        "candidate_count_by_source": dict(sorted(by_system.items())),
        "unique_lda_lobbyists_by_source": unique_lda_by_system,
        "candidates_with_government_term_hit": sum(
            row["lda_government_term_hit"] for row in persisted_rows
        ),
        "candidates_with_any_name_collision": sum(
            row["lda_name_collision_max"] > 1
            or row["target_cross_source_multiplicity_max"] > 1
            for row in persisted_rows
        ),
        "candidates_with_cross_source_name_multiplicity": sum(
            row["target_cross_source_multiplicity_max"] > 1
            for row in persisted_rows
        ),
        "file": {
            "path": candidate_path.name,
            "records": candidate_count,
            "bytes": candidate_path.stat().st_size,
            "sha256": sha256_file(candidate_path),
            "encoding": "canonical-ndjson-gzip-mtime-0",
            "privacy_class": "restricted-machine-local",
        },
        "review_required_count": sum(
            row["review_status"] == "REVIEW_REQUIRED" for row in persisted_rows
        ),
        "confidence_values_emitted": sum(
            row["confidence"] is not None for row in persisted_rows
        ),
        "canonical_identity_merges": sum(
            bool(row["canonical_merge"]) for row in persisted_rows
        ),
        "downstream_identity_gate": "CLOSED",
        "failures": [],
    }
    validate_instance(receipt, receipt_schema)
    _validate_record_semantics(receipt, receipt["schema_id"])
    receipt_path = output / "lda_identity_candidate_receipt.json"
    temporary = receipt_path.with_suffix(f".json.tmp.{os.getpid()}")
    temporary.write_bytes(canonical_json_bytes(receipt))
    os.replace(temporary, receipt_path)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lda-corpus-root", required=True)
    parser.add_argument("--politico-root", required=True)
    parser.add_argument("--source-native-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--generated-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        receipt = build_candidates(
            args.lda_corpus_root,
            args.politico_root,
            args.source_native_root,
            args.output_root,
            generated_at=args.generated_at,
        )
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
