from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

from legislation import build_acu_bill_title_corroboration_query_plan as PLAN


def record(
    key: str,
    *,
    official_title: str | None = None,
    title: str | None = None,
) -> dict[str, object]:
    return {
        "canonical_key": key,
        "official_title": official_title,
        "title": title,
    }


def membership(key: str, query_id: str) -> dict[str, str]:
    return {"canonical_key": key, "query_id": query_id}


def review(
    key: str,
    query_id: str,
    **values: object,
) -> dict[str, object]:
    return {
        "canonical_key": key,
        "query_id": query_id,
        **values,
    }


def test_all_scope_emits_exact_title_and_number_on_closed_domain_set() -> None:
    result = PLAN.build_query_rows(
        records=[
            record(
                "119:hr:4437",
                official_title=(
                    "Supervisory Modifications for Appropriate "
                    "Risk-Based Testing Act"
                ),
            )
        ],
        memberships=[
            membership("119:hr:4437", "examination_supervision"),
            membership("119:hr:4437", "coverage_probe"),
        ],
        review_rows=[
            review(
                "119:hr:4437",
                "coverage_probe",
                ambiguous_short_act_alias_probe=True,
            )
        ],
        scope="all",
    )

    assert result["selected_measure_count"] == 1
    assert result["selected_membership_count"] == 2
    assert len(result["queries"]) == 4
    assert {row["domain"] for row in result["queries"]} == {
        "americascreditunions.org",
    }
    assert {row["bill_number_phrase"] for row in result["queries"]} == {
        "H.R. 4437",
        "H.R.4437",
        "HR 4437",
        "HR4437",
    }
    for row in result["queries"]:
        assert (
            '"Supervisory Modifications for Appropriate '
            'Risk-Based Testing Act"' in row["search_query"]
        )
        assert row["search_query"].startswith(f"site:{row['domain']} ")
        assert row["candidate_query_ids"] == [
            "coverage_probe",
            "examination_supervision",
        ]
        assert row["ambiguous_candidate_query_ids"] == ["coverage_probe"]
        assert row["automatic_inclusion"] is None
        assert row["automatic_exclusion"] is None
        assert row["sponsorship_role_assigned"] is None
        assert (
            row["sponsorship_role_authority"]
            == "Congress.gov relationship data only"
        )
        assert row["acu_evidence_cannot_override_official_relevance"] is True
        assert row["official_relevance_authority"].startswith("Congress.gov")


def test_ambiguous_scope_uses_only_explicit_markers_and_keeps_all_categories() -> None:
    result = PLAN.build_query_rows(
        records=[
            record("119:hr:1", title="Credit Union Choice Act"),
            record("119:s:2", title="Credit Union Governance Act"),
        ],
        memberships=[
            membership("119:hr:1", "charter_modernization_governance"),
            membership("119:hr:1", "field_of_membership"),
            membership("119:s:2", "charter_modernization_governance"),
        ],
        review_rows=[
            review(
                "119:hr:1",
                "field_of_membership",
                triage_bucket="unresolved_weak_or_ambiguous",
            ),
            review(
                "119:s:2",
                "charter_modernization_governance",
                disposition="include_relevant",
            ),
        ],
        scope="ambiguous",
    )

    assert {row["canonical_key"] for row in result["queries"]} == {
        "119:hr:1"
    }
    assert result["selected_measure_count"] == 1
    assert result["selected_membership_count"] == 2
    assert result["coverage"][0]["candidate_query_ids"] == [
        "charter_modernization_governance",
        "field_of_membership",
    ]
    assert result["coverage"][0]["ambiguous_candidate_query_ids"] == [
        "field_of_membership"
    ]


def test_ambiguous_scope_without_review_rows_fails_closed() -> None:
    with unittest.TestCase().assertRaisesRegex(
        PLAN.QueryPlanError, "requires explicit candidate/review rows"
    ):
        PLAN.build_query_rows(
            records=[record("119:hr:1", title="Credit Union Choice Act")],
            memberships=[membership("119:hr:1", "charter")],
            scope="ambiguous",
        )


def test_generic_or_missing_title_is_retained_as_blocking_unresolved() -> None:
    result = PLAN.build_query_rows(
        records=[record("119:hr:9", title="Act")],
        memberships=[membership("119:hr:9", "coverage_probe")],
        scope="all",
    )

    assert result["queries"] == []
    assert result["coverage"] == []
    assert result["selected_measure_count"] == 1
    assert result["unresolved"][0]["canonical_key"] == "119:hr:9"
    assert (
        result["unresolved"][0]["reason"]
        == "official_title_missing_or_generic"
    )
    assert result["unresolved"][0]["absence_is_neutral"] is True
    assert result["unresolved"][0]["automatic_exclusion"] is None


def test_short_specific_act_title_is_safe_when_paired_with_bill_number() -> None:
    result = PLAN.build_query_rows(
        records=[record("119:hr:4437", official_title="SMART Act")],
        memberships=[
            membership("119:hr:4437", "examination_supervision")
        ],
        scope="all",
    )

    assert len(result["queries"]) == 4
    assert all(
        '"SMART Act"' in row["search_query"]
        and (
            '"H.R. 4437"' in row["search_query"]
            or '"H.R.4437"' in row["search_query"]
            or '"HR 4437"' in row["search_query"]
            or '"HR4437"' in row["search_query"]
        )
        for row in result["queries"]
    )


def test_domain_eras_are_congress_specific() -> None:
    result = PLAN.build_query_rows(
        records=[
            record("116:hr:1", title="Credit Union Legacy Act"),
            record("118:hr:2", title="Credit Union Merger Act"),
            record("119:hr:3", title="Credit Union Current Act"),
        ],
        memberships=[
            membership("116:hr:1", "category"),
            membership("118:hr:2", "category"),
            membership("119:hr:3", "category"),
        ],
        scope="all",
    )
    domains = {
        key: {
            row["domain"]
            for row in result["queries"]
            if row["canonical_key"] == key
        }
        for key in ("116:hr:1", "118:hr:2", "119:hr:3")
    }
    assert domains["116:hr:1"] == {"news.cuna.org", "nafcu.org"}
    assert domains["118:hr:2"] == {
        "americascreditunions.org",
        "news.cuna.org",
        "nafcu.org",
    }
    assert domains["119:hr:3"] == {"americascreditunions.org"}


def test_article_only_title_cannot_override_congress_record() -> None:
    result = PLAN.build_query_rows(
        records=[record("119:hr:8", title="An Act.")],
        memberships=[membership("119:hr:8", "category")],
        review_rows=[
            review(
                "119:hr:8",
                "category",
                official_title="Specific Trade Press Title Act",
            )
        ],
        scope="all",
    )
    assert result["queries"] == []
    assert (
        result["unresolved"][0]["reason"]
        == "official_title_missing_or_generic"
    )


def test_invalid_bill_identity_is_retained_unresolved() -> None:
    result = PLAN.build_query_rows(
        records=[
            record("119:unknown:8", title="Specific Credit Union Act")
        ],
        memberships=[membership("119:unknown:8", "category")],
        scope="all",
    )
    assert result["queries"] == []
    assert result["selected_measure_count"] == 1
    assert (
        result["unresolved"][0]["reason"]
        == "invalid_canonical_bill_identity"
    )


def test_build_writes_hash_bound_deterministic_query_plan(tmp_path: Path) -> None:
    run = tmp_path / "final_run"
    run.mkdir()
    records = run / "records.ndjson"
    memberships = run / "memberships.ndjson"
    records.write_text(
        json.dumps(
            record(
                "118:s:3623",
                official_title="Credit Card Competition Act of 2023",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    memberships.write_text(
        json.dumps(membership("118:s:3623", "interchange")) + "\n",
        encoding="utf-8",
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "union": {
                    "records_file": records.name,
                    "memberships_file": memberships.name,
                },
            }
        ),
        encoding="utf-8",
    )

    report = PLAN.build(
        run_dir=run,
        out_dir=tmp_path / "audit_query_plan",
        scope="all",
        review_ledger=None,
    )

    assert report["status"] == "PASS"
    assert report["network_requests_performed"] == 0
    assert report["production_or_mainframe_written"] is False
    assert report["query_count"] == 12
    assert report["unresolved_measure_count"] == 0
    assert report["contracts"]["absence_is_neutral"] is True
    assert (
        report["contracts"]["sponsorship_role_authority"]
        == "Congress.gov relationship data only"
    )
    for output in report["outputs"].values():
        path = tmp_path / "audit_query_plan" / output["path"]
        assert PLAN.sha256_file(path) == output["sha256"]

    reused = PLAN.build(
        run_dir=run,
        out_dir=tmp_path / "audit_query_plan",
        scope="all",
        review_ledger=None,
        resume=True,
    )
    assert reused["reused"] is True

    query_path = (
        tmp_path
        / "audit_query_plan"
        / report["outputs"]["queries"]["path"]
    )
    query_path.write_text("tampered\n", encoding="utf-8")
    with unittest.TestCase().assertRaisesRegex(
        PLAN.QueryPlanError, "output hash mismatch"
    ):
        PLAN.build(
            run_dir=run,
            out_dir=tmp_path / "audit_query_plan",
            scope="all",
            review_ledger=None,
            resume=True,
        )


class ACUBillTitleCorroborationQueryPlanTests(unittest.TestCase):
    def test_all_scope(self) -> None:
        test_all_scope_emits_exact_title_and_number_on_closed_domain_set()

    def test_ambiguous_scope(self) -> None:
        test_ambiguous_scope_uses_only_explicit_markers_and_keeps_all_categories()

    def test_ambiguous_requires_review(self) -> None:
        test_ambiguous_scope_without_review_rows_fails_closed()

    def test_generic_title(self) -> None:
        test_generic_or_missing_title_is_retained_as_blocking_unresolved()

    def test_short_specific_title(self) -> None:
        test_short_specific_act_title_is_safe_when_paired_with_bill_number()

    def test_domain_eras(self) -> None:
        test_domain_eras_are_congress_specific()

    def test_official_authority(self) -> None:
        test_article_only_title_cannot_override_congress_record()

    def test_invalid_identity(self) -> None:
        test_invalid_bill_identity_is_retained_unresolved()

    def test_hash_bound_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            test_build_writes_hash_bound_deterministic_query_plan(
                Path(temporary)
            )
