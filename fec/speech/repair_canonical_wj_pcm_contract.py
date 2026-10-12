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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8-sig").splitlines() if line]
    manifest_before = sha256(args.manifest)
    repairs = []
    for row in rows:
        if row.get("outlet") != "CSPAN" or row.get("show") != "Washington Journal":
            continue
        path = Path(str(row.get("out_wav") or ""))
        expected = int(row.get("frames") or 0)
        with wave.open(str(path), "rb") as source:
            facts = (source.getnchannels(), source.getframerate(), source.getsampwidth())
            actual = source.getnframes()
            if actual == expected:
                continue
            if facts != (1, 16000, 2):
                raise RuntimeError(f"WJ WAV contract mismatch: {path}")
            if actual < expected:
                raise RuntimeError(f"refusing to pad short WJ audio: {path}")
            if actual - expected > source.getframerate():
                raise RuntimeError(f"WJ overrun exceeds one-second repair ceiling: {path}")
            payload = source.readframes(expected)
        before_hash = sha256(path)
        write_wav_atomic(path, payload, 16000)
        samples = np.frombuffer(payload, dtype="<i2").astype(np.float64) / 32768.0
        digest = sha256(path)
        row["bytes"] = path.stat().st_size
        row["dur"] = round(expected / 16000.0, 6)
        row["end"] = round(float(row["start"]) + expected / 16000.0, 6)
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
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"repaired_rows": len(repairs), "status": "PASS"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
