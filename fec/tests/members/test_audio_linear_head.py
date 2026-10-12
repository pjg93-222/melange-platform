# -*- coding: utf-8 -*-
"""TDD battery for the paper-faithful Wav2Vec linear-head candidate.

The classifier is research-only and verification-only.  It may score a supplied member claim,
but it must never identify, rank, or silently relabel a unit as another member.

Standalone: python fec/tests/members/test_audio_linear_head.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback

import numpy as np

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import members.b5_audio_linear_head as linear  # noqa: E402
import members.b5_audio_linear_pilot as pilot  # noqa: E402

SHA_A = hashlib.sha256(b"training-embeddings").hexdigest()
SHA_B = hashlib.sha256(b"split-manifest").hexdigest()


def training_rows():
    centers = {
        "Member A": (4.0, 0.0, 0.0),
        "Member B": (0.0, 4.0, 0.0),
        "Member C": (0.0, 0.0, 4.0),
    }
    rows = []
    for member in sorted(centers):
        center = centers[member]
        for index in range(4):
            delta = (index - 1.5) * 0.04
            rows.append({
                "member": member,
                "event_id": "%s-event-%d" % (member[-1].lower(), index),
                "source_sha256": hashlib.sha256(
                    ("%s-source-%d" % (member, index)).encode("utf-8")).hexdigest(),
                "embedding": [
                    center[0] + delta,
                    center[1] - delta / 2.0,
                    center[2] + delta / 3.0,
                ],
            })
    return rows


def fitted():
    return linear.fit_linear_head(
        training_rows(), training_sha256=SHA_A, split_manifest_sha256=SHA_B)


def test_fit_is_deterministic_balanced_and_candidate_only():
    one = fitted()
    two = fitted()
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    assert one["schema"] == "b5-audio-linear-head-v1"
    assert one["status"] == "RESEARCH_CANDIDATE_NOT_CONSUMED"
    assert one["backend"] == "wav2vec2-frozen-embedding"
    assert one["classes"] == ["Member A", "Member B", "Member C"]
    assert one["training_sha256"] == SHA_A
    assert one["split_manifest_sha256"] == SHA_B
    assert one["training"]["class_counts"] == {
        "Member A": 4, "Member B": 4, "Member C": 4,
    }
    assert one["training"]["hyperparameters"] == {
        "C": 1.0,
        "l1_ratio": 0.0,
        "max_iter": 2000,
        "solver": "lbfgs",
        "tol": 1e-08,
    }
    json.dumps(one)


def test_score_claim_returns_only_claimed_class_evidence():
    head = fitted()
    right = linear.score_claim(head, [4.1, -0.1, 0.0], "Member A")
    wrong = linear.score_claim(head, [4.1, -0.1, 0.0], "Member B")
    assert right["claimed_member"] == "Member A"
    assert right["claimed_probability"] > 0.90, right
    assert right["margin_over_best_other"] > 0.80, right
    assert wrong["claimed_member"] == "Member B"
    assert wrong["claimed_probability"] < 0.10, wrong
    forbidden = {"predicted_member", "winner", "best_member", "competitor_member"}
    assert not forbidden.intersection(right)
    assert not forbidden.intersection(wrong)


def test_training_refuses_class_imbalance_and_insufficient_event_diversity():
    imbalanced = training_rows()[:-1]
    try:
        linear.fit_linear_head(imbalanced, SHA_A, SHA_B)
    except ValueError as exc:
        assert "balanced" in str(exc).lower()
    else:
        raise AssertionError("imbalanced class windows were accepted")

    one_event = training_rows()
    for row in one_event:
        if row["member"] == "Member A":
            row["event_id"] = "same-event"
    try:
        linear.fit_linear_head(one_event, SHA_A, SHA_B)
    except ValueError as exc:
        assert "event" in str(exc).lower()
    else:
        raise AssertionError("single-event member was accepted")


def split_rows():
    rows = []
    for member in ("Member A", "Member B", "Member C"):
        for split in ("train", "calibration", "test"):
            rows.append({
                "member": member,
                "split": split,
                "event_id": "%s-%s-event" % (member, split),
                "source_sha256": hashlib.sha256(
                    ("%s-%s-source" % (member, split)).encode("utf-8")).hexdigest(),
            })
    return rows


def test_split_manifest_requires_member_complete_event_and_source_disjointness():
    summary = linear.validate_source_disjoint_splits(split_rows())
    assert summary["members"] == ["Member A", "Member B", "Member C"]
    assert summary["split_counts"] == {"calibration": 3, "test": 3, "train": 3}

    event_leak = split_rows()
    event_leak[1]["event_id"] = event_leak[0]["event_id"]
    try:
        linear.validate_source_disjoint_splits(event_leak)
    except ValueError as exc:
        assert "event" in str(exc).lower()
    else:
        raise AssertionError("event leakage across splits was accepted")

    source_leak = split_rows()
    source_leak[1]["source_sha256"] = source_leak[0]["source_sha256"]
    try:
        linear.validate_source_disjoint_splits(source_leak)
    except ValueError as exc:
        assert "source" in str(exc).lower()
    else:
        raise AssertionError("source leakage across splits was accepted")

    incomplete = split_rows()[:-1]
    try:
        linear.validate_source_disjoint_splits(incomplete)
    except ValueError as exc:
        assert "all splits" in str(exc).lower()
    else:
        raise AssertionError("member missing a held-out split was accepted")


def test_claim_trials_preserve_the_supplied_claim_and_score_kind():
    head = fitted()
    rows = [
        {
            "trial_id": "genuine-a",
            "corpus": "hearing",
            "claimed_member": "Member A",
            "label": "genuine",
            "sex": "F",
            "caption_mismatch": False,
            "embedding": [4.1, -0.1, 0.0],
        },
        {
            "trial_id": "impostor-a",
            "corpus": "hearing",
            "claimed_member": "Member A",
            "label": "impostor",
            "sex": "M",
            "caption_mismatch": True,
            "embedding": [-0.1, 4.1, 0.0],
        },
    ]
    artifact = linear.build_claim_trials(head, rows, label_provenance="MACHINE_CAPTION_SMOKE")
    assert artifact["schema"] == "b5-audio-identity-trials-v1"
    assert artifact["status"] == "RESEARCH_CANDIDATE_NOT_CONSUMED"
    assert artifact["score_kind"] == "claimed_class_probability"
    assert artifact["label_provenance"] == "MACHINE_CAPTION_SMOKE"
    assert [row["claimed_member"] for row in artifact["trials"]] == [
        "Member A", "Member A",
    ]
    assert artifact["trials"][0]["score"] > artifact["trials"][1]["score"]
    assert all("predicted_member" not in row for row in artifact["trials"])
    assert len(artifact["head_sha256"]) == 64


def test_module_exposes_no_identification_or_argmax_path():
    path = os.path.join(FEC, "members", "b5_audio_linear_head.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert "argmax" not in source
    assert "def identify" not in source
    assert "predicted_member" not in source


def test_real_pilot_manifest_freezes_first_four_hard_match_sources():
    verify = {}
    for member in ("Member A", "Member B", "Member C"):
        for suffix in ("z", "b", "a", "d", "c"):
            verify["%s/clip_%s.wav" % (member, suffix)] = {
                "speaker": "SOLO_TARGET",
                "identity": "MATCH",
                "keep": True,
            }
        verify["%s/clip_mismatch.wav" % member] = {
            "speaker": "SOLO_TARGET",
            "identity": "MISMATCH",
            "keep": False,
        }
    manifest = pilot.freeze_split_manifest(
        verify, ["Member C", "Member A", "Member B"])
    selected = manifest["selected"]
    assert [row["member"] for row in selected[:4]] == ["Member A"] * 4
    member_a = [row for row in selected if row["member"] == "Member A"]
    assert [(row["path"], row["split"]) for row in member_a] == [
        ("Member A/clip_a.wav", "train"),
        ("Member A/clip_b.wav", "train"),
        ("Member A/clip_c.wav", "calibration"),
        ("Member A/clip_d.wav", "test"),
    ]
    assert manifest["selection_rule"] == (
        "same 30-member failed-cosine cohort; lexicographically first four "
        "SOLO_TARGET/MATCH/keep clips; 2 train, 1 calibration, 1 test"
    )
    assert len(manifest["caption_mismatch_clips"]) == 3


def test_energy_windows_choose_one_fixed_window_per_quartile():
    sr = 10
    window_seconds = 2.0
    samples = np.zeros(160, dtype=np.float64)  # eight complete windows
    energies = (1, 4, 2, 3, 8, 5, 6, 9)
    for index, energy in enumerate(energies):
        samples[index * 20:(index + 1) * 20] = energy
    windows = pilot.select_energy_windows(
        samples, sr, window_seconds=window_seconds, window_count=4)
    assert [(row["start"], row["end"]) for row in windows] == [
        (20, 40), (60, 80), (80, 100), (140, 160),
    ]
    assert all(row["end"] - row["start"] == 20 for row in windows)
    assert windows == pilot.select_energy_windows(
        samples.copy(), sr, window_seconds=window_seconds, window_count=4)


def test_pilot_impostor_trials_are_explicit_claims_not_relabels():
    head = fitted()
    rows = [{
        "member": "Member A",
        "corpus": "houselive",
        "split": "test",
        "event_id": "event-a",
        "window_id": "window-a",
        "embedding": [4.1, -0.1, 0.0],
        "sex": "F",
    }]
    trials = pilot.claim_rows_for_split(head, rows, "test")
    assert len(trials) == 3
    genuine = [row for row in trials if row["label"] == "genuine"]
    impostors = [row for row in trials if row["label"] == "impostor"]
    assert [row["claimed_member"] for row in genuine] == ["Member A"]
    assert [row["claimed_member"] for row in impostors] == ["Member B", "Member C"]
    assert all(row["true_member"] == "Member A" for row in trials)
    assert all("predicted_member" not in row for row in trials)


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
    print("test_audio_linear_head: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
