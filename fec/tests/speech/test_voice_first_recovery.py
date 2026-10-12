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


if __name__ == "__main__":
    unittest.main()
