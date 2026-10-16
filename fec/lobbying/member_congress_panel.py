# -*- coding: utf-8 -*-
"""Build the fixed-effects-ready member-by-Congress lobbying panel."""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

import argparse
import collections
import csv
import glob
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

from paths import ROOT as MB_ROOT

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SCHEMA = "member-congress-panel-v1"
CONGRESSES = (117, 118, 119)
YEARS = tuple(range(2021, 2027))
STRICT = frozenset({"formal_strict", "both_strict", "title_strict"})
FINANCE_COMMITTEES = frozenset({"HSBA", "SSBK", "HSAG", "SSAF"})
SECTORS = (
    "bank",
    "credit_union",
    "crypto",
    "fintech",
    "telecom",
    "financial_other",
)
BILL_TYPE_LABELS = {
    "hr": "H.R.",
    "s": "S.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hres": "H.Res.",
    "sres": "S.Res.",
    "hamdt": "H.Amdt.",
    "samdt": "S.Amdt.",
}
PANEL_FIELDS = (
    "bioguide",
    "congress",
    "chamber",
    "party_code",
    "first_service_year",
    "state",
    "portfolio_bills",
    "att_bank",
    "att_credit_union",
    "att_crypto",
    "att_fintech",
    "att_telecom",
    "att_financial_other",
    "att_cu_league",
    "pro_cu_sponsored",
    "pro_cu_cosponsored",
    "anti_cu_sponsored",
    "anti_cu_cosponsored",
    "net_directional",
    "finance_committee",
    "cu_branch_share_v119",
    "owner_rate_v119",
    "employment_rate_v119",
)
COMMITTEE_COVERAGE_NOTE = (
    "Congress 117 has no committee snapshot by design; finance_committee is "
    "empty, never imputed to 0, for every 117 row."
)


class MemberCongressPanelError(RuntimeError):
    """An input contract failed; do not publish a partial panel."""


def _derived_dir():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise MemberCongressPanelError("LOCALAPPDATA is not set")
    return os.path.join(local, "Melange", "lda_rebuild", "derived")


def _raw_dir(derived=None):
    return os.path.join(os.path.dirname(derived or _derived_dir()), "raw")


def _iter_csv(path, label, required_fields):
    if not os.path.isfile(path):
        raise MemberCongressPanelError(f"{label} not found: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            missing = sorted(set(required_fields) - fields)
            if missing:
                raise MemberCongressPanelError(
                    f"{label} lacks required columns: {', '.join(missing)}"
                )
            for row_number, row in enumerate(reader, start=2):
                yield row_number, row
    except MemberCongressPanelError:
        raise
    except (OSError, csv.Error) as exc:
        raise MemberCongressPanelError(f"{label} unreadable: {exc}") from exc


def _integer(value, label, minimum=None):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise MemberCongressPanelError(f"{label} must be an integer") from exc
    if minimum is not None and number < minimum:
        raise MemberCongressPanelError(f"{label} must be at least {minimum}")
    return number


def _integerish(value, label, minimum=None):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise MemberCongressPanelError(f"{label} must be an integer") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise MemberCongressPanelError(f"{label} must be an integer")
    result = int(number)
    if minimum is not None and result < minimum:
        raise MemberCongressPanelError(f"{label} must be at least {minimum}")
    return result


def _optional_number(value, label):
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MemberCongressPanelError(f"{label} must be numeric or null") from exc
    if not math.isfinite(number):
        raise MemberCongressPanelError(f"{label} must be finite")
    return number


def _bill_key_parts(value, label):
    parts = str(value or "").strip().split(":")
    if len(parts) != 3:
        raise MemberCongressPanelError(f"{label} is not a canonical bill key")
    congress = _integer(parts[0], f"{label} Congress", minimum=1)
    bill_type = parts[1]
    number = _integer(parts[2], f"{label} number", minimum=1)
    if bill_type not in BILL_TYPE_LABELS:
        raise MemberCongressPanelError(
            f"{label} has unsupported bill type {bill_type!r}"
        )
    canonical = f"{congress}:{bill_type}:{number}"
    if str(value or "").strip() != canonical:
        raise MemberCongressPanelError(f"{label} is not canonical: {value!r}")
    return congress, bill_type, number


def _bill_key_from_display(display, congress):
    """Mirror make_lobby_network_html.py's bill-directions overlay parser."""
    congress_text = str(congress or "").strip()
    if not congress_text:
        return None
    try:
        congress_number = _integer(
            congress_text, "bill metadata Congress", minimum=1
        )
    except MemberCongressPanelError:
        return None
    token = re.sub(r"[^a-z0-9]", "", str(display or "").casefold())
    for bill_type in sorted(BILL_TYPE_LABELS, key=len, reverse=True):
        if not token.startswith(bill_type):
            continue
        number_text = token[len(bill_type):]
        if number_text.isdigit() and int(number_text) > 0:
            return f"{congress_number}:{bill_type}:{int(number_text)}"
    return None


def _load_bill_index(derived):
    required = ("channel", "lob_id", "bill_key", "client_name", "org_sector")
    pairs = {}
    input_rows = 0
    strict_rows = 0
    for year in YEARS:
        path = os.path.join(derived, f"our_bill_lobby_index_{year}.csv")
        for row_number, row in _iter_csv(path, "bill-lobby index", required):
            input_rows += 1
            if (row.get("channel") or "").strip() not in STRICT:
                continue
            strict_rows += 1
            org = (row.get("lob_id") or "").strip()
            bill = (row.get("bill_key") or "").strip()
            if not org or not bill:
                raise MemberCongressPanelError(
                    f"bill-lobby index row {row_number} in {path} has blank org/bill"
                )
            _bill_key_parts(bill, f"bill-lobby index row {row_number} bill_key")
            sectors = {
                item.strip()
                for item in (row.get("org_sector") or "").split("|")
                if item.strip()
            }
            unknown = sorted(sectors - set(SECTORS))
            if unknown:
                raise MemberCongressPanelError(
                    f"bill-lobby index row {row_number} has unknown sectors: "
                    + ", ".join(unknown)
                )
            pair = pairs.setdefault(
                (org, bill), {"sectors": set(), "league": False}
            )
            pair["sectors"].update(sectors)
            if "LEAGUE" in (row.get("client_name") or "").upper():
                pair["league"] = True
    return pairs, {
        "input_rows": input_rows,
        "strict_rows": strict_rows,
        "distinct_org_bill_pairs": len(pairs),
    }


def _load_memberships(path):
    required = ("bill_key", "bioguide_id", "role", "sponsorship_date")
    edges = set()
    input_rows = 0
    for row_number, row in _iter_csv(path, "interaction bill members", required):
        input_rows += 1
        bill = (row.get("bill_key") or "").strip()
        member = (row.get("bioguide_id") or "").strip()
        role = (row.get("role") or "").strip().casefold()
        if not bill or not member:
            raise MemberCongressPanelError(
                f"interaction bill members row {row_number} has blank bill/member"
            )
        _bill_key_parts(bill, f"interaction bill members row {row_number} bill_key")
        if role not in {"sponsor", "cosponsor"}:
            raise MemberCongressPanelError(
                f"interaction bill members row {row_number} has invalid role {role!r}"
            )
        edges.add((bill, member, role))
    return edges, {"input_rows": input_rows, "distinct_edges": len(edges)}


def _load_directions(path):
    required = ("Bill", "Congress", "Category", "Direction", "Note")
    directions = {}
    rows_read = 0
    unjoinable = 0
    for row_number, row in _iter_csv(path, "bill directions", required):
        rows_read += 1
        key = _bill_key_from_display(row.get("Bill"), row.get("Congress"))
        if key is None:
            unjoinable += 1
            continue
        direction = _integer(
            row.get("Direction"),
            f"bill directions row {row_number} direction",
            minimum=-1,
        )
        if direction not in (-1, 0, 1):
            raise MemberCongressPanelError(
                f"bill directions row {row_number} direction must be -1, 0, or 1"
            )
        if key in directions:
            raise MemberCongressPanelError(
                f"bill directions repeats canonical bill {key}"
            )
        directions[key] = direction
    return directions, {
        "rows": rows_read,
        "joined": len(directions),
        "unjoinable": unjoinable,
        "directional": sum(value in (-1, 1) for value in directions.values()),
        "neutral": sum(value == 0 for value in directions.values()),
    }


def _canonical_chamber(value, label):
    normalized = str(value or "").strip().casefold()
    if normalized in {"house", "rep", "h"}:
        return "House"
    if normalized in {"senate", "sen", "s"}:
        return "Senate"
    raise MemberCongressPanelError(f"{label} must be House or Senate")


def _spine_choice(rows, key):
    unique = {}
    for row in rows:
        identity = (
            row["chamber"],
            row["party_code"],
            row["state"],
            row["icpsr"],
        )
        unique.setdefault(identity, row)
    working = list(unique.values())
    rule = None
    if len({row["chamber"] for row in working}) > 1:
        senate = [row for row in working if row["chamber"] == "Senate"]
        if not senate:
            raise MemberCongressPanelError(
                f"spine has an unknown chamber transition for {key!r}"
            )
        working = senate
        rule = "mid_congress_chamber_move_prefer_senate"
    states = {row["state"] for row in working}
    if len(states) != 1:
        raise MemberCongressPanelError(
            f"spine has conflicting states for member-Congress {key!r}"
        )
    if len(working) > 1:
        working.sort(
            key=lambda row: (
                row["icpsr"] if row["icpsr"] is not None else -1,
                row["party_code"],
            )
        )
        working = [working[-1]]
        rule = rule or "mid_congress_party_switch_higher_numeric_identity"
    if len(working) != 1:
        raise MemberCongressPanelError(
            f"spine failed to resolve member-Congress {key!r}"
        )
    return working[0], rule


def _load_spine(path):
    required = (
        "bioguide_id",
        "congress",
        "chamber",
        "party_code",
        "state_abbrev",
    )
    earliest = {}
    grouped = collections.defaultdict(list)
    target_input_rows = 0
    target_nonlegislative_rows_excluded = 0
    all_rows_with_bioguide = 0
    for row_number, row in _iter_csv(path, "member-Congress spine", required):
        member = (row.get("bioguide_id") or "").strip()
        if not member:
            continue
        all_rows_with_bioguide += 1
        congress = _integerish(
            row.get("congress"), f"spine row {row_number} Congress", minimum=1
        )
        earliest[member] = min(congress, earliest.get(member, congress))
        if congress not in CONGRESSES:
            continue
        chamber_text = str(row.get("chamber") or "").strip().casefold()
        if chamber_text == "president":
            target_nonlegislative_rows_excluded += 1
            continue
        target_input_rows += 1
        state = (row.get("state_abbrev") or "").strip().upper()
        if not state:
            raise MemberCongressPanelError(
                f"spine row {row_number} has blank state_abbrev"
            )
        party_code = _integerish(
            row.get("party_code"), f"spine row {row_number} party_code"
        )
        icpsr_text = (row.get("icpsr") or "").strip()
        icpsr = (
            _integerish(icpsr_text, f"spine row {row_number} icpsr")
            if icpsr_text
            else None
        )
        grouped[(member, congress)].append(
            {
                "bioguide": member,
                "congress": congress,
                "chamber": _canonical_chamber(
                    chamber_text, f"spine row {row_number} chamber"
                ),
                "party_code": party_code,
                "state": state,
                "icpsr": icpsr,
            }
        )
    resolved = {}
    resolutions = []
    duplicate_input_rows = 0
    for key, rows in grouped.items():
        selected, rule = _spine_choice(rows, key)
        resolved[key] = selected
        if len(rows) > 1:
            duplicate_input_rows += len(rows) - 1
            resolutions.append(
                {
                    "bioguide": key[0],
                    "congress": key[1],
                    "input_rows": len(rows),
                    "rule": rule or "exact_duplicate",
                    "selected_chamber": selected["chamber"],
                    "selected_party_code": selected["party_code"],
                }
            )
    rows_per_congress = {
        str(congress): sum(key[1] == congress for key in resolved)
        for congress in CONGRESSES
    }
    return resolved, earliest, {
        "all_rows_with_bioguide": all_rows_with_bioguide,
        "target_input_rows": target_input_rows,
        "target_nonlegislative_rows_excluded": (
            target_nonlegislative_rows_excluded
        ),
        "panel_rows": len(resolved),
        "rows_per_congress": rows_per_congress,
        "duplicate_input_rows_resolved": duplicate_input_rows,
        "duplicate_member_congresses": resolutions,
    }


def _load_committees(raw):
    snapshot_dir = os.path.join(raw, "committee_snapshots")
    by_congress = {}
    paths = {}
    for congress in (118, 119):
        matches = sorted(
            glob.glob(
                os.path.join(
                    snapshot_dir, f"committee-membership-{congress}-*.yaml"
                )
            )
        )
        if len(matches) != 1:
            raise MemberCongressPanelError(
                f"expected exactly one Congress {congress} committee snapshot; "
                f"found {len(matches)} in {snapshot_dir}"
            )
        path = matches[0]
        current = None
        members = set()
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.rstrip("\n")
                    if (
                        stripped
                        and not stripped.startswith((" ", "-", "\t"))
                        and stripped.endswith(":")
                    ):
                        current = stripped[:-1].strip()
                    elif "bioguide:" in stripped and current in FINANCE_COMMITTEES:
                        member = stripped.split("bioguide:", 1)[1].strip()
                        if member:
                            members.add(member)
        except OSError as exc:
            raise MemberCongressPanelError(
                f"committee snapshot unreadable: {path}: {exc}"
            ) from exc
        by_congress[congress] = members
        paths[str(congress)] = os.path.abspath(path)
    return by_congress, paths


def _load_json(path, label):
    if not os.path.isfile(path):
        raise MemberCongressPanelError(f"{label} not found: {path}")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise MemberCongressPanelError(f"{label} unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise MemberCongressPanelError(f"{label} root must be an object")
    districts = payload.get("districts")
    if not isinstance(districts, dict):
        raise MemberCongressPanelError(f"{label} districts must be an object")
    return districts


def _load_district_context(financial_path, community_path):
    financial_source = _load_json(financial_path, "district financial context")
    community_source = _load_json(community_path, "district community context")
    financial = {}
    for key, payload in financial_source.items():
        if not isinstance(payload, dict) or not isinstance(payload.get("ratio"), dict):
            raise MemberCongressPanelError(
                f"district financial context {key!r} lacks ratio object"
            )
        financial[key] = _optional_number(
            payload["ratio"].get("cu_branch_share"),
            f"district financial context {key} cu_branch_share",
        )
    community = {}
    for key, payload in community_source.items():
        if not isinstance(payload, dict) or not isinstance(payload.get("derived"), dict):
            raise MemberCongressPanelError(
                f"district community context {key!r} lacks derived object"
            )
        community[key] = {
            "owner_rate": _optional_number(
                payload["derived"].get("owner_occupied_share"),
                f"district community context {key} owner_occupied_share",
            ),
            "employment_rate": _optional_number(
                payload["derived"].get("employment_population_ratio"),
                f"district community context {key} employment_population_ratio",
            ),
        }
    return financial, community


def _load_district_crosswalk(path):
    required = ("bioguide_id", "type", "state", "district")
    crosswalk = {}
    for row_number, row in _iter_csv(path, "legislator district crosswalk", required):
        if (row.get("type") or "").strip().casefold() != "rep":
            continue
        member = (row.get("bioguide_id") or "").strip()
        state = (row.get("state") or "").strip().upper()
        if not member or not state:
            raise MemberCongressPanelError(
                f"legislator district crosswalk row {row_number} has blank member/state"
            )
        district = _integerish(
            row.get("district"),
            f"legislator district crosswalk row {row_number} district",
            minimum=0,
        )
        key = f"{state}-{district:02d}"
        if member in crosswalk and crosswalk[member] != key:
            raise MemberCongressPanelError(
                f"legislator district crosswalk repeats {member} inconsistently"
            )
        crosswalk[member] = key
    return crosswalk


def build_panel(
    derived=None,
    raw=None,
    bill_directions_path=None,
    spine_path=None,
    legislators_path=None,
    district_financial_path=None,
    district_community_path=None,
):
    """Load the contracted inputs and return deterministic rows plus receipt."""
    derived = os.path.abspath(derived or _derived_dir())
    raw = os.path.abspath(raw or _raw_dir(derived))
    bill_directions_path = os.path.abspath(
        bill_directions_path
        or os.path.join(MB_ROOT, "data", "legislation", "bill_directions.csv")
    )
    spine_path = os.path.abspath(
        spine_path or os.path.join(MB_ROOT, "votes", "HSall_members.csv")
    )
    legislators_path = os.path.abspath(
        legislators_path or os.path.join(MB_ROOT, "fec", "legislators.csv")
    )
    district_financial_path = os.path.abspath(
        district_financial_path
        or os.path.join(MB_ROOT, "data", "district", "district_financial.json")
    )
    district_community_path = os.path.abspath(
        district_community_path
        or os.path.join(MB_ROOT, "data", "district", "district_community.json")
    )

    org_bill_pairs, index_receipt = _load_bill_index(derived)
    membership_path = os.path.join(
        derived, "interactions", "interaction_bill_members.csv"
    )
    memberships, membership_receipt = _load_memberships(membership_path)
    directions, direction_receipt = _load_directions(bill_directions_path)
    spine, earliest, spine_receipt = _load_spine(spine_path)
    committees, committee_paths = _load_committees(raw)
    district_crosswalk = _load_district_crosswalk(legislators_path)
    financial, community = _load_district_context(
        district_financial_path, district_community_path
    )

    members_by_bill = collections.defaultdict(set)
    roles_by_member_bill = collections.defaultdict(set)
    orphan_edges = set()
    orphan_member_congresses = set()
    for bill, member, role in memberships:
        congress = _bill_key_parts(bill, "interaction bill membership bill_key")[0]
        members_by_bill[bill].add(member)
        roles_by_member_bill[(member, bill)].add(role)
        if congress in CONGRESSES and (member, congress) not in spine:
            orphan_edges.add((bill, member, role))
            orphan_member_congresses.add((member, congress))

    portfolio = collections.defaultdict(set)
    attention = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    league_attention = collections.defaultdict(set)
    for (org, bill), pair in org_bill_pairs.items():
        congress = _bill_key_parts(bill, "bill-lobby index bill_key")[0]
        if congress not in CONGRESSES:
            continue
        for member in members_by_bill.get(bill, ()):
            key = (member, congress)
            if key not in spine:
                continue
            portfolio[key].add(bill)
            org_bill = (org, bill)
            for sector in pair["sectors"]:
                attention[key][sector].add(org_bill)
            if "credit_union" in pair["sectors"] and pair["league"]:
                league_attention[key].add(org_bill)

    directional = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    for (member, bill), roles in roles_by_member_bill.items():
        congress = _bill_key_parts(bill, "directional membership bill_key")[0]
        key = (member, congress)
        if key not in spine:
            continue
        direction = directions.get(bill)
        if direction not in (-1, 1):
            continue
        prefix = "pro" if direction == 1 else "anti"
        for role in roles:
            role_field = "sponsored" if role == "sponsor" else "cosponsored"
            directional[key][f"{prefix}_cu_{role_field}"].add(bill)

    rows = []
    district_eligible = 0
    district_crosswalk_joined = 0
    district_context_complete = 0
    district_98_fallback = 0
    for key in sorted(spine, key=lambda item: (item[0], item[1])):
        member, congress = key
        service = spine[key]
        counts = directional.get(key, {})
        pro_sponsored = len(counts.get("pro_cu_sponsored", ()))
        pro_cosponsored = len(counts.get("pro_cu_cosponsored", ()))
        anti_sponsored = len(counts.get("anti_cu_sponsored", ()))
        anti_cosponsored = len(counts.get("anti_cu_cosponsored", ()))
        row = {
            "bioguide": member,
            "congress": congress,
            "chamber": service["chamber"],
            "party_code": service["party_code"],
            "first_service_year": 1789 + 2 * (earliest[member] - 1),
            "state": service["state"],
            "portfolio_bills": len(portfolio.get(key, ())),
            "att_bank": len(attention[key].get("bank", ())),
            "att_credit_union": len(attention[key].get("credit_union", ())),
            "att_crypto": len(attention[key].get("crypto", ())),
            "att_fintech": len(attention[key].get("fintech", ())),
            "att_telecom": len(attention[key].get("telecom", ())),
            "att_financial_other": len(
                attention[key].get("financial_other", ())
            ),
            "att_cu_league": len(league_attention.get(key, ())),
            "pro_cu_sponsored": pro_sponsored,
            "pro_cu_cosponsored": pro_cosponsored,
            "anti_cu_sponsored": anti_sponsored,
            "anti_cu_cosponsored": anti_cosponsored,
            "net_directional": (
                pro_sponsored
                + pro_cosponsored
                - anti_sponsored
                - anti_cosponsored
            ),
            "finance_committee": (
                "" if congress == 117 else int(member in committees[congress])
            ),
            "cu_branch_share_v119": "",
            "owner_rate_v119": "",
            "employment_rate_v119": "",
        }
        if congress == 119 and service["chamber"] == "House":
            district_eligible += 1
            district_key = district_crosswalk.get(member)
            if district_key is not None:
                district_crosswalk_joined += 1
                # Delegates (DC/PR) are district 0 in legislators.csv but
                # keyed -98 in the district context files.
                if district_key.endswith("-00") and not (
                    district_key in financial or district_key in community
                ):
                    alt_key = district_key[:-3] + "-98"
                    if alt_key in financial or alt_key in community:
                        district_key = alt_key
                        district_98_fallback += 1
                row["cu_branch_share_v119"] = financial.get(district_key, "")
                community_row = community.get(district_key, {})
                row["owner_rate_v119"] = community_row.get("owner_rate", "")
                row["employment_rate_v119"] = community_row.get(
                    "employment_rate", ""
                )
                if all(
                    row[field] != ""
                    for field in (
                        "cu_branch_share_v119",
                        "owner_rate_v119",
                        "employment_rate_v119",
                    )
                ):
                    district_context_complete += 1
        rows.append(row)

    rows_per_congress = {
        str(congress): sum(row["congress"] == congress for row in rows)
        for congress in CONGRESSES
    }
    membership_receipt.update(
        {
            "orphan_membership_edges": len(orphan_edges),
            "orphan_member_congress_pairs": len(orphan_member_congresses),
        }
    )
    receipt = {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "rows_per_congress": rows_per_congress,
        "spine_counts": spine_receipt,
        "bill_index": index_receipt,
        "memberships": membership_receipt,
        "directions": direction_receipt,
        "committee_coverage_note": COMMITTEE_COVERAGE_NOTE,
        "committee_members_per_congress": {
            str(congress): len(committees[congress]) for congress in (118, 119)
        },
        "district_context": {
            "vintage": 119,
            "eligible_119_house_rows": district_eligible,
            "legislator_crosswalk_joined": district_crosswalk_joined,
            "complete_context_rows": district_context_complete,
            "district_98_fallback_rows": district_98_fallback,
            "owner_rate_source_field": "derived.owner_occupied_share",
            "employment_rate_source_field": (
                "derived.employment_population_ratio"
            ),
        },
        "input_paths": {
            "bill_lobby_indexes": [
                os.path.abspath(
                    os.path.join(derived, f"our_bill_lobby_index_{year}.csv")
                )
                for year in YEARS
            ],
            "interaction_bill_members": os.path.abspath(membership_path),
            "bill_directions": bill_directions_path,
            "member_congress_spine": spine_path,
            "committee_snapshots": committee_paths,
            "legislators": legislators_path,
            "district_financial": district_financial_path,
            "district_community": district_community_path,
        },
    }
    return rows, receipt


def write_outputs(rows, receipt, out_dir):
    """Write panel.csv and receipt.json through same-directory temp files."""
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    panel_path = os.path.join(out_dir, "panel.csv")
    receipt_path = os.path.join(out_dir, "receipt.json")
    panel_temporary = f"{panel_path}.tmp.{os.getpid()}"
    receipt_temporary = f"{receipt_path}.tmp.{os.getpid()}"
    try:
        with open(
            panel_temporary, "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=PANEL_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
        with open(
            receipt_temporary, "w", encoding="utf-8", newline="\n"
        ) as handle:
            json.dump(
                receipt,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=1,
            )
            handle.write("\n")
        os.replace(panel_temporary, panel_path)
        os.replace(receipt_temporary, receipt_path)
    except BaseException:
        for temporary in (panel_temporary, receipt_temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
        raise
    return panel_path, receipt_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out-dir",
        default=os.path.join(_derived_dir(), "member_congress_panel"),
    )
    args = parser.parse_args(argv)
    rows, receipt = build_panel()
    panel_path, _receipt_path = write_outputs(rows, receipt, args.out_dir)
    counts = receipt["rows_per_congress"]
    print(
        "member-congress panel: %d rows (117: %d, 118: %d, 119: %d) -> %s"
        % (
            len(rows),
            counts["117"],
            counts["118"],
            counts["119"],
            panel_path,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
