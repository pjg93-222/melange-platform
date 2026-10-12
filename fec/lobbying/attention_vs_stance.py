# -*- coding: utf-8 -*-
"""Describe whether lobbying attention falls on CU allies or adversaries.

This is DESCRIPTIVE.  It is not a hypothesis test.  Spearman p-values are
reported but are not interpreted as confirmation of a hypothesis.  Correlation
cannot establish direction: an organization may lobby allies because they are
allies, or members may become allies because they were lobbied.

The authoritative join is champions bioguide_id to congestion bioguide, then
congestion member to the exact display-name keys in both stance inputs.  Names
are not normalized and never serve as the primary join key.

The public-record measures in say_do_gap are specific to credit-union issues,
so they are a sharper instrument for credit-union organizations than for bank
organizations.  That asymmetry must be considered when comparing strata.
SAY-DO quadrant is consistency (talker/aligned/doer), not friendly-versus-
hostile direction, and is never used to label allies or adversaries here.

Directional stance comes from category_stance scores.  A score of 0.0 whose
matrix tier is the em dash ("—") means no position was measured, not neutral;
that cell is excluded.  At least three measured categories are required for a
member stance, and missing stance is never imputed.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import collections
import csv
import hashlib
import io
import json
import math
import os
import statistics
import sys

from scipy import stats

import members.historical_member_names as identity


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


NO_POSITION_TIER = "—"
MIN_CATEGORY_COUNT = 3
QUADRANTS = ("talker", "aligned", "doer")
SPEARMAN_FIELDS = (
    "stance_mean",
    "stance_defensive_mean",
    "say_z",
    "do_z",
    "gap",
    "sponsored_cu",
)
METHOD_NOTE = (
    "This analysis is DESCRIPTIVE, is not a hypothesis test, and makes no "
    "causal claim. Spearman p-values are reported but are not interpreted as "
    "confirmation of a hypothesis. Correlation cannot establish direction: "
    "an organization may lobby allies because they are allies, or members may "
    "become allies because they were lobbied. The public-record measures in "
    "say_do_gap are measured on credit-union issues specifically, so they are "
    "a sharper instrument for credit-union organizations than for bank "
    "organizations; that asymmetry must be considered when comparing strata. "
    "SAY-DO quadrant measures consistency (talker/aligned/doer), not whether a "
    "member is friendly. Directional stance is the mean category_stance score "
    "over measured categories. A 0.0 category score paired with matrix tier "
    "'—' is unmeasured, not neutral, and is excluded. Members with fewer than "
    "three measured categories have null stance and are excluded from the "
    "directional 2x2 without imputation. Attention is split at the within-org "
    "median: scores equal to the median are high attention. The authoritative "
    "join is exact champions bioguide_id to congestion bioguide, followed by "
    "the congestion member display name as an exact key into both stance "
    "inputs; names are not normalized or used as the primary join key."
)
MEMBER_FIELDS = [
    "bioguide_id",
    "member_name",
    "congestion_member_name",
    "champion_rank",
    "champion_score",
    "sector_rank",
    "sector_relative_score",
    "attention_median",
    "attention_level",
    "category_stance_name",
    "say_do_gap_name",
    "stance_mean",
    "stance_defensive_mean",
    "n_categories_with_data",
    "n_defensive_categories_with_data",
    "stance_direction",
    "primary_cell_label",
    "say_z",
    "do_z",
    "gap",
    "sponsored_cu",
    "quadrant",
    "say_do_consistency_cell",
]
FLAT_FIELDS = [
    "lob_id",
    "org_name",
    "org_side",
    "n_champion_members",
    "min_members",
    "skipped_reason",
    "attention_median",
    "excluded_missing_stance_n",
    "excluded_missing_stance_samples",
    "excluded_fewer_than_3_categories_n",
    "excluded_fewer_than_3_categories_samples",
    "excluded_missing_quadrant_n",
    "excluded_missing_quadrant_samples",
]
for _metric in SPEARMAN_FIELDS:
    FLAT_FIELDS.extend(
        [
            _metric + "_rho",
            _metric + "_p",
            _metric + "_n",
            _metric + "_excluded_missing_value",
            _metric + "_excluded_missing_stance",
        ]
    )
for _attention in ("high", "low"):
    for _direction in ("supportive", "hostile"):
        FLAT_FIELDS.extend(
            [
                _attention + "_" + _direction + "_label",
                _attention + "_" + _direction + "_n",
                _attention + "_" + _direction + "_names",
            ]
        )
for _attention in ("high", "low"):
    for _quadrant in QUADRANTS:
        FLAT_FIELDS.extend(
            [
                _attention + "_" + _quadrant + "_n",
                _attention + "_" + _quadrant + "_names",
            ]
        )
FLAT_FIELDS.extend(
    [
        "top_25_n_considered",
        "top_25_talker_n",
        "top_25_aligned_n",
        "top_25_doer_n",
        "top_25_missing_n",
    ]
)


class InstrumentError(Exception):
    """An input or design failure that must be reported rather than guessed."""


def _read_bytes(path, label):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise InstrumentError(f"cannot read {label} {path}: {exc}") from exc


def _sha256(data):
    return hashlib.sha256(data).hexdigest().upper()


def _decode(data, label):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InstrumentError(f"{label} is not valid UTF-8: {exc}") from exc


def _load_json(path, label):
    data = _read_bytes(path, label)
    try:
        value = json.loads(_decode(data, label))
    except json.JSONDecodeError as exc:
        raise InstrumentError(f"invalid {label} JSON in {path}: {exc}") from exc
    return value, {
        "path": os.path.abspath(path),
        "sha256": _sha256(data),
    }


def _optional_number(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InstrumentError(f"{label} must be numeric or null")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InstrumentError(f"{label} must be numeric or null") from exc
    if not math.isfinite(number):
        raise InstrumentError(f"{label} must be finite")
    return number


def _required_number(value, label):
    number = _optional_number(value, label)
    if number is None:
        raise InstrumentError(f"{label} is required")
    return number


def load_champions(path):
    data = _read_bytes(path, "champions CSV")
    reader = csv.DictReader(io.StringIO(_decode(data, "champions CSV"), newline=""))
    required = {
        "lob_id",
        "org_name",
        "org_side",
        "bioguide_id",
        "member_name",
        "champion_rank",
        "champion_score",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise InstrumentError(
            f"champions CSV missing required fields: {sorted(required)}"
        )
    rows = []
    seen = set()
    org_metadata = {}
    identities = {}
    for row_number, source in enumerate(reader, start=2):
        if None in source:
            raise InstrumentError(
                f"champions CSV row {row_number} has extra columns"
            )
        lob_id = (source.get("lob_id") or "").strip()
        bioguide = source.get("bioguide_id") or ""
        if bioguide != bioguide.strip():
            raise InstrumentError(
                f"champions CSV row {row_number} has noncanonical bioguide whitespace"
            )
        if not lob_id or not bioguide:
            raise InstrumentError(
                f"champions CSV row {row_number} has blank identity"
            )
        key = (lob_id, bioguide)
        if key in seen:
            raise InstrumentError(
                f"champions CSV duplicate organization-member row {key!r}"
            )
        seen.add(key)
        member_name = (source.get("member_name") or "").strip()
        if bioguide in identities:
            if identities[bioguide]["member_name"] != member_name:
                raise InstrumentError(
                    f"champions CSV gives {bioguide} inconsistent names"
                )
        else:
            identities[bioguide] = {
                "bioguide_id": bioguide,
                "member_name": member_name,
            }
        metadata = (
            (source.get("org_name") or "").strip() or None,
            (source.get("org_side") or "").strip() or None,
        )
        if lob_id in org_metadata and org_metadata[lob_id] != metadata:
            raise InstrumentError(
                f"champions CSV gives {lob_id} inconsistent organization metadata"
            )
        org_metadata[lob_id] = metadata
        champion_rank = _required_number(
            source.get("champion_rank"),
            f"champions row {row_number} champion_rank",
        )
        if champion_rank < 1 or not champion_rank.is_integer():
            raise InstrumentError(
                f"champions row {row_number} champion_rank is not a positive integer"
            )
        row = dict(source)
        row.update(
            {
                "lob_id": lob_id,
                "bioguide_id": bioguide,
                "member_name": member_name,
                "org_name": metadata[0],
                "org_side": metadata[1],
                "champion_rank": int(champion_rank),
                "champion_score": _required_number(
                    source.get("champion_score"),
                    f"champions row {row_number} champion_score",
                ),
                "sector_rank": _optional_number(
                    source.get("sector_rank"),
                    f"champions row {row_number} sector_rank",
                ),
                "sector_relative_score": _optional_number(
                    source.get("sector_relative_score"),
                    f"champions row {row_number} sector_relative_score",
                ),
            }
        )
        rows.append(row)
    if not rows:
        raise InstrumentError("champions CSV is empty")
    rows.sort(
        key=lambda row: (
            row["lob_id"],
            row["champion_rank"],
            row["bioguide_id"],
        )
    )
    return rows, identities, {
        "path": os.path.abspath(path),
        "sha256": _sha256(data),
        "rows": len(rows),
        "members": len(identities),
        "organizations": len(org_metadata),
    }


def load_congestion(path):
    """Load the model's canonical display-name/bioguide crosswalk.

    Field access deliberately mirrors hearing_antagonism_build.py: records are
    keyed by ``m['member']`` and bioguide is read with ``m.get('bioguide')``.
    """
    document, provenance = _load_json(path, "congestion")
    if not isinstance(document, list):
        raise InstrumentError("congestion root must be a list")
    for index, record in enumerate(document):
        if not isinstance(record, dict):
            raise InstrumentError(
                f"congestion record {index} must be an object"
            )
        if "member" not in record:
            raise InstrumentError(
                f"congestion record {index} is missing member"
            )
        if not isinstance(record["member"], str) or not record["member"]:
            raise InstrumentError(
                f"congestion record {index} has invalid member"
            )
    member_counts = collections.Counter(record["member"] for record in document)
    duplicate_members = sorted(
        member for member, count in member_counts.items() if count > 1
    )
    if duplicate_members:
        raise InstrumentError(
            "congestion has duplicate member keys: "
            + repr(duplicate_members[:10])
        )

    Cj = {record["member"]: record for record in document}
    bio = {member: Cj[member].get("bioguide") for member in Cj}
    by_bioguide = {}
    for member in sorted(Cj):
        bioguide = bio[member]
        if bioguide is not None and not isinstance(bioguide, str):
            raise InstrumentError(
                f"congestion member {member!r} has non-text bioguide"
            )
        if isinstance(bioguide, str) and bioguide != bioguide.strip():
            raise InstrumentError(
                f"congestion member {member!r} has noncanonical bioguide whitespace"
            )
        if not bioguide:
            continue
        if bioguide in by_bioguide:
            raise InstrumentError(
                "congestion bioguide maps to multiple members: "
                + repr(bioguide)
            )
        by_bioguide[bioguide] = {
            "member": member,
            "bioguide": bioguide,
        }
    provenance.update(
        {
            "records": len(document),
            "members": len(Cj),
            "members_with_bioguide": len(by_bioguide),
            "member_field": "member",
            "bioguide_field": "bioguide",
        }
    )
    return by_bioguide, Cj, provenance


def load_say_do_gap(path):
    document, provenance = _load_json(path, "say_do_gap")
    if not isinstance(document, dict) or not isinstance(document.get("members"), dict):
        raise InstrumentError("say_do_gap must contain a member-keyed 'members' object")
    parsed = {}
    for name, source in document["members"].items():
        if not isinstance(source, dict):
            raise InstrumentError(f"say_do_gap member {name!r} is not an object")
        quadrant = source.get("quadrant")
        if quadrant is not None and quadrant not in QUADRANTS:
            raise InstrumentError(
                f"say_do_gap member {name!r} has unknown quadrant {quadrant!r}"
            )
        sponsored = _optional_number(
            source.get("sponsored_cu"), f"say_do_gap {name!r} sponsored_cu"
        )
        if sponsored is not None and (
            sponsored < 0 or not sponsored.is_integer()
        ):
            raise InstrumentError(
                f"say_do_gap member {name!r} sponsored_cu must be a nonnegative integer"
            )
        parsed[name] = {
            "source_name": name,
            "say_z": _optional_number(source.get("say_z"), f"{name!r} say_z"),
            "do_z": _optional_number(source.get("do_z"), f"{name!r} do_z"),
            "gap": _optional_number(source.get("gap"), f"{name!r} gap"),
            "sponsored_cu": None if sponsored is None else int(sponsored),
            "quadrant": quadrant,
        }
    provenance.update(
        {
            "members": len(parsed),
            "member_axis": "members.<display name>",
        }
    )
    return parsed, provenance


def load_category_stance(path):
    document, provenance = _load_json(path, "category_stance")
    if not isinstance(document, dict):
        raise InstrumentError("category_stance root must be an object")
    cats = document.get("cats")
    matrix = document.get("matrix")
    scores = document.get("scores")
    defensive = document.get("defensive")
    if (
        not isinstance(cats, list)
        or len(cats) != 18
        or any(not isinstance(cat, str) or not cat for cat in cats)
        or len(set(cats)) != len(cats)
    ):
        raise InstrumentError("category_stance cats must be 18 unique names")
    if not isinstance(matrix, dict) or not isinstance(scores, dict):
        raise InstrumentError("category_stance matrix and scores must be objects")
    if set(matrix) != set(scores):
        raise InstrumentError(
            "category_stance matrix and scores member axes do not match"
        )
    if (
        not isinstance(defensive, list)
        or len(defensive) != 3
        or any(not isinstance(cat, str) for cat in defensive)
        or len(set(defensive)) != len(defensive)
        or not set(defensive).issubset(cats)
    ):
        raise InstrumentError(
            "category_stance defensive must be three unique names from cats"
        )
    parsed = {}
    cat_set = set(cats)
    defensive_set = set(defensive)
    for name in sorted(matrix):
        tiers = matrix[name]
        member_scores = scores[name]
        if not isinstance(tiers, dict) or not isinstance(member_scores, dict):
            raise InstrumentError(
                f"category_stance member {name!r} axes must be objects"
            )
        if set(tiers) != cat_set or set(member_scores) != cat_set:
            raise InstrumentError(
                f"category_stance member {name!r} does not mirror cats exactly"
            )
        measured = []
        defensive_measured = []
        for cat in cats:
            tier = tiers[cat]
            if not isinstance(tier, str):
                raise InstrumentError(
                    f"category_stance matrix {name!r}/{cat!r} is not text"
                )
            score = _required_number(
                member_scores[cat],
                f"category_stance score {name!r}/{cat!r}",
            )
            if score < -1.0 or score > 1.0:
                raise InstrumentError(
                    f"category_stance score {name!r}/{cat!r} is outside [-1, 1]"
                )
            if tier == NO_POSITION_TIER:
                if score != 0.0:
                    raise InstrumentError(
                        "category_stance em-dash/no-position cell has nonzero "
                        + f"score for {name!r}/{cat!r}"
                    )
                continue
            measured.append(score)
            if cat in defensive_set:
                defensive_measured.append(score)
        parsed[name] = {
            "source_name": name,
            "stance_mean": (
                None
                if len(measured) < MIN_CATEGORY_COUNT
                else sum(measured) / len(measured)
            ),
            "stance_defensive_mean": (
                None
                if not defensive_measured
                else sum(defensive_measured) / len(defensive_measured)
            ),
            "n_categories_with_data": len(measured),
            "n_defensive_categories_with_data": len(defensive_measured),
        }
    provenance.update(
        {
            "member_axis": (
                "matrix.<display name>.<category> and "
                "scores.<display name>.<category>"
            ),
            "members": len(parsed),
            "cats": list(cats),
            "cats_count": len(cats),
            "defensive": list(defensive),
            "no_position_rule": (
                "matrix tier '—' paired with score 0.0 is unmeasured and "
                "excluded, not neutral"
            ),
            "minimum_measured_categories_for_stance": MIN_CATEGORY_COUNT,
        }
    )
    return parsed, provenance


def _receipt_values(values):
    ordered = sorted(values)
    return {"count": len(ordered), "samples": ordered[:10]}


def make_bioguide_join_receipts(
    identities,
    congestion_by_bioguide,
    congestion_by_member,
    say_do,
    category,
):
    champion_bioguides = set(identities)
    congestion_bioguides = set(congestion_by_bioguide)
    matched = champion_bioguides & congestion_bioguides
    missing_champions = [
        bioguide + " | " + (identities[bioguide]["member_name"] or "<blank>")
        for bioguide in champion_bioguides - congestion_bioguides
    ]
    congestion_not_champions = []
    for member in sorted(congestion_by_member):
        bioguide = congestion_by_member[member].get("bioguide")
        if bioguide not in champion_bioguides:
            congestion_not_champions.append(
                (bioguide or "<missing bioguide>") + " | " + member
            )
    missing_say_do = [
        member for member in congestion_by_member if member not in say_do
    ]
    missing_category = [
        member for member in congestion_by_member if member not in category
    ]
    denominator = len(champion_bioguides)
    return {
        "join_key": "exact bioguide",
        "champions_members": denominator,
        "congestion_members": len(congestion_by_member),
        "matched_bioguides": len(matched),
        "champions_bioguide_match_rate": (
            0.0 if denominator == 0 else len(matched) / denominator
        ),
        "champions_bioguide_absent_from_congestion": _receipt_values(
            missing_champions
        ),
        "congestion_bioguide_absent_from_champions": _receipt_values(
            congestion_not_champions
        ),
        "congestion_display_name_absent_from_say_do_gap": _receipt_values(
            missing_say_do
        ),
        "congestion_display_name_absent_from_category_stance": _receipt_values(
            missing_category
        ),
    }


def _rounded(value, digits=12):
    if value is None:
        return None
    if not math.isfinite(float(value)):
        return None
    result = round(float(value), digits)
    return 0.0 if result == 0.0 else result


def attach_member_records(
    champion_rows, congestion_by_bioguide, say_do, category, resolver=None
):
    output = []
    for source in champion_rows:
        row = dict(source)
        # A champions row can name a member who has since left office, whom the
        # current roster cannot name.  The resolver supplies the historical name
        # and the former-member marking; without one the row carries no
        # historical display and _member_display still degrades visibly.
        row["historical_member_display"] = (
            None
            if resolver is None
            else resolver.resolve(row["bioguide_id"])["display"]
        )
        congestion_row = congestion_by_bioguide.get(row["bioguide_id"])
        congestion_name = (
            None if congestion_row is None else congestion_row["member"]
        )
        category_row = category.get(congestion_name)
        say_do_row = say_do.get(congestion_name)
        row.update(
            {
                "congestion_member_name": congestion_name,
                "category_stance_name": (
                    None if category_row is None else category_row["source_name"]
                ),
                "say_do_gap_name": (
                    None if say_do_row is None else say_do_row["source_name"]
                ),
                "stance_mean": (
                    None if category_row is None else category_row["stance_mean"]
                ),
                "stance_defensive_mean": (
                    None
                    if category_row is None
                    else category_row["stance_defensive_mean"]
                ),
                "n_categories_with_data": (
                    0
                    if category_row is None
                    else category_row["n_categories_with_data"]
                ),
                "n_defensive_categories_with_data": (
                    0
                    if category_row is None
                    else category_row["n_defensive_categories_with_data"]
                ),
                "say_z": None if say_do_row is None else say_do_row["say_z"],
                "do_z": None if say_do_row is None else say_do_row["do_z"],
                "gap": None if say_do_row is None else say_do_row["gap"],
                "sponsored_cu": (
                    None if say_do_row is None else say_do_row["sponsored_cu"]
                ),
                "quadrant": (
                    None if say_do_row is None else say_do_row["quadrant"]
                ),
            }
        )
        output.append(row)
    return output


def spearman_result(rows, field, missing_stance):
    pairs = [
        (row["champion_score"], row[field])
        for row in rows
        if row.get(field) is not None
    ]
    result = {
        "rho": None,
        "p": None,
        "n": len(pairs),
        "excluded_missing_value": len(rows) - len(pairs),
        "excluded_missing_stance": missing_stance,
    }
    if len(pairs) < 2:
        result["note"] = "fewer than two complete pairs"
        return result
    x_values = [pair[0] for pair in pairs]
    y_values = [pair[1] for pair in pairs]
    if len(set(x_values)) < 2 or len(set(y_values)) < 2:
        result["note"] = "constant input; Spearman rho and p are undefined"
        return result
    measured = stats.spearmanr(x_values, y_values)
    result["rho"] = _rounded(measured.statistic)
    result["p"] = _rounded(measured.pvalue)
    return result


def _member_display(row):
    """Name a member for a reader, and NEVER hand back a bare identifier.

    The ladder is congestion.json, then the champions member_name, then the
    historical Voteview name attached by ``attach_member_records`` (which also
    marks a departed member), and only then the bioguide with an explicit
    unresolved marker so the failure is visible rather than silent.
    """
    name = (
        row.get("congestion_member_name") or row.get("member_name") or ""
    ).strip()
    if name:
        return name
    resolved = (row.get("historical_member_display") or "").strip()
    if resolved:
        return resolved
    return f"{row['bioguide_id']} ({identity.UNRESOLVED_MARKER})"


def _cell_payload(rows, label):
    ordered = sorted(
        rows,
        key=lambda row: (
            -row["champion_score"],
            _member_display(row),
            row["bioguide_id"],
        ),
    )
    return {
        "label": label,
        "n": len(ordered),
        "names": [_member_display(row) for row in ordered[:15]],
        "names_capped_at": 15,
    }


def _receipt_rows(rows):
    ordered = sorted(
        rows,
        key=lambda row: (
            _member_display(row),
            row["bioguide_id"],
        ),
    )
    return {
        "count": len(ordered),
        "samples": [_member_display(row) for row in ordered[:10]],
    }


def analyze_organization_rows(rows, min_members=25):
    if min_members < 1:
        raise InstrumentError("min_members must be positive")
    rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: (row["champion_rank"], row["bioguide_id"]),
    )
    if not rows:
        raise InstrumentError("cannot analyze an organization with no rows")
    first = rows[0]
    entry = {
        "lob_id": first["lob_id"],
        "org_name": first.get("org_name"),
        "org_side": first.get("org_side"),
        "n_champion_members": len(rows),
        "min_members": min_members,
        "skipped_reason": None,
        "members": [],
    }
    if len(rows) < min_members:
        entry["skipped_reason"] = (
            f"champion_members={len(rows)} below min_members={min_members}"
        )
        return entry, []

    median = float(statistics.median(row["champion_score"] for row in rows))
    missing_stance_rows = [row for row in rows if row.get("stance_mean") is None]
    insufficient_rows = [
        row
        for row in rows
        if row.get("category_stance_name") is not None
        and row.get("n_categories_with_data", 0) < MIN_CATEGORY_COUNT
    ]
    for row in rows:
        row["attention_median"] = median
        row["attention_level"] = (
            "high" if row["champion_score"] >= median else "low"
        )
        if row.get("stance_mean") is None:
            row["stance_direction"] = None
            row["primary_cell_label"] = None
        else:
            row["stance_direction"] = (
                "supportive" if row["stance_mean"] >= 0.0 else "hostile"
            )
            if row["attention_level"] == "high":
                row["primary_cell_label"] = (
                    "worked allies"
                    if row["stance_direction"] == "supportive"
                    else "worked adversaries"
                )
            else:
                row["primary_cell_label"] = (
                    "low-attention supportive"
                    if row["stance_direction"] == "supportive"
                    else "low-attention hostile"
                )
        row["say_do_consistency_cell"] = (
            None
            if row.get("quadrant") is None
            else row["attention_level"] + "/" + row["quadrant"]
        )

    primary_cells = {}
    primary_labels = {
        ("high", "supportive"): "worked allies",
        ("high", "hostile"): "worked adversaries",
        ("low", "supportive"): "low-attention supportive",
        ("low", "hostile"): "low-attention hostile",
    }
    for attention in ("high", "low"):
        primary_cells[attention] = {}
        for direction in ("supportive", "hostile"):
            cell_rows = [
                row
                for row in rows
                if row["attention_level"] == attention
                and row["stance_direction"] == direction
            ]
            primary_cells[attention][direction] = _cell_payload(
                cell_rows, primary_labels[(attention, direction)]
            )

    consistency_cells = {}
    for attention in ("high", "low"):
        consistency_cells[attention] = {}
        for quadrant in QUADRANTS:
            cell_rows = [
                row
                for row in rows
                if row["attention_level"] == attention
                and row.get("quadrant") == quadrant
            ]
            consistency_cells[attention][quadrant] = _cell_payload(
                cell_rows, attention + " attention / " + quadrant
            )

    missing_quadrant_rows = [row for row in rows if row.get("quadrant") is None]
    top = rows[:25]
    top_counts = collections.Counter(
        row["quadrant"] for row in top if row.get("quadrant") in QUADRANTS
    )
    entry.update(
        {
            "attention_median": _rounded(median),
            "attention_split_rule": (
                "high if champion_score >= within-org median; low otherwise"
            ),
            "spearman": {
                field: spearman_result(rows, field, len(missing_stance_rows))
                for field in SPEARMAN_FIELDS
            },
            "primary_attention_by_stance": {
                "kind": "2x2",
                "headline": True,
                "stance_rule": "supportive if stance_mean >= 0; hostile if < 0",
                "cells": primary_cells,
                "excluded_missing_stance": _receipt_rows(missing_stance_rows),
                "excluded_fewer_than_3_measured_categories": _receipt_rows(
                    insufficient_rows
                ),
            },
            "secondary_attention_by_say_do_consistency": {
                "kind": "2x3",
                "headline": False,
                "note": (
                    "SAY-DO consistency says nothing about whether the member "
                    "is friendly; it only describes whether words and "
                    "legislative behavior match."
                ),
                "cells": consistency_cells,
                "excluded_missing_quadrant": _receipt_rows(
                    missing_quadrant_rows
                ),
            },
            "top_25_say_do_quadrants": {
                "n_top_champions_considered": len(top),
                "talker": top_counts["talker"],
                "aligned": top_counts["aligned"],
                "doer": top_counts["doer"],
                "missing": sum(row.get("quadrant") is None for row in top),
            },
        }
    )

    entry["members"] = [
        {
            field: (
                _rounded(row.get(field))
                if field
                in {
                    "champion_score",
                    "sector_relative_score",
                    "attention_median",
                    "stance_mean",
                    "stance_defensive_mean",
                    "say_z",
                    "do_z",
                    "gap",
                }
                else row.get(field)
            )
            for field in MEMBER_FIELDS
        }
        for row in rows
    ]
    return entry, rows


def flatten_org_entry(entry):
    """Flatten one per-organization JSON result into one deterministic row."""
    row = {
        "lob_id": entry["lob_id"],
        "org_name": entry.get("org_name"),
        "org_side": entry.get("org_side"),
        "n_champion_members": entry["n_champion_members"],
        "min_members": entry["min_members"],
        "skipped_reason": entry["skipped_reason"],
        "attention_median": entry.get("attention_median"),
    }
    if entry["skipped_reason"] is not None:
        return row
    primary = entry["primary_attention_by_stance"]
    consistency = entry["secondary_attention_by_say_do_consistency"]
    missing_stance = primary["excluded_missing_stance"]
    insufficient = primary["excluded_fewer_than_3_measured_categories"]
    missing_quadrant = consistency["excluded_missing_quadrant"]
    row.update(
        {
            "excluded_missing_stance_n": missing_stance["count"],
            "excluded_missing_stance_samples": json.dumps(
                missing_stance["samples"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "excluded_fewer_than_3_categories_n": insufficient["count"],
            "excluded_fewer_than_3_categories_samples": json.dumps(
                insufficient["samples"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "excluded_missing_quadrant_n": missing_quadrant["count"],
            "excluded_missing_quadrant_samples": json.dumps(
                missing_quadrant["samples"],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
    )
    for metric in SPEARMAN_FIELDS:
        result = entry["spearman"][metric]
        row[metric + "_rho"] = result["rho"]
        row[metric + "_p"] = result["p"]
        row[metric + "_n"] = result["n"]
        row[metric + "_excluded_missing_value"] = result[
            "excluded_missing_value"
        ]
        row[metric + "_excluded_missing_stance"] = result[
            "excluded_missing_stance"
        ]
    for attention in ("high", "low"):
        for direction in ("supportive", "hostile"):
            cell = primary["cells"][attention][direction]
            prefix = attention + "_" + direction
            row[prefix + "_label"] = cell["label"]
            row[prefix + "_n"] = cell["n"]
            row[prefix + "_names"] = json.dumps(
                cell["names"], ensure_ascii=False, separators=(",", ":")
            )
    for attention in ("high", "low"):
        for quadrant in QUADRANTS:
            cell = consistency["cells"][attention][quadrant]
            prefix = attention + "_" + quadrant
            row[prefix + "_n"] = cell["n"]
            row[prefix + "_names"] = json.dumps(
                cell["names"], ensure_ascii=False, separators=(",", ":")
            )
    top = entry["top_25_say_do_quadrants"]
    row.update(
        {
            "top_25_n_considered": top["n_top_champions_considered"],
            "top_25_talker_n": top["talker"],
            "top_25_aligned_n": top["aligned"],
            "top_25_doer_n": top["doer"],
            "top_25_missing_n": top["missing"],
        }
    )
    return row


def build_analysis(
    champion_rows,
    congestion_by_bioguide,
    say_do,
    category,
    min_members=25,
    resolver=None,
):
    attached = attach_member_records(
        champion_rows, congestion_by_bioguide, say_do, category, resolver=resolver
    )
    grouped = collections.defaultdict(list)
    for row in attached:
        grouped[row["lob_id"]].append(row)
    per_org = []
    flat_rows = []
    for lob_id in sorted(grouped):
        entry, analyzed = analyze_organization_rows(
            grouped[lob_id], min_members=min_members
        )
        per_org.append(entry)
        flat_rows.append(flatten_org_entry(entry))
    return per_org, flat_rows


def _atomic_text(path, writer, encoding):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp." + str(os.getpid())
    try:
        with open(temporary, "w", encoding=encoding, newline="") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def write_outputs(out_dir, document, flat_rows):
    json_path = os.path.join(out_dir, "attention_vs_stance.json")
    csv_path = os.path.join(out_dir, "attention_vs_stance.csv")

    def json_writer(handle):
        json.dump(
            document,
            handle,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")

    def csv_writer(handle):
        writer = csv.DictWriter(
            handle,
            fieldnames=FLAT_FIELDS,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for source in flat_rows:
            writer.writerow({field: source.get(field) for field in FLAT_FIELDS})

    _atomic_text(json_path, json_writer, encoding="utf-8")
    _atomic_text(csv_path, csv_writer, encoding="utf-8-sig")
    return json_path, csv_path


def _format_receipt_item(label, payload):
    return (
        label
        + "="
        + str(payload["count"])
        + " "
        + repr(payload["samples"])
    )


def _print_summary(document):
    print("Attention vs stance (descriptive; no causal claim)")
    receipt = document["join_receipts"]
    print(
        f"  exact bioguide: matched {receipt['matched_bioguides']}/"
        + f"{receipt['champions_members']} champion members "
        + f"({receipt['champions_bioguide_match_rate']:.1%})"
    )
    for key in (
        "champions_bioguide_absent_from_congestion",
        "congestion_bioguide_absent_from_champions",
        "congestion_display_name_absent_from_say_do_gap",
        "congestion_display_name_absent_from_category_stance",
    ):
        print("    " + _format_receipt_item(key, receipt[key]))
    for org in document["per_org"]:
        label = org["org_name"] or org["lob_id"]
        if org["skipped_reason"] is not None:
            print(f"  {label}: SKIPPED - {org['skipped_reason']}")
            continue
        primary = org["primary_attention_by_stance"]["cells"]["high"]
        print(
            f"  {label}: worked allies={primary['supportive']['n']}, "
            + f"worked adversaries={primary['hostile']['n']}, "
            + "missing stance="
            + str(
                org["primary_attention_by_stance"]
                ["excluded_missing_stance"]["count"]
            )
        )
        for field in SPEARMAN_FIELDS:
            result = org["spearman"][field]
            rho = "null" if result["rho"] is None else f"{result['rho']:.3f}"
            p_value = "null" if result["p"] is None else f"{result['p']:.3g}"
            print(
                f"    Spearman champion_score vs {field}: "
                + f"rho={rho}, p={p_value}, n={result['n']}"
            )
        quadrants = org["top_25_say_do_quadrants"]
        consistency = org["secondary_attention_by_say_do_consistency"]["cells"]
        print(
            "    SAY-DO consistency 2x3: high "
            + "/".join(
                f"{quadrant}={consistency['high'][quadrant]['n']}"
                for quadrant in QUADRANTS
            )
            + "; low "
            + "/".join(
                f"{quadrant}={consistency['low'][quadrant]['n']}"
                for quadrant in QUADRANTS
            )
        )
        print(
            "    top-25 SAY-DO consistency: "
            + f"talker={quadrants['talker']}, aligned={quadrants['aligned']}, "
            + f"doer={quadrants['doer']}, missing={quadrants['missing']}"
        )


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            "Descriptively compare lobbying attention with members' CU stance."
        )
    )
    parser.add_argument("--champions", required=True, metavar="CSV")
    parser.add_argument(
        "--congestion",
        default=os.path.join(MB_ROOT, "data", "engine", "congestion.json"),
        metavar="JSON",
    )
    parser.add_argument(
        "--say-do-gap",
        default=os.path.join(MB_ROOT, "say_do_gap.json"),
        metavar="JSON",
    )
    parser.add_argument(
        "--category-stance",
        default=os.path.join(MB_ROOT, "category_stance.json"),
        metavar="JSON",
    )
    parser.add_argument("--out-dir", default=".", metavar="DIR")
    parser.add_argument("--min-members", type=int, default=25)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.min_members < 1:
        print("ERROR: --min-members must be positive", file=sys.stderr)
        return 2
    try:
        champion_rows, identities, champion_provenance = load_champions(
            args.champions
        )
        congestion_by_bioguide, congestion_by_member, congestion_provenance = (
            load_congestion(args.congestion)
        )
        say_do, say_do_provenance = load_say_do_gap(args.say_do_gap)
        category, category_provenance = load_category_stance(
            args.category_stance
        )
        receipts = make_bioguide_join_receipts(
            identities,
            congestion_by_bioguide,
            congestion_by_member,
            say_do,
            category,
        )
        if receipts["champions_bioguide_match_rate"] < 0.85:
            print(
                "JOIN RECEIPT: exact bioguide matched "
                + f"{receipts['matched_bioguides']}/"
                + f"{receipts['champions_members']} champion members "
                + f"({receipts['champions_bioguide_match_rate']:.1%})",
                file=sys.stderr,
            )
            for key in (
                "champions_bioguide_absent_from_congestion",
                "congestion_bioguide_absent_from_champions",
                "congestion_display_name_absent_from_say_do_gap",
                "congestion_display_name_absent_from_category_stance",
            ):
                print(
                    "JOIN RECEIPT: "
                    + _format_receipt_item(key, receipts[key]),
                    file=sys.stderr,
                )
            raise InstrumentError(
                "champion-member exact-bioguide join rate below 85%"
            )
        name_resolver = identity.MemberNameResolver.from_paths(
            congestion_path=args.congestion
        )
        per_org, flat_rows = build_analysis(
            champion_rows,
            congestion_by_bioguide,
            say_do,
            category,
            min_members=args.min_members,
            resolver=name_resolver,
        )
        document = {
            "method_note": METHOD_NOTE,
            "provenance": {
                "champions": champion_provenance,
                "congestion": congestion_provenance,
                "member_name_fallback_ladder": {
                    **name_resolver.receipt(),
                    "sources": name_resolver.provenance,
                },
                "say_do_gap": say_do_provenance,
                "category_stance": category_provenance,
            },
            "join_receipts": receipts,
            "per_org": per_org,
        }
        write_outputs(args.out_dir, document, flat_rows)
        _print_summary(document)
    except InstrumentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
