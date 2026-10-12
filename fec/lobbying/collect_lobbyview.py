# -*- coding: utf-8 -*-
"""Standalone ingest for the LobbyView bulk-export vintage dated 2024-10-25.

Inputs: clients.csv and issues.csv in the configured LobbyView raw directory,
plus any delivered reports, issue_text/texts, bills, and network tables.
Outputs: classified clients, candidate adversary clients, a finance-issue index,
global issue statistics, the member-attention network repository and review sheet,
and a fail-closed source manifest in the output directory.
Decision log (2026-07-31): keep this collector data-plane-independent, process the
delivered network table, inventory issue-text and bills tables, and expose the
bank/credit-union fight surface as candidate evidence pending user ratification.
Decision log (2026-07-31): names are decisive; NAICS alone is suggestive only.
Decision log (2026-07-31): output CSVs preserve client names verbatim and are
not spreadsheet-formula-escaped; @STAKE is a real value, so downstream Excel
users should import client names as text.
Decision log (2026-07-31): high confidence means a name-rule match on an
unambiguous banking or credit-union token; low means a generic "bank" token or
NAICS-only evidence. Adversary-set construction should start from high and treat
low as review candidates.
Decision log (2026-07-31): reports are latest-version-only per the codebook, so
no amendment dedup is needed. Amount semantics differ by self-filer status and
amount text is preserved in the joined CSV; spend aggregates parse blank as 0.0.
Decision log (2026-07-31): one 2000-vintage report has a blank lob_id; ingest
tolerates and counts it as unlinkable/side other, but blank report_uuid remains
a fail-closed error.
Decision log (2026-07-31): 17,336 reports rows (1.0%) reference lob_ids absent
from the 2024-10-25 clients export, a LobbyView cross-table vintage mismatch;
they join as side other with blank client_name and are counted, not rejected.
Decision log (2026-07-31): reports-absent and --skip-issues runs do not remove
prior report artifacts; lobbyview_manifest.json outputs is the authority for
what the CURRENT run produced, so downstream consumers must key off it and
never off file presence.
Decision log (2026-07-31): duplicate_natural_keys counts collisions of the coarse (lob_id, registrant_id, filing_year, filing_period_code) key, which registrations, terminations, no-activity filings, and amendments legitimately share; it is a tripwire receipt, not evidence of amendment double-counting (the export is latest-version-only per the codebook).
Coverage note (2026-07-31): issue-code coverage collapses after filing_year 2021 (only a sliver of 2022 reports carries issue codes; measured max_finance_joined_filing_year records the actual edge); reports-only coverage runs through 2023 plus a 2024 sliver, so post-2021 finance columns are structurally undercounted.
Kim, In Song (2018). LobbyView: Firm-level Lobbying & Congressional Bills Database.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# MB_ROOT is imported for repository convention and as an isolation boundary.
# This standalone collector never reads or writes Moneyball data-plane artifacts.
try:
    from moneyball_core import dump_atomic
except Exception:
    # 2026-07-31: Worktree data planes can be empty, so the kernel import may
    # fail there; retain the kernel's temp-sibling + os.replace semantics.
    def dump_atomic(obj, path, **kw):
        temp_path = path + ".tmp." + str(os.getpid())
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(obj, handle, **kw)
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise


VINTAGE = "2024-10-25"
RULE_VERSION = "v2"
DEFAULT_CODES = "BAN,FIN,TAX,BNK,CSP,HOU,INS,RET,SMB,MON,ACC,ECN"
SIDES = ("bank", "credit_union", "financial_other", "other")
HIGH_CONFIDENCE_RULES = {
    "name:credit-union",
    "name:cu-trade",
    "name:bankers-assn",
    "name:bank-trade",
    "name:bancorp",
    "name:major-bank",
}

CLIENT_INPUT_FIELDS = ["lob_id", "gvkey", "bvdid", "naics", "client_name"]
CLIENT_OUTPUT_FIELDS = [
    "lob_id",
    "client_name",
    "naics",
    "gvkey",
    "bvdid",
    "side",
    "match_rule",
    "confidence",
]
ISSUE_INPUT_FIELDS = [
    "report_uuid",
    "general_issue_code",
    "issue_ordinal_position",
    "government_entity_ids",
]
ISSUE_OUTPUT_FIELDS = ["report_uuid", "codes", "hill", "n_finance_rows"]
REPORT_INPUT_FIELDS = [
    "report_uuid",
    "lob_id",
    "registrant_id",
    "registrant_name",
    "filing_year",
    "filing_period_code",
    "amount",
    "is_no_activity",
    "is_self_filer",
    "is_amendment",
    "filing_url",
]
REPORT_JOIN_FIELDS = [
    "report_uuid",
    "lob_id",
    "client_name",
    "side",
    "confidence",
    "filing_year",
    "filing_period_code",
    "amount",
    "is_self_filer",
    "is_no_activity",
    "is_amendment",
    "registrant_id",
    "registrant_name",
    "codes",
    "hill",
    "n_finance_rows",
]
NETWORK_INPUT_FIELDS = ["lob_id", "bioguide_id", "year", "n_bills"]
NETWORK_MEMBER_FIELDS = [
    "industry_set",
    "bioguide",
    "year",
    "n_bills_sum",
    "n_clients",
]
INDUSTRY_REVIEW_FIELDS = [
    "set",
    "client_name",
    "lob_id",
    "match_rule",
    "confidence",
    "n_bills_2021_2022",
]
NETWORK_INDUSTRY_SETS = (
    "credit_union",
    "bank",
    "financial_other",
    "nonfinancial",
    "unclassified",
)
NETWORK_REVIEW_SETS = {"credit_union", "bank", "financial_other"}

EXPECTED_TABLES = {
    "clients": ("clients.csv",),
    "issues": ("issues.csv",),
    "reports": ("reports.csv",),
    "issue_text": ("issue_text.csv", "texts.csv"),
    "bills": ("bills.csv",),
    "network": ("network.csv",),
}
FUTURE_TABLES = ("issue_text", "bills", "network")

# 2026-07-31: These non-depository senses were observed as false positives in
# the 2024-10-25 vintage.
EXCLUSION_RE = re.compile(
    r"FOOD BANK|BLOOD BANK|WORLD BANK|BANK STREET|MILK BANK|EYE BANK|"
    r"BONE\s?BANK|SEED BANK|LAND BANK|ORGAN BANK|TISSUE BANK|DIAPER BANK|"
    r"DATA BANK|SPERM BANK|MEMORY BANK|MITIGATION BANK|CONSERVATION BANK|"
    r"WETLAND\s?BANK|OUTER BANKS|BOGUE BANKS|BANK NOTE|\bBANKS,|"
    r"&\s?BANKS\b|TAX-DEFERRED SAVINGS"
)
CU_TRADE_RE = re.compile(
    r"\bNAFCU\b|\bCUNA\s+MUTUAL\b|\bNASCUS\b|DEFENSE CREDIT UNION COUNCIL"
)
CREDIT_UNION_RE = re.compile(r"\bCREDIT UNIONS?\b|\bCUSO\b")
BIOGUIDE_RE = re.compile(r"^[A-Z][0-9]{6}$")
BANCORP_RE = re.compile(
    r"\bBANCORP\b|\bBANCSHARES\b|\bBANKSHARES\b|\bBANCORPORATION\b"
)
MAJOR_BANK_RE = re.compile(
    r"\b(?:JPMORGAN|J\.?\s?P\.?\s?MORGAN|BANK OF AMERICA|WELLS FARGO|"
    r"CITIGROUP|CITIBANK|GOLDMAN SACHS|MORGAN STANLEY|U\.?S\.? BANCORP|"
    r"PNC FINANCIAL|TRUIST|CAPITAL ONE|FIFTH THIRD|REGIONS FINANCIAL|"
    r"KEYCORP|M&T BANK|HUNTINGTON BANC|ZIONS BANC|COMERICA|"
    r"AMERICAN EXPRESS|DISCOVER FINANCIAL|SYNCHRONY FINANCIAL)\b"
)
GENERIC_BANK_RE = re.compile(
    r"\bBANK\b|\bBANKS\b|\bBANKING\b|\bBANCO\b|"
    r"\bSAVINGS\s+(?:BANK|ASSOCIATION)\b|\bBANCORPSOUTH\b"
)


class IngestError(Exception):
    """Expected ingest failure that must exit 2 without creating outputs."""


def _normalize_name(value):
    return " ".join((value or "").upper().split())


def _confidence(match_rule):
    base_rule = (match_rule or "").split("+", 1)[0]
    return "high" if base_rule in HIGH_CONFIDENCE_RULES else "low"


def _naics_classification(naics):
    value = (naics or "").strip()
    if value and not value.isdigit():
        return "other", ""
    if value.startswith("52213"):
        return "credit_union", "naics:52213"
    if value.startswith("5221"):
        return "financial_other", "naics:5221x"
    if value.startswith("52"):
        return "financial_other", "naics:52-other"
    return "other", ""


def classify_client(name, naics):
    """Return (side, match_rule) using decisive names before NAICS fallbacks."""
    normalized_name = _normalize_name(name)
    normalized_naics = (naics or "").strip()
    validated_naics = normalized_naics if normalized_naics.isdigit() else ""
    name_side = None
    name_rule = ""

    # Credit-union names are checked first and are decisive even if a bank-name
    # exclusion or a conflicting NAICS code is also present.
    if CREDIT_UNION_RE.search(normalized_name):
        name_side, name_rule = "credit_union", "name:credit-union"
    elif CU_TRADE_RE.search(normalized_name):
        name_side, name_rule = "credit_union", "name:cu-trade"
    elif not EXCLUSION_RE.search(normalized_name):
        # The exclusion applies only to the bank NAME rules in this branch.
        if re.search(
            r"BANK POLICY INSTITUTE|CONSUMER BANKERS ASSOCIATION|"
            r"MORTGAGE BANKERS ASSOCIATION|AMERICA'S MUTUAL BANKS|"
            r"INDEPENDENT COMMUNITY BANKERS",
            normalized_name,
        ):
            name_side, name_rule = "bank", "name:bank-trade"
        elif re.search(r"\bBANKERS ASSOCIATION\b", normalized_name):
            name_side, name_rule = "bank", "name:bankers-assn"
        elif BANCORP_RE.search(normalized_name):
            name_side, name_rule = "bank", "name:bancorp"
        elif MAJOR_BANK_RE.search(normalized_name):
            name_side, name_rule = "bank", "name:major-bank"
        elif GENERIC_BANK_RE.search(normalized_name):
            name_side, name_rule = "bank", "name:bank-generic"

    naics_side, naics_rule = _naics_classification(validated_naics)
    if name_side is not None:
        if validated_naics and naics_side != name_side:
            name_rule += "+naics-conflict"
        return name_side, name_rule
    return naics_side, naics_rule


def _write_csv_atomic(path, fieldnames, rows):
    temp_path = path + ".tmp." + str(os.getpid())
    try:
        with open(
            temp_path, "w", encoding="utf-8-sig", newline=""
        ) as handle:
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


def _file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _inventory_csv(path, count_rows=True):
    data_rows = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if count_rows:
            for _row in reader:
                data_rows += 1
    return {
        "present": True,
        "processed": False,
        "path": path,
        "data_rows": data_rows if count_rows else None,
        "md5": _file_md5(path),
        "header": header,
        "matched_filename": os.path.basename(path),
    }


def _absent_inventory():
    return {
        "present": False,
        "processed": False,
        "path": None,
        "data_rows": None,
        "md5": None,
        "header": None,
        "matched_filename": None,
    }


def _matched_path(raw_dir, candidates):
    for filename in candidates:
        path = os.path.join(raw_dir, filename)
        if os.path.isfile(path):
            return path
    return None


def _parse_codes(value):
    codes = []
    seen = set()
    for piece in value.split(","):
        code = piece.strip().upper()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _parse_bool(value, field, row_no):
    if value == "true":
        return True
    if value in {"false", ""}:
        return False
    raise IngestError(
        f"reports.csv data row {row_no}: invalid {field} token {value!r}"
    )


def _load_clients(path):
    rows = []
    adversaries = {"bank": [], "credit_union": []}
    client_info = {}
    counts = {side: 0 for side in SIDES}
    counts_by_confidence = {
        "bank": {"high": 0, "low": 0},
        "credit_union": {"high": 0, "low": 0},
    }

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != CLIENT_INPUT_FIELDS:
            raise IngestError(
                "clients.csv header mismatch: expected "
                + ",".join(CLIENT_INPUT_FIELDS)
            )
        for data_row_number, source in enumerate(reader, start=1):
            if len(source) != len(header):
                raise IngestError(
                    f"clients.csv data row {data_row_number}: wrong field count "
                    f"(expected {len(header)}, got {len(source)})"
                )
            if source == header:
                raise IngestError(
                    f"clients.csv data row {data_row_number}: repeated header"
                )
            lob_id = source[0].strip()
            if not lob_id:
                raise IngestError(
                    f"clients.csv data row {data_row_number}: blank lob_id"
                )
            gvkey = source[1].strip()
            bvdid = source[2].strip()
            naics = source[3].strip()
            client_name = source[4]
            if lob_id in client_info:
                first_client_name = client_info[lob_id][0]
                raise IngestError(
                    f"clients.csv data row {data_row_number}: duplicate lob_id "
                    f"{lob_id!r} for client_names {first_client_name!r} and "
                    f"{client_name!r}"
                )
            side, match_rule = classify_client(client_name, naics)
            confidence = _confidence(match_rule)
            client_info[lob_id] = (client_name, side, confidence)
            counts[side] += 1
            rows.append(
                {
                    "lob_id": lob_id,
                    "client_name": client_name,
                    "naics": naics,
                    "gvkey": gvkey,
                    "bvdid": bvdid,
                    "side": side,
                    "match_rule": match_rule,
                    "confidence": confidence,
                }
            )
            if side in adversaries:
                counts_by_confidence[side][confidence] += 1
                adversaries[side].append(
                    {
                        "lob_id": lob_id,
                        "client_name": client_name,
                        "naics": naics,
                        "match_rule": match_rule,
                        "confidence": confidence,
                    }
                )

    if not rows:
        raise IngestError("clients.csv has 0 data rows")
    return rows, counts, adversaries, counts_by_confidence, client_info


def _network_industry_set(side):
    if side in NETWORK_REVIEW_SETS:
        return side
    if side == "other":
        return "nonfinancial"
    raise IngestError(f"unknown classified client side {side!r}")


def _network_processing_enabled(skip_issues, network_path):
    return network_path is not None and not skip_issues


def _network_caveats(year_max):
    return [
        "n_bills counts bills the member SPONSORED OR COSPONSORED that the client's filings lobbied - lobbying attention to legislation the member put their name on, NOT disclosed contact (the LDA does not require disclosing which legislators were contacted). Edge semantics verified 2026-08-01 against a primary-source rebuild: BECU's 181 LobbyView edges for 2021 are exactly the sponsor plus 180 cosponsors of H.R. 1996.",
        f"coverage ends {year_max} (same cliff family as issue codes); the FEC lane is the only fresh member-level money signal after",
        "an absent client-member-year dyad means ZERO and dyads disappear/reappear - YoY math zero-fills",
        "industry sets are name-rule classified, candidate status pending Paul's ratification; state leagues and CUSOs are credit_union by Paul's 2026-07-31 directive",
    ]


def _process_network(path, client_rows, expected_rows=None):
    """Validate and aggregate the LobbyView client-member-year network."""
    client_by_lob_id = {row["lob_id"]: row for row in client_rows}
    aggregate = {}
    top_2021_2022 = {}
    yearly_totals = {name: {} for name in NETWORK_INDUSTRY_SETS}
    review_attention = {row["lob_id"]: 0 for row in client_rows}
    rows_read = 0
    rows_matched_classified = 0
    unclassified_rows = 0
    n_blank_bioguide_rows = 0
    blank_bioguide_n_bills = 0
    rows_by_industry_set = {
        name: 0 for name in NETWORK_INDUSTRY_SETS
    }
    seen_dyads = set()
    duplicate_dyad_rows = 0
    distinct_bioguides = set()
    year_min = None
    year_max = None

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != NETWORK_INPUT_FIELDS:
            raise IngestError(
                "network.csv header mismatch: expected "
                + ",".join(NETWORK_INPUT_FIELDS)
            )
        for data_row_number, source in enumerate(reader, start=1):
            rows_read += 1
            if len(source) != len(header):
                raise IngestError(
                    f"network.csv data row {data_row_number}: wrong field count "
                    f"(expected {len(header)}, got {len(source)})"
                )
            lob_id = source[0].strip()
            if not lob_id:
                raise IngestError(
                    f"network.csv data row {data_row_number}: blank lob_id"
                )
            bioguide = source[1].strip()
            if bioguide and not BIOGUIDE_RE.fullmatch(bioguide):
                raise IngestError(
                    f"network.csv data row {data_row_number}: invalid bioguide_id "
                    f"{bioguide!r}"
                )
            try:
                year = int(source[2])
            except ValueError:
                raise IngestError(
                    f"network.csv data row {data_row_number}: invalid year "
                    f"{source[2]!r}"
                )
            if year < 2000 or year > 2035:
                raise IngestError(
                    f"network.csv data row {data_row_number}: year {year} "
                    "outside 2000..2035"
                )
            try:
                n_bills = int(source[3])
            except ValueError:
                raise IngestError(
                    f"network.csv data row {data_row_number}: invalid n_bills "
                    f"{source[3]!r}"
                )
            if n_bills < 1:
                raise IngestError(
                    f"network.csv data row {data_row_number}: n_bills must be >= 1"
                )

            client = client_by_lob_id.get(lob_id)
            if client is None:
                industry_set = "unclassified"
                unclassified_rows += 1
            else:
                industry_set = _network_industry_set(client["side"])
                rows_matched_classified += 1

            if not bioguide:
                n_blank_bioguide_rows += 1
                blank_bioguide_n_bills += n_bills
                continue

            rows_by_industry_set[industry_set] += 1
            dyad = (lob_id, bioguide, year)
            if dyad in seen_dyads:
                duplicate_dyad_rows += 1
            else:
                seen_dyads.add(dyad)

            if client is not None and year in {2021, 2022}:
                review_attention[lob_id] += n_bills
            distinct_bioguides.add(bioguide)
            year_min = year if year_min is None else min(year_min, year)
            year_max = year if year_max is None else max(year_max, year)
            yearly = yearly_totals[industry_set]
            yearly[year] = yearly.get(year, 0) + n_bills

            key = (industry_set, bioguide, year)
            item = aggregate.get(key)
            if item is None:
                item = {"n_bills_sum": 0, "clients": set()}
                aggregate[key] = item
            item["n_bills_sum"] += n_bills
            item["clients"].add(lob_id)

            if year in {2021, 2022}:
                member_key = (industry_set, bioguide)
                member = top_2021_2022.get(member_key)
                if member is None:
                    member = {2021: 0, 2022: 0, "clients": set()}
                    top_2021_2022[member_key] = member
                member[year] += n_bills
                member["clients"].add(lob_id)

    if rows_read == 0:
        raise IngestError("network.csv has 0 data rows")
    if rows_matched_classified == 0:
        raise IngestError(
            f"0 of {rows_read} network rows matched any classified client; "
            "vintage mismatch or wrong clients file - refusing to publish empty "
            "network artifacts"
        )
    if expected_rows is not None and rows_read != expected_rows:
        raise IngestError(
            "network.csv row-count mismatch: inventory counted "
            f"{expected_rows}, processing iterated {rows_read}"
        )

    member_rows = []
    for industry_set, bioguide, year in sorted(aggregate):
        item = aggregate[(industry_set, bioguide, year)]
        member_rows.append(
            {
                "industry_set": industry_set,
                "bioguide": bioguide,
                "year": year,
                "n_bills_sum": item["n_bills_sum"],
                "n_clients": len(item["clients"]),
            }
        )

    per_industry = {}
    for industry_set in NETWORK_INDUSTRY_SETS:
        top_members = []
        for (member_set, bioguide), member in top_2021_2022.items():
            if member_set != industry_set:
                continue
            n_2021 = member[2021]
            n_2022 = member[2022]
            top_members.append(
                {
                    "bioguide": bioguide,
                    "n_bills_2021": n_2021,
                    "n_bills_2022": n_2022,
                    "total": n_2021 + n_2022,
                    "yoy_change": n_2022 - n_2021,
                    "n_clients": len(member["clients"]),
                }
            )
        top_members.sort(key=lambda row: (-row["total"], row["bioguide"]))
        per_industry[industry_set] = {
            "yearly_totals": {
                str(year): total
                for year, total in sorted(yearly_totals[industry_set].items())
            },
            "distinct_members_2021_2022": len(top_members),
            "top_members_2021_2022": top_members[:50],
        }

    review_rows = []
    for row in client_rows:
        industry_set = _network_industry_set(row["side"])
        if industry_set not in NETWORK_REVIEW_SETS:
            continue
        review_rows.append(
            {
                "set": industry_set,
                "client_name": row["client_name"],
                "lob_id": row["lob_id"],
                "match_rule": row["match_rule"],
                "confidence": row["confidence"],
                "n_bills_2021_2022": review_attention[row["lob_id"]],
            }
        )
    review_rows.sort(
        key=lambda row: (
            row["set"],
            -row["n_bills_2021_2022"],
            row["client_name"],
            row["lob_id"],
        )
    )

    return {
        "member_rows": member_rows,
        "review_rows": review_rows,
        "coverage": {
            "rows_read": rows_read,
            "rows_matched_classified": rows_matched_classified,
            "n_blank_bioguide_rows": n_blank_bioguide_rows,
            "blank_bioguide_n_bills": blank_bioguide_n_bills,
            "rows_by_industry_set": rows_by_industry_set,
            "duplicate_dyad_rows": duplicate_dyad_rows,
            "unclassified_row_share": (
                unclassified_rows / rows_read if rows_read else 0.0
            ),
            "distinct_bioguides": len(distinct_bioguides),
            "year_min": year_min,
            "year_max": year_max,
        },
        "per_industry": per_industry,
    }


def _entity_ids(value):
    stripped = (value or "").strip().strip("{}")
    if not stripped:
        return set(), 0
    tokens = [piece.strip() for piece in stripped.split(",") if piece.strip()]
    return set(tokens), sum(not token.isdigit() for token in tokens)


def _load_issues(path, finance_codes):
    total_rows = 0
    distinct_reports = set()
    rows_per_code = {}
    hill_rows_per_code = {}
    finance_reports = {}
    n_nonnumeric_entity_tokens = 0

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != ISSUE_INPUT_FIELDS:
            raise IngestError(
                "issues.csv header mismatch: expected "
                + ",".join(ISSUE_INPUT_FIELDS)
            )
        for data_row_number, source in enumerate(reader, start=1):
            if len(source) != len(header):
                raise IngestError(
                    f"issues.csv data row {data_row_number}: wrong field count "
                    f"(expected {len(header)}, got {len(source)})"
                )
            if source == header:
                raise IngestError(
                    f"issues.csv data row {data_row_number}: repeated header"
                )
            report_uuid = source[0].strip()
            if not report_uuid:
                raise IngestError(
                    f"issues.csv data row {data_row_number}: blank report_uuid"
                )
            code = source[1].strip().upper()
            if not code:
                raise IngestError(
                    f"issues.csv data row {data_row_number}: blank general_issue_code"
                )
            total_rows += 1
            entities, nonnumeric_entity_tokens = _entity_ids(source[3])
            n_nonnumeric_entity_tokens += nonnumeric_entity_tokens
            on_hill = "1" in entities or "2" in entities

            distinct_reports.add(report_uuid)
            rows_per_code[code] = rows_per_code.get(code, 0) + 1
            if on_hill:
                hill_rows_per_code[code] = hill_rows_per_code.get(code, 0) + 1

            if code in finance_codes:
                aggregate = finance_reports.get(report_uuid)
                if aggregate is None:
                    aggregate = {"codes": set(), "hill": False, "n_finance_rows": 0}
                    finance_reports[report_uuid] = aggregate
                aggregate["codes"].add(code)
                aggregate["hill"] = aggregate["hill"] or on_hill
                aggregate["n_finance_rows"] += 1

            if total_rows % 500000 == 0:
                print(f"issues: processed {total_rows} rows")

    if total_rows == 0:
        raise IngestError("issues.csv has 0 data rows")
    return {
        "total_rows": total_rows,
        "distinct_reports": len(distinct_reports),
        "rows_per_code": dict(sorted(rows_per_code.items())),
        "hill_rows_per_code": dict(sorted(hill_rows_per_code.items())),
        "finance_reports": finance_reports,
        "n_nonnumeric_entity_tokens": n_nonnumeric_entity_tokens,
    }


def _issue_index_rows(finance_reports):
    for report_uuid in sorted(finance_reports):
        aggregate = finance_reports[report_uuid]
        yield {
            "report_uuid": report_uuid,
            "codes": "|".join(sorted(aggregate["codes"])),
            "hill": 1 if aggregate["hill"] else 0,
            "n_finance_rows": aggregate["n_finance_rows"],
        }


def _process_reports(path, finance_reports, client_info, joined_temp_path):
    reports_rows = 0
    finance_joined = 0
    duplicate_report_uuids = 0
    n_amendment_rows = 0
    duplicate_natural_keys = 0
    n_blank_bool = 0
    blank_lob_id_rows = 0
    unknown_lob_id_rows = 0
    seen_report_uuids = set()
    seen_natural_keys = set()
    matched_finance_reports = set()
    max_finance_joined_filing_year = None
    adversary_spend = {
        "bank": {"high": {}, "low": {}},
        "credit_union": {"high": {}, "low": {}},
    }
    top_spender_totals = {"bank": {}, "credit_union": {}}

    def joined_rows():
        nonlocal reports_rows, finance_joined, duplicate_report_uuids
        nonlocal n_amendment_rows, duplicate_natural_keys, n_blank_bool
        nonlocal blank_lob_id_rows, unknown_lob_id_rows
        nonlocal max_finance_joined_filing_year
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header != REPORT_INPUT_FIELDS:
                raise IngestError(
                    "reports.csv header mismatch: expected "
                    + ",".join(REPORT_INPUT_FIELDS)
                )
            for data_row_number, source in enumerate(reader, start=1):
                if len(source) != len(header):
                    raise IngestError(
                        f"reports.csv data row {data_row_number}: wrong field count "
                        f"(expected {len(header)}, got {len(source)})"
                    )
                if source == header:
                    raise IngestError(
                        f"reports.csv data row {data_row_number}: repeated header"
                    )

                report_uuid = source[0].strip()
                if not report_uuid:
                    raise IngestError(
                        f"reports.csv data row {data_row_number}: blank report_uuid"
                    )
                lob_id = source[1].strip()
                filing_year = source[4].strip()
                if len(filing_year) != 4 or not filing_year.isdigit():
                    raise IngestError(
                        f"reports.csv data row {data_row_number}: filing_year is "
                        "not a 4-digit integer"
                    )
                filing_period_code = source[5].strip()
                if filing_period_code not in {"H1", "H2", "Q1", "Q2", "Q3", "Q4"}:
                    raise IngestError(
                        f"reports.csv data row {data_row_number}: invalid "
                        f"filing_period_code {filing_period_code!r}"
                    )
                amount_text = source[6].strip()
                if amount_text:
                    try:
                        amount = float(amount_text)
                    except ValueError as exc:
                        raise IngestError(
                            f"reports.csv data row {data_row_number}: invalid amount "
                            f"{amount_text!r}"
                        ) from exc
                    if not math.isfinite(amount):
                        raise IngestError(
                            f"reports.csv data row {data_row_number}: non-finite "
                            f"amount {amount_text!r}"
                        )
                    if amount < 0:
                        raise IngestError(
                            f"reports.csv data row {data_row_number}: negative "
                            f"amount {amount_text!r}"
                        )
                    if amount > 1e10:
                        raise IngestError(
                            f"reports.csv data row {data_row_number}: implausible "
                            f"amount {amount_text!r}"
                        )
                else:
                    amount = 0.0

                _parse_bool(source[7], "is_no_activity", data_row_number)
                is_self_filer = _parse_bool(
                    source[8], "is_self_filer", data_row_number
                )
                is_amendment = _parse_bool(
                    source[9], "is_amendment", data_row_number
                )

                reports_rows += 1
                if report_uuid in seen_report_uuids:
                    duplicate_report_uuids += 1
                    continue
                seen_report_uuids.add(report_uuid)

                n_blank_bool += sum(
                    value == "" for value in (source[7], source[8], source[9])
                )
                if is_amendment:
                    n_amendment_rows += 1

                registrant_id = source[2].strip()
                natural_key = (
                    lob_id,
                    registrant_id,
                    filing_year,
                    filing_period_code,
                )
                if natural_key in seen_natural_keys:
                    duplicate_natural_keys += 1
                else:
                    seen_natural_keys.add(natural_key)

                # Both linkability receipts use the post-dedup unique-report_uuid
                # population so their denominators are directly comparable.
                if not lob_id:
                    blank_lob_id_rows += 1
                if lob_id and lob_id not in client_info:
                    unknown_lob_id_rows += 1
                client_name, side, confidence = client_info.get(
                    lob_id, ("", "other", "low")
                )
                finance_aggregate = finance_reports.get(report_uuid)

                if side in adversary_spend:
                    yearly = adversary_spend[side][confidence]
                    aggregate = yearly.get(filing_year)
                    if aggregate is None:
                        aggregate = {
                            "n_reports": 0,
                            "amount_sum": 0.0,
                            "n_finance_reports": 0,
                            "finance_amount_sum": 0.0,
                            "n_self_filer_reports": 0,
                            "self_filer_finance_amount_sum": 0.0,
                            "n_hill_reports": 0,
                            "per_code": {},
                        }
                        yearly[filing_year] = aggregate
                    aggregate["n_reports"] += 1
                    aggregate["amount_sum"] += amount

                    if finance_aggregate is not None:
                        aggregate["n_finance_reports"] += 1
                        aggregate["finance_amount_sum"] += amount
                        if is_self_filer:
                            aggregate["n_self_filer_reports"] += 1
                            aggregate["self_filer_finance_amount_sum"] += amount
                        if finance_aggregate["hill"]:
                            aggregate["n_hill_reports"] += 1
                        for code in sorted(finance_aggregate["codes"]):
                            aggregate["per_code"][code] = (
                                aggregate["per_code"].get(code, 0) + 1
                            )

                        spender = top_spender_totals[side].get(lob_id)
                        if spender is None:
                            spender = {
                                "client_name": client_name,
                                "confidence": confidence,
                                "total_amount": 0.0,
                                "n_reports": 0,
                                "n_self_filer_reports": 0,
                                "min_year": filing_year,
                                "max_year": filing_year,
                            }
                            top_spender_totals[side][lob_id] = spender
                        spender["total_amount"] += amount
                        spender["n_reports"] += 1
                        if is_self_filer:
                            spender["n_self_filer_reports"] += 1
                        spender["min_year"] = min(
                            spender["min_year"], filing_year
                        )
                        spender["max_year"] = max(
                            spender["max_year"], filing_year
                        )

                if finance_aggregate is not None:
                    finance_joined += 1
                    matched_finance_reports.add(report_uuid)
                    filing_year_number = int(filing_year)
                    if (
                        max_finance_joined_filing_year is None
                        or filing_year_number > max_finance_joined_filing_year
                    ):
                        max_finance_joined_filing_year = filing_year_number
                    yield {
                        "report_uuid": report_uuid,
                        "lob_id": lob_id,
                        "client_name": client_name,
                        "side": side,
                        "confidence": confidence,
                        "filing_year": filing_year,
                        "filing_period_code": filing_period_code,
                        "amount": amount_text,
                        "is_self_filer": source[8],
                        "is_no_activity": source[7],
                        "is_amendment": source[9],
                        "registrant_id": registrant_id,
                        "registrant_name": source[3],
                        "codes": "|".join(sorted(finance_aggregate["codes"])),
                        "hill": 1 if finance_aggregate["hill"] else 0,
                        "n_finance_rows": finance_aggregate["n_finance_rows"],
                    }

    with open(
        joined_temp_path, "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_JOIN_FIELDS)
        writer.writeheader()
        writer.writerows(joined_rows())

    top_spenders = {"bank": [], "credit_union": []}
    for side in ("bank", "credit_union"):
        ranked = sorted(
            top_spender_totals[side].items(),
            key=lambda item: (-item[1]["total_amount"], item[0]),
        )[:25]
        for lob_id, spender in ranked:
            top_spenders[side].append(
                {
                    "lob_id": lob_id,
                    "client_name": spender["client_name"],
                    "confidence": spender["confidence"],
                    "total_amount": spender["total_amount"],
                    "n_reports": spender["n_reports"],
                    "n_self_filer_reports": spender["n_self_filer_reports"],
                    "first_year": spender["min_year"],
                    "last_year": spender["max_year"],
                }
            )

    join_stats = {
        "reports_rows": reports_rows,
        "finance_joined": finance_joined,
        "finance_unmatched": len(finance_reports) - len(matched_finance_reports),
        "duplicate_report_uuids": duplicate_report_uuids,
        "n_amendment_rows": n_amendment_rows,
        "duplicate_natural_keys": duplicate_natural_keys,
        "n_blank_bool": n_blank_bool,
        "blank_lob_id_rows": blank_lob_id_rows,
        "unknown_lob_id_rows": unknown_lob_id_rows,
    }
    return (
        join_stats,
        adversary_spend,
        top_spenders,
        max_finance_joined_filing_year,
    )


def _build_parser():
    local_appdata = os.environ.get("LOCALAPPDATA", "%LOCALAPPDATA%")
    default_raw = os.environ.get(
        "LOBBYVIEW_RAW", os.path.join(local_appdata, "Melange", "lobbyview", "raw")
    )
    default_out = os.environ.get(
        "LOBBYVIEW_OUT",
        os.path.join(local_appdata, "Melange", "lobbyview", "derived"),
    )
    parser = argparse.ArgumentParser(
        description="Ingest and classify the LobbyView 2024-10-25 bulk export."
    )
    parser.add_argument("--raw", default=default_raw, help="LobbyView raw directory")
    parser.add_argument("--out", default=default_out, help="Derived output directory")
    parser.add_argument(
        "--skip-issues",
        action="store_true",
        help="Skip issue, report, and network processing; inventory only",
    )
    return parser


def _run(args):
    raw_dir = os.path.abspath(args.raw)
    out_dir = os.path.abspath(args.out)
    raw_real = os.path.realpath(raw_dir)
    out_real = os.path.realpath(out_dir)
    root_real = os.path.realpath(MB_ROOT)
    for path_name, resolved_path in (("raw", raw_real), ("out", out_real)):
        try:
            under_root = os.path.normcase(
                os.path.commonpath([resolved_path, root_real])
            ) == os.path.normcase(root_real)
        except ValueError:
            under_root = False
        if under_root:
            raise IngestError(
                f"{path_name} path {resolved_path} is inside MB_ROOT; this collector "
                "is deliberately data-plane-independent"
            )
    codes_dial = _parse_codes(os.environ.get("LOBBYVIEW_CODES", DEFAULT_CODES))
    # These LDA codes cover the credit-union/bank fight surface, including bank,
    # credit, tax, housing, insurance, retirement, and monetary-policy issues.
    finance_codes = set(codes_dial)
    if not args.skip_issues and not finance_codes:
        raise IngestError(
            "LOBBYVIEW_CODES is empty; refusing to run with no finance codes"
        )

    # Fail closed before output-directory creation: validate every required path,
    # then fully read required tables and inventory all present future tables.
    if not os.path.isdir(raw_dir):
        raise IngestError(f"raw directory not found: {raw_dir}")
    clients_path = os.path.join(raw_dir, "clients.csv")
    if not os.path.isfile(clients_path):
        raise IngestError(f"required clients.csv not found: {clients_path}")
    issues_path = os.path.join(raw_dir, "issues.csv")
    if not args.skip_issues and not os.path.isfile(issues_path):
        raise IngestError(f"required issues.csv not found: {issues_path}")

    clients_md5 = _file_md5(clients_path)
    (
        client_rows,
        counts,
        adversaries,
        counts_by_confidence,
        client_info,
    ) = _load_clients(clients_path)
    if _file_md5(clients_path) != clients_md5:
        raise IngestError(f"input changed mid-run: {clients_path}")
    issue_results = None
    issues_md5 = None
    if not args.skip_issues:
        issues_md5 = _file_md5(issues_path)
        issue_results = _load_issues(issues_path, finance_codes)
        if _file_md5(issues_path) != issues_md5:
            raise IngestError(f"input changed mid-run: {issues_path}")
        if len(issue_results["finance_reports"]) == 0:
            codes_text = ", ".join(sorted(finance_codes))
            raise IngestError(
                f"0 finance reports matched codes {{{codes_text}}}; refusing to "
                "publish empty finance artifacts"
            )

    manifest_tables = {}
    manifest_tables["clients"] = {
        "present": True,
        "processed": True,
        "path": clients_path,
        "data_rows": len(client_rows),
        "md5": clients_md5,
        "header": list(CLIENT_INPUT_FIELDS),
        "matched_filename": "clients.csv",
    }
    if os.path.isfile(issues_path):
        if args.skip_issues:
            manifest_tables["issues"] = _inventory_csv(issues_path, count_rows=False)
        else:
            manifest_tables["issues"] = {
                "present": True,
                "processed": True,
                "path": issues_path,
                "data_rows": issue_results["total_rows"],
                "md5": issues_md5,
                "header": list(ISSUE_INPUT_FIELDS),
                "matched_filename": "issues.csv",
            }
    else:
        manifest_tables["issues"] = _absent_inventory()
        print("WAITING: issues not present in raw dir")

    absent_future = []
    reports_path = _matched_path(raw_dir, EXPECTED_TABLES["reports"])
    reports_md5 = None
    if reports_path is None:
        manifest_tables["reports"] = _absent_inventory()
        absent_future.append("reports")
        print("WAITING: reports not present in raw dir")
    elif args.skip_issues:
        manifest_tables["reports"] = _inventory_csv(reports_path)
    else:
        reports_md5 = _file_md5(reports_path)

    network_path = None
    for table in FUTURE_TABLES:
        matched = _matched_path(raw_dir, EXPECTED_TABLES[table])
        if matched is None:
            manifest_tables[table] = _absent_inventory()
            absent_future.append(table)
            print(f"WAITING: {table} not present in raw dir")
        else:
            manifest_tables[table] = _inventory_csv(matched)
            if table == "network":
                network_path = matched

    network_results = None
    if _network_processing_enabled(args.skip_issues, network_path):
        network_inventory = manifest_tables["network"]
        network_results = _process_network(
            network_path,
            client_rows,
            expected_rows=network_inventory["data_rows"],
        )
        if _file_md5(network_path) != network_inventory["md5"]:
            raise IngestError(f"input changed mid-run: {network_path}")
        manifest_tables["network"] = {
            "present": True,
            "processed": True,
            "path": network_path,
            "data_rows": network_results["coverage"]["rows_read"],
            "md5": network_inventory["md5"],
            "header": list(NETWORK_INPUT_FIELDS),
            "matched_filename": "network.csv",
        }

    generated = datetime.datetime.now(datetime.timezone.utc).isoformat()
    run_id = hashlib.sha1(
        (generated + raw_dir + out_dir).encode("utf-8")
    ).hexdigest()[:12]
    adversary_payload = {
        "provenance": {
            "vintage": VINTAGE,
            "generated": generated,
            "run_id": run_id,
            "rule_version": RULE_VERSION,
            "status": "candidate - adversary-set membership awaits user ratification",
            "source": "LobbyView bulk data export (Kim, In Song 2018)",
        },
        "counts": {side: counts[side] for side in SIDES},
        "counts_by_confidence": counts_by_confidence,
        "bank": adversaries["bank"],
        "credit_union": adversaries["credit_union"],
    }
    manifest_payload = {
        "vintage": VINTAGE,
        "generated": generated,
        "run_id": run_id,
        "raw_dir": raw_dir,
        "out_dir": out_dir,
    }
    manifest_payload.update(manifest_tables)

    # This is intentionally the first filesystem mutation in the ingest.
    os.makedirs(out_dir, exist_ok=True)
    produced_outputs = []
    report_join_stats = None
    if reports_path is not None and not args.skip_issues:
        reports_join_output = os.path.join(
            out_dir, "lobbyview_reports_finance.csv"
        )
        reports_join_temp = reports_join_output + ".tmp." + str(os.getpid())
        try:
            (
                report_join_stats,
                adversary_spend,
                top_spenders,
                max_finance_joined_filing_year,
            ) = _process_reports(
                reports_path,
                issue_results["finance_reports"],
                client_info,
                reports_join_temp,
            )
            if _file_md5(reports_path) != reports_md5:
                raise IngestError(f"input changed mid-run: {reports_path}")
            if report_join_stats["finance_joined"] == 0:
                n_finance_reports = len(issue_results["finance_reports"])
                raise IngestError(
                    f"0 of {n_finance_reports} finance report_uuids joined to "
                    "reports.csv; vintage mismatch or wrong reports file — "
                    "refusing to publish empty finance artifacts"
                )
            os.replace(reports_join_temp, reports_join_output)
        except BaseException:
            try:
                os.remove(reports_join_temp)
            except OSError:
                pass
            raise
        manifest_payload["reports"] = {
            "present": True,
            "processed": True,
            "path": reports_path,
            "data_rows": report_join_stats["reports_rows"],
            "md5": reports_md5,
            "header": list(REPORT_INPUT_FIELDS),
            "matched_filename": "reports.csv",
        }
        produced_outputs.append(reports_join_output)
        spend_payload = {
            "provenance": {
                "vintage": VINTAGE,
                "generated": generated,
                "run_id": run_id,
                "rule_version": RULE_VERSION,
                "status": "candidate - adversary-set membership awaits user ratification",
                "source": "LobbyView bulk data export (Kim, In Song 2018)",
            },
            "coverage": {
                "max_finance_joined_filing_year": (
                    max_finance_joined_filing_year
                ),
                "note": (
                    "issue-code coverage collapses after filing_year 2021 (only "
                    "a sliver of 2022 reports carries issue codes; measured "
                    "max_finance_joined_filing_year records the actual edge); "
                    "reports-only coverage runs through 2023 plus a 2024 sliver, "
                    "so post-2021 finance columns are structurally undercounted."
                ),
            },
            "by_year": adversary_spend,
            "top_spenders": top_spenders,
            "join_stats": report_join_stats,
        }
        spend_output = os.path.join(out_dir, "lobbyview_adversary_spend.json")
        dump_atomic(
            spend_payload,
            spend_output,
            indent=1,
            ensure_ascii=False,
        )
        produced_outputs.append(spend_output)

    if network_results is not None:
        network_member_output = os.path.join(
            out_dir, "lobbyview_network_member.csv"
        )
        _write_csv_atomic(
            network_member_output,
            NETWORK_MEMBER_FIELDS,
            network_results["member_rows"],
        )
        produced_outputs.append(network_member_output)

        network_industry_payload = {
            "provenance": {
                "vintage": VINTAGE,
                "generated": generated,
                "run_id": run_id,
            },
            "coverage": network_results["coverage"],
            "caveats": _network_caveats(
                network_results["coverage"]["year_max"]
            ),
            "per_industry": network_results["per_industry"],
        }
        network_industry_output = os.path.join(
            out_dir, "lobbyview_network_industry.json"
        )
        dump_atomic(
            network_industry_payload,
            network_industry_output,
            indent=1,
            ensure_ascii=False,
        )
        produced_outputs.append(network_industry_output)

        industry_review_output = os.path.join(
            out_dir, "lobbyview_industry_review.csv"
        )
        _write_csv_atomic(
            industry_review_output,
            INDUSTRY_REVIEW_FIELDS,
            network_results["review_rows"],
        )
        produced_outputs.append(industry_review_output)

    clients_output = os.path.join(out_dir, "lobbyview_clients_classified.csv")
    _write_csv_atomic(
        clients_output,
        CLIENT_OUTPUT_FIELDS,
        client_rows,
    )
    produced_outputs.append(clients_output)
    adversary_output = os.path.join(out_dir, "lobbyview_adversary_clients.json")
    dump_atomic(
        adversary_payload,
        adversary_output,
        indent=1,
        ensure_ascii=False,
    )
    produced_outputs.append(adversary_output)

    if not args.skip_issues:
        finance_report_count = len(issue_results["finance_reports"])
        issue_index_output = os.path.join(
            out_dir, "lobbyview_issue_index_finance.csv"
        )
        _write_csv_atomic(
            issue_index_output,
            ISSUE_OUTPUT_FIELDS,
            _issue_index_rows(issue_results["finance_reports"]),
        )
        produced_outputs.append(issue_index_output)
        issue_stats = {
            "run_id": run_id,
            "total_rows": issue_results["total_rows"],
            "distinct_reports": issue_results["distinct_reports"],
            "rows_per_code": issue_results["rows_per_code"],
            "hill_rows_per_code": issue_results["hill_rows_per_code"],
            "finance_reports": finance_report_count,
            "n_nonnumeric_entity_tokens": issue_results[
                "n_nonnumeric_entity_tokens"
            ],
            "codes_dial": codes_dial,
        }
        issue_stats_output = os.path.join(out_dir, "lobbyview_issue_stats.json")
        dump_atomic(
            issue_stats,
            issue_stats_output,
            indent=1,
            ensure_ascii=False,
        )
        produced_outputs.append(issue_stats_output)

    # The manifest is the final write: its presence certifies that every earlier
    # output for this run reached its atomic replacement point.
    manifest_payload["outputs"] = {
        os.path.basename(path): _file_md5(path) for path in produced_outputs
    }
    dump_atomic(
        manifest_payload,
        os.path.join(out_dir, "lobbyview_manifest.json"),
        indent=1,
        ensure_ascii=False,
    )

    absent_text = ",".join(absent_future) if absent_future else "none"
    if args.skip_issues:
        issue_summary = "issues SKIPPED"
    else:
        issue_summary = (
            f"issues rows {issue_results['total_rows']} -> finance reports "
            f"{len(issue_results['finance_reports'])}"
        )
    if report_join_stats is None:
        report_summary = ""
    else:
        report_summary = (
            f" | reports rows {report_join_stats['reports_rows']} -> "
            f"finance-joined {report_join_stats['finance_joined']}"
        )
    if network_results is None:
        network_summary = ""
    else:
        network_summary = (
            f" | network rows {network_results['coverage']['rows_read']} -> "
            f"member-years {len(network_results['member_rows'])}"
        )
    print(
        f"lobbyview ingest: clients {len(client_rows)} "
        f"(bank {counts['bank']}, cu {counts['credit_union']}, "
        f"fin_other {counts['financial_other']}) | {issue_summary}"
        f"{report_summary}{network_summary} | "
        f"absent: {absent_text}"
    )
    return 0


def main():
    # 2026-07-31: Windows limits csv.field_size_limit to a 32-bit C long.
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            break
        except OverflowError:
            limit //= 2
    args = _build_parser().parse_args()
    try:
        return _run(args)
    except IngestError as exc:
        print(f"ERROR: {exc}")
        return 2
    except Exception as exc:
        print(f"ERROR: lobbyview ingest failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
