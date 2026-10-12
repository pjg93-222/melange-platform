# -*- coding: utf-8 -*-
"""Build the per-activity lobbying interaction ledger.

The shipped network answers "did this organization lobby a bill this member
sponsored or cosponsored, and how often".  It cannot answer "show me the actual
filings", because it aggregates to (organization, member, year) counts before
anything reaches disk.  This module keeps the DISCLOSURE ROW itself: the filing
date, the registrant that filed it, the individual lobbyists named on it, the
bill cited, and the description the lobbyist wrote.

It deliberately reuses ``lobbying.build_network_2021_rebuild`` for every judgement call --
organization attribution, bill-mention resolution, and the sponsor/cosponsor
map -- so the ledger and the network agree BY CONSTRUCTION rather than by
coincidence.  ``verify_against_network`` makes that agreement a hard gate: the
per-year activity counts and the strict dyad set must match the shipped network
CSVs exactly, or the build fails without writing.

ON POSITION.  The LD-2 form has no position field.  Every one of the 24,819
in-scope activity records carries zero position keys -- measured, not assumed.
So ``position`` ships empty with ``position_basis='not_disclosed'``.  The column
is wired end to end so an inference lane can fill it later; until one does, the
honest answer is the lobbyist's own words, which is why the description travels
with every row.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import csv
import hashlib
import json
import os
import sys

from lobbying import build_network_2021_rebuild as builder

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


IngestError = builder.IngestError

YEARS = tuple(range(2021, 2027))
PARTIAL_YEAR = 2026
FIRST_SOURCE_CONGRESS = 115

LEDGER_FIELDS = [
    "filing_year",
    "filing_uuid",
    "activity_index",
    "dt_posted",
    "filing_date",
    "filing_period",
    "lob_id",
    "client_name",
    "registrant_name",
    "lobbyists",
    "n_lobbyists",
    "covered_positions",
    "general_issue_code",
    "general_issue_display",
    "government_entities",
    "bill_key",
    "bill_display",
    "channel",
    "note_id",
    "position",
    "position_basis",
]
NOTE_FIELDS = ["note_id", "n_activities", "n_characters", "description"]
BILL_MEMBER_FIELDS = [
    "bill_key",
    "bioguide_id",
    "role",
    "sponsorship_date",
]

# The channel vocabulary is the shipped one; a ledger row is emitted only for a
# bill the network itself would have counted, and the channel records which
# extraction saw it so a reader can discount title-only matches.
CHANNEL_FORMAL = "formal_strict"
CHANNEL_BOTH = "both_strict"
CHANNEL_TITLE_STRICT = "title_strict"
CHANNEL_TITLE_GENEROUS = "title_generous_only"

POSITION_NOT_DISCLOSED = "not_disclosed"


def _note_id(description):
    return hashlib.sha256(description.encode("utf-8")).hexdigest()[:16]


def _filing_date(dt_posted):
    """Calendar date of the filing, which is the only date LDA timestamps."""
    if not dt_posted:
        return ""
    return str(dt_posted)[:10]


def _lobbyist_name(record):
    person = record.get("lobbyist") or {}
    parts = [
        str(person.get(field) or "").strip()
        for field in ("first_name", "middle_name", "last_name")
    ]
    return " ".join(part for part in parts if part)


def _activity_channel(bill_key, result):
    """Which extraction channel credited this bill, in the shipped vocabulary."""
    formal = bill_key in result["credited_formal_keys"]
    title = bill_key in result["generous_title_keys"]
    strict = bill_key in result["strict_bill_keys"]
    if formal and title:
        return CHANNEL_BOTH
    if formal:
        return CHANNEL_FORMAL
    if strict:
        return CHANNEL_TITLE_STRICT
    return CHANNEL_TITLE_GENEROUS


def load_scope(paths):
    """Organization scope and attribution indexes, from the shipped receipts.

    The receipts file records the client IDs the full-scope build actually
    accepted for each organization.  Reading it -- instead of re-deriving the
    harvest -- is what makes this ledger reproduce that build rather than a
    plausible neighbour of it.
    """
    receipts_path = builder.fullscope_receipts_path(paths)
    if not os.path.isfile(receipts_path):
        raise IngestError(
            f"missing full-scope receipts {receipts_path}; run the network build first"
        )
    with open(receipts_path, "rb") as handle:
        receipts = json.loads(handle.read().decode("utf-8"))
    per_org = receipts.get("identity_harvest", {}).get("per_org")
    if not isinstance(per_org, list) or not per_org:
        raise IngestError("full-scope receipts carry no identity_harvest.per_org")
    harvested = {}
    for row in per_org:
        lob_id = row.get("lob_id")
        if not isinstance(lob_id, str) or not lob_id:
            raise IngestError("identity_harvest.per_org row has no lob_id")
        harvested[lob_id] = set(row.get("accepted_client_ids") or [])
    return harvested, receipts


def load_org_sectors(paths):
    """sector per lob_id, from the same roster the industry page reads."""
    roster_path = os.path.join(
        paths["derived_dir"], "sector_spend", "sector_roster.csv"
    )
    if not os.path.isfile(roster_path):
        raise IngestError(f"missing sector roster {roster_path}")
    sectors = {}
    ambiguous = set()
    with open(roster_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            sector = (row.get("sector") or "").strip()
            if not sector:
                continue
            for client_id in (row.get("client_ids") or "").split("|"):
                client_id = client_id.strip()
                if not client_id:
                    continue
                previous = sectors.get(client_id)
                if previous is not None and previous != sector:
                    ambiguous.add(client_id)
                sectors[client_id] = sector
    for client_id in ambiguous:
        sectors.pop(client_id, None)
    return sectors, len(ambiguous)


def load_bill_people(paths, congresses):
    """Sponsor/cosponsor map plus title index, per Congress.

    Uses the builder's loader so the member set behind every ledger row is
    literally the set the network attributed bills to.
    """
    people_maps = {}
    title_indexes = {}
    roles = {}
    receipts = {}
    for congress in sorted(congresses):
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        loaded = builder.load_sponsor_map(
            builder.billstatus_dir_for_congress(paths, congress),
            congress=congress,
            zip_types=zip_types,
            include_forecast_metadata=True,
        )
        people_map, _keys, _lacking, title_index, _title_receipts, map_receipts = (
            loaded[:6]
        )
        forecast_metadata = loaded[6]
        people_maps[congress] = people_map
        title_indexes[congress] = title_index
        receipts[congress] = {
            "bills_mapped": map_receipts["bills_mapped"],
            "bills_lacking_sponsor": map_receipts["bills_lacking_sponsor"],
        }
        # Role and sponsorship date are not in the network's merged people set;
        # they come from the same parse, so they cannot disagree with it.
        for key, metadata in forecast_metadata.items():
            canonical = builder.canonical_bill_key(key)
            entry = roles.setdefault(canonical, {})
            sponsor = metadata.get("sponsor_bioguide")
            if sponsor:
                entry[sponsor] = ("sponsor", metadata.get("introduced_date") or "")
            for cosponsor in metadata.get("cosponsors") or ():
                bioguide = cosponsor.get("bioguide_id")
                if not bioguide or bioguide in entry:
                    continue
                entry[bioguide] = (
                    "cosponsor",
                    cosponsor.get("sponsorship_date") or "",
                )
    return people_maps, title_indexes, roles, receipts


def _iter_scope_filings(paths, filing_year, harvested, crosswalk_indexes):
    """Every sweep filing attributed to an in-scope organization, deduplicated."""
    sweep_dir = os.path.join(paths["sweeps_dir"], str(filing_year))
    if not os.path.isdir(sweep_dir):
        raise IngestError(f"missing sweep directory {sweep_dir}")
    seen = set()
    for page_name in sorted(os.listdir(sweep_dir)):
        if not page_name.endswith(".json"):
            continue
        with open(os.path.join(sweep_dir, page_name), "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
        for index, filing in enumerate(payload.get("results") or []):
            builder._validate_sweep_filing(filing, filing_year, page_name, index)
            filing_uuid = filing["filing_uuid"]
            if filing_uuid in seen:
                continue
            seen.add(filing_uuid)
            attribution = builder.attribute_sweep_filing(
                filing, harvested, crosswalk_indexes=crosswalk_indexes
            )
            lob_id = attribution["lob_id"]
            if not lob_id:
                continue
            yield lob_id, filing


def build_ledger(paths, years=YEARS, progress=None):
    """Resolve every in-scope activity into ledger rows, notes, and members."""
    harvested, scope_receipts = load_scope(paths)
    sectors, ambiguous_sector_ids = load_org_sectors(paths)
    crosswalk_indexes = builder.bridge3_crosswalk_indexes()

    congresses = sorted(
        set(range(FIRST_SOURCE_CONGRESS, builder.congress_for_year(max(years)) + 1))
    )
    people_maps, title_indexes, roles, people_receipts = load_bill_people(
        paths, congresses
    )

    ledger = []
    notes = {}
    counts = collections.Counter()
    activities_by_year = collections.Counter()
    filings_by_year = collections.Counter()
    dyads = collections.defaultdict(set)
    org_names = {}
    bills_seen = set()

    for filing_year in sorted(years):
        congress = builder.congress_for_year(filing_year)
        title_index = title_indexes[congress]
        # A filing may cite a bill from an earlier Congress; the builder resolves
        # those against every Congress up to the filing's own.
        year_people_maps = {
            source: people_maps[source]
            for source in congresses
            if source <= congress
        }
        for lob_id, filing in _iter_scope_filings(
            paths, filing_year, harvested, crosswalk_indexes
        ):
            filings_by_year[filing_year] += 1
            client = filing.get("client") or {}
            client_name = str(client.get("name") or "").strip()
            org_names.setdefault(lob_id, client_name)
            client_id = builder.normalize_client_identifier(
                client.get("id"), filing["filing_uuid"], "client.id"
            )
            sector = sectors.get(str(client_id), "")
            registrant = str((filing.get("registrant") or {}).get("name") or "").strip()
            dt_posted = filing.get("dt_posted") or ""
            filing_period = filing.get("filing_period") or ""

            activities = builder._validated_activities(filing, filing["filing_uuid"])
            for activity_index, activity in enumerate(activities):
                activities_by_year[filing_year] += 1
                description = activity["description"]
                result = builder.resolve_activity_mentions(
                    description, title_index, filing_year, year_people_maps
                )
                bill_keys = sorted(
                    result["generous_bill_keys"],
                    key=lambda key: builder.canonical_bill_key(key),
                )
                if not bill_keys:
                    counts["activities_without_a_bill"] += 1
                    continue
                counts["activities_with_a_bill"] += 1
                note_id = _note_id(description)
                note = notes.setdefault(
                    note_id,
                    {
                        "note_id": note_id,
                        "n_activities": 0,
                        "n_characters": len(description),
                        "description": description,
                    },
                )
                note["n_activities"] += 1

                lobbyists = []
                covered = []
                for record in activity.get("lobbyists") or []:
                    name = _lobbyist_name(record)
                    if name and name not in lobbyists:
                        lobbyists.append(name)
                    position = str(record.get("covered_position") or "").strip()
                    if position and position not in covered:
                        covered.append(position)
                entities = []
                for entity in activity.get("government_entities") or []:
                    name = (
                        entity.get("name")
                        if isinstance(entity, dict)
                        else str(entity)
                    )
                    name = str(name or "").strip()
                    if name and name not in entities:
                        entities.append(name)

                for bill_key in bill_keys:
                    canonical = builder.canonical_bill_key(bill_key)
                    bills_seen.add(canonical)
                    channel = _activity_channel(bill_key, result)
                    ledger.append(
                        {
                            "filing_year": filing_year,
                            "filing_uuid": filing["filing_uuid"],
                            "activity_index": activity_index,
                            "dt_posted": dt_posted,
                            "filing_date": _filing_date(dt_posted),
                            "filing_period": filing_period,
                            "lob_id": lob_id,
                            "client_name": client_name,
                            "registrant_name": registrant,
                            "lobbyists": "|".join(lobbyists),
                            "n_lobbyists": len(lobbyists),
                            "covered_positions": "|".join(covered),
                            "general_issue_code": activity.get("general_issue_code")
                            or "",
                            "general_issue_display": activity.get(
                                "general_issue_code_display"
                            )
                            or "",
                            "government_entities": "|".join(entities),
                            "bill_key": canonical,
                            "bill_display": _bill_display(canonical),
                            "channel": channel,
                            "note_id": note_id,
                            # LDA discloses no position.  Wired, deliberately empty.
                            "position": "",
                            "position_basis": POSITION_NOT_DISCLOSED,
                        }
                    )
                    counts["ledger_rows"] += 1
                    if bill_key in result["strict_bill_keys"]:
                        people = year_people_maps.get(
                            bill_key[2], {}
                        ).get(bill_key, set())
                        for bioguide in people:
                            dyads[(lob_id, bioguide, filing_year)].add(canonical)
        if progress:
            progress(
                f"  {filing_year}: filings={filings_by_year[filing_year]} "
                f"activities={activities_by_year[filing_year]} "
                f"ledger_rows={counts['ledger_rows']}"
            )

    bill_members = []
    for canonical in sorted(bills_seen):
        for bioguide, (role, date) in sorted(roles.get(canonical, {}).items()):
            bill_members.append(
                {
                    "bill_key": canonical,
                    "bioguide_id": bioguide,
                    "role": role,
                    "sponsorship_date": date,
                }
            )

    ledger.sort(
        key=lambda row: (
            row["filing_year"],
            row["filing_date"],
            row["client_name"],
            row["filing_uuid"],
            row["activity_index"],
            row["bill_key"],
        )
    )
    receipts = {
        "generated_by": "fec/lobbying/lobby_interactions.py",
        "corpus_basis": scope_receipts.get("CORPUS_BASIS", ""),
        "years": list(sorted(years)),
        "partial_year": PARTIAL_YEAR,
        "congresses_loaded": congresses,
        "filings_attributed": dict(sorted(filings_by_year.items())),
        "activities_parsed": dict(sorted(activities_by_year.items())),
        "activities_with_a_bill": counts["activities_with_a_bill"],
        "activities_without_a_bill": counts["activities_without_a_bill"],
        "ledger_rows": counts["ledger_rows"],
        "distinct_notes": len(notes),
        "distinct_bills": len(bills_seen),
        "bill_member_rows": len(bill_members),
        "organizations": len(org_names),
        "ambiguous_sector_client_ids_dropped": ambiguous_sector_ids,
        "bill_people": people_receipts,
        "position_basis": POSITION_NOT_DISCLOSED,
        "position_note": (
            "The LD-2 form has no position field. Every in-scope activity record "
            "carries zero position keys, so position ships empty and the "
            "lobbyist's own description travels with every row instead."
        ),
    }
    return {
        "ledger": ledger,
        "notes": [notes[key] for key in sorted(notes)],
        "bill_members": bill_members,
        "dyads": dyads,
        "org_names": org_names,
        "receipts": receipts,
    }


def _bill_display(canonical):
    congress, bill_type, number = canonical.split(":")
    labels = {
        "hr": "H.R.",
        "s": "S.",
        "hjres": "H.J.Res.",
        "sjres": "S.J.Res.",
        "hconres": "H.Con.Res.",
        "sconres": "S.Con.Res.",
        "hres": "H.Res.",
        "sres": "S.Res.",
    }
    return f"{labels.get(bill_type, bill_type.upper() + '.')}{number}"


def verify_against_network(paths, built, years=YEARS):
    """Hard gate: the ledger must reproduce the shipped network exactly.

    Two independent checks. Activity counts prove the same filings were read;
    the strict dyad set proves the same bills reached the same members. Either
    disagreeing means the ledger is describing a different corpus than the page
    around it, which is worse than having no ledger at all.
    """
    problems = []
    parsed = built["receipts"]["activities_parsed"]
    network_dyads = collections.defaultdict(set)
    for filing_year in sorted(years):
        path = builder.fullscope_network_output_path(paths, filing_year, strict=True)
        if not os.path.isfile(path):
            raise IngestError(f"missing shipped network CSV {path}")
        with open(path, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                network_dyads[(row["lob_id"], row["bioguide_id"], filing_year)] = int(
                    row["n_bills"]
                )
    ledger_dyads = {key: len(value) for key, value in built["dyads"].items()}

    missing = sorted(set(network_dyads) - set(ledger_dyads))
    extra = sorted(set(ledger_dyads) - set(network_dyads))
    mismatched = sorted(
        key
        for key in set(network_dyads) & set(ledger_dyads)
        if network_dyads[key] != ledger_dyads[key]
    )
    if missing:
        problems.append(
            f"{len(missing)} shipped network dyads absent from the ledger, "
            f"first {missing[:3]}"
        )
    if extra:
        problems.append(
            f"{len(extra)} ledger dyads absent from the shipped network, "
            f"first {extra[:3]}"
        )
    if mismatched:
        sample = [
            (key, network_dyads[key], ledger_dyads[key]) for key in mismatched[:3]
        ]
        problems.append(
            f"{len(mismatched)} dyads disagree on n_bills, first {sample}"
        )
    return {
        "network_dyads": len(network_dyads),
        "ledger_dyads": len(ledger_dyads),
        "activities_parsed": parsed,
        "dyads_missing": len(missing),
        "dyads_extra": len(extra),
        "dyads_mismatched": len(mismatched),
        "problems": problems,
        "agrees": not problems,
    }


def _write_csv_atomic(path, fieldnames, rows):
    temp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temp_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=fieldnames, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def write_outputs(output_dir, built, verification):
    os.makedirs(output_dir, exist_ok=True)
    ledger_path = os.path.join(output_dir, "interaction_ledger.csv")
    notes_path = os.path.join(output_dir, "interaction_notes.csv")
    members_path = os.path.join(output_dir, "interaction_bill_members.csv")
    receipts_path = os.path.join(output_dir, "interaction_receipts.json")
    _write_csv_atomic(ledger_path, LEDGER_FIELDS, built["ledger"])
    _write_csv_atomic(notes_path, NOTE_FIELDS, built["notes"])
    _write_csv_atomic(members_path, BILL_MEMBER_FIELDS, built["bill_members"])
    receipts = dict(built["receipts"])
    receipts["network_agreement"] = verification
    builder.dump_atomic(receipts, receipts_path, indent=2, sort_keys=True)
    return {
        "ledger": ledger_path,
        "notes": notes_path,
        "bill_members": members_path,
        "receipts": receipts_path,
    }


def build(data_root=None, output_dir=None, years=YEARS, verify=True, progress=None):
    paths = builder.default_paths()
    if data_root:
        paths = dict(paths)
        paths["derived_dir"] = data_root
    output_dir = output_dir or os.path.join(paths["derived_dir"], "interactions")
    built = build_ledger(paths, years=years, progress=progress)
    verification = (
        verify_against_network(paths, built, years=years)
        if verify
        else {"agrees": None, "problems": [], "skipped": True}
    )
    if verify and not verification["agrees"]:
        raise IngestError(
            "interaction ledger disagrees with the shipped network: "
            + "; ".join(verification["problems"])
        )
    outputs = write_outputs(output_dir, built, verification)
    return built, verification, outputs


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Build the per-activity lobbying interaction ledger."
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--years", default=None)
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip the network-agreement gate (diagnostic only)",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    years = (
        tuple(int(value) for value in args.years.split(","))
        if args.years
        else YEARS
    )
    try:
        built, verification, outputs = build(
            data_root=args.data_root,
            output_dir=args.output_dir,
            years=years,
            verify=not args.no_verify,
            progress=lambda line: print(line, flush=True),
        )
    except IngestError as exc:
        print(f"INTERACTIONS ERROR: {exc}")
        return 2
    receipts = built["receipts"]
    print(
        "interaction ledger: "
        f"rows={receipts['ledger_rows']} "
        f"activities_with_a_bill={receipts['activities_with_a_bill']} "
        f"notes={receipts['distinct_notes']} "
        f"bills={receipts['distinct_bills']} "
        f"bill_member_rows={receipts['bill_member_rows']}"
    )
    print(
        "network agreement: "
        + (
            "EXACT"
            if verification.get("agrees")
            else ("SKIPPED" if verification.get("skipped") else "FAILED")
        )
        + f" ({verification.get('ledger_dyads')} dyads)"
    )
    for path in outputs.values():
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
