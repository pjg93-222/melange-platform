from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import govinfo_discovery_fallback as FALLBACK
from legislation import refresh_legislation_discovery as REFRESH
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def fixture_status(
    number: int, *, text_url: str | None = None, amendments: str = ""
) -> bytes:
    versions = (
        f"<textVersions><item><date>2026-08-30</date><type>Introduced in House</type>"
        f"<formats><item><url>{text_url}</url></item></formats></item></textVersions>"
        if text_url
        else ""
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<billStatus>
  <version>3.0.0</version>
  <bill>
    <congress>119</congress><type>HR</type><number>{number}</number>
    <updateDate>2026-08-30T12:00:00Z</updateDate>
    <titles><item><title>Safe Local Finance Act</title><titleType>Official Title as Introduced</titleType></item></titles>
    <sponsors><item><bioguideId>T000001</bioguideId><fullName>Test, Terry [Rep.-I-TS-1]</fullName></item></sponsors>
    <cosponsors><item><bioguideId>C000001</bioguideId><fullName>Cosponsor, Casey</fullName><isOriginalCosponsor>true</isOriginalCosponsor></item></cosponsors>
    <actions><item><actionDate>2026-08-30</actionDate><text>Introduced in House</text></item></actions>
    {versions}
    {amendments}
  </bill>
</billStatus>
""".encode()


def setup_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, bytes]]:
    registry_path = tmp_path / "registry.json"
    registry = {
        "collection_contract": {
            "congresses": [119],
            "legislation_types": ["hr", "hamdt"],
            "required_window_start": "2020-07-25",
            "required_window_end": "2026-09-04",
        },
        "categories": [
            {
                "id": "community_finance",
                "label": "Community Finance",
                "scoring_category": True,
                "query_terms": [
                    {
                        "kind": "outline_identifier",
                        "query": "community development finance",
                    }
                ],
            }
        ],
        "coverage_probes": [
            {
                "id": "credit_union_recall",
                "label": "Credit Union Recall",
                "scoring_category": False,
                "record_kind": "coverage_probe",
                "query_terms": [
                    {"kind": "coverage_probe", "query": '"credit union"'}
                ],
            }
        ],
    }
    write_json(registry_path, registry)
    registry_sha = FALLBACK.sha256_bytes(registry_path.read_bytes())

    baseline = tmp_path / "baseline"
    baseline_record = {
        "record_kind": "bill",
        "congress": 119,
        "type": "hr",
        "number": 1,
        "canonical_key": "119:hr:1",
        "title": "Never mutate this baseline row",
        "matched_scoring_categories": [],
        "matched_monitor_categories": [],
        "matched_coverage_probes": [],
    }
    baseline_membership = {
        "query_id": "old_query",
        "canonical_key": "119:hr:1",
        "result_url": "https://www.congress.gov/bill/119th-congress/house-bill/1",
    }
    write_ndjson(baseline / "congress_advanced_records.ndjson", [baseline_record])
    write_ndjson(
        baseline / "congress_advanced_memberships.ndjson", [baseline_membership]
    )
    write_ndjson(baseline / "coverage_gap_ledger.ndjson", [])
    write_ndjson(baseline / "exclusion_ledger.ndjson", [])
    write_json(baseline / "scope_and_exclusion_contract.json", {"baseline": True})
    write_json(
        baseline / "manifest.json",
        {
            "status": "complete",
            "completed_at": "2026-07-26T19:04:16Z",
            "registry_sha256": registry_sha,
            "union": {
                "records_file": "congress_advanced_records.ndjson",
                "unique_record_count": 1,
                "records_sha256": FALLBACK.stable_hash([baseline_record]),
                "memberships_file": "congress_advanced_memberships.ndjson",
                "membership_count": 1,
                "memberships_sha256": FALLBACK.stable_hash([baseline_membership]),
            },
        },
    )

    out = tmp_path / "out"
    browser_failure = tmp_path / "browser_failure.json"
    write_json(
        browser_failure,
        {
            "status": "failed",
            "automatic_verification_timeout": {
                "collection_stopped_without_result_acceptance": True
            },
            "error": {"code": "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT"},
        },
    )
    sitemap_url = BILLSTATUS.expected_sitemap_url(119, "hr")
    old_url = BILLSTATUS.expected_record_url(119, "hr", 1)
    new_url = BILLSTATUS.expected_record_url(119, "hr", 99)
    text_url = (
        "https://www.govinfo.gov/content/pkg/BILLS-119hr99ih/"
        "uslm/BILLS-119hr99ih.xml"
    )
    sitemap = f"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{old_url}</loc><lastmod>2026-08-30</lastmod></url>
  <url><loc>{new_url}</loc><lastmod>2026-08-30</lastmod></url>
</urlset>
""".encode()
    bill_text = b"""<?xml version="1.0"?>
<!DOCTYPE bill PUBLIC "-//US Congress//DTDs/bill.dtd//EN" "bill.dtd">
<bill><legis-body>This community development finance proposal serves every credit union.</legis-body></bill>
"""
    amendments = """
    <amendments>
      <amendment>
        <number>700</number><congress>119</congress><type>HAMDT</type>
        <purpose>To expand community development finance options.</purpose>
        <updateDate>2026-08-31T12:00:00Z</updateDate>
        <sponsors><item><bioguideId>A000001</bioguideId><fullName>Amender, Alex</fullName></item></sponsors>
        <proposedDate>2026-08-31T04:00:00Z</proposedDate>
        <amendedBill><congress>119</congress><type>HR</type><number>99</number></amendedBill>
      </amendment>
      <amendment>
        <number>701</number><congress>119</congress><type>HAMDT</type>
        <purpose>To rename a post office.</purpose>
        <updateDate>2026-08-31T12:00:00Z</updateDate>
        <amendedBill><congress>119</congress><type>HR</type><number>99</number></amendedBill>
      </amendment>
    </amendments>
    """
    return (
        registry_path,
        baseline,
        out,
        browser_failure,
        {
            sitemap_url: sitemap,
            old_url: fixture_status(1),
            new_url: fixture_status(99, text_url=text_url, amendments=amendments),
            text_url: bill_text,
        },
    )


def test_official_fallback_is_cumulative_pending_and_idempotent(tmp_path: Path) -> None:
    registry, baseline, out, failure, responses = setup_fixture(tmp_path)

    def fetch(url: str, limit: int) -> bytes:
        assert len(responses[url]) <= limit
        return responses[url]

    first = FALLBACK.run_fallback(
        registry_path=registry,
        baseline_dir=baseline,
        out_dir=out,
        browser_failure_path=failure,
        workers=2,
        fetch=fetch,
    )
    records = FALLBACK._read_ndjson(out / "congress_advanced_records.ndjson")
    memberships = FALLBACK._read_ndjson(
        out / "congress_advanced_memberships.ndjson"
    )
    assert len(records) == 3
    assert records[0]["title"] == "Never mutate this baseline row"
    bill = next(row for row in records if row["canonical_key"] == "119:hr:99")
    amendment = next(row for row in records if row["canonical_key"] == "119:hamdt:700")
    assert bill["candidate_status"] == "included_unreviewed_candidate"
    assert bill["fallback_discovery"]["scoring_eligible"] is False
    assert bill["matched_scoring_categories"] == ["community_finance"]
    assert bill["matched_coverage_probes"] == ["credit_union_recall"]
    assert amendment["matched_scoring_categories"] == ["community_finance"]
    assert "parent_bill_title" in amendment["fallback_discovery"]["matching_evidence_scope"]
    assert not any(row["canonical_key"] == "119:hamdt:701" for row in records)
    assert len(memberships) == 4
    assert all(
        row.get("scoring_eligible") is False
        for row in memberships
        if row["canonical_key"] == "119:hr:99"
    )
    assert first["immutable_baseline"]["preexisting_records_preserved_losslessly"]
    receipt = json.loads(
        (out / "govinfo_fallback_receipt.json").read_text()
    )
    assert "hamdt" in receipt["types_without_top_level_sitemaps"]
    assert receipt["scan"]["new_matching_pending_amendment_candidates"] == 1

    records_before = (out / "congress_advanced_records.ndjson").read_bytes()
    memberships_before = (out / "congress_advanced_memberships.ndjson").read_bytes()
    second = FALLBACK.run_fallback(
        registry_path=registry,
        baseline_dir=baseline,
        out_dir=out,
        browser_failure_path=failure,
        workers=2,
        fetch=fetch,
    )
    assert second["union"]["unique_record_count"] == 3
    assert (out / "congress_advanced_records.ndjson").read_bytes() == records_before
    assert (
        out / "congress_advanced_memberships.ndjson"
    ).read_bytes() == memberships_before


def test_malformed_official_source_fails_before_union_replacement(tmp_path: Path) -> None:
    registry, baseline, out, failure, responses = setup_fixture(tmp_path)

    def fetch(url: str, limit: int) -> bytes:
        return responses[url]

    FALLBACK.run_fallback(
        registry_path=registry,
        baseline_dir=baseline,
        out_dir=out,
        browser_failure_path=failure,
        workers=1,
        fetch=fetch,
    )
    before = (out / "congress_advanced_records.ndjson").read_bytes()
    sitemap_url = BILLSTATUS.expected_sitemap_url(119, "hr")
    responses[sitemap_url] = b"<urlset><url>"
    with pytest.raises(FALLBACK.FallbackError, match="malformed sitemap"):
        FALLBACK.run_fallback(
            registry_path=registry,
            baseline_dir=baseline,
            out_dir=out,
            browser_failure_path=failure,
            workers=1,
            fetch=fetch,
        )
    assert (out / "congress_advanced_records.ndjson").read_bytes() == before


def test_wrapper_only_accepts_fresh_fail_closed_verification_receipt(tmp_path: Path) -> None:
    stale = tmp_path / "manifest.partial.1.json"
    fresh = tmp_path / "manifest.partial.2.json"
    body = {
        "status": "failed",
        "automatic_verification_timeout": {
            "collection_stopped_without_result_acceptance": True
        },
        "error": {"code": "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT"},
    }
    write_json(stale, body)
    old = time.time() - 100
    stale.touch()
    import os

    os.utime(stale, (old, old))
    started = time.time()
    write_json(fresh, body)
    assert (
        REFRESH.latest_automatic_verification_failure(
            tmp_path, started_after=started
        )
        == fresh
    )
    body["error"]["code"] = "SOME_OTHER_FAILURE"
    write_json(fresh, body)
    assert (
        REFRESH.latest_automatic_verification_failure(
            tmp_path, started_after=started
        )
        is None
    )
