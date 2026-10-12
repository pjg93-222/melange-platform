#!/usr/bin/env python3
"""Build a deterministic ACU/CUNA/NAFCU bill-title search query plan.

This producer does not search the web and does not make legislation decisions.
It emits exact official-title plus bill-number queries restricted to the
current America's Credit Unions domain and the appropriate legacy CUNA/NAFCU
news domains. Search results are corroborative review evidence only:

* a positive result may corroborate bill identity, category, direction, or a
  reported action, subject to careful review;
* absence is neutral and can never support inclusion or exclusion;
* no result can automatically include or exclude a candidate; and
* Sponsor / Original Cosponsor / Cosponsor / withdrawn status comes only from
  Congress.gov relationship data.

The default ``all`` scope preserves exhaustive candidate coverage. The
``ambiguous`` scope is available only with explicit review rows and selects a
measure when any of its category memberships has an explicit ambiguity marker.
Rows missing an official title or parseable bill identity are retained in a
separate unresolved ledger and make the plan BLOCKED rather than disappearing.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

try:
    from legislation_measure_key import CANONICAL_KEY_RE, SUPPORTED_CONGRESSES
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import (
        CANONICAL_KEY_RE,
        SUPPORTED_CONGRESSES,
    )


SCHEMA = "acu-legislation-bill-title-corroboration-query-plan-v1"
QUERY_SCHEMA = "acu-legislation-bill-title-corroboration-query-v1"
COVERAGE_SCHEMA = "acu-legislation-bill-title-query-coverage-v1"
UNRESOLVED_SCHEMA = "acu-legislation-bill-title-query-unresolved-v1"
REQUIRED_CONGRESSES = frozenset(SUPPORTED_CONGRESSES)
HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
# ``CANONICAL_KEY_RE`` is imported above.  The former local pattern carried
# ``re.IGNORECASE``, which made this the only reader in the lane that accepted
# ``117:HR:1``.  ``canonical_parts`` already casefolds its input before
# matching, so dropping the flag is behaviour-preserving here and removes the
# mixed-case duplicate-key hazard everywhere else.


@dataclass(frozen=True)
class DomainSpec:
    domain_id: str
    organization: str
    domain: str
    era: str
    applicable_congresses: frozenset[int]


# Deliberately closed: a caller cannot silently add a third-party press domain.
OFFICIAL_DOMAINS = (
    DomainSpec(
        "acu_current",
        "America's Credit Unions",
        "americascreditunions.org",
        "current",
        frozenset({118, 119}),
    ),
    DomainSpec(
        "cuna_legacy_news",
        "Credit Union National Association",
        "news.cuna.org",
        "legacy",
        frozenset({116, 117, 118}),
    ),
    DomainSpec(
        "nafcu_legacy",
        "National Association of Federally-Insured Credit Unions",
        "nafcu.org",
        "legacy",
        frozenset({116, 117, 118}),
    ),
)

BILL_TYPE_LABELS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
    "hamdt": "H.Amdt.",
    "samdt": "S.Amdt.",
}
BILL_TYPE_PLAIN = {
    "hr": "HR",
    "s": "S",
    "hjres": "HJ Res",
    "sjres": "SJ Res",
    "hconres": "H Con Res",
    "sconres": "S Con Res",
    "hres": "H Res",
    "sres": "S Res",
    "hamdt": "H Amdt",
    "samdt": "S Amdt",
}

POSITIVE_CORROBORATION_FIELDS = (
    "bill_identity",
    "candidate_category",
    "direction_or_industry_position",
    "reported_legislative_action",
)
NON_DISPOSITIVE_CONTRACT = {
    "positive_result_is_corroborative_not_dispositive": True,
    "positive_result_may_corroborate": list(POSITIVE_CORROBORATION_FIELDS),
    "positive_result_requires_careful_human_review": True,
    "absence_is_neutral": True,
    "absence_is_never_exclusion_evidence": True,
    "absence_is_never_inclusion_evidence": True,
    "automatic_inclusion": None,
    "automatic_exclusion": None,
    "automatic_category_decision": None,
    "automatic_direction_decision": None,
    "sponsorship_role_assigned": None,
    "sponsorship_role_authority": "Congress.gov relationship data only",
    "official_relevance_authority": (
        "Congress.gov official title, summary, measure text, and operative "
        "provisions"
    ),
    "acu_evidence_cannot_override_official_relevance": True,
}


class QueryPlanError(RuntimeError):
    """The query plan cannot be built without weakening its audit contract."""


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise QueryPlanError(f"required NDJSON input is missing: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise QueryPlanError(
                f"{path}:{line_number} must contain a JSON object"
            )
        rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_ndjson(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    values = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True)
            for row in values
        )
        + ("\n" if values else ""),
        encoding="utf-8",
        newline="\n",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    display = (
        resolved.relative_to(relative_to.resolve()).as_posix()
        if relative_to is not None
        else str(resolved)
    )
    return {
        "path": display,
        "size": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def safe_output_directory(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise QueryPlanError(
            f"query-plan output must be new or empty: {resolved}"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def reusable_report(
    *,
    out_dir: Path,
    scope: str,
    inputs: Mapping[str, Any],
) -> dict[str, Any] | None:
    manifest_path = out_dir.resolve() / "manifest.json"
    if not manifest_path.is_file():
        return None
    report = read_json(manifest_path)
    if not isinstance(report, dict):
        raise QueryPlanError("existing query-plan manifest is not an object")
    if (
        report.get("schema") != SCHEMA
        or report.get("scope") != scope
        or report.get("inputs") != inputs
        or report.get("status") not in {"PASS", "BLOCKED"}
    ):
        raise QueryPlanError(
            "existing query-plan manifest is stale or input-incompatible"
        )
    outputs = report.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {
        "queries",
        "candidate_coverage",
        "unresolved",
    }:
        raise QueryPlanError("existing query-plan outputs are incomplete")
    for name, receipt in outputs.items():
        if not isinstance(receipt, dict):
            raise QueryPlanError(
                f"existing query-plan receipt is invalid: {name}"
            )
        relative = Path(normalize(receipt.get("path")))
        artifact = (out_dir.resolve() / relative).resolve()
        if out_dir.resolve() not in artifact.parents:
            raise QueryPlanError(
                f"existing query-plan output escapes directory: {name}"
            )
        if (
            not artifact.is_file()
            or artifact.stat().st_size != receipt.get("size")
            or sha256_file(artifact) != receipt.get("sha256")
        ):
            raise QueryPlanError(
                f"existing query-plan output hash mismatch: {name}"
            )
    return report


def canonical_parts(value: Any) -> tuple[int, str, int]:
    text = normalize(value).casefold()
    match = CANONICAL_KEY_RE.fullmatch(text)
    if not match:
        raise QueryPlanError(f"invalid candidate canonical key: {value!r}")
    congress, bill_type, number = match.groups()
    return int(congress), bill_type.casefold(), int(number)


def official_title(
    record: Mapping[str, Any],
    _review_rows: Sequence[Mapping[str, Any]],
) -> tuple[str | None, str | None]:
    sources: list[tuple[Any, str]] = [
        (record.get("official_title"), "congress_gov_official_title"),
    ]
    # The discovery union is itself a Congress.gov product. Its title is an
    # official-source title, unlike press prose or a machine-generated purpose.
    sources.append((record.get("title"), "congress_gov_union_record_title"))
    for raw, source in sources:
        title = normalize(raw)
        if title and not generic_title(title):
            return title, source
    return None, None


def generic_title(value: Any) -> bool:
    text = normalize(value).casefold()
    text = re.sub(r"^(?:the|this|an|a)\s+", "", text)
    text = re.sub(r"[^a-z]+", "", text)
    return text in {"act", "bill", "resolution", "amendment"}


def explicit_ambiguity_markers(row: Mapping[str, Any]) -> list[str]:
    markers: list[str] = []
    for field in (
        "ambiguous_short_act_alias_probe",
        "review_required",
        "unresolved",
    ):
        if row.get(field) is True:
            markers.append(f"{field}=true")
    for field in ("triage_bucket", "triage", "adjudication_state", "disposition"):
        value = normalize(row.get(field)).casefold()
        if value and any(
            token in value
            for token in ("ambiguous", "unresolved", "requires_review", "needs_review")
        ):
            markers.append(f"{field}={value}")
    return sorted(set(markers))


def quote_exact(value: str) -> str:
    # Search-engine exact phrases use double quotes. Remove control characters
    # and escape embedded quotes rather than allowing query syntax injection.
    return normalize(value).replace("\\", "\\\\").replace('"', '\\"')


def bill_number_variants(bill_type: str, number: int) -> tuple[tuple[str, str], ...]:
    punctuation = BILL_TYPE_LABELS[bill_type]
    plain = BILL_TYPE_PLAIN[bill_type]
    return (
        ("official_punctuation_spaced", f"{punctuation} {number}"),
        ("official_punctuation_compact", f"{punctuation}{number}"),
        ("plain_spaced", f"{plain} {number}"),
        ("plain_compact", f"{plain.replace(' ', '')}{number}"),
    )


def stable_query_id(
    canonical_key: str, domain_id: str, variant_id: str
) -> str:
    digest = hashlib.sha256(
        f"{canonical_key}|{domain_id}|{variant_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"acu-title:{digest}"


def build_query_rows(
    *,
    records: Sequence[Mapping[str, Any]],
    memberships: Sequence[Mapping[str, Any]],
    review_rows: Sequence[Mapping[str, Any]] = (),
    scope: str = "all",
) -> dict[str, Any]:
    if scope not in {"all", "ambiguous"}:
        raise QueryPlanError("scope must be 'all' or 'ambiguous'")
    if scope == "ambiguous" and not review_rows:
        raise QueryPlanError(
            "ambiguous scope requires explicit candidate/review rows"
        )

    record_by_key: dict[str, Mapping[str, Any]] = {}
    for record in records:
        key = normalize(record.get("canonical_key")).casefold()
        if not key:
            raise QueryPlanError("discovery record lacks canonical_key")
        if key in record_by_key:
            raise QueryPlanError(f"duplicate discovery record: {key}")
        record_by_key[key] = record

    query_ids_by_key: dict[str, set[str]] = {}
    membership_pairs: set[tuple[str, str]] = set()
    for membership in memberships:
        key = normalize(membership.get("canonical_key")).casefold()
        query_id = normalize(membership.get("query_id"))
        if key not in record_by_key:
            raise QueryPlanError(
                f"membership references missing discovery record: {key}"
            )
        if not query_id:
            raise QueryPlanError(f"membership lacks query_id: {key}")
        pair = (key, query_id)
        if pair in membership_pairs:
            raise QueryPlanError(f"duplicate candidate membership: {pair}")
        membership_pairs.add(pair)
        query_ids_by_key.setdefault(key, set()).add(query_id)

    review_by_pair: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in review_rows:
        key = normalize(row.get("canonical_key")).casefold()
        query_id = normalize(row.get("query_id"))
        if not key or not query_id:
            raise QueryPlanError(
                "candidate/review row lacks canonical_key or query_id"
            )
        if (key, query_id) not in membership_pairs:
            raise QueryPlanError(
                f"candidate/review row is outside discovery memberships: "
                f"{(key, query_id)}"
            )
        review_by_pair.setdefault((key, query_id), []).append(row)

    queries: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    selected_membership_count = 0
    for key in sorted(query_ids_by_key):
        record = record_by_key[key]
        candidate_query_ids = sorted(query_ids_by_key[key])
        rows_for_measure = [
            row
            for query_id in candidate_query_ids
            for row in review_by_pair.get((key, query_id), [])
        ]
        ambiguity_by_query_id = {
            query_id: sorted(
                {
                    marker
                    for row in review_by_pair.get((key, query_id), [])
                    for marker in explicit_ambiguity_markers(row)
                }
            )
            for query_id in candidate_query_ids
        }
        ambiguous_query_ids = sorted(
            query_id
            for query_id, markers in ambiguity_by_query_id.items()
            if markers
        )
        selected = scope == "all" or bool(ambiguous_query_ids)
        if not selected:
            continue
        selected_membership_count += len(candidate_query_ids)

        try:
            congress, bill_type, number = canonical_parts(key)
        except QueryPlanError as exc:
            unresolved.append(
                {
                    "schema": UNRESOLVED_SCHEMA,
                    "canonical_key": key,
                    "candidate_query_ids": candidate_query_ids,
                    "reason": "invalid_canonical_bill_identity",
                    "detail": str(exc),
                    **NON_DISPOSITIVE_CONTRACT,
                }
            )
            continue
        title, title_source = official_title(record, rows_for_measure)
        if not title:
            unresolved.append(
                {
                    "schema": UNRESOLVED_SCHEMA,
                    "canonical_key": key,
                    "congress": congress,
                    "bill_type": bill_type,
                    "bill_number": number,
                    "candidate_query_ids": candidate_query_ids,
                    "ambiguous_candidate_query_ids": ambiguous_query_ids,
                    "reason": "official_title_missing_or_generic",
                    **NON_DISPOSITIVE_CONTRACT,
                }
            )
            continue

        variants = bill_number_variants(bill_type, number)
        display_number = variants[0][1]
        query_ids: list[str] = []
        applicable_domains = tuple(
            domain
            for domain in OFFICIAL_DOMAINS
            if congress in domain.applicable_congresses
        )
        for domain in applicable_domains:
            for variant_id, number_phrase in variants:
                query_id = stable_query_id(key, domain.domain_id, variant_id)
                query_ids.append(query_id)
                queries.append(
                    {
                        "schema": QUERY_SCHEMA,
                        "query_id": query_id,
                        "scope": scope,
                        "canonical_key": key,
                        "congress": congress,
                        "bill_type": bill_type,
                        "bill_number": number,
                        "display_number": display_number,
                        "official_title": title,
                        "official_title_source": title_source,
                        "candidate_query_ids": candidate_query_ids,
                        "ambiguous_candidate_query_ids": ambiguous_query_ids,
                        "ambiguity_markers_by_query_id": ambiguity_by_query_id,
                        "domain_id": domain.domain_id,
                        "organization": domain.organization,
                        "domain": domain.domain,
                        "domain_era": domain.era,
                        "domain_applicable_congresses": sorted(
                            domain.applicable_congresses
                        ),
                        "bill_number_variant": variant_id,
                        "bill_number_phrase": number_phrase,
                        "search_query": (
                            f'site:{domain.domain} '
                            f'"{quote_exact(title)}" '
                            f'"{quote_exact(number_phrase)}"'
                        ),
                        **NON_DISPOSITIVE_CONTRACT,
                    }
                )
        coverage.append(
            {
                "schema": COVERAGE_SCHEMA,
                "scope": scope,
                "canonical_key": key,
                "congress": congress,
                "display_number": display_number,
                "official_title": title,
                "official_title_source": title_source,
                "candidate_query_ids": candidate_query_ids,
                "ambiguous_candidate_query_ids": ambiguous_query_ids,
                "queried_domains": [
                    domain.domain for domain in applicable_domains
                ],
                "query_count": len(query_ids),
                "query_ids": query_ids,
                **NON_DISPOSITIVE_CONTRACT,
            }
        )

    queries.sort(
        key=lambda row: (
            row["canonical_key"],
            row["domain_id"],
            row["bill_number_variant"],
        )
    )
    coverage.sort(key=lambda row: row["canonical_key"])
    unresolved.sort(key=lambda row: row["canonical_key"])
    return {
        "queries": queries,
        "coverage": coverage,
        "unresolved": unresolved,
        "selected_measure_count": len(coverage) + len(unresolved),
        "selected_membership_count": selected_membership_count,
    }


def resolve_run_inputs(run_dir: Path) -> tuple[Path, Path, Path]:
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise QueryPlanError("discovery/final manifest must be a JSON object")
    if normalize(manifest.get("status")).casefold() not in {
        "complete",
        "pass",
        "passed",
    }:
        raise QueryPlanError("discovery/final candidate generation is not complete")
    union = manifest.get("union")
    if not isinstance(union, dict):
        raise QueryPlanError("manifest lacks a union candidate ledger")
    records_name = normalize(union.get("records_file"))
    memberships_name = normalize(union.get("memberships_file"))
    if not records_name or not memberships_name:
        raise QueryPlanError("manifest lacks union records/memberships file names")
    records = (run_dir / records_name).resolve()
    memberships = (run_dir / memberships_name).resolve()
    if run_dir.resolve() not in records.parents or run_dir.resolve() not in memberships.parents:
        raise QueryPlanError("union candidate input escapes the run directory")
    return manifest_path, records, memberships


def build(
    *,
    run_dir: Path,
    out_dir: Path,
    scope: str,
    review_ledger: Path | None,
    resume: bool = False,
) -> dict[str, Any]:
    run = run_dir.resolve()
    manifest_path, records_path, memberships_path = resolve_run_inputs(run)
    review_path = review_ledger.resolve() if review_ledger else None
    inputs = {
        "run_manifest": file_receipt(manifest_path),
        "records": file_receipt(records_path),
        "memberships": file_receipt(memberships_path),
        "review_ledger": (
            file_receipt(review_path) if review_path else None
        ),
    }
    if resume:
        prior = reusable_report(
            out_dir=out_dir,
            scope=scope,
            inputs=inputs,
        )
        if prior is not None:
            return {**prior, "reused": True}
    review_rows = read_ndjson(review_path) if review_path else []
    result = build_query_rows(
        records=read_ndjson(records_path),
        memberships=read_ndjson(memberships_path),
        review_rows=review_rows,
        scope=scope,
    )
    out = safe_output_directory(out_dir)
    query_path = out / "acu_bill_title_corroboration_queries.ndjson"
    coverage_path = out / "acu_bill_title_query_candidate_coverage.ndjson"
    unresolved_path = out / "acu_bill_title_query_unresolved.ndjson"
    write_ndjson(query_path, result["queries"])
    write_ndjson(coverage_path, result["coverage"])
    write_ndjson(unresolved_path, result["unresolved"])
    status = "BLOCKED" if result["unresolved"] else "PASS"
    report = {
        "schema": SCHEMA,
        "status": status,
        "stage": "query_plan_only",
        "network_requests_performed": 0,
        "production_or_mainframe_written": False,
        "scope": scope,
        "candidate_measure_count": result["selected_measure_count"],
        "candidate_membership_count": result["selected_membership_count"],
        "query_count": len(result["queries"]),
        "queryable_measure_count": len(result["coverage"]),
        "unresolved_measure_count": len(result["unresolved"]),
        "queries_per_queryable_measure_by_congress": {
            str(congress): (
                sum(
                    congress in domain.applicable_congresses
                    for domain in OFFICIAL_DOMAINS
                )
                * 4
            )
            for congress in sorted(REQUIRED_CONGRESSES)
        },
        "allowed_domains": [
            {
                "domain_id": domain.domain_id,
                "organization": domain.organization,
                "domain": domain.domain,
                "era": domain.era,
                "applicable_congresses": sorted(
                    domain.applicable_congresses
                ),
            }
            for domain in OFFICIAL_DOMAINS
        ],
        "contracts": NON_DISPOSITIVE_CONTRACT,
        "inputs": inputs,
        "outputs": {
            "queries": file_receipt(query_path, relative_to=out),
            "candidate_coverage": file_receipt(
                coverage_path, relative_to=out
            ),
            "unresolved": file_receipt(unresolved_path, relative_to=out),
        },
    }
    write_json(out / "manifest.json", report)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Emit exact official-title + bill-number search queries for "
            "America's Credit Unions and legacy CUNA/NAFCU domains."
        )
    )
    result.add_argument("--run", type=Path, required=True)
    result.add_argument("--out", type=Path, required=True)
    result.add_argument(
        "--scope", choices=("all", "ambiguous"), default="all"
    )
    result.add_argument(
        "--review-ledger",
        type=Path,
        help=(
            "Candidate/review NDJSON containing explicit ambiguity markers; "
            "required for --scope ambiguous."
        ),
    )
    result.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Reuse a hash-identical complete query plan; fail closed if an "
            "existing plan is stale or incomplete."
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = build(
            run_dir=args.run,
            out_dir=args.out,
            scope=args.scope,
            review_ledger=args.review_ledger,
            resume=args.resume,
        )
    except (OSError, ValueError, json.JSONDecodeError, QueryPlanError) as exc:
        print(f"ACU bill-title query plan failed closed: {exc}")
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
