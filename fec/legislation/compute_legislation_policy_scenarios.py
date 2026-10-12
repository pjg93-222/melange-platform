#!/usr/bin/env python3
"""Compute staged legislation role-weight scenarios without selecting one.

Inputs are the canonical accepted relationship ledger and the explicit scoring
direction ledger.  Outputs quantify the three currently competing role-weight
policies under the aggressive Congress decay schedule.  They are audit/shadow
artifacts only: this producer never writes a workbook, ranks, or a production
model file, and never selects a promotion policy.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import itertools
import json
import math
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from legislation.build_accepted_legislation_generation import (
    DECAY_WEIGHT,
    ROLE_STRENGTH,
    ROLE_WEIGHT_POLICY_SCENARIOS,
    SCHEMA as ACCEPTED_ROW_SCHEMA,
)
from legislation.collect_official_rollcall_events import canonical_category

try:
    from lane_file_io import atomic_write_bytes, read_ndjson_text
    from legislation_measure_key import (
        CANONICAL_KEY_RE,
        measure_weight,
        measure_weight_policy,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes, read_ndjson_text
    from fec.legislation_measure_key import (
        CANONICAL_KEY_RE,
        measure_weight,
        measure_weight_policy,
    )


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA = "legislation-policy-scenario-manifest-v1"
EVENT_SCHEMA = "legislation-policy-role-event-v1"
MEMBER_CATEGORY_SCHEMA = "legislation-member-category-policy-scenario-v1"
PAIRWISE_SCHEMA = "legislation-policy-pairwise-delta-v1"
TOTALS_SCHEMA = "legislation-policy-scenario-totals-v1"
BASELINE_DELTA_SCHEMA = "legislation-policy-baseline-member-delta-v1"
DIRECTION_SCHEMA = "legislation-direction-decision-v1"
BIOGUIDE_RE = re.compile(r"^[A-Z]\d{6}$")
MEASURE_KEY_RE = CANONICAL_KEY_RE
POLICIES = tuple(ROLE_WEIGHT_POLICY_SCENARIOS)
FORBIDDEN_OUTPUT_NAMES = {
    "ranks_python.json",
    "archive_leg.json",
    "vote_category_lean.json",
    "moneyball main version - blended.xlsx",
}


class ScenarioError(ValueError):
    """An input or staged-output contract failed."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute audit-only legislation role-weight scenarios."
    )
    parser.add_argument("--accepted-ledger", type=Path, required=True)
    parser.add_argument("--scoring-directions", type=Path, required=True)
    parser.add_argument("--baseline-scenario", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Directory under audits/legfix5 or the system temp directory.",
    )
    return parser.parse_args()


def json_compact(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ScenarioError(f"input file does not exist: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        read_ndjson_text(path).splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ScenarioError(
                f"invalid JSON at {path}:{line_number}: {error}"
            ) from error
        if not isinstance(row, dict):
            raise ScenarioError(f"non-object row at {path}:{line_number}")
        rows.append({**row, "_input_line": line_number})
    if not rows:
        raise ScenarioError(f"input ledger is empty: {path}")
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_atomic(path: Path, body: str) -> None:
    # The scratch name used to be fixed, so two scenario runs writing the same
    # output clobbered each other's staging file.  ``atomic_write_bytes`` is
    # unique per process and per call, fsync'd, and retries the publish through
    # the OneDrive lock window.  Newlines stay LF: the body is encoded here
    # rather than written through text mode.
    atomic_write_bytes(path, body.encode("utf-8"))


def write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    values = [json_compact(row) for row in rows]
    write_atomic(path, "\n".join(values) + ("\n" if values else ""))


def write_json(path: Path, value: Any) -> None:
    write_atomic(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )


def validate_out(out: Path, inputs: Iterable[Path]) -> Path:
    resolved = out.resolve()
    audit_root = (ROOT / "audits" / "legfix5").resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if resolved.suffix:
        raise ScenarioError("--out must be a directory")
    if resolved.name.casefold() in FORBIDDEN_OUTPUT_NAMES:
        raise ScenarioError(f"production output name is prohibited: {resolved.name}")
    if not (
        resolved == audit_root
        or audit_root in resolved.parents
        or resolved == temp_root
        or temp_root in resolved.parents
    ):
        raise ScenarioError(
            "--out must be under audits/legfix5 or the system temp directory"
        )
    for input_path in inputs:
        if resolved == input_path.resolve():
            raise ScenarioError("--out cannot overwrite an input")
    return resolved


def parse_measure_key(value: Any) -> tuple[str, int, str, int]:
    key = str(value or "").strip().casefold()
    match = MEASURE_KEY_RE.fullmatch(key)
    if not match:
        raise ScenarioError(f"invalid type-aware measure key: {value!r}")
    return key, int(match.group(1)), match.group(2), int(match.group(3))


def scoring_category_from_row(row: dict[str, Any]) -> str:
    for field in ("category_label", "category", "query_id"):
        category = canonical_category(row.get(field))
        if category:
            return category
    raise ScenarioError(
        "row has no recognized one-of-18 category at line "
        f"{row.get('_input_line')}: {row.get('category')!r}"
    )


def accepted_category_from_row(row: dict[str, Any]) -> str:
    try:
        return scoring_category_from_row(row)
    except ScenarioError:
        if row.get("scoring_category") is not False:
            raise
        source = str(
            row.get("category") or row.get("category_label") or ""
        ).strip()
        if not source:
            raise ScenarioError(
                f"non-scoring row {row.get('_input_line')} has no category"
            )
        return f"non_scoring:{source}"


def validate_provenance(provenance: Any, label: str) -> dict[str, Any]:
    if not isinstance(provenance, dict) or not provenance:
        raise ScenarioError(f"{label} provenance must be a nonempty object")
    try:
        return json.loads(json.dumps(provenance, ensure_ascii=False))
    except (TypeError, ValueError) as error:
        raise ScenarioError(f"{label} provenance is not JSON-compatible") from error


def accepted_identity(row: dict[str, Any]) -> tuple[str, int, str, int]:
    if row.get("schema") != ACCEPTED_ROW_SCHEMA:
        raise ScenarioError(
            f"accepted row {row['_input_line']} has wrong schema "
            f"{row.get('schema')!r}"
        )
    key, congress, measure_type, number = parse_measure_key(row.get("measure_key"))
    if (
        row.get("congress") != congress
        or row.get("measure_type") != measure_type
        or row.get("measure_number") != number
    ):
        raise ScenarioError(
            f"accepted row {row['_input_line']} has conflicting measure identity"
        )
    return key, congress, measure_type, number


def direction_identity(row: dict[str, Any]) -> tuple[str, int, str, int]:
    if row.get("schema") != DIRECTION_SCHEMA:
        raise ScenarioError(
            f"direction row {row['_input_line']} has wrong schema "
            f"{row.get('schema')!r}"
        )
    key, congress, measure_type, number = parse_measure_key(
        row.get("canonical_key")
    )
    if (
        row.get("congress") != congress
        or row.get("measure_type") != measure_type
        or row.get("number") != number
    ):
        raise ScenarioError(
            f"direction row {row['_input_line']} has conflicting measure identity"
        )
    return key, congress, measure_type, number


def load_directions(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    directions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key, congress, measure_type, number = direction_identity(row)
        category = scoring_category_from_row(row)
        direction = row.get("direction_sign", row.get("direction"))
        if isinstance(direction, bool) or direction not in {-1, 0, 1}:
            raise ScenarioError(
                f"direction row {row['_input_line']} has invalid sign {direction!r}"
            )
        if row.get("disposition") != "included":
            raise ScenarioError(
                f"direction row {row['_input_line']} is not included"
            )
        provenance = validate_provenance(
            row.get("direction_provenance"),
            f"direction row {row['_input_line']}",
        )
        source = provenance.get("source_url") or provenance.get("source_reference")
        if not source or not provenance.get("rationale"):
            raise ScenarioError(
                f"direction row {row['_input_line']} has incomplete provenance"
            )
        edge = (key, category)
        normalized = {
            "canonical_key": key,
            "congress": congress,
            "measure_type": measure_type,
            "number": number,
            "category": category,
            "direction_sign": int(direction),
            "direction_provenance": provenance,
            "source_row": row["_input_line"],
        }
        prior = directions.get(edge)
        if prior is not None and prior != normalized:
            raise ScenarioError(f"conflicting duplicate direction edge: {edge}")
        directions[edge] = normalized
    return directions


def zero_reason(row: dict[str, Any]) -> str | None:
    if row["membership_status"] == "excluded":
        return "membership_excluded"
    if row["relationship_status"] == "withdrawn":
        return "relationship_withdrawn"
    if row.get("scoring_category") is False:
        return "category_non_scoring"
    return None


def build_events(
    accepted_rows: list[dict[str, Any]],
    directions: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: dict[
        tuple[str, str, str, str, str], list[dict[str, Any]]
    ] = defaultdict(list)
    names: dict[str, str] = {}
    required_direction_edges: set[tuple[str, str]] = set()
    for row in accepted_rows:
        key, congress, measure_type, number = accepted_identity(row)
        category = accepted_category_from_row(row)
        bioguide = str(row.get("member_bioguide_id") or "").strip().upper()
        name = " ".join(str(row.get("member_name") or "").split())
        if not BIOGUIDE_RE.fullmatch(bioguide) or not name:
            raise ScenarioError(
                f"accepted row {row['_input_line']} lacks a valid member identity"
            )
        if bioguide in names and names[bioguide] != name:
            raise ScenarioError(f"conflicting names for {bioguide}")
        names[bioguide] = name
        role = row.get("role")
        if role not in ROLE_STRENGTH:
            raise ScenarioError(
                f"accepted row {row['_input_line']} has invalid role {role!r}"
            )
        membership = row.get("membership_status")
        relationship = row.get("relationship_status")
        if membership not in {"included", "excluded"}:
            raise ScenarioError(
                f"accepted row {row['_input_line']} has invalid membership status"
            )
        if relationship not in {"current", "withdrawn"}:
            raise ScenarioError(
                f"accepted row {row['_input_line']} has invalid relationship status"
            )
        provenance = validate_provenance(
            row.get("provenance"), f"accepted row {row['_input_line']}"
        )
        expected_measure_weight = measure_weight(key)
        expected_effective_weight = round(
            DECAY_WEIGHT[congress] * expected_measure_weight, 12
        )
        declared_measure_weight = row.get(
            "measure_weight", expected_measure_weight
        )
        declared_effective_weight = row.get(
            "effective_weight", expected_effective_weight
        )
        if (
            isinstance(declared_measure_weight, bool)
            or not isinstance(declared_measure_weight, (int, float))
            or float(declared_measure_weight) != expected_measure_weight
        ):
            raise ScenarioError(
                f"accepted row {row['_input_line']} measure_weight does not "
                "match the original-method bill/amendment policy"
            )
        if (
            isinstance(declared_effective_weight, bool)
            or not isinstance(declared_effective_weight, (int, float))
            or float(declared_effective_weight) != expected_effective_weight
        ):
            raise ScenarioError(
                f"accepted row {row['_input_line']} effective_weight does not "
                "equal Congress decay times measure weight"
            )
        normalized = {
            "measure_key": key,
            "congress": congress,
            "measure_type": measure_type,
            "measure_number": number,
            "category": category,
            "source_category": row.get("category"),
            "member_bioguide_id": bioguide,
            "member_name": name,
            "role": role,
            "membership_status": membership,
            "relationship_status": relationship,
            "scoring_category": row.get("scoring_category") is not False,
            "measure_weight": expected_measure_weight,
            "effective_weight": expected_effective_weight,
            "provenance": provenance,
            "source_row": row["_input_line"],
        }
        group = (key, category, bioguide, membership, relationship)
        candidates[group].append(normalized)
        if zero_reason(normalized) is None:
            required_direction_edges.add((key, category))

    missing_directions = sorted(required_direction_edges - set(directions))
    extra_directions = sorted(set(directions) - required_direction_edges)
    if missing_directions or extra_directions:
        details = []
        if missing_directions:
            details.append(f"missing={missing_directions[:20]!r}")
        if extra_directions:
            details.append(f"extra={extra_directions[:20]!r}")
        raise ScenarioError(
            "direction coverage does not exactly match included current scoring "
            "measure/category edges: " + "; ".join(details)
        )

    events: list[dict[str, Any]] = []
    for group in sorted(candidates):
        rows = candidates[group]
        selected = max(
            rows,
            key=lambda item: (
                ROLE_STRENGTH[item["role"]],
                -item["source_row"],
            ),
        )
        reason = zero_reason(selected)
        direction = directions.get((selected["measure_key"], selected["category"]))
        direction_applies = reason is None
        sign = direction["direction_sign"] if direction_applies else None
        if reason is None and sign == 0:
            reason = "neutral_direction"
        active = reason is None
        decay = DECAY_WEIGHT[selected["congress"]]
        measure_factor = selected["measure_weight"]
        effective_weight = selected["effective_weight"]
        weights = {
            policy: ROLE_WEIGHT_POLICY_SCENARIOS[policy][selected["role"]]
            for policy in POLICIES
        }
        contributions = {
            policy: round(
                (sign or 0) * effective_weight * weights[policy], 12
            )
            if active
            else 0.0
            for policy in POLICIES
        }
        events.append(
            {
                "schema": EVENT_SCHEMA,
                **{
                    field: selected[field]
                    for field in (
                        "measure_key",
                        "congress",
                        "measure_type",
                        "measure_number",
                        "category",
                        "member_bioguide_id",
                        "member_name",
                        "role",
                        "membership_status",
                        "relationship_status",
                        "scoring_category",
                    )
                },
                "source_categories": sorted(
                    {str(item["source_category"]) for item in rows}
                ),
                "source_rows": sorted(item["source_row"] for item in rows),
                "strongest_role_rule": (
                    "sponsor > original_cosponsor > cosponsor"
                ),
                "decay_weight": decay,
                "measure_weight": measure_factor,
                "effective_weight": effective_weight,
                "direction_sign": sign,
                "direction_provenance": (
                    direction["direction_provenance"]
                    if direction_applies
                    else None
                ),
                "role_weights": weights,
                "policy_contributions": contributions,
                "activity_included": active,
                "zero_weight_reason": reason,
                "provenance": selected["provenance"],
            }
        )
    return events


def aggregate_member_categories(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[(event["member_bioguide_id"], event["category"])].append(event)
    rows: list[dict[str, Any]] = []
    for (bioguide, category), items in sorted(groups.items()):
        names = {item["member_name"] for item in items}
        if len(names) != 1:
            raise ScenarioError(f"conflicting names while aggregating {bioguide}")
        scores = {
            policy: round(
                math.fsum(item["policy_contributions"][policy] for item in items),
                12,
            )
            for policy in POLICIES
        }
        rows.append(
            {
                "schema": MEMBER_CATEGORY_SCHEMA,
                "member_bioguide_id": bioguide,
                "member_name": next(iter(names)),
                "category": category,
                "policy_scores": scores,
                "included_activity_event_count": sum(
                    item["activity_included"] for item in items
                ),
                "excluded_event_count": sum(
                    item["membership_status"] == "excluded" for item in items
                ),
                "withdrawn_event_count": sum(
                    item["relationship_status"] == "withdrawn" for item in items
                ),
                "role_counts": dict(
                    sorted(
                        Counter(
                            item["role"]
                            for item in items
                            if item["activity_included"]
                        ).items()
                    )
                ),
                "measure_keys": sorted(
                    {item["measure_key"] for item in items}
                ),
            }
        )
    return rows


def build_totals(
    member_categories: list[dict[str, Any]],
) -> dict[str, Any]:
    members: dict[str, dict[str, Any]] = {}
    for row in member_categories:
        member = members.setdefault(
            row["member_bioguide_id"],
            {
                "member_name": row["member_name"],
                "categories": {},
                "policy_totals": {policy: 0.0 for policy in POLICIES},
            },
        )
        member["categories"][row["category"]] = row["policy_scores"]
        for policy in POLICIES:
            member["policy_totals"][policy] += row["policy_scores"][policy]
    for member in members.values():
        member["categories"] = dict(sorted(member["categories"].items()))
        member["policy_totals"] = {
            policy: round(member["policy_totals"][policy], 12)
            for policy in POLICIES
        }
    return {
        "schema": TOTALS_SCHEMA,
        "selected_policy": None,
        "final_ranks_computed": False,
        "policies": {
            policy: ROLE_WEIGHT_POLICY_SCENARIOS[policy]
            for policy in POLICIES
        },
        "decay_weights": {
            str(congress): weight
            for congress, weight in sorted(DECAY_WEIGHT.items())
        },
        "measure_weight_policy": measure_weight_policy(),
        "members": dict(sorted(members.items())),
    }


def pairwise_deltas(
    member_categories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member_row in member_categories:
        scores = member_row["policy_scores"]
        for left, right in itertools.combinations(POLICIES, 2):
            left_score = scores[left]
            right_score = scores[right]
            rows.append(
                {
                    "schema": PAIRWISE_SCHEMA,
                    "member_bioguide_id": member_row["member_bioguide_id"],
                    "member_name": member_row["member_name"],
                    "category": member_row["category"],
                    "left_policy": left,
                    "right_policy": right,
                    "left_score": left_score,
                    "right_score": right_score,
                    "delta_right_minus_left": round(
                        right_score - left_score, 12
                    ),
                    "score_sign_conflict": (
                        (left_score < 0 < right_score)
                        or (right_score < 0 < left_score)
                        or ((left_score == 0) != (right_score == 0))
                    ),
                }
            )
    return rows


def conflict_diagnostics(
    events: list[dict[str, Any]],
    member_categories: list[dict[str, Any]],
    pairwise: list[dict[str, Any]],
) -> dict[str, Any]:
    pairs: dict[str, dict[str, Any]] = {}
    for left, right in itertools.combinations(POLICIES, 2):
        subset = [
            row
            for row in pairwise
            if row["left_policy"] == left and row["right_policy"] == right
        ]
        changed = [
            row for row in subset if row["delta_right_minus_left"] != 0
        ]
        maximum = max(
            changed,
            key=lambda row: (
                abs(row["delta_right_minus_left"]),
                row["member_bioguide_id"],
                row["category"],
            ),
            default=None,
        )
        pairs[f"{left}__vs__{right}"] = {
            "member_category_count": len(subset),
            "changed_member_category_count": len(changed),
            "score_sign_conflict_count": sum(
                row["score_sign_conflict"] for row in subset
            ),
            "sum_absolute_delta": round(
                math.fsum(abs(row["delta_right_minus_left"]) for row in subset),
                12,
            ),
            "max_absolute_delta": (
                abs(maximum["delta_right_minus_left"]) if maximum else 0.0
            ),
            "max_delta_member_category": (
                {
                    "member_bioguide_id": maximum["member_bioguide_id"],
                    "category": maximum["category"],
                    "delta_right_minus_left": maximum[
                        "delta_right_minus_left"
                    ],
                }
                if maximum
                else None
            ),
        }
    return {
        "schema": "legislation-policy-conflict-diagnostics-v1",
        "policy_selection_resolved": False,
        "promotion_policy_selected": None,
        "final_rank_claim": False,
        "active_role_counts": dict(
            sorted(
                Counter(
                    event["role"]
                    for event in events
                    if event["activity_included"]
                ).items()
            )
        ),
        "zero_weight_reason_counts": dict(
            sorted(
                Counter(
                    event["zero_weight_reason"]
                    for event in events
                    if event["zero_weight_reason"]
                ).items()
            )
        ),
        "member_category_count": len(member_categories),
        "pairwise": pairs,
    }


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ScenarioError(f"baseline scenario does not exist: {path}")
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ScenarioError(f"invalid baseline JSON: {error}") from error
    if not isinstance(baseline, dict) or baseline.get("schema") != TOTALS_SCHEMA:
        raise ScenarioError(
            f"baseline must use schema {TOTALS_SCHEMA!r}"
        )
    expected_policies = {
        policy: ROLE_WEIGHT_POLICY_SCENARIOS[policy] for policy in POLICIES
    }
    if baseline.get("policies") != expected_policies:
        raise ScenarioError(
            "baseline role-weight policies do not match current scenarios"
        )
    expected_decay = {
        str(congress): weight
        for congress, weight in sorted(DECAY_WEIGHT.items())
    }
    if baseline.get("decay_weights") != expected_decay:
        raise ScenarioError(
            "baseline decay weights do not match current scenarios"
        )
    if baseline.get("measure_weight_policy") != measure_weight_policy():
        raise ScenarioError(
            "baseline measure-weight policy does not match current scenarios"
        )
    if not isinstance(baseline.get("members"), dict):
        raise ScenarioError("baseline members must be an object")
    return baseline


def baseline_deltas(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_members = current["members"]
    baseline_members = baseline["members"]
    for bioguide in sorted(set(current_members) | set(baseline_members)):
        current_member = current_members.get(bioguide, {})
        baseline_member = baseline_members.get(bioguide, {})
        current_name = current_member.get("member_name")
        baseline_name = baseline_member.get("member_name")
        if current_name and baseline_name and current_name != baseline_name:
            raise ScenarioError(
                f"baseline/current member name mismatch for {bioguide}"
            )
        current_totals = current_member.get("policy_totals", {})
        baseline_totals = baseline_member.get("policy_totals", {})
        categories = sorted(
            set(current_member.get("categories", {}))
            | set(baseline_member.get("categories", {}))
        )
        for policy in POLICIES:
            current_total = float(current_totals.get(policy, 0.0))
            baseline_total = float(baseline_totals.get(policy, 0.0))
            category_values = {}
            for category in categories:
                current_value = float(
                    current_member.get("categories", {})
                    .get(category, {})
                    .get(policy, 0.0)
                )
                baseline_value = float(
                    baseline_member.get("categories", {})
                    .get(category, {})
                    .get(policy, 0.0)
                )
                category_values[category] = round(
                    current_value - baseline_value, 12
                )
            rows.append(
                {
                    "schema": BASELINE_DELTA_SCHEMA,
                    "member_bioguide_id": bioguide,
                    "member_name": current_name or baseline_name,
                    "policy": policy,
                    "current_total": current_total,
                    "baseline_total": baseline_total,
                    "delta_current_minus_baseline": round(
                        current_total - baseline_total, 12
                    ),
                    "category_deltas": category_values,
                }
            )
    return rows


def compute(
    *,
    accepted_ledger: Path,
    scoring_directions: Path,
    out: Path,
    baseline_scenario: Path | None = None,
) -> dict[str, Any]:
    accepted_path = accepted_ledger.resolve()
    directions_path = scoring_directions.resolve()
    baseline_path = baseline_scenario.resolve() if baseline_scenario else None
    inputs = [accepted_path, directions_path]
    if baseline_path:
        inputs.append(baseline_path)
    output_dir = validate_out(out, inputs)
    accepted_rows = read_ndjson(accepted_path)
    direction_rows = read_ndjson(directions_path)
    directions = load_directions(direction_rows)
    events = build_events(accepted_rows, directions)
    member_categories = aggregate_member_categories(events)
    totals = build_totals(member_categories)
    pairwise = pairwise_deltas(member_categories)
    diagnostics = conflict_diagnostics(events, member_categories, pairwise)
    baseline = load_baseline(baseline_path) if baseline_path else None
    baseline_rows = baseline_deltas(totals, baseline) if baseline else []

    paths = {
        "role_activity_events": output_dir / "role_activity_events.ndjson",
        "member_category_scenarios": (
            output_dir / "member_category_policy_scenarios.ndjson"
        ),
        "pairwise_deltas": output_dir / "policy_pairwise_deltas.ndjson",
        "scenario_totals": output_dir / "scenario_totals.json",
        "conflict_diagnostics": (
            output_dir / "policy_conflict_diagnostics.json"
        ),
    }
    write_ndjson(paths["role_activity_events"], events)
    write_ndjson(paths["member_category_scenarios"], member_categories)
    write_ndjson(paths["pairwise_deltas"], pairwise)
    write_json(paths["scenario_totals"], totals)
    write_json(paths["conflict_diagnostics"], diagnostics)
    if baseline_path:
        paths["baseline_member_deltas"] = (
            output_dir / "baseline_member_deltas.ndjson"
        )
        write_ndjson(paths["baseline_member_deltas"], baseline_rows)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "status": "complete",
        "scenario_computation_complete": True,
        "staged_only": True,
        "production_artifacts_written": False,
        "workbook_written": False,
        "final_ranks_computed": False,
        "promotion_policy_selected": None,
        "promotion_ready": False,
        "promotion_blockers": ["role_weight_policy_unresolved"],
        "inputs": {
            "accepted_ledger": receipt(accepted_path),
            "scoring_directions": receipt(directions_path),
            "baseline_scenario": receipt(baseline_path)
            if baseline_path
            else None,
        },
        "policies": {
            policy: ROLE_WEIGHT_POLICY_SCENARIOS[policy]
            for policy in POLICIES
        },
        "decay_weights": {
            str(congress): weight
            for congress, weight in sorted(DECAY_WEIGHT.items())
        },
        "measure_weight_policy": measure_weight_policy(),
        "strongest_role_rule": (
            "sponsor > original_cosponsor > cosponsor per "
            "type-aware measure/category/member/status"
        ),
        "excluded_and_withdrawn_contribution": 0.0,
        "direction_coverage_complete": True,
        "counts": {
            "accepted_input_rows": len(accepted_rows),
            "direction_edges": len(directions),
            "role_activity_events": len(events),
            "active_role_activity_events": sum(
                event["activity_included"] for event in events
            ),
            "member_category_rows": len(member_categories),
            "pairwise_delta_rows": len(pairwise),
            "baseline_member_delta_rows": len(baseline_rows),
        },
        "outputs": {
            name: receipt(path) for name, path in sorted(paths.items())
        },
        "producer": receipt(Path(__file__).resolve()),
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    args = parse_args()
    try:
        manifest = compute(
            accepted_ledger=args.accepted_ledger,
            scoring_directions=args.scoring_directions,
            baseline_scenario=args.baseline_scenario,
            out=args.out,
        )
    except (ScenarioError, OSError, json.JSONDecodeError) as error:
        print(f"compute_legislation_policy_scenarios: FAIL CLOSED: {error}")
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
