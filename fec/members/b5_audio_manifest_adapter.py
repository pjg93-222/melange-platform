#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind the audited accepted-clip manifest to a candidate-only audio review queue.

This adapter never writes the active review, descriptor, or profile artifacts.  It admits only
provenance-bound on-air Washington Journal and Axe Files clips that already satisfy the immutable
30-second/50-word clip floors.  It does not invent topics, segments beyond the whole accepted clip,
or either of the two required independent human reviews.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_rules import MIN_CLIP_TARGET_SEC, MIN_CLIP_TARGET_WORDS
from paths import ROOT


DEFAULT_MANIFEST = Path(r"C:\melange-data\member_clips\clips_manifest.jsonl")
DEFAULT_AUDIT = Path(ROOT) / "data" / "engine" / "collection_intake_receipt.json"
DEFAULT_OUT = Path(ROOT) / "data" / "members" / "b5_audio_reviews_v2.json.candidate"
REQUIRED_BINDING = (
    "turn_id", "package", "transcript_path", "text_sha256", "source_audio_sha256",
    "out_wav", "sha256", "frames", "samplerate",
)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_row_bytes(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def row_sha256(row):
    return hashlib.sha256(canonical_row_bytes(row)).hexdigest()


def read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise RuntimeError("invalid manifest JSON at line %d" % number) from exc
            if not isinstance(row, dict):
                raise RuntimeError("non-object manifest row at line %d" % number)
            rows.append(row)
    return rows


def safe_clip_path(value, allowed_root):
    path = Path(value or "").resolve()
    root = Path(allowed_root).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("clip path escapes accepted root: %s" % path) from exc
    return path


def queue_key(row):
    # Legacy WJ rows reuse artifact_id across several official transcript turns.  Bind the
    # candidate to the unique artifact+turn+audio tuple instead of weakening uniqueness.
    payload = "|".join(str(row.get(key) or "") for key in
                       ("outlet", "artifact_id", "turn_id", "sha256"))
    return "manifest:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_disposition(row):
    outlet = row.get("outlet")
    if outlet == "CSPAN":
        if (row.get("show") != "Washington Journal" or
                row.get("alignment_method") != "official-cspan-speaker+official-offset-boundaries"):
            return "source_policy_mismatch"
    elif outlet == "CNN":
        if (row.get("show") != "CNN The Axe Files" or
                row.get("provenance_schema") != "axe-clip-turn-v1"):
            return "source_policy_mismatch"
    elif outlet == "HouseLive":
        return "not_on_air_interview"
    elif outlet in ("NPR", "SUNDAY"):
        return "missing_turn_provenance"
    else:
        return "source_policy_mismatch"
    if any(row.get(field) in (None, "") for field in REQUIRED_BINDING):
        return "missing_turn_provenance"
    try:
        if float(row.get("dur") or 0) < MIN_CLIP_TARGET_SEC:
            return "below_30_second_floor"
        if int(row.get("words") or 0) < MIN_CLIP_TARGET_WORDS:
            return "below_50_word_floor"
    except (TypeError, ValueError):
        return "invalid_floor_fields"
    return "queueable_pending_two_reviews_and_topic"


def binding(row):
    return {
        "source_artifact_id": row.get("artifact_id"), "turn_id": row.get("turn_id"),
        "out_wav": row.get("out_wav"), "audio_sha256": row.get("sha256"),
        "frames": int(row.get("frames")), "samplerate": int(row.get("samplerate")),
        "transcript_path": row.get("transcript_path"), "text_sha256": row.get("text_sha256"),
        "source_audio_sha256": row.get("source_audio_sha256"),
        "source_row_sha256": row_sha256(row),
    }


def candidate_row(row):
    duration = float(row.get("frames")) / float(row.get("samplerate"))
    return {
        "member": row.get("member"), "source_id": row.get("package"),
        "source_url": row.get("package"), "venue": "on_air_interview",
        "outlet": row.get("outlet"), "program": row.get("show"),
        "aired_at": row.get("date") or row.get("turn_date"),
        "recording_context": "accepted broadcast/podcast clip; manifest-bound; pending human review",
        "speech_id": row.get("turn_id"),
        "topic": {},
        "segments": [{"start_sec": 0.0, "end_sec": round(duration, 6),
                      "word_count": int(row.get("words")),
                      "transcript_sha256": row.get("text_sha256")}],
        "reviews": [], "manifest_binding": binding(row),
        "review_status": "PENDING_TWO_DISTINCT_HUMAN_REVIEWS_AND_TRANSCRIPT_TOPIC",
    }


def validate_audio(row, allowed_root, verify_hash=True):
    path = safe_clip_path(row.get("out_wav"), allowed_root)
    if not path.is_file():
        raise RuntimeError("accepted clip missing: %s" % path)
    with wave.open(str(path), "rb") as handle:
        facts = (handle.getnchannels(), handle.getframerate(), handle.getsampwidth(),
                 handle.getnframes())
    if facts[:3] != (1, 16000, 2) or facts[3] != int(row.get("frames") or -1):
        raise RuntimeError("accepted clip PCM/frame mismatch: %s" % path)
    if verify_hash and sha256_file(path) != row.get("sha256"):
        raise RuntimeError("accepted clip SHA256 mismatch: %s" % path)


def load_existing(path):
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != "b5-audio-review-v2-candidate":
        raise RuntimeError("existing candidate has wrong schema")
    return doc.get("clips") or {}


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def build(manifest_path, audit_path, output_path, roster_path, allowed_root,
          merge_existing=True, verify_hash=True):
    before_sha = sha256_file(manifest_path)
    rows = read_jsonl(manifest_path)
    after_sha = sha256_file(manifest_path)
    if before_sha != after_sha:
        raise RuntimeError("clip manifest changed while it was being read")
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    if (audit.get("schema") != "collection-intake-receipt-v1" or
            audit.get("status") != "ACCEPTED"):
        raise RuntimeError("collection audit receipt is not an accepted gate receipt")
    if (audit.get("inputs") or {}).get("clip_manifest_sha256") != before_sha:
        raise RuntimeError("collection audit receipt is not bound to the current clip manifest")
    errors = (audit.get("house") or {}).get("all_clip_errors") or []
    if errors or int((audit.get("house") or {}).get("all_clip_rows") or -1) != len(rows):
        raise RuntimeError("collection audit does not accept the complete current clip manifest")
    roster = {row.get("member") for row in json.loads(Path(roster_path).read_text(encoding="utf-8"))}
    existing = load_existing(Path(output_path)) if merge_existing else {}
    seen_paths = set(); seen_logical = set(); queued = {}; dispositions = Counter()
    by_member = defaultdict(lambda: {"clips": 0, "seconds": 0.0, "programs": set(), "outlets": set()})
    reviews_preserved = reviews_demoted = 0
    for row in rows:
        if row.get("member") not in roster:
            raise RuntimeError("manifest member not in roster: %r" % row.get("member"))
        out_wav = str(Path(row.get("out_wav") or "").resolve()).lower()
        logical = (row.get("outlet"), row.get("turn_id") or row.get("sid"),
                   row.get("seg"), row.get("member"))
        if out_wav in seen_paths or logical in seen_logical:
            raise RuntimeError("duplicate accepted clip path/logical key")
        seen_paths.add(out_wav); seen_logical.add(logical)
        disposition = source_disposition(row); dispositions[disposition] += 1
        if disposition != "queueable_pending_two_reviews_and_topic":
            continue
        validate_audio(row, allowed_root, verify_hash=verify_hash)
        key = queue_key(row); fresh = candidate_row(row); prior = existing.get(key) or {}
        if ((prior.get("manifest_binding") or {}).get("source_row_sha256") ==
                fresh["manifest_binding"]["source_row_sha256"]):
            for field in ("topic", "segments", "reviews"):
                if prior.get(field):
                    fresh[field] = prior[field]
            if prior.get("reviews") or prior.get("topic"):
                reviews_preserved += 1
        elif prior.get("reviews") or prior.get("topic"):
            reviews_demoted += 1
        queued[key] = fresh
        agg = by_member[row["member"]]; agg["clips"] += 1
        agg["seconds"] += float(row["frames"]) / float(row["samplerate"])
        agg["programs"].add(row["show"]); agg["outlets"].add(row["outlet"])
    if sum(dispositions.values()) != len(rows) or len(queued) != dispositions["queueable_pending_two_reviews_and_topic"]:
        raise RuntimeError("audio intake disposition partition is incomplete")
    members = {member: {"candidate_clips": row["clips"], "candidate_seconds": round(row["seconds"], 3),
                        "programs": sorted(row["programs"]), "outlets": sorted(row["outlets"]),
                        "status": "PENDING_DOUBLE_REVIEW_NOT_ACTIVE_AUDIO"}
               for member, row in sorted(by_member.items())}
    doc = {
        "schema": "b5-audio-review-v2-candidate",
        "status": "CANDIDATE_ONLY_CONSUMED_BY_NOTHING_AWAITING_DOUBLE_REVIEW",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "two_distinct_reviews_per_clip": True, "target_identity_required": True,
            "target_only_segments_required": True, "on_air_interviews_only": True,
            "minimum_confirmed_target_seconds_per_clip": MIN_CLIP_TARGET_SEC,
            "minimum_aligned_target_words_per_clip": MIN_CLIP_TARGET_WORDS,
            "audio_may_modify_big_five": False, "active_artifacts_written": False,
        },
        "source_manifest": {"path": str(Path(manifest_path).resolve()), "sha256": before_sha,
                            "row_n": len(rows), "audit_receipt": str(Path(audit_path).resolve()),
                            "audit_receipt_sha256": sha256_file(audit_path)},
        "partition": {"total": len(rows), "queued": len(queued),
                      "excluded": len(rows) - len(queued), "dispositions": dict(sorted(dispositions.items()))},
        "merge": {"reviews_or_topics_preserved": reviews_preserved,
                  "changed_rows_demoted": reviews_demoted},
        "members": members, "clips": queued,
    }
    atomic_json(Path(output_path), doc)
    return doc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--audit-receipt", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--roster", type=Path, default=Path(ROOT) / "data/engine/congestion.json")
    parser.add_argument("--allowed-root", type=Path, default=Path(r"C:\melange-data"))
    parser.add_argument("--no-merge-existing", action="store_true")
    parser.add_argument("--no-audio-hash", action="store_true",
                        help="skip per-WAV SHA replay; manifest/audit binding and PCM checks remain")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    doc = build(args.manifest, args.audit_receipt, args.out, args.roster, args.allowed_root,
                merge_existing=not args.no_merge_existing, verify_hash=not args.no_audio_hash)
    print("accepted-audio candidate intake: %d total = %d queued + %d excluded; active audio unchanged" %
          (doc["partition"]["total"], doc["partition"]["queued"], doc["partition"]["excluded"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
