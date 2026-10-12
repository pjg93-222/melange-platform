#!/usr/bin/env python3
"""Reconcile every legfix5 evidence source against the discovery universe.

Answers, with a hash-bound receipt and no silent truncation:

* Does every discovery candidate and membership appear where it should?
* Which candidates have no GovInfo BillStatus record, and is that expected?
* Which candidates have no accepted official text version, and why?
* Are the 10 declared missing-URL exceptions real and still exactly those 10?
* How far does the Congress.gov full-text lane actually reach?
* Where do the review ledger and the discovery membership universe disagree
  (the known raw-vs-effective edge gap), in both directions?

This audit is read-only. It performs no collection, culling, scoring, sync,
promotion, or main-corpus write, and it writes only its own receipt.

Two policies govern how sources are read, and they are deliberate:

* CONTROL FILES -- the discovery records, memberships, review ledger, and the
  GovInfo run's ``completed.json`` and ``all_jobs.ndjson`` -- FAIL CLOSED.
  Without them no count in this receipt means anything.

* PER-ITEM RECEIPTS -- BillStatus records, GovInfo accepted/failure receipts,
  and Congress.gov full-text receipts -- are COUNTED AND SURFACED when they
  cannot be read.  Silently skipping one shrank a coverage count and still
  emitted a passing check, which is precisely the failure this audit exists to
  catch.  ``no_evidence_receipt_was_unreadable`` fails when any is unreadable.

A receipt asserting ``status == "accepted"`` is an assertion, not evidence.
This audit opens the archived body it names and reports asserted and present
counts separately: ``text_relevance_httr_full_stage_20260726`` held 2,886
accepted receipts against ONE surviving archive, and counting assertions
reported full coverage over a 2,885-file loss.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from typing import Any, Iterator, Mapping

try:
    from lane_file_io import open_ndjson
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson
    from fec.legislation_measure_key import CANONICAL_KEY_RE


RECEIPT_SCHEMA = "acu-legfix5-coverage-reconciliation-receipt-v2"
TOOL_NAME = "audit_legfix5_coverage_reconciliation.py"
TOOL_VERSION = "2.0.0"

# Sibling keys a receipt may use to name the body it archived.
ARCHIVE_PATH_KEYS = ("html_file", "archive_file", "body_file", "path")

AMENDMENT_KINDS = {"hamdt", "samdt"}
DECLARED_MISSING_URL_KEYS = {
    "117:hr:5677",
    "117:hr:5695",
    "118:hr:3496",
    "118:hr:3571",
    "118:hr:7324",
    "119:hr:4584",
    "119:hr:5185",
    "119:hr:5210",
    "119:hr:9821",
    "119:hr:9900",
}


class ReconciliationError(RuntimeError):
    """Raised when a source cannot be read without ambiguity."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=".part"
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_ndjson(path: Path) -> Iterator[Mapping[str, Any]]:
    # ``open_ndjson`` reads as utf-8-sig so a PowerShell-written file with a
    # leading UTF-8 BOM parses instead of failing on line 1.
    with open_ndjson(path) as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ReconciliationError(
                    f"{path.name}:{number}: invalid JSON"
                ) from error
            if isinstance(row, dict):
                yield row


def measure_kind(key: str) -> str:
    return key.split(":")[1]


def sample(values, limit: int = 20) -> list[str]:
    return sorted(values)[:limit]


def read_receipt(
    path: Path, unreadable: list[str]
) -> Mapping[str, Any] | None:
    """Read one per-item receipt, recording rather than swallowing a failure.

    Returns ``None`` when the receipt cannot be read and appends its path to
    ``unreadable`` so the caller can surface it.  A locked or truncated receipt
    used to ``continue`` here, quietly shrinking a coverage count while the
    check it fed still reported PASS.
    """
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        unreadable.append(str(path))
        return None
    if not isinstance(value, dict):
        unreadable.append(str(path))
        return None
    return value


def archive_body_length(path: Path) -> int:
    """Length of the body an archive holds, without decompressing it.

    A gzip member records its uncompressed size in the last four bytes of the
    stream (RFC 1952 ISIZE), so a recorded ``byte_length`` can be checked in
    constant time.  Any other file is measured directly.
    """
    if path.suffix == ".gz":
        with path.open("rb") as stream:
            stream.seek(-4, os.SEEK_END)
            return struct.unpack("<I", stream.read(4))[0]
    return path.stat().st_size


def archive_state(row: Mapping[str, Any]) -> tuple[str, str | None]:
    """Classify the archived body a receipt claims to have written.

    Returns one of ``present`` / ``absent`` / ``undeclared`` / ``truncated`` /
    ``unreadable`` together with the path examined.  ``undeclared`` means the
    receipt named no archive at all, which is itself a coverage gap: nothing
    backs the acceptance.
    """
    raw = ""
    for key in ARCHIVE_PATH_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            raw = value
            break
    if not raw:
        return "undeclared", None

    path = Path(raw.replace("\\", "/"))
    if not path.exists():
        return "absent", str(path)

    declared = row.get("byte_length")
    if not isinstance(declared, int):
        return "present", str(path)
    try:
        if archive_body_length(path) != declared:
            return "truncated", str(path)
    except (OSError, struct.error):
        return "unreadable", str(path)
    return "present", str(path)


def audit(args: argparse.Namespace) -> Mapping[str, Any]:
    records_path = Path(args.records).resolve()
    memberships_path = Path(args.memberships).resolve()
    ledger_path = Path(args.ledger).resolve()
    govinfo_run = Path(args.govinfo_run).resolve()
    billstatus_root = Path(args.billstatus).resolve()
    full_text_root = Path(args.full_text_receipts).resolve()

    # --- discovery universe -------------------------------------------------
    candidates: set[str] = set()
    for row in read_ndjson(records_path):
        key = str(row.get("canonical_key") or "")
        if CANONICAL_KEY_RE.fullmatch(key):
            candidates.add(key)

    discovery_edges: set[tuple[str, str]] = set()
    for row in read_ndjson(memberships_path):
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "").strip()
        if CANONICAL_KEY_RE.fullmatch(key) and query_id:
            discovery_edges.add((key, query_id))

    edge_candidates = {key for key, _ in discovery_edges}

    # --- review ledger ------------------------------------------------------
    ledger_edges: set[tuple[str, str]] = set()
    ledger_dispositions: collections.Counter = collections.Counter()
    for row in read_ndjson(ledger_path):
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "").strip()
        if CANONICAL_KEY_RE.fullmatch(key) and query_id:
            ledger_edges.add((key, query_id))
        ledger_dispositions[str(row.get("disposition") or "")] += 1

    in_discovery_not_ledger = discovery_edges - ledger_edges
    in_ledger_not_discovery = ledger_edges - discovery_edges

    # Per-item receipts are counted, never silently skipped; see the module
    # docstring for the two-policy split.
    unreadable_receipts: list[str] = []

    # --- BillStatus ---------------------------------------------------------
    billstatus_keys: set[str] = set()
    for path in billstatus_root.rglob("*.json"):
        row = read_receipt(path, unreadable_receipts)
        if row is None:
            continue
        key = str(row.get("canonical_key") or "")
        if CANONICAL_KEY_RE.fullmatch(key):
            billstatus_keys.add(key)

    missing_billstatus = candidates - billstatus_keys
    missing_by_kind = collections.Counter(
        measure_kind(key) for key in missing_billstatus
    )
    missing_non_amendment = {
        key for key in missing_billstatus if measure_kind(key) not in AMENDMENT_KINDS
    }

    # --- GovInfo official text ---------------------------------------------
    completed = json.loads(
        (govinfo_run / "completed.json").read_text(encoding="utf-8")
    )
    planned: collections.Counter = collections.Counter()
    for row in read_ndjson(govinfo_run / "all_jobs.ndjson"):
        planned[str(row.get("canonical_key") or "")] += 1

    accepted: collections.Counter = collections.Counter()
    for path in (govinfo_run / "receipts" / "accepted").glob("*.json"):
        row = read_receipt(path, unreadable_receipts)
        if row is None:
            continue
        accepted[str(row.get("canonical_key") or "")] += 1

    quarantined: list[Mapping[str, Any]] = []
    for path in (govinfo_run / "receipts" / "failure").glob("*.json"):
        row = read_receipt(path, unreadable_receipts)
        if row is not None:
            quarantined.append(row)

    planned_keys = set(planned)
    fully_reviewed = {
        key for key in planned_keys if accepted.get(key, 0) == planned[key]
    }
    partially_reviewed = {
        key
        for key in planned_keys
        if 0 < accepted.get(key, 0) < planned[key]
    }
    zero_accepted = {key for key in planned_keys if accepted.get(key, 0) == 0}

    observed_missing_url = set(completed.get("missing_url_keys") or [])

    # A candidate has no planned official text if BillStatus listed no text
    # version URL for it, or if it has no BillStatus record at all.
    no_planned_text = candidates - planned_keys

    # --- Congress.gov full text --------------------------------------------
    # A receipt asserting acceptance is not evidence that the text survives.
    # Asserted and present are counted separately so an archive loss cannot
    # hide behind the receipts that describe it.
    asserted_text_keys: set[str] = set()
    present_text_keys: set[str] = set()
    accepted_receipt_count = 0
    archive_states: collections.Counter = collections.Counter()
    archive_gaps: list[str] = []
    for path in full_text_root.rglob("receipt.json"):
        row = read_receipt(path, unreadable_receipts)
        if row is None:
            continue
        key = str(row.get("canonical_key") or "")
        if not CANONICAL_KEY_RE.fullmatch(key) or row.get("status") != "accepted":
            continue
        accepted_receipt_count += 1
        asserted_text_keys.add(key)
        state, examined = archive_state(row)
        archive_states[state] += 1
        if state == "present":
            present_text_keys.add(key)
        else:
            archive_gaps.append(f"{key}|{state}|{examined or '-'}")

    missing_archive_keys = asserted_text_keys - present_text_keys

    findings: list[dict[str, Any]] = []

    def finding(name: str, ok: bool, detail: str, **extra: Any) -> None:
        findings.append(
            {"check": name, "pass": ok, "detail": detail, **extra}
        )

    finding(
        "every_membership_candidate_is_a_discovery_record",
        edge_candidates <= candidates,
        f"{len(edge_candidates - candidates)} membership candidates absent "
        "from the discovery record set",
        sample=sample(edge_candidates - candidates),
    )
    finding(
        "every_discovery_candidate_has_at_least_one_membership",
        candidates <= edge_candidates,
        f"{len(candidates - edge_candidates)} discovery candidates carry no "
        "membership edge",
        sample=sample(candidates - edge_candidates),
    )
    finding(
        "declared_missing_url_exceptions_are_exactly_the_ten",
        observed_missing_url == DECLARED_MISSING_URL_KEYS,
        f"observed {len(observed_missing_url)} missing-URL keys; "
        f"unexpected={sorted(observed_missing_url - DECLARED_MISSING_URL_KEYS)}, "
        f"resolved={sorted(DECLARED_MISSING_URL_KEYS - observed_missing_url)}",
    )
    finding(
        "missing_url_exceptions_are_discovery_candidates",
        observed_missing_url <= candidates,
        f"{len(observed_missing_url - candidates)} missing-URL keys are not "
        "discovery candidates",
    )
    finding(
        "no_candidate_lacks_billstatus_without_being_an_amendment",
        not missing_non_amendment,
        f"{len(missing_non_amendment)} non-amendment candidates have no "
        "BillStatus record",
        sample=sample(missing_non_amendment),
    )
    finding(
        "every_planned_official_text_version_reached_a_terminal_receipt",
        int(completed.get("dispatch_count") or 0)
        == int(completed.get("accepted_count") or 0)
        + int(completed.get("failure_count") or 0),
        f"dispatched={completed.get('dispatch_count')} "
        f"accepted={completed.get('accepted_count')} "
        f"quarantined={completed.get('failure_count')}",
    )
    finding(
        "collector_performed_no_retries",
        int(completed.get("retry_waits") or 0) == 0
        and int(completed.get("attempts_per_job") or 0) == 1,
        f"retry_waits={completed.get('retry_waits')} "
        f"attempts_per_job={completed.get('attempts_per_job')}",
    )
    finding(
        "review_ledger_covers_the_discovery_membership_universe",
        not in_discovery_not_ledger,
        f"{len(in_discovery_not_ledger)} discovery edges are absent from the "
        "review ledger",
        sample=[f"{k}|{q}" for k, q in sample(in_discovery_not_ledger)],
    )
    finding(
        "review_ledger_introduces_no_edge_outside_discovery",
        not in_ledger_not_discovery,
        f"{len(in_ledger_not_discovery)} review-ledger edges are not in the "
        "discovery universe",
        sample=[f"{k}|{q}" for k, q in sample(in_ledger_not_discovery)],
    )
    finding(
        "every_accepted_full_text_receipt_has_its_archived_body",
        not archive_gaps,
        f"{len(archive_gaps)} of {accepted_receipt_count} accepted full-text "
        f"receipts have no verifiable archived body "
        f"({dict(archive_states)})",
        sample=sample(archive_gaps),
    )
    finding(
        "no_evidence_receipt_was_unreadable",
        not unreadable_receipts,
        f"{len(unreadable_receipts)} per-item evidence receipts could not be "
        "read; every one of them silently shrank a coverage count",
        sample=sample(unreadable_receipts),
    )

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "status": (
            "PASS_COVERAGE_RECONCILIATION"
            if all(item["pass"] for item in findings)
            else "COVERAGE_GAPS_PRESENT"
        ),
        "inputs": {
            "discovery_records": {
                "path": str(records_path),
                "sha256": sha256_file(records_path),
            },
            "discovery_memberships": {
                "path": str(memberships_path),
                "sha256": sha256_file(memberships_path),
            },
            "membership_review_ledger": {
                "path": str(ledger_path),
                "sha256": sha256_file(ledger_path),
            },
            "govinfo_run": {
                "path": str(govinfo_run),
                "run_id": completed.get("run_id"),
                "plan_sha256": completed.get("plan_sha256"),
                "completed_sha256": sha256_file(govinfo_run / "completed.json"),
            },
        },
        "discovery": {
            "candidates": len(candidates),
            "membership_edges": len(discovery_edges),
            "candidates_carrying_edges": len(edge_candidates),
        },
        "review_ledger": {
            "edges": len(ledger_edges),
            "dispositions": dict(ledger_dispositions),
            "discovery_edges_absent_from_ledger": len(in_discovery_not_ledger),
            "ledger_edges_absent_from_discovery": len(in_ledger_not_discovery),
        },
        "billstatus": {
            "records": len(billstatus_keys),
            "candidates_without_record": len(missing_billstatus),
            "candidates_without_record_by_measure_kind": dict(missing_by_kind),
            "non_amendment_candidates_without_record": len(missing_non_amendment),
        },
        "govinfo_official_text": {
            "candidates_with_planned_versions": len(planned_keys),
            "candidates_with_no_planned_version": len(no_planned_text),
            "all_versions_accepted": len(fully_reviewed),
            "partially_accepted": len(partially_reviewed),
            "zero_accepted": len(zero_accepted),
            "zero_accepted_keys": sorted(zero_accepted),
            "partially_accepted_sample": sample(partially_reviewed),
            "quarantined_versions": len(quarantined),
            "declared_missing_url_keys": sorted(observed_missing_url),
        },
        "congress_gov_full_text": {
            # Asserted = what the receipts claim.  Present = what survives on
            # disk.  They diverged by 2,885 files once; never collapse them.
            "accepted_receipts": accepted_receipt_count,
            "candidates_with_asserted_text": len(asserted_text_keys),
            "candidates_with_present_text": len(present_text_keys),
            "candidates_with_accepted_text": len(present_text_keys),
            "candidates_with_asserted_but_absent_text": len(
                missing_archive_keys
            ),
            "archived_body_states": dict(archive_states),
            "archived_body_gap_sample": sample(archive_gaps),
            "coverage_fraction_of_candidates": round(
                len(present_text_keys) / max(1, len(candidates)), 4
            ),
            "asserted_coverage_fraction_of_candidates": round(
                len(asserted_text_keys) / max(1, len(candidates)), 4
            ),
            "candidates_without_accepted_text": len(
                candidates - present_text_keys
            ),
        },
        "evidence_readability": {
            "unreadable_per_item_receipts": len(unreadable_receipts),
            "unreadable_per_item_receipt_sample": sample(unreadable_receipts),
            "control_file_policy": "fail_closed",
            "per_item_receipt_policy": "counted_and_surfaced",
        },
        "findings": findings,
        "safety": {
            "collection_performed": False,
            "culling_performed": False,
            "scoring_performed": False,
            "main_corpus_write_performed": False,
        },
    }
    atomic_write(
        Path(args.out).resolve() / "receipt.json",
        canonical_json_bytes(receipt) + b"\n",
    )
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True)
    parser.add_argument("--memberships", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--billstatus", required=True)
    parser.add_argument("--full-text-receipts", required=True)
    parser.add_argument("--govinfo-run", required=True)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        receipt = audit(parse_args(argv))
    except (ReconciliationError, OSError, ValueError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS_COVERAGE_RECONCILIATION" else 3


if __name__ == "__main__":
    raise SystemExit(main())
