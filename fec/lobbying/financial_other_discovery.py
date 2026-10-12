# -*- coding: utf-8 -*-
"""Discover high-spend finance-coded LDA filers outside every roster.

Paul's 2026-08-05 directive ("Build the financial_other discovery channel
too"): the generic financial_other population is frozen at its LobbyView
vintage, so a sweep-native finance organization that is not credit-union,
bank, crypto, or fintech by name has no path into the full-scope universe.
This tool is that path's REVIEW channel.  It scans the sweep corpus for
clients whose lobbying activities carry finance issue codes, drops everything
already in the full-scope universe, already sector-classified, or
deliberately excluded by the roster guards, and writes a spend-ranked review
sheet.

DISCOVERY ONLY: nothing is auto-included.  Every addition to a roster or to
the universe remains Paul's classification decision.

Income and expenses are reported as SEPARATE columns (fees paid to outside
registrants vs self-reported in-house spending) and are never summed; the
ranking key is the greater of the two, which orders the sheet without
publishing a combined figure.  Amendments are approximated by keeping only
the latest-posted filing per (client, registrant, year, period).
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))  # noqa: E501,E702

import argparse
import collections
import csv
import json
import os
import sys
from datetime import datetime, timezone

from lobbying import sector_spend

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


FINANCE_ISSUE_CODES = {
    "BAN": "Banking",
    "FIN": "Financial Institutions/Investments/Securities",
    "MON": "Minting/Money/Gold Standard",
}
SPEND_FLOOR = 50000.0
SAMPLE_DESCRIPTION_CHARS = 160

CSV_FIELDS = (
    "rank",
    "client_names",
    "client_ids",
    "first_year",
    "last_year",
    "filings_kept",
    "finance_activities",
    "finance_activity_share",
    "income_total",
    "expenses_total",
    "ranking_spend",
    "finance_codes",
    "registrants",
    "sample_description",
)


class DiscoveryError(RuntimeError):
    """An input contract failed; do not publish a partial sheet."""


def _registrant_key(filing):
    registrant = filing.get("registrant") or {}
    identifier = registrant.get("id")
    if identifier is not None and str(identifier).strip():
        return "id:" + str(identifier).strip()
    name = str(registrant.get("name") or "").strip()
    return "name:" + sector_spend.normalize_name(name)


def _finance_activities(filing, codes):
    matched = []
    for activity in filing.get("lobbying_activities") or ():
        code = str((activity or {}).get("general_issue_code") or "").strip()
        if code in codes:
            matched.append(activity)
    return matched


def _money(filing, field):
    value = filing.get(field)
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DiscoveryError(
            f"filing {filing.get('filing_uuid')} has a bad {field}: {value!r}"
        ) from exc


def discover(
    filings,
    universe_ids,
    universe_names,
    sectors=None,
    codes=None,
    floor=SPEND_FLOOR,
):
    """Pure discovery over an iterable of filings; returns (rows, receipt).

    ``universe_ids``/``universe_names`` describe the current full-scope org
    universe (lob_id_or_client_id values and normalize_name'd client names).
    """
    sectors = sector_spend.DEFAULT_SECTORS if sectors is None else sectors
    codes = frozenset(FINANCE_ISSUE_CODES) if codes is None else frozenset(codes)
    kept = {}
    counts = collections.Counter()
    for filing in filings:
        counts["filings_scanned"] += 1
        client = filing.get("client") or {}
        raw_name = str(client.get("name") or "").strip()
        if not raw_name:
            continue
        matched = _finance_activities(filing, codes)
        if not matched:
            continue
        counts["finance_coded_filings"] += 1
        client_id = sector_spend._client_id(filing)
        normalized = sector_spend.normalize_name(raw_name)
        if client_id in universe_ids or normalized in universe_names:
            counts["already_in_universe"] += 1
            continue
        disposition = sector_spend.scope_sector_disposition(filing, sectors)
        if disposition == "classified":
            counts["already_sector_classified"] += 1
            continue
        if disposition == "excluded":
            counts["deliberately_excluded"] += 1
            continue
        supersession_key = (
            client_id,
            _registrant_key(filing),
            int(filing.get("filing_year")),
            str(filing.get("filing_period") or "").strip(),
        )
        posted = str(filing.get("dt_posted") or "")
        prior = kept.get(supersession_key)
        if prior is not None and (posted, str(filing.get("filing_uuid"))) <= prior[0]:
            counts["superseded_dropped"] += 1
            continue
        if prior is not None:
            counts["superseded_dropped"] += 1
        kept[supersession_key] = (
            (posted, str(filing.get("filing_uuid"))),
            filing,
            matched,
        )

    candidates = {}
    for (client_id, _registrant, year, _period), (_stamp, filing, matched) in kept.items():
        row = candidates.setdefault(
            client_id,
            {
                "client_id": client_id,
                "names": set(),
                "years": set(),
                "filings_kept": 0,
                "finance_activities": 0,
                "activities_total": 0,
                "income_total": 0.0,
                "expenses_total": 0.0,
                "codes": collections.Counter(),
                "registrants": set(),
                "sample_description": "",
            },
        )
        client = filing.get("client") or {}
        row["names"].add(str(client.get("name") or "").strip())
        row["years"].add(year)
        row["filings_kept"] += 1
        row["finance_activities"] += len(matched)
        row["activities_total"] += len(filing.get("lobbying_activities") or ())
        row["income_total"] += _money(filing, "income")
        row["expenses_total"] += _money(filing, "expenses")
        for activity in matched:
            code = str(activity.get("general_issue_code") or "").strip()
            row["codes"][code] += 1
            if not row["sample_description"]:
                description = " ".join(
                    str(activity.get("description") or "").split()
                )
                if description:
                    row["sample_description"] = description[
                        :SAMPLE_DESCRIPTION_CHARS
                    ]
        registrant = (filing.get("registrant") or {}).get("name")
        if registrant and str(registrant).strip():
            row["registrants"].add(str(registrant).strip())

    rows = []
    for row in candidates.values():
        ranking_spend = max(row["income_total"], row["expenses_total"])
        if ranking_spend < floor:
            counts["below_floor"] += 1
            continue
        rows.append((ranking_spend, row))
    rows.sort(key=lambda item: (-item[0], item[1]["client_id"]))

    sheet = []
    for position, (ranking_spend, row) in enumerate(rows, start=1):
        sheet.append(
            {
                "rank": position,
                "client_names": " | ".join(sorted(row["names"])),
                "client_ids": row["client_id"],
                "first_year": min(row["years"]),
                "last_year": max(row["years"]),
                "filings_kept": row["filings_kept"],
                "finance_activities": row["finance_activities"],
                # Separates finance ORGS (share near 1.0) from diversified
                # giants whose filings carry one banking activity among many.
                "finance_activity_share": round(
                    row["finance_activities"] / row["activities_total"], 3
                )
                if row["activities_total"]
                else 0.0,
                "income_total": round(row["income_total"], 2),
                "expenses_total": round(row["expenses_total"], 2),
                "ranking_spend": round(ranking_spend, 2),
                "finance_codes": "|".join(
                    f"{code}:{count}"
                    for code, count in sorted(row["codes"].items())
                ),
                "registrants": " | ".join(sorted(row["registrants"])[:3]),
                "sample_description": row["sample_description"],
            }
        )
    receipt = {
        "counts": dict(counts),
        "candidates_listed": len(sheet),
        "finance_issue_codes": dict(FINANCE_ISSUE_CODES),
        "spend_floor": floor,
        "ranking_rule": "max(income_total, expenses_total); series never summed",
        "supersession_rule": (
            "latest dt_posted per (client, registrant, year, period); "
            "approximation of amendment supersession"
        ),
        "discovery_only": True,
    }
    return sheet, receipt


def _load_universe(roster_path):
    ids = set()
    names = set()
    try:
        handle = open(roster_path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise DiscoveryError(
            f"cannot read full-scope org roster {roster_path}: {exc}"
        ) from exc
    with handle:
        reader = csv.DictReader(handle)
        required = {"lob_id_or_client_id", "client_name"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise DiscoveryError(
                "full-scope org roster must contain lob_id_or_client_id and client_name"
            )
        for row in reader:
            identifier = (row.get("lob_id_or_client_id") or "").strip()
            if identifier:
                ids.add(identifier)
            name = (row.get("client_name") or "").strip()
            if name:
                names.add(sector_spend.normalize_name(name))
    if not ids:
        raise DiscoveryError("full-scope org roster yielded no organizations")
    return ids, names


def _write_outputs(out_dir, sheet, receipt):
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "candidates.csv")
    receipt_path = os.path.join(out_dir, "receipt.json")
    temporary = csv_path + f".tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in sheet:
            writer.writerow(row)
    os.replace(temporary, csv_path)
    receipt = dict(receipt)
    receipt["generated_utc"] = datetime.now(timezone.utc).isoformat()
    temporary = receipt_path + f".tmp.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, receipt_path)
    return csv_path, receipt_path


def _parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--years", type=sector_spend.parse_years, default=sector_spend.DEFAULT_YEARS)
    parser.add_argument("--floor", type=float, default=SPEND_FLOOR)
    parser.add_argument("--out-dir", default=None)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    derived = os.path.join(
        os.environ["LOCALAPPDATA"], "Melange", "lda_rebuild", "derived"
    )
    out_dir = args.out_dir or os.path.join(derived, "financial_other_discovery")
    universe_ids, universe_names = _load_universe(
        os.path.join(derived, "fullscope_org_roster.csv")
    )
    sweep_root = sector_spend.default_sweep_root()
    sheet, receipt = discover(
        sector_spend.iter_corpus_filings(sweep_root, args.years),
        universe_ids,
        universe_names,
        floor=args.floor,
    )
    receipt["years"] = sorted(int(year) for year in args.years)
    receipt["universe_orgs"] = len(universe_ids)
    csv_path, receipt_path = _write_outputs(out_dir, sheet, receipt)
    counts = receipt["counts"]
    print(
        "financial_other discovery: %d candidates >= $%s | scanned %d filings, "
        "%d finance-coded | already-in-universe %d, sector-classified %d, "
        "deliberately-excluded %d, below-floor %d"
        % (
            receipt["candidates_listed"],
            f"{int(receipt['spend_floor']):,}",
            counts.get("filings_scanned", 0),
            counts.get("finance_coded_filings", 0),
            counts.get("already_in_universe", 0),
            counts.get("already_sector_classified", 0),
            counts.get("deliberately_excluded", 0),
            counts.get("below_floor", 0),
        )
    )
    print(f"  review sheet: {csv_path}")
    print(f"  receipt:      {receipt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
