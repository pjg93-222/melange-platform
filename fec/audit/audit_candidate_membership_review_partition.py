#!/usr/bin/env python3
"""Audit a candidate-membership review ledger without changing source data.

The review ledger must be an exact, lossless partition into included,
false-positive exclusion, and unresolved files.  This audit intentionally
operates on membership keys (canonical_key, query_id), not only bill keys,
because relevance decisions are category-specific.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from lane_file_io import iter_ndjson_raw_lines, atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import iter_ndjson_raw_lines, atomic_write_bytes


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "acu-candidate-membership-review-partition-audit-v1"
PARTITIONS = {
    "included": ("included_memberships.ndjson", "include"),
    "excluded": ("false_positive_exclusions.ndjson", "exclude_false_positive"),
    "unresolved": ("unresolved_memberships.ndjson", "unresolved"),
}
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
NEGATIVE_ARTICLE_PATTERNS = (
    re.compile(r"\barticle absence\b", re.I),
    re.compile(r"\babsence of (?:a |any )?(?:trusted |press )?articles?\b", re.I),
    re.compile(r"\bno (?:trusted |press )?articles? (?:was |were )?(?:found|located|matched)\b", re.I),
    re.compile(r"\black of (?:a |any )?(?:trusted |press )?articles?\b", re.I),
    re.compile(r"\bwithout (?:a |any )?(?:trusted |press )?articles?\b", re.I),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ndjson_file_facts(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    byte_count = 0
    line_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            byte_count += len(chunk)
            line_count += chunk.count(b"\n")
    return {
        "path": str(path),
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
        "record_count": line_count,
    }


def resolve_manifest_output_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_membership_key(row: dict[str, Any]) -> tuple[str, str]:
    canonical_key = row.get("canonical_key")
    query_id = row.get("query_id")
    if not isinstance(canonical_key, str) or not canonical_key.strip():
        raise ValueError("canonical_key must be a non-empty string")
    if not isinstance(query_id, str) or not query_id.strip():
        raise ValueError("query_id must be a non-empty string")
    return canonical_key.strip(), query_id.strip()


def printable_key(key: tuple[str, str]) -> str:
    return f"{key[0]}::{key[1]}"


def key_set_sha256(keys: Iterable[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for canonical_key, query_id in sorted(keys):
        digest.update(canonical_key.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(query_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _nonempty_string(row: dict[str, Any], field: str) -> bool:
    return isinstance(row.get(field), str) and bool(row[field].strip())


def _official_exclusion_provenance_errors(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in (
        "reason",
        "evidence",
        "evidence_basis",
        "official_decision_text_version",
        "official_decision_section",
        "official_decision_evidence_excerpt",
        "official_decision_retrieved_at",
        "decision_type",
        "reason_code",
        "reviewer_confidence",
        "adjudication_state",
        "reviewer",
        "reviewed_at",
    ):
        if not _nonempty_string(row, field):
            errors.append(f"missing_nonempty_{field}")

    source_url = row.get("official_decision_source_url")
    if not (
        isinstance(source_url, str)
        and source_url.startswith("https://www.congress.gov/")
    ):
        errors.append("official_decision_source_url_is_not_congress_gov")
    if row.get("decision_source") != "explicit_review_decision":
        errors.append("decision_source_is_not_explicit_review_decision")
    if row.get("official_decision_text_unavailable") is not False:
        errors.append("official_decision_text_unavailable_is_not_false")
    if row.get("candidate_retained_in_discovery_corpus") is not True:
        errors.append("candidate_not_retained_in_discovery_corpus")

    excerpt = row.get("official_decision_evidence_excerpt")
    excerpt_sha = row.get("official_decision_evidence_sha256")
    if not isinstance(excerpt_sha, str) or not HEX_SHA256.fullmatch(excerpt_sha):
        errors.append("invalid_official_decision_evidence_sha256")
    elif isinstance(excerpt, str):
        observed = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        if observed != excerpt_sha:
            errors.append("official_decision_evidence_sha256_mismatch")

    provenance = row.get("decision_provenance")
    if not (
        isinstance(provenance, list)
        and provenance
        and all(isinstance(item, str) and item.strip() for item in provenance)
    ):
        errors.append("missing_decision_provenance")

    checked = row.get("evidence_sources_checked")
    if not (
        isinstance(checked, list)
        and checked
        and any(
            isinstance(item, str) and item.startswith("official_")
            for item in checked
        )
    ):
        errors.append("official_evidence_source_not_checked")
    return errors


def _negative_article_evidence_fields(row: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for field in (
        "reason",
        "evidence",
        "evidence_basis",
        "reason_code",
        "decision_source",
        "likely_false_positive_reason",
    ):
        value = row.get(field)
        if isinstance(value, str) and any(
            pattern.search(value) for pattern in NEGATIVE_ARTICLE_PATTERNS
        ):
            fields.append(field)
    for field in ("decision_provenance", "evidence_sources_checked"):
        value = row.get(field)
        if isinstance(value, list):
            joined = "\n".join(item for item in value if isinstance(item, str))
            if any(pattern.search(joined) for pattern in NEGATIVE_ARTICLE_PATTERNS):
                fields.append(field)
    return fields


def _read_ndjson(
    path: Path,
    *,
    on_row: Any,
) -> tuple[int, list[dict[str, Any]]]:
    count = 0
    parse_errors: list[dict[str, Any]] = []
    # ``iter_ndjson_raw_lines`` strips a leading UTF-8 BOM from line 1, so a
    # PowerShell ``Out-File -Encoding utf8`` copy no longer reports a spurious
    # parse error on its first record.
    for line_number, raw_with_newline in iter_ndjson_raw_lines(path):
        raw = raw_with_newline.rstrip(b"\r\n")
        if not raw:
            parse_errors.append(
                {"line": line_number, "error": "blank_line"}
            )
            continue
        count += 1
        try:
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("row is not a JSON object")
            on_row(line_number, raw, row)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            parse_errors.append(
                {"line": line_number, "error": str(exc)}
            )
    return count, parse_errors


def audit_review_partition(review_dir: Path) -> dict[str, Any]:
    review_dir = review_dir.resolve()
    ledger_path = review_dir / "membership_review_ledger.ndjson"
    manifest_path = review_dir / "manifest.json"
    required_paths = [ledger_path, manifest_path] + [
        review_dir / filename for filename, _ in PARTITIONS.values()
    ]
    missing = [str(path) for path in required_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing required review files: {missing}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ledger: dict[tuple[str, str], tuple[str, str]] = {}
    ledger_dispositions: Counter[str] = Counter()
    duplicate_ledger_keys: list[str] = []
    invalid_ledger_keys: list[dict[str, Any]] = []
    row_contract_errors: list[dict[str, Any]] = []
    exclusion_provenance_errors: list[dict[str, Any]] = []
    article_absence_negative_use: list[dict[str, Any]] = []
    harmless_enrichment_lag: list[dict[str, Any]] = []
    provenance_inconsistencies: list[dict[str, Any]] = []
    neutral_article_absence_exclusions = 0

    def accept_ledger_row(line_number: int, raw: bytes, row: dict[str, Any]) -> None:
        nonlocal neutral_article_absence_exclusions
        try:
            key = stable_membership_key(row)
        except ValueError as exc:
            invalid_ledger_keys.append({"line": line_number, "error": str(exc)})
            return
        if key in ledger:
            duplicate_ledger_keys.append(printable_key(key))
        else:
            ledger[key] = (
                str(row.get("disposition")),
                hashlib.sha256(raw).hexdigest(),
            )
        disposition = row.get("disposition")
        ledger_dispositions[str(disposition)] += 1
        if disposition not in {expected for _, expected in PARTITIONS.values()}:
            row_contract_errors.append(
                {
                    "key": printable_key(key),
                    "error": "unsupported_disposition",
                    "value": disposition,
                }
            )
        if row.get("candidate_retained_in_discovery_corpus") is not True:
            row_contract_errors.append(
                {
                    "key": printable_key(key),
                    "error": "candidate_not_retained_in_discovery_corpus",
                }
            )
        if row.get("article_absence_is_never_exclusion_evidence") is not True:
            row_contract_errors.append(
                {
                    "key": printable_key(key),
                    "error": "article_absence_contract_not_true",
                }
            )

        if disposition != "exclude_false_positive":
            return
        provenance_errors = _official_exclusion_provenance_errors(row)
        if provenance_errors:
            exclusion_provenance_errors.append(
                {"key": printable_key(key), "errors": provenance_errors}
            )
        negative_fields = _negative_article_evidence_fields(row)
        if negative_fields:
            article_absence_negative_use.append(
                {"key": printable_key(key), "fields": negative_fields}
            )
        if (
            row.get("trusted_article_match_count") == 0
            and row.get("trusted_article_review_signal")
            == "no_trusted_article_match_not_dispositive"
        ):
            neutral_article_absence_exclusions += 1

        official_unavailable = row.get("official_decision_text_unavailable")
        derived_unavailable = row.get("text_unavailable")
        if official_unavailable is False and derived_unavailable is True:
            if (
                not provenance_errors
                and row.get("official_text_evidence_state")
                in {None, "official_text_unavailable", "not_enriched"}
            ):
                harmless_enrichment_lag.append(
                    {
                        "key": printable_key(key),
                        "classification": (
                            "harmless_automated_enrichment_lag_with_complete_"
                            "official_decision_provenance"
                        ),
                    }
                )
            else:
                provenance_inconsistencies.append(
                    {
                        "key": printable_key(key),
                        "error": (
                            "official_decision_text_available_but_derived_"
                            "text_unavailable_without_complete_independent_provenance"
                        ),
                    }
                )
        elif (
            isinstance(official_unavailable, bool)
            and isinstance(derived_unavailable, bool)
            and official_unavailable is not derived_unavailable
        ):
            provenance_inconsistencies.append(
                {
                    "key": printable_key(key),
                    "error": "official_and_derived_text_availability_contradict",
                }
            )

    ledger_line_count, ledger_parse_errors = _read_ndjson(
        ledger_path, on_row=accept_ledger_row
    )

    partition_results: dict[str, Any] = {}
    partition_key_owner: dict[tuple[str, str], str] = {}
    duplicate_across_partitions: list[dict[str, Any]] = []
    missing_from_ledger: list[dict[str, Any]] = []
    wrong_partition: list[dict[str, Any]] = []
    line_content_mismatches: list[dict[str, Any]] = []

    for partition_name, (filename, expected_disposition) in PARTITIONS.items():
        path = review_dir / filename
        partition_keys: set[tuple[str, str]] = set()
        duplicate_partition_keys: list[str] = []

        def accept_partition_row(
            line_number: int, raw: bytes, row: dict[str, Any]
        ) -> None:
            try:
                key = stable_membership_key(row)
            except ValueError as exc:
                missing_from_ledger.append(
                    {
                        "partition": partition_name,
                        "line": line_number,
                        "error": str(exc),
                    }
                )
                return
            printed = printable_key(key)
            if key in partition_keys:
                duplicate_partition_keys.append(printed)
            partition_keys.add(key)
            previous_owner = partition_key_owner.get(key)
            if previous_owner is not None and previous_owner != partition_name:
                duplicate_across_partitions.append(
                    {
                        "key": printed,
                        "partitions": [previous_owner, partition_name],
                    }
                )
            else:
                partition_key_owner[key] = partition_name

            ledger_row = ledger.get(key)
            if ledger_row is None:
                missing_from_ledger.append(
                    {"key": printed, "partition": partition_name}
                )
                return
            ledger_disposition, ledger_line_sha = ledger_row
            if (
                row.get("disposition") != expected_disposition
                or ledger_disposition != expected_disposition
            ):
                wrong_partition.append(
                    {
                        "key": printed,
                        "partition": partition_name,
                        "partition_disposition": row.get("disposition"),
                        "ledger_disposition": ledger_disposition,
                        "expected_disposition": expected_disposition,
                    }
                )
            partition_line_sha = hashlib.sha256(raw).hexdigest()
            if partition_line_sha != ledger_line_sha:
                line_content_mismatches.append(
                    {
                        "key": printed,
                        "partition": partition_name,
                        "ledger_line_sha256": ledger_line_sha,
                        "partition_line_sha256": partition_line_sha,
                    }
                )

        line_count, parse_errors = _read_ndjson(
            path, on_row=accept_partition_row
        )
        partition_results[partition_name] = {
            "file": str(path),
            "file_sha256": sha256_file(path),
            "line_count": line_count,
            "distinct_stable_membership_key_count": len(partition_keys),
            "stable_membership_key_set_sha256": key_set_sha256(partition_keys),
            "duplicate_stable_membership_keys": duplicate_partition_keys,
            "parse_errors": parse_errors,
        }

    missing_from_partitions = sorted(
        printable_key(key) for key in set(ledger) - set(partition_key_owner)
    )
    extra_partition_keys = sorted(
        printable_key(key) for key in set(partition_key_owner) - set(ledger)
    )
    partition_union_sha = key_set_sha256(partition_key_owner)
    ledger_key_sha = key_set_sha256(ledger)
    ledger_file_sha = sha256_file(ledger_path)

    manifest_contract_errors: list[str] = []
    expected_manifest_counts = {
        "membership_count": ledger_line_count,
        "distinct_review_membership_key_count": len(ledger),
        "included_count": partition_results["included"]["line_count"],
        "false_positive_exclusion_count": partition_results["excluded"][
            "line_count"
        ],
        "unresolved_count": partition_results["unresolved"]["line_count"],
    }
    for field, observed in expected_manifest_counts.items():
        if manifest.get(field) != observed:
            manifest_contract_errors.append(
                f"{field}: manifest={manifest.get(field)!r}, observed={observed!r}"
            )
    if manifest.get("article_absence_is_never_exclusion_evidence") is not True:
        manifest_contract_errors.append(
            "article_absence_is_never_exclusion_evidence is not true"
        )
    if manifest.get("article_based_automated_exclusion_count") != 0:
        manifest_contract_errors.append(
            "article_based_automated_exclusion_count is not zero"
        )

    manifest_output_binding_errors: list[str] = []
    if manifest.get("atomic_output_publish") is not True:
        manifest_output_binding_errors.append(
            "atomic_output_publish is not true"
        )
    if manifest.get("output_files_hash_bound") is not True:
        manifest_output_binding_errors.append(
            "output_files_hash_bound is not true"
        )
    sorted_queue_path = review_dir / "sorted_review_queue.ndjson"
    sorted_queue_facts = (
        ndjson_file_facts(sorted_queue_path)
        if sorted_queue_path.is_file()
        else None
    )
    if sorted_queue_facts is None:
        manifest_output_binding_errors.append(
            "sorted_review_queue.ndjson is missing"
        )
    observed_output_facts = {
        "ledger": {
            "path": str(ledger_path),
            "bytes": ledger_path.stat().st_size,
            "sha256": ledger_file_sha,
            "record_count": ledger_line_count,
        },
        **{
            name: {
                "path": result["file"],
                "bytes": Path(result["file"]).stat().st_size,
                "sha256": result["file_sha256"],
                "record_count": result["line_count"],
            }
            for name, result in partition_results.items()
        },
    }
    if sorted_queue_facts is not None:
        observed_output_facts["sorted_review_queue"] = sorted_queue_facts
    manifest_outputs = manifest.get("outputs")
    if not isinstance(manifest_outputs, dict):
        manifest_output_binding_errors.append(
            "outputs is not a hash-binding object"
        )
        manifest_outputs = {}
    manifest_output_names = {
        "excluded": "exclusions",
    }
    for name, observed in observed_output_facts.items():
        receipt_name = manifest_output_names.get(name, name)
        receipt = manifest_outputs.get(receipt_name)
        if not isinstance(receipt, dict):
            manifest_output_binding_errors.append(
                f"outputs.{receipt_name} is missing or not an object"
            )
            continue
        receipt_path = resolve_manifest_output_path(receipt.get("path"))
        if receipt_path != Path(observed["path"]).resolve():
            manifest_output_binding_errors.append(
                f"outputs.{receipt_name}.path does not bind the observed file"
            )
        for field in ("bytes", "sha256", "record_count"):
            if receipt.get(field) != observed[field]:
                manifest_output_binding_errors.append(
                    f"outputs.{receipt_name}.{field}: "
                    f"manifest={receipt.get(field)!r}, "
                    f"observed={observed[field]!r}"
                )

    all_parse_errors = {
        "ledger": ledger_parse_errors,
        **{
            name: result["parse_errors"]
            for name, result in partition_results.items()
        },
    }
    blocking_issue_count = sum(
        (
            len(duplicate_ledger_keys),
            len(invalid_ledger_keys),
            len(row_contract_errors),
            len(exclusion_provenance_errors),
            len(article_absence_negative_use),
            len(provenance_inconsistencies),
            len(duplicate_across_partitions),
            len(missing_from_ledger),
            len(wrong_partition),
            len(line_content_mismatches),
            len(missing_from_partitions),
            len(extra_partition_keys),
            len(manifest_contract_errors),
            len(manifest_output_binding_errors),
            sum(len(errors) for errors in all_parse_errors.values()),
            sum(
                len(result["duplicate_stable_membership_keys"])
                for result in partition_results.values()
            ),
        )
    )

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "review_directory": str(review_dir),
        "status": "PASS" if blocking_issue_count == 0 else "FAIL",
        "blocking_issue_count": blocking_issue_count,
        "stable_membership_key_definition": ["canonical_key", "query_id"],
        "source_bindings": {
            "manifest_file": str(manifest_path),
            "manifest_file_sha256": sha256_file(manifest_path),
            "ledger_file": str(ledger_path),
            "ledger_file_sha256": ledger_file_sha,
            "ledger_line_count": ledger_line_count,
            "ledger_distinct_stable_membership_key_count": len(ledger),
            "ledger_stable_membership_key_set_sha256": ledger_key_sha,
        },
        "partition_files": partition_results,
        "partition_exactness": {
            "partition_line_count_sum": sum(
                result["line_count"] for result in partition_results.values()
            ),
            "partition_distinct_key_union_count": len(partition_key_owner),
            "partition_stable_membership_key_union_sha256": partition_union_sha,
            "ledger_and_partition_key_hashes_equal": ledger_key_sha
            == partition_union_sha,
            "duplicate_ledger_keys": duplicate_ledger_keys,
            "invalid_ledger_keys": invalid_ledger_keys,
            "duplicate_across_partitions": duplicate_across_partitions,
            "missing_from_ledger": missing_from_ledger,
            "missing_from_partitions": missing_from_partitions,
            "extra_partition_keys": extra_partition_keys,
            "wrong_partition": wrong_partition,
            "line_content_mismatches": line_content_mismatches,
            "parse_errors": all_parse_errors,
        },
        "ledger_disposition_counts": dict(sorted(ledger_dispositions.items())),
        "preservation_and_provenance": {
            "all_rows_candidate_retained": not any(
                item["error"] == "candidate_not_retained_in_discovery_corpus"
                for item in row_contract_errors
            ),
            "row_contract_errors": row_contract_errors,
            "exclusion_count": partition_results["excluded"]["line_count"],
            "exclusions_with_complete_official_decision_provenance_count": (
                partition_results["excluded"]["line_count"]
                - len(exclusion_provenance_errors)
            ),
            "exclusion_provenance_errors": exclusion_provenance_errors,
        },
        "article_absence_policy": {
            "article_absence_used_negatively_count": len(
                article_absence_negative_use
            ),
            "negative_uses": article_absence_negative_use,
            "exclusions_with_no_trusted_article_match_but_official_evidence_count": (
                neutral_article_absence_exclusions
            ),
            "interpretation": (
                "No trusted-article match is neutral metadata. It is not a "
                "provenance inconsistency when an exclusion is independently "
                "supported by complete official Congress.gov evidence."
            ),
        },
        "semantic_field_consistency": {
            "harmless_automated_enrichment_lag_count": len(
                harmless_enrichment_lag
            ),
            "harmless_automated_enrichment_lag": harmless_enrichment_lag,
            "actual_provenance_inconsistency_count": len(
                provenance_inconsistencies
            ),
            "actual_provenance_inconsistencies": provenance_inconsistencies,
            "interpretation": (
                "text_unavailable describes the automated enrichment stream; "
                "official_decision_text_unavailable describes explicit review "
                "evidence. Their false/true combination is harmless only when "
                "the explicit exclusion has complete hash-verified official "
                "Congress.gov provenance and the enrichment stream is absent."
            ),
        },
        "manifest_contract_errors": manifest_contract_errors,
        "manifest_output_binding": {
            "passed": not manifest_output_binding_errors,
            "errors": manifest_output_binding_errors,
            "observed_outputs": observed_output_facts,
        },
    }
    report["audit_payload_sha256"] = canonical_json_sha256(report)
    return report


def write_report(report: dict[str, Any], output_path: Path) -> str:
    output_path = output_path.resolve()
    sidecar = output_path.with_suffix(output_path.suffix + ".sha256")
    bindings = report.get("source_bindings") or {}
    inputs = [bindings.get("manifest_file"), bindings.get("ledger_file")]
    inputs.extend(row.get("file") for row in (report.get("partition_files") or {}).values())
    for value in inputs:
        if not isinstance(value, str) or not value:
            continue
        source = Path(value).resolve()
        for target in (output_path, sidecar):
            if (target == source or (target.exists() and source.exists() and target.samefile(source))):
                raise ValueError("report output must not overwrite a bound input")
    body = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    file_sha = hashlib.sha256(body).hexdigest()
    atomic_write_bytes(output_path, body)
    atomic_write_bytes(sidecar, f"{file_sha}  {output_path.name}\n".encode("ascii"))
    return file_sha


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = audit_review_partition(args.review_dir)
        file_sha = write_report(report, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"audit failed before report generation: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "blocking_issue_count": report["blocking_issue_count"],
                "output": str(args.output.resolve()),
                "output_file_sha256": file_sha,
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
