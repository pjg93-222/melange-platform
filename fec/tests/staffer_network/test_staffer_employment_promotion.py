import csv
from pathlib import Path
import tempfile
import unittest

from fec.staffer_network.employment_promotion import (
    ContainerDirectory,
    MemberDirectory,
    _affiliation_memberships,
    _groups_and_edges,
    _month_bounds,
)


class EmploymentPromotionTest(unittest.TestCase):
    def test_voteview_member_office_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "members.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["bioname", "bioguide_id"])
                writer.writeheader()
                writer.writerow({"bioname": "VITTER, David", "bioguide_id": "V000127"})
            directory = ContainerDirectory(MemberDirectory(path), [])
            result = directory.resolve("lda:lobbyist:1", "Senator David Vitter")
            self.assertEqual(result["normalized_container_id"], "member-office:V000127")
            self.assertEqual(result["container_kind"], "member_office")

    def test_person_bound_dba_registrant_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "members.csv"
            path.write_text("bioname,bioguide_id\n", encoding="utf-8")
            directory = ContainerDirectory(MemberDirectory(path), [{
                "lda_source_person_id": "lda:lobbyist:147663",
                "registrant_source_org_id": "lda:registrant:11322",
                "employer_name": "CREDIT UNION NATIONAL ASSOCIATION. INC. DBA AMERICA'S CREDIT UNIONS",
            }])
            result = directory.resolve("lda:lobbyist:147663", "America's Credit Unions")
            self.assertEqual(result["normalized_container_id"], "lda:registrant:11322")
            self.assertEqual(result["container_kind"], "registrant")

    def test_shared_affiliation_weight_uses_overlap(self):
        memberships = [
            {
                "person_id": "p1", "container_id": "office:a", "container_kind": "other_org",
                "relationship_type": "EMPLOYMENT", "start_latest": "2020-01-01",
                "end_earliest": "2021-01-01", "support_ids": ["s1"],
            },
            {
                "person_id": "p2", "container_id": "office:a", "container_kind": "other_org",
                "relationship_type": "EMPLOYMENT", "start_latest": "2020-07-01",
                "end_earliest": "2021-07-01", "support_ids": ["s2"],
            },
        ]
        groups, edges = _groups_and_edges(memberships, tau_days=365.2425)
        self.assertEqual(groups[0]["person_count"], 2)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["overlap_days"], 184)
        self.assertGreater(edges[0]["edge_weight"], 0)
        self.assertLess(edges[0]["edge_weight"], 1)

    def test_chamber_wide_container_group_emitted_without_co_tenure_edge(self):
        # STAFFA-8 (owner ruling 2026-09-07, extended to this module's own two chamber-wide
        # buckets, CO_TENURE_EXCLUDED_CONTAINER_IDS): a chamber-wide employer string says two
        # people were paid by the same institution, not that they shared a workplace, so the
        # affiliation GROUP is still emitted but no person-person clique is enumerated over it.
        memberships = [
            {
                "person_id": "p1", "container_id": "organization:united-states-senate",
                "container_kind": "other_org", "relationship_type": "EMPLOYMENT",
                "start_latest": "2020-01-01", "end_earliest": "2021-01-01",
                "support_ids": ["s1"],
            },
            {
                "person_id": "p2", "container_id": "organization:united-states-senate",
                "container_kind": "other_org", "relationship_type": "EMPLOYMENT",
                "start_latest": "2020-07-01", "end_earliest": "2021-07-01",
                "support_ids": ["s2"],
            },
        ]
        stats = {}
        groups, edges = _groups_and_edges(memberships, tau_days=365.2425, stats=stats)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["container_id"], "organization:united-states-senate")
        self.assertEqual(groups[0]["person_count"], 2)
        self.assertEqual(edges, [])
        # STAFFA-4.v2 reserved this counter precisely so an excluded-container skip is never
        # folded into co_tenure_pairs_rejected_zero_overlap (VERIFY_STAFFA.md: conflating the
        # two would misreport a container exclusion as an overlap rejection).
        self.assertEqual(stats["co_tenure_pairs_skipped_excluded_container"], 1)

    def test_month_bounds_are_half_open_for_end(self):
        self.assertEqual(_month_bounds("2023-11", boundary="start"), ("2023-11-01", "2023-11-30"))
        self.assertEqual(_month_bounds("2023-11", boundary="end"), ("2023-11-02", "2023-12-01"))

    def test_registrant_membership_uses_observed_years_not_envelope(self):
        rows = _affiliation_memberships([], [{
            "episode_id": "lda:registrant-employment:x",
            "lda_source_person_id": "lda:lobbyist:1",
            "registrant_source_org_id": "lda:registrant:2",
            "start_year": 2022,
            "end_year": 2025,
            "filing_years": [2022, 2025],
        }])
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [(row["start_earliest"], row["end_earliest"]) for row in rows],
            [("2022-01-01", "2023-01-01"), ("2025-01-01", "2026-01-01")],
        )


if __name__ == "__main__":
    unittest.main()
