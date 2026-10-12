# -*- coding: utf-8 -*-
"""Honest forward-prediction harness for lobbying relationships.

The unit is ``(lob_id, bioguide_id, year=Y)`` and the target is whether the
organization has a strict-network dyad with the member in ``Y+1``.  Every
candidate member in the ``fec/legislators.csv`` roster is zero-filled.  Features are built
only from sources whose maximum source year is Y or earlier, and that invariant
is asserted before a row may enter a design matrix.

This is a DESCRIPTIVE forecasting harness, not a causal model.  Coefficients
are associations under a fixed lambda=1.0 ridge penalty and must not be read as
effects.  FEC contribution data is deliberately excluded because the
preregistered round-3 test measured its contribution beyond these same
structural controls at -0.0002.  Roughly 82% of next-year targets persist from
the prior year, so that persistence baseline must accompany any accuracy
figure; predicting the persistent core is not product skill.

``do_z``, ``say_z``, ``gap``, ``sponsored_cu``, and ``stance_mean`` are also
deliberately excluded.  Their source artifacts are current-vintage aggregates
with no year axis, so using them in a historical feature row would leak post-Y
information.  The sanctioned future upgrade is to construct a separate,
year-vintaged stance feature from the dated per-item files
``press_stance_final.csv``, ``tweet_stance_final.csv``,
``hearing_stance_final.csv``, ``speech_stance_final.csv``,
``interview_stance_final.csv``, and ``bluesky_stance_final.csv``, filtering
every item to ``date <= Y``.  Do not re-add the aggregate snapshot fields.

Temporal evaluations are rolling and never shuffled.  A split with cutoff T
trains on feature years no later than T and tests only feature year T+1.  Build
receipts must identify one common network corpus basis for every source and
target year in a split.  Unknown or mixed coverage is refused, as is a panel
with only one usable year-pair, because neither permits an honest temporal
train/test evaluation.

Finance-committee membership is available only where a pinned Congress
snapshot exists.  The default is fail-loud.  With
``--allow-missing-committee``, a row in an uncovered Congress carries no
committee value at all, and every split touching such a row omits the feature
from its entire design matrix.  Missing membership is never coded as zero.

Persistence accuracy on the churn subset is structurally zero because churn
contains exactly the dyads whose next-year state is the opposite of their
current state.  Churn headlines therefore compare the model with the
best-performing trivial baseline, never persistence alone.
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
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

from lobbying import build_network_2021_rebuild as builder
from lobbying import fec_lda_bridge3_look as bridge3
from lobbying import lobby_champions as champions


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


RIDGE_LAMBDA = 1.0
IRLS_MAX_ITERATIONS = 100
IRLS_TOLERANCE = 1e-8
HISTORICAL_PERSISTENCE = 0.82
NETWORK_FIELDS = ["lob_id", "bioguide_id", "year", "n_bills"]
SCORE_FIELDS = [
    "lob_id",
    "bioguide_id",
    "year",
    "target_year",
    "train_through",
    "predicted_probability",
    "actual",
    "baseline_persistence",
    "baseline_popularity_probability",
    "baseline_popularity_prediction",
]
CONTINUOUS_FEATURES = (
    "n_bills_y",
    "n_bills_y_minus_1",
    "prior_years_present",
    "years_since_first_contact",
    "years_since_last_contact",
    "log1p_seniority",
    "log1p_legislative_volume",
    "org_total_dyads_y",
    "org_median_n_bills_y",
    "member_popularity_other_orgs_y",
)
CATEGORICAL_FEATURES = (
    "chamber",
    "party",
    "finance_committee",
    "org_side",
)
MODEL_FEATURES = CONTINUOUS_FEATURES + CATEGORICAL_FEATURES
OPTIONAL_MISSING_FEATURES = frozenset({"finance_committee"})
MISSING_COMMITTEE_REASON = (
    "pinned finance-committee snapshot unavailable; feature omitted from the "
    "entire split and never imputed to zero"
)
PERSISTENCE_CHURN_NOTE = (
    "Persistence accuracy is structurally 0% on every nonempty churn subset "
    "because churn contains exactly the dyads whose Y+1 state differs from Y, "
    "while persistence predicts no change."
)
EXCLUSION_REASON = (
    "source artifact is a current-vintage aggregate with no year axis; "
    "using it would leak post-Y information"
)
FEATURES_EXCLUDED_FOR_LEAKAGE = {
    name: EXCLUSION_REASON
    for name in ("do_z", "say_z", "gap", "sponsored_cu", "stance_mean")
}
METHOD_NOTE = (
    "This is a DESCRIPTIVE forecasting harness, not a causal model. "
    "Coefficients are associations under a fixed lambda=1.0 penalty and "
    "must not be read as effects. FEC contribution data is deliberately "
    "excluded because the preregistered round-3 test measured its "
    "contribution beyond these same structural controls at -0.0002. The "
    "approximately 82% persistence baseline must be quoted alongside any "
    "accuracy figure. Product value is assessed on churn, not the persistent "
    "core. Five current-vintage behavioral snapshot fields were considered "
    "and excluded for leakage; dated per-item stance inputs are the sanctioned "
    "future year-vintaged upgrade path."
)


class ForecastError(Exception):
    """An input, leakage, coverage, or design failure that cannot be guessed."""


class LeakageError(ForecastError):
    """A feature claims information later than its feature year."""


def _congress_for_year(year):
    """Normalize the builder-derived Congress to int at its single boundary."""
    value = builder.congress_for_calendar_year(int(year))
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ForecastError(f"builder returned invalid Congress {value!r}") from exc


def _read_bytes(path, label):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ForecastError(f"cannot read {label} {path}: {exc}") from exc


def _decode(data, label):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ForecastError(f"{label} is not valid UTF-8: {exc}") from exc


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest().upper()


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ForecastError(f"cannot hash input {path}: {exc}") from exc
    return digest.hexdigest().upper()


def _finite(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ForecastError(f"{label} must be finite")
    return result


def _rounded(value, digits=12):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    result = round(value, digits)
    return 0.0 if result == 0.0 else result


def load_coverage_bases(receipts_path, requested_years):
    """Read per-year corpus bases; absence is a hard failure, never a guess."""
    data = _read_bytes(receipts_path, "network build receipts")
    try:
        document = json.loads(_decode(data, receipts_path))
    except ValueError as exc:
        raise ForecastError(f"malformed build receipts {receipts_path}: {exc}") from exc
    years_node = document.get("years") if isinstance(document, dict) else None
    if not isinstance(years_node, dict):
        raise ForecastError("coverage basis cannot be determined: receipts lack years")
    bases = {}
    for year in sorted(requested_years):
        payload = years_node.get(str(year))
        network = payload.get("network") if isinstance(payload, dict) else None
        source = network.get("source") if isinstance(network, dict) else None
        if not isinstance(source, str) or not source.strip():
            raise ForecastError(
                f"coverage basis cannot be determined for requested year {year}"
            )
        bases[year] = source.strip()
    return bases, {
        "path": os.path.abspath(receipts_path),
        "sha256": _sha256_bytes(data),
        "basis_by_year": {str(year): bases[year] for year in sorted(bases)},
    }


def assert_coverage_homogeneous(years, basis_by_year):
    years = sorted(set(int(year) for year in years))
    missing = [year for year in years if year not in basis_by_year]
    if missing:
        raise ForecastError(
            "coverage basis cannot be determined for year(s) "
            + ",".join(str(year) for year in missing)
        )
    grouped = collections.defaultdict(list)
    for year in years:
        grouped[basis_by_year[year]].append(year)
    if len(grouped) != 1:
        detail = "; ".join(
            f"{basis}={','.join(str(year) for year in grouped[basis])}"
            for basis in sorted(grouped)
        )
        raise ForecastError("mixed network corpus basis refused: " + detail)
    return next(iter(grouped))


def load_voteview_seniority(path, current_year=champions.CURRENT_YEAR):
    """Load only min(congress) per bioguide; duplicate congress rows are benign.

    This deliberately mirrors ``hearing_antagonism_build.load_seniority``.
    Chamber, party, state, district, and ICPSR are never consulted, so a
    legitimate mid-Congress office or party transition cannot create a
    conflict on the seniority path.
    """
    data = _read_bytes(path, "Voteview seniority source")
    reader = csv.DictReader(io.StringIO(_decode(data, path), newline=""))
    required = {"congress", "bioguide_id"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ForecastError("Voteview seniority source header mismatch")
    first = {}
    same_congress_counts = collections.Counter()
    seen_pairs = set()
    for row in reader:
        bioguide = (row.get("bioguide_id") or "").strip()
        try:
            congress = int(row.get("congress") or "")
        except ValueError:
            continue
        if not bioguide:
            continue
        pair = (bioguide, congress)
        if pair in seen_pairs:
            same_congress_counts[pair] += 1
        else:
            seen_pairs.add(pair)
        if bioguide not in first or congress < first[bioguide]:
            first[bioguide] = congress
    seniority = {
        bioguide: current_year - (1789 + 2 * (first_congress - 1))
        for bioguide, first_congress in first.items()
        if first_congress
    }
    return seniority, {
        "path": os.path.abspath(path),
        "sha256": _sha256_bytes(data),
        "derivation": (
            f"{current_year} - (1789 + 2 * (min_voteview_congress - 1))"
        ),
        "deduplication": (
            "min(congress) per bioguide; repeated rows within a Congress are "
            "ignored because chamber/party/state/district/icpsr are not read"
        ),
        "bioguides": len(seniority),
        "bioguide_congress_pairs_with_additional_rows": len(same_congress_counts),
    }


def build_legislator_rosters(roster, years, seniority_current):
    """Attach year-adjusted seniority to the legislators.csv structure roster."""
    if not roster:
        raise ForecastError("legislators.csv yielded an empty structure roster")
    output = {}
    for year in sorted(set(years)):
        enriched = {}
        for bioguide in sorted(roster):
            if bioguide not in seniority_current:
                raise ForecastError(f"Voteview seniority missing for {bioguide}")
            value = dict(roster[bioguide])
            value["seniority_years"] = max(
                0,
                int(seniority_current[bioguide])
                - (champions.CURRENT_YEAR - year),
            )
            enriched[bioguide] = value
        output[year] = enriched
    return output


def _voteview_value(row, name):
    aliases = {
        "state": ("state_abbrev", "state"),
        "district": ("district_code", "district"),
        "icpsr": ("icpsr",),
        "chamber": ("chamber",),
    }
    for alias in aliases[name]:
        value = row.get(alias)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def resolve_voteview_congress_rows(rows):
    """Resolve a single Voteview row only for callers that truly need one.

    Senate wins a same-Congress House-to-Senate move.  Same-chamber identity
    alternatives prefer the ICPSR carried into the immediately following
    Congress; absent such a row, Voteview's higher-ICPSR post-switch identity
    wins.  Same-bioguide/Congress/chamber rows that disagree on state remain
    fatal.  Every non-identical collapse receipts the discarded identities.
    Returns ``(resolved, receipts)`` where resolved is keyed by
    ``(bioguide_id, congress)``.
    """
    normalized = []

    def receipt_identity(row):
        return {
            "chamber": _voteview_value(row, "chamber"),
            "state": _voteview_value(row, "state"),
            "party_code": str(row.get("party_code") or "").strip(),
            "icpsr": _voteview_value(row, "icpsr"),
        }

    def numeric_identity(row):
        try:
            icpsr = int(float(_voteview_value(row, "icpsr")))
            party = int(float(str(row.get("party_code") or "0").strip()))
        except ValueError as exc:
            raise ForecastError(
                "Voteview party-switch row has nonnumeric party_code/icpsr"
            ) from exc
        return icpsr, party
    for index, source in enumerate(rows):
        row = dict(source)
        bioguide = (row.get("bioguide_id") or "").strip()
        try:
            congress = int(row.get("congress") or "")
        except (TypeError, ValueError) as exc:
            raise ForecastError(f"Voteview resolver row {index} has invalid Congress") from exc
        chamber = _voteview_value(row, "chamber").casefold()
        if not bioguide or chamber not in {"house", "senate"}:
            raise ForecastError(f"Voteview resolver row {index} has invalid identity/chamber")
        row["bioguide_id"] = bioguide
        row["congress"] = congress
        normalized.append(row)

    groups = collections.defaultdict(list)
    for row in normalized:
        groups[(row["bioguide_id"], row["congress"])].append(row)

    resolved = {}
    receipts = []
    for key in sorted(groups):
        candidates = groups[key]
        by_chamber = collections.defaultdict(list)
        for row in candidates:
            by_chamber[_voteview_value(row, "chamber").casefold()].append(row)
        for chamber, chamber_rows in sorted(by_chamber.items()):
            states = {
                _voteview_value(row, "state")
                for row in chamber_rows
                if _voteview_value(row, "state")
            }
            if len(states) > 1:
                raise ForecastError(
                    "Voteview contradictory same-bioguide/congress/chamber states "
                    f"for {key[0]}/{key[1]}/{chamber}: {sorted(states)!r}"
                )
        all_states = {
            _voteview_value(row, "state")
            for row in candidates
            if _voteview_value(row, "state")
        }
        if len(all_states) > 1:
            raise ForecastError(
                "Voteview contradictory same-bioguide/congress states across chambers "
                f"for {key[0]}/{key[1]}: {sorted(all_states)!r}"
            )

        working = candidates
        if "house" in by_chamber and "senate" in by_chamber:
            working = by_chamber["senate"]
            discarded = [
                receipt_identity(row) for row in by_chamber["house"]
            ]
            receipts.append(
                {
                    "bioguide_id": key[0],
                    "congress": key[1],
                    "rule": "mid_congress_chamber_move_prefer_senate",
                    "selected_chamber": "Senate",
                    "selected_party_code": str(
                        working[0].get("party_code") or ""
                    ).strip(),
                    "selected_icpsr": _voteview_value(working[0], "icpsr"),
                    "discarded_rows": discarded,
                }
            )
        elif len(by_chamber) > 1:
            raise ForecastError(
                f"Voteview resolver encountered unknown chamber combination for {key!r}"
            )

        unique_rows = []
        seen = set()
        for row in working:
            signature = tuple(sorted((str(name), str(value)) for name, value in row.items()))
            if signature not in seen:
                seen.add(signature)
                unique_rows.append(row)
        working = unique_rows
        if len(working) > 1:
            following = [
                row
                for row in normalized
                if row["bioguide_id"] == key[0] and row["congress"] == key[1] + 1
            ]
            following_icpsrs = {
                _voteview_value(row, "icpsr") for row in following
            }
            carried = [
                row
                for row in working
                if _voteview_value(row, "icpsr") in following_icpsrs
            ]
            if carried:
                selected = max(carried, key=numeric_identity)
                rule = "mid_congress_party_switch_carried_forward"
            else:
                selected = max(working, key=numeric_identity)
                rule = "mid_congress_party_switch_higher_icpsr_tiebreak"
            discarded = [
                receipt_identity(row) for row in working if row is not selected
            ]
            working = [selected]
            receipts.append(
                {
                    "bioguide_id": key[0],
                    "congress": key[1],
                    "rule": rule,
                    "following_congress": key[1] + 1,
                    "selected_party_code": str(
                        selected.get("party_code") or ""
                    ).strip(),
                    "selected_icpsr": _voteview_value(selected, "icpsr"),
                    "discarded_rows": discarded,
                }
            )
        if len(working) != 1:
            raise ForecastError(f"Voteview resolver failed to select one row for {key!r}")
        resolved[key] = working[0]
    return resolved, receipts


def _introduced_year(member_stream, archive_name, member_name):
    values = []
    try:
        for _event, element in ET.iterparse(member_stream, events=("end",)):
            if element.tag.rsplit("}", 1)[-1] == "introducedDate":
                text = (element.text or "").strip()
                if text:
                    values.append(text)
            element.clear()
    except ET.ParseError as exc:
        raise ForecastError(
            f"malformed BILLSTATUS XML {archive_name}:{member_name}: {exc}"
        ) from exc
    distinct = sorted(set(values))
    if len(distinct) != 1 or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", distinct[0]) is None:
        raise ForecastError(
            f"BILLSTATUS introducedDate absent or ambiguous in {archive_name}:{member_name}"
        )
    return int(distinct[0][:4])


def load_bill_introduced_years(raw_dir, congresses):
    """Read only dates; sponsor/cosponsor people-sets remain builder-derived."""
    result = {}
    for congress in sorted(set(congresses)):
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        billstatus_dir = os.path.join(raw_dir, f"billstatus{congress}")
        for archive_token, type_token in zip_types:
            archive_name = f"BILLSTATUS-{congress}-{archive_token}.zip"
            archive_path = os.path.join(billstatus_dir, archive_name)
            member_re = re.compile(
                r"(?:.*/)?BILLSTATUS-"
                + str(congress)
                + re.escape(archive_token)
                + r"([0-9]+)\.xml$",
                re.IGNORECASE,
            )
            try:
                archive = zipfile.ZipFile(archive_path, "r")
            except (OSError, zipfile.BadZipFile) as exc:
                raise ForecastError(f"cannot open BILLSTATUS archive {archive_path}: {exc}") from exc
            with archive:
                infos = sorted(
                    (info for info in archive.infolist() if info.filename.lower().endswith(".xml")),
                    key=lambda info: info.filename.lower(),
                )
                for info in infos:
                    match = member_re.fullmatch(info.filename)
                    if match is None:
                        raise ForecastError(
                            f"unexpected BILLSTATUS member {archive_name}:{info.filename}"
                        )
                    key = (type_token, int(match.group(1)), congress)
                    if key in result:
                        raise ForecastError(f"duplicate BILLSTATUS bill key {key!r}")
                    with archive.open(info, "r") as member_stream:
                        result[key] = _introduced_year(
                            member_stream, archive_name, info.filename
                        )
    return result


def legislative_volume_by_year(people_maps, introduced_years, years):
    """Count people-set bills in the current Congress introduced no later than Y."""
    output = {}
    for year in sorted(years):
        congress = _congress_for_year(year)
        people_map = people_maps.get(congress)
        if people_map is None:
            raise ForecastError(f"BILLSTATUS people-set missing for Congress {congress}")
        counts = collections.Counter()
        for bill_key in sorted(people_map):
            introduced = introduced_years.get(bill_key)
            if introduced is None:
                raise ForecastError(f"BILLSTATUS introducedDate missing for {bill_key!r}")
            if introduced > year:
                continue
            for bioguide in set(people_map[bill_key]):
                counts[bioguide] += 1
        output[year] = dict(counts)
    return output


def _median(values):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def normalize_committee_by_congress(committee_by_congress):
    """Normalize loader keys once; reject collisions rather than guessing."""
    if not isinstance(committee_by_congress, dict):
        raise ForecastError("committee snapshots must be a Congress-keyed mapping")
    normalized = {}
    for raw_congress, members in committee_by_congress.items():
        try:
            congress = int(raw_congress)
        except (TypeError, ValueError) as exc:
            raise ForecastError(
                f"committee snapshot has invalid Congress key {raw_congress!r}"
            ) from exc
        member_set = set(members)
        if congress in normalized and normalized[congress] != member_set:
            raise ForecastError(
                f"committee snapshots conflict after Congress-key normalization: {congress}"
            )
        normalized[congress] = member_set
    return normalized


def missing_committee_congresses(congresses, committee_by_congress):
    normalized = normalize_committee_by_congress(committee_by_congress)
    requested = sorted({int(congress) for congress in congresses})
    return [congress for congress in requested if congress not in normalized]


def require_committee_coverage(congresses, committee_by_congress, allow_missing=False):
    """Return normalized snapshots or fail with only genuinely absent keys."""
    normalized = normalize_committee_by_congress(committee_by_congress)
    requested = sorted({int(congress) for congress in congresses})
    missing = [congress for congress in requested if congress not in normalized]
    if missing and not allow_missing:
        raise ForecastError(
            "pinned finance-committee snapshot missing for Congress(es) "
            + ",".join(str(congress) for congress in missing)
        )
    return normalized, missing


def assert_as_of_features(row):
    """Reject any feature source later than Y and any excluded snapshot field."""
    year = int(row["year"])
    sources = row.get("_feature_source_years")
    if not isinstance(sources, dict):
        raise LeakageError("feature row lacks structural source-year ledger")
    for feature, source_year in sorted(sources.items()):
        if feature in FEATURES_EXCLUDED_FOR_LEAKAGE:
            raise LeakageError(f"excluded current-vintage feature present: {feature}")
        if source_year is not None and int(source_year) > year:
            raise LeakageError(
                f"feature {feature} uses year {source_year} after feature year {year}"
            )
    unavailable = set(row.get("_features_unavailable", ()))
    invalid_unavailable = sorted(unavailable - OPTIONAL_MISSING_FEATURES)
    if invalid_unavailable:
        raise LeakageError(
            "row declares unsupported unavailable features " + repr(invalid_unavailable)
        )
    for feature in sorted(unavailable):
        if feature in row or feature in sources:
            raise LeakageError(
                f"unavailable feature {feature} was populated instead of omitted"
            )
    missing = [
        feature
        for feature in MODEL_FEATURES
        if feature not in unavailable and feature not in sources
    ]
    if missing:
        raise LeakageError("feature source-year ledger missing " + repr(missing))
    overlap = sorted(set(row) & set(FEATURES_EXCLUDED_FOR_LEAKAGE))
    if overlap:
        raise LeakageError("excluded current-vintage fields present: " + repr(overlap))
    return True


def build_panel(
    org_year,
    years,
    rosters,
    legislative_volumes,
    committee_by_congress,
    org_metadata,
    min_org_dyads=25,
    allow_missing_committee=False,
):
    """Create zero-filled, as-of-Y rows only for consecutive covered pairs."""
    selected = sorted(set(years))
    selected_set = set(selected)
    feature_years = [year for year in selected if year + 1 in selected_set]
    feature_congresses = sorted({_congress_for_year(year) for year in feature_years})
    committee_by_congress, missing_committee = require_committee_coverage(
        feature_congresses,
        committee_by_congress,
        allow_missing=allow_missing_committee,
    )
    popularity = {}
    organization_counts = {}
    for year in selected:
        member_orgs = collections.defaultdict(set)
        active_orgs = set()
        for lob_id in sorted(org_year):
            values = org_year[lob_id].get(year)
            if values is None:
                continue
            active_orgs.add(lob_id)
            for bioguide, count in values.items():
                if count > 0:
                    member_orgs[bioguide].add(lob_id)
        popularity[year] = {
            bioguide: len(lob_ids) for bioguide, lob_ids in member_orgs.items()
        }
        organization_counts[year] = len(active_orgs)

    rows = []
    skipped_org_years = []
    missing_committee_rows = collections.Counter()
    for lob_id in sorted(org_year):
        yearly = org_year[lob_id]
        for year in selected:
            if year + 1 not in selected_set or year not in yearly or year + 1 not in yearly:
                continue
            roster = rosters.get(year)
            if not roster:
                raise ForecastError(f"roster missing for feature year {year}")
            current = yearly[year]
            current_positive = {
                bioguide: count
                for bioguide, count in current.items()
                if count > 0 and bioguide in roster
            }
            org_total = len(current_positive)
            if org_total < min_org_dyads:
                skipped_org_years.append(
                    {
                        "lob_id": lob_id,
                        "year": year,
                        "org_total_dyads_y": org_total,
                        "reason": f"below min_org_dyads={min_org_dyads}",
                    }
                )
                continue
            median_count = _median(current_positive.values())
            congress = _congress_for_year(year)
            committees = committee_by_congress.get(congress)
            side = (org_metadata.get(lob_id, {}).get("side") or "unknown").casefold()
            history_years = sorted(
                prior
                for prior in yearly
                if prior <= year and prior in selected_set
            )
            for bioguide in sorted(roster):
                present_years = [
                    prior
                    for prior in history_years
                    if yearly[prior].get(bioguide, 0) > 0
                ]
                current_count = int(current.get(bioguide, 0))
                prior_count = int(yearly.get(year - 1, {}).get(bioguide, 0))
                prior_present = sum(prior < year for prior in present_years)
                first_gap = 0 if not present_years else year - min(present_years)
                last_gap = 0 if not present_years else year - max(present_years)
                total_popularity = popularity[year].get(bioguide, 0)
                other_popularity = total_popularity - int(current_count > 0)
                other_org_denominator = max(1, organization_counts[year] - 1)
                member = roster[bioguide]
                feature_values = {
                    "n_bills_y": float(current_count),
                    "n_bills_y_minus_1": float(prior_count),
                    "prior_years_present": float(prior_present),
                    "years_since_first_contact": float(first_gap),
                    "years_since_last_contact": float(last_gap),
                    "log1p_seniority": math.log1p(member["seniority_years"]),
                    "log1p_legislative_volume": math.log1p(
                        legislative_volumes[year].get(bioguide, 0)
                    ),
                    "org_total_dyads_y": float(org_total),
                    "org_median_n_bills_y": float(median_count),
                    "member_popularity_other_orgs_y": float(other_popularity),
                    "chamber": member["chamber"],
                    "party": member["party"],
                    "org_side": side,
                }
                unavailable = []
                if committees is None:
                    unavailable.append("finance_committee")
                    missing_committee_rows[congress] += 1
                else:
                    feature_values["finance_committee"] = (
                        "1" if bioguide in committees else "0"
                    )
                source_years = {
                    feature: year
                    for feature in MODEL_FEATURES
                    if feature not in unavailable
                }
                source_years["n_bills_y_minus_1"] = year - 1
                row = {
                    "lob_id": lob_id,
                    "bioguide_id": bioguide,
                    "year": year,
                    "target_year": year + 1,
                    "actual": int(yearly[year + 1].get(bioguide, 0) > 0),
                    "baseline_persistence": int(current_count > 0),
                    "baseline_popularity_probability": min(
                        1.0, other_popularity / other_org_denominator
                    ),
                    "_committee_congress": congress,
                    "_features_unavailable": unavailable,
                    "_feature_source_years": source_years,
                }
                row.update(feature_values)
                assert_as_of_features(row)
                rows.append(row)
    rows.sort(key=lambda row: (row["year"], row["lob_id"], row["bioguide_id"]))
    missing_receipt = {}
    if missing_committee_rows:
        missing_receipt["finance_committee"] = {
            "reason": MISSING_COMMITTEE_REASON,
            "congresses": sorted(missing_committee_rows),
            "row_counts_by_congress": {
                str(congress): missing_committee_rows[congress]
                for congress in sorted(missing_committee_rows)
            },
            "total_rows": sum(missing_committee_rows.values()),
        }
    return rows, {
        "skipped_org_years": skipped_org_years,
        "rows": len(rows),
        "usable_feature_years": sorted({row["year"] for row in rows}),
        "committee_snapshot_congresses_available": sorted(committee_by_congress),
        "committee_snapshot_congresses_missing": missing_committee,
        "features_excluded_for_missing_data": missing_receipt,
    }


def make_temporal_split(rows, train_through):
    """Never admit the T+1 feature row to training."""
    train = [row for row in rows if int(row["year"]) <= int(train_through)]
    test = [row for row in rows if int(row["year"]) == int(train_through) + 1]
    if any(row["year"] > train_through for row in train):
        raise LeakageError("temporal split placed a post-T row in training")
    if any(row["year"] != train_through + 1 for row in test):
        raise LeakageError("temporal split test year is not T+1")
    if not train or not test:
        raise ForecastError(
            f"no temporal train/test split available for train-through {train_through}"
        )
    return train, test


def available_cutoffs(rows, maximum_train_through):
    pair_years = sorted({int(row["year"]) for row in rows})
    if len(pair_years) < 2:
        raise ForecastError("no temporal train/test split available")
    pair_set = set(pair_years)
    cutoffs = [
        year
        for year in pair_years
        if year <= maximum_train_through and year + 1 in pair_set
    ]
    if not cutoffs or maximum_train_through not in cutoffs:
        raise ForecastError(
            f"no temporal train/test split available through {maximum_train_through}"
        )
    return cutoffs


def split_missing_feature_policy(rows):
    """Choose one feature set for the whole split; never mix availability."""
    committee_counts = collections.Counter(
        int(row["_committee_congress"])
        for row in rows
        if "finance_committee" in set(row.get("_features_unavailable", ()))
    )
    if not committee_counts:
        return set(), {}
    receipt = {
        "finance_committee": {
            "reason": MISSING_COMMITTEE_REASON,
            "congresses": sorted(committee_counts),
            "row_counts_by_congress": {
                str(congress): committee_counts[congress]
                for congress in sorted(committee_counts)
            },
            "total_rows": sum(committee_counts.values()),
            "split_policy": (
                "feature omitted from every training and test row in this split"
            ),
        }
    }
    return {"finance_committee"}, receipt


def fit_preprocessor(rows, excluded_features=()):
    excluded = set(excluded_features)
    invalid = sorted(excluded - set(MODEL_FEATURES))
    if invalid:
        raise ForecastError("unknown design exclusions " + repr(invalid))
    means = {}
    scales = {}
    dropped = []
    design_names = ["intercept"]
    for feature in CONTINUOUS_FEATURES:
        if feature in excluded:
            continue
        values = [_finite(row[feature], feature) for row in rows]
        mean = sum(values) / len(values)
        scale = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
        means[feature] = mean
        scales[feature] = scale
        if scale <= bridge3.RANK_TOLERANCE:
            dropped.append(feature)
        else:
            design_names.append(feature)
    levels = {}
    references = {}
    for feature in CATEGORICAL_FEATURES:
        if feature in excluded:
            continue
        observed = sorted({str(row[feature]) for row in rows})
        if not observed:
            raise ForecastError(f"categorical feature {feature} has no levels")
        levels[feature] = observed
        references[feature] = observed[0]
        for level in observed[1:]:
            design_names.append(f"{feature}={level}")
    return {
        "continuous_means": means,
        "continuous_scales": scales,
        "dropped_zero_variance_features": dropped,
        "categorical_levels": levels,
        "categorical_references": references,
        "excluded_features": sorted(excluded),
        "active_continuous_features": [
            feature for feature in CONTINUOUS_FEATURES if feature not in excluded
        ],
        "active_categorical_features": [
            feature for feature in CATEGORICAL_FEATURES if feature not in excluded
        ],
        "design_names": design_names,
    }


def transform_rows(rows, preprocessor):
    matrix = []
    unseen = collections.Counter()
    dropped = set(preprocessor["dropped_zero_variance_features"])
    for row in rows:
        assert_as_of_features(row)
        values = [1.0]
        for feature in preprocessor["active_continuous_features"]:
            if feature in dropped:
                continue
            values.append(
                (_finite(row[feature], feature) - preprocessor["continuous_means"][feature])
                / preprocessor["continuous_scales"][feature]
            )
        for feature in preprocessor["active_categorical_features"]:
            value = str(row[feature])
            known = preprocessor["categorical_levels"][feature]
            if value not in known:
                unseen[(feature, value)] += 1
            for level in known[1:]:
                values.append(1.0 if value == level else 0.0)
        matrix.append(values)
    return matrix, [
        {"feature": key[0], "level": key[1], "rows": unseen[key]}
        for key in sorted(unseen)
    ]


def _sigmoid(value):
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(value, 700.0)))
    exp_value = math.exp(max(value, -700.0))
    return exp_value / (1.0 + exp_value)


def irls_logistic(matrix, outcomes, feature_names, ridge_lambda=RIDGE_LAMBDA):
    """Fixed-ridge logistic IRLS using bridge QR detection and SVD pinv."""
    if ridge_lambda != RIDGE_LAMBDA:
        raise ForecastError("ridge lambda is fixed at 1.0 and may not be tuned")
    if not matrix or len(matrix) != len(outcomes):
        raise ForecastError("logistic design is empty or outcome length differs")
    columns = len(matrix[0])
    if columns != len(feature_names) or any(len(row) != columns for row in matrix):
        raise ForecastError("logistic design has inconsistent columns")
    y = np.asarray([float(value) for value in outcomes], dtype=float)
    if np.any((y != 0.0) & (y != 1.0)):
        raise ForecastError("logistic outcomes must be binary")
    if float(np.min(y)) == float(np.max(y)):
        raise ForecastError("logistic training outcome has one class")
    x = np.asarray(matrix, dtype=float)
    if not np.all(np.isfinite(x)):
        raise ForecastError("logistic design contains non-finite values")
    basis = bridge3._pivoted_qr_basis(
        matrix, tolerance=bridge3.RANK_TOLERANCE
    )
    rank = len(basis)
    rank_deficient = rank < columns
    # The large cosponsorship harness needs only the rank receipt.  Releasing
    # the row-length QR vectors here preserves the exact detector and avoids
    # retaining its full basis throughout IRLS.
    del basis
    penalty = np.eye(columns, dtype=float)
    penalty[0, 0] = 0.0
    beta = np.zeros(columns, dtype=float)
    base_rate = min(1.0 - 1e-8, max(1e-8, float(np.mean(y))))
    beta[0] = math.log(base_rate / (1.0 - base_rate))
    converged = False
    iterations = 0
    hessian = None
    for iteration in range(1, IRLS_MAX_ITERATIONS + 1):
        linear = x @ beta
        probability = np.asarray([_sigmoid(value) for value in linear], dtype=float)
        weights = np.maximum(probability * (1.0 - probability), 1e-12)
        hessian = x.T @ (weights[:, None] * x) + ridge_lambda * penalty
        score = x.T @ (y - probability) - ridge_lambda * (penalty @ beta)
        step = np.linalg.pinv(hessian, rcond=bridge3.RANK_TOLERANCE) @ score
        if not np.all(np.isfinite(step)):
            raise ForecastError("IRLS produced a non-finite Newton step")
        beta = beta + step
        iterations = iteration
        if float(np.max(np.abs(step))) <= IRLS_TOLERANCE:
            converged = True
            break
    if not converged or hessian is None:
        raise ForecastError("IRLS did not converge within 100 iterations")
    linear = x @ beta
    probability = np.asarray([_sigmoid(value) for value in linear], dtype=float)
    weights = np.maximum(probability * (1.0 - probability), 1e-12)
    hessian = x.T @ (weights[:, None] * x) + ridge_lambda * penalty
    covariance = np.linalg.pinv(hessian, rcond=bridge3.RANK_TOLERANCE)
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    coefficients = [
        {
            "feature": feature_names[index],
            "coefficient": _rounded(beta[index]),
            "standard_error": _rounded(standard_errors[index]),
        }
        for index in range(columns)
    ]
    return {
        "beta": beta.tolist(),
        "coefficients": coefficients,
        "iterations": iterations,
        "converged": converged,
        "ridge_lambda": ridge_lambda,
        "design_rank": rank,
        "design_columns": columns,
        "rank_deficient": rank_deficient,
        "rank_deficiency_handling": (
            "detected before solve with bridge3 pivoted QR; fixed-ridge Hessian "
            "solved through the bridge3 SVD-pseudo-inverse numerical path"
        ),
        "hessian_condition_number": _rounded(np.linalg.cond(hessian)),
    }


def predict_probabilities(matrix, beta):
    coefficients = np.asarray(beta, dtype=float)
    return [
        _sigmoid(float(np.asarray(row, dtype=float) @ coefficients))
        for row in matrix
    ]


def auc_score(actual, scores):
    pairs = sorted((float(score), int(value)) for value, score in zip(actual, scores))
    positive_n = sum(value for _score, value in pairs)
    negative_n = len(pairs) - positive_n
    if not positive_n or not negative_n:
        return None
    wins = 0.0
    negatives_below = 0
    start = 0
    while start < len(pairs):
        stop = start + 1
        while stop < len(pairs) and pairs[stop][0] == pairs[start][0]:
            stop += 1
        tied = pairs[start:stop]
        tied_positives = sum(value for _score, value in tied)
        tied_negatives = len(tied) - tied_positives
        wins += tied_positives * negatives_below
        wins += 0.5 * tied_positives * tied_negatives
        negatives_below += tied_negatives
        start = stop
    return wins / (positive_n * negative_n)


def brier_score(actual, scores):
    if not actual:
        return None
    return sum((float(score) - int(value)) ** 2 for value, score in zip(actual, scores)) / len(actual)


def precision_at_k(rows, score_field, cutoffs=(10, 25, 50)):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["lob_id"]].append(row)
    output = {}
    for cutoff in cutoffs:
        values = []
        for lob_id in sorted(groups):
            ordered = sorted(
                groups[lob_id],
                key=lambda row: (-float(row[score_field]), row["bioguide_id"]),
            )
            selected = ordered[: min(cutoff, len(ordered))]
            if selected:
                values.append(sum(row["actual"] for row in selected) / len(selected))
        output[f"precision_at_{cutoff}"] = None if not values else sum(values) / len(values)
    return output


def persistence_carryover(rows):
    next_targets = [row for row in rows if int(row["actual"]) == 1]
    if not next_targets:
        return None
    return sum(int(row["baseline_persistence"]) for row in next_targets) / len(next_targets)


def churn_subset(rows):
    return [
        row
        for row in rows
        if int(row["actual"]) != int(row["baseline_persistence"])
    ]


def binary_metrics(rows, prediction_field):
    if not rows:
        return {"n": 0, "accuracy": None, "precision": None, "recall": None}
    tp = sum(
        int(row[prediction_field]) == 1 and int(row["actual"]) == 1
        for row in rows
    )
    fp = sum(
        int(row[prediction_field]) == 1 and int(row["actual"]) == 0
        for row in rows
    )
    fn = sum(
        int(row[prediction_field]) == 0 and int(row["actual"]) == 1
        for row in rows
    )
    correct = sum(
        int(row[prediction_field]) == int(row["actual"]) for row in rows
    )
    return {
        "n": len(rows),
        "accuracy": correct / len(rows),
        "precision": None if tp + fp == 0 else tp / (tp + fp),
        "recall": None if tp + fn == 0 else tp / (tp + fn),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
    }


def calibration_deciles(rows):
    ordered = sorted(
        rows,
        key=lambda row: (
            float(row["predicted_probability"]),
            row["lob_id"],
            row["bioguide_id"],
        ),
    )
    if not ordered:
        return []
    buckets = [[] for _ in range(10)]
    for index, row in enumerate(ordered):
        bucket = min(9, (index * 10) // len(ordered))
        buckets[bucket].append(row)
    output = []
    for index, bucket in enumerate(buckets, start=1):
        if not bucket:
            continue
        probabilities = [float(row["predicted_probability"]) for row in bucket]
        output.append(
            {
                "decile": index,
                "n": len(bucket),
                "min_predicted": _rounded(min(probabilities)),
                "max_predicted": _rounded(max(probabilities)),
                "mean_predicted": _rounded(sum(probabilities) / len(bucket)),
                "observed_rate": _rounded(
                    sum(int(row["actual"]) for row in bucket) / len(bucket)
                ),
            }
        )
    return output


def _score_metrics(rows, score_field):
    actual = [int(row["actual"]) for row in rows]
    scores = [float(row[score_field]) for row in rows]
    result = {
        "auc": _rounded(auc_score(actual, scores)),
        "brier_score": _rounded(brier_score(actual, scores)),
    }
    result.update(
        {key: _rounded(value) for key, value in precision_at_k(rows, score_field).items()}
    )
    return result


def split_churn_verdict(metrics):
    """Compare model churn accuracy with the strongest trivial baseline."""
    model_accuracy = metrics["model"]["churn"]["accuracy"]
    baseline_accuracies = [
        (
            name,
            metrics["baselines"][name]["churn"]["accuracy"],
        )
        # Popularity wins a tied argmax so structurally-zero persistence is
        # never elevated into the headline merely by insertion order.
        for name in ("popularity", "persistence")
    ]
    available = [item for item in baseline_accuracies if item[1] is not None]
    if model_accuracy is None or not available:
        return {
            "outcome": "unavailable",
            "best_baseline": None,
            "best_baseline_churn_accuracy": None,
            "model_churn_accuracy": model_accuracy,
            "headline": "CHURN VERDICT UNAVAILABLE: empty churn subset",
        }
    best_name, best_accuracy = max(available, key=lambda item: item[1])
    model_text = f"{model_accuracy:.1%}"
    baseline_text = f"{best_accuracy:.1%}"
    if model_accuracy > best_accuracy:
        outcome = "win"
        headline = (
            f"MODEL WINS ON CHURN: model {model_text} vs "
            f"{best_name} {baseline_text}"
        )
    elif model_accuracy < best_accuracy:
        outcome = "loss"
        headline = (
            f"MODEL LOSES ON CHURN: {best_name} {baseline_text} vs "
            f"model {model_text}"
        )
    else:
        outcome = "tie"
        headline = (
            f"MODEL TIES BEST BASELINE ON CHURN: {best_name} "
            f"{baseline_text} vs model {model_text}"
        )
    return {
        "outcome": outcome,
        "best_baseline": best_name,
        "best_baseline_churn_accuracy": best_accuracy,
        "model_churn_accuracy": model_accuracy,
        "headline": headline,
    }


def evaluate_split(test_rows):
    churn = churn_subset(test_rows)
    for row in test_rows:
        row["model_prediction"] = int(float(row["predicted_probability"]) >= 0.5)
        row["baseline_popularity_prediction"] = int(
            float(row["baseline_popularity_probability"]) >= 0.5
        )
    model = _score_metrics(test_rows, "predicted_probability")
    persistence = _score_metrics(test_rows, "baseline_persistence")
    popularity = _score_metrics(test_rows, "baseline_popularity_probability")
    model["churn"] = binary_metrics(churn, "model_prediction")
    persistence["churn"] = binary_metrics(churn, "baseline_persistence")
    popularity["churn"] = binary_metrics(churn, "baseline_popularity_prediction")
    persistence["churn"]["structurally_zero_by_construction"] = True
    persistence["churn"]["annotation"] = PERSISTENCE_CHURN_NOTE
    persistence["carryover_among_next_year_targets"] = _rounded(
        persistence_carryover(test_rows)
    )
    result = {
        "n_test_rows": len(test_rows),
        "n_test_orgs": len({row["lob_id"] for row in test_rows}),
        "churn_n": len(churn),
        "churn_base_rate": _rounded(len(churn) / len(test_rows)),
        "positive_rate_within_churn": (
            None
            if not churn
            else _rounded(sum(row["actual"] for row in churn) / len(churn))
        ),
        "model": model,
        "baselines": {
            "persistence": persistence,
            "popularity": popularity,
        },
        "calibration_deciles": calibration_deciles(test_rows),
    }
    result["churn_verdict"] = split_churn_verdict(result)
    result["headline"] = result["churn_verdict"]["headline"]
    return result


def overall_churn_verdict(split_reports):
    """Aggregate split verdicts without recomputing any metric."""
    comparisons = []
    counts = collections.Counter()
    for split in split_reports:
        verdict = split["metrics"]["churn_verdict"]
        outcome = verdict["outcome"]
        counts[outcome] += 1
        comparisons.append(
            {
                "train_through": split.get("train_through"),
                "test_feature_year": split.get("test_feature_year"),
                "outcome": outcome,
                "best_baseline": verdict["best_baseline"],
                "best_baseline_churn_accuracy": verdict[
                    "best_baseline_churn_accuracy"
                ],
                "model_churn_accuracy": verdict["model_churn_accuracy"],
                "headline": verdict["headline"],
            }
        )
    evaluated = counts["win"] + counts["loss"] + counts["tie"]
    if counts["loss"] > evaluated / 2.0:
        lead = "MODEL DOES NOT BEAT TRIVIAL BASELINES"
    elif counts["win"] > counts["loss"] + counts["tie"]:
        lead = "MODEL BEATS TRIVIAL BASELINES ON MOST SPLITS"
    else:
        lead = "MODEL DOES NOT BEAT TRIVIAL BASELINES"
    headline = (
        f"{lead}: won {counts['win']}, lost {counts['loss']}, "
        f"tied {counts['tie']} across {evaluated} evaluated temporal splits "
        "against each split's best churn baseline"
    )
    return {
        "headline": headline,
        "comparison_metric": "churn accuracy",
        "comparison_rule": (
            "model compared with the higher churn accuracy among persistence "
            "and popularity in each split"
        ),
        "splits_evaluated": evaluated,
        "wins": counts["win"],
        "losses": counts["loss"],
        "ties": counts["tie"],
        "unavailable": counts["unavailable"],
        "persistence_churn_note": PERSISTENCE_CHURN_NOTE,
        "per_split": comparisons,
    }


def run_splits(rows, cutoffs, basis_by_year):
    reports = []
    score_rows = []
    for cutoff in cutoffs:
        train, test = make_temporal_split(rows, cutoff)
        years_used = {
            value
            for row in train + test
            for value in (int(row["year"]), int(row["target_year"]))
        }
        corpus_basis = assert_coverage_homogeneous(years_used, basis_by_year)
        excluded_features, missing_feature_receipt = split_missing_feature_policy(
            train + test
        )
        preprocessor = fit_preprocessor(
            train, excluded_features=excluded_features
        )
        train_matrix, train_unseen = transform_rows(train, preprocessor)
        if train_unseen:
            raise ForecastError("training transform unexpectedly produced unseen levels")
        fit = irls_logistic(
            train_matrix,
            [row["actual"] for row in train],
            preprocessor["design_names"],
        )
        test_matrix, unseen = transform_rows(test, preprocessor)
        probabilities = predict_probabilities(test_matrix, fit["beta"])
        scored = []
        for source, probability in zip(test, probabilities):
            row = dict(source)
            row["predicted_probability"] = probability
            row["train_through"] = cutoff
            scored.append(row)
        metrics = evaluate_split(scored)
        coefficient_table = list(fit["coefficients"])
        for feature in preprocessor["dropped_zero_variance_features"]:
            coefficient_table.append(
                {
                    "feature": feature,
                    "coefficient": None,
                    "standard_error": None,
                    "included": False,
                    "reason": "zero variance in training split",
                }
            )
        for feature in sorted(excluded_features):
            coefficient_table.append(
                {
                    "feature": feature,
                    "coefficient": None,
                    "standard_error": None,
                    "included": False,
                    "reason": MISSING_COMMITTEE_REASON,
                }
            )
        reports.append(
            {
                "train_through": cutoff,
                "test_feature_year": cutoff + 1,
                "test_target_year": cutoff + 2,
                "network_corpus_basis": corpus_basis,
                "train_feature_years": sorted({row["year"] for row in train}),
                "n_train_rows": len(train),
                "n_train_orgs": len({row["lob_id"] for row in train}),
                "feature_preprocessing": preprocessor,
                "features_excluded_for_missing_data": missing_feature_receipt,
                "unseen_test_categorical_levels": unseen,
                "model_fit": {
                    key: value for key, value in fit.items() if key != "beta" and key != "coefficients"
                },
                "coefficient_table": coefficient_table,
                "metrics": metrics,
            }
        )
        score_rows.extend(scored)
    return reports, score_rows


def _atomic_text(path, writer):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".forecast-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer(handle)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_outputs(out_dir, document, score_rows):
    json_path = os.path.join(out_dir, "forecast_report.json")
    csv_path = os.path.join(out_dir, "forecast_scores.csv")

    def write_json(handle):
        json.dump(document, handle, sort_keys=True, indent=2, ensure_ascii=False)
        handle.write("\n")

    def write_csv(handle):
        writer = csv.DictWriter(handle, fieldnames=SCORE_FIELDS, lineterminator="\n")
        writer.writeheader()
        for source in sorted(
            score_rows,
            key=lambda row: (row["year"], row["lob_id"], row["bioguide_id"]),
        ):
            writer.writerow(
                {
                    "lob_id": source["lob_id"],
                    "bioguide_id": source["bioguide_id"],
                    "year": source["year"],
                    "target_year": source["target_year"],
                    "train_through": source["train_through"],
                    "predicted_probability": format(
                        float(source["predicted_probability"]), ".12g"
                    ),
                    "actual": source["actual"],
                    "baseline_persistence": source["baseline_persistence"],
                    "baseline_popularity_probability": format(
                        float(source["baseline_popularity_probability"]), ".12g"
                    ),
                    "baseline_popularity_prediction": source[
                        "baseline_popularity_prediction"
                    ],
                }
            )

    _atomic_text(json_path, write_json)
    _atomic_text(csv_path, write_csv)


def print_summary(document):
    print("Lobby relationship forecast — CHURN FIRST")
    for split in document["per_split"]:
        metrics = split["metrics"]
        model = metrics["model"]["churn"]
        persistence = metrics["baselines"]["persistence"]["churn"]
        popularity = metrics["baselines"]["popularity"]["churn"]
        print(
            f"  T={split['train_through']} -> feature {split['test_feature_year']} "
            f"predicts {split['test_target_year']} [{split['network_corpus_basis']}]"
        )
        print(
            "    HEADLINE: "
            + metrics["headline"]
            + f" | churn={metrics['churn_base_rate']:.1%} ({metrics['churn_n']} dyads)"
        )
        print(
            "    churn accuracy: model "
            + ("NA" if model["accuracy"] is None else f"{model['accuracy']:.1%}")
            + " | popularity "
            + (
                "NA"
                if popularity["accuracy"] is None
                else f"{popularity['accuracy']:.1%}"
            )
            + " | persistence "
            + ("NA" if persistence["accuracy"] is None else f"{persistence['accuracy']:.1%}")
            + " (structurally zero by churn definition)"
            + " (historical persistence context: ~82%)"
        )
        print(
            f"    pooled AUC model={metrics['model']['auc']} "
            f"persistence={metrics['baselines']['persistence']['auc']} "
            f"popularity={metrics['baselines']['popularity']['auc']}"
        )
        if split["model_fit"]["rank_deficient"]:
            print(
                "    DESIGN WARNING: rank deficient "
                f"({split['model_fit']['design_rank']}/"
                f"{split['model_fit']['design_columns']}); see JSON handling receipt"
            )
        for feature, receipt in sorted(
            split["features_excluded_for_missing_data"].items()
        ):
            print(
                f"    MISSING-DATA EXCLUSION: {feature}; Congresses "
                + ",".join(str(value) for value in receipt["congresses"])
                + f"; affected rows={receipt['total_rows']}; no zero imputation"
            )
    print("  Features excluded for leakage:")
    for feature in sorted(document["features_excluded_for_leakage"]):
        print(f"    {feature}: {document['features_excluded_for_leakage'][feature]}")
    print(
        "  DESCRIPTIVE only; fixed-penalty coefficients are associations, not effects. "
        "FEC contributions excluded (-0.0002 beyond structural controls)."
    )
    print(document["verdict"]["headline"])


def _parser():
    parser = argparse.ArgumentParser(
        description="Temporally honest descriptive lobbying-relationship forecast."
    )
    parser.add_argument("--networks-dir", required=True, metavar="DIR")
    parser.add_argument(
        "--years", nargs="*", metavar="YEAR", help="years separated by spaces or commas"
    )
    parser.add_argument("--train-through", type=int, required=True, metavar="T")
    parser.add_argument("--out-dir", default=".", metavar="DIR")
    parser.add_argument("--min-org-dyads", type=int, default=25)
    parser.add_argument(
        "--allow-missing-committee",
        action="store_true",
        help=(
            "omit finance_committee from any split touching a Congress without "
            "a pinned snapshot; values are never imputed"
        ),
    )
    return parser


def main(argv=None, environ=None):
    args = _parser().parse_args(argv)
    if args.min_org_dyads < 1:
        print("ERROR: --min-org-dyads must be positive", file=sys.stderr)
        return 2
    env = os.environ if environ is None else environ
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        print("ERROR: LOCALAPPDATA is unset", file=sys.stderr)
        return 2
    networks_dir = os.path.abspath(args.networks_dir)
    rebuild_root = os.path.join(local_app_data, "Melange", "lda_rebuild")
    raw_dir = os.path.join(rebuild_root, "raw")
    receipts_path = os.path.join(networks_dir, "rebuild_receipts.json")
    voteview_path = os.path.join(MB_ROOT, "votes", "HSall_members.csv")
    legislators_path = os.path.join(MB_ROOT, "fec", "legislators.csv")
    committee_dir = os.path.join(raw_dir, "committee_snapshots")
    classified_path = os.path.join(
        local_app_data,
        "Melange",
        "lobbyview",
        "derived",
        "lobbyview_clients_classified.csv",
    )
    try:
        network_paths = champions.discover_network_paths(networks_dir)
        years = champions.parse_years(args.years, set(network_paths))
        if len(years) < 3:
            raise ForecastError("no temporal train/test split available")
        basis_by_year, coverage_provenance = load_coverage_bases(
            receipts_path, years
        )
        org_year, network_provenance = champions.read_networks(
            network_paths, years
        )
        seniority_2026, seniority_provenance = load_voteview_seniority(
            voteview_path
        )
        legislator_roster, legislators_provenance = champions.load_legislators(
            legislators_path
        )
        rosters = build_legislator_rosters(
            legislator_roster, years, seniority_2026
        )
        congresses = sorted(
            {_congress_for_year(year) for year in years}
        )
        selected_set = set(years)
        committee_congresses = sorted(
            {
                _congress_for_year(year)
                for year in years
                if year + 1 in selected_set
            }
        )
        loaded_committees, committee_provenance = champions.load_committee_snapshots(
            committee_dir, committee_congresses
        )
        committees, missing_committees = require_committee_coverage(
            committee_congresses,
            loaded_committees,
            allow_missing=args.allow_missing_committee,
        )
        people_maps, billstatus_provenance = champions.load_bill_people_maps(
            raw_dir, congresses
        )
        introduced_years = load_bill_introduced_years(raw_dir, congresses)
        volumes = legislative_volume_by_year(
            people_maps, introduced_years, years
        )
        classified, classified_provenance = champions.load_classified_names(
            classified_path
        )
        if not classified_provenance.get("available"):
            raise ForecastError("org_side source is unavailable")
        rows, panel_receipt = build_panel(
            org_year,
            years,
            rosters,
            volumes,
            committees,
            classified,
            min_org_dyads=args.min_org_dyads,
            allow_missing_committee=args.allow_missing_committee,
        )
        cutoffs = available_cutoffs(rows, args.train_through)
        per_split, score_rows = run_splits(rows, cutoffs, basis_by_year)
        verdict = overall_churn_verdict(per_split)
        document = {
            "method_note": METHOD_NOTE,
            "descriptive_not_causal": True,
            "historical_persistence_baseline": HISTORICAL_PERSISTENCE,
            "accuracy_reporting_rule": (
                "quote the approximately 82% persistence baseline alongside any accuracy figure"
            ),
            "fec_contribution_data": {
                "included": False,
                "reason": (
                    "preregistered round-3 incremental contribution beyond the same "
                    "structural controls was -0.0002"
                ),
            },
            "features": list(MODEL_FEATURES),
            "feature_definitions": {
                "dyad_history": (
                    "n_bills in Y and Y-1; number of earlier selected covered years "
                    "present; elapsed years from first and last observed contact through Y. "
                    "Elapsed values are zero when no contact has yet been observed."
                ),
                "member_structure": (
                    "fec/legislators.csv roster party/chamber and Voteview-minimum-"
                    "Congress year-adjusted log1p seniority; pinned finance-committee "
                    "snapshot; log1p cumulative "
                    "current-Congress BILLSTATUS people-set volume introduced by Y."
                ),
                "org_context": (
                    "positive roster dyad count in Y, median n_bills among those positive "
                    "dyads, and classified org_side (unknown retained as a level)."
                ),
                "member_popularity": (
                    "number of other network organizations with a positive member dyad in Y"
                ),
            },
            "features_excluded_for_leakage": dict(FEATURES_EXCLUDED_FOR_LEAKAGE),
            "features_excluded_for_missing_data": panel_receipt[
                "features_excluded_for_missing_data"
            ],
            "sanctioned_upgrade_path": (
                "Build year-vintaged stance from press_stance_final.csv, "
                "tweet_stance_final.csv, hearing_stance_final.csv, "
                "speech_stance_final.csv, interview_stance_final.csv, and "
                "bluesky_stance_final.csv by filtering each item to date <= Y; "
                "never use the current aggregate snapshots."
            ),
            "leakage_guard": {
                "rule": "every feature source year must be <= row year Y",
                "structural_assertion": (
                    "assert_as_of_features checks an explicit source-year ledger before "
                    "a row enters any design matrix; targets remain outside that ledger"
                ),
            },
            "model": {
                "family": "plain logistic regression by IRLS",
                "ridge_lambda": RIDGE_LAMBDA,
                "lambda_tuned": False,
                "continuous_standardization": "training-split mean and population SD",
                "categorical_encoding": "one-hot with lexicographically first observed reference dropped",
                "numerical_path": (
                    "bridge3 pivoted-QR rank detection plus SVD pseudo-inverse "
                    "for the fixed-ridge IRLS Hessian"
                ),
            },
            "baseline_definitions": {
                "persistence": (
                    "score/prediction is 1 iff the focal dyad exists in Y; "
                    "carryover is the share of Y+1 targets already present in Y"
                ),
                "popularity": (
                    "score is other-org positive dyads for the member in Y divided by "
                    "the number of other active organizations; ranking uses that score "
                    "and its churn class prediction uses the fixed >=0.5 threshold"
                ),
                "model_churn_prediction_threshold": 0.5,
            },
            "requested_years": years,
            "requested_train_through": args.train_through,
            "evaluated_cutoffs": cutoffs,
            "min_org_dyads": args.min_org_dyads,
            "verdict": verdict,
            "panel_receipt": panel_receipt,
            "provenance": {
                "network_csvs": network_provenance,
                "coverage_receipts": coverage_provenance,
                "network_corpus_basis_by_year": {
                    str(year): basis_by_year[year] for year in sorted(basis_by_year)
                },
                "voteview_seniority": seniority_provenance,
                "legislators_structure": legislators_provenance,
                "feature_sources": {
                    "dyad_history": "strict annual network CSVs through Y",
                    "seniority": (
                        "votes/HSall_members.csv; min(congress) per bioguide only"
                    ),
                    "candidate_roster": "fec/legislators.csv",
                    "chamber": "fec/legislators.csv type",
                    "party": "fec/legislators.csv party",
                    "finance_committee": "pinned committee snapshot by Congress",
                    "legislative_volume": (
                        "BILLSTATUS builder sponsor/cosponsor people-sets censored by introducedDate <= Y"
                    ),
                    "org_total_dyads_and_median": "strict annual network CSV for Y",
                    "org_side": "lobbyview_clients_classified.csv side",
                    "member_popularity": "strict annual network CSV for Y, other orgs only",
                    "voteview_single_row_resolution": (
                        "not used by this harness; resolver is retained and tested for future callers"
                    ),
                },
                "voteview_row_resolution_receipts": [],
                "committee_snapshots": committee_provenance,
                "committee_snapshot_lookup": {
                    "directory": os.path.abspath(committee_dir),
                    "key_normalization": "Congress keys normalized to int at load boundary",
                    "requested_congresses": committee_congresses,
                    "available_congresses": sorted(committees),
                    "missing_congresses": missing_committees,
                    "allow_missing_committee": args.allow_missing_committee,
                },
                "billstatus_people_sets": billstatus_provenance,
                "billstatus_volume_censoring": (
                    "builder-expanded sponsor/cosponsor people-sets; current-Congress "
                    "bills counted only when introducedDate <= Y"
                ),
                "org_side": classified_provenance,
            },
            "per_split": per_split,
        }
        write_outputs(args.out_dir, document, score_rows)
        print_summary(document)
    except (ForecastError, champions.InstrumentError, builder.IngestError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
