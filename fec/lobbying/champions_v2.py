# -*- coding: utf-8 -*-
"""Build direction-aware, mutually exclusive lobbying champion boards.

The artifact is descriptive and display-only.  It replaces direction-blind
residual attention as the champions-family headline without becoming an
engine, rank, or receptivity input.
Tier-1 crowns are never eligible for any sector's threat board, and a member
on any retained threat board holds no champions seat (the champion seat
yields, Paul 2026-08-09).
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

import argparse
import collections
import csv
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

from paths import ROOT as MB_ROOT
import members.historical_member_names as identity
import members.member_identity as desk_identity

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SCHEMA = "champions-v2-v1"
SECTORS = ("credit_union", "bank", "crypto", "fintech", "telecom")
CHAMBERS = ("House", "Senate")
SIGN_SOURCES = ("canonical", "kim", "prior")
SIGN_AUTHORITY = {source: index for index, source in enumerate(SIGN_SOURCES)}
SUPPORT_THRESHOLD = 0.7
OPPOSITION_THRESHOLD = 0.3
CHAMPION_LIMIT_PER_CHAMBER = 25
THREAT_LIMIT = 25

CU_COMPONENTS = (
    "log1p_supportive_att",
    "net_directional",
    "O",
    "log1p_invested",
    "rapport",
)
OTHER_COMPONENTS = ("log1p_supportive_att", "rapport")

VETO_RULE = (
    "VETO: opposition_att > supportive_att -> never a champion. OPPOSITION "
    "LEADERSHIP: sponsoring >= 1 corroborated-opposed bill, or cosponsoring "
    ">= 2, forces the THREAT board regardless of supportive profile, except "
    "for the Tier-1 crown rule in CHAMPION EXCLUSIVITY "
    "(2026-08-06 routing fix - the CCCA author and its Senate lead "
    "cosponsor are opposition leadership, not mixed dockets)."
)
MIXED_RULE = (
    "MIXED: not vetoed AND not opposition leadership AND "
    "opposition_att >= 3 -> issue-specific board. Tier-1 crown demotions under "
    "CHAMPION EXCLUSIVITY also land on mixed when opposition_att >= 3."
)
CHAMPION_EXCLUSIVITY_RULE = (
    "CHAMPION EXCLUSIVITY (crown supremacy, Paul 2026-08-09): a Tier-1 crown "
    "(overall display Champion) never appears on any sector's threat board. "
    "A crown whose docket would force the threat board is demoted to the mixed "
    "board when opposition_att >= 3, and is otherwise omitted and receipted "
    "under crown_threat_exclusions. Uniform across every sector."
)
CROSS_SECTOR_RULE = (
    "CROSS-SECTOR EXCLUSIVITY (champion seat yields, Paul 2026-08-09): a "
    "member on any sector's RETAINED threat board holds no champions seat in "
    "any sector - the threat seat stands (the 2026-08-06 Durbin routing fix "
    "is ratified truth) and the champions seat yields, receipted under "
    "cross_sector_exclusions. Crowns resolve the other way first (crown "
    "supremacy): they never appear as threats, so their champions seats "
    "never yield. Uniform across every sector."
)
CORROBORATION_RULE = (
    "opp_sponsored/opp_cosponsored(m) = bills m sponsors/cosponsors whose "
    "sector-oppose signal is CORROBORATED (>= 2 oppose-signed orgs of that "
    "sector on the bill). Threat = z(log1p opposition_att) + opp_sponsored "
    "+ 0.5 * opp_cosponsored."
)
METHOD_NOTE = (
    "Signed attention is inferred (leads, not disclosures) except canonical "
    "rows, which carry Paul's marks; boards are DESCRIPTIVE decision aids - "
    "no engine, rank, or receptivity input reads them."
)

BILL_TYPE_ALIASES = {
    "HR": "hr",
    "HRES": "hres",
    "HJRES": "hjres",
    "HCONRES": "hconres",
    "S": "s",
    "SRES": "sres",
    "SJRES": "sjres",
    "SCONRES": "sconres",
}
BILL_KEY_RE = re.compile(r"^\d{3}:[a-z]+:\d+$")


class ChampionsV2Error(RuntimeError):
    """An input or structural contract failed; publish no partial artifact."""


def _derived_dir():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise ChampionsV2Error("LOCALAPPDATA is not set")
    return os.path.join(local, "Melange", "lda_rebuild", "derived")


def _default_paths():
    derived = _derived_dir()
    return {
        "signed_attention_path": os.path.join(
            derived, "stance_priors", "signed_attention.csv"
        ),
        "interactions_path": os.path.join(
            derived, "interactions", "interaction_bill_members.csv"
        ),
        "panel_path": os.path.join(
            derived, "member_congress_panel", "panel.csv"
        ),
        "pillar_path": os.path.join(
            MB_ROOT, "data", "engine", "pillar_inputs_snapshot.json"
        ),
        "member_bundle_path": os.path.join(MB_ROOT, "member_desk_bundle.json"),
        "titles_path": os.path.join(derived, "bill_titles", "titles.json"),
        "bill_directions_path": os.path.join(
            MB_ROOT, "data", "legislation", "bill_directions.csv"
        ),
    }


def _iter_csv(path, label, required_fields):
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ChampionsV2Error(f"cannot read {label} {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        missing = sorted(set(required_fields) - fields)
        if missing:
            raise ChampionsV2Error(
                f"{label} lacks required columns: {', '.join(missing)}"
            )
        try:
            for row_number, row in enumerate(reader, start=2):
                yield row_number, row
        except csv.Error as exc:
            raise ChampionsV2Error(f"cannot parse {label} {path}: {exc}") from exc


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise ChampionsV2Error(f"cannot read {label} {path}: {exc}") from exc


def _number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool):
        raise ChampionsV2Error(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ChampionsV2Error(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ChampionsV2Error(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise ChampionsV2Error(f"{label} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ChampionsV2Error(f"{label} must be at most {maximum}")
    return number


def _integer(value, label):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ChampionsV2Error(f"{label} must be an integer") from exc
    return number


def _bill_key(value, label):
    key = (value or "").strip().lower()
    if not BILL_KEY_RE.match(key):
        raise ChampionsV2Error(f"{label} has invalid bill_key {value!r}")
    return key


def _load_signed_attention(path):
    required = (
        "lob_id", "client_name", "bill_key", "org_sector",
        "sign_source", "p_support", "label",
    )
    rows = []
    seen = set()
    sign_coverage = {
        sector: {source: 0 for source in SIGN_SOURCES} for sector in SECTORS
    }
    for row_number, row in _iter_csv(path, "signed attention", required):
        lob_id = (row.get("lob_id") or "").strip()
        if not lob_id:
            raise ChampionsV2Error(
                f"signed attention row {row_number} has blank lob_id"
            )
        bill = _bill_key(
            row.get("bill_key"), f"signed attention row {row_number}"
        )
        sectors = tuple(
            sector for sector in SECTORS
            if sector in {
                token.strip()
                for token in (row.get("org_sector") or "").split("|")
                if token.strip()
            }
        )
        source = (row.get("sign_source") or "").strip().casefold()
        raw_probability = (row.get("p_support") or "").strip()
        probability = None
        if raw_probability:
            probability = _number(
                raw_probability,
                f"signed attention row {row_number} p_support",
                minimum=0.0,
                maximum=1.0,
            )
            if source not in SIGN_AUTHORITY:
                raise ChampionsV2Error(
                    f"signed attention row {row_number} has a probability but "
                    f"unsupported sign_source {source!r}"
                )
        identity_key = (lob_id, bill)
        if identity_key in seen:
            raise ChampionsV2Error(
                f"signed attention repeats org-bill pair {lob_id} / {bill}"
            )
        seen.add(identity_key)
        for sector in sectors:
            if source in SIGN_AUTHORITY:
                sign_coverage[sector][source] += 1
        rows.append(
            {
                "lob_id": lob_id,
                "bill_key": bill,
                "sectors": sectors,
                "sign_source": source,
                "p_support": probability,
            }
        )
    if not rows:
        raise ChampionsV2Error("signed attention has no rows")
    return rows, sign_coverage


def _load_interactions(path):
    required = ("bill_key", "bioguide_id", "role")
    members_by_bill = collections.defaultdict(dict)
    rows = 0
    for row_number, row in _iter_csv(path, "interaction bill members", required):
        rows += 1
        bill = _bill_key(row.get("bill_key"), f"interaction row {row_number}")
        bioguide = (row.get("bioguide_id") or "").strip()
        role = (row.get("role") or "").strip().casefold()
        if not bioguide:
            raise ChampionsV2Error(f"interaction row {row_number} has blank bioguide")
        if role not in {"sponsor", "cosponsor"}:
            raise ChampionsV2Error(
                f"interaction row {row_number} has unsupported role {role!r}"
            )
        previous = members_by_bill[bill].get(bioguide)
        if previous is not None and previous != role:
            raise ChampionsV2Error(
                f"interaction bill {bill} maps {bioguide} to two roles"
            )
        members_by_bill[bill][bioguide] = role
    if not rows:
        raise ChampionsV2Error("interaction bill members has no rows")
    return members_by_bill, rows


def _load_panel(path):
    required = ("bioguide", "congress", "chamber", "net_directional")
    net_by_member = collections.defaultdict(float)
    seen = set()
    rows = 0
    for row_number, row in _iter_csv(path, "member-congress panel", required):
        congress = _integer(
            row.get("congress"), f"member-congress panel row {row_number} congress"
        )
        if congress not in {117, 118, 119}:
            continue
        rows += 1
        bioguide = (row.get("bioguide") or "").strip()
        if not bioguide:
            raise ChampionsV2Error(
                f"member-congress panel row {row_number} has blank bioguide"
            )
        key = (bioguide, congress)
        if key in seen:
            raise ChampionsV2Error(
                f"member-congress panel repeats {bioguide} / {congress}"
            )
        seen.add(key)
        net_by_member[bioguide] += _number(
            row.get("net_directional"),
            f"member-congress panel row {row_number} net_directional",
        )
    if not rows:
        raise ChampionsV2Error("member-congress panel has no 117-119 rows")
    return dict(net_by_member), rows


def _load_o(path):
    payload = _load_json(path, "pillar input snapshot")
    values = payload.get("O") if isinstance(payload, dict) else None
    if not isinstance(values, dict) or not values:
        raise ChampionsV2Error("pillar input snapshot lacks non-empty O mapping")
    return {
        str(name): _number(value, f"pillar O for {name}")
        for name, value in values.items()
    }


def _load_member_bundle(path):
    payload = _load_json(path, "member desk bundle")
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, list) or not members:
        raise ChampionsV2Error("member desk bundle lacks non-empty members list")
    by_name = {}
    name_to_bioguide = {}
    for index, row in enumerate(members):
        if not isinstance(row, dict):
            raise ChampionsV2Error(f"member desk bundle row {index} is not an object")
        missing = {
            field for field in ("name", "chamber", "tier", "invested")
            if field not in row
        }
        if missing:
            raise ChampionsV2Error(
                f"member desk bundle row {index} lacks {', '.join(sorted(missing))}"
            )
        name = str(row.get("name") or "").strip()
        chamber = str(row.get("chamber") or "").strip()
        if not name or chamber not in CHAMBERS:
            raise ChampionsV2Error(
                f"member desk bundle row {index} has invalid name/chamber"
            )
        if name in by_name:
            raise ChampionsV2Error(f"member desk bundle repeats member name {name!r}")
        by_name[name] = {
            "chamber": chamber,
            "tier": row.get("tier"),
            "invested": _number(
                row.get("invested"), f"member desk bundle invested for {name}", minimum=0
            ),
        }
        bio = row.get("bio")
        bioguide = (bio.get("bioguide") or "").strip() if isinstance(bio, dict) else ""
        if bioguide:
            name_to_bioguide[name] = bioguide
    return (
        by_name,
        desk_identity.Resolver(by_name, name_to_bioguide=name_to_bioguide),
        len(members),
    )


def _load_titles(path):
    payload = _load_json(path, "bill title cache")
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("schema") != "bill-titles-v1":
        raise ChampionsV2Error(f"bill title cache schema mismatch: {schema!r}")
    titles = payload.get("titles")
    if not isinstance(titles, dict):
        raise ChampionsV2Error("bill title cache lacks titles mapping")
    return {
        _bill_key(key, "bill title cache"): str(value or "").strip()
        for key, value in titles.items()
    }


def _direction_bill_key(bill, congress, label):
    normalized = re.sub(r"[^A-Z0-9]", "", str(bill or "").upper())
    match = re.match(r"^([A-Z]+)(\d+)$", normalized)
    if not match or match.group(1) not in BILL_TYPE_ALIASES:
        raise ChampionsV2Error(f"{label} has unsupported Bill value {bill!r}")
    congress_number = _integer(congress, f"{label} Congress")
    return f"{congress_number}:{BILL_TYPE_ALIASES[match.group(1)]}:{int(match.group(2))}"


def _load_bill_directions(path):
    required = ("Bill", "Congress", "Category")
    categories = {}
    rows = 0
    for row_number, row in _iter_csv(path, "bill directions", required):
        rows += 1
        # Coordinator fix 2026-08-06: the canonical file carries rows with a
        # blank Congress (14, known-unjoinable) and rows with a blank
        # Category (optional issue label).  Both fall through: the issue
        # string falls back to the title cache downstream.
        congress_raw = (row.get("Congress") or "").strip()
        category = (row.get("Category") or "").strip()
        if not congress_raw or not category:
            continue
        key = _direction_bill_key(
            row.get("Bill"), congress_raw, f"bill directions row {row_number}"
        )
        previous = categories.get(key)
        if previous is not None and previous != category:
            raise ChampionsV2Error(
                f"bill directions maps {key} to two categories"
            )
        categories[key] = category
    if not rows:
        raise ChampionsV2Error("bill directions has no rows")
    return categories, rows


def _z_scores(rows, value):
    if not rows:
        return {}
    values = [float(value(row)) for row in rows]
    mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / len(values)
    scale = math.sqrt(variance)
    if scale <= 1e-12:
        return {row["bioguide"]: 0.0 for row in rows}
    return {
        row["bioguide"]: (item - mean) / scale
        for row, item in zip(rows, values)
    }


def _driving_bills(
    member,
    sector,
    opposed_bills,
    opposed_sponsored,
    oppose_orgs_by_bill,
    oppose_sources_by_bill,
    titles,
    categories,
):
    sponsored = opposed_sponsored.get(member, set())
    if sponsored:
        bills = sorted(
            sponsored,
            key=lambda bill: (-len(oppose_orgs_by_bill[sector][bill]), bill),
        )
    else:
        bills = sorted(
            opposed_bills.get(member, set()),
            key=lambda bill: (-len(oppose_orgs_by_bill[sector][bill]), bill),
        )[:3]
    driving = []
    issues = []
    for bill in bills:
        source_counts = oppose_sources_by_bill[sector].get(bill, {})
        source = min(source_counts, key=lambda item: SIGN_AUTHORITY[item])
        title = titles.get(bill) or bill
        category = categories.get(bill) or ""
        driving.append(
            {
                "bill_key": bill,
                "title": title,
                "category": category,
                "sign_source": source,
            }
        )
        issues.append(category or title or bill)
    return issues, driving


def _score_rows(rows, sector):
    component_names = CU_COMPONENTS if sector == "credit_union" else OTHER_COMPONENTS
    feature_getters = {
        "log1p_supportive_att": lambda row: math.log1p(row["components"]["supportive_att"]),
        "net_directional": lambda row: row["components"]["net_directional"],
        "O": lambda row: row["components"]["O"],
        "log1p_invested": lambda row: math.log1p(row["components"]["invested"]),
        "rapport": lambda row: row["components"]["rapport"],
    }
    for chamber in CHAMBERS:
        chamber_rows = [row for row in rows if row["chamber"] == chamber]
        component_z = [
            _z_scores(chamber_rows, feature_getters[name]) for name in component_names
        ]
        opposition_z = _z_scores(
            chamber_rows,
            lambda row: math.log1p(row["components"]["opposition_att"]),
        )
        for row in chamber_rows:
            bioguide = row["bioguide"]
            composite = sum(values[bioguide] for values in component_z) / len(component_z)
            threat = (
                opposition_z[bioguide]
                + row["components"]["opp_sponsored"]
                + 0.5 * row["components"]["opp_cosponsored"]
            )
            if not math.isfinite(composite) or not math.isfinite(threat):
                raise ChampionsV2Error(
                    f"non-finite score for {sector} / {bioguide}"
                )
            row["composite"] = round(composite, 6)
            row["threat"] = round(threat, 6)


def _row_sort(row, score_field):
    return (-row[score_field], row["name"].casefold(), row["bioguide"])


def _assert_boards(sector, boards, crown_bioguides, threat_bioguides):
    memberships = {}
    for board_name in ("champions", "threats", "mixed"):
        for row in boards[board_name]:
            bioguide = row["bioguide"]
            if bioguide in memberships:
                raise ChampionsV2Error(
                    f"{sector} board overlap: {bioguide} appears on "
                    f"{memberships[bioguide]} and {board_name}"
                )
            memberships[bioguide] = board_name
            if board_name == "threats" and bioguide in crown_bioguides:
                raise ChampionsV2Error(
                    f"{sector} champion exclusivity failure: "
                    f"{bioguide} Tier-1 crown reached threats"
                )
            if board_name == "champions" and bioguide in threat_bioguides:
                raise ChampionsV2Error(
                    f"{sector} cross-sector exclusivity failure: "
                    f"{bioguide} holds a champions seat while on a threat board"
                )
            if not math.isfinite(row["composite"]):
                raise ChampionsV2Error(
                    f"{sector} {board_name} row {bioguide} has non-finite composite"
                )
    for row in boards["champions"]:
        components = row["components"]
        if components["opposition_att"] > components["supportive_att"]:
            raise ChampionsV2Error(
                f"{sector} veto failure: {row['bioguide']} reached champions"
            )


def build_payload(
    signed_attention_path=None,
    interactions_path=None,
    panel_path=None,
    pillar_path=None,
    member_bundle_path=None,
    titles_path=None,
    bill_directions_path=None,
    resolver=None,
):
    defaults = _default_paths()
    signed_attention_path = signed_attention_path or defaults["signed_attention_path"]
    interactions_path = interactions_path or defaults["interactions_path"]
    panel_path = panel_path or defaults["panel_path"]
    pillar_path = pillar_path or defaults["pillar_path"]
    member_bundle_path = member_bundle_path or defaults["member_bundle_path"]
    titles_path = titles_path or defaults["titles_path"]
    bill_directions_path = bill_directions_path or defaults["bill_directions_path"]

    signed_rows, sign_coverage = _load_signed_attention(signed_attention_path)
    members_by_bill, interaction_rows = _load_interactions(interactions_path)
    net_by_member, panel_rows = _load_panel(panel_path)
    o_by_name = _load_o(pillar_path)
    desk_by_name, desk_resolver, desk_members = _load_member_bundle(member_bundle_path)
    titles = _load_titles(titles_path)
    categories, direction_rows = _load_bill_directions(bill_directions_path)
    if resolver is None:
        try:
            resolver = identity.MemberNameResolver.from_paths()
        except identity.IdentityError as exc:
            raise ChampionsV2Error(f"member-name resolver unavailable: {exc}") from exc

    support = {sector: collections.Counter() for sector in SECTORS}
    opposition = {sector: collections.Counter() for sector in SECTORS}
    support_congresses = {
        sector: collections.defaultdict(set) for sector in SECTORS
    }
    opposed_bills = {
        sector: collections.defaultdict(set) for sector in SECTORS
    }
    member_oppose_sources = {
        sector: collections.defaultdict(collections.Counter) for sector in SECTORS
    }
    oppose_orgs_by_bill = {
        sector: collections.defaultdict(set) for sector in SECTORS
    }
    oppose_sources_by_bill = {
        sector: collections.defaultdict(collections.Counter) for sector in SECTORS
    }
    signed_bills_without_members = set()

    for signed in signed_rows:
        probability = signed["p_support"]
        if probability is None:
            continue
        bill = signed["bill_key"]
        bill_members = members_by_bill.get(bill, {})
        if not bill_members:
            signed_bills_without_members.add(bill)
            continue
        congress = _integer(bill.split(":", 1)[0], f"bill congress for {bill}")
        for sector in signed["sectors"]:
            if probability >= SUPPORT_THRESHOLD:
                for member in bill_members:
                    support[sector][member] += 1
                    support_congresses[sector][member].add(congress)
            elif probability <= OPPOSITION_THRESHOLD:
                oppose_orgs_by_bill[sector][bill].add(signed["lob_id"])
                oppose_sources_by_bill[sector][bill][signed["sign_source"]] += 1
                for member in bill_members:
                    opposition[sector][member] += 1
                    opposed_bills[sector][member].add(bill)
                    member_oppose_sources[sector][member][signed["sign_source"]] += 1

    opposed_sponsored = {
        sector: collections.defaultdict(set) for sector in SECTORS
    }
    # Coordinator routing fix (2026-08-06, the Durbin/Marshall acceptance
    # probe): corroborated opposed COSPONSORSHIP also counts - the CCCA's
    # Senate lead cosponsor is opposition leadership, not a mixed docket.
    opposed_cosponsored = {
        sector: collections.defaultdict(set) for sector in SECTORS
    }
    for sector in SECTORS:
        for bill, orgs in oppose_orgs_by_bill[sector].items():
            if len(orgs) < 2:
                continue
            for member, role in members_by_bill.get(bill, {}).items():
                if role == "sponsor":
                    opposed_sponsored[sector][member].add(bill)
                else:
                    opposed_cosponsored[sector][member].add(bill)

    sectors_payload = {}
    sector_receipts = {}
    staged_sectors = {}
    all_formers = set()
    all_desk_unavailable = set()
    crown_bioguides = set()
    for sector in SECTORS:
        candidates = sorted(set(support[sector]) | set(opposition[sector]))
        rows = []
        former_members = []
        desk_unavailable_members = []
        for bioguide in candidates:
            try:
                record = resolver.resolve(bioguide)
            except identity.IdentityError as exc:
                raise ChampionsV2Error(
                    f"cannot resolve member {bioguide}: {exc}"
                ) from exc
            if record["is_former"]:
                former_members.append(bioguide)
                all_formers.add(bioguide)
                continue
            if record["status"] == identity.STATUS_UNRESOLVED:
                raise ChampionsV2Error(
                    f"current-board candidate {bioguide} has no resolvable name"
                )
            resolved_name = record["name"]
            name = desk_resolver.resolve(name=resolved_name, bioguide=bioguide)
            if name is None:
                desk_unavailable_members.append(bioguide)
                all_desk_unavailable.add(bioguide)
                continue
            desk = desk_by_name[name]
            desk_tier = _integer(
                desk["tier"], f"desk tier for {sector} / {bioguide}"
            )
            if desk_tier == 1:
                crown_bioguides.add(bioguide)
            supportive_att = int(support[sector][bioguide])
            opposition_att = int(opposition[sector][bioguide])
            components = {
                "supportive_att": supportive_att,
                "opposition_att": opposition_att,
                "opp_sponsored": len(opposed_sponsored[sector].get(bioguide, set())),
                "opp_cosponsored": len(opposed_cosponsored[sector].get(bioguide, set())),
                "rapport": len(support_congresses[sector].get(bioguide, set())),
                "net_directional": None,
                "O": None,
                "invested": None,
                "sign_source_counts": {
                    source: int(member_oppose_sources[sector][bioguide][source])
                    for source in SIGN_SOURCES
                },
            }
            if sector == "credit_union":
                if bioguide not in net_by_member:
                    raise ChampionsV2Error(
                        f"CU candidate {bioguide} is absent from the 117-119 panel"
                    )
                if name not in o_by_name:
                    raise ChampionsV2Error(
                        f"CU candidate {bioguide} / {name} is absent from pillar O"
                    )
                components.update(
                    {
                        "net_directional": net_by_member[bioguide],
                        "O": o_by_name[name],
                        "invested": desk["invested"],
                    }
                )
            rows.append(
                {
                    "bioguide": bioguide,
                    "name": name,
                    "chamber": desk["chamber"],
                    "tier": desk["tier"] if sector == "credit_union" else None,
                    "components": components,
                    "composite": 0.0,
                    "threat": 0.0,
                    "issues": [],
                    "driving_bills": [],
                }
            )

        _score_rows(rows, sector)
        champions = []
        threats = []
        mixed = []
        crown_threat_exclusions = []
        for row in rows:
            components = row["components"]
            vetoed = components["opposition_att"] > components["supportive_att"]
            # Opposition LEADERSHIP forces the threat board regardless of the
            # member's supportive profile (2026-08-06 routing fix): sponsoring
            # any corroborated-opposed bill, or cosponsoring two or more.
            opposition_leader = (
                components["opp_sponsored"] >= 1
                or components["opp_cosponsored"] >= 2
            )
            is_mixed = (
                not vetoed
                and not opposition_leader
                and components["opposition_att"] >= 3
            )
            if vetoed or opposition_leader:
                row["issues"], row["driving_bills"] = _driving_bills(
                    row["bioguide"], sector, opposed_bills[sector],
                    opposed_sponsored[sector], oppose_orgs_by_bill,
                    oppose_sources_by_bill, titles, categories,
                )
                if row["bioguide"] in crown_bioguides:
                    routed_to = (
                        "mixed" if components["opposition_att"] >= 3 else "omitted"
                    )
                    if routed_to == "mixed":
                        mixed.append(row)
                    crown_threat_exclusions.append(
                        {
                            "bioguide": row["bioguide"],
                            "name": row["name"],
                            "chamber": row["chamber"],
                            "tier": 1,
                            "threat": row["threat"],
                            "opposition_att": components["opposition_att"],
                            "opp_sponsored": components["opp_sponsored"],
                            "opp_cosponsored": components["opp_cosponsored"],
                            "routed_to": routed_to,
                        }
                    )
                else:
                    threats.append(row)
            elif is_mixed:
                row["issues"], row["driving_bills"] = _driving_bills(
                    row["bioguide"], sector, opposed_bills[sector],
                    opposed_sponsored[sector], oppose_orgs_by_bill,
                    oppose_sources_by_bill, titles, categories,
                )
                mixed.append(row)
            else:
                champions.append(row)

        threats.sort(key=lambda row: _row_sort(row, "threat"))
        threats = threats[:THREAT_LIMIT]
        mixed.sort(key=lambda row: _row_sort(row, "composite"))
        staged_sectors[sector] = {
            "champions_class": champions,
            "threats": threats,
            "mixed": mixed,
            "candidate_members": len(candidates),
            "eligible_current_members": len(rows),
            "former_members": former_members,
            "desk_unavailable_members": desk_unavailable_members,
            "crown_threat_exclusions": crown_threat_exclusions,
        }

    # CROSS-SECTOR EXCLUSIVITY (champion seat yields, Paul 2026-08-09): the
    # champions cut must see every sector's RETAINED threat board, so champion
    # retention runs as a second pass over the staged sectors.
    threat_sectors_by_bioguide = {}
    for sector in SECTORS:
        for row in staged_sectors[sector]["threats"]:
            threat_sectors_by_bioguide.setdefault(
                row["bioguide"], []
            ).append(sector)
    threat_bioguides = set(threat_sectors_by_bioguide)
    for sector in SECTORS:
        staged = staged_sectors[sector]
        cross_sector_exclusions = []
        eligible_champions = []
        for row in staged["champions_class"]:
            threat_sectors = sorted(
                threat_sectors_by_bioguide.get(row["bioguide"], ())
            )
            if threat_sectors:
                cross_sector_exclusions.append(
                    {
                        "bioguide": row["bioguide"],
                        "name": row["name"],
                        "chamber": row["chamber"],
                        "composite": row["composite"],
                        "threat_sectors": threat_sectors,
                        "routed_to": "omitted",
                    }
                )
            else:
                eligible_champions.append(row)
        retained_champions = []
        for chamber in CHAMBERS:
            retained_champions.extend(
                sorted(
                    (row for row in eligible_champions
                     if row["chamber"] == chamber),
                    key=lambda row: _row_sort(row, "composite"),
                )[:CHAMPION_LIMIT_PER_CHAMBER]
            )
        retained_champions.sort(key=lambda row: _row_sort(row, "composite"))
        boards = {
            "champions": retained_champions,
            "threats": staged["threats"],
            "mixed": staged["mixed"],
        }
        _assert_boards(sector, boards, crown_bioguides, threat_bioguides)
        sectors_payload[sector] = boards
        sector_receipts[sector] = {
            "composite_components": list(
                CU_COMPONENTS if sector == "credit_union" else OTHER_COMPONENTS
            ),
            "row_counts": {
                "candidate_members": staged["candidate_members"],
                "eligible_current_members": staged["eligible_current_members"],
                "champions": len(retained_champions),
                "threats": len(staged["threats"]),
                "mixed": len(staged["mixed"]),
            },
            "sign_coverage": sign_coverage[sector],
            "former_members_excluded": len(staged["former_members"]),
            "former_bioguides": staged["former_members"],
            "desk_members_excluded": len(staged["desk_unavailable_members"]),
            "desk_unavailable_bioguides": staged["desk_unavailable_members"],
            "crown_threat_exclusions": staged["crown_threat_exclusions"],
            "cross_sector_exclusions": cross_sector_exclusions,
        }

    payload = {
        "schema": SCHEMA,
        "method_note": METHOD_NOTE,
        "sectors": sectors_payload,
        "receipt": {
            "sector_order": list(SECTORS),
            "sectors": sector_receipts,
            "ladder": "canonical > kim > prior",
            "rules": {
                "veto": VETO_RULE,
                "mixed": MIXED_RULE,
                "corroboration": CORROBORATION_RULE,
                "champion_exclusivity": CHAMPION_EXCLUSIVITY_RULE,
                "cross_sector": CROSS_SECTOR_RULE,
            },
            "method_note": METHOD_NOTE,
            "thresholds": {
                "support_at_or_above": SUPPORT_THRESHOLD,
                "oppose_at_or_below": OPPOSITION_THRESHOLD,
            },
            "limits": {
                "champions_per_chamber": CHAMPION_LIMIT_PER_CHAMBER,
                "threats": THREAT_LIMIT,
                "mixed": None,
            },
            "former_members_excluded": len(all_formers),
            "former_bioguides": sorted(all_formers),
            "desk_members_excluded": len(all_desk_unavailable),
            "desk_unavailable_bioguides": sorted(all_desk_unavailable),
            "identity_resolution": resolver.receipt(),
            "input_rows": {
                "signed_attention": len(signed_rows),
                "interaction_bill_members": interaction_rows,
                "member_congress_panel_117_119": panel_rows,
                "member_desk_bundle_members": desk_members,
                "bill_directions": direction_rows,
            },
            "signed_bills_without_member_roster": len(signed_bills_without_members),
        },
    }
    for sector, boards in payload["sectors"].items():
        _assert_boards(sector, boards, crown_bioguides, threat_bioguides)
    return payload


def _write_json_atomic(payload, path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return len(body.encode("utf-8"))


def main(argv=None):
    defaults = _default_paths()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--signed-attention", default=defaults["signed_attention_path"])
    parser.add_argument("--interactions", default=defaults["interactions_path"])
    parser.add_argument("--panel", default=defaults["panel_path"])
    parser.add_argument("--pillar", default=defaults["pillar_path"])
    parser.add_argument("--member-bundle", default=defaults["member_bundle_path"])
    parser.add_argument("--titles", default=defaults["titles_path"])
    parser.add_argument("--bill-directions", default=defaults["bill_directions_path"])
    parser.add_argument(
        "--out",
        default=os.path.join(_derived_dir(), "champions_v2", "boards.json"),
    )
    args = parser.parse_args(argv)
    payload = build_payload(
        signed_attention_path=args.signed_attention,
        interactions_path=args.interactions,
        panel_path=args.panel,
        pillar_path=args.pillar,
        member_bundle_path=args.member_bundle,
        titles_path=args.titles,
        bill_directions_path=args.bill_directions,
    )
    payload["generated_utc"] = datetime.now(timezone.utc).isoformat()
    size = _write_json_atomic(payload, args.out)
    counts = {
        board: sum(len(payload["sectors"][sector][board]) for sector in SECTORS)
        for board in ("champions", "threats", "mixed")
    }
    print(
        "champions v2: %d champions | %d threats | %d mixed | %d bytes -> %s"
        % (
            counts["champions"], counts["threats"], counts["mixed"], size,
            os.path.abspath(args.out),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
