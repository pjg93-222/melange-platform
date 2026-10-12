from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from fec.legislation.legislation_culling_integration_contract import (
    EVIDENCE_SCHEMA,
    MEMBERSHIP_SCHEMA as INPUT_MEMBERSHIP_SCHEMA,
)
from fec.legislation.run_reversible_legislation_culling import (
    GATE_ENFORCED_BY_PRECONDITION,
    GATE_LEGACY_SOURCE_DECLARED_ABSENT,
    GATE_NO_DECLARED_INPUT_COUNTS,
    ReversibleCullingError,
    _load_ledger_state,
    append_restoration,
    build_generation,
    build_parser,
    evaluate_gates,
    value_sha256,
)


SHA = "a" * 64
URL = "https://www.congress.gov/bill/117th-congress/house-bill/1"
TEXT_URL = URL + "/text"
EVENT_TIME = "2026-07-27T03:00:00Z"


def evidence(
    key: str = "117:hr:1",
    *,
    query_ids: tuple[str, ...] = ("q1", "q2"),
) -> dict:
    return {
        "schema": EVIDENCE_SCHEMA,
        "canonical_key": key,
        "candidate_preserved": True,
        "discovery": {
            "path": "candidate.ndjson",
            "sha256": SHA,
            "record_sha256": SHA,
        },
        "membership_query_ids": list(query_ids),
        "official_evidence": {
            "title": {
                "status": "available",
                "source_url": URL,
                "text_sha256": SHA,
            },
            "summary": {
                "status": "available",
                "source_url": URL + "/summary",
                "text_sha256": SHA,
            },
            "full_text": {
                "status": "accepted",
                "expected_url": TEXT_URL,
                "final_url": TEXT_URL,
                "body_sha256": SHA,
                "archive_reverified": True,
                "decompressed_body_sha256": SHA,
                "marker_bounded_text_sha256": SHA,
                "extraction_receipt_sha256": SHA,
                "cookie_values_persisted": False,
                "version_inventory": {
                    "expected_version_count": 1,
                    "reviewed_version_count": 1,
                    "all_versions_reviewed": True,
                },
                "validation": {
                    "status_200": True,
                    "exact_url": True,
                    "html_content_type": True,
                    "not_challenge": True,
                    "expected_main_marker": True,
                },
            },
        },
        "category_evidence": [
            {
                "category_id": f"category_{index:02d}",
                "title_reviewed": True,
                "summary_status_reviewed": True,
                "full_text_reviewed": True,
                "absence_is_neutral": True,
                "evidence_sha256": SHA,
            }
            for index in range(19)
        ],
        "keyword_review": {
            "ruleset_sha256": SHA,
            "title_reviewed": True,
            "summary_reviewed": True,
            "full_text_reviewed": True,
            "absence_is_neutral": True,
        },
        "stage": {
            "status": "available",
            "value": "introduced",
            "actions_sha256": SHA,
        },
        "relationships": {
            "status": "complete",
            "role_provenance_complete": True,
            "sponsor_count": 1,
            "original_cosponsor_count": 1,
            "cosponsor_count": 2,
            "edges_sha256": SHA,
        },
    }


def decision(
    query_id: str,
    disposition: str,
    *,
    key: str = "117:hr:1",
) -> dict:
    return {
        "schema": INPUT_MEMBERSHIP_SCHEMA,
        "canonical_key": key,
        "query_id": query_id,
        "disposition": disposition,
        "adjudication_state": "human_ratified",
        "automated_final_decision": False,
        "reviewer_confidence": "high",
        "reason": "Official title, summary, and every text version reviewed.",
        "reason_code": "official_evidence_reviewed",
        "reviewer": "reviewer",
        "reviewed_at": "2026-07-26T22:00:00-05:00",
        "decision_evidence_sha256": SHA,
        "evidence_sources_checked": [
            "official_title",
            "official_crs_summary",
            "official_full_text",
        ],
        "absence_used_as_exclusion_evidence": False,
        "full_text_rule_out_complete": True,
    }


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def setup_inputs(
    tmp_path: Path,
    *,
    evidence_rows: list[dict] | None = None,
    membership_rows: list[dict] | None = None,
    decision_rows: list[dict] | None = None,
) -> tuple[Path, Path, Path, Path]:
    evidence_path = tmp_path / "evidence.ndjson"
    memberships_path = tmp_path / "memberships.ndjson"
    decisions_path = tmp_path / "decisions.ndjson"
    root = tmp_path / "out"
    write_ndjson(
        evidence_path,
        evidence_rows if evidence_rows is not None else [evidence()],
    )
    write_ndjson(
        memberships_path,
        membership_rows
        if membership_rows is not None
        else [
            {"canonical_key": "117:hr:1", "query_id": "q1"},
            {"canonical_key": "117:hr:1", "query_id": "q2"},
        ],
    )
    write_ndjson(
        decisions_path,
        decision_rows
        if decision_rows is not None
        else [
            decision("q1", "exclude_false_positive"),
            decision("q2", "exclude_false_positive"),
        ],
    )
    return evidence_path, memberships_path, decisions_path, root


def run(tmp_path: Path, **overrides):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(
        tmp_path,
        **overrides,
    )
    result = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time=EVENT_TIME,
    )
    return result, evidence_path, memberships_path, decisions_path, root


def read_generation_rows(result: dict, name: str) -> list[dict]:
    path = Path(result["receipt"]["outputs"][name]["path"])
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def test_every_explicit_exclusion_creates_reversible_global_junk(tmp_path):
    result, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    receipt = result["receipt"]
    assert receipt["counts"]["membership_partition"] == {
        "flagged": 0,
        "junk": 2,
        "keep": 0,
    }
    assert receipt["counts"]["candidate_partition"] == {
        "flagged": 0,
        "junk": 1,
        "keep": 0,
    }
    assert receipt["ledgers"]["new_junk_exclusions_appended"] == 1
    assert len((root / "junk_exclusions.ndjson").read_text().splitlines()) == 1
    assert (root / "restorations.ndjson").read_bytes() == b""
    candidates = read_generation_rows(result, "candidate_partition.ndjson")
    assert candidates[0]["all_memberships_explicitly_excluded"] is True
    assert candidates[0]["candidate_deletion_performed"] is False
    assert receipt["safety"]["main_corpus_write_performed"] is False
    assert evidence_path.exists()
    assert memberships_path.exists()
    assert decisions_path.exists()


def test_one_keep_vetoes_global_junk(tmp_path):
    result, *_ = run(
        tmp_path,
        decision_rows=[
            decision("q1", "include"),
            decision("q2", "exclude_false_positive"),
        ],
    )
    assert result["receipt"]["counts"]["candidate_partition"] == {
        "flagged": 0,
        "junk": 0,
        "keep": 1,
    }
    assert result["receipt"]["ledgers"]["new_junk_exclusions_appended"] == 0


def test_missing_or_incomplete_evidence_is_always_flagged(tmp_path):
    incomplete = evidence()
    incomplete["official_evidence"]["full_text"]["status"] = "missing"
    result, *_ = run(
        tmp_path,
        evidence_rows=[incomplete],
    )
    memberships = read_generation_rows(result, "membership_partition.ndjson")
    assert {row["disposition"] for row in memberships} == {"flagged"}
    assert all(
        "decision_not_finalizable_against_official_evidence"
        in row["flag_reasons"]
        for row in memberships
    )
    assert all(row["missing_evidence_can_produce_junk"] is False for row in memberships)
    assert result["receipt"]["counts"]["candidate_partition"]["flagged"] == 1
    assert result["receipt"]["counts"]["candidate_partition"]["junk"] == 0


def test_missing_candidate_evidence_is_flagged_not_fatal(tmp_path):
    result, *_ = run(tmp_path, evidence_rows=[])
    memberships = read_generation_rows(result, "membership_partition.ndjson")
    assert all(row["disposition"] == "flagged" for row in memberships)
    assert all(
        "missing_candidate_evidence" in row["flag_reasons"]
        for row in memberships
    )
    assert result["receipt"]["ledgers"]["new_junk_exclusions_appended"] == 0


def test_legacy_edge_vetoes_global_junk(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    legacy_path = tmp_path / "legacy.ndjson"
    write_ndjson(
        legacy_path,
        [{"canonical_key": "117:hr:1", "source": "preserved_snapshot"}],
    )
    result = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        legacy_edges_path=legacy_path,
        root=root,
        event_time=EVENT_TIME,
    )
    candidates = read_generation_rows(result, "candidate_partition.ndjson")
    assert candidates[0]["disposition"] == "flagged"
    assert candidates[0]["legacy_edge_present"] is True
    assert result["receipt"]["ledgers"]["new_junk_exclusions_appended"] == 0


def test_restoration_is_append_only_and_vetoes_future_junk(tmp_path):
    first, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    restoration = append_restoration(
        root=root,
        canonical_key="117:hr:1",
        reason="User restored this candidate for another review.",
        restored_by="User",
        restored_at="2026-07-27T03:05:00Z",
    )
    assert restoration["candidate_restored_edge_active"] is True
    assert len((root / "junk_exclusions.ndjson").read_text().splitlines()) == 1
    assert len((root / "restorations.ndjson").read_text().splitlines()) == 1

    second = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time="2026-07-27T03:10:00Z",
    )
    candidates = read_generation_rows(second, "candidate_partition.ndjson")
    assert candidates[0]["disposition"] == "flagged"
    assert candidates[0]["restored_edge_present"] is True
    assert second["receipt"]["ledgers"]["new_junk_exclusions_appended"] == 0
    assert first["generation_id"] != second["generation_id"]


def test_rerun_is_idempotent_and_does_not_duplicate_junk(tmp_path):
    first, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    second = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time="2026-07-27T04:00:00Z",
    )
    assert first["generation_id"] == second["generation_id"]
    assert second["receipt"]["ledgers"]["new_junk_exclusions_appended"] == 0
    assert len((root / "junk_exclusions.ndjson").read_text().splitlines()) == 1


def test_source_inputs_remain_byte_identical(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (evidence_path, memberships_path, decisions_path)
    }
    build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time=EVENT_TIME,
    )
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in before
    } == before


def test_duplicate_decision_fails_closed(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(
        tmp_path,
        decision_rows=[
            decision("q1", "include"),
            decision("q1", "exclude_false_positive"),
        ],
    )
    with pytest.raises(ReversibleCullingError, match="duplicate membership decision"):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            root=root,
            no_legacy_edges=True,
            event_time=EVENT_TIME,
        )


def test_tampered_append_only_ledger_fails_closed(tmp_path):
    _, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    ledger = root / "junk_exclusions.ndjson"
    body = ledger.read_text(encoding="utf-8")
    # Write canonically (LF) so this test isolates the content-hash guard
    # rather than tripping the CRLF guard first on Windows.
    ledger.write_text(
        body.replace('"restorable":true', '"restorable":false'),
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(ReversibleCullingError, match="content hash drift"):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            root=root,
            no_legacy_edges=True,
            event_time=EVENT_TIME,
        )


def test_crlf_rewritten_ledger_fails_closed(tmp_path):
    _, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    ledger = root / "junk_exclusions.ndjson"
    raw = ledger.read_bytes()
    ledger.write_bytes(raw.replace(b"\n", b"\r\n"))
    with pytest.raises(ReversibleCullingError, match="noncanonical CRLF"):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            root=root,
            no_legacy_edges=True,
            event_time=EVENT_TIME,
        )


def test_membership_universe_mismatch_flags_every_affected_row(tmp_path):
    # Evidence claims a membership set that discovery does not contain.
    result, *_ = run(
        tmp_path,
        evidence_rows=[evidence(query_ids=("q1", "q3"))],
    )
    memberships = read_generation_rows(result, "membership_partition.ndjson")
    assert {row["disposition"] for row in memberships} == {"flagged"}
    assert all(
        "membership_universe_mismatch" in row["flag_reasons"]
        for row in memberships
    )
    assert all(row["finalizable"] is False for row in memberships)
    assert result["receipt"]["counts"]["candidate_partition"] == {
        "flagged": 1,
        "junk": 0,
        "keep": 0,
    }
    assert result["receipt"]["ledgers"]["new_junk_exclusions_appended"] == 0


def test_membership_universe_subset_is_also_a_mismatch(tmp_path):
    # Evidence omits a discovered membership; exclusion must not finalize.
    result, *_ = run(
        tmp_path,
        evidence_rows=[evidence(query_ids=("q1",))],
    )
    memberships = read_generation_rows(result, "membership_partition.ndjson")
    assert all(
        "membership_universe_mismatch" in row["flag_reasons"]
        for row in memberships
    )
    assert result["receipt"]["counts"]["membership_partition"]["junk"] == 0


def test_rerun_preserves_each_invocation_without_collision(tmp_path):
    first, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    second = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time="2026-07-27T05:00:00Z",
    )
    assert first["generation_id"] == second["generation_id"]
    invocations = sorted(
        (
            Path(root)
            / "generations"
            / first["generation_id"]
            / "invocations"
        ).glob("*.json")
    )
    # One invocation appended the exclusion, the other appended nothing.
    assert len(invocations) == 2
    appended = sorted(
        json.loads(path.read_text(encoding="utf-8"))[
            "new_junk_exclusions_appended"
        ]
        for path in invocations
    )
    assert appended == [0, 1]


def test_flagged_review_csv_is_user_editable_and_authority_remains_ndjson(tmp_path):
    result, *_ = run(tmp_path, evidence_rows=[])
    csv_path = Path(result["receipt"]["outputs"]["flagged_memberships.csv"]["path"])
    ndjson_path = Path(
        result["receipt"]["outputs"]["flagged_memberships.ndjson"]["path"]
    )
    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "review_decision" in header
    assert "review_reason" in header
    assert len(ndjson_path.read_text().splitlines()) == 2
    assert result["receipt"]["xlsx_status"]["created"] is False


# ---------------------------------------------------------------------------
# Defect 1: the legacy edge source must be declared, never omitted.
# ---------------------------------------------------------------------------


def test_omitted_legacy_edge_source_fails_closed(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    with pytest.raises(
        ReversibleCullingError,
        match="a legacy edge source must be declared",
    ):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            root=root,
            event_time=EVENT_TIME,
        )
    assert not root.exists()


def test_ambiguous_legacy_edge_declaration_fails_closed(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    legacy_path = tmp_path / "legacy.ndjson"
    write_ndjson(legacy_path, [{"canonical_key": "117:hr:1"}])
    with pytest.raises(
        ReversibleCullingError,
        match="legacy edge source is ambiguous",
    ):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            legacy_edges_path=legacy_path,
            no_legacy_edges=True,
            root=root,
            event_time=EVENT_TIME,
        )


def test_declared_absent_legacy_source_never_claims_the_veto_gate_passed(tmp_path):
    result, *_ = run(tmp_path)
    receipt = result["receipt"]
    assert receipt["legacy_edges_declaration"] == {
        "mode": "declared_absent",
        "record_count": 0,
        "candidate_veto_count": 0,
    }
    assert (
        receipt["gates"]["keep_flagged_legacy_restored_edges_veto_global_junk"]
        == GATE_LEGACY_SOURCE_DECLARED_ABSENT
    )
    assert receipt["inputs"]["legacy_edges"] is None


def test_supplied_legacy_source_measures_the_veto_gate(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    legacy_path = tmp_path / "legacy.ndjson"
    write_ndjson(
        legacy_path,
        [{"canonical_key": "117:hr:1", "source": "preserved_snapshot"}],
    )
    result = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        legacy_edges_path=legacy_path,
        root=root,
        event_time=EVENT_TIME,
    )
    receipt = result["receipt"]
    assert receipt["legacy_edges_declaration"] == {
        "mode": "file",
        "record_count": 1,
        "candidate_veto_count": 1,
    }
    assert (
        receipt["gates"]["keep_flagged_legacy_restored_edges_veto_global_junk"]
        is True
    )
    assert receipt["gate_evidence"]["candidates_vetoed_by"]["legacy"] == 1


def test_cli_run_requires_an_explicit_legacy_edge_declaration():
    parser = build_parser()
    base = [
        "run",
        "--evidence",
        "evidence.ndjson",
        "--memberships",
        "memberships.ndjson",
        "--decisions",
        "decisions.ndjson",
        "--root",
        "root",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(base)
    with pytest.raises(SystemExit):
        parser.parse_args(base + ["--legacy-edges", "legacy.ndjson", "--no-legacy-edges"])
    args = parser.parse_args(base + ["--no-legacy-edges"])
    assert args.no_legacy_edges is True
    assert args.legacy_edges is None


# ---------------------------------------------------------------------------
# Defect 2: the junk record must bind the published candidate partition row.
# ---------------------------------------------------------------------------


def test_junk_record_binds_the_published_candidate_partition_row(tmp_path):
    result, _evidence, _memberships, _decisions, root = run(tmp_path)
    published = {
        row["canonical_key"]: row
        for row in read_generation_rows(result, "candidate_partition.ndjson")
    }
    records = [
        json.loads(line)
        for line in (root / "junk_exclusions.ndjson").read_text().splitlines()
    ]
    assert records
    for record in records:
        row = published[record["canonical_key"]]
        assert row["active_junk_exclusion_present"] is True
        assert record["candidate_partition_sha256"] == value_sha256(row)


# ---------------------------------------------------------------------------
# Defect 3: an active exclusion may not outlive the partition that produced it.
# ---------------------------------------------------------------------------


def test_active_junk_conflict_is_superseded_in_the_ledger(tmp_path):
    first, evidence_path, memberships_path, decisions_path, root = run(tmp_path)
    assert first["receipt"]["counts"]["candidate_partition"]["junk"] == 1

    # Re-collected discovery: one membership is now an explicit keep, so the
    # active exclusion in the authoritative ledger contradicts the partition.
    write_ndjson(
        decisions_path,
        [
            decision("q1", "include"),
            decision("q2", "exclude_false_positive"),
        ],
    )
    second = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time="2026-07-27T06:00:00Z",
    )
    # The authoritative ledger must no longer report this candidate as junk.
    state = _load_ledger_state(root)
    assert "117:hr:1" not in state["active_exclusions"]
    assert "117:hr:1" in state["restored_candidates"]
    assert second["receipt"]["ledgers"]["junk_exclusions_superseded"] == 1

    # Append-only: the exclusion is withdrawn, never deleted or rewritten.
    exclusion_lines = (root / "junk_exclusions.ndjson").read_text().splitlines()
    restoration_lines = (root / "restorations.ndjson").read_text().splitlines()
    assert len(exclusion_lines) == 1
    assert len(restoration_lines) == 1
    exclusion = json.loads(exclusion_lines[0])
    restoration = json.loads(restoration_lines[0])
    assert restoration["target_exclusion_id"] == exclusion["exclusion_id"]
    assert (
        restoration["derived_by"]["rule"]
        == "supersede-active-junk-on-membership-conflict-v1"
    )
    assert restoration["supersession_evidence"]["conflicting_membership_keys"] == [
        "117:hr:1|q1"
    ]
    assert (root / "restoration_receipts").is_dir()

    candidates = read_generation_rows(second, "candidate_partition.ndjson")
    assert candidates[0]["active_junk_exclusion_present"] is False
    assert candidates[0]["restored_edge_present"] is True
    assert candidates[0]["disposition"] == "keep"

    # A rerun supersedes nothing: the exclusion is already withdrawn.
    third = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        event_time="2026-07-27T07:00:00Z",
    )
    assert third["receipt"]["ledgers"]["junk_exclusions_superseded"] == 0
    assert second["generation_id"] == third["generation_id"]
    assert len((root / "restorations.ndjson").read_text().splitlines()) == 1


# ---------------------------------------------------------------------------
# Defect 4: every gate is derived from the run.
# ---------------------------------------------------------------------------


def gate_membership_row(
    query_id: str,
    disposition: str = "junk",
    *,
    key: str = "117:hr:1",
    flag_reasons: tuple[str, ...] = (),
) -> dict:
    return {
        "canonical_key": key,
        "query_id": query_id,
        "disposition": disposition,
        "flag_reasons": sorted(flag_reasons),
        "finalizable": disposition != "flagged",
        "missing_evidence_can_produce_junk": False,
        "source_candidate_mutated": False,
    }


def gate_candidate_row(
    key: str = "117:hr:1",
    *,
    disposition: str = "junk",
    membership_counts: dict | None = None,
    membership_keys: list[str] | None = None,
    active_junk_exclusion_present: bool = True,
    legacy_edge_present: bool = False,
    restored_edge_present: bool = False,
    all_memberships_explicitly_excluded: bool = True,
) -> dict:
    keys = (
        membership_keys
        if membership_keys is not None
        else [f"{key}|q1", f"{key}|q2"]
    )
    return {
        "canonical_key": key,
        "disposition": disposition,
        "membership_count": len(keys),
        "membership_counts": (
            membership_counts
            if membership_counts is not None
            else {"keep": 0, "junk": len(keys), "flagged": 0}
        ),
        "membership_keys": keys,
        "all_memberships_explicitly_excluded": all_memberships_explicitly_excluded,
        "legacy_edge_present": legacy_edge_present,
        "restored_edge_present": restored_edge_present,
        "active_junk_exclusion_present": active_junk_exclusion_present,
        "partition_reasons": ["every_membership_explicitly_excluded"],
        "candidate_deletion_performed": False,
        "main_corpus_write_authorized": False,
    }


def gate_context(**overrides) -> dict:
    receipts = {
        "candidate_evidence": {
            "path": "evidence.ndjson",
            "bytes": 10,
            "sha256": SHA,
        },
        "legacy_edges": None,
    }
    context = {
        "membership_rows": [gate_membership_row("q1"), gate_membership_row("q2")],
        "candidate_rows": [gate_candidate_row()],
        "membership_universe": {("117:hr:1", "q1"), ("117:hr:1", "q2")},
        "legacy_keys": set(),
        "legacy_declaration_mode": "declared_absent",
        "restored_candidates": set(),
        "declared_counts": None,
        "ledger_record_counts": {"junk_exclusions": 1, "restorations": 0},
        "ledger_append_only": {
            "junk_exclusions": {
                "bytes_before": 0,
                "bytes_after": 400,
                "records_before": 0,
                "records_after": 1,
                "prefix_preserved": True,
            },
            "restorations": {
                "bytes_before": 0,
                "bytes_after": 0,
                "records_before": 0,
                "records_after": 0,
                "prefix_preserved": True,
            },
        },
        "input_receipts": receipts,
        "reverified_input_receipts": copy.deepcopy(receipts),
    }
    context.update(overrides)
    return context


def test_gate_baseline_is_consistent_and_states_how_it_was_established():
    gates, evidence = evaluate_gates(**gate_context())
    assert gates["membership_partition_exact"] is True
    assert gates["candidate_partition_exact"] is True
    assert gates["missing_evidence_flagged_never_junk"] is True
    assert gates["candidate_junk_requires_every_membership_excluded"] is True
    assert gates["junk_and_restoration_ledgers_append_only"] is True
    assert gates["source_candidates_immutable"] is True
    assert gates["integration_authorized"] is False
    assert (
        gates["partition_totals_match_declared_input_counts"]
        == GATE_NO_DECLARED_INPUT_COUNTS
    )
    assert gates["inputs_outside_output_root"] == GATE_ENFORCED_BY_PRECONDITION
    assert evidence["membership_rows_partitioned"] == 2
    assert evidence["globally_junked_candidates"] == 1


def test_gate_detects_a_dropped_membership_row():
    context = gate_context()
    context["membership_rows"] = context["membership_rows"][:1]
    with pytest.raises(
        ReversibleCullingError,
        match="membership partition does not cover the discovery universe",
    ):
        evaluate_gates(**context)


def test_gate_detects_a_duplicated_membership_row():
    context = gate_context()
    context["membership_rows"] = [
        gate_membership_row("q1"),
        gate_membership_row("q1"),
    ]
    with pytest.raises(
        ReversibleCullingError,
        match="membership partition repeats",
    ):
        evaluate_gates(**context)


def test_gate_detects_a_candidate_partition_that_does_not_tile_memberships():
    context = gate_context()
    context["candidate_rows"] = [
        gate_candidate_row(membership_keys=["117:hr:1|q1"])
    ]
    with pytest.raises(
        ReversibleCullingError,
        match="does not tile the membership partition",
    ):
        evaluate_gates(**context)


def test_gate_detects_junk_built_on_a_membership_with_incomplete_evidence():
    context = gate_context()
    context["membership_rows"] = [
        gate_membership_row("q1"),
        gate_membership_row(
            "q2",
            "flagged",
            flag_reasons=("missing_candidate_evidence",),
        ),
    ]
    with pytest.raises(
        ReversibleCullingError,
        match="not a clean explicit exclusion",
    ):
        evaluate_gates(**context)


def test_gate_detects_incomplete_evidence_that_was_not_flagged():
    context = gate_context()
    context["membership_rows"] = [
        gate_membership_row("q1"),
        gate_membership_row(
            "q2",
            "junk",
            flag_reasons=("invalid_or_incomplete_candidate_evidence",),
        ),
    ]
    with pytest.raises(
        ReversibleCullingError,
        match="incomplete evidence was not flagged",
    ):
        evaluate_gates(**context)


def test_gate_detects_junk_without_an_active_ledger_exclusion():
    context = gate_context()
    context["candidate_rows"] = [
        gate_candidate_row(active_junk_exclusion_present=False)
    ]
    with pytest.raises(
        ReversibleCullingError,
        match="not bound to an active ledger exclusion",
    ):
        evaluate_gates(**context)


def test_gate_detects_an_active_exclusion_surviving_a_non_junk_candidate():
    context = gate_context()
    context["membership_rows"] = [
        gate_membership_row("q1", "keep"),
        gate_membership_row("q2"),
    ]
    context["candidate_rows"] = [
        gate_candidate_row(
            disposition="keep",
            membership_counts={"keep": 1, "junk": 1, "flagged": 0},
            all_memberships_explicitly_excluded=False,
        )
    ]
    with pytest.raises(
        ReversibleCullingError,
        match="active derived junk exclusion survives a keep",
    ):
        evaluate_gates(**context)


def test_gate_detects_a_veto_edge_that_did_not_stop_global_junk():
    context = gate_context()
    context["legacy_keys"] = {"117:hr:1"}
    context["candidate_rows"] = [gate_candidate_row(legacy_edge_present=True)]
    with pytest.raises(
        ReversibleCullingError,
        match="veto edge did not stop global junk",
    ):
        evaluate_gates(**context)


def test_gate_detects_a_ledger_prefix_that_was_rewritten():
    context = gate_context()
    context["ledger_append_only"]["junk_exclusions"]["prefix_preserved"] = False
    with pytest.raises(
        ReversibleCullingError,
        match="ledger is not append-only",
    ):
        evaluate_gates(**context)


def test_gate_detects_an_input_that_changed_during_the_run():
    context = gate_context()
    context["reverified_input_receipts"]["candidate_evidence"]["sha256"] = "b" * 64
    with pytest.raises(
        ReversibleCullingError,
        match="immutable inputs changed during culling",
    ):
        evaluate_gates(**context)


def test_declared_input_counts_are_enforced_against_the_partition(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    with pytest.raises(
        ReversibleCullingError,
        match="disagree with the declared input counts",
    ):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            root=root,
            no_legacy_edges=True,
            declared_candidate_count=18557,
            declared_membership_count=64004,
            event_time=EVENT_TIME,
        )
    # The mismatch is caught before any derived exclusion is appended.
    assert (root / "junk_exclusions.ndjson").read_bytes() == b""


def test_declared_input_counts_that_match_are_recorded_as_measured(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    result = build_generation(
        evidence_path=evidence_path,
        memberships_path=memberships_path,
        decisions_path=decisions_path,
        root=root,
        no_legacy_edges=True,
        declared_candidate_count=1,
        declared_membership_count=2,
        event_time=EVENT_TIME,
    )
    receipt = result["receipt"]
    assert receipt["gates"]["partition_totals_match_declared_input_counts"] is True
    assert receipt["counts"]["declared_input_counts"] == {
        "candidate_count": 1,
        "membership_count": 2,
    }


def test_declared_input_counts_must_be_supplied_together(tmp_path):
    evidence_path, memberships_path, decisions_path, root = setup_inputs(tmp_path)
    with pytest.raises(
        ReversibleCullingError,
        match="must be supplied together",
    ):
        build_generation(
            evidence_path=evidence_path,
            memberships_path=memberships_path,
            decisions_path=decisions_path,
            root=root,
            no_legacy_edges=True,
            declared_candidate_count=1,
            event_time=EVENT_TIME,
        )


def test_receipt_gate_evidence_is_derived_from_the_run(tmp_path):
    result, *_ = run(tmp_path)
    evidence_block = result["receipt"]["gate_evidence"]
    assert evidence_block["membership_rows_partitioned"] == 2
    assert evidence_block["candidate_rows_partitioned"] == 1
    assert evidence_block["discovery_membership_universe"] == 2
    assert evidence_block["globally_junked_candidates"] == 1
    assert evidence_block["incomplete_evidence_memberships"] == 0
    assert evidence_block["candidates_vetoed_by"] == {
        "keep": 0,
        "flagged": 0,
        "legacy": 0,
        "restored": 0,
    }
    assert evidence_block["ledger_records_chain_verified"] == {
        "junk_exclusions": 1,
        "restorations": 0,
    }
    assert evidence_block["declared_input_counts"] is None

    flagged_dir = tmp_path / "flagged"
    flagged_dir.mkdir()
    flagged, *_ = run(flagged_dir, evidence_rows=[])
    flagged_evidence = flagged["receipt"]["gate_evidence"]
    assert flagged_evidence["incomplete_evidence_memberships"] == 2
    assert flagged_evidence["globally_junked_candidates"] == 0
    assert flagged_evidence["candidates_vetoed_by"]["flagged"] == 1


def test_mixed_corpus_partitions_and_gates_end_to_end(tmp_path):
    # One candidate is fully excluded, one has an undecided membership.
    result, _evidence, _memberships, _decisions, root = run(
        tmp_path,
        evidence_rows=[evidence(), evidence("117:s:5")],
        membership_rows=[
            {"canonical_key": "117:hr:1", "query_id": "q1"},
            {"canonical_key": "117:hr:1", "query_id": "q2"},
            {"canonical_key": "117:s:5", "query_id": "q1"},
            {"canonical_key": "117:s:5", "query_id": "q2"},
        ],
        decision_rows=[
            decision("q1", "exclude_false_positive"),
            decision("q2", "exclude_false_positive"),
            decision("q1", "exclude_false_positive", key="117:s:5"),
        ],
    )
    receipt = result["receipt"]
    assert receipt["counts"]["candidate_partition"] == {
        "flagged": 1,
        "junk": 1,
        "keep": 0,
    }
    assert receipt["counts"]["membership_partition"] == {
        "flagged": 1,
        "junk": 3,
        "keep": 0,
    }
    assert receipt["gate_evidence"]["globally_junked_candidates"] == 1
    assert receipt["gate_evidence"]["candidates_vetoed_by"]["flagged"] == 1
    assert receipt["ledgers"]["new_junk_exclusions_appended"] == 1

    published = {
        row["canonical_key"]: row
        for row in read_generation_rows(result, "candidate_partition.ndjson")
    }
    assert published["117:hr:1"]["disposition"] == "junk"
    assert published["117:s:5"]["disposition"] == "flagged"
    assert published["117:s:5"]["active_junk_exclusion_present"] is False
    records = [
        json.loads(line)
        for line in (root / "junk_exclusions.ndjson").read_text().splitlines()
    ]
    assert [record["canonical_key"] for record in records] == ["117:hr:1"]
    assert records[0]["candidate_partition_sha256"] == value_sha256(
        published["117:hr:1"]
    )
