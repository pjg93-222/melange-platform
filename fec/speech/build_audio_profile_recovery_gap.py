"""Build a recovery-routing ledger from the atomic full-member audio census.

The output deliberately uses the existing ``speech-member-profile-gap-v1``
schema understood by :mod:`source_media_recovery`.  It is a routing artifact,
not an identity acceptance artifact: local holdings can be discovered and
aligned for a cold speaker, but they cannot be promoted until the independent
identity gates recorded in the census are satisfied.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CENSUS = ROOT / "data/speech/congress_audio_profile_census_full_v1.jsonl"
DEFAULT_INVENTORY = (
    ROOT / "data/speech/source_media_inventory_cumulative_current_snapshot.jsonl"
)
DEFAULT_OUTPUT = ROOT / "data/speech/congress_audio_profile_recovery_gap_v1.jsonl"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    finally:
        Path(raw).unlink(missing_ok=True)


def holdings_by_member(
    inventory: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    holdings: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "package_ids": set(),
            "turns": 0,
            "words": 0,
            "by_source": Counter(),
            "by_discovery_status": Counter(),
        }
    )
    for package in inventory:
        source = str(package.get("source_family") or "unknown")
        status = str((package.get("discovery") or {}).get("status") or "unknown")
        package_id = str(package.get("package_id") or "")
        for item in package.get("members") or []:
            member = str(item.get("member") or "").strip()
            if not member:
                continue
            row = holdings[member]
            if package_id:
                row["package_ids"].add(package_id)
            row["turns"] += int(item.get("turns") or 0)
            row["words"] += int(item.get("words") or 0)
            row["by_source"][source] += 1
            row["by_discovery_status"][status] += 1
    return holdings


def is_target(row: dict[str, Any], target: str) -> bool:
    if target == "general":
        return row.get("verified_audio_profile") is not True
    if target == "strict":
        return row.get("strict_identity_prerequisite_ready") is not True
    if target == "nonfloor":
        return row.get("verified_nonfloor_descriptive_profile") is not True
    raise ValueError(f"unsupported target: {target}")


def build_rows(
    census: Iterable[dict[str, Any]],
    inventory: Iterable[dict[str, Any]],
    target: str,
) -> list[dict[str, Any]]:
    holdings = holdings_by_member(inventory)
    rows: list[dict[str, Any]] = []
    for census_row in sorted(census, key=lambda value: str(value.get("member") or "")):
        member = str(census_row.get("member") or "").strip()
        if not member:
            raise ValueError("census row has no member")
        strict = census_row.get("strict_identity_prerequisite") or {}
        basis = census_row.get("verified_audio_basis") or {}
        local = holdings.get(member) or {
            "package_ids": set(), "turns": 0, "words": 0,
            "by_source": Counter(), "by_discovery_status": Counter(),
        }
        target_recovery = is_target(census_row, target)
        exact_windows = int(basis.get("canonical_any_setting_exact_windows") or 0)
        events = int(strict.get("verified_events") or 0)
        seconds = float(strict.get("verified_unique_audio_seconds") or 0.0)
        rows.append({
            # Compatibility with source_media_recovery.load_member_gap.
            "schema": "speech-member-profile-gap-v1",
            "member": member,
            "bioguide": census_row.get("bioguide"),
            "chamber": census_row.get("chamber"),
            "target_kind": target,
            "target_recovery": target_recovery,
            "status": (
                "TARGET_RECOVERY" if target_recovery else "SUFFICIENT_FOR_TARGET"
            ),
            # Existing recovery priority fields.  They describe deficits only;
            # they are never interpreted as identity evidence.
            "clips_needed": max(0, 5 - exact_windows) if target == "general" else 0,
            "minutes_needed": round(max(0.0, 180.0 - seconds) / 60.0, 6),
            "events_needed": max(0, 3 - events),
            "verified_ledgers": {
                "general_profile": census_row.get("verified_audio_profile") is True,
                "nonfloor_profile": (
                    census_row.get("verified_nonfloor_descriptive_profile") is True
                ),
                "strict_identity_prerequisite": (
                    census_row.get("strict_identity_prerequisite_ready") is True
                ),
                "canonical_any_setting_exact_windows": exact_windows,
                "canonical_nonfloor_exact_windows": int(
                    basis.get("canonical_nonfloor_exact_windows") or 0
                ),
                "strict_verified_events": events,
                "strict_verified_unique_audio_seconds": round(seconds, 6),
            },
            "local_unprocessed_holdings": {
                "packages": len(local["package_ids"]),
                "turns": int(local["turns"]),
                "words": int(local["words"]),
                "by_source": dict(sorted(local["by_source"].items())),
                "by_discovery_status": dict(
                    sorted(local["by_discovery_status"].items())
                ),
            },
            "safety": {
                "routing_only": True,
                "speaker_identity_authority": False,
                "candidate_promotion_authorized": False,
                "ecapa_cold_start_positive_identity_authority": False,
            },
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--target", choices=("general", "strict", "nonfloor"), default="strict")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    census = read_jsonl(args.census)
    inventory = read_jsonl(args.inventory)
    rows = build_rows(census, inventory, args.target)
    write_atomic(
        args.output,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
    )
    targets = [row for row in rows if row["target_recovery"]]
    receipt = {
        "schema": "congress-audio-profile-recovery-gap-receipt-v1",
        "status": "PASS_ROUTING_ONLY",
        "generated_at": utc_now(),
        "target_kind": args.target,
        "contract": {
            "general_profile_floor_exact_windows": 5,
            "strict_identity_minimum_events": 3,
            "strict_identity_minimum_seconds": 180.0,
            "ledgers_are_separate": True,
            "routing_is_identity_acceptance": False,
        },
        "inputs": {
            "census": {"path": str(args.census.resolve()), "sha256": sha256_file(args.census)},
            "inventory": {
                "path": str(args.inventory.resolve()), "sha256": sha256_file(args.inventory)
            },
        },
        "coverage": {
            "universe": len(rows),
            "target_members": len(targets),
            "target_members_with_local_packages": sum(
                row["local_unprocessed_holdings"]["packages"] > 0 for row in targets
            ),
            "target_local_packages": sum(
                row["local_unprocessed_holdings"]["packages"] for row in targets
            ),
            "target_local_turns": sum(
                row["local_unprocessed_holdings"]["turns"] for row in targets
            ),
            "target_local_words": sum(
                row["local_unprocessed_holdings"]["words"] for row in targets
            ),
        },
        "output": {"path": str(args.output.resolve()), "sha256": sha256_file(args.output)},
    }
    receipt_path = args.output.with_suffix(".receipt.json")
    write_atomic(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt["coverage"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
