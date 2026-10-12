#!/usr/bin/env python3
"""Build the fail-closed exact-window profile gap under the published v2 gate.

The legacy exact-window ledger is already accepted/manual-identity speech.  A
recovered broadcast window is added only when (a) its member is eligible in the
strict raw-anchor receipt, (b) its persisted exhaustive comparison passes the
published global v2 score and margin floors as part of a run of at least three
consecutive windows, and (c) that exact window exists in the finalized WAV
ledger.  The newer per-claim v3 ``passed`` field is deliberately ignored.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


SCHEMA = "speech-locked-exact-profile-gap-v1"
RECEIPT_SCHEMA = "speech-locked-exact-profile-gap-receipt-v1"
MIN_RUN_WINDOWS = 3
PROFILE_FLOOR = 5
LOCKED_MIN_TARGET_BEST_SCORE = 0.595722615
LOCKED_MIN_BEST_ANCHOR_MARGIN = 0.344785426
LOCKED_SCORING_CONTRACT_SHA256 = (
    "831106d49a8efe9bf36dcc80f5b9310ce2b154141cf857c8f1daef4328c23a24"
)


class GapError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_sha256(value) -> str:
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise GapError(f"invalid/missing JSON: {path}") from exc


def read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise GapError(f"missing JSONL: {path}") from exc
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise GapError(f"invalid JSONL {path}:{number}") from exc
        if not isinstance(row, dict):
            raise GapError(f"non-object JSONL {path}:{number}")
        rows.append(row)
    return rows


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ) + "\n")
    os.replace(temporary, path)


def qualifying_locked_windows(
    identities: list[dict], eligible: set[str], score_floor: float,
    margin_floor: float,
) -> tuple[list[dict], int]:
    retained: list[dict] = []
    run_count = 0
    for decision in identities:
        member = str(decision.get("claimed_member") or "")
        if member not in eligible:
            continue
        windows = list(decision.get("windows") or [])
        index = 0
        while index < len(windows):
            window = windows[index]
            passes = (
                str(window.get("winner") or "") == member
                and float(window.get("target_best") or -1.0) >= score_floor
                and float(window.get("best_anchor_margin") or -1.0) >= margin_floor
            )
            if not passes:
                index += 1
                continue
            start = index
            while index < len(windows):
                window = windows[index]
                if not (
                    str(window.get("winner") or "") == member
                    and float(window.get("target_best") or -1.0) >= score_floor
                    and float(window.get("best_anchor_margin") or -1.0) >= margin_floor
                ):
                    break
                index += 1
            if index - start < MIN_RUN_WINDOWS:
                continue
            run_count += 1
            for window in windows[start:index]:
                retained.append({
                    "member": member,
                    "parent_clip_id": str(decision.get("clip_id") or ""),
                    "identity_window_index": int(window.get("window_index", -1)),
                })
    return retained, run_count


def validate_locked_calibration(calibration: dict) -> tuple[float, float]:
    """Refuse policy drift while retaining the published locked-v2 label."""
    score_floor = float(calibration.get("minimum_target_best_score") or -1.0)
    margin_floor = float(calibration.get("minimum_best_anchor_margin") or -1.0)
    if (
        calibration.get("schema") != "speech-speaker-anchor-calibration-v2"
        or calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or int(calibration.get("false_accepts", -1)) != 0
        or score_floor != LOCKED_MIN_TARGET_BEST_SCORE
        or margin_floor != LOCKED_MIN_BEST_ANCHOR_MARGIN
        or calibration.get("scoring_contract_sha256")
        != LOCKED_SCORING_CONTRACT_SHA256
    ):
        raise GapError("published locked-v2 calibration floors or contract drifted")
    return score_floor, margin_floor


def build(args: argparse.Namespace) -> tuple[list[dict], dict]:
    paths = {
        name: Path(value).resolve() for name, value in {
            "roster": args.roster,
            "legacy": args.legacy_manifest,
            "legacy_receipt": args.legacy_receipt,
            "identity": args.identity_manifest,
            "identity_receipt": args.identity_receipt,
            "final": args.final_manifest,
            "final_receipt": args.final_receipt,
            "calibration": args.calibration,
            "anchor_receipt": args.anchor_receipt,
        }.items()
    }
    roster_raw = read_json(paths["roster"])
    if not isinstance(roster_raw, list):
        raise GapError("roster must be a JSON list")
    roster = [str(row.get("member") or "") for row in roster_raw]
    if "" in roster or len(roster) != len(set(roster)):
        raise GapError("roster contains a missing or duplicate member")

    legacy = read_jsonl(paths["legacy"])
    legacy_receipt = read_json(paths["legacy_receipt"])
    if (
        legacy_receipt.get("schema") != "speech-canonical-exact-window-receipt-v1"
        or legacy_receipt.get("status") != "PASS"
        or legacy_receipt.get("outputs", {}).get("manifest_sha256")
        != sha256_file(paths["legacy"])
        or legacy_receipt.get("contract", {}).get("house_floor_excluded_from_headline")
        is not True
    ):
        raise GapError("legacy exact-window receipt is stale")

    identities = read_jsonl(paths["identity"])
    identity_receipt = read_json(paths["identity_receipt"])
    if (
        identity_receipt.get("status") != "PASS"
        or identity_receipt.get("manifest_sha256") != sha256_file(paths["identity"])
        or identity_receipt.get("all_anchor_comparisons_persisted") is not True
    ):
        raise GapError("identity receipt is stale or lacks persisted comparisons")
    finals = read_jsonl(paths["final"])
    final_receipt = read_json(paths["final_receipt"])
    if (
        final_receipt.get("status") != "PASS"
        or final_receipt.get("manifest_sha256") != sha256_file(paths["final"])
        or final_receipt.get("independent_identity_window_finalization") is not True
        or final_receipt.get("final_window_audio_equals_identity_tested_window_required")
        is not True
        or final_receipt.get("whole_final_audio_equals_identity_candidate_required")
        is not False
        or final_receipt.get("partial_final_audio_equals_identity_tested_run_required")
        is not False
    ):
        raise GapError("exact-window final receipt is stale or semantically inaccurate")

    calibration = read_json(paths["calibration"])
    anchor_receipt = read_json(paths["anchor_receipt"])
    score_floor, margin_floor = validate_locked_calibration(calibration)
    eligible = set(anchor_receipt.get("eligible_targets") or [])
    if (
        anchor_receipt.get("status") != "PASS"
        or int(anchor_receipt.get("minimum_target_anchors", -1)) != 3
        or int(anchor_receipt.get("minimum_target_events", -1)) != 3
        or int(anchor_receipt.get("minimum_target_seconds", -1)) != 180
        or len(eligible) != int(anchor_receipt.get("eligible_target_speakers", -1))
        or calibration.get("anchor_vectors_sha256")
        != identity_receipt.get("anchor_vectors_sha256")
        or calibration.get("calibration_window_manifest_sha256")
        != identity_receipt.get("calibration_window_manifest_sha256")
    ):
        raise GapError("published locked-v2 identity evidence is stale or incompatible")

    locked, locked_runs = qualifying_locked_windows(
        identities, eligible, score_floor, margin_floor
    )
    finalized = {
        (str(row.get("parent_clip_id") or ""),
         int(row.get("identity_window_index", -1))): row
        for row in finals
    }
    if len(finalized) != len(finals):
        raise GapError("final ledger contains a duplicate parent/window identity")
    locked_final = [row for row in locked if (
        row["parent_clip_id"], row["identity_window_index"]
    ) in finalized]

    legacy_counts = Counter(str(row.get("member") or "") for row in legacy)
    recovered_counts = Counter(row["member"] for row in locked_final)
    unknown = (set(legacy_counts) | set(recovered_counts)) - set(roster)
    if unknown:
        raise GapError("exact-window ledgers contain out-of-roster members: "
                       + ", ".join(sorted(unknown)))
    rows = []
    for member in roster:
        legacy_n = legacy_counts[member]
        recovered_n = recovered_counts[member]
        total = legacy_n + recovered_n
        rows.append({
            "schema": SCHEMA,
            "member": member,
            "legacy_exact_windows": legacy_n,
            "recovered_locked_v2_final_windows": recovered_n,
            "exact_verified_windows": total,
            "profile_floor": PROFILE_FLOOR,
            "status": "VERIFIED_PROFILE" if total >= PROFILE_FLOOR
                      else "NEEDS_MORE_VERIFIED_WINDOWS",
        })
    rows.sort(key=lambda row: row["member"])
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "profile_floor_exact_windows": PROFILE_FLOOR,
            "window_seconds": 8,
            "house_floor_excluded": True,
            "recovered_identity_policy": "published-global-v2-locked-floors",
            "v3_per_claim_pass_field_ignored": True,
            "only_physically_finalized_recovered_windows_counted": True,
        },
        "inputs": {name: {"path": str(path), "sha256": sha256_file(path)}
                   for name, path in paths.items()},
        "locked_identity": {
            "minimum_target_best_score": score_floor,
            "minimum_best_anchor_margin": margin_floor,
            "minimum_consecutive_windows": MIN_RUN_WINDOWS,
            "strict_anchor_eligible_members": len(eligible),
            "counterfactual_qualifying_runs": locked_runs,
            "counterfactual_qualifying_windows": len(locked),
            "physically_finalized_intersection_windows": len(locked_final),
            "qualifying_but_not_v3_finalized_windows": len(locked) - len(locked_final),
            "calibration_scoring_contract_sha256":
                calibration.get("scoring_contract_sha256"),
        },
        "coverage": {
            "roster_members": len(roster),
            "represented_members": sum(
                1 for row in rows if row["exact_verified_windows"] > 0
            ),
            "verified_profiles": sum(
                1 for row in rows if row["status"] == "VERIFIED_PROFILE"
            ),
            "needs_more_profiles": sum(
                1 for row in rows if row["status"] != "VERIFIED_PROFILE"
            ),
            "legacy_exact_windows": len(legacy),
            "recovered_locked_v2_final_windows": len(locked_final),
        },
        "output": {
            "manifest": str(Path(args.output).resolve()),
            "rows_sha256": stable_sha256(rows),
        },
    }
    atomic_jsonl(Path(args.output).resolve(), rows)
    receipt["output"]["manifest_sha256"] = sha256_file(Path(args.output).resolve())
    atomic_json(Path(args.receipt).resolve(), receipt)
    return rows, receipt


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", required=True)
    parser.add_argument("--legacy-manifest", required=True)
    parser.add_argument("--legacy-receipt", required=True)
    parser.add_argument("--identity-manifest", required=True)
    parser.add_argument("--identity-receipt", required=True)
    parser.add_argument("--final-manifest", required=True)
    parser.add_argument("--final-receipt", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--anchor-receipt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipt", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        rows, receipt = build(parse_args(argv))
    except GapError as exc:
        print(f"LOCKED EXACT GAP FAILED: {exc}")
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if rows else 3


if __name__ == "__main__":
    raise SystemExit(main())
