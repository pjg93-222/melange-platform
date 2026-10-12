#!/usr/bin/env python3
"""Focused tests for the 19-category adjudication review queue builder.

Run with::

    python -m pytest fec/tests/legislation/test_build_legislation_category_review_queue.py -q
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
for candidate in (str(REPO_ROOT), str(REPO_ROOT / "fec")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from legislation import build_legislation_category_review_queue as queue_builder  # noqa: E402
from legislation.build_legislation_category_review_queue import ReviewQueueError  # noqa: E402
from legislation.legislation_culling_integration_contract import (  # noqa: E402
    REQUIRED_FULL_TEXT_CHECKS,
)


CATEGORY_IDS = [f"category_{index:02d}" for index in range(1, 20)]
PROBE_ID = "title_alias_probe_category_01_smart_act"


# ---------------------------------------------------------------------------
# Fixture corpus
# ---------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_ndjson(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"".join(queue_builder.canonical_json_bytes(row) + b"\n" for row in rows)
    )


def _query_file(directory: Path, query_id: str, **overrides) -> None:
    header = {
        "schema": "congress-advanced-search-query-v1",
        "query_id": query_id,
        "label": query_id.replace("_", " ").title(),
        "scoring_category": True,
        "record_kind": "category",
        "parent_category_id": None,
        "title_alias": None,
        "ambiguous_short_act_alias": False,
        "membership_non_dispositive": False,
        "registry_sha256": _sha("registry"),
        "query_sha256": _sha(query_id),
        "advanced_search_query": (
            '(credit union capital) OR ("SMART Act") OR (Act) OR '
            '((congressId:119 AND cite:"s2704"))'
        ),
        "search_url": "https://www.congress.gov/advanced-search/legislation?x=1",
        "result_total": 3,
        "unique_record_count": 3,
    }
    header.update(overrides)
    # ``pages`` must be last: the builder parses a bounded prefix that ends at
    # the ``pages`` key, so everything before it has to be a valid JSON prefix.
    header["pages"] = [{"file": "raw/pages/x/page_001.json", "record_count": 3}]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{query_id}.json").write_text(
        json.dumps(header, indent=2), encoding="utf-8"
    )


def _record(canonical_key: str, *, title: str, number: int) -> dict:
    congress, kind, num = canonical_key.split(":")
    return {
        "record_kind": "bill",
        "congress": int(congress),
        "type": kind,
        "number": int(num),
        "canonical_key": canonical_key,
        "canonical_url": (
            f"https://www.congress.gov/bill/{congress}th-congress/house-bill/{num}"
        ),
        "display_number": f"H.R.{num}",
        "heading": f"H.R.{num} - {congress}th Congress",
        "title": title,
        "purpose": None,
        "sponsor": "Example, Member [Rep.-D-XX-1]",
        "raw_text": f"BILL {number}. H.R.{num} {title} credit union capital.",
    }


def _membership(canonical_key: str, query_id: str, **overrides) -> dict:
    row = {
        "query_id": query_id,
        "label": query_id.replace("_", " ").title(),
        "scoring_category": True,
        "record_kind": "category",
        "parent_category_id": None,
        "parent_category_label": None,
        "identifier_text": None,
        "title_alias": None,
        "ambiguous_short_act_alias": False,
        "membership_non_dispositive": False,
        "source_paragraph": None,
        "canonical_key": canonical_key,
        "source_page": 1,
        "source_rank": 1,
        "result_url": (
            "https://www.congress.gov/index.php/bill/"
            f"{canonical_key.split(':')[0]}th-congress/house-bill/"
            f"{canonical_key.split(':')[2]}?hl=x&s=1&r=1"
        ),
    }
    row.update(overrides)
    return row


def _evidence(canonical_key: str, query_ids, *, complete: bool = False) -> dict:
    congress, _, num = canonical_key.split(":")
    url = f"https://www.congress.gov/bill/{congress}th-congress/house-bill/{num}"
    envelope = {
        "schema": "acu-legislation-candidate-evidence-v1",
        "canonical_key": canonical_key,
        "candidate_preserved": True,
        "discovery": {
            "path": "discovery/congress_advanced_records.ndjson",
            "sha256": _sha("discovery"),
            "record_sha256": _sha(canonical_key),
        },
        "membership_query_ids": sorted(query_ids),
        "official_evidence": {
            "title": {
                "status": "available",
                "source_url": url,
                "text_sha256": _sha(f"title:{canonical_key}"),
                "text_length": 40,
                "provenance": "congress_gov_advanced_search_record",
            },
            "summary": {
                "status": "available_pending_congress_gov_fetch",
                "reason": "not yet fetched",
                "text_sha256": _sha(f"summary:{canonical_key}"),
                "availability_receipt_sha256": _sha(f"bs:{canonical_key}"),
            },
            "full_text": {
                "status": "missing",
                "reason": "no Congress.gov full-text receipt",
                "version_inventory": {
                    "expected_version_count": 2,
                    "reviewed_version_count": 1,
                    "all_versions_reviewed": False,
                },
            },
        },
        "category_evidence": [],
        "keyword_review": {"status": "not_performed", "reason": "no review yet"},
        "stage": {"status": "missing", "reason": "no BillStatus record"},
        "relationships": {
            "status": "complete",
            "role_provenance_complete": True,
            "sponsor_count": 1,
            "original_cosponsor_count": 0,
            "cosponsor_count": 0,
            "edges_sha256": _sha(f"edges:{canonical_key}"),
        },
    }
    if not complete:
        return envelope

    body = _sha(f"body:{canonical_key}")
    envelope["official_evidence"]["summary"] = {
        "status": "officially_unavailable",
        "unavailability_reason": "BillStatus authoritatively lists no CRS summary",
        "availability_receipt_sha256": _sha(f"bs:{canonical_key}"),
    }
    envelope["official_evidence"]["full_text"] = {
        "status": "accepted",
        "expected_url": f"{url}/text",
        "final_url": f"{url}/text",
        "body_sha256": body,
        "archive_reverified": True,
        "decompressed_body_sha256": body,
        "marker_bounded_text_sha256": _sha(f"marker:{canonical_key}"),
        "extraction_receipt_sha256": _sha(f"extract:{canonical_key}"),
        "cookie_values_persisted": False,
        "validation": {name: True for name in REQUIRED_FULL_TEXT_CHECKS},
        "version_inventory": {
            "expected_version_count": 2,
            "reviewed_version_count": 2,
            "all_versions_reviewed": True,
        },
    }
    envelope["category_evidence"] = [
        {
            "category_id": category_id,
            "title_reviewed": True,
            "summary_status_reviewed": True,
            "full_text_reviewed": True,
            "absence_is_neutral": True,
            "evidence_sha256": _sha(f"{canonical_key}:{category_id}"),
        }
        for category_id in CATEGORY_IDS
    ]
    envelope["keyword_review"] = {
        "ruleset_sha256": _sha("ruleset"),
        "title_reviewed": True,
        "summary_reviewed": True,
        "full_text_reviewed": True,
        "absence_is_neutral": True,
    }
    envelope["stage"] = {
        "status": "available",
        "value": "Referred to committee",
        "actions_sha256": _sha(f"actions:{canonical_key}"),
    }
    return envelope


def build_corpus(root: Path, *, complete_keys=()) -> dict:
    """Write a small but contract-valid discovery + evidence corpus."""

    queries_dir = root / "queries"
    for category_id in CATEGORY_IDS:
        _query_file(queries_dir, category_id)
    _query_file(
        queries_dir,
        PROBE_ID,
        record_kind="coverage_probe",
        scoring_category=False,
        parent_category_id=CATEGORY_IDS[0],
        title_alias="SMART Act",
        membership_non_dispositive=True,
        advanced_search_query='("SMART Act")',
    )

    # broad: 19 scoring categories; narrow: 1 category; probe_only: probe edge.
    memberships = []
    universe = {
        "119:hr:1": list(CATEGORY_IDS),
        "119:hr:2": [CATEGORY_IDS[0]],
        "119:hr:3": [PROBE_ID],
    }
    for canonical_key, query_ids in universe.items():
        for query_id in query_ids:
            if query_id == PROBE_ID:
                memberships.append(
                    _membership(
                        canonical_key,
                        query_id,
                        record_kind="coverage_probe",
                        scoring_category=False,
                        membership_non_dispositive=True,
                        title_alias="SMART Act",
                        ambiguous_short_act_alias=True,
                        parent_category_id=CATEGORY_IDS[0],
                    )
                )
            else:
                memberships.append(_membership(canonical_key, query_id))

    records = [
        _record("119:hr:1", title="Broad Financial Services Act", number=1),
        _record("119:hr:2", title="Narrow Credit Union Capital Act", number=2),
        _record("119:hr:3", title="SMART Act", number=3),
    ]
    records_path = root / "records.ndjson"
    memberships_path = root / "memberships.ndjson"
    evidence_path = root / "evidence" / "candidate_evidence.ndjson"
    _write_ndjson(records_path, records)
    _write_ndjson(memberships_path, memberships)
    _write_ndjson(
        evidence_path,
        [
            _evidence(key, ids, complete=key in complete_keys)
            for key, ids in universe.items()
        ],
    )

    receipt = {
        "schema": "acu-legislation-candidate-evidence-receipt-v1",
        "inputs": {
            "discovery_records": {
                "path": str(records_path),
                "sha256": queue_builder.sha256_file(records_path),
            },
            "discovery_memberships": {
                "path": str(memberships_path),
                "sha256": queue_builder.sha256_file(memberships_path),
            },
        },
    }
    (evidence_path.parent / "receipt.json").write_bytes(
        queue_builder.canonical_json_bytes(receipt) + b"\n"
    )

    return {
        "records": records_path,
        "memberships": memberships_path,
        "queries": queries_dir,
        "evidence": evidence_path,
        "universe": universe,
    }


def run_build(paths, out_root: Path, **kwargs):
    return queue_builder.build(
        records_path=paths["records"],
        memberships_path=paths["memberships"],
        queries_dir=paths["queries"],
        evidence_path=paths["evidence"],
        root=out_root,
        **kwargs,
    )


def read_rows(manifest, name: str):
    path = Path(manifest["receipt"]["outputs"][name]["path"])
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# Term extraction and matching
# ---------------------------------------------------------------------------


def test_extract_query_terms_separates_phrases_aliases_generics_and_cites():
    terms = queue_builder.extract_query_terms(
        '(credit union capital) OR ("SMART Act") OR (Act) OR '
        '((congressId:119 AND cite:"s2704"))'
    )
    by_kind = {term["kind"]: term["text"] for term in terms}
    assert by_kind["phrase"] == "credit union capital"
    assert by_kind["short_act_alias_non_dispositive"] == "SMART Act"
    assert by_kind["generic_act_non_evidence"] == "Act"
    assert by_kind["cite_identifier"] == "119 s2704"
    # Only a real phrase may ever be treated as an evidence token.
    assert [term["text"] for term in terms if term["is_evidence_token"]] == [
        "credit union capital"
    ]


def test_proximity_expressions_yield_components_never_operators():
    terms = queue_builder.extract_query_terms('("credit card routing" ~5) OR (MDI)')
    kinds = {term["kind"] for term in terms}
    assert "proximity_component" in kinds
    assert all("~" not in term["text"] for term in terms)
    assert all(term["text"].upper() not in {"OR", "AND", "NOT"} for term in terms)


def test_term_matching_is_token_bounded():
    pattern = queue_builder.compile_term("MDI")
    assert pattern.search("the MDI program") is not None
    assert pattern.search("AMDIX holdings") is None
    assert queue_builder.compile_term("credit union").search("credit  union") is not None


def test_cite_anchors_resolve_to_canonical_measure_keys():
    assert queue_builder.cite_anchor_canonical_key("119 s2704") == "119:s:2704"
    assert queue_builder.cite_anchor_canonical_key("116 hr7035") == "116:hr:7035"
    assert queue_builder.cite_anchor_canonical_key("119 hjres14") == "119:hjres:14"
    # Leading zeros normalise; unknown congresses and junk resolve to nothing.
    assert queue_builder.cite_anchor_canonical_key("119 s0042") == "119:s:42"
    assert queue_builder.cite_anchor_canonical_key("115 s1") is None
    assert queue_builder.cite_anchor_canonical_key("credit union") is None


def test_explicitly_named_membership_is_marked_and_leads_its_measure(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    # Make a mid-alphabet category query name 119:hr:1 outright, so that a
    # correct sort cannot be confused with alphabetical order by query_id.
    named_query_id = CATEGORY_IDS[5]
    named_query = paths["queries"] / f"{named_query_id}.json"
    header = json.loads(named_query.read_text(encoding="utf-8"))
    header["advanced_search_query"] = (
        '(credit union capital) OR ((congressId:119 AND cite:"hr1"))'
    )
    named_query.write_text(json.dumps(header, indent=2), encoding="utf-8")

    manifest = run_build(paths, tmp_path / "out")
    rows = read_rows(manifest, "queue.ndjson")
    named = [
        row
        for row in rows
        if row["query_terms"]["candidate_explicitly_named_in_query"]
    ]
    assert len(named) == 1
    assert (named[0]["canonical_key"], named[0]["query_id"]) == (
        "119:hr:1",
        named_query_id,
    )
    assert manifest["receipt"]["counts"][
        "rows_where_candidate_is_explicitly_named_by_the_query"
    ] == 1

    measure_rows = [row for row in rows if row["canonical_key"] == "119:hr:1"]
    # The named edge leads its measure even though its query_id sorts sixth.
    assert measure_rows[0]["query_id"] == named_query_id
    assert measure_rows[1]["query_id"] == CATEGORY_IDS[0]
    # Every other edge of that measure knows the query names measures, without
    # claiming that this measure is one of them.
    other = measure_rows[1]["query_terms"]
    assert other["candidate_explicitly_named_in_query"] is False


def test_match_block_always_declares_absence_neutral():
    compiled = [
        (term, queue_builder.compile_term(term["text"]))
        for term in queue_builder.extract_query_terms("(nowhere phrase)")
    ]
    block = queue_builder.match_terms(compiled, {"title": "unrelated text"})
    assert block["matched_term_count"] == 0
    assert block["absence_is_neutral"] is True
    assert block["matching_is_navigational_not_evidentiary"] is True


# ---------------------------------------------------------------------------
# Query header reading
# ---------------------------------------------------------------------------


def test_read_query_header_uses_a_bounded_prefix(tmp_path):
    directory = tmp_path / "queries"
    _query_file(directory, CATEGORY_IDS[0])
    path = directory / f"{CATEGORY_IDS[0]}.json"
    # Append a large trailing payload; the header must still parse without it.
    body = json.loads(path.read_text(encoding="utf-8"))
    body["pages"] = [{"file": f"page_{index}", "blob": "x" * 500} for index in range(400)]
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    header = queue_builder.read_query_header(path, max_bytes=1 << 20)
    assert header["query_id"] == CATEGORY_IDS[0]
    assert "pages" not in header


def test_read_query_header_fails_closed_without_a_header(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"schema": "congress-advanced-search-query-v1"}), encoding="utf-8")
    with pytest.raises(ReviewQueueError, match="no metadata header"):
        queue_builder.read_query_header(path)


# ---------------------------------------------------------------------------
# Queue construction
# ---------------------------------------------------------------------------


def test_every_discovered_membership_is_queued_exactly_once(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    manifest = run_build(paths, tmp_path / "out")
    rows = read_rows(manifest, "queue.ndjson")
    expected = {
        (key, query_id)
        for key, query_ids in paths["universe"].items()
        for query_id in query_ids
    }
    observed = [(row["canonical_key"], row["query_id"]) for row in rows]
    assert len(observed) == len(expected)
    assert set(observed) == expected
    counts = manifest["receipt"]["counts"]
    assert counts["rows_needing_adjudication"] == len(expected)
    assert manifest["receipt"]["gates"]["every_discovered_membership_accounted_for"]


def test_queue_never_decides_anything(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    manifest = run_build(paths, tmp_path / "out")
    rows = read_rows(manifest, "queue.ndjson")
    for row in rows:
        assert row["adjudication"]["disposition"] is None
        assert row["adjudication"]["state"].startswith("unresolved")
        assert row["adjudication"]["human_ratification_required"] is True
        assert row["adjudication"]["automated_final_decision_permitted"] is False
        assert row["safety"]["disposition_assigned"] is False
        assert row["safety"]["category_evidence_written"] is False
    safety = manifest["receipt"]["safety"]
    assert safety["adjudication_performed"] is False
    assert safety["category_evidence_written"] is False
    assert safety["keep_or_junk_marked"] is False
    assert safety["main_corpus_write_performed"] is False


def test_missing_evidence_becomes_a_blocker_never_an_exclusion(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    manifest = run_build(paths, tmp_path / "out")
    rows = read_rows(manifest, "queue.ndjson")
    for row in rows:
        assert "candidate_19_category_audit_absent" in row["evidence_blockers"]
        assert row["decision_can_finalize_today"] is False
        # A blocker never resolves to a disposition of any kind.
        assert row["adjudication"]["disposition"] is None
    assert manifest["receipt"]["gates"][
        "missing_evidence_recorded_as_blocker_never_exclusion"
    ]
    assert manifest["receipt"]["counts"][
        "rows_that_can_finalize_after_adjudication_alone"
    ] == 0


def test_priority_orders_scoring_full_text_and_breadth_first(tmp_path):
    paths = build_corpus(tmp_path / "corpus", complete_keys={"119:hr:2"})
    manifest = run_build(paths, tmp_path / "out")
    rows = read_rows(manifest, "queue.ndjson")
    assert [row["priority_rank"] for row in rows] == list(range(1, len(rows) + 1))

    # 119:hr:2 has accepted full text, so its scoring edge is tier T1 and its
    # candidate outranks the 19-membership candidate that has no text.
    assert rows[0]["canonical_key"] == "119:hr:2"
    assert rows[0]["tier"].startswith("T1_")

    tiers = [row["tier"] for row in rows]
    assert tiers.index("T2_scoring_category_text_not_accepted") < tiers.index(
        "T4_non_dispositive_probe"
    )
    # The 19-category candidate outranks the probe-only candidate.
    order = [row["canonical_key"] for row in rows]
    assert order.index("119:hr:1") < order.index("119:hr:3")


def test_candidate_rows_are_contiguous_and_worksheets_carry_19_slots(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    manifest = run_build(paths, tmp_path / "out")
    rows = read_rows(manifest, "queue.ndjson")
    seen: list[str] = []
    for row in rows:
        if not seen or seen[-1] != row["canonical_key"]:
            assert row["canonical_key"] not in seen, "candidate rows are not contiguous"
            seen.append(row["canonical_key"])

    worksheets = read_rows(manifest, "candidate_worksheets.ndjson")
    assert len(worksheets) == len(paths["universe"])
    for worksheet in worksheets:
        slots = worksheet["category_audit_slots"]
        assert len(slots) == 19
        assert len({slot["category_id"] for slot in slots}) == 19
        assert all(slot["state"] == "not_performed" for slot in slots)
        members = {
            slot["category_id"] for slot in slots if slot["is_discovered_membership"]
        }
        assert members == set(worksheet["membership_query_ids"]) & set(CATEGORY_IDS)
    assert manifest["receipt"]["counts"]["category_audit_rows_required"] == (
        len(worksheets) * 19
    )


def test_batches_partition_the_queue_without_splitting_a_candidate(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    manifest = run_build(paths, tmp_path / "out", batch_size=5)
    rows = read_rows(manifest, "queue.ndjson")
    batches = read_rows(manifest, "batches.ndjson")
    assert sum(batch["row_count"] for batch in batches) == len(rows)
    by_candidate: dict[str, set[str]] = {}
    for row in rows:
        by_candidate.setdefault(row["canonical_key"], set()).add(row["batch_id"])
    assert all(len(values) == 1 for values in by_candidate.values())


def test_csv_reviewer_columns_are_emitted_blank(tmp_path):
    import csv as csv_module

    paths = build_corpus(tmp_path / "corpus")
    manifest = run_build(paths, tmp_path / "out")
    csv_path = Path(manifest["receipt"]["outputs"]["queue.csv"]["path"])
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    reader = csv_module.DictReader(raw.decode("utf-8-sig").splitlines())
    written = list(reader)
    assert len(written) == manifest["receipt"]["outputs"]["queue.csv"]["record_count"]
    for row in written:
        for column in (
            "disposition",
            "evidence_basis",
            "evidence_url",
            "evidence_quote",
            "reviewer_confidence",
            "reason",
            "reason_code",
            "reviewer",
            "reviewed_at",
        ):
            assert row[column] == ""
        assert row["canonical_key"]
        assert row["queue_row_id"].startswith("qr-")


# ---------------------------------------------------------------------------
# Determinism, resumability, immutability
# ---------------------------------------------------------------------------


def test_build_is_deterministic_and_idempotent(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    out = tmp_path / "out"
    first = run_build(paths, out, event_time="2026-07-27T00:00:00Z")
    second = run_build(paths, out, event_time="2026-07-27T09:30:00Z")
    assert first["queue_id"] == second["queue_id"]
    generations = sorted((out / "queues").iterdir())
    assert len(generations) == 1
    # Per-invocation metadata is preserved without colliding with the sealed
    # generation, exactly as the reversible culling engine does.
    assert len(list((generations[0] / "invocations").iterdir())) == 2
    for name in ("queue.ndjson", "candidate_worksheets.ndjson", "queries.ndjson"):
        assert (
            first["receipt"]["outputs"][name]["sha256"]
            == second["receipt"]["outputs"][name]["sha256"]
        )


def test_generation_artifact_tampering_is_detected(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    out = tmp_path / "out"
    manifest = run_build(paths, out)
    target = Path(manifest["receipt"]["outputs"]["queue.ndjson"]["path"])
    target.write_bytes(target.read_bytes() + b'{"tampered":true}\n')
    with pytest.raises(ReviewQueueError, match="immutable queue artifact drift"):
        run_build(paths, out)


def test_a_finalizable_decision_retires_its_row(tmp_path):
    paths = build_corpus(tmp_path / "corpus", complete_keys={"119:hr:2"})
    decisions = tmp_path / "decisions.ndjson"
    _write_ndjson(
        decisions,
        [
            {
                "schema": "acu-legislation-membership-adjudication-v1",
                "canonical_key": "119:hr:2",
                "query_id": CATEGORY_IDS[0],
                "disposition": "include",
                "adjudication_state": "human_ratified",
                "automated_final_decision": False,
                "reviewer_confidence": "high",
                "reason": "official title states the credit-union capital rule",
                "reason_code": "official_title",
                "reviewer": "paul",
                "reviewed_at": "2026-07-27T00:00:00Z",
                "decision_evidence_sha256": _sha("decision"),
                "evidence_sources_checked": [
                    "official_title",
                    "official_full_text",
                    "official_summary_unavailable",
                ],
                "absence_used_as_exclusion_evidence": False,
                "full_text_rule_out_complete": True,
            }
        ],
    )
    manifest = run_build(paths, tmp_path / "out", decision_paths=[decisions])
    counts = manifest["receipt"]["counts"]
    assert counts["rows_retired_by_existing_finalizable_decision"] == 1
    assert counts["rows_needing_adjudication"] == 20
    retired = read_rows(manifest, "retired_rows.ndjson")
    assert retired[0]["canonical_key"] == "119:hr:2"
    assert retired[0]["retired_by_this_tool"] is False
    rows = read_rows(manifest, "queue.ndjson")
    assert ("119:hr:2", CATEGORY_IDS[0]) not in {
        (row["canonical_key"], row["query_id"]) for row in rows
    }


def test_a_non_finalizable_decision_keeps_the_row_and_records_it(tmp_path):
    paths = build_corpus(tmp_path / "corpus", complete_keys={"119:hr:2"})
    decisions = tmp_path / "decisions.ndjson"
    _write_ndjson(
        decisions,
        [
            {
                "schema": "acu-legislation-membership-adjudication-v1",
                "canonical_key": "119:hr:2",
                "query_id": CATEGORY_IDS[0],
                "disposition": "include",
                # Automated triage output can never finalize.
                "adjudication_state": "automated_triage",
                "automated_final_decision": True,
                "reviewer_confidence": "high",
                "reason": "keyword hit",
                "reason_code": "automated",
                "reviewer": "triage",
                "reviewed_at": "2026-07-27T00:00:00Z",
                "decision_evidence_sha256": _sha("decision"),
                "evidence_sources_checked": ["official_title"],
                "absence_used_as_exclusion_evidence": False,
                "full_text_rule_out_complete": False,
                "decision_source": "automated_triage_v1",
            }
        ],
    )
    manifest = run_build(paths, tmp_path / "out", decision_paths=[decisions])
    counts = manifest["receipt"]["counts"]
    assert counts["rows_retired_by_existing_finalizable_decision"] == 0
    assert counts["rows_with_prior_non_finalizable_decision"] == 1
    rows = read_rows(manifest, "queue.ndjson")
    kept = [
        row
        for row in rows
        if row["canonical_key"] == "119:hr:2" and row["query_id"] == CATEGORY_IDS[0]
    ]
    assert len(kept) == 1
    assert kept[0]["adjudication"]["state"] == (
        "unresolved_prior_decision_not_finalizable"
    )
    assert kept[0]["prior_decision"]["decision_source"] == "automated_triage_v1"
    assert kept[0]["adjudication"]["disposition"] is None


# ---------------------------------------------------------------------------
# Fail-closed guards
# ---------------------------------------------------------------------------


def test_evidence_built_from_other_discovery_files_is_rejected(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    other = build_corpus(tmp_path / "other")
    # Give the second copy genuinely different discovery bytes, the way the
    # "-PaulComputer" discovery copies differ from the canonical ones.
    other_records = [
        json.loads(line)
        for line in other["records"].read_text(encoding="utf-8").splitlines()
        if line
    ]
    other_records[0]["title"] = "A Different Discovery Copy Act"
    _write_ndjson(other["records"], other_records)
    with pytest.raises(ReviewQueueError, match="different discovery"):
        queue_builder.build(
            records_path=other["records"],
            memberships_path=other["memberships"],
            queries_dir=paths["queries"],
            evidence_path=paths["evidence"],
            root=tmp_path / "out",
        )


def test_membership_universe_mismatch_fails_closed(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    rows = [
        json.loads(line)
        for line in paths["evidence"].read_text(encoding="utf-8").splitlines()
        if line
    ]
    for row in rows:
        if row["canonical_key"] == "119:hr:2":
            row["membership_query_ids"] = [CATEGORY_IDS[1]]
    _write_ndjson(paths["evidence"], rows)
    with pytest.raises(ReviewQueueError, match="does not match discovery"):
        run_build(paths, tmp_path / "out")


def test_wrong_category_query_count_fails_closed(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    (paths["queries"] / f"{CATEGORY_IDS[-1]}.json").unlink()
    with pytest.raises(ReviewQueueError, match="expected 19 category queries"):
        run_build(paths, tmp_path / "out")


def test_inputs_inside_the_output_root_are_rejected(tmp_path):
    corpus = tmp_path / "corpus"
    paths = build_corpus(corpus)
    with pytest.raises(ReviewQueueError, match="inside the output root"):
        run_build(paths, corpus)


def test_membership_metadata_disagreeing_with_the_registry_fails_closed(tmp_path):
    paths = build_corpus(tmp_path / "corpus")
    rows = [
        json.loads(line)
        for line in paths["memberships"].read_text(encoding="utf-8").splitlines()
        if line
    ]
    for row in rows:
        if row["query_id"] == CATEGORY_IDS[0] and row["canonical_key"] == "119:hr:2":
            row["scoring_category"] = False
    _write_ndjson(paths["memberships"], rows)
    # The evidence receipt now binds a stale memberships hash, so rebuild it.
    receipt_path = paths["evidence"].parent / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["inputs"]["discovery_memberships"]["sha256"] = queue_builder.sha256_file(
        paths["memberships"]
    )
    receipt_path.write_bytes(queue_builder.canonical_json_bytes(receipt) + b"\n")
    with pytest.raises(ReviewQueueError, match="disagrees with the query registry"):
        run_build(paths, tmp_path / "out")
