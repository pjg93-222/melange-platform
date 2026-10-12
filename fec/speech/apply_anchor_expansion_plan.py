"""Apply a verified canonical-anchor expansion plan to a derived vault manifest."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fec.paths import ROOT as ROOT_VALUE
from fec.speech import source_media_recovery as SMR
from fec.speech import speaker_anchor_recovery as SAR


ROOT = Path(ROOT_VALUE)
DEFAULT_PLAN = ROOT / "data" / "speech" / "speaker_anchor_expansion_plan_v1.jsonl"
DEFAULT_PLAN_RECEIPT = ROOT / "data" / "speech" / "speaker_anchor_expansion_plan_v1.receipt.json"
DEFAULT_OUTPUT = ROOT / "data" / "speech" / "speaker_anchor_vault_expanded_v2.jsonl"
DEFAULT_RECEIPT = ROOT / "data" / "speech" / "speaker_anchor_vault_expanded_v2.receipt.json"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise SAR.IdentityRefused(f"expected object: {path}")
    return value


def row_hash(row: dict[str, Any]) -> str:
    body = dict(row)
    body.pop("anchor_row_sha256", None)
    return SMR.sha256_json(body)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_member: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        by_member[row["member"]].append(row)
    coverage: dict[str, dict[str, Any]] = {}
    eligible: list[str] = []
    for member, anchors in sorted(by_member.items()):
        events = {row["event_id"] for row in anchors}
        seconds = sum(float(row["duration_sec"]) for row in anchors)
        is_eligible = (
            len(anchors) >= SAR.MIN_TARGET_ANCHORS
            and len(events) >= SAR.MIN_TARGET_EVENTS
            and seconds >= SAR.MIN_TARGET_SECONDS
        )
        if is_eligible:
            eligible.append(member)
        coverage[member] = {
            "anchors": len(anchors), "events": len(events),
            "duration_sec": round(seconds, 6), "target_eligible": is_eligible,
        }
    eligible_set = set(eligible)
    for row in rows:
        row["target_eligible"] = row["member"] in eligible_set
        row["anchor_row_sha256"] = row_hash(row)
    return {
        "accepted_anchors": len(rows),
        "vaulted_speakers": len(by_member),
        "eligible_target_speakers": len(eligible),
        "eligible_targets": eligible,
        "duration_hours": round(sum(float(row["duration_sec"]) for row in rows) / 3600.0, 6),
        "minimum_target_anchors": SAR.MIN_TARGET_ANCHORS,
        "minimum_target_events": SAR.MIN_TARGET_EVENTS,
        "minimum_target_seconds": SAR.MIN_TARGET_SECONDS,
        "refused_references": [],
        "coverage": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-manifest", type=Path, default=SAR.ANCHOR_MANIFEST)
    parser.add_argument("--base-receipt", type=Path, default=SAR.ANCHOR_RECEIPT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--plan-receipt", type=Path, default=DEFAULT_PLAN_RECEIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    base_receipt = load_object(args.base_receipt)
    plan_receipt = load_object(args.plan_receipt)
    if (
        base_receipt.get("schema") != SAR.ANCHOR_RECEIPT_SCHEMA
        or base_receipt.get("manifest_sha256") != SMR.sha256_file(args.base_manifest)
        or plan_receipt.get("schema") != "speech-speaker-anchor-expansion-plan-receipt-v1"
        or plan_receipt.get("status") != "PASS"
        or plan_receipt.get("manifest_sha256") != SMR.sha256_file(args.plan)
        or plan_receipt.get("anchor_manifest_sha256") != SMR.sha256_file(args.base_manifest)
    ):
        raise SAR.IdentityRefused("anchor expansion inputs are stale or unbound")

    rows = SMR.read_jsonl(args.base_manifest, required=True)
    plan = SMR.read_jsonl(args.plan, required=True)
    existing_ids = {row["anchor_id"] for row in rows}
    existing_audio = {row.get("audio_sha256") for row in rows}
    added = 0
    for member_plan in plan:
        if member_plan.get("status") != "CAN_REACH_ELIGIBLE_FROM_CANONICAL":
            continue
        member = str(member_plan["member"])
        for selected in member_plan.get("selected_canonical_anchors") or []:
            path = Path(str(selected["path"]))
            duration, frames = SAR.wav_contract(path)
            digest = SMR.sha256_file(path)
            if digest != selected.get("audio_sha256") or frames != int(selected.get("frames") or -1):
                raise SAR.IdentityRefused(f"planned canonical anchor changed: {path}")
            if digest in existing_audio:
                continue
            anchor_id = "anchor:" + SMR.sha256_text("canonical:" + str(path.resolve()))[:24]
            if anchor_id in existing_ids:
                raise SAR.IdentityRefused(f"canonical anchor ID collision: {path}")
            row = {
                "schema": SAR.ANCHOR_SCHEMA,
                "anchor_id": anchor_id,
                "member": member,
                "event_id": str(selected["event_id"]),
                "path": str(path.resolve()),
                "relative_path": str(path.resolve()),
                "duration_sec": round(duration, 6),
                "frames": frames,
                "audio_sha256": digest,
                "label_contract": {
                    "speaker": "SOLO_TARGET",
                    "identity": "MATCH",
                    "keep": True,
                    "source": "accepted-canonical-caption-aligned-voice-reference",
                    "evidence": (
                        f"canonical accepted capture; lexical match={selected.get('canonical_match')}; "
                        f"outlet={selected.get('outlet')}; show={selected.get('show')}"
                    ),
                },
            }
            row["anchor_row_sha256"] = row_hash(row)
            rows.append(row)
            existing_ids.add(anchor_id)
            existing_audio.add(digest)
            added += 1

    summary = summarize(rows)
    receipt = SAR.persist_anchor_manifest(rows, summary, args.output, args.receipt)
    receipt.update({
        "base_manifest_sha256": SMR.sha256_file(args.base_manifest),
        "base_receipt_sha256": SMR.sha256_file(args.base_receipt),
        "expansion_plan_sha256": SMR.sha256_file(args.plan),
        "expansion_plan_receipt_sha256": SMR.sha256_file(args.plan_receipt),
        "canonical_anchors_added": added,
        "derived_vault": True,
    })
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SAR.IdentityRefused as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(2)
