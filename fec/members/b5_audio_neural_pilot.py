# -*- coding: utf-8 -*-
"""Fresh-cohort test of the paper-exact Adam-trained Wav2Vec single layer.

The prior 30-member cosine/logistic cohort is permanently consumed for diagnosis.  This experiment
uses every different member with six machine-hard-match sources: three train sources, one source for
early stopping, one for threshold calibration, and one untouched test source.  It uses raw
mean-pooled Wav2Vec embeddings, exactly the single-layer/Adam/cross-entropy/batch-8 method reported
by Mestre and Ryan.  Results remain weak-label research candidates with zero production consumers.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import members.b5_audio_calibrate as calibrate  # noqa: E402
import members.b5_audio_linear_pilot as common  # noqa: E402
import members.b5_audio_neural_head as neural  # noqa: E402
from members.b5_audio_embed import get_backend  # noqa: E402

SCHEMA = "b5-audio-neural-pilot-v1"
STATUS_PROMISING = "PROMISING_WEAK_LABEL_SMOKE"
STATUS_FAILED = "FAILED_WEAK_LABEL_SMOKE"
LABEL_PROVENANCE = "MACHINE_CAPTION_HARD_SIGNAL"
EXPECTED_FRESH_COHORT_N = 21
MIN_HELDOUT_GENUINE_UNIT_ACCEPT = 0.90
MIN_HELDOUT_IMPOSTOR_UNIT_REJECT = 0.99
SELECTION_RULE = (
    "all members outside the consumed 30-member cohort with at least six "
    "SOLO_TARGET/MATCH/keep clips; lexicographically first six clips; "
    "3 train, 1 early-stop validation, 1 calibration, 1 untouched test"
)


def freeze_fresh_manifest(verify, excluded_members, expected_cohort_n=EXPECTED_FRESH_COHORT_N):
    """Freeze the fresh six-source cohort without inspecting any model output."""
    excluded_members = {str(member).strip() for member in excluded_members}
    good_by_member = {}
    for path, row in verify.items():
        normalized = path.replace("\\", "/")
        member = os.path.dirname(normalized)
        if (member not in excluded_members and row.get("speaker") == "SOLO_TARGET"
                and row.get("identity") == "MATCH" and row.get("keep") is True):
            good_by_member.setdefault(member, []).append(normalized)
    cohort = sorted(member for member, paths in good_by_member.items() if len(paths) >= 6)
    if len(cohort) != int(expected_cohort_n):
        raise ValueError("fresh six-source cohort drift: expected %d members, got %d" %
                         (expected_cohort_n, len(cohort)))
    splits = ("train", "train", "train", "validation", "calibration", "test")
    selected = []
    for member in cohort:
        for path, split in zip(sorted(good_by_member[member])[:6], splits):
            selected.append({
                "member": member,
                "path": path,
                "event_id": path,
                "split": split,
                "label_provenance": LABEL_PROVENANCE,
            })
    return {
        "schema": "b5-audio-neural-split-manifest-v1",
        "status": neural.STATUS,
        "selection_rule": SELECTION_RULE,
        "excluded_consumed_members": sorted(excluded_members),
        "cohort_members": cohort,
        "selected": selected,
    }


def _validate_manifest(rows):
    expected = {"train": 3, "validation": 1, "calibration": 1, "test": 1}
    counts = Counter((row["member"], row["split"]) for row in rows)
    members = sorted({row["member"] for row in rows})
    for member in members:
        got = {split: counts[(member, split)] for split in sorted(expected)}
        if got != {split: expected[split] for split in sorted(expected)}:
            raise ValueError("%s split counts %r != %r" % (member, got, expected))
    events = [row["event_id"] for row in rows]
    if len(events) != len(set(events)):
        raise ValueError("event duplication/leakage in six-source manifest")
    sources = [row["source_sha256"] for row in rows]
    if len(sources) != len(set(sources)):
        raise ValueError("source duplication/leakage in six-source manifest")
    return {
        "members": members,
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "event_n": len(events),
        "source_n": len(sources),
    }


def _load_excluded(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    members = sorted({str(row.get("expected_member") or row.get("member") or "").strip()
                      for row in rows if row.get("expected_member") or row.get("member")})
    if len(members) != 30:
        raise ValueError("consumed cohort record must contain exactly 30 members")
    return set(members), {
        "path": os.path.abspath(path),
        "sha256": common._sha256_file(path),
        "member_n": len(members),
    }


def _claims(head, embedded_rows, split):
    return common.claim_rows_for_split(head, embedded_rows, split)


def run(data_root, consumed_cohort_csv, model_dir, model_manifest, out_dir):
    data_root = os.path.abspath(data_root)
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    excluded, consumed_record = _load_excluded(consumed_cohort_csv)
    verify_path = os.path.join(data_root, "data", "members", "b5_audio_verify.json")
    with open(verify_path, encoding="utf-8") as handle:
        verify = json.load(handle)
    manifest = freeze_fresh_manifest(verify, excluded)
    manifest["verify_sha256"] = common._sha256_file(verify_path)
    for row in manifest["selected"]:
        full_path = os.path.join(data_root, "audio_pilot", row["path"].replace("/", os.sep))
        row["source_sha256"] = common._sha256_file(full_path)
    manifest["split_summary"] = _validate_manifest(manifest["selected"])
    manifest_path = os.path.join(
        out_dir, "b5_audio_neural_split_manifest.json.candidate")
    common._dump(manifest, manifest_path)
    split_manifest_sha256 = common._sha256_file(manifest_path)

    model_manifest_sha256 = common._sha256_file(model_manifest)
    backend = get_backend("wav2vec2", model_dir, model_manifest)
    cache_dir = os.path.join(out_dir, "b5_audio_neural_raw_embedding_cache.candidate")
    embedded = []
    cache_hits_total = 0
    for index, row in enumerate(manifest["selected"], 1):
        rows, clip_sha256, cache_hits = common._embed_clip(
            data_root, row, backend, model_manifest_sha256, cache_dir,
            embedding_mode="raw")
        if clip_sha256 != row["source_sha256"]:
            raise RuntimeError("clip changed after manifest freeze: %s" % row["path"])
        embedded.extend(rows)
        cache_hits_total += cache_hits
        print("embed raw %d/%d %s | cache %d/%d" %
              (index, len(manifest["selected"]), row["path"],
               cache_hits, common.WINDOW_COUNT), flush=True)

    train_rows = [row for row in embedded if row["split"] == "train"]
    validation_rows = [row for row in embedded if row["split"] == "validation"]
    head = neural.fit_neural_head(
        train_rows,
        validation_rows,
        training_sha256=common._training_sha256(train_rows),
        split_manifest_sha256=split_manifest_sha256,
    )
    head["model_manifest_sha256"] = model_manifest_sha256
    head["window_protocol"] = {
        "window_seconds": common.WINDOW_SECONDS,
        "window_count_per_clip": common.WINDOW_COUNT,
        "selection": "one highest-RMS complete window per temporal quartile",
        "minimum_rms": common.MIN_WINDOW_RMS,
    }
    head_path = os.path.join(out_dir, "b5_audio_neural_head.json.candidate")
    common._dump(head, head_path)

    calibration_claims = _claims(head, embedded, "calibration")
    calibration_trials = neural.build_claim_trials(
        head, calibration_claims, LABEL_PROVENANCE)
    calibration_trials_path = os.path.join(
        out_dir, "b5_audio_neural_calibration_trials.json.candidate")
    common._dump(calibration_trials, calibration_trials_path)
    thresholds = calibrate.calibrate(
        calibration_trials["trials"],
        source_sha256=common._sha256_file(calibration_trials_path),
        label_provenance=LABEL_PROVENANCE,
        score_kind=neural.SCORE_KIND,
    )
    thresholds_path = os.path.join(
        out_dir, "b5_audio_neural_thresholds.json.candidate")
    common._dump(thresholds, thresholds_path)

    # This fresh cohort has zero machine-caption mismatch clips.  The generic calibration correctly
    # leaves theta_accept at 1.0 and fails its 148-negative bar.  For laboratory discrimination only,
    # held-out evaluation uses the calibration EER boundary; no consumer accepts this artifact.
    research_threshold = thresholds["theta_eer"]
    test_claims = _claims(head, embedded, "test")
    test_trials = neural.build_claim_trials(head, test_claims, LABEL_PROVENANCE)
    test_trials_path = os.path.join(
        out_dir, "b5_audio_neural_test_trials.json.candidate")
    common._dump(test_trials, test_trials_path)
    heldout = common._evaluate(
        test_trials["trials"], research_threshold,
        threshold_role="theta_eer research-only; theta_accept unavailable without mismatch negatives")
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
        "caption_mismatch_n_gte_148": False,
        "calibration_artifact_consumable": False,
    })
    status = STATUS_PROMISING if all(measurement_bars.values()) else STATUS_FAILED
    pilot = {
        "schema": SCHEMA,
        "status": status,
        "production_consumers": 0,
        "label_provenance": LABEL_PROVENANCE,
        "method": {
            "representation": "raw 768-dimensional mean-pooled facebook/wav2vec2-base",
            "classifier": "single neural linear layer",
            "optimizer": "Adam",
            "learning_rate": 0.001,
            "loss": "cross_entropy",
            "batch_size": 8,
            "early_stopping_patience": 3,
            "source": "Mestre and Ryan 2026, section 4.3 and footnote 9",
        },
        "cohort_n": len(manifest["cohort_members"]),
        "consumed_cohort_exclusion": consumed_record,
        "split_manifest_sha256": split_manifest_sha256,
        "model_manifest_sha256": model_manifest_sha256,
        "head_sha256": common._sha256_file(head_path),
        "calibration_trials_sha256": common._sha256_file(calibration_trials_path),
        "thresholds_sha256": common._sha256_file(thresholds_path),
        "test_trials_sha256": common._sha256_file(test_trials_path),
        "calibration": {
            "status": thresholds["status"],
            "eer_rate": thresholds["eer_rate"],
            "theta_eer": thresholds["theta_eer"],
            "theta_accept": thresholds["theta_accept"],
            "caption_mismatch_n": thresholds["caption_mismatch_n"],
        },
        "heldout_test": heldout,
        "research_threshold_rule": (
            "theta_eer fixed on calibration split; diagnostic only because this fresh cohort has "
            "zero caption-mismatch negatives and the consumable theta_accept bar cannot run"
        ),
        "bars": bars,
        "cache": {
            "directory": cache_dir,
            "window_embeddings_total": len(embedded),
            "cache_hits": cache_hits_total,
        },
        "disposition": (
            "Fresh-member, fresh-source weak-label laboratory evidence only. Production remains "
            "blocked on human identity ratification and the 148 caption-mismatch bar."
        ),
    }
    pilot_path = os.path.join(out_dir, "b5_audio_neural_pilot.json.candidate")
    common._dump(pilot, pilot_path)
    print("neural pilot: %s | calibration EER %.4f | heldout genuine %.4f | "
          "heldout impostor reject %.4f" %
          (status, thresholds["eer_rate"], unit["genuine_accept_rate"],
           unit["impostor_reject_rate"]), flush=True)
    print("wrote %s" % pilot_path, flush=True)
    return pilot


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the fresh six-source paper-exact Wav2Vec neural-head smoke.")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--consumed-cohort-csv", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--model-manifest", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    return run(
        args.data_root, args.consumed_cohort_csv, args.model_dir,
        args.model_manifest, args.out_dir)


if __name__ == "__main__":
    main()
