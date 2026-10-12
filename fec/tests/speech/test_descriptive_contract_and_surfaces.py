from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))

from speech import descriptive_contract as C  # noqa: E402


def block():
    row = {"clips": 1, "minutes": 1.0, "episodes": 1}
    for channel in C.CHANNELS:
        row[channel + "_mean"] = 1.0
        row[channel + "_sd"] = None
    return row


def artifact():
    contract = {
        "display_only": True,
        "audio_may_modify_big_five": False,
        "audio_may_modify_rank_or_allocation": False,
        "identity_first": True,
        "complete_manifest_partition_required": True,
        "clip_hash_and_pcm_contract_required": True,
        "verified_source_media_receipt_chain_required": True,
        "verified_source_media_features_recomputed": True,
        "labeled_subject_tonality_stratified": True,
        "candidate_stage_features_may_enter_verified_measurements": False,
        "source_program_metadata_may_identify_speaker": False,
        "cross_source_and_canonical_deduplication_required": True,
        "canonical_source_interval_supersession_required": True,
        "source_audio_physical_cleanup_authorized": False,
        "floor_audio_in_headline": False,
        "setting_and_subject_effects_are_descriptive_not_causal": True,
    }
    return {
        "schema": C.SCHEMA, "status": C.STATUS, "contract": contract,
        "corpus": {"manifest_rows": 1, "prepared_rows": 1, "excluded_rows": 0,
                   "superseded_rows": 0,
                   "members": 1, "hours": 1 / 60, "cut_status": "ALL_HASH_AND_PCM_VERIFIED",
                   "by_source_family": {"npr": {"clips": 1}}, "by_subject": {},
                   "unlabeled_subject_clips": 1},
        "source": {
            "source_media_verified_final": {"enabled": False, "accepted_clips": 0},
            "canonical_source_lineage": {"enabled": False, "required": False},
            "canonical_supersession": {
                "schema": "speech-canonical-record-supersession-v1",
                "status": "NOT_NEEDED", "superseded_rows": 0,
                "supersession_rows_sha256": "empty", "physical_cleanup_authorized": False,
                "records": [],
            },
        },
        "members": {"A": {
            "coverage": {"clips": 1, "minutes": 1.0, "settings": ["npr"],
                         "setting_count": 1, "identity_tiers": {"package_aligned": 1},
                         "cut_status": "ALL_HASH_AND_PCM_VERIFIED"},
            "headline_interviews_only": block(), "by_setting": {"npr": block()},
            "by_source": {"npr": block()}, "by_subject": {},
            "setting_contrasts": [], "subject_tonality_contrasts": [],
            "subject_effects": [], "caveats": [],
        }},
    }


class ContractTests(unittest.TestCase):
    def write_pair(self, directory):
        directory = Path(directory)
        payload = artifact()
        out = directory / "speech.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        receipt = {
            "schema": C.RECEIPT_SCHEMA, "status": "PASS", "artifact_sha256": digest,
            "manifest_rows": 1, "prepared_rows": 1, "excluded_rows": 0,
            "superseded_rows": 0, "canonical_supersession_rows_sha256": "empty",
            "verified_final_manifest_rows": 0,
            "contract": payload["contract"],
        }
        receipt_path = directory / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return out, receipt_path

    def write_zero_final_cnn_pair(self, directory):
        out, receipt_path = self.write_pair(directory)
        payload = json.loads(out.read_text(encoding="utf-8"))
        verified = payload["source"]["source_media_verified_final"]
        verified.update({
            "enabled": True, "accepted_clips": 1,
            "manifest_sha256": "1" * 64, "receipt_sha256": "2" * 64,
            "identity_manifest_sha256": "3" * 64,
            "identity_receipt_sha256": "4" * 64,
            "candidate_manifest_sha256": "5" * 64,
            "candidate_receipt_sha256": "6" * 64,
            "inventory_sha256": "7" * 64, "calibration_sha256": "8" * 64,
            "scoring_contract_sha256": "9" * 64,
            "features_recomputed_from_every_final_wav": True,
            "candidate_features_trusted": False, "source_pcm_reverified": True,
            "cumulative_source_bindings_complete": True,
            "by_source": {
                "abc-this-week": {"verified_final_clips": 1},
                "cnn": {"verified_final_clips": 0},
            },
        })
        out.write_text(json.dumps(payload), encoding="utf-8")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.update({
            "artifact_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            "verified_final_manifest_rows": 1,
            "verified_final_audit_status": "PASS",
            "verified_final_manifest_sha256": verified["manifest_sha256"],
            "verified_final_receipt_sha256": verified["receipt_sha256"],
        })
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return out, receipt_path

    def test_valid_display_only_artifact_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            out, receipt = self.write_pair(directory)
            self.assertEqual(C.load_optional(directory, out, receipt)["members"]["A"]["coverage"]["clips"], 1)

    def test_rank_or_big_five_permission_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            out, receipt = self.write_pair(directory)
            payload = json.loads(out.read_text(encoding="utf-8"))
            payload["contract"]["audio_may_modify_rank_or_allocation"] = True
            out.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(C.SpeechContractError):
                C.load_optional(directory, out, receipt)

    def test_hash_mismatched_receipt_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            out, receipt = self.write_pair(directory)
            out.write_text(out.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaises(C.SpeechContractError):
                C.load_optional(directory, out, receipt)

    def test_zero_final_cnn_ledger_does_not_require_cnn_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            out, receipt = self.write_zero_final_cnn_pair(directory)
            loaded = C.load_optional(directory, out, receipt)
            self.assertEqual(
                loaded["source"]["source_media_verified_final"]["by_source"]["cnn"]
                ["verified_final_clips"], 0,
            )

    def test_malformed_zero_final_cnn_count_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            out, receipt = self.write_zero_final_cnn_pair(directory)
            payload = json.loads(out.read_text(encoding="utf-8"))
            payload["source"]["source_media_verified_final"]["by_source"]["cnn"][
                "verified_final_clips"] = "0"
            out.write_text(json.dumps(payload), encoding="utf-8")
            receipt_row = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_row["artifact_sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
            receipt.write_text(json.dumps(receipt_row), encoding="utf-8")
            with self.assertRaisesRegex(C.SpeechContractError, "nonnegative integer"):
                C.load_optional(directory, out, receipt)


class SurfaceIsolationTests(unittest.TestCase):
    def test_member_desk_attaches_speech_after_rank_postflight(self):
        source = (FEC / "surfaces" / "make_member_desk.py").read_text(encoding="utf-8")
        postflight = source.index("bundle['integrity']['validation'] = DI.postflight")
        attachment = source.index("_member['speech_observed'] =", postflight)
        self.assertLess(postflight, attachment)

    def test_speech_has_own_descriptive_section(self):
        template = (FEC / "surfaces" / "templates" / "make_member_desk_00.tpl").read_text(encoding="utf-8")
        self.assertIn("sub('Observed speech & context',speechHTML(m))", template)
        self.assertIn("These observations never modify Big Five, rank, stance, persuasion, or allocation", template)

    def test_pipeline_builds_speech_before_both_moneyball_surfaces(self):
        source = (FEC / "run_pipeline.py").read_text(encoding="utf-8")
        # Repointed 2026-09-02 at the cumulative source-media lane: the step now carries eight
        # CLI arguments instead of an empty list, so anchor on the tuple's opening and confirm
        # the --verified-manifest argument (the lane's own binding) is inside this step's tuple.
        speech = source.index("('build_descriptive_speech.py',[")
        speech_end = source.index("]),", speech)
        self.assertIn("'--verified-manifest'", source[speech:speech_end])
        self.assertLess(speech, source.index("('build_dashboard_bundle.py',[])"))
        self.assertLess(speech, source.index("('make_member_desk.py',[])"))

    def test_network_tooltip_marks_speech_descriptive(self):
        template = (FEC / "surfaces" / "templates" / "make_dashboard_html_00.tpl").read_text(encoding="utf-8")
        self.assertIn("Observed speech:", template)
        self.assertIn("(descriptive only)", template)

    def test_dashboard_tables_source_media_recovery_without_engine_wiring(self):
        template = (FEC / "surfaces" / "templates" / "make_dashboard_html_00.tpl").read_text(encoding="utf-8")
        self.assertIn("renderSpeechCoverage", template)
        self.assertIn("source_media_recovery", template)
        self.assertIn("never changes Big Five, rank, or allocation", template)


if __name__ == "__main__":
    unittest.main(verbosity=2)
