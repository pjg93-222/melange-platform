from __future__ import annotations

from pathlib import Path
import unittest


FEC = Path(__file__).resolve().parents[2]

DIRECT_SCORING_CONSUMERS = (
    "legislation/build_accepted_legislation_workbook.py",
    "surfaces/build_dashboard_bundle.py",
    "legislation/build_legislation_base_network.py",
    "legislation/build_legislation_direction_ledger.py",
    "legislation/compute_legislation_policy_scenarios.py",
    "surfaces/make_member_desk.py",
    "legislation/materialize_legislation_base_and_bipartisan.py",
    "legislation/materialize_legislation_rollcall_aggregate.py",
    "moneyball_core.py",
    "legislation/recompute_legislative_workbook.py",
    "run_pipeline.py",
    "legislation/run_legislation_dashboard_member_desk.py",
    "legislation/run_legislation_network_dependents.py",
    "legislation/run_legislation_pillars_and_ranks.py",
    "legislation/run_legislation_say_do_first_pass.py",
    "legislation/run_legislation_say_do_second_pass.py",
    "legislation/run_legislation_second_pass_dependents.py",
    "weekly_refresh.py",
)

NON_AUTHORITATIVE_IDENTIFIERS = (
    "review_sidecar_current.json",
    "structured_relevance_current.json",
    "partial_review_prefill",
    "partial_review_queue",
    "membership_review_proposals.ndjson",
    "staged_review_queue.ndjson",
)


class LegislationSidecarDownstreamIsolationTests(unittest.TestCase):
    def test_direct_scoring_consumers_have_no_sidecar_discovery_path(
        self,
    ) -> None:
        for name in DIRECT_SCORING_CONSUMERS:
            path = FEC / name
            self.assertTrue(path.is_file(), name)
            source = path.read_text(encoding="utf-8").casefold()
            for identifier in NON_AUTHORITATIVE_IDENTIFIERS:
                self.assertNotIn(
                    identifier.casefold(),
                    source,
                    f"{name} directly references {identifier}",
                )


if __name__ == "__main__":
    unittest.main()
