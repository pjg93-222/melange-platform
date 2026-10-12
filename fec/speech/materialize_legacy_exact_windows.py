#!/usr/bin/env python3
"""Account for or materialize active canonical speech as exact 8-second units.

This is deliberately a sidecar operation.  It does not reinterpret the legacy
accepted-clip manifest and it does not modify the descriptive artifact.  The
descriptive artifact supplies the authoritative active/superseded partition;
this command then partitions every active, non-House-floor parent WAV into
adjacent 128,000-frame children plus one explicitly recorded sub-window tail.

``--mode account`` hashes every child PCM and the canonical WAV bytes without
writing child WAVs.  ``--mode materialize`` additionally writes those exact
canonical child WAVs to a new, previously absent directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import ROOT


SCHEMA = "speech-canonical-exact-window-v1"
RECEIPT_SCHEMA = "speech-canonical-exact-window-receipt-v1"
EXCLUSION_SCHEMA = "speech-canonical-exact-window-exclusion-v1"
SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHANNELS = 1
WINDOW_SECONDS = 8
WINDOW_FRAMES = SAMPLE_RATE * WINDOW_SECONDS


class ExactWindowError(RuntimeError):
    pass


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def row_sha256(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def stable_sha256(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ExactWindowError(f"invalid/missing JSON: {path}") from exc


def read_jsonl(path: Path):
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ExactWindowError(f"missing JSONL: {path}") from exc
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ExactWindowError(f"invalid JSONL {path}:{number}") from exc
        if not isinstance(row, dict):
            raise ExactWindowError(f"non-object JSONL {path}:{number}")
        rows.append(row)
    return rows


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ) + "\n")
    os.replace(temporary, path)


def is_house_floor(row: dict) -> bool:
    return str(row.get("outlet") or "").upper() == "HOUSELIVE"


def accepted_partition(
    manifest_rows: list[dict], manifest_sha256: str, descriptive: dict
) -> tuple[list[tuple[str, dict]], list[dict]]:
    """Validate and return the artifact's active/superseded canonical partition."""
    source = descriptive.get("source") or {}
    contract = descriptive.get("contract") or {}
    supersession = source.get("canonical_supersession") or {}
    if (
        descriptive.get("schema") != "speech-descriptive-v1"
        or source.get("manifest_sha256") != manifest_sha256
        or contract.get("canonical_source_interval_supersession_required") is not True
        or contract.get("floor_audio_in_headline") is not False
        or supersession.get("schema") != "speech-canonical-record-supersession-v1"
        or supersession.get("physical_cleanup_authorized") is not False
    ):
        raise ExactWindowError(
            "descriptive artifact does not bind the manifest's locked partition"
        )
    records = supersession.get("records") or []
    if (
        not isinstance(records, list)
        or int(supersession.get("superseded_rows", -1)) != len(records)
        or supersession.get("supersession_rows_sha256") != stable_sha256(records)
    ):
        raise ExactWindowError("canonical supersession ledger is stale")

    indexed: dict[str, dict] = {}
    for row in manifest_rows:
        digest = row_sha256(row)
        if digest in indexed:
            raise ExactWindowError("canonical manifest contains a duplicate row identity")
        indexed[digest] = row
    superseded_ids = set()
    for record in records:
        digest = str(record.get("canonical_manifest_row_sha256") or "")
        parent = indexed.get(digest)
        if (
            not parent
            or digest in superseded_ids
            or record.get("action")
            != "CANONICAL_RECORD_SUPERSEDED_SOURCE_INTERVAL_OVERLAP"
            or record.get("canonical_clip_audio_sha256") != parent.get("sha256")
            or record.get("member") != parent.get("member")
            or os.path.normcase(str(record.get("out_wav") or ""))
            != os.path.normcase(str(parent.get("out_wav") or ""))
            or not record.get("superseded_by")
        ):
            raise ExactWindowError("canonical supersession row does not bind its parent")
        superseded_ids.add(digest)
    if len(superseded_ids) != len(records):
        raise ExactWindowError("supersession ledger is not a unique manifest subset")
    active = [(digest, row) for digest, row in indexed.items()
              if digest not in superseded_ids]
    if len(active) + len(records) != len(manifest_rows):
        raise ExactWindowError("active and superseded rows do not partition the manifest")
    return active, records


def validate_parent_wav(row: dict) -> tuple[Path, int]:
    path = Path(str(row.get("out_wav") or ""))
    if not path.is_absolute() or not path.is_file():
        raise ExactWindowError(f"accepted parent WAV missing: {path}")
    try:
        with wave.open(str(path), "rb") as handle:
            facts = (
                handle.getnchannels(), handle.getframerate(),
                handle.getsampwidth(), handle.getnframes(),
            )
    except (OSError, wave.Error) as exc:
        raise ExactWindowError(f"accepted parent WAV is invalid: {path}") from exc
    frames = int(row.get("frames", -1))
    if facts != (CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, frames):
        raise ExactWindowError(f"accepted parent WAV/frame contract failed: {path}")
    expected = str(row.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(path) != expected:
        raise ExactWindowError(f"accepted parent WAV SHA256 mismatch: {path}")
    return path, frames


def lineage_root(row: dict, parent_id: str) -> dict:
    source_sha = str(row.get("source_audio_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", source_sha):
        start = int(round(float(row.get("start") or 0) * SAMPLE_RATE))
        return {
            "scope": "immutable_full_source_audio",
            "source_audio_sha256": source_sha,
            "source_start_frame": start,
            "source_wav": str(row.get("src_wav") or ""),
        }
    return {
        "scope": "immutable_accepted_parent_clip",
        "source_audio_sha256": str(row["sha256"]),
        "source_start_frame": 0,
        "source_wav": str(row["out_wav"]),
        "accepted_parent_manifest_row_sha256": parent_id,
    }


def child_lineage(row: dict, parent_id: str, index: int) -> dict:
    relative_start = index * WINDOW_FRAMES
    root = lineage_root(row, parent_id)
    source_start = int(root["source_start_frame"]) + relative_start
    window_id = f"canonical:{parent_id}:window:{index}"
    lineage = {
        "schema": "speech-canonical-exact-window-lineage-v1",
        "window_id": window_id,
        "parent_manifest_row_sha256": parent_id,
        "parent_audio_sha256": str(row["sha256"]),
        "parent_relative_start_frame": relative_start,
        "source_lineage_scope": root["scope"],
        "source_audio_sha256": root["source_audio_sha256"],
        "source_start_frame": source_start,
        "source_frame_count": WINDOW_FRAMES,
        "sample_rate": SAMPLE_RATE,
    }
    lineage["source_frame_identity_sha256"] = stable_sha256({
        "source_audio_sha256": lineage["source_audio_sha256"],
        "source_start_frame": source_start,
        "source_frame_count": WINDOW_FRAMES,
        "sample_rate": SAMPLE_RATE,
    })
    lineage["lineage_sha256"] = stable_sha256(lineage)
    return lineage


def canonical_wav_bytes(pcm: bytes) -> bytes:
    if len(pcm) != WINDOW_FRAMES * SAMPLE_WIDTH:
        raise ExactWindowError("exact child PCM has the wrong byte count")
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)
    return output.getvalue()


def safe_member_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return slug or "unknown_member"


def process(
    manifest_path: Path,
    descriptive_path: Path,
    out_manifest: Path,
    exclusions_path: Path,
    receipt_path: Path,
    *,
    mode: str = "account",
    audio_dir: Path | None = None,
) -> tuple[list[dict], list[dict], dict]:
    if mode not in {"account", "materialize"}:
        raise ExactWindowError(f"unsupported mode: {mode}")
    if mode == "materialize" and audio_dir is None:
        raise ExactWindowError("materialize mode requires --audio-dir")
    if mode == "account" and audio_dir is not None:
        raise ExactWindowError("account mode must not name --audio-dir")

    manifest_path = manifest_path.resolve()
    descriptive_path = descriptive_path.resolve()
    manifest_sha = sha256_file(manifest_path)
    rows = read_jsonl(manifest_path)
    descriptive = read_json(descriptive_path)
    active, superseded = accepted_partition(rows, manifest_sha, descriptive)

    temporary_audio_dir = None
    final_audio_dir = None
    if mode == "materialize":
        final_audio_dir = audio_dir.resolve()
        if final_audio_dir.exists():
            raise ExactWindowError(
                f"refusing to overwrite existing exact-window directory: {final_audio_dir}"
            )
        final_audio_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary_audio_dir = final_audio_dir.with_name(
            final_audio_dir.name + f".{os.getpid()}.tmp"
        )
        if temporary_audio_dir.exists():
            raise ExactWindowError(f"temporary audio directory exists: {temporary_audio_dir}")
        temporary_audio_dir.mkdir()

    output_rows: list[dict] = []
    exclusions: list[dict] = []
    seen_pcm: set[str] = set()
    seen_source_frames: set[str] = set()
    parent_frames_accounted = Counter()
    try:
        for parent_id, row in sorted(active, key=lambda item: item[0]):
            frames = int(row.get("frames", -1))
            if is_house_floor(row):
                exclusions.append({
                    "schema": EXCLUSION_SCHEMA,
                    "reason": "HOUSE_FLOOR_EXCLUDED_FROM_HEADLINE",
                    "parent_manifest_row_sha256": parent_id,
                    "member": str(row.get("member") or ""),
                    "outlet": str(row.get("outlet") or ""),
                    "excluded_frames": frames,
                })
                continue
            path, frames = validate_parent_wav(row)
            complete_windows, tail_frames = divmod(frames, WINDOW_FRAMES)
            with wave.open(str(path), "rb") as handle:
                for index in range(complete_windows):
                    pcm = handle.readframes(WINDOW_FRAMES)
                    if len(pcm) != WINDOW_FRAMES * SAMPLE_WIDTH:
                        raise ExactWindowError(f"short PCM read from accepted parent: {path}")
                    lineage = child_lineage(row, parent_id, index)
                    source_identity = lineage["source_frame_identity_sha256"]
                    pcm_sha = hashlib.sha256(pcm).hexdigest()
                    if source_identity in seen_source_frames:
                        raise ExactWindowError(
                            f"duplicate exact source-frame child: {lineage['window_id']}"
                        )
                    if pcm_sha in seen_pcm:
                        raise ExactWindowError(
                            f"duplicate exact child PCM: {lineage['window_id']}"
                        )
                    seen_source_frames.add(source_identity)
                    seen_pcm.add(pcm_sha)
                    wav_bytes = canonical_wav_bytes(pcm)
                    audio_sha = hashlib.sha256(wav_bytes).hexdigest()
                    relative = None
                    if temporary_audio_dir is not None:
                        relative = Path(safe_member_slug(str(row.get("member") or ""))) / (
                            f"{parent_id[:24]}__window_{index:05d}.wav"
                        )
                        destination = temporary_audio_dir / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(wav_bytes)
                    record = {
                        "schema": SCHEMA,
                        "status": "MATERIALIZED" if mode == "materialize" else "ACCOUNTED",
                        "window_id": lineage["window_id"],
                        "member": str(row.get("member") or ""),
                        "outlet": str(row.get("outlet") or ""),
                        "show": row.get("show"),
                        "setting": "non_house_floor",
                        "frames": WINDOW_FRAMES,
                        "duration_seconds": WINDOW_SECONDS,
                        "sample_rate": SAMPLE_RATE,
                        "channels": CHANNELS,
                        "sample_width_bytes": SAMPLE_WIDTH,
                        "pcm_sha256": pcm_sha,
                        "audio_sha256": audio_sha,
                        "output_wav": (
                            str((final_audio_dir / relative).resolve())
                            if final_audio_dir is not None else None
                        ),
                        "lineage": lineage,
                    }
                    record["window_row_sha256"] = stable_sha256(record)
                    output_rows.append(record)
                    parent_frames_accounted[parent_id] += WINDOW_FRAMES
                tail = handle.readframes(tail_frames)
                if len(tail) != tail_frames * SAMPLE_WIDTH:
                    raise ExactWindowError(f"short tail read from accepted parent: {path}")
            if tail_frames:
                exclusions.append({
                    "schema": EXCLUSION_SCHEMA,
                    "reason": "TRAILING_FRAGMENT_SHORTER_THAN_EXACT_WINDOW",
                    "parent_manifest_row_sha256": parent_id,
                    "member": str(row.get("member") or ""),
                    "outlet": str(row.get("outlet") or ""),
                    "parent_relative_start_frame": complete_windows * WINDOW_FRAMES,
                    "excluded_frames": tail_frames,
                    "excluded_pcm_sha256": hashlib.sha256(tail).hexdigest(),
                })
                parent_frames_accounted[parent_id] += tail_frames
            if parent_frames_accounted[parent_id] != frames:
                raise ExactWindowError(f"parent frame partition is incomplete: {parent_id}")
        if temporary_audio_dir is not None:
            os.replace(temporary_audio_dir, final_audio_dir)
            temporary_audio_dir = None
    finally:
        if temporary_audio_dir is not None and temporary_audio_dir.is_dir():
            shutil.rmtree(temporary_audio_dir)

    output_rows.sort(key=lambda row: row["window_id"])
    exclusions.sort(key=lambda row: (
        str(row.get("reason") or ""),
        str(row.get("parent_manifest_row_sha256") or ""),
    ))
    active_nonfloor = [(digest, row) for digest, row in active if not is_house_floor(row)]
    active_floor = [(digest, row) for digest, row in active if is_house_floor(row)]
    tail_rows = [row for row in exclusions if row["reason"].startswith("TRAILING_")]
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "contract": {
            "window_frames": WINDOW_FRAMES,
            "window_seconds": WINDOW_SECONDS,
            "sample_rate": SAMPLE_RATE,
            "active_nonfloor_parents_partitioned": True,
            "within_parent_windows_nonoverlapping": True,
            "source_frame_identities_unique": True,
            "pcm_identities_unique": True,
            "tails_shorter_than_one_window_excluded_explicitly": True,
            "superseded_canonical_rows_excluded": True,
            "house_floor_excluded_from_headline": True,
            "legacy_manifest_semantics_modified": False,
        },
        "inputs": {
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha,
            "manifest_rows": len(rows),
            "descriptive_artifact": str(descriptive_path),
            "descriptive_artifact_sha256": sha256_file(descriptive_path),
        },
        "partition": {
            "active_rows": len(active),
            "superseded_rows": len(superseded),
            "active_nonfloor_parents": len(active_nonfloor),
            "active_house_floor_parents_excluded": len(active_floor),
            "exact_nonfloor_windows": len(output_rows),
            "exact_nonfloor_frames": len(output_rows) * WINDOW_FRAMES,
            "tail_exclusions": len(tail_rows),
            "tail_excluded_frames": sum(row["excluded_frames"] for row in tail_rows),
            "active_nonfloor_parent_frames": sum(int(row["frames"])
                                                   for _, row in active_nonfloor),
            "active_house_floor_frames_excluded": sum(int(row["frames"])
                                                        for _, row in active_floor),
        },
        "outputs": {
            "manifest": str(out_manifest.resolve()),
            "manifest_rows_sha256": stable_sha256(output_rows),
            "exclusions": str(exclusions_path.resolve()),
            "exclusion_rows_sha256": stable_sha256(exclusions),
            "audio_dir": str(final_audio_dir) if final_audio_dir is not None else None,
        },
    }
    partition = receipt["partition"]
    if (
        partition["exact_nonfloor_frames"] + partition["tail_excluded_frames"]
        != partition["active_nonfloor_parent_frames"]
    ):
        raise ExactWindowError("receipt does not account for every active nonfloor frame")
    atomic_jsonl(out_manifest.resolve(), output_rows)
    atomic_jsonl(exclusions_path.resolve(), exclusions)
    receipt["outputs"]["manifest_sha256"] = sha256_file(out_manifest.resolve())
    receipt["outputs"]["exclusions_sha256"] = sha256_file(exclusions_path.resolve())
    if final_audio_dir is not None:
        written = sorted(final_audio_dir.rglob("*.wav"))
        if len(written) != len(output_rows):
            raise ExactWindowError("materialized WAV count differs from exact-window ledger")
    atomic_json(receipt_path.resolve(), receipt)
    return output_rows, exclusions, receipt


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--descriptive-artifact", type=Path, required=True)
    parser.add_argument("--out-manifest", type=Path, required=True)
    parser.add_argument("--exclusions", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--mode", choices=("account", "materialize"), default="account")
    parser.add_argument("--audio-dir", type=Path)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        rows, _, receipt = process(
            args.manifest, args.descriptive_artifact, args.out_manifest,
            args.exclusions, args.receipt, mode=args.mode, audio_dir=args.audio_dir,
        )
    except ExactWindowError as exc:
        print(f"EXACT WINDOW BUILD FAILED: {exc}", file=sys.stderr, flush=True)
        return 2
    print(
        "EXACT WINDOW PASS mode=%s windows=%d members=%d" % (
            args.mode, len(rows), len({row["member"] for row in rows}),
        ),
        flush=True,
    )
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
