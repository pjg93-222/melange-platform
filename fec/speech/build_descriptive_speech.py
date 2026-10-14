#!/usr/bin/env python3
"""Build the display-only speech-habit and context layer.

The accepted clip manifest is the unit of account.  Every row must be bound to a
current roster member, a real mono/16 kHz/16-bit WAV, an immutable clip hash, and
either exact turn provenance or a verified episode/package alignment.  Existing
canonical-corpus eGeMAPS measurements are reused only when their span key
matches the accepted manifest; missing measurements are computed locally with
the same eGeMAPSv02 functionals.  Recovered source-media finals are held to a
stricter rule: their complete candidate/identity/calibration/final receipt chain
and exact secured-source PCM are revalidated, and every final WAV is measured
anew. Candidate-stage acoustic features are never trusted.

This artifact is descriptive.  It must never modify Big Five, ranking, stance,
allocation, persuasion, or any other Moneyball engine value.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import glob
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
from urllib.parse import urlsplit
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import MELANGE_DATA, ROOT, to_host, to_stored


SCHEMA = "speech-descriptive-v1"
STATUS = "DISPLAY_ONLY_NO_ENGINE_WIRING"
CHANNELS = ("pause", "pace", "volume_dyn", "pitch_var", "strain", "f0_mean")
FEATURE_COLUMNS = {
    "pause": "MeanUnvoicedSegmentLength",
    "pace": "VoicedSegmentsPerSec",
    "volume_dyn": "loudness_sma3_stddevNorm",
    "pitch_var": "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
    "strain": "jitterLocal_sma3nz_amean",
    "f0_mean": "F0semitoneFrom27.5Hz_sma3nz_amean",
}
# Washington Journal's accepted v1 adapter stored integer transcript-boundary
# durations while its resampled PCM frame counts retain sub-second precision.
# Across all 6,697 accepted rows the maximum observed representation delta is
# 0.743 s. PCM shape, exact frame count, and SHA256 remain strict; this limit
# only reconciles the coarse legacy ``dur`` field with those authoritative
# values.
LEGACY_DURATION_FRAME_TOLERANCE_SEC = 0.75
INTERVIEW_SETTINGS = {
    "washington_journal", "npr", "sunday_show", "longform_podcast",
    "broadcast_news_interview",
}
SETTING_LABELS = {
    "washington_journal": "Washington Journal",
    "npr": "NPR interview",
    "sunday_show": "Sunday show",
    "longform_podcast": "Long-form podcast",
    "broadcast_news_interview": "Broadcast news interview",
    "house_floor": "House floor",
}
SUPPLEMENT_SCHEMA = "speech-source-media-supplement-clip-v1"
SUPPLEMENT_FEATURES = {
    "pause": "pause_mean_sec",
    "pace": "voiced_rate",
    "volume_dyn": "loudness_var_norm",
    "pitch_var": "pitch_var_norm",
    "strain": "jitter",
    "f0_mean": "f0_mean_semitone",
}
VERIFIED_FINAL_SCHEMA = "speech-source-verified-clip-v3"
VERIFIED_FINAL_RECEIPT_SCHEMA = "speech-source-verified-clip-receipt-v3"
IDENTITY_SCHEMA = "speech-source-speaker-identity-v3"
IDENTITY_RECEIPT_SCHEMA = "speech-source-speaker-identity-receipt-v3"
CANDIDATE_SCHEMA = "speech-source-media-candidate-clip-v2"
CANDIDATE_RECEIPT_SCHEMA = "speech-source-media-candidate-clip-receipt-v2"
CALIBRATION_SCHEMA = "speech-speaker-anchor-calibration-v2"
MIN_VERIFIED_ALIGNMENT_RATIO = 0.68
COMPACT_UNRESOLVED_REASON = "candidate has fewer than three full speech windows"
COMPACT_UNRESOLVED_FIELDS = frozenset({
    "schema", "clip_id", "claimed_member", "status", "reasons",
    "operational_confidence_label", "probability_claim",
    "scoring_contract_sha256", "identified_at", "identity_row_sha256",
})
DEFAULT_VERIFIED_MANIFEST = Path(ROOT) / "data" / "speech" / "source_media_verified_v2.jsonl"
DEFAULT_VERIFIED_RECEIPT = Path(ROOT) / "data" / "speech" / "source_media_verified_v2.receipt.json"
DEFAULT_IDENTITY_MANIFEST = Path(ROOT) / "data" / "speech" / "source_media_identity_v2.jsonl"
DEFAULT_IDENTITY_RECEIPT = Path(ROOT) / "data" / "speech" / "source_media_identity_v2.receipt.json"
DEFAULT_CANDIDATE_MANIFEST = Path(MELANGE_DATA, "source_audio", "candidate_clips.jsonl")
DEFAULT_CANDIDATE_RECEIPT = Path(MELANGE_DATA, "source_audio", "candidate_clips.receipt.json")
DEFAULT_INVENTORY = Path(ROOT) / "data" / "speech" / "source_media_inventory_v1.jsonl"
DEFAULT_CALIBRATION = Path(ROOT) / "data" / "speech" / "speaker_anchor_calibration_v1.json"
CANONICAL_LINEAGE_SCHEMA = "speech-canonical-source-lineage-v1"
CANONICAL_LINEAGE_RECEIPT_SCHEMA = "speech-canonical-source-lineage-receipt-v1"
DEFAULT_CANONICAL_LINEAGE = Path(ROOT) / "data" / "speech" / "canonical_source_lineage_v1.jsonl"
DEFAULT_CANONICAL_LINEAGE_RECEIPT = (
    Path(ROOT) / "data" / "speech" / "canonical_source_lineage_v1.receipt.json"
)


class SpeechBuildError(RuntimeError):
    pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path(MELANGE_DATA, "member_clips", "clips_manifest.jsonl"))
    parser.add_argument("--audit", type=Path,
                        default=Path(ROOT) / "data" / "engine" / "collection_intake_receipt.json")
    parser.add_argument("--roster", type=Path,
                        default=Path(ROOT) / "data" / "engine" / "congestion.json")
    parser.add_argument("--out", type=Path,
                        default=Path(ROOT) / "data" / "speech" / "speech_descriptive_v1.json")
    parser.add_argument("--cache", type=Path,
                        default=Path(ROOT) / "data" / "speech" / "speech_descriptive_clip_cache.jsonl")
    parser.add_argument("--receipt", type=Path,
                        default=Path(ROOT) / "data" / "speech" / "speech_descriptive_v1.receipt.json")
    parser.add_argument("--supplement-manifest", type=Path,
                        default=Path(ROOT) / "data" / "speech" /
                        "source_media_supplement_v1.jsonl")
    parser.add_argument("--supplement-receipt", type=Path,
                        default=Path(ROOT) / "data" / "speech" /
                        "source_media_supplement_v1.receipt.json")
    parser.add_argument("--verified-manifest", type=Path, default=DEFAULT_VERIFIED_MANIFEST)
    parser.add_argument("--verified-receipt", type=Path, default=DEFAULT_VERIFIED_RECEIPT)
    parser.add_argument("--identity-manifest", type=Path, default=DEFAULT_IDENTITY_MANIFEST)
    parser.add_argument("--identity-receipt", type=Path, default=DEFAULT_IDENTITY_RECEIPT)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE_MANIFEST)
    parser.add_argument("--candidate-receipt", type=Path, default=DEFAULT_CANDIDATE_RECEIPT)
    parser.add_argument("--source-media-inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--speaker-calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--canonical-lineage", type=Path, default=DEFAULT_CANONICAL_LINEAGE)
    parser.add_argument("--canonical-lineage-receipt", type=Path,
                        default=DEFAULT_CANONICAL_LINEAGE_RECEIPT)
    parser.add_argument("--reactions", type=Path, default=Path(ROOT) / "outputs" /
                        "speech_reaction_profiles_20260825" / "wj_reactions.jsonl")
    parser.add_argument("--topics", type=Path, default=Path(ROOT) / "outputs" /
                        "speech_triggers_20260826" / "trigger_pairs_pathA_v3.jsonl")
    parser.add_argument("--no-verify-audio-hashes", action="store_true")
    parser.add_argument("--no-measure-missing", action="store_true")
    return parser.parse_args(argv)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def row_sha256(row) -> str:
    return hashlib.sha256(canonical_bytes(row)).hexdigest()


def audit_row_sha256(row) -> str:
    """Match source_media_recovery's ASCII-stable JSON row hash."""
    return hashlib.sha256(json.dumps(
        row, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def read_jsonl(path: Path, *, required=True):
    if not path.is_file():
        if required:
            raise SpeechBuildError("required JSONL is missing: %s" % path)
        return []
    rows = []
    with path.open(encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise SpeechBuildError("invalid JSONL %s:%d" % (path, number)) from exc
            if not isinstance(value, dict):
                raise SpeechBuildError("non-object JSONL %s:%d" % (path, number))
            rows.append(value)
    return rows


def read_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SpeechBuildError("invalid/missing JSON: %s" % path) from exc
    return value


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".%d.tmp" % os.getpid())
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# The only producer of an in-band C-SPAN interview row is
# verify_local_youtube_exact_windows.py, which writes
# "in-band-approved-first+surname+asr-turn-interior+ecapa-rejection-only" (its
# approved-first-name-alias vocabulary).  The bare full-name spelling below is the
# pre-alias wording; both are kept so already-promoted rows keep their setting.
IN_BAND_INTERVIEW_ALIGNMENT = frozenset({
    "in-band-full-name+asr-turn-interior+ecapa-rejection-only",
    "in-band-approved-first+surname+asr-turn-interior+ecapa-rejection-only",
})


def setting_family(row):
    outlet = str(row.get("outlet") or "").upper()
    if outlet == "CSPAN":
        if str(row.get("alignment_method") or "") in IN_BAND_INTERVIEW_ALIGNMENT:
            return "broadcast_news_interview"
        return "washington_journal"
    if outlet == "NPR":
        return "npr"
    if outlet == "SUNDAY":
        return "sunday_show"
    if outlet == "CNN":
        return "longform_podcast"
    if outlet == "HOUSELIVE":
        return "house_floor"
    raise SpeechBuildError("unknown speech setting/outlet: %r" % row.get("outlet"))


def generic_span_key(row):
    return (
        str(row.get("sid") or ""), str(row.get("member") or ""),
        int(row.get("seg") or 0), round(float(row.get("start") or 0), 3),
        round(float(row.get("end") or 0), 3),
    )


def wj_span_key(row):
    start = row.get("member_start_s", row.get("start"))
    return (str(row.get("sid") or ""), str(row.get("member") or ""),
            round(float(start or 0), 3))


def feature_key(row, outlet):
    return wj_span_key(row) if outlet == "CSPAN" else generic_span_key(row)


def feature_paths():
    base = Path(ROOT) / "outputs"
    groups = {
        "NPR": sorted(Path(p) for p in glob.glob(str(
            base / "npr_pilot_20260824" / "npr_features_*.jsonl"))),
        "SUNDAY": sorted(Path(p) for p in glob.glob(str(
            base / "sunday_pilot_20260825" / "sunday_features_*.jsonl"))),
        "CSPAN": [base / "wj_features_20260825" / "wj_features_dedup.jsonl"],
        "CNN": [base / "axe_pilot_20260827" / "axe_features.jsonl"],
    }
    for outlet, paths in groups.items():
        if not paths or any(not path.is_file() for path in paths):
            raise SpeechBuildError("feature sources missing for %s: %r" % (outlet, paths))
    return groups


def load_feature_index(paths, outlet):
    """Dedupe overlapping shards without selecting on a measured channel."""
    best = {}
    physical = conflicts = 0
    for path in sorted(paths, key=lambda p: os.path.normcase(str(p))):
        for row in read_jsonl(path):
            physical += 1
            key = feature_key(row, outlet)
            rank = (-float(row.get("match") or 0), -float(row.get("dur") or 0),
                    os.path.normcase(path.name))
            prior = best.get(key)
            if prior is not None and prior[0] != row:
                conflicts += 1
            if prior is None or rank < prior[1]:
                best[key] = (row, rank, path, row_sha256(row))
    file_hashes = {path: sha256_file(path) for path in {value[2] for value in best.values()}}
    return {key: {"row": value[0], "path": value[2], "row_sha256": value[3],
                  "file_sha256": file_hashes[value[2]]}
            for key, value in best.items()}, {
                "physical_rows": physical, "unique_rows": len(best),
                "conflicting_duplicate_rows": conflicts,
            }


def load_npr_bindings():
    path = Path(MELANGE_DATA, "npr_audio", "fetch_log.jsonl")
    bindings = {}
    for row in read_jsonl(path):
        if row.get("status") == "OK" and row.get("sid"):
            bindings[str(row["sid"])] = row
    return path, bindings


def load_sunday_bindings():
    path = Path(MELANGE_DATA, "sunday_audio", "targets.json")
    bindings = {}
    for row in read_json(path):
        sid = "%s_%s" % (row.get("fam"), row.get("date"))
        bindings[sid] = row
    return path, bindings


def load_local_youtube_bindings():
    """Load immutable exact-cut identity bindings from successful write runs."""
    bindings = {}
    receipts = []
    speech_root = Path(ROOT) / "data" / "speech"
    current = speech_root / "local_youtube_exact_window_verification_current_v1.receipt.json"
    receipt_paths = [current] if current.is_file() else list(speech_root.glob(
        "local_youtube_exact_window_verification*_v1.receipt.json"
    ))
    for receipt_path in receipt_paths:
        receipt = read_json(receipt_path)
        if (
            receipt.get("schema") != "local-youtube-exact-window-verification-receipt-v1"
            or receipt.get("status") != "PASS"
            or receipt.get("mode") != "write"
        ):
            continue
        inputs = receipt.get("inputs") or {}
        if not inputs:
            raise SpeechBuildError("local YouTube verification input chain is missing")
        for item in inputs.values():
            if not isinstance(item, dict):
                continue
            input_path = Path(to_host(str(item.get("path") or "")))
            if (
                not input_path.is_file()
                or sha256_file(input_path) != item.get("sha256")
            ):
                raise SpeechBuildError("local YouTube verification input receipt is stale")
        output = Path(to_host(str((receipt.get("output") or {}).get("path") or "")))
        if (
            not output.is_file()
            or sha256_file(output) != (receipt.get("output") or {}).get("sha256")
        ):
            raise SpeechBuildError("local YouTube verification output receipt is stale")
        receipt_sha = sha256_file(receipt_path)
        receipts.append({
            "path": str(receipt_path.resolve()),
            "sha256": receipt_sha,
            "output_sha256": (receipt.get("output") or {}).get("sha256"),
        })
        for row in read_jsonl(output):
            if (
                row.get("status") != "PASS_EXACT_WINDOW"
                or row.get("final_identity_acceptance") is not True
            ):
                continue
            key = (
                str(Path(to_host(str(row.get("out_wav") or ""))).resolve()),
                str(row.get("member") or ""),
                str(row.get("video_id") or ""),
                round(float(row.get("start_sec")), 6),
                round(float(row.get("end_sec")), 6),
                str(row.get("wav_sha256") or ""),
            )
            value = {
                "receipt_sha256": receipt_sha,
                "verification_row_sha256": row_sha256(row),
                "identity_evidence": row.get("identity_evidence"),
                "identity_evidence_basis": row.get("identity_evidence_basis"),
                "ecapa_role": row.get("ecapa_role"),
            }
            if key in bindings and bindings[key] != value:
                raise SpeechBuildError("conflicting local YouTube identity bindings")
            bindings[key] = value
    return bindings, sorted(receipts, key=lambda row: row["path"])


TURN_FIELDS = ("turn_id", "transcript_path", "text_sha256", "source_audio_sha256")


def classify_identity(
        row, npr_bindings, sunday_bindings, feature=None, local_youtube_bindings=None):
    local_youtube_bindings = local_youtube_bindings or {}
    if all(row.get(field) not in (None, "") for field in TURN_FIELDS):
        return {
            "tier": "turn_bound", "label": "Exact attributed turn",
            "basis": str(row.get("alignment_method") or row.get("binding_method") or
                         row.get("provenance_schema") or "turn-bound transcript alignment"),
        }
    outlet = str(row.get("outlet") or "").upper()
    sid = str(row.get("sid") or "")
    member = str(row.get("member") or "")
    local_key = (
        str(Path(to_host(str(row.get("out_wav") or ""))).resolve()),
        member,
        sid,
        round(float(row.get("start") or 0), 6),
        round(float(row.get("end") or 0), 6),
        str(row.get("sha256") or ""),
    )
    local_binding = local_youtube_bindings.get(local_key)
    if local_binding is not None:
        return {
            "tier": "turn_bound_receipt_verified",
            "label": "In-band named exact turn",
            "basis": local_binding["identity_evidence_basis"],
            "identity_evidence": local_binding["identity_evidence"],
            "verification_row_sha256": local_binding["verification_row_sha256"],
            "verification_receipt_sha256": local_binding["receipt_sha256"],
            "ecapa_role": local_binding["ecapa_role"],
        }
    if outlet == "NPR":
        binding = npr_bindings.get(sid)
        if not binding or binding.get("member") != member or not binding.get("pkg"):
            raise SpeechBuildError("NPR package/member identity binding failed: %s/%s" %
                                   (sid, member))
        return {
            "tier": "package_aligned", "label": "Published transcript package alignment",
            "basis": "exact NPR fetch-log package/member plus monotonic transcript alignment",
            "package": binding.get("pkg"),
        }
    if outlet == "SUNDAY":
        binding = sunday_bindings.get(sid)
        feature_row = (feature or {}).get("row") or {}
        feature_bound = (str(feature_row.get("sid") or "") == sid and
                         str(feature_row.get("member") or "") == member)
        seeded_member = bool(binding and member in (binding.get("members") or []))
        if not binding or not (seeded_member or feature_bound):
            raise SpeechBuildError("Sunday episode/date/member identity binding failed: %s/%s" %
                                   (sid, member))
        return {
            "tier": "episode_aligned", "label": "Episode/date transcript alignment",
            "basis": ("exact show-family/date target plus transcript-aligned feature member/span"
                      if feature_bound else
                      "exact show-family/date/member target plus monotonic transcript alignment"),
            "package": binding.get("url"),
            "member_binding": "transcript_feature" if feature_bound else "episode_seed",
        }
    raise SpeechBuildError("clip lacks an accepted identity binding: %s/%s/%s" %
                           (outlet, sid, member))


def validate_manifest_contract(manifest_path, audit_path, roster_path):
    before = sha256_file(manifest_path)
    rows = read_jsonl(manifest_path)
    after = sha256_file(manifest_path)
    if before != after:
        raise SpeechBuildError("clip manifest changed while being read")
    audit = read_json(audit_path)
    if audit.get("schema") != "collection-intake-receipt-v1" or audit.get("status") != "ACCEPTED":
        raise SpeechBuildError("collection intake receipt is not ACCEPTED")
    if (audit.get("inputs") or {}).get("clip_manifest_sha256") != before:
        raise SpeechBuildError("collection receipt is not bound to the current clip manifest")
    house = audit.get("house") or {}
    if house.get("all_clip_errors") or int(house.get("all_clip_rows") or -1) != len(rows):
        raise SpeechBuildError("collection receipt does not accept the complete manifest")
    roster = {str(row.get("member") or "") for row in read_json(roster_path)}
    return rows, before, roster, audit


def wav_contract(row, verify_hash):
    path = Path(to_host(str(row.get("out_wav") or "")))
    if not path.is_file():
        raise SpeechBuildError("accepted clip missing: %s" % path)
    with wave.open(str(path), "rb") as handle:
        facts = (handle.getnchannels(), handle.getframerate(), handle.getsampwidth(),
                 handle.getnframes())
    expected_frames = int(row.get("frames") or -1)
    if facts[:3] != (1, 16000, 2) or facts[3] != expected_frames:
        raise SpeechBuildError("accepted clip WAV/frame contract failed: %s" % path)
    expected_duration = expected_frames / 16000.0
    manifest_duration = float(row.get("dur") or 0)
    if abs(expected_duration - manifest_duration) > LEGACY_DURATION_FRAME_TOLERANCE_SEC:
        raise SpeechBuildError("accepted clip duration/frame mismatch: %s" % path)
    if row.get("start") is not None and row.get("end") is not None:
        boundary_duration = float(row["end"]) - float(row["start"])
        if abs(boundary_duration - manifest_duration) > 0.02:
            raise SpeechBuildError("accepted clip transcript-boundary duration mismatch: %s" % path)
    expected_hash = str(row.get("sha256") or "")
    if len(expected_hash) != 64:
        raise SpeechBuildError("accepted clip lacks SHA256: %s" % path)
    if verify_hash and sha256_file(path) != expected_hash:
        raise SpeechBuildError("accepted clip SHA256 mismatch: %s" % path)
    return path


def supplement_wav_contract(row, verify_hash):
    path = Path(to_host(str(row.get("wav") or "")))
    if not path.is_absolute() or not path.is_file():
        raise SpeechBuildError("supplemental clip missing or path is not absolute: %s" % path)
    with wave.open(str(path), "rb") as handle:
        facts = (handle.getnchannels(), handle.getframerate(), handle.getsampwidth(),
                 handle.getnframes())
    if facts[:3] != (1, 16000, 2):
        raise SpeechBuildError("supplemental clip WAV contract failed: %s" % path)
    duration = facts[3] / 16000.0
    if abs(duration - float(row.get("duration_sec") or 0)) > 0.02:
        raise SpeechBuildError("supplemental clip duration/frame mismatch: %s" % path)
    if abs((float(row.get("end_sec") or 0) - float(row.get("start_sec") or 0)) - duration) > 0.02:
        raise SpeechBuildError("supplemental clip source-boundary mismatch: %s" % path)
    expected_hash = str(row.get("audio_sha256") or "")
    if len(expected_hash) != 64:
        raise SpeechBuildError("supplemental clip lacks SHA256: %s" % path)
    if verify_hash and sha256_file(path) != expected_hash:
        raise SpeechBuildError("supplemental clip SHA256 mismatch: %s" % path)
    return path, facts[3]


def recovered_setting(row):
    text = " ".join(str(row.get(key) or "").lower()
                    for key in ("show", "package", "source_family"))
    sunday_tokens = (
        "state-of-the-union", "/sotu/", "fox-news-sunday", "face-the-nation",
        "this-week", "meet-the-press",
    )
    return "sunday_show" if any(token in text for token in sunday_tokens) else "broadcast_news_interview"


def supplement_outlet(row):
    return {
        "cnn": "CNN",
        "fox": "FOX",
        "msnow": "MSNOW",
        "abc": "ABC",
        "abc-this-week": "ABC",
        "cbs-ftn": "CBS",
        "pbs": "PBS",
        "nbc-mtp": "NBC",
    }.get(str(row.get("source_family") or ""),
          str(row.get("source_family") or "SOURCE_MEDIA").upper())


def _stable_sha256(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _row_field_sha256(row, field):
    body = dict(row)
    body.pop(field, None)
    return _stable_sha256(body)


def _identity_window_clip_id(parent, run, window_index):
    return "%s:run:%d-%d:window:%d" % (
        parent, int(run["start_window"]),
        int(run["end_window_exclusive"]), int(window_index),
    )


def _accepted_identity_window_units(decision):
    """Independently derive exact final units from qualifying accepted runs."""
    status = decision.get("status")
    if status not in {
            "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"}:
        return []
    parent = str(decision.get("clip_id") or "")
    runs = decision.get("accepted_runs") or []
    if not runs:
        if status == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
            return []
        raise SpeechBuildError("partial accepted identity decision has no accepted runs")
    if int(decision.get("accepted_run_count", -1)) != len(runs):
        raise SpeechBuildError("accepted-run count does not match persisted runs")
    windows = decision.get("windows") or []
    claimed_member = str(decision.get("claimed_member") or "")
    if not parent or not isinstance(windows, list) or not claimed_member:
        raise SpeechBuildError("accepted identity decision lacks windows/member")
    units = []
    seen = set()
    for run in runs:
        if (
                run.get("identity_run_sha256")
                != _row_field_sha256(run, "identity_run_sha256")
                or run.get("all_windows_passed") is not True):
            raise SpeechBuildError("accepted identity run is hash-invalid")
        start = int(run.get("start_window", -1))
        end = int(run.get("end_window_exclusive", -1))
        count = int(run.get("window_count", -1))
        selected = windows[start:end] if 0 <= start < end <= len(windows) else []
        if (
                not (0 <= start < end <= len(windows))
                or end - start != count
                or count < 3
                or len(selected) != count
                or any(
                    int(window.get("window_index", -1)) != absolute_index
                    or window.get("passed") is not True
                    or window.get("winner") != claimed_member
                    for absolute_index, window in enumerate(selected, start)
                )):
            raise SpeechBuildError(
                "partial accepted identity run geometry is invalid"
                if status == "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                else "accepted identity run geometry is invalid"
            )
        for window_index in range(start, end):
            if window_index in seen:
                raise SpeechBuildError("accepted identity runs overlap at a window")
            seen.add(window_index)
            units.append({
                "clip_id": _identity_window_clip_id(parent, run, window_index),
                "run": run,
                "window_index": window_index,
            })
    if (
            status == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
            and seen != set(range(len(windows)))):
        raise SpeechBuildError(
            "whole accepted identity runs do not cover every persisted window")
    if not units:
        raise SpeechBuildError("accepted identity decision has no exact window units")
    return units


def _identity_window_lineage(candidate, decision, run, window_index):
    """Rebuild the exact lineage hash written by speaker finalization."""
    units = _accepted_identity_window_units(decision)
    matching = [
        unit for unit in units
        if unit["window_index"] == int(window_index)
        and unit["run"].get("identity_run_sha256")
        == run.get("identity_run_sha256")
    ]
    if len(matching) != 1:
        raise SpeechBuildError("verified identity window has no unique accepted lineage")
    exact_width = 8 * 16000
    index = int(window_index)
    relative_start = index * exact_width
    if (
            int(run.get("relative_start_frame", -1))
            != int(run.get("start_window", -1)) * exact_width
            or int(run.get("relative_frame_count", -1))
            != int(run.get("window_count", -1)) * exact_width
            or int(run.get("source_start_frame", -1))
            != int(candidate.get("source_start_frame", -1))
            + int(run.get("relative_start_frame", -1))
            or int(run.get("source_frame_count", -1))
            != int(run.get("relative_frame_count", -1))
            or int(run.get("sample_rate", -1)) != 16000
            or relative_start + exact_width
            > int(candidate.get("source_frame_count", -1))):
        raise SpeechBuildError("accepted identity run exact-frame geometry is invalid")
    window = (decision.get("windows") or [])[index]
    lineage = {
        "schema": "speech-source-speaker-identity-window-v1",
        "parent_candidate_clip_sha256": candidate.get("clip_sha256"),
        "identity_row_sha256": decision.get("identity_row_sha256"),
        "identity_run_sha256": run.get("identity_run_sha256"),
        "identity_window_index": index,
        "qualifying_run_start_window": int(run["start_window"]),
        "qualifying_run_end_window_exclusive": int(run["end_window_exclusive"]),
        "relative_start_frame": relative_start,
        "source_start_frame": int(candidate.get("source_start_frame") or 0)
        + relative_start,
        "source_frame_count": exact_width,
        "sample_rate": 16000,
        "window_evidence_sha256": _stable_sha256(window),
        "scoring_contract_sha256": decision.get("scoring_contract_sha256"),
    }
    lineage["identity_window_sha256"] = _row_field_sha256(
        lineage, "identity_window_sha256")
    return lineage


def _candidate_cut_binding(row):
    fields = (
        "source_audio_sha256", "source_receipt_sha256", "asr_transcript_sha256",
        "asr_transcript_receipt_sha256", "source_sample_rate", "source_start_frame",
        "source_frame_count", "source_slice_pcm_sha256", "staged_pcm_sha256",
        "audio_sha256", "turn_id",
    )
    return _stable_sha256({field: row.get(field) for field in fields})


def _expected_verified_final_ids(identity_rows):
    """Derive the complete final-ID set from accepted identity decisions."""
    expected = set()
    for decision in identity_rows:
        status = decision.get("status")
        parent = str(decision.get("clip_id") or "")
        if status in {
                "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"}:
            units = _accepted_identity_window_units(decision)
            if units:
                ids = [unit["clip_id"] for unit in units]
                if len(ids) != len(set(ids)):
                    raise SpeechBuildError(
                        "accepted identity decision has duplicate final windows")
                expected.update(ids)
            elif status == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
                if not parent:
                    raise SpeechBuildError("accepted identity decision lacks a clip ID")
                expected.add(parent)
    return expected


def _verified_source_ledger(candidate_rows, identity_rows, final_rows):
    """Build a deterministic per-source candidate→identity→final ledger."""
    candidates = [row for row in candidate_rows if row.get("schema") == CANDIDATE_SCHEMA]
    candidate_by_id = {}
    candidate_audio_sources = {}
    for row in candidates:
        clip_id = str(row.get("clip_id") or "")
        source = str(row.get("source_family") or "")
        audio = str(row.get("audio_sha256") or "")
        if not clip_id or clip_id in candidate_by_id or not source:
            raise SpeechBuildError("verified-source candidate IDs/sources are not unique")
        candidate_by_id[clip_id] = row
        prior_source = candidate_audio_sources.get(audio)
        if audio and prior_source is not None and prior_source != source:
            raise SpeechBuildError("candidate audio identity is duplicated across source families")
        if audio:
            candidate_audio_sources[audio] = source

    identity_by_id = {}
    expected_by_source = defaultdict(set)
    for decision in identity_rows:
        parent = str(decision.get("clip_id") or "")
        candidate = candidate_by_id.get(parent)
        if not parent or parent in identity_by_id or candidate is None:
            raise SpeechBuildError("identity decisions do not uniquely bind auditable candidates")
        identity_by_id[parent] = decision
        source = str(candidate["source_family"])
        if decision.get("status") in {
                "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"}:
            units = _accepted_identity_window_units(decision)
            if units:
                expected_by_source[source].update(
                    unit["clip_id"] for unit in units)
            elif decision.get("status") == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS":
                expected_by_source[source].add(parent)

    finals_by_id = {}
    final_audio = set()
    final_pcm = set()
    final_runs = set()
    final_windows = set()
    final_frame_identities = set()
    for row in final_rows:
        clip_id = str(row.get("clip_id") or "")
        parent = str(row.get("parent_clip_id") or clip_id)
        candidate = candidate_by_id.get(parent)
        source = str(row.get("source_family") or "")
        audio = str(row.get("audio_sha256") or "")
        pcm = str(row.get("source_slice_pcm_sha256") or "")
        run = str(row.get("identity_run_sha256") or "")
        identity_window = str(row.get("identity_window_sha256") or "")
        frame_identity = (
            str(row.get("source_audio_sha256") or ""),
            int(row.get("source_start_frame", -1)),
            int(row.get("source_frame_count", -1)),
        )
        if (
            not clip_id or clip_id in finals_by_id or candidate is None
            or source != str(candidate.get("source_family") or "")
        ):
            raise SpeechBuildError("verified finals do not uniquely preserve candidate source binding")
        if not audio or audio in final_audio:
            raise SpeechBuildError("verified final audio identity is duplicated")
        if identity_window and identity_window in final_windows:
            raise SpeechBuildError("verified final identity-window lineage is duplicated")
        if run and not identity_window and run in final_runs:
            raise SpeechBuildError("verified final accepted-run identity is duplicated")
        if pcm and pcm in final_pcm:
            raise SpeechBuildError("verified final PCM identity is duplicated")
        if frame_identity in final_frame_identities:
            raise SpeechBuildError("verified final secured-source frame identity is duplicated")
        finals_by_id[clip_id] = row
        final_audio.add(audio)
        if pcm:
            final_pcm.add(pcm)
        final_frame_identities.add(frame_identity)
        if identity_window:
            final_windows.add(identity_window)
        elif run:
            final_runs.add(run)

    by_source = {}
    sources = sorted(
        {str(row.get("source_family") or "") for row in candidates}
        | set(expected_by_source)
        | {str(row.get("source_family") or "") for row in final_rows}
    )
    for source in sources:
        if not source:
            raise SpeechBuildError("verified-source ledger contains an empty source family")
        source_candidates = [row for row in candidates if row.get("source_family") == source]
        source_identities = [
            row for row in identity_rows
            if str(candidate_by_id[str(row.get("clip_id") or "")].get("source_family") or "")
            == source
        ]
        source_finals = [row for row in final_rows if row.get("source_family") == source]
        expected_ids = sorted(expected_by_source[source])
        final_ids = sorted(str(row["clip_id"]) for row in source_finals)
        by_source[source] = {
            "candidate_clips": len(source_candidates),
            "candidate_clip_ids_sha256": _stable_sha256(sorted(
                str(row["clip_id"]) for row in source_candidates)),
            "candidate_row_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("clip_sha256") or "") for row in source_candidates)),
            "identity_decisions": len(source_identities),
            "identity_clip_ids_sha256": _stable_sha256(sorted(
                str(row["clip_id"]) for row in source_identities)),
            "identity_row_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("identity_row_sha256") or "") for row in source_identities)),
            "expected_final_clips": len(expected_ids),
            "expected_final_ids_sha256": _stable_sha256(expected_ids),
            "verified_final_clips": len(source_finals),
            "final_ids_sha256": _stable_sha256(final_ids),
            "final_row_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("final_row_sha256") or "") for row in source_finals)),
            "final_audio_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("audio_sha256") or "") for row in source_finals)),
            "final_pcm_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("source_slice_pcm_sha256") or "")
                for row in source_finals)),
            "identity_runs": sum(bool(row.get("identity_run_sha256")) for row in source_finals),
            "identity_run_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("identity_run_sha256")) for row in source_finals
                if row.get("identity_run_sha256"))),
            "identity_windows": sum(
                bool(row.get("identity_window_sha256")) for row in source_finals),
            "identity_window_sha256s_sha256": _stable_sha256(sorted(
                str(row.get("identity_window_sha256")) for row in source_finals
                if row.get("identity_window_sha256"))),
            "source_frame_bindings_sha256": _stable_sha256(sorted(
                "%s:%d:%d" % (
                    row.get("source_audio_sha256"), int(row.get("source_start_frame", -1)),
                    int(row.get("source_frame_count", -1)),
                ) for row in source_finals)),
            "final_id_set_complete": final_ids == expected_ids,
        }
    return by_source


def _require_disjoint_origin_identities(groups_by_origin):
    """Prevent clip or audio double counting between prepared corpus lanes."""
    all_ids = [str(row.get("clip_id") or "") for group in groups_by_origin for row in group]
    if not all(all_ids) or len(all_ids) != len(set(all_ids)):
        raise SpeechBuildError("canonical, verified-final, and supplemental clip IDs overlap")
    origin_audio = [
        {str(row.get("audio_sha256") or "") for row in group if row.get("audio_sha256")}
        for group in groups_by_origin
    ]
    for first, second in itertools.combinations(range(len(origin_audio)), 2):
        if origin_audio[first] & origin_audio[second]:
            raise SpeechBuildError(
                "canonical, verified-final, and supplemental audio identities overlap"
            )


def _canonical_cnn_lineage_kind(row):
    """Return the canonical CNN family whose full-source lineage is required."""
    if str(row.get("provenance_schema") or "") == "axe-clip-turn-v1":
        return "cnn-axe"
    outlet = str(row.get("outlet") or "").upper()
    sid = str(row.get("sid") or "").lower()
    show = str(row.get("show") or "").lower()
    if outlet == "SUNDAY" and (sid.startswith("sotu") or "state of the union" in show):
        return "cnn-sotu"
    return None


def _verified_source_needs_cnn_lineage(row):
    family = str(row.get("source_family") or "").strip().lower()
    return family == "cnn" or family.startswith("cnn-") or "axe" in family


def _interval_overlap_frames(first_start, first_count, second_start, second_count):
    """Return strict half-open frame overlap, rejecting malformed geometry."""
    values = (first_start, first_count, second_start, second_count)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise SpeechBuildError("source-lineage frame intervals must contain integers")
    if first_start < 0 or second_start < 0 or first_count <= 0 or second_count <= 0:
        raise SpeechBuildError("source-lineage frame intervals are invalid")
    return max(0, min(first_start + first_count, second_start + second_count)
               - max(first_start, second_start))


def _canonical_frame_geometry(row):
    try:
        sample_rate = int(row.get("samplerate", -1))
        start_seconds = float(row.get("start"))
        frame_count = int(row.get("frames", -1))
    except (TypeError, ValueError) as exc:
        raise SpeechBuildError("canonical CNN source-frame geometry is malformed") from exc
    start_frame = round(start_seconds * sample_rate)
    if sample_rate != 16000 or start_frame < 0 or frame_count <= 0:
        raise SpeechBuildError("canonical CNN source-frame geometry is invalid")
    return start_frame, frame_count, sample_rate


def validate_canonical_source_lineage(
        manifest_rows, manifest_sha, lineage_path, receipt_path, *, required,
        verify_hash=True):
    """Load exact SOTU/Axe full-source lineage for overlap supersession.

    Axe rows already serialize their full-source digest in the canonical
    manifest.  Historical CNN SOTU rows do not, so an independently hashed
    sidecar binds that digest to each immutable canonical row.  When current
    verified CNN finals exist, absence or drift of any relevant canonical
    lineage fails closed before aggregation.
    """
    relevant = [row for row in manifest_rows if _canonical_cnn_lineage_kind(row)]
    if not required:
        return {}, {
            "enabled": False,
            "required": False,
            "relevant_canonical_rows": len(relevant),
        }
    lineage_path = Path(lineage_path)
    receipt_path = Path(receipt_path)
    if not lineage_path.is_file() or not receipt_path.is_file():
        raise SpeechBuildError(
            "verified CNN finals require the canonical SOTU source-lineage manifest and receipt"
        )
    receipt = read_json(receipt_path)
    rows = read_jsonl(lineage_path)
    lineage_sha = sha256_file(lineage_path)
    receipt_sha = sha256_file(receipt_path)
    sotu_rows = [row for row in relevant if _canonical_cnn_lineage_kind(row) == "cnn-sotu"]
    expected_sotu_ids = {row_sha256(row) for row in sotu_rows}
    if (
        receipt.get("schema") != CANONICAL_LINEAGE_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("canonical_manifest_sha256") != manifest_sha
        or receipt.get("manifest_sha256") != lineage_sha
        or int(receipt.get("canonical_sotu_rows", -1)) != len(sotu_rows)
        or int(receipt.get("canonical_sotu_sources", -1))
        != len({str(Path(to_host(str(row.get("src_wav") or ""))).resolve()) for row in sotu_rows})
        or receipt.get("canonical_sotu_row_ids_sha256")
        != _stable_sha256(sorted(expected_sotu_ids))
        or receipt.get("all_relevant_canonical_rows_bound") is not True
        or receipt.get("source_hashes_verified") is not True
        or receipt.get("all_cnn_lineage_revalidated") is not True
        or int(receipt.get("canonical_axe_rows_verified", -1))
        != len(relevant) - len(sotu_rows)
        or receipt.get("physical_cleanup_authorized") is not False
    ):
        raise SpeechBuildError("canonical SOTU source-lineage receipt is stale or incomplete")

    sidecar = {}
    for row in rows:
        body_hash = row.get("lineage_row_sha256")
        canonical_id = str(row.get("canonical_row_sha256") or "")
        if (
            row.get("schema") != CANONICAL_LINEAGE_SCHEMA
            or row.get("status") != "VERIFIED_CANONICAL_SOURCE_LINEAGE"
            or body_hash != _row_field_sha256(row, "lineage_row_sha256")
            or canonical_id not in expected_sotu_ids
            or canonical_id in sidecar
            or row.get("canonical_manifest_sha256") != manifest_sha
        ):
            raise SpeechBuildError("canonical SOTU source-lineage row is stale or duplicated")
        sidecar[canonical_id] = row
    if set(sidecar) != expected_sotu_ids or int(receipt.get("lineage_rows", -1)) != len(rows):
        raise SpeechBuildError("canonical SOTU source-lineage sidecar is not an exact row partition")

    source_hash_cache = {}
    lineage_by_canonical_id = {}
    for canonical in relevant:
        canonical_id = row_sha256(canonical)
        kind = _canonical_cnn_lineage_kind(canonical)
        start_frame, frame_count, sample_rate = _canonical_frame_geometry(canonical)
        source_path = Path(to_host(str(canonical.get("src_wav") or "")))
        if not source_path.is_absolute() or not source_path.is_file():
            raise SpeechBuildError("canonical CNN full-source WAV is missing: %s" % source_path)
        if kind == "cnn-sotu":
            lineage = sidecar[canonical_id]
            source_sha = _require_hash(
                lineage.get("source_audio_sha256"), "canonical SOTU source SHA256")
            if (
                str(Path(to_host(str(lineage.get("source_wav") or ""))).resolve())
                != str(source_path.resolve())
                or str(Path(to_host(str(lineage.get("out_wav") or ""))).resolve())
                != str(Path(to_host(str(canonical.get("out_wav") or ""))).resolve())
                or lineage.get("canonical_clip_audio_sha256") != canonical.get("sha256")
                or int(lineage.get("source_start_frame", -1)) != start_frame
                or int(lineage.get("source_frame_count", -1)) != frame_count
                or int(lineage.get("source_end_frame_exclusive", -1))
                != start_frame + frame_count
                or int(lineage.get("source_sample_rate", -1)) != sample_rate
            ):
                raise SpeechBuildError("canonical SOTU source-lineage binding disagrees with its row")
        else:
            source_sha = _require_hash(
                canonical.get("source_audio_sha256"), "canonical Axe source SHA256")
        wav_facts = _wav_facts(source_path)
        if start_frame + frame_count > wav_facts[3]:
            raise SpeechBuildError("canonical CNN interval exceeds its full-source WAV")
        if verify_hash:
            source_key = str(source_path.resolve())
            actual_sha = source_hash_cache.get(source_key)
            if actual_sha is None:
                actual_sha = sha256_file(source_path)
                source_hash_cache[source_key] = actual_sha
            if actual_sha != source_sha:
                raise SpeechBuildError("canonical CNN full-source WAV SHA256 mismatch")
        lineage_by_canonical_id[canonical_id] = {
            "kind": kind,
            "canonical_row_sha256": canonical_id,
            "canonical_clip_audio_sha256": str(canonical.get("sha256") or ""),
            "out_wav": str(Path(to_host(str(canonical.get("out_wav") or ""))).resolve()),
            "source_wav": str(source_path.resolve()),
            "source_audio_sha256": source_sha,
            "source_start_frame": start_frame,
            "source_frame_count": frame_count,
            "source_end_frame_exclusive": start_frame + frame_count,
            "source_sample_rate": sample_rate,
            "reference_url": canonical.get("package"),
        }
    if sha256_file(lineage_path) != lineage_sha or sha256_file(receipt_path) != receipt_sha:
        raise SpeechBuildError("canonical SOTU source-lineage chain changed while being read")
    return lineage_by_canonical_id, {
        "enabled": True,
        "required": True,
        "manifest": str(lineage_path.resolve()),
        "manifest_sha256": lineage_sha,
        "receipt": str(receipt_path.resolve()),
        "receipt_sha256": receipt_sha,
        "canonical_manifest_sha256": manifest_sha,
        "canonical_sotu_rows": len(sotu_rows),
        "canonical_axe_rows": len(relevant) - len(sotu_rows),
        "canonical_cnn_rows": len(relevant),
        "source_hashes_reverified": bool(verify_hash),
    }


def supersede_canonical_source_overlaps(
        canonical_records, manifest_rows, verified_records, lineage_by_canonical_id):
    """Partition canonical CNN records superseded by verified source intervals."""
    if not any(_verified_source_needs_cnn_lineage(row) for row in verified_records):
        return list(canonical_records), [], list(verified_records)
    manifest_by_id = {row_sha256(row): row for row in manifest_rows}
    relevant_ids = {
        canonical_id for canonical_id, row in manifest_by_id.items()
        if _canonical_cnn_lineage_kind(row)
    }
    if not relevant_ids or set(lineage_by_canonical_id) != relevant_ids:
        raise SpeechBuildError("canonical CNN lineage is missing or contains unexpected records")
    verified_by_source = defaultdict(list)
    for row in verified_records:
        if not _verified_source_needs_cnn_lineage(row):
            continue
        source_sha = _require_hash(
            row.get("source_audio_sha256"), "verified CNN full-source SHA256")
        cut = row.get("cut") or {}
        start = int(cut.get("source_start_frame", -1))
        count = int(cut.get("frames", -1))
        _interval_overlap_frames(start, count, start, count)
        verified_by_source[source_sha].append((row, start, count))

    active = []
    superseded = []
    verified_copies = [dict(row) for row in verified_records]
    verified_copy_by_id = {str(row.get("clip_id") or ""): row for row in verified_copies}
    for record in canonical_records:
        canonical_id = str(record.get("manifest_row_sha256") or record.get("clip_id") or "")
        if canonical_id not in relevant_ids:
            active.append(record)
            continue
        lineage = lineage_by_canonical_id[canonical_id]
        matches = []
        for verified, verified_start, verified_count in verified_by_source.get(
                lineage["source_audio_sha256"], []):
            overlap = _interval_overlap_frames(
                lineage["source_start_frame"], lineage["source_frame_count"],
                verified_start, verified_count,
            )
            if overlap:
                matches.append({
                    "clip_id": str(verified.get("clip_id") or ""),
                    "reference_url": verified.get("reference_url"),
                    "source_start_frame": verified_start,
                    "source_frame_count": verified_count,
                    "overlap_frames": overlap,
                })
        if not matches:
            active.append(record)
            continue
        matches.sort(key=lambda row: row["clip_id"])
        canonical = manifest_by_id[canonical_id]
        ledger_row = {
            "action": "CANONICAL_RECORD_SUPERSEDED_SOURCE_INTERVAL_OVERLAP",
            "physical_cleanup_authorized": False,
            "canonical_clip_id": str(record.get("clip_id") or ""),
            "canonical_manifest_row_sha256": canonical_id,
            "canonical_clip_audio_sha256": lineage["canonical_clip_audio_sha256"],
            "member": record.get("member"),
            "out_wav": lineage["out_wav"],
            "source_wav": lineage["source_wav"],
            "source_audio_sha256": lineage["source_audio_sha256"],
            "source_start_frame": lineage["source_start_frame"],
            "source_frame_count": lineage["source_frame_count"],
            "source_end_frame_exclusive": lineage["source_end_frame_exclusive"],
            "canonical_reference_url": canonical.get("package"),
            "superseded_by": matches,
        }
        superseded.append(ledger_row)
        for match in matches:
            verified_copy = verified_copy_by_id[match["clip_id"]]
            verified_copy.setdefault("supersedes_canonical", []).append({
                "canonical_manifest_row_sha256": canonical_id,
                "canonical_clip_audio_sha256": lineage["canonical_clip_audio_sha256"],
                "out_wav": lineage["out_wav"],
                "source_wav": lineage["source_wav"],
                "overlap_frames": match["overlap_frames"],
            })
    superseded.sort(key=lambda row: row["canonical_manifest_row_sha256"])
    for row in verified_copies:
        if row.get("supersedes_canonical"):
            row["supersedes_canonical"].sort(
                key=lambda value: value["canonical_manifest_row_sha256"])
    return active, superseded, verified_copies


def _wav_facts(path):
    try:
        with wave.open(str(path), "rb") as handle:
            facts = (handle.getnchannels(), handle.getsampwidth(),
                     handle.getframerate(), handle.getnframes())
    except (OSError, wave.Error) as exc:
        raise SpeechBuildError("verified source-media WAV is invalid: %s" % path) from exc
    if facts[:3] != (1, 2, 16000):
        raise SpeechBuildError("verified source-media WAV is not mono PCM16/16 kHz: %s" % path)
    return facts


def _pcm_sha256(path, start_frame, frame_count):
    if start_frame < 0 or frame_count <= 0:
        raise SpeechBuildError("verified source-media PCM bounds are invalid")
    _channels, width, _rate, frames = _wav_facts(path)
    if start_frame + frame_count > frames:
        raise SpeechBuildError("verified source-media PCM range exceeds its WAV")
    with wave.open(str(path), "rb") as handle:
        handle.setpos(start_frame)
        payload = handle.readframes(frame_count)
    if len(payload) != frame_count * width:
        raise SpeechBuildError("verified source-media PCM range is incomplete")
    return hashlib.sha256(payload).hexdigest()


def _require_file_hash(path, expected, label, cache=None):
    path = Path(to_host(str(path or "")))
    if not path.is_absolute() or not path.is_file():
        raise SpeechBuildError("%s is missing or is not an absolute file: %s" % (label, path))
    cache_key = str(path.resolve())
    actual = (cache or {}).get(cache_key)
    if actual is None:
        actual = sha256_file(path)
        if cache is not None:
            cache[cache_key] = actual
    if actual != expected:
        raise SpeechBuildError("%s SHA256 mismatch: %s" % (label, path))
    return path


def _require_hash(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value.lower()):
        raise SpeechBuildError("%s is not a SHA256 digest" % label)
    return value.lower()


def _validate_identity_decision_variants(
        candidate_rows, identity_rows, calibration_sha256, scoring_sha256):
    """Validate scored decisions and the sole compact fail-closed variant."""
    candidates = {str(row.get("clip_id") or ""): row for row in candidate_rows}
    if len(candidates) != len(candidate_rows):
        raise SpeechBuildError("candidate manifest contains duplicate/missing clip IDs")
    for decision in identity_rows:
        clip_id = str(decision.get("clip_id") or "")
        candidate = candidates.get(clip_id)
        if (
            candidate is None
            or decision.get("schema") != IDENTITY_SCHEMA
            or decision.get("identity_row_sha256")
            != _row_field_sha256(decision, "identity_row_sha256")
        ):
            raise SpeechBuildError("identity decision schema/hash/candidate binding is stale")
        if "candidate_clip_sha256" not in decision:
            try:
                identified_at = datetime.fromisoformat(
                    str(decision.get("identified_at") or ""))
                candidate_frames = int(candidate.get("source_frame_count", -1))
            except (TypeError, ValueError) as exc:
                raise SpeechBuildError("compact unresolved identity row is malformed") from exc
            if (
                frozenset(decision) != COMPACT_UNRESOLVED_FIELDS
                or decision.get("status") != "UNRESOLVED"
                or decision.get("reasons") != [COMPACT_UNRESOLVED_REASON]
                or decision.get("claimed_member") != candidate.get("member")
                or decision.get("operational_confidence_label")
                != "100_PERCENT_STRUCTURAL_CONSENSUS"
                or decision.get("probability_claim") is not False
                or decision.get("scoring_contract_sha256") != scoring_sha256
                or identified_at.tzinfo is None
                or not (0 < candidate_frames < 3 * 8 * 16000)
            ):
                raise SpeechBuildError("compact unresolved identity row is malformed or promotable")
            continue
        comparison = Path(to_host(str(decision.get("comparison_matrix") or "")))
        if (
            decision.get("status") not in {
                "UNRESOLVED", "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            }
            or decision.get("candidate_clip_sha256") != candidate.get("clip_sha256")
            or decision.get("candidate_audio_sha256") != candidate.get("audio_sha256")
            or decision.get("candidate_cut_binding_sha256")
            != candidate.get("cut_binding_sha256")
            or decision.get("calibration_sha256") != calibration_sha256
            or decision.get("scoring_contract_sha256") != scoring_sha256
            or not comparison.is_absolute() or not comparison.is_file()
            or decision.get("comparison_matrix_sha256") != sha256_file(comparison)
        ):
            raise SpeechBuildError("scored identity decision binding is stale")


def _verified_setting(row):
    declared = str(row.get("setting") or "").strip().lower().replace("-", "_")
    if declared and declared not in {"interview", "unknown", "unclassified"}:
        return declared
    return recovered_setting(row)


def _validate_cumulative_candidate_identity_merge(
        candidate_receipt, identity_receipt, candidate_rows, identity_rows):
    """Recompute and verify the immutable union behind a multi-source chain."""
    sources = {str(row.get("source_family") or "") for row in candidate_rows}
    if len(sources) <= 1:
        return False
    candidate_ledger = candidate_receipt.get("input_chains")
    identity_ledger = identity_receipt.get("input_chains")
    ledger_sha = _stable_sha256(candidate_ledger) if isinstance(candidate_ledger, list) else None
    candidate_partitions_disjoint = (
        candidate_receipt.get("source_partitions_disjoint") is True
    )
    identity_partitions_disjoint = (
        identity_receipt.get("source_partitions_disjoint") is True
    )
    declared_overlap = set(candidate_receipt.get("overlapping_source_families") or [])
    guarded_overlap = (
        not candidate_partitions_disjoint
        and not identity_partitions_disjoint
        and bool(declared_overlap)
        and declared_overlap
        == set(identity_receipt.get("overlapping_source_families") or [])
        and identity_receipt.get("input_candidate_audio_frame_sets_disjoint") is True
    )
    if (
        candidate_receipt.get("cumulative_chain_merge") is not True
        or identity_receipt.get("cumulative_chain_merge") is not True
        or not (
            (candidate_partitions_disjoint and identity_partitions_disjoint)
            or guarded_overlap
        )
        or candidate_receipt.get("all_input_candidate_identity_receipts_verified") is not True
        or identity_receipt.get("all_input_candidate_identity_receipts_verified") is not True
        or not isinstance(candidate_ledger, list) or len(candidate_ledger) < 2
        or identity_ledger != candidate_ledger
        or candidate_receipt.get("input_chains_sha256") != ledger_sha
        or identity_receipt.get("input_chains_sha256") != ledger_sha
    ):
        raise SpeechBuildError("multi-source candidate/identity artifacts lack an exact merge ledger")
    exclusions = candidate_receipt.get("excluded_same_member_overlap_candidates") or []
    exclusion_sha = _stable_sha256(exclusions)
    if (
        not isinstance(exclusions, list)
        or identity_receipt.get("excluded_same_member_overlap_candidates") != exclusions
        or candidate_receipt.get("excluded_same_member_overlap_candidates_sha256")
        != exclusion_sha
        or identity_receipt.get("excluded_same_member_overlap_candidates_sha256")
        != exclusion_sha
        or (exclusions and not guarded_overlap)
    ):
        raise SpeechBuildError("cumulative overlap-exclusion ledger is stale")
    excluded_by_id = {
        str(row.get("clip_id") or ""): row for row in exclusions if isinstance(row, dict)
    }
    if "" in excluded_by_id or len(excluded_by_id) != len(exclusions):
        raise SpeechBuildError("cumulative overlap-exclusion ledger has duplicate IDs")
    seen_excluded_ids = set()
    ledger_sources = set()
    input_candidates = []
    input_identities = []
    prior_candidate_ids = set()
    prior_candidate_audio = set()
    prior_chain_intervals = {}
    for chain in candidate_ledger:
        chain_sources = set(chain.get("source_families") or []) if isinstance(chain, dict) else set()
        source_overlap = chain_sources & ledger_sources
        if (
            not chain_sources
            or source_overlap - declared_overlap
            or (source_overlap and not guarded_overlap)
        ):
            raise SpeechBuildError("cumulative candidate/identity input source partitions overlap")
        ledger_sources.update(chain_sources)
        paths = {
            key: Path(str(chain.get(key) or ""))
            for key in (
                "candidate_manifest", "candidate_receipt", "identity_manifest",
                "identity_receipt", "input_inventory",
            )
        }
        if not all(path.is_absolute() and path.is_file() for path in paths.values()):
            raise SpeechBuildError("cumulative candidate/identity merge input is missing")
        for key, path in paths.items():
            expected_key = key + "_sha256"
            if chain.get(expected_key) != sha256_file(path):
                raise SpeechBuildError("cumulative candidate/identity merge input hash drifted")
        candidate_input_receipt = read_json(paths["candidate_receipt"])
        identity_input_receipt = read_json(paths["identity_receipt"])
        candidate_input_rows = read_jsonl(paths["candidate_manifest"])
        identity_input_rows = read_jsonl(paths["identity_manifest"])
        if (
            candidate_input_receipt.get("schema") != CANDIDATE_RECEIPT_SCHEMA
            or candidate_input_receipt.get("status") != "AUDIT_REQUIRED"
            or candidate_input_receipt.get("manifest_sha256")
            != chain["candidate_manifest_sha256"]
            or candidate_input_receipt.get("inventory_sha256")
            != chain["input_inventory_sha256"]
            or identity_input_receipt.get("schema") != IDENTITY_RECEIPT_SCHEMA
            or identity_input_receipt.get("status") != "PASS"
            or identity_input_receipt.get("manifest_sha256")
            != chain["identity_manifest_sha256"]
            or identity_input_receipt.get("candidate_manifest_sha256")
            != chain["candidate_manifest_sha256"]
            or identity_input_receipt.get("candidate_receipt_sha256")
            != chain["candidate_receipt_sha256"]
            or identity_input_receipt.get("inventory_sha256")
            != chain["input_inventory_sha256"]
            or identity_input_receipt.get("calibration_sha256")
            != identity_receipt.get("calibration_sha256")
            or identity_input_receipt.get("scoring_contract_sha256")
            != identity_receipt.get("scoring_contract_sha256")
            or finite(identity_input_receipt.get("minimum_alignment_ratio"))
            != finite(identity_receipt.get("minimum_alignment_ratio"))
            or identity_input_receipt.get("exact_frame_cut_binding_required") is not True
            or identity_input_receipt.get("all_anchor_comparisons_persisted") is not True
            or int(chain.get("candidate_clips", -1)) != len(candidate_input_rows)
            or int(chain.get("identity_decisions", -1)) != len(identity_input_rows)
            or {str(row.get("source_family") or "") for row in candidate_input_rows}
            != chain_sources
        ):
            raise SpeechBuildError("cumulative candidate/identity merge input receipt is dishonest")
        current_intervals = []
        for candidate in candidate_input_rows:
            clip_id = str(candidate.get("clip_id") or "")
            audio_sha = str(candidate.get("audio_sha256") or "")
            source_sha = str(candidate.get("source_audio_sha256") or "")
            start = int(candidate.get("source_start_frame", -1))
            count = int(candidate.get("source_frame_count", -1))
            exclusion = excluded_by_id.get(clip_id)
            if exclusion is not None:
                overlap_ids = sorted(
                    old_id
                    for old_id, old_candidate in {
                        str(row.get("clip_id") or ""): row for row in input_candidates
                    }.items()
                    if str(old_candidate.get("source_audio_sha256") or "") == source_sha
                    and _interval_overlap_frames(
                        start, count,
                        int(old_candidate.get("source_start_frame", -1)),
                        int(old_candidate.get("source_frame_count", -1)),
                    )
                )
                prior_by_id = {
                    str(row.get("clip_id") or ""): row for row in input_candidates
                }
                if (
                    exclusion.get("reason")
                    != "SAME_MEMBER_SOURCE_FRAME_OVERLAP_ALREADY_SECURED"
                    or exclusion.get("member") != candidate.get("member")
                    or exclusion.get("source_audio_sha256") != source_sha
                    or int(exclusion.get("source_start_frame", -1)) != start
                    or int(exclusion.get("source_frame_count", -1)) != count
                    or exclusion.get("overlapping_prior_clip_ids") != overlap_ids
                    or not overlap_ids
                    or any(
                        prior_by_id[old_id].get("member") != candidate.get("member")
                        for old_id in overlap_ids
                    )
                ):
                    raise SpeechBuildError(
                        "cumulative same-member overlap exclusion is dishonest"
                    )
                seen_excluded_ids.add(clip_id)
                continue
            if (
                not clip_id or clip_id in prior_candidate_ids
                or not audio_sha or audio_sha in prior_candidate_audio
                or not source_sha or start < 0 or count <= 0
                or any(
                    _interval_overlap_frames(start, count, old_start, old_count)
                    for old_start, old_count in prior_chain_intervals.get(source_sha, [])
                )
            ):
                raise SpeechBuildError(
                    "cumulative candidate/identity merge inputs overlap across chains"
                )
            current_intervals.append((source_sha, start, count))
            prior_candidate_ids.add(clip_id)
            prior_candidate_audio.add(audio_sha)
        for source_sha, start, count in current_intervals:
            prior_chain_intervals.setdefault(source_sha, []).append((start, count))
        input_candidates.extend(candidate_input_rows)
        input_candidates = [
            row for row in input_candidates
            if str(row.get("clip_id") or "") not in excluded_by_id
        ]
        input_identities.extend(
            row for row in identity_input_rows
            if str(row.get("clip_id") or "") not in excluded_by_id
        )
    if seen_excluded_ids != set(excluded_by_id):
        raise SpeechBuildError("cumulative overlap-exclusion ledger was not fully consumed")
    if ledger_sources != sources:
        raise SpeechBuildError("cumulative candidate/identity merge omits or invents a source")
    sorted_candidates = sorted(candidate_rows, key=lambda row: str(row.get("clip_id") or ""))
    sorted_input_candidates = sorted(
        input_candidates, key=lambda row: str(row.get("clip_id") or ""))
    sorted_identities = sorted(identity_rows, key=lambda row: str(row.get("clip_id") or ""))
    sorted_input_identities = sorted(
        input_identities, key=lambda row: str(row.get("clip_id") or ""))
    if (
        _stable_sha256(sorted_candidates) != _stable_sha256(sorted_input_candidates)
        or _stable_sha256(sorted_identities) != _stable_sha256(sorted_input_identities)
    ):
        raise SpeechBuildError("cumulative candidate/identity manifests are not the exact input union")
    return True


def _validate_verified_receipt_chain(
        manifest_path, receipt_path, identity_manifest_path, identity_receipt_path,
        candidate_manifest_path, candidate_receipt_path, inventory_path, calibration_path):
    paths = [
        manifest_path, receipt_path, identity_manifest_path, identity_receipt_path,
        candidate_manifest_path, candidate_receipt_path, inventory_path, calibration_path,
    ]
    if not all(Path(path).is_file() for path in paths):
        missing = [str(path) for path in paths if not Path(path).is_file()]
        raise SpeechBuildError("verified source-media receipt chain is incomplete: %s" % missing)
    receipt = read_json(receipt_path)
    identity_receipt = read_json(identity_receipt_path)
    candidate_receipt = read_json(candidate_receipt_path)
    calibration = read_json(calibration_path)
    manifest_sha = sha256_file(manifest_path)
    receipt_sha = sha256_file(receipt_path)
    identity_manifest_sha = sha256_file(identity_manifest_path)
    identity_receipt_sha = sha256_file(identity_receipt_path)
    candidate_manifest_sha = sha256_file(candidate_manifest_path)
    candidate_receipt_sha = sha256_file(candidate_receipt_path)
    inventory_sha = sha256_file(inventory_path)
    calibration_sha = sha256_file(calibration_path)
    minimum_alignment_ratio = finite(identity_receipt.get("minimum_alignment_ratio"))
    final_minimum_alignment_ratio = finite(receipt.get("minimum_alignment_ratio"))
    if (
        receipt.get("schema") != VERIFIED_FINAL_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("final_schema") != VERIFIED_FINAL_SCHEMA
        or receipt.get("manifest_sha256") != manifest_sha
        or receipt.get("identity_schema") != IDENTITY_SCHEMA
        or receipt.get("identity_manifest_sha256") != identity_manifest_sha
        or receipt.get("identity_receipt_sha256") != identity_receipt_sha
        or receipt.get("candidate_manifest_sha256") != candidate_manifest_sha
        or receipt.get("candidate_receipt_sha256") != candidate_receipt_sha
        or receipt.get("inventory_sha256") != inventory_sha
        or receipt.get("calibration_sha256") != calibration_sha
        or receipt.get("reference_url_required") is not True
        or receipt.get("whole_final_audio_equals_identity_candidate_required") is not False
        or receipt.get("partial_final_audio_equals_identity_tested_run_required") is not False
        or receipt.get("final_window_audio_equals_identity_tested_window_required") is not True
        or receipt.get("independent_identity_window_finalization") is not True
        or receipt.get("final_audio_matches_source_frame_binding_required") is not True
        or receipt.get("canonical_source_overlap_requires_record_supersession") is not True
        or receipt.get("source_audio_cleanup_authorized") is not False
        or receipt.get("errors") not in (None, [])
        or final_minimum_alignment_ratio is None
        or minimum_alignment_ratio is None
        or final_minimum_alignment_ratio != minimum_alignment_ratio
    ):
        raise SpeechBuildError("verified final receipt is stale or incomplete")
    if (
        candidate_receipt.get("schema") != CANDIDATE_RECEIPT_SCHEMA
        or candidate_receipt.get("status") != "AUDIT_REQUIRED"
        or candidate_receipt.get("manifest_sha256") != candidate_manifest_sha
        or candidate_receipt.get("inventory_sha256") != inventory_sha
        or candidate_receipt.get("exact_frame_cut_binding_required") is not True
    ):
        raise SpeechBuildError("candidate receipt is stale or incomplete")
    if (
        identity_receipt.get("schema") != IDENTITY_RECEIPT_SCHEMA
        or identity_receipt.get("status") != "PASS"
        or identity_receipt.get("identity_schema") != IDENTITY_SCHEMA
        or identity_receipt.get("manifest_sha256") != identity_manifest_sha
        or identity_receipt.get("candidate_manifest_sha256") != candidate_manifest_sha
        or identity_receipt.get("candidate_receipt_sha256") != candidate_receipt_sha
        or identity_receipt.get("inventory_sha256") != inventory_sha
        or identity_receipt.get("calibration_sha256") != calibration_sha
        or identity_receipt.get("exact_frame_cut_binding_required") is not True
        or identity_receipt.get("all_anchor_comparisons_persisted") is not True
        or minimum_alignment_ratio is None
        or minimum_alignment_ratio < MIN_VERIFIED_ALIGNMENT_RATIO
        or minimum_alignment_ratio > 1.0
    ):
        raise SpeechBuildError("identity receipt is stale or incomplete")
    scoring_sha = _require_hash(receipt.get("scoring_contract_sha256"),
                                "verified scoring-contract SHA256")
    scoring_contract = calibration.get("scoring_contract")
    if (
        calibration.get("schema") != CALIBRATION_SCHEMA
        or calibration.get("status") != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
        or calibration.get("all_vaulted_speakers_compared") is not True
        or calibration.get("event_holdout_enforced") is not True
        or calibration.get("serialized_thresholds_rechecked") is not True
        or calibration.get("probability_claim") is not False
        or int(calibration.get("false_accepts", -1)) != 0
        or float(calibration.get("empirical_precision") or 0) != 1.0
        or not isinstance(scoring_contract, dict)
        or _stable_sha256(scoring_contract) != scoring_sha
        or calibration.get("scoring_contract_sha256") != scoring_sha
        or identity_receipt.get("scoring_contract_sha256") != scoring_sha
    ):
        raise SpeechBuildError("speaker calibration/scoring contract is stale or non-zero-FAR")
    final_rows = read_jsonl(manifest_path)
    identity_rows = read_jsonl(identity_manifest_path)
    candidate_rows = read_jsonl(candidate_manifest_path)
    inventory_rows = read_jsonl(inventory_path)
    stable_hashes = (
        sha256_file(manifest_path) == manifest_sha,
        sha256_file(receipt_path) == receipt_sha,
        sha256_file(identity_manifest_path) == identity_manifest_sha,
        sha256_file(identity_receipt_path) == identity_receipt_sha,
        sha256_file(candidate_manifest_path) == candidate_manifest_sha,
        sha256_file(candidate_receipt_path) == candidate_receipt_sha,
        sha256_file(inventory_path) == inventory_sha,
        sha256_file(calibration_path) == calibration_sha,
    )
    if not all(stable_hashes):
        raise SpeechBuildError("verified source-media receipt chain changed while being read")
    if int(receipt.get("verified_final_clips", -1)) != len(final_rows):
        raise SpeechBuildError("verified final receipt row count mismatch")
    if int(identity_receipt.get("decisions") or -1) != len(identity_rows):
        raise SpeechBuildError("identity receipt row count mismatch")
    if int(candidate_receipt.get("candidate_clips") or -1) != len(candidate_rows):
        raise SpeechBuildError("candidate receipt row count mismatch")
    _validate_identity_decision_variants(
        candidate_rows, identity_rows, calibration_sha, scoring_sha,
    )
    cumulative_chain_merge = _validate_cumulative_candidate_identity_merge(
        candidate_receipt, identity_receipt, candidate_rows, identity_rows,
    )
    expected_final_ids = _expected_verified_final_ids(identity_rows)
    final_ids = [str(row.get("clip_id") or "") for row in final_rows]
    expected_ids_sha256 = _stable_sha256(sorted(expected_final_ids))
    identity_window_finalization_required = any(
        _accepted_identity_window_units(decision)
        for decision in identity_rows
        if decision.get("status") in {
            "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
            "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
        }
    )
    if (
        len(final_ids) != len(set(final_ids))
        or set(final_ids) != expected_final_ids
        or int(receipt.get("expected_final_clips", -1)) != len(expected_final_ids)
        or receipt.get("expected_final_ids_sha256") != expected_ids_sha256
        or receipt.get("final_id_set_complete") is not True
        or (
            identity_window_finalization_required
            and receipt.get("independent_identity_window_finalization") is not True
        )
    ):
        raise SpeechBuildError(
            "verified final manifest does not exactly cover every accepted identity decision"
        )
    by_source = _verified_source_ledger(candidate_rows, identity_rows, final_rows)
    if (
        any(row.get("final_id_set_complete") is not True for row in by_source.values())
        or receipt.get("cumulative_source_bindings_complete") is not True
        or receipt.get("by_source") != by_source
    ):
        raise SpeechBuildError(
            "verified final receipt does not preserve exact cumulative per-source bindings"
        )
    return final_rows, identity_rows, candidate_rows, inventory_rows, {
        "manifest_sha256": manifest_sha,
        "receipt_sha256": receipt_sha,
        "identity_manifest_sha256": identity_manifest_sha,
        "identity_receipt_sha256": identity_receipt_sha,
        "candidate_manifest_sha256": candidate_manifest_sha,
        "candidate_receipt_sha256": candidate_receipt_sha,
        "inventory_sha256": inventory_sha,
        "calibration_sha256": calibration_sha,
        "scoring_contract_sha256": scoring_sha,
        "expected_final_clips": len(expected_final_ids),
        "expected_final_ids_sha256": expected_ids_sha256,
        "final_id_set_complete": True,
        "minimum_alignment_ratio": float(minimum_alignment_ratio),
        "cumulative_source_bindings_complete": True,
        "cumulative_candidate_identity_chain_merge": cumulative_chain_merge,
        "by_source": by_source,
        "calibration": calibration,
        "receipt": receipt,
    }


def validate_supplement_contract(manifest_path, receipt_path, roster, verify_hash=True):
    if not manifest_path.exists() and not receipt_path.exists():
        return [], {"enabled": False, "accepted_clips": 0}
    if not manifest_path.is_file() or not receipt_path.is_file():
        raise SpeechBuildError("supplemental manifest and receipt must either both exist or both be absent")
    receipt = read_json(receipt_path)
    rows = read_jsonl(manifest_path)
    if receipt.get("schema") != "speech-source-media-supplement-receipt-v1":
        raise SpeechBuildError("source-media supplement receipt schema is invalid")
    if receipt.get("manifest_sha256") != sha256_file(manifest_path):
        raise SpeechBuildError("source-media supplement receipt is not bound to its manifest")
    contract = receipt.get("contract") or {}
    # This historical supplement never performed exhaustive vaulted-speaker
    # identity.  It is deliberately non-promoting and cannot enter Moneyball.
    # Current v2 verified finals are integrated through their own receipt chain.
    if receipt.get("status") in {"PASS", "PASS_NON_PROMOTING"}:
        return [], {
            "enabled": False,
            "deprecated": True,
            "excluded_clips": len(rows),
            "reason": "supplement has no exhaustive voice identity",
        }
    raise SpeechBuildError("source-media supplement receipt status is invalid")


def measure_file(path, smile_holder):
    if smile_holder.get("smile") is None:
        try:
            import opensmile
        except ImportError as exc:
            raise SpeechBuildError("OpenSMILE is required to measure uncached clips") from exc
        smile_holder["version"] = getattr(opensmile, "__version__", "unknown")
        smile_holder["smile"] = opensmile.Smile(
            feature_set=opensmile.FeatureSet.eGeMAPSv02,
            feature_level=opensmile.FeatureLevel.Functionals,
        )
    try:
        features = smile_holder["smile"].process_file(str(path)).iloc[0]
        return {name: round(float(features[column]), 8)
                for name, column in FEATURE_COLUMNS.items()}
    except Exception as exc:
        raise SpeechBuildError("OpenSMILE measurement failed for %s: %s" %
                               (path, str(exc)[:180])) from exc


def validate_verified_final_contract(
        manifest_path, receipt_path, identity_manifest_path, identity_receipt_path,
        candidate_manifest_path, candidate_receipt_path, inventory_path,
        calibration_path, roster, *, verify_hash=True, measure=None):
    """Validate and measure every exhaustive-identity source-media final.

    The final WAV is compared to the exact PCM frame range of its secured source
    WAV.  Candidate features are deliberately ignored even for whole-turn finals.
    ``measure`` is injectable only to permit deterministic contract tests.
    """
    manifest_path = Path(manifest_path)
    receipt_path = Path(receipt_path)
    if not manifest_path.exists() and not receipt_path.exists():
        return [], {"enabled": False, "accepted_clips": 0}
    final_rows, identity_rows, candidate_rows, inventory_rows, chain = (
        _validate_verified_receipt_chain(
            manifest_path, receipt_path, Path(identity_manifest_path),
            Path(identity_receipt_path), Path(candidate_manifest_path),
            Path(candidate_receipt_path), Path(inventory_path), Path(calibration_path),
        )
    )
    candidates = {
        str(row.get("clip_id") or ""): row for row in candidate_rows
        if row.get("schema") == CANDIDATE_SCHEMA
    }
    identities = {str(row.get("clip_id") or ""): row for row in identity_rows}
    inventory = {str(row.get("package") or ""): row for row in inventory_rows}
    if len(identities) != len(identity_rows):
        raise SpeechBuildError("identity manifest contains duplicate/missing clip IDs")
    smile_holder = {"smile": None, "version": None}
    measure = measure or measure_file
    file_hash_cache = {}
    seen = set()
    records = []
    for row in final_rows:
        clip_id = str(row.get("clip_id") or "")
        parent_clip_id = str(row.get("parent_clip_id") or clip_id)
        if not clip_id or clip_id in seen:
            raise SpeechBuildError("verified final manifest contains duplicate/missing clip IDs")
        seen.add(clip_id)
        if (
            row.get("schema") != VERIFIED_FINAL_SCHEMA
            or row.get("status") != "VERIFIED_FINAL"
            or row.get("final_row_sha256") != _row_field_sha256(row, "final_row_sha256")
            or row.get("identity_contract")
            != "100_PERCENT_STRUCTURAL_CONSENSUS_NOT_PROBABILITY"
            or row.get("calibration_sha256") != chain["calibration_sha256"]
            or row.get("scoring_contract_sha256") != chain["scoring_contract_sha256"]
            or row.get("candidate_provenance_pass") is not True
            or row.get("program_airtime_used_for_identity") is not False
        ):
            raise SpeechBuildError("verified final row contract is stale: %s" % clip_id)
        member = str(row.get("member") or "")
        if member not in roster:
            raise SpeechBuildError("verified final member is outside current roster: %r" % member)
        reference_url = str(row.get("reference_url") or "")
        parsed_reference = urlsplit(reference_url)
        if parsed_reference.scheme not in {"http", "https"} or not parsed_reference.netloc:
            raise SpeechBuildError("verified final lacks a public reference URL: %s" % clip_id)
        candidate = candidates.get(parent_clip_id)
        decision = identities.get(parent_clip_id)
        if not candidate or not decision:
            raise SpeechBuildError("verified final lacks candidate/identity parents: %s" % clip_id)
        if (
            candidate.get("status") != "CANDIDATE_AUDIT_REQUIRED"
            or candidate.get("clip_sha256") != _row_field_sha256(candidate, "clip_sha256")
            or candidate.get("cut_binding_sha256") != _candidate_cut_binding(candidate)
            or row.get("candidate_clip_sha256") != candidate.get("clip_sha256")
            or row.get("cut_binding_sha256") != candidate.get("cut_binding_sha256")
            or row.get("source_audio_sha256") != candidate.get("source_audio_sha256")
            or row.get("text_sha256") != candidate.get("text_sha256")
            or row.get("turn_id") != candidate.get("turn_id")
            or member != str(candidate.get("member") or "")
            or row.get("source_family") != candidate.get("source_family")
        ):
            raise SpeechBuildError("verified final candidate binding is invalid: %s" % clip_id)
        candidate_alignment = finite(candidate.get("alignment_ratio"))
        final_alignment = finite(row.get("alignment_ratio"))
        if (
            candidate_alignment is None
            or final_alignment is None
            or candidate_alignment < chain["minimum_alignment_ratio"]
            or candidate_alignment > 1.0
            or final_alignment != candidate_alignment
        ):
            raise SpeechBuildError(
                "verified final lexical alignment is below or differs from its locked candidate"
            )
        if (
            decision.get("schema") != IDENTITY_SCHEMA
            or decision.get("identity_row_sha256")
            != _row_field_sha256(decision, "identity_row_sha256")
            or decision.get("candidate_clip_sha256") != candidate.get("clip_sha256")
            or decision.get("candidate_audio_sha256") != candidate.get("audio_sha256")
            or decision.get("candidate_cut_binding_sha256") != candidate.get("cut_binding_sha256")
            or decision.get("calibration_sha256") != chain["calibration_sha256"]
            or decision.get("scoring_contract_sha256") != chain["scoring_contract_sha256"]
            or decision.get("all_vaulted_speakers_compared") is not True
            or decision.get("probability_claim") is not False
            or decision.get("claimed_member") != member
            or row.get("identity_row_sha256") != decision.get("identity_row_sha256")
        ):
            raise SpeechBuildError("verified final identity binding is invalid: %s" % clip_id)
        calibration = chain["calibration"]
        per_claim_thresholds = (
            calibration.get("per_claim_thresholds") or {}
        ).get(member)
        if per_claim_thresholds is not None:
            if (
                calibration.get("per_claim_thresholds_status")
                != "PASS_ZERO_FALSE_ACCEPT_CALIBRATION"
                or int(calibration.get("per_claim_false_accepts", -1)) != 0
                or int(per_claim_thresholds.get("false_accepts", -1)) != 0
            ):
                raise SpeechBuildError(
                    "verified final per-claim calibration is invalid"
                )
            expected_minimum_score = per_claim_thresholds.get(
                "minimum_target_best_score"
            )
            expected_minimum_margin = per_claim_thresholds.get(
                "minimum_best_anchor_margin"
            )
        else:
            expected_minimum_score = calibration.get("minimum_target_best_score")
            expected_minimum_margin = calibration.get("minimum_best_anchor_margin")
        windows = decision.get("windows") or []
        if (
            not isinstance(windows, list)
            or len(windows) < int(calibration.get("minimum_unanimous_windows") or 3)
            or int(decision.get("candidate_windows") or -1) != len(windows)
            or int(decision.get("vaulted_speakers_compared") or -1)
            != int(calibration.get("vaulted_speakers") or -2)
            or float(decision.get("minimum_target_best_score") or -1)
            != float(expected_minimum_score)
            or float(decision.get("minimum_best_anchor_margin") or -1)
            != float(expected_minimum_margin)
            or sorted(int(window.get("window_index", -1)) for window in windows)
            != list(range(len(windows)))
        ):
            raise SpeechBuildError("verified final identity-window calibration is invalid")
        comparison = _require_file_hash(
            decision.get("comparison_matrix"), decision.get("comparison_matrix_sha256"),
            "identity comparison matrix", file_hash_cache,
        )
        if (
            str(Path(to_host(str(row.get("identity_comparison") or ""))).resolve())
            != str(comparison.resolve())
            or row.get("identity_comparison_sha256")
            != decision.get("comparison_matrix_sha256")
        ):
            raise SpeechBuildError("verified final comparison-matrix binding is invalid")
        source = _require_file_hash(
            candidate.get("source_wav"), candidate.get("source_audio_sha256"),
            "secured source WAV", file_hash_cache if verify_hash else None,
        )
        staged = _require_file_hash(
            candidate.get("wav"), candidate.get("audio_sha256"),
            "identity candidate WAV", file_hash_cache if verify_hash else None,
        )
        source_receipt_path = _require_file_hash(
            candidate.get("source_receipt"), candidate.get("source_receipt_sha256"),
            "source acquisition receipt", file_hash_cache,
        )
        source_receipt = read_json(source_receipt_path)
        source_receipt_reference = str(
            source_receipt.get("reference_url")
            or source_receipt.get("media_url")
            or ""
        )
        if (
            source_receipt.get("schema") != "speech-source-media-acquisition-v1"
            or source_receipt.get("wav_sha256") != candidate.get("source_audio_sha256")
            or Path(to_host(str(source_receipt.get("wav") or ""))).resolve() != source.resolve()
            or source_receipt.get("provider") != candidate.get("provider")
            or source_receipt.get("media_id") != candidate.get("media_id")
            or source_receipt_reference != reference_url
        ):
            raise SpeechBuildError("source acquisition receipt binding is invalid: %s" % clip_id)
        transcript_receipt_path = _require_file_hash(
            candidate.get("asr_transcript_receipt"),
            candidate.get("asr_transcript_receipt_sha256"),
            "Whisper transcript receipt", file_hash_cache,
        )
        transcript_receipt = read_json(transcript_receipt_path)
        transcript_path = _require_file_hash(
            candidate.get("asr_transcript"), candidate.get("asr_transcript_sha256"),
            "Whisper word transcript", file_hash_cache,
        )
        if (
            transcript_receipt.get("schema")
            != "speech-source-asr-transcript-receipt-v1"
            or transcript_receipt.get("status") != "PASS"
            or transcript_receipt.get("speaker_identity_authority") is not False
            or transcript_receipt.get("transcript_sha256") != sha256_file(transcript_path)
            or transcript_receipt.get("source_audio_sha256")
            != candidate.get("source_audio_sha256")
            or row.get("transcript_sha256") != candidate.get("asr_transcript_sha256")
            or str(transcript_receipt.get("reference_url") or "") != reference_url
        ):
            raise SpeechBuildError("Whisper transcript receipt binding is invalid: %s" % clip_id)
        inventory_row = inventory.get(str(candidate.get("package") or ""))
        if (
            not inventory_row
            or row.get("inventory_package_id") != inventory_row.get("package_id")
            or row.get("inventory_row_sha256") != _stable_sha256(inventory_row)
        ):
            raise SpeechBuildError("verified final inventory binding is invalid: %s" % clip_id)
        candidate_start = int(candidate.get("source_start_frame", -1))
        candidate_frames = int(candidate.get("source_frame_count", -1))
        final_start = int(row.get("source_start_frame", -1))
        final_frames = int(row.get("source_frame_count", -1))
        if final_frames != int(row.get("frames", -1)) or int(row.get("sample_rate") or 0) != 16000:
            raise SpeechBuildError("verified final frame contract is invalid: %s" % clip_id)
        run_sha = row.get("identity_run_sha256")
        window_index_value = row.get("identity_window_index")
        if window_index_value is not None:
            try:
                window_index = int(window_index_value)
            except (TypeError, ValueError) as exc:
                raise SpeechBuildError(
                    "verified final identity-window index is invalid: %s" % clip_id
                ) from exc
            matching = [run for run in (decision.get("accepted_runs") or [])
                        if run.get("identity_run_sha256") == run_sha]
            if len(matching) != 1:
                raise SpeechBuildError(
                    "verified final has no unique qualifying identity run")
            run = matching[0]
            lineage = _identity_window_lineage(
                candidate, decision, run, window_index)
            expected_clip_id = _identity_window_clip_id(
                parent_clip_id, run, window_index)
            if (
                    decision.get("status") not in {
                        "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                        "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS",
                    }
                    or run.get("parent_candidate_clip_sha256")
                    != candidate.get("clip_sha256")
                    or run.get("scoring_contract_sha256")
                    != chain["scoring_contract_sha256"]
                    or clip_id != expected_clip_id
                    or row.get("identity_window_sha256")
                    != lineage["identity_window_sha256"]
                    or row.get("identity_qualifying_run_start_window")
                    != lineage["qualifying_run_start_window"]
                    or row.get("identity_qualifying_run_end_window_exclusive")
                    != lineage["qualifying_run_end_window_exclusive"]
                    or final_start != lineage["source_start_frame"]
                    or final_frames != lineage["source_frame_count"]
                    or final_frames != 8 * 16000):
                raise SpeechBuildError(
                    "verified final identity-window lineage is invalid")
            if (
                    decision.get("status")
                    == "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                    and (
                        decision.get("unanimous_windows") is not True
                        or decision.get("full_candidate_identity_coverage") is not True
                        or int(decision.get("candidate_total_frames", -1))
                        != candidate_frames
                        or int(decision.get("identity_tested_frame_count", -1))
                        != candidate_frames
                        or int(decision.get("identity_excluded_tail_frames", -1)) != 0
                        or candidate_frames != len(windows) * 8 * 16000
                    )):
                raise SpeechBuildError(
                    "whole accepted identity-window coverage is invalid")
        elif run_sha is None:
            if (
                clip_id != parent_clip_id
                or decision.get("status")
                != "ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                or decision.get("unanimous_windows") is not True
                or decision.get("full_candidate_identity_coverage") is not True
                or int(decision.get("candidate_total_frames", -1)) != candidate_frames
                or int(decision.get("identity_tested_frame_count", -1)) != candidate_frames
                or int(decision.get("identity_excluded_tail_frames", -1)) != 0
                or any(window.get("passed") is not True
                       or window.get("winner") != member for window in windows)
                or candidate_frames != len(windows) * 8 * 16000
                or final_start != candidate_start
                or final_frames != candidate_frames
                or row.get("audio_sha256") != candidate.get("audio_sha256")
            ):
                raise SpeechBuildError("whole verified final is not the exact accepted candidate")
        else:
            matching = [run for run in (decision.get("accepted_runs") or [])
                        if run.get("identity_run_sha256") == run_sha]
            if len(matching) != 1:
                raise SpeechBuildError("partial verified final has no unique accepted identity run")
            run = matching[0]
            start_window = int(run.get("start_window", -1))
            end_window = int(run.get("end_window_exclusive", -1))
            expected_clip_id = "%s:run:%d-%d" % (
                parent_clip_id, start_window, end_window,
            )
            if (
                decision.get("status")
                != "PARTIALLY_ACCEPTED_100_PERCENT_STRUCTURAL_CONSENSUS"
                or run_sha != _row_field_sha256(run, "identity_run_sha256")
                or run.get("all_windows_passed") is not True
                or run.get("parent_candidate_clip_sha256") != candidate.get("clip_sha256")
                or run.get("scoring_contract_sha256") != chain["scoring_contract_sha256"]
                or not (0 <= start_window < end_window <= len(windows))
                or end_window - start_window != int(run.get("window_count") or -1)
                or int(run.get("window_count") or 0) < 3
                or int(run.get("relative_start_frame", -1)) != start_window * 8 * 16000
                or int(run.get("relative_frame_count", -1))
                != int(run.get("window_count") or -1) * 8 * 16000
                or len(windows[start_window:end_window])
                != int(run.get("window_count") or -1)
                or any(
                    int(window.get("window_index", -1)) != absolute_index
                    or window.get("passed") is not True
                    or window.get("winner") != member
                    for absolute_index, window in enumerate(
                        windows[start_window:end_window], start_window
                    )
                )
                or clip_id != expected_clip_id
                or final_start != int(run.get("source_start_frame", -1))
                or final_frames != int(run.get("source_frame_count", -1))
                or final_start < candidate_start
                or final_start + final_frames > candidate_start + candidate_frames
            ):
                raise SpeechBuildError("partial verified final run geometry is invalid")
        final_path = _require_file_hash(
            row.get("wav"), row.get("audio_sha256"), "verified final WAV",
            file_hash_cache if verify_hash else None,
        )
        if _wav_facts(final_path)[3] != final_frames:
            raise SpeechBuildError("verified final WAV/frame count mismatch: %s" % clip_id)
        final_pcm = _pcm_sha256(final_path, 0, final_frames)
        source_pcm = _pcm_sha256(source, final_start, final_frames)
        staged_relative_start = final_start - candidate_start
        staged_pcm = _pcm_sha256(staged, staged_relative_start, final_frames)
        if (
            final_pcm != source_pcm
            or final_pcm != staged_pcm
            or final_pcm != row.get("source_slice_pcm_sha256")
        ):
            raise SpeechBuildError("verified final PCM differs from its secured source frames")
        duration = final_frames / 16000.0
        if (
            abs(float(row.get("duration_sec") or 0) - duration) > 1e-6
            or abs((float(row.get("end_sec") or 0) - float(row.get("start_sec") or 0))
                   - duration) > 1e-6
        ):
            raise SpeechBuildError("verified final duration/frame geometry is invalid")
        measured = measure(final_path, smile_holder)
        if any(finite(measured.get(channel)) is None for channel in CHANNELS):
            raise SpeechBuildError("verified final has nonfinite recomputed eGeMAPS values")
        subject = str(row.get("subject") or "").strip() or None
        records.append({
            "clip_id": clip_id,
            "manifest_row_sha256": row["final_row_sha256"],
            "audio_sha256": row["audio_sha256"],
            "source_wav": str(Path(to_host(str(candidate["source_wav"]))).resolve()),
            "source_audio_sha256": row["source_audio_sha256"],
            "member": member,
            "outlet": supplement_outlet(row),
            "source_family": row.get("source_family"),
            "show": row.get("show"),
            "setting": _verified_setting(row),
            "subject": subject,
            "subject_status": "LABELED" if subject else "UNLABELED",
            "reference_url": reference_url,
            "package": row.get("package"),
            "sid": row.get("inventory_package_id") or row.get("package"),
            "seg": row.get("turn_id"),
            "date": row.get("date"),
            "duration_seconds": round(duration, 6),
            "words": int(candidate.get("words") or 0) if run_sha is None else 0,
            "turn_words": int(candidate.get("words") or 0),
            "match": float(row.get("alignment_ratio") or 0),
            "identity": {
                "tier": "exhaustive_voice_verified",
                "label": "Exhaustive multi-anchor structural consensus",
                "basis": row.get("identity_contract"),
                "probability_claim": False,
                "identity_row_sha256": row.get("identity_row_sha256"),
                "identity_run_sha256": run_sha,
                "identity_window_index": window_index_value,
                "identity_window_sha256": row.get("identity_window_sha256"),
                "comparison_sha256": row.get("identity_comparison_sha256"),
                "calibration_sha256": row.get("calibration_sha256"),
                "scoring_contract_sha256": row.get("scoring_contract_sha256"),
            },
            "cut": {
                "status": "HASH_AND_PCM_VERIFIED",
                "start": row.get("start_sec"), "end": row.get("end_sec"),
                "source_start_frame": final_start, "frames": final_frames,
                "samplerate": 16000, "source_slice_pcm_sha256": final_pcm,
            },
            "measurement_source": "verified_final_opensmile_egemaps_v02_recomputed",
            "feature_provenance": {
                "verified_manifest": str(manifest_path.resolve()),
                "verified_manifest_sha256": chain["manifest_sha256"],
                "verified_final_row_sha256": row["final_row_sha256"],
                "measured_from_final_audio_sha256": row["audio_sha256"],
                "parent_candidate_features_trusted": False,
            },
            "measures": {channel: round(float(measured[channel]), 8)
                         for channel in CHANNELS},
        })
    return records, {
        "enabled": True,
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": chain["manifest_sha256"],
        "receipt": str(Path(receipt_path).resolve()),
        "receipt_sha256": chain["receipt_sha256"],
        "identity_manifest_sha256": chain["identity_manifest_sha256"],
        "identity_receipt_sha256": chain["identity_receipt_sha256"],
        "candidate_manifest_sha256": chain["candidate_manifest_sha256"],
        "candidate_receipt_sha256": chain["candidate_receipt_sha256"],
        "inventory_sha256": chain["inventory_sha256"],
        "calibration_sha256": chain["calibration_sha256"],
        "scoring_contract_sha256": chain["scoring_contract_sha256"],
        "minimum_alignment_ratio": chain["minimum_alignment_ratio"],
        "cumulative_source_bindings_complete": chain["cumulative_source_bindings_complete"],
        "by_source": chain["by_source"],
        "canonical_source_overlap_requires_record_supersession": True,
        "accepted_clips": len(records),
        "features_recomputed_from_every_final_wav": True,
        "candidate_features_trusted": False,
        "source_pcm_reverified": True,
        "opensmile_version": smile_holder.get("version"),
    }


def load_cache(path):
    cached = {}
    for row in read_jsonl(path, required=False):
        digest = str(row.get("audio_sha256") or "")
        if len(digest) == 64:
            cached[digest] = row
    return cached


def measurement_from_feature(found):
    row = found["row"]
    measured = {name: finite(row.get(name)) for name in CHANNELS}
    if any(value is None for value in measured.values()):
        raise SpeechBuildError("feature row has missing/nonfinite eGeMAPS values: %s" %
                               found["path"])
    return measured


def build_clip_records(manifest_rows, roster, indices, cache, verify_hash=True,
                       measure_missing=True, cache_path=None, checkpoint_every=100):
    npr_path, npr_bindings = load_npr_bindings()
    sunday_path, sunday_bindings = load_sunday_bindings()
    local_youtube_bindings, local_youtube_receipts = load_local_youtube_bindings()
    smile_holder = {"smile": None, "version": None}
    records = []
    checkpoint_cache = dict(cache)
    counts = Counter()
    for number, row in enumerate(manifest_rows, 1):
        member = str(row.get("member") or "")
        if member not in roster:
            raise SpeechBuildError("manifest member is outside current roster: %r" % member)
        outlet = str(row.get("outlet") or "").upper()
        path = wav_contract(row, verify_hash)
        digest = str(row["sha256"])
        manifest_digest = row_sha256(row)
        prior = cache.get(digest)
        found = (indices.get(outlet) or {}).get(feature_key(row, outlet))
        identity = classify_identity(
            row, npr_bindings, sunday_bindings, found,
            local_youtube_bindings=local_youtube_bindings,
        )
        if (prior and prior.get("manifest_row_sha256") == manifest_digest and
                all(finite((prior.get("measures") or {}).get(ch)) is not None for ch in CHANNELS)):
            measured = {ch: float(prior["measures"][ch]) for ch in CHANNELS}
            measurement_source = prior.get("measurement_source") or "cache"
            feature_provenance = prior.get("feature_provenance")
            counts["cache_reused"] += 1
        elif found is not None:
            measured = measurement_from_feature(found)
            measurement_source = "accepted_span_feature"
            feature_provenance = {
                "path": str(found["path"].resolve()),
                "file_sha256": found["file_sha256"],
                "row_sha256": found["row_sha256"],
            }
            counts["feature_reused"] += 1
        elif measure_missing:
            measured = measure_file(path, smile_holder)
            measurement_source = "opensmile_egemaps_v02_local"
            feature_provenance = None
            counts["locally_measured"] += 1
        else:
            raise SpeechBuildError("accepted clip lacks a measurement: manifest line %d" % number)
        record = {
            "clip_id": manifest_digest,
            "manifest_row_sha256": manifest_digest,
            "audio_sha256": digest,
            "member": member,
            "outlet": outlet,
            "show": row.get("show"),
            "setting": setting_family(row),
            "sid": row.get("sid"),
            "seg": row.get("seg"),
            "date": row.get("date") or row.get("turn_date"),
            "duration_seconds": round(int(row["frames"]) / 16000.0, 6),
            "words": int(row.get("words") or 0),
            "match": float(row.get("match") or 0),
            "identity": identity,
            "cut": {
                "status": "HASH_AND_PCM_VERIFIED",
                "start": row.get("start"), "end": row.get("end"),
                "frames": int(row["frames"]), "samplerate": int(row["samplerate"]),
            },
            "measurement_source": measurement_source,
            "feature_provenance": feature_provenance,
            "measures": {key: round(float(value), 8) for key, value in measured.items()},
        }
        records.append(record)
        checkpoint_cache[digest] = record
        counts["accepted"] += 1
        counts["identity_" + identity["tier"]] += 1
        counts["setting_" + record["setting"]] += 1
        if (cache_path and measurement_source == "opensmile_egemaps_v02_local" and
                counts["locally_measured"] > 0 and
                counts["locally_measured"] % checkpoint_every == 0):
            atomic_jsonl(cache_path, [checkpoint_cache[key]
                                     for key in sorted(checkpoint_cache)])
            print("speech measurement checkpoint: local=%d accepted=%d" %
                  (counts["locally_measured"], counts["accepted"]), flush=True)
    if (smile_holder.get("version") is None and
            any(row.get("measurement_source") == "opensmile_egemaps_v02_local"
                for row in records)):
        try:
            import opensmile
            smile_holder["version"] = getattr(opensmile, "__version__", "unknown")
        except ImportError as exc:
            raise SpeechBuildError("cached local measurements require the opensmile runtime") from exc
    return records, counts, {
        "npr_binding_path": str(npr_path.resolve()),
        "npr_binding_sha256": sha256_file(npr_path),
        "sunday_binding_path": str(sunday_path.resolve()),
        "sunday_binding_sha256": sha256_file(sunday_path),
        "local_youtube_verified_bindings": len(local_youtube_bindings),
        "local_youtube_verification_receipts": local_youtube_receipts,
        "opensmile_version": smile_holder.get("version"),
    }


def mean_sd(values):
    clean = [float(value) for value in values if finite(value) is not None]
    if not clean:
        return None, None
    return (sum(clean) / len(clean), statistics.stdev(clean) if len(clean) > 1 else None)


def block(rows):
    result = {
        "clips": len(rows),
        "minutes": round(sum(float(row["duration_seconds"]) for row in rows) / 60.0, 3),
        "episodes": len({(row.get("outlet"), row.get("sid")) for row in rows}),
    }
    for channel in CHANNELS:
        mean, sd = mean_sd([row["measures"][channel] for row in rows])
        result[channel + "_mean"] = round(mean, 6) if mean is not None else None
        result[channel + "_sd"] = round(sd, 6) if sd is not None else None
    return result


def ci95(values):
    if len(values) < 2:
        return None
    se = statistics.stdev(values) / math.sqrt(len(values))
    mean = statistics.mean(values)
    return [round(mean - 1.96 * se, 6), round(mean + 1.96 * se, 6)]


def aggregate_members(records):
    grouped = defaultdict(list)
    for row in records:
        grouped[row["member"]].append(row)
    members = {}
    for member, rows in sorted(grouped.items()):
        by_setting_rows = defaultdict(list)
        for row in rows:
            by_setting_rows[row["setting"]].append(row)
        by_setting = {setting: block(value)
                      for setting, value in sorted(by_setting_rows.items())}
        by_source_rows = defaultdict(list)
        by_subject_rows = defaultdict(list)
        for row in rows:
            by_source_rows[str(row.get("source_family") or row.get("outlet") or "unknown")].append(row)
            if row.get("subject"):
                by_subject_rows[str(row["subject"])].append(row)
        by_source = {source: block(value)
                     for source, value in sorted(by_source_rows.items())}
        by_subject = {subject: block(value)
                      for subject, value in sorted(by_subject_rows.items())}
        subject_contrasts = []
        for first, second in itertools.combinations(sorted(by_subject), 2):
            if by_subject[first]["clips"] < 2 or by_subject[second]["clips"] < 2:
                continue
            subject_contrasts.append({
                "first": first,
                "second": second,
                "first_clips": by_subject[first]["clips"],
                "second_clips": by_subject[second]["clips"],
                "delta_first_minus_second": {
                    channel: round(
                        by_subject[first][channel + "_mean"]
                        - by_subject[second][channel + "_mean"],
                        6,
                    )
                    for channel in CHANNELS
                },
            })
        interview_rows = [row for row in rows if row["setting"] in INTERVIEW_SETTINGS]
        contrasts = []
        for first, second in itertools.combinations(sorted(by_setting), 2):
            if by_setting[first]["clips"] < 2 or by_setting[second]["clips"] < 2:
                continue
            contrasts.append({
                "first": first, "second": second,
                "first_clips": by_setting[first]["clips"],
                "second_clips": by_setting[second]["clips"],
                "delta_first_minus_second": {
                    channel: round(by_setting[first][channel + "_mean"] -
                                   by_setting[second][channel + "_mean"], 6)
                    for channel in CHANNELS
                },
            })
        identity_counts = Counter(row["identity"]["tier"] for row in rows)
        members[member] = {
            "coverage": {
                "clips": len(rows),
                "minutes": round(sum(row["duration_seconds"] for row in rows) / 60.0, 3),
                "settings": sorted(by_setting),
                "setting_count": len(by_setting),
                "source_families": sorted(by_source),
                "subjects": sorted(by_subject),
                "identity_tiers": dict(sorted(identity_counts.items())),
                "cut_status": "ALL_HASH_AND_PCM_VERIFIED",
                "reference_urls": sorted({str(row.get("reference_url")) for row in rows
                                           if row.get("reference_url")}),
            },
            # Standing venue rule: floor speech is a setting/control, never the member headline.
            "headline_interviews_only": block(interview_rows) if interview_rows else None,
            "by_setting": by_setting,
            "by_source": by_source,
            "by_subject": by_subject,
            "setting_contrasts": contrasts,
            "subject_tonality_contrasts": subject_contrasts,
            "subject_effects": [],
            "caveats": (["House-floor audio is displayed only as a setting contrast; it is not "
                         "pooled into the interview-based speech-habit headline."]
                        if "house_floor" in by_setting else []),
        }
    return members


def add_percentiles(members):
    for channel in ("pause", "pace", "volume_dyn", "pitch_var"):
        values = sorted(
            (float(row["headline_interviews_only"][channel + "_mean"]), name)
            for name, row in members.items()
            if row.get("headline_interviews_only") and
            row["headline_interviews_only"].get(channel + "_mean") is not None and
            row["headline_interviews_only"].get("clips", 0) >= 3
        )
        positions = defaultdict(list)
        for index, (value, _name) in enumerate(values):
            positions[value].append(index)
        for value, name in values:
            indexes = positions[value]
            pct = 50.0 if len(values) == 1 else 100.0 * statistics.mean(indexes) / (len(values) - 1)
            members[name]["headline_interviews_only"][channel + "_percentile"] = round(pct, 1)


def global_setting_effects(members):
    settings = sorted({setting for row in members.values() for setting in row["by_setting"]})
    channels = {}
    for channel in CHANNELS:
        comparisons = []
        for first, second in itertools.combinations(settings, 2):
            diffs = []
            for row in members.values():
                a = row["by_setting"].get(first)
                b = row["by_setting"].get(second)
                if not a or not b or a["clips"] < 2 or b["clips"] < 2:
                    continue
                diffs.append(a[channel + "_mean"] - b[channel + "_mean"])
            if len(diffs) < 5:
                continue
            comparisons.append({
                "first": first, "second": second,
                "n_members": len(diffs),
                "mean_delta_first_minus_second": round(statistics.mean(diffs), 6),
                "median_delta_first_minus_second": round(statistics.median(diffs), 6),
                "ci95_normal": ci95(diffs),
            })
        channels[channel] = comparisons
    return {
        "method": "matched-member setting means; each member contributes one difference; "
                  "minimum two clips in each setting and five matched members; descriptive, not causal",
        "setting_labels": SETTING_LABELS,
        "channels": channels,
    }


def global_subject_tonality_effects(members):
    """Matched-member descriptive contrasts for labeled recovered subjects."""
    subjects = sorted({subject for row in members.values() for subject in row["by_subject"]})
    channels = {}
    for channel in CHANNELS:
        comparisons = []
        for first, second in itertools.combinations(subjects, 2):
            diffs = []
            for row in members.values():
                first_block = row["by_subject"].get(first)
                second_block = row["by_subject"].get(second)
                if (
                    not first_block
                    or not second_block
                    or first_block["clips"] < 2
                    or second_block["clips"] < 2
                ):
                    continue
                diffs.append(
                    first_block[channel + "_mean"]
                    - second_block[channel + "_mean"]
                )
            if len(diffs) < 5:
                continue
            comparisons.append({
                "first": first,
                "second": second,
                "n_members": len(diffs),
                "mean_delta_first_minus_second": round(statistics.mean(diffs), 6),
                "median_delta_first_minus_second": round(statistics.median(diffs), 6),
                "ci95_normal": ci95(diffs),
            })
        channels[channel] = comparisons
    return {
        "method": (
            "matched-member labeled-subject means; each member contributes one difference; "
            "minimum two clips per subject and five matched members; descriptive, not causal"
        ),
        "subjects": subjects,
        "channels": channels,
    }


def reaction_key(row):
    return (str(row.get("member") or ""), str(row.get("pid") or ""),
            int(row.get("member_offset") or 0))


def topic_key(row):
    return (str(row.get("member") or ""), str(row.get("program") or ""),
            int(row.get("member_offset") or 0))


def subject_effects(reactions_path, topics_path, members):
    topics = defaultdict(list)
    for row in read_jsonl(topics_path):
        key = topic_key(row)
        topics[key].append(row)
    duplicate_topic_keys = sum(1 for rows in topics.values() if len(rows) > 1)
    # Prefer the padded capture for duplicate clip families, then the longer capture.
    best = {}
    for row in read_jsonl(reactions_path):
        rank = (0 if row.get("pad_family") == "padded_v2" else 1,
                -float(row.get("clip_dur_s") or 0), str(row.get("wav") or ""))
        key = reaction_key(row)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, row)
    joined = []
    missing = ambiguous = excluded = 0
    for _rank, reaction in best.values():
        topic_options = topics.get(reaction_key(reaction)) or []
        if not topic_options:
            missing += 1
            continue
        # The legacy Path-A ``member_offset`` column is degenerate for a small
        # number of programs.  Never guess among multiple topic rows sharing a
        # key: only a unique topic row can enter subject-effect aggregation.
        if len(topic_options) != 1:
            ambiguous += 1
            continue
        topic = topic_options[0]
        if topic.get("excluded_from_profile") or topic.get("identity_status") == "SUSPECT":
            excluded += 1
            continue
        label = topic.get("topic")
        if not label or label in ("UNLABELED", "PROCEDURAL"):
            continue
        joined.append({
            "member": reaction["member"], "topic": label,
            "f0_delta": float(reaction["f0_delta"]),
            "loud_delta": float(reaction["loud_delta"]),
            "identity_status": topic.get("identity_status"),
            "stim_class": topic.get("stim_class"),
        })
    member_center = {}
    for member in {row["member"] for row in joined}:
        subset = [row for row in joined if row["member"] == member]
        member_center[member] = {
            "f0": statistics.mean(row["f0_delta"] for row in subset),
            "loud": statistics.mean(row["loud_delta"] for row in subset),
        }
    by_topic = defaultdict(list)
    by_member_topic = defaultdict(list)
    for row in joined:
        enriched = dict(row)
        enriched["f0_within_member"] = row["f0_delta"] - member_center[row["member"]]["f0"]
        enriched["loud_within_member"] = row["loud_delta"] - member_center[row["member"]]["loud"]
        by_topic[row["topic"]].append(enriched)
        by_member_topic[(row["member"], row["topic"])].append(enriched)
    global_topics = {}
    for topic, rows in sorted(by_topic.items()):
        people = {row["member"] for row in rows}
        if len(rows) < 5 or len(people) < 3:
            continue
        global_topics[topic] = {
            "reactions": len(rows), "members": len(people),
            "mean_f0_delta_semitones": round(statistics.mean(r["f0_delta"] for r in rows), 6),
            "mean_loudness_delta": round(statistics.mean(r["loud_delta"] for r in rows), 6),
            "within_member_f0_shift_semitones": round(
                statistics.mean(r["f0_within_member"] for r in rows), 6),
            "within_member_loudness_shift": round(
                statistics.mean(r["loud_within_member"] for r in rows), 6),
        }
    for (member, topic), rows in sorted(by_member_topic.items()):
        if member not in members or len(rows) < 2:
            continue
        members[member]["subject_effects"].append({
            "topic": topic, "reactions": len(rows),
            "mean_f0_delta_semitones": round(statistics.mean(r["f0_delta"] for r in rows), 6),
            "mean_loudness_delta": round(statistics.mean(r["loud_delta"] for r in rows), 6),
            "relative_to_member_f0_semitones": round(
                statistics.mean(r["f0_within_member"] for r in rows), 6),
            "relative_to_member_loudness": round(
                statistics.mean(r["loud_within_member"] for r in rows), 6),
        })
    for row in members.values():
        row["subject_effects"].sort(
            key=lambda item: (-item["reactions"], item["topic"]))
        if row["subject_effects"]:
            row["caveats"].append(
                "Subject effects use Washington Journal stimulus-response windows. The reaction "
                "channel is descriptively useful but not a stable member trait and is not causal."
            )
    return {
        "method": "Washington Journal reaction windows joined to Path-A v3 stimulus topics; "
                  "one padded-preferred capture per member turn; suspect identity rows excluded; "
                  "non-unique legacy offsets excluded; within-member centering; descriptive, not causal",
        "reaction_rows": len(best), "joined_labeled_rows": len(joined),
        "missing_topic_join": missing, "ambiguous_topic_join": ambiguous,
        "duplicate_topic_keys": duplicate_topic_keys, "suspect_excluded": excluded,
        "topics": global_topics,
        "warning": "The topic taxonomy is not adjudicated and reaction cross-family reliability "
                   "was previously r=0.20. Do not interpret as a personality or causal effect.",
    }


def interview_transcript_inventory():
    """Inventory collected text separately from audio that can support tonality."""
    path = Path(ROOT) / "social_media.db"
    if not path.is_file():
        return {"available": False, "path": to_stored(path), "entered_prepared_audio_corpus": 0}
    groups = defaultdict(lambda: {
        "turns": 0, "members": set(), "words": 0, "first_date": None, "last_date": None,
    })
    uri = "file:%s?mode=ro" % path.resolve().as_posix()
    connection = sqlite3.connect(uri, uri=True)
    try:
        rows = connection.execute(
            "select member,date,package,nwords from speeches where source='INTERVIEW'"
        )
        for member, date, package, words in rows:
            host = (urlsplit(str(package or "")).hostname or "non-url").lower()
            row = groups[host]
            row["turns"] += 1
            row["members"].add(str(member or ""))
            row["words"] += int(words or 0)
            if date and (row["first_date"] is None or date < row["first_date"]):
                row["first_date"] = date
            if date and (row["last_date"] is None or date > row["last_date"]):
                row["last_date"] = date
    finally:
        connection.close()
    sources = {}
    for host, row in sorted(groups.items()):
        sources[host] = {
            "turns": row["turns"], "members": len(row["members"]), "words": row["words"],
            "first_date": row["first_date"], "last_date": row["last_date"],
        }
    facts = path.stat()
    return {
        "available": True,
        "path": to_stored(path.resolve()),
        "snapshot_size_bytes": facts.st_size,
        "snapshot_mtime_ns": facts.st_mtime_ns,
        "scope": "member-attributed rows where speeches.source = INTERVIEW, grouped by package host",
        "classification": "collected attributed transcript text; not evidence for acoustic tonality",
        "sources": sources,
        "entered_prepared_audio_corpus": 0,
    }


def corpus_summary(records, manifest_rows):
    by_outlet = {}
    for outlet in sorted({row["outlet"] for row in records}):
        subset = [row for row in records if row["outlet"] == outlet]
        by_outlet[outlet] = {
            "clips": len(subset),
            "hours": round(sum(row["duration_seconds"] for row in subset) / 3600.0, 3),
            "members": len({row["member"] for row in subset}),
        }
    by_show = {}
    for show in sorted({str(row.get("show") or "Unknown") for row in records}):
        subset = [row for row in records if str(row.get("show") or "Unknown") == show]
        by_show[show] = {
            "clips": len(subset),
            "hours": round(sum(row["duration_seconds"] for row in subset) / 3600.0, 3),
            "members": len({row["member"] for row in subset}),
        }
    by_source_family = {}
    source_labels = {str(row.get("source_family") or row.get("outlet") or "unknown")
                     for row in records}
    for source in sorted(source_labels):
        subset = [row for row in records
                  if str(row.get("source_family") or row.get("outlet") or "unknown") == source]
        by_source_family[source] = {
            "clips": len(subset),
            "hours": round(sum(row["duration_seconds"] for row in subset) / 3600.0, 3),
            "members": len({row["member"] for row in subset}),
        }
    by_subject = {}
    for subject in sorted({str(row["subject"]) for row in records if row.get("subject")}):
        subset = [row for row in records if str(row.get("subject") or "") == subject]
        by_subject[subject] = {
            "clips": len(subset),
            "hours": round(sum(row["duration_seconds"] for row in subset) / 3600.0, 3),
            "members": len({row["member"] for row in subset}),
        }
    legacy_root = Path(ROOT) / "audio_pilot"
    legacy_wavs = list(legacy_root.rglob("*.wav")) if legacy_root.is_dir() else []
    legacy_parts = list(legacy_root.rglob("*.part")) if legacy_root.is_dir() else []
    youtube_root = Path(MELANGE_DATA, "youtube_audio")
    youtube_rows = read_jsonl(youtube_root / "youtube_manifest.jsonl", required=False)
    youtube_channels = Counter(str(row.get("channel") or "Unknown") for row in youtube_rows)
    youtube_identities = Counter(str(row.get("identity") or "missing") for row in youtube_rows)
    discovery_path = youtube_root / "channel_tally.json"
    discovered_news = {}
    if discovery_path.is_file():
        tally = read_json(discovery_path)
        for row in tally.get("channels") or []:
            channel = str(row.get("channel") or "")
            if any(token in channel.lower() for token in
                   ("fox", "abc", "cnn", "cbs", "nbc", "ms now", "msnbc", "pbs", "c-span",
                    "forbes breaking news")):
                discovered_news[channel] = {
                    "hits": int(row.get("hits") or 0),
                    "allowlisted": bool(row.get("allowlisted")),
                }
    recovery_summary_path = Path(ROOT) / "data" / "speech" / "source_media_inventory_v1.summary.json"
    source_media_recovery = None
    if recovery_summary_path.is_file():
        recovery = read_json(recovery_summary_path)
        accepted_by_family = Counter(
            str(row.get("source_family") or "unknown")
            for row in records
            if row.get("measurement_source") in {
                "audited_source_media_opensmile_egemaps_v02",
                "verified_final_opensmile_egemaps_v02_recomputed",
            }
        )
        recovery_sources = {}
        for family, row in sorted((recovery.get("by_source") or {}).items()):
            states = row.get("status") or {}
            recovery_sources[family] = {
                "packages": int(row.get("packages") or 0),
                "turns": int(row.get("turns") or 0),
                "prepared_packages": int(states.get("prepared") or 0),
                "exact_timed_packages": int(states.get("exact_timed_captions") or 0),
                "ambiguous_packages": int(states.get("ambiguous") or 0),
                "no_media_packages": int(states.get("no_media") or 0),
                "blocked_packages": int(states.get("blocked_no_exact_media") or 0) +
                                    int(states.get("blocked_no_public_media_id") or 0),
                "pending_packages": int(states.get("needs_probe") or 0) +
                                    int(states.get("probe_error") or 0) +
                                    (int(states.get("blocked_no_exact_media") or 0)
                                     if family == "msnow" else 0),
                "audited_clips": int(accepted_by_family.get(family, 0)),
            }
        source_media_recovery = {
            "status": "COMPLETE" if recovery.get("all_packages_dispositioned") else "IN_PROGRESS",
            "packages": int(recovery.get("packages") or 0),
            "turns": int(recovery.get("turns") or 0),
            "pending_packages": int(recovery.get("pending_packages") or 0),
            "stale_dispositions_requiring_reprobe": int(
                recovery.get("stale_dispositions_requiring_reprobe") or 0
            ),
            "inventory_summary": to_stored(recovery_summary_path.resolve()),
            "inventory_summary_sha256": sha256_file(recovery_summary_path),
            "by_source": recovery_sources,
        }
    return {
        "manifest_rows": len(manifest_rows),
        "prepared_rows": len(records),
        "excluded_rows": len(manifest_rows) - len(records),
        "hours": round(sum(row["duration_seconds"] for row in records) / 3600.0, 3),
        "members": len({row["member"] for row in records}),
        "by_outlet": by_outlet,
        "by_show": by_show,
        "by_source_family": by_source_family,
        "by_subject": by_subject,
        "unlabeled_subject_clips": sum(not row.get("subject") for row in records),
        "identity_tiers": dict(sorted(Counter(
            row["identity"]["tier"] for row in records).items())),
        "cut_status": "ALL_HASH_AND_PCM_VERIFIED",
        "legacy_audio_pilot": {
            "wav_files": len(legacy_wavs), "incomplete_part_files": len(legacy_parts),
            "classification": "source-only legacy corpus; not merged because it lacks the "
                              "accepted manifest's uniform turn/cut provenance",
            "entered_prepared_corpus": 0,
        },
        "raw_youtube_audio": {
            "files": len(youtube_rows),
            "hours": round(sum(float(row.get("measured_duration") or 0)
                               for row in youtube_rows) / 3600.0, 3),
            "channels": dict(sorted(youtube_channels.items())),
            "identity": dict(sorted(youtube_identities.items())),
            "discovered_news_channels": dict(sorted(discovered_news.items())),
            "classification": "raw discovery supply; title-match-only identity is insufficient",
            "entered_prepared_corpus": 0,
        },
        "collected_interview_transcripts": interview_transcript_inventory(),
        "source_media_recovery": source_media_recovery,
    }


def build(args):
    manifest_rows, manifest_sha, roster, audit = validate_manifest_contract(
        args.manifest.resolve(), args.audit.resolve(), args.roster.resolve())
    supplement_records, supplement_meta = validate_supplement_contract(
        args.supplement_manifest.resolve(), args.supplement_receipt.resolve(), roster,
        verify_hash=not args.no_verify_audio_hashes,
    )
    verified_records, verified_meta = validate_verified_final_contract(
        args.verified_manifest.resolve(), args.verified_receipt.resolve(),
        args.identity_manifest.resolve(), args.identity_receipt.resolve(),
        args.candidate_manifest.resolve(), args.candidate_receipt.resolve(),
        args.source_media_inventory.resolve(), args.speaker_calibration.resolve(), roster,
        # The verified-final lane always hashes and PCM-checks its inputs.  The
        # legacy performance escape hatch may not weaken this acceptance gate.
        verify_hash=True,
    )
    cnn_lineage_required = any(
        _verified_source_needs_cnn_lineage(row) for row in verified_records
    )
    canonical_lineage, canonical_lineage_meta = validate_canonical_source_lineage(
        manifest_rows, manifest_sha, args.canonical_lineage.resolve(),
        args.canonical_lineage_receipt.resolve(), required=cnn_lineage_required,
        # The legacy canonical-hash escape hatch cannot weaken lineage overlap
        # detection for current-contract CNN finals.
        verify_hash=True,
    )
    groups = feature_paths()
    indices = {}
    feature_stats = {}
    source_hashes = {}
    for outlet, paths in groups.items():
        indices[outlet], feature_stats[outlet] = load_feature_index(paths, outlet)
        source_hashes[outlet] = [{"path": str(path.resolve()), "sha256": sha256_file(path)}
                                  for path in paths]
    cache = load_cache(args.cache.resolve())
    records, counts, binding_meta = build_clip_records(
        manifest_rows, roster, indices, cache,
        verify_hash=not args.no_verify_audio_hashes,
        measure_missing=not args.no_measure_missing,
        cache_path=args.cache.resolve(),
    )
    if len(records) != len(manifest_rows):
        raise SpeechBuildError("prepared corpus does not partition the complete canonical manifest")
    canonical_records = list(records)
    records, superseded, verified_records = supersede_canonical_source_overlaps(
        canonical_records, manifest_rows, verified_records, canonical_lineage,
    )
    superseded_ids = {
        row["canonical_manifest_row_sha256"] for row in superseded
    }
    for row in canonical_records:
        if row["manifest_row_sha256"] not in superseded_ids:
            continue
        counts["accepted"] -= 1
        counts["identity_" + row["identity"]["tier"]] -= 1
        counts["setting_" + row["setting"]] -= 1
        counts["canonical_superseded"] += 1
    groups_by_origin = [records, verified_records, supplement_records]
    _require_disjoint_origin_identities(groups_by_origin)
    records.extend(verified_records)
    records.extend(supplement_records)
    counts["verified_final_recomputed"] += len(verified_records)
    counts["supplemental_audited"] += len(supplement_records)
    counts["accepted"] += len(verified_records) + len(supplement_records)
    for row in [*verified_records, *supplement_records]:
        counts["identity_" + row["identity"]["tier"]] += 1
        counts["setting_" + row["setting"]] += 1
    members = aggregate_members(records)
    add_percentiles(members)
    settings = global_setting_effects(members)
    subjects = subject_effects(args.reactions.resolve(), args.topics.resolve(), members)
    subjects["verified_final_tonality"] = global_subject_tonality_effects(members)
    artifact = {
        "schema": SCHEMA,
        "status": STATUS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "display_only": True,
            "audio_may_modify_big_five": False,
            "audio_may_modify_rank_or_allocation": False,
            "identity_first": True,
            "complete_manifest_partition_required": True,
            "clip_hash_and_pcm_contract_required": True,
            "verified_source_media_receipt_chain_required": True,
            "verified_source_media_features_recomputed": True,
            "labeled_subject_tonality_stratified": True,
            "candidate_stage_features_may_enter_verified_measurements": False,
            "source_program_metadata_may_identify_speaker": False,
            "cross_source_and_canonical_deduplication_required": True,
            "canonical_source_interval_supersession_required": True,
            "source_audio_physical_cleanup_authorized": False,
            "floor_audio_in_headline": False,
            "setting_and_subject_effects_are_descriptive_not_causal": True,
        },
        "source": {
            "manifest": str(args.manifest.resolve()), "manifest_sha256": manifest_sha,
            "collection_audit": str(args.audit.resolve()),
            "collection_audit_sha256": sha256_file(args.audit.resolve()),
            "feature_sources": source_hashes,
            "feature_reconciliation": feature_stats,
            "bindings": binding_meta,
            "source_media_verified_final": verified_meta,
            "canonical_source_lineage": canonical_lineage_meta,
            "canonical_supersession": {
                "schema": "speech-canonical-record-supersession-v1",
                "status": "APPLIED" if superseded else "NOT_NEEDED",
                "superseded_rows": len(superseded),
                "supersession_rows_sha256": _stable_sha256(superseded),
                "physical_cleanup_authorized": False,
                "records": superseded,
            },
            "source_media_supplement": supplement_meta,
            "reactions_sha256": sha256_file(args.reactions.resolve()),
            "topics_sha256": sha256_file(args.topics.resolve()),
        },
        "corpus": corpus_summary(
            records, [*manifest_rows, *verified_records, *supplement_records]
        ),
        "measurement_counts": dict(sorted(counts.items())),
        "setting_effects": settings,
        "subject_effects": subjects,
        "members": members,
    }
    artifact["corpus"]["superseded_rows"] = len(superseded)
    if (artifact["corpus"]["prepared_rows"] + len(superseded)
            != artifact["corpus"]["manifest_rows"]):
        raise SpeechBuildError("active and superseded records do not partition the accepted corpus")
    atomic_jsonl(args.cache.resolve(), records)
    atomic_json(args.out.resolve(), artifact)
    receipt = {
        "schema": "speech-descriptive-receipt-v1",
        "status": "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest_sha,
        "manifest_rows": len(manifest_rows) + len(verified_records) + len(supplement_records),
        "canonical_manifest_rows": len(manifest_rows),
        "verified_final_manifest_rows": len(verified_records),
        "supplemental_manifest_rows": len(supplement_records),
        "prepared_rows": len(records),
        "excluded_rows": len(superseded),
        "superseded_rows": len(superseded),
        "canonical_source_lineage_receipt_sha256": canonical_lineage_meta.get("receipt_sha256"),
        "canonical_supersession_rows_sha256": _stable_sha256(superseded),
        "members": len(members),
        "artifact": str(args.out.resolve()),
        "artifact_sha256": sha256_file(args.out.resolve()),
        "cache": str(args.cache.resolve()),
        "cache_sha256": sha256_file(args.cache.resolve()),
        "contract": artifact["contract"],
        "collection_audit_status": audit.get("status"),
        "verified_final_audit_status": "PASS" if verified_meta.get("enabled") else "NOT_PRESENT",
        "verified_final_manifest_sha256": verified_meta.get("manifest_sha256"),
        "verified_final_receipt_sha256": verified_meta.get("receipt_sha256"),
        "supplement_audit_status": "PASS" if supplement_meta.get("enabled") else "NOT_PRESENT",
    }
    atomic_json(args.receipt.resolve(), receipt)
    return artifact, receipt


def main(argv=None):
    args = parse_args(argv)
    try:
        artifact, receipt = build(args)
    except SpeechBuildError as exc:
        print("SPEECH DESCRIPTIVE BUILD FAILED: %s" % exc, file=sys.stderr, flush=True)
        return 2
    corpus = artifact["corpus"]
    print("SPEECH DESCRIPTIVE PASS clips=%d hours=%.3f members=%d settings=%d subjects=%d" % (
        corpus["prepared_rows"], corpus["hours"], corpus["members"],
        len(artifact["setting_effects"]["setting_labels"]),
        len(artifact["subject_effects"]["topics"]),
    ), flush=True)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
