# -*- coding: utf-8 -*-
"""Locked FEC exposure x rebuilt-LDA attention corroboration look, round 3.

No-argument mode reports gate status. ``--simulate`` runs only the fitted
synthetic battery. Only ``--look`` may construct a real X-Y pairing. The
preregistration is never modified by this script.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import collections
import csv
import datetime
import hashlib
import json
import math
import os
import random
import re
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


PREREG_PATH = os.path.join(MB_ROOT, "fec_lda_bridge3_prereg.md")
LEGISLATORS_PATH = os.path.join(MB_ROOT, "fec", "legislators.csv")
PRIOR_SHA256 = "CE3136A896CFA2E86CF80B68227825F863EAD1C605C6A1C1B189DDAFAA9EDED4"
PRIOR_MEMBER_COUNT = 363
LEGISLATORS_SHA256 = "C5A6CBDBAAE0BB19FAA89852C29922809ECECC70B97C149B4709588087E5E391"
YEARS = (2023, 2024, 2025, 2026)
CYCLES = ("2023-2024", "2025-2026")
CYCLE_YEARS = {"2023-2024": (2023, 2024), "2025-2026": (2025, 2026)}
CYCLE_CONGRESS = {"2023-2024": 118, "2025-2026": 119}
INDUSTRIES = ("credit_union", "fintech", "crypto")
STRATA = tuple((industry, cycle) for industry in INDUSTRIES for cycle in CYCLES)
EXPECTED_X_POSITIVE = {
    ("fintech", "2023-2024"): 48,
    ("crypto", "2023-2024"): 44,
    ("credit_union", "2023-2024"): 48,
    ("fintech", "2025-2026"): 55,
    ("crypto", "2025-2026"): 66,
    ("credit_union", "2025-2026"): 80,
}
NETWORK_HEADER = ["lob_id", "bioguide_id", "year", "n_bills"]
TOTALS_HEADER = ["bioguide", "total_n_bills"]
STRICT_FILENAMES = {year: f"our_network_{year}_strict.csv" for year in YEARS}
GENEROUS_FILENAMES = {
    year: f"our_network_{year}_generous.csv" for year in YEARS
}
TOTAL_FILENAMES = {year: f"totals_{year}.csv" for year in YEARS}
PERMUTATIONS = 5000
PERMUTATION_SEED = 87001
RANK_TOLERANCE = 1e-10
CONTROL_NAMES = ("party", "chamber", "volume", "committee")
ROUND2_OMISSION = (
    "  - round-2-window descriptive replay: OMITTED — its committee-control "
    "input has no pinned pre-lock snapshot rule; emitting it would require "
    "post-lock discretion, so it is not computed."
)
OVERCONTROL_SENTENCE = (
    "association present but not separable from committee structure with "
    "this design"
)
CONSEQUENCE_WORDING = {
    "STRONG": (
        "FEC relational exposure is STRONGLY CORROBORATED (2023–26, "
        "org-exact, our instrument, four-control) as a cross-sectional proxy "
        "for LDA lobbying attention."
    ),
    "CORROBORATED": (
        'candidate permitted; labels "directionally corroborated"; '
        'unqualified "predictor" barred.'
    ),
    "UNCORROBORATED": (
        "FEC exposure remains a target-side money prior ONLY and must NOT be "
        "presented as a lobbying-relationship predictor or projection. Route "
        "CLOSED for the 2023–2026 window on this instrument; A DIFFERENT "
        "CONTROL SET ON THIS WINDOW IS NOT A SUCCESSOR."
    ),
}

CROSSWALK = {
    "credit_union": {
        "BOEING EMPLOYEES' CREDIT UNION": "d039e7e5-f821-55d5-9e38-f93c0b71aecb",
        "NAVY FEDERAL CREDIT UNION": "69c5a9e9-7d79-557e-8ff5-f07df772534d",
        "PENTAGON FEDERAL CREDIT UNION": "c27f7c24-43ec-52f7-80d6-ebb74cc526b1",
    },
    "fintech": {
        "BLOCK, INC.": "0f549ea5-9fdb-5cb9-9a9f-82682d4ef353",
        "SQUARE, INC.": "f2f8dae7-b696-5e01-8067-31b357170ec2",
        "CHIME": "9a33b530-6a10-52df-9d55-a2f4e0168316",
        "DAVE, INC.": "5f438304-3b42-5241-9821-874137fdc0ce",
        "KALSHI, INC.": "18cf487e-9e89-52ec-93dc-d91de45c0b84",
        "KLARNA INC.": "c3dc5b6d-38b1-5331-bec4-6cf081fa6cf1",
        "PLAID INC.": "3f7a52db-9be0-5222-84bd-693a1ccc38ae",
        "ROBINHOOD": "4f43f95f-f6f0-5b47-9f74-08d6b885ac55",
        "SOFI TECHNOLOGIES, INC.": "03b1f6cd-0546-59d2-97d3-9a31da3063b0",
        "STRIPE": "b849b9da-67fa-5eae-914b-92ac41c7ad31",
        "STRIPE, INC.": "cb4ac22a-dd82-5a42-a289-83fd13ba07d0",
    },
    "crypto": {
        "BAKKT HOLDINGS, INC.": "dbdd9f97-b360-53f0-adc8-9a7e5e4c967c",
        "BAM TRADING SERVICES, INC./BINANCE.US": "1c3314d2-e20e-5484-8084-97e58c4eca81",
        "BINANCE HOLDINGS LIMITED": "75d2c2aa-fb94-5005-acff-1479c56d41b5",
        "COINBASE, INC.": "67e35f81-afa7-5258-998e-7f08a3512e20",
        "COINBASE GLOBAL, INC.": "7ddb0913-d306-57d7-b42d-febd24a314cf",
        "FORIS DAX, INC. D/B/A CRYPTO.COM": "6904853b-ba69-5493-85b0-6699c0f7e33b",
        "GEMINI TRUST COMPANY, LLC": "4317e970-8485-55be-af60-d59762a1c8d7",
        "PAYWARD INC D/B/A KRAKEN": "8b517298-a4d4-5e93-bf5a-86af84076c66",
    },
}
PINNED_LOB_IDS = frozenset(
    lob_id for clients in CROSSWALK.values() for lob_id in clients.values()
)


class IngestError(Exception):
    """A gate or input failure that does not consume the look."""


class DefectError(Exception):
    """Independent implementations disagreed; the look is not consumed."""


def _sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): cannot hash " + path + ": " + str(exc)
        ) from exc
    return digest.hexdigest().upper()


def _input_paths():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise IngestError("LOOK REFUSED (not consumed): LOCALAPPDATA is unset")
    root = os.path.join(local_app_data, "Melange", "lda_rebuild")
    derived = os.path.join(root, "derived")
    return {
        "strict": {
            year: os.path.join(derived, STRICT_FILENAMES[year]) for year in YEARS
        },
        "generous": {
            year: os.path.join(derived, GENEROUS_FILENAMES[year]) for year in YEARS
        },
        "totals": {
            year: os.path.join(derived, TOTAL_FILENAMES[year]) for year in YEARS
        },
        "committee_dir": os.path.join(root, "raw", "committee_snapshots"),
        "prior": os.path.join(
            local_app_data,
            "Melange",
            "fec_spending",
            "derived",
            "fec_opposition_prior.json",
        ),
        "legislators": LEGISLATORS_PATH,
    }


def _verify_crosswalk():
    names = set()
    lob_ids = set()
    for industry in INDUSTRIES:
        for name, lob_id in CROSSWALK[industry].items():
            if name in names:
                raise DefectError("duplicate name in locked crosswalk: " + name)
            if lob_id in lob_ids:
                raise DefectError("duplicate lob_id in locked crosswalk: " + lob_id)
            names.add(name)
            lob_ids.add(lob_id)
    if lob_ids != PINNED_LOB_IDS or len(lob_ids) != 22:
        raise DefectError("hardcoded crosswalk is not the locked 22-lob_id set")


def _read_prereg():
    try:
        with open(PREREG_PATH, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): prereg absent at " + PREREG_PATH
        ) from exc
    if re.search(r"(?m)^STATUS: LOCKED\s*$", text) is None:
        raise IngestError(
            "LOOK REFUSED (not consumed): STATUS: LOCKED line absent"
        )
    if re.search(r"(?:^|\n)## RESULT-BRIDGE3(?:\n|$)", text):
        raise IngestError(
            "LOOK REFUSED (not consumed): RESULT-BRIDGE3 already recorded"
        )
    return text


def _collection_body(text):
    headings = list(
        re.finditer(r"(?:^|\n)## COLLECTION RECORD(?:\n|$)", text)
    )
    if len(headings) != 1:
        raise IngestError(
            "LOOK REFUSED (not consumed): COLLECTION RECORD absent or malformed"
        )
    start = headings[0].end()
    following = re.search(r"\n## ", text[start:])
    return text[start:start + following.start()] if following else text[start:]


def _collection_record_pins(text):
    lines = _collection_body(text).splitlines()
    csv_pins = {}
    filenames = (
        list(STRICT_FILENAMES.values())
        + list(GENEROUS_FILENAMES.values())
        + list(TOTAL_FILENAMES.values())
    )
    for filename in filenames:
        candidates = [line for line in lines if filename in line]
        pattern = re.compile(
            re.escape(filename)
            + r" SHA-256 ([0-9A-Fa-f]{64}) rows ([0-9]+)"
        )
        if len(candidates) != 1 or pattern.fullmatch(candidates[0]) is None:
            raise IngestError(
                "LOOK REFUSED (not consumed): COLLECTION RECORD line malformed for "
                + filename
            )
        match = pattern.fullmatch(candidates[0])
        csv_pins[filename] = {
            "sha256": match.group(1).upper(),
            "rows": int(match.group(2)),
        }

    committee_pattern = re.compile(
        r"committee-membership-(118|119)-([0-9A-Fa-f]{40})\.yaml "
        r"SHA-256 ([0-9A-Fa-f]{64})"
    )
    committee_candidates = [
        line for line in lines if line.startswith("committee-membership-")
    ]
    committee = {}
    for line in committee_candidates:
        match = committee_pattern.fullmatch(line)
        if match is None:
            raise IngestError(
                "LOOK REFUSED (not consumed): malformed committee snapshot line"
            )
        congress = int(match.group(1))
        if congress in committee:
            raise IngestError(
                f"LOOK REFUSED (not consumed): duplicate committee snapshot {congress}"
            )
        committee[congress] = {
            "filename": line.split(" SHA-256 ", 1)[0],
            "commit": match.group(2).lower(),
            "sha256": match.group(3).upper(),
        }
    if set(committee) != {118, 119}:
        raise IngestError(
            "LOOK REFUSED (not consumed): committee snapshots must be exactly 118 and 119"
        )

    harvest_pattern = re.compile(
        r"harvested-client-ids "
        r"([0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}) "
        r"count ([0-9]+)"
    )
    harvest_candidates = [
        line for line in lines if line.startswith("harvested-client-ids")
    ]
    if len(harvest_candidates) != 22:
        raise IngestError(
            "LOOK REFUSED (not consumed): expected exactly 22 harvested-client-ids lines"
        )
    harvest = {}
    for line in harvest_candidates:
        match = harvest_pattern.fullmatch(line)
        if match is None:
            raise IngestError(
                "LOOK REFUSED (not consumed): malformed harvested-client-ids line: "
                + line
            )
        lob_id = match.group(1).lower()
        if lob_id not in PINNED_LOB_IDS:
            raise IngestError(
                "LOOK REFUSED (not consumed): unrecognized harvested lob_id " + lob_id
            )
        if lob_id in harvest:
            raise IngestError(
                "LOOK REFUSED (not consumed): duplicate harvested lob_id " + lob_id
            )
        harvest[lob_id] = int(match.group(2))
    missing = sorted(PINNED_LOB_IDS - set(harvest))
    if missing:
        raise IngestError(
            "LOOK REFUSED (not consumed): missing harvested lob_id " + missing[0]
        )
    return {"csv": csv_pins, "committee": committee, "harvest": harvest}


def parse_committee_snapshot_text(text):
    """Parse the pinned committee-id/bioguide YAML subset, failing loudly."""
    if not isinstance(text, str):
        raise IngestError("committee snapshot text must be a string")
    committees = collections.defaultdict(set)
    current = None
    in_item = False
    item_bioguide = None
    top_pattern = re.compile(r"([A-Z0-9]+):\s*")
    empty_top_pattern = re.compile(r"([A-Z0-9]+):\s*\[\]\s*")
    item_pattern = re.compile(
        r"\s*-\s+(name|bioguide):\s*(.*?)\s*"
    )
    metadata_pattern = re.compile(
        r"\s{2,}([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*"
    )

    def finish_item(line_number):
        nonlocal in_item, item_bioguide
        if not in_item:
            return
        if item_bioguide is None:
            raise IngestError(
                f"committee snapshot item missing bioguide before line {line_number}"
            )
        if item_bioguide in committees[current]:
            raise IngestError(
                f"committee snapshot duplicate bioguide {item_bioguide} in {current}"
            )
        committees[current].add(item_bioguide)
        in_item = False
        item_bioguide = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        empty_top = empty_top_pattern.fullmatch(line)
        if empty_top is not None:
            finish_item(line_number)
            committee_id = empty_top.group(1)
            if committee_id in committees:
                raise IngestError(
                    f"committee snapshot duplicate key {committee_id} at line "
                    f"{line_number}"
                )
            committees[committee_id] = set()
            current = None
            continue
        top = top_pattern.fullmatch(line)
        if top is not None:
            finish_item(line_number)
            current = top.group(1)
            if current in committees:
                raise IngestError(
                    f"committee snapshot duplicate key {current} at line {line_number}"
                )
            committees[current] = set()
            continue
        item = item_pattern.fullmatch(line)
        if item is not None:
            if current is None:
                raise IngestError(
                    f"committee snapshot member before key at line {line_number}"
                )
            finish_item(line_number)
            in_item = True
            if item.group(1) == "bioguide":
                if re.fullmatch(r"[A-Z][0-9]{6}", item.group(2)) is None:
                    raise IngestError(
                        f"committee snapshot invalid bioguide at line {line_number}"
                    )
                item_bioguide = item.group(2)
            continue
        metadata = metadata_pattern.fullmatch(line)
        if metadata is not None and current and in_item:
            if metadata.group(1) == "bioguide":
                if item_bioguide is not None or re.fullmatch(
                    r"[A-Z][0-9]{6}", metadata.group(2)
                ) is None:
                    raise IngestError(
                        f"committee snapshot invalid/duplicate bioguide at line {line_number}"
                    )
                item_bioguide = metadata.group(2)
            continue
        raise IngestError(
            f"committee snapshot malformed line {line_number}: {line!r}"
        )
    finish_item(len(text.splitlines()) + 1)
    if not committees:
        raise IngestError("committee snapshot has no committee keys")
    return dict(committees)


def finance_committee_members(committees):
    members = set()
    for committee_id, bioguides in committees.items():
        if committee_id.startswith("HSBA") or committee_id.startswith("SSBK"):
            members.update(bioguides)
    return members


def _read_committee_snapshot(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return finance_committee_members(
                parse_committee_snapshot_text(handle.read())
            )
    except OSError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): committee snapshot unreadable at "
            + path
            + ": "
            + str(exc)
        ) from exc


def _load_prior(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): prior JSON unreadable: " + str(exc)
        ) from exc
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, dict):
        raise IngestError("LOOK REFUSED (not consumed): prior members map absent")
    if len(members) != PRIOR_MEMBER_COUNT:
        raise IngestError(
            "LOOK REFUSED (not consumed): prior member count drift "
            f"({len(members)} != {PRIOR_MEMBER_COUNT})"
        )
    return members


def _load_legislators(path):
    required = {"bioguide_id", "party", "type"}
    output = {}
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): legislators.csv unreadable: " + str(exc)
        ) from exc
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise IngestError(
                "LOOK REFUSED (not consumed): legislators.csv header mismatch"
            )
        for row_number, row in enumerate(reader, start=2):
            bioguide = (row.get("bioguide_id") or "").strip()
            party = (row.get("party") or "").strip()
            chamber = (row.get("type") or "").strip()
            if not re.fullmatch(r"[A-Z][0-9]{6}", bioguide):
                raise IngestError(
                    f"legislators.csv invalid bioguide at row {row_number}"
                )
            if bioguide in output:
                raise IngestError(
                    f"legislators.csv duplicate bioguide {bioguide}"
                )
            if not party or chamber not in {"rep", "sen"}:
                raise IngestError(
                    f"legislators.csv invalid party/type at row {row_number}"
                )
            output[bioguide] = {
                "party_name": party,
                "party": 1.0 if party == "Republican" else 0.0,
                "chamber": 1.0 if chamber == "sen" else 0.0,
            }
    return output


def _cycle_for_year(year):
    for cycle, cycle_years in CYCLE_YEARS.items():
        if year in cycle_years:
            return cycle
    raise IngestError(f"unsupported analysis year {year}")


def _load_network(paths, pins, collect_attention):
    lob_to_industry = {
        lob_id: industry
        for industry in INDUSTRIES
        for lob_id in CROSSWALK[industry].values()
    }
    attention = {stratum: {} for stratum in STRATA}
    active = {stratum: set() for stratum in STRATA}
    rows_read = {}
    for year in YEARS:
        path = paths[year]
        filename = os.path.basename(path)
        rows_read[year] = 0
        seen = set()
        try:
            handle = open(path, "r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise IngestError(
                "LOOK REFUSED (not consumed): network unreadable at "
                + path
                + ": "
                + str(exc)
            ) from exc
        with handle:
            reader = csv.reader(handle)
            if next(reader, None) != NETWORK_HEADER:
                raise IngestError(
                    "LOOK REFUSED (not consumed): network header mismatch in "
                    + filename
                )
            for row_number, source in enumerate(reader, start=2):
                rows_read[year] += 1
                if len(source) != 4 or source[2].strip() != str(year):
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): malformed network row {row_number} in {filename}"
                    )
                lob_id = source[0].strip()
                bioguide = source[1].strip()
                try:
                    n_bills = int(source[3])
                except ValueError as exc:
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): invalid n_bills at row {row_number} in {filename}"
                    ) from exc
                if n_bills < 0 or not re.fullmatch(r"[A-Z][0-9]{6}", bioguide):
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): invalid network row {row_number} in {filename}"
                    )
                key = (lob_id, bioguide)
                if key in seen:
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): duplicate network dyad at row {row_number} in {filename}"
                    )
                seen.add(key)
                industry = lob_to_industry.get(lob_id)
                if industry is None:
                    continue
                stratum = (industry, _cycle_for_year(year))
                active[stratum].add(lob_id)
                if collect_attention:
                    values = attention[stratum]
                    values[bioguide] = values.get(bioguide, 0) + n_bills
        if rows_read[year] != pins[filename]["rows"]:
            raise IngestError(
                f"LOOK REFUSED (not consumed): row count drift for {filename}"
            )
    return {"attention": attention, "active_lob_ids": active, "rows": rows_read}


def _load_totals(paths, pins):
    by_cycle = {cycle: {} for cycle in CYCLES}
    rows_read = {}
    for year in YEARS:
        path = paths[year]
        filename = os.path.basename(path)
        rows_read[year] = 0
        seen = set()
        try:
            handle = open(path, "r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise IngestError(
                "LOOK REFUSED (not consumed): totals unreadable at " + path
            ) from exc
        with handle:
            reader = csv.reader(handle)
            if next(reader, None) != TOTALS_HEADER:
                raise IngestError(
                    "LOOK REFUSED (not consumed): totals header mismatch in "
                    + filename
                )
            for row_number, source in enumerate(reader, start=2):
                rows_read[year] += 1
                if len(source) != 2:
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): malformed totals row {row_number} in {filename}"
                    )
                bioguide = source[0].strip()
                try:
                    value = int(source[1])
                except ValueError as exc:
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): invalid total at row {row_number} in {filename}"
                    ) from exc
                if bioguide in seen or value < 0:
                    raise IngestError(
                        f"LOOK REFUSED (not consumed): duplicate/negative totals row {row_number} in {filename}"
                    )
                seen.add(bioguide)
                cycle_values = by_cycle[_cycle_for_year(year)]
                cycle_values[bioguide] = cycle_values.get(bioguide, 0) + value
        if rows_read[year] != pins[filename]["rows"]:
            raise IngestError(
                f"LOOK REFUSED (not consumed): row count drift for {filename}"
            )
    return by_cycle


def _expected_hashes(paths, pins):
    expected = {
        paths["prior"]: PRIOR_SHA256,
        paths["legislators"]: LEGISLATORS_SHA256,
    }
    for kind in ("strict", "generous", "totals"):
        for year in YEARS:
            path = paths[kind][year]
            expected[path] = pins["csv"][os.path.basename(path)]["sha256"]
    for congress in (118, 119):
        pin = pins["committee"][congress]
        path = os.path.join(paths["committee_dir"], pin["filename"])
        expected[path] = pin["sha256"]
    return expected


def _gated_sources(collect_real_y):
    _verify_crosswalk()
    prereg_text = _read_prereg()
    pins = _collection_record_pins(prereg_text)
    paths = _input_paths()
    expected = _expected_hashes(paths, pins)
    for path, digest in expected.items():
        if not os.path.isfile(path):
            raise IngestError(
                "LOOK REFUSED (not consumed): pinned input absent at " + path
            )
        if _sha256(path) != digest:
            raise IngestError(
                "LOOK REFUSED (not consumed): SHA-256 drift for "
                + os.path.basename(path)
            )

    members = _load_prior(paths["prior"])
    legislators = _load_legislators(paths["legislators"])
    totals = _load_totals(paths["totals"], pins["csv"])
    strict = _load_network(paths["strict"], pins["csv"], collect_real_y)
    generous = _load_network(
        paths["generous"], pins["csv"], collect_real_y
    )
    committee = {}
    for cycle in CYCLES:
        congress = CYCLE_CONGRESS[cycle]
        snapshot_path = os.path.join(
            paths["committee_dir"], pins["committee"][congress]["filename"]
        )
        committee[cycle] = _read_committee_snapshot(snapshot_path)

    for path, digest in expected.items():
        if _sha256(path) != digest:
            raise IngestError(
                "LOOK REFUSED (not consumed): input drift between hash and read for "
                + os.path.basename(path)
            )
    return {
        "members": members,
        "legislators": legislators,
        "totals": totals,
        "strict": strict,
        "generous": generous,
        "committee": committee,
        "harvest": pins["harvest"],
        "paths": paths,
    }


def _status():
    present = os.path.isfile(PREREG_PATH)
    locked = False
    consumed = False
    collection = "ABSENT"
    fingerprints = "UNAVAILABLE"
    if present:
        try:
            with open(PREREG_PATH, "r", encoding="utf-8") as handle:
                text = handle.read()
            locked = re.search(r"(?m)^STATUS: LOCKED\s*$", text) is not None
            consumed = bool(
                re.search(r"(?:^|\n)## RESULT-BRIDGE3(?:\n|$)", text)
            )
            pins = _collection_record_pins(text)
            collection = "PARSEABLE"
            paths = _input_paths()
            expected = _expected_hashes(paths, pins)
            if any(not os.path.isfile(path) for path in expected):
                fingerprints = "FILE ABSENT"
            else:
                fingerprints = (
                    "MATCH"
                    if all(_sha256(path) == digest for path, digest in expected.items())
                    else "DRIFT"
                )
        except (OSError, IngestError):
            collection = "MALFORMED OR UNREADABLE"
    print("FEC x LDA BRIDGE3 preregistered look status")
    print("  prereg present: " + ("YES" if present else "NO"))
    print("  prereg locked: " + ("LOCKED" if locked else "NOT LOCKED"))
    print("  RESULT-BRIDGE3 consumed: " + ("YES" if consumed else "NO"))
    print("  collection record: " + collection)
    print("  pinned input fingerprints: " + fingerprints)
    print("  real pairing: NOT CONSTRUCTED")
    return 0


def _member_x(member, industry, cycle):
    try:
        value = member["associated_by_industry"][industry][cycle][
            "assoc_relational_used"
        ]
        number = float(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestError(
            f"prior member has invalid assoc_relational_used for {industry} {cycle}"
        ) from exc
    if not math.isfinite(number):
        raise IngestError(
            f"prior member has non-finite assoc_relational_used for {industry} {cycle}"
        )
    return number


def fractional_ranks(values):
    """Average-tie fractional ranks (r - 0.5) / n."""
    if not values:
        raise IngestError("fractional ranks received an empty vector")
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        average = ((start + 1) + stop) / 2.0
        fractional = (average - 0.5) / len(values)
        for position in range(start, stop):
            ranks[order[position]] = fractional
        start = stop
    return ranks


def volume_within_party_fractional(records):
    output = [None] * len(records)
    cells = collections.defaultdict(list)
    for index, record in enumerate(records):
        cells[(record["industry"], record["cycle"], record["party_name"])].append(
            index
        )
    for indices in cells.values():
        values = [records[index]["volume_raw"] for index in indices]
        ranked = fractional_ranks(values)
        for index, value in zip(indices, ranked):
            output[index] = value
    return output


def _entry_status(sources):
    nonzero_harvest = {
        lob_id for lob_id, count in sources["harvest"].items() if count > 0
    }
    status = {}
    entering = []
    for industry, cycle in STRATA:
        active = sources["strict"]["active_lob_ids"][(industry, cycle)]
        active_count = len(active & nonzero_harvest)
        x_positive = sum(
            1
            for member in sources["members"].values()
            if member.get("in_roster")
            and _member_x(member, industry, cycle) > 0
        )
        if x_positive != EXPECTED_X_POSITIVE[(industry, cycle)]:
            raise IngestError(
                "LOOK REFUSED (not consumed): pinned X>0 count drift for "
                f"{industry} {cycle} ({x_positive} != "
                f"{EXPECTED_X_POSITIVE[(industry, cycle)]})"
            )
        entered = active_count >= 3 and x_positive >= 1
        reasons = []
        if active_count < 3:
            reasons.append("active_nonzero_harvest_orgs<3")
        if x_positive < 1:
            reasons.append("no X>0 member")
        status[(industry, cycle)] = {
            "entered": entered,
            "active_orgs": active_count,
            "x_positive_members": x_positive,
            "reason": ", ".join(reasons),
        }
        if entered:
            entering.append((industry, cycle))
    return status, entering


def _build_records(sources, entering, network_kind, include_real_y):
    records = []
    attention = sources[network_kind]["attention"]
    for industry, cycle in entering:
        for bioguide in sorted(sources["members"]):
            member = sources["members"][bioguide]
            if not member.get("in_roster"):
                continue
            x_value = _member_x(member, industry, cycle)
            if x_value <= 0:
                continue
            legislator = sources["legislators"].get(bioguide)
            if legislator is None:
                raise IngestError(
                    "LOOK REFUSED (not consumed): roster member absent from legislators.csv: "
                    + bioguide
                )
            records.append(
                {
                    "industry": industry,
                    "cycle": cycle,
                    "member": bioguide,
                    "x": x_value,
                    "y": float(
                        attention[(industry, cycle)].get(bioguide, 0)
                        if include_real_y
                        else 0
                    ),
                    "party_name": legislator["party_name"],
                    "party": legislator["party"],
                    "chamber": legislator["chamber"],
                    "committee": (
                        1.0
                        if bioguide in sources["committee"][cycle]
                        else 0.0
                    ),
                    "volume_raw": float(
                        sources["totals"][cycle].get(bioguide, 0)
                    ),
                }
            )
    volumes = volume_within_party_fractional(records)
    for record, volume in zip(records, volumes):
        record["volume"] = volume
    return records


def _member_blocked_y_permutation(records, rng, y_field="y"):
    """One locked member-blocked shuffle, returned in record order."""
    output = [None] * len(records)
    by_industry = collections.defaultdict(dict)
    for index, record in enumerate(records):
        by_industry[record["industry"]].setdefault(record["cycle"], []).append(
            (index, record)
        )
    for industry in sorted(by_industry):
        union = sorted(
            {
                record["member"]
                for rows in by_industry[industry].values()
                for _index, record in rows
            }
        )
        sources = list(union)
        rng.shuffle(sources)
        mapping = dict(zip(union, sources))
        for cycle in sorted(by_industry[industry]):
            rows = sorted(
                by_industry[industry][cycle], key=lambda item: item[1]["member"]
            )
            value_by_member = {
                record["member"]: record[y_field] for _index, record in rows
            }
            used = set()
            leftovers = []
            for index, record in rows:
                source = mapping[record["member"]]
                if source in value_by_member:
                    output[index] = value_by_member[source]
                    used.add(source)
                else:
                    leftovers.append((index, record["member"]))
            remaining = sorted(set(value_by_member) - used)
            rng.shuffle(remaining)
            for (index, _target), source in zip(leftovers, remaining):
                output[index] = value_by_member[source]
    if any(value is None for value in output):
        raise DefectError("member-blocked shuffle left an unassigned position")
    return output


def _pearson_one(left, right):
    if len(left) != len(right) or not left:
        raise IngestError("statistic received empty or unequal vectors")
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right)
    )
    left_ss = sum((value - mean_left) ** 2 for value in left)
    right_ss = sum((value - mean_right) ** 2 for value in right)
    if left_ss == 0.0 or right_ss == 0.0:
        return 0.0
    return numerator / math.sqrt(left_ss * right_ss)


def _pivoted_qr_basis(matrix, tolerance=RANK_TOLERANCE):
    columns = [
        [float(matrix[row][column]) for row in range(len(matrix))]
        for column in range(len(matrix[0]))
    ]
    residuals = [list(column) for column in columns]
    remaining = list(range(len(columns)))
    basis = []
    initial = max(math.sqrt(sum(value * value for value in col)) for col in columns)
    while remaining:
        pivot = max(
            remaining,
            key=lambda index: sum(value * value for value in residuals[index]),
        )
        norm = math.sqrt(sum(value * value for value in residuals[pivot]))
        if norm <= tolerance * initial:
            break
        vector = [value / norm for value in residuals[pivot]]
        basis.append(vector)
        remaining.remove(pivot)
        for index in remaining:
            coefficient = sum(
                a * b for a, b in zip(vector, residuals[index])
            )
            residuals[index] = [
                value - coefficient * direction
                for value, direction in zip(residuals[index], vector)
            ]
    return basis


def _qr_residual(values, matrix):
    basis = _pivoted_qr_basis(matrix)
    projected = [0.0] * len(values)
    for vector in basis:
        coefficient = sum(a * b for a, b in zip(vector, values))
        projected = [
            current + coefficient * direction
            for current, direction in zip(projected, vector)
        ]
    return [value - fitted for value, fitted in zip(values, projected)]


def _design(rows, names):
    return [[1.0] + [float(row[name]) for name in names] for row in rows]


def statistics_implementation_one(records, rho_only=False, full=True):
    """Fractional ranks, exact blocked test, and pivoted-QR projection."""

    def average_fractional(values):
        ordered = sorted(range(len(values)), key=lambda index: values[index])
        result = [0.0] * len(values)
        first = 0
        while first < len(ordered):
            last = first + 1
            while last < len(ordered) and values[ordered[last]] == values[ordered[first]]:
                last += 1
            rank = ((first + 1 + last) / 2.0 - 0.5) / len(values)
            for position in range(first, last):
                result[ordered[position]] = rank
            first = last
        return result

    groups = {}
    for stratum in sorted({(row["industry"], row["cycle"]) for row in records}):
        rows = sorted(
            [row for row in records if (row["industry"], row["cycle"]) == stratum],
            key=lambda row: row["member"],
        )
        x_fractional = average_fractional([row["x"] for row in rows])
        y_fractional = average_fractional([row["y"] for row in rows])
        groups[stratum] = {
            "rows": rows,
            "members": [row["member"] for row in rows],
            "x": x_fractional,
            "y": y_fractional,
            "x_integer": [int(round(2 * len(rows) * value)) for value in x_fractional],
            "y_integer": [int(round(2 * len(rows) * value)) for value in y_fractional],
        }
    pooled_rows = []
    pooled_x = []
    pooled_y = []
    for stratum in sorted(groups):
        pooled_rows.extend(groups[stratum]["rows"])
        pooled_x.extend(groups[stratum]["x"])
        pooled_y.extend(groups[stratum]["y"])
    rho = _pearson_one(pooled_x, pooled_y)
    if rho_only:
        return {"rho": rho}

    common = math.lcm(*(len(group["rows"]) ** 2 for group in groups.values()))

    def score(y_by_stratum):
        total = 0
        for stratum, group in groups.items():
            n = len(group["rows"])
            multiplier = common // (n * n)
            for x_value, y_value in zip(group["x_integer"], y_by_stratum[stratum]):
                total += (x_value - n) * (y_value - n) * multiplier
        return total

    observed_score = score(
        {stratum: group["y_integer"] for stratum, group in groups.items()}
    )
    observed_by_stratum = {
        stratum: sum(
            (x_value - len(group["rows"]))
            * (y_value - len(group["rows"]))
            for x_value, y_value in zip(group["x_integer"], group["y_integer"])
        )
        for stratum, group in groups.items()
    }
    stratum_exceed = {stratum: 0 for stratum in groups}
    generator = random.Random(PERMUTATION_SEED)
    exceed = 0
    industries = sorted({stratum[0] for stratum in groups})
    for _draw in range(PERMUTATIONS):
        permuted = {}
        for industry in industries:
            industry_strata = [key for key in sorted(groups) if key[0] == industry]
            union = sorted(
                {member for key in industry_strata for member in groups[key]["members"]}
            )
            shuffled = list(union)
            generator.shuffle(shuffled)
            mapping = dict(zip(union, shuffled))
            for key in industry_strata:
                group = groups[key]
                values = dict(zip(group["members"], group["y_integer"]))
                assigned = {}
                used = set()
                leftovers = []
                for target in group["members"]:
                    source = mapping[target]
                    if source in values:
                        assigned[target] = values[source]
                        used.add(source)
                    else:
                        leftovers.append(target)
                remaining = sorted(set(values) - used)
                generator.shuffle(remaining)
                for target, source in zip(leftovers, remaining):
                    assigned[target] = values[source]
                permuted[key] = [assigned[member] for member in group["members"]]
        if score(permuted) >= observed_score:
            exceed += 1
        if full:
            for stratum, group in groups.items():
                candidate = sum(
                    (x_value - len(group["rows"]))
                    * (y_value - len(group["rows"]))
                    for x_value, y_value in zip(
                        group["x_integer"], permuted[stratum]
                    )
                )
                if candidate >= observed_by_stratum[stratum]:
                    stratum_exceed[stratum] += 1
    p_value = (exceed + 1) / (PERMUTATIONS + 1)

    def partial(names):
        matrix = _design(pooled_rows, names)
        return _pearson_one(
            _qr_residual(pooled_x, matrix), _qr_residual(pooled_y, matrix)
        )

    matrix = _design(pooled_rows, CONTROL_NAMES)
    result = {
        "n": len(records),
        "rho": rho,
        "p": p_value,
        "partial": partial(CONTROL_NAMES),
        "condition_number": float(np.linalg.cond(np.asarray(matrix, dtype=float))),
    }
    if not full:
        return result
    result["single_partials"] = {
        name: partial((name,)) for name in CONTROL_NAMES
    }
    result["three_control_partial"] = partial(("party", "chamber", "volume"))
    per_stratum = {}
    for stratum, group in groups.items():
        one_rho = _pearson_one(group["x"], group["y"])
        per_stratum[stratum[0] + "|" + stratum[1]] = {
            "n": len(group["rows"]),
            "rho": one_rho,
            "p": (stratum_exceed[stratum] + 1) / (PERMUTATIONS + 1),
        }
    result["per_stratum"] = per_stratum
    cycle_x = []
    cycle_y = []
    for key in sorted(groups):
        if key[1] == "2025-2026":
            cycle_x.extend(groups[key]["x"])
            cycle_y.extend(groups[key]["y"])
    result["rho_2025_2026"] = _pearson_one(cycle_x, cycle_y) if cycle_x else 0.0
    return result


def statistics_implementation_two(records, rho_only=False, full=True):
    """Independent count-rank derivation with SVD pseudo-inverse projection."""

    def count_fractional(values):
        counts = collections.Counter(values)
        below = 0
        lookup = {}
        integer_lookup = {}
        for value in sorted(counts):
            count = counts[value]
            lookup[value] = (below + count / 2.0) / len(values)
            integer_lookup[value] = 2 * below + count
            below += count
        return (
            [lookup[value] for value in values],
            [integer_lookup[value] for value in values],
        )

    def product_moment(left, right):
        if len(left) != len(right) or not left:
            raise IngestError("re-derivation received empty or unequal vectors")
        count = len(left)
        left_sum = sum(left)
        right_sum = sum(right)
        numerator = count * sum(a * b for a, b in zip(left, right)) - left_sum * right_sum
        left_term = count * sum(value * value for value in left) - left_sum * left_sum
        right_term = count * sum(value * value for value in right) - right_sum * right_sum
        if left_term == 0.0 or right_term == 0.0:
            return 0.0
        return numerator / math.sqrt(left_term * right_term)

    strata = {}
    keys = sorted({(row["industry"], row["cycle"]) for row in records})
    for key in keys:
        rows = sorted(
            (row for row in records if (row["industry"], row["cycle"]) == key),
            key=lambda row: row["member"],
        )
        x_fractional, x_integer = count_fractional([row["x"] for row in rows])
        y_fractional, y_integer = count_fractional([row["y"] for row in rows])
        strata[key] = {
            "rows": rows,
            "members": [row["member"] for row in rows],
            "x": x_fractional,
            "y": y_fractional,
            "x_integer": x_integer,
            "y_integer": y_integer,
        }
    all_rows = []
    x_values = []
    y_values = []
    for key in keys:
        all_rows += strata[key]["rows"]
        x_values += strata[key]["x"]
        y_values += strata[key]["y"]
    rho = product_moment(x_values, y_values)
    if rho_only:
        return {"rho": rho}

    common_denominator = math.lcm(
        *(len(strata[key]["rows"]) ** 2 for key in keys)
    )

    def integer_statistic(assignments):
        value = 0
        for key in keys:
            group = strata[key]
            size = len(group["rows"])
            weight = common_denominator // (size * size)
            for x_item, y_item in zip(group["x_integer"], assignments[key]):
                value += (x_item - size) * (y_item - size) * weight
        return value

    observed = integer_statistic(
        {key: list(strata[key]["y_integer"]) for key in keys}
    )
    stratum_observed = {
        key: sum(
            (x_item - len(strata[key]["rows"]))
            * (y_item - len(strata[key]["rows"]))
            for x_item, y_item in zip(
                strata[key]["x_integer"], strata[key]["y_integer"]
            )
        )
        for key in keys
    }
    stratum_at_least = {key: 0 for key in keys}
    rng = random.Random(PERMUTATION_SEED)
    at_least = 0
    for _draw in range(PERMUTATIONS):
        assignments = {}
        for industry in sorted({key[0] for key in keys}):
            relevant = [key for key in keys if key[0] == industry]
            all_members = sorted(
                {member for key in relevant for member in strata[key]["members"]}
            )
            source_members = list(all_members)
            rng.shuffle(source_members)
            destination_to_source = dict(zip(all_members, source_members))
            for key in relevant:
                group = strata[key]
                values = dict(zip(group["members"], group["y_integer"]))
                result_by_member = {}
                consumed = set()
                unmatched = []
                for destination in group["members"]:
                    source = destination_to_source[destination]
                    if source in values:
                        result_by_member[destination] = values[source]
                        consumed.add(source)
                    else:
                        unmatched.append(destination)
                available = sorted(set(values) - consumed)
                rng.shuffle(available)
                for destination, source in zip(unmatched, available):
                    result_by_member[destination] = values[source]
                assignments[key] = [
                    result_by_member[member] for member in group["members"]
                ]
        if integer_statistic(assignments) >= observed:
            at_least += 1
        if full:
            for key in keys:
                size = len(strata[key]["rows"])
                candidate = sum(
                    (x_item - size) * (y_item - size)
                    for x_item, y_item in zip(
                        strata[key]["x_integer"], assignments[key]
                    )
                )
                if candidate >= stratum_observed[key]:
                    stratum_at_least[key] += 1
    permutation_p = (at_least + 1) / (PERMUTATIONS + 1)

    def svd_partial(names):
        design = np.asarray(_design(all_rows, names), dtype=float)
        x_array = np.asarray(x_values, dtype=float)
        y_array = np.asarray(y_values, dtype=float)
        inverse = np.linalg.pinv(design, rcond=RANK_TOLERANCE)
        x_residual = x_array - design @ (inverse @ x_array)
        y_residual = y_array - design @ (inverse @ y_array)
        return product_moment(x_residual.tolist(), y_residual.tolist())

    full_design = np.asarray(_design(all_rows, CONTROL_NAMES), dtype=float)
    output = {
        "n": len(records),
        "rho": rho,
        "p": permutation_p,
        "partial": svd_partial(CONTROL_NAMES),
        "condition_number": float(np.linalg.cond(full_design)),
    }
    if not full:
        return output
    output["single_partials"] = {
        name: svd_partial((name,)) for name in CONTROL_NAMES
    }
    output["three_control_partial"] = svd_partial(
        ("party", "chamber", "volume")
    )
    per_stratum = {}
    for key in keys:
        group = strata[key]
        size = len(group["rows"])
        stratum_rho = product_moment(group["x"], group["y"])
        per_stratum[key[0] + "|" + key[1]] = {
            "n": size,
            "rho": stratum_rho,
            "p": (stratum_at_least[key] + 1) / (PERMUTATIONS + 1),
        }
    output["per_stratum"] = per_stratum
    later_x = []
    later_y = []
    for key in keys:
        if key[1] == "2025-2026":
            later_x += strata[key]["x"]
            later_y += strata[key]["y"]
    output["rho_2025_2026"] = (
        product_moment(later_x, later_y) if later_x else 0.0
    )
    return output


def _assert_agreement(first, second, label):
    def compare(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            if set(left) != set(right):
                raise DefectError(path + " key mismatch")
            for key in sorted(left):
                compare(left[key], right[key], path + "." + str(key))
            return
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if math.isinf(float(left)) and math.isinf(float(right)):
                return
            if not math.isfinite(float(left)) or not math.isfinite(float(right)):
                raise DefectError(path + " non-finite disagreement")
            if path.endswith(".p"):
                if left != right:
                    raise DefectError(path + " exact p mismatch")
            elif abs(float(left) - float(right)) > 1e-9:
                raise DefectError(path + " differs: " + repr(left) + " vs " + repr(right))
            return
        if left != right:
            raise DefectError(path + " differs")

    compare(first, second, label)


def _standardize(values):
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    if variance == 0.0:
        raise IngestError("battery vector has zero variance")
    scale = math.sqrt(variance)
    return [(value - mean) / scale for value in values]


def _battery_components(records):
    """Return ordered margins, z(X ranks), and z(fitted X ranks)."""
    ordered = []
    x_fractional = []
    for stratum in sorted({(row["industry"], row["cycle"]) for row in records}):
        rows = sorted(
            [row for row in records if (row["industry"], row["cycle"]) == stratum],
            key=lambda row: row["member"],
        )
        ordered.extend(rows)
        x_fractional.extend(fractional_ranks([row["x"] for row in rows]))
    matrix = _design(ordered, CONTROL_NAMES)
    residual = _qr_residual(x_fractional, matrix)
    fitted = [value - error for value, error in zip(x_fractional, residual)]
    return {
        "records": ordered,
        "x_fractional": x_fractional,
        "x_fitted": fitted,
        "z_x": _standardize(x_fractional),
        "z_x_fitted": _standardize(fitted),
    }


def _synthetic_records(records, y_values):
    output = []
    for record, value in zip(records, y_values):
        copy = dict(record)
        copy["y"] = float(value)
        output.append(copy)
    return output


def _evaluate_synthetic(label, records):
    first = statistics_implementation_one(records, full=False)
    second = statistics_implementation_two(records, full=False)
    _assert_agreement(first, second, "battery." + label)
    return {"implementation_one": first, "implementation_two": second}


def _run_battery(base_records):
    components = _battery_components(base_records)
    ordered = components["records"]
    z_x = components["z_x"]
    z_fitted = components["z_x_fitted"]

    planted_rng = random.Random(424242)
    planted_epsilon = [planted_rng.gauss(0.0, 1.0) for _row in ordered]
    low = 0.5
    high = 8.0
    for _iteration in range(60):
        midpoint = (low + high) / 2.0
        values = [
            x_value + midpoint * error
            for x_value, error in zip(z_x, planted_epsilon)
        ]
        rho = statistics_implementation_one(
            _synthetic_records(ordered, values), rho_only=True
        )["rho"]
        if rho > 0.35:
            low = midpoint
        else:
            high = midpoint
    planted_lambda = (low + high) / 2.0
    planted_y = [
        x_value + planted_lambda * error
        for x_value, error in zip(z_x, planted_epsilon)
    ]
    planted_records = _synthetic_records(ordered, planted_y)
    planted = _evaluate_synthetic("planted", planted_records)

    null_y = _member_blocked_y_permutation(
        planted_records, random.Random(535353)
    )
    null = _evaluate_synthetic(
        "member_blocked_null", _synthetic_records(ordered, null_y)
    )

    confound_rng = random.Random(646464)
    confound_error = [confound_rng.gauss(0.0, 1.0) for _row in ordered]
    confound_y = [
        fitted + 0.15 * error
        for fitted, error in zip(z_fitted, confound_error)
    ]
    confound = _evaluate_synthetic(
        "confound_only", _synthetic_records(ordered, confound_y)
    )

    true_rng = random.Random(757575)
    true_error = [true_rng.gauss(0.0, 1.0) for _row in ordered]
    true_y = [
        x_value
        + planted_lambda * error
        + 0.75 * fitted
        + 0.15 * error
        for x_value, fitted, error in zip(z_x, z_fitted, true_error)
    ]
    true_confound = _evaluate_synthetic(
        "true_confound", _synthetic_records(ordered, true_y)
    )

    planted_value = planted["implementation_one"]
    null_value = null["implementation_one"]
    confound_value = confound["implementation_one"]
    true_value = true_confound["implementation_one"]
    planted_pass = (
        0.32 <= planted_value["rho"] <= 0.38
        and planted_value["rho"] >= 0.20
        and planted_value["p"] < 0.05
    )
    null_pass = not (
        null_value["rho"] >= 0.20 and null_value["p"] < 0.05
    )
    confound_vacuous = confound_value["rho"] < 0.20
    confound_pass = confound_value["partial"] < 0.10
    true_pass = (
        true_value["rho"] >= 0.20
        and true_value["p"] < 0.05
        and true_value["partial"] >= 0.10
    )
    passed = planted_pass and null_pass and confound_pass and true_pass
    return {
        "status": "PASS" if passed else "FAILURE",
        "lambda": planted_lambda,
        "planted": planted,
        "null": null,
        "confound_only": confound,
        "true_confound": true_confound,
        "confound_vacuous": confound_vacuous,
        "checks": {
            "planted": planted_pass,
            "null": null_pass,
            "confound_only": confound_pass,
            "true_confound": true_pass,
        },
    }


def _format_battery(battery):
    lines = [
        "## BATTERY-BRIDGE3",
        "- battery " + battery["status"],
        f"- planted lambda: {battery['lambda']:.12f}",
    ]
    specifications = (
        ("planted", "PLANTED seed 424242"),
        ("null", "MEMBER-BLOCKED NULL seed 535353"),
        ("confound_only", "CONFOUND-ONLY seed 646464"),
        ("true_confound", "TRUE+CONFOUND seed 757575"),
    )
    for key, label in specifications:
        first = battery[key]["implementation_one"]
        second = battery[key]["implementation_two"]
        suffix = ""
        if key == "confound_only" and battery["confound_vacuous"]:
            suffix = " (VACUOUS-PASS: raw pooled rho < +0.20)"
        lines.append(
            f"- {label}: check={'PASS' if battery['checks'][key] else 'FAILURE'}{suffix}; "
            f"impl1 rho={first['rho']:+.12f}, p={first['p']:.12f}, "
            f"partial={first['partial']:+.12f}; impl2 rho={second['rho']:+.12f}, "
            f"p={second['p']:.12f}, partial={second['partial']:+.12f}"
        )
    if battery["status"] == "FAILURE":
        lines.append(
            "- round RETIRED WITHOUT A LOOK; successor requires a fresh locked design"
        )
    else:
        lines.append("- look eligibility: battery admitted")
    return "\n".join(lines)


def _tier(result):
    if result["rho"] >= 0.40 and result["p"] < 0.01 and result["partial"] >= 0.20:
        return "STRONG"
    if result["rho"] >= 0.20 and result["p"] < 0.05 and result["partial"] >= 0.10:
        return "CORROBORATED"
    return "UNCORROBORATED"


def _format_result(
    first,
    second,
    generous_first,
    generous_second,
    battery,
    entry_status,
    harvest,
):
    tier = _tier(first)
    lines = [
        "## RESULT-BRIDGE3",
        "- date: " + datetime.date.today().isoformat(),
        f"- pooled N: {first['n']}",
        "- stratum entry status:",
    ]
    for industry, cycle in STRATA:
        item = entry_status[(industry, cycle)]
        if item["entered"]:
            key = industry + "|" + cycle
            lines.append(
                f"  - {industry} x {cycle}: entered; pairs={first['per_stratum'][key]['n']}; "
                f"active_orgs={item['active_orgs']}"
            )
        else:
            lines.append(
                f"  - {industry} x {cycle}: coverage-limited; {item['reason']}; "
                f"active_orgs={item['active_orgs']}"
            )
    zero_harvest = sorted(lob_id for lob_id, count in harvest.items() if count == 0)
    lines.extend(
        [
            "zero-harvest coverage-limited orgs: "
            + (",".join(zero_harvest) if zero_harvest else "NONE"),
            f"- pooled fractional-rank Spearman rho: {first['rho']:+.12f}",
            f"- exact member-blocked permutation p: {first['p']:.12f} "
            f"(K={PERMUTATIONS}, seed={PERMUTATION_SEED})",
            f"- four-control partial: {first['partial']:+.12f}",
            f"- design-matrix condition number: {first['condition_number']:.12g}",
            "- tier: " + tier,
            "- consequence (fixed wording): " + CONSEQUENCE_WORDING[tier],
            f"- implementation 1: rho={first['rho']:+.12f}, p={first['p']:.12f}, "
            f"partial={first['partial']:+.12f}",
            f"- implementation 2: rho={second['rho']:+.12f}, p={second['p']:.12f}, "
            f"partial={second['partial']:+.12f}",
            "- battery summary: " + battery["status"] + f"; lambda={battery['lambda']:.12f}",
        ]
    )
    bar_one = first["rho"] >= 0.20 and first["p"] < 0.05
    if (
        tier == "UNCORROBORATED"
        and bar_one
        and first["partial"] < 0.10
        and first["three_control_partial"] >= 0.10
    ):
        lines.append("- pre-declared overcontrol interpretation: " + OVERCONTROL_SENTENCE)
    lines.append("- secondaries (firewalled; ground nothing):")
    for industry, cycle in STRATA:
        key = industry + "|" + cycle
        if key not in first["per_stratum"]:
            lines.append(f"  - {industry} x {cycle}: NOT ENTERING")
            continue
        one = first["per_stratum"][key]
        two = second["per_stratum"][key]
        lines.append(
            f"  - {industry} x {cycle}: impl1 rho={one['rho']:+.12f}, "
            f"p={one['p']:.12f}; impl2 rho={two['rho']:+.12f}, p={two['p']:.12f}"
        )
    for name in CONTROL_NAMES:
        lines.append(
            f"  - {name}-only partial: impl1={first['single_partials'][name]:+.12f}; "
            f"impl2={second['single_partials'][name]:+.12f}"
        )
    lines.extend(
        [
            "  - three-control partial (party, chamber, volume; committee excluded): "
            f"impl1={first['three_control_partial']:+.12f}; "
            f"impl2={second['three_control_partial']:+.12f}",
            "  - 2025-2026-only rho: "
            f"impl1={first['rho_2025_2026']:+.12f}; "
            f"impl2={second['rho_2025_2026']:+.12f}",
            "  - generous-mode pooled rho: "
            f"impl1={generous_first['rho']:+.12f}; "
            f"impl2={generous_second['rho']:+.12f}",
            ROUND2_OMISSION,
        ]
    )
    return "\n".join(lines)


def _run(mode):
    collect_real_y = mode == "look"
    sources = _gated_sources(collect_real_y)
    entry_status, entering = _entry_status(sources)
    base_records = _build_records(sources, entering, "strict", False)
    entering_industries = {industry for industry, _cycle in entering}
    if len(base_records) < 60 or len(entering_industries) < 2:
        print(
            "VOID-FOR-COVERAGE: pooled pairs="
            + str(len(base_records))
            + ", distinct entering industries="
            + str(len(entering_industries))
            + "; look NOT consumed"
        )
        return 0

    battery = _run_battery(base_records)
    print(_format_battery(battery))
    if battery["status"] != "PASS":
        return 4
    if mode == "simulate":
        return 0

    primary = _build_records(sources, entering, "strict", True)
    first = statistics_implementation_one(primary)
    second = statistics_implementation_two(primary)
    _assert_agreement(first, second, "real_look")

    generous_records = _build_records(sources, entering, "generous", True)
    generous_first = statistics_implementation_one(
        generous_records, rho_only=True
    )
    generous_second = statistics_implementation_two(
        generous_records, rho_only=True
    )
    _assert_agreement(generous_first, generous_second, "generous_secondary")
    print()
    print(
        _format_result(
            first,
            second,
            generous_first,
            generous_second,
            battery,
            entry_status,
            sources["harvest"],
        )
    )
    return 0


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Locked FEC x LDA BRIDGE3 corroboration look",
        allow_abbrev=False,
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--simulate", action="store_true", help="battery only")
    modes.add_argument("--look", action="store_true", help="consume the one look")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(
        sys.argv[1:] if argv is None else list(argv)
    )
    if not args.simulate and not args.look:
        return _status()
    mode = "look" if args.look else "simulate"
    try:
        return _run(mode)
    except DefectError as exc:
        print(
            "DEFECT HALT: "
            + str(exc)
            + "; DEFECT HALT does not consume the look"
        )
        return 3
    except IngestError as exc:
        print("ERROR: " + str(exc))
        return 2
    except Exception as exc:
        print("ERROR: FEC x LDA BRIDGE3 look failed: " + str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
