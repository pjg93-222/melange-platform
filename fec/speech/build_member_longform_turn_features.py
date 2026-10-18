#!/usr/bin/env python3
"""Measure entire hash-bound interview turns without promoting review authority.

Eight-second identity anchors remain unchanged. These whole-turn eGeMAPSv02
functionals are a separate, uncalibrated delivery lane, never short-clip ranks,
personality estimates, semantic findings, or political persuasion guidance.
Legacy labels alone cannot establish that a human checked the whole speaker
turn. Their physically valid measurements remain non-narrative diagnostics.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics
import wave
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fec.paths import MELANGE_DATA, to_host
from fec.speech import source_media_recovery as SMR
from fec.speech import longform_automated_authority as auto

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data/speech/member_longform_turn_features_v1.json"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_turn_features_v1.receipt.json"
DEFAULT_LEADERSHIP = ROOT / "data/speech/member_leadership_role_cutoffs_v1.json"
FEATURE_COLUMNS = {
    "pause": "MeanUnvoicedSegmentLength",
    "pace": "VoicedSegmentsPerSec",
    "volume_dyn": "loudness_sma3_stddevNorm",
    "pitch_var": "F0semitoneFrom27.5Hz_sma3nz_stddevNorm",
    "strain": "jitterLocal_sma3nz_amean",
    "f0_mean": "F0semitoneFrom27.5Hz_sma3nz_amean",
}
FEATURE_DEFINITIONS = {
    "pause": "Mean unvoiced-segment length in seconds; not hesitation or reluctance.",
    "pace": "Voiced segments per second; not words per minute.",
    "volume_dyn": "Normalized loudness variability; not absolute loudness.",
    "pitch_var": "Normalized pitch variability; not emotion or personality.",
    "strain": "Local jitter; a nonclinical acoustic measure, not psychological strain.",
    "f0_mean": "Mean fundamental frequency in semitones relative to 27.5 Hz; not Hz.",
}
MODERN_RECEIPT = "member-longform-reviewed-turn-receipt-v1"
RECEIPT_SCHEMAS = {
    MODERN_RECEIPT,
    "member-interview-long-turn-receipt-v1",
    "member-leadership-prerole-long-turn-receipt-v1",
}
CONTRACT = {
    "whole_turn_measurement_not_fixed_windows": True,
    "maximum_turn_seconds": None,
    "identity_anchors_modified": False,
    "physical_source_slice_and_transcript_binding_required": True,
    "legacy_review_labels_establish_whole_turn_speaker_identity": False,
    "qualified_requires_hash_bound_named_human_full_turn_review": False,
    "qualified_allows_user_authorized_source_bound_automated_verification": True,
    "automated_verification_is_human_review": False,
    "qualified_requires_recomputed_complete_interior_acoustic_coverage": True,
    "candidate_measurements_are_narrative_evidence": False,
    "current_leader_baseline_requires_pre_current_role": True,
    "recurrence_requires_two_independent_productions_and_settings": True,
    "source_counts_alone_establish_recurrence": False,
    "features_are_unadjusted_recording_specific_descriptions": True,
    "short_window_percentile_comparability_established": False,
    "automatic_trait_or_rank_effect": False,
    "stance_allocation_or_persuasion_effect": False,
    "semantic_findings_generated": False,
}


class FeatureRefusal(RuntimeError):
    pass


def row_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


class Inputs:
    """Hash physical dependencies once, then detect concurrent modifications."""

    def __init__(self):
        self.records: dict[str, dict] = {}

    def bind(self, path: Path, expected: str | None = None) -> Path:
        path = Path(path).resolve()
        if not path.is_file():
            raise FeatureRefusal(f"missing_input:{path}")
        key = str(path)
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        if key not in self.records:
            digest = SMR.sha256_file(path)
            after = path.stat()
            if signature != (after.st_size, after.st_mtime_ns):
                raise FeatureRefusal(f"input_changed_while_hashing:{path}")
            self.records[key] = {"path": key, "sha256": digest, "_signature": signature}
        record = self.records[key]
        if signature != record["_signature"]:
            raise FeatureRefusal(f"input_changed_during_build:{path}")
        if expected is not None and (not expected or record["sha256"] != expected):
            raise FeatureRefusal(f"input_hash_mismatch:{path}")
        return path

    def json(self, path: Path) -> dict:
        return json.loads(self.bind(path).read_text(encoding="utf-8-sig"))

    def jsonl(self, path: Path) -> list[dict]:
        return SMR.read_jsonl(self.bind(path), required=True)

    def snapshot(self) -> list[dict]:
        for record in self.records.values():
            self.bind(Path(to_host(record["path"])))
        return [{"path": key, "sha256": record["sha256"]}
                for key, record in sorted(self.records.items())]


def identity(row: dict) -> tuple[str, str]:
    return str(row.get("member") or ""), str(row.get("source_id") or row.get("video_id") or "")


def audio_hash(row: dict) -> str:
    return str(row.get("wav_sha256") or row.get("source_audio_sha256") or "")


def matching_source(rows: list[dict], turn: dict, *, transcript=False) -> dict:
    matches = [row for row in rows if identity(row) == identity(turn)
               and audio_hash(row) == turn.get("source_audio_sha256")
               and (not transcript or row.get("transcript_sha256") == turn.get("source_transcript_sha256"))]
    if not matches:
        raise FeatureRefusal(f"{'transcript' if transcript else 'source'}_binding_missing:{turn['turn_id']}")
    return matches[0]


def pcm(path: Path, *, start: float | None = None, end: float | None = None) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
            raise FeatureRefusal(f"wav_contract:{path}")
        first = 0 if start is None else round(start * 16000)
        last = source.getnframes() if end is None else round(end * 16000)
        if first < 0 or last <= first or last > source.getnframes():
            raise FeatureRefusal(f"turn_out_of_bounds:{path}")
        source.setpos(first)
        payload = source.readframes(last - first)
        if len(payload) != (last - first) * 2:
            raise FeatureRefusal(f"truncated_pcm:{path}")
        return payload, last - first


def measure_file(path: Path, holder: dict) -> dict:
    """Same feature set, aliases and rounding as the existing short verifier."""
    if holder.get("smile") is None:
        import opensmile
        holder["version"] = getattr(opensmile, "__version__", "unknown")
        holder["smile"] = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                                         feature_level=opensmile.FeatureLevel.Functionals)
    raw = holder["smile"].process_file(str(path)).iloc[0]
    return {name: round(float(raw[column]), 8) for name, column in FEATURE_COLUMNS.items()}


def source_era(turn: dict, source: dict, leadership: dict) -> tuple[str, str | None]:
    role = leadership.get(turn["member"])
    if not role:
        return "NOT_CURRENT_FORMAL_LEADERSHIP", None
    cutoff = str(role["current_role_assumed_on"])
    observed = turn.get("recorded_at") or source.get("recorded_at") or turn.get("published") or source.get("published")
    try:
        date = dt.date.fromisoformat(str(observed)[:10])
        before = date < dt.date.fromisoformat(cutoff[:10])
    except ValueError:
        return "LEADERSHIP_SOURCE_DATE_UNRESOLVED", cutoff
    return ("PRE_CURRENT_LEADERSHIP_ROLE" if before else "CURRENT_ROLE_CONDITIONED"), cutoff


def review_authority(turn: dict, receipt: dict, chain: dict[str, Path], inputs: Inputs) -> tuple[str, list[str]]:
    if receipt["schema"] != MODERN_RECEIPT:
        return "LEGACY_REVIEW_AUTHORITY_UNRESOLVED", ["Legacy status and sparse identity anchors do not independently establish named human review of the whole turn."]
    names = ("selection", "candidates", "continuity", "reviews")
    if not chain and all((receipt.get("input_paths") or {}).get(name) for name in names):
        chain = {name: Path(to_host(receipt["input_paths"][name])) for name in names}
    if any(name not in chain for name in names):
        return "MODERN_REVIEW_CHAIN_NOT_PROVIDED", ["All four materializer review-chain inputs are required for qualified measurements."]
    data = {}
    for name in names:
        inputs.bind(chain[name], str((receipt.get("inputs") or {}).get(name + "_sha256") or ""))
        data[name] = inputs.jsonl(chain[name])
    selected = {}
    for name in names[:3]:
        matches = [row for row in data[name] if identity(row) == identity(turn)]
        if len(matches) != 1:
            raise FeatureRefusal(f"review_chain_source_binding:{name}:{turn['turn_id']}")
        selected[name] = matches[0]
        if audio_hash(matches[0]) != turn["source_audio_sha256"] or matches[0].get("transcript_sha256") != turn["source_transcript_sha256"]:
            raise FeatureRefusal(f"review_chain_hash_binding:{name}:{turn['turn_id']}")
    if selected["selection"].get("status") != "SELECTED_HARD_NAME_CUE_CANDIDATE" or not selected["selection"].get("cues"):
        raise FeatureRefusal(f"review_chain_missing_named_selection:{turn['turn_id']}")
    candidate = selected["candidates"]
    if candidate.get("status") != "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED":
        raise FeatureRefusal(f"review_chain_candidate_status:{turn['turn_id']}")
    indexes = [index for index, item in enumerate(candidate.get("turns") or [])
               if abs(float(item["start_sec"]) - float(turn["start_sec"])) <= .001
               and abs(float(item["end_sec"]) - float(turn["end_sec"])) <= .001]
    if len(indexes) != 1:
        raise FeatureRefusal(f"review_chain_turn_boundary:{turn['turn_id']}")
    for module_name in ("build_member_longform_review_queue.py", "verify_member_longform_speaker_continuity.py"):
        inputs.bind(Path(__file__).with_name(module_name))
    from fec.speech.build_member_longform_review_queue import (
        qualified_turn_coverage, REQUIRED_CONSECUTIVE_WINDOWS,
    )
    coverage = qualified_turn_coverage(candidate, selected["continuity"], indexes[0], candidate["turns"][indexes[0]])
    if coverage is None:
        raise FeatureRefusal(f"complete_turn_continuity_not_verified:{turn['turn_id']}")
    if coverage["maximum_consecutive_passing_windows"] < REQUIRED_CONSECUTIVE_WINDOWS:
        raise FeatureRefusal(f"review_chain_identity_anchors:{turn['turn_id']}")
    if turn.get("turn_coverage") != coverage or turn.get("candidate_turn_binding_sha256") != coverage.get("turn_binding_sha256"):
        raise FeatureRefusal(f"materialized_turn_coverage_binding:{turn['turn_id']}")
    reviews = [row for row in data["reviews"] if row.get("turn_id") == turn["turn_id"]]
    if len(reviews) != 1:
        raise FeatureRefusal(f"review_chain_review_missing:{turn['turn_id']}")
    review = reviews[0]
    if identity(review) != identity(turn) or any(abs(float(review[key]) - float(turn[key])) > .001 for key in ("start_sec", "end_sec")):
        raise FeatureRefusal(f"human_review_turn_binding:{turn['turn_id']}")
    if audio_hash(review) != turn["source_audio_sha256"] or review.get("transcript_sha256") != turn["source_transcript_sha256"]:
        raise FeatureRefusal(f"human_review_source_hash_binding:{turn['turn_id']}")
    if review.get("candidate_turn_binding_sha256") != coverage["turn_binding_sha256"]:
        raise FeatureRefusal(f"human_review_candidate_binding:{turn['turn_id']}")
    automated = review.get("reviewer_type") == "AUTOMATED"
    if automated:
        try:
            for path in auto.verify_boundaries(review, selected["selection"], candidate, candidate["turns"][indexes[0]]):
                inputs.bind(path)
            inputs.bind(Path(auto.__file__))
        except (ValueError, KeyError, OSError) as exc:
            raise FeatureRefusal(f"automatic_verification_invalid:{turn['turn_id']}:{exc}") from exc
        if not (selected["continuity"].get("anchored_member") is True or selected["continuity"].get("supplemental_member") is True):
            raise FeatureRefusal(f"automatic_cold_start_is_not_positive_identity:{turn['turn_id']}")
    elif review.get("reviewer_type") != "HUMAN" or not str(review.get("reviewer_name") or "").strip():
        raise FeatureRefusal(f"named_human_review_missing:{turn['turn_id']}")
    if not automated and review.get("review_attestation") != "personally_listened_and_reviewed_identity_boundaries_and_semantics":
        raise FeatureRefusal(f"whole_turn_review_attestation_missing:{turn['turn_id']}")
    if any(review.get(key) != "ACCEPT" for key in ("review_status", "identity_review", "boundary_review", "semantic_review")):
        raise FeatureRefusal(f"human_review_not_accepted:{turn['turn_id']}")
    if any(not str(review.get(key) or "").strip() for key in ("identity_review_note", "boundary_review_note")):
        raise FeatureRefusal(f"human_review_notes_missing:{turn['turn_id']}")
    reviewer_field, date_field = auto.provenance_fields(automated)
    if not turn.get(date_field) or turn.get(date_field) != review.get("reviewed_at") or turn.get(reviewer_field) != str(review["reviewer_name"]).strip():
        raise FeatureRefusal(f"human_review_provenance_missing:{turn['turn_id']}")
    try:
        reviewed_at = dt.datetime.fromisoformat(str(review["reviewed_at"]).replace("Z", "+00:00"))
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() is None:
            raise ValueError("review date requires a timezone")
    except ValueError as exc:
        raise FeatureRefusal(f"human_review_date_invalid:{turn['turn_id']}") from exc
    if turn.get("review_row_sha256") != row_hash(review):
        raise FeatureRefusal(f"human_review_row_hash_mismatch:{turn['turn_id']}")
    if turn.get("identity_review") != review["identity_review_note"] or turn.get("boundary_review") != review["boundary_review_note"]:
        raise FeatureRefusal(f"human_review_notes_binding:{turn['turn_id']}")
    findings = review.get("semantic_findings") or []
    if not findings or any(not str(item.get(key) or "").strip() for item in findings
                           for key in ("kind", "finding", "scope")):
        raise FeatureRefusal(f"human_review_semantics_incomplete:{turn['turn_id']}")
    if turn.get("semantic_findings") != findings or any(item.get("meeting_phase") is not None and item["meeting_phase"] not in {"open", "bring", "run_the_room", "close"} for item in findings):
        raise FeatureRefusal(f"human_review_semantics_binding:{turn['turn_id']}")
    return auto.AUTHORITY if automated else "HASH_BOUND_NAMED_HUMAN_REVIEW", []


def aggregate(turns: list[dict]) -> dict:
    """Do not pool candidates, role-conditioned recordings, or short anchors."""
    members = {}
    for member in sorted({row["member"] for row in turns}):
        groups = defaultdict(list)
        for row in turns:
            if row["member"] != member:
                continue
            stratum = ("qualified_baseline" if row["baseline_measurement_eligible"] else
                       "qualified_role_conditioned" if row["profile_measurement_eligible"] else "candidate_only")
            groups[stratum].append(row)
        members[member] = {}
        for stratum in ("qualified_baseline", "qualified_role_conditioned", "candidate_only"):
            rows = groups[stratum]
            by_source = defaultdict(list)
            for row in rows:
                by_source[(row["source_id"], row["source_audio_sha256"], row["source_era"])].append(row)
            source_groups = []
            for (source_id, source_sha, era), items in sorted(by_source.items()):
                source_groups.append({
                    "source_id": source_id, "source_audio_sha256": source_sha, "source_era": era,
                    "title": items[0].get("title"), "channel": items[0].get("channel"), "url": items[0].get("url"),
                    "turn_ids": [item["turn_id"] for item in items], "turns": len(items),
                    "minutes": round(sum(item["measured_duration_seconds"] for item in items) / 60, 6),
                    "features_arithmetic_mean_over_turns": {
                        key: round(statistics.mean(item["features"][key] for item in items), 8) for key in FEATURE_COLUMNS},
                })
            members[member][stratum] = {
                "turns": len(rows), "sources": len(by_source),
                "minutes": round(sum(row["measured_duration_seconds"] for row in rows) / 60, 6),
                "source_groups": source_groups,
            }
        members[member]["recurring_communication_pattern_established"] = False
    return members


def build_artifact(turn_pairs: list[tuple[Path, Path]], source_paths: list[Path], transcript_paths: list[Path],
                   leadership_path: Path, *, review_chain: dict[str, Path] | None = None,
                   quality_holds_path: Path | None = None, measure=None, generated_at=None) -> dict:
    inputs = Inputs()
    inputs.bind(Path(__file__))
    sources = [row for path in source_paths for row in inputs.jsonl(path)]
    transcripts = [row for path in transcript_paths for row in inputs.jsonl(path)]
    leadership = inputs.json(leadership_path).get("members") or {}
    holds = inputs.jsonl(quality_holds_path) if quality_holds_path else []
    holder: dict = {}
    rows = []
    seen_ids = set()
    intervals = defaultdict(list)
    for turn_path, receipt_path in turn_pairs:
        receipt = inputs.json(receipt_path)
        if receipt.get("schema") not in RECEIPT_SCHEMAS or receipt.get("status") != "PASS":
            raise FeatureRefusal(f"invalid_turn_receipt:{receipt_path}")
        inputs.bind(turn_path, str(receipt.get("artifact_sha256") or ""))
        if Path(to_host(str(receipt.get("artifact") or ""))).resolve() != Path(turn_path).resolve():
            raise FeatureRefusal(f"turn_receipt_path_mismatch:{turn_path}")
        turns = inputs.jsonl(turn_path)
        if (receipt.get("counts") or {}).get("turns") != len(turns):
            raise FeatureRefusal(f"turn_receipt_count_mismatch:{turn_path}")
        for turn in turns:
            turn_id = str(turn.get("turn_id") or "")
            if not turn_id or turn_id in seen_ids:
                raise FeatureRefusal(f"missing_or_duplicate_turn_id:{turn_id}")
            seen_ids.add(turn_id)
            if turn.get("schema") != "member-interview-long-turn-v1" or turn.get("status") not in {"HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED", auto.STATUS}:
                raise FeatureRefusal(f"unexpected_materialized_turn_status:{turn_id}")
            expected_row_sha = turn.get("row_sha256")
            if not expected_row_sha or row_hash({key: value for key, value in turn.items() if key != "row_sha256"}) != expected_row_sha:
                raise FeatureRefusal(f"turn_row_hash_mismatch:{turn_id}")
            contract = turn.get("contract") or {}
            if contract.get("complete_reviewed_turn_not_fixed_window") is not True or contract.get("maximum_turn_seconds", "missing") is not None or contract.get("automatic_trait_or_rank_effect") is not False:
                raise FeatureRefusal(f"turn_contract_mismatch:{turn_id}")
            start, end = float(turn["start_sec"]), float(turn["end_sec"])
            if not math.isfinite(start) or not math.isfinite(end) or end <= start or abs((end - start) - float(turn["duration_seconds"])) > .001:
                raise FeatureRefusal(f"invalid_turn_duration:{turn_id}")
            key = (turn["member"], turn["source_audio_sha256"])
            if any(start < previous_end - .001 and end > previous_start + .001 for previous_start, previous_end in intervals[key]):
                raise FeatureRefusal(f"duplicate_or_overlapping_source_interval:{turn_id}")
            intervals[key].append((start, end))
            source = matching_source(sources, turn)
            transcript = matching_source(transcripts, turn, transcript=True)
            source_wav = inputs.bind(Path(to_host(source.get("wav") or source.get("source_wav") or "")), turn["source_audio_sha256"])
            transcript_path = inputs.bind(Path(to_host(transcript["transcript"])), turn["source_transcript_sha256"])
            turn_wav = inputs.bind(Path(to_host(turn["turn_audio_wav"])), turn["turn_audio_sha256"])
            source_pcm, source_frames = pcm(source_wav, start=start, end=end)
            turn_pcm, turn_frames = pcm(turn_wav)
            if source_frames != turn_frames or source_pcm != turn_pcm:
                raise FeatureRefusal(f"turn_pcm_not_exact_source_slice:{turn_id}")
            selected_words = [word for word in inputs.jsonl(transcript_path)
                              if float(word["end_sec"]) > start and float(word["start_sec"]) < end]
            if len(selected_words) != turn.get("word_count"):
                raise FeatureRefusal(f"turn_transcript_word_count_mismatch:{turn_id}")
            expected_text = " ".join(str(word.get("word") or "").strip() for word in selected_words).strip()
            if " ".join(str(turn.get("transcript_text") or "").split()) != " ".join(expected_text.split()):
                raise FeatureRefusal(f"turn_transcript_text_mismatch:{turn_id}")
            authority, reasons = review_authority(turn, receipt, review_chain or {}, inputs)
            held = any(identity(hold) == identity(turn) and audio_hash(hold) == turn["source_audio_sha256"]
                       and (hold.get("transcript_sha256") or hold.get("source_transcript_sha256")) == turn["source_transcript_sha256"] for hold in holds)
            if held:
                reasons.append("This exact source audio/transcript binding has an unresolved quality hold.")
            era, cutoff = source_era(turn, source, leadership)
            qualified = authority in {"HASH_BOUND_NAMED_HUMAN_REVIEW", auto.AUTHORITY} and not held
            eligible = qualified and era in {"PRE_CURRENT_LEADERSHIP_ROLE", "NOT_CURRENT_FORMAL_LEADERSHIP"}
            measured = measure(turn_wav) if measure is not None else measure_file(turn_wav, holder)
            if set(measured) != set(FEATURE_COLUMNS) or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in measured.values()):
                raise FeatureRefusal(f"invalid_measured_features:{turn_id}")
            duration = turn_frames / 16000
            row = {key: turn.get(key) for key in ("turn_id", "member", "source_id", "title", "channel", "url", "source_audio_sha256", "source_transcript_sha256", "turn_audio_sha256", "start_sec", "end_sec")}
            row.update({
                "input_turn_row_sha256": expected_row_sha, "input_turn_artifact": str(Path(turn_path).resolve()),
                "input_turn_receipt": str(Path(receipt_path).resolve()), "input_status_claim": turn["status"],
                "source_wav": str(source_wav), "source_transcript": str(transcript_path), "turn_audio_wav": str(turn_wav),
                "measured_duration_seconds": duration, "pcm_sha256": hashlib.sha256(turn_pcm).hexdigest(),
                "word_count": len(selected_words), "transcript_words_per_minute": round(len(selected_words) * 60 / duration, 6),
                "word_rate_scope": "ASR word count divided by entire turn duration, including pauses; transcription errors may affect this estimate.",
                "features": {key: round(float(measured[key]), 8) for key in FEATURE_COLUMNS},
                "authority": authority, "qualification_limits": reasons, "quality_hold": held,
                "source_era": era, "current_role_assumed_on": cutoff,
                "recorded_at": turn.get("recorded_at") or source.get("recorded_at"),
                "published": turn.get("published") or source.get("published"),
                "profile_measurement_eligible": qualified, "baseline_measurement_eligible": eligible,
                "human_reviewer": turn.get("human_reviewer") if qualified else None,
                "human_reviewed_at": turn.get("human_reviewed_at") if qualified else None,
                "automated_verifier": turn.get("automated_verifier") if qualified else None,
                "automated_verified_at": turn.get("automated_verified_at") if qualified else None,
                "identity_anchor_windows_inside_turn": turn.get("identity_anchor_windows_inside_turn"),
                "turn_coverage": turn.get("turn_coverage") if qualified else None,
                "candidate_turn_binding_sha256": turn.get("candidate_turn_binding_sha256") if qualified else None,
                "new_human_review_performed_by_measurement_builder": False,
            })
            row["row_sha256"] = row_hash(row)
            rows.append(row)
    rows.sort(key=lambda row: (row["member"], row["source_id"], row["start_sec"], row["turn_id"]))
    return {
        "schema": "member-longform-turn-features-v1",
        "status": "MEASURED_DELIVERY_NO_AUTOMATIC_PROFILE_PROMOTION",
        "generated_at": generated_at or dt.datetime.now(dt.timezone.utc).isoformat(),
        "contract": dict(CONTRACT), "feature_columns": dict(FEATURE_COLUMNS),
        "feature_definitions": dict(FEATURE_DEFINITIONS),
        "measurement": {"feature_set": "eGeMAPSv02", "feature_level": "Functionals",
                        "opensmile_version": holder.get("version", "INJECTED_TEST_MEASURER"),
                        "scope": "entire validated turn WAV, including pauses; no maximum duration",
                        "aggregation": "unweighted arithmetic means of whole-turn functionals within a source and authority/era stratum, not a pooled waveform or chamber percentile"},
        "counts": {"turns": len(rows), "members": len({row["member"] for row in rows}),
                   "sources": len({(row["member"], row["source_id"], row["source_audio_sha256"]) for row in rows}),
                   "minutes": round(sum(row["measured_duration_seconds"] for row in rows) / 60, 6),
                   "qualified_turns": sum(row["profile_measurement_eligible"] for row in rows),
                   "baseline_eligible_turns": sum(row["baseline_measurement_eligible"] for row in rows),
                   "candidate_only_turns": sum(not row["profile_measurement_eligible"] for row in rows),
                   "quality_held_turns": sum(row["quality_hold"] for row in rows)},
        "inputs": inputs.snapshot(), "members": aggregate(rows), "turns": rows,
    }


def current_measurement(out: Path, receipt_path: Path) -> bool:
    """Reuse only a complete generation whose producer and source bytes match."""
    try:
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        inputs = receipt.get('inputs') or []
        producer = str(Path(__file__).resolve())
        if (receipt.get('schema') != 'member-longform-turn-features-receipt-v1'
                or receipt.get('status') != 'PASS_MEASUREMENT_ONLY'
                or Path(to_host(receipt.get('artifact') or '')).resolve() != out.resolve()
                or receipt.get('artifact_sha256') != SMR.sha256_file(out)
                or receipt.get('contract') != CONTRACT
                or producer not in {to_host(item.get('path') or '') for item in inputs}):
            return False
        for item in inputs:
            source = Path(to_host(item['path']))
            if not source.is_file() or SMR.sha256_file(source) != item.get('sha256'):
                return False
        artifact = json.loads(out.read_text(encoding='utf-8'))
        return (artifact.get('inputs') == inputs and artifact.get('contract') == CONTRACT
                and artifact.get('schema') == 'member-longform-turn-features-v1'
                and artifact.get('status') == 'MEASURED_DELIVERY_NO_AUTOMATIC_PROFILE_PROMOTION'
                and artifact.get('feature_columns') == FEATURE_COLUMNS
                and artifact.get('feature_definitions') == FEATURE_DEFINITIONS)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turn-pair", nargs=2, type=Path, action="append", metavar=("TURNS_JSONL", "PASS_RECEIPT"))
    parser.add_argument("--source-manifest", type=Path, action="append")
    parser.add_argument("--transcript-manifest", type=Path, action="append")
    parser.add_argument("--leadership", type=Path, default=DEFAULT_LEADERSHIP)
    parser.add_argument("--review-chain", nargs=2, action="append", metavar=("NAME", "PATH"))
    parser.add_argument("--quality-holds", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--if-current", action="store_true", help="Reuse a fully hash-verified default generation")
    args = parser.parse_args()
    custom_inputs = any((args.turn_pair, args.source_manifest, args.transcript_manifest,
                         args.review_chain, args.quality_holds, args.leadership != DEFAULT_LEADERSHIP))
    if args.if_current and not custom_inputs and current_measurement(args.out, args.receipt):
        print('PASS_MEASUREMENT_ONLY: current source and producer hashes verified; existing measurement retained')
        return 0
    turn_pairs = args.turn_pair or [
        (ROOT / f"data/speech/{stem}.jsonl", ROOT / f"data/speech/{stem}.receipt.json")
        for stem in ("member_interview_long_turns_v1", "member_leadership_prerole_long_turns_v1")]
    sources = args.source_manifest or [ROOT / "data/speech/member_interview_sources_20260903.jsonl",
                                       ROOT / "data/speech/member_leadership_prerole_acquisition_v1.jsonl"]
    transcripts = args.transcript_manifest or [ROOT / "data/speech/member_interview_asr_20260903.jsonl",
                                               Path(MELANGE_DATA, "member_leadership_prerole_transcripts_v1.jsonl")]
    chain = dict(args.review_chain or [])
    if set(chain) - {"selection", "candidates", "continuity", "reviews"} or len(chain) != len(args.review_chain or []):
        parser.error("review-chain names must be unique: selection, candidates, continuity, reviews")
    artifact = build_artifact(turn_pairs, sources, transcripts, args.leadership,
                              review_chain={key: Path(value) for key, value in chain.items()},
                              quality_holds_path=args.quality_holds)
    input_paths = {item["path"] for item in artifact["inputs"]}
    if str(args.out.resolve()) in input_paths or str(args.receipt.resolve()) in input_paths or args.out.resolve() == args.receipt.resolve():
        raise FeatureRefusal("output_must_not_overwrite_an_input")
    SMR.write_json_atomic(args.out, artifact)
    receipt = {
        "schema": "member-longform-turn-features-receipt-v1", "status": "PASS_MEASUREMENT_ONLY",
        "generated_at": artifact["generated_at"], "artifact": str(args.out.resolve()),
        "artifact_sha256": SMR.sha256_file(args.out), "counts": artifact["counts"],
        "contract": artifact["contract"], "inputs": artifact["inputs"],
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
