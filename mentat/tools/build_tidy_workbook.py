"""Write the Tableau-shaped `<Industry> Industry Spending Data.xlsx` from a tidy staging store.

Future repo path: `mentat/tools/build_tidy_workbook.py`.

This implements section 3 of the tidy staging contract (`STAGING_CONTRACT.md`,
schema_version `tidy-1`): the sheet order, the column header registry of section
2, the per-cycle split above `--split-rows`, the `About` provenance rows (whose
`Lineage` value is the single literal `Source DB SHA-256: <hex>` that
`app/contribution_data._workbook_source` :152-167 scans sheet XML for), and the
`Data Quality` sheet carrying the store's own rows plus the writer's checks
W1-W11.

A sheet over `--split-rows` becomes one sheet per cycle named `<split base>
<election year>`, e.g. `Contributions 2022` (Paul, S1, 2026-09-26). Section 3's
`<Sheet> <cycle_label>` spelling made `Network Candidate Activity 2021-2022`, 36
characters where Excel allows 31, so every all-cycles build that split that sheet
crashed; that sheet now splits under the base `Network Candidates`. Books published
before keep the old spelling until they are rebuilt, and the console reads both
(`app/report_insights.split_sheet_base`).

Nothing is published unless every severity-`fail` check passed: the workbook is
written to `<out>.pending-<pid>` and only then `os.replace`d into place. On a
failure the pending file is removed and the process exits 2, so a workbook whose
numbers disagree with the source database never reaches the report directory.

Every input is read-only. SQLite is opened through a `mode=ro` URI with
`PRAGMA query_only=ON`; the payload is never `json.load`ed whole.

Stdlib + openpyxl only, Python 3.12.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import warnings
import zipfile
from xml.etree import ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo

WRITER_VERSION = "tidy-writer-1.1.0"
SCHEMA_VERSION = "tidy-1"
DEFAULT_SPLIT_ROWS = 50000
MAX_SHEET_NAME = 31
# Excel refuses a sheet name holding any of these characters.
EXCEL_FORBIDDEN_SHEET_CHARACTERS = frozenset("[]:*?/\\")

MONEY_FORMAT = "$#,##0.00;[Red]($#,##0.00);-"
INTEGER_FORMAT = "#,##0"
YEAR_FORMAT = "0"
DATE_FORMAT = "yyyy-mm-dd"

# W9 `no_multivalue_cells` checks an explicit INCLUDE-LIST of categorical dimension
# columns, not "every text column except an exempt list" (Paul's ruling, 2026-09-15).
#
# Why the rule was inverted. The old form checked every T/ID column except a list of
# known prose columns, so the guard's correctness depended on that list being
# exhaustive for every dataset it would ever meet. It was not. The first live Credit
# Union export refused to publish on two false positives:
#     Contributions / Receipt Type (6)
#         "JFC TRANSFER: SAVE AMERICA JOINT FUNDRAISING COMMITTEE; 2021 AGGREGATE"
#     Downstream Activity / Source Committee (78)
#         "MADISON PAC; THE"      - one real FEC committee name, inverted article
# Neither is a packed multi-value cell. FEC names and filed descriptions legitimately
# contain ';' and cannot be enumerated in advance, because they are other people's
# free text. A packed "a; b" list, by contrast, can only appear in a column OUR OWN
# derivations write, and those are a small, closed, enumerable set.
#
# So W9 now asks "is this one of the categorical dimensions we derive?" rather than
# "is this not prose?". A header absent from this list is NOT checked. That is the
# intended, deliberate weakening: better to miss a packed value in a name column
# (where it cannot arise) than to refuse a correct workbook over "MADISON PAC; THE".
MULTIVALUE_CHECKED_HEADERS = frozenset((
    "Election Cycle", "Cycle Alignment", "Contributor Tier", "Recipient Category",
    "Category Source", "Recipient Party", "Party Source", "Committee Type",
    "Committee Designation", "Record Type", "Source Table", "Funding Source Type",
    "Funding Direction", "Receipt Role", "Committee Relationship", "Mapping Source",
    "Relationship", "Link Source", "Candidate Party", "Office", "Incumbent Status",
    "Activity Type", "Support / Oppose", "Transaction Type", "Funding Type",
    "Direction", "Traversal Status", "State", "Sponsor State", "Sponsor Office",
    "Committee Party", "Committee State", "Office Code",
))

# DOCUMENTATION ONLY - not consulted by W9 or by the Tableau lint any more.
# These are the columns the previous exempt-list form named as deliberate prose or
# encoded records. Kept as the record of that reasoning, and because it explains why
# each must stay OUT of MULTIVALUE_CHECKED_HEADERS above:
#     "Memo Text", "Source URL", "Representative Path", "Transfer Records",
#     "Unresolved Reasons", "Detail", "Inclusion Reason", "Duplicate Reason",
#     "Company Match Reason", "Accounting Note", "Rule",
#     "Ownership Status"  - committee_map prose, e.g. "Organization/committee; no
#                           individual ownership link in FEC files"
# NOTE: "Traversal Status" was on that exempt list and is now CHECKED, because it is a
# status enum our own traversal writes rather than filed text. It had 0 rows in the
# 2026-09-15 Credit Union run (transfer_paths was empty), so the promotion is not yet
# verified against real data - if it ever proves to carry prose, take it back out.
MULTIVALUE_EXEMPT_HEADERS_DOC = (
    "Memo Text", "Source URL", "Representative Path", "Transfer Records",
    "Unresolved Reasons", "Detail", "Inclusion Reason", "Duplicate Reason",
    "Company Match Reason", "Accounting Note", "Rule", "Ownership Status",
)

TRUE_WORDS = frozenset(("1", "y", "yes", "true", "t"))
FALSE_WORDS = frozenset(("0", "n", "no", "false", "f"))

# A text value that opens with one of these is a spreadsheet formula in waiting
# (OWASP CSV injection list). openpyxl would turn a leading '=' into a live formula.
FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


# --------------------------------------------------------------------------
# Column registry (contract section 2). (store column, sheet header, kind)
# kinds: T text, ID text-identifier, I integer, R money, D date, B flag
# --------------------------------------------------------------------------
CONTRIBUTIONS = (
    ("source_id", "Source ID", "ID"), ("transaction_id", "Transaction ID", "ID"),
    ("back_reference_transaction_id", "Back Reference Transaction ID", "ID"),
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("source_cycle", "FEC Filing Cycle", "I"), ("cycle_alignment", "Cycle Alignment", "T"),
    ("company", "Company", "T"), ("contributor", "Contributor", "T"),
    ("person_key", "Person Key", "T"), ("identity_basis", "Identity Basis", "T"),
    ("employee_tier", "Contributor Tier", "T"), ("employer", "Employer", "T"),
    ("occupation", "Occupation", "T"),
    ("street_1", "Street 1", "T"), ("street_2", "Street 2", "T"),
    ("city", "City", "T"), ("state", "State", "T"),
    ("zip5", "Contributor ZIP", "ID"), ("zipcode", "Contributor ZIP Raw", "ID"),
    ("date", "Date", "D"), ("amount", "Amount", "R"),
    ("analysis_amount", "Analysis Amount", "R"),
    ("include_in_analysis", "Include in Analysis", "B"),
    ("inclusion_reason", "Inclusion Reason", "T"),
    ("company_match_status", "Company Match Status", "T"),
    ("company_match_reason", "Company Match Reason", "T"),
    ("company_attribution_status", "Company Attribution Status", "T"),
    ("company_attribution_reason", "Company Attribution Reason", "T"),
    ("receipt_role_unverified", "Receipt Role Unverified", "B"),
    ("receipt_role_reason", "Receipt Role Reason", "T"),
    ("matched_alias", "Matched Alias", "T"), ("recipient", "Recipient", "T"),
    ("committee_id", "Committee ID", "ID"), ("recipient_key", "Recipient Key", "T"),
    ("recipient_category", "Recipient Category", "T"),
    ("category_source", "Category Source", "T"), ("party", "Recipient Party", "T"),
    ("party_source", "Party Source", "T"),
    ("committee_type_code", "Committee Type Code", "T"),
    ("committee_type", "Committee Type", "T"),
    ("committee_designation_code", "Committee Designation Code", "T"),
    ("committee_designation", "Committee Designation", "T"),
    ("record_type", "Record Type", "T"), ("source_table", "Source Table", "T"),
    ("funding_source_type", "Funding Source Type", "T"),
    ("funding_direction", "Funding Direction", "T"),
    ("receipt_role", "Receipt Role", "T"), ("receipt_type_full", "Receipt Type", "T"),
    ("filing_form", "Filing Form", "T"), ("line_number", "Line Number", "ID"),
    ("memo_text", "Memo Text", "T"), ("is_memo_leg", "Memo Leg", "B"),
    ("is_conduit_leg", "Conduit Leg", "B"),
    ("superseded_by_amendment", "Superseded Amendment", "B"),
    ("duplicate_filing_version", "Duplicate Filing Version", "B"),
    ("duplicate_memo", "Duplicate Memo", "B"),
    ("duplicate_of_sub_id", "Duplicate Of Source ID", "ID"),
    ("duplicate_reason", "Duplicate Reason", "T"),
    ("recipient_state", "Recipient State", "T"),
    ("represented_state", "Represented State", "T"),
    ("source_url", "Source URL", "T"),
)

GRAIN = (("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
         ("company", "Company", "T"))

GIVING_FACTS = GRAIN + (
    ("employee_tier", "Contributor Tier", "T"),
    ("recipient_category", "Recipient Category", "T"), ("party", "Recipient Party", "T"),
    ("giving", "Giving", "R"), ("gift_count", "Gift Count", "I"),
    ("distinct_contributors", "Distinct Contributors", "I"),
    ("distinct_recipients", "Distinct Recipients", "I"),
)

RECIPIENT_FACTS = GRAIN + (
    ("committee_id", "Committee ID", "ID"), ("employee_tier", "Contributor Tier", "T"),
    ("recipient", "Recipient", "T"), ("recipient_category", "Recipient Category", "T"),
    ("category_source", "Category Source", "T"), ("party", "Recipient Party", "T"),
    ("party_source", "Party Source", "T"),
    ("committee_type_code", "Committee Type Code", "T"),
    ("committee_type", "Committee Type", "T"),
    ("committee_designation_code", "Committee Designation Code", "T"),
    ("committee_designation", "Committee Designation", "T"),
    ("sponsor_candidate_id", "Sponsor Candidate ID", "ID"),
    ("sponsor_candidate", "Sponsor Candidate", "T"),
    ("sponsor_office", "Sponsor Office", "T"), ("sponsor_state", "Sponsor State", "T"),
    ("sponsor_district", "Sponsor District", "ID"),
    ("sponsor_count", "Sponsor Count", "I"),
    ("connected_organization", "Connected Organization", "T"),
    ("committee_relationship", "Committee Relationship", "T"),
    ("ownership_status", "Ownership Status", "T"), ("giving", "Giving", "R"),
    ("gift_count", "Gift Count", "I"),
    ("distinct_contributors", "Distinct Contributors", "I"),
    ("first_gift_date", "First Gift Date", "D"), ("last_gift_date", "Last Gift Date", "D"),
    ("fec_committee_url", "FEC Committee URL", "T"),
)

CONTRIBUTOR_FACTS = GRAIN + (
    ("person_key", "Person Key", "T"), ("party", "Recipient Party", "T"),
    ("contributor", "Contributor", "T"), ("employee_tier", "Contributor Tier", "T"),
    ("identity_basis", "Identity Basis", "T"), ("city", "City", "T"),
    ("state", "State", "T"), ("zip5", "Contributor ZIP", "ID"),
    ("employer", "Employer", "T"), ("occupation", "Occupation", "T"),
    ("distinct_addresses", "Distinct Addresses", "I"), ("giving", "Giving", "R"),
    ("gift_count", "Gift Count", "I"),
)

LOCATION_FACTS = GRAIN + (
    ("state", "State", "T"), ("city", "City", "T"), ("zip5", "Contributor ZIP", "ID"),
    ("giving", "Giving", "R"), ("gift_count", "Gift Count", "I"),
    ("distinct_contributors", "Distinct Contributors", "I"),
)

COMMITTEES = (
    ("committee_id", "Committee ID", "ID"), ("cycle_label", "Election Cycle", "T"),
    ("cycle", "Cycle End Year", "I"), ("committee_name", "Committee", "T"),
    ("committee_type_code", "Committee Type Code", "T"),
    ("committee_type", "Committee Type", "T"),
    ("committee_designation_code", "Committee Designation Code", "T"),
    ("committee_designation", "Committee Designation", "T"),
    ("party", "Committee Party", "T"), ("party_affiliation", "Party Affiliation", "T"),
    ("committee_state", "Committee State", "T"),
    ("recipient_category", "Recipient Category", "T"),
    ("category_source", "Category Source", "T"), ("party_source", "Party Source", "T"),
    ("connected_organization", "Connected Organization", "T"),
    ("ownership_status", "Ownership Status", "T"),
    ("committee_relationship", "Committee Relationship", "T"),
    ("mapping_source", "Mapping Source", "T"), ("sponsor_count", "Sponsor Count", "I"),
    ("top_downstream_candidate_id", "Top Downstream Candidate ID", "ID"),
    ("top_downstream_candidate", "Top Downstream Candidate", "T"),
    ("top_downstream_activity", "Top Downstream Activity", "R"),
    ("fec_committee_url", "FEC Committee URL", "T"),
)

COMMITTEE_SPONSORS = (
    ("committee_id", "Committee ID", "ID"), ("candidate_id", "Candidate ID", "ID"),
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("relationship", "Relationship", "T"), ("link_source", "Link Source", "T"),
    ("candidate", "Candidate", "T"), ("source_url", "Source URL", "T"),
)

CANDIDATES = (
    ("candidate_id", "Candidate ID", "ID"), ("candidate", "Candidate", "T"),
    ("party", "Candidate Party", "T"), ("party_affiliation", "Party Affiliation", "T"),
    ("office_code", "Office Code", "T"), ("office", "Office", "T"),
    ("state", "State", "T"), ("district", "District", "ID"),
    ("incumbent_status", "Incumbent Status", "T"),
    ("principal_committee_id", "Principal Committee ID", "ID"),
    ("fec_candidate_url", "FEC Candidate URL", "T"), ("source_url", "Source URL", "T"),
)

DOWNSTREAM_ACTIVITY = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("source_committee", "Source Committee", "T"),
    ("source_committee_id", "Source Committee ID", "ID"),
    ("source_recipient_category", "Source Recipient Category", "T"),
    ("candidate", "Candidate", "T"), ("candidate_id", "Candidate ID", "ID"),
    ("candidate_party", "Candidate Party", "T"), ("office", "Office", "T"),
    ("state", "State", "T"), ("district", "District", "ID"),
    ("activity_type", "Activity Type", "T"), ("support_oppose", "Support / Oppose", "T"),
    ("transaction_type", "Transaction Type", "T"),
    ("first_activity_date", "First Activity Date", "D"),
    ("last_activity_date", "Last Activity Date", "D"),
    ("source_rows", "Source Rows", "I"), ("included_rows", "Included Rows", "I"),
    ("excluded_rows", "Excluded Rows", "I"), ("superseded_rows", "Superseded Rows", "I"),
    ("memo_rows", "Memo Rows", "I"), ("duplicate_rows", "Duplicate Rows", "I"),
    ("unverified_version_rows", "Unverified Version Rows", "I"),
    ("unresolved_version_rows", "Unresolved Version Rows", "I"),
    ("unresolved_version_amount", "Unresolved Version Amount", "R"),
    ("repeated_itemization_rows", "Repeated Itemization Rows", "I"),
    ("reported_amount", "Reported Amount", "R"),
    ("excluded_amount", "Excluded Amount", "R"),
    ("net_included_amount", "Net Included Amount", "R"),
    ("fec_committee_url", "FEC Committee URL", "T"),
    ("fec_candidate_url", "FEC Candidate URL", "T"), ("source_url", "Source URL", "T"),
    ("transfer_records", "Transfer Records", "T"),
    ("filing_records", "Filing Records", "T"),
)

BENEFICIARY_BRIDGE = (
    ("bridge_key", "Bridge Key", "T"), ("cycle_label", "Election Cycle", "T"),
    ("cycle", "Cycle End Year", "I"), ("company", "Company", "T"),
    ("source_committee", "Source Committee", "T"),
    ("source_committee_id", "Source Committee ID", "ID"),
    ("source_recipient_category", "Source Recipient Category", "T"),
    ("candidate", "Candidate", "T"), ("candidate_id", "Candidate ID", "ID"),
    ("candidate_party", "Candidate Party", "T"), ("office", "Office", "T"),
    ("state", "State", "T"), ("district", "District", "ID"),
    ("transaction_count", "Transaction Count", "I"),
    ("first_activity_date", "First Activity Date", "D"),
    ("last_activity_date", "Last Activity Date", "D"),
    ("downstream_activity", "Downstream Activity", "R"),
    ("support_amount", "Support Activity", "R"),
    ("oppose_amount", "Oppose Activity", "R"),
    ("other_activity_amount", "Contribution / Other Activity", "R"),
    ("associated_funding", "Associated Funding", "R"),
)

# F14 (Paul approved option (a), 2026-09-26): one row per transfer edge and cycle.
# The root x committee x edge rows are the store's transfer_path_detail table, which
# is not a sheet (8.9M rows for Crypto): it stays in the store for the console.
TRANSFER_PATHS = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("source_committee", "Source Committee", "ID"),
    ("destination_committee", "Destination Committee", "ID"),
    ("reported_net_transfer", "Reported Net Transfer", "R"),
    ("record_count", "Record Count", "I"),
    ("first_transfer_date", "First Transfer", "D"), ("last_transfer_date", "Last Transfer", "D"),
    ("traversal_status", "Traversal Status", "T"),
    ("funded_roots_reaching", "Funded Roots Reaching", "I"),
    ("shortest_depth", "Shortest Depth", "I"),
    ("top_funded_roots", "Top Funded Roots", "T"),
    ("roots_via_carryover", "Roots via Carryover", "I"),
    ("date_rule", "Date Rule", "T"),
    ("source_gate", "Source Gate", "D"), ("source_gate_source", "Source Gate From", "T"),
    ("excluded_before_gate_records", "Records Before Gate", "I"),
    ("excluded_before_gate_amount", "Excluded Before Gate Amount", "R"),
    ("source_carryover_route", "Carryover Route", "T"),
    ("carryover_status", "Carryover Status", "T"),
)

FUNDING_COHORTS = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("company", "Company", "T"), ("funding_type", "Funding Type", "T"),
    ("recipient_committee", "Recipient Committee", "T"),
    ("receipt_role", "Receipt Role", "T"),
    ("included_net_receipts", "Included Net Receipts", "R"),
    ("receipt_count", "Receipt Count", "I"), ("first_receipt", "First Receipt", "D"),
    ("last_receipt", "Last Receipt", "D"),
)

ALLOCATION_ESTIMATES = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("funded_committee", "Funded Committee", "ID"),
    ("candidate_id", "Candidate ID", "ID"), ("candidate", "Candidate", "T"),
    ("direction", "Direction", "T"),
    ("conditional_estimate", "Conditional Estimate", "R"),
)

ALLOCATION_COVERAGE = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("funded_committee", "Funded Committee", "ID"),
    ("net_input_funding", "Net Input Funding", "R"),
    ("nonnegative_model_funding", "Nonnegative Model Funding", "R"),
    ("estimated_candidate_activity", "Estimated Candidate Activity", "R"),
    ("unresolved", "Unresolved", "R"),
    ("negative_net_funding", "Negative Net Funding", "R"),
    ("unresolved_reasons", "Unresolved Reasons", "T"),
)

SOURCE_FRESHNESS = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("fec_dataset", "FEC Dataset", "T"), ("checked_at", "Checked At", "T"),
    ("server_last_modified", "Server Last Modified", "T"), ("sha256", "SHA-256", "T"),
    ("source_url", "Source URL", "T"),
)

CLASSIFICATION_REVIEW = (
    ("cycle_label", "Election Cycle", "T"), ("cycle", "Cycle End Year", "I"),
    ("committee_id", "Committee ID", "ID"), ("committee", "Committee", "T"),
    ("official_type", "Official Type", "T"),
    ("reporting_category", "Reporting Category", "T"),
    ("included_funding", "Included Funding", "R"),
)

ACCOUNTING_BASIS = (("basis", "Basis", "T"), ("rule", "Rule", "T"))

DATA_QUALITY = (
    ("check_id", "Check", "T"), ("source", "Source", "T"), ("severity", "Severity", "T"),
    ("passed", "Passed", "B"), ("detail", "Detail", "T"), ("metric", "Metric", "T"),
)

ABOUT = (("key", "Key", "T"), ("value", "Value", "T"))


class Sheet:
    """One staging table rendered as one sheet (or one sheet per cycle)."""

    __slots__ = ("table", "name", "columns", "key", "dynamic", "split_base")

    def __init__(self, table, name, columns, key, dynamic=False, split_base=None):
        self.table, self.name, self.columns = table, name, columns
        self.key, self.dynamic = key, dynamic
        # A per-cycle part is `<split_base> <election year>` (split_sheet_name).
        # Every sheet splits under its own name except the one Paul gave a
        # shorter base (S1, 2026-09-26).
        self.split_base = split_base or name


SHEETS = (
    Sheet("contributions", "Contributions", CONTRIBUTIONS, ("source_id",)),
    Sheet("giving_facts", "Giving Facts", GIVING_FACTS,
          ("cycle_label", "company", "employee_tier", "recipient_category", "party")),
    Sheet("recipient_facts", "Recipient Facts", RECIPIENT_FACTS,
          ("cycle_label", "company", "committee_id", "employee_tier")),
    Sheet("contributor_facts", "Contributor Facts", CONTRIBUTOR_FACTS,
          ("cycle_label", "company", "person_key", "party")),
    Sheet("location_facts", "Location Facts", LOCATION_FACTS,
          ("cycle_label", "company", "state", "city", "zip5")),
    Sheet("committees", "Committees", COMMITTEES, ("committee_id", "cycle_label")),
    Sheet("committee_sponsors", "Committee Sponsors", COMMITTEE_SPONSORS,
          ("committee_id", "candidate_id", "cycle_label", "link_source")),
    Sheet("candidates", "Candidates", CANDIDATES, ("candidate_id",)),
    # `candidate` extends the contract's stated key: 92 groups in the Credit
    # Union cache share one candidate_id across different payee names (vendor
    # rows in the FEC pas2 file), so the contract key is not unique on real data.
    Sheet("downstream_activity", "Downstream Activity", DOWNSTREAM_ACTIVITY,
          ("cycle_label", "source_committee_id", "candidate_id", "activity_type",
           "support_oppose", "transaction_type", "candidate")),
    Sheet("beneficiary_bridge", "Beneficiary Bridge", BENEFICIARY_BRIDGE,
          ("bridge_key",), dynamic=True),
    Sheet("beneficiary_activity", "Network Candidate Activity", BENEFICIARY_BRIDGE,
          ("cycle_label", "source_committee_id", "candidate_id"), dynamic=True,
          split_base="Network Candidates"),
    Sheet("transfer_paths", "Transfer Paths", TRANSFER_PATHS,
          ("cycle_label", "source_committee", "destination_committee")),
    Sheet("funding_cohorts", "Funding Cohorts", FUNDING_COHORTS,
          ("cycle_label", "company", "funding_type", "recipient_committee",
           "receipt_role")),
    Sheet("allocation_estimates", "Allocation Estimates", ALLOCATION_ESTIMATES,
          ("cycle_label", "funded_committee", "candidate_id", "direction")),
    Sheet("allocation_coverage", "Allocation Coverage", ALLOCATION_COVERAGE,
          ("cycle_label", "funded_committee")),
    Sheet("source_freshness", "Source Freshness", SOURCE_FRESHNESS,
          ("cycle_label", "fec_dataset")),
    Sheet("downstream_reach", "Downstream Reach", (),
          ("cycle", "view", "company", "source_committee_id"), dynamic=True),
    Sheet("downstream_measurement_ledger", "Downstream Measurement", (),
          ("cycle", "view", "company", "candidate_id"), dynamic=True),
    Sheet("downstream_sources", "Downstream Sources", (),
          ("cycle", "source"), dynamic=True),
    Sheet("classification_review", "Classification Review", CLASSIFICATION_REVIEW,
          ("cycle_label", "committee_id")),
    Sheet("state_destinations", "State Destinations", (), (), dynamic=True),
    Sheet("recipient_locations", "Recipient Locations", (), (), dynamic=True),
    Sheet("accounting_basis", "Accounting Basis", ACCOUNTING_BASIS, ("basis",)),
)

SMALL_WORDS = frozenset(("a", "an", "and", "as", "at", "by", "for", "in", "of", "on",
                         "or", "the", "to", "vs"))


# --------------------------------------------------------------------------
# Naming and typing helpers
# --------------------------------------------------------------------------
def snake(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def title_header(column):
    """Header for a store column the registry does not name (contract 2.11-2.12)."""
    if column.startswith("has_"):
        return f"Has {title_header(column[4:])} Activity"
    words = []
    for index, word in enumerate(column.split("_")):
        if word in ("id", "zip", "url", "fec", "sha256"):
            words.append("SHA-256" if word == "sha256" else word.upper())
        elif index and word in SMALL_WORDS:
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


def infer_kind(column, sql_type):
    if column.startswith(("has_", "is_")):
        return "B"
    if re.search(r"(^|_)(id|key|zip5|zipcode|district|line_number)($|_)", column):
        return "ID"
    if re.search(r"(^|_)date$|_date_|^date$", column):
        return "D"
    upper = (sql_type or "").upper()
    if any(token in upper for token in ("REAL", "FLOA", "DOUB", "NUM")):
        return "R"
    if "INT" in upper:
        return "I"
    return "T"


def convert(value, kind):
    if value is None:
        return None
    if kind == "B":
        text = str(value).strip().lower()
        if text in TRUE_WORDS:
            return True
        if text in FALSE_WORDS:
            return False
        return None if not text else bool(value)
    if kind == "R":
        return float(value)
    if kind == "I":
        return int(value)
    if kind == "D":
        text = str(value).strip()[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    text = str(value)
    return text if text.strip() else None


def number_format(kind, header):
    if kind == "R":
        return MONEY_FORMAT
    if kind == "I":
        # A year is not a quantity: '#,##0' would publish 2,022. Every other
        # integer column keeps the contract's thousands format.
        return YEAR_FORMAT if header.endswith(("Year", "Cycle")) else INTEGER_FORMAT
    if kind == "D":
        return DATE_FORMAT
    return None


def width(kind, header):
    if kind == "D":
        return 12
    if kind in ("R", "I"):
        return max(12, min(20, len(header) + 4))
    return max(12, min(46, len(header) + 6))


# --------------------------------------------------------------------------
# Staging store access
# --------------------------------------------------------------------------
def read_only(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def store_tables(connection):
    return {row[0] for row in
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def table_columns(connection, table):
    return [(row[1], row[2]) for row in connection.execute(f'PRAGMA table_info("{table}")')]


def resolve_columns(connection, sheet):
    """Registry columns first, then any extra store columns when dynamic."""
    present = table_columns(connection, sheet.table)
    types = dict(present)
    columns = list(sheet.columns)
    known = {c for c, _, _ in columns}
    if sheet.dynamic:
        for name, _ in present:
            if name not in known:
                columns.append((name, title_header(name), infer_kind(name, types[name])))
    return columns, set(types)


def select(sheet, columns, available, where=None):
    fields = ", ".join(f'"{c}"' if c in available else f'NULL AS "{c}"'
                       for c, _, _ in columns)
    usable = [k for k in sheet.key if k in available]
    order = (" ORDER BY " + ", ".join(f'"{k}"' for k in usable)) if usable else ""
    clause = f" WHERE {where}" if where else ""
    return f'SELECT {fields} FROM "{sheet.table}"{clause}{order}'


# --------------------------------------------------------------------------
# Workbook writing (write-only mode: tableColumns must be populated by hand)
# --------------------------------------------------------------------------
def open_sheet(book, name, columns):
    if len(name) > MAX_SHEET_NAME:
        raise ValueError(f"Sheet name '{name}' exceeds {MAX_SHEET_NAME} characters.")
    problems = sheet_name_problems(name)
    if problems:
        raise ValueError(f"Sheet name '{name}' is not a valid Excel sheet name: "
                         f"{'; '.join(problems)}.")
    worksheet = book.create_sheet(title=name)
    for index, (_, header, kind) in enumerate(columns, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width(kind, header)
    worksheet.freeze_panes = "A2"
    worksheet.append([h for _, h, _ in columns])
    return worksheet


def append_rows(worksheet, columns, rows):
    formats = [number_format(k, h) for _, h, k in columns]
    kinds = [k for _, _, k in columns]
    count = 0
    for row in rows:
        cells = []
        for index, kind in enumerate(kinds):
            value = convert(row[index], kind)
            if isinstance(value, str) and value.startswith(FORMULA_LEADERS):
                # Strings only: a real -5 is a float here and keeps its number cell.
                cell = WriteOnlyCell(worksheet, value=value)
                cell.data_type = "s"
                cells.append(cell)
            elif value is None or formats[index] is None:
                cells.append(value)
            else:
                cell = WriteOnlyCell(worksheet, value=value)
                cell.number_format = formats[index]
                cells.append(cell)
        worksheet.append(cells)
        count += 1
    return count


def finish_sheet(worksheet, columns, count, table_id):
    reference = f"A1:{get_column_letter(len(columns))}{max(count, 1) + 1}"
    table = Table(displayName=table_id, ref=reference, headerRowCount=1)
    table.tableColumns = [TableColumn(id=i, name=h)
                          for i, (_, h, _) in enumerate(columns, 1)]
    table.autoFilter = AutoFilter(ref=reference)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=False,
                                          showLastColumn=False, showRowStripes=True,
                                          showColumnStripes=False)
    with warnings.catch_warnings():
        # add_table warns unconditionally in write-only mode; tableColumns is set
        # explicitly above, which is exactly what the warning asks for.
        warnings.filterwarnings("ignore", message="In write-only mode you must add")
        worksheet.add_table(table)


def table_name(sheet_name, cycle_label=None):
    name = "tbl_" + snake(sheet_name)
    if cycle_label:
        name += "_" + str(cycle_label).split("-")[-1]
    return name


def sheet_name_problems(name):
    """Why Excel would refuse `name` as a worksheet name; [] when it would not."""
    text = str(name or "")
    problems = []
    if not text.strip():
        problems.append("blank")
    if len(text) > MAX_SHEET_NAME:
        problems.append(f"{len(text)} characters, over Excel's {MAX_SHEET_NAME}")
    forbidden = sorted(set(text) & EXCEL_FORBIDDEN_SHEET_CHARACTERS)
    if forbidden:
        problems.append("holds " + " ".join(forbidden))
    if text.startswith("'") or text.endswith("'"):
        problems.append("begins or ends with an apostrophe")
    if text.casefold() == "history":
        problems.append("'History' is reserved by Excel")
    return problems


def election_year(cycle_label):
    """The even year that ends an FEC two-year cycle ('2021-2022' -> '2022'); else None."""
    match = re.fullmatch(r"(?:(\d{4})-)?(\d{4})", str(cycle_label or "").strip())
    if (not match or int(match[2]) % 2
            or (match[1] is not None and int(match[1]) != int(match[2]) - 1)):
        return None
    return match[2]


def split_sheet_name(sheet, cycle_label):
    """The sheet one cycle's rows go to when `sheet` is split: `<split base> <year>`.

    Paul, S1 (2026-09-26): `Contributions 2022`, and `Network Candidates 2022`
    for Network Candidate Activity. A label that is not an FEC two-year cycle
    (the exporter's sprintf writes `NA-NA` for a receipt with no date) keeps its
    own text, as every part did before S1, so a store that published before
    still publishes. Books published before S1 keep `<Sheet> <cycle_label>`
    until rebuilt; `app/report_insights.split_sheet_base` reads both back.
    """
    if cycle_label is None:
        # `cycle_label = ?` never selects a NULL, so such a part would be planned
        # and then written empty; the old spelling failed later on that count.
        raise ValueError(f"{sheet.name}: rows with no cycle_label cannot be split "
                         "into per-cycle sheets.")
    label = str(cycle_label).strip()
    return f"{sheet.split_base} {election_year(label) or label}"


SHEET_OPEN = re.compile(rb"(<worksheet\b[^>]*>(?:\s*<sheetPr\b[^>]*(?:/>|>.*?</sheetPr>))?)",
                        re.S)


def pin_dimensions(path, sizes):
    """Write a real <dimension> into every sheet part. NOT cosmetic.

    openpyxl's write-only writer emits no <dimension>, and its read-only reader
    (`worksheet/_read_only.ReadOnlyWorksheet._get_size` ->
    `_reader.WorkSheetParser.parse_dimensions`) then iterparses until the END of
    <sheetData> - i.e. it XML-parses every cell of every sheet just to size the
    workbook. Measured on the Credit Union mock: 18.65 million elements and
    418 s inside a single `openpyxl.load_workbook(read_only=True)`, paid again
    on every `report_insights.worksheet_data` call the console makes. With the
    dimension present the parser returns on the first element.

    The archive is rewritten once, to `<path>.dim-<pid>`, then os.replace'd.
    """
    source = Path(path)
    temporary = source.with_name(source.name + f".dim-{os.getpid()}")
    with zipfile.ZipFile(source) as archive:
        parts = sheet_parts(archive)
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
            for item in archive.infolist():
                data = archive.read(item.filename)
                name = parts.get(item.filename)
                if name in sizes:
                    columns, rows = sizes[name]
                    element = (f'<dimension ref="A1:{get_column_letter(columns)}'
                               f'{rows}"/>').encode()
                    data, count = SHEET_OPEN.subn(
                        lambda match: match.group(1) + element, data, count=1)
                    if not count:
                        raise ValueError(f"{name}: no <worksheet> element to pin")
                target.writestr(item, data)
    os.replace(temporary, source)


def sheet_parts(archive):
    """part name -> sheet name, via workbook.xml and its relationships."""
    package = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    office = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    relations = {node.get("Id"): node.get("Target") for node
                 in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
                 if node.tag == f"{package}Relationship"}
    result = {}
    for node in ET.fromstring(archive.read("xl/workbook.xml")).iter(f"{main}sheet"):
        target = relations.get(node.get(f"{office}id"), "")
        result["xl/" + target.lstrip("/").removeprefix("xl/")] = node.get("name")
    return result


# --------------------------------------------------------------------------
# Checks W1-W11
# --------------------------------------------------------------------------
class Checks:
    def __init__(self):
        self.rows = []

    def add(self, check_id, passed, detail, metric="", severity="fail", source="writer"):
        self.rows.append({"check_id": check_id, "source": source, "severity": severity,
                          "passed": 1 if passed else 0, "detail": detail,
                          "metric": "" if metric is None else str(metric)})

    def skip(self, check_id, reason):
        self.add(check_id, True, f"SKIPPED: {reason}", "", severity="info")

    def failed(self):
        return [r for r in self.rows if r["severity"] == "fail" and not r["passed"]]


def money(value):
    return f"{round(float(value or 0.0), 2):,.2f}"


def close_enough(left, right):
    return round(float(left or 0.0), 2) == round(float(right or 0.0), 2)


def cycle_sums(connection, table, column, present, clause=""):
    if table not in present:
        return None
    return {row[0]: row[1] for row in connection.execute(
        f'SELECT cycle_label, ROUND(SUM(COALESCE("{column}",0)),2) '
        f'FROM "{table}" {clause} GROUP BY cycle_label')}


def compare_cycles(checks, check_id, left, right, left_name, right_name):
    if left is None or right is None:
        missing = left_name if left is None else right_name
        checks.skip(check_id, f"{missing} table absent from the staging store")
        return
    for label in sorted(set(left) | set(right)):
        a, b = left.get(label, 0.0), right.get(label, 0.0)
        checks.add(check_id, close_enough(a, b),
                   f"{label}: {left_name} {money(a)} vs {right_name} {money(b)}", money(a))


def fact_checks(connection, checks, present):
    """W1-W4: every aggregate sheet re-adds to the receipt rows it came from."""
    included = "WHERE include_in_analysis = 1"
    base = cycle_sums(connection, "contributions", "analysis_amount", present, included)
    compare_cycles(checks, "giving_facts_vs_contributions",
                   cycle_sums(connection, "giving_facts", "giving", present), base,
                   "Giving Facts", "Contributions analysis")
    compare_cycles(checks, "recipient_facts_vs_contributions",
                   cycle_sums(connection, "recipient_facts", "giving", present), base,
                   "Recipient Facts", "Contributions analysis")
    person = cycle_sums(connection, "contributions", "analysis_amount", present,
                        included + " AND person_key IS NOT NULL AND person_key <> ''")
    compare_cycles(checks, "contributor_facts_vs_contributions",
                   cycle_sums(connection, "contributor_facts", "giving", present), person,
                   "Contributor Facts", "Contributions analysis (person rows)")
    compare_cycles(checks, "location_facts_vs_contributions",
                   cycle_sums(connection, "location_facts", "giving", present), base,
                   "Location Facts", "Contributions analysis")
    return base


def expected_totals(checks, base, metadata, payload_path):
    """W5: the payload's own expectation, computed before any sheet existed."""
    expected = decode_json(metadata.get("expected_cycle_totals")) or {}
    if not expected and payload_path:
        expected = (payload_metadata(payload_path) or {}).get("expected_cycle_totals") or {}
    if not expected:
        checks.skip("expected_cycle_totals",
                    "no expected_cycle_totals in staging metadata or --payload")
    elif base is None:
        checks.skip("expected_cycle_totals", "contributions table absent")
    else:
        for label in sorted(set(expected) | set(base)):
            a, b = base.get(label, 0.0), expected.get(label, 0.0)
            checks.add("expected_cycle_totals", close_enough(a, b),
                       f"{label}: Contributions analysis {money(a)} vs expected {money(b)}",
                       money(b))


def overview_totals(checks, base, receipt_path):
    """W6: the Overview's Excel-computed cycle totals from its finalizer receipt."""
    if not receipt_path:
        checks.skip("overview_cycle_totals", "--overview-receipt not supplied")
        return
    path = Path(receipt_path)
    if not path.is_file():
        checks.skip("overview_cycle_totals", f"receipt file not found at {path}")
        return
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    totals = (document.get("formula_receipt") or document).get("cycle_totals")
    if not totals:
        checks.skip("overview_cycle_totals", f"no cycle_totals inside {path.name}")
        return
    if base is None:
        checks.skip("overview_cycle_totals", "contributions table absent")
        return
    for label in sorted(set(totals) | set(base)):
        a, b = base.get(label, 0.0), totals.get(label, 0.0)
        checks.add("overview_cycle_totals", close_enough(a, b),
                   f"{label}: Contributions analysis {money(a)} vs Overview {money(b)}",
                   money(b))


def source_facts(database, industry, cycles, all_observations=False):
    """Raw reported amount/count, honoring the staged artifact's policy.

    New stores retain every attribution status and coalesce the same source ID
    with report.R's accepted-first/person-query priority. Legacy stores retain
    their historical accepted-only scope; they are not silently reinterpreted.
    """
    totals = {}
    with contextlib.closing(read_only(database)) as connection:
        names = store_tables(connection)
        display = {}
        if "company_display_names" in names:
            display = dict(connection.execute(
                "SELECT canonical_name, display_name FROM company_display_names"))
        seen = set()
        queries = []
        for priority,table in enumerate(("contributions", "corporate_giving")):
            if table not in names:
                continue
            queries.append(f"SELECT company, contribution_receipt_date, contribution_receipt_amount, sub_id, "
                f"CASE WHEN company_match_status='accepted' THEN {priority} ELSE {priority+2} END AS priority "
                f"FROM {table} WHERE industry = ?" + ("" if all_observations else " AND company_match_status = 'accepted'"))
        if not queries:
            return {}
        rows = connection.execute(' UNION ALL '.join(queries)+' ORDER BY priority, sub_id', (industry,)*len(queries))
        for company, day, amount, source_id, _ in rows:
            if all_observations and source_id in seen:
                continue
            seen.add(source_id)
            match = re.match(r"^(\d{4})-\d\d-\d\d", str(day or ""))
            if not match:
                continue
            year = int(match[1])
            cycle = year + year % 2
            if cycle not in cycles:
                continue
            key = (f"{cycle - 1}-{cycle}", display.get(company, company))
            previous = totals.get(key, (0.0, 0))
            value = float(amount)
            if not math.isfinite(value):
                raise ValueError('Invalid source amount; cannot reconcile a zero substitute')
            totals[key] = (previous[0] + value, previous[1] + 1)
    return {k: (round(v[0], 2), v[1]) for k, v in totals.items()}


def database_facts(checks, connection, present, metadata, database, expected_sha):
    """W7: accepted Amount and Source ID counts recomputed from the source DB."""
    if not database:
        checks.skip("database_amount_and_rows", "--database not supplied")
        return
    path = Path(database)
    if not path.is_file():
        checks.skip("database_amount_and_rows", f"source database missing at {path}")
        return
    if expected_sha:
        actual = file_sha256(path)
        if actual.lower() != expected_sha.lower():
            checks.add("database_amount_and_rows", False,
                       f"--database sha256 {actual} does not match the pinned "
                       f"{expected_sha}", actual)
            return
    industry = metadata.get("industry")
    cycles = set(decode_json(metadata.get("supported_cycles")) or [])
    if not industry or not cycles:
        checks.skip("database_amount_and_rows",
                    "staging metadata lacks industry or supported_cycles")
        return
    if "contributions" not in present:
        checks.skip("database_amount_and_rows", "contributions table absent")
        return
    try:
        actual = source_facts(path, industry, cycles, metadata.get('attribution_policy') == 'contradictions-only-v1')
    except sqlite3.Error as error:
        checks.skip("database_amount_and_rows", f"source database unreadable: {error}")
        return
    expected = {(r[0], r[1]): (r[2], r[3]) for r in connection.execute(
        "SELECT cycle_label, company, ROUND(SUM(amount),2), COUNT(source_id) "
        "FROM contributions GROUP BY cycle_label, company")}
    for key in sorted(set(expected) | set(actual)):
        book_amount, book_rows = expected.get(key, (0.0, 0))
        db_amount, db_rows = actual.get(key, (0.0, 0))
        checks.add("database_amount_and_rows",
                   close_enough(book_amount, db_amount) and book_rows == db_rows,
                   f"{key[0]} {key[1]}: workbook {money(book_amount)} / {book_rows:,} rows"
                   f" vs source DB {money(db_amount)} / {db_rows:,} rows", money(db_amount))


def structural_checks(connection, checks, emits, about):
    """W8 unique keys, W9 no packed multi-values, W10 headers, W11 lineage literal."""
    seen = set()
    for emit in emits:
        if emit.base in seen:
            continue
        seen.add(emit.base)
        sheet, available = emit.sheet, emit.available
        usable = [k for k in sheet.key if k in available]
        if usable:
            keys = ", ".join(f'"{k}"' for k in usable)
            total = connection.execute(
                f'SELECT COUNT(*) FROM "{sheet.table}"').fetchone()[0]
            distinct = connection.execute(
                f'SELECT COUNT(*) FROM (SELECT 1 FROM "{sheet.table}" GROUP BY {keys})'
            ).fetchone()[0]
            checks.add("unique_keys", total == distinct,
                       f"{sheet.name}: {total:,} rows, {distinct:,} distinct on "
                       f"({', '.join(usable)})", distinct)
        offenders = []
        for column, header, kind in emit.columns:
            if kind not in ("T", "ID") or column not in available:
                continue
            # Include-list, not exempt-list: only categorical dimensions we derive
            # can carry a packed "a; b" value. See MULTIVALUE_CHECKED_HEADERS.
            if header not in MULTIVALUE_CHECKED_HEADERS:
                continue
            hits = connection.execute(
                f'SELECT COUNT(*) FROM "{sheet.table}" WHERE "{column}" LIKE ?',
                ("%;%",)).fetchone()[0]
            if hits:
                offenders.append(f"{header} ({hits:,})")
        checks.add("no_multivalue_cells", not offenders,
                   f"{sheet.name}: " + (", ".join(offenders) if offenders
                                        else "no ';' in packed-value columns"),
                   len(offenders))
    for emit in emits:
        headers = [h for _, h, _ in emit.columns]
        checks.add("header_vocabulary", len(headers) == len(set(headers)),
                   f"{emit.name}: {len(headers)} headers on row 1, "
                   f"{len(set(headers))} distinct", len(headers))
    lineage = [v for k, v in about if k == "Lineage"]
    literal = bool(lineage) and bool(
        re.fullmatch(r"Source DB SHA-256: [a-f0-9]{64}", lineage[0]))
    checks.add("lineage_literal", literal,
               f"About Lineage = {lineage[0] if lineage else '(missing)'}",
               lineage[0].rsplit(" ", 1)[-1] if literal else "")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def decode_json(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(value)
    return None


def payload_metadata(path):
    """The payload's first object. Never json.load a multi-hundred-MB payload."""
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            head = stream.read(2_000_000)
    except OSError:
        return None
    match = re.search(r'"metadata"\s*:\s*', head)
    if not match:
        return None
    with contextlib.suppress(json.JSONDecodeError):
        return json.JSONDecoder().raw_decode(head[match.end():])[0]
    return None


class Emit:
    """One physical worksheet: a sheet spec, its columns, and its row filter."""

    __slots__ = ("name", "sheet", "columns", "available", "where", "parameters",
                 "rows", "base", "table_id")

    def __init__(self, name, sheet, columns, available, where, parameters, rows,
                 table_id):
        self.name, self.sheet, self.columns = name, sheet, columns
        self.available, self.where, self.parameters = available, where, parameters
        self.rows, self.base, self.table_id = rows, sheet.name, table_id


def plan_sheets(connection, present, split_rows):
    emits, absent = [], []
    for sheet in SHEETS:
        if sheet.table not in present:
            absent.append(sheet.table)
            continue
        total = connection.execute(f'SELECT COUNT(*) FROM "{sheet.table}"').fetchone()[0]
        if not total:
            absent.append(f"{sheet.table} (empty)")
            continue
        columns, available = resolve_columns(connection, sheet)
        if total > split_rows and "cycle_label" in available:
            for label, count in connection.execute(
                    f'SELECT cycle_label, COUNT(*) FROM "{sheet.table}" '
                    f'GROUP BY cycle_label ORDER BY 1'):
                emits.append(Emit(split_sheet_name(sheet, label), sheet, columns, available,
                                  "cycle_label = ?", (label,), count,
                                  table_name(sheet.name, label)))
        else:
            emits.append(Emit(sheet.name, sheet, columns, available, None, (), total,
                              table_name(sheet.name)))
    # Every name is checked here, before a row is written: a name Excel would
    # refuse, or two sheets that would share one, stops the build at once
    # instead of after the writing it would waste.
    used = set()
    for emit in emits:
        problems = sheet_name_problems(emit.name)
        if emit.name.casefold() in used:
            problems.append("already names another sheet")
        used.add(emit.name.casefold())
        if problems:
            raise ValueError(f"Sheet name '{emit.name}' is not a valid Excel sheet name: "
                             f"{'; '.join(problems)}.")
    return emits, absent


def about_rows(arguments, metadata, industry, cycle_label, database_sha, emits, blanks,
               absent, quality_rows, started):
    cycles = metadata.get("cycle_labels") or metadata.get("supported_cycles") or "[]"
    rows = [
        ("Industry", industry),
        ("Schema Version", metadata.get("schema_version") or SCHEMA_VERSION),
        ("Generated At", metadata.get("generated_at")
         or started.isoformat(timespec="seconds")),
        ("Cycle Label", cycle_label or "All cycles"),
        ("Cycles", cycles),
        ("Lineage", f"Source DB SHA-256: {database_sha}" if database_sha
         else "Source DB SHA-256 unavailable"),
        ("Source DB SHA-256", database_sha or ""),
        ("Source DB Path", metadata.get("database_path") or ""),
        ("Overview Workbook SHA-256", hash_if_file(arguments.overview)),
        ("Payload SHA-256", hash_if_file(arguments.payload)),
        ("Exporter Version", metadata.get("exporter_version") or ""),
        ("Writer Version", WRITER_VERSION),
        ("Accounting Note", metadata.get("accounting_note") or ""),
        ("Downstream accounting", "Network Candidate Activity counts each reachable spending committee once per cycle/candidate, without a top-100 cutoff. Beneficiary Bridge is company association: shared PAC activity repeats across companies. Do not sum company associations or add either table to receipts, transfers, or the other activity table."),
        ("Split Rows Threshold", arguments.split_rows),
    ]
    # About's own row count is the only self-referential one: 14 fixed rows, one
    # per emitted sheet plus About and Data Quality, then the two JSON rows.
    own = len(rows) + len(emits) + 2 + 2
    rows.append(("Rows: About", own))
    rows += [(f"Rows: {emit.name}", emit.rows) for emit in emits]
    rows.append(("Rows: Data Quality", quality_rows))
    rows.append(("Blank columns", json.dumps(blanks)))
    rows.append(("Absent tables", json.dumps(absent)))
    return [(k, str(v)) for k, v in rows]


def hash_if_file(path):
    return file_sha256(path) if path and Path(path).is_file() else ""


def blank_columns(connection, emits):
    """Headers with no value at all for this industry: absent, or present and NULL."""
    blanks, seen = [], set()
    for emit in emits:
        if emit.base in seen:
            continue
        seen.add(emit.base)
        for column, header, _ in emit.columns:
            if column not in emit.available:
                blanks.append(f"{emit.base} / {header}")
                continue
            filled = connection.execute(
                f'SELECT COUNT("{column}") FROM "{emit.sheet.table}"').fetchone()[0]
            if not filled:
                blanks.append(f"{emit.base} / {header}")
    return blanks


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def parse_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staging", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--database")
    parser.add_argument("--database-sha256")
    parser.add_argument("--payload")
    parser.add_argument("--overview")
    parser.add_argument("--overview-receipt")
    parser.add_argument("--split-rows", type=int,
                        default=int(os.environ.get("MENTAT_DATA_SPLIT_ROWS",
                                                   DEFAULT_SPLIT_ROWS)))
    parser.add_argument("--industry")
    parser.add_argument("--cycle-label")
    return parser.parse_args(argv)


def build(arguments):
    started = datetime.now(timezone.utc)
    out = Path(arguments.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pending = out.with_name(out.name + f".pending-{os.getpid()}")
    connection = read_only(arguments.staging)
    try:
        present = store_tables(connection)
        metadata = (dict(connection.execute("SELECT key, value FROM metadata"))
                    if "metadata" in present else {})
        industry = arguments.industry or metadata.get("industry") or "Industry"
        cycle_label = arguments.cycle_label or metadata.get("cycle_label") or ""
        database_sha = arguments.database_sha256 or metadata.get("database_sha256") or ""

        emits, absent = plan_sheets(connection, present, arguments.split_rows)
        checks = Checks()
        base = fact_checks(connection, checks, present)
        expected_totals(checks, base, metadata, arguments.payload)
        overview_totals(checks, base, arguments.overview_receipt)
        database_facts(checks, connection, present, metadata, arguments.database,
                       database_sha)

        store_quality = []
        if "data_quality" in present:
            columns = {name for name, _ in table_columns(connection, "data_quality")}
            fields = ", ".join(f'"{c}"' if c in columns else f'NULL AS "{c}"'
                               for c, _, _ in DATA_QUALITY)
            store_quality = [list(r) for r in connection.execute(
                f"SELECT {fields} FROM data_quality ORDER BY source, check_id")]

        blanks = blank_columns(connection, emits)
        about = about_rows(arguments, metadata, industry, cycle_label, database_sha,
                           emits, blanks, absent,
                           len(store_quality) + len(checks.rows) + 11, started)
        structural_checks(connection, checks, emits, about)
        quality = store_quality + [[r[c] for c, _, _ in DATA_QUALITY]
                                   for r in checks.rows]
        about = about_rows(arguments, metadata, industry, cycle_label, database_sha,
                           emits, blanks, absent, len(quality), started)

        book = openpyxl.Workbook(write_only=True)
        about_sheet = open_sheet(book, "About", ABOUT)
        counts, oversize = {}, []
        for emit in emits:
            worksheet = open_sheet(book, emit.name, emit.columns)
            rows = connection.execute(
                select(emit.sheet, emit.columns, emit.available, emit.where),
                emit.parameters)
            written = append_rows(worksheet, emit.columns, rows)
            finish_sheet(worksheet, emit.columns, written, emit.table_id)
            counts[emit.name] = written
            if written != emit.rows:
                raise ValueError(f"{emit.name}: planned {emit.rows} rows, wrote {written}")
            if written > DEFAULT_SPLIT_ROWS:
                oversize.append(emit.name)
        quality_sheet = open_sheet(book, "Data Quality", DATA_QUALITY)
        counts["Data Quality"] = append_rows(quality_sheet, DATA_QUALITY, quality)
        finish_sheet(quality_sheet, DATA_QUALITY, counts["Data Quality"],
                     "tbl_data_quality")
        counts["About"] = append_rows(about_sheet, ABOUT, about)
        finish_sheet(about_sheet, ABOUT, counts["About"], "tbl_about")
        book.save(pending)
        sizes = {"About": (len(ABOUT), counts["About"] + 1),
                 "Data Quality": (len(DATA_QUALITY), counts["Data Quality"] + 1)}
        sizes.update({e.name: (len(e.columns), counts[e.name] + 1) for e in emits})
        pin_dimensions(pending, sizes)
    except BaseException:
        pending.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    # Stored source guards are part of publication, not only worksheet prose.
    # Only numeric 1 establishes a critical pass; NULL/other states remain unknown.
    source_checks = [dict(zip((c for c, _, _ in DATA_QUALITY), row)) for row in store_quality]
    failures = [row for row in source_checks
                if row["severity"] == "fail" and row["passed"] != 1] + checks.failed()
    ordered = {"About": counts["About"]}
    ordered.update({e.name: counts[e.name] for e in emits})
    ordered["Data Quality"] = counts["Data Quality"]
    receipt = {"expected_worksheet_count": len(ordered),
               "sha256": None if failures else file_sha256(pending),
               "rows_per_sheet": ordered, "checks": source_checks + checks.rows,
               "split_sheets": {e.base: [x.name for x in emits if x.base == e.base]
                                for e in emits if e.name != e.base},
               "schema_version": SCHEMA_VERSION, "writer_version": WRITER_VERSION,
               "out": str(out), "absent_tables": absent,
               "blank_columns": blanks, "oversize_sheets": oversize,
               "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 2),
               "ok": not failures}
    Path(str(out) + ".receipt.json").write_text(json.dumps(receipt, indent=1),
                                                encoding="utf-8")
    if failures:
        pending.unlink(missing_ok=True)
        for row in failures:
            print(f"FAIL {row['check_id']}: {row['detail']}", file=sys.stderr)
        print(f"{len(failures)} severity-fail Data Quality row(s) failed; "
              f"nothing published.", file=sys.stderr)
        return 2
    os.replace(pending, out)
    print(f"Wrote {out} ({len(ordered)} sheets, {sum(ordered.values()):,} rows, "
          f"{receipt['seconds']}s)")
    return 0


def main(argv=None):
    return build(parse_arguments(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
