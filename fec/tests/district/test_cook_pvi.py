"""Integrity and identity checks for the dated, descriptive Cook PVI snapshot."""
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.district import cook_pvi


@pytest.fixture(scope="module")
def reference():
    return cook_pvi.load_reference()


@pytest.fixture
def copied_source(tmp_path):
    return Path(shutil.copytree(cook_pvi.SOURCE_DIR, tmp_path / "cook"))


def rehash(source, filename):
    """Let a planted data defect reach semantic checks past the byte guard."""
    path = source / "source_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    matching = [key for key in manifest["sha256"] if key.replace("\\", "/") == filename]
    assert len(matching) == 1
    manifest["sha256"][matching[0]] = hashlib.sha256((source / filename).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")


def change_csv(source, change):
    path = source / cook_pvi.CSV_NAME
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    change(rows)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    rehash(source, cook_pvi.CSV_NAME)


def test_shipped_census_and_source_provenance(reference):
    rows = reference["districts"]
    assert len(rows) == 435
    assert len({row["state"] for row in rows.values()}) == 50
    assert {key for key, row in rows.items() if row["district_number"] == 0} == {
        "AK-00", "DE-00", "ND-00", "SD-00", "VT-00", "WY-00",
    }
    assert Counter(row["pvi_2026_label"].split("+")[0] for row in rows.values()) == {
        "D": 203, "R": 225, "EVEN": 7,
    }
    assert rows["AL-01"]["pvi_2025_signed_points"] == 27
    assert rows["AL-01"]["pvi_2026_signed_points"] == 17
    assert rows["AL-07"]["pvi_2026_signed_points"] == -10
    assert rows["AZ-06"]["pvi_2026_signed_points"] == 0
    meta = reference["metadata"]
    assert meta["article_publication_date"] == "2026-07-29"
    assert meta["retrieval_date"] == "2026-10-05"
    assert meta["edition"] == 2026
    assert meta["display_only"] is True
    assert meta["boundary_currency_status"] == "UNVERIFIED-HERE"
    assert meta["csv_sha256"] == "3bf2786d391323b48f4d87e9b5c5e82802bbf7749a1c83e7c6da600fa353cb71"


@pytest.mark.parametrize("filename", [cook_pvi.CSV_NAME, "raw/dataset.csv", "normalize_source.py"])
def test_changed_source_bytes_fail_hash_guard(copied_source, filename):
    path = copied_source / filename
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="source hash mismatch"):
        cook_pvi.load_reference(copied_source)


@pytest.mark.parametrize("year", [2025, 2026])
def test_wrong_sign_rejected_even_with_updated_checksum(copied_source, year):
    def corrupt(rows):
        row = next(row for row in rows if row["district_id"] == "AL-07")
        field = f"pvi_{year}_signed_points"
        row[field] = str(-int(row[field]))
    change_csv(copied_source, corrupt)
    with pytest.raises(ValueError, match="sign/label mismatch"):
        cook_pvi.load_reference(copied_source)


@pytest.mark.parametrize("defect", ["duplicate", "missing"])
def test_incomplete_census_rejected_even_with_updated_checksum(copied_source, defect):
    def corrupt(rows):
        if defect == "duplicate":
            rows[-1] = rows[0].copy()
        else:
            rows.pop()
    change_csv(copied_source, corrupt)
    with pytest.raises(ValueError, match="435 expected unique districts"):
        cook_pvi.load_reference(copied_source)


def test_missing_pvi_is_not_silently_treated_as_even(copied_source):
    change_csv(copied_source, lambda rows: rows[0].update(pvi_2026_label=""))
    with pytest.raises(ValueError, match="missing or extra cells"):
        cook_pvi.load_reference(copied_source)


@pytest.mark.parametrize("label", ["T+6", "R-6", "R+0", "D+2.5", "EVEN "])
def test_malformed_pvi_labels_rejected_after_source_cell_agrees(copied_source, label):
    def corrupt(rows):
        rows[0]["pvi_2026_label"] = label
        suffix = rows[0]["pvi_2026_source_sort_key"]
        rows[0]["pvi_2026_source_cell"] = f"{label}@@{suffix}"
    change_csv(copied_source, corrupt)
    with pytest.raises(ValueError, match="Invalid Cook PVI label"):
        cook_pvi.load_reference(copied_source)


def test_false_even_zero_rejected_when_original_source_label_disagrees(copied_source):
    change_csv(copied_source, lambda rows: rows[0].update(pvi_2026_label="EVEN", pvi_2026_signed_points="0"))
    with pytest.raises(ValueError, match="source cell mismatch"):
        cook_pvi.load_reference(copied_source)


def test_invalid_publisher_sort_suffix_rejected(copied_source):
    change_csv(copied_source, lambda rows: rows[0].update(pvi_2026_source_cell="R+6@@not-an-integer"))
    with pytest.raises(ValueError, match="Invalid Cook source cell"):
        cook_pvi.load_reference(copied_source)


def test_source_cannot_claim_verified_boundaries(copied_source):
    change_csv(copied_source, lambda rows: rows[0].update(boundary_currency_status="VERIFIED"))
    with pytest.raises(ValueError, match="Unsupported Cook boundary certification"):
        cook_pvi.load_reference(copied_source)


def test_json_disagreement_is_rejected(copied_source):
    name = "cook_pvi_2026_districts.json"
    path = copied_source / name
    document = json.loads(path.read_text(encoding="utf-8"))
    document["rows"][0]["incumbent_source_text"] = "Changed name"
    path.write_text(json.dumps(document), encoding="utf-8")
    rehash(copied_source, name)
    with pytest.raises(ValueError, match="CSV/JSON snapshot disagreement"):
        cook_pvi.load_reference(copied_source)


def test_raw_disagreement_is_rejected(copied_source):
    name = "raw/dataset.csv"
    path = copied_source / name
    original = path.read_bytes()
    assert b"Nick Begich" in original
    path.write_bytes(original.replace(b"Nick Begich", b"Wrong source name", 1))
    rehash(copied_source, name)
    with pytest.raises(ValueError, match="raw/normalized disagreement"):
        cook_pvi.load_reference(copied_source)


def test_absent_optional_source_is_unavailable_but_partial_source_raises(tmp_path):
    absent = tmp_path / "absent"
    assert cook_pvi.load_optional_reference(absent) is None
    absent.mkdir()
    with pytest.raises(FileNotFoundError):
        cook_pvi.load_optional_reference(absent)


@pytest.mark.parametrize("member", [
    {"chamber": "House", "district": "AK-AL"},
    {"chamber": "House", "district": "AK-00"},
    {"chamber": "House", "district": "At-large, Alaska"},
    {"chamber": "House", "state": "Alaska", "district": "At Large"},
    {"chamber": "House", "state": "AK", "district": 0},
])
def test_at_large_identity_formats(reference, member):
    result = cook_pvi.member_reference(reference, member)
    assert result["status"] == "available"
    assert result["district_id"] == "AK-00"
    assert result["pvi_label"] == "R+6"
    assert result["signed_points"] == 6
    assert result["match_basis"] == "district_code_only"
    assert result["boundary_currency_status"] == "UNVERIFIED-HERE"


def test_roster_zero_district_and_full_state_name(reference):
    result = cook_pvi.member_reference(
        reference, {"chamber": "House"}, {"type": "rep", "state": "Alaska", "district": 0},
    )
    assert result["status"] == "available"
    assert result["district_id"] == "AK-00"
    assert result["identity_source"] == "legislators_bioguide"


def test_even_stays_available_while_unknown_district_stays_absent(reference):
    even = cook_pvi.member_reference(reference, {"chamber": "House", "district": "AZ-06"})
    missing = cook_pvi.member_reference(reference, {"chamber": "House", "district": "AZ-99"})
    assert even["status"] == "available"
    assert even["pvi_label"] == "EVEN"
    assert even["signed_points"] == 0
    assert missing["status"] == "unmatched"
    assert "signed_points" not in missing
    assert "pvi_label" not in missing


@pytest.mark.parametrize("chamber", ["Senate", "sen"])
def test_senate_never_receives_house_district_pvi(reference, chamber):
    result = cook_pvi.member_reference(
        reference, {"chamber": chamber, "district": "AK-00"},
        {"type": "rep", "state": "AK", "district": 0},
    )
    assert result["status"] == "outside_scope"
    assert "signed_points" not in result


@pytest.mark.parametrize("state", ["DC", "AS", "GU", "MP", "PR", "VI"])
def test_delegates_and_territories_remain_outside_scope(reference, state):
    result = cook_pvi.member_reference(reference, {"chamber": "House", "district": f"{state}-00"})
    assert result["status"] == "outside_scope"
    assert "signed_points" not in result


@pytest.mark.parametrize("member, legislator", [
    ({"district": "AK-00"}, None),
    ({"chamber": "House"}, None),
    ({"chamber": "House", "name": "Nick Begich"}, None),
    ({"chamber": "House", "district": "District 1"}, None),
    ({"chamber": "House", "state": "Alaska", "district": "CA-01"}, None),
    ({"chamber": "House", "district": "AK-00"}, {"type": "sen", "state": "AK", "district": 0}),
    ({"chamber": "House", "district": "AK-00"}, {"type": "rep", "state": "AK"}),
    ({"chamber": "House", "state": "CA", "district": "CA-01"}, {"type": "rep", "state": "AK", "district": 0}),
    ({"chamber": "House", "district": "CA-02"}, {"type": "rep", "state": "CA", "district": 1}),
    ({"chamber": "House", "district": "District 1 or District 2, New York"}, None),
    ({"chamber": "House", "district": "District 1 (at large), Alaska"}, None),
])
def test_missing_or_conflicting_identity_never_gets_a_value(reference, member, legislator):
    result = cook_pvi.member_reference(reference, member, legislator)
    assert result["status"] == "unmatched"
    assert "signed_points" not in result


def test_absent_source_returns_unavailable_for_identified_house_member():
    result = cook_pvi.member_reference(None, {"chamber": "House", "district": "AK-00"})
    assert result["status"] == "unavailable"
    assert "signed_points" not in result


def test_bundle_addition_preserves_every_prior_field_and_source(reference):
    bundle = {
        "schema": "existing-desk", "vintage": "2026-10-05", "nested": {"weights": [0.2, 0.8]},
        "members": [
            {"chamber": "House", "district": "AZ-06", "bio": {"bioguide": "A000001"},
             "score": 0.75, "rank": 3, "allocation": {"dollars": 100, "factor": 1.0}},
            {"chamber": "Senate", "state": "AK", "score": None, "rank": None},
            {"chamber": "House", "district": "PR-00", "score": -1},
            {"chamber": "House", "name": "Unidentified member", "score": 0},
        ],
    }
    original_bundle = deepcopy(bundle)
    original_reference = deepcopy(reference)
    legislators = {"A000001": {"type": "rep", "state": "AZ", "district": 6}}
    original_legislators = deepcopy(legislators)
    enriched = cook_pvi.attach_reference(bundle, reference, legislators)
    restored = deepcopy(enriched)
    restored.pop("cook_pvi_meta")
    for member in restored["members"]:
        member.pop("cook_pvi")
    assert restored == original_bundle
    assert reference == original_reference
    assert legislators == original_legislators
    assert enriched["cook_pvi_meta"]["coverage"] == {
        "available": 1, "outside_scope": 2, "unmatched": 1,
    }
    assert enriched["members"][0]["cook_pvi"]["signed_points"] == 0
    assert enriched["members"][0]["cook_pvi"]["identity_source"] == "legislators_bioguide"
    assert enriched["cook_pvi_meta"]["display_only"] is True
    assert enriched["cook_pvi_meta"]["csv_sha256"] == reference["metadata"]["csv_sha256"]


def test_empty_bundle_and_missing_source_have_explicit_coverage():
    empty = cook_pvi.attach_reference({"members": []}, None)
    assert empty["cook_pvi_meta"] == {"display_only": True, "coverage": {}}
    bundle = {"members": [{"chamber": "House", "district": "AK-00"}, {"chamber": "Senate"}]}
    result = cook_pvi.attach_reference(bundle, None)
    assert result["cook_pvi_meta"]["coverage"] == {"unavailable": 1, "outside_scope": 1}
