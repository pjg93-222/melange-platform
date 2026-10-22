"""Trim overlong legacy WJ WAVs to their accepted frame contract and rebind hashes.

This never pads audio.  It repairs only Washington Journal rows whose physical
WAV is longer than the stored accepted frame count by at most one second.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import wave

import numpy as np


DEFAULT_MANIFEST = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECEIPT = ROOT / "data/speech/wj_pcm_contract_repair_v1.receipt.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    descriptor, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        Path(raw).unlink(missing_ok=True)


def write_wav_atomic(path: Path, payload: bytes, rate: int) -> None:
    temporary = path.with_name(path.stem + ".pcm-contract.part" + path.suffix)
    with wave.open(str(temporary), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(payload)
    os.replace(temporary, path)


_WAVE_HEADER_BYTES = 44  # header written by write_wav_atomic (stdlib wave)
_SOURCE_HEADER_SLACK = 64  # legacy ffmpeg headers carry a LIST chunk (78 bytes observed)


def _is_prior_trim(row: dict, path: Path, facts: tuple, expected: int) -> bool:
    """True only when a hash mismatch is explained by this script's own earlier trim."""
    if facts != (1, 16000, 2):
        return False
    current = path.stat().st_size
    if current != _WAVE_HEADER_BYTES + 2 * expected:
        return False  # not the exact output shape of write_wav_atomic
    stored = row.get("bytes")
    if isinstance(stored, bool) or not isinstance(stored, int):
        return False
    shrink = stored - current
    return 0 < shrink <= 2 * 16000 + _SOURCE_HEADER_SLACK  # at most one second of PCM16 removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    manifest_before = sha256(args.manifest)
    repairs = []
    planned = []
    for row in rows:
        if row.get("outlet") != "CSPAN" or row.get("show") != "Washington Journal":
            continue
        raw_frames = row.get("frames")
        try:
            expected = int(raw_frames)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("WJ frame contract must be a positive integer") from exc
        if isinstance(raw_frames, bool) or expected <= 0 or (isinstance(raw_frames, float) and raw_frames != expected):
            raise RuntimeError("WJ frame contract must be a positive integer")
        try:
            start = float(row["start"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("WJ start contract must be finite") from exc
        if not math.isfinite(start):
            raise RuntimeError("WJ start contract must be finite")
        path = Path(str(row.get("out_wav") or ""))
        with wave.open(str(path), "rb") as source:
            facts = (source.getnchannels(), source.getframerate(), source.getsampwidth())
            actual = source.getnframes()
            if actual == expected:
                # a prior partial run may have trimmed the WAV without rebinding the manifest;
                # rebind only that case, never corrupted, replaced or edited audio
                if row.get("sha256") and sha256(path) != row["sha256"]:
                    if not _is_prior_trim(row, path, facts, expected):
                        raise RuntimeError(f"WJ WAV hash mismatch is not a prior trim: {path}")
                    planned.append((row, path, expected, actual, start))
                continue
            if facts != (1, 16000, 2):
                raise RuntimeError(f"WJ WAV contract mismatch: {path}")
            if actual < expected:
                raise RuntimeError(f"refusing to pad short WJ audio: {path}")
            if actual - expected > source.getframerate():
                raise RuntimeError(f"WJ overrun exceeds one-second repair ceiling: {path}")
        planned.append((row, path, expected, actual, start))

    for row, path, expected, actual, start in planned:
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels(), source.getframerate(), source.getsampwidth(), source.getnframes()) != (1, 16000, 2, actual):
                raise RuntimeError(f"WJ WAV contract changed after preflight: {path}")
            payload = source.readframes(expected)
        before_hash = sha256(path)
        if actual != expected:
            write_wav_atomic(path, payload, 16000)
        samples = np.frombuffer(payload, dtype="<i2").astype(np.float64) / 32768.0
        digest = sha256(path)
        row["bytes"] = path.stat().st_size
        row["dur"] = round(expected / 16000.0, 6)
        row["end"] = round(start + expected / 16000.0, 6)
        row["rms"] = float(math.sqrt(float(np.mean(samples * samples))))
        row["sha256"] = digest
        if Path(str(row.get("src_wav") or "")).resolve() == path.resolve():
            row["source_audio_sha256"] = digest
        repairs.append({
            "path": str(path.resolve()),
            "frames_before": actual,
            "frames_after": expected,
            "sha256_before": before_hash,
            "sha256_after": digest,
        })

    write_jsonl_atomic(args.manifest, rows)
    receipt = {
        "schema": "wj-pcm-contract-repair-receipt-v1",
        "status": "PASS",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "contract": {
            "padding_permitted": False,
            "maximum_trim_seconds": 1.0,
            "trim_keeps_member_turn_prefix": True,
        },
        "manifest_sha256_before": manifest_before,
        "manifest_sha256_after": sha256(args.manifest),
        "repaired_rows": len(repairs),
        "repairs": repairs,
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.receipt.with_name(args.receipt.name + ".part")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, args.receipt)
    print(json.dumps({"repaired_rows": len(repairs), "status": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
