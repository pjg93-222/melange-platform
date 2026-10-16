# -*- coding: utf-8 -*-
"""Build the bill-repository lobbying sidecar.

The interaction ledger is the activity-level authority.  The annual bill index
is the network's already-adjudicated bridge from its canonical organization ID
to the exact repository bill identity and sector; this producer validates that
bridge against the sector roster instead of inventing another name join.

One interaction is one ledger row.  Filing totals deduplicate ``filing_uuid``;
organization and lobbyist counts deliberately do not, because the repository
needs the amount of disclosed bill work rather than another distinct-bill
count.  A lobbyist's top interaction is their heaviest organization/bill pair
across the corpus, with the latest filing date breaking ties.  Their industry
is the modal sector across every ledger interaction naming them.

Member rows carry NO per-member count.  LDA filings never name the members
lobbied, so the only true number is the bill-level interaction total, which
ships once as ``totals.interactions``.  (v1 stamped that bill total onto every
member row, which displayed as an identical "per-member" count — misleading,
Paul 2026-08-05.)
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import collections
import csv
import heapq
import json
import os
import re
import sys
import unicodedata

import members.historical_member_names as identity
from lobbying import build_network_2021_rebuild as network_builder
from lobbying import lobby_interactions as interaction_source

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SCHEMA = "bill-lobbying-sidecar-v2"
YEARS = tuple(range(2021, 2027))
TOP_ORGANIZATIONS = 8
TOP_LOBBYISTS = 5
TOP_MEMBERS = 12
TOP_FORECAST = 8

FORECAST_FIELDS = (
    "candidate_configuration",
    "congress",
    "bill",
    "member",
    "predicted_probability",
    "actual",
)

COVERAGE_NOTE = (
    "Only roughly 28% of lobbying activities cite a bill when pooled across the "
    "2021–2026 window. This sidecar covers that bill-citing subset; a bill absent "
    "from the sidecar has no bill-citing filings in this corpus, which does not "
    "mean there was no lobbying on it."
)
MEMBER_BASIS_NOTE = (
    "Members are the bill's sponsors and cosponsors used by the lobbying network. "
    "The filing establishes that the organization worked the bill, not that it "
    "contacted, persuaded, or spoke with any named member."
)

DEFAULT_OUTPUT = os.path.join(
    MB_ROOT, "data", "lobbying", "bill_lobbying_sidecar.json"
)

LEDGER_REQUIRED = tuple(
    field
    for field in interaction_source.LEDGER_FIELDS
    if field
    in {
        "filing_uuid",
        "filing_date",
        "lob_id",
        "client_name",
        "registrant_name",
        "lobbyists",
        "bill_key",
        "bill_display",
    }
)
BILL_MEMBER_REQUIRED = tuple(interaction_source.BILL_MEMBER_FIELDS)
SECTOR_REQUIRED = (
    "sector",
    "canonical_family",
    "raw_name_variants",
    "normalized_name_variants",
    "client_ids",
)


class SidecarError(RuntimeError):
    """An input contract failed; do not publish a partial sidecar."""


def _default_data_root():
    return network_builder.default_paths()["derived_dir"]


def _iter_csv(path, label, required_fields):
    if not os.path.isfile(path):
        raise SidecarError(f"{label} not found: {path}")
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            missing = sorted(set(required_fields) - fields)
            if missing:
                raise SidecarError(
                    f"{label} lacks required columns: {', '.join(missing)}"
                )
            for row_number, row in enumerate(reader, start=2):
                yield row_number, row
    except SidecarError:
        raise
    except (OSError, csv.Error) as exc:
        raise SidecarError(f"{label} unreadable: {exc}") from exc


def _normalize_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    text = text.replace("&", " AND ").replace("’", "'").replace("`", "'")
    text = re.sub(r"[^A-Z0-9']+", " ", text.upper())
    return re.sub(r"\s+", " ", text).strip()


def _split_roster_values(value):
    return [
        item.strip()
        for item in re.split(r"\s+\|\s+", value or "")
        if item.strip()
    ]


def _unambiguous_index(rows):
    values = {}
    conflicts = set()
    for key, value in rows:
        if not key or not value:
            continue
        previous = values.get(key)
        if previous is not None and previous != value:
            conflicts.add(key)
        else:
            values[key] = value
    for key in conflicts:
        values.pop(key, None)
    return values, conflicts


def _load_sector_roster(path):
    id_rows = []
    name_rows = []
    sectors = set()
    for row_number, row in _iter_csv(
        path, "sector roster", SECTOR_REQUIRED
    ):
        sector = (row["sector"] or "").strip()
        family = (row["canonical_family"] or "").strip()
        if not sector or not family:
            raise SidecarError(
                f"sector roster row {row_number} has blank sector/family"
            )
        sectors.add(sector)
        id_rows.extend(
            (client_id, sector)
            for client_id in _split_roster_values(row["client_ids"])
        )
        aliases = [family]
        aliases.extend(_split_roster_values(row["raw_name_variants"]))
        aliases.extend(_split_roster_values(row["normalized_name_variants"]))
        name_rows.extend((_normalize_name(alias), sector) for alias in aliases)
    if not sectors:
        raise SidecarError("sector roster is empty")
    # The bill-lobby index vocabulary is wider than the roster: financial_other
    # marks finance-lane filers outside the four named industries (790 rows in
    # 2021 alone), and sectors may be pipe-combined. It is a valid label here.
    sectors = set(sectors) | {"financial_other"}
    by_id, id_conflicts = _unambiguous_index(id_rows)
    by_name, name_conflicts = _unambiguous_index(name_rows)
    return {
        "sectors": sectors,
        "by_id": by_id,
        "by_name": by_name,
        "ambiguous_ids": len(id_conflicts),
        "ambiguous_names": len(name_conflicts),
    }


def _validate_bill_key(value, context):
    try:
        return network_builder.bill_key_from_canonical(value)
    except network_builder.IngestError as exc:
        raise SidecarError(f"{context}: {exc}") from exc


def _load_bill_index(data_root, sector_roster, years=YEARS):
    organizations = {}
    rows_read = 0
    seen_year_pairs = set()
    for year in years:
        path = network_builder.bill_lobby_index_output_path(
            {"derived_dir": data_root}, year
        )
        for row_number, row in _iter_csv(
            path,
            f"{year} bill-lobby index",
            network_builder.BILL_INDEX_FIELDS,
        ):
            rows_read += 1
            bill_key = (row["bill_key"] or "").strip()
            internal = _validate_bill_key(
                bill_key, f"{year} bill-lobby index row {row_number}"
            )
            try:
                congress = int(row["congress"])
                bill_number = int(row["bill_number"])
            except (TypeError, ValueError) as exc:
                raise SidecarError(
                    f"{year} bill-lobby index row {row_number} has invalid bill parts"
                ) from exc
            if (
                congress != internal[2]
                or (row["bill_type"] or "").strip() != internal[0].lower()
                or bill_number != internal[1]
            ):
                raise SidecarError(
                    f"{year} bill-lobby index row {row_number} disagrees with {bill_key}"
                )
            lob_id = (row["lob_id"] or "").strip()
            name = (row["client_name"] or "").strip()
            if not lob_id or not name:
                raise SidecarError(
                    f"{year} bill-lobby index row {row_number} has blank organization"
                )
            year_pair = (year, bill_key, lob_id)
            if year_pair in seen_year_pairs:
                raise SidecarError(
                    f"{year} bill-lobby index repeats {bill_key}/{lob_id}"
                )
            seen_year_pairs.add(year_pair)

            sector = (row["org_sector"] or "").strip()
            if not sector:
                sector = sector_roster["by_id"].get(lob_id) or sector_roster[
                    "by_name"
                ].get(_normalize_name(name), "")
            unknown = sorted(
                token
                for token in sector.split("|")
                if token and token not in sector_roster["sectors"]
            )
            if not sector or unknown:
                detail = "blank" if not sector else ", ".join(unknown)
                raise SidecarError(
                    f"{year} bill-lobby index row {row_number} has invalid sector {detail}"
                )

            pair = (bill_key, lob_id)
            value = {"name": name, "sector": sector}
            previous = organizations.get(pair)
            if previous is not None and previous != value:
                raise SidecarError(
                    f"bill-lobby index changes organization identity for {bill_key}/{lob_id}"
                )
            organizations[pair] = value
    if not organizations:
        raise SidecarError("bill-lobby indexes are empty")
    return organizations, rows_read


def _load_bill_members(path):
    members = collections.defaultdict(list)
    seen = set()
    for row_number, row in _iter_csv(
        path, "interaction bill members", BILL_MEMBER_REQUIRED
    ):
        bill_key = (row["bill_key"] or "").strip()
        bioguide = (row["bioguide_id"] or "").strip()
        if not bill_key or not bioguide:
            raise SidecarError(
                f"interaction bill members row {row_number} has blank identity"
            )
        _validate_bill_key(bill_key, f"interaction bill members row {row_number}")
        pair = (bill_key, bioguide)
        if pair in seen:
            raise SidecarError(
                f"interaction bill members repeats {bill_key}/{bioguide}"
            )
        seen.add(pair)
        members[bill_key].append(
            {
                "bioguide_id": bioguide,
                "role": (row["role"] or "").strip(),
                "sponsorship_date": (row.get("sponsorship_date") or "").strip(),
            }
        )
    return members


def _forecast_bill_key(congress, bill_label, context):
    parts = bill_label.split()
    if len(parts) != 2 or not parts[1].isdigit():
        raise SidecarError(f"{context} has unparseable bill label {bill_label!r}")
    try:
        congress_number = int(str(congress).strip())
    except (TypeError, ValueError) as exc:
        raise SidecarError(f"{context} has a bad congress {congress!r}") from exc
    return f"{congress_number}:{parts[0].lower()}:{int(parts[1])}"


def _load_joining_forecast(forecast_dir, wanted_bill_keys, resolver):
    """Top joining-forecast rows per lobbied bill, defensible configuration only.

    Source is the cosponsor_sc lane (the same artifact the industry surface
    quotes): a DESCRIPTIVE ridge-logistic cosponsorship model.  Probabilities
    are associations, never causal effects.
    """
    report_path = os.path.join(forecast_dir, "cosponsor_forecast_report.json")
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SidecarError(
            f"cannot read cosponsor forecast report {report_path}: {exc}"
        ) from exc
    configuration = str(
        report.get("headline_candidate_configuration") or ""
    ).strip()
    if not configuration:
        raise SidecarError(
            "cosponsor forecast report lacks headline_candidate_configuration"
        )
    congresses = sorted(
        int(value) for value in report.get("requested_congresses") or ()
    )
    if not congresses:
        raise SidecarError("cosponsor forecast report lacks requested_congresses")
    heaps = {}
    rows_read = 0
    rows_matched = 0
    for row_number, row in _iter_csv(
        os.path.join(forecast_dir, "cosponsor_scores.csv"),
        "cosponsor scores",
        FORECAST_FIELDS,
    ):
        rows_read += 1
        if (row["candidate_configuration"] or "").strip() != configuration:
            continue
        bill_key = _forecast_bill_key(
            row["congress"],
            (row["bill"] or "").strip(),
            f"cosponsor score row {row_number}",
        )
        if bill_key not in wanted_bill_keys:
            continue
        rows_matched += 1
        member = (row["member"] or "").strip()
        if not member:
            raise SidecarError(f"cosponsor score row {row_number} has a blank member")
        try:
            probability = float(row["predicted_probability"])
        except (TypeError, ValueError) as exc:
            raise SidecarError(
                f"cosponsor score row {row_number} has a bad probability"
            ) from exc
        if not 0.0 <= probability <= 1.0:
            raise SidecarError(
                f"cosponsor score row {row_number} probability outside [0, 1]"
            )
        actual_text = (row["actual"] or "").strip()
        if actual_text not in ("", "0", "1"):
            raise SidecarError(
                f"cosponsor score row {row_number} actual must be blank or 0/1"
            )
        item = (probability, member, int(actual_text) if actual_text else -1)
        heap = heaps.setdefault(bill_key, [])
        if len(heap) < TOP_FORECAST:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    forecast_rows = {}
    for bill_key, heap in heaps.items():
        rows = []
        for probability, member, joined_code in sorted(
            heap, key=lambda entry: (-entry[0], entry[1])
        ):
            record = resolver.resolve(member)
            rows.append(
                {
                    "bioguide_id": member,
                    "name": (
                        record["name"] if record["resolved"] else record["display"]
                    ),
                    "is_former": bool(record["is_former"]),
                    "probability": round(probability, 4),
                    "joined": None if joined_code < 0 else joined_code,
                }
            )
        forecast_rows[bill_key] = rows
    note = (
        "Joining forecast: descriptive cosponsorship model "
        f"({configuration} candidates) over the finance-committee bill panel, "
        f"congresses {congresses[0]}-{congresses[-1]}; probabilities are "
        "associations, not causal effects. Bills outside that panel carry no "
        "forecast."
    )
    receipt = {
        "configuration": configuration,
        "congresses": congresses,
        "rows_read": rows_read,
        "rows_matched": rows_matched,
        "bills_with_forecast": len(forecast_rows),
    }
    return forecast_rows, receipt, note


def _bill_bucket():
    return {
        "filings": set(),
        "dates": [],
        "organizations": collections.Counter(),
        "lobbyists": collections.Counter(),
    }


def _engagement_bucket():
    return {"count": 0, "date": "", "bill": ""}


def _winner(counter, dates=None):
    if not counter:
        return ""
    dates = dates or {}
    weight = max((count, dates.get(key, "")) for key, count in counter.items())
    return min(
        (
            key
            for key, count in counter.items()
            if (count, dates.get(key, "")) == weight
        ),
        key=lambda value: value.casefold(),
    )


def _top_engagement(engagements, organizations):
    if not engagements:
        return {}
    weight = max(
        (entry["count"], entry["date"])
        for entry in engagements.values()
    )
    candidates = [
        (pair, entry)
        for pair, entry in engagements.items()
        if (entry["count"], entry["date"]) == weight
    ]
    pair, entry = min(
        candidates,
        key=lambda item: (
            organizations[item[0]]["name"].casefold(),
            item[0][1],
        ),
    )
    return {
        "organization": organizations[pair]["name"],
        "bill": entry["bill"] or pair[0],
        "bill_key": pair[0],
        "date": entry["date"],
        "count": entry["count"],
    }


def _aggregate_ledger(path, organizations):
    bills = collections.defaultdict(_bill_bucket)
    industries = collections.defaultdict(collections.Counter)
    engagements = collections.defaultdict(
        lambda: collections.defaultdict(_engagement_bucket)
    )
    firms = collections.defaultdict(collections.Counter)
    firm_dates = collections.defaultdict(dict)
    rows_read = 0

    for row_number, row in _iter_csv(
        path, "interaction ledger", LEDGER_REQUIRED
    ):
        rows_read += 1
        bill_key = (row["bill_key"] or "").strip()
        lob_id = (row["lob_id"] or "").strip()
        filing_uuid = (row["filing_uuid"] or "").strip()
        if not bill_key or not lob_id or not filing_uuid:
            raise SidecarError(
                f"interaction ledger row {row_number} has blank identity"
            )
        _validate_bill_key(bill_key, f"interaction ledger row {row_number}")
        organization = organizations.get((bill_key, lob_id))
        if organization is None:
            raise SidecarError(
                f"interaction ledger row {row_number} has no bill-index join for "
                f"{bill_key}/{lob_id}"
            )
        bucket = bills[bill_key]
        filing_date = (row["filing_date"] or "").strip()
        bill_display = (row["bill_display"] or "").strip()
        bucket["filings"].add(filing_uuid)
        if filing_date:
            bucket["dates"].append(filing_date)
        bucket["organizations"][lob_id] += 1

        lobbyists = sorted(
            {
                name.strip()
                for name in (row["lobbyists"] or "").split("|")
                if name.strip()
            },
            key=str.casefold,
        )
        firm = (row["registrant_name"] or "").strip()
        for lobbyist in lobbyists:
            bucket["lobbyists"][lobbyist] += 1
            industries[lobbyist][organization["sector"]] += 1
            engagement = engagements[lobbyist][(bill_key, lob_id)]
            engagement["count"] += 1
            engagement["date"] = max(engagement["date"], filing_date)
            engagement["bill"] = bill_display or engagement["bill"]
            if firm:
                firm_key = (bill_key, lobbyist)
                firms[firm_key][firm] += 1
                firm_dates[firm_key][firm] = max(
                    firm_dates[firm_key].get(firm, ""), filing_date
                )
    if not bills:
        raise SidecarError("interaction ledger is empty")
    return {
        "bills": bills,
        "industries": industries,
        "engagements": engagements,
        "firms": firms,
        "firm_dates": firm_dates,
        "rows": rows_read,
    }


def _organization_rows(bill_key, bucket, organizations):
    rows = [
        {
            "name": organizations[(bill_key, lob_id)]["name"],
            "sector": organizations[(bill_key, lob_id)]["sector"],
            "count": count,
        }
        for lob_id, count in bucket["organizations"].items()
    ]
    rows.sort(key=lambda row: (-row["count"], row["name"].casefold()))
    return rows[:TOP_ORGANIZATIONS]


def _lobbyist_rows(bill_key, bucket, aggregate, organizations):
    ordered = sorted(
        bucket["lobbyists"].items(),
        key=lambda item: (-item[1], item[0].casefold()),
    )[:TOP_LOBBYISTS]
    rows = []
    for lobbyist, count in ordered:
        firm_key = (bill_key, lobbyist)
        rows.append(
            {
                "name": lobbyist,
                "firm": _winner(
                    aggregate["firms"].get(firm_key, {}),
                    aggregate["firm_dates"].get(firm_key, {}),
                ),
                "count": count,
                "top_interaction": _top_engagement(
                    aggregate["engagements"].get(lobbyist, {}), organizations
                ),
                "industry": _winner(
                    aggregate["industries"].get(lobbyist, {})
                ),
            }
        )
    return rows


def _member_rows(bill_key, bill_members, resolver):
    # No per-member number is shipped: LDA filings never name the members
    # lobbied (see MEMBER_BASIS_NOTE), so v1's per-row copy of the bill total
    # was retired.  Sponsor first, then earliest cosponsors.
    ordering = {}
    rows = []
    for source in bill_members.get(bill_key, ()):
        record = resolver.resolve(source["bioguide_id"])
        ordering[source["bioguide_id"]] = (
            0 if source["role"] == "sponsor" else 1,
            source["sponsorship_date"] or "9999-99-99",
            record["name"].casefold(),
            source["bioguide_id"],
        )
        rows.append(
            {
                "bioguide_id": source["bioguide_id"],
                "name": (
                    record["name"] if record["resolved"] else record["display"]
                ),
                "role": source["role"],
                "is_former": bool(record["is_former"]),
            }
        )
    rows.sort(key=lambda row: ordering[row["bioguide_id"]])
    return rows[:TOP_MEMBERS]


def build_payload(data_root, ledger_dir=None, resolver=None, years=YEARS):
    data_root = os.path.abspath(data_root)
    ledger_dir = os.path.abspath(
        ledger_dir or os.path.join(data_root, "interactions")
    )
    sector_roster = _load_sector_roster(
        os.path.join(data_root, "sector_spend", "sector_roster.csv")
    )
    organizations, index_rows = _load_bill_index(
        data_root, sector_roster, years=years
    )
    bill_members = _load_bill_members(
        os.path.join(ledger_dir, "interaction_bill_members.csv")
    )
    if resolver is None:
        try:
            resolver = identity.MemberNameResolver.from_paths()
        except identity.IdentityError as exc:
            raise SidecarError(
                f"member name resolution unavailable: {exc}"
            ) from exc
    aggregate = _aggregate_ledger(
        os.path.join(ledger_dir, "interaction_ledger.csv"), organizations
    )
    forecast_rows, forecast_receipt, forecast_note = _load_joining_forecast(
        os.path.join(data_root, "cosponsor_sc"),
        set(aggregate["bills"]),
        resolver,
    )

    bills = {}
    for bill_key in sorted(aggregate["bills"]):
        bucket = aggregate["bills"][bill_key]
        interaction_count = sum(bucket["organizations"].values())
        dates = bucket["dates"]
        bills[bill_key] = {
            "organizations": _organization_rows(
                bill_key, bucket, organizations
            ),
            "top_lobbyists": _lobbyist_rows(
                bill_key, bucket, aggregate, organizations
            ),
            "members_lobbied": _member_rows(
                bill_key, bill_members, resolver
            ),
            "joining_forecast": forecast_rows.get(bill_key, []),
            "totals": {
                "filings": len(bucket["filings"]),
                "orgs": len(bucket["organizations"]),
                "interactions": interaction_count,
                "first_filing_date": min(dates) if dates else "",
                "last_filing_date": max(dates) if dates else "",
            },
        }
    return {
        "schema": SCHEMA,
        "coverage_note": COVERAGE_NOTE,
        "member_basis_note": MEMBER_BASIS_NOTE,
        "forecast_note": forecast_note,
        "bills": bills,
        "receipt": {
            "ledger_rows": aggregate["rows"],
            "bill_index_rows": index_rows,
            "bills": len(bills),
            "sector_roster_ambiguous_ids": sector_roster["ambiguous_ids"],
            "sector_roster_ambiguous_names": sector_roster["ambiguous_names"],
            "forecast": forecast_receipt,
            "caps": {
                "organizations": TOP_ORGANIZATIONS,
                "top_lobbyists": TOP_LOBBYISTS,
                "members_lobbied": TOP_MEMBERS,
                "joining_forecast": TOP_FORECAST,
            },
        },
    }


def _write_json_atomic(payload, path):
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
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


def _parser():
    parser = argparse.ArgumentParser(
        description="Build bill-level lobbying summaries for the bill repository"
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--ledger-dir", default=None)
    parser.add_argument("--out", default=DEFAULT_OUTPUT)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    data_root = args.data_root or _default_data_root()
    payload = build_payload(data_root, ledger_dir=args.ledger_dir)
    size = _write_json_atomic(payload, args.out)
    print(
        "bill lobbying sidecar: %d bills | %d bytes -> %s"
        % (len(payload["bills"]), size, os.path.abspath(args.out))
    )
    return payload


if __name__ == "__main__":
    main()
