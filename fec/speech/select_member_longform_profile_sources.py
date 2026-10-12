#!/usr/bin/env python3
"""Select one evidence-bound long-form source path for every active member.

Current formal leaders are routed only to the dedicated pre-current-role
ledger.  Other members prefer the primary recording and fall back to
independently acquired second, third, and fourth recordings after failed name-cue
screens.  This stage selects a candidate source; it grants no identity,
turn-boundary, semantic, or profile authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from fec.speech import source_media_recovery as SMR
from fec.speech.acquire_member_longform_audio import (
    acquisition_matches_candidate, acquisition_matches_queue,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROSTER = ROOT / "data/members/profile_basis_v2.json"
DEFAULT_ROLES = ROOT / "data/speech/member_leadership_role_cutoffs_v1.json"
DEFAULT_LEADER_SOURCES = ROOT / "data/speech/member_leadership_prerole_reviewed_sources_v1.jsonl"
DEFAULT_PRIMARY_TRANSCRIPTS = Path(r"C:\melange-data\member_longform_transcripts_v1.jsonl")
DEFAULT_PRIMARY_ACQUISITION = ROOT / "data/speech/member_longform_acquisition_v1.jsonl"
DEFAULT_PRIMARY_CUES = Path(r"C:\melange-data\member_longform_identity_cues_v1.jsonl")
DEFAULT_PRIMARY_QUEUE = ROOT / "data/speech/member_longform_acquisition_queue_v1.jsonl"
DEFAULT_RETRY_TRANSCRIPTS = Path(r"C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl")
DEFAULT_RETRY_ACQUISITION = ROOT / "data/speech/member_longform_no_cue_retry_acquisition_v1.jsonl"
DEFAULT_RETRY_CUES = Path(r"C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl")
DEFAULT_RETRY2_TRANSCRIPTS = Path(r"C:\melange-data\member_longform_no_cue_retry2_transcripts_v1.jsonl")
DEFAULT_RETRY2_ACQUISITION = ROOT / "data/speech/member_longform_no_cue_retry2_acquisition_v1.jsonl"
DEFAULT_RETRY2_CUES = Path(r"C:\melange-data\member_longform_no_cue_retry2_identity_cues_v1.jsonl")
DEFAULT_RETRY3_TRANSCRIPTS = Path(r"C:\melange-data\member_longform_no_cue_retry3_transcripts_v1.jsonl")
DEFAULT_RETRY3_ACQUISITION = ROOT / "data/speech/member_longform_no_cue_retry3_acquisition_v1.jsonl"
DEFAULT_RETRY3_CUES = Path(r"C:\melange-data\member_longform_no_cue_retry3_identity_cues_v1.jsonl")
DEFAULT_LEADER_TRANSCRIPTS = Path(r"C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl")
DEFAULT_LEADER_ACQUISITION = ROOT / "data/speech/member_leadership_prerole_acquisition_v1.jsonl"
DEFAULT_LEADER_CUES = Path(r"C:\melange-data\member_leadership_prerole_identity_cues_v1.jsonl")
DEFAULT_OUT = ROOT / "data/speech/member_longform_profile_source_selection_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_profile_source_selection_v1.receipt.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def optional_rows(path: Path) -> list[dict]:
    return SMR.read_jsonl(path) if path.is_file() else []


def by_member(path: Path) -> dict[str, dict]:
    result = {}
    for row in optional_rows(path):
        member = str(row["member"])
        if member in result:
            raise SystemExit(f"duplicate member in {path}: {member}")
        result[member] = row
    return result


def ready(row: dict | None) -> bool:
    return bool(row and row.get("transcript") and row.get("transcript_sha256"))


def has_cue(row: dict | None) -> bool:
    return bool(row and row.get("status") == "HARD_IN_BAND_NAME_CUE_CANDIDATE" and row.get("cues"))


def require_source_match(cue: dict, acquisition: dict | None, *, context: str) -> None:
    for key in ("member", "source_id", "wav_sha256"):
        if not acquisition or not cue.get(key) or cue.get(key) != acquisition.get(key):
            raise SystemExit(f"source lineage mismatch ({key}): {context}")


def require_lineage(cue: dict, transcript: dict | None, acquisition: dict | None,
                    *, context: str, file_hashes: dict) -> None:
    """Bind both positive and negative cue results before using their evidence."""
    require_source_match(cue, acquisition, context=context)
    if not transcript:
        raise SystemExit(f"missing transcript row: {context}")
    for key in ("member", "source_id", "wav_sha256", "transcript_sha256"):
        if not cue.get(key) or cue.get(key) != transcript.get(key):
            raise SystemExit(f"transcript lineage mismatch ({key}): {context}")
    for key, rows in (("wav", (cue, transcript, acquisition)),
                      ("transcript", (cue, transcript))):
        if any(not row.get(key) for row in rows):
            raise SystemExit(f"missing {key} path: {context}")
        paths = [Path(str(row[key])).resolve() for row in rows]
        if any(path != paths[0] for path in paths[1:]):
            raise SystemExit(f"source lineage path mismatch ({key}): {context}")
        path = paths[0]
        if not path.is_file():
            raise SystemExit(f"missing {key} file: {context}: {path}")
        stat = path.stat()
        cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
        if cache_key not in file_hashes:
            file_hashes[cache_key] = SMR.sha256_file(path)
        if file_hashes[cache_key] != cue[key + "_sha256"]:
            raise SystemExit(f"physical {key} hash mismatch: {context}")
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise SystemExit(f"{key} file changed during verification: {context}")


def rejected_source(row: dict, rejected: list[dict]) -> bool:
    return any(
        acquisition_matches_candidate(row, candidate)
        or bool(row.get("wav_sha256") and row.get("wav_sha256") == candidate.get("wav_sha256"))
        for candidate in rejected
    )


def source_record(cue: dict, acquisition: dict | None, *, selection: str, source_era: str) -> dict:
    # Also protect direct callers: never combine an old cue/transcript with new audio.
    require_source_match(cue, acquisition, context=str(cue.get("member")))
    row = dict(cue)
    for key in (
        "title", "channel", "url", "recorded_at", "published",
        "current_role_assumed_on", "wav", "wav_sha256",
    ):
        if acquisition and acquisition.get(key) is not None:
            row[key] = acquisition.get(key)
    return {
        **row,
        "schema": "member-longform-profile-source-selection-v1",
        "status": "SELECTED_HARD_NAME_CUE_CANDIDATE",
        "selection": selection,
        "source_era": source_era,
        "speaker_identity_authority": False,
        "turn_boundary_authority": False,
        "semantic_authority": False,
        "profile_effect": "NONE_UNTIL_FULL_PROMOTION",
        "selected_at": utc_now(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES)
    parser.add_argument("--leader-sources", type=Path, default=DEFAULT_LEADER_SOURCES)
    parser.add_argument("--primary-transcripts", type=Path, default=DEFAULT_PRIMARY_TRANSCRIPTS)
    parser.add_argument("--primary-acquisition", type=Path, default=DEFAULT_PRIMARY_ACQUISITION)
    parser.add_argument("--primary-cues", type=Path, default=DEFAULT_PRIMARY_CUES)
    parser.add_argument("--primary-queue", type=Path, default=DEFAULT_PRIMARY_QUEUE,
                        help="Current approved primary queue; present reviewed replacements must match acquisition")
    parser.add_argument("--rejected-sources", type=Path,
                        help="Optional member-scoped superseded/rejected source ledger; excludes matching retries without deleting audio")
    parser.add_argument("--quality-holds", type=Path,
                        help="Optional exact member/source/audio/transcript quality exclusions; does not certify unlisted sources")
    parser.add_argument("--retry-transcripts", type=Path, default=DEFAULT_RETRY_TRANSCRIPTS)
    parser.add_argument("--retry-acquisition", type=Path, default=DEFAULT_RETRY_ACQUISITION)
    parser.add_argument("--retry-cues", type=Path, default=DEFAULT_RETRY_CUES)
    parser.add_argument("--retry2-transcripts", type=Path, default=DEFAULT_RETRY2_TRANSCRIPTS)
    parser.add_argument("--retry2-acquisition", type=Path, default=DEFAULT_RETRY2_ACQUISITION)
    parser.add_argument("--retry2-cues", type=Path, default=DEFAULT_RETRY2_CUES)
    parser.add_argument("--retry3-transcripts", type=Path, default=DEFAULT_RETRY3_TRANSCRIPTS)
    parser.add_argument("--retry3-acquisition", type=Path, default=DEFAULT_RETRY3_ACQUISITION)
    parser.add_argument("--retry3-cues", type=Path, default=DEFAULT_RETRY3_CUES)
    parser.add_argument("--leader-transcripts", type=Path, default=DEFAULT_LEADER_TRANSCRIPTS)
    parser.add_argument("--leader-acquisition", type=Path, default=DEFAULT_LEADER_ACQUISITION)
    parser.add_argument("--leader-cues", type=Path, default=DEFAULT_LEADER_CUES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    input_hashes = {
        path: SMR.sha256_file(path) if path.is_file() else None
        for key, path in vars(args).items()
        if key not in {"out", "receipt"} and isinstance(path, Path)
    }

    roster_doc = read_json(args.roster)
    roster = set((roster_doc.get("members") or {}).keys())
    roles_doc = read_json(args.roles)
    roles = roles_doc.get("members") or {}
    if not roster or not set(roles) <= roster:
        raise SystemExit("invalid roster or leadership registry")

    leader_sources = by_member(args.leader_sources)
    if set(leader_sources) != set(roles):
        raise SystemExit("leadership source ledger does not exactly match leadership registry")
    primary_transcripts = by_member(args.primary_transcripts)
    primary_acquisition = by_member(args.primary_acquisition)
    retry_transcripts = by_member(args.retry_transcripts)
    retry_acquisition = by_member(args.retry_acquisition)
    retry2_transcripts = by_member(args.retry2_transcripts)
    retry2_acquisition = by_member(args.retry2_acquisition)
    retry3_transcripts = by_member(args.retry3_transcripts)
    retry3_acquisition = by_member(args.retry3_acquisition)
    leader_transcripts = by_member(args.leader_transcripts)
    leader_acquisition = by_member(args.leader_acquisition)
    primary_cues = by_member(args.primary_cues)
    retry_cues = by_member(args.retry_cues)
    retry2_cues = by_member(args.retry2_cues)
    retry3_cues = by_member(args.retry3_cues)
    leader_cues = by_member(args.leader_cues)
    primary_queue = by_member(args.primary_queue)
    if primary_queue and set(primary_queue) != roster:
        raise SystemExit("primary queue does not exactly match active roster")
    rejected_by_member: dict[str, list[dict]] = defaultdict(list)
    quality_by_member: dict[str, list[dict]] = defaultdict(list)
    if args.rejected_sources:
        if not args.rejected_sources.is_file():
            raise SystemExit("explicit rejected-source ledger is missing")
        for row in optional_rows(args.rejected_sources):
            rejected_by_member[str(row["member"])].append(row)
    if args.quality_holds:
        if not args.quality_holds.is_file():
            raise SystemExit("explicit quality-hold ledger is missing")
        for row in optional_rows(args.quality_holds):
            if any(not row.get(key) for key in ("member", "source_id", "wav_sha256", "transcript_sha256")):
                raise SystemExit("quality hold lacks exact member/source/audio/transcript binding")
            quality_by_member[str(row["member"])].append(row)
    for member, queue_row in primary_queue.items():
        for holder in (queue_row, queue_row.get("primary") or {}):
            rejected_by_member[member].extend(
                {"source_id": source_id} for source_id in holder.get("rejected_source_ids") or []
            )
            rejected_by_member[member].extend(
                {"url": url} for url in holder.get("rejected_urls") or []
            )
        if ((queue_row.get("primary") or {}).get("reviewed_replacement")
                and not acquisition_matches_queue(primary_acquisition.get(member) or {}, queue_row)):
            raise SystemExit(f"approved primary replacement not acquired: {member}")

    # A valid output receipt must not bless mismatched rows merely because the
    # individual ledger files have hashes. Validate the linkage before any write.
    streams = (
        ("primary", primary_cues, primary_transcripts, primary_acquisition),
        ("retry", retry_cues, retry_transcripts, retry_acquisition),
        ("retry2", retry2_cues, retry2_transcripts, retry2_acquisition),
        ("retry3", retry3_cues, retry3_transcripts, retry3_acquisition),
        ("leadership", leader_cues, leader_transcripts, leader_acquisition),
    )
    file_hashes: dict = {}
    excluded_by_member: dict[str, list[dict]] = defaultdict(list)
    held_by_member: dict[str, list[dict]] = defaultdict(list)
    for label, cues, transcripts, acquisitions in streams:
        for member, cue in list(cues.items()):
            if member not in roster:
                raise SystemExit(f"cue member outside active roster: {label}: {member}")
            require_lineage(cue, transcripts.get(member), acquisitions.get(member),
                            context=f"{label}: {member}", file_hashes=file_hashes)
            if rejected_source(cue, rejected_by_member.get(member, [])):
                excluded_by_member[member].append({
                    "stream": label, "source_id": cue.get("source_id"),
                    "reason": "SUPERSEDED_OR_REJECTED_PRIMARY_PENDING_REVIEW",
                    "media_preserved": True,
                })
                cues.pop(member, None)
            for hold in quality_by_member.get(member, []):
                if all(cue.get(key) == hold[key] for key in ("member", "source_id", "wav_sha256", "transcript_sha256")):
                    held_by_member[member].append({
                        "stream": label, "source_id": cue.get("source_id"),
                        "wav_sha256": cue.get("wav_sha256"),
                        "transcript_sha256": cue.get("transcript_sha256"),
                        "reason": hold.get("reason") or hold.get("quality_flags") or "QUALITY_REVIEW_REQUIRED",
                        "media_preserved": True,
                    })
                    cues.pop(member, None)
                    break

    rows: list[dict] = []
    for member in sorted(roster):
        if member in roles:
            source = leader_sources[member]
            if source.get("status") == "HISTORICAL_LONGFORM_SOURCE_GAP":
                rows.append({
                    "schema": "member-longform-profile-source-selection-v1",
                    "status": "LEADERSHIP_PREROLE_SOURCE_GAP",
                    "member": member,
                    "current_role": roles[member].get("current_role"),
                    "current_role_assumed_on": roles[member].get("current_role_assumed_on"),
                    "gap_reason": source.get("gap_reason"),
                    "speaker_identity_authority": False,
                    "profile_effect": "NONE",
                    "selected_at": utc_now(),
                })
                continue
            cue = leader_cues.get(member)
            transcript = leader_transcripts.get(member)
            if has_cue(cue):
                cutoff = str(roles[member]["current_role_assumed_on"])
                recorded = str((transcript or {}).get("recorded_at") or "")
                if not recorded or recorded[:10] >= cutoff[:10]:
                    raise SystemExit(f"leadership pre-role cutoff violation: {member}")
                row = source_record(
                    cue, leader_acquisition.get(member),
                    selection="leadership_pre_current_role",
                    source_era="PRE_CURRENT_LEADERSHIP_ROLE",
                )
                row["recorded_at"] = recorded
                row["published"] = (transcript or {}).get("published")
                row["current_role"] = roles[member].get("current_role")
                row["current_role_assumed_on"] = cutoff
                rows.append(row)
            elif ready(transcript):
                rows.append({
                    "schema": "member-longform-profile-source-selection-v1",
                    "status": "LEADERSHIP_PREROLE_NO_HARD_NAME_CUE",
                    "member": member,
                    "source_id": transcript.get("source_id"),
                    "source_era": "PRE_CURRENT_LEADERSHIP_ROLE",
                    "current_role": roles[member].get("current_role"),
                    "current_role_assumed_on": roles[member].get("current_role_assumed_on"),
                    "speaker_identity_authority": False,
                    "profile_effect": "NONE",
                    "selected_at": utc_now(),
                })
            else:
                rows.append({
                    "schema": "member-longform-profile-source-selection-v1",
                    "status": "LEADERSHIP_PREROLE_TRANSCRIPT_PENDING",
                    "member": member,
                    "source_id": (transcript or {}).get("source_id") or source.get("source_id"),
                    "source_era": "PRE_CURRENT_LEADERSHIP_ROLE",
                    "current_role": roles[member].get("current_role"),
                    "current_role_assumed_on": roles[member].get("current_role_assumed_on"),
                    "speaker_identity_authority": False,
                    "profile_effect": "NONE",
                    "selected_at": utc_now(),
                })
            continue

        primary_cue = primary_cues.get(member)
        retry_cue = retry_cues.get(member)
        retry2_cue = retry2_cues.get(member)
        retry3_cue = retry3_cues.get(member)
        if has_cue(primary_cue):
            rows.append(source_record(
                primary_cue, primary_acquisition.get(member),
                selection="primary",
                source_era="NOT_CURRENT_FORMAL_LEADERSHIP",
            ))
        elif has_cue(retry_cue):
            rows.append(source_record(
                retry_cue, retry_acquisition.get(member),
                selection="independent_second_source_after_primary_no_cue",
                source_era="NOT_CURRENT_FORMAL_LEADERSHIP",
            ))
        elif has_cue(retry2_cue):
            rows.append(source_record(
                retry2_cue, retry2_acquisition.get(member),
                selection="independent_third_source_after_two_failed_cue_screens",
                source_era="NOT_CURRENT_FORMAL_LEADERSHIP",
            ))
        elif has_cue(retry3_cue):
            rows.append(source_record(
                retry3_cue, retry3_acquisition.get(member),
                selection="independent_fourth_source_after_three_failed_cue_screens",
                source_era="NOT_CURRENT_FORMAL_LEADERSHIP",
            ))
        elif not ready(primary_transcripts.get(member)):
            rows.append({
                "schema": "member-longform-profile-source-selection-v1",
                "status": "PRIMARY_TRANSCRIPT_PENDING",
                "member": member,
                "speaker_identity_authority": False,
                "profile_effect": "NONE",
                "selected_at": utc_now(),
            })
        elif member in retry_transcripts and not ready(retry_transcripts.get(member)):
            rows.append({
                "schema": "member-longform-profile-source-selection-v1",
                "status": "SECOND_SOURCE_TRANSCRIPT_PENDING",
                "member": member,
                "source_id": retry_transcripts[member].get("source_id"),
                "speaker_identity_authority": False,
                "profile_effect": "NONE",
                "selected_at": utc_now(),
            })
        elif member in retry2_transcripts and not ready(retry2_transcripts.get(member)):
            rows.append({
                "schema": "member-longform-profile-source-selection-v1",
                "status": "THIRD_SOURCE_TRANSCRIPT_PENDING",
                "member": member,
                "source_id": retry2_transcripts[member].get("source_id"),
                "speaker_identity_authority": False,
                "profile_effect": "NONE",
                "selected_at": utc_now(),
            })
        elif member in retry3_transcripts and not ready(retry3_transcripts.get(member)):
            rows.append({
                "schema": "member-longform-profile-source-selection-v1",
                "status": "FOURTH_SOURCE_TRANSCRIPT_PENDING",
                "member": member,
                "source_id": retry3_transcripts[member].get("source_id"),
                "speaker_identity_authority": False,
                "profile_effect": "NONE",
                "selected_at": utc_now(),
            })
        else:
            rows.append({
                "schema": "member-longform-profile-source-selection-v1",
                "status": "NO_HARD_NAME_CUE_AFTER_AVAILABLE_SOURCES",
                "member": member,
                "speaker_identity_authority": False,
                "profile_effect": "NONE",
                "selected_at": utc_now(),
            })

    if len(rows) != len(roster) or {row["member"] for row in rows} != roster:
        raise SystemExit("selection output does not exactly cover the active roster")
    for row in rows:
        if excluded_by_member.get(row["member"]):
            row["excluded_sources"] = excluded_by_member[row["member"]]
        if held_by_member.get(row["member"]):
            row["quality_held_sources"] = held_by_member[row["member"]]
            if row["status"] != "SELECTED_HARD_NAME_CUE_CANDIDATE":
                row["status"] = ("LEADERSHIP_PREROLE_QUALITY_REVIEW_PENDING"
                                 if row["member"] in roles else "QUALITY_REVIEW_PENDING")
                row["profile_effect"] = "NONE"
    for path, expected_hash in input_hashes.items():
        current_hash = SMR.sha256_file(path) if path.is_file() else None
        if current_hash != expected_hash:
            raise SystemExit(f"selection input changed during verification: {path}")
    SMR.write_jsonl_atomic(args.out, rows)
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    receipt = {
        "schema": "member-longform-profile-source-selection-receipt-v1",
        "status": "PASS_SELECTION_ONLY",
        "generated_at": utc_now(),
        "counts": {"active_members": len(roster), **status_counts},
        "contract": {
            "one_row_per_active_member": True,
            "current_leaders_use_pre_current_role_baseline_only": True,
            "strict_recording_date_cutoff": True,
            "selection_establishes_identity": False,
            "selection_has_profile_effect": False,
            "cue_transcript_acquisition_source_binding_verified": True,
            "physical_audio_and_transcript_hashes_verified": True,
            "superseded_recordings_preserved_but_not_reselected": True,
            "quality_holds_are_exclusions_not_review_authority": True,
        },
        "inputs": {
            "primary_queue_sha256": SMR.sha256_file(args.primary_queue) if args.primary_queue.is_file() else None,
            "rejected_sources_sha256": SMR.sha256_file(args.rejected_sources) if args.rejected_sources else None,
            "quality_holds_sha256": SMR.sha256_file(args.quality_holds) if args.quality_holds else None,
            "roster_sha256": SMR.sha256_file(args.roster),
            "roles_sha256": SMR.sha256_file(args.roles),
            "leader_sources_sha256": SMR.sha256_file(args.leader_sources),
            "primary_transcripts_sha256": SMR.sha256_file(args.primary_transcripts),
            "primary_acquisition_sha256": SMR.sha256_file(args.primary_acquisition),
            "primary_cues_sha256": SMR.sha256_file(args.primary_cues),
            "retry_transcripts_sha256": (
                SMR.sha256_file(args.retry_transcripts) if args.retry_transcripts.is_file() else None
            ),
            "retry_acquisition_sha256": (
                SMR.sha256_file(args.retry_acquisition) if args.retry_acquisition.is_file() else None
            ),
            "retry_cues_sha256": (
                SMR.sha256_file(args.retry_cues) if args.retry_cues.is_file() else None
            ),
            "retry2_transcripts_sha256": (
                SMR.sha256_file(args.retry2_transcripts) if args.retry2_transcripts.is_file() else None
            ),
            "retry2_acquisition_sha256": (
                SMR.sha256_file(args.retry2_acquisition) if args.retry2_acquisition.is_file() else None
            ),
            "retry2_cues_sha256": (
                SMR.sha256_file(args.retry2_cues) if args.retry2_cues.is_file() else None
            ),
            "retry3_transcripts_sha256": (
                SMR.sha256_file(args.retry3_transcripts) if args.retry3_transcripts.is_file() else None
            ),
            "retry3_acquisition_sha256": (
                SMR.sha256_file(args.retry3_acquisition) if args.retry3_acquisition.is_file() else None
            ),
            "retry3_cues_sha256": (
                SMR.sha256_file(args.retry3_cues) if args.retry3_cues.is_file() else None
            ),
            "leader_transcripts_sha256": SMR.sha256_file(args.leader_transcripts),
            "leader_acquisition_sha256": SMR.sha256_file(args.leader_acquisition),
            "leader_cues_sha256": SMR.sha256_file(args.leader_cues),
        },
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
