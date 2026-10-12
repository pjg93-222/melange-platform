from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, FEC / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


POLICY = load_module(
    "dashboard_legislative_workbench_tested",
    "legislation/dashboard_legislative_workbench.py",
)
REPOSITORY = load_module(
    "congress_bill_repository_tested",
    "legislation/congress_bill_repository.py",
)
UPDATER = load_module(
    "update_bill_repository_tested",
    "legislation/update_bill_repository.py",
)


class LegislativeInfluencePolicyTests(unittest.TestCase):
    def test_chair_is_harder_to_flip_and_more_powerful_as_source(self) -> None:
        modifiers = POLICY.role_modifiers("c")
        self.assertGreater(modifiers["target_resistance"], 1.0)
        self.assertGreater(modifiers["source_shock"], 1.0)

    def test_bare_vice_role_is_preserved(self) -> None:
        seats = [{"name": "Financial Services (full)", "role": "Vice"}]
        self.assertEqual(
            POLICY.jurisdiction_role_code(
                seats, "Credit Union Capital Requirements"
            ),
            "v",
        )

    def test_jurisdiction_role_is_specific_to_legislative_area(self) -> None:
        seats = [
            {
                "name": "Financial Services - Financial Institutions",
                "role": "Chair",
            }
        ]
        self.assertEqual(
            POLICY.jurisdiction_role_code(
                seats, "Credit Union Capital Requirements"
            ),
            "c",
        )
        self.assertEqual(
            POLICY.jurisdiction_role_code(seats, "Defense CU Issues"),
            "-",
        )

    def test_policy_bundle_reconfigures_domains_and_preserves_role_matrix(
        self,
    ) -> None:
        categories = [
            "Credit Union Capital Requirements",
            "Defense CU Issues",
        ]
        members = [
            {
                "name": "Chair One",
                "stances": "CA",
                "committees": [
                    {
                        "name": (
                            "Financial Services - Financial Institutions"
                        ),
                        "role": "Chair",
                    }
                ],
            },
            {
                "name": "Member Two",
                "stances": "PO",
                "committees": [],
            },
            {
                "name": "Member Three",
                "stances": "-S",
                "committees": [],
            },
        ]
        bundle = POLICY.policy_bundle(categories, members)
        self.assertEqual(bundle["schema"], "dashboard-influence-policy-v1")
        self.assertEqual(len(bundle["domains"]), 2)
        self.assertEqual(bundle["roles"][0], "c-")
        self.assertEqual(
            bundle["centrality"]["method"],
            "weighted Katz-Bonacich outbound reach",
        )
        self.assertEqual(
            bundle["centrality"]["recompute"],
            "every legislative-area change",
        )
        self.assertNotEqual(
            bundle["domains"][0]["lt_threshold_scale"],
            bundle["domains"][1]["lt_threshold_scale"],
        )

    def test_dashboard_paths_and_centrality_share_issue_kernel(self) -> None:
        template = (
            FEC / "surfaces" / "templates" / "make_dashboard_html_00.tpl"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "configureIssueNetwork();if(layout==='area')"
            "setLayout('area',true);refresh()",
            template,
        )
        self.assertIn(
            "PE[u][v]=Math.max(0,Math.min(.995",
            template,
        )
        for token in (
            "function distToTarget(t)",
            "function tracePath(sponsor,target,dT)",
            "function pathReliability(path)",
            "function issueKatzScores()",
            "CENT=issueKatzScores()",
            # The plain-language label is reused elsewhere, so bind this pin to
            # the member-summary row's own centrality value rather than the bare words.
            "<span>Reach on this issue</span>"
            "<b style=color:${centralCol(CENTPCT[v])}>",
            'data-c=central',
        ):
            self.assertIn(token, template)

    def test_area_topology_is_weighted_anchored_and_explanatory(self) -> None:
        template = (
            FEC / "surfaces" / "templates" / "make_dashboard_html_00.tpl"
        ).read_text(encoding="utf-8")
        for token in (
            "data-l=area",
            "function areaTopologyPositions()",
            "for(const sv in PE[u])",
            "if(layout==='area')setLayout('area',true)",
            "anchorScreen=anchor>=0?toScreen",
            "selected member stays fixed on screen for orientation",
            "No members or underlying ties were added or removed.",
        ):
            self.assertIn(token, template)


class CongressBillRepositoryTests(unittest.TestCase):
    def test_bill_identity_normalizes_supported_congress_types(self) -> None:
        parsed = REPOSITORY.parse_bill_identity("H.J.Res. 59", 119)
        self.assertEqual(parsed["key"], "119:hjres:59")
        self.assertEqual(parsed["display"], "H.J.Res.59")
        self.assertEqual(
            parsed["url"],
            (
                "https://www.congress.gov/bill/119th-congress/"
                "house-joint-resolution/59"
            ),
        )

    def test_snapshot_splits_original_cosponsors_and_attaches_amendments(
        self,
    ) -> None:
        seed = {
            "bill": "H.R.1919",
            "congress": 119,
            "title": "Seed title",
            "url": (
                "https://www.congress.gov/bill/119th-congress/"
                "house-bill/1919"
            ),
        }
        detail = {
            "bill": {
                "congress": 119,
                "type": "HR",
                "number": "1919",
                "title": "Live title",
                "latestAction": {
                    "actionDate": "2026-07-01",
                    "text": "Referred to committee.",
                },
                "sponsors": [{"fullName": "Rep. Example [R-XX-1]"}],
            }
        }
        cosponsors = {
            "cosponsors": [
                {
                    "fullName": "Rep. Original [D-YY-2]",
                    "bioguideId": "O000001",
                    "isOriginalCosponsor": True,
                    "sponsorshipDate": "2026-01-03",
                },
                {
                    "fullName": "Rep. Later [R-ZZ-3]",
                    "bioguideId": "L000001",
                    "isOriginalCosponsor": False,
                    "sponsorshipDate": "2026-02-03",
                },
            ]
        }
        amendments = {
            "amendments": [
                {
                    "congress": 119,
                    "type": "HAMDT",
                    "number": "12",
                    "description": "Adds a reporting requirement.",
                    "latestAction": {
                        "actionDate": "2026-06-20",
                        "text": "Agreed to by voice vote.",
                    },
                }
            ]
        }
        snapshot = REPOSITORY.normalize_bill_snapshot(
            seed,
            detail,
            cosponsors,
            amendments,
            updated_at="2026-07-29T00:00:00Z",
        )
        self.assertEqual(snapshot["title"], "Live title")
        self.assertEqual(
            [row["name"] for row in snapshot["original_cosponsors"]],
            ["Rep. Original [D-YY-2]"],
        )
        self.assertEqual(
            [row["name"] for row in snapshot["cosponsors"]],
            ["Rep. Later [R-ZZ-3]"],
        )
        self.assertEqual(snapshot["amendments"][0]["display"], "H.Amdt.12")

    def test_failed_refresh_keeps_last_good_bill_and_marks_it_stale(
        self,
    ) -> None:
        existing = {
            "schema": "congress-bill-repository-v1",
            "bills": {
                "119:hr:1919": {
                    "key": "119:hr:1919",
                    "title": "Last good title",
                    "updated_at": "2026-07-28T00:00:00Z",
                }
            },
        }
        merged = REPOSITORY.merge_refresh(
            existing,
            refreshed={},
            failures={"119:hr:1919": "temporary 500"},
            updated_at="2026-07-29T00:00:00Z",
        )
        bill = merged["bills"]["119:hr:1919"]
        self.assertEqual(bill["title"], "Last good title")
        self.assertTrue(bill["stale"])
        self.assertEqual(merged["failures"]["119:hr:1919"], "temporary 500")

    def test_corrupt_primary_recovers_from_valid_lastgood(self) -> None:
        old_bill = {
            "key": "119:hr:1919",
            "display": "H.R.1919",
            "congress": 119,
            "title": "Known good",
        }
        old_repository = {
            "schema": "congress-bill-repository-v1",
            "bills": {"119:hr:1919": old_bill},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bill_repository_live.json"
            backup = path.with_suffix(path.suffix + ".lastgood")
            path.write_text("{corrupt", encoding="utf-8")
            backup.write_text(
                json.dumps(old_repository),
                encoding="utf-8",
            )
            recovered, used_backup = UPDATER.read_repository(path)
            self.assertTrue(used_backup)
            self.assertEqual(
                recovered["bills"]["119:hr:1919"]["title"],
                "Known good",
            )
            candidate = REPOSITORY.merge_refresh(
                recovered,
                refreshed={},
                failures={"119:hr:1919": "temporary 500"},
            )
            REPOSITORY.write_repository_atomic(path, candidate)
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8"))["bills"][
                    "119:hr:1919"
                ]["title"],
                "Known good",
            )

    def test_partial_refresh_is_not_reported_as_pass(self) -> None:
        self.assertEqual(UPDATER.refresh_status(614, 0), "PASS")
        self.assertEqual(UPDATER.refresh_status(613, 1), "PARTIAL")
        self.assertEqual(UPDATER.refresh_status(0, 614), "STALE")

    def test_live_repository_requires_matching_pass_receipt(self) -> None:
        bundle = {
            "members": [{"bills": [{"bill": "H.R.1919", "congress": 119}]}]
        }
        bill = {
            "key": "119:hr:1919",
            "display": "H.R.1919",
            "congress": 119,
            "title": "Current title",
        }
        repository = {
            "schema": "congress-bill-repository-v1",
            "updated_at": "2026-08-28T12:00:00Z",
            "source": "https://www.congress.gov/",
            "bills": {bill["key"]: bill},
            "failures": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_path = root / "member_desk_bundle.json"
            repository_path = root / "bill_repository_live.json"
            receipt_path = root / "receipt.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            repository_path.write_text(json.dumps(repository), encoding="utf-8")
            UPDATER.write_refresh_receipt(
                receipt_path,
                result={
                    "status": "PASS",
                    "seeded": 1,
                    "refreshed": 1,
                    "failed": 0,
                    "transport": "test",
                },
                bundle_path=bundle_path,
                output_path=repository_path,
            )
            loaded = REPOSITORY.validate_live_repository(
                bundle_path, repository_path, receipt_path
            )
            self.assertEqual(len(loaded["bills"]), 1)
            repository_path.write_text(
                json.dumps({**repository, "failures": {"x": "tampered"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                REPOSITORY.RepositoryError, "not hash-bound"
            ):
                REPOSITORY.validate_live_repository(
                    bundle_path, repository_path, receipt_path
                )


class DashboardWorkbenchTemplateTests(unittest.TestCase):
    def test_member_desk_exposes_repository_tracking_and_builder_state(
        self,
    ) -> None:
        template = (
            FEC / "surfaces" / "templates" / "make_member_desk_00.tpl"
        ).read_text(encoding="utf-8")
        for token in (
            "data-v=bills",
            "mdesk_bill_tracker_v1",
            "mdesk_coal_snapshots_v1",
            "id=cbparty",
            "id=cbcommittee",
            "id=billmore",
            "Suggested next cosponsors",
            "Amendments",
            "Congress.gov",
            "adaptiveEdgeProbability",
            "ADJPROB",
            "if(!(base>0))return 0;",
        ):
            self.assertIn(token, template)

    def test_network_view_uses_same_domain_and_chair_policy(self) -> None:
        template = (
            FEC / "surfaces" / "templates" / "make_dashboard_html_00.tpl"
        ).read_text(encoding="utf-8")
        self.assertIn("configureIssueNetwork", template)
        self.assertIn("target_resistance", template)
        self.assertIn("source_shock", template)
        self.assertIn("lt_threshold_scale", template)

    def test_producers_embed_live_repository_and_shared_policy(self) -> None:
        desk = (FEC / "surfaces" / "make_member_desk.py").read_text(encoding="utf-8")
        dashboard = (FEC / "surfaces" / "build_dashboard_bundle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("'bill_live': BILL_LIVE", desk)
        self.assertIn("'influence_policy': influence_policy", desk)
        self.assertIn("'influence_policy': influence_policy", dashboard)

    def test_live_repository_feeds_the_bills_ledger_not_only_the_display(
        self,
    ) -> None:
        """2026-08-29 (Paul): collector-captured cosponsor joins reach the
        LEDGER through a verification gate, not just bundle['bill_live']."""
        desk = (FEC / "surfaces" / "make_member_desk.py").read_text(encoding="utf-8")
        self.assertIn("CRU.merge_collector_roster_updates(", desk)
        self.assertIn("'collector_roster_updates.ndjson'", desk)
        # the fourth sidecar merges BEFORE the role-precedence collapse
        self.assertLess(
            desk.index("CRU.merge_collector_roster_updates("),
            desk.index("# collapse duplicate rows"),
        )
        # and its rows are declared to the legislation source reconciliation
        integrity = (FEC / "audit" / "dashboard_integrity.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("bill_collector_roster_rows", integrity)
        self.assertIn("'bill_collector_roster_excluded'", integrity)
        self.assertIn("media_audit['bill_collector_roster_rows']", desk)

    def test_autonomous_refresh_can_promote_the_live_page(self) -> None:
        updater = (FEC / "legislation" / "update_bill_repository.py").read_text(
            encoding="utf-8"
        )
        browser = (
            FEC / "refresh_bill_repository_headless.mjs"
        ).read_text(encoding="utf-8")
        self.assertIn("--rebuild-dashboard", updater)
        self.assertIn('"make_member_desk.py"', updater)
        self.assertIn('"make_combined.py"', updater)
        self.assertNotIn("CONGRESS_API_KEY", updater)
        self.assertIn("refresh_bill_repository_headless.mjs", updater)
        self.assertIn("launchPersistentContext", browser)
        self.assertIn("headless: true", browser)
        self.assertIn("Amendment count mismatch", browser)
        self.assertIn("human-verification", browser)


if __name__ == "__main__":
    unittest.main()
