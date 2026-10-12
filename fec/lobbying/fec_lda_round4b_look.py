# -*- coding: utf-8 -*-
"""Round 4B FEC congestion x classified bill-position corroboration look.

``--battery`` constructs synthetic Y from real X margins and controls only.
``--look`` reruns that battery before it loads the classifier output.  Neither
mode modifies a preregistration or any source artifact.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT

import argparse
import csv
import datetime
import json
import math
import os
import re
import sys

from lobbying import build_network_2021_rebuild as builder
from lobbying import fec_lda_bridge3_look as bridge3


sys.stdout.reconfigure(encoding="utf-8", errors="replace")


STRATA = ("credit_union", "bank")
WINDOW_YEARS = (2023, 2024)
WINDOW_CYCLE = "2023-2024"
DIRECTIONAL_LABELS = frozenset({"Support", "Oppose"})
POSITION_LABELS = frozenset({"Support", "Oppose", "Amend", "Monitor", "Mention"})
POSITION_FIELDS = frozenset(
    {
        "stratum",
        "lob_id",
        "report_uuid",
        "filing_year",
        "bill_key",
        "our_position",
    }
)
CONGESTION_SHA256 = "DDC9D746600DBDFC60B76C54537662E19C758CBFB9D7A478A71B7BF90CD727A6"
LEGISLATORS_SHA256 = bridge3.LEGISLATORS_SHA256
COMMITTEE_SHA256 = "D70F765B3A13E262D21B0C3C45DCD00687A8B13537EA92FF20929CA16116C99F"
COMMITTEE_FILENAME = (
    "committee-membership-118-"
    "c3843a3e83e5b00e33f4140a977af546514396e2.yaml"
)
TOTALS_PINS = {
    2023: {
        "sha256": "BC34C92CA4FEBD75194ACC0FFC8310E27D2C56F93B077759E02126B060C545AD",
        "rows": 814,
    },
    2024: {
        "sha256": "4566BF9A87F61FC3046988904D5988B20BE3A8ECE6583C442F902B7555705CB9",
        "rows": 811,
    },
}
TOTALS_HEADER = ["bioguide", "total_n_bills"]
CONTROL_NAMES = bridge3.CONTROL_NAMES
PERMUTATIONS = bridge3.PERMUTATIONS
PERMUTATION_SEED = bridge3.PERMUTATION_SEED
VOID_EXIT = 5

DEFAULT_CONGESTION = (
    r"C:\Users\pgoff\OneDrive\Desktop\Melange Systems\data\engine\congestion.json"
)
DEFAULT_LEGISLATORS = os.path.join(MB_ROOT, "fec", "legislators.csv")


# Round 3 statistical core: imported, not re-derived.  These are the original
# fractional ranking, integer-exact member shuffle, pivoted-QR/SVD projections,
# condition-number calculation, and <=1e-9/exact-p agreement check.
IngestError = bridge3.IngestError
DefectError = bridge3.DefectError
fractional_ranks = bridge3.fractional_ranks
volume_within_party_fractional = bridge3.volume_within_party_fractional
statistics_implementation_one = bridge3.statistics_implementation_one
statistics_implementation_two = bridge3.statistics_implementation_two
_assert_agreement = bridge3._assert_agreement


def _local_defaults():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return {
            "positions": None,
            "committee": None,
            "totals_2023": None,
            "totals_2024": None,
        }
    paths = builder.default_paths(local_app_data)
    return {
        "positions": os.path.join(paths["derived_dir"], "bill_positions.jsonl"),
        "committee": os.path.join(
            paths["raw_dir"], "committee_snapshots", COMMITTEE_FILENAME
        ),
        "totals_2023": os.path.join(paths["derived_dir"], "totals_2023.csv"),
        "totals_2024": os.path.join(paths["derived_dir"], "totals_2024.csv"),
    }


def _require_path(path, option):
    if not path:
        raise IngestError(
            f"LOOK REFUSED (not consumed): {option} is required when LOCALAPPDATA is unset"
        )
    return path


def _verify_pin(path, expected, label):
    if not os.path.isfile(path):
        raise IngestError(
            f"LOOK REFUSED (not consumed): {label} absent at {path}"
        )
    actual = bridge3._sha256(path)
    if actual != expected:
        raise IngestError(
            f"LOOK REFUSED (not consumed): SHA-256 drift for {label} "
            f"({actual} != {expected})"
        )


def _number(value, source, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IngestError(f"{source} field {field} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise IngestError(f"{source} field {field} is negative or non-finite")
    return result


def _x_by_stratum_from_congestion_rows(rows):
    """Validate the pinned congestion row schema and map cu/banks by bioguide."""
    if not isinstance(rows, list):
        raise IngestError("congestion.json top level must be a list")
    output = {stratum: {} for stratum in STRATA}
    names = set()
    for index, row in enumerate(rows, start=1):
        source = f"congestion.json row {index}"
        if not isinstance(row, dict):
            raise IngestError(source + " is not an object")
        missing = {"member", "bioguide", "cu", "banks"} - set(row)
        if missing:
            raise IngestError(
                source + " missing field(s): " + ",".join(sorted(missing))
            )
        member_name = row["member"]
        bioguide = row["bioguide"]
        if not isinstance(member_name, str) or not member_name.strip():
            raise IngestError(source + " field member is blank or not a string")
        if member_name in names:
            raise IngestError(source + " duplicates member key " + member_name)
        names.add(member_name)
        if not isinstance(bioguide, str) or re.fullmatch(
            r"[A-Z][0-9]{6}", bioguide
        ) is None:
            raise IngestError(source + " field bioguide is invalid")
        if any(bioguide in output[stratum] for stratum in STRATA):
            raise IngestError(source + " duplicates bioguide " + bioguide)
        output["credit_union"][bioguide] = _number(row["cu"], source, "cu")
        output["bank"][bioguide] = _number(row["banks"], source, "banks")
    if not rows:
        raise IngestError("congestion.json has no members")
    return output


def _load_congestion(path):
    # Hash before parsing so no unpinned member or money field can enter a run.
    _verify_pin(path, CONGESTION_SHA256, "data/engine/congestion.json")
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            rows = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError("congestion.json unreadable: " + str(exc)) from exc
    return _x_by_stratum_from_congestion_rows(rows)


def _canonical_bill_key(value, source):
    if not isinstance(value, dict) or set(value) != {
        "type",
        "number",
        "congress",
    }:
        raise IngestError(source + " has malformed bill_key")
    type_token = value["type"]
    number = value["number"]
    congress = value["congress"]
    if not isinstance(type_token, str) or not type_token:
        raise IngestError(source + " has invalid bill_key.type")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise IngestError(source + " has invalid bill_key.number")
    if (
        isinstance(congress, bool)
        or not isinstance(congress, int)
        or congress not in builder.CONGRESS_FIRST_YEAR
    ):
        raise IngestError(source + " has unsupported bill_key.congress")
    allowed_types = builder.SUPPORTED_BILL_TYPES_BY_CONGRESS[congress]
    if type_token not in allowed_types:
        raise IngestError(source + " has unsupported bill_key.type")
    return (type_token, number, congress)


def _load_real_y(path):
    """Load the classifier writer's exact association-result field names."""
    try:
        handle = open(path, "r", encoding="utf-8-sig")
    except OSError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): positions JSONL unreadable: " + str(exc)
        ) from exc
    rows = []
    with handle:
        for line_number, line in enumerate(handle, start=1):
            source = f"positions JSONL line {line_number}"
            if not line.strip():
                raise IngestError(source + " is blank")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IngestError(source + " is malformed: " + str(exc)) from exc
            if not isinstance(row, dict):
                raise IngestError(source + " is not an object")
            missing = POSITION_FIELDS - set(row)
            if missing:
                raise IngestError(
                    source + " missing classifier field(s): " + ",".join(sorted(missing))
                )
            stratum = row["stratum"]
            lob_id = row["lob_id"]
            report_uuid = row["report_uuid"]
            filing_year = row["filing_year"]
            label = row["our_position"]
            if stratum not in STRATA:
                raise IngestError(source + " has invalid stratum")
            if not isinstance(lob_id, str) or not lob_id.strip():
                raise IngestError(source + " has invalid lob_id")
            if not isinstance(report_uuid, str) or builder.UUID_RE.fullmatch(
                report_uuid
            ) is None:
                raise IngestError(source + " has invalid report_uuid")
            if (
                isinstance(filing_year, bool)
                or not isinstance(filing_year, int)
                or filing_year not in WINDOW_YEARS
            ):
                raise IngestError(source + " has filing_year outside 2023+2024")
            if label not in POSITION_LABELS:
                raise IngestError(source + " has invalid or absent our_position")
            bill_key = _canonical_bill_key(row["bill_key"], source)
            rows.append(
                {
                    "stratum": stratum,
                    "lob_id": lob_id.strip(),
                    "report_uuid": report_uuid,
                    "filing_year": filing_year,
                    "bill_key": bill_key,
                    "our_position": label,
                }
            )
    if not rows:
        raise IngestError("LOOK REFUSED (not consumed): positions JSONL is empty")
    return rows


def _bill_key_tuple(value, source="position row"):
    if isinstance(value, tuple) and len(value) == 3:
        return value
    return _canonical_bill_key(value, source)


def _load_bill_people_map(position_rows):
    directional_keys = {
        _bill_key_tuple(row["bill_key"])
        for row in position_rows
        if row["our_position"] in DIRECTIONAL_LABELS
    }
    congresses = sorted({key[2] for key in directional_keys})
    try:
        paths = builder.default_paths(os.environ.get("LOCALAPPDATA"))
        people = {}
        billstatus_keys = set()
        for congress in congresses:
            loaded = builder.load_sponsor_map(
                builder.billstatus_dir_for_congress(paths, congress),
                congress=congress,
            )
            people.update(loaded[0])
            billstatus_keys.update(loaded[1])
    except builder.IngestError as exc:
        raise IngestError(
            "LOOK REFUSED (not consumed): BILLSTATUS people-map load failed: "
            + str(exc)
        ) from exc
    missing = sorted(directional_keys - billstatus_keys)
    if missing:
        raise IngestError(
            "LOOK REFUSED (not consumed): directional bill absent from BILLSTATUS: "
            + repr(missing[0])
        )
    return {key: set(people.get(key, set())) for key in directional_keys}


def _construct_y(position_rows, bill_people_map, members):
    """Construct distinct positioned-bill counts while retaining every member."""
    members = set(members)
    bills = {stratum: set() for stratum in STRATA}
    support = {stratum: set() for stratum in STRATA}
    oppose = {stratum: set() for stratum in STRATA}
    associations = {stratum: set() for stratum in STRATA}
    by_association = {}
    for row_number, row in enumerate(position_rows, start=1):
        stratum = row.get("stratum")
        label = row.get("our_position")
        if stratum not in STRATA:
            raise IngestError(f"position row {row_number} has invalid stratum")
        if label not in POSITION_LABELS:
            raise IngestError(f"position row {row_number} has invalid our_position")
        if label not in DIRECTIONAL_LABELS:
            continue
        lob_id = row.get("lob_id")
        if not isinstance(lob_id, str) or not lob_id:
            raise IngestError(f"position row {row_number} has invalid lob_id")
        key = _bill_key_tuple(row.get("bill_key"), f"position row {row_number}")
        if key not in bill_people_map:
            raise IngestError(
                f"position row {row_number} bill is absent from loaded people map"
            )
        bills[stratum].add(key)
        (support if label == "Support" else oppose)[stratum].add(key)
        associations[stratum].add(lob_id)
        by_association.setdefault((stratum, lob_id), set()).add(key)

    def count_member_bills(selected):
        result = {
            stratum: {member: 0 for member in sorted(members)}
            for stratum in STRATA
        }
        for stratum in STRATA:
            for key in selected[stratum]:
                for member in set(bill_people_map[key]) & members:
                    result[stratum][member] += 1
        return result

    return {
        "all": count_member_bills(bills),
        "support": count_member_bills(support),
        "oppose": count_member_bills(oppose),
        "bills_by_stratum": bills,
        "support_bills": support,
        "oppose_bills": oppose,
        "associations_by_stratum": associations,
        "positioned_bill_counts": {
            key: len(value) for key, value in by_association.items()
        },
    }


def _load_totals(paths):
    totals = {}
    for year in WINDOW_YEARS:
        path = paths[year]
        pin = TOTALS_PINS[year]
        _verify_pin(path, pin["sha256"], f"totals_{year}.csv")
        seen = set()
        rows_read = 0
        try:
            handle = open(path, "r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise IngestError(f"totals_{year}.csv unreadable: {exc}") from exc
        with handle:
            reader = csv.reader(handle)
            if next(reader, None) != TOTALS_HEADER:
                raise IngestError(f"totals_{year}.csv header mismatch")
            for row_number, source in enumerate(reader, start=2):
                rows_read += 1
                if len(source) != 2:
                    raise IngestError(
                        f"malformed totals_{year}.csv row {row_number}"
                    )
                bioguide = source[0].strip()
                try:
                    value = int(source[1])
                except ValueError as exc:
                    raise IngestError(
                        f"invalid totals_{year}.csv row {row_number}"
                    ) from exc
                if (
                    re.fullmatch(r"[A-Z][0-9]{6}", bioguide) is None
                    or bioguide in seen
                    or value < 0
                ):
                    raise IngestError(
                        f"duplicate/invalid totals_{year}.csv row {row_number}"
                    )
                seen.add(bioguide)
                totals[bioguide] = totals.get(bioguide, 0) + value
        if rows_read != pin["rows"]:
            raise IngestError(
                f"totals_{year}.csv row count drift ({rows_read} != {pin['rows']})"
            )
    return totals


def _load_margins(args):
    congestion = _load_congestion(args.congestion)
    _verify_pin(args.legislators, LEGISLATORS_SHA256, "legislators.csv")
    _verify_pin(args.committee_yaml, COMMITTEE_SHA256, COMMITTEE_FILENAME)
    legislators = bridge3._load_legislators(args.legislators)
    committee = bridge3._read_committee_snapshot(args.committee_yaml)
    totals_paths = {2023: args.totals_2023, 2024: args.totals_2024}
    totals = _load_totals(totals_paths)
    # Detect a source changing after its initial hash and read.
    _verify_pin(args.congestion, CONGESTION_SHA256, "data/engine/congestion.json")
    _verify_pin(args.legislators, LEGISLATORS_SHA256, "legislators.csv")
    _verify_pin(args.committee_yaml, COMMITTEE_SHA256, COMMITTEE_FILENAME)
    for year in WINDOW_YEARS:
        _verify_pin(
            totals_paths[year], TOTALS_PINS[year]["sha256"], f"totals_{year}.csv"
        )
    return {
        "x": congestion,
        "legislators": legislators,
        "committee": committee,
        "totals": totals,
    }


def _build_records(margins, strata, y_by_stratum=None):
    records = []
    for stratum in strata:
        for bioguide in sorted(margins["x"][stratum]):
            x_value = margins["x"][stratum][bioguide]
            if x_value <= 0.0:
                continue
            legislator = margins["legislators"].get(bioguide)
            if legislator is None:
                raise IngestError(
                    "LOOK REFUSED (not consumed): X>0 congestion member absent "
                    "from legislators.csv: " + bioguide
                )
            records.append(
                {
                    # Round 3 uses (industry, cycle) as its stratum key.  A
                    # single fixed cycle makes each Round 4B industry value
                    # exactly one member-shuffle stratum.
                    "industry": stratum,
                    "cycle": WINDOW_CYCLE,
                    "member": bioguide,
                    "x": x_value,
                    "y": float(
                        0.0
                        if y_by_stratum is None
                        else y_by_stratum[stratum].get(bioguide, 0)
                    ),
                    "party_name": legislator["party_name"],
                    "party": legislator["party"],
                    "chamber": legislator["chamber"],
                    "committee": 1.0 if bioguide in margins["committee"] else 0.0,
                    "volume_raw": float(margins["totals"].get(bioguide, 0)),
                }
            )
    volumes = volume_within_party_fractional(records)
    for record, volume in zip(records, volumes):
        record["volume"] = volume
    return records


def _entry_status(y_data, x_by_stratum):
    status = {}
    for stratum in STRATA:
        association_count = len(y_data["associations_by_stratum"][stratum])
        x_positive = sum(
            value > 0.0 for value in x_by_stratum[stratum].values()
        )
        entered = association_count >= 2 and x_positive >= 10
        reasons = []
        if association_count < 2:
            reasons.append("qualifying_directional_associations<2")
        if x_positive < 10:
            reasons.append("members_with_X>0<10")
        status[stratum] = {
            "entered": entered,
            "active_associations": association_count,
            "x_positive_members": x_positive,
            "directional_bills": len(y_data["bills_by_stratum"][stratum]),
            "reason": ", ".join(reasons),
        }
    return status


def _coverage_status(entry_status):
    entering = [stratum for stratum in STRATA if entry_status[stratum]["entered"]]
    pooled = sum(entry_status[stratum]["x_positive_members"] for stratum in entering)
    return {
        "void": pooled < 80 or len(entering) < 2,
        "pooled_members": pooled,
        "entering_strata": entering,
    }


def _margin_coverage(margins):
    status = {
        stratum: {
            "entered": sum(value > 0.0 for value in margins["x"][stratum].values())
            >= 10,
            "x_positive_members": sum(
                value > 0.0 for value in margins["x"][stratum].values()
            ),
        }
        for stratum in STRATA
    }
    return _coverage_status(status)


def _run_battery(base_records):
    # STRUCTURAL ASSERTION: this battery accepts completed real-X/control
    # records and has no positions path.  It never calls _load_real_y or
    # aggregates a real bill.  Every Y below is synthesized by Round 3's
    # locked lambda-bisection harness from these margins and fixed seeds.
    return bridge3._run_battery(base_records)


def _format_battery(battery):
    lines = [
        "## BATTERY-R4B",
        "- battery " + battery["status"],
        f"- planted lambda: {battery['lambda']:.12f}",
    ]
    specifications = (
        ("planted", "PLANTED seed 424242"),
        ("null", "MEMBER-SHUFFLE NULL seed 535353"),
        ("confound_only", "CONFOUND-ONLY seed 646464"),
        ("true_confound", "TRUE+CONFOUND seed 757575"),
    )
    for key, label in specifications:
        first = battery[key]["implementation_one"]
        second = battery[key]["implementation_two"]
        suffix = ""
        if key == "confound_only" and battery["confound_vacuous"]:
            suffix = " (VACUOUS-PASS warning: raw pooled rho < +0.20)"
        lines.append(
            f"- {label}: check={'PASS' if battery['checks'][key] else 'FAILURE'}{suffix}; "
            f"impl1 rho={first['rho']:+.12f}, p={first['p']:.12f}, "
            f"partial={first['partial']:+.12f}; impl2 rho={second['rho']:+.12f}, "
            f"p={second['p']:.12f}, partial={second['partial']:+.12f}"
        )
    lines.append(
        "- real-Y isolation: PASS; battery is structurally unable to load positions"
    )
    lines.append(
        "- look eligibility: "
        + ("battery admitted" if battery["status"] == "PASS" else "REFUSED")
    )
    return "\n".join(lines)


def _statistics_pair(records, label, full=True):
    first = statistics_implementation_one(records, full=full)
    second = statistics_implementation_two(records, full=full)
    _assert_agreement(first, second, label)
    return first, second


def _bars(result):
    return {
        "bar1": result["rho"] >= 0.20 and result["p"] < 0.05,
        "bar2": result["partial"] >= 0.10,
        "strong": (
            result["rho"] >= 0.40
            and result["p"] < 0.01
            and result["partial"] >= 0.20
        ),
    }


def _tier(first, second):
    first_bars = _bars(first)
    second_bars = _bars(second)
    fragile = first_bars != second_bars
    bars = {
        key: first_bars[key] and second_bars[key]
        for key in first_bars
    }
    if fragile:
        tier = "UNCORROBORATED"
    elif bars["strong"]:
        tier = "STRONGLY CORROBORATED"
    elif bars["bar1"] and bars["bar2"]:
        tier = "CORROBORATED"
    else:
        tier = "UNCORROBORATED"
    return tier, bars, fragile


def _format_result(
    primary,
    support_only,
    oppose_only,
    battery,
    entry_status,
    y_data,
):
    first, second = primary
    support_first, support_second = support_only
    oppose_first, oppose_second = oppose_only
    tier, bars, fragile = _tier(first, second)
    lines = [
        "## RESULT-R4B",
        "- date: " + datetime.date.today().isoformat(),
        f"- pooled members: {first['n']}",
        "- stratum entry status:",
    ]
    for stratum in STRATA:
        item = entry_status[stratum]
        if item["entered"]:
            key = stratum + "|" + WINDOW_CYCLE
            lines.append(
                f"  - {stratum}: entered; members={first['per_stratum'][key]['n']}; "
                f"qualifying_associations={item['active_associations']}; "
                f"directional_bills={item['directional_bills']}"
            )
        else:
            lines.append(
                f"  - {stratum}: coverage-limited; {item['reason']}; "
                f"qualifying_associations={item['active_associations']}; "
                f"members_X_positive={item['x_positive_members']}"
            )
    lines.extend(
        [
            f"- pooled fractional-rank Spearman rho: {first['rho']:+.12f}",
            f"- exact member-shuffle permutation p: {first['p']:.12f} "
            f"(K={PERMUTATIONS}, seed={PERMUTATION_SEED})",
            f"- four-control partial: {first['partial']:+.12f}",
            f"- design-matrix condition number: {first['condition_number']:.12g}",
            "- bar1 (rho >= +0.20 and p < .05): "
            + ("PASS" if bars["bar1"] else "MISS"),
            "- bar2 (four-control partial >= +0.10): "
            + ("PASS" if bars["bar2"] else "MISS"),
            "- STRONG (rho >= +0.40, p < .01, partial >= +0.20): "
            + ("PASS" if bars["strong"] else "MISS"),
            "- tier: " + tier,
            "- implementation 1: "
            f"rho={first['rho']:+.12f}, p={first['p']:.12f}, "
            f"partial={first['partial']:+.12f}",
            "- implementation 2: "
            f"rho={second['rho']:+.12f}, p={second['p']:.12f}, "
            f"partial={second['partial']:+.12f}",
            "- battery summary: "
            + battery["status"]
            + f"; lambda={battery['lambda']:.12f}",
            "- FRAGILE=MISS rule: statistics (date excluded) must reproduce "
            "exactly on verbatim rerun"
            + ("; current dual-threshold result is FRAGILE=MISS" if fragile else ""),
            "- secondaries (FIREWALLED; ground nothing):",
            "  - Support-only Y: "
            f"impl1 rho={support_first['rho']:+.12f}, p={support_first['p']:.12f}; "
            f"impl2 rho={support_second['rho']:+.12f}, p={support_second['p']:.12f}",
            "  - Oppose-only Y: "
            f"impl1 rho={oppose_first['rho']:+.12f}, p={oppose_first['p']:.12f}; "
            f"impl2 rho={oppose_second['rho']:+.12f}, p={oppose_second['p']:.12f}",
        ]
    )
    for stratum in STRATA:
        if not entry_status[stratum]["entered"]:
            lines.append(f"  - {stratum}: NOT ENTERING")
            continue
        key = stratum + "|" + WINDOW_CYCLE
        one = first["per_stratum"][key]
        two = second["per_stratum"][key]
        lines.append(
            f"  - {stratum}: impl1 rho={one['rho']:+.12f}, p={one['p']:.12f}; "
            f"impl2 rho={two['rho']:+.12f}, p={two['p']:.12f}"
        )
    lines.append(
        "  - three-control partial (party, chamber, volume; committee excluded): "
        f"impl1={first['three_control_partial']:+.12f}; "
        f"impl2={second['three_control_partial']:+.12f}"
    )
    lines.append("  - positioned-bill count per association:")
    for (stratum, lob_id), count in sorted(y_data["positioned_bill_counts"].items()):
        lines.append(f"    - {stratum} | {lob_id}: {count}")
    return "\n".join(lines)


def _void_message(coverage, label="coverage"):
    return (
        "VOID-FOR-COVERAGE: "
        + label
        + "; pooled members="
        + str(coverage["pooled_members"])
        + ", entering strata="
        + str(len(coverage["entering_strata"]))
        + "; look NOT consumed"
    )


def _emit(text, path):
    if path is None:
        print(text)
        return
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    os.makedirs(parent, exist_ok=True)
    temporary = absolute + ".tmp." + str(os.getpid())
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip() + "\n")
        os.replace(temporary, absolute)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _run(args):
    margins = _load_margins(args)
    margin_coverage = _margin_coverage(margins)
    if margin_coverage["void"]:
        _emit(_void_message(margin_coverage, "real-X margin coverage"), args.out)
        return VOID_EXIT
    margin_records = _build_records(
        margins, margin_coverage["entering_strata"], y_by_stratum=None
    )
    battery = _run_battery(margin_records)
    if args.battery:
        _emit(_format_battery(battery), args.out)
        return 0 if battery["status"] == "PASS" else 4
    if battery["status"] != "PASS":
        _emit(_format_battery(battery), args.out)
        return 4

    # The real-Y loader is deliberately below the complete battery gate.
    position_rows = _load_real_y(args.positions)
    bill_people_map = _load_bill_people_map(position_rows)
    members = set().union(*(set(margins["x"][key]) for key in STRATA))
    y_data = _construct_y(position_rows, bill_people_map, members)
    entry_status = _entry_status(y_data, margins["x"])
    coverage = _coverage_status(entry_status)
    if coverage["void"]:
        _emit(_void_message(coverage), args.out)
        return VOID_EXIT

    entering = coverage["entering_strata"]
    primary_records = _build_records(margins, entering, y_data["all"])
    primary = _statistics_pair(primary_records, "real_look", full=True)
    support_records = _build_records(margins, entering, y_data["support"])
    support_only = _statistics_pair(
        support_records, "support_only_secondary", full=False
    )
    oppose_records = _build_records(margins, entering, y_data["oppose"])
    oppose_only = _statistics_pair(
        oppose_records, "oppose_only_secondary", full=False
    )
    _emit(
        _format_result(
            primary,
            support_only,
            oppose_only,
            battery,
            entry_status,
            y_data,
        ),
        args.out,
    )
    return 0


def _build_parser():
    defaults = _local_defaults()
    parser = argparse.ArgumentParser(
        description="Round 4B FEC congestion x classified-position look",
        allow_abbrev=False,
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--battery", action="store_true", help="synthetic battery only")
    modes.add_argument("--look", action="store_true", help="run the gated real look")
    parser.add_argument("--positions", default=defaults["positions"], metavar="JSONL")
    parser.add_argument("--congestion", default=DEFAULT_CONGESTION, metavar="PATH")
    parser.add_argument("--legislators", default=DEFAULT_LEGISLATORS, metavar="CSV")
    parser.add_argument(
        "--committee-yaml", default=defaults["committee"], metavar="YAML"
    )
    parser.add_argument(
        "--totals-2023", default=defaults["totals_2023"], metavar="CSV"
    )
    parser.add_argument(
        "--totals-2024", default=defaults["totals_2024"], metavar="CSV"
    )
    parser.add_argument("--out", metavar="PATH")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(
        sys.argv[1:] if argv is None else list(argv)
    )
    try:
        if args.look:
            args.positions = _require_path(args.positions, "--positions")
        args.committee_yaml = _require_path(args.committee_yaml, "--committee-yaml")
        args.totals_2023 = _require_path(args.totals_2023, "--totals-2023")
        args.totals_2024 = _require_path(args.totals_2024, "--totals-2024")
        return _run(args)
    except DefectError as exc:
        print(
            "DEFECT HALT: "
            + str(exc)
            + "; DEFECT HALT does not consume the look"
        )
        return 3
    except (IngestError, builder.IngestError) as exc:
        print("ERROR: " + str(exc))
        return 2
    except Exception as exc:
        print("ERROR: FEC x LDA ROUND4B look failed: " + str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
