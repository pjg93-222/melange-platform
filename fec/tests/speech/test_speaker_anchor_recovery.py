import json
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import wave

import numpy as np

from fec.speech import source_media_recovery as SMR
from fec.speech import speaker_anchor_recovery as SAR
from fec.speech import build_descriptive_speech as BDS


class SpeakerAnchorRecoveryTests(unittest.TestCase):
    def test_partial_source_interval_overlap_is_rejected(self):
        intervals = {}
        SAR.validated_candidate_frame_interval(intervals, "a" * 64, 0, 384000)
        with self.assertRaisesRegex(SAR.IdentityRefused, "intervals overlap"):
            SAR.validated_candidate_frame_interval(
                intervals, "a" * 64, 320000, 384000,
            )
        SAR.validated_candidate_frame_interval(
            intervals, "a" * 64, 384000, 128000,
        )
        SAR.validated_candidate_frame_interval(
            intervals, "b" * 64, 320000, 384000,
        )

    def test_merge_source_overlap_requires_explicit_consolidation_mode(self):
        with self.assertRaises(SAR.IdentityRefused):
            SAR.validated_merge_source_overlap(
                {"cnn"}, {"cnn", "fox"}, allow_overlap=False,
            )
        self.assertEqual(
            SAR.validated_merge_source_overlap(
                {"cnn"}, {"cnn", "fox"}, allow_overlap=True,
            ),
            {"cnn"},
        )
        self.assertEqual(
            SAR.validated_merge_source_overlap(
                {"abc-this-week"}, {"cnn", "fox"}, allow_overlap=False,
            ),
            set(),
        )

    def _merge_fixture(self, root, overlap_in_chain=False):
        root = Path(root)
        inventory = root / "inventory.jsonl"
        packages = ["https://example.test/abc", "https://example.test/fox"]
        SMR.write_jsonl_atomic(inventory, [
            {"package": package, "package_id": "pkg:%d" % index}
            for index, package in enumerate(packages)
        ])
        calibration = root / "calibration.json"
        calibration.write_text("{}", encoding="utf-8")
        calibration_sha = SMR.sha256_file(calibration)

        candidate_receipts = []
        identity_receipts = []
        for index, source in enumerate(("abc-this-week", "fox"), 1):
            candidate = {
                "schema": SMR.CLIP_SCHEMA, "status": "CANDIDATE_AUDIT_REQUIRED",
                "clip_id": "candidate:%d" % index, "source_family": source,
                "member": "Member One",
                "package": packages[index - 1], "alignment_ratio": 0.99,
                "source_audio_sha256": str(index) * 64,
                "source_receipt_sha256": ("a" if index == 1 else "b") * 64,
                "asr_transcript_sha256": "c" * 64,
                "asr_transcript_receipt_sha256": "d" * 64,
                "source_sample_rate": 16000, "source_start_frame": 0,
                "source_frame_count": (320000 if index == 1 else 384000),
                "source_slice_pcm_sha256": "e" * 64,
                "staged_pcm_sha256": "e" * 64,
                "audio_sha256": ("f" if index == 1 else "0") * 64,
                "turn_id": "sqlite:speeches:%d" % index,
            }
            candidate["cut_binding_sha256"] = SMR.candidate_cut_binding(candidate)
            candidate["clip_sha256"] = SMR._candidate_hash(candidate)
            cands = [candidate]
            if overlap_in_chain and index == 1:
                twin = dict(
                    candidate, clip_id="candidate:3", member="Member Two",
                    source_audio_sha256=candidate["source_audio_sha256"],
                    source_start_frame=0, source_frame_count=160000,
                    audio_sha256="9" * 64, turn_id="sqlite:speeches:3",
                )
                twin["cut_binding_sha256"] = SMR.candidate_cut_binding(twin)
                twin["clip_sha256"] = SMR._candidate_hash(twin)
                cands.append(twin)
            candidate_manifest = root / ("candidate_%d.jsonl" % index)
            SMR.write_jsonl_atomic(candidate_manifest, cands)
            candidate_receipt = root / ("candidate_%d.receipt.json" % index)
            SMR.write_json_atomic(candidate_receipt, {
                "schema": SMR.CANDIDATE_RECEIPT_SCHEMA, "status": "AUDIT_REQUIRED",
                "manifest": str(candidate_manifest.resolve()),
                "manifest_sha256": SMR.sha256_file(candidate_manifest),
                "candidate_clips": len(cands), "inventory": str(inventory.resolve()),
                "inventory_sha256": SMR.sha256_file(inventory),
                "exact_frame_cut_binding_required": True,
            })
            comparison = root / ("comparison_%d.npz" % index)
            comparison.write_bytes(("comparison-%d" % index).encode())
            if index == 1:
                decision = {
                    "schema": SAR.IDENTITY_SCHEMA, "status": "UNRESOLVED",
                    "clip_id": candidate["clip_id"],
                    "claimed_member": candidate["member"],
                    "reasons": [SAR.COMPACT_UNRESOLVED_REASON],
                    "operational_confidence_label": "100_PERCENT_STRUCTURAL_CONSENSUS",
                    "probability_claim": False,
                    "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
                    "identified_at": "2026-08-30T12:00:00-04:00",
                }
            else:
                decision = {
                    "schema": SAR.IDENTITY_SCHEMA, "status": "UNRESOLVED",
                    "clip_id": candidate["clip_id"],
                    "candidate_clip_sha256": candidate["clip_sha256"],
                    "candidate_audio_sha256": candidate["audio_sha256"],
                    "candidate_cut_binding_sha256": candidate["cut_binding_sha256"],
                    "calibration_sha256": calibration_sha,
                    "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
                    "comparison_matrix": str(comparison.resolve()),
                    "comparison_matrix_sha256": SMR.sha256_file(comparison),
                }
            decision["identity_row_sha256"] = SAR._row_hash(
                decision, "identity_row_sha256")
            decisions = [decision]
            if len(cands) > 1:
                twin_decision = dict(
                    decision, clip_id="candidate:3", claimed_member="Member Two")
                twin_decision["identity_row_sha256"] = SAR._row_hash(
                    twin_decision, "identity_row_sha256")
                decisions.append(twin_decision)
            identity_manifest = root / ("identity_%d.jsonl" % index)
            SMR.write_jsonl_atomic(identity_manifest, decisions)
            identity_receipt = root / ("identity_%d.receipt.json" % index)
            SMR.write_json_atomic(identity_receipt, {
                "schema": SAR.IDENTITY_RECEIPT_SCHEMA, "status": "PASS",
                "identity_schema": SAR.IDENTITY_SCHEMA,
                "manifest": str(identity_manifest.resolve()),
                "manifest_sha256": SMR.sha256_file(identity_manifest),
                "decisions": len(decisions),
                "candidate_manifest_sha256": SMR.sha256_file(candidate_manifest),
                "candidate_receipt_sha256": SMR.sha256_file(candidate_receipt),
                "inventory_sha256": SMR.sha256_file(inventory),
                "calibration_sha256": calibration_sha,
                "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
                "minimum_alignment_ratio": 0.68,
                "exact_frame_cut_binding_required": True,
                "all_anchor_comparisons_persisted": True,
                "anchor_vectors_sha256": "1" * 64,
                "calibration_window_manifest_sha256": "2" * 64,
                "model_manifest_sha256": "3" * 64,
            })
            candidate_receipts.append(str(candidate_receipt))
            identity_receipts.append(str(identity_receipt))

        args = SimpleNamespace(
            merge_candidate_receipt=candidate_receipts,
            merge_identity_receipt=identity_receipts,
            minimum_ratio=0.68, calibration=str(calibration), inventory=str(inventory),
            candidate_manifest=str(root / "cumulative_candidates.jsonl"),
            candidate_receipt=str(root / "cumulative_candidates.receipt.json"),
            decision_manifest=str(root / "cumulative_identity.jsonl"),
            decision_receipt=str(root / "cumulative_identity.receipt.json"),
        )
        return args, calibration_sha

    def test_merge_identity_receipt_copies_computed_frame_disjointness(self):
        with tempfile.TemporaryDirectory() as directory:
            args, _ = self._merge_fixture(Path(directory), overlap_in_chain=True)
            self.assertEqual(SAR.run_merge(args), 0)
            cand = json.loads(Path(args.candidate_receipt).read_text())
            ident = json.loads(Path(args.decision_receipt).read_text())
            self.assertIs(cand["input_candidate_audio_frame_sets_disjoint"], False)
            self.assertIs(ident["input_candidate_audio_frame_sets_disjoint"], False)

    def test_short_anchor_is_refused_before_embedding(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wav = root / "Member One" / "a.wav"
            wav.parent.mkdir()
            wav.write_bytes(b"x")
            with mock.patch.object(SAR, "_read_verify", return_value={
                "Member One/a.wav": {"speaker": "SOLO_TARGET", "identity": "MATCH", "keep": True},
            }), mock.patch.object(SAR, "wav_contract", return_value=(10.0, 160000)):
                rows, summary = SAR.build_anchor_manifest(
                    Path("verify.json"), root, hash_audio=False)
            self.assertEqual(rows, [])
            self.assertEqual(len(summary["refused_references"]), 1)

    def test_embed_and_identify_refuse_model_swap(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "anchors.jsonl"
            SMR.write_jsonl_atomic(manifest, [])
            model = root / "model.json"
            model.write_text("{}", encoding="utf-8")
            receipt = root / "anchors.receipt.json"
            SMR.write_json_atomic(receipt, {
                "schema": SAR.ANCHOR_RECEIPT_SCHEMA,
                "manifest_sha256": SMR.sha256_file(manifest),
                "model_manifest_sha256": "0" * 64,
            })
            args = SimpleNamespace(
                anchor_manifest=str(manifest), anchor_receipt=str(receipt),
                model_manifest=str(model), vault_root=str(root / "vault"),
                max_anchors=None, shard_index=0, shard_count=1, workers=1,
            )
            result = {"status": "PASS"}
            with mock.patch.object(SAR, "_production_backend") as backend,                     mock.patch.object(SAR, "build_embedding_vault", return_value=result) as build:
                # A fresh vault root passes even though the receipt records an old hash.
                self.assertEqual(SAR.run_embed(args), 0)
                build.assert_called_once()
                # The vault root is now bound to this model; a different model on it is refused.
                model.write_text('{"swap": 1}', encoding="utf-8")
                build.reset_mock()
                with self.assertRaisesRegex(SAR.IdentityRefused, "different model"):
                    SAR.run_embed(args)
                build.assert_not_called()
            SAR._bind_cache_to_model(root / "cache", "a" * 64)
            SAR._bind_cache_to_model(root / "cache", "a" * 64)
            with self.assertRaisesRegex(SAR.IdentityRefused, "different model"):
                SAR._bind_cache_to_model(root / "cache", "b" * 64)

    def test_identify_cancels_queued_work_drops_orphans_and_reuses_compact_rows(self):
        source = Path(SAR.__file__).read_text(encoding="utf-8")
        start = source.index("def run_identify")
        end = source.index("def run_merge")
        identify = source[start:end]
        self.assertIn("shutdown(wait=True, cancel_futures=True)", identify)
        self.assertIn("in all_candidate_ids", identify)
        gate = identify[identify.index("if prior and not args.refresh:"):]
        self.assertLess(
            gate.index("compact_unresolved_short_window"),
            gate.index("_validate_transcript_binding(candidate)"),
        )

    def test_merge_emits_exact_cumulative_candidate_identity_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args, calibration_sha = self._merge_fixture(root)
            self.assertEqual(SAR.run_merge(args), 0)
            merged_candidates = SMR.read_jsonl(Path(args.candidate_manifest), required=True)
            merged_decisions = SMR.read_jsonl(Path(args.decision_manifest), required=True)
            candidate_receipt = json.loads(Path(args.candidate_receipt).read_text())
            identity_receipt = json.loads(Path(args.decision_receipt).read_text())
            self.assertEqual(len(merged_candidates), 2)
            self.assertEqual(len(merged_decisions), 2)
            self.assertNotIn("candidate_clip_sha256", merged_decisions[0])
            self.assertTrue(candidate_receipt["cumulative_chain_merge"])
            self.assertEqual(candidate_receipt["input_chains"], identity_receipt["input_chains"])
            self.assertEqual(
                candidate_receipt["input_chains_sha256"],
                identity_receipt["input_chains_sha256"],
            )
            self.assertTrue(BDS._validate_cumulative_candidate_identity_merge(
                candidate_receipt, identity_receipt, merged_candidates, merged_decisions,
            ))
            expected, errors = SAR.expected_secure_final_ids(
                merged_decisions,
                {row["clip_id"]: row for row in merged_candidates},
                calibration_sha,
            )
            self.assertEqual(expected, set())
            self.assertEqual(errors, [])

    def test_compact_unresolved_identity_rejects_forged_rows(self):
        candidate = {
            "clip_id": "candidate:short", "member": "Member One",
            "source_frame_count": 320000,
        }
        decision = {
            "schema": SAR.IDENTITY_SCHEMA, "status": "UNRESOLVED",
            "clip_id": candidate["clip_id"], "claimed_member": candidate["member"],
            "reasons": [SAR.COMPACT_UNRESOLVED_REASON],
            "operational_confidence_label": "100_PERCENT_STRUCTURAL_CONSENSUS",
            "probability_claim": False,
            "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
            "identified_at": "2026-08-30T12:00:00-04:00",
        }
        decision["identity_row_sha256"] = SAR._row_hash(
            decision, "identity_row_sha256")
        self.assertEqual(
            SAR.validate_merge_identity_decision(decision, candidate, "a" * 64),
            "compact_unresolved_short_window",
        )
        for mutation in (
            {"reasons": ["candidate hash mismatch"]},
            {"accepted_runs": []},
            {"status": "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"},
        ):
            forged = {**decision, **mutation}
            forged["identity_row_sha256"] = SAR._row_hash(
                forged, "identity_row_sha256")
            with self.assertRaises(SAR.IdentityRefused):
                SAR.validate_merge_identity_decision(forged, candidate, "a" * 64)
        with self.assertRaises(SAR.IdentityRefused):
            SAR.validate_merge_identity_decision(
                decision, {**candidate, "source_frame_count": 384000}, "a" * 64,
            )

    def test_cumulative_verified_source_ledger_is_per_source_and_rejects_duplicates(self):
        def units(source, suffix):
            candidate = {
                "schema": SMR.CLIP_SCHEMA, "clip_id": "candidate:" + suffix,
                "source_family": source, "audio_sha256": suffix * 64,
                "clip_sha256": (suffix.upper() * 64).lower(),
            }
            decision = {
                "clip_id": candidate["clip_id"],
                "status": "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "identity_row_sha256": ("d" + suffix) * 32,
            }
            final = {
                "clip_id": candidate["clip_id"], "parent_clip_id": candidate["clip_id"],
                "source_family": source, "audio_sha256": ("f" + suffix) * 32,
                "source_audio_sha256": ("s" + suffix) * 32,
                "source_start_frame": 0, "source_frame_count": 384000,
                "identity_run_sha256": None, "final_row_sha256": ("r" + suffix) * 32,
            }
            return candidate, decision, final

        abc = units("abc-this-week", "1")
        fox = units("fox", "2")
        abc_ledger, errors = SAR.cumulative_verified_source_ledger(
            [abc[0]], [abc[1]], [abc[2]])
        self.assertEqual(errors, [])
        cumulative, errors = SAR.cumulative_verified_source_ledger(
            [abc[0], fox[0]], [abc[1], fox[1]], [abc[2], fox[2]])
        self.assertEqual(errors, [])
        self.assertEqual(cumulative["abc-this-week"], abc_ledger["abc-this-week"])
        self.assertEqual(cumulative["fox"]["verified_final_clips"], 1)

        duplicate = dict(fox[2], audio_sha256=abc[2]["audio_sha256"])
        _ledger, errors = SAR.cumulative_verified_source_ledger(
            [abc[0], fox[0]], [abc[1], fox[1]], [abc[2], duplicate])
        self.assertTrue(any("duplicate final audio" in row["reason"] for row in errors))

    def test_identity_parallelism_changes_throughput_not_evidence_contract(self):
        source = Path(SAR.__file__).read_text(encoding="utf-8")
        self.assertIn("ThreadPoolExecutor", source)
        self.assertIn("concurrent.futures.as_completed", source)
        self.assertIn("extra RAM buys throughput rather than relaxed evidence", source)
        self.assertIn('parser.add_argument("--workers"', source)
        reuse_start = source.index("if prior and not args.refresh:")
        reuse_end = source.index("if args.max_candidates", reuse_start)
        reuse_gate = source[reuse_start:reuse_end]
        self.assertIn("_validate_transcript_binding(candidate)", reuse_gate)
        self.assertIn("_validate_candidate_cut_binding(", reuse_gate)
        self.assertIn("validate_candidate_provenance_binding(", reuse_gate)
        self.assertIn("current_candidate_valid", reuse_gate)
        self.assertIn("candidate.get(\"schema\") == SMR.CLIP_SCHEMA", reuse_gate)

    def test_four_networks_are_processed_before_remaining_sources(self):
        values = ["pbs", "cnn", "fox", "npr", "abc-this-week", "msnow"]
        self.assertEqual(
            SAR.ordered_source_families(values),
            ["abc-this-week", "fox", "msnow", "cnn", "npr", "pbs"],
        )

    def test_anchor_metadata_requires_all_three_prior_acceptance_signals(self):
        accepted = {"speaker": "SOLO_TARGET", "identity": "MATCH", "keep": True}
        self.assertTrue(SAR.accepted_anchor_metadata(accepted))
        for field, value in (("speaker", "MULTI_SPEAKER"), ("identity", "NEUTRAL"), ("keep", False)):
            broken = dict(accepted)
            broken[field] = value
            self.assertFalse(SAR.accepted_anchor_metadata(broken))

    def test_zero_false_accept_calibration_and_unanimous_identity(self):
        anchors = np.asarray(
            [[1.0, 0.0, 0.0]] * 4
            + [[0.0, 1.0, 0.0]] * 4
            + [[0.0, 0.0, 1.0]] * 4,
            dtype=np.float64,
        )
        members = ["Ada"] * 4 + ["Grace"] * 4 + ["Katherine"] * 4
        events = [f"event:{index}" for index in range(len(members))]
        eligible = {"Ada", "Grace", "Katherine"}
        calibration_vectors = np.repeat(anchors, 3, axis=0)
        calibration_members = np.repeat(np.asarray(members), 3).tolist()
        calibration_events = np.repeat(np.asarray(events), 3).tolist()
        calibration_window_indexes = list(range(3)) * len(events)
        calibration = SAR.calibrate_zero_false_accept(
            anchors,
            members,
            eligible,
            event_ids=events,
            calibration_vectors=calibration_vectors,
            calibration_members=calibration_members,
            calibration_event_ids=calibration_events,
            calibration_window_indexes=calibration_window_indexes,
        )
        self.assertEqual(calibration["status"], "PASS_ZERO_FALSE_ACCEPT_CALIBRATION")
        self.assertEqual(calibration["false_accepts"], 0)
        self.assertEqual(calibration["genuine_sequences"], 12)
        self.assertEqual(calibration["wrong_claim_sequences"], 24)
        self.assertTrue(calibration["event_holdout_enforced"])
        self.assertEqual(
            calibration["scoring_contract_sha256"],
            SAR.identity_scoring_contract_sha256(),
        )
        candidate = np.asarray([[1.0, 0.0, 0.0]] * 3)
        decision = SAR.evaluate_identity(candidate, "Ada", anchors, members, eligible, calibration)
        self.assertEqual(decision["status"], "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS")
        self.assertEqual(decision["vaulted_speakers_compared"], 3)
        self.assertTrue(decision["unanimous_windows"])

        dissent = candidate.copy()
        dissent[-1] = [0.0, 1.0, 0.0]
        refused = SAR.evaluate_identity(dissent, "Ada", anchors, members, eligible, calibration)
        self.assertEqual(refused["status"], "UNRESOLVED")
        self.assertIn("WINDOW_2_CONSENSUS_FAIL", refused["reasons"])

        mixed = np.asarray(
            [[1.0, 0.0, 0.0]] * 4 + [[0.0, 1.0, 0.0]],
            dtype=np.float64,
        )
        partial = SAR.evaluate_identity(mixed, "Ada", anchors, members, eligible, calibration)
        self.assertEqual(
            partial["status"],
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        )
        self.assertEqual(partial["accepted_run_count"], 1)
        self.assertEqual(partial["accepted_runs"][0]["start_window"], 0)
        self.assertEqual(partial["accepted_runs"][0]["end_window_exclusive"], 4)

        tie = np.asarray([[1.0, 1.0, 0.0]] * 3, dtype=np.float64)
        tied = SAR.evaluate_identity(tie, "Ada", anchors, members, eligible, calibration)
        self.assertEqual(tied["status"], "UNRESOLVED")

    def test_calibration_groups_duplicate_primary_event_windows_and_rejects_blank_events(self):
        anchors = np.asarray(
            [[1.0, 0.0]] * 3 + [[0.0, 1.0]] * 3,
            dtype=np.float64,
        )
        members = ["Ada"] * 3 + ["Grace"] * 3
        primary_events = ["a", "a", "c", "d", "e", "f"]
        calibration_source_indexes = [0, 2, 3, 4, 5]
        calibration_vectors = np.repeat(anchors[calibration_source_indexes], 3, axis=0)
        calibration_members = np.repeat(
            np.asarray(members)[calibration_source_indexes], 3
        ).tolist()
        calibration_events = np.repeat(
            np.asarray(["a", "c", "d", "e", "f"]), 3
        ).tolist()
        kwargs = {
            "calibration_vectors": calibration_vectors,
            "calibration_members": calibration_members,
            "calibration_event_ids": calibration_events,
            "calibration_window_indexes": list(range(3)) * len(calibration_source_indexes),
        }
        calibration = SAR.calibrate_zero_false_accept(
            anchors,
            members,
            {"Ada", "Grace"},
            event_ids=primary_events,
            **kwargs,
        )
        self.assertFalse(calibration["primary_event_ids_unique"])
        self.assertTrue(calibration["same_event_anchor_windows_excluded_together"])
        with self.assertRaisesRegex(SAR.IdentityRefused, "matching nonblank primary event IDs"):
            SAR.calibrate_zero_false_accept(
                anchors,
                members,
                {"Ada", "Grace"},
                event_ids=["a", "a", "", "d", "e", "f"],
                **kwargs,
            )

    def test_singleton_event_still_contributes_every_wrong_claim_sequence(self):
        anchors = np.asarray(
            [[1.0, 0.0, 0.0]]
            + [[0.0, 1.0, 0.0]] * 2
            + [[0.0, 0.0, 1.0]] * 2,
            dtype=np.float64,
        )
        members = ["Singleton", "Grace", "Grace", "Katherine", "Katherine"]
        events = [f"event:{index}" for index in range(len(members))]
        calibration = SAR.calibrate_zero_false_accept(
            anchors,
            members,
            {"Grace", "Katherine"},
            event_ids=events,
            calibration_vectors=np.repeat(anchors, 3, axis=0),
            calibration_members=np.repeat(np.asarray(members), 3).tolist(),
            calibration_event_ids=np.repeat(np.asarray(events), 3).tolist(),
            calibration_window_indexes=list(range(3)) * len(events),
        )
        self.assertEqual(calibration["events_skipped_no_remaining_true_reference"], 1)
        self.assertEqual(calibration["held_out_events_evaluated"], 4)
        self.assertEqual(calibration["genuine_sequences"], 4)
        self.assertEqual(calibration["wrong_claim_sequences"], 6)

    def test_runtime_windows_are_exactly_calibration_duration_and_drop_tail(self):
        rate = 16000
        width = 8 * rate
        self.assertEqual(SAR.candidate_window_bounds(width - 1, rate), [])
        for seconds in (20.0, 23.999):
            bounds = SAR.candidate_window_bounds(int(seconds * rate), rate)
            self.assertLess(len(bounds), SAR.MIN_CANDIDATE_WINDOWS)
        twenty_five_second_bounds = SAR.candidate_window_bounds(25 * rate, rate)
        self.assertEqual(
            twenty_five_second_bounds,
            [(0, width), (width, 2 * width), (2 * width, 3 * width)],
        )
        coverage = SAR.candidate_identity_coverage(
            twenty_five_second_bounds, 25 * rate, rate
        )
        self.assertFalse(coverage["full_candidate_identity_coverage"])
        self.assertEqual(coverage["identity_tested_frame_count"], 24 * rate)
        self.assertEqual(coverage["identity_excluded_tail_frames"], rate)
        self.assertEqual(
            SAR.candidate_window_bounds(3 * width + width // 2, rate),
            [(0, width), (width, 2 * width), (2 * width, 3 * width)],
        )
        self.assertTrue(
            all(end - start == width for start, end in SAR.candidate_window_bounds(10 * width + 1, rate))
        )

        candidate = {"source_start_frame": 777, "source_frame_count": 5 * width}
        run = {
            "start_window": 1,
            "end_window_exclusive": 4,
            "window_count": 3,
            "relative_start_frame": width,
            "relative_frame_count": 3 * width,
            "source_start_frame": 777 + width,
            "source_frame_count": 3 * width,
            "sample_rate": rate,
        }
        self.assertEqual(
            SAR.validate_identity_run_geometry(candidate, run),
            (width, 3 * width),
        )
        broken = dict(run, relative_frame_count=3 * width + 1)
        with self.assertRaisesRegex(SAR.IdentityRefused, "exact sequence"):
            SAR.validate_identity_run_geometry(candidate, broken)
        zero_origin_candidate = dict(candidate, source_start_frame=0)
        zero_origin_run = dict(run, source_start_frame=width)
        self.assertEqual(
            SAR.validate_identity_run_geometry(zero_origin_candidate, zero_origin_run),
            (width, 3 * width),
        )

    def test_calibration_windows_are_three_exact_disjoint_runtime_windows(self):
        rate = 16000
        samples = np.linspace(-0.2, 0.2, 30 * rate, dtype=np.float32)
        windows, start, end = SAR.select_calibration_windows(samples, rate)
        self.assertEqual(len(windows), 3)
        self.assertTrue(all(window.size == 8 * rate for window in windows))
        self.assertEqual(end - start, 24.0)
        self.assertFalse(np.shares_memory(windows[0], windows[1]))
        with self.assertRaisesRegex(SAR.IdentityRefused, "too short"):
            SAR.select_calibration_windows(np.zeros(20 * rate, dtype=np.float32), rate)

    def test_identity_refuses_missing_anchor_floor_or_failed_calibration(self):
        anchors = np.eye(3)
        members = ["Ada", "Grace", "Katherine"]
        candidate = np.asarray([[1.0, 0.0, 0.0]] * 3)
        failed = {
            "status": "CALIBRATION_STOP_BAR_MISS",
            "minimum_target_best_score": 0.0,
            "minimum_best_anchor_margin": 0.0,
            "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
        }
        decision = SAR.evaluate_identity(candidate, "Ada", anchors, members, set(), failed)
        self.assertEqual(decision["status"], "UNRESOLVED")
        self.assertIn("CALIBRATION_NOT_PASS", decision["reasons"])
        self.assertIn("TARGET_ANCHOR_FLOOR_NOT_MET", decision["reasons"])

    def test_shared_source_requires_turns_from_zero_candidate_packages(self):
        source = str((Path("shared") / "source.wav").resolve())
        rows = [
            {
                "package_id": "pkg:one",
                "members": [{"rowids": [1]}],
                "acquisition": {"items": [{"wav": source, "caption_rowids": []}]},
            },
            {
                "package_id": "pkg:two",
                "members": [{"rowids": [2]}],
                "acquisition": {"items": [{"wav": source, "caption_rowids": []}]},
            },
        ]
        expected, packages = SAR.inventory_source_turn_requirements(rows)
        self.assertEqual(expected[source], {1, 2})
        self.assertEqual(packages[source], {"pkg:one", "pkg:two"})

    def test_retention_requires_transcript_reference_identity_and_final_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.wav"
            staged = root / "staged.wav"
            final_root = root / "final"
            final_wav = final_root / "clip.wav"
            identity_frames = 3 * 8 * 16000
            payload = b"\x00\x00" + (b"\x01\x00" * identity_frames)
            with wave.open(str(source), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(payload)
            with wave.open(str(staged), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(payload[2:])
            final_root.mkdir()

            source_sha = SMR.sha256_file(source)
            source_receipt = root / "source.json"
            source_receipt.write_text(json.dumps({
                "schema": "speech-source-media-acquisition-v1",
                "wav": str(source.resolve()),
                "wav_sha256": source_sha,
                "provider": "internet-archive-tvnews",
                "media_id": "ia:test",
                "reference_url": "https://archive.org/details/example",
            }), encoding="utf-8")
            words = root / "transcript.words.jsonl"
            words.write_text('{"word":"hello","start_sec":0,"end_sec":1}\n', encoding="utf-8")
            transcript_receipt_path = root / "transcript.receipt.json"
            receipt = {
                "schema": SMR.ASR_RECEIPT_SCHEMA,
                "status": "PASS",
                "transcript": str(words.resolve()),
                "transcript_sha256": SMR.sha256_file(words),
                "source_audio_sha256": source_sha,
                "reference_url": "https://archive.org/details/example",
            }
            transcript_receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            pcm_sha = SMR.wav_pcm_slice_sha256(source, 1, identity_frames)
            candidate = {
                "schema": SMR.CLIP_SCHEMA,
                "status": "CANDIDATE_AUDIT_REQUIRED",
                "clip_id": "clip:1",
                "turn_id": "sqlite:speeches:1",
                "source_expected_turn_rowids": [1],
                "alignment_ratio": 0.95,
                "provider": "internet-archive-tvnews",
                "media_id": "ia:test",
                "source_wav": str(source.resolve()),
                "source_audio_sha256": source_sha,
                "source_receipt": str(source_receipt.resolve()),
                "source_receipt_sha256": SMR.sha256_file(source_receipt),
                "asr_transcript": str(words.resolve()),
                "asr_transcript_sha256": SMR.sha256_file(words),
                "asr_transcript_receipt": str(transcript_receipt_path.resolve()),
                "asr_transcript_receipt_sha256": SMR.sha256_file(transcript_receipt_path),
                "sample_rate": 16000,
                "source_sample_rate": 16000,
                "source_start_frame": 1,
                "source_frame_count": identity_frames,
                "source_slice_pcm_sha256": pcm_sha,
                "staged_pcm_sha256": pcm_sha,
                "wav": str(staged.resolve()),
                "audio_sha256": SMR.sha256_file(staged),
            }
            candidate["cut_binding_sha256"] = SMR.candidate_cut_binding(candidate)
            candidate["clip_sha256"] = SMR._candidate_hash(candidate)
            digest, frames, _ = SAR._write_final_cut(
                candidate, final_wav, minimum_ratio=0.68
            )
            self.assertEqual(digest, candidate["audio_sha256"])
            self.assertEqual(frames, identity_frames)
            comparison = root / "comparison.npz"
            comparison.write_bytes(b"comparison")
            decision = {
                "schema": SAR.IDENTITY_SCHEMA,
                "status": "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "clip_id": "clip:1",
                "candidate_clip_sha256": candidate["clip_sha256"],
                "candidate_audio_sha256": candidate["audio_sha256"],
                "candidate_cut_binding_sha256": candidate["cut_binding_sha256"],
                "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
                "candidate_total_frames": identity_frames,
                "identity_tested_frame_count": identity_frames,
                "identity_excluded_tail_frames": 0,
                "full_candidate_identity_coverage": True,
                "comparison_matrix": str(comparison.resolve()),
                "comparison_matrix_sha256": SMR.sha256_file(comparison),
            }
            decision["identity_row_sha256"] = SAR._row_hash(decision, "identity_row_sha256")
            final = {
                "schema": SAR.FINAL_SCHEMA,
                "status": "VERIFIED_FINAL",
                "clip_id": "clip:1",
                "wav": str(final_wav.resolve()),
                "audio_sha256": candidate["audio_sha256"],
                "identity_row_sha256": decision["identity_row_sha256"],
                "candidate_clip_sha256": candidate["clip_sha256"],
                "cut_binding_sha256": candidate["cut_binding_sha256"],
            }
            final["final_row_sha256"] = SAR._row_hash(final, "final_row_sha256")
            self.assertEqual(
                SAR.retention_ready(
                    receipt,
                    [candidate],
                    {"clip:1": decision},
                    {"clip:1": final},
                    final_root=final_root,
                ),
                (True, []),
            )
            final_wav.write_bytes(b"corrupt")
            ready, reasons = SAR.retention_ready(
                receipt,
                [candidate],
                {"clip:1": decision},
                {"clip:1": final},
                final_root=final_root,
            )
            self.assertFalse(ready)
            self.assertIn("FINAL_WAV_HASH_INVALID:clip:1", reasons)

            with wave.open(str(staged), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(payload[2:] + b"\x02\x00")
            candidate["audio_sha256"] = SMR.sha256_file(staged)
            candidate["cut_binding_sha256"] = SMR.candidate_cut_binding(candidate)
            with self.assertRaisesRegex(SAR.IdentityRefused, "trailing or missing frames"):
                SAR._validate_candidate_cut_binding(candidate, minimum_ratio=0.68)

    def test_verified_24_second_candidate_cuts_three_lineage_preserving_windows(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.wav"
            staged = root / "candidate.wav"
            width = 8 * 16000
            payloads = [
                int(value).to_bytes(2, "little", signed=True) * width
                for value in (101, 202, 303)
            ]
            for path in (source, staged):
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(b"".join(payloads))
            source_receipt = root / "source.receipt.json"
            source_receipt.write_text(json.dumps({
                "schema": "speech-source-media-acquisition-v1",
                "wav": str(source.resolve()),
                "wav_sha256": SMR.sha256_file(source),
                "provider": "fixture", "media_id": "fixture:24s",
                "reference_url": "https://example.test/24s",
            }), encoding="utf-8")
            candidate = {
                "schema": SMR.CLIP_SCHEMA, "status": "CANDIDATE_AUDIT_REQUIRED",
                "clip_id": "clip:24s", "member": "Ada", "alignment_ratio": 0.99,
                "provider": "fixture", "media_id": "fixture:24s",
                "source_wav": str(source.resolve()),
                "source_audio_sha256": SMR.sha256_file(source),
                "source_receipt": str(source_receipt.resolve()),
                "source_receipt_sha256": SMR.sha256_file(source_receipt),
                "source_sample_rate": 16000, "sample_rate": 16000,
                "source_start_frame": 0, "source_frame_count": 3 * width,
                "source_slice_pcm_sha256": SMR.wav_pcm_slice_sha256(
                    source, 0, 3 * width),
                "staged_pcm_sha256": SMR.wav_pcm_slice_sha256(
                    staged, 0, 3 * width),
                "wav": str(staged.resolve()), "audio_sha256": SMR.sha256_file(staged),
            }
            candidate["cut_binding_sha256"] = SMR.candidate_cut_binding(candidate)
            candidate["clip_sha256"] = SMR._candidate_hash(candidate)
            run = {
                "start_window": 0, "end_window_exclusive": 3, "window_count": 3,
                "relative_start_frame": 0, "relative_frame_count": 3 * width,
                "source_start_frame": 0, "source_frame_count": 3 * width,
                "sample_rate": 16000, "all_windows_passed": True,
                "parent_candidate_clip_sha256": candidate["clip_sha256"],
                "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
            }
            run["identity_run_sha256"] = SAR._row_hash(run, "identity_run_sha256")
            decision = {
                "schema": SAR.IDENTITY_SCHEMA,
                "status": "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "clip_id": candidate["clip_id"], "claimed_member": "Ada",
                "candidate_clip_sha256": candidate["clip_sha256"],
                "candidate_audio_sha256": candidate["audio_sha256"],
                "candidate_cut_binding_sha256": candidate["cut_binding_sha256"],
                "calibration_sha256": "calibration",
                "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
                "unanimous_windows": True, "full_candidate_identity_coverage": True,
                "candidate_total_frames": 3 * width,
                "identity_tested_frame_count": 3 * width,
                "identity_excluded_tail_frames": 0,
                "windows": [
                    {"window_index": index, "passed": True, "winner": "Ada"}
                    for index in range(3)
                ],
                "accepted_runs": [run], "accepted_run_count": 1,
            }
            decision["identity_row_sha256"] = SAR._row_hash(
                decision, "identity_row_sha256")

            expected, errors = SAR.expected_secure_final_ids(
                [decision], {candidate["clip_id"]: candidate}, "calibration")
            units = SAR.accepted_identity_window_units(decision, candidate)
            self.assertEqual(errors, [])
            self.assertEqual(expected, {unit["clip_id"] for unit in units})
            self.assertEqual(len(units), 3)
            self.assertEqual(
                [unit["window_index"] for unit in units], [0, 1, 2])
            self.assertEqual(
                len({unit["lineage"]["identity_window_sha256"] for unit in units}), 3)

            final_rows = []
            for unit in units:
                target = root / ("final-%d.wav" % unit["window_index"])
                digest, frames, duration, pcm = SAR._write_final_window(
                    candidate, decision, run, unit["window_index"], target,
                    minimum_ratio=0.68,
                )
                self.assertEqual(frames, width)
                self.assertEqual(duration, 8.0)
                self.assertEqual(
                    pcm,
                    SMR.wav_pcm_slice_sha256(
                        source, unit["window_index"] * width, width),
                )
                final_rows.append({
                    "clip_id": unit["clip_id"], "parent_clip_id": candidate["clip_id"],
                    "source_family": "cnn", "audio_sha256": digest,
                    "source_audio_sha256": candidate["source_audio_sha256"],
                    "source_start_frame": unit["lineage"]["source_start_frame"],
                    "source_frame_count": width,
                    "source_slice_pcm_sha256": pcm,
                    "identity_run_sha256": run["identity_run_sha256"],
                    "identity_window_sha256": unit["lineage"]["identity_window_sha256"],
                    "final_row_sha256": str(unit["window_index"]) * 64,
                })
            candidate["source_family"] = "cnn"
            ledger, ledger_errors = SAR.cumulative_verified_source_ledger(
                [candidate], [decision], final_rows)
            self.assertEqual(ledger_errors, [])
            self.assertEqual(ledger["cnn"]["verified_final_clips"], 3)
            self.assertEqual(ledger["cnn"]["identity_windows"], 3)

            duplicate_pcm = dict(final_rows[2], audio_sha256=final_rows[0]["audio_sha256"])
            _ledger, duplicate_errors = SAR.cumulative_verified_source_ledger(
                [candidate], [decision], [*final_rows[:2], duplicate_pcm])
            self.assertTrue(any(
                "duplicate final audio" in row["reason"] for row in duplicate_errors))
            true_pcm_duplicate = dict(
                final_rows[2],
                source_slice_pcm_sha256=final_rows[0]["source_slice_pcm_sha256"],
            )
            _ledger, duplicate_errors = SAR.cumulative_verified_source_ledger(
                [candidate], [decision], [*final_rows[:2], true_pcm_duplicate])
            self.assertTrue(any(
                "duplicate final PCM" in row["reason"] for row in duplicate_errors))
            duplicate_frames = dict(
                final_rows[2], source_start_frame=final_rows[0]["source_start_frame"])
            _ledger, duplicate_errors = SAR.cumulative_verified_source_ledger(
                [candidate], [decision], [*final_rows[:2], duplicate_frames])
            self.assertTrue(any(
                "duplicate secured-source frame" in row["reason"]
                for row in duplicate_errors))

    def test_expected_secure_final_ids_rejects_run_outside_persisted_windows(self):
        width = int(SAR.CALIBRATION_WINDOW_SEC * 16000)
        candidate = {
            "clip_id": "clip:oob",
            "audio_sha256": "candidate-audio",
            "cut_binding_sha256": "candidate-cut",
            "alignment_ratio": 0.99,
            "source_start_frame": 7,
            "source_frame_count": 6 * width,
        }
        candidate["clip_sha256"] = SMR._candidate_hash(candidate)
        run = {
            "start_window": 3,
            "end_window_exclusive": 6,
            "window_count": 3,
            "all_windows_passed": True,
            "relative_start_frame": 3 * width,
            "relative_frame_count": 3 * width,
            "source_start_frame": 7 + 3 * width,
            "source_frame_count": 3 * width,
            "sample_rate": 16000,
            "parent_candidate_clip_sha256": candidate["clip_sha256"],
            "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
        }
        run["identity_run_sha256"] = SAR._row_hash(run, "identity_run_sha256")
        decision = {
            "schema": SAR.IDENTITY_SCHEMA,
            "status": "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "clip_id": candidate["clip_id"],
            "claimed_member": "Ada",
            "candidate_clip_sha256": candidate["clip_sha256"],
            "candidate_audio_sha256": candidate["audio_sha256"],
            "candidate_cut_binding_sha256": candidate["cut_binding_sha256"],
            "calibration_sha256": "calibration",
            "scoring_contract_sha256": SAR.identity_scoring_contract_sha256(),
            "windows": [
                {
                    "window_index": index,
                    "passed": True,
                    "winner": "Ada",
                }
                for index in range(3)
            ],
            "accepted_runs": [run],
            "accepted_run_count": 1,
        }
        decision["identity_row_sha256"] = SAR._row_hash(
            decision, "identity_row_sha256"
        )

        expected, errors = SAR.expected_secure_final_ids(
            [decision], {candidate["clip_id"]: candidate}, "calibration"
        )
        self.assertEqual(expected, set())
        self.assertTrue(
            any("outside the persisted decision windows" in row["reason"] for row in errors)
        )

    def test_candidate_provenance_does_not_use_program_or_airtime_for_identity(self):
        package = "https://transcripts.cnn.com/show/ctmo/date/2026-08-28/segment/01"
        discovery = {
            "status": "candidate_asr_required",
            "provider": "internet-archive-tvnews",
            "media_id": "ia:CNNW_20260828_100000_CNN_This_Morning",
            "media_url": "https://archive.org/download/example/example.mp4",
            "evidence": {
                "broadcast_title": "A different program and date",
                "utc_airtime": "1999-01-01T00:00:00+00:00",
            },
        }
        record = {
            "package": package,
            "package_id": SMR.package_id(package),
            "source_family": "cnn",
            "date": "2026-08-28",
            "discovery": discovery,
        }
        candidate = {
            "package": package,
            "provider": discovery["provider"],
            "media_id": discovery["media_id"],
        }
        result = SAR.validate_candidate_provenance_binding(candidate, {package: record})
        self.assertTrue(result["candidate_provenance_pass"])
        self.assertFalse(result["program_airtime_used_for_identity"])

        absent = dict(candidate, media_id="ia:not-in-current-discovery")
        with self.assertRaisesRegex(SAR.IdentityRefused, "provenance"):
            SAR.validate_candidate_provenance_binding(absent, {package: record})

        wrong_provider = dict(candidate, provider="youtube")
        with self.assertRaisesRegex(SAR.IdentityRefused, "provenance"):
            SAR.validate_candidate_provenance_binding(wrong_provider, {package: record})

        # A later discovery refresh may supersede the media selection.  The
        # exact current inventory acquisition remains valid provenance when
        # provider, media ID, WAV, source hash, and reference all still bind.
        acquired_wav = (Path.cwd() / "secured-acquisition.wav").resolve()
        acquired_record = dict(record)
        acquired_record["discovery"] = {
            "status": "candidate_asr_required",
            "provider": "youtube",
            "media_id": "youtube:newer-selection",
            "media_url": "https://example.com/newer",
        }
        acquired_record["acquisition"] = {
            "status": "acquired",
            "items": [{
                "provider": discovery["provider"],
                "media_id": discovery["media_id"],
                "wav": str(acquired_wav),
                "wav_sha256": "a" * 64,
                "reference_url": "https://archive.org/details/example",
                "archive_source": {"access": "public-bounded-tv-clips"},
            }],
        }
        acquired_candidate = {
            **candidate,
            "source_wav": str(acquired_wav),
            "source_audio_sha256": "a" * 64,
        }
        acquired_binding = SAR.validate_candidate_provenance_binding(
            acquired_candidate, {package: acquired_record}
        )
        self.assertEqual(
            acquired_binding["provenance_kind"], "current_secured_acquisition"
        )
        acquired_candidate["source_audio_sha256"] = "b" * 64
        with self.assertRaisesRegex(SAR.IdentityRefused, "secured acquisition"):
            SAR.validate_candidate_provenance_binding(
                acquired_candidate, {package: acquired_record}
            )

        video_id = "abcdefghijk"
        youtube = {
            "status": "candidate_asr_required",
            "provider": "youtube",
            "media_id": f"youtube:{video_id}",
            "media_url": f"https://www.youtube.com/watch?v={video_id}",
            "evidence": {
                "package_url": package,
                "package_id": SMR.package_id(package),
                "broadcast_date": "2026-08-28",
                "official_channel_id": "UCupvZG-5ko_eiXAupbDfxWw",
                "transcript_airtime_utc": "2026-08-28T10:30:00+00:00",
                "candidate_broadcast_airtime_utc": "2026-08-27T03:14:00+00:00",
                "video_timestamp": dt.datetime(
                    2026, 8, 28, 12, 0, tzinfo=dt.timezone.utc
                ).timestamp(),
                "expected_program_label": "CNN This Morning",
                "search_result_title": "CNN This Morning interview",
                "video_id": video_id,
            },
        }
        youtube_record = {
            "package": package,
            "package_id": SMR.package_id(package),
            "source_family": "cnn",
            "date": "2026-08-28",
            "discovery": {"status": "candidate_asr_required", "candidates": [youtube]},
        }
        youtube_candidate = {
            "package": package,
            "provider": "youtube",
            "media_id": f"youtube:{video_id}",
        }
        self.assertTrue(
            SAR.validate_candidate_provenance_binding(
                youtube_candidate, {package: youtube_record}
            )["candidate_provenance_pass"]
        )
        youtube["evidence"].pop("candidate_broadcast_airtime_utc")
        self.assertTrue(
            SAR.validate_candidate_provenance_binding(
                youtube_candidate, {package: youtube_record}
            )["candidate_provenance_pass"]
        )


if __name__ == "__main__":
    unittest.main()
