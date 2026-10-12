"""Accounting, coverage and forecasting invariants for optional analysis panels."""
import copy
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from analysis_metrics import analyze


def fact(amount=100, *, company="A", committee="C1", cycle=2024,
         when=None, channel="employee", **extra):
    return dict(source_id=f"{company}-{committee}-{cycle}-{amount}", company=company,
                committee_id=committee, recipient="Recipient " + committee,
                cycle=cycle, date=when or f"{cycle}-01-15", funding_source=channel,
                amount_cents=amount, industry="Example", **extra)


def panel(rows, name, context=None, **request):
    return analyze(rows, {"panels": [name], **request}, context)["panels"][name]


class AccountingTests(unittest.TestCase):
    def test_streaming_compaction_preserves_record_counts_signs_and_donor_breadth(self):
        rows = (fact(100 if index % 2 else -50, contributor_key=str(index % 3)) for index in range(10000))
        result = analyze(rows)
        self.assertEqual(result["metadata"]["compact_groups"], 2)
        self.assertEqual(result["metadata"]["selected_records"], 10000)
        favorite = result["panels"]["favorites"]["rows"][0]
        self.assertEqual(favorite["amount_cents"], 250000)
        self.assertEqual(favorite["gross_cents"], 500000)
        self.assertEqual(favorite["records"], 10000)
        self.assertEqual(favorite["contributors"], 3)

    def test_memory_limit_returns_no_partial_monetary_results(self):
        with patch("analysis_metrics.MAX_COMPACT_GROUPS", 2):
            result = analyze(fact(100, committee=f"C{index}") for index in range(10))
        self.assertFalse(result["metadata"]["complete_input_scan"])
        self.assertEqual(result["metadata"]["totals_by_channel"], [])
        self.assertTrue(all(value["status"] == "unavailable" and not value["rows"] for value in result["panels"].values()))

    def test_donor_memory_limit_withholds_counts_but_keeps_exact_money(self):
        with patch("analysis_metrics.MAX_DONOR_IDENTITIES", 2):
            result = analyze(fact(100, contributor_key=str(index)) for index in range(10))
        self.assertTrue(result["metadata"]["complete_input_scan"])
        self.assertFalse(result["metadata"]["donor_breadth_available"])
        favorite = result["panels"]["favorites"]["rows"][0]
        self.assertIsNone(favorite["contributors"])
        self.assertEqual(favorite["amount_cents"], 1000)

    def test_signed_amounts_and_channel_separation(self):
        rows = [fact(100), fact(-25), fact(900, channel="connected_pac")]
        result = analyze(rows)
        totals = {row["funding_source"]: row for row in result["metadata"]["totals_by_channel"]}
        self.assertEqual(totals["employee"]["amount_cents"], 75)
        self.assertEqual(totals["employee"]["gross_cents"], 100)
        self.assertEqual(totals["employee"]["adjustment_cents"], -25)
        self.assertEqual(totals["connected_pac"]["amount_cents"], 900)
        self.assertEqual(len(result["panels"]["favorites"]["rows"]), 2)

    def test_large_cents_are_exact(self):
        value = 2**54 + 7
        result = panel([fact(value), fact(-2)], "favorites")
        self.assertEqual(result["rows"][0]["amount_cents"], value - 2)

    def test_input_rows_not_deduplicated_without_accounting_authority(self):
        row = fact(123)
        result = panel([row, row], "favorites")
        self.assertEqual(result["rows"][0]["amount_cents"], 246)
        self.assertEqual(result["rows"][0]["records"], 2)

    def test_invalid_money_excluded_explicitly(self):
        result = analyze([fact(20), fact(1.2), fact(True), fact("20")])
        self.assertEqual(result["metadata"]["quality"]["invalid_amount_rows"], 3)
        self.assertEqual(result["metadata"]["totals_by_channel"][0]["amount_cents"], 20)
        self.assertTrue(any("Excluded 3" in item for item in result["metadata"]["notices"]))

    def test_unknowns_do_not_create_company_or_donor_breadth(self):
        result = panel([fact(100, company="", committee="", contributor_key="")], "favorites")
        self.assertEqual(result["rows"][0]["companies"], 0)
        self.assertEqual(result["rows"][0]["contributors"], 0)
        self.assertIsNone(result["rows"][0]["relative_preference"])

    def test_unattributed_company_not_treated_as_continuing_relationship(self):
        rows = [fact(100, company="", cycle=2022), fact(200, company="")]
        result = panel(rows, "changes")
        values = {row["component"]: row["change_cents"] for row in result["rows"]}
        self.assertEqual(values["Unattributed company observations"], 100)
        self.assertEqual(values["Continuing observed companies"], 0)
        self.assertEqual(panel(rows, "relationships")["rows"], [])

    def test_name_only_recipient_drill_is_restricted_to_exact_unresolved_name(self):
        result = panel([fact(100, committee="")], "favorites")["rows"][0]
        self.assertIn({"field": "committee_id", "op": "eq", "value": ""}, result["filters"])
        self.assertIn({"field": "recipient", "op": "eq", "value": "Recipient "}, result["filters"])

    def test_empty_scope_has_all_panels_unavailable(self):
        result = analyze([])
        self.assertEqual(len(result["panels"]), 8)
        self.assertTrue(all(value["status"] == "unavailable" for value in result["panels"].values()))

    def test_raw_basis_never_implies_giving(self):
        result = analyze([fact()], {"basis": "raw"})
        for value in result["panels"].values():
            self.assertTrue(any("not a measure of giving" in notice for notice in value["notices"]))

    def test_order_invariant_and_inputs_unchanged(self):
        rows = [fact(100, company="B"), fact(200, company="A", cycle=2022), fact(-30, company="B", cycle=2022)]
        original = copy.deepcopy(rows)
        forward = analyze(rows, {"as_of": "2026-09-20"})
        backward = analyze(list(reversed(rows)), {"as_of": "2026-09-20"})
        self.assertEqual(forward, backward)
        self.assertEqual(rows, original)
        json.dumps(forward, allow_nan=False)

    def test_scalar_and_array_filters_precede_totals(self):
        rows = [fact(100), fact(200, company="B"), fact(300, channel="pac"), fact(400, cycle=2022)]
        result = analyze(rows, {"company": "A", "funding_sources": ["employee"], "cycles": ["2023-2024"]})
        self.assertEqual(result["metadata"]["selected_records"], 1)
        self.assertEqual(result["metadata"]["totals_by_channel"][0]["amount_cents"], 100)

    def test_top_n_never_limits_denominators(self):
        rows = [fact(100, committee=f"C{index}") for index in range(7)]
        result = panel(rows, "favorites", top_n=2)
        self.assertEqual(result["total_rows"], 7)
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(result["truncated"])
        self.assertAlmostEqual(result["rows"][0]["positive_share"], 1 / 7)

    def test_top_n_bounded_and_unknown_panel_rejected(self):
        result = analyze([], {"top_n": 10000, "panels": []})
        self.assertEqual(result["metadata"]["top_n"], 100)
        self.assertEqual(result["panels"], {})
        with self.assertRaisesRegex(ValueError, "Unknown analysis"):
            analyze([], {"panels": ["anything"]})

    def test_non_finite_top_n_falls_back_to_the_default(self):
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value):
                self.assertEqual(analyze([], {"top_n": value, "panels": []})["metadata"]["top_n"], 25)


class FavoritesAndPortfolioTests(unittest.TestCase):
    def test_relative_preference_requires_real_positive_reference(self):
        rows = [fact(800), fact(9200, committee="C2")]
        context = {"industry_reference": {"label": "Other scope", "by_channel": {
            "employee": {"total_positive_cents": 10000, "recipient_positive_cents": {"C1": 200}}}}}
        values = {row["committee_id"]: row for row in panel(rows, "favorites", context)["rows"]}
        self.assertEqual(values["C1"]["relative_preference"], 4)
        self.assertIsNone(values["C2"]["relative_preference"])

    def test_company_breadth_is_distinct_and_positive_only(self):
        rows = [fact(100, contributor_key="person"), fact(200, contributor_key="person"),
                fact(50, company="B", cycle=2022), fact(-70, company="C")]
        result = panel(rows, "favorites")["rows"][0]
        self.assertEqual(result["companies"], 2)
        self.assertEqual(result["contributors"], 1)
        self.assertEqual(result["observed_cycles"], 2)

    def test_concentration_is_positive_and_exclusion_exact(self):
        rows = [fact(300, company="A"), fact(-1000, company="A"), fact(100, company="B")]
        result = panel(rows, "concentration")
        card = next(row for row in result["cards"] if row["dimension"] == "Company")
        self.assertEqual(card["top_one_share"], .75)
        self.assertEqual(card["excluding_largest_cents"], 100)
        self.assertEqual(card["hhi"], .625)

    def test_negative_only_concentration_unavailable(self):
        result = panel([fact(-100)], "concentration")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["cards"][0]["hhi"])

    def test_overlap_uses_normalized_shares_and_resolved_ids(self):
        rows = [fact(50, company="A", committee="C1"), fact(50, company="A", committee="C2"),
                fact(500, company="B", committee="C1"), fact(500, company="B", committee="C2"),
                fact(1000, company="C", committee="C3"), fact(5000, company="Z", committee="")]
        result = panel(rows, "overlap")
        best = result["rows"][0]
        self.assertEqual((best["company"], best["other_company"], best["overlap"]), ("A", "B", 1))
        self.assertEqual(best["shared_recipients"], 2)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["rows"][-1]["overlap"], 0)


class TemporalAndChangeTests(unittest.TestCase):
    def test_monthly_compaction_preserves_day_cutoff_and_invalid_record_counts(self):
        rows = [fact(100, cycle=2022, when="2022-09-10"), fact(200, cycle=2022, when="2022-09-30"),
                fact(300, cycle=2022, when="2022-09-02"), fact(9, cycle=2022, when="invalid"),
                fact(8, cycle=2022, when="invalid")]
        result = analyze(rows, {"as_of": "2024-09-20"})
        trend = result["panels"]["trends"]["rows"][0]
        self.assertEqual(result["metadata"]["compact_groups"], 3)
        self.assertEqual(trend["amount_cents"], 617)
        self.assertEqual(trend["matched_amount_cents"], 400)
        self.assertEqual(trend["temporal_excluded_records"], 2)
        self.assertEqual(trend["records"], 5)

    def test_as_of_excludes_future_and_retains_undated_in_full_amounts(self):
        rows = [fact(100), fact(200, when="2024-10-01"), fact(300, when="invalid")]
        result = analyze(rows, {"as_of": "2024-09-20"})
        self.assertEqual(result["metadata"]["after_as_of_records"], 1)
        trend = result["panels"]["trends"]["rows"][0]
        self.assertEqual(trend["amount_cents"], 400)
        self.assertEqual(trend["matched_amount_cents"], 100)
        self.assertEqual(trend["temporal_excluded_records"], 1)

    def test_cycle_date_conflicts_and_bad_cycle_cannot_crash_or_enter_timing(self):
        result = analyze([fact(100, when="2022-01-01"), fact(200, cycle="invalid", when="2024-01-01")])
        self.assertEqual(result["metadata"]["temporal_excluded_records"], 2)
        self.assertEqual(result["panels"]["timing"]["status"], "unavailable")
        self.assertEqual(result["metadata"]["totals_by_channel"][0]["amount_cents"], 300)

    def test_matching_cutoff_handles_phase_and_leap_day(self):
        rows = [fact(100, cycle=2022, when="2022-02-28"), fact(200, cycle=2022, when="2022-03-01"),
                fact(500, cycle=2024, when="2024-02-29")]
        values = {row["cycle_end"]: row for row in panel(rows, "trends", as_of="2024-02-29")["rows"]}
        self.assertEqual(values[2022]["matched_through"], "2022-02-28")
        self.assertEqual(values[2022]["matched_amount_cents"], 100)
        self.assertEqual(values[2022]["amount_cents"], 300)

    def test_odd_year_cycle_stage_uses_first_cycle_year(self):
        rows = [fact(100, cycle=2024, when="2023-06-01"), fact(200, cycle=2024, when="2024-06-01")]
        result = panel(rows, "trends", as_of="2025-09-20")["rows"][0]
        self.assertEqual(result["matched_through"], "2023-09-20")
        self.assertEqual(result["matched_amount_cents"], 100)

    def test_bad_as_of_raises(self):
        with self.assertRaisesRegex(ValueError, "as_of"):
            analyze([fact()], {"as_of": "2024-02-31"})

    def test_changes_exact_decomposition_and_coverage_unknown(self):
        rows = [fact(100, company="A", cycle=2022), fact(150, company="A"),
                fact(60, company="B", cycle=2022), fact(80, company="C"), fact(-10, company="A")]
        result = panel(rows, "changes", as_of="2024-09-20")
        changes = {row["component"]: row["change_cents"] for row in result["rows"]}
        self.assertEqual(changes, {"Continuing observed companies": 40, "Newly observed companies": 80,
                                   "No longer observed companies": -60})
        self.assertEqual(result["cards"][0]["change_cents"], 60)
        self.assertTrue(result["cards"][0]["reconciled"])
        self.assertIsNone(result["cards"][0]["explicitly_covered_baseline_cents"])
        self.assertTrue(all(not row["coverage_comparable"] for row in result["company_changes"]))

    def test_changes_match_cutoff_and_preserve_full_totals(self):
        rows = [fact(100, cycle=2022), fact(900, cycle=2022, when="2022-12-01"), fact(200)]
        result = panel(rows, "changes", as_of="2024-09-20")
        card = result["cards"][0]
        self.assertEqual(card["baseline_cents"], 100)
        self.assertEqual(card["full_observed_baseline_cents"], 1000)
        self.assertEqual(card["change_cents"], 100)
        row = result["company_changes"][0]
        self.assertIn({"field": "date", "op": "lte", "value": "2022-09-20"}, row["comparison_scopes"]["baseline"])
        self.assertIn({"field": "date", "op": "lte", "value": "2024-09-20"}, row["filters"])

    def test_changes_conserve_random_signed_inputs_in_each_channel(self):
        rng = random.Random(7)
        rows = [fact(rng.randint(-500, 500), company=f"C{rng.randint(0, 9)}", cycle=rng.choice([2022, 2024]),
                     channel=rng.choice(["employee", "pac"])) for _ in range(250)]
        result = panel(rows, "changes")
        for card in result["cards"]:
            channel = card["funding_source"]
            expected = sum(row["amount_cents"] * (1 if row["cycle"] == 2024 else -1) for row in rows if row["funding_source"] == channel)
            self.assertEqual(card["component_sum_cents"], expected)

    def test_explicit_coverage_is_separate_from_observed_membership(self):
        rows = [fact(100, cycle=2022), fact(200)]
        context = {"company_coverage_by_cycle": {"2021-2022": ["A"], "2023-2024": {"A": {"complete": True}}}}
        result = panel(rows, "changes", context)
        self.assertEqual(result["cards"][0]["explicitly_covered_baseline_cents"], 100)
        self.assertTrue(result["company_changes"][0]["coverage_comparable"])

    def test_changes_recipient_decomposition_is_exact_and_counts_recipients(self):
        # One company throughout: the company table is all "continuing", while
        # recipients enter and leave. Board I1's waterfall and counts use recipients.
        unattributed = fact(7, committee="")
        unattributed["recipient"] = ""
        rows = [fact(100, committee="C1", cycle=2022), fact(150, committee="C1"), fact(-10, committee="C1"),
                fact(60, committee="C2", cycle=2022), fact(80, committee="C3"), unattributed]
        result = panel(rows, "changes", as_of="2024-09-20")
        self.assertEqual({row["component"]: row["change_cents"] for row in result["rows"]},
                         {"Continuing observed companies": 67, "Newly observed companies": 0, "No longer observed companies": 0})
        parts = {row["component"]: row for row in result["recipient_components"]}
        self.assertEqual({key: row["change_cents"] for key, row in parts.items()},
                         {"Continuing observed recipients": 40, "Newly observed recipients": 80,
                          "No longer observed recipients": -60, "Unattributed recipient observations": 7})
        self.assertEqual([parts[key]["recipients"] for key in ("Continuing observed recipients", "Newly observed recipients",
                                                                 "No longer observed recipients", "Unattributed recipient observations")], [1, 1, 1, None])
        card = result["cards"][0]
        self.assertEqual((card["continuing_recipients"], card["newly_observed_recipients"], card["no_longer_observed_recipients"]), (1, 1, 1))
        self.assertEqual(card["recipient_component_sum_cents"], card["change_cents"])
        self.assertEqual(card["change_cents"], 67)
        self.assertTrue(card["recipients_reconciled"])
        self.assertIn({"field": "committee_id", "op": "in", "value": ["C3"]}, parts["Newly observed recipients"]["comparison_scopes"]["current"])
        self.assertIn({"field": "recipient", "op": "in", "value": ["", "Unknown"]}, parts["Unattributed recipient observations"]["filters"])

    def test_changes_recipient_scope_is_withheld_rather_than_broadened(self):
        rows = [fact(100, committee="C1", cycle=2022), fact(80, committee="C3"), fact(9, committee="")]
        result = panel(rows, "changes", as_of="2024-09-20")
        newly = next(row for row in result["recipient_components"] if row["component"] == "Newly observed recipients")
        self.assertEqual((newly["recipients"], newly["change_cents"], newly["filters"], newly["comparison_scopes"]), (2, 89, None, None))
        self.assertTrue(any("newly observed recipients cannot open as one evidence scope" in notice for notice in result["notices"]))

    def test_changes_recipient_decomposition_conserves_random_signed_inputs(self):
        rng = random.Random(11)
        rows = [fact(rng.randint(-500, 500), company=f"K{rng.randint(0, 4)}", committee=f"C{rng.randint(0, 9)}",
                     cycle=rng.choice([2022, 2024]), channel=rng.choice(["employee", "pac"])) for _ in range(300)]
        result = panel(rows, "changes")
        for card in result["cards"]:
            channel = card["funding_source"]
            parts = [row for row in result["recipient_components"] if row["funding_source"] == channel]
            self.assertEqual(sum(row["change_cents"] for row in parts), card["change_cents"])
            self.assertEqual(card["continuing_recipients"] + card["newly_observed_recipients"] + card["no_longer_observed_recipients"],
                             len({row["committee_id"] for row in rows if row["funding_source"] == channel}))

    def test_favorites_carry_same_stage_amounts_and_current_period_leaders(self):
        rows = [fact(100, committee="C1", cycle=2022), fact(900, committee="C1", cycle=2022, when="2022-12-01"),
                fact(200, committee="C1"), fact(50, committee="C2", cycle=2022)]
        result = panel(rows, "favorites", as_of="2024-09-20")
        values = {row["committee_id"]: row for row in result["rows"]}
        self.assertEqual(values["C1"]["amount_cents"], 1200, "The favorite net amount still spans every observed cycle")
        self.assertEqual((values["C1"]["baseline_cents"], values["C1"]["current_cents"], values["C1"]["change_cents"]), (100, 200, 100))
        self.assertEqual((values["C2"]["baseline_cents"], values["C2"]["current_cents"], values["C2"]["change_cents"]), (50, 0, -50))
        self.assertEqual([(row["committee_id"], row["current_cents"], row["change_cents"]) for row in result["current_period_leaders"]], [("C1", 200, 100)])
        leader = result["current_period_leaders"][0]
        self.assertIn({"field": "date", "op": "lte", "value": "2022-09-20"}, leader["comparison_scopes"]["baseline"])
        self.assertIn({"field": "committee_id", "op": "eq", "value": "C1"}, leader["filters"])

    def test_current_period_leaders_rank_every_recipient_not_the_bounded_table(self):
        rows = [fact(10_000 - index, committee=f"H{index:02d}", cycle=2022) for index in range(30)]
        rows += [fact(index + 1, committee=f"N{index:02d}") for index in range(12)]
        result = panel(rows, "favorites", as_of="2024-09-20", top_n=5)
        self.assertEqual(len(result["rows"]), 5)
        self.assertEqual([row["committee_id"] for row in result["current_period_leaders"]], [f"N{index:02d}" for index in range(11, 1, -1)])

    def test_favorites_without_a_pair_carry_no_period_values(self):
        result = panel([fact(100)], "favorites", as_of="2024-09-20")
        self.assertEqual((result["rows"][0]["baseline_cents"], result["rows"][0]["change_cents"]), (None, None))
        self.assertEqual(result["current_period_leaders"], [])

    def test_retention_is_observed_overlap_not_proven_departure(self):
        rows = [fact(100, cycle=2022), fact(100), fact(100, cycle=2022, committee="C2")]
        result = panel(rows, "relationships")
        self.assertEqual(result["cards"][0]["observed_retention"], .5)
        missing = next(row for row in result["rows"] if row["committee_id"] == "C2")
        self.assertEqual(missing["state"], "No positive activity observed in current cycle")
        self.assertEqual(missing["coverage"], "Coverage comparability unverified")

    def test_timing_comparison_needs_two_prior_matching_election_phases(self):
        rows = [fact(100, cycle=2016), fact(300, cycle=2020), fact(400, cycle=2024)]
        result = panel(rows, "timing")["rows"]
        self.assertEqual(result[0]["relative_to_prior_months"], 2)
        self.assertIsNone(result[1]["relative_to_prior_months"])
        self.assertEqual(result[0]["phase"], "Presidential-cycle year")


class ForecastTests(unittest.TestCase):
    def history(self, cycles=(2018, 2020, 2022, 2024)):
        return [fact(cycle * 100, cycle=cycle) for cycle in cycles]

    def test_closed_calendar_cycles_do_not_invent_completeness(self):
        result = panel(self.history(), "forecast", as_of="2026-09-20")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("explicit", " ".join(result["notices"]))

    def test_four_explicit_complete_cycles_enable_unvalidated_baseline(self):
        result = panel(self.history(), "forecast", {"complete_cycles": [2018, 2020, 2022, 2024]}, as_of="2026-09-20")
        self.assertEqual(result["status"], "ready")
        cycle_rows = [row for row in result["rows"] if row["product"] == "Cycle baseline"]
        self.assertEqual(cycle_rows[0]["period"], "2025-2026")
        self.assertEqual(cycle_rows[0]["amount_cents"], 202200)
        self.assertEqual(cycle_rows[1]["amount_cents"], 202400)
        self.assertEqual(result["diagnostics"][0]["backtest_count"], 0)
        self.assertIsNone(result["diagnostics"][0]["mae_cents"])
        self.assertFalse(result["diagnostics"][0]["interval_available"])

    def test_nonconsecutive_or_bad_date_history_is_withheld(self):
        for cycles in ((2016, 2020, 2022, 2024), (2018, 2020, 2022)):
            with self.subTest(cycles=cycles):
                result = panel(self.history(cycles), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
                self.assertEqual(result["status"], "unavailable")
        rows = self.history() + [fact(100, cycle=2022, when="invalid")]
        self.assertEqual(panel(rows, "forecast", {"complete_cycles": [2018, 2020, 2022, 2024]}, as_of="2026-09-20")["status"], "unavailable")

    def test_current_cycle_never_trains_even_if_flagged_complete(self):
        cycles = [2020, 2022, 2024, 2026]
        result = panel(self.history(cycles), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual(result["status"], "unavailable")

    def test_rolling_backtest_uses_only_earlier_same_phase(self):
        cycles = [2014, 2016, 2018, 2020, 2022, 2024]
        result = panel(self.history(cycles), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        diagnostic = result["diagnostics"][0]
        self.assertEqual(diagnostic["backtest_count"], 2)
        self.assertEqual(diagnostic["mae_cents"], 400)
        self.assertEqual(diagnostic["backtests"][0]["forecast_basis_cycle"], "2017-2018")
        self.assertEqual(diagnostic["backtests"][0]["cycle"], "2021-2022")

    def test_pace_projection_requires_current_through_evidence(self):
        cycles = [2018, 2020, 2022, 2024]
        rows = []
        for cycle in cycles:
            rows += [fact(100, cycle=cycle), fact(100, cycle=cycle, when=f"{cycle}-12-01")]
        rows += [fact(100, cycle=2026)]
        context = {"complete_cycles": cycles}
        without = panel(rows, "forecast", context, as_of="2026-09-20")
        self.assertFalse(any(row["product"] == "Current-cycle pace projection" for row in without["rows"]))
        with_evidence = panel(rows, "forecast", {**context, "verified_data_through": "2026-09-20"}, as_of="2026-09-20")
        pace = next(row for row in with_evidence["rows"] if row["product"] == "Current-cycle pace projection")
        self.assertEqual(pace["amount_cents"], 200)
        self.assertEqual(pace["historical_completion_share"], .5)

    def test_missing_quarter_not_fabricated_as_zero(self):
        cycles = [2018, 2020, 2022, 2024]
        result = panel(self.history(cycles), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertFalse(any(row["product"] == "Next-quarter baseline" for row in result["rows"]))
        rows = self.history(cycles) + [fact(321, cycle=2022, when="2022-11-01")]
        result = panel(rows, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        quarter = next(row for row in result["rows"] if row["product"] == "Next-quarter baseline")
        self.assertEqual(quarter["period"], "2026-Q4")
        self.assertEqual(quarter["amount_cents"], 321)

    def test_missing_completeness_record_is_named_in_the_withheld_reason(self):
        for context in (None, {}, {"complete_cycles": []}, {"complete_cycles": None}):
            with self.subTest(context=context):
                result = panel(self.history(), "forecast", context, as_of="2026-09-20")
                self.assertEqual(result["status"], "unavailable")
                diagnostic = result["diagnostics"][0]
                self.assertEqual(diagnostic["status"], "unavailable")
                self.assertEqual(diagnostic["reason"], "No completeness record is connected, so the forecast is withheld.")
                self.assertEqual(len(result["diagnostics"]), 1)

    def test_insufficient_completeness_evidence_keeps_the_history_reason(self):
        cycles = (2018, 2020, 2022)
        result = panel(self.history(cycles), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["diagnostics"][0]["reason"],
                         "Need four consecutive explicitly complete cycles with valid event dates.")

    def test_complete_cycles_read_is_unchanged_for_array_like_and_falsy_iterables(self):
        import analysis_metrics

        class FalsyCycles(list):
            def __bool__(self):
                return False

        builders = [FalsyCycles]
        try:
            import numpy
            builders.append(numpy.array)
        except ImportError:
            pass
        for build in builders:
            with self.subTest(container=build.__name__):
                values = build([2022, 2024])
                original = {analysis_metrics._cycle(value) for value in values} - {None}
                self.assertEqual(analysis_metrics._complete_cycles({"complete_cycles": values}), original)
                self.assertEqual(original, {2022, 2024})
                cycles = build([2018, 2020, 2022, 2024])
                self.assertEqual(panel(self.history(), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")["status"], "ready")

    def excluded(self, result):
        return {label: row["reason"] for row in result["diagnostics"] if row["status"] == "excluded"
                for label in row["excluded_cycles"]}

    def test_cycles_dropped_from_history_are_disclosed_with_their_reason(self):
        cycles = [2010, 2014, 2016, 2018, 2020, 2022, 2024]
        result = panel(self.history(cycles), "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual(result["diagnostics"][0]["training_cycles"],
                         ["2013-2014", "2015-2016", "2017-2018", "2019-2020", "2021-2022", "2023-2024"])
        self.assertEqual(result["diagnostics"][1:],
                         [dict(funding_source="employee", status="excluded", excluded_cycles=["2009-2010"],
                               reason="Older than a break in consecutive eligible cycles.")])

    def test_every_observed_cycle_either_trains_or_is_excluded_once_with_its_rule(self):
        cycles = [2016, 2018, 2020, 2022, 2024, 2026]
        rows = self.history([2014, 2016, 2018, 2020, 2022, 2024, 2026]) + [fact(100, cycle=2016, when="invalid"), fact(100, cycle="bad")]
        result = panel(rows, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        diagnostic = result["diagnostics"][0]
        self.assertEqual(diagnostic["training_cycles"], ["2017-2018", "2019-2020", "2021-2022", "2023-2024"])
        self.assertEqual(self.excluded(result), {
            "Unknown": "No valid cycle on the selected records.",
            "2013-2014": "Not listed as explicitly complete.",
            "2015-2016": "Records include a missing, invalid or cycle-inconsistent event date.",
            "2025-2026": "Cycle has not closed by the as-of date."})
        observed = ["Unknown"] + [f"{cycle - 1}-{cycle}" for cycle in (2014, 2016, 2018, 2020, 2022, 2024, 2026)]
        listed = diagnostic["training_cycles"] + [label for row in result["diagnostics"] if row["status"] == "excluded"
                                                  for label in row["excluded_cycles"]]
        self.assertEqual(sorted(listed), sorted(observed))

    def test_disclosure_rows_follow_every_channel_diagnostic(self):
        cycles = [2018, 2020, 2022, 2024]
        rows = self.history() + [fact(100, cycle=cycle, channel="pac") for cycle in (2016, 2022, 2024)]
        result = panel(rows, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual([(row["funding_source"], row["status"]) for row in result["diagnostics"]],
                         [("employee", "ready"), ("pac", "unavailable"), ("pac", "excluded")])
        self.assertEqual(result["diagnostics"][2]["excluded_cycles"], ["2015-2016"])

    def test_exclusions_without_as_of_are_named(self):
        result = panel(self.history(), "forecast", {"complete_cycles": [2018, 2020, 2022, 2024]})
        self.assertEqual(set(self.excluded(result).values()), {"No valid as-of date is set to establish a closed cycle."})
        self.assertEqual(len(self.excluded(result)), 4)

    def test_production_context_without_a_completeness_record_emits_only_the_withheld_reason(self):
        rows = []
        for channel in ("employee_original", "corporate_original"):
            for cycle in range(2014, 2028, 2):
                rows += [fact(1000 + 10 * n, company=f"K{n}", committee=f"C{n}", cycle=cycle, channel=channel) for n in range(3)]
            rows += [fact(-50, cycle=2022, channel=channel), fact(100, cycle=2020, when="invalid", channel=channel),
                     fact(900000, cycle=2026, when="2026-03-01", channel=channel)]
        cycles = list(range(2014, 2028, 2))
        context = {"source_scope": {"dataset": "example", "industry": "Example", "basis": "receipt", "as_of": "2026-09-20"},
                   "provenance": {}, "cycles": cycles}
        result = panel(rows, "forecast", context, as_of="2026-09-20")
        self.assertEqual((result["status"], result["rows"]), ("unavailable", []))
        self.assertEqual(result["diagnostics"], [
            dict(funding_source=channel, status="unavailable", training_cycles=[], backtest_count=0,
                 reason="No completeness record is connected, so the forecast is withheld.")
            for channel in ("corporate_original", "employee_original")])

    def test_warning_rows_never_accompany_an_unavailable_channel(self):
        cycles = [2018, 2020, 2022, 2024]
        risky = [fact(-500000, cycle=2022, when="2022-03-01"), fact(900000, cycle=2026, when="2026-03-01")]
        rows = self.history() + risky + [fact(cycle * 100, cycle=cycle, channel="pac") for cycle in (2022, 2024)]
        rows += [fact(row["amount_cents"], cycle=row["cycle"], when=row["date"], channel="pac") for row in risky]
        result = panel(rows, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        by_status = {row["funding_source"]: row["status"] for row in result["diagnostics"] if row["status"] in ("ready", "unavailable")}
        self.assertEqual(by_status, {"employee": "ready", "pac": "unavailable"})
        self.assertEqual({row["funding_source"] for row in self.warnings(result)}, {"employee"})
        only_pac = [row for row in rows if row["funding_source"] == "pac"]
        result = panel(only_pac, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(self.warnings(result), [])

    def test_pace_projection_basis_lists_only_the_cycles_used(self):
        rows = []
        for cycle in [2014, 2016, 2018, 2020, 2022, 2024]:
            rows += [fact(100, cycle=cycle, when=f"{cycle}-02-01"), fact(100, cycle=cycle, when=f"{cycle}-12-01")]
        rows = [row for row in rows if not (row["cycle"] == 2014 and row["date"].endswith("02-01"))]
        rows.append(fact(100, cycle=2026, when="2026-02-01"))
        context = {"complete_cycles": [2014, 2016, 2018, 2020, 2022, 2024], "verified_data_through": "2026-09-20"}
        pace = next(row for row in panel(rows, "forecast", context, as_of="2026-09-20")["rows"]
                    if row["product"] == "Current-cycle pace projection")
        self.assertEqual(pace["forecast_basis_cycle"], "2017-2018, 2021-2022")
        self.assertEqual(pace["historical_completion_share"], .5)

    def test_non_finite_horizon_falls_back_to_the_default(self):
        context = {"complete_cycles": [2018, 2020, 2022, 2024]}
        expected = panel(self.history(), "forecast", context, as_of="2026-09-20")["rows"]
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(value=value):
                result = panel(self.history(), "forecast", context, as_of="2026-09-20", horizon=value)
                self.assertEqual(result["rows"], expected)
                self.assertEqual([row["period"] for row in result["rows"]], ["2025-2026", "2027-2028"])

    def warnings(self, result):
        return [row for row in result["diagnostics"] if row["status"] == "warning"]

    def test_current_cycle_activity_above_the_baseline_is_flagged_without_changing_it(self):
        cycles = [2018, 2020, 2022, 2024]
        rows = self.history() + [fact(900000, cycle=2026, when="2026-03-01")]
        result = panel(rows, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual(self.warnings(result), [dict(
            funding_source="employee", status="warning", product="Cycle baseline", period="2025-2026",
            baseline_cents=202200, observed_cents=900000,
            warning="Observed amount to date in the current cycle exceeds the baseline.")])
        self.assertEqual(next(row for row in result["rows"] if row["product"] == "Cycle baseline")["amount_cents"], 202200)
        self.assertEqual([column["key"] for column in result["columns"]],
                         ["product", "period", "funding_source", "amount_cents", "forecast_basis_cycle", "backtest_count"])
        for observed in (0, 100, 202200):
            with self.subTest(observed=observed):
                rows = self.history() + ([fact(observed, cycle=2026, when="2026-03-01")] if observed else [])
                self.assertEqual(self.warnings(panel(rows, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")), [])

    def test_zero_or_negative_basis_is_flagged_per_baseline_period(self):
        cycles = [2018, 2020, 2022, 2024]
        refunds = self.history() + [fact(-500000, cycle=2022, when="2022-03-01")]
        result = panel(refunds, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual(self.warnings(result), [dict(
            funding_source="employee", status="warning", product="Cycle baseline", period="2025-2026",
            forecast_basis_cycle="2021-2022", baseline_cents=-297800, warning="Historical basis amount is zero or negative.")])
        zero = self.history() + [fact(-202200, cycle=2022, when="2022-03-01")]
        self.assertEqual([row["period"] for row in self.warnings(panel(zero, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20"))],
                         ["2025-2026"])
        flat = [fact(0, cycle=cycle) for cycle in cycles]
        self.assertEqual([row["period"] for row in self.warnings(panel(flat, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20"))],
                         ["2025-2026", "2027-2028"])
        quarter = self.history() + [fact(-321, cycle=2022, when="2022-11-01")]
        result = panel(quarter, "forecast", {"complete_cycles": cycles}, as_of="2026-09-20")
        self.assertEqual([(row["product"], row["period"], row["baseline_cents"]) for row in self.warnings(result)],
                         [("Next-quarter baseline", "2026-Q4", -321)])


if __name__ == "__main__":
    unittest.main()
