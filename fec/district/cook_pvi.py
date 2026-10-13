"""Offline Cook PVI source snapshot and descriptive district-code lookup.

Import safe. No model, allocation or ranking consumer belongs in this module.
An available match identifies a historical source row, not verified current
district geography or incumbency. Missing and out-of-scope values stay absent.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "source_data" / "cook_pvi" / "2026"
SCHEMA = "cook-pvi-district-reference-v1"
CSV_NAME = "cook_pvi_2026_districts.csv"
SOURCE_URL = "https://www.cookpolitical.com/cook-pvi/2026-partisan-voting-index/district-map-and-list"
# 2020 apportionment for the 2026 source snapshot, not a current-boundary claim.
SEATS = dict(zip(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    (7, 1, 9, 4, 52, 8, 5, 1, 28, 14, 2, 2, 17, 9, 4, 4, 6, 6, 2, 8, 9, 13, 8, 4, 8, 2, 3, 4, 2, 12, 3, 26, 14, 1, 15, 5, 6, 17, 2, 7, 1, 9, 38, 4, 1, 11, 10, 2, 8, 1),
))
STATE_NAMES = dict(zip(
    ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming").lower().split("|"),
    SEATS,
))
OUTSIDE_STATES = {"DC", "AS", "GU", "MP", "PR", "VI"}
STATE_NAMES.update({"district of columbia": "DC", "american samoa": "AS", "guam": "GU",
                    "northern mariana islands": "MP", "puerto rico": "PR",
                    "u.s. virgin islands": "VI", "virgin islands": "VI"})
INTEGER_FIELDS = {"district_number", "district_source_sort_key", "pvi_2025_signed_points",
                  "pvi_2026_signed_points", "pvi_2026_source_sort_key", "source_rank_2026"}


def _points(label):
    if label == "EVEN":
        return 0
    match = re.fullmatch(r"([DR])\+([1-9]\d?)", label or "")
    if not match:
        raise ValueError(f"Invalid Cook PVI label: {label!r}")
    return int(match[2]) * (-1 if match[1] == "D" else 1)


def _cell(value):
    match = re.fullmatch(r"(.+)@@(\d+)", value or "")
    if not match:
        raise ValueError(f"Invalid Cook source cell: {value!r}")
    return match[1], int(match[2])


def load_reference(source_dir=SOURCE_DIR):
    """Validate the complete hash-bound CSV/JSON/raw snapshot before returning it."""
    source_dir = Path(source_dir)
    manifest = json.loads((source_dir / "source_manifest.json").read_text(encoding="utf-8"))
    metadata = manifest["metadata"]
    hashes = manifest["sha256"]
    required_files = {CSV_NAME, "cook_pvi_2026_districts.json", "raw/dataset.csv",
                      "raw/datawrapper_fejB0_v1.html", "normalize_source.py", "source_parse_plan.md"}
    normalized_hashes = {key.replace("\\", "/"): value for key, value in hashes.items()}
    if set(normalized_hashes) != required_files:
        raise ValueError("Cook PVI manifest must cover the six source artifacts")
    content = {}
    for name in sorted(required_files):
        content[name] = (source_dir / name).read_bytes()
        if hashlib.sha256(content[name]).hexdigest() != normalized_hashes[name]:
            raise ValueError(f"Cook PVI source hash mismatch: {name}")
    if (metadata.get("record_count") != 435 or metadata.get("source_page_url") != SOURCE_URL
            or metadata.get("article_publication_date") != "2026-07-29"
            or metadata.get("retrieval_date") != "2026-10-05"):
        raise ValueError("Unexpected Cook PVI snapshot provenance")

    reader = csv.DictReader(io.StringIO(content[CSV_NAME].decode("utf-8-sig")))
    required_columns = INTEGER_FIELDS | {"district_source_cell", "district_label", "district_id",
        "state", "incumbent_source_text", "incumbent_party_source", "pvi_2025_label",
        "pvi_2026_source_cell", "pvi_2026_label", "boundary_currency_status"}
    if set(reader.fieldnames or []) != required_columns or len(reader.fieldnames) != len(required_columns):
        raise ValueError("Unexpected Cook PVI CSV columns")
    records = []
    for row in reader:
        if set(row) != required_columns or any(value is None or value == "" for value in row.values()):
            raise ValueError("Cook PVI row contains missing or extra cells")
        for field in INTEGER_FIELDS:
            if not re.fullmatch(r"-?\d+", row[field]):
                raise ValueError(f"Invalid integer in Cook PVI {field}")
            row[field] = int(row[field])
        key = f"{row['state']}-{row['district_number']:02d}"
        expected_label = f"{row['state']}-AL" if row["district_number"] == 0 else key
        if row["district_id"] != key or row["district_label"] != expected_label:
            raise ValueError(f"Cook district identity mismatch: {key}")
        if _cell(row["district_source_cell"]) != (expected_label, row["district_source_sort_key"]):
            raise ValueError(f"Cook district source cell mismatch: {key}")
        if _cell(row["pvi_2026_source_cell"]) != (row["pvi_2026_label"], row["pvi_2026_source_sort_key"]):
            raise ValueError(f"Cook PVI source cell mismatch: {key}")
        for year in (2025, 2026):
            if _points(row[f"pvi_{year}_label"]) != row[f"pvi_{year}_signed_points"]:
                raise ValueError(f"Cook PVI sign/label mismatch: {key}, {year}")
        if row["boundary_currency_status"] != "UNVERIFIED-HERE":
            raise ValueError(f"Unsupported Cook boundary certification: {key}")
        records.append(row)
    expected = {f"{state}-{district:02d}" for state, seats in SEATS.items()
                for district in ([0] if seats == 1 else range(1, seats + 1))}
    keys = [row["district_id"] for row in records]
    if len(keys) != 435 or set(keys) != expected:
        raise ValueError("Cook PVI snapshot must contain exactly the 435 expected unique districts")
    if sorted(row["source_rank_2026"] for row in records) != list(range(1, 436)):
        raise ValueError("Cook PVI source ranks are incomplete or duplicated")
    original = json.loads(content["cook_pvi_2026_districts.json"])
    normalized_json = [{**row, **{field: int(row[field]) for field in INTEGER_FIELDS}}
                       for row in original["rows"]]
    if original["metadata"] != metadata or normalized_json != records:
        raise ValueError("Cook PVI CSV/JSON snapshot disagreement")
    raw_rows = list(csv.DictReader(io.StringIO(content["raw/dataset.csv"].decode("utf-8-sig")), delimiter="\t"))
    raw_by_label = {row["Dist"]: row for row in raw_rows}
    if len(raw_rows) != 435 or len(raw_by_label) != 435:
        raise ValueError("Cook PVI raw source districts are incomplete or duplicated")
    for row in records:
        raw = raw_by_label.get(row["district_source_cell"], {})
        expected_raw = {"Dist": row["district_source_cell"], "Incumbent": row["incumbent_source_text"],
                        "Incumbent Party": row["incumbent_party_source"], "2025 PVI": row["pvi_2025_label"],
                        "2026 PVI": row["pvi_2026_source_cell"], "Rank": str(row["source_rank_2026"])}
        if raw != expected_raw:
            raise ValueError(f"Cook PVI raw/normalized disagreement: {row['district_id']}")
    return {
        "schema": SCHEMA,
        "metadata": {**{key: metadata[key] for key in (
            "source_page_url", "source_embed_url", "source_asset_url", "article_publication_date",
            "embed_last_modified_at", "retrieval_date", "record_count", "signed_points_convention")},
            "edition": 2026, "display_only": True, "match_basis": "district_code_only",
            "boundary_currency_status": "UNVERIFIED-HERE", "csv_sha256": normalized_hashes[CSV_NAME],
            "source_manifest_sha256": hashlib.sha256((source_dir / "source_manifest.json").read_bytes()).hexdigest(),
            "interpretation": "Rounded historical presidential partisan lean; not an election forecast.",
            "geography_note": "Historical district-code match only; current boundaries and incumbency are unverified."},
        "districts": {row["district_id"]: row for row in records},
    }


def load_optional_reference(source_dir=SOURCE_DIR):
    """Absent snapshot is unavailable; a partial/corrupt snapshot fails visibly."""
    return load_reference(source_dir) if Path(source_dir).exists() else None


def _state(value):
    value = str(value or "").strip()
    return STATE_NAMES.get(value.lower(), value.upper())


def _district_key(state, value):
    value = str(value if value is not None else "").strip()
    if value.upper() in {"AL", "AT-LARGE", "AT LARGE"}:
        number = 0
    elif re.fullmatch(r"\d{1,2}", value):
        number = int(value)
    else:
        return None
    return f"{state}-{number:02d}" if state in SEATS else None


def _member_district(member):
    value = member.get("district")
    text = str(value if value is not None else "").strip()
    state = _state(member.get("state") or (text.rsplit(",", 1)[-1] if "," in text else ""))
    canonical = re.fullmatch(r"([A-Za-z]{2})-(\d{1,2}|AL)", text, re.I)
    if canonical:
        if state and state != canonical[1].upper():
            return state, None, True
        state = canonical[1].upper()
        return state, _district_key(state, canonical[2]), False
    numbers = set(re.findall(r"\bdistrict\s+(\d{1,2})\b", text, re.I))
    at_large = re.search(r"\bat[- ]large\b", text, re.I)
    if len(numbers) > 1 or (numbers and at_large):
        return state, None, True
    number = next(iter(numbers)) if numbers else None
    return state, _district_key(state, number if number is not None else "AL" if at_large else text), False


def member_reference(reference, member, legislator=None):
    """Return dated context; never match source incumbent names or fill Senate values."""
    chamber = str(member.get("chamber") or "").strip().lower()
    typ = str((legislator or {}).get("type") or "").strip().lower()
    if chamber in {"senate", "sen"}:
        return {"status": "outside_scope", "reason": "District PVI does not cover Senate seats."}
    if chamber not in {"house", "rep"}:
        return {"status": "unmatched", "reason": "House chamber identity is unavailable."}
    member_state, member_key, conflict = _member_district(member)
    if conflict:
        return {"status": "unmatched", "reason": "Member state and district identity conflict."}
    if legislator is not None:
        if typ != "rep":
            return {"status": "unmatched", "reason": "Roster chamber identity conflicts with the member."}
        state = _state(legislator.get("state"))
        key = _district_key(state, legislator.get("district"))
        if (member_state and member_state != state) or (member_key and member_key != key):
            return {"status": "unmatched", "reason": "Roster and member district identity conflict."}
        identity_source = "legislators_bioguide"
    else:
        state, key = member_state, member_key
        identity_source = "member_district_text"
    if state in OUTSIDE_STATES:
        return {"status": "outside_scope", "reason": "This House district is outside the 435-district source snapshot."}
    if reference is None:
        return {"status": "unavailable", "reason": "Cook PVI source snapshot is unavailable."}
    row = reference["districts"].get(key)
    if row is None:
        return {"status": "unmatched", "reason": "No unambiguous district-code match in the source snapshot."}
    return {"status": "available", "district_id": key, "district_label": row["district_label"],
            "pvi_label": row["pvi_2026_label"], "signed_points": row["pvi_2026_signed_points"],
            "boundary_currency_status": row["boundary_currency_status"],
            "match_basis": "district_code_only", "identity_source": identity_source}


def attach_reference(bundle, reference, legislators=None):
    """Add only PVI context fields, after all existing desk calculations finish."""
    counts = {}
    for member in bundle["members"]:
        bioguide = (member.get("bio") or {}).get("bioguide")
        member["cook_pvi"] = member_reference(reference, member, (legislators or {}).get(bioguide))
        status = member["cook_pvi"]["status"]
        counts[status] = counts.get(status, 0) + 1
    bundle["cook_pvi_meta"] = {**(reference["metadata"] if reference else {"display_only": True}),
                               "coverage": counts}
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=ROOT / "data/district/cook_pvi_2026.json")
    args = parser.parse_args()
    reference = load_reference(args.source_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                     prefix=args.output.name + ".", suffix=".tmp", delete=False, newline='\r\n') as handle:
        temporary = Path(handle.name)
        try:
            json.dump(reference, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Validated {len(reference['districts'])} historical districts -> {args.output}")


if __name__ == "__main__":
    main()
