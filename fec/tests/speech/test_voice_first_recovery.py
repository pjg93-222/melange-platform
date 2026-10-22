import unittest

from fec.speech import voice_first_recovery as VFR


class VoiceFirstRecoveryTests(unittest.TestCase):
    def test_complete_recording_uses_two_exact_grids(self):
        windows = VFR.all_grid_windows(5 * VFR.WINDOW_FRAMES)
        self.assertEqual(
            windows,
            [(0, index * VFR.WINDOW_FRAMES) for index in range(5)]
            + [
                (4 * VFR.SAMPLE_RATE, 4 * VFR.SAMPLE_RATE + index * VFR.WINDOW_FRAMES)
                for index in range(4)
            ],
        )

    def test_seeded_scan_covers_both_grids_and_dense_expansion_is_exact(self):
        total = 40 * VFR.WINDOW_FRAMES
        seeds = VFR.seeded_grid_windows(total, stride_sec=64)
        self.assertTrue(any(grid == 0 for grid, _ in seeds))
        self.assertTrue(any(grid == 4 * VFR.SAMPLE_RATE for grid, _ in seeds))
        dense = VFR.dense_windows_around_seeds(total, [seeds[3]], radius_sec=24)
        self.assertTrue(dense)
        self.assertTrue(all((start - grid) % VFR.WINDOW_FRAMES == 0 for grid, start in dense))
        self.assertTrue(all(0 <= start <= total - VFR.WINDOW_FRAMES for _, start in dense))

    def test_only_three_consecutive_same_winner_windows_form_a_unit(self):
        rows = []
        for index, member in enumerate(("Ada", "Ada", "Ada", "Grace", "Ada", "Ada")):
            row = {
                "source_audio_sha256": "a" * 64,
                "grid_offset_frames": 0,
                "source_start_frame": index * VFR.WINDOW_FRAMES,
                "winner": member,
                "winner_best": 0.9,
                "best_anchor_margin": 0.5,
                "passes_3x_window": True,
                "window_row_sha256": str(index) * 64,
            }
            rows.append(row)
        runs = VFR.passing_runs(rows)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["member"], "Ada")
        self.assertEqual(runs[0]["source_start_frame"], 0)
        self.assertEqual(runs[0]["source_frame_count"], 3 * VFR.WINDOW_FRAMES)

    def test_profile_selection_prefers_distinct_sources_and_rejects_overlap(self):
        def run(source, start, margin):
            return {
                "source_audio_sha256": source * 64,
                "source_start_frame": start,
                "source_frame_count": 3 * VFR.WINDOW_FRAMES,
                "member": "Ada", "minimum_best_anchor_margin": margin,
            }
        values = [
            run("a", 0, 0.9),
            run("a", VFR.WINDOW_FRAMES, 0.8),
            run("b", 0, 0.7),
            run("a", 3 * VFR.WINDOW_FRAMES, 0.6),
        ]
        selected = VFR.select_disjoint_profile_units(values, {"Ada": 3})
        self.assertEqual(len(selected), 3)
        self.assertEqual(
            {row["source_audio_sha256"] for row in selected}, {"a" * 64, "b" * 64},
        )
        self.assertNotIn(values[1], selected)

    def test_subject_is_attached_after_identity_from_transcript_only(self):
        self.assertEqual(
            VFR.classify_subject("The border and asylum system need immigration reform"),
            "immigration",
        )
        self.assertEqual(VFR.classify_subject("Thank you for having me today"),
                         "general_public_affairs")

    def test_target_eligibility_is_recomputed_from_real_events_and_seconds(self):
        rows = [
            {"member": "Eligible", "event_id": "a", "duration_sec": 60},
            {"member": "Eligible", "event_id": "b", "duration_sec": 60},
            {"member": "Eligible", "event_id": "c", "duration_sec": 60},
            {"member": "OneEvent", "event_id": "x", "duration_sec": 200},
            {"member": "OneEvent", "event_id": "x", "duration_sec": 200},
            {"member": "OneEvent", "event_id": "x", "duration_sec": 200},
            {"member": "TooShort", "event_id": "a", "duration_sec": 8},
            {"member": "TooShort", "event_id": "b", "duration_sec": 8},
            {"member": "TooShort", "event_id": "c", "duration_sec": 8},
        ]
        coverage = VFR.physical_member_coverage(rows)
        self.assertTrue(coverage["Eligible"]["target_eligible"])
        self.assertFalse(coverage["OneEvent"]["target_eligible"])
        self.assertFalse(coverage["TooShort"]["target_eligible"])
        self.assertEqual(coverage["OneEvent"]["duration_sec"], 600.0)
        self.assertEqual(coverage["OneEvent"]["events"], 1)

    def test_scan_chain_refuses_missing_per_claim_thresholds_and_foreign_vault(self):
        ok = "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        anchor = {"combined_vectors_sha256": "v", "model_manifest_sha256": "m"}
        prod = {"per_claim_thresholds_status": ok, "per_claim_false_accepts": 0,
                "anchor_vectors_sha256": "v", "model_manifest_sha256": "m"}
        multi = {"protocols": {"1x8": {"per_claim_thresholds_status": ok}},
                 "production_three_window_calibration_sha256": "p"}
        VFR.require_scan_calibration_chain(anchor, prod, multi, "p")
        cases = [
            ({"per_claim_thresholds_status": None}, {}, "p"),
            ({"per_claim_false_accepts": 1}, {}, "p"),
            ({"anchor_vectors_sha256": "other"}, {}, "p"),
            ({"model_manifest_sha256": "other"}, {}, "p"),
            ({}, {"protocols": {"1x8": {}}}, "p"),
            ({}, {}, "different-file"),
        ]
        for prod_patch, multi_patch, sha in cases:
            with self.assertRaises(VFR.VoiceFirstRefused):
                VFR.require_scan_calibration_chain(
                    anchor, {**prod, **prod_patch}, {**multi, **multi_patch}, sha)

    def test_word_gate_runs_before_profile_cap(self):
        words = [{"word": "one two three four five", "start_sec": 0.0, "end_sec": 1.0}]
        runs = [
            {"source_audio_sha256": "a", "source_start_frame": 0, "source_frame_count": 100},
            {"source_audio_sha256": "a", "source_start_frame": 100 * VFR.SAMPLE_RATE,
             "source_frame_count": 100},
        ]
        kept, rejected = VFR.filter_runs_by_word_gate(runs, {"a": words})
        self.assertEqual(kept, runs[:1])
        self.assertEqual(rejected, 1)

    def test_scan_status_is_pass_only_when_a_dense_window_passed(self):
        self.assertEqual(VFR.scan_status_for([]), "NO_PASSING_WINDOWS")
        self.assertEqual(VFR.scan_status_for([{"passes_3x_window": False}]),
                         "NO_PASSING_WINDOWS")
        self.assertEqual(
            VFR.scan_status_for([{"passes_3x_window": False}, {"passes_3x_window": True}]),
            "PASS",
        )


if __name__ == "__main__":
    unittest.main()
