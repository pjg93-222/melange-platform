# -*- coding: utf-8 -*-
"""Materialize a staged, legacy-compatible official roll-call aggregate.

The official collector is the evidence producer.  This module is a separate
computation/audit boundary: it verifies the collector generation and its
accepted-ledger/direction bindings, independently recomputes category
contributions, applies the approved Congress decay, and emits both a
``vote_category_lean.json`` compatibility projection and complete decision
ledgers.

No production path is accepted.  Outputs are allowed only below
``audits/legfix5`` or the operating-system temporary directory.  The default
paths support the isolated shadow runner: it sets ``MONEYBALL_ROOT`` to the
staged run directory and materializes inputs below ``inputs/``.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterable, Sequence

from paths import ROOT as MB_ROOT
from legislation.collect_official_rollcall_events import (
    CATEGORIES,
    canonical_category,
    normalize_direction,
)
from legislation_measure_key import measure_weight, measure_weight_policy


CODE_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "official-rollcall-aggregate-materialization-v1"
CONTRIBUTION_SCHEMA = "official-rollcall-category-contribution-v1"
CELL_SCHEMA = "official-rollcall-category-cell-v1"
DECAY = {119: 1.0, 118: 0.66, 117: 0.33, 116: 0.16}
MANIFEST_NAME = "rollcall_aggregate_manifest.json"
OUTPUT_NAMES = (
    "vote_category_lean.json",
    "official_rollcall_category_cells.ndjson",
    "official_rollcall_category_contributions.ndjson",
    "official_rollcall_event_decisions.ndjson",
    "official_rollcall_member_vote_decisions.ndjson",
    "official_rollcall_measure_decisions.ndjson",
    "official_rollcall_member_identity_audit.ndjson",
    "rollcall_consumer_compatibility.json",
)
REQUIRED_ROLLCALL_OUTPUTS = {
    "official_rollcall_event_ledger.ndjson",
    "rollcall_measure_match_audit.ndjson",
    "official_rollcall_member_votes.ndjson",
    "rollcall_direction_audit.ndjson",
    "source_snapshots.ndjson",
    "official_rollcall_aggregate.json",
}
MEASURE_RE = re.compile(
    r"^(?P<congress>\d{1,3}):(?P<measure_type>"
    r"hr|s|hjres|sjres|hconres|sconres|hres|sres|hamdt|samdt):"
    r"(?P<number>\d+)$"
)
EVENT_RE = re.compile(
    r"^(?P<congress>\d{1,3}):(?P<chamber>house|senate):"
    r"(?P<session>\d+):(?P<roll>\d+)$"
)
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
HEX_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class MaterializerError(RuntimeError):
    """A fail-closed staged materialization contract violation."""


def parser() -> argparse.ArgumentParser:
    root = Path(MB_ROOT)
    result = argparse.ArgumentParser(
        description=(
            "Build a staged-only, hash-bound official roll-call category "
            "aggregate. Never writes a workbook or production artifact."
        )
    )
    result.add_argument(
        "--rollcall",
        type=Path,
        default=root / "inputs" / "rollcall",
        help="PASS output directory or manifest from collect_official_rollcall_events.py",
    )
    result.add_argument(
        "--accepted-generation",
        type=Path,
        default=root / "inputs" / "accepted_generation",
        help="Complete staged accepted-legislation generation directory or manifest",
    )
    result.add_argument(
        "--direction",
        type=Path,
        default=root / "inputs" / "direction",
        help="PASS direction generation directory or manifest",
    )
    result.add_argument(
        "--out",
        type=Path,
        default=root,
        help="Existing or new staged output directory",
    )
    return result


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    shown = path.relative_to(relative_to) if relative_to else path.resolve()
    return {
        "path": str(shown).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaterializerError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MaterializerError(f"JSON root must be an object: {path}")
    return value


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise MaterializerError(
                        f"non-object NDJSON row at {path}:{line_number}"
                    )
                rows.append({**value, "_materializer_input_row": line_number})
    except json.JSONDecodeError as exc:
        raise MaterializerError(f"invalid NDJSON at {path}: {exc}") from exc
    except OSError as exc:
        raise MaterializerError(f"cannot read {path}: {exc}") from exc
    return rows


def resolve_manifest(value: Path, label: str) -> tuple[Path, dict[str, Any]]:
    path = value.expanduser().resolve()
    if path.is_dir():
        path = path / "manifest.json"
    if not path.is_file():
        raise MaterializerError(f"{label} manifest does not exist: {path}")
    return path, read_json(path)


def recursive_hashes(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                "sha256" in str(key).casefold()
                and isinstance(item, str)
                and HEX_SHA_RE.fullmatch(item.casefold())
            ):
                result.add(item.casefold())
            result.update(recursive_hashes(item))
    elif isinstance(value, list):
        for item in value:
            result.update(recursive_hashes(item))
    return result


def receipt_path_candidates(
    manifest_path: Path, item: dict[str, Any]
) -> list[Path]:
    raw = item.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise MaterializerError(f"output receipt lacks a path in {manifest_path}")
    path = Path(raw)
    declared = (
        path.resolve()
        if path.is_absolute()
        else (manifest_path.parent / path).resolve()
    )
    # The shadow runner copies producer outputs beside a copied manifest but
    # intentionally preserves the original receipt payload. Prefer that
    # isolated, hash-identical copy when present.
    local = (manifest_path.parent / path.name).resolve()
    return [local, declared] if local != declared else [declared]


def verify_receipt(
    manifest_path: Path, item: dict[str, Any], label: str
) -> dict[str, Any]:
    sha = str(item.get("sha256") or "").casefold()
    if not HEX_SHA_RE.fullmatch(sha):
        raise MaterializerError(f"{label} has no valid SHA-256 receipt")
    candidates = receipt_path_candidates(manifest_path, item)
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise MaterializerError(
            f"{label} output is missing: "
            + ", ".join(str(path) for path in candidates)
        )
    path = existing[0]
    actual = sha256_file(path)
    if actual != sha:
        # A corrupt isolated copy must fail closed; never fall back to the
        # original absolute path and silently escape the shadow generation.
        raise MaterializerError(
            f"{label} output hash mismatch: expected {sha}, observed {actual}"
        )
    if item.get("size") is not None and path.stat().st_size != int(item["size"]):
        raise MaterializerError(f"{label} output size mismatch: {path}")
    return {**file_receipt(path), "resolved_path": path}


def verify_output_receipts(
    manifest_path: Path,
    manifest: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    raw = manifest.get("outputs")
    if not isinstance(raw, dict) or not raw:
        raise MaterializerError(f"{label} manifest has no output receipts")
    result = {}
    for name, item in sorted(raw.items()):
        if not isinstance(item, dict):
            raise MaterializerError(f"{label} output receipt is invalid: {name}")
        result[str(name)] = verify_receipt(
            manifest_path, item, f"{label}/{name}"
        )
    return result


def accepted_receipt(
    manifest_path: Path, manifest: dict[str, Any]
) -> dict[str, Any]:
    raw_path = manifest.get("canonical_ledger_file")
    raw_sha = str(manifest.get("canonical_ledger_sha256") or "").casefold()
    if not raw_path or not HEX_SHA_RE.fullmatch(raw_sha):
        raise MaterializerError(
            "accepted generation lacks its canonical ledger hash receipt"
        )
    item = {
        "path": raw_path,
        "sha256": raw_sha,
        "size": manifest.get("canonical_ledger_size"),
    }
    if item["size"] is None:
        item.pop("size")
    return verify_receipt(manifest_path, item, "accepted/canonical_ledger")


def validate_inputs(
    *,
    rollcall_value: Path,
    accepted_value: Path,
    direction_value: Path,
) -> dict[str, Any]:
    accepted_path, accepted = resolve_manifest(
        accepted_value, "accepted generation"
    )
    if not (
        accepted.get("schema") == "accepted-legislation-generation-manifest-v1"
        and str(accepted.get("status") or "").casefold()
        in {"complete", "pass", "passed"}
        and accepted.get("generation_complete") is True
        and accepted.get("upstream_acceptance_passed") is True
        and accepted.get("staged_only") is True
        and accepted.get("production_artifacts_written") is False
    ):
        raise MaterializerError(
            "accepted generation is not complete, accepted, and staged-only"
        )
    if accepted.get("measure_weight_policy") != measure_weight_policy():
        raise MaterializerError(
            "accepted generation measure-weight policy is missing or drifted"
        )
    accepted_output = accepted_receipt(accepted_path, accepted)

    direction_path, direction = resolve_manifest(direction_value, "direction")
    if not (
        direction.get("schema") == "legislation-direction-ledger-manifest-v1"
        and direction.get("status") == "PASS"
        and direction.get("acceptance_passed") is True
        and direction.get("production_write_prohibited") is True
    ):
        raise MaterializerError("direction generation manifest is not PASS")
    direction_outputs = verify_output_receipts(
        direction_path, direction, "direction"
    )
    scoring_name = "scoring_directions.ndjson"
    if scoring_name not in direction_outputs:
        raise MaterializerError(
            f"direction generation lacks {scoring_name}"
        )
    if accepted_output["sha256"] not in recursive_hashes(direction.get("inputs")):
        raise MaterializerError(
            "direction generation is not hash-bound to the accepted ledger"
        )

    rollcall_path, rollcall = resolve_manifest(rollcall_value, "rollcall")
    if not (
        rollcall.get("schema") == "official-rollcall-staging-v1"
        and rollcall.get("acceptance_passed") is True
        and int(rollcall.get("blocking_issue_count") or 0) == 0
        and not list(rollcall.get("blocking_issues") or [])
        and rollcall.get("staging_only") is True
        and rollcall.get("production_vote_category_lean_written") is False
    ):
        raise MaterializerError("official roll-call collector manifest is not PASS")
    rollcall_outputs = verify_output_receipts(
        rollcall_path, rollcall, "rollcall"
    )
    missing = sorted(REQUIRED_ROLLCALL_OUTPUTS - set(rollcall_outputs))
    if missing:
        raise MaterializerError(
            "roll-call generation lacks required outputs: " + ", ".join(missing)
        )
    rollcall_input_hashes = recursive_hashes(rollcall.get("inputs"))
    if accepted_output["sha256"] not in rollcall_input_hashes:
        raise MaterializerError(
            "roll-call generation is not hash-bound to the accepted ledger"
        )
    scoring_sha = direction_outputs[scoring_name]["sha256"]
    if scoring_sha not in rollcall_input_hashes:
        raise MaterializerError(
            "roll-call generation is not hash-bound to scoring directions"
        )
    return {
        "accepted": {
            "manifest_path": accepted_path,
            "manifest": accepted,
            "manifest_receipt": file_receipt(accepted_path),
            "ledger": accepted_output,
        },
        "direction": {
            "manifest_path": direction_path,
            "manifest": direction,
            "manifest_receipt": file_receipt(direction_path),
            "outputs": direction_outputs,
        },
        "rollcall": {
            "manifest_path": rollcall_path,
            "manifest": rollcall,
            "manifest_receipt": file_receipt(rollcall_path),
            "outputs": rollcall_outputs,
        },
    }


def parse_measure_key(value: Any) -> tuple[int, str, int, str]:
    row = value if isinstance(value, dict) else {}
    raw = (
        row.get("canonical_key")
        or row.get("measure_key")
        or (value if isinstance(value, str) else None)
    )
    if isinstance(raw, dict):
        raw = raw.get("canonical_key")
    match = MEASURE_RE.fullmatch(str(raw or "").strip().casefold())
    if not match:
        raise MaterializerError(f"invalid type-aware measure key: {raw!r}")
    congress = int(match.group("congress"))
    measure_type = match.group("measure_type")
    number = int(match.group("number"))
    canonical = f"{congress}:{measure_type}:{number}"
    field_values = {
        "congress": row.get("congress"),
        "measure_type": row.get("measure_type") or row.get("type"),
        "number": row.get("number") or row.get("measure_number"),
    }
    if field_values["congress"] is not None and int(field_values["congress"]) != congress:
        raise MaterializerError(f"measure Congress conflicts with {canonical}")
    if (
        field_values["measure_type"] is not None
        and str(field_values["measure_type"]).casefold() != measure_type
    ):
        raise MaterializerError(f"measure type conflicts with {canonical}")
    if field_values["number"] is not None and int(field_values["number"]) != number:
        raise MaterializerError(f"measure number conflicts with {canonical}")
    return congress, measure_type, number, canonical


def measure_from_row(row: dict[str, Any]) -> tuple[int, str, int, str]:
    value = row.get("measure_key")
    return parse_measure_key(value if value is not None else row)


def parse_event_key(
    row: dict[str, Any], *, included: bool
) -> tuple[str, tuple[int, str, int, int] | None]:
    event_id = str(row.get("event_id") or "").strip().casefold()
    nested = row.get("event_key")
    if nested is None:
        if included:
            raise MaterializerError(
                f"included event lacks an immutable event key: {event_id!r}"
            )
        if not event_id:
            raise MaterializerError("event row lacks event_id")
        return event_id, None
    if not isinstance(nested, dict):
        raise MaterializerError(f"invalid event_key for {event_id!r}")
    chamber_token = re.sub(
        r"[^a-z]", "", str(nested.get("chamber") or "").casefold()
    )
    chamber = "house" if chamber_token in {"h", "house"} else (
        "senate" if chamber_token in {"s", "senate"} else ""
    )
    try:
        components = (
            int(nested["congress"]),
            chamber,
            int(nested["session"]),
            int(nested["roll"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MaterializerError(f"incomplete event_key for {event_id!r}") from exc
    canonical = (
        f"{components[0]}:{components[1]}:{components[2]}:{components[3]}"
    )
    if not chamber or event_id != canonical or not EVENT_RE.fullmatch(canonical):
        raise MaterializerError(
            f"event_id/event_key mismatch: {event_id!r} != {canonical!r}"
        )
    return canonical, components


def category_value(row: dict[str, Any]) -> str:
    for key in ("category_label", "category", "query_id"):
        category = canonical_category(row.get(key))
        if category:
            return category
    raise MaterializerError(f"unrecognized scoring category: {row!r}")


def direction_sign(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("sign")
    sign = normalize_direction(value)
    return int(sign) if sign in {-1, 0, 1} else None


def load_edge_contract(
    accepted_path: Path, directions_path: Path
) -> tuple[
    set[tuple[str, str]],
    dict[tuple[str, str], dict[str, Any]],
    dict[str, float],
]:
    accepted_rows = read_ndjson(accepted_path)
    if not accepted_rows:
        raise MaterializerError("accepted canonical ledger is empty")
    accepted_edges: set[tuple[str, str]] = set()
    measure_weights: dict[str, float] = {}
    for row in accepted_rows:
        congress, _kind, _number, measure = measure_from_row(row)
        if str(row.get("membership_status") or "").casefold() != "included":
            continue
        if row.get("scoring_category") is not True:
            continue
        expected_measure_weight = measure_weight(measure)
        expected_effective_weight = round(
            DECAY[congress] * expected_measure_weight, 12
        )
        declared_measure_weight = row.get("measure_weight")
        declared_effective_weight = row.get("effective_weight")
        if (
            isinstance(declared_measure_weight, bool)
            or not isinstance(declared_measure_weight, (int, float))
            or float(declared_measure_weight) != expected_measure_weight
        ):
            raise MaterializerError(
                f"accepted row measure_weight is missing or drifted: {measure}"
            )
        if (
            isinstance(declared_effective_weight, bool)
            or not isinstance(declared_effective_weight, (int, float))
            or float(declared_effective_weight) != expected_effective_weight
        ):
            raise MaterializerError(
                f"accepted row effective_weight is missing or drifted: {measure}"
            )
        previous_weight = measure_weights.setdefault(
            measure, expected_measure_weight
        )
        if previous_weight != expected_measure_weight:
            raise MaterializerError(
                f"accepted measure weight is inconsistent across edges: {measure}"
            )
        accepted_edges.add((measure, category_value(row)))
    if not accepted_edges:
        raise MaterializerError(
            "accepted canonical ledger has no included scoring edges"
        )

    directions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in read_ndjson(directions_path):
        _congress, _kind, _number, measure = measure_from_row(row)
        category = category_value(row)
        sign = direction_sign(row.get("direction_sign", row.get("direction")))
        edge = (measure, category)
        if (
            row.get("disposition") != "included"
            or sign not in {-1, 0, 1}
            or edge in directions
        ):
            raise MaterializerError(
                f"direction row is not a unique included explicit sign: {edge}"
            )
        directions[edge] = {
            "sign": sign,
            "meaning": row.get("direction_meaning") or row.get("meaning"),
            "provenance": row.get("direction_provenance"),
            "selected_candidate_id": row.get("selected_candidate_id"),
        }
    if set(directions) != accepted_edges:
        missing = sorted(accepted_edges - set(directions))
        extra = sorted(set(directions) - accepted_edges)
        raise MaterializerError(
            "accepted/direction edge mismatch: "
            f"missing={missing[:10]!r} extra={extra[:10]!r}"
        )
    return accepted_edges, directions, measure_weights


def normalize_position(value: Any) -> tuple[str, int | None]:
    token = re.sub(r"[^a-z]+", "_", str(value or "").casefold()).strip("_")
    if token in {"yea", "yes", "aye"}:
        return token, 1
    if token in {"nay", "no"}:
        return token, -1
    return token or "missing", None


def source_contribution_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    values = []
    raw = row.get("category_contributions")
    if not isinstance(raw, list):
        return ("invalid",)
    for item in raw:
        if not isinstance(item, dict):
            return ("invalid",)
        category = canonical_category(item.get("category"))
        values.append(
            (
                category,
                item.get("disposition"),
                item.get("aligned_value"),
                direction_sign(item.get("direction")),
            )
        )
    return tuple(sorted(values, key=lambda item: str(item)))


def validate_source_contributions(
    row: dict[str, Any],
    expected: dict[str, dict[str, Any]],
    vote_value: int,
) -> bool:
    raw = row.get("category_contributions")
    if not isinstance(raw, list):
        return False
    seen: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            return False
        category = canonical_category(item.get("category"))
        if not category or category in seen:
            return False
        seen[category] = item
    if set(seen) != set(expected):
        return False
    for category, direction in expected.items():
        item = seen[category]
        sign = int(direction["sign"])
        if direction_sign(item.get("direction")) != sign:
            return False
        if sign == 0:
            if (
                item.get("disposition") != "excluded"
                or item.get("aligned_value") is not None
            ):
                return False
        elif (
            item.get("disposition") != "included"
            or item.get("aligned_value") != vote_value * sign
        ):
            return False
    return True


def compact_event_row(row: dict[str, Any]) -> dict[str, Any]:
    action = row.get("congress_action")
    return {
        "event_id": row.get("event_id"),
        "event_key": row.get("event_key"),
        "measure_key": row.get("measure_key"),
        "categories": row.get("categories"),
        "source_disposition": row.get("disposition"),
        "source_reason": row.get("reason"),
        "aggregation_ready": row.get("aggregation_ready"),
        "official_vote_url": row.get("official_vote_url"),
        "congress_action": (
            {
                "date": action.get("date"),
                "text": action.get("text"),
                "type": action.get("type"),
            }
            if isinstance(action, dict)
            else None
        ),
    }


def audit_consumers() -> tuple[dict[str, Any], list[str]]:
    specifications = {
        "legacy_producer": {
            "path": CODE_ROOT / "fec" / "legislation" / "vote_by_category.py",
            "tokens": ("CATLAB", "bill2cat", "ANTI=", "vote_category_lean.json"),
        },
        "moneyball_core": {
            "path": CODE_ROOT / "fec" / "moneyball_core.py",
            "tokens": ("VL['name_of']", "VL['votes']", "d.get('lean'", "d.get('n'"),
        },
        "per_category_stance": {
            "path": CODE_ROOT / "fec" / "stance" / "per_category_stance.py",
            "tokens": ("VC['name_of']", "VC['votes']", "vinfo['n']", "vinfo['lean']"),
        },
    }
    source_audit = {}
    blockers = []
    for name, spec in specifications.items():
        path = spec["path"]
        if not path.is_file():
            blockers.append(f"downstream_source_missing:{name}")
            source_audit[name] = {"path": str(path), "exists": False}
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        missing = [token for token in spec["tokens"] if token not in text]
        if missing and name != "legacy_producer":
            blockers.append(f"downstream_consumer_contract_unrecognized:{name}")
        source_audit[name] = {
            **file_receipt(path),
            "expected_contract_tokens_present": not missing,
            "missing_contract_tokens": missing,
        }
    report = {
        "schema": "rollcall-consumer-compatibility-audit-v1",
        "status": "PASS" if not blockers else "FAIL",
        "staging_only": True,
        "source_audit": source_audit,
        "legacy_projection": {
            "output": "vote_category_lean.json",
            "shape": {
                "name_of": "{bioguide_id: selected_official_display_name}",
                "votes": (
                    "{bioguide_id: {category: {lean: measure_weighted_signed_mean, "
                    "n: raw_recorded_vote_count}}}"
                ),
            },
            "bioguide_is_authoritative_identity": True,
            "n_preserves_existing_raw_count_semantics": True,
            "lean_enforces_bill_1_amendment_0_5_measure_weight": True,
            "decayed_lean_is_diagnostic_only": True,
        },
        "compatible_consumers": {
            "moneyball_core.vote_corroboration": {
                "compatible": True,
                "uses": ["name_of", "votes", "lean", "n>=1"],
                "limitation": "name join is lossy; consumer is not Bioguide-native",
            },
            "per_category_stance": {
                "compatible": True,
                "uses": ["name_of", "votes", "lean", "n>=5"],
                "limitation": (
                    "n remains the raw recorded-vote count; the separately "
                    "named Congress-decay diagnostic is not consumed"
                ),
            },
        },
        "incompatibilities": {
            "collect_official_rollcall_events.aggregate": [
                "emits members/cells rather than the live name_of/votes schema",
                "does not apply Congress decay",
                "does not independently deduplicate member-event votes",
            ],
            "vote_by_category.py": [
                "uses type-collapsing (Congress,chamber,number) measure keys",
                "contains only 17 categories and omits CU Tax Status",
                "hard-codes most category direction signs",
                "does not apply Congress decay",
                "depends on an external manually-cleaned CSV",
                "writes the production aggregate directly",
            ],
            "live_consumers": [
                "join Bioguide votes back to roster names through name_of",
                "do not consume detailed direction, decay, exclusion, or event provenance",
            ],
        },
        "blocking_issues": blockers,
    }
    return report, blockers


def build_outputs(inputs: dict[str, Any]) -> dict[str, Any]:
    accepted_file = inputs["accepted"]["ledger"]["resolved_path"]
    directions_file = inputs["direction"]["outputs"][
        "scoring_directions.ndjson"
    ]["resolved_path"]
    accepted_edges, directions, measure_weights = load_edge_contract(
        accepted_file, directions_file
    )
    directions_by_measure: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (measure, category), decision in sorted(directions.items()):
        directions_by_measure[measure][category] = decision

    roll_outputs = inputs["rollcall"]["outputs"]
    events = read_ndjson(
        roll_outputs["official_rollcall_event_ledger.ndjson"]["resolved_path"]
    )
    member_votes = read_ndjson(
        roll_outputs["official_rollcall_member_votes.ndjson"]["resolved_path"]
    )
    measure_rows = read_ndjson(
        roll_outputs["rollcall_measure_match_audit.ndjson"]["resolved_path"]
    )
    source_aggregate = read_json(
        roll_outputs["official_rollcall_aggregate.json"]["resolved_path"]
    )
    blockers: list[str] = []

    expected_event_counts = inputs["rollcall"]["manifest"].get(
        "event_disposition_counts"
    )
    observed_event_counts = dict(
        sorted(Counter(str(row.get("disposition")) for row in events).items())
    )
    if expected_event_counts != observed_event_counts:
        raise MaterializerError(
            "event ledger counts do not reconcile to the PASS collector manifest"
        )
    expected_vote_counts = inputs["rollcall"]["manifest"].get(
        "member_vote_disposition_counts"
    )
    observed_vote_counts = dict(
        sorted(Counter(str(row.get("disposition")) for row in member_votes).items())
    )
    if expected_vote_counts != observed_vote_counts:
        raise MaterializerError(
            "member-vote counts do not reconcile to the PASS collector manifest"
        )

    event_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    event_meta: dict[int, dict[str, Any]] = {}
    for row in events:
        disposition = str(row.get("disposition") or "")
        if disposition not in {"included", "excluded", "unmatched", "ambiguous"}:
            raise MaterializerError(f"invalid event disposition: {disposition!r}")
        congress, _kind, _number, measure = measure_from_row(row)
        event_id, event_key = parse_event_key(
            row, included=disposition == "included"
        )
        if event_key is not None and event_key[0] != congress:
            raise MaterializerError(
                f"event/measure Congress mismatch: {event_id}/{measure}"
            )
        identity = (event_id, measure)
        event_groups[identity].append(row)
        event_meta[id(row)] = {
            "event_id": event_id,
            "event_key": event_key,
            "measure": measure,
            "congress": congress,
        }

    event_audit: list[dict[str, Any]] = []
    primary_events: dict[tuple[str, str], dict[str, Any]] = {}
    event_ready: dict[tuple[str, str], bool] = {}
    exact_event_duplicates = 0
    conflicting_event_duplicates = 0
    for identity, rows in sorted(event_groups.items()):
        ordered = sorted(rows, key=lambda row: row["_materializer_input_row"])
        signatures = {canonical_json({k: v for k, v in row.items() if not k.startswith("_")}) for row in ordered}
        conflict = len(signatures) > 1
        if conflict:
            conflicting_event_duplicates += len(ordered)
            blockers.append(
                f"conflicting_duplicate_event:{identity[0]}:{identity[1]}"
            )
        primary = ordered[0]
        primary_events[identity] = primary
        event_ready[identity] = not conflict
        for index, row in enumerate(ordered):
            if conflict:
                decision, reason = "ambiguous", "conflicting_duplicate_event"
            elif index:
                exact_event_duplicates += 1
                decision, reason = "excluded", "exact_duplicate_event"
            else:
                decision = str(row.get("disposition"))
                reason = str(row.get("reason") or "")
            event_audit.append(
                {
                    "schema": SCHEMA,
                    "source_row": row["_materializer_input_row"],
                    **compact_event_row(row),
                    "materializer_disposition": decision,
                    "materializer_reason": reason,
                    "type_aware_event_measure_key": (
                        f"{identity[0]}|{identity[1]}"
                    ),
                }
            )

    included_by_event_id: dict[str, set[str]] = defaultdict(set)
    for identity, event in primary_events.items():
        if event.get("disposition") == "included":
            included_by_event_id[identity[0]].add(identity[1])
    for event_id, measures in sorted(included_by_event_id.items()):
        if len(measures) > 1:
            blockers.append(
                f"included_event_matches_multiple_measures:{event_id}:"
                + ",".join(sorted(measures))
            )
            for measure in measures:
                event_ready[(event_id, measure)] = False

    for identity, event in sorted(primary_events.items()):
        measure = identity[1]
        congress = event_meta[id(event)]["congress"]
        if event.get("disposition") != "included":
            event_ready[identity] = False
            continue
        if congress not in DECAY:
            blockers.append(f"unsupported_congress_decay:{measure}")
            event_ready[identity] = False
        expected = directions_by_measure.get(measure, {})
        actual_categories = {
            canonical_category(value) for value in (event.get("categories") or [])
        }
        if None in actual_categories or actual_categories != set(expected):
            blockers.append(f"event_category_set_mismatch:{identity[0]}:{measure}")
            event_ready[identity] = False
        raw_direction_rows = event.get("category_directions")
        actual_directions: dict[str, int] = {}
        if isinstance(raw_direction_rows, list):
            for item in raw_direction_rows:
                if not isinstance(item, dict):
                    continue
                category = canonical_category(item.get("category"))
                sign = direction_sign(item.get("direction"))
                if (
                    category
                    and sign in {-1, 0, 1}
                    and item.get("disposition") == "included"
                    and category not in actual_directions
                ):
                    actual_directions[category] = sign
        expected_signs = {
            category: int(value["sign"]) for category, value in expected.items()
        }
        if actual_directions != expected_signs:
            blockers.append(
                f"event_direction_set_mismatch:{identity[0]}:{measure}"
            )
            event_ready[identity] = False

    accepted_measures = {measure for measure, _category in accepted_edges}
    audited_measures = set()
    measure_audit = []
    for row in measure_rows:
        _congress, _kind, _number, measure = measure_from_row(row)
        audited_measures.add(measure)
        measure_audit.append(
            {
                "schema": SCHEMA,
                "source_row": row["_materializer_input_row"],
                **{key: value for key, value in row.items() if not key.startswith("_")},
                "preserved_for_audit": True,
            }
        )
    if audited_measures != accepted_measures:
        blockers.append(
            "measure_audit_universe_mismatch:"
            f"missing={sorted(accepted_measures-audited_measures)!r}:"
            f"extra={sorted(audited_measures-accepted_measures)!r}"
        )

    vote_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    vote_meta: dict[int, dict[str, Any]] = {}
    identity_names: dict[str, Counter[str]] = defaultdict(Counter)
    missing_identity_rows: list[dict[str, Any]] = []
    for row in member_votes:
        _congress, _kind, _number, measure = measure_from_row(row)
        event_id = str(row.get("event_id") or "").strip().casefold()
        event_identity = (event_id, measure)
        event = primary_events.get(event_identity)
        bioguide = str(row.get("bioguide_id") or "").strip().upper()
        if bioguide and not BIOGUIDE_RE.fullmatch(bioguide):
            blockers.append(
                f"invalid_member_bioguide:{event_id}:{measure}:"
                f"{row['_materializer_input_row']}"
            )
            bioguide = ""
        name = re.sub(r"\s+", " ", str(row.get("member_name") or "")).strip()
        if bioguide and name:
            identity_names[bioguide][name] += 1
        meta = {
            "event_identity": event_identity,
            "event": event,
            "measure": measure,
            "event_id": event_id,
            "bioguide": bioguide or None,
        }
        vote_meta[id(row)] = meta
        if bioguide:
            vote_groups[(event_id, measure, bioguide)].append(row)
        else:
            missing_identity_rows.append(row)

    row_decisions: dict[int, tuple[str, str]] = {}
    exact_vote_duplicates = 0
    conflicting_vote_duplicates = 0
    duplicate_cell_keys: set[tuple[str, str]] = set()
    for key, rows in sorted(vote_groups.items()):
        ordered = sorted(rows, key=lambda row: row["_materializer_input_row"])
        signatures = {
            (
                normalize_position(row.get("position"))[1],
                str(row.get("disposition") or ""),
                source_contribution_signature(row),
            )
            for row in ordered
        }
        if len(signatures) > 1:
            conflicting_vote_duplicates += len(ordered)
            blockers.append(
                f"conflicting_duplicate_member_vote:{key[0]}:{key[1]}:{key[2]}"
            )
            for row in ordered:
                row_decisions[id(row)] = (
                    "ambiguous",
                    "conflicting_duplicate_member_vote",
                )
            continue
        row_decisions[id(ordered[0])] = ("primary", "unique_member_event_vote")
        for row in ordered[1:]:
            exact_vote_duplicates += 1
            row_decisions[id(row)] = ("duplicate", "exact_duplicate_member_event_vote")
            for category, decision in directions_by_measure.get(key[1], {}).items():
                if int(decision["sign"]) != 0:
                    duplicate_cell_keys.add((key[2], category))
    for row in missing_identity_rows:
        row_decisions[id(row)] = (
            str(row.get("disposition") or "unmatched"),
            "member_bioguide_not_available",
        )

    contributions: list[dict[str, Any]] = []
    member_audit: list[dict[str, Any]] = []
    cell_accumulator: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(
        member_votes,
        key=lambda value: (
            str(value.get("event_id") or ""),
            str(value.get("bioguide_id") or ""),
            value["_materializer_input_row"],
        ),
    ):
        meta = vote_meta[id(row)]
        event_identity = meta["event_identity"]
        event = meta["event"]
        measure = meta["measure"]
        bioguide = meta["bioguide"]
        source_disposition = str(row.get("disposition") or "")
        decision, decision_reason = row_decisions[id(row)]
        position_token, vote_value = normalize_position(row.get("position"))
        congress = parse_measure_key(measure)[0]
        measure_factor = measure_weights.get(measure)
        expected = directions_by_measure.get(measure, {})
        source_integrity = True
        if (
            decision == "primary"
            and event is not None
            and event_ready.get(event_identity, False)
            and vote_value is not None
            and bioguide is not None
        ):
            source_integrity = validate_source_contributions(
                row, expected, vote_value
            )
            if not source_integrity:
                blockers.append(
                    f"member_contribution_contract_mismatch:"
                    f"{event_identity[0]}:{measure}:{bioguide}"
                )
        audit_dispositions = []
        for category, direction in sorted(expected.items()):
            sign = int(direction["sign"])
            decay = DECAY.get(congress)
            aligned = None
            measure_weighted = None
            weighted = None
            if event is None:
                disposition, reason = "unmatched", "member_vote_has_no_event"
                blockers.append(
                    f"member_vote_has_no_event:{event_identity[0]}:{measure}:"
                    f"{row['_materializer_input_row']}"
                )
            elif decision == "duplicate":
                disposition, reason = "excluded", decision_reason
            elif decision == "ambiguous":
                disposition, reason = "ambiguous", decision_reason
            elif event.get("disposition") != "included":
                disposition = str(event.get("disposition") or "excluded")
                reason = "event_not_included:" + str(event.get("reason") or "")
            elif not event_ready.get(event_identity, False):
                disposition, reason = "ambiguous", "event_not_aggregation_ready"
            elif not bioguide:
                disposition = (
                    source_disposition
                    if source_disposition in {"unmatched", "ambiguous"}
                    else "unmatched"
                )
                reason = "member_bioguide_not_available"
                blockers.append(
                    f"included_event_member_identity_unresolved:"
                    f"{event_identity[0]}:{measure}:"
                    f"{row['_materializer_input_row']}"
                )
            elif vote_value is None:
                disposition, reason = "excluded", "abstain_or_missing_vote"
            elif not source_integrity:
                disposition, reason = (
                    "ambiguous",
                    "collector_contribution_contract_mismatch",
                )
            elif sign == 0:
                disposition, reason = "excluded", "explicit_neutral_direction"
            elif decay is None:
                disposition, reason = "ambiguous", "unsupported_congress_decay"
            elif measure_factor is None:
                disposition, reason = "ambiguous", "missing_bound_measure_weight"
            else:
                disposition, reason = (
                    "included",
                    (
                        "official_vote_explicit_direction_with_congress_decay_"
                        "and_measure_weight"
                    ),
                )
                aligned = vote_value * sign
                effective_weight = decay * measure_factor
                measure_weighted = aligned * measure_factor
                weighted = aligned * effective_weight
                cell_key = (bioguide, category)
                cell = cell_accumulator.setdefault(
                    cell_key,
                    {
                        "bioguide_id": bioguide,
                        "category": category,
                        "raw_aligned_sum": 0,
                        "measure_weighted_aligned_sum": 0.0,
                        "weighted_aligned_sum": 0.0,
                        "raw_recorded_vote_count": 0,
                        "measure_weight_sum": 0.0,
                        "weight_sum": 0.0,
                        "congress_counts": Counter(),
                        "events": [],
                    },
                )
                cell["raw_aligned_sum"] += aligned
                cell["measure_weighted_aligned_sum"] += measure_weighted
                cell["weighted_aligned_sum"] += weighted
                cell["raw_recorded_vote_count"] += 1
                cell["measure_weight_sum"] += measure_factor
                cell["weight_sum"] += effective_weight
                cell["congress_counts"][congress] += 1
                cell["events"].append(
                    {
                        "event_id": event_identity[0],
                        "measure_key": measure,
                        "congress": congress,
                        "position": row.get("position"),
                        "vote_value": vote_value,
                        "direction_sign": sign,
                        "aligned_value": aligned,
                        "decay_weight": decay,
                        "measure_weight": measure_factor,
                        "effective_weight": round(effective_weight, 12),
                        "measure_weighted_aligned_value": round(
                            measure_weighted, 12
                        ),
                        "weighted_aligned_value": round(weighted, 12),
                    }
                )
            audit_dispositions.append(disposition)
            contributions.append(
                {
                    "schema": CONTRIBUTION_SCHEMA,
                    "source_member_vote_row": row["_materializer_input_row"],
                    "type_aware_event_measure_key": (
                        f"{event_identity[0]}|{measure}"
                    ),
                    "event_id": event_identity[0],
                    "event_key": event.get("event_key") if event else None,
                    "measure_key": measure,
                    "congress": congress,
                    "category": category,
                    "bioguide_id": bioguide,
                    "member_name": row.get("member_name"),
                    "position": row.get("position"),
                    "normalized_position": position_token,
                    "vote_value": vote_value,
                    "direction_sign": sign,
                    "direction_meaning": direction.get("meaning"),
                    "decay_weight": decay,
                    "measure_weight": measure_factor,
                    "effective_weight": (
                        round(decay * measure_factor, 12)
                        if decay is not None and measure_factor is not None
                        else None
                    ),
                    "aligned_value": aligned,
                    "measure_weighted_aligned_value": (
                        round(measure_weighted, 12)
                        if measure_weighted is not None
                        else None
                    ),
                    "weighted_aligned_value": (
                        round(weighted, 12) if weighted is not None else None
                    ),
                    "disposition": disposition,
                    "reason": reason,
                    "source_event_disposition": (
                        event.get("disposition") if event else None
                    ),
                    "source_member_vote_disposition": source_disposition,
                    "source_bioguide_resolution_status": (
                        (row.get("bioguide_resolution") or {}).get("status")
                        if isinstance(row.get("bioguide_resolution"), dict)
                        else None
                    ),
                }
            )
        if any(value == "included" for value in audit_dispositions):
            materializer_disposition = "included"
        elif any(value == "ambiguous" for value in audit_dispositions):
            materializer_disposition = "ambiguous"
        elif any(value == "unmatched" for value in audit_dispositions):
            materializer_disposition = "unmatched"
        else:
            materializer_disposition = "excluded"
        member_audit.append(
            {
                "schema": SCHEMA,
                "source_row": row["_materializer_input_row"],
                "event_id": event_identity[0],
                "measure_key": measure,
                "bioguide_id": bioguide,
                "member_name": row.get("member_name"),
                "position": row.get("position"),
                "source_disposition": source_disposition,
                "source_reason": row.get("reason"),
                "source_bioguide_resolution_status": (
                    (row.get("bioguide_resolution") or {}).get("status")
                    if isinstance(row.get("bioguide_resolution"), dict)
                    else None
                ),
                "deduplication_decision": decision,
                "deduplication_reason": decision_reason,
                "materializer_disposition": materializer_disposition,
                "category_decision_counts": dict(
                    sorted(Counter(audit_dispositions).items())
                ),
            }
        )

    identity_audit = []
    name_of = {}
    name_variant_conflicts = 0
    for bioguide, candidates in sorted(identity_names.items()):
        ordered = sorted(
            candidates.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )
        selected = ordered[0][0] if ordered else None
        if len(ordered) > 1:
            name_variant_conflicts += 1
        identity_audit.append(
            {
                "schema": SCHEMA,
                "bioguide_id": bioguide,
                "selected_display_name": selected,
                "selection_rule": "most_frequent_then_casefold_lexical",
                "name_variant_conflict": len(ordered) > 1,
                "name_candidates": [
                    {"name": name, "row_count": count}
                    for name, count in ordered
                ],
                "authoritative_identity": "bioguide_id",
            }
        )
        if selected:
            name_of[bioguide] = selected

    cell_rows = []
    legacy_votes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (bioguide, category), cell in sorted(cell_accumulator.items()):
        if bioguide not in name_of:
            blockers.append(f"scored_bioguide_has_no_display_name:{bioguide}")
        events_for_cell = sorted(
            cell["events"],
            key=lambda item: (
                item["event_id"],
                item["measure_key"],
                item["position"] or "",
            ),
        )
        measure_weight_sum = round(cell["measure_weight_sum"], 12)
        measure_weighted_sum = round(
            cell["measure_weighted_aligned_sum"], 12
        )
        weight_sum = round(cell["weight_sum"], 12)
        weighted_sum = round(cell["weighted_aligned_sum"], 12)
        lean = round(measure_weighted_sum / measure_weight_sum, 12)
        decayed_lean = round(weighted_sum / weight_sum, 12)
        row = {
            "schema": CELL_SCHEMA,
            "bioguide_id": bioguide,
            "selected_display_name": name_of.get(bioguide),
            "category": category,
            "lean": lean,
            "decayed_lean_diagnostic": decayed_lean,
            "n": cell["raw_recorded_vote_count"],
            "raw_recorded_vote_count": cell["raw_recorded_vote_count"],
            "raw_aligned_sum": cell["raw_aligned_sum"],
            "measure_weight_sum": measure_weight_sum,
            "measure_weighted_aligned_sum": measure_weighted_sum,
            "weight_sum": weight_sum,
            "weighted_aligned_sum": weighted_sum,
            "congress_counts": {
                str(key): value
                for key, value in sorted(cell["congress_counts"].items())
            },
            "event_ids": sorted({item["event_id"] for item in events_for_cell}),
            "measure_keys": sorted(
                {item["measure_key"] for item in events_for_cell}
            ),
            "events": events_for_cell,
        }
        cell_rows.append(row)
        legacy_votes[bioguide][category] = {"lean": lean, "n": row["n"]}

    source_cells = {}
    duplicate_source_cells = []
    for row in source_aggregate.get("cells") or []:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("bioguide_id") or ""), str(row.get("category") or ""))
        if key in source_cells:
            duplicate_source_cells.append(key)
        source_cells[key] = {
            "aligned_sum": row.get("aligned_sum"),
            "n": row.get("recorded_vote_count"),
        }
    recomputed_unweighted = {
        (row["bioguide_id"], row["category"]): {
            "aligned_sum": row["raw_aligned_sum"],
            "n": row["raw_recorded_vote_count"],
        }
        for row in cell_rows
    }
    comparison_keys = set(source_cells) | set(recomputed_unweighted)
    mismatched_cells = sorted(
        key
        for key in comparison_keys
        if source_cells.get(key) != recomputed_unweighted.get(key)
    )
    unexplained_mismatches = sorted(set(mismatched_cells) - duplicate_cell_keys)
    if duplicate_source_cells:
        blockers.append("collector_aggregate_has_duplicate_cells")
    if unexplained_mismatches:
        blockers.append(
            f"collector_aggregate_recompute_mismatch:"
            f"{len(unexplained_mismatches)}_cells"
        )

    compatibility, compatibility_blockers = audit_consumers()
    blockers.extend(compatibility_blockers)
    unique_blockers = sorted(set(blockers))
    contributions.sort(
        key=lambda row: (
            row["event_id"],
            row["measure_key"],
            row["bioguide_id"] or "",
            row["category"],
            row["source_member_vote_row"],
        )
    )
    event_audit.sort(
        key=lambda row: (
            str(row["event_id"]),
            str((row.get("measure_key") or {}).get("canonical_key") or ""),
            row["source_row"],
        )
    )
    member_audit.sort(
        key=lambda row: (
            row["event_id"],
            row["measure_key"],
            row["bioguide_id"] or "",
            row["source_row"],
        )
    )
    measure_audit.sort(
        key=lambda row: (
            str(row.get("canonical_key") or ""),
            str(row.get("category") or ""),
            row["source_row"],
        )
    )
    return {
        "legacy": {
            "name_of": {key: name_of[key] for key in sorted(name_of)},
            "votes": {
                bioguide: {
                    category: legacy_votes[bioguide][category]
                    for category in sorted(legacy_votes[bioguide])
                }
                for bioguide in sorted(legacy_votes)
            },
        },
        "cells": cell_rows,
        "contributions": contributions,
        "events": event_audit,
        "members": member_audit,
        "measures": measure_audit,
        "identities": identity_audit,
        "compatibility": compatibility,
        "blockers": unique_blockers,
        "counts": {
            "accepted_measure_category_edges": len(accepted_edges),
            "accepted_measures": len(accepted_measures),
            "source_event_rows": len(events),
            "source_member_vote_rows": len(member_votes),
            "source_measure_audit_rows": len(measure_rows),
            "event_decision_rows": len(event_audit),
            "member_vote_decision_rows": len(member_audit),
            "measure_decision_rows": len(measure_audit),
            "category_contribution_rows": len(contributions),
            "included_category_contributions": sum(
                row["disposition"] == "included" for row in contributions
            ),
            "category_cells": len(cell_rows),
            "members_with_scored_votes": len(legacy_votes),
            "exact_event_duplicates_excluded": exact_event_duplicates,
            "conflicting_event_duplicate_rows": conflicting_event_duplicates,
            "exact_member_event_vote_duplicates_excluded": exact_vote_duplicates,
            "conflicting_member_event_vote_rows": conflicting_vote_duplicates,
            "abstain_or_missing_contributions_excluded": sum(
                row["reason"] == "abstain_or_missing_vote"
                for row in contributions
            ),
            "explicit_neutral_contributions_excluded": sum(
                row["reason"] == "explicit_neutral_direction"
                for row in contributions
            ),
            "excluded_event_contributions_preserved": sum(
                str(row["reason"]).startswith("event_not_included:")
                for row in contributions
            ),
            "unmatched_contributions_preserved": sum(
                row["disposition"] == "unmatched" for row in contributions
            ),
            "unmatched_or_ambiguous_identity_rows_preserved": sum(
                row.get("source_bioguide_resolution_status")
                in {"unmatched", "ambiguous"}
                for row in member_audit
            ),
            "member_name_variant_conflicts": name_variant_conflicts,
        },
        "collector_aggregate_comparison": {
            "basis": (
                "unweighted official collector cells versus independently "
                "recomputed, duplicate-deduplicated raw cells"
            ),
            "source_cell_count": len(source_cells),
            "recomputed_cell_count": len(recomputed_unweighted),
            "mismatched_cell_count": len(mismatched_cells),
            "mismatched_cells": [
                {"bioguide_id": key[0], "category": key[1]}
                for key in mismatched_cells
            ],
            "mismatches_explained_by_duplicate_deduplication": [
                {"bioguide_id": key[0], "category": key[1]}
                for key in sorted(set(mismatched_cells) & duplicate_cell_keys)
            ],
            "unexplained_mismatch_count": len(unexplained_mismatches),
            "unexplained_mismatches": [
                {"bioguide_id": key[0], "category": key[1]}
                for key in unexplained_mismatches
            ],
            "compatible": not mismatched_cells,
        },
    }


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_output_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    audit_root = (CODE_ROOT / "audits" / "legfix5").resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved in {audit_root, temp_root}:
        raise MaterializerError("output must be a child staging directory")
    if not (
        is_relative_to(resolved, audit_root)
        or is_relative_to(resolved, temp_root)
    ):
        raise MaterializerError(
            "output is restricted to audits/legfix5 or a temporary directory"
        )
    return resolved


def install_idempotently(temp: Path, out: Path, names: Sequence[str]) -> bool:
    targets = [out / name for name in names]
    existing = [path.exists() for path in targets]
    if any(existing):
        if not all(existing):
            raise MaterializerError(
                "partial prior output exists; refusing a mixed generation"
            )
        mismatched = [
            path.name
            for path in targets
            if path.read_bytes() != (temp / path.name).read_bytes()
        ]
        if mismatched:
            raise MaterializerError(
                "immutable staged output differs from requested generation: "
                + ", ".join(mismatched)
            )
        return True
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        os.replace(temp / name, out / name)
    return False


def materialize(
    *,
    rollcall: Path,
    accepted_generation: Path,
    direction: Path,
    out: Path,
) -> dict[str, Any]:
    out = validate_output_path(out)
    inputs = validate_inputs(
        rollcall_value=rollcall,
        accepted_value=accepted_generation,
        direction_value=direction,
    )
    built = build_outputs(inputs)
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(
        tempfile.mkdtemp(prefix=f".{out.name}.rollcall-", dir=str(out.parent))
    )
    try:
        write_json(temp / "vote_category_lean.json", built["legacy"])
        write_ndjson(
            temp / "official_rollcall_category_cells.ndjson", built["cells"]
        )
        write_ndjson(
            temp / "official_rollcall_category_contributions.ndjson",
            built["contributions"],
        )
        write_ndjson(
            temp / "official_rollcall_event_decisions.ndjson", built["events"]
        )
        write_ndjson(
            temp / "official_rollcall_member_vote_decisions.ndjson",
            built["members"],
        )
        write_ndjson(
            temp / "official_rollcall_measure_decisions.ndjson",
            built["measures"],
        )
        write_ndjson(
            temp / "official_rollcall_member_identity_audit.ndjson",
            built["identities"],
        )
        write_json(
            temp / "rollcall_consumer_compatibility.json",
            built["compatibility"],
        )
        outputs = {
            name: file_receipt(temp / name, relative_to=temp)
            for name in OUTPUT_NAMES
        }
        generation_basis = {
            "accepted_manifest_sha256": inputs["accepted"]["manifest_receipt"][
                "sha256"
            ],
            "accepted_ledger_sha256": inputs["accepted"]["ledger"]["sha256"],
            "direction_manifest_sha256": inputs["direction"]["manifest_receipt"][
                "sha256"
            ],
            "direction_output_hashes": sorted(
                item["sha256"] for item in inputs["direction"]["outputs"].values()
            ),
            "rollcall_manifest_sha256": inputs["rollcall"]["manifest_receipt"][
                "sha256"
            ],
            "rollcall_output_hashes": sorted(
                item["sha256"] for item in inputs["rollcall"]["outputs"].values()
            ),
            "outputs": outputs,
            "decay": DECAY,
        }
        manifest = {
            "schema": SCHEMA,
            "status": "PASS" if not built["blockers"] else "FAIL",
            "acceptance_passed": not built["blockers"],
            "generated_at": None,
            "generation_id": sha256_bytes(
                canonical_json(generation_basis).encode("utf-8")
            ),
            "staging_only": True,
            "production_written": False,
            "workbook_written": False,
            "output_path_policy": "audits/legfix5_or_operating_system_temp_only",
            "identity_policy": {
                "authoritative_member_identity": "Bioguide ID",
                "legacy_name_of_is_compatibility_only": True,
                "missing_bioguide_is_never_scored": True,
                "member_name_selection": "most_frequent_then_casefold_lexical",
            },
            "measure_key_policy": "congress:measure_type:number",
            "event_key_policy": "congress:chamber:session:roll",
            "event_measure_join_key": "(event_key,type_aware_measure_key)",
            "direction_policy": {
                "source": "PASS scoring_directions.ndjson",
                "explicit_values": [-1, 0, 1],
                "neutral_zero_is_visible_and_unscored": True,
            },
            "decay_policy": {
                "name": (
                    "diagnostic_only_aggressive_congress_decay_"
                    "1_0.66_0.33_0.16"
                ),
                "weights": {
                    str(congress): value
                    for congress, value in sorted(DECAY.items(), reverse=True)
                },
                "measure_weight_policy": measure_weight_policy(),
                "canonical_legacy_lean": (
                    "sum(aligned*measure_weight)/sum(measure_weight)"
                ),
                "decayed_lean_diagnostic": (
                    "sum(aligned*measure_weight*decay)/"
                    "sum(measure_weight*decay)"
                ),
                "n": "raw deduplicated directional recorded-vote count",
                "policy_mismatch": (
                    "Congress decay is canonical for legislation relationships "
                    "but no existing roll-call consumer applies it; the "
                    "bill/amendment measure factor is enforced in canonical "
                    "lean while Congress decay remains diagnostic"
                ),
                "diagnostic_is_not_consumed_downstream": True,
            },
            "missing_abstain_policy": {
                "present_not_voting_absent_or_blank": (
                    "visible excluded contribution; never zero-imputed"
                ),
                "members_absent_from_official_XML": (
                    "not observed and not fabricated"
                ),
            },
            "deduplication_policy": {
                "event_identity": "(event_key,type_aware_measure_key)",
                "member_vote_identity": (
                    "(event_key,type_aware_measure_key,bioguide_id)"
                ),
                "exact_duplicates": "one primary scored; all duplicates visible",
                "conflicting_duplicates": "all excluded as ambiguous and block PASS",
            },
            "inputs": {
                "accepted_generation_manifest": inputs["accepted"][
                    "manifest_receipt"
                ],
                "accepted_canonical_ledger": {
                    key: value
                    for key, value in inputs["accepted"]["ledger"].items()
                    if key != "resolved_path"
                },
                "direction_manifest": inputs["direction"]["manifest_receipt"],
                "direction_outputs": {
                    name: {
                        key: value
                        for key, value in item.items()
                        if key != "resolved_path"
                    }
                    for name, item in inputs["direction"]["outputs"].items()
                },
                "rollcall_manifest": inputs["rollcall"]["manifest_receipt"],
                "rollcall_outputs": {
                    name: {
                        key: value
                        for key, value in item.items()
                        if key != "resolved_path"
                    }
                    for name, item in inputs["rollcall"]["outputs"].items()
                },
                "hash_binding_passed": True,
            },
            "counts": built["counts"],
            "collector_aggregate_comparison": built[
                "collector_aggregate_comparison"
            ],
            "decision_reconciliation": {
                "all_event_rows_preserved": (
                    built["counts"]["source_event_rows"]
                    == built["counts"]["event_decision_rows"]
                ),
                "all_member_vote_rows_preserved": (
                    built["counts"]["source_member_vote_rows"]
                    == built["counts"]["member_vote_decision_rows"]
                ),
                "all_measure_audit_rows_preserved": (
                    built["counts"]["source_measure_audit_rows"]
                    == built["counts"]["measure_decision_rows"]
                ),
                "excluded_unmatched_and_ambiguous_rows_are_outputs": True,
            },
            "consumer_compatibility": {
                "audit": outputs["rollcall_consumer_compatibility.json"],
                "legacy_schema_projection_written": True,
                "known_incompatibilities_are_not_silently_coerced": True,
            },
            "blocking_issue_count": len(built["blockers"]),
            "blocking_issues": built["blockers"],
            "outputs": outputs,
        }
        write_json(temp / MANIFEST_NAME, manifest)
        reused = install_idempotently(
            temp, out, (*OUTPUT_NAMES, MANIFEST_NAME)
        )
        result = read_json(out / MANIFEST_NAME)
        result["_idempotent_reuse"] = reused
        return result
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = materialize(
            rollcall=args.rollcall,
            accepted_generation=args.accepted_generation,
            direction=args.direction,
            out=args.out,
        )
    except MaterializerError as exc:
        print(
            f"materialize_legislation_rollcall_aggregate: FAIL CLOSED: {exc}",
            file=sys.stderr,
        )
        return 2
    print(
        "official roll-call aggregate: "
        f"{manifest['status']} | cells={manifest['counts']['category_cells']} | "
        f"contributions={manifest['counts']['category_contribution_rows']} | "
        f"idempotent_reuse={manifest.pop('_idempotent_reuse', False)}"
    )
    print(args.out.resolve() / MANIFEST_NAME)
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
