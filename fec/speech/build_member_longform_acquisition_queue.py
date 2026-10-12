#!/usr/bin/env python3
"""Reconcile verified, YouTube, Apple, and general-web interview sources."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from fec.speech.source_media_recovery import replace_atomic_with_retry


ROOT = Path(__file__).resolve().parents[2]
ROSTER = ROOT / "data/members/profile_basis_v2.json"
YOUTUBE = ROOT / "data/speech/member_longform_youtube_discovery_v1.jsonl"
APPLE = ROOT / "data/speech/member_longform_discovery_v1.jsonl"
WEB = ROOT / "data/speech/member_longform_general_web_candidates_v1.jsonl"
OVERRIDES = ROOT / "data/speech/member_longform_reviewed_source_overrides_v1.jsonl"
VERIFIED = ROOT / "data/speech/member_interview_descriptive_v1.json"
OUT = ROOT / "data/speech/member_longform_acquisition_queue_v1.jsonl"
RECEIPT = ROOT / "data/speech/member_longform_acquisition_queue_v1.receipt.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_atomic(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    replace_atomic_with_retry(temporary, path)


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    replace_atomic_with_retry(temporary, path)


def compact_youtube(row: dict) -> dict:
    return {
        "source_type": "youtube", "url": row.get("url"), "video_id": row.get("video_id"),
        "title": row.get("title"), "channel": row.get("channel"),
        "published": row.get("published"),
        "duration_seconds": row.get("duration_seconds"), "candidate_rank": row.get("candidate_rank"),
        "metadata_relevance_score": row.get("metadata_relevance_score"),
    }


def compact_apple(row: dict) -> dict:
    return {
        "source_type": "apple_directory_audio", "url": row.get("reference_url"),
        "audio_url": row.get("audio_url"), "title": row.get("title"), "channel": row.get("show"),
        "published": row.get("published"),
        "duration_seconds": row.get("duration_seconds"), "candidate_rank": row.get("candidate_rank"),
        "metadata_relevance_score": row.get("metadata_relevance_score"),
    }


def compact_web(row: dict) -> dict:
    return {
        "source_type": row.get("resolved_source_type") or "general_web",
        "url": row.get("resolved_url") or row.get("url"),
        "provider_page_url": row.get("url"),
        "video_id": row.get("video_id"), "audio_url": row.get("audio_url"),
        "title": row.get("title"),
        "channel": row.get("provider"), "published": row.get("published"),
        "duration_seconds": row.get("duration_seconds"),
        "candidate_rank": 1, "metadata_relevance_score": None,
    }


def compact_override(row: dict) -> dict:
    return {
        "source_type": row["source_type"], "url": row.get("url"),
        "video_id": row.get("video_id"), "audio_url": row.get("audio_url"),
        "title": row.get("title"), "channel": row.get("channel"),
        "published": row.get("published"),
        "duration_seconds": row.get("duration_seconds"), "candidate_rank": 0,
        "metadata_relevance_score": row.get("metadata_relevance_score"),
        "reviewed_replacement": True, "review_reason": row.get("review_reason"),
    }


def main() -> int:
    roster = sorted(json.loads(ROSTER.read_text(encoding="utf-8-sig"))["members"])
    grouped = {member: [] for member in roster}
    for row in read_jsonl(YOUTUBE):
        grouped[row["member"]].append(compact_youtube(row))
    for row in read_jsonl(APPLE):
        grouped[row["member"]].append(compact_apple(row))
    for row in read_jsonl(WEB):
        grouped[row["member"]].append(compact_web(row))
    reviewed_override_members = set()
    if OVERRIDES.exists():
        for row in read_jsonl(OVERRIDES):
            grouped[row["member"]].append(compact_override(row))
            reviewed_override_members.add(row["member"])
    verified_doc = json.loads(VERIFIED.read_text(encoding="utf-8-sig"))
    verified_by_member = {}
    for row in verified_doc.get("verified_sources") or []:
        verified_by_member.setdefault(row["member"], []).append({
            "source_type": "verified_longform", "url": row.get("url"), "title": row.get("title"),
            "channel": row.get("channel"), "duration_seconds": row.get("measured_duration"),
            "source_id": row.get("source_id"), "identity_method": row.get("identity_method"),
        })

    rows = []
    for member in roster:
        alternatives = grouped[member]
        alternatives.sort(key=lambda row: (
            0 if row.get("reviewed_replacement") else 1,
            {"youtube": 0, "apple_directory_audio": 1, "general_web": 2}.get(row["source_type"], 9),
            int(row.get("candidate_rank") or 99),
            -(int(row.get("metadata_relevance_score") or 0)),
        ))
        # A reviewed replacement exists because automated discovery was shown
        # to be unsafe for this member (for example, a homonym, opponent-only
        # episode, or debate).  Do not let acquisition silently fall through
        # from that reviewed source to the same unreviewed pool.  A failed
        # reviewed source must remain visible and receive another reviewed
        # replacement instead of producing plausible-looking wrong-person
        # audio.
        reviewed_alternatives = [
            row for row in alternatives if row.get("reviewed_replacement")
        ]
        if reviewed_alternatives:
            alternatives = reviewed_alternatives
        verified_sources = verified_by_member.get(member) or []
        if verified_sources:
            primary = verified_sources[0]
            status = "VERIFIED_LONGFORM_SOURCE_AVAILABLE"
            route = "already_verified"
            next_step = "review_additional_full_turns_and_regenerate_profile"
        elif alternatives:
            primary = alternatives[0]
            status = "CANDIDATE_READY_FOR_ACQUISITION"
            route = {
                "youtube": "youtube_audio_download",
                "apple_directory_audio": "direct_episode_audio_download",
                "general_web": "provider_page_media_resolution",
            }[primary["source_type"]]
            next_step = "download_hash_asr_in_band_name_full_turn_review_speaker_gate"
        else:
            raise SystemExit(f"no long-form interview candidate for {member}")
        rows.append({
            "schema": "member-longform-acquisition-queue-v1",
            "member": member,
            "status": status,
            "primary": primary,
            "acquisition_route": route,
            "next_step": next_step,
            "verified_sources": verified_sources,
            "candidate_count": len(alternatives),
            "alternatives": alternatives[:6],
            "contract": {
                "search_metadata_establishes_identity": False,
                "profile_effect_before_promotion": "NONE",
                "semantic_unit": "complete member turn through speaker handoff; no duration maximum",
            },
        })
    write_jsonl_atomic(OUT, rows)
    receipt = {
        "schema": "member-longform-acquisition-queue-receipt-v1",
        "status": "PASS_FULL_ROSTER_HAS_SOURCE_OR_CANDIDATE",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "counts": {
            "roster_members": len(roster),
            "queued_members": len(rows),
            "verified_members": sum(row["status"] == "VERIFIED_LONGFORM_SOURCE_AVAILABLE" for row in rows),
            "candidate_members": sum(row["status"] == "CANDIDATE_READY_FOR_ACQUISITION" for row in rows),
            "missing_members": sum(not row.get("primary") for row in rows),
            "reviewed_source_overrides": len(reviewed_override_members),
            "primary_youtube": sum(row["primary"]["source_type"] == "youtube" for row in rows),
            "primary_apple": sum(row["primary"]["source_type"] == "apple_directory_audio" for row in rows),
            "primary_general_web": sum(row["primary"]["source_type"] == "general_web" for row in rows),
        },
        "contract": {
            "every_member_has_source_or_candidate": all(row.get("primary") for row in rows),
            "candidate_is_not_acquired_or_verified": True,
            "promotion_requires_audio_hash_asr_in_band_name_full_turns_and_speaker_gate": True,
        },
        "inputs": {path.name: sha256(path) for path in (ROSTER, YOUTUBE, APPLE, WEB, OVERRIDES, VERIFIED)},
        "output": {"path": str(OUT.resolve()), "sha256": sha256(OUT)},
    }
    write_json_atomic(RECEIPT, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
