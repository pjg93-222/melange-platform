"""Independently verify completeness and source references in a career sidecar."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import sqlite3

from fec.staffer_network.complete_existing_career_histories import (
    contains_phrase, norm, normalized_title, packed, sha,
)


def records(path):
    with gzip.open(path, "rt", encoding="utf8") as stream:
        for line in stream:
            yield json.loads(line)


def evaluate(completion_receipt: Path, output: Path) -> dict:
    completion_receipt, output = Path(completion_receipt), Path(output)
    if output.exists():
        raise ValueError("Immutable evaluation already exists")
    receipt = json.loads(completion_receipt.read_bytes())
    if receipt.get("status") != "PASS" or receipt.get("dates_invented") != 0:
        raise ValueError("Completion receipt did not pass")
    for binding in receipt["inputs"].values():
        if binding and sha(binding["path"]) != binding["sha256"]:
            raise ValueError("Authenticated input changed: " + binding["path"])

    all_rows = []
    for item in receipt["files"]:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Output changed: " + item["path"])
        decoded = list(records(item["path"]))
        if len(decoded) != item["records"]:
            raise ValueError("Output row count changed")
        all_rows.extend(decoded)
    if Counter(row["status"] for row in all_rows) != Counter(receipt["counts"]):
        raise ValueError("Receipt status accounting mismatch")

    directory_rows = [row for row in all_rows if "missing_claim_id" in row]
    release_rows = [row for row in all_rows if "clause_id" in row and "binding_decision_ids" in row]
    historical_rows = [row for row in all_rows if "historical_claim_id" in row]
    ledger_path = receipt["inputs"]["ledger"]["path"]
    with sqlite3.connect("file:" + Path(ledger_path).resolve().as_posix() + "?mode=ro", uri=True) as db:
        expected_directory = {row[0] for row in db.execute(
            "SELECT id FROM claim WHERE source='directory' AND status='MISSING_DATE_BOUND'")}
        if {row["missing_claim_id"] for row in directory_rows} != expected_directory:
            raise ValueError("Directory queue is not an exact partition")
        claims = {row[0]: row for row in db.execute(
            "SELECT id,person,employer,title,start,end_exclusive,payload FROM claim "
            "WHERE source='directory'")}
        for row in directory_rows:
            if row["missing_claim_id"] not in claims:
                raise ValueError("Missing directory source reference")
            for reference in row["bounded_references"]:
                source = claims.get(reference["claim_id"])
                if not source or list(source[4:6]) != reference["interval"]:
                    raise ValueError("Bounded directory reference changed")
            if row["status"] == "RESOLVED_EXACT_POLITICO_DUPLICATE":
                if not row["bounded_references"] or any(
                        ref["interval"] != row["interval"] for ref in row["bounded_references"]):
                    raise ValueError("Resolved directory interval lacks exact support")

    release = json.loads(Path(receipt["inputs"]["release"]["path"]).read_bytes())
    expected_release = set()
    for binding in release.get("identity_bindings", []):
        if binding.get("kind") != "covered_politico":
            continue
        for item in binding.get("evidence", {}).get("support", []):
            if (item.get("covered_dates", {}).get("status") == "MISSING_DATE_BOUND"
                    and item.get("dates_from_directory_only") and len(item.get("directory_dates") or ()) == 2):
                expected_release.add(item["clause_id"])
    if {row["clause_id"] for row in release_rows} != expected_release:
        raise ValueError("Accepted release clause queue is not an exact partition")

    historical_binding = receipt["inputs"].get("historical_holdings")
    if historical_binding:
        with sqlite3.connect("file:" + Path(historical_binding["path"]).resolve().as_posix() + "?mode=ro",
                             uri=True) as db:
            expected_historical = {row[0] for row in db.execute(
                "SELECT id FROM clause WHERE status='BOUNDED_SOURCE_INTERVAL'")}
            if {row["clause_id"] for row in historical_rows} != expected_historical:
                raise ValueError("Historical bounded clauses are not an exact partition")
            valid_support = {(row[0], row[1], row[2], row[3], row[4]) for row in db.execute(
                "SELECT window_id,id,claim_id,filing_uuid,activity FROM support")}
            for row in historical_rows:
                for support in row["filing_support"]:
                    key = (support["window_id"], support["support_id"], row["historical_claim_id"],
                           support["filing_uuid"], support["activity"])
                    if key not in valid_support:
                        raise ValueError("Historical filing support reference changed")
                if row["status"] == "STRUCTURED_EXACT_HELD_OFFICE_TITLE":
                    text = normalized_title(row["raw_text"])
                    if not (contains_phrase(text, row["employer_key"])
                            and contains_phrase(text, row["title_key"])):
                        raise ValueError("Structured office/title is not explicit in source clause")

    if len(directory_rows) + len(release_rows) + len(historical_rows) != len(all_rows):
        raise ValueError("Unexpected or multiply classified output row")
    result = {
        "schema": "existing-career-completion-evaluation-v1", "status": "PASS",
        "completion_receipt": {"path": str(completion_receipt.resolve()),
                               "sha256": sha(completion_receipt)},
        "checks": {
            "directory_exact_partition": len(directory_rows),
            "accepted_release_exact_partition": len(release_rows),
            "historical_bounded_exact_partition": len(historical_rows),
            "all_references_exist": True, "all_resolved_intervals_supported": True,
            "all_structured_phrases_explicit": True,
        },
        "rows_evaluated": len(all_rows), "dates_invented": 0,
        "production_activated": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completion", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.completion, args.output), indent=2))


if __name__ == "__main__":
    main()
