"""Hand-checkable invariants for the shadow effectiveness policy."""
import copy
from datetime import date, timedelta
import json
import os
import sys
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)
from engine.legislative_effectiveness import STAGES, ScoringPolicy, compute_effectiveness


def iso(days):
    return (date(2023, 1, 3) + timedelta(days=days)).isoformat()


def bill(number=1, stages=None, congress=118, measure_type="hr", eligible=True):
    stages = stages if stages is not None else dict(zip(STAGES, (0, 20, 40, 200, 300)))
    return {"measure_id": f"{congress}:{measure_type}:{number}", "congress": congress,
            "chamber": "House", "introduced_date": iso(0), "measure_type": measure_type,
            "eligible": eligible, "milestones": {
                stage: {"date": iso(stages[stage]) if stage in stages else None,
                        "state": "observed", "reached": stage in stages, "evidence": [stage]}
                for stage in STAGES}}


def role(measure, member="A", kind="sponsor", joined=0, withdrawn=None):
    return {"measure_id": measure, "bioguide_id": member, "role": kind,
            "joined_date": iso(joined), "withdrawn_date": iso(withdrawn) if withdrawn is not None else None,
            "chronology_status": "verified", "evidence": ["official"]}


def signal(measure, standard=1, tensor=1, category="CU Tax Status", member=None, **extra):
    return {"measure_id": measure, "category": category, "standard_direction": standard,
            "tensor_cu": tensor, "bioguide_id": member, **extra}


def population(*ids, congress=118):
    return [{"bioguide_id": member, "congress": congress, "chamber": "House", "name": member}
            for member in ids]


def run(bills, roles=None, signals=None, roster=None, weights=None, **kwargs):
    return compute_effectiveness(
        bills, roles if roles is not None else [role(b["measure_id"]) for b in bills],
        [{"measure_id": b["measure_id"], "weight": (weights or {}).get(b["measure_id"], 5)} for b in bills],
        roster or population("A", "B"), signals if signals is not None else [signal(b["measure_id"]) for b in bills],
        as_of=kwargs.pop("as_of", "2024-12-31"), **kwargs)


def member(artifact, identity="A", congress=118):
    return next(row for row in artifact["members"] if row["bioguide_id"] == identity and row["congress"] == congress)


class LegislativeEffectivenessTests(unittest.TestCase):
    def test_paper_example_172(self):
        bills = []
        for number in range(1000):
            stages = {"BILL": 0}
            for stage, limit in (("AIC", 200), ("ABC", 100), ("PASS", 50), ("LAW", 20)):
                if number < limit:
                    stages[stage] = 100
            bills.append(bill(number + 1, stages))
        roles = [role(b["measure_id"], "A" if n == 0 else "B") for n, b in enumerate(bills)]
        result = run(bills, roles, roster=population("A", "B", *(f"P{i}" for i in range(98))),
                     include_explanations=False)
        self.assertEqual(result["terms"]["118:House"]["denominators"],
                         dict(zip(STAGES, (5000., 1000., 500., 250., 100.))))
        self.assertAlmostEqual(member(result)["sponsor_reference"]["total"], 1.72)
        self.assertAlmostEqual(result["terms"]["118:House"]["sponsor_observed_mean"], 1.)

    def test_complete_chamber_mean_one_and_role_ratios(self):
        a, b = bill(1), bill(2)
        result = run([a, b], [role(a["measure_id"]), role(b["measure_id"], "B")])
        self.assertAlmostEqual(member(result)["sponsor_reference"]["total"], 1.)
        self.assertAlmostEqual(member(result)["standard"]["results"]["net"], 3.)
        self.assertAlmostEqual(member(result)["tensor"]["results"]["net"], 1.)
        self.assertEqual(member(result)["tensor"]["results"]["distinct_measures"], 1)
        json.dumps(result, allow_nan=False)

    def test_full_chamber_off_topic_and_unsigned_denominators(self):
        a, b = bill(1), bill(2)
        alone = run([a])
        together = run([a, b], [role(a["measure_id"]), role(b["measure_id"], "B")],
                       [signal(a["measure_id"], -1, -.8)])
        self.assertEqual(together["terms"]["118:House"]["denominators"], dict.fromkeys(STAGES, 10.))
        self.assertAlmostEqual(member(together)["sponsor_reference"]["total"],
                               member(alone)["sponsor_reference"]["total"] / 2)
        self.assertAlmostEqual(member(together)["standard"]["results"]["negative"], 3.)
        self.assertAlmostEqual(member(together)["standard"]["results"]["net"], -3.)
        self.assertIsNone(member(together, "B")["standard"]["results"]["net"])

    def test_impact_changes_numerator_and_all_denominators(self):
        a, b = bill(1), bill(2)
        result = run([a, b], [role(a["measure_id"]), role(b["measure_id"], "B")],
                     weights={a["measure_id"]: 10})
        self.assertEqual(result["terms"]["118:House"]["denominators"], dict.fromkeys(STAGES, 15.))
        self.assertAlmostEqual(member(result)["sponsor_reference"]["total"], 4/3)
        self.assertAlmostEqual(member(result, "B")["sponsor_reference"]["total"], 2/3)

    def test_zero_stage_and_unknown_history_not_fabricated_zero(self):
        a = bill(stages={"BILL": 0})
        result = run([a])
        self.assertIsNone(member(result)["sponsor_reference"]["total"])
        self.assertAlmostEqual(member(result)["sponsor_reference"]["observed_total"], .4)
        self.assertIsNone(member(result)["standard"]["results"]["net"])
        self.assertEqual(result["terms"]["118:House"]["zero_denominator_stages"], list(STAGES[1:]))
        a["milestones"]["AIC"] = {"date": None, "state": "unknown", "reached": None}
        result = run([a])
        self.assertEqual(result["terms"]["118:House"]["coverage"]["unknown_milestones"]["AIC"], 1)

    def test_letters_substantive_advocacy_with_no_invented_milestones(self):
        a, letter = bill(), bill(9, measure_type="letter", eligible=True)
        result = run([a, letter], weights={letter["measure_id"]: 10})
        self.assertEqual(result["terms"]["118:House"]["denominators"], dict.fromkeys(STAGES, 5.))
        rows = [r for r in result["contributions"] if r["measure_id"] == letter["measure_id"]]
        self.assertTrue(all(r["stage"] == "ADVOCACY" for r in rows))
        self.assertEqual(rows[0]["impact_weight"], 5)
        self.assertAlmostEqual(member(result)["standard"]["advocacy"]["net"], 6.)
        self.assertAlmostEqual(member(result)["standard"]["results"]["net"], 6.)

    def test_ineligible_amendment_advocacy_discount_separate_from_impact(self):
        a, amendment = bill(), bill(3, measure_type="hamdt", eligible=True)
        amendment["advocacy_discount"] = .5
        result = run([a, amendment])
        self.assertEqual(result["terms"]["118:House"]["coverage"]["eligible_measures"], 1)
        self.assertAlmostEqual(member(result)["standard"]["advocacy"]["net"], 4.5)

    def test_later_join_monotonic_original_distinct_and_no_retroactive_stages(self):
        a = bill()
        result = run([a], [role(a["measure_id"]), role(a["measure_id"], "B", "original"),
                          role(a["measure_id"], "C", "cosponsor", 90),
                          role(a["measure_id"], "D", "cosponsor", 180)],
                     roster=population("A", "B", "C", "D"))
        c = member(result, "C")["standard"]["results"]["net"]
        d = member(result, "D")["standard"]["results"]["net"]
        self.assertAlmostEqual(c, 2 * .8 * 2 ** (-.5))
        self.assertAlmostEqual(d, 2 * .8 * .5)
        self.assertGreater(c, d)
        self.assertEqual(member(result, "B")["standard"]["results"]["net"], 8.)
        rows = [r for r in result["contributions"] if r["bioguide_id"] == "C" and r.get("kind") == "results"]
        self.assertEqual({r["stage"] for r in rows}, {"PASS", "LAW"})
        self.assertEqual({r["timing_multiplier"] for r in rows}, {2 ** (-.5)})

    def test_join_inclusive_withdrawal_exclusive_and_duplicate_episodes(self):
        a = bill()
        late = role(a["measure_id"], "B", "cosponsor", 200, 300)
        result = run([a], [role(a["measure_id"]), late, late])
        rows = [r for r in result["contributions"] if r["bioguide_id"] == "B" and r.get("kind") == "results"]
        self.assertEqual([r["stage"] for r in rows], ["PASS", "PASS"])
        self.assertEqual(result["coverage"]["duplicate_roles"], 1)
        self.assertAlmostEqual(member(result, "B")["standard"]["results"]["net"], .4 * 2 ** (-200 / 180))

    def test_rejoin_keeps_first_delay_and_deduplicates_advocacy(self):
        a = bill()
        result = run([a], [role(a["measure_id"]), role(a["measure_id"], "B", "cosponsor", 90, 150),
                          role(a["measure_id"], "B", "cosponsor", 250)])
        rows = [r for r in result["contributions"] if r["bioguide_id"] == "B" and r.get("kind") == "results"]
        self.assertEqual({r["stage"] for r in rows}, {"LAW"})
        self.assertEqual({r["delay_days"] for r in rows}, {90})
        self.assertEqual(member(result, "B")["standard"]["advocacy"]["distinct_measures"], 1)

    def test_future_actions_roles_signals_and_date_only_rerun(self):
        a = bill()
        roles = [role(a["measure_id"]), role(a["measure_id"], "B", "cosponsor", 90)]
        earlier = run([a], roles, as_of=iso(220))
        later = run([a], roles, as_of=iso(280))
        first_rows = [r for r in earlier["contributions"] if r.get("kind") == "results"]
        second_rows = [r for r in later["contributions"] if r.get("kind") == "results"]
        self.assertEqual(first_rows, second_rows)
        self.assertTrue(all(r["stage"] != "LAW" for r in first_rows))
        future = run([a], [role(a["measure_id"]), role(a["measure_id"], "B", "cosponsor", 400)],
                     [signal(a["measure_id"], effective_date=iso(400))], as_of=iso(350))
        self.assertEqual(future["coverage"]["future_join"], 1)
        self.assertEqual(future["coverage"]["future_signals"], 1)

    def test_bad_or_missing_chronology_quarantined(self):
        a = bill()
        bad = role(a["measure_id"], "B", "cosponsor", -1)
        original = role(a["measure_id"], "B", "original", 10)
        missing = role(a["measure_id"], "B", "cosponsor")
        missing["joined_date"] = None
        result = run([a], [role(a["measure_id"]), bad, original, missing])
        self.assertEqual(member(result, "B")["coverage"]["quarantined_roles"], 3)
        self.assertIsNone(member(result, "B")["standard"]["results"]["net"])

    def test_signals_honor_accepted_member_scope(self):
        a = bill()
        result = run([a], [role(a["measure_id"]), role(a["measure_id"], "B", "original")],
                     [signal(a["measure_id"], member="A")])
        self.assertIsNone(member(result, "B")["standard"]["results"]["net"])
        self.assertEqual(member(result, "B")["coverage"]["selected_measures"], [])

    def test_tensor_unique_bill_and_confidence_not_multiplier(self):
        a = bill()
        result = run([a], signals=[signal(a["measure_id"], tensor=.6, confidence=.01),
                                  signal(a["measure_id"], tensor=.6, category="Other", confidence=.99)])
        self.assertAlmostEqual(member(result)["tensor"]["results"]["net"], 1.2)
        self.assertAlmostEqual(member(result)["standard"]["results"]["net"], 6.)
        self.assertEqual(member(result)["tensor"]["results"]["distinct_measures"], 1)
        self.assertAlmostEqual(member(result)["tensor"]["categories"]["Other"]["net"], 1.2)

    def test_missing_conflicting_signal_is_unknown_not_zero(self):
        a = bill()
        result = run([a], signals=[signal(a["measure_id"], None, None)])
        self.assertIsNone(member(result)["standard"]["results"]["net"])
        self.assertEqual(member(result)["standard"]["results"]["unknown_count"], 5)
        conflict = run([a], signals=[signal(a["measure_id"], tensor=.4),
                                    signal(a["measure_id"], tensor=.8, category="Other")])
        self.assertIsNone(member(conflict)["tensor"]["results"]["net"])
        neutral = run([a], signals=[signal(a["measure_id"], 0, 0)])
        self.assertEqual(member(neutral)["tensor"]["results"]["net"], 0.)

    def test_category_priority_once_with_explicit_bill_allocation(self):
        a = bill()
        result = run([a], signals=[signal(a["measure_id"], 1, category="X"),
                                  signal(a["measure_id"], -1, category="Y")],
                     policy=ScoringPolicy(category_weights={"X": 2, "Y": 1}))
        self.assertAlmostEqual(member(result)["standard"]["results"]["net"], 3.)
        self.assertAlmostEqual(member(result)["standard"]["results"]["positive"], 6.)
        self.assertAlmostEqual(member(result)["standard"]["results"]["negative"], 3.)
        self.assertAlmostEqual(member(result)["standard"]["categories"]["X"]["net"], 6.)
        self.assertAlmostEqual(member(result)["standard"]["categories"]["Y"]["net"], -6.)

    def test_normalize_terms_before_decay_and_distinct_roster(self):
        a, b = bill(), bill(congress=119)
        b["introduced_date"] = "2025-01-03"
        for stage in STAGES:
            b["milestones"][stage]["date"] = "2025-02-01"
        b["milestones"]["BILL"]["date"] = "2025-01-03"
        rb = role(b["measure_id"])
        rb["joined_date"] = "2025-01-03"
        roster = population("A", "B") + population("A", "B", "C", "D", congress=119)
        roster.append(copy.deepcopy(roster[0]))
        result = run([a, b], [role(a["measure_id"]), rb], roster=roster, as_of="2026-09-01")
        self.assertEqual(result["terms"]["118:House"]["population"], 2)
        self.assertAlmostEqual(member(result)["standard"]["results"]["net"], 6.)
        self.assertAlmostEqual(member(result, congress=119)["standard"]["results"]["net"], 12.)
        history = next(r for r in result["historical_members"] if r["bioguide_id"] == "A")
        self.assertAlmostEqual(history["standard"]["results"]["net"], .8 * 6 + 12)

    def test_institutional_context_retains_full_results(self):
        a = bill()
        base = run([a])
        context = [{"bioguide_id": "A", "congress": 118, "committee_role": "chair", "majority": True}]
        contextual = run([a], institutional_context=context)
        self.assertEqual(member(base)["standard"], member(contextual)["standard"])
        self.assertEqual(member(contextual)["institutional_context"], context)

    def test_duplicate_bill_conflict_rejected_and_policy_is_shadow(self):
        a, duplicate = bill(), bill()
        duplicate["introduced_date"] = iso(2)
        with self.assertRaises(ValueError):
            run([a, duplicate])
        with self.assertRaises(ValueError):
            ScoringPolicy(cosponsor_half_life_days=0)
        result = run([a, a])
        self.assertEqual(result["policy"]["policy_status"], "shadow_candidate")
        self.assertEqual(result["policy"]["cosponsor_half_life_days"], 180.)

    def test_pre_introduction_action_retained_without_any_member_credit(self):
        a = bill()
        a["milestones"]["AIC"]["date"] = iso(-100)
        result = run([a])
        term = result["terms"]["118:House"]
        self.assertEqual(term["denominators"]["AIC"], 5.)
        self.assertEqual(term["coverage"]["unattributed_stage_credit"]["AIC"], 1)
        self.assertFalse(term["sponsor_mean_one_expected"])
        self.assertIsNone(member(result)["sponsor_reference"]["total"])
        self.assertAlmostEqual(member(result)["sponsor_reference"]["observed_total"], 1.6)
        self.assertFalse(any(row["stage"] == "AIC" for row in result["contributions"]))

    def test_sponsor_withdrawal_also_excludes_later_reference_milestones(self):
        a = bill()
        result = run([a], [role(a["measure_id"], withdrawn=200)])
        reference = member(result)["sponsor_reference"]
        self.assertAlmostEqual(reference["observed_total"], 1.2)
        self.assertIsNone(reference["total"])
        self.assertEqual(result["terms"]["118:House"]["coverage"]["unattributed_stage_credit"]["PASS"], 1)
        self.assertFalse(any(row["stage"] in ("PASS", "LAW") for row in result["contributions"]))

    def test_selected_missing_role_keeps_partial_known_results_but_net_unavailable(self):
        a, b = bill(1), bill(2)
        result = run([a, b], [role(a["measure_id"])],
                     [signal(a["measure_id"], member="A"), signal(b["measure_id"], member="A")])
        self.assertIsNone(member(result)["standard"]["results"]["net"])
        self.assertAlmostEqual(member(result)["standard"]["results"]["observed_net"], 3.)
        self.assertEqual(result["coverage"]["accepted_selections_without_verified_role"], 1)

    def test_same_member_two_chambers_keeps_separate_populations_and_results(self):
        a, b = bill(), bill(2, measure_type="s")
        b["chamber"] = "Senate"
        roster = population("A", "B") + [{"bioguide_id": "A", "chamber": "Senate", "congress": 118}]
        result = run([a, b], roster=roster)
        rows = [row for row in result["members"] if row["bioguide_id"] == "A"]
        self.assertEqual({row["chamber"] for row in rows}, {"House", "Senate"})
        self.assertEqual(result["terms"]["118:Senate"]["population"], 1)

    def test_sparse_signs_preserve_known_observed_components(self):
        a = bill()
        result = run([a], signals=[signal(a["measure_id"], 1, category="X"),
                                  signal(a["measure_id"], None, category="Y")])
        bucket = member(result)["standard"]["results"]
        self.assertIsNone(bucket["net"])
        self.assertAlmostEqual(bucket["observed_net"], 3.)


if __name__ == "__main__":
    unittest.main()
