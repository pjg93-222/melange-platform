# -*- coding: utf-8 -*-
"""Temporally honest descriptive forecast of congressional cosponsorship.

The unit is ``(bill, member)``.  Bills are finance-relevant when either (1) a
member of the builder's pinned 22-organization finance roster named the bill
on the strict lobbying channel during its Congress, or (2) BILLSTATUS records
an introduction-date referral to House Financial Services, Senate Banking,
House Agriculture, or Senate Agriculture.  Bills below ``--min-cosponsors``
are excluded, and a bill's sponsor is never a candidate.  Every other member
in the Voteview roster for that Congress is a genuine candidate and therefore
a real zero when absent from the final cosponsor list.

Voteview identity rows are loaded and conflict-validated only for requested
Congresses and their direct priors.  Earlier rows contribute solely to the
existing min-Congress seniority scan, which reads no chamber, state, party,
ICPSR, or NOMINATE field.  Same-chamber party switches prefer the ICPSR carried
into the following Congress, falling back to Voteview's higher post-switch
ICPSR convention when no following identity exists; every collapse is
receipted with the discarded party/ICPSR identity.

Every propensity, sponsor-affinity, and network feature is computed from HR
and S bills in the PRIOR Congress only.  This HR/S restriction is uniform: it
puts Congresses 116, 117, and 118 on the same historical basis instead of
letting later splits exploit six extra bill types.  No final cosponsor count,
action, passage field, or post-introduction field enters the design matrix.

``prior_committee_coservice`` is dropped split-wide from both evaluations:
the required 117th-Congress snapshot does not exist on disk, and replacing it
with same-Congress service would be temporal contamination.  The separate
``member_on_referred_committee`` feature is retained only when a snapshot for
the bill's Congress exists.  Committee assignments are ordinarily made at the
start of a Congress and are therefore known before the great majority of bills
are introduced.  The pinned files are point-in-time captures, however, not
dated rosters, so this feature has the stated small imprecision that it can
reflect mid-Congress committee changes.

This is DESCRIPTIVE forecasting, not causal inference.  Fixed-lambda ridge
coefficients are associations, not effects.  Cosponsorship is predicted as
the SUBSTRATE of lobbying attention, not as lobbying itself.

The full panel is stored in raw-double column arrays.  IRLS streams 25,000-row
design chunks into p-by-p normal equations; it never materializes full X.
Rank is checked by pivoted QR and SVD on the unpenalized X'WX Gram matrix,
whose condition number is explicitly labeled as Gram-scale (approximately
the square of weighted-X conditioning).  AUC and average precision are exact
external merges of chunk-sorted scores, not binned approximations, and the
score CSV is streamed through temporary split spools.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, to_host

import argparse
import array
import collections
import csv
import datetime
import hashlib
import heapq
import io
import json
import math
import os
import re
import struct
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
import zipfile

from lobbying import build_network_2021_rebuild as builder
from lobbying import fec_lda_bridge3_look as bridge3
from lobbying import lobby_champions as champions
from lobbying import lobby_forecast as prior_harness


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ForecastError = prior_harness.ForecastError
LeakageError = prior_harness.LeakageError
irls_logistic = prior_harness.irls_logistic
predict_probabilities = prior_harness.predict_probabilities
RIDGE_LAMBDA = prior_harness.RIDGE_LAMBDA
np = prior_harness.np

DEFAULT_CONGRESSES = (117, 118, 119)
DEFAULT_TRAIN_THROUGH = 118
LIVE_CONGRESS = 119
DEFAULT_SYSTEMS_ROOT = to_host(r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems")   # identity on Windows; the container's mount there
PRIOR_BILL_TYPES = frozenset({"HR", "S"})
FINANCE_REFERRAL_CODES = frozenset({"HSBA", "SSBK", "HSAG", "SSAF"})
# A 1-of-1 sponsor history must not be worth 17 SD; at M=10 it is worth ~1.6 SD.
AFFINITY_SHRINK_M = 10.0
# Candidate-row levels below this floor train the explicit fallback bucket.
CATEGORICAL_POOL_MIN_ROWS = 1000
CATEGORICAL_OTHER_LEVEL = "__OTHER__"
CALIBRATION_METHOD = "isotonic_pool_adjacent_violators"
RELIABILITY_BIN_COUNT = 10
PRIOR_COSERVICE_REASON = (
    "requires a 117th-Congress committee snapshot, which does not exist on disk"
)
MEMBER_COMMITTEE_REASON = (
    "target-Congress point-in-time committee snapshot unavailable; feature "
    "omitted from the entire split and never imputed to zero"
)
METHOD_NOTE = (
    "DESCRIPTIVE forecasting, not causal inference; coefficients are associations "
    "under a fixed lambda=1.0 ridge penalty. Cosponsorship is predicted as the "
    "SUBSTRATE of lobbying attention, not as lobbying itself. Every prior-history "
    "feature uses HR/S bills in the prior Congress only. Committee assignments are "
    "normally made at the start of a Congress and are substantively known at bill "
    "introduction, but the committee inputs are point-in-time captures rather than "
    "dated rosters and can reflect mid-Congress changes. For the live Congress "
    "(the 119th), members absent from fec/legislators.csv at scoring time are "
    "excluded as candidates; their joins are not counted (20 pre-snapshot 119th "
    "joins as of 2026-09-04)."
)

CONTINUOUS_FEATURES = (
    "prior_sponsor_affinity_count",
    "prior_sponsor_affinity_rate",
    "prior_member_cosponsorships",
    "prior_network_degree",
    "prior_network_eigenvector",
    "member_nominate_dim1",
    "member_nominate_dim2",
    "sponsor_nominate_dim1",
    "sponsor_nominate_dim2",
    "nominate_dim1_abs_distance",
    "nominate_dim2_abs_distance",
    "member_ideology_missing",
    "sponsor_ideology_missing",
    "ideology_distance_missing",
    "same_party",
    "same_state",
    "same_chamber",
    "member_seniority",
    "member_on_referred_committee",
    "prior_committee_coservice",
    "bill_committee_referrals",
    "sponsor_majority_party",
)
CATEGORICAL_FEATURES = ("bill_type", "policy_area")
MODEL_FEATURES = CONTINUOUS_FEATURES + CATEGORICAL_FEATURES
OPTIONAL_FEATURE_REASONS = {
    "prior_committee_coservice": PRIOR_COSERVICE_REASON,
    "member_on_referred_committee": MEMBER_COMMITTEE_REASON,
}
PRIOR_FEATURES = frozenset(
    {
        "prior_sponsor_affinity_count",
        "prior_sponsor_affinity_rate",
        "prior_member_cosponsorships",
        "prior_network_degree",
        "prior_network_eigenvector",
        "member_nominate_dim1",
        "member_nominate_dim2",
        "sponsor_nominate_dim1",
        "sponsor_nominate_dim2",
        "nominate_dim1_abs_distance",
        "nominate_dim2_abs_distance",
        "member_ideology_missing",
        "sponsor_ideology_missing",
        "ideology_distance_missing",
        "prior_committee_coservice",
    }
)
POST_INTRODUCTION_FIELDS = frozenset(
    {
        "cosponsor_count",
        "sponsorship_date",
        "actions",
        "latest_action",
        "passage",
        "enacted",
    }
)
SCORE_FIELDS = (
    "candidate_configuration",
    "congress",
    "bill",
    "member",
    "predicted_probability",
    "calibrated_probability",
    "actual",
    "baseline_member_propensity",
    "baseline_member_propensity_probability",
    "baseline_sponsor_affinity",
    "baseline_sponsor_affinity_probability",
    "baseline_party_match",
)
STREAM_CHUNK_ROWS = 25000
_SCORE_RECORD = struct.Struct("<dB")


class ColumnarPanel:
    """Compact append-only panel with raw-double feature columns.

    Repeated strings are dictionary encoded and identifiers are integer coded.
    ``row_dict`` and iteration retain the small-fixture compatibility used by
    the leakage tests without materializing dictionaries for the real panel.
    """

    def __init__(self):
        self.numeric = {
            feature: array.array("d") for feature in CONTINUOUS_FEATURES
        }
        self.categorical_codes = {
            feature: array.array("I") for feature in CATEGORICAL_FEATURES
        }
        self.categorical_levels = {feature: [] for feature in CATEGORICAL_FEATURES}
        self._categorical_index = {feature: {} for feature in CATEGORICAL_FEATURES}
        self.actual = array.array("b")
        self.congress = array.array("H")
        self.bill_index = array.array("I")
        self.member_index = array.array("I")
        self.member_propensity_probability = array.array("d")
        self.sponsor_affinity_totals = array.array("d")
        self.bills = []
        self.members = []
        self._member_index = {}
        self.congress_ranges = {}
        self.unavailable_by_congress = {}
        self.missing_congresses_by_feature = {}
        self.expected_features_by_congress = {}

    def __len__(self):
        return len(self.actual)

    def __iter__(self):
        for index in range(len(self)):
            yield self.row_dict(index)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self.row_dict(i) for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return self.row_dict(index)

    def register_bill(self, bill):
        self.bills.append(dict(bill))
        return len(self.bills) - 1

    def _member_code(self, bioguide, display_name):
        code = self._member_index.get(bioguide)
        if code is None:
            code = len(self.members)
            self._member_index[bioguide] = code
            self.members.append(
                {"bioguide_id": bioguide, "member": display_name or bioguide}
            )
        return code

    def _categorical_code(self, feature, value):
        value = str(value)
        code = self._categorical_index[feature].get(value)
        if code is None:
            code = len(self.categorical_levels[feature])
            self._categorical_index[feature][value] = code
            self.categorical_levels[feature].append(value)
        return code

    def set_availability(self, congress, unavailable, missing_congresses):
        congress = int(congress)
        self.unavailable_by_congress[congress] = frozenset(unavailable)
        self.expected_features_by_congress[congress] = frozenset(MODEL_FEATURES) - set(
            unavailable
        )
        self.missing_congresses_by_feature[congress] = {
            feature: tuple(values) for feature, values in missing_congresses.items()
        }

    def append(
        self,
        congress,
        bill_code,
        bioguide,
        display_name,
        actual,
        values,
        member_propensity_probability,
        sponsor_affinity_total,
    ):
        congress = int(congress)
        unavailable = self.unavailable_by_congress[congress]
        expected = self.expected_features_by_congress[congress]
        if values.keys() != expected:
            raise ForecastError(
                "columnar feature schema mismatch: "
                + repr(sorted(expected.symmetric_difference(values.keys())))
            )
        start_stop = self.congress_ranges.get(congress)
        if start_stop is None:
            self.congress_ranges[congress] = [len(self), len(self)]
        elif start_stop[1] != len(self):
            raise ForecastError("columnar rows for a Congress must be contiguous")
        for feature in CONTINUOUS_FEATURES:
            self.numeric[feature].append(
                0.0 if feature in unavailable else float(values[feature])
            )
        for feature in CATEGORICAL_FEATURES:
            self.categorical_codes[feature].append(
                self._categorical_code(feature, values[feature])
            )
        self.actual.append(int(actual))
        self.congress.append(congress)
        self.bill_index.append(int(bill_code))
        self.member_index.append(self._member_code(bioguide, display_name))
        self.member_propensity_probability.append(
            float(member_propensity_probability)
        )
        self.sponsor_affinity_totals.append(float(sponsor_affinity_total))
        self.congress_ranges[congress][1] = len(self)

    def bounds(self, congress):
        try:
            start, stop = self.congress_ranges[int(congress)]
        except KeyError as exc:
            raise ForecastError(f"panel has no rows for Congress {congress}") from exc
        return start, stop

    def categorical_value(self, feature, index):
        code = self.categorical_codes[feature][index]
        return self.categorical_levels[feature][code]

    def row_dict(self, index):
        congress = int(self.congress[index])
        bill = self.bills[self.bill_index[index]]
        member = self.members[self.member_index[index]]
        unavailable = self.unavailable_by_congress[congress]
        values = {
            feature: self.numeric[feature][index]
            for feature in CONTINUOUS_FEATURES
            if feature not in unavailable
        }
        values.update(
            {
                feature: self.categorical_value(feature, index)
                for feature in CATEGORICAL_FEATURES
            }
        )
        source_ledger = {
            feature: (congress - 1 if feature in PRIOR_FEATURES else congress)
            for feature in values
        }
        row = {
            "congress": congress,
            "bill": bill["bill"],
            "bill_number": bill["bill_number"],
            "bioguide_id": member["bioguide_id"],
            "member": member["member"],
            "sponsor_bioguide": bill["sponsor_bioguide"],
            "actual": int(self.actual[index]),
            "baseline_member_propensity": values["prior_member_cosponsorships"],
            "baseline_member_propensity_probability": self.member_propensity_probability[index],
            "baseline_sponsor_affinity": values["prior_sponsor_affinity_count"],
            "baseline_sponsor_affinity_probability": values["prior_sponsor_affinity_rate"],
            "baseline_party_match": values["same_party"],
            "_introduced_date": bill["introduced_date"],
            "_features_unavailable": tuple(sorted(unavailable)),
            "_missing_feature_congresses": dict(
                self.missing_congresses_by_feature[congress]
            ),
            "_feature_source_congresses": source_ledger,
        }
        row.update(values)
        return row


class StreamedScores:
    def __init__(self, sources):
        self.sources = tuple(
            sorted(
                sources,
                key=lambda item: (item["congress"], item["configuration"]),
            )
        )


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


def _record_input(inputs, path, role):
    absolute = os.path.abspath(path)
    prior = inputs.get(absolute)
    if prior is None:
        inputs[absolute] = {
            "path": absolute,
            "sha256": _sha256_file(absolute),
            "roles": [role],
        }
    elif role not in prior["roles"]:
        prior["roles"].append(role)


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


def _parse_int(value, label):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError) as exc:
        raise ForecastError(f"{label} must be integer-like") from exc


def parse_congresses(values):
    if not values:
        return list(DEFAULT_CONGRESSES)
    tokens = []
    for value in values:
        tokens.extend(part for part in str(value).split(",") if part.strip())
    try:
        congresses = sorted({int(token.strip()) for token in tokens})
    except ValueError as exc:
        raise ForecastError("--congresses must contain integers") from exc
    if len(congresses) < 2:
        raise ForecastError("at least two consecutive Congresses are required")
    return congresses


def _voteview_float(row, field):
    raw = (row.get(field) or "").strip()
    if not raw:
        return None
    value = _finite(raw, field)
    return value


def _normalize_voteview_row(row):
    chamber = (row.get("chamber") or "").strip().title()
    if chamber not in {"House", "Senate"}:
        raise ForecastError("resolved Voteview row has invalid chamber")
    return {
        "bioguide_id": (row.get("bioguide_id") or "").strip(),
        "congress": _parse_int(row.get("congress"), "Voteview congress"),
        "chamber": chamber,
        "party_code": _parse_int(row.get("party_code"), "Voteview party_code"),
        "state": (row.get("state_abbrev") or "").strip().upper(),
        "icpsr": (row.get("icpsr") or "").strip(),
        "nominate_dim1": _voteview_float(row, "nominate_dim1"),
        "nominate_dim2": _voteview_float(row, "nominate_dim2"),
        "member_name": (row.get("bioname") or "").strip(),
    }


def build_candidate_universes(voteview_rows, congresses, enforce_expected=False):
    """Resolve only target Congresses and their priors from Voteview.

    The all-history pass reads only ``bioguide_id`` and ``congress`` to retain
    the shipped earliest-Congress seniority derivation.  Chamber, state, party,
    ICPSR, and NOMINATE fields outside the loaded identity window are ignored
    and never sent to the conflict resolver.
    """
    selected = set(int(value) for value in congresses)
    loaded_congresses = selected | {congress - 1 for congress in selected}
    eligible = []
    earliest = {}
    ignored_identity_rows = 0
    for source in voteview_rows:
        bioguide = (source.get("bioguide_id") or "").strip()
        try:
            congress = int(source.get("congress") or "")
        except (TypeError, ValueError):
            continue
        if bioguide:
            earliest[bioguide] = min(congress, earliest.get(bioguide, congress))
        if congress not in loaded_congresses:
            ignored_identity_rows += 1
            continue
        chamber = (source.get("chamber") or "").strip().casefold()
        if not bioguide or chamber not in {"house", "senate"}:
            continue
        row = dict(source)
        row["bioguide_id"] = bioguide
        row["congress"] = congress
        eligible.append(row)
    resolved, all_receipts = prior_harness.resolve_voteview_congress_rows(eligible)
    universes = {}
    for congress in sorted(selected):
        universe = {
            bioguide: _normalize_voteview_row(row)
            for (bioguide, row_congress), row in sorted(resolved.items())
            if row_congress == congress
        }
        if not universe:
            raise ForecastError(f"Voteview candidate universe is empty for Congress {congress}")
        if enforce_expected and not 540 <= len(universe) <= 560:
            raise ForecastError(
                f"Voteview candidate count for Congress {congress} is {len(universe)}, "
                "outside the expected 540-560 historical range"
            )
        universes[congress] = universe
    receipts = list(all_receipts)
    receipt = {
        "candidate_counts": {
            str(congress): len(universes[congress]) for congress in sorted(universes)
        },
        "current_roster_consulted": False,
        "identity_rows_loaded_congresses": sorted(loaded_congresses),
        "identity_rows_validated": len(eligible),
        "identity_rows_outside_scope_ignored": ignored_identity_rows,
        "seniority_scan": (
            "min(congress) per bioguide across the file; no chamber/state/party/"
            "ICPSR/NOMINATE field outside the loaded identity window is validated"
        ),
        "dedupe_receipts": receipts,
        "mid_congress_senate_preferences": sum(
            item["rule"] == "mid_congress_chamber_move_prefer_senate"
            for item in receipts
        ),
        "party_switch_carried_forward_preferences": sum(
            item["rule"] == "mid_congress_party_switch_carried_forward"
            for item in receipts
        ),
        "party_switch_higher_icpsr_tiebreaks": sum(
            item["rule"] == "mid_congress_party_switch_higher_icpsr_tiebreak"
            for item in receipts
        ),
    }
    return universes, resolved, earliest, receipt


def load_voteview(path, congresses, inputs, enforce_expected=True):
    data = _read_bytes(path, "Voteview members")
    _record_input(inputs, path, "candidate_universe_ideology_seniority")
    rows = csv.DictReader(io.StringIO(_decode(data, path), newline=""))
    universes, resolved, earliest, receipt = build_candidate_universes(
        rows, congresses, enforce_expected=enforce_expected
    )
    receipt.update({"path": os.path.abspath(path), "sha256": _sha256_bytes(data)})
    return universes, resolved, earliest, receipt


def _normalize_committee_code(value):
    code = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return code[:-2] if code.endswith("00") else code


def load_committee_maps(committee_dir, congresses, inputs):
    try:
        names = os.listdir(committee_dir)
    except FileNotFoundError:
        names = []
    except OSError as exc:
        raise ForecastError(f"cannot list committee snapshots: {exc}") from exc
    available = {}
    provenance = []
    for congress in sorted(set(congresses)):
        pattern = re.compile(
            r"committee-membership-" + str(congress) + r"-[0-9A-Fa-f]{40}\.yaml$"
        )
        matches = sorted(name for name in names if pattern.fullmatch(name))
        if len(matches) > 1:
            raise ForecastError(f"multiple committee snapshots for Congress {congress}")
        if not matches:
            continue
        path = os.path.join(committee_dir, matches[0])
        data = _read_bytes(path, "committee snapshot")
        _record_input(inputs, path, "committee_membership")
        parsed = bridge3.parse_committee_snapshot_text(_decode(data, path))
        available[congress] = {
            _normalize_committee_code(code): set(members)
            for code, members in parsed.items()
        }
        provenance.append(
            {
                "congress": congress,
                "path": os.path.abspath(path),
                "sha256": _sha256_bytes(data),
            }
        )
    return available, provenance


def load_billstatus(raw_dir, congresses, inputs):
    loaded = {}
    provenance = []
    for congress in sorted(set(congresses)):
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES if congress in {115, 116} else builder.ZIP_TYPES
        )
        archive_rows = []
        directory = os.path.join(raw_dir, f"billstatus{congress}")
        for archive_token, type_token in zip_types:
            path = os.path.join(directory, f"BILLSTATUS-{congress}-{archive_token}.zip")
            _record_input(inputs, path, f"billstatus_congress_{congress}")
            # Observational vintage guard: the path above was built from the loop
            # variable, so only the archive's own records can witness which
            # Congress these bytes actually describe.
            observed_congress = assert_billstatus_vintage(path, congress)
            archive_rows.append(
                {
                    "bill_type": type_token,
                    "path": os.path.abspath(path),
                    "sha256": _sha256_file(path),
                    "observed_congress": observed_congress,
                    "vintage_witness": "BILLSTATUS record <congress> element",
                }
            )
        result = builder.load_sponsor_map(
            directory,
            congress=congress,
            zip_types=zip_types,
            include_forecast_metadata=True,
        )
        loaded[congress] = {
            "people": result[0],
            "keys": result[1],
            "title_index": result[3],
            "metadata": result[6],
        }
        provenance.append(
            {
                "congress": congress,
                "archives": archive_rows,
                "parser_receipt": result[5],
            }
        )
    return loaded, provenance


def _date_prefix(value, label):
    raw = str(value or "")[:10]
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError as exc:
        raise ForecastError(f"invalid {label} date {value!r}") from exc


def introduction_referral_codes(metadata):
    """Return only referrals recorded no later than the introduction date."""
    introduced = _date_prefix(metadata["introduced_date"], "introduction")
    codes = set()
    for committee in metadata.get("committee_referrals", ()):
        qualifies = False
        for activity in committee.get("activities", ()):
            if str(activity.get("name") or "").strip().casefold() != "referred to":
                continue
            if _date_prefix(activity.get("date"), "committee referral") <= introduced:
                qualifies = True
                break
        if qualifies:
            code = _normalize_committee_code(committee.get("system_code"))
            if code:
                codes.add(code)
    return frozenset(codes)


def _bill_label(key):
    return f"{key[0]} {key[1]}"


def _bill_key_from_receipt(row):
    try:
        return (str(row["type"]).upper(), int(row["number"]), int(row["congress"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastError("malformed lobbied-bill receipt row") from exc


def load_lobbied_bill_keys(paths, billstatus, congresses, inputs):
    """Load strict-channel bills for the pinned 22-organization finance roster."""
    requested = set(congresses)
    output = {congress: set() for congress in requested}
    receipts = {}
    if 117 in requested:
        worklist_path = paths["bridge3_worklist"]
        worklist, worklist_md5 = builder.load_worklist(worklist_path)
        manifest_path = builder.worklist_manifest_path(paths, worklist_path)
        manifest = builder.load_worklist_manifest(manifest_path, worklist_md5)
        selected = [
            row
            for row in worklist
            if row["filing_year"] in {2021, 2022}
            and row["lob_id"] in builder.BRIDGE3_LOB_IDS
        ]
        filing_paths, missing = builder.discover_filing_paths(
            paths, selected, manifest, require_complete=False
        )
        available = [row for row in selected if row["report_uuid"] in filing_paths]
        _record_input(inputs, worklist_path, "finance_roster_lobbying_worklist")
        _record_input(inputs, manifest_path, "finance_roster_lobbying_manifest")
        for path in sorted(set(filing_paths.values())):
            _record_input(inputs, path, "finance_roster_lobbying_filing")
        people_maps = {
            congress: billstatus[congress]["people"] for congress in billstatus
        }
        per_year = {}
        for filing_year in (2021, 2022):
            year_rows = [row for row in available if row["filing_year"] == filing_year]
            result = builder.parse_filings(
                year_rows,
                title_index=billstatus[117]["title_index"],
                filing_paths=filing_paths,
                default_filing_year=filing_year,
                bill_people_maps=people_maps,
            )
            keys = {
                key
                for key in result["distinct_bills_strict"]
                if key[2] == 117 and key in billstatus[117]["keys"]
            }
            output[117].update(keys)
            per_year[str(filing_year)] = {
                "available_filings": len(year_rows),
                "strict_current_congress_bills": len(keys),
            }
        receipts["117"] = {
            "source": "builder union_bridge3_worklist strict channel",
            "finance_roster": "builder.BRIDGE3_LOB_IDS pinned 22",
            "worklist_records": len(selected),
            "missing_certified_filings": missing,
            "per_filing_year": per_year,
            "distinct_bills": len(output[117]),
        }
    receipt_path = paths["receipts"]
    later = requested & {118, 119}
    if later:
        data = _read_bytes(receipt_path, "rebuild receipts")
        _record_input(inputs, receipt_path, "finance_roster_lobbying_receipts")
        try:
            document = json.loads(_decode(data, receipt_path))
        except json.JSONDecodeError as exc:
            raise ForecastError(f"invalid rebuild receipts: {exc}") from exc
        for congress in sorted(later):
            filing_years = (2023, 2024) if congress == 118 else (2025, 2026)
            per_year = {}
            for filing_year in filing_years:
                year = (document.get("years") or {}).get(str(filing_year))
                if not isinstance(year, dict):
                    raise ForecastError(f"rebuild receipts missing year {filing_year}")
                rows = ((year.get("network") or {}).get("per_dyad_bills_strict"))
                if not isinstance(rows, list):
                    raise ForecastError(
                        f"rebuild receipts year {filing_year} lacks strict bill ledger"
                    )
                year_keys = set()
                for dyad in rows:
                    for bill in dyad.get("bill_keys", []):
                        key = _bill_key_from_receipt(bill)
                        if key[2] == congress and key in billstatus[congress]["keys"]:
                            year_keys.add(key)
                output[congress].update(year_keys)
                per_year[str(filing_year)] = len(year_keys)
            receipts[str(congress)] = {
                "source": "builder rebuild_receipts strict per-dyad bill ledger",
                "finance_roster": "builder.BRIDGE3_LOB_IDS pinned 22",
                "per_filing_year_distinct_bills": per_year,
                "distinct_bills": len(output[congress]),
            }
    return output, receipts


def compute_prior_statistics(metadata_by_bill):
    """Compute every behavioral prior on the pinned HR/S-only basis."""
    member_counts = collections.Counter()
    affinity = collections.Counter()
    sponsor_totals = collections.Counter()
    adjacency = collections.defaultdict(collections.Counter)
    nodes = set()
    eligible = [
        (key, metadata_by_bill[key])
        for key in sorted(metadata_by_bill)
        if key[0] in PRIOR_BILL_TYPES
    ]
    for _key, metadata in eligible:
        sponsor = metadata.get("sponsor_bioguide")
        cosponsors = {
            item["bioguide_id"] for item in metadata.get("cosponsors", ())
        }
        nodes.update(cosponsors)
        if sponsor:
            nodes.add(sponsor)
            sponsor_totals[sponsor] += 1
        for member in cosponsors:
            member_counts[member] += 1
            if sponsor and member != sponsor:
                affinity[(member, sponsor)] += 1
                adjacency[member][sponsor] += 1.0
                adjacency[sponsor][member] += 1.0
    degree = {node: len(adjacency[node]) for node in nodes}
    centrality = _eigenvector_centrality(nodes, adjacency)
    return {
        "member_cosponsorships": member_counts,
        "affinity": affinity,
        "sponsor_totals": sponsor_totals,
        "degree": degree,
        "eigenvector": centrality,
        "bill_count": len(eligible),
        "network_definition": (
            "undirected weighted sponsor-cosponsor projection; one edge-weight "
            "increment per HR/S bill; degree is unique-neighbor count"
        ),
    }


def _eigenvector_centrality(nodes, adjacency, max_iterations=1000, tolerance=1e-12):
    ordered = sorted(nodes)
    if not ordered:
        return {}
    scale = math.sqrt(len(ordered))
    vector = {node: 1.0 / scale for node in ordered}
    for _iteration in range(max_iterations):
        # The identity shift prevents a bipartite sign/period-two oscillation
        # without changing the eigenvectors of the symmetric adjacency matrix.
        updated = {
            node: vector[node]
            + sum(weight * vector[neighbor] for neighbor, weight in adjacency[node].items())
            for node in ordered
        }
        norm = math.sqrt(sum(value * value for value in updated.values()))
        if norm == 0.0:
            return {node: 0.0 for node in ordered}
        updated = {node: value / norm for node, value in updated.items()}
        delta = max(abs(updated[node] - vector[node]) for node in ordered)
        vector = updated
        if delta <= tolerance:
            break
    return vector


def _ideology(resolved, bioguide, prior_congress):
    source = resolved.get((bioguide, prior_congress))
    if source is None:
        return 0.0, 0.0, 1
    row = _normalize_voteview_row(source)
    if row["nominate_dim1"] is None or row["nominate_dim2"] is None:
        return 0.0, 0.0, 1
    return row["nominate_dim1"], row["nominate_dim2"], 0


def _majority_party_code(congress, chamber):
    mapping = {
        (117, "House"): 100,
        (117, "Senate"): 100,
        (118, "House"): 200,
        (118, "Senate"): 100,
        (119, "House"): 200,
        (119, "Senate"): 200,
    }
    try:
        return mapping[(int(congress), chamber)]
    except KeyError as exc:
        raise ForecastError(f"majority-party mapping missing for {congress}/{chamber}") from exc


def select_finance_bills(metadata_by_bill, lobbied_keys, min_cosponsors):
    selected = []
    counts = collections.Counter()
    for key in sorted(metadata_by_bill):
        metadata = metadata_by_bill[key]
        sponsor = metadata.get("sponsor_bioguide")
        cosponsors = {
            item["bioguide_id"] for item in metadata.get("cosponsors", ())
        }
        referral_codes = introduction_referral_codes(metadata)
        by_lobbying = key in lobbied_keys
        by_committee = bool(referral_codes & FINANCE_REFERRAL_CODES)
        if not (by_lobbying or by_committee):
            continue
        counts["finance_before_minimum"] += 1
        counts["lobbying_arm_before_minimum"] += int(by_lobbying)
        counts["committee_arm_before_minimum"] += int(by_committee)
        counts["overlap_before_minimum"] += int(by_lobbying and by_committee)
        if not sponsor:
            counts["excluded_missing_sponsor"] += 1
            continue
        if len(cosponsors) < min_cosponsors:
            counts["excluded_below_minimum"] += 1
            continue
        counts["selected"] += 1
        counts["lobbying_arm_selected"] += int(by_lobbying)
        counts["committee_arm_selected"] += int(by_committee)
        counts["overlap_selected"] += int(by_lobbying and by_committee)
        selected.append(
            {
                "key": key,
                "metadata": metadata,
                "cosponsors": cosponsors,
                "referral_codes": referral_codes,
                "finance_by_lobbying": by_lobbying,
                "finance_by_committee": by_committee,
            }
        )
    return selected, dict(counts)


def _member_on_referred_committee(member, referral_codes, committee_map):
    return int(
        any(member in committee_map.get(code, set()) for code in referral_codes)
    )


def _members_shared_prior_committee(member, sponsor, committee_map):
    return int(
        any(
            member in committee_members and sponsor in committee_members
            for committee_members in committee_map.values()
        )
    )


def observe_billstatus_congress(path, sample=1):
    """Read the Congress an archive declares in its OWN bytes.

    The loader derives both the directory and the filename from its loop
    variable, so anything parsed back out of the path restates the rule it
    would be used to test.  The ``<congress>`` element inside a BILLSTATUS
    record is written by GovInfo, travels with the data, and is therefore the
    only vintage statement in this pipeline that does not descend from our own
    control flow.  Returns the set of Congresses the sampled records declare.
    """
    declared = set()
    try:
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".xml")]
            for name in sorted(members)[:sample]:
                root = ElementTree.fromstring(archive.read(name))
                for element in root.iter("congress"):
                    text = (element.text or "").strip()
                    if text.isdigit():
                        declared.add(int(text))
                        break
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise LeakageError(f"cannot observe BILLSTATUS vintage for {path}: {exc}") from exc
    if not declared:
        raise LeakageError(f"BILLSTATUS archive declares no Congress: {path}")
    return declared


def assert_billstatus_vintage(path, expected_congress):
    """Fail when an archive's own records disagree with the Congress assumed.

    This is the observational half of the temporal guarantee.  ``expected_congress``
    comes from the loader's control flow; the returned set comes from GovInfo's
    bytes.  A mis-indexed load -- reading Congress T where T-1 was intended --
    is visible here and nowhere else in the module.
    """
    expected = int(expected_congress)
    declared = observe_billstatus_congress(path)
    if declared != {expected}:
        raise LeakageError(
            f"BILLSTATUS vintage mismatch for {os.path.basename(path)}: records "
            f"declare Congress {sorted(declared)}, loader assumed {expected}"
        )
    return expected


def assert_no_leakage(row):
    """Enforce sponsor exclusion and the declared-feature schema.

    SCOPE, stated because this function previously overclaimed it: the row
    ledger cannot witness temporal provenance.  ``row_dict`` synthesises
    ``_feature_source_congresses`` from PRIOR_FEATURES membership, the same rule
    any congress comparison here would test, so those clauses were tautologies
    and have been removed rather than left to read as guarantees.  Temporal
    separation is enforced constructively by the ``congress - 1`` lookups in
    build_panel and observed by assert_billstatus_vintage; the schema clauses
    below compare fields whose values come from different places, so they can
    and do fire.
    """
    if row.get("bioguide_id") == row.get("sponsor_bioguide"):
        raise LeakageError("bill sponsor entered its own candidate set")
    overlap = sorted(set(row) & POST_INTRODUCTION_FIELDS)
    if overlap:
        raise LeakageError("post-introduction design field present: " + repr(overlap))
    unavailable = set(row.get("_features_unavailable", ()))
    invalid = sorted(unavailable - set(OPTIONAL_FEATURE_REASONS))
    if invalid:
        raise LeakageError("unknown unavailable features " + repr(invalid))
    for feature in MODEL_FEATURES:
        if feature in unavailable:
            if feature in row:
                raise LeakageError(f"unavailable feature {feature} was populated")
            continue
        if feature not in row:
            raise LeakageError(f"feature {feature} lacks a value")
    return True


def assert_panel_invariants(panel):
    """Structural checks on the finished panel, on the production path.

    Runs once per built panel, not per row.  The sponsor comparison is
    vectorised over the whole panel: it catches a mis-indexed candidate loop,
    which the per-bill ``continue`` cannot, because the two sides are resolved
    through different index columns after the fact.
    """
    design = set(MODEL_FEATURES)
    overlap = sorted(design & POST_INTRODUCTION_FIELDS)
    if overlap:
        raise LeakageError("post-introduction field is a model feature: " + repr(overlap))
    # A sponsor absent from the member index cannot be any row's member, so it
    # is sound to skip it -- but never return early on that basis: an empty map
    # must still report the panel as fully checked, not as unchecked.
    sponsor_codes = {}
    for code, bill in enumerate(panel.bills):
        member_code = panel._member_index.get(bill.get("sponsor_bioguide"))
        if member_code is not None:
            sponsor_codes[code] = member_code
    violations = 0
    for index in range(len(panel)):
        bill_code = panel.bill_index[index]
        expected = sponsor_codes.get(bill_code)
        if expected is not None and panel.member_index[index] == expected:
            violations += 1
    if violations:
        raise LeakageError(
            f"bill sponsor entered its own candidate set on {violations} panel rows"
        )
    return len(panel)


def build_panel(
    congresses,
    universes,
    resolved_voteview,
    earliest_congress,
    billstatus,
    lobbied_by_congress,
    committee_maps,
    min_cosponsors=5,
    same_chamber_only=False,
    current_roster=None,
    live_congress=None,
):
    panel = ColumnarPanel()
    receipts = {}
    prior_receipts = {}
    roster_members = None if current_roster is None else frozenset(current_roster)
    if roster_members is not None:
        live_congress = max(congresses) if live_congress is None else int(live_congress)
        if live_congress not in set(congresses):
            raise ForecastError("live Congress for current-roster filtering is not requested")
    for congress in sorted(congresses):
        if congress - 1 not in billstatus:
            raise ForecastError(f"prior BILLSTATUS missing for Congress {congress}")
        prior = compute_prior_statistics(billstatus[congress - 1]["metadata"])
        prior_receipts[str(congress - 1)] = {
            "bill_types": sorted(PRIOR_BILL_TYPES),
            "bills": prior["bill_count"],
            "network_definition": prior["network_definition"],
        }
        selected, bill_receipt = select_finance_bills(
            billstatus[congress]["metadata"],
            lobbied_by_congress.get(congress, set()),
            min_cosponsors,
        )
        missing_sponsors = []
        unavailable = set()
        missing_congresses = {}
        if congress - 1 not in committee_maps:
            unavailable.add("prior_committee_coservice")
            missing_congresses["prior_committee_coservice"] = [congress - 1]
        if congress not in committee_maps:
            unavailable.add("member_on_referred_committee")
            missing_congresses["member_on_referred_committee"] = [congress]
        panel.set_availability(congress, unavailable, missing_congresses)
        rows_before = len(panel)
        positives = 0
        cross_chamber_rows_excluded = 0
        departed_member_rows_excluded = 0
        for bill in selected:
            metadata = bill["metadata"]
            sponsor = metadata["sponsor_bioguide"]
            sponsor_row = universes[congress].get(sponsor)
            if sponsor_row is None:
                missing_sponsors.append(_bill_label(bill["key"]))
                continue
            bill_chamber = str(metadata.get("origin_chamber") or "").strip().title()
            if bill_chamber not in {"House", "Senate"}:
                raise ForecastError(
                    f"invalid origin chamber for {_bill_label(bill['key'])}: "
                    f"{metadata.get('origin_chamber')!r}"
                )
            sponsor_d1, sponsor_d2, sponsor_missing = _ideology(
                resolved_voteview, sponsor, congress - 1
            )
            bill_code = panel.register_bill(
                {
                    "bill": _bill_label(bill["key"]),
                    "bill_number": bill["key"][1],
                    "sponsor_bioguide": sponsor,
                    "introduced_date": metadata["introduced_date"],
                }
            )
            for member in sorted(universes[congress]):
                if member == sponsor:
                    continue
                member_row = universes[congress][member]
                if same_chamber_only and member_row["chamber"] != bill_chamber:
                    cross_chamber_rows_excluded += 1
                    continue
                if (
                    roster_members is not None
                    and congress == live_congress
                    and member not in roster_members
                ):
                    departed_member_rows_excluded += 1
                    continue
                member_d1, member_d2, member_missing = _ideology(
                    resolved_voteview, member, congress - 1
                )
                affinity_count = prior["affinity"].get((member, sponsor), 0)
                affinity_total = prior["sponsor_totals"].get(sponsor, 0)
                values = {
                    "prior_sponsor_affinity_count": affinity_count,
                    # The raw rate remains here for the B2 benchmark. The model
                    # transform applies training-split empirical-Bayes shrinkage.
                    "prior_sponsor_affinity_rate": (
                        affinity_count / max(1, affinity_total)
                    ),
                    "prior_member_cosponsorships": prior[
                        "member_cosponsorships"
                    ].get(member, 0),
                    "prior_network_degree": prior["degree"].get(member, 0),
                    "prior_network_eigenvector": prior["eigenvector"].get(member, 0.0),
                    "member_nominate_dim1": member_d1,
                    "member_nominate_dim2": member_d2,
                    "sponsor_nominate_dim1": sponsor_d1,
                    "sponsor_nominate_dim2": sponsor_d2,
                    "nominate_dim1_abs_distance": (
                        0.0 if member_missing or sponsor_missing else abs(member_d1 - sponsor_d1)
                    ),
                    "nominate_dim2_abs_distance": (
                        0.0 if member_missing or sponsor_missing else abs(member_d2 - sponsor_d2)
                    ),
                    "member_ideology_missing": member_missing,
                    "sponsor_ideology_missing": sponsor_missing,
                    "ideology_distance_missing": int(member_missing or sponsor_missing),
                    "same_party": int(member_row["party_code"] == sponsor_row["party_code"]),
                    "same_state": int(member_row["state"] == sponsor_row["state"]),
                    "same_chamber": int(member_row["chamber"] == bill_chamber),
                    "member_seniority": congress - earliest_congress[member] + 1,
                    "bill_committee_referrals": len(bill["referral_codes"]),
                    "sponsor_majority_party": int(
                        sponsor_row["party_code"]
                        == _majority_party_code(congress, sponsor_row["chamber"])
                    ),
                    "bill_type": bill["key"][0],
                    "policy_area": metadata.get("policy_area") or "Unknown",
                }
                if "prior_committee_coservice" not in unavailable:
                    values["prior_committee_coservice"] = (
                        _members_shared_prior_committee(
                            member, sponsor, committee_maps[congress - 1]
                        )
                    )
                if "member_on_referred_committee" not in unavailable:
                    values["member_on_referred_committee"] = (
                        _member_on_referred_committee(
                            member, bill["referral_codes"], committee_maps[congress]
                        )
                    )
                actual = int(member in bill["cosponsors"])
                positives += actual
                panel.append(
                    congress=congress,
                    bill_code=bill_code,
                    bioguide=member,
                    display_name=member_row["member_name"] or member,
                    actual=actual,
                    values=values,
                    member_propensity_probability=(
                        values["prior_member_cosponsorships"]
                        / max(1, prior["bill_count"])
                    ),
                    sponsor_affinity_total=affinity_total,
                )
        bill_receipt.update(
            {
                "candidate_universe": len(universes[congress]),
                "selected_bills_with_sponsor_outside_universe": missing_sponsors,
                "modeled_bills": bill_receipt.get("selected", 0)
                - len(missing_sponsors),
                "panel_rows": len(panel) - rows_before,
                "positives": positives,
                "candidate_configuration": (
                    "same_chamber_only" if same_chamber_only else "unrestricted"
                ),
                "same_chamber_only": bool(same_chamber_only),
                "structurally_impossible_cross_chamber_rows_excluded": (
                    cross_chamber_rows_excluded
                ),
                "departed_member_rows_excluded": departed_member_rows_excluded,
            }
        )
        bill_receipt["base_rate"] = (
            None
            if bill_receipt["panel_rows"] == 0
            else bill_receipt["positives"] / bill_receipt["panel_rows"]
        )
        receipts[str(congress)] = bill_receipt
    if not panel:
        raise ForecastError("finance-relevant panel is empty")
    # Production-path guard.  build_panel is reached from main for every
    # configuration, so unlike the retired transform_rows lane this always runs.
    assert_panel_invariants(panel)
    raw_array_bytes = sum(
        len(column) * column.itemsize for column in panel.numeric.values()
    ) + sum(
        len(column) * column.itemsize for column in panel.categorical_codes.values()
    ) + sum(
        len(column) * column.itemsize
        for column in (
            panel.actual,
            panel.congress,
            panel.bill_index,
            panel.member_index,
            panel.member_propensity_probability,
            panel.sponsor_affinity_totals,
        )
    )
    return panel, {
        "per_congress": receipts,
        "prior_hr_s_basis": prior_receipts,
        "columnar_storage": {
            "rows": len(panel),
            "numeric_feature_type": "array.array('d') raw float64",
            "categorical_storage": "dictionary-coded array.array('I')",
            "raw_array_bytes": raw_array_bytes,
            "full_design_matrix_materialized": False,
        },
    }


def split_missing_feature_policy(rows, congresses=None):
    if isinstance(rows, ColumnarPanel):
        selected = sorted(int(value) for value in congresses)
        excluded = set()
        receipts = {}
        for feature, reason in OPTIONAL_FEATURE_REASONS.items():
            congress_counts = {}
            required = set()
            for congress in selected:
                if feature not in rows.unavailable_by_congress.get(congress, ()):
                    continue
                start, stop = rows.bounds(congress)
                congress_counts[congress] = stop - start
                required.update(
                    rows.missing_congresses_by_feature[congress].get(feature, ())
                )
            if not congress_counts:
                continue
            excluded.add(feature)
            receipts[feature] = {
                "reason": reason,
                "congresses": sorted(congress_counts),
                "required_snapshot_congresses": sorted(required),
                "row_counts_by_congress": {
                    str(congress): congress_counts[congress]
                    for congress in sorted(congress_counts)
                },
                "total_rows": sum(congress_counts.values()),
                "split_policy": (
                    "feature omitted from every training and test row in this split"
                ),
                "zero_imputed": False,
            }
        return excluded, receipts
    excluded = set()
    receipts = {}
    for feature, reason in OPTIONAL_FEATURE_REASONS.items():
        affected = [
            row
            for row in rows
            if feature in set(row.get("_features_unavailable", ()))
        ]
        if not affected:
            continue
        excluded.add(feature)
        congress_counts = collections.Counter(int(row["congress"]) for row in affected)
        required = sorted(
            {
                value
                for row in affected
                for value in row.get("_missing_feature_congresses", {}).get(feature, [])
            }
        )
        receipts[feature] = {
            "reason": reason,
            "congresses": sorted(congress_counts),
            "required_snapshot_congresses": required,
            "row_counts_by_congress": {
                str(congress): congress_counts[congress]
                for congress in sorted(congress_counts)
            },
            "total_rows": len(affected),
            "split_policy": "feature omitted from every training and test row in this split",
            "zero_imputed": False,
        }
    return excluded, receipts


def fit_preprocessor(rows, excluded_features=()):
    excluded = set(excluded_features)
    invalid = sorted(excluded - set(MODEL_FEATURES))
    if invalid:
        raise ForecastError("unknown feature exclusions " + repr(invalid))
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
        "active_continuous_features": [
            feature for feature in CONTINUOUS_FEATURES if feature not in excluded
        ],
        "active_categorical_features": [
            feature for feature in CATEGORICAL_FEATURES if feature not in excluded
        ],
        "excluded_features": sorted(excluded),
        "design_names": design_names,
        "standardization": "training-split mean and population SD for numeric features",
    }


def transform_rows(rows, preprocessor):
    matrix = []
    unseen = collections.Counter()
    dropped = set(preprocessor["dropped_zero_variance_features"])
    for row in rows:
        assert_no_leakage(row)
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


def _affinity_shrinkage_receipt(panel, start, stop):
    counts = panel.numeric["prior_sponsor_affinity_count"]
    aggregate_count = 0.0
    aggregate_exposure = 0.0
    for index in range(start, stop):
        aggregate_count += _finite(counts[index], "prior_sponsor_affinity_count")
        aggregate_exposure += _finite(
            panel.sponsor_affinity_totals[index], "prior sponsor affinity total"
        )
    mu = 0.0 if aggregate_exposure <= 0.0 else aggregate_count / aggregate_exposure
    return {
        "M": AFFINITY_SHRINK_M,
        "mu": mu,
        "mu_source": (
            "aggregate prior sponsor-affinity counts divided by aggregate sponsor-bill "
            "exposure across the training-split panel rows"
        ),
        "training_rows": stop - start,
        "aggregate_affinity_count": _rounded(aggregate_count),
        "aggregate_sponsor_bill_exposure": _rounded(aggregate_exposure),
        "baseline_uses_raw_rate": True,
    }


def _columnar_continuous_value(panel, feature, index, affinity_shrinkage):
    if feature != "prior_sponsor_affinity_rate":
        return _finite(panel.numeric[feature][index], feature)
    count = _finite(
        panel.numeric["prior_sponsor_affinity_count"][index],
        "prior_sponsor_affinity_count",
    )
    total = _finite(
        panel.sponsor_affinity_totals[index], "prior sponsor affinity total"
    )
    strength = affinity_shrinkage["M"]
    return (count + strength * affinity_shrinkage["mu"]) / (total + strength)


def fit_columnar_preprocessor(panel, congress, excluded_features=()):
    excluded = set(excluded_features)
    invalid = sorted(excluded - set(MODEL_FEATURES))
    if invalid:
        raise ForecastError("unknown feature exclusions " + repr(invalid))
    start, stop = panel.bounds(congress)
    count = stop - start
    means = {}
    scales = {}
    dropped = []
    design_names = ["intercept"]
    affinity_shrinkage = _affinity_shrinkage_receipt(panel, start, stop)
    for feature in CONTINUOUS_FEATURES:
        if feature in excluded:
            continue
        total = 0.0
        for index in range(start, stop):
            total += _columnar_continuous_value(
                panel, feature, index, affinity_shrinkage
            )
        mean = total / count
        squared = 0.0
        for index in range(start, stop):
            difference = (
                _columnar_continuous_value(
                    panel, feature, index, affinity_shrinkage
                )
                - mean
            )
            squared += difference * difference
        scale = math.sqrt(squared / count)
        means[feature] = mean
        scales[feature] = scale
        if scale <= bridge3.RANK_TOLERANCE:
            dropped.append(feature)
        else:
            design_names.append(feature)
    levels = {}
    references = {}
    level_codes = {}
    training_codes = {}
    pooled_level_codes = {}
    pooled_level_receipts = {}
    for feature in CATEGORICAL_FEATURES:
        if feature in excluded:
            continue
        counts = collections.Counter(
            int(panel.categorical_codes[feature][index])
            for index in range(start, stop)
        )
        if not counts:
            raise ForecastError(f"categorical feature {feature} has no levels")
        if CATEGORICAL_OTHER_LEVEL in {
            panel.categorical_levels[feature][code] for code in counts
        }:
            raise ForecastError(
                f"categorical feature {feature} uses reserved level {CATEGORICAL_OTHER_LEVEL}"
            )
        pooled_codes = {
            code for code, rows in counts.items() if rows < CATEGORICAL_POOL_MIN_ROWS
        }
        if len(pooled_codes) == len(counts):
            retained_reference = min(
                counts,
                key=lambda code: (
                    -counts[code], panel.categorical_levels[feature][code]
                ),
            )
            pooled_codes.remove(retained_reference)
        retained = sorted(
            panel.categorical_levels[feature][code]
            for code in counts
            if code not in pooled_codes
        )
        modeled_levels = retained + [CATEGORICAL_OTHER_LEVEL]
        levels[feature] = modeled_levels
        references[feature] = retained[0]
        level_codes[feature] = {
            level: panel._categorical_index[feature][level] for level in retained
        }
        training_codes[feature] = sorted(counts)
        pooled_level_codes[feature] = sorted(pooled_codes)
        pooled_level_receipts[feature] = [
            {
                "level": panel.categorical_levels[feature][code],
                "training_rows": counts[code],
            }
            for code in sorted(
                pooled_codes,
                key=lambda code: panel.categorical_levels[feature][code],
            )
        ]
        for level in modeled_levels[1:]:
            design_names.append(f"{feature}={level}")
    return {
        "continuous_means": means,
        "continuous_scales": scales,
        "dropped_zero_variance_features": dropped,
        "categorical_levels": levels,
        "categorical_references": references,
        "categorical_level_codes": level_codes,
        "categorical_training_codes": training_codes,
        "categorical_pooled_level_codes": pooled_level_codes,
        "categorical_pooled_levels": pooled_level_receipts,
        "categorical_pool_min_rows": CATEGORICAL_POOL_MIN_ROWS,
        "categorical_unseen_mapping": CATEGORICAL_OTHER_LEVEL,
        "affinity_shrinkage": affinity_shrinkage,
        "active_continuous_features": [
            feature for feature in CONTINUOUS_FEATURES if feature not in excluded
        ],
        "active_categorical_features": [
            feature for feature in CATEGORICAL_FEATURES if feature not in excluded
        ],
        "excluded_features": sorted(excluded),
        "design_names": design_names,
        "standardization": "training-split mean and population SD for numeric features",
    }


def transform_columnar_chunk(panel, start, stop, preprocessor):
    rows = stop - start
    matrix = np.empty((rows, len(preprocessor["design_names"])), dtype=float)
    matrix[:, 0] = 1.0
    column_index = 1
    dropped = set(preprocessor["dropped_zero_variance_features"])
    for feature in preprocessor["active_continuous_features"]:
        if feature in dropped:
            continue
        if feature == "prior_sponsor_affinity_rate":
            shrinkage = preprocessor["affinity_shrinkage"]
            counts = np.frombuffer(
                panel.numeric["prior_sponsor_affinity_count"], dtype=np.float64
            )[start:stop]
            totals = np.frombuffer(
                panel.sponsor_affinity_totals, dtype=np.float64
            )[start:stop]
            source = (
                counts + shrinkage["M"] * shrinkage["mu"]
            ) / (totals + shrinkage["M"])
        else:
            source = np.frombuffer(panel.numeric[feature], dtype=np.float64)[
                start:stop
            ]
        matrix[:, column_index] = (
            source - preprocessor["continuous_means"][feature]
        ) / preprocessor["continuous_scales"][feature]
        column_index += 1
    unseen = collections.Counter()
    for feature in preprocessor["active_categorical_features"]:
        codes = np.frombuffer(panel.categorical_codes[feature], dtype=np.uint32)[
            start:stop
        ]
        known_codes = set(preprocessor["categorical_training_codes"][feature])
        unseen_codes = set(int(value) for value in np.unique(codes)) - known_codes
        for code in unseen_codes:
            unseen[(feature, panel.categorical_levels[feature][code])] += int(
                np.sum(codes == code)
            )
        for level in preprocessor["categorical_levels"][feature][1:]:
            if level == CATEGORICAL_OTHER_LEVEL:
                other_codes = set(
                    preprocessor["categorical_pooled_level_codes"][feature]
                ) | unseen_codes
                matrix[:, column_index] = np.isin(codes, sorted(other_codes))
            else:
                code = preprocessor["categorical_level_codes"][feature][level]
                matrix[:, column_index] = codes == code
            column_index += 1
    if column_index != matrix.shape[1]:
        raise ForecastError("columnar transform produced the wrong design width")
    return matrix, unseen


def _columnar_outcomes(panel, start, stop):
    return np.frombuffer(panel.actual, dtype=np.int8)[start:stop].astype(float)


def _stream_probability(linear):
    return np.asarray(
        [prior_harness._sigmoid(float(value)) for value in linear], dtype=float
    )


def _gram_rank_receipt(gram):
    """Detect numerical rank on the unpenalized p-by-p Gram scale."""
    columns = int(gram.shape[0])
    basis = bridge3._pivoted_qr_basis(
        gram.tolist(), tolerance=bridge3.RANK_TOLERANCE
    )
    qr_rank = len(basis)
    del basis
    singular_values = np.linalg.svd(gram, compute_uv=False)
    largest = float(singular_values[0]) if len(singular_values) else 0.0
    svd_rank = int(
        np.sum(singular_values > bridge3.RANK_TOLERANCE * largest)
    ) if largest else 0
    rank = min(qr_rank, svd_rank)
    condition = float(np.linalg.cond(gram)) if columns else float("inf")
    return {
        "design_rank": rank,
        "design_columns": columns,
        "rank_deficient": rank < columns,
        "gram_pivoted_qr_rank": qr_rank,
        "gram_svd_rank": svd_rank,
        "unpenalized_gram_condition_number": _rounded(condition),
        "unpenalized_gram_condition_infinite": not math.isfinite(condition),
        "condition_number_scale": (
            "X'WX Gram scale; approximately the square of the weighted-X condition number"
        ),
    }


def irls_logistic_streaming(
    n_rows,
    feature_names,
    chunk_factory,
    ridge_lambda=RIDGE_LAMBDA,
):
    """Exact chunked IRLS from streamed X'WX and X'(y-p) sufficient statistics."""
    if ridge_lambda != RIDGE_LAMBDA:
        raise ForecastError("ridge lambda is fixed at 1.0 and may not be tuned")
    if n_rows <= 0 or not feature_names:
        raise ForecastError("streaming logistic design is empty")
    columns = len(feature_names)
    positives = 0.0
    observed = 0
    for matrix, outcomes in chunk_factory():
        if matrix.ndim != 2 or matrix.shape[1] != columns:
            raise ForecastError("streaming logistic design has inconsistent columns")
        if len(outcomes) != matrix.shape[0]:
            raise ForecastError("streaming logistic outcome length differs")
        if not np.all(np.isfinite(matrix)):
            raise ForecastError("streaming logistic design contains non-finite values")
        if np.any((outcomes != 0.0) & (outcomes != 1.0)):
            raise ForecastError("streaming logistic outcomes must be binary")
        positives += float(np.sum(outcomes))
        observed += len(outcomes)
    if observed != n_rows:
        raise ForecastError("streaming logistic row count changed between passes")
    if positives in {0.0, float(n_rows)}:
        raise ForecastError("logistic training outcome has one class")
    penalty = np.eye(columns, dtype=float)
    penalty[0, 0] = 0.0
    beta = np.zeros(columns, dtype=float)
    base_rate = min(1.0 - 1e-8, max(1e-8, positives / n_rows))
    beta[0] = math.log(base_rate / (1.0 - base_rate))
    converged = False
    iterations = 0
    for iteration in range(1, prior_harness.IRLS_MAX_ITERATIONS + 1):
        gram = np.zeros((columns, columns), dtype=float)
        score = np.zeros(columns, dtype=float)
        seen = 0
        for matrix, outcomes in chunk_factory():
            probability = _stream_probability(matrix @ beta)
            weights = np.maximum(probability * (1.0 - probability), 1e-12)
            gram += matrix.T @ (weights[:, None] * matrix)
            score += matrix.T @ (outcomes - probability)
            seen += len(outcomes)
        if seen != n_rows:
            raise ForecastError("streaming logistic row count changed between iterations")
        hessian = gram + ridge_lambda * penalty
        score -= ridge_lambda * (penalty @ beta)
        step = np.linalg.pinv(hessian, rcond=bridge3.RANK_TOLERANCE) @ score
        if not np.all(np.isfinite(step)):
            raise ForecastError("streaming IRLS produced a non-finite Newton step")
        beta += step
        iterations = iteration
        if float(np.max(np.abs(step))) <= prior_harness.IRLS_TOLERANCE:
            converged = True
            break
    if not converged:
        raise ForecastError("streaming IRLS did not converge within 100 iterations")
    final_gram = np.zeros((columns, columns), dtype=float)
    for matrix, _outcomes in chunk_factory():
        probability = _stream_probability(matrix @ beta)
        weights = np.maximum(probability * (1.0 - probability), 1e-12)
        final_gram += matrix.T @ (weights[:, None] * matrix)
    rank_receipt = _gram_rank_receipt(final_gram)
    hessian = final_gram + ridge_lambda * penalty
    covariance = np.linalg.pinv(hessian, rcond=bridge3.RANK_TOLERANCE)
    standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    coefficients = [
        {
            "feature": feature_names[index],
            "coefficient": _rounded(beta[index]),
            "standard_error": _rounded(standard_errors[index]),
            "se": _rounded(standard_errors[index]),
        }
        for index in range(columns)
    ]
    return {
        "beta": beta.tolist(),
        "coefficients": coefficients,
        "iterations": iterations,
        "converged": converged,
        "ridge_lambda": ridge_lambda,
        **rank_receipt,
        "rank_deficiency_handling": (
            "detected on the streamed unpenalized p-by-p X'WX Gram matrix with "
            "pivoted QR and SVD; fixed-ridge Hessian solved by SVD pseudo-inverse"
        ),
        "hessian_condition_number": _rounded(np.linalg.cond(hessian)),
        "normal_equations_streamed": True,
        "chunk_rows": STREAM_CHUNK_ROWS,
    }


def auc_score(actual, scores):
    return prior_harness.auc_score(actual, scores)


def average_precision(actual, scores):
    """Threshold-grouped AP, so tied baseline scores are order invariant."""
    pairs = sorted(
        ((float(score), int(value)) for value, score in zip(actual, scores)),
        reverse=True,
    )
    positives = sum(value for _score, value in pairs)
    if not positives:
        return None
    true_positives = 0
    seen = 0
    area = 0.0
    start = 0
    while start < len(pairs):
        stop = start + 1
        while stop < len(pairs) and pairs[stop][0] == pairs[start][0]:
            stop += 1
        group_positives = sum(value for _score, value in pairs[start:stop])
        true_positives += group_positives
        seen += stop - start
        area += (group_positives / positives) * (true_positives / seen)
        start = stop
    return area


def brier_score(actual, scores):
    return prior_harness.brier_score(actual, scores)


def precision_at_k(rows, score_field, cutoffs=(10, 25, 50)):
    """Bill-average precision with fractional expectation at a tied boundary."""
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(int(row["congress"]), row["bill"])].append(row)
    output = {}
    for cutoff in cutoffs:
        values = []
        for key in sorted(groups):
            ordered = sorted(
                groups[key],
                key=lambda row: -float(row[score_field]),
            )
            denominator = min(cutoff, len(ordered))
            if not denominator:
                continue
            expected_positives = 0.0
            remaining = denominator
            start = 0
            while remaining and start < len(ordered):
                stop = start + 1
                score = float(ordered[start][score_field])
                while (
                    stop < len(ordered)
                    and float(ordered[stop][score_field]) == score
                ):
                    stop += 1
                tied = ordered[start:stop]
                tied_positives = sum(int(row["actual"]) for row in tied)
                admitted = min(remaining, len(tied))
                expected_positives += admitted * tied_positives / len(tied)
                remaining -= admitted
                start = stop
            values.append(expected_positives / denominator)
        output[f"precision_at_{cutoff}"] = (
            None if not values else sum(values) / len(values)
        )
    return output


def _score_metrics(rows, score_field, brier_field=None):
    actual = [int(row["actual"]) for row in rows]
    scores = [float(row[score_field]) for row in rows]
    result = {
        "auc": _rounded(auc_score(actual, scores)),
        "average_precision": _rounded(average_precision(actual, scores)),
        "brier": (
            None
            if brier_field is None
            else _rounded(
                brier_score(actual, [float(row[brier_field]) for row in rows])
            )
        ),
    }
    result.update(
        {
            key: _rounded(value)
            for key, value in precision_at_k(rows, score_field).items()
        }
    )
    if brier_field is None:
        result["brier_unavailable_reason"] = (
            "no probabilistic score was supplied"
        )
    elif brier_field != score_field:
        result["brier_probability_field"] = brier_field
    return result


BASELINE_FIELDS = {
    "B1_member_propensity": "baseline_member_propensity",
    "B2_sponsor_affinity": "baseline_sponsor_affinity",
    "B3_party_match": "baseline_party_match",
}
BASELINE_BRIER_FIELDS = {
    "B1_member_propensity": "baseline_member_propensity_probability",
    "B2_sponsor_affinity": "baseline_sponsor_affinity_probability",
    "B3_party_match": "baseline_party_match",
}


class _ExternalScoreMetric:
    """Exact disk-backed AUC/AP with only one scoring chunk resident."""

    def __init__(self, work_dir, name):
        self.work_dir = work_dir
        self.name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        self.runs = []
        self.rows = 0
        self.positives = 0
        self.brier_error = 0.0

    def add_chunk(self, scores, actual, brier_probabilities):
        scores = np.asarray(scores, dtype=float)
        actual = np.asarray(actual, dtype=np.int8)
        probabilities = np.asarray(brier_probabilities, dtype=float)
        if len(scores) != len(actual) or len(scores) != len(probabilities):
            raise ForecastError("streamed metric chunk lengths differ")
        if not np.all(np.isfinite(scores)) or not np.all(np.isfinite(probabilities)):
            raise ForecastError("streamed metric received non-finite scores")
        if np.any((probabilities < 0.0) | (probabilities > 1.0)):
            raise ForecastError("Brier probability lies outside [0,1]")
        order = np.argsort(scores, kind="mergesort")
        path = os.path.join(
            self.work_dir, f"metric-{self.name}-{len(self.runs):06d}.bin"
        )
        payload = bytearray(len(scores) * _SCORE_RECORD.size)
        offset = 0
        for index in order:
            _SCORE_RECORD.pack_into(
                payload, offset, float(scores[index]), int(actual[index])
            )
            offset += _SCORE_RECORD.size
        try:
            with open(path, "wb") as handle:
                handle.write(payload)
        except OSError as exc:
            raise ForecastError(f"cannot write metric run {path}: {exc}") from exc
        self.runs.append(path)
        for value, probability in zip(actual, probabilities):
            self.brier_error += (float(probability) - int(value)) ** 2
        self.rows += len(actual)
        self.positives += int(np.sum(actual))

    def finalize(self):
        if not self.rows:
            raise ForecastError("streamed metric is empty")
        handles = []
        heap = []

        def push(run_index):
            data = handles[run_index].read(_SCORE_RECORD.size)
            if not data:
                return
            if len(data) != _SCORE_RECORD.size:
                raise ForecastError("truncated external metric run")
            score, actual = _SCORE_RECORD.unpack(data)
            heapq.heappush(heap, (score, run_index, actual))

        try:
            for path in self.runs:
                handles.append(open(path, "rb"))
            for run_index in range(len(handles)):
                push(run_index)
            negatives_below = 0
            positives_below = 0
            rows_below = 0
            auc_wins = 0.0
            average_precision_area = 0.0
            negatives = self.rows - self.positives
            while heap:
                score = heap[0][0]
                group_rows = 0
                group_positives = 0
                while heap and heap[0][0] == score:
                    _score, run_index, actual = heapq.heappop(heap)
                    group_rows += 1
                    group_positives += int(actual)
                    push(run_index)
                group_negatives = group_rows - group_positives
                auc_wins += group_positives * negatives_below
                auc_wins += 0.5 * group_positives * group_negatives
                if self.positives:
                    average_precision_area += (
                        group_positives
                        / self.positives
                        * (self.positives - positives_below)
                        / (self.rows - rows_below)
                    )
                negatives_below += group_negatives
                positives_below += group_positives
                rows_below += group_rows
        except OSError as exc:
            raise ForecastError(f"cannot merge external metric runs: {exc}") from exc
        finally:
            for handle in handles:
                handle.close()
            for path in self.runs:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
        auc = (
            None
            if not self.positives or not negatives
            else auc_wins / (self.positives * negatives)
        )
        return {
            "auc": _rounded(auc),
            "average_precision": _rounded(
                None if not self.positives else average_precision_area
            ),
            "brier": _rounded(self.brier_error / self.rows),
            "metric_aggregation": (
                "exact external merge of chunk-sorted IEEE-754 scores; no binning"
            ),
        }


class _BillPrecisionAccumulator:
    def __init__(self, metric_names, cutoffs=(10, 25, 50)):
        self.metric_names = tuple(metric_names)
        self.cutoffs = tuple(cutoffs)
        self.current_bill = None
        self.groups = None
        self.sums = {
            name: {cutoff: 0.0 for cutoff in self.cutoffs}
            for name in self.metric_names
        }
        self.bill_count = 0

    def add(self, bill_code, scores, actual):
        if self.current_bill != bill_code:
            self._finish_bill()
            self.current_bill = bill_code
            self.groups = {name: collections.Counter() for name in self.metric_names}
        for name in self.metric_names:
            self.groups[name][(float(scores[name]), int(actual))] += 1

    def _finish_bill(self):
        if self.current_bill is None:
            return
        self.bill_count += 1
        for name in self.metric_names:
            score_groups = collections.defaultdict(lambda: [0, 0])
            total = 0
            for (score, actual), count in self.groups[name].items():
                score_groups[score][0] += count
                score_groups[score][1] += count * actual
                total += count
            ordered = sorted(score_groups.items(), reverse=True)
            for cutoff in self.cutoffs:
                denominator = min(cutoff, total)
                remaining = denominator
                expected = 0.0
                for _score, (group_rows, group_positives) in ordered:
                    if not remaining:
                        break
                    admitted = min(remaining, group_rows)
                    expected += admitted * group_positives / group_rows
                    remaining -= admitted
                self.sums[name][cutoff] += expected / denominator

    def finalize(self):
        self._finish_bill()
        return {
            name: {
                f"precision_at_{cutoff}": _rounded(
                    self.sums[name][cutoff] / self.bill_count
                )
                for cutoff in self.cutoffs
            }
            for name in self.metric_names
        }


def streamed_auc_for_fixture(scores, actual, work_dir, chunk_rows=3):
    """Small public helper used to verify exact external AUC aggregation."""
    metric = _ExternalScoreMetric(work_dir, "fixture")
    for start in range(0, len(scores), chunk_rows):
        stop = min(len(scores), start + chunk_rows)
        chunk_scores = np.asarray(scores[start:stop], dtype=float)
        chunk_actual = np.asarray(actual[start:stop], dtype=np.int8)
        metric.add_chunk(chunk_scores, chunk_actual, chunk_scores)
    return metric.finalize()["auc"]


def split_verdict(metrics):
    """Compare model AP with the strongest baseline, never a selected weak one."""
    model_value = metrics["model"]["average_precision"]
    available = [
        (name, metrics["baselines"][name]["average_precision"])
        for name in BASELINE_FIELDS
        if metrics["baselines"][name]["average_precision"] is not None
    ]
    if model_value is None or not available:
        return {
            "outcome": "unavailable",
            "best_baseline": None,
            "best_baseline_average_precision": None,
            "model_average_precision": model_value,
            "headline": "VERDICT UNAVAILABLE: average precision could not be computed",
        }
    priority = {name: -index for index, name in enumerate(BASELINE_FIELDS)}
    best_name, best_value = max(
        available, key=lambda item: (item[1], priority[item[0]])
    )
    if model_value > best_value:
        outcome = "win"
        headline = (
            f"MODEL WINS: model AP {model_value:.4f} vs best baseline "
            f"{best_name} {best_value:.4f}"
        )
    elif model_value < best_value:
        outcome = "loss"
        headline = (
            f"MODEL LOSES: best baseline {best_name} AP {best_value:.4f} vs "
            f"model {model_value:.4f}"
        )
    else:
        outcome = "tie"
        headline = (
            f"MODEL TIES BEST BASELINE: {best_name} and model AP {model_value:.4f}"
        )
    return {
        "outcome": outcome,
        "comparison_metric": "average_precision",
        "best_baseline": best_name,
        "best_baseline_average_precision": best_value,
        "model_average_precision": model_value,
        "headline": headline,
    }


def evaluate_split(scored_rows):
    if not scored_rows:
        raise ForecastError("test split is empty")
    positives = sum(int(row["actual"]) for row in scored_rows)
    model = _score_metrics(
        scored_rows, "predicted_probability", brier_field="predicted_probability"
    )
    baselines = {
        name: _score_metrics(
            scored_rows,
            field,
            brier_field=BASELINE_BRIER_FIELDS[name],
        )
        for name, field in BASELINE_FIELDS.items()
    }
    b1_p25 = baselines["B1_member_propensity"]["precision_at_25"]
    model_p25 = model["precision_at_25"]
    metrics = {
        "n_test_rows": len(scored_rows),
        "n_test_bills": len({row["bill"] for row in scored_rows}),
        "positives": positives,
        "base_rate": _rounded(positives / len(scored_rows)),
        "model": model,
        "baselines": baselines,
        "lift_over_B1_at_precision_25": {
            "model_precision_at_25": model_p25,
            "B1_precision_at_25": b1_p25,
            "absolute_difference": (
                None if model_p25 is None or b1_p25 is None else _rounded(model_p25 - b1_p25)
            ),
            "ratio": (
                None
                if model_p25 is None or b1_p25 in {None, 0.0}
                else _rounded(model_p25 / b1_p25)
            ),
        },
    }
    metrics["verdict"] = split_verdict(metrics)
    metrics["headline"] = metrics["verdict"]["headline"]
    return metrics


def _split_specs(congresses, train_through):
    selected = set(congresses)
    specs = []
    if {117, 118} <= selected and train_through >= 117:
        specs.append(
            {
                "train_congress": 117,
                "test_congress": 118,
                "role": "secondary_feature_reduced",
            }
        )
    if {118, 119} <= selected and train_through >= 118:
        specs.append(
            {"train_congress": 118, "test_congress": 119, "role": "primary"}
        )
    if not specs:
        raise ForecastError("requested Congresses/train-through yield no specified split")
    # Execution order is chronological so a split can only consume an
    # already-written earlier split's out-of-sample score spool.
    return sorted(
        specs,
        key=lambda item: (item["test_congress"], item["train_congress"]),
    )


def policy_area_bill_counts(panel, congress):
    start, stop = panel.bounds(congress)
    bills_by_area = collections.defaultdict(set)
    for index in range(start, stop):
        area = panel.categorical_value("policy_area", index)
        bills_by_area[area].add(int(panel.bill_index[index]))
    return {
        area: len(bill_codes) for area, bill_codes in sorted(bills_by_area.items())
    }


def annotate_policy_area_coefficients(coefficient_table, bill_counts):
    for row in coefficient_table:
        feature = str(row.get("feature") or "")
        if not feature.startswith("policy_area="):
            continue
        area = feature.split("=", 1)[1]
        row["policy_area_training_bill_count"] = int(bill_counts.get(area, 0))
    return coefficient_table


def _columnar_chunk_factory(panel, congress, preprocessor, chunk_rows):
    start, stop = panel.bounds(congress)

    def factory():
        for chunk_start in range(start, stop, chunk_rows):
            chunk_stop = min(stop, chunk_start + chunk_rows)
            matrix, unseen = transform_columnar_chunk(
                panel, chunk_start, chunk_stop, preprocessor
            )
            if unseen:
                raise ForecastError(
                    "training transform unexpectedly found unseen categorical levels"
                )
            yield matrix, _columnar_outcomes(panel, chunk_start, chunk_stop)

    return factory


def fit_isotonic_calibrator(score_spool_path):
    pairs = []
    try:
        with open(score_spool_path, "r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, fieldnames=SCORE_FIELDS)
            for row_number, row in enumerate(reader, start=1):
                predicted = _finite(
                    row.get("predicted_probability"),
                    f"calibration pair {row_number} predicted_probability",
                )
                actual = _parse_int(
                    row.get("actual"), f"calibration pair {row_number} actual"
                )
                if not 0.0 <= predicted <= 1.0 or actual not in {0, 1}:
                    raise ForecastError(
                        f"calibration pair {row_number} is outside probability/actual bounds"
                    )
                pairs.append((predicted, actual))
    except OSError as exc:
        raise ForecastError(
            f"cannot read earlier-split calibration spool {score_spool_path}: {exc}"
        ) from exc
    if not pairs:
        raise ForecastError("earlier temporal split yielded no calibration pairs")
    pairs.sort(key=lambda item: item[0])
    blocks = []
    for predicted, actual in pairs:
        if blocks and predicted == blocks[-1][1]:
            blocks[-1][2] += actual
            blocks[-1][3] += 1
        else:
            blocks.append([predicted, predicted, float(actual), 1])
        while (
            len(blocks) >= 2
            and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]
        ):
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                [
                    left[0],
                    right[1],
                    left[2] + right[2],
                    left[3] + right[3],
                ]
            )
    return {
        "upper_bounds": [block[1] for block in blocks],
        "values": [block[2] / block[3] for block in blocks],
        "pair_count": len(pairs),
        "knot_count": len(blocks),
    }


def apply_isotonic_calibrator(calibrator, probability):
    value = _finite(probability, "raw predicted probability")
    upper_bounds = calibrator["upper_bounds"]
    low = 0
    high = len(upper_bounds)
    while low < high:
        middle = (low + high) // 2
        if value <= upper_bounds[middle]:
            high = middle
        else:
            low = middle + 1
    return calibrator["values"][min(low, len(upper_bounds) - 1)]


class _ReliabilityAccumulator:
    def __init__(self):
        self.bins = [
            {"rows": 0, "raw": 0.0, "calibrated": 0.0, "actual": 0.0}
            for _ in range(RELIABILITY_BIN_COUNT)
        ]

    def add_chunk(self, raw, calibrated, actual):
        for raw_value, calibrated_value, actual_value in zip(raw, calibrated, actual):
            bin_index = min(
                int(float(raw_value) * RELIABILITY_BIN_COUNT),
                RELIABILITY_BIN_COUNT - 1,
            )
            receipt = self.bins[bin_index]
            receipt["rows"] += 1
            receipt["raw"] += float(raw_value)
            receipt["calibrated"] += float(calibrated_value)
            receipt["actual"] += int(actual_value)

    def finalize(self):
        table = []
        for index, receipt in enumerate(self.bins):
            rows = receipt["rows"]
            if rows == 0:
                continue
            table.append(
                {
                    "raw_probability_bin": (
                        f"{index / RELIABILITY_BIN_COUNT:.2f}-"
                        f"{(index + 1) / RELIABILITY_BIN_COUNT:.2f}"
                    ),
                    "rows": rows,
                    "mean_predicted_raw": _rounded(receipt["raw"] / rows),
                    "mean_predicted_calibrated": _rounded(
                        receipt["calibrated"] / rows
                    ),
                    "observed_rate": _rounded(receipt["actual"] / rows),
                }
            )
        return table


def _score_columnar_split(
    panel,
    congress,
    configuration,
    preprocessor,
    beta,
    work_dir,
    chunk_rows,
    calibrator=None,
):
    start, stop = panel.bounds(congress)
    metric_names = ("model",) + tuple(BASELINE_FIELDS)
    metrics = {
        name: _ExternalScoreMetric(
            work_dir, f"{configuration}-{congress}-{name}"
        )
        for name in metric_names
    }
    bill_precision = _BillPrecisionAccumulator(metric_names)
    reliability = _ReliabilityAccumulator()
    unseen = collections.Counter()
    spool_path = os.path.join(
        work_dir, f"scores-{configuration}-{congress}.csvpart"
    )
    coefficients = np.asarray(beta, dtype=float)
    try:
        spool = open(spool_path, "w", encoding="utf-8", newline="")
    except OSError as exc:
        raise ForecastError(f"cannot open score spool {spool_path}: {exc}") from exc
    with spool:
        writer = csv.DictWriter(spool, fieldnames=SCORE_FIELDS, lineterminator="\n")
        for chunk_start in range(start, stop, chunk_rows):
            chunk_stop = min(stop, chunk_start + chunk_rows)
            matrix, chunk_unseen = transform_columnar_chunk(
                panel, chunk_start, chunk_stop, preprocessor
            )
            unseen.update(chunk_unseen)
            probability = _stream_probability(matrix @ coefficients)
            calibrated_probability = np.asarray(
                [
                    float(value)
                    if calibrator is None
                    else apply_isotonic_calibrator(calibrator, value)
                    for value in probability
                ],
                dtype=float,
            )
            actual = np.frombuffer(panel.actual, dtype=np.int8)[chunk_start:chunk_stop]
            reliability.add_chunk(probability, calibrated_probability, actual)
            b1 = np.frombuffer(
                panel.numeric["prior_member_cosponsorships"], dtype=np.float64
            )[chunk_start:chunk_stop]
            b1_probability = np.frombuffer(
                panel.member_propensity_probability, dtype=np.float64
            )[chunk_start:chunk_stop]
            b2 = np.frombuffer(
                panel.numeric["prior_sponsor_affinity_count"], dtype=np.float64
            )[chunk_start:chunk_stop]
            b2_probability = np.frombuffer(
                panel.numeric["prior_sponsor_affinity_rate"], dtype=np.float64
            )[chunk_start:chunk_stop]
            b3 = np.frombuffer(panel.numeric["same_party"], dtype=np.float64)[
                chunk_start:chunk_stop
            ]
            metrics["model"].add_chunk(probability, actual, probability)
            metrics["B1_member_propensity"].add_chunk(
                b1, actual, b1_probability
            )
            metrics["B2_sponsor_affinity"].add_chunk(
                b2, actual, b2_probability
            )
            metrics["B3_party_match"].add_chunk(b3, actual, b3)
            for offset in range(chunk_stop - chunk_start):
                index = chunk_start + offset
                bill_code = int(panel.bill_index[index])
                member_code = int(panel.member_index[index])
                score_values = {
                    "model": float(probability[offset]),
                    "B1_member_propensity": float(b1[offset]),
                    "B2_sponsor_affinity": float(b2[offset]),
                    "B3_party_match": float(b3[offset]),
                }
                bill_precision.add(bill_code, score_values, int(actual[offset]))
                bill = panel.bills[bill_code]
                member = panel.members[member_code]
                writer.writerow(
                    {
                        "candidate_configuration": configuration,
                        "congress": congress,
                        "bill": bill["bill"],
                        "member": member["bioguide_id"],
                        "predicted_probability": format(
                            float(probability[offset]), ".12g"
                        ),
                        "calibrated_probability": format(
                            float(calibrated_probability[offset]), ".12g"
                        ),
                        "actual": int(actual[offset]),
                        "baseline_member_propensity": format(float(b1[offset]), ".12g"),
                        "baseline_member_propensity_probability": format(
                            float(b1_probability[offset]), ".12g"
                        ),
                        "baseline_sponsor_affinity": format(float(b2[offset]), ".12g"),
                        "baseline_sponsor_affinity_probability": format(
                            float(b2_probability[offset]), ".12g"
                        ),
                        "baseline_party_match": format(float(b3[offset]), ".12g"),
                    }
                )
    precision = bill_precision.finalize()
    finalized = {name: metrics[name].finalize() for name in metric_names}
    for name in metric_names:
        finalized[name].update(precision[name])
    finalized["B1_member_propensity"]["brier_probability_field"] = (
        "baseline_member_propensity_probability"
    )
    finalized["B2_sponsor_affinity"]["brier_probability_field"] = (
        "baseline_sponsor_affinity_probability"
    )
    model = finalized.pop("model")
    baselines = {
        name: finalized[name] for name in BASELINE_FIELDS
    }
    b1_p25 = baselines["B1_member_propensity"]["precision_at_25"]
    model_p25 = model["precision_at_25"]
    positives = metrics["model"].positives
    result = {
        "n_test_rows": stop - start,
        "n_test_bills": bill_precision.bill_count,
        "positives": positives,
        "base_rate": _rounded(positives / (stop - start)),
        "model": model,
        "baselines": baselines,
        "lift_over_B1_at_precision_25": {
            "model_precision_at_25": model_p25,
            "B1_precision_at_25": b1_p25,
            "absolute_difference": (
                None
                if model_p25 is None or b1_p25 is None
                else _rounded(model_p25 - b1_p25)
            ),
            "ratio": (
                None
                if model_p25 is None or b1_p25 in {None, 0.0}
                else _rounded(model_p25 / b1_p25)
            ),
        },
    }
    result["verdict"] = split_verdict(result)
    result["headline"] = result["verdict"]["headline"]
    unseen_receipt = [
        {
            "feature": key[0],
            "level": key[1],
            "rows": unseen[key],
            "mapped_to": CATEGORICAL_OTHER_LEVEL,
        }
        for key in sorted(unseen)
    ]
    return result, unseen_receipt, spool_path, reliability.finalize()


def run_splits(
    rows,
    congresses,
    train_through,
    work_dir=None,
    chunk_rows=STREAM_CHUNK_ROWS,
    configuration="unrestricted",
):
    if not isinstance(rows, ColumnarPanel):
        raise ForecastError("run_splits requires the compact ColumnarPanel")
    if work_dir is None:
        raise ForecastError("run_splits requires a temporary work directory")
    if chunk_rows <= 0:
        raise ForecastError("chunk_rows must be positive")
    reports = []
    score_sources = []
    earlier_oos = None
    for spec in _split_specs(congresses, train_through):
        if spec["role"] == "primary" and earlier_oos is None:
            raise ForecastError(
                "primary 118->119 calibration requires earlier 117->118 OOS pairs"
            )
        train_start, train_stop = rows.bounds(spec["train_congress"])
        excluded, missing_receipt = split_missing_feature_policy(
            rows, (spec["train_congress"], spec["test_congress"])
        )
        preprocessor = fit_columnar_preprocessor(
            rows, spec["train_congress"], excluded_features=excluded
        )
        chunk_factory = _columnar_chunk_factory(
            rows, spec["train_congress"], preprocessor, chunk_rows
        )
        fit = irls_logistic_streaming(
            train_stop - train_start,
            preprocessor["design_names"],
            chunk_factory,
        )
        calibrator = (
            None
            if earlier_oos is None
            else fit_isotonic_calibrator(earlier_oos["path"])
        )
        metrics, unseen, spool_path, reliability_table = _score_columnar_split(
            rows,
            spec["test_congress"],
            configuration,
            preprocessor,
            fit["beta"],
            work_dir,
            chunk_rows,
            calibrator=calibrator,
        )
        coefficient_table = list(fit["coefficients"])
        for feature in preprocessor["dropped_zero_variance_features"]:
            coefficient_table.append(
                {
                    "feature": feature,
                    "coefficient": None,
                    "standard_error": None,
                    "se": None,
                    "included": False,
                    "reason": "zero variance in training split",
                }
            )
        for feature in sorted(excluded):
            coefficient_table.append(
                {
                    "feature": feature,
                    "coefficient": None,
                    "standard_error": None,
                    "se": None,
                    "included": False,
                    "reason": OPTIONAL_FEATURE_REASONS[feature],
                }
            )
        policy_counts = policy_area_bill_counts(rows, spec["train_congress"])
        coefficient_policy_counts = dict(policy_counts)
        coefficient_policy_counts[CATEGORICAL_OTHER_LEVEL] = sum(
            policy_counts.get(item["level"], 0)
            for item in preprocessor["categorical_pooled_levels"].get(
                "policy_area", ()
            )
        )
        annotate_policy_area_coefficients(
            coefficient_table, coefficient_policy_counts
        )
        reports.append(
            {
                **spec,
                "candidate_configuration": configuration,
                "feature_reduced": bool(excluded),
                "n_train_rows": train_stop - train_start,
                "n_train_bills": len(
                    set(rows.bill_index[train_start:train_stop])
                ),
                "features_excluded_for_missing_data": missing_receipt,
                "feature_preprocessing": preprocessor,
                "unseen_test_categorical_levels": unseen,
                "unseen_test_categorical_rows_mapped_to_other": sum(
                    item["rows"] for item in unseen
                ),
                "calibration": {
                    "method": (
                        CALIBRATION_METHOD
                        if calibrator is not None
                        else "identity_no_earlier_temporal_split"
                    ),
                    "fitted_on_split": (
                        None if earlier_oos is None else earlier_oos["split"]
                    ),
                    "pair_count": (
                        0 if calibrator is None else calibrator["pair_count"]
                    ),
                    "knot_count": (
                        0 if calibrator is None else calibrator["knot_count"]
                    ),
                    "raw_ranking_metrics_unchanged": True,
                    "pre_post_reliability": reliability_table,
                },
                "model_fit": {
                    key: value
                    for key, value in fit.items()
                    if key not in {"beta", "coefficients"}
                },
                "coefficient_table": coefficient_table,
                "policy_area_training_bill_counts": policy_counts,
                "coefficient_interpretation_note": (
                    "Policy-area levels below the receipted training-row threshold are "
                    "pooled into __OTHER__; coefficients remain descriptive, and the "
                    "attached training bill counts show category support."
                ),
                "metrics": metrics,
            }
        )
        score_sources.append(
            {
                "configuration": configuration,
                "congress": spec["test_congress"],
                "path": spool_path,
            }
        )
        earlier_oos = {
            "path": spool_path,
            "split": f"{spec['train_congress']}->{spec['test_congress']}",
        }
    # Preserve the established primary-first report/display order after the
    # leakage-safe chronological fitting pass is complete.
    reports.sort(key=lambda item: item["role"] != "primary")
    return reports, StreamedScores(score_sources)


def overall_verdict(split_reports):
    primary = next(
        (report for report in split_reports if report["role"] == "primary"),
        split_reports[0],
    )
    counts = collections.Counter(
        report["metrics"]["verdict"]["outcome"] for report in split_reports
    )
    primary_verdict = primary["metrics"]["verdict"]
    headline = (
        primary_verdict["headline"]
        + f" | temporal split receipts: wins={counts['win']}, "
        f"losses={counts['loss']}, ties={counts['tie']}"
    )
    return {
        "headline": headline,
        "comparison_metric": "average_precision",
        "comparison_rule": (
            "each split compares the model with the highest average precision "
            "among all three baselines; the primary split leads and splits are never averaged"
        ),
        "primary_split": f"{primary['train_congress']}->{primary['test_congress']}",
        "primary_outcome": primary_verdict["outcome"],
        "primary_best_baseline": primary_verdict["best_baseline"],
        "wins": counts["win"],
        "losses": counts["loss"],
        "ties": counts["tie"],
        "unavailable": counts["unavailable"],
        "per_split": [
            {
                "role": report["role"],
                "train_congress": report["train_congress"],
                "test_congress": report["test_congress"],
                **report["metrics"]["verdict"],
            }
            for report in split_reports
        ],
    }


def _atomic_text(path, writer):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".cosponsor-forecast-", dir=directory)
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
    report_path = os.path.join(out_dir, "cosponsor_forecast_report.json")
    score_path = os.path.join(out_dir, "cosponsor_scores.csv")

    def write_json(handle):
        json.dump(document, handle, sort_keys=True, indent=2, ensure_ascii=False)
        handle.write("\n")

    def write_csv(handle):
        writer = csv.DictWriter(handle, fieldnames=SCORE_FIELDS, lineterminator="\n")
        writer.writeheader()
        if isinstance(score_rows, StreamedScores):
            for source_receipt in score_rows.sources:
                source_path = source_receipt["path"]
                try:
                    with open(source_path, "r", encoding="utf-8", newline="") as source:
                        for line in source:
                            handle.write(line)
                except OSError as exc:
                    raise ForecastError(
                        f"cannot stream score spool {source_path}: {exc}"
                    ) from exc
            return
        for row in sorted(
            score_rows,
            key=lambda item: (
                int(item["congress"]),
                item["bill"],
                item["bioguide_id"],
            ),
        ):
            writer.writerow(
                {
                    "candidate_configuration": row.get(
                        "candidate_configuration", "unrestricted"
                    ),
                    "congress": row["congress"],
                    "bill": row["bill"],
                    "member": row["bioguide_id"],
                    "predicted_probability": format(
                        float(row["predicted_probability"]), ".12g"
                    ),
                    "calibrated_probability": format(
                        float(
                            row.get(
                                "calibrated_probability",
                                row["predicted_probability"],
                            )
                        ),
                        ".12g",
                    ),
                    "actual": row["actual"],
                    "baseline_member_propensity": row[
                        "baseline_member_propensity"
                    ],
                    "baseline_member_propensity_probability": row.get(
                        "baseline_member_propensity_probability", ""
                    ),
                    "baseline_sponsor_affinity": row[
                        "baseline_sponsor_affinity"
                    ],
                    "baseline_sponsor_affinity_probability": row.get(
                        "baseline_sponsor_affinity_probability", ""
                    ),
                    "baseline_party_match": row["baseline_party_match"],
                }
            )

    _atomic_text(score_path, write_csv)
    _atomic_text(report_path, write_json)
    return report_path, score_path


def print_summary(document):
    # The verdict deliberately precedes even the program title.
    print(document["verdict"]["headline"])
    print("Cosponsor forecast - descriptive substrate of lobbying attention")
    configurations = document.get("candidate_configurations") or {
        "unrestricted": {"per_split": document["per_split"], "role": "context"}
    }
    headline = document.get("headline_candidate_configuration", "unrestricted")
    order = [headline] + sorted(name for name in configurations if name != headline)
    for configuration in order:
        result = configurations[configuration]
        print(f"  CONFIGURATION: {configuration} ({result['role']})")
        for split in result["per_split"]:
            metrics = split["metrics"]
            model = metrics["model"]
            best = metrics["verdict"]["best_baseline"]
            print(
                f"    {split['role']}: Congress {split['train_congress']} -> "
                f"{split['test_congress']} | base rate={metrics['base_rate']:.3%} | "
                f"rows={metrics['n_test_rows']} bills={metrics['n_test_bills']}"
            )
            print(
                f"      HEADLINE: {metrics['headline']} | model AUC={model['auc']} "
                f"AP={model['average_precision']} Brier={model['brier']}"
            )
            print(
                f"      best baseline={best}; lift over B1 at P@25="
                f"{metrics['lift_over_B1_at_precision_25']['ratio']}x"
            )
            for feature, receipt in sorted(
                split["features_excluded_for_missing_data"].items()
            ):
                print(
                    f"      FEATURE OMITTED SPLIT-WIDE: {feature}: "
                    f"{receipt['reason']}"
                )
            if split["model_fit"]["rank_deficient"]:
                print(
                    "      DESIGN WARNING: rank deficiency detected "
                    f"({split['model_fit']['design_rank']}/"
                    f"{split['model_fit']['design_columns']}); see JSON receipt"
                )
    print(
        "  DESCRIPTIVE, not causal; fixed-penalty coefficients are associations. "
        "Cosponsorship is the substrate of lobbying attention, not lobbying itself."
    )


def _parser():
    parser = argparse.ArgumentParser(
        description="Temporally honest descriptive cosponsorship forecast."
    )
    parser.add_argument(
        "--congresses",
        nargs="*",
        default=None,
        metavar="CONGRESS",
        help="Congress numbers separated by spaces or commas (default 117,118,119)",
    )
    parser.add_argument(
        "--train-through", type=int, default=DEFAULT_TRAIN_THROUGH, metavar="CONGRESS"
    )
    parser.add_argument("--out-dir", default=".", metavar="DIR")
    parser.add_argument(
        "--legislators",
        default=None,
        metavar="CSV",
        help=(
            "current serving roster used only to exclude departed candidates from "
            "the latest requested Congress"
        ),
    )
    parser.add_argument("--min-cosponsors", type=int, default=5, metavar="N")
    parser.add_argument(
        "--same-chamber-only",
        action="store_true",
        help=(
            "also run the defensible same-chamber candidate universe, retain the "
            "unrestricted run as context, and lead the report with the restricted result"
        ),
    )
    return parser


def main(argv=None, environ=None):
    args = _parser().parse_args(argv)
    if args.min_cosponsors < 0:
        print("ERROR: --min-cosponsors must be nonnegative", file=sys.stderr)
        return 2
    env = os.environ if environ is None else environ
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        print("ERROR: LOCALAPPDATA is unset", file=sys.stderr)
        return 2
    inputs = {}
    try:
        congresses = parse_congresses(args.congresses)
        unsupported = sorted(set(congresses) - set(DEFAULT_CONGRESSES))
        if unsupported:
            raise ForecastError(
                "this pinned evaluation supports Congresses 117-119 only: "
                + repr(unsupported)
            )
        systems_root = env.get("MELANGE_SYSTEMS_ROOT", DEFAULT_SYSTEMS_ROOT)
        voteview_path = os.path.join(systems_root, "votes", "HSall_members.csv")
        legislators_path = os.path.abspath(
            args.legislators
            or os.path.join(systems_root, "fec", "legislators.csv")
        )
        paths = builder.default_paths(local_app_data)
        raw_dir = paths["raw_dir"]
        needed_billstatus = sorted(set(congresses) | {min(congresses) - 1})
        universes, resolved, earliest, roster_receipt = load_voteview(
            voteview_path, congresses, inputs, enforce_expected=True
        )
        current_roster = None
        if LIVE_CONGRESS in congresses:
            current_roster, current_roster_receipt = champions.load_legislators(
                legislators_path
            )
            _record_input(inputs, legislators_path, "latest_congress_serving_roster")
            current_roster_receipt.update(
                {
                    "applied": True,
                    "semantic": "presence means currently serving",
                    "applied_to_congress": LIVE_CONGRESS,
                    "historical_congresses_filtered": False,
                }
            )
        else:
            current_roster_receipt = {
                "applied": False,
                "reason": "live Congress 119 was not requested",
                "historical_congresses_filtered": False,
            }
        billstatus, billstatus_provenance = load_billstatus(
            raw_dir, needed_billstatus, inputs
        )
        committee_maps, committee_provenance = load_committee_maps(
            os.path.join(raw_dir, "committee_snapshots"), congresses, inputs
        )
        lobbied, lobbying_receipts = load_lobbied_bill_keys(
            paths, billstatus, congresses, inputs
        )
        # Title indexes and duplicate bill-person sets are needed only for the
        # strict lobbying-title resolution above.  Keep the compact forecast
        # metadata and release those large intermediate structures before the
        # panel arrays are allocated.
        for congress_payload in billstatus.values():
            congress_payload.pop("people", None)
            congress_payload.pop("keys", None)
            congress_payload.pop("title_index", None)
        out_dir = os.path.abspath(args.out_dir)
        os.makedirs(out_dir, exist_ok=True)
        score_work = tempfile.TemporaryDirectory(
            prefix=".cosponsor-score-work-", dir=out_dir
        )
        configuration_specs = [("unrestricted", False)]
        if args.same_chamber_only:
            configuration_specs.append(("same_chamber_only", True))
        configuration_results = {}
        all_score_sources = []
        for configuration, restricted in configuration_specs:
            rows, configuration_panel_receipt = build_panel(
                congresses,
                universes,
                resolved,
                earliest,
                billstatus,
                lobbied,
                committee_maps,
                min_cosponsors=args.min_cosponsors,
                same_chamber_only=restricted,
                current_roster=current_roster,
                live_congress=LIVE_CONGRESS,
            )
            configuration_splits, configuration_scores = run_splits(
                rows,
                congresses,
                args.train_through,
                work_dir=score_work.name,
                chunk_rows=STREAM_CHUNK_ROWS,
                configuration=configuration,
            )
            configuration_results[configuration] = {
                "same_chamber_only": restricted,
                "reader_should_quote": restricted,
                "role": "defensible_headline" if restricted else "context",
                "verdict": overall_verdict(configuration_splits),
                "panel_receipt": configuration_panel_receipt,
                "per_split": configuration_splits,
            }
            all_score_sources.extend(configuration_scores.sources)
        headline_configuration = (
            "same_chamber_only" if args.same_chamber_only else "unrestricted"
        )
        headline_result = configuration_results[headline_configuration]
        panel_receipt = headline_result["panel_receipt"]
        per_split = headline_result["per_split"]
        primary_split = next(
            (split for split in per_split if split["role"] == "primary"),
            per_split[-1],
        )
        verdict = headline_result["verdict"]
        score_rows = StreamedScores(all_score_sources)
        document = {
            "method_note": METHOD_NOTE,
            "model_vintage": "v2-surgical",
            "model_vintage_receipt": {
                "affinity_shrink_M": AFFINITY_SHRINK_M,
                "affinity_mu": primary_split["feature_preprocessing"][
                    "affinity_shrinkage"
                ]["mu"],
                "affinity_mu_source": primary_split["feature_preprocessing"][
                    "affinity_shrinkage"
                ]["mu_source"],
                "calibration_method": primary_split["calibration"]["method"],
                "calibration_fitted_on_split": primary_split["calibration"][
                    "fitted_on_split"
                ],
                "categorical_pool_min_rows": CATEGORICAL_POOL_MIN_ROWS,
            },
            "descriptive_not_causal": True,
            "cosponsorship_is_lobbying_substrate_not_lobbying": True,
            "headline_candidate_configuration": headline_configuration,
            "candidate_configurations": configuration_results,
            "configuration_quote_guidance": (
                "Quote same_chamber_only as the defensible headline because cross-chamber "
                "cosponsorship is structurally impossible; unrestricted is context only."
                if args.same_chamber_only
                else "Only unrestricted was requested; rerun with --same-chamber-only for "
                "the defensible headline and side-by-side comparison."
            ),
            "finance_bill_definition": {
                "rule": (
                    "union of (a) bills named by any organization in the builder's "
                    "pinned 22-org finance roster on the strict lobbying channel and "
                    "(b) bills with an introduction-date BILLSTATUS referral to "
                    "HSBA, SSBK, HSAG, or SSAF"
                ),
                "lobbying_roster": "builder.BRIDGE3_LOB_IDS (22 pinned organizations)",
                "lobbying_channel": "strict",
                "committee_codes": sorted(FINANCE_REFERRAL_CODES),
                "post_introduction_referrals_used": False,
            },
            "target": {
                "unit": "bill-member",
                "positive": "member appears in the bill's final BILLSTATUS cosponsor list",
                "zero_fill_unrestricted": (
                    "every other Voteview member serving in that Congress"
                ),
                "zero_fill_same_chamber_only": (
                    "every other Voteview member serving in the bill's origin chamber"
                ),
                "latest_congress_departed_member_policy": (
                    "exclude Voteview candidates absent from the current legislators.csv "
                    "serving roster only for the latest requested Congress"
                ),
                "historical_congresses_keep_contemporaneous_panels": True,
                "sponsor_excluded": True,
                "min_cosponsors": args.min_cosponsors,
                "minimum_is_selection_not_feature": True,
                "missing_sponsor_structure_policy": (
                    "a bill is excluded and receipted when its sponsor has no row in "
                    "the shipped target-Congress Voteview vintage; sponsor features "
                    "are never fabricated"
                ),
            },
            "prior_history_basis": {
                "bill_types": sorted(PRIOR_BILL_TYPES),
                "applied_uniformly": True,
                "reason": (
                    "Congress 116 has HR/S archives only; applying HR/S to every prior "
                    "Congress prevents richer later archives from creating split-specific signal"
                ),
            },
            "committee_timing": {
                "member_on_referred_committee": (
                    "same-Congress assignments are ordinarily made at the start of the "
                    "Congress and known at introduction"
                ),
                "qualification": (
                    "snapshots are point-in-time captures, not dated rosters, and can "
                    "reflect mid-Congress committee changes"
                ),
                "prior_committee_coservice": {
                    "included": False,
                    "reason": PRIOR_COSERVICE_REASON,
                    "same_period_proxy_used": False,
                },
            },
            "features": list(MODEL_FEATURES),
            "feature_definitions": {
                "sponsor_affinity": (
                    "member's HR/S cosponsorship count on this sponsor's bills in the "
                    "prior Congress; the model rate is empirical-Bayes shrunk with "
                    "training-split mu and M=10, while B2 retains the raw count/total rate"
                ),
                "member_propensity": "member's total HR/S cosponsorships in the prior Congress",
                "network": (
                    "prior-Congress HR/S undirected weighted sponsor-cosponsor network; "
                    "unique-neighbor degree and shifted-power-iteration eigenvector centrality"
                ),
                "ideology": (
                    "prior-Congress Voteview dimensions for member and sponsor plus absolute "
                    "distances; unavailable freshmen are neutral-zero encoded with explicit "
                    "missing indicators"
                ),
                "structure": (
                    "same party/state/chamber, Voteview earliest-Congress seniority, and "
                    "point-in-time target-Congress membership on an introduction-date "
                    "referred committee"
                ),
                "bill": (
                    "introduction-date referral count, bill type, sponsor majority-party "
                    "status, and top-level BILLSTATUS policy area"
                ),
            },
            "leakage_guard": {
                "prior_features": "source Congress must equal target Congress minus one",
                "bill_features": "introduction-fixed fields and referrals dated no later than introduction",
                "excluded_fields": sorted(POST_INTRODUCTION_FIELDS),
                "final_cosponsor_count_in_design": False,
                "actions_or_passage_in_design": False,
            },
            "model": {
                "family": "ridge logistic regression by IRLS",
                "ridge_lambda": RIDGE_LAMBDA,
                "lambda_tuned": False,
                "resampled": False,
                "class_reweighted": False,
                "continuous_standardization": "training-split mean and population SD",
                "calibration": (
                    "deterministic isotonic pool-adjacent-violators fitted only on the "
                    "immediately earlier temporal split's out-of-sample pairs"
                ),
                "categorical_unseen_handling": (
                    "training levels below the receipted row threshold and all unseen "
                    "test/live levels map to an explicit __OTHER__ coefficient"
                ),
                "rank_detection_and_solver": (
                    "pivoted-QR and SVD detection on streamed unpenalized X'WX; "
                    "fixed-ridge p-by-p Hessian solved by SVD pseudo-inverse"
                ),
                "normal_equations": "streamed in 25,000-row chunks",
                "full_design_matrix_materialized": False,
                "panel_storage": "array.array raw-double columns plus dictionary-coded strings",
                "standard_errors": (
                    "sqrt diagonal of the inverse penalized observed information matrix; "
                    "reported as both se and standard_error"
                ),
                "standard_error_scale": (
                    "binomial-logit dispersion is fixed at 1; no residual-variance "
                    "multiplier is applied"
                ),
                "standard_error_interpretation": (
                    "conditional on the fixed lambda=1.0 penalty and descriptive only, "
                    "not inferential uncertainty for causal effects"
                ),
            },
            "coefficient_honesty_note": (
                "Policy-area levels below the receipted training-row threshold are pooled "
                "into __OTHER__ on this finance-selected panel. Coefficients are not "
                "substantive findings; each carries its training bill count so category "
                "support remains visible."
            ),
            "baseline_definitions": {
                "B1_member_propensity": {
                    "ranking_score": "prior-Congress HR/S cosponsorship count",
                    "brier_probability": (
                        "that count divided by all HR/S bills in the prior Congress"
                    ),
                },
                "B2_sponsor_affinity": {
                    "ranking_score": "prior-Congress HR/S count with this sponsor",
                    "brier_probability": (
                        "that count divided by the sponsor's prior-Congress HR/S bills"
                    ),
                },
                "B3_party_match": {
                    "ranking_and_brier_score": (
                        "1 iff member and sponsor have the same Voteview party_code"
                    )
                },
            },
            "evaluation": {
                "primary": "train Congress 118, test Congress 119",
                "secondary": "train Congress 117, test Congress 118; feature-reduced",
                "splits_pooled_or_averaged": False,
                "headline_metric": "average_precision",
                "headline_baseline_rule": "best of all three baselines within each split",
                "precision_at_k_ties": (
                    "fractional expected positives when a score tie crosses the cutoff"
                ),
                "auc_average_precision_aggregation": (
                    "exact disk-backed external merge of chunk-sorted scores; no binning"
                ),
                "ranking_metrics_probability_field": "predicted_probability (raw sigmoid)",
                "scores_csv_buffered_in_memory": False,
                "candidate_configuration_comparison": (
                    "with --same-chamber-only, restricted and unrestricted configurations "
                    "are reported separately and never averaged"
                ),
            },
            "memory_engineering": {
                "panel": "raw array.array columns; no persistent per-row dictionaries",
                "irls_chunk_rows": STREAM_CHUNK_ROWS,
                "normal_equation_peak_order": "O(chunk_rows * p + p^2)",
                "rank_matrix": "unpenalized p-by-p X'WX Gram matrix",
                "metric_scores_retained_in_memory": False,
                "metric_sort": "exact chunk runs merged from temporary disk files",
                "score_csv": "streamed; never accumulated as a Python row list",
            },
            "requested_congresses": congresses,
            "train_through": args.train_through,
            "verdict": verdict,
            "panel_receipt": panel_receipt,
            "features_excluded_for_missing_data": {
                f"{split['train_congress']}->{split['test_congress']}": split[
                    "features_excluded_for_missing_data"
                ]
                for split in per_split
            },
            "provenance": {
                "every_input_sha256": [
                    {
                        **inputs[path],
                        "roles": sorted(inputs[path]["roles"]),
                    }
                    for path in sorted(inputs)
                ],
                "voteview_candidate_universe": roster_receipt,
                "latest_congress_serving_roster": current_roster_receipt,
                "billstatus": billstatus_provenance,
                "committee_snapshots": committee_provenance,
                "lobbying_bill_selection": lobbying_receipts,
            },
            "per_split": per_split,
        }
        write_outputs(out_dir, document, score_rows)
        score_work.cleanup()
        print_summary(document)
    except (
        ForecastError,
        builder.IngestError,
        champions.InstrumentError,
        bridge3.IngestError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
