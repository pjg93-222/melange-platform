# -*- coding: utf-8 -*-
"""TDD battery for the paper-exact Adam-trained Wav2Vec single-layer candidate."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import members.b5_audio_neural_head as neural  # noqa: E402
import members.b5_audio_neural_pilot as pilot  # noqa: E402

SHA_A = hashlib.sha256(b"raw-training-embeddings").hexdigest()
SHA_B = hashlib.sha256(b"fresh-six-source-split").hexdigest()


def rows(split):
    centers = {
        "Member A": (3.0, 0.0, 0.0),
        "Member B": (0.0, 3.0, 0.0),
        "Member C": (0.0, 0.0, 3.0),
    }
    count = 12 if split == "train" else 4
    out = []
    for member in sorted(centers):
        center = centers[member]
        for index in range(count):
            delta = ((index % 5) - 2) * 0.025
            out.append({
                "member": member,
                "event_id": "%s-%s-event-%d" % (member, split, index // 4),
                "source_sha256": hashlib.sha256(
                    ("%s-%s-source-%d" % (member, split, index)).encode("utf-8")
                ).hexdigest(),
                "embedding": [
                    center[0] + delta,
                    center[1] - delta / 2.0,
                    center[2] + delta / 3.0,
                ],
            })
    return out


def fitted():
    return neural.fit_neural_head(
        rows("train"), rows("validation"), SHA_A, SHA_B)


def test_paper_neural_head_uses_exact_fixed_training_contract():
    one = fitted()
    two = fitted()
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert one["schema"] == "b5-audio-neural-linear-head-v1"
    assert one["status"] == "RESEARCH_CANDIDATE_NOT_CONSUMED"
    assert one["classes"] == ["Member A", "Member B", "Member C"]
    assert one["training"]["hyperparameters"] == {
        "batch_size": 8,
        "early_stopping_patience": 3,
        "learning_rate": 0.001,
        "loss": "cross_entropy",
        "max_epochs": 200,
        "optimizer": "Adam",
        "seed": 0,
    }
    assert one["training"]["input_scaling"] == "none; raw mean-pooled Wav2Vec embedding"
    assert one["training"]["stopped_epoch"] <= 200
    right = neural.score_claim(one, [3.1, -0.1, 0.0], "Member A")
    wrong = neural.score_claim(one, [3.1, -0.1, 0.0], "Member B")
    assert right["claimed_probability"] > 0.90, right
    assert wrong["claimed_probability"] < 0.10, wrong
    forbidden = {"predicted_member", "winner", "best_member", "competitor_member"}
    assert not forbidden.intersection(right)


def test_paper_neural_head_refuses_train_validation_source_or_event_leakage():
    train = rows("train")
    validation = rows("validation")
    validation[0]["event_id"] = train[0]["event_id"]
    try:
        neural.fit_neural_head(train, validation, SHA_A, SHA_B)
    except ValueError as exc:
        assert "event" in str(exc).lower()
    else:
        raise AssertionError("train/validation event leak was accepted")

    train = rows("train")
    validation = rows("validation")
    validation[0]["source_sha256"] = train[0]["source_sha256"]
    try:
        neural.fit_neural_head(train, validation, SHA_A, SHA_B)
    except ValueError as exc:
        assert "source" in str(exc).lower()
    else:
        raise AssertionError("train/validation source leak was accepted")


def test_paper_neural_module_exposes_no_identification_path():
    path = os.path.join(FEC, "members", "b5_audio_neural_head.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "argmax" not in source
    assert "def identify" not in source
    assert "predicted_member" not in source


def test_neural_claim_trials_preserve_explicit_claims():
    head = fitted()
    artifact = neural.build_claim_trials(head, [{
        "trial_id": "heldout-a-as-b",
        "corpus": "fresh-six-source",
        "claimed_member": "Member B",
        "true_member": "Member A",
        "label": "impostor",
        "embedding": [3.1, -0.1, 0.0],
        "event_id": "fresh-test-event",
        "window_id": "window-1",
        "split": "test",
        "caption_mismatch": False,
    }], label_provenance="MACHINE_CAPTION_HARD_SIGNAL")
    assert artifact["score_kind"] == "claimed_class_probability"
    assert artifact["trials"][0]["claimed_member"] == "Member B"
    assert artifact["trials"][0]["true_member"] == "Member A"
    assert "predicted_member" not in artifact["trials"][0]


def test_fresh_manifest_freezes_six_sources_and_excludes_consumed_cohort():
    verify = {}
    for member in ("Member A", "Member B", "Member C", "Member D"):
        for suffix in ("f", "a", "e", "b", "d", "c"):
            verify["%s/clip_%s.wav" % (member, suffix)] = {
                "speaker": "SOLO_TARGET",
                "identity": "MATCH",
                "keep": True,
            }
    manifest = pilot.freeze_fresh_manifest(
        verify, excluded_members={"Member A"}, expected_cohort_n=3)
    assert manifest["cohort_members"] == ["Member B", "Member C", "Member D"]
    member_b = [row for row in manifest["selected"] if row["member"] == "Member B"]
    assert [(row["path"], row["split"]) for row in member_b] == [
        ("Member B/clip_a.wav", "train"),
        ("Member B/clip_b.wav", "train"),
        ("Member B/clip_c.wav", "train"),
        ("Member B/clip_d.wav", "validation"),
        ("Member B/clip_e.wav", "calibration"),
        ("Member B/clip_f.wav", "test"),
    ]
    assert manifest["selection_rule"] == (
        "all members outside the consumed 30-member cohort with at least six "
        "SOLO_TARGET/MATCH/keep clips; lexicographically first six clips; "
        "3 train, 1 early-stop validation, 1 calibration, 1 untouched test"
    )


def test_research_eer_boundary_is_not_mislabeled_as_accept_threshold():
    scored = [
        {
            "event_id": "event-a",
            "claimed_member": "Member A",
            "label": "genuine",
            "score": 0.8,
            "margin_over_best_other": 0.7,
        },
        {
            "event_id": "event-a",
            "claimed_member": "Member B",
            "label": "impostor",
            "score": 0.1,
            "margin_over_best_other": -0.7,
        },
    ]
    result = pilot.common._evaluate(
        scored, 0.5, threshold_role="theta_eer research-only")
    assert result["decision_threshold"] == 0.5
    assert result["threshold_role"] == "theta_eer research-only"
    assert "theta_accept_from_calibration" not in result


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print("PASS %s" % fn.__name__)
        except BaseException:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
    print("test_audio_neural_head: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
