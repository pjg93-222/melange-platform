# -*- coding: utf-8 -*-
"""TDD contracts for deterministic speaker-threshold calibration.

The real calibration look remains blocked by the draft preregistration and absent local model
weights. These fixtures exercise only the engineering contract with synthetic pre-computed cosine
scores: EER rate is distinct from the EER score threshold, stop-bar misses cannot be consumed,
caption-mismatch rejection is enforced, sex parity is reported without retuning, and artifacts are
byte-identical across reruns.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import members.b5_audio_calibrate as CAL  # noqa: E402
import members.b5_audio_verify2 as VERIFY  # noqa: E402


def good_trials():
    rows = []
    for corpus in ("legacy_floor", "houselive"):
        for i in range(20):
            rows.append({
                "trial_id": "%s-g-%02d" % (corpus, i),
                "corpus": corpus,
                "label": "genuine",
                "score": 0.94 + 0.002 * (i % 10),
                "sex": "F" if i % 2 else "M",
                "caption_mismatch": False,
            })
            rows.append({
                "trial_id": "%s-i-%02d" % (corpus, i),
                "corpus": corpus,
                "label": "impostor",
                "score": 0.20 + 0.005 * (i % 10),
                "sex": "F" if i % 2 else "M",
                "caption_mismatch": False,
            })
    for i in range(148):
        rows.append({
            "trial_id": "caption-mismatch-%03d" % i,
            "corpus": "legacy_floor",
            "label": "impostor",
            "score": 0.25 + 0.0002 * i,
            "sex": "F" if i % 2 else "M",
            "caption_mismatch": True,
        })
    return rows


def test_calibration_separates_eer_rate_from_score_threshold():
    doc = CAL.calibrate(
        good_trials(), source_sha256="a" * 64, label_provenance="HUMAN_RATIFIED")
    assert doc["schema"] == "b5-audio-identity-thresholds-v1", doc
    assert doc["status"] == "CALIBRATION_PASS", doc
    assert 0 <= doc["eer_rate"] <= 0.05, doc
    assert -1 <= doc["theta_eer"] <= doc["theta_accept"] <= 1, doc
    assert doc["theta_enroll"] == doc["theta_accept"], doc
    assert doc["caption_mismatch_rejection_rate"] >= 0.99, doc
    assert doc["calibration"]["source_sha256"] == "a" * 64
    assert set(doc["per_corpus"]) == {"legacy_floor", "houselive"}
    loaded = VERIFY.validate_thresholds_doc(doc, "synthetic")
    assert loaded["theta_eer"] == doc["theta_eer"]


def test_overlap_hits_stop_bar_and_is_not_consumable():
    rows = []
    for i, score in enumerate((0.40, 0.45, 0.50, 0.55, 0.60) * 4):
        rows.append({"trial_id": "g%02d" % i, "corpus": "bad", "label": "genuine",
                     "score": score, "sex": "M", "caption_mismatch": False})
        rows.append({"trial_id": "i%02d" % i, "corpus": "bad", "label": "impostor",
                     "score": score, "sex": "F", "caption_mismatch": i < 2})
    doc = CAL.calibrate(
        rows, source_sha256="b" * 64, label_provenance="HUMAN_RATIFIED")
    assert doc["status"] == "CALIBRATION_STOP_BAR_MISS", doc
    assert doc["eer_rate"] > 0.05 or doc["caption_mismatch_rejection_rate"] < 0.99
    try:
        VERIFY.validate_thresholds_doc(doc, "synthetic miss")
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("failed calibration was consumable")


def test_sex_parity_red_flag_is_reported_without_threshold_retuning():
    rows = good_trials()
    # One female genuine sits below the otherwise clean accept threshold. Male FRR remains zero;
    # the raw error-rate ratio is therefore infinite and must be reported as a red flag.
    rows[2]["score"] = 0.50
    doc = CAL.calibrate(
        rows, source_sha256="c" * 64, label_provenance="HUMAN_RATIFIED")
    assert doc["status"] == "CALIBRATION_PASS", doc
    assert doc["sex_parity"]["red_flag"] is True, doc["sex_parity"]
    assert doc["sex_parity"]["threshold_was_retuned_for_parity"] is False
    json.dumps(doc, allow_nan=False)


def test_cli_is_deterministic_and_requires_candidate_paths():
    with tempfile.TemporaryDirectory() as tmp:
        trials_path = os.path.join(tmp, "b5_audio_identity_trials.json.candidate")
        with open(trials_path, "w", encoding="utf-8") as handle:
            json.dump({
                "schema": "b5-audio-identity-trials-v1",
                "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
                "label_provenance": "HUMAN_RATIFIED",
                "trials": good_trials(),
            }, handle)
        out = os.path.join(tmp, "b5_audio_identity_thresholds.json.candidate")
        CAL.main(["--trials", trials_path, "--out", out])
        first = open(out, "rb").read()
        CAL.main(["--trials", trials_path, "--out", out])
        second = open(out, "rb").read()
        assert first == second
        try:
            CAL.main(["--trials", trials_path, "--out", os.path.join(tmp, "thresholds.json")])
        except SystemExit as exc:
            assert ".json.candidate" in str(exc)
        else:
            raise AssertionError("non-candidate calibration output was accepted")


def test_malformed_trials_fail_closed():
    bad = good_trials()
    bad[0] = dict(bad[0], score=2.0)
    try:
        CAL.calibrate(
            bad, source_sha256="d" * 64, label_provenance="HUMAN_RATIFIED")
    except ValueError as exc:
        assert "score" in str(exc)
    else:
        raise AssertionError("out-of-range cosine score was accepted")


def test_machine_labeled_trials_can_smoke_but_never_be_consumed():
    doc = CAL.calibrate(
        good_trials(),
        source_sha256="e" * 64,
        label_provenance="MACHINE_CAPTION_HARD_SIGNAL",
    )
    assert doc["status"] == "CALIBRATION_WEAK_LABEL_SMOKE", doc
    assert doc["bars"]["human_ratified_labels"] is False
    assert doc["label_provenance"] == "MACHINE_CAPTION_HARD_SIGNAL"
    try:
        VERIFY.validate_thresholds_doc(doc, "weak-label smoke")
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("weak machine labels produced consumable thresholds")


def test_claim_probability_calibration_is_typed_and_cosine_verifier_refuses_it():
    doc = CAL.calibrate(
        good_trials(),
        source_sha256="f" * 64,
        label_provenance="HUMAN_RATIFIED",
        score_kind="claimed_class_probability",
    )
    assert doc["status"] == "CALIBRATION_PASS", doc
    assert doc["score_kind"] == "claimed_class_probability"
    try:
        VERIFY.validate_thresholds_doc(doc, "linear-head probability thresholds")
    except SystemExit as exc:
        assert exc.code == 2
        assert "score_kind" in str(exc)
    else:
        raise AssertionError("probability thresholds were accepted by the cosine verifier")

    bad = good_trials()
    bad[0] = dict(bad[0], score=-0.01)
    try:
        CAL.calibrate(
            bad,
            source_sha256="f" * 64,
            label_provenance="HUMAN_RATIFIED",
            score_kind="claimed_class_probability",
        )
    except ValueError as exc:
        assert "score" in str(exc)
    else:
        raise AssertionError("negative claimed-class probability was accepted")


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
        except BaseException:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
        else:
            passed += 1
            print("PASS %s" % fn.__name__)
    print("test_audio_calibrate: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
