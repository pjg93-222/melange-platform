"""The ruled LDA status table (D-022 item 3) and its join onto display people.

Every label string below is Paul's approved wording. These are PINS: a change to
any one of them is a change to what the surface tells a reader, and must be a
ruling, not an edit.
"""
import gzip
import hashlib
import json

import pytest

from fec.staffer_network import lda_status_labels as labels


CONFIRMED_TEXT = "Lobbyist · former Hill staff, confirmed"
LIKELY_TEXT = "Lobbyist · former Hill staff, likely"
NO_RECORD_TEXT = "Lobbyist · no Hill record found"


def write_rows(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "rows": len(rows)}


def sidecar_fixture(tmp_path, summary_rows=None, registrant_rows=None, **receipt_overrides):
    directory = tmp_path / labels.SIDECAR_GENERATION
    directory.mkdir(parents=True, exist_ok=True)
    summary_rows = summary_rows if summary_rows is not None else [
        {"lda_source_person_id": "lda:lobbyist:1", "max_tier": "L5", "registrant_side_tier": "R2",
         "tier_by_chamber": {"house": "L5", "senate": "L0"}, "person_office_record_count": 3},
        {"lda_source_person_id": "lda:lobbyist:2", "max_tier": "L2", "registrant_side_tier": "R1",
         "tier_by_chamber": {"house": "L2", "senate": "L0"}, "person_office_record_count": 1},
        {"lda_source_person_id": "lda:lobbyist:3", "max_tier": "L0", "registrant_side_tier": "R0",
         "tier_by_chamber": {"house": "L0", "senate": "L0"}, "person_office_record_count": 0},
    ]
    registrant_rows = registrant_rows if registrant_rows is not None else [
        {"lda_source_person_id": "lda:lobbyist:1", "registrant_side_tier": "R2", "registrants": [
            {"employer_name": "OLD FIRM", "registrant_source_org_id": "lda:registrant:9",
             "start_year": 2011, "end_year": 2014},
            {"employer_name": "HANCE SCARBOROUGH", "registrant_source_org_id": "lda:registrant:17443",
             "start_year": 2021, "end_year": 2026}]},
        {"lda_source_person_id": "lda:lobbyist:2", "registrant_side_tier": "R1", "registrants": [
            {"employer_name": "TATA AMERICA INTERNATIONAL CORPORATION",
             "registrant_source_org_id": "lda:registrant:401092051", "start_year": 2021, "end_year": 2023}]},
        {"lda_source_person_id": "lda:lobbyist:3", "registrant_side_tier": "R0", "registrants": [
            {"employer_name": "JIM DORNAN STRATEGIES", "registrant_source_org_id": "lda:registrant:5",
             "start_year": 2021, "end_year": 2021}]},
    ]
    files = {labels.SUMMARY_FILE: write_rows(directory / labels.SUMMARY_FILE, summary_rows),
             labels.REGISTRANT_FILE: write_rows(directory / labels.REGISTRANT_FILE, registrant_rows)}
    receipt = {"status": "PASS", "failures": [], "files": files, "rule_version": "tiered-corroboration-v2",
               "review_status": "REVIEW_REQUIRED",
               "promotion_policy": "REVIEW_REQUIRED_ZERO_CANONICAL_PROMOTIONS", **receipt_overrides}
    (directory / labels.RECEIPT_FILE).write_text(json.dumps(receipt), encoding="utf-8")
    return directory


# --------------------------------------------------------------------------
# The table, verbatim
# --------------------------------------------------------------------------
@pytest.mark.parametrize("tier,code,text", [
    ("L5", "CONFIRMED", CONFIRMED_TEXT), ("L4", "CONFIRMED", CONFIRMED_TEXT),
    ("L3", "LIKELY", LIKELY_TEXT), ("L2", "LIKELY", LIKELY_TEXT),
    ("L1", "NO_RECORD", NO_RECORD_TEXT), ("L0", "NO_RECORD", NO_RECORD_TEXT)])
def test_every_tier_maps_to_its_ruled_code_and_words(tier, code, text):
    assert labels.status_code(tier) == code
    assert labels.status_label(code) == text


def test_a_person_who_is_not_a_lobbyist_gets_no_label():
    assert labels.status_code("") == labels.NONE
    assert labels.status_code(None) == labels.NONE
    assert labels.status_label(labels.NONE) == ""
    assert labels.person_status([], {"summaries": {}}) is None
    assert labels.person_status(["person:hill:person:a"], {"summaries": {}}) is None


@pytest.mark.parametrize("tier", ["R0", "R1", "R2"])
def test_employer_line_names_the_registrant_when_it_is_on_file(tier):
    # Ruled 2026-09-07: R0 names the employer on file exactly like R1/R2 -- an
    # R0 registrant IS on file, it is just not independently corroborated.
    assert labels.employer_line(tier, "HANCE SCARBOROUGH") == "Employer on file: HANCE SCARBOROUGH"


def test_employer_line_for_a_missing_name_at_any_tier():
    # A missing name cannot be turned into an employer line at any tier,
    # including R0 -- but the fallback is never the retired string.
    assert labels.employer_line("R0", "JIM DORNAN STRATEGIES") == "Employer on file: JIM DORNAN STRATEGIES"
    assert labels.employer_line("R0", None) == "No employer name on file"
    assert labels.employer_line("R2", "") == "No employer name on file"
    assert labels.employer_line("", "") == "No employer name on file"
    assert labels.employer_line("", None) == "No employer name on file"


@pytest.mark.parametrize("tier,phrase", [
    ("R2", "corroborated by dated evidence"),
    ("R1", "corroborated by disclosure"),
    ("R0", "not independently corroborated")])
def test_every_registrant_tier_maps_to_its_corroboration_phrase(tier, phrase):
    assert labels.employer_corroboration(tier) == phrase


def test_a_person_with_no_registrant_tier_gets_no_corroboration_phrase():
    assert labels.employer_corroboration("") == ""
    assert labels.employer_corroboration(None) == ""
    assert labels.employer_corroboration("garbage") == ""


def test_the_retired_employer_string_is_gone():
    # "Employer not on file" is retired everywhere (D-022 follow-up,
    # 2026-09-07): no tier/name combination may ever produce it.
    retired = "Employer not on file"
    assert labels.EMPLOYER_NAME_UNKNOWN != retired
    for tier in ("", "R0", "R1", "R2", "garbage"):
        for name in (None, "", "SOME FIRM"):
            assert labels.employer_line(tier, name) != retired


def test_only_four_codes_exist():
    assert set(labels.CODE_LABELS) == {"CONFIRMED", "LIKELY", "NO_RECORD", "NONE"}
    assert set(labels.TIER_CODES) == {"L0", "L1", "L2", "L3", "L4", "L5"}


# --------------------------------------------------------------------------
# Engagement and client-count wording, ruled 2026-09-07 alongside the
# employment ruling itself.  "mercenary" is the owner's word for the concept
# and is never printed at a person -- engagement_label only ever returns the
# neutral half of the pair.
# --------------------------------------------------------------------------
def test_engagement_label_reads_the_two_ruled_engagements():
    assert labels.engagement_label("contract") == "Contract lobbyist"
    assert labels.engagement_label("in_house") == "In-house lobbyist"


def test_engagement_label_is_empty_for_no_engagement():
    assert labels.engagement_label("") == ""
    assert labels.engagement_label(None) == ""
    assert labels.engagement_label("mercenary") == ""  # the owner's word is not a code


@pytest.mark.parametrize("count,line", [
    (1, "Files for 1 client organisation"),
    (2, "Files for 2 client organisations"),
    (3, "Files for 3 client organisations"),
    (250, "Files for 250 client organisations")])
def test_client_count_line_pluralizes_off_the_singular(count, line):
    assert labels.client_count_line(count) == line


@pytest.mark.parametrize("count", [0, -1, None, "", "not-a-number"])
def test_client_count_line_is_empty_where_there_is_no_count_to_state(count):
    # An in-house filer's registrant is its own client, so a count for them
    # would be a number about nothing -- client_count_line stays silent for
    # every input that is not a positive integer.
    assert labels.client_count_line(count) == ""


# --------------------------------------------------------------------------
# The join
# --------------------------------------------------------------------------
def test_lda_ids_are_recovered_from_the_node_id_and_from_a_spine_join():
    standalone = "person:lda:lobbyist:7"
    assert labels.lda_source_ids(standalone, {}) == ["lda:lobbyist:7"]
    spine = "person:hill:person:abc"
    assert labels.lda_source_ids(spine, {"lda_source_person_id": "lda:lobbyist:7"}) == ["lda:lobbyist:7"]
    merged = labels.lda_source_ids(spine, {
        "lda_source_person_id": "lda:lobbyist:7",
        "source_person_ids": ["lda:lobbyist:8", "politico:uuid"],
        "identity_alias_ids": ["person:lda:lobbyist:9"],
        "source_identity_records": [{"id": "person:lda:lobbyist:10"}]})
    assert merged == ["lda:lobbyist:10", "lda:lobbyist:7", "lda:lobbyist:8", "lda:lobbyist:9"]
    assert labels.lda_source_ids("politico:uuid", {}) == []


def test_status_reads_the_sealed_sidecar_and_names_the_most_recent_employer(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path))
    # Gained employment_people (2026-09-07): 0 here because no `employment` map
    # was passed at all, not because the ruling found nobody -- see the
    # employer_source tests below for the populated case.
    assert sidecar["counts"] == {"summary_rows": 3, "registrant_names": 3, "employment_people": 0}
    status = labels.person_status(["person:lda:lobbyist:1"], sidecar)
    assert status["code"] == "CONFIRMED" and status["label"] == CONFIRMED_TEXT
    assert status["employer_line"] == "Employer on file: HANCE SCARBOROUGH"  # 2026 beats 2014
    assert status["employer_corroboration"] == "corroborated by dated evidence"  # R2
    assert status["tier"] == "L5" and status["registrant_tier"] == "R2"
    assert status["tier_by_chamber"] == {"house": "L5", "senate": "L0"}
    assert status["canonical_eligible"] is False and status["operator_included"] is False
    assert status["provenance"]["generation"] == labels.SIDECAR_GENERATION
    assert {entry["role"] for entry in status["provenance"]["files"]} == {
        labels.SUMMARY_FILE, labels.REGISTRANT_FILE}
    assert all(len(entry["sha256"]) == 64 for entry in status["provenance"]["files"])
    assert len(status["provenance"]["receipt_sha256"]) == 64


def test_the_registrant_display_name_comes_from_the_lda_catalog_when_it_has_one(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path),
                                  {"organization:lda:registrant:17443": "Hance Scarborough LLP"})
    status = labels.person_status(["lda:lobbyist:1"], sidecar)
    assert status["employer_line"] == "Employer on file: Hance Scarborough LLP"


def test_a_merged_person_is_labelled_from_every_record_the_decision_joined(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path))
    status = labels.person_status(["lda:lobbyist:3", "lda:lobbyist:2"], sidecar)
    assert status["tier"] == "L2" and status["code"] == "LIKELY"
    assert status["registrant_tier"] == "R1"
    assert status["employer_line"] == "Employer on file: TATA AMERICA INTERNATIONAL CORPORATION"
    assert status["employer_corroboration"] == "corroborated by disclosure"  # R1
    assert status["lda_source_person_ids"] == ["lda:lobbyist:2", "lda:lobbyist:3"]
    assert status["matched_source_person_ids"] == ["lda:lobbyist:2", "lda:lobbyist:3"]


def test_an_lda_id_absent_from_the_summary_is_labelled_from_nothing(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path))
    status = labels.person_status(["lda:lobbyist:404"], sidecar)
    assert status["code"] == labels.NONE and status["label"] == ""
    assert status["tier"] is None and status["matched_source_person_ids"] == []


# --------------------------------------------------------------------------
# The employment ruling (2026-09-07), handed in as `employment=...` in the
# shape fec.staffer_network.lda_employment.load_from_organisations_artifact
# returns.  Handed nothing, this module keeps doing exactly what it always
# did: the sidecar's own registrant list, ordered by year.
# --------------------------------------------------------------------------
def employment_fixture(people=None, truncated=False):
    return {"people": people or {}, "truncated": truncated,
            "meta": {"schema_id": "staffer-organisations-section-v3"}}


def test_employer_source_is_the_sidecar_year_order_when_no_employment_is_passed(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path))
    status = labels.person_status(["lda:lobbyist:1"], sidecar)
    assert status["employer_source"] == labels.EMPLOYER_SOURCE_SIDECAR
    assert status["employer_line"] == "Employer on file: HANCE SCARBOROUGH"  # unchanged sidecar answer
    assert status["engagement"] is None and status["engagement_label"] == ""
    assert status["client_count"] is None and status["client_count_line"] == ""
    assert status["clients"] == []


def test_employer_source_is_the_ruling_when_employment_names_this_person(tmp_path):
    employment = employment_fixture({
        "lda:lobbyist:1": {"employer_name": "HANCE SCARBOROUGH LLP", "as_of": "2026 Q2",
                           "engagement": "contract", "client_count": 3, "clients": {}}})
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    status = labels.person_status(["lda:lobbyist:1"], sidecar)
    assert status["employer_source"] == labels.EMPLOYER_SOURCE_RULING
    # The ruling's own name is used verbatim, even though it differs from the
    # sidecar's "HANCE SCARBOROUGH" -- the ruling reads the raw LDA corpus and
    # does not defer to the sidecar's separate registrant-matching.
    assert status["employer_line"] == "Employer on file: HANCE SCARBOROUGH LLP"
    assert status["employer_as_of"] == "2026 Q2"
    assert status["engagement"] == "contract"
    assert status["engagement_label"] == "Contract lobbyist"
    assert status["client_count"] == 3
    assert status["client_count_line"] == "Files for 3 client organisations"


def test_in_house_engagement_from_the_ruling_carries_no_client_count(tmp_path):
    employment = employment_fixture({
        "lda:lobbyist:1": {"employer_name": "HANCE SCARBOROUGH", "as_of": "2026 Q2",
                           "engagement": "in_house", "clients": {}}})
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    status = labels.person_status(["lda:lobbyist:1"], sidecar)
    assert status["engagement"] == "in_house"
    assert status["engagement_label"] == "In-house lobbyist"
    assert status["client_count"] is None
    assert status["client_count_line"] == ""


def test_clients_are_populated_from_the_employment_map_and_sorted_by_name(tmp_path):
    employment = employment_fixture({
        "lda:lobbyist:1": {
            "employer_name": "HANCE SCARBOROUGH", "as_of": "2026 Q2",
            "engagement": "contract", "client_count": 2,
            "clients": {"lda:client:12": {"name": "PRAIRIE TELECOM CORP", "latest_period": "2025 Q4"},
                       "lda:client:9": {"name": "GLOBAL WIDGETS INC", "latest_period": "2026 Q1"}}}})
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    status = labels.person_status(["lda:lobbyist:1"], sidecar)
    assert status["clients"] == [
        {"id": "lda:client:9", "name": "GLOBAL WIDGETS INC", "latest_period": "2026 Q1"},
        {"id": "lda:client:12", "name": "PRAIRIE TELECOM CORP", "latest_period": "2025 Q4"}]


def test_an_employment_map_that_is_silent_for_this_person_falls_back_to_the_sidecar(tmp_path):
    # `employment` is truthy but names nobody this person carries an id for --
    # the fallback is per person, not all-or-nothing for the whole call.
    employment = employment_fixture({"lda:lobbyist:999": {"employer_name": "SOMEONE ELSE",
                                                           "as_of": "2026 Q1"}})
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    status = labels.person_status(["lda:lobbyist:1"], sidecar)
    assert status["employer_source"] == labels.EMPLOYER_SOURCE_SIDECAR
    assert status["employer_line"] == "Employer on file: HANCE SCARBOROUGH"


def test_a_merged_person_takes_the_employer_with_the_later_as_of_not_the_later_id(tmp_path):
    # lda:lobbyist:20 sorts BEFORE :21 but names the chronologically LATER
    # filing. person_status has to compare `as_of`, not fall through the
    # sorted id list and let whichever is processed last win by construction --
    # this is the fixture that would catch a regression to that.
    employment = employment_fixture({
        "lda:lobbyist:20": {"employer_name": "LATE ARRIVALS LLC", "as_of": "2026 Q3",
                            "engagement": "in_house", "clients": {}},
        "lda:lobbyist:21": {"employer_name": "OLD GUARD LLP", "as_of": "2022 Q1",
                            "engagement": "in_house", "clients": {}}})
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    status = labels.person_status(["lda:lobbyist:21", "lda:lobbyist:20"], sidecar)
    assert status["employer_line"] == "Employer on file: LATE ARRIVALS LLC"
    assert status["employer_as_of"] == "2026 Q3"
    assert status["employer_source"] == labels.EMPLOYER_SOURCE_RULING
    assert status["lda_source_person_ids"] == ["lda:lobbyist:20", "lda:lobbyist:21"]


def test_load_sidecar_carries_the_employment_ruling_provenance(tmp_path):
    sidecar_bare = labels.load_sidecar(sidecar_fixture(tmp_path))
    assert sidecar_bare["counts"]["employment_people"] == 0
    assert sidecar_bare["provenance"]["employment_ruling_applied"] is False
    assert sidecar_bare["provenance"]["employment_ruling_truncated"] is False
    assert sidecar_bare["employment"] == {}

    employment = employment_fixture(
        {"lda:lobbyist:1": {"employer_name": "X", "as_of": "2026 Q1"}}, truncated=True)
    sidecar_ruled = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    assert sidecar_ruled["counts"]["employment_people"] == 1
    assert sidecar_ruled["provenance"]["employment_ruling_applied"] is True
    assert sidecar_ruled["provenance"]["employment_ruling_truncated"] is True
    assert sidecar_ruled["provenance"]["employment_ruling_meta"] == employment["meta"]


@pytest.mark.parametrize("break_it", ["hash", "size", "receipt", "missing"])
def test_the_sidecar_fails_closed(tmp_path, break_it):
    directory = sidecar_fixture(tmp_path, **({"status": "IN_PROGRESS"} if break_it == "receipt" else {}))
    if break_it == "hash":
        write_rows(directory / labels.SUMMARY_FILE, [
            {"lda_source_person_id": "lda:lobbyist:1", "max_tier": "L5", "registrant_side_tier": "R2"}])
    if break_it == "size":
        receipt = json.loads((directory / labels.RECEIPT_FILE).read_text(encoding="utf-8"))
        receipt["files"][labels.SUMMARY_FILE] = {"bytes": 1, "rows": 3}
        (directory / labels.RECEIPT_FILE).write_text(json.dumps(receipt), encoding="utf-8")
    if break_it == "missing":
        (directory / labels.REGISTRANT_FILE).unlink()
    with pytest.raises(labels.SidecarUnavailable):
        labels.load_sidecar(directory)


# --------------------------------------------------------------------------
# attach()
# --------------------------------------------------------------------------
def nodes_fixture():
    return {
        "person:lda:lobbyist:1": {"id": "person:lda:lobbyist:1", "kind": "person", "label": "ONE"},
        "person:hill:person:a": {"id": "person:hill:person:a", "kind": "person", "label": "Two",
                                 "lda_source_person_id": "lda:lobbyist:2"},
        "person:lda:lobbyist:3": {"id": "person:lda:lobbyist:3", "kind": "person", "label": "THREE"},
        "person:lda:lobbyist:404": {"id": "person:lda:lobbyist:404", "kind": "person", "label": "ABSENT"},
        "person:hill:person:b": {"id": "person:hill:person:b", "kind": "person", "label": "No Lobbying"},
        "organization:lda:registrant:1": {"id": "organization:lda:registrant:1", "kind": "organization",
                                          "label": "A FIRM", "lda_source_person_id": "lda:lobbyist:1"},
    }


def test_attach_labels_lda_people_only_and_counts_the_join(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path))
    nodes = nodes_fixture()
    counts = labels.attach(nodes, sidecar)
    # Four people carry an LDA id; three of them have a summary row: the join count.
    assert counts["lda_people"] == 4
    assert counts["joined_to_summary"] == 3
    assert counts["labels"] == {"CONFIRMED": 1, "LIKELY": 1, "NO_RECORD": 1, "NONE": 1}
    assert sum(counts["labels"].values()) == counts["lda_people"]
    assert counts["joined_to_summary"] == sum(
        1 for node in nodes.values()
        if node.get("kind") == "person" and (node.get("lda_status") or {}).get("matched_source_person_ids"))
    assert nodes["person:lda:lobbyist:1"]["lda_status"]["label"] == CONFIRMED_TEXT
    assert nodes["person:hill:person:a"]["lda_status"]["label"] == LIKELY_TEXT
    assert nodes["person:lda:lobbyist:3"]["lda_status"]["label"] == NO_RECORD_TEXT
    assert nodes["person:lda:lobbyist:404"]["lda_status"]["label"] == ""
    # A person with no lobbying record, and an organisation, are never labelled.
    assert "lda_status" not in nodes["person:hill:person:b"]
    assert "lda_status" not in nodes["organization:lda:registrant:1"]


def test_without_a_sidecar_nothing_is_labelled():
    nodes = nodes_fixture()
    for empty in (None, {}, {"summaries": {}}):
        counts = labels.attach(nodes, empty)
        assert counts == {"lda_people": 0, "joined_to_summary": 0, "labels": {}, "status": "sidecar_unavailable"}
    assert not any("lda_status" in node for node in nodes.values())


def test_attach_counts_how_many_labels_came_from_the_ruling(tmp_path):
    employment = employment_fixture({
        "lda:lobbyist:1": {"employer_name": "HANCE SCARBOROUGH", "as_of": "2026 Q2",
                           "engagement": "contract", "client_count": 2, "clients": {}}})
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path), employment=employment)
    nodes = nodes_fixture()
    counts = labels.attach(nodes, sidecar)
    # Of the 4 LDA people attach() labels here, only lobbyist:1 is named by the
    # employment map; the rest fall back to the sidecar's year order.
    assert counts["employer_from_ruling"] == 1
    assert counts["employer_from_sidecar_year_order"] == counts["lda_people"] - 1
    assert counts["engagements"] == {"contract": 1}
    status = nodes["person:lda:lobbyist:1"]["lda_status"]
    assert status["employer_source"] == labels.EMPLOYER_SOURCE_RULING
    assert status["engagement"] == "contract"
    assert status["client_count"] == 2


def test_a_stale_label_is_removed_when_the_person_no_longer_carries_an_lda_id(tmp_path):
    sidecar = labels.load_sidecar(sidecar_fixture(tmp_path))
    nodes = {"person:hill:person:c": {"id": "person:hill:person:c", "kind": "person",
                                      "label": "Was Merged", "lda_status": {"code": "CONFIRMED"}}}
    labels.attach(nodes, sidecar)
    assert "lda_status" not in nodes["person:hill:person:c"]
