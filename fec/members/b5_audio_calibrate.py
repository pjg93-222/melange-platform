# -*- coding: utf-8 -*-
"""Deterministic threshold calibration for the research-only speaker identity layer.

This module consumes pre-computed, labeled similarity trials. It supports either cosine scores
from the enrollment-centroid verifier or claimed-class probabilities from the paper-faithful
linear-head candidate. It does not load audio, models, or network resources, so building and
testing it does not consume the draft preregistration's real calibration look. The real trial
artifact remains user-gated and SHA-pinned before use.

The important distinction is explicit:

* ``theta_eer`` is the cosine SCORE threshold at the equal-error operating point.
* ``eer_rate`` is the observed ERROR RATE at that point and is what the <=5% stop bar evaluates.

Only ``CALIBRATION_PASS`` artifacts are consumable. A stop-bar miss is still written for the
scientific record, but every downstream loader must refuse it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCHEMA = "b5-audio-identity-thresholds-v1"
TRIALS_SCHEMA = "b5-audio-identity-trials-v1"
STATUS_PASS = "CALIBRATION_PASS"
STATUS_MISS = "CALIBRATION_STOP_BAR_MISS"
STATUS_WEAK_LABEL = "CALIBRATION_WEAK_LABEL_SMOKE"
HUMAN_LABEL_PROVENANCE = "HUMAN_RATIFIED"
SCORE_COSINE = "cosine"
SCORE_CLAIM_PROBABILITY = "claimed_class_probability"
SCORE_KINDS = (SCORE_COSINE, SCORE_CLAIM_PROBABILITY)
MAX_EER_RATE = 0.05
MIN_CAPTION_MISMATCH_N = 148
MIN_CAPTION_MISMATCH_REJECTION = 0.99


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_trials(rows, score_kind):
    if score_kind not in SCORE_KINDS:
        raise ValueError("score_kind must be one of %r" % (SCORE_KINDS,))
    if not isinstance(rows, list) or not rows:
        raise ValueError("trials must be a non-empty list")
    seen = set()
    out = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("trial %d is not an object" % index)
        trial_id = str(raw.get("trial_id") or "").strip()
        corpus = str(raw.get("corpus") or "").strip()
        label = str(raw.get("label") or "").strip().lower()
        sex = str(raw.get("sex") or "unknown").strip().upper() or "UNKNOWN"
        if not trial_id or trial_id in seen:
            raise ValueError("trial %d has missing/duplicate trial_id" % index)
        seen.add(trial_id)
        if not corpus:
            raise ValueError("trial %s lacks corpus" % trial_id)
        if label not in ("genuine", "impostor"):
            raise ValueError("trial %s label must be genuine or impostor" % trial_id)
        try:
            score = float(raw.get("score"))
        except (TypeError, ValueError):
            raise ValueError("trial %s score is not numeric" % trial_id)
        low = -1.0 if score_kind == SCORE_COSINE else 0.0
        if not math.isfinite(score) or not low <= score <= 1.0:
            raise ValueError("trial %s score %.6r is outside %s range [%.0f,1]" %
                             (trial_id, score, score_kind, low))
        caption_mismatch = bool(raw.get("caption_mismatch"))
        if caption_mismatch and label != "impostor":
            raise ValueError("trial %s caption_mismatch must be an impostor" % trial_id)
        out.append({
            "trial_id": trial_id, "corpus": corpus, "label": label, "score": score,
            "sex": sex, "caption_mismatch": caption_mismatch,
        })
    for corpus in sorted({row["corpus"] for row in out}):
        labels = {row["label"] for row in out if row["corpus"] == corpus}
        if labels != {"genuine", "impostor"}:
            raise ValueError("corpus %s requires genuine and impostor trials" % corpus)
    return out


def _threshold_candidates(scores):
    unique = sorted(set(float(score) for score in scores))
    candidates = [-1.0]
    candidates.extend((a + b) / 2.0 for a, b in zip(unique[:-1], unique[1:]))
    candidates.append(1.0)
    return sorted(set(max(-1.0, min(1.0, value)) for value in candidates))


def _rates(rows, threshold):
    genuine = [row for row in rows if row["label"] == "genuine"]
    impostor = [row for row in rows if row["label"] == "impostor"]
    false_rejects = sum(row["score"] < threshold for row in genuine)
    false_accepts = sum(row["score"] >= threshold for row in impostor)
    return {
        "genuine_n": len(genuine), "impostor_n": len(impostor),
        "false_reject_n": false_rejects, "false_accept_n": false_accepts,
        "frr": false_rejects / float(len(genuine)),
        "far": false_accepts / float(len(impostor)),
    }


def _eer(rows):
    candidates = _threshold_candidates([row["score"] for row in rows])
    evaluated = []
    for threshold in candidates:
        rates = _rates(rows, threshold)
        eer_rate = (rates["far"] + rates["frr"]) / 2.0
        key = (abs(rates["far"] - rates["frr"]), eer_rate, threshold)
        evaluated.append((key, threshold, rates, eer_rate))
    _, threshold, rates, eer_rate = min(evaluated, key=lambda row: row[0])
    return {
        "theta_eer": threshold, "eer_rate": eer_rate,
        **rates,
    }


def _error_ratio(values):
    finite = [float(value) for value in values]
    if len(finite) < 2:
        return None
    low, high = min(finite), max(finite)
    if low == 0:
        return 1.0 if high == 0 else "infinite"
    return high / low


def _sex_parity(rows, threshold):
    groups = {}
    for sex in sorted({row["sex"] for row in rows if row["sex"] not in ("", "UNKNOWN")}):
        selected = [row for row in rows if row["sex"] == sex]
        labels = {row["label"] for row in selected}
        if labels != {"genuine", "impostor"}:
            continue
        groups[sex] = _rates(selected, threshold)
    far_ratio = _error_ratio([row["far"] for row in groups.values()])
    frr_ratio = _error_ratio([row["frr"] for row in groups.values()])
    ratios = [value for value in (far_ratio, frr_ratio) if value is not None]
    red_flag = any(value == "infinite" or float(value) > 2.0 for value in ratios)
    return {
        "groups": groups, "far_ratio_max_to_min": far_ratio,
        "frr_ratio_max_to_min": frr_ratio, "red_flag": red_flag,
        "threshold_was_retuned_for_parity": False,
        "meaning": "reported diagnostic only; threshold selection never uses sex labels",
    }


def calibrate(rows, source_sha256, label_provenance, score_kind=SCORE_COSINE):
    """Return the complete deterministic thresholds artifact from labeled similarity trials."""
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ValueError("source_sha256 must be a 64-character SHA-256")
    label_provenance = str(label_provenance or "").strip()
    if not label_provenance:
        raise ValueError("label_provenance is required")
    score_kind = str(score_kind or "").strip()
    trials = _validated_trials(rows, score_kind)
    by_corpus = defaultdict(list)
    for row in trials:
        by_corpus[row["corpus"]].append(row)
    per_corpus = {corpus: _eer(selected) for corpus, selected in sorted(by_corpus.items())}
    theta_eer = max(row["theta_eer"] for row in per_corpus.values())
    eer_rate = max(row["eer_rate"] for row in per_corpus.values())

    caption = [row for row in trials if row["caption_mismatch"]]
    candidates = [threshold for threshold in
                  _threshold_candidates([row["score"] for row in trials])
                  if threshold >= theta_eer]
    if not candidates:
        candidates = [1.0]
    selected = None
    for threshold in candidates:
        rejected = sum(row["score"] < threshold for row in caption)
        rejection_rate = rejected / float(len(caption)) if caption else 0.0
        if rejection_rate >= MIN_CAPTION_MISMATCH_REJECTION:
            selected = threshold
            break
    theta_accept = selected if selected is not None else max(candidates)
    caption_rejected = sum(row["score"] < theta_accept for row in caption)
    caption_rejection_rate = caption_rejected / float(len(caption)) if caption else 0.0
    overall_rates = _rates(trials, theta_accept)
    bars = {
        "all_corpus_eer_rate_lte_0_05": all(
            row["eer_rate"] <= MAX_EER_RATE for row in per_corpus.values()),
        "caption_mismatch_n_gte_148": len(caption) >= MIN_CAPTION_MISMATCH_N,
        "caption_mismatch_rejection_gte_0_99":
            caption_rejection_rate >= MIN_CAPTION_MISMATCH_REJECTION,
        "human_ratified_labels": label_provenance == HUMAN_LABEL_PROVENANCE,
    }
    measurement_bars = [value for key, value in bars.items()
                        if key != "human_ratified_labels"]
    if not all(measurement_bars):
        status = STATUS_MISS
    elif bars["human_ratified_labels"]:
        status = STATUS_PASS
    else:
        status = STATUS_WEAK_LABEL
    return {
        "schema": SCHEMA, "status": status,
        "score_kind": score_kind,
        "label_provenance": label_provenance,
        "theta_accept": theta_accept,
        "theta_enroll": theta_accept,
        "theta_eer": theta_eer,
        "eer_rate": eer_rate,
        "far_at_theta_accept": overall_rates["far"],
        "frr_at_theta_accept": overall_rates["frr"],
        "caption_mismatch_n": len(caption),
        "caption_mismatch_rejection_rate": caption_rejection_rate,
        "per_corpus": per_corpus,
        "sex_parity": _sex_parity(trials, theta_accept),
        "bars": bars,
        "calibration": {
            "method": "deterministic labeled %s DET/EER calibration" % score_kind,
            "accept_rule": "score >= theta_accept",
            "theta_accept_selection":
                "lowest candidate >= max corpus theta_eer that rejects >=99% caption mismatches",
            "theta_enroll_rule": "equal to theta_accept; conservative single calibrated boundary",
            "eer_rate_stop_bar": MAX_EER_RATE,
            "caption_mismatch_min_n": MIN_CAPTION_MISMATCH_N,
            "caption_mismatch_rejection_bar": MIN_CAPTION_MISMATCH_REJECTION,
            "source_sha256": source_sha256,
            "trial_n": len(trials),
            "no_threshold_retuning_for_sex_parity": True,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Calibrate speaker-identity cosine thresholds from labeled candidate trials.")
    parser.add_argument("--trials", required=True,
                        help="b5-audio-identity-trials-v1 .json.candidate")
    parser.add_argument("--out", required=True,
                        help="b5_audio_identity_thresholds.json.candidate")
    args = parser.parse_args(argv)
    if not args.trials.endswith(".json.candidate"):
        raise SystemExit("REFUSED: --trials must end in .json.candidate")
    if not args.out.endswith(".json.candidate"):
        raise SystemExit("REFUSED: --out must end in .json.candidate")
    with open(args.trials, encoding="utf-8") as handle:
        source = json.load(handle)
    if (source.get("schema") != TRIALS_SCHEMA or
            source.get("status") != "RESEARCH_CANDIDATE_NOT_CONSUMED"):
        raise SystemExit("REFUSED: trials artifact schema/status mismatch")
    doc = calibrate(
        source.get("trials"),
        sha256_file(args.trials),
        source.get("label_provenance"),
        source.get("score_kind") or SCORE_COSINE,
    )
    from moneyball_core import dump_atomic
    dump_atomic(doc, args.out, sort_keys=True, indent=1)
    print("audio identity calibration: %s | max EER %.4f | caption reject %.4f" %
          (doc["status"], doc["eer_rate"], doc["caption_mismatch_rejection_rate"]))
    print("wrote %s" % args.out)
    return doc


if __name__ == "__main__":
    main()
