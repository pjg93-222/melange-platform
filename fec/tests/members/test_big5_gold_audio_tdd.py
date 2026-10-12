# -*- coding: utf-8 -*-
"""TDD contracts for the 2026-07-15 Senate-gold and source-audio expansion."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import unittest


FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
DATA_ROOT = Path(os.environ.get("MONEYBALL_ROOT", ROOT))


class GoldMultitraitContracts(unittest.TestCase):
    def setUp(self):
        self.artifact = json.loads((DATA_ROOT / "data" / "stance" / "big5_gold_anchored.json").read_text(encoding="utf-8"))
        self.profile = json.loads((DATA_ROOT / "data" / "members" / "profile_basis_v2.json").read_text(encoding="utf-8"))
        self.desk = {row["name"]: row for row in
                     json.loads((DATA_ROOT / "member_desk_bundle.json").read_text(encoding="utf-8"))["members"]}

    def test_every_big_five_trait_has_out_of_fold_gold_results(self):
        self.assertEqual("big5-gold-anchored-v2", self.artifact["schema"])
        self.assertEqual({"O", "C", "E", "A", "N"}, set(self.artifact["traits"]))
        for trait, result in self.artifact["traits"].items():
            self.assertIn(result["status"], {
                "ACTIVE_EXTERNAL_GOLD_ANCHORED",
                "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED",
                "WITHHELD_FAILED_GATES",
            })
            self.assertTrue(result["validation"]["predictions_are_out_of_fold"], trait)
            self.assertEqual({"senate_insiders", "academic_experts"}, set(result["validation"]["panels"]), trait)
            self.assertIn("selected_weights", result, trait)
            self.assertIn("ablation", result, trait)

    def test_e_explicitly_tests_dominance_and_role_adjusted_visibility(self):
        e = self.artifact["traits"]["E"]
        self.assertIn("interaction_dominance", e["candidate_channels"])
        self.assertIn("role_adjusted_visibility", e["candidate_channels"])
        self.assertIn("without_interaction_dominance", e["ablation"])
        self.assertIn("without_role_adjusted_visibility", e["ablation"])
        self.assertEqual(
            e["uses_interaction_dominance"],
            e["selected_weights"].get("interaction_dominance", 0) > 0,
        )
        self.assertEqual(
            e["uses_role_adjusted_visibility"],
            e["selected_weights"].get("role_adjusted_visibility", 0) > 0,
        )

    def test_neuroticism_reverses_expert_emotional_stability(self):
        n = self.artifact["traits"]["N"]
        self.assertEqual("negative_expert_emotional_stability", n["gold_direction"])
        self.assertIn("affective_content_n", n["candidate_channels"])
        self.assertGreaterEqual(n["coverage_n"], 450)

    def test_all_broad_traits_activate_when_at_least_one_panel_passes(self):
        self.assertEqual(
            {"O": "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED",
             "C": "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED",
             "E": "ACTIVE_EXTERNAL_GOLD_ANCHORED",
             "A": "ACTIVE_EXTERNAL_GOLD_ANCHORED",
             "N": "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED"},
            {trait: row["status"] for trait, row in self.artifact["traits"].items()},
        )
        for trait, result in self.artifact["traits"].items():
            self.assertTrue(any(result["association_gates"].values()), trait)
            self.assertGreater(len(result["members"]), 300, trait)

    def test_profile_propagates_all_broad_gold_traits_without_imputation(self):
        common = set.intersection(*(set(self.artifact["traits"][trait]["members"])
                                    for trait in ("O", "C", "E", "A", "N")))
        sample = sorted(common)[0]
        member = self.profile["members"][sample]
        canonical = {"O": "Openness", "C": "Conscientiousness", "E": "Extraversion",
                     "A": "Agreeableness", "N": "Neuroticism"}
        self.assertEqual({"O", "C", "E", "A", "N"}, set(member["external_gold_traits"]))
        for trait, gold in self.artifact["traits"].items():
            observed = member["external_gold_traits"][trait]
            self.assertEqual(gold["status"], observed["status"], trait)
            self.assertEqual(gold["members"][sample][trait], observed["z"], trait)
            self.assertEqual(observed["z"], member["personality_facets"][canonical[trait]]["z"], trait)

    def test_profile_integrates_broad_n_and_retains_qualified_affective_n(self):
        n_gold = self.artifact["traits"]["N"]
        self.assertEqual("ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED", n_gold["status"])
        observed = [row for row in self.profile["members"].values()
                    if row["personality_facets"]["Neuroticism"]["z"] is not None]
        self.assertEqual(n_gold["coverage_n"], len(observed))
        self.assertGreaterEqual(sum(row["personality_facets"]["Neuroticism_affective_content"]["z"] is not None
                                    for row in self.profile["members"].values()), 450)
        self.assertTrue(all(row["personality_facets"]["Neuroticism"]["z"] ==
                            row["external_gold_traits"]["N"]["z"] for row in observed))

    def test_member_desk_uses_broad_gold_for_every_canonical_axis(self):
        order = ("E", "O", "C", "A", "N")
        for name, row in self.desk.items():
            expected = [((self.artifact["traits"][trait]["members"].get(name) or {}).get("percentile"))
                        for trait in order]
            expected_payload = None if all(value is None for value in expected) else expected
            self.assertEqual(expected_payload, (row.get("psy") or {}).get("big5_pct"), name)


class AudioSourcePilotContracts(unittest.TestCase):
    def setUp(self):
        self.pilot = json.loads((DATA_ROOT / "data" / "members" / "b5_audio_source_pilot.json.candidate").read_text(encoding="utf-8"))

    def test_pilot_audits_all_requested_source_families(self):
        self.assertEqual("b5-audio-source-pilot-v1", self.pilot["schema"])
        self.assertEqual({"washington_journal", "cnn", "floor", "houselive"}, set(self.pilot["sources"]))
        for source, row in self.pilot["sources"].items():
            self.assertIn(row["status"], {"MEASUREMENT_SMOKE_PASS", "BLOCKED_NO_AUDIO_BINDING", "BLOCKED_NO_ALIGNED_AUDIO"}, source)
            self.assertIn("transcript_units", row, source)
            self.assertIn("local_audio_files", row, source)

    def test_smoke_results_cannot_become_production_profiles(self):
        self.assertEqual("RESEARCH_SMOKE_ONLY_NOT_PROFILE", self.pilot["status"])
        self.assertEqual(0, self.pilot["production_profile_n"])
        self.assertFalse(self.pilot["contract"]["unreviewed_audio_may_enter_profiles"])
        self.assertTrue(self.pilot["contract"]["within_speaker_pitch_deviation_required"])
        self.assertTrue(self.pilot["contract"]["transcript_identified_topics_required"])

    def test_houselive_smoke_has_paired_official_timing_and_finite_deviations(self):
        source = self.pilot["sources"]["houselive"]
        smoke = self.pilot["houselive_measurement_smoke"]
        self.assertEqual("MEASUREMENT_SMOKE_PASS", source["status"])
        self.assertEqual("MEASUREMENT_SMOKE_PASS", smoke["status"])
        self.assertGreaterEqual(smoke["speech_units"], 5)
        self.assertGreater(smoke["named_speaker_units"], 0)
        self.assertTrue(smoke["media_asset"]["url"].startswith("https://houseliveprod-"))
        self.assertEqual("WebVTT", smoke["caption_asset"]["type"])
        self.assertGreaterEqual(len({row["topic"] for row in smoke["units"] if row["topic"] != "other"}), 2)
        for row in smoke["units"]:
            self.assertIsInstance(row["pitch_deviation_from_speaker_baseline_z"], float)
            self.assertEqual(64, len(row["audio_sha256"]))
            self.assertEqual(64, len(row["transcript_sha256"]))


class BehavioralIntegrityContracts(unittest.TestCase):
    def setUp(self):
        self.behavior = json.loads((DATA_ROOT / "behavioral_dispositions.json").read_text(encoding="utf-8"))
        self.lep = json.loads((DATA_ROOT / "data/members/lep_criterion.json").read_text(encoding="utf-8"))

    def test_diligence_has_pinned_components_and_excludes_chrg(self):
        self.assertEqual("behavioral-dispositions-v2", self.behavior["schema"])
        self.assertEqual("PASS", self.behavior["status"])
        contract = self.behavior["integrity"]["diligence"]
        self.assertFalse(contract["legacy_chrg_enters_diligence"])
        self.assertTrue(contract["missing_components_never_penalized"])
        self.assertGreaterEqual(contract["roll_call_member_n"], 450)
        self.assertGreaterEqual(contract["lep_followthrough_member_n"], 440)
        self.assertGreaterEqual(self.behavior["coverage"]["diligence_z"], 525)
        self.assertEqual(64, len(self.behavior["inputs_sha256"]["data/members/lep_criterion.json"]))

    def test_diligence_values_are_finite_and_clipped(self):
        values = [row["diligence_z"] for row in self.behavior["members"].values()
                  if row.get("diligence_z") is not None]
        self.assertTrue(values)
        self.assertTrue(all(math.isfinite(value) and -3 <= value <= 3 for value in values))

    def test_lep_followthrough_declares_active_static_scope(self):
        self.assertEqual("lep-criterion-v2", self.lep["schema"])
        self.assertEqual("ACTIVE_STATIC_CRITERION", self.lep["status"])
        self.assertEqual(8, self.lep["min_bills"])
        followthrough = [row["followthrough"] for row in self.lep["per_member"].values()
                         if row.get("followthrough") is not None]
        self.assertGreaterEqual(len(followthrough), 440)
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in followthrough))
        self.assertIn("behavioral_dispositions.py", self.lep["consumers"])


if __name__ == "__main__":
    unittest.main()
