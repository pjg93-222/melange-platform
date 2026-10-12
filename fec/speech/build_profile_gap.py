"""Build the deterministic per-member speech-profile sufficiency ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fec.paths import ROOT as ROOT_VALUE


ROOT = Path(ROOT_VALUE)
DEFAULT_ARTIFACT = ROOT / "data" / "speech" / "speech_descriptive_v1.json"
DEFAULT_ROSTER = ROOT / "data" / "engine" / "congestion.json"
DEFAULT_OUTPUT = ROOT / "data" / "speech" / "member_speech_profile_gap_v1.jsonl"
DEFAULT_ANCHOR_RECEIPT = ROOT / "data" / "speech" / "speaker_anchor_vault_v1.receipt.json"
DEFAULT_ANCHOR_CALIBRATION = ROOT / "data" / "speech" / "speaker_anchor_calibration_v1.json"
MIN_NONFLOOR_CLIPS = 5
VOICEPRINT_REFERENCE_MINUTES = 3.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def build_rows(artifact: dict[str, Any], roster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    members = artifact.get("members") or {}
    names = sorted({str(row.get("member") or "") for row in roster if row.get("member")})
    rows: list[dict[str, Any]] = []
    for name in names:
        profile = members.get(name) or {}
        settings = profile.get("by_setting") or {}
        nonfloor = {
            setting: value for setting, value in settings.items()
            if setting != "house_floor" and isinstance(value, dict)
        }
        clips = sum(int(value.get("clips") or 0) for value in nonfloor.values())
        episodes = sum(int(value.get("episodes") or 0) for value in nonfloor.values())
        minutes = sum(float(value.get("minutes") or 0.0) for value in nonfloor.values())
        # The attached accuracy report documents two different gates:
        # descriptive habits clear the reliability bar at five units, while
        # 180 seconds is the verified-clean voiceprint reference floor. The
        # latter belongs to identity anchoring and must not inflate the
        # display-only descriptive profile requirement.
        sufficient = clips >= MIN_NONFLOOR_CLIPS
        rows.append({
            "schema": "speech-member-profile-gap-v1",
            "member": name,
            "status": (
                "SUFFICIENT" if sufficient
                else "NO_NONFLOOR_AUDIO" if clips == 0
                else "NEEDS_MORE"
            ),
            "target_recovery": not sufficient,
            "nonfloor_clips": clips,
            "nonfloor_episodes": episodes,
            "nonfloor_minutes": round(minutes, 3),
            "clips_needed": max(0, MIN_NONFLOOR_CLIPS - clips),
            "minutes_needed": 0.0,
            "observed_nonfloor_minutes_ge_voiceprint_reference_floor": (
                minutes >= VOICEPRINT_REFERENCE_MINUTES
            ),
            "voiceprint_reference_minutes_shortfall": round(
                max(0.0, VOICEPRINT_REFERENCE_MINUTES - minutes), 3
            ),
            "nonfloor_settings": sorted(nonfloor),
            "total_clips": int((profile.get("coverage") or {}).get("clips") or 0),
            "total_minutes": round(float((profile.get("coverage") or {}).get("minutes") or 0.0), 3),
            "profile_floor": {
                "minimum_nonfloor_clips": MIN_NONFLOOR_CLIPS,
                "house_floor_counts_for_profile": False,
                "voiceprint_reference_floor_applied_to_profile": False,
            },
        })
    return rows


def annotate_anchor_readiness(
    rows: list[dict[str, Any]], anchor_receipt: dict[str, Any]
) -> None:
    coverage = anchor_receipt.get("coverage") or {}
    for row in rows:
        anchor = coverage.get(row["member"])
        if anchor is None:
            status = "MISSING"
        elif anchor.get("target_eligible") is True:
            status = "ELIGIBLE"
        else:
            status = "INELIGIBLE"
        target = row["target_recovery"] is True
        row["identity_anchor_status"] = status
        row["identity_anchor_coverage"] = (
            {
                "anchors": int(anchor.get("anchors") or 0),
                "events": int(anchor.get("events") or 0),
                "duration_sec": round(float(anchor.get("duration_sec") or 0.0), 6),
            }
            if anchor is not None else {"anchors": 0, "events": 0, "duration_sec": 0.0}
        )
        row["media_recovery_ready"] = target and status == "ELIGIBLE"
        row["anchor_recovery_required"] = target and status != "ELIGIBLE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--anchor-receipt", type=Path, default=DEFAULT_ANCHOR_RECEIPT)
    parser.add_argument("--anchor-calibration", type=Path, default=DEFAULT_ANCHOR_CALIBRATION)
    args = parser.parse_args()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8-sig"))
    roster = json.loads(args.roster.read_text(encoding="utf-8-sig"))
    rows = build_rows(artifact, roster)
    anchor_receipt = json.loads(args.anchor_receipt.read_text(encoding="utf-8-sig"))
    anchor_calibration = json.loads(args.anchor_calibration.read_text(encoding="utf-8-sig"))
    if (
        anchor_receipt.get("status") != "PASS"
        or anchor_calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or anchor_calibration.get("anchor_manifest_sha256")
        != anchor_receipt.get("manifest_sha256")
        or anchor_calibration.get("anchor_vectors_sha256")
        != anchor_receipt.get("combined_vectors_sha256")
    ):
        raise RuntimeError("anchor receipt/calibration chain is not identity-ready")
    annotate_anchor_readiness(rows, anchor_receipt)
    write_atomic(args.output, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    counts = {status: sum(row["status"] == status for row in rows) for status in (
        "SUFFICIENT", "NEEDS_MORE", "NO_NONFLOOR_AUDIO"
    )}
    receipt = {
        "schema": "speech-member-profile-gap-receipt-v1",
        "status": "PASS",
        "artifact_sha256": sha256_file(args.artifact),
        "roster_sha256": sha256_file(args.roster),
        "anchor_receipt_sha256": sha256_file(args.anchor_receipt),
        "anchor_calibration_sha256": sha256_file(args.anchor_calibration),
        "manifest": str(args.output.resolve()),
        "manifest_sha256": sha256_file(args.output),
        "members": len(rows),
        "sufficient": counts["SUFFICIENT"],
        "needs_more": counts["NEEDS_MORE"],
        "no_nonfloor_audio": counts["NO_NONFLOOR_AUDIO"],
        "target_recovery": sum(row["target_recovery"] for row in rows),
        "media_recovery_ready": sum(row["media_recovery_ready"] for row in rows),
        "anchor_recovery_required": sum(row["anchor_recovery_required"] for row in rows),
        "profile_minimum_nonfloor_clips": MIN_NONFLOOR_CLIPS,
        "voiceprint_reference_floor_minutes": VOICEPRINT_REFERENCE_MINUTES,
        "voiceprint_reference_floor_applied_to_profile": False,
        "reliability_basis": (
            "speech habits clear the documented 0.70 reliability bar at five units; "
            "floor-only prosody is excluded; the separate 180-second voiceprint "
            "reference floor is not a descriptive-profile requirement"
        ),
    }
    receipt_path = args.output.with_suffix(".receipt.json")
    write_atomic(receipt_path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
