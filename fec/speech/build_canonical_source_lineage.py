#!/usr/bin/env python3
"""Persist exact full-source hashes for historical canonical CNN SOTU clips.

The canonical accepted manifest is intentionally not rewritten.  This sidecar
adds the lineage field it predates, binding each SOTU row to the immutable full
source WAV and exact half-open frame interval needed for record supersession.
It reads and hashes media but never deletes, moves, or modifies it.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech import build_descriptive_speech as S


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canonical-manifest", type=Path,
        default=Path(r"C:\melange-data\member_clips\clips_manifest.jsonl"),
    )
    parser.add_argument("--out", type=Path, default=S.DEFAULT_CANONICAL_LINEAGE)
    parser.add_argument(
        "--receipt", type=Path, default=S.DEFAULT_CANONICAL_LINEAGE_RECEIPT,
    )
    return parser.parse_args(argv)


def build(canonical_manifest: Path, out: Path, receipt_path: Path):
    canonical_manifest = canonical_manifest.resolve()
    manifest_rows = S.read_jsonl(canonical_manifest)
    manifest_sha = S.sha256_file(canonical_manifest)
    sotu = [
        row for row in manifest_rows
        if S._canonical_cnn_lineage_kind(row) == "cnn-sotu"
    ]
    if not sotu:
        raise S.SpeechBuildError("canonical manifest contains no CNN SOTU rows")
    source_hashes = {}
    rows = []
    for canonical in sotu:
        canonical_id = S.row_sha256(canonical)
        S.wav_contract(canonical, verify_hash=True)
        source = Path(str(canonical.get("src_wav") or ""))
        if not source.is_absolute() or not source.is_file():
            raise S.SpeechBuildError("canonical SOTU full-source WAV is missing: %s" % source)
        channels, width, sample_rate, total_frames = S._wav_facts(source)
        start_frame, frame_count, declared_rate = S._canonical_frame_geometry(canonical)
        if (channels, width, sample_rate) != (1, 2, declared_rate):
            raise S.SpeechBuildError("canonical SOTU full-source WAV format is invalid")
        if start_frame + frame_count > total_frames:
            raise S.SpeechBuildError("canonical SOTU interval exceeds its full-source WAV")
        source_key = str(source.resolve())
        if source_key not in source_hashes:
            source_hashes[source_key] = S.sha256_file(source)
        row = {
            "schema": S.CANONICAL_LINEAGE_SCHEMA,
            "status": "VERIFIED_CANONICAL_SOURCE_LINEAGE",
            "canonical_manifest_sha256": manifest_sha,
            "canonical_row_sha256": canonical_id,
            "canonical_clip_audio_sha256": canonical["sha256"],
            "out_wav": str(Path(str(canonical["out_wav"])).resolve()),
            "source_wav": source_key,
            "source_audio_sha256": source_hashes[source_key],
            "source_sample_rate": sample_rate,
            "source_total_frames": total_frames,
            "source_start_frame": start_frame,
            "source_frame_count": frame_count,
            "source_end_frame_exclusive": start_frame + frame_count,
            "physical_cleanup_authorized": False,
        }
        row["lineage_row_sha256"] = S._row_field_sha256(row, "lineage_row_sha256")
        rows.append(row)
    rows.sort(key=lambda row: row["canonical_row_sha256"])
    S.atomic_jsonl(out.resolve(), rows)
    receipt = {
        "schema": S.CANONICAL_LINEAGE_RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canonical_manifest": str(canonical_manifest),
        "canonical_manifest_sha256": manifest_sha,
        "manifest": str(out.resolve()),
        "manifest_sha256": S.sha256_file(out.resolve()),
        "lineage_rows": len(rows),
        "canonical_sotu_rows": len(sotu),
        "canonical_sotu_sources": len(source_hashes),
        "canonical_axe_rows_verified": sum(
            S._canonical_cnn_lineage_kind(row) == "cnn-axe" for row in manifest_rows
        ),
        "canonical_sotu_row_ids_sha256": S._stable_sha256(sorted(
            row["canonical_row_sha256"] for row in rows
        )),
        "source_hashes_sha256": S._stable_sha256(sorted(source_hashes.values())),
        "all_relevant_canonical_rows_bound": True,
        "source_hashes_verified": True,
        "all_cnn_lineage_revalidated": True,
        "physical_cleanup_authorized": False,
    }
    S.atomic_json(receipt_path.resolve(), receipt)
    try:
        S.validate_canonical_source_lineage(
            manifest_rows, manifest_sha, out.resolve(), receipt_path.resolve(),
            required=True, verify_hash=True,
        )
    except Exception:
        receipt["status"] = "FAILED"
        receipt["source_hashes_verified"] = False
        receipt["all_cnn_lineage_revalidated"] = False
        S.atomic_json(receipt_path.resolve(), receipt)
        raise
    return receipt


def main(argv=None):
    args = parse_args(argv)
    try:
        receipt = build(args.canonical_manifest, args.out, args.receipt)
    except S.SpeechBuildError as exc:
        print("CANONICAL SOURCE LINEAGE FAILED: %s" % exc, file=sys.stderr, flush=True)
        return 2
    print("CANONICAL SOURCE LINEAGE PASS rows=%d sources=%d" % (
        receipt["canonical_sotu_rows"], receipt["canonical_sotu_sources"],
    ), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
