"""Prepare an isolated all-roster repair batch without competing live writes.

Existing good ASR is reused. Caption-derived timings are retranscribed from
the original WAV rather than clipped, sorted, or treated as exact alignment.
Primary backlog already assigned to the existing collection worker is left
waiting, not duplicated. Every roster member retains an explicit queue row.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path

from fec.speech import source_media_recovery as SMR
from fec.speech import snapshot_member_longform_ready_sources as snapshot
from fec.speech import select_member_longform_profile_sources as selector
from audits.longform_collection_20260904 import audit_primary_transcript_quality as quality


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.out_root.resolve()
    if root.exists():
        raise ValueError("new isolated batch directory required")
    snapshot.verify_snapshot(args.snapshot)
    receipt = json.loads((args.snapshot / "snapshot.receipt.json").read_text(encoding="utf-8"))
    docs = {key: (json.loads(Path(item["snapshot_path"]).read_text(encoding="utf-8-sig"))
                  if key in {"roster", "roles"} else SMR.read_jsonl(Path(item["snapshot_path"]), required=True))
            for key, item in receipt["inputs"].items()}
    indexes = {key: snapshot.rows_by_member(docs[key], key) for key in (
        "primary_queue", "primary_acquisition", "primary_transcripts", "leader_sources", "leader_acquisition", "leader_transcripts")}
    # Read the active primary manifest once, solely to reuse completed repairs.
    live_payload = selector.DEFAULT_PRIMARY_TRANSCRIPTS.read_bytes()
    live = snapshot.rows_by_member(quality.jsonl(live_payload), "latest primary transcripts")
    acquired_rows, transcript_rows, queue_rows = [], [], []
    for member in sorted(docs["roster"]["members"]):
        role = docs["roles"]["members"].get(member)
        stream = "leader" if role else "primary"
        acquired = indexes[stream + "_acquisition"].get(member)
        transcript = indexes[stream + "_transcripts"].get(member)
        if not role and member in live:
            transcript = live[member]
        item = {"member": member, "stream": stream, "source_id": (acquired or {}).get("source_id"),
                "profile_effect": "NONE_UNTIL_COMPLETE_AUTOMATED_VERIFICATION"}
        problem = snapshot.source_problem(member, acquired, transcript, indexes["primary_queue"].get(member),
            role, indexes["leader_sources"].get(member), docs["rejected_sources"], docs["quality_holds"])
        recoverable = {None, "TRANSCRIPT_PENDING", "STALE_TRANSCRIPT_SOURCE_BINDING", "QUALITY_HOLD_REQUIRES_SEPARATE_REPAIR_REVIEW"}
        if problem not in recoverable:
            queue_rows.append({**item, "status": "SOURCE_HOLD", "reason": problem})
            continue
        # source_problem returns early on stale transcripts. Independently check
        # the acquired source against authoritative selection before scheduling.
        acquisition_problem = snapshot.source_problem(member, acquired, None, indexes["primary_queue"].get(member),
            role, indexes["leader_sources"].get(member), docs["rejected_sources"], [], acquisition_only=True)
        if acquisition_problem:
            queue_rows.append({**item, "status": "SOURCE_HOLD", "reason": acquisition_problem})
            continue
        acquired_rows.append(acquired)
        report = quality.inspect(transcript, acquired) if transcript else None
        same_source = report and report.get("binding") == "CURRENT_SOURCE_AND_RECORDED_AUDIO_HASH_MATCH"
        existing_asr = (same_source and selector.ready(transcript)
                        and transcript.get("status") != "CAPTION_TRANSCRIPT_READY_IDENTITY_UNVERIFIED"
                        and not report["flags"] and problem != "QUALITY_HOLD_REQUIRES_SEPARATE_REPAIR_REVIEW")
        if existing_asr:
            transcript_rows.append(transcript)
            queue_rows.append({**item, "status": "REUSE_ASR_FOR_AUTOMATED_PROCESSING", "reason": "technical_diagnostics_passed_not_identity_or_semantic_approval"})
        elif not role and problem in {"TRANSCRIPT_PENDING", "STALE_TRANSCRIPT_SOURCE_BINDING"}:
            transcript_rows.append({**acquired, "schema": "member-longform-transcript-v1", "status": "WAITING_FOR_EXISTING_COLLECTION_WORKER"})
            queue_rows.append({**item, "status": "WAITING_FOR_EXISTING_COLLECTION_WORKER", "reason": problem})
        else:
            transcript_rows.append({**acquired, "schema": "member-longform-transcript-v1", "status": "LOCAL_ASR_REQUIRED",
                "superseded_transcript": (transcript or {}).get("transcript"),
                "superseded_transcript_sha256": (transcript or {}).get("transcript_sha256"),
                "speaker_identity_authority": False})
            queue_rows.append({**item, "status": "RETRANSCRIBE_ORIGINAL_AUDIO", "reason": problem or "estimated_caption_times_or_diagnostic_risks",
                               "diagnostic_flags": (report or {}).get("flags", [])})
    SMR.write_jsonl_atomic(root / "acquisitions.jsonl", acquired_rows)
    SMR.write_jsonl_atomic(root / "transcripts.jsonl", transcript_rows)
    SMR.write_jsonl_atomic(root / "member_queue.jsonl", queue_rows)
    counts = dict(Counter(row["status"] for row in queue_rows))
    SMR.write_json_atomic(root / "prepare.receipt.json", {"schema": "member-longform-isolated-automated-batch-v1",
        "status": "PREPARED_NOT_VERIFIED", "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "active_members": len(queue_rows), "counts": counts,
        "source_snapshot": str(args.snapshot.resolve()), "source_snapshot_receipt_sha256": SMR.sha256_file(args.snapshot / "snapshot.receipt.json"),
        "live_primary_manifest_read_sha256": snapshot.digest(live_payload),
        "artifacts": {name: {"path": str(root / name), "sha256": SMR.sha256_file(root / name)}
                      for name in ("acquisitions.jsonl", "transcripts.jsonl", "member_queue.jsonl")},
        "contract": {"live_manifests_written": False, "human_signoff_required": False,
                     "failed_checks_are_verified": False, "caption_timing_repaired_by_clipping_or_sorting": False,
                     "same_whisper_asr_model": True, "original_recordings_preserved": True}})
    print({"active_members": len(queue_rows), "counts": counts}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
