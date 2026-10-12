from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

import legislation.build_accepted_legislation_generation as accepted
from legislation import build_accepted_legislation_workbook as accepted_workbook
from legislation import build_legislation_base_network as base_network
from legislation import build_legislation_direction_ledger as direction_ledger
from legislation import build_legislation_workbook_numeric_plan as numeric_plan
from legislation import collect_official_rollcall_events as rollcalls
from legislation import compute_legislation_policy_scenarios as scenarios
from tests.legislation.test_build_accepted_legislation_generation import fixtures


def read_ndjson(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def rewrite_review_categories(inputs: dict[str, Path]) -> None:
    """Use canonical 18-category identifiers recognized by roll-call staging."""

    review_manifest_path = inputs["review_manifest_path"]
    manifest = json.loads(review_manifest_path.read_text(encoding="utf-8"))
    ledger_path = review_manifest_path.parent / manifest["ledger_file"]
    rows = read_ndjson(ledger_path)
    rows[0].update(
        {
            "query_id": "credit_union_capital_requirements",
            "category_label": "CU Capital Requirements",
        }
    )
    rows[1].update(
        {
            "query_id": "credit_union_tax_status",
            "category_label": "CU Tax Status",
        }
    )
    ledger_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    rename = {
        "capital": "credit_union_capital_requirements",
        "tax": "credit_union_tax_status",
    }
    run_manifest_path = inputs["run_manifest_path"]
    run_manifest = json.loads(
        run_manifest_path.read_text(encoding="utf-8")
    )
    raw_path = (
        run_manifest_path.parent
        / run_manifest["union"]["memberships_file"]
    )
    raw_rows = read_ndjson(raw_path)
    for row in raw_rows:
        row["query_id"] = rename.get(row["query_id"], row["query_id"])
    raw_path.write_text(
        "\n".join(json.dumps(row) for row in raw_rows) + "\n",
        encoding="utf-8",
    )
    run_manifest["selected_query_ids"] = [
        rename.get(query_id, query_id)
        for query_id in run_manifest["selected_query_ids"]
    ]
    for entry in run_manifest["queries"]:
        query_id = rename.get(entry["query_id"], entry["query_id"])
        entry["query_id"] = query_id
        entry["query_file"] = f"queries/{query_id}.json"
    run_manifest["union"]["memberships_sha256"] = (
        accepted.sha256_rows_like_javascript(raw_rows)
    )
    run_manifest_path.write_text(
        json.dumps(run_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["discovery_manifest_sha256"] = accepted.sha256_file(
        run_manifest_path
    )
    review_manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    acceptance_manifest_path = inputs[
        "historical_membership_acceptance_manifest_path"
    ]
    acceptance_manifest = json.loads(
        acceptance_manifest_path.read_text(encoding="utf-8")
    )
    gate = acceptance_manifest[
        "historical_membership_acceptance_gate"
    ]
    gate["current_manifest_sha256"] = accepted.sha256_file(
        run_manifest_path
    )
    gate["review_ledger_sha256"] = accepted.sha256_file(ledger_path)
    acceptance_manifest_path.write_text(
        json.dumps(acceptance_manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def add_distinct_later_cosponsor(
    relationships_manifest_path: Path,
) -> None:
    """Keep the duplicate-role hazard and add a true later cosponsor."""

    manifest = json.loads(
        relationships_manifest_path.read_text(encoding="utf-8")
    )
    ledger_path = (
        relationships_manifest_path.parent / manifest["edges_file"]
    )
    rows = read_ndjson(ledger_path)
    rows.append(
        {
            "canonical_key": "118:hr:10",
            "congress": 118,
            "type": "hr",
            "number": 10,
            "display_number": "H.R.10",
            "title": "Specific Credit Union Act",
            "role": "Cosponsor",
            "member": "Later, Lee",
            "bioguide_id": "L000001",
            "original_cosponsor": False,
            "relationship_status": "current",
            "sponsorship_date": "02/01/2023",
            "source_url": (
                "https://www.congress.gov/bill/118th-congress/"
                "house-bill/10/cosponsors"
            ),
        }
    )
    ledger_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    manifest["edge_count"] = len(rows)
    manifest["edges_sha256"] = accepted.sha256_rows_like_javascript(
        rows
    )
    relationships_manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def with_input_lines(rows: list[dict]) -> list[dict]:
    return [
        {**row, "_input_line": line_number}
        for line_number, row in enumerate(rows, start=1)
    ]


class CrossProducerAcceptedLedgerContractTests(unittest.TestCase):
    def test_roles_and_zero_rows_survive_every_direct_consumer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            inputs = fixtures(Path(temporary))
            rewrite_review_categories(inputs)
            add_distinct_later_cosponsor(
                inputs["relationships_manifest_path"]
            )
            accepted.build_generation(**inputs)
            ledger_path = (
                inputs["out_dir"]
                / "accepted_legislation_relationships.ndjson"
            )
            canonical_rows = read_ndjson(ledger_path)

            # Two reviewed category memberships (one included, one excluded)
            # repeat across Sponsor, strongest Original Cosponsor, distinct
            # later Cosponsor, and withdrawn relationship rows.  The input
            # also has a weaker current Cosponsor row for M000001: the
            # strongest role must win without double-counting that member.
            self.assertEqual(len(canonical_rows), 8)
            self.assertEqual(
                {row["membership_status"] for row in canonical_rows},
                {"included", "excluded"},
            )
            self.assertEqual(
                {row["relationship_status"] for row in canonical_rows},
                {"current", "withdrawn"},
            )
            self.assertTrue(
                all(
                    base_network.BIOGUIDE_RE.fullmatch(
                        row["member_bioguide_id"]
                    )
                    for row in canonical_rows
                )
            )
            self.assertEqual(
                sum(
                    row["category"] == "credit_union_capital_requirements"
                    for row in canonical_rows
                ),
                4,
            )
            included_rows = [
                row
                for row in canonical_rows
                if row["membership_status"] == "included"
            ]
            self.assertEqual(
                {
                    row["member_bioguide_id"]: (
                        row["role"],
                        row["relationship_status"],
                    )
                    for row in included_rows
                },
                {
                    "S000001": ("sponsor", "current"),
                    "M000001": ("original_cosponsor", "current"),
                    "L000001": ("cosponsor", "current"),
                    "F000001": ("cosponsor", "withdrawn"),
                },
            )
            self.assertEqual(
                sum(
                    row["member_bioguide_id"] == "M000001"
                    and row["membership_status"] == "included"
                    for row in canonical_rows
                ),
                1,
            )

            relationships = base_network.load_relationships(ledger_path)
            network_edges, contributions, exclusions, names = (
                base_network.construct_edges(relationships)
            )
            self.assertEqual(len(network_edges), 2)
            self.assertEqual(len(contributions), 2)
            self.assertEqual(
                {
                    row["cosponsor_bioguide_id"]: row["cosponsor_role"]
                    for row in contributions
                },
                {
                    "M000001": "original_cosponsor",
                    "L000001": "cosponsor",
                },
            )
            self.assertEqual(
                {
                    row["cosponsor_bioguide_id"]: row["role_factor"]
                    for row in contributions
                },
                {"M000001": 1.0, "L000001": 0.5},
            )
            self.assertTrue(
                all(row["current_cosponsor_count"] == 2 for row in contributions)
            )
            self.assertEqual(
                {
                    row["To"]: (row["NOriginal"], row["NRegular"])
                    for row in network_edges
                },
                {
                    "M000001": (1, 0),
                    "L000001": (0, 1),
                },
            )
            self.assertEqual(
                {row["reason"] for row in exclusions},
                {"membership_excluded", "relationship_withdrawn"},
            )
            self.assertEqual(len(exclusions), 5)
            self.assertTrue(
                all(row["network_contribution"] == 0.0 for row in exclusions)
            )
            self.assertTrue(all(row["provenance"] for row in exclusions))
            self.assertEqual(
                {
                    (
                        row["member_bioguide_id"],
                        row["category"],
                        row["reason"],
                    )
                    for row in exclusions
                },
                {
                    (
                        "F000001",
                        "credit_union_capital_requirements",
                        "relationship_withdrawn",
                    ),
                    *{
                        (
                            bioguide,
                            "credit_union_tax_status",
                            "membership_excluded",
                        )
                        for bioguide in {
                            "S000001",
                            "M000001",
                            "L000001",
                            "F000001",
                        }
                    },
                },
            )
            self.assertEqual(
                set(names),
                {"S000001", "M000001", "L000001", "F000001"},
            )

            edges, candidates, by_edge = (
                direction_ledger.accepted_edges_and_candidates(ledger_path)
            )
            self.assertEqual(len(edges), 1)
            only_direction_edge = next(iter(edges))
            self.assertEqual(
                only_direction_edge.measure.canonical, "118:hr:10"
            )
            self.assertEqual(
                only_direction_edge.category, "CU Capital Requirements"
            )
            self.assertEqual(candidates, [])
            self.assertEqual(dict(by_edge), {})

            measures, direction_audit = rollcalls.load_accepted_measures(
                ledger_path
            )
            self.assertEqual(len(measures), 1)
            only_measure = next(iter(measures))
            self.assertEqual(only_measure.canonical, "118:hr:10")
            self.assertEqual(
                set(measures[only_measure]["categories"]),
                {"CU Capital Requirements"},
            )
            self.assertEqual(len(direction_audit), 1)
            self.assertEqual(
                direction_audit[0]["reason"],
                "missing_explicit_category_direction",
            )

            workbook = accepted_workbook.build_materialization(
                canonical_rows,
                {("118:hr:10", "CU Capital Requirements"): 1},
                generation_id=accepted.sha256_file(ledger_path),
                selected_role_policy="workbook_legacy_4_2_1",
            )
            self.assertEqual(
                workbook["archive_leg"],
                {
                    "Later, Lee": {"CU Capital Requirements": 0.66},
                    "Member, One": {"CU Capital Requirements": 1.32},
                    "Sponsor, Sam": {"CU Capital Requirements": 1.98},
                },
            )
            self.assertEqual(len(workbook["archive_rows"]), 4)
            self.assertEqual(
                {
                    sheet: len(workbook["model_sheets"][sheet]) - 1
                    for sheet in (
                        "Sponsor Interactions",
                        "Original Co-Sponsor Interaction",
                        "Co-Sponsor Interactions",
                    )
                },
                {
                    "Sponsor Interactions": 1,
                    "Original Co-Sponsor Interaction": 1,
                    "Co-Sponsor Interactions": 1,
                },
            )
            workbook_rows = workbook["routing_audit"]["rows"]
            self.assertEqual(
                dict(
                    sorted(
                        (
                            disposition,
                            sum(
                                row["disposition"] == disposition
                                for row in workbook_rows
                            ),
                        )
                        for disposition in {
                            row["disposition"] for row in workbook_rows
                        }
                    )
                ),
                {
                    "excluded_membership": 4,
                    "selected": 3,
                    "withdrawn_relationship": 1,
                },
            )
            workbook_zero_rows = [
                row
                for row in workbook_rows
                if row["disposition"]
                in {"excluded_membership", "withdrawn_relationship"}
            ]
            self.assertTrue(
                all(
                    row["contribution"] == 0.0
                    and row["archive_role_weight"] == 0.0
                    and row["decay_weight"] == 0.0
                    and not row["included_in_activity_workbooks"]
                    for row in workbook_zero_rows
                )
            )

            scenario_directions = {
                ("118:hr:10", "CU Capital Requirements"): {
                    "direction_sign": 1,
                    "direction_provenance": {
                        "source_url": (
                            "https://www.congress.gov/bill/"
                            "118th-congress/house-bill/10"
                        ),
                        "source_reference": "official provisions",
                        "rationale": "Fixture direction for contract test.",
                    },
                }
            }
            scenario_events = scenarios.build_events(
                with_input_lines(canonical_rows),
                scenario_directions,
            )
            self.assertEqual(len(scenario_events), 8)
            active_scenario_events = [
                row for row in scenario_events if row["activity_included"]
            ]
            self.assertEqual(
                {
                    row["member_bioguide_id"]: row["role"]
                    for row in active_scenario_events
                },
                {
                    "S000001": "sponsor",
                    "M000001": "original_cosponsor",
                    "L000001": "cosponsor",
                },
            )
            self.assertEqual(
                sum(
                    row["member_bioguide_id"] == "M000001"
                    and row["activity_included"]
                    for row in scenario_events
                ),
                1,
            )
            zero_scenario_events = [
                row for row in scenario_events if not row["activity_included"]
            ]
            self.assertEqual(len(zero_scenario_events), 5)
            self.assertTrue(
                all(
                    set(row["policy_contributions"].values()) == {0.0}
                    for row in zero_scenario_events
                )
            )
            self.assertEqual(
                {
                    row["zero_weight_reason"] for row in zero_scenario_events
                },
                {"membership_excluded", "relationship_withdrawn"},
            )

            members = [
                {
                    "name": row["member_name"],
                    "bioguide_id": row["member_bioguide_id"],
                }
                for row in sorted(
                    {
                        row["member_bioguide_id"]: row
                        for row in canonical_rows
                    }.values(),
                    key=lambda row: row["member_bioguide_id"],
                )
            ]
            by_bioguide = {
                row["bioguide_id"]: index
                for index, row in enumerate(members)
            }
            numeric_events, numeric_counts = numeric_plan.accepted_events(
                canonical_rows,
                generation_id=accepted.sha256_file(ledger_path),
                directions={
                    ("118:hr:10", "CU Capital Requirements"): 1
                },
                by_bioguide=by_bioguide,
                members=members,
                cfpb_routing={},
            )
            self.assertEqual(
                {
                    members[row["member_index"]]["bioguide_id"]: row["role"]
                    for row in numeric_events
                },
                {
                    "S000001": "sponsor",
                    "M000001": "original_cosponsor",
                    "L000001": "cosponsor",
                },
            )
            self.assertEqual(numeric_counts["accepted_bill_events"], 3)
            self.assertEqual(numeric_counts["non_scoring_or_inactive"], 5)


if __name__ == "__main__":
    unittest.main()
