# -*- coding: utf-8 -*-
"""Descriptively rank unusually high organization-to-member bill attention.

This is a DESCRIPTIVE instrument, explicitly not a preregistered hypothesis
test.  It consumes no look, makes no causal claim, and reports no p-values.

The underlying edge means "a filing named a bill this member sponsored or
cosponsored."  A filing naming a member's bill is NOT proof of contact,
agreement, or a relationship.  The residual rank is an association between an
organization's filings and a member's bills after structural adjustment; no
significance may be claimed from it.

FEC contribution data is deliberately NOT a feature.  The preregistered
round-3 test found that money contributes -0.0002 beyond these same structural
controls, so including it would add noise wearing the costume of signal.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import collections
import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import sys

from lobbying import build_network_2021_rebuild as builder
from lobbying import fec_lda_bridge3_look as bridge3
import members.historical_member_names as identity


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


CURRENT_YEAR = 2026
NETWORK_RE = re.compile(r"our_network_([0-9]{4})_strict\.csv$")
# Full-scope family (2026-08-05): champions_fullscope must be computed over
# the SAME strict networks the industry surface renders, or the page's
# every-active-org-has-champions-coverage assertion cannot hold.  The shipped
# 8/2 build silently read the legacy files above; --fullscope restores intent.
FULLSCOPE_NETWORK_RE = re.compile(
    r"our_network_fullscope_([0-9]{4})_strict\.csv$"
)
NETWORK_FIELDS = ["lob_id", "bioguide_id", "year", "n_bills"]
BASE_CONTROLS = (
    "log1p_seniority",
    "log1p_legislative_volume",
    "chamber",
    "party",
)
COMMITTEE_CONTROL = "finance_committee"
CSV_FIELDS = [
    "lob_id",
    "org_name",
    "name_source",
    "org_side",
    "covered_years",
    "controls_used",
    "controls_omitted_reason",
    "bioguide_id",
    "member_name",
    "party",
    "chamber",
    "champion_rank",
    "champion_score",
    "sector_basis",
    "sector_mean_score",
    "sector_n_orgs",
    "sector_relative_score",
    "sector_rank",
    "raw_n_bills",
    "raw_percentile",
    "years_present",
    "first_contact_year",
    "latest_year",
    "trend_sign",
    "direction_of_travel",
    "yearly_n_bills",
    "seniority_years",
    "legislative_volume",
    "finance_committee",
    "observed_log_attention",
    "predicted_log_attention",
    "residual",
    "residual_standard_error",
    "r2",
    "n_members_modeled",
]
METHOD_NOTE = (
    "This is a descriptive residual-attention ranking, not a preregistered "
    "hypothesis test; it consumes no look and makes no causal claim. It ranks "
    "ASSOCIATION between an organization's filings and a member's bills. A "
    "filing naming a member's bill is NOT proof of contact, agreement, or a "
    "relationship: the underlying edge is 'lobbied a bill this member "
    "sponsored or cosponsored.' Residual rank has no p-value, is not a "
    "hypothesis test, and no significance may be claimed from it. Attention "
    "is modeled as log1p of distinct-bill counts after zero-filling every "
    "fec/legislators.csv roster member within each organization's own covered "
    "years. Seniority and legislative volume enter as log1p controls; only "
    "log1p(seniority) is used because returns to tenure are strongly "
    "diminishing. Legislative volume counts distinct BILLSTATUS people-set "
    "bills across every Congress touched by the organization's covered years, "
    "with each Congress counted once. Committee membership enters only when "
    "a pinned snapshot is "
    "available for every Congress touched by that organization's covered "
    "years; otherwise it is omitted and receipted. Studentized here means "
    "the OLS residual divided by the model residual standard error. Raw-count "
    "percentiles use average ranks for ties. Scores must not be compared "
    "across organizations with different control sets without consulting "
    "controls_used. FEC contribution data is deliberately NOT a feature: the "
    "preregistered round-3 test found money contributes -0.0002 beyond these "
    "same structural controls, so including it would add noise wearing the "
    "costume of signal."
)


class InstrumentError(Exception):
    """An input or design failure that must be reported rather than guessed."""


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest().upper()


def _read_bytes(path, label):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise InstrumentError(f"cannot read {label} {path}: {exc}") from exc


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise InstrumentError(f"cannot hash input {path}: {exc}") from exc
    return digest.hexdigest().upper()


def _decode_csv(data, label):
    try:
        return io.StringIO(data.decode("utf-8-sig"), newline="")
    except UnicodeDecodeError as exc:
        raise InstrumentError(f"{label} is not valid UTF-8: {exc}") from exc


def _decode_text(data, label):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InstrumentError(f"{label} is not valid UTF-8: {exc}") from exc


def discover_network_paths(networks_dir, pattern=None):
    """Return year -> strict-network path, discovered afresh each call."""
    pattern = NETWORK_RE if pattern is None else pattern
    try:
        names = os.listdir(networks_dir)
    except OSError as exc:
        raise InstrumentError(
            f"cannot list networks directory {networks_dir}: {exc}"
        ) from exc
    paths = {}
    for name in sorted(names):
        match = pattern.fullmatch(name)
        if match is None:
            continue
        year = int(match.group(1))
        path = os.path.abspath(os.path.join(networks_dir, name))
        if year in paths:
            raise InstrumentError(f"duplicate strict network year {year}")
        paths[year] = path
    if not paths:
        raise InstrumentError(
            f"no our_network_YEAR_strict.csv files in {networks_dir}"
        )
    return paths


def parse_years(tokens, available_years):
    if tokens is None:
        return sorted(available_years)
    pieces = []
    for token in tokens:
        pieces.extend(part.strip() for part in str(token).split(","))
    if not pieces or any(not piece for piece in pieces):
        raise InstrumentError("--years requires one or more four-digit years")
    years = []
    for piece in pieces:
        if re.fullmatch(r"[0-9]{4}", piece) is None:
            raise InstrumentError(f"invalid --years value {piece!r}")
        year = int(piece)
        if year not in available_years:
            raise InstrumentError(
                f"requested year {year} has no strict network file"
            )
        years.append(year)
    if len(years) != len(set(years)):
        raise InstrumentError("--years contains a duplicate year")
    return sorted(years)


def read_networks(paths_by_year, years):
    """Read and hash exactly the bytes consumed during this invocation."""
    org_year = collections.defaultdict(dict)
    provenance = []
    for year in sorted(years):
        path = paths_by_year[year]
        data = _read_bytes(path, "strict network")
        provenance.append(
            {
                "year": year,
                "path": os.path.abspath(path),
                "sha256": _sha256_bytes(data),
            }
        )
        reader = csv.DictReader(_decode_csv(data, path))
        if reader.fieldnames != NETWORK_FIELDS:
            raise InstrumentError(
                f"network header mismatch in {path}: {reader.fieldnames!r}"
            )
        seen = set()
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise InstrumentError(
                    f"network row {row_number} has extra columns in {path}"
                )
            lob_id = (row.get("lob_id") or "").strip()
            bioguide = (row.get("bioguide_id") or "").strip()
            row_year = (row.get("year") or "").strip()
            raw_count = (row.get("n_bills") or "").strip()
            if not lob_id:
                raise InstrumentError(
                    f"blank lob_id at network row {row_number} in {path}"
                )
            if re.fullmatch(r"[A-Z][0-9]{6}", bioguide) is None:
                raise InstrumentError(
                    f"invalid bioguide at network row {row_number} in {path}"
                )
            if row_year != str(year):
                raise InstrumentError(
                    f"wrong year at network row {row_number} in {path}"
                )
            try:
                n_bills = int(raw_count)
            except ValueError as exc:
                raise InstrumentError(
                    f"invalid n_bills at network row {row_number} in {path}"
                ) from exc
            if n_bills < 0 or str(n_bills) != raw_count:
                raise InstrumentError(
                    f"noncanonical n_bills at network row {row_number} in {path}"
                )
            key = (lob_id, bioguide)
            if key in seen:
                raise InstrumentError(
                    f"duplicate dyad at network row {row_number} in {path}"
                )
            seen.add(key)
            # The existence of any row establishes this organization's coverage
            # in this year; missing dyads inside that boundary are real zeros.
            org_year[lob_id].setdefault(year, {})[bioguide] = n_bills
    return dict(org_year), provenance


def load_seniority(path):
    """bioguide -> years in office, mirroring load_seniority exactly."""
    data = _read_bytes(path, "Voteview seniority source")
    first = {}
    for row in csv.DictReader(_decode_csv(data, path)):
        bioguide = row.get("bioguide_id")
        try:
            congress = int(row["congress"])
        except (ValueError, TypeError, KeyError):
            continue
        if bioguide and (
            bioguide not in first or congress < first[bioguide]
        ):
            first[bioguide] = congress
    output = {}
    for bioguide, first_congress in first.items():
        if first_congress:
            output[bioguide] = CURRENT_YEAR - (
                1789 + 2 * (first_congress - 1)
            )
    return output, {
        "path": os.path.abspath(path),
        "sha256": _sha256_bytes(data),
        "derivation": (
            "2026 - (1789 + 2 * (earliest_voteview_congress - 1))"
        ),
    }


def _party_code(value, row_number):
    lookup = {
        "democrat": "D",
        "d": "D",
        "republican": "R",
        "r": "R",
        "independent": "I",
        "i": "I",
    }
    try:
        return lookup[value.strip().casefold()]
    except (AttributeError, KeyError) as exc:
        raise InstrumentError(
            f"legislators.csv invalid party at row {row_number}"
        ) from exc


def load_legislators(path):
    data = _read_bytes(path, "legislators roster")
    required = {"bioguide_id", "party", "type", "full_name"}
    reader = csv.DictReader(_decode_csv(data, path))
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise InstrumentError("legislators.csv header mismatch")
    roster = {}
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise InstrumentError(
                f"legislators.csv row {row_number} has extra columns"
            )
        bioguide = (row.get("bioguide_id") or "").strip()
        chamber = (row.get("type") or "").strip().casefold()
        if re.fullmatch(r"[A-Z][0-9]{6}", bioguide) is None:
            raise InstrumentError(
                f"legislators.csv invalid bioguide at row {row_number}"
            )
        if bioguide in roster:
            raise InstrumentError(
                f"legislators.csv duplicate bioguide {bioguide}"
            )
        if chamber not in {"rep", "sen"}:
            raise InstrumentError(
                f"legislators.csv invalid chamber at row {row_number}"
            )
        roster[bioguide] = {
            "member_name": (row.get("full_name") or "").strip() or None,
            "party": _party_code(row.get("party"), row_number),
            "chamber": chamber,
        }
    if not roster:
        raise InstrumentError("legislators.csv yielded an empty roster")
    return roster, {
        "path": os.path.abspath(path),
        "sha256": _sha256_bytes(data),
        "members": len(roster),
    }


def resolve_roster_names(roster, resolver):
    """Fill every roster member_name that legislators.csv left blank.

    legislators.csv carries roster rows whose ``full_name`` is empty (G000607
    is one), and champions.csv is the name source for the dashboard, so a blank
    here is how a bare bioguide reached the page.  The resolver's ladder answers
    from congestion.json first and marks anything it still cannot name.
    """
    filled = []
    for bioguide in sorted(roster):
        if roster[bioguide].get("member_name"):
            continue
        record = resolver.resolve(bioguide)
        roster[bioguide]["member_name"] = record["display"]
        filled.append(f"{bioguide} | {record['display']} | {record['source']}")
    return roster, {
        "roster_rows_with_blank_full_name": len(filled),
        "resolved_from_fallback_ladder": filled,
        "fallback_order": resolver.receipt()["fallback_order"],
    }


def _congress_for_year(year):
    return builder.congress_for_calendar_year(year)


def load_committee_snapshots(committee_dir, congresses):
    available = {}
    provenance = []
    try:
        names = os.listdir(committee_dir)
    except FileNotFoundError:
        names = []
    except OSError as exc:
        raise InstrumentError(
            f"cannot list committee snapshot directory {committee_dir}: {exc}"
        ) from exc
    for congress in sorted(congresses):
        pattern = re.compile(
            r"committee-membership-"
            + str(congress)
            + r"-[0-9A-Fa-f]{40}\.yaml$"
        )
        matches = sorted(name for name in names if pattern.fullmatch(name))
        if len(matches) > 1:
            raise InstrumentError(
                f"multiple pinned committee snapshots for Congress {congress}"
            )
        if not matches:
            continue
        path = os.path.abspath(os.path.join(committee_dir, matches[0]))
        data = _read_bytes(path, "committee snapshot")
        text = _decode_text(data, path)
        committees = bridge3.parse_committee_snapshot_text(text)
        available[congress] = bridge3.finance_committee_members(committees)
        provenance.append(
            {
                "congress": congress,
                "path": path,
                "sha256": _sha256_bytes(data),
            }
        )
    return available, provenance


def load_bill_people_maps(raw_dir, congresses):
    people_maps = {}
    provenance = []
    for congress in sorted(congresses):
        billstatus_dir = os.path.join(raw_dir, f"billstatus{congress}")
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        archive_rows = []
        for archive_token, _type_token in zip_types:
            path = os.path.abspath(
                os.path.join(
                    billstatus_dir,
                    f"BILLSTATUS-{congress}-{archive_token}.zip",
                )
            )
            archive_rows.append(
                {"path": path, "sha256": _sha256_file(path)}
            )
        loaded = builder.load_sponsor_map(
            billstatus_dir,
            congress=congress,
            zip_types=zip_types,
        )
        people_maps[congress] = loaded[0]
        provenance.append(
            {
                "congress": congress,
                "archives": archive_rows,
                "distinct_bills": len(loaded[0]),
                "builder": "lobbying.build_network_2021_rebuild.load_sponsor_map",
            }
        )
    return people_maps, provenance


def legislative_volume(people_maps, congresses):
    """Count each BILLSTATUS bill once per sponsor/cosponsor."""
    counts = collections.Counter()
    for congress in sorted(set(congresses)):
        if congress not in people_maps:
            raise InstrumentError(
                f"legislative people-set missing for Congress {congress}"
            )
        for people in people_maps[congress].values():
            for bioguide in set(people):
                counts[bioguide] += 1
    return dict(counts)


def _load_json_optional(path, label):
    if not os.path.isfile(path):
        return None, {"path": os.path.abspath(path), "available": False}
    data = _read_bytes(path, label)
    try:
        value = json.loads(_decode_text(data, path))
    except ValueError as exc:
        raise InstrumentError(f"malformed {label} {path}: {exc}") from exc
    return value, {
        "path": os.path.abspath(path),
        "available": True,
        "sha256": _sha256_bytes(data),
    }


def load_association_names(path):
    payload, provenance = _load_json_optional(path, "association sets")
    if payload is None:
        return {}, provenance
    groups = payload.get("association_sets") if isinstance(payload, dict) else None
    if not isinstance(groups, dict):
        raise InstrumentError("association_sets.json lacks association_sets map")
    names = {}
    for group_name in sorted(groups):
        entries = groups[group_name]
        if not isinstance(entries, list):
            raise InstrumentError(
                f"association set {group_name!r} is not a list"
            )
        for entry in entries:
            if not isinstance(entry, dict):
                raise InstrumentError("association set entry is not an object")
            lob_id = str(entry.get("lob_id") or "").strip()
            client_names = entry.get("client_names")
            if not lob_id or not isinstance(client_names, list):
                raise InstrumentError("association set entry lacks lob_id/client_names")
            display = next(
                (
                    name.strip()
                    for name in client_names
                    if isinstance(name, str) and name.strip()
                ),
                None,
            )
            if display is None:
                raise InstrumentError(
                    f"association set {lob_id} has no displayable client name"
                )
            previous = names.get(lob_id)
            if previous is not None and previous != display:
                raise InstrumentError(
                    f"association sets give conflicting names for {lob_id}"
                )
            names[lob_id] = display
    return names, provenance


def load_classified_names(path):
    if not os.path.isfile(path):
        return {}, {"path": os.path.abspath(path), "available": False}
    data = _read_bytes(path, "classified LobbyView clients")
    reader = csv.DictReader(_decode_csv(data, path))
    required = {"lob_id", "client_name", "side"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise InstrumentError("classified LobbyView clients header mismatch")
    values = {}
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise InstrumentError(
                f"classified clients row {row_number} has extra columns"
            )
        lob_id = (row.get("lob_id") or "").strip()
        name = (row.get("client_name") or "").strip()
        side = (row.get("side") or "").strip().casefold()
        if not lob_id or not name:
            raise InstrumentError(
                f"classified clients row {row_number} has blank identity"
            )
        value = {"name": name, "side": side or None}
        previous = values.get(lob_id)
        if previous is not None and previous != value:
            raise InstrumentError(
                f"classified clients conflict for lob_id {lob_id}"
            )
        values[lob_id] = value
    return values, {
        "path": os.path.abspath(path),
        "available": True,
        "sha256": _sha256_bytes(data),
    }


def bridge_crosswalk_names():
    output = {}
    for industry in sorted(bridge3.CROSSWALK):
        for name, lob_id in bridge3.CROSSWALK[industry].items():
            if lob_id in output and output[lob_id] != name:
                raise InstrumentError(
                    f"bridge crosswalk has conflicting names for {lob_id}"
                )
            output[lob_id] = name
    return output


def resolve_org_metadata(lob_ids, association_names, classified):
    bridge_names = bridge_crosswalk_names()
    output = {}
    for lob_id in sorted(lob_ids):
        classified_row = classified.get(lob_id, {})
        if lob_id in bridge_names:
            name = bridge_names[lob_id]
            source = "bridge3_crosswalk"
        elif lob_id in association_names:
            name = association_names[lob_id]
            source = "association_sets"
        elif lob_id in classified:
            name = classified_row["name"]
            source = "lobbyview_clients_classified"
        else:
            name = None
            source = "unresolved"
        output[lob_id] = {
            "org_name": name,
            "name_source": source,
            "org_side": classified_row.get("side"),
        }
    return output


def _trend(yearly_values):
    years = sorted(yearly_values)
    if len(years) < 2:
        return 0, "flat"
    mean_year = sum(years) / len(years)
    mean_value = sum(yearly_values[year] for year in years) / len(years)
    numerator = sum(
        (year - mean_year) * (yearly_values[year] - mean_value)
        for year in years
    )
    if numerator > 0.0:
        return 1, "increasing"
    if numerator < 0.0:
        return -1, "decreasing"
    return 0, "flat"


def build_member_rows(
    yearly_attention,
    covered_years,
    roster,
    seniority,
    volume,
    committee_members=None,
):
    """Zero-fill every roster member inside an organization's coverage."""
    years = sorted(covered_years)
    if not years:
        raise InstrumentError("organization has no covered years")
    rows = []
    for bioguide in sorted(roster):
        if bioguide not in seniority:
            raise InstrumentError(
                f"Voteview seniority missing for roster member {bioguide}"
            )
        yearly = {
            year: int(yearly_attention.get(year, {}).get(bioguide, 0))
            for year in years
        }
        present = [year for year in years if yearly[year] > 0]
        trend_sign, direction = _trend(yearly)
        member = roster[bioguide]
        rows.append(
            {
                "bioguide_id": bioguide,
                "member_name": member.get("member_name"),
                "party": member["party"],
                "chamber": member["chamber"],
                "seniority_years": int(seniority[bioguide]),
                "legislative_volume": int(volume.get(bioguide, 0)),
                "finance_committee": (
                    None
                    if committee_members is None
                    else bioguide in committee_members
                ),
                "raw_n_bills": sum(yearly.values()),
                "years_present": len(present),
                "first_contact_year": min(present) if present else None,
                "latest_year": max(present) if present else None,
                "trend_sign": trend_sign,
                "direction_of_travel": direction,
                "yearly_n_bills": {str(year): yearly[year] for year in years},
            }
        )
    return rows


def _raw_percentiles(rows):
    order = sorted(
        range(len(rows)), key=lambda index: rows[index]["raw_n_bills"]
    )
    percentiles = [0.0] * len(rows)
    start = 0
    while start < len(order):
        stop = start + 1
        while (
            stop < len(order)
            and rows[order[stop]]["raw_n_bills"]
            == rows[order[start]]["raw_n_bills"]
        ):
            stop += 1
        average_rank = ((start + 1) + stop) / 2.0
        percentile = 100.0 * (average_rank - 0.5) / len(rows)
        for position in range(start, stop):
            percentiles[order[position]] = percentile
        start = stop
    return percentiles


def _design(rows, include_committee):
    matrix = []
    for row in rows:
        # Enter log1p(seniority) only: returns to tenure are strongly
        # diminishing, and entering raw seniority beside it is wrong.
        values = [
            1.0,
            math.log1p(row["seniority_years"]),
            math.log1p(row["legislative_volume"]),
            1.0 if row["chamber"] == "sen" else 0.0,
            1.0 if row["party"] == "R" else 0.0,
            1.0 if row["party"] == "I" else 0.0,
        ]
        if include_committee:
            values.append(1.0 if row["finance_committee"] else 0.0)
        matrix.append(values)
    return matrix


def _finite_round(value, digits=12):
    if not math.isfinite(value):
        raise InstrumentError("model produced a non-finite value")
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0.0 else rounded


def score_member_rows(rows, include_committee, min_links=25):
    """Fit one organization with bridge3's pivoted-QR projection."""
    nonzero_links = sum(row["raw_n_bills"] > 0 for row in rows)
    base = {
        "n_members_modeled": len(rows),
        "n_nonzero_member_links": nonzero_links,
        "r2": None,
        "residual_standard_error": None,
        "design_rank": None,
        "residual_df": None,
        "skipped_reason": None,
        "rows": [],
    }
    if nonzero_links < min_links:
        base["skipped_reason"] = (
            f"nonzero_member_links={nonzero_links} below min_links={min_links}"
        )
        return base
    if not rows:
        base["skipped_reason"] = "degenerate_design: empty roster"
        return base
    matrix = _design(rows, include_committee)
    columns = len(matrix[0])
    basis = bridge3._pivoted_qr_basis(
        matrix, tolerance=bridge3.RANK_TOLERANCE
    )
    rank = len(basis)
    base["design_rank"] = rank
    if rank != columns:
        base["skipped_reason"] = (
            f"rank_deficient_design: rank={rank}, columns={columns}"
        )
        return base
    residual_df = len(rows) - rank
    base["residual_df"] = residual_df
    if residual_df <= 0:
        base["skipped_reason"] = (
            "degenerate_design: no positive residual degrees of freedom"
        )
        return base
    observed = [math.log1p(row["raw_n_bills"]) for row in rows]
    fitted = [0.0] * len(rows)
    for vector in basis:
        coefficient = sum(a * b for a, b in zip(vector, observed))
        fitted = [
            current + coefficient * direction
            for current, direction in zip(fitted, vector)
        ]
    residuals = [
        value - prediction for value, prediction in zip(observed, fitted)
    ]
    sse = sum(value * value for value in residuals)
    mean_observed = sum(observed) / len(observed)
    sst = sum((value - mean_observed) ** 2 for value in observed)
    if sst <= bridge3.RANK_TOLERANCE:
        base["skipped_reason"] = "degenerate_outcome: zero outcome variance"
        return base
    residual_standard_error = math.sqrt(sse / residual_df)
    if (
        not math.isfinite(residual_standard_error)
        or residual_standard_error <= bridge3.RANK_TOLERANCE
    ):
        base["skipped_reason"] = (
            "degenerate_fit: residual standard error is zero or non-finite"
        )
        return base
    r2 = 1.0 - sse / sst
    percentiles = _raw_percentiles(rows)
    scored = []
    for index, source in enumerate(rows):
        row = dict(source)
        row.update(
            {
                "champion_score": _finite_round(
                    residuals[index] / residual_standard_error
                ),
                "raw_percentile": _finite_round(percentiles[index], 8),
                "observed_log_attention": _finite_round(observed[index]),
                "predicted_log_attention": _finite_round(fitted[index]),
                "residual": _finite_round(residuals[index]),
            }
        )
        scored.append(row)
    scored.sort(
        key=lambda row: (
            -row["champion_score"],
            -row["raw_n_bills"],
            -row["years_present"],
            row["bioguide_id"],
        )
    )
    for rank_number, row in enumerate(scored, start=1):
        row["champion_rank"] = rank_number
    base.update(
        {
            "r2": _finite_round(r2),
            "residual_standard_error": _finite_round(
                residual_standard_error
            ),
            "rows": scored,
        }
    )
    return base


def analyze_organization(
    lob_id,
    yearly_attention,
    roster,
    seniority,
    volume,
    committee_by_congress,
    metadata,
    min_links=25,
    top=25,
):
    covered_years = sorted(yearly_attention)
    congresses = sorted({_congress_for_year(year) for year in covered_years})
    missing_committees = [
        congress for congress in congresses if congress not in committee_by_congress
    ]
    if missing_committees:
        committee_members = None
        controls_used = list(BASE_CONTROLS)
        omitted_reason = (
            "finance_committee omitted because no pinned committee snapshot "
            "is available for Congress(es) "
            + ", ".join(str(value) for value in missing_committees)
            + "; covered_years="
            + ",".join(str(value) for value in covered_years)
        )
    else:
        committee_members = set()
        for congress in congresses:
            committee_members.update(committee_by_congress[congress])
        controls_used = list(BASE_CONTROLS) + [COMMITTEE_CONTROL]
        omitted_reason = None
    rows = build_member_rows(
        yearly_attention,
        covered_years,
        roster,
        seniority,
        volume,
        committee_members=committee_members,
    )
    model = score_member_rows(
        rows,
        include_committee=committee_members is not None,
        min_links=min_links,
    )
    outside_roster = sorted(
        {
            bioguide
            for values in yearly_attention.values()
            for bioguide in values
            if bioguide not in roster
        }
    )
    entry = {
        "lob_id": lob_id,
        "org_name": metadata.get("org_name"),
        "name_source": metadata.get("name_source", "unresolved"),
        "org_side": metadata.get("org_side"),
        "covered_years": covered_years,
        "controls_used": controls_used,
        "controls_omitted_reason": omitted_reason,
        "n_members_modeled": model["n_members_modeled"],
        "n_nonzero_member_links": model["n_nonzero_member_links"],
        "n_observed_members_outside_roster": len(outside_roster),
        "r2": model["r2"],
        "residual_standard_error": model["residual_standard_error"],
        "design_rank": model["design_rank"],
        "residual_df": model["residual_df"],
        "skipped_reason": model["skipped_reason"],
        "champions": [],
    }
    csv_rows = []
    if model["skipped_reason"] is None:
        enriched = []
        for model_row in model["rows"]:
            row = dict(model_row)
            row["controls_used"] = list(controls_used)
            row["controls_omitted_reason"] = omitted_reason
            enriched.append(row)
            flat = {
                "lob_id": lob_id,
                "org_name": entry["org_name"],
                "name_source": entry["name_source"],
                "org_side": entry["org_side"],
                "covered_years": json.dumps(covered_years, separators=(",", ":")),
                "controls_used": json.dumps(
                    controls_used, separators=(",", ":")
                ),
                "controls_omitted_reason": omitted_reason,
                "residual_standard_error": model["residual_standard_error"],
                "r2": model["r2"],
                "n_members_modeled": model["n_members_modeled"],
            }
            flat.update(model_row)
            flat["yearly_n_bills"] = json.dumps(
                model_row["yearly_n_bills"],
                sort_keys=True,
                separators=(",", ":"),
            )
            csv_rows.append(flat)
        entry["champions"] = enriched[:top]
    return entry, csv_rows


def analyze_all(
    org_year,
    roster,
    seniority,
    people_maps,
    committee_by_congress,
    metadata,
    min_links=25,
    top=25,
):
    per_org = []
    csv_rows = []
    volume_cache = {}
    for lob_id in sorted(org_year):
        years = sorted(org_year[lob_id])
        congress_key = tuple(
            sorted({_congress_for_year(year) for year in years})
        )
        if congress_key not in volume_cache:
            volume_cache[congress_key] = legislative_volume(
                people_maps, congress_key
            )
        entry, flat = analyze_organization(
            lob_id,
            org_year[lob_id],
            roster,
            seniority,
            volume_cache[congress_key],
            committee_by_congress,
            metadata.get(
                lob_id,
                {
                    "org_name": None,
                    "name_source": "unresolved",
                    "org_side": None,
                },
            ),
            min_links=min_links,
            top=top,
        )
        per_org.append(entry)
        csv_rows.extend(flat)
    add_sector_relative(per_org, csv_rows)
    return per_org, csv_rows


def add_sector_relative(per_org, csv_rows):
    """Add leave-one-organization-out comparison scores in place.

    A resolved organization's comparison pool contains only other scored
    organizations on the same side.  An unresolved focal organization uses
    every other scored organization.  The focal score is never permitted to
    enter its own mean.
    """
    by_member = collections.defaultdict(list)
    seen = set()
    for row in csv_rows:
        key = (row["lob_id"], row["bioguide_id"])
        if key in seen:
            raise InstrumentError(
                "duplicate scored organization-member row for "
                + f"{row['lob_id']} / {row['bioguide_id']}"
            )
        seen.add(key)
        by_member[row["bioguide_id"]].append(row)

    for rows in by_member.values():
        for focal in rows:
            side = focal.get("org_side")
            focal["sector_basis"] = "all_orgs" if side is None else str(side)
            comparisons = [
                candidate
                for candidate in rows
                if candidate["lob_id"] != focal["lob_id"]
                and (side is None or candidate.get("org_side") == side)
            ]
            focal["sector_n_orgs"] = len(comparisons)
            if not comparisons:
                focal["sector_mean_score"] = None
                focal["sector_relative_score"] = None
                focal["sector_rank"] = None
                continue
            mean_score = _finite_round(
                sum(row["champion_score"] for row in comparisons)
                / len(comparisons)
            )
            focal["sector_mean_score"] = mean_score
            focal["sector_relative_score"] = _finite_round(
                focal["champion_score"] - mean_score
            )

    by_org = collections.defaultdict(list)
    for row in csv_rows:
        by_org[row["lob_id"]].append(row)
    for rows in by_org.values():
        ranked = sorted(
            (
                row
                for row in rows
                if row["sector_relative_score"] is not None
            ),
            key=lambda row: (
                -row["sector_relative_score"],
                row["champion_rank"],
                row["bioguide_id"],
            ),
        )
        for rank_number, row in enumerate(ranked, start=1):
            row["sector_rank"] = rank_number

    flat_by_key = {
        (row["lob_id"], row["bioguide_id"]): row for row in csv_rows
    }
    sector_fields = (
        "sector_basis",
        "sector_mean_score",
        "sector_n_orgs",
        "sector_relative_score",
        "sector_rank",
    )
    for org in per_org:
        for champion in org["champions"]:
            flat = flat_by_key[(org["lob_id"], champion["bioguide_id"])]
            champion.update({field: flat[field] for field in sector_fields})


def _atomic_text(path, writer, encoding="utf-8"):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(temp_path, "w", encoding=encoding, newline="") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def write_outputs(out_dir, document, csv_rows):
    json_path = os.path.join(out_dir, "champions.json")
    csv_path = os.path.join(out_dir, "champions.csv")

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
            fieldnames=CSV_FIELDS,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for source in csv_rows:
            writer.writerow({field: source.get(field) for field in CSV_FIELDS})

    _atomic_text(json_path, json_writer, encoding="utf-8")
    _atomic_text(csv_path, csv_writer, encoding="utf-8-sig")
    return json_path, csv_path


def _generated_utc():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _print_summary(per_org, csv_rows=None):
    print("Lobby champions (descriptive residual attention; no causal claim)")
    sector_by_org = collections.defaultdict(list)
    for row in csv_rows or []:
        if row.get("sector_relative_score") is not None:
            sector_by_org[row["lob_id"]].append(row)
    for org in per_org:
        label = org["org_name"] or org["lob_id"]
        if org["skipped_reason"] is not None:
            print(f"  {label}: SKIPPED - {org['skipped_reason']}")
            continue
        print(f"  {label} ({org['lob_id']}):")
        for champion in org["champions"][:5]:
            # Never print a bare identifier: an unnamed member is marked, not silent.
            member = champion["member_name"] or (
                champion["bioguide_id"] + f" ({identity.UNRESOLVED_MARKER})"
            )
            print(
                "    "
                + member
                + f": score={champion['champion_score']:.3f}, "
                + f"raw_bills={champion['raw_n_bills']}, "
                + f"years_present={champion['years_present']}"
            )
        candidates = sector_by_org.get(org["lob_id"])
        if candidates is None:
            candidates = [
                row
                for row in org["champions"]
                if row.get("sector_relative_score") is not None
            ]
        candidates = sorted(
            candidates,
            key=lambda row: (
                row["sector_rank"],
                row["champion_rank"],
                row["bioguide_id"],
            ),
        )
        print("    org-specific top 5 (sector-relative):")
        if not candidates:
            print("      unavailable (no leave-one-out comparison orgs)")
        for champion in candidates[:5]:
            # Never print a bare identifier: an unnamed member is marked, not silent.
            member = champion["member_name"] or (
                champion["bioguide_id"] + f" ({identity.UNRESOLVED_MARKER})"
            )
            print(
                "      "
                + member
                + f": relative={champion['sector_relative_score']:.3f}, "
                + f"sector_mean={champion['sector_mean_score']:.3f}, "
                + f"n_orgs={champion['sector_n_orgs']}"
            )


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            "Descriptively rank residual organization-to-member bill attention."
        )
    )
    parser.add_argument(
        "--years",
        nargs="*",
        metavar="YEAR",
        help="years separated by spaces or commas (default: all available)",
    )
    parser.add_argument("--networks-dir", metavar="DIR")
    parser.add_argument(
        "--fullscope",
        action="store_true",
        help="discover our_network_fullscope_YEAR_strict.csv instead of the legacy family",
    )
    parser.add_argument("--out-dir", default=".", metavar="DIR")
    parser.add_argument("--min-links", type=int, default=25)
    parser.add_argument("--top", type=int, default=25)
    return parser


def main(argv=None, environ=None):
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    if args.min_links < 1:
        print("ERROR: --min-links must be positive", file=sys.stderr)
        return 2
    if args.top < 1:
        print("ERROR: --top must be positive", file=sys.stderr)
        return 2
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        print("ERROR: LOCALAPPDATA is unset", file=sys.stderr)
        return 2
    rebuild_root = os.path.join(local_app_data, "Melange", "lda_rebuild")
    networks_dir = args.networks_dir or os.path.join(rebuild_root, "derived")
    raw_dir = os.path.join(rebuild_root, "raw")
    committee_dir = os.path.join(raw_dir, "committee_snapshots")
    association_path = os.path.join(
        raw_dir,
        "positions",
        "assoc_2023_2024",
        "association_sets.json",
    )
    classified_path = os.path.join(
        local_app_data,
        "Melange",
        "lobbyview",
        "derived",
        "lobbyview_clients_classified.csv",
    )
    seniority_path = os.path.join(MB_ROOT, "votes", "HSall_members.csv")
    legislators_path = os.path.join(MB_ROOT, "fec", "legislators.csv")
    congestion_path = os.path.join(MB_ROOT, "data", "engine", "congestion.json")
    try:
        network_paths = discover_network_paths(
            networks_dir,
            pattern=FULLSCOPE_NETWORK_RE if args.fullscope else NETWORK_RE,
        )
        years = parse_years(args.years, set(network_paths))
        # Network bytes and hashes are acquired fresh on every invocation.
        org_year, network_provenance = read_networks(network_paths, years)
        seniority, seniority_provenance = load_seniority(seniority_path)
        roster, legislators_provenance = load_legislators(legislators_path)
        name_resolver = identity.MemberNameResolver.from_paths(
            congestion_path=congestion_path,
            legislators_path=legislators_path,
            voteview_path=seniority_path,
        )
        roster, roster_name_provenance = resolve_roster_names(roster, name_resolver)
        congresses = sorted({_congress_for_year(year) for year in years})
        committees, committee_provenance = load_committee_snapshots(
            committee_dir, congresses
        )
        people_maps, billstatus_provenance = load_bill_people_maps(
            raw_dir, congresses
        )
        association_names, association_provenance = load_association_names(
            association_path
        )
        classified, classified_provenance = load_classified_names(
            classified_path
        )
        metadata = resolve_org_metadata(
            set(org_year), association_names, classified
        )
        per_org, csv_rows = analyze_all(
            org_year,
            roster,
            seniority,
            people_maps,
            committees,
            metadata,
            min_links=args.min_links,
            top=args.top,
        )
        document = {
            "generated_utc": _generated_utc(),
            "provenance": {
                "selected_years": years,
                "network_csvs": network_provenance,
                "seniority_source": seniority_provenance,
                "legislators": legislators_provenance,
                "member_name_fallback_ladder": {
                    **roster_name_provenance,
                    "sources": name_resolver.provenance,
                },
                "committee_snapshots": committee_provenance,
                "committee_snapshots_missing_congresses": [
                    congress for congress in congresses if congress not in committees
                ],
                "billstatus_people_sets": billstatus_provenance,
                "legislative_volume_time_basis": (
                    "distinct bills in every Congress touched by an "
                    "organization's covered years; each Congress counted once"
                ),
                "name_sources": {
                    "bridge3_crosswalk": {
                        "path": os.path.abspath(bridge3.__file__),
                        "sha256": _sha256_file(bridge3.__file__),
                    },
                    "association_sets": association_provenance,
                    "lobbyview_clients_classified": classified_provenance,
                },
            },
            "method_note": METHOD_NOTE,
            "per_org": per_org,
        }
        write_outputs(args.out_dir, document, csv_rows)
        _print_summary(per_org, csv_rows)
    except (
        InstrumentError,
        builder.IngestError,
        bridge3.IngestError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
