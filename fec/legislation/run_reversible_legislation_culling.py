#!/usr/bin/env python3
"""Build a deterministic, reversible legislation culling proposal.

This program is intentionally independent of Codex and any model runtime.  It
reads immutable discovery/evidence inputs, classifies every membership at the
natural key ``(canonical_key, query_id)``, and writes only to a dedicated audit
root.  It never edits discovery records, the main corpus, scoring inputs, or
promotion artifacts.

Candidate-wide junk is a derived, reversible state.  A candidate is eligible
only when every membership is a finalizable explicit exclusion and no keep,
flagged, legacy, or restored edge exists.  Missing or incomplete evidence is
always flagged and can never produce junk.

The legacy-edge input is explicit.  A run either supplies ``--legacy-edges`` or
declares ``--no-legacy-edges``; an omitted flag is never silently read as "no
legacy edges exist", and the declaration is recorded in the receipt so the
legacy veto gate can never claim to have held against a source that was never
supplied.

The derived junk ledger is the authoritative "globally junked" artifact, so it
is never allowed to disagree with the published partition.  When an active
exclusion no longer matches the current memberships the engine appends a
superseding restoration record: the ledgers stay append-only, nothing is
mutated or deleted, and the withdrawn exclusion stops being effective state.

Every receipt gate is derived from the run.  ``True`` means this invocation
computed a falsifiable check over its own rows and the check held, a string
names the reason no measurement was possible, and ``False`` is used only for
the negative assertion that integration was not authorized.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable, Mapping

try:
    from lane_file_io import iter_ndjson_raw_lines
    from legislation_measure_key import CANONICAL_KEY_RE
    from legislation.legislation_culling_integration_contract import (
        CullingContractError,
        membership_is_finalizable,
        validate_candidate_evidence,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import iter_ndjson_raw_lines
    from fec.legislation_measure_key import CANONICAL_KEY_RE
    from fec.legislation.legislation_culling_integration_contract import (
        CullingContractError,
        membership_is_finalizable,
        validate_candidate_evidence,
    )


TOOL_NAME = "run_reversible_legislation_culling.py"
TOOL_VERSION = "1.1.0"
MEMBERSHIP_SCHEMA = "acu-legislation-reversible-membership-disposition-v1"
CANDIDATE_SCHEMA = "acu-legislation-reversible-candidate-partition-v1"
JUNK_SCHEMA = "acu-legislation-derived-junk-exclusion-v1"
RESTORATION_SCHEMA = "acu-legislation-derived-junk-restoration-v1"
RESTORATION_RECEIPT_SCHEMA = "acu-legislation-restoration-receipt-v1"
GENERATION_SCHEMA = "acu-legislation-reversible-culling-generation-v2"
RECEIPT_SCHEMA = "acu-legislation-reversible-culling-receipt-v2"
ZERO_SHA256 = "0" * 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

JUNK_LEDGER = "junk_exclusions.ndjson"
RESTORATION_LEDGER = "restorations.ndjson"

DISPOSITIONS = ("keep", "junk", "flagged")

# The legacy-edge source is declared, never inferred from an absent argument.
LEGACY_EDGE_SOURCE_FILE = "file"
LEGACY_EDGE_SOURCE_DECLARED_ABSENT = "declared_absent"

# Non-boolean gate values.  A gate is a boolean only when this invocation
# measured it; otherwise it states why it could not be measured.
GATE_ENFORCED_BY_PRECONDITION = "enforced_by_precondition"
GATE_LEGACY_SOURCE_DECLARED_ABSENT = "legacy_edge_source_declared_absent"
GATE_NO_DECLARED_INPUT_COUNTS = "no_declared_input_counts_supplied"

# Flag reasons that mean the official evidence for a membership is missing or
# incomplete.  Any membership carrying one of these must be flagged and can
# never contribute to global junk.
EVIDENCE_INCOMPLETE_FLAG_REASONS = frozenset(
    {
        "missing_candidate_evidence",
        "invalid_or_incomplete_candidate_evidence",
        "missing_membership_decision",
        "membership_universe_mismatch",
        "decision_not_finalizable_against_official_evidence",
    }
)


class ReversibleCullingError(ValueError):
    """Raised when the culling proposal cannot be proven safe."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def value_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def validate_timestamp(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReversibleCullingError(
            f"{field} must be an ISO-8601 UTC timestamp ending in Z"
        )
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReversibleCullingError(f"{field} is invalid") from exc
    return value


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReversibleCullingError(f"{field} must be a non-empty string")
    return value.strip()


def require_canonical_key(value: Any, field: str = "canonical_key") -> str:
    key = require_text(value, field)
    if not CANONICAL_KEY_RE.fullmatch(key):
        raise ReversibleCullingError(f"{field} is invalid: {key!r}")
    return key


def require_query_id(value: Any, field: str = "query_id") -> str:
    query_id = require_text(value, field)
    if any(character.isspace() for character in query_id):
        raise ReversibleCullingError(f"{field} may not contain whitespace")
    return query_id


def membership_key(canonical_key: str, query_id: str) -> str:
    return f"{canonical_key}|{query_id}"


def require_legacy_edge_declaration(
    legacy_edges_path: Path | None,
    no_legacy_edges: bool,
) -> str:
    """Force the legacy-edge source to be a declaration, not an omission.

    An absent argument used to be indistinguishable from "no legacy edges
    exist", which let the receipt assert that the legacy veto held when no
    legacy source had ever been read.
    """
    if legacy_edges_path is not None and no_legacy_edges:
        raise ReversibleCullingError(
            "legacy edge source is ambiguous: a legacy edge file and "
            "no_legacy_edges were both supplied"
        )
    if legacy_edges_path is None and not no_legacy_edges:
        raise ReversibleCullingError(
            "a legacy edge source must be declared: pass --legacy-edges with the "
            "preserved edge file, or --no-legacy-edges to record that no legacy "
            "edge source exists for this run"
        )
    return (
        LEGACY_EDGE_SOURCE_DECLARED_ABSENT
        if legacy_edges_path is None
        else LEGACY_EDGE_SOURCE_FILE
    )


def require_declared_input_counts(
    candidate_count: int | None,
    membership_count: int | None,
) -> dict[str, int] | None:
    """Normalize the operator's declared corpus size, if one was declared."""
    if (candidate_count is None) != (membership_count is None):
        raise ReversibleCullingError(
            "declared candidate and membership counts must be supplied together"
        )
    if candidate_count is None or membership_count is None:
        return None
    for field, value in (
        ("declared_candidate_count", candidate_count),
        ("declared_membership_count", membership_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReversibleCullingError(
                f"{field} must be a non-negative integer"
            )
    return {
        "candidate_count": candidate_count,
        "membership_count": membership_count,
    }


def file_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ReversibleCullingError(f"required input is missing: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def optional_file_receipt(path: Path | None) -> dict[str, Any] | None:
    return file_receipt(path) if path is not None else None


def read_ndjson(
    path: Path,
    *,
    label: str,
) -> list[tuple[dict[str, Any], str]]:
    rows: list[tuple[dict[str, Any], str]] = []
    # ``iter_ndjson_raw_lines`` strips a leading UTF-8 BOM from line 1 *before*
    # the row is hashed, so a PowerShell-written input parses and its per-row
    # digest is the hash of the JSON payload rather than of the encoding
    # marker.  A BOM-free file is byte-identical to before, so no existing
    # digest moves.
    for line_number, raw_with_newline in iter_ndjson_raw_lines(path):
        if not raw_with_newline.strip():
            continue
        raw = raw_with_newline.rstrip(b"\r\n")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReversibleCullingError(
                f"{label}:{line_number}: invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ReversibleCullingError(
                f"{label}:{line_number}: row must be an object"
            )
        rows.append((value, sha256_bytes(raw)))
    return rows


def _record_content_sha256(row: Mapping[str, Any]) -> str:
    core = dict(row)
    core.pop("record_content_sha256", None)
    return value_sha256(core)


def _seal_record(row: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(row)
    sealed["record_content_sha256"] = _record_content_sha256(sealed)
    return sealed


def _write_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise ReversibleCullingError(
            f"refusing to overwrite immutable artifact: {path}"
        ) from exc


def _atomic_initialize_empty(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.close(descriptor)
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


class ExclusiveLock:
    def __init__(self, root: Path):
        self.path = root / ".reversible-culling.lock"
        self.descriptor: int | None = None

    def __enter__(self) -> "ExclusiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError as exc:
            raise ReversibleCullingError(
                f"another culling writer owns {self.path}; inspect it first"
            ) from exc
        os.write(self.descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(self.descriptor)
        return self

    def __exit__(self, *_: Any) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


def initialize_ledgers(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    junk = root / JUNK_LEDGER
    restorations = root / RESTORATION_LEDGER
    if junk.exists() != restorations.exists():
        raise ReversibleCullingError(
            "partial ledger initialization detected; refusing to repair silently"
        )
    if not junk.exists():
        _atomic_initialize_empty(junk)
        _atomic_initialize_empty(restorations)


def _validate_ledger(
    path: Path,
    expected_schema: str,
) -> tuple[list[dict[str, Any]], list[bytes]]:
    rows: list[dict[str, Any]] = []
    raw_lines: list[bytes] = []
    previous = ZERO_SHA256
    with path.open("rb") as stream:
        for line_number, raw_with_newline in enumerate(stream, 1):
            if not raw_with_newline.endswith(b"\n"):
                raise ReversibleCullingError(
                    f"{path.name}:{line_number}: unterminated ledger record"
                )
            raw = raw_with_newline[:-1]
            if raw.endswith(b"\r"):
                raise ReversibleCullingError(
                    f"{path.name}:{line_number}: noncanonical CRLF"
                )
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ReversibleCullingError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from exc
            if (
                not isinstance(row, dict)
                or canonical_json_bytes(row) != raw
                or row.get("schema") != expected_schema
            ):
                raise ReversibleCullingError(
                    f"{path.name}:{line_number}: schema or canonical JSON drift"
                )
            if row.get("previous_record_sha256") != previous:
                raise ReversibleCullingError(
                    f"{path.name}:{line_number}: hash chain is broken"
                )
            if row.get("record_content_sha256") != _record_content_sha256(row):
                raise ReversibleCullingError(
                    f"{path.name}:{line_number}: content hash drift"
                )
            previous = sha256_bytes(raw)
            rows.append(row)
            raw_lines.append(raw_with_newline)
    return rows, raw_lines


def _append_ledger_record(
    path: Path,
    rows: list[dict[str, Any]],
    raw_lines: list[bytes],
    core: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    previous = (
        sha256_bytes(raw_lines[-1][:-1]) if raw_lines else ZERO_SHA256
    )
    record = _seal_record({**core, "previous_record_sha256": previous})
    raw = canonical_json_bytes(record)
    with path.open("ab") as stream:
        stream.write(raw + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    rows.append(record)
    raw_lines.append(raw + b"\n")
    return record, sha256_bytes(raw)


def _ledger_receipt(path: Path, raw_lines: list[bytes]) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "record_count": len(raw_lines),
        "tail_record_sha256": (
            sha256_bytes(raw_lines[-1][:-1]) if raw_lines else ZERO_SHA256
        ),
    }


def _load_ledger_state(root: Path) -> dict[str, Any]:
    exclusions, exclusion_lines = _validate_ledger(
        root / JUNK_LEDGER,
        JUNK_SCHEMA,
    )
    restorations, restoration_lines = _validate_ledger(
        root / RESTORATION_LEDGER,
        RESTORATION_SCHEMA,
    )
    exclusion_by_candidate: dict[str, tuple[dict[str, Any], str]] = {}
    exclusion_by_id: dict[str, tuple[dict[str, Any], str]] = {}
    for row, raw_line in zip(exclusions, exclusion_lines):
        key = require_canonical_key(row.get("canonical_key"))
        exclusion_id = require_text(row.get("exclusion_id"), "exclusion_id")
        line_sha = sha256_bytes(raw_line[:-1])
        if key in exclusion_by_candidate:
            raise ReversibleCullingError(
                f"multiple derived junk exclusions for {key}"
            )
        if exclusion_id in exclusion_by_id:
            raise ReversibleCullingError(
                f"duplicate exclusion_id: {exclusion_id}"
            )
        membership_keys = row.get("membership_keys")
        if (
            not isinstance(membership_keys, list)
            or not membership_keys
            or membership_keys != sorted(set(membership_keys))
        ):
            raise ReversibleCullingError(
                f"{key}: exclusion membership_keys are not a unique sorted list"
            )
        exclusion_by_candidate[key] = (row, line_sha)
        exclusion_by_id[exclusion_id] = (row, line_sha)

    restored_candidates: set[str] = set()
    restored_targets: set[str] = set()
    for row in restorations:
        key = require_canonical_key(row.get("canonical_key"))
        target_id = require_text(row.get("target_exclusion_id"), "target_exclusion_id")
        target = exclusion_by_id.get(target_id)
        if target is None:
            raise ReversibleCullingError(
                f"restoration targets unknown exclusion: {target_id}"
            )
        target_row, target_sha = target
        if target_row["canonical_key"] != key:
            raise ReversibleCullingError(
                f"restoration candidate/target mismatch for {key}"
            )
        if row.get("target_exclusion_record_sha256") != target_sha:
            raise ReversibleCullingError(
                f"restoration target hash drift for {key}"
            )
        if target_id in restored_targets or key in restored_candidates:
            raise ReversibleCullingError(
                f"duplicate restoration edge for {key}"
            )
        restored_targets.add(target_id)
        restored_candidates.add(key)

    active_exclusions = {
        key: value
        for key, value in exclusion_by_candidate.items()
        if value[0]["exclusion_id"] not in restored_targets
    }
    return {
        "exclusions": exclusions,
        "exclusion_lines": exclusion_lines,
        "restorations": restorations,
        "restoration_lines": restoration_lines,
        "exclusion_by_candidate": exclusion_by_candidate,
        "exclusion_by_id": exclusion_by_id,
        "restored_candidates": restored_candidates,
        "active_exclusions": active_exclusions,
    }


def _index_evidence(
    rows: Iterable[tuple[dict[str, Any], str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    values: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for row, line_sha in rows:
        key = require_canonical_key(row.get("canonical_key"))
        if key in values:
            raise ReversibleCullingError(f"duplicate candidate evidence: {key}")
        values[key] = row
        hashes[key] = line_sha
    return values, hashes


def _index_memberships(
    rows: Iterable[tuple[dict[str, Any], str]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], str]]:
    values: dict[tuple[str, str], dict[str, Any]] = {}
    hashes: dict[tuple[str, str], str] = {}
    for row, line_sha in rows:
        natural_key = (
            require_canonical_key(row.get("canonical_key")),
            require_query_id(row.get("query_id")),
        )
        if natural_key in values:
            raise ReversibleCullingError(
                f"duplicate discovery membership: {natural_key}"
            )
        values[natural_key] = row
        hashes[natural_key] = line_sha
    if not values:
        raise ReversibleCullingError("membership universe is empty")
    return values, hashes


def _index_decisions(
    rows: Iterable[tuple[dict[str, Any], str]],
    membership_keys: set[tuple[str, str]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], str]]:
    values: dict[tuple[str, str], dict[str, Any]] = {}
    hashes: dict[tuple[str, str], str] = {}
    for row, line_sha in rows:
        natural_key = (
            require_canonical_key(row.get("canonical_key")),
            require_query_id(row.get("query_id")),
        )
        if natural_key not in membership_keys:
            raise ReversibleCullingError(
                f"decision is outside the discovery universe: {natural_key}"
            )
        if natural_key in values:
            raise ReversibleCullingError(
                f"duplicate membership decision: {natural_key}"
            )
        values[natural_key] = row
        hashes[natural_key] = line_sha
    return values, hashes


def _load_legacy_keys(
    path: Path | None,
    candidate_keys: set[str],
) -> tuple[set[str], dict[str, list[str]]]:
    # ``path is None`` only ever reaches here after
    # ``require_legacy_edge_declaration`` recorded an explicit declared absence.
    if path is None:
        return set(), {}
    result: set[str] = set()
    hashes: dict[str, list[str]] = {}
    for row, line_sha in read_ndjson(path, label="legacy_edges"):
        key = require_canonical_key(
            row.get("canonical_key") or row.get("candidate_id")
        )
        if key not in candidate_keys:
            raise ReversibleCullingError(
                f"legacy edge is outside the discovery universe: {key}"
            )
        result.add(key)
        hashes.setdefault(key, []).append(line_sha)
    for values in hashes.values():
        values.sort()
    return result, hashes


def _classify_memberships(
    evidence: Mapping[str, dict[str, Any]],
    evidence_hashes: Mapping[str, str],
    memberships: Mapping[tuple[str, str], dict[str, Any]],
    membership_hashes: Mapping[tuple[str, str], str],
    decisions: Mapping[tuple[str, str], dict[str, Any]],
    decision_hashes: Mapping[tuple[str, str], str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    evidence_validation: dict[str, tuple[bool, str | None]] = {}
    for key, candidate_evidence in evidence.items():
        try:
            validate_candidate_evidence(candidate_evidence)
            evidence_validation[key] = (True, None)
        except (
            CullingContractError,
            TypeError,
            ValueError,
            LookupError,
            AttributeError,
        ) as exc:
            evidence_validation[key] = (False, str(exc))

    # The discovered membership universe is the authority.  Candidate evidence
    # that claims a different set of query/category memberships cannot be used
    # to finalize any decision for that candidate, because the reviewer would
    # have been shown a membership set that does not match discovery.
    discovered_universe: dict[str, set[str]] = {}
    for universe_key, universe_query_id in memberships:
        discovered_universe.setdefault(universe_key, set()).add(
            universe_query_id
        )

    universe_mismatch: dict[str, bool] = {}
    for key, candidate_evidence in evidence.items():
        declared = candidate_evidence.get("membership_query_ids")
        if not isinstance(declared, list) or not all(
            isinstance(entry, str) for entry in declared
        ):
            universe_mismatch[key] = True
            continue
        universe_mismatch[key] = (
            len(set(declared)) != len(declared)
            or set(declared) != discovered_universe.get(key, set())
        )

    for natural_key in sorted(memberships):
        key, query_id = natural_key
        candidate_evidence = evidence.get(key)
        decision = decisions.get(natural_key)
        reasons: list[str] = []
        if candidate_evidence is None:
            reasons.append("missing_candidate_evidence")
        elif not evidence_validation[key][0]:
            reasons.append("invalid_or_incomplete_candidate_evidence")
        if decision is None:
            reasons.append("missing_membership_decision")
        mismatched = bool(
            candidate_evidence is not None and universe_mismatch.get(key, True)
        )
        if mismatched:
            reasons.append("membership_universe_mismatch")

        finalizable = bool(
            candidate_evidence is not None
            and decision is not None
            and not mismatched
            and evidence_validation[key][0]
            and membership_is_finalizable(decision, candidate_evidence)
        )
        if decision is not None and not finalizable:
            reasons.append("decision_not_finalizable_against_official_evidence")

        if finalizable and decision.get("disposition") == "include":
            disposition = "keep"
        elif (
            finalizable
            and decision.get("disposition") == "exclude_false_positive"
        ):
            disposition = "junk"
        else:
            disposition = "flagged"
        if disposition == "flagged" and not reasons:
            reasons.append("unresolved_membership")

        output.append(
            {
                "schema": MEMBERSHIP_SCHEMA,
                "canonical_key": key,
                "query_id": query_id,
                "disposition": disposition,
                "flag_reasons": sorted(set(reasons)),
                "source_membership_sha256": membership_hashes[natural_key],
                "candidate_evidence_sha256": evidence_hashes.get(key),
                "decision_sha256": decision_hashes.get(natural_key),
                "decision_disposition": (
                    decision.get("disposition") if decision is not None else None
                ),
                "finalizable": finalizable,
                "missing_evidence_can_produce_junk": False,
                "source_candidate_mutated": False,
            }
        )
    return output


def _candidate_rows(
    membership_rows: list[dict[str, Any]],
    *,
    legacy_keys: set[str],
    legacy_hashes: Mapping[str, list[str]],
    ledger_state: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in membership_rows:
        by_candidate.setdefault(row["canonical_key"], []).append(row)

    rows: list[dict[str, Any]] = []
    eligible_for_new_junk: list[str] = []
    conflicting_active_exclusions: list[str] = []
    restored_candidates = set(ledger_state["restored_candidates"])
    active_exclusions = ledger_state["active_exclusions"]
    for key in sorted(by_candidate):
        members = sorted(by_candidate[key], key=lambda item: item["query_id"])
        counts = {
            disposition: sum(
                item["disposition"] == disposition for item in members
            )
            for disposition in DISPOSITIONS
        }
        legacy_veto = key in legacy_keys
        restored_veto = key in restored_candidates
        active_exclusion = active_exclusions.get(key)
        all_explicitly_excluded = bool(
            members and counts["junk"] == len(members)
        )
        conflict = bool(
            active_exclusion is not None and not all_explicitly_excluded
        )

        reasons: list[str] = []
        if counts["keep"]:
            disposition = "keep"
            reasons.append("one_or_more_memberships_kept")
        elif counts["flagged"]:
            disposition = "flagged"
            reasons.append("one_or_more_memberships_flagged")
        elif legacy_veto:
            disposition = "flagged"
            reasons.append("legacy_edge_vetoes_global_junk")
        elif restored_veto:
            disposition = "flagged"
            reasons.append("restored_edge_vetoes_global_junk")
        elif all_explicitly_excluded:
            disposition = "junk"
            reasons.append("every_membership_explicitly_excluded")
            if active_exclusion is None:
                eligible_for_new_junk.append(key)
        else:
            disposition = "flagged"
            reasons.append("candidate_partition_not_closed")
        if conflict:
            disposition = "flagged"
            reasons.append("active_junk_ledger_conflicts_with_current_memberships")
            conflicting_active_exclusions.append(key)

        rows.append(
            {
                "schema": CANDIDATE_SCHEMA,
                "canonical_key": key,
                "disposition": disposition,
                "membership_count": len(members),
                "membership_counts": counts,
                "membership_keys": [
                    membership_key(item["canonical_key"], item["query_id"])
                    for item in members
                ],
                "all_memberships_explicitly_excluded": all_explicitly_excluded,
                "legacy_edge_present": legacy_veto,
                "legacy_edge_sha256s": legacy_hashes.get(key, []),
                "restored_edge_present": restored_veto,
                "active_junk_exclusion_present": active_exclusion is not None,
                "partition_reasons": sorted(set(reasons)),
                "candidate_deletion_performed": False,
                "main_corpus_write_authorized": False,
            }
        )
    return rows, eligible_for_new_junk, conflicting_active_exclusions


def _project_post_append_candidate_rows(
    candidate_rows: list[dict[str, Any]],
    eligible: Iterable[str],
) -> list[dict[str, Any]]:
    """Project the partition rows exactly as they will be published.

    ``_append_new_junk_exclusions`` binds each junk record to a candidate
    partition row.  The rows handed to it were derived from the pre-append
    ledger, but the rows that reach ``candidate_partition.ndjson`` are re-derived
    from the post-append ledger, where ``active_junk_exclusion_present`` has
    flipped.  Binding the pre-append row therefore produced a
    ``candidate_partition_sha256`` that could never match the published artifact.
    The append is unavoidable and one-way, so the published row is projected
    first, bound into the ledger, and then proven equal to the recomputed
    partition by ``build_generation``.
    """
    pending = set(eligible)
    return [
        {**row, "active_junk_exclusion_present": True}
        if row["canonical_key"] in pending
        else dict(row)
        for row in candidate_rows
    ]


def _append_new_junk_exclusions(
    root: Path,
    candidate_rows: list[dict[str, Any]],
    eligible: list[str],
    membership_rows: list[dict[str, Any]],
    *,
    event_time: str,
) -> int:
    # ``candidate_rows`` must be the rows that will be published, not the rows
    # derived from the pre-append ledger state.
    if not eligible:
        return 0
    validate_timestamp(event_time, "event_time")
    candidate_by_key = {
        row["canonical_key"]: row for row in candidate_rows
    }
    membership_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in membership_rows:
        membership_by_candidate.setdefault(row["canonical_key"], []).append(row)

    state = _load_ledger_state(root)
    appended = 0
    for key in sorted(eligible):
        if key in state["exclusion_by_candidate"]:
            continue
        members = sorted(
            membership_by_candidate[key],
            key=lambda item: item["query_id"],
        )
        if not members or any(item["disposition"] != "junk" for item in members):
            raise ReversibleCullingError(
                f"{key}: refusing derived junk append without all memberships junk"
            )
        membership_keys = [
            membership_key(key, item["query_id"]) for item in members
        ]
        binding = [
            {
                "membership_key": membership_key(key, item["query_id"]),
                "source_membership_sha256": item["source_membership_sha256"],
                "candidate_evidence_sha256": item["candidate_evidence_sha256"],
                "decision_sha256": item["decision_sha256"],
            }
            for item in members
        ]
        exclusion_id = "junk-" + value_sha256(
            {
                "canonical_key": key,
                "membership_bindings": binding,
                "tool_version": TOOL_VERSION,
            }
        )[:32]
        _append_ledger_record(
            root / JUNK_LEDGER,
            state["exclusions"],
            state["exclusion_lines"],
            {
                "schema": JUNK_SCHEMA,
                "exclusion_id": exclusion_id,
                "canonical_key": key,
                "recorded_at": event_time,
                "derived_by": {
                    "tool": TOOL_NAME,
                    "version": TOOL_VERSION,
                    "rule": "all-memberships-explicitly-excluded-no-veto-v1",
                },
                "membership_keys": membership_keys,
                "membership_bindings": binding,
                "candidate_partition_sha256": value_sha256(candidate_by_key[key]),
                "restorable": True,
                "source_candidate_mutated": False,
                "main_corpus_write_authorized": False,
            },
        )
        appended += 1
    _load_ledger_state(root)
    return appended


def _append_restoration_record(
    root: Path,
    state: Mapping[str, Any],
    *,
    canonical_key: str,
    reason: str,
    restored_by: str,
    restored_at: str,
    derived_by: Mapping[str, Any] | None = None,
    supersession_evidence: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Append one restoration edge against ``canonical_key``'s active exclusion.

    ``state`` must be a live ``_load_ledger_state`` result; the ledger lists are
    extended in place so consecutive appends keep the hash chain intact.
    """
    target = state["active_exclusions"].get(canonical_key)
    if target is None:
        if canonical_key in state["restored_candidates"]:
            raise ReversibleCullingError(f"{canonical_key} is already restored")
        raise ReversibleCullingError(
            f"{canonical_key} has no active derived junk exclusion"
        )
    target_row, target_sha = target
    restoration_id = "restore-" + value_sha256(
        {
            "canonical_key": canonical_key,
            "target_exclusion_record_sha256": target_sha,
            "reason": reason,
            "restored_by": restored_by,
            "restored_at": restored_at,
        }
    )[:32]
    core: dict[str, Any] = {
        "schema": RESTORATION_SCHEMA,
        "restoration_id": restoration_id,
        "canonical_key": canonical_key,
        "target_exclusion_id": target_row["exclusion_id"],
        "target_exclusion_record_sha256": target_sha,
        "reason": reason,
        "restored_by": restored_by,
        "restored_at": restored_at,
        "source_candidate_mutated": False,
        "main_corpus_write_authorized": False,
    }
    if derived_by is not None:
        core["derived_by"] = dict(derived_by)
    if supersession_evidence is not None:
        core["supersession_evidence"] = dict(supersession_evidence)
    return _append_ledger_record(
        root / RESTORATION_LEDGER,
        state["restorations"],
        state["restoration_lines"],
        core,
    )


def _write_restoration_receipt(
    root: Path,
    *,
    record: Mapping[str, Any],
    record_sha256: str,
    verified: Mapping[str, Any],
) -> dict[str, Any]:
    restoration_id = record["restoration_id"]
    key = record["canonical_key"]
    receipt = _seal_record(
        {
            "schema": RESTORATION_RECEIPT_SCHEMA,
            "restoration_id": restoration_id,
            "canonical_key": key,
            "restoration_record_sha256": record_sha256,
            "restoration_record": dict(record),
            "ledgers": {
                "junk_exclusions": _ledger_receipt(
                    root / JUNK_LEDGER,
                    verified["exclusion_lines"],
                ),
                "restorations": _ledger_receipt(
                    root / RESTORATION_LEDGER,
                    verified["restoration_lines"],
                ),
            },
            "candidate_restored_edge_active": (
                key in verified["restored_candidates"]
            ),
            "candidate_deletion_performed": False,
            "main_corpus_write_performed": False,
        }
    )
    _write_new(
        root / "restoration_receipts" / f"{restoration_id}.json",
        canonical_json_bytes(receipt) + b"\n",
    )
    return receipt


def _supersede_conflicting_junk_exclusions(
    root: Path,
    candidate_rows: list[dict[str, Any]],
    membership_rows: list[dict[str, Any]],
    conflicted: list[str],
    *,
    event_time: str,
) -> list[dict[str, Any]]:
    """Withdraw active junk exclusions the current partition contradicts.

    The junk ledger is the authoritative global-junk artifact, so flagging the
    generation row while the exclusion stays active leaves a consumer reading
    junk where the engine says flagged, permanently and with nothing scheduled
    to correct it.  The ledger is append-only, so the exclusion is neither
    mutated nor deleted: a superseding restoration edge is appended, which is
    exactly what ``_load_ledger_state`` already honours when it computes
    effective state.  The candidate keeps the resulting restored veto, so it can
    never be re-junked without a fresh human decision.
    """
    if not conflicted:
        return []
    validate_timestamp(event_time, "event_time")
    rows_by_key = {row["canonical_key"]: row for row in candidate_rows}
    non_junk_by_candidate: dict[str, list[str]] = {}
    for row in membership_rows:
        if row["disposition"] != "junk":
            non_junk_by_candidate.setdefault(row["canonical_key"], []).append(
                membership_key(row["canonical_key"], row["query_id"])
            )

    state = _load_ledger_state(root)
    appended: list[tuple[dict[str, Any], str]] = []
    for key in sorted(set(conflicted)):
        if key not in state["active_exclusions"]:
            continue
        row = rows_by_key[key]
        record, record_sha = _append_restoration_record(
            root,
            state,
            canonical_key=key,
            reason=(
                "Superseded by the culling engine: the active derived junk "
                "exclusion contradicts the current membership partition."
            ),
            restored_by=f"{TOOL_NAME} (derived supersession)",
            restored_at=event_time,
            derived_by={
                "tool": TOOL_NAME,
                "version": TOOL_VERSION,
                "rule": "supersede-active-junk-on-membership-conflict-v1",
            },
            supersession_evidence={
                "membership_counts": dict(row["membership_counts"]),
                "all_memberships_explicitly_excluded": row[
                    "all_memberships_explicitly_excluded"
                ],
                "conflicting_membership_keys": sorted(
                    non_junk_by_candidate.get(key, [])
                ),
                "candidate_partition_reasons": list(row["partition_reasons"]),
            },
        )
        appended.append((record, record_sha))

    verified = _load_ledger_state(root)
    for record, record_sha in appended:
        key = record["canonical_key"]
        if (
            key in verified["active_exclusions"]
            or key not in verified["restored_candidates"]
        ):
            raise ReversibleCullingError(
                f"{key}: supersession did not withdraw the active junk exclusion"
            )
        _write_restoration_receipt(
            root,
            record=record,
            record_sha256=record_sha,
            verified=verified,
        )
    return [record for record, _ in appended]


def _append_only_evidence(
    path: Path,
    before: Mapping[str, Any],
    records_after: int,
) -> dict[str, Any]:
    """Measure that this invocation only extended ``path``.

    The pre-invocation prefix of an append-only ledger must survive byte for
    byte; a rewrite, truncation, or in-place edit changes it.
    """
    body = path.read_bytes()
    return {
        "bytes_before": before["bytes"],
        "bytes_after": len(body),
        "records_before": before["record_count"],
        "records_after": records_after,
        "prefix_preserved": (
            len(body) >= before["bytes"]
            and sha256_bytes(body[: before["bytes"]]) == before["sha256"]
        ),
    }


def _ndjson_body(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _flagged_csv_body(rows: list[dict[str, Any]]) -> bytes:
    fields = [
        "canonical_key",
        "query_id",
        "flag_reasons",
        "decision_disposition",
        "candidate_evidence_sha256",
        "decision_sha256",
        "review_decision",
        "review_reason",
        "reviewer",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "canonical_key": row["canonical_key"],
                "query_id": row["query_id"],
                "flag_reasons": ";".join(row["flag_reasons"]),
                "decision_disposition": row["decision_disposition"] or "",
                "candidate_evidence_sha256": (
                    row["candidate_evidence_sha256"] or ""
                ),
                "decision_sha256": row["decision_sha256"] or "",
                "review_decision": "",
                "review_reason": "",
                "reviewer": "",
            }
        )
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _reverify_inputs(
    before: Mapping[str, dict[str, Any] | None],
) -> dict[str, dict[str, Any] | None]:
    return {
        name: (None if original is None else file_receipt(Path(original["path"])))
        for name, original in before.items()
    }


def _ensure_inputs_unchanged(
    before: Mapping[str, dict[str, Any] | None],
) -> None:
    current = _reverify_inputs(before)
    for name, original in before.items():
        if original is None:
            continue
        if current[name] != original:
            raise ReversibleCullingError(
                f"immutable input changed during culling: {name}"
            )


def _declared_totals_gate(
    declared_counts: Mapping[str, int] | None,
    candidate_count: int,
    membership_count: int,
) -> Any:
    """Assert the partition totals equal the corpus the operator declared.

    Nothing else in the run can detect a silently truncated discovery input:
    every universe-derived check still agrees with itself.
    """
    if declared_counts is None:
        return GATE_NO_DECLARED_INPUT_COUNTS
    if (
        declared_counts["candidate_count"] != candidate_count
        or declared_counts["membership_count"] != membership_count
    ):
        raise ReversibleCullingError(
            "partition totals disagree with the declared input counts: "
            f"declared {declared_counts['candidate_count']} candidates / "
            f"{declared_counts['membership_count']} memberships, partitioned "
            f"{candidate_count} / {membership_count}"
        )
    return True


def evaluate_gates(
    *,
    membership_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    membership_universe: set[tuple[str, str]],
    legacy_keys: set[str],
    legacy_declaration_mode: str,
    restored_candidates: set[str],
    declared_counts: Mapping[str, int] | None,
    ledger_record_counts: Mapping[str, int],
    ledger_append_only: Mapping[str, Mapping[str, Any]],
    input_receipts: Mapping[str, dict[str, Any] | None],
    reverified_input_receipts: Mapping[str, dict[str, Any] | None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive every receipt gate from this run and fail closed on a violation.

    The gates block used to be eight hardcoded ``True`` literals backed by two
    checks that could not fail.  Every gate below is computed from the rows this
    run is about to publish and from the ledgers it just read back, so each one
    is falsifiable; a violated gate raises rather than shipping a receipt that
    claims a property it does not have.
    """
    if legacy_declaration_mode not in (
        LEGACY_EDGE_SOURCE_FILE,
        LEGACY_EDGE_SOURCE_DECLARED_ABSENT,
    ):
        raise ReversibleCullingError(
            f"unknown legacy edge declaration: {legacy_declaration_mode!r}"
        )

    candidate_universe = {key for key, _ in membership_universe}
    membership_key_universe = {
        membership_key(key, query_id) for key, query_id in membership_universe
    }

    # Gate: the membership partition is exactly the discovery universe, labelled
    # once each.  The previous check summed the same rows it counted and could
    # not fail.
    seen_memberships: set[tuple[str, str]] = set()
    for row in membership_rows:
        if row["disposition"] not in DISPOSITIONS:
            raise ReversibleCullingError(
                "membership partition holds an unknown disposition: "
                f"{row['disposition']!r}"
            )
        natural_key = (row["canonical_key"], row["query_id"])
        if natural_key in seen_memberships:
            raise ReversibleCullingError(
                f"membership partition repeats {natural_key}"
            )
        seen_memberships.add(natural_key)
    if seen_memberships != membership_universe:
        raise ReversibleCullingError(
            "membership partition does not cover the discovery universe exactly:"
            f" {len(seen_memberships)} partitioned vs "
            f"{len(membership_universe)} discovered"
        )

    # Gate: the candidate partition covers every discovered candidate once and
    # tiles the membership partition.
    seen_candidates: set[str] = set()
    covered_membership_keys: set[str] = set()
    counted_memberships = 0
    for row in candidate_rows:
        if row["disposition"] not in DISPOSITIONS:
            raise ReversibleCullingError(
                "candidate partition holds an unknown disposition: "
                f"{row['disposition']!r}"
            )
        key = row["canonical_key"]
        if key in seen_candidates:
            raise ReversibleCullingError(f"candidate partition repeats {key}")
        seen_candidates.add(key)
        if row["membership_count"] != len(row["membership_keys"]):
            raise ReversibleCullingError(
                f"{key}: membership_count disagrees with membership_keys"
            )
        if sum(row["membership_counts"].values()) != row["membership_count"]:
            raise ReversibleCullingError(
                f"{key}: membership_counts do not sum to membership_count"
            )
        counted_memberships += row["membership_count"]
        covered_membership_keys.update(row["membership_keys"])
    if seen_candidates != candidate_universe:
        raise ReversibleCullingError(
            "candidate partition does not cover the discovery universe exactly:"
            f" {len(seen_candidates)} partitioned vs "
            f"{len(candidate_universe)} discovered"
        )
    if (
        counted_memberships != len(membership_rows)
        or covered_membership_keys != membership_key_universe
    ):
        raise ReversibleCullingError(
            "candidate partition does not tile the membership partition"
        )

    declared_totals_gate = _declared_totals_gate(
        declared_counts,
        len(candidate_rows),
        len(membership_rows),
    )

    # Gate: missing or incomplete evidence is flagged and never becomes junk.
    junk_candidates = {
        row["canonical_key"]
        for row in candidate_rows
        if row["disposition"] == "junk"
    }
    incomplete_evidence_memberships = 0
    for row in membership_rows:
        reasons = set(row["flag_reasons"])
        label = membership_key(row["canonical_key"], row["query_id"])
        if row["missing_evidence_can_produce_junk"]:
            raise ReversibleCullingError(
                f"{label}: membership claims missing evidence can produce junk"
            )
        if reasons & EVIDENCE_INCOMPLETE_FLAG_REASONS:
            incomplete_evidence_memberships += 1
            if row["disposition"] != "flagged" or row["finalizable"]:
                raise ReversibleCullingError(
                    f"{label}: incomplete evidence was not flagged"
                )
        if row["canonical_key"] in junk_candidates and (
            row["disposition"] != "junk" or reasons
        ):
            raise ReversibleCullingError(
                f"{label}: globally junked candidate holds a membership that is "
                "not a clean explicit exclusion"
            )

    # Gate: global junk requires every membership to be an explicit exclusion,
    # and the ledger must already carry the matching active exclusion.
    for row in candidate_rows:
        if row["disposition"] != "junk":
            continue
        key = row["canonical_key"]
        if (
            not row["all_memberships_explicitly_excluded"]
            or row["membership_count"] < 1
            or row["membership_counts"]["junk"] != row["membership_count"]
        ):
            raise ReversibleCullingError(
                f"{key}: global junk without every membership explicitly excluded"
            )
        if not row["active_junk_exclusion_present"]:
            raise ReversibleCullingError(
                f"{key}: global junk is not bound to an active ledger exclusion"
            )

    # Gate: keep, flagged, legacy, and restored edges all veto global junk, and
    # no active ledger exclusion outlives a non-junk partition row.
    vetoed_by = {"keep": 0, "flagged": 0, "legacy": 0, "restored": 0}
    for row in candidate_rows:
        key = row["canonical_key"]
        if row["legacy_edge_present"] != (key in legacy_keys):
            raise ReversibleCullingError(
                f"{key}: legacy edge flag disagrees with the legacy source"
            )
        if row["restored_edge_present"] != (key in restored_candidates):
            raise ReversibleCullingError(
                f"{key}: restored edge flag disagrees with the restoration ledger"
            )
        arms = {
            "keep": bool(row["membership_counts"]["keep"]),
            "flagged": bool(row["membership_counts"]["flagged"]),
            "legacy": bool(row["legacy_edge_present"]),
            "restored": bool(row["restored_edge_present"]),
        }
        for arm, active in arms.items():
            if active:
                vetoed_by[arm] += 1
        if any(arms.values()) and row["disposition"] == "junk":
            raise ReversibleCullingError(
                f"{key}: a veto edge did not stop global junk"
            )
        if row["active_junk_exclusion_present"] and row["disposition"] != "junk":
            raise ReversibleCullingError(
                f"{key}: an active derived junk exclusion survives a "
                f"{row['disposition']} candidate partition row"
            )

    # Gate: both ledgers only grew, and the pre-invocation prefix of each is
    # byte-identical to what it was before the appends.
    for name, evidence in ledger_append_only.items():
        if not evidence["prefix_preserved"]:
            raise ReversibleCullingError(
                f"{name}: ledger prefix changed; the ledger is not append-only"
            )
        if evidence["records_after"] < evidence["records_before"]:
            raise ReversibleCullingError(
                f"{name}: ledger lost records; the ledger is not append-only"
            )

    # Gate: the immutable inputs are byte-identical to the receipts taken before
    # any output was derived, and nothing in the run claims a source write.
    if dict(reverified_input_receipts) != dict(input_receipts):
        raise ReversibleCullingError(
            "immutable inputs changed during culling"
        )
    for row in membership_rows:
        if row["source_candidate_mutated"]:
            raise ReversibleCullingError(
                f"{membership_key(row['canonical_key'], row['query_id'])}: "
                "membership claims a source candidate was mutated"
            )
    for row in candidate_rows:
        if row["candidate_deletion_performed"] or row["main_corpus_write_authorized"]:
            raise ReversibleCullingError(
                f"{row['canonical_key']}: candidate claims a deletion or main "
                "corpus write"
            )

    gates = {
        "membership_partition_exact": True,
        "candidate_partition_exact": True,
        "partition_totals_match_declared_input_counts": declared_totals_gate,
        "missing_evidence_flagged_never_junk": True,
        "candidate_junk_requires_every_membership_excluded": True,
        "keep_flagged_legacy_restored_edges_veto_global_junk": (
            True
            if legacy_declaration_mode == LEGACY_EDGE_SOURCE_FILE
            else GATE_LEGACY_SOURCE_DECLARED_ABSENT
        ),
        "junk_and_restoration_ledgers_append_only": True,
        "source_candidates_immutable": True,
        # No output can witness this: it is enforced before any input is read,
        # by refusing an input that resolves inside the output root.
        "inputs_outside_output_root": GATE_ENFORCED_BY_PRECONDITION,
        "integration_authorized": False,
    }
    evidence = {
        "membership_rows_partitioned": len(membership_rows),
        "candidate_rows_partitioned": len(candidate_rows),
        "discovery_membership_universe": len(membership_universe),
        "discovery_candidate_universe": len(candidate_universe),
        "declared_input_counts": (
            dict(declared_counts) if declared_counts is not None else None
        ),
        "incomplete_evidence_memberships": incomplete_evidence_memberships,
        "globally_junked_candidates": len(junk_candidates),
        "candidates_vetoed_by": vetoed_by,
        "legacy_edge_declaration_mode": legacy_declaration_mode,
        "legacy_edge_candidates": len(legacy_keys),
        "restored_edge_candidates": len(restored_candidates & candidate_universe),
        "ledger_records_chain_verified": dict(ledger_record_counts),
        "source_inputs_reverified": sorted(
            name for name, receipt in input_receipts.items() if receipt is not None
        ),
    }
    return gates, evidence


def build_generation(
    *,
    evidence_path: Path,
    memberships_path: Path,
    decisions_path: Path,
    root: Path,
    legacy_edges_path: Path | None = None,
    no_legacy_edges: bool = False,
    declared_candidate_count: int | None = None,
    declared_membership_count: int | None = None,
    event_time: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    legacy_declaration_mode = require_legacy_edge_declaration(
        legacy_edges_path,
        no_legacy_edges,
    )
    declared_counts = require_declared_input_counts(
        declared_candidate_count,
        declared_membership_count,
    )
    inputs = {
        "candidate_evidence": file_receipt(evidence_path),
        "discovery_memberships": file_receipt(memberships_path),
        "membership_decisions": file_receipt(decisions_path),
        "legacy_edges": optional_file_receipt(legacy_edges_path),
    }
    for name, receipt in inputs.items():
        if receipt is None:
            continue
        input_path = Path(receipt["path"])
        try:
            input_path.relative_to(root)
        except ValueError:
            continue
        raise ReversibleCullingError(
            f"{name} is inside the output root; inputs must remain immutable"
        )

    with ExclusiveLock(root):
        initialize_ledgers(root)
        ledger_state = _load_ledger_state(root)
        junk_ledger_before = _ledger_receipt(
            root / JUNK_LEDGER,
            ledger_state["exclusion_lines"],
        )
        restoration_ledger_before = _ledger_receipt(
            root / RESTORATION_LEDGER,
            ledger_state["restoration_lines"],
        )
        evidence, evidence_hashes = _index_evidence(
            read_ndjson(evidence_path, label="candidate_evidence")
        )
        memberships, membership_hashes = _index_memberships(
            read_ndjson(memberships_path, label="discovery_memberships")
        )
        candidate_keys = {key for key, _ in memberships}
        extra_evidence = set(evidence) - candidate_keys
        if extra_evidence:
            raise ReversibleCullingError(
                "candidate evidence is outside the discovery membership universe: "
                + ", ".join(sorted(extra_evidence)[:10])
            )
        decisions, decision_hashes = _index_decisions(
            read_ndjson(decisions_path, label="membership_decisions"),
            set(memberships),
        )
        legacy_keys, legacy_hashes = _load_legacy_keys(
            legacy_edges_path,
            candidate_keys,
        )
        membership_rows = _classify_memberships(
            evidence,
            evidence_hashes,
            memberships,
            membership_hashes,
            decisions,
            decision_hashes,
        )
        candidate_rows, eligible, conflicted = _candidate_rows(
            membership_rows,
            legacy_keys=legacy_keys,
            legacy_hashes=legacy_hashes,
            ledger_state=ledger_state,
        )
        # Checked before any ledger append so a declared-count mismatch cannot
        # leave a derived exclusion behind for a run that never published.
        _declared_totals_gate(
            declared_counts,
            len(candidate_rows),
            len(membership_rows),
        )
        resolved_event_time = event_time or utc_now()

        superseded = _supersede_conflicting_junk_exclusions(
            root,
            candidate_rows,
            membership_rows,
            conflicted,
            event_time=resolved_event_time,
        )
        if superseded:
            ledger_state = _load_ledger_state(root)
            candidate_rows, eligible, conflicted = _candidate_rows(
                membership_rows,
                legacy_keys=legacy_keys,
                legacy_hashes=legacy_hashes,
                ledger_state=ledger_state,
            )
            if conflicted:
                raise ReversibleCullingError(
                    "active derived junk exclusions survived supersession: "
                    + ", ".join(sorted(conflicted)[:10])
                )

        published_rows = _project_post_append_candidate_rows(
            candidate_rows,
            eligible,
        )
        appended_count = _append_new_junk_exclusions(
            root,
            published_rows,
            eligible,
            membership_rows,
            event_time=resolved_event_time,
        )
        ledger_state = _load_ledger_state(root)
        candidate_rows, residual_eligible, residual_conflicts = _candidate_rows(
            membership_rows,
            legacy_keys=legacy_keys,
            legacy_hashes=legacy_hashes,
            ledger_state=ledger_state,
        )
        if candidate_rows != published_rows:
            raise ReversibleCullingError(
                "published candidate partition diverged from the rows bound into "
                "the derived junk ledger"
            )
        if residual_eligible or residual_conflicts:
            raise ReversibleCullingError(
                "derived junk ledger did not converge with the candidate "
                "partition"
            )

        membership_counts = {
            disposition: sum(
                row["disposition"] == disposition for row in membership_rows
            )
            for disposition in DISPOSITIONS
        }
        candidate_counts = {
            disposition: sum(
                row["disposition"] == disposition for row in candidate_rows
            )
            for disposition in DISPOSITIONS
        }
        ledger_append_only = {
            "junk_exclusions": _append_only_evidence(
                root / JUNK_LEDGER,
                junk_ledger_before,
                len(ledger_state["exclusion_lines"]),
            ),
            "restorations": _append_only_evidence(
                root / RESTORATION_LEDGER,
                restoration_ledger_before,
                len(ledger_state["restoration_lines"]),
            ),
        }
        gates, gate_evidence = evaluate_gates(
            membership_rows=membership_rows,
            candidate_rows=candidate_rows,
            membership_universe=set(memberships),
            legacy_keys=legacy_keys,
            legacy_declaration_mode=legacy_declaration_mode,
            restored_candidates=set(ledger_state["restored_candidates"]),
            declared_counts=declared_counts,
            ledger_record_counts={
                "junk_exclusions": len(ledger_state["exclusion_lines"]),
                "restorations": len(ledger_state["restoration_lines"]),
            },
            ledger_append_only=ledger_append_only,
            input_receipts=inputs,
            reverified_input_receipts=_reverify_inputs(inputs),
        )

        flagged_rows = [
            row for row in membership_rows if row["disposition"] == "flagged"
        ]
        junk_receipt = _ledger_receipt(
            root / JUNK_LEDGER,
            ledger_state["exclusion_lines"],
        )
        restoration_receipt = _ledger_receipt(
            root / RESTORATION_LEDGER,
            ledger_state["restoration_lines"],
        )
        legacy_edges_declaration = {
            "mode": legacy_declaration_mode,
            "record_count": sum(len(values) for values in legacy_hashes.values()),
            "candidate_veto_count": len(legacy_keys),
        }
        generation_basis = {
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
            "inputs": inputs,
            "legacy_edges_declaration": legacy_edges_declaration,
            "declared_input_counts": declared_counts,
            "junk_ledger": junk_receipt,
            "restoration_ledger": restoration_receipt,
            "membership_rows_sha256": value_sha256(membership_rows),
            "candidate_rows_sha256": value_sha256(candidate_rows),
        }
        generation_id = value_sha256(generation_basis)
        generation_root = root / "generations" / generation_id
        bodies = {
            "membership_partition.ndjson": _ndjson_body(membership_rows),
            "candidate_partition.ndjson": _ndjson_body(candidate_rows),
            "flagged_memberships.ndjson": _ndjson_body(flagged_rows),
            "flagged_memberships.csv": _flagged_csv_body(flagged_rows),
        }
        output_receipts = {
            name: {
                "path": str((generation_root / name).resolve()),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "record_count": (
                    len(flagged_rows)
                    if name.startswith("flagged_memberships")
                    else (
                        len(membership_rows)
                        if name.startswith("membership_partition")
                        else len(candidate_rows)
                    )
                ),
            }
            for name, body in bodies.items()
        }
        receipt_core = {
            "schema": RECEIPT_SCHEMA,
            "status": "PASS_REVERSIBLE_CULLING_PROPOSAL",
            "generation_id": generation_id,
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
            "inputs": inputs,
            "legacy_edges_declaration": legacy_edges_declaration,
            # ``new_junk_exclusions_appended``, ``junk_exclusions_superseded``,
            # and the append-only prefix proof are deliberately absent here:
            # they are per-invocation metadata, not generation state.  A first
            # run appends N records and an identical rerun appends 0 while
            # describing the exact same generation, so embedding them in the
            # sealed manifest would make an idempotent rerun collide.  They are
            # preserved per invocation under ``invocations/`` and returned to
            # the caller below.
            "ledgers": {
                "junk_exclusions": junk_receipt,
                "restorations": restoration_receipt,
            },
            "counts": {
                "candidate_count": len(candidate_rows),
                "membership_count": len(membership_rows),
                "candidate_partition": candidate_counts,
                "membership_partition": membership_counts,
                "declared_input_counts": declared_counts,
                "legacy_candidate_veto_count": len(legacy_keys),
                "restored_candidate_veto_count": len(
                    ledger_state["restored_candidates"]
                ),
            },
            "outputs": output_receipts,
            "gates": gates,
            "gate_evidence": gate_evidence,
            "safety": {
                "candidate_deletion_performed": False,
                "source_candidate_write_performed": False,
                "scoring_performed": False,
                "sync_or_promotion_performed": False,
                "main_corpus_write_performed": False,
            },
            "xlsx_status": {
                "created": False,
                "reason": (
                    "artifact-tool workspace dependency loader unavailable; "
                    "reviewable UTF-8 CSV and authoritative NDJSON emitted"
                ),
            },
        }
        receipt = _seal_record(receipt_core)
        manifest = {
            "schema": GENERATION_SCHEMA,
            "generation_id": generation_id,
            "receipt": receipt,
            "generation_basis": generation_basis,
        }
        manifest_body = canonical_json_bytes(manifest) + b"\n"

        if generation_root.exists():
            manifest_path = generation_root / "manifest.json"
            if (
                not manifest_path.is_file()
                or manifest_path.read_bytes() != manifest_body
            ):
                raise ReversibleCullingError(
                    f"immutable generation collision: {generation_root}"
                )
            for name, body in bodies.items():
                if (generation_root / name).read_bytes() != body:
                    raise ReversibleCullingError(
                        f"immutable generation artifact drift: {name}"
                    )
        else:
            generation_root.mkdir(parents=True, exist_ok=False)
            for name, body in bodies.items():
                _write_new(generation_root / name, body)
            _write_new(generation_root / "manifest.json", manifest_body)

        # Preserve every invocation against this generation, including reruns
        # that appended nothing.  Content-addressed so an identical invocation
        # is idempotent while a distinct one is never lost.
        invocation = {
            "schema": GENERATION_SCHEMA + "-invocation",
            "generation_id": generation_id,
            "event_time": resolved_event_time,
            "new_junk_exclusions_appended": appended_count,
            "junk_exclusions_superseded": len(superseded),
            "superseded_restoration_ids": [
                record["restoration_id"] for record in superseded
            ],
            "ledger_append_only": ledger_append_only,
        }
        invocation_body = canonical_json_bytes(invocation) + b"\n"
        invocation_path = (
            generation_root
            / "invocations"
            / f"{sha256_bytes(invocation_body)}.json"
        )
        if not invocation_path.exists():
            _write_new(invocation_path, invocation_body)

        _ensure_inputs_unchanged(inputs)
        return {
            **manifest,
            "receipt": {
                **receipt,
                "ledgers": {
                    **receipt["ledgers"],
                    "new_junk_exclusions_appended": appended_count,
                    "junk_exclusions_superseded": len(superseded),
                },
            },
        }


def append_restoration(
    *,
    root: Path,
    canonical_key: str,
    reason: str,
    restored_by: str,
    restored_at: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    key = require_canonical_key(canonical_key)
    reason = require_text(reason, "reason")
    restored_by = require_text(restored_by, "restored_by")
    timestamp = validate_timestamp(restored_at or utc_now(), "restored_at")
    with ExclusiveLock(root):
        initialize_ledgers(root)
        state = _load_ledger_state(root)
        record, record_sha = _append_restoration_record(
            root,
            state,
            canonical_key=key,
            reason=reason,
            restored_by=restored_by,
            restored_at=timestamp,
        )
        return _write_restoration_receipt(
            root,
            record=record,
            record_sha256=record_sha,
            verified=_load_ledger_state(root),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run")
    run.add_argument("--evidence", type=Path, required=True)
    run.add_argument("--memberships", type=Path, required=True)
    run.add_argument("--decisions", type=Path, required=True)
    # The legacy edge source is declared, never omitted: an absent flag used to
    # be indistinguishable from "no legacy edges exist".
    legacy_source = run.add_mutually_exclusive_group(required=True)
    legacy_source.add_argument("--legacy-edges", type=Path)
    legacy_source.add_argument("--no-legacy-edges", action="store_true")
    run.add_argument("--expect-candidate-count", type=int)
    run.add_argument("--expect-membership-count", type=int)
    run.add_argument("--root", type=Path, required=True)
    run.add_argument("--event-time")

    restore = commands.add_parser("restore")
    restore.add_argument("--root", type=Path, required=True)
    restore.add_argument("--canonical-key", required=True)
    restore.add_argument("--reason", required=True)
    restore.add_argument("--restored-by", required=True)
    restore.add_argument("--restored-at")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            result = build_generation(
                evidence_path=args.evidence.resolve(),
                memberships_path=args.memberships.resolve(),
                decisions_path=args.decisions.resolve(),
                legacy_edges_path=(
                    args.legacy_edges.resolve() if args.legacy_edges else None
                ),
                no_legacy_edges=args.no_legacy_edges,
                declared_candidate_count=args.expect_candidate_count,
                declared_membership_count=args.expect_membership_count,
                root=args.root,
                event_time=args.event_time,
            )
        else:
            result = append_restoration(
                root=args.root,
                canonical_key=args.canonical_key,
                reason=args.reason,
                restored_by=args.restored_by,
                restored_at=args.restored_at,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        return 0
    except (
        ReversibleCullingError,
        CullingContractError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
