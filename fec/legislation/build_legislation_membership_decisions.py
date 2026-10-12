#!/usr/bin/env python3
"""Project resolved review-ledger rows into membership decision records.

The membership review ledger carries one row per ``(canonical_key, query_id)``
edge, the overwhelming majority of which are still ``unresolved``.  This
program emits ``acu-legislation-membership-adjudication-v1`` records for the
rows that actually carry a disposition, preserving each row's real
adjudication state, decision source, reviewer, and evidence provenance.

It deliberately does not upgrade, infer, or complete a decision.  A row that
was produced by automated triage stays marked as automated, so the culling
engine's own finalizability gate — not this adapter — decides whether the
decision may leave the flagged queue.  Unresolved rows are never emitted.

Edges outside the discovery membership universe are dropped and counted
explicitly in the receipt; they are never silently truncated.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator, Mapping

try:
    from lane_file_io import open_ndjson
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import open_ndjson
    from fec.legislation_measure_key import CANONICAL_KEY_RE


MEMBERSHIP_SCHEMA = "acu-legislation-membership-adjudication-v1"
RECEIPT_SCHEMA = "acu-legislation-membership-decision-receipt-v1"
TOOL_NAME = "build_legislation_membership_decisions.py"
TOOL_VERSION = "1.0.0"

FINAL_DISPOSITIONS = {"include", "exclude_false_positive"}


class DecisionBuildError(RuntimeError):
    """Raised when decisions cannot be projected without ambiguity."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
                raise DecisionBuildError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from error
            if not isinstance(row, dict):
                raise DecisionBuildError(
                    f"{path.name}:{line_number}: not an object"
                )
            yield row


def project(row: Mapping[str, Any]) -> dict[str, Any]:
    """Carry a resolved ledger row across without completing it."""

    decision_source = str(row.get("decision_source") or "")
    # A decision produced by automated triage is reported as automated.  The
    # culling contract refuses to finalize such a row, which is the intended
    # behaviour: only human ratification or an explicitly reverified
    # pre-existing project decision may leave the flagged queue.
    automated = decision_source.startswith("automated")
    return {
        "schema": MEMBERSHIP_SCHEMA,
        "canonical_key": row.get("canonical_key"),
        "query_id": row.get("query_id"),
        "disposition": row.get("disposition"),
        "adjudication_state": row.get("adjudication_state"),
        "automated_final_decision": automated,
        "reviewer_confidence": row.get("reviewer_confidence"),
        "reason": row.get("reason"),
        "reason_code": row.get("reason_code"),
        "reviewer": row.get("reviewer"),
        "reviewed_at": row.get("reviewed_at"),
        "decision_evidence_sha256": row.get("official_decision_evidence_sha256"),
        "evidence_sources_checked": list(row.get("evidence_sources_checked") or []),
        "absence_used_as_exclusion_evidence": bool(
            row.get("article_absence_is_never_exclusion_evidence") is False
        ),
        "full_text_rule_out_complete": bool(
            row.get("official_text_evidence_state") == "complete"
        ),
        "decision_source": decision_source,
        "decision_provenance": row.get("decision_provenance"),
        "official_decision_source_url": row.get("official_decision_source_url"),
    }


def build(args: argparse.Namespace) -> Mapping[str, Any]:
    ledger_path = Path(args.ledger).resolve()
    memberships_path = Path(args.memberships).resolve()
    output_root = Path(args.out).resolve()

    universe: set[tuple[str, str]] = set()
    for row in read_ndjson(memberships_path):
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "").strip()
        if CANONICAL_KEY_RE.fullmatch(key) and query_id:
            universe.add((key, query_id))

    emitted: dict[tuple[str, str], dict[str, Any]] = {}
    counters = {
        "ledger_rows": 0,
        "unresolved_skipped": 0,
        "resolved_seen": 0,
        "emitted": 0,
        "outside_discovery_universe_dropped": 0,
        "duplicate_edges_dropped": 0,
        "include": 0,
        "exclude_false_positive": 0,
        "automated_decision_source": 0,
    }
    dropped: list[dict[str, Any]] = []

    for row in read_ndjson(ledger_path):
        counters["ledger_rows"] += 1
        disposition = str(row.get("disposition") or "")
        if disposition not in FINAL_DISPOSITIONS:
            counters["unresolved_skipped"] += 1
            continue
        counters["resolved_seen"] += 1
        key = str(row.get("canonical_key") or "")
        query_id = str(row.get("query_id") or "").strip()
        natural_key = (key, query_id)
        if natural_key not in universe:
            counters["outside_discovery_universe_dropped"] += 1
            dropped.append(
                {
                    "canonical_key": key,
                    "query_id": query_id,
                    "disposition": disposition,
                    "reason": "edge is not in the discovery membership universe",
                }
            )
            continue
        if natural_key in emitted:
            counters["duplicate_edges_dropped"] += 1
            dropped.append(
                {
                    "canonical_key": key,
                    "query_id": query_id,
                    "disposition": disposition,
                    "reason": "duplicate resolved edge in the review ledger",
                }
            )
            continue
        projected = project(row)
        emitted[natural_key] = projected
        counters["emitted"] += 1
        counters[disposition] += 1
        if projected["automated_final_decision"]:
            counters["automated_decision_source"] += 1

    rows = [
        canonical_json_bytes(emitted[key]) + b"\n" for key in sorted(emitted)
    ]
    body = b"".join(rows)
    decisions_path = output_root / "membership_decisions.ndjson"
    atomic_write(decisions_path, body)

    dropped_body = b"".join(
        canonical_json_bytes(row) + b"\n" for row in dropped
    )
    atomic_write(output_root / "dropped_edges.ndjson", dropped_body)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS_MEMBERSHIP_DECISIONS_PROJECTED",
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "inputs": {
            "membership_review_ledger": {
                "path": str(ledger_path),
                "sha256": sha256_file(ledger_path),
            },
            "discovery_memberships": {
                "path": str(memberships_path),
                "sha256": sha256_file(memberships_path),
                "edge_count": len(universe),
            },
        },
        "outputs": {
            "membership_decisions.ndjson": {
                "path": str(decisions_path),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "record_count": len(rows),
            },
            "dropped_edges.ndjson": {
                "path": str(output_root / "dropped_edges.ndjson"),
                "bytes": len(dropped_body),
                "sha256": sha256_bytes(dropped_body),
                "record_count": len(dropped),
            },
        },
        "counts": counters,
        "safety": {
            "decisions_upgraded_or_completed": False,
            "unresolved_rows_emitted": False,
            "culling_performed": False,
            "main_corpus_write_performed": False,
        },
    }
    atomic_write(
        output_root / "receipt.json", canonical_json_bytes(receipt) + b"\n"
    )
    return receipt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--memberships", required=True)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        receipt = build(parse_args(argv))
    except (DecisionBuildError, OSError, ValueError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
