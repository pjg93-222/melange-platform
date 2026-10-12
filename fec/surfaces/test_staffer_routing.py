from datetime import date, timedelta
import time
import unittest

from fec.surfaces.staffer_routing import AdvocacyRouter


def episode(person, employer, start="2020-01-01", end="2021-01-01", **extra):
    return {"person_id": person, "employer_id": employer, "start": start, "end_exclusive": end,
            "confidence": 1.0, "provenance": {"fixture": True, "person_id": person}, **extra}


def stated(person, employer="user-stated-team", as_of="2026-09-04", stated_end=True):
    """Mirrors staffer_live_service._user_episodes: a dated statement of a present fact."""
    end = (date.fromisoformat(as_of) + timedelta(days=1)).isoformat() if stated_end else None
    return episode(person, employer, as_of, end, as_of=as_of, current_evidence=True, user_asserted=True,
                   review_only=True, confidence="UNKNOWN", date_precision="USER_STATEMENT_AS_OF_DAY")


def current(person, employer):
    return episode(person, employer, "2026-01-01", "2026-09-05", current_evidence=True, as_of="2026-09-04")


class RoutingTests(unittest.TestCase):
    def test_every_coworker_and_activity_collaborator_is_reachable_by_stable_pages(self):
        people = ['root'] + [f'p{i:03}' for i in range(237)]
        nodes = {p: {'id': p, 'kind': 'person', 'label': 'Same Name'} for p in people}
        episodes = [episode(p, 'office') for p in people]
        episodes += [episode(p, 'activity:test', relationship='co_reported_lobbying_activity') for p in people]
        router = AdvocacyRouter(nodes, episodes, 'pages', '2026-09-05')
        for function, field in ((router.coworkers, 'coworkers'), (router.reported_collaborators, 'collaborators')):
            observed = []
            for offset in range(0, 250, 50):
                page = function('root', {'limit': 50, 'offset': offset})
                self.assertEqual(page['total'], 237)
                self.assertEqual(page['has_more'], offset < 200)
                observed.extend(row['person_id'] for row in page[field])
            self.assertEqual(observed, people[1:])
            self.assertEqual(function('root', {'offset': 300})[field], [])

    def make(self, episodes, as_of="2026-09-04", **node_changes):
        nodes = {name: {"id": name, "kind": "person", "label": name.title()} for name in ("gordon", "stephanie", "greg", "greg_other")}
        for name, changes in node_changes.items():
            nodes[name].update(changes)
        return AdvocacyRouter(nodes, episodes, "r1", as_of)

    def test_same_firm_current_evidence_and_identity_boundary(self):
        router = self.make([current("gordon", "firm"), current("stephanie", "firm"), current("greg", "firm")])
        route = router.route("gordon", "stephanie")
        self.assertEqual(route["status"], "found")
        self.assertEqual(route["cost"], 1)
        self.assertEqual(route["hops"][0]["relationship"], "current_coworker")
        self.assertEqual(len(route["hops"][0]["provenance"]), 2)
        self.assertEqual(router.route("gordon", "greg_other")["status"], "unreachable")

    def test_nonoverlap_and_abutting_intervals_never_link_through_hub(self):
        router = self.make([episode("gordon", "firm"), episode("stephanie", "firm", "2021-01-01", "2022-01-01")])
        self.assertEqual(router.route("gordon", "stephanie")["status"], "unreachable")
        self.assertEqual(router.coworkers("gordon")["coworkers"], [])

    def test_astar_zero_heuristic_chooses_cheaper_two_hop_path(self):
        router = self.make([episode("gordon", "old"), episode("stephanie", "old"),
                            current("gordon", "new1"), current("greg", "new1"),
                            current("greg", "new2"), current("stephanie", "new2")])
        route = router.route("gordon", "stephanie")
        self.assertEqual(route["node_ids"], ["gordon", "greg", "stephanie"])
        self.assertEqual(route["cost"], 2)
        self.assertEqual(route["heuristic"], 0)
        self.assertEqual(router.route("gordon", "stephanie", {"prefer_current": False})["node_ids"], ["gordon", "stephanie"])

    def test_stale_current_snapshot_is_historical_not_current(self):
        episodes = [episode(p, "firm", "2026-01-01", "2026-07-18", current_evidence=True, as_of="2026-07-17") for p in ("gordon", "stephanie")]
        router = self.make(episodes)
        hop = router.route("gordon", "stephanie")["hops"][0]
        self.assertEqual(hop["temporal_status"], "historical")
        self.assertEqual(hop["cost"], 3)
        self.assertEqual(router.route("gordon", "stephanie", {"include_historical": False})["status"], "unreachable")

    def test_review_unresolved_and_user_assertion_filters(self):
        for flag, option in (("review_only", "include_review"), ("unresolved", "include_unresolved"), ("user_asserted", "include_user_asserted")):
            router = self.make([episode("gordon", "firm", **{flag: True}), episode("stephanie", "firm")])
            self.assertEqual(router.route("gordon", "stephanie", {option: False})["status"], "unreachable")
            self.assertEqual(router.route("gordon", "stephanie", {option: True})["status"], "found")

    def test_user_stated_current_coworkers_stand_until_a_later_statement(self):
        """A statement of present fact does not lapse because the calendar advanced."""
        activity = [episode(p, "activity:q2", "2026-04-01", "2026-07-01", as_of="2026-07-17",
                            relationship="co_reported_lobbying_activity", continuous_employment_asserted=False)
                    for p in ("gordon", "stephanie")]
        for as_of in ("2026-09-04", "2026-09-05", "2026-10-04"):
            for stated_end in (True, False):
                pair = [stated(p, stated_end=stated_end) for p in ("gordon", "stephanie")]
                router = self.make(pair + activity, as_of=as_of)
                route = router.route("gordon", "stephanie", {"include_user_asserted": True})
                hop = route["hops"][0]
                self.assertEqual((as_of, stated_end, route["cost"], hop["relationship"], hop["temporal_status"]),
                                 (as_of, stated_end, 1, "current_coworker", "current"))
                self.assertTrue(hop["user_asserted"])
                self.assertEqual(hop["start"], "2026-09-04")
                # The checkbox stays the only gate: off, the stated edge is simply gone.
                fallback = router.route("gordon", "stephanie")
                self.assertEqual((fallback["cost"], fallback["hops"][0]["relationship"]),
                                 (2, "co_reported_lobbying_activity"))
                self.assertEqual(self.make(pair, as_of=as_of).route("gordon", "stephanie")["status"], "unreachable")

    def test_a_later_user_statement_supersedes_the_earlier_one(self):
        episodes = [stated(p, as_of=day) for day in ("2026-09-04", "2026-09-20") for p in ("gordon", "stephanie")]
        hop = self.make(episodes, as_of="2026-09-25").route("gordon", "stephanie", {"include_user_asserted": True})["hops"][0]
        self.assertEqual((hop["cost"], hop["temporal_status"], hop["start"]), (1, "current", "2026-09-20"))
        # A statement establishes nothing current about the days before it was made.
        early = self.make(episodes, as_of="2026-09-01").route("gordon", "stephanie", {"include_user_asserted": True})
        self.assertEqual((early["cost"], early["hops"][0]["temporal_status"]), (3, "unknown_current_status"))

    def test_policy_is_opt_in_and_never_a_coworker(self):
        router = self.make([], gordon={"policy_ids": ["finance"]}, stephanie={"policy_ids": ["finance"]})
        self.assertEqual(router.route("gordon", "stephanie")["status"], "unreachable")
        hop = router.route("gordon", "stephanie", {"include_policy": True})["hops"][0]
        self.assertEqual(hop["relationship"], "shared_policy_weak")
        self.assertEqual(router.coworkers("gordon", {"include_policy": True})["coworkers"], [])

    def test_missing_id_unlimited_routes_nonnegative_costs_and_zero_hop(self):
        router = self.make([current("gordon", "firm"), current("stephanie", "firm")])
        self.assertEqual(router.route("nobody", "stephanie")["status"], "unknown_person")
        self.assertEqual(router.route("gordon", "stephanie", {"max_neighbor_checks": 1})["status"], "found")
        self.assertEqual(router.route("gordon", "gordon")["cost"], 0)
        for value in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                router.route("gordon", "stephanie", {"current_cost": value})

    def test_cache_is_revision_scoped_and_results_are_isolated(self):
        router = self.make([current("gordon", "firm"), current("stephanie", "firm")])
        first = router.route("gordon", "stephanie")
        first["hops"][0]["cost"] = 999
        self.assertEqual(router.route("gordon", "stephanie")["hops"][0]["cost"], 1)
        router.revision = "r2"
        self.assertEqual(router.route("gordon", "stephanie")["revision"], "r2")
        self.assertEqual(len(router._cache), 2)

    def test_undated_evidence_is_rejected_except_explicit_dated_snapshot(self):
        router = self.make([episode("gordon", "firm", None, None), episode("stephanie", "firm")])
        self.assertEqual(router.rejected_episodes, 1)
        self.assertEqual(router.route("gordon", "stephanie")["status"], "unreachable")
        router = self.make([episode(p, "firm", None, None, current_evidence=True, as_of="2026-09-04") for p in ("gordon", "stephanie")])
        self.assertEqual(router.route("gordon", "stephanie")["hops"][0]["start"], "2026-09-04")

    def test_filing_year_gaps_never_create_associations(self):
        router = self.make([episode("gordon", "firm", "2020-01-01", "2025-01-01", filing_years=[2020, 2024], continuous_employment_asserted=False),
                            episode("stephanie", "firm", "2022-01-01", "2023-01-01", filing_years=[2022], continuous_employment_asserted=False)])
        self.assertEqual(router.route("gordon", "stephanie", {"include_weak": True})["status"], "unreachable")

    def test_filing_associations_are_optional_weak_and_never_coworkers(self):
        router = self.make([episode(p, "firm", filing_years=[2024, 2026], continuous_employment_asserted=False,
                                   confidence="SOURCE_NATIVE_FILING_ASSOCIATION") for p in ("gordon", "stephanie")])
        self.assertEqual(router.route("gordon", "stephanie")["status"], "unreachable")
        hop = router.route("gordon", "stephanie", {"include_weak": True})["hops"][0]
        self.assertEqual(hop["relationship"], "co_reported_firm_association")
        self.assertEqual(hop["filing_years"], [2024, 2026])
        self.assertIsNone(hop["start"])
        self.assertIsNone(hop["confidence"])
        self.assertTrue(hop["weak"])
        self.assertIn("overlap not established", hop["confidence_scope"])
        self.assertEqual(hop["cost"], 5)
        self.assertEqual(router.coworkers("gordon", {"include_weak": True})["coworkers"], [])
        self.assertEqual(router.route("gordon", "stephanie", {"include_weak": True, "filing_cost": 0})["cost"], 3)

    def test_unknown_identity_is_not_an_intermediate_shortcut(self):
        episodes = [current("gordon", "first"), current("greg", "first"), current("greg", "second"), current("stephanie", "second")]
        router = self.make(episodes, greg={"name_status": "UNRESOLVED_SOURCE_NAME"})
        self.assertEqual(router.route("gordon", "stephanie")["status"], "unreachable")
        self.assertEqual(router.route("greg", "stephanie")["status"], "unknown_person")
        router = self.make(episodes, greg={"label": ""})
        self.assertEqual(router.route("gordon", "stephanie", {"include_unresolved": True})["status"], "unreachable")

    def test_exact_reported_activity_routes_by_default_but_is_not_tenure(self):
        episodes = [episode(p, "activity:abc", "2026-04-01", "2026-07-01",
                            relationship="co_reported_lobbying_activity", continuous_employment_asserted=False,
                            as_of="2026-07-17", current_evidence=False, report_period="2026 Q2",
                            firm_id="organization:11322") for p in ("gordon", "stephanie", "greg")]
        router = self.make(episodes + [episode("gordon", "old-firm"), episode("stephanie", "old-firm")])
        route = router.route("gordon", "stephanie")
        self.assertEqual(route["cost"], 2)
        hop = route["hops"][0]
        self.assertEqual(hop["relationship"], "co_reported_lobbying_activity")
        self.assertFalse(hop["current_evidence"])
        self.assertFalse(hop["continuous_employment_asserted"])
        self.assertIn("familiarity not established", hop["confidence_scope"])
        self.assertEqual(hop["report_period"]["start"], "2026-04-01")
        self.assertEqual(len(router.reported_collaborators("gordon")["collaborators"]), 2)
        self.assertEqual(len(router.coworkers("gordon")["coworkers"]), 1)
        self.assertEqual(router.coworkers("greg")["coworkers"], [])

    def test_reported_activity_requires_same_exact_activity_not_same_firm(self):
        router = self.make([episode("gordon", "activity:abc", relationship="co_reported_lobbying_activity", firm_id="same-firm"),
                            episode("stephanie", "activity:xyz", relationship="co_reported_lobbying_activity", firm_id="same-firm")])
        self.assertEqual(router.route("gordon", "stephanie")["status"], "unreachable")
        self.assertEqual(router.reported_collaborators("gordon")["collaborators"], [])

    def test_reported_activity_prefers_latest_period_at_equal_cost(self):
        episodes = [episode(p, "activity:" + year, year + "-01-01", year + "-04-01", relationship="co_reported_lobbying_activity")
                    for year in ("2024", "2026") for p in ("gordon", "stephanie")]
        router = self.make(episodes)
        self.assertEqual(router.route("gordon", "stephanie")["hops"][0]["start"], "2026-01-01")

    def test_direct_edge_is_found_in_both_directions_behind_a_large_activity_hub(self):
        """A direct cost-3 edge must never hide behind the scan of cheaper activity neighbours."""
        others = ["lobbyist%02d" % index for index in range(25)]
        nodes = {name: {"id": name, "kind": "person", "label": name.title()} for name in ("gordon", "adrienne", *others)}
        hub = [episode(p, "activity:hub", "2026-04-01", "2026-07-01", relationship="co_reported_lobbying_activity") for p in ("gordon", *others)]
        office = [episode(p, "office", "2014-01-01", "2015-01-01") for p in ("gordon", "adrienne")]
        router = AdvocacyRouter(nodes, hub + office, "r1", "2026-09-04")
        for origin, target in (("gordon", "adrienne"), ("adrienne", "gordon")):
            route = router.route(origin, target, {"max_expansions": 2})
            self.assertEqual((origin, route["status"], route["cost"], route["node_ids"], route["expanded"]),
                             (origin, "found", 3, [origin, target], 1))
        # The early exit is a lower bound on the remaining path, not a heuristic:
        # a cheaper hop class keeps the frontier settled and a cheaper chain wins.
        bridge = [current("lobbyist00", "bridge"), current("adrienne", "bridge")]
        router = AdvocacyRouter(nodes, hub + office + bridge, "r1", "2026-09-04")
        self.assertEqual(router.route("gordon", "adrienne")["node_ids"], ["gordon", "adrienne"])
        cheaper = router.route("gordon", "adrienne", {"current_cost": .5})
        self.assertEqual((cheaper["cost"], cheaper["node_ids"]), (2.5, ["gordon", "lobbyist00", "adrienne"]))

    def test_prefer_current_reports_whether_it_changed_any_cost(self):
        """On evidence without current hops the option is an explicit no-op, never a silent one."""
        historical = self.make([episode("gordon", "firm"), episode("stephanie", "firm")])
        for flag in (True, False):
            route = historical.route("gordon", "stephanie", {"prefer_current": flag})
            self.assertEqual((flag, route["cost"], route["prefer_current"]), (flag, 3, "no_effect"))
        self.assertEqual(historical.coworkers("gordon")["prefer_current"], "no_effect")
        self.assertEqual(historical.route("nobody", "stephanie")["prefer_current"], "no_effect")
        dated = self.make([current("gordon", "firm"), current("stephanie", "firm")])
        preferred = dated.route("gordon", "stephanie")
        self.assertEqual((preferred["cost"], preferred["prefer_current"]), (1, "applied"))
        declined = dated.route("gordon", "stephanie", {"prefer_current": False})
        self.assertEqual((declined["cost"], declined["prefer_current"]), (3, "off"))
        self.assertEqual(dated.coworkers("gordon", {"prefer_current": False})["prefer_current"], "off")

    def test_route_completes_past_legacy_budgets_and_reports_wall_time(self):
        """Routes finish after legacy time/work cutoffs; coworker pages stay bounded."""
        clock = [0.0]
        class SlowProvider:
            revision = "slow"

            def neighbors(self, person, options, budget):
                clock[0] += .05
                return {}
        nodes = {name: {"id": name, "kind": "person", "label": name.title()} for name in ("gordon", "stephanie")}
        router = AdvocacyRouter(nodes, [episode("gordon", "firm"), episode("stephanie", "firm")], "r1", "2026-09-04",
                                activity_provider=SlowProvider(), clock=lambda: clock[0])
        route = router.route("gordon", "stephanie", {"timeout_ms": 20})
        self.assertEqual((route["status"], route["timeout_ms"]), ("found", None))
        self.assertGreaterEqual(route["search_elapsed_ms"], 40)
        self.assertTrue(route["optimal"])
        limited = router.route("gordon", "stephanie", {"max_neighbor_checks": 1})
        self.assertEqual((limited["status"], limited["timeout_ms"]), ("found", None))
        self.assertIn("search_elapsed_ms", limited)
        self.assertIsNone(router.route("gordon", "stephanie", {"timeout_ms": 99999})["timeout_ms"])
        cached = router.route("gordon", "stephanie", {"timeout_ms": 99999})
        self.assertEqual(cached['search_elapsed_ms'], 50)
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(cached['cache_elapsed_ms'], 0)
        self.assertEqual(router.coworkers('missing')['prefer_current'], 'no_effect')
        self.assertIn('prefer_current', router.coworkers('gordon', {'max_neighbor_checks': 1}))


if __name__ == "__main__":
    unittest.main()
