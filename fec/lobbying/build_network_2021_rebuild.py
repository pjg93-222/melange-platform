# -*- coding: utf-8 -*-
"""Build multi-year Senate LDA legislator networks.

``--full-scope`` builds additive, sweep-sourced networks for one invariant
union of classified finance clients, pinned associations, pinned companies,
and fixed-pattern finance clients observed in the 2021--2026 sweeps.  It never
selects a legacy output path.

``--emit-bill-index`` keeps the same full-scope resolver and corpus basis but
emits its organization-bill identities, exact channel provenance, and an exact
full outer join to the authoritative legislation membership repository.

Each lobbied bill credits every legislator who sponsored or cosponsored it.
Generous outputs retain ambiguous-title fan-out; strict outputs require a
same-segment formal citation to disambiguate titles mapping to multiple bills.
Bill identities include Congress. Each formal citation binds to the first
following ``of 20XX`` suffix in its semicolon segment; older suffixes resolve
to the original Congress and never to a filing-year same-number bill.
Known limitation: slash-joined citations of the form "HR 4368/S 2131" parse only the left-hand side; measured cost is 2 of 376 distinct bills (0.5%) on the 2023-24 association corpus.

Amendment policy is pinned to a union-of-versions basis. Bills are distinct
per organization, so original/amendment overlap cannot double-count a dyad;
divergence from a latest-version-text basis remains an adjudication item.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import collections
import csv
import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from lobbying import client_identity_harvest as identity_harvest
from lobbying import sector_spend as sector_authority

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


EXPECTED_WORKLIST_RECORDS = 117
MIN_FILING_YEAR = 2021
MAX_FILING_YEAR = 2026
YEAR_TO_CONGRESS = {
    2021: 117,
    2022: 117,
    2023: 118,
    2024: 118,
    2025: 119,
    2026: 119,
}
CONGRESS_FIRST_YEAR = {
    115: 2017,
    116: 2019,
    117: 2021,
    118: 2023,
    119: 2025,
}
NETWORK_FIELDS = ["lob_id", "bioguide_id", "year", "n_bills"]
QUARTERLY_NETWORK_FIELDS = [
    "lob_id",
    "bioguide_id",
    "year",
    "quarter",
    "n_bills",
]
TOTAL_FIELDS = ["bioguide", "total_n_bills"]
FULLSCOPE_ROSTER_FIELDS = [
    "lob_id_or_client_id",
    "client_name",
    "source_flags",
    "first_year_seen",
    "last_year_seen",
]
BILL_INDEX_FIELDS = [
    "congress",
    "bill_type",
    "bill_number",
    "bill_key",
    "lob_id",
    "client_name",
    "org_sector",
    "n_filings",
    "n_activities",
    "first_filing_period",
    "last_filing_period",
    "channel",
]
BILL_REPOSITORY_JOIN_FIELDS = [
    "bill_key",
    "canonical_key",
    "congress",
    "bill_type",
    "bill_number",
    "join_status",
    "repository_present",
    "lobbying_present",
    "lobbying_years",
    "n_lobbying_orgs",
    "n_lobbying_rows",
    "n_filings",
    "n_activities",
    "lobbying_channels",
    "category_id",
    "integration_state",
    "measure_weight",
    "audit_sections_json",
    "audit_bills_json",
    "audit_titles_json",
    "audit_n_cosponsors_json",
    "audit_congresses_json",
    "direction_categories_json",
    "directions_json",
    "direction_notes_json",
]
BILL_INDEX_HONESTY = (
    "Only approximately 28% of lobbying activities cite a bill number when "
    "pooled across the 2021–2026 window; this index covers the cited subset and "
    "is not the full lobbying footprint of any organization.",
    "An organization-bill row means the organization's filing named that "
    "bill. It is not evidence of a position on the bill; direction and marking "
    "come from bill_directions.csv, a separate human-adjudicated instrument.",
)
CANONICAL_BILL_TYPES = frozenset(
    {
        "hr",
        "s",
        "hjres",
        "sjres",
        "hconres",
        "sconres",
        "hres",
        "sres",
        "hamdt",
        "samdt",
    }
)
DISPLAY_BILL_PREFIXES = (
    ("H.CON.RES.", "hconres"),
    ("S.CON.RES.", "sconres"),
    ("H.J.RES.", "hjres"),
    ("S.J.RES.", "sjres"),
    ("H.AMDT.", "hamdt"),
    ("S.AMDT.", "samdt"),
    ("H.RES.", "hres"),
    ("S.RES.", "sres"),
    ("H.R.", "hr"),
    ("S.", "s"),
)
BRIDGE3_YEARS = frozenset({2023, 2024, 2025, 2026})
# This is a position-item assembly boundary, not a network-source boundary.
# 2023 position items use the UUID worklist; the network builder uses the
# separate pinned-22 boundary below and has used the sweep for 2023.
POSITION_ITEM_SWEEP_YEARS = frozenset({2024, 2025, 2026})
PINNED_22_SWEEP_NETWORK_YEARS = frozenset({2023, 2024, 2025, 2026})
FULLSCOPE_SIDES = frozenset({"credit_union", "bank", "financial_other"})
FULLSCOPE_SWEEP_YEARS = tuple(sorted(YEAR_TO_CONGRESS))
FULLSCOPE_STATE_AND_TERRITORY_NAMES = (
    "ALABAMA",
    "ALASKA",
    "ARIZONA",
    "ARKANSAS",
    "CALIFORNIA",
    "COLORADO",
    "CONNECTICUT",
    "DELAWARE",
    "DISTRICT OF COLUMBIA",
    "FLORIDA",
    "GEORGIA",
    "HAWAII",
    "IDAHO",
    "ILLINOIS",
    "INDIANA",
    "IOWA",
    "KANSAS",
    "KENTUCKY",
    "LOUISIANA",
    "MAINE",
    "MARYLAND",
    "MASSACHUSETTS",
    "MICHIGAN",
    "MINNESOTA",
    "MISSISSIPPI",
    "MISSOURI",
    "MONTANA",
    "NEBRASKA",
    "NEVADA",
    "NEW HAMPSHIRE",
    "NEW JERSEY",
    "NEW MEXICO",
    "NEW YORK",
    "NORTH CAROLINA",
    "NORTH DAKOTA",
    "OHIO",
    "OKLAHOMA",
    "OREGON",
    "PENNSYLVANIA",
    "PUERTO RICO",
    "RHODE ISLAND",
    "SOUTH CAROLINA",
    "SOUTH DAKOTA",
    "TENNESSEE",
    "TEXAS",
    "UTAH",
    "VERMONT",
    "VIRGIN ISLANDS",
    "VIRGINIA",
    "WASHINGTON",
    "WEST VIRGINIA",
    "WISCONSIN",
    "WYOMING",
)
# Applied to the already-normalized LDA client.name.  These expressions are
# deliberately fixed in source: additions are scope changes and therefore
# change CORPUS_BASIS.  State/territory leagues and CUSOs are standing credit-
# union-sector inclusions even when their names omit "CREDIT UNION".
FULLSCOPE_FINANCE_NAME_PATTERNS = (
    ("credit_union", r"\bCREDIT\s+UNIONS?\b", "credit_union"),
    ("cu_league", r"\bCU\s+LEAGUES?\b", "credit_union"),
    ("fcu_acronym", r"\bFCU\b", "credit_union"),
    (
        "cu_trade_acronym",
        r"\b(?:NAFCU|CUNA|NASCUS|NACUSO)\b",
        "credit_union",
    ),
    (
        "bankers_association",
        r"\bBANKERS\s+(?:ASSOCIATION|ASSN)\b",
        "bank",
    ),
    (
        "savings_institution",
        r"\bSAVINGS\s+INSTITUTIONS?\b",
        "bank",
    ),
    (
        "community_bank",
        r"\bCOMMUNITY\s+BANK(?:S|ING|ERS)?\b",
        "bank",
    ),
    ("bancorp", r"\bBANCORP(?:ORATION)?\b", "bank"),
    ("bancshares", r"\bBANCSHARES?\b", "bank"),
    ("federal_savings", r"\bFEDERAL\s+SAVINGS\b", "bank"),
    ("thrift", r"\bTHRIFTS?\b", "bank"),
    ("cuso", r"\b(?:CUSOS?|NACUSO)\b", "credit_union"),
    (
        "state_or_territory_league",
        r"\b(?:"
        + "|".join(
            re.escape(name) for name in FULLSCOPE_STATE_AND_TERRITORY_NAMES
        )
        + r")\b.*\b(?:CREDIT\s+UNIONS?|CU|FINANCIAL)\b.*\bLEAGUES?\b",
        "credit_union",
    ),
)
# Sweep-native sector channel (2026-08-05, Paul: organizations like Wintermute
# "should be included"): every sweep filing is also offered to the sector_spend
# roster authority (every roster sector, full include/exclude semantics), so a
# sector-classified organization absent from the LobbyView-era classification
# CSV still enters the full-scope union.  Roster edits are therefore scope
# changes and change CORPUS_BASIS on the next rebuild, exactly like edits to
# FULLSCOPE_FINANCE_NAME_PATTERNS itself.
FULLSCOPE_SECTOR_CHANNEL_PREFIX = "sector_roster:"
FULLSCOPE_SECTOR_CHANNEL_SIDES = {
    "credit_union": "credit_union",
    "bank": "bank",
    "crypto": "financial_other",
    "fintech": "financial_other",
    # Telecom (2026-10-07) is its OWN side, not financial_other: the full
    # board treats it as a separate industry.  FULLSCOPE_SIDES is untouched on
    # purpose - it only gates the frozen LobbyView-era classification CSV, which
    # has no telecom rows; telecom enters through this sector-roster channel.
    "telecom": "telecom",
}
EXPECTED_FULLSCOPE_ASSOCIATIONS = 22
EXPECTED_FULLSCOPE_PINNED_COMPANIES = 22
LEGACY_CORPUS_BASIS = "legacy:vintage-dependent-worklist-or-pinned-22-sweep"
FULLSCOPE_IDENTITY_RULE = {
    "primary": "harvested_global_client.id_with_name_plausibility_guard",
    "secondary": "exact_normalized_client.name",
    "combination": "union",
    "never_identifying": "client.client_id",
}
AMENDMENT_POLICY_NOTE = (
    "union-of-versions basis; distinct-per-org bill counting makes "
    "original/amendment overlap double-count impossible; divergence from a "
    "latest-version-text basis is an adjudication item"
)
UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
EXPECTED_LOB_IDS_2021 = frozenset(
    {
        "0f549ea5-9fdb-5cb9-9a9f-82682d4ef353",
        "18cf487e-9e89-52ec-93dc-d91de45c0b84",
        "1c3314d2-e20e-5484-8084-97e58c4eca81",
        "3f7a52db-9be0-5222-84bd-693a1ccc38ae",
        "4317e970-8485-55be-af60-d59762a1c8d7",
        "4f43f95f-f6f0-5b47-9f74-08d6b885ac55",
        "5f438304-3b42-5241-9821-874137fdc0ce",
        "67e35f81-afa7-5258-998e-7f08a3512e20",
        "9a33b530-6a10-52df-9d55-a2f4e0168316",
        "b849b9da-67fa-5eae-914b-92ac41c7ad31",
        "c3dc5b6d-38b1-5331-bec4-6cf081fa6cf1",
        "cb4ac22a-dd82-5a42-a289-83fd13ba07d0",
        "d039e7e5-f821-55d5-9e38-f93c0b71aecb",
        "f2f8dae7-b696-5e01-8067-31b357170ec2",
    }
)
BRIDGE3_CROSSWALK = {
    "credit_union": {
        "BOEING EMPLOYEES' CREDIT UNION": (
            "d039e7e5-f821-55d5-9e38-f93c0b71aecb"
        ),
        "NAVY FEDERAL CREDIT UNION": (
            "69c5a9e9-7d79-557e-8ff5-f07df772534d"
        ),
        "PENTAGON FEDERAL CREDIT UNION": (
            "c27f7c24-43ec-52f7-80d6-ebb74cc526b1"
        ),
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
        "SOFI TECHNOLOGIES, INC.": (
            "03b1f6cd-0546-59d2-97d3-9a31da3063b0"
        ),
        "STRIPE": "b849b9da-67fa-5eae-914b-92ac41c7ad31",
        "STRIPE, INC.": "cb4ac22a-dd82-5a42-a289-83fd13ba07d0",
    },
    "crypto": {
        "BAKKT HOLDINGS, INC.": (
            "dbdd9f97-b360-53f0-adc8-9a7e5e4c967c"
        ),
        "BAM TRADING SERVICES, INC./BINANCE.US": (
            "1c3314d2-e20e-5484-8084-97e58c4eca81"
        ),
        "BINANCE HOLDINGS LIMITED": (
            "75d2c2aa-fb94-5005-acff-1479c56d41b5"
        ),
        "COINBASE, INC.": "67e35f81-afa7-5258-998e-7f08a3512e20",
        "COINBASE GLOBAL, INC.": (
            "7ddb0913-d306-57d7-b42d-febd24a314cf"
        ),
        "FORIS DAX, INC. D/B/A CRYPTO.COM": (
            "6904853b-ba69-5493-85b0-6699c0f7e33b"
        ),
        "GEMINI TRUST COMPANY, LLC": (
            "4317e970-8485-55be-af60-d59762a1c8d7"
        ),
        "PAYWARD INC D/B/A KRAKEN": (
            "8b517298-a4d4-5e93-bf5a-86af84076c66"
        ),
    },
}
BRIDGE3_LOB_IDS = frozenset(
    lob_id
    for clients in BRIDGE3_CROSSWALK.values()
    for lob_id in clients.values()
)
ZIP_TYPES = (
    ("hr", "HR"),
    ("s", "S"),
    ("hjres", "HJRES"),
    ("sjres", "SJRES"),
    ("hconres", "HCONRES"),
    ("sconres", "SCONRES"),
    ("hres", "HRES"),
    ("sres", "SRES"),
)
TYPE_TOKENS = {token for _archive, token in ZIP_TYPES}
HISTORICAL_ZIP_TYPES = tuple(
    item for item in ZIP_TYPES if item[1] in {"HR", "S"}
)
SUPPORTED_BILL_TYPES_BY_CONGRESS = {
    115: frozenset(
        type_token for _archive, type_token in HISTORICAL_ZIP_TYPES
    ),
    116: frozenset(
        type_token for _archive, type_token in HISTORICAL_ZIP_TYPES
    ),
    117: frozenset(TYPE_TOKENS),
    118: frozenset(TYPE_TOKENS),
    119: frozenset(TYPE_TOKENS),
}

_START = r"(?<![A-Za-z0-9'’-])"
_NUMBER_END = r"(?![A-Za-z0-9])"
_FINAL_SEPARATOR = r"(?:\s*\.\s*|\s+)"
_RES = r"RES(?:OLUTION)?"
_BILL_START_GUARD_RE = re.compile(_START + r"$")
_S_CONTEXT_RE = re.compile(r"(?<![A-Za-z0-9])U(?:[.\s]*)$", re.IGNORECASE)
CONGRESS_CONTEXT_RE = re.compile(
    r"\b1(?:0\d|1[0-7])th\s+Congress\b", re.IGNORECASE
)


def _bill_pattern(type_body, flags=re.IGNORECASE):
    return re.compile(
        _START + type_body + r"(?P<number>\d+)" + _NUMBER_END,
        flags,
    )


def _run_into_title_pattern(type_body, flags=re.IGNORECASE):
    # Two title letters preserve the existing ``HR 50A`` rejection while
    # allowing the measured digit-to-title-word boundary.
    return re.compile(
        _START
        + type_body
        + r"(?P<number>\d{1,4})(?![0-9-])(?=[A-Za-z]{2})",
        flags,
    )


BILL_PATTERNS = (
    (
        "HJRES",
        _bill_pattern(
            r"H\s*\.?\s*J\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "SJRES",
        _bill_pattern(
            r"S\s*\.?\s*J\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        True,
    ),
    (
        "HCONRES",
        _bill_pattern(
            r"H\s*\.?\s*CON\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "SCONRES",
        _bill_pattern(
            r"S\s*\.?\s*CON\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        True,
    ),
    (
        "HRES",
        _bill_pattern(r"H\s*\.?\s*" + _RES + _FINAL_SEPARATOR),
        False,
    ),
    (
        "SRES",
        _bill_pattern(r"S\s*\.?\s*" + _RES + _FINAL_SEPARATOR),
        True,
    ),
    (
        "HR",
        _bill_pattern(
            r"(?:H\s*\.\s*R|H\s+R|HR(?=\s*\.))" + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "HR",
        _bill_pattern(r"HR(?:\s+|(?=\d))", flags=0),
        False,
    ),
    (
        "S",
        _bill_pattern(r"S\s*\.\s*"),
        True,
    ),
)

RUNS_INTO_TITLE_PATTERNS = (
    (
        "HJRES",
        _run_into_title_pattern(
            r"H\s*\.?\s*J\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "SJRES",
        _run_into_title_pattern(
            r"S\s*\.?\s*J\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        True,
    ),
    (
        "HCONRES",
        _run_into_title_pattern(
            r"H\s*\.?\s*CON\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "SCONRES",
        _run_into_title_pattern(
            r"S\s*\.?\s*CON\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        True,
    ),
    (
        "HRES",
        _run_into_title_pattern(
            r"H\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "SRES",
        _run_into_title_pattern(
            r"S\s*\.?\s*" + _RES + _FINAL_SEPARATOR
        ),
        True,
    ),
    (
        "HR",
        _run_into_title_pattern(
            r"(?:H\s*\.\s*R|H\s+R|HR(?=\s*\.))"
            + _FINAL_SEPARATOR
        ),
        False,
    ),
    (
        "HR",
        _run_into_title_pattern(r"HR(?:\s+|(?=\d))", flags=0),
        False,
    ),
    (
        "S",
        _run_into_title_pattern(r"S\s*\.\s*"),
        True,
    ),
)

# This scanner is intentionally broader than the accepted token.  It lets the
# audit surface record over-range strings such as ``S 99999`` and false starts
# such as the final ``s 7`` in ``SBA's 7(a)``.  Acceptance remains gated in
# _recover_dotless_bare_s_mentions below.
DOTLESS_BARE_S_SCAN_RE = re.compile(
    r"S\s+(?P<number>\d+)", re.IGNORECASE
)
FORMAL_CITING_CONTEXT_PATTERNS = (
    ("HR", _bill_pattern(r"H\s*\.\s*R\s*\.\s*"), False),
    ("HR", _bill_pattern(r"HR\s+", flags=0), False),
    ("S", _bill_pattern(r"S\s*\.\s*"), True),
)
DOTLESS_BARE_S_REJECTION_CONDITIONS = (
    "negative_lookbehind",
    "billstatus_existence",
    "citing_context_proof",
    "range_sanity",
)
DOTLESS_BARE_S_SAMPLE_LIMIT = 10
MECHANICAL_CITATION_RECOVERY_KINDS = (
    "runs_into_title",
)

# Continuations are interpreted only after a formal typed bill match.
CONTINUATION_RE = re.compile(
    r"\s*(?P<separator>,|;|&|\band\b)\s*"
    r"(?P<number>\d{1,4})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
THOUSANDS_GROUP_RE = re.compile(
    r"\s*,\s*(?P<number>\d{3})(?![A-Za-z0-9])"
)
NEXT_COMMA_GROUP_RE = re.compile(
    r"\s*,\s*\d{1,4}(?![A-Za-z0-9])"
)

# This deliberately recognizes formal-looking H./S. identifiers declined by
# exact parsing. Exact spans are removed below; the remainder is a verbatim
# diagnosis surface and is never normalized into the network.
BILL_LIKE_RE = re.compile(
    _START
    + r"(?:"
    + r"H\s*\.?\s*J\s*\.?\s*"
    + _RES
    + r"|S\s*\.?\s*J\s*\.?\s*"
    + _RES
    + r"|H\s*\.?\s*CON\s*\.?\s*"
    + _RES
    + r"|S\s*\.?\s*CON\s*\.?\s*"
    + _RES
    + r"|H\s*\.?\s*"
    + _RES
    + r"|S\s*\.?\s*"
    + _RES
    + r"|H\s*\.?\s*R|S"
    + r")"
    + r"(?:\s*(?:(?:NO\.?)|[.\-:#\u2010-\u2015]))*"
    + r"\s*\d(?:\d|,(?=\d))*"
    + r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
BARE_CONCATENATED_S1_RE = re.compile(r"^S1$", re.IGNORECASE)
TITLE_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
TITLE_YEAR_SUFFIX_RE = re.compile(r"\s+OF\s+202[1-6]$", re.IGNORECASE)
STALE_CITATION_YEAR_RE = re.compile(
    r"\bOF\s+(20\d{2})\b", re.IGNORECASE
)
TITLE_KIND_FRAGMENTS = (
    "OFFICIAL TITLE",
    "SHORT TITLE",
    "POPULAR TITLE",
)


class IngestError(Exception):
    """Expected rebuild failure that must exit 2."""


FILING_PERIOD_QUARTERS = {
    "q1": 1,
    "q2": 2,
    "q3": 3,
    "q4": 4,
    "h1": 2,
    "h2": 4,
    "first_quarter": 1,
    "second_quarter": 2,
    "third_quarter": 3,
    "fourth_quarter": 4,
    "mid_year": 2,
    "year_end": 4,
}


def filing_period_to_quarter(value):
    if not isinstance(value, str):
        raise IngestError(f"unrecognized filing period {value!r}")
    normalized = value.strip().lower()
    try:
        return FILING_PERIOD_QUARTERS[normalized]
    except KeyError as exc:
        raise IngestError(f"unrecognized filing period {value!r}") from exc


def dump_atomic(obj, path, **kwargs):
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(temp_path, "w", encoding="utf-8", newline='\r\n') as handle:
            json.dump(obj, handle, **kwargs)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _write_csv_atomic(path, fieldnames, rows):
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(temp_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def normalize_worklist_boolean(value, field, record_name):
    """Normalize only JSON booleans or the generator's exact string literals."""
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise IngestError(
        f"worklist record {record_name} field {field} must be a boolean "
        'or exact lowercase string "true"/"false"'
    )


def validate_report_uuid(value, record_name):
    if not isinstance(value, str) or UUID_RE.fullmatch(value) is None:
        raise IngestError(
            f"worklist record {record_name} field report_uuid is not a "
            "strict 8-4-4-4-12 hexadecimal UUID"
        )
    return value


def validate_filing_year(value, record_name):
    if isinstance(value, bool):
        filing_year = None
    elif isinstance(value, int):
        filing_year = value
    elif isinstance(value, str) and value in {
        str(year) for year in range(MIN_FILING_YEAR, MAX_FILING_YEAR + 1)
    }:
        filing_year = int(value)
    else:
        filing_year = None
    if filing_year is None or filing_year not in YEAR_TO_CONGRESS:
        raise IngestError(
            f"worklist record {record_name} field filing_year must be an "
            f"integer or exact four-digit string in "
            f"{MIN_FILING_YEAR}..{MAX_FILING_YEAR}"
        )
    return filing_year


def congress_for_year(filing_year):
    if filing_year not in YEAR_TO_CONGRESS:
        raise IngestError(f"unsupported filing year {filing_year!r}")
    return YEAR_TO_CONGRESS[filing_year]


def congress_first_year(filing_year):
    return CONGRESS_FIRST_YEAR[congress_for_year(filing_year)]


def congress_for_calendar_year(calendar_year):
    """Return the Congress whose two-year session contains calendar_year."""
    if not isinstance(calendar_year, int) or calendar_year < 1789:
        raise IngestError(
            f"cannot resolve Congress for calendar year {calendar_year!r}"
        )
    return ((calendar_year - 1789) // 2) + 1


def validate_lob_id_scope(records):
    observed = {
        record.get("lob_id")
        for record in records
        if isinstance(record, dict)
    }
    if observed != EXPECTED_LOB_IDS_2021:
        missing = sorted(EXPECTED_LOB_IDS_2021 - observed)
        unexpected = sorted(observed - EXPECTED_LOB_IDS_2021, key=str)
        raise IngestError(
            "worklist lob_id scope differs from pinned 14: "
            f"missing={missing}, unexpected={unexpected}"
        )
    return observed


def validate_2021_comparison_scope(records):
    if len(records) != EXPECTED_WORKLIST_RECORDS:
        raise IngestError(
            f"2021 comparison worklist has {len(records)} records; "
            f"expected {EXPECTED_WORKLIST_RECORDS}"
        )
    if any(record.get("filing_year") != 2021 for record in records):
        raise IngestError("2021 comparison worklist contains a non-2021 filing")
    validate_lob_id_scope(records)
    return True


def default_paths(local_app_data=None, bill_repository_root=None):
    base = local_app_data or os.environ.get("LOCALAPPDATA")
    if not base:
        raise IngestError("LOCALAPPDATA is missing")
    melange_root = os.path.join(base, "Melange")
    rebuild_root = os.path.join(melange_root, "lda_rebuild")
    raw_dir = os.path.join(rebuild_root, "raw")
    derived_dir = os.path.join(rebuild_root, "derived")
    bill_repository_root = bill_repository_root or os.environ.get(
        "MELANGE_BILL_REPOSITORY_ROOT"
    ) or os.path.join(
        os.path.expanduser("~"),
        "OneDrive",
        "Desktop",
        "Melange Systems",
    )
    return {
        "raw_dir": raw_dir,
        "worklist": os.path.join(
            raw_dir, "pinned_2021_filings_worklist.json"
        ),
        "finance_worklist": os.path.join(
            raw_dir, "finance_2021_2024_filings_worklist.json"
        ),
        "bridge3_worklist": os.path.join(
            raw_dir, "union_bridge3_worklist.json"
        ),
        "bridge3_harvest_worklist": os.path.join(
            raw_dir, "pinned_bridge3_worklist.json"
        ),
        "sweeps_dir": os.path.join(raw_dir, "sweeps"),
        "filings_dir": os.path.join(raw_dir, "filings_2021"),
        "partitioned_filings_dir": os.path.join(raw_dir, "filings"),
        "filings_manifest": os.path.join(
            raw_dir, "fec_lda_filings_manifest.json"
        ),
        "billstatus_dir": os.path.join(raw_dir, "billstatus117"),
        "network": os.path.join(
            melange_root, "lobbyview", "raw", "network.csv"
        ),
        "client_classification": os.path.join(
            melange_root,
            "lobbyview",
            "derived",
            "lobbyview_clients_classified.csv",
        ),
        "association_sets": os.path.join(
            raw_dir,
            "positions",
            "assoc_2023_2024",
            "association_sets.json",
        ),
        "association_id_harvest_seed": os.path.join(
            raw_dir,
            "positions",
            "assoc_2023_2024",
            "id_harvest_seed.json",
        ),
        "legislation_membership_edges": os.path.join(
            bill_repository_root, "legislation_membership_edges.ndjson"
        ),
        "bills_audit": os.path.join(bill_repository_root, "bills_audit.csv"),
        "bill_directions": os.path.join(
            bill_repository_root, "data", "legislation", "bill_directions.csv"
        ),
        "derived_dir": derived_dir,
        "our_network": os.path.join(derived_dir, "our_network_2021.csv"),
        "receipts": os.path.join(derived_dir, "rebuild_receipts.json"),
        "comparison": os.path.join(
            derived_dir, "cross_comparison_2021.json"
        ),
    }


def _read_bytes(path, label):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise IngestError(f"cannot read {label} {path}: {exc}") from exc


def _decode_json(data, label):
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IngestError(f"invalid JSON in {label}: {exc}") from exc


def load_worklist(
    path, legacy_default_year=None, comparison_2021=False
):
    data = _read_bytes(path, "worklist")
    records = _decode_json(data, f"worklist {path}")
    if not isinstance(records, list):
        raise IngestError("worklist must be a JSON list")
    seen = set()
    required = {
        "report_uuid",
        "lob_id",
        "filing_period_code",
        "is_amendment",
        "is_no_activity",
        "filing_url",
    }
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise IngestError(f"worklist record {index} is not an object")
        missing = sorted(required - set(record))
        if missing:
            raise IngestError(
                f"worklist record {index} missing key {missing[0]}"
            )
        if "filing_year" not in record:
            if legacy_default_year is None:
                raise IngestError(
                    f"worklist record {index} missing key filing_year"
                )
            record["filing_year"] = legacy_default_year
        record["filing_year"] = validate_filing_year(
            record["filing_year"], index
        )
        report_uuid = validate_report_uuid(record["report_uuid"], index)
        if report_uuid in seen:
            raise IngestError(f"duplicate worklist UUID {report_uuid}")
        seen.add(report_uuid)
        if not isinstance(record["lob_id"], str) or not record["lob_id"]:
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid lob_id"
            )
        if not isinstance(record["filing_period_code"], str):
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid filing_period_code"
            )
        for field in ("is_amendment", "is_no_activity"):
            record[field] = normalize_worklist_boolean(
                record[field], field, f"{index} ({report_uuid})"
            )
        if not isinstance(record["filing_url"], str):
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid filing_url"
            )
    if comparison_2021:
        validate_2021_comparison_scope(records)
    return records, hashlib.md5(data).hexdigest()


def validate_filing_manifest(path, worklist, worklist_md5, filings_dir):
    manifest = _decode_json(
        _read_bytes(path, "filings manifest"), f"filings manifest {path}"
    )
    if not isinstance(manifest, dict):
        raise IngestError("filings manifest must be an object")
    if manifest.get("worklist_md5") != worklist_md5:
        raise IngestError("filings manifest worklist_md5 does not match worklist")
    if manifest.get("failures") != []:
        raise IngestError("filings manifest failures must be empty")
    md5_map = manifest.get("md5")
    if not isinstance(md5_map, dict):
        raise IngestError("filings manifest missing object key md5")
    for record in worklist:
        report_uuid = record["report_uuid"]
        filing_path = os.path.join(filings_dir, report_uuid + ".json")
        expected = md5_map.get(report_uuid)
        if not isinstance(expected, str) or not expected:
            raise IngestError(
                f"filings manifest missing md5 for {report_uuid}"
            )
        if not os.path.isfile(filing_path):
            raise IngestError(f"missing fetched filing {report_uuid}")
        if file_md5(filing_path) != expected.lower():
            raise IngestError(f"filing md5 mismatch for {report_uuid}")
    return manifest


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def worklist_manifest_path(paths, worklist_path):
    if _same_path(worklist_path, paths["worklist"]):
        return paths["filings_manifest"]
    stem = os.path.splitext(os.path.basename(worklist_path))[0]
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    if not safe_stem:
        raise IngestError(
            f"cannot derive manifest name from worklist {worklist_path}"
        )
    return os.path.join(
        paths["raw_dir"], f"fec_lda_filings_manifest_{safe_stem}.json"
    )


def filing_paths_for_record(paths, record, legacy_first=False):
    report_uuid = record["report_uuid"]
    filing_year = record["filing_year"]
    partitioned_path = os.path.join(
        paths["partitioned_filings_dir"],
        str(filing_year),
        report_uuid + ".json",
    )
    candidates = [partitioned_path]
    if filing_year == 2021:
        legacy_path = os.path.join(
            paths["filings_dir"], report_uuid + ".json"
        )
        candidates = (
            [legacy_path, partitioned_path]
            if legacy_first
            else [partitioned_path, legacy_path]
        )
    return tuple(dict.fromkeys(candidates))


def load_worklist_manifest(path, worklist_md5):
    manifest = _decode_json(
        _read_bytes(path, "filings manifest"), f"filings manifest {path}"
    )
    if not isinstance(manifest, dict):
        raise IngestError("filings manifest must be an object")
    if manifest.get("worklist_md5") != worklist_md5:
        raise IngestError("filings manifest worklist_md5 does not match worklist")
    if not isinstance(manifest.get("failures"), list):
        raise IngestError("filings manifest failures must be a list")
    if not isinstance(manifest.get("md5"), dict):
        raise IngestError("filings manifest missing object key md5")
    return manifest


def discover_filing_paths(
    paths,
    records,
    manifest,
    legacy_first=False,
    require_complete=False,
):
    md5_map = manifest["md5"]
    filing_paths = {}
    missing = []
    for record in records:
        report_uuid = record["report_uuid"]
        expected = md5_map.get(report_uuid)
        if not isinstance(expected, str) or not expected:
            missing.append(
                {
                    "report_uuid": report_uuid,
                    "lob_id": record["lob_id"],
                    "filing_year": record["filing_year"],
                    "reason": "manifest_md5_missing",
                }
            )
            continue
        candidates = filing_paths_for_record(
            paths, record, legacy_first=legacy_first
        )
        existing = [path for path in candidates if os.path.isfile(path)]
        if not existing:
            missing.append(
                {
                    "report_uuid": report_uuid,
                    "lob_id": record["lob_id"],
                    "filing_year": record["filing_year"],
                    "reason": "certified_file_missing",
                }
            )
            continue
        selected = existing[0]
        if file_md5(selected) != expected.lower():
            raise IngestError(f"filing md5 mismatch for {report_uuid}")
        filing_paths[report_uuid] = selected
    if require_complete:
        if manifest["failures"] != []:
            raise IngestError(
                "2021 comparison filings manifest failures must be empty"
            )
        if missing:
            raise IngestError(
                f"2021 comparison is missing {len(missing)} certified filings"
            )
    return filing_paths, missing


def normalize_client_name(value):
    """Apply the locked uppercase/punctuation/whitespace normalization."""
    if not isinstance(value, str):
        raise IngestError("client name must be a string")
    normalized = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in value.upper()
    )
    return " ".join(normalized.split())


def fullscope_name_pattern_matches(value):
    """Return the fixed finance-name pattern IDs matching one client name."""
    normalized = normalize_client_name(value)
    return tuple(
        pattern_id
        for pattern_id, expression, _sector in FULLSCOPE_FINANCE_NAME_PATTERNS
        if re.search(expression, normalized) is not None
    )


def _fullscope_pinned_crosswalk():
    """Read the pinned company IDs from the locked look; never copy them."""
    from lobbying import fec_lda_bridge3_look as bridge3_look

    return bridge3_look.CROSSWALK


def load_fullscope_org_scope(
    paths,
    pinned_crosswalk=None,
    enforce_fixed_counts=False,
):
    """Load and union the three fixed full-scope organization sources."""
    orgs = {}

    def add_org(lob_id, names, source, side=None):
        if not isinstance(lob_id, str) or not lob_id.strip():
            raise IngestError(f"full-scope {source} has an invalid lob_id")
        clean_names = []
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise IngestError(
                    f"full-scope {source} {lob_id} has an invalid client name"
                )
            clean_names.append(name.strip())
        if not clean_names:
            raise IngestError(
                f"full-scope {source} {lob_id} has no client names"
            )
        row = orgs.setdefault(
            lob_id,
            {"names": set(), "sources": set(), "sides": set()},
        )
        row["names"].update(clean_names)
        row["sources"].add(source)
        if side is not None:
            row["sides"].add(side)

    classification_path = paths["client_classification"]
    try:
        classification_handle = open(
            classification_path, "r", encoding="utf-8-sig", newline=""
        )
    except OSError as exc:
        raise IngestError(
            f"cannot read full-scope classification {classification_path}: {exc}"
        ) from exc
    classified_lob_ids = set()
    classified_rows = 0
    classified_by_side = collections.Counter()
    classification_guard_exclusions = {}
    with classification_handle:
        reader = csv.DictReader(classification_handle)
        required = {"lob_id", "client_name", "side"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise IngestError(
                "full-scope classification must contain lob_id, client_name, "
                "and side"
            )
        for row_number, row in enumerate(reader, start=2):
            side = (row.get("side") or "").strip()
            if side not in FULLSCOPE_SIDES:
                continue
            lob_id = (row.get("lob_id") or "").strip()
            client_name = (row.get("client_name") or "").strip()
            # The frozen LobbyView-era classification obeys the same exclusion
            # policy as the sector roster (2026-08-05 bank tightening);
            # otherwise a name Paul excluded from the roster would re-enter
            # the universe through this source.
            guard_reason = sector_authority.scope_classification_exclusion(
                client_name
            )
            if guard_reason:
                classification_guard_exclusions[client_name] = guard_reason
                continue
            add_org(lob_id, [client_name], "classified_finance", side=side)
            classified_rows += 1
            classified_lob_ids.add(lob_id)
            classified_by_side[side] += 1
    if not classified_lob_ids:
        raise IngestError("full-scope classification selected no organizations")

    association_path = paths["association_sets"]
    association_payload = _decode_json(
        _read_bytes(association_path, "association sets"),
        f"association sets {association_path}",
    )
    association_sets = (
        association_payload.get("association_sets")
        if isinstance(association_payload, dict)
        else None
    )
    if not isinstance(association_sets, dict):
        raise IngestError("association sets missing object key association_sets")
    association_lob_ids = set()
    for stratum in sorted(association_sets):
        entries = association_sets[stratum]
        if not isinstance(entries, list):
            raise IngestError(
                f"association_sets.{stratum} must be a list"
            )
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise IngestError(
                    f"association_sets.{stratum}[{index}] is not an object"
                )
            lob_id = entry.get("lob_id")
            names = entry.get("client_names")
            if not isinstance(names, list):
                raise IngestError(
                    f"association_sets.{stratum}[{index}].client_names "
                    "must be a list"
                )
            add_org(lob_id, names, "trade_association", side=stratum)
            association_lob_ids.add(lob_id)

    pinned_crosswalk = (
        _fullscope_pinned_crosswalk()
        if pinned_crosswalk is None
        else pinned_crosswalk
    )
    if not isinstance(pinned_crosswalk, dict):
        raise IngestError("pinned company crosswalk must be an object")
    pinned_lob_ids = set()
    for stratum in sorted(pinned_crosswalk):
        clients = pinned_crosswalk[stratum]
        if not isinstance(clients, dict):
            raise IngestError(
                f"pinned company crosswalk {stratum!r} is not an object"
            )
        for client_name, lob_id in sorted(clients.items()):
            add_org(lob_id, [client_name], "pinned_company", side=stratum)
            pinned_lob_ids.add(lob_id)

    if enforce_fixed_counts:
        if len(association_lob_ids) != EXPECTED_FULLSCOPE_ASSOCIATIONS:
            raise IngestError(
                "full-scope association source is not the fixed 22-lob_id set"
            )
        if len(pinned_lob_ids) != EXPECTED_FULLSCOPE_PINNED_COMPANIES:
            raise IngestError(
                "full-scope pinned-company source is not the locked 22-lob_id set"
            )

    normalized_orgs = {}
    name_to_lob = {}
    for lob_id in sorted(orgs):
        row = orgs[lob_id]
        names = tuple(sorted(row["names"], key=lambda value: (value.upper(), value)))
        normalized_names = tuple(sorted({normalize_client_name(name) for name in names}))
        if any(not name for name in normalized_names):
            raise IngestError(f"full-scope organization {lob_id} has an empty name")
        for normalized_name in normalized_names:
            prior = name_to_lob.get(normalized_name)
            if prior is not None and prior != lob_id:
                raise IngestError(
                    "full-scope exact normalized client name maps to multiple "
                    f"lob_ids: {normalized_name!r} -> {prior}, {lob_id}"
                )
            name_to_lob[normalized_name] = lob_id
        normalized_orgs[lob_id] = {
            "names": names,
            "normalized_names": normalized_names,
            "source_name_by_normalized": {
                normalized_name: next(
                    name
                    for name in names
                    if normalize_client_name(name) == normalized_name
                )
                for normalized_name in normalized_names
            },
            "canonical_name": names[0],
            "sources": tuple(sorted(row["sources"])),
            "sides": tuple(sorted(row["sides"])),
        }

    source_lob_ids = {
        "classified_finance": frozenset(classified_lob_ids),
        "trade_association": frozenset(association_lob_ids),
        "pinned_company": frozenset(pinned_lob_ids),
    }
    return {
        "lob_ids": frozenset(normalized_orgs),
        "orgs": normalized_orgs,
        "name_to_lob": name_to_lob,
        "source_lob_ids": source_lob_ids,
        "receipt": {
            "org_set_size": len(normalized_orgs),
            "scope_operation": "set_union_by_lob_id_or_lda_client_id",
            "classified_sides": sorted(FULLSCOPE_SIDES),
            "classified_rows": classified_rows,
            "classified_lob_ids": len(classified_lob_ids),
            "classified_by_side": {
                side: classified_by_side[side]
                for side in sorted(FULLSCOPE_SIDES)
            },
            "trade_association_lob_ids": len(association_lob_ids),
            "pinned_company_lob_ids": len(pinned_lob_ids),
            "classification_rows_excluded_by_sector_guard": {
                "count": len(classification_guard_exclusions),
                "names": {
                    name: classification_guard_exclusions[name]
                    for name in sorted(classification_guard_exclusions)
                },
            },
            "source_counts": {
                "classified_finance": len(classified_lob_ids),
                "trade_association": len(association_lob_ids),
                "pinned_company": len(pinned_lob_ids),
            },
            "net_new_by_union_order": {
                "classified_finance": len(classified_lob_ids),
                "trade_association": len(
                    association_lob_ids - classified_lob_ids
                ),
                "pinned_company": len(
                    pinned_lob_ids
                    - (classified_lob_ids | association_lob_ids)
                ),
            },
            "overlaps": {
                "classified_and_association": len(
                    classified_lob_ids & association_lob_ids
                ),
                "classified_and_pinned": len(
                    classified_lob_ids & pinned_lob_ids
                ),
                "association_and_pinned": len(
                    association_lob_ids & pinned_lob_ids
                ),
            },
            "sources": {
                "classification": {
                    "path": os.path.abspath(classification_path),
                    "md5": file_md5(classification_path),
                },
                "association_sets": {
                    "path": os.path.abspath(association_path),
                    "md5": file_md5(association_path),
                },
                "pinned_companies": {
                    "module": "lobbying.fec_lda_bridge3_look",
                    "symbol": "CROSSWALK",
                },
            },
        },
    }


def fullscope_corpus_basis(scope):
    """Hash the realized scope and attribution contract into the basis key."""
    definition = {
        "mode": "full_scope",
        "network_source": "sweep",
        "scope_sweep_years": list(FULLSCOPE_SWEEP_YEARS),
        "classified_sides": sorted(FULLSCOPE_SIDES),
        "finance_name_patterns": [
            {
                "pattern_id": pattern_id,
                "expression": expression,
                "sector": sector,
            }
            for pattern_id, expression, sector in FULLSCOPE_FINANCE_NAME_PATTERNS
        ],
        "identity_rule": FULLSCOPE_IDENTITY_RULE,
        "organizations": [
            {
                "lob_id": lob_id,
                "normalized_names": list(scope["orgs"][lob_id]["normalized_names"]),
                "sources": list(scope["orgs"][lob_id]["sources"]),
                "pattern_client_ids": list(
                    scope["orgs"][lob_id].get("pattern_client_ids", ())
                ),
                "pattern_matches": list(
                    scope["orgs"][lob_id].get("pattern_matches", ())
                ),
            }
            for lob_id in sorted(scope["lob_ids"])
        ],
    }
    canonical = json.dumps(
        definition,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "fullscope:sha256:" + hashlib.sha256(canonical).hexdigest()


def assert_fullscope_corpus_basis(year_receipts, expected_basis):
    observed = {
        receipt["network"]["CORPUS_BASIS"]
        for receipt in year_receipts.values()
    }
    if observed != {expected_basis}:
        raise IngestError(
            "full-scope years do not share one realized CORPUS_BASIS: "
            + repr(sorted(observed))
        )
    if expected_basis == LEGACY_CORPUS_BASIS:
        raise IngestError("full-scope CORPUS_BASIS equals the legacy basis")
    return expected_basis


def normalize_client_identifier(value, filing_uuid, field):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise IngestError(
            f"filing {filing_uuid} {field} must be an integer or string"
        )
    normalized = str(value).strip()
    if not normalized:
        raise IngestError(
            f"filing {filing_uuid} {field} must be nonempty"
        )
    return normalized


def bridge3_crosswalk_indexes(crosswalk=None):
    crosswalk = BRIDGE3_CROSSWALK if crosswalk is None else crosswalk
    name_to_lob = {}
    lob_to_industry = {}
    lob_to_name = {}
    diagnostic_words = set()
    for industry in sorted(crosswalk):
        clients = crosswalk[industry]
        if not isinstance(clients, dict):
            raise IngestError(
                f"bridge3 crosswalk industry {industry!r} is not an object"
            )
        for client_name, lob_id in sorted(clients.items()):
            normalized_name = normalize_client_name(client_name)
            if not normalized_name:
                raise IngestError("bridge3 crosswalk has an empty client name")
            if normalized_name in name_to_lob:
                raise IngestError(
                    "bridge3 crosswalk has duplicate normalized client name "
                    + normalized_name
                )
            if lob_id in lob_to_industry:
                raise IngestError(
                    f"bridge3 crosswalk has duplicate lob_id {lob_id}"
                )
            name_to_lob[normalized_name] = lob_id
            lob_to_industry[lob_id] = industry
            lob_to_name[lob_id] = client_name
            diagnostic_words.update(
                word for word in normalized_name.split() if len(word) > 5
            )
    if crosswalk is BRIDGE3_CROSSWALK and len(lob_to_industry) != 22:
        raise IngestError("bridge3 crosswalk is not the locked 22-lob_id set")
    return {
        "name_to_lob": name_to_lob,
        "lob_to_industry": lob_to_industry,
        "lob_to_name": lob_to_name,
        "diagnostic_words": frozenset(diagnostic_words),
    }


def _validated_client(payload, filing_uuid):
    if not isinstance(payload, dict):
        raise IngestError(f"filing {filing_uuid} is not an object")
    client = payload.get("client")
    if not isinstance(client, dict):
        raise IngestError(f"filing {filing_uuid} key client is not an object")
    if "id" not in client:
        raise IngestError(f"filing {filing_uuid} missing key client.id")
    if "client_id" not in client:
        raise IngestError(f"filing {filing_uuid} missing key client.client_id")
    if "name" not in client:
        raise IngestError(f"filing {filing_uuid} missing key client.name")
    senate_client_id = normalize_client_identifier(
        client["id"], filing_uuid, "client.id"
    )
    registrant_scoped_client_id = normalize_client_identifier(
        client["client_id"], filing_uuid, "client.client_id"
    )
    if not isinstance(client["name"], str) or not client["name"].strip():
        raise IngestError(
            f"filing {filing_uuid} key client.name must be a nonempty string"
        )
    return senate_client_id, registrant_scoped_client_id, client["name"]


def _validated_activities(payload, filing_uuid):
    if "lobbying_activities" not in payload:
        raise IngestError(
            f"filing {filing_uuid} missing key lobbying_activities"
        )
    activities = payload["lobbying_activities"]
    if not isinstance(activities, list):
        raise IngestError(
            f"filing {filing_uuid} key lobbying_activities is not a list"
        )
    for index, activity in enumerate(activities):
        if not isinstance(activity, dict):
            raise IngestError(
                f"filing {filing_uuid} lobbying_activities[{index}] "
                "is not an object"
            )
        if "description" not in activity:
            raise IngestError(
                f"filing {filing_uuid} lobbying_activities[{index}] "
                "missing key description"
            )
        if not isinstance(activity["description"], str):
            raise IngestError(
                f"filing {filing_uuid} lobbying_activities[{index}].description "
                "is not a string"
            )
    return activities


def _new_fullscope_seed_state():
    return {
        "identity_by_uuid": {},
        "identity_sources_by_uuid": collections.defaultdict(set),
        "aliases": {},
        "pattern_candidates": {},
    }


def _register_fullscope_identity(
    seed_state,
    filing_uuid,
    client_id,
    client_name,
    source,
):
    identity = (client_id, client_name)
    prior = seed_state["identity_by_uuid"].get(filing_uuid)
    if prior is not None and prior != identity:
        raise IngestError(
            "full-scope identity seed UUID has conflicting cached/sweep "
            f"identities: {filing_uuid} -> {prior!r}, {identity!r}"
        )
    seed_state["identity_by_uuid"][filing_uuid] = identity
    seed_state["identity_sources_by_uuid"][filing_uuid].add(source)


def _add_fullscope_seed(
    seed_state,
    lob_id,
    source_name,
    filing_uuid,
    source,
):
    normalized_name = normalize_client_name(source_name)
    key = (lob_id, normalized_name)
    row = seed_state["aliases"].setdefault(
        key,
        {
            "lob_id": lob_id,
            "name": source_name,
            "uuids": set(),
            "sources": collections.Counter(),
        },
    )
    row["uuids"].add(filing_uuid)
    row["sources"][source] += 1


def _record_fullscope_pattern_candidate(
    seed_state,
    filing_uuid,
    filing_year,
    client_id,
    client_name,
    payload=None,
):
    matched_patterns = fullscope_name_pattern_matches(client_name)
    if payload is not None:
        matched_patterns = tuple(
            dict.fromkeys(
                matched_patterns
                + tuple(
                    FULLSCOPE_SECTOR_CHANNEL_PREFIX + sector
                    for sector in sector_authority.scope_sector_matches(
                        payload, sector_authority.DEFAULT_SECTORS
                    )
                )
            )
        )
    if not matched_patterns:
        return ()
    normalized_name = normalize_client_name(client_name)
    row = seed_state["pattern_candidates"].setdefault(
        client_id,
        {
            "client_id": client_id,
            "client_names": set(),
            "normalized_names": set(),
            "patterns": set(),
            "patterns_by_name": collections.defaultdict(set),
            "years": set(),
            "filing_uuids": set(),
            "seed_name_by_uuid": {},
            "first_seen": None,
        },
    )
    prior_seed_name = row["seed_name_by_uuid"].get(filing_uuid)
    if prior_seed_name is not None and prior_seed_name != client_name:
        raise IngestError(
            "name-pattern seed UUID has multiple client names: "
            f"{filing_uuid} -> {prior_seed_name!r}, {client_name!r}"
        )
    row["client_names"].add(client_name)
    row["normalized_names"].add(normalized_name)
    row["patterns"].update(matched_patterns)
    row["patterns_by_name"][client_name].update(matched_patterns)
    row["years"].add(filing_year)
    row["filing_uuids"].add(filing_uuid)
    row["seed_name_by_uuid"][filing_uuid] = client_name
    seen_key = (filing_year, filing_uuid, client_name)
    if row["first_seen"] is None or seen_key < row["first_seen"]:
        row["first_seen"] = seen_key
    _register_fullscope_identity(
        seed_state,
        filing_uuid,
        client_id,
        client_name,
        f"sweep_{filing_year}_finance_name_pattern",
    )
    return matched_patterns


def _extend_fullscope_scope_with_pattern_clients(
    scope,
    seed_state,
    base_harvested_client_ids,
):
    """Merge pattern clients into known lob_ids or add client.id org keys."""
    base_id_owner = {}
    for lob_id in sorted(base_harvested_client_ids):
        for client_id in sorted(base_harvested_client_ids[lob_id]):
            prior = base_id_owner.get(client_id)
            if prior is not None and prior != lob_id:
                raise IngestError(
                    "base harvested client.id has multiple owners before "
                    f"pattern union: {client_id} -> {prior}, {lob_id}"
                )
            base_id_owner[client_id] = lob_id

    pattern_owners = set()
    new_orgs = set()
    per_client = []
    per_pattern_clients = collections.defaultdict(set)
    per_pattern_orgs = collections.defaultdict(set)
    for client_id in sorted(seed_state["pattern_candidates"]):
        candidate = seed_state["pattern_candidates"][client_id]
        owner = base_id_owner.get(client_id)
        if owner is None:
            exact_owners = {
                scope["name_to_lob"][name]
                for name in candidate["normalized_names"]
                if name in scope["name_to_lob"]
            }
            if len(exact_owners) > 1:
                raise IngestError(
                    "pattern-derived client names map to multiple base orgs: "
                    f"client.id {client_id} -> {sorted(exact_owners)}"
                )
            owner = next(iter(exact_owners), None)
        merged_into_existing = owner is not None
        if owner is None:
            owner = client_id
            if owner in scope["orgs"]:
                raise IngestError(
                    "pattern-derived client.id collides with an existing lob_id: "
                    + client_id
                )
            first_name = candidate["first_seen"][2]
            scope["orgs"][owner] = {
                "names": tuple(),
                "normalized_names": tuple(),
                "source_name_by_normalized": {},
                "canonical_name": first_name,
                "sources": tuple(),
                "sides": tuple(),
            }
            new_orgs.add(owner)

        org = scope["orgs"][owner]
        names = set(org["names"])
        names.update(candidate["client_names"])
        normalized_names = {
            normalize_client_name(name) for name in names
        }
        source_name_by_normalized = dict(
            org.get("source_name_by_normalized", {})
        )
        for name in sorted(names, key=lambda value: (value.upper(), value)):
            source_name_by_normalized.setdefault(
                normalize_client_name(name), name
            )
        sources = set(org["sources"])
        sources.add("name_pattern")
        sides = set(org["sides"])
        matched_sectors = {
            sector
            for pattern_id, _expression, sector in FULLSCOPE_FINANCE_NAME_PATTERNS
            if pattern_id in candidate["patterns"]
        }
        for pattern_id in candidate["patterns"]:
            if pattern_id.startswith(FULLSCOPE_SECTOR_CHANNEL_PREFIX):
                matched_sectors.add(
                    FULLSCOPE_SECTOR_CHANNEL_SIDES[
                        pattern_id[len(FULLSCOPE_SECTOR_CHANNEL_PREFIX):]
                    ]
                )
        sides.update(matched_sectors)
        org.update(
            {
                "names": tuple(
                    sorted(names, key=lambda value: (value.upper(), value))
                ),
                "normalized_names": tuple(sorted(normalized_names)),
                "source_name_by_normalized": source_name_by_normalized,
                "sources": tuple(sorted(sources)),
                "sides": tuple(sorted(sides)),
                "pattern_matches": tuple(
                    sorted(
                        set(org.get("pattern_matches", ()))
                        | set(candidate["patterns"])
                    )
                ),
                "pattern_client_ids": tuple(
                    sorted(
                        set(org.get("pattern_client_ids", ())) | {client_id}
                    )
                ),
                "pattern_years": tuple(
                    sorted(
                        set(org.get("pattern_years", ()))
                        | set(candidate["years"])
                    )
                ),
            }
        )
        pattern_owners.add(owner)
        for pattern_id in candidate["patterns"]:
            per_pattern_clients[pattern_id].add(client_id)
            per_pattern_orgs[pattern_id].add(owner)
        for filing_uuid in sorted(candidate["filing_uuids"]):
            _add_fullscope_seed(
                seed_state,
                owner,
                candidate["seed_name_by_uuid"][filing_uuid],
                filing_uuid,
                "sweep_finance_name_pattern",
            )
        per_client.append(
            {
                "client_id": client_id,
                "lob_id_or_client_id": owner,
                "merged_into_existing_org": merged_into_existing,
                "first_year_seen": min(candidate["years"]),
                "last_year_seen": max(candidate["years"]),
                "matches": [
                    {
                        "client_name": name,
                        "normalized_client_name": normalize_client_name(name),
                        "matched_patterns": sorted(
                            candidate["patterns_by_name"][name]
                        ),
                    }
                    for name in sorted(
                        candidate["client_names"],
                        key=lambda value: (value.upper(), value),
                    )
                ],
            }
        )

    owners_by_name = collections.defaultdict(set)
    for owner in sorted(scope["orgs"]):
        for normalized_name in scope["orgs"][owner]["normalized_names"]:
            owners_by_name[normalized_name].add(owner)
    scope["name_to_lob"] = {
        name: next(iter(owners))
        for name, owners in sorted(owners_by_name.items())
        if len(owners) == 1
    }
    ambiguous_names = {
        name: sorted(owners)
        for name, owners in sorted(owners_by_name.items())
        if len(owners) > 1
    }
    scope["lob_ids"] = frozenset(scope["orgs"])
    scope["source_lob_ids"]["name_pattern"] = frozenset(pattern_owners)
    scope["receipt"]["org_set_size"] = len(scope["lob_ids"])
    scope["receipt"]["source_counts"]["name_pattern"] = len(
        pattern_owners
    )
    scope["receipt"]["net_new_by_union_order"]["name_pattern"] = len(
        new_orgs
    )
    scope["receipt"]["name_pattern"] = {
        "scope_sweep_years": list(FULLSCOPE_SWEEP_YEARS),
        "pattern_definitions": [
            {
                "pattern_id": pattern_id,
                "expression": expression,
                "sector": sector,
            }
            for pattern_id, expression, sector in FULLSCOPE_FINANCE_NAME_PATTERNS
        ],
        "id_channel_guard": "fixed_name_token_plausibility",
        "distinct_lda_client_ids": len(seed_state["pattern_candidates"]),
        "contributed_orgs": len(pattern_owners),
        "net_new_orgs": len(new_orgs),
        "ambiguous_normalized_names_excluded_from_name_channel": (
            ambiguous_names
        ),
        "by_pattern": {
            pattern_id: {
                "distinct_lda_client_ids": len(per_pattern_clients[pattern_id]),
                "contributed_orgs": len(per_pattern_orgs[pattern_id]),
            }
            for pattern_id in sorted(
                set(per_pattern_clients)
                | {p for p, _e, _s in FULLSCOPE_FINANCE_NAME_PATTERNS}
            )
        },
        "per_client": per_client,
    }
    return scope["receipt"]["name_pattern"]


def _load_fullscope_cached_identity_seeds(paths, scope, seed_state):
    """Load owned UUID seeds from the existing partitioned filing cache."""
    source_specs = (
        ("finance_worklist", paths["finance_worklist"], None),
        ("pinned_2021_worklist", paths["worklist"], 2021),
        ("pinned_company_worklist", paths["bridge3_harvest_worklist"], None),
    )
    seed_records = {}
    source_receipts = []
    for source_label, worklist_path, legacy_year in source_specs:
        records, digest = load_worklist(
            worklist_path, legacy_default_year=legacy_year
        )
        selected = 0
        for record in records:
            lob_id = record["lob_id"]
            if lob_id not in scope["lob_ids"]:
                continue
            filing_uuid = record["report_uuid"]
            owner = (lob_id, record["filing_year"])
            prior = seed_records.get(filing_uuid)
            if prior is not None and prior[:2] != owner:
                raise IngestError(
                    "full-scope cache seed UUID maps to multiple owners or "
                    f"years: {filing_uuid} -> {prior[:2]}, {owner}"
                )
            seed_records[filing_uuid] = owner + (source_label,)
            selected += 1
        source_receipts.append(
            {
                "source": source_label,
                "path": os.path.abspath(worklist_path),
                "md5": digest,
                "records": len(records),
                "in_scope_records": selected,
            }
        )

    cached = 0
    missing = []
    for filing_uuid in sorted(seed_records):
        lob_id, filing_year, source_label = seed_records[filing_uuid]
        filing_candidates = filing_paths_for_record(
            paths,
            {
                "report_uuid": filing_uuid,
                "filing_year": filing_year,
            },
        )
        filing_path = next(
            (path for path in filing_candidates if os.path.isfile(path)),
            None,
        )
        if filing_path is None:
            missing.append(
                {
                    "filing_uuid": filing_uuid,
                    "lob_id": lob_id,
                    "filing_year": filing_year,
                    "source": source_label,
                    "reason": "uuid_absent_from_partitioned_filings_cache",
                }
            )
            continue
        payload = _decode_json(
            _read_bytes(filing_path, f"identity seed filing {filing_uuid}"),
            f"identity seed filing {filing_uuid}",
        )
        observed_uuid = _validate_sweep_filing(
            payload, filing_year, os.path.basename(filing_path), 0
        )
        if observed_uuid != filing_uuid:
            raise IngestError(
                f"cached identity seed {filing_path} UUID differs from filename"
            )
        client_id, _registrant_scoped_id, client_name = _validated_client(
            payload, filing_uuid
        )
        _register_fullscope_identity(
            seed_state,
            filing_uuid,
            client_id,
            client_name,
            "by_uuid_cache",
        )
        for source_name in scope["orgs"][lob_id]["names"]:
            _add_fullscope_seed(
                seed_state,
                lob_id,
                source_name,
                filing_uuid,
                "by_uuid_cache",
            )
        cached += 1

    association_seed_path = paths["association_id_harvest_seed"]
    association_seed = _decode_json(
        _read_bytes(association_seed_path, "association identity seed"),
        f"association identity seed {association_seed_path}",
    )
    if not isinstance(association_seed, dict):
        raise IngestError("association identity seed must be an object")
    try:
        cache_years = sorted(
            int(name)
            for name in os.listdir(paths["partitioned_filings_dir"])
            if name.isdigit()
            and os.path.isdir(
                os.path.join(paths["partitioned_filings_dir"], name)
            )
        )
    except OSError as exc:
        raise IngestError(
            "cannot list partitioned filings cache for association seeds: "
            + str(exc)
        ) from exc
    association_selected = 0
    association_cached = 0
    for lob_id in sorted(association_seed):
        if lob_id not in scope["lob_ids"]:
            continue
        entry = association_seed[lob_id]
        if not isinstance(entry, dict):
            raise IngestError(
                f"association identity seed {lob_id} is not an object"
            )
        source_name = entry.get("name")
        uuids = entry.get("uuids")
        if not isinstance(source_name, str) or not isinstance(uuids, list):
            raise IngestError(
                f"association identity seed {lob_id} has invalid name/uuids"
            )
        for filing_uuid in uuids:
            validate_report_uuid(filing_uuid, f"association seed {lob_id}")
            association_selected += 1
            hits = [
                (year, os.path.join(
                    paths["partitioned_filings_dir"],
                    str(year),
                    filing_uuid + ".json",
                ))
                for year in cache_years
                if os.path.isfile(
                    os.path.join(
                        paths["partitioned_filings_dir"],
                        str(year),
                        filing_uuid + ".json",
                    )
                )
            ]
            if len(hits) > 1:
                raise IngestError(
                    "association identity seed UUID appears in multiple cache "
                    f"partitions: {filing_uuid}"
                )
            if not hits:
                missing.append(
                    {
                        "filing_uuid": filing_uuid,
                        "lob_id": lob_id,
                        "filing_year": None,
                        "source": "association_id_harvest_seed",
                        "reason": "uuid_absent_from_partitioned_filings_cache",
                    }
                )
                continue
            filing_year, filing_path = hits[0]
            payload = _decode_json(
                _read_bytes(filing_path, f"identity seed filing {filing_uuid}"),
                f"identity seed filing {filing_uuid}",
            )
            observed_uuid = _validate_sweep_filing(
                payload, filing_year, os.path.basename(filing_path), 0
            )
            if observed_uuid != filing_uuid:
                raise IngestError(
                    f"cached identity seed {filing_path} UUID differs from filename"
                )
            client_id, _registrant_scoped_id, client_name = _validated_client(
                payload, filing_uuid
            )
            _register_fullscope_identity(
                seed_state,
                filing_uuid,
                client_id,
                client_name,
                "by_uuid_cache",
            )
            _add_fullscope_seed(
                seed_state,
                lob_id,
                source_name,
                filing_uuid,
                "association_id_harvest_seed",
            )
            association_cached += 1

    source_receipts.append(
        {
            "source": "association_id_harvest_seed",
            "path": os.path.abspath(association_seed_path),
            "md5": file_md5(association_seed_path),
            "in_scope_records": association_selected,
            "cached_records": association_cached,
        }
    )
    cache_scan_specs = [
        (
            year,
            os.path.join(paths["partitioned_filings_dir"], str(year)),
        )
        for year in cache_years
    ]
    if os.path.isdir(paths["filings_dir"]):
        cache_scan_specs.append((2021, paths["filings_dir"]))
    cache_files_scanned = 0
    cache_exact_name_seeds = set()
    for filing_year, cache_dir in cache_scan_specs:
        try:
            cache_names = sorted(os.listdir(cache_dir))
        except OSError as exc:
            raise IngestError(
                f"cannot list by-UUID cache directory {cache_dir}: {exc}"
            ) from exc
        for cache_name in cache_names:
            filing_uuid, extension = os.path.splitext(cache_name)
            if extension.lower() != ".json" or UUID_RE.fullmatch(filing_uuid) is None:
                continue
            cache_files_scanned += 1
            filing_path = os.path.join(cache_dir, cache_name)
            payload = _decode_json(
                _read_bytes(filing_path, f"by-UUID cache filing {filing_uuid}"),
                f"by-UUID cache filing {filing_uuid}",
            )
            observed_uuid = _validate_sweep_filing(
                payload, filing_year, cache_name, 0
            )
            if observed_uuid != filing_uuid:
                raise IngestError(
                    f"by-UUID cache filing {filing_path} differs from filename"
                )
            client_id, _registrant_scoped_id, client_name = _validated_client(
                payload, filing_uuid
            )
            normalized_name = normalize_client_name(client_name)
            lob_id = scope["name_to_lob"].get(normalized_name)
            if lob_id is None:
                continue
            source_name = scope["orgs"][lob_id][
                "source_name_by_normalized"
            ][normalized_name]
            _register_fullscope_identity(
                seed_state,
                filing_uuid,
                client_id,
                client_name,
                "by_uuid_cache_exact_name",
            )
            _add_fullscope_seed(
                seed_state,
                lob_id,
                source_name,
                filing_uuid,
                "by_uuid_cache_exact_name",
            )
            cache_exact_name_seeds.add(filing_uuid)
    source_receipts.append(
        {
            "source": "by_uuid_cache_exact_name_scan",
            "directories": [
                os.path.abspath(cache_dir)
                for _filing_year, cache_dir in cache_scan_specs
            ],
            "files_scanned": cache_files_scanned,
            "exact_name_seed_filings": len(cache_exact_name_seeds),
        }
    )
    return {
        "sources": source_receipts,
        "unique_owned_seed_uuids": len(seed_records),
        "cached_owned_seed_uuids": cached,
        "missing_seed_filings": missing,
    }


def _stage_fullscope_sweep_year(
    connection,
    sweep_dir,
    filing_year,
    scope,
    seed_state,
):
    """Parse each page once, validate/dedupe, and stage compact filing data."""
    page_paths = _sweep_page_paths(sweep_dir, filing_year)
    seen = set()
    first_body_digest_by_uuid = {}
    duplicate_filing_uuids = set()
    duplicate_uuid_body_mismatch = set()
    page_md5 = {}
    raw_filing_occurrences = 0
    max_posted = None
    max_posted_raw = None
    for page_path in page_paths:
        page_name = os.path.basename(page_path)
        data = _read_bytes(page_path, f"full-scope sweep {filing_year} page")
        page_md5[page_name] = hashlib.md5(data).hexdigest()
        page = _decode_json(data, f"full-scope sweep {filing_year} {page_name}")
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise IngestError(
                f"full-scope sweep {filing_year} {page_name} missing results list"
            )
        staged_rows = []
        for result_index, payload in enumerate(page["results"]):
            filing_uuid = _validate_sweep_filing(
                payload, filing_year, page_name, result_index
            )
            raw_filing_occurrences += 1
            if "dt_posted" in payload and payload["dt_posted"] is not None:
                posted, posted_raw = _parse_dt_posted(
                    payload["dt_posted"], filing_uuid
                )
                if max_posted is None or posted > max_posted:
                    max_posted = posted
                    max_posted_raw = posted_raw
            body_digest = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).digest()
            if filing_uuid in seen:
                duplicate_filing_uuids.add(filing_uuid)
                if first_body_digest_by_uuid[filing_uuid] != body_digest:
                    duplicate_uuid_body_mismatch.add(filing_uuid)
                continue
            seen.add(filing_uuid)
            first_body_digest_by_uuid[filing_uuid] = body_digest
            client_id, _registrant_scoped_id, client_name = _validated_client(
                payload, filing_uuid
            )
            normalized_name = normalize_client_name(client_name)
            exact_lob_id = scope["name_to_lob"].get(normalized_name)
            if exact_lob_id is not None:
                source_name = scope["orgs"][exact_lob_id][
                    "source_name_by_normalized"
                ][normalized_name]
                _register_fullscope_identity(
                    seed_state,
                    filing_uuid,
                    client_id,
                    client_name,
                    f"sweep_{filing_year}",
                )
                _add_fullscope_seed(
                    seed_state,
                    exact_lob_id,
                    source_name,
                    filing_uuid,
                    f"sweep_{filing_year}",
                )
            _record_fullscope_pattern_candidate(
                seed_state,
                filing_uuid,
                filing_year,
                client_id,
                client_name,
                payload=payload,
            )
            staged_rows.append(
                (
                    filing_year,
                    filing_uuid,
                    client_id,
                    client_name,
                    normalized_name,
                    payload.get("filing_period"),
                    json.dumps(
                        payload["lobbying_activities"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
            )
        connection.executemany(
            "INSERT INTO staged_filings "
            "(filing_year, filing_uuid, client_id, client_name, "
            "normalized_name, filing_period, activities_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            staged_rows,
        )
    manifest = _validate_sweep_manifest(sweep_dir, filing_year, page_md5)
    return {
        "source": "sweep",
        "filing_year": filing_year,
        "page_count": len(page_paths),
        "unique_filings": len(seen),
        "raw_filing_occurrences": raw_filing_occurrences,
        "deduplication": {
            "rule": "filing_uuid_first_occurrence",
            "duplicate_filing_uuids": {
                "count": len(duplicate_filing_uuids),
                "filing_uuids": sorted(duplicate_filing_uuids),
            },
            "duplicate_uuid_body_mismatch": {
                "count": len(duplicate_uuid_body_mismatch),
                "filing_uuids": sorted(duplicate_uuid_body_mismatch),
            },
        },
        "sweep_through_dt_posted": max_posted_raw,
        "manifest": manifest,
    }


def _harvest_fullscope_client_ids(scope, seed_state, cache_receipt):
    """Use the shared guard for every source-name channel, then union IDs."""
    accepted_by_lob = {lob_id: set() for lob_id in scope["lob_ids"]}
    per_org = {
        lob_id: {
            "lob_id": lob_id,
            "source_names": list(scope["orgs"][lob_id]["names"]),
            "seed_filing_uuids": set(),
            "filing_names_seen": set(),
            "accepted_client_ids": set(),
            "rejected_client_ids": [],
            "seed_sources": collections.Counter(),
        }
        for lob_id in scope["lob_ids"]
    }

    def load_identity(_owner, filing_uuid):
        return seed_state["identity_by_uuid"].get(filing_uuid)

    for key in sorted(seed_state["aliases"]):
        alias = seed_state["aliases"][key]
        lob_id = alias["lob_id"]
        uuids = tuple(sorted(alias["uuids"]))
        guarded = identity_harvest.harvest_guarded_client_ids(
            {
                lob_id: {
                    "name": alias["name"],
                    "uuids": uuids,
                }
            },
            load_identity,
            error_type=IngestError,
            owner_label="full-scope organizations",
        )[lob_id]
        row = per_org[lob_id]
        row["seed_filing_uuids"].update(uuids)
        row["filing_names_seen"].update(guarded["filing_names_seen"])
        row["accepted_client_ids"].update(guarded["accepted_client_ids"])
        for rejection in guarded["rejected_client_ids"]:
            sourced = dict(rejection)
            sourced["source_name"] = alias["name"]
            row["rejected_client_ids"].append(sourced)
        row["seed_sources"].update(alias["sources"])

    client_id_owner = {}
    for lob_id in sorted(per_org):
        for client_id in sorted(per_org[lob_id]["accepted_client_ids"]):
            prior = client_id_owner.get(client_id)
            if prior is not None and prior != lob_id:
                raise IngestError(
                    "harvested client.id maps to multiple full-scope "
                    f"organizations: {client_id} -> {prior}, {lob_id}"
                )
            client_id_owner[client_id] = lob_id
            accepted_by_lob[lob_id].add(client_id)

    receipt_rows = []
    for lob_id in sorted(per_org):
        row = per_org[lob_id]
        receipt_rows.append(
            {
                "lob_id": lob_id,
                "source_names": row["source_names"],
                "seed_filing_uuids": len(row["seed_filing_uuids"]),
                "seed_sources": {
                    source: row["seed_sources"][source]
                    for source in sorted(row["seed_sources"])
                },
                "filing_names_seen": sorted(row["filing_names_seen"]),
                "accepted_client_ids": sorted(row["accepted_client_ids"]),
                "rejected_client_ids": sorted(
                    row["rejected_client_ids"],
                    key=lambda item: (
                        item["client_id"],
                        item["filing_client_name"],
                        item["source_name"],
                    ),
                ),
            }
        )
    return accepted_by_lob, {
        "seed_policy": "requested_sweeps_plus_existing_by_uuid_cache",
        "identifying_client_key_field": "client.id",
        "registrant_scoped_non_identifying_field": "client.client_id",
        "id_channel_guard": "fixed_name_token_plausibility",
        "secondary_rule": "exact_normalized_client.name",
        "channel_combination": "union",
        "accepted_client_ids": len(client_id_owner),
        "organizations_with_accepted_client_ids": sum(
            bool(values) for values in accepted_by_lob.values()
        ),
        "cache": cache_receipt,
        "per_org": receipt_rows,
    }


def harvest_client_ids(
    worklist,
    filing_paths,
    crosswalk=None,
    validate_cached_filings=False,
):
    """Harvest name-guarded global ``client.id`` values from all seed years."""
    indexes = bridge3_crosswalk_indexes(crosswalk)
    pinned_lob_ids = set(indexes["lob_to_industry"])
    seed = {
        lob_id: {
            "stratum": indexes["lob_to_industry"][lob_id],
            "name": indexes["lob_to_name"][lob_id],
            "uuids": [],
        }
        for lob_id in pinned_lob_ids
    }
    record_by_uuid = {}
    for record in worklist:
        lob_id = record["lob_id"]
        if lob_id not in pinned_lob_ids:
            continue
        filing_uuid = record["report_uuid"]
        filing_year = record["filing_year"]
        prior = record_by_uuid.get(filing_uuid)
        identity = (lob_id, filing_year)
        if prior is not None:
            if prior != identity:
                raise IngestError(
                    "identity seed UUID maps to multiple owners or years: "
                    f"{filing_uuid} -> {prior}, {identity}"
                )
            continue
        record_by_uuid[filing_uuid] = identity
        seed[lob_id]["uuids"].append(filing_uuid)
    for entry in seed.values():
        entry["uuids"] = tuple(sorted(entry["uuids"]))

    registrant_scoped = {lob_id: set() for lob_id in pinned_lob_ids}
    def load_identity(lob_id, filing_uuid):
        filing_path = filing_paths.get(filing_uuid)
        if filing_path is None:
            return None
        payload = _decode_json(
            _read_bytes(filing_path, f"filing {filing_uuid}"),
            f"filing {filing_uuid}",
        )
        if validate_cached_filings:
            filing_year = record_by_uuid[filing_uuid][1]
            observed_uuid = _validate_sweep_filing(
                payload,
                filing_year,
                os.path.basename(filing_path),
                0,
            )
            if observed_uuid != filing_uuid:
                raise IngestError(
                    f"cached filing {filing_path} UUID does not match its filename"
                )
        (
            senate_client_id,
            registrant_scoped_client_id,
            client_name,
        ) = _validated_client(payload, filing_uuid)
        registrant_scoped[lob_id].add(
            registrant_scoped_client_id
        )
        return senate_client_id, client_name

    guarded = identity_harvest.harvest_guarded_client_ids(
        seed,
        load_identity,
        error_type=IngestError,
        owner_label="pinned orgs",
    )
    harvested = {
        lob_id: set(guarded[lob_id]["accepted_client_ids"])
        for lob_id in sorted(guarded)
    }
    seed_years = sorted({year for _lob_id, year in record_by_uuid.values()})
    count_rows = []
    for lob_id in sorted(harvested):
        guarded_row = guarded[lob_id]
        unfetched = sorted(
            filing_uuid
            for filing_uuid in seed[lob_id]["uuids"]
            if filing_uuid not in filing_paths
        )
        count_rows.append(
            {
                "lob_id": lob_id,
                "industry": indexes["lob_to_industry"][lob_id],
                "lobbyview_name": indexes["lob_to_name"][lob_id],
                "seed_filing_uuids": len(seed[lob_id]["uuids"]),
                "source_filings_all_years": guarded_row["uuids_present"],
                "unfetched_filings": guarded_row["uuids_missing"],
                "unfetched_filing_uuids": unfetched,
                "harvested_client_ids_count": len(harvested[lob_id]),
                "harvested_client_ids_source_field": "client.id",
                "client_ids": list(guarded_row["client_ids"]),
                "accepted_client_ids": list(
                    guarded_row["accepted_client_ids"]
                ),
                "rejected_client_ids": [
                    dict(rejection)
                    for rejection in guarded_row["rejected_client_ids"]
                ],
                "filing_names_seen": list(
                    guarded_row["filing_names_seen"]
                ),
                "registrant_scoped_client_ids_non_identifying": sorted(
                    registrant_scoped[lob_id]
                ),
                "registrant_scoped_client_ids_non_identifying_count": len(
                    registrant_scoped[lob_id]
                ),
            }
        )
    return harvested, {
        "harvest_filing_years": seed_years,
        "harvest_max_filing_year": max(seed_years) if seed_years else None,
        "harvest_year_cap": None,
        "identifying_client_key_field": "client.id",
        "registrant_scoped_non_identifying_field": "client.client_id",
        "id_channel_guard": "fixed_name_token_plausibility",
        "per_org": count_rows,
        "counts_by_lob_id": {
            lob_id: len(harvested[lob_id]) for lob_id in sorted(harvested)
        },
        "registrant_scoped_client_ids_non_identifying_by_lob_id": {
            lob_id: sorted(registrant_scoped[lob_id])
            for lob_id in sorted(registrant_scoped)
        },
    }


def load_pinned_identity_harvest(paths, crosswalk=None):
    """Load the two pinned worklists and harvest only cached own filings."""
    source_specs = (
        (paths["worklist"], 2021),
        (paths["bridge3_harvest_worklist"], None),
    )
    source_receipts = []
    combined = []
    for worklist_path, legacy_year in source_specs:
        records, worklist_md5 = load_worklist(
            worklist_path, legacy_default_year=legacy_year
        )
        combined.extend(records)
        source_receipts.append(
            {
                "path": os.path.abspath(worklist_path),
                "md5": worklist_md5,
                "records": len(records),
                "filing_years": sorted(
                    {record["filing_year"] for record in records}
                ),
            }
        )

    indexes = bridge3_crosswalk_indexes(crosswalk)
    unexpected_lob_ids = sorted(
        {
            record["lob_id"]
            for record in combined
            if record["lob_id"] not in indexes["lob_to_industry"]
        }
    )
    if unexpected_lob_ids:
        raise IngestError(
            "pinned identity worklist contains lob_id outside locked scope: "
            + unexpected_lob_ids[0]
        )

    unique_records = {}
    for record in combined:
        filing_uuid = record["report_uuid"]
        identity = (record["lob_id"], record["filing_year"])
        prior = unique_records.get(filing_uuid)
        if prior is not None:
            prior_identity = (prior["lob_id"], prior["filing_year"])
            if prior_identity != identity:
                raise IngestError(
                    "pinned identity UUID maps to multiple owners or years: "
                    f"{filing_uuid} -> {prior_identity}, {identity}"
                )
            continue
        unique_records[filing_uuid] = record

    filing_paths = {}
    unfetched = []
    for filing_uuid in sorted(unique_records):
        record = unique_records[filing_uuid]
        filing_path = os.path.join(
            paths["partitioned_filings_dir"],
            str(record["filing_year"]),
            filing_uuid + ".json",
        )
        if os.path.isfile(filing_path):
            filing_paths[filing_uuid] = filing_path
        else:
            unfetched.append(
                {
                    "report_uuid": filing_uuid,
                    "lob_id": record["lob_id"],
                    "filing_year": record["filing_year"],
                    "reason": "uuid_absent_from_partitioned_filings_cache",
                }
            )

    harvested, receipt = harvest_client_ids(
        list(unique_records.values()),
        filing_paths,
        crosswalk=crosswalk,
        validate_cached_filings=True,
    )
    receipt.update(
        {
            "seed_worklists": source_receipts,
            "partitioned_filings_cache": os.path.abspath(
                paths["partitioned_filings_dir"]
            ),
            "seed_filing_uuids": len(unique_records),
            "cached_seed_filing_uuids": len(filing_paths),
            "unfetched_seed_filings": unfetched,
        }
    )
    return harvested, receipt


def attribute_sweep_filing(
    payload,
    harvested_client_ids,
    crosswalk=None,
    crosswalk_indexes=None,
):
    filing_uuid = payload.get("filing_uuid", "<unknown>")
    senate_client_id, _registrant_scoped_id, client_name = _validated_client(
        payload, filing_uuid
    )
    indexes = (
        bridge3_crosswalk_indexes(crosswalk)
        if crosswalk_indexes is None
        else crosswalk_indexes
    )
    id_hits = {
        lob_id
        for lob_id, client_ids in harvested_client_ids.items()
        if senate_client_id in client_ids
    }
    normalized_name = normalize_client_name(client_name)
    name_lob = indexes["name_to_lob"].get(normalized_name)
    name_hits = set() if name_lob is None else {name_lob}
    hits = id_hits | name_hits
    if len(id_hits) > 1 or len(name_hits) > 1 or len(hits) > 1:
        raise IngestError(
            f"filing {filing_uuid} has ambiguous pinned-org attribution: "
            f"client.id_hits={sorted(id_hits)}, name_hits={sorted(name_hits)}"
        )
    lob_id = next(iter(hits), None)
    near_miss = (
        lob_id is None
        and bool(normalized_name)
        and any(
            word in normalized_name for word in indexes["diagnostic_words"]
        )
    )
    return {
        "lob_id": lob_id,
        "client_id_match": bool(id_hits),
        "client_identifier_field": "client.id",
        "exact_name_match": bool(name_hits),
        "normalized_client_name": normalized_name,
        "near_miss": near_miss,
    }


def _validate_sweep_filing(payload, filing_year, page_name, result_index):
    label = f"sweep {filing_year} {page_name} result {result_index}"
    if not isinstance(payload, dict):
        raise IngestError(f"{label} is not an object")
    filing_uuid = payload.get("filing_uuid")
    if not isinstance(filing_uuid, str) or UUID_RE.fullmatch(filing_uuid) is None:
        raise IngestError(f"{label} has invalid filing_uuid")
    if "filing_year" not in payload:
        raise IngestError(f"{label} missing key filing_year")
    observed_year = validate_filing_year(payload["filing_year"], label)
    if observed_year != filing_year:
        raise IngestError(
            f"{label} filing_year is {observed_year}; expected {filing_year}"
        )
    _validated_client(payload, filing_uuid)
    _validated_activities(payload, filing_uuid)
    return filing_uuid


def _sweep_page_paths(sweep_dir, filing_year):
    try:
        names = os.listdir(sweep_dir)
    except OSError as exc:
        raise IngestError(
            f"cannot list sweep directory {sweep_dir}: {exc}"
        ) from exc
    page_re = re.compile(r"page_(\d+)\.json$")
    numbered = []
    for name in names:
        match = page_re.fullmatch(name)
        if match is not None:
            numbered.append((int(match.group(1)), name))
    if not numbered:
        raise IngestError(f"sweep {filing_year} has no page_*.json files")
    numbered.sort()
    numbers = [number for number, _name in numbered]
    if len(numbers) != len(set(numbers)):
        raise IngestError(f"sweep {filing_year} has duplicate page numbers")
    if numbers != list(range(1, len(numbers) + 1)):
        raise IngestError(
            f"sweep {filing_year} page numbers are not contiguous from 1"
        )
    return [os.path.join(sweep_dir, name) for _number, name in numbered]


def _parse_dt_posted(value, filing_uuid):
    if not isinstance(value, str) or not value.strip():
        raise IngestError(
            f"filing {filing_uuid} dt_posted must be a nonempty ISO timestamp"
        )
    raw = value.strip()
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise IngestError(
            f"filing {filing_uuid} has invalid dt_posted {raw!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc), raw


def _validate_sweep_manifest(sweep_dir, filing_year, page_md5):
    path = os.path.join(sweep_dir, "sweep_manifest.json")
    payload = _decode_json(
        _read_bytes(path, "sweep manifest"), f"sweep manifest {path}"
    )
    if not isinstance(payload, dict):
        raise IngestError(f"sweep {filing_year} manifest must be an object")
    if payload.get("filing_year") != filing_year:
        raise IngestError(
            f"sweep manifest filing_year is {payload.get('filing_year')!r}; "
            f"expected {filing_year}"
        )
    if payload.get("complete") is not True:
        raise IngestError(f"sweep {filing_year} manifest is not complete")
    if payload.get("page_count") != len(page_md5):
        raise IngestError(
            f"sweep {filing_year} manifest page_count does not match pages"
        )
    manifest_md5 = payload.get("md5")
    if not isinstance(manifest_md5, dict):
        raise IngestError(
            f"sweep {filing_year} manifest missing object key md5"
        )
    if set(manifest_md5) != set(page_md5):
        raise IngestError(
            f"sweep {filing_year} manifest page names do not match disk"
        )
    for page_name, digest in page_md5.items():
        expected = manifest_md5.get(page_name)
        if not isinstance(expected, str) or expected.lower() != digest:
            raise IngestError(
                f"sweep {filing_year} manifest md5 mismatch for {page_name}"
            )
    return {
        "path": os.path.abspath(path),
        "md5": file_md5(path),
        "page_count": payload["page_count"],
        "page_md5": {name: page_md5[name] for name in sorted(page_md5)},
    }


def scan_sweep_pages(
    sweep_dir,
    filing_year,
    harvested_client_ids=None,
    crosswalk=None,
    require_manifest=False,
):
    """Validate a sweep and complete attribution before any aggregation."""
    if filing_year not in BRIDGE3_YEARS:
        raise IngestError(f"unsupported bridge3 sweep year {filing_year}")
    page_paths = _sweep_page_paths(sweep_dir, filing_year)
    seen = set()
    first_body_digest_by_uuid = {}
    duplicate_filing_uuids = set()
    duplicate_uuid_body_mismatch = set()
    page_md5 = {}
    raw_filing_occurrences = 0
    max_posted = None
    max_posted_raw = None
    attribution_by_uuid = {}
    attribution_counts = collections.Counter()
    per_org_counts = collections.Counter()
    per_org_channel_counts = collections.defaultdict(collections.Counter)
    per_org_id_client_names = collections.defaultdict(set)
    exact_name_counts = collections.Counter()
    unmatched_near_names = set()
    crosswalk_indexes = bridge3_crosswalk_indexes(crosswalk)
    pinned_lob_ids = set(crosswalk_indexes["lob_to_industry"])
    if harvested_client_ids is not None:
        missing = sorted(pinned_lob_ids - set(harvested_client_ids))
        extra = sorted(set(harvested_client_ids) - pinned_lob_ids)
        if missing or extra:
            raise IngestError(
                "harvested client.id scope does not match pinned orgs: "
                f"missing={missing}, extra={extra}"
            )
    for page_path in page_paths:
        page_name = os.path.basename(page_path)
        data = _read_bytes(page_path, f"sweep {filing_year} page")
        page_md5[page_name] = hashlib.md5(data).hexdigest()
        page = _decode_json(data, f"sweep {filing_year} {page_name}")
        if not isinstance(page, dict):
            raise IngestError(
                f"sweep {filing_year} {page_name} must be an object"
            )
        if "results" not in page or not isinstance(page["results"], list):
            raise IngestError(
                f"sweep {filing_year} {page_name} missing results list"
            )
        for result_index, payload in enumerate(page["results"]):
            filing_uuid = _validate_sweep_filing(
                payload, filing_year, page_name, result_index
            )
            raw_filing_occurrences += 1
            if "dt_posted" in payload and payload["dt_posted"] is not None:
                posted, posted_raw = _parse_dt_posted(
                    payload["dt_posted"], filing_uuid
                )
                if max_posted is None or posted > max_posted:
                    max_posted = posted
                    max_posted_raw = posted_raw
            body_digest = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).digest()
            if filing_uuid in seen:
                duplicate_filing_uuids.add(filing_uuid)
                if first_body_digest_by_uuid[filing_uuid] != body_digest:
                    duplicate_uuid_body_mismatch.add(filing_uuid)
                continue
            seen.add(filing_uuid)
            first_body_digest_by_uuid[filing_uuid] = body_digest
            if harvested_client_ids is not None:
                result = attribute_sweep_filing(
                    payload,
                    harvested_client_ids,
                    crosswalk=crosswalk,
                    crosswalk_indexes=crosswalk_indexes,
                )
                if result["lob_id"] is None:
                    attribution_counts["unmatched"] += 1
                    if result["near_miss"]:
                        unmatched_near_names.add(
                            result["normalized_client_name"]
                        )
                else:
                    attribution_by_uuid[filing_uuid] = result["lob_id"]
                    per_org_counts[result["lob_id"]] += 1
                    if result["client_id_match"]:
                        attribution_counts["client_id_match"] += 1
                        per_org_channel_counts[result["lob_id"]]["id"] += 1
                        per_org_id_client_names[result["lob_id"]].add(
                            payload["client"]["name"]
                        )
                    if result["exact_name_match"]:
                        attribution_counts["exact_name_match"] += 1
                        per_org_channel_counts[result["lob_id"]]["name"] += 1
                        exact_name_counts[
                            result["normalized_client_name"]
                        ] += 1
                    if (
                        result["client_id_match"]
                        and result["exact_name_match"]
                    ):
                        attribution_counts["both_match"] += 1
                        per_org_channel_counts[result["lob_id"]]["both"] += 1
    manifest_receipt = None
    if require_manifest:
        manifest_receipt = _validate_sweep_manifest(
            sweep_dir, filing_year, page_md5
        )
    per_org_year = []
    coverage_gaps = []
    if harvested_client_ids is not None:
        for lob_id in sorted(pinned_lob_ids):
            channels = per_org_channel_counts[lob_id]
            matched_filings = per_org_counts[lob_id]
            row = {
                "lob_id": lob_id,
                "industry": crosswalk_indexes["lob_to_industry"][lob_id],
                "filing_year": filing_year,
                "matched_filings": matched_filings,
                "matched_by_id": channels["id"],
                "matched_by_name": channels["name"],
                "matched_by_both": channels["both"],
                "client_names_matched_by_id": sorted(
                    per_org_id_client_names[lob_id]
                ),
            }
            per_org_year.append(row)
            if matched_filings == 0:
                coverage_gaps.append(
                    {
                        "lob_id": lob_id,
                        "industry": crosswalk_indexes["lob_to_industry"][
                            lob_id
                        ],
                        "filing_year": filing_year,
                        "reason": "zero_id_and_exact_name_matches",
                    }
                )
    return {
        "page_paths": tuple(page_paths),
        "attribution_by_uuid": attribution_by_uuid,
        "receipt": {
            "filing_year": filing_year,
            "pages": len(page_paths),
            "filings": len(seen),
            "raw_filing_occurrences": raw_filing_occurrences,
            "sweep_duplicate_filing_uuids": {
                "count": len(duplicate_filing_uuids),
                "filing_uuids": sorted(duplicate_filing_uuids),
            },
            "sweep_duplicate_uuid_body_mismatch": {
                "count": len(duplicate_uuid_body_mismatch),
                "filing_uuids": sorted(duplicate_uuid_body_mismatch),
            },
            "sweep_through_dt_posted": max_posted_raw,
            "page_md5": {
                name: page_md5[name] for name in sorted(page_md5)
            },
            "manifest": manifest_receipt,
            "attribution": {
                "primary_identifying_field": "client.id",
                "identifying_client_key_field": "client.id",
                "id_channel_guard": "fixed_name_token_plausibility",
                "secondary_rule": "exact_normalized_client.name",
                "registrant_scoped_non_identifying_field": (
                    "client.client_id"
                ),
                "matched_filings": len(attribution_by_uuid),
                "client_id_matches": attribution_counts["client_id_match"],
                "exact_name_matches": attribution_counts["exact_name_match"],
                "both_matches": attribution_counts["both_match"],
                "unmatched_filings": attribution_counts["unmatched"],
                "per_org_matched_filings": {
                    lob_id: per_org_counts[lob_id]
                    for lob_id in sorted(pinned_lob_ids)
                },
                "per_org_year": per_org_year,
                "coverage_gaps": coverage_gaps,
                "exact_name_match_counts": {
                    name: exact_name_counts[name]
                    for name in sorted(exact_name_counts)
                },
                "unmatched_near_names": sorted(unmatched_near_names),
            },
        },
    }


def iter_sweep_filings(page_paths, filing_year):
    """Re-read a validated sweep, yielding the first body for each UUID."""
    seen = set()
    for page_path in page_paths:
        page_name = os.path.basename(page_path)
        page = _decode_json(
            _read_bytes(page_path, f"sweep {filing_year} page"),
            f"sweep {filing_year} {page_name}",
        )
        for result_index, payload in enumerate(page["results"]):
            filing_uuid = _validate_sweep_filing(
                payload, filing_year, page_name, result_index
            )
            if filing_uuid in seen:
                continue
            seen.add(filing_uuid)
            yield payload


def load_client_industries(path, lob_ids):
    required_lob_ids = set(lob_ids)
    industries = {}
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise IngestError(
            f"cannot read client classification {path}: {exc}"
        ) from exc
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"lob_id", "side"}.issubset(
            reader.fieldnames
        ):
            raise IngestError(
                "client classification must contain lob_id and side columns"
            )
        for row_number, row in enumerate(reader, start=2):
            lob_id = (row.get("lob_id") or "").strip()
            if lob_id not in required_lob_ids:
                continue
            if lob_id in industries:
                raise IngestError(
                    f"duplicate client classification lob_id {lob_id} "
                    f"at row {row_number}"
                )
            side = (row.get("side") or "").strip()
            if not side:
                raise IngestError(
                    f"client classification lob_id {lob_id} has blank side"
                )
            industries[lob_id] = side
    missing = sorted(required_lob_ids - set(industries))
    if missing:
        raise IngestError(
            f"client classification missing {len(missing)} worklist lob_ids: "
            f"first={missing[0]}"
        )
    return industries


def billstatus_dir_for_congress(paths, congress):
    if congress not in CONGRESS_FIRST_YEAR:
        raise IngestError(f"unsupported Congress {congress}")
    return os.path.join(paths["raw_dir"], f"billstatus{congress}")


def network_output_path(paths, filing_year, strict=False):
    if strict:
        suffix = "_strict"
    elif filing_year in BRIDGE3_YEARS:
        suffix = "_generous"
    else:
        suffix = ""
    return os.path.join(
        paths["derived_dir"], f"our_network_{filing_year}{suffix}.csv"
    )


def quarterly_network_output_path(paths, filing_year):
    return os.path.join(
        paths["derived_dir"], f"our_network_quarterly_{filing_year}.csv"
    )


def fullscope_network_output_path(paths, filing_year, strict=False):
    channel = "strict" if strict else "generous"
    return os.path.join(
        paths["derived_dir"],
        f"our_network_fullscope_{filing_year}_{channel}.csv",
    )


def fullscope_quarterly_network_output_path(paths, filing_year):
    return os.path.join(
        paths["derived_dir"],
        f"our_network_fullscope_q{filing_year}_strict.csv",
    )


def bill_lobby_index_output_path(paths, filing_year):
    return os.path.join(
        paths["derived_dir"], f"our_bill_lobby_index_{filing_year}.csv"
    )


def bill_lobby_index_manifest_path(paths):
    return os.path.join(
        paths["derived_dir"], "our_bill_lobby_index_manifest.json"
    )


def bill_repository_join_output_path(paths):
    return os.path.join(
        paths["derived_dir"], "our_bill_repository_join.csv"
    )


def fullscope_receipts_path(paths):
    return os.path.join(paths["derived_dir"], "fullscope_receipts.json")


def fullscope_org_roster_path(paths):
    return os.path.join(paths["derived_dir"], "fullscope_org_roster.csv")


def assert_fullscope_output_isolation(paths, years):
    """Prove the full-scope writer set is disjoint from every legacy path."""
    fullscope_paths = {
        os.path.abspath(fullscope_receipts_path(paths)),
        os.path.abspath(fullscope_org_roster_path(paths)),
        os.path.abspath(bill_lobby_index_manifest_path(paths)),
        os.path.abspath(bill_repository_join_output_path(paths)),
    }
    legacy_paths = {
        os.path.abspath(paths["receipts"]),
        os.path.abspath(paths["comparison"]),
    }
    for year in years:
        fullscope_paths.update(
            {
                os.path.abspath(fullscope_network_output_path(paths, year)),
                os.path.abspath(
                    fullscope_network_output_path(paths, year, strict=True)
                ),
                os.path.abspath(
                    fullscope_quarterly_network_output_path(paths, year)
                ),
                os.path.abspath(bill_lobby_index_output_path(paths, year)),
            }
        )
        legacy_paths.update(
            {
                os.path.abspath(network_output_path(paths, year)),
                os.path.abspath(network_output_path(paths, year, strict=True)),
                os.path.abspath(quarterly_network_output_path(paths, year)),
                os.path.abspath(totals_output_path(paths, year)),
            }
        )
    overlap = sorted(fullscope_paths & legacy_paths)
    if overlap:
        raise IngestError(
            "full-scope output path overlaps a legacy output: " + overlap[0]
        )
    return {
        "fullscope": sorted(fullscope_paths),
        "legacy_never_written": sorted(legacy_paths),
    }


def totals_output_path(paths, filing_year):
    return os.path.join(paths["derived_dir"], f"totals_{filing_year}.csv")


def canonical_bill_key(bill_key):
    """Render an internal bill tuple in the repository's exact key format."""
    if not isinstance(bill_key, (tuple, list)) or len(bill_key) != 3:
        raise IngestError(f"invalid internal bill key {bill_key!r}")
    type_token, number, congress = bill_key
    if not isinstance(type_token, str):
        raise IngestError(f"invalid bill type in {bill_key!r}")
    normalized_type = type_token.strip().lower()
    if normalized_type not in CANONICAL_BILL_TYPES:
        raise IngestError(f"unsupported canonical bill type {type_token!r}")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise IngestError(f"invalid bill number in {bill_key!r}")
    if isinstance(congress, bool) or not isinstance(congress, int) or congress <= 0:
        raise IngestError(f"invalid Congress in {bill_key!r}")
    return f"{congress}:{normalized_type}:{number}"


def bill_key_from_canonical(value):
    """Parse only the authoritative ``congress:type:number`` convention."""
    if not isinstance(value, str):
        raise IngestError(f"canonical bill key must be a string, got {value!r}")
    match = re.fullmatch(r"([1-9][0-9]*):([a-z]+):([1-9][0-9]*)", value)
    if match is None or match.group(2) not in CANONICAL_BILL_TYPES:
        raise IngestError(f"invalid canonical bill key {value!r}")
    bill_key = (
        match.group(2).upper(),
        int(match.group(3)),
        int(match.group(1)),
    )
    if canonical_bill_key(bill_key) != value:
        raise IngestError(f"noncanonical bill key {value!r}")
    return bill_key


def repository_display_bill_key(display_bill, congress):
    """Normalize an exact repository display identifier without fuzzy matching."""
    if not isinstance(display_bill, str) or not display_bill.strip():
        raise IngestError(f"invalid repository bill display {display_bill!r}")
    if isinstance(congress, bool):
        raise IngestError(f"invalid repository Congress {congress!r}")
    if isinstance(congress, int):
        normalized_congress = congress
    elif isinstance(congress, str) and re.fullmatch(
        r"[1-9][0-9]*", congress.strip()
    ):
        normalized_congress = int(congress.strip())
    else:
        raise IngestError(f"invalid repository Congress {congress!r}")
    compact = re.sub(r"\s+", "", display_bill).upper()
    for prefix, type_token in DISPLAY_BILL_PREFIXES:
        if not compact.startswith(prefix):
            continue
        number_text = compact[len(prefix) :]
        if re.fullmatch(r"[1-9][0-9]*", number_text) is None:
            break
        return canonical_bill_key(
            (type_token.upper(), int(number_text), normalized_congress)
        )
    raise IngestError(f"unrecognized repository bill display {display_bill!r}")


def _bill_index_channel(sources, strict):
    normalized_sources = set(sources)
    if normalized_sources == {"formal"}:
        origin = "formal"
    elif normalized_sources == {"title"}:
        origin = "title"
    elif normalized_sources == {"formal", "title"}:
        origin = "both"
    else:
        raise IngestError(
            f"bill index has invalid channel sources {sorted(normalized_sources)!r}"
        )
    if "formal" in normalized_sources and not strict:
        raise IngestError("a formal bill citation cannot be generous-only")
    return origin + ("_strict" if strict else "_generous_only")


def _filing_period_sort_key(value):
    if not isinstance(value, str) or not value.strip():
        raise IngestError(f"bill index has invalid filing period {value!r}")
    normalized = value.strip().lower()
    return filing_period_to_quarter(normalized), normalized, value.strip()


def bill_index_rows(filing_year, observations_by_lob_bill, scope):
    """Render one deterministic row per organization-bill-year observation."""
    rows = []
    for (lob_id, bill_key), observation in sorted(
        observations_by_lob_bill.items(),
        key=lambda item: (
            item[0][1][2],
            item[0][1][0].lower(),
            item[0][1][1],
            item[0][0],
        ),
    ):
        if lob_id not in scope["orgs"]:
            raise IngestError(f"bill index has out-of-scope lob_id {lob_id!r}")
        periods = set(observation.get("filing_periods", ()))
        if not periods:
            raise IngestError(
                f"bill index {lob_id} {bill_key!r} has no filing periods"
            )
        n_filings = observation.get("n_filings")
        n_activities = observation.get("n_activities")
        if (
            isinstance(n_filings, bool)
            or not isinstance(n_filings, int)
            or n_filings <= 0
            or isinstance(n_activities, bool)
            or not isinstance(n_activities, int)
            or n_activities <= 0
        ):
            raise IngestError(
                f"bill index {lob_id} {bill_key!r} has invalid counts"
            )
        ordered_periods = sorted(periods, key=_filing_period_sort_key)
        type_token, number, congress = bill_key
        org = scope["orgs"][lob_id]
        rows.append(
            {
                "congress": congress,
                "bill_type": type_token.lower(),
                "bill_number": number,
                "bill_key": canonical_bill_key(bill_key),
                "lob_id": lob_id,
                "client_name": org["canonical_name"],
                "org_sector": "|".join(sorted(set(org.get("sides", ())))),
                "n_filings": n_filings,
                "n_activities": n_activities,
                "first_filing_period": ordered_periods[0],
                "last_filing_period": ordered_periods[-1],
                "channel": _bill_index_channel(
                    observation.get("sources", ()),
                    bool(observation.get("strict")),
                ),
            }
        )
    return rows


def bill_index_year_receipt(filing_year, rows, corpus_basis, output_path):
    channels = collections.Counter(row["channel"] for row in rows)
    strict_rows = sum(
        count for channel, count in channels.items() if channel.endswith("_strict")
    )
    generous_only_rows = sum(
        count
        for channel, count in channels.items()
        if channel.endswith("_generous_only")
    )
    if strict_rows + generous_only_rows != len(rows):
        raise IngestError(
            f"bill index {filing_year} channel receipts do not partition rows"
        )
    if len({(row["bill_key"], row["lob_id"]) for row in rows}) != len(rows):
        raise IngestError(
            f"bill index {filing_year} is not one row per bill and organization"
        )
    return {
        "filing_year": filing_year,
        "CORPUS_BASIS": corpus_basis,
        "distinct_bills": len({row["bill_key"] for row in rows}),
        "distinct_orgs": len({row["lob_id"] for row in rows}),
        "rows": len(rows),
        "generous_rows": len(rows),
        "strict_rows": strict_rows,
        "generous_only_rows": generous_only_rows,
        "channel_counts": {
            channel: channels[channel] for channel in sorted(channels)
        },
        "channel_definition": {
            "origin": "formal citation, title match, or both",
            "strictness": (
                "*_strict rows enter both strict and generous extraction sets; "
                "*_generous_only rows enter only the generous set"
            ),
        },
        "output": os.path.abspath(output_path),
    }


def assert_bill_index_corpus_basis(
    bill_index_years, network_years, expected_basis
):
    index_bases = {
        receipt["CORPUS_BASIS"] for receipt in bill_index_years.values()
    }
    network_bases = {
        network_years[year]["network"]["CORPUS_BASIS"]
        for year in bill_index_years
    }
    if index_bases != {expected_basis} or network_bases != {expected_basis}:
        raise IngestError(
            "bill index and full-scope network do not share one CORPUS_BASIS"
        )
    if expected_basis == LEGACY_CORPUS_BASIS:
        raise IngestError("bill index CORPUS_BASIS equals the legacy basis")
    return expected_basis


def _clean_csv_row(row, label, line_number):
    # DictReader stores surplus fields as a list under key None.
    if None in row:
        raise IngestError(f"{label} line {line_number} has extra fields")
    return {key: (value or "").strip() for key, value in row.items()}


def _load_repository_csv(path, label, required_fields):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise IngestError(f"cannot read {label} {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(required_fields).issubset(
            reader.fieldnames
        ):
            raise IngestError(
                f"{label} must contain {', '.join(required_fields)}"
            )
        return [
            _clean_csv_row(row, label, reader.line_num)
            for row in reader
        ]


def load_bill_repository(paths):
    membership_path = paths["legislation_membership_edges"]
    data = _read_bytes(membership_path, "legislation membership edges")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"invalid UTF-8 legislation membership edges {membership_path}: {exc}"
        ) from exc
    membership_rows = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestError(
                f"invalid legislation membership edge line {line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise IngestError(
                f"legislation membership edge line {line_number} is not an object"
            )
        required = {
            "canonical_key",
            "category_id",
            "integration_state",
            "measure_weight",
        }
        if not required.issubset(row):
            raise IngestError(
                f"legislation membership edge line {line_number} lacks required fields"
            )
        bill_key_from_canonical(row["canonical_key"])
        membership_rows.append(row)
    audit_rows = _load_repository_csv(
        paths["bills_audit"],
        "bills audit",
        ("section", "bill", "title", "n_cosponsors", "congresses"),
    )
    direction_rows = _load_repository_csv(
        paths["bill_directions"],
        "bill directions",
        ("Bill", "Congress", "Category", "Direction", "Note"),
    )
    source_receipt = {
        "legislation_membership_edges": {
            "path": os.path.abspath(membership_path),
            "md5": file_md5(membership_path),
            "rows": len(membership_rows),
        },
        "bills_audit": {
            "path": os.path.abspath(paths["bills_audit"]),
            "md5": file_md5(paths["bills_audit"]),
            "rows": len(audit_rows),
        },
        "bill_directions": {
            "path": os.path.abspath(paths["bill_directions"]),
            "md5": file_md5(paths["bill_directions"]),
            "rows": len(direction_rows),
        },
    }
    return membership_rows, audit_rows, direction_rows, source_receipt


def _json_distinct_values(rows, field):
    values = sorted({row.get(field, "") for row in rows if row.get(field, "")})
    return (
        json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        if values
        else ""
    )


def _repository_join_coverage(lobby_keys, repository_keys):
    matched = set(lobby_keys) & set(repository_keys)
    lobbying_only = set(lobby_keys) - set(repository_keys)
    repository_only = set(repository_keys) - set(lobby_keys)
    return {
        "repository_distinct_bills": len(repository_keys),
        "repository_bills_with_lobbying": len(matched),
        "repository_bills_without_lobbying": len(repository_only),
        "lobbied_distinct_bills": len(lobby_keys),
        "lobbied_bills_absent_from_repository": len(lobbying_only),
        "unjoinable_keys": {
            "lobbying_not_in_repository": {
                "count": len(lobbying_only),
                "samples": sorted(lobbying_only)[:20],
            },
            "repository_without_lobbying": {
                "count": len(repository_only),
                "samples": sorted(repository_only)[:20],
            },
        },
    }


def build_bill_repository_join(
    bill_index_rows_by_year,
    membership_rows,
    audit_rows,
    direction_rows,
):
    """Build an exact full outer join; neither unjoined side is discarded."""
    lobbying_by_key = {}
    keys_by_year = {}
    for filing_year in sorted(bill_index_rows_by_year):
        year_keys = set()
        seen_org_bills = set()
        for row in bill_index_rows_by_year[filing_year]:
            bill_key = row["bill_key"]
            internal_key = bill_key_from_canonical(bill_key)
            if (
                row.get("congress") != internal_key[2]
                or row.get("bill_type") != internal_key[0].lower()
                or row.get("bill_number") != internal_key[1]
            ):
                raise IngestError(f"bill index row disagrees with {bill_key}")
            org_bill = (row["lob_id"], bill_key)
            if org_bill in seen_org_bills:
                raise IngestError(
                    f"duplicate bill index row for {filing_year} {org_bill!r}"
                )
            seen_org_bills.add(org_bill)
            year_keys.add(bill_key)
            aggregate = lobbying_by_key.setdefault(
                bill_key,
                {
                    "years": set(),
                    "orgs": set(),
                    "rows": 0,
                    "n_filings": 0,
                    "n_activities": 0,
                    "channels": set(),
                },
            )
            aggregate["years"].add(filing_year)
            aggregate["orgs"].add(row["lob_id"])
            aggregate["rows"] += 1
            aggregate["n_filings"] += int(row["n_filings"])
            aggregate["n_activities"] += int(row["n_activities"])
            aggregate["channels"].add(row["channel"])
        keys_by_year[filing_year] = year_keys

    repository_by_key = collections.defaultdict(list)
    for row in membership_rows:
        canonical_key = row.get("canonical_key")
        bill_key_from_canonical(canonical_key)
        repository_by_key[canonical_key].append(row)

    audit_by_key = collections.defaultdict(list)
    audit_unparseable = []
    for row in audit_rows:
        congresses = [
            value
            for value in re.split(r"[;,|/\s]+", row.get("congresses", ""))
            if value
        ]
        if not congresses:
            audit_unparseable.append(f"{row.get('bill', '')}@")
            continue
        for congress in congresses:
            try:
                key = repository_display_bill_key(row.get("bill", ""), congress)
            except IngestError:
                audit_unparseable.append(
                    f"{row.get('bill', '')}@{congress}"
                )
                continue
            audit_by_key[key].append(row)

    directions_by_key = collections.defaultdict(list)
    direction_unparseable = []
    for row in direction_rows:
        try:
            key = repository_display_bill_key(
                row.get("Bill", ""), row.get("Congress", "")
            )
        except IngestError:
            direction_unparseable.append(
                f"{row.get('Bill', '')}@{row.get('Congress', '')}"
            )
            continue
        directions_by_key[key].append(row)

    all_keys = sorted(set(lobbying_by_key) | set(repository_by_key))
    rows = []
    for key in all_keys:
        type_token, number, congress = bill_key_from_canonical(key)
        lobbying = lobbying_by_key.get(key)
        repository_edges = repository_by_key.get(key) or [None]
        audit_matches = audit_by_key.get(key, ())
        direction_matches = directions_by_key.get(key, ())
        if lobbying is not None and key in repository_by_key:
            join_status = "matched"
        elif lobbying is not None:
            join_status = "lobbying_only"
        else:
            join_status = "repository_only"
        metadata = {
            "audit_sections_json": _json_distinct_values(
                audit_matches, "section"
            ),
            "audit_bills_json": _json_distinct_values(audit_matches, "bill"),
            "audit_titles_json": _json_distinct_values(audit_matches, "title"),
            "audit_n_cosponsors_json": _json_distinct_values(
                audit_matches, "n_cosponsors"
            ),
            "audit_congresses_json": _json_distinct_values(
                audit_matches, "congresses"
            ),
            "direction_categories_json": _json_distinct_values(
                direction_matches, "Category"
            ),
            "directions_json": _json_distinct_values(
                direction_matches, "Direction"
            ),
            "direction_notes_json": _json_distinct_values(
                direction_matches, "Note"
            ),
        }
        for edge in sorted(
            repository_edges,
            key=lambda item: (
                "" if item is None else str(item.get("category_id", "")),
                "" if item is None else str(item.get("integration_state", "")),
                "" if item is None else str(item.get("measure_weight", "")),
            ),
        ):
            row = {
                "bill_key": key if lobbying is not None else "",
                "canonical_key": key if edge is not None else "",
                "congress": congress,
                "bill_type": type_token.lower(),
                "bill_number": number,
                "join_status": join_status,
                "repository_present": 1 if edge is not None else 0,
                "lobbying_present": 1 if lobbying is not None else 0,
                "lobbying_years": (
                    "|".join(str(year) for year in sorted(lobbying["years"]))
                    if lobbying is not None
                    else ""
                ),
                "n_lobbying_orgs": (
                    len(lobbying["orgs"]) if lobbying is not None else 0
                ),
                "n_lobbying_rows": (
                    lobbying["rows"] if lobbying is not None else 0
                ),
                "n_filings": (
                    lobbying["n_filings"] if lobbying is not None else 0
                ),
                "n_activities": (
                    lobbying["n_activities"] if lobbying is not None else 0
                ),
                "lobbying_channels": (
                    "|".join(sorted(lobbying["channels"]))
                    if lobbying is not None
                    else ""
                ),
                "category_id": "" if edge is None else edge["category_id"],
                "integration_state": (
                    "" if edge is None else edge["integration_state"]
                ),
                "measure_weight": (
                    "" if edge is None else edge["measure_weight"]
                ),
            }
            row.update(metadata)
            rows.append(row)

    repository_keys = set(repository_by_key)
    audit_only_keys = set(audit_by_key) - repository_keys
    direction_only_keys = set(directions_by_key) - repository_keys
    union_coverage = _repository_join_coverage(
        set(lobbying_by_key), repository_keys
    )
    receipt = dict(union_coverage)
    receipt.update(
        {
            "repository_membership_rows": len(membership_rows),
            "join_rows": len(rows),
            "coverage_by_year": {
                str(year): _repository_join_coverage(keys, repository_keys)
                for year, keys in sorted(keys_by_year.items())
            },
            "metadata_normalization": {
                "bills_audit_unparseable": {
                    "count": len(audit_unparseable),
                    "samples": sorted(set(audit_unparseable))[:20],
                },
                "bill_directions_unparseable": {
                    "count": len(direction_unparseable),
                    "samples": sorted(set(direction_unparseable))[:20],
                },
                "bills_audit_distinct_keys": len(audit_by_key),
                "bills_audit_keys_in_authoritative_repository": len(
                    set(audit_by_key) & repository_keys
                ),
                "bills_audit_keys_absent_from_authoritative_repository": {
                    "count": len(audit_only_keys),
                    "samples": sorted(audit_only_keys)[:20],
                },
                "bill_directions_distinct_keys": len(directions_by_key),
                "bill_directions_keys_in_authoritative_repository": len(
                    set(directions_by_key) & repository_keys
                ),
                "bill_directions_keys_absent_from_authoritative_repository": {
                    "count": len(direction_only_keys),
                    "samples": sorted(direction_only_keys)[:20],
                },
                "fuzzy_matching_used": False,
            },
        }
    )
    return rows, receipt


def _valid_family_start(text, start, s_family):
    if not s_family:
        return True
    return _S_CONTEXT_RE.search(text[:start]) is None


def _followed_by_comma_group(text, start):
    return NEXT_COMMA_GROUP_RE.match(text, start) is not None


def _comma_is_thousands_separator(text, start):
    match = THOUSANDS_GROUP_RE.match(text, start)
    if match is None:
        return False
    return not _followed_by_comma_group(text, match.end())


def _extract_runs_into_title_mentions(text):
    mentions = []
    for type_token, pattern, s_family in RUNS_INTO_TITLE_PATTERNS:
        for match in pattern.finditer(text):
            if not _valid_family_start(text, match.start(), s_family):
                continue
            mentions.append(
                {
                    "type": type_token,
                    "number": int(match.group("number")),
                    "verbatim": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                    "_recovery_kinds": ["runs_into_title"],
                }
            )
    return mentions


def _public_bill_mention(mention):
    return {
        key: value
        for key, value in mention.items()
        if key != "_recovery_kinds"
    }


def _valid_dotless_bare_s_start(text, start):
    """Apply the existing S-family guard plus the stricter bare-S boundary."""
    if not _valid_family_start(text, start, True):
        return False
    if _BILL_START_GUARD_RE.search(text[:start]) is None:
        return False
    if start == 0:
        return True
    preceding = text[start - 1]
    return not (
        re.match(r"\w", preceding) is not None
        or preceding == "."
        or "\N{HYPHEN}" <= preceding <= "\N{HORIZONTAL BAR}"
    )


def _has_formal_citing_context(text):
    for _type_token, pattern, s_family in FORMAL_CITING_CONTEXT_PATTERNS:
        for match in pattern.finditer(text):
            if _valid_family_start(text, match.start(), s_family):
                return True
    return False


def _dotless_bare_s_context(text, start, end):
    return text[max(0, start - 60):min(len(text), end + 60)]


def _senate_bill_maximum(bill_people_maps, congress):
    congress_map = bill_people_maps.get(congress)
    if congress_map is None:
        return None
    numbers = [
        number
        for type_token, number, key_congress in congress_map
        if type_token == "S" and key_congress == congress
    ]
    return max(numbers) if numbers else None


def _resolve_mention_congress(
    filing_congress,
    congress_start_year,
    year_suffixes,
    mention_end,
):
    following_suffix = next(
        (
            suffix
            for suffix in year_suffixes
            if suffix["start"] >= mention_end
        ),
        None,
    )
    resolved_year = (
        following_suffix["year"]
        if following_suffix is not None
        else None
    )
    if resolved_year is not None and resolved_year < congress_start_year:
        return congress_for_calendar_year(resolved_year), resolved_year
    return filing_congress, resolved_year


def _recover_dotless_bare_s_mentions(
    description,
    filing_congress,
    congress_start_year,
    bill_people_maps,
):
    """Recover gated bare-S bills under the existing Congress assignment."""
    has_citing_context = _has_formal_citing_context(description)
    accepted = []
    recovered = []
    rejected = []
    segments = description.split(";")
    segment_start = 0
    segment_ranges = []
    for index, segment in enumerate(segments):
        segment_end = segment_start + len(segment)
        year_suffixes = [
            {
                "year": int(match.group(1)),
                "start": match.start(),
            }
            for match in STALE_CITATION_YEAR_RE.finditer(segment)
        ]
        segment_ranges.append(
            (segment_start, segment_end, year_suffixes)
        )
        if index < len(segments) - 1:
            segment_start = segment_end + 1

    for match in DOTLESS_BARE_S_SCAN_RE.finditer(description):
        number_text = match.group("number")
        if match.end() < len(description):
            following = description[match.end()]
            if (
                re.match(r"\w", following) is not None
                and re.match(
                    r"[A-Za-z]{2}", description[match.end():]
                ) is None
            ):
                continue
        if _comma_is_thousands_separator(description, match.end()):
            continue

        number = int(number_text)
        resolved_congress = filing_congress
        for current_start, current_end, year_suffixes in segment_ranges:
            if not current_start <= match.start() < current_end:
                continue
            mention_end = match.end() - current_start
            resolved_congress, _resolved_year = (
                _resolve_mention_congress(
                    filing_congress,
                    congress_start_year,
                    year_suffixes,
                    mention_end,
                )
            )
            break

        bill_key = ("S", number, resolved_congress)
        congress_map = bill_people_maps.get(resolved_congress)
        senate_maximum = _senate_bill_maximum(
            bill_people_maps, resolved_congress
        )
        condition_passes = {
            "negative_lookbehind": _valid_dotless_bare_s_start(
                description, match.start()
            ),
            "billstatus_existence": (
                congress_map is not None and bill_key in congress_map
            ),
            "citing_context_proof": has_citing_context,
            "range_sanity": (
                len(number_text) <= 4
                and senate_maximum is not None
                and 1 <= number <= senate_maximum
            ),
        }
        failed_conditions = [
            condition
            for condition in DOTLESS_BARE_S_REJECTION_CONDITIONS
            if not condition_passes[condition]
        ]
        sample = {
            "matched_text": match.group(0),
            "context": _dotless_bare_s_context(
                description, match.start(), match.end()
            ),
        }
        bill_key_row = _sorted_bill_key_rows({bill_key})[0]
        if failed_conditions:
            rejected_sample = dict(sample)
            rejected_sample["candidate_bill_key"] = bill_key_row
            rejected_sample["failed_conditions"] = failed_conditions
            rejected.append(rejected_sample)
            continue

        accepted.append(
            {
                "type": "S",
                "number": number,
                "verbatim": match.group(0),
                "start": match.start(),
                "end": match.end(),
            }
        )
        recovered_sample = dict(sample)
        recovered_sample["resolved_bill_key"] = bill_key_row
        recovered.append(recovered_sample)
    return accepted, recovered, rejected


def extract_bill_mentions(text):
    """Return (parsed mentions, unparsed formal-looking mentions)."""
    if not isinstance(text, str):
        raise IngestError("bill extraction input is not a string")
    parsed = []
    for type_token, pattern, s_family in BILL_PATTERNS:
        for match in pattern.finditer(text):
            if not _valid_family_start(text, match.start(), s_family):
                continue
            if _comma_is_thousands_separator(text, match.end()):
                continue
            parsed.append(
                {
                    "type": type_token,
                    "number": int(match.group("number")),
                    "verbatim": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                }
            )
            cursor = match.end()
            while True:
                if _comma_is_thousands_separator(text, cursor):
                    break
                continuation = CONTINUATION_RE.match(text, cursor)
                if continuation is None:
                    break
                if _comma_is_thousands_separator(text, continuation.end()):
                    break
                parsed.append(
                    {
                        "type": type_token,
                        "number": int(continuation.group("number")),
                        "verbatim": continuation.group("number"),
                        "start": continuation.start("number"),
                        "end": continuation.end("number"),
                    }
                )
                cursor = continuation.end()
    parsed.sort(key=lambda item: (item["start"], item["end"], item["type"]))
    for left, right in zip(parsed, parsed[1:]):
        if right["start"] < left["end"]:
            raise IngestError(
                "bill regex overlap between "
                f"{left['verbatim']!r} and {right['verbatim']!r}"
            )

    spans = [(item["start"], item["end"]) for item in parsed]
    unparsed = []
    for match in BILL_LIKE_RE.finditer(text):
        start, end = match.span()
        verbatim = match.group(0)
        if BARE_CONCATENATED_S1_RE.fullmatch(verbatim):
            continue
        if verbatim[0].upper() == "S" and not _valid_family_start(
            text, start, True
        ):
            continue
        if any(start < parsed_end and end > parsed_start for parsed_start, parsed_end in spans):
            continue
        unparsed.append(
            {"verbatim": verbatim, "start": start, "end": end}
        )
    return parsed, unparsed


def _normalized_text_with_spans(text):
    if not isinstance(text, str):
        raise IngestError("title normalization input is not a string")
    words = [
        (match.group(0).upper(), match.start(), match.end())
        for match in TITLE_WORD_RE.finditer(text)
    ]
    if words and words[0][0] == "THE":
        words = words[1:]
    normalized_parts = []
    source_spans = []
    cursor = 0
    for word, source_start, source_end in words:
        if normalized_parts:
            cursor += 1
        normalized_start = cursor
        normalized_parts.append(word)
        cursor += len(word)
        source_spans.append(
            (normalized_start, cursor, source_start, source_end)
        )
    return " ".join(normalized_parts), source_spans


def normalize_title(text):
    """Normalize a bill title or activity description for title matching."""
    normalized, _source_spans = _normalized_text_with_spans(text)
    return normalized


def title_index_forms(text):
    """Return the full normalized title and any pinned yearless variant."""
    normalized = normalize_title(text)
    if not normalized:
        return ()
    forms = [normalized]
    if TITLE_YEAR_SUFFIX_RE.search(normalized):
        yearless = TITLE_YEAR_SUFFIX_RE.sub("", normalized).strip()
        if yearless and yearless != normalized:
            forms.append(yearless)
    return tuple(forms)


def title_is_eligible(normalized_title):
    return (
        len(normalized_title) >= 12
        and len(normalized_title.split()) >= 3
    )


def _index_bill_titles(bill_key, raw_titles, title_index):
    excluded = 0
    forms_seen_for_bill = set()
    for raw_title in raw_titles:
        for normalized_title in title_index_forms(raw_title):
            if normalized_title in forms_seen_for_bill:
                continue
            forms_seen_for_bill.add(normalized_title)
            if not title_is_eligible(normalized_title):
                excluded += 1
                continue
            title_index[normalized_title].add(bill_key)
    return excluded


class TitleIndex(dict):
    """Title dictionary with a first-word candidate index."""

    def __init__(self, titles, typed_titles_by_bill=None):
        super().__init__(titles)
        self.typed_titles_by_bill = (
            {} if typed_titles_by_bill is None else typed_titles_by_bill
        )
        candidates = collections.defaultdict(list)
        for normalized_title in self:
            first_word = normalized_title.split(" ", 1)[0]
            candidates[first_word].append(normalized_title)
        self.by_first_word = {
            first_word: tuple(sorted(values))
            for first_word, values in candidates.items()
        }


def finalize_title_index(title_index, typed_titles_by_bill=None):
    if isinstance(title_index, TitleIndex):
        return title_index
    return TitleIndex(
        {
            normalized_title: set(bill_keys)
            for normalized_title, bill_keys in title_index.items()
        },
        typed_titles_by_bill=typed_titles_by_bill,
    )


def build_title_index(titles_by_bill):
    """Build a normalized title-to-bills dictionary from synthetic or XML data."""
    title_index = collections.defaultdict(set)
    excluded = 0
    for bill_key in sorted(titles_by_bill):
        excluded += _index_bill_titles(
            bill_key, titles_by_bill[bill_key], title_index
        )
    finalized = finalize_title_index(title_index)
    return finalized, {
        "titles_in_dictionary": len(title_index),
        "titles_excluded_by_guard": excluded,
    }


def _source_verbatim(text, source_spans, start, end):
    first = next(
        (item for item in source_spans if item[0] == start), None
    )
    last = next(
        (item for item in reversed(source_spans) if item[1] == end), None
    )
    if first is None or last is None:
        raise IngestError("internal title-match boundary is not token-aligned")
    return text[first[2]:last[3]]


def match_bill_titles(text, title_index):
    """Match eligible indexed titles with longest-overlap precedence."""
    normalized, source_spans = _normalized_text_with_spans(text)
    if not normalized or not title_index:
        return {
            "bill_keys": set(),
            "unique_matches": [],
            "ambiguous_matches": [],
        }

    indexed = finalize_title_index(title_index)
    candidates = []
    for normalized_start, normalized_end, _source_start, _source_end in (
        source_spans
    ):
        first_word = normalized[normalized_start:normalized_end]
        for normalized_title in indexed.by_first_word.get(first_word, ()):
            start = normalized_start
            end = start + len(normalized_title)
            if normalized[start:end] != normalized_title:
                continue
            if end < len(normalized) and normalized[end] != " ":
                continue
            candidates.append(
                {
                    "start": start,
                    "end": end,
                    "normalized_title": normalized_title,
                    "bill_keys": set(indexed[normalized_title]),
                }
            )

    candidates.sort(
        key=lambda item: (
            -(item["end"] - item["start"]),
            item["start"],
            item["normalized_title"],
        )
    )
    accepted = []
    for candidate in candidates:
        candidate_length = candidate["end"] - candidate["start"]
        shadowed = any(
            candidate["start"] < prior["end"]
            and candidate["end"] > prior["start"]
            and prior["end"] - prior["start"] > candidate_length
            for prior in accepted
        )
        if not shadowed:
            accepted.append(candidate)
    accepted.sort(
        key=lambda item: (
            item["start"], item["end"], item["normalized_title"]
        )
    )

    bill_keys = set()
    unique_matches = []
    ambiguous_matches = []
    for item in accepted:
        credited = set(item["bill_keys"])
        bill_keys.update(credited)
        row = {
            "verbatim": _source_verbatim(
                text, source_spans, item["start"], item["end"]
            ),
            "bill_keys": _sorted_bill_key_rows(credited),
        }
        if len(credited) > 1:
            ambiguous_matches.append(row)
        else:
            unique_matches.append(row)
    return {
        "bill_keys": bill_keys,
        "unique_matches": unique_matches,
        "ambiguous_matches": ambiguous_matches,
    }


def _sorted_bill_key_rows(keys):
    return [
        {
            "type": type_token,
            "number": number,
            "congress": congress,
        }
        for type_token, number, congress in sorted(set(keys))
    ]


def _bill_people_size_rows(keys, bill_people_maps):
    rows = []
    for bill_key in sorted(set(keys)):
        type_token, number, congress = bill_key
        people_map = bill_people_maps.get(congress)
        if people_map is None or bill_key not in people_map:
            raise IngestError(
                f"credited bill {type_token} {number} in Congress "
                f"{congress} lacks a people-set"
            )
        row = _sorted_bill_key_rows({bill_key})[0]
        row["people_set_size"] = len(people_map[bill_key])
        rows.append(row)
    return rows


def _registrant_name(payload, report_uuid):
    if "registrant" not in payload:
        raise IngestError(f"filing {report_uuid} missing key registrant")
    registrant = payload["registrant"]
    if not isinstance(registrant, dict):
        raise IngestError(
            f"filing {report_uuid} key registrant is not an object"
        )
    if "name" not in registrant:
        raise IngestError(
            f"filing {report_uuid} missing key registrant.name"
        )
    name = registrant["name"]
    if not isinstance(name, str) or not name.strip():
        raise IngestError(
            f"filing {report_uuid} key registrant.name is not a nonempty string"
        )
    return name.strip()


def _title_match_sort_key(item):
    return (
        item["verbatim"].upper(),
        item["verbatim"],
        tuple(
            (bill["type"], bill["number"], bill["congress"])
            for bill in item["bill_keys"]
        ),
    )


def _sourced_title_match_sort_key(item):
    return item["report_uuid"], _title_match_sort_key(item)


def _bill_keys_from_rows(rows):
    return {
        (row["type"], row["number"], row["congress"])
        for row in rows
    }


def _empty_dotless_bare_s_audit():
    return {
        "recovered_count": 0,
        "recovered_samples": [],
        "rejected_count": 0,
        "rejected_by_condition": {
            condition: {"count": 0, "samples": []}
            for condition in DOTLESS_BARE_S_REJECTION_CONDITIONS
        },
    }


def _append_dotless_bare_s_sample(destination, sample):
    if len(destination) < DOTLESS_BARE_S_SAMPLE_LIMIT:
        destination.append(dict(sample))


def _record_dotless_bare_s_audit(audit, activity_result, filing_uuid):
    for sample in activity_result["dotless_bare_s_recovered"]:
        sourced = {"filing_uuid": filing_uuid}
        sourced.update(sample)
        audit["recovered_count"] += 1
        _append_dotless_bare_s_sample(
            audit["recovered_samples"], sourced
        )
    for sample in activity_result["dotless_bare_s_rejected"]:
        sourced = {"filing_uuid": filing_uuid}
        sourced.update(sample)
        audit["rejected_count"] += 1
        for condition in sample["failed_conditions"]:
            bucket = audit["rejected_by_condition"][condition]
            bucket["count"] += 1
            _append_dotless_bare_s_sample(bucket["samples"], sourced)


def _merge_dotless_bare_s_audits(destination, source):
    destination["recovered_count"] += source["recovered_count"]
    for sample in source["recovered_samples"]:
        _append_dotless_bare_s_sample(
            destination["recovered_samples"], sample
        )
    destination["rejected_count"] += source["rejected_count"]
    for condition in DOTLESS_BARE_S_REJECTION_CONDITIONS:
        destination_bucket = destination["rejected_by_condition"][
            condition
        ]
        source_bucket = source["rejected_by_condition"][condition]
        destination_bucket["count"] += source_bucket["count"]
        for sample in source_bucket["samples"]:
            _append_dotless_bare_s_sample(
                destination_bucket["samples"], sample
            )


def _dotless_bare_s_receipts(audit):
    return {
        "dotless_bare_s_recovered": {
            "count": audit["recovered_count"],
            "samples": list(audit["recovered_samples"]),
        },
        "dotless_bare_s_rejected": {
            "count": audit["rejected_count"],
            "by_failed_condition": {
                condition: {
                    "count": audit["rejected_by_condition"][condition][
                        "count"
                    ],
                    "samples": list(
                        audit["rejected_by_condition"][condition][
                            "samples"
                        ]
                    ),
                }
                for condition in DOTLESS_BARE_S_REJECTION_CONDITIONS
            },
        },
    }


def _empty_mechanical_citation_audit():
    return {
        recovery_kind: {"count": 0, "samples": []}
        for recovery_kind in MECHANICAL_CITATION_RECOVERY_KINDS
    }


def _record_mechanical_citation_audit(
    audit,
    activity_result,
    filing_uuid,
):
    for recovery_kind in MECHANICAL_CITATION_RECOVERY_KINDS:
        result_field = recovery_kind + "_recovered"
        for sample in activity_result[result_field]:
            sourced = {"filing_uuid": filing_uuid}
            sourced.update(sample)
            bucket = audit[recovery_kind]
            bucket["count"] += 1
            _append_dotless_bare_s_sample(bucket["samples"], sourced)


def _merge_mechanical_citation_audits(destination, source):
    for recovery_kind in MECHANICAL_CITATION_RECOVERY_KINDS:
        destination_bucket = destination[recovery_kind]
        source_bucket = source[recovery_kind]
        destination_bucket["count"] += source_bucket["count"]
        for sample in source_bucket["samples"]:
            _append_dotless_bare_s_sample(
                destination_bucket["samples"], sample
            )


def _mechanical_citation_receipts(audit):
    return {
        recovery_kind + "_recovered": {
            "count": audit[recovery_kind]["count"],
            "samples": list(audit[recovery_kind]["samples"]),
        }
        for recovery_kind in MECHANICAL_CITATION_RECOVERY_KINDS
    }


def _record_mechanical_recovery(
    description,
    mention,
    bill_key,
    recovered_by_kind,
):
    recovery_kinds = mention.get("_recovery_kinds", ())
    if not recovery_kinds:
        return
    sample = {
        "matched_text": mention["verbatim"],
        "context": _dotless_bare_s_context(
            description, mention["start"], mention["end"]
        ),
        "resolved_bill_key": _sorted_bill_key_rows({bill_key})[0],
    }
    for recovery_kind in recovery_kinds:
        recovered_by_kind[recovery_kind].append(dict(sample))


def resolve_activity_mentions(
    description,
    title_index,
    filing_year,
    bill_people_maps,
):
    """Resolve one activity, including original-Congress stale citations."""
    filing_year = validate_filing_year(filing_year, "activity")
    filing_congress = congress_for_year(filing_year)
    congress_start_year = congress_first_year(filing_year)
    parsed, unparsed = extract_bill_mentions(description)
    runs_into_title_mentions = _extract_runs_into_title_mentions(
        description
    )
    (
        dotless_bare_s_mentions,
        dotless_bare_s_recovered,
        dotless_bare_s_rejected,
    ) = _recover_dotless_bare_s_mentions(
        description,
        filing_congress,
        congress_start_year,
        bill_people_maps,
    )
    recovered_mentions = (
        runs_into_title_mentions + dotless_bare_s_mentions
    )
    if recovered_mentions:
        recovered_spans = [
            (mention["start"], mention["end"])
            for mention in recovered_mentions
        ]
        unparsed = [
            item
            for item in unparsed
            if not any(
                item["start"] < recovered_end
                and item["end"] > recovered_start
                for recovered_start, recovered_end in recovered_spans
            )
        ]
        parsed.extend(recovered_mentions)
        parsed.sort(
            key=lambda item: (item["start"], item["end"], item["type"])
        )
    title_result = match_bill_titles(description, title_index)
    generous_title_keys = set(title_result["bill_keys"])
    formal_keys = set()
    credited_formal_keys = set()
    strict_title_keys = set()
    resolved_parsed = []
    stale_citations = []
    stale_unresolvable = []
    mechanical_recovered = {
        kind: [] for kind in MECHANICAL_CITATION_RECOVERY_KINDS
    }
    segment_start = 0
    segments = description.split(";")
    for index, segment in enumerate(segments):
        segment_end = segment_start + len(segment)
        segment_mentions = [
            mention
            for mention in parsed
            if segment_start <= mention["start"] < segment_end
        ]
        segment_title_result = match_bill_titles(segment, title_index)
        segment_title_keys = (
            set(segment_title_result["bill_keys"])
            & generous_title_keys
        )
        year_suffixes = [
            {
                "year": int(match.group(1)),
                "start": match.start(),
            }
            for match in STALE_CITATION_YEAR_RE.finditer(segment)
        ]
        segment_formal_keys = set()
        has_stale_mention = False
        current_mentions = []
        for mention in segment_mentions:
            mention_end = mention["end"] - segment_start
            resolved_congress, resolved_year = _resolve_mention_congress(
                filing_congress,
                congress_start_year,
                year_suffixes,
                mention_end,
            )
            if (
                resolved_year is not None
                and resolved_year < congress_start_year
            ):
                has_stale_mention = True
                historical_map = bill_people_maps.get(resolved_congress)
                supported_types = SUPPORTED_BILL_TYPES_BY_CONGRESS.get(
                    resolved_congress, frozenset()
                )
                bill_key = (
                    mention["type"],
                    mention["number"],
                    resolved_congress,
                )
                formal_keys.add(bill_key)
                segment_formal_keys.add(bill_key)
                resolved_mention = _public_bill_mention(mention)
                resolved_mention["congress"] = resolved_congress
                resolved_parsed.append(resolved_mention)
                receipt_base = {
                    "verbatim_segment": segment.strip(),
                    "source_years": [resolved_year],
                    "filing_congress": filing_congress,
                    "resolved_congress": resolved_congress,
                    "cited_key": _sorted_bill_key_rows({bill_key})[0],
                }
                if historical_map is None:
                    unresolved = dict(receipt_base)
                    unresolved["reason"] = "unsupported_congress"
                    stale_unresolvable.append(unresolved)
                    continue
                if mention["type"] not in supported_types:
                    unresolved = dict(receipt_base)
                    unresolved["reason"] = "unsupported_bill_type"
                    stale_unresolvable.append(unresolved)
                    continue
                if bill_key not in historical_map:
                    unresolved = dict(receipt_base)
                    unresolved["reason"] = "bill_not_found"
                    stale_unresolvable.append(unresolved)
                    continue
                credited_formal_keys.add(bill_key)
                _record_mechanical_recovery(
                    description,
                    mention,
                    bill_key,
                    mechanical_recovered,
                )
                generous_stale_keys = {bill_key} | segment_title_keys
                stale_receipt = dict(receipt_base)
                stale_receipt.update(
                    {
                        "resolved_bill_key": _sorted_bill_key_rows(
                            {bill_key}
                        )[0],
                        "credited_people_set_size": len(
                            historical_map[bill_key]
                        ),
                        "credited_keys_generous": _sorted_bill_key_rows(
                            generous_stale_keys
                        ),
                        "credited_keys_strict": _sorted_bill_key_rows(
                            {bill_key}
                        ),
                        "credited_people_set_sizes_generous": (
                            _bill_people_size_rows(
                                generous_stale_keys, bill_people_maps
                            )
                        ),
                        "credited_people_set_sizes_strict": (
                            _bill_people_size_rows(
                                {bill_key}, bill_people_maps
                            )
                        ),
                    }
                )
                stale_citations.append(stale_receipt)
            else:
                current_mentions.append(mention)

        for mention in current_mentions:
            bill_key = (
                mention["type"],
                mention["number"],
                filing_congress,
            )
            formal_keys.add(bill_key)
            credited_formal_keys.add(bill_key)
            segment_formal_keys.add(bill_key)
            resolved_mention = _public_bill_mention(mention)
            resolved_mention["congress"] = filing_congress
            resolved_parsed.append(resolved_mention)
            _record_mechanical_recovery(
                description,
                mention,
                bill_key,
                mechanical_recovered,
            )

        if not has_stale_mention:
            for title_match in segment_title_result["unique_matches"]:
                strict_title_keys.update(
                    _bill_keys_from_rows(title_match["bill_keys"])
                    & generous_title_keys
                )
            for title_match in segment_title_result["ambiguous_matches"]:
                candidate_keys = (
                    _bill_keys_from_rows(title_match["bill_keys"])
                    & generous_title_keys
                )
                disambiguated = candidate_keys & segment_formal_keys
                if len(disambiguated) == 1:
                    strict_title_keys.update(disambiguated)
        if index < len(segments) - 1:
            segment_start = segment_end + 1

    resolved_parsed.sort(
        key=lambda item: (item["start"], item["end"], item["type"])
    )
    return {
        "parsed": resolved_parsed,
        "unparsed": unparsed,
        "title_result": title_result,
        "formal_keys": formal_keys,
        "credited_formal_keys": credited_formal_keys,
        "generous_title_keys": generous_title_keys,
        "strict_title_keys": strict_title_keys,
        "generous_bill_keys": credited_formal_keys | generous_title_keys,
        "strict_bill_keys": credited_formal_keys | strict_title_keys,
        "stale_citations": stale_citations,
        "stale_unresolvable": stale_unresolvable,
        "dotless_bare_s_recovered": dotless_bare_s_recovered,
        "dotless_bare_s_rejected": dotless_bare_s_rejected,
        "runs_into_title_recovered": mechanical_recovered[
            "runs_into_title"
        ],
    }


def parse_filings(
    worklist,
    filings_dir=None,
    title_index=None,
    filing_paths=None,
    default_filing_year=2021,
    bill_people_maps=None,
    collect_quarterly=False,
):
    title_index = {} if title_index is None else title_index
    bill_people_maps = (
        {} if bill_people_maps is None else bill_people_maps
    )
    bills_by_lob = collections.defaultdict(set)
    bills_by_lob_strict = collections.defaultdict(set)
    bills_by_lob_quarter_strict = collections.defaultdict(set)
    filings_by_quarter = collections.Counter()
    parsed_mentions = []
    unparsed_counter = collections.Counter()
    per_filing_mentions = {}
    congress_contexts = []
    unique_title_matches = []
    ambiguous_title_matches = []
    stale_citations = []
    stale_unresolvable = []
    dotless_bare_s_audit = _empty_dotless_bare_s_audit()
    mechanical_citation_audit = _empty_mechanical_citation_audit()
    amendment_groups = collections.defaultdict(
        lambda: {"originals": [], "amendments": []}
    )
    activities_count = 0
    no_activity_count = 0
    amendment_count = 0
    filings_with_title_matches = 0
    bills_added_by_title_channel = 0

    for record in worklist:
        report_uuid = record["report_uuid"]
        record_year = validate_filing_year(
            record.get("filing_year", default_filing_year), report_uuid
        )
        if record["is_no_activity"]:
            no_activity_count += 1
        if record["is_amendment"]:
            amendment_count += 1
        if filing_paths is not None:
            filing_path = filing_paths.get(report_uuid)
            if not filing_path:
                raise IngestError(
                    f"no resolved filing path for {report_uuid}"
                )
        else:
            if filings_dir is None:
                raise IngestError("parse_filings requires a filings directory")
            filing_path = os.path.join(filings_dir, report_uuid + ".json")
        payload = _decode_json(
            _read_bytes(filing_path, f"filing {report_uuid}"),
            f"filing {report_uuid}",
        )
        if not isinstance(payload, dict):
            raise IngestError(f"filing {report_uuid} is not an object")
        if "lobbying_activities" not in payload:
            raise IngestError(
                f"filing {report_uuid} missing key lobbying_activities"
            )
        activities = payload["lobbying_activities"]
        if not isinstance(activities, list):
            raise IngestError(
                f"filing {report_uuid} key lobbying_activities is not a list"
            )
        if record["is_no_activity"] and activities:
            raise IngestError(
                f"filing {report_uuid} is_no_activity=true but "
                "lobbying_activities is nonempty"
            )

        registrant = _registrant_name(payload, report_uuid)
        period = record["filing_period_code"]
        if not isinstance(period, str) or not period:
            raise IngestError(
                f"worklist UUID {report_uuid} has invalid filing_period_code"
            )
        quarter = None
        if collect_quarterly:
            quarter = filing_period_to_quarter(period)
            filings_by_quarter[quarter] += 1
        group_key = (record["lob_id"], registrant, period)
        group_side = "amendments" if record["is_amendment"] else "originals"
        amendment_groups[group_key][group_side].append(report_uuid)

        filing_formal_bills = set()
        filing_credited_formal_bills = set()
        filing_title_bills = set()
        filing_bills = set()
        filing_bills_strict = set()
        filing_title_matches = []
        filing_unparsed = []
        for index, activity in enumerate(activities):
            activities_count += 1
            if not isinstance(activity, dict):
                raise IngestError(
                    f"filing {report_uuid} lobbying_activities[{index}] "
                    "is not an object"
                )
            if "description" not in activity:
                raise IngestError(
                    f"filing {report_uuid} lobbying_activities[{index}] "
                    "missing key description"
                )
            description = activity["description"]
            if not isinstance(description, str):
                raise IngestError(
                    f"filing {report_uuid} lobbying_activities[{index}].description "
                    "is not a string"
                )
            for context_match in CONGRESS_CONTEXT_RE.finditer(description):
                congress_contexts.append(
                    {
                        "report_uuid": report_uuid,
                        "matched_text": context_match.group(0),
                    }
                )
            activity_result = resolve_activity_mentions(
                description,
                title_index,
                record_year,
                bill_people_maps,
            )
            _record_dotless_bare_s_audit(
                dotless_bare_s_audit, activity_result, report_uuid
            )
            _record_mechanical_citation_audit(
                mechanical_citation_audit,
                activity_result,
                report_uuid,
            )
            filing_formal_bills.update(activity_result["formal_keys"])
            filing_credited_formal_bills.update(
                activity_result["credited_formal_keys"]
            )
            filing_bills.update(activity_result["generous_bill_keys"])
            filing_bills_strict.update(activity_result["strict_bill_keys"])
            for mention in activity_result["parsed"]:
                mention_with_source = dict(mention)
                mention_with_source["report_uuid"] = report_uuid
                mention_with_source["lob_id"] = record["lob_id"]
                mention_with_source["filing_year"] = record_year
                parsed_mentions.append(mention_with_source)
            filing_unparsed.extend(
                item["verbatim"] for item in activity_result["unparsed"]
            )
            unparsed_counter.update(
                item["verbatim"] for item in activity_result["unparsed"]
            )

            title_result = activity_result["title_result"]
            filing_title_bills.update(title_result["bill_keys"])
            for field, destination in (
                ("unique_matches", unique_title_matches),
                ("ambiguous_matches", ambiguous_title_matches),
            ):
                for title_match in title_result[field]:
                    filing_title_matches.append(dict(title_match))
                    sourced_match = dict(title_match)
                    sourced_match["report_uuid"] = report_uuid
                    destination.append(sourced_match)
            for field, destination in (
                ("stale_citations", stale_citations),
                ("stale_unresolvable", stale_unresolvable),
            ):
                for stale_receipt in activity_result[field]:
                    sourced_receipt = dict(stale_receipt)
                    sourced_receipt["report_uuid"] = report_uuid
                    sourced_receipt["lob_id"] = record["lob_id"]
                    destination.append(sourced_receipt)

        if filing_title_matches:
            filings_with_title_matches += 1
        bills_added_by_title_channel += len(
            filing_title_bills - filing_credited_formal_bills
        )
        bills_by_lob[record["lob_id"]].update(filing_bills)
        bills_by_lob_strict[record["lob_id"]].update(filing_bills_strict)
        if collect_quarterly:
            bills_by_lob_quarter_strict[
                (record["lob_id"], quarter)
            ].update(filing_bills_strict)
        per_filing_mentions[report_uuid] = {
            "lob_id": record["lob_id"],
            "parsed_bill_keys": _sorted_bill_key_rows(filing_formal_bills),
            "matched_titles": sorted(
                filing_title_matches, key=_title_match_sort_key
            ),
            "unparsed_items": sorted(
                filing_unparsed, key=lambda item: (item.lower(), item)
            ),
        }

    mixed_amendment_groups = []
    for (lob_id, registrant, period), members in sorted(
        amendment_groups.items()
    ):
        if not members["originals"] or not members["amendments"]:
            continue
        mixed_amendment_groups.append(
            {
                "lob_id": lob_id,
                "registrant": registrant,
                "period": period,
                "original_report_uuids": sorted(members["originals"]),
                "amendment_report_uuids": sorted(members["amendments"]),
            }
        )

    distinct_bills = set()
    for lob_bills in bills_by_lob.values():
        distinct_bills.update(lob_bills)
    distinct_bills_strict = set()
    for lob_bills in bills_by_lob_strict.values():
        distinct_bills_strict.update(lob_bills)

    result = {
        "filings_parsed": len(worklist),
        "no_activity_filings": no_activity_count,
        "amendment_filings": amendment_count,
        "original_and_amendment_groups": mixed_amendment_groups,
        "activities": activities_count,
        "raw_mentions": len(parsed_mentions),
        "parsed_mentions": parsed_mentions,
        "unparsed_mentions": unparsed_counter,
        "per_filing_mentions": per_filing_mentions,
        "congress_context_mentions": sorted(
            congress_contexts,
            key=lambda item: (item["report_uuid"], item["matched_text"]),
        ),
        "stale_citations": sorted(
            stale_citations,
            key=lambda item: (
                item["report_uuid"],
                item["verbatim_segment"],
                item["resolved_congress"],
                item["cited_key"]["type"],
                item["cited_key"]["number"],
            ),
        ),
        "stale_unresolvable": sorted(
            stale_unresolvable,
            key=lambda item: (
                item["report_uuid"],
                item["verbatim_segment"],
                item["resolved_congress"],
                item["cited_key"]["type"],
                item["cited_key"]["number"],
                item["reason"],
            ),
        ),
        "title_channel": {
            "filings_with_title_matches": filings_with_title_matches,
            "unique_title_matches": len(unique_title_matches),
            "ambiguous_title_matches": len(ambiguous_title_matches),
            "bills_added_by_title_channel": bills_added_by_title_channel,
            "unique_matches": sorted(
                unique_title_matches, key=_sourced_title_match_sort_key
            ),
            "ambiguous_matches": sorted(
                ambiguous_title_matches, key=_sourced_title_match_sort_key
            ),
        },
        "bills_by_lob": dict(bills_by_lob),
        "bills_by_lob_strict": dict(bills_by_lob_strict),
        "distinct_bills": distinct_bills,
        "distinct_bills_strict": distinct_bills_strict,
    }
    result.update(_dotless_bare_s_receipts(dotless_bare_s_audit))
    result.update(
        _mechanical_citation_receipts(mechanical_citation_audit)
    )
    if collect_quarterly:
        result.update(
            {
                "bills_by_lob_quarter_strict": dict(
                    bills_by_lob_quarter_strict
                ),
                "filings_by_quarter": {
                    quarter: filings_by_quarter[quarter]
                    for quarter in range(1, 5)
                },
                "filings_unassigned_to_quarter": 0,
            }
        )
    return result


def filing_bill_sets(
    payload,
    filing_uuid,
    filing_year,
    title_index,
    bill_people_maps,
    collect_bill_index=False,
):
    """Extract generous and strict bill sets from one in-memory filing."""
    activities = _validated_activities(payload, filing_uuid)
    generous = set()
    strict = set()
    raw_mentions = 0
    title_matches = 0
    stale_citations = 0
    stale_unresolvable = 0
    dotless_bare_s_audit = _empty_dotless_bare_s_audit()
    mechanical_citation_audit = _empty_mechanical_citation_audit()
    bill_observations = collections.defaultdict(
        lambda: {"n_activities": 0, "sources": set(), "strict": False}
    )
    for activity in activities:
        result = resolve_activity_mentions(
            activity["description"],
            title_index,
            filing_year,
            bill_people_maps,
        )
        _record_dotless_bare_s_audit(
            dotless_bare_s_audit, result, filing_uuid
        )
        _record_mechanical_citation_audit(
            mechanical_citation_audit, result, filing_uuid
        )
        generous.update(result["generous_bill_keys"])
        strict.update(result["strict_bill_keys"])
        if collect_bill_index:
            for bill_key in result["generous_bill_keys"]:
                observation = bill_observations[bill_key]
                observation["n_activities"] += 1
                if bill_key in result["credited_formal_keys"]:
                    observation["sources"].add("formal")
                if bill_key in result["generous_title_keys"]:
                    observation["sources"].add("title")
                if bill_key in result["strict_bill_keys"]:
                    observation["strict"] = True
        raw_mentions += len(result["parsed"])
        title_matches += len(result["title_result"]["unique_matches"])
        title_matches += len(result["title_result"]["ambiguous_matches"])
        stale_citations += len(result["stale_citations"])
        stale_unresolvable += len(result["stale_unresolvable"])
    return {
        "generous": generous,
        "strict": strict,
        "activities": len(activities),
        "raw_mentions": raw_mentions,
        "title_matches": title_matches,
        "stale_citations": stale_citations,
        "stale_unresolvable": stale_unresolvable,
        "bill_observations": dict(bill_observations),
        "_dotless_bare_s_audit": dotless_bare_s_audit,
        "_mechanical_citation_audit": mechanical_citation_audit,
    }


def aggregate_sweep_network(
    page_paths,
    filing_year,
    attribution_by_uuid,
    title_index,
    bill_people_maps,
    collect_quarterly=False,
):
    bills_by_lob = collections.defaultdict(set)
    bills_by_lob_strict = collections.defaultdict(set)
    bills_by_lob_quarter_strict = collections.defaultdict(set)
    filings_by_quarter = collections.Counter()
    counters = collections.Counter()
    dotless_bare_s_audit = _empty_dotless_bare_s_audit()
    mechanical_citation_audit = _empty_mechanical_citation_audit()
    matched_seen = set()
    for payload in iter_sweep_filings(page_paths, filing_year):
        filing_uuid = payload["filing_uuid"]
        lob_id = attribution_by_uuid.get(filing_uuid)
        if lob_id is None:
            continue
        matched_seen.add(filing_uuid)
        result = filing_bill_sets(
            payload,
            filing_uuid,
            filing_year,
            title_index,
            bill_people_maps,
        )
        _merge_dotless_bare_s_audits(
            dotless_bare_s_audit, result["_dotless_bare_s_audit"]
        )
        _merge_mechanical_citation_audits(
            mechanical_citation_audit,
            result["_mechanical_citation_audit"],
        )
        bills_by_lob[lob_id].update(result["generous"])
        bills_by_lob_strict[lob_id].update(result["strict"])
        if collect_quarterly:
            quarter = filing_period_to_quarter(payload.get("filing_period"))
            filings_by_quarter[quarter] += 1
            bills_by_lob_quarter_strict[(lob_id, quarter)].update(
                result["strict"]
            )
        counters["filings"] += 1
        for field in (
            "activities",
            "raw_mentions",
            "title_matches",
            "stale_citations",
            "stale_unresolvable",
        ):
            counters[field] += result[field]
    missing = sorted(set(attribution_by_uuid) - matched_seen)
    if missing:
        raise IngestError(
            f"sweep {filing_year} lost {len(missing)} attributed filings "
            f"between validation and aggregation; first={missing[0]}"
        )
    distinct_generous = set().union(*bills_by_lob.values()) if bills_by_lob else set()
    distinct_strict = (
        set().union(*bills_by_lob_strict.values())
        if bills_by_lob_strict
        else set()
    )
    parse_receipts = dict(counters)
    parse_receipts.update(
        _dotless_bare_s_receipts(dotless_bare_s_audit)
    )
    parse_receipts.update(
        _mechanical_citation_receipts(mechanical_citation_audit)
    )
    result = {
        "bills_by_lob": dict(bills_by_lob),
        "bills_by_lob_strict": dict(bills_by_lob_strict),
        "distinct_bills": distinct_generous,
        "distinct_bills_strict": distinct_strict,
        "receipts": parse_receipts,
    }
    if collect_quarterly:
        result.update(
            {
                "bills_by_lob_quarter_strict": dict(
                    bills_by_lob_quarter_strict
                ),
                "filings_by_quarter": {
                    quarter: filings_by_quarter[quarter]
                    for quarter in range(1, 5)
                },
                "filings_unassigned_to_quarter": 0,
            }
        )
    return result


def fullscope_resolution_receipt(
    scope,
    filing_year,
    per_org_channel_counts,
    per_org_filings,
):
    """Build mutually exclusive per-year organization resolution receipts."""
    categories = collections.Counter()
    coverage_gaps = []
    per_org = []
    for lob_id in sorted(scope["lob_ids"]):
        channels = per_org_channel_counts.get(lob_id, {})
        id_filings = channels.get("id", 0)
        name_filings = channels.get("name", 0)
        both_filings = channels.get("both", 0)
        matched_filings = per_org_filings.get(lob_id, 0)
        has_id = bool(id_filings)
        has_name = bool(name_filings)
        if has_id and has_name:
            category = "by_both"
        elif has_id:
            category = "by_id_only"
        elif has_name:
            category = "by_name_only"
        else:
            category = "not_at_all"
        categories[category] += 1
        row = {
            "lob_id": lob_id,
            "filing_year": filing_year,
            "resolution": category,
            "matched_filings": matched_filings,
            "matched_by_id": id_filings,
            "matched_by_name": name_filings,
            "matched_by_both": both_filings,
        }
        per_org.append(row)
        if category == "not_at_all":
            coverage_gaps.append(
                {
                    "lob_id": lob_id,
                    "filing_year": filing_year,
                    "source_names": list(scope["orgs"][lob_id]["names"]),
                    "reason": "zero_client.id_and_exact_normalized_name_matches",
                }
            )
    receipt = {
        "org_set_size": len(scope["lob_ids"]),
        "category_rule": (
            "mutually exclusive per organization/year: id_only, name_only, "
            "both channels observed, or neither channel observed"
        ),
        "resolved_by_id": categories["by_id_only"],
        "resolved_by_name": categories["by_name_only"],
        "resolved_by_id_only": categories["by_id_only"],
        "resolved_by_name_only": categories["by_name_only"],
        "resolved_by_both": categories["by_both"],
        "not_at_all": categories["not_at_all"],
        "resolved_with_id_inclusive": (
            categories["by_id_only"] + categories["by_both"]
        ),
        "resolved_with_name_inclusive": (
            categories["by_name_only"] + categories["by_both"]
        ),
    }
    if sum(categories.values()) != len(scope["lob_ids"]):
        raise IngestError(
            f"full-scope {filing_year} resolution categories do not partition scope"
        )
    return receipt, coverage_gaps, per_org


def assert_quarterly_annual_relationship(
    annual_dyad_bill_sets,
    quarterly_dyad_bill_sets,
):
    """Assert annual sets are dyad-wise unions, while quarter sums may repeat."""
    union_by_dyad = collections.defaultdict(set)
    for (lob_id, bioguide, _quarter), bills in quarterly_dyad_bill_sets.items():
        union_by_dyad[(lob_id, bioguide)].update(bills)
    normalized_union = {
        key: bills for key, bills in union_by_dyad.items() if bills
    }
    normalized_annual = {
        key: set(bills)
        for key, bills in annual_dyad_bill_sets.items()
        if bills
    }
    if normalized_union != normalized_annual:
        missing = sorted(set(normalized_annual) - set(normalized_union))
        extra = sorted(set(normalized_union) - set(normalized_annual))
        raise IngestError(
            "quarterly strict sets do not union to annual strict sets: "
            f"missing_dyads={missing[:3]}, extra_dyads={extra[:3]}"
        )
    annual_incidence = sum(len(bills) for bills in normalized_annual.values())
    quarter_incidence = sum(
        len(bills) for bills in quarterly_dyad_bill_sets.values()
    )
    if quarter_incidence < annual_incidence:
        raise IngestError(
            "quarterly bill incidence is smaller than annual union incidence"
        )
    return {
        "relationship": (
            "for each dyad, the annual strict bill set equals the union of its "
            "four quarterly strict bill sets; summing quarterly n_bills may "
            "exceed annual n_bills when one bill appears in multiple quarters"
        ),
        "annual_equals_union_of_quarters": True,
        "annual_dyad_bill_incidence": annual_incidence,
        "summed_quarterly_dyad_bill_incidence": quarter_incidence,
        "cross_quarter_repeat_excess": quarter_incidence - annual_incidence,
    }


def fullscope_observed_years_by_org(connection):
    """Return sweep years observed through either attribution channel."""
    observed = collections.defaultdict(set)
    cursor = connection.execute(
        "SELECT f.filing_year, f.filing_uuid, ids.lob_id, names.lob_id "
        "FROM staged_filings AS f "
        "LEFT JOIN accepted_ids AS ids ON ids.client_id = f.client_id "
        "LEFT JOIN exact_names AS names "
        "ON names.normalized_name = f.normalized_name "
        "WHERE ids.lob_id IS NOT NULL OR names.lob_id IS NOT NULL"
    )
    for filing_year, filing_uuid, id_lob_id, name_lob_id in cursor:
        if (
            id_lob_id is not None
            and name_lob_id is not None
            and id_lob_id != name_lob_id
        ):
            raise IngestError(
                f"full-scope filing {filing_uuid} has conflicting roster "
                f"attribution: client.id -> {id_lob_id}, exact name -> "
                f"{name_lob_id}"
            )
        owner = id_lob_id if id_lob_id is not None else name_lob_id
        observed[owner].add(filing_year)
    return observed


def fullscope_org_roster_rows(scope, observed_years_by_org=None):
    """Emit one auditable row for every unioned organization."""
    observed_years_by_org = (
        {} if observed_years_by_org is None else observed_years_by_org
    )
    rows = []
    for owner in sorted(scope["lob_ids"]):
        org = scope["orgs"][owner]
        years = set(observed_years_by_org.get(owner, ()))
        years.update(org.get("pattern_years", ()))
        flags = list(org["sources"])
        flags.extend(
            "name_pattern:" + pattern_id
            for pattern_id in org.get("pattern_matches", ())
        )
        rows.append(
            {
                "lob_id_or_client_id": owner,
                "client_name": org["canonical_name"],
                "source_flags": "|".join(sorted(set(flags))),
                "first_year_seen": min(years) if years else "",
                "last_year_seen": max(years) if years else "",
            }
        )
    if len(rows) != len(scope["lob_ids"]):
        raise IngestError("full-scope roster does not cover the complete org set")
    return rows


def aggregate_staged_fullscope_year(
    connection,
    filing_year,
    scope,
    title_index,
    bill_people_maps,
    collect_quarterly=False,
    collect_bill_index=False,
):
    """Filter staged filings through the two-channel crosswalk and aggregate."""
    bills_by_lob = collections.defaultdict(set)
    bills_by_lob_strict = collections.defaultdict(set)
    bills_by_lob_quarter_strict = collections.defaultdict(set)
    filings_by_quarter = collections.Counter()
    counters = collections.Counter()
    per_org_filings = collections.Counter()
    per_org_channel_counts = collections.defaultdict(collections.Counter)
    dotless_bare_s_audit = _empty_dotless_bare_s_audit()
    mechanical_citation_audit = _empty_mechanical_citation_audit()
    bill_index_observations = {}
    cursor = connection.execute(
        "SELECT f.filing_uuid, f.client_id, f.client_name, "
        "f.normalized_name, f.filing_period, f.activities_json, "
        "ids.lob_id, names.lob_id "
        "FROM staged_filings AS f "
        "LEFT JOIN accepted_ids AS ids ON ids.client_id = f.client_id "
        "LEFT JOIN exact_names AS names "
        "ON names.normalized_name = f.normalized_name "
        "WHERE f.filing_year = ? "
        "AND (ids.lob_id IS NOT NULL OR names.lob_id IS NOT NULL) "
        "ORDER BY f.filing_uuid",
        (filing_year,),
    )
    for (
        filing_uuid,
        _client_id,
        _client_name,
        _normalized_name,
        filing_period,
        activities_json,
        id_lob_id,
        name_lob_id,
    ) in cursor:
        if (
            id_lob_id is not None
            and name_lob_id is not None
            and id_lob_id != name_lob_id
        ):
            raise IngestError(
                f"full-scope filing {filing_uuid} has conflicting attribution: "
                f"client.id -> {id_lob_id}, exact name -> {name_lob_id}"
            )
        lob_id = id_lob_id if id_lob_id is not None else name_lob_id
        try:
            activities = json.loads(activities_json)
        except json.JSONDecodeError as exc:
            raise IngestError(
                f"staged full-scope filing {filing_uuid} has invalid activities"
            ) from exc
        payload = {"lobbying_activities": activities}
        result = filing_bill_sets(
            payload,
            filing_uuid,
            filing_year,
            title_index,
            bill_people_maps,
            collect_bill_index=collect_bill_index,
        )
        _merge_dotless_bare_s_audits(
            dotless_bare_s_audit, result["_dotless_bare_s_audit"]
        )
        _merge_mechanical_citation_audits(
            mechanical_citation_audit,
            result["_mechanical_citation_audit"],
        )
        bills_by_lob[lob_id].update(result["generous"])
        bills_by_lob_strict[lob_id].update(result["strict"])
        if collect_bill_index:
            period = filing_period.strip() if isinstance(filing_period, str) else None
            _filing_period_sort_key(period)
            for bill_key, filing_observation in result[
                "bill_observations"
            ].items():
                observation = bill_index_observations.setdefault(
                    (lob_id, bill_key),
                    {
                        "n_filings": 0,
                        "n_activities": 0,
                        "filing_periods": set(),
                        "sources": set(),
                        "strict": False,
                    },
                )
                observation["n_filings"] += 1
                observation["n_activities"] += filing_observation[
                    "n_activities"
                ]
                observation["filing_periods"].add(period)
                observation["sources"].update(filing_observation["sources"])
                observation["strict"] = (
                    observation["strict"] or filing_observation["strict"]
                )
        if collect_quarterly:
            quarter = filing_period_to_quarter(filing_period)
            filings_by_quarter[quarter] += 1
            bills_by_lob_quarter_strict[(lob_id, quarter)].update(
                result["strict"]
            )
        counters["filings"] += 1
        per_org_filings[lob_id] += 1
        if id_lob_id is not None:
            per_org_channel_counts[lob_id]["id"] += 1
        if name_lob_id is not None:
            per_org_channel_counts[lob_id]["name"] += 1
        if id_lob_id is not None and name_lob_id is not None:
            per_org_channel_counts[lob_id]["both"] += 1
        for field in (
            "activities",
            "raw_mentions",
            "title_matches",
            "stale_citations",
            "stale_unresolvable",
        ):
            counters[field] += result[field]

    distinct_generous = (
        set().union(*bills_by_lob.values()) if bills_by_lob else set()
    )
    distinct_strict = (
        set().union(*bills_by_lob_strict.values())
        if bills_by_lob_strict
        else set()
    )
    resolution, coverage_gaps, per_org_resolution = (
        fullscope_resolution_receipt(
            scope,
            filing_year,
            per_org_channel_counts,
            per_org_filings,
        )
    )
    parse_receipts = dict(counters)
    parse_receipts.update(_dotless_bare_s_receipts(dotless_bare_s_audit))
    parse_receipts.update(
        _mechanical_citation_receipts(mechanical_citation_audit)
    )
    result = {
        "bills_by_lob": dict(bills_by_lob),
        "bills_by_lob_strict": dict(bills_by_lob_strict),
        "distinct_bills": distinct_generous,
        "distinct_bills_strict": distinct_strict,
        "resolution": resolution,
        "coverage_gaps": coverage_gaps,
        "per_org_resolution": per_org_resolution,
        "receipts": parse_receipts,
        "bill_index_observations": bill_index_observations,
    }
    if collect_quarterly:
        result.update(
            {
                "bills_by_lob_quarter_strict": dict(
                    bills_by_lob_quarter_strict
                ),
                "filings_by_quarter": {
                    quarter: filings_by_quarter[quarter]
                    for quarter in range(1, 5)
                },
                "filings_unassigned_to_quarter": 0,
            }
        )
    return result


def iter_worklist_payloads(records, filing_paths):
    for record in records:
        filing_uuid = record["report_uuid"]
        filing_path = filing_paths.get(filing_uuid)
        if filing_path is None:
            continue
        payload = _decode_json(
            _read_bytes(filing_path, f"filing {filing_uuid}"),
            f"filing {filing_uuid}",
        )
        if not isinstance(payload, dict):
            raise IngestError(f"filing {filing_uuid} is not an object")
        yield filing_uuid, payload


def full_universe_payloads(
    filing_year,
    worklist_records,
    filing_paths,
    sweep_page_paths,
):
    """Yield the locked full universe; worklist copies win for 2023 UUIDs."""
    seen = set()
    if filing_year == 2023:
        for filing_uuid, payload in iter_worklist_payloads(
            worklist_records, filing_paths
        ):
            seen.add(filing_uuid)
            yield filing_uuid, payload
    for payload in iter_sweep_filings(sweep_page_paths, filing_year):
        filing_uuid = payload["filing_uuid"]
        if filing_uuid in seen:
            continue
        seen.add(filing_uuid)
        yield filing_uuid, payload


def build_full_universe_totals(
    filing_payloads,
    filing_year,
    title_index,
    bill_people_maps,
    attribution_people_map,
):
    """Sum strict distinct-client-bill people-set attention for one year."""
    bills_by_client = collections.defaultdict(set)
    counters = collections.Counter()
    dotless_bare_s_audit = _empty_dotless_bare_s_audit()
    mechanical_citation_audit = _empty_mechanical_citation_audit()
    for filing_uuid, payload in filing_payloads:
        senate_client_id, _registrant_scoped_id, _client_name = (
            _validated_client(payload, filing_uuid)
        )
        result = filing_bill_sets(
            payload,
            filing_uuid,
            filing_year,
            title_index,
            bill_people_maps,
        )
        _merge_dotless_bare_s_audits(
            dotless_bare_s_audit, result["_dotless_bare_s_audit"]
        )
        _merge_mechanical_citation_audits(
            mechanical_citation_audit,
            result["_mechanical_citation_audit"],
        )
        bills_by_client[senate_client_id].update(result["strict"])
        counters["filings"] += 1
        for field in (
            "activities",
            "raw_mentions",
            "title_matches",
            "stale_citations",
            "stale_unresolvable",
        ):
            counters[field] += result[field]

    totals = collections.Counter()
    distinct_client_bills = 0
    for bill_keys in bills_by_client.values():
        distinct_client_bills += len(bill_keys)
        for bill_key in bill_keys:
            for bioguide in attribution_people_map.get(bill_key, set()):
                totals[bioguide] += 1
    rows = [
        {"bioguide": bioguide, "total_n_bills": totals[bioguide]}
        for bioguide in sorted(totals)
    ]
    receipt = dict(counters)
    receipt.update(_dotless_bare_s_receipts(dotless_bare_s_audit))
    receipt.update(
        _mechanical_citation_receipts(mechanical_citation_audit)
    )
    receipt.update(
        {
            "clients": len(bills_by_client),
            "distinct_client_bills_strict": distinct_client_bills,
            "legislator_rows": len(rows),
            "attention_sum": sum(totals.values()),
            "title_mode": "strict",
        }
    )
    return rows, receipt


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _bill_metadata_for_member(
    member_stream,
    archive_name,
    member_name,
    include_typed_titles=False,
    include_forecast_metadata=False,
):
    stack = []
    sponsors_seen = False
    cosponsors_seen = False
    sponsor_bioguides = []
    cosponsor_bioguides = []
    cosponsor_records = []
    titles = []
    introduced_dates = []
    origin_chambers = []
    policy_areas = []
    committee_referrals = []
    typed_titles = {
        "short_title": [],
        "official_title": [],
    }
    try:
        for event, element in ET.iterparse(
            member_stream, events=("start", "end")
        ):
            name = _local_name(element.tag)
            if event == "start":
                stack.append(name)
                continue
            path = tuple(stack)
            if include_forecast_metadata and len(path) >= 2:
                value = (element.text or "").strip()
                if path[-2:] == ("bill", "introducedDate") and value:
                    introduced_dates.append(value)
                elif path[-2:] == ("bill", "originChamber") and value:
                    origin_chambers.append(value)
                elif (
                    path[-2:] == ("policyArea", "name")
                    and "subjects" not in path
                    and value
                ):
                    policy_areas.append(value)
            if len(path) >= 2 and path[-2:] == ("bill", "sponsors"):
                if sponsors_seen:
                    raise IngestError(
                        f"duplicate bill/sponsors in {archive_name}:{member_name}"
                    )
                sponsors_seen = True
                items = [
                    child
                    for child in list(element)
                    if _local_name(child.tag) == "item"
                ]
                for index, item in enumerate(items):
                    identifiers = [
                        child
                        for child in list(item)
                        if _local_name(child.tag) == "bioguideId"
                    ]
                    if len(identifiers) != 1 or not (
                        identifiers[0].text or ""
                    ).strip():
                        raise IngestError(
                            f"{archive_name}:{member_name} sponsor item {index} "
                            "missing expected bill/sponsors/item/bioguideId"
                        )
                    sponsor_bioguides.append(identifiers[0].text.strip())
            if len(path) >= 2 and path[-2:] == ("bill", "cosponsors"):
                if cosponsors_seen:
                    raise IngestError(
                        f"duplicate bill/cosponsors in {archive_name}:{member_name}"
                    )
                cosponsors_seen = True
                items = [
                    child
                    for child in list(element)
                    if _local_name(child.tag) == "item"
                ]
                for index, item in enumerate(items):
                    identifiers = [
                        child
                        for child in list(item)
                        if _local_name(child.tag) == "bioguideId"
                    ]
                    if len(identifiers) != 1 or not (
                        identifiers[0].text or ""
                    ).strip():
                        raise IngestError(
                            f"{archive_name}:{member_name} cosponsor item "
                            f"{index} missing expected "
                            "bill/cosponsors/item/bioguideId"
                        )
                    cosponsor_bioguides.append(
                        identifiers[0].text.strip()
                    )
                    if include_forecast_metadata:
                        fields = collections.defaultdict(list)
                        for child in list(item):
                            child_value = (child.text or "").strip()
                            if child_value:
                                fields[_local_name(child.tag)].append(child_value)
                        dates = fields.get("sponsorshipDate", [])
                        if len(dates) != 1:
                            raise IngestError(
                                f"{archive_name}:{member_name} cosponsor item "
                                f"{index} missing unique sponsorshipDate"
                            )
                        cosponsor_records.append(
                            {
                                "bioguide_id": identifiers[0].text.strip(),
                                "sponsorship_date": dates[0],
                            }
                        )
            if (
                include_forecast_metadata
                and len(path) >= 2
                and path[-2:] == ("bill", "committees")
            ):
                items = [
                    child
                    for child in list(element)
                    if _local_name(child.tag) == "item"
                ]
                for index, item in enumerate(items):
                    fields = collections.defaultdict(list)
                    activities = []
                    for child in list(item):
                        child_name = _local_name(child.tag)
                        child_value = (child.text or "").strip()
                        if child_name != "activities":
                            if child_value:
                                fields[child_name].append(child_value)
                            continue
                        for activity_item in list(child):
                            if _local_name(activity_item.tag) != "item":
                                continue
                            activity_fields = collections.defaultdict(list)
                            for activity_child in list(activity_item):
                                activity_value = (activity_child.text or "").strip()
                                if activity_value:
                                    activity_fields[
                                        _local_name(activity_child.tag)
                                    ].append(activity_value)
                            activities.append(
                                {
                                    "name": (activity_fields.get("name") or [None])[0],
                                    "date": (activity_fields.get("date") or [None])[0],
                                }
                            )
                    system_codes = fields.get("systemCode", [])
                    names = fields.get("name", [])
                    chambers = fields.get("chamber", [])
                    if len(system_codes) != 1 or len(names) != 1:
                        raise IngestError(
                            f"{archive_name}:{member_name} committee item {index} "
                            "missing unique systemCode/name"
                        )
                    committee_referrals.append(
                        {
                            "system_code": system_codes[0],
                            "name": names[0],
                            "chamber": chambers[0] if len(chambers) == 1 else None,
                            "activities": tuple(activities),
                        }
                    )
            if len(path) >= 2 and path[-2:] == ("bill", "titles"):
                items = [
                    child
                    for child in list(element)
                    if _local_name(child.tag) == "item"
                ]
                for item in items:
                    fields = collections.defaultdict(list)
                    for child in list(item):
                        value = (child.text or "").strip()
                        if value:
                            fields[_local_name(child.tag)].append(value)
                    kinds = fields.get("titleType", [])
                    if not any(
                        fragment in kind.upper()
                        for kind in kinds
                        for fragment in TITLE_KIND_FRAGMENTS
                    ):
                        continue
                    title_values = fields.get("title", [])
                    titles.extend(title_values)
                    if any("SHORT TITLE" in kind.upper() for kind in kinds):
                        typed_titles["short_title"].extend(title_values)
                    if any(
                        "OFFICIAL TITLE" in kind.upper() for kind in kinds
                    ):
                        typed_titles["official_title"].extend(title_values)
            if (
                "sponsors" not in path
                and "cosponsors" not in path
                and "titles" not in path
                and (not include_forecast_metadata or "committees" not in path)
            ):
                element.clear()
            stack.pop()
    except ET.ParseError as exc:
        raise IngestError(
            f"malformed XML in {archive_name}:{member_name}: {exc}"
        ) from exc
    if len(set(sponsor_bioguides)) > 1:
        raise IngestError(
            f"multiple sponsor bioguides in {archive_name}:{member_name}"
        )
    if not sponsors_seen or not sponsor_bioguides:
        sponsor = None
    else:
        sponsor = sponsor_bioguides[0]
    result = (sponsor, set(cosponsor_bioguides), titles)
    if include_typed_titles:
        result += (
            {
                field: tuple(dict.fromkeys(values))
                for field, values in typed_titles.items()
            },
        )
    if include_forecast_metadata:
        if len(set(introduced_dates)) != 1:
            raise IngestError(
                f"{archive_name}:{member_name} missing unique bill/introducedDate"
            )
        if len(set(origin_chambers)) != 1:
            raise IngestError(
                f"{archive_name}:{member_name} missing unique bill/originChamber"
            )
        if len(set(policy_areas)) > 1:
            raise IngestError(
                f"{archive_name}:{member_name} has multiple top-level policy areas"
            )
        result += (
            {
                "sponsor_bioguide": sponsor,
                "cosponsors": tuple(
                    sorted(
                        cosponsor_records,
                        key=lambda item: (
                            item["bioguide_id"], item["sponsorship_date"]
                        ),
                    )
                ),
                "introduced_date": introduced_dates[0],
                "origin_chamber": origin_chambers[0],
                "policy_area": policy_areas[0] if policy_areas else None,
                "committee_referrals": tuple(committee_referrals),
            },
        )
    return result


def _sponsor_for_member(member_stream, archive_name, member_name):
    sponsor, _cosponsors, _titles = _bill_metadata_for_member(
        member_stream, archive_name, member_name
    )
    return sponsor


def load_sponsor_map(
    billstatus_dir,
    congress=117,
    zip_types=None,
    include_forecast_metadata=False,
):
    if zip_types is None:
        zip_types = (
            HISTORICAL_ZIP_TYPES if congress in {115, 116} else ZIP_TYPES
        )
    else:
        zip_types = tuple(zip_types)
    bill_people_map = {}
    billstatus_keys = set()
    lacking_sponsor = set()
    title_index = collections.defaultdict(set)
    typed_titles_by_bill = {}
    forecast_metadata_by_bill = {}
    titles_excluded_by_guard = 0
    xml_members = 0

    for archive_token, type_token in zip_types:
        archive_name = f"BILLSTATUS-{congress}-{archive_token}.zip"
        archive_path = os.path.join(billstatus_dir, archive_name)
        if not os.path.isfile(archive_path):
            raise IngestError(f"missing BILLSTATUS archive {archive_path}")
        member_re = re.compile(
            r"(?:.*/)?BILLSTATUS-"
            + str(congress)
            + re.escape(archive_token)
            + r"(\d+)\.xml$",
            re.IGNORECASE,
        )
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise IngestError(
                f"cannot open BILLSTATUS archive {archive_path}: {exc}"
            ) from exc
        with archive:
            xml_infos = sorted(
                (
                    info
                    for info in archive.infolist()
                    if info.filename.lower().endswith(".xml")
                ),
                key=lambda info: info.filename.lower(),
            )
            for info in xml_infos:
                match = member_re.fullmatch(info.filename)
                if match is None:
                    raise IngestError(
                        f"unexpected XML member name {archive_name}:{info.filename}"
                    )
                key = (type_token, int(match.group(1)), congress)
                if key in billstatus_keys:
                    raise IngestError(
                        "duplicate BILLSTATUS member for "
                        f"{type_token} {key[1]} in Congress {congress}"
                    )
                billstatus_keys.add(key)
                xml_members += 1
                try:
                    with archive.open(info, "r") as member_stream:
                        parsed_metadata = _bill_metadata_for_member(
                            member_stream,
                            archive_name,
                            info.filename,
                            include_typed_titles=True,
                            include_forecast_metadata=include_forecast_metadata,
                        )
                        sponsor, cosponsors, raw_titles, typed_titles = (
                            parsed_metadata[:4]
                        )
                        if include_forecast_metadata:
                            forecast_metadata_by_bill[key] = parsed_metadata[4]
                except IngestError:
                    raise
                except Exception as exc:
                    raise IngestError(
                        f"cannot parse {archive_name}:{info.filename}: {exc}"
                    ) from exc
                titles_excluded_by_guard += _index_bill_titles(
                    key, raw_titles, title_index
                )
                typed_titles_by_bill[key] = typed_titles
                people = set(cosponsors)
                if sponsor is None:
                    lacking_sponsor.add(key)
                else:
                    people.add(sponsor)
                previous = bill_people_map.get(key)
                if previous is not None and previous != people:
                    raise IngestError(
                        f"conflicting bill people for {type_token} {key[1]}"
                    )
                bill_people_map[key] = people

    receipts = {
        "congress": congress,
        "xml_members": xml_members,
        "bills_mapped": sum(
            bool(people) for people in bill_people_map.values()
        ),
        "bills_lacking_sponsor": len(lacking_sponsor),
        "bills_lacking_sponsor_refs": [
            f"{type_token} {number} (Congress {bill_congress})"
            for type_token, number, bill_congress in sorted(lacking_sponsor)
        ],
    }
    title_receipts = {
        "congress": congress,
        "titles_in_dictionary": len(title_index),
        "titles_excluded_by_guard": titles_excluded_by_guard,
    }
    title_index = finalize_title_index(
        title_index, typed_titles_by_bill=typed_titles_by_bill
    )
    result = (
        bill_people_map,
        billstatus_keys,
        lacking_sponsor,
        title_index,
        title_receipts,
        receipts,
    )
    if include_forecast_metadata:
        result += (forecast_metadata_by_bill,)
    return result


def build_dyad_bill_sets(bills_by_lob, bill_people_map):
    """Attribute each distinct bill to every sponsor or cosponsor."""
    dyad_bills = collections.defaultdict(set)
    for lob_id, bill_keys in bills_by_lob.items():
        for bill_key in set(bill_keys):
            for bioguide in set(bill_people_map.get(bill_key, set())):
                dyad_bills[(str(lob_id), bioguide)].add(bill_key)
    return dict(dyad_bills)


def build_quarterly_dyad_bill_sets(bills_by_lob_quarter, bill_people_map):
    """Attribute quarterly strict bill sets to sponsors and cosponsors."""
    dyad_bills = collections.defaultdict(set)
    for (lob_id, quarter), bill_keys in bills_by_lob_quarter.items():
        for bill_key in set(bill_keys):
            for bioguide in set(bill_people_map.get(bill_key, set())):
                dyad_bills[(str(lob_id), bioguide, quarter)].add(bill_key)
    return dict(dyad_bills)


def build_dyads(bills_by_lob, bill_people_map):
    dyad_bills = build_dyad_bill_sets(bills_by_lob, bill_people_map)
    return {
        key: len(bills) for key, bills in sorted(dyad_bills.items())
    }


def per_dyad_bill_rows(dyad_bill_sets):
    return [
        {
            "lob_id": lob_id,
            "bioguide": bioguide,
            "bill_keys": _sorted_bill_key_rows(bills),
        }
        for (lob_id, bioguide), bills in sorted(dyad_bill_sets.items())
    ]


def matched_bill_people_rows(bill_keys, bill_people_map):
    rows = []
    for type_token, number, congress in sorted(set(bill_keys)):
        key = (type_token, number, congress)
        if key not in bill_people_map:
            raise IngestError(
                f"matched bill {type_token} {number} in Congress "
                f"{congress} lacks a people-set"
            )
        rows.append(
            {
                "type": type_token,
                "number": number,
                "congress": congress,
                "people_set_size": len(set(bill_people_map[key])),
            }
        )
    return rows


def mentioned_sponsorless_rows(bills_by_lob, sponsorless_keys):
    mentioned = set().union(*bills_by_lob.values()) if bills_by_lob else set()
    rows = []
    for type_token, number, congress in sorted(
        mentioned & set(sponsorless_keys)
    ):
        key = (type_token, number, congress)
        rows.append(
            {
                "type": type_token,
                "number": number,
                "congress": congress,
                "lob_ids": sorted(
                    lob_id
                    for lob_id, bills in bills_by_lob.items()
                    if key in bills
                ),
            }
        )
    return rows


def load_their_edges(path, lob_ids):
    if set(lob_ids) != EXPECTED_LOB_IDS_2021:
        raise IngestError("LobbyView slice scope is not the pinned 14 lob_ids")
    edges = {}
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise IngestError(f"cannot read LobbyView network {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != NETWORK_FIELDS:
            raise IngestError(
                "LobbyView network header is "
                f"{reader.fieldnames!r}; expected {NETWORK_FIELDS!r}"
            )
        for row_number, row in enumerate(reader, start=2):
            try:
                year = int(row["year"])
            except (TypeError, ValueError) as exc:
                raise IngestError(
                    f"LobbyView network row {row_number} has invalid year"
                ) from exc
            lob_id = (row["lob_id"] or "").strip()
            if year != 2021 or lob_id not in lob_ids:
                continue
            bioguide = (row["bioguide_id"] or "").strip()
            if not bioguide:
                raise IngestError(
                    f"LobbyView network row {row_number} has blank bioguide_id"
                )
            try:
                n_bills = int(row["n_bills"])
            except (TypeError, ValueError) as exc:
                raise IngestError(
                    f"LobbyView network row {row_number} has invalid n_bills"
                ) from exc
            if n_bills < 0:
                raise IngestError(
                    f"LobbyView network row {row_number} has negative n_bills"
                )
            key = (lob_id, bioguide)
            if key in edges:
                raise IngestError(
                    f"duplicate LobbyView 2021 dyad {key[0]}/{key[1]}"
                )
            edges[key] = n_bills
    return edges


def _edge_rows(edges):
    return [
        {"lob_id": lob_id, "bioguide": bioguide, "n_bills": n_bills}
        for (lob_id, bioguide), n_bills in sorted(edges.items())
    ]


def compare_edges(their_edges, our_edges, lob_ids=None):
    their_keys = set(their_edges)
    our_keys = set(our_edges)
    common_keys = sorted(their_keys & our_keys)
    theirs_only_keys = sorted(their_keys - our_keys)
    ours_only_keys = sorted(our_keys - their_keys)

    common = [
        {
            "lob_id": key[0],
            "bioguide": key[1],
            "n_theirs": their_edges[key],
            "n_ours": our_edges[key],
            "exact_match": their_edges[key] == our_edges[key],
        }
        for key in common_keys
    ]
    theirs_only = [
        {
            "lob_id": key[0],
            "bioguide": key[1],
            "n_theirs": their_edges[key],
        }
        for key in theirs_only_keys
    ]
    ours_only = [
        {
            "lob_id": key[0],
            "bioguide": key[1],
            "n_ours": our_edges[key],
        }
        for key in ours_only_keys
    ]

    organizations = set(lob_ids or ()) | {
        key[0] for key in their_keys | our_keys
    }
    per_org_summary = []
    for lob_id in sorted(organizations):
        their_org = {key for key in their_keys if key[0] == lob_id}
        our_org = {key for key in our_keys if key[0] == lob_id}
        common_org = their_org & our_org
        per_org_summary.append(
            {
                "lob_id": lob_id,
                "edges_theirs": len(their_org),
                "edges_ours": len(our_org),
                "common": len(common_org),
                "exact_n_bills_matches": sum(
                    their_edges[key] == our_edges[key] for key in common_org
                ),
                "theirs_only": len(their_org - our_org),
                "ours_only": len(our_org - their_org),
            }
        )

    return {
        "their_edges": _edge_rows(their_edges),
        "our_edges": _edge_rows(our_edges),
        "common": common,
        "theirs_only": theirs_only,
        "ours_only": ours_only,
        "per_org_summary": per_org_summary,
        "totals": {
            "edges_theirs": len(their_edges),
            "edges_ours": len(our_edges),
            "common": len(common),
            "exact_n_bills_matches": sum(
                item["exact_match"] for item in common
            ),
            "theirs_only": len(theirs_only),
            "ours_only": len(ours_only),
        },
    }


def _counter_rows(counter):
    return [
        {"verbatim": verbatim, "count": counter[verbatim]}
        for verbatim in sorted(counter, key=lambda item: (item.lower(), item))
    ]


def _generated():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_years(value):
    if not isinstance(value, str) or not value.strip():
        raise IngestError("--years must be a comma-separated year list")
    years = []
    for token in value.split(","):
        token = token.strip()
        if not token.isdigit():
            raise IngestError(f"invalid --years token {token!r}")
        year = int(token)
        if year not in YEAR_TO_CONGRESS:
            raise IngestError(f"unsupported --years value {year}")
        if year in years:
            raise IngestError(f"duplicate --years value {year}")
        years.append(year)
    return tuple(years)


def _network_rows(edges, filing_year):
    return [
        {
            "lob_id": lob_id,
            "bioguide_id": bioguide,
            "year": filing_year,
            "n_bills": n_bills,
        }
        for (lob_id, bioguide), n_bills in sorted(edges.items())
    ]


def _quarterly_network_rows(dyad_bill_sets, filing_year):
    return [
        {
            "lob_id": lob_id,
            "bioguide_id": bioguide,
            "year": filing_year,
            "quarter": quarter,
            "n_bills": len(bills),
        }
        for (lob_id, bioguide, quarter), bills in sorted(
            dyad_bill_sets.items()
        )
    ]


def per_industry_set_rows(
    year_records,
    available_records,
    industries,
    filing_result,
    generous_edges,
    strict_edges,
):
    available_uuids = {
        record["report_uuid"] for record in available_records
    }
    rows = []
    for industry_set in sorted(
        {industries[record["lob_id"]] for record in year_records}
    ):
        worklist_lobs = {
            record["lob_id"]
            for record in year_records
            if industries[record["lob_id"]] == industry_set
        }
        active_lobs = {
            record["lob_id"]
            for record in available_records
            if industries[record["lob_id"]] == industry_set
        }
        generous_bills = set()
        strict_bills = set()
        for lob_id in active_lobs:
            generous_bills.update(
                filing_result["bills_by_lob"].get(lob_id, set())
            )
            strict_bills.update(
                filing_result["bills_by_lob_strict"].get(lob_id, set())
            )
        rows.append(
            {
                "industry_set": industry_set,
                "lob_ids_in_worklist": len(worklist_lobs),
                "lob_ids_with_filings": len(active_lobs),
                "filings_available": sum(
                    record["report_uuid"] in available_uuids
                    and industries[record["lob_id"]] == industry_set
                    for record in year_records
                ),
                "distinct_bills_generous": len(generous_bills),
                "distinct_bills_strict": len(strict_bills),
                "edges_generous": sum(
                    key[0] in worklist_lobs for key in generous_edges
                ),
                "edges_strict": sum(
                    key[0] in worklist_lobs for key in strict_edges
                ),
            }
        )
    return rows


def bridge3_sweep_industry_rows(
    crosswalk_indexes,
    sweep_receipt,
    filing_result,
    generous_edges,
    strict_edges,
):
    per_org_filings = sweep_receipt["attribution"][
        "per_org_matched_filings"
    ]
    rows = []
    for industry in sorted(set(crosswalk_indexes["lob_to_industry"].values())):
        industry_lobs = {
            lob_id
            for lob_id, mapped_industry in crosswalk_indexes[
                "lob_to_industry"
            ].items()
            if mapped_industry == industry
        }
        active_lobs = {
            lob_id for lob_id in industry_lobs if per_org_filings.get(lob_id, 0)
        }
        generous_bills = set()
        strict_bills = set()
        for lob_id in active_lobs:
            generous_bills.update(
                filing_result["bills_by_lob"].get(lob_id, set())
            )
            strict_bills.update(
                filing_result["bills_by_lob_strict"].get(lob_id, set())
            )
        rows.append(
            {
                "industry_set": industry,
                "lob_ids_in_crosswalk": len(industry_lobs),
                "lob_ids_with_filings": len(active_lobs),
                "filings_available": sum(
                    per_org_filings.get(lob_id, 0) for lob_id in industry_lobs
                ),
                "distinct_bills_generous": len(generous_bills),
                "distinct_bills_strict": len(strict_bills),
                "edges_generous": sum(
                    key[0] in industry_lobs for key in generous_edges
                ),
                "edges_strict": sum(
                    key[0] in industry_lobs for key in strict_edges
                ),
            }
        )
    return rows


def _run_full_scope(paths, args, years):
    if args.compare_2021:
        raise IngestError("--compare-2021 is unavailable with --full-scope")
    if args.worklist:
        raise IngestError("--worklist is unavailable with --full-scope")
    build_years = tuple(sorted(years))
    output_isolation = assert_fullscope_output_isolation(paths, build_years)
    repository_inputs = (
        load_bill_repository(paths) if args.emit_bill_index else None
    )
    scope = load_fullscope_org_scope(paths, enforce_fixed_counts=True)
    seed_state = _new_fullscope_seed_state()
    cache_receipt = _load_fullscope_cached_identity_seeds(
        paths, scope, seed_state
    )

    with tempfile.TemporaryDirectory(prefix="lda_fullscope_stage_") as stage_dir:
        database_path = os.path.join(stage_dir, "filings.sqlite3")
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute(
                "CREATE TABLE staged_filings ("
                "filing_year INTEGER NOT NULL, "
                "filing_uuid TEXT NOT NULL, "
                "client_id TEXT NOT NULL, "
                "client_name TEXT NOT NULL, "
                "normalized_name TEXT NOT NULL, "
                "filing_period TEXT, "
                "activities_json TEXT NOT NULL, "
                "PRIMARY KEY (filing_year, filing_uuid))"
            )
            sweep_receipts = {}
            for filing_year in FULLSCOPE_SWEEP_YEARS:
                sweep_receipts[filing_year] = _stage_fullscope_sweep_year(
                    connection,
                    os.path.join(paths["sweeps_dir"], str(filing_year)),
                    filing_year,
                    scope,
                    seed_state,
                )
            connection.commit()

            base_harvested_client_ids, base_harvest_receipt = (
                _harvest_fullscope_client_ids(
                    scope, seed_state, cache_receipt
                )
            )
            pattern_receipt = _extend_fullscope_scope_with_pattern_clients(
                scope,
                seed_state,
                base_harvested_client_ids,
            )
            harvested_client_ids, harvest_receipt = (
                _harvest_fullscope_client_ids(
                    scope, seed_state, cache_receipt
                )
            )
            for pattern_client in pattern_receipt["per_client"]:
                owner = pattern_client["lob_id_or_client_id"]
                client_id = pattern_client["client_id"]
                if client_id not in harvested_client_ids[owner]:
                    raise IngestError(
                        "finance name-pattern client.id failed the shared name "
                        f"plausibility guard: {client_id} -> {owner}"
                    )
                pattern_client["id_guard_accepted"] = True
            harvest_receipt["base_scope_before_pattern_union"] = {
                "org_set_size": len(base_harvested_client_ids),
                "accepted_client_ids": base_harvest_receipt[
                    "accepted_client_ids"
                ],
                "organizations_with_accepted_client_ids": (
                    base_harvest_receipt[
                        "organizations_with_accepted_client_ids"
                    ]
                ),
            }
            harvest_receipt["name_pattern_union"] = pattern_receipt
            corpus_basis = fullscope_corpus_basis(scope)
            if corpus_basis == LEGACY_CORPUS_BASIS:
                raise IngestError(
                    "derived full-scope basis unexpectedly equals legacy"
                )
            connection.execute(
                "CREATE TABLE accepted_ids ("
                "client_id TEXT PRIMARY KEY, lob_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE exact_names ("
                "normalized_name TEXT PRIMARY KEY, lob_id TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO accepted_ids (client_id, lob_id) VALUES (?, ?)",
                [
                    (client_id, lob_id)
                    for lob_id in sorted(harvested_client_ids)
                    for client_id in sorted(harvested_client_ids[lob_id])
                ],
            )
            connection.executemany(
                "INSERT INTO exact_names (normalized_name, lob_id) "
                "VALUES (?, ?)",
                sorted(scope["name_to_lob"].items()),
            )
            connection.execute(
                "CREATE INDEX staged_filings_year_client_id "
                "ON staged_filings (filing_year, client_id)"
            )
            connection.execute(
                "CREATE INDEX staged_filings_year_name "
                "ON staged_filings (filing_year, normalized_name)"
            )
            connection.commit()
            os.makedirs(paths["derived_dir"], exist_ok=True)
            roster_rows = fullscope_org_roster_rows(
                scope,
                fullscope_observed_years_by_org(connection),
            )
            roster_path = fullscope_org_roster_path(paths)
            _write_csv_atomic(
                roster_path,
                FULLSCOPE_ROSTER_FIELDS,
                roster_rows,
            )
            scope["receipt"]["roster"] = {
                "path": os.path.abspath(roster_path),
                "rows": len(roster_rows),
                "one_row_per_organization": True,
            }

            filing_congresses = {
                congress_for_year(year) for year in build_years
            }
            congress_maps = {}
            for congress in range(115, max(filing_congresses) + 1):
                zip_types = (
                    HISTORICAL_ZIP_TYPES
                    if congress in {115, 116}
                    else ZIP_TYPES
                )
                congress_maps[congress] = load_sponsor_map(
                    billstatus_dir_for_congress(paths, congress),
                    congress=congress,
                    zip_types=zip_types,
                )
            bill_people_maps = {
                congress: congress_map[0]
                for congress, congress_map in congress_maps.items()
            }

            os.makedirs(paths["derived_dir"], exist_ok=True)
            receipt_years = {}
            bill_index_rows_by_year = {}
            bill_index_years = {}
            cached_attribution_congress = None
            attribution_people_map = None
            attribution_billstatus_keys = None
            attribution_sponsorless_keys = None
            attribution_people_receipts = None
            for filing_year in build_years:
                congress = congress_for_year(filing_year)
                current_map = congress_maps[congress]
                title_index = current_map[3]
                title_dictionary_receipts = current_map[4]
                if cached_attribution_congress != congress:
                    attribution_people_map = {}
                    attribution_billstatus_keys = set()
                    attribution_sponsorless_keys = set()
                    attribution_people_receipts = {}
                    for source_congress in range(115, congress + 1):
                        source_map = congress_maps[source_congress]
                        attribution_people_map.update(source_map[0])
                        attribution_billstatus_keys.update(source_map[1])
                        attribution_sponsorless_keys.update(source_map[2])
                        attribution_people_receipts[str(source_congress)] = (
                            source_map[5]
                        )
                    cached_attribution_congress = congress

                filing_result = aggregate_staged_fullscope_year(
                    connection,
                    filing_year,
                    scope,
                    title_index,
                    bill_people_maps,
                    collect_quarterly=args.emit_quarterly,
                    collect_bill_index=args.emit_bill_index,
                )
                generous_dyad_bills = build_dyad_bill_sets(
                    filing_result["bills_by_lob"], attribution_people_map
                )
                strict_dyad_bills = build_dyad_bill_sets(
                    filing_result["bills_by_lob_strict"],
                    attribution_people_map,
                )
                generous_edges = {
                    key: len(bills)
                    for key, bills in sorted(generous_dyad_bills.items())
                }
                strict_edges = {
                    key: len(bills)
                    for key, bills in sorted(strict_dyad_bills.items())
                }
                generous_path = fullscope_network_output_path(
                    paths, filing_year
                )
                strict_path = fullscope_network_output_path(
                    paths, filing_year, strict=True
                )
                _write_csv_atomic(
                    generous_path,
                    NETWORK_FIELDS,
                    _network_rows(generous_edges, filing_year),
                )
                _write_csv_atomic(
                    strict_path,
                    NETWORK_FIELDS,
                    _network_rows(strict_edges, filing_year),
                )

                outputs = {
                    "generous": os.path.abspath(generous_path),
                    "strict": os.path.abspath(strict_path),
                }
                if args.emit_bill_index:
                    index_rows = bill_index_rows(
                        filing_year,
                        filing_result["bill_index_observations"],
                        scope,
                    )
                    indexed_keys = {row["bill_key"] for row in index_rows}
                    resolved_keys = {
                        canonical_bill_key(bill_key)
                        for bill_key in filing_result["distinct_bills"]
                    }
                    if indexed_keys != resolved_keys:
                        raise IngestError(
                            f"bill index {filing_year} does not preserve the "
                            "full-scope generous bill set"
                        )
                    index_path = bill_lobby_index_output_path(
                        paths, filing_year
                    )
                    _write_csv_atomic(
                        index_path,
                        BILL_INDEX_FIELDS,
                        index_rows,
                    )
                    bill_index_rows_by_year[filing_year] = index_rows
                    bill_index_years[str(filing_year)] = (
                        bill_index_year_receipt(
                            filing_year,
                            index_rows,
                            corpus_basis,
                            index_path,
                        )
                    )
                    outputs["bill_index"] = os.path.abspath(index_path)
                quarterly_receipt = None
                if args.emit_quarterly:
                    quarterly_dyad_bills = build_quarterly_dyad_bill_sets(
                        filing_result["bills_by_lob_quarter_strict"],
                        attribution_people_map,
                    )
                    quarterly_relationship = (
                        assert_quarterly_annual_relationship(
                            strict_dyad_bills, quarterly_dyad_bills
                        )
                    )
                    quarterly_path = fullscope_quarterly_network_output_path(
                        paths, filing_year
                    )
                    _write_csv_atomic(
                        quarterly_path,
                        QUARTERLY_NETWORK_FIELDS,
                        _quarterly_network_rows(
                            quarterly_dyad_bills, filing_year
                        ),
                    )
                    outputs["quarterly_strict"] = os.path.abspath(
                        quarterly_path
                    )
                    quarterly_receipt = {
                        "channel": "strict",
                        "filings_by_quarter": {
                            str(quarter): filing_result[
                                "filings_by_quarter"
                            ][quarter]
                            for quarter in range(1, 5)
                        },
                        "filings_unassigned_to_quarter": filing_result[
                            "filings_unassigned_to_quarter"
                        ],
                        "dyad_quarter_edges": len(quarterly_dyad_bills),
                        "output": os.path.abspath(quarterly_path),
                    }
                    quarterly_receipt.update(quarterly_relationship)

                matched_bills = (
                    filing_result["distinct_bills"]
                    & attribution_billstatus_keys
                )
                matched_bills_with_people = {
                    bill_key
                    for bill_key in matched_bills
                    if attribution_people_map[bill_key]
                }
                sweep_receipt = sweep_receipts[filing_year]
                parse_receipt = filing_result["receipts"]
                network_receipt = {
                    "source": "sweep",
                    "CORPUS_BASIS": corpus_basis,
                    "org_set_size": len(scope["lob_ids"]),
                    "org_resolution": filing_result["resolution"],
                    "coverage_gaps": filing_result["coverage_gaps"],
                    "per_org_resolution": filing_result[
                        "per_org_resolution"
                    ],
                    "filings_attributed": parse_receipt.get("filings", 0),
                    "activities_parsed": parse_receipt.get("activities", 0),
                    "distinct_bills": {
                        "generous": len(filing_result["distinct_bills"]),
                        "strict": len(filing_result["distinct_bills_strict"]),
                    },
                    "edges": {
                        "generous": len(generous_edges),
                        "strict": len(strict_edges),
                    },
                    "sweep_manifest_md5": sweep_receipt["manifest"]["md5"],
                    "sweep_page_count": sweep_receipt["manifest"][
                        "page_count"
                    ],
                    "raw_mentions": parse_receipt.get("raw_mentions", 0),
                    "title_matches": parse_receipt.get("title_matches", 0),
                    "stale_citations": parse_receipt.get(
                        "stale_citations", 0
                    ),
                    "stale_unresolvable": parse_receipt.get(
                        "stale_unresolvable", 0
                    ),
                    "dotless_bare_s_recovered": parse_receipt[
                        "dotless_bare_s_recovered"
                    ],
                    "dotless_bare_s_rejected": parse_receipt[
                        "dotless_bare_s_rejected"
                    ],
                    "runs_into_title_recovered": parse_receipt[
                        "runs_into_title_recovered"
                    ],
                }
                year_receipt = {
                    "filing_year": filing_year,
                    "congress": congress,
                    "CORPUS_BASIS": corpus_basis,
                    "edge_counts": {
                        "generous": len(generous_edges),
                        "strict": len(strict_edges),
                    },
                    "network": network_receipt,
                    "sweep": sweep_receipt,
                    "title_channel": title_dictionary_receipts,
                    "bills_matched_to_sponsors_or_cosponsors": len(
                        matched_bills_with_people
                    ),
                    "mentioned_bills_lacking_sponsors": (
                        mentioned_sponsorless_rows(
                            filing_result["bills_by_lob"],
                            attribution_sponsorless_keys,
                        )
                    ),
                    "bill_people_maps": attribution_people_receipts,
                    "outputs": outputs,
                }
                if quarterly_receipt is not None:
                    year_receipt["quarterly_attention"] = quarterly_receipt
                receipt_years[str(filing_year)] = year_receipt

            assert_fullscope_corpus_basis(receipt_years, corpus_basis)
            bill_index_manifest = None
            bill_repository_join_path = None
            if args.emit_bill_index:
                assert_bill_index_corpus_basis(
                    bill_index_years, receipt_years, corpus_basis
                )
                (
                    membership_rows,
                    audit_rows,
                    direction_rows,
                    repository_sources,
                ) = repository_inputs
                join_rows, join_receipt = build_bill_repository_join(
                    bill_index_rows_by_year,
                    membership_rows,
                    audit_rows,
                    direction_rows,
                )
                bill_repository_join_path = (
                    bill_repository_join_output_path(paths)
                )
                _write_csv_atomic(
                    bill_repository_join_path,
                    BILL_REPOSITORY_JOIN_FIELDS,
                    join_rows,
                )
                join_receipt["output"] = os.path.abspath(
                    bill_repository_join_path
                )
                bill_index_manifest = {
                    "mode": "full_scope_bill_index",
                    "requested_years": list(build_years),
                    "CORPUS_BASIS": corpus_basis,
                    "corpus_basis_invariant": (
                        "Every emitted bill-index year has exactly the same "
                        "CORPUS_BASIS as its full-scope network year."
                    ),
                    "honesty": list(BILL_INDEX_HONESTY),
                    "repository_sources": repository_sources,
                    "repository_join": join_receipt,
                    "years": bill_index_years,
                }
                dump_atomic(
                    bill_index_manifest,
                    bill_lobby_index_manifest_path(paths),
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                )
            receipts = {
                "generated": _generated(),
                "mode": "full_scope",
                "requested_years": list(years),
                "CORPUS_BASIS": corpus_basis,
                "scope": scope["receipt"],
                "identity_harvest": harvest_receipt,
                "output_isolation": output_isolation,
                "scope_sweeps": {
                    str(year): sweep_receipts[year]
                    for year in FULLSCOPE_SWEEP_YEARS
                },
                "outputs": {
                    "org_roster": os.path.abspath(roster_path),
                },
                "years": receipt_years,
            }
            if bill_index_manifest is not None:
                receipts["outputs"].update(
                    {
                        "bill_index_manifest": os.path.abspath(
                            bill_lobby_index_manifest_path(paths)
                        ),
                        "bill_repository_join": os.path.abspath(
                            bill_repository_join_path
                        ),
                    }
                )
            receipts_path = fullscope_receipts_path(paths)
            dump_atomic(
                receipts,
                receipts_path,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
        finally:
            connection.close()

    summary = ", ".join(
        f"{year}:g{receipt_years[str(year)]['network']['edges']['generous']}"
        f"/s{receipt_years[str(year)]['network']['edges']['strict']}"
        for year in build_years
    )
    print(
        f"lda full-scope rebuild: {summary} | receipts "
        f"{fullscope_receipts_path(paths)}"
    )
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Build multi-year Senate LDA legislator networks."
    )
    parser.add_argument(
        "--worklist",
        metavar="PATH",
        help="filing worklist JSON (default: pinned 2021 worklist)",
    )
    parser.add_argument(
        "--years",
        default="2021",
        help="comma-separated filing years (default: 2021)",
    )
    parser.add_argument(
        "--compare-2021",
        action="store_true",
        help="emit the pinned 2021 LobbyView cross-comparison",
    )
    parser.add_argument(
        "--emit-quarterly",
        action="store_true",
        help="emit strict quarterly attention networks",
    )
    parser.add_argument(
        "--emit-bill-index",
        action="store_true",
        help=(
            "emit per-year organization-bill indexes and the exact bill "
            "repository join (requires --full-scope)"
        ),
    )
    parser.add_argument(
        "--full-scope",
        action="store_true",
        help="build sweep-sourced networks for the fixed all-finance union",
    )
    return parser.parse_args(list(argv))


def _run(environ=None, argv=()):
    environ = os.environ if environ is None else environ
    paths = default_paths(
        environ.get("LOCALAPPDATA"),
        environ.get("MELANGE_BILL_REPOSITORY_ROOT"),
    )
    args = _parse_args(argv)
    years = parse_years(args.years)
    if args.emit_bill_index and not args.full_scope:
        raise IngestError("--emit-bill-index requires --full-scope")
    if args.full_scope:
        return _run_full_scope(paths, args, years)
    bridge3_mode = any(year in BRIDGE3_YEARS for year in years)
    if bridge3_mode and any(year not in BRIDGE3_YEARS for year in years):
        raise IngestError(
            "bridge3 --years cannot mix 2023..2026 with legacy years"
        )
    if args.compare_2021 and 2021 not in years:
        raise IngestError("--compare-2021 requires 2021 in --years")
    if args.compare_2021 and bridge3_mode:
        raise IngestError("--compare-2021 is unavailable in bridge3 mode")
    worklist_path = args.worklist or (
        paths["bridge3_worklist"] if bridge3_mode else paths["worklist"]
    )
    legacy_default = _same_path(worklist_path, paths["worklist"])
    worklist, worklist_md5 = load_worklist(
        worklist_path,
        legacy_default_year=2021 if legacy_default else None,
        comparison_2021=args.compare_2021,
    )
    crosswalk_indexes = None
    if bridge3_mode:
        crosswalk_indexes = bridge3_crosswalk_indexes()
        unexpected_lob_ids = sorted(
            {
                record["lob_id"]
                for record in worklist
                if record["lob_id"] not in BRIDGE3_LOB_IDS
            }
        )
        if unexpected_lob_ids:
            raise IngestError(
                "bridge3 worklist contains lob_id outside locked 22: "
                + unexpected_lob_ids[0]
            )
    requested_records = [
        record for record in worklist if record["filing_year"] in years
    ]
    for filing_year in years:
        if filing_year not in PINNED_22_SWEEP_NETWORK_YEARS and not any(
            record["filing_year"] == filing_year
            for record in requested_records
        ):
            raise IngestError(
                f"worklist has no records for requested year {filing_year}"
            )

    manifest_path = worklist_manifest_path(paths, worklist_path)
    manifest = load_worklist_manifest(manifest_path, worklist_md5)
    needed_records = (
        [record for record in worklist if record["filing_year"] <= 2023]
        if bridge3_mode
        else requested_records
    )
    filing_paths, missing_filings = discover_filing_paths(
        paths,
        needed_records,
        manifest,
        legacy_first=legacy_default,
        require_complete=args.compare_2021,
    )
    available_records = [
        record for record in needed_records if record["report_uuid"] in filing_paths
    ]
    if bridge3_mode:
        # Vintage lock: identity harvest is permitted only for sweep-sourced
        # 2023--2026 networks.  The 2021/2022 UUID-worklist path stays in the
        # non-bridge branch below and therefore cannot consult this loader.
        assert all(year in PINNED_22_SWEEP_NETWORK_YEARS for year in years)
        industries = dict(crosswalk_indexes["lob_to_industry"])
        harvested_client_ids, harvest_receipt = load_pinned_identity_harvest(
            paths
        )
        sweep_scans = {}
        for filing_year in years:
            sweep_dir = os.path.join(
                paths["sweeps_dir"], str(filing_year)
            )
            sweep_scans[filing_year] = scan_sweep_pages(
                sweep_dir,
                filing_year,
                harvested_client_ids=(
                    harvested_client_ids
                    if filing_year in PINNED_22_SWEEP_NETWORK_YEARS
                    else None
                ),
                require_manifest=True,
            )
    else:
        industries = load_client_industries(
            paths["client_classification"],
            {record["lob_id"] for record in worklist},
        )
        harvest_receipt = None
        sweep_scans = {}

    filing_congresses = {congress_for_year(year) for year in years}
    congress_maps = {}
    for congress in range(115, max(filing_congresses) + 1):
        zip_types = (
            HISTORICAL_ZIP_TYPES if congress in {115, 116} else ZIP_TYPES
        )
        congress_maps[congress] = load_sponsor_map(
            billstatus_dir_for_congress(paths, congress),
            congress=congress,
            zip_types=zip_types,
        )
    bill_people_maps = {
        congress: congress_map[0]
        for congress, congress_map in congress_maps.items()
    }

    os.makedirs(paths["derived_dir"], exist_ok=True)
    receipt_years = {}
    generous_edges_by_year = {}
    for filing_year in years:
        congress = congress_for_year(filing_year)
        (
            _current_bill_people_map,
            _current_billstatus_keys,
            _current_sponsorless_keys,
            title_index,
            title_dictionary_receipts,
            _current_bill_people_receipts,
        ) = congress_maps[congress]
        attribution_people_map = {}
        attribution_billstatus_keys = set()
        attribution_sponsorless_keys = set()
        attribution_people_receipts = {}
        for source_congress in range(115, congress + 1):
            source_map = congress_maps[source_congress]
            attribution_people_map.update(source_map[0])
            attribution_billstatus_keys.update(source_map[1])
            attribution_sponsorless_keys.update(source_map[2])
            attribution_people_receipts[str(source_congress)] = source_map[5]
        year_records = [
            record
            for record in requested_records
            if record["filing_year"] == filing_year
        ]
        year_available = [
            record
            for record in available_records
            if record["filing_year"] == filing_year
        ]
        filing_result = _dotless_bare_s_receipts(
            _empty_dotless_bare_s_audit()
        )
        filing_result.update(
            _mechanical_citation_receipts(
                _empty_mechanical_citation_audit()
            )
        )
        if filing_year in PINNED_22_SWEEP_NETWORK_YEARS:
            parsed_result = aggregate_sweep_network(
                sweep_scans[filing_year]["page_paths"],
                filing_year,
                sweep_scans[filing_year]["attribution_by_uuid"],
                title_index,
                bill_people_maps,
                collect_quarterly=args.emit_quarterly,
            )
            parse_receipts = _dotless_bare_s_receipts(
                _empty_dotless_bare_s_audit()
            )
            parse_receipts.update(
                _mechanical_citation_receipts(
                    _empty_mechanical_citation_audit()
                )
            )
            parse_receipts.update(parsed_result["receipts"])
            parsed_result["receipts"] = parse_receipts
            filing_result.update(parsed_result)
        else:
            filing_result.update(
                parse_filings(
                    year_available,
                    title_index=title_index,
                    filing_paths=filing_paths,
                    default_filing_year=filing_year,
                    bill_people_maps=bill_people_maps,
                    collect_quarterly=args.emit_quarterly,
                )
            )
        generous_dyad_bills = build_dyad_bill_sets(
            filing_result["bills_by_lob"], attribution_people_map
        )
        strict_dyad_bills = build_dyad_bill_sets(
            filing_result["bills_by_lob_strict"], attribution_people_map
        )
        generous_edges = {
            key: len(bills)
            for key, bills in sorted(generous_dyad_bills.items())
        }
        strict_edges = {
            key: len(bills)
            for key, bills in sorted(strict_dyad_bills.items())
        }
        generous_edges_by_year[filing_year] = generous_edges
        _write_csv_atomic(
            network_output_path(paths, filing_year),
            NETWORK_FIELDS,
            _network_rows(generous_edges, filing_year),
        )
        _write_csv_atomic(
            network_output_path(paths, filing_year, strict=True),
            NETWORK_FIELDS,
            _network_rows(strict_edges, filing_year),
        )
        quarterly_receipt = None
        if args.emit_quarterly:
            quarterly_dyad_bills = build_quarterly_dyad_bill_sets(
                filing_result["bills_by_lob_quarter_strict"],
                attribution_people_map,
            )
            quarterly_path = quarterly_network_output_path(
                paths, filing_year
            )
            _write_csv_atomic(
                quarterly_path,
                QUARTERLY_NETWORK_FIELDS,
                _quarterly_network_rows(
                    quarterly_dyad_bills, filing_year
                ),
            )
            quarterly_receipt = {
                "channel": "strict",
                "filings_by_quarter": {
                    str(quarter): filing_result["filings_by_quarter"][quarter]
                    for quarter in range(1, 5)
                },
                "filings_unassigned_to_quarter": filing_result[
                    "filings_unassigned_to_quarter"
                ],
                "edges": len(quarterly_dyad_bills),
                "output": os.path.abspath(quarterly_path),
            }

        matched_bills = (
            filing_result["distinct_bills"]
            & attribution_billstatus_keys
        )
        matched_bills_with_people = {
            bill_key
            for bill_key in matched_bills
            if attribution_people_map[bill_key]
        }
        year_missing = [
            item
            for item in missing_filings
            if item["filing_year"] == filing_year
        ]
        if filing_year in PINNED_22_SWEEP_NETWORK_YEARS:
            sweep_receipt = sweep_scans[filing_year]["receipt"]
            parse_receipts = filing_result["receipts"]
            title_channel_receipts = dict(title_dictionary_receipts)
            title_channel_receipts["matches"] = parse_receipts.get(
                "title_matches", 0
            )
            per_industry = bridge3_sweep_industry_rows(
                crosswalk_indexes,
                sweep_receipt,
                filing_result,
                generous_edges,
                strict_edges,
            )
            network_receipt = {
                "source": "sweep",
                "filings_in_sweep": sweep_receipt["filings"],
                "filings_attributed": parse_receipts.get("filings", 0),
                "activities": parse_receipts.get("activities", 0),
                "raw_mentions": parse_receipts.get("raw_mentions", 0),
                "title_matches": parse_receipts.get("title_matches", 0),
                "stale_citations": parse_receipts.get(
                    "stale_citations", 0
                ),
                "stale_unresolvable": parse_receipts.get(
                    "stale_unresolvable", 0
                ),
                "dotless_bare_s_recovered": parse_receipts[
                    "dotless_bare_s_recovered"
                ],
                "dotless_bare_s_rejected": parse_receipts[
                    "dotless_bare_s_rejected"
                ],
                "runs_into_title_recovered": parse_receipts[
                    "runs_into_title_recovered"
                ],
                "per_dyad_bills": per_dyad_bill_rows(
                    generous_dyad_bills
                ),
                "per_dyad_bills_strict": per_dyad_bill_rows(
                    strict_dyad_bills
                ),
            }
        else:
            sweep_receipt = (
                sweep_scans[filing_year]["receipt"]
                if filing_year in sweep_scans
                else None
            )
            missing_counter = collections.Counter(
                item["verbatim"]
                for item in filing_result["parsed_mentions"]
                if (
                    item["type"],
                    item["number"],
                    item["congress"],
                ) not in attribution_billstatus_keys
            )
            title_channel_receipts = dict(title_dictionary_receipts)
            title_channel_receipts.update(filing_result["title_channel"])
            per_industry = per_industry_set_rows(
                year_records,
                year_available,
                industries,
                filing_result,
                generous_edges,
                strict_edges,
            )
            network_receipt = {
                "source": "uuid_worklist",
                "filings_in_worklist": len(year_records),
                "filings_parsed": filing_result["filings_parsed"],
                "filings_missing": year_missing,
                "no_activity_filings": filing_result[
                    "no_activity_filings"
                ],
                "amendment_filings": filing_result["amendment_filings"],
                "original_and_amendment_groups": filing_result[
                    "original_and_amendment_groups"
                ],
                "amendment_policy": AMENDMENT_POLICY_NOTE,
                "activities": filing_result["activities"],
                "raw_mentions": filing_result["raw_mentions"],
                "unparsed_bill_like_mention_count": sum(
                    filing_result["unparsed_mentions"].values()
                ),
                "unparsed_bill_like_mentions": _counter_rows(
                    filing_result["unparsed_mentions"]
                ),
                "congress_context_mention_count": len(
                    filing_result["congress_context_mentions"]
                ),
                "congress_context_mentions": filing_result[
                    "congress_context_mentions"
                ],
                "stale_citations": filing_result["stale_citations"],
                "stale_unresolvable": filing_result[
                    "stale_unresolvable"
                ],
                "dotless_bare_s_recovered": filing_result[
                    "dotless_bare_s_recovered"
                ],
                "dotless_bare_s_rejected": filing_result[
                    "dotless_bare_s_rejected"
                ],
                "runs_into_title_recovered": filing_result[
                    "runs_into_title_recovered"
                ],
                "per_filing_mentions": filing_result[
                    "per_filing_mentions"
                ],
                "per_dyad_bills": per_dyad_bill_rows(
                    generous_dyad_bills
                ),
                "per_dyad_bills_strict": per_dyad_bill_rows(
                    strict_dyad_bills
                ),
                "bill_refs_not_found_in_resolved_congress": _counter_rows(
                    missing_counter
                ),
            }

        totals_receipt = None
        if bridge3_mode:
            totals_rows, totals_receipt = build_full_universe_totals(
                full_universe_payloads(
                    filing_year,
                    year_available,
                    filing_paths,
                    sweep_scans[filing_year]["page_paths"],
                ),
                filing_year,
                title_index,
                bill_people_maps,
                attribution_people_map,
            )
            _write_csv_atomic(
                totals_output_path(paths, filing_year),
                TOTAL_FIELDS,
                totals_rows,
            )

        year_receipt = {
            "filing_year": filing_year,
            "congress": congress,
            "distinct_bills_overall": len(
                filing_result["distinct_bills"]
            ),
            "distinct_bills_strict": len(
                filing_result["distinct_bills_strict"]
            ),
            "edge_counts": {
                "generous": len(generous_edges),
                "strict": len(strict_edges),
            },
            "title_channel": title_channel_receipts,
            "bills_matched_to_sponsors_or_cosponsors": len(
                matched_bills_with_people
            ),
            "matched_bill_people_sets": matched_bill_people_rows(
                matched_bills, attribution_people_map
            ),
            "mentioned_bills_lacking_sponsors": mentioned_sponsorless_rows(
                filing_result["bills_by_lob"],
                attribution_sponsorless_keys,
            ),
            "per_industry_set": per_industry,
            "bill_people_maps": attribution_people_receipts,
            "outputs": {
                "generous": os.path.abspath(
                    network_output_path(paths, filing_year)
                ),
                "strict": os.path.abspath(
                    network_output_path(paths, filing_year, strict=True)
                ),
                "totals": (
                    os.path.abspath(totals_output_path(paths, filing_year))
                    if bridge3_mode
                    else None
                ),
            },
        }
        if quarterly_receipt is not None:
            year_receipt["outputs"]["quarterly"] = quarterly_receipt[
                "output"
            ]
            year_receipt["quarterly_attention"] = quarterly_receipt
        if bridge3_mode:
            year_receipt.update(
                {
                    "network": network_receipt,
                    "sweep": sweep_receipt,
                    "totals": totals_receipt,
                }
            )
        else:
            year_receipt.update(network_receipt)
        receipt_years[str(filing_year)] = year_receipt

    receipts = {
        "generated": _generated(),
        "worklist": os.path.abspath(worklist_path),
        "worklist_md5": worklist_md5,
        "requested_years": list(years),
        "comparison_2021_enabled": args.compare_2021,
        "bridge3_mode": bridge3_mode,
        "client_id_harvest": harvest_receipt,
        "years": receipt_years,
    }
    dump_atomic(
        receipts,
        paths["receipts"],
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    if args.compare_2021:
        lob_ids = {record["lob_id"] for record in worklist}
        their_edges = load_their_edges(paths["network"], lob_ids)
        comparison = compare_edges(
            their_edges,
            generous_edges_by_year[2021],
            lob_ids=lob_ids,
        )
        dump_atomic(
            comparison,
            paths["comparison"],
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    summary = ", ".join(
        f"{year}:g{receipt_years[str(year)]['edge_counts']['generous']}"
        f"/s{receipt_years[str(year)]['edge_counts']['strict']}"
        for year in years
    )
    print(f"lda rebuild: {summary} | receipts {paths['receipts']}")
    return 0


def main(argv=None):
    try:
        return _run(argv=sys.argv[1:] if argv is None else argv)
    except IngestError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: LDA network rebuild failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
