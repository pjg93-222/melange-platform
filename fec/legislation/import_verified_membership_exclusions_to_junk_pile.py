#!/usr/bin/env python3
"""Import provenance-complete membership exclusions into the reversible junk pile.

This is deliberately narrow.  It imports only already-adjudicated
``exclude_false_positive`` membership decisions with complete, hash-verified
Congress.gov evidence.  A bill can have other unresolved category memberships,
so the junk-pile identifier is ``<canonical_key>::<query_id>`` and never implies
that the entire discovery record has been deleted.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from legislation import manage_legislation_junk_pile as junk


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
    / "candidate_membership_review"
    / "false_positive_exclusions.ndjson"
)
DEFAULT_JUNK_ROOT = ROOT / "audits" / "legfix5" / "legislation_junk_pile"
DEFAULT_RUN_ROOT = (
    ROOT / "audits" / "legfix5" / "legislation_culling_20260726"
)
DISCOVERY_RECORDS = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
    / "congress_advanced_records.ndjson"
)
DISCOVERY_MEMBERSHIPS = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_pages_20260725"
    / "congress_advanced_memberships.ndjson"
)
RUN_ID = "legislation-culling-20260726"
SOURCE_NAME = "legislation-culling"
TOTAL_DISCOVERY_CANDIDATES = 18_557
REQUIRED_FIELDS = (
    "canonical_key",
    "query_id",
    "title",
    "reason",
    "reason_code",
    "evidence_basis",
    "official_decision_source_url",
    "official_decision_text_version",
    "official_decision_section",
    "official_decision_evidence_excerpt",
    "official_decision_evidence_sha256",
    "official_decision_retrieved_at",
    "reviewer",
    "reviewed_at",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_event(events_root: Path, sequence: int, event: dict[str, Any]) -> Path:
    events_root.mkdir(parents=True, exist_ok=True)
    event_path = events_root / f"{sequence:04d}-{event['kind']}.json"
    if event_path.exists():
        existing = json.loads(event_path.read_text(encoding="utf-8"))
        if canonical_sha256(existing) != canonical_sha256(event):
            raise junk.JunkPileError(
                f"refusing to overwrite different culling event: {event_path}"
            )
        return event_path
    with event_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(event, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event_path


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            row["_source_line_number"] = line_number
            rows.append(row)
    return rows


def validate_row(row: dict[str, Any], evidence_index: dict[str, dict[str, Any]]) -> None:
    missing = [
        field
        for field in REQUIRED_FIELDS
        if not isinstance(row.get(field), str) or not row[field].strip()
    ]
    if missing:
        raise junk.JunkPileError(
            f"{row.get('canonical_key', '<unknown>')}: missing fields {missing}"
        )
    if row.get("disposition") != "exclude_false_positive":
        raise junk.JunkPileError("source row is not an explicit false-positive exclusion")
    if row.get("decision_type") != "membership_category_relevance":
        raise junk.JunkPileError("source row is not membership-scoped")
    if row.get("candidate_retained_in_discovery_corpus") is not True:
        raise junk.JunkPileError("source row does not preserve the discovery candidate")
    if row.get("official_decision_text_unavailable") is not False:
        raise junk.JunkPileError("official decision text is unavailable")
    if not row["official_decision_source_url"].startswith(
        "https://www.congress.gov/"
    ):
        raise junk.JunkPileError("official evidence is not a Congress.gov URL")
    excerpt_hash = hashlib.sha256(
        row["official_decision_evidence_excerpt"].encode("utf-8")
    ).hexdigest()
    if excerpt_hash != row["official_decision_evidence_sha256"]:
        raise junk.JunkPileError("official evidence excerpt hash drift")

    # The checks above verify transcription only. The excerpt hash is computed
    # from the excerpt itself, so it proves the text was copied faithfully and
    # binds nothing to Congress.gov. None of the architecture's junk
    # preconditions -- high reviewer confidence, human ratification, complete
    # full-text rule-out, all text versions reviewed for absence-based
    # exclusions -- were enforced anywhere on this path, and this is the only
    # code that writes the junk pile. A low-confidence, unratified exclusion
    # could reach it with a PASS receipt.
    #
    # The contract already encodes those preconditions. Defer to it rather than
    # restating them, so the two can never drift apart.
    _require_finalizable_under_contract(row, evidence_index)


def _load_contract():
    """Import the culling contract, or refuse to proceed."""
    try:
        from fec.legislation import legislation_culling_integration_contract as contract
        return contract
    except ImportError:
        pass
    try:
        from legislation import legislation_culling_integration_contract as contract  # type: ignore
        return contract
    except ImportError as exc:
        raise junk.JunkPileError(
            "cannot import legislation_culling_integration_contract; refusing to "
            "write the junk pile without contract enforcement"
        ) from exc


def load_evidence_index(evidence_path: Path) -> dict[str, dict[str, Any]]:
    """Index candidate evidence envelopes by canonical_key.

    Required. The contract decides finalizability from the membership row AND
    its evidence envelope, so without this the check cannot be performed at all
    -- which is precisely how unreviewed exclusions reached the junk pile.
    """
    evidence_path = evidence_path.resolve()
    if not evidence_path.is_file():
        raise junk.JunkPileError(
            f"candidate evidence not found at {evidence_path}; refusing to write the "
            "junk pile without the evidence the contract needs"
        )
    index: dict[str, dict[str, Any]] = {}
    with evidence_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError as exc:
                # Unreadable evidence must not be mistaken for absent evidence.
                raise junk.JunkPileError(
                    f"unreadable candidate evidence in {evidence_path}: {exc}"
                ) from exc
            key = str(envelope.get("canonical_key") or "").strip()
            if key:
                index[key] = envelope
    if not index:
        raise junk.JunkPileError(f"candidate evidence at {evidence_path} is empty")
    return index


def _require_finalizable_under_contract(
    row: dict[str, Any],
    evidence_index: dict[str, dict[str, Any]],
) -> None:
    """Refuse any row the culling contract would not finalize.

    Fails closed on every uncertainty: missing contract, missing evidence
    envelope, contract error, or a False verdict.
    """
    contract = _load_contract()
    checker = getattr(contract, "membership_is_finalizable", None)
    if not callable(checker):
        raise junk.JunkPileError(
            "contract exposes no membership_is_finalizable; refusing to write the "
            "junk pile without contract enforcement"
        )

    key = str(row.get("canonical_key") or "").strip()
    envelope = evidence_index.get(key)
    if envelope is None:
        raise junk.JunkPileError(
            f"{key}: no candidate evidence envelope. Missing evidence is flagged, "
            "never junked."
        )

    try:
        finalizable = checker(row, envelope)
    except Exception as exc:
        raise junk.JunkPileError(
            f"{key}: contract rejected this row: {exc}"
        ) from exc

    if not finalizable:
        raise junk.JunkPileError(
            f"{key}::{row.get('query_id', '<unknown>')}: membership_is_finalizable() "
            "is False. An exclusion the culling contract will not finalize must never "
            "enter the junk pile. Check adjudication_state (human_ratified or "
            "reverified_preexisting_project_decision), automated_final_decision, "
            "reviewer_confidence (high/medium), full_text_rule_out_complete and "
            "absence_used_as_exclusion_evidence."
        )


def evidence_for(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "authority": "Congress.gov",
            "url": row["official_decision_source_url"],
            "text_version": row["official_decision_text_version"],
            "section": row["official_decision_section"],
            "excerpt": row["official_decision_evidence_excerpt"],
            "excerpt_sha256": row["official_decision_evidence_sha256"],
            "retrieved_at": row["official_decision_retrieved_at"],
            "basis": row["evidence_basis"],
        }
    ]


def progress_payload(
    *,
    processed: int,
    junk_count: int,
    receipt: Path | None,
    discovery_hashes: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "timestamp_utc": utc_now(),
        "run_id": RUN_ID,
        "lane": "codex",
        "source": SOURCE_NAME,
        "kind": "culling.progress",
        "status": "running",
        "stage": "provisional-reversible-culling",
        "summary": f"{processed}/{TOTAL_DISCOVERY_CANDIDATES} "
        f"({processed / TOTAL_DISCOVERY_CANDIDATES:.2%})",
        "detail": (
            "Only provenance-complete membership false positives are moving "
            "to the reversible junk pile. Stage, missing-cosponsor, amendment, "
            "and unresolved-role evidence remains fail-closed."
        ),
        "current": processed,
        "total": TOTAL_DISCOVERY_CANDIDATES,
        "retained": 0,
        "junk": junk_count,
        "manual_review": TOTAL_DISCOVERY_CANDIDATES - processed,
        "discovery_records_sha256": discovery_hashes["records"],
        "discovery_memberships_sha256": discovery_hashes["memberships"],
        "candidate_deletion_allowed": False,
        "junk_pile_reversible": True,
        "historical_probe_gate_complete": False,
        "historical_probes_remaining": 15,
        "latest_audit_receipt": str(receipt.resolve()) if receipt else None,
    }


def import_rows(
    source: Path,
    junk_root: Path,
    run_root: Path,
    evidence: Path,
) -> dict[str, Any]:
    source = source.resolve()
    junk_root = junk_root.resolve()
    run_root = run_root.resolve()
    progress_path = run_root / "progress.json"
    events_root = run_root / "events"

    source_hash_before = sha256_file(source)
    discovery_hashes_before = {
        "records": sha256_file(DISCOVERY_RECORDS),
        "memberships": sha256_file(DISCOVERY_MEMBERSHIPS),
    }
    # Loaded before any row is examined, so a missing or unreadable evidence file
    # stops the import outright instead of letting rows through unchecked.
    evidence_index = load_evidence_index(evidence)
    rows = read_rows(source)
    if not rows:
        raise junk.JunkPileError("no verified exclusions were supplied")

    state = junk.compute_effective_state(junk.initialize(junk_root))
    existing_by_id = {
        row["candidate_id"]: row for row in state["exclusions"]
    }
    processed = len(state["exclusions"])

    for source_index, row in enumerate(rows, 1):
        validate_row(row, evidence_index)
        source_line = row.pop("_source_line_number")
        membership_id = f"{row['canonical_key']}::{row['query_id']}"
        if not membership_id.startswith(f"{row['canonical_key']}::"):
            raise junk.JunkPileError("membership identifier/canonical key mismatch")

        decision = existing_by_id.get(membership_id)
        if decision is None:
            stable_digest = hashlib.sha256(membership_id.encode("utf-8")).hexdigest()
            decision = junk.append_exclusion(
                junk_root,
                candidate_id=membership_id,
                rule_id="official-membership-false-positive",
                rule_version="1.0.0",
                reason=row["reason"],
                evidence=evidence_for(row),
                provenance=[
                    {
                        "source_path": str(source),
                        "source_sha256": source_hash_before,
                        "source_line_number": source_line,
                        "original_reviewer": row["reviewer"],
                        "original_reviewed_at": row["reviewed_at"],
                        "original_decision_provenance": (
                            row.get("decision_provenance") or []
                        ),
                        "scope": "membership_only",
                        "bill_discovery_record_retained": True,
                    }
                ],
                decided_by="Codex import of hash-verified prior adjudication",
                candidate_payload=row,
                source_path=source,
                source_locator={
                    "kind": "ndjson_record",
                    "line_number": source_line,
                    "canonical_key": row["canonical_key"],
                    "query_id": row["query_id"],
                },
                decision_id=f"exclude-membership-{stable_digest[:20]}",
            )
            existing_by_id[membership_id] = decision

        receipt = junk.export_current(junk_root)
        state = junk.compute_effective_state(junk_root)
        processed = len(state["exclusions"])
        payload = progress_payload(
            processed=processed,
            junk_count=len(state["current"]),
            receipt=receipt,
            discovery_hashes=discovery_hashes_before,
        )
        write_json_atomic(progress_path, payload)
        event = {
            "schema_version": 1,
            "timestamp_utc": utc_now(),
            "run_id": RUN_ID,
            "lane": "codex",
            "source": SOURCE_NAME,
            "kind": "membership.excluded",
            "status": "completed",
            "stage": "provisional-reversible-culling",
            "summary": f"{processed}/{TOTAL_DISCOVERY_CANDIDATES} "
            f"- {membership_id} moved to reversible junk",
            "detail": row["reason"],
            "command": (
                "import_verified_membership_exclusions_to_junk_pile.py "
                "--scope membership-only"
            ),
            "input": {
                "candidate_id": row["canonical_key"],
                "title": row["title"],
                "query_id": row["query_id"],
                "rule": row["reason_code"],
                "official_url": row["official_decision_source_url"],
                "official_excerpt": row[
                    "official_decision_evidence_excerpt"
                ],
            },
            "output": {
                "decision_id": decision["decision_id"],
                "destination": str(junk_root),
                "candidate_record_deleted": False,
                "membership_scope_only": True,
                "junk_count": len(state["current"]),
            },
            "artifacts": [
                str(receipt.resolve()),
                str((junk_root / "exclusion_decisions.ndjson").resolve()),
            ],
            "current": processed,
            "total": TOTAL_DISCOVERY_CANDIDATES,
        }
        write_event(events_root, source_index, event)

    source_hash_after = sha256_file(source)
    discovery_hashes_after = {
        "records": sha256_file(DISCOVERY_RECORDS),
        "memberships": sha256_file(DISCOVERY_MEMBERSHIPS),
    }
    if source_hash_after != source_hash_before:
        raise junk.JunkPileError("source exclusion ledger changed during import")
    if discovery_hashes_after != discovery_hashes_before:
        raise junk.JunkPileError("discovery corpus changed during culling import")

    final_receipt = junk.export_current(junk_root)
    final_state = junk.compute_effective_state(junk_root)
    return {
        "status": "PASS",
        "source_rows": len(rows),
        "exclusion_decisions": len(final_state["exclusions"]),
        "current_junk_pile": len(final_state["current"]),
        "restored": len(final_state["restored"]),
        "membership_scope_only": True,
        "discovery_corpus_modified": False,
        "discovery_hashes": discovery_hashes_after,
        "source_sha256": source_hash_after,
        "audit_receipt": str(final_receipt.resolve()),
        "progress": str(progress_path.resolve()),
        "events": str(events_root.resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--junk-root", type=Path, default=DEFAULT_JUNK_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    # Required, with no default. The junk pile must never be written without the
    # evidence the culling contract needs to judge finalizability, and a default
    # would let a stale or superseded generation be picked up silently.
    parser.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help=(
            "candidate_evidence.ndjson for the generation these exclusions were "
            "adjudicated against"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = import_rows(
            args.source, args.junk_root, args.run_root, args.evidence
        )
    except (OSError, json.JSONDecodeError, junk.JunkPileError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
