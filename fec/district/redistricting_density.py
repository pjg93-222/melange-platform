# -*- coding: utf-8 -*-
"""Build symmetric old/new county-weight branch density and member deltas.

Both boundary vintages use one fixed raw branch universe and one identical
block-count county allocation method.  This module never downloads, geocodes,
extracts a BEF archive, or writes inside the repository data tree.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import unicodedata
import zipfile
from collections.abc import Mapping

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

from paths import ROOT as MB_ROOT


SCHEMA = "cd116-density-v1"
SYMMETRIC_METHOD_NOTE = (
    "old/new shares are computed by one identical county-weight method; "
    "deltas are internally consistent; levels are NOT comparable to "
    "district_financial.json's exact-geocoded shares."
)
BEF_HEADER = ("BLOCKID", "CD116")
CD119_HEADER = ("block_geoid", "cd")
BEF_NATIONAL_MEMBER = "National_CD116.txt"
BEF_OVERRIDE_MEMBERS = {
    "08": "08_CO_CD116.txt",
    "27": "27_MN_CD116.txt",
    "42": "42_PA_CD116.txt",
}
NCUA_BRANCH_MEMBER = "Credit Union Branch Information.txt"
NCUA_FOICU_MEMBER = "FOICU.txt"
NCUA_BRANCH_FIELDS = frozenset({
    "CU_NUMBER",
    "MainOffice",
    "PhysicalAddressStateCode",
    "PhysicalAddressCountyName2",
})
NCUA_FOICU_FIELDS = frozenset({
    "CU_NUMBER", "STATE_CODE", "COUNTY_CODE"
})
MEMBER_FIELDS = (
    "bioguide",
    "state",
    "seat_117",
    "seat_119",
    "seat_changed",
    "cu_share_old",
    "cu_share_new",
    "delta",
)
CU_DROP_ESCALATION_THRESHOLD = 0.10
UNASSIGNED_BLOCK_BUCKET = "__UNASSIGNED__"
CHUNK_SIZE = 1024 * 1024

FIPS_TO_USPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "60": "AS", "66": "GU", "69": "MP", "72": "PR",
    "78": "VI",
}
VOTING_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})
USPS_TO_FIPS = {
    state: fips for fips, state in FIPS_TO_USPS.items()
}


class RedistrictingDensityError(RuntimeError):
    """An input contract failed; do not publish partial outputs."""


def _utc_iso(timestamp=None):
    if timestamp is None:
        value = datetime.datetime.now(datetime.timezone.utc)
    else:
        value = datetime.datetime.fromtimestamp(
            timestamp, datetime.timezone.utc
        )
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise RedistrictingDensityError(
            f"failed to hash input {path}: {exc}"
        ) from exc
    return digest.hexdigest()


def _require_file(path, label):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise RedistrictingDensityError(
            f"{label} not found (network use is forbidden): {path}"
        )
    return path


def _read_json(path, label):
    _require_file(path, label)
    try:
        with open(path, "r", encoding="utf-8-sig") as source:
            value = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RedistrictingDensityError(f"{label} unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise RedistrictingDensityError(f"{label} root must be an object")
    return value


def _validate_recorded_sha(path, receipt, label):
    expected = str((receipt or {}).get("sha256") or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise RedistrictingDensityError(
            f"{label} receipt lacks a valid sha256"
        )
    observed = _sha256(path)
    if observed != expected:
        raise RedistrictingDensityError(
            f"{label} sha256 mismatch: expected {expected}, observed {observed}"
        )
    return observed


def _input_receipt(path, **extra):
    value = {
        "file": os.path.abspath(path),
        "sha256": _sha256(path),
        "retrieved": _utc_iso(os.path.getmtime(path)),
    }
    value.update(extra)
    return value


def _block_and_district(row, label):
    if isinstance(row, Mapping):
        block = str(
            row.get("BLOCKID") or row.get("block_geoid") or ""
        ).strip()
        district = str(
            row.get("CD116") or row.get("cd") or row.get("district") or ""
        ).strip()
    else:
        try:
            block, district = row
        except (TypeError, ValueError) as exc:
            raise RedistrictingDensityError(
                f"{label} must contain (block, district) pairs"
            ) from exc
        block = str(block or "").strip()
        district = str(district or "").strip()
    if len(block) != 15 or not block.isdigit():
        raise RedistrictingDensityError(
            f"{label} has invalid 15-digit block GEOID {block!r}"
        )
    if len(district) != 2 or not (
        district.isdigit() or district == "ZZ"
    ):
        raise RedistrictingDensityError(
            f"{label} has invalid two-character district {district!r}"
        )
    if block[:2] not in FIPS_TO_USPS:
        raise RedistrictingDensityError(
            f"{label} has unmapped state FIPS {block[:2]!r}"
        )
    return block, district


def _load_ct_tract_recode(path):
    """Read the CT 2020-to-2022 tract bridge (same file fetch_hmda_district uses)."""
    path = _require_file(path, "CT 2020-to-2022 tract bridge")
    recode = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = ("tract_fips_2020", "tract_fips_2022")
            if not set(fields) <= set(reader.fieldnames or ()):
                raise RedistrictingDensityError(
                    f"CT bridge {path} lacks columns {list(fields)!r}; "
                    f"found {reader.fieldnames!r}"
                )
            for row in reader:
                old = (row.get(fields[0]) or "").strip()
                new = (row.get(fields[1]) or "").strip()
                for tract in (old, new):
                    if (
                        len(tract) != 11
                        or not tract.isdigit()
                        or not tract.startswith("09")
                    ):
                        raise RedistrictingDensityError(
                            f"CT bridge row {reader.line_num} has invalid "
                            f"CT tract {tract!r}"
                        )
                if recode.setdefault(old, new) != new:
                    raise RedistrictingDensityError(
                        f"CT bridge maps 2020 tract {old} to multiple "
                        "2022 tracts"
                    )
    except RedistrictingDensityError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RedistrictingDensityError(
            f"CT bridge unreadable: {exc}"
        ) from exc
    if not recode:
        raise RedistrictingDensityError("CT bridge has no rows")
    return recode


def _county_of_block(block, ct_recode):
    # FDIC/ACS code CT by 2022 planning region (09110..09190), not the 2020
    # legacy counties (09001..09015), so CT blocks must be recoded first.
    if block[:2] != "09":
        return block[:5]
    if ct_recode is None:
        raise RedistrictingDensityError(
            "CT block seen but no CT 2020-to-2022 tract bridge was supplied"
        )
    tract = ct_recode.get(block[:11])
    if tract is None:
        raise RedistrictingDensityError(
            f"CT tract {block[:11]} is absent from the 2020-to-2022 bridge"
        )
    return tract[:5]


def _add_block(counts, block, district, ct_recode=None):
    state = FIPS_TO_USPS[block[:2]]
    if state not in VOTING_STATES:
        return False
    bucket = (
        UNASSIGNED_BLOCK_BUCKET
        if district in {"98", "ZZ"}
        else f"{state}-{district}"
    )
    counts[_county_of_block(block, ct_recode)][bucket] += 1
    return True


def _finalize_county_counts(counts):
    weights = {}
    districts = set()
    for county in sorted(counts):
        all_counts = counts[county]
        denominator = sum(all_counts.values())
        if denominator <= 0:
            raise RedistrictingDensityError(
                f"county {county} has no block rows"
            )
        district_counts = {
            district: count
            for district, count in all_counts.items()
            if district != UNASSIGNED_BLOCK_BUCKET
        }
        county_weights = tuple(
            (district, count / denominator)
            for district, count in sorted(district_counts.items())
        )
        if sum(weight for _district, weight in county_weights) > 1 + 1e-12:
            raise RedistrictingDensityError(
                f"county weights for {county} sum above one"
            )
        weights[county] = county_weights
        districts.update(district_counts)
    return weights, districts


def _weight_receipt(counts, weights, districts, rows_read, effective_rows):
    return {
        "block_rows_read": rows_read,
        "effective_voting_state_block_rows": effective_rows,
        "unassigned_block_rows": sum(
            value.get(UNASSIGNED_BLOCK_BUCKET, 0)
            for value in counts.values()
        ),
        "counties": len(weights),
        "split_county_count": sum(
            len(value) > 1 for value in weights.values()
        ),
        "district_count": len(districts),
        "weight_method": (
            "block-count approximation: blocks-in-district / "
            "all blocks-in-county; BEF contains no population weights"
        ),
    }


def county_weights_from_blocks(rows, ct_recode=None):
    """Aggregate synthetic/effective block rows into block-count weights."""
    counts = collections.defaultdict(collections.Counter)
    rows_read = 0
    effective_rows = 0
    for row_number, row in enumerate(rows, start=1):
        block, district = _block_and_district(
            row, f"block row {row_number}"
        )
        rows_read += 1
        effective_rows += int(
            _add_block(counts, block, district, ct_recode)
        )
    weights, districts = _finalize_county_counts(counts)
    return weights, _weight_receipt(
        counts, weights, districts, rows_read, effective_rows
    )


def _read_cd116_member(
    archive,
    member,
    counts,
    expected_state=None,
    ignored_states=frozenset(),
    ct_recode=None,
):
    try:
        raw = archive.open(member, "r")
    except (KeyError, OSError, RuntimeError) as exc:
        raise RedistrictingDensityError(
            f"failed to open CD116 member {member}: {exc}"
        ) from exc
    rows_read = 0
    effective_rows = 0
    ignored = collections.Counter()
    try:
        with raw:
            with io.TextIOWrapper(
                raw, encoding="utf-8-sig", errors="strict", newline=""
            ) as source:
                reader = csv.DictReader(source)
                if tuple(reader.fieldnames or ()) != BEF_HEADER:
                    raise RedistrictingDensityError(
                        f"CD116 member {member} header mismatch: expected "
                        f"{list(BEF_HEADER)!r}, found {reader.fieldnames!r}"
                    )
                for row in reader:
                    if None in row:
                        raise RedistrictingDensityError(
                            f"CD116 member {member} row {reader.line_num} "
                            f"has surplus fields {row[None]!r}"
                        )
                    block, district = _block_and_district(
                        row, f"CD116 member {member} row {reader.line_num}"
                    )
                    rows_read += 1
                    state_fips = block[:2]
                    if expected_state and state_fips != expected_state:
                        raise RedistrictingDensityError(
                            f"CD116 override {member} contains state "
                            f"{state_fips}; expected {expected_state}"
                        )
                    if state_fips in ignored_states:
                        ignored[state_fips] += 1
                        continue
                    effective_rows += int(
                        _add_block(counts, block, district, ct_recode)
                    )
    except RedistrictingDensityError:
        raise
    except (OSError, UnicodeError, csv.Error, RuntimeError) as exc:
        raise RedistrictingDensityError(
            f"failed streaming CD116 member {member}: {exc}"
        ) from exc
    if rows_read == 0:
        raise RedistrictingDensityError(
            f"CD116 member {member} has no data rows"
        )
    return {
        "member": member,
        "header": list(BEF_HEADER),
        "rows_read": rows_read,
        "effective_rows": effective_rows,
        "ignored_rows_by_state": dict(sorted(ignored.items())),
    }


def load_cd116_weights(
    zip_path,
    source_receipt_path=None,
    expected_districts=None,
    ct_recode=None,
):
    """Stream CD116 with complete state-file precedence for CO/MN/PA."""
    zip_path = _require_file(zip_path, "CD116 BEF ZIP")
    source_receipt_path = os.path.abspath(
        source_receipt_path or f"{zip_path}.source.json"
    )
    source_receipt = _read_json(
        source_receipt_path, "CD116 source receipt"
    )
    observed_sha = _validate_recorded_sha(
        zip_path, source_receipt, "CD116 BEF ZIP"
    )
    counts = collections.defaultdict(collections.Counter)
    members = {}
    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = set(archive.namelist())
            required = {BEF_NATIONAL_MEMBER, *BEF_OVERRIDE_MEMBERS.values()}
            missing = sorted(required - names)
            if missing:
                raise RedistrictingDensityError(
                    "CD116 ZIP missing member(s): " + ", ".join(missing)
                )
            for state_fips, member in BEF_OVERRIDE_MEMBERS.items():
                members[member] = _read_cd116_member(
                    archive,
                    member,
                    counts,
                    expected_state=state_fips,
                    ct_recode=ct_recode,
                )
            members[BEF_NATIONAL_MEMBER] = _read_cd116_member(
                archive,
                BEF_NATIONAL_MEMBER,
                counts,
                ignored_states=frozenset(BEF_OVERRIDE_MEMBERS),
                ct_recode=ct_recode,
            )
    except RedistrictingDensityError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise RedistrictingDensityError(
            f"invalid CD116 ZIP {zip_path}: {exc}"
        ) from exc
    weights, districts = _finalize_county_counts(counts)
    if expected_districts is not None and len(districts) != expected_districts:
        raise RedistrictingDensityError(
            f"CD116 yielded {len(districts)} voting districts; "
            f"expected {expected_districts}"
        )
    national = members[BEF_NATIONAL_MEMBER]
    overrides = {}
    for state_fips, member in BEF_OVERRIDE_MEMBERS.items():
        ignored = national["ignored_rows_by_state"].get(state_fips, 0)
        if ignored == 0:
            raise RedistrictingDensityError(
                f"national CD116 member has no rows for override {state_fips}"
            )
        overrides[state_fips] = {
            "member": member,
            "override_rows_used": members[member]["effective_rows"],
            "national_rows_ignored": ignored,
        }
    rows_read = sum(value["rows_read"] for value in members.values())
    effective_rows = sum(
        value["effective_rows"] for value in members.values()
    )
    receipt = _weight_receipt(
        counts, weights, districts, rows_read, effective_rows
    )
    receipt.update({
        "header": list(BEF_HEADER),
        "state_overrides_applied": len(overrides),
        "state_override_details": overrides,
        "members": {key: members[key] for key in sorted(members)},
        "input": {
            "file": zip_path,
            "source_receipt": source_receipt_path,
            "sha256": observed_sha,
            "url": source_receipt.get("url"),
            "retrieved": source_receipt.get("retrieved"),
        },
    })
    return weights, districts, receipt


def load_cd119_weights(path, expected_districts=None, ct_recode=None):
    """Stream normalized CD119 block assignments into county weights."""
    path = _require_file(path, "CD119 block assignment")
    counts = collections.defaultdict(collections.Counter)
    rows_read = 0
    effective_rows = 0
    try:
        with gzip.open(
            path, "rt", encoding="utf-8-sig", errors="strict", newline=""
        ) as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != CD119_HEADER:
                raise RedistrictingDensityError(
                    f"CD119 header mismatch: expected {list(CD119_HEADER)!r}, "
                    f"found {reader.fieldnames!r}"
                )
            for row in reader:
                if None in row:
                    raise RedistrictingDensityError(
                        f"CD119 row {reader.line_num} has surplus fields "
                        f"{row[None]!r}"
                    )
                block, district = _block_and_district(
                    row, f"CD119 row {reader.line_num}"
                )
                rows_read += 1
                effective_rows += int(
                    _add_block(counts, block, district, ct_recode)
                )
    except RedistrictingDensityError:
        raise
    except (OSError, EOFError, UnicodeError, csv.Error) as exc:
        raise RedistrictingDensityError(
            f"CD119 block assignment unreadable: {exc}"
        ) from exc
    if rows_read == 0:
        raise RedistrictingDensityError("CD119 block assignment has no rows")
    weights, districts = _finalize_county_counts(counts)
    if expected_districts is not None and len(districts) != expected_districts:
        raise RedistrictingDensityError(
            f"CD119 yielded {len(districts)} voting districts; "
            f"expected {expected_districts}"
        )
    receipt = _weight_receipt(
        counts, weights, districts, rows_read, effective_rows
    )
    receipt.update({
        "header": list(CD119_HEADER),
        "input": _input_receipt(path),
    })
    return weights, districts, receipt


def _county_fips(value):
    text = str(value if value is not None else "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit() or len(text) > 5:
        return None
    return text.zfill(5)


def _canonical_county_name(value):
    text = str(value or "").strip().strip("\"'*").casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\bsaint\b", "st", text)
    return re.sub(r"\s+", " ", text).strip()


def _county_name_variants(value):
    exact = _canonical_county_name(value)
    base = re.sub(
        r" (city and borough|census area|municipality|borough|county|parish)$",
        "",
        exact,
    )
    return tuple(dict.fromkeys(item for item in (exact, base) if item))


def _add_county_reference(index, ambiguous, state, name, fips, source):
    state = str(state or "").strip().upper()
    fips = _county_fips(fips)
    if state not in USPS_TO_FIPS or fips is None:
        return False
    if fips[:2] != USPS_TO_FIPS[state]:
        return False
    added = False
    for name_key in _county_name_variants(name):
        key = (state, name_key)
        if key in ambiguous:
            ambiguous[key].add(fips)
            continue
        prior = index.get(key)
        if prior is None:
            index[key] = {"fips": fips, "sources": {source}}
            added = True
        elif prior["fips"] == fips:
            prior["sources"].add(source)
            added = True
        else:
            ambiguous[key] = {prior["fips"], fips}
            del index[key]
    return added


def _resolve_county_reference(index, state, name):
    matches = {
        index[(state, variant)]["fips"]
        for variant in _county_name_variants(name)
        if (state, variant) in index
    }
    if len(matches) != 1:
        return None, None
    fips = next(iter(matches))
    sources = set()
    for variant in _county_name_variants(name):
        value = index.get((state, variant))
        if value is not None and value["fips"] == fips:
            sources.update(value["sources"])
    return fips, "+".join(sorted(sources))


def _load_acs_county_references(path):
    path = _require_file(path, "ACS geography county reference")
    index = {}
    ambiguous = {}
    rows_read = 0
    county_rows = 0
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source, delimiter="|")
            required = {"SUMLEVEL", "STUSAB", "STATE", "COUNTY", "NAME"}
            missing = sorted(required - set(reader.fieldnames or ()))
            if missing:
                raise RedistrictingDensityError(
                    "ACS geography reference lacks columns: "
                    + ", ".join(missing)
                )
            for row in reader:
                rows_read += 1
                if str(row.get("SUMLEVEL") or "").strip() != "050":
                    continue
                county_rows += 1
                name = str(row.get("NAME") or "").split(",", 1)[0]
                _add_county_reference(
                    index,
                    ambiguous,
                    row.get("STUSAB"),
                    name,
                    str(row.get("STATE") or "") + str(row.get("COUNTY") or ""),
                    "acs_2024_1yr_county",
                )
    except RedistrictingDensityError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RedistrictingDensityError(
            f"ACS geography county reference unreadable: {exc}"
        ) from exc
    if county_rows == 0:
        raise RedistrictingDensityError(
            "ACS geography reference has no summary-level 050 county rows"
        )
    return index, ambiguous, {
        "rows_read": rows_read,
        "county_rows": county_rows,
        "input": _input_receipt(path),
    }


def _read_zip_csv(archive, member, required_fields, label):
    try:
        raw = archive.open(member, "r")
    except (KeyError, OSError, RuntimeError) as exc:
        raise RedistrictingDensityError(
            f"failed to open {label} member {member}: {exc}"
        ) from exc
    rows = []
    with raw:
        with io.TextIOWrapper(
            raw, encoding="utf-8-sig", errors="strict", newline=""
        ) as source:
            reader = csv.DictReader(source)
            fields = set(reader.fieldnames or ())
            missing = sorted(set(required_fields) - fields)
            if missing:
                raise RedistrictingDensityError(
                    f"{label} member {member} lacks columns: "
                    + ", ".join(missing)
                )
            for row in reader:
                if None in row:
                    raise RedistrictingDensityError(
                        f"{label} member {member} row {reader.line_num} "
                        f"has surplus fields {row[None]!r}"
                    )
                rows.append(row)
    if not rows:
        raise RedistrictingDensityError(
            f"{label} member {member} has no rows"
        )
    return list(reader.fieldnames or ()), rows


def _normalize_code(value, width):
    text = str(value if value is not None else "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if not text.isdigit() or len(text) > width:
        return None
    return text.zfill(width)


def load_fdic_branches(path, common_counties):
    """Load the raw SOD universe using only STCNTYBR geography."""
    path = _require_file(path, "FDIC SOD raw cache")
    branches = []
    drops = collections.Counter()
    raw_rows = 0
    voting_state_rows = 0
    seen_ids = set()
    try:
        with gzip.open(
            path, "rt", encoding="utf-8", errors="strict", newline=""
        ) as source:
            for line_number, line in enumerate(source, start=1):
                raw_rows += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RedistrictingDensityError(
                        f"FDIC cache line {line_number} is invalid JSON: {exc}"
                    ) from exc
                if not isinstance(row, dict):
                    raise RedistrictingDensityError(
                        f"FDIC cache line {line_number} is not an object"
                    )
                state = str(row.get("STALPBR") or "").strip().upper()
                if state not in VOTING_STATES:
                    drops["non_voting_state_excluded"] += 1
                    continue
                voting_state_rows += 1
                county = _county_fips(row.get("STCNTYBR"))
                if county is None:
                    drops["missing_or_invalid_stcntybr"] += 1
                    continue
                if county[:2] != USPS_TO_FIPS[state]:
                    raise RedistrictingDensityError(
                        f"FDIC cache line {line_number} state/county mismatch: "
                        f"{state}/{county}"
                    )
                if county not in common_counties:
                    drops["county_absent_from_old_or_new_weights"] += 1
                    continue
                source_id = str(row.get("UNINUMBR") or line_number).strip()
                if source_id in seen_ids:
                    raise RedistrictingDensityError(
                        f"FDIC cache duplicates UNINUMBR {source_id!r}"
                    )
                seen_ids.add(source_id)
                branches.append({
                    "source_id": f"fdic-{source_id}",
                    "county_fips": county,
                })
    except RedistrictingDensityError:
        raise
    except (OSError, EOFError, UnicodeError, gzip.BadGzipFile) as exc:
        raise RedistrictingDensityError(f"FDIC cache unreadable: {exc}") from exc
    if raw_rows == 0:
        raise RedistrictingDensityError("FDIC cache has no rows")
    return branches, {
        "raw_branches": raw_rows,
        "voting_state_branches": voting_state_rows,
        "eligible_common_universe": len(branches),
        "dropped_branches": sum(drops.values()),
        "drop_reasons": dict(sorted(drops.items())),
        "geography_field": "STCNTYBR",
        "input": _input_receipt(path),
    }


def load_ncua_branches(path, acs_geo_path, common_counties):
    """Resolve raw NCUA county names through strict on-disk references."""
    path = _require_file(path, "NCUA call-report raw cache")
    index, ambiguous, acs_receipt = _load_acs_county_references(acs_geo_path)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
            missing_members = sorted(
                {NCUA_BRANCH_MEMBER, NCUA_FOICU_MEMBER} - names
            )
            if missing_members:
                raise RedistrictingDensityError(
                    "NCUA ZIP missing member(s): " + ", ".join(missing_members)
                )
            branch_header, branch_rows = _read_zip_csv(
                archive,
                NCUA_BRANCH_MEMBER,
                NCUA_BRANCH_FIELDS,
                "NCUA branch information",
            )
            foicu_header, foicu_rows = _read_zip_csv(
                archive,
                NCUA_FOICU_MEMBER,
                NCUA_FOICU_FIELDS,
                "NCUA FOICU",
            )
    except RedistrictingDensityError:
        raise
    except (OSError, UnicodeError, csv.Error, zipfile.BadZipFile) as exc:
        raise RedistrictingDensityError(f"NCUA ZIP unreadable: {exc}") from exc

    foicu_by_cu = {}
    invalid_foicu_county_codes = 0
    for row_number, row in enumerate(foicu_rows, start=2):
        cu_number = str(row.get("CU_NUMBER") or "").strip()
        state_fips = _normalize_code(row.get("STATE_CODE"), 2)
        county_code = _normalize_code(row.get("COUNTY_CODE"), 3)
        if not cu_number:
            raise RedistrictingDensityError(
                f"NCUA FOICU row {row_number} has blank CU_NUMBER"
            )
        if (
            state_fips not in FIPS_TO_USPS or
            county_code is None or
            county_code == "000"
        ):
            invalid_foicu_county_codes += 1
            continue
        county_fips = state_fips + county_code
        prior = foicu_by_cu.get(cu_number)
        if prior is not None and prior != county_fips:
            raise RedistrictingDensityError(
                f"NCUA FOICU repeats CU {cu_number} with conflicting counties"
            )
        foicu_by_cu[cu_number] = county_fips

    main_office_references = 0
    main_office_state_mismatches = 0
    for row in branch_rows:
        main_office = str(row.get("MainOffice") or "").strip().casefold()
        if main_office != "yes":
            continue
        cu_number = str(row.get("CU_NUMBER") or "").strip()
        county_fips = foicu_by_cu.get(cu_number)
        state = str(
            row.get("PhysicalAddressStateCode") or ""
        ).strip().upper()
        if county_fips is None or state not in USPS_TO_FIPS:
            continue
        if county_fips[:2] != USPS_TO_FIPS[state]:
            main_office_state_mismatches += 1
            continue
        if _add_county_reference(
            index,
            ambiguous,
            state,
            row.get("PhysicalAddressCountyName2"),
            county_fips,
            "ncua_foicu_main_office",
        ):
            main_office_references += 1

    branches = []
    drops = collections.Counter()
    resolution_methods = collections.Counter()
    voting_state_rows = 0
    for row_number, row in enumerate(branch_rows, start=2):
        state = str(
            row.get("PhysicalAddressStateCode") or ""
        ).strip().upper()
        if state not in VOTING_STATES:
            drops["non_voting_state_excluded"] += 1
            continue
        voting_state_rows += 1
        county_name = str(
            row.get("PhysicalAddressCountyName2") or ""
        ).strip()
        if not county_name:
            drops["blank_county_name"] += 1
            continue
        county_fips, method = _resolve_county_reference(
            index, state, county_name
        )
        if county_fips is None:
            drops["county_name_unresolved_or_ambiguous"] += 1
            continue
        if county_fips not in common_counties:
            drops["county_absent_from_old_or_new_weights"] += 1
            continue
        resolution_methods[method] += 1
        branches.append({
            "source_id": f"ncua-{row_number - 1:09d}",
            "county_fips": county_fips,
        })

    geography_drops = (
        drops["blank_county_name"] +
        drops["county_name_unresolved_or_ambiguous"] +
        drops["county_absent_from_old_or_new_weights"]
    )
    drop_rate = geography_drops / voting_state_rows if voting_state_rows else 0.0
    if drop_rate > CU_DROP_ESCALATION_THRESHOLD:
        raise RedistrictingDensityError(
            "ESCALATION: NCUA voting-state geography drops are %.2f%% "
            "(%d/%d), above the 10.00%% stop threshold; reasons=%r; "
            "no outputs may be published"
            % (
                drop_rate * 100,
                geography_drops,
                voting_state_rows,
                dict(sorted(drops.items())),
            )
        )
    ambiguous_sample = [
        {
            "state": key[0],
            "county_key": key[1],
            "fips_candidates": sorted(values),
        }
        for key, values in sorted(ambiguous.items())[:25]
    ]
    return branches, {
        "raw_branches": len(branch_rows),
        "voting_state_branches": voting_state_rows,
        "eligible_common_universe": len(branches),
        "geography_dropped_branches": geography_drops,
        "geography_drop_rate": drop_rate,
        "drop_reasons": dict(sorted(drops.items())),
        "resolution_methods": dict(sorted(resolution_methods.items())),
        "county_reference": {
            "acs": acs_receipt,
            "foicu_rows": len(foicu_rows),
            "foicu_valid_county_codes": len(foicu_by_cu),
            "foicu_invalid_county_codes": invalid_foicu_county_codes,
            "main_office_references_added": main_office_references,
            "main_office_state_mismatches": main_office_state_mismatches,
            "resolved_name_keys": len(index),
            "ambiguous_name_keys": len(ambiguous),
            "ambiguous_sample": ambiguous_sample,
            "method": (
                "strict state+canonical-county-name lookup from on-disk "
                "ACS summary-level 050 rows, supplemented only by NCUA "
                "main-office county names joined to FOICU state/county codes; "
                "conflicting keys are excluded"
            ),
        },
        "members": {
            "branch": NCUA_BRANCH_MEMBER,
            "foicu": NCUA_FOICU_MEMBER,
        },
        "headers": {
            "branch": branch_header,
            "foicu": foicu_header,
        },
        "drop_escalation_threshold": CU_DROP_ESCALATION_THRESHOLD,
        "passed_drop_guard": True,
        "input": _input_receipt(path),
    }


def allocate_branches(
    county_weights,
    bank_branches,
    cu_branches,
    district_ids=None,
    cu_drop_threshold=CU_DROP_ESCALATION_THRESHOLD,
):
    """Fractionally allocate a fixed branch universe through county weights."""
    bank_branches = list(bank_branches)
    cu_branches = list(cu_branches)
    if district_ids is None:
        district_ids = {
            district
            for values in county_weights.values()
            for district, _weight in values
        }
    totals = {
        district: {"bank": 0.0, "cu": 0.0}
        for district in set(district_ids)
    }
    receipt = {}
    for source, branches, metric in (
        ("fdic", bank_branches, "bank"),
        ("ncua", cu_branches, "cu"),
    ):
        drops = collections.Counter()
        allocated_branches = 0
        allocated_equivalents = 0.0
        for branch in branches:
            county = _county_fips(branch.get("county_fips"))
            if county is None:
                drops["missing_usable_county"] += 1
                continue
            weights = county_weights.get(county)
            if weights is None:
                drops["county_absent_from_boundary_weights"] += 1
                continue
            if not weights:
                drops["county_has_no_district_allocation"] += 1
                continue
            weight_sum = sum(weight for _district, weight in weights)
            if weight_sum <= 0 or weight_sum > 1 + 1e-9:
                raise RedistrictingDensityError(
                    f"boundary weights for county {county} sum to "
                    f"invalid value {weight_sum:.12f}"
                )
            for district, weight in weights:
                if district not in totals:
                    raise RedistrictingDensityError(
                        f"county {county} resolves to unknown district {district}"
                    )
                totals[district][metric] += weight
            allocated_branches += 1
            allocated_equivalents += weight_sum
        fractional_total = sum(value[metric] for value in totals.values())
        # Relative tolerance: absolute 1e-9 is unrealistic for float
        # accumulation across ~75k fractional branch allocations
        # (coordinator fix 2026-08-06; observed drift 2.2e-9 at 74,684).
        if not math.isclose(
            fractional_total,
            allocated_equivalents,
            rel_tol=1e-12,
            abs_tol=1e-6,
        ):
            raise RedistrictingDensityError(
                f"{source} fractional allocation is not conserved: "
                f"{fractional_total:.12f} != {allocated_equivalents:.12f}"
            )
        receipt[source] = {
            "input_branches": len(branches),
            "allocated_branches": allocated_branches,
            "dropped_branches": sum(drops.values()),
            "drop_reasons": dict(sorted(drops.items())),
            "fractional_branch_total": fractional_total,
            "unallocated_fractional_residual": (
                allocated_branches - allocated_equivalents
            ),
        }
    cu_input = receipt["ncua"]["input_branches"]
    cu_dropped = receipt["ncua"]["dropped_branches"]
    cu_drop_rate = cu_dropped / cu_input if cu_input else 0.0
    receipt["cu_drop_rate"] = cu_drop_rate
    receipt["cu_drop_escalation_threshold"] = cu_drop_threshold
    if cu_drop_threshold is not None and cu_drop_rate > cu_drop_threshold:
        raise RedistrictingDensityError(
            "ESCALATION: allocation dropped %.2f%% of CU branches (%d/%d), "
            "above the %.2f%% stop threshold; no outputs may be published"
            % (
                cu_drop_rate * 100,
                cu_dropped,
                cu_input,
                cu_drop_threshold * 100,
            )
        )
    districts = {}
    for district in sorted(totals):
        bank_count = totals[district]["bank"]
        cu_count = totals[district]["cu"]
        combined = bank_count + cu_count
        districts[district] = {
            "bank_branches": round(bank_count, 12),
            "cu_branches": round(cu_count, 12),
            "cu_share": round(cu_count / combined, 12) if combined else None,
        }
    return districts, receipt


def _number(value, label, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RedistrictingDensityError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise RedistrictingDensityError(f"{label} must be finite")
    if minimum is not None and result < minimum:
        raise RedistrictingDensityError(f"{label} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise RedistrictingDensityError(f"{label} must be at most {maximum}")
    return result


def _integerish(value, label, minimum=None):
    number = _number(value, label)
    if not number.is_integer():
        raise RedistrictingDensityError(f"{label} must be an integer")
    result = int(number)
    if minimum is not None and result < minimum:
        raise RedistrictingDensityError(f"{label} must be at least {minimum}")
    return result


def _share_map(values, label):
    shares = {}
    for district, payload in values.items():
        raw = payload.get("cu_share") if isinstance(payload, Mapping) else payload
        shares[str(district)] = _number(
            raw, f"{label} {district} share", minimum=0.0, maximum=1.0
        )
    return shares


def _spine_house_seats(spine_rows):
    grouped = collections.defaultdict(set)
    input_rows = 0
    for row_number, row in enumerate(spine_rows, start=2):
        congress = _integerish(
            row.get("congress"), f"spine row {row_number} congress", minimum=1
        )
        if congress not in (117, 119):
            continue
        chamber = str(row.get("chamber") or "").strip().casefold()
        if chamber not in {"house", "rep", "h"}:
            continue
        member = str(
            row.get("bioguide_id") or row.get("bioguide") or ""
        ).strip()
        if not member:
            continue
        state = str(
            row.get("state_abbrev") or row.get("state") or ""
        ).strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise RedistrictingDensityError(
                f"spine row {row_number} has invalid state {state!r}"
            )
        district_value = row.get("district_code")
        if district_value is None or str(district_value).strip() == "":
            district_value = row.get("district")
        district = _integerish(
            district_value,
            f"spine row {row_number} district_code",
            minimum=0,
        )
        grouped[(member, congress)].add((state, district))
        input_rows += 1
    resolved = {}
    for key, seats in grouped.items():
        if len(seats) != 1:
            raise RedistrictingDensityError(
                f"spine has conflicting House seats for {key!r}: "
                f"{sorted(seats)!r}"
            )
        resolved[key] = next(iter(seats))
    return resolved, {
        "house_input_rows_117_119": input_rows,
        "resolved_member_congress_seats": len(resolved),
        "duplicate_seat_rows_collapsed": input_rows - len(resolved),
    }


def _seat_id(state, district_number, available, label):
    candidates = [f"{state}-{district_number:02d}"]
    if district_number in (0, 1):
        candidates.append(f"{state}-00")
    for candidate in candidates:
        if candidate in available:
            return candidate
    raise RedistrictingDensityError(
        f"{label} seat {state}/{district_number} has no density; "
        f"tried {candidates!r}"
    )


def build_member_deltas(spine_rows, old_densities, new_densities):
    """Join continuing voting House members to symmetric old/new shares."""
    old_shares = _share_map(old_densities, "old-line district")
    new_shares = _share_map(new_densities, "new-line district")
    seats, spine_receipt = _spine_house_seats(spine_rows)
    members_117 = {member for member, congress in seats if congress == 117}
    members_119 = {member for member, congress in seats if congress == 119}
    continuing = sorted(members_117 & members_119)
    rows = []
    non_voting = []
    for member in continuing:
        state_117, district_117 = seats[(member, 117)]
        state_119, district_119 = seats[(member, 119)]
        if state_117 not in VOTING_STATES or state_119 not in VOTING_STATES:
            non_voting.append(member)
            continue
        seat_117 = _seat_id(
            state_117, district_117, old_shares, f"member {member} Congress 117"
        )
        seat_119 = _seat_id(
            state_119, district_119, new_shares, f"member {member} Congress 119"
        )
        old_share = old_shares[seat_117]
        new_share = new_shares[seat_119]
        rows.append({
            "bioguide": member,
            "state": state_119,
            "seat_117": seat_117,
            "seat_119": seat_119,
            "seat_changed": int(seat_117 != seat_119),
            "cu_share_old": old_share,
            "cu_share_new": new_share,
            "delta": round(new_share - old_share, 12),
        })
    receipt = dict(spine_receipt)
    receipt.update({
        "members_house_117": len(members_117),
        "members_house_119": len(members_119),
        "continuing_house_members": len(continuing),
        "delta_rows": len(rows),
        "only_117_excluded": len(members_117 - members_119),
        "only_119_excluded": len(members_119 - members_117),
        "non_voting_continuing_excluded": len(non_voting),
        "non_voting_continuing_bioguides": non_voting,
    })
    return rows, receipt


def _load_spine_rows(path):
    path = _require_file(path, "member-Congress spine")
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = set(reader.fieldnames or ())
            required = {"congress", "chamber", "district_code"}
            if not ({"bioguide_id", "bioguide"} & fields):
                required.add("bioguide_id")
            if not ({"state_abbrev", "state"} & fields):
                required.add("state_abbrev")
            missing = sorted(required - fields)
            if missing:
                raise RedistrictingDensityError(
                    "member spine lacks columns: " + ", ".join(missing)
                )
            return list(reader)
    except RedistrictingDensityError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RedistrictingDensityError(f"member spine unreadable: {exc}") from exc


def build_artifacts(
    cd116_zip,
    cd119_path,
    fdic_path,
    ncua_path,
    acs_geo_path,
    spine_path,
    source_receipt_path=None,
    ct_bridge_path=None,
):
    """Build symmetric old/new district maps and member deltas in memory."""
    ct_recode = (
        _load_ct_tract_recode(ct_bridge_path) if ct_bridge_path else None
    )
    old_weights, old_districts, old_receipt = load_cd116_weights(
        cd116_zip,
        source_receipt_path=source_receipt_path,
        expected_districts=435,
        ct_recode=ct_recode,
    )
    new_weights, new_districts, new_receipt = load_cd119_weights(
        cd119_path, expected_districts=435, ct_recode=ct_recode
    )
    common_counties = set(old_weights) & set(new_weights)
    if not common_counties:
        raise RedistrictingDensityError(
            "old/new boundary weights have no common counties"
        )
    bank_branches, fdic_receipt = load_fdic_branches(
        fdic_path, common_counties
    )
    cu_branches, ncua_receipt = load_ncua_branches(
        ncua_path, acs_geo_path, common_counties
    )
    old_density, old_allocation = allocate_branches(
        old_weights,
        bank_branches,
        cu_branches,
        district_ids=old_districts,
        cu_drop_threshold=None,
    )
    new_density, new_allocation = allocate_branches(
        new_weights,
        bank_branches,
        cu_branches,
        district_ids=new_districts,
        cu_drop_threshold=None,
    )
    for source in ("fdic", "ncua"):
        if (
            old_allocation[source]["input_branches"] !=
            new_allocation[source]["input_branches"]
        ):
            raise RedistrictingDensityError(
                f"old/new {source} branch universes differ"
            )
        if old_allocation[source]["dropped_branches"] or new_allocation[source][
            "dropped_branches"
        ]:
            raise RedistrictingDensityError(
                f"internal error: common {source} universe dropped during allocation"
            )
    for label, density in (("old", old_density), ("new", new_density)):
        empty = sorted(
            district
            for district, value in density.items()
            if value["cu_share"] is None
        )
        if empty:
            raise RedistrictingDensityError(
                f"{label}-line districts with zero total branches "
                f"(county-code mismatch?): {empty!r}"
            )
    spine_rows = _load_spine_rows(spine_path)
    member_rows, member_receipt = build_member_deltas(
        spine_rows, old_density, new_density
    )
    receipt = {
        "symmetric_method_note": SYMMETRIC_METHOD_NOTE,
        "branch_universe": {
            "fdic": fdic_receipt,
            "ncua": ncua_receipt,
            "common_counties": len(common_counties),
            "old_only_counties": len(set(old_weights) - set(new_weights)),
            "new_only_counties": len(set(new_weights) - set(old_weights)),
        },
        "boundary_weights": {
            "old_lines_cd116": old_receipt,
            "new_lines_cd119": new_receipt,
        },
        "allocation": {
            "old_lines": old_allocation,
            "new_lines": new_allocation,
            "same_branch_objects_used_for_both": True,
        },
        "member_join": member_receipt,
        "input_receipts": {
            "cd116": old_receipt["input"],
            "cd119": new_receipt["input"],
            "fdic": fdic_receipt["input"],
            "ncua": ncua_receipt["input"],
            "acs_county_reference": ncua_receipt["county_reference"][
                "acs"
            ]["input"],
            "member_spine": _input_receipt(spine_path),
        },
    }
    artifact = {
        "schema": SCHEMA,
        "generated_at": _utc_iso(),
        "symmetric_method_note": SYMMETRIC_METHOD_NOTE,
        "old_lines": old_density,
        "new_lines": new_density,
        "receipt": receipt,
    }
    return artifact, member_rows


def _outside_repo(path):
    absolute = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(MB_ROOT))
    try:
        return os.path.commonpath((absolute, root)) != root
    except ValueError:
        return True


def _format_csv_number(value):
    return format(float(value), ".12g")


def write_outputs(artifact, member_rows, out_dir):
    """Atomically write the contracted outputs outside the repository."""
    out_dir = os.path.abspath(out_dir)
    if not _outside_repo(out_dir):
        raise RedistrictingDensityError(
            f"refusing repository-tree output: {out_dir}"
        )
    os.makedirs(out_dir, exist_ok=True)
    density_path = os.path.join(out_dir, "cd116_density.json")
    member_path = os.path.join(out_dir, "member_density_delta.csv")
    density_temporary = f"{density_path}.tmp.{os.getpid()}"
    member_temporary = f"{member_path}.tmp.{os.getpid()}"
    try:
        with open(
            density_temporary, "w", encoding="utf-8", newline="\n"
        ) as target:
            json.dump(
                artifact,
                target,
                ensure_ascii=False,
                sort_keys=True,
                indent=1,
            )
            target.write("\n")
        with open(
            member_temporary, "w", encoding="utf-8", newline=""
        ) as target:
            writer = csv.DictWriter(
                target, fieldnames=MEMBER_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for row in member_rows:
                rendered = dict(row)
                for field in ("cu_share_old", "cu_share_new", "delta"):
                    rendered[field] = _format_csv_number(rendered[field])
                writer.writerow(rendered)
        os.replace(density_temporary, density_path)
        os.replace(member_temporary, member_path)
    except BaseException:
        for temporary in (density_temporary, member_temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
        raise
    return density_path, member_path


def _local_root(kind):
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RedistrictingDensityError("LOCALAPPDATA is not set")
    return os.path.join(local, "Melange", kind)


def main(argv=None):
    lda_root = _local_root("lda_rebuild")
    data_root = os.environ.get("DEMO_DATA_ROOT") or _local_root("demographics")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cd116-zip",
        default=os.path.join(lda_root, "raw", "redistricting", "cd116.zip"),
    )
    parser.add_argument(
        "--cd119",
        default=os.path.join(data_root, "geo", "cd119_block_assign.csv.gz"),
    )
    parser.add_argument(
        "--fdic",
        default=os.path.join(data_root, "financial", "fdic_sod_2025.jsonl.gz"),
    )
    parser.add_argument(
        "--ncua",
        default=os.path.join(
            data_root, "financial", "call-report-data-2026-03.zip"
        ),
    )
    parser.add_argument(
        "--acs-geography",
        default=os.path.join(
            data_root, "acs", "2024", "1yr", "Geos20241YR.txt"
        ),
    )
    parser.add_argument(
        "--ct-bridge",
        default=os.path.join(data_root, "geo", "ct_2022_block_crosswalk.csv"),
    )
    parser.add_argument(
        "--spine", default=os.path.join(MB_ROOT, "votes", "HSall_members.csv")
    )
    parser.add_argument(
        "--out-dir",
        default=os.path.join(lda_root, "derived", "redistricting"),
    )
    args = parser.parse_args(argv)
    artifact, member_rows = build_artifacts(
        cd116_zip=args.cd116_zip,
        cd119_path=args.cd119,
        fdic_path=args.fdic,
        ncua_path=args.ncua,
        acs_geo_path=args.acs_geography,
        spine_path=args.spine,
        ct_bridge_path=args.ct_bridge,
    )
    density_path, member_path = write_outputs(
        artifact, member_rows, args.out_dir
    )
    print(
        "symmetric redistricting density: %d old districts, %d new "
        "districts, %d member deltas -> %s, %s"
        % (
            len(artifact["old_lines"]),
            len(artifact["new_lines"]),
            len(member_rows),
            density_path,
            member_path,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
