from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import run_billstatus_bulk_enrichment as WORKER
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS
import audit.audit_billstatus_enrichment_completeness as COMPLETENESS
from legislation import run_legislation_presync_audit as PRESYNC


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def build_plan(tmp_path: Path) -> tuple[Path, str]:
    run = tmp_path / "discovery"
    queries = run / "queries"
    queries.mkdir(parents=True)
    sources = [
        {
            "canonical_key": "119:hr:7",
            "congress": 119,
            "type": "hr",
            "number": 7,
        },
        {
            "canonical_key": "119:samdt:9",
            "congress": 119,
            "type": "samdt",
            "number": 9,
        },
    ]
    query_hash = BILLSTATUS.stable_json_sha256(sources)
    query = {
        "schema": "congress-advanced-search-query-v1",
        "query_id": "q1",
        "registry_sha256": "a" * 64,
        "record_kind": "category",
        "scoring_category": True,
        "result_total": 2,
        "unique_record_count": 2,
        "records_sha256": query_hash,
        "validation": {
            "exact_result_total": True,
            "unique_result_total": True,
            "every_page_exact": True,
        },
        "records": sources,
    }
    (queries / "q1.json").write_text(json.dumps(query), encoding="utf-8")
    strict = run / "manifest.partial.json"
    strict.write_text(
        json.dumps(
            {
                "primary_instrument": BILLSTATUS.PRIMARY_DISCOVERY,
                "quorum_used": False,
                "registry_sha256": "a" * 64,
                "selected_query_ids": ["q1"],
                "queries": [
                    {
                        "query_id": "q1",
                        "query_file": "queries/q1.json",
                        "result_total": 2,
                        "unique_record_count": 2,
                        "records_sha256": query_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    aggregate = [
        {
            **source,
            "matched_scoring_categories": ["q1"],
            "matched_monitor_categories": [],
            "matched_coverage_probes": [],
        }
        for source in sources
    ]
    records = run / "records.ndjson"
    exclusions = run / "exclusions.ndjson"
    write_ndjson(records, aggregate)
    exclusions.write_text("", encoding="utf-8")
    plan = BILLSTATUS.build_fetch_plan(
        records, exclusions, strict_manifest_path=strict
    )
    BILLSTATUS.validate_snapshot_plan(
        plan, expected_strict_manifest=strict
    )
    plan_path = tmp_path / "audit" / "snapshot_plan.json"
    plan_path.parent.mkdir()
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    return plan_path, BILLSTATUS.expected_record_url(119, "hr", 7)


def test_offline_fetch_resume_roles_and_amendment_retention(
    tmp_path: Path,
) -> None:
    plan_path, record_url = build_plan(tmp_path)
    calls: list[str] = []
    record_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<billStatus><version>3.0.0</version><bill>
<number>7</number><congress>119</congress><type>HR</type>
<updateDate>2026-07-20T12:00:00Z</updateDate>
<titles><item><title>Official title</title>
<titleType>Official Title as Introduced</titleType></item></titles>
<summaries><summary><versionCode>00</versionCode>
<actionDate>2025-01-03</actionDate><text>Summary text.</text>
</summary></summaries>
<sponsors><item><bioguideId>S000001</bioguideId>
<fullName>Rep. Sponsor</fullName></item></sponsors>
<onBehalfOfSponsor><item><bioguideId>B000001</bioguideId>
<fullName>Rep. Beneficial Sponsor</fullName>
<type>Introduced on behalf of</type></item></onBehalfOfSponsor>
<cosponsors><item><bioguideId>C000001</bioguideId>
<isOriginalCosponsor>True</isOriginalCosponsor>
<sponsorshipDate>2025-01-03</sponsorshipDate>
<sponsorshipWithdrawnDate>2025-01-10</sponsorshipWithdrawnDate>
</item><item><bioguideId>C000001</bioguideId>
<fullName>Rep. Returning Cosponsor</fullName>
<isOriginalCosponsor>False</isOriginalCosponsor>
<sponsorshipDate>2025-01-11</sponsorshipDate></item></cosponsors>
<policyArea><name>Finance and Financial Sector</name></policyArea>
<subjects><legislativeSubjects><item>
<name>Credit unions</name></item></legislativeSubjects></subjects>
<committees><billCommittees><item>
<name>House Financial Services Committee</name>
</item></billCommittees></committees>
<actions><item><actionDate>2025-04-01</actionDate><text>Passed.</text>
<recordedVotes><recordedVote><chamber>House</chamber>
<congress>119</congress><date>2025-04-01T12:00:00Z</date>
<rollNumber>10</rollNumber><sessionNumber>1</sessionNumber>
<url>https://clerk.house.gov/evs/2025/roll010.xml</url>
</recordedVote></recordedVotes></item></actions>
<textVersions><item><date>2025-01-03</date><type>Introduced</type>
<formats><item><url>https://www.govinfo.gov/content/pkg/x/xml/x.xml</url>
</item></formats></item>
<item><date>2025-04-01</date><type>Engrossed</type></item>
</textVersions>
</bill></billStatus>"""

    def fetch(url: str) -> WORKER.FetchResponse:
        calls.append(url)
        if url == record_url:
            body = record_xml
        else:
            stem = url.split("/BILLSTATUS/")[-1].split("/")[0]
            congress = int(stem[:3])
            measure_type = stem[3:]
            loc = (
                record_url
                if congress == 119 and measure_type == "hr"
                else BILLSTATUS.expected_record_url(
                    congress, measure_type, 999999
                )
            )
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"<url><loc>{loc}</loc>"
                "<lastmod>2026-07-20T13:00:00Z</lastmod></url>"
                "</urlset>"
            ).encode("utf-8")
        return WORKER.FetchResponse(
            body=body,
            status=200,
            final_url=url,
            headers={"content-type": "application/xml"},
        )

    out = tmp_path / "audits" / "fetch_runs"
    first = WORKER.run(
        plan_path=plan_path,
        out_root=out,
        workers=2,
        fetch=fetch,
        enforce_workspace_audits=False,
    )
    assert first["status"] == "COMPLETE"
    assert len(calls) == 33
    assert first["partition"] == {
        "all_candidates": 2,
        "top_level_planned": 1,
        "top_level_selected_this_run": 1,
        "top_level_completed": 1,
        "top_level_failed": 0,
        "nested_only_amendments_retained_unresolved": 1,
        "remaining_top_level": 0,
    }
    receipt = first["record_receipts"][0]
    parsed = json.loads(Path(receipt["parsed_path"]).read_text(encoding="utf-8"))
    assert parsed["titles"][0]["title"] == "Official title"
    assert parsed["sponsors"][0]["relationship_role"] == "Sponsor"
    assert parsed["cosponsors"][0]["relationship_role"] == "Original Cosponsor"
    assert parsed["recorded_votes"][0]["roll_number"] == "10"
    assert parsed["text_versions"][0]["urls"][0].startswith(
        "https://www.govinfo.gov/"
    )

    def forbidden_fetch(url: str) -> WORKER.FetchResponse:
        raise AssertionError(f"resume attempted network: {url}")

    second = WORKER.run(
        plan_path=plan_path,
        out_root=out,
        workers=2,
        fetch=forbidden_fetch,
        enforce_workspace_audits=False,
    )
    assert second["status"] == "COMPLETE"
    assert second["network_executed"] is False
    assert second["sitemaps"]["reused"] == 32
    assert len(second["record_receipts"]) == 1

    audit, candidate_gaps, field_gaps, role_gaps = (
        COMPLETENESS.build_audit(Path(second["manifest_path"]))
    )
    assert audit["closure"]["candidate_partition_closed"] is True
    assert audit["closure"]["top_level_parsed_records_validated"] == 1
    assert audit["content_counts"]["title_items"] == 1
    assert audit["content_counts"]["summary_items"] == 1
    assert audit["content_counts"]["recorded_vote_items"] == 1
    assert audit["relationship_counts"]["sponsor_relationships"] == 1
    assert (
        audit["relationship_counts"]["original_cosponsor_relationships"]
        == 1
    )
    assert any(
        row["gap_kind"]
        == "nested_amendment_has_no_standalone_bulk_package"
        for row in candidate_gaps
    )
    assert any(
        row["gap_kind"] == "missing_update_date_including_text"
        for row in field_gaps
    )
    assert any(
        row["gap_kind"] == "relationship_full_name_missing"
        for row in role_gaps
    )
    assert any(
        row["gap_kind"] == "on_behalf_of_sponsor_not_structured"
        and row["detail"]["bioguide_id"] == "B000001"
        for row in role_gaps
    )
    assert any(
        row["gap_kind"] == "repeated_member_relationship_history"
        and row["detail"]["bioguide_id"] == "C000001"
        and row["detail"]["event_count"] == 2
        for row in role_gaps
    )
    assert {
        "source_policy_area_not_structured",
        "source_legislative_subjects_not_structured",
        "source_committees_not_structured",
    }.issubset({row["gap_kind"] for row in candidate_gaps})
    assert any(
        row["gap_kind"] == "text_version_urls_missing"
        and row["item_index"] == 1
        for row in field_gaps
    )
    audit_path = COMPLETENESS.write_audit(
        tmp_path / "audits" / "completeness",
        audit,
        candidate_gaps,
        field_gaps,
        role_gaps,
    )
    validated = COMPLETENESS.validate_audit(audit_path)
    assert validated["status"] == COMPLETENESS.STATUS
    assert validated["candidate_exclusion_performed"] is False
    assert validated["automatic_scoring_performed"] is False
    presync_passed, presync_state = (
        PRESYNC.validate_billstatus_enrichment_completeness(
            audit_path,
            expected_plan_path=plan_path,
            expected_worker_manifest_path=Path(second["manifest_path"]),
        )
    )
    assert presync_passed is True
    assert presync_state["status"] == "CURRENT"
    assert presync_state["gap_ledger_output_set_closed"] is True
    assert (
        presync_state["gap_rows_retained_neutral_and_non_scoring"] is True
    )
    assert presync_state["relationship_partition_closed"] is True
    assert set(presync_state["gap_ledgers"]) == set(
        PRESYNC.EXPECTED_BILLSTATUS_GAP_OUTPUTS
    )
    original_audit = audit_path.read_bytes()
    audit_payload = json.loads(original_audit.decode("utf-8"))

    missing_output = json.loads(original_audit.decode("utf-8"))
    missing_output["outputs"].pop("candidate_visibility_gaps")
    with mock.patch.object(
        PRESYNC.BILLSTATUS_COMPLETENESS,
        "validate_audit",
        return_value=missing_output,
    ):
        passed, state = PRESYNC.validate_billstatus_enrichment_completeness(
            audit_path,
            expected_plan_path=plan_path,
            expected_worker_manifest_path=Path(second["manifest_path"]),
        )
    assert passed is False
    assert any("output set drifted" in issue for issue in state["issues"])

    stale_worker = json.loads(original_audit.decode("utf-8"))
    stale_worker["inputs"]["worker_producer"]["sha256"] = "0" * 64
    with mock.patch.object(
        PRESYNC.BILLSTATUS_COMPLETENESS,
        "validate_audit",
        return_value=stale_worker,
    ):
        passed, state = PRESYNC.validate_billstatus_enrichment_completeness(
            audit_path,
            expected_plan_path=plan_path,
            expected_worker_manifest_path=Path(second["manifest_path"]),
        )
    assert passed is False
    assert any(
        "worker producer receipt drifted" in issue
        for issue in state["issues"]
    )

    partition_drift = json.loads(original_audit.decode("utf-8"))
    partition_drift["relationship_counts"]["relationship_rows"] += 1
    with mock.patch.object(
        PRESYNC.BILLSTATUS_COMPLETENESS,
        "validate_audit",
        return_value=partition_drift,
    ):
        passed, state = PRESYNC.validate_billstatus_enrichment_completeness(
            audit_path,
            expected_plan_path=plan_path,
            expected_worker_manifest_path=Path(second["manifest_path"]),
        )
    assert passed is False
    assert any(
        "relationship partition drifted" in issue
        for issue in state["issues"]
    )

    candidate_ledger = Path(
        audit_payload["outputs"]["candidate_visibility_gaps"]["path"]
    )
    original_candidate_ledger = candidate_ledger.read_bytes()
    candidate_lines = original_candidate_ledger.decode("utf-8").splitlines()
    forged_gap = json.loads(candidate_lines[0])
    forged_gap["automatic_scoring_authorized"] = True
    candidate_lines[0] = json.dumps(forged_gap, separators=(",", ":"))
    candidate_ledger.write_text(
        "\n".join(candidate_lines) + "\n",
        encoding="utf-8",
    )
    forged_audit = json.loads(original_audit.decode("utf-8"))
    forged_receipt = forged_audit["outputs"]["candidate_visibility_gaps"]
    forged_receipt["bytes"] = candidate_ledger.stat().st_size
    forged_receipt["sha256"] = PRESYNC.sha256_file(candidate_ledger)
    with mock.patch.object(
        PRESYNC.BILLSTATUS_COMPLETENESS,
        "validate_audit",
        return_value=forged_audit,
    ):
        passed, state = PRESYNC.validate_billstatus_enrichment_completeness(
            audit_path,
            expected_plan_path=plan_path,
            expected_worker_manifest_path=Path(second["manifest_path"]),
        )
    assert passed is False
    assert any("row safety contract drifted" in issue for issue in state["issues"])

    candidate_ledger.write_bytes(original_candidate_ledger)
    audit_path.write_bytes(original_audit)
    role_ledger = Path(
        validated["outputs"]["role_provenance_gaps"]["path"]
    )
    original_role_ledger = role_ledger.read_text(encoding="utf-8")
    role_ledger.write_text(
        original_role_ledger + "{}\n",
        encoding="utf-8",
    )
    try:
        COMPLETENESS.validate_audit(audit_path)
    except COMPLETENESS.CompletenessError as error:
        assert "ledger drift" in str(error)
    else:
        raise AssertionError("tampered completeness ledger validated")
    role_ledger.write_text(original_role_ledger, encoding="utf-8")
    audit_document = json.loads(audit_path.read_text(encoding="utf-8"))
    unsafe_rows = [
        json.loads(line)
        for line in original_role_ledger.splitlines()
        if line.strip()
    ]
    unsafe_rows[0]["automatic_scoring_authorized"] = True
    write_ndjson(role_ledger, unsafe_rows)
    audit_document["outputs"]["role_provenance_gaps"] = {
        **BILLSTATUS.file_receipt(role_ledger),
        "record_count": len(unsafe_rows),
    }
    audit_path.write_text(
        json.dumps(audit_document, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        COMPLETENESS.validate_audit(audit_path)
    except COMPLETENESS.CompletenessError as error:
        assert "unsafe gap row authority" in str(error)
    else:
        raise AssertionError("scoring-authorized gap row validated")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(
        prefix="billstatus-worker-test-"
    ) as directory:
        test_offline_fetch_resume_roles_and_amendment_retention(
            Path(directory)
        )
    print("1 BILLSTATUS worker test passed")
