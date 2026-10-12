from __future__ import annotations

import json
from pathlib import Path
import shutil
import copy
import sys
import tempfile
import unittest
import wave

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from speech import build_descriptive_speech as S  # noqa: E402
from speech import build_canonical_source_lineage as CSL  # noqa: E402


def clip(member, setting, value, sid="episode", seg=0):
    return {
        "member": member, "setting": setting, "outlet": "TEST", "show": setting,
        "sid": sid, "seg": seg, "duration_seconds": 60.0,
        "identity": {"tier": "turn_bound"},
        "measures": {channel: float(value) for channel in S.CHANNELS},
    }


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def make_wav(path, frames=384000):
    width = 8 * 16000
    payload = b""
    remaining = frames
    window = 0
    while remaining:
        block = min(width, remaining)
        payload += int(window + 1).to_bytes(2, "little", signed=True) * block
        remaining -= block
        window += 1
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(payload)


def verified_fixture(directory):
    directory = Path(directory)
    source = directory / "source.wav"
    candidate_wav = directory / "candidate.wav"
    final_wav = directory / "final.wav"
    make_wav(source)
    shutil.copyfile(source, candidate_wav)
    shutil.copyfile(source, final_wav)
    reference = "https://example.test/recording"
    package = "https://example.test/transcript"
    source_receipt_path = directory / "source.json"
    source_digest = S.sha256_file(source)
    write_json(source_receipt_path, {
        "schema": "speech-source-media-acquisition-v1", "wav": str(source),
        "wav_sha256": source_digest, "provider": "fixture", "media_id": "fixture:1",
        "reference_url": reference,
    })
    transcript = directory / "transcript.words.jsonl"
    write_jsonl(transcript, [{"start": 0.0, "end": 1.0, "word": "test"}])
    transcript_digest = S.sha256_file(transcript)
    transcript_receipt_path = directory / "transcript.receipt.json"
    write_json(transcript_receipt_path, {
        "schema": "speech-source-asr-transcript-receipt-v1", "status": "PASS",
        "speaker_identity_authority": False, "transcript_sha256": transcript_digest,
        "source_audio_sha256": source_digest, "reference_url": reference,
    })
    source_pcm = S._pcm_sha256(source, 0, 384000)
    candidate = {
        "schema": S.CANDIDATE_SCHEMA, "status": "CANDIDATE_AUDIT_REQUIRED",
        "clip_id": "candidate:1", "member": "Member One", "source_family": "abc-this-week",
        "show": "this-week", "package": package, "turn_id": "sqlite:speeches:1",
        "text_sha256": "1" * 64, "provider": "fixture", "media_id": "fixture:1",
        "reference_url": reference, "source_wav": str(source),
        "source_audio_sha256": source_digest, "source_receipt": str(source_receipt_path),
        "source_receipt_sha256": S.sha256_file(source_receipt_path),
        "asr_transcript": str(transcript), "asr_transcript_sha256": transcript_digest,
        "asr_transcript_receipt": str(transcript_receipt_path),
        "asr_transcript_receipt_sha256": S.sha256_file(transcript_receipt_path),
        "source_sample_rate": 16000, "sample_rate": 16000, "source_start_frame": 0,
        "source_frame_count": 384000, "source_slice_pcm_sha256": source_pcm,
        "staged_pcm_sha256": source_pcm, "wav": str(candidate_wav),
        "audio_sha256": S.sha256_file(candidate_wav), "alignment_ratio": 0.99,
        "words": 50, "features": {"f0_mean_semitone": 999.0},
    }
    candidate["cut_binding_sha256"] = S._candidate_cut_binding(candidate)
    candidate["clip_sha256"] = S._row_field_sha256(candidate, "clip_sha256")
    candidate_manifest = directory / "candidates.jsonl"
    write_jsonl(candidate_manifest, [candidate])
    inventory_row = {"package": package, "package_id": "pkg:fixture"}
    inventory = directory / "inventory.jsonl"
    write_jsonl(inventory, [inventory_row])
    candidate_receipt = directory / "candidates.receipt.json"
    write_json(candidate_receipt, {
        "schema": S.CANDIDATE_RECEIPT_SCHEMA, "status": "AUDIT_REQUIRED",
        "manifest_sha256": S.sha256_file(candidate_manifest), "candidate_clips": 1,
        "inventory_sha256": S.sha256_file(inventory),
        "exact_frame_cut_binding_required": True,
    })
    scoring_contract = {"schema": "fixture-scoring-contract", "all_speakers": True}
    scoring_sha = S._stable_sha256(scoring_contract)
    calibration = directory / "calibration.json"
    write_json(calibration, {
        "schema": S.CALIBRATION_SCHEMA, "status": "PASS_ZERO_FALSE_ACCEPT_CALIBRATION",
        "all_vaulted_speakers_compared": True, "event_holdout_enforced": True,
        "serialized_thresholds_rechecked": True, "probability_claim": False,
        "false_accepts": 0, "empirical_precision": 1.0,
        "minimum_unanimous_windows": 3, "vaulted_speakers": 1,
        "minimum_target_best_score": 0.5, "minimum_best_anchor_margin": 0.2,
        "scoring_contract": scoring_contract, "scoring_contract_sha256": scoring_sha,
    })
    comparison = directory / "comparison.npz"
    comparison.write_bytes(b"fixture-comparison")
    decision = {
        "schema": S.IDENTITY_SCHEMA, "status": "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        "clip_id": candidate["clip_id"], "claimed_member": candidate["member"],
        "candidate_clip_sha256": candidate["clip_sha256"],
        "candidate_audio_sha256": candidate["audio_sha256"],
        "candidate_cut_binding_sha256": candidate["cut_binding_sha256"],
        "calibration_sha256": S.sha256_file(calibration),
        "scoring_contract_sha256": scoring_sha, "all_vaulted_speakers_compared": True,
        "probability_claim": False, "unanimous_windows": True, "candidate_windows": 3,
        "candidate_total_frames": 384000, "identity_tested_frame_count": 384000,
        "identity_excluded_tail_frames": 0, "full_candidate_identity_coverage": True,
        "vaulted_speakers_compared": 1, "minimum_target_best_score": 0.5,
        "minimum_best_anchor_margin": 0.2,
        "windows": [{"window_index": index, "passed": True, "winner": "Member One"}
                    for index in range(3)],
        "comparison_matrix": str(comparison),
        "comparison_matrix_sha256": S.sha256_file(comparison), "accepted_runs": [],
    }
    decision["identity_row_sha256"] = S._row_field_sha256(decision, "identity_row_sha256")
    identity_manifest = directory / "identity.jsonl"
    write_jsonl(identity_manifest, [decision])
    identity_receipt = directory / "identity.receipt.json"
    write_json(identity_receipt, {
        "schema": S.IDENTITY_RECEIPT_SCHEMA, "status": "PASS",
        "identity_schema": S.IDENTITY_SCHEMA, "manifest_sha256": S.sha256_file(identity_manifest),
        "decisions": 1, "candidate_manifest_sha256": S.sha256_file(candidate_manifest),
        "candidate_receipt_sha256": S.sha256_file(candidate_receipt),
        "inventory_sha256": S.sha256_file(inventory),
        "calibration_sha256": S.sha256_file(calibration),
        "scoring_contract_sha256": scoring_sha, "exact_frame_cut_binding_required": True,
        "all_anchor_comparisons_persisted": True, "minimum_alignment_ratio": 0.68,
    })
    final = {
        "schema": S.VERIFIED_FINAL_SCHEMA, "status": "VERIFIED_FINAL",
        "clip_id": candidate["clip_id"], "parent_clip_id": candidate["clip_id"],
        "member": candidate["member"], "source_family": candidate["source_family"],
        "show": candidate["show"], "date": "2024-01-01", "subject": "economy",
        "setting": "interview", "package": package, "reference_url": reference,
        "turn_id": candidate["turn_id"], "text_sha256": candidate["text_sha256"],
        "source_audio_sha256": source_digest,
        "candidate_clip_sha256": candidate["clip_sha256"],
        "cut_binding_sha256": candidate["cut_binding_sha256"],
        "source_start_frame": 0, "source_frame_count": 384000,
        "source_slice_pcm_sha256": source_pcm, "transcript_sha256": transcript_digest,
        "identity_row_sha256": decision["identity_row_sha256"], "identity_run_sha256": None,
        "identity_comparison": str(comparison),
        "identity_comparison_sha256": S.sha256_file(comparison),
        "identity_contract": "100_PERCENT_STRUCTURAL_CONSENSUS_NOT_PROBABILITY",
        "calibration_sha256": S.sha256_file(calibration),
        "scoring_contract_sha256": scoring_sha, "candidate_provenance_pass": True,
        "program_airtime_used_for_identity": False, "inventory_package_id": "pkg:fixture",
        "inventory_row_sha256": S._stable_sha256(inventory_row), "alignment_ratio": 0.99,
        "start_sec": 0.0, "end_sec": 24.0, "duration_sec": 24.0,
        "frames": 384000, "sample_rate": 16000, "wav": str(final_wav),
        "audio_sha256": S.sha256_file(final_wav), "features": {"f0_mean_semitone": 999.0},
    }
    final["final_row_sha256"] = S._row_field_sha256(final, "final_row_sha256")
    final_manifest = directory / "final.jsonl"
    write_jsonl(final_manifest, [final])
    by_source = S._verified_source_ledger([candidate], [decision], [final])
    final_receipt = directory / "final.receipt.json"
    write_json(final_receipt, {
        "schema": S.VERIFIED_FINAL_RECEIPT_SCHEMA, "status": "PASS",
        "final_schema": S.VERIFIED_FINAL_SCHEMA, "identity_schema": S.IDENTITY_SCHEMA,
        "manifest_sha256": S.sha256_file(final_manifest), "verified_final_clips": 1,
        "expected_final_clips": 1,
        "expected_final_ids_sha256": S._stable_sha256([candidate["clip_id"]]),
        "final_id_set_complete": True,
        "cumulative_source_bindings_complete": True, "by_source": by_source,
        "identity_manifest_sha256": S.sha256_file(identity_manifest),
        "identity_receipt_sha256": S.sha256_file(identity_receipt),
        "candidate_manifest_sha256": S.sha256_file(candidate_manifest),
        "candidate_receipt_sha256": S.sha256_file(candidate_receipt),
        "inventory_sha256": S.sha256_file(inventory),
        "calibration_sha256": S.sha256_file(calibration),
        "scoring_contract_sha256": scoring_sha, "reference_url_required": True,
        "minimum_alignment_ratio": 0.68,
        "whole_final_audio_equals_identity_candidate_required": False,
        "partial_final_audio_equals_identity_tested_run_required": False,
        "final_window_audio_equals_identity_tested_window_required": True,
        "independent_identity_window_finalization": True,
        "final_audio_matches_source_frame_binding_required": True,
        "canonical_source_overlap_requires_record_supersession": True,
        "source_audio_cleanup_authorized": False, "errors": [],
    })
    return {
        "manifest": final_manifest, "receipt": final_receipt,
        "identity_manifest": identity_manifest, "identity_receipt": identity_receipt,
        "candidate_manifest": candidate_manifest, "candidate_receipt": candidate_receipt,
        "inventory": inventory, "calibration": calibration, "source": source,
    }


class KeysAndIdentity(unittest.TestCase):
    def test_wav_contract_allows_only_observed_legacy_duration_rounding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.wav"
            frames = 11888  # 0.743 s: empirical accepted-manifest maximum
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\0\0" * frames)
            row = {
                "out_wav": str(path),
                "frames": frames,
                "dur": 0.0,
                "start": 10.0,
                "end": 10.0,
                "sha256": S.sha256_file(path),
            }
            self.assertEqual(S.wav_contract(row, True), path)
            row["dur"] = 1.5
            row["end"] = 11.5
            with self.assertRaises(S.SpeechBuildError):
                S.wav_contract(row, False)

    def test_wj_feature_key_uses_member_turn_start(self):
        manifest = {"sid": "1", "member": "A", "start": 29.0}
        feature = {"sid": "1", "member": "A", "member_start_s": 29.0}
        self.assertEqual(S.feature_key(manifest, "CSPAN"),
                         S.feature_key(feature, "CSPAN"))

    def test_npr_requires_exact_fetch_log_member_package(self):
        row = {"outlet": "NPR", "sid": "n1", "member": "Member"}
        identity = S.classify_identity(
            row, {"n1": {"member": "Member", "pkg": "https://example/p"}}, {})
        self.assertEqual(identity["tier"], "package_aligned")
        with self.assertRaises(S.SpeechBuildError):
            S.classify_identity(row, {"n1": {"member": "Other", "pkg": "x"}}, {})

    def test_exact_turn_binding_takes_priority(self):
        row = {"outlet": "CSPAN", "turn_id": "t", "transcript_path": "p",
               "text_sha256": "a", "source_audio_sha256": "b"}
        self.assertEqual(S.classify_identity(row, {}, {})["tier"], "turn_bound")

    def test_local_video_requires_exact_write_receipt_binding(self):
        out_wav = str(Path("verified.wav").resolve())
        row = {
            "outlet": "CSPAN", "sid": "video1", "member": "Member",
            "out_wav": out_wav, "start": 12.0, "end": 20.0,
            "sha256": "a" * 64,
            "alignment_method": "in-band-full-name+asr-turn-interior+ecapa-rejection-only",
        }
        key = (out_wav, "Member", "video1", 12.0, 20.0, "a" * 64)
        binding = {
            key: {
                "receipt_sha256": "b" * 64,
                "verification_row_sha256": "c" * 64,
                "identity_evidence": "Representative Member",
                "identity_evidence_basis": "literal-in-band-full-name",
                "ecapa_role": "rejection-only-for-cold-identity",
            }
        }
        identity = S.classify_identity(
            row, {}, {}, local_youtube_bindings=binding
        )
        self.assertEqual(identity["tier"], "turn_bound_receipt_verified")
        self.assertEqual(S.setting_family(row), "broadcast_news_interview")
        # AUDIOA-1: the only producer of an in-band C-SPAN interview row
        # (verify_local_youtube_exact_windows.py) writes this alias-vocabulary spelling,
        # not the bare full-name spelling above; setting_family() must accept both.
        producer_row = dict(row, alignment_method=(
            "in-band-approved-first+surname+asr-turn-interior+ecapa-rejection-only"
        ))
        self.assertEqual(S.setting_family(producer_row), "broadcast_news_interview")
        with self.assertRaises(S.SpeechBuildError):
            S.classify_identity(row, {}, {})

    def test_sunday_discovered_speaker_requires_transcript_feature_binding(self):
        row = {"outlet": "SUNDAY", "sid": "sotu_2022-01-30", "member": "Pete Sessions"}
        bindings = {"sotu_2022-01-30": {"members": ["Robert Menendez"], "url": "episode"}}
        feature = {"row": {"sid": "sotu_2022-01-30", "member": "Pete Sessions"}}
        identity = S.classify_identity(row, {}, bindings, feature)
        self.assertEqual(identity["member_binding"], "transcript_feature")
        with self.assertRaises(S.SpeechBuildError):
            S.classify_identity(row, {}, bindings, None)


class AggregationContracts(unittest.TestCase):
    def test_floor_audio_is_not_in_interview_headline(self):
        rows = [clip("A", "npr", 1.0), clip("A", "house_floor", 9.0, "floor")]
        member = S.aggregate_members(rows)["A"]
        self.assertEqual(member["coverage"]["clips"], 2)
        self.assertEqual(member["headline_interviews_only"]["clips"], 1)
        self.assertEqual(member["headline_interviews_only"]["pitch_var_mean"], 1.0)
        self.assertIn("house_floor", member["by_setting"])

    def test_setting_effects_use_matched_member_means(self):
        rows = []
        for index in range(5):
            name = "M%d" % index
            rows.extend([
                clip(name, "npr", 3.0, "npr1", 1),
                clip(name, "npr", 3.0, "npr2", 2),
                clip(name, "sunday_show", 1.0, "sun1", 1),
                clip(name, "sunday_show", 1.0, "sun2", 2),
            ])
        effects = S.global_setting_effects(S.aggregate_members(rows))
        comparison = effects["channels"]["pitch_var"][0]
        self.assertEqual(comparison["n_members"], 5)
        self.assertEqual(comparison["mean_delta_first_minus_second"], 2.0)

    def test_subject_effects_prefer_padded_and_drop_suspect(self):
        topics = [
            {"member": "A", "program": "1", "member_offset": 10,
             "topic": "TAX", "identity_status": "VERIFIED", "excluded_from_profile": False},
            {"member": "A", "program": "1", "member_offset": 20,
             "topic": "TAX", "identity_status": "UNRESOLVED", "excluded_from_profile": False},
            {"member": "A", "program": "1", "member_offset": 30,
             "topic": "TAX", "identity_status": "SUSPECT", "excluded_from_profile": True},
        ]
        reactions = [
            {"member": "A", "pid": "1", "member_offset": 10, "pad_family": "unpadded_v1",
             "clip_dur_s": 20, "wav": "old", "f0_delta": 99, "loud_delta": 99},
            {"member": "A", "pid": "1", "member_offset": 10, "pad_family": "padded_v2",
             "clip_dur_s": 30, "wav": "new", "f0_delta": 2, "loud_delta": 0.2},
            {"member": "A", "pid": "1", "member_offset": 20, "pad_family": "padded_v2",
             "clip_dur_s": 30, "wav": "b", "f0_delta": 4, "loud_delta": 0.4},
            {"member": "A", "pid": "1", "member_offset": 30, "pad_family": "padded_v2",
             "clip_dur_s": 30, "wav": "bad", "f0_delta": 100, "loud_delta": 1},
        ]
        members = {"A": {"subject_effects": [], "caveats": []}}
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            topic_path = directory / "topics.jsonl"
            reaction_path = directory / "reactions.jsonl"
            write_jsonl(topic_path, topics)
            write_jsonl(reaction_path, reactions)
            result = S.subject_effects(reaction_path, topic_path, members)
        self.assertEqual(result["reaction_rows"], 3)
        self.assertEqual(result["suspect_excluded"], 1)
        self.assertEqual(result["joined_labeled_rows"], 2)
        self.assertEqual(members["A"]["subject_effects"][0]["mean_f0_delta_semitones"], 3.0)

    def test_subject_effects_fail_closed_on_duplicate_legacy_offset(self):
        topics = [
            {"member": "A", "program": "1", "member_offset": 10,
             "topic": "TAX", "identity_status": "VERIFIED", "excluded_from_profile": False},
            {"member": "A", "program": "1", "member_offset": 10,
             "topic": "DEFENSE", "identity_status": "VERIFIED", "excluded_from_profile": False},
        ]
        reactions = [
            {"member": "A", "pid": "1", "member_offset": 10, "pad_family": "padded_v2",
             "clip_dur_s": 30, "wav": "ambiguous", "f0_delta": 2, "loud_delta": 0.2},
        ]
        members = {"A": {"subject_effects": [], "caveats": []}}
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            topic_path = directory / "topics.jsonl"
            reaction_path = directory / "reactions.jsonl"
            write_jsonl(topic_path, topics)
            write_jsonl(reaction_path, reactions)
            result = S.subject_effects(reaction_path, topic_path, members)
        self.assertEqual(result["duplicate_topic_keys"], 1)
        self.assertEqual(result["ambiguous_topic_join"], 1)
        self.assertEqual(result["joined_labeled_rows"], 0)
        self.assertEqual(members["A"]["subject_effects"], [])


class VerifiedFinalContracts(unittest.TestCase):
    def validate(self, fixture, measure):
        return S.validate_verified_final_contract(
            fixture["manifest"], fixture["receipt"], fixture["identity_manifest"],
            fixture["identity_receipt"], fixture["candidate_manifest"],
            fixture["candidate_receipt"], fixture["inventory"], fixture["calibration"],
            {"Member One"}, measure=measure,
        )

    def test_verified_final_recomputes_features_and_retains_context(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            records, meta = self.validate(
                fixture,
                lambda path, _holder: calls.append(path) or {
                    channel: 42.0 for channel in S.CHANNELS
                },
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(records[0]["measures"]["f0_mean"], 42.0)
        self.assertEqual(records[0]["measurement_source"],
                         "verified_final_opensmile_egemaps_v02_recomputed")
        self.assertFalse(records[0]["feature_provenance"]["parent_candidate_features_trusted"])
        self.assertEqual(records[0]["setting"], "sunday_show")
        self.assertEqual(records[0]["subject"], "economy")
        self.assertEqual(records[0]["reference_url"], "https://example.test/recording")
        self.assertTrue(meta["source_pcm_reverified"])

    def test_verified_three_window_candidate_becomes_three_descriptive_units(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            candidate = json.loads(fixture["candidate_manifest"].read_text(
                encoding="utf-8").strip())
            decision = json.loads(fixture["identity_manifest"].read_text(
                encoding="utf-8").strip())
            width = 8 * 16000
            run = {
                "start_window": 0, "end_window_exclusive": 3, "window_count": 3,
                "relative_start_frame": 0, "relative_frame_count": 3 * width,
                "source_start_frame": 0, "source_frame_count": 3 * width,
                "sample_rate": 16000, "all_windows_passed": True,
                "parent_candidate_clip_sha256": candidate["clip_sha256"],
                "scoring_contract_sha256": decision["scoring_contract_sha256"],
            }
            run["identity_run_sha256"] = S._row_field_sha256(
                run, "identity_run_sha256")
            decision["accepted_runs"] = [run]
            decision["accepted_run_count"] = 1
            decision["identity_row_sha256"] = S._row_field_sha256(
                decision, "identity_row_sha256")
            write_jsonl(fixture["identity_manifest"], [decision])
            identity_receipt = json.loads(fixture["identity_receipt"].read_text(
                encoding="utf-8"))
            identity_receipt["manifest_sha256"] = S.sha256_file(
                fixture["identity_manifest"])
            write_json(fixture["identity_receipt"], identity_receipt)

            base_final = json.loads(fixture["manifest"].read_text(
                encoding="utf-8").strip())
            finals = []
            for index in range(3):
                lineage = S._identity_window_lineage(
                    candidate, decision, run, index)
                final_wav = Path(directory) / ("final-window-%d.wav" % index)
                with wave.open(str(fixture["source"]), "rb") as source_handle:
                    source_handle.setpos(index * width)
                    payload = source_handle.readframes(width)
                with wave.open(str(final_wav), "wb") as target_handle:
                    target_handle.setnchannels(1)
                    target_handle.setsampwidth(2)
                    target_handle.setframerate(16000)
                    target_handle.writeframes(payload)
                final = copy.deepcopy(base_final)
                final.update({
                    "clip_id": S._identity_window_clip_id(
                        candidate["clip_id"], run, index),
                    "identity_row_sha256": decision["identity_row_sha256"],
                    "identity_run_sha256": run["identity_run_sha256"],
                    "identity_window_index": index,
                    "identity_window_sha256": lineage["identity_window_sha256"],
                    "identity_qualifying_run_start_window": 0,
                    "identity_qualifying_run_end_window_exclusive": 3,
                    "source_start_frame": index * width,
                    "source_frame_count": width,
                    "source_slice_pcm_sha256": S._pcm_sha256(
                        fixture["source"], index * width, width),
                    "start_sec": float(index * 8),
                    "end_sec": float((index + 1) * 8),
                    "duration_sec": 8.0, "frames": width,
                    "wav": str(final_wav),
                    "audio_sha256": S.sha256_file(final_wav),
                    "features": None,
                })
                final["final_row_sha256"] = S._row_field_sha256(
                    final, "final_row_sha256")
                finals.append(final)
            write_jsonl(fixture["manifest"], finals)
            receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
            expected_ids = sorted(final["clip_id"] for final in finals)
            receipt.update({
                "manifest_sha256": S.sha256_file(fixture["manifest"]),
                "verified_final_clips": 3, "expected_final_clips": 3,
                "expected_final_ids_sha256": S._stable_sha256(expected_ids),
                "identity_manifest_sha256": S.sha256_file(fixture["identity_manifest"]),
                "identity_receipt_sha256": S.sha256_file(fixture["identity_receipt"]),
                "independent_identity_window_finalization": True,
                "by_source": S._verified_source_ledger(
                    [candidate], [decision], finals),
            })
            write_json(fixture["receipt"], receipt)
            records, meta = self.validate(
                fixture,
                lambda path, _holder: calls.append(path) or {
                    channel: 42.0 for channel in S.CHANNELS
                },
            )
        self.assertEqual(len(records), 3)
        self.assertEqual(len(calls), 3)
        self.assertEqual([row["duration_seconds"] for row in records], [8.0] * 3)
        self.assertEqual(
            [row["identity"]["identity_window_index"] for row in records],
            [0, 1, 2],
        )
        self.assertEqual(meta["accepted_clips"], 3)

    def test_verified_final_rejects_source_pcm_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            with fixture["source"].open("r+b") as handle:
                handle.seek(44)
                handle.write(b"\xff\xff")
            with self.assertRaises(S.SpeechBuildError):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })

    def test_verified_final_rejects_stale_scoring_calibration_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            calibration = json.loads(fixture["calibration"].read_text(encoding="utf-8"))
            calibration["scoring_contract"]["all_speakers"] = False
            write_json(fixture["calibration"], calibration)
            with self.assertRaises(S.SpeechBuildError):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })

    def test_verified_final_rejects_rehashed_manifest_missing_accepted_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            fixture["manifest"].write_text("", encoding="utf-8")
            receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
            receipt["manifest_sha256"] = S.sha256_file(fixture["manifest"])
            receipt["verified_final_clips"] = 0
            # Even a self-consistent but dishonest completeness claim must lose
            # to the independently derived accepted-identity set.
            receipt["expected_final_clips"] = 0
            receipt["expected_final_ids_sha256"] = S._stable_sha256([])
            receipt["final_id_set_complete"] = True
            write_json(fixture["receipt"], receipt)
            with self.assertRaises(S.SpeechBuildError):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })

    def test_verified_final_rejects_rehashed_out_of_bounds_partial_run(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            decision = json.loads(fixture["identity_manifest"].read_text(
                encoding="utf-8").strip())
            run = {
                "start_window": 5, "end_window_exclusive": 8, "window_count": 3,
                "relative_start_frame": 5 * 8 * 16000,
                "relative_frame_count": 3 * 8 * 16000,
                "source_start_frame": 5 * 8 * 16000,
                "source_frame_count": 3 * 8 * 16000, "sample_rate": 16000,
                "all_windows_passed": True,
                "parent_candidate_clip_sha256": decision["candidate_clip_sha256"],
                "scoring_contract_sha256": decision["scoring_contract_sha256"],
            }
            run["identity_run_sha256"] = S._row_field_sha256(
                run, "identity_run_sha256")
            decision["status"] = "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
            decision["unanimous_windows"] = False
            decision["accepted_runs"] = [run]
            decision["accepted_run_count"] = 1
            decision["identity_row_sha256"] = S._row_field_sha256(
                decision, "identity_row_sha256")
            write_jsonl(fixture["identity_manifest"], [decision])
            identity_receipt = json.loads(fixture["identity_receipt"].read_text(
                encoding="utf-8"))
            identity_receipt["manifest_sha256"] = S.sha256_file(
                fixture["identity_manifest"])
            write_json(fixture["identity_receipt"], identity_receipt)

            final = json.loads(fixture["manifest"].read_text(encoding="utf-8").strip())
            final_id = "%s:run:5-8" % decision["clip_id"]
            final["clip_id"] = final_id
            final["identity_row_sha256"] = decision["identity_row_sha256"]
            final["identity_run_sha256"] = run["identity_run_sha256"]
            final["final_row_sha256"] = S._row_field_sha256(final, "final_row_sha256")
            write_jsonl(fixture["manifest"], [final])
            final_receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
            final_receipt["manifest_sha256"] = S.sha256_file(fixture["manifest"])
            final_receipt["identity_manifest_sha256"] = S.sha256_file(
                fixture["identity_manifest"])
            final_receipt["identity_receipt_sha256"] = S.sha256_file(
                fixture["identity_receipt"])
            final_receipt["expected_final_clips"] = 1
            final_receipt["expected_final_ids_sha256"] = S._stable_sha256([final_id])
            write_json(fixture["receipt"], final_receipt)
            with self.assertRaisesRegex(
                    S.SpeechBuildError, "partial accepted identity run geometry is invalid"):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })

    def test_verified_final_rejects_fully_rehashed_low_alignment_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            candidate = json.loads(fixture["candidate_manifest"].read_text(
                encoding="utf-8").strip())
            candidate["alignment_ratio"] = 0.2
            candidate["clip_sha256"] = S._row_field_sha256(candidate, "clip_sha256")
            write_jsonl(fixture["candidate_manifest"], [candidate])
            candidate_receipt = json.loads(fixture["candidate_receipt"].read_text(
                encoding="utf-8"))
            candidate_receipt["manifest_sha256"] = S.sha256_file(
                fixture["candidate_manifest"])
            write_json(fixture["candidate_receipt"], candidate_receipt)

            decision = json.loads(fixture["identity_manifest"].read_text(
                encoding="utf-8").strip())
            decision["candidate_clip_sha256"] = candidate["clip_sha256"]
            decision["identity_row_sha256"] = S._row_field_sha256(
                decision, "identity_row_sha256")
            write_jsonl(fixture["identity_manifest"], [decision])
            identity_receipt = json.loads(fixture["identity_receipt"].read_text(
                encoding="utf-8"))
            identity_receipt["manifest_sha256"] = S.sha256_file(
                fixture["identity_manifest"])
            identity_receipt["candidate_manifest_sha256"] = S.sha256_file(
                fixture["candidate_manifest"])
            identity_receipt["candidate_receipt_sha256"] = S.sha256_file(
                fixture["candidate_receipt"])
            write_json(fixture["identity_receipt"], identity_receipt)

            final = json.loads(fixture["manifest"].read_text(encoding="utf-8").strip())
            final["candidate_clip_sha256"] = candidate["clip_sha256"]
            final["identity_row_sha256"] = decision["identity_row_sha256"]
            final["alignment_ratio"] = 0.2
            final["final_row_sha256"] = S._row_field_sha256(final, "final_row_sha256")
            write_jsonl(fixture["manifest"], [final])
            final_receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
            final_receipt["manifest_sha256"] = S.sha256_file(fixture["manifest"])
            final_receipt["candidate_manifest_sha256"] = S.sha256_file(
                fixture["candidate_manifest"])
            final_receipt["candidate_receipt_sha256"] = S.sha256_file(
                fixture["candidate_receipt"])
            final_receipt["identity_manifest_sha256"] = S.sha256_file(
                fixture["identity_manifest"])
            final_receipt["identity_receipt_sha256"] = S.sha256_file(
                fixture["identity_receipt"])
            final_receipt["by_source"] = S._verified_source_ledger(
                [candidate], [decision], [final])
            write_json(fixture["receipt"], final_receipt)
            with self.assertRaisesRegex(
                    S.SpeechBuildError, "lexical alignment is below"):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })

    def test_verified_final_rejects_final_receipt_alignment_policy_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
            receipt["minimum_alignment_ratio"] = 0.69
            write_json(fixture["receipt"], receipt)
            with self.assertRaisesRegex(
                    S.SpeechBuildError, "verified final receipt is stale or incomplete"):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })

    def test_cumulative_source_ledger_preserves_prior_source_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            candidate = json.loads(fixture["candidate_manifest"].read_text(
                encoding="utf-8").strip())
            decision = json.loads(fixture["identity_manifest"].read_text(
                encoding="utf-8").strip())
            final = json.loads(fixture["manifest"].read_text(encoding="utf-8").strip())
            abc = S._verified_source_ledger([candidate], [decision], [final])["abc-this-week"]
            fox_candidate = copy.deepcopy(candidate)
            fox_candidate.update({
                "clip_id": "candidate:fox", "source_family": "fox",
                "audio_sha256": "2" * 64, "clip_sha256": "3" * 64,
            })
            fox_decision = copy.deepcopy(decision)
            fox_decision.update({
                "clip_id": "candidate:fox", "candidate_clip_sha256": "3" * 64,
                "candidate_audio_sha256": "2" * 64, "identity_row_sha256": "4" * 64,
            })
            fox_final = copy.deepcopy(final)
            fox_final.update({
                "clip_id": "candidate:fox", "parent_clip_id": "candidate:fox",
                "source_family": "fox", "candidate_clip_sha256": "3" * 64,
                "identity_row_sha256": "4" * 64, "audio_sha256": "5" * 64,
                "source_audio_sha256": "6" * 64,
                "source_slice_pcm_sha256": "8" * 64,
                "final_row_sha256": "7" * 64,
            })
            cumulative = S._verified_source_ledger(
                [candidate, fox_candidate], [decision, fox_decision], [final, fox_final])
        self.assertEqual(cumulative["abc-this-week"], abc)
        self.assertEqual(cumulative["fox"]["verified_final_clips"], 1)

    def test_cumulative_source_ledger_rejects_cross_source_audio_and_run_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            candidate = json.loads(fixture["candidate_manifest"].read_text(
                encoding="utf-8").strip())
            decision = json.loads(fixture["identity_manifest"].read_text(
                encoding="utf-8").strip())
            final = json.loads(fixture["manifest"].read_text(encoding="utf-8").strip())
            fox_candidate = copy.deepcopy(candidate)
            fox_candidate.update({
                "clip_id": "candidate:fox", "source_family": "fox", "clip_sha256": "3" * 64,
            })
            with self.assertRaisesRegex(S.SpeechBuildError, "candidate audio identity"):
                S._verified_source_ledger(
                    [candidate, fox_candidate], [decision], [final])

            fox_candidate["audio_sha256"] = "2" * 64
            fox_decision = copy.deepcopy(decision)
            fox_decision.update({
                "clip_id": "candidate:fox", "candidate_clip_sha256": "3" * 64,
                "candidate_audio_sha256": "2" * 64, "identity_row_sha256": "4" * 64,
            })
            final["identity_run_sha256"] = "8" * 64
            fox_final = copy.deepcopy(final)
            fox_final.update({
                "clip_id": "candidate:fox", "parent_clip_id": "candidate:fox",
                "source_family": "fox", "candidate_clip_sha256": "3" * 64,
                "identity_row_sha256": "4" * 64, "audio_sha256": "5" * 64,
                "source_audio_sha256": "6" * 64, "final_row_sha256": "7" * 64,
            })
            with self.assertRaisesRegex(S.SpeechBuildError, "accepted-run identity"):
                S._verified_source_ledger(
                    [candidate, fox_candidate], [decision, fox_decision], [final, fox_final])

    def test_canonical_and_verified_audio_identity_overlap_is_rejected(self):
        canonical = [{"clip_id": "canonical", "audio_sha256": "a" * 64}]
        verified = [{"clip_id": "verified", "audio_sha256": "a" * 64}]
        with self.assertRaisesRegex(S.SpeechBuildError, "audio identities overlap"):
            S._require_disjoint_origin_identities([canonical, verified, []])

    def test_verified_final_rejects_tampered_per_source_receipt_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = verified_fixture(directory)
            receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
            receipt["by_source"]["abc-this-week"]["verified_final_clips"] = 0
            write_json(fixture["receipt"], receipt)
            with self.assertRaisesRegex(S.SpeechBuildError, "cumulative per-source"):
                self.validate(fixture, lambda _path, _holder: {
                    channel: 1.0 for channel in S.CHANNELS
                })


class CanonicalSourceOverlapContracts(unittest.TestCase):
    def test_sotu_lineage_builder_persists_and_consumer_revalidates_full_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sotu.wav"
            clip_path = root / "clip.wav"
            make_wav(source, frames=32000)
            make_wav(clip_path, frames=16000)
            canonical = {
                "outlet": "SUNDAY", "show": "CNN State of the Union",
                "sid": "sotu_fixture", "member": "Member One", "samplerate": 16000,
                "start": 0.0, "end": 1.0, "dur": 1.0, "frames": 16000,
                "sha256": S.sha256_file(clip_path), "out_wav": str(clip_path),
                "src_wav": str(source),
            }
            manifest = root / "canonical.jsonl"
            lineage = root / "lineage.jsonl"
            receipt = root / "lineage.receipt.json"
            write_jsonl(manifest, [canonical])
            built = CSL.build(manifest, lineage, receipt)
            self.assertEqual(built["canonical_sotu_rows"], 1)
            bindings, meta = S.validate_canonical_source_lineage(
                [canonical], S.sha256_file(manifest), lineage, receipt,
                required=True, verify_hash=True,
            )
            self.assertEqual(
                bindings[S.row_sha256(canonical)]["source_audio_sha256"],
                S.sha256_file(source),
            )
            self.assertTrue(meta["source_hashes_reverified"])

    def test_frame_overlap_partial_containment_equal_and_nonoverlap(self):
        self.assertEqual(S._interval_overlap_frames(0, 100, 75, 100), 25)
        self.assertEqual(S._interval_overlap_frames(0, 100, 20, 10), 10)
        self.assertEqual(S._interval_overlap_frames(20, 10, 0, 100), 10)
        self.assertEqual(S._interval_overlap_frames(20, 10, 20, 10), 10)
        self.assertEqual(S._interval_overlap_frames(0, 100, 100, 20), 0)
        self.assertEqual(S._interval_overlap_frames(0, 100, 101, 20), 0)

    def canonical_rows(self):
        manifest = {
            "outlet": "SUNDAY", "show": "CNN State of the Union",
            "sid": "sotu_fixture", "member": "Member One", "samplerate": 16000,
            "start": 1.0, "end": 2.0, "frames": 16000,
            "sha256": "a" * 64, "out_wav": r"C:\fixture\canonical.wav",
            "src_wav": r"C:\fixture\source.wav",
        }
        canonical_id = S.row_sha256(manifest)
        record = {
            "clip_id": canonical_id, "manifest_row_sha256": canonical_id,
            "audio_sha256": "a" * 64, "member": "Member One",
        }
        lineage = {
            canonical_id: {
                "kind": "cnn-sotu", "canonical_row_sha256": canonical_id,
                "canonical_clip_audio_sha256": "a" * 64,
                "out_wav": r"C:\fixture\canonical.wav",
                "source_wav": r"C:\fixture\source.wav",
                "source_audio_sha256": "b" * 64,
                "source_start_frame": 16000, "source_frame_count": 16000,
                "source_end_frame_exclusive": 32000, "source_sample_rate": 16000,
                "reference_url": None,
            }
        }
        return manifest, record, lineage

    def test_overlapping_verified_final_supersedes_record_and_preserves_files_and_link(self):
        manifest, record, lineage = self.canonical_rows()
        verified = {
            "clip_id": "verified:cnn", "source_family": "cnn",
            "source_audio_sha256": "b" * 64,
            "reference_url": "https://example.test/cnn-recording",
            "cut": {"source_start_frame": 24000, "frames": 16000},
        }
        active, superseded, verified_records = S.supersede_canonical_source_overlaps(
            [record], [manifest], [verified], lineage,
        )
        self.assertEqual(active, [])
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["superseded_by"][0]["overlap_frames"], 8000)
        self.assertEqual(superseded[0]["out_wav"], r"C:\fixture\canonical.wav")
        self.assertEqual(superseded[0]["source_wav"], r"C:\fixture\source.wav")
        self.assertEqual(
            superseded[0]["superseded_by"][0]["reference_url"],
            "https://example.test/cnn-recording",
        )
        self.assertFalse(superseded[0]["physical_cleanup_authorized"])
        self.assertEqual(
            verified_records[0]["supersedes_canonical"][0]["overlap_frames"], 8000,
        )

    def test_nonoverlapping_same_source_keeps_canonical_record(self):
        manifest, record, lineage = self.canonical_rows()
        verified = {
            "clip_id": "verified:cnn", "source_family": "cnn",
            "source_audio_sha256": "b" * 64,
            "reference_url": "https://example.test/cnn-recording",
            "cut": {"source_start_frame": 32000, "frames": 16000},
        }
        active, superseded, _verified = S.supersede_canonical_source_overlaps(
            [record], [manifest], [verified], lineage,
        )
        self.assertEqual(active, [record])
        self.assertEqual(superseded, [])

    def test_missing_cnn_lineage_fails_closed(self):
        manifest, record, _lineage = self.canonical_rows()
        verified = {
            "clip_id": "verified:cnn", "source_family": "cnn",
            "source_audio_sha256": "b" * 64,
            "reference_url": "https://example.test/cnn-recording",
            "cut": {"source_start_frame": 16000, "frames": 16000},
        }
        with self.assertRaisesRegex(S.SpeechBuildError, "lineage is missing"):
            S.supersede_canonical_source_overlaps(
                [record], [manifest], [verified], {},
            )

    def test_required_sotu_sidecar_missing_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.jsonl"
            with self.assertRaisesRegex(S.SpeechBuildError, "require the canonical SOTU"):
                S.validate_canonical_source_lineage(
                    [], "0" * 64, missing, missing.with_suffix(".receipt.json"),
                    required=True,
                )


class DisplayOnlyContract(unittest.TestCase):
    def test_labeled_subject_tonality_uses_matched_members(self):
        def subject_block(value):
            block = {"clips": 2, "minutes": 1.0, "episodes": 2}
            for channel in S.CHANNELS:
                block[channel + "_mean"] = float(value)
                block[channel + "_sd"] = 0.0
            return block

        members = {
            "Member %d" % index: {
                "by_subject": {
                    "economy": subject_block(index + 1),
                    "health": subject_block(index + 2),
                }
            }
            for index in range(6)
        }
        result = S.global_subject_tonality_effects(members)
        self.assertEqual(result["subjects"], ["economy", "health"])
        comparison = result["channels"]["f0_mean"][0]
        self.assertEqual(comparison["n_members"], 6)
        self.assertEqual(comparison["mean_delta_first_minus_second"], -1.0)

    def test_contract_forbids_engine_and_big_five_wiring(self):
        source = (FEC / "speech" / "build_descriptive_speech.py").read_text(encoding="utf-8")
        self.assertIn('"audio_may_modify_big_five": False', source)
        self.assertIn('"audio_may_modify_rank_or_allocation": False', source)
        self.assertIn('"floor_audio_in_headline": False', source)
        self.assertIn('"verified_source_media_features_recomputed": True', source)
        self.assertIn('"labeled_subject_tonality_stratified": True', source)
        self.assertIn('"candidate_stage_features_may_enter_verified_measurements": False', source)
        self.assertIn('counts["locally_measured"] > 0', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
