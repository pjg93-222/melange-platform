from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import stage_billstatus_bulk_enrichment as billstatus


@contextmanager
def raises(error_type: type[BaseException], match: str):
    try:
        yield
    except error_type as error:
        assert re.search(match, str(error)), (match, str(error))
    else:
        raise AssertionError(f"{error_type.__name__} was not raised")


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_fetch_plan_is_discovery_only_and_preserves_amendments(tmp_path: Path) -> None:
    discovery = tmp_path / "records.ndjson"
    exclusions = tmp_path / "exclusions.ndjson"
    reviewed_exclusions = tmp_path / "false_positive_exclusions.ndjson"
    write_ndjson(
        discovery,
        [
            {
                "congress": 119,
                "type": "hr",
                "number": 7,
                "canonical_key": "119:hr:7",
            },
            {
                "congress": 116,
                "type": "hamdt",
                "number": 2,
                "canonical_key": "116:hamdt:2",
            },
            {
                "congress": 118,
                "type": "samdt",
                "number": 9,
                "canonical_key": "118:samdt:9",
            },
        ],
    )
    write_ndjson(exclusions, [{"canonical_key": "119:hr:99", "reason": "x"}])
    write_ndjson(
        reviewed_exclusions,
        [{"canonical_key": "119:hr:8", "decision": "exclude_false_positive"}],
    )

    plan = billstatus.build_fetch_plan(
        discovery, exclusions, (reviewed_exclusions,)
    )
    billstatus.validate_fetch_plan(plan)

    assert plan["primary_discovery"] == (
        "Congress.gov Advanced Search - Legislation"
    )
    assert plan["quorum_used"] is False
    assert plan["network_executed"] is False
    assert plan["latest_action_exhaustive_evidence_authorized"] is False
    assert plan["production_write_authorized"] is False
    assert plan["candidate_partition"] == {
        "all": 3,
        "top_level_enrichable": 1,
        "nested_only_amendments": 2,
    }
    assert len(plan["type_sitemaps"]) == 32
    assert [row["canonical_key"] for row in plan["record_tasks"]] == [
        "119:hr:7"
    ]
    assert all(
        row["candidate_retained"] for row in plan["unsupported_candidates"]
    )
    assert plan["inputs"]["exclusion_ledger"]["sha256"] == (
        billstatus.sha256_file(exclusions)
    )
    assert plan["inputs"]["exclusion_provenance"] == [
        {
            "path": str(reviewed_exclusions),
            "sha256": billstatus.sha256_file(reviewed_exclusions),
            "bytes": reviewed_exclusions.stat().st_size,
        }
    ]


def test_plan_fails_closed_on_quorum_matrix_and_candidate_count(
    tmp_path: Path,
) -> None:
    discovery = tmp_path / "records.ndjson"
    exclusions = tmp_path / "exclusions.ndjson"
    write_ndjson(
        discovery,
        [
            {
                "congress": 119,
                "type": "s",
                "number": 1,
                "canonical_key": "119:s:1",
            }
        ],
    )
    exclusions.write_text("", encoding="utf-8")
    plan = billstatus.build_fetch_plan(discovery, exclusions)

    bad = dict(plan, quorum_used=True)
    with raises(billstatus.ContractError, match="Quorum"):
        billstatus.validate_fetch_plan(bad)

    bad = dict(plan, latest_action_exhaustive_evidence_authorized=True)
    with raises(billstatus.ContractError, match="latest_action"):
        billstatus.validate_fetch_plan(bad)

    bad = dict(plan, type_sitemaps=plan["type_sitemaps"][:-1])
    with raises(billstatus.ContractError, match="32/32"):
        billstatus.validate_fetch_plan(bad)

    bad = json.loads(json.dumps(plan))
    bad["candidate_partition"]["all"] += 1
    with raises(billstatus.ContractError, match="candidate count"):
        billstatus.validate_fetch_plan(bad)

    bad = json.loads(json.dumps(plan))
    bad["record_tasks"][0]["canonical_key"] = "119:s:999"
    with raises(billstatus.ContractError, match="planned keys"):
        billstatus.validate_fetch_plan(bad)


def test_unknown_or_duplicate_discovery_records_fail_closed(
    tmp_path: Path,
) -> None:
    exclusions = tmp_path / "exclusions.ndjson"
    exclusions.write_text("", encoding="utf-8")
    discovery = tmp_path / "records.ndjson"
    duplicate = {
        "congress": 119,
        "type": "hr",
        "number": 1,
        "canonical_key": "119:hr:1",
    }
    write_ndjson(discovery, [duplicate, duplicate])
    with raises(billstatus.ContractError, match="duplicate"):
        billstatus.build_fetch_plan(discovery, exclusions)

    write_ndjson(
        discovery,
        [
            {
                "congress": 119,
                "type": "treaty",
                "number": 1,
                "canonical_key": "119:treaty:1",
            }
        ],
    )
    with raises(billstatus.ContractError, match="unexpected"):
        billstatus.build_fetch_plan(discovery, exclusions)


def test_snapshot_requires_exact_selected_manifest_query_union(
    tmp_path: Path,
) -> None:
    run = tmp_path / "strict_run"
    queries = run / "queries"
    queries.mkdir(parents=True)
    source = {
        "record_kind": "bill",
        "congress": 119,
        "type": "hr",
        "number": 7,
        "canonical_key": "119:hr:7",
        "source_query_id": "q1",
        "source_page": 1,
        "source_rank": 1,
    }
    records_hash = billstatus.stable_json_sha256([source])
    query = {
        "schema": "congress-advanced-search-query-v1",
        "query_id": "q1",
        "registry_sha256": "a" * 64,
        "record_kind": "category",
        "scoring_category": True,
        "result_total": 1,
        "unique_record_count": 1,
        "records_sha256": records_hash,
        "validation": {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
        },
        "records": [source],
    }
    query_path = queries / "q1.json"
    query_path.write_text(json.dumps(query), encoding="utf-8")
    strict = run / "manifest.partial.json"
    strict.write_text(
        json.dumps(
            {
                "primary_instrument": (
                    "Congress.gov Advanced Search - Legislation"
                ),
                "quorum_used": False,
                "registry_sha256": "a" * 64,
                "selected_query_ids": ["q1"],
                "queries": [
                    {
                        "query_id": "q1",
                        "query_file": "queries/q1.json",
                        "result_total": 1,
                        "unique_record_count": 1,
                        "records_sha256": records_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    aggregate = dict(source)
    aggregate.pop("source_query_id")
    aggregate.pop("source_page")
    aggregate.pop("source_rank")
    aggregate.update(
        {
            "matched_scoring_categories": ["q1"],
            "matched_monitor_categories": [],
            "matched_coverage_probes": [],
        }
    )
    discovery = run / "records.ndjson"
    exclusions = run / "exclusions.ndjson"
    exclusions.write_text("", encoding="utf-8")
    write_ndjson(discovery, [aggregate])
    plan = billstatus.build_fetch_plan(
        discovery, exclusions, strict_manifest_path=strict
    )
    billstatus.validate_snapshot_plan(
        plan, expected_strict_manifest=strict
    )

    extra = {
        **aggregate,
        "number": 8,
        "canonical_key": "119:hr:8",
    }
    write_ndjson(discovery, [aggregate, extra])
    with raises(billstatus.ContractError, match="exact strict-manifest"):
        billstatus.build_fetch_plan(
            discovery, exclusions, strict_manifest_path=strict
        )
    memberships = run / "memberships.ndjson"
    membership_rows = [
        {"canonical_key": "119:hr:8", "query_id": "historical_q"}
    ]
    write_ndjson(memberships, membership_rows)
    cumulative = run / "manifest.json"
    cumulative.write_text(
        json.dumps(
            {
                "union": {
                    "records_file": discovery.name,
                    "unique_record_count": 2,
                    "records_sha256": billstatus.stable_json_sha256(
                        [aggregate, extra]
                    ),
                    "memberships_file": memberships.name,
                    "membership_count": 1,
                    "memberships_sha256": (
                        billstatus.stable_json_sha256(membership_rows)
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    cumulative_plan = billstatus.build_fetch_plan(
        discovery,
        exclusions,
        strict_manifest_path=strict,
        cumulative_manifest_path=cumulative,
        cumulative_memberships_path=memberships,
    )
    billstatus.validate_snapshot_plan(
        cumulative_plan, expected_strict_manifest=strict
    )
    cumulative_state = cumulative_plan["snapshot_contract"][
        "strict_manifest_query_union"
    ]
    assert cumulative_state["closure_mode"] == (
        "cumulative_historical_aggregate_plus_selected_snapshot"
    )
    assert cumulative_state["selected_strict_candidate_count"] == 1
    assert cumulative_state["historical_snapshot_only_retained_count"] == 1

    write_ndjson(discovery, [])
    with raises(billstatus.ContractError, match="exact strict-manifest"):
        billstatus.build_fetch_plan(
            discovery, exclusions, strict_manifest_path=strict
        )

    write_ndjson(discovery, [aggregate])
    query["records"][0]["number"] = 99
    query_path.write_text(json.dumps(query), encoding="utf-8")
    with raises(billstatus.ContractError, match="reconciliation drift"):
        billstatus.validate_snapshot_plan(
            plan, expected_strict_manifest=strict
        )


def test_v3_parser_preserves_roles_votes_content_and_provenance(
    tmp_path: Path,
) -> None:
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<billStatus>
  <version>3.0.0</version>
  <bill>
    <number>42</number><congress>119</congress><type>HR</type>
    <updateDate>2026-07-20T12:00:00Z</updateDate>
    <updateDateIncludingText>2026-07-20T12:00:00Z</updateDateIncludingText>
    <titles><item><title>Official title</title>
      <titleType>Official Title as Introduced</titleType>
      <billTextVersionCode>IH</billTextVersionCode>
    </item></titles>
    <summaries><summary><versionCode>00</versionCode>
      <actionDate>2025-01-03</actionDate>
      <text><![CDATA[<p>Requires a staged audit.</p>]]></text>
    </summary></summaries>
    <sponsors><item><bioguideId>S000001</bioguideId>
      <fullName>Rep. Sponsor</fullName></item></sponsors>
    <cosponsors>
      <item><bioguideId>O000001</bioguideId>
        <isOriginalCosponsor>True</isOriginalCosponsor>
        <sponsorshipDate>2025-01-03</sponsorshipDate></item>
      <item><bioguideId>L000001</bioguideId>
        <isOriginalCosponsor>False</isOriginalCosponsor>
        <sponsorshipDate>2025-02-01</sponsorshipDate></item>
      <item><bioguideId>W000001</bioguideId>
        <isOriginalCosponsor>True</isOriginalCosponsor>
        <sponsorshipDate>2025-01-03</sponsorshipDate>
        <sponsorshipWithdrawnDate>2025-03-01</sponsorshipWithdrawnDate>
      </item>
    </cosponsors>
    <actions><item><actionDate>2025-04-01</actionDate>
      <text>Passed by recorded vote.</text>
      <recordedVotes><recordedVote><chamber>House</chamber>
        <congress>119</congress><date>2025-04-01T12:00:00Z</date>
        <rollNumber>10</rollNumber><sessionNumber>1</sessionNumber>
        <url>https://clerk.house.gov/evs/2025/roll010.xml</url>
      </recordedVote></recordedVotes>
    </item></actions>
    <textVersions><item><date>2025-01-03</date><type>Introduced</type>
      <formats><item><url>https://www.govinfo.gov/content/pkg/x/xml/x.xml</url>
      </item></formats></item></textVersions>
  </bill>
</billStatus>"""
    path = tmp_path / "BILLSTATUS-119hr42.xml"
    path.write_bytes(xml)
    digest = hashlib.sha256(xml).hexdigest()
    parsed = billstatus.parse_billstatus_xml(
        path,
        source_url=billstatus.expected_record_url(119, "hr", 42),
        sitemap_lastmod="2026-07-20T13:00:00Z",
        expected_sha256=digest,
    )

    assert parsed["canonical_key"] == "119:hr:42"
    assert parsed["titles"][0]["title"] == "Official title"
    assert "Requires a staged audit" in parsed["summaries"][0]["text"]
    assert len(parsed["sponsors"]) == 1
    assert parsed["sponsors"][0]["relationship_role"] == "Sponsor"
    assert parsed["sponsors"][0]["role_provenance_complete"] is True
    assert [row["relationship_status"] for row in parsed["cosponsors"]] == [
        "current",
        "current",
        "withdrawn",
    ]
    assert [row["relationship_role"] for row in parsed["cosponsors"]] == [
        "Original Cosponsor",
        "Cosponsor",
        "Original Cosponsor",
    ]
    assert all(
        row["role_provenance_complete"] for row in parsed["cosponsors"]
    )
    assert parsed["cosponsors"][0]["original_cosponsor"] is True
    assert parsed["cosponsors"][1]["original_cosponsor"] is False
    assert parsed["cosponsors"][2]["original_cosponsor"] is True
    assert parsed["recorded_votes"][0]["roll_number"] == "10"
    assert parsed["record_update_date_including_text"].endswith("Z")
    assert parsed["provenance"]["source_sha256"] == digest


def test_xml_validation_fails_closed_on_hash_schema_identity_and_entities(
    tmp_path: Path,
) -> None:
    base = (
        "<billStatus><version>{version}</version><bill>"
        "<number>1</number><congress>119</congress><type>HR</type>"
        "<updateDate>2026-01-01T00:00:00Z</updateDate>"
        "</bill></billStatus>"
    )
    path = tmp_path / "record.xml"
    path.write_text(base.format(version="3.0.0"), encoding="utf-8")
    digest = billstatus.sha256_file(path)
    with raises(billstatus.ContractError, match="SHA-256"):
        billstatus.parse_billstatus_xml(
            path,
            source_url=billstatus.expected_record_url(119, "hr", 1),
            sitemap_lastmod="2026-01-01T01:00:00Z",
            expected_sha256="0" * 64,
        )
    with raises(billstatus.ContractError, match="identity"):
        billstatus.parse_billstatus_xml(
            path,
            source_url=billstatus.expected_record_url(119, "hr", 2),
            sitemap_lastmod="2026-01-01T01:00:00Z",
            expected_sha256=digest,
        )

    path.write_text(base.format(version="4.0.0"), encoding="utf-8")
    with raises(billstatus.ContractError, match="unapproved"):
        billstatus.parse_billstatus_xml(
            path,
            source_url=billstatus.expected_record_url(119, "hr", 1),
            sitemap_lastmod="2026-01-01T01:00:00Z",
            expected_sha256=billstatus.sha256_file(path),
        )

    path.write_text(
        (" " * 100_001)
        + "<!DOCTYPE x [<!ENTITY bad 'x'>]>"
        + base.format(version="3.0.0"),
        encoding="utf-8",
    )
    with raises(billstatus.ContractError, match="DOCTYPE"):
        billstatus.parse_billstatus_xml(
            path,
            source_url=billstatus.expected_record_url(119, "hr", 1),
            sitemap_lastmod="2026-01-01T01:00:00Z",
            expected_sha256=billstatus.sha256_file(path),
        )

    mismatched_vote = b"""<billStatus><version>3.0.0</version><bill>
      <number>1</number><congress>119</congress><type>HR</type>
      <updateDate>2026-01-01T00:00:00Z</updateDate>
      <actions><item><recordedVotes><recordedVote>
        <chamber>House</chamber><congress>118</congress>
        <rollNumber>1</rollNumber><sessionNumber>1</sessionNumber>
      </recordedVote></recordedVotes></item></actions>
    </bill></billStatus>"""
    path.write_bytes(mismatched_vote)
    with raises(billstatus.ContractError, match="recorded vote Congress"):
        billstatus.parse_billstatus_xml(
            path,
            source_url=billstatus.expected_record_url(119, "hr", 1),
            sitemap_lastmod="2026-01-01T01:00:00Z",
            expected_sha256=billstatus.sha256_file(path),
        )


def test_plan_output_is_new_staging_json_only(tmp_path: Path) -> None:
    with raises(billstatus.ContractError, match="audit/staging"):
        billstatus.validate_staging_plan_output(
            tmp_path / "production" / "vote_category_lean.json"
        )
    with raises(billstatus.ContractError, match="audit/staging"):
        billstatus.validate_staging_plan_output(
            tmp_path / "billstatus_staging_plan.json"
        )
    with raises(billstatus.ContractError, match="JSON file"):
        billstatus.validate_staging_plan_output(
            tmp_path / "staging" / "plan.txt"
        )
    existing = tmp_path / "staging" / "existing.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("{}\n", encoding="utf-8")
    with raises(billstatus.ContractError, match="already exists"):
        billstatus.validate_staging_plan_output(existing)

    candidate = tmp_path / "staging" / "new_plan.json"
    assert billstatus.validate_staging_plan_output(candidate) == (
        candidate.resolve()
    )
    assert not candidate.exists()


if __name__ == "__main__":
    tests = [
        test_fetch_plan_is_discovery_only_and_preserves_amendments,
        test_plan_fails_closed_on_quorum_matrix_and_candidate_count,
        test_unknown_or_duplicate_discovery_records_fail_closed,
        test_snapshot_requires_exact_selected_manifest_query_union,
        test_v3_parser_preserves_roles_votes_content_and_provenance,
        test_xml_validation_fails_closed_on_hash_schema_identity_and_entities,
        test_plan_output_is_new_staging_json_only,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(
            prefix="billstatus-contract-test-"
        ) as directory:
            test(Path(directory))
    print(f"{len(tests)} BILLSTATUS staging tests passed")
