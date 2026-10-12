# -*- coding: utf-8 -*-
"""Build a fail-closed, audit-only measure/category direction ledger.

The accepted canonical ledger defines the complete set of type-aware
measure/category edges.  Direction evidence is read from that ledger and,
*only when explicitly supplied*, from a legacy bill-direction registry,
archive stance audit, or trusted-press cross-reference.  No source is searched
implicitly and article absence is never evidence.

Only explicit, nonconflicting, provenance-complete decisions are written to
``scoring_directions.ndjson``.  Every candidate and every missing, conflicting,
weak, semantically invalid, or unmatched item remains visible in the audit
outputs and causes ``acceptance_passed`` to be false where applicable.

The scoring output uses the exact fields consumed by
``collect_official_rollcall_events.py``:

* ``canonical_key``, ``congress``, ``measure_type``, ``number``, ``category``;
* numeric ``direction`` / ``direction_sign`` in ``{-1, 0, 1}``; and
* ``direction_provenance`` with ``source_url`` or ``source_reference`` plus a
  substantive ``rationale``.

CFPB subtypes are not collapsed into a generic sign.  ``reduce_authority`` is
industry-aligned ``+1``, ``expand_authority`` is ``-1``, and ``neutral`` is
``0``.  A missing or inconsistent CFPB subtype blocks the edge.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Sequence
import urllib.parse

from legislation.collect_official_rollcall_events import (
    ACCEPTED_DISPOSITIONS,
    MEASURE_TYPES,
    MeasureKey,
    canonical_category,
    category_from_row,
    measure_key_from_row,
    normalize_space,
    normalize_token,
    parse_canonical_measure_key,
    read_records,
)


HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
AUDIT_ROOT = (ROOT / "audits" / "legfix5").resolve()
REPO_TEMP_ROOT = (ROOT / "tmp").resolve()

MANIFEST_SCHEMA = "legislation-direction-ledger-manifest-v1"
CANDIDATE_SCHEMA = "legislation-direction-candidate-v1"
AUDIT_SCHEMA = "legislation-direction-edge-audit-v1"
DECISION_SCHEMA = "legislation-direction-decision-v1"
UNMATCHED_SCHEMA = "legislation-direction-unmatched-evidence-v1"

SOURCE_PRIORITY = {
    "accepted_ledger": 0,
    "archive_stance_evidence": 1,
    "bill_directions": 2,
    "trusted_press_cross_reference": 3,
}
SIGN_MEANING = {
    1: "yea_is_industry_aligned",
    -1: "nay_is_industry_aligned",
    0: "neutral_unscored",
}
CFPB_SIGN = {
    "reduce_authority": 1,
    "expand_authority": -1,
    "neutral": 0,
}
CFPB_CATEGORY_SUBTYPE = {
    "cfpb:reduce": "reduce_authority",
    "cfpb:expand": "expand_authority",
    "cfpb:neutral": "neutral",
}
CFPB_SUBTYPE_ALIASES = {
    "reduce": "reduce_authority",
    "restrain": "reduce_authority",
    "reform": "reduce_authority",
    "reduce_authority": "reduce_authority",
    "restrain_authority": "reduce_authority",
    "expand": "expand_authority",
    "increase": "expand_authority",
    "expand_authority": "expand_authority",
    "increase_authority": "expand_authority",
    "neutral": "neutral",
    "no_direction": "neutral",
}
PRODUCTION_NAMES = frozenset(
    {
        "data/legislation/bill_directions.csv",
        "data/legislation/cfpb_bill_directions.csv",
        "vote_category_lean.json",
        "archive_leg.json",
        "category_stance.json",
        "ranks_python.json",
    }
)
FORMAL_MEASURE_RE = re.compile(
    r"^\s*(?P<prefix>"
    r"H\.?\s*R\.?|S\.?|"
    r"H\.?\s*J\.?\s*Res\.?|S\.?\s*J\.?\s*Res\.?|"
    r"H\.?\s*Con\.?\s*Res\.?|S\.?\s*Con\.?\s*Res\.?|"
    r"H\.?\s*Res\.?|S\.?\s*Res\.?|"
    r"H\.?\s*Amdt\.?|S\.?\s*Amdt\.?"
    r")\s*(?P<number>\d+)\s*$",
    re.IGNORECASE,
)


class DirectionError(ValueError):
    """Input or output violates the staged direction contract."""


@dataclass(frozen=True, order=True)
class Edge:
    measure: MeasureKey
    category: str

    @property
    def key(self) -> str:
        return f"{self.measure.canonical}|{self.category}"

    def to_dict(self) -> dict[str, Any]:
        return {**self.measure.to_dict(), "category": self.category}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Build an explicit staged measure/category direction ledger."
    )
    result.add_argument(
        "--accepted",
        required=True,
        type=Path,
        help="accepted canonical measure/category ledger",
    )
    result.add_argument(
        "--out",
        required=True,
        type=Path,
        help="audit/temp output directory; production paths are refused",
    )
    result.add_argument(
        "--bill-directions",
        type=Path,
        help="optional explicit legacy bill_directions.csv-style evidence",
    )
    result.add_argument(
        "--archive-stance-evidence",
        type=Path,
        help="optional explicit JSON/NDJSON/CSV archive stance evidence",
    )
    result.add_argument(
        "--press-cross-reference",
        type=Path,
        help="optional explicit trusted-press cross-reference evidence",
    )
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "sha256": sha256_file(path),
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def allowed_output_roots() -> tuple[Path, ...]:
    roots = {AUDIT_ROOT, REPO_TEMP_ROOT, Path(tempfile.gettempdir()).resolve()}
    for name in ("TEMP", "TMP", "TMPDIR"):
        if os.environ.get(name):
            roots.add(Path(os.environ[name]).resolve())
    return tuple(sorted(roots, key=str))


def validate_out(raw: Path) -> Path:
    out = raw.resolve()
    if out.suffix:
        raise DirectionError("--out must be a directory, not a file")
    if out.name.casefold() in PRODUCTION_NAMES:
        raise DirectionError(f"production output name is prohibited: {out.name}")
    if not any(_is_relative_to(out, root) for root in allowed_output_roots()):
        raise DirectionError(
            "--out must resolve under audits/legfix5 or a temp directory; "
            f"received {out}"
        )
    return out


def clean_url(value: Any) -> str | None:
    text = normalize_space(value)
    if not text:
        return None
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    query = [
        (key, item)
        for key, item in urllib.parse.parse_qsl(
            parts.query, keep_blank_values=True
        )
        if key.casefold() not in {"api_key", "key", "token"}
    ]
    return urllib.parse.urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path,
            urllib.parse.urlencode(query),
            parts.fragment,
        )
    )


def _field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def read_evidence_records(path: Path) -> list[dict[str, Any]]:
    """Read optional evidence, including legacy audit objects with ``rows``."""
    if path.suffix.casefold() != ".json":
        return read_records(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise DirectionError(f"invalid JSON in {path}: {exc}") from exc
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = None
        for field in (
            "rows",
            "records",
            "directions",
            "matches",
            "membership_evidence",
            "measures",
        ):
            value = payload.get(field)
            if isinstance(value, list):
                rows = value
                break
        if rows is None:
            rows = [payload]
    else:
        raise DirectionError(f"unsupported JSON evidence root in {path}")
    if not all(isinstance(row, dict) for row in rows):
        raise DirectionError(f"non-object evidence record in {path}")
    return [
        {**row, "_input_line": index}
        for index, row in enumerate(rows, start=1)
    ]


def parse_sign(value: Any) -> int | None:
    """Parse only explicit industry-aligned signs; never infer an action sign."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number in {-1.0, 0.0, 1.0}:
            return int(number)
        return None
    text = str(value).strip()
    if text in {"-1", "0", "1", "+1"}:
        return int(text)
    token = normalize_token(text)
    if token in {
        "pro",
        "support",
        "cu_support",
        "industry_support",
        "pro_industry",
        "yea_is_pro",
        "yes_is_pro",
        "yea_is_industry_aligned",
    }:
        return 1
    if token in {
        "anti",
        "oppose",
        "cu_oppose",
        "industry_oppose",
        "anti_industry",
        "nay_is_pro",
        "no_is_pro",
        "nay_is_industry_aligned",
    }:
        return -1
    if token in {"neutral", "unscored", "neutral_unscored", "no_direction"}:
        return 0
    return None


def parse_formal_measure(value: Any, congress: Any) -> MeasureKey | None:
    try:
        congress_number = int(str(congress).strip())
    except (TypeError, ValueError):
        return None
    match = FORMAL_MEASURE_RE.fullmatch(str(value or ""))
    if not match:
        return None
    token = re.sub(r"[^a-z]", "", match.group("prefix").casefold())
    aliases = {
        "hr": "hr",
        "s": "s",
        "hjres": "hjres",
        "sjres": "sjres",
        "hconres": "hconres",
        "sconres": "sconres",
        "hres": "hres",
        "sres": "sres",
        "hamdt": "hamdt",
        "samdt": "samdt",
    }
    measure_type = aliases.get(token)
    number = int(match.group("number"))
    if (
        measure_type not in MEASURE_TYPES
        or congress_number <= 0
        or number <= 0
    ):
        return None
    return MeasureKey(congress_number, measure_type, number)


def measure_from_evidence(row: dict[str, Any]) -> MeasureKey | None:
    for value in (
        row.get("canonical_key"),
        row.get("measure_key")
        if isinstance(row.get("measure_key"), str)
        else None,
        (row.get("measure_key") or {}).get("canonical_key")
        if isinstance(row.get("measure_key"), dict)
        else None,
    ):
        if value:
            try:
                return parse_canonical_measure_key(value)
            except Exception:
                return None
    try:
        return measure_key_from_row(row)
    except Exception:
        pass
    return parse_formal_measure(
        _field(row, "Bill", "bill", "measure", "display_number", "Name"),
        _field(row, "Congress", "congress"),
    )


def category_and_subtype(
    row: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    raw = _field(
        row,
        "category",
        "Category",
        "category_label",
        "scoring_category_name",
        "effective_category",
        "source_section",
        "query_id",
    )
    raw_text = normalize_space(raw)
    category_subtype = CFPB_CATEGORY_SUBTYPE.get(raw_text.casefold())
    if category_subtype:
        return "CFPB", category_subtype, raw_text
    category = canonical_category(raw)
    if not category:
        return None, None, raw_text or None
    subtype_raw = _field(
        row,
        "cfpb_subtype",
        "direction_subtype",
        "policy_subtype",
        "cfpb_direction",
    )
    subtype = (
        CFPB_SUBTYPE_ALIASES.get(normalize_token(subtype_raw))
        if subtype_raw is not None
        else None
    )
    return category, subtype, raw_text or None


def provenance_from_row(
    row: dict[str, Any], source_kind: str
) -> dict[str, Any]:
    nested = row.get("direction_provenance")
    if not isinstance(nested, dict):
        nested = {}
    source_url = clean_url(
        _field(
            nested,
            "source_url",
            "url",
        )
        or _field(
            row,
            "direction_source_url",
            "official_direction_source_url",
            "official_text_url",
            "congress_gov_url",
            "article_url",
            "url",
        )
    )
    source_reference = normalize_space(
        _field(nested, "source_reference", "reference")
        or _field(
            row,
            "direction_source_reference",
            "direction_source",
            "source_reference",
            "direction_mechanism",
        )
    )
    if not source_reference and source_kind == "bill_directions":
        source_reference = normalize_space(row.get("Note"))
    rationale = normalize_space(
        _field(nested, "rationale", "reason")
        or _field(
            row,
            "direction_reason",
            "direction_rationale",
            "Rationale",
            "Reason",
            "override_reason",
        )
    )
    return {
        "source_url": source_url,
        "source_reference": source_reference or None,
        "rationale": rationale or None,
        "provenance_complete": bool(
            (source_url or source_reference) and rationale
        ),
    }


def candidate_id(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"candidate_id"}
    }
    encoded = json.dumps(
        stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_candidate(
    row: dict[str, Any],
    source_kind: str,
    source_path: Path,
    accepted_edges: set[Edge],
) -> tuple[dict[str, Any], Edge | None]:
    measure = measure_from_evidence(row)
    category, cfpb_subtype, raw_category = category_and_subtype(row)
    edge = (
        Edge(measure, category)
        if measure is not None and category is not None
        else None
    )
    raw_direction = _field(
        row,
        "direction",
        "Direction",
        "direction_sign",
        "cu_direction",
        "pro_industry_sign",
        "aligned_yea_sign",
        "sign",
    )
    sign = parse_sign(raw_direction)
    has_direction_field = raw_direction not in (None, "")
    provenance = provenance_from_row(row, source_kind)
    line_number = int(row.get("_input_line") or 0)
    reason: str
    status: str
    blocking = False
    if measure is None:
        status, reason, blocking = (
            "unmatched",
            "missing_or_invalid_type_aware_measure_identity",
            True,
        )
    elif category is None:
        status, reason, blocking = (
            "unmatched",
            "missing_or_unrecognized_explicit_category",
            True,
        )
    elif edge not in accepted_edges:
        status, reason, blocking = (
            "unmatched",
            "evidence_references_unaccepted_measure_category_edge",
            True,
        )
    elif not has_direction_field:
        status, reason = "corroboration_only", "no_explicit_direction"
    elif sign is None:
        status, reason, blocking = (
            "invalid",
            "invalid_or_nonexplicit_industry_direction",
            True,
        )
    elif category == "CFPB" and cfpb_subtype is None:
        status, reason, blocking = (
            "invalid",
            "cfpb_direction_missing_explicit_subtype",
            True,
        )
    elif (
        category == "CFPB"
        and cfpb_subtype is not None
        and CFPB_SIGN[cfpb_subtype] != sign
    ):
        status, reason, blocking = (
            "invalid",
            "cfpb_subtype_sign_mismatch",
            True,
        )
    elif not provenance["provenance_complete"]:
        status, reason = "weak_provenance", "direction_provenance_incomplete"
    else:
        status, reason = "eligible", "explicit_direction_with_provenance"

    payload: dict[str, Any] = {
        "schema": CANDIDATE_SCHEMA,
        "source_kind": source_kind,
        "input_path": str(source_path.resolve()),
        "input_line": line_number,
        "canonical_key": measure.canonical if measure else None,
        "congress": measure.congress if measure else None,
        "measure_type": measure.measure_type if measure else None,
        "number": measure.number if measure else None,
        "category": category,
        "raw_category": raw_category,
        "cfpb_subtype": cfpb_subtype,
        "direction_sign": sign,
        "direction_meaning": SIGN_MEANING.get(sign),
        "raw_direction": raw_direction,
        "source_url": provenance["source_url"],
        "source_reference": provenance["source_reference"],
        "rationale": provenance["rationale"],
        "provenance_complete": provenance["provenance_complete"],
        "status": status,
        "reason": reason,
        "blocking_source_issue": blocking,
    }
    payload["candidate_id"] = candidate_id(payload)
    return payload, edge if edge in accepted_edges else None


def accepted_edges_and_candidates(
    accepted_path: Path,
) -> tuple[set[Edge], list[dict[str, Any]], dict[Edge, list[dict[str, Any]]]]:
    rows = read_records(accepted_path)
    if not rows:
        raise DirectionError("accepted ledger is empty")
    edges: set[Edge] = set()
    candidates: list[dict[str, Any]] = []
    by_edge: dict[Edge, list[dict[str, Any]]] = defaultdict(list)

    def is_included_scoring_row(row: dict[str, Any]) -> bool:
        membership_status = row.get("membership_status")
        if membership_status is not None:
            normalized_status = normalize_token(membership_status)
            if normalized_status not in {"included", "excluded"}:
                raise DirectionError(
                    f"invalid membership_status in accepted ledger at "
                    f"{accepted_path}:{row['_input_line']}: "
                    f"{membership_status!r}"
                )
            if normalized_status == "excluded":
                return False
        if row.get("scoring_category") is False:
            return False
        return True

    # Establish the complete accepted edge universe before attaching candidates.
    for row in rows:
        if not is_included_scoring_row(row):
            continue
        disposition = row.get("disposition")
        if (
            disposition is not None
            and normalize_token(disposition) not in ACCEPTED_DISPOSITIONS
        ):
            raise DirectionError(
                f"non-accepted row in accepted ledger at "
                f"{accepted_path}:{row['_input_line']}: {disposition!r}"
            )
        if row.get("included_for_scoring") is False:
            raise DirectionError(
                f"included_for_scoring=false in accepted ledger at "
                f"{accepted_path}:{row['_input_line']}"
            )
        try:
            measure = measure_key_from_row(row)
            category = category_from_row(row)
        except Exception as exc:
            raise DirectionError(
                f"invalid accepted edge at "
                f"{accepted_path}:{row['_input_line']}: {exc}"
            ) from exc
        edges.add(Edge(measure, category))
    for row in rows:
        if not is_included_scoring_row(row):
            continue
        raw_direction = _field(
            row,
            "direction",
            "Direction",
            "direction_sign",
            "cu_direction",
            "pro_industry_sign",
            "aligned_yea_sign",
            "sign",
        )
        # Rows without an embedded decision define the edge but do not create
        # negative evidence.  This is especially important for article absence.
        if raw_direction in (None, ""):
            continue
        candidate, edge = build_candidate(
            row, "accepted_ledger", accepted_path, edges
        )
        candidates.append(candidate)
        if edge is not None:
            by_edge[edge].append(candidate)
    return edges, candidates, by_edge


def attach_source(
    source_path: Path,
    source_kind: str,
    edges: set[Edge],
    candidates: list[dict[str, Any]],
    by_edge: dict[Edge, list[dict[str, Any]]],
) -> None:
    for row in read_evidence_records(source_path):
        candidate, edge = build_candidate(row, source_kind, source_path, edges)
        candidates.append(candidate)
        if edge is not None:
            by_edge[edge].append(candidate)


def evaluate_edges(
    edges: Iterable[Edge],
    by_edge: dict[Edge, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    audit: list[dict[str, Any]] = []
    scoring: list[dict[str, Any]] = []
    for edge in sorted(edges):
        candidates = sorted(
            by_edge.get(edge, []),
            key=lambda row: (
                SOURCE_PRIORITY[row["source_kind"]],
                row["input_path"],
                row["input_line"],
                row["candidate_id"],
            ),
        )
        directional = [
            row for row in candidates if row["direction_sign"] is not None
        ]
        semantic_invalid = [
            row
            for row in candidates
            if row["status"] == "invalid"
            and row["reason"]
            in {
                "cfpb_direction_missing_explicit_subtype",
                "cfpb_subtype_sign_mismatch",
                "invalid_or_nonexplicit_industry_direction",
            }
        ]
        signs = {row["direction_sign"] for row in directional}
        complete = [row for row in directional if row["status"] == "eligible"]
        subtypes = {
            row["cfpb_subtype"]
            for row in directional
            if row["cfpb_subtype"] is not None
        }
        selected: dict[str, Any] | None = None
        if semantic_invalid:
            disposition, reason = "ambiguous", "invalid_direction_semantics"
        elif not directional:
            disposition, reason = "missing", "missing_explicit_category_direction"
        elif len(signs) != 1:
            disposition, reason = "ambiguous", "conflicting_category_directions"
        elif edge.category == "CFPB" and len(subtypes) != 1:
            disposition, reason = "ambiguous", "conflicting_or_missing_cfpb_subtype"
        elif not complete:
            disposition, reason = "weak_provenance", "direction_provenance_incomplete"
        else:
            selected = complete[0]
            disposition, reason = "included", "explicit_direction_with_provenance"

        audit_row = {
            "schema": AUDIT_SCHEMA,
            **edge.to_dict(),
            "edge_key": edge.key,
            "disposition": disposition,
            "reason": reason,
            "candidate_count": len(candidates),
            "directional_candidate_count": len(directional),
            "eligible_candidate_count": len(complete),
            "candidate_ids": [row["candidate_id"] for row in candidates],
            "candidate_signs": sorted(signs),
            "cfpb_subtypes": sorted(subtypes),
            "selected_candidate_id": (
                selected["candidate_id"] if selected else None
            ),
        }
        audit.append(audit_row)
        if selected is None:
            continue
        sign = int(selected["direction_sign"])
        provenance = {
            "source_url": selected["source_url"],
            "source_reference": selected["source_reference"],
            "rationale": selected["rationale"],
        }
        scoring.append(
            {
                "schema": DECISION_SCHEMA,
                **edge.to_dict(),
                "direction": sign,
                "direction_sign": sign,
                "direction_meaning": SIGN_MEANING[sign],
                "meaning": SIGN_MEANING[sign],
                "direction_subtype": selected["cfpb_subtype"],
                "cfpb_subtype": selected["cfpb_subtype"],
                "direction_provenance": provenance,
                "direction_source_url": selected["source_url"],
                "direction_source_reference": selected["source_reference"],
                "direction_reason": selected["rationale"],
                "selected_candidate_id": selected["candidate_id"],
                "supporting_candidate_ids": [
                    row["candidate_id"] for row in directional
                ],
                "disposition": "included",
                "scoring_eligible": sign != 0,
            }
        )
    return audit, scoring


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(
                row,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    accepted_path = args.accepted.resolve()
    if not accepted_path.is_file():
        raise DirectionError(f"accepted ledger does not exist: {accepted_path}")
    out = validate_out(args.out)
    optional_sources = [
        ("bill_directions", args.bill_directions),
        ("archive_stance_evidence", args.archive_stance_evidence),
        ("trusted_press_cross_reference", args.press_cross_reference),
    ]
    resolved_sources: list[tuple[str, Path]] = []
    for source_kind, raw_path in optional_sources:
        if raw_path is None:
            continue
        path = raw_path.resolve()
        if not path.is_file():
            raise DirectionError(f"{source_kind} does not exist: {path}")
        resolved_sources.append((source_kind, path))

    edges, candidates, by_edge = accepted_edges_and_candidates(accepted_path)
    for source_kind, path in resolved_sources:
        attach_source(path, source_kind, edges, candidates, by_edge)

    candidates.sort(
        key=lambda row: (
            row["canonical_key"] or "",
            row["category"] or "",
            SOURCE_PRIORITY[row["source_kind"]],
            row["input_path"],
            row["input_line"],
            row["candidate_id"],
        )
    )
    audit, scoring = evaluate_edges(edges, by_edge)
    unmatched = [
        {
            "schema": UNMATCHED_SCHEMA,
            **candidate,
        }
        for candidate in candidates
        if candidate["status"] == "unmatched"
    ]
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "direction_candidates.ndjson": out / "direction_candidates.ndjson",
        "edge_direction_audit.ndjson": out / "edge_direction_audit.ndjson",
        "scoring_directions.ndjson": out / "scoring_directions.ndjson",
        "unmatched_direction_evidence.ndjson": (
            out / "unmatched_direction_evidence.ndjson"
        ),
    }
    write_ndjson(paths["direction_candidates.ndjson"], candidates)
    write_ndjson(paths["edge_direction_audit.ndjson"], audit)
    write_ndjson(paths["scoring_directions.ndjson"], scoring)
    write_ndjson(paths["unmatched_direction_evidence.ndjson"], unmatched)

    edge_counts = Counter(row["disposition"] for row in audit)
    source_issue_count = sum(
        bool(row["blocking_source_issue"]) for row in candidates
    )
    acceptance_passed = bool(
        audit
        and len(scoring) == len(audit)
        and all(row["disposition"] == "included" for row in audit)
        and source_issue_count == 0
        and not unmatched
    )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "status": "PASS" if acceptance_passed else "FAIL",
        "production_write_prohibited": True,
        "acceptance_passed": acceptance_passed,
        "inputs": {
            "accepted": file_receipt(accepted_path),
            "optional_sources": [
                {"source_kind": kind, **file_receipt(path)}
                for kind, path in resolved_sources
            ],
            "sources_not_passed_are_not_read": True,
        },
        "policy": {
            "measure_identity": "congress:measure_type:number",
            "edge_identity": "(congress,measure_type,number,category)",
            "direction_values": [-1, 0, 1],
            "direction_meaning": SIGN_MEANING,
            "cfpb_subtype_sign": CFPB_SIGN,
            "article_absence_is_evidence": False,
            "press_cross_reference_is_only_read_when_explicitly_passed": True,
            "missing_conflicting_or_weak_edges_block_acceptance": True,
            "unmatched_or_invalid_passed_evidence_blocks_acceptance": True,
        },
        "counts": {
            "accepted_edges": len(edges),
            "direction_candidates": len(candidates),
            "scoring_directions": len(scoring),
            "unmatched_direction_evidence": len(unmatched),
            "blocking_source_issues": source_issue_count,
            "edge_dispositions": dict(sorted(edge_counts.items())),
            "candidate_statuses": dict(
                sorted(Counter(row["status"] for row in candidates).items())
            ),
        },
        "outputs": {
            name: file_receipt(path) for name, path in sorted(paths.items())
        },
        "producer": file_receipt(Path(__file__).resolve()),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        "staged direction ledger: "
        f"{len(scoring)}/{len(edges)} scoring-ready; "
        f"acceptance_passed={acceptance_passed}"
    )
    print(out / "manifest.json")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    try:
        manifest = run(parser().parse_args(argv))
    except DirectionError as exc:
        print(f"build_legislation_direction_ledger: FAIL CLOSED: {exc}")
        return 2
    return 0 if manifest["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
