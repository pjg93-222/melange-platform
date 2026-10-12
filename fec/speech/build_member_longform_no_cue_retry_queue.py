#!/usr/bin/env python3
"""Build a second-source queue for recordings with no literal name cue.

This queue never promotes identity.  It selects a different, politically
contextual long-form candidate so the in-band name gate can be retried without
discarding the first acquired recording.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from fec.speech import discover_member_longform_youtube as discovery
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CUES = Path(r"C:\melange-data\member_longform_identity_cues_v1.jsonl")
DEFAULT_DISCOVERY = ROOT / "data/speech/member_longform_no_cue_retry_discovery_v1.jsonl"
DEFAULT_ACQUISITION = ROOT / "data/speech/member_longform_acquisition_v1.jsonl"
DEFAULT_OVERRIDES = ROOT / "data/speech/member_longform_reviewed_source_overrides_v1.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_longform_no_cue_retry_queue_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_no_cue_retry_queue_v1.receipt.json"

ROLE_WORDS = {
    "rep", "representative", "congressman", "congresswoman", "senator",
    "congress", "congressional", "candidate", "capitol", "assemblymember",
}
INTERVIEW_PHRASES = ("interview", "podcast", "conversation", "one on one", "sit down")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def title_name_phrases(member: str) -> set[str]:
    parts = discovery.normalized(member).split()
    if len(parts) < 2:
        return set()
    first, surname = parts[0], parts[-1]
    return {
        " ".join((candidate, surname))
        for candidate in {first, *discovery.FIRST_NAME_ALIASES.get(first, ())}
    }


def contextual_score(member: str, row: dict) -> tuple[int, int, int, str]:
    title = discovery.normalized(str(row.get("title") or ""))
    channel = discovery.normalized(str(row.get("channel") or ""))
    words = set((title + " " + channel).split())
    name_in_title = any(phrase in title for phrase in title_name_phrases(member))
    role = bool(words & ROLE_WORDS)
    interview = any(phrase in title for phrase in INTERVIEW_PHRASES)
    score = int(row.get("metadata_relevance_score") or 0)
    score += 24 if name_in_title else 0
    score += 12 if role else 0
    score += 5 if interview else 0
    return score, int(name_in_title), int(role), str(row.get("title") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cues", type=Path, default=DEFAULT_CUES)
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--acquisition", type=Path, default=DEFAULT_ACQUISITION)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    failed = {
        row["member"] for row in SMR.read_jsonl(args.cues, required=True)
        if row.get("status") == "NO_FULL_NAME_CUE_FOUND"
    }
    overridden = {
        row["member"] for row in SMR.read_jsonl(args.overrides)
    } if args.overrides.exists() else set()
    current = {
        row["member"]: row for row in SMR.read_jsonl(args.acquisition, required=True)
    }
    grouped: dict[str, list[dict]] = {}
    for row in SMR.read_jsonl(args.discovery, required=True):
        grouped.setdefault(str(row["member"]), []).append(row)

    rows = []
    skipped_overrides = failed & overridden
    for member in sorted(failed - overridden):
        existing = current.get(member) or {}
        alternatives = [
            row for row in grouped.get(member, [])
            if row.get("video_id") != existing.get("source_id")
            and row.get("url") != existing.get("url")
        ]
        alternatives.sort(key=lambda row: contextual_score(member, row), reverse=True)
        if not alternatives:
            continue
        compact = [{
            "source_type": "youtube", "url": row.get("url"),
            "video_id": row.get("video_id"), "title": row.get("title"),
            "channel": row.get("channel"), "duration_seconds": row.get("duration_seconds"),
            "candidate_rank": row.get("candidate_rank"),
            "metadata_relevance_score": row.get("metadata_relevance_score"),
            "contextual_retry_score": contextual_score(member, row)[0],
        } for row in alternatives[:6]]
        rows.append({
            "schema": "member-longform-no-cue-retry-queue-v1",
            "status": "SECOND_SOURCE_CANDIDATE_READY_FOR_ACQUISITION",
            "member": member,
            "primary": compact[0],
            "alternatives": compact,
            "candidate_count": len(alternatives),
            "acquisition_route": "youtube_audio_download",
            "next_step": "download_hash_transcribe_repeat_in_band_name_gate",
            "contract": {
                "first_recording_discarded": False,
                "search_metadata_establishes_identity": False,
                "profile_effect_before_promotion": "NONE",
            },
        })

    SMR.write_jsonl_atomic(args.out, rows)
    receipt = {
        "schema": "member-longform-no-cue-retry-queue-receipt-v1",
        "status": "PASS_RETRY_QUEUE_ONLY",
        "generated_at": utc_now(),
        "counts": {
            "no_cue_members": len(failed),
            "deferred_to_reviewed_primary_override": len(skipped_overrides),
            "second_source_queued": len(rows),
            "without_second_source_candidate": len(failed - overridden) - len(rows),
        },
        "contract": {
            "second_source_is_identity_unverified": True,
            "first_recording_is_preserved": True,
            "profile_effect": "NONE_UNTIL_FULL_PROMOTION",
        },
        "inputs": {
            "cues_sha256": SMR.sha256_file(args.cues),
            "discovery_sha256": SMR.sha256_file(args.discovery),
            "acquisition_sha256": SMR.sha256_file(args.acquisition),
            "overrides_sha256": SMR.sha256_file(args.overrides),
        },
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
