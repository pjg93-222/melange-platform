# -*- coding: utf-8 -*-
"""Run the frozen, source-disjoint 30-member Wav2Vec linear-head smoke.

This is a laboratory candidate, not a production builder.  It freezes the exact 30-member cohort
from the failed raw-cosine diagnostic, then selects the lexicographically first four clips that the
existing machine-caption review marked SOLO_TARGET / MATCH / keep.  The first two independent
sources train the fixed linear head, the third calibrates it, and the fourth is held out for one
final test.  Four non-overlapping five-second windows are selected per clip: one highest-energy
window from each temporal quartile.  No member, source, split, window, or hyperparameter is chosen
from model outcomes.

Machine-caption labels are explicitly weak.  They can support a smoke result, but the calibration
artifact cannot become consumable without HUMAN_RATIFIED provenance and the existing 148-example
caption-mismatch stop bar.  Every scored row is an explicit caller-supplied claim; the experiment
never substitutes a different member.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import members.b5_audio_calibrate as calibrate  # noqa: E402
import members.b5_audio_linear_head as linear  # noqa: E402
from audio_rules import IDENTITY_MIN_MARGIN  # noqa: E402
from members.b5_audio_embed import get_backend  # noqa: E402

SCHEMA = "b5-audio-linear-pilot-v1"
STATUS_PROMISING = "PROMISING_WEAK_LABEL_SMOKE"
STATUS_FAILED = "FAILED_WEAK_LABEL_SMOKE"
LABEL_PROVENANCE = "MACHINE_CAPTION_HARD_SIGNAL"
WINDOW_SECONDS = 5.0
WINDOW_COUNT = 4
MIN_WINDOW_RMS = 1e-4
MIN_HELDOUT_GENUINE_UNIT_ACCEPT = 0.90
MIN_HELDOUT_IMPOSTOR_UNIT_REJECT = 0.99
COHORT_N = 30
SELECTION_RULE = (
    "same 30-member failed-cosine cohort; lexicographically first four "
    "SOLO_TARGET/MATCH/keep clips; 2 train, 1 calibration, 1 test"
)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(doc):
    blob = json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _dump(doc, path):
    from moneyball_core import dump_atomic
    dump_atomic(doc, path, sort_keys=True, indent=1)


def freeze_split_manifest(verify, members):
    """Freeze four hard-match sources/member before any embedding or model outcome is seen."""
    members = sorted(set(str(member).strip() for member in members if str(member).strip()))
    selected = []
    mismatch = []
    for member in members:
        good = sorted(
            path.replace("\\", "/")
            for path, row in verify.items()
            if os.path.dirname(path.replace("\\", "/")) == member
            and row.get("speaker") == "SOLO_TARGET"
            and row.get("identity") == "MATCH"
            and row.get("keep") is True
        )
        if len(good) < 4:
            raise ValueError("%s has only %d hard-match clips; four are required" %
                             (member, len(good)))
        for index, path in enumerate(good[:4]):
            selected.append({
                "member": member,
                "path": path,
                "split": "train" if index < 2 else ("calibration" if index == 2 else "test"),
                "event_id": path,
                "label_provenance": LABEL_PROVENANCE,
            })
        mismatch.extend({
            "member": member,
            "path": path.replace("\\", "/"),
            "speaker": row.get("speaker"),
            "identity": row.get("identity"),
            "keep": bool(row.get("keep")),
            "label_provenance": LABEL_PROVENANCE,
        } for path, row in sorted(verify.items())
          if os.path.dirname(path.replace("\\", "/")) == member
          and row.get("identity") == "MISMATCH")
    return {
        "schema": "b5-audio-linear-split-manifest-v1",
        "status": linear.STATUS,
        "selection_rule": SELECTION_RULE,
        "cohort_members": members,
        "selected": selected,
        "caption_mismatch_clips": mismatch,
    }


def select_energy_windows(samples, sr, window_seconds=WINDOW_SECONDS,
                          window_count=WINDOW_COUNT):
    """Select one fixed complete window per temporal quartile, using RMS only within quartile."""
    samples = np.asarray(samples, dtype=np.float64).reshape(-1)
    sr = int(sr)
    window = int(round(float(window_seconds) * sr))
    if sr <= 0 or window <= 0:
        raise ValueError("positive sample rate and window duration are required")
    complete = samples.size // window
    if complete < int(window_count):
        raise ValueError("clip has %d complete windows; %d required" %
                         (complete, window_count))
    candidate_indices = np.arange(complete)
    groups = np.array_split(candidate_indices, int(window_count))
    selected = []
    for group in groups:
        scored = []
        for index in group.tolist():
            start = int(index * window)
            end = int(start + window)
            rms = float(np.sqrt(np.mean(np.square(samples[start:end]))))
            scored.append((rms, -start, start, end))
        rms, _, start, end = max(scored)
        if rms < MIN_WINDOW_RMS:
            raise ValueError("selected window RMS %.8f is below speech-energy floor %.8f" %
                             (rms, MIN_WINDOW_RMS))
        selected.append({"start": start, "end": end, "rms": rms})
    return selected


def claim_rows_for_split(head, embedded_rows, split):
    """Create explicit genuine and impostor claims; no alternative identity is returned."""
    classes = list(head["classes"])
    out = []
    for row in sorted(
            (item for item in embedded_rows if item["split"] == split),
            key=lambda item: (item["member"], item["event_id"], item["window_id"])):
        true_member = row["member"]
        claims = [true_member] + [member for member in classes if member != true_member]
        for claimed_member in claims:
            label = "genuine" if claimed_member == true_member else "impostor"
            claim_token = hashlib.sha256(claimed_member.encode("utf-8")).hexdigest()[:12]
            out.append({
                "trial_id": "%s|%s|%s|claim-%s" %
                            (split, row["event_id"], row["window_id"], claim_token),
                "corpus": "legacy_audio_pilot",
                "claimed_member": claimed_member,
                "true_member": true_member,
                "label": label,
                "sex": row.get("sex") or "UNKNOWN",
                "caption_mismatch": False,
                "event_id": row["event_id"],
                "window_id": row["window_id"],
                "split": split,
                "embedding": row["embedding"],
            })
    return out


def _load_cohort(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    members = sorted({str(row.get("expected_member") or row.get("member") or "").strip()
                      for row in rows})
    members = [member for member in members if member]
    if len(members) != COHORT_N:
        raise ValueError("failed-cosine cohort must contain exactly %d members, got %d" %
                         (COHORT_N, len(members)))
    baseline = {
        "source_path": os.path.abspath(path),
        "source_sha256": _sha256_file(path),
        "member_n": len(members),
        "row_n": len(rows),
        "correct_n": sum(str(row.get("correct")).lower() == "true" for row in rows),
        "identity_guard_pass_n": sum(row.get("identity_guard") == "PASS" for row in rows),
        "meaning": "failure archaeology only; never accepted as profile evidence",
    }
    return members, baseline


def _load_audio(path):
    import soundfile as sf
    samples, sr = sf.read(path, dtype="float32", always_2d=False)
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if int(sr) != 16000:
        raise ValueError("%s has sample rate %d; the pinned Wav2Vec path requires 16000" %
                         (path, int(sr)))
    return samples, int(sr)


def _cache_vector(cache_dir, cache_key, compute):
    os.makedirs(cache_dir, exist_ok=True)
    cache_name = hashlib.sha256(cache_key.encode("utf-8")).hexdigest() + ".npy"
    cache_path = os.path.join(cache_dir, cache_name)
    if os.path.isfile(cache_path):
        try:
            vector = np.load(cache_path, allow_pickle=False)
        except (ValueError, OSError, EOFError):
            vector = None  # truncated by an interrupted write; recompute
        if vector is not None and vector.ndim == 1 and vector.size:
            return vector.astype(np.float64), True
    vector = np.asarray(compute(), dtype=np.float64).reshape(-1)
    tmp_path = cache_path + ".tmp"
    with open(tmp_path, "wb") as handle:
        np.save(handle, vector, allow_pickle=False)
    os.replace(tmp_path, cache_path)
    return vector, False


def _embed_clip(data_root, row, backend, model_manifest_sha256, cache_dir,
                embedding_mode="unit"):
    full_path = os.path.join(data_root, "audio_pilot", row["path"].replace("/", os.sep))
    if not os.path.isfile(full_path):
        raise ValueError("audio clip is missing: %s" % full_path)
    clip_sha256 = _sha256_file(full_path)
    samples, sr = _load_audio(full_path)
    windows = select_energy_windows(samples, sr)
    embedded = []
    cache_hits = 0
    for index, window in enumerate(windows):
        start, end = window["start"], window["end"]
        window_source_sha256 = hashlib.sha256(
            ("%s:%d:%d" % (clip_sha256, start, end)).encode("ascii")).hexdigest()
        if embedding_mode not in ("unit", "raw"):
            raise ValueError("embedding_mode must be unit or raw")
        cache_key = "%s:%s:%s" % (
            model_manifest_sha256, embedding_mode, window_source_sha256)
        embed_function = backend.embed_raw if embedding_mode == "raw" else backend.embed
        vector, hit = _cache_vector(
            cache_dir, cache_key,
            lambda start=start, end=end: embed_function(samples[start:end], sr))
        cache_hits += int(hit)
        embedded.append({
            "member": row["member"],
            "split": row.get("split") or "caption_mismatch",
            "event_id": row["event_id"],
            "window_id": "%s-window-%d-%d" % (row["event_id"], start, end),
            "source_sha256": window_source_sha256,
            "clip_sha256": clip_sha256,
            "embedding": [float(value) for value in vector],
            "rms": window["rms"],
            "sex": "UNKNOWN",
        })
    return embedded, clip_sha256, cache_hits


def _training_sha256(rows):
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda item: (
            item["member"], item["event_id"], item["window_id"])):
        digest.update(row["member"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(row["event_id"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(row["source_sha256"].encode("ascii"))
        digest.update(np.asarray(row["embedding"], dtype="<f8").tobytes())
    return digest.hexdigest()


def _score_claim_rows(head, rows):
    return linear.build_claim_trials(head, rows, LABEL_PROVENANCE)


def _evaluate(scored_trials, decision_threshold, threshold_role="theta_accept"):
    by_unit = defaultdict(list)
    segment_counts = Counter()
    for row in scored_trials:
        accepted = (float(row["score"]) >= float(decision_threshold) and
                    float(row["margin_over_best_other"]) >= IDENTITY_MIN_MARGIN)
        segment_counts[(row["label"], accepted)] += 1
        by_unit[(row["event_id"], row["claimed_member"], row["label"])].append(accepted)
    unit_counts = Counter()
    for (_, _, label), decisions in by_unit.items():
        unit_counts[(label, all(decisions))] += 1
    genuine_segment_n = segment_counts[("genuine", True)] + segment_counts[("genuine", False)]
    impostor_segment_n = segment_counts[("impostor", True)] + segment_counts[("impostor", False)]
    genuine_unit_n = unit_counts[("genuine", True)] + unit_counts[("genuine", False)]
    impostor_unit_n = unit_counts[("impostor", True)] + unit_counts[("impostor", False)]
    return {
        "decision_threshold": float(decision_threshold),
        "threshold_role": threshold_role,
        "identity_min_margin": IDENTITY_MIN_MARGIN,
        "segment": {
            "genuine_n": genuine_segment_n,
            "genuine_accept_rate": (
                segment_counts[("genuine", True)] / float(genuine_segment_n)),
            "impostor_n": impostor_segment_n,
            "impostor_reject_rate": (
                segment_counts[("impostor", False)] / float(impostor_segment_n)),
        },
        "unit_all_windows_must_pass": {
            "genuine_n": genuine_unit_n,
            "genuine_accept_rate": unit_counts[("genuine", True)] / float(genuine_unit_n),
            "impostor_n": impostor_unit_n,
            "impostor_reject_rate": unit_counts[("impostor", False)] / float(impostor_unit_n),
        },
    }


def run(data_root, cohort_csv, model_dir, model_manifest, out_dir):
    data_root = os.path.abspath(data_root)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    members, raw_cosine_baseline = _load_cohort(cohort_csv)
    with open(os.path.join(data_root, "data", "members", "b5_audio_verify.json"), encoding="utf-8") as handle:
        verify = json.load(handle)
    manifest = freeze_split_manifest(verify, members)

    for row in manifest["selected"]:
        full_path = os.path.join(data_root, "audio_pilot", row["path"].replace("/", os.sep))
        row["source_sha256"] = _sha256_file(full_path)
    split_summary = linear.validate_source_disjoint_splits(manifest["selected"])
    manifest["split_summary"] = split_summary
    manifest_path = os.path.join(out_dir, "b5_audio_linear_split_manifest.json.candidate")
    _dump(manifest, manifest_path)
    split_manifest_sha256 = _sha256_file(manifest_path)

    model_manifest_sha256 = _sha256_file(model_manifest)
    backend = get_backend("wav2vec2", model_dir, model_manifest)
    cache_dir = os.path.join(out_dir, "b5_audio_linear_embedding_cache.candidate")
    embedded = []
    total_cache_hits = 0
    for index, row in enumerate(manifest["selected"], 1):
        rows, clip_sha256, cache_hits = _embed_clip(
            data_root, row, backend, model_manifest_sha256, cache_dir)
        if clip_sha256 != row["source_sha256"]:
            raise RuntimeError("clip changed after split manifest was frozen: %s" % row["path"])
        embedded.extend(rows)
        total_cache_hits += cache_hits
        print("embed selected %d/%d %s | cache %d/%d" %
              (index, len(manifest["selected"]), row["path"], cache_hits, WINDOW_COUNT),
              flush=True)

    train_rows = [row for row in embedded if row["split"] == "train"]
    head = linear.fit_linear_head(
        train_rows,
        training_sha256=_training_sha256(train_rows),
        split_manifest_sha256=split_manifest_sha256,
    )
    head["model_manifest_sha256"] = model_manifest_sha256
    head["window_protocol"] = {
        "window_seconds": WINDOW_SECONDS,
        "window_count_per_clip": WINDOW_COUNT,
        "selection": "one highest-RMS complete window per temporal quartile",
        "minimum_rms": MIN_WINDOW_RMS,
    }
    head_path = os.path.join(out_dir, "b5_audio_linear_head.json.candidate")
    _dump(head, head_path)

    calibration_claims = claim_rows_for_split(head, embedded, "calibration")
    mismatch_embedded = []
    for index, row in enumerate(manifest["caption_mismatch_clips"], 1):
        mismatch_row = dict(row, split="caption_mismatch", event_id=row["path"])
        rows, _, cache_hits = _embed_clip(
            data_root, mismatch_row, backend, model_manifest_sha256, cache_dir)
        mismatch_embedded.extend(rows)
        total_cache_hits += cache_hits
        print("embed caption mismatch %d/%d %s | cache %d/%d" %
              (index, len(manifest["caption_mismatch_clips"]), row["path"],
               cache_hits, WINDOW_COUNT), flush=True)
    for row in mismatch_embedded:
        calibration_claims.append({
            "trial_id": "caption-mismatch|%s|%s" % (row["event_id"], row["window_id"]),
            "corpus": "legacy_audio_pilot",
            "claimed_member": row["member"],
            "true_member": "UNKNOWN_MACHINE_MISMATCH",
            "label": "impostor",
            "sex": row.get("sex") or "UNKNOWN",
            "caption_mismatch": True,
            "event_id": row["event_id"],
            "window_id": row["window_id"],
            "split": "calibration",
            "embedding": row["embedding"],
        })
    calibration_trials = _score_claim_rows(head, calibration_claims)
    calibration_trials_path = os.path.join(
        out_dir, "b5_audio_linear_calibration_trials.json.candidate")
    _dump(calibration_trials, calibration_trials_path)
    thresholds = calibrate.calibrate(
        calibration_trials["trials"],
        source_sha256=_sha256_file(calibration_trials_path),
        label_provenance=LABEL_PROVENANCE,
        score_kind=linear.SCORE_KIND,
    )
    thresholds_path = os.path.join(
        out_dir, "b5_audio_linear_thresholds.json.candidate")
    _dump(thresholds, thresholds_path)

    test_claims = claim_rows_for_split(head, embedded, "test")
    test_trials = _score_claim_rows(head, test_claims)
    test_trials_path = os.path.join(out_dir, "b5_audio_linear_test_trials.json.candidate")
    _dump(test_trials, test_trials_path)
    heldout = _evaluate(test_trials["trials"], thresholds["theta_accept"])
    unit = heldout["unit_all_windows_must_pass"]
    measurement_bars = {
        "calibration_eer_rate_lte_0_05": thresholds["eer_rate"] <= 0.05,
        "heldout_genuine_unit_accept_gte_0_90":
            unit["genuine_accept_rate"] >= MIN_HELDOUT_GENUINE_UNIT_ACCEPT,
        "heldout_impostor_unit_reject_gte_0_99":
            unit["impostor_reject_rate"] >= MIN_HELDOUT_IMPOSTOR_UNIT_REJECT,
    }
    bars = dict(measurement_bars)
    bars.update({
        "human_ratified_labels": False,
        "caption_mismatch_n_gte_148":
            thresholds["caption_mismatch_n"] >= calibrate.MIN_CAPTION_MISMATCH_N,
        "calibration_artifact_consumable": thresholds["status"] == calibrate.STATUS_PASS,
    })
    status = STATUS_PROMISING if all(measurement_bars.values()) else STATUS_FAILED
    pilot = {
        "schema": SCHEMA,
        "status": status,
        "production_consumers": 0,
        "label_provenance": LABEL_PROVENANCE,
        "cohort_n": len(members),
        "raw_cosine_failure_archaeology": raw_cosine_baseline,
        "split_manifest_sha256": split_manifest_sha256,
        "model_manifest_sha256": model_manifest_sha256,
        "head_sha256": _sha256_file(head_path),
        "calibration_trials_sha256": _sha256_file(calibration_trials_path),
        "thresholds_sha256": _sha256_file(thresholds_path),
        "test_trials_sha256": _sha256_file(test_trials_path),
        "calibration": {
            "status": thresholds["status"],
            "eer_rate": thresholds["eer_rate"],
            "theta_eer": thresholds["theta_eer"],
            "theta_accept": thresholds["theta_accept"],
            "caption_mismatch_n": thresholds["caption_mismatch_n"],
            "caption_mismatch_rejection_rate":
                thresholds["caption_mismatch_rejection_rate"],
        },
        "heldout_test": heldout,
        "bars": bars,
        "cache": {
            "directory": cache_dir,
            "window_embeddings_total": len(embedded) + len(mismatch_embedded),
            "cache_hits": total_cache_hits,
        },
        "disposition": (
            "Machine-label laboratory evidence only. Even a promising measurement result "
            "cannot be consumed or merged into the production identity path until independent "
            "human ratification and the full caption-mismatch stop bar pass."
        ),
    }
    pilot_path = os.path.join(out_dir, "b5_audio_linear_pilot.json.candidate")
    _dump(pilot, pilot_path)
    print("linear pilot: %s | calibration EER %.4f | heldout genuine %.4f | "
          "heldout impostor reject %.4f" %
          (status, thresholds["eer_rate"], unit["genuine_accept_rate"],
           unit["impostor_reject_rate"]), flush=True)
    print("wrote %s" % pilot_path, flush=True)
    return pilot


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the frozen 30-member Wav2Vec linear-head weak-label smoke.")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--cohort-csv", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    return run(
        args.data_root, args.cohort_csv, args.model_dir, args.model_manifest, args.out_dir)


if __name__ == "__main__":
    main()
