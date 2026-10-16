#!/usr/bin/env python3
"""Assemble immutable per-candidate official-evidence envelopes.

This is the missing producer for ``acu-legislation-candidate-evidence-v1``:
``legislation_culling_integration_contract`` defines the envelope and
``run_reversible_legislation_culling`` consumes it, but nothing built it.

The program is deliberately conservative.  It only reports evidence it can
bind to an immutable receipt, and it never invents, upgrades, or relabels
provenance:

* A title is ``available`` only from the Congress.gov discovery record.
* A summary is ``officially_unavailable`` only when the GovInfo BillStatus
  record authoritatively lists no summary; when a summary exists but has not
  been fetched from Congress.gov it is reported with its real, non-authoritative
  provenance and is deliberately *not* treated as ready.
* Full text is ``accepted`` only when the Congress.gov archive physically
  rehashes to the receipt's body digest and the marker-bounded extraction
  succeeds.
* ``category_evidence`` and ``keyword_review`` are emitted empty until a real
  19-category review exists.  Missing review is always flagged, never junk.

It reads discovery, BillStatus, Congress.gov archives, and the GovInfo version
receipts, and writes only into its own output root.  It performs no culling,
scoring, sync, promotion, or main-corpus write.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zlib
from typing import Any, Iterator, Mapping

try:
    from lane_file_io import open_ndjson
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson
    from fec.legislation_measure_key import CANONICAL_KEY_RE


EVIDENCE_SCHEMA = "acu-legislation-candidate-evidence-v1"
RECEIPT_SCHEMA = "acu-legislation-candidate-evidence-receipt-v1"
EXTRACTION_SCHEMA = "acu-legislation-full-text-extraction-receipt-v1"
TOOL_NAME = "build_legislation_candidate_evidence.py"
TOOL_VERSION = "1.0.0"

CONGRESS_URL_RE = re.compile(r"^https://(?:www\.)?congress\.gov/", re.I)
AMENDMENT_KINDS = {"hamdt", "samdt"}


class EvidenceBuildError(RuntimeError):
    """Raised when evidence cannot be assembled without ambiguity."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceBuildError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


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
    with open_ndjson(path) as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise EvidenceBuildError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from error
            require(
                isinstance(row, dict), f"{path.name}:{line_number}: not an object"
            )
            yield row


def measure_kind(canonical_key: str) -> str:
    return canonical_key.split(":")[1]


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def build_title(record: Mapping[str, Any]) -> dict[str, Any]:
    """Titles come from the Congress.gov discovery record, or not at all."""

    text = first_text(
        record.get("title"), record.get("purpose"), record.get("heading")
    )
    url = str(record.get("canonical_url") or "")
    if not text or not CONGRESS_URL_RE.match(url):
        return {
            "status": "missing",
            "reason": "no Congress.gov official title captured in discovery",
        }
    return {
        "status": "available",
        "source_url": url,
        "text_sha256": sha256_text(text),
        "text_length": len(text),
        "provenance": "congress_gov_advanced_search_record",
    }


def build_summary(
    billstatus: Mapping[str, Any] | None,
    billstatus_sha256: str | None,
) -> dict[str, Any]:
    """A summary is only ever explicitly-absent or honestly non-authoritative."""

    if billstatus is None or billstatus_sha256 is None:
        return {
            "status": "missing",
            "reason": "no GovInfo BillStatus record for this candidate",
        }
    summaries = billstatus.get("summaries") or []
    if not summaries:
        return {
            "status": "officially_unavailable",
            "unavailability_reason": (
                "GovInfo BillStatus authoritatively lists no CRS summary for "
                "this measure"
            ),
            "availability_receipt_sha256": billstatus_sha256,
            "provenance": "govinfo_billstatus_record",
        }
    return {
        # Deliberately not "available": the text exists but has not been
        # fetched from Congress.gov, and claiming a Congress.gov source URL
        # for GovInfo-sourced text would falsify provenance.
        "status": "available_pending_congress_gov_fetch",
        "reason": (
            "summary text present in GovInfo BillStatus; Congress.gov "
            "official summary not yet collected"
        ),
        "summary_count": len(summaries),
        "text_sha256": sha256_bytes(canonical_json_bytes(summaries)),
        "availability_receipt_sha256": billstatus_sha256,
        "provenance": "govinfo_billstatus_record",
    }


def build_full_text(
    receipt: Mapping[str, Any] | None,
    archive_path: Path | None,
    extraction_root: Path,
    version_inventory: Mapping[str, Any],
) -> dict[str, Any]:
    """Full text is accepted only if the archive physically reverifies."""

    if receipt is None:
        return {
            "status": "missing",
            "reason": "no Congress.gov full-text receipt for this candidate",
            "version_inventory": version_inventory,
        }
    if receipt.get("status") != "accepted":
        return {
            "status": "quarantined",
            "reason": f"receipt status is {receipt.get('status')!r}",
            "version_inventory": version_inventory,
        }
    body_sha256 = str(receipt.get("body_sha256") or "")
    expected_url = str(receipt.get("expected_url") or "")
    marker = str(receipt.get("expected_marker") or "")

    if archive_path is None or not archive_path.is_file():
        return {
            "status": "archive_missing",
            "reason": "accepted receipt has no physical archive",
            "expected_url": expected_url,
            "version_inventory": version_inventory,
        }

    try:
        body = gzip.decompress(archive_path.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as error:
        return {
            "status": "archive_unreadable",
            "reason": f"{type(error).__name__}: {error}",
            "expected_url": expected_url,
            "version_inventory": version_inventory,
        }

    decompressed_sha256 = sha256_bytes(body)
    if decompressed_sha256 != body_sha256:
        return {
            "status": "archive_drift",
            "reason": "archive does not rehash to the receipt body digest",
            "expected_url": expected_url,
            "body_sha256": body_sha256,
            "decompressed_body_sha256": decompressed_sha256,
            "version_inventory": version_inventory,
        }

    text = body.decode("utf-8", errors="replace")
    index = text.find(marker) if marker else -1
    if index < 0:
        return {
            "status": "marker_absent",
            "reason": f"expected marker {marker!r} not found in archive",
            "expected_url": expected_url,
            "version_inventory": version_inventory,
        }
    bounded = text[index:]
    marker_sha256 = sha256_text(bounded)

    extraction = {
        "schema": EXTRACTION_SCHEMA,
        "canonical_key": receipt.get("canonical_key"),
        "expected_url": expected_url,
        "archive_path": str(archive_path.resolve()),
        "archive_sha256": sha256_file(archive_path),
        "body_sha256": body_sha256,
        "decompressed_body_sha256": decompressed_sha256,
        "expected_marker": marker,
        "marker_offset": index,
        "marker_bounded_bytes": len(bounded.encode("utf-8")),
        "marker_bounded_text_sha256": marker_sha256,
    }
    extraction_body = canonical_json_bytes(extraction) + b"\n"
    extraction_sha256 = sha256_bytes(extraction_body)
    extraction_path = (
        extraction_root / extraction_sha256[:2] / f"{extraction_sha256}.json"
    )
    if not extraction_path.exists():
        atomic_write(extraction_path, extraction_body)

    return {
        "status": "accepted",
        "expected_url": expected_url,
        "final_url": str(receipt.get("final_url") or ""),
        "body_sha256": body_sha256,
        "archive_reverified": True,
        "decompressed_body_sha256": decompressed_sha256,
        "marker_bounded_text_sha256": marker_sha256,
        "extraction_receipt_sha256": extraction_sha256,
        "cookie_values_persisted": bool(receipt.get("cookie_values_persisted")),
        "validation": dict(receipt.get("validation") or {}),
        "version_inventory": version_inventory,
    }


def build_stage(
    billstatus: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if billstatus is None:
        return {"status": "missing", "reason": "no BillStatus record"}
    actions = billstatus.get("actions") or []
    if not actions:
        return {"status": "missing", "reason": "BillStatus lists no actions"}
    # BillStatus lists actions newest first, so list position is not recency.
    latest = max(
        (row for row in actions if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("action_date") or ""),
            str(row.get("action_time") or ""),
            -int(row.get("action_index") or 0),
        ),
        default=None,
    )
    if latest is None:
        return {"status": "missing", "reason": "BillStatus lists no actions"}
    value = first_text(
        latest.get("action_type"), latest.get("text"), latest.get("action_code")
    )
    if not value:
        return {"status": "missing", "reason": "latest action has no stage value"}
    return {
        "status": "available",
        "value": value,
        "action_count": len(actions),
        "actions_sha256": sha256_bytes(canonical_json_bytes(actions)),
    }


def build_relationships(
    canonical_key: str,
    billstatus: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if measure_kind(canonical_key) in AMENDMENT_KINDS:
        return {
            "status": "not_required",
            "reason": "amendment roles are carried by the amended measure",
        }
    if billstatus is None:
        return {"status": "missing", "reason": "no BillStatus record"}
    sponsors = billstatus.get("sponsors") or []
    cosponsors = billstatus.get("cosponsors") or []
    if not sponsors:
        return {"status": "missing", "reason": "BillStatus lists no sponsor"}
    original = [
        row
        for row in cosponsors
        if isinstance(row, dict) and row.get("original_cosponsor") is True
    ]
    role_provenance_complete = all(
        isinstance(row, dict) and row.get("role_provenance_complete") is True
        for row in [*sponsors, *cosponsors]
    )
    return {
        "status": "complete" if role_provenance_complete else "incomplete",
        "role_provenance_complete": role_provenance_complete,
        "sponsor_count": len(sponsors),
        "original_cosponsor_count": len(original),
        "cosponsor_count": len(cosponsors),
        "edges_sha256": sha256_bytes(
            canonical_json_bytes(
                {"sponsors": sponsors, "cosponsors": cosponsors}
            )
        ),
    }


def load_billstatus(root: Path) -> dict[str, tuple[Mapping[str, Any], str]]:
    index: dict[str, tuple[Mapping[str, Any], str]] = {}
    for path in sorted(root.rglob("*.json")):
        raw = path.read_bytes()
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        key = str(row.get("canonical_key") or "")
        if not CANONICAL_KEY_RE.fullmatch(key):
            continue
        index[key] = (row, sha256_bytes(raw))
    return index


def load_full_text_receipts(
    root: Path,
) -> dict[str, tuple[Mapping[str, Any], Path | None]]:
    index: dict[str, tuple[Mapping[str, Any], Path | None]] = {}
    for path in sorted(root.rglob("receipt.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = str(row.get("canonical_key") or "")
        if not CANONICAL_KEY_RE.fullmatch(key):
            continue
        archive = None
        html_file = str(row.get("html_file") or "")
        if html_file:
            candidate = Path(html_file)
            if candidate.is_file():
                archive = candidate
        index[key] = (row, archive)
    return index


def load_govinfo_inventory(run_root: Path | None) -> dict[str, dict[str, int]]:
    """Count planned vs accepted official text versions per candidate."""

    inventory: dict[str, dict[str, int]] = {}
    if run_root is None:
        return inventory
    all_jobs = run_root / "all_jobs.ndjson"
    if all_jobs.is_file():
        for row in read_ndjson(all_jobs):
            key = str(row.get("canonical_key") or "")
            entry = inventory.setdefault(key, {"expected": 0, "accepted": 0})
            entry["expected"] += 1
    accepted_root = run_root / "receipts" / "accepted"
    if accepted_root.is_dir():
        for path in accepted_root.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            key = str(row.get("canonical_key") or "")
            entry = inventory.setdefault(key, {"expected": 0, "accepted": 0})
            entry["accepted"] += 1
    return inventory


def build(args: argparse.Namespace) -> Mapping[str, Any]:
    records_path = Path(args.records).resolve()
    memberships_path = Path(args.memberships).resolve()
    output_root = Path(args.out).resolve()

    records: dict[str, Mapping[str, Any]] = {}
    record_hashes: dict[str, str] = {}
    for row in read_ndjson(records_path):
        key = str(row.get("canonical_key") or "")
        if not CANONICAL_KEY_RE.fullmatch(key):
            continue
        require(key not in records, f"duplicate discovery record: {key}")
        records[key] = row
        record_hashes[key] = sha256_bytes(canonical_json_bytes(row))

    universe: dict[str, set[str]] = {}
    membership_count = 0
    for row in read_ndjson(memberships_path):
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "").strip()
        if not CANONICAL_KEY_RE.fullmatch(key) or not query_id:
            continue
        universe.setdefault(key, set()).add(query_id)
        membership_count += 1

    billstatus = load_billstatus(Path(args.billstatus).resolve())
    receipts = load_full_text_receipts(Path(args.full_text_receipts).resolve())
    inventory = load_govinfo_inventory(
        Path(args.govinfo_run).resolve() if args.govinfo_run else None
    )

    discovery_receipt = {
        "path": str(records_path),
        "sha256": sha256_file(records_path),
    }
    extraction_root = output_root / "extraction_receipts"

    rows: list[bytes] = []
    counters = {
        "candidates": 0,
        "title_available": 0,
        "summary_officially_unavailable": 0,
        "summary_pending_congress_gov": 0,
        "full_text_accepted": 0,
        "full_text_missing": 0,
        "stage_available": 0,
        "relationships_complete_or_not_required": 0,
        "candidates_without_membership": 0,
    }
    for key in sorted(records):
        record = records[key]
        query_ids = sorted(universe.get(key, set()))
        if not query_ids:
            counters["candidates_without_membership"] += 1
            continue
        status = billstatus.get(key)
        billstatus_row = status[0] if status else None
        billstatus_sha = status[1] if status else None
        receipt_entry = receipts.get(key)
        version_inventory_counts = inventory.get(key, {"expected": 0, "accepted": 0})
        version_inventory = {
            "expected_version_count": version_inventory_counts["expected"],
            "reviewed_version_count": version_inventory_counts["accepted"],
            "all_versions_reviewed": bool(
                version_inventory_counts["expected"] > 0
                and version_inventory_counts["expected"]
                == version_inventory_counts["accepted"]
            ),
        }

        title = build_title(record)
        summary = build_summary(billstatus_row, billstatus_sha)
        full_text = build_full_text(
            receipt_entry[0] if receipt_entry else None,
            receipt_entry[1] if receipt_entry else None,
            extraction_root,
            version_inventory,
        )
        stage = build_stage(billstatus_row)
        relationships = build_relationships(key, billstatus_row)

        counters["candidates"] += 1
        counters["title_available"] += title["status"] == "available"
        counters["summary_officially_unavailable"] += (
            summary["status"] == "officially_unavailable"
        )
        counters["summary_pending_congress_gov"] += (
            summary["status"] == "available_pending_congress_gov_fetch"
        )
        counters["full_text_accepted"] += full_text["status"] == "accepted"
        counters["full_text_missing"] += full_text["status"] == "missing"
        counters["stage_available"] += stage["status"] == "available"
        counters["relationships_complete_or_not_required"] += relationships[
            "status"
        ] in {"complete", "not_required"}

        envelope = {
            "schema": EVIDENCE_SCHEMA,
            "canonical_key": key,
            "candidate_preserved": True,
            "discovery": {
                **discovery_receipt,
                "record_sha256": record_hashes[key],
            },
            "membership_query_ids": query_ids,
            "official_evidence": {
                "title": title,
                "summary": summary,
                "full_text": full_text,
            },
            # A real 19-category adjudication has not been performed.  Empty
            # is the honest value; it keeps every membership flagged and can
            # never authorize junk.
            "category_evidence": [],
            "keyword_review": {
                "status": "not_performed",
                "reason": "no 19-category keyword adjudication exists yet",
            },
            "stage": stage,
            "relationships": relationships,
        }
        rows.append(canonical_json_bytes(envelope) + b"\n")

    evidence_path = output_root / "candidate_evidence.ndjson"
    body = b"".join(rows)
    atomic_write(evidence_path, body)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS_CANDIDATE_EVIDENCE_ASSEMBLED",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "inputs": {
            "discovery_records": discovery_receipt,
            "discovery_memberships": {
                "path": str(memberships_path),
                "sha256": sha256_file(memberships_path),
                "membership_count": membership_count,
            },
            "billstatus_records": {
                "path": str(Path(args.billstatus).resolve()),
                "record_count": len(billstatus),
            },
            "full_text_receipts": {
                "path": str(Path(args.full_text_receipts).resolve()),
                "receipt_count": len(receipts),
            },
            "govinfo_run": (
                str(Path(args.govinfo_run).resolve())
                if args.govinfo_run
                else None
            ),
        },
        "outputs": {
            "candidate_evidence.ndjson": {
                "path": str(evidence_path),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "record_count": len(rows),
            }
        },
        "counts": counters,
        "safety": {
            "category_review_performed": False,
            "keyword_review_performed": False,
            "culling_performed": False,
            "scoring_performed": False,
            "main_corpus_write_performed": False,
        },
    }
    atomic_write(
        output_root / "receipt.json", canonical_json_bytes(receipt) + b"\n"
    )
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", required=True)
    parser.add_argument("--memberships", required=True)
    parser.add_argument("--billstatus", required=True)
    parser.add_argument("--full-text-receipts", required=True)
    parser.add_argument("--govinfo-run", default=None)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        receipt = build(parse_args(argv))
    except (EvidenceBuildError, OSError, ValueError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
