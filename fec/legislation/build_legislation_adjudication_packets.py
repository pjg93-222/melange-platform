#!/usr/bin/env python3
"""Assemble deterministic candidate-level adjudication packets, offline.

One packet is one *proposal unit*: a single Congress-qualified measure carrying
all nineteen category slots and every discovered membership.  Nothing here
adjudicates, culls, scores, synchronises, promotes, or writes the corpus, the
junk ledger, rank outputs, integration state, or production.  It makes no
network request; every byte is re-derived from local hash-manifested archives
and re-verified against the receipt that produced it.

Evidence sources, in the order the relevance-review method gives them:

* official title -- from the sealed candidate-evidence envelope;
* official CRS or GovInfo BillStatus summary -- GovInfo is admissible summary
  evidence under the user's original review contract;
* Congress.gov official full text -- re-extracted from the archived page by the
  extraction receipt's marker offset and re-hashed;
* all official text versions -- joined from the GovInfo bill-version collector
  run, verified accepted-receipt by accepted-receipt against
  ``object_path``/``content_sha256``.  A version whose object is not present
  locally is recorded as unavailable.  The single displayed Congress.gov page
  is *never* counted as an all-version review;
* stage, action history, and sponsor/original-cosponsor/cosponsor roles -- from
  the GovInfo BillStatus enrichment records.
* frozen original-method evidence -- category boundaries, recovered operative
  amendment text, amendment offerer and parent context, attributed sponsor
  floor remarks, trusted press corroboration, and the separately preserved
  letter track.

Search membership is carried as *provenance only*.  It is never decision
evidence, and term absence is never negative evidence.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter
import gzip
import hashlib
import html as html_module
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterator, Mapping, Sequence

try:
    from legislation.legislation_culling_integration_contract import validate_candidate_evidence
    from legislation_measure_key import CANONICAL_KEY_RE
    from legislation.legislation_original_method_evidence import (
        OriginalMethodEvidence,
        OriginalMethodEvidenceError,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.legislation.legislation_culling_integration_contract import (
        validate_candidate_evidence,
    )
    from fec.legislation_measure_key import CANONICAL_KEY_RE
    from fec.legislation.legislation_original_method_evidence import (
        OriginalMethodEvidence,
        OriginalMethodEvidenceError,
    )


PACKET_SCHEMA = "acu-legislation-opus-adjudication-packet-v2"
MANIFEST_SCHEMA = "acu-legislation-opus-adjudication-packet-manifest-v2"
RECEIPT_SCHEMA = "acu-legislation-opus-adjudication-packet-receipt-v2"
TOOL_NAME = "build_legislation_adjudication_packets.py"
TOOL_VERSION = "2.0.0"
EXPECTED_CATEGORY_COUNT = 19
AMENDMENT_TYPES = {"hamdt", "samdt"}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONGRESS_URL_RE = re.compile(r"^https://(?:www\.)?congress\.gov/", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S
)
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANKLINES_RE = re.compile(r"\n{3,}")


class PacketBuildError(RuntimeError):
    """A fail-closed input, archive, or verification error."""


def utc_stamp_free_canonical_bytes(value: Any) -> bytes:
    """Canonical JSON bytes; no clock is consulted, so output is reproducible."""

    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_sha(value: Any) -> bool:
    return bool(SHA256_RE.fullmatch(str(value or "")))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PacketBuildError(f"cannot read JSON {path}: {exc}") from exc


def iter_ndjson(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        raise PacketBuildError(f"input does not exist: {path}")
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PacketBuildError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(value, dict):
                raise PacketBuildError(f"row {line_number} of {path} is not an object")
            yield value


def exclusive_write(path: Path, data: bytes) -> str:
    """Create one immutable artifact, or verify a byte-identical existing one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("xb") as handle:
            created = True
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != data:
            raise PacketBuildError(f"immutable artifact collision: {path}")
    except BaseException:
        if created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return sha256_bytes(data)


def commit_staged_file(staging: Path, destination: Path, expected_sha256: str) -> None:
    """Commit a large staged file without loading or overwriting it in memory."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as output:
            created = True
            with staging.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        if sha256_file(destination) != expected_sha256:
            raise PacketBuildError(f"immutable artifact collision: {destination}")
    except BaseException:
        if created:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
    if sha256_file(destination) != expected_sha256:
        raise PacketBuildError(f"committed packet hash differs: {destination}")


def html_to_text(raw: bytes) -> str:
    """Reduce an archived Congress.gov fragment to reviewable plain text."""

    text = raw.decode("utf-8", errors="replace")
    text = SCRIPT_STYLE_RE.sub(" ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|tr|h[1-6])\s*>", "\n", text, flags=re.I)
    text = TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    text = WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return BLANKLINES_RE.sub("\n\n", text).strip()


def xml_to_text(raw: bytes) -> str:
    """Reduce an official GovInfo bill XML object to reviewable plain text."""

    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<\?xml[^>]*\?>", " ", text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(
        r"<(metadata|dublinCore)\b.*?</\1>", " ", text, flags=re.I | re.S
    )
    text = re.sub(
        r"</(section|subsection|paragraph|title|header|text|toc-entry)\s*>",
        "\n",
        text,
        flags=re.I,
    )
    text = TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    text = WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return BLANKLINES_RE.sub("\n\n", text).strip()


def load_extraction_receipts(root: Path) -> dict[str, dict[str, Any]]:
    if not root.is_dir():
        raise PacketBuildError(f"extraction receipt root does not exist: {root}")
    # Keyed by the receipt's own content hash, not canonical_key: the evidence
    # builder never prunes old content-addressed receipts, so one key may
    # legitimately have several receipts on disk.
    receipts: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        receipt_sha = sha256_file(path)
        if receipt_sha in receipts:
            continue
        row = read_json(path)
        if not isinstance(row, dict):
            raise PacketBuildError(f"extraction receipt is not an object: {path}")
        key = str(row.get("canonical_key") or "")
        if not key:
            raise PacketBuildError(f"extraction receipt has no canonical_key: {path}")
        row["_receipt_path"] = str(path)
        receipts[receipt_sha] = row
    return receipts


def bind_extraction_receipt(
    evidence: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return the extraction receipt the evidence envelope itself names.

    None means no receipt is named or the named one is not on disk. A receipt
    that is named but fails the sha, canonical_key or body_sha256 binding
    yields a sentinel that congress_gov_full_text reports as not bound.
    """

    full_text = (evidence.get("official_evidence") or {}).get("full_text") or {}
    declared = str(full_text.get("extraction_receipt_sha256") or "")
    if not declared:
        return None
    unbound: dict[str, Any] = {"_unbound": True}
    if not valid_sha(declared):
        return unbound
    extraction = receipts.get(declared)
    if extraction is None:
        return None
    if str(extraction.get("body_sha256") or "") != str(full_text.get("body_sha256") or ""):
        return unbound
    if str(extraction.get("canonical_key") or "") != str(evidence.get("canonical_key") or ""):
        return unbound
    return extraction


def load_govinfo_versions(run_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Index every accepted GovInfo bill-version receipt by candidate.

    The object body is *not* read here; presence and hash verification happen
    per packet so that an absent object is recorded rather than assumed.
    """

    accepted = run_root / "receipts" / "accepted"
    if not accepted.is_dir():
        raise PacketBuildError(f"GovInfo accepted receipts absent: {accepted}")
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(accepted.rglob("*.json")):
        row = read_json(path)
        key = str(row.get("canonical_key") or "")
        if not key:
            raise PacketBuildError(f"GovInfo receipt has no canonical_key: {path}")
        row["_receipt_path"] = str(path)
        row["_receipt_sha256"] = sha256_file(path)
        by_candidate.setdefault(key, []).append(row)
    for key, rows in by_candidate.items():
        rows.sort(
            key=lambda item: (
                int(item.get("version_index") or 0),
                str(item.get("version_code") or ""),
                str(item.get("job_id") or ""),
            )
        )
    return by_candidate


def load_billstatus_index(parsed_root: Path) -> dict[str, Path]:
    if not parsed_root.is_dir():
        raise PacketBuildError(f"BillStatus parsed root does not exist: {parsed_root}")
    index: dict[str, Path] = {}
    for path in sorted(parsed_root.rglob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        key = str(row.get("canonical_key") or "")
        if key:
            index[key] = path
    return index


def congress_gov_full_text(
    evidence: Mapping[str, Any],
    extraction: Mapping[str, Any] | None,
    *,
    materialize: bool,
) -> dict[str, Any]:
    """Re-derive and re-verify the archived Congress.gov displayed text."""

    full_text = (evidence.get("official_evidence") or {}).get("full_text") or {}
    block: dict[str, Any] = {
        "status": full_text.get("status"),
        "expected_url": full_text.get("expected_url"),
        "final_url": full_text.get("final_url"),
        "body_sha256": full_text.get("body_sha256"),
        "marker_bounded_text_sha256": full_text.get("marker_bounded_text_sha256"),
        "archive_reverified": full_text.get("archive_reverified"),
        "displayed_version_only": True,
        "all_version_review_supported": False,
        "text": None,
        "text_bytes": None,
        "text_verified": False,
        "unavailable_reason": None,
    }
    if full_text.get("status") not in {"accepted", "resumed"}:
        block["unavailable_reason"] = "congress_gov_full_text_not_accepted"
        return block
    if extraction is None:
        block["unavailable_reason"] = "extraction_receipt_absent"
        return block
    if extraction.get("_unbound"):
        block["unavailable_reason"] = "extraction_receipt_not_bound"
        return block

    archive_path = Path(str(extraction.get("archive_path") or ""))
    block["archive_path"] = str(archive_path)
    block["marker_offset"] = extraction.get("marker_offset")
    block["marker_bounded_bytes"] = extraction.get("marker_bounded_bytes")
    if not archive_path.is_file():
        block["unavailable_reason"] = "archived_page_absent"
        return block
    if not materialize:
        block["unavailable_reason"] = "not_materialized_candidate_is_ineligible"
        return block
    declared_archive_sha = str(extraction.get("archive_sha256") or "")
    if not valid_sha(declared_archive_sha):
        block["unavailable_reason"] = "archive_sha256_invalid"
        return block
    if sha256_file(archive_path) != declared_archive_sha:
        block["unavailable_reason"] = "archive_file_hash_drift"
        return block

    try:
        body = gzip.open(archive_path, "rb").read()
    except OSError as exc:
        block["unavailable_reason"] = f"archive_unreadable: {exc.__class__.__name__}"
        return block
    if sha256_bytes(body) != str(extraction.get("decompressed_body_sha256") or ""):
        block["unavailable_reason"] = "archive_body_hash_drift"
        return block

    # The evidence builder records marker_offset as an index into the UTF-8
    # decoded string, not the compressed or decompressed byte stream.
    offset = int(extraction.get("marker_offset") or 0)
    text_body = body.decode("utf-8", errors="replace")
    bounded_text = text_body[offset:]
    fragment = bounded_text.encode("utf-8")
    if len(fragment) != int(extraction.get("marker_bounded_bytes") or 0):
        block["unavailable_reason"] = "marker_bounded_length_drift"
        return block
    if sha256_bytes(fragment) != str(extraction.get("marker_bounded_text_sha256") or ""):
        block["unavailable_reason"] = "marker_bounded_slice_hash_drift"
        return block

    text = html_to_text(fragment)
    block["text"] = text
    block["text_bytes"] = len(text.encode("utf-8"))
    block["text_verified"] = True
    block["extraction_receipt_path"] = extraction.get("_receipt_path")
    return block


def official_text_versions(
    evidence: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    *,
    materialize: bool,
) -> dict[str, Any]:
    """Join, verify, and parse every official GovInfo text version."""

    inventory = (
        (evidence.get("official_evidence") or {}).get("full_text") or {}
    ).get("version_inventory") or {}
    expected = int(inventory.get("expected_version_count") or 0)
    versions: list[dict[str, Any]] = []
    parsed_ok = 0
    for receipt in receipts:
        object_path = Path(str(receipt.get("object_path") or ""))
        content_sha = str(receipt.get("content_sha256") or "")
        entry: dict[str, Any] = {
            "version_code": receipt.get("version_code"),
            "version_type": receipt.get("version_type"),
            "version_date": receipt.get("version_date"),
            "version_index": receipt.get("version_index"),
            "govinfo_package": receipt.get("govinfo_package"),
            "official_source_url": receipt.get("final_url") or receipt.get("url"),
            "content_sha256": content_sha,
            "content_bytes": receipt.get("content_bytes"),
            "accepted_receipt_path": receipt.get("_receipt_path"),
            "accepted_receipt_sha256": receipt.get("_receipt_sha256"),
            "identity_validation": receipt.get("identity_validation"),
            "object_path": str(object_path),
            "object_present": False,
            "content_verified": False,
            "text": None,
            "text_bytes": None,
            "unavailable_reason": None,
        }
        if receipt.get("kind") != "accepted" or int(receipt.get("http_status") or 0) != 200:
            entry["unavailable_reason"] = "receipt_is_not_an_accepted_200"
            versions.append(entry)
            continue
        if not valid_sha(content_sha):
            entry["unavailable_reason"] = "receipt_content_sha256_invalid"
            versions.append(entry)
            continue
        if not object_path.is_file():
            entry["unavailable_reason"] = "official_xml_object_absent_from_local_store"
            versions.append(entry)
            continue
        entry["object_present"] = True
        if not materialize:
            entry["unavailable_reason"] = "not_materialized_candidate_is_ineligible"
            versions.append(entry)
            continue
        raw = object_path.read_bytes()
        if sha256_bytes(raw) != content_sha:
            entry["unavailable_reason"] = "official_xml_object_hash_drift"
            versions.append(entry)
            continue
        entry["content_verified"] = True
        text = xml_to_text(raw)
        if not text:
            entry["unavailable_reason"] = "official_xml_object_yielded_no_text"
            versions.append(entry)
            continue
        entry["text"] = text
        entry["text_bytes"] = len(text.encode("utf-8"))
        parsed_ok += 1
        versions.append(entry)

    complete = bool(expected >= 1 and parsed_ok == expected and len(versions) >= expected)
    return {
        "expected_version_count": expected,
        "declared_all_versions_reviewed": inventory.get("all_versions_reviewed"),
        "declared_reviewed_version_count": inventory.get("reviewed_version_count"),
        "govinfo_accepted_receipt_count": len(receipts),
        "locally_parsed_version_count": parsed_ok,
        "all_versions_locally_parsed": complete,
        "all_version_review_complete": complete,
        "single_congress_gov_page_is_not_an_all_version_review": True,
        "versions": versions,
    }


def summary_block(
    evidence: Mapping[str, Any],
    billstatus: Mapping[str, Any] | None,
    *,
    amendment: bool,
) -> dict[str, Any]:
    summary = (evidence.get("official_evidence") or {}).get("summary") or {}
    status = str(summary.get("status") or "")
    block: dict[str, Any] = {
        "status": status,
        "source_kind": "congress_gov_crs",
        "source_url": summary.get("source_url"),
        "text": None,
        "text_sha256": summary.get("text_sha256"),
        "unavailability_reason": summary.get("unavailability_reason"),
        "availability_receipt_sha256": summary.get("availability_receipt_sha256"),
        "settled": False,
        "absence_is_neutral": True,
    }
    if amendment:
        block.update(
            {
                "status": "not_applicable_to_amendment",
                "source_kind": "not_applicable",
                "settled": True,
                "unavailability_reason": (
                    "CRS bill summaries are not an amendment evidence requirement; "
                    "the recovered operative amendment text is reviewed separately"
                ),
            }
        )
        return block
    if status == "available" and valid_sha(summary.get("text_sha256")) and CONGRESS_URL_RE.match(
        str(summary.get("source_url") or "")
    ):
        block["text"] = summary.get("text")
        block["settled"] = True
    elif status == "officially_unavailable" and valid_sha(
        summary.get("availability_receipt_sha256")
    ) and str(summary.get("unavailability_reason") or "").strip():
        block["settled"] = True

    held: list[dict[str, Any]] = []
    for item in (billstatus or {}).get("summaries") or []:
        if isinstance(item, Mapping) and str(item.get("text") or "").strip():
            held.append(
                {
                    "action_date": item.get("action_date"),
                    "action_description": item.get("action_description"),
                    "version_code": item.get("version_code"),
                    "text": html_to_text(str(item.get("text")).encode("utf-8")),
                }
            )
    if held:
        govinfo_url = (billstatus or {}).get("provenance", {}).get("source_url")
        block["govinfo_billstatus_summary"] = {
            "admissible_as_summary_evidence": True,
            "provenance": govinfo_url,
            "reason": (
                "The original review contract expressly accepts GovInfo as "
                "summary evidence of record."
            ),
            "summaries": held,
        }
        if not block["settled"]:
            joined = "\n\n".join(
                str(item.get("text") or "") for item in held if item.get("text")
            )
            block.update(
                {
                    "status": "available",
                    "source_kind": "govinfo_billstatus",
                    "source_url": govinfo_url,
                    "text": joined,
                    "text_sha256": sha256_bytes(joined.encode("utf-8")),
                    "settled": True,
                }
            )
    return block


def stage_and_relationships(
    evidence: Mapping[str, Any], billstatus: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    stage_evidence = evidence.get("stage") or {}
    relationships_evidence = evidence.get("relationships") or {}
    actions = []
    for item in (billstatus or {}).get("actions") or []:
        if isinstance(item, Mapping):
            actions.append(
                {
                    "action_index": item.get("action_index"),
                    "action_date": item.get("action_date"),
                    "action_type": item.get("action_type"),
                    "text": item.get("text"),
                    "source_system_name": item.get("source_system_name"),
                }
            )
    stage = {
        "status": stage_evidence.get("status"),
        "value": stage_evidence.get("value"),
        "reason": stage_evidence.get("reason"),
        "actions_sha256": stage_evidence.get("actions_sha256"),
        "action_count": len(actions),
        "actions": actions,
    }

    def people(field: str) -> list[dict[str, Any]]:
        out = []
        for item in (billstatus or {}).get(field) or []:
            if not isinstance(item, Mapping):
                continue
            out.append(
                {
                    "bioguide_id": item.get("bioguide_id"),
                    "full_name": item.get("full_name"),
                    "party": item.get("party"),
                    "state": item.get("state"),
                    "relationship_role": item.get("relationship_role"),
                    "original_cosponsor": item.get("original_cosponsor"),
                    "withdrawn_date": item.get("withdrawn_date"),
                    "role_provenance": item.get("role_provenance"),
                }
            )
        return out

    cosponsors = people("cosponsors")
    relationships = {
        "status": relationships_evidence.get("status"),
        "reason": relationships_evidence.get("reason"),
        "role_provenance_complete": relationships_evidence.get(
            "role_provenance_complete"
        ),
        "sponsors": people("sponsors"),
        "original_cosponsors": [
            row for row in cosponsors if row.get("original_cosponsor") is True
        ],
        "cosponsors": [
            row for row in cosponsors if row.get("original_cosponsor") is not True
        ],
    }
    return stage, relationships


def build_packet(
    *,
    worksheet: Mapping[str, Any],
    evidence: Mapping[str, Any],
    memberships: Sequence[Mapping[str, Any]],
    categories: Sequence[Mapping[str, Any]],
    extraction: Mapping[str, Any] | None,
    govinfo_receipts: Sequence[Mapping[str, Any]],
    billstatus: Mapping[str, Any] | None,
    original_method: OriginalMethodEvidence,
    max_text_bytes: int,
) -> dict[str, Any]:
    key = str(worksheet.get("canonical_key") or "")
    if not CANONICAL_KEY_RE.fullmatch(key):
        raise PacketBuildError(f"invalid canonical_key: {key!r}")
    readiness = validate_candidate_evidence(evidence)
    measure_type = str(worksheet.get("measure_type") or "").casefold()
    amendment = measure_type in AMENDMENT_TYPES
    contextual = original_method.candidate_evidence(key)

    title = (evidence.get("official_evidence") or {}).get("title") or {}
    summary = summary_block(evidence, billstatus, amendment=amendment)
    inventory = (
        (evidence.get("official_evidence") or {}).get("full_text") or {}
    ).get("version_inventory") or {}
    expected_versions = int(inventory.get("expected_version_count") or 0)

    amendment_text = (
        (contextual or {}).get("official_amendment_text") or {}
        if amendment
        else {}
    )
    amendment_text_available = (
        amendment_text.get("status") == "available"
        and bool(str(amendment_text.get("text") or "").strip())
    )
    amendment_context_available = bool(
        amendment_text_available
        or (contextual or {}).get("sponsor_floor_remarks")
        or (contextual or {}).get("floor_speech_rescue")
        or (contextual or {}).get("press")
    )

    # Structural gates that do not depend on reading any archive body.
    blockers: list[str] = []
    warnings: list[str] = []
    if evidence.get("candidate_preserved") is not True:
        blockers.append("candidate_preservation_assertion_absent")
    if not readiness["title_ready"]:
        blockers.append("official_title_not_available_from_congress_gov")
    if not summary["settled"]:
        blockers.append("official_crs_summary_neither_published_nor_officially_absent")
    if not readiness["full_text_ready"] and not amendment_text_available:
        if amendment:
            warnings.append("verified_official_amendment_text_not_available")
        else:
            blockers.append("official_full_text_not_accepted_from_congress_gov")
    if expected_versions < 1 and not amendment:
        blockers.append("no_official_text_version_is_declared_for_this_candidate")
    if amendment and not amendment_context_available:
        blockers.append("no_original_method_amendment_evidence_surface_is_available")
    if amendment and expected_versions < 1:
        warnings.append(
            "govinfo_has_no_standalone_amendment_version_inventory; "
            "absence_based_exclusion_is_not_permitted"
        )
    if amendment and not amendment_text_available:
        warnings.append(
            "floor_or_press_context_cannot_substitute_for_operative_amendment_text"
        )
    if len(categories) != EXPECTED_CATEGORY_COUNT:
        blockers.append("category_registry_is_not_exactly_nineteen")
    if not memberships:
        blockers.append("candidate_carries_no_discovered_membership")

    materialize = bool(readiness["full_text_ready"]) and not any(
        item
        in {
            "candidate_preservation_assertion_absent",
            "official_title_not_available_from_congress_gov",
            "category_registry_is_not_exactly_nineteen",
            "candidate_carries_no_discovered_membership",
        }
        for item in blockers
    )
    congress_text = congress_gov_full_text(
        evidence, extraction, materialize=materialize
    )
    versions = official_text_versions(
        evidence, govinfo_receipts, materialize=materialize
    )
    if materialize and not congress_text["text_verified"]:
        blockers.append(
            "congress_gov_full_text_could_not_be_reverified: "
            + str(congress_text["unavailable_reason"])
        )

    stage, relationships = stage_and_relationships(evidence, billstatus)

    include_evidence_complete = (
        not blockers
        and (
            bool(readiness["full_text_ready"])
            if not amendment
            else amendment_text_available
        )
    )
    text_bytes = int(congress_text.get("text_bytes") or 0) + sum(
        int(item.get("text_bytes") or 0) for item in versions["versions"]
    )
    if contextual:
        text_bytes += int(amendment_text.get("full_text_bytes") or 0)
        text_bytes += int(
            ((contextual.get("sponsor_floor_remarks") or {}).get("full_text_bytes"))
            or 0
        )
        text_bytes += sum(
            int((((item.get("body") or {}).get("full_text_bytes")) or 0))
            for item in contextual.get("press") or []
        )
    provider_dispatch_eligible = not blockers
    oversized = provider_dispatch_eligible and text_bytes > max_text_bytes
    if oversized:
        blockers.append("evidence_text_exceeds_configured_packet_ceiling")
        include_evidence_complete = False
        provider_dispatch_eligible = False

    slots = []
    discovered = {str(row.get("query_id") or "") for row in memberships}
    for category in categories:
        category_id = str(category.get("category_id") or "")
        slots.append(
            {
                "category_id": category_id,
                "label": category.get("label"),
                "scoring_category": category.get("scoring_category"),
                "query_sha256": category.get("query_sha256"),
                "is_discovered_membership": category_id in discovered,
                "state": "not_performed",
                "absence_is_neutral": True,
            }
        )

    membership_rows = []
    for row in sorted(memberships, key=lambda item: str(item.get("query_id") or "")):
        cause = row.get("membership_cause") or {}
        terms = row.get("query_terms") or {}
        membership_rows.append(
            {
                "query_id": row.get("query_id"),
                "queue_row_id": row.get("queue_row_id"),
                "label": cause.get("label"),
                "record_kind": cause.get("record_kind"),
                "scoring_category": cause.get("scoring_category"),
                "membership_non_dispositive": cause.get("membership_non_dispositive"),
                "ambiguous_short_act_alias": cause.get("ambiguous_short_act_alias"),
                "tier": row.get("tier"),
                "candidate_explicitly_named_in_query": terms.get(
                    "candidate_explicitly_named_in_query"
                ),
                "search_membership_is_provenance_not_evidence": True,
                "term_absence_is_never_negative_evidence": True,
            }
        )

    packet = {
        "schema": PACKET_SCHEMA,
        "canonical_key": key,
        "proposal_unit": "candidate",
        "candidate": {
            "display_number": worksheet.get("display_number"),
            "congress": worksheet.get("congress"),
            "measure_type": worksheet.get("measure_type"),
            "number": worksheet.get("number"),
            "record_kind": worksheet.get("record_kind"),
            "canonical_url": (worksheet.get("links") or {}).get("canonical_url"),
            "text_url": (worksheet.get("links") or {}).get("text_url"),
            "sponsor_display": worksheet.get("sponsor"),
            "amends_bill": (
                ((contextual or {}).get("parent_context") or {}).get("canonical_key")
            ),
            "fixed_downstream_weight": 0.5 if amendment else 1.0,
        },
        "official_evidence": {
            "title": {
                "status": title.get("status"),
                "text": worksheet.get("official_title"),
                "text_sha256": title.get("text_sha256"),
                "source_url": title.get("source_url"),
            },
            "official_summary": summary,
            "congress_gov_full_text": congress_text,
            "official_text_versions": versions,
            "stage_and_actions": stage,
            "relationship_roles": relationships,
        },
        "contextual_and_corroborative_evidence": contextual,
        "original_method": {
            "bundle_manifest_sha256": original_method.manifest_sha256,
            "category_method_registry_sha256": (
                original_method.method_registry_sha256
            ),
            "evidence_class_rules": {
                "official_dispositive": (
                    "may support inclusion or affirmative false-positive review"
                ),
                "official_context_only": (
                    "may support context or attributed intent; never domain alone"
                ),
                "corroborative_only": (
                    "may corroborate; only explicit America's Credit Unions "
                    "language may establish institutional direction"
                ),
                "role_authority_only": (
                    "assigns the named role only; parent and letter roles never inherit"
                ),
            },
            "preservation_track": original_method.preservation,
        },
        "category_slots": slots,
        "category_slot_count": len(slots),
        "memberships": membership_rows,
        "membership_count": len(membership_rows),
        "readiness_snapshot": readiness,
        "eligibility": {
            "include_evidence_complete": include_evidence_complete,
            "exclude_official_text_review_complete": bool(
                include_evidence_complete
                and (
                    amendment_text_available
                    if amendment
                    else versions["all_version_review_complete"]
                )
            ),
            "exclude_all_version_review_complete": bool(
                include_evidence_complete
                and not amendment
                and versions["all_version_review_complete"]
            ),
            "provider_dispatch_eligible": provider_dispatch_eligible,
            "evidence_text_bytes": text_bytes,
            "blockers": sorted(set(blockers)),
            "warnings": sorted(set(warnings)),
        },
        "policy": {
            "proposal_only": True,
            "automated_final_decision_permitted": False,
            "human_ratification_required": True,
            "candidate_deletion_allowed": False,
            "absence_is_neutral": True,
            "search_membership_is_not_decision_evidence": True,
            "main_corpus_write_authorized": False,
        },
        "candidate_evidence_sha256": worksheet.get("candidate_evidence_sha256"),
    }
    packet["evidence_sha256"] = sha256_bytes(
        utc_stamp_free_canonical_bytes(
            {
                "official_evidence": packet["official_evidence"],
                "contextual_and_corroborative_evidence": packet[
                    "contextual_and_corroborative_evidence"
                ],
                "original_method": packet["original_method"],
            }
        )
    )
    packet["packet_row_id"] = "pk-" + sha256_bytes(
        utc_stamp_free_canonical_bytes(
            {
                "canonical_key": key,
                "evidence_sha256": packet["evidence_sha256"],
                "category_slots": [row["category_id"] for row in slots],
                "memberships": [row["query_id"] for row in membership_rows],
                "tool_version": TOOL_VERSION,
            }
        )
    )[:32]
    return packet


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-generation", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--extraction-receipts", type=Path, required=True)
    parser.add_argument("--govinfo-run", type=Path, required=True)
    parser.add_argument("--billstatus-parsed", type=Path, required=True)
    parser.add_argument("--original-method-bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-text-bytes", type=int, default=600_000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if args.max_text_bytes < 4096:
        parser.error("--max-text-bytes must be at least 4096")
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    return args


def run(args: argparse.Namespace) -> dict[str, Any]:
    generation = args.queue_generation.resolve()
    queue_manifest = read_json(generation / "manifest.json")
    queue_id = str(queue_manifest.get("queue_id") or "")
    if not valid_sha(queue_id):
        raise PacketBuildError("queue manifest has no valid queue_id")

    evidence_path = args.evidence.resolve()
    evidence_receipt_path = evidence_path.parent / "receipt.json"
    evidence_receipt = read_json(evidence_receipt_path)
    bound = (queue_manifest.get("receipt") or {}).get("candidate_evidence_binding") or {}
    if str(bound.get("receipt_path") or "") != str(evidence_receipt_path):
        raise PacketBuildError(
            "queue generation is not bound to the supplied candidate evidence receipt"
        )
    if bound.get("verified") is not True:
        raise PacketBuildError("queue candidate-evidence binding is not verified")
    evidence_sha = sha256_file(evidence_path)
    declared = (
        (evidence_receipt.get("outputs") or {})
        .get("candidate_evidence.ndjson", {})
        .get("sha256")
    )
    if evidence_sha != declared:
        raise PacketBuildError("candidate evidence file hash differs from its receipt")

    categories = [
        row
        for row in (queue_manifest.get("receipt") or {}).get("category_registry") or []
    ]
    if len(categories) != EXPECTED_CATEGORY_COUNT:
        raise PacketBuildError("queue category registry is not exactly nineteen")
    original_method = OriginalMethodEvidence(
        args.original_method_bundle.resolve(),
        category_ids=[str(row.get("category_id") or "") for row in categories],
    )

    evidence_by_key = {
        str(row.get("canonical_key") or ""): row
        for row in iter_ndjson(evidence_path)
    }
    memberships_by_key: dict[str, list[dict[str, Any]]] = {}
    for row in iter_ndjson(generation / "queue.ndjson"):
        memberships_by_key.setdefault(str(row.get("canonical_key") or ""), []).append(row)

    extraction = load_extraction_receipts(args.extraction_receipts.resolve())
    govinfo = load_govinfo_versions(args.govinfo_run.resolve())
    billstatus_index = load_billstatus_index(args.billstatus_parsed.resolve())

    inputs = {
        "queue_generation": {
            "path": str(generation),
            "queue_id": queue_id,
            "manifest_sha256": sha256_file(generation / "manifest.json"),
            "queue_sha256": sha256_file(generation / "queue.ndjson"),
            "worksheets_sha256": sha256_file(generation / "candidate_worksheets.ndjson"),
        },
        "candidate_evidence": {
            "path": str(evidence_path),
            "sha256": evidence_sha,
            "receipt_path": str(evidence_receipt_path),
            "receipt_sha256": sha256_file(evidence_receipt_path),
        },
        "extraction_receipts": {
            "path": str(args.extraction_receipts.resolve()),
            "receipt_count": len(extraction),
        },
        "govinfo_run": {
            "path": str(args.govinfo_run.resolve()),
            "accepted_receipt_count": sum(len(rows) for rows in govinfo.values()),
            "candidates_with_accepted_receipts": len(govinfo),
        },
        "billstatus_parsed": {
            "path": str(args.billstatus_parsed.resolve()),
            "record_count": len(billstatus_index),
        },
        "original_method_bundle": original_method.receipt(),
    }
    parameters = {
        "max_text_bytes": args.max_text_bytes,
        "limit": args.limit or None,
        "expected_category_count": EXPECTED_CATEGORY_COUNT,
    }
    if args.plan_only:
        plan = {
            "schema": MANIFEST_SCHEMA,
            "status": "PLAN_ONLY",
            "inputs": inputs,
            "parameters": parameters,
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        }
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return plan

    counters: Counter[str] = Counter()
    version_counters: Counter[str] = Counter()
    emitted = 0
    packet_bytes = 0
    packet_digest = hashlib.sha256()
    output_root = args.out.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".packets-staging-{os.getpid()}.ndjson"
    try:
        with staging.open("xb") as staged:
            for worksheet in iter_ndjson(generation / "candidate_worksheets.ndjson"):
                key = str(worksheet.get("canonical_key") or "")
                evidence = evidence_by_key.get(key)
                if evidence is None:
                    raise PacketBuildError(f"worksheet {key} has no candidate evidence")
                packet = build_packet(
                    worksheet=worksheet,
                    evidence=evidence,
                    memberships=memberships_by_key.get(key, []),
                    categories=categories,
                    extraction=bind_extraction_receipt(evidence, extraction),
                    govinfo_receipts=govinfo.get(key, []),
                    billstatus=(
                        read_json(billstatus_index[key])
                        if key in billstatus_index
                        else None
                    ),
                    original_method=original_method,
                    max_text_bytes=args.max_text_bytes,
                )
                row_bytes = utc_stamp_free_canonical_bytes(packet)
                staged.write(row_bytes)
                packet_digest.update(row_bytes)
                packet_bytes += len(row_bytes)
                emitted += 1
                eligibility = packet["eligibility"]
                counters["packets"] += 1
                counters["memberships"] += packet["membership_count"]
                counters["category_slots"] += packet["category_slot_count"]
                if eligibility["include_evidence_complete"]:
                    counters["include_evidence_complete"] += 1
                if eligibility["provider_dispatch_eligible"]:
                    counters["provider_dispatch_eligible"] += 1
                    counters["dispatch_eligible_memberships"] += packet[
                        "membership_count"
                    ]
                else:
                    counters["auto_flagged_no_provider_call"] += 1
                    counters["auto_flagged_memberships"] += packet["membership_count"]
                if eligibility["exclude_all_version_review_complete"]:
                    counters["exclude_all_version_review_complete"] += 1
                for blocker in eligibility["blockers"]:
                    counters["blocker:" + blocker.split(":")[0]] += 1
                for warning in eligibility["warnings"]:
                    counters["warning:" + warning.split(":")[0]] += 1
                versions = packet["official_evidence"]["official_text_versions"]
                version_counters["declared_versions"] += versions[
                    "expected_version_count"
                ]
                version_counters["govinfo_accepted_receipts"] += versions[
                    "govinfo_accepted_receipt_count"
                ]
                version_counters["locally_parsed_versions"] += versions[
                    "locally_parsed_version_count"
                ]
                for item in versions["versions"]:
                    if item.get("unavailable_reason"):
                        version_counters[
                            "unavailable:" + str(item["unavailable_reason"])
                        ] += 1
                if args.limit and emitted >= args.limit:
                    break
            staged.flush()
            os.fsync(staged.fileno())
    except BaseException:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
        raise

    packets_sha = packet_digest.hexdigest()
    basis = {
        "inputs": inputs,
        "parameters": parameters,
        "packets_sha256": packets_sha,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }
    packet_generation_id = sha256_bytes(utc_stamp_free_canonical_bytes(basis))
    out_dir = output_root / "packets" / packet_generation_id
    commit_staged_file(staging, out_dir / "packets.ndjson", packets_sha)
    method_registry_bytes = utc_stamp_free_canonical_bytes(
        original_method.method_registry
    )
    method_registry_sha = exclusive_write(
        out_dir / "category_method_registry.json", method_registry_bytes
    )
    if method_registry_sha != original_method.method_registry_sha256:
        raise PacketBuildError("category method registry hash changed while writing")

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS_ADJUDICATION_PACKETS_BUILT",
        "packet_generation_id": packet_generation_id,
        "counts": dict(sorted(counters.items())),
        "version_join": dict(sorted(version_counters.items())),
        "inputs": inputs,
        "parameters": parameters,
        "evidence_rules": {
            "search_membership_alone": "never sufficient to include or exclude",
            "term_absence": "never negative evidence",
            "press_or_article_absence": "never admissible as exclusion evidence",
            "official_crs_summary": (
                "Congress.gov CRS or GovInfo BillStatus summary is admissible; "
                "an officially recorded absence is neutral"
            ),
            "all_official_text_versions": (
                "required for an absence-based exclude_false_positive; the single "
                "displayed Congress.gov page is never an all-version review"
            ),
            "amendment_text": (
                "recovered operative amendment text is official_dispositive; "
                "amendments remain ineligible for absence-based exclusion because "
                "GovInfo has no standalone all-version inventory"
            ),
            "floor_remarks": (
                "attributed sponsor intent context only; never domain evidence alone"
            ),
            "parent_context": (
                "context only; vehicle is not disqualifying and direction never inherits"
            ),
            "trusted_press": (
                "corroborative; explicit America's Credit Unions language may "
                "establish institutional direction, but press absence is neutral"
            ),
            "letters": (
                "preserved as a separate evidence track; leads/signatories never "
                "become bill sponsor or cosponsor roles"
            ),
        },
        "safety": {
            "network_request_performed": False,
            "adjudication_performed": False,
            "category_evidence_written": False,
            "disposition_assigned": False,
            "culling_performed": False,
            "junk_ledger_write_performed": False,
            "scoring_performed": False,
            "rank_write_performed": False,
            "integration_write_performed": False,
            "sync_or_promotion_performed": False,
            "main_corpus_write_performed": False,
            "production_write_performed": False,
        },
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "packet_generation_id": packet_generation_id,
        "generation_basis": basis,
        "receipt": receipt,
        "outputs": {
            "packets.ndjson": {
                "path": str(out_dir / "packets.ndjson"),
                "bytes": packet_bytes,
                "record_count": emitted,
                "sha256": packets_sha,
            },
            "category_method_registry.json": {
                "path": str(out_dir / "category_method_registry.json"),
                "bytes": len(method_registry_bytes),
                "record_count": EXPECTED_CATEGORY_COUNT,
                "sha256": method_registry_sha,
            },
        },
    }
    if sha256_file(evidence_path) != evidence_sha:
        raise PacketBuildError("candidate evidence changed during the run")
    exclusive_write(
        out_dir / "manifest.json", utc_stamp_free_canonical_bytes(manifest)
    )
    exclusive_write(
        out_dir / "receipt.json", utc_stamp_free_canonical_bytes(receipt)
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    try:
        run(parse_args(argv))
        return 0
    except (PacketBuildError, OriginalMethodEvidenceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
